"""Bounded, explicit Recovery asset acquisition."""

from dataclasses import dataclass
import hashlib
from pathlib import Path
import shutil
from typing import Callable, Optional
from urllib.parse import urlparse
import urllib.request

from macloader.exceptions import ArtifactDownloadError, ChecksumMismatchError


@dataclass(frozen=True)
class RecoveryAsset:
    product: str
    build: str
    source_url: str
    sha256: str
    size_bytes: int


class RecoveryAcquirer:
    def __init__(self, transport: Optional[Callable[[str, Path], None]] = None, max_bytes: int = 16 * 1024 * 1024 * 1024):
        self.transport = transport
        self.max_bytes = max_bytes

    def download(self, asset: RecoveryAsset, destination: Path) -> Path:
        parsed = urlparse(asset.source_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ArtifactDownloadError("Recovery source must use HTTPS")
        if asset.size_bytes <= 0 or asset.size_bytes > self.max_bytes:
            raise ArtifactDownloadError("Recovery asset size is missing or exceeds the configured limit")
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_name(destination.name + ".part")
        try:
            if self.transport:
                self.transport(asset.source_url, part)
            else:
                with urllib.request.urlopen(asset.source_url, timeout=60) as response, part.open("wb") as handle:
                    total = 0
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > min(self.max_bytes, asset.size_bytes):
                            raise ArtifactDownloadError("Recovery download exceeds the declared size")
                        handle.write(chunk)
            actual_size = part.stat().st_size
            if actual_size != asset.size_bytes:
                raise ArtifactDownloadError(f"Recovery size mismatch: expected {asset.size_bytes}, got {actual_size}")
            digest = hashlib.sha256(part.read_bytes()).hexdigest()
            if digest != asset.sha256.lower():
                raise ChecksumMismatchError(f"Recovery checksum mismatch: expected {asset.sha256}, got {digest}")
            part.replace(destination)
            return destination
        except Exception:
            part.unlink(missing_ok=True)
            raise
