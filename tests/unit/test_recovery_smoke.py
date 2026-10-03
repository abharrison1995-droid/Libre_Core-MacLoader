"""Smoke mode verifies bytes while refusing exact qualification/install authority."""
import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Mapping
import pytest

from macloader.database.loader import get_database
from macloader.domain.recovery import RecoveryLock
from macloader.exceptions import ArtifactDownloadError, ChecksumMismatchError
from macloader.recovery.acquirer import RecoveryAcquirer
from macloader.recovery.discovery import AppleRecoveryDiscovery, DiscoveryResponse
from macloader.recovery.smoke import SmokeRecoveryService, VerifiedSmokeRecovery


def fixture() -> dict[str, str]:
    return json.loads((Path(__file__).parents[1] / 'fixtures/recovery/signed_encoding.json').read_text())  # type: ignore[no-any-return]


def client(monkeypatch: pytest.MonkeyPatch, *, bad_url: bool = False) -> SmokeRecoveryService:
    f = fixture()
    image, chunk = base64.b64decode(f['image_payload']), base64.b64decode(f['chunklist_payload'])
    def transport(url: str, headers: Mapping[str, str], body: bytes) -> DiscoveryResponse:
        if url.endswith('/RecoveryImage'):
            values = dict(AP='opaque-product-id', AU=('http' if bad_url else 'https') + '://osrecovery.apple.com/image.dmg', AH=hashlib.sha256(image).hexdigest(), AT='private-image-token', CU='https://osrecovery.apple.com/image.chunklist', CH=hashlib.sha256(chunk).hexdigest(), CT='private-chunk-token')
            return DiscoveryResponse({}, '\n'.join(f'{k}: {v}' for k, v in values.items()).encode())
        return DiscoveryResponse({'Set-Cookie': 'session=private-cookie'}, b'')
    discovery = AppleRecoveryDiscovery(transport=transport)
    monkeypatch.setattr(discovery, '_asset_size', lambda url, token: len(chunk) if 'chunklist' in url else len(image))
    policy = next(iter(get_database().campaigns.values())).smoke_recovery_policy
    return SmokeRecoveryService(policy, discovery)


def test_smoke_acquisition_resume_readback_and_no_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import macloader.recovery.acquirer as module
    f = fixture()
    monkeypatch.setattr(module, '_APPLE_EFI_ROM_PUBLIC_KEY', int(f['test_public_key'], 16))
    service = client(monkeypatch)
    target = __import__('macloader.recovery.service', fromlist=['RecoveryService']).RecoveryService().target()
    candidate = service.discover(target)
    assert candidate.image.build == 'unproven'
    def download(url: str, destination: Path) -> None:
        destination.write_bytes(base64.b64decode(f['chunklist_payload'] if 'chunklist' in url else f['image_payload']))
    record = service.acquire(candidate, 'a' * 64, tmp_path / 'private', acquirer=RecoveryAcquirer(transport=download))
    assert service.verify(record, tmp_path / 'private', 'a' * 64) == record
    assert VerifiedSmokeRecovery.from_dict(record.to_dict()) == record
    assert record.requested_target.build == '24A335' and record.to_dict()['actual_build'] is None
    assert record.to_dict()['installation_authorized'] is False and record.to_dict()['qualifying'] is False
    assert not list(tmp_path.rglob('*lock*'))
    with pytest.raises((ValueError, KeyError)):
        RecoveryLock.from_dict(record.to_dict())
    assert 'private-image-token' not in (tmp_path / 'private/smoke.json').read_text()
    with pytest.raises(ValueError, match='stale'):
        service.verify(record, tmp_path / 'private', 'b' * 64)
    (tmp_path / 'private/BaseSystem.dmg').write_bytes(b'corrupt')
    with pytest.raises((ArtifactDownloadError, ChecksumMismatchError)):
        service.verify(record, tmp_path / 'private', 'a' * 64)


def test_unsigned_wrong_hash_and_unsafe_asset_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = client(monkeypatch, bad_url=True)
    from macloader.recovery.service import RecoveryService
    with pytest.raises(ValueError, match='HTTPS'):
        service.discover(RecoveryService().target())
    service = client(monkeypatch)
    candidate = service.discover(RecoveryService().target())
    f = fixture()
    def download(url: str, path: Path) -> None:
        path.write_bytes(base64.b64decode(f['chunklist_payload'] if 'chunklist' in url else f['image_payload']))
    # Real production Apple public key cannot authenticate our synthetic signature.
    with pytest.raises(ArtifactDownloadError, match='signature'):
        service.acquire(candidate, 'a' * 64, tmp_path / 'signature', acquirer=RecoveryAcquirer(transport=download))
    corrupted = replace(candidate, image=replace(candidate.image, sha256='f' * 64))
    with pytest.raises((ArtifactDownloadError, ChecksumMismatchError)):
        service.acquire(corrupted, 'a' * 64, tmp_path / 'hash', acquirer=RecoveryAcquirer(transport=download))
    with pytest.raises(ValueError):
        VerifiedSmokeRecovery.from_dict({'kind': 'prototype-smoke-recovery-v1', 'qualifying': True})


def test_guided_exact_failure_requires_explicit_mode_without_target_substitution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.unit.test_autoloader_build import ready
    from macloader.autoloader.models import Stage
    service = ready(tmp_path)
    assert service.session is not None and service.configuration is not None
    digest = service.configuration.semantic_digest
    service._record_artifact('tools')
    service._record_artifact('dependencies')
    service._record_artifact('efi', digest='e' * 64, path=str(tmp_path / 'synthetic-efi'))
    monkeypatch.setattr(service.workflow, 'discover_recovery', lambda **kwargs: (_ for _ in ()).throw(ArtifactDownloadError('HTTP 405')))
    assert service.advance_until_blocked().stage == Stage.RECOVERY_MODE
    monkeypatch.setattr(SmokeRecoveryService, 'discover', lambda *args: (_ for _ in ()).throw(ArtifactDownloadError('unsupported source')))
    action = service.perform_choice('Smoke test only')
    assert action.code == 'SMOKE_RECOVERY_BLOCKED'
    assert service.configuration.semantic_digest == digest and service.configuration.target is not None
    assert service.configuration.target.build == '24A335' and 'recovery' not in service.session.artifacts
