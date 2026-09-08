import collections
import hashlib
import io
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Callable, Optional
import unittest.mock

import pytest

from macloader.exceptions import ArtifactDownloadError, ChecksumMismatchError
from macloader.recovery import RecoveryAcquirer, RecoveryAsset
from macloader.recovery.acquirer import _RecoveryRedirectHandler, is_approved_recovery_host


def _asset(payload: bytes, url: str = "https://osrecovery.apple.com/recovery") -> RecoveryAsset:
    return RecoveryAsset("InstallAssistant", "24A348", url, hashlib.sha256(payload).hexdigest(), len(payload))


class _MockResponse:
    def __init__(self, data: bytes, headers: Optional[dict[str, str]] = None, url: str = "https://osrecovery.apple.com/recovery") -> None:
        self._bio = io.BytesIO(data)
        self.headers = headers if headers is not None else {"Content-Length": str(len(data))}
        self._url = url
        self.fp = unittest.mock.MagicMock()
        self.fp.raw._sock.settimeout = unittest.mock.MagicMock()

    def read(self, amt: int = -1) -> bytes:
        return self._bio.read(amt)

    def geturl(self) -> str:
        return self._url

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
