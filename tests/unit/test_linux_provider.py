"""Unit tests for LinuxHardwareProvider using isolated filesystem mocks."""

from pathlib import Path
from macloader.detection.linux import LinuxHardwareProvider


def test_linux_provider_reads_sysfs_and_proc(tmp_path: Path) -> None:
    sys_root = tmp_path / "sys"
    proc_root = tmp_path / "proc"

    # Setup mock DMI sysfs
    dmi_dir = sys_root / "class" / "dmi" / "id"
    dmi_dir.mkdir(parents=True)
    (dmi_dir / "sys_vendor").write_text("LENOVO\n")
    (dmi_dir / "product_name").write_text("20L7CTO1WW\n")
    (dmi_dir / "product_version").write_text("ThinkPad T480s\n")
    (dmi_dir / "bios_version").write_text("N22ET76W (1.53 )\n")
    (dmi_dir / "bios_date").write_text("01/18/2023\n")
    (dmi_dir / "product_serial").write_text("PF1ABCDE\n")
    (dmi_dir / "product_uuid").write_text("12345678-1234-1234-1234-123456789abc\n")

    # Setup mock cpuinfo
    proc_root.mkdir(parents=True)
    cpuinfo_content = """processor	: 0
vendor_id	: GenuineIntel
cpu family	: 6
model		: 142
model name	: Intel(R) Core(TM) i7-8550U CPU @ 1.80GHz
stepping	: 10
microcode	: 0xf4

processor	: 1
vendor_id	: GenuineIntel
cpu family	: 6
model		: 142
model name	: Intel(R) Core(TM) i7-8550U CPU @ 1.80GHz
stepping	: 10
microcode	: 0xf4
"""
    (proc_root / "cpuinfo").write_text(cpuinfo_content)

    # Setup mock PCI devices
    pci_dir = sys_root / "bus" / "pci" / "devices" / "0000:00:02.0"
    pci_dir.mkdir(parents=True)
    (pci_dir / "vendor").write_text("0x8086\n")
    (pci_dir / "device").write_text("0x5917\n")
    (pci_dir / "subsystem_vendor").write_text("0x17aa\n")
    (pci_dir / "subsystem_device").write_text("0x225c\n")
    (pci_dir / "class").write_text("0x030000\n")

    # Setup mock input devices
    (proc_root / "bus" / "input").mkdir(parents=True)
    input_content = """I: Bus=001d Vendor=04f3 Product=2634 Version=0100
N: Name="ELAN061E:00 04F3:2634 Touchscreen"
P: Phys=
S: Sysfs=/devices/pci0000:00/0000:00:15.1/i2c_designware.1/i2c-1/i2c-ELAN061E:00/input/input10
U: Uniq=
H: Handlers=event10 
B: PROP=2
B: EV=b
B: KEY=400 0 0 0 0 0
B: ABS=260800000000003

"""
    (proc_root / "bus" / "input" / "devices").write_text(input_content)

    provider = LinuxHardwareProvider(sys_root=str(sys_root), proc_root=str(proc_root))
    snapshot = provider.probe()

    assert snapshot.manufacturer == "LENOVO"
    assert snapshot.product_name == "20L7CTO1WW"
    assert snapshot.machine_type == "20L7"
    assert snapshot.cpu is not None
    assert snapshot.cpu.model_name == "Intel(R) Core(TM) i7-8550U CPU @ 1.80GHz"
    assert snapshot.cpu.cores == 2
    assert snapshot.igpu is not None
    assert snapshot.igpu.pci.canonical_id == "8086:5917"
    assert len(snapshot.input_devices) == 1
    assert snapshot.input_devices[0].kind == "touchscreen"
