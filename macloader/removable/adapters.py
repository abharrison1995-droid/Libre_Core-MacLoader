"""Platform removable-device discovery boundaries.

Only the Windows adapter is advertised by the P7 contract.  The adapter keeps
device discovery separate from the guarded writer: a drive letter or friendly
name is never used as the device identity, and a platform without a qualified
backend remains explicitly disabled.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable, Iterable, Optional, Protocol

from macloader.removable.writer import RemovableDevice, RemovableMediaWriter, UnsafeRemovableTarget, WritePlan


class WindowsQualifiedBackend(Protocol):
    """Privileged boundary supplied only after Windows media qualification."""

    def lock_and_dismount(self, device: RemovableDevice) -> None:
        ...

    def write(self, plan: WritePlan, source_dir: Path) -> None:
        ...

    def flush(self, device: RemovableDevice) -> None:
        ...

    def readback(self, plan: WritePlan, source_dir: Path) -> bool:
        ...

    def invalidate(self, device: RemovableDevice, reason: str) -> None:
        ...

    def remount(self, device: RemovableDevice) -> None:
        ...


@dataclass(frozen=True)
class AdapterStatus:
    platform: str
    advertised: bool
    qualified: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "advertised": self.advertised,
            "qualified": self.qualified,
            "reason": self.reason,
        }


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "online"}
    return False


def _as_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise UnsafeRemovableTarget(f"adapter returned an invalid {field}")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise UnsafeRemovableTarget(f"adapter returned an invalid {field}") from exc
    if result < 0:
        raise UnsafeRemovableTarget(f"adapter returned a negative {field}")
    return result


class WindowsRemovableAdapter:
    """Windows whole-disk discovery with an injectable command boundary.

    The default command is discovery-only.  A write backend must be injected
    after the platform has independently qualified locking, dismounting,
    bounded writes, flush, remount and readback.  This prevents merely running
    on Windows from silently enabling destructive operations.
    """

    platform_name = "windows"

    def __init__(
        self,
        runner: Optional[Callable[[str], str]] = None,
        backend: Optional[WindowsQualifiedBackend] = None,
        platform: Optional[str] = None,
    ) -> None:
        self._runner = runner or self._run_powershell
        self._backend = backend
        self._platform = platform or sys.platform

    @property
    def status(self) -> AdapterStatus:
        if self._platform != "win32":
            return AdapterStatus("windows", False, False, "Windows adapter is unavailable on this host")
        if self._backend is None:
            return AdapterStatus(
                "windows",
                True,
                False,
                "Windows discovery is available; destructive backend qualification is still required",
            )
        required = ("lock_and_dismount", "write", "flush", "readback", "remount", "invalidate")
        if not all(callable(getattr(self._backend, name, None)) for name in required):
            return AdapterStatus("windows", True, False, "Windows backend lacks lock, flush, remount, or readback qualification")
        return AdapterStatus("windows", True, True, "Windows discovery and injected write backend are enabled")

    def enumerate(self) -> list[RemovableDevice]:
        if self._platform != "win32":
            return []
        raw = self._runner(self._powershell_query())
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UnsafeRemovableTarget("Windows disk discovery returned malformed JSON") from exc
        rows: Iterable[Any]
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = [payload]
        else:
            raise UnsafeRemovableTarget("Windows disk discovery returned an invalid shape")

        devices: list[RemovableDevice] = []
        for row in rows:
            if not isinstance(row, dict):
                raise UnsafeRemovableTarget("Windows disk discovery returned a non-object row")
            serial = self._optional_text(row.get("SerialNumber"))
            number = row.get("Number")
            if serial:
                device_id = f"windows:serial:{serial}"
            else:
                # PhysicalDrive is a whole-disk path, never a drive letter.  A
                # missing serial deliberately remains non-qualified for writes.
                device_id = f"windows:physical:{_as_int(number, 'disk number')}"
            mount_state_known = "Mounted" in row
            mounted = _as_bool(row.get("Mounted")) if mount_state_known else True
            devices.append(
                RemovableDevice(
                    device_id=device_id,
                    model=self._required_text(row.get("FriendlyName"), "model"),
                    capacity_bytes=_as_int(row.get("Size"), "capacity"),
                    is_system_disk=_as_bool(row.get("IsSystem")) or _as_bool(row.get("IsBoot")),
                    is_removable=_as_bool(row.get("IsRemovable")) or str(row.get("BusType", "")).lower() == "usb",
                    mounted=mounted or str(row.get("OperationalStatus", "")).lower() == "mounted",
                    serial=serial,
                    vendor=self._optional_text(row.get("Manufacturer")),
                    read_only=_as_bool(row.get("IsReadOnly")),
                    partitions=self._partitions(row.get("Partitions")),
                    whole_device=not ("IsWholeDevice" in row) or _as_bool(row.get("IsWholeDevice")),
                    system_disk_ref=device_id if (_as_bool(row.get("IsSystem")) or _as_bool(row.get("IsBoot"))) else None,
                )
            )
        return devices

    def write(self, plan: object, source_dir: Path) -> None:
        if self._backend is None or not self.status.qualified:
            raise UnsafeRemovableTarget("Windows destructive backend is not qualified")
        if not isinstance(plan, WritePlan):
            raise UnsafeRemovableTarget("Windows backend received an invalid media plan")
        self._backend.lock_and_dismount(plan.target)
        try:
            self._backend.write(plan, source_dir)
            self._backend.flush(plan.target)
        except Exception:
            try:
                self._backend.invalidate(plan.target, "Windows media write failed before readback")
            finally:
                try:
                    self._backend.remount(plan.target)
                finally:
                    raise

    def invalidate(self, plan: object, reason: str) -> None:
        if self._backend is None or not self.status.qualified or not isinstance(plan, WritePlan):
            return
        try:
            self._backend.invalidate(plan.target, reason)
        finally:
            self._backend.remount(plan.target)

    def readback(self, plan: object, source_dir: Path) -> bool:
        if self._backend is None or not self.status.qualified:
            return False
        if not isinstance(plan, WritePlan):
            return False
        try:
            return bool(self._backend.readback(plan, source_dir))
        finally:
            self._backend.remount(plan.target)

    def writer(self) -> RemovableMediaWriter:
        """Build the shared guarded writer; no device is touched by this call."""
        return RemovableMediaWriter(
            destructive_write=self.write,
            enumerator=self.enumerate,
            readback_verifier=self.readback,
            invalidator=lambda plan, reason: self.invalidate(plan, reason),
        )

    @staticmethod
    def _optional_text(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _required_text(cls, value: Any, field: str) -> str:
        text = cls._optional_text(value)
        if not text:
            raise UnsafeRemovableTarget(f"Windows disk discovery returned no {field}")
        return text

    @classmethod
    def _partitions(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        values = value if isinstance(value, list) else [value]
        result: list[str] = []
        for item in values:
            text = cls._optional_text(item)
            if text:
                result.append(text)
        return tuple(result)

    @staticmethod
    def _powershell_query() -> str:
        # ConvertTo-Json is bounded to disk rows and intentionally excludes
        # volume drive letters.  The serial/model/whole-disk number are the
        # identity inputs used by the shared stale-target checks.
        return (
            "Get-Disk | Select-Object Number,FriendlyName,SerialNumber,Size,BusType,"
            "IsBoot,IsSystem,IsReadOnly,OperationalStatus,IsRemovable,Manufacturer,"
            "@{Name='Partitions';Expression={(Get-Partition -DiskNumber $_.Number -ErrorAction Stop | ForEach-Object { $_.PartitionNumber })}},"
            "@{Name='Mounted';Expression={((Get-Partition -DiskNumber $_.Number -ErrorAction Stop | Get-Volume -ErrorAction Stop) | Where-Object { $_.DriveLetter -or $_.Path }).Count -gt 0}} | "
            "ConvertTo-Json -Compress"
        )

    @staticmethod
    def _run_powershell(script: str) -> str:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return completed.stdout


class LinuxRemovableAdapter:
    """Explicitly opt-in Linux discovery boundary.

    Linux support is not advertised by default.  The class exists so a future
    qualified udev/lsblk backend has one shared contract and cannot be enabled
    accidentally by importing it.
    """

    def __init__(self, advertised: bool = False, enumerator: Optional[Callable[[], list[RemovableDevice]]] = None):
        self._advertised = advertised
        self._enumerator = enumerator

    @property
    def status(self) -> AdapterStatus:
        if not self._advertised:
            return AdapterStatus("linux", False, False, "Linux removable-media support is not advertised")
        if self._enumerator is None:
            return AdapterStatus("linux", True, False, "Linux discovery backend is not qualified")
        return AdapterStatus("linux", True, False, "Linux write/readback qualification is still required")

    def enumerate(self) -> list[RemovableDevice]:
        if not self._advertised or self._enumerator is None:
            return []
        return list(self._enumerator())


def current_adapter() -> WindowsRemovableAdapter | LinuxRemovableAdapter:
    """Return the host adapter without enabling unqualified writes."""
    if sys.platform == "win32":
        return WindowsRemovableAdapter()
    return LinuxRemovableAdapter(advertised=False)
