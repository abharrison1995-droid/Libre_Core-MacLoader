"""P4 trusted-toolchain and schema qualification coverage."""

from dataclasses import replace
from pathlib import Path

import pytest

from macloader.build.config import SchemaDrivenConfigGenerator, load_reviewed_profile
from macloader.domain.contracts import ToolchainSelection
from macloader.identity.service import IdentityService
from macloader.toolchain.loader import ToolchainTrustError, TrustedToolchainLoader


def _trusted_selection() -> ToolchainSelection:
    try:
        return TrustedToolchainLoader().select()
    except ToolchainTrustError as exc:
        pytest.skip(f"ignored P4 toolchain bundle is unavailable: {exc}")
    raise AssertionError("unreachable")


def test_catalog_selects_qualified_matching_opencore_toolchain() -> None:
    selection = _trusted_selection()
    assert selection.opencore_version == "1.0.7"
    assert selection.ocvalidate_version == "1.0.7"
    assert selection.provenance["source"] == "trusted-catalog"
    assert selection.provenance["qualification"] == "qualified"
    assert selection.acpi_compiler == "20260408"
    assert selection.identity_tool == "2.1.8"


def test_catalog_rejects_modified_selection() -> None:
    selection = _trusted_selection()
    forged = replace(selection, ocvalidate_sha256="0" * 64)
    with pytest.raises(ToolchainTrustError, match="does not match"):
        TrustedToolchainLoader().verify_selection(forged)


def test_schema_generator_orders_kext_parents_before_plugins() -> None:
    ordered = SchemaDrivenConfigGenerator._ordered_kexts(
        ["SMCSuperIO.kext", "AppleALC.kext", "VirtualSMC.kext", "Lilu.kext", "BlueToolFixup.kext"]
    )
    assert ordered == ("Lilu.kext", "VirtualSMC.kext", "AppleALC.kext", "BlueToolFixup.kext", "SMCSuperIO.kext")


def test_real_matching_ocvalidate_accepts_schema_generated_config(tmp_path: Path) -> None:
    selection = _trusted_selection()
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
