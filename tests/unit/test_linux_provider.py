"""Unit tests for LinuxHardwareProvider handling sysfs/procfs enumeration, errors, and safety."""

import os
from pathlib import Path
from typing import Any, Optional, Tuple
from unittest.mock import patch

from click.testing import CliRunner
import pytest

from macloader.compatibility.engine import CompatibilityEngine
from macloader.database.loader import Database
from macloader.detection.linux import LinuxHardwareProvider
from macloader.domain.compatibility import CompatibilityState
from macloader.exceptions import HardwareDetectionError
from macloader.ui.cli import cli


def _create_baseline_linux_tree(tmp_path: Path) -> Tuple[Path, Path]:
    """Create a fully populated, valid mock sysfs and procfs tree."""
    sys_root = tmp_path / "sys"
    proc_root = tmp_path / "proc"

    # 1. DMI sysfs
    dmi_dir = sys_root / "class" / "dmi" / "id"
    dmi_dir.mkdir(parents=True, exist_ok=True)
    (dmi_dir / "sys_vendor").write_text("LENOVO\n")
    (dmi_dir / "product_name").write_text("20L7CTO1WW\n")
    (dmi_dir / "product_version").write_text("ThinkPad T480s\n")
    (dmi_dir / "bios_version").write_text("N22ET76W (1.53 )\n")
    (dmi_dir / "bios_date").write_text("01/18/2023\n")
    (dmi_dir / "product_serial").write_text("PF1ABCDE\n")
    (dmi_dir / "product_uuid").write_text("12345678-1234-1234-1234-123456789abc\n")

    # 2. /proc/cpuinfo
    proc_root.mkdir(parents=True, exist_ok=True)
    cpuinfo_content = """processor	: 0
vendor_id	: GenuineIntel
cpu family	: 6
model		: 142
model name	: Intel(R) Core(TM) i7-8550U CPU @ 1.80GHz
stepping	: 10
cpu cores	: 4
microcode	: 0xf4

processor	: 1
vendor_id	: GenuineIntel
cpu family	: 6
model		: 142
model name	: Intel(R) Core(TM) i7-8550U CPU @ 1.80GHz
stepping	: 10
cpu cores	: 4
microcode	: 0xf4
"""
    (proc_root / "cpuinfo").write_text(cpuinfo_content)

    # 3. PCI devices (portable slot naming for Windows CI compatibility)
    pci_base = sys_root / "bus" / "pci" / "devices"
    pci_base.mkdir(parents=True, exist_ok=True)

    def _add_pci(slot: str, ven: str, dev: str, subven: str, subdev: str, cls: str) -> Path:
        slot_name = slot.replace(":", "_") if os.name == "nt" else slot
        dev_dir = pci_base / slot_name
        dev_dir.mkdir(parents=True, exist_ok=True)
        (dev_dir / "vendor").write_text(f"0x{ven}\n")
        (dev_dir / "device").write_text(f"0x{dev}\n")
        (dev_dir / "subsystem_vendor").write_text(f"0x{subven}\n")
        (dev_dir / "subsystem_device").write_text(f"0x{subdev}\n")
        (dev_dir / "class").write_text(f"0x{cls}\n")
        return dev_dir

    _add_pci("0000:00:02.0", "8086", "5917", "17aa", "225c", "030000")  # UHD 620
    _add_pci("0000:00:1f.6", "8086", "15d8", "17aa", "225c", "020000")  # I219-V
    _add_pci("0000:01:00.0", "8086", "24fd", "8086", "0010", "028000")  # AC 8265
    _add_pci("0000:00:1f.3", "8086", "9d71", "17aa", "225c", "040300")  # HD Audio
    _add_pci("0000:02:00.0", "144d", "a808", "144d", "a801", "010802")  # NVMe PM981
    _add_pci("0000:00:14.0", "8086", "9d2f", "17aa", "225c", "0c0330")  # xHCI USB

    # 4. USB devices
    usb_base = sys_root / "bus" / "usb" / "devices" / "1-10"
    usb_base.mkdir(parents=True, exist_ok=True)
    (usb_base / "idVendor").write_text("0x8087\n")
    (usb_base / "idProduct").write_text("0x0a2b\n")
    (usb_base / "manufacturer").write_text("Intel Corp.\n")
    (usb_base / "product").write_text("Bluetooth Wireless Interface\n")

    # 5. Audio /proc/asound
    card_codec_dir = proc_root / "asound" / "card0"
    card_codec_dir.mkdir(parents=True, exist_ok=True)
    (card_codec_dir / "codec#0").write_text(
        "Codec: Realtek ALC257\nAddress: 0\nAFG Function Id: 0x1 (selected)\nVendor Id: 0x10ec0257\n"
    )

    # 6. /proc/bus/input/devices
    input_dir = proc_root / "bus" / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    input_content = """I: Bus=0011 Vendor=0001 Product=0001 Version=ab41
N: Name="AT Translated Set 2 keyboard"
P: Phys=isa0060/serio0/input0
S: Sysfs=/devices/platform/i8042/serio0/input/input0

I: Bus=001d Vendor=04f3 Product=2634 Version=0100
N: Name="ELAN061E:00 04F3:2634 Touchscreen"
P: Phys=
S: Sysfs=/devices/pci0000:00/0000:00:15.1/i2c_designware.1/i2c-1/i2c-ELAN061E:00/input/input10

I: Bus=0011 Vendor=0002 Product=0007 Version=01b1
N: Name="SynPS/2 Synaptics TouchPad"
P: Phys=isa0060/serio1/input0
S: Sysfs=/devices/platform/i8042/serio1/input/input3

I: Bus=0011 Vendor=0002 Product=000a Version=0000
N: Name="TPPS/2 IBM TrackPoint"
P: Phys=synaptics-pt/serio0/input0
S: Sysfs=/devices/platform/i8042/serio1/serio2/input/input4
"""
    (input_dir / "devices").write_text(input_content)

    # 7. /sys/class/block
    block_dir = sys_root / "class" / "block" / "nvme0n1" / "device"
    block_dir.mkdir(parents=True, exist_ok=True)
    (block_dir / "model").write_text("Samsung SSD 970 EVO Plus 500GB\n")

    return sys_root, proc_root


