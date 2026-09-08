"""Unit tests for S10 hardware contracts and raw evidence boundary validation.

Validates that raw_evidence and nested inventory_status are strictly checked and
normalized at snapshot boundaries, that CompatibilityEngine never crashes on
malformed types or calls .get() on arbitrary imported objects, and that
malformed inputs yield contextual domain errors or conservative UNKNOWN policies.
"""

from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock
import pytest
from click.testing import CliRunner

from macloader.compatibility.engine import CompatibilityEngine
from macloader.database.loader import Database, get_database
from macloader.detection.fixture import FixtureHardwareProvider
from macloader.domain.compatibility import CompatibilityState
from macloader.domain.hardware import (
    CpuInfo,
    GpuInfo,
    HardwareSnapshot,
    PciDevice,
    normalize_inventory_status,
    normalize_raw_evidence,
)
from macloader.exceptions import HardwareContractError
from macloader.ui.cli import cli


def _make_minimal_t480s_snapshot(**kwargs: Any) -> HardwareSnapshot:
    """Construct a minimal valid T480s snapshot for testing."""
    defaults: Dict[str, Any] = {
        "manufacturer": "LENOVO",
        "product_name": "20L7CTO1WW",
        "product_version": "ThinkPad T480s",
        "machine_type": "20L7",
        "cpu": CpuInfo(model_name="Intel Core i7-8550U", generation="Kaby Lake Refresh"),
        "igpu": GpuInfo(name="Intel UHD Graphics 620", pci=PciDevice(vendor_id="8086", device_id="5917"), is_igpu=True),
        "raw_evidence": {},
    }
    defaults.update(kwargs)
    return HardwareSnapshot(**defaults)


# ---------------------------------------------------------------------------
# 1. Normalization helper tests
# ---------------------------------------------------------------------------

def test_normalize_inventory_status_handles_non_dict() -> None:
    assert normalize_inventory_status(None) == {}
    assert normalize_inventory_status("not a dict") == {}
    assert normalize_inventory_status([1, 2, 3]) == {}
    assert normalize_inventory_status(42) == {}


def test_normalize_inventory_status_strictly_filters_booleans() -> None:
    raw = {
        "wifi": True,
        "audio": False,
        "ethernet": 1,         # int truthy, but not bool True
        "storage": "true",     # string, not bool True
        "input": None,         # None
        123: True,             # Non-string key
    }
    normalized = normalize_inventory_status(raw)
    assert normalized["wifi"] is True
    assert normalized["audio"] is False
    assert normalized["ethernet"] is False
    assert normalized["storage"] is False
    assert normalized["input"] is False
    assert 123 not in normalized


def test_normalize_raw_evidence_accepts_none_and_empty() -> None:
    assert normalize_raw_evidence(None) == {}
    assert normalize_raw_evidence({}) == {}


def test_normalize_raw_evidence_rejects_non_dict() -> None:
    with pytest.raises(HardwareContractError, match="raw_evidence must be a dictionary"):
        normalize_raw_evidence("not-a-dict")

    with pytest.raises(HardwareContractError, match="raw_evidence must be a dictionary"):
        normalize_raw_evidence([1, 2, 3])


def test_normalize_raw_evidence_validates_nested_inventory_status() -> None:
    with pytest.raises(HardwareContractError, match="inventory_status in raw_evidence must be a dictionary"):
        normalize_raw_evidence({"inventory_status": "string"})

    with pytest.raises(HardwareContractError, match="inventory_status in raw_evidence must be a dictionary"):
        normalize_raw_evidence({"inventory_status": [1, 2]})

    # Valid nested inventory_status is normalized
    res = normalize_raw_evidence({"inventory_status": {"wifi": True, "audio": "yes"}})
    assert res["inventory_status"]["wifi"] is True
    assert res["inventory_status"]["audio"] is False


