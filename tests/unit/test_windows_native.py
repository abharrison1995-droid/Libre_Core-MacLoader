"""Synthetic API/fault tests: none qualify a physical Windows writer."""
from dataclasses import replace
import ctypes
import hashlib
import json
from pathlib import Path
import struct
from typing import Any
import zlib

import pytest

from macloader.removable.adapters import WindowsRemovableAdapter
from macloader.removable.writer import MediaFileDigest, RemovableDevice, UnsafeRemovableTarget, WritePlan
from macloader.removable.windows_image import WindowsMediaImage, _lfn
from macloader.removable.windows_native import (WindowsNativeBackend, Win32Storage, descriptor, disk_extent,
    QUERY_PROPERTY, GEOMETRY, DEVICE_NUMBER, GET_ATTRIBUTES, SET_ATTRIBUTES, EXTENTS, LOCK, DISMOUNT, UNLOCK)
from macloader.removable.windows_qualification import (REQUIRED_CASES, approved_backend, backend_digest,
    qualification_report, valid_approval)


def row() -> dict[str, Any]:
    return dict(Number=3, FriendlyName='Test USB', SerialNumber='SYNTHETIC', Size=1024,
        IsBoot=False, IsSystem=False, IsReadOnly=False, BusType='USB', IsRemovable=True,
        Mounted=False, Partitions=['1'])


def device() -> RemovableDevice:
    return WindowsRemovableAdapter(runner=lambda _: json.dumps([row()]), platform='win32').enumerate()[0]


def storage_descriptor(serial: bytes = b'SYNTHETIC\0', bus: int = 7) -> bytes:
    result = bytearray(36)
    struct.pack_into('<II', result, 0, 36, 36 + len(serial))
    result[10] = 1
    struct.pack_into('<II', result, 24, 36, bus)
    return bytes(result) + serial


class FakeAPI:
    def __init__(self) -> None:
        self.paths: dict[int, str] = {}
        self.closed: list[int] = []
        self.controls: list[int] = []
        self.blocks: dict[int, bytes] = {}
        self.offline = False
        self.corrupt = False
        self.fail: int | str | None = None
        self.serial = b'SYNTHETIC\0'
        self.capacity = 1024
        self.sector = 512
        self.partition = 0xffffffff
        self.bus = 7
        self.attributes = 0
        self.system_number = 0
        self.inventory_calls = 0

    def open(self, path: str, writable: bool) -> int:
        if self.fail == 'open' and writable:
            raise UnsafeRemovableTarget('test open failed')
        handle = len(self.paths) + 1
        self.paths[handle] = path
        return handle

    def close(self, handle: int) -> None:
        self.closed.append(handle)

    def ioctl(self, handle: int, code: int, data: bytes = b'', size: int = 65536) -> bytes:
        self.controls.append(code)
        if self.fail == code:
            raise UnsafeRemovableTarget('test control failed')
        if code == QUERY_PROPERTY:
            return storage_descriptor(self.serial, self.bus)
        if code == GEOMETRY:
            result = bytearray(32)
            struct.pack_into('<IQ', result, 20, self.sector, self.capacity)
            return bytes(result)
        if code == DEVICE_NUMBER:
            return struct.pack('<III', 7, 3, self.partition)
        if code == GET_ATTRIBUTES:
            return struct.pack('<IIQ', 16, 0, self.attributes | int(self.offline))
        if code == SET_ATTRIBUTES:
            assert struct.unpack('<IB3xQQ4I', data) == (40, 0, 1, 1, 0, 0, 0, 0)
            self.offline = True
        if code == EXTENTS:
            number = self.system_number if self.paths[handle] == 'SYSTEM' else (3 if self.paths[handle] == 'TARGET' else 7)
            return struct.pack('<I4xI4xQQ', 1, number, 0, self.capacity)
        return b''

    def read(self, handle: int, offset: int, size: int) -> bytes:
        if self.fail == 'read':
            raise UnsafeRemovableTarget('test read failed')
        data = self.blocks[offset]
        return bytes(size) if self.corrupt else data

    def write(self, handle: int, offset: int, data: bytes) -> None:
        if self.fail == 'write':
            raise UnsafeRemovableTarget('test short write')
        self.blocks[offset] = data

    def flush(self, handle: int) -> None:
        if self.fail == 'flush':
            raise UnsafeRemovableTarget('test flush failed')

    def volumes(self) -> list[str]:
        self.inventory_calls += 1
        return ['SYSTEM', 'TARGET', 'OTHER']

    def system_volume(self) -> str:
        return 'SYSTEM'