def test_linux_provider_reads_sysfs_and_proc(tmp_path: Path) -> None:
    """Basic smoke test asserting baseline probe on Linux."""
    sys_root, proc_root = _create_baseline_linux_tree(tmp_path)
    provider = LinuxHardwareProvider(sys_root=str(sys_root), proc_root=str(proc_root))
    snapshot = provider.probe()

    assert snapshot.manufacturer == "LENOVO"
    assert snapshot.product_name == "20L7CTO1WW"
    assert snapshot.machine_type == "20L7"
    assert snapshot.cpu is not None
    assert snapshot.cpu.model_name == "Intel(R) Core(TM) i7-8550U CPU @ 1.80GHz"
    assert snapshot.cpu.cores == 4
    assert snapshot.igpu is not None
    assert snapshot.igpu.pci.canonical_id == "8086:5917"
    assert len(snapshot.input_devices) == 3


def test_linux_provider_complete_baseline_is_build_eligible(tmp_path: Path, db: Database) -> None:
    """A fully readable, valid Linux sysfs snapshot produces complete inventory and is build-eligible."""
    sys_root, proc_root = _create_baseline_linux_tree(tmp_path)
    provider = LinuxHardwareProvider(sys_root=str(sys_root), proc_root=str(proc_root))
    snapshot = provider.probe()

    inv_status = snapshot.raw_evidence["inventory_status"]
    assert all(inv_status.values()), f"Expected all inventory categories complete: {inv_status}"

    engine = CompatibilityEngine(db=db)
    report = engine.evaluate(snapshot, target_macos="sequoia")

    assert report.overall_state in (CompatibilityState.SUPPORTED, CompatibilityState.EXPERIMENTAL, CompatibilityState.CONDITIONAL)
    assert report.can_generate_build_plan is True


def test_simulated_permission_error_on_pci_yields_uncertain_snapshot_and_denies_build_plan(
    tmp_path: Path, db: Database
) -> None:
    """PermissionError on /sys/bus/pci/devices keeps categories unknown rather than confirmed absent."""
    sys_root, proc_root = _create_baseline_linux_tree(tmp_path)
    provider = LinuxHardwareProvider(sys_root=str(sys_root), proc_root=str(proc_root))

    pci_dir = sys_root / "bus" / "pci" / "devices"

    # Simulate PermissionError on pci_dir.iterdir()
    def mock_iterdir(self_path: Path) -> Any:
        if self_path == pci_dir:
            raise PermissionError(f"[Errno 13] Permission denied: '{pci_dir}'")
        return original_iterdir(self_path)

    original_iterdir = Path.iterdir
    with patch.object(Path, "iterdir", mock_iterdir):
        # Also ensure lspci fallback fails so PCI device list stays empty
        with patch.object(provider, "_run_command", return_value=None):
            snapshot = provider.probe()

    # 1. No unhandled crash or traceback
    assert snapshot is not None

    # 2. Source errors tracked
    assert "pci" in snapshot.raw_evidence["source_errors"]
    assert any("Permission denied" in err for err in snapshot.raw_evidence["source_errors"]["pci"])

    # 3. Inventory completeness is marked False, not True
    inv_status = snapshot.raw_evidence["inventory_status"]
    assert inv_status["pci"] is False
    assert inv_status["ethernet"] is False
    assert inv_status["wifi"] is False
    assert inv_status["audio"] is False

    # 4. Compatibility evaluation marks unknown and denies build plan
    engine = CompatibilityEngine(db=db)
    report = engine.evaluate(snapshot, target_macos="sequoia")

    assert report.overall_state == CompatibilityState.UNKNOWN
    assert report.can_generate_build_plan is False


