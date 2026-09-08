"""Fixture-driven EFI construction and validation tests."""

from pathlib import Path
import copy
import hashlib
import zipfile
import pytest

from macloader.build.efi import EfiBuilder
from macloader.database.loader import Database
from macloader.dependencies.resolver import DependencyResolver
from macloader.domain.build_plan import BuildPlan
from macloader.domain.compatibility import CompatibilityState
from macloader.domain.contracts import CONTRACT_SCHEMA_VERSION, ToolchainSelection
from macloader.domain.dependencies import ArtifactVariant
from macloader.exceptions import BuildPlanError


def test_builder_extracts_selected_components_and_publishes_validated_tree(tmp_path: Path) -> None:
    db = Database()
    archives = {}
    for dependency_id in ("opencore", "lilu", "virtualsmc"):
        archive = tmp_path / f"{dependency_id}.zip"
        spec = db.get_dependency_spec(dependency_id)
        assert spec is not None
        with zipfile.ZipFile(archive, "w") as zf:
            for component in spec.subcomponents:
                name = f"Release/{component}"
                if component.endswith(".kext"):
                    name += "/Contents/Info.plist"
                zf.writestr(name, b"fixture")
        catalog_artifact = spec.get_artifact(ArtifactVariant.RELEASE)
        assert catalog_artifact is not None
        catalog_artifact.asset_name = archive.name
        catalog_artifact.sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
        catalog_artifact.size_bytes = archive.stat().st_size
        archives[dependency_id] = archive

    catalog = db.get_dependency_catalog()
    assert catalog is not None
    plan = BuildPlan("Lenovo ThinkPad T480s", "sequoia", "fixture", CompatibilityState.EXPERIMENTAL, policy_version=catalog.policy_version)
    resolver = DependencyResolver(db)
    dep_set = resolver.resolve(plan, ArtifactVariant.RELEASE)
    toolchain = ToolchainSelection(CONTRACT_SCHEMA_VERSION, "1.0.7", "1.0.7", None, None, None, "windows", "x86_64")

    result = EfiBuilder(db=db).build(plan, dep_set, archives, tmp_path / "output", fake_identity={"SystemProductName": "MacBookPro15,2"}, toolchain=toolchain)
    assert result.validation.status == "VALID"
    assert (result.output_dir / "EFI/BOOT/BOOTx64.efi").is_file()
    assert (result.output_dir / "EFI/OC/OpenCore.efi").is_file()
    assert (result.output_dir / "EFI/OC/Tools/OpenShell.efi").is_file()

    mismatched_plan = BuildPlan("Lenovo ThinkPad T480s", "sequoia", "different-snapshot", CompatibilityState.EXPERIMENTAL, is_actionable=True, build_ready=True)
    with pytest.raises(BuildPlanError, match="different BuildPlan"):
        EfiBuilder(db=db).build(plan=mismatched_plan, dependencies=dep_set, artifact_paths=archives, output_dir=tmp_path / "mismatch", toolchain=toolchain)
