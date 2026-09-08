"""Unit tests for capability mapping, conditional selection, and version policy resolution."""

from pathlib import Path
import pytest

from macloader.database.loader import Database
from macloader.dependencies.resolver import DependencyResolver
from macloader.detection.fixture import FixtureHardwareProvider
from macloader.compatibility.engine import CompatibilityEngine
from macloader.domain.dependencies import ArtifactVariant
from macloader.domain.build_plan import BuildPlan
from macloader.domain.compatibility import CompatibilityState
from macloader.exceptions import DependencyNotFoundError


def test_resolve_t480s_baseline_sequoia(test_db: Database, t480s_baseline_fixture: Path) -> None:
    provider = FixtureHardwareProvider(t480s_baseline_fixture)
    snapshot = provider.probe()
    engine = CompatibilityEngine(db=test_db)
    report = engine.evaluate(snapshot, target_macos="sequoia")
    plan = engine.generate_build_plan(report)

    resolver = DependencyResolver(db=test_db)
    dep_set = resolver.resolve(plan, variant=ArtifactVariant.RELEASE)

    dep_ids = [d.dependency_id for d in dep_set.resolved_dependencies]
    assert "opencore" in dep_ids
    assert "lilu" in dep_ids
    assert "whatevergreen" in dep_ids
    assert "virtualsmc" in dep_ids
    assert "applealc" in dep_ids
    assert "intelmausi" in dep_ids
    assert "itlwm" in dep_ids

    # Ordering check: Lilu must precede WhateverGreen, AppleALC, VirtualSMC
    assert dep_ids.index("lilu") < dep_ids.index("whatevergreen")
    assert dep_ids.index("lilu") < dep_ids.index("applealc")
    assert dep_ids.index("lilu") < dep_ids.index("virtualsmc")


def test_resolve_t480_with_mx150_does_not_select_nvidia_kext(test_db: Database, t480_mx150_fixture: Path) -> None:
    provider = FixtureHardwareProvider(t480_mx150_fixture)
    snapshot = provider.probe()
    engine = CompatibilityEngine(db=test_db)
    report = engine.evaluate(snapshot, target_macos="sequoia")
    plan = engine.generate_build_plan(report)

    resolver = DependencyResolver(db=test_db)
    dep_set = resolver.resolve(plan, variant=ArtifactVariant.RELEASE)

    dep_ids = [d.dependency_id for d in dep_set.resolved_dependencies]
    # MX150 is disabled via ACPI SSDT / boot args; no Nvidia driver should be resolved
    assert not any("nvidia" in d.lower() for d in dep_ids)
    assert not any("geforce" in d.lower() for d in dep_ids)


def test_resolve_t480_pm981_selects_nvmefix(test_db: Database, t480_pm981_fixture: Path) -> None:
    provider = FixtureHardwareProvider(t480_pm981_fixture)
    snapshot = provider.probe()
    engine = CompatibilityEngine(db=test_db)
    report = engine.evaluate(snapshot, target_macos="sequoia")
    plan = engine.generate_build_plan(report)

    resolver = DependencyResolver(db=test_db)
    dep_set = resolver.resolve(plan, variant=ArtifactVariant.RELEASE)

    dep_ids = [d.dependency_id for d in dep_set.resolved_dependencies]
    assert "nvmefix" in dep_ids
    assert dep_ids.index("lilu") < dep_ids.index("nvmefix")


def test_resolve_tahoe_leaves_audio_unresolved_with_warning(test_db: Database, t480s_baseline_fixture: Path) -> None:
    provider = FixtureHardwareProvider(t480s_baseline_fixture)
    snapshot = provider.probe()
    engine = CompatibilityEngine(db=test_db)
    report = engine.evaluate(snapshot, target_macos="tahoe")
    plan = engine.generate_build_plan(report)

    resolver = DependencyResolver(db=test_db)
    dep_set = resolver.resolve(plan, variant=ArtifactVariant.RELEASE)

    dep_ids = [d.dependency_id for d in dep_set.resolved_dependencies]
    # AppleALC should NOT be selected as solved on Tahoe
    assert "applealc" not in dep_ids
    assert any("Tahoe analogue audio" in u for u in dep_set.unresolved_requirements)
    assert any("AppleHDA" in w for w in dep_set.warnings)


def test_resolve_sonoma_selects_airportitlwm(test_db: Database, t480s_baseline_fixture: Path) -> None:
    provider = FixtureHardwareProvider(t480s_baseline_fixture)
    snapshot = provider.probe()
    engine = CompatibilityEngine(db=test_db)
    report = engine.evaluate(snapshot, target_macos="sonoma")
    plan = engine.generate_build_plan(report)

    resolver = DependencyResolver(db=test_db)
    dep_set = resolver.resolve(plan, variant=ArtifactVariant.RELEASE)

    dep_ids = [d.dependency_id for d in dep_set.resolved_dependencies]
    assert "airportitlwm" in dep_ids


def test_resolve_rejects_blocked_plan(test_db: Database) -> None:
    plan = BuildPlan(
        target_model="Unknown", target_macos="sequoia", hardware_snapshot_id="unknown",
        support_state=CompatibilityState.BLOCKED,
    )
    with pytest.raises(DependencyNotFoundError, match="not actionable"):
        DependencyResolver(db=test_db).resolve(plan)