def test_simulated_permission_error_on_cpuinfo_yields_uncertain_cpu(tmp_path: Path, db: Database) -> None:
    """PermissionError on /proc/cpuinfo marks CPU uncertain and denies build plan."""
    sys_root, proc_root = _create_baseline_linux_tree(tmp_path)
    provider = LinuxHardwareProvider(sys_root=str(sys_root), proc_root=str(proc_root))

    cpuinfo_path = proc_root / "cpuinfo"

    def mock_read_text(self_path: Path, *args: Any, **kwargs: Any) -> str:
        if self_path == cpuinfo_path:
            raise PermissionError(f"[Errno 13] Permission denied: '{cpuinfo_path}'")
        return str(original_read_text(self_path, *args, **kwargs))

    original_read_text = Path.read_text
    with patch.object(Path, "read_text", mock_read_text):
        snapshot = provider.probe()

    assert snapshot.cpu is None
    assert "cpu" in snapshot.raw_evidence["source_errors"]
    assert snapshot.raw_evidence["inventory_status"]["cpu"] is False

    engine = CompatibilityEngine(db=db)
    report = engine.evaluate(snapshot, target_macos="sequoia")
    assert report.overall_state == CompatibilityState.UNKNOWN
    assert report.can_generate_build_plan is False


def test_broken_symlinks_handled_without_traceback(tmp_path: Path) -> None:
    """A broken driver symlink in a sysfs device directory does not raise an exception."""
    sys_root, proc_root = _create_baseline_linux_tree(tmp_path)

    # Create a broken symlink inside one of the PCI device dirs
    slot_name = "0000_00_02_0" if os.name == "nt" else "0000:00:02.0"
    dev_dir = sys_root / "bus" / "pci" / "devices" / slot_name
    broken_link = dev_dir / "driver"

    # On Windows, creating real symlinks may require SeCreateSymbolicLinkPrivilege.
    # We patch driver_link.resolve() to raise FileNotFoundError / OSError to deterministically simulate a broken symlink.
    provider = LinuxHardwareProvider(sys_root=str(sys_root), proc_root=str(proc_root))

    def mock_resolve(self_path: Path, *args: Any, **kwargs: Any) -> Path:
        if self_path.name == "driver":
            raise FileNotFoundError(f"No such file or directory: '{self_path}'")
        return original_resolve(self_path, *args, **kwargs)

    original_resolve = Path.resolve
    with patch.object(Path, "resolve", mock_resolve):
        snapshot = provider.probe()

    # The device is still discovered successfully
    assert snapshot.igpu is not None
    assert snapshot.igpu.pci.canonical_id == "8086:5917"
    assert snapshot.igpu.pci.driver is None


def test_disappearing_device_during_enumeration_handled_without_traceback(tmp_path: Path, db: Database) -> None:
    """A device node disappearing while reading attributes marks category uncertain and denies build plan."""
    sys_root, proc_root = _create_baseline_linux_tree(tmp_path)
    provider = LinuxHardwareProvider(sys_root=str(sys_root), proc_root=str(proc_root))

    # Simulate FileNotFoundError on one specific attribute read (disappearing Wi-Fi device)
    def mock_read_file(path: Path, category: Optional[str] = None) -> Optional[str]:
        if "01:00.0" in str(path) or "01_00.0" in str(path) or "0000_01_00_0" in str(path):
            return None  # Disappeared
        return original_read_file(path, category=category)

    original_read_file = provider._read_file
    with patch.object(provider, "_read_file", mock_read_file):
        snapshot = provider.probe()

    # 1. No unhandled crash
    assert snapshot is not None
    # 2. Other devices (like iGPU 8086:5917) are still enumerated
    assert snapshot.igpu is not None

    # 3. Disappearing PCI device triggers uncertainty: pci and wifi are incomplete
    inv_status = snapshot.raw_evidence["inventory_status"]
    assert inv_status["pci"] is False
    assert inv_status["wifi"] is False
    assert "pci" in snapshot.raw_evidence["source_errors"]

    # 4. Actionable false completeness is prevented: build plan denied!
    engine = CompatibilityEngine(db=db)
    report = engine.evaluate(snapshot, target_macos="sequoia")
    assert report.overall_state == CompatibilityState.UNKNOWN
    assert report.can_generate_build_plan is False


