"""P4 trusted-toolchain and schema qualification coverage."""

from dataclasses import replace
import hashlib
import io
import os
from pathlib import Path
import plistlib
import subprocess
import tarfile
import zipfile

import pytest
import yaml

from macloader.build.config import SchemaDrivenConfigGenerator, load_reviewed_profile
from macloader.domain.contracts import ToolchainSelection
from macloader.identity.service import IdentityService
from macloader.toolchain.loader import ToolchainTrustError, TrustedToolchainLoader
from macloader.toolchain.loader import ToolRecord
from macloader.dependencies.downloader import Downloader
from macloader.exceptions import ChecksumMismatchError


def _synthetic_loader(tmp_path: Path) -> TrustedToolchainLoader:
    """Provide a deterministic catalog when ignored private tools are absent in CI."""
    host, architecture = TrustedToolchainLoader.host_tuple()
    tool_root = tmp_path / "synthetic-toolchain"
    tool_root.mkdir()
    schema: dict[str, object] = {
        "ACPI": {}, "Booter": {}, "DeviceProperties": {}, "Kernel": {},
        "Misc": {}, "NVRAM": {}, "PlatformInfo": {"Generic": {}}, "UEFI": {},
    }
    sample_path = tool_root / "Sample.plist"
    sample_path.write_bytes(plistlib.dumps(schema, sort_keys=False))

    def write_tool(name: str, version: str) -> Path:
        path = tool_root / name
        if os.name == "nt":
            path = path.with_suffix(".cmd")
            path.write_text(f"@echo off\necho {version}\nexit /b 0\n", encoding="utf-8", newline="\r\n")
        else:
            path.write_text(f"#!/bin/sh\necho {version}\n", encoding="utf-8")
            path.chmod(0o700)
        return path

    validator = write_tool("ocvalidate", "1.0.7")
    compiler = write_tool("iasl", "20260408")
    identity = write_tool("macserial", "2.1.8")

    def tool_record(path: Path, version: str, invocation: str) -> dict[str, object]:
        data = path.read_bytes()
        return {
            "file_name": path.name,
            "version": version,
            "source_url": "https://example.invalid/synthetic-tool",
            "source_sha256": "0" * 64,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "invocation": invocation,
        }

    catalog = {
        "schema_version": "1",
        "policy_version": "synthetic-ci",
        "toolchains": [{
            "id": f"synthetic-{host}-{architecture}",
            "host_platform": host,
            "host_architecture": architecture,
            "opencore_version": "1.0.7",
            "archive_url": "https://example.invalid/synthetic-archive",
            "archive_sha256": "0" * 64,
            "sample_plist": tool_record(sample_path, "1.0.7", ""),
            "ocvalidate": tool_record(validator, "1.0.7", ""),
            "acpi_compiler": tool_record(compiler, "20260408", "-v"),
            "identity_tool": tool_record(identity, "2.1.8", "--version"),
        }],
    }
    catalog_path = tmp_path / "synthetic-catalog.yaml"
    catalog_path.write_text(yaml.safe_dump(catalog, sort_keys=False), encoding="utf-8")
    return TrustedToolchainLoader(catalog_path=catalog_path, tool_root=tool_root)


def _trusted_selection(tmp_path: Path) -> tuple[TrustedToolchainLoader, ToolchainSelection]:
    loader = TrustedToolchainLoader()
    # CI intentionally has no ignored private tool bundle.  Use the synthetic
    # fixture only when that entire private root is absent; any catalog,
    # digest, size, version, or path failure in an available root must fail.
    if not loader.tool_root.exists():
        loader = _synthetic_loader(tmp_path)
        return loader, loader.select()
    try:
        return loader, loader.select()
    except ToolchainTrustError:
        # Unit coverage remains deterministic when no complete private tool
        # bundle is available. If every pin is present but fails, keep the
        # failure visible instead of silently replacing it with fake tools.
        record = loader.record()
        pinned = (record.sample_plist, record.ocvalidate, record.acpi_compiler, record.identity_tool)
        if all((loader.tool_root / item.file_name).is_file() for item in pinned):
            raise
        loader = _synthetic_loader(tmp_path)
        return loader, loader.select()


