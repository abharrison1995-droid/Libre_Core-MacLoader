"""Schema-driven, non-destructive configuration workflow shared by all UIs."""

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping, Optional, Tuple
import uuid

from macloader.configuration.migrations import import_configuration
from macloader.configuration.service import ConfigurationEvaluation
from macloader.configuration.store import ConfigurationStore
from macloader.config import DEFAULT_WORKSPACE_DIR
from macloader.domain.configuration import ConfigurationIssue, UserConfiguration
from macloader.domain.compatibility import CompatibilityReport
from macloader.domain.evidence import EvidenceRecord
from macloader.domain.hardware import HardwareSnapshot
from macloader.domain.build_plan import BuildPlan
from macloader.domain.dependencies import ArtifactVariant, ResolvedDependencySet
from macloader.domain.recovery import RecoveryBinding, RecoveryLock
from macloader.orchestrator import Orchestrator
from macloader.recovery.discovery import DiscoveryResponse, RecoveryDiscoveryResult
from macloader.recovery.acquirer import RecoveryBundle
from macloader.removable import MediaBindings, RemovableDevice, RemovableMediaWriter, WritePlan, current_adapter


MAX_IMPORT_BYTES = 4 * 1024 * 1024
MAX_IMPORT_DEPTH = 32
MAX_IMPORT_NODES = 10000
MAX_IMPORT_STRING = 8192


@dataclass(frozen=True)
class WorkflowState:
    """The semantic result rendered by both CLI and TUI."""

    configuration: UserConfiguration
    evaluation: ConfigurationEvaluation

    def to_dict(self) -> dict[str, Any]:
        return {
            "configuration": self.configuration.to_dict(),
            "issues": [issue.to_dict() for issue in self.evaluation.issues],
            "plan": self.evaluation.plan.to_dict(),
            "accepted": self.evaluation.accepted is not None,
            "semantic_digest": self.configuration.semantic_digest,
        }


