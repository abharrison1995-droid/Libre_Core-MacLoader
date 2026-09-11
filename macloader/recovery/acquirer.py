"""Bounded, explicit Recovery asset acquisition."""

from dataclasses import dataclass
import hashlib
import json
import os
import re
import shutil
import stat
import struct
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Optional
from urllib.parse import urlparse
import urllib.error
import urllib.request

from macloader.exceptions import ArtifactDownloadError, ChecksumMismatchError
from macloader.domain.recovery import RecoveryEvidence


@dataclass(frozen=True)
class RecoveryAsset:
    product: str
    build: str
    source_url: str
    sha256: str
    size_bytes: int
    session_token: Optional[str] = None


@dataclass(frozen=True)
class RecoveryBundle:
    image_path: Path
    chunklist_path: Path
    evidence: RecoveryEvidence


# OpenCore's pinned macrecovery.py public key.  The signed chunklist, rather
# than a locally computed digest alone, is the authenticity boundary.
_APPLE_EFI_ROM_PUBLIC_KEY = int(
    "c3e748cad9cd384329e10e25a91e43e1a762ff529ade578c935bddf9b13f2179d4855e6fc89e9e29ca12517d17dfa1edce0bebf0ea7b461ffe61d94e2bdf72c196f89acd3536b644064014dae25a15db6bb0852ecbd120916318d1ccdea3c84c92ed743fc176d0baca920d3fcf3158aff731f88ce0623182a8ed67e650515f75745909f07d415f55fc15a35654d118c55a462d37a3acda08612f3f3f6571761efccbcc299aee99b3a4fd6212ccfff5ef37a2c334e871191f7e1c31960e010a54e86fa3f62e6d6905e1cd57732410a3eb0c6b4defdabe9f59bf1618758c751cd56cef851d1c0eaa1c558e37ac108da9089863d20e2e7e4bf475ec66fe6b3efdcf",
    16,
)
_CHUNKLIST_HEADER = struct.Struct("<4sIBBBxQQQ")
_CHUNK = struct.Struct("<I32s")
_MAX_CHUNKS = 2_000_000


def is_approved_recovery_host(host: Optional[str]) -> bool:
    if not host:
        return False
    h = host.lower()
    return h in {"osrecovery.apple.com", "updates.cdn-apple.com", "cdn-apple.com"}


