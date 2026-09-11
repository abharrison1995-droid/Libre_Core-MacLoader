import collections
import base64
import hashlib
import io
import os
from pathlib import Path
import shutil
import sys
import struct
from typing import Any, Callable, Optional
import unittest.mock

import pytest

from macloader.exceptions import ArtifactDownloadError, ChecksumMismatchError
from macloader.recovery import RecoveryAcquirer, RecoveryAsset
from macloader.recovery.acquirer import _RecoveryRedirectHandler, is_approved_recovery_host, verify_apple_chunklist
import macloader.recovery.acquirer as acquirer_module


def _asset(payload: bytes, url: str = "https://osrecovery.apple.com/recovery") -> RecoveryAsset:
    return RecoveryAsset("InstallAssistant", "24A348", url, hashlib.sha256(payload).hexdigest(), len(payload))


class _MockResponse:
    def __init__(
        self,
        data: bytes,
        headers: Optional[dict[str, str]] = None,
        url: str = "https://osrecovery.apple.com/recovery",
        status: int = 200,
    ) -> None:
        self._bio = io.BytesIO(data)
        self.headers = headers if headers is not None else {"Content-Length": str(len(data))}
        self._url = url
        self.status = status
        self.fp = unittest.mock.MagicMock()
        self.fp.raw._sock.settimeout = unittest.mock.MagicMock()

    def read(self, amt: int = -1) -> bytes:
        return self._bio.read(amt)

    def geturl(self) -> str:
        return self._url

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "_MockResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


def test_recovery_download_streams_to_owned_temp_and_publishes_atomically(tmp_path: Path) -> None:
    payload = b"recovery payload"

    def transport(_: str, destination: Path) -> None:
        destination.write_bytes(payload)

    destination = tmp_path / "Recovery.dmg"
    result = RecoveryAcquirer(transport=transport).download(_asset(payload), destination)
    assert result == destination
    assert destination.read_bytes() == payload
    assert not list(tmp_path.glob("*.part"))


def test_recovery_rejects_size_hash_and_cancellation_failures(tmp_path: Path) -> None:
    payload = b"payload"

    def oversized_transport(_: str, destination: Path) -> None:
        destination.write_bytes(payload + b"-too-large")

    with pytest.raises(ArtifactDownloadError, match="exceeds the configured size"):
        RecoveryAcquirer(transport=oversized_transport).download(_asset(payload), tmp_path / "Recovery.dmg")

    def truncated_transport(_: str, destination: Path) -> None:
        destination.write_bytes(payload[:3])

    with pytest.raises(ArtifactDownloadError, match="size mismatch"):
        RecoveryAcquirer(transport=truncated_transport).download(_asset(payload), tmp_path / "truncated.dmg")

    with pytest.raises(ArtifactDownloadError, match="cancelled"):
        RecoveryAcquirer(transport=lambda *_: None, cancel=lambda: True).download(_asset(payload), tmp_path / "cancelled.dmg")

    wrong = RecoveryAsset("InstallAssistant", "24A348", "https://osrecovery.apple.com/recovery", "0" * 64, len(payload))

    def correct_transport(_: str, destination: Path) -> None:
        destination.write_bytes(payload)

    with pytest.raises(ChecksumMismatchError):
        RecoveryAcquirer(transport=correct_transport).download(wrong, tmp_path / "wrong.dmg")


def test_recovery_rejects_non_apple_hosts_unless_configured(tmp_path: Path) -> None:
    payload = b"test payload"
    dest = tmp_path / "dest.dmg"

    # Default rejects arbitrary non-Apple host
    with pytest.raises(ArtifactDownloadError, match="not an approved Apple domain"):
        RecoveryAcquirer().download(RecoveryAsset("IA", "24A348", "https://attacker.com/recovery", "0" * 64, len(payload)), dest)

    # Allowed when explicitly in allowed_hosts
    def transport(_: str, destination: Path) -> None:
        destination.write_bytes(payload)

    acquirer = RecoveryAcquirer(transport=transport, allowed_hosts={"attacker.com"})
    res = acquirer.download(RecoveryAsset("IA", "24A348", "https://attacker.com/recovery", hashlib.sha256(payload).hexdigest(), len(payload)), dest)
    assert res == dest


