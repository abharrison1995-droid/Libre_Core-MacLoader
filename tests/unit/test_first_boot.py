"""Boot reports are explicit, bound, and never support/install promotion."""
from dataclasses import replace
from pathlib import Path
import pytest
from macloader.autoloader.first_boot import CHECKPOINTS, checkpoint_binding, instructions, next_checkpoint, record_checkpoint
from macloader.autoloader.models import ActionKind, Stage
from macloader.database.loader import get_database
from tests.unit.test_usb_map import evidence


def test_route_proof_and_all_checkpoints_keep_experimental() -> None:
    campaign = next(iter(get_database().campaigns.values()))
    procedure = instructions(campaign, evidence())
    assert 'left USB-A beside HDMI' in procedure and 'F12' in procedure
    assert 'SS01' not in procedure and 'unproven' in procedure and 'Do not erase' in procedure
    results: dict[str, str] = {}
    for name, statement in CHECKPOINTS:
        action = next_checkpoint(results, procedure)
        assert action.kind == ActionKind.HUMAN and statement in action.message
        record_checkpoint(results, 'Confirmed')
        assert results[name] == 'confirmed'
    action = next_checkpoint(results, procedure)
    assert action.kind == ActionKind.COMPLETE and 'not authorized' in action.message
    assert campaign.support_state == 'EXPERIMENTAL'
    with pytest.raises(ValueError):
        record_checkpoint(results, 'Confirmed')
    missing = replace(evidence(), observations=tuple(o for o in evidence().observations if o.logical_port != campaign.profile.usb.first_install_route))
    with pytest.raises(ValueError, match='unproven'):
        instructions(campaign, missing)


def test_failure_resume_and_binding_change() -> None:
    results: dict[str, str] = {}
    record_checkpoint(results, 'Confirmed')
    record_checkpoint(results, 'Failed or not reached')
    assert next_checkpoint(results, 'procedure').code == 'FIRST_BOOT_FAILED'
    record_checkpoint(results, 'Repeat this checkpoint')
    assert results == {'picker': 'confirmed'}
    assert next_checkpoint(results, 'procedure').code == 'FIRST_BOOT_CHECKPOINT'
    assert checkpoint_binding('a', 'b', 'c') != checkpoint_binding('a', 'b', 'd')


def test_service_reports_and_invalidation_do_not_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.unit.test_autoloader_build import ready
    from macloader.domain.contracts import canonical_json_digest
    service = ready(tmp_path)
    assert service.session is not None and service.configuration is not None
    for kind in ('tools', 'dependencies', 'efi', 'recovery', 'media_source'):
        service._record_artifact(kind, digest='a' * 64)
    service.media.source = tmp_path
    service._record_artifact('media_write', digest='b' * 64, readback='complete', eject='complete', purpose='picker-recovery-smoke-only')
    # Explicit test seam replaces physical publication verification; no device written.
    monkeypatch.setattr(service, '_prepare_media', lambda: None)
    assert service.advance_until_blocked().stage == Stage.FIRST_BOOT
    for _ in CHECKPOINTS:
        action = service.perform_choice('Confirmed')
    assert action.kind == ActionKind.COMPLETE
    assert service.configuration.recovery is None
    assert 'install' not in service.session.artifacts
    assert canonical_json_digest(service.session.checkpoints)
    updated = replace(service.configuration, observations=service.configuration.observations[:-1])
    service.save_configuration(updated)
    assert not service.session.checkpoints and 'media_write' not in service.session.artifacts
