"""Atomic, revision-aware storage for public configuration JSON."""

from pathlib import Path
import json
import os
import shutil
import tempfile
from typing import Any, Optional

from macloader.domain.configuration import UserConfiguration


class ConfigurationStoreError(ValueError):
    """A configuration could not be safely stored or loaded."""


class ConfigurationStore:
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
        current: Optional[UserConfiguration] = None
        if target.exists():
            current = self.load(configuration.configuration_id)
        if expected_revision is not None and (current is None or current.revision != expected_revision):
            raise ConfigurationStoreError("configuration revision conflict")
        if current is not None and configuration.revision <= current.revision:
            raise ConfigurationStoreError("configuration revision must increase on replacement")
        payload = configuration.to_json(indent=2) + "\n"
        fd, temporary_name = tempfile.mkstemp(prefix=f".{configuration.configuration_id}.", suffix=".tmp", dir=self.root)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            temporary.write_text(payload, encoding="utf-8")
            if target.exists():
                backup = target.with_suffix(".json.bak")
                if backup.exists() and backup.is_symlink():
                    raise ConfigurationStoreError("configuration backup must not be a symlink")
                shutil.copy2(target, backup)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

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
