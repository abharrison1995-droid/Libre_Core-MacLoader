"""Clean-workspace tool provisioning and a real EFI build validated by ocvalidate.

Everything the EFI build consumes here is SYNTHETIC SOFTWARE-ONLY EVIDENCE:
the 20L8/BIOS 1.62 fixture, the ACPI tables (compiled from placeholder ASL),
the USB observation, the acknowledgements, the identity and the dependency
archives.  A VALID result proves only that MacLoader's real builder, the
reproducibly built pinned iASL and the matching pinned ocvalidate agree on the
generated tree.  It is not evidence about the physical T480s, its firmware, a
bootable USB, Recovery, or an installation.

The real-tool tests need the catalog-pinned toolchain.  They run when either
``MACLOADER_TEST_TOOLCHAIN_ROOT`` names an already provisioned tool directory,
or ``MACLOADER_NETWORK_TESTS=1`` allows provisioning into a fresh, empty
workspace from the pinned GitHub release archives.  Otherwise they skip.
"""

import copy
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import tarfile
from typing import Any, Iterator
import zipfile

import pytest

import macloader.build.efi as efi_module
import macloader.toolchain.loader as loader_module
import macloader.workflow.service as workflow_module
from macloader.build.acpi import TABLE_NAMES, AcpiProcessor
from macloader.build.config import load_reviewed_profile
from macloader.build.efi import EfiBuilder
from macloader.configuration.store import ConfigurationStore
from macloader.dependencies.resolver import DependencyResolver
from macloader.domain.dependencies import ArtifactVariant, DependencyArtifact
from macloader.evidence.usb import UsbEvidenceSession, UsbPortObservation
from macloader.identity.service import IdentityService
from macloader.toolchain.loader import ToolchainTrustError, TrustedToolchainLoader
from macloader.workflow.service import WorkflowService


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SYNTHETIC_FIXTURE = FIXTURES / "t480s" / "t480s_20l8_bios162_synthetic.json"
POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="POSIX tool fixtures")


# --- deterministic clean-workspace provisioning (no network) ----------------------


class ArchiveServer:
    """Serves exact bytes per URL and, like the real downloader, verifies digests."""

    def __init__(self, archives: dict[str, bytes]) -> None:
        self.archives = archives
        self.requests: list[str] = []

    def download_artifact(self, artifact: DependencyArtifact, destination: Path, cancel: Any = None) -> Path:
        self.requests.append(artifact.source_url)
        data = self.archives[artifact.source_url]
        if hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise ToolchainTrustError("synthetic archive digest mismatch")
        destination.write_bytes(data)
        return destination


def _script(version: str) -> bytes:
    return f"#!/bin/sh\necho 'synthetic tool {version}'\n".encode()


