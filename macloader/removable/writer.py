"""Safety-first removable media operations.

The platform adapters are intentionally injected. The shared contract refuses
ambiguous, non-removable, system, mounted, or stale devices before an adapter can
perform any destructive call. Re-enumeration protects against hot-swap races.
"""

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time
from contextlib import contextmanager
from typing import Callable, Iterator, List, Optional, Sequence

from macloader.exceptions import MacLoaderError
from macloader.domain.contracts import BuildManifest, canonical_json_digest
from macloader.domain.recovery import RecoveryEvidence, RecoveryLock
from macloader.recovery.acquirer import verify_apple_chunklist
from macloader.recovery.service import load_recovery_policy


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
    partitions: tuple[str, ...] = ()
    whole_device: bool = True
    system_disk_ref: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "partitions", tuple(str(item) for item in self.partitions))

    @property
    def public_device_ref(self) -> str:
        return "device-" + hashlib.sha256(self.device_id.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class MediaFileDigest:
    """Bounded readback expectation for one relative media file."""

    relative_path: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.relative_path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class MediaBindings:
    """Inputs whose change invalidates a destructive media plan."""

    efi_manifest_digest: str = ""
    recovery_lock_digest: str = ""
    validation_digest: str = ""
    configuration_digest: str = ""
    toolchain_digest: str = ""
    evidence_digest: str = ""

    @classmethod
    def from_published_contracts(
        cls,
        manifest: BuildManifest,
        lock: RecoveryLock,
        evidence: RecoveryEvidence,
    ) -> "MediaBindings":
        """Derive the complete media binding from the published contracts.

        The six fields are not independent caller assertions.  They are a
        compact reference to one manifest, one Recovery lock, and its verified
        evidence.  Keeping this derivation next to the value object gives the
        planner and the final writer the same canonical contract.
        """
        manifest_digest = canonical_json_digest(manifest.to_dict())
        if evidence.lock_digest.lower() != lock.digest.lower():
            raise ValueError("Recovery evidence is bound to a different lock identity")
        return cls(
            efi_manifest_digest=manifest_digest,
            recovery_lock_digest=lock.digest,
            validation_digest=canonical_json_digest({
                "manifest_digest": manifest_digest,
                "validation_report": manifest.validation_report,
            }),
            configuration_digest=lock.binding.configuration_digest,
            toolchain_digest=manifest.toolchain_digest,
            evidence_digest=canonical_json_digest(evidence.to_dict()),
        )

    @property
    def complete(self) -> bool:
        return all(
            _is_sha256(value)
            for value in (
                self.efi_manifest_digest,
                self.recovery_lock_digest,
                self.validation_digest,
                self.configuration_digest,
                self.toolchain_digest,
                self.evidence_digest,
            )
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "efi_manifest_digest": self.efi_manifest_digest,
            "recovery_lock_digest": self.recovery_lock_digest,
            "validation_digest": self.validation_digest,
            "configuration_digest": self.configuration_digest,
            "toolchain_digest": self.toolchain_digest,
            "evidence_digest": self.evidence_digest,
        }


@dataclass(frozen=True)
class WritePlan:
    target: RemovableDevice
    required_bytes: int
    partitions: Sequence[str] = field(default_factory=lambda: ("GPT", "EFI", "Recovery"))
    destroys_data: bool = True
    expected_files: Sequence[MediaFileDigest] = field(default_factory=tuple)
    bindings: MediaBindings = field(default_factory=MediaBindings)
    source_validated: bool = False

    def __post_init__(self) -> None:
        if self.required_bytes < 0:
            raise ValueError("required_bytes must not be negative")
        object.__setattr__(self, "partitions", tuple(str(item) for item in self.partitions))
        object.__setattr__(self, "expected_files", tuple(self.expected_files))

    @property
    def public_device_ref(self) -> str:
        """Opaque descriptor suitable for a screen or redacted diagnostic."""
        return self.target.public_device_ref

    @property
    def plan_digest(self) -> str:
        payload = {
            "target": {
                "device_id": self.public_device_ref,
                "model": self.target.model,
                "capacity_bytes": self.target.capacity_bytes,
                "serial": self.target.serial,
                "vendor": self.target.vendor,
                "is_system_disk": self.target.is_system_disk,
                "is_removable": self.target.is_removable,
                "mounted": self.target.mounted,
                "read_only": self.target.read_only,
                "partitions": list(self.target.partitions),
                "whole_device": self.target.whole_device,
                "system_disk_ref": self.target.system_disk_ref,
            },
            "required_bytes": self.required_bytes,
            "partitions": list(self.partitions),
            "destroys_data": self.destroys_data,
            "expected_files": [item.to_dict() for item in self.expected_files],
            "bindings": self.bindings.to_dict(),
            "source_validated": self.source_validated,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "device": {
                "device_id": self.public_device_ref,
                "model": self.target.model,
                "capacity_bytes": self.target.capacity_bytes,
                "serial": "<redacted>" if self.target.serial else None,
                "is_removable": self.target.is_removable,
                "is_system_disk": self.target.is_system_disk,
                "mounted": self.target.mounted,
                "read_only": self.target.read_only,
                "existing_partitions": list(self.target.partitions),
                "whole_device": self.target.whole_device,
            },
            "required_bytes": self.required_bytes,
            "partitions": list(self.partitions),
            "destroys_data": self.destroys_data,
            "expected_files": [item.to_dict() for item in self.expected_files],
            "bindings": self.bindings.to_dict(),
            "plan_digest": self.plan_digest,
            "confirmation_phrase": f"ERASE {self.public_device_ref} {self.target.capacity_bytes}",
            "destructive_write_enabled": self.bindings.complete,
        }


@dataclass(frozen=True)
class DestructiveConfirmation:
    """Short-lived typed confirmation bound to one immutable media plan."""

    plan_digest: str
    device_id: str
    capacity_bytes: int
    typed_phrase: str
    issued_at: float
    expires_at: float

    @classmethod
    def issue(cls, plan: WritePlan, now: Optional[float] = None, ttl_seconds: float = 120.0) -> "DestructiveConfirmation":
        if ttl_seconds <= 0 or ttl_seconds > 900:
            raise ValueError("confirmation lifetime must be between 0 and 900 seconds")
        issued = time.monotonic() if now is None else float(now)
        return cls(
            plan_digest=plan.plan_digest,
            device_id=plan.target.device_id,
            capacity_bytes=plan.target.capacity_bytes,
            typed_phrase=f"ERASE {plan.public_device_ref} {plan.target.capacity_bytes}",
            issued_at=issued,
            expires_at=issued + ttl_seconds,
        )

    def valid_for(self, plan: WritePlan, now: Optional[float] = None) -> bool:
        current = time.monotonic() if now is None else float(now)
        return (
            self.plan_digest == plan.plan_digest
            and self.device_id == plan.target.device_id
            and self.capacity_bytes == plan.target.capacity_bytes
            and self.typed_phrase == f"ERASE {plan.public_device_ref} {plan.target.capacity_bytes}"
            and self.issued_at <= current < self.expires_at
        )


MAX_MEDIA_FILES = 10_000
MAX_MEDIA_FILE_BYTES = 8 * 1024 * 1024 * 1024
MAX_MEDIA_TOTAL_BYTES = 32 * 1024 * 1024 * 1024
# Every media plan lays out a GPT EFI System Partition formatted as FAT32.
# FAT32 directory entries store a 32-bit file size, so a larger payload file
# cannot be represented and must be rejected while planning, before any
# repartitioning could start.
FAT32_MAX_FILE_BYTES = 0xFFFFFFFF
COPY_CHUNK_BYTES = 1024 * 1024
_WINDOWS_PLATFORM = os.name == "nt"


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower())


