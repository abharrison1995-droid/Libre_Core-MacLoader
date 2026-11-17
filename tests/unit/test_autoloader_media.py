"""Disposable tests exercise production contracts; never physical qualification."""
import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Callable
import pytest

from macloader.autoloader.media import GuidedMediaService
from macloader.database.loader import get_database
from macloader.domain.contracts import BuildManifest, canonical_json_digest
from macloader.evidence.acpi_capture import CaptureError
from macloader.recovery.acquirer import RecoveryAcquirer
from macloader.recovery.service import RecoveryService
from macloader.recovery.smoke import VerifiedSmokeRecovery
from macloader.removable.adapters import AdapterStatus
from macloader.removable.writer import DisposableImageAdapter, MediaBindings, RemovableMediaWriter, UnsafeRemovableTarget
from tests.unit.test_recovery_smoke import client, fixture

if TYPE_CHECKING:
    from macloader.autoloader.service import AutoloaderService


def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GuidedMediaService:
    import macloader.recovery.acquirer as module
    data = fixture()
    monkeypatch.setattr(module, '_APPLE_EFI_ROM_PUBLIC_KEY', int(data['test_public_key'], 16))
    efi = tmp_path / 'efi'
    for name in ('EFI/BOOT/BOOTx64.efi', 'EFI/OC/OpenCore.efi'):
        p = efi / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'synthetic only')
    manifest = BuildManifest(schema_version='0.1', target_model='lenovo-thinkpad-t480s', target_macos='15.0', build_digest='e' * 64, artifact_lock_digest='d' * 64,
        toolchain_digest='t' * 64, validation_report='VALID', output_digest=RemovableMediaWriter._efi_payload_digest(efi))
    # BuildManifest SHA contracts use lowercase hex.
    manifest = replace(manifest, toolchain_digest='a' * 64)
    (efi / 'manifest.json').write_text(json.dumps(manifest.to_dict()))
    campaign = next(iter(get_database().campaigns.values()))
    binding = canonical_json_digest({'configuration': 'c' * 64, 'campaign': campaign.digest, 'efi': manifest.build_digest, 'purpose': 'first-boot-only'})
    smoke = client(monkeypatch)
    candidate = smoke.discover(RecoveryService().target())
    def download(url: str, path: Path) -> None:
        path.write_bytes(base64.b64decode(data['chunklist_payload'] if 'chunklist' in url else data['image_payload']))
    record = smoke.acquire(candidate, binding, tmp_path / 'recovery', acquirer=RecoveryAcquirer(transport=download))
    media = GuidedMediaService()
    media.prepare(tmp_path / 'media', efi, tmp_path / 'recovery', manifest, record, smoke, 'c' * 64, campaign.digest)
    assert media.bindings is not None and media.bindings.complete and media.bindings.recovery_lock_digest == ''
    return media


class DisposableWindows:
    guided_eject_available = True
    status = AdapterStatus('windows', True, True, 'TEST ONLY disposable adapter')
    def __init__(self, root: Path):
        self.image = DisposableImageAdapter(root)
        self.devices = [self.image.create_mock_device()]
        self.ejected = False
    def enumerate(self):  # type: ignore[no-untyped-def]
        return self.devices
    def writer(self):  # type: ignore[no-untyped-def]
        return RemovableMediaWriter(self.image.write, self.enumerate, readback_verifier=self.image.verify_readback, invalidator=self.image.invalidate)
    def safe_eject(self, device):  # type: ignore[no-untyped-def]
        self.ejected = True


def test_smoke_media_layout_readback_eject_and_fresh_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = prepared(tmp_path, monkeypatch)
    adapter = DisposableWindows(tmp_path / 'disposable')
    adapter.devices.append(replace(adapter.devices[0], device_id='internal', is_system_disk=True))
    media.adapter = adapter
    labels = media.discover()
    assert len(labels) == 1 and 'DISPOSABLE001' not in labels[0]
    media.select(labels[0])
    marked: list[bool] = []
    plan = media.write(lambda: False, lambda: marked.append(True))
    assert adapter.ejected and marked == [True] and adapter.image.writes_performed == 1
    assert (adapter.image.target_path / 'com.apple.recovery.boot/BaseSystem.dmg').is_file()
    assert plan.bindings.purpose == 'picker-recovery-smoke-only'
    media.select(media.discover()[0])
    adapter.devices[0] = replace(adapter.devices[0], serial='swapped')
    with pytest.raises(CaptureError, match='may have been erased'):
        media.write(lambda: False, lambda: None)
    assert adapter.image.writes_performed == 1


def test_stale_sources_unsigned_corruption_and_unqualified_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = prepared(tmp_path, monkeypatch)
    assert not media.qualified
    with pytest.raises(CaptureError, match='qualified Windows'):
        media.discover()
    assert media.source is not None and media.bindings is not None
    (media.source / 'com.apple.recovery.boot/BaseSystem.dmg').write_bytes(b'corrupt')
    with pytest.raises(UnsafeRemovableTarget):
        RemovableMediaWriter._validate_published_artifacts(media.source, media.bindings, require_all=True)
    assert not replace(media.bindings, purpose='qualification').complete
    assert not replace(media.bindings, recovery_lock_digest='f' * 64).complete
    raw = VerifiedSmokeRecovery.from_dict(json.loads((media.source / 'Recovery/smoke.json').read_text())).to_dict()
    raw['verified_chunks'] = True
    with pytest.raises(ValueError):
        VerifiedSmokeRecovery.from_dict(raw)


