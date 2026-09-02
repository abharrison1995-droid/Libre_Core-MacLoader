"""Unit tests for preliminary BuildPlan generation."""

from pathlib import Path
from macloader.compatibility.engine import CompatibilityEngine
from macloader.database.loader import Database
from macloader.detection.fixture import FixtureHardwareProvider
from macloader.domain.compatibility import CompatibilityReport, CompatibilityState, SupportDecision


def test_generate_build_plan_t480s_tahoe(t480s_baseline_fixture: Path, db: Database) -> None:
    snapshot = FixtureHardwareProvider(t480s_baseline_fixture).probe()
    engine = CompatibilityEngine(db=db)
    report = engine.evaluate(snapshot, target_macos="tahoe")
    plan = engine.generate_build_plan(report)

    assert plan.target_model == "Lenovo ThinkPad T480s"
    assert plan.target_macos == "tahoe"
    assert plan.is_preliminary is True
    assert "accelerated_intel_uhd_620" in plan.required_capabilities
    assert "alc257_audio" in plan.required_capabilities
    assert "intel_gigabit_ethernet" in plan.required_capabilities
    assert "intel_wireless_lan" in plan.required_capabilities

    # Check unresolved requirements are present rather than fabricated
    assert any("framebuffer" in u.lower() for u in plan.unresolved_requirements)
    assert any("tahoe audio" in u.lower() for u in plan.unresolved_requirements)


def test_generate_build_plan_t480_mx150(t480_mx150_fixture: Path, db: Database) -> None:
    snapshot = FixtureHardwareProvider(t480_mx150_fixture).probe()
    engine = CompatibilityEngine(db=db)
    report = engine.evaluate(snapshot, target_macos="sequoia")
    plan = engine.generate_build_plan(report)

    assert plan.target_model == "Lenovo ThinkPad T480"
    assert "disable_discrete_gpu" in plan.required_capabilities
    assert any(c.get("category") == "graphics_dgpu" for c in plan.planned_components)


def test_generate_build_plan_omits_graphics_when_no_igpu_detected(db: Database) -> None:
    # Regression test: generate_build_plan() must not unconditionally claim
    # Intel UHD 620 graphics support when no iGPU was actually detected.
    engine = CompatibilityEngine(db=db)
    report = CompatibilityReport(
        snapshot_id="test-no-igpu",
        target_macos="sequoia",
        model_id="thinkpad-t480",
        model_name="Lenovo ThinkPad T480",
        overall_state=CompatibilityState.CONDITIONAL,
        model_decision=SupportDecision(target="model:thinkpad-t480", state=CompatibilityState.CONDITIONAL, reason="test"),
        component_results=[],
        can_generate_build_plan=True,
    )
    plan = engine.generate_build_plan(report)

    assert "accelerated_intel_uhd_620" not in plan.required_capabilities
    assert not any(c.get("category") == "graphics" for c in plan.planned_components)
