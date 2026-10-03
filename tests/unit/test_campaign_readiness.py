"""Readiness cannot turn fixtures or unknown checks into a boot permission."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import pytest
from macloader.autoloader.models import Stage
from macloader.autoloader.readiness import EXPECTED_JOBS, ReadinessGate, campaign_readiness, hosted_ci_gate
from macloader.autoloader.service import AutoloaderService
from macloader.detection.reference_fixture import reference_fixture
from macloader.configuration.campaign_match import match_campaign
from tests.unit.test_autoloader import candidate


def test_unknown_touch_confirmation_is_scoped_and_cannot_override_observed_touch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = candidate()
    snapshot.displays[0].touch_capability = None
    service = AutoloaderService(root=tmp_path)
    action = service.start(snapshot, private_material='test')
    assert action.code == 'PANEL_TOUCH_UNKNOWN'
    monkeypatch.setattr(service, 'advance_until_blocked', lambda **kwargs: service.next_action())
    assert service.perform_choice('Confirm non-touch panel').stage == Stage.ACPI
    assert service.configuration is not None
    restarted = AutoloaderService(root=tmp_path)
    assert restarted.start(snapshot, private_material='test').stage == Stage.ACPI
    changed = candidate()
    changed.displays[0].touch_capability = True
    assert restarted.start(changed, private_material='test').code == 'HARDWARE_MISMATCH'
    assert restarted.configuration is not None
    assert not any(c.action == 'human-confirmed' for c in restarted.configuration.confirmations)
    changed.displays[0].touch_capability = None
    changed.displays[0].resolution = '2560x1440'
    assert 'panel.resolution' in match_campaign(changed, service.workflow.orchestrator.db, service.configuration).mismatches


def test_unsure_preserves_unknown_and_fixture_cannot_ready(tmp_path: Path) -> None:
    snapshot = candidate()
    snapshot.displays[0].touch_capability = None
    service = AutoloaderService(root=tmp_path)
    service.start(snapshot, private_material='test')
    assert service.perform_choice('Touch panel or unsure').code == 'PANEL_SCOPE_UNPROVEN'
    report = campaign_readiness(service, ReadinessGate('Known green', True, 'test seam'))
    assert not report['ready_for_first_boot'] and report['installation_authorized'] is False
    text = json.dumps(report)
    assert str(tmp_path) not in text and 'configuration_id' not in text
    assert report['support_state'] == 'EXPERIMENTAL'


def test_supplied_capture_fixture_scrubs_identifiers_and_cannot_capture_host(tmp_path: Path) -> None:
    source = candidate()
    with pytest.raises(ValueError, match='supplied live'):
        reference_fixture(source)
    # Synthetic simulation of a supplied capture, used only for sanitizer testing.
    source.raw_evidence = {'os': 'linux', 'inventory_status': {'pci': True}, 'private_path': '/home/private', 'raw_serial': 'PRIVATE-NOT-PUBLISHABLE'}
    source.serial_number = 'PRIVATE-NOT-PUBLISHABLE'
    source.uuid = '12345678-1234-1234-1234-123456789abc'
    source.storage[0].serial = 'SECRET-STORAGE'
    source.wifi[0].mac_address = '11:22:33:44:55:66'
    sanitized = reference_fixture(source)
    serialized = sanitized.to_json()
    assert all(value not in serialized for value in ('PRIVATE-NOT-PUBLISHABLE', '/home/private', 'SECRET-STORAGE', '11:22:33:44:55:66', source.uuid))
    assert sanitized.raw_evidence['fixture_is_not_evidence'] is True
    assert sanitized.machine_type == '20L8' and sanitized.storage[0].model == source.storage[0].model
    service = AutoloaderService(root=tmp_path)
    service.start(sanitized, private_material='test')
    assert service.advance_until_blocked().code == 'SYNTHETIC_CAPTURE_DISABLED'


def test_ci_gate_uses_exact_actual_jobs_and_rejects_dirty_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commit = 'a' * 40
    dirty = False
    missing = False
    def run(args, **kwargs):  # type: ignore[no-untyped-def]
        if args[0] == 'git':
            output = commit if args[1] == 'rev-parse' else (' M macloader/foo.py' if dirty else ' M docs/PROJECT_STATUS.md')
        elif 'list' in args:
            output = json.dumps([dict(headSha=commit, status='completed', conclusion='success', databaseId=1)])
        else:
            jobs = list(EXPECTED_JOBS)
            output = json.dumps({'jobs': [dict(name=name, conclusion='success') for name in (jobs[:-1] if missing else jobs)]})
        return subprocess.CompletedProcess(args, 0, output, '')
    monkeypatch.setattr(subprocess, 'run', run)
    assert hosted_ci_gate(tmp_path).passed
    missing = True
    assert not hosted_ci_gate(tmp_path).passed
    dirty = True
    assert not hosted_ci_gate(tmp_path).passed


def test_placeholder_dmi_identity_and_broken_inputs_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = candidate()
    snapshot.uuid = '00000000-0000-0000-0000-000000000000'
    snapshot.serial_number = 'SYNTHETIC-STABLE-SERIAL'
    assert AutoloaderService._private_machine_material(snapshot) == 'SYNTHETIC-STABLE-SERIAL'
    snapshot.uuid = 'FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF'
    assert AutoloaderService._private_machine_material(snapshot) == 'SYNTHETIC-STABLE-SERIAL'
    snapshot.uuid = 'not-a-uuid'
    assert AutoloaderService._private_machine_material(snapshot) == 'SYNTHETIC-STABLE-SERIAL'
    service = AutoloaderService(root=tmp_path)
    service.start(candidate(), private_material='test')
    monkeypatch.setattr(service, '_calculate_next_action', lambda: (_ for _ in ()).throw(OSError('private-path')))
    assert service.next_action().code == 'GUIDED_STATE_INVALID'
    assert 'private-path' not in service.next_action().message
    assert service.session is not None
    service.session.actions.append(dict(stage='write', destructive='may-have-occurred'))
    assert 'may already have occurred' in service.failure_message()
    assert service.public_status()['destructive_operation_may_have_occurred'] is True
