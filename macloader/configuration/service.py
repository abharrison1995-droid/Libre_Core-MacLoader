"""Shared configuration evaluation used by CLI and future Textual views."""

from dataclasses import dataclass, replace
from typing import List, Optional, Tuple

from macloader.compatibility.engine import CompatibilityEngine
from macloader.configuration.policy import ConfigurationPolicy, load_configuration_policy
from macloader.database.loader import Database, get_database
from macloader.domain.build_plan import BuildPlan
from macloader.domain.compatibility import CompatibilityReport, CompatibilityState
from macloader.domain.configuration import (
    AcceptedConfiguration, Acknowledgement, BlockingStage, ConfigurationIssue,
    IssueSeverity, UserConfiguration,
)
from macloader.domain.contracts import canonical_json_digest
from macloader.domain.evidence import EvidenceCompleteness
from macloader.domain.hardware import HardwareSnapshot


@dataclass(frozen=True)
class ConfigurationEvaluation:
    configuration: UserConfiguration
    issues: Tuple[ConfigurationIssue, ...]
    plan: BuildPlan
    report: CompatibilityReport
    accepted: Optional[AcceptedConfiguration] = None

    @property
    def has_blockers(self) -> bool:
        return any(issue.blocking for issue in self.issues)


class ConfigurationService:
    """Reconcile a draft against current observations and trusted policy."""

    def __init__(self, db: Optional[Database] = None, policy: Optional[ConfigurationPolicy] = None):
        self.db = db or get_database()
        self.policy = policy or load_configuration_policy()
        self.engine = CompatibilityEngine(db=self.db)

    def new_draft(self, snapshot: HardwareSnapshot) -> UserConfiguration:
        return UserConfiguration(
            hardware_snapshot_id=snapshot.snapshot_id,
            hardware_snapshot_digest=self._snapshot_digest(snapshot),
            option_selections=self.policy.default_selections(),
            policy_version=self.policy.policy_version,
        )

    def evaluate(self, draft: UserConfiguration, snapshot: HardwareSnapshot) -> ConfigurationEvaluation:
        issues: List[ConfigurationIssue] = []
        if draft.policy_version and draft.policy_version != self.policy.policy_version:
            issues.append(self._issue("POLICY_STALE", "policy_version", "Saved selections were created under a different policy bundle.", "Review changed selections and save a new revision."))
        if draft.hardware_snapshot_id and draft.hardware_snapshot_id != snapshot.snapshot_id:
            issues.append(self._issue("SNAPSHOT_MISMATCH", "hardware_snapshot_id", "The draft is bound to a different hardware snapshot.", "Probe or import the matching sanitized snapshot."))
        if draft.hardware_snapshot_digest and draft.hardware_snapshot_digest != self._snapshot_digest(snapshot):
            issues.append(self._issue("SNAPSHOT_STALE", "hardware_snapshot_digest", "The bound hardware content digest no longer matches.", "Reconcile observations before continuing."))

        target = draft.target
        if target is None:
            issues.append(self._issue("EXACT_TARGET_REQUIRED", "target", "A product-only macOS choice is not reproducible.", "Select an exact version and build from the trusted release catalog."))
            compatibility_product = "sequoia"
        else:
            release = self.policy.get_release(target.product_id, target.version, target.build)
            if release is None or release.release_record_digest != target.release_record_digest:
                issues.append(self._issue("TARGET_NOT_IN_POLICY", "target", "The exact version/build is absent or stale in the current release catalog.", "Select an exact current catalog record."))
            compatibility_product = target.product_id

        model = self.db.get_model_by_machine_type(snapshot.machine_type or "") if snapshot.machine_type else None
        if model is None or model.id != self.policy.model_id:
            issues.append(self._issue("MODEL_NOT_QUALIFIED", "hardware.model", "The observed machine is not the exact T480s model covered by this policy.", "Resolve the model identity before accepting a T480s configuration."))

        try:
            report = self.engine.evaluate(snapshot, target_macos=compatibility_product)
        except Exception as exc:
            # Keep the presentation contract structured even when imported target data is malformed.
            issues.append(self._issue("COMPATIBILITY_EVALUATION_FAILED", "target.product_id", str(exc), "Correct the target or hardware evidence."))
            report = self.engine.evaluate(snapshot, target_macos="sequoia")
        plan = self.engine.generate_build_plan(report)

        self._validate_options(draft, target, issues)
        self._validate_evidence(draft, snapshot, issues)
        for requirement in plan.unresolved_requirements:
            issues.append(self._issue("UNRESOLVED_REQUIREMENT", "build_plan.unresolved_requirements", requirement, "Resolve the requirement or keep the build blocked."))
        if report.overall_state in (CompatibilityState.UNKNOWN, CompatibilityState.BLOCKED):
            issues.append(self._issue("COMPATIBILITY_BLOCKED", "hardware", "Compatibility policy does not permit an actionable plan.", "Resolve unknown or blocked hardware evidence."))
        elif report.overall_state == CompatibilityState.EXPERIMENTAL:
            issues.append(ConfigurationIssue(
                code="PHYSICAL_ACCEPTANCE_PENDING", field_path="acceptance", severity=IssueSeverity.WARNING,
                blocking_stage=BlockingStage.ACCEPTANCE, rule_id="acceptance.physical_required",
                explanation="Fixture/software evaluation does not establish physical T480s acceptance.",
                remediation="Run the exact hardware acceptance suite before claiming SUPPORTED.",
            ))

        blocking = [item for item in issues if item.blocking]
        unresolved = list(plan.unresolved_requirements)
        unresolved.extend(item.explanation for item in blocking if item.explanation not in unresolved)
        plan = replace(
            plan,
            target_version=target.version if target else "",
            target_build=target.build if target else "",
            target_release_digest=target.release_record_digest if target else "",
            stable_model_id=model.id if model else "",
            hardware_content_digest=self._snapshot_digest(snapshot),
            evidence_digests=[record.digest for record in draft.evidence],
            profile_bindings=list(self.policy.profiles),
            unresolved_requirements=unresolved,
            accepted_configuration_digest="" if blocking else draft.semantic_digest,
        )
        accepted = None if blocking else AcceptedConfiguration(draft, draft.semantic_digest)
        return ConfigurationEvaluation(draft, tuple(issues), plan, report, accepted)

    def accept(self, evaluation: ConfigurationEvaluation) -> AcceptedConfiguration:
        if evaluation.has_blockers or evaluation.accepted is None:
            raise ValueError("configuration has blocking issues and cannot be accepted")
        return evaluation.accepted

    def acknowledge(self, draft: UserConfiguration, rule_id: str, warning_text: str) -> UserConfiguration:
        warning_digest = canonical_json_digest({"rule_id": rule_id, "warning": warning_text})
        binding_digest = self._acknowledgement_binding_digest(draft)
        ack = Acknowledgement(rule_id, warning_digest, binding_digest)
        return replace(draft, acknowledgements=tuple((*draft.acknowledgements, ack)))

    def _validate_options(self, draft: UserConfiguration, target: Optional[object], issues: List[ConfigurationIssue]) -> None:
        selected = draft.selected_options()
        for option_id, value in selected.items():
            option = self.policy.options.get(option_id)
            if option is None:
                issues.append(self._issue("OPTION_UNKNOWN", f"options.{option_id}", "The option is not defined by the current policy bundle.", "Remove it and select a versioned policy option."))
                continue
            invalid = option.validate_value(value)
            if invalid:
                issues.append(self._issue("OPTION_VALUE_INVALID", f"options.{option_id}", invalid, "Choose one of the policy-listed values."))
            if option.applies_to_products and target is not None and getattr(target, "product_id", "") not in option.applies_to_products:
                issues.append(self._issue("OPTION_NOT_APPLICABLE", f"options.{option_id}", "The option is not applicable to this exact macOS product.", "Choose an applicable reviewed preset."))
            if option.requires_acknowledgement:
                binding = self._acknowledgement_binding_digest(draft)
                warning_digest = canonical_json_digest({"rule_id": option_id, "warning": option.explanation})
                if not any(
                    item.rule_id == option_id
                    and item.warning_digest == warning_digest
                    and item.configuration_digest == binding
                    for item in draft.acknowledgements
                ):
                    issues.append(self._issue("ACKNOWLEDGEMENT_REQUIRED", f"options.{option_id}", "This experimental option requires a specific acknowledgement bound to the current selections.", "Acknowledge the exact warning after reviewing the configuration."))
        for option in self.policy.options.values():
            if option.default_value is not None and option.option_id not in selected:
                issues.append(self._issue("OPTION_REQUIRED", f"options.{option.option_id}", "A required policy option has no selection.", "Use a reviewed policy value."))
        for option_id, value in selected.items():
            option = self.policy.options.get(option_id)
            if option and any(conflict in selected for conflict in option.conflicts):
                issues.append(self._issue("OPTION_CONFLICT", f"options.{option_id}", f"Selection '{value}' conflicts with another policy option.", "Remove the conflicting selection."))

    def _validate_evidence(self, draft: UserConfiguration, snapshot: HardwareSnapshot, issues: List[ConfigurationIssue]) -> None:
        usb_records = [record for record in draft.evidence if record.kind == "usb"]
        if not any(
            record.completeness == EvidenceCompleteness.COMPLETE
            and record.physical_port_evidence
            and record.capture_method == "manual-physical-port-session"
            for record in usb_records
        ):
            issues.append(self._issue("USB_PHYSICAL_EVIDENCE_REQUIRED", "evidence.usb", "A USB map cannot be complete without physical port evidence.", "Capture and verify every applicable physical port before accepting the build."))
        if not snapshot.bios_version:
            issues.append(self._issue("BIOS_EVIDENCE_REQUIRED", "hardware.bios_version", "BIOS identity is missing from the observed hardware snapshot.", "Capture the BIOS version and bind it to evidence."))

    @staticmethod
    def _snapshot_digest(snapshot: HardwareSnapshot) -> str:
        data = snapshot.to_dict()
        data.pop("snapshot_id", None)
        data.pop("timestamp", None)
        return canonical_json_digest(data)

    @staticmethod
    def _acknowledgement_binding_digest(draft: UserConfiguration) -> str:
        data = draft.semantic_dict()
        data["acknowledgements"] = []
        return canonical_json_digest(data)

    @staticmethod
    def _issue(code: str, field_path: str, explanation: str, remediation: str) -> ConfigurationIssue:
        return ConfigurationIssue(
            code=code, field_path=field_path, severity=IssueSeverity.ERROR,
            blocking_stage=BlockingStage.BUILD, rule_id=f"configuration.{code.lower()}",
            explanation=explanation, remediation=remediation,
        )
