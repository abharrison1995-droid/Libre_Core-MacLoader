"""Explicit non-qualifying Recovery: byte authenticity never implies build identity."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Optional
from urllib.request import HTTPRedirectHandler, Request, build_opener

from macloader.domain.contracts import canonical_json_digest
from macloader.domain.recovery import RecoveryTarget
from macloader.exceptions import ArtifactDownloadError
from macloader.recovery.acquirer import RecoveryAcquirer, RecoveryAsset, verify_apple_chunklist
from macloader.recovery.discovery import AppleRecoveryDiscovery, DiscoveryResponse, MAX_DISCOVERY_BYTES, _redacted_record_digest


class _NoMetadataRedirects(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        raise ArtifactDownloadError('Smoke metadata redirects are not permitted')


@dataclass(frozen=True)
class SmokeRecoveryCandidate:
    requested_target: RecoveryTarget
    image: RecoveryAsset
    chunklist: RecoveryAsset
    discovery_digest: str
    protocol_digest: str


@dataclass(frozen=True)
class VerifiedSmokeRecovery:
    requested_target: RecoveryTarget
    binding_digest: str
    image_digest: str
    chunklist_digest: str
    discovery_digest: str
    protocol_digest: str
    image_size_bytes: int
    verified_chunks: int

    def to_dict(self) -> dict[str, object]:
        return dict(kind='prototype-smoke-recovery-v1', qualifying=False,
            installation_authorized=False, actual_build=None, requested_target=self.requested_target.to_dict(),
            binding_digest=self.binding_digest, image_digest=self.image_digest, chunklist_digest=self.chunklist_digest,
            discovery_digest=self.discovery_digest, protocol_digest=self.protocol_digest,
            image_size_bytes=self.image_size_bytes, verified_chunks=self.verified_chunks,
            metadata_trust='untrusted', payload_authentication='apple-signed-chunklist')

    @property
    def digest(self) -> str:
        return canonical_json_digest(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Any) -> 'VerifiedSmokeRecovery':
        if not isinstance(raw, dict) or raw.get('kind') != 'prototype-smoke-recovery-v1' or raw.get('qualifying') is not False or raw.get('installation_authorized') is not False or raw.get('actual_build') is not None:
            raise ValueError('Invalid non-qualifying smoke record')
        if type(raw.get('image_size_bytes')) is not int or type(raw.get('verified_chunks')) is not int:
            raise ValueError('Smoke sizes must be integers')
        result = cls(RecoveryTarget.from_dict(raw['requested_target']), str(raw['binding_digest']), str(raw['image_digest']), str(raw['chunklist_digest']), str(raw['discovery_digest']), str(raw['protocol_digest']), int(raw['image_size_bytes']), int(raw['verified_chunks']))
        if result.to_dict() != raw or result.image_size_bytes <= 0 or result.verified_chunks <= 0:
            raise ValueError('Invalid smoke record schema')
        if any(not re.fullmatch(r'[0-9a-f]{64}', value) for value in (result.binding_digest, result.image_digest, result.chunklist_digest, result.discovery_digest, result.protocol_digest)):
            raise ValueError('Invalid smoke provenance digest')
        return result


class SmokeRecoveryService:
    def __init__(self, policy: Mapping[str, Any], discovery: Optional[AppleRecoveryDiscovery] = None):
        if policy.get('purpose') != 'picker-recovery-smoke-only' or policy.get('metadata_trust') != 'untrusted' or policy.get('installation_authorized') is not False or policy.get('asset_scheme') != 'https':
            raise ValueError('Smoke policy cannot grant installation or qualification')
        self.policy = policy
        self.protocol_digest = canonical_json_digest(dict(policy))
        self.client = discovery or AppleRecoveryDiscovery(transport=self._metadata_transport)

    @staticmethod
    def _metadata_transport(url: str, headers: Mapping[str, str], body: bytes) -> DiscoveryResponse:
        # Explicit policy migration: fixed legacy origin only; metadata is UNTRUSTED.
        if not url.startswith('https://osrecovery.apple.com/'):
            raise ArtifactDownloadError('Smoke metadata origin is invalid')
        request = Request('http://' + url.removeprefix('https://'), headers=dict(headers), data=body or None)
        with build_opener(_NoMetadataRedirects()).open(request, timeout=30) as response:
            data = response.read(MAX_DISCOVERY_BYTES + 1)
            if len(data) > MAX_DISCOVERY_BYTES:
                raise ArtifactDownloadError('Smoke metadata exceeds the bounded limit')
            return DiscoveryResponse(dict(response.headers), data)

    def discover(self, requested: RecoveryTarget, cancel: Optional[Callable[[], bool]] = None) -> SmokeRecoveryCandidate:
        import secrets
        self.client.cancel = cancel
        session = self.client._request('/')
        cookie = self.client._session_cookie(session.headers)
        response = self.client._request('/InstallationPayload/RecoveryImage', cookie=cookie,
            post={'cid': secrets.token_hex(8).upper(), 'sn': self.client.mlb, 'bid': self.client.board_id,
                  'k': secrets.token_hex(32).upper(), 'fg': secrets.token_hex(32).upper(), 'os': 'default'})
        values = self.client._parse_key_values(response.body)
        if not all(k in values for k in ('AP', 'AU', 'AH', 'AT', 'CU', 'CH', 'CT')) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', values.get('AP', '')):
            raise ArtifactDownloadError('Smoke discovery metadata is incomplete')
        assets = []
        for url_key, digest_key, token_key in (('AU', 'AH', 'AT'), ('CU', 'CH', 'CT')):
            url = self.client._asset_url(values[url_key])
            digest = values[digest_key].lower()
            size = self.client._asset_size(url, values[token_key])
            if size is None or not re.fullmatch(r'[0-9a-f]{64}', digest):
                raise ArtifactDownloadError('Smoke Recovery assets need bounded sizes and valid integrity metadata')
            assets.append(RecoveryAsset('OpenCorePickerSmoke', 'unproven', url, digest, size, values[token_key], 'picker-recovery-smoke-only'))
        return SmokeRecoveryCandidate(requested, assets[0], assets[1], _redacted_record_digest(requested, values), self.protocol_digest)

    def acquire(self, candidate: SmokeRecoveryCandidate, binding_digest: str, destination: Path,
                cancel: Optional[Callable[[], bool]] = None, acquirer: Optional[RecoveryAcquirer] = None) -> VerifiedSmokeRecovery:
        from macloader.workflow.service import WorkflowService
        if any(asset.purpose != 'picker-recovery-smoke-only' or asset.build != 'unproven' for asset in (candidate.image, candidate.chunklist)):
            raise ValueError('Smoke acquisition cannot use qualification assets')
        if candidate.protocol_digest != self.protocol_digest:
            raise ValueError('Smoke protocol policy changed')
        WorkflowService._ensure_private_directory(destination)
        downloader = acquirer or RecoveryAcquirer(cancel=cancel, resume=True)
        binding = canonical_json_digest({'binding': binding_digest, 'discovery': candidate.discovery_digest, 'protocol': self.protocol_digest, 'purpose': 'smoke-only'})
        bundle = downloader.download_bundle(candidate.image, candidate.chunklist, destination / 'BaseSystem.dmg', destination / 'BaseSystem.chunklist', binding, verification_tool='OpenCore-1.0.8-smoke-protocol', resume=downloader.resume)
        record = VerifiedSmokeRecovery(candidate.requested_target, binding_digest, bundle.evidence.image_digest, bundle.evidence.chunklist_digest,
            candidate.discovery_digest, self.protocol_digest, bundle.evidence.image_size_bytes, bundle.evidence.verified_chunks)
        WorkflowService._write_private_json(destination / 'smoke.json', json.dumps(record.to_dict()))
        return record

    def verify(self, record: VerifiedSmokeRecovery, destination: Path, binding_digest: str,
               cancel: Optional[Callable[[], bool]] = None) -> VerifiedSmokeRecovery:
        if record.binding_digest != binding_digest or record.protocol_digest != self.protocol_digest:
            raise ValueError('Smoke Recovery is bound to stale inputs')
        chunks, size = verify_apple_chunklist(destination / 'BaseSystem.dmg', destination / 'BaseSystem.chunklist', record.image_digest, record.chunklist_digest, cancel)
        if (chunks, size) != (record.verified_chunks, record.image_size_bytes):
            raise ValueError('Smoke Recovery readback changed')
        return record
