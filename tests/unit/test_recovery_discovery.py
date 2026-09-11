"""P5 exact-target Recovery discovery and authenticated readback tests."""

from dataclasses import replace
import hashlib
from pathlib import Path
import struct
import unittest.mock

import pytest

from macloader.domain.recovery import RecoveryBinding, RecoveryEvidence, RecoveryLock, RecoveryProduct, RecoveryState, RecoveryTarget
from macloader.recovery.acquirer import verify_apple_chunklist
from macloader.recovery.discovery import AppleRecoveryDiscovery, DiscoveryResponse
from macloader.recovery.service import RecoveryService, load_recovery_policy
from macloader.exceptions import ArtifactDownloadError


def _target() -> RecoveryTarget:
    return RecoveryTarget("sequoia", "macOS Sequoia", "15.0", "24A335")


def _response(ap: str) -> bytes:
    return (
        f"AP: {ap}\n"
        "AU: https://updates.cdn-apple.com/recovery/image.dmg\n"
        f"AH: {'a' * 64}\n"
        "AT: image-token\n"
        "CU: https://updates.cdn-apple.com/recovery/image.chunklist\n"
        f"CH: {'b' * 64}\n"
        "CT: chunk-token\n"
    ).encode()


def test_discovery_locks_only_an_exact_server_identified_target() -> None:
    calls: list[tuple[str, bytes]] = []

    def transport(url: str, _headers: object, data: bytes) -> DiscoveryResponse:
        calls.append((url, data))
        if url.endswith("/"):
            return DiscoveryResponse({"Set-Cookie": "session=opaque-token; Path=/"}, b"")
        return DiscoveryResponse({}, _response("InstallAssistant 24A335"))

    result = AppleRecoveryDiscovery(transport=transport).discover(_target())
    assert result.state == RecoveryState.DISCOVERED
    assert result.product is not None
    assert result.product.target == _target()
    assert "token" not in str(result.to_dict()).lower()
    assert len(calls) == 2
    assert b"sn=00000000000000000" in calls[1][1]


def test_discovery_does_not_treat_default_product_as_exact() -> None:
    def transport(url: str, _headers: object, _data: bytes) -> DiscoveryResponse:
        if url.endswith("/"):
            return DiscoveryResponse({"Set-Cookie": "session=opaque"}, b"")
        return DiscoveryResponse({}, _response("InstallAssistant"))

    result = AppleRecoveryDiscovery(transport=transport).discover(_target())
    assert result.state == RecoveryState.AMBIGUOUS
    assert result.product is None


def test_discovery_rejects_server_substitution_and_malformed_metadata() -> None:
    def substituted(url: str, _headers: object, _data: bytes) -> DiscoveryResponse:
        if url.endswith("/"):
            return DiscoveryResponse({"Set-Cookie": "session=opaque"}, b"")
        return DiscoveryResponse({}, _response("InstallAssistant 24A348"))

    result = AppleRecoveryDiscovery(transport=substituted).discover(_target())
    assert result.state == RecoveryState.UNAVAILABLE
    assert result.product is None

    with pytest.raises(ArtifactDownloadError, match="malformed line"):
        AppleRecoveryDiscovery._parse_key_values(b"AP: x\nnot metadata\n")

    with pytest.raises(ArtifactDownloadError, match="duplicate"):
        AppleRecoveryDiscovery._parse_key_values(b"AP: x\nAP: y\n")


def test_discovery_distinguishes_explicit_unavailable_from_ambiguous() -> None:
    def unavailable(url: str, _headers: object, _data: bytes) -> DiscoveryResponse:
        if url.endswith("/"):
            return DiscoveryResponse({"Set-Cookie": "session=opaque"}, b"")
        return DiscoveryResponse({}, _response("InstallAssistant") + b"ER: unavailable\n")

    result = AppleRecoveryDiscovery(transport=unavailable).discover(_target())
    assert result.state == RecoveryState.UNAVAILABLE
    assert "explicitly" in result.diagnostics[0]


def test_discovery_rejects_unapproved_asset_urls() -> None:
    with pytest.raises(ValueError, match="approved Apple HTTPS"):
        AppleRecoveryDiscovery._asset_url("https://example.invalid/recovery")
    with pytest.raises(ValueError, match="unsafe URL"):
        AppleRecoveryDiscovery._asset_url("https://updates.cdn-apple.com:444/recovery")


def test_recovery_lock_rejects_stale_binding() -> None:
    digest = "a" * 64
    binding = RecoveryBinding(digest, "thinkpad-t480s", digest, digest, digest, digest, digest, digest)
    product = RecoveryProduct(
        _target(), "https://updates.cdn-apple.com/image", digest, 1,
        "https://updates.cdn-apple.com/chunklist", digest, 1, "<private>", "<private>"
    )
    lock = RecoveryLock("1", RecoveryState.LOCKED, product, binding, digest, digest)
    lock.assert_current(binding, _target())
    with pytest.raises(ValueError, match="stale"):
        lock.assert_current(replace(binding, build_plan_digest="b" * 64), _target())


