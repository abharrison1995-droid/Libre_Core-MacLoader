"""Unit tests for the streaming Downloader with mocked network transport and offline mode."""

import hashlib
from pathlib import Path
import pytest

from macloader.database.loader import Database
from macloader.dependencies.downloader import Downloader
from macloader.domain.dependencies import ArtifactVariant, DependencyArtifact
from macloader.exceptions import ArtifactDownloadError, ChecksumMismatchError
from macloader.orchestrator import Orchestrator


def test_downloader_with_matching_mock_transport(tmp_path: Path) -> None:
    content = b"Valid downloaded archive payload"
    expected_sha = hashlib.sha256(content).hexdigest()

    artifact = DependencyArtifact(
        asset_name="test.zip",
        source_url="https://example.com/test.zip",
        sha256=expected_sha,
        size_bytes=len(content),
        variant=ArtifactVariant.RELEASE,
    )

    def mock_transport(url: str, dest_path: Path) -> None:
        dest_path.write_bytes(content)

    downloader = Downloader(transport=mock_transport)
    out_file = tmp_path / "test_out.zip"
    res = downloader.download_artifact(artifact, out_file)

    assert res == out_file
    assert out_file.is_file()
    assert out_file.read_bytes() == content
    # .part file should not remain
    assert not (tmp_path / "test_out.part").exists()


def test_downloader_fails_and_cleans_up_on_hash_mismatch(tmp_path: Path) -> None:
    content = b"Wrong content"
    expected_sha = "a" * 64

    artifact = DependencyArtifact(
        asset_name="bad.zip",
        source_url="https://example.com/bad.zip",
        sha256=expected_sha,
        size_bytes=len(content),
    )

    def mock_transport(url: str, dest_path: Path) -> None:
        dest_path.write_bytes(content)

    downloader = Downloader(transport=mock_transport)
    out_file = tmp_path / "bad_out.zip"

    with pytest.raises(ChecksumMismatchError) as exc:
        downloader.download_artifact(artifact, out_file)
    assert "Integrity check failed" in str(exc.value)
    assert not out_file.exists()
    assert not (tmp_path / "bad_out.part").exists()


def test_downloader_fails_on_network_transport_error(tmp_path: Path) -> None:
    artifact = DependencyArtifact(
        asset_name="error.zip",
        source_url="https://example.com/error.zip",
        sha256="0" * 64,
        size_bytes=10,
    )

    def mock_error_transport(url: str, dest_path: Path) -> None:
        raise OSError("Connection timeout simulating network drop")

    downloader = Downloader(transport=mock_error_transport)
    out_file = tmp_path / "error_out.zip"

    with pytest.raises(ArtifactDownloadError) as exc:
        downloader.download_artifact(artifact, out_file)
    assert "Connection timeout" in str(exc.value)
    assert not out_file.exists()


def test_offline_fetch_mode_raises_on_cache_miss(tmp_path: Path, t480s_baseline_fixture: Path) -> None:
    orchestrator = Orchestrator(cache_dir=tmp_path / "empty_cache")
    snapshot = orchestrator.probe_hardware(fixture_path=t480s_baseline_fixture)
    plan = orchestrator.generate_plan(snapshot, target_macos="sequoia")
    dep_set = orchestrator.resolve_dependencies(plan)

    with pytest.raises(ArtifactDownloadError) as exc:
        orchestrator.fetch_dependencies(dep_set, offline=True, plan=plan)
    assert "Offline mode: the following required artifacts are missing" in str(exc.value)


def test_downloader_enforces_download_budget(tmp_path: Path) -> None:
    artifact = DependencyArtifact(
        asset_name="large.zip", source_url="https://example.com/large.zip", sha256="0" * 64, size_bytes=10,
    )

    def oversized_transport(url: str, dest_path: Path) -> None:
        dest_path.write_bytes(b"0123456789")

    with pytest.raises(ArtifactDownloadError, match="maximum size"):
        Downloader(transport=oversized_transport, max_download_bytes=4).download_artifact(artifact, tmp_path / "large.zip")
