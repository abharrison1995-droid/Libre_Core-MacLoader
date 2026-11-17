"""Machine-bound ACPI validation failure coverage without private fixtures."""

import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Callable

import pytest

from macloader.build.acpi import AcpiProcessor, TABLE_NAMES
from macloader.domain.contracts import canonical_json_digest
from macloader.exceptions import BuildPlanError


def _table(signature: str = "DSDT", body: bytes = b"test") -> bytes:
    data = bytearray(36 + len(body))
    data[:4] = signature.encode("ascii")
    data[4:8] = len(data).to_bytes(4, "little")
    data[8] = 2
    data[10:16] = b"LENOVO"
    data[16:24] = b"P4TEST  "
    data[24:28] = (1).to_bytes(4, "little")
    data[28:32] = b"MLDR"
    data[32:36] = (1).to_bytes(4, "little")
    data[36:] = body
    data[9] = (-sum(data)) % 256
    return bytes(data)


def test_acpi_header_length_and_checksum_are_checked(tmp_path: Path) -> None:
    valid = tmp_path / "dsdt.dat"
    valid.write_bytes(_table())
    assert AcpiProcessor._validate_table(valid)["signature"] == "DSDT"
    invalid_checksum = tmp_path / "bad.dat"
    invalid_checksum.write_bytes(_table()[:-1] + bytes([_table()[-1] ^ 1]))
    with pytest.raises(BuildPlanError, match="signature or checksum"):
        AcpiProcessor._validate_table(invalid_checksum)
    invalid_length = tmp_path / "length.dat"
    data = bytearray(_table())
    data[4:8] = (len(data) + 1).to_bytes(4, "little")
    invalid_length.write_bytes(data)
    with pytest.raises(BuildPlanError, match="length"):
        AcpiProcessor._validate_table(invalid_length)
    truncated = tmp_path / "truncated.dat"
    truncated.write_bytes(b"DSDT")
    with pytest.raises(BuildPlanError, match="truncated"):
        AcpiProcessor._validate_table(truncated)


def test_acpi_capture_requires_exact_table_set(tmp_path: Path) -> None:
    table_dir = tmp_path / "PRIVATE-ACPI"
    table_dir.mkdir()
    for name in TABLE_NAMES:
        (table_dir / name).write_bytes(_table("DSDT" if name == "dsdt.dat" else "SSDT"))
    assert len(AcpiProcessor._find_tables(table_dir)) == 12
    (table_dir / "extra.dat").write_bytes(_table("SSDT"))
    with pytest.raises(BuildPlanError, match="exactly one DSDT"):
        AcpiProcessor._find_tables(table_dir)


def test_acpi_table_symlink_is_not_machine_bound_evidence(tmp_path: Path) -> None:
    table_dir = tmp_path / "PRIVATE-ACPI"
    table_dir.mkdir()
    outside = tmp_path / "outside.dat"
    outside.write_bytes(_table())
    try:
        (table_dir / "dsdt.dat").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this host")
    with pytest.raises(BuildPlanError, match="missing or unsafe"):
        AcpiProcessor._validate_table(table_dir / "dsdt.dat")


