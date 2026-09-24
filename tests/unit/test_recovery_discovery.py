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
import macloader.recovery.service as service_module
from macloader.exceptions import ArtifactDownloadError, ChecksumMismatchError


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


def test_ap_product_identifier_never_proves_an_exact_build() -> None:
    calls: list[tuple[str, bytes]] = []

    def transport(url: str, _headers: object, data: bytes) -> DiscoveryResponse:
        calls.append((url, data))
        if url.endswith("/"):
            return DiscoveryResponse({"Set-Cookie": "session=opaque-token; Path=/"}, b"")
        return DiscoveryResponse({}, _response("696-28424"))

    result = AppleRecoveryDiscovery(transport=transport).discover(_target())
    assert result.state == RecoveryState.AMBIGUOUS
    assert result.product is None
    assert result.apple_product_id == "696-28424"
    assert "without authenticated version/build metadata" in result.diagnostics[0]
    assert "token" not in str(result.to_dict()).lower()
    assert len(calls) == 2
    assert b"sn=00000000000000000" in calls[1][1]


def test_discovery_does_not_treat_default_product_as_exact() -> None:
    def transport(url: str, _headers: object, _data: bytes) -> DiscoveryResponse:
        if url.endswith("/"):
            return DiscoveryResponse({"Set-Cookie": "session=opaque"}, b"")
        return DiscoveryResponse({}, _response("696-28424"))

    result = AppleRecoveryDiscovery(transport=transport).discover(_target())
    assert result.state == RecoveryState.AMBIGUOUS
    assert result.product is None
    assert result.apple_product_id == "696-28424"


def test_discovery_rejects_server_substitution_and_malformed_metadata() -> None:
    def substituted(url: str, _headers: object, _data: bytes) -> DiscoveryResponse:
        if url.endswith("/"):
            return DiscoveryResponse({"Set-Cookie": "session=opaque"}, b"")
        return DiscoveryResponse({}, _response("696-28424"))

    result = AppleRecoveryDiscovery(transport=substituted).discover(_target())
    assert result.state == RecoveryState.AMBIGUOUS
    assert result.product is None
    assert result.apple_product_id == "696-28424"

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


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("board_id", "invalid", "board ID"),
        ("mlb", "short", "17-character"),
        ("tool_version", "0.0.0", "pinned OpenCore"),
        ("discovery_host", "example.invalid", "pinned HTTPS"),
        ("query_scheme", "http", "pinned HTTPS"),
        ("asset_hosts", ("osrecovery.apple.com",), "asset hosts"),
    ],
)
def test_discovery_rejects_policy_constructor_drift(field: str, value: object, message: str) -> None:
    kwargs: dict[str, object] = {field: value}
    with pytest.raises(ValueError, match=message):
        AppleRecoveryDiscovery(**kwargs)  # type: ignore[arg-type]


def test_discovery_rejects_missing_cookie_and_cancellation() -> None:
    with pytest.raises(ArtifactDownloadError, match="session cookie"):
        AppleRecoveryDiscovery(
            transport=lambda url, _headers, _data: DiscoveryResponse({}, b"")
        ).discover(_target())

    calls = 0

    def cancel_after_first() -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    def transport(url: str, _headers: object, _data: bytes) -> DiscoveryResponse:
        return DiscoveryResponse({"Set-Cookie": "session=opaque"} if url.endswith("/") else {}, _response("696-28424"))

    with pytest.raises(ArtifactDownloadError, match="cancelled"):
        AppleRecoveryDiscovery(transport=transport, cancel=cancel_after_first).discover(_target())


