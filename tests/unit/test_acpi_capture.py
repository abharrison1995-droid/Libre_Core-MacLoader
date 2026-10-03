"""Direct capture uses synthetic tables only; no host firmware is read in CI."""
from dataclasses import replace
from pathlib import Path
import pytest

from macloader.build.acpi import AcpiProcessor, TABLE_NAMES
from macloader.evidence.acpi_capture import CaptureError, LinuxAcpiCaptureProvider, validate_capture
from macloader.autoloader.service import AutoloaderService
from tests.unit.test_autoloader import candidate


def table(signature: bytes, value: int = 0) -> bytes:
    data = bytearray(36)
    data[:4] = signature
    data[4:8] = (36).to_bytes(4, "little")
    data[10] = value
    data[9] = (-sum(data)) % 256
    return bytes(data)


def sysfs(root: Path) -> None:
    (root / "DSDT").write_bytes(table(b"DSDT"))
    for i in range(1, 12):
        (root / f"SSDT{i}").write_bytes(table(b"SSDT", i))


def test_exact_count_is_independent_of_filename_constant(tmp_path: Path) -> None:
    assert len(TABLE_NAMES) == 12  # one DSDT + eleven SSDTs (not twelve)
    sysfs(tmp_path)
    result = LinuxAcpiCaptureProvider(tmp_path).capture()
    assert len(result) == 12 and result["ssdt10.dat"] == table(b"SSDT", 11)


@pytest.mark.parametrize("change", ["missing", "extra", "malformed", "wrong-signature", "link"])
def test_capture_rejects_invalid_set(tmp_path: Path, change: str) -> None:
    sysfs(tmp_path)
    if change == "missing":
        (tmp_path / "SSDT1").unlink()
    elif change == "extra":
        (tmp_path / "SSDT12").write_bytes(table(b"SSDT"))
    elif change == "malformed":
        (tmp_path / "SSDT1").write_bytes(b"broken")
    elif change == "wrong-signature":
        (tmp_path / "SSDT1").write_bytes(table(b"DSDT"))
    else:
        (tmp_path / "SSDT1").unlink()
        try:
            (tmp_path / "SSDT1").symlink_to(tmp_path / "SSDT2")
        except OSError:
            pytest.skip("OS does not permit creating symlinks")
    with pytest.raises(ValueError):
        LinuxAcpiCaptureProvider(tmp_path).capture()