def _fake_iasl(path: Path) -> str:
    if os.name == "nt":
        runner = path.with_name("fake_iasl_runner.py")
        runner.write_text(
            """import sys
from pathlib import Path

if sys.argv[1] == "-v":
    print("ASL+ Optimizing Compiler/Disassembler version 20260408")
elif sys.argv[1] == "-d":
    source = Path(sys.argv[4]).with_suffix(".dsl")
    text = source.read_text(encoding="utf-8") if source.is_file() else 'DefinitionBlock ("", "SSDT", 2, "LENOVO", "P4TEST", 1) {}\\n'
    Path(sys.argv[3] + ".dsl").write_text(text, encoding="utf-8")
    print("disassembled")
elif sys.argv[1] == "-tc":
    Path(sys.argv[3] + ".aml").write_bytes(b"AML")
    print("compiled with 1 Warning")
else:
    raise SystemExit(2)
""",
            encoding="utf-8",
            newline="\r\n",
        )
        path = path.with_suffix(".cmd")
        path.write_text(
            f"@echo off\n\"{sys.executable}\" \"%~dp0fake_iasl_runner.py\" %*\n",
            encoding="utf-8",
            newline="\r\n",
        )
    else:
        path.write_text(
            """#!/bin/sh
if [ "$1" = "-v" ]; then
  echo "ASL+ Optimizing Compiler/Disassembler version 20260408"
  exit 0
fi
if [ "$1" = "-d" ]; then
  if [ -f "${4%.dat}.dsl" ]; then
    cat "${4%.dat}.dsl" > "$3.dsl"
  else
    printf 'DefinitionBlock ("", "SSDT", 2, "LENOVO", "P4TEST", 1) {}\\n' > "$3.dsl"
  fi
  echo "disassembled"
  exit 0
fi
if [ "$1" = "-tc" ]; then
  printf 'AML' > "$3.aml"
  echo "compiled with 1 Warning"
  exit 0
fi
exit 2
""",
            encoding="utf-8",
        )
    path.chmod(0o700)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_dsl(directory: Path, *, cpu: str = "PR00", controller: str = "XHC0",
               controller_address: str = "0x00140000", ec: str = "EC00", extra: str = "") -> None:
    directory.joinpath("dsdt.dsl").write_text(
        f'''DefinitionBlock ("", "DSDT", 2, "LENOVO", "P4TEST", 1)
{{
    Scope (\\_PR) {{ Processor ({cpu}, 0, 0, 0) {{}} }}
    Scope (\\_SB_)
    {{
        Device (PCI0)
        {{
            Device ({controller}) {{ Name (_ADR, {controller_address}) }}
            Device (VID0) {{ Name (_ADR, 0x00020000) }}
            Device (BRG0) {{ Device (USB0) {{ Name (_ADR, 0x00140000) }} }}
            Device (LPC0) {{ Device ({ec}) {{ Name (_HID, EisaId ("PNP0C09")) }} }}
            {extra}
        }}
    }}
}}
''', encoding="utf-8",
    )


def _prepare_build(tmp_path: Path, *, ec: str = "EC00", native_pnlf: bool = False) -> tuple[AcpiProcessor, Path]:
    tool = tmp_path / "iasl"
    tool_digest = _fake_iasl(tool)
    capture = tmp_path / "capture"
    table_dir = capture / "PRIVATE-ACPI"
    table_dir.mkdir(parents=True)
    for name in TABLE_NAMES:
        signature = "DSDT" if name == "dsdt.dat" else "SSDT"
        (table_dir / name).write_bytes(_table(signature, name.encode("ascii")))
    _write_dsl(table_dir, ec=ec, extra="Device (PNLF) {}" if native_pnlf else "")

    if os.name == "nt":
        tool = tool.with_suffix(".cmd")
    processor = AcpiProcessor(tool, tool_digest, tmp_path / "work")
    return processor, capture


@pytest.mark.parametrize(("native_pnlf", "ec"), [(False, "EC00"), (True, "EC00"), (True, "EC__")])
def test_acpi_processor_runs_machine_bound_pipeline_with_pinned_tool(
    tmp_path: Path, native_pnlf: bool, ec: str,
) -> None:
    processor, capture = _prepare_build(tmp_path, ec=ec, native_pnlf=native_pnlf)
    output = tmp_path / "output" / "ACPI"
    result = processor.build(capture, output, expected_bios_binding="N22ET85W-1.62")

    pnlf_files = () if native_pnlf else ("SSDT-PNLF-T480S.aml",)
    ec_files = () if ec == "EC__" else ("SSDT-EC-T480S.aml",)
    assert result.output_files == ("SSDT-PLUG-T480S.aml", *pnlf_files, "SSDT-USBX-T480S.aml", *ec_files)
    notes = ("firmware defines PNLF; SSDT-PNLF omitted",) if native_pnlf else ()
    if ec == "EC__":
        notes += ("native EC present; SSDT-EC omitted",)
    assert result.notes == notes
    assert result.tool_digest == processor.iasl_sha256
    assert result.to_dict()["table_count"] == len(TABLE_NAMES)
    assert all((output / name).read_bytes() == b"AML" for name in result.output_files)
    report = json.loads((output.parent / "P4-ACPI-qualification.json").read_text(encoding="utf-8"))
    assert report["notes"] == list(notes)
    assert (output / "SSDT-PNLF-T480S.aml").is_file() == (not native_pnlf)
    assert (output / "SSDT-EC-T480S.aml").is_file() == bool(ec_files)
    assert result.generated_digest == canonical_json_digest({
        "bios_binding": "N22ET85W-1.62",
        "notes": notes,
        "sources": [{
            "name": name,
            "size_bytes": 3,
            "sha256": hashlib.sha256(b"AML").hexdigest(),
            "diagnostic_digest": canonical_json_digest({"stdout": "compiled with 1 Warning\n", "stderr": ""}),
        } for name in result.output_files],
    })
    assert any(item.warnings == 1 for item in result.diagnostics)
    run_dir = processor.work_root / result.source_evidence_digest
    assert {path.name for path in run_dir.iterdir()} == {"generated-sources", "generated-aml"}
    assert (run_dir / "generated-aml" / "SSDT-PLUG-T480S.aml").is_file()
    assert (run_dir / "generated-sources" / "SSDT-PLUG-T480S.dsl").is_file()

    with pytest.raises(BuildPlanError, match="evidence digest"):
        processor.build(
            capture,
            tmp_path / "other-output",
            expected_bios_binding="N22ET85W-1.62",
            expected_evidence_digest="0" * 64,
        )


