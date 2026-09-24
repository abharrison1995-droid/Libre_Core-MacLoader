"""P7 platform adapter and immutable-plan regression tests."""

from dataclasses import replace
import json
from pathlib import Path
import shutil
import os
import subprocess
from typing import Any, Optional, cast
from unittest import mock

import pytest

from macloader.removable import (
    DestructiveConfirmation,
    LinuxRemovableAdapter,
    MediaBindings,
    RemovableDevice,
    UnsafeRemovableTarget,
    WindowsRemovableAdapter,
    current_adapter,
)
from macloader.removable.writer import RemovableMediaWriter
from macloader.removable.adapters import LinuxBlockDeviceBackend
import macloader.removable.adapters as adapters_module
import macloader.removable.writer as writer_module


def _empty_linux_runner(_args: list[str], _input: Optional[str] = None) -> str:
    return ""


QUALIFIED = MediaBindings("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64, "f" * 64)


def test_windows_discovery_uses_whole_disk_identity_and_redacts_serial() -> None:
    adapter = WindowsRemovableAdapter(
        runner=lambda _script: '[{"Number": 3, "FriendlyName": "USB Disk", "SerialNumber": "SECRET-SERIAL", "Size": 1000, "BusType": "USB", "IsBoot": false, "IsSystem": false, "IsReadOnly": false, "OperationalStatus": "Online", "Mounted": false, "IsRemovable": true}]',
        platform="win32",
    )
    devices = adapter.enumerate()
    assert devices[0].device_id == "windows:serial:SECRET-SERIAL"
    assert devices[0].is_removable is True
    assert adapter.status.advertised is True
    assert adapter.status.qualified is False
    assert "SECRET-SERIAL" not in adapter.status.reason


def test_windows_discovery_rejects_malformed_rows() -> None:
    adapter = WindowsRemovableAdapter(runner=lambda _script: "[1]", platform="win32")
    with pytest.raises(UnsafeRemovableTarget, match="non-object row"):
        adapter.enumerate()


def test_windows_discovery_parses_single_row_and_whole_disk_safety_fields() -> None:
    adapter = WindowsRemovableAdapter(
        runner=lambda _script: '{"Number": 4, "FriendlyName": " USB Disk ", "SerialNumber": "  ", "Size": "1000", "BusType": "USB", "IsBoot": "1", "IsReadOnly": "yes", "OperationalStatus": "Mounted", "Partitions": [1, "", null], "IsWholeDevice": false}',
        platform="win32",
    )
    device = adapter.enumerate()[0]
    assert device.device_id == "windows:physical:4"
    assert device.model == "USB Disk"
    assert device.capacity_bytes == 1000
    assert device.is_system_disk is True
    assert device.is_removable is True
    assert device.mounted is True
    assert device.read_only is True
    assert device.partitions == ("1",)
    assert device.whole_device is False
    assert device.system_disk_ref == device.device_id


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("not-json", "malformed JSON"),
        ("null", "invalid shape"),
        ('[{"Number": true, "FriendlyName": "USB", "Size": 1}]', "invalid disk number"),
        ('[{"Number": -1, "FriendlyName": "USB", "Size": 1}]', "negative disk number"),
        ('[{"Number": 1, "Size": 1}]', "no model"),
        ('[{"Number": 1, "FriendlyName": "USB", "Size": -1}]', "negative capacity"),
    ],
)
def test_windows_discovery_rejects_invalid_metadata(raw: str, message: str) -> None:
    adapter = WindowsRemovableAdapter(runner=lambda _script: raw, platform="win32")
    with pytest.raises(UnsafeRemovableTarget, match=message):
        adapter.enumerate()


def test_windows_adapter_is_unavailable_off_host() -> None:
    adapter = WindowsRemovableAdapter(platform="linux")
    assert adapter.status.to_dict() == {
        "platform": "windows",
        "advertised": False,
        "qualified": False,
        "reason": "Windows adapter is unavailable on this host",
    }
    assert adapter.enumerate() == []


class IncompleteWindowsBackend:
    pass


