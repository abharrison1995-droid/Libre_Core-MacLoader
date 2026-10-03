"""Resumable semantic engine; UI clients never choose workflow prerequisites."""
from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Callable, Optional

from macloader.autoloader.models import ActionKind, AutoloaderSession, NextAction, Stage
from macloader.autoloader.store import AutoloaderSessionStore
from macloader.config import DEFAULT_PRIVATE_DIR
from macloader.configuration.campaign_match import CampaignMatch, match_campaign
from macloader.configuration.observations import reconcile_configuration
from macloader.configuration.store import ConfigurationStore
from macloader.detection.sanitize import sanitize_hardware_snapshot
from macloader.domain.configuration import UserConfiguration
from macloader.domain.evidence import EvidenceCompleteness
from macloader.domain.hardware import HardwareSnapshot
from macloader.workflow.service import WorkflowService


class AutoloaderService:
    def __init__(self, workflow: Optional[WorkflowService] = None, root: Optional[Path] = None):
        self.root = Path(root or (DEFAULT_PRIVATE_DIR / "campaigns")).absolute()
        self.store = AutoloaderSessionStore(self.root)
        self.workflow = workflow or WorkflowService(store=ConfigurationStore(self.root / "configurations"))
        self.session: Optional[AutoloaderSession] = None
        self.snapshot: Optional[HardwareSnapshot] = None
        self.configuration: Optional[UserConfiguration] = None
        self.match: Optional[CampaignMatch] = None
        self.handlers: dict[Stage, Callable[[], None]] = {}
        self._blocker: Optional[NextAction] = None

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
            self.store.save_snapshot(session, safe.to_json())
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
            self.store.save_snapshot(session, safe.to_json())
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
        if self.match.mismatches:
            return NextAction(Stage.HARDWARE, ActionKind.BLOCKED, "Detected hardware differs from the reviewed campaign. Your session is preserved; resolve the hardware mismatch in diagnostics.", "HARDWARE_MISMATCH")
        if self.match.unknown:
            return NextAction(Stage.HARDWARE, ActionKind.BLOCKED, "Some required hardware facts could not be proven. MacLoader has preserved the session and needs focused evidence collection.", "HARDWARE_UNKNOWN")
        for kind, stage in (("acpi", Stage.ACPI), ("usb", Stage.USB)):
            records = [record for record in self.configuration.evidence if record.kind == kind]
            if not any(record.completeness == EvidenceCompleteness.COMPLETE for record in records):
                return NextAction(stage, ActionKind.AUTOMATIC if stage in self.handlers else ActionKind.BLOCKED,
                                  "Collect firmware tables." if kind == "acpi" else "Identify this laptop’s physical USB ports by moving the test device when prompted.",
                                  "ACPI_CAPTURE_REQUIRED" if kind == "acpi" else "USB_PHYSICAL_EVIDENCE_REQUIRED")
        for stage, artifact in ((Stage.TOOLS, "tools"), (Stage.DEPENDENCIES, "dependencies")):
            if artifact not in self.session.artifacts:
                return NextAction(stage, ActionKind.AUTOMATIC if stage in self.handlers else ActionKind.BLOCKED, "Prepare verified tools and dependencies.", "SOFTWARE_PREPARATION_REQUIRED")
        if self.configuration.identity_ref is None:
            return NextAction(Stage.IDENTITY, ActionKind.HUMAN, "Choose a private identity for this installation.", choices=("Generate new", "Reuse existing"))
        evaluation = self.workflow.evaluate(self.configuration, self.snapshot).evaluation
        if any(issue.code == "ACKNOWLEDGEMENT_REQUIRED" for issue in evaluation.issues):
            return NextAction(Stage.ACCEPTANCE, ActionKind.HUMAN, "Review and accept the experimental T480s prototype configuration.", choices=("Accept prototype",))
        if evaluation.has_blockers:
            return NextAction(Stage.BUILD, ActionKind.BLOCKED, "Configuration validation needs attention. No destructive operation occurred; your evidence and private identity are preserved.", "CONFIGURATION_BLOCKED")
        if "efi" not in self.session.artifacts:
            return NextAction(Stage.BUILD, ActionKind.AUTOMATIC if Stage.BUILD in self.handlers else ActionKind.BLOCKED, "Build and validate the EFI.", "EFI_BUILD_REQUIRED")
        return NextAction(Stage.RECOVERY_MODE, ActionKind.BLOCKED, "Resolve Recovery for the frozen target. Qualification requires proof of the exact build; no installation is authorized.", "RECOVERY_REQUIRED")

    def advance_until_blocked(self, cancel: Optional[Callable[[], bool]] = None) -> NextAction:
        """Run only registered safe operations; recompute prerequisites after each."""
        for _ in range(32):
            action = self.next_action()
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
            except Exception:
                record["state"] = "failed"
                record["code"] = "AUTOMATIC_ACTION_FAILED"
                self._save_session()
                self._blocker = NextAction(action.stage, ActionKind.BLOCKED, "Preparation could not complete this step. No destructive operation occurred; saved inputs are preserved. Open diagnostics or retry.", "AUTOMATIC_ACTION_FAILED")
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

    def _save_session(self) -> None:
        if self.session is None:
            raise ValueError("No active guided session")
        previous = self.session.revision
        self.session.revision += 1
        self.store.save(self.session, previous)

    def save_configuration(self, configuration: UserConfiguration) -> None:
        self.workflow.save_revision(configuration)
        self.configuration = self.workflow.load(configuration.configuration_id)

    def public_status(self) -> dict[str, object]:
        return {"machine": "Lenovo ThinkPad T480s" if self.session else "Unmatched machine",
                "experimental": True, "action": self.next_action().to_dict(),
                "destructive_operation_performed": False}
