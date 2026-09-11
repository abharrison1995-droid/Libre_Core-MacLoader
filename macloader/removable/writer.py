"""Safety-first removable media operations.

The platform adapters are intentionally injected. The shared contract refuses
ambiguous, non-removable, system, mounted, or stale devices before an adapter can
perform any destructive call. Re-enumeration protects against hot-swap races.
"""

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
from contextlib import contextmanager
from typing import Callable, Iterator, List, Optional

from macloader.exceptions import MacLoaderError


class UnsafeRemovableTarget(MacLoaderError):
    """The selected device cannot be safely written."""


@dataclass(frozen=True)
class RemovableDevice:
    device_id: str
    model: str
    capacity_bytes: int
    is_system_disk: bool
    is_removable: bool
    mounted: bool
    serial: Optional[str] = None
    vendor: Optional[str] = None
    read_only: bool = False


@dataclass(frozen=True)
class WritePlan:
    target: RemovableDevice
    required_bytes: int
    partitions: List[str] = field(default_factory=lambda: ["GPT", "EFI", "Recovery"])
    destroys_data: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "device": {
                "device_id": self.target.device_id,
                "model": self.target.model,
                "capacity_bytes": self.target.capacity_bytes,
                "serial": "<redacted>" if self.target.serial else None,
                "is_removable": self.target.is_removable,
                "is_system_disk": self.target.is_system_disk,
                "mounted": self.target.mounted,
                "read_only": self.target.read_only,
            },
            "required_bytes": self.required_bytes,
            "partitions": list(self.partitions),
            "destroys_data": self.destroys_data,
            "destructive_write_enabled": False,
        }


class DisposableImageAdapter:
    """A safe, non-destructive disposable image write adapter for testing and validation.

    Writes EFI and recovery partitions/payloads into a file image or folder target,
    supporting simulated write interruption and simulated readback corruption.
    """

    def __init__(
        self,
        target_path: Path,
        simulate_interruption: bool = False,
        corrupt_readback: bool = False,
    ):
        self.target_path = Path(target_path)
        self.simulate_interruption = simulate_interruption
        self.corrupt_readback = corrupt_readback
        self.writes_performed: int = 0

    def create_mock_device(
        self,
        device_id: str = "DISPOSABLE_USB",
        capacity_bytes: int = 16 * 1024 * 1024 * 1024,
        model: str = "Disposable Image Target",
        serial: Optional[str] = "DISPOSABLE001",
        is_removable: bool = True,
        is_system_disk: bool = False,
        mounted: bool = False,
        read_only: bool = False,
    ) -> RemovableDevice:
        """Create a RemovableDevice representing this disposable image target."""
        return RemovableDevice(
            device_id=device_id,
            model=model,
            capacity_bytes=capacity_bytes,
            is_system_disk=is_system_disk,
            is_removable=is_removable,
            mounted=mounted,
            serial=serial,
            read_only=read_only,
        )

    def write(self, plan: WritePlan, source_dir: Path) -> None:
        if self.simulate_interruption:
            raise OSError("Simulated device write interruption (device disconnected during write)")

        self.target_path.mkdir(parents=True, exist_ok=True)
        # Write partition descriptor / manifest
        partition_info = "\n".join(plan.partitions)
        (self.target_path / "PARTITIONS.txt").write_text(partition_info, encoding="utf-8")

        # Copy EFI payload from source_dir into the image directory
        dest_efi = self.target_path / "EFI"
        if dest_efi.exists():
            shutil.rmtree(dest_efi)

        if (source_dir / "EFI").is_dir():
            shutil.copytree(source_dir / "EFI", dest_efi)
        elif source_dir.is_dir():
            shutil.copytree(source_dir, dest_efi, dirs_exist_ok=True)

        self.writes_performed += 1

    # Alias write_payload for write
    write_payload = write

    def verify_readback(self, plan: WritePlan, source_dir: Path) -> bool:
        if self.corrupt_readback:
            return False

        if not self.target_path.is_dir():
            return False

        source_efi = source_dir / "EFI" if (source_dir / "EFI").is_dir() else source_dir
        dest_efi = self.target_path / "EFI"

        if not dest_efi.is_dir():
            return False

        expected_partition_info = "\n".join(plan.partitions)
        partition_info = self.target_path / "PARTITIONS.txt"
        if not partition_info.is_file() or partition_info.read_text(encoding="utf-8") != expected_partition_info:
            return False

        source_files = {path.relative_to(source_efi) for path in source_efi.rglob("*") if path.is_file()}
        destination_files = {path.relative_to(dest_efi) for path in dest_efi.rglob("*") if path.is_file()}
        if not source_files or source_files != destination_files:
            return False

        for rel in source_files:
            src_file = source_efi / rel
            dst_file = dest_efi / rel
            if dst_file.stat().st_size != src_file.stat().st_size:
                return False
            if hashlib.sha256(dst_file.read_bytes()).digest() != hashlib.sha256(src_file.read_bytes()).digest():
                return False
        return True