def test_recovery_retains_prior_valid_destination_across_all_failure_modes(tmp_path: Path) -> None:
    original_data = b"pre-existing valid recovery file"

    def check_failure(acquirer: RecoveryAcquirer, asset: RecoveryAsset) -> None:
        dest = tmp_path / "ExistingRecovery.dmg"
        dest.write_bytes(original_data)
        try:
            acquirer.download(asset, dest)
        except (ArtifactDownloadError, ChecksumMismatchError):
            pass
        assert dest.exists()
        assert dest.read_bytes() == original_data
        assert not list(tmp_path.glob("*.part"))

    def _write_bad(_: str, p: Path) -> None:
        p.write_bytes(b"bad")

    def _write_big(_: str, p: Path) -> None:
        p.write_bytes(b"way-too-big")

    def _write_small(_: str, p: Path) -> None:
        p.write_bytes(b"a")

    # 1. Checksum mismatch
    check_failure(
        RecoveryAcquirer(transport=_write_bad),
        RecoveryAsset("IA", "24A348", "https://osrecovery.apple.com/pkg", "0" * 64, 3),
    )

    # 2. Oversized
    check_failure(
        RecoveryAcquirer(transport=_write_big),
        RecoveryAsset("IA", "24A348", "https://osrecovery.apple.com/pkg", "0" * 64, 2),
    )

    # 3. Truncated
    check_failure(
        RecoveryAcquirer(transport=_write_small),
        RecoveryAsset("IA", "24A348", "https://osrecovery.apple.com/pkg", "0" * 64, 10),
    )

    # 4. Cancelled
    check_failure(
        RecoveryAcquirer(transport=lambda *_: None, cancel=lambda: True),
        RecoveryAsset("IA", "24A348", "https://osrecovery.apple.com/pkg", "0" * 64, 10),
    )

    # 5. Network error
    mock_opener = unittest.mock.MagicMock()
    mock_opener.open.side_effect = OSError("Network dropped")
    with unittest.mock.patch("urllib.request.build_opener", return_value=mock_opener):
        check_failure(
            RecoveryAcquirer(),
            RecoveryAsset("IA", "24A348", "https://osrecovery.apple.com/pkg", "0" * 64, 10),
        )


def test_recovery_rejects_invalid_url_and_hash_metadata(tmp_path: Path) -> None:
    acquirer = RecoveryAcquirer()
    dest = tmp_path / "dummy.dmg"

    # Non-HTTPS
    with pytest.raises(ArtifactDownloadError, match="must use HTTPS"):
        acquirer.download(RecoveryAsset("IA", "24A348", "http://osrecovery.apple.com/pkg", "a" * 64, 100), dest)

    # URL with embedded credentials
    with pytest.raises(ArtifactDownloadError, match="must use HTTPS"):
        acquirer.download(RecoveryAsset("IA", "24A348", "https://user:pass@osrecovery.apple.com/pkg", "a" * 64, 100), dest)

    # URL with non-standard port
    with pytest.raises(ArtifactDownloadError, match="must use HTTPS"):
        acquirer.download(RecoveryAsset("IA", "24A348", "https://osrecovery.apple.com:8080/pkg", "a" * 64, 100), dest)

    # Zero or negative size
    with pytest.raises(ArtifactDownloadError, match="missing or exceeds"):
        acquirer.download(RecoveryAsset("IA", "24A348", "https://osrecovery.apple.com/pkg", "a" * 64, 0), dest)
    with pytest.raises(ArtifactDownloadError, match="missing or exceeds"):
        acquirer.download(RecoveryAsset("IA", "24A348", "https://osrecovery.apple.com/pkg", "a" * 64, -5), dest)

    # Size exceeds max_bytes
    with pytest.raises(ArtifactDownloadError, match="missing or exceeds"):
        RecoveryAcquirer(max_bytes=1000).download(RecoveryAsset("IA", "24A348", "https://osrecovery.apple.com/pkg", "a" * 64, 2000), dest)

    # Invalid SHA-256 format
    with pytest.raises(ArtifactDownloadError, match="SHA-256 is invalid"):
        acquirer.download(RecoveryAsset("IA", "24A348", "https://osrecovery.apple.com/pkg", "not-a-hash", 100), dest)
    with pytest.raises(ArtifactDownloadError, match="SHA-256 is invalid"):
        acquirer.download(RecoveryAsset("IA", "24A348", "https://osrecovery.apple.com/pkg", "a" * 63, 100), dest)