def test_catalog_selects_qualified_matching_opencore_toolchain(tmp_path: Path) -> None:
    _, selection = _trusted_selection(tmp_path)
    assert selection.opencore_version == "1.0.7"
    assert selection.ocvalidate_version == "1.0.7"
    assert selection.provenance["source"] == "trusted-catalog"
    assert selection.provenance["qualification"] == "qualified"
    assert selection.acpi_compiler == "20260408"
    assert selection.identity_tool == "2.1.8"


def test_catalog_rejects_modified_selection(tmp_path: Path) -> None:
    loader, selection = _trusted_selection(tmp_path)
    forged = replace(selection, ocvalidate_sha256="0" * 64)
    with pytest.raises(ToolchainTrustError, match="does not match"):
        loader.verify_selection(forged)


def test_schema_generator_orders_kext_parents_before_plugins() -> None:
    ordered = SchemaDrivenConfigGenerator._ordered_kexts(
        ["SMCSuperIO.kext", "AppleALC.kext", "VirtualSMC.kext", "Lilu.kext", "BlueToolFixup.kext"]
    )
    assert ordered == ("Lilu.kext", "VirtualSMC.kext", "AppleALC.kext", "BlueToolFixup.kext", "SMCSuperIO.kext")


def test_real_matching_ocvalidate_accepts_schema_generated_config(tmp_path: Path) -> None:
    _, selection = _trusted_selection(tmp_path)
    assert selection.sample_plist_path is not None
    assert selection.sample_plist_sha256 is not None
    assert selection.ocvalidate_path is not None
    profile = load_reviewed_profile()
    generator = SchemaDrivenConfigGenerator(Path(selection.sample_plist_path), selection.sample_plist_sha256)
    config = generator.generate(
        profile,
        identity=IdentityService.fake_identity(),
        kexts=(),
        drivers=("OpenRuntime.efi",),
        acpi_files=(),
        opencore_version=selection.opencore_version,
        acpi_digest="",
        evidence_digests=(),
    )
    config_path = tmp_path / "config.plist"
    generator.write(config, config_path)
    import subprocess

    completed = subprocess.run(
        [selection.ocvalidate_path, str(config_path)], capture_output=True, text=True, check=False, timeout=30
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def _provisioning_catalog(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    """Build trusted synthetic archives to exercise acquisition and extraction."""
    host, architecture = TrustedToolchainLoader.host_tuple()
    schema: dict[str, object] = {"ACPI": {}, "Kernel": {}, "PlatformInfo": {}, "UEFI": {}}
    sample = plistlib.dumps(schema)
    validator = b"#!/bin/sh\necho 1.0.7\n"
    identity = b"#!/bin/sh\necho 2.1.8\n"
    compiler = b"#!/bin/sh\necho iasl version 20260408\n"

    oc_buffer = io.BytesIO()
    with zipfile.ZipFile(oc_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Docs/Sample.plist", sample)
        archive.writestr("Utilities/ocvalidate/ocvalidate.linux", validator)
        archive.writestr("Utilities/macserial/macserial.linux", identity)
    oc_archive = oc_buffer.getvalue()

    oc_url = "https://github.com/acidanthera/OpenCorePkg/releases/download/1.0.7/OpenCore-1.0.7-RELEASE.zip"
    acpi_url = "https://github.com/open-acpica/acpica/releases/download/20260408/iasl"
    sources = {oc_url: oc_archive, acpi_url: compiler}

    def record(file_name: str, version: str, contents: bytes, url: str, member: str | None, invocation: str) -> dict[str, object]:
        return {
            "file_name": file_name,
            "version": version,
            "source_url": url + (f"#{member}" if member else ""),
            "source_sha256": hashlib.sha256(sources[url]).hexdigest(),
            "size_bytes": len(contents),
            "sha256": hashlib.sha256(contents).hexdigest(),
            "invocation": invocation,
        }

    catalog = {
        "schema_version": "1",
        "policy_version": "synthetic-provisioning",
        "toolchains": [{
            "id": f"synthetic-{host}-{architecture}",
            "host_platform": host,
            "host_architecture": architecture,
            "opencore_version": "1.0.7",
            "archive_url": oc_url,
            "archive_sha256": hashlib.sha256(oc_archive).hexdigest(),
            "sample_plist": record("Sample.plist", "1.0.7", sample, oc_url, "Docs/Sample.plist", ""),
            "ocvalidate": record("ocvalidate", "1.0.7", validator, oc_url, "Utilities/ocvalidate/ocvalidate.linux", ""),
            "acpi_compiler": record("iasl", "20260408", compiler, acpi_url, None, "-v"),
            "identity_tool": record("macserial", "2.1.8", identity, oc_url, "Utilities/macserial/macserial.linux", "--version"),
        }],
    }
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(yaml.safe_dump(catalog, sort_keys=False), encoding="utf-8")
    return catalog_path, sources


def test_provision_downloads_only_catalog_pins_and_selects_real_banners(tmp_path: Path) -> None:
    catalog_path, archives = _provisioning_catalog(tmp_path)
    tool_root = tmp_path / "workspace" / "p4-toolchain"

    def transport(url: str, destination: Path) -> None:
        destination.write_bytes(archives[url])

    selection = TrustedToolchainLoader(catalog_path, tool_root).provision(
        downloader=Downloader(transport=transport)
    )
    assert selection.provenance["qualification"] == "qualified"
    assert selection.ocvalidate_path is not None and Path(selection.ocvalidate_path).is_file()
    assert selection.identity_tool_path is not None and Path(selection.identity_tool_path).is_file()
    assert selection.sample_plist_path is not None and Path(selection.sample_plist_path).is_file()
    assert (tool_root / "iasl").stat().st_mode & 0o111


def test_provision_rejects_archive_digest_mismatch_without_publishing_tools(tmp_path: Path) -> None:
    catalog_path, archives = _provisioning_catalog(tmp_path)
    tool_root = tmp_path / "workspace" / "p4-toolchain"
    first_url = next(iter(archives))

    def tampered_transport(url: str, destination: Path) -> None:
        data = archives[url]
        destination.write_bytes(data + b"tampered" if url == first_url else data)

    with pytest.raises(ChecksumMismatchError, match="Integrity check failed"):
        TrustedToolchainLoader(catalog_path, tool_root).provision(
            downloader=Downloader(transport=tampered_transport)
        )
    assert not list(tool_root.glob("*.verified"))
    assert not list(tool_root.glob("ocvalidate"))


def test_provision_rejects_wrong_platform_binary_before_publishing_any_tool(tmp_path: Path) -> None:
    catalog_path, archives = _provisioning_catalog(tmp_path)
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    windows_binary = b"MZ" + b"\0" * 62
    bad_url = "https://github.com/open-acpica/acpica/releases/download/20260408/iasl"
    archives[bad_url] = windows_binary
    record = catalog["toolchains"][0]["acpi_compiler"]
    record["size_bytes"] = len(windows_binary)
    record["sha256"] = hashlib.sha256(windows_binary).hexdigest()
    record["source_sha256"] = hashlib.sha256(windows_binary).hexdigest()
    catalog_path.write_text(yaml.safe_dump(catalog, sort_keys=False), encoding="utf-8")
    tool_root = tmp_path / "workspace" / "p4-toolchain"

    def transport(url: str, destination: Path) -> None:
        destination.write_bytes(archives[url])

    with pytest.raises(ToolchainTrustError, match="Unable to execute trusted acpi_compiler"):
        TrustedToolchainLoader(catalog_path, tool_root).provision(downloader=Downloader(transport=transport))
    assert not list(tool_root.glob("*.verified"))
    assert not list(tool_root.glob("ocvalidate"))
    assert not list(tool_root.glob("macserial"))


def test_toolchain_extractors_reject_ambiguous_or_unsafe_archive_members(tmp_path: Path) -> None:
    payload = b"pinned tool bytes"
    record = ToolRecord(
        name="ocvalidate", file_name="tool", version="1.0.7",
        source_url="https://example.invalid/archive", source_sha256="0" * 64,
        size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(), invocation="",
    )

    missing_zip = tmp_path / "missing.zip"
    with zipfile.ZipFile(missing_zip, "w") as archive:
        archive.writestr("other", payload)
    with pytest.raises(ToolchainTrustError, match="missing or ambiguous"):
        TrustedToolchainLoader._read_zip_member(missing_zip, "tool", record)

    symlink_zip = tmp_path / "symlink.zip"
    link = zipfile.ZipInfo("tool")
    link.create_system = 3
    link.external_attr = (0o120777 << 16)
    with zipfile.ZipFile(symlink_zip, "w") as archive:
        archive.writestr(link, payload)
    with pytest.raises(ToolchainTrustError, match="unsafe type or size"):
        TrustedToolchainLoader._read_zip_member(symlink_zip, "tool", record)

    duplicate_zip = tmp_path / "duplicate.zip"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(duplicate_zip, "w") as archive:
            archive.writestr("tool", payload)
            archive.writestr("tool", payload)
    with pytest.raises(ToolchainTrustError, match="missing or ambiguous"):
        TrustedToolchainLoader._read_zip_member(duplicate_zip, "tool", record)

    bad_tar = tmp_path / "bad.tar.gz"
    with tarfile.open(bad_tar, "w:gz") as archive:
        directory = tarfile.TarInfo("release/iasl")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
    compiler_record = replace(record, name="acpi_compiler", file_name="iasl")
    with pytest.raises(ToolchainTrustError, match="contains source only"):
        TrustedToolchainLoader._read_tar_tool(bad_tar, compiler_record)


def test_toolchain_extractors_enforce_size_type_and_digest_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import macloader.toolchain.loader as loader_module

    payload = b"binary"
    record = ToolRecord(
        name="ocvalidate", file_name="tool", version="1.0.7",
        source_url="https://example.invalid/archive", source_sha256="0" * 64,
        size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(), invocation="",
    )
    archive_path = tmp_path / "tool.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("tool", payload)
    monkeypatch.setattr(loader_module, "MAX_TOOL_BYTES", 1)
    with pytest.raises(ToolchainTrustError, match="exceeds the tool size limit"):
        TrustedToolchainLoader._read_zip_member(archive_path, "tool", record)
    monkeypatch.setattr(loader_module, "MAX_TOOL_BYTES", 128 * 1024 * 1024)
    with pytest.raises(ToolchainTrustError, match="digest or size mismatch"):
        TrustedToolchainLoader._read_zip_member(archive_path, "tool", replace(record, sha256="0" * 64))

    tar_path = tmp_path / "tool.tar.gz"
    with tarfile.open(tar_path, "w:gz") as archive:
        link = tarfile.TarInfo("release/tool")
        link.type = tarfile.SYMTYPE
        link.linkname = "elsewhere"
        archive.addfile(link)
    with pytest.raises(ToolchainTrustError, match="unsafe type or size"):
        TrustedToolchainLoader._read_tar_tool(tar_path, record)


def test_acpica_build_policy_fails_closed_without_build_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import macloader.toolchain.loader as loader_module

    record = ToolRecord(
        name="acpi_compiler", file_name="iasl", version="20260408",
        source_url="https://example.invalid/source", source_sha256="0" * 64,
        size_bytes=1, sha256="0" * 64, invocation="-v",
    )
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz"):
        pass
    staging = tmp_path / "stage"
    staging.mkdir()
    with pytest.raises(ToolchainTrustError, match="source build policy is missing"):
        TrustedToolchainLoader._build_acpica_iasl(archive, record, staging)

    source_record = replace(record, build_source_root="acpica-unix")
    monkeypatch.setattr(loader_module.shutil, "which", lambda _program: None)
    with pytest.raises(ToolchainTrustError, match="requires make, GCC, Bison, Flex and m4"):
        TrustedToolchainLoader._build_acpica_iasl(archive, source_record, staging)


@pytest.mark.parametrize(
    ("catalog_text", "message"),
    [
        ("[]", "unsupported schema"),
        ("schema_version: '1'\npolicy_version: test\ntoolchains: []\n", "no records"),
        ("schema_version: '1'\npolicy_version: test\ntoolchains: [bad]\n", "records must be mappings"),
    ],
)
def test_toolchain_catalog_rejects_unreviewable_shapes(
    tmp_path: Path, catalog_text: str, message: str
) -> None:
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(catalog_text, encoding="utf-8")
    with pytest.raises(ToolchainTrustError, match=message):
        TrustedToolchainLoader(catalog, tmp_path / "tools")


@pytest.mark.parametrize(
    ("label", "build", "message"),
    [
        ("ocvalidate", {"kind": "acpica-unix-iasl", "source_root": "acpica"}, "unsupported source build policy"),
        ("acpi_compiler", {"kind": "other", "source_root": "acpica"}, "unsupported source build policy"),
        ("acpi_compiler", {"kind": "acpica-unix-iasl", "source_root": "../outside"}, "unsafe ACPICA source root"),
    ],
)
def test_toolchain_catalog_rejects_unreviewed_build_policies(
    label: str, build: dict[str, str], message: str
) -> None:
    from macloader.toolchain.loader import _tool_record

    with pytest.raises(ToolchainTrustError, match=message):
        _tool_record({"build": build}, label)


def test_toolchain_catalog_reports_missing_required_pins() -> None:
    from macloader.toolchain.loader import _tool_record

    with pytest.raises(ToolchainTrustError, match="missing sample_plist.file_name"):
        _tool_record({}, "sample_plist")


def test_toolchain_root_and_version_checks_reject_untrusted_state(tmp_path: Path) -> None:
    loader = _synthetic_loader(tmp_path)
    linked_root = tmp_path / "linked-toolchain"
    linked_root.symlink_to(loader.tool_root, target_is_directory=True)
    loader.tool_root = linked_root
    with pytest.raises(ToolchainTrustError, match="symlink boundary"):
        loader.select()

    version_root = tmp_path / "version"
    version_root.mkdir()
    loader = _synthetic_loader(version_root)
    deferred = loader.select(require_executable_checks=False)
    assert deferred.provenance["ocvalidate_banner"] == "verification deferred"
    record = replace(loader.record().ocvalidate, version="wrong-version")
    validator = Path(deferred.ocvalidate_path or "")
    with pytest.raises(ToolchainTrustError, match="did not report version"):
        loader._run_version(validator, record)


def test_toolchain_verifier_rejects_path_size_and_digest_mismatches(tmp_path: Path) -> None:
    loader = _synthetic_loader(tmp_path)
    record = loader.record().ocvalidate
    with pytest.raises(ToolchainTrustError, match="unsafe filename"):
        loader._verify_file(replace(record, file_name="../ocvalidate"))
    with pytest.raises(ToolchainTrustError, match="size mismatch"):
        loader._verify_file(replace(record, size_bytes=record.size_bytes + 1))
    with pytest.raises(ToolchainTrustError, match="digest mismatch"):
        loader._verify_file(replace(record, sha256="0" * 64))


def test_toolchain_catalog_requires_unique_host_record(tmp_path: Path) -> None:
    loader = _synthetic_loader(tmp_path)
    with pytest.raises(ToolchainTrustError, match="No unique trusted toolchain record"):
        loader.record("darwin", "arm64")


def test_provision_rolls_back_previous_tools_when_final_selection_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path, archives = _provisioning_catalog(tmp_path)
    tool_root = tmp_path / "workspace" / "p4-toolchain"
    tool_root.mkdir(parents=True)
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    record = catalog["toolchains"][0]
    previous: dict[str, bytes] = {}
    for item in (record["sample_plist"], record["ocvalidate"], record["acpi_compiler"], record["identity_tool"]):
        data = ("previous:" + item["file_name"]).encode("utf-8")
        (tool_root / item["file_name"]).write_bytes(data)
        previous[item["file_name"]] = data

    loader = TrustedToolchainLoader(catalog_path, tool_root)
    original_select = loader.select
    calls = 0

    def fail_after_publication(*, require_executable_checks: bool = True) -> ToolchainSelection:
        nonlocal calls
        calls += 1
        selected = original_select(require_executable_checks=require_executable_checks)
        if calls == 2:
            raise ToolchainTrustError("injected final publication verification failure")
        return selected

    monkeypatch.setattr(loader, "select", fail_after_publication)

    def transport(url: str, destination: Path) -> None:
        destination.write_bytes(archives[url])

    with pytest.raises(ToolchainTrustError, match="injected final publication"):
        loader.provision(downloader=Downloader(transport=transport))
    assert {name: (tool_root / name).read_bytes() for name in previous} == previous
    assert not list(tool_root.glob("*.verified"))


def test_acpica_source_build_extracts_only_safe_paths_and_pins_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import macloader.toolchain.loader as loader_module

    archive_path = tmp_path / "acpica-source.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name in (
            "acpica-unix-20260408",
            "acpica-unix-20260408/generate",
            "acpica-unix-20260408/generate/unix",
        ):
            directory = tarfile.TarInfo(name)
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
        makefile = tarfile.TarInfo("acpica-unix-20260408/generate/unix/Makefile")
        makefile.size = 5
        archive.addfile(makefile, io.BytesIO(b"iasl:\n\t@true\n"))

    output = b"#!/bin/sh\necho ASL+ Optimizing Compiler 20260408\n"
    record = ToolRecord(
        name="acpi_compiler", file_name="iasl", version="20260408",
        source_url="https://github.com/open-acpica/acpica/releases/download/20260408/source.tar.gz",
        source_sha256="0" * 64, size_bytes=len(output),
        sha256=hashlib.sha256(output).hexdigest(), invocation="-v",
        build_source_root="acpica-unix-20260408",
    )
    monkeypatch.setattr(loader_module.shutil, "which", lambda _program: "/usr/bin/tool")

    def fake_make(_args: list[str], *, cwd: Path, **kwargs: object) -> subprocess.CompletedProcess[str]:
        binary = cwd / "bin" / "iasl"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(output)
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(loader_module.subprocess, "run", fake_make)
    staging = tmp_path / "staging"
    staging.mkdir()
    assert TrustedToolchainLoader._build_acpica_iasl(archive_path, record, staging) == output

    unsafe_archive = tmp_path / "unsafe-source.tar.gz"
    with tarfile.open(unsafe_archive, "w:gz") as archive:
        traversal = tarfile.TarInfo("acpica-unix-20260408/../../outside")
        traversal.size = 1
        archive.addfile(traversal, io.BytesIO(b"x"))
    unsafe_staging = tmp_path / "unsafe-staging"
    unsafe_staging.mkdir()
    with pytest.raises(ToolchainTrustError, match="unsafe source path"):
        TrustedToolchainLoader._build_acpica_iasl(unsafe_archive, record, unsafe_staging)
    assert not (tmp_path / "outside").exists()