class WorkflowService:
    """Own persistence and semantic transitions; presentation layers stay thin."""

    def __init__(self, orchestrator: Optional[Orchestrator] = None, store: Optional[ConfigurationStore] = None):
        self.orchestrator = orchestrator or Orchestrator()
        self.store = store or ConfigurationStore(DEFAULT_WORKSPACE_DIR / "configurations")

    def create(self, fixture: Optional[Path] = None, sanitize: bool = False) -> tuple[UserConfiguration, HardwareSnapshot]:
        snapshot = self.orchestrator.probe_hardware(fixture_path=fixture, sanitize=sanitize)
        return self.orchestrator.new_configuration(snapshot), snapshot

    def evaluate(self, configuration: UserConfiguration, snapshot: HardwareSnapshot) -> WorkflowState:
        return WorkflowState(configuration, self.orchestrator.evaluate_configuration(configuration, snapshot))

    def load(self, configuration_id: str) -> UserConfiguration:
        return self.store.load(configuration_id)

    def save(self, configuration: UserConfiguration) -> Path:
        return self.store.save(configuration)

    def save_revision(self, configuration: UserConfiguration) -> Path:
        return self.save(replace(configuration, revision=configuration.revision + 1))

    def set_target(self, configuration: UserConfiguration, version: str, build: str) -> UserConfiguration:
        release = self.orchestrator.configuration_service.policy.get_release("sequoia", version, build)
        if release is None:
            raise ValueError("The exact version/build is not present in the trusted release catalog")
        return replace(configuration, target=release.target(), acknowledgements=(), revision=configuration.revision + 1)

    def set_option(self, configuration: UserConfiguration, option_id: str, value: str) -> UserConfiguration:
        selections = configuration.selected_options()
        selections[option_id] = value
        return replace(
            configuration,
            option_selections=tuple(selections.items()),
            acknowledgements=(),
            revision=configuration.revision + 1,
        )

    def acknowledge(self, configuration: UserConfiguration, rule_id: str, warning_text: str) -> UserConfiguration:
        acknowledged = self.orchestrator.configuration_service.acknowledge(configuration, rule_id, warning_text)
        return replace(acknowledged, revision=configuration.revision + 1)

    def import_file(self, path: Path) -> tuple[UserConfiguration, tuple[ConfigurationIssue, ...]]:
        path = Path(path)
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError("configuration import must be a regular file")
            if path.stat().st_size > MAX_IMPORT_BYTES:
                raise ValueError("configuration import exceeds the bounded size limit")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self._check_import_limits(payload)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("configuration import is unreadable or malformed") from exc
        configuration, issues = import_configuration(payload)
        return configuration, issues

    def import_as_new(self, path: Path) -> tuple[UserConfiguration, tuple[ConfigurationIssue, ...]]:
        configuration, issues = self.import_file(path)
        return replace(configuration, configuration_id=str(uuid.uuid4()), revision=0), issues

    def migrate_file(self, path: Path) -> tuple[UserConfiguration, tuple[ConfigurationIssue, ...]]:
        """Explicit legacy/schema migration boundary; imported data is always a new draft."""
        return self.import_as_new(path)

    def export_file(self, configuration: UserConfiguration, path: Path) -> None:
        self._atomic_json_write(Path(path), self.store.export_public(configuration))

    def add_evidence(self, configuration: UserConfiguration, record: EvidenceRecord) -> UserConfiguration:
        records = tuple(item for item in configuration.evidence if item.evidence_id != record.evidence_id)
        return replace(configuration, evidence=(*records, record), revision=configuration.revision + 1)

    def plan(self, configuration: UserConfiguration, snapshot: HardwareSnapshot) -> BuildPlan:
        return self.evaluate(configuration, snapshot).evaluation.plan

    def support_review(self, configuration: UserConfiguration, snapshot: HardwareSnapshot) -> CompatibilityReport:
        target_macos = configuration.target.version if configuration.target is not None else "sequoia"
        return self.orchestrator.check_support(snapshot, target_macos=target_macos)

    def verify_recovery_cache(self, lock_path: Path, image_path: Path, chunklist_path: Path) -> object:
        lock = self.orchestrator.recovery_service.load_lock(Path(lock_path))
        return self.orchestrator.recovery_service.verify(lock, Path(image_path), Path(chunklist_path))

    @staticmethod
    def removable_status() -> dict[str, object]:
        adapter = current_adapter()
        devices: list[dict[str, object]] = []
        if adapter.status.advertised:
            devices = [
                {
                    "device_id": device.public_device_ref,
                    "model": device.model,
                    "capacity_bytes": device.capacity_bytes,
                    "serial": "<redacted>" if device.serial else None,
                    "vendor": device.vendor,
                    "partitions": list(device.partitions),
                    "whole_device": device.whole_device,
                    "is_system_disk": device.is_system_disk,
                    "is_removable": device.is_removable,
                    "mounted": device.mounted,
                    "read_only": device.read_only,
                }
                for device in adapter.enumerate()
            ]
        return {
            "status": "qualified" if adapter.status.qualified else "discovery_not_qualified",
            "adapter": adapter.status.to_dict(),
            "devices": devices,
            "writes_enabled": False,
        }

    def resolve_dependencies(
        self,
        configuration: UserConfiguration,
        snapshot: HardwareSnapshot,
        variant: ArtifactVariant = ArtifactVariant.RELEASE,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> tuple[WorkflowState, ResolvedDependencySet]:
        if cancel and cancel():
            raise ValueError("Dependency resolution cancelled")
        state = self.evaluate(configuration, snapshot)
        dependencies = self.orchestrator.resolve_dependencies(state.evaluation.plan, variant=variant)
        if cancel and cancel():
            raise ValueError("Dependency resolution cancelled")
        return state, dependencies

    def build_efi_preview(
        self,
        configuration: UserConfiguration,
        snapshot: HardwareSnapshot,
        output: Path,
        offline: bool = True,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> object:
        """Build and validate only the generated EFI output; media is never touched."""
        state, dependencies = self.resolve_dependencies(configuration, snapshot)
        if state.evaluation.has_blockers:
            raise ValueError("EFI build blocked by configuration issues")
        if not dependencies.is_complete:
            raise ValueError("EFI build blocked by unresolved dependency requirements")
        artifacts = self.orchestrator.fetch_dependencies(
            dependencies, offline=offline, plan=state.evaluation.plan, cancel=cancel
        )
        return self.orchestrator.build_efi(state.evaluation.plan, dependencies, artifacts, Path(output))

    def discover_recovery(
        self,
        transport: Optional[Callable[[str, Mapping[str, str], bytes], DiscoveryResponse]] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> RecoveryDiscoveryResult:
        return self.orchestrator.discover_recovery(transport=transport, cancel=cancel)

    def acquire_recovery(
        self,
        result: RecoveryDiscoveryResult,
        binding: RecoveryBinding,
        destination: Path,
        cancel: Optional[Callable[[], bool]] = None,
        resume: bool = True,
    ) -> tuple[RecoveryLock, RecoveryBundle]:
        return self.orchestrator.recovery_service.acquire(
            result, binding, destination, cancel=cancel, resume=resume
        )

    @staticmethod
    def preview_media_plan(
        device: RemovableDevice,
        required_bytes: int,
        writer: Optional[RemovableMediaWriter] = None,
        source_dir: Optional[Path] = None,
        bindings: Optional[MediaBindings] = None,
    ) -> WritePlan:
        return (writer or RemovableMediaWriter()).dry_run(
            device,
            required_bytes,
            source_dir=source_dir,
            bindings=bindings,
        )

    @staticmethod
    def render_json(state: WorkflowState) -> str:
        return json.dumps(state.to_dict(), indent=2)

    @staticmethod
    def _check_import_limits(value: Any, depth: int = 0, nodes: list[int] | None = None) -> None:
        if nodes is None:
            nodes = [0]
        nodes[0] += 1
        if nodes[0] > MAX_IMPORT_NODES or depth > MAX_IMPORT_DEPTH:
            raise ValueError("configuration import is too deeply nested or large")
        if isinstance(value, str) and len(value) > MAX_IMPORT_STRING:
            raise ValueError("configuration import contains an oversized string")
        if isinstance(value, dict):
            for key, item in value.items():
                WorkflowService._check_import_limits(key, depth + 1, nodes)
                WorkflowService._check_import_limits(item, depth + 1, nodes)
        elif isinstance(value, list):
            for item in value:
                WorkflowService._check_import_limits(item, depth + 1, nodes)

    @staticmethod
    def _atomic_json_write(path: Path, payload: Any) -> None:
        path = Path(path)
        ConfigurationStore._assert_safe_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        ConfigurationStore._assert_safe_path(path)
        if not path.parent.is_dir():
            raise ValueError("configuration export parent is not a directory")
        if path.is_symlink():
            raise ValueError("configuration export destination must not be a symlink")
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            ConfigurationStore._assert_safe_path(path)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