class TinyImage:
    def __init__(self, plan: WritePlan, source: Path):
        pass
    def chunks(self):  # type: ignore[no-untyped-def]
        yield 0, b'A' * 512
        yield 512, b'B' * 512


def backend(monkeypatch: pytest.MonkeyPatch) -> tuple[WindowsNativeBackend, FakeAPI, list[dict[str, Any]]]:
    api, rows = FakeAPI(), [row()]
    monkeypatch.setattr('macloader.removable.windows_native.WindowsMediaImage', TinyImage)
    return WindowsNativeBackend(lambda: rows, api), api, rows


def complete(b: WindowsNativeBackend, tmp_path: Path) -> WritePlan:
    plan = WritePlan(device(), 100)
    b.lock_and_dismount(plan.target)
    b.write(plan, tmp_path)
    b.flush(plan.target)
    assert b.readback(plan, tmp_path)
    return plan


def test_normal_raw_readback_offline_and_other_disks_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    b, api, rows = backend(monkeypatch)
    rows.append(dict(row(), Number=7, SerialNumber='OTHER'))
    plan = complete(b, tmp_path)
    assert LOCK in api.controls and DISMOUNT in api.controls
    b.remount(plan.target)
    assert b.volume_handles  # Old volumes remain locked until confirmed offline.
    b.safe_eject(plan.target)
    assert api.offline and b.handle is None and not b.volume_handles
    written_handles = [path for path in api.paths.values() if 'PhysicalDrive' in path]
    assert written_handles == [r'\\.\PhysicalDrive3']
    assert not b.production_qualified and not approved_backend()


@pytest.mark.parametrize('change', [dict(SerialNumber='OTHER'), dict(Size=2048), dict(FriendlyName='Other'),
    dict(IsBoot=True), dict(IsSystem=True), dict(IsReadOnly=True), dict(Mounted=True), dict(BusType='SATA', IsRemovable=False), dict(Partitions=['2'])])
