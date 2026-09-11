"""High-level orchestrator coordinating detection, compatibility, BuildPlan, and dependency workflows."""

import logging
import platform
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Union

from macloader.compatibility.engine import CompatibilityEngine
from macloader.database.loader import Database, get_database
from macloader.dependencies.cache import CacheManager
from macloader.dependencies.downloader import Downloader
from macloader.dependencies.resolver import DependencyResolver
from macloader.build.efi import EfiBuildResult, EfiBuilder
from macloader.detection import (
    BaseHardwareProvider,
    FixtureHardwareProvider,
    get_default_provider,
    sanitize_hardware_snapshot,
)
from macloader.domain.build_plan import BuildPlan
from macloader.domain.compatibility import CompatibilityReport
from macloader.domain.dependencies import (
    ArtifactVariant,
    DependencySpec,
    ResolvedDependencySet,
)
from macloader.domain.contracts import CONTRACT_SCHEMA_VERSION, ToolchainSelection
from macloader.build.config import ReviewedEfiProfile
from macloader.domain.hardware import HardwareSnapshot
from macloader.domain.configuration import UserConfiguration
from macloader.configuration.service import ConfigurationEvaluation, ConfigurationService
from macloader.exceptions import ArtifactDownloadError
from macloader.recovery.service import RecoveryService
from macloader.recovery.discovery import DiscoveryResponse, RecoveryDiscoveryResult

logger = logging.getLogger(__name__)


