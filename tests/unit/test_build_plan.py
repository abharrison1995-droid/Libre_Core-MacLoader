"""Unit tests for preliminary BuildPlan generation."""

from pathlib import Path
from macloader.compatibility.engine import CompatibilityEngine
from macloader.database.loader import Database
from macloader.detection.fixture import FixtureHardwareProvider


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
