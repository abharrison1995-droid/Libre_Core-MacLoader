"""Private guided store protects bindings and refuses stale revisions."""
from dataclasses import replace
import json
import os
from pathlib import Path
import uuid

import pytest

from macloader.autoloader.models import AutoloaderSession
from macloader.autoloader.store import AmbiguousSessionError, AutoloaderSessionStore, MachineIdentityUnavailable
from macloader.workflow.service import WorkflowService


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
    with pytest.raises(AmbiguousSessionError, match="Multiple"):
        store.find(binding, "campaign")


def test_unusable_material_is_a_distinct_identity_error(tmp_path: Path) -> None:
    store = AutoloaderSessionStore(tmp_path)
    for material in ("", "[REDACTED-SERIAL]"):
        with pytest.raises(MachineIdentityUnavailable):
            store.machine_binding(material)


def _write_raw_session(root: Path, name: str, content: str) -> None:
    # Private writer so the Windows ACL check passes and only the content is bad.
    WorkflowService._write_private_json(root / name, content)


def test_corrupt_file_is_skipped_and_valid_session_still_found(tmp_path: Path) -> None:
    store = AutoloaderSessionStore(tmp_path)
    binding = store.machine_binding("test")
    session = AutoloaderSession("campaign", "a" * 64, binding, str(uuid.uuid4()), "snapshot")
    store.save(session, None)
    _write_raw_session(tmp_path, "broken.session.json", "{not json")
    discovery = store.discover(binding, "campaign")
    assert discovery.session == session
    assert [item.name for item in discovery.skipped] == ["broken.session.json"]
    assert discovery.skipped[0].might_match
    assert store.find(binding, "campaign") == session


def test_unknown_key_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    store = AutoloaderSessionStore(tmp_path)
    binding = store.machine_binding("test")
    record = AutoloaderSession("campaign", "a" * 64, binding, str(uuid.uuid4()), "snapshot").to_dict()
    record["unexpected_field"] = "value"
    _write_raw_session(tmp_path, "extra.session.json", json.dumps(record))
    discovery = store.discover(binding, "campaign")
    assert discovery.session is None
    assert [(item.name, item.might_match) for item in discovery.skipped] == [("extra.session.json", True)]


def test_skipped_file_for_another_machine_or_campaign_cannot_match(tmp_path: Path) -> None:
    store = AutoloaderSessionStore(tmp_path)
    binding = store.machine_binding("test")
    other_machine = AutoloaderSession("campaign", "a" * 64, "b" * 64, str(uuid.uuid4()), "snapshot").to_dict()
    other_campaign = AutoloaderSession("other-campaign", "a" * 64, binding, str(uuid.uuid4()), "snapshot").to_dict()
    for record in (other_machine, other_campaign):
        record["revision"] = "not-a-number"
    _write_raw_session(tmp_path, "a.session.json", json.dumps(other_machine))
    _write_raw_session(tmp_path, "b.session.json", json.dumps(other_campaign))
    _write_raw_session(tmp_path, "c.session.json", json.dumps(["not", "a", "mapping"]))
    discovery = store.discover(binding, "campaign")
    assert discovery.session is None
    assert [(item.name, item.might_match) for item in discovery.skipped] == [
        ("a.session.json", False), ("b.session.json", False), ("c.session.json", True)]


def test_session_schema_and_key_corruption_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        AutoloaderSession.from_dict({"schema_version": "old"})
    store = AutoloaderSessionStore(tmp_path)
    store.machine_binding("test")
    key = tmp_path / "machine-key.json"
    key.write_text(json.dumps({"key": "00"}))
    with pytest.raises(ValueError, match="key"):
        store.machine_binding("test")
