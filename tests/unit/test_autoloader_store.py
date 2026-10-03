"""Private guided store protects bindings and refuses stale revisions."""
from dataclasses import replace
import json
import os
from pathlib import Path
import uuid

import pytest

from macloader.autoloader.models import AutoloaderSession
from macloader.autoloader.store import AutoloaderSessionStore


def test_binding_key_and_records_private_cas(tmp_path: Path) -> None:
    store = AutoloaderSessionStore(tmp_path)
    binding = store.machine_binding("private-unique-material")
    assert store.machine_binding("private-unique-material") == binding
    assert store.machine_binding("different") != binding
    session = AutoloaderSession("campaign", "a" * 64, binding, str(uuid.uuid4()), "snapshot")
    store.save(session, None)
    assert store.find(binding, "campaign") == session
    with pytest.raises(ValueError, match="changed"):
        store.save(session, 0)
    revised = replace(session, revision=1)
    store.save(revised, 0)
    with pytest.raises(ValueError, match="changed"):
        store.save(replace(session, revision=1), 0)
    if os.name != "nt":
        assert (tmp_path / "machine-key.json").stat().st_mode & 0o077 == 0
    assert "private-unique-material" not in (tmp_path / "machine-key.json").read_text()
    with pytest.raises(ValueError):
        store.machine_binding("[REDACTED-SERIAL]")
    with pytest.raises(ValueError):
        store.load("../bad")


def test_ambiguous_sessions_fail_closed(tmp_path: Path) -> None:
    store = AutoloaderSessionStore(tmp_path)
    binding = store.machine_binding("test")
    for _ in range(2):
        store.save(AutoloaderSession("campaign", "a" * 64, binding, str(uuid.uuid4()), "snapshot"), None)
    with pytest.raises(ValueError, match="Multiple"):
        store.find(binding, "campaign")


def test_session_schema_and_key_corruption_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        AutoloaderSession.from_dict({"schema_version": "old"})
    store = AutoloaderSessionStore(tmp_path)
    store.machine_binding("test")
    key = tmp_path / "machine-key.json"
    key.write_text(json.dumps({"key": "00"}))
    with pytest.raises(ValueError, match="key"):
        store.machine_binding("test")