def _payload_root(source_dir: Path) -> Path:
    source_dir = Path(source_dir)
    if (source_dir / "EFI").is_dir() and (source_dir / "Recovery").is_dir():
        return source_dir
    return source_dir / "EFI" if (source_dir / "EFI").is_dir() else source_dir


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_tree_bounded(source: Path, destination: Path) -> None:
    source = Path(source)
    destination = Path(destination)
    source_stat = os.lstat(source)
    if stat.S_ISLNK(source_stat.st_mode) or not stat.S_ISDIR(source_stat.st_mode):
        raise UnsafeRemovableTarget("media source contains a non-directory or symlink boundary")
    copied_files = 0
    copied_bytes = 0
    destination.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source.rglob("*")):
        relative = entry.relative_to(source)
        target = destination / relative
        entry_stat = os.lstat(entry)
        if stat.S_ISLNK(entry_stat.st_mode):
            raise UnsafeRemovableTarget(f"media source contains a symlink: {relative}")
        if stat.S_ISDIR(entry_stat.st_mode):
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not stat.S_ISREG(entry_stat.st_mode):
            raise UnsafeRemovableTarget(f"media source contains a non-regular file: {relative}")
        copied_files += 1
        copied_bytes += entry_stat.st_size
        if copied_files > MAX_MEDIA_FILES or copied_bytes > MAX_MEDIA_TOTAL_BYTES:
            raise UnsafeRemovableTarget("media source exceeds the bounded file or byte limit")
        if entry_stat.st_size > MAX_MEDIA_FILE_BYTES:
            raise UnsafeRemovableTarget(f"media source file exceeds the bounded size limit: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with entry.open("rb") as source_handle, target.open("wb") as target_handle:
            while chunk := source_handle.read(COPY_CHUNK_BYTES):
                target_handle.write(chunk)
            target_handle.flush()
            os.fsync(target_handle.fileno())


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
        self.target_path.mkdir(parents=True, exist_ok=True)
        incomplete = self.target_path / "WRITE-INCOMPLETE"
        incomplete.write_text("write started; this image is not bootable", encoding="utf-8")
        if self.simulate_interruption:
            raise OSError("Simulated device write interruption (device disconnected during write)")

        # Write partition descriptor / manifest only alongside an incomplete
        # marker.  A cancelled or interrupted image can never look ready.
        partition_info = "\n".join(plan.partitions)
        partition_tmp = self.target_path / "PARTITIONS.txt.part"
        partition_tmp.write_text(partition_info, encoding="utf-8")

        # Copy EFI and Recovery payloads from the immutable source snapshot into
        # a temporary tree, using bounded chunks and refusing symlinks.
        dest_efi = self.target_path / "EFI"
        payload_root = _payload_root(source_dir)
        staged_payload = self.target_path / "payload.part"
        if staged_payload.exists():
            shutil.rmtree(staged_payload)
        _copy_tree_bounded(payload_root, staged_payload)
        if payload_root == Path(source_dir):
            for name in ("EFI", "Recovery"):
                destination = self.target_path / name
                if destination.exists():
                    shutil.rmtree(destination)
                os.replace(staged_payload / name, destination)
            staged_payload.rmdir()
        else:
            if dest_efi.exists():
                shutil.rmtree(dest_efi)
            os.replace(staged_payload, dest_efi)
        os.replace(partition_tmp, self.target_path / "PARTITIONS.txt")
        incomplete.unlink(missing_ok=True)

        self.writes_performed += 1

    # Alias write_payload for write
    write_payload = write

    def invalidate(self, plan: WritePlan, reason: str) -> None:
        """Leave a durable marker that makes the disposable target unready."""
        self.target_path.mkdir(parents=True, exist_ok=True)
        (self.target_path / "WRITE-INCOMPLETE").write_text(reason[:1024], encoding="utf-8")

    def verify_readback(self, plan: WritePlan, source_dir: Path) -> bool:
        if self.corrupt_readback:
            return False

        if not self.target_path.is_dir():
            return False
        if (self.target_path / "WRITE-INCOMPLETE").exists() or (self.target_path / "payload.part").exists():
            return False

        source_payload = _payload_root(source_dir)
        destination_payload = self.target_path if source_payload == Path(source_dir) else self.target_path / "EFI"

        if not destination_payload.is_dir():
            return False

        expected_partition_info = "\n".join(plan.partitions)
        partition_info = self.target_path / "PARTITIONS.txt"
        if not partition_info.is_file() or partition_info.read_text(encoding="utf-8") != expected_partition_info:
            return False

        source_files = {path.relative_to(source_payload) for path in source_payload.rglob("*") if path.is_file() and not path.is_symlink()}
        destination_files = {
            path.relative_to(destination_payload)
            for path in destination_payload.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.relative_to(destination_payload).as_posix() not in {"PARTITIONS.txt", "WRITE-INCOMPLETE"}
        }
        if not source_files or source_files != destination_files:
            return False

        for rel in source_files:
            src_file = source_payload / rel
            dst_file = destination_payload / rel
            if dst_file.stat().st_size != src_file.stat().st_size:
                return False
            if _sha256_file(dst_file) != _sha256_file(src_file):
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
        invalidator: Optional[Callable[[WritePlan, str], None]] = None,
        require_published_artifacts: bool = True,
    ):
        self.destructive_write = destructive_write
        self.enumerator = enumerator
        self.source_validator = source_validator
        self.readback_verifier = readback_verifier
        self.invalidator = invalidator
        self.require_published_artifacts = require_published_artifacts

    def dry_run(
        self,
        device: RemovableDevice,
        required_bytes: int,
        source_dir: Optional[Path] = None,
        bindings: Optional[MediaBindings] = None,
    ) -> WritePlan:
        """Create a validated write plan without executing any destructive operation."""
        self._assert_safe(device, required_bytes)
        expected_files: tuple[MediaFileDigest, ...] = ()
        if source_dir is not None:
            self._validate_source(source_dir)
            expected_files = self._source_manifest(source_dir)
            if bindings is not None and bindings.complete and not (Path(source_dir) / "Recovery").is_dir():
                raise UnsafeRemovableTarget("A qualified media plan requires an explicit Recovery payload")
        return WritePlan(
            device,
            required_bytes,
            expected_files=expected_files,
            bindings=bindings or MediaBindings(),
            source_validated=source_dir is not None,
        )

    def write(
        self,
        plan: WritePlan,
        source_dir: Path,
        confirmation: DestructiveConfirmation,
        cancel: Optional[Callable[[], bool]] = None,
        progress: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Perform guarded destructive write with re-enumeration and readback checks."""
        if not plan.bindings.complete:
            raise UnsafeRemovableTarget("Media plan is not bound to EFI, Recovery, validation, configuration, toolchain, and evidence digests")
        if not plan.source_validated:
            raise UnsafeRemovableTarget("Media plan was not created by the guarded source-validation boundary")
        if not isinstance(confirmation, DestructiveConfirmation):
            raise UnsafeRemovableTarget("A typed expiring destructive confirmation is required")
        if not confirmation.valid_for(plan):
            raise UnsafeRemovableTarget("Destructive confirmation is invalid, stale, or expired")
        if cancel and cancel():
            raise UnsafeRemovableTarget("Media write cancelled before source preparation")
        # 1. Source validation
        self._validate_source(source_dir)
        # Planning normally performs this check, but a WritePlan is a public
        # value object and can be constructed directly.  Repeat the actual
        # Recovery boundary immediately before device re-enumeration and never
        # trust the six digest-shaped fields as proof that Recovery exists.
        if plan.bindings.complete:
            recovery_dir = Path(source_dir) / "Recovery"
            if recovery_dir.is_symlink() or not recovery_dir.is_dir():
                raise UnsafeRemovableTarget("A qualified media source must contain the current Recovery payload")
            if not any(path.is_file() and not path.is_symlink() for path in recovery_dir.rglob("*")):
                raise UnsafeRemovableTarget("A qualified media source has no Recovery payload")
            self._validate_published_artifacts(
                Path(source_dir), plan.bindings, require_all=self.require_published_artifacts
            )
        current_manifest = self._source_manifest(source_dir)
        if current_manifest != plan.expected_files:
            raise UnsafeRemovableTarget("Media source changed after planning; the immutable plan is stale")

        # 2. Re-enumeration to prevent hot-swap / stale target races
        fresh_device = self._recheck_device(plan.target, plan.required_bytes)
        fresh_plan = WritePlan(
            target=fresh_device,
            required_bytes=plan.required_bytes,
            partitions=plan.partitions,
            destroys_data=plan.destroys_data,
            expected_files=plan.expected_files,
            bindings=plan.bindings,
            source_validated=plan.source_validated,
        )
        if not fresh_device.serial or not fresh_device.serial.strip():
            raise UnsafeRemovableTarget(
                "A stable device serial or equivalent identity is required before a destructive write"
            )
        if fresh_plan.plan_digest != plan.plan_digest:
            raise UnsafeRemovableTarget("Target changed after confirmation; the immutable media plan is stale")

        # 3. Check write adapter
        if self.destructive_write is None:
            raise UnsafeRemovableTarget("No platform write adapter is configured")
        if self.readback_verifier is None:
            raise UnsafeRemovableTarget("No readback verifier is configured")

        # 4. Execute destructive write
        destructive_started = False
        try:
            # Validate once, then hand the adapter an immutable no-follow
            # snapshot. This closes the validation-to-copy symlink race.
            with self._immutable_source_snapshot(source_dir) as snapshot:
                snapshot_manifest = self._source_manifest(snapshot)
                if snapshot_manifest != plan.expected_files:
                    raise UnsafeRemovableTarget("Source changed while preparing the bounded write snapshot")
                if cancel and cancel():
                    raise UnsafeRemovableTarget("Media write cancelled before destructive I/O")
                if progress:
                    progress(0, sum(item.size_bytes for item in plan.expected_files))
                destructive_started = True
                self.destructive_write(fresh_plan, snapshot)
                if cancel and cancel():
                    raise UnsafeRemovableTarget("Media write cancelled; readback is required before readiness")
                verified = self.readback_verifier(fresh_plan, snapshot)
        except (Exception, KeyboardInterrupt) as exc:
            invalidation_error = self._invalidate(fresh_plan, str(exc)) if destructive_started else None
            suffix = f"; invalidation failed: {invalidation_error}" if invalidation_error else ""
            raise UnsafeRemovableTarget(
                f"Write operation failed or was interrupted; readback may also have failed{suffix}: {exc}"
            ) from exc
        if not verified:
            invalidation_error = self._invalidate(fresh_plan, "readback verification failed")
            suffix = f"; invalidation failed: {invalidation_error}" if invalidation_error else ""
            raise UnsafeRemovableTarget(f"Readback verification failed after write{suffix}")
        if progress:
            total = sum(item.size_bytes for item in plan.expected_files)
            progress(total, total)

    @contextmanager
    def _immutable_source_snapshot(self, source_dir: Path) -> Iterator[Path]:
        snapshot = Path(tempfile.mkdtemp(prefix="macloader-media-source-"))
        try:
            self._copy_no_follow(Path(source_dir), snapshot)
            yield snapshot
        finally:
            shutil.rmtree(snapshot, ignore_errors=True)

    def _invalidate(self, plan: WritePlan, reason: str) -> Optional[Exception]:
        if self.invalidator is None:
            return None
        try:
            self.invalidator(plan, reason)
        except Exception as exc:  # The original write failure remains primary.
            return exc
        return None

    @classmethod
    def _copy_no_follow(cls, source: Path, destination: Path) -> None:
        source_stat = os.lstat(source)
        if stat.S_ISLNK(source_stat.st_mode):
            raise UnsafeRemovableTarget(f"Source tree changed to a symlink: {source.name}")
        if stat.S_ISDIR(source_stat.st_mode):
            destination.mkdir(parents=True, exist_ok=True)
            if _WINDOWS_PLATFORM:
                # Windows does not provide the POSIX /proc/self/fd and
                # O_NOFOLLOW/O_DIRECTORY combination used below.  Enumerate
                # without following reparse points, recheck each entry, and
                # open regular files only after the boundary checks.  The
                # source snapshot remains disposable and is never used as a
                # device identity.
                try:
                    entries = list(os.scandir(source))
                except OSError as exc:
                    raise UnsafeRemovableTarget(f"Unable to read media source directory: {source.name}") from exc
                for entry in entries:
                    entry_path = Path(entry.path)
                    if entry.is_symlink():
                        raise UnsafeRemovableTarget(f"Source tree changed to a symlink: {entry.name}")
                    entry_stat = os.lstat(entry_path)
                    if stat.S_ISLNK(entry_stat.st_mode):
                        raise UnsafeRemovableTarget(f"Source tree changed to a symlink: {entry.name}")
                    cls._copy_no_follow(entry_path, destination / entry.name)
                return
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
                f"Target device '{expected_target.public_device_ref}' is no longer present (stale device / removed)"
            )
        if len(matches) > 1:
            raise UnsafeRemovableTarget(
                f"Ambiguous target devices found for '{expected_target.public_device_ref}'"
            )

        current = matches[0]
        # Detect capacity or identity modification (hot-swap race)
        if current.capacity_bytes != expected_target.capacity_bytes:
            raise UnsafeRemovableTarget(
                f"Target device capacity changed from {expected_target.capacity_bytes} to {current.capacity_bytes} (possible hot-swap)"
            )
        if (
            current.model != expected_target.model
            or current.serial != expected_target.serial
            or current.vendor != expected_target.vendor
            or current.is_removable != expected_target.is_removable
            or current.is_system_disk != expected_target.is_system_disk
            or current.partitions != expected_target.partitions
            or current.whole_device != expected_target.whole_device
        ):
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
    def _validate_published_artifacts(
        source_dir: Path, bindings: MediaBindings, *, require_all: bool = False
    ) -> None:
        """Reconcile the published manifest and Recovery evidence at write time."""
        manifest_path = source_dir / "manifest.json"
        if not manifest_path.exists() and not require_all:
            return
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise UnsafeRemovableTarget("Qualified media source must contain a regular EFI manifest")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise UnsafeRemovableTarget("EFI manifest is unreadable") from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("validation_report") != "VALID"
            or canonical_json_digest(manifest) != bindings.efi_manifest_digest
        ):
            raise UnsafeRemovableTarget("EFI manifest is invalid or does not match the media binding")
        try:
            parsed_manifest = BuildManifest.from_dict(manifest)
        except ValueError as exc:
            raise UnsafeRemovableTarget("EFI manifest is not a valid published contract") from exc
        if parsed_manifest.output_digest != RemovableMediaWriter._efi_payload_digest(source_dir):
            raise UnsafeRemovableTarget("EFI payload contents do not match the published manifest")

        lock_path = source_dir / "Recovery" / "recovery.lock.json"
        evidence_path = source_dir / "Recovery" / "recovery.evidence.json"
        if require_all and (
            lock_path.is_symlink()
            or evidence_path.is_symlink()
            or not lock_path.is_file()
            or not evidence_path.is_file()
        ):
            raise UnsafeRemovableTarget("Recovery lock and evidence must be present as regular files")
        if not require_all and not lock_path.exists() and not evidence_path.exists():
            return
        if lock_path.is_symlink() or evidence_path.is_symlink() or not lock_path.is_file() or not evidence_path.is_file():
            raise UnsafeRemovableTarget("Recovery lock and evidence must be present as regular files")
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise UnsafeRemovableTarget("Recovery lock or evidence is unreadable") from exc
        if not isinstance(lock, dict) or not isinstance(evidence, dict):
            raise UnsafeRemovableTarget("Recovery lock and evidence must be objects")
        try:
            parsed_lock = RecoveryLock.from_dict(lock)
            parsed_evidence = RecoveryEvidence(**evidence)
        except (TypeError, ValueError) as exc:
            raise UnsafeRemovableTarget("Recovery lock or evidence is not a valid published contract") from exc
        if (
            parsed_lock.digest != bindings.recovery_lock_digest
            or parsed_evidence.lock_digest != bindings.recovery_lock_digest
            or parsed_evidence.signed_chunklist is not True
        ):
            raise UnsafeRemovableTarget("Recovery lock/evidence identity or authenticity does not match the media binding")
        try:
            expected_bindings = MediaBindings.from_published_contracts(
                parsed_manifest, parsed_lock, parsed_evidence
            )
        except ValueError as exc:
            raise UnsafeRemovableTarget("Published Recovery evidence is not bound to the current lock") from exc
        if bindings != expected_bindings:
            raise UnsafeRemovableTarget(
                "EFI, Recovery, validation, configuration, toolchain, and evidence bindings disagree"
            )
        try:
            policy = load_recovery_policy()
        except Exception as exc:
            raise UnsafeRemovableTarget("Trusted Recovery policy is unavailable at the media boundary") from exc
        manifest_digest = canonical_json_digest(manifest)
        if (
            parsed_lock.source_policy_digest.lower() != policy.digest.lower()
            or parsed_lock.product.target != policy.target
            or parsed_lock.binding.target_digest.lower() != policy.target.digest.lower()
            or parsed_lock.binding.policy_digest.lower() != policy.digest.lower()
            or parsed_lock.binding.efi_manifest_digest.lower() != manifest_digest.lower()
            or parsed_lock.binding.toolchain_digest.lower() != parsed_manifest.toolchain_digest.lower()
        ):
            raise UnsafeRemovableTarget("Published Recovery lock is not bound to the current policy and EFI manifest")
        if parsed_lock.state.value != "verified":
            raise UnsafeRemovableTarget("Recovery lock is not in the verified state")
        if (
            parsed_evidence.image_digest.lower() != parsed_lock.product.image_sha256.lower()
            or parsed_evidence.chunklist_digest.lower() != parsed_lock.product.chunklist_sha256.lower()
            or parsed_lock.product.image_size_bytes is None
            or parsed_lock.product.chunklist_size_bytes is None
        ):
            raise UnsafeRemovableTarget("Recovery evidence does not match the locked asset identities")
        build = parsed_lock.product.target.build
        image_path = source_dir / "Recovery" / f"Recovery-{build}.dmg"
        chunklist_path = source_dir / "Recovery" / f"Recovery-{build}.chunklist"
        if (
            image_path.is_symlink()
            or chunklist_path.is_symlink()
            or not image_path.is_file()
            or not chunklist_path.is_file()
        ):
            raise UnsafeRemovableTarget("Published Recovery image and chunklist are missing or unsafe")
        if (
            image_path.stat().st_size != parsed_lock.product.image_size_bytes
            or image_path.stat().st_size != parsed_evidence.image_size_bytes
            or chunklist_path.stat().st_size != parsed_lock.product.chunklist_size_bytes
            or _sha256_file(image_path).lower() != parsed_lock.product.image_sha256.lower()
            or _sha256_file(chunklist_path).lower() != parsed_lock.product.chunklist_sha256.lower()
        ):
            raise UnsafeRemovableTarget("Published Recovery payload bytes do not match verified evidence")
        try:
            verified_chunks, verified_image_size = verify_apple_chunklist(
                image_path,
                chunklist_path,
                parsed_lock.product.image_sha256,
                parsed_lock.product.chunklist_sha256,
            )
        except Exception as exc:
            raise UnsafeRemovableTarget("Published Recovery payload failed signed chunklist verification") from exc
        if (
            verified_chunks != parsed_evidence.verified_chunks
            or verified_image_size != parsed_evidence.image_size_bytes
        ):
            raise UnsafeRemovableTarget("Published Recovery evidence does not match signed chunklist verification")
        state_path = source_dir / "Recovery" / "recovery.state.json"
        if require_all:
            if state_path.is_symlink() or not state_path.is_file():
                raise UnsafeRemovableTarget("Authoritative Recovery state is missing")
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise UnsafeRemovableTarget("Authoritative Recovery state is unreadable") from exc
            if (
                not isinstance(state, dict)
                or state.get("schema_version") != "1"
                or state.get("lock") != lock
                or state.get("evidence") != evidence
            ):
                raise UnsafeRemovableTarget("Recovery compatibility files do not match authoritative state")

    @staticmethod
    def _efi_payload_digest(source_dir: Path) -> str:
        """Recompute the EFI build digest, excluding the separately published Recovery payload."""
        records: list[dict[str, object]] = []
        for entry in sorted(Path(source_dir).rglob("*")):
            relative = entry.relative_to(source_dir).as_posix()
            if relative == "manifest.json" or relative == "Recovery" or relative.startswith("Recovery/"):
                continue
            entry_stat = os.lstat(entry)
            if stat.S_ISLNK(entry_stat.st_mode):
                raise UnsafeRemovableTarget(f"media source contains a symlink: {relative}")
            if stat.S_ISDIR(entry_stat.st_mode):
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                raise UnsafeRemovableTarget(f"media source contains a non-regular file: {relative}")
            records.append({"path": relative, "size": entry_stat.st_size, "sha256": _sha256_file(entry)})
        return canonical_json_digest({"files": records})

    @classmethod
    def _source_manifest(cls, source_dir: Path) -> tuple[MediaFileDigest, ...]:
        source_dir = Path(source_dir)
        efi_dir = _payload_root(source_dir)
        files: list[MediaFileDigest] = []
        total_bytes = 0
        for entry in sorted(efi_dir.rglob("*")):
            relative = entry.relative_to(efi_dir).as_posix()
            entry_stat = os.lstat(entry)
            if stat.S_ISLNK(entry_stat.st_mode):
                raise UnsafeRemovableTarget(f"media source contains a symlink: {relative}")
            if stat.S_ISDIR(entry_stat.st_mode):
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                raise UnsafeRemovableTarget(f"media source contains a non-regular file: {relative}")
            if entry_stat.st_size > MAX_MEDIA_FILE_BYTES:
                raise UnsafeRemovableTarget(f"media source file exceeds the bounded size limit: {relative}")
            if entry_stat.st_size > FAT32_MAX_FILE_BYTES:
                raise UnsafeRemovableTarget(
                    f"media source file exceeds the FAT32 per-file limit of {FAT32_MAX_FILE_BYTES} bytes: {relative}"
                )
            total_bytes += entry_stat.st_size
            if len(files) >= MAX_MEDIA_FILES or total_bytes > MAX_MEDIA_TOTAL_BYTES:
                raise UnsafeRemovableTarget("media source exceeds the bounded file or byte limit")
            files.append(MediaFileDigest(relative, entry_stat.st_size, _sha256_file(entry)))
        if not files:
            raise UnsafeRemovableTarget("media source contains no regular payload files")
        return tuple(files)

    @staticmethod
    def _assert_safe(device: RemovableDevice, required_bytes: int) -> None:
        if not device.device_id or not device.device_id.strip():
            raise UnsafeRemovableTarget("Target device ID is missing or empty")
        if not device.is_removable:
            raise UnsafeRemovableTarget("Target is not removable")
        if not device.whole_device:
            raise UnsafeRemovableTarget("Refusing a partition target; a whole removable device is required")
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
