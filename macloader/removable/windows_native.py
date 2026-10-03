"""Native Windows storage boundary. Implementation is never qualification.

Only the selected whole disk is opened for writes. Structured discovery resolves
its stable identity; native handle descriptors independently bind serial, bus,
size and disk number. Volume GUIDs are locked, never selected by drive letter.
"""
import ctypes
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
from typing import Any, Callable, Protocol

from macloader.removable.writer import RemovableDevice, RemovableMediaWriter, UnsafeRemovableTarget, WritePlan
from macloader.removable.windows_image import WindowsMediaImage

BACKEND_VERSION = 'windows-native-gpt-fat32-v1'
LOCK = 0x90018
UNLOCK = 0x9001c
DISMOUNT = 0x90020
GET_ATTRIBUTES = 0x700f0
SET_ATTRIBUTES = 0x7c0f4
QUERY_PROPERTY = 0x2d1400
DEVICE_NUMBER = 0x2d1080
GEOMETRY = 0x700a0
EXTENTS = 0x560000


class StorageAPI(Protocol):
    def open(self, path: str, writable: bool) -> int: ...
    def close(self, handle: int) -> None: ...
    def ioctl(self, handle: int, code: int, data: bytes = b'', size: int = 65536) -> bytes: ...
    def read(self, handle: int, offset: int, size: int) -> bytes: ...
    def write(self, handle: int, offset: int, data: bytes) -> None: ...
    def flush(self, handle: int) -> None: ...
    def volumes(self) -> list[str]: ...
    def system_volume(self) -> str: ...


def descriptor(data: bytes) -> tuple[str, int]:
    if len(data) < 36:
        raise UnsafeRemovableTarget('Native storage descriptor is incomplete')
    size = struct.unpack_from('<I', data, 4)[0]
    serial_offset, bus = struct.unpack_from('<II', data, 24)
    if not 36 <= size <= len(data) or not 36 <= serial_offset < size or data[8] != 0 or data[10] != 1:
        raise UnsafeRemovableTarget('Native USB identity is unavailable')
    end = data.find(b'\0', serial_offset, size)
    if end < 0:
        raise UnsafeRemovableTarget('Native USB serial is unterminated')
    try:
        serial = data[serial_offset:end].decode('ascii').strip()
    except UnicodeDecodeError as exc:
        raise UnsafeRemovableTarget('Native USB serial is invalid') from exc
    if not serial:
        raise UnsafeRemovableTarget('Native USB serial is missing')
    return serial, bus


def disk_extent(data: bytes) -> int:
    if len(data) < 32 or struct.unpack_from('<I', data)[0] != 1:
        raise UnsafeRemovableTarget('Volume has ambiguous physical disk extents')
    return int(struct.unpack_from('<I', data, 8)[0])