def test_acpi_build_cleans_raw_intermediates_after_compile_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    processor, capture = _prepare_build(tmp_path)
    digest = processor.capture_evidence_digest(capture, "synthetic-bios")
    run_dir = processor.work_root / digest
    original_run = processor._run

    def fail_generated_compile(args: list[str], diagnostic_path: Path,
                               cancel: Callable[[], bool] | None = None) -> tuple[int, str, str]:
        if diagnostic_path.name == "SSDT-PLUG-T480S-compile.txt":
            assert (run_dir / "disassembled" / "dsdt.dsl").is_file()
            assert (run_dir / "raw-compiled" / "dsdt.aml").is_file()
            assert (run_dir / "raw-diagnostics" / "dsdt.txt").is_file()
            diagnostic_path.write_text("synthetic compile failure", encoding="utf-8")
            return 1, "", "synthetic compile failure"
        return original_run(args, diagnostic_path, cancel=cancel)

    monkeypatch.setattr(processor, "_run", fail_generated_compile)
    with pytest.raises(BuildPlanError, match="generated ACPI source failed compilation"):
        processor.build(capture, tmp_path / "output" / "ACPI", expected_bios_binding="synthetic-bios")

    assert {path.name for path in run_dir.iterdir()} == {"generated-sources", "generated-aml"}
    assert (run_dir / "generated-sources" / "SSDT-PLUG-T480S.dsl").is_file()


def test_acpi_build_does_not_follow_a_linked_intermediate_directory(tmp_path: Path) -> None:
    processor, capture = _prepare_build(tmp_path)
    digest = processor.capture_evidence_digest(capture, "synthetic-bios")
    run_dir = processor.work_root / digest
    run_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep"
    marker.touch()
    link = run_dir / "raw-diagnostics"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this host")

    with pytest.raises(BuildPlanError, match="work directory is unsafe"):
        processor.build(capture, tmp_path / "output" / "ACPI", expected_bios_binding="synthetic-bios")

    assert link.is_symlink()
    assert set(outside.iterdir()) == {marker}


@pytest.mark.parametrize("cpu", ["PR00", "CPU0"])
def test_names_resolve_from_nested_firmware_devices(tmp_path: Path, cpu: str) -> None:
    _write_dsl(tmp_path, cpu=cpu, controller="USBX")
    names = AcpiProcessor._resolve_names(tmp_path)
    assert names == {
        "cpu0": rf"\_PR.{cpu}", "xhc": r"\_SB.PCI0.USBX", "ec": r"\_SB.PCI0.LPC0.EC00",
        "igpu": r"\_SB.PCI0.VID0", "has_pnlf": False,
    }
    source = AcpiProcessor._generated_sources("synthetic-bios", names)["SSDT-PLUG-T480S"]
    assert rf"External (\_PR.{cpu}, ProcessorObj)" in source
    assert rf"Scope (\_PR.{cpu})" in source


def test_xhc_must_be_direct_child_of_pci0(tmp_path: Path) -> None:
    _write_dsl(tmp_path, controller_address="Zero")
    with pytest.raises(BuildPlanError, match="Captured firmware does not define xhc"):
        AcpiProcessor._resolve_names(tmp_path)


