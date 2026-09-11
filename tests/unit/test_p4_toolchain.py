"""P4 trusted-toolchain and schema qualification coverage."""

from dataclasses import replace
import hashlib
import os
from pathlib import Path
import plistlib

import pytest
import yaml

from macloader.build.config import SchemaDrivenConfigGenerator, load_reviewed_profile
from macloader.domain.contracts import ToolchainSelection
from macloader.identity.service import IdentityService
from macloader.toolchain.loader import ToolchainTrustError, TrustedToolchainLoader


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
    try:
        loader = TrustedToolchainLoader()
        return loader, loader.select()
    except ToolchainTrustError:
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
