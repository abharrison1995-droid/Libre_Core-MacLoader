"""Fixture-driven EFI construction and validation tests."""

from pathlib import Path
import hashlib
import zipfile

from macloader.build.efi import EfiBuilder
from macloader.domain.build_plan import BuildPlan
from macloader.domain.compatibility import CompatibilityState
from macloader.domain.dependencies import ArtifactVariant, DependencyArtifact, ResolvedDependency, ResolvedDependencySet


def test_builder_extracts_selected_components_and_publishes_validated_tree(tmp_path: Path) -> None:
    archive = tmp_path / "opencore.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name in (
            "Release/EFI/BOOT/BOOTx64.efi",
            "Release/EFI/OC/OpenCore.efi",
            "Release/EFI/OC/Drivers/OpenRuntime.efi",
            "Release/Lilu.kext/Contents/Info.plist",
        ):
            zf.writestr(name, b"fixture")

    artifact = DependencyArtifact("opencore.zip", "https://example.test/opencore.zip", hashlib.sha256(archive.read_bytes()).hexdigest(), archive.stat().st_size, ArtifactVariant.RELEASE)
    dep = ResolvedDependency("opencore", "OpenCorePkg", "1.0.7", ArtifactVariant.RELEASE, artifact, "core", "test", subcomponents=[
        "EFI/BOOT/BOOTx64.efi", "EFI/OC/OpenCore.efi", "EFI/OC/Drivers/OpenRuntime.efi", "Lilu.kext",
    ])
    dep_set = ResolvedDependencySet("Lenovo ThinkPad T480s", "sequoia", "test", ArtifactVariant.RELEASE, catalog_digest="0" * 64, resolved_dependencies=[dep], is_complete=True)
    plan = BuildPlan("Lenovo ThinkPad T480s", "sequoia", "fixture", CompatibilityState.EXPERIMENTAL, is_actionable=True, build_ready=True)

    result = EfiBuilder().build(plan, dep_set, {"opencore": archive}, tmp_path / "output", fake_identity={"SystemProductName": "MacBookPro15,2"})
    assert result.validation.status == "VALID"
    assert (result.output_dir / "EFI/BOOT/BOOTx64.efi").is_file()
    assert (result.output_dir / "EFI/OC/OpenCore.efi").is_file()
    assert (result.output_dir / "EFI/OC/Kexts/Lilu.kext/Contents/Info.plist").is_file()