# ---------------------------------------------------------------------------
# 2. HardwareSnapshot boundary tests (direct construction & from_dict)
# ---------------------------------------------------------------------------

def test_hardware_snapshot_from_dict_rejects_non_dict_data() -> None:
    for invalid in (None, "string", [1, 2, 3], 42, 3.14):
        with pytest.raises(HardwareContractError, match="Hardware snapshot data must be a dictionary"):
            HardwareSnapshot.from_dict(invalid)


def test_hardware_snapshot_from_dict_rejects_malformed_raw_evidence() -> None:
    for invalid in ("not-a-dict", [1, 2], 123):
        with pytest.raises(HardwareContractError, match="raw_evidence must be a dictionary"):
            HardwareSnapshot.from_dict({"raw_evidence": invalid})


def test_hardware_snapshot_from_dict_rejects_malformed_nested_inventory_status() -> None:
    for invalid in ("not-a-dict", [1, 2], 123):
        with pytest.raises(HardwareContractError, match="inventory_status in raw_evidence must be a dictionary"):
            HardwareSnapshot.from_dict({"raw_evidence": {"inventory_status": invalid}})


def test_hardware_snapshot_from_dict_validates_field_types() -> None:
    with pytest.raises(HardwareContractError, match="cpu must be a dictionary or null"):
        HardwareSnapshot.from_dict({"cpu": "intel-i7"})

    with pytest.raises(HardwareContractError, match="igpu must be a dictionary or null"):
        HardwareSnapshot.from_dict({"igpu": "intel-uhd-620"})

    with pytest.raises(HardwareContractError, match="Field 'dgpus' must be a list"):
        HardwareSnapshot.from_dict({"dgpus": "nvidia-mx150"})

    with pytest.raises(HardwareContractError, match="Field 'storage' must be a list"):
        HardwareSnapshot.from_dict({"storage": {"model": "PM981"}})


def test_hardware_snapshot_post_init_validates_direct_construction() -> None:
    # Setting raw_evidence=None normalizes safely to {}
    snap = _make_minimal_t480s_snapshot(raw_evidence=None)
    assert snap.raw_evidence == {}

    # Setting raw_evidence to a non-dict raises HardwareContractError
    with pytest.raises(HardwareContractError, match="raw_evidence must be a dictionary"):
        _make_minimal_t480s_snapshot(raw_evidence="invalid-string")

    with pytest.raises(HardwareContractError, match="raw_evidence must be a dictionary"):
        _make_minimal_t480s_snapshot(raw_evidence=[1, 2, 3])

    # Setting inventory_status to a non-dict raises HardwareContractError
    with pytest.raises(HardwareContractError, match="inventory_status in raw_evidence must be a dictionary"):
        _make_minimal_t480s_snapshot(raw_evidence={"inventory_status": "not-a-dict"})


# ---------------------------------------------------------------------------
# 3. CompatibilityEngine direct API tests
# ---------------------------------------------------------------------------

def test_engine_evaluate_rejects_non_snapshot_input(db: Database) -> None:
    engine = CompatibilityEngine(db=db)
    invalid: Any
    for invalid in (None, "string", [1, 2, 3], 42, {}):
        with pytest.raises(HardwareContractError, match="Expected HardwareSnapshot instance"):
            engine.evaluate(invalid)


def test_engine_evaluate_does_not_crash_on_mutated_raw_evidence_and_yields_unknown_policy(db: Database) -> None:
    """Even if an attacker or mock bypasses __post_init__ and sets raw_evidence to None/list/string,

    CompatibilityEngine must not crash with AttributeError and must yield UNKNOWN policy without build eligibility.
    """
    engine = CompatibilityEngine(db=db)

    for bad_evidence in (None, "corrupted", [1, 2, 3], 999):
        snap = _make_minimal_t480s_snapshot()
        object.__setattr__(snap, "raw_evidence", bad_evidence)

        # Must not raise AttributeError: 'NoneType' object has no attribute 'get'
        report = engine.evaluate(snap, target_macos="sequoia")

        # Must evaluate to UNKNOWN and refuse build plan generation
        assert report.overall_state == CompatibilityState.UNKNOWN
        assert report.can_generate_build_plan is False

        # Missing device categories are conservatively marked unknown
        unknown_cats = [r.category for r in report.component_results if r.decision.state == CompatibilityState.UNKNOWN]
        assert "audio" in unknown_cats
        assert "wifi" in unknown_cats
        assert "storage" in unknown_cats


