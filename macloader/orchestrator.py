"""High-level orchestrator coordinating detection, compatibility, BuildPlan, and dependency workflows."""

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

from macloader.compatibility.engine import CompatibilityEngine
from macloader.database.loader import Database, get_database
from macloader.database.schema import DependencyCatalogSchema
from macloader.dependencies.cache import CacheManager
from macloader.dependencies.downloader import Downloader
from macloader.dependencies.resolver import DependencyResolver
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
from macloader.domain.hardware import HardwareSnapshot
from macloader.exceptions import ArtifactDownloadError

logger = logging.getLogger(__name__)


class Orchestrator:
    """Coordinates workflow stages across MacLoader."""

    def __init__(self, db: Optional[Database] = None, cache_dir: Optional[Union[str, Path]] = None):
        self.db = db or get_database()
        self.engine = CompatibilityEngine(db=self.db)
        self.resolver = DependencyResolver(db=self.db)
        self.cache = CacheManager(cache_dir=cache_dir)

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
    ) -> Dict[str, Path]:
        """Acquire and cache all resolved dependencies, verifying their SHA-256 integrity."""
        downloader = Downloader(transport=transport)
        results: Dict[str, Path] = {}
        missing_offline: List[str] = []

        for dep in dep_set.resolved_dependencies:
            spec = self.db.get_dependency_spec(dep.dependency_id)
            if not spec:
                continue

            # Check cache first
            cached_path = self.cache.get_cached_path_if_valid(spec, dep.variant)
            if cached_path:
                results[dep.dependency_id] = cached_path
                continue

            if offline:
                missing_offline.append(f"{dep.project_name} ({dep.version} {dep.variant.value})")
                continue

            # Download to cache directory
            target_path = self.cache.get_artifact_cache_path(spec, dep.variant)
            downloader.download_artifact(dep.artifact, target_path)
            # Re-verify and update index
            self.cache.put_artifact(spec, dep.variant, target_path)
            results[dep.dependency_id] = target_path

        if missing_offline:
            raise ArtifactDownloadError(
                f"Offline mode: the following required artifacts are missing from cache: {', '.join(missing_offline)}"
            )

        return results

    def verify_cached_dependencies(self, dep_set: ResolvedDependencySet) -> Dict[str, bool]:
        """Verify the integrity of all cached artifacts belonging to the resolved set."""
        status_map: Dict[str, bool] = {}
        for dep in dep_set.resolved_dependencies:
            spec = self.db.get_dependency_spec(dep.dependency_id)
            if spec:
                status_map[dep.dependency_id] = self.cache.has_valid_artifact(spec, dep.variant)
        return status_map
