"""Schema-driven, non-destructive configuration workflow shared by all UIs."""

from dataclasses import dataclass, replace
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Any, Callable, Mapping, Optional, Tuple
import uuid

from macloader.configuration.migrations import import_configuration
from macloader.configuration.service import ConfigurationEvaluation
from macloader.configuration.store import ConfigurationStore
from macloader.config import (
    DEFAULT_ACPI_DIR, DEFAULT_IDENTITY_DIR, DEFAULT_PRIVATE_DIR, DEFAULT_WORKSPACE_DIR,
)
from macloader.domain.configuration import ConfigurationIssue, UserConfiguration
from macloader.domain.compatibility import CompatibilityReport
from macloader.domain.evidence import EvidenceConfidence, EvidenceRecord
from macloader.domain.hardware import HardwareSnapshot
from macloader.domain.build_plan import BuildPlan
from macloader.domain.dependencies import ArtifactVariant, ResolvedDependencySet
from macloader.domain.contracts import BuildManifest, IdentityReference, ToolchainSelection, canonical_json_digest
from macloader.domain.recovery import RecoveryBinding, RecoveryEvidence, RecoveryLock
from macloader.exceptions import MacLoaderError
from macloader.build.acpi import AcpiProcessor, normalize_bios_binding
from macloader.build.config import effective_profile_digest, load_reviewed_profile
from macloader.build.efi import EfiBuildResult
from macloader.evidence.acpi import AcpiEvidenceBundle, AcpiTableRecord
from macloader.identity.service import IdentityService, IdentityServiceError
from macloader.orchestrator import Orchestrator
from macloader.recovery.discovery import DiscoveryResponse, RecoveryDiscoveryResult
from macloader.recovery.evidence import RecoveryDiscoveryEvidence, RecoveryReadiness, assess_recovery_evidence
from macloader.recovery.acquirer import RecoveryBundle
from macloader.removable import MediaBindings, RemovableDevice, RemovableMediaWriter, WritePlan, current_adapter