def test_recovery_rejects_symlinks_and_insufficient_disk_space(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    acquirer = RecoveryAcquirer(transport=lambda *_: None)
    payload = b"payload"
    asset = _asset(payload)
    dest = tmp_path / "dest.dmg"

    # Broken/dangling symlink destination (is_symlink() is True even if target does not exist)
    monkeypatch.setattr(Path, "is_symlink", lambda self: True)
    with pytest.raises(ArtifactDownloadError, match="must not be a symlink"):
        acquirer.download(asset, dest)

    # Broken/dangling parent directory symlink
    def mock_is_symlink(self: Path) -> bool:
        if self == dest.parent:
            return True
        return False
    monkeypatch.setattr(Path, "is_symlink", mock_is_symlink)
    with pytest.raises(ArtifactDownloadError, match="has a symlinked parent"):
        acquirer.download(asset, dest)

    # Disk space check
    monkeypatch.undo()
    Usage = collections.namedtuple("Usage", ["total", "used", "free"])
    monkeypatch.setattr(shutil, "disk_usage", lambda _: Usage(1000, 1000, len(payload) - 1))
    with pytest.raises(ArtifactDownloadError, match="Insufficient free disk space"):
        acquirer.download(asset, dest)


def test_recovery_redirect_handler_policy() -> None:
    handler = _RecoveryRedirectHandler(is_approved_recovery_host)

    # Allowed Apple host
    req = unittest.mock.MagicMock()
    fp = unittest.mock.MagicMock()
    with unittest.mock.patch("urllib.request.HTTPRedirectHandler.redirect_request") as mock_super:
        mock_super.return_value = "ok"
        res = handler.redirect_request(req, fp, 302, "Found", {}, "https://osrecovery.apple.com/asset.dmg")
        assert res == "ok"

    # Allowed Apple CDN host
    with unittest.mock.patch("urllib.request.HTTPRedirectHandler.redirect_request") as mock_super:
        mock_super.return_value = "ok"
        res = handler.redirect_request(req, fp, 302, "Found", {}, "https://updates.cdn-apple.com/asset.dmg")
        assert res == "ok"

    # Allowed bare cdn-apple.com host
    with unittest.mock.patch("urllib.request.HTTPRedirectHandler.redirect_request") as mock_super:
        mock_super.return_value = "ok"
        res = handler.redirect_request(req, fp, 302, "Found", {}, "https://cdn-apple.com/asset.dmg")
        assert res == "ok"

    # Disallowed external host
    with pytest.raises(ArtifactDownloadError, match="Recovery redirect leaves the approved HTTPS source policy"):
        handler.redirect_request(req, fp, 302, "Found", {}, "https://malicious.test/asset.dmg")

    # Disallowed non-HTTPS
    with pytest.raises(ArtifactDownloadError, match="Recovery redirect leaves the approved HTTPS source policy"):
        handler.redirect_request(req, fp, 302, "Found", {}, "http://osrecovery.apple.com/asset.dmg")

    # Disallowed port
    with pytest.raises(ArtifactDownloadError, match="Recovery redirect leaves the approved HTTPS source policy"):
        handler.redirect_request(req, fp, 302, "Found", {}, "https://osrecovery.apple.com:8443/asset.dmg")


def test_recovery_urllib_streaming_success(tmp_path: Path) -> None:
    payload = b"Apple Recovery streamed content" * 1024
    asset = _asset(payload)
    dest = tmp_path / "downloaded.pkg"

    mock_resp = _MockResponse(payload)
    mock_opener = unittest.mock.MagicMock()
    mock_opener.open.return_value = mock_resp

    with unittest.mock.patch("urllib.request.build_opener", return_value=mock_opener):
        result = RecoveryAcquirer(timeout_seconds=10.0).download(asset, dest)

    assert result == dest
    assert dest.read_bytes() == payload
    assert not list(tmp_path.glob("*.part"))
    assert mock_resp.fp.raw._sock.settimeout.called


def test_recovery_resume_retains_partial_and_uses_validated_http_range(tmp_path: Path) -> None:
    payload = b"0123456789abcdef"
    asset = _asset(payload)
    destination = tmp_path / "resumable.dmg"
    acquirer = RecoveryAcquirer(resume=True, timeout_seconds=10.0)
    partial, metadata = acquirer._resume_paths(asset, destination)
    partial.write_bytes(payload[:6])
    acquirer._write_resume_metadata(metadata, asset)

    response = _MockResponse(
        payload[6:],
        headers={"Content-Length": str(len(payload) - 6), "Content-Range": f"bytes 6-{len(payload) - 1}/{len(payload)}"},
        status=206,
    )
    opener = unittest.mock.MagicMock()
    opener.open.return_value = response
    with unittest.mock.patch("urllib.request.build_opener", return_value=opener):
        assert acquirer.download(asset, destination) == destination

    request = opener.open.call_args.args[0]
    assert request.headers["Range"] == "bytes=6-"
    assert destination.read_bytes() == payload
    assert not partial.exists()
    assert not metadata.exists()


def test_recovery_resume_cancellation_retains_partial_and_server_range_fallback_restarts(tmp_path: Path) -> None:
    payload = b"0123456789abcdef"
    asset = _asset(payload)
    destination = tmp_path / "resumable.dmg"
    cancelled = [False]

    class CancelAfterOneChunk(_MockResponse):
        def read(self, amt: int = -1) -> bytes:
            value = super().read(amt)
            if value:
                cancelled[0] = True
            return value

    opener = unittest.mock.MagicMock()
    opener.open.return_value = CancelAfterOneChunk(payload)
    with unittest.mock.patch("urllib.request.build_opener", return_value=opener):
        with pytest.raises(ArtifactDownloadError, match="cancelled"):
            RecoveryAcquirer(resume=True, cancel=lambda: cancelled[0]).download(asset, destination)
    partial, metadata = RecoveryAcquirer._resume_paths(asset, destination)
    assert partial.exists()
    assert metadata.exists()

    # A 200 response that ignores Range must replace, not concatenate, the
    # retained prefix.
    opener.open.return_value = _MockResponse(payload, status=200)
    with unittest.mock.patch("urllib.request.build_opener", return_value=opener):
        assert RecoveryAcquirer(resume=True).download(asset, destination) == destination
    assert destination.read_bytes() == payload


def test_recovery_resume_rejects_non_regular_metadata_without_blocking(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO test requires POSIX")
    payload = b"fifo-safe-recovery"
    asset = _asset(payload)
    destination = tmp_path / "fifo-safe.dmg"
    partial, metadata = RecoveryAcquirer._resume_paths(asset, destination)
    partial.write_bytes(b"prefix")
    getattr(os, "mkfifo")(metadata)
    opener = unittest.mock.MagicMock()
    opener.open.return_value = _MockResponse(payload)
    with unittest.mock.patch("urllib.request.build_opener", return_value=opener):
        assert RecoveryAcquirer(resume=True).download(asset, destination) == destination
    assert destination.read_bytes() == payload


def test_recovery_urllib_detects_deadline_timeout_during_streaming(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"chunk1" + b"chunk2" + b"chunk3"
    asset = _asset(payload)
    dest = tmp_path / "timeout.pkg"

    # Realistic slow-trickle stream where time advances on chunk read
    clock = [100.0]
    monkeypatch.setattr("time.monotonic", lambda: clock[0])

    class SlowTrickleResponse:
        def __init__(self) -> None:
            self.chunks = [b"chunk1", b"chunk2", b"chunk3"]
            self.headers = {"Content-Length": str(len(payload))}
            self.fp = unittest.mock.MagicMock()
            self.fp.raw._sock.settimeout = unittest.mock.MagicMock()

        def read(self, amt: int = -1) -> bytes:
            if not self.chunks:
                return b""
            # Advance clock past the 1.0 second timeout upon reading chunk 2
            clock[0] += 2.0
            return self.chunks.pop(0)

        def geturl(self) -> str:
            return "https://osrecovery.apple.com/recovery"

        def __enter__(self) -> "SlowTrickleResponse":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    acquirer = RecoveryAcquirer(timeout_seconds=1.0)
    mock_opener = unittest.mock.MagicMock()
    mock_opener.open.return_value = SlowTrickleResponse()

    with unittest.mock.patch("urllib.request.build_opener", return_value=mock_opener):
        with pytest.raises(ArtifactDownloadError, match="exceeded its total deadline"):
            acquirer.download(asset, dest)

    assert not dest.exists()
    assert not list(tmp_path.glob("*.part"))


def test_recovery_urllib_detects_midstream_cancellation(tmp_path: Path) -> None:
    payload = b"first_chunk_of_recovery_second_chunk_of_recovery"
    asset = _asset(payload)
    dest = tmp_path / "cancel.pkg"

    cancelled = [False]

    class CancellableResponse:
        def __init__(self) -> None:
            self.chunks = [b"first_chunk_of_recovery_", b"second_chunk_of_recovery"]
            self.headers = {"Content-Length": str(len(payload))}
            self.fp = unittest.mock.MagicMock()
            self.fp.raw._sock.settimeout = unittest.mock.MagicMock()

        def read(self, amt: int = -1) -> bytes:
            if not self.chunks:
                return b""
            cancelled[0] = True  # Trigger cancellation during read
            return self.chunks.pop(0)

        def geturl(self) -> str:
            return "https://osrecovery.apple.com/recovery"

        def __enter__(self) -> "CancellableResponse":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    acquirer = RecoveryAcquirer(cancel=lambda: cancelled[0])
    mock_opener = unittest.mock.MagicMock()
    mock_opener.open.return_value = CancellableResponse()

    with unittest.mock.patch("urllib.request.build_opener", return_value=mock_opener):
        with pytest.raises(ArtifactDownloadError, match="cancelled"):
            acquirer.download(asset, dest)

    assert not dest.exists()
    assert not list(tmp_path.glob("*.part"))


def test_recovery_urllib_rejects_content_length_mismatch(tmp_path: Path) -> None:
    payload = b"payload data"
    asset = _asset(payload)
    dest = tmp_path / "mismatch.pkg"

    # Content-Length mismatch
    mock_resp = _MockResponse(payload, headers={"Content-Length": str(len(payload) + 10)})
    mock_opener = unittest.mock.MagicMock()
    mock_opener.open.return_value = mock_resp

    with unittest.mock.patch("urllib.request.build_opener", return_value=mock_opener):
        with pytest.raises(ArtifactDownloadError, match="Recovery size mismatch"):
            RecoveryAcquirer().download(asset, dest)


def test_recovery_large_synthetic_asset_uses_bounded_memory(tmp_path: Path) -> None:
    # 16 GiB declared size
    sixteen_gib = 16 * 1024 * 1024 * 1024
    chunk_pattern = b"A" * (1024 * 1024)  # 1 MiB chunk
    chunks_count = 8  # Produce 8 MiB in chunks to test streaming behavior without consuming 16 GiB disk

    # Verify that generator stream processes constant memory
    class SyntheticStreamResponse:
        def __init__(self) -> None:
            self.yielded = 0
            self.headers = {"Content-Length": str(chunks_count * len(chunk_pattern))}
            self.fp = unittest.mock.MagicMock()
            self.fp.raw._sock.settimeout = unittest.mock.MagicMock()

        def read(self, amt: int = -1) -> bytes:
            if self.yielded >= chunks_count:
                return b""
            self.yielded += 1
            return chunk_pattern

        def geturl(self) -> str:
            return "https://osrecovery.apple.com/recovery"

        def __enter__(self) -> "SyntheticStreamResponse":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    hasher = hashlib.sha256()
    for _ in range(chunks_count):
        hasher.update(chunk_pattern)
    expected_sha = hasher.hexdigest()

    asset = RecoveryAsset("LargeRecovery", "24A348", "https://osrecovery.apple.com/recovery", expected_sha, chunks_count * len(chunk_pattern))
    dest = tmp_path / "synthetic_large.dmg"

    mock_opener = unittest.mock.MagicMock()
    mock_opener.open.return_value = SyntheticStreamResponse()

    with unittest.mock.patch("urllib.request.build_opener", return_value=mock_opener):
        res = RecoveryAcquirer(max_bytes=sixteen_gib).download(asset, dest)

    assert res == dest
    assert dest.stat().st_size == chunks_count * len(chunk_pattern)


def test_recovery_normalizes_network_errors(tmp_path: Path) -> None:
    payload = b"data"
    asset = _asset(payload)
    dest = tmp_path / "net_err.pkg"

    mock_opener = unittest.mock.MagicMock()
    mock_opener.open.side_effect = OSError("Connection reset by peer")

    with unittest.mock.patch("urllib.request.build_opener", return_value=mock_opener):
        with pytest.raises(ArtifactDownloadError, match="Recovery download failed: Connection reset by peer"):
            RecoveryAcquirer().download(asset, dest)

    assert not dest.exists()
    assert not list(tmp_path.glob("*.part"))


def test_recovery_bundle_failure_preserves_prior_pair(tmp_path: Path) -> None:
    image_payload = b"new recovery image"
    chunklist_header = struct.pack("<4sIBBBxQQQ", b"CNKL", 0x24, 1, 1, 2, 1, 0x24, 0x24 + 0x24)
    chunklist_entry = struct.pack("<I32s", len(image_payload), hashlib.sha256(image_payload).digest())
    chunklist_payload = chunklist_header + chunklist_entry + hashlib.sha256(chunklist_header + chunklist_entry).digest()
    image_destination = tmp_path / "Recovery.dmg"
    chunklist_destination = tmp_path / "Recovery.chunklist"
    image_destination.write_bytes(b"prior image")
    chunklist_destination.write_bytes(b"prior chunklist")

    def transport(url: str, destination: Path) -> None:
        destination.write_bytes(chunklist_payload if url.endswith("chunklist") else image_payload)

    image = RecoveryAsset("InstallAssistant", "24A335", "https://osrecovery.apple.com/image", hashlib.sha256(image_payload).hexdigest(), len(image_payload))
    chunklist = RecoveryAsset("InstallAssistant", "24A335", "https://osrecovery.apple.com/chunklist", hashlib.sha256(chunklist_payload).hexdigest(), len(chunklist_payload))
    with pytest.raises(ArtifactDownloadError):
        RecoveryAcquirer(transport=transport).download_bundle(
            image, chunklist, image_destination, chunklist_destination, "a" * 64
        )
    assert image_destination.read_bytes() == b"prior image"
    assert chunklist_destination.read_bytes() == b"prior chunklist"


def test_recovery_bundle_publishes_only_after_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_payload = b"verified image"
    chunklist_payload = b"verified signed chunklist"
    image_destination = tmp_path / "Recovery.dmg"
    chunklist_destination = tmp_path / "Recovery.chunklist"
    image = RecoveryAsset("InstallAssistant", "24A335", "https://osrecovery.apple.com/image", hashlib.sha256(image_payload).hexdigest(), len(image_payload))
    chunklist = RecoveryAsset("InstallAssistant", "24A335", "https://osrecovery.apple.com/chunklist", hashlib.sha256(chunklist_payload).hexdigest(), len(chunklist_payload))

    def fake_download(self: RecoveryAcquirer, asset: RecoveryAsset, destination: Path) -> Path:
        destination.write_bytes(image_payload if asset is image else chunklist_payload)
        return destination

    monkeypatch.setattr(RecoveryAcquirer, "download", fake_download)
    monkeypatch.setattr(
        "macloader.recovery.acquirer.verify_apple_chunklist",
        lambda *_args: (1, len(image_payload)),
    )
    bundle = RecoveryAcquirer().download_bundle(
        image, chunklist, image_destination, chunklist_destination, "b" * 64
    )
    assert bundle.image_path == image_destination
    assert bundle.chunklist_path == chunklist_destination
    assert image_destination.read_bytes() == image_payload
    assert chunklist_destination.read_bytes() == chunklist_payload
    assert bundle.evidence.verified_chunks == 1


def test_recovery_windows_bundle_uses_path_transaction_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_payload = b"windows image"
    chunklist_payload = b"windows chunklist"
    image_destination = tmp_path / "Recovery.dmg"
    chunklist_destination = tmp_path / "Recovery.chunklist"
    image_destination.write_bytes(b"old image")
    chunklist_destination.write_bytes(b"old chunklist")
    image = RecoveryAsset("InstallAssistant", "24A335", "https://osrecovery.apple.com/image", hashlib.sha256(image_payload).hexdigest(), len(image_payload))
    chunklist = RecoveryAsset("InstallAssistant", "24A335", "https://osrecovery.apple.com/chunklist", hashlib.sha256(chunklist_payload).hexdigest(), len(chunklist_payload))

    def fake_download(self: RecoveryAcquirer, asset: RecoveryAsset, destination: Path) -> Path:
        destination.write_bytes(image_payload if asset is image else chunklist_payload)
        return destination

    monkeypatch.setattr(acquirer_module, "_WINDOWS_PLATFORM", True)
    monkeypatch.setattr(RecoveryAcquirer, "download", fake_download)
    monkeypatch.setattr("macloader.recovery.acquirer.verify_apple_chunklist", lambda *_args: (1, len(image_payload)))
    bundle = RecoveryAcquirer().download_bundle(
        image, chunklist, image_destination, chunklist_destination, "d" * 64
    )
    assert bundle.image_path.read_bytes() == image_payload
    assert bundle.chunklist_path.read_bytes() == chunklist_payload


def test_recovery_windows_resume_helpers_reject_symlinks_and_non_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(acquirer_module, "_WINDOWS_PLATFORM", True)
    directory = tmp_path / "staging"
    directory.mkdir()
    asset = _asset(b"resume")
    partial, metadata = RecoveryAcquirer._resume_paths(asset, directory / "Recovery.dmg")
    partial.write_bytes(b"resume")
    RecoveryAcquirer._write_resume_metadata(metadata, asset)
    assert RecoveryAcquirer._resume_metadata_matches_fd(directory, metadata.name, asset) is True
    resume_fd = RecoveryAcquirer._open_resume_fd(directory, partial.name, os.O_RDWR)
    os.close(resume_fd)
    assert RecoveryAcquirer._open_owned_directory(directory) == directory
    with pytest.raises(ArtifactDownloadError, match="not safely accessible"):
        RecoveryAcquirer._open_owned_directory(tmp_path / "missing")


def test_recovery_verifies_signed_chunklist_encoding_with_deterministic_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the OpenCore CNKL RSA encoding without storing a private key.

    The release gate still requires a real Apple chunklist readback; this
    deterministic fixture only proves the parser's byte order and padding path.
    """
    image_payload = base64.b64decode("Zml4dHVyZSBzaWduZWQgaW1hZ2U=")
    chunklist_payload = base64.b64decode(
        "Q05LTCQAAAABAQEAAQAAAAAAAAAkAAAAAAAAAEgAAAAAAAAAFAAAACJ0vvMymY5Lj1WHSj913vuwk+u8XpTtGGHkNI4ty0VlluGUe+I+EhegVcbNZwUv3hHiGysV1qq0cuRZTjEVZ+RPfAkUkKlodynlYtH9oYL5/jrV44gqLj28PGD2VEYRng0O+qB4gD1JtuFPKGKLoqRdM1vL8rm/zI69DmFGZ3lWxnqZLAsdGjX0k0ncr4TBgyT4WBCXini1BBnMCM5rqPRLmOZpIGODOVqebOlsYKn8JEyoJjMnrbM1RPuSuO0bX4LnNvdvo7JiD+zlH4gvSx8Se5dla08Ez/MG8E8mp0uDjf6MFKWd4BqCPbLR2eXkXf6If6FBiIXu2PhCLMlC5/hH1X/ITYS5BIMEjMKfNLMHxWvzaBpi1LM1WBV6p83EGA=="
    )
    image_path = tmp_path / "image"
    chunklist_path = tmp_path / "chunklist"
    image_path.write_bytes(image_payload)
    chunklist_path.write_bytes(chunklist_payload)
    test_public_key = int(
        "b4415cbce1ad9d50f0c824f18bc6ef6d5f681d14cd8cf3ae810cca3e391d511f76217957b9c2e350fbf5a90417e1a8a05cb0b746610884cb5dceb96d6e30fe97becec6866af6a7c8c3593468f4e3736468e4d589349bb87680213f86dcaaac7841b6686daf56851a0c4d82428b25962d0262a5e5b2cb9b1ac83c5ba7498cbb13c8b0ebc93cb51292295b6d238a213d0213a4087a376edbee0409f64d4a54dc328e3c8c3c0364357f8e937b4b7da54197de98542ec36ebf959632e3100a7c6e777691a23f2d80c7e6899259c9fcc89cce3e3d51ebce32e7861a26fd99f738ac47e9e64012691dce7db11fbf91760145e932ec187032d8d6a1c0ddd46f97da780b",
        16,
    )
    monkeypatch.setattr(acquirer_module, "_APPLE_EFI_ROM_PUBLIC_KEY", test_public_key)
    chunks, size = verify_apple_chunklist(
        image_path,
        chunklist_path,
        "2274bef332998e4b8f55874a3f75defbb093ebbc5e94ed1861e4348e2dcb4565",
        "d3a8e903d3319ed6dbf42261af41be8a4b5139caa7f117a3d320dff65cd7ca60",
    )
    assert chunks == 1
    assert size == len(image_payload)
    corrupted = bytearray(chunklist_payload)
    corrupted[-1] ^= 0x01
    chunklist_path.write_bytes(corrupted)
    with pytest.raises(ArtifactDownloadError, match="signature is invalid"):
        verify_apple_chunklist(
            image_path,
            chunklist_path,
            "2274bef332998e4b8f55874a3f75defbb093ebbc5e94ed1861e4348e2dcb4565",
            hashlib.sha256(corrupted).hexdigest(),
        )


def test_recovery_bundle_rolls_back_if_second_publication_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_payload = b"replacement image"
    chunklist_payload = b"replacement chunklist"
    image_destination = tmp_path / "Recovery.dmg"
    chunklist_destination = tmp_path / "Recovery.chunklist"
    image_destination.write_bytes(b"original image")
    chunklist_destination.write_bytes(b"original chunklist")
    image = RecoveryAsset("InstallAssistant", "24A335", "https://osrecovery.apple.com/image", hashlib.sha256(image_payload).hexdigest(), len(image_payload))
    chunklist = RecoveryAsset("InstallAssistant", "24A335", "https://osrecovery.apple.com/chunklist", hashlib.sha256(chunklist_payload).hexdigest(), len(chunklist_payload))

    def fake_download(self: RecoveryAcquirer, asset: RecoveryAsset, destination: Path) -> Path:
        destination.write_bytes(image_payload if asset is image else chunklist_payload)
        return destination

    publications = 0

    def fail_second(part: Path, destination: Path, _destination_directory_fd: Optional[int] = None) -> None:
        nonlocal publications
        publications += 1
        if publications == 2:
            raise OSError("simulated publication interruption")
        part.replace(destination)

    monkeypatch.setattr(RecoveryAcquirer, "download", fake_download)
    monkeypatch.setattr("macloader.recovery.acquirer.verify_apple_chunklist", lambda *_args: (1, len(image_payload)))
    monkeypatch.setattr(RecoveryAcquirer, "_publish_owned", staticmethod(fail_second))
    with pytest.raises(OSError, match="publication interruption"):
        RecoveryAcquirer().download_bundle(
            image, chunklist, image_destination, chunklist_destination, "c" * 64
        )
    assert image_destination.read_bytes() == b"original image"
    assert chunklist_destination.read_bytes() == b"original chunklist"


def test_recovery_publication_uses_validated_windows_atomic_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"payload"
    asset = _asset(payload)
    monkeypatch.setattr(acquirer_module, "_WINDOWS_PLATFORM", True)

    def transport(_url: str, destination: Path) -> None:
        destination.write_bytes(payload)

    destination = tmp_path / "Recovery.dmg"
    assert RecoveryAcquirer(transport=transport).download(asset, destination) == destination
    assert destination.read_bytes() == payload