def test_discovery_parser_and_size_probe_fail_closed() -> None:
    assert AppleRecoveryDiscovery._parse_key_values(b"\n") == {}
    with pytest.raises(ArtifactDownloadError, match="invalid metadata"):
        AppleRecoveryDiscovery._parse_key_values(b"aA: value\n")
    with pytest.raises(ArtifactDownloadError, match="invalid metadata"):
        AppleRecoveryDiscovery._parse_key_values(b"AP: \n")
    with pytest.raises(ArtifactDownloadError, match="session cookie"):
        AppleRecoveryDiscovery._session_cookie({"Set-Cookie": "other=value"})

    class Response:
        def __init__(self, headers: dict[str, str], url: str = "https://updates.cdn-apple.com/recovery/image.dmg") -> None:
            self.headers = headers
            self._url = url

        def geturl(self) -> str:
            return self._url

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            pass

    discovery = AppleRecoveryDiscovery()
    for headers in ({}, {"Content-Length": "invalid"}, {"Content-Length": "0"}, {"Content-Length": str(17 * 1024 * 1024 * 1024)}):
        opener = unittest.mock.MagicMock()
        opener.open.return_value = Response(headers)
        with unittest.mock.patch("macloader.recovery.discovery.build_opener", return_value=opener):
            assert discovery._asset_size("https://updates.cdn-apple.com/recovery/image.dmg", "opaque") is None

    opener = unittest.mock.MagicMock()
    opener.open.side_effect = OSError("network")
    with unittest.mock.patch("macloader.recovery.discovery.build_opener", return_value=opener):
        assert discovery._asset_size("https://updates.cdn-apple.com/recovery/image.dmg", "opaque") is None


def test_discovery_transport_enforces_endpoint_and_response_limits() -> None:
    discovery = AppleRecoveryDiscovery()
    with pytest.raises(ArtifactDownloadError, match="outside the pinned Apple policy"):
        discovery._transport("http://osrecovery.apple.com/", {}, b"")

    class Response:
        headers: dict[str, str] = {}

        def __init__(self, body: bytes) -> None:
            self.body = body

        def read(self, size: int) -> bytes:
            return self.body[:size]

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            pass

    with unittest.mock.patch(
        "macloader.recovery.discovery.urlopen",
        return_value=Response(b"x" * (64 * 1024 + 1)),
    ):
        with pytest.raises(ArtifactDownloadError, match="bounded limit"):
            discovery._transport("https://osrecovery.apple.com/", {}, b"")

    with unittest.mock.patch("macloader.recovery.discovery.urlopen", side_effect=OSError("offline")):
        with pytest.raises(ArtifactDownloadError, match="OSError"):
            discovery._transport("https://osrecovery.apple.com/", {}, b"")


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
        b"" if url.endswith("/") else _response("696-28424"),
    ))
    assert result.state == RecoveryState.AMBIGUOUS
    assert result.apple_product_id == "696-28424"
    assert result.product is None


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
    product = RecoveryProduct(
        _target(), "https://updates.cdn-apple.com/image", "a" * 64, 1,
        "https://updates.cdn-apple.com/chunklist", "b" * 64, 1, "<private>", "<private>"
    )
    result = __import__("macloader.recovery.discovery", fromlist=["RecoveryDiscoveryResult"]).RecoveryDiscoveryResult(
        RecoveryState.DISCOVERED, _target(), product, "f" * 64,
    )
    digest = "f" * 64
    binding = RecoveryBinding(_target().digest, "thinkpad-t480s", digest, digest, policy.digest, digest, digest, digest)
    lock = service.lock(result, binding)
    path = tmp_path / "recovery.lock.json"
    service.save_verified_bundle(
        lock,
        RecoveryEvidence(lock.digest, digest, digest, True, 1, 1, "test"),
        tmp_path,
    )
    loaded = service.load_lock(path)
    assert loaded.product.image_session_ref == "<private>"
    assert loaded.product.target == _target()
    assert loaded.binding == binding


def test_recovery_service_blocks_unbounded_live_acquisition(tmp_path: Path) -> None:
    policy = load_recovery_policy()
    service = RecoveryService(policy)
    result = service.discover(transport=lambda url, _headers, _data: DiscoveryResponse(
        {"Set-Cookie": "session=opaque"} if url.endswith("/") else {},
        b"" if url.endswith("/") else _response("696-28424"),
    ))
    digest = "1" * 64
    binding = RecoveryBinding(_target().digest, "thinkpad-t480s", digest, digest, policy.digest, digest, digest, digest)
    with pytest.raises(ValueError, match="exact discovered"):
        service.acquire(result, binding, tmp_path)


