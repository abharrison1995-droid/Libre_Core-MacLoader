"""Private session persistence, keyed machine binding and atomic revisions."""
import hashlib
import hmac
import json
from pathlib import Path
import secrets
from typing import Optional
import uuid

from macloader.autoloader.models import AutoloaderSession
from macloader.configuration.store import ConfigurationStore
from macloader.workflow.service import WorkflowService


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
            raise ValueError("A private stable machine binding is unavailable")
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
        matches = []
        for path in self.root.glob("*.session.json"):
            session = AutoloaderSession.from_dict(WorkflowService._read_private_json(path, "guided session"))
            if session.machine_binding == binding and session.campaign_id == campaign_id:
                matches.append(session)
        if len(matches) > 1:
            raise ValueError("Multiple compatible guided sessions need a deliberate selection")
        return matches[0] if matches else None

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