def test_readback_failure_invalidates_and_clears_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = prepared(tmp_path, monkeypatch)
    adapter = DisposableWindows(tmp_path / 'disposable')
    adapter.image.corrupt_readback = True
    media.adapter = adapter
    media.select(media.discover()[0])
    with pytest.raises(CaptureError, match='not ready'):
        media.write(lambda: False, lambda: None)
    assert media.selected is None and not adapter.ejected
    assert (adapter.image.target_path / 'WRITE-INCOMPLETE').exists()


RESTART_CHOICE = 'I will reuse or discard that USB; start again'


def _at_media_stage(tmp_path: Path, *extra: str) -> 'AutoloaderService':
    """A session whose sources are prepared; extra artifacts are recorded on top."""
    from tests.unit.test_autoloader_build import ready
    service = ready(tmp_path)
    for kind in ('tools', 'dependencies', 'efi', 'recovery', 'media_source'):
        service._record_artifact(kind, digest='a' * 64)
    if 'media_attempt' in extra:
        service._record_artifact('media_attempt', destructive='may-have-occurred')
    if 'media_write' in extra:
        service._record_artifact('media_write', digest='b' * 64, readback='complete', eject='complete', purpose='picker-recovery-smoke-only')
    return service


def _relaunch(tmp_path: Path) -> 'AutoloaderService':
    from macloader.autoloader.service import AutoloaderService
    from tests.unit.test_autoloader import candidate
    restarted = AutoloaderService(root=tmp_path / 'private')
    restarted.start(candidate(), private_material='test')
    # Test seam: sources count as prepared; the disposable adapter never writes here.
    restarted.media.source = tmp_path
    restarted.media.adapter = DisposableWindows(tmp_path / 'disposable')
    return restarted


def test_relaunch_after_unfinished_write_warns_then_resumes_usb_selection(tmp_path: Path) -> None:
    from macloader.autoloader.models import ActionKind, Stage
    _at_media_stage(tmp_path, 'media_attempt')
    restarted = _relaunch(tmp_path)
    action = restarted.next_action()
    assert action.code == 'MEDIA_ATTEMPT_UNRESOLVED'
    assert action.kind == ActionKind.HUMAN and action.stage == Stage.MEDIA
    assert action.choices == (RESTART_CHOICE,)
    assert 'must not be booted' in action.message
    after = restarted.perform_choice(RESTART_CHOICE)
    assert after.code == 'MEDIA_DEVICE_REQUIRED'
    assert restarted.session is not None and 'media_attempt' not in restarted.session.artifacts
    # The removal was saved: a further relaunch goes straight to USB selection.
    again = _relaunch(tmp_path)
    assert again.session is not None and 'media_attempt' not in again.session.artifacts
    assert again.next_action().code == 'MEDIA_DEVICE_REQUIRED'


def test_completed_write_is_not_reported_as_unfinished(tmp_path: Path) -> None:
    from macloader.autoloader.models import Stage
    _at_media_stage(tmp_path, 'media_attempt', 'media_write')
    action = _relaunch(tmp_path).next_action()
    assert action.code != 'MEDIA_ATTEMPT_UNRESOLVED' and action.stage == Stage.FIRST_BOOT


def _stub_write_gate(service: 'AutoloaderService') -> None:
    assert service.snapshot is not None
    snapshot = service.snapshot
    service._current_efi_manifest = lambda: None  # type: ignore[assignment,method-assign,return-value]
    service._current_bound_snapshot = lambda: snapshot  # type: ignore[method-assign]


def test_unfinished_write_check_is_silent_during_the_write(tmp_path: Path) -> None:
    service = _at_media_stage(tmp_path)
    service.media.source = tmp_path
    service.media.adapter = DisposableWindows(tmp_path / 'disposable')
    _stub_write_gate(service)
    seen: list[str] = []

    def write(cancel: object, mark_started: Callable[[], None]) -> None:
        mark_started()
        seen.append(service.next_action().code)
        raise CaptureError('MEDIA_WRITE_FAILED', 'synthetic failure after the boundary')

    service.media.write = write  # type: ignore[method-assign,assignment]
    with pytest.raises(CaptureError):
        service._write_media()
    assert seen and seen[0] != 'MEDIA_ATTEMPT_UNRESOLVED'
    assert service.next_action().code == 'MEDIA_ATTEMPT_UNRESOLVED'


def test_refusal_before_boundary_does_not_say_erased(tmp_path: Path) -> None:
    service = _at_media_stage(tmp_path)
    service.media.source = tmp_path
    service.media.adapter = DisposableWindows(tmp_path / 'disposable')
    _stub_write_gate(service)
    assert service.media.selected is None
    with pytest.raises(ValueError) as error:
        service._write_media()
    assert 'erased' not in str(error.value)
    assert service.session is not None and 'media_attempt' not in service.session.artifacts
    assert service.public_status()['destructive_operation_may_have_occurred'] is False
    assert 'erased' not in service.failure_message()
    assert service.next_action().code == 'MEDIA_DEVICE_REQUIRED'


def test_failure_after_boundary_says_erased_and_stays_visible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _at_media_stage(tmp_path)
    media = prepared(tmp_path, monkeypatch)
    adapter = DisposableWindows(tmp_path / 'disposable')
    adapter.image.corrupt_readback = True
    media.adapter = adapter
    media.select(media.discover()[0])
    service.media = media
    _stub_write_gate(service)
    with pytest.raises(CaptureError, match='may have been erased'):
        service._write_media()
    assert service.session is not None and 'media_attempt' in service.session.artifacts
    assert 'media_write' not in service.session.artifacts
    assert service.next_action().code == 'MEDIA_ATTEMPT_UNRESOLVED'
