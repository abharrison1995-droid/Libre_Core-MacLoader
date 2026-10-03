"""Resumable semantic engine; UI clients never choose workflow prerequisites."""
from dataclasses import replace
from datetime import datetime, timezone
import os
import tempfile
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from macloader.autoloader.models import ActionKind, AutoloaderSession, NextAction, Stage
from macloader.autoloader.store import AutoloaderSessionStore
from macloader.config import DEFAULT_PRIVATE_DIR
from macloader.configuration.campaign_match import CampaignMatch, match_campaign
from macloader.configuration.observations import reconcile_configuration
from macloader.configuration.store import ConfigurationStore
from macloader.detection.sanitize import sanitize_hardware_snapshot
from macloader.domain.configuration import UserConfiguration
from macloader.domain.contracts import IdentityReference
from macloader.identity.service import IdentityService, IdentityServiceError
from macloader.domain.evidence import EvidenceCompleteness
from macloader.domain.hardware import HardwareSnapshot
from macloader.workflow.service import WorkflowService


if TYPE_CHECKING:
    from macloader.evidence.usb_capture import UsbEvidenceCollector


class AutoloaderService:
    def __init__(self, workflow: Optional[WorkflowService] = None, root: Optional[Path] = None):
        self.root = Path(root or (DEFAULT_PRIVATE_DIR / "campaigns")).absolute()
        self.store = AutoloaderSessionStore(self.root)
        self.workflow = workflow or WorkflowService(store=ConfigurationStore(self.root / "configurations"), private_root=self.root)
        self.session: Optional[AutoloaderSession] = None
        self.snapshot: Optional[HardwareSnapshot] = None
        self.configuration: Optional[UserConfiguration] = None
        self.match: Optional[CampaignMatch] = None
        self.handlers: dict[Stage, Callable[[], None]] = {Stage.ACPI: self._capture_acpi, Stage.TOOLS: self._prepare_tools, Stage.DEPENDENCIES: self._prepare_dependencies, Stage.BUILD: self._build_efi}
        self._cancel: Callable[[], bool] = lambda: False
        self._blocker: Optional[NextAction] = None
        self.usb_collector: Optional["UsbEvidenceCollector"] = None
        self.identity_root = self.root / "identities"
        self.workflow.orchestrator.builder.identity_store_dir = self.identity_root

    @staticmethod
    def _private_machine_material(snapshot: HardwareSnapshot) -> str:
        for field in (snapshot.uuid, snapshot.serial_number):
            if field and "REDACTED" not in field.upper() and field.strip().lower() not in {"unknown", "none", "default string", "to be filled by o.e.m."}:
                return field.strip()
        if os.name != "nt":
            path = Path("/etc/machine-id")
            if path.is_file():
                value = path.read_text().strip()
                if value and len(value) == 32:
                    return "linux-os:" + value
        raise ValueError("A private stable machine binding is unavailable")

    def start(self, snapshot: Optional[HardwareSnapshot] = None, *, private_material: Optional[str] = None) -> NextAction:
        """Probe before resuming; supplied snapshots/material are engineering seams."""
        raw = snapshot or self.workflow.orchestrator.probe_hardware()
        candidate = self.workflow.orchestrator.db.candidate_campaign(raw)
        self._blocker = None
        self.usb_collector = None
        self.session = None
        self.configuration = None
        self.snapshot = sanitize_hardware_snapshot(raw)
        if candidate is None:
            self._blocker = NextAction(Stage.MATCH, ActionKind.BLOCKED, "This machine and BIOS do not match a reviewed reference campaign.", "CAMPAIGN_NOT_FOUND")
            return self._blocker
        try:
            material = private_material if private_material is not None else self._private_machine_material(raw)
            binding = self.store.machine_binding(material)
            session = self.store.find(binding, candidate.campaign_id)
        except ValueError:
            self._blocker = NextAction(Stage.MATCH, ActionKind.BLOCKED, "MacLoader cannot safely identify a unique saved session. Your saved work is preserved; open Engineering diagnostics.", "SESSION_BINDING_AMBIGUOUS")
            return self._blocker
        safe = self.snapshot
        if session is None:
            draft = self.workflow.orchestrator.new_configuration(safe)
            draft = replace(draft, target=candidate.release.target(), loaded_base_revision=0)
            draft = reconcile_configuration(draft, safe)
            self.workflow.save(draft)
            session = AutoloaderSession(candidate.campaign_id, candidate.digest, binding, draft.configuration_id, safe.snapshot_id)
            self.workflow.save_snapshot(draft, safe)
            self.store.save(session, None)
        else:
            # Private HMAC and exact firmware campaign corroborate the machine;
            # retain the session snapshot identity while updating current facts.
            safe = replace(safe, snapshot_id=session.snapshot_id)
            draft = self.workflow.load(session.configuration_id)
            updated = reconcile_configuration(draft, safe)
            if updated.semantic_digest != draft.semantic_digest:
                self.workflow.save_revision(updated)
            if session.campaign_digest != candidate.digest:
                session.campaign_digest = candidate.digest
                session.artifacts.clear()
                updated = replace(updated, acknowledgements=(),
                                  target=candidate.release.target(),
                                  option_selections=candidate.configuration_policy.default_selections(),
                                  policy_version=candidate.configuration_policy.policy_version)
                loaded = self.workflow.load(session.configuration_id)
                self.workflow.save_revision(replace(updated, loaded_base_revision=loaded.revision))
            for attempt in session.actions:
                if attempt.get("state") == "started":
                    attempt["state"] = "interrupted"
            session.artifacts = {key: artifact for key, artifact in session.artifacts.items()
                                 if artifact.get("input_digest") == self.workflow.load(session.configuration_id).semantic_digest}
            self.workflow.save_snapshot(draft, safe)
            previous = session.revision
            session.revision += 1
            self.store.save(session, previous)
        self.session, self.snapshot = session, safe
        self.configuration = self.workflow.load(session.configuration_id)
        self.match = match_campaign(safe, self.workflow.orchestrator.db)
        return self.next_action()

    def next_action(self) -> NextAction:
        if self._blocker:
            return self._blocker
        if self.session is None or self.configuration is None or self.snapshot is None or self.match is None:
            return NextAction(Stage.DETECT, ActionKind.AUTOMATIC, "Identify this laptop.")
        campaign = self.match.campaign
        if campaign is not None and (
            self.configuration.target != campaign.release.target()
            or self.configuration.option_selections != campaign.configuration_policy.default_selections()
        ):
            return NextAction(Stage.HARDWARE, ActionKind.BLOCKED, "This session has engineering policy changes. Saved work is preserved; review those changes before using the guided prototype.", "POLICY_SELECTION_DRIFT")
        if self.match.mismatches:
            return NextAction(Stage.HARDWARE, ActionKind.BLOCKED, "Detected hardware differs from the reviewed campaign. Your session is preserved; resolve the hardware mismatch in diagnostics.", "HARDWARE_MISMATCH")
        if self.match.unknown:
            return NextAction(Stage.HARDWARE, ActionKind.BLOCKED, "Some required hardware facts could not be proven. MacLoader has preserved the session and needs focused evidence collection.", "HARDWARE_UNKNOWN")
        for kind, stage in (("acpi", Stage.ACPI), ("usb", Stage.USB)):
            records = [record for record in self.configuration.evidence if record.kind == kind]
            if not any(record.completeness == EvidenceCompleteness.COMPLETE or (kind == "usb" and record.completeness == EvidenceCompleteness.PARTIAL and record.physical_port_evidence and all("logical USB-C correlation unresolved" in check for check in record.unresolved_checks)) for record in records):
                if kind == "usb":
                    begun = "usb_wizard" in self.session.artifacts
                    return NextAction(Stage.USB, ActionKind.HUMAN, self.usb_collector.instruction if self.usb_collector else "Remove external USB devices. Port checks use a USB 3 device and a USB 2 device or cable. MacLoader records the routes; you only move the device.", "USB_WAITING" if begun else "USB_PHYSICAL_EVIDENCE_REQUIRED", () if begun else ("Begin port checks",))
                return NextAction(stage, ActionKind.AUTOMATIC if stage in self.handlers else ActionKind.BLOCKED,
                                  ("Collect firmware tables." if stage in self.handlers else "Firmware-table collection is not available in this prototype yet. Use Engineering to import existing evidence.")
                                  if kind == "acpi" else "Identify this laptop’s physical USB ports by moving the test device when prompted.",
                                  "ACPI_CAPTURE_REQUIRED" if kind == "acpi" else "USB_PHYSICAL_EVIDENCE_REQUIRED")
        for stage, artifact in ((Stage.TOOLS, "tools"),):
            if artifact not in self.session.artifacts:
                return NextAction(stage, ActionKind.AUTOMATIC if stage in self.handlers else ActionKind.BLOCKED, "Prepare verified tools and dependencies.", "SOFTWARE_PREPARATION_REQUIRED")
        identity_valid = False
        if self.configuration.identity_ref is not None:
            try:
                IdentityService(self.identity_root).reuse(self.configuration.identity_ref)
                identity_valid = True
            except IdentityServiceError:
                pass
        if not identity_valid:
            choices = ("Generate new", "Reuse existing") if len(self._reusable_identities()) == 1 else ("Generate new",)
            return NextAction(Stage.IDENTITY, ActionKind.HUMAN, "Choose a private identity for this installation.", choices=choices)
        evaluation = self.workflow.evaluate(self.configuration, self.snapshot).evaluation
        if any(issue.blocking and issue.code != "ACKNOWLEDGEMENT_REQUIRED" for issue in evaluation.issues):
            return NextAction(Stage.BUILD, ActionKind.BLOCKED, "Configuration validation needs attention. No destructive operation occurred; your evidence and private identity are preserved.", "CONFIGURATION_BLOCKED")
        if any(issue.code == "ACKNOWLEDGEMENT_REQUIRED" for issue in evaluation.issues):
            return NextAction(Stage.ACCEPTANCE, ActionKind.HUMAN, "Review and accept the experimental T480s prototype configuration.", choices=("Accept prototype",))
        if evaluation.has_blockers:
            return NextAction(Stage.BUILD, ActionKind.BLOCKED, "Configuration validation needs attention. No destructive operation occurred; your evidence and private identity are preserved.", "CONFIGURATION_BLOCKED")
        if "dependencies" not in self.session.artifacts:
            return NextAction(Stage.DEPENDENCIES, ActionKind.AUTOMATIC, "Acquire and verify the reviewed dependencies.")
        if "efi" not in self.session.artifacts:
            return NextAction(Stage.BUILD, ActionKind.AUTOMATIC if Stage.BUILD in self.handlers else ActionKind.BLOCKED, "Build and validate the EFI.", "EFI_BUILD_REQUIRED")
        return NextAction(Stage.RECOVERY_MODE, ActionKind.BLOCKED, "Resolve Recovery for the frozen target. Qualification requires proof of the exact build; no installation is authorized.", "RECOVERY_REQUIRED")

    def advance_until_blocked(self, cancel: Optional[Callable[[], bool]] = None) -> NextAction:
        """Run only registered safe operations; recompute prerequisites after each."""
        self._cancel = cancel or (lambda: False)
        for _ in range(32):
            action = self.next_action()
            if action.code == "USB_WAITING" and not (cancel and cancel()):
                try:
                    if self.usb_collector is None:
                        self._prepare_usb()
                    self._poll_usb()
                except Exception as exc:
                    from macloader.evidence.acpi_capture import CaptureError
                    self._blocker = NextAction(Stage.USB, ActionKind.BLOCKED, str(exc) if isinstance(exc, CaptureError) else "USB evidence collection could not complete. Saved observations are preserved; open Engineering diagnostics.", exc.code if isinstance(exc, CaptureError) else "USB_CAPTURE_FAILED")
                    return self._blocker
                if self.next_action().stage != Stage.USB:
                    continue
                return self.next_action()
            if action.kind != ActionKind.AUTOMATIC:
                return action
            if cancel and cancel():
                return NextAction(action.stage, ActionKind.BLOCKED, "Preparation paused. Your saved work is preserved.", "CANCELLED")
            handler = self.handlers.get(action.stage)
            if handler is None:
                if action.stage == Stage.DETECT:
                    self.start()
                    continue
                return action
            if self.session is None or self.configuration is None:
                raise ValueError("No active guided session")
            record = dict(stage=action.stage.value, state="started", input_digest=self.configuration.semantic_digest,
                          campaign_digest=self.session.campaign_digest, started_at=datetime.now(timezone.utc).isoformat())
            self.session.actions.append(record)
            self._save_session()
            try:
                handler()
            except Exception as exc:
                from macloader.evidence.acpi_capture import CaptureError
                record["state"] = "failed"
                record["code"] = exc.code if isinstance(exc, CaptureError) else "AUTOMATIC_ACTION_FAILED"
                diagnostic = self.root / "diagnostics" / f"{self.session.session_id}-{len(self.session.actions)}.json"
                self.workflow._ensure_private_directory(diagnostic.parent)
                self.workflow._write_private_json(diagnostic, json.dumps({"exception": type(exc).__name__, "detail": str(exc), "stage": action.stage.value}))
                record["diagnostic_ref"] = str(diagnostic)
                self._save_session()
                self._blocker = NextAction(action.stage, ActionKind.BLOCKED, str(exc) if isinstance(exc, CaptureError) else "Preparation could not complete this step. No destructive operation occurred; saved inputs are preserved. Open diagnostics or retry.", str(record["code"]))
                return self._blocker
            record["completed_at"] = datetime.now(timezone.utc).isoformat()
            if self.next_action() == action:
                record["state"] = "failed"
                record["code"] = "NO_PROGRESS"
                self._save_session()
                return NextAction(action.stage, ActionKind.BLOCKED, "This operation made no progress; saved inputs are preserved.", "NO_PROGRESS")
            record["state"] = "complete"
            self._save_session()
        raise RuntimeError("Guided action limit exceeded")

    def _record_artifact(self, kind: str, **metadata: str) -> None:
        if self.configuration is None or self.session is None:
            raise ValueError("No active campaign")
        self.session.artifacts[kind] = {"input_digest": self.configuration.semantic_digest, **metadata}
        self._save_session()

    def _prepare_tools(self) -> None:
        from macloader.toolchain.loader import TrustedToolchainLoader
        if self._cancel():
            raise ValueError("Cancelled")
        tools = TrustedToolchainLoader().provision()
        self._record_artifact("tools", digest=tools.digest)

    def _prepare_dependencies(self) -> None:
        if self.configuration is None or self.snapshot is None:
            raise ValueError("No active campaign")
        state, dependencies = self.workflow.resolve_dependencies(self.configuration, self.snapshot, cancel=self._cancel)
        if state.evaluation.has_blockers or not dependencies.is_complete:
            raise ValueError("Dependencies are blocked by current configuration")
        self.workflow.orchestrator.fetch_dependencies(dependencies, offline=False, plan=state.evaluation.plan, cancel=self._cancel)
        self._record_artifact("dependencies", digest=dependencies.canonical_digest())

    def _build_efi(self) -> None:
        from macloader.evidence.acpi_capture import CaptureError
        if self.configuration is None or self.snapshot is None or self.session is None:
            raise ValueError("No active campaign")
        if self.snapshot.raw_evidence.get("synthetic_fixture"):
            raise CaptureError("SYNTHETIC_BUILD_DISABLED", "Synthetic fixtures cannot produce a production installation EFI. Use the deterministic test harness.")
        parent = self.root / "builds" / self.session.session_id
        self.workflow._ensure_private_directory(parent)
        output = parent / self.configuration.semantic_digest
        # Crash after publication: validate and adopt only if current bindings match.
        if output.exists():
            from macloader.domain.contracts import BuildManifest
            from macloader.toolchain.loader import TrustedToolchainLoader
            manifest = BuildManifest.from_dict(self.workflow._read_private_json(output / "manifest.json", "EFI manifest"))
            tools = TrustedToolchainLoader().select()
            self.workflow.derive_recovery_binding(self.configuration, self.snapshot, tools, manifest, output)
            self._record_artifact("efi", path=str(output), digest=manifest.build_digest)
            return
        result = self.workflow.build_efi_preview(self.configuration, self.snapshot, output, offline=False, cancel=self._cancel)
        if result.validation.status != "VALID":
            raise ValueError("EFI validation failed")
        # Published identity-bearing files remain inside the protected campaign tree.
        for directory in [result.output_dir, *[p for p in result.output_dir.rglob("*") if p.is_dir()]]:
            self.workflow._ensure_private_directory(directory)
        for path in result.output_dir.rglob("*"):
            if path.is_file():
                self.workflow._protect_private_file(path)
        self._record_artifact("efi", path=str(result.output_dir), digest=result.manifest.build_digest)

    @staticmethod
    def _usb_platform() -> str:
        return sys.platform

    def _prepare_usb(self) -> None:
        from macloader.evidence.usb_capture import UsbEvidenceCollector, UsbCaptureStep, LinuxUsbEventProvider, collect_firmware_usb_addresses
        from macloader.evidence.usb import UsbEvidenceSession, UsbPortObservation
        from macloader.evidence.acpi_capture import CaptureError
        from macloader.domain.evidence import EvidenceConfidence
        from macloader.toolchain.loader import TrustedToolchainLoader
        from macloader.configuration.observations import EVIDENCE_SCOPES, scope_digest
        from macloader.domain.contracts import canonical_json_digest
        if self.snapshot is None or self.configuration is None or self.session is None or self.match is None or self.match.campaign is None:
            raise ValueError("No active campaign")
        if self.snapshot.raw_evidence.get("synthetic_fixture"):
            raise CaptureError("SYNTHETIC_CAPTURE_DISABLED", "Synthetic fixtures cannot collect physical USB evidence from this host.")
        if self._usb_platform() == "win32":
            raise CaptureError("USB_PROVIDER_UNAVAILABLE", "Windows physical-to-firmware USB correlation is not yet qualified. Use the Linux collector or Engineering evidence import; no port number will be guessed.")
        acpi = next(r for r in self.configuration.evidence if r.kind == "acpi")
        loader = TrustedToolchainLoader()
        tools = loader.provision()
        if not tools.acpi_compiler_path or not tools.acpi_compiler_sha256:
            raise CaptureError("USB_FIRMWARE_UNAVAILABLE", "Verified firmware route tooling is unavailable.")
        from macloader.build.acpi import AcpiProcessor
        AcpiProcessor.capture_evidence_digest(Path(acpi.private_ref).parent, acpi.bios_binding, self.snapshot.snapshot_id)
        addresses = collect_firmware_usb_addresses(Path(acpi.private_ref).parent, Path(tools.acpi_compiler_path), tools.acpi_compiler_sha256, self.root / "usb-private", self._cancel)
        provider = LinuxUsbEventProvider(self.session.machine_binding, addresses)
        policy = self.match.campaign.evidence_policy["usb_capture"]
        binding = canonical_json_digest({"campaign": self.session.campaign_digest, "acpi": acpi.digest, "usb": scope_digest(self.snapshot, EVIDENCE_SCOPES["usb"])})
        path = self.root / "usb" / f"{self.session.session_id}-{binding}.json"
        self.workflow._ensure_private_directory(path.parent)
        if path.exists():
            progress = self.workflow._read_private_json(path, "USB wizard")
            evidence = UsbEvidenceSession.from_dict(progress["evidence"])
            if evidence.snapshot_id != self.snapshot.snapshot_id or evidence.bios_binding != acpi.bios_binding or evidence.private_ref != str(path.with_suffix(".evidence.json")):
                raise CaptureError("USB_PROGRESS_INVALID", "Saved USB progress has an incompatible machine binding.")
        else:
            progress = None
            internal = []
            for event in provider.enumerate():
                label = policy["internal_labels"].get(event.logical_port)
                if label and event.internal and event.port_address is not None:
                    internal.append(UsbPortObservation(label, event.logical_port, "internal", f"{event.speed_mbps}Mbps", event.controller,
                                                       internal_device=True, port_address=event.port_address, namespace_path=event.namespace_path))
            evidence = UsbEvidenceSession(self.snapshot.snapshot_id, acpi.bios_binding, str(path.with_suffix(".evidence.json")), "guided-usb-1", tuple(internal), EvidenceConfidence.HIGH)
        steps = tuple(UsbCaptureStep(**step) for step in policy["steps"])
        self.usb_collector = UsbEvidenceCollector(evidence, steps, provider,
            lambda payload: self.workflow._write_private_json(path, json.dumps(payload)), progress)
        self.session.artifacts["usb_wizard"] = {"input_digest": self.configuration.semantic_digest, "binding": binding}
        self._save_session()

    def _poll_usb(self) -> None:
        from macloader.configuration.observations import EVIDENCE_SCOPES, scope_digest
        if self.usb_collector is None or self.configuration is None or self.snapshot is None or self.match is None or self.match.campaign is None:
            raise ValueError("No USB collector")
        if not self.usb_collector.poll():
            return
        evidence = self.usb_collector.session
        from macloader.evidence.acpi_capture import CaptureError
        observed = self._current_bound_snapshot()
        if scope_digest(observed, EVIDENCE_SCOPES["usb"]) != scope_digest(self.snapshot, EVIDENCE_SCOPES["usb"]):
            raise CaptureError("USB_MACHINE_CHANGED", "Machine or BIOS changed during physical port checks; saved observations need recollection.")
        policy = self.match.campaign.evidence_policy["usb_capture"]
        routes = {o.logical_port for o in evidence.observations}
        if not set(policy["minimum_routes"]) <= routes:
            from macloader.evidence.acpi_capture import CaptureError
            raise CaptureError("USB_SCOPE_INCOMPLETE", "Port movements are saved, but required internal USB routes remain unproven. Open Engineering diagnostics; no complete map was claimed.")
        self.workflow._write_private_json(Path(evidence.private_ref), json.dumps(evidence.to_dict()))
        record = replace(evidence.to_evidence_record(), input_scope=EVIDENCE_SCOPES["usb"], input_digest=scope_digest(self.snapshot, EVIDENCE_SCOPES["usb"]))
        self.save_configuration(self.workflow.add_evidence(self.configuration, record))

    def _current_bound_snapshot(self) -> HardwareSnapshot:
        from macloader.evidence.acpi_capture import CaptureError
        raw = self.workflow.orchestrator.probe_hardware()
        if self.session is None or self.store.machine_binding(self._private_machine_material(raw)) != self.session.machine_binding:
            raise CaptureError("MACHINE_CHANGED", "Machine binding changed during collection; nothing was accepted.")
        return replace(sanitize_hardware_snapshot(raw), snapshot_id=self.session.snapshot_id)

    def _capture_acpi(self) -> None:
        from macloader.evidence.acpi_capture import CaptureError, LinuxElevatedAcpiCaptureProvider, WindowsAcpiCaptureProvider
        from macloader.toolchain.loader import TrustedToolchainLoader
        if self.snapshot is None or self.configuration is None:
            raise ValueError("No bound machine")
        if self.snapshot.raw_evidence.get("synthetic_fixture"):
            raise CaptureError("SYNTHETIC_CAPTURE_DISABLED", "Synthetic fixtures cannot collect this host’s firmware. No host tables were read.")
        current = self._current_bound_snapshot
        if os.name != "nt":
            updated = self.workflow.collect_acpi(self.configuration, self.snapshot, LinuxElevatedAcpiCaptureProvider(), current, self._cancel)
        else:
            loader = TrustedToolchainLoader()
            loader.provision()
            tool = loader.record().firmware_capture
            if tool is None:
                raise CaptureError("ACPI_TOOL_MISSING", "The trusted firmware capture tool is unavailable for this host.")
            self.workflow._ensure_private_directory(self.root)
            with tempfile.TemporaryDirectory(prefix=".capture-", dir=self.root) as temporary:
                provider = WindowsAcpiCaptureProvider(loader._verify_file(tool), tool.sha256, Path(temporary))
                updated = self.workflow.collect_acpi(self.configuration, self.snapshot, provider, current, self._cancel)
        self.save_configuration(updated)

    def _save_session(self) -> None:
        if self.session is None:
            raise ValueError("No active guided session")
        previous = self.session.revision
        self.session.revision += 1
        self.store.save(self.session, previous)

    def save_configuration(self, configuration: UserConfiguration) -> None:
        self.workflow.save_revision(configuration)
        self.configuration = self.workflow.load(configuration.configuration_id)
        if self.session is not None:
            self.session.artifacts = {key: artifact for key, artifact in self.session.artifacts.items() if artifact.get("input_digest") == self.configuration.semantic_digest}
            self.session.checkpoints.clear()
            self._save_session()

    def _reusable_identities(self) -> tuple[str, ...]:
        refs = []
        if self.identity_root.is_dir():
            identities = IdentityService(self.identity_root)
            for path in sorted(self.identity_root.glob("*.json")):
                try:
                    identities.reuse(IdentityReference("0.1", path.name, True))
                    refs.append(path.name)
                except IdentityServiceError:
                    continue
        return tuple(refs)

    def perform_choice(self, choice: str, cancel: Optional[Callable[[], bool]] = None) -> NextAction:
        self._cancel = cancel or (lambda: False)
        action = self.next_action()
        if action.kind != ActionKind.HUMAN or choice not in action.choices:
            raise ValueError("This choice is not valid at the current checkpoint")
        if self.configuration is None or self.snapshot is None:
            raise ValueError("No active guided configuration")
        if action.stage == Stage.USB:
            self._prepare_usb()
        elif action.stage == Stage.ACCEPTANCE:
            updated = self.configuration
            policy = self.workflow.orchestrator.configuration_service.policy
            for option in policy.options.values():
                if option.requires_acknowledgement and option.option_id in updated.selected_options():
                    updated = self.workflow.orchestrator.configuration_service.acknowledge(updated, option.option_id, option.explanation)
            self.save_configuration(updated)
        elif action.stage == Stage.IDENTITY:
            if choice == "Reuse existing":
                refs = self._reusable_identities()
                if len(refs) != 1:
                    raise IdentityServiceError("Existing identity selection changed")
                private = IdentityService(self.identity_root).reuse(IdentityReference("0.1", refs[0], True))
            else:
                if self.snapshot.raw_evidence.get("synthetic_fixture"):
                    raise IdentityServiceError("Synthetic fixtures cannot create a real private identity")
                from macloader.toolchain.loader import TrustedToolchainLoader
                selection = TrustedToolchainLoader().provision()
                if selection.identity_tool_path is None:
                    raise IdentityServiceError("Verified private identity tool is unavailable")
                identities = IdentityService(self.identity_root, Path(selection.identity_tool_path))
                private = identities.store(identities.generate(allow_real=True))
            self.save_configuration(self.workflow.set_identity_reference(self.configuration, private.storage_ref))
        else:
            raise ValueError("Checkpoint handler is unavailable")
        if self.session is not None:
            self.session.actions.append(dict(stage=action.stage.value, state="human-confirmed",
                                             campaign_digest=self.session.campaign_digest,
                                             input_digest=self.configuration.semantic_digest,
                                             completed_at=datetime.now(timezone.utc).isoformat()))
            self._save_session()
        return self.advance_until_blocked(cancel=cancel)

    def review_summary(self) -> str:
        if self.snapshot is None or self.match is None or self.match.campaign is None:
            return "No reviewed campaign selected."
        campaign = self.match.campaign
        graphics = "Intel UHD 620" if "graphics.igpu" not in (*self.match.unknown, *self.match.mismatches) else "unproven or different graphics"
        audio = "Realtek ALC257" if "audio.codec" not in (*self.match.unknown, *self.match.mismatches) else "unproven or different audio"
        network = "Intel networking" if not any(field in (*self.match.unknown, *self.match.mismatches) for field in ("wifi.identity", "ethernet.identity", "bluetooth.identity")) else "network identity needs evidence"
        return (f"Experimental T480s prototype\n"
                f"Detected: Lenovo ThinkPad T480s {self.snapshot.machine_type}; BIOS {campaign.bios_binding}\n"
                f"Detected: {graphics}; {audio}; {network}\n"
                f"Target: {campaign.release.product_name} {campaign.release.version} ({campaign.release.build})\n"
                "Reviewed profile is experimental and has not been physically accepted.")

    def public_status(self) -> dict[str, object]:
        return {"machine": "Lenovo ThinkPad T480s" if self.session else "Unmatched machine",
                "experimental": True, "action": self.next_action().to_dict(),
                "destructive_operation_performed": False}
