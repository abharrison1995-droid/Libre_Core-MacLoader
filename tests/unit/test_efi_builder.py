"""Fixture-driven EFI construction and validation tests."""

from pathlib import Path
import hashlib
import json
import plistlib
import zipfile
import pytest

from macloader.build.efi import EfiBuilder
from macloader.build.config import SchemaDrivenConfigGenerator
from macloader.database.loader import Database
from macloader.dependencies.resolver import DependencyResolver
from macloader.domain.build_plan import BuildPlan
from macloader.domain.compatibility import CompatibilityState
from macloader.domain.contracts import BuildManifest, CONTRACT_SCHEMA_VERSION, ToolchainSelection
from macloader.domain.dependencies import ArtifactVariant
from macloader.exceptions import BuildPlanError
from macloader.identity.service import IdentityService


def _minimal_tree(root: Path) -> None:
    for relative in ("EFI/BOOT/BOOTx64.efi", "EFI/OC/OpenCore.efi", "EFI/OC/Drivers/OpenRuntime.efi"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    (root / "EFI/OC/config.plist").write_bytes(
        plistlib.dumps({"OC": {"Version": "1.0.7"}, "UEFI": {"Drivers": []}, "Kernel": {"Add": []}, "PlatformInfo": {"Generic": {"SystemProductName": "MacBookPro15,2", "SystemSerialNumber": "SERIAL", "MLB": "MLB1234", "SystemUUID": "12345678"}}})
    )


def test_structural_validation_is_not_release_valid_without_ocvalidate(tmp_path: Path) -> None:
    root = tmp_path / "efi"
    _minimal_tree(root)
    report = EfiBuilder().validate_tree(root)
    assert report.status == "STRUCTURAL_ONLY"
    assert report.validator_version is None


def test_ocvalidate_failure_and_config_file_mismatch_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "efi"
    _minimal_tree(root)
    config = {"UEFI": {"Drivers": [{"Path": "Missing.efi"}]}, "Kernel": {"Add": []}}
    (root / "EFI/OC/config.plist").write_bytes(plistlib.dumps(config))
    validator = tmp_path / "ocvalidate.py"
    validator.write_text("import sys\nsys.exit(2)\n", encoding="utf-8")
    toolchain = ToolchainSelection(CONTRACT_SCHEMA_VERSION, "1.0.7", "1.0.7", None, None, None, "windows", "x86_64", {"qualification": "qualified"}, str(validator), hashlib.sha256(validator.read_bytes()).hexdigest())

    report = EfiBuilder().validate_tree(root, toolchain=toolchain, synthetic_test_mode=True)
    assert report.status == "INVALID"
    assert any("Configured driver is missing" in error for error in report.errors)

    (root / "EFI/OC/config.plist").write_bytes(plistlib.dumps({"OC": {"Version": "1.0.7"}, "UEFI": {"Drivers": []}, "Kernel": {"Add": []}, "PlatformInfo": {"Generic": {"SystemProductName": "MacBookPro15,2", "SystemSerialNumber": "SERIAL", "MLB": "MLB1234", "SystemUUID": "12345678"}}}))
    builder = EfiBuilder()
    expected_manifest = BuildManifest(
        CONTRACT_SCHEMA_VERSION,
        "a" * 64,
        "Lenovo ThinkPad T480s",
        "sequoia",
        "b" * 64,
        "VALID",
        output_paths={"efi": "EFI"},
        toolchain_digest="c" * 64,
        identity_digest="d" * 64,
        output_digest=builder._tree_digest(root),
    )
    (root / "manifest.json").write_text(json.dumps(expected_manifest.to_dict()), encoding="utf-8")
    report = builder.validate_tree(root, toolchain=toolchain, expected_manifest=expected_manifest, synthetic_test_mode=True)
    assert report.status == "INVALID"
    assert report.checks["ocvalidate_exit"] == "2"


def test_qualified_validation_requires_a_trusted_expected_manifest(tmp_path: Path) -> None:
    root = tmp_path / "efi"
    _minimal_tree(root)
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

    report = EfiBuilder().validate_tree(root, toolchain=toolchain, synthetic_test_mode=True)

    assert report.status == "INVALID"
    assert any("expected manifest" in error for error in report.errors)


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
    validator = tmp_path / "ocvalidate.py"
    validator.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    toolchain = ToolchainSelection(CONTRACT_SCHEMA_VERSION, "1.0.7", "1.0.7", None, None, None, "windows", "x86_64", {"qualification": "qualified"}, str(validator), hashlib.sha256(validator.read_bytes()).hexdigest())

    builder = EfiBuilder(db=db, identity_store_dir=tmp_path / "identities")
    fake_identity = {"SystemProductName": "MacBookPro15,2", "SystemSerialNumber": "SERIAL", "MLB": "MLB1234", "SystemUUID": "12345678"}
    result = builder.build(plan, dep_set, archives, tmp_path / "output", fake_identity=fake_identity, toolchain=toolchain, synthetic_test_mode=True)
    assert result.validation.status == "VALID"
    assert (result.output_dir / "EFI/BOOT/BOOTx64.efi").is_file()
    assert (result.output_dir / "EFI/OC/OpenCore.efi").is_file()
    assert (result.output_dir / "EFI/OC/Tools/OpenShell.efi").is_file()
    for dependency_id in ("opencore", "lilu", "virtualsmc"):
        expected_license = (db.data_dir / "licenses" / f"{dependency_id}.txt").read_text(encoding="utf-8")
        published_license = (result.output_dir / "LICENSES" / f"{dependency_id}.txt").read_text(encoding="utf-8")
        assert published_license == expected_license
        assert result.manifest.license_digests[dependency_id] == hashlib.sha256(expected_license.encode("utf-8")).hexdigest()
    assert not (result.output_dir / ".identity.private.json").exists()
    assert (tmp_path / "identities" / result.identity.storage_ref).is_file()

    manifest_path = result.output_dir / "manifest.json"
    mutated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutated_manifest["artifact_lock_digest"] = "f" * 64
    manifest_path.write_text(json.dumps(mutated_manifest), encoding="utf-8")
    report = builder.validate_tree(result.output_dir, toolchain=toolchain, identity=fake_identity, expected_manifest=result.manifest, synthetic_test_mode=True)
    assert report.status == "INVALID"
    assert any("manifest identity" in error for error in report.errors)

    mismatched_plan = BuildPlan("Lenovo ThinkPad T480s", "sequoia", "different-snapshot", CompatibilityState.EXPERIMENTAL, is_actionable=True, build_ready=True)
    with pytest.raises(BuildPlanError, match="different BuildPlan"):
        EfiBuilder(db=db).build(plan=mismatched_plan, dependencies=dep_set, artifact_paths=archives, output_dir=tmp_path / "mismatch", toolchain=toolchain, synthetic_test_mode=True)


def test_identity_validation_rejects_incomplete_or_extra_fields() -> None:
    assert EfiBuilder._identity_errors({"SystemProductName": "MacBookPro15,2"})
    assert EfiBuilder._identity_errors({"SystemProductName": "MacBookPro15,2", "SystemSerialNumber": "s", "MLB": "m", "SystemUUID": "u", "secret": "x"})


def test_synthetic_schema_config_uses_sample_schema_and_encoded_rom(tmp_path: Path) -> None:
    schema = {
        "ACPI": {"Add": [], "Delete": [], "Patch": []},
        "Booter": {}, "DeviceProperties": {"Add": {}},
        "Kernel": {"Add": []}, "Misc": {}, "NVRAM": {"Add": {}},
        "PlatformInfo": {"Generic": {"ROM": bytes(6)}}, "UEFI": {"Drivers": []},
    }
    sample = tmp_path / "Sample.plist"
    sample.write_bytes(plistlib.dumps(schema))
    generator = SchemaDrivenConfigGenerator(sample, hashlib.sha256(sample.read_bytes()).hexdigest())
    identity = IdentityService.fake_identity()

    config = generator.generate_synthetic_for_test(identity=identity, kexts=["Kexts/Lilu.kext"], drivers=[])

    assert config["Kernel"]["Add"][0]["BundlePath"] == "Kexts/Lilu.kext"
    assert config["PlatformInfo"]["Generic"]["ROM"] == bytes.fromhex(identity["ROM"])
    assert "synthetic" in config["#WARNING - MacLoader"].lower()


def test_diagnostic_redaction_handles_non_text_platform_values() -> None:
    identity = {
        "SystemSerialNumber": "C02SYNTHETIC1", "ROM": bytes.fromhex("a1b2c3d4e5f6"),
        "AdviseFeatures": False, "ProcessorType": 0, "MLB": "",
    }
    text = "serial C02SYNTHETIC1 rom a1b2c3d4e5f6 ROM A1B2C3D4E5F6 value 0 False"
    redacted = EfiBuilder._redact_diagnostics(text, identity)
    assert redacted == "serial <redacted> rom <redacted> ROM <redacted> value 0 False"