def test_engine_evaluate_rejects_inferred_completion_from_truthy_non_booleans(db: Database) -> None:
    """Non-boolean truthy values like 'true', 1, or [1] must NOT infer completed inventory."""
    engine = CompatibilityEngine(db=db)
    snap = _make_minimal_t480s_snapshot()

    # Artificially set non-boolean values in inventory_status
    object.__setattr__(
        snap,
        "raw_evidence",
        {
            "inventory_status": {
                "audio": "true",       # string
                "ethernet": 1,         # integer
                "wifi": [True],        # list
                "bluetooth": {"ok": True},  # dict
                "storage": "yes",      # string
                "input": 42,           # integer
            }
        },
    )

    report = engine.evaluate(snap, target_macos="sequoia")

    # Inferred completion is rejected; all unconfirmed empty classes are UNKNOWN
    assert report.overall_state == CompatibilityState.UNKNOWN
    assert report.can_generate_build_plan is False
    unknown_reasons = [r.decision.reason for r in report.component_results if r.decision.state == CompatibilityState.UNKNOWN]
    assert any("Inventory for this device class was not confirmed complete" in reason for reason in unknown_reasons)


def test_engine_handles_arbitrary_imported_component_match_rules(db: Database) -> None:
    """CompatibilityEngine must handle non-dict match_rules, None rule values, and scalar rule values."""
    engine = CompatibilityEngine(db=db)

    for invalid_rules in (None, "rules", 123, {"pci_ids": None}, {"pci_ids": 1234}, {"pci_ids": [None, 8086]}):
        mock_comp = MagicMock()
        mock_comp.id = "mock-component"
        mock_comp.match_rules = invalid_rules

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(db, "get_components_by_category", lambda cat: [mock_comp])
            # None of these should raise AttributeError or TypeError
            comp = engine._match_component_by_pci("audio", "8086:9d71")
            assert comp is None or comp == mock_comp

            comp_usb = engine._match_component_by_usb("8087:0a2b")
            assert comp_usb is None


def test_engine_evaluate_rejects_non_string_target_macos(db: Database) -> None:
    """Non-string target_macos raises UnsupportedMacOSError cleanly."""
    from macloader.exceptions import UnsupportedMacOSError

    engine = CompatibilityEngine(db=db)
    snap = _make_minimal_t480s_snapshot()

    for invalid_target in (None, 123, ["sequoia"]):
        with pytest.raises(UnsupportedMacOSError, match="Invalid macOS target"):
            engine.evaluate(snap, target_macos=invalid_target)  # type: ignore[arg-type]


def test_engine_evaluate_handles_duck_typed_snapshot_with_non_dict_inventory_status(db: Database) -> None:
    """A duck-typed snapshot whose get_inventory_status returns None or a list must not crash engine."""
    engine = CompatibilityEngine(db=db)
    snap = _make_minimal_t480s_snapshot()

    for bad_ret in (None, "string", [1, 2, 3]):
        setattr(snap, "get_inventory_status", lambda b=bad_ret: b)
        report = engine.evaluate(snap, target_macos="sequoia")
        assert report.overall_state == CompatibilityState.UNKNOWN
        assert report.can_generate_build_plan is False


# ---------------------------------------------------------------------------
# 4. JSON / YAML Fixture loading and CLI boundary tests
# ---------------------------------------------------------------------------