def test_empty_pci_directory_remains_uncertain(tmp_path: Path, db: Database) -> None:
    """An empty PCI directory with 0 devices does not produce confirmed-complete inventory."""
    sys_root, proc_root = _create_baseline_linux_tree(tmp_path)
    pci_dir = sys_root / "bus" / "pci" / "devices"

    # Remove all mock PCI devices
    for item in list(pci_dir.iterdir()):
        if item.is_dir():
            for f in item.iterdir():
                f.unlink()
            item.rmdir()

    provider = LinuxHardwareProvider(sys_root=str(sys_root), proc_root=str(proc_root))
    with patch.object(provider, "_run_command", return_value=None):
        snapshot = provider.probe()

    inv_status = snapshot.raw_evidence["inventory_status"]
    assert inv_status["pci"] is False
    assert inv_status["ethernet"] is False
    assert inv_status["wifi"] is False

    engine = CompatibilityEngine(db=db)
    report = engine.evaluate(snapshot, target_macos="sequoia")
    assert report.can_generate_build_plan is False


def test_raise_on_error_raises_hardware_detection_error(tmp_path: Path) -> None:
    """When raise_on_error=True, any enumeration error raises HardwareDetectionError."""
    sys_root, proc_root = _create_baseline_linux_tree(tmp_path)
    provider = LinuxHardwareProvider(sys_root=str(sys_root), proc_root=str(proc_root), raise_on_error=True)

    pci_dir = sys_root / "bus" / "pci" / "devices"

    def mock_iterdir(self_path: Path) -> Any:
        if self_path == pci_dir:
            raise PermissionError(f"[Errno 13] Permission denied: '{pci_dir}'")
        return original_iterdir(self_path)

    original_iterdir = Path.iterdir
    with patch.object(Path, "iterdir", mock_iterdir):
        with pytest.raises(HardwareDetectionError) as exc_info:
            provider.probe()

    assert "enumeration" in str(exc_info.value).lower() or "error" in str(exc_info.value).lower()


def test_inaccessible_sys_root_raises_when_raise_on_error(tmp_path: Path) -> None:
    """Inaccessible sys_root raises HardwareDetectionError when raise_on_error=True."""
    non_existent = tmp_path / "does_not_exist"
    proc_root = tmp_path / "proc"
    proc_root.mkdir(parents=True, exist_ok=True)
    provider = LinuxHardwareProvider(sys_root=str(non_existent), proc_root=str(proc_root), raise_on_error=True)

    with pytest.raises(HardwareDetectionError) as exc_info:
        provider.probe()

    assert "inaccessible" in str(exc_info.value).lower() or "error" in str(exc_info.value).lower()


def test_cli_probe_handles_hardware_detection_error_cleanly() -> None:
    """CLI prints controlled error to stderr and exits with 1 when HardwareDetectionError occurs on probe."""
    runner = CliRunner()

    with patch("macloader.orchestrator.Orchestrator.probe_hardware", side_effect=HardwareDetectionError("Simulated sysfs permission denied")):
        res = runner.invoke(cli, ["probe"])

    assert res.exit_code == 1
    assert "Detection Error:" in res.output or "Detection Error:" in getattr(res, "stderr", "")
    assert "Traceback" not in res.output


def test_cli_support_handles_hardware_detection_error_cleanly() -> None:
    """CLI prints controlled error to stderr and exits with 1 when HardwareDetectionError occurs on support."""
    runner = CliRunner()

    with patch("macloader.orchestrator.Orchestrator.probe_hardware", side_effect=HardwareDetectionError("Simulated procfs permission denied")):
        res = runner.invoke(cli, ["support", "-m", "sequoia"])

    assert res.exit_code == 1
    assert "Support Evaluation Error:" in res.output or "Support Evaluation Error:" in getattr(res, "stderr", "")
    assert "Traceback" not in res.output
