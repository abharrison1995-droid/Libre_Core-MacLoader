"""Integration tests covering end-to-end detection, compatibility, and planning pipeline."""

from pathlib import Path
from macloader.orchestrator import Orchestrator
from macloader.domain.compatibility import CompatibilityState


def test_full_pipeline_t480s_baseline(t480s_baseline_fixture: Path) -> None:
    orchestrator = Orchestrator()
    snapshot = orchestrator.probe_hardware(fixture_path=t480s_baseline_fixture)
    report = orchestrator.check_support(snapshot, target_macos="sequoia")
    plan = orchestrator.generate_plan(snapshot, target_macos="sequoia")

    assert snapshot.machine_type == "20L7"
    assert report.model_id == "thinkpad-t480s"
    assert report.overall_state == CompatibilityState.EXPERIMENTAL
    assert plan.target_model == "Lenovo ThinkPad T480s"
    assert "accelerated_intel_uhd_620" in plan.required_capabilities


def test_full_pipeline_t480_mx150(t480_mx150_fixture: Path) -> None:
    orchestrator = Orchestrator()
    snapshot = orchestrator.probe_hardware(fixture_path=t480_mx150_fixture)
    report = orchestrator.check_support(snapshot, target_macos="sequoia")
    plan = orchestrator.generate_plan(snapshot, target_macos="sequoia")

    assert snapshot.machine_type == "20L6"
    assert report.model_id == "thinkpad-t480"
    assert report.overall_state == CompatibilityState.CONDITIONAL
    assert "disable_discrete_gpu" in plan.required_capabilities


def test_full_pipeline_unsupported_model(x1_carbon_fixture: Path) -> None:
    orchestrator = Orchestrator()
    snapshot = orchestrator.probe_hardware(fixture_path=x1_carbon_fixture)
    report = orchestrator.check_support(snapshot, target_macos="sequoia")

    assert report.overall_state == CompatibilityState.UNKNOWN
    assert report.can_generate_build_plan is False