def test_fixture_provider_rejects_top_level_null(tmp_path: Path) -> None:
    fixture_path = tmp_path / "null_fixture.json"
    fixture_path.write_text("null", encoding="utf-8")

    provider = FixtureHardwareProvider(fixture_path)
    with pytest.raises(HardwareContractError, match="must contain a top-level dictionary"):
        provider.probe()


def test_fixture_provider_rejects_top_level_list(tmp_path: Path) -> None:
    fixture_path = tmp_path / "list_fixture.json"
    fixture_path.write_text("[1, 2, 3]", encoding="utf-8")

    provider = FixtureHardwareProvider(fixture_path)
    with pytest.raises(HardwareContractError, match="must contain a top-level dictionary"):
        provider.probe()


def test_fixture_provider_rejects_top_level_string(tmp_path: Path) -> None:
    fixture_path = tmp_path / "str_fixture.json"
    fixture_path.write_text('"just a string"', encoding="utf-8")

    provider = FixtureHardwareProvider(fixture_path)
    with pytest.raises(HardwareContractError, match="must contain a top-level dictionary"):
        provider.probe()


def test_fixture_provider_rejects_malformed_raw_evidence(tmp_path: Path) -> None:
    fixture_path = tmp_path / "bad_evidence.json"
    fixture_path.write_text('{"manufacturer": "LENOVO", "raw_evidence": "not a dict"}', encoding="utf-8")

    provider = FixtureHardwareProvider(fixture_path)
    with pytest.raises(HardwareContractError, match="raw_evidence must be a dictionary"):
        provider.probe()


def test_fixture_provider_rejects_yaml_invalid_shapes(tmp_path: Path) -> None:
    # Top-level null in YAML
    yaml_null = tmp_path / "null_fixture.yaml"
    yaml_null.write_text("~", encoding="utf-8")
    with pytest.raises(HardwareContractError, match="must contain a top-level dictionary"):
        FixtureHardwareProvider(yaml_null).probe()

    # Top-level scalar in YAML
    yaml_str = tmp_path / "str_fixture.yml"
    yaml_str.write_text("just-a-string\n", encoding="utf-8")
    with pytest.raises(HardwareContractError, match="must contain a top-level dictionary"):
        FixtureHardwareProvider(yaml_str).probe()


def test_cli_probe_with_malformed_fixture_exits_cleanly_without_traceback(tmp_path: Path) -> None:
    fixture_path = tmp_path / "bad.json"
    fixture_path.write_text("null", encoding="utf-8")

    runner = CliRunner()
    res = runner.invoke(cli, ["probe", "-f", str(fixture_path)])

    assert res.exit_code == 1
    assert "Detection Error:" in res.output or "Detection Error:" in getattr(res, "stderr", "")
    assert "Traceback" not in res.output


def test_cli_support_with_malformed_fixture_exits_cleanly_without_traceback(tmp_path: Path) -> None:
    fixture_path = tmp_path / "bad.json"
    fixture_path.write_text("[1, 2, 3]", encoding="utf-8")

    runner = CliRunner()
    res = runner.invoke(cli, ["support", "-m", "sequoia", "-f", str(fixture_path)])

    assert res.exit_code == 1
    assert "Support Evaluation Error:" in res.output or "Detection Error:" in res.output or "Support Evaluation Error:" in getattr(res, "stderr", "")
    assert "Traceback" not in res.output


def test_cli_plan_with_malformed_fixture_exits_cleanly_without_traceback(tmp_path: Path) -> None:
    fixture_path = tmp_path / "bad.json"
    fixture_path.write_text('{"manufacturer": "LENOVO", "raw_evidence": "not-a-dict"}', encoding="utf-8")

    runner = CliRunner()
    res = runner.invoke(cli, ["plan", "-m", "sequoia", "-f", str(fixture_path)])

    assert res.exit_code == 1
    assert "Traceback" not in res.output