def test_ambiguous_xhc_is_rejected(tmp_path: Path) -> None:
    _write_dsl(tmp_path, extra="Device (USB1) { Name (_ADR, 0x00140000) }")
    with pytest.raises(BuildPlanError, match="multiple matches for xhc"):
        AcpiProcessor._resolve_names(tmp_path)


def test_generated_plug_returns_plugin_type_only_under_darwin(tmp_path: Path) -> None:
    _write_dsl(tmp_path, cpu="CPU0")
    names = AcpiProcessor._resolve_names(tmp_path)
    source = AcpiProcessor._generated_sources("synthetic-bios", names)["SSDT-PLUG-T480S"]
    assert '''If (_OSI ("Darwin"))
    {
        Scope (\\_PR.CPU0)
        {
            Method (_DSM, 4, NotSerialized)''' in source
    assert "If (Arg2 == Zero)" in source
    assert "Return (Buffer (One) { 0x03 })" in source
    assert 'Return (Package (0x02) { "plugin-type", One })' in source


def test_generated_usbx_has_darwin_power_properties(tmp_path: Path) -> None:
    _write_dsl(tmp_path)
    names = AcpiProcessor._resolve_names(tmp_path)
    source = AcpiProcessor._generated_sources("synthetic-bios", names)["SSDT-USBX-T480S"]
    assert "Scope (\\_SB)" in source
    assert "Device (USBX)" in source
    assert "Method (_DSM, 4, NotSerialized)" in source
    assert "Method (_STA, 0, NotSerialized)" in source
    assert 'If (_OSI ("Darwin"))' in source
    assert '"kUSBSleepPowerSupply", 0x13EC' in source
    assert '"kUSBSleepPortCurrentLimit", 0x0834' in source
    assert '"kUSBWakePowerSupply", 0x13EC' in source
    assert '"kUSBWakePortCurrentLimit", 0x0834' in source


def test_generated_ec_has_darwin_status(tmp_path: Path) -> None:
    _write_dsl(tmp_path, ec="EC0")
    names = AcpiProcessor._resolve_names(tmp_path)
    source = AcpiProcessor._generated_sources("synthetic-bios", names)["SSDT-EC-T480S"]
    assert r"Scope (\_SB)" in source
    assert "Device (EC)" in source
    assert 'Name (_HID, "ACID0001")' in source
    assert "Method (_STA, 0, NotSerialized)" in source
    assert 'If (_OSI ("Darwin"))' in source
    assert "Return (0x0F)" in source
    assert "Return (Zero)" in source


def test_generated_pnlf_has_kaby_lake_backlight_identity_and_darwin_status(tmp_path: Path) -> None:
    _write_dsl(tmp_path)
    names = AcpiProcessor._resolve_names(tmp_path)
    source = AcpiProcessor._generated_sources("synthetic-bios", names)["SSDT-PNLF-T480S"]
    assert 'Name (_HID, EisaId ("APP0002"))' in source
    assert 'Name (_CID, "backlight")' in source
    assert "Name (_UID, 0x10)" in source
    assert "Method (_STA, 0, NotSerialized)" in source
    assert 'If (_OSI ("Darwin"))' in source
    assert "Return (0x0B)" in source
    assert "Return (Zero)" in source


def test_acpi_processor_rejects_missing_capture_and_untrusted_tool(tmp_path: Path) -> None:
    missing_tool = AcpiProcessor(tmp_path / "missing-iasl", "0" * 64, tmp_path / "work")
    with pytest.raises(BuildPlanError, match="missing or unsafe"):
        missing_tool.build(tmp_path / "capture", tmp_path / "output", expected_bios_binding="bios")

    tool = tmp_path / "iasl"
    _fake_iasl(tool)
    if os.name == "nt":
        tool = tool.with_suffix(".cmd")
    wrong_digest = AcpiProcessor(tool, "0" * 64, tmp_path / "work2")
    with pytest.raises(BuildPlanError, match="digest"):
        wrong_digest.build(tmp_path / "capture", tmp_path / "output", expected_bios_binding="bios")

    processor = AcpiProcessor(tool, hashlib.sha256(tool.read_bytes()).hexdigest(), tmp_path / "work3")
    with pytest.raises(BuildPlanError, match="directory is missing"):
        processor.build(tmp_path / "capture", tmp_path / "output", expected_bios_binding="bios")
