"""Shared configuration evaluation used by CLI and future Textual views."""

from dataclasses import dataclass, replace
import json
from pathlib import Path
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
from macloader.build.config import effective_profile_digest, load_reviewed_profile
from macloader.build.acpi import AcpiProcessor, normalize_bios_binding
from macloader.evidence.acpi import AcpiEvidenceBundle
from macloader.evidence.usb import UsbEvidenceSession


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

        # The policy profile is a consumed build input, not just descriptive
        # review metadata.  Keep its exact target scope attached to the plan.
        try:
            reviewed_profile = load_reviewed_profile()
        except Exception as exc:
            reviewed_profile = None
            issues.append(self._issue("PROFILE_UNAVAILABLE", "build_profile", str(exc), "Restore the reviewed profile bundle before continuing."))
        if reviewed_profile is not None and target is not None:
            if (
                reviewed_profile.model_id != self.policy.model_id
                or reviewed_profile.product_id != target.product_id
                or reviewed_profile.version != target.version
                or reviewed_profile.build != target.build
            ):
                issues.append(self._issue(
                    "PROFILE_SCOPE_MISMATCH", "build_profile", "The reviewed EFI profile does not exactly match the accepted model and target.",
                    "Select the reviewed profile for the exact model, version, and build.",
                ))

        self._validate_options(draft, target, issues)
        self._validate_evidence(draft, snapshot, issues)
        resolved_policy_requirements = self._resolved_policy_requirements(draft, target)
        remaining_plan_requirements = [
            requirement for requirement in plan.unresolved_requirements
            if requirement not in resolved_policy_requirements
        ]
        for requirement in remaining_plan_requirements:
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
        unresolved = list(remaining_plan_requirements)
        unresolved.extend(item.explanation for item in blocking if item.explanation not in unresolved)
        plan_evidence_digests = [record.digest for record in draft.evidence]
        for record in draft.evidence:
            if record.kind != "acpi":
                continue
            source = self._evidence_source(record.private_ref)
            if source is not None:
                try:
                    plan_evidence_digests.append(
                        AcpiProcessor.capture_evidence_digest(source.parent, record.bios_binding)
                    )
                except (OSError, ValueError):
                    pass
        plan = replace(
            plan,
            target_version=target.version if target else "",
            target_build=target.build if target else "",
            target_release_digest=target.release_record_digest if target else "",
            stable_model_id=model.id if model else "",
            hardware_content_digest=self._snapshot_digest(snapshot),
            evidence_digests=plan_evidence_digests,
            profile_bindings=[*self.policy.profiles, reviewed_profile.profile_id if reviewed_profile else ""],
            effective_option_selections=[[key, value] for key, value in draft.option_selections],
            effective_profile_digest=(
                effective_profile_digest(reviewed_profile, dict(draft.option_selections))
                if reviewed_profile else ""
            ),
            effective_audio_layout=self._audio_layout(draft),
            unresolved_requirements=unresolved,
            accepted_configuration_digest="" if blocking else draft.semantic_digest,
        )
        accepted = None if blocking else AcceptedConfiguration(draft, draft.semantic_digest)
        return ConfigurationEvaluation(draft, tuple(issues), plan, report, accepted)

    def _resolved_policy_requirements(self, draft: UserConfiguration, target: Optional[object]) -> Tuple[str, ...]:
        """Resolve only requirements covered by the reviewed exact-target profile."""
        if target is None or getattr(target, "product_id", "") != "sequoia":
            return ()
        selected = draft.selected_options()
        if selected.get("profile.graphics") != "kaby-lake-r-uhd620":
            return ()
        if selected.get("profile.audio") not in {"layout-11", "layout-86", "layout-97", "layout-99"}:
            return ()
        return (
            "Exact framebuffer and device-id injection policy deferred to v0.0.5",
            "Exact framebuffer/connector policy deferred to v0.0.5 EFI generator",
            "Audio layout-id selection deferred to v0.0.5",
            "Exact layout ID selection deferred to v0.0.5 EFI generator and physical verification",
        )

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
        for record in draft.evidence:
            if record.machine_snapshot_id != snapshot.snapshot_id:
                issues.append(self._issue("EVIDENCE_SNAPSHOT_MISMATCH", f"evidence.{record.kind}", "Evidence belongs to a different hardware snapshot.", "Capture or import evidence from the active snapshot."))
            if snapshot.bios_version and normalize_bios_binding(record.bios_binding) != normalize_bios_binding(snapshot.bios_version):
                issues.append(self._issue("EVIDENCE_BIOS_MISMATCH", f"evidence.{record.kind}", "Evidence is bound to a different BIOS version.", "Capture evidence after confirming the active BIOS version."))
            source = self._evidence_source(record.private_ref)
            if source is None:
                issues.append(self._issue("EVIDENCE_SOURCE_MISSING", f"evidence.{record.kind}", "The private evidence source is missing or unsafe; metadata alone is not proof.", "Restore the private evidence file and re-evaluate the draft."))
                continue
            try:
                payload = json.loads(source.read_text(encoding="utf-8"))
                if record.kind == "usb":
                    actual = UsbEvidenceSession.from_dict(payload).to_evidence_record()
                elif record.kind == "acpi":
                    actual = AcpiEvidenceBundle.from_dict(payload).to_evidence_record()
                else:
                    raise ValueError("unsupported evidence kind")
                if (
                    actual.digest.lower() != record.digest.lower()
                    or actual.machine_snapshot_id != snapshot.snapshot_id
                    or normalize_bios_binding(actual.bios_binding) != normalize_bios_binding(snapshot.bios_version or "")
                    or normalize_bios_binding(actual.bios_binding) != normalize_bios_binding(record.bios_binding)
                    or actual.private_ref != record.private_ref
                ):
                    raise ValueError("evidence digest, source reference, snapshot, or BIOS binding does not match")
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                issues.append(self._issue("EVIDENCE_SOURCE_INVALID", f"evidence.{record.kind}", f"Private evidence could not be verified: {exc}", "Re-capture the evidence with the supported collector."))
        usb_records = [record for record in draft.evidence if record.kind == "usb"]
        if not any(
            record.completeness == EvidenceCompleteness.COMPLETE
            and record.physical_port_evidence
            and record.capture_method == "manual-physical-port-session"
            or (
                record.completeness == EvidenceCompleteness.PARTIAL
                and record.physical_port_evidence
                and record.capture_method == "manual-physical-port-session"
                and any("logical USB-C correlation unresolved" in check for check in record.unresolved_checks)
            )
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
    def _audio_layout(draft: UserConfiguration) -> Optional[int]:
        value = draft.selected_options().get("profile.audio", "")
        if not value.startswith("layout-"):
            return None
        try:
            return int(value.removeprefix("layout-"))
        except ValueError:
            return None

    @staticmethod
    def _evidence_source(private_ref: str) -> Optional[Path]:
        candidate = Path(private_ref)
        if not candidate.is_absolute() and any(part == ".." for part in candidate.parts):
            return None
        candidates = [candidate] if candidate.is_absolute() else [candidate, Path("workspace") / candidate]
        for path in candidates:
            absolute = path.absolute()
            if any(ancestor.is_symlink() for ancestor in (absolute, *absolute.parents) if ancestor.exists()):
                continue
            if absolute.is_file() and not absolute.is_symlink():
                return absolute
        return None

    @staticmethod
    def _issue(code: str, field_path: str, explanation: str, remediation: str) -> ConfigurationIssue:
        return ConfigurationIssue(
            code=code, field_path=field_path, severity=IssueSeverity.ERROR,
            blocking_stage=BlockingStage.BUILD, rule_id=f"configuration.{code.lower()}",
            explanation=explanation, remediation=remediation,
        )
