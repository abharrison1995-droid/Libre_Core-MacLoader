"""Platform removable-device discovery and guarded writing boundaries."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import zlib
from typing import Any, Callable, Iterable, Optional, Protocol

_fcntl: Any = None
try:  # fcntl is absent on Windows; this module remains importable there.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised by Windows packaging
    _fcntl = None

from macloader.removable.writer import (
    FAT32_MAX_FILE_BYTES,
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


# Linux block-device ioctls (asm-generic encodings used on x86_64 and arm64).
_BLKRRPART = 0x125F
_BLKGETSIZE64 = 0x80081272
_BLKSSZGET = 0x1268
_BLKFLSBUF = 0x1261
_BLKGETDISKSEQ = 0x80081280

SECTOR_BYTES = 512
ESP_FIRST_LBA = 2048
GPT_ENTRY_COUNT = 128
GPT_ENTRY_BYTES = 128
GPT_ENTRY_SECTORS = GPT_ENTRY_COUNT * GPT_ENTRY_BYTES // SECTOR_BYTES
ESP_TYPE_GUID = uuid.UUID("C12A7328-F81F-11D2-BA4B-00A0C93EC93B")
ESP_PARTITION_NAME = "MACLOADER"
FAT32_VOLUME_LABEL = "MACLOADER"
FAT32_MIN_CLUSTERS = 65525
# Planning margin for FAT tables, directory clusters and cluster slack, plus a
# floor that keeps mkfs.fat able to build a valid FAT32 (>= 65525 clusters).
MIN_ESP_BYTES = 64 * 1024 * 1024
FAT32_OVERHEAD_BYTES = 16 * 1024 * 1024
FAT32_PER_FILE_SLACK_BYTES = 64 * 1024
_FAT_FORBIDDEN = set('"*/:<>?\\|')


@dataclass(frozen=True)
class BlockIdentity:
    """Kernel identity of one attachment of one block device.

    ``diskseq`` is a per-boot monotonically increasing sequence number the
    kernel assigns to each disk attachment.  It is never reused, so a device
    that is unplugged and replaced by another (even one that receives the same
    major:minor number or by-id alias) cannot match a recorded identity.
    """

    rdev: int
    diskseq: int
    size_bytes: int
    logical_sector_bytes: int


@dataclass(frozen=True)
class PartitionGeometry:
    parent_rdev: int
    number: int
    start_sector: int
    size_sectors: int


@dataclass
class _LockedDevice:
    device_id: str
    descriptor: int
    alias: Path
    identity: BlockIdentity
    node_dir: Path
    disk_node: Path
    partition_descriptor: Optional[int] = None
    partition_node: Optional[Path] = None
    destructive_started: bool = False


class LinuxBlockProbe:
    """Thin kernel boundary used by :class:`LinuxBlockDeviceBackend`.

    Tests replace the probe to model hotplug races deterministically without
    touching a host device.
    """

    sysfs_block_root = Path("/sys/dev/block")
    # Private device nodes need a filesystem that permits them; /run and /tmp
    # are normally mounted nodev.  devtmpfs is not.
    node_parent = Path("/dev")

    def open_locked(self, path: Path) -> int:
        if _fcntl is None:
            raise UnsafeRemovableTarget("Linux exclusive device locking is unavailable on this host")
        descriptor = os.open(path, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
        try:
            _fcntl.flock(descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except OSError:
            os.close(descriptor)
            raise
        return descriptor

    def open_readonly(self, path: Path) -> int:
        # A read-only, non-blocking open neither modifies the device nor waits
        # on removable-media state.
        return os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))

    def identity_of_fd(self, descriptor: int) -> BlockIdentity:
        if _fcntl is None:
            raise UnsafeRemovableTarget("Linux block-device identity checks are unavailable on this host")
        status = os.fstat(descriptor)
        if not stat.S_ISBLK(status.st_mode):
            raise UnsafeRemovableTarget("Selected Linux target is not a physical block device")
        try:
            size = struct.unpack("=Q", _fcntl.ioctl(descriptor, _BLKGETSIZE64, bytes(8)))[0]
            sector = struct.unpack("=i", _fcntl.ioctl(descriptor, _BLKSSZGET, bytes(4)))[0]
            diskseq = struct.unpack("=Q", _fcntl.ioctl(descriptor, _BLKGETDISKSEQ, bytes(8)))[0]
        except OSError as exc:
            raise UnsafeRemovableTarget(
                "Linux kernel did not report block size and disk sequence identity (Linux 5.15+ is required)"
            ) from exc
        if diskseq <= 0:
            raise UnsafeRemovableTarget("Linux kernel reported no disk sequence identity")
        return BlockIdentity(status.st_rdev, diskseq, size, sector)

    def identity_of_path(self, path: Path) -> BlockIdentity:
        descriptor = self.open_readonly(path)
        try:
            return self.identity_of_fd(descriptor)
        finally:
            os.close(descriptor)

    def make_node(self, directory: Path, name: str, rdev: int) -> Path:
        """Create a private block node for exactly ``rdev`` in a root-only directory."""
        path = directory / name
        os.mknod(path, stat.S_IFBLK | 0o600, rdev)
        return path

    def reread_partitions(self, descriptor: int) -> None:
        if _fcntl is None:
            raise UnsafeRemovableTarget("Linux partition re-read is unavailable on this host")
        _fcntl.ioctl(descriptor, _BLKRRPART, 0)

    def find_partition(self, disk_rdev: int, number: int) -> Optional[int]:
        """Return the kernel device number of partition ``number`` of the disk."""
        base = self.sysfs_block_root / f"{os.major(disk_rdev)}:{os.minor(disk_rdev)}"
        try:
            children = list(base.resolve(strict=True).iterdir())
        except OSError:
            return None
        for child in children:
            try:
                if int((child / "partition").read_text(encoding="ascii").strip()) != number:
                    continue
                major, minor = (int(part) for part in (child / "dev").read_text(encoding="ascii").strip().split(":", 1))
            except (OSError, ValueError):
                continue
            return os.makedev(major, minor)
        return None

    def partition_geometry(self, rdev: int) -> PartitionGeometry:
        base = self.sysfs_block_root / f"{os.major(rdev)}:{os.minor(rdev)}"
        try:
            number = int((base / "partition").read_text(encoding="ascii").strip())
            start = int((base / "start").read_text(encoding="ascii").strip())
            size = int((base / "size").read_text(encoding="ascii").strip())
            parent_text = (base.resolve(strict=True).parent / "dev").read_text(encoding="ascii").strip()
            parent_major, parent_minor = (int(part) for part in parent_text.split(":", 1))
        except (OSError, ValueError) as exc:
            raise UnsafeRemovableTarget("Linux did not report the prepared partition geometry") from exc
        return PartitionGeometry(os.makedev(parent_major, parent_minor), number, start, size)

    def is_partition(self, rdev: int) -> bool:
        return (self.sysfs_block_root / f"{os.major(rdev)}:{os.minor(rdev)}" / "partition").exists()

    def drop_cache(self, descriptor: int) -> None:
        if _fcntl is not None:
            _fcntl.ioctl(descriptor, _BLKFLSBUF, 0)

    def mounted_device(self, mount: Path) -> int:
        return os.stat(mount).st_dev

    def is_mount(self, mount: Path) -> bool:
        return os.path.ismount(mount)


class LinuxBlockDeviceBackend:
    """Root-only GPT/FAT32 writer for stable whole-disk Linux by-id targets.

    The by-id alias is used exactly once, to find and lock the device.  From
    then on no command receives an alias or udev symlink.  The backend creates
    private block nodes for the verified device numbers of the locked disk and
    its created partition in a root-only directory, and keeps descriptors for
    both open.  An open descriptor holds the kernel disk, so its device number
    cannot be handed to a newly attached device while the operation runs.
    Before and after every command the backend proves that the locked
    descriptor, the private node and the original alias still report the same
    device number, disk sequence and size.  A retargeted alias or a replaced
    device therefore stops the operation.

    The partition table is re-read through the locked descriptor and the new
    partition is found through sysfs, so the operation does not wait on udev,
    which postpones events for a disk whose exclusive lock is held.

    The implementation is deliberately not marked production-qualified. A
    sacrificial physical-device campaign must qualify the running kernel,
    controller, mount manager and eject path before this backend can be enabled.
    """

    production_qualified = False
    _by_id_root = Path("/dev/disk/by-id")
    _mount_parent = Path("/run")
    _partition_wait_seconds = 5.0

    def __init__(
        self,
        enumerate_devices: Callable[[], list[RemovableDevice]],
        runner: Optional[Callable[[list[str], Optional[str]], str]] = None,
        probe: Optional[LinuxBlockProbe] = None,
    ) -> None:
        self._enumerate_devices = enumerate_devices
        self._runner = runner
        self._probe = probe or LinuxBlockProbe()
        self._locked: dict[str, _LockedDevice] = {}
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
        if device.device_id in self._locked:
            raise UnsafeRemovableTarget("Selected Linux USB device is already locked by this session")
        current = self._current(device)
        alias = self._device_path(current)
        try:
            descriptor = self._probe.open_locked(alias)
        except OSError as exc:
            raise UnsafeRemovableTarget("Unable to exclusively lock the selected Linux USB device") from exc
        node_dir: Optional[Path] = None
        try:
            identity = self._probe.identity_of_fd(descriptor)
            if (
                identity.size_bytes != current.capacity_bytes
                or identity.logical_sector_bytes != SECTOR_BYTES
                or self._probe.is_partition(identity.rdev)
            ):
                raise UnsafeRemovableTarget(
                    "Locked Linux device is not the planned whole 512-byte-sector disk"
                )
            node_dir = Path(tempfile.mkdtemp(prefix=".macloader-", dir=self._probe.node_parent))
            os.chmod(node_dir, 0o700)
            disk_node = self._probe.make_node(node_dir, "disk", identity.rdev)
        except BaseException:
            os.close(descriptor)
            if node_dir is not None:
                shutil.rmtree(node_dir, ignore_errors=True)
            raise
        self._locked[current.device_id] = _LockedDevice(current.device_id, descriptor, alias, identity, node_dir, disk_node)
        try:
            # A second enumeration and identity check closes the
            # time-of-check/open race. Never silently unmount volumes which a
            # desktop service may remount.
            self._current(current)
            self._pinned_disk(self._locked[current.device_id])
        except BaseException:
            self._close_lock(current.device_id)
            raise

    def _locked_device(self, device: RemovableDevice, stage: str) -> _LockedDevice:
        locked = self._locked.get(device.device_id)
        if locked is None:
            raise UnsafeRemovableTarget(f"Linux USB device lock was lost before {stage}")
        return locked

    def _held_identity(self, locked: _LockedDevice) -> BlockIdentity:
        try:
            return self._probe.identity_of_fd(locked.descriptor)
        except OSError as exc:
            raise UnsafeRemovableTarget("Locked Linux USB device disappeared") from exc

    def _pinned_disk(self, locked: _LockedDevice) -> Path:
        """Return the private disk node after proving it is the locked disk."""
        if self._held_identity(locked) != locked.identity:
            raise UnsafeRemovableTarget("Locked Linux USB device changed identity (hotplug or media change)")
        for label, path in (("private device node", locked.disk_node), ("by-id alias", locked.alias)):
            try:
                observed = self._probe.identity_of_path(path)
            except OSError as exc:
                raise UnsafeRemovableTarget(f"Locked Linux USB {label} is missing") from exc
            if observed != locked.identity:
                raise UnsafeRemovableTarget(
                    f"Locked Linux USB {label} now refers to a different device; refusing to continue"
                )
        return locked.disk_node

    def _attach_partition(self, locked: _LockedDevice, size_sectors: int) -> Path:
        """Find, open and pin partition 1 of the locked disk through the kernel."""
        self._release_partition(locked)
        deadline = time.monotonic() + self._partition_wait_seconds
        rdev = self._probe.find_partition(locked.identity.rdev, 1)
        while rdev is None:
            if time.monotonic() >= deadline:
                raise UnsafeRemovableTarget("Linux did not expose the prepared EFI partition for the locked disk")
            time.sleep(0.1)
            rdev = self._probe.find_partition(locked.identity.rdev, 1)
        node = self._probe.make_node(locked.node_dir, "part1", rdev)
        locked.partition_node = node
        try:
            locked.partition_descriptor = self._probe.open_readonly(node)
        except OSError as exc:
            raise UnsafeRemovableTarget("Prepared EFI partition could not be opened") from exc
        self._pinned_partition(locked, size_sectors)
        return node

    def _pinned_partition(self, locked: _LockedDevice, size_sectors: int) -> Path:
        """Prove the held partition belongs to the locked disk and planned layout."""
        if locked.partition_descriptor is None or locked.partition_node is None:
            raise UnsafeRemovableTarget("Prepared EFI partition is not attached")
        try:
            held = self._probe.identity_of_fd(locked.partition_descriptor)
            observed = self._probe.identity_of_path(locked.partition_node)
        except OSError as exc:
            raise UnsafeRemovableTarget("Prepared EFI partition disappeared") from exc
        geometry = self._probe.partition_geometry(held.rdev)
        if (
            held != observed
            or held.diskseq != locked.identity.diskseq
            or held.rdev == locked.identity.rdev
            or geometry.parent_rdev != locked.identity.rdev
            or geometry.number != 1
            or geometry.start_sector != ESP_FIRST_LBA
            or geometry.size_sectors != size_sectors
            or held.size_bytes != size_sectors * SECTOR_BYTES
        ):
            raise UnsafeRemovableTarget("Prepared EFI partition does not belong to the locked disk and planned layout")
        return locked.partition_node

    def _run_pinned(
        self, locked: _LockedDevice, args: list[str], input_text: Optional[str] = None,
        partition_sectors: Optional[int] = None,
    ) -> None:
        """Run one media command, proving the locked identity before and after it."""
        self._pinned_disk(locked)
        if partition_sectors is not None:
            self._pinned_partition(locked, partition_sectors)
        self._run(args, input_text)
        self._pinned_disk(locked)
        if partition_sectors is not None:
            self._pinned_partition(locked, partition_sectors)

    @staticmethod
    def _preflight_payload(plan: WritePlan, partition_sectors: int) -> None:
        """Reject payloads the FAT32 ESP cannot hold before any lock or command."""
        total = 0
        names: set[str] = set()
        for record in plan.expected_files:
            relative = Path(record.relative_path)
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise UnsafeRemovableTarget("Media plan contains an unsafe relative path")
            target = LinuxBlockDeviceBackend._boot_relative(relative)
            for part in target.parts:
                if (
                    any(character in _FAT_FORBIDDEN or ord(character) < 0x20 for character in part)
                    or part.endswith((" ", "."))
                ):
                    raise UnsafeRemovableTarget("A media payload name cannot be stored on FAT32")
            key = target.as_posix().casefold()
            if key in names:
                raise UnsafeRemovableTarget("Media payload names collide on case-insensitive FAT32")
            names.add(key)
            if record.size_bytes > FAT32_MAX_FILE_BYTES:
                raise UnsafeRemovableTarget("A media payload file exceeds the FAT32 per-file limit")
            if record.size_bytes > MAX_MEDIA_FILE_BYTES:
                raise UnsafeRemovableTarget("A media payload file exceeds the bounded size limit")
            total += record.size_bytes
        if total > MAX_MEDIA_TOTAL_BYTES:
            raise UnsafeRemovableTarget("Media payload exceeds the bounded total size limit")
        if not plan.expected_files:
            raise UnsafeRemovableTarget("Media plan contains no payload files")
        partition_bytes = partition_sectors * SECTOR_BYTES
        if partition_bytes < MIN_ESP_BYTES:
            raise UnsafeRemovableTarget("Linux USB device is too small for a valid FAT32 EFI partition")
        needed = total + FAT32_OVERHEAD_BYTES + FAT32_PER_FILE_SLACK_BYTES * len(plan.expected_files)
        if needed > partition_bytes:
            raise UnsafeRemovableTarget("Media payload does not fit in the planned EFI partition")

    def preflight(self, plan: WritePlan) -> None:
        """Validate the plan against the target size without touching the device."""
        self._preflight_payload(plan, self._partition_sectors(plan.target.capacity_bytes // SECTOR_BYTES))

    def write(self, plan: WritePlan, source_dir: Path) -> None:
        current = self._current(plan.target, plan.required_bytes)
        locked = self._locked_device(current, "write")
        sectors = locked.identity.size_bytes // SECTOR_BYTES
        partition_sectors = self._partition_sectors(sectors)
        self._preflight_payload(plan, partition_sectors)
        table = self._partition_script(sectors)
        disk = self._pinned_disk(locked)
        locked.destructive_started = True
        self._run_pinned(locked, ["sfdisk", "--no-reread", "--wipe", "always", "--wipe-partitions", "always", str(disk)], table)
        self._release_partition(locked)
        try:
            self._probe.reread_partitions(locked.descriptor)
        except OSError as exc:
            raise UnsafeRemovableTarget("Linux could not re-read the prepared partition table") from exc
        partition = self._attach_partition(locked, partition_sectors)
        self._run_pinned(
            locked, ["mkfs.vfat", "-F", "32", "-S", str(SECTOR_BYTES), "-n", FAT32_VOLUME_LABEL, str(partition)],
            partition_sectors=partition_sectors,
        )
        def populate(mount: Path) -> None:
            self._copy_boot_layout(source_dir, mount, plan)
            self._sync_mount(mount)

        self._mount_and_run(
            locked, current.device_id, partition_sectors, "macloader-usb-", "nosuid,nodev,noexec", populate,
        )

    def _mount_and_run(
        self, locked: _LockedDevice, device_id: str, partition_sectors: int, prefix: str,
        options: str, action: Callable[[Path], object],
    ) -> object:
        partition = self._pinned_partition(locked, partition_sectors)
        mount = Path(tempfile.mkdtemp(prefix=prefix, dir=self._mount_parent))
        primary: Optional[BaseException] = None
        try:
            try:
                self._run(["mount", "-t", "vfat", "-o", options, str(partition), str(mount)])
            finally:
                # Record the mount before any further check so that every
                # failure path unmounts it; never rmdir a mounted directory.
                if self._probe.is_mount(mount):
                    self._mounts[device_id] = (mount, partition)
            self._pinned_disk(locked)
            self._assert_mounted_partition(locked, mount, partition_sectors)
            result = action(mount)
            self._assert_mounted_partition(locked, mount, partition_sectors)
            return result
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                if device_id in self._mounts:
                    self._unmount(device_id)
                else:
                    mount.rmdir()
            except (OSError, UnsafeRemovableTarget):
                # Do not let cleanup replace the real failure; invalidation
                # retries the unmount and still wipes through the descriptor.
                if primary is None:
                    raise

    def _assert_mounted_partition(self, locked: _LockedDevice, mount: Path, partition_sectors: int) -> None:
        self._pinned_partition(locked, partition_sectors)
        assert locked.partition_descriptor is not None
        expected = self._probe.identity_of_fd(locked.partition_descriptor).rdev
        if self._probe.mounted_device(mount) != expected:
            raise UnsafeRemovableTarget("Mounted volume is not the locked disk's prepared EFI partition")

    def flush(self, device: RemovableDevice) -> None:
        locked = self._locked_device(device, "flush")
        os.fsync(locked.descriptor)
        disk = self._pinned_disk(locked)
        self._run_pinned(locked, ["blockdev", "--flushbufs", str(disk)])

    def readback(self, plan: WritePlan, source_dir: Path) -> bool:
        current = self._current(plan.target, plan.required_bytes, allow_created_partition=True)
        locked = self._locked_device(current, "readback")
        self._pinned_disk(locked)
        sectors = locked.identity.size_bytes // SECTOR_BYTES
        partition_sectors = self._partition_sectors(sectors)
        # Discard cached pages so the layout checks observe media.
        self._probe.drop_cache(locked.descriptor)
        try:
            verify_gpt_layout(locked.descriptor, locked.identity.size_bytes)
            verify_fat32_volume(locked.descriptor, ESP_FIRST_LBA, partition_sectors)
        except UnsafeRemovableTarget:
            return False
        if locked.partition_descriptor is None:
            self._attach_partition(locked, partition_sectors)
        assert locked.partition_descriptor is not None
        self._probe.drop_cache(locked.partition_descriptor)
        return bool(self._mount_and_run(
            locked, current.device_id, partition_sectors, "macloader-readback-", "ro,nosuid,nodev,noexec",
            lambda mount: self._verify_boot_layout(source_dir, mount, plan),
        ))

    def invalidate(self, device: RemovableDevice, reason: str) -> None:
        del reason  # Raw private identifiers and user text are never persisted.
        locked = self._locked.get(device.device_id)
        if locked is None:
            # Failure before lock acquisition has not touched the device.
            return
        try:
            if device.device_id in self._mounts:
                try:
                    self._unmount(device.device_id)
                except (OSError, UnsafeRemovableTarget):
                    # A busy mount must not prevent invalidation. Detach it
                    # lazily; the raw wipe below still goes to the locked disk.
                    mount, _partition = self._mounts.pop(device.device_id)
                    try:
                        self._run(["umount", "--lazy", "--", str(mount)])
                    except UnsafeRemovableTarget:
                        pass
            if not locked.destructive_started:
                # No destructive command ran; the media is unchanged.
                return
            try:
                held = self._probe.identity_of_fd(locked.descriptor)
            except OSError as exc:
                raise UnsafeRemovableTarget("Failed media disappeared before invalidation") from exc
            if held != locked.identity:
                # The descriptor no longer refers to the locked attachment.
                # Never direct invalidation writes at whatever occupies it now.
                raise UnsafeRemovableTarget("Failed media changed identity; refusing to invalidate another device")
            try:
                self._invalidate_descriptor(locked.descriptor, locked.identity.size_bytes)
            except OSError as exc:
                raise UnsafeRemovableTarget("Failed media could not be invalidated") from exc
            self._eject_while_locked(locked)
        finally:
            self._close_lock(device.device_id)

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
        try:
            if device.device_id in self._mounts:
                self._unmount(device.device_id)
            self._eject_while_locked(locked)
        finally:
            self._close_lock(device.device_id)

    def _eject_while_locked(self, locked: _LockedDevice) -> None:
        """Flush and power off through the private node while the lock pins it."""
        self._release_partition(locked)
        disk = self._pinned_disk(locked)
        self._run_pinned(locked, ["blockdev", "--flushbufs", str(disk)])
        self._pinned_disk(locked)
        self._run(["udisksctl", "power-off", "--no-user-interaction", "-b", str(disk)])

    @staticmethod
    def _partition_sectors(sectors: int) -> int:
        partition_sectors = sectors - ESP_FIRST_LBA - (1 + GPT_ENTRY_SECTORS)
        if partition_sectors <= 0:
            raise UnsafeRemovableTarget("Linux USB device is too small for a GPT EFI layout")
        return partition_sectors

    @classmethod
    def _partition_script(cls, sectors: int) -> str:
        partition_sectors = cls._partition_sectors(sectors)
        return (
            f"label: gpt\nunit: sectors\nfirst-lba: {ESP_FIRST_LBA}\n\n"
            f"start={ESP_FIRST_LBA}, size={partition_sectors}, type=uefi, name={ESP_PARTITION_NAME}\n"
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
            if source_status.st_size > MAX_MEDIA_FILE_BYTES or source_status.st_size > FAT32_MAX_FILE_BYTES:
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
        """Verify every planned file byte-for-byte and reject any unplanned file."""
        expected: set[str] = set()
        for record in plan.expected_files:
            relative = Path(record.relative_path)
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                return False
            source = Path(source_dir) / relative
            target_relative = cls._boot_relative(relative)
            expected.add(target_relative.as_posix())
            target = mount / target_relative
            try:
                if target.is_symlink() or not target.is_file() or target.stat().st_size != record.size_bytes:
                    return False
                if _sha256_file(source) != record.sha256 or _sha256_file(target) != record.sha256:
                    return False
            except OSError:
                return False
        observed: set[str] = set()
        try:
            for entry in mount.rglob("*"):
                if entry.is_symlink():
                    return False
                if entry.is_file():
                    observed.add(entry.relative_to(mount).as_posix())
                elif not entry.is_dir():
                    return False
        except OSError:
            return False
        return bool(plan.expected_files) and observed == expected

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

    @staticmethod
    def _release_partition(locked: _LockedDevice) -> None:
        if locked.partition_descriptor is not None:
            os.close(locked.partition_descriptor)
            locked.partition_descriptor = None
        if locked.partition_node is not None:
            locked.partition_node.unlink(missing_ok=True)
            locked.partition_node = None

    def _close_lock(self, device_id: str) -> None:
        locked = self._locked.pop(device_id, None)
        if locked is None:
            return
        try:
            self._release_partition(locked)
        finally:
            try:
                if _fcntl is not None:
                    _fcntl.flock(locked.descriptor, _fcntl.LOCK_UN)
            finally:
                os.close(locked.descriptor)
                shutil.rmtree(locked.node_dir, ignore_errors=True)


def _read_exact(descriptor: int, offset: int, length: int) -> bytes:
    data = b""
    while len(data) < length:
        chunk = os.pread(descriptor, length - len(data), offset + len(data))
        if not chunk:
            raise UnsafeRemovableTarget("Media readback ended before the expected layout")
        data += chunk
    return data


def _gpt_header(descriptor: int, lba: int) -> dict[str, Any]:
    raw = _read_exact(descriptor, lba * SECTOR_BYTES, SECTOR_BYTES)
    (signature, revision, header_size, header_crc, reserved, my_lba, alternate_lba,
     first_usable, last_usable, disk_guid, entries_lba, entry_count, entry_size,
     entries_crc) = struct.unpack_from("<8sIIIIQQQQ16sQIII", raw)
    if signature != b"EFI PART" or revision != 0x00010000 or header_size != 92 or reserved != 0:
        raise UnsafeRemovableTarget("GPT header signature or revision is invalid")
    zeroed = raw[:16] + b"\x00\x00\x00\x00" + raw[20:header_size]
    if zlib.crc32(zeroed) & 0xFFFFFFFF != header_crc:
        raise UnsafeRemovableTarget("GPT header checksum is invalid")
    if any(raw[header_size:]):
        raise UnsafeRemovableTarget("GPT header reserved area is not zero")
    return {
        "my_lba": my_lba, "alternate_lba": alternate_lba, "first_usable": first_usable,
        "last_usable": last_usable, "disk_guid": disk_guid, "entries_lba": entries_lba,
        "entry_count": entry_count, "entry_size": entry_size, "entries_crc": entries_crc,
    }


def verify_gpt_layout(descriptor: int, size_bytes: int) -> PartitionGeometry:
    """Verify protective MBR, both GPT copies and the single planned ESP entry."""
    if size_bytes % SECTOR_BYTES:
        raise UnsafeRemovableTarget("Media size is not a whole number of 512-byte sectors")
    sectors = size_bytes // SECTOR_BYTES
    last_lba = sectors - 1
    partition_sectors = LinuxBlockDeviceBackend._partition_sectors(sectors)
    mbr = _read_exact(descriptor, 0, SECTOR_BYTES)
    if mbr[510:512] != b"\x55\xaa" or mbr[446 + 4] != 0xEE:
        raise UnsafeRemovableTarget("Protective MBR is missing")
    primary = _gpt_header(descriptor, 1)
    backup = _gpt_header(descriptor, last_lba)
    expected_last_usable = last_lba - 1 - GPT_ENTRY_SECTORS
    for label, header, my_lba, alternate, entries_lba in (
        ("primary", primary, 1, last_lba, 2),
        ("backup", backup, last_lba, 1, last_lba - GPT_ENTRY_SECTORS),
    ):
        if (
            header["my_lba"] != my_lba
            or header["alternate_lba"] != alternate
            or header["entries_lba"] != entries_lba
            or header["entry_count"] != GPT_ENTRY_COUNT
            or header["entry_size"] != GPT_ENTRY_BYTES
            or header["first_usable"] != ESP_FIRST_LBA
            or header["last_usable"] != expected_last_usable
        ):
            raise UnsafeRemovableTarget(f"{label.capitalize()} GPT header does not match the planned geometry")
    if primary["disk_guid"] != backup["disk_guid"] or primary["entries_crc"] != backup["entries_crc"]:
        raise UnsafeRemovableTarget("Primary and backup GPT headers disagree")
    entry_bytes = GPT_ENTRY_COUNT * GPT_ENTRY_BYTES
    primary_entries = _read_exact(descriptor, 2 * SECTOR_BYTES, entry_bytes)
    backup_entries = _read_exact(descriptor, (last_lba - GPT_ENTRY_SECTORS) * SECTOR_BYTES, entry_bytes)
    if zlib.crc32(primary_entries) & 0xFFFFFFFF != primary["entries_crc"]:
        raise UnsafeRemovableTarget("Primary GPT partition entries checksum is invalid")
    if zlib.crc32(backup_entries) & 0xFFFFFFFF != backup["entries_crc"] or backup_entries != primary_entries:
        raise UnsafeRemovableTarget("Backup GPT partition entries are invalid or differ from the primary")
    used = [
        primary_entries[index:index + GPT_ENTRY_BYTES]
        for index in range(0, entry_bytes, GPT_ENTRY_BYTES)
        if any(primary_entries[index:index + 16])
    ]
    if len(used) != 1:
        raise UnsafeRemovableTarget("GPT must contain exactly the planned EFI System Partition")
    type_guid, _unique_guid, first_lba, end_lba, _attributes, raw_name = struct.unpack_from("<16s16sQQQ72s", used[0])
    name = raw_name.decode("utf-16-le", errors="replace").rstrip("\x00")
    if uuid.UUID(bytes_le=type_guid) != ESP_TYPE_GUID:
        raise UnsafeRemovableTarget("GPT partition is not an EFI System Partition")
    if first_lba != ESP_FIRST_LBA or end_lba - first_lba + 1 != partition_sectors or name != ESP_PARTITION_NAME:
        raise UnsafeRemovableTarget("EFI System Partition geometry does not match the plan")
    return PartitionGeometry(0, 1, first_lba, partition_sectors)


def verify_fat32_volume(descriptor: int, start_sector: int, size_sectors: int) -> None:
    """Verify the FAT32 boot sector, its backup and FSInfo for the planned ESP."""
    base = start_sector * SECTOR_BYTES
    boot = _read_exact(descriptor, base, SECTOR_BYTES)
    (bytes_per_sector, sectors_per_cluster, reserved, fat_count, root_entries, total16,
     _media, fat_size16, sectors_per_track, _heads, _hidden, total32, fat_size32, _flags, version,
     root_cluster, fsinfo_sector, backup_sector) = struct.unpack_from("<HBHBHHBHHHIIIHHIHH", boot, 11)
    if boot[510:512] != b"\x55\xaa" or boot[0] not in (0xEB, 0xE9):
        raise UnsafeRemovableTarget("FAT32 boot sector signature is invalid")
    if (
        bytes_per_sector != SECTOR_BYTES
        or sectors_per_cluster == 0
        or sectors_per_cluster & (sectors_per_cluster - 1)
        or reserved == 0
        or fat_count != 2
        or root_entries != 0
        or total16 != 0
        or fat_size16 != 0
        or fat_size32 == 0
        or version != 0
        or root_cluster < 2
        or not 0 < sectors_per_track <= 255
        # mkfs.fat rounds the filesystem down to a whole number of tracks.
        or not size_sectors - sectors_per_track < total32 <= size_sectors
    ):
        raise UnsafeRemovableTarget("FAT32 boot parameters do not match the planned EFI partition")
    if boot[0x42] != 0x29 or boot[0x52:0x5A] != b"FAT32   " or boot[0x47:0x52] != FAT32_VOLUME_LABEL.ljust(11).encode("ascii"):
        raise UnsafeRemovableTarget("FAT32 volume label or type is not the planned MacLoader volume")
    data_sectors = total32 - reserved - fat_count * fat_size32
    if data_sectors <= 0 or data_sectors // sectors_per_cluster < FAT32_MIN_CLUSTERS:
        raise UnsafeRemovableTarget("FAT32 volume has too few clusters to be a valid FAT32 filesystem")
    if fsinfo_sector == 0 or fsinfo_sector >= reserved or backup_sector in (0, 0xFFFF) or backup_sector >= reserved:
        raise UnsafeRemovableTarget("FAT32 FSInfo or backup boot sector location is invalid")
    if _read_exact(descriptor, base + backup_sector * SECTOR_BYTES, SECTOR_BYTES) != boot:
        raise UnsafeRemovableTarget("FAT32 backup boot sector differs from the primary")
    fsinfo = _read_exact(descriptor, base + fsinfo_sector * SECTOR_BYTES, SECTOR_BYTES)
    if (
        fsinfo[0:4] != b"RRaA"
        or fsinfo[484:488] != b"rrAa"
        or fsinfo[508:512] != b"\x00\x00\x55\xaa"
    ):
        raise UnsafeRemovableTarget("FAT32 FSInfo sector is invalid")
    fat_offset = base + reserved * SECTOR_BYTES
    first_fat = _read_exact(descriptor, fat_offset, 12)
    second_fat = _read_exact(descriptor, fat_offset + fat_size32 * SECTOR_BYTES, 12)
    media_entry = struct.unpack_from("<I", first_fat)[0] & 0x0FFFFFFF
    if first_fat != second_fat or media_entry & 0xFF != boot[21] or media_entry >> 8 != 0x0FFFFF:
        raise UnsafeRemovableTarget("FAT32 allocation tables are inconsistent")


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
        # Reject unrepresentable payloads before the device is even locked.
        self._backend.preflight(plan)
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
