"""Platform removable-device discovery and guarded writing boundaries."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterable, Optional, Protocol

_fcntl: Any = None
try:  # fcntl is absent on Windows; this module remains importable there.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised by Windows packaging
    _fcntl = None

from macloader.removable.writer import (
    MAX_MEDIA_FILE_BYTES,
    MAX_MEDIA_TOTAL_BYTES,
    RemovableDevice,
    RemovableMediaWriter,
    UnsafeRemovableTarget,
    WritePlan,
)


class WindowsQualifiedBackend(Protocol):
    """Privileged boundary supplied only after Windows media qualification."""

    # Native backends must explicitly attest that they implement the
    # production-qualified lock/write/flush/readback contract.  Merely
    # exposing methods is not enough to enable destructive media operations.
    production_qualified: bool

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
        synthetic_test_mode: bool = False,
    ) -> None:
        self._runner = runner or self._run_powershell
        self._backend = backend
        self._platform = platform or sys.platform
        self._synthetic_test_mode = synthetic_test_mode

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
        if not self._synthetic_test_mode and getattr(self._backend, "production_qualified", False) is not True:
            return AdapterStatus(
                "windows", True, False,
                "Windows backend must explicitly attest production media qualification",
            )
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
        # Remount only after successful verification. On false or exception the
        # shared writer calls invalidate first; invalidate owns the final remount.
        verified = bool(self._backend.readback(plan, source_dir))
        if verified:
            self._backend.remount(plan.target)
        return verified

    def writer(self) -> RemovableMediaWriter:
        """Build the shared guarded writer; no device is touched by this call."""
        return RemovableMediaWriter(
            destructive_write=self.write,
            enumerator=self.enumerate,
            readback_verifier=self.readback,
            invalidator=lambda plan, reason: self.invalidate(plan, reason),
            # A native backend must explicitly attest that it is the
            # production-qualified path before the writer requires the
            # published EFI/Recovery cross-binding checks.  Test backends do
            # not carry this attestation and remain synthetic only.
            require_published_artifacts=self.status.qualified and not self._synthetic_test_mode,
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


class LinuxBlockDeviceBackend:
    """Root-only GPT/FAT32 writer for stable whole-disk Linux by-id targets.

    The implementation is deliberately not marked production-qualified. A
    sacrificial physical-device campaign must qualify the running kernel,
    controller, mount manager and eject path before this backend can be enabled.
    """

    production_qualified = False
    _by_id_root = Path("/dev/disk/by-id")
    _mount_parent = Path("/run")

    def __init__(
        self,
        enumerate_devices: Callable[[], list[RemovableDevice]],
        runner: Optional[Callable[[list[str], Optional[str]], str]] = None,
    ) -> None:
        self._enumerate_devices = enumerate_devices
        self._runner = runner
        self._locked: dict[str, tuple[int, Path]] = {}
        self._mounts: dict[str, tuple[Path, Path]] = {}

    def _run(self, args: list[str], input_text: Optional[str] = None) -> str:
        if self._runner is not None:
            return self._runner(args, input_text)
        try:
            completed = subprocess.run(
                args, input=input_text, text=True, capture_output=True,
                timeout=90, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UnsafeRemovableTarget(f"Linux media operation could not run ({Path(args[0]).name})") from exc
        if completed.returncode != 0:
            # Command stderr often contains device identifiers; keep it out of
            # diagnostics and expose only the operation name.
            raise UnsafeRemovableTarget(f"Linux media operation failed ({Path(args[0]).name})")
        return completed.stdout

    @staticmethod
    def _require_root() -> None:
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            raise UnsafeRemovableTarget("Linux USB writing requires root privileges; rerun with an approved sudo workflow")

    @classmethod
    def _device_path(cls, device: RemovableDevice) -> Path:
        prefix = "linux:by-id:"
        if not device.device_id.startswith(prefix):
            raise UnsafeRemovableTarget("Linux writes require a stable whole-device by-id identity")
        alias = device.device_id[len(prefix):]
        if not alias or Path(alias).name != alias or not re.fullmatch(r"(?:usb|wwn)-[A-Za-z0-9._:+-]+", alias):
            raise UnsafeRemovableTarget("Linux whole-device identity is malformed")
        return cls._by_id_root / alias

    def _current(
        self,
        expected: RemovableDevice,
        required_bytes: int = 1,
        *,
        allow_created_partition: bool = False,
    ) -> RemovableDevice:
        matches = [item for item in self._enumerate_devices() if item.device_id == expected.device_id]
        if len(matches) != 1:
            raise UnsafeRemovableTarget("Selected Linux USB identity is missing or ambiguous")
        current = matches[0]
        if (
            current.capacity_bytes != expected.capacity_bytes
            or current.serial != expected.serial
            or current.model != expected.model
            or (not allow_created_partition and current.partitions != expected.partitions)
            or current.is_system_disk != expected.is_system_disk
            or current.is_removable != expected.is_removable
            or current.mounted != expected.mounted
            or not current.whole_device
        ):
            raise UnsafeRemovableTarget("Selected Linux USB identity changed since planning")
        RemovableMediaWriter._assert_safe(current, required_bytes)
        path = self._device_path(current)
        try:
            resolved = path.resolve(strict=True)
            mode = resolved.stat().st_mode
        except OSError as exc:
            raise UnsafeRemovableTarget("Selected Linux USB whole-device alias is missing") from exc
        if not stat.S_ISBLK(mode) or not str(resolved).startswith("/dev/"):
            raise UnsafeRemovableTarget("Selected Linux target is not a physical block device")
        return current

    def lock_and_dismount(self, device: RemovableDevice) -> None:
        self._require_root()
        if _fcntl is None:
            raise UnsafeRemovableTarget("Linux exclusive device locking is unavailable on this host")
        current = self._current(device)
        path = self._device_path(current)
        try:
            descriptor = os.open(path, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
            _fcntl.flock(descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except OSError as exc:
            raise UnsafeRemovableTarget("Unable to exclusively lock the selected Linux USB device") from exc
        self._locked[current.device_id] = (descriptor, path)
        try:
            # A second mount check closes the time-of-check/open race. Never
            # silently unmount volumes which a desktop service may remount.
            self._current(current)
        except Exception:
            self._close_lock(current.device_id)
            raise

    def write(self, plan: WritePlan, source_dir: Path) -> None:
        current = self._current(plan.target, plan.required_bytes)
        if current.device_id not in self._locked:
            raise UnsafeRemovableTarget("Linux USB device was not exclusively locked")
        path = self._device_path(current)
        sectors = current.capacity_bytes // 512
        table = self._partition_script(sectors)
        self._run(["sfdisk", "--wipe", "always", "--wipe-partitions", "always", str(path)], table)
        self._run(["blockdev", "--rereadpt", str(path)])
        partition = self._partition_path(path)
        self._run(["mkfs.vfat", "-F", "32", "-n", "MACLOADER", str(partition)])
        mount = Path(tempfile.mkdtemp(prefix="macloader-usb-", dir=self._mount_parent))
        try:
            self._run(["mount", "-t", "vfat", "-o", "nosuid,nodev,noexec", str(partition), str(mount)])
            self._mounts[current.device_id] = (mount, partition)
            self._copy_boot_layout(source_dir, mount, plan)
            self._sync_mount(mount)
        finally:
            if current.device_id in self._mounts:
                self._unmount(current.device_id)
            else:
                mount.rmdir()

    def flush(self, device: RemovableDevice) -> None:
        locked = self._locked.get(device.device_id)
        if locked is None:
            raise UnsafeRemovableTarget("Linux USB device lock was lost before flush")
        os.fsync(locked[0])
        self._run(["blockdev", "--flushbufs", str(locked[1])])

    def readback(self, plan: WritePlan, source_dir: Path) -> bool:
        current = self._current(plan.target, plan.required_bytes, allow_created_partition=True)
        locked = self._locked.get(current.device_id)
        if locked is None:
            raise UnsafeRemovableTarget("Linux USB device lock was lost before readback")
        partition = self._partition_path(locked[1])
        mount = Path(tempfile.mkdtemp(prefix="macloader-readback-", dir=self._mount_parent))
        try:
            self._run(["mount", "-t", "vfat", "-o", "ro,nosuid,nodev,noexec", str(partition), str(mount)])
            self._mounts[current.device_id] = (mount, partition)
            verified = self._verify_boot_layout(source_dir, mount, plan)
            return verified
        finally:
            if current.device_id in self._mounts:
                self._unmount(current.device_id)

    def invalidate(self, device: RemovableDevice, reason: str) -> None:
        del reason  # Raw private identifiers and user text are never persisted.
        locked = self._locked.get(device.device_id)
        if locked is None:
            # Failure before lock acquisition has not touched the device.
            return
        if device.device_id in self._mounts:
            self._unmount(device.device_id)
        descriptor, path = locked
        size = device.capacity_bytes
        try:
            self._invalidate_descriptor(descriptor, size)
            self._run(["blockdev", "--flushbufs", str(path)])
        except OSError as exc:
            raise UnsafeRemovableTarget("Failed media could not be invalidated") from exc
        self._close_lock(device.device_id)
        self._safe_eject(path)

    @staticmethod
    def _invalidate_descriptor(descriptor: int, size: int) -> None:
        """Clear both GPT copies and signatures with bounded 1 MiB writes."""
        zeroes = memoryview(bytes(1024 * 1024))
        for offset in (0, max(0, size - len(zeroes))):
            os.lseek(descriptor, offset, os.SEEK_SET)
            remaining = zeroes
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("short invalidation write")
                remaining = remaining[written:]
        os.fsync(descriptor)

    def safe_eject(self, device: RemovableDevice) -> None:
        locked = self._locked.get(device.device_id)
        if locked is None:
            return
        if device.device_id in self._mounts:
            self._unmount(device.device_id)
        self._run(["blockdev", "--flushbufs", str(locked[1])])
        path = locked[1]
        self._close_lock(device.device_id)
        self._safe_eject(path)

    @classmethod
    def _partition_path(cls, disk: Path) -> Path:
        alias = disk.name + "-part1"
        partition = cls._by_id_root / alias
        for _ in range(50):
            try:
                resolved = partition.resolve(strict=True)
                if stat.S_ISBLK(resolved.stat().st_mode) and str(resolved).startswith("/dev/"):
                    return partition
            except OSError:
                pass
            time.sleep(0.1)
        raise UnsafeRemovableTarget("Linux did not expose the prepared EFI partition through its stable by-id alias")

    @staticmethod
    def _partition_script(sectors: int) -> str:
        first_lba = 2048
        partition_sectors = sectors - first_lba - 33
        if partition_sectors <= 0:
            raise UnsafeRemovableTarget("Linux USB device is too small for a GPT EFI layout")
        return (
            "label: gpt\nunit: sectors\nfirst-lba: 2048\n\n"
            f"start={first_lba}, size={partition_sectors}, type=uefi, name=MACLOADER\n"
        )

    def _copy_boot_layout(self, source_dir: Path, mount: Path, plan: WritePlan) -> None:
        total = 0
        for record in plan.expected_files:
            relative = Path(record.relative_path)
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise UnsafeRemovableTarget("Media plan contains an unsafe relative path")
            source = Path(source_dir) / relative
            source_status = os.lstat(source)
            if not stat.S_ISREG(source_status.st_mode) or source.is_symlink() or source_status.st_size != record.size_bytes:
                raise UnsafeRemovableTarget("Validated media source changed before Linux USB write")
            if source_status.st_size > MAX_MEDIA_FILE_BYTES:
                raise UnsafeRemovableTarget("A media payload file exceeds the bounded size limit")
            total += source_status.st_size
            if total > MAX_MEDIA_TOTAL_BYTES:
                raise UnsafeRemovableTarget("Media payload exceeds the bounded total size limit")
            if _sha256_file(source) != record.sha256:
                raise UnsafeRemovableTarget("Validated media source digest changed before Linux USB write")
            target_relative = self._boot_relative(relative)
            destination = mount / target_relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                with os.fdopen(source_fd, "rb", closefd=False) as reader, destination.open("xb") as writer:
                    remaining = record.size_bytes
                    while remaining:
                        chunk = reader.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise UnsafeRemovableTarget("Media source ended during bounded USB copy")
                        writer.write(chunk)
                        remaining -= len(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
            finally:
                os.close(source_fd)

    @classmethod
    def _verify_boot_layout(cls, source_dir: Path, mount: Path, plan: WritePlan) -> bool:
        for record in plan.expected_files:
            relative = Path(record.relative_path)
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                return False
            source = Path(source_dir) / relative
            target = mount / cls._boot_relative(relative)
            try:
                if target.is_symlink() or not target.is_file() or target.stat().st_size != record.size_bytes:
                    return False
                if _sha256_file(source) != record.sha256 or _sha256_file(target) != record.sha256:
                    return False
            except OSError:
                return False
        return bool(plan.expected_files)

    @staticmethod
    def _boot_relative(relative: Path) -> Path:
        if relative.parts and relative.parts[0].casefold() == "recovery":
            return Path("com.apple.recovery.boot", *relative.parts[1:])
        return relative

    def _sync_mount(self, mount: Path) -> None:
        descriptor = os.open(mount, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            if hasattr(os, "syncfs"):
                os.syncfs(descriptor)
            else:
                os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _unmount(self, device_id: str) -> None:
        mount_entry = self._mounts.get(device_id)
        if mount_entry is None:
            return
        mount, _partition = mount_entry
        self._run(["umount", "--", str(mount)])
        mount.rmdir()
        self._mounts.pop(device_id, None)

    def _safe_eject(self, disk: Path) -> None:
        self._run(["udisksctl", "power-off", "--no-user-interaction", "-b", str(disk)])

    def _close_lock(self, device_id: str) -> None:
        locked = self._locked.pop(device_id, None)
        if locked is not None and _fcntl is not None:
            try:
                _fcntl.flock(locked[0], _fcntl.LOCK_UN)
            finally:
                os.close(locked[0])


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class LinuxRemovableAdapter:
    """Linux whole-device discovery with a production-gated write backend."""

    platform_name = "linux"
    _columns = "NAME,PATH,TYPE,SIZE,MODEL,SERIAL,WWN,RM,RO,TRAN,MOUNTPOINTS,PKNAME,MAJ:MIN"

    def __init__(
        self,
        advertised: bool = False,
        enumerator: Optional[Callable[[], list[RemovableDevice]]] = None,
        backend: Optional[LinuxBlockDeviceBackend] = None,
        runner: Optional[Callable[[list[str], Optional[str]], str]] = None,
        platform: Optional[str] = None,
        by_id_root: Optional[Path] = None,
    ) -> None:
        self._advertised = advertised
        self._platform = platform or sys.platform
        self._runner = runner
        self._by_id_root = Path(by_id_root or LinuxBlockDeviceBackend._by_id_root)
        self._enumerator = enumerator or (self._discover if advertised and self._platform.startswith("linux") else None)
        self._backend = backend or (LinuxBlockDeviceBackend(self.enumerate) if advertised else None)

    @property
    def status(self) -> AdapterStatus:
        if self._platform != "linux":
            return AdapterStatus("linux", False, False, "Linux adapter is unavailable on this host")
        if not self._advertised:
            return AdapterStatus("linux", False, False, "Linux removable-media support is not advertised")
        if self._enumerator is None:
            return AdapterStatus("linux", True, False, "Linux whole-device discovery is unavailable")
        if self._backend is not None and getattr(self._backend, "production_qualified", False) is True:
            return AdapterStatus("linux", True, True, "Linux removable-media backend has completed physical qualification")
        return AdapterStatus(
            "linux", True, False,
            "Linux whole-device discovery and guarded writer are implemented; sacrificial physical-media qualification is still required",
        )

    def _output(self, args: list[str], input_text: Optional[str] = None) -> str:
        if self._runner is not None:
            return self._runner(args, input_text)
        try:
            completed = subprocess.run(args, input=input_text, text=True, capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UnsafeRemovableTarget(f"Linux disk discovery could not run ({Path(args[0]).name})") from exc
        if completed.returncode != 0:
            raise UnsafeRemovableTarget(f"Linux disk discovery failed ({Path(args[0]).name})")
        return completed.stdout

    def _discover(self) -> list[RemovableDevice]:
        raw = self._output(["lsblk", "--json", "--bytes", "--paths", "--output", self._columns])
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UnsafeRemovableTarget("Linux block-device discovery returned malformed JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("blockdevices"), list):
            raise UnsafeRemovableTarget("Linux block-device discovery returned an invalid shape")
        try:
            root_mm = self._output(["findmnt", "-nro", "MAJ:MIN", "--target", "/"]).strip()
        except UnsafeRemovableTarget:
            root_mm = ""
        by_id: dict[str, str] = {}
        try:
            aliases = sorted(self._by_id_root.iterdir())
        except OSError:
            aliases = []
        for alias in aliases:
            if not alias.name.startswith(("usb-", "wwn-")) or "-part" in alias.name or not alias.is_symlink():
                continue
            try:
                by_id[str(alias.resolve(strict=True))] = alias.name
            except OSError:
                continue

        def flatten(rows: list[Any], ancestors: tuple[dict[str, Any], ...] = ()) -> list[tuple[dict[str, Any], tuple[dict[str, Any], ...]]]:
            result: list[tuple[dict[str, Any], tuple[dict[str, Any], ...]]] = []
            for row in rows:
                if not isinstance(row, dict):
                    raise UnsafeRemovableTarget("Linux block-device discovery returned an invalid row")
                result.append((row, ancestors))
                children = row.get("children") or []
                if isinstance(children, list):
                    result.extend(flatten(children, (*ancestors, row)))
            return result

        devices: list[RemovableDevice] = []
        for disk, _ancestors in flatten(payload["blockdevices"]):
            if str(disk.get("type", "")).lower() != "disk":
                continue
            path = str(disk.get("path") or "")
            stable_alias = by_id.get(path)
            if not stable_alias:
                continue
            children = disk.get("children") or []
            nested = flatten(children) if isinstance(children, list) else []
            parts = tuple(str(item[0].get("path") or item[0].get("name") or "") for item in nested)
            mountpoints: list[str] = []
            for node, _parent in [(disk, ())] + nested:
                values = node.get("mountpoints")
                if isinstance(values, list):
                    mountpoints.extend(str(value) for value in values if value)
                elif node.get("mountpoint"):
                    mountpoints.append(str(node["mountpoint"]))
            subtree = [disk, *(node for node, _parent in nested)]
            system_disk = not root_mm or any(str(item.get("maj:min") or "") == root_mm for item in subtree)
            transport = str(disk.get("tran") or "").lower()
            removable = transport == "usb" and _as_bool(disk.get("rm"))
            stable_serial = str(disk.get("serial") or disk.get("wwn") or stable_alias).strip()
            devices.append(RemovableDevice(
                device_id=f"linux:by-id:{stable_alias}",
                model=str(disk.get("model") or "Unknown Linux block device").strip(),
                capacity_bytes=_as_int(disk.get("size"), "capacity"),
                is_system_disk=system_disk or transport != "usb",
                is_removable=removable,
                mounted=bool(mountpoints),
                serial=stable_serial or None,
                vendor=None,
                read_only=_as_bool(disk.get("ro")),
                partitions=parts,
                whole_device=True,
                system_disk_ref=f"linux:by-id:{stable_alias}" if system_disk or transport != "usb" else None,
            ))
        return devices

    def enumerate(self) -> list[RemovableDevice]:
        if not self._advertised or self._enumerator is None:
            return []
        return list(self._enumerator())

    def write(self, plan: object, source_dir: Path) -> None:
        if self._backend is None or not self.status.qualified:
            raise UnsafeRemovableTarget("Linux destructive backend is not physically qualified")
        if not isinstance(plan, WritePlan):
            raise UnsafeRemovableTarget("Linux backend received an invalid media plan")
        self._backend.lock_and_dismount(plan.target)
        self._backend.write(plan, source_dir)
        self._backend.flush(plan.target)

    def readback(self, plan: object, source_dir: Path) -> bool:
        if self._backend is None or not self.status.qualified or not isinstance(plan, WritePlan):
            return False
        verified = self._backend.readback(plan, source_dir)
        if verified:
            self._backend.safe_eject(plan.target)
        return verified

    def invalidate(self, plan: object, reason: str) -> None:
        if self._backend is not None and self.status.qualified and isinstance(plan, WritePlan):
            self._backend.invalidate(plan.target, reason)

    def writer(self) -> RemovableMediaWriter:
        if not self.status.qualified or self._backend is None:
            return RemovableMediaWriter()
        return RemovableMediaWriter(
            destructive_write=self.write,
            enumerator=self.enumerate,
            readback_verifier=self.readback,
            invalidator=lambda plan, reason: self.invalidate(plan, reason),
            require_published_artifacts=True,
        )


def current_adapter() -> WindowsRemovableAdapter | LinuxRemovableAdapter:
    """Return the host adapter without enabling unqualified writes."""
    if sys.platform == "win32":
        return WindowsRemovableAdapter()
    return LinuxRemovableAdapter(advertised=True)
