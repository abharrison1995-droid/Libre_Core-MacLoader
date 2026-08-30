"""Unit tests for strict ThinkPad model matching and disambiguation."""

from pathlib import Path
from macloader.compatibility.model_matcher import match_model
from macloader.database.loader import Database
from macloader.detection.fixture import FixtureHardwareProvider
from macloader.domain.compatibility import CompatibilityState


def test_match_t480s_baseline(t480s_baseline_fixture: Path, db: Database) -> None:
    provider = FixtureHardwareProvider(t480s_baseline_fixture)
    snapshot = provider.probe()
    model, decision = match_model(snapshot, db)

    assert model is not None
    assert model.id == "thinkpad-t480s"
    assert model.display_name == "Lenovo ThinkPad T480s"
    assert decision.state == CompatibilityState.EXPERIMENTAL


def test_match_t480_igpu(t480_igpu_fixture: Path, db: Database) -> None:
    provider = FixtureHardwareProvider(t480_igpu_fixture)
    snapshot = provider.probe()
    model, decision = match_model(snapshot, db)

    assert model is not None
    assert model.id == "thinkpad-t480"
    assert model.display_name == "Lenovo ThinkPad T480"
    assert decision.state == CompatibilityState.EXPERIMENTAL


def test_t480_and_t480s_are_unambiguously_distinguished(
    t480s_baseline_fixture: Path,
    t480_igpu_fixture: Path,
    db: Database,
) -> None:
    t480s_snap = FixtureHardwareProvider(t480s_baseline_fixture).probe()
    t480_snap = FixtureHardwareProvider(t480_igpu_fixture).probe()

    model_s, _ = match_model(t480s_snap, db)
    model_non_s, _ = match_model(t480_snap, db)

    assert model_s is not None and model_s.id == "thinkpad-t480s"
    assert model_non_s is not None and model_non_s.id == "thinkpad-t480"
    assert model_s.id != model_non_s.id


def test_reject_unsupported_lenovo_model(x1_carbon_fixture: Path, db: Database) -> None:
    snapshot = FixtureHardwareProvider(x1_carbon_fixture).probe()
    model, decision = match_model(snapshot, db)

    assert model is None
    assert decision.state == CompatibilityState.BLOCKED
    assert "Unsupported Lenovo ThinkPad model" in decision.reason


def test_reject_non_lenovo_hardware(non_lenovo_fixture: Path, db: Database) -> None:
    snapshot = FixtureHardwareProvider(non_lenovo_fixture).probe()
    model, decision = match_model(snapshot, db)

    assert model is None
    assert decision.state == CompatibilityState.BLOCKED
    assert "Unsupported manufacturer 'Dell Inc.'" in decision.reason
