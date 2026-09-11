"""Machine-bound ACPI validation failure coverage without private fixtures."""

import hashlib
import os
from pathlib import Path

import pytest

from macloader.build.acpi import AcpiProcessor, TABLE_NAMES
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
    assert len(AcpiProcessor._find_tables(table_dir)) == 13
    (table_dir / "extra.dat").write_bytes(_table("SSDT"))
    with pytest.raises(BuildPlanError, match="exactly one DSDT"):
        AcpiProcessor._find_tables(table_dir)


def _fake_iasl(path: Path) -> str:
    if os.name == "nt":
        path = path.with_suffix(".cmd")
        path.write_text(
            """@echo off
if "%~1" == "-v" (
  echo ASL+ Optimizing Compiler/Disassembler version 20260408
  exit /b 0
)
if "%~1" == "-d" (
  echo DefinitionBlock ("", "SSDT", 2, "LENOVO", "P4TEST", 1) {} > "%~3.dsl"
  echo disassembled
  exit /b 0
)
if "%~1" == "-tc" (
  <nul set /p "=AML" > "%~3.aml"
  echo compiled with 1 Warning
  exit /b 0
)
exit /b 2
""",
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
  printf 'DefinitionBlock ("", "SSDT", 2, "LENOVO", "P4TEST", 1) {}\\n' > "$3.dsl"
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


def test_acpi_processor_runs_machine_bound_pipeline_with_pinned_tool(tmp_path: Path) -> None:
    tool = tmp_path / "iasl"
    tool_digest = _fake_iasl(tool)
    capture = tmp_path / "capture"
    table_dir = capture / "PRIVATE-ACPI"
    table_dir.mkdir(parents=True)
    for name in TABLE_NAMES:
        signature = "DSDT" if name == "dsdt.dat" else "SSDT"
        (table_dir / name).write_bytes(_table(signature, name.encode("ascii")))

    if os.name == "nt":
        tool = tool.with_suffix(".cmd")
    processor = AcpiProcessor(tool, tool_digest, tmp_path / "work")
    output = tmp_path / "output" / "ACPI"
    result = processor.build(capture, output, expected_bios_binding="N22ET85W-1.62")

    assert result.output_files == (
        "SSDT-PLUG-T480S.aml",
        "SSDT-PNLF-T480S.aml",
        "SSDT-USBX-T480S.aml",
    )
    assert result.tool_digest == tool_digest
    assert result.to_dict()["table_count"] == len(TABLE_NAMES)
    assert all((output / name).read_bytes() == b"AML" for name in result.output_files)
    assert (output.parent / "P4-ACPI-qualification.json").is_file()
    assert any(item.warnings == 1 for item in result.diagnostics)

    with pytest.raises(BuildPlanError, match="evidence digest"):
        processor.build(
            capture,
            tmp_path / "other-output",
            expected_bios_binding="N22ET85W-1.62",
            expected_evidence_digest="0" * 64,
        )


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
