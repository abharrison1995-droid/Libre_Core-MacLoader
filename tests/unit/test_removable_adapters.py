"""P7 platform adapter and immutable-plan regression tests."""

from dataclasses import replace
from pathlib import Path
import subprocess
from typing import Any, cast
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
import macloader.removable.adapters as adapters_module
import macloader.removable.writer as writer_module


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
    adapter = WindowsRemovableAdapter(backend=backend, platform="win32")
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
    )
    device = adapter.enumerate()[0]
    plan = RemovableMediaWriter().dry_run(device, 1, source_dir=valid_source, bindings=QUALIFIED)
    with pytest.raises(OSError, match="simulated write failure"):
        adapter.write(plan, valid_source)
    assert backend.events == ["lock-dismount", "write", "invalidate", "remount"]


def test_windows_backend_readback_and_invalidation_always_remount(
    valid_source: Path,
) -> None:
    backend = FalseReadbackWindowsBackend()
    adapter = WindowsRemovableAdapter(
        runner=lambda _script: '[{"Number": 3, "FriendlyName": "USB Disk", "SerialNumber": "SERIAL", "Size": 1000, "BusType": "USB", "Mounted": false, "IsRemovable": true}]',
        backend=backend,
        platform="win32",
    )
    device = adapter.enumerate()[0]
    plan = RemovableMediaWriter().dry_run(device, 1, source_dir=valid_source, bindings=QUALIFIED)
    assert adapter.readback(plan, valid_source) is False
    adapter.invalidate(plan, "test invalidation")
    assert backend.events == ["readback", "remount", "invalidate", "remount"]


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
    writer = RemovableMediaWriter()
    plan = writer.dry_run(device, 1, source_dir=valid_source, bindings=QUALIFIED)
    (valid_source / "EFI" / "OC" / "config.plist").write_text("changed", encoding="utf-8")
    confirmation = DestructiveConfirmation.issue(plan)
    with pytest.raises(UnsafeRemovableTarget, match="changed after planning"):
        writer.write(plan, valid_source, confirmation)


def test_stale_target_diagnostic_uses_opaque_reference(valid_source: Path) -> None:
    secret_id = "windows:serial:PRIVATE-SERIAL"
    device = RemovableDevice(secret_id, "USB", 1024, False, True, False, serial="PRIVATE-SERIAL")
    writer = RemovableMediaWriter(enumerator=lambda: [])
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
