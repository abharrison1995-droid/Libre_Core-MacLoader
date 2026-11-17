"""Private session persistence, keyed machine binding and atomic revisions."""
from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import secrets
from typing import Any, Optional
import uuid

from macloader.autoloader.models import AutoloaderSession
from macloader.configuration.store import ConfigurationStore
from macloader.workflow.service import WorkflowService


class MachineIdentityUnavailable(ValueError):
    """No usable private machine identity (UUID, serial or OS id) exists."""


class AmbiguousSessionError(ValueError):
    """More than one valid saved session matches this machine and campaign."""


# Errors a malformed session file raises while it is read or parsed.
# json.JSONDecodeError is a ValueError, and the private reader wraps it anyway.
_UNREADABLE_SESSION_ERRORS = (ValueError, TypeError, KeyError, AttributeError)


@dataclass(frozen=True)
class SkippedSessionFile:
    """A session file discovery could not read; ``name`` is the file name only."""

    name: str
    # False only when the readable part proves another machine or campaign.
    might_match: bool


@dataclass(frozen=True)
class SessionDiscovery:
    session: Optional[AutoloaderSession]
    skipped: tuple[SkippedSessionFile, ...] = ()


class AutoloaderSessionStore:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()
        WorkflowService._ensure_private_directory(self.root)
        self._locks = ConfigurationStore(self.root)

    def _path(self, session_id: str) -> Path:
        if str(uuid.UUID(session_id)) != session_id:
            raise ValueError("Invalid session reference")
        return self.root / f"{session_id}.session.json"

    def machine_binding(self, private_material: str) -> str:
        if not private_material or "REDACTED" in private_material.upper():
            raise MachineIdentityUnavailable("A private stable machine binding is unavailable")
        with self._locks._transaction_lock("machine-key"):
            path = self.root / "machine-key.json"
            if path.exists():
                key_data = WorkflowService._read_private_json(path, "machine binding key")
                key = bytes.fromhex(key_data["key"])
            else:
                key = secrets.token_bytes(32)
                WorkflowService._write_private_json(path, json.dumps({"key": key.hex()}))
            if len(key) != 32:
                raise ValueError("Invalid machine binding key")
        return hmac.new(key, ("macloader-machine-v1:" + private_material).encode(), hashlib.sha256).hexdigest()

    def find(self, binding: str, campaign_id: str) -> Optional[AutoloaderSession]:
        return self.discover(binding, campaign_id).session

    def discover(self, binding: str, campaign_id: str) -> SessionDiscovery:
        """Find the one matching session; unreadable files are skipped and named."""
        matches = []
        skipped = []
        for path in sorted(self.root.glob("*.session.json")):
            data: Any = None
            try:
                data = WorkflowService._read_private_json(path, "guided session")
                session = AutoloaderSession.from_dict(data)
            except _UNREADABLE_SESSION_ERRORS:
                skipped.append(SkippedSessionFile(path.name, self._might_match(data, binding, campaign_id)))
                continue
            if session.machine_binding == binding and session.campaign_id == campaign_id:
                matches.append(session)
        if len(matches) > 1:
            raise AmbiguousSessionError("Multiple compatible guided sessions need a deliberate selection")
        return SessionDiscovery(matches[0] if matches else None, tuple(skipped))

    @staticmethod
    def _might_match(data: Any, binding: str, campaign_id: str) -> bool:
        """An unreadable file may be ours unless its readable fields prove otherwise."""
        if not isinstance(data, dict):
            return True
        stored_binding, stored_campaign = data.get("machine_binding"), data.get("campaign_id")
        if isinstance(stored_binding, str) and stored_binding != binding:
            return False
        if isinstance(stored_campaign, str) and stored_campaign != campaign_id:
            return False
        return True

    def save(self, session: AutoloaderSession, expected_revision: Optional[int]) -> None:
        target = self._path(session.session_id)
        with self._locks._transaction_lock(session.session_id):
            current = self.load(session.session_id) if target.exists() else None
            if current is None:
                if expected_revision is not None or session.revision != 0:
                    raise ValueError("Guided session revision does not match")
            elif current.revision != expected_revision or session.revision != current.revision + 1:
                raise ValueError("Guided session changed; reload before continuing")
            WorkflowService._write_private_json(target, json.dumps(session.to_dict(), sort_keys=True))

    def load(self, session_id: str) -> AutoloaderSession:
        return AutoloaderSession.from_dict(WorkflowService._read_private_json(self._path(session_id), "guided session"))

    def save_snapshot(self, session: AutoloaderSession, snapshot_json: str) -> None:
        WorkflowService._write_private_json(self.root / f"{session.session_id}.snapshot.json", snapshot_json)
