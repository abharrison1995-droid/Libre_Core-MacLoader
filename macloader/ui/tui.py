"""Keyboard-accessible Textual presentation over the shared workflow service."""

from pathlib import Path
import json
import asyncio
from typing import Callable, Optional

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Label, Static

from macloader.domain.configuration import UserConfiguration
from macloader.domain.hardware import HardwareSnapshot
from macloader.domain.dependencies import ArtifactVariant
from macloader.domain.contracts import BuildManifest
from macloader.domain.recovery import RecoveryBinding, RecoveryLock, RecoveryState
from macloader.evidence.acpi import AcpiEvidenceBundle
from macloader.evidence.usb import UsbEvidenceSession
from macloader.removable.writer import RemovableDevice
from macloader.workflow.service import WorkflowService, WorkflowState
from macloader.recovery.discovery import RecoveryDiscoveryResult
from macloader.recovery.acquirer import RecoveryBundle


class WorkflowApp(App[None]):
    """Non-destructive workflow client; CLI and TUI use identical service calls."""

    TITLE = "Libre_Core MacLoader"
    DEFAULT_CSS = """
    # Rows are explicitly sized so Textual does not allocate one full-width
    # child per control and place the primary action outside the viewport.
    .control-row { width: 100%; height: 3; min-height: 3; }
    .control-row Input { width: 1fr; min-width: 0; }
    .control-row Button { width: 16; min-width: 12; }
    .control-note { width: 1fr; min-width: 18; color: $text-muted; }
    #primary-actions Button { width: 1fr; min-width: 12; }
    #workflow-status { max-height: 6; overflow-y: auto; }
    #workflow-stages { max-height: 4; overflow-y: auto; }
    """
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
        self._operation_generation = 0
        self._recovery_result: Optional[RecoveryDiscoveryResult] = None
        self._efi_manifest: Optional[BuildManifest] = None
        self._efi_output: Optional[Path] = None
        self._last_state: Optional[WorkflowState] = None
        self._latest_message = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="workflow-content"):
            yield Static("Loading shared workflow…", id="workflow-status")
            yield Static("", id="workflow-stages")
            yield Label("Exact Recovery target")
            with Horizontal(classes="control-row"):
                yield Input("15.0", placeholder="version", id="version-input")
                yield Input("24A335", placeholder="build", id="build-input")
                yield Button("Apply target", id="apply-target", variant="primary")
            yield Label("Policy option")
            with Horizontal(classes="control-row"):
                yield Input(placeholder="option.id", id="option-id-input")
                yield Input(placeholder="value", id="option-value-input")
                yield Button("Set option", id="set-option")
            yield Label("Experimental acknowledgement")
            with Horizontal(classes="control-row"):
                yield Input(placeholder="rule or option id", id="ack-rule-input")
                yield Input(placeholder="exact warning text", id="ack-warning-input")
                yield Button("Acknowledge", id="acknowledge")
            with Horizontal(id="primary-actions", classes="control-row"):
                yield Button("Evaluate", id="evaluate", variant="primary")
                yield Button("Save revision", id="save")
                yield Button("Cancel", id="cancel")
            yield Label("Configuration import/export and evidence review")
            with Horizontal(classes="control-row"):
                yield Input(placeholder="JSON file path", id="config-path-input")
                yield Button("Import", id="import-config")
                yield Button("Migrate", id="migrate-config")
                yield Button("Export redacted", id="export-config")
            with Horizontal(classes="control-row"):
                yield Input(placeholder="evidence JSON path", id="evidence-path-input")
                yield Input(placeholder="usb or acpi", id="evidence-kind-input")
                yield Button("Import evidence", id="import-evidence")
            with Horizontal(classes="control-row"):
                yield Input(placeholder="private identity filename to reuse", id="identity-ref-input")
                yield Button("Use stored identity", id="set-identity")
            yield Label("Plan, dependency, EFI and Recovery stages")
            with Horizontal(classes="control-row"):
                yield Button("Review hardware support", id="support-review")
                yield Button("Resolve dependencies", id="resolve-dependencies")
                yield Input(placeholder="EFI output directory", id="efi-output-input")
                yield Button("Build/validate EFI", id="build-efi")
            with Horizontal(classes="control-row"):
                yield Button("Discover exact Recovery", id="discover-recovery")
                yield Static("Binding derives from the current verified EFI", classes="control-note")
                yield Input(placeholder="Recovery cache directory", id="recovery-destination-input")
                yield Input(placeholder="type exact large-download checkpoint", id="recovery-checkpoint-input")
                yield Button("Acquire Recovery", id="acquire-recovery")
            with Horizontal(classes="control-row"):
                yield Input(placeholder="Recovery lock JSON", id="recovery-lock-input")
                yield Input(placeholder="Recovery image", id="recovery-image-input")
                yield Input(placeholder="Recovery chunklist", id="recovery-chunklist-input")
                yield Button("Verify Recovery cache", id="verify-recovery")
                yield Button("USB adapter status", id="usb-status")
            yield Label("Non-destructive media plan")
            with Horizontal(classes="control-row"):
                yield Input(placeholder="stable device identity", id="media-device-id-input")
                yield Input(placeholder="model", id="media-model-input")
                yield Input(placeholder="capacity bytes", id="media-capacity-input")
                yield Input(placeholder="required bytes", id="media-required-input")
                yield Button("Preview media plan", id="media-plan")
        yield Footer()

    def on_mount(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        self._cancel_workflow_workers()
        self._cancel_requested = False
        self._invalidate_efi()
        status = self.query_one("#workflow-status", Static)
        try:
            if self.config_id and self._draft is None:
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
            elif self._draft is not None and self.fixture is not None:
                # Refresh hardware observations without abandoning an active
                # draft.  Starting a new draft is an explicit user action.
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
        self._cancel_workflow_workers()
        self._cancel_requested = False
        try:
            self.service.save_revision(self._draft)
            self._draft = self.service.load(self._draft.configuration_id)
            self.config_id = self._draft.configuration_id
            status.update(
                f"Saved configuration {self._draft.configuration_id} revision {self._draft.revision}. "
                "Private storage paths are intentionally not displayed."
            )
            self._show_stages(None)
        except Exception as exc:
            status.update(f"Save blocked: {type(exc).__name__}: {exc}")

    def action_cancel(self) -> None:
        self._cancel_workflow_workers()
        self.query_one("#workflow-status", Static).update(
            "Cancelled. No destructive operation is available in this workflow; the draft remains unchanged."
        )

    async def on_unmount(self) -> None:
        """Invalidate and bounded-wait for workers before teardown."""
        self._cancel_workflow_workers()
        active = [worker for worker in self.workers if worker.group == "workflow-stage"]
        if active:
            await asyncio.gather(
                *(asyncio.wait_for(worker.wait(), timeout=1.0) for worker in active),
                return_exceptions=True,
            )

    def _cancel_workflow_workers(self) -> None:
        self._cancel_requested = True
        self._operation_generation += 1
        for worker in list(self.workers):
            if worker.group == "workflow-stage":
                worker.cancel()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions: dict[str, Callable[[], None]] = {
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
            "set-identity": self._set_identity,
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
            self._invalidate_efi()
            self._operation_generation += 1
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
            self._invalidate_efi()
            self._operation_generation += 1
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
            self._invalidate_efi()
            self._operation_generation += 1
            self._evaluate()
        except Exception as exc:
            self._message(f"Acknowledgement blocked: {type(exc).__name__}: {exc}")

    def _import_config(self) -> None:
        self._import_config_file(migrate=False)

    def _migrate_config(self) -> None:
        self._import_config_file(migrate=True)

    def _import_config_file(self, migrate: bool) -> None:
        self._cancel_workflow_workers()
        self._cancel_requested = False
        try:
            path = Path(self.query_one("#config-path-input", Input).value.strip())
            self._draft, issues = self.service.migrate_file(path) if migrate else self.service.import_as_new(path)
            self.service.save(self._draft)
            self._draft = self.service.load(self._draft.configuration_id)
            self.config_id = self._draft.configuration_id
            self._snapshot = None
            self._invalidate_efi()
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
        self._cancel_workflow_workers()
        self._cancel_requested = False
        try:
            path = Path(self.query_one("#evidence-path-input", Input).value.strip())
            payload = json.loads(path.read_text(encoding="utf-8"))
            kind = self.query_one("#evidence-kind-input", Input).value.strip().lower()
            if kind == "usb":
                record = UsbEvidenceSession.from_dict(payload).to_evidence_record()
            elif kind == "acpi":
                record = AcpiEvidenceBundle.from_dict(payload).to_evidence_record()
            else:
                raise ValueError("Evidence kind must be exactly usb or acpi")
            self._draft = self.service.add_evidence(self._draft, record)
            self._invalidate_efi()
            self.service.save(self._draft)
            self._draft = self.service.load(self._draft.configuration_id)
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

    def _set_identity(self) -> None:
        if self._draft is None:
            self._message("Identity reuse requires a loaded configuration.")
            return
        try:
            self._draft = self.service.set_identity_reference(
                self._draft, self.query_one("#identity-ref-input", Input).value.strip()
            )
            self._invalidate_efi()
            self._operation_generation += 1
            self._evaluate()
        except Exception as exc:
            self._message(f"Identity reuse blocked: {type(exc).__name__}: {exc}")

    def _resolve_dependencies(self) -> None:
        if self._draft is None or self._snapshot is None:
            self._message("Dependency resolution requires an evaluated configuration and explicit fixture.")
            return
        self._cancel_requested = False
        self._operation_generation += 1
        self._resolve_dependencies_worker(self._draft, self._snapshot, self._operation_generation)

    @work(thread=True, exclusive=True, group="workflow-stage")
    def _resolve_dependencies_worker(self, draft: UserConfiguration, snapshot: HardwareSnapshot, generation: int) -> None:
        try:
            state, dependencies = self.service.resolve_dependencies(
                draft, snapshot, ArtifactVariant.RELEASE, cancel=lambda: self._cancel_requested or generation != self._operation_generation
            )
            self.call_from_thread(self._publish_message_if_current, generation,
                f"Dependency resolution: {len(dependencies.resolved_dependencies)} resolved entries; "
                f"complete={dependencies.is_complete}; plan={state.evaluation.plan.canonical_digest()}"
            )
        except Exception as exc:
            self.call_from_thread(
                self._publish_message_if_current,
                generation,
                f"Dependency resolution blocked: {type(exc).__name__}: {exc}",
            )

    def _build_efi(self) -> None:
        output = Path(self.query_one("#efi-output-input", Input).value.strip())
        if self._draft is None or self._snapshot is None:
            self._message("EFI build requires an evaluated configuration and explicit fixture.")
            return
        self._cancel_requested = False
        self._invalidate_efi()
        self._operation_generation += 1
        self._build_efi_worker(output, self._draft, self._snapshot, self._operation_generation)

    @work(thread=True, exclusive=True, group="workflow-stage")
    def _build_efi_worker(self, output: Path, draft: UserConfiguration, snapshot: HardwareSnapshot, generation: int) -> None:
        try:
            result = self.service.build_efi_preview(
                draft, snapshot, output, offline=True,
                cancel=lambda: self._cancel_requested or generation != self._operation_generation,
            )
            self.call_from_thread(self._publish_efi_result, generation, result, output)
        except Exception as exc:
            self.call_from_thread(
                self._publish_message_if_current,
                generation,
                f"EFI build/validation blocked: {type(exc).__name__}: {exc}",
            )

    @work(thread=True, exclusive=True, group="workflow-stage")
    def _discover_recovery(self) -> None:
        self._cancel_requested = False
        self._operation_generation += 1
        generation = self._operation_generation
        try:
            result = self.service.discover_recovery(
                cancel=lambda: self._cancel_requested or generation != self._operation_generation
            )
            self.call_from_thread(self._publish_recovery_discovery, generation, result)
        except Exception as exc:
            self.call_from_thread(
                self._publish_message_if_current,
                generation,
                f"Recovery discovery blocked: {type(exc).__name__}: {exc}",
            )

    def _acquire_recovery(self) -> None:
        checkpoint = self.query_one("#recovery-checkpoint-input", Input).value.strip()
        destination = Path(self.query_one("#recovery-destination-input", Input).value.strip())
        if checkpoint != "I UNDERSTAND LARGE APPLE RECOVERY DOWNLOAD":
            self._message("Recovery acquisition blocked: type the exact explicit large-download checkpoint.")
            return
        if self._efi_manifest is None or self._efi_output is None:
            self._message(
                "Recovery acquisition blocked: complete the current qualified EFI build first; "
                "manual binding JSON is not accepted."
            )
            return
        if self._draft is None or self._snapshot is None:
            self._message("Recovery acquisition blocked: evaluate the current configuration first.")
            return
        self._cancel_requested = False
        self._operation_generation += 1
        self._acquire_recovery_worker(
            checkpoint, destination, self._draft, self._snapshot, self._efi_manifest,
            self._efi_output, self._recovery_result, self._operation_generation
        )

    @work(thread=True, exclusive=True, group="workflow-stage")
    def _acquire_recovery_worker(
        self,
        checkpoint: str,
        destination: Path,
        draft: UserConfiguration,
        snapshot: HardwareSnapshot,
        manifest: BuildManifest,
        efi_output: Path,
        recovery_result: Optional[RecoveryDiscoveryResult],
        generation: int,
    ) -> None:
        if recovery_result is None or recovery_result.state != RecoveryState.DISCOVERED:
            self.call_from_thread(
                self._publish_message_if_current,
                generation,
                "Recovery acquisition blocked: discover the exact target first; no fallback is allowed.",
            )
            return
        try:
            binding = self.service.derive_recovery_binding(
                draft, snapshot, None, manifest, efi_output=efi_output
            )
            verified_artifacts = {
                "configuration_digest": binding.configuration_digest,
                "build_plan_digest": binding.build_plan_digest,
                "catalog_digest": binding.catalog_digest,
                "toolchain_digest": binding.toolchain_digest,
                "efi_manifest_digest": binding.efi_manifest_digest,
            }
            lock, bundle = self.service.acquire_recovery(
                recovery_result,
                binding,
                destination,
                cancel=lambda: self._cancel_requested or generation != self._operation_generation,
                resume=True,
                verified_artifacts=verified_artifacts,
                require_verified=True,
            )
            self.call_from_thread(
                self._publish_recovery_result,
                generation,
                lock,
                bundle,
                destination,
            )
        except Exception as exc:
            self.call_from_thread(
                self._publish_message_if_current,
                generation,
                f"Recovery acquisition blocked: {type(exc).__name__}: {exc}",
            )

    def _verify_recovery(self) -> None:
        lock = Path(self.query_one("#recovery-lock-input", Input).value.strip())
        image = Path(self.query_one("#recovery-image-input", Input).value.strip())
        chunklist = Path(self.query_one("#recovery-chunklist-input", Input).value.strip())
        self._cancel_requested = False
        self._operation_generation += 1
        self._verify_recovery_worker(lock, image, chunklist, self._operation_generation)

    @work(thread=True, exclusive=True, group="workflow-stage")
    def _verify_recovery_worker(
        self, lock: Path, image: Path, chunklist: Path, generation: int
    ) -> None:
        try:
            evidence = self.service.verify_recovery_cache(
                lock,
                image,
                chunklist,
                cancel=lambda: self._cancel_requested or generation != self._operation_generation,
            )
            self.call_from_thread(
                self._publish_message_if_current,
                generation,
                f"Recovery cache verified: {getattr(evidence, 'verified_chunks', 0)} signed chunks.",
            )
        except Exception as exc:
            self.call_from_thread(
                self._publish_message_if_current,
                generation,
                f"Recovery cache verification blocked: {type(exc).__name__}: {exc}",
            )

    def _usb_status(self) -> None:
        try:
            status = self.service.removable_status()
            self._message(
                f"USB adapter status: {status['status']}; device discovery is not qualified; "
                f"writes_enabled={status['writes_enabled']}"
            )
        except Exception as exc:
            self._message(f"USB discovery blocked: {type(exc).__name__}: retry discovery or inspect the adapter.")

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
            # Cancellation belongs to the previous operation, not to the
            # draft.  A retry starts a fresh generation without discarding it.
            self._cancel_requested = False
            self._operation_generation += 1
        if self._draft is None or self._snapshot is None:
            self._message("Evaluation requires an explicit hardware fixture or a new safe probe.")
            return
        self._show_state(self.service.evaluate(self._draft, self._snapshot))

    def _show_state(self, state: Optional[WorkflowState]) -> None:
        if state is None:
            self._show_stages(None)
            return
        self._last_state = state
        self._latest_message = ""
        self._render_status()

    def _render_status(self) -> None:
        status = self.query_one("#workflow-status", Static)
        state = self._last_state
        if state is None:
            status.update(self._latest_message or "No configuration review has been run yet.")
            return
        lines = [
            "Configuration review",
            f"configuration: {state.configuration.configuration_id} revision {state.configuration.revision}",
            f"semantic digest: {state.configuration.semantic_digest}",
            f"target: {state.configuration.target.to_dict() if state.configuration.target else 'exact target required'}",
            f"issues: {len(state.evaluation.issues)}",
        ]
        lines.extend(f"{issue.code}: {issue.explanation}" for issue in state.evaluation.issues)
        if self._latest_message:
            lines.extend(("", f"Latest action: {self._latest_message}"))
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
        self._latest_message = message
        self._render_status()

    def _invalidate_efi(self) -> None:
        """A build is valid only for the exact current draft and snapshot."""
        self._efi_manifest = None
        self._efi_output = None

    def _publish_message_if_current(self, generation: int, message: str) -> None:
        """Publish a worker message only on the UI thread and current generation."""
        if generation == self._operation_generation:
            self._message(message)

    def _publish_efi_result(self, generation: int, result: object, output: Path) -> None:
        """Commit an EFI result atomically with the generation check."""
        if generation != self._operation_generation:
            return
        self._efi_manifest = getattr(result, "manifest", None)
        self._efi_output = output
        validation = getattr(result, "validation", None)
        self._message(
            f"EFI build/validation completed in {output.name}; "
            f"status={getattr(validation, 'status', 'unknown')}"
        )

    def _publish_recovery_discovery(
        self, generation: int, result: RecoveryDiscoveryResult
    ) -> None:
        if generation != self._operation_generation:
            return
        self._recovery_result = result
        diagnostics = "; ".join(result.diagnostics)
        self._message(f"Recovery discovery: {result.state.value}. {diagnostics}")

    def _publish_recovery_result(
        self,
        generation: int,
        lock: RecoveryLock,
        bundle: RecoveryBundle,
        destination: Path,
    ) -> None:
        if generation != self._operation_generation:
            return
        self.service.persist_recovery_result(lock, bundle.evidence, destination)
        self._message(
            f"Recovery acquired state={lock.state.value}; image={bundle.image_path.name}; "
            "private cache paths are not displayed."
        )


def run_tui(fixture: Optional[Path] = None, config_id: Optional[str] = None) -> None:
    WorkflowApp(fixture=fixture, config_id=config_id).run()
