"""Cache management for verified OpenCore and kext dependency archives."""

import hashlib
import json
import logging
import os
import re
import socket
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable, Dict, Iterator, List, Optional, Union

from macloader.config import DEFAULT_CACHE_DIR
from macloader.domain.dependencies import ArtifactVariant, DependencySpec
from macloader.exceptions import ArtifactDownloadError, ChecksumMismatchError

logger = logging.getLogger(__name__)


def is_process_alive(pid: int) -> bool:
    """Check whether a process with the given PID is currently alive on this host."""
    if pid <= 0 or pid > 2147483647:
        return False
    if sys.platform == "win32":
        import ctypes
        import ctypes.wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
        kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
        kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        kernel32.WaitForSingleObject.restype = ctypes.wintypes.DWORD
        kernel32.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
        if not handle:
            err = ctypes.get_last_error()
            return bool(err == 5)  # ERROR_ACCESS_DENIED means process exists in another security context
        try:
            res = kernel32.WaitForSingleObject(handle, 0)
            return bool(res == WAIT_TIMEOUT)
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except (OverflowError, ValueError):
            return False
        else:
            return True


def compute_file_sha256(file_path: Path) -> str:
    """Compute lowercase hex SHA-256 hash of a file in 64KB chunks."""
    h = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().lower()


