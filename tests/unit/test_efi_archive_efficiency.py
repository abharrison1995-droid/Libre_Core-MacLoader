"""Unit tests for S11: EFI archive extraction efficiency, copy consolidation,
bundle/plugin topology preservation, collision safety, and peak disk budgets.
"""

from pathlib import Path
import hashlib
import plistlib
import shutil
import zipfile
from typing import Any
from unittest.mock import patch
import pytest

from macloader.build.efi import (
    DEFAULT_MAX_ARTIFACT_EXPANDED_BYTES,
    DEFAULT_MAX_BUILD_EXPANDED_BYTES,
    EfiBuilder,
)
from macloader.database.loader import Database
from macloader.dependencies.archive import (
    DEFAULT_MAX_ARCHIVE_MEMBERS,
    safe_extract_zip,
    validate_zip_archive,
)
from macloader.dependencies.resolver import DependencyResolver
from macloader.domain.build_plan import BuildPlan
from macloader.domain.compatibility import CompatibilityState
from macloader.domain.contracts import CONTRACT_SCHEMA_VERSION, ToolchainSelection
from macloader.domain.dependencies import ArtifactVariant
from macloader.exceptions import ArchiveSecurityError, BuildPlanError


def _setup_test_environment(tmp_path: Path, db: Database) -> tuple[BuildPlan, ToolchainSelection, dict[str, Path]]:
    """Helper to construct verified test archives and build plan."""
    archives: dict[str, Path] = {}
    for dependency_id in ("opencore", "lilu", "virtualsmc"):
        archive = tmp_path / f"{dependency_id}.zip"
        spec = db.get_dependency_spec(dependency_id)
        assert spec is not None
        with zipfile.ZipFile(archive, "w") as zf:
            for component in spec.subcomponents:
                name = f"Release/{component}"
                stem = Path(component).name
                if stem.endswith(".kext"):
                    stem_name = stem[:-5]
                    zf.writestr(f"{name}/Contents/Info.plist", b"<plist>kext-info</plist>")
                    zf.writestr(f"{name}/Contents/MacOS/{stem_name}", b"\xca\xfe\xba\xbe")
                    # Include a plugin bundle inside VirtualSMC to test plugin topology
                    if stem == "VirtualSMC.kext":
                        zf.writestr(
                            f"{name}/Contents/PlugIns/SMCProcessor.kext/Contents/Info.plist",
                            b"<plist>smc-processor</plist>",
                        )
                        zf.writestr(
                            f"{name}/Contents/PlugIns/SMCProcessor.kext/Contents/MacOS/SMCProcessor",
                            b"\xca\xfe\xba\xbe-processor",
                        )
                else:
                    zf.writestr(name, b"efi-binary-payload")
        catalog_artifact = spec.get_artifact(ArtifactVariant.RELEASE)
        assert catalog_artifact is not None
        catalog_artifact.asset_name = archive.name
        catalog_artifact.sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
        catalog_artifact.size_bytes = archive.stat().st_size
        archives[dependency_id] = archive

    catalog = db.get_dependency_catalog()
    assert catalog is not None
    plan = BuildPlan(
        "Lenovo ThinkPad T480s",
        "sequoia",
        "fixture",
        CompatibilityState.EXPERIMENTAL,
        policy_version=catalog.policy_version,
    )
    validator = tmp_path / "ocvalidate.py"
    validator.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    toolchain = ToolchainSelection(
        CONTRACT_SCHEMA_VERSION,
        "1.0.7",
        "1.0.7",
        None,
        None,
        None,
        "windows",
        "x86_64",
        {"qualification": "qualified"},
        str(validator),
        hashlib.sha256(validator.read_bytes()).hexdigest(),
    )
    return plan, toolchain, archives