def test_changed_table_permission_cancellation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sysfs(tmp_path)
    provider = LinuxAcpiCaptureProvider(tmp_path)
    original = provider._read
    calls = 0
    def read(cancel):  # type: ignore[no-untyped-def]
        nonlocal calls
        result = original(cancel)
        calls += 1
        if calls == 2:
            result["ssdt.dat"] = table(b"SSDT", 50)
        return result
    monkeypatch.setattr(provider, "_read", read)
    with pytest.raises(CaptureError, match="changed"):
        provider.capture()
    with pytest.raises(CaptureError, match="paused"):
        provider.capture(lambda: True)
    monkeypatch.setattr(Path, "iterdir", lambda _self: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(CaptureError, match="grant read access"):
        original(lambda: False)


def test_workflow_binds_capture_and_rejects_changed_machine(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    sysfs(source)
    service = AutoloaderService(root=tmp_path / "private")
    service.start(candidate(), private_material="test")
    assert service.configuration is not None and service.snapshot is not None
    updated = service.workflow.collect_acpi(service.configuration, service.snapshot, LinuxAcpiCaptureProvider(source), lambda: service.snapshot)  # type: ignore[arg-type,return-value]
    record = next(r for r in updated.evidence if r.kind == "acpi")
    assert record.input_scope and Path(record.private_ref).is_file()
    assert str(source) not in Path(record.private_ref).read_text()
    with pytest.raises(CaptureError, match="Machine or BIOS"):
        service.workflow.collect_acpi(service.configuration, service.snapshot, LinuxAcpiCaptureProvider(source), lambda: replace(candidate(), bios_version="wrong"))
    with pytest.raises(CaptureError):
        validate_capture({"dsdt.dat": table(b"DSDT")})


def test_windows_capture_pins_version_count_and_protects_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib
    import subprocess
    from macloader.evidence.acpi_capture import WindowsAcpiCaptureProvider
    tool = tmp_path / "acpidump.exe"
    tool.write_bytes(b"synthetic-non-executable")
    dest = tmp_path / "capture"
    dest.mkdir()
    provider = WindowsAcpiCaptureProvider(tool, hashlib.sha256(tool.read_bytes()).hexdigest(), dest)
    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if args[-1] == "-v":
            return subprocess.CompletedProcess(args, 0, b"20260408", b"")
        for name in TABLE_NAMES:
            (dest / name).write_bytes(table(b"DSDT" if name == "dsdt.dat" else b"SSDT"))
        return subprocess.CompletedProcess(args, 0, b"", b"")
    monkeypatch.setattr(subprocess, "run", run)
    assert len(provider.capture()) == 12
    with pytest.raises(CaptureError, match="empty private"):
        provider.capture()
    provider.expected_sha256 = "0" * 64
    with pytest.raises(CaptureError, match="hash"):
        provider.capture()


def test_catalog_windows_assets_and_license_are_pinned() -> None:
    from macloader.toolchain.loader import TrustedToolchainLoader
    loader = TrustedToolchainLoader()
    record = loader.record("windows", "x86_64")
    assert record.opencore_version == "1.0.7"
    assert record.acpi_compiler.file_name.endswith(".exe")
    assert record.firmware_capture is not None
    assert record.firmware_capture.sha256 == "a0095a57521378c290d030db7ad196a27de2fcc770dd0347104ff49d19d792e0"
    notice = loader.catalog_path.parent.parent / "licenses" / "ACPICA-20260408.txt"
    assert "Copyright" in notice.read_text() and "NO WARRANTY" in notice.read_text()


@pytest.mark.parametrize("case", ["success", "cancel", "denied", "wrong-set", "missing-helper"])
def test_narrow_elevated_reader_has_fixed_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    import base64
    import json
    from macloader.evidence.acpi_capture import LinuxElevatedAcpiCaptureProvider, LINUX_READ_HELPER
    import macloader.evidence.acpi_capture as module
    provider = LinuxElevatedAcpiCaptureProvider()
    def denied(_self, _cancel):  # type: ignore[no-untyped-def]
        raise CaptureError("ACPI_PERMISSION", "permission")
    monkeypatch.setattr(LinuxAcpiCaptureProvider, "capture", denied)
    monkeypatch.setattr(module.shutil, "which", lambda _: None if case == "missing-helper" else "/usr/bin/pkexec")
    monkeypatch.setattr(Path, "is_file", lambda _: True)
    data = {"DSDT": base64.b64encode(table(b"DSDT")).decode()}
    data.update({f"SSDT{i}": base64.b64encode(table(b"SSDT", i)).decode() for i in range(1, 12)})
    if case == "wrong-set":
        data.pop("SSDT11")
    class Process:
        returncode = 1 if case == "denied" else 0
        def communicate(self, timeout: float) -> tuple[str, str]:
            return json.dumps(data), "private diagnostics never displayed"
    def popen(args: list[str], **kwargs: object) -> Process:
        assert args == ["pkexec", "/usr/bin/python3", "-I", "-c", LINUX_READ_HELPER]
        assert str(tmp_path) not in LINUX_READ_HELPER
        assert "os.O_RDONLY" in LINUX_READ_HELPER and "os.write" not in LINUX_READ_HELPER
        return Process()
    monkeypatch.setattr(module.subprocess, "Popen", popen)
    monkeypatch.setattr(AcpiProcessor, "_terminate_process", lambda _: None)
    if case == "success":
        assert len(provider.capture()) == 12
    else:
        with pytest.raises(CaptureError):
            provider.capture(lambda: case == "cancel")
