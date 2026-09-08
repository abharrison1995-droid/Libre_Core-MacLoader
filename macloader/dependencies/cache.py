"""Cache management for verified OpenCore and kext dependency archives."""

import hashlib
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile
from typing import Any, Dict, Iterator, List, Optional, Union

from macloader.config import DEFAULT_CACHE_DIR
from macloader.domain.dependencies import ArtifactVariant, DependencySpec
from macloader.exceptions import ChecksumMismatchError

logger = logging.getLogger(__name__)


def compute_file_sha256(file_path: Path) -> str:
    """Compute lowercase hex SHA-256 hash of a file in 64KB chunks."""
    h = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().lower()


class CacheManager:
    """Manages local storage and hash validation of downloaded dependency archives."""

    def __init__(self, cache_dir: Optional[Union[str, Path]] = None, max_cache_bytes: int = 2 * 1024 * 1024 * 1024):
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.max_cache_bytes = max_cache_bytes
        self.downloads_dir = self.cache_dir / "downloads"
        self.index_file = self.cache_dir / "index.json"
        self.lock_file = self.cache_dir / ".cache.lock"
        self._ensure_dirs()
        with self._locked():
            self._cleanup_orphans()

    def _ensure_dirs(self) -> None:
        self.downloads_dir.mkdir(parents=True, exist_ok=True)

    def _cleanup_orphans(self) -> None:
        for path in self.downloads_dir.iterdir():
            if path.is_file() and (path.name.startswith(".") or path.suffix == ".part"):
                path.unlink(missing_ok=True)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        deadline = time.monotonic() + 30
        acquired = False
        while not acquired:
            try:
                fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                acquired = True
            except FileExistsError:
                try:
                    if time.time() - self.lock_file.stat().st_mtime > 300:
                        self.lock_file.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for dependency cache lock")
                time.sleep(0.05)
        try:
            yield
        finally:
            self.lock_file.unlink(missing_ok=True)

    def _load_index(self) -> Dict[str, Any]:
        if self.index_file.is_file():
            try:
                data = json.loads(self.index_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception as e:
                logger.debug(f"Failed to read cache index: {e}")
        return {}

    def _save_index(self, index_data: Dict[str, Any]) -> None:
        try:
            fd, name = tempfile.mkstemp(prefix=".index.", suffix=".tmp", dir=self.cache_dir)
            os.close(fd)
            tmp = Path(name)
            tmp.write_text(json.dumps(index_data, indent=2), encoding="utf-8")
            with tmp.open("r+b") as handle:
                os.fsync(handle.fileno())
            os.replace(tmp, self.index_file)
        except Exception as e:
            logger.error(f"Failed to save cache index: {e}")
            if "tmp" in locals():
                tmp.unlink(missing_ok=True)
            raise

    def get_artifact_cache_path(self, spec: DependencySpec, variant: ArtifactVariant = ArtifactVariant.RELEASE) -> Path:
        """Construct the deterministic file path for a dependency artifact."""
        artifact = spec.get_artifact(variant)
        expected_sha = (artifact.sha256 if artifact else "unknown")[:12]
        for value, label in ((spec.id, "dependency ID"), (spec.version, "version")):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", value) or ".." in value:
                raise ChecksumMismatchError(f"Unsafe {label} for cache path: {value!r}")
        suffix = "." + (artifact.archive_type if artifact else "zip").replace("/", "_")
        filename = f"{spec.id}_{spec.version}_{variant.value}_{expected_sha}{suffix}"
        target = (self.downloads_dir / filename).resolve()
        if not target.is_relative_to(self.downloads_dir.resolve()):
            raise ChecksumMismatchError("Cache artifact path escapes the downloads directory")
        return target

    def has_valid_artifact(self, spec: DependencySpec, variant: ArtifactVariant = ArtifactVariant.RELEASE) -> bool:
        """Check if artifact is present in cache AND matches its expected SHA-256."""
        artifact = spec.get_artifact(variant)
        if not artifact:
            return False

        cache_path = self.get_artifact_cache_path(spec, variant)
        if not cache_path.is_file():
            return False

        # Always verify SHA-256 on cache hit
        actual_sha = compute_file_sha256(cache_path)
        if actual_sha != artifact.sha256.lower():
            logger.warning(
                f"Cache entry corrupted for {spec.id} ({variant.value}): expected {artifact.sha256}, got {actual_sha}."
            )
            # Remove corrupted file
            cache_path.unlink(missing_ok=True)
            return False

        return True

    def get_cached_path_if_valid(self, spec: DependencySpec, variant: ArtifactVariant = ArtifactVariant.RELEASE) -> Optional[Path]:
        """Return the Path to cached artifact if present and valid, otherwise None."""
        if self.has_valid_artifact(spec, variant):
            return self.get_artifact_cache_path(spec, variant)
        return None

    def put_artifact(self, spec: DependencySpec, variant: ArtifactVariant, source_file: Path) -> Path:
        """Atomically place a verified artifact into the cache."""
        artifact = spec.get_artifact(variant)
        if not artifact:
            raise ChecksumMismatchError(f"No artifact definition for {spec.id} variant {variant.value}")

        target_path = self.get_artifact_cache_path(spec, variant)
        with self._locked():
            # Only copy if source is not already target path
            if source_file.resolve() != target_path.resolve():
                fd, name = tempfile.mkstemp(prefix=f".{target_path.name}.", suffix=".tmp", dir=self.downloads_dir)
                os.close(fd)
                tmp = Path(name)
                try:
                    shutil.copyfile(source_file, tmp, follow_symlinks=False)
                    actual_sha = compute_file_sha256(tmp)
                    if actual_sha != artifact.sha256.lower():
                        raise ChecksumMismatchError(f"Checksum or size mismatch for {spec.id} ({variant.value})")
                    os.replace(tmp, target_path)
                finally:
                    tmp.unlink(missing_ok=True)

            index = self._load_index()
            key = f"{spec.id}:{spec.version}:{variant.value}"
            actual_sha = compute_file_sha256(target_path)
            if actual_sha != artifact.sha256.lower():
                raise ChecksumMismatchError(f"Published cache artifact changed for {spec.id} ({variant.value})")
            index[key] = {
                "dependency_id": spec.id,
                "version": spec.version,
                "variant": variant.value,
                "sha256": actual_sha,
                "path": str(target_path),
                "size_bytes": target_path.stat().st_size,
            }
            self._save_index(index)
            self._prune_cache_locked()
        return target_path

    def _prune_cache_locked(self) -> None:
        files = [path for path in self.downloads_dir.iterdir() if path.is_file() and not path.name.startswith(".")]
        total = sum(path.stat().st_size for path in files)
        for path in sorted(files, key=lambda item: item.stat().st_mtime):
            if total <= self.max_cache_bytes:
                break
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total -= size

    def get_cache_stats(self) -> Dict[str, Any]:
        """Return summary statistics of cached files."""
        total_files = 0
        total_bytes = 0
        entries: List[Dict[str, Any]] = []

        if self.downloads_dir.is_dir():
            for f in self.downloads_dir.iterdir():
                if not f.is_file() or f.name.startswith(".") or f.suffix == ".part":
                    continue
                total_files += 1
                size = f.stat().st_size
                total_bytes += size
                entries.append({"name": f.name, "size_bytes": size, "path": str(f)})

        return {
            "cache_dir": str(self.cache_dir),
            "total_files": total_files,
            "total_size_bytes": total_bytes,
            "entries": entries,
        }

    def clear_cache(self) -> None:
        """Remove all cached dependency files and reset index."""
        with self._locked():
            if self.downloads_dir.is_dir():
                for f in self.downloads_dir.iterdir():
                    if f.is_file() and not f.is_symlink():
                        f.unlink(missing_ok=True)
            self.index_file.unlink(missing_ok=True)
