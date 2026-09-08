"""Unit tests for S15: maintainability, unused imports, and canonical digest consolidation.

Covers:
1. Canonical JSON digest determinism, sorting, and whitespace elimination.
2. BuildPlan canonical digest stability, round-trip serialization, and audit field exclusion.
3. ResolvedDependencySet canonical digest stability and audit field exclusion.
4. DependencyResolver catalog digest determinism.
5. Backward compatibility of `_digest` alias in `macloader.domain.contracts`.
"""

import hashlib
import json
from macloader.database.loader import Database
from macloader.dependencies.resolver import DependencyResolver
from macloader.domain.build_plan import BuildPlan
from macloader.domain.compatibility import CompatibilityState
from macloader.domain.contracts import (
    ArtifactLock,
    ArtifactLockEntry,
    CONTRACT_SCHEMA_VERSION,
    ToolchainSelection,
    _digest,
    canonical_json_digest,
)
from macloader.domain.dependencies import (
    ArtifactVariant,
    DependencyArtifact,
    ResolvedDependency,
    ResolvedDependencySet,
)


def test_canonical_json_digest_determinism() -> None:
    """Keys in different insertion order produce identical digest output."""
    dict_a = {"z": 1, "a": 2, "m": {"b": 3, "a": 4}}
    dict_b = {"a": 2, "m": {"a": 4, "b": 3}, "z": 1}

    digest_a = canonical_json_digest(dict_a)
    digest_b = canonical_json_digest(dict_b)

    assert digest_a == digest_b
    # Digest must match manual hashlib calculation with compact JSON
    expected = hashlib.sha256(json.dumps(dict_a, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    assert digest_a == expected
    # Assert against literal precomputed golden hash to catch parameter drift
    assert digest_a == "e45c30b44d3d323ad520c1705dc1debea784800c25887d83cc182f4895836442"


def test_digest_alias_backwards_compatible() -> None:
    """_digest alias remains identical to canonical_json_digest."""
    sample = {"key": "value", "numbers": [1, 2, 3]}
    assert _digest(sample) == canonical_json_digest(sample)


def test_build_plan_canonical_digest_stability() -> None:
    """BuildPlan.canonical_digest is stable across different timestamps and plan IDs."""
    plan1 = BuildPlan(
        target_model="Lenovo ThinkPad T480s",
        target_macos="sequoia",
        hardware_snapshot_id="snap-1234",
        support_state=CompatibilityState.EXPERIMENTAL,
        required_capabilities=["alc257_audio", "intel_gigabit_ethernet"],
        planned_components=[{"category": "audio", "name": "Realtek ALC257"}],
        unresolved_requirements=["Unresolved A"],
        warnings=["Warning B"],
        plan_id="id-1",
        timestamp="2026-01-01T00:00:00Z",
    )

    plan2 = BuildPlan(
        target_model="Lenovo ThinkPad T480s",
        target_macos="sequoia",
        hardware_snapshot_id="snap-1234",
        support_state=CompatibilityState.EXPERIMENTAL,
        required_capabilities=["alc257_audio", "intel_gigabit_ethernet"],
        planned_components=[{"category": "audio", "name": "Realtek ALC257"}],
        unresolved_requirements=["Unresolved A"],
        warnings=["Warning B"],
        plan_id="id-2",
        timestamp="2026-09-08T22:00:00Z",
    )

    assert plan1.canonical_digest() == plan2.canonical_digest()

    # Round-trip serialization preserves canonical digest
    restored = BuildPlan.from_dict(plan1.to_dict())
    assert restored.canonical_digest() == plan1.canonical_digest()


def test_resolved_dependency_set_canonical_digest_stability() -> None:
    """ResolvedDependencySet.canonical_digest is stable across timestamps."""
    artifact = DependencyArtifact(
        variant=ArtifactVariant.RELEASE,
        asset_name="Lilu-1.7.2-RELEASE.zip",
        source_url="https://github.com/acidanthera/Lilu/releases/download/1.7.2/Lilu-1.7.2-RELEASE.zip",
        sha256="0" * 64,
        size_bytes=1024,
    )
    dep = ResolvedDependency(
        dependency_id="lilu",
        project_name="Lilu",
        version="1.7.2",
        variant=ArtifactVariant.RELEASE,
        artifact=artifact,
        reason="Required patcher",
        required_by="system",
    )

    set1 = ResolvedDependencySet(
        target_model="ThinkPad T480s",
        target_macos="sequoia",
        policy_version="2026.08-a",
        plan_digest="plan-abc",
        catalog_digest="cat-xyz",
        variant=ArtifactVariant.RELEASE,
        resolved_dependencies=[dep],
        unresolved_requirements=[],
        warnings=[],
        timestamp="2026-01-01T00:00:00Z",
    )

    set2 = ResolvedDependencySet(
        target_model="ThinkPad T480s",
        target_macos="sequoia",
        policy_version="2026.08-a",
        plan_digest="plan-abc",
        catalog_digest="cat-xyz",
        variant=ArtifactVariant.RELEASE,
        resolved_dependencies=[dep],
        unresolved_requirements=[],
        warnings=[],
        timestamp="2026-09-08T22:00:00Z",
    )

    assert set1.canonical_digest() == set2.canonical_digest()

    # Round-trip serialization preserves canonical digest
    restored = ResolvedDependencySet.from_dict(set1.to_dict())
    assert restored.canonical_digest() == set1.canonical_digest()


def test_dependency_resolver_catalog_digest_deterministic(db: Database) -> None:
    """DependencyResolver.catalog_digest produces identical digest on repeat calls."""
    resolver = DependencyResolver(db=db)
    digest1 = resolver.catalog_digest()
    digest2 = resolver.catalog_digest()
    assert digest1 == digest2
    assert len(digest1) == 64


def test_contract_properties_digest_consistency() -> None:
    """ArtifactLock.digest and ToolchainSelection.digest match canonical_json_digest."""
    entry = ArtifactLockEntry(
        dependency_id="lilu",
        version="1.7.2",
        variant="RELEASE",
        asset_name="Lilu-1.7.2-RELEASE.zip",
        source_url="https://example.com/lilu.zip",
        sha256="a" * 64,
        size_bytes=1024,
    )
    lock = ArtifactLock(
        schema_version=CONTRACT_SCHEMA_VERSION,
        policy_version="2026.08-a",
        catalog_digest="cat-digest",
        entries=(entry,),
        plan_digest="plan-digest",
    )
    assert lock.digest == canonical_json_digest(lock.to_dict())

    toolchain = ToolchainSelection(
        schema_version=CONTRACT_SCHEMA_VERSION,
        opencore_version="1.0.7",
        ocvalidate_version="1.0.7",
        acpi_compiler=None,
        identity_tool=None,
        recovery_tool=None,
        host_platform="windows",
        host_architecture="x86_64",
    )
    assert toolchain.digest == canonical_json_digest(toolchain.to_dict())