class Orchestrator:
    """Coordinates workflow stages across MacLoader."""

    def __init__(self, db: Optional[Database] = None, cache_dir: Optional[Union[str, Path]] = None):
        self.db = db or get_database()
        self.engine = CompatibilityEngine(db=self.db)
        self._resolver: Optional[DependencyResolver] = None
        self._cache: Optional[CacheManager] = None
        self._cache_dir = cache_dir
        self.builder = EfiBuilder(db=self.db)
        self._configuration_service: Optional[ConfigurationService] = None
        self._recovery_service: Optional[RecoveryService] = None

    @property
    def configuration_service(self) -> ConfigurationService:
        """Shared policy/evaluation service for CLI and future TUI presentations."""
        if self._configuration_service is None:
            self._configuration_service = ConfigurationService(db=self.db)
        return self._configuration_service

    @property
    def recovery_service(self) -> RecoveryService:
        """Shared exact-target Recovery service for CLI and Textual clients."""
        if self._recovery_service is None:
            self._recovery_service = RecoveryService()
        return self._recovery_service

    def discover_recovery(
        self,
        transport: Optional[Callable[[str, Mapping[str, str], bytes], DiscoveryResponse]] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> RecoveryDiscoveryResult:
        return self.recovery_service.discover(transport=transport, cancel=cancel)

    @property
    def resolver(self) -> DependencyResolver:
        if self._resolver is None:
            self._resolver = DependencyResolver(db=self.db)
        return self._resolver

    @property
    def cache(self) -> CacheManager:
        if self._cache is None:
            self._cache = CacheManager(cache_dir=self._cache_dir)
        return self._cache

    def probe_hardware(
        self,
        provider: Optional[BaseHardwareProvider] = None,
        fixture_path: Optional[Union[str, Path]] = None,
        sanitize: bool = False,
    ) -> HardwareSnapshot:
        """Probe hardware using either a fixture or host system provider."""
        if fixture_path:
            p: BaseHardwareProvider = FixtureHardwareProvider(fixture_path)
        elif provider:
            p = provider
        else:
            p = get_default_provider()

        snapshot = p.probe()
        if sanitize:
            snapshot = sanitize_hardware_snapshot(snapshot)
        return snapshot

    def check_support(
        self,
        snapshot: HardwareSnapshot,
        target_macos: str = "sequoia",
    ) -> CompatibilityReport:
        """Evaluate snapshot compatibility against the target macOS version."""
        return self.engine.evaluate(snapshot=snapshot, target_macos=target_macos)

    def generate_plan(
        self,
        snapshot: HardwareSnapshot,
        target_macos: str = "sequoia",
    ) -> BuildPlan:
        """Generate a preliminary BuildPlan for the detected snapshot and target macOS."""
        report = self.check_support(snapshot=snapshot, target_macos=target_macos)
        return self.engine.generate_build_plan(report=report)

    def new_configuration(self, snapshot: HardwareSnapshot) -> UserConfiguration:
        return self.configuration_service.new_draft(snapshot)

    def evaluate_configuration(self, configuration: UserConfiguration, snapshot: HardwareSnapshot) -> ConfigurationEvaluation:
        return self.configuration_service.evaluate(configuration, snapshot)

    def list_catalog_dependencies(self) -> List[DependencySpec]:
        """Return all available specifications from the dependency catalog."""
        return self.db.list_dependency_specs()

    def resolve_dependencies(
        self,
        plan: BuildPlan,
        variant: ArtifactVariant = ArtifactVariant.RELEASE,
    ) -> ResolvedDependencySet:
        """Resolve required dependencies for the given BuildPlan without performing network I/O."""
        return self.resolver.resolve(plan=plan, variant=variant)

    def fetch_dependencies(
        self,
        dep_set: ResolvedDependencySet,
        offline: bool = False,
        transport: Optional[Callable[[str, Path], None]] = None,
        *,
        plan: Optional[BuildPlan] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Path]:
        """Acquire and cache all resolved dependencies, verifying their SHA-256 integrity."""
        downloader = Downloader(transport=transport)
        results: Dict[str, Path] = {}
        missing_offline: List[str] = []

        if plan is None:
            raise ArtifactDownloadError("Dependency acquisition requires the BuildPlan bound to the lock")
        self._validate_dependency_set(plan, dep_set)
        if not dep_set.resolved_dependencies:
            raise ArtifactDownloadError("Cannot fetch an empty resolved dependency set.")
        if not dep_set.catalog_digest or dep_set.catalog_digest != self.resolver.catalog_digest():
            raise ArtifactDownloadError("Resolved dependency set was created from a stale catalog digest.")
        catalog = self.db.get_dependency_catalog()
        if not catalog or dep_set.policy_version != catalog.policy_version:
            raise ArtifactDownloadError("Resolved dependency set was created from a stale dependency policy.")

        for dep in dep_set.resolved_dependencies:
            if cancel and cancel():
                raise ArtifactDownloadError("Dependency acquisition was cancelled.")

            spec = self.db.get_dependency_spec(dep.dependency_id)
            if not spec:
                raise ArtifactDownloadError(f"Resolved dependency '{dep.dependency_id}' is absent from the current catalog.")
            current_artifact = spec.get_artifact(dep.variant)
            if current_artifact is None or current_artifact.to_dict() != dep.artifact.to_dict():
                raise ArtifactDownloadError(
                    f"Resolved dependency '{dep.dependency_id}' is stale or does not match the current catalog lock."
                )

            # Check cache first
            cached_path = self.cache.get_cached_path_if_valid(spec, dep.variant)
            if cached_path:
                results[dep.dependency_id] = cached_path
                continue

            if offline:
                missing_offline.append(f"{dep.project_name} ({dep.version} {dep.variant.value})")
                continue

            # Acquire artifact with per-artifact locking and cache re-check
            target_path = self.cache.acquire_artifact(spec, dep.variant, downloader, cancel=cancel)
            results[dep.dependency_id] = target_path

        if missing_offline:
            raise ArtifactDownloadError(
                f"Offline mode: the following required artifacts are missing from cache: {', '.join(missing_offline)}"
            )

        return results

    def verify_cached_dependencies(self, dep_set: ResolvedDependencySet, *, plan: Optional[BuildPlan] = None) -> Dict[str, bool]:
        """Verify the integrity of all cached artifacts belonging to the resolved set."""
        status_map: Dict[str, bool] = {}
        if plan is None:
            return {dep.dependency_id: False for dep in dep_set.resolved_dependencies}
        try:
            self._validate_dependency_set(plan, dep_set)
        except ArtifactDownloadError:
            return {dep.dependency_id: False for dep in dep_set.resolved_dependencies}
        if not dep_set.catalog_digest or dep_set.catalog_digest != self.resolver.catalog_digest():
            return {dep.dependency_id: False for dep in dep_set.resolved_dependencies}
        for dep in dep_set.resolved_dependencies:
            spec = self.db.get_dependency_spec(dep.dependency_id)
            if not spec:
                status_map[dep.dependency_id] = False
            else:
                current_artifact = spec.get_artifact(dep.variant)
                status_map[dep.dependency_id] = bool(
                    current_artifact
                    and current_artifact.to_dict() == dep.artifact.to_dict()
                    and self.cache.has_valid_artifact(spec, dep.variant)
                )
        return status_map

    def _validate_dependency_set(self, plan: BuildPlan, dep_set: ResolvedDependencySet) -> None:
        if dep_set.plan_digest != plan.canonical_digest():
            raise ArtifactDownloadError("Resolved dependency set is bound to a different BuildPlan")
        expected = self.resolver.resolve(plan, dep_set.variant)
        signature = lambda item: (
            item.dependency_id.lower(), item.project_name, item.version, item.variant.value,
            tuple(item.subcomponents), item.artifact.to_dict(), item.reason, item.required_by, item.is_transitive,
        )
        if [signature(item) for item in dep_set.resolved_dependencies] != [signature(item) for item in expected.resolved_dependencies]:
            raise ArtifactDownloadError("Resolved dependency set does not exactly match the current BuildPlan resolution")
        if dep_set.unresolved_requirements != expected.unresolved_requirements:
            raise ArtifactDownloadError("Resolved dependency set unresolved requirements are stale")

    def build_efi(
        self,
        plan: BuildPlan,
        dep_set: ResolvedDependencySet,
        artifact_paths: Dict[str, Path],
        output_dir: Union[str, Path],
        fake_identity: Optional[Dict[str, str]] = None,
        toolchain: Optional[ToolchainSelection] = None,
        ocvalidate_path: Optional[Union[str, Path]] = None,
        ocvalidate_sha256: Optional[str] = None,
        reviewed_profile: Optional[ReviewedEfiProfile] = None,
        private_acpi_capture: Optional[Union[str, Path]] = None,
        expected_acpi_evidence_digest: Optional[str] = None,
    ) -> EfiBuildResult:
        if dep_set.plan_digest != plan.canonical_digest():
            raise ArtifactDownloadError("Dependency lock is bound to a different BuildPlan")
        if toolchain is None:
            opencore = self.db.get_dependency_spec("opencore")
            if opencore is None:
                raise ArtifactDownloadError("OpenCore toolchain policy is unavailable")
            toolchain = ToolchainSelection(
                schema_version=CONTRACT_SCHEMA_VERSION,
                opencore_version=opencore.version,
                ocvalidate_version=opencore.version,
                acpi_compiler=None,
                identity_tool=None,
                recovery_tool=None,
                host_platform=platform.system().lower(),
                host_architecture=platform.machine().lower(),
                provenance={"source": "cli-supplied", "qualification": "pending-s03"},
                ocvalidate_path=str(ocvalidate_path) if ocvalidate_path else None,
                ocvalidate_sha256=ocvalidate_sha256.lower() if ocvalidate_sha256 else None,
            )
        return self.builder.build(
            plan, dep_set, artifact_paths, Path(output_dir), fake_identity=fake_identity, toolchain=toolchain,
            reviewed_profile=reviewed_profile,
            private_acpi_capture=Path(private_acpi_capture) if private_acpi_capture is not None else None,
            expected_acpi_evidence_digest=expected_acpi_evidence_digest,
        )
