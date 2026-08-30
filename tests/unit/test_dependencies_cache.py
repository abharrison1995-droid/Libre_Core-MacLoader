"""Unit tests for the cache manager, hash validation, and atomic commits."""

import hashlib
from pathlib import Path
import pytest

from macloader.database.loader import Database
from macloader.dependencies.cache import CacheManager, compute_file_sha256
from macloader.domain.dependencies import ArtifactVariant
from macloader.exceptions import ChecksumMismatchError


def test_cache_manager_stores_and_verifies_valid_artifact(tmp_path: Path, test_db: Database) -> None:
    cache_mgr = CacheManager(cache_dir=tmp_path / "cache")
    spec = test_db.get_dependency_spec("lilu")
    assert spec is not None
    artifact = spec.get_artifact(ArtifactVariant.RELEASE)
    assert artifact is not None

    # Create dummy source file with matching SHA-256
    temp_file = tmp_path / "test_lilu.zip"
    # We create content whose hash matches artifact.sha256 or mock the artifact
    content = b"Mock Lilu binary content"
    temp_file.write_bytes(content)
    actual_hash = hashlib.sha256(content).hexdigest()
    # Temporarily set artifact sha256 to actual_hash for test
    artifact.sha256 = actual_hash

    cached_path = cache_mgr.put_artifact(spec, ArtifactVariant.RELEASE, temp_file)
    assert cached_path.is_file()
    assert cache_mgr.has_valid_artifact(spec, ArtifactVariant.RELEASE) is True

    # Check stats
    stats = cache_mgr.get_cache_stats()
    assert stats["total_files"] == 1
    assert stats["total_size_bytes"] == len(content)


def test_cache_manager_rejects_corrupted_file(tmp_path: Path, test_db: Database) -> None:
    cache_mgr = CacheManager(cache_dir=tmp_path / "cache")
    spec = test_db.get_dependency_spec("whatevergreen")
    assert spec is not None

    temp_file = tmp_path / "corrupt.zip"
    temp_file.write_bytes(b"Corrupted content")

    # put_artifact with non-matching hash raises ChecksumMismatchError
    with pytest.raises(ChecksumMismatchError):
        cache_mgr.put_artifact(spec, ArtifactVariant.RELEASE, temp_file)


def test_cache_clear(tmp_path: Path) -> None:
    cache_mgr = CacheManager(cache_dir=tmp_path / "cache")
    dummy_file = cache_mgr.downloads_dir / "test.zip"
    dummy_file.write_bytes(b"data")

    assert cache_mgr.get_cache_stats()["total_files"] == 1
    cache_mgr.clear_cache()
    assert cache_mgr.get_cache_stats()["total_files"] == 0