def test_unsigned_chunklist_is_not_authenticated(tmp_path: Path) -> None:
    image = tmp_path / "Recovery.dmg"
    chunklist = tmp_path / "Recovery.chunklist"
    image.write_bytes(b"recovery")
    header = struct.pack("<4sIBBBxQQQ", b"CNKL", 0x24, 1, 1, 2, 1, 0x24, 0x24 + 0x24)
    chunk = struct.pack("<I32s", len(b"recovery"), hashlib.sha256(b"recovery").digest())
    chunklist.write_bytes(header + chunk + hashlib.sha256(header + chunk).digest())
    with pytest.raises(ArtifactDownloadError, match="unsigned"):
        verify_apple_chunklist(image, chunklist, hashlib.sha256(b"recovery").hexdigest(), hashlib.sha256(chunklist.read_bytes()).hexdigest())


def test_policy_loader_and_service_bind_exact_target() -> None:
    policy = load_recovery_policy()
    service = RecoveryService(policy)
    assert policy.target == _target()
    assert service.target().digest == _target().digest
    assert len(policy.digest) == 64

    result = service.discover(transport=lambda url, _headers, _data: DiscoveryResponse(
        {"Set-Cookie": "session=opaque"} if url.endswith("/") else {},
        b"" if url.endswith("/") else _response("InstallAssistant 24A335"),
    ))
    assert result.product is not None
    digest = "c" * 64
    binding = RecoveryBinding(_target().digest, "thinkpad-t480s", digest, digest, policy.digest, digest, digest, digest)
    lock = service.lock(result, binding)
    assert lock.state == RecoveryState.LOCKED
    assert lock.product.to_dict()["image_session_ref"] == "<private>"


def test_service_rejects_non_discovered_result_and_evidence_rejects_untrusted_data() -> None:
    policy = load_recovery_policy()
    service = RecoveryService(policy)
    result = __import__("macloader.recovery.discovery", fromlist=["RecoveryDiscoveryResult"]).RecoveryDiscoveryResult(
        RecoveryState.AMBIGUOUS, _target(), None, "d" * 64, ("ambiguous",)
    )
    binding = RecoveryBinding("e" * 64, "thinkpad-t480s", "e" * 64, "e" * 64, "e" * 64, "e" * 64, "e" * 64, "e" * 64)
    with pytest.raises(ValueError, match="exact discovered"):
        service.lock(result, binding)
    with pytest.raises(ValueError, match="signed"):
        RecoveryEvidence("a" * 64, "b" * 64, "c" * 64, False, 1, 1, "test")


def test_recovery_lock_round_trips_without_private_session_values(tmp_path: Path) -> None:
    policy = load_recovery_policy()
    service = RecoveryService(policy)
    result = service.discover(transport=lambda url, _headers, _data: DiscoveryResponse(
        {"Set-Cookie": "session=opaque"} if url.endswith("/") else {},
        b"" if url.endswith("/") else _response("InstallAssistant 24A335"),
    ))
    digest = "f" * 64
    binding = RecoveryBinding(_target().digest, "thinkpad-t480s", digest, digest, policy.digest, digest, digest, digest)
    lock = service.lock(result, binding)
    path = tmp_path / "recovery.lock.json"
    service.save_lock(lock, path)
    loaded = service.load_lock(path)
    assert loaded.product.image_session_ref == "<private>"
    assert loaded.product.target == _target()
    assert loaded.binding == binding


def test_recovery_service_blocks_unbounded_live_acquisition(tmp_path: Path) -> None:
    policy = load_recovery_policy()
    service = RecoveryService(policy)
    result = service.discover(transport=lambda url, _headers, _data: DiscoveryResponse(
        {"Set-Cookie": "session=opaque"} if url.endswith("/") else {},
        b"" if url.endswith("/") else _response("InstallAssistant 24A335"),
    ))
    digest = "1" * 64
    binding = RecoveryBinding(_target().digest, "thinkpad-t480s", digest, digest, policy.digest, digest, digest, digest)
    with pytest.raises(ArtifactDownloadError, match="asset sizes"):
        service.acquire(result, binding, tmp_path)


def test_discovery_parser_and_target_guards() -> None:
    with pytest.raises(ValueError, match="frozen"):
        AppleRecoveryDiscovery(transport=lambda *_: DiscoveryResponse({}, b"")).discover(
            RecoveryTarget("sequoia", "macOS Sequoia", "15.1", "24B83")
        )
    with pytest.raises(ArtifactDownloadError, match="UTF-8"):
        AppleRecoveryDiscovery._parse_key_values(b"\xff")
    with pytest.raises(ArtifactDownloadError, match="bounded"):
        AppleRecoveryDiscovery._parse_key_values(b"AA: x\n" * 20000)
    with pytest.raises(ValueError, match="unsafe URL"):
        AppleRecoveryDiscovery._asset_url("https://updates.cdn-apple.com")


def test_live_discovery_size_probe_requires_bounded_apple_metadata() -> None:
    class Response:
        headers = {"Content-Length": "1234"}

        def geturl(self) -> str:
            return "https://updates.cdn-apple.com/recovery/image.dmg"

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            pass

    opener = unittest.mock.MagicMock()
    opener.open.return_value = Response()
    with unittest.mock.patch("macloader.recovery.discovery.build_opener", return_value=opener):
        discovery = AppleRecoveryDiscovery()
        assert discovery._asset_size("https://updates.cdn-apple.com/recovery/image.dmg", "opaque") == 1234
    request = opener.open.call_args.args[0]
    assert request.get_method() == "HEAD"
    assert request.headers["Cookie"] == "AssetToken=opaque"
