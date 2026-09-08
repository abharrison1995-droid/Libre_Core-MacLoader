"""Bounded, explicit Recovery asset acquisition."""

from dataclasses import dataclass
import hashlib
import os
import re
import shutil
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Optional
from urllib.parse import urlparse
import urllib.error
import urllib.request

from macloader.exceptions import ArtifactDownloadError, ChecksumMismatchError


@dataclass(frozen=True)
class RecoveryAsset:
    product: str
    build: str
    source_url: str
    sha256: str
    size_bytes: int


def is_approved_recovery_host(host: Optional[str]) -> bool:
    if not host:
        return False
    h = host.lower()
    apple_suffixes = (".apple.com", ".cdn-apple.com")
    return h in ("apple.com", "cdn-apple.com") or any(h.endswith(s) for s in apple_suffixes)


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
    ):
        self.transport = transport
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds
        self.cancel = cancel
        self.allowed_hosts = allowed_hosts

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
        if asset.size_bytes <= 0 or asset.size_bytes > self.max_bytes:
            raise ArtifactDownloadError("Recovery asset size is missing or exceeds the configured limit")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", asset.sha256):
            raise ArtifactDownloadError("Recovery asset SHA-256 is invalid")
        if destination.is_symlink():
            raise ArtifactDownloadError("Recovery destination must not be a symlink")
        self._reject_symlink_ancestors(destination.parent)

        part: Optional[Path] = None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(destination.parent).free < asset.size_bytes:
                raise ArtifactDownloadError("Insufficient free disk space for Recovery asset")
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
                req = urllib.request.Request(asset.source_url, headers={"User-Agent": "MacLoader-Recovery/0.0.4"})
                hasher = hashlib.sha256()
                with opener.open(req, timeout=min(60.0, self.timeout_seconds)) as response, part.open("wb") as handle:
                    final = urlparse(response.geturl())
                    if final.scheme != "https" or not self._is_host_approved(final.hostname):
                        raise ArtifactDownloadError("Recovery redirect leaves the approved HTTPS source host")
                    content_length_header = response.headers.get("Content-Length")
                    if content_length_header:
                        try:
                            content_length = int(content_length_header)
                        except ValueError as e:
                            raise ArtifactDownloadError(f"Invalid Content-Length header: {content_length_header}") from e
                        if content_length > self.max_bytes:
                            raise ArtifactDownloadError("Recovery asset size exceeds the configured limit")
                        if content_length != asset.size_bytes:
                            raise ArtifactDownloadError(f"Recovery size mismatch: expected {asset.size_bytes}, got {content_length}")
                    total = 0
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
                actual_size = part.stat().st_size
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
            part.replace(destination)
            return destination
        except (ChecksumMismatchError, ArtifactDownloadError):
            if part is not None:
                part.unlink(missing_ok=True)
            raise
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            if part is not None:
                part.unlink(missing_ok=True)
            raise ArtifactDownloadError(f"Recovery download failed: {e}") from e
        except Exception as e:
            if part is not None:
                part.unlink(missing_ok=True)
            raise ArtifactDownloadError(f"Unexpected error acquiring Recovery asset: {e}") from e

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
