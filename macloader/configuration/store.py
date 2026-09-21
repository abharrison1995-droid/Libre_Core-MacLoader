"""Atomic, revision-aware storage for public configuration JSON."""

from pathlib import Path
from contextlib import contextmanager
import json
import os
import shutil
import tempfile
import threading
from typing import Any, Iterator, Optional

from macloader.domain.configuration import UserConfiguration


class ConfigurationStoreError(ValueError):
    """A configuration could not be safely stored or loaded."""


class ConfigurationStore:
    _thread_locks: dict[str, threading.RLock] = {}
    _thread_locks_guard = threading.Lock()

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._assert_safe_path(self.root)

    def path_for(self, configuration_id: str) -> Path:
        if not configuration_id or Path(configuration_id).name != configuration_id or configuration_id in {".", ".."}:
            raise ConfigurationStoreError("configuration ID is not a safe file name")
        return self.root / f"{configuration_id}.json"

    def save(self, configuration: UserConfiguration, expected_revision: Optional[int] = None) -> Path:
        target = self.path_for(configuration.configuration_id)
        self._assert_safe_path(target)
        with self._transaction_lock(configuration.configuration_id):
            # The revision check, backup, and publication are one transaction.
            # A lock only around os.replace() still permits two stale readers
            # to validate against the same base revision.
            current: Optional[UserConfiguration] = None
            if target.exists():
                current = self.load(configuration.configuration_id)
            actual_revision = current.revision if current is not None else 0
            if current is not None and expected_revision is None:
                raise ConfigurationStoreError(
                    "configuration replacement requires the loaded base revision"
                )
            if expected_revision is not None and actual_revision != expected_revision:
                raise ConfigurationStoreError("configuration revision conflict")
            if current is not None and configuration.revision <= current.revision:
                raise ConfigurationStoreError("configuration revision must increase on replacement")
            payload = configuration.to_json(indent=2) + "\n"
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{configuration.configuration_id}.", suffix=".tmp", dir=self.root
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                if current is not None:
                    backup = target.with_suffix(".json.bak")
                    if backup.exists() and backup.is_symlink():
                        raise ConfigurationStoreError("configuration backup must not be a symlink")
                    backup_tmp = backup.with_name(f".{backup.name}.tmp")
                    shutil.copy2(target, backup_tmp)
                    os.replace(backup_tmp, backup)
                os.replace(temporary, target)
                self._fsync_directory()
            finally:
                temporary.unlink(missing_ok=True)
        return target

    @contextmanager
    def _transaction_lock(self, configuration_id: str) -> Iterator[None]:
        """Serialize a configuration transaction in-process and across processes."""
        key = str(self.root.absolute() / configuration_id)
        with self._thread_locks_guard:
            lock = self._thread_locks.setdefault(key, threading.RLock())
        with lock:
            lock_path = self.root / f".{configuration_id}.lock"
            self._assert_safe_path(lock_path)
            with lock_path.open("a+", encoding="utf-8") as handle:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if os.name == "nt":
                        import msvcrt
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _fsync_directory(self) -> None:
        if os.name == "nt":
            return
        try:
            fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            raise ConfigurationStoreError(f"unable to make configuration publication durable: {exc}") from exc

    def load(self, configuration_id: str) -> UserConfiguration:
        path = self.path_for(configuration_id)
        self._assert_safe_path(path)
        if not path.is_file() or path.is_symlink():
            raise ConfigurationStoreError("configuration file is missing or unsafe")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return UserConfiguration.from_dict(data)
        except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            raise ConfigurationStoreError(f"invalid stored configuration: {exc}") from exc

    def export_public(self, configuration: UserConfiguration) -> dict[str, Any]:
        """Return a shareable view; identity storage references are private too."""
        data = configuration.to_dict()
        data["identity_ref"] = None
        public_evidence = []
        for record in data["evidence"]:
            summary = dict(record)
            summary["private_ref"] = "<private>"
            # A public export is a review summary, never proof that can reopen
            # a physical gate after import.
            summary["completeness"] = "missing"
            summary["physical_port_evidence"] = False
            summary["unresolved_checks"] = ["private evidence was omitted from public export"]
            public_evidence.append(summary)
        data["evidence"] = public_evidence
        return data

    @staticmethod
    def _assert_safe_path(path: Path) -> None:
        absolute = path.absolute()
        for ancestor in (absolute, *absolute.parents):
            if ancestor.is_symlink():
                raise ConfigurationStoreError("configuration storage path contains a symlink")
