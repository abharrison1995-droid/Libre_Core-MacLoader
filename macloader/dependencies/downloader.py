"""Safe streaming downloader with SHA-256 validation and mockable transport."""

import logging
from pathlib import Path
from typing import Callable, Optional
import urllib.error
import urllib.request

from macloader.dependencies.cache import compute_file_sha256
from macloader.domain.dependencies import DependencyArtifact
from macloader.exceptions import ArtifactDownloadError, ChecksumMismatchError

logger = logging.getLogger(__name__)


class Downloader:
    """Safely acquires remote dependency archives with integrity verification."""

    def __init__(self, transport: Optional[Callable[[str, Path], None]] = None, timeout: int = 30):
        self.transport = transport
        self.timeout = timeout

    def download_artifact(self, artifact: DependencyArtifact, destination_path: Path) -> Path:
        """Download an artifact to destination_path while verifying its SHA-256 hash."""
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        part_path = destination_path.with_suffix(".part")

        try:
            if self.transport:
                # Custom mock transport (used in tests)
                self.transport(artifact.source_url, part_path)
            else:
                # Standard HTTPS streaming download
                req = urllib.request.Request(
                    artifact.source_url,
                    headers={"User-Agent": "MacLoader-Downloader/0.0.4"},
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    with part_path.open("wb") as out_f:
                        while chunk := response.read(65536):
                            out_f.write(chunk)

            # Compute and verify SHA-256
            actual_sha = compute_file_sha256(part_path)

            if actual_sha != artifact.sha256.lower():
                raise ChecksumMismatchError(
                    f"Integrity check failed for {artifact.asset_name}: "
                    f"expected {artifact.sha256}, got {actual_sha}."
                )

            # Atomic rename from .part to target path
            part_path.replace(destination_path)
            logger.info(f"Successfully downloaded and verified: {artifact.asset_name}")
            return destination_path

        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            part_path.unlink(missing_ok=True)
            raise ArtifactDownloadError(f"Failed to download {artifact.asset_name} from {artifact.source_url}: {e}") from e
        except ChecksumMismatchError:
            part_path.unlink(missing_ok=True)
            raise
        except Exception as e:
            part_path.unlink(missing_ok=True)
            raise ArtifactDownloadError(f"Unexpected error downloading {artifact.asset_name}: {e}") from e