def _synthetic_catalog(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    host, arch = TrustedToolchainLoader.host_tuple()
    sample = plistlib.dumps({"ACPI": {}, "Kernel": {}, "PlatformInfo": {}, "UEFI": {}})
    ocvalidate, macserial, iasl = _script("1.0.7"), _script("2.1.8"), _script("20260408")
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w") as zip_bundle:
        zip_bundle.writestr("Docs/Sample.plist", sample)
        zip_bundle.writestr("Utilities/ocvalidate/ocvalidate.linux", ocvalidate)
        zip_bundle.writestr("Utilities/macserial/macserial.linux", macserial)
    tarred = io.BytesIO()
    with tarfile.open(fileobj=tarred, mode="w:gz") as tar_bundle:
        info = tarfile.TarInfo("acpica/bin/iasl")
        info.size, info.mode = len(iasl), 0o755
        tar_bundle.addfile(info, io.BytesIO(iasl))
    oc_url = "https://github.com/synthetic/OpenCorePkg/releases/download/1.0.7/OpenCore-1.0.7-RELEASE.zip"
    acpica_url = "https://github.com/synthetic/acpica/releases/download/20260408/acpica.tar.gz"
    archives = {oc_url: zipped.getvalue(), acpica_url: tarred.getvalue()}
    oc_digest = hashlib.sha256(archives[oc_url]).hexdigest()
    acpica_digest = hashlib.sha256(archives[acpica_url]).hexdigest()

    def tool(file_name: str, version: str, url: str, digest: str, data: bytes, invocation: str) -> dict[str, Any]:
        return {
            "file_name": file_name, "version": version, "source_url": url, "source_sha256": digest,
            "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "invocation": invocation,
        }

    catalog = {
        "schema_version": "1",
        "policy_version": "synthetic-software-only",
        "toolchains": [{
            "id": "synthetic", "host_platform": host, "host_architecture": arch, "opencore_version": "1.0.7",
            "archive_url": oc_url, "archive_sha256": oc_digest,
            "sample_plist": tool("Sample.plist", "1.0.7", oc_url + "#Docs/Sample.plist", oc_digest, sample, ""),
            "ocvalidate": tool("ocvalidate", "1.0.7", oc_url + "#Utilities/ocvalidate/ocvalidate.linux", oc_digest, ocvalidate, ""),
            "acpi_compiler": tool("iasl", "20260408", acpica_url, acpica_digest, iasl, "-v"),
            "identity_tool": tool("macserial", "2.1.8", oc_url + "#Utilities/macserial/macserial.linux", oc_digest, macserial, ""),
        }],
    }
    path = tmp_path / "catalog.yaml"
    path.write_text(json.dumps(catalog), encoding="utf-8")  # JSON is valid YAML
    return path, archives


@POSIX_ONLY
def test_clean_workspace_provisioning_is_complete_idempotent_and_repairable(tmp_path: Path) -> None:
    catalog, archives = _synthetic_catalog(tmp_path)
    root = tmp_path / "workspace" / "p4-toolchain"
    loader = TrustedToolchainLoader(catalog_path=catalog, tool_root=root)
    with pytest.raises(ToolchainTrustError):
        loader.select()

    server = ArchiveServer(archives)
    selection = loader.provision(downloader=server)  # type: ignore[arg-type]
    assert sorted(path.name for path in root.iterdir()) == ["Sample.plist", "iasl", "macserial", "ocvalidate"]
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "Sample.plist").stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "ocvalidate").stat().st_mode) == 0o700
    assert selection.provenance["source"] == "trusted-catalog"
    assert "synthetic tool 1.0.7" in str(selection.provenance["ocvalidate_banner"])
    assert len(server.requests) == 2
    assert not list(root.parent.glob("macloader-toolchain-*")), "staging must be removed"

    # A complete verified workspace is reused without any network access.
    offline = ArchiveServer({})
    assert loader.provision(downloader=offline).to_dict() == selection.to_dict()  # type: ignore[arg-type]
    assert offline.requests == []

    # Tampering is detected and repaired from the pinned archives only.
    (root / "ocvalidate").write_bytes(_script("9.9.9"))
    with pytest.raises(ToolchainTrustError, match="size mismatch|digest mismatch"):
        loader.select()
    repaired = loader.provision(downloader=ArchiveServer(archives))  # type: ignore[arg-type]
    assert repaired.to_dict() == selection.to_dict()


@POSIX_ONLY
def test_clean_workspace_provisioning_publishes_nothing_on_a_bad_archive(tmp_path: Path) -> None:
    catalog, archives = _synthetic_catalog(tmp_path)
    tampered = {url: data + b"tampered" for url, data in archives.items()}
    root = tmp_path / "workspace" / "p4-toolchain"
    loader = TrustedToolchainLoader(catalog_path=catalog, tool_root=root)
    with pytest.raises(ToolchainTrustError, match="digest mismatch"):
        loader.provision(downloader=ArchiveServer(tampered))  # type: ignore[arg-type]
    assert list(root.iterdir()) == []
    assert not list(root.parent.glob("macloader-toolchain-*"))


# --- real pinned toolchain ---------------------------------------------------------


