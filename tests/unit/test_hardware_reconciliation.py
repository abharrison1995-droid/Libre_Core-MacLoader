"""Observation reconciliation preserves uncertainty and invalidates scoped inputs."""
from dataclasses import replace
import json
from pathlib import Path

from macloader.configuration.observations import (
    EVIDENCE_SCOPES, hardware_facts, reconcile_configuration, reconcile_observations, scope_digest, snapshot_observations,
)
from macloader.configuration.service import ConfigurationService
from macloader.domain.configuration import HardwareObservation, ObservationStatus
from macloader.domain.evidence import EvidenceCompleteness, EvidenceConfidence, EvidenceRecord
from macloader.domain.hardware import HardwareSnapshot


def snapshot(fixture: Path) -> HardwareSnapshot:
    return HardwareSnapshot.from_dict(json.loads(fixture.read_text()))


def test_high_confidence_auto_accepts_unknown_stays_unresolved() -> None:
    high = HardwareObservation("machine.type", "20L8", "dmi", "2", confidence="high")
    unknown = HardwareObservation("panel.touch", "", "edid", "2", status=ObservationStatus.UNKNOWN)
    result = reconcile_observations((high, unknown))
    assert result.confirmations[0].effective_value == "20L8"
    assert result.unresolved == ("panel.touch",)


def test_conflicting_observations_do_not_accept() -> None:
    one = HardwareObservation("machine.type", "20L8", "dmi", "2", confidence="high")
    two = replace(one, value="20L7", source="CIM")
    result = reconcile_observations((one, two))
    assert not result.confirmations
    assert all(o.status == ObservationStatus.CONFLICTING for o in result.observations)
    assert result.unresolved == ("machine.type",)


def test_missing_touch_probe_does_not_mean_non_touch(t480s_baseline_fixture: Path) -> None:
    observed = snapshot(t480s_baseline_fixture)
    assert hardware_facts(observed)["panel.touch"] is None
    assert next(o for o in snapshot_observations(observed) if o.field_path == "panel.touch").status == ObservationStatus.UNKNOWN


def test_repeat_probe_is_idempotent_and_private(t480s_baseline_fixture: Path) -> None:
    observed = snapshot(t480s_baseline_fixture)
    service = ConfigurationService()
    reconciled = service.reconcile(service.new_draft(observed), observed)
    repeated = service.reconcile(reconciled, replace(observed, timestamp="later"))
    assert repeated.semantic_digest == reconciled.semantic_digest
    assert reconciled.observations and reconciled.confirmations
    serialized = json.dumps([o.to_dict() for o in reconciled.observations])
    assert "REDACTED" not in serialized
    assert "serial_number" not in serialized
    assert "mac_address" not in serialized
    assert "storage.serial" not in serialized


def test_bios_invalidates_acpi_but_storage_preserves_usb(t480s_baseline_fixture: Path) -> None:
    observed = snapshot(t480s_baseline_fixture)
    service = ConfigurationService()
    draft = service.reconcile(service.new_draft(observed), observed)
    records = tuple(EvidenceRecord(
        kind, kind, "1", "a" * 64, "private", observed.snapshot_id, observed.bios_version or "", "test", "1",
        EvidenceCompleteness.COMPLETE, EvidenceConfidence.HIGH,
        input_scope=EVIDENCE_SCOPES[kind], input_digest=scope_digest(observed, EVIDENCE_SCOPES[kind]),
    ) for kind in ("acpi", "usb"))
    draft = replace(draft, evidence=records)
    bios_changed = reconcile_configuration(draft, replace(observed, bios_version="N22ET85W (1.62 )"))
    assert all(record.completeness == EvidenceCompleteness.STALE for record in bios_changed.evidence)
    storage = [replace(s, model="changed model") for s in observed.storage]
    unrelated = reconcile_configuration(draft, replace(observed, storage=storage))
    assert all(record.completeness == EvidenceCompleteness.COMPLETE for record in unrelated.evidence)


def test_legacy_evidence_stales_conservatively(t480s_baseline_fixture: Path) -> None:
    observed = snapshot(t480s_baseline_fixture)
    service = ConfigurationService()
    draft = service.reconcile(service.new_draft(observed), observed)
    record = EvidenceRecord("acpi", "acpi", "1", "a" * 64, "private", observed.snapshot_id, "BIOS", "test", "1", EvidenceCompleteness.COMPLETE)
    changed = replace(observed, storage=[])
    assert reconcile_configuration(replace(draft, evidence=(record,)), changed).evidence[0].completeness == EvidenceCompleteness.STALE


def test_candidate_matching_requires_observed_panel_and_codec_subsystem(t480s_baseline_fixture: Path) -> None:
    from macloader.configuration.campaign_match import match_campaign
    from macloader.database.loader import Database

    observed = snapshot(t480s_baseline_fixture)
    observed = replace(observed, machine_type="20L8", product_name="20L8CTO1WW", bios_version="N22ET85W (1.62 )")
    observed.raw_evidence["inventory_status"]["pci"] = True
    db = Database()
    incomplete = match_campaign(observed, db)
    assert "panel.touch" in incomplete.unknown
    assert "audio.subsystem" in incomplete.unknown
    observed.displays = [replace(d, touch_capability=False, source="synthetic-test-only") for d in observed.displays]
    observed.audio = [replace(a, codec_subsystem_id="17aa:2258") for a in observed.audio]
    assert match_campaign(observed, db).ready
    observed.displays = [replace(d, touch_capability=True) for d in observed.displays]
    assert "panel.touch" in match_campaign(observed, db).mismatches
    observed.wifi = []
    assert not match_campaign(observed, db).ready


def test_reviewed_campaign_accepts_detected_i219_v_reference_variant() -> None:
    from tests.unit.test_autoloader import candidate
    snapshot = candidate()
    snapshot.ethernet[0].pci.device_id = "15d8"
    from macloader.configuration.campaign_match import match_campaign
    from macloader.database.loader import get_database
    assert match_campaign(snapshot, get_database()).ready