class Win32Storage:
    """ctypes calls with pointer-width handles and bounded page-aligned I/O."""
    def __init__(self, dll: Any = None) -> None:
        if dll is None and sys.platform != 'win32':
            raise UnsafeRemovableTarget('Native Windows storage is unavailable on this host')
        self.native_runtime = dll is None
        self.dll: Any = dll if dll is not None else getattr(ctypes, 'WinDLL')('kernel32', use_last_error=True)
        p, u, h, b = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int32
        signatures = {
            'CreateFileW': ([ctypes.c_wchar_p, u, u, p, u, u, h], h),
            'CloseHandle': ([h], b), 'DeviceIoControl': ([h, u, p, u, p, u, p, p], b),
            'SetFilePointerEx': ([h, ctypes.c_int64, p, u], b),
            'ReadFile': ([h, p, u, p, p], b), 'WriteFile': ([h, p, u, p, p], b),
            'FlushFileBuffers': ([h], b), 'VirtualAlloc': ([p, ctypes.c_size_t, u, u], p),
            'VirtualFree': ([p, ctypes.c_size_t, u], b),
            'FindFirstVolumeW': ([ctypes.c_wchar_p, u], h),
            'FindNextVolumeW': ([h, ctypes.c_wchar_p, u], b), 'FindVolumeClose': ([h], b),
            'GetWindowsDirectoryW': ([ctypes.c_wchar_p, u], u),
            'GetVolumePathNameW': ([ctypes.c_wchar_p, ctypes.c_wchar_p, u], b),
            'GetVolumeNameForVolumeMountPointW': ([ctypes.c_wchar_p, ctypes.c_wchar_p, u], b),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = args, result

    @staticmethod
    def _check(ok: object, operation: str) -> None:
        if not ok:
            # Device paths/serials and localised system messages stay private.
            raise UnsafeRemovableTarget(f'Native Windows {operation} failed')

    def open(self, path: str, writable: bool) -> int:
        if not re.fullmatch(r'\\\\\.\\PhysicalDrive[0-9]+|\\\\\?\\Volume\{[0-9A-Fa-f-]{36}\}', path):
            raise UnsafeRemovableTarget('Native storage requires a whole disk or volume GUID')
        value = self.dll.CreateFileW(path, 0xc0000000 if writable else 0x80000000, 3, None, 3,
            0xa0000000 if writable and 'PhysicalDrive' in path else 0, None)
        if value is None or value == ctypes.c_void_p(-1).value:
            raise UnsafeRemovableTarget('Native Windows storage handle could not be opened; administrator privileges may be required')
        return int(value)

    def close(self, handle: int) -> None:
        self._check(self.dll.CloseHandle(handle), 'handle close')

    def ioctl(self, handle: int, code: int, data: bytes = b'', size: int = 65536) -> bytes:
        incoming = ctypes.create_string_buffer(data) if data else None
        outgoing = ctypes.create_string_buffer(size) if size else None
        returned = ctypes.c_uint32()
        self._check(self.dll.DeviceIoControl(handle, code, incoming, len(data), outgoing, size, ctypes.byref(returned), None), 'storage control')
        if returned.value > size:
            raise UnsafeRemovableTarget('Native storage control exceeded its bound')
        return outgoing.raw[:returned.value] if outgoing else b''

    def _transfer(self, handle: int, offset: int, size: int, data: bytes | None) -> bytes:
        if offset < 0 or offset % 512 or not 0 < size <= 1024 * 1024 or size % 512:
            raise UnsafeRemovableTarget('Native storage I/O is not sector aligned or bounded')
        memory = self.dll.VirtualAlloc(None, size, 0x3000, 4)
        self._check(memory, 'aligned allocation')
        try:
            self._check(self.dll.SetFilePointerEx(handle, offset, None, 0), 'seek')
            returned = ctypes.c_uint32()
            if data is not None:
                ctypes.memmove(memory, data, size)
            method = self.dll.ReadFile if data is None else self.dll.WriteFile
            self._check(method(handle, memory, size, ctypes.byref(returned), None), 'read' if data is None else 'write')
            if returned.value != size:
                raise UnsafeRemovableTarget('Native Windows storage returned short I/O')
            return ctypes.string_at(memory, size) if data is None else b''
        finally:
            self._check(self.dll.VirtualFree(memory, 0, 0x8000), 'aligned buffer release')

    def read(self, handle: int, offset: int, size: int) -> bytes:
        return self._transfer(handle, offset, size, None)

    def write(self, handle: int, offset: int, data: bytes) -> None:
        self._transfer(handle, offset, len(data), data)

    def flush(self, handle: int) -> None:
        self._check(self.dll.FlushFileBuffers(handle), 'flush')

    def volumes(self) -> list[str]:
        buffer = ctypes.create_unicode_buffer(1024)
        handle = self.dll.FindFirstVolumeW(buffer, len(buffer))
        if handle is None or handle == ctypes.c_void_p(-1).value:
            raise UnsafeRemovableTarget('Native Windows volume inventory is unavailable')
        result = []
        try:
            while True:
                result.append(buffer.value.rstrip('\\'))
                if len(result) > 1024:
                    raise UnsafeRemovableTarget('Native Windows volume inventory exceeds its bound')
                if not self.dll.FindNextVolumeW(handle, buffer, len(buffer)):
                    if getattr(ctypes, 'get_last_error')() != 18:  # ERROR_NO_MORE_FILES
                        raise UnsafeRemovableTarget('Native Windows volume inventory was interrupted')
                    break
        finally:
            self._check(self.dll.FindVolumeClose(handle), 'volume inventory close')
        return result

    def system_volume(self) -> str:
        windows, mount, volume = (ctypes.create_unicode_buffer(1024) for _ in range(3))
        size = self.dll.GetWindowsDirectoryW(windows, len(windows))
        if not 0 < size < len(windows):
            raise UnsafeRemovableTarget('Native Windows system location is unavailable')
        self._check(self.dll.GetVolumePathNameW(windows, mount, len(mount)), 'system mount lookup')
        self._check(self.dll.GetVolumeNameForVolumeMountPointW(mount, volume, len(volume)), 'system volume lookup')
        return volume.value.rstrip('\\')


class WindowsNativeBackend:
    """Implements WindowsQualifiedBackend; approval is an exact reviewed record."""
    def __init__(self, inventory: Callable[[], list[dict[str, Any]]], api: StorageAPI | None = None,
                 cancel: Callable[[], bool] | None = None):
        self.inventory, self.api = inventory, api
        self.cancel = cancel or (lambda: False)
        self.device: RemovableDevice | None = None
        self.handle: int | None = None
        self.volume_handles: list[int] = []
        self.volume_paths: dict[str, int] = {}
        self.number: int | None = None
        self.chunks: list[tuple[int, int, str]] = []
        self.verified = False
        self.touched = False
        self.remounted = False
        self.invalidation_complete = False

    @property
    def production_qualified(self) -> bool:
        from macloader.removable.windows_qualification import approved_backend
        if self.api is not None and (type(self.api) is not Win32Storage or not self.api.native_runtime):
            return False
        return approved_backend()

    def _api(self) -> StorageAPI:
        if self.api is None:
            self.api = Win32Storage()
        return self.api

    def set_cancel(self, cancel: Callable[[], bool]) -> None:
        self.cancel = cancel

    def _current(self, device: RemovableDevice, changed_layout: bool = False) -> int:
        from macloader.removable.adapters import WindowsRemovableAdapter
        rows = self.inventory()
        required = {'Number', 'FriendlyName', 'SerialNumber', 'Size', 'IsBoot', 'IsSystem', 'IsReadOnly', 'Mounted', 'BusType', 'Partitions'}
        if not all(isinstance(row, dict) and required <= set(row) and all(isinstance(row[key], bool) for key in ('IsBoot', 'IsSystem', 'IsReadOnly', 'Mounted')) for row in rows):
            raise UnsafeRemovableTarget('Windows disk safety inventory is incomplete')
        adapter = WindowsRemovableAdapter(runner=lambda _: json.dumps(rows), platform='win32')
        devices = adapter.enumerate()
        matches = [(row, found) for row, found in zip(rows, devices) if found.device_id == device.device_id]
        if len(matches) != 1:
            raise UnsafeRemovableTarget('Selected Windows USB is missing or ambiguous')
        row, found = matches[0]
        if changed_layout:
            from dataclasses import replace
            found = replace(found, partitions=device.partitions)
        if found != device:
            raise UnsafeRemovableTarget('Selected Windows USB changed after confirmation')
        RemovableMediaWriter._assert_safe(found, 1)
        if not found.serial or found.device_id != 'windows:serial:' + found.serial:
            raise UnsafeRemovableTarget('Selected Windows USB lacks stable identity')
        number = row.get('Number')
        if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= 65535:
            raise UnsafeRemovableTarget('Native Windows disk locator is invalid')
        return number

    def _native_binding(self, handle: int, device: RemovableDevice, number: int) -> None:
        api = self._api()
        serial, bus = descriptor(api.ioctl(handle, QUERY_PROPERTY, bytes(12)))
        geometry = api.ioctl(handle, GEOMETRY)
        address = api.ioctl(handle, DEVICE_NUMBER)
        attributes = api.ioctl(handle, GET_ATTRIBUTES)
        if len(geometry) < 32 or len(address) != 12 or len(attributes) < 16:
            raise UnsafeRemovableTarget('Native Windows disk binding is incomplete')
        dtype, actual, partition = struct.unpack('<III', address)
        sector, capacity = struct.unpack_from('<IQ', geometry, 20)
        flags = struct.unpack_from('<Q', attributes, 8)[0]
        if serial != device.serial or bus != 7 or dtype != 7 or actual != number or partition not in (0, 0xffffffff) or sector != 512 or capacity != device.capacity_bytes or flags & 2:
            raise UnsafeRemovableTarget('Native Windows disk binding disagrees with selected USB')

    def _volume_disk(self, path: str) -> int:
        api = self._api()
        if path in self.volume_paths:
            return disk_extent(api.ioctl(self.volume_paths[path], EXTENTS))
        handle = api.open(path, False)
        try:
            return disk_extent(api.ioctl(handle, EXTENTS))
        finally:
            api.close(handle)

    def lock_and_dismount(self, device: RemovableDevice) -> None:
        if self.handle is not None:
            raise UnsafeRemovableTarget('Native Windows writer is already in use')
        api = self._api()
        number = self._current(device)
        if self._volume_disk(api.system_volume()) == number:
            raise UnsafeRemovableTarget('Native Windows system disk is never a USB target')
        volumes = [path for path in api.volumes() if self._volume_disk(path) == number]
        try:
            for path in volumes:
                handle = api.open(path, True)
                self.volume_handles.append(handle)
                api.ioctl(handle, LOCK, size=0)
                self.volume_paths[path] = handle
                if disk_extent(api.ioctl(handle, EXTENTS)) != number:
                    raise UnsafeRemovableTarget("Volume changed during native locking")
                api.ioctl(handle, DISMOUNT, size=0)
            self.handle = api.open(r'\\.\PhysicalDrive' + str(number), True)
            self.device, self.number = device, number
            self._native_binding(self.handle, device, number)
            if self._current(device) != number:
                raise UnsafeRemovableTarget('USB locator changed during locking')
            current_volumes = [path for path in api.volumes() if self._volume_disk(path) == number]
            if set(current_volumes) != set(volumes):
                raise UnsafeRemovableTarget('USB volumes changed during locking')
            self.chunks, self.verified, self.touched, self.remounted = [], False, False, False
            self.invalidation_complete = False
        except BaseException:
            self._release()
            raise

    def _locked(self, device: RemovableDevice) -> int:
        if self.handle is None or self.device != device or self.number is None:
            raise UnsafeRemovableTarget('Selected Windows USB has no bound native lock')
        self._native_binding(self.handle, device, self.number)
        return self.handle

    def write(self, plan: WritePlan, source_dir: Path) -> None:
        image = WindowsMediaImage(plan, source_dir)  # Reject bad layout before I/O.
        handle = self._locked(plan.target)
        if self._current(plan.target) != self.number:
            raise UnsafeRemovableTarget('Selected USB changed immediately before write')
        for offset, data in image.chunks():
            if self.cancel():
                raise UnsafeRemovableTarget('Windows USB write cancelled; medium is unready')
            self.touched = True  # Even a failed/short native write may modify sectors.
            self._api().write(handle, offset, data)
            self.chunks.append((offset, len(data), hashlib.sha256(data).hexdigest()))

    def flush(self, device: RemovableDevice) -> None:
        self._api().flush(self._locked(device))

    def readback(self, plan: WritePlan, source_dir: Path) -> bool:
        del source_dir  # Shared writer verifies the immutable source; verify raw bytes.
        self.verified = False
        handle = self._locked(plan.target)
        if self._current(plan.target, changed_layout=True) != self.number or not self.chunks:
            raise UnsafeRemovableTarget('USB changed before native readback')
        offset_end = 0
        for offset, size, digest in self.chunks:
            if self.cancel():
                raise UnsafeRemovableTarget('Windows USB readback cancelled')
            if offset != offset_end or hashlib.sha256(self._api().read(handle, offset, size)).hexdigest() != digest:
                return False
            offset_end += size
        self.verified = offset_end == plan.target.capacity_bytes
        return self.verified

    def remount(self, device: RemovableDevice) -> None:
        if self.device is None:
            return
        self._locked(device)
        # Keep raw disk handle bound until final offline/invalidation. Unlocking
        # old volumes must not refresh/mount the newly written GPT automatically.
        if not self.verified:
            self._release_volumes()
        self.remounted = True

    def safe_eject(self, device: RemovableDevice) -> None:
        handle = self._locked(device)
        if not self.verified:
            raise UnsafeRemovableTarget('Windows USB cannot be ejected as ready without full readback')
        try:
            self._api().flush(handle)
            self._offline(handle)
            self._release()
        except BaseException:
            self.verified = False
            raise

    def _offline(self, handle: int) -> None:
        api = self._api()
        # SET_DISK_ATTRIBUTES is 40 bytes, non-persistent, OFFLINE bit only.
        api.ioctl(handle, SET_ATTRIBUTES, struct.pack('<IB3xQQ4I', 40, 0, 1, 1, 0, 0, 0, 0), size=0)
        api.ioctl(handle, 0x70140, size=0)  # IOCTL_DISK_UPDATE_PROPERTIES
        attributes = api.ioctl(handle, GET_ATTRIBUTES)
        if len(attributes) < 16 or not struct.unpack_from('<Q', attributes, 8)[0] & 1:
            raise UnsafeRemovableTarget('Windows did not confirm the exact USB offline')

    def invalidate(self, device: RemovableDevice, reason: str) -> None:
        del reason
        self.verified = False
        if self.handle is None:
            return
        handle = self._locked(device)
        completed = False
        try:
            if self.touched:
                block = bytes(min(device.capacity_bytes, 1024 * 1024))
                offsets = (0, device.capacity_bytes - len(block))
                for offset in offsets:
                    self._api().write(handle, offset, block)
                self._api().flush(handle)
                for offset in offsets:
                    if self._api().read(handle, offset, len(block)) != block:
                        raise UnsafeRemovableTarget("Failed USB signatures could not be verified invalidated")
                self._offline(handle)
                completed = True
        finally:
            self.verified = False
            self._release()
        self.invalidation_complete = completed

    def _release_volumes(self) -> None:
        handles, self.volume_handles = self.volume_handles, []
        self.volume_paths = {}
        first: Exception | None = None
        for handle in reversed(handles):
            try:
                self._api().ioctl(handle, UNLOCK, size=0)
            except Exception as exc:
                first = first or exc
            finally:
                self._api().close(handle)
        if first:
            raise first

    def _release(self) -> None:
        try:
            self._release_volumes()
        finally:
            if self.handle is not None:
                handle, self.handle = self.handle, None
                self._api().close(handle)
            self.device, self.number = None, None
