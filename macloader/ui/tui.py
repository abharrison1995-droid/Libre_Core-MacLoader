"""Keyboard-accessible Textual presentation over the shared workflow service."""

from pathlib import Path
import json
from typing import Optional

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Label, Static

from macloader.domain.configuration import UserConfiguration
from macloader.domain.hardware import HardwareSnapshot
from macloader.domain.dependencies import ArtifactVariant
from macloader.domain.recovery import RecoveryBinding, RecoveryState
from macloader.evidence.acpi import AcpiEvidenceBundle
from macloader.evidence.usb import UsbEvidenceSession
from macloader.removable.writer import RemovableDevice
from macloader.workflow.service import WorkflowService, WorkflowState
from macloader.recovery.discovery import RecoveryDiscoveryResult


class WorkflowApp(App[None]):
    """Non-destructive workflow client; CLI and TUI use identical service calls."""

    TITLE = "Libre_Core MacLoader"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("s", "save", "Save draft"),
        ("c", "cancel", "Cancel current action"),
    ]

    def __init__(
        self,
        fixture: Optional[Path] = None,
        config_id: Optional[str] = None,
        service: Optional[WorkflowService] = None,
    ) -> None:
        super().__init__()
        self.fixture = fixture
        self.config_id = config_id
        self.service = service or WorkflowService()
        self._draft: Optional[UserConfiguration] = None
        self._snapshot: Optional[HardwareSnapshot] = None
        self._cancel_requested = False
        self._recovery_result: Optional[RecoveryDiscoveryResult] = None

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="workflow-content"):
            yield Static("Loading shared workflow…", id="workflow-status")
            yield Static("", id="workflow-stages")
            yield Label("Exact Recovery target")
            with Horizontal():
                yield Input("15.0", placeholder="version", id="version-input")
                yield Input("24A335", placeholder="build", id="build-input")
                yield Button("Apply target", id="apply-target", variant="primary")
            yield Label("Policy option")
            with Horizontal():
                yield Input(placeholder="option.id", id="option-id-input")
                yield Input(placeholder="value", id="option-value-input")
                yield Button("Set option", id="set-option")
            yield Label("Experimental acknowledgement")
            with Horizontal():
                yield Input(placeholder="rule or option id", id="ack-rule-input")
                yield Input(placeholder="exact warning text", id="ack-warning-input")
                yield Button("Acknowledge", id="acknowledge")
            with Horizontal():
                yield Button("Evaluate", id="evaluate", variant="primary")
                yield Button("Save revision", id="save")
                yield Button("Cancel", id="cancel")
            yield Label("Configuration import/export and evidence review")
            with Horizontal():
                yield Input(placeholder="JSON file path", id="config-path-input")
                yield Button("Import", id="import-config")
                yield Button("Migrate", id="migrate-config")
                yield Button("Export redacted", id="export-config")
            with Horizontal():
                yield Input(placeholder="evidence JSON path", id="evidence-path-input")
                yield Input(placeholder="usb or acpi", id="evidence-kind-input")
                yield Button("Import evidence", id="import-evidence")
            yield Label("Plan, dependency, EFI and Recovery stages")
            with Horizontal():
                yield Button("Review hardware support", id="support-review")
                yield Button("Resolve dependencies", id="resolve-dependencies")
                yield Input(placeholder="EFI output directory", id="efi-output-input")
                yield Button("Build/validate EFI", id="build-efi")
            with Horizontal():
                yield Button("Discover exact Recovery", id="discover-recovery")
                yield Input(placeholder="Recovery binding JSON", id="recovery-binding-input")
                yield Input(placeholder="Recovery cache directory", id="recovery-destination-input")
                yield Input(placeholder="type exact large-download checkpoint", id="recovery-checkpoint-input")
                yield Button("Acquire Recovery", id="acquire-recovery")
            with Horizontal():
                yield Input(placeholder="Recovery lock JSON", id="recovery-lock-input")
                yield Input(placeholder="Recovery image", id="recovery-image-input")
                yield Input(placeholder="Recovery chunklist", id="recovery-chunklist-input")
                yield Button("Verify Recovery cache", id="verify-recovery")
                yield Button("USB adapter status", id="usb-status")
            yield Label("Non-destructive media plan")
            with Horizontal():
                yield Input(placeholder="stable device identity", id="media-device-id-input")
                yield Input(placeholder="model", id="media-model-input")
                yield Input(placeholder="capacity bytes", id="media-capacity-input")
                yield Input(placeholder="required bytes", id="media-required-input")
                yield Button("Preview media plan", id="media-plan")
        yield Footer()

    def on_mount(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        self._cancel_requested = False
        status = self.query_one("#workflow-status", Static)
        try:
            if self.config_id:
                self._draft = self.service.load(self.config_id)
                if self.fixture is None:
                    self._snapshot = None
                    status.update(
                        "Resume blocked: --fixture is required to validate the saved hardware snapshot. "
                        "No live hardware re-probe was performed."
                    )
                    self._show_stages(None)
                    return
                _, self._snapshot = self.service.create(self.fixture)
            else:
                self._draft, self._snapshot = self.service.create(self.fixture)
            self._show_state(self.service.evaluate(self._draft, self._snapshot))
        except Exception as exc:
            status.update(f"Workflow blocked: {type(exc).__name__}: {exc}")
            self._show_stages(None)

    def action_save(self) -> None:
        status = self.query_one("#workflow-status", Static)
        if self._draft is None:
            status.update("Nothing to save; press Refresh to load a draft.")
            return
        try:
            self.service.save_revision(self._draft)
            self._draft = self.service.load(self._draft.configuration_id)
            status.update(
                f"Saved configuration {self._draft.configuration_id} revision {self._draft.revision}. "
                "Private storage paths are intentionally not displayed."
            )
            self._show_stages(None)
        except Exception as exc:
            status.update(f"Save blocked: {type(exc).__name__}: {exc}")

    def action_cancel(self) -> None:
        self._cancel_requested = True
        for worker in self.workers:
            if worker.group == "workflow-stage":
                worker.cancel()
        self.query_one("#workflow-status", Static).update(
            "Cancelled. No destructive operation is available in this workflow; the draft remains unchanged."
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "apply-target": self._apply_target,
            "set-option": self._set_option,
            "acknowledge": self._acknowledge,
            "evaluate": self._evaluate,
            "save": self.action_save,
            "cancel": self.action_cancel,
            "import-config": self._import_config,
            "migrate-config": self._migrate_config,
            "export-config": self._export_config,
            "import-evidence": self._import_evidence,
            "resolve-dependencies": self._resolve_dependencies,
            "support-review": self._support_review,
            "build-efi": self._build_efi,
            "discover-recovery": self._discover_recovery,
            "acquire-recovery": self._acquire_recovery,
            "verify-recovery": self._verify_recovery,
            "usb-status": self._usb_status,
            "media-plan": self._media_plan,
        }
        action = actions.get(event.button.id or "")
        if action is not None:
            action()

    def _apply_target(self) -> None:
        if self._draft is None:
            self._message("Create or resume a configuration first.")
            return
        try:
            self._draft = self.service.set_target(
                self._draft,
                self.query_one("#version-input", Input).value.strip(),
                self.query_one("#build-input", Input).value.strip(),
            )
            self._evaluate()
        except Exception as exc:
            self._message(f"Target change blocked: {type(exc).__name__}: {exc}")

    def _set_option(self) -> None:
        if self._draft is None:
            self._message("Create or resume a configuration first.")
            return
        try:
            self._draft = self.service.set_option(
                self._draft,
                self.query_one("#option-id-input", Input).value.strip(),
                self.query_one("#option-value-input", Input).value.strip(),
            )
            self._evaluate()
        except Exception as exc:
            self._message(f"Option change blocked: {type(exc).__name__}: {exc}")

    def _acknowledge(self) -> None:
        if self._draft is None:
            self._message("Create or resume a configuration first.")
            return
        try:
            self._draft = self.service.acknowledge(
                self._draft,
                self.query_one("#ack-rule-input", Input).value.strip(),
                self.query_one("#ack-warning-input", Input).value,
            )
            self._evaluate()
        except Exception as exc:
            self._message(f"Acknowledgement blocked: {type(exc).__name__}: {exc}")

    def _import_config(self) -> None:
        self._import_config_file(migrate=False)

    def _migrate_config(self) -> None:
        self._import_config_file(migrate=True)

    def _import_config_file(self, migrate: bool) -> None:
        try:
            path = Path(self.query_one("#config-path-input", Input).value.strip())
            self._draft, issues = self.service.migrate_file(path) if migrate else self.service.import_as_new(path)
            self.service.save(self._draft)
            self.config_id = self._draft.configuration_id
            self._snapshot = None
            action = "Migrated" if migrate else "Imported"
            self._message(
                f"{action} configuration {self._draft.configuration_id} revision {self._draft.revision}; "
                f"issues requiring review: {len(issues)}. Source file: {path.name}"
            )
            self._show_stages(None)
        except Exception as exc:
            self._message(f"Configuration import blocked: {type(exc).__name__}: {exc}")

    def _export_config(self) -> None:
        if self._draft is None:
            self._message("Export requires a loaded configuration.")
            return
        try:
            path = Path(self.query_one("#config-path-input", Input).value.strip())
            self.service.export_file(self._draft, path)
            self._message(f"Exported redacted configuration {self._draft.configuration_id} to {path.name}")
        except Exception as exc:
            self._message(f"Configuration export blocked: {type(exc).__name__}: {exc}")

    def _import_evidence(self) -> None:
        if self._draft is None:
            self._message("Evidence import requires a loaded configuration.")
            return
        try:
            path = Path(self.query_one("#evidence-path-input", Input).value.strip())
            payload = json.loads(path.read_text(encoding="utf-8"))
            kind = self.query_one("#evidence-kind-input", Input).value.strip().lower()
            record = (
                UsbEvidenceSession.from_dict(payload).to_evidence_record()
                if kind == "usb"
                else AcpiEvidenceBundle.from_dict(payload).to_evidence_record()
            )
            self._draft = self.service.add_evidence(self._draft, record)
            self.service.save(self._draft)
            self._message(
                f"Imported sanitized {kind} evidence into configuration {self._draft.configuration_id}; "
                f"source file: {path.name}"
            )
            self._show_stages(None)
        except Exception as exc:
            self._message(f"Evidence import blocked: {type(exc).__name__}: {exc}")

    def _support_review(self) -> None:
        if self._draft is None or self._snapshot is None:
            self._message("Hardware support review requires an evaluated configuration and explicit fixture.")
            return
        try:
            report = self.service.support_review(self._draft, self._snapshot)
            self._message(f"Hardware support review: {report.overall_state.value}; report is evidence-bound and non-destructive.")
        except Exception as exc:
            self._message(f"Hardware support review blocked: {type(exc).__name__}: {exc}")

    def _resolve_dependencies(self) -> None:
        self._resolve_dependencies_worker()

    @work(thread=True, exclusive=True, group="workflow-stage")
    def _resolve_dependencies_worker(self) -> None:
        if self._draft is None or self._snapshot is None:
            self.call_from_thread(self._message, "Dependency resolution requires an evaluated configuration and explicit fixture.")
            return
        try:
            state, dependencies = self.service.resolve_dependencies(
                self._draft, self._snapshot, ArtifactVariant.RELEASE, cancel=lambda: self._cancel_requested
            )
            self.call_from_thread(self._message,
                f"Dependency resolution: {len(dependencies.resolved_dependencies)} resolved entries; "
                f"complete={dependencies.is_complete}; plan={state.evaluation.plan.canonical_digest()}"
            )
        except Exception as exc:
            self.call_from_thread(self._message, f"Dependency resolution blocked: {type(exc).__name__}: {exc}")

    def _build_efi(self) -> None:
        output = Path(self.query_one("#efi-output-input", Input).value.strip())
        self._build_efi_worker(output)

    @work(thread=True, exclusive=True, group="workflow-stage")
    def _build_efi_worker(self, output: Path) -> None:
        if self._draft is None or self._snapshot is None:
            self.call_from_thread(self._message, "EFI build requires an evaluated configuration and explicit fixture.")
            return
        try:
            result = self.service.build_efi_preview(
                self._draft, self._snapshot, output, offline=True, cancel=lambda: self._cancel_requested
            )
            validation = getattr(result, "validation", None)
            self.call_from_thread(self._message,
                f"EFI build/validation completed in {output.name}; "
                f"status={getattr(validation, 'status', 'unknown')}"
            )
        except Exception as exc:
            self.call_from_thread(self._message, f"EFI build/validation blocked: {type(exc).__name__}: {exc}")

    @work(thread=True, exclusive=True, group="workflow-stage")
    def _discover_recovery(self) -> None:
        try:
            self._recovery_result = self.service.discover_recovery(cancel=lambda: self._cancel_requested)
            diagnostics = "; ".join(self._recovery_result.diagnostics)
            self.call_from_thread(self._message, f"Recovery discovery: {self._recovery_result.state.value}. {diagnostics}")
        except Exception as exc:
            self.call_from_thread(self._message, f"Recovery discovery blocked: {type(exc).__name__}: {exc}")

    def _acquire_recovery(self) -> None:
        checkpoint = self.query_one("#recovery-checkpoint-input", Input).value.strip()
        binding_path = Path(self.query_one("#recovery-binding-input", Input).value.strip())
        destination = Path(self.query_one("#recovery-destination-input", Input).value.strip())
        if checkpoint != "I UNDERSTAND LARGE APPLE RECOVERY DOWNLOAD":
            self._message("Recovery acquisition blocked: type the exact explicit large-download checkpoint.")
            return
        self._acquire_recovery_worker(checkpoint, binding_path, destination)

    @work(thread=True, exclusive=True, group="workflow-stage")
    def _acquire_recovery_worker(self, checkpoint: str, binding_path: Path, destination: Path) -> None:
        if self._recovery_result is None or self._recovery_result.state != RecoveryState.DISCOVERED:
            self.call_from_thread(self._message, "Recovery acquisition blocked: discover the exact target first; no fallback is allowed.")
            return
        try:
            binding_data = json.loads(binding_path.read_text(encoding="utf-8"))
            binding = RecoveryBinding.from_dict(binding_data)
            lock, bundle = self.service.acquire_recovery(
                self._recovery_result,
                binding,
                destination,
                cancel=lambda: self._cancel_requested,
                resume=True,
            )
            self.call_from_thread(self._message,
                f"Recovery acquired state={lock.state.value}; image={bundle.image_path.name}; "
                "private cache paths are not displayed."
            )
        except Exception as exc:
            self.call_from_thread(self._message, f"Recovery acquisition blocked: {type(exc).__name__}: {exc}")

    def _verify_recovery(self) -> None:
        try:
            lock = Path(self.query_one("#recovery-lock-input", Input).value.strip())
            image = Path(self.query_one("#recovery-image-input", Input).value.strip())
            chunklist = Path(self.query_one("#recovery-chunklist-input", Input).value.strip())
            evidence = self.service.verify_recovery_cache(lock, image, chunklist)
            self._message(f"Recovery cache verified: {getattr(evidence, 'verified_chunks', 0)} signed chunks.")
        except Exception as exc:
            self._message(f"Recovery cache verification blocked: {type(exc).__name__}: {exc}")

    def _usb_status(self) -> None:
        status = self.service.removable_status()
        self._message(
            f"USB adapter status: {status['status']}; device discovery is not qualified; "
            f"writes_enabled={status['writes_enabled']}"
        )

    def _media_plan(self) -> None:
        try:
            device_id = self.query_one("#media-device-id-input", Input).value.strip()
            model = self.query_one("#media-model-input", Input).value.strip()
            capacity = int(self.query_one("#media-capacity-input", Input).value.strip())
            required = int(self.query_one("#media-required-input", Input).value.strip())
            plan = self.service.preview_media_plan(
                RemovableDevice(device_id, model, capacity, False, True, False), required
            )
            self._message(
                f"Non-destructive media plan created for {plan.target.model}; "
                "stable identity recheck is required and physical writes remain disabled."
            )
        except Exception as exc:
            self._message(f"Media plan blocked: {type(exc).__name__}: {exc}")

    def _evaluate(self) -> None:
        if self._cancel_requested:
            self._message("Cancelled. Press Refresh to begin a new review action.")
            return
        if self._draft is None or self._snapshot is None:
            self._message("Evaluation requires an explicit hardware fixture or a new safe probe.")
            return
        self._show_state(self.service.evaluate(self._draft, self._snapshot))

    def _show_state(self, state: Optional[WorkflowState]) -> None:
        if state is None:
            self._show_stages(None)
            return
        status = self.query_one("#workflow-status", Static)
        lines = [
            "Configuration review",
            f"configuration: {state.configuration.configuration_id} revision {state.configuration.revision}",
            f"semantic digest: {state.configuration.semantic_digest}",
            f"target: {state.configuration.target.to_dict() if state.configuration.target else 'exact target required'}",
            f"issues: {len(state.evaluation.issues)}",
        ]
        lines.extend(f"{issue.code}: {issue.explanation}" for issue in state.evaluation.issues)
        status.update("\n".join(lines))
        self._show_stages(state)

    def _show_stages(self, state: Optional[WorkflowState]) -> None:
        stages = [
            "Shared workflow stages",
            "configuration/evidence review: "
            + (f"loaded ({len(self._draft.evidence)} evidence records)" if self._draft else "not loaded"),
            "hardware support review: Review hardware support invokes the shared compatibility engine",
            "exact target/profile/options: editable above through WorkflowService",
            "dependency resolution: Resolve dependencies invokes the shared resolver",
            "EFI build/validation: Build/validate EFI writes only the selected output directory",
            "Recovery discovery/acquisition: exact target, binding, checkpoint and resumable cache required",
            "Recovery cache verification and USB adapter status are read-only checks",
            "media plan: Preview media plan is non-destructive; physical writes are disabled",
            "cancellation: safe draft cancellation is available; no disk write is exposed",
        ]
        if state is not None:
            stages.append(f"plan digest: {state.evaluation.plan.canonical_digest()}")
        self.query_one("#workflow-stages", Static).update("\n".join(stages))

    def _message(self, message: str) -> None:
        self.query_one("#workflow-status", Static).update(message)


def run_tui(fixture: Optional[Path] = None, config_id: Optional[str] = None) -> None:
    WorkflowApp(fixture=fixture, config_id=config_id).run()
