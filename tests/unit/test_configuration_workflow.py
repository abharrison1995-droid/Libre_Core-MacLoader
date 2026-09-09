"""P0-P2 schema-driven configuration workflow coverage."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from macloader.configuration.migrations import import_configuration
from macloader.configuration.policy import load_configuration_policy
from macloader.configuration.service import ConfigurationService
from macloader.configuration.store import ConfigurationStore, ConfigurationStoreError
from macloader.dependencies.resolver import DependencyResolver
from macloader.detection.fixture import FixtureHardwareProvider
from macloader.domain.build_plan import BuildPlan
from macloader.domain.compatibility import CompatibilityState
from macloader.domain.configuration import UserConfiguration
from macloader.domain.contracts import IdentityReference
from macloader.domain.evidence import EvidenceCompleteness, EvidenceConfidence, EvidenceRecord
from macloader.domain.targets import MacOsTarget
from macloader.evidence.acpi import AcpiEvidenceBundle, AcpiTableRecord
from macloader.evidence.usb import UsbEvidenceSession, UsbObservationState, UsbPortObservation
from macloader.exceptions import DependencyNotFoundError


def test_policy_has_exact_release_and_versioned_defaults() -> None:
    policy = load_configuration_policy()
    release = policy.get_release("sequoia", "15.0", "24A335")
    assert release is not None
    assert len(release.release_record_digest) == 64
    assert policy.default_selections()
    with pytest.raises(TypeError):
        policy.options["new.option"] = policy.options["profile.audio"]  # type: ignore[index]


def test_target_rejects_product_aliases_and_round_trips() -> None:
    policy = load_configuration_policy()
    release = policy.get_release("sequoia", "15.0", "24A335")
    assert release is not None
    target = release.target()
    assert MacOsTarget.from_dict(target.to_dict()) == target
    with pytest.raises(ValueError, match="exact dotted version"):
        MacOsTarget("sequoia", "macOS Sequoia", "latest", "24A335", target.release_record_digest)


def test_configuration_round_trip_is_strict_and_semantically_stable() -> None:
    configuration = UserConfiguration(option_selections=(("profile.audio", "layout-11"),))
    restored = UserConfiguration.from_dict(configuration.to_dict())
    assert restored.semantic_digest == configuration.semantic_digest
    with pytest.raises(ValueError, match="unknown fields"):
        UserConfiguration.from_dict({**configuration.to_dict(), "unexpected": True})
    with pytest.raises(ValueError, match="Unsupported configuration schema"):
        UserConfiguration.from_dict({**configuration.to_dict(), "schema_version": "999"})


def test_fixture_configuration_path_requires_exact_target_and_usb_evidence(t480s_baseline_fixture: Path) -> None:
    snapshot = FixtureHardwareProvider(t480s_baseline_fixture).probe()
    service = ConfigurationService()
    draft = service.new_draft(snapshot)
    incomplete = service.evaluate(draft, snapshot)
    assert any(issue.code == "EXACT_TARGET_REQUIRED" for issue in incomplete.issues)
    assert any(issue.code == "USB_PHYSICAL_EVIDENCE_REQUIRED" for issue in incomplete.issues)

    release = service.policy.get_release("sequoia", "15.0", "24A335")
    assert release is not None
    targeted = UserConfiguration.from_dict({**draft.to_dict(), "target": release.target().to_dict()})
    evaluated = service.evaluate(targeted, snapshot)
    assert evaluated.plan.stable_model_id == "thinkpad-t480s"
    assert evaluated.plan.target_version == "15.0"
    assert evaluated.plan.target_build == "24A335"
    assert evaluated.plan.accepted_configuration_digest == ""
    assert evaluated.accepted is None
    assert evaluated.plan.build_ready is False

    physical_session = UsbEvidenceSession(
        snapshot.snapshot_id, snapshot.bios_version or "", "private/usb.json", "collector-1",
        (
            UsbPortObservation("left-a", "HS01", "USB-A", "5Gbps", "8086:9d2f"),
            UsbPortObservation("left-c", "HS02", "USB-C", "10Gbps", "8086:9d2f", orientation="both"),
        ),
    )
    with_evidence = replace(targeted, evidence=(physical_session.to_evidence_record(),))
    evidence_evaluation = service.evaluate(with_evidence, snapshot)
    assert not any(issue.code == "USB_PHYSICAL_EVIDENCE_REQUIRED" for issue in evidence_evaluation.issues)


def test_legacy_plan_import_is_a_draft_with_blocking_issue() -> None:
    draft, issues = import_configuration({
        "target_model": "Lenovo ThinkPad T480s",
        "target_macos": "sequoia",
        "hardware_snapshot_id": "legacy",
    })
    assert draft.target is None
    assert issues[0].code == "LEGACY_PLAN_REQUIRES_REVIEW"
    assert issues[0].blocking


def test_configuration_store_atomic_revision_backup_and_redaction(tmp_path: Path) -> None:
    store = ConfigurationStore(tmp_path / "configs")
    configuration = UserConfiguration(
        configuration_id="example", revision=1,
        identity_ref=IdentityReference("0.1", "private/example.json", True),
    )
    path = store.save(configuration)
    assert path.is_file()
    assert store.load("example").semantic_digest == configuration.semantic_digest
    with pytest.raises(ConfigurationStoreError, match="revision"):
        store.save(configuration, expected_revision=1)
    updated = UserConfiguration.from_dict({**configuration.to_dict(), "revision": 2})
    store.save(updated, expected_revision=1)
    assert path.with_suffix(".json.bak").is_file()
    exported = store.export_public(updated)
    assert exported["identity_ref"] is None
    assert "private/example.json" not in json.dumps(exported)
    assert UserConfiguration.from_dict(exported).evidence == ()


def test_evidence_contract_round_trip_and_rejects_invalid_content() -> None:
    record = EvidenceRecord(
        evidence_id="usb-session-1", kind="usb", schema_version="1", digest="a" * 64,
        private_ref="private/usb.json", machine_snapshot_id="snapshot", bios_binding="bios-1",
        capture_method="manual", capture_version="1", completeness=EvidenceCompleteness.COMPLETE,
        confidence=EvidenceConfidence.HIGH, unresolved_checks=(), physical_port_evidence=True,
    )
    assert EvidenceRecord.from_dict(record.to_dict()) == record
    with pytest.raises(ValueError, match="digest"):
        replace(record, digest="bad")
    with pytest.raises(ValueError, match="physical_port_evidence"):
        replace(record, physical_port_evidence="yes")  # type: ignore[arg-type]


def test_service_acknowledgement_is_bound_to_exact_policy_warning(t480s_baseline_fixture: Path) -> None:
    snapshot = FixtureHardwareProvider(t480s_baseline_fixture).probe()
    service = ConfigurationService()
    draft = service.new_draft(snapshot)
    option = service.policy.options["profile.smbios"]
    acknowledged = service.acknowledge(draft, option.option_id, option.explanation)
    evaluation = service.evaluate(acknowledged, snapshot)
    assert not any(issue.code == "ACKNOWLEDGEMENT_REQUIRED" and issue.field_path.endswith("profile.smbios") for issue in evaluation.issues)
    wrong = service.acknowledge(draft, option.option_id, "different warning")
    wrong_evaluation = service.evaluate(wrong, snapshot)
    assert any(issue.code == "ACKNOWLEDGEMENT_REQUIRED" and issue.field_path.endswith("profile.smbios") for issue in wrong_evaluation.issues)


def test_service_rejects_stale_target_and_unknown_option(t480s_baseline_fixture: Path) -> None:
    snapshot = FixtureHardwareProvider(t480s_baseline_fixture).probe()
    service = ConfigurationService()
    draft = service.new_draft(snapshot)
    stale_target = MacOsTarget("sequoia", "macOS Sequoia", "15.1", "24B83", "b" * 64)
    stale = replace(draft, target=stale_target, option_selections=(("unknown.option", "value"),))
    evaluation = service.evaluate(stale, snapshot)
    codes = {issue.code for issue in evaluation.issues}
    assert "TARGET_NOT_IN_POLICY" in codes
    assert "OPTION_UNKNOWN" in codes
    assert "OPTION_REQUIRED" in codes


def test_store_rejects_unsafe_and_missing_paths(tmp_path: Path) -> None:
    store = ConfigurationStore(tmp_path / "configs")
    with pytest.raises(ConfigurationStoreError, match="safe file name"):
        store.path_for("../escape")
    with pytest.raises(ConfigurationStoreError, match="missing or unsafe"):
        store.load("missing")


def test_resolver_rejects_forged_incomplete_accepted_binding() -> None:
    from macloader.database.loader import get_database

    catalog = get_database(reload=True).get_dependency_catalog()
    assert catalog is not None
    plan = BuildPlan(
        target_model="Lenovo ThinkPad T480s", target_macos="sequoia", hardware_snapshot_id="snapshot",
        support_state=CompatibilityState.EXPERIMENTAL, policy_version=catalog.policy_version,
        accepted_configuration_digest="a" * 64,
    )
    with pytest.raises(DependencyNotFoundError, match="accepted configuration binding"):
        DependencyResolver().resolve(plan)


def test_usb_completeness_is_derived_from_physical_observations() -> None:
    ports = (
        UsbPortObservation("left-a", "HS01", "USB-A", "5Gbps", "8086:9d2f"),
        UsbPortObservation("left-c", "HS02", "USB-C", "10Gbps", "8086:9d2f", orientation="both"),
    )
    session = UsbEvidenceSession("snapshot", "N22ET76W", "private/usb.json", "collector-1", ports, EvidenceConfidence.HIGH)
    assert session.completeness == (EvidenceCompleteness.COMPLETE, ())
    record = session.to_evidence_record()
    assert record.physical_port_evidence is True
    assert record.capture_method == "manual-physical-port-session"
    assert UsbEvidenceSession.from_dict(session.to_dict()).completeness == session.completeness

    partial = UsbEvidenceSession(
        "snapshot", "N22ET76W", "private/usb.json", "collector-1",
        (replace(ports[1], orientation=None, state=UsbObservationState.UNTESTED),),
    )
    assert partial.completeness[0] == EvidenceCompleteness.PARTIAL
    conflicting = UsbEvidenceSession("snapshot", "N22ET76W", "private/usb.json", "collector-1", (ports[0], ports[0]))
    assert conflicting.completeness[0] == EvidenceCompleteness.CONFLICTING


def test_acpi_evidence_is_metadata_only_and_duplicate_tables_are_incomplete() -> None:
    table = AcpiTableRecord("DSDT", "c" * 64, "private/DSDT.aml", ("_SB.PCI0",))
    bundle = AcpiEvidenceBundle("snapshot", "N22ET76W", "private/acpi.json", "acpidump-1", (table,), EvidenceConfidence.MEDIUM)
    record = bundle.to_evidence_record()
    assert record.kind == "acpi"
    assert record.completeness == EvidenceCompleteness.COMPLETE
    assert AcpiEvidenceBundle.from_dict(bundle.to_dict()).complete is True
    duplicate = AcpiEvidenceBundle("snapshot", "N22ET76W", "private/acpi.json", "acpidump-1", (table, table))
    assert duplicate.to_evidence_record().completeness == EvidenceCompleteness.PARTIAL
    with pytest.raises(ValueError, match="digest"):
        AcpiTableRecord("DSDT", "bad", "private/DSDT.aml")