def test_windows_backend_status_fails_closed_when_methods_are_missing() -> None:
    adapter = WindowsRemovableAdapter(
        backend=cast(Any, IncompleteWindowsBackend()), platform="win32"
    )
    assert adapter.status.qualified is False
    assert "lacks" in adapter.status.reason


def test_windows_backend_rejects_invalid_plan_and_safe_noops_for_invalid_readback() -> None:
    backend = FakeWindowsBackend()
    adapter = WindowsRemovableAdapter(backend=backend, platform="win32", synthetic_test_mode=True)
    with pytest.raises(UnsafeRemovableTarget, match="invalid media plan"):
        adapter.write(object(), Path("."))
    assert adapter.readback(object(), Path(".")) is False
    adapter.invalidate(object(), "ignored")
    assert backend.events == []


def test_windows_backend_failure_invalidates_and_remounts(
    valid_source: Path,
) -> None:
    backend = FailingWindowsBackend()
    adapter = WindowsRemovableAdapter(
        runner=lambda _script: '[{"Number": 3, "FriendlyName": "USB Disk", "SerialNumber": "SERIAL", "Size": 1000, "BusType": "USB", "Mounted": false, "IsRemovable": true}]',
        backend=backend,
        platform="win32",
        synthetic_test_mode=True,
    )
    device = adapter.enumerate()[0]
    plan = RemovableMediaWriter().dry_run(device, 1, source_dir=valid_source, bindings=QUALIFIED)
    with pytest.raises(OSError, match="simulated write failure"):
        adapter.write(plan, valid_source)
    assert backend.events == ["lock-dismount", "write", "invalidate", "remount"]


def test_windows_backend_invalidates_failed_readback_before_remount(
    valid_source: Path,
) -> None:
    backend = FalseReadbackWindowsBackend()
    adapter = WindowsRemovableAdapter(
        runner=lambda _script: '[{"Number": 3, "FriendlyName": "USB Disk", "SerialNumber": "SERIAL", "Size": 1000, "BusType": "USB", "Mounted": false, "IsRemovable": true}]',
        backend=backend,
        platform="win32",
        synthetic_test_mode=True,
    )
    device = adapter.enumerate()[0]
    plan = RemovableMediaWriter().dry_run(device, 1, source_dir=valid_source, bindings=QUALIFIED)
    assert adapter.readback(plan, valid_source) is False
    adapter.invalidate(plan, "test invalidation")
    assert backend.events == ["readback", "invalidate", "remount"]


def test_windows_discovery_fails_closed_when_mount_state_is_missing() -> None:
    adapter = WindowsRemovableAdapter(
        runner=lambda _script: '[{"Number": 3, "FriendlyName": "USB Disk", "SerialNumber": "SERIAL", "Size": 1000, "BusType": "USB", "IsRemovable": true}]',
        platform="win32",
    )
    assert adapter.enumerate()[0].mounted is True


def test_windows_online_disk_with_mounted_volume_is_rejected_by_writer() -> None:
    adapter = WindowsRemovableAdapter(
        runner=lambda _script: '[{"Number": 3, "FriendlyName": "USB Disk", "SerialNumber": "SERIAL", "Size": 1000, "BusType": "USB", "Mounted": true, "IsRemovable": true}]',
        platform="win32",
    )
    with pytest.raises(UnsafeRemovableTarget, match="mounted"):
        RemovableMediaWriter().dry_run(adapter.enumerate()[0], 1)


def test_windows_adapter_never_writes_without_qualified_backend(tmp_path: Path) -> None:
    adapter = WindowsRemovableAdapter(runner=lambda _script: "[]", platform="win32")
    with pytest.raises(UnsafeRemovableTarget, match="not qualified"):
        adapter.write(object(), tmp_path)


