"""Unit tests for S14: residual detection, policy deduplication, and schema/cache alignment.

Covers:
1. Unified CPU generation inference across Windows and Linux providers.
2. Stable deduplication of report warnings and unresolved requirements preserving distinct reasons.
3. Cache path safety for all schema-accepted artifact identifiers and version characters (including `~`).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from macloader.compatibility.engine import CompatibilityEngine, _deduplicate_preserve_order
from macloader.database.loader import Database
from macloader.database.schema import ComponentVersionPolicy, DatabaseValidationError, DependencyCatalogSchema
from macloader.dependencies.cache import CacheManager
from macloader.detection.linux import LinuxHardwareProvider
from macloader.detection.normalize import infer_cpu_generation
from macloader.detection.windows import WindowsHardwareProvider
from macloader.domain.compatibility import CompatibilityReport, CompatibilityState, SupportDecision
from macloader.domain.dependencies import ArtifactVariant, DependencyArtifact, DependencySpec
from macloader.domain.hardware import CpuInfo, GpuInfo, HardwareSnapshot, PciDevice
from macloader.exceptions import ChecksumMismatchError



# ---------------------------------------------------------------------------
# 1. Unified CPU generation inference & paired host consistency
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "model_str,expected_gen",
    [
        ("Intel(R) Core(TM) i7-8550U CPU @ 1.80GHz", "Kaby Lake Refresh"),
        ("Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz", "Kaby Lake Refresh"),
        ("Intel(R) Core(TM) i5-8350U CPU @ 1.70GHz", "Kaby Lake Refresh"),
        ("Intel(R) Core(TM) i7-8650U CPU @ 1.90GHz", "Kaby Lake Refresh"),
        ("Intel Core Processor (8th Gen)", "Kaby Lake Refresh"),
        ("Intel(R) Core(TM) i5-7200U CPU @ 2.50GHz", "Kaby Lake"),
        ("Intel(R) Core(TM) i5-7300U CPU @ 2.60GHz", "Kaby Lake"),
        ("Intel(R) Core(TM) i7-7500U CPU @ 2.70GHz", "Kaby Lake"),
        ("Intel(R) Core(TM) i7-7600U CPU @ 2.80GHz", "Kaby Lake"),
        ("Intel Core Processor (7th Gen)", "Kaby Lake"),
        ("Intel(R) Core(TM) i5-6200U CPU @ 2.30GHz", "Skylake"),
        ("Intel(R) Core(TM) i7-6500U CPU @ 2.50GHz", "Skylake"),
        ("Intel(R) Core(TM) i7-10510U CPU @ 1.80GHz", "Comet Lake"),
        ("Intel(R) Core(TM) i7-1065G7 CPU @ 1.30GHz", "Ice Lake"),
        ("AMD Ryzen 7 5800U", None),
        ("Unknown CPU", None),
        (None, None),
    ],
)
def test_infer_cpu_generation_consistency(model_str: str | None, expected_gen: str | None) -> None:
    """Windows and Linux providers share the identical CPU generation inference routine."""
    assert infer_cpu_generation(model_str) == expected_gen


def test_paired_windows_and_linux_classify_same_cpu_identically(tmp_path: Path) -> None:
    """A 7th-gen or 8th-gen processor parsed through either provider produces the same generation."""
    cases = [
        ("Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz", "Kaby Lake Refresh"),
        ("Intel(R) Core(TM) i7-8550U CPU @ 1.80GHz", "Kaby Lake Refresh"),
        ("Intel(R) Core(TM) i5-7200U CPU @ 2.50GHz", "Kaby Lake"),
        ("Intel(R) Core(TM) i7-7500U CPU @ 2.70GHz", "Kaby Lake"),
    ]
    for idx, (cpu_name, expected_gen) in enumerate(cases):
        # 1. Windows Hardware Provider probe
        def mock_runner(script: str, name: str = cpu_name) -> str:
            if "Win32_ComputerSystem" in script:
                return json.dumps({"Manufacturer": "LENOVO", "Model": "20L7CTO1WW"})
            elif "Win32_Processor" in script:
                return json.dumps({"Name": name, "NumberOfCores": 4, "NumberOfLogicalProcessors": 8})
            return ""

        win_prov = WindowsHardwareProvider(command_runner=mock_runner)
        win_snap = win_prov.probe()

        # 2. Linux Hardware Provider probe
        sys_root = tmp_path / f"sys_{idx}"
        proc_root = tmp_path / f"proc_{idx}"
        proc_root.mkdir(parents=True, exist_ok=True)
        (proc_root / "cpuinfo").write_text(
            f"processor\t: 0\nvendor_id\t: GenuineIntel\nmodel name\t: {cpu_name}\ncpu cores\t: 4\n"
        )
        linux_prov = LinuxHardwareProvider(sys_root=str(sys_root), proc_root=str(proc_root))
        linux_snap = linux_prov.probe()

        assert win_snap.cpu is not None
        assert linux_snap.cpu is not None
        assert win_snap.cpu.generation == expected_gen
        assert linux_snap.cpu.generation == expected_gen
        assert win_snap.cpu.generation == linux_snap.cpu.generation


# ---------------------------------------------------------------------------
# 2. Report deduplication preserving distinct reasons
# ---------------------------------------------------------------------------

def test_deduplicate_preserve_order_function() -> None:
    raw = ["reason A", "reason B", "reason A", "reason C", "reason B", "reason D"]
    deduped = _deduplicate_preserve_order(raw)
    assert deduped == ["reason A", "reason B", "reason C", "reason D"]


def test_compatibility_report_deduplicates_warnings_and_unresolved_requirements(db: Database) -> None:
    """When multiple components emit identical warnings or requirements, the report deduplicates them."""
    engine = CompatibilityEngine(db=db)

    # Construct snapshot with components that would emit repeated limitations/requirements
    snapshot = HardwareSnapshot(
        manufacturer="LENOVO",
        product_name="20L7CTO1WW",
        product_version="ThinkPad T480s",
        machine_type="20L7",
        cpu=CpuInfo(model_name="Intel Core i7-8550U", generation="Kaby Lake Refresh"),
        igpu=GpuInfo(name="Intel UHD Graphics 620", pci=PciDevice(vendor_id="8086", device_id="5917"), is_igpu=True),
        raw_evidence={"inventory_status": {"audio": True, "ethernet": True, "wifi": True, "storage": True, "input": True}},
    )

    # Artificially inject duplicate warnings via monkeypatch
    dummy_comp = MagicMock()
    dummy_comp.id = "test-comp"
    dummy_comp.macos_policies = {
        "sequoia": ComponentVersionPolicy(
            state=CompatibilityState.EXPERIMENTAL,
            reason="Test reason",
            unresolved_requirements=["Unresolved requirement 1", "Unresolved requirement 1"],
            known_limitations=["Duplicate warning A", "Duplicate warning A", "Distinct warning B"],
        )
    }
    with patch.object(engine, "_match_component_by_pci", return_value=dummy_comp), \
         patch.object(db, "get_component", return_value=dummy_comp):
        report = engine.evaluate(snapshot, target_macos="sequoia")

        # Warnings must be deduplicated in exact encounter order
        assert report.warnings == ["Duplicate warning A", "Distinct warning B"]

        # Unresolved requirements must be deduplicated in exact encounter order
        assert report.unresolved_requirements == ["Unresolved requirement 1"]

        # Build plan generation must also maintain deduplicated unresolved requirements
        plan = engine.generate_build_plan(report)
        assert plan.unresolved_requirements.count("Unresolved requirement 1") == 1



# ---------------------------------------------------------------------------
# 3. Schema & Cache version-character agreement
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "version_str",
    [
        "1.0.0",
        "1.0.0-rc1",
        "1.0.0+build123",
        "1.0.0~beta2",          # ~ accepted by schema and cache
        "v2_0_4",
        "0.9.8.7",
        "alpha.1+2024.01",
    ],
)
def test_schema_and_cache_accept_valid_version_strings(tmp_path: Path, version_str: str) -> None:
    """Every version accepted by schema.py must produce a safe cache path in cache.py."""
    # 1. Verify schema accepts this version in catalog definition
    catalog_data = {
        "macloader_dependency_set": "2024.1",
        "dependencies": [
            {
                "id": "test-kext",
                "project_name": "Test Kext",
                "upstream_repository": "https://github.com/acidanthera/TestKext",
                "license": "BSD-3-Clause",
                "version": version_str,
                "release_tag": version_str,
                "artifacts": {
                    "RELEASE": {
                            "asset_name": "Test.zip",
                        "source_url": f"https://github.com/acidanthera/TestKext/releases/download/{version_str}/Test.zip",
                        "sha256": "0" * 64,
                        "size_bytes": 1024,
                        "archive_type": "zip",
                    }
                },
            }
        ],
    }
    validated = DependencyCatalogSchema.validate_and_load(catalog_data, "test.yaml")
    assert validated.dependencies["test-kext"].version == version_str

    # 2. Verify cache produces a safe path without throwing ChecksumMismatchError
    cache = CacheManager(cache_dir=tmp_path)
    spec = DependencySpec(
        id="test-kext",
        project_name="Test Kext",
        upstream_repository="https://github.com/acidanthera/TestKext",
        license="BSD-3-Clause",
        version=version_str,
        release_tag=version_str,
        artifacts={
            ArtifactVariant.RELEASE: DependencyArtifact(
                variant=ArtifactVariant.RELEASE,
                asset_name="TestKext-RELEASE.zip",
                source_url=f"https://github.com/acidanthera/TestKext/releases/download/{version_str}/Test.zip",
                sha256="a" * 64,
                size_bytes=1024,
                archive_type="zip",
            )
        },
    )
    cache_path = cache.get_artifact_cache_path(spec, ArtifactVariant.RELEASE)
    assert cache_path.is_relative_to(cache.downloads_dir)
    assert version_str in cache_path.name

    # 3. Verify lock file naming also works cleanly
    lock_file = cache.get_artifact_lock_file(spec, ArtifactVariant.RELEASE)
    assert lock_file.parent == cache.cache_dir


@pytest.mark.parametrize(
    "invalid_version",
    [
        "../traversal",
        "1.0/slash",
        "1.0\\backslash",
        "1.0:colon",
        "1.0\x00nullbyte",
        "~1.0.0",
        ".1.0.0",
        "1.0..0",
        "",
        " ",
    ],
)
def test_cache_and_schema_reject_unsafe_versions(tmp_path: Path, invalid_version: str) -> None:
    """Unsafe versions (path traversal, slashes, null bytes, invalid starts) are rejected by both schema and cache."""
    # 1. Reject in cache
    cache = CacheManager(cache_dir=tmp_path)
    spec = DependencySpec(
        id="test-kext",
        project_name="Test",
        upstream_repository="https://github.com/acidanthera/Test",
        license="BSD",
        version=invalid_version,
        release_tag="1.0.0",
    )
    with pytest.raises(ChecksumMismatchError, match="Unsafe version"):
        cache.get_artifact_cache_path(spec)

    # 2. Reject in schema
    catalog_data = {
        "macloader_dependency_set": "2024.1",
        "dependencies": [
            {
                "id": "test-kext",
                "project_name": "Test",
                "upstream_repository": "https://github.com/acidanthera/Test",
                "license": "BSD-3-Clause",
                "version": invalid_version,
                "release_tag": "1.0.0",
                "artifacts": {
                    "RELEASE": {
                        "asset_name": "test.zip",
                        "source_url": "https://github.com/acidanthera/Test/releases/download/1.0.0/test.zip",
                        "sha256": "0" * 64,
                        "size_bytes": 1024,
                    }
                },
            }
        ],
    }
    with pytest.raises(DatabaseValidationError):
        DependencyCatalogSchema.validate_and_load(catalog_data, "test.yaml")


def test_cache_rejects_unsafe_spec_id(tmp_path: Path) -> None:
    """Unsafe dependency IDs (such as path traversal) are rejected by cache."""
    cache = CacheManager(cache_dir=tmp_path)
    spec = DependencySpec(
        id="../traversal",
        project_name="Test",
        upstream_repository="https://github.com/acidanthera/Test",
        license="BSD",
        version="1.0.0",
        release_tag="1.0.0",
    )
    with pytest.raises(ChecksumMismatchError, match="Unsafe dependency ID"):
        cache.get_artifact_cache_path(spec)


def test_infer_cpu_generation_defensive_types() -> None:
    """Non-string or whitespace-only CPU models safely return None without crashing."""
    assert infer_cpu_generation("") is None
    assert infer_cpu_generation("   ") is None
    # mypy ignore to test runtime resilience
    assert infer_cpu_generation(12345) is None  # type: ignore[arg-type]
    assert infer_cpu_generation({"cpu": "i7"}) is None  # type: ignore[arg-type]


def test_deduplicate_preserve_order_defensive() -> None:
    """Deduplication safely handles None, empty collections, and unhashable elements."""
    assert _deduplicate_preserve_order(None) == []
    assert _deduplicate_preserve_order([]) == []
    # Test handling of unhashables
    assert _deduplicate_preserve_order([{"a": 1}, {"a": 1}, "b"]) == ["{'a': 1}", "b"]  # type: ignore[list-item]