class RemovableMediaWriter:
    """Guarded writer that enforces device safety, re-enumeration, confirmation,
    source validation, and post-write readback verification.
    """

    def __init__(
        self,
        destructive_write: Optional[Callable[[WritePlan, Path], None]] = None,
        enumerator: Optional[Callable[[], List[RemovableDevice]]] = None,
        source_validator: Optional[Callable[[Path], bool]] = None,
        readback_verifier: Optional[Callable[[WritePlan, Path], bool]] = None,
    ):
        self.destructive_write = destructive_write
        self.enumerator = enumerator
        self.source_validator = source_validator
        self.readback_verifier = readback_verifier

    def dry_run(
        self,
        device: RemovableDevice,
        required_bytes: int,
        source_dir: Optional[Path] = None,
    ) -> WritePlan:
        """Create a validated write plan without executing any destructive operation."""
        self._assert_safe(device, required_bytes)
        if source_dir is not None:
            self._validate_source(source_dir)
        return WritePlan(device, required_bytes)

    def write(self, plan: WritePlan, source_dir: Path, confirmation: str) -> None:
        """Perform guarded destructive write with re-enumeration and readback checks."""
        # 1. Source validation
        self._validate_source(source_dir)

        # 2. Exact confirmation validation
        expected = f"WRITE {plan.target.device_id} {plan.target.capacity_bytes}"
        if confirmation != expected:
            raise UnsafeRemovableTarget("Confirmation does not match the exact device identity and capacity")

        # 3. Re-enumeration to prevent hot-swap / stale target races
        fresh_device = self._recheck_device(plan.target, plan.required_bytes)
        fresh_plan = WritePlan(
            target=fresh_device,
            required_bytes=plan.required_bytes,
            partitions=plan.partitions,
            destroys_data=plan.destroys_data,
        )
        if not fresh_device.serial or not fresh_device.serial.strip():
            raise UnsafeRemovableTarget(
                "A stable device serial or equivalent identity is required before a destructive write"
            )

        # 4. Check write adapter
        if self.destructive_write is None:
            raise UnsafeRemovableTarget("No platform write adapter is configured")
        if self.readback_verifier is None:
            raise UnsafeRemovableTarget("No readback verifier is configured")

        # 5. Execute destructive write
        try:
            # Validate once, then hand the adapter an immutable no-follow
            # snapshot. This closes the validation-to-copy symlink race.
            with self._immutable_source_snapshot(source_dir) as snapshot:
                self.destructive_write(fresh_plan, snapshot)
                verified = self.readback_verifier(fresh_plan, snapshot)
        except (Exception, KeyboardInterrupt) as exc:
            raise UnsafeRemovableTarget(f"Write operation failed or was interrupted; readback may also have failed: {exc}") from exc
        if not verified:
            raise UnsafeRemovableTarget("Readback verification failed after write")

    @contextmanager
    def _immutable_source_snapshot(self, source_dir: Path) -> Iterator[Path]:
        snapshot = Path(tempfile.mkdtemp(prefix="macloader-media-source-"))
        try:
            self._copy_no_follow(Path(source_dir), snapshot)
            yield snapshot
        finally:
            shutil.rmtree(snapshot, ignore_errors=True)

    @classmethod
    def _copy_no_follow(cls, source: Path, destination: Path) -> None:
        source_stat = os.lstat(source)
        if stat.S_ISLNK(source_stat.st_mode):
            raise UnsafeRemovableTarget(f"Source tree changed to a symlink: {source.name}")
        if stat.S_ISDIR(source_stat.st_mode):
            destination.mkdir(parents=True, exist_ok=True)
            directory_flags = os.O_RDONLY
            if hasattr(os, "O_DIRECTORY"):
                directory_flags |= os.O_DIRECTORY
            if hasattr(os, "O_NOFOLLOW"):
                directory_flags |= os.O_NOFOLLOW
            directory_fd = os.open(source, directory_flags)
            try:
                scan_path = Path(f"/proc/self/fd/{directory_fd}") if Path("/proc/self/fd").is_dir() else source
                for entry in os.scandir(scan_path):
                    cls._copy_no_follow(Path(entry.path), destination / entry.name)
            finally:
                os.close(directory_fd)
            return
        if not stat.S_ISREG(source_stat.st_mode):
            raise UnsafeRemovableTarget(f"Source tree contains a non-regular file: {source.name}")
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        source_fd = os.open(source, file_flags)
        try:
            opened_stat = os.fstat(source_fd)
            if not stat.S_ISREG(opened_stat.st_mode):
                raise UnsafeRemovableTarget(f"Source tree entry is not a regular file: {source.name}")
            with os.fdopen(source_fd, "rb", closefd=False) as source_handle, destination.open("wb") as destination_handle:
                while chunk := source_handle.read(1024 * 1024):
                    destination_handle.write(chunk)
        finally:
            os.close(source_fd)

    def _recheck_device(self, expected_target: RemovableDevice, required_bytes: int) -> RemovableDevice:
        """Re-enumerate devices to verify the target is still present, unaltered, and safe."""
        if self.enumerator is None:
            self._assert_safe(expected_target, required_bytes)
            return expected_target

        try:
            current_devices = self.enumerator()
        except Exception as exc:
            raise UnsafeRemovableTarget(f"Device re-enumeration failed: {exc}") from exc

        matches = [d for d in current_devices if d.device_id == expected_target.device_id]
        if not matches:
            raise UnsafeRemovableTarget(
                f"Target device '{expected_target.device_id}' is no longer present (stale device / removed)"
            )
        if len(matches) > 1:
            raise UnsafeRemovableTarget(
                f"Ambiguous target devices found for '{expected_target.device_id}'"
            )

        current = matches[0]
        # Detect capacity or identity modification (hot-swap race)
        if current.capacity_bytes != expected_target.capacity_bytes:
            raise UnsafeRemovableTarget(
                f"Target device capacity changed from {expected_target.capacity_bytes} to {current.capacity_bytes} (possible hot-swap)"
            )
        if current.model != expected_target.model or current.serial != expected_target.serial:
            raise UnsafeRemovableTarget(
                "Target device identity changed since planning (possible hot-swap)"
            )

        self._assert_safe(current, required_bytes)
        return current

    def _validate_source(self, source_dir: Path) -> None:
        """Validate source directory structure, safety, and EFI/Recovery integrity."""
        source_dir = Path(source_dir)
        if not source_dir.exists() or not source_dir.is_dir():
            raise UnsafeRemovableTarget(f"Source directory does not exist or is not a directory: {source_dir}")
        if source_dir.is_symlink():
            raise UnsafeRemovableTarget(f"Source directory must not be a symlink: {source_dir}")

        # Ensure no ancestor directory is a symlink
        current = source_dir.absolute()
        for p in (current, *current.parents):
            if p.is_symlink():
                raise UnsafeRemovableTarget(f"Source directory ancestor is a symlink: {p}")

        # Check for structural EFI presence if source claims to be an EFI tree
        efi_candidate = source_dir / "EFI"
        if efi_candidate.is_symlink():
            raise UnsafeRemovableTarget(f"EFI directory must not be a symlink: {efi_candidate}")
        efi_dir = efi_candidate if efi_candidate.is_dir() else source_dir
        for entry in efi_dir.rglob("*"):
            if entry.is_symlink():
                raise UnsafeRemovableTarget(f"EFI source contains a symlink: {entry.name}")
        boot_efi = efi_dir / "BOOT" / "BOOTx64.efi"
        oc_efi = efi_dir / "OC" / "OpenCore.efi"

        if not boot_efi.is_file() or boot_efi.is_symlink():
            raise UnsafeRemovableTarget(f"Source EFI is missing required bootloader: {boot_efi}")
        if not oc_efi.is_file() or oc_efi.is_symlink():
            raise UnsafeRemovableTarget(f"Source EFI is missing OpenCore binary: {oc_efi}")

        # External validator callback (e.g. ocvalidate or recovery validator)
        if self.source_validator is not None:
            try:
                valid = self.source_validator(source_dir)
            except Exception as exc:
                raise UnsafeRemovableTarget(f"Source validation raised an error: {exc}") from exc
            if not valid:
                raise UnsafeRemovableTarget("Source directory failed EFI/Recovery validation")

    @staticmethod
    def _assert_safe(device: RemovableDevice, required_bytes: int) -> None:
        if not device.device_id or not device.device_id.strip():
            raise UnsafeRemovableTarget("Target device ID is missing or empty")
        if not device.is_removable:
            raise UnsafeRemovableTarget("Target is not removable")
        if device.is_system_disk:
            raise UnsafeRemovableTarget("Refusing to modify the system disk")
        if device.mounted:
            raise UnsafeRemovableTarget("Target is mounted or busy")
        if device.read_only:
            raise UnsafeRemovableTarget("Target device is read-only")
        if device.capacity_bytes <= 0:
            raise UnsafeRemovableTarget("Target capacity is invalid or zero")
        if device.capacity_bytes < required_bytes:
            raise UnsafeRemovableTarget("Target capacity is insufficient")