MAX_IMPORT_BYTES = 4 * 1024 * 1024
MAX_IMPORT_DEPTH = 32
MAX_IMPORT_NODES = 10000
MAX_IMPORT_STRING = 8192
PRIVATE_IDENTITY_CONFIRMATION = "GENERATE A PRIVATE SMBIOS IDENTITY FOR THIS INSTALLATION"
DEFAULT_RECOVERY_EVIDENCE_PATH = DEFAULT_PRIVATE_DIR / "recovery" / "discovery-evidence.json"


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
        draft = replace(self.orchestrator.new_configuration(snapshot), loaded_base_revision=0)
        return draft, snapshot

    def evaluate(self, configuration: UserConfiguration, snapshot: HardwareSnapshot) -> WorkflowState:
        return WorkflowState(configuration, self.orchestrator.evaluate_configuration(configuration, snapshot))

    def load(self, configuration_id: str) -> UserConfiguration:
        configuration = self.store.load(configuration_id)
        return replace(configuration, loaded_base_revision=configuration.revision)

    def save_snapshot(self, configuration: UserConfiguration, snapshot: HardwareSnapshot) -> Path:
        """Persist the local resume snapshot with owner-only permissions."""
        if configuration.hardware_snapshot_id != snapshot.snapshot_id:
            raise ValueError("resume snapshot does not match the configuration binding")
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", configuration.configuration_id):
            raise ValueError("configuration ID is not safe for private snapshot storage")
        root = DEFAULT_PRIVATE_DIR / "snapshots"
        self._ensure_private_directory(root)
        destination = root / f"{configuration.configuration_id}.json"
        payload = snapshot.to_json(indent=2) + "\n"
        self._write_private_json(destination, payload)
        return destination

    def resume_snapshot(self, configuration_id: str) -> HardwareSnapshot:
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", configuration_id):
            raise ValueError("configuration ID is not safe for private snapshot storage")
        path = DEFAULT_PRIVATE_DIR / "snapshots" / f"{configuration_id}.json"
        data = self._read_private_json(path, "resume snapshot")
        snapshot = HardwareSnapshot.from_dict(data)
        configuration = self.load(configuration_id)
        if snapshot.snapshot_id != configuration.hardware_snapshot_id:
            raise ValueError("stored resume snapshot does not match the configuration binding")
        return snapshot

    def import_acpi_capture(
        self,
        configuration: UserConfiguration,
        snapshot: HardwareSnapshot,
        source_directory: Path,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> tuple[UserConfiguration, EvidenceRecord]:
        """Validate and privately import this machine's raw DSDT/SSDT capture.

        ``cancel`` is checked before the private capture is published, so a
        cancelled import leaves nothing behind.  The returned configuration is
        not saved; the caller commits it only if the operation is still current.
        """
        if configuration.hardware_snapshot_id != snapshot.snapshot_id:
            raise ValueError("ACPI import requires the configuration's bound hardware snapshot")
        if snapshot.machine_type != "20L8":
            raise ValueError("machine-bound ACPI import is currently reviewed only for ThinkPad T480s 20L8")
        profile = load_reviewed_profile()
        if normalize_bios_binding(snapshot.bios_version or "") != profile.bios_binding:
            raise ValueError("ACPI capture must match the reviewed N22ET85W BIOS 1.62 profile")
        source = Path(source_directory).expanduser().absolute()
        self._reject_symlink_path(source)
        table_dir = source / "PRIVATE-ACPI" if (source / "PRIVATE-ACPI").is_dir() else source
        paths = AcpiProcessor._find_tables(table_dir)
        table_data: list[tuple[Path, bytes, dict[str, object]]] = []
        for path in paths:
            data = AcpiProcessor._read_table_bytes(path)
            metadata = AcpiProcessor._validate_table_bytes(data, path.name)
            if len(data) != metadata["length"]:
                raise ValueError("ACPI capture changed while being imported")
            table_data.append((path, data, metadata))

        self._ensure_private_directory(DEFAULT_ACPI_DIR)
        capture_id = uuid.uuid4().hex
        final_root = DEFAULT_ACPI_DIR / capture_id
        staging_path = Path(tempfile.mkdtemp(prefix=".capture-", dir=DEFAULT_ACPI_DIR))
        staging_root: Optional[Path] = staging_path
        try:
            if os.name != "nt":
                os.chmod(staging_path, 0o700)
            private_tables = staging_path / "PRIVATE-ACPI"
            private_tables.mkdir(mode=0o700)
            records: list[AcpiTableRecord] = []
            for path, data, metadata in table_data:
                target = private_tables / path.name.lower()
                with target.open("xb") as handle:
                    os.fchmod(handle.fileno(), 0o600) if hasattr(os, "fchmod") else None
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._protect_private_file(target)
                records.append(AcpiTableRecord(
                    table_name=path.name.lower(),
                    sha256=str(metadata["sha256"]),
                    source="operator-imported from the bound T480s machine",
                    namespace_paths=(),
                ))
            bundle = AcpiEvidenceBundle(
                snapshot_id=snapshot.snapshot_id,
                bios_binding=profile.bios_binding,
                private_ref=str(final_root / "evidence.json"),
                capture_version="1",
                tables=tuple(records),
                confidence=EvidenceConfidence.MEDIUM,
            )
            metadata_path = staging_path / "evidence.json"
            self._write_private_json(metadata_path, json.dumps(bundle.to_dict(), sort_keys=True, indent=2) + "\n")
            if cancel and cancel():
                raise ValueError("ACPI import cancelled before publication; nothing was stored")
            os.replace(staging_path, final_root)
            staging_root = None
            record = bundle.to_evidence_record()
            updated = self.add_evidence(configuration, record)
            return updated, record
        finally:
            if staging_root is not None and staging_root.exists():
                shutil.rmtree(staging_root, ignore_errors=True)

    def generate_private_identity(
        self,
        configuration: UserConfiguration,
        confirmation: str,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> tuple[UserConfiguration, str]:
        """Generate and privately store a real identity for this configuration.

        ``cancel`` is checked before the tool is provisioned and immediately
        before the identity is stored.  The returned configuration is not
        saved; a caller whose operation became stale must discard the stored
        identity with :meth:`discard_private_identity` instead of saving.
        """
        if confirmation != PRIVATE_IDENTITY_CONFIRMATION:
            raise IdentityServiceError("Private identity generation requires the exact explicit confirmation phrase")
        if configuration.target is None or configuration.target.product_id != "sequoia":
            raise IdentityServiceError("Private identity requires a selected T480s Sequoia configuration")
        if cancel and cancel():
            raise IdentityServiceError("Private identity generation cancelled; nothing was generated")
        from macloader.toolchain.loader import TrustedToolchainLoader
        toolchain = TrustedToolchainLoader().provision()
        if toolchain.identity_tool_path is None:
            raise IdentityServiceError("Trusted macserial is unavailable; provision the pinned toolchain first")
        identities = IdentityService(DEFAULT_IDENTITY_DIR, Path(toolchain.identity_tool_path))
        values = identities.generate(allow_real=True)
        if cancel and cancel():
            raise IdentityServiceError("Private identity generation cancelled; nothing was stored")
        private = identities.store(values)
        updated = self.set_identity_reference(configuration, private.storage_ref)
        return updated, private.storage_ref

    @staticmethod
    def discard_private_identity(storage_ref: str) -> None:
        """Delete a just-generated identity that a stale operation must not adopt."""
        if not re.fullmatch(r"[0-9a-f]{32}\.json", storage_ref):
            raise ValueError("only a generated private identity reference can be discarded")
        path = Path(DEFAULT_IDENTITY_DIR) / storage_ref
        if path.is_symlink():
            raise ValueError("private identity path must not be a symlink")
        path.unlink(missing_ok=True)

    @staticmethod
    def discard_acpi_capture(record: EvidenceRecord) -> None:
        """Delete a just-imported private capture that a stale operation must not adopt."""
        root = Path(record.private_ref).parent
        acpi_root = Path(DEFAULT_ACPI_DIR).absolute()
        if (
            Path(record.private_ref).name != "evidence.json"
            or root.absolute().parent != acpi_root
            or not re.fullmatch(r"[0-9a-f]{32}", root.name)
            or root.is_symlink()
        ):
            raise ValueError("only a private ACPI capture imported by this workspace can be discarded")
        shutil.rmtree(root, ignore_errors=True)

    def reuse_private_identity(
        self,
        configuration: UserConfiguration,
        storage_ref: str,
    ) -> UserConfiguration:
        private = IdentityService(DEFAULT_IDENTITY_DIR).reuse(IdentityReference("0.1", storage_ref, redacted=True))
        return self.set_identity_reference(configuration, private.storage_ref)

    def preflight(
        self,
        configuration: Optional[UserConfiguration],
        snapshot: Optional[HardwareSnapshot],
    ) -> dict[str, Any]:
        """Report every locally knowable prerequisite and unresolved external gate."""
        checks: list[dict[str, str]] = []

        def add(check_id: str, state: str, summary: str, action: str = "") -> None:
            checks.append({"id": check_id, "state": state, "summary": summary, "action": action})

        if configuration is None:
            add("configuration", "missing", "No saved configuration is selected.", "Run `macloader config new`, then set and review the exact target.")
        else:
            exact_target = configuration.target is not None and (
                configuration.target.product_id == "sequoia"
                and configuration.target.version == "15.0"
                and configuration.target.build == "24A335"
            )
            add(
                "exact_target", "ready" if exact_target else "missing",
                "Configuration selects Sequoia 15.0 build 24A335." if exact_target else "Configuration does not select the frozen Sequoia 15.0/24A335 target.",
                "Use `config set ID --version 15.0 --build 24A335` after reviewing the target." if not exact_target else "",
            )
        if snapshot is None:
            add("machine_snapshot", "missing", "No matching local hardware snapshot is available.", "Resume with the private saved snapshot or provide the matching fixture.")
        elif configuration is None:
            add("machine_snapshot", "missing", "A hardware snapshot was observed, but no saved configuration binds it.",
                "Run `macloader config new` on this machine, then rerun `macloader preflight --config CONFIG_ID`.")
        elif snapshot.snapshot_id != configuration.hardware_snapshot_id:
            add("machine_snapshot", "blocked", "The observed machine snapshot does not match the configuration binding.", "Load the original snapshot or create a new configuration on the reference machine.")
        if snapshot is not None and (configuration is None or snapshot.snapshot_id == configuration.hardware_snapshot_id):
            supported_machine = snapshot.machine_type == "20L8"
            bios_matches = normalize_bios_binding(snapshot.bios_version or "") == "N22ET85W-1.62"
            add("reference_machine", "ready" if supported_machine and bios_matches else "blocked",
                "Reference ThinkPad T480s 20L8 / N22ET85W 1.62 observed." if supported_machine and bios_matches else "Observed hardware or BIOS does not match ThinkPad T480s 20L8 / N22ET85W 1.62.",
                "Probe the reference 20L8 with BIOS N22ET85W 1.62; do not change BIOS as part of preflight." if not supported_machine or not bios_matches else "")

        if configuration is not None and snapshot is not None and snapshot.snapshot_id == configuration.hardware_snapshot_id:
            evaluation = self.evaluate(configuration, snapshot).evaluation
            blocking = [issue for issue in evaluation.issues if issue.blocking]
            add("configuration_review", "ready" if not blocking else "blocked",
                "Configuration review has no blockers." if not blocking else f"Configuration review has {len(blocking)} blocker(s).",
                "Review each issue and its remediation: " + "; ".join(f"{item.code}: {item.remediation}" for item in blocking) if blocking else "")

            acpi_record = next((record for record in configuration.evidence if record.kind == "acpi"), None)
            acpi_ready = False
            if acpi_record is not None:
                source = self.orchestrator.configuration_service._evidence_source(acpi_record.private_ref)
                try:
                    if source is None:
                        raise ValueError("private capture is missing")
                    profile = load_reviewed_profile()
                    AcpiProcessor.capture_evidence_digest(source.parent, profile.bios_binding, snapshot.snapshot_id)
                    acpi_ready = True
                except (OSError, ValueError, MacLoaderError):
                    acpi_ready = False
            add("private_acpi", "ready" if acpi_ready else "missing",
                "Machine-bound private ACPI capture is present." if acpi_ready else "Machine-bound private ACPI capture is missing or invalid.",
                "On this T480s, capture read-only with `sudo \"$(command -v macloader)\" evidence acpi-capture DIRECTORY`, "
                "then import it with `macloader evidence acpi-import ID DIRECTORY`." if not acpi_ready else "")

            usb_records = [record for record in configuration.evidence if record.kind == "usb"]
            usb_ready = any(record.completeness.value == "complete" and record.physical_port_evidence for record in usb_records)
            add("usb_evidence", "ready" if usb_ready else "missing",
                "Complete physical USB port evidence is present." if usb_ready else "Physical USB port evidence is absent or incomplete.",
                "Capture and review the physical port session; USB-C logical correlation remains unresolved until measured." if not usb_ready else "")

            identity_ok = False
            if configuration.identity_ref is not None:
                try:
                    IdentityService(DEFAULT_IDENTITY_DIR).reuse(configuration.identity_ref)
                    identity_ok = True
                except IdentityServiceError:
                    identity_ok = False
            add("private_identity", "ready" if identity_ok else "missing",
                "A private SMBIOS identity is selected and locally verified." if identity_ok else "No reusable private SMBIOS identity is selected.",
                "Choose `macloader identity generate ID` with its explicit confirmation phrase, or reuse a reviewed private identity." if not identity_ok else "")

            try:
                from macloader.toolchain.loader import TrustedToolchainLoader, ToolchainTrustError
                try:
                    TrustedToolchainLoader().select()
                    tools_ok = True
                except ToolchainTrustError:
                    tools_ok = False
                add("toolchain", "ready" if tools_ok else "missing",
                    "Catalog-pinned OpenCore, ocvalidate, iASL and macserial are verified." if tools_ok else "Catalog-pinned host tools are not installed or failed verification.",
                    "Run `macloader toolchain install` to acquire and verify catalog-pinned tools." if not tools_ok else "")
            except (OSError, ValueError):
                add("toolchain", "blocked", "Trusted toolchain policy could not be loaded.", "Repair the packaged toolchain catalog before building.")

            try:
                _, dependencies = self.resolve_dependencies(configuration, snapshot)
                complete = dependencies.is_complete
                cached = self.orchestrator.verify_cached_dependencies(dependencies, plan=self.evaluate(configuration, snapshot).evaluation.plan)
                cache_ready = complete and bool(cached) and all(cached.values())
                add("dependencies", "ready" if cache_ready else "missing",
                    "All catalog-pinned dependencies are resolved and cached." if cache_ready else "Verified dependencies are missing from the workspace cache.",
                    "Use the TUI build action or `macloader deps fetch --fixture FIXTURE` to acquire verified dependencies." if not cache_ready else "")
            except Exception as exc:
                add("dependencies", "blocked", "Dependency resolution could not complete.", f"Resolve the configuration blockers and retry ({type(exc).__name__}).")
        else:
            unchecked_action = (
                "Select the configuration bound to this machine with `macloader preflight --config CONFIG_ID` "
                "to check this prerequisite."
            )
            for check_id, summary in (
                ("private_acpi", "Machine-bound private ACPI evidence has not been checked."),
                ("usb_evidence", "Physical USB port evidence has not been checked."),
                ("private_identity", "A private SMBIOS identity has not been checked."),
                ("toolchain", "Catalog-pinned host tools have not been checked."),
                ("dependencies", "Dependency readiness has not been checked."),
            ):
                add(check_id, "missing", summary, unchecked_action)

        recovery = self.recovery_readiness()
        add("exact_recovery", recovery.state, recovery.summary, recovery.action)
        try:
            adapter = self.removable_status()
            media_ready = adapter["status"] == "qualified"
            add("physical_media", "ready" if media_ready else "unqualified",
                "Removable-media adapter reports qualified." if media_ready else "Media software checks do not establish physical USB writer qualification.",
                "Complete sacrificial USB write, full readback, failure invalidation and safe-eject qualification after disposable-image tests." if not media_ready else "")
        except (MacLoaderError, OSError, ValueError, RuntimeError) as exc:
            add("physical_media", "blocked",
                "Removable-media discovery could not complete safely.",
                f"Resolve the host discovery error ({type(exc).__name__}) before considering media operations.")

        states = [item["state"] for item in checks]
        overall = "ready" if all(state == "ready" for state in states) else "blocked"
        return {"status": overall, "checks": checks, "synthetic_test_results_are_not_physical_qualification": True}

    @staticmethod
    def _ensure_private_directory(path: Path) -> None:
        root = Path(path).expanduser().absolute()
        for ancestor in (root, *root.parents):
            if ancestor.exists() and ancestor.is_symlink():
                raise ValueError("private workspace contains a symlink boundary")
        try:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt":
                root.chmod(0o700)
                if root.stat().st_mode & 0o077:
                    raise ValueError("private workspace permissions are too broad")
            else:
                IdentityService(root)._assert_private_root()
        except OSError as exc:
            raise ValueError("unable to protect the private workspace") from exc

    @classmethod
    def _write_private_json(cls, path: Path, content: str) -> None:
        destination = Path(path).absolute()
        cls._ensure_private_directory(destination.parent)
        if destination.is_symlink():
            raise ValueError("private workspace destination must not be a symlink")
        fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
        temporary = Path(temporary_name)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            cls._protect_private_file(temporary)
            if destination.is_symlink():
                raise ValueError("private workspace destination became a symlink")
            os.replace(temporary, destination)
            if os.name != "nt" and destination.stat().st_mode & 0o077:
                destination.chmod(0o600)
                if destination.stat().st_mode & 0o077:
                    raise ValueError("private workspace file permissions are too broad")
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _read_private_json(path: Path, label: str) -> Any:
        destination = Path(path)
        if destination.is_symlink() or not destination.is_file():
            raise ValueError(f"{label} is missing or unsafe")
        if os.name != "nt" and destination.stat().st_mode & 0o077:
            raise ValueError(f"{label} permissions are too broad")
        if os.name == "nt":
            IdentityService._assert_private_acl(destination)
        fd: Optional[int] = None
        try:
            fd = os.open(destination, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            status = os.fstat(fd)
            if not stat.S_ISREG(status.st_mode) or status.st_size > 32 * 1024 * 1024:
                raise ValueError(f"{label} exceeds the safe read limit")
            payload = os.read(fd, status.st_size)
            return json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{label} is malformed or unreadable") from exc
        finally:
            if fd is not None:
                os.close(fd)

    @staticmethod
    def _protect_private_file(path: Path) -> None:
        if os.name != "nt":
            path.chmod(0o600)
            if path.stat().st_mode & 0o077:
                raise ValueError("private workspace file permissions are too broad")
            return
        account = getpass.getuser()
        if not account:
            raise ValueError("unable to determine the private workspace owner")
        try:
            result = subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:(R,W)"],
                capture_output=True, text=True, check=False,
            )
        except OSError as exc:
            raise ValueError("unable to protect private workspace file") from exc
        if result.returncode != 0:
            raise ValueError("unable to protect private workspace file")
        IdentityService._assert_private_acl(path)

    @staticmethod
    def _reject_symlink_path(path: Path) -> None:
        for ancestor in (path, *path.parents):
            if ancestor.exists() and ancestor.is_symlink():
                raise ValueError("ACPI import path contains a symlink boundary")
        if not path.is_dir():
            raise ValueError("ACPI import source must be a directory")

    def save(self, configuration: UserConfiguration) -> Path:
        expected = configuration.loaded_base_revision
        if expected is None:
            # A save is a compare-and-swap, not an implicit upsert.  A caller
            # that did not load this id has no trustworthy base revision.  The
            # only safe untracked save is a brand-new draft at revision zero.
            if configuration.revision != 0:
                raise ValueError(
                    "configuration must be loaded before saving an existing or non-zero revision"
                )
            expected = 0
        return self.store.save(configuration, expected_revision=expected)

    def save_revision(self, configuration: UserConfiguration) -> Path:
        expected = configuration.loaded_base_revision
        if expected is None:
            raise ValueError(
                "configuration must be loaded before saving a new revision"
            )
        next_revision = expected + 1
        saved = replace(configuration, revision=next_revision)
        return self.store.save(saved, expected_revision=expected)

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

    def set_identity_reference(self, configuration: UserConfiguration, storage_ref: str) -> UserConfiguration:
        if Path(storage_ref).name != storage_ref or not storage_ref.endswith(".json"):
            raise ValueError("identity reuse requires a local redacted JSON filename")
        return replace(
            configuration,
            identity_ref=IdentityReference("0.1", storage_ref, redacted=True),
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

    def verify_recovery_cache(
        self,
        lock_path: Path,
        image_path: Path,
        chunklist_path: Path,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> RecoveryEvidence:
        lock = self.orchestrator.recovery_service.load_lock(Path(lock_path))
        return self.orchestrator.recovery_service.verify(
            lock, Path(image_path), Path(chunklist_path), cancel=cancel
        )

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
        if cancel and cancel():
            raise ValueError("Dependency resolution cancelled")
        dependencies = self.orchestrator.resolve_dependencies(
            state.evaluation.plan, variant=variant, cancel=cancel
        )
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
        ocvalidate_path: Optional[Path] = None,
        ocvalidate_sha256: Optional[str] = None,
    ) -> EfiBuildResult:
        """Build and validate only the generated EFI output; media is never touched."""
        state, dependencies = self.resolve_dependencies(configuration, snapshot, cancel=cancel)
        if state.evaluation.has_blockers:
            details = "; ".join(
                f"{issue.code}: {issue.remediation}"
                for issue in state.evaluation.issues if issue.blocking
            )
            raise ValueError(f"EFI build blocked by configuration issues: {details}")
        if not dependencies.is_complete:
            unresolved = ", ".join(dependencies.unresolved_requirements)
            raise ValueError(f"EFI build blocked by unresolved dependencies: {unresolved or 'see the BuildPlan'}")
        from macloader.toolchain.loader import ToolchainTrustError, TrustedToolchainLoader
        toolchain_loader = TrustedToolchainLoader()
        try:
            toolchain = toolchain_loader.select() if offline else toolchain_loader.provision()
        except ToolchainTrustError as exc:
            action = "run `macloader toolchain install` while online" if offline else "check the pinned toolchain catalog and network access"
            raise ValueError(f"EFI build requires the verified catalog-pinned toolchain; {action}: {exc}") from exc
        try:
            reviewed_profile = load_reviewed_profile()
            acpi_record = next(record for record in configuration.evidence if record.kind == "acpi")
            evidence_source = self.orchestrator.configuration_service._evidence_source(acpi_record.private_ref)
            if evidence_source is None:
                raise ValueError("private ACPI evidence source is missing or unsafe")
            private_acpi_capture = evidence_source.parent
            expected_evidence_digest = AcpiProcessor.capture_evidence_digest(
                private_acpi_capture, acpi_record.bios_binding, snapshot.snapshot_id
            )
        except (StopIteration, OSError, ValueError) as exc:
            raise ValueError(f"EFI build blocked by machine-bound ACPI evidence: {exc}") from exc
        artifacts = self.orchestrator.fetch_dependencies(
            dependencies, offline=offline, plan=state.evaluation.plan, cancel=cancel
        )
        with self.orchestrator.lease_dependencies(dependencies, cancel=cancel) as leased_artifacts:
            return self.orchestrator.build_efi(
                state.evaluation.plan,
                dependencies,
                leased_artifacts,
                Path(output),
                toolchain=toolchain,
                reviewed_profile=reviewed_profile,
                private_acpi_capture=private_acpi_capture,
                expected_acpi_evidence_digest=expected_evidence_digest,
                identity_reference=configuration.identity_ref,
                cancel=cancel,
                ocvalidate_path=ocvalidate_path,
                ocvalidate_sha256=ocvalidate_sha256,
            )

    def discover_recovery(
        self,
        transport: Optional[Callable[[str, Mapping[str, str], bytes], DiscoveryResponse]] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> RecoveryDiscoveryResult:
        """Query Apple once and record the redacted outcome as current evidence.

        A cancelled or superseded query records nothing, so it cannot replace
        newer evidence with an observation the operator abandoned.
        """
        policy = self.orchestrator.recovery_service.policy
        try:
            if transport is None and cancel is None:
                result = self.orchestrator.discover_recovery()
            else:
                result = self.orchestrator.discover_recovery(transport=transport, cancel=cancel)
        except (MacLoaderError, OSError) as exc:
            if not (cancel and cancel()) and "cancelled" not in str(exc).lower():
                try:
                    self._record_recovery_evidence(
                        RecoveryDiscoveryEvidence.from_error(exc, policy.digest, policy.target.digest)
                    )
                except (OSError, ValueError) as record_error:
                    # Never let a recording problem mask the discovery error.
                    exc.add_note(f"Recovery evidence could not be recorded: {record_error}")
            raise
        if not (cancel and cancel()):
            self._record_recovery_evidence(RecoveryDiscoveryEvidence.from_result(result, policy.digest))
        return result

    def _record_recovery_evidence(self, evidence: RecoveryDiscoveryEvidence) -> None:
        path = Path(DEFAULT_RECOVERY_EVIDENCE_PATH)
        self._write_private_json(path, json.dumps(evidence.to_dict(), sort_keys=True, indent=2) + "\n")

    def recovery_readiness(self) -> RecoveryReadiness:
        """Assess the exact-Recovery gate from the currently recorded evidence."""
        policy = self.orchestrator.recovery_service.policy
        path = Path(DEFAULT_RECOVERY_EVIDENCE_PATH)
        data: Any = None
        load_error: Optional[str] = None
        try:
            if path.exists() or path.is_symlink():
                for ancestor in path.parents:
                    if ancestor.is_symlink():
                        raise ValueError("Recovery evidence path contains a symlink boundary")
                data = self._read_private_json(path, "Recovery discovery evidence")
        except (OSError, ValueError) as exc:
            load_error = str(exc)
        return assess_recovery_evidence(
            data, policy_digest=policy.digest, target_digest=policy.target.digest, load_error=load_error
        )

    def acquire_recovery(
        self,
        result: RecoveryDiscoveryResult,
        binding: RecoveryBinding,
        destination: Path,
        cancel: Optional[Callable[[], bool]] = None,
        resume: bool = True,
        verified_artifacts: Optional[Mapping[str, str]] = None,
        require_verified: bool = False,
    ) -> tuple[RecoveryLock, RecoveryBundle]:
        return self.orchestrator.recovery_service.acquire(
            result, binding, destination, cancel=cancel, resume=resume,
            verified_artifacts=verified_artifacts, require_verified=require_verified,
        )

    def persist_recovery_result(
        self, lock: RecoveryLock, evidence: RecoveryEvidence, destination: Path
    ) -> tuple[Path, Path, Path]:
        return self.orchestrator.recovery_service.save_verified_bundle(lock, evidence, Path(destination))

    def derive_recovery_binding(
        self,
        configuration: UserConfiguration,
        snapshot: HardwareSnapshot,
        toolchain: Optional[ToolchainSelection],
        manifest: BuildManifest,
        efi_output: Optional[Path] = None,
    ) -> RecoveryBinding:
        state = self.evaluate(configuration, snapshot)
        if state.evaluation.has_blockers or state.evaluation.accepted is None:
            raise ValueError("Recovery binding requires an accepted current configuration")
        if toolchain is None:
            toolchain = self.orchestrator.trusted_toolchain()
        if efi_output is not None:
            validation = self.orchestrator.builder.validate_tree(
                Path(efi_output), toolchain=toolchain, expected_manifest=manifest
            )
            if validation.status != "VALID":
                raise ValueError(
                    "EFI output failed the trusted manifest validation: "
                    + "; ".join(validation.errors)
                )
        dependencies = self.orchestrator.resolve_dependencies(state.evaluation.plan)
        if not dependencies.is_complete:
            raise ValueError("Recovery binding requires a complete current dependency resolution")
        reviewed_profile = load_reviewed_profile()
        acpi_record = next((record for record in configuration.evidence if record.kind == "acpi"), None)
        if acpi_record is None:
            raise ValueError("Recovery binding requires the current verified ACPI evidence")
        evidence_source = self.orchestrator.configuration_service._evidence_source(acpi_record.private_ref)
        if evidence_source is None:
            raise ValueError("Recovery binding requires a safe current ACPI evidence source")
        expected_acpi_digest = AcpiProcessor.capture_evidence_digest(
            evidence_source.parent, acpi_record.bios_binding, snapshot.snapshot_id
        )
        expected_build_digest = canonical_json_digest({
            "plan_digest": state.evaluation.plan.canonical_digest(),
            "dependency_digest": dependencies.canonical_digest(),
            "toolchain_digest": toolchain.digest,
            "identity_digest": manifest.identity_digest,
            "output_digest": manifest.output_digest,
            "schema_digest": manifest.schema_digest,
            "profile_digest": effective_profile_digest(
                reviewed_profile, dict(state.evaluation.plan.effective_option_selections)
            ),
            "acpi_digest": manifest.acpi_digest,
            "evidence_digests": [expected_acpi_digest],
            "usb_policy_state": manifest.usb_policy_state,
            "usb_first_install_route": manifest.usb_first_install_route,
            "schema_version": manifest.schema_version,
        })
        return self.orchestrator.recovery_service.derive_binding(
            configuration,
            state.evaluation.plan,
            toolchain,
            manifest,
            self.orchestrator.resolver.catalog_digest(),
            expected_artifact_lock_digest=dependencies.to_artifact_lock().digest,
            expected_build_digest=expected_build_digest,
            expected_profile_digest=effective_profile_digest(
                reviewed_profile, dict(state.evaluation.plan.effective_option_selections)
            ),
            expected_evidence_digests=(expected_acpi_digest,),
            expected_identity_reference=(
                configuration.identity_ref.storage_ref if configuration.identity_ref is not None else None
            ),
            workflow_capability=self.orchestrator._workflow_capability,
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
