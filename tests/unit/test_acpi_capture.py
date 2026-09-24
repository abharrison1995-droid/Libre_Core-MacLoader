"""Linux ACPI capture route, exercised against a synthetic sysfs/DMI tree.

These tables are synthetic software-only evidence.  They prove that the
capture route produces exactly what the private importer accepts; they are
not evidence from any physical machine.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat

from click.testing import CliRunner
import pytest

import macloader.evidence.acpi_capture as capture_module
import macloader.workflow.service as workflow_module
from macloader.build.acpi import TABLE_NAMES
from macloader.configuration.store import ConfigurationStore
from macloader.evidence.acpi_capture import AcpiCaptureError, capture_acpi_tables, check_reference_machine, sudo_owner
from macloader.ui.cli import cli
from macloader.workflow.service import WorkflowService


pytestmark = pytest.mark.skipif(os.name == "nt", reason="Linux sysfs capture route")

SSDT_COUNT = 11  # pinned to the reviewed real capture, not derived from the importer
assert len(TABLE_NAMES) == SSDT_COUNT + 1


def _table(signature: str, body: bytes) -> bytes:
    data = bytearray(36 + len(body))
    data[:4] = signature.encode("ascii")
    data[4:8] = len(data).to_bytes(4, "little")
    data[8] = 2
    data[10:16] = b"LENOVO"
    data[16:24] = b"SYNTHTBL"
    data[36:] = body
    data[9] = (-sum(data)) % 256
    return bytes(data)


def _sysfs(root: Path, ssdts: int = SSDT_COUNT) -> tuple[Path, Path]:
    tables = root / "tables"
    tables.mkdir(parents=True)
    (tables / "DSDT").write_bytes(_table("DSDT", b"synthetic-dsdt"))
    # Firmware order is numeric; lexical order would put SSDT10 before SSDT2.
    for index in range(1, ssdts + 1):
        (tables / f"SSDT{index}").write_bytes(_table("SSDT", f"synthetic-ssdt-{index}".encode()))
    (tables / "FACP").write_bytes(_table("FACP", b"ignored"))
    (tables / "dynamic").mkdir()
    (tables / "dynamic" / "SSDT99").write_bytes(_table("SSDT", b"runtime"))
    dmi = root / "dmi"
    dmi.mkdir()
    for name, value in {
        "sys_vendor": "LENOVO", "product_name": "20L8SYNTH0",
        "product_version": "ThinkPad T480s", "bios_version": "N22ET85W (1.62 )",
    }.items():
        (dmi / name).write_text(value + "\n", encoding="ascii")
    return tables, dmi


def test_capture_writes_importer_layout_privately_in_firmware_order(tmp_path: Path) -> None:
    tables, dmi = _sysfs(tmp_path / "sys")
    destination = tmp_path / "private-capture"
    result = capture_acpi_tables(
        destination, tables_root=tables, dmi_root=dmi, now=datetime(2026, 9, 24, tzinfo=timezone.utc)
    )

    table_dir = destination / "PRIVATE-ACPI"
    assert sorted(path.name for path in table_dir.glob("*.dat")) == sorted(TABLE_NAMES)
    assert (table_dir / "ssdt.dat").read_bytes() == (tables / "SSDT1").read_bytes()
    assert (table_dir / "ssdt1.dat").read_bytes() == (tables / "SSDT2").read_bytes()
    assert (table_dir / "ssdt10.dat").read_bytes() == (tables / f"SSDT{SSDT_COUNT}").read_bytes()
    assert not (table_dir / "ssdt11.dat").exists()
    assert b"runtime" not in b"".join(path.read_bytes() for path in table_dir.iterdir())
    for path in (destination, table_dir):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
    for path in table_dir.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    manifest = json.loads((destination / "capture-manifest.json").read_text(encoding="utf-8"))
    assert manifest["bios_binding"] == "N22ET85W-1.62" and manifest["machine_type"] == "20L8"
    assert [item["firmware_name"] for item in manifest["tables"]][:3] == ["DSDT", "SSDT1", "SSDT2"]
    sums = (table_dir / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    assert len(sums) == len(TABLE_NAMES)
    summary = result.summary()
    assert summary["ssdt_count"] == SSDT_COUNT and summary["dsdt_count"] == 1
    assert "synthetic" not in json.dumps(summary)


def test_captured_directory_is_accepted_by_the_private_importer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path
) -> None:
    tables, dmi = _sysfs(tmp_path / "sys")
    destination = tmp_path / "capture"
    capture_acpi_tables(destination, tables_root=tables, dmi_root=dmi)
    monkeypatch.setattr(workflow_module, "DEFAULT_PRIVATE_DIR", tmp_path / "workspace" / "private")
    monkeypatch.setattr(workflow_module, "DEFAULT_ACPI_DIR", tmp_path / "workspace" / "private" / "acpi")
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    configuration, snapshot = service.create(fixtures_dir / "t480s" / "t480s_20l8_bios162_synthetic.json")
    updated, record = service.import_acpi_capture(configuration, snapshot, destination, allow_synthetic_snapshot=True)
    assert record.completeness.value == "complete"
    assert any(item.kind == "acpi" for item in updated.evidence)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("product_name", "20L7CTO1WW", "not a Lenovo 20L8"),
        ("sys_vendor", "Dell Inc.", "not a Lenovo 20L8"),
        ("product_version", "ThinkPad T480", "does not identify a ThinkPad T480s"),
        ("bios_version", "N22ET76W (1.53 )", "not the reviewed N22ET85W"),
    ],
)
def test_capture_refuses_other_machines_and_bios(tmp_path: Path, field: str, value: str, message: str) -> None:
    tables, dmi = _sysfs(tmp_path / "sys")
    (dmi / field).write_text(value, encoding="ascii")
    destination = tmp_path / "capture"
    with pytest.raises(AcpiCaptureError, match=message):
        capture_acpi_tables(destination, tables_root=tables, dmi_root=dmi)
    assert not destination.exists()


def test_capture_refuses_missing_dmi(tmp_path: Path) -> None:
    with pytest.raises(AcpiCaptureError, match="unavailable"):
        check_reference_machine(tmp_path)


@pytest.mark.parametrize("ssdts", [SSDT_COUNT - 1, SSDT_COUNT + 1])
def test_capture_refuses_a_table_count_the_profile_does_not_cover(tmp_path: Path, ssdts: int) -> None:
    tables, dmi = _sysfs(tmp_path / "sys", ssdts=ssdts)
    destination = tmp_path / "capture"
    with pytest.raises(AcpiCaptureError, match=f"exposes {ssdts} static SSDTs"):
        capture_acpi_tables(destination, tables_root=tables, dmi_root=dmi)
    assert not destination.exists()


def test_capture_refuses_corrupt_tables_and_missing_dsdt(tmp_path: Path) -> None:
    tables, dmi = _sysfs(tmp_path / "sys")
    corrupt = bytearray((tables / "SSDT3").read_bytes())
    corrupt[-1] ^= 0xFF
    (tables / "SSDT3").write_bytes(bytes(corrupt))
    with pytest.raises(AcpiCaptureError, match="checksum"):
        capture_acpi_tables(tmp_path / "a", tables_root=tables, dmi_root=dmi)
    (tables / "SSDT3").write_bytes(_table("DSDT", b"wrong-signature"))
    with pytest.raises(AcpiCaptureError, match="unexpected signature"):
        capture_acpi_tables(tmp_path / "b", tables_root=tables, dmi_root=dmi)
    (tables / "DSDT").unlink()
    with pytest.raises(AcpiCaptureError, match="exactly one DSDT"):
        capture_acpi_tables(tmp_path / "c", tables_root=tables, dmi_root=dmi)
    with pytest.raises(AcpiCaptureError, match="does not expose firmware ACPI tables"):
        capture_acpi_tables(tmp_path / "d", tables_root=tmp_path / "absent", dmi_root=dmi)
    assert not any((tmp_path / name).exists() for name in "abcd")


def test_capture_refuses_unsafe_destinations_and_symlinked_tables(tmp_path: Path) -> None:
    tables, dmi = _sysfs(tmp_path / "sys")
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(AcpiCaptureError, match="already exists"):
        capture_acpi_tables(existing, tables_root=tables, dmi_root=dmi)
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    with pytest.raises(AcpiCaptureError, match="inside a Git checkout"):
        capture_acpi_tables(checkout / "acpi", tables_root=tables, dmi_root=dmi)
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "target", target_is_directory=True)
    with pytest.raises(AcpiCaptureError, match="symlink"):
        capture_acpi_tables(link / "acpi", tables_root=tables, dmi_root=dmi)
    (tables / "SSDT4").unlink()
    (tables / "SSDT4").symlink_to(tables / "SSDT5")
    with pytest.raises(AcpiCaptureError, match="cannot be opened safely"):
        capture_acpi_tables(tmp_path / "capture", tables_root=tables, dmi_root=dmi)


def test_capture_reports_root_requirement_and_bounds_table_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tables, dmi = _sysfs(tmp_path / "sys")

    def denied(*_args: object, **_kwargs: object) -> int:
        raise PermissionError("denied")

    with monkeypatch.context() as patch:
        patch.setattr(capture_module.os, "open", denied)
        with pytest.raises(AcpiCaptureError, match="rerun the capture with sudo"):
            capture_module._read_table(tables / "DSDT")
    monkeypatch.setattr(capture_module, "MAX_ACPI_TABLE_BYTES", 16)
    with pytest.raises(AcpiCaptureError, match="safe size limit"):
        capture_acpi_tables(tmp_path / "capture", tables_root=tables, dmi_root=dmi)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(AcpiCaptureError, match="not a regular file"):
        capture_module._read_table(fifo)


def test_capture_rolls_back_a_partial_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tables, dmi = _sysfs(tmp_path / "sys")
    calls = {"count": 0}
    original = capture_module._write_private

    def failing(path: Path, data: bytes) -> None:
        calls["count"] += 1
        if calls["count"] == 5:
            raise OSError("disk full")
        original(path, data)

    monkeypatch.setattr(capture_module, "_write_private", failing)
    with pytest.raises(OSError, match="disk full"):
        capture_acpi_tables(tmp_path / "capture", tables_root=tables, dmi_root=dmi)
    assert not (tmp_path / "capture").exists()


def test_sudo_owner_only_applies_to_root_with_sudo_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture_module.os, "geteuid", lambda: 0)
    assert sudo_owner({"SUDO_UID": "1000", "SUDO_GID": "1000"}) == (1000, 1000)
    assert sudo_owner({"SUDO_UID": "0", "SUDO_GID": "0"}) is None
    assert sudo_owner({}) is None
    monkeypatch.setattr(capture_module.os, "geteuid", lambda: 1000)
    assert sudo_owner({"SUDO_UID": "1000", "SUDO_GID": "1000"}) is None


def test_capture_hands_ownership_to_the_sudo_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tables, dmi = _sysfs(tmp_path / "sys")
    owned: list[str] = []
    monkeypatch.setattr(capture_module.os, "chown", lambda path, *_args, **_kwargs: owned.append(Path(path).name))
    capture_acpi_tables(tmp_path / "capture", tables_root=tables, dmi_root=dmi, owner=(1000, 1000))
    assert {"capture", "PRIVATE-ACPI", "capture-manifest.json", "dsdt.dat", "SHA256SUMS"} <= set(owned)


def test_cli_capture_reports_next_step_and_refusals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tables, dmi = _sysfs(tmp_path / "sys")
    original = capture_module.capture_acpi_tables
    monkeypatch.setattr(
        capture_module, "capture_acpi_tables",
        lambda destination, owner=None: original(destination, tables_root=tables, dmi_root=dmi, owner=owner),
    )
    result = CliRunner().invoke(cli, ["evidence", "acpi-capture", str(tmp_path / "capture")])
    assert result.exit_code == 0, result.output
    assert f"Next: macloader evidence acpi-import CONFIG_ID {tmp_path / 'capture'}" in result.output.replace("\n", "")
    as_json = CliRunner().invoke(cli, ["evidence", "acpi-capture", str(tmp_path / "capture2"), "--json"])
    assert json.loads(as_json.stdout)["table_count"] == len(TABLE_NAMES)
    refused = CliRunner().invoke(cli, ["evidence", "acpi-capture", str(tmp_path / "capture")])
    assert refused.exit_code != 0 and "already exists" in refused.output


def test_synthetic_snapshot_cannot_satisfy_real_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path
) -> None:
    tables, dmi = _sysfs(tmp_path / "sys")
    destination = tmp_path / "capture"
    capture_acpi_tables(destination, tables_root=tables, dmi_root=dmi)
    monkeypatch.setattr(workflow_module, "DEFAULT_PRIVATE_DIR", tmp_path / "workspace" / "private")
    monkeypatch.setattr(workflow_module, "DEFAULT_ACPI_DIR", tmp_path / "workspace" / "private" / "acpi")
    service = WorkflowService(store=ConfigurationStore(tmp_path / "configs"))
    configuration, snapshot = service.create(fixtures_dir / "t480s" / "t480s_20l8_bios162_synthetic.json")
    with pytest.raises(ValueError, match="synthetic software-only snapshot"):
        service.import_acpi_capture(configuration, snapshot, destination)
    with pytest.raises(ValueError, match="synthetic software-only snapshot"):
        service.build_efi_preview(configuration, snapshot, tmp_path / "efi")
    report = service.preflight(None, snapshot)
    reference = next(item for item in report["checks"] if item["id"] == "reference_machine")
    assert reference["state"] == "blocked" and "synthetic" in reference["summary"]