def test_recovery_service_rejects_stale_lock_bindings() -> None:
    policy = load_recovery_policy()
    service = RecoveryService(policy)
    product = RecoveryProduct(
        policy.target, "https://updates.cdn-apple.com/image", "a" * 64, 1,
        "https://updates.cdn-apple.com/chunklist", "b" * 64, 1, "<private>", "<private>"
    )
    result = __import__("macloader.recovery.discovery", fromlist=["RecoveryDiscoveryResult"]).RecoveryDiscoveryResult(
        RecoveryState.DISCOVERED, policy.target, product, "f" * 64,
    )
    base = RecoveryBinding(
        policy.target.digest,
        "thinkpad-t480s",
        "a" * 64,
        "b" * 64,
        policy.digest,
        "c" * 64,
        "d" * 64,
        "e" * 64,
    )
    with pytest.raises(ValueError, match="target"):
        service.lock(result, replace(base, target_digest="f" * 64))
    with pytest.raises(ValueError, match="policy"):
        service.lock(result, replace(base, policy_digest="f" * 64))
    different_target = RecoveryTarget("sonoma", "macOS Sonoma", "14.0", "23F79")
    different_product = replace(result.product, target=different_target)
    different_result = replace(result, product=different_product)
    with pytest.raises(ValueError, match="policy target"):
        service.lock(different_result, base)


def test_recovery_service_rejects_malformed_locks_and_unbounded_assets(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable"):
        RecoveryService.load_lock(malformed)
    not_object = tmp_path / "list.json"
    not_object.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="object"):
        RecoveryService.load_lock(not_object)

    policy = load_recovery_policy()
    service = RecoveryService(policy)
    product = RecoveryProduct(
        policy.target,
        "https://updates.cdn-apple.com/image",
        "a" * 64,
        None,
        "https://updates.cdn-apple.com/chunklist",
        "b" * 64,
        1,
        "<private>",
        "<private>",
    )
    with pytest.raises(ArtifactDownloadError, match="bounded Recovery asset sizes"):
        service._assets_for(product)


def test_recovery_service_verify_binds_readback_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy = load_recovery_policy()
    service = RecoveryService(policy)
    product = RecoveryProduct(
        policy.target, "https://updates.cdn-apple.com/image", "a" * 64, 5,
        "https://updates.cdn-apple.com/chunklist", "b" * 64, 1, "<private>", "<private>"
    )
    result = __import__("macloader.recovery.discovery", fromlist=["RecoveryDiscoveryResult"]).RecoveryDiscoveryResult(
        RecoveryState.DISCOVERED, policy.target, product, "f" * 64,
    )
    binding = RecoveryBinding(
        policy.target.digest,
        "thinkpad-t480s",
        "a" * 64,
        "b" * 64,
        policy.digest,
        "c" * 64,
        "d" * 64,
        "e" * 64,
    )
    lock = service.lock(result, binding)
    service.save_verified_bundle(
        lock,
        RecoveryEvidence(lock.digest, "b" * 64, "c" * 64, True, 1, 5, "test"),
        tmp_path,
    )
    image = tmp_path / "image"
    chunklist = tmp_path / "chunklist"
    image.write_bytes(b"image")
    chunklist.write_bytes(b"chunklist")
    monkeypatch.setattr(service_module, "verify_apple_chunklist", lambda *_args: (1, 5))
    evidence = service.verify(lock, image, chunklist)
    assert evidence.verified_chunks == 1
    monkeypatch.setattr(service_module, "verify_apple_chunklist", lambda *_args: (1, 4))
    with pytest.raises(ChecksumMismatchError, match="image size"):
        service.verify(lock, image, chunklist)


def test_recovery_service_rejects_unsafe_metadata_destinations(tmp_path: Path) -> None:
    policy = load_recovery_policy()
    service = RecoveryService(policy)
    file_path = tmp_path / "not-a-directory"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(ArtifactDownloadError, match="not a directory"):
        service._assert_safe_directory(file_path)
    try:
        symlink = tmp_path / "symlink"
        symlink.symlink_to(file_path)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable on this host")
    with pytest.raises(ArtifactDownloadError, match="symlinked directory"):
        service._assert_safe_directory(symlink)


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