def test_prewrite_full_identity_revalidation(change: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    b, api, rows = backend(monkeypatch)
    rows[0].update(change)
    with pytest.raises(UnsafeRemovableTarget):
        b.lock_and_dismount(device())
    assert not api.blocks


@pytest.mark.parametrize('kind', ['missing', 'duplicate', 'bad-number', 'no-serial', 'system-native'])
def test_missing_ambiguous_locator_and_system_native(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    b, api, rows = backend(monkeypatch)
    if kind == 'missing': rows.clear()
    if kind == 'duplicate': rows.append(row())
    if kind == 'bad-number': rows[0]['Number'] = 1.5
    if kind == 'no-serial': rows[0]['SerialNumber'] = None
    if kind == 'system-native': api.system_number = 3
    with pytest.raises(UnsafeRemovableTarget): b.lock_and_dismount(device())
    assert not api.blocks


@pytest.mark.parametrize('field,value', [('serial', b'CHANGED\0'), ('bus', 11), ('capacity', 2048), ('sector', 4096), ('partition', 1), ('attributes', 2)])
def test_native_descriptor_binding_mismatch(field: str, value: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    b, api, _ = backend(monkeypatch)
    setattr(api, field, value)
    with pytest.raises(UnsafeRemovableTarget): b.lock_and_dismount(device())
    assert b.handle is None and not api.blocks


@pytest.mark.parametrize('failure', [LOCK, DISMOUNT, QUERY_PROPERTY, GEOMETRY, DEVICE_NUMBER, GET_ATTRIBUTES, 'open'])
def test_lock_failures_release_owned_handles(failure: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    b, api, _ = backend(monkeypatch)
    api.fail = failure
    with pytest.raises(UnsafeRemovableTarget): b.lock_and_dismount(device())
    assert b.handle is None and not b.volume_handles and not api.blocks


@pytest.mark.parametrize('failure', ['write', 'flush', 'read', 'corrupt', 'cancel', SET_ATTRIBUTES, UNLOCK])
def test_failed_operation_never_marks_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Any) -> None:
    b, api, _ = backend(monkeypatch)
    target, plan = device(), WritePlan(device(), 100)
    b.lock_and_dismount(target)
    if failure == 'cancel': b.set_cancel(lambda: True)
    elif failure == 'corrupt': api.corrupt = True
    else: api.fail = failure
    try:
        b.write(plan, tmp_path)
        b.flush(target)
        verified = b.readback(plan, tmp_path)
        if not verified:
            assert not b.verified
            return
        b.remount(target)
        with pytest.raises(UnsafeRemovableTarget): b.safe_eject(target)
    except UnsafeRemovableTarget:
        assert not b.verified or failure in (SET_ATTRIBUTES, UNLOCK)
    finally:
        api.fail, api.corrupt = None, False
        b.set_cancel(lambda: False)
        b.invalidate(target, 'failed')
    assert b.handle is None and not b.verified


def test_cancel_between_chunks_and_native_identity_changed_after_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    b, api, rows = backend(monkeypatch)
    target = device()
    b.lock_and_dismount(target)
    calls = iter([False, True])
    b.set_cancel(lambda: next(calls))
    with pytest.raises(UnsafeRemovableTarget, match='cancelled'): b.write(WritePlan(target, 100), tmp_path)
    assert 0 in api.blocks and 512 not in api.blocks
    b.set_cancel(lambda: False)
    b.invalidate(target, 'cancelled')
    assert api.offline and b.handle is None
    b, api, rows = backend(monkeypatch)
    b.lock_and_dismount(target)
    rows[0]['Mounted'] = True
    with pytest.raises(UnsafeRemovableTarget): b.write(WritePlan(target, 100), tmp_path)
    b.invalidate(target, 'not touched')
    assert not api.blocks and b.handle is None


def test_eject_before_readback_and_unlocked_access_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    b, api, _ = backend(monkeypatch)
    with pytest.raises(UnsafeRemovableTarget): b.flush(device())
    b.remount(device())
    b.invalidate(device(), 'before locking')
    b.lock_and_dismount(device())
    with pytest.raises(UnsafeRemovableTarget): b.lock_and_dismount(device())
    with pytest.raises(UnsafeRemovableTarget): b.safe_eject(device())
    b.invalidate(device(), 'not touched')
    assert not api.offline


@pytest.mark.parametrize('data', [b'', bytes(36), storage_descriptor(b'NO-TERMINATOR'), storage_descriptor(b'\xff\0'), storage_descriptor(b'\0')])
def test_descriptor_rejects_unproven_identity(data: bytes) -> None:
    with pytest.raises(UnsafeRemovableTarget): descriptor(data)


def test_descriptor_and_extents_are_bounded() -> None:
    assert descriptor(storage_descriptor()) == ('SYNTHETIC', 7)
    assert disk_extent(struct.pack('<I4xI4xQQ', 1, 7, 0, 100)) == 7
    for data in (b'', struct.pack('<I4xI4xQQ', 2, 7, 0, 100)):
        with pytest.raises(UnsafeRemovableTarget): disk_extent(data)


def image_plan(tmp_path: Path, names: tuple[str, ...] = ('EFI/BOOT/BOOTx64.efi', 'com.apple.recovery.boot/BaseSystem.dmg')) -> WritePlan:
    records = []
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = name.encode() * 100
        path.write_bytes(data)
        records.append(MediaFileDigest(name, len(data), hashlib.sha256(data).hexdigest()))
    return WritePlan(replace(device(), capacity_bytes=1024 ** 3), 10000, expected_files=records)


def test_gpt_fat_stream_crc_alignment_names_files_and_zero_free_space(tmp_path: Path) -> None:
    plan = image_plan(tmp_path, ('EFI/BOOT/BOOTx64.efi', 'com.apple.recovery.boot/BaseSystem.dmg', 'Empty.txt', 'Long ü filename.chunklist'))
    image = WindowsMediaImage(plan, tmp_path)
    metadata = {offset: data for offset, data in image.extents if isinstance(data, bytes)}
    header = bytearray(metadata[512])
    expected = struct.unpack_from('<I', header, 16)[0]
    struct.pack_into('<I', header, 16, 0)
    assert zlib.crc32(header[:92]) == expected
    assert zlib.crc32(metadata[1024]) == struct.unpack_from('<I', header, 88)[0]
    assert metadata[0][-2:] == b'\x55\xaa'
    boot = metadata[2048 * 512]
    assert len(boot) == 512 and boot[82:90] == b'FAT32   '
    assert struct.unpack_from('<H', boot, 11)[0] == 512
    assert struct.unpack_from('<I', boot, 44)[0] == 2
    assert len(image.fat) == image.fat_sectors * 512
    end = 0
    for offset, data in image.chunks():
        assert offset == end and offset % 512 == 0 and len(data) % 512 == 0 and len(data) <= 1024 * 1024
        end += len(data)
    assert end == plan.target.capacity_bytes
    for node in image.nodes:
        if node.source:
            assert node.digest == hashlib.sha256(node.source.read_bytes()).hexdigest()
    assert image.root.cluster == 2
    assert _lfn('Long ü filename.chunklist', b'F0000001   ')[0] & 0x40


@pytest.mark.parametrize('capacity', [512, 1024 ** 3 + 1, 129 * 1024 ** 3])
def test_image_rejects_capacity_before_io(tmp_path: Path, capacity: int) -> None:
    plan = image_plan(tmp_path)
    with pytest.raises(UnsafeRemovableTarget): WindowsMediaImage(replace(plan, target=replace(plan.target, capacity_bytes=capacity)), tmp_path)


@pytest.mark.parametrize('name', ['../bad', '/bad', 'EFI/a:b', 'EFI/back\\slash', 'EFI/trailing.', 'EFI/trailing '])
def test_image_rejects_unsafe_paths(tmp_path: Path, name: str) -> None:
    plan = WritePlan(replace(device(), capacity_bytes=1024 ** 3), 1, expected_files=[MediaFileDigest(name, 1, 'a' * 64)])
    with pytest.raises(UnsafeRemovableTarget): WindowsMediaImage(plan, tmp_path)


def test_image_rejects_layout_collisions_large_file_and_source_mutation(tmp_path: Path) -> None:
    plan = image_plan(tmp_path)
    for broken in (replace(plan, expected_files=()), replace(plan, partitions=('MBR',)),
                   replace(plan, expected_files=[replace(plan.expected_files[0], size_bytes=2 ** 32)]),
                   replace(plan, expected_files=[plan.expected_files[0], replace(plan.expected_files[0], relative_path='efi/boot/bootX64.efi')])):
        with pytest.raises(UnsafeRemovableTarget): WindowsMediaImage(broken, tmp_path)
    with pytest.raises(UnsafeRemovableTarget): _lfn('x' * 256, b'F0000001   ')
    image = WindowsMediaImage(plan, tmp_path)
    (tmp_path / plan.expected_files[0].relative_path).write_bytes(b'changed')
    with pytest.raises(UnsafeRemovableTarget): list(image.chunks())


def test_reviewed_approval_is_exact_and_local_reports_cannot_enable() -> None:
    digest = backend_digest()
    record = dict(backend_version='windows-native-gpt-fat32-v1', backend_sha256=digest,
        windows_version='10.0.test', architecture='AMD64', reviewed=True,
        physical_report_sha256='b' * 64, review_url='https://github.com/abharrison1995-droid/Libre_Core-MacLoader/pull/1',
        cases={case: 'physical-pass' for case in REQUIRED_CASES})
    host = ('win32', '10.0.test', 'AMD64')
    assert valid_approval(record, digest, host)
    for key, value in [('backend_sha256', 'c' * 64), ('reviewed', False), ('cases', {}), ('review_url', 'https://evil.test'), ('physical_report_sha256', 'not-a-hash')]:
        assert not valid_approval(dict(record, **{key: value}), digest, host)
    assert not valid_approval(record, digest, ('linux', '10.0.test', 'AMD64'))
    assert not valid_approval(record, digest, ('win32', '10.0.other', 'AMD64'))
    report = qualification_report(device().public_device_ref, 'a' * 40, {'normal-write': 'physical-pass'})
    assert not report['production_approved'] and not report['reviewed']
    assert 'SYNTHETIC' not in json.dumps(report)
    assert not approved_backend()
    with pytest.raises(ValueError): qualification_report('raw serial', 'a' * 40, {})
    with pytest.raises(ValueError): qualification_report(device().public_device_ref, 'a' * 40, {'fake': 'physical-pass'})


def test_native_boundary_unavailable_off_windows_and_io_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    if __import__('sys').platform != 'win32':
        with pytest.raises(UnsafeRemovableTarget): Win32Storage()
    api = Win32Storage.__new__(Win32Storage)
    for offset, size in ((-1, 512), (1, 512), (0, 0), (0, 513), (0, 2 * 1024 * 1024)):
        with pytest.raises(UnsafeRemovableTarget): api._transfer(1, offset, size, None)
    for path in ('C:', r'\\.\C:', 'PhysicalDrive3', r'\\.\PhysicalDrive3/evil'):
        with pytest.raises(UnsafeRemovableTarget): api.open(path, True)


class _Function:
    def __init__(self, callback: Any):
        self.callback = callback
    def __call__(self, *args: Any) -> Any:
        return self.callback(*args)


class FakeDLL:
    def __init__(self) -> None:
        self.memory: Any = None
        self.calls: list[str] = []
        self.short = False
        self.failure: str | None = None
        self.next_volume = 0
        for name in ('CreateFileW', 'CloseHandle', 'DeviceIoControl', 'SetFilePointerEx', 'ReadFile', 'WriteFile',
                     'FlushFileBuffers', 'VirtualAlloc', 'VirtualFree', 'FindFirstVolumeW', 'FindNextVolumeW',
                     'FindVolumeClose', 'GetWindowsDirectoryW', 'GetVolumePathNameW', 'GetVolumeNameForVolumeMountPointW'):
            setattr(self, name, _Function(lambda *args, name=name: self.call(name, *args)))
    def call(self, name: str, *args: Any) -> Any:
        self.calls.append(name)
        if self.failure == name:
            return None if name in ('CreateFileW', 'FindFirstVolumeW') else 0
        if name == 'CreateFileW': return 100
        if name == 'VirtualAlloc':
            self.memory = ctypes.create_string_buffer(args[1] + 4096)
            return (ctypes.addressof(self.memory) + 4095) & ~4095
        if name in ('ReadFile', 'WriteFile'):
            ctypes.cast(args[3], ctypes.POINTER(ctypes.c_uint32))[0] = args[2] - (512 if self.short else 0)
            if name == 'ReadFile': ctypes.memmove(args[1], b'Z' * args[2], args[2])
        if name == 'DeviceIoControl':
            if args[5]:
                ctypes.memmove(args[4], b'DATA', 4)
            ctypes.cast(args[6], ctypes.POINTER(ctypes.c_uint32))[0] = 4 if args[5] else 0
        if name == 'FindFirstVolumeW':
            args[0].value = '\\\\?\\Volume{00000000-0000-0000-0000-000000000001}\\'
            return 77
        if name == 'FindNextVolumeW':
            self.next_volume += 1
            if self.next_volume == 1:
                args[1].value = '\\\\?\\Volume{00000000-0000-0000-0000-000000000002}\\'
                return 1
            return 0
        if name == 'GetWindowsDirectoryW':
            args[0].value = 'C:\\Windows'
            return 10
        if name == 'GetVolumePathNameW': args[1].value = 'C:\\'
        if name == 'GetVolumeNameForVolumeMountPointW': args[1].value = '\\\\?\\Volume{00000000-0000-0000-0000-000000000001}\\'
        return 1


def test_ctypes_wrappers_use_page_aligned_io_native_guids_and_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    dll = FakeDLL()
    api = Win32Storage(dll)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 18, raising=False)
    assert api.open(r'\\.\PhysicalDrive3', True) == 100
    assert api.open(r'\\?\Volume{00000000-0000-0000-0000-000000000001}', False) == 100
    api.write(100, 512, b'X' * 512)
    assert api.read(100, 0, 512) == b'Z' * 512
    assert 'VirtualFree' in dll.calls
    assert api.ioctl(100, GET_ATTRIBUTES, b'input') == b'DATA'
    assert api.ioctl(100, LOCK, size=0) == b''
    api.flush(100)
    assert len(api.volumes()) == 2
    assert api.system_volume().endswith('000000000001}')
    api.close(100)
    dll.short = True
    with pytest.raises(UnsafeRemovableTarget, match='short'): api.write(100, 0, b'X' * 512)
    dll.failure = 'CreateFileW'
    with pytest.raises(UnsafeRemovableTarget): api.open(r'\\.\PhysicalDrive3', True)
    dll.failure = 'FlushFileBuffers'
    with pytest.raises(UnsafeRemovableTarget): api.flush(100)
    dll.failure = 'GetWindowsDirectoryW'
    with pytest.raises(UnsafeRemovableTarget): api.system_volume()
    dll.failure = 'FindFirstVolumeW'
    with pytest.raises(UnsafeRemovableTarget): api.volumes()
    dll.failure = None
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 5)
    with pytest.raises(UnsafeRemovableTarget): api.volumes()


def test_native_inventory_requires_proven_safety_flags_and_usb_enum(monkeypatch: pytest.MonkeyPatch) -> None:
    b, api, rows = backend(monkeypatch)
    del rows[0]['IsBoot']
    with pytest.raises(UnsafeRemovableTarget, match='incomplete'): b.lock_and_dismount(device())
    numeric = dict(row(), BusType=7)
    assert WindowsRemovableAdapter(runner=lambda _: json.dumps([numeric]), platform='win32').enumerate()[0].is_removable


def test_harness_stops_off_host_and_before_confirmation_without_green(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    from macloader.removable.windows_qualification import run_physical_qualification
    from macloader.autoloader.readiness import ReadinessGate
    monkeypatch.setattr(sys, 'platform', 'linux')
    with pytest.raises(UnsafeRemovableTarget, match='Windows'): run_physical_qualification(tmp_path / 'report.json')
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr('macloader.autoloader.readiness.hosted_ci_gate', lambda: ReadinessGate('CI', False, 'not green'))
    with pytest.raises(UnsafeRemovableTarget, match='known-green'): run_physical_qualification(tmp_path / 'report.json')
    assert not (tmp_path / 'report.json').exists()


@pytest.mark.parametrize('fail', [False, True])
def test_harness_synthetic_seams_preserve_private_unreviewed_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: bool) -> None:
    import sys
    import click
    import subprocess
    from macloader.autoloader.readiness import ReadinessGate
    from macloader.removable.windows_qualification import run_physical_qualification
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr('macloader.autoloader.readiness.hosted_ci_gate', lambda: ReadinessGate('CI', True, 'synthetic test'))
    monkeypatch.setattr(subprocess, 'check_output', lambda *args, **kwargs: 'a' * 40)
    data = dict(row(), Size=1024 ** 3)
    monkeypatch.setattr(WindowsRemovableAdapter, '_run_powershell', staticmethod(lambda _: json.dumps([data])))
    monkeypatch.setattr(click, 'prompt', lambda *args, **kwargs: 1)
    monkeypatch.setattr(click, 'confirm', lambda *args, **kwargs: True)
    events: list[str] = []
    class HarnessBackend:
        def __init__(self, inventory: Any):
            assert inventory()[0]['Number'] == 3
        def lock_and_dismount(self, device: RemovableDevice) -> None: events.append('lock')
        def write(self, plan: WritePlan, source: Path) -> None:
            events.append('write')
            if fail: raise OSError('synthetic failure')
        def flush(self, device: RemovableDevice) -> None: events.append('flush')
        def readback(self, plan: WritePlan, source: Path) -> bool:
            events.append('readback')
            return True
        def safe_eject(self, device: RemovableDevice) -> None: events.append('offline')
        def invalidate(self, device: RemovableDevice, reason: str) -> None: events.append('invalidate')
    monkeypatch.setattr('macloader.removable.windows_native.WindowsNativeBackend', HarnessBackend)
    path = tmp_path / 'private/report.json'
    if fail:
        with pytest.raises(UnsafeRemovableTarget): run_physical_qualification(path)
    else:
        run_physical_qualification(path)
    report = json.loads(path.read_text())
    assert report['production_approved'] is False and report['bootable'] is False and report['reviewed'] is False
    assert 'SYNTHETIC' not in path.read_text()
    assert report['operation'] == ('failed-usb-unready' if fail else 'full-readback-offline-complete')
    assert events == (['lock', 'write', 'invalidate', 'invalidate'] if fail else ['lock', 'write', 'flush', 'readback', 'offline'])
    assert sum(value == 'not-run' for value in report['cases'].values()) == (15 if fail else 13)
    with pytest.raises(UnsafeRemovableTarget, match='preserved'): run_physical_qualification(path)


def test_packaged_approval_unknown_shape_and_source_drift_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import macloader.removable.windows_qualification as qualification
    original = Path.read_text
    host = ('win32', '10.0.test', 'AMD64')
    record = dict(backend_version='windows-native-gpt-fat32-v1', backend_sha256=backend_digest(),
        windows_version=host[1], architecture='AMD64', reviewed=True, physical_report_sha256='b' * 64,
        review_url='https://github.com/abharrison1995-droid/Libre_Core-MacLoader/pull/1',
        cases={case: 'physical-pass' for case in REQUIRED_CASES})
    payload: list[str] = [json.dumps({'schema': 1, 'approvals': [record]})]
    def read(path: Path, *args: Any, **kwargs: Any) -> str:
        if path.name == 'windows-writer-approvals.yaml': return payload[0]
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setattr(qualification, 'runtime_platform', lambda: host)
    assert approved_backend()
    monkeypatch.setattr(qualification, 'backend_digest', lambda: 'c' * 64)
    assert not approved_backend()
    for value in ('null', '{bad: [', '{"schema":2,"approvals":[]}', '{"schema":1,"approvals":{}}'):
        payload[0] = value
        assert not approved_backend()


def test_qualification_cli_status_and_explicit_result_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    from click.testing import CliRunner
    from macloader.autoloader.readiness import ReadinessGate
    import macloader.removable.windows_qualification as qualification
    runner = CliRunner()
    status = runner.invoke(qualification.cli, [])
    assert status.exit_code == 0 and '"production_approved": false' in status.output
    for args in (['--run'], ['--result', 'physical-pass'], ['--record-case', 'cancelled', '--report', str(tmp_path / 'missing')], ['--run', '--record-case', 'cancelled']):
        assert runner.invoke(qualification.cli, args).exit_code != 0
    monkeypatch.setattr('macloader.autoloader.readiness.hosted_ci_gate', lambda: ReadinessGate('CI', True, 'test only'))
    monkeypatch.setattr(subprocess, 'check_output', lambda *args, **kwargs: 'a' * 40)
    path = tmp_path / 'private-report.json'
    from macloader.workflow.service import WorkflowService
    report = qualification_report(device().public_device_ref, 'a' * 40, {})
    WorkflowService._write_private_json(path, json.dumps(report))
    result = runner.invoke(qualification.cli, ['--record-case', 'cancelled', '--result', 'physical-pass', '--report', str(path)], input='y\n')
    assert result.exit_code == 0, result.output
    assert json.loads(path.read_text())['cases']['cancelled'] == 'physical-pass'
    assert 'Production remains unapproved' in result.output
    for case, outcome in (('unknown', 'physical-pass'), ('cancelled', 'made-up')):
        with pytest.raises(ValueError): qualification.record_physical_case(path, case, outcome)
    report['backend_sha256'] = 'b' * 64
    WorkflowService._write_private_json(path, json.dumps(report))
    with pytest.raises(ValueError, match='binding'): qualification.record_physical_case(path, 'cancelled', 'physical-pass')
    monkeypatch.setattr('macloader.autoloader.readiness.hosted_ci_gate', lambda: ReadinessGate('CI', False, 'not green'))
    with pytest.raises(ValueError): qualification.record_physical_case(path, 'cancelled', 'physical-pass')


def test_disposable_image_with_independent_filesystem_tools(tmp_path: Path) -> None:
    import shutil
    import subprocess
    tools = {name: shutil.which(name) for name in ('sgdisk', 'fsck.fat', 'mcopy')}
    if not all(tools.values()):
        pytest.skip('Optional independent GPT/FAT tools unavailable; deterministic stream tests remain required')
    plan = image_plan(tmp_path, ('EFI/BOOT/BOOTx64.efi', 'com.apple.recovery.boot/BaseSystem.chunklist'))
    image = WindowsMediaImage(plan, tmp_path)
    disk_path, partition_path = tmp_path / 'disk.img', tmp_path / 'partition.img'
    with disk_path.open('wb') as disk, partition_path.open('wb') as partition:
        disk.truncate(plan.target.capacity_bytes)
        partition.truncate(image.partition_sectors * 512)
        for offset, content in image.extents:
            data = content if isinstance(content, bytes) else content.source.read_bytes()  # type: ignore[union-attr]
            disk.seek(offset); disk.write(data)
            if 2048 * 512 <= offset < (2048 + image.partition_sectors) * 512:
                partition.seek(offset - 2048 * 512); partition.write(data)
    result = subprocess.run([str(tools['sgdisk']), '-v', str(disk_path)], capture_output=True, text=True, check=True, timeout=30)
    assert 'No problems found' in result.stdout
    subprocess.run([str(tools['fsck.fat']), '-n', str(partition_path)], capture_output=True, text=True, check=True, timeout=30)
    copied = tmp_path / 'readback.bin'
    name = plan.expected_files[-1].relative_path
    subprocess.run([str(tools['mcopy']), '-i', str(disk_path) + '@@1048576', '::/' + name, str(copied)], capture_output=True, check=True, timeout=30)
    assert hashlib.sha256(copied.read_bytes()).hexdigest() == plan.expected_files[-1].sha256


@pytest.mark.parametrize('scenario', ['short-write', 'cancelled', 'flush-failed', 'readback-corruption', 'eject-failed'])
def test_labelled_physical_fault_harness_with_synthetic_io_seams(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str) -> None:
    import sys
    import subprocess
    import click
    from macloader.autoloader.readiness import ReadinessGate
    from macloader.removable.windows_qualification import run_physical_qualification
    api = FakeAPI()
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr('macloader.autoloader.readiness.hosted_ci_gate', lambda: ReadinessGate('CI', True, 'synthetic'))
    monkeypatch.setattr(subprocess, 'check_output', lambda *args, **kwargs: 'a' * 40)
    monkeypatch.setattr(WindowsRemovableAdapter, '_run_powershell', staticmethod(lambda _: json.dumps([dict(row(), Size=1024 ** 3)])))
    monkeypatch.setattr(click, 'prompt', lambda *args, **kwargs: 1)
    monkeypatch.setattr(click, 'confirm', lambda *args, **kwargs: True)
    monkeypatch.setattr('macloader.removable.windows_native.Win32Storage', lambda: api)
    class FaultBackend:
        def __init__(self, inventory: Any, api: Any):
            self.api = api
            self.cancel: Any = lambda: False
            self.invalidation_complete = False
        def set_cancel(self, callback: Any) -> None: self.cancel = callback
        def lock_and_dismount(self, device: RemovableDevice) -> None: pass
        def write(self, plan: WritePlan, source: Path) -> None:
            self.api.write(100, 0, b'A' * 1024)
            if self.cancel(): raise OSError('controlled cancellation')
        def flush(self, device: RemovableDevice) -> None: self.api.flush(100)
        def readback(self, plan: WritePlan, source: Path) -> bool: return bool(self.api.read(100, 0, 1024) == b'A' * 1024)
        def safe_eject(self, device: RemovableDevice) -> None:
            self.api.ioctl(100, SET_ATTRIBUTES, struct.pack('<IB3xQQ4I', 40, 0, 1, 1, 0, 0, 0, 0), 0)
        def invalidate(self, device: RemovableDevice, reason: str) -> None:
            self.api.write(100, 0, bytes(1024))
            self.api.flush(100)
            assert self.api.read(100, 0, 1024) == bytes(1024)
            self.safe_eject(device)
            self.invalidation_complete = True
    monkeypatch.setattr('macloader.removable.windows_native.WindowsNativeBackend', FaultBackend)
    path = tmp_path / f'{scenario}.json'
    run_physical_qualification(path, scenario)
    report = json.loads(path.read_text())
    assert report['cases'][scenario] == 'physical-pass'
    assert report['operation'] == 'injected-failure-withheld-ready-invalidation-offline-complete'
    assert report['method'] == 'controlled-fault-injection-on-physical-medium'
    assert report['production_approved'] is False and report['bootable'] is False


def test_failed_signature_invalidation_and_changed_volume_inventory_are_unready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    b, api, _ = backend(monkeypatch)
    target = device()
    b.lock_and_dismount(target)
    b.write(WritePlan(target, 1), tmp_path)
    monkeypatch.setattr(api, 'read', lambda *args: b'bad')
    with pytest.raises(UnsafeRemovableTarget, match='invalidated'): b.invalidate(target, 'failed')
    assert not b.invalidation_complete and b.handle is None and not b.verified
    b, api, _ = backend(monkeypatch)
    calls = iter([['SYSTEM', 'TARGET', 'OTHER'], ['SYSTEM', 'OTHER']])
    monkeypatch.setattr(api, 'volumes', lambda: next(calls))
    with pytest.raises(UnsafeRemovableTarget, match='volumes changed'): b.lock_and_dismount(target)
    assert b.handle is None and not api.blocks


def test_successful_readback_cannot_survive_a_later_corrupt_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    b, api, _ = backend(monkeypatch)
    plan = complete(b, tmp_path)
    api.corrupt = True
    assert not b.readback(plan, tmp_path) and not b.verified
    with pytest.raises(UnsafeRemovableTarget): b.safe_eject(plan.target)
    api.corrupt = False
    b.invalidate(plan.target, 'corrupt')
    assert b.invalidation_complete


def test_uefi_boot_paths_keep_canonical_short_names_and_long_aliases_do_not_collide(tmp_path: Path) -> None:
    from macloader.removable.windows_image import _aliases, _Node
    aliases = _aliases({name: _Node(name) for name in ('EFI', 'BOOT', 'BOOTx64.efi', 'F0000001', 'some long name', 'file.with.two.dots')})
    assert aliases['EFI'] == b'EFI        '
    assert aliases['BOOT'] == b'BOOT       '
    assert aliases['BOOTx64.efi'] == b'BOOTX64 EFI'
    assert len(set(aliases.values())) == len(aliases)
    assert aliases['some long name'] != b'F0000001   '
    image = WindowsMediaImage(image_plan(tmp_path), tmp_path)
    root_data = next(data for offset, data in image.extents if offset == image._offset(image.root))
    assert isinstance(root_data, bytes) and b'EFI        ' in root_data