class CacheManager:
    """Manages local storage and hash validation of downloaded dependency archives."""

    def __init__(
        self,
        cache_dir: Optional[Union[str, Path]] = None,
        max_cache_bytes: int = 2 * 1024 * 1024 * 1024,
        lock_timeout: float = 30.0,
        liveness_check: Optional[Callable[[int], bool]] = None,
        poll_interval: float = 0.05,
        remote_lease_seconds: float = 300.0,
    ):
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.max_cache_bytes = max_cache_bytes
        self.lock_timeout = lock_timeout
        self.liveness_check = liveness_check or is_process_alive
        self.poll_interval = poll_interval
        self.remote_lease_seconds = remote_lease_seconds
        self.downloads_dir = self.cache_dir / "downloads"
        self.index_file = self.cache_dir / "index.json"
        self.lock_file = self.cache_dir / ".cache.lock"
        self._ensure_dirs()
        with self._locked():
            self._cleanup_orphans()

    def _ensure_dirs(self) -> None:
        self.downloads_dir.mkdir(parents=True, exist_ok=True)

    def is_temp_file_active(self, path: Path, max_age_seconds: float = 60.0) -> bool:
        """Check if a temporary or part file is active (held by a live acquisition or modified recently)."""
        name = path.name
        if not (name.startswith(".") or name.endswith((".part", ".tmp"))):
            return False

        try:
            mtime = path.stat().st_mtime
            if time.time() - mtime < max_age_seconds:
                return True
        except OSError:
            return True

        try:
            for lock_path in self.cache_dir.glob(".lock.*"):
                meta = self._read_lock_meta(lock_path)
                if not meta:
                    continue
                owner_pid = meta.get("pid")
                owner_host = meta.get("hostname")
                acquired_at = meta.get("acquired_at", 0.0)
                is_same_host = (owner_host == socket.gethostname())
                is_alive = False
                if is_same_host and isinstance(owner_pid, int):
                    is_alive = self.liveness_check(owner_pid)
                elif not is_same_host and isinstance(acquired_at, (int, float)):
                    is_alive = (time.time() - acquired_at <= self.remote_lease_seconds)

                if is_alive:
                    # 1. Match against explicit artifact_id in lock metadata
                    artifact_id = meta.get("artifact_id")
                    if artifact_id and artifact_id.lower() in name.lower():
                        return True
                    # 2. Robust prefix-stripping fallback (.lock.<safe_id>.<ver>.<var>)
                    if lock_path.name.startswith(".lock."):
                        raw_name = lock_path.name[6:]
                        parts = raw_name.split(".")
                        safe_id = parts[0] if parts else ""
                        if safe_id and safe_id.lower() in name.lower():
                            return True
        except OSError:
            pass

        return False

    def _cleanup_orphans(self, max_age_seconds: float = 60.0) -> None:
        if not self.downloads_dir.is_dir():
            return
        for path in self.downloads_dir.iterdir():
            try:
                if path.is_file() and (path.name.startswith(".") or path.name.endswith((".part", ".tmp"))):
                    if not self.is_temp_file_active(path, max_age_seconds=max_age_seconds):
                        path.unlink(missing_ok=True)
            except (OSError, PermissionError):
                pass

    @contextmanager
    def _locked(self, timeout: Optional[float] = None) -> Iterator[None]:
        with self._locked_file(self.lock_file, timeout=timeout):
            yield

    @contextmanager
    def artifact_locked(
        self,
        spec: DependencySpec,
        variant: ArtifactVariant,
        timeout: Optional[float] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> Iterator[None]:
        """Per-artifact lock enabling independent parallel downloads of different artifacts."""
        lock_path = self.get_artifact_lock_file(spec, variant)
        extra_meta = {
            "artifact_id": spec.id,
            "version": spec.version,
            "variant": variant.value,
            "cache_path": str(self.get_artifact_cache_path(spec, variant)),
        }
        with self._locked_file(lock_path, timeout=timeout, cancel=cancel, extra_meta=extra_meta):
            yield

    @contextmanager
    def artifact_lease(
        self,
        spec: DependencySpec,
        variant: ArtifactVariant,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> Iterator[Path]:
        """Keep a verified cache entry protected for the whole build."""
        with self.artifact_locked(spec, variant, cancel=cancel):
            path = self.get_artifact_cache_path(spec, variant)
            if not self.has_valid_artifact(spec, variant):
                raise ArtifactDownloadError(
                    f"Cached dependency disappeared or changed before build: {spec.id} ({variant.value})"
                )
            yield path

    def _is_active_artifact_lease(self, path: Path) -> bool:
        """Return whether a live artifact lock protects this exact cache path."""
        target = str(Path(path).absolute())
        try:
            for lock_path in self.cache_dir.glob(".lock.*"):
                meta = self._read_lock_meta(lock_path)
                if not meta or meta.get("cache_path") != target:
                    continue
                owner_host = meta.get("hostname")
                owner_pid = meta.get("pid")
                if owner_host == socket.gethostname() and isinstance(owner_pid, int):
                    return self.liveness_check(owner_pid)
                acquired_at = meta.get("acquired_at")
                return isinstance(acquired_at, (int, float)) and time.time() - acquired_at <= self.remote_lease_seconds
        except OSError:
            return True
        return False

    def get_artifact_lock_file(self, spec: DependencySpec, variant: ArtifactVariant) -> Path:
        """Deterministic per-artifact lock file path."""
        safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", spec.id)
        safe_version = re.sub(r"[^A-Za-z0-9._-]", "_", spec.version)
        return self.cache_dir / f".lock.{safe_id}.{safe_version}.{variant.value}"

    @contextmanager
    def _locked_file(
        self,
        lock_file: Path,
        timeout: Optional[float] = None,
        cancel: Optional[Callable[[], bool]] = None,
        extra_meta: Optional[Dict[str, Any]] = None,
    ) -> Iterator[None]:
        timeout_seconds = timeout if timeout is not None else self.lock_timeout
        deadline = time.monotonic() + timeout_seconds
        owner_token = uuid.uuid4().hex
        lock_meta = {
            "pid": os.getpid(),
            "token": owner_token,
            "hostname": socket.gethostname(),
            "acquired_at": time.time(),
        }
        if extra_meta:
            lock_meta.update(extra_meta)
        meta_bytes = json.dumps(lock_meta).encode("utf-8")

        acquired = False
        first_attempt = True
        while not acquired:
            if cancel and cancel():
                raise ArtifactDownloadError(f"Acquisition cancelled while waiting for lock on {lock_file.name}")
            if not first_attempt and time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for dependency cache lock on {lock_file}")
            first_attempt = False

            try:
                fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    os.write(fd, meta_bytes)
                finally:
                    os.close(fd)
                acquired = True
            except FileExistsError:
                existing_meta = self._read_lock_meta(lock_file)
                if existing_meta is None:
                    # Check if the lock file is empty/corrupt and older than 1.0 second
                    try:
                        st = lock_file.stat()
                        if time.time() - st.st_mtime > 1.0:
                            if self._atomic_reclaim(lock_file, expected_token=None):
                                continue
                    except OSError:
                        pass
                    time.sleep(self.poll_interval)
                    continue

                owner_pid = existing_meta.get("pid")
                owner_host = existing_meta.get("hostname")
                stale_token = existing_meta.get("token")
                acquired_at = existing_meta.get("acquired_at", 0.0)

                is_same_host = (owner_host == socket.gethostname())
                is_alive = False
                if is_same_host and isinstance(owner_pid, int):
                    is_alive = self.liveness_check(owner_pid)
                elif not is_same_host and isinstance(acquired_at, (int, float)):
                    is_alive = (time.time() - acquired_at <= self.remote_lease_seconds)

                if not is_alive and stale_token is not None:
                    if self._atomic_reclaim(lock_file, expected_token=stale_token):
                        continue

                time.sleep(self.poll_interval)

        try:
            yield
        finally:
            try:
                current = self._read_lock_meta(lock_file)
                if current and current.get("token") == owner_token:
                    for attempt in range(5):
                        try:
                            lock_file.unlink(missing_ok=True)
                            break
                        except OSError:
                            if attempt == 4:
                                logger.debug(f"Failed to release lock on {lock_file} after retries")
                            time.sleep(0.02)
                else:
                    logger.warning(f"Lock release skipped for {lock_file.name}: replaced by another owner")
            except OSError as e:
                logger.debug(f"Failed to release lock on {lock_file}: {e}")

    def _atomic_reclaim(self, lock_file: Optional[Path] = None, expected_token: Optional[str] = None) -> bool:
        """Atomically reclaim a stale or corrupted lock file without racing other processes."""
        target = lock_file if lock_file is not None else self.lock_file
        reclaim_path = self.cache_dir / f".reclaim.{uuid.uuid4().hex}.tmp"
        try:
            os.replace(target, reclaim_path)
        except (FileNotFoundError, OSError):
            return False

        try:
            raw = reclaim_path.read_bytes()
            meta = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:
            meta = None

        actual_token = meta.get("token") if isinstance(meta, dict) else None
        if expected_token is None or actual_token == expected_token:
            reclaim_path.unlink(missing_ok=True)
            logger.info(f"Atomically reclaimed stale lock {target.name}")
            return True
        else:
            try:
                os.replace(reclaim_path, target)
            except OSError:
                reclaim_path.unlink(missing_ok=True)
            return False

    def _read_lock_meta(self, lock_file: Optional[Path] = None) -> Optional[Dict[str, Any]]:
        target = lock_file if lock_file is not None else self.lock_file
        try:
            raw = target.read_bytes()
            if not raw:
                return None
            data = json.loads(raw.decode("utf-8"))
            if isinstance(data, dict):
                return data
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return None

    def acquire_artifact(
        self,
        spec: DependencySpec,
        variant: ArtifactVariant,
        downloader: Any,
        timeout: Optional[float] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> Path:
        """Coordinate check/download/verify/publish lifecycle per artifact with cache re-check."""
        # 1. Fast pre-check before acquiring artifact lock
        cached = self.get_cached_path_if_valid(spec, variant)
        if cached:
            return cached

        # 2. Acquire per-artifact lock (other artifacts proceed concurrently)
        with self.artifact_locked(spec, variant, timeout=timeout, cancel=cancel):
            # 3. CRITICAL: Re-check cache after acquiring ownership (concurrent miss handling)
            cached_after_lock = self.get_cached_path_if_valid(spec, variant)
            if cached_after_lock:
                logger.info(f"Artifact {spec.id} ({variant.value}) resolved from concurrent cache acquisition")
                return cached_after_lock

            if cancel and cancel():
                raise ArtifactDownloadError(f"Acquisition cancelled for {spec.id} ({variant.value})")

            artifact = spec.get_artifact(variant)
            if not artifact:
                raise ChecksumMismatchError(f"No artifact definition for {spec.id} variant {variant.value}")

            target_path = self.get_artifact_cache_path(spec, variant)
            try:
                downloader.download_artifact(artifact, target_path, cancel=cancel)
            except TypeError:
                downloader.download_artifact(artifact, target_path)
            self.put_artifact(spec, variant, target_path)
            return target_path

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
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+~-]*", value) or ".." in value:
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
            # Validation is intentionally read-only.  Publication uses the
            # cache lock and may replace this pathname concurrently; deleting
            # it here could remove a valid replacement that was published
            # after this verifier opened the old bytes.
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
        files = [
            path for path in self.downloads_dir.iterdir()
            if path.is_file() and not path.name.startswith(".") and not path.name.endswith((".part", ".tmp"))
        ]
        total = sum(path.stat().st_size for path in files)
        pruned_any = False
        for path in sorted(files, key=lambda item: item.stat().st_mtime):
            if total <= self.max_cache_bytes:
                break
            if self._is_active_artifact_lease(path):
                continue
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total -= size
            pruned_any = True

        if pruned_any:
            index = self._load_index()
            if index:
                remaining_paths = {p.resolve() for p in self.downloads_dir.iterdir() if p.is_file()}
                pruned_index = {
                    k: v for k, v in index.items()
                    if isinstance(v, dict) and Path(v.get("path", "")).resolve() in remaining_paths
                }
                if len(pruned_index) != len(index):
                    self._save_index(pruned_index)

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

    def clear_cache(self, keep_active_downloads: bool = True) -> None:
        """Remove all cached dependency files and reset index while protecting active transfers."""
        with self._locked():
            if self.downloads_dir.is_dir():
                for f in self.downloads_dir.iterdir():
                    if f.is_file() and not f.is_symlink():
                        if self._is_active_artifact_lease(f):
                            continue
                        if keep_active_downloads and self.is_temp_file_active(f):
                            continue
                        f.unlink(missing_ok=True)
            self.index_file.unlink(missing_ok=True)