@pytest.fixture(scope="session")
def pinned_toolchain_root(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    configured = os.environ.get("MACLOADER_TEST_TOOLCHAIN_ROOT")
    if configured:
        root = Path(configured).expanduser().absolute()
        TrustedToolchainLoader(tool_root=root).select()
        yield root
        return
    if os.environ.get("MACLOADER_NETWORK_TESTS") != "1":
        pytest.skip(
            "set MACLOADER_NETWORK_TESTS=1 to provision the pinned toolchain into a clean workspace, "
            "or MACLOADER_TEST_TOOLCHAIN_ROOT to reuse a verified one"
        )
    root = tmp_path_factory.mktemp("clean-workspace") / "p4-toolchain"
    assert not root.exists()
    TrustedToolchainLoader(tool_root=root).provision()
    yield root


def test_real_clean_workspace_provisioning_matches_the_catalog(pinned_toolchain_root: Path) -> None:
    loader = TrustedToolchainLoader(tool_root=pinned_toolchain_root)
    record = loader.record()
    selection = loader.select()
    expected = {item.file_name for item in (record.sample_plist, record.ocvalidate, record.acpi_compiler, record.identity_tool)}
    assert {path.name for path in pinned_toolchain_root.iterdir()} == expected
    assert selection.opencore_version == selection.ocvalidate_version == record.opencore_version
    assert record.ocvalidate.version in str(selection.provenance["ocvalidate_banner"])
    assert record.acpi_compiler.version in str(selection.provenance["iasl_banner"])
    for item in (record.ocvalidate, record.acpi_compiler, record.identity_tool):
        path = pinned_toolchain_root / item.file_name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item.sha256


def _synthetic_acpi_capture(directory: Path, iasl: Path) -> Path:
    """Compile placeholder tables; SYNTHETIC, software-only, not firmware evidence."""
    table_dir = directory / "PRIVATE-ACPI"
    table_dir.mkdir(parents=True)
    work = directory / "asl"
    work.mkdir()
    for index, name in enumerate(TABLE_NAMES):
        signature = "DSDT" if name == "dsdt.dat" else "SSDT"
        source = work / f"table{index}.asl"
        source.write_text(
            f'DefinitionBlock ("", "{signature}", 2, "LENOVO", "SYN{index:05d}", 1) {{ Name (SYN{index:X}, {index}) }}\n',
            encoding="ascii",
        )
        compiled = subprocess.run(
            [str(iasl), "-p", str(work / f"table{index}"), str(source)],
            capture_output=True, text=True, check=False, timeout=60,
        )
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr
        (work / f"table{index}.aml").replace(table_dir / name)
    shutil.rmtree(work)
    return directory


def _synthetic_archives(db: Any, plan: Any, directory: Path) -> dict[str, Path]:
    """Placeholder dependency archives whose digests are pinned in-memory only."""
    directory.mkdir()
    archives: dict[str, Path] = {}
    for dependency in DependencyResolver(db).resolve(plan, ArtifactVariant.RELEASE).resolved_dependencies:
        spec = db.get_dependency_spec(dependency.dependency_id)
        archive = directory / f"{dependency.dependency_id}.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            for component in spec.subcomponents:
                name = f"Release/{component}"
                if component.endswith(".kext"):
                    bundle.writestr(f"{name}/Contents/Info.plist", b"synthetic")
                    bundle.writestr(f"{name}/Contents/MacOS/{Path(component).stem}", b"synthetic")
                else:
                    bundle.writestr(name, b"synthetic")
        artifact = spec.get_artifact(ArtifactVariant.RELEASE)
        artifact.asset_name = archive.name
        artifact.sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
        artifact.size_bytes = archive.stat().st_size
        archives[dependency.dependency_id] = archive
    return archives


def test_real_efi_builder_with_synthetic_inputs_passes_matching_ocvalidate(
    pinned_toolchain_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loader_module, "DEFAULT_TOOL_ROOT", pinned_toolchain_root)
    monkeypatch.setattr(efi_module, "DEFAULT_WORKSPACE_DIR", tmp_path / "workspace")
    monkeypatch.setattr(workflow_module, "DEFAULT_PRIVATE_DIR", tmp_path / "workspace" / "private")
    monkeypatch.setattr(workflow_module, "DEFAULT_ACPI_DIR", tmp_path / "workspace" / "private" / "acpi")
    toolchain = TrustedToolchainLoader().select()
    assert toolchain.acpi_compiler_path is not None

    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    configuration, snapshot = service.create(SYNTHETIC_FIXTURE)
    assert "SYNTHETIC" in str(snapshot.raw_evidence.get("fixture_note", ""))
    configuration = service.set_target(configuration, "15.0", "24A335")
    capture = _synthetic_acpi_capture(tmp_path / "synthetic-acpi", Path(toolchain.acpi_compiler_path))
    configuration, acpi_record = service.import_acpi_capture(
        configuration, snapshot, capture, allow_synthetic_snapshot=True
    )
    usb_source = tmp_path / "synthetic-usb-session.json"
    usb = UsbEvidenceSession(
        snapshot.snapshot_id, "N22ET85W-1.62", str(usb_source), "synthetic-software-only",
        (UsbPortObservation("left-a", "HS01", "USB-A", "5Gbps", "8086:9d2f"),),
    )
    usb_source.write_text(json.dumps(usb.to_dict()), encoding="utf-8")
    configuration = service.add_evidence(configuration, usb.to_evidence_record())
    policy = service.orchestrator.configuration_service.policy
    for option_id, option in policy.options.items():
        if option.requires_acknowledgement and option_id in configuration.selected_options():
            configuration = service.acknowledge(configuration, option_id, option.explanation)
    evaluation = service.evaluate(configuration, snapshot).evaluation
    assert not evaluation.has_blockers and not evaluation.plan.unresolved_requirements
    assert any(issue.code == "PHYSICAL_ACCEPTANCE_PENDING" for issue in evaluation.issues)

    plan = evaluation.plan
    # The synthetic archive digests are pinned in a private copy of the
    # catalog so the process-wide database singleton is never changed.
    db = copy.deepcopy(service.orchestrator.db)
    archives = _synthetic_archives(db, plan, tmp_path / "synthetic-archives")
    dependencies = DependencyResolver(db).resolve(plan, ArtifactVariant.RELEASE)
    capture_root = Path(acpi_record.private_ref).parent
    evidence_digest = AcpiProcessor.capture_evidence_digest(capture_root, "N22ET85W-1.62", snapshot.snapshot_id)
    result = EfiBuilder(db=db, identity_store_dir=tmp_path / "identities").build(
        plan, dependencies, archives, tmp_path / "efi",
        fake_identity=IdentityService.fake_identity(), toolchain=toolchain,
        reviewed_profile=load_reviewed_profile(), private_acpi_capture=capture_root,
        expected_acpi_evidence_digest=evidence_digest,
    )

    assert result.validation.status == "VALID", result.validation.errors
    assert result.validation.checks["ocvalidate_exit"] == "0"
    assert result.validation.validator_version == toolchain.ocvalidate_version == toolchain.opencore_version
    assert result.manifest.toolchain_digest == toolchain.digest
    assert result.manifest.evidence_digests == (evidence_digest,)
    generated = sorted(path.name for path in (result.output_dir / "EFI" / "OC" / "ACPI").iterdir())
    assert generated and all(name.startswith("SSDT-") and name.endswith(".aml") for name in generated)

    # The same real ocvalidate rejects a semantically broken config.plist.
    broken = tmp_path / "broken"
    shutil.copytree(result.output_dir, broken)
    config_path = broken / "EFI" / "OC" / "config.plist"
    config = plistlib.loads(config_path.read_bytes())
    config["Misc"]["Security"]["Vault"] = "NotAValidVaultMode"
    config_path.write_bytes(plistlib.dumps(config))
    manifest = replace(result.manifest, output_digest=EfiBuilder._tree_digest(broken))
    (broken / "manifest.json").write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
    report = EfiBuilder(db=db).validate_tree(broken, toolchain=toolchain, expected_manifest=manifest)
    assert report.status == "INVALID"
    assert report.checks["ocvalidate_exit"] != "0"


def test_real_ocvalidate_selection_is_rejected_when_forged(pinned_toolchain_root: Path) -> None:
    selection = TrustedToolchainLoader(tool_root=pinned_toolchain_root).select()
    forged = replace(selection, ocvalidate_sha256="0" * 64)
    with pytest.raises(ToolchainTrustError, match="does not match the trusted catalog"):
        TrustedToolchainLoader(tool_root=pinned_toolchain_root).verify_selection(forged)