class _RecoveryRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, is_host_approved: Callable[[Optional[str]], bool]):
        super().__init__()
        self._is_host_approved = is_host_approved

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        parsed = urlparse(newurl)
        if (
            parsed.scheme != "https"
            or not self._is_host_approved(parsed.hostname)
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise ArtifactDownloadError("Recovery redirect leaves the approved HTTPS source policy")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class RecoveryAcquirer:
    def __init__(
        self,
        transport: Optional[Callable[[str, Path], None]] = None,
        max_bytes: int = 16 * 1024 * 1024 * 1024,
        timeout_seconds: float = 60.0,
        cancel: Optional[Callable[[], bool]] = None,
        allowed_hosts: Optional[set[str]] = None,
        expected_macos: str = "sequoia",
        resume: bool = False,
    ):
        self.transport = transport
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds
        self.cancel = cancel
        self.allowed_hosts = allowed_hosts
        self.expected_macos = expected_macos
        self.resume = resume

    def _is_host_approved(self, host: Optional[str]) -> bool:
        if not host:
            return False
        h = host.lower()
        if self.allowed_hosts is not None:
            return h in {a.lower() for a in self.allowed_hosts}
        return is_approved_recovery_host(h)

    def download(self, asset: RecoveryAsset, destination: Path) -> Path:
        parsed = urlparse(asset.source_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ArtifactDownloadError("Recovery source must use HTTPS")
        if not self._is_host_approved(parsed.hostname):
            raise ArtifactDownloadError("Recovery source host is not an approved Apple domain")
        if not asset.product or not asset.product.strip():
            raise ArtifactDownloadError("Recovery asset product is missing or empty")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", asset.product):
            raise ArtifactDownloadError(f"Recovery asset product contains invalid characters: {asset.product}")
        if not asset.build or not asset.build.strip():
            raise ArtifactDownloadError("Recovery asset build is missing or empty")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", asset.build):
            raise ArtifactDownloadError(f"Recovery asset build contains invalid characters: {asset.build}")
        if not validate_recovery_product_version(asset.product, asset.build, self.expected_macos):
            raise ArtifactDownloadError(
                f"Recovery product/build is not supported for macOS {self.expected_macos}: "
                f"{asset.product}/{asset.build}"
            )
        if asset.size_bytes <= 0 or asset.size_bytes > self.max_bytes:
            raise ArtifactDownloadError("Recovery asset size is missing or exceeds the configured limit")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", asset.sha256):
            raise ArtifactDownloadError("Recovery asset SHA-256 is invalid")
        self._require_safe_publication()
        if destination.is_symlink():
            raise ArtifactDownloadError("Recovery destination must not be a symlink")
        self._reject_symlink_ancestors(destination.parent)

        part: Optional[Path] = None
        metadata: Optional[Path] = None
        resume_directory_fd: Optional[int] = None
        part_fd: Optional[int] = None
        metadata_fd: Optional[int] = None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._reject_symlink_ancestors(destination.parent)
            if shutil.disk_usage(destination.parent).free < asset.size_bytes:
                raise ArtifactDownloadError("Insufficient free disk space for Recovery asset")
            if self.resume:
                if self.transport:
                    raise ArtifactDownloadError("Resumable Recovery requires the HTTPS range-capable transport")
                if destination.exists() and not destination.is_symlink():
                    if destination.stat().st_size == asset.size_bytes and self._streaming_sha256(
                        destination, time.monotonic() + self.timeout_seconds
                    ).lower() == asset.sha256.lower():
                        return destination
                    destination.unlink()
                part, metadata = self._resume_paths(asset, destination)
                resume_directory_fd = self._open_owned_directory(destination.parent)
                if not self._resume_metadata_matches_fd(resume_directory_fd, metadata.name, asset):
                    self._unlink_relative(resume_directory_fd, part.name)
                    self._unlink_relative(resume_directory_fd, metadata.name)
                    metadata_fd = self._open_resume_fd(resume_directory_fd, metadata.name, os.O_RDWR | os.O_CREAT | os.O_TRUNC)
                    self._write_resume_metadata_fd(metadata_fd, asset)
                else:
                    metadata_fd = self._open_resume_fd(resume_directory_fd, metadata.name, os.O_RDWR)
                part_fd = self._open_resume_fd(resume_directory_fd, part.name, os.O_RDWR | os.O_CREAT)
                if os.fstat(part_fd).st_size > asset.size_bytes:
                    os.close(part_fd)
                    part_fd = None
                    self._unlink_relative(resume_directory_fd, part.name)
                    part_fd = self._open_resume_fd(resume_directory_fd, part.name, os.O_RDWR | os.O_CREAT)
            else:
                fd, part_name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".part", dir=destination.parent)
                os.close(fd)
                part = Path(part_name)
            deadline = time.monotonic() + self.timeout_seconds

            self._check_cancel(deadline)
            if self.transport:
                self.transport(asset.source_url, part)
                self._check_cancel(deadline)
                actual_size = part.stat().st_size
                if actual_size > min(self.max_bytes, asset.size_bytes):
                    raise ArtifactDownloadError("Recovery download exceeds the configured size")
                if actual_size != asset.size_bytes:
                    raise ArtifactDownloadError(f"Recovery size mismatch: expected {asset.size_bytes}, got {actual_size}")
                digest = self._streaming_sha256(part, deadline)
            else:
                opener = urllib.request.build_opener(_RecoveryRedirectHandler(self._is_host_approved))
                request_headers = {"User-Agent": "MacLoader-Recovery/0.0.4"}
                if asset.session_token:
                    request_headers["Cookie"] = f"AssetToken={asset.session_token}"
                existing_size = os.fstat(part_fd).st_size if self.resume and part_fd is not None else 0
                hasher = hashlib.sha256()
                if existing_size:
                    if part_fd is None:
                        raise ArtifactDownloadError("Recovery resume file descriptor was not opened")
                    with os.fdopen(os.dup(part_fd), "rb", closefd=True) as existing_handle:
                        while chunk := existing_handle.read(1024 * 1024):
                            self._check_cancel(deadline)
                            hasher.update(chunk)
                    request_headers["Range"] = f"bytes={existing_size}-"
                req = urllib.request.Request(asset.source_url, headers=request_headers)
                with opener.open(req, timeout=min(60.0, self.timeout_seconds)) as response:
                    final = urlparse(response.geturl())
                    if final.scheme != "https" or not self._is_host_approved(final.hostname):
                        raise ArtifactDownloadError("Recovery redirect leaves the approved HTTPS source host")
                    status = getattr(response, "status", None)
                    if status is None:
                        try:
                            status = response.getcode()
                        except AttributeError:
                            status = None
                    content_range = response.headers.get("Content-Range")
                    resumed = existing_size > 0 and status == 206
                    if existing_size and resumed:
                        match = re.fullmatch(r"bytes (\d+)-\d+/(\d+|\*)", content_range or "")
                        if match is None or int(match.group(1)) != existing_size:
                            raise ArtifactDownloadError("Recovery server returned an invalid resume range")
                    elif existing_size:
                        # A compliant server may ignore Range.  Discard the
                        # partial and verify the resulting full response instead
                        # of concatenating duplicate bytes.
                        hasher = hashlib.sha256()
                        existing_size = 0
                    content_length_header = response.headers.get("Content-Length")
                    if content_length_header:
                        try:
                            content_length = int(content_length_header)
                        except ValueError as e:
                            raise ArtifactDownloadError(f"Invalid Content-Length header: {content_length_header}") from e
                        if content_length > self.max_bytes:
                            raise ArtifactDownloadError("Recovery asset size exceeds the configured limit")
                        expected_length = asset.size_bytes - existing_size if resumed else asset.size_bytes
                        if content_length != expected_length:
                            raise ArtifactDownloadError(f"Recovery size mismatch: expected {asset.size_bytes}, got {content_length}")
                    total = existing_size
                    if part_fd is not None:
                        os.lseek(part_fd, 0, os.SEEK_END if resumed else os.SEEK_SET)
                        if not resumed:
                            os.ftruncate(part_fd, 0)
                        handle = os.fdopen(os.dup(part_fd), "ab" if resumed else "wb", closefd=True)
                    else:
                        handle = part.open("ab" if resumed else "wb")
                    with handle:
                        while True:
                            self._check_cancel(deadline)
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise ArtifactDownloadError("Recovery download exceeded its total deadline")
                            raw = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
                            if raw is not None:
                                raw.settimeout(max(0.1, remaining))
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > min(self.max_bytes, asset.size_bytes):
                                raise ArtifactDownloadError("Recovery download exceeds the declared size")
                            hasher.update(chunk)
                            handle.write(chunk)
                actual_size = os.fstat(part_fd).st_size if part_fd is not None else part.stat().st_size
                if actual_size > min(self.max_bytes, asset.size_bytes):
                    raise ArtifactDownloadError("Recovery download exceeds the configured size")
                if actual_size != asset.size_bytes:
                    raise ArtifactDownloadError(f"Recovery size mismatch: expected {asset.size_bytes}, got {actual_size}")
                digest = hasher.hexdigest()

            self._check_cancel(deadline)
            if part.is_symlink():
                raise ArtifactDownloadError("Recovery temporary file became a symlink")
            if digest.lower() != asset.sha256.lower():
                raise ChecksumMismatchError(f"Recovery checksum mismatch: expected {asset.sha256}, got {digest}")
            self._publish_owned(part, destination)
            if metadata is not None:
                if resume_directory_fd is not None:
                    self._unlink_relative(resume_directory_fd, metadata.name)
                else:
                    metadata.unlink(missing_ok=True)
            part = None
            return destination
        except (ChecksumMismatchError, ArtifactDownloadError) as exc:
            if part is not None and (not self.resume or isinstance(exc, ChecksumMismatchError)):
                if resume_directory_fd is not None:
                    self._unlink_relative(resume_directory_fd, part.name)
                    if metadata is not None:
                        self._unlink_relative(resume_directory_fd, metadata.name)
                else:
                    part.unlink(missing_ok=True)
                    if metadata is not None:
                        metadata.unlink(missing_ok=True)
            raise
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            if part is not None and not self.resume:
                part.unlink(missing_ok=True)
            raise ArtifactDownloadError(f"Recovery download failed: {e}") from e
        except Exception as e:
            if part is not None and not self.resume:
                part.unlink(missing_ok=True)
            raise ArtifactDownloadError(f"Unexpected error acquiring Recovery asset: {e}") from e
        finally:
            for candidate in (part_fd, metadata_fd, resume_directory_fd):
                if candidate is not None:
                    try:
                        os.close(candidate)
                    except OSError:
                        pass

    @staticmethod
    def _resume_paths(asset: RecoveryAsset, destination: Path) -> tuple[Path, Path]:
        suffix = asset.sha256.lower()
        part = destination.with_name(f".{destination.name}.{suffix}.part")
        return part, Path(str(part) + ".json")

    @staticmethod
    def _resume_metadata_matches(metadata: Path, asset: RecoveryAsset) -> bool:
        try:
            data = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return False
        return bool(data == {"sha256": asset.sha256.lower(), "size_bytes": asset.size_bytes})

    @staticmethod
    def _resume_metadata_matches_fd(directory_fd: int, name: str, asset: RecoveryAsset) -> bool:
        try:
            flags = os.O_RDONLY | os.O_NOFOLLOW
            if hasattr(os, "O_NONBLOCK"):
                flags |= os.O_NONBLOCK
            fd = os.open(name, flags, dir_fd=directory_fd)
            try:
                metadata_stat = os.fstat(fd)
                if not stat.S_ISREG(metadata_stat.st_mode) or metadata_stat.st_size > 16 * 1024:
                    return False
                data = json.loads(os.read(fd, metadata_stat.st_size).decode("utf-8"))
            finally:
                os.close(fd)
        except (OSError, ValueError, TypeError, UnicodeError):
            return False
        return bool(data == {"sha256": asset.sha256.lower(), "size_bytes": asset.size_bytes})

    @staticmethod
    def _open_resume_fd(directory_fd: int, name: str, flags: int) -> int:
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        fd = os.open(name, flags | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise ArtifactDownloadError("Recovery resume file must be a regular file")
        return fd

    @staticmethod
    def _write_resume_metadata_fd(fd: int, asset: RecoveryAsset) -> None:
        payload = (json.dumps({"sha256": asset.sha256.lower(), "size_bytes": asset.size_bytes}, sort_keys=True) + "\n").encode("utf-8")
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload)
        os.fsync(fd)

    @staticmethod
    def _write_resume_metadata(metadata: Path, asset: RecoveryAsset) -> None:
        metadata.write_text(
            json.dumps({"sha256": asset.sha256.lower(), "size_bytes": asset.size_bytes}, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def download_bundle(
        self,
        image: RecoveryAsset,
        chunklist: RecoveryAsset,
        image_destination: Path,
        chunklist_destination: Path,
        lock_digest: str,
        verification_tool: str = "OpenCore-macrecovery-1.0.7-compatible",
        resume: Optional[bool] = None,
    ) -> RecoveryBundle:
        """Download both signed assets and verify every image chunk before publication."""
        image_destination = Path(image_destination)
        chunklist_destination = Path(chunklist_destination)
        if image_destination.parent.absolute() != chunklist_destination.parent.absolute():
            raise ArtifactDownloadError("Recovery bundle assets must share a publication directory")
        self._reject_symlink_ancestors(image_destination.parent)
        if image_destination.is_symlink() or chunklist_destination.is_symlink():
            raise ArtifactDownloadError("Recovery bundle destinations must not be symlinks")
        self._require_safe_publication()
        destination_directory_fd = self._open_owned_directory(image_destination.parent)
        use_resume = self.resume if resume is None else resume
        downloader = self
        if use_resume != self.resume:
            downloader = RecoveryAcquirer(
                transport=self.transport,
                max_bytes=self.max_bytes,
                timeout_seconds=self.timeout_seconds,
                cancel=self.cancel,
                allowed_hosts=self.allowed_hosts,
                expected_macos=self.expected_macos,
                resume=use_resume,
            )
        try:
            # Keep staging outside the publication directory. When resuming is
            # explicitly enabled, retain a deterministic hidden staging
            # directory so an interrupted image/chunklist transfer can use HTTP
            # Range on the next invocation. The validated destination descriptor
            # remains open through the whole transaction.
            persistent_staging = image_destination.parent / f".{image_destination.stem}.recovery-resume"
            if use_resume:
                if persistent_staging.is_symlink():
                    raise ArtifactDownloadError("Recovery resume staging directory must not be a symlink")
                persistent_staging.mkdir(parents=True, exist_ok=True)
                self._reject_symlink_ancestors(persistent_staging)
                staging = persistent_staging
            else:
                staging_name = tempfile.mkdtemp(prefix="recovery-bundle.")
                staging = Path(staging_name)
            try:
                staging_directory_fd = self._open_owned_directory(staging)
                try:
                    staged_image = downloader.download(image, staging / "image.part")
                    staged_chunklist = downloader.download(chunklist, staging / "chunklist.part")
                    verified_chunks, image_size = verify_apple_chunklist(
                        staged_image, staged_chunklist, image.sha256, chunklist.sha256
                    )
                    had_image = self._entry_exists(destination_directory_fd, image_destination.name)
                    had_chunklist = self._entry_exists(destination_directory_fd, chunklist_destination.name)
                    try:
                        if had_image:
                            self._rename_relative(
                                image_destination.name, "image.backup", destination_directory_fd, staging_directory_fd
                            )
                        if had_chunklist:
                            self._rename_relative(
                                chunklist_destination.name, "chunklist.backup", destination_directory_fd, staging_directory_fd
                            )
                        self._publish_owned(staged_image, image_destination, destination_directory_fd)
                        self._publish_owned(staged_chunklist, chunklist_destination, destination_directory_fd)
                    except Exception:
                        self._unlink_relative(destination_directory_fd, image_destination.name)
                        self._unlink_relative(destination_directory_fd, chunklist_destination.name)
                        if had_image and self._entry_exists(staging_directory_fd, "image.backup"):
                            self._rename_relative(
                                "image.backup", image_destination.name, staging_directory_fd, destination_directory_fd
                            )
                        if had_chunklist and self._entry_exists(staging_directory_fd, "chunklist.backup"):
                            self._rename_relative(
                                "chunklist.backup", chunklist_destination.name, staging_directory_fd, destination_directory_fd
                            )
                        raise
                    if use_resume:
                        for entry in staging.iterdir():
                            if entry.is_file() or entry.is_symlink():
                                entry.unlink()
                        staging.rmdir()
                finally:
                    os.close(staging_directory_fd)
            finally:
                if not use_resume:
                    shutil.rmtree(staging, ignore_errors=False)
        finally:
            os.close(destination_directory_fd)
        evidence = RecoveryEvidence(
            lock_digest=lock_digest,
            image_digest=image.sha256.lower(),
            chunklist_digest=chunklist.sha256.lower(),
            signed_chunklist=True,
            verified_chunks=verified_chunks,
            image_size_bytes=image_size,
            verification_tool=verification_tool,
        )
        return RecoveryBundle(image_destination, chunklist_destination, evidence)

    @staticmethod
    def _publish_owned(part: Path, destination: Path, destination_directory_fd: Optional[int] = None) -> None:
        """Publish inside a rechecked directory without following it."""
        RecoveryAcquirer._require_safe_publication()
        RecoveryAcquirer._reject_symlink_ancestors(part.parent)
        RecoveryAcquirer._reject_symlink_ancestors(destination.parent)
        if part.is_symlink():
            raise ArtifactDownloadError("Recovery temporary asset must not be a symlink")
        if destination.is_symlink():
            raise ArtifactDownloadError("Recovery destination must not be a symlink")
        source_directory_fd = RecoveryAcquirer._open_owned_directory(part.parent)
        close_destination_fd = destination_directory_fd is None
        if destination_directory_fd is None:
            destination_directory_fd = RecoveryAcquirer._open_owned_directory(destination.parent)
        try:
            os.rename(
                part.name,
                destination.name,
                src_dir_fd=source_directory_fd,
                dst_dir_fd=destination_directory_fd,
            )
        finally:
            os.close(source_directory_fd)
            if close_destination_fd:
                os.close(destination_directory_fd)

    @staticmethod
    def _require_safe_publication() -> None:
        if os.name == "nt" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            raise ArtifactDownloadError("Safe Recovery publication primitives are unavailable on this platform")

    @staticmethod
    def _open_owned_directory(path: Path) -> int:
        RecoveryAcquirer._reject_symlink_ancestors(path)
        try:
            return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise ArtifactDownloadError("Recovery publication directory is not safely accessible") from exc

    @staticmethod
    def _entry_exists(directory_fd: int, name: str) -> bool:
        try:
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True

    @staticmethod
    def _rename_relative(source: str, destination: str, source_directory_fd: int, destination_directory_fd: int) -> None:
        os.rename(source, destination, src_dir_fd=source_directory_fd, dst_dir_fd=destination_directory_fd)

    @staticmethod
    def _unlink_relative(directory_fd: int, name: str) -> None:
        try:
            os.unlink(name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass

    def _check_cancel(self, deadline: float) -> None:
        if self.cancel and self.cancel():
            raise ArtifactDownloadError("Recovery download cancelled")
        if time.monotonic() > deadline:
            raise ArtifactDownloadError("Recovery download exceeded its total deadline")

    def _streaming_sha256(self, path: Path, deadline: float) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                self._check_cancel(deadline)
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _reject_symlink_ancestors(path: Path) -> None:
        current = path.absolute()
        for p in (current, *current.parents):
            if p.is_symlink():
                raise ArtifactDownloadError("Recovery destination has a symlinked parent")


SUPPORTED_RECOVERY_MATRIX: dict[str, dict[str, Any]] = {
    "sequoia": {
        "name": "macOS Sequoia",
        "major_version": 15,
        "build_prefix": "24",
        "supported_models": ["Lenovo ThinkPad T480s", "Lenovo ThinkPad T480"],
    },
    "sonoma": {
        "name": "macOS Sonoma",
        "major_version": 14,
        "build_prefix": "23",
        "supported_models": ["Lenovo ThinkPad T480s"],
    },
    "tahoe": {
        "name": "macOS Tahoe",
        "major_version": 26,
        "build_prefix": "26",
        "supported_models": [],
    },
}


def validate_recovery_product_version(product: str, build: str, expected_macos: Optional[str] = None) -> bool:
    """Validate that product and build match a supported macOS release."""
    if not product or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", product):
        return False
    if not build or not re.fullmatch(r"[0-9]{2}[A-Za-z][0-9]{2,6}[a-z]?", build):
        return False
    if expected_macos is not None:
        targets: tuple[str, ...] = (expected_macos.lower(),)
    else:
        targets = tuple(SUPPORTED_RECOVERY_MATRIX)
    return any(
        target in SUPPORTED_RECOVERY_MATRIX
        and build.startswith(SUPPORTED_RECOVERY_MATRIX[target]["build_prefix"])
        for target in targets
    )


def verify_recovery_integrity(asset: RecoveryAsset, target_path: Path) -> bool:
    """Bounded integrity readback check of a downloaded recovery asset."""
    target_path = Path(target_path)
    if (
        asset.size_bytes <= 0
        or asset.size_bytes > 16 * 1024 * 1024 * 1024
        or not re.fullmatch(r"[0-9a-fA-F]{64}", asset.sha256)
    ):
        return False
    if not target_path.is_file() or target_path.is_symlink():
        return False
    if target_path.stat().st_size != asset.size_bytes:
        return False
    hasher = hashlib.sha256()
    with target_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest().lower() == asset.sha256.lower()


def verify_apple_chunklist(
    image_path: Path,
    chunklist_path: Path,
    expected_image_sha256: str,
    expected_chunklist_sha256: str,
) -> tuple[int, int]:
    """Verify Apple's signed CNKL file and complete image readback.

    The parser is deliberately bounded and rejects the unsigned chunklist mode
    used by the upstream utility as a hard authentication failure.
    """
    image_path = Path(image_path)
    chunklist_path = Path(chunklist_path)
    if image_path.is_symlink() or chunklist_path.is_symlink() or not image_path.is_file() or not chunklist_path.is_file():
        raise ArtifactDownloadError("Recovery image and chunklist must be regular files")
    for value, label in ((expected_image_sha256, "image"), (expected_chunklist_sha256, "chunklist")):
        if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
            raise ArtifactDownloadError(f"Recovery {label} digest is invalid")
    if hashlib.sha256(chunklist_path.read_bytes()).hexdigest().lower() != expected_chunklist_sha256.lower():
        raise ChecksumMismatchError("Recovery chunklist checksum mismatch")

    with chunklist_path.open("rb") as handle:
        header = handle.read(_CHUNKLIST_HEADER.size)
        if len(header) != _CHUNKLIST_HEADER.size:
            raise ArtifactDownloadError("Recovery chunklist header is truncated")
        magic, header_size, file_version, chunk_method, signature_method, count, offset, signature_offset = _CHUNKLIST_HEADER.unpack(header)
        if magic != b"CNKL" or header_size != _CHUNKLIST_HEADER.size or file_version != 1 or chunk_method != 1:
            raise ArtifactDownloadError("Recovery chunklist header is not the supported Apple format")
        if count <= 0 or count > _MAX_CHUNKS or offset != _CHUNKLIST_HEADER.size:
            raise ArtifactDownloadError("Recovery chunklist has an invalid chunk count or offset")
        if signature_offset != offset + _CHUNK.size * count:
            raise ArtifactDownloadError("Recovery chunklist signature offset is invalid")
        chunks = []
        digest = hashlib.sha256()
        digest.update(header)
        for _ in range(count):
            entry = handle.read(_CHUNK.size)
            if len(entry) != _CHUNK.size:
                raise ArtifactDownloadError("Recovery chunklist entries are truncated")
            digest.update(entry)
            chunks.append(_CHUNK.unpack(entry))
        signed_digest = digest.digest()
        if signature_method == 1:
            signature = handle.read(256)
            if len(signature) != 256:
                raise ArtifactDownloadError("Recovery chunklist signature is truncated")
            plaintext = int(f"0x1{'f' * 404}003031300d060960864801650304020105000420{'0' * 64}", 16) | int.from_bytes(signed_digest, "big")
            if pow(int.from_bytes(signature, "little"), 0x10001, _APPLE_EFI_ROM_PUBLIC_KEY) != plaintext:
                raise ArtifactDownloadError("Recovery chunklist signature is invalid")
        elif signature_method == 2:
            # OpenCore recognizes this as a digest-only development format;
            # MacLoader requires authenticated Apple Recovery for release use.
            raise ArtifactDownloadError("Recovery chunklist is unsigned")
        else:
            raise ArtifactDownloadError("Recovery chunklist signature method is unsupported")
        if handle.read(1) != b"":
            raise ArtifactDownloadError("Recovery chunklist contains trailing data")

    image_hash = hashlib.sha256()
    image_size = 0
    with image_path.open("rb") as image:
        for index, (size, expected) in enumerate(chunks, start=1):
            data = image.read(size)
            if len(data) != size or hashlib.sha256(data).digest() != expected:
                raise ChecksumMismatchError(f"Recovery image chunk {index} failed verification")
            image_hash.update(data)
            image_size += size
        if image.read(1) != b"":
            raise ArtifactDownloadError("Recovery image is larger than its signed chunklist")
    if image_hash.hexdigest().lower() != expected_image_sha256.lower():
        raise ChecksumMismatchError("Recovery image checksum mismatch")
    return len(chunks), image_size