class FakeWindowsBackend:
    production_qualified = True

    def __init__(self) -> None:
        self.events: list[str] = []

    def lock_and_dismount(self, device: RemovableDevice) -> None:
        self.events.append("lock-dismount")

    def write(self, plan: object, source_dir: Path) -> None:
        self.events.append("write")

    def flush(self, device: RemovableDevice) -> None:
        self.events.append("flush")

    def readback(self, plan: object, source_dir: Path) -> bool:
        self.events.append("readback")
        return True

    def invalidate(self, device: RemovableDevice, reason: str) -> None:
        self.events.append("invalidate")

    def remount(self, device: RemovableDevice) -> None:
        self.events.append("remount")


class FailingWindowsBackend(FakeWindowsBackend):
    def write(self, plan: object, source_dir: Path) -> None:
        self.events.append("write")
        raise OSError("simulated write failure")


class FalseReadbackWindowsBackend(FakeWindowsBackend):
    def readback(self, plan: object, source_dir: Path) -> bool:
        self.events.append("readback")
        return False


def test_windows_qualified_backend_lifecycle_is_ordered(valid_source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercise the platform-specific no-follow snapshot branch on every host;
    # the real Windows run covers the native filesystem implementation too.
    monkeypatch.setattr(writer_module, "_WINDOWS_PLATFORM", True)
    backend = FakeWindowsBackend()
    adapter = WindowsRemovableAdapter(
        runner=lambda _script: '[{"Number": 3, "FriendlyName": "USB Disk", "SerialNumber": "SERIAL", "Size": 1000, "BusType": "USB", "Mounted": false, "IsRemovable": true}]',
        backend=backend,
        platform="win32",
        synthetic_test_mode=True,
    )
    assert adapter.status.qualified is True
    device = adapter.enumerate()[0]
    plan = RemovableMediaWriter().dry_run(device, 1, source_dir=valid_source, bindings=QUALIFIED)
    writer = adapter.writer()
    writer.write(plan, valid_source, DestructiveConfirmation.issue(plan))
    assert backend.events == ["lock-dismount", "write", "flush", "readback", "remount"]


def test_writer_invalidates_after_post_write_cancellation(valid_source: Path) -> None:
    backend = FakeWindowsBackend()
    adapter = WindowsRemovableAdapter(
        runner=lambda _script: '[{"Number": 3, "FriendlyName": "USB Disk", "SerialNumber": "SERIAL", "Size": 1000, "BusType": "USB", "Mounted": false, "IsRemovable": true}]',
        backend=backend,
        platform="win32",
        synthetic_test_mode=True,
    )
    device = adapter.enumerate()[0]
    writer = adapter.writer()
    plan = writer.dry_run(device, 1, source_dir=valid_source, bindings=QUALIFIED)
    with pytest.raises(UnsafeRemovableTarget, match="cancelled"):
        writer.write(plan, valid_source, DestructiveConfirmation.issue(plan), cancel=lambda: True if backend.events.count("write") else False)
    assert "invalidate" in backend.events
    assert backend.events[-1] == "remount"


def test_linux_is_disabled_until_explicitly_advertised() -> None:
    adapter = LinuxRemovableAdapter()
    assert adapter.status.to_dict() == {
        "platform": "linux",
        "advertised": False,
        "qualified": False,
        "reason": "Linux removable-media support is not advertised",
    }
    assert adapter.enumerate() == []


def test_linux_advertised_discovery_remains_nonqualified() -> None:
    device = RemovableDevice("linux:serial:1", "USB", 1024, False, True, False, serial="1")
    adapter = LinuxRemovableAdapter(advertised=True, enumerator=lambda: [device])
    assert adapter.status.qualified is False
    assert "qualification" in adapter.status.reason
    assert adapter.enumerate() == [device]


def test_linux_discovery_uses_stable_by_id_and_rejects_system_internal_and_mounted_disks(
    tmp_path: Path,
) -> None:
    internal_node = tmp_path / "internal-disk"
    usb_node = tmp_path / "usb-disk"
    internal_node.touch()
    usb_node.touch()
    by_id = tmp_path / "by-id"
    by_id.mkdir()
    (by_id / "wwn-0xINTERNAL001").symlink_to(internal_node)
    (by_id / "usb-MAKER_MODEL_SERIAL001-0:0").symlink_to(usb_node)
    payload = {"blockdevices": [
        {
            "name": "sda", "path": str(internal_node), "type": "disk", "size": 500_000_000,
            "model": "Internal SSD", "serial": "INTERNAL001", "wwn": "0xINTERNAL001",
            "rm": False, "ro": False, "tran": "sata", "maj:min": "8:0", "mountpoints": [None],
            "children": [{"name": "sda2", "path": "/dev/sda2", "type": "part", "size": 400_000_000,
                          "maj:min": "8:2", "mountpoints": ["/"]}],
        },
        {
            "name": "sdb", "path": str(usb_node), "type": "disk", "size": 64_000_000,
            "model": "External USB", "serial": "SERIAL001", "wwn": None,
            "rm": True, "ro": False, "tran": "usb", "maj:min": "8:16", "mountpoints": [None],
            "children": [{"name": "sdb1", "path": "/dev/sdb1", "type": "part", "size": 60_000_000,
                          "maj:min": "8:17", "mountpoints": [None]}],
        },
    ]}

    def runner(args: list[str], _input: str | None = None) -> str:
        if args[0] == "lsblk":
            return json.dumps(payload)
        if args[0] == "findmnt":
            return "8:2\n"
        raise AssertionError(f"unexpected discovery command {args[0]}")

    adapter = LinuxRemovableAdapter(advertised=True, runner=runner, by_id_root=by_id)
    internal, usb = adapter.enumerate()
    assert internal.device_id == "linux:by-id:wwn-0xINTERNAL001"
    assert internal.is_system_disk is True and internal.is_removable is False
    assert usb.device_id == "linux:by-id:usb-MAKER_MODEL_SERIAL001-0:0"
    assert usb.is_removable is True and usb.is_system_disk is False and usb.mounted is False
    assert adapter.status.qualified is False
    assert "sacrificial" in adapter.status.reason


def test_linux_unqualified_adapter_cannot_enter_physical_write_path(valid_source: Path) -> None:
    adapter = LinuxRemovableAdapter(advertised=True, enumerator=lambda: [])
    device = RemovableDevice("linux:by-id:usb-MAKER_USB001-0:0", "USB", 64_000_000, False, True, False, serial="USB001")
    plan = RemovableMediaWriter().dry_run(device, 1, source_dir=valid_source, bindings=QUALIFIED)
    with pytest.raises(UnsafeRemovableTarget, match="not physically qualified"):
        adapter.write(plan, valid_source)


def test_linux_backend_rejects_unstable_identities_and_small_partition_layout(tmp_path: Path) -> None:
    with pytest.raises(UnsafeRemovableTarget, match="stable whole-device"):
        LinuxBlockDeviceBackend._device_path(RemovableDevice("windows:disk:1", "USB", 8_000_000, False, True, False))
    with pytest.raises(UnsafeRemovableTarget, match="malformed"):
        LinuxBlockDeviceBackend._device_path(RemovableDevice("linux:by-id:../sda", "USB", 8_000_000, False, True, False))

    monkeypatch_root = tmp_path / "by-id"
    monkeypatch_root.mkdir()
    original = LinuxBlockDeviceBackend._by_id_root
    LinuxBlockDeviceBackend._by_id_root = monkeypatch_root
    try:
        layout = LinuxBlockDeviceBackend._partition_script(100_000)
        assert "label: gpt" in layout and "type=uefi" in layout
        with pytest.raises(UnsafeRemovableTarget, match="too small"):
            LinuxBlockDeviceBackend._partition_script(2048)
    finally:
        LinuxBlockDeviceBackend._by_id_root = original


def test_linux_backend_rechecks_identity_before_block_device_access(tmp_path: Path) -> None:
    device = RemovableDevice("linux:by-id:usb-MAKER_TEST001-0:0", "USB", 8_000_000, False, True, False, serial="TEST001")
    backend = LinuxBlockDeviceBackend(lambda: [device], runner=_empty_linux_runner)
    alias_root = tmp_path / "by-id"
    alias_root.mkdir()
    original = LinuxBlockDeviceBackend._by_id_root
    LinuxBlockDeviceBackend._by_id_root = alias_root
    try:
        with pytest.raises(UnsafeRemovableTarget, match="identity changed since planning"):
            backend._current(replace(device, capacity_bytes=device.capacity_bytes + 512))
        with pytest.raises(UnsafeRemovableTarget, match="whole-device alias is missing"):
            backend._current(device)
        with pytest.raises(UnsafeRemovableTarget, match="missing or ambiguous"):
            LinuxBlockDeviceBackend(lambda: [device, device], runner=_empty_linux_runner)._current(device)
    finally:
        LinuxBlockDeviceBackend._by_id_root = original


def test_linux_backend_lock_preconditions_and_command_failures_are_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = RemovableDevice("linux:by-id:usb-MAKER_GUARD001-0:0", "USB", 8_000_000, False, True, False, serial="GUARD001")
    backend = LinuxBlockDeviceBackend(lambda: [device])
    monkeypatch.setattr(adapters_module.os, "geteuid", lambda: 1000)
    with pytest.raises(UnsafeRemovableTarget, match="requires root privileges"):
        backend.lock_and_dismount(device)
    monkeypatch.setattr(adapters_module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(adapters_module, "_fcntl", None)
    with pytest.raises(UnsafeRemovableTarget, match="exclusive device locking"):
        backend.lock_and_dismount(device)

    monkeypatch.setattr(backend, "_runner", None)
    monkeypatch.setattr(adapters_module.subprocess, "run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "ok", ""))
    assert backend._run(["sfdisk", "/dev/secret-device"]) == "ok"
    monkeypatch.setattr(adapters_module.subprocess, "run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, "", "contains device id"))
    with pytest.raises(UnsafeRemovableTarget, match=r"operation failed \(sfdisk\)") as failure:
        backend._run(["sfdisk", "/dev/secret-device"])
    assert "secret-device" not in str(failure.value)


def test_linux_backend_flush_eject_and_sync_helpers_use_safe_temp_paths(
    tmp_path: Path,
) -> None:
    device = RemovableDevice("linux:by-id:usb-MAKER_SYNC001-0:0", "USB", 8_000_000, False, True, False, serial="SYNC001")
    image = tmp_path / "disposable-device-file"
    image.write_bytes(b"test")
    descriptor = os.open(image, os.O_RDWR)
    commands: list[str] = []

    def recording_runner(args: list[str], _input: Optional[str] = None) -> str:
        commands.append(args[0])
        return ""

    backend = LinuxBlockDeviceBackend(lambda: [device], runner=recording_runner)
    backend._locked[device.device_id] = (descriptor, image)
    mount = tmp_path / "mounted-tree"
    mount.mkdir()
    (mount / "payload").write_bytes(b"sync")

    backend._sync_mount(mount)
    backend.flush(device)
    backend.safe_eject(device)

    assert commands == ["blockdev", "blockdev", "udisksctl"]
    assert backend._locked == {}


def test_linux_backend_mocked_write_orchestrates_bounded_layout_without_host_writes(
    valid_source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = RemovableDevice("linux:by-id:usb-MAKER_WRITE001-0:0", "USB", 64_000_000, False, True, False, serial="WRITE001")
    plan = RemovableMediaWriter().dry_run(device, 1, source_dir=valid_source, bindings=QUALIFIED)
    disk_image = tmp_path / "mock-block-device"
    disk_image.write_bytes(b"disposable")
    descriptor = os.open(disk_image, os.O_RDWR)
    mount_parent = tmp_path / "mounts"
    mount_parent.mkdir()
    mount_path = mount_parent / "write-mount"
    commands: list[str] = []

    def recording_runner(args: list[str], _input: Optional[str] = None) -> str:
        commands.append(args[0])
        if args[0] == "umount":
            for child in mount_path.iterdir():
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        return ""

    def create_write_mount(**_kwargs: Any) -> str:
        mount_path.mkdir()
        return str(mount_path)

    backend = LinuxBlockDeviceBackend(lambda: [device], runner=recording_runner)
    backend._locked[device.device_id] = (descriptor, disk_image)
    monkeypatch.setattr(backend, "_current", lambda _expected, _required_bytes=1, **_kwargs: device)
    monkeypatch.setattr(LinuxBlockDeviceBackend, "_device_path", classmethod(lambda _cls, _device: disk_image))
    monkeypatch.setattr(LinuxBlockDeviceBackend, "_partition_path", classmethod(lambda _cls, _disk: tmp_path / "partition"))
    monkeypatch.setattr(backend, "_sync_mount", lambda _mount: commands.append("sync"))
    monkeypatch.setattr(adapters_module.tempfile, "mkdtemp", create_write_mount)

    backend.write(plan, valid_source)
    os.close(descriptor)

    assert commands == ["sfdisk", "blockdev", "mkfs.vfat", "mount", "sync", "umount"]
    assert not mount_path.exists()


def test_linux_backend_readback_and_failure_invalidation_use_only_disposable_files(
    valid_source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = RemovableDevice("linux:by-id:usb-MAKER_READ001-0:0", "USB", 4_194_304, False, True, False, serial="READ001")
    plan = RemovableMediaWriter().dry_run(device, 1, source_dir=valid_source, bindings=QUALIFIED)
    image = tmp_path / "disposable-block-image"
    image.write_bytes(b"X" * device.capacity_bytes)
    descriptor = os.open(image, os.O_RDWR)
    mount_parent = tmp_path / "mounts"
    mount_parent.mkdir()
    mount_path = mount_parent / "read-mount"
    commands: list[str] = []

    def recording_runner(args: list[str], _input: Optional[str] = None) -> str:
        commands.append(args[0])
        return ""

    def create_read_mount(**_kwargs: Any) -> str:
        mount_path.mkdir()
        return str(mount_path)

    backend = LinuxBlockDeviceBackend(lambda: [device], runner=recording_runner)
    backend._locked[device.device_id] = (descriptor, image)
    monkeypatch.setattr(backend, "_current", lambda _expected, _required_bytes=1, **_kwargs: device)
    monkeypatch.setattr(LinuxBlockDeviceBackend, "_partition_path", classmethod(lambda _cls, _disk: tmp_path / "partition"))
    monkeypatch.setattr(backend, "_verify_boot_layout", lambda _source, _mount, _plan: True)
    monkeypatch.setattr(adapters_module.tempfile, "mkdtemp", create_read_mount)

    assert backend.readback(plan, valid_source) is True
    backend.invalidate(device, "synthetic readback failure")
    bytes_after_invalidation = image.read_bytes()
    assert bytes_after_invalidation[:1024 * 1024] == bytes(1024 * 1024)
    assert bytes_after_invalidation[-1024 * 1024:] == bytes(1024 * 1024)
    assert commands == ["mount", "umount", "blockdev", "udisksctl"]


def test_linux_boot_layout_moves_verified_recovery_to_opencore_directory(
    valid_source: Path, tmp_path: Path
) -> None:
    backend = LinuxBlockDeviceBackend(lambda: [], runner=_empty_linux_runner)
    device = RemovableDevice("usb", "USB", 64_000_000, False, True, False, serial="USB001")
    plan = RemovableMediaWriter().dry_run(device, 1, source_dir=valid_source, bindings=QUALIFIED)
    mount = tmp_path / "volume"
    mount.mkdir()
    backend._copy_boot_layout(valid_source, mount, plan)

    assert (mount / "EFI" / "BOOT" / "BOOTx64.efi").read_bytes() == b"boot"
    assert (mount / "EFI" / "OC" / "OpenCore.efi").read_bytes() == b"oc"
    assert (mount / "com.apple.recovery.boot" / "BaseSystem.dmg").read_bytes() == b"recovery"
    assert backend._verify_boot_layout(valid_source, mount, plan)
    (mount / "com.apple.recovery.boot" / "BaseSystem.dmg").write_bytes(b"changed")
    assert not backend._verify_boot_layout(valid_source, mount, plan)


def test_linux_gpt_fat32_disposable_image_readback_and_failure_invalidation(
    valid_source: Path, tmp_path: Path
) -> None:
    """Exercise real GPT/FAT32 tools on a temporary file image, never a USB device."""
    tools = ("sfdisk", "mkfs.vfat", "mcopy", "mmd")
    if any(shutil.which(name) is None for name in tools):
        pytest.skip("disposable image integration requires sfdisk, dosfstools and mtools")
    image = tmp_path / "disposable-usb.img"
    capacity = 128 * 1024 * 1024
    with image.open("wb") as handle:
        handle.truncate(capacity)
    backend = LinuxBlockDeviceBackend(lambda: [], runner=_empty_linux_runner)
    sectors = capacity // 512
    table = subprocess.run(
        ["sfdisk", "--wipe", "always", str(image)], input=backend._partition_script(sectors),
        text=True, capture_output=True, check=False, timeout=30,
    )
    assert table.returncode == 0, table.stderr
    partition_json = subprocess.run(["sfdisk", "--json", str(image)], capture_output=True, text=True, check=True)
    partition = json.loads(partition_json.stdout)["partitiontable"]["partitions"][0]
    assert partition["type"] == "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
    offset = int(partition["start"]) * 512
    format_result = subprocess.run(
        ["mkfs.vfat", "--offset=2048", "-F", "32", "-n", "MACLOADER", str(image), str(int(partition["size"]) // 2)],
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert format_result.returncode == 0, format_result.stderr
    plan = RemovableMediaWriter().dry_run(
        RemovableDevice("usb", "Disposable image", capacity, False, True, False, serial="IMAGE001"),
        1, source_dir=valid_source, bindings=QUALIFIED,
    )
    staged = tmp_path / "staged-volume"
    staged.mkdir()
    backend._copy_boot_layout(valid_source, staged, plan)
    image_spec = f"{image}@@{offset}"
    for child in sorted(staged.iterdir()):
        copied = subprocess.run(
            ["mcopy", "-s", "-i", image_spec, str(child), "::"],
            capture_output=True, text=True, check=False, timeout=30,
        )
        assert copied.returncode == 0, copied.stderr
    extracted = tmp_path / "readback"
    extracted.mkdir()
    for name in ("EFI", "com.apple.recovery.boot"):
        copied = subprocess.run(
            ["mcopy", "-s", "-i", image_spec, f"::/{name}", str(extracted)],
            capture_output=True, text=True, check=False, timeout=30,
        )
        assert copied.returncode == 0, copied.stderr
    assert backend._verify_boot_layout(valid_source, extracted, plan)

    # Failure injection: corrupt the Recovery file after writing, verify the
    # full readback detects it, then run the same bounded invalidation routine
    # against the disposable image and ensure the GPT is no longer mountable.
    damaged = tmp_path / "damaged.dmg"
    damaged.write_bytes(b"bad")
    subprocess.run(
        ["mcopy", "-o", "-i", image_spec, str(damaged), "::/com.apple.recovery.boot/BaseSystem.dmg"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    corrupted = tmp_path / "corrupted-readback"
    corrupted.mkdir()
    subprocess.run(
        ["mcopy", "-s", "-i", image_spec, "::/EFI", str(corrupted)], capture_output=True, text=True, check=True, timeout=30,
    )
    subprocess.run(
        ["mcopy", "-s", "-i", image_spec, "::/com.apple.recovery.boot", str(corrupted)], capture_output=True, text=True, check=True, timeout=30,
    )
    assert not backend._verify_boot_layout(valid_source, corrupted, plan)
    descriptor = os.open(image, os.O_RDWR)
    try:
        backend._invalidate_descriptor(descriptor, capacity)
    finally:
        os.close(descriptor)
    assert image.read_bytes()[:8 * 512] == bytes(8 * 512)
    invalidated = subprocess.run(["sfdisk", "--json", str(image)], capture_output=True, text=True, check=False)
    assert invalidated.returncode != 0


def test_current_adapter_does_not_enable_unqualified_media(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters_module.sys, "platform", "linux")
    assert isinstance(current_adapter(), LinuxRemovableAdapter)
    monkeypatch.setattr(adapters_module.sys, "platform", "win32")
    adapter = current_adapter()
    assert isinstance(adapter, WindowsRemovableAdapter)
    assert adapter.status.qualified is False


def test_powershell_runner_uses_bounded_noninteractive_command() -> None:
    completed = subprocess.CompletedProcess([], 0, stdout="[]", stderr="")
    with mock.patch.object(adapters_module.subprocess, "run", return_value=completed) as run:
        assert WindowsRemovableAdapter._run_powershell("Get-Disk") == "[]"
    run.assert_called_once_with(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "Get-Disk"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_confirmation_expires_and_binds_plan(valid_source: Path, tmp_path: Path) -> None:
    device = RemovableDevice("usb-1", "USB", 1024, False, True, False, serial="SERIAL")
    writer = RemovableMediaWriter()
    plan = writer.dry_run(device, 1, source_dir=valid_source, bindings=QUALIFIED)
    confirmation = DestructiveConfirmation.issue(plan, now=10.0, ttl_seconds=2.0)
    assert confirmation.valid_for(plan, now=11.0)
    assert not confirmation.valid_for(plan, now=12.0)
    changed = replace(plan, required_bytes=2)
    assert not confirmation.valid_for(changed, now=11.0)


def test_media_plan_manifest_detects_source_mutation(valid_source: Path) -> None:
    device = RemovableDevice("usb-1", "USB", 1024, False, True, False, serial="SERIAL")
    writer = RemovableMediaWriter(require_published_artifacts=False)
    plan = writer.dry_run(device, 1, source_dir=valid_source, bindings=QUALIFIED)
    (valid_source / "EFI" / "OC" / "config.plist").write_text("changed", encoding="utf-8")
    confirmation = DestructiveConfirmation.issue(plan)
    with pytest.raises(UnsafeRemovableTarget, match="changed after planning"):
        writer.write(plan, valid_source, confirmation)


def test_stale_target_diagnostic_uses_opaque_reference(valid_source: Path) -> None:
    secret_id = "windows:serial:PRIVATE-SERIAL"
    device = RemovableDevice(secret_id, "USB", 1024, False, True, False, serial="PRIVATE-SERIAL")
    writer = RemovableMediaWriter(enumerator=lambda: [], require_published_artifacts=False)
    plan = writer.dry_run(device, 1, source_dir=valid_source, bindings=QUALIFIED)
    with pytest.raises(UnsafeRemovableTarget) as caught:
        writer.write(plan, valid_source, DestructiveConfirmation.issue(plan))
    assert secret_id not in str(caught.value)
    assert "PRIVATE-SERIAL" not in str(caught.value)
    assert plan.public_device_ref in str(caught.value)


def test_disposable_readback_requires_recovery_and_efi(valid_source: Path, tmp_path: Path) -> None:
    from macloader.removable import DisposableImageAdapter

    target = tmp_path / "image"
    adapter = DisposableImageAdapter(target)
    writer = RemovableMediaWriter(
        destructive_write=adapter.write,
        readback_verifier=adapter.verify_readback,
        require_published_artifacts=False,
    )
    device = RemovableDevice("usb-2", "USB", 1024, False, True, False, serial="SERIAL")
    plan = writer.dry_run(device, 1, source_dir=valid_source, bindings=QUALIFIED)
    writer.write(plan, valid_source, DestructiveConfirmation.issue(plan))
    (target / "Recovery" / "BaseSystem.dmg").unlink()
    assert adapter.verify_readback(plan, valid_source) is False


@pytest.fixture
def valid_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "EFI" / "BOOT").mkdir(parents=True)
    (source / "EFI" / "OC").mkdir()
    (source / "EFI" / "BOOT" / "BOOTx64.efi").write_bytes(b"boot")
    (source / "EFI" / "OC" / "OpenCore.efi").write_bytes(b"oc")
    (source / "EFI" / "OC" / "config.plist").write_text("config", encoding="utf-8")
    (source / "Recovery").mkdir()
    (source / "Recovery" / "BaseSystem.dmg").write_bytes(b"recovery")
    return source