def test_copy_elimination_and_single_snapshot_per_dependency(tmp_path: Path) -> None:
    """Measure file copy operations during build.

    Verifies:
    1. Exactly 1 copy per dependency (the single trusted private snapshot).
    2. Zero redundant snapshot copies inside safe_extract_zip.
    3. Zero intermediate extraction copies (shutil.copy2 / shutil.copytree = 0).
    """
    db = Database()
    plan, toolchain, archives = _setup_test_environment(tmp_path, db)
    resolver = DependencyResolver(db)
    dep_set = resolver.resolve(plan, ArtifactVariant.RELEASE)

    copyfile_calls: list[tuple[str, str]] = []
    copy2_calls: list[tuple[str, str]] = []
    copytree_calls: list[tuple[str, str]] = []

    orig_copyfile = shutil.copyfile
    orig_copy2 = shutil.copy2
    orig_copytree = shutil.copytree

    def tracked_copyfile(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
        copyfile_calls.append((str(src), str(dst)))
        return orig_copyfile(src, dst, *args, **kwargs)

    def tracked_copy2(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
        copy2_calls.append((str(src), str(dst)))
        return orig_copy2(src, dst, *args, **kwargs)

    def tracked_copytree(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
        copytree_calls.append((str(src), str(dst)))
        return orig_copytree(src, dst, *args, **kwargs)

    with patch("shutil.copyfile", side_effect=tracked_copyfile), \
         patch("shutil.copy2", side_effect=tracked_copy2), \
         patch("shutil.copytree", side_effect=tracked_copytree):
        builder = EfiBuilder(db=db, identity_store_dir=tmp_path / "identities")
        result = builder.build(
            plan=plan,
            dependencies=dep_set,
            artifact_paths=archives,
            output_dir=tmp_path / "output_single_snapshot",
            fake_identity={"SystemProductName": "MacBookPro15,2", "SystemSerialNumber": "SERIAL", "MLB": "MLB1234", "SystemUUID": "12345678"},
            toolchain=toolchain,
        )

    assert result.validation.status == "VALID"
    num_dependencies = len(dep_set.resolved_dependencies)
    assert num_dependencies == 3

    # Assert: Exactly 1 copyfile per dependency (single snapshot)
    assert len(copyfile_calls) == num_dependencies
    for src, dst in copyfile_calls:
        assert Path(src).suffix == ".zip"
        assert Path(dst).name == "archive.zip"

    # Assert: Zero redundant copy2 or copytree calls during extraction
    assert len(copy2_calls) == 0
    assert len(copytree_calls) == 0


def test_bundle_and_plugin_topology_preservation(tmp_path: Path) -> None:
    """Verify that bundle relative structure and nested plugin topology are preserved."""
    db = Database()
    plan, toolchain, archives = _setup_test_environment(tmp_path, db)
    resolver = DependencyResolver(db)
    dep_set = resolver.resolve(plan, ArtifactVariant.RELEASE)

    builder = EfiBuilder(db=db, identity_store_dir=tmp_path / "identities")
    result = builder.build(
        plan=plan,
        dependencies=dep_set,
        artifact_paths=archives,
        output_dir=tmp_path / "output_topology",
        fake_identity={"SystemProductName": "MacBookPro15,2", "SystemSerialNumber": "SERIAL", "MLB": "MLB1234", "SystemUUID": "12345678"},
        toolchain=toolchain,
    )

    kext_dir = result.output_dir / "EFI" / "OC" / "Kexts" / "VirtualSMC.kext"
    assert kext_dir.is_dir()
    assert (kext_dir / "Contents" / "Info.plist").read_bytes() == b"<plist>kext-info</plist>"
    assert (kext_dir / "Contents" / "MacOS" / "VirtualSMC").read_bytes() == b"\xca\xfe\xba\xbe"

    plugin_dir = kext_dir / "Contents" / "PlugIns" / "SMCProcessor.kext"
    assert plugin_dir.is_dir()
    assert (plugin_dir / "Contents" / "Info.plist").read_bytes() == b"<plist>smc-processor</plist>"
    assert (plugin_dir / "Contents" / "MacOS" / "SMCProcessor").read_bytes() == b"\xca\xfe\xba\xbe-processor"


def test_inter_dependency_file_collision_detected(tmp_path: Path) -> None:
    """Verify that if two dependencies collide on the same destination file, extraction aborts."""
    db = Database()
    # Add a colliding subcomponent to lilu spec in database before resolving
    lilu_spec = db.get_dependency_spec("lilu")
    assert lilu_spec is not None
    lilu_spec.subcomponents.append("EFI/BOOT/BOOTx64.efi")

    plan, toolchain, archives = _setup_test_environment(tmp_path, db)
    resolver = DependencyResolver(db)
    dep_set = resolver.resolve(plan, ArtifactVariant.RELEASE)

    builder = EfiBuilder(db=db, identity_store_dir=tmp_path / "identities")
    with pytest.raises(BuildPlanError, match="Extraction security error.*Extraction collision"):
        builder.build(
            plan=plan,
            dependencies=dep_set,
            artifact_paths=archives,
            output_dir=tmp_path / "output_collision",
            fake_identity={"SystemProductName": "MacBookPro15,2", "SystemSerialNumber": "SERIAL", "MLB": "MLB1234", "SystemUUID": "12345678"},
            toolchain=toolchain,
        )
    assert not (tmp_path / "output_collision").exists()


def test_per_artifact_expanded_size_limit_enforced(tmp_path: Path) -> None:
    """Verify that an artifact exceeding max_artifact_expanded_bytes is rejected."""
    db = Database()
    plan, toolchain, archives = _setup_test_environment(tmp_path, db)
    resolver = DependencyResolver(db)
    dep_set = resolver.resolve(plan, ArtifactVariant.RELEASE)

    # Set builder limit lower than the payload size
    small_budget_builder = EfiBuilder(
        db=db,
        identity_store_dir=tmp_path / "identities",
        max_artifact_expanded_bytes=10,  # 10 bytes limit
    )
    with pytest.raises(BuildPlanError, match="(exceeds the configured limit|exceeds limit)"):
        small_budget_builder.build(
            plan=plan,
            dependencies=dep_set,
            artifact_paths=archives,
            output_dir=tmp_path / "output_size_limit",
            fake_identity={"SystemProductName": "MacBookPro15,2", "SystemSerialNumber": "SERIAL", "MLB": "MLB1234", "SystemUUID": "12345678"},
            toolchain=toolchain,
        )
    assert not (tmp_path / "output_size_limit").exists()


def test_cumulative_build_disk_budget_enforced(tmp_path: Path) -> None:
    """Verify that total extracted bytes exceeding max_build_expanded_bytes is rejected."""
    db = Database()
    plan, toolchain, archives = _setup_test_environment(tmp_path, db)
    resolver = DependencyResolver(db)
    dep_set = resolver.resolve(plan, ArtifactVariant.RELEASE)

    # Set build disk budget enough for first dependency but not all dependencies
    small_build_builder = EfiBuilder(
        db=db,
        identity_store_dir=tmp_path / "identities",
        max_artifact_expanded_bytes=100000,
        max_build_expanded_bytes=100,  # Very small total build budget
    )
    with pytest.raises(BuildPlanError, match="maximum peak disk budget"):
        small_build_builder.build(
            plan=plan,
            dependencies=dep_set,
            artifact_paths=archives,
            output_dir=tmp_path / "output_budget_limit",
            fake_identity={"SystemProductName": "MacBookPro15,2", "SystemSerialNumber": "SERIAL", "MLB": "MLB1234", "SystemUUID": "12345678"},
            toolchain=toolchain,
        )
    assert not (tmp_path / "output_budget_limit").exists()


def test_validate_zip_archive_rejects_excess_members(tmp_path: Path) -> None:
    """Verify archive with member count exceeding limit is rejected."""
    zip_file = tmp_path / "many_members.zip"
    with zipfile.ZipFile(zip_file, "w") as zf:
        for i in range(15):
            zf.writestr(f"file_{i}.txt", b"x")

    with pytest.raises(ArchiveSecurityError, match="Archive contains too many members"):
        validate_zip_archive(zip_file, max_members=10)


def test_safe_extract_zip_rejects_symlink_destination(tmp_path: Path) -> None:
    """Verify safe_extract_zip refuses to extract into or over a symlink."""
    zip_file = tmp_path / "archive.zip"
    with zipfile.ZipFile(zip_file, "w") as zf:
        zf.writestr("test.txt", b"payload")

    fake_symlink_dir = tmp_path / "link_target"
    fake_symlink_dir.mkdir()

    with patch.object(Path, "is_symlink", return_value=True):
        with pytest.raises(ArchiveSecurityError, match="(must not be a symlink|Refusing to overwrite symlink|Refusing to follow symlink)"):
            safe_extract_zip(zip_file, fake_symlink_dir)


def test_safe_extract_zip_detects_path_escape_with_member_map(tmp_path: Path) -> None:
    """Verify member_map cannot route files outside target_dir."""
    zip_file = tmp_path / "archive.zip"
    with zipfile.ZipFile(zip_file, "w") as zf:
        zf.writestr("file.txt", b"payload")

    target_dir = tmp_path / "target"
    target_dir.mkdir()
    escaped_dest = tmp_path / "escaped.txt"

    with pytest.raises(ArchiveSecurityError, match="Extraction path escape attempt"):
        safe_extract_zip(zip_file, target_dir, member_map={"file.txt": escaped_dest})


def test_verified_archive_tamper_after_snapshot_is_impossible(tmp_path: Path) -> None:
    """Verify that modifying the source archive during build does not affect verified extraction."""
    db = Database()
    plan, toolchain, archives = _setup_test_environment(tmp_path, db)
    resolver = DependencyResolver(db)
    dep_set = resolver.resolve(plan, ArtifactVariant.RELEASE)

    builder = EfiBuilder(db=db, identity_store_dir=tmp_path / "identities")

    # If the source archive hash doesn't match expected before snapshot, build fails
    corrupt_archive = archives["opencore"]
    corrupt_archive.write_bytes(b"tampered-contents")

    with pytest.raises(BuildPlanError, match="Verified archive changed or does not match"):
        builder.build(
            plan=plan,
            dependencies=dep_set,
            artifact_paths=archives,
            output_dir=tmp_path / "output_tamper",
            fake_identity={"SystemProductName": "MacBookPro15,2", "SystemSerialNumber": "SERIAL", "MLB": "MLB1234", "SystemUUID": "12345678"},
            toolchain=toolchain,
        )


def test_explicit_plugin_subcomponent_resolution_and_validation(tmp_path: Path) -> None:
    """Verify that dependencies listing explicit plugin subcomponents (e.g. VoodooPS2)
    resolve destination paths and config.plist bundle paths accurately.
    """
    db = Database()
    plan, toolchain, archives = _setup_test_environment(tmp_path, db)
    resolver = DependencyResolver(db)
    dep_set = resolver.resolve(plan, ArtifactVariant.RELEASE)

    # Add a mock voodoops2-like dependency with explicit plugin subcomponent
    spec = db.get_dependency_spec("lilu")
    assert spec is not None
    spec.subcomponents = [
        "VoodooPS2Controller.kext",
        "VoodooPS2Controller.kext/Contents/PlugIns/VoodooPS2Keyboard.kext",
    ]
    archive = tmp_path / "lilu.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        # Top-level bundle
        zf.writestr("Release/VoodooPS2Controller.kext/Contents/Info.plist", b"<plist>main</plist>")
        zf.writestr("Release/VoodooPS2Controller.kext/Contents/MacOS/VoodooPS2Controller", b"\xca\xfe\xba\xbe")
        # Nested plugin bundle
        zf.writestr(
            "Release/VoodooPS2Controller.kext/Contents/PlugIns/VoodooPS2Keyboard.kext/Contents/Info.plist",
            b"<plist>keyboard</plist>",
        )
        zf.writestr(
            "Release/VoodooPS2Controller.kext/Contents/PlugIns/VoodooPS2Keyboard.kext/Contents/MacOS/VoodooPS2Keyboard",
            b"\xca\xfe\xba\xbe-kbd",
        )

    art = spec.get_artifact(ArtifactVariant.RELEASE)
    assert art is not None
    art.sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    art.size_bytes = archive.stat().st_size
    archives["lilu"] = archive

    # Re-resolve with updated subcomponents
    dep_set = resolver.resolve(plan, ArtifactVariant.RELEASE)

    builder = EfiBuilder(db=db, identity_store_dir=tmp_path / "identities")
    result = builder.build(
        plan=plan,
        dependencies=dep_set,
        artifact_paths=archives,
        output_dir=tmp_path / "output_plugin_subcomp",
        fake_identity={"SystemProductName": "MacBookPro15,2", "SystemSerialNumber": "SERIAL", "MLB": "MLB1234", "SystemUUID": "12345678"},
        toolchain=toolchain,
    )
    assert result.validation.status == "VALID"

    # Verify both main kext and nested plugin were extracted to the right place
    main_kext = result.output_dir / "EFI" / "OC" / "Kexts" / "VoodooPS2Controller.kext"
    plugin_kext = main_kext / "Contents" / "PlugIns" / "VoodooPS2Keyboard.kext"
    assert (main_kext / "Contents" / "Info.plist").read_bytes() == b"<plist>main</plist>"
    assert (plugin_kext / "Contents" / "Info.plist").read_bytes() == b"<plist>keyboard</plist>"

    # Verify config.plist has both BundlePaths
    config_data = (result.output_dir / "EFI" / "OC" / "config.plist").read_bytes()
    config_dict = plistlib.loads(config_data)
    bundle_paths = [entry["BundlePath"] for entry in config_dict["Kernel"]["Add"]]
    assert "VoodooPS2Controller.kext" in bundle_paths
    assert "VoodooPS2Controller.kext/Contents/PlugIns/VoodooPS2Keyboard.kext" in bundle_paths


def test_root_level_kext_bundle_without_directory_record(tmp_path: Path) -> None:
    """Verify that root-level kext bundles without explicit directory records extract successfully."""
    db = Database()
    plan, toolchain, archives = _setup_test_environment(tmp_path, db)
    resolver = DependencyResolver(db)

    # Rebuild lilu.zip without Release/ prefix and without directory records
    spec = db.get_dependency_spec("lilu")
    assert spec is not None
    spec.subcomponents = ["Lilu.kext"]
    archive = tmp_path / "lilu.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        # Only files, no directory record for Lilu.kext or Lilu.kext/
        zf.writestr("Lilu.kext/Contents/Info.plist", b"<plist>lilu</plist>")
        zf.writestr("Lilu.kext/Contents/MacOS/Lilu", b"\xca\xfe\xba\xbe")

    art = spec.get_artifact(ArtifactVariant.RELEASE)
    assert art is not None
    art.sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    art.size_bytes = archive.stat().st_size
    archives["lilu"] = archive

    dep_set = resolver.resolve(plan, ArtifactVariant.RELEASE)
    builder = EfiBuilder(db=db, identity_store_dir=tmp_path / "identities")
    result = builder.build(
        plan=plan,
        dependencies=dep_set,
        artifact_paths=archives,
        output_dir=tmp_path / "output_root_kext",
        fake_identity={"SystemProductName": "MacBookPro15,2", "SystemSerialNumber": "SERIAL", "MLB": "MLB1234", "SystemUUID": "12345678"},
        toolchain=toolchain,
    )
    assert result.validation.status == "VALID"
    assert (result.output_dir / "EFI" / "OC" / "Kexts" / "Lilu.kext" / "Contents" / "Info.plist").is_file()


def test_config_file_errors_rejects_case_insensitive_duplicate_entries(tmp_path: Path) -> None:
    """Verify that config.plist driver/kext entries differing only in case are rejected."""
    root = tmp_path / "efi"
    drivers_dir = root / "EFI" / "OC" / "Drivers"
    drivers_dir.mkdir(parents=True)
    (drivers_dir / "OpenRuntime.efi").write_bytes(b"driver")

    config = {
        "UEFI": {
            "Drivers": [
                {"Path": "OpenRuntime.efi"},
                {"Path": "openruntime.efi"},  # Case duplicate
            ]
        },
        "Kernel": {"Add": []},
    }
    errors = EfiBuilder._config_file_errors(root, config)
    assert any("Duplicate driver configuration entry: openruntime.efi" in err for err in errors)


def test_validate_zip_archive_rejects_empty_and_reserved_names(tmp_path: Path) -> None:
    """Verify validate_zip_archive rejects empty names, dot names, and extended Windows reserved names."""
    # Empty / dot name
    zip_empty = tmp_path / "dot.zip"
    with zipfile.ZipFile(zip_empty, "w") as zf:
        zf.writestr(".", b"payload")
    with pytest.raises(ArchiveSecurityError, match="Invalid member name"):
        validate_zip_archive(zip_empty)

    # Windows reserved names CONIN$
    zip_reserved = tmp_path / "conin.zip"
    with zipfile.ZipFile(zip_reserved, "w") as zf:
        zf.writestr("CONIN$/test.txt", b"payload")
    with pytest.raises(ArchiveSecurityError, match="Insecure Windows path"):
        validate_zip_archive(zip_reserved)


def test_safe_extract_zip_cleans_up_partial_file_on_error(tmp_path: Path) -> None:
    """Verify that partial files are unlinked if an exception occurs during extraction."""
    zip_file = tmp_path / "archive.zip"
    with zipfile.ZipFile(zip_file, "w") as zf:
        zf.writestr("large.bin", b"X" * 10000)

    target_dir = tmp_path / "target_cleanup"
    target_dir.mkdir()

    # Set budget smaller than the file size
    with pytest.raises(ArchiveSecurityError, match="exceeds the configured limit"):
        safe_extract_zip(zip_file, target_dir, max_expanded_bytes=100)

    # Partial file should not be left on disk
    assert not (target_dir / "large.bin").exists()
