"""Safe streaming downloader with SHA-256 validation and mockable transport."""

import logging
import hashlib
import os
from pathlib import Path
import tempfile
import time
from typing import Callable, Optional
import urllib.error
import urllib.request
from urllib.parse import urlparse

from macloader.dependencies.cache import compute_file_sha256
from macloader.domain.dependencies import DependencyArtifact
from macloader.exceptions import ArtifactDownloadError, ChecksumMismatchError

logger = logging.getLogger(__name__)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: set[str]):
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, new):  # type: ignore[no-untyped-def]
        parsed = urlparse(new)
        if (
            parsed.scheme != "https"
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
            or (parsed.hostname or "") not in self.allowed_hosts
        ):
            raise ArtifactDownloadError("Refusing an insecure or unapproved download redirect")
        return super().redirect_request(req, fp, code, msg, headers, new)


class Downloader:
    """Safely acquires remote dependency archives with integrity verification."""

    def __init__(self, transport: Optional[Callable[[str, Path], None]] = None, timeout: int = 30, max_download_bytes: int = 512 * 1024 * 1024):
        self.transport = transport
        self.timeout = timeout
        self.max_download_bytes = max_download_bytes

    def download_artifact(
        self,
        artifact: DependencyArtifact,
        destination_path: Path,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> Path:
        """Download an artifact to destination_path while verifying its SHA-256 hash."""
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        fd, part_name = tempfile.mkstemp(prefix=f".{destination_path.name}.", suffix=".part", dir=destination_path.parent)
        os.close(fd)
        part_path = Path(part_name)
        deadline = time.monotonic() + self.timeout

        try:
            if cancel and cancel():
                raise ArtifactDownloadError(f"Download of {artifact.asset_name} was cancelled")
            if self.transport:
                # Custom mock transport (used in tests)
                self.transport(artifact.source_url, part_path)
                if cancel and cancel():
                    raise ArtifactDownloadError(f"Download of {artifact.asset_name} was cancelled")
                if part_path.stat().st_size > self.max_download_bytes:
                    raise ArtifactDownloadError("Download exceeds the configured maximum size")
                actual_sha = compute_file_sha256(part_path)
            else:
                # Standard HTTPS streaming download. Hash and size are
                # checked while bytes are received so an oversized response
                # cannot consume unbounded disk space.
                req = urllib.request.Request(
                    artifact.source_url,
                    headers={"User-Agent": "MacLoader-Downloader/0.0.4"},
                )
                source = urlparse(artifact.source_url)
                if source.scheme != "https" or source.port not in (None, 443) or source.username or source.password or not source.hostname:
                    raise ArtifactDownloadError("Artifact source URL must use HTTPS")
                allowed_hosts = {source.hostname, "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
                opener = urllib.request.build_opener(_SafeRedirectHandler(allowed_hosts))
                with opener.open(req, timeout=self.timeout) as response:
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > self.max_download_bytes:
                        raise ArtifactDownloadError("Download exceeds the configured maximum size")
                    if content_length and artifact.size_bytes > 0 and int(content_length) != artifact.size_bytes:
                        raise ArtifactDownloadError("Download size does not match the cataloged artifact size")
                    hasher = hashlib.sha256()
                    total = 0
                    with part_path.open("wb") as out_f:
                        while True:
                            if cancel and cancel():
                                raise ArtifactDownloadError(f"Download of {artifact.asset_name} was cancelled")
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise ArtifactDownloadError("Download exceeded its deadline")
                            raw = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
                            if raw is not None:
                                raw.settimeout(max(0.1, remaining))
                            chunk = response.read(65536)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > self.max_download_bytes:
                                raise ArtifactDownloadError("Download exceeds the configured maximum size")
                            if time.monotonic() > deadline:
                                raise ArtifactDownloadError("Download exceeded its deadline")
                            hasher.update(chunk)
                            out_f.write(chunk)
                    actual_sha = hasher.hexdigest().lower()

            actual_size = part_path.stat().st_size
            if artifact.size_bytes > 0 and actual_size != artifact.size_bytes:
                raise ArtifactDownloadError(
                    f"Downloaded size mismatch for {artifact.asset_name}: expected {artifact.size_bytes}, got {actual_size}"
                )

            if actual_sha != artifact.sha256.lower():
                raise ChecksumMismatchError(
                    f"Integrity check failed for {artifact.asset_name}: "
                    f"expected {artifact.sha256}, got {actual_sha}."
                )

            # Atomic rename from .part to target path
            part_path.replace(destination_path)
            logger.info(f"Successfully downloaded and verified: {artifact.asset_name}")
            return destination_path

        except KeyboardInterrupt:
            # Ctrl-C is an interruption, not a transport failure.  The
            # unconditional cleanup below still owns the temporary file.
            raise
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            raise ArtifactDownloadError(f"Failed to download {artifact.asset_name} from {artifact.source_url}: {e}") from e
        except ChecksumMismatchError:
            raise
        except Exception as e:
            raise ArtifactDownloadError(f"Unexpected error downloading {artifact.asset_name}: {e}") from e
        finally:
            # A .part file is owned by this operation.  Successful replace()
            # removes it; every failure or interruption removes it here.
            part_path.unlink(missing_ok=True)
