"""Unit tests for CompatibilityEngine and version-aware evaluation."""

from pathlib import Path
import pytest

from macloader.compatibility.engine import CompatibilityEngine
from macloader.database.loader import Database
from macloader.detection.fixture import FixtureHardwareProvider
from macloader.domain.compatibility import CompatibilityState
from macloader.exceptions import UnsupportedMacOSError


def test_evaluate_t480s_baseline_sequoia(t480s_baseline_fixture: Path, db: Database) -> None:
    snapshot = FixtureHardwareProvider(t480s_baseline_fixture).probe()
    engine = CompatibilityEngine(db=db)
    report = engine.evaluate(snapshot, target_macos="sequoia")

    assert report.model_id == "thinkpad-t480s"
    assert report.target_macos == "sequoia"
    assert report.overall_state == CompatibilityState.EXPERIMENTAL
    assert report.can_generate_build_plan is True

    categories = [r.category for r in report.component_results]
    assert "cpu" in categories
    assert "graphics" in categories
    assert "audio" in categories
    assert "ethernet" in categories
    assert "wifi" in categories
    assert "storage" in categories


def test_evaluate_t480_with_mx150_flags_conditional_and_requires_disabling(
    t480_mx150_fixture: Path,
    db: Database,
) -> None:
    snapshot = FixtureHardwareProvider(t480_mx150_fixture).probe()
    engine = CompatibilityEngine(db=db)
    report = engine.evaluate(snapshot, target_macos="sequoia")

    assert report.model_id == "thinkpad-t480"
    assert report.overall_state == CompatibilityState.CONDITIONAL

    # Verify MX150 component decision
    dgpu_res = next((r for r in report.component_results if "mx150" in r.component_id.lower()), None)
    assert dgpu_res is not None
    assert dgpu_res.decision.state == CompatibilityState.CONDITIONAL
    assert any("SSDT-dGPU-Off" in act or "-wegnoegpu" in act for act in dgpu_res.decision.required_actions)

    # Verify warning was added
    assert any("Discrete GPU detected" in w for w in report.warnings)


def test_samsung_pm981_is_conditional_not_unconditionally_blocked(
    t480_pm981_fixture: Path,
    db: Database,
) -> None:
    snapshot = FixtureHardwareProvider(t480_pm981_fixture).probe()
    engine = CompatibilityEngine(db=db)
    report = engine.evaluate(snapshot, target_macos="sequoia")

    # Overall state should be CONDITIONAL, not BLOCKED
    assert report.overall_state == CompatibilityState.CONDITIONAL
    assert report.can_generate_build_plan is True

    # Storage decision must require NVMeFix and contain instability warning
    storage_res = next((r for r in report.component_results if r.category == "storage"), None)
    assert storage_res is not None
    assert storage_res.decision.state == CompatibilityState.CONDITIONAL
    assert any("NVMeFix" in act for act in storage_res.decision.required_actions)


def test_tahoe_audio_and_wifi_behavior(t480s_baseline_fixture: Path, db: Database) -> None:
    snapshot = FixtureHardwareProvider(t480s_baseline_fixture).probe()
    engine = CompatibilityEngine(db=db)
    report = engine.evaluate(snapshot, target_macos="tahoe")

    assert report.target_macos == "tahoe"
    # Audio in Tahoe is CONDITIONAL because AppleHDA was removed
    audio_res = next((r for r in report.component_results if r.category == "audio"), None)
    assert audio_res is not None
    assert audio_res.decision.state == CompatibilityState.CONDITIONAL
    assert any("AppleHDA" in lim for lim in audio_res.decision.known_limitations)

    # Wi-Fi in Tahoe is EXPERIMENTAL
    wifi_res = next((r for r in report.component_results if r.category == "wifi"), None)
    assert wifi_res is not None
    assert wifi_res.decision.state == CompatibilityState.EXPERIMENTAL


def test_unsupported_model_blocks_evaluation(x1_carbon_fixture: Path, db: Database) -> None:
    snapshot = FixtureHardwareProvider(x1_carbon_fixture).probe()
    engine = CompatibilityEngine(db=db)
    report = engine.evaluate(snapshot, target_macos="sequoia")

    assert report.overall_state == CompatibilityState.BLOCKED
    assert report.can_generate_build_plan is False
    assert report.model_id is None


def test_invalid_macos_raises_unsupported_macos_error(t480s_baseline_fixture: Path, db: Database) -> None:
    snapshot = FixtureHardwareProvider(t480s_baseline_fixture).probe()
    engine = CompatibilityEngine(db=db)
    with pytest.raises(UnsupportedMacOSError, match="Unknown or unsupported macOS target"):
        engine.evaluate(snapshot, target_macos="windows95")
