"""Unit tests for S07 per-artifact concurrency, lifecycle coordination, and temp file safety."""

import hashlib
import multiprocessing
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, Optional
import pytest

from macloader.dependencies.cache import CacheManager, compute_file_sha256
from macloader.dependencies.downloader import Downloader
from macloader.domain.dependencies import ArtifactVariant, DependencyArtifact, DependencySpec
from macloader.exceptions import ArtifactDownloadError


def _make_spec(dep_id: str, version: str = "1.0.0", content: bytes = b"dummy_content", variant: ArtifactVariant = ArtifactVariant.RELEASE) -> DependencySpec:
    sha = hashlib.sha256(content).hexdigest()
    art = DependencyArtifact(
        asset_name=f"{dep_id}-{version}.zip",
        source_url=f"https://github.com/example/{dep_id}/releases/download/v{version}/{dep_id}-{version}.zip",
        sha256=sha,
        size_bytes=len(content),
        variant=variant,
        archive_type="zip",
    )
    return DependencySpec(
        id=dep_id,
        project_name=dep_id.title(),
        upstream_repository=f"https://github.com/example/{dep_id}",
        license="BSD-3-Clause",
        version=version,
        release_tag=f"v{version}",
        artifacts={variant.value: art},
    )


def test_concurrent_misses_same_artifact_single_download(tmp_path: Path) -> None:
    """Two concurrent misses for the same artifact result in exactly one download and both callers receiving valid path."""
    cache_dir = tmp_path / "cache"
    mgr = CacheManager(cache_dir=cache_dir)
    content = b"artifact_binary_data_12345"
    spec = _make_spec("opencorepkg", version="1.0.0", content=content)

    download_count = 0
    count_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def slow_transport(url: str, dest: Path) -> None:
        nonlocal download_count
        with count_lock:
            download_count += 1
        time.sleep(0.15)
        dest.write_bytes(content)

    downloader = Downloader(transport=slow_transport)

    results: Dict[str, Optional[Path]] = {"t1": None, "t2": None}
    errors: Dict[str, Optional[Exception]] = {"t1": None, "t2": None}

    def worker(worker_id: str) -> None:
        try:
            barrier.wait(timeout=5.0)
            target = mgr.acquire_artifact(spec, ArtifactVariant.RELEASE, downloader)
            results[worker_id] = target
        except Exception as e:
            errors[worker_id] = e

    t1 = threading.Thread(target=worker, args=("t1",))
    t2 = threading.Thread(target=worker, args=("t2",))

    t1.start()
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    assert errors["t1"] is None
    assert errors["t2"] is None
    res_t1 = results["t1"]
    res_t2 = results["t2"]
    assert res_t1 is not None
    assert res_t2 is not None
    assert res_t1 == res_t2
    assert res_t1.is_file()
    assert compute_file_sha256(res_t1) == hashlib.sha256(content).hexdigest()
    assert download_count == 1


def test_concurrent_acquisition_different_artifacts_progress_independently(tmp_path: Path) -> None:
    """Acquisition of different artifacts progresses concurrently without lock contention."""
    cache_dir = tmp_path / "cache"
    mgr = CacheManager(cache_dir=cache_dir)

    content_a = b"content_for_artifact_A"
    content_b = b"content_for_artifact_B"
    spec_a = _make_spec("lilu", "1.6.8", content_a)
    spec_b = _make_spec("virtualsmc", "1.3.3", content_b)

    active_downloads = 0
    max_concurrent = 0
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def transport_a(url: str, dest: Path) -> None:
        nonlocal active_downloads, max_concurrent
        with lock:
            active_downloads += 1
            if active_downloads > max_concurrent:
                max_concurrent = active_downloads
        time.sleep(0.15)
        dest.write_bytes(content_a)
        with lock:
            active_downloads -= 1

    def transport_b(url: str, dest: Path) -> None:
        nonlocal active_downloads, max_concurrent
        with lock:
            active_downloads += 1
            if active_downloads > max_concurrent:
                max_concurrent = active_downloads
        time.sleep(0.15)
        dest.write_bytes(content_b)
        with lock:
            active_downloads -= 1

    downloader_a = Downloader(transport=transport_a)
    downloader_b = Downloader(transport=transport_b)

    results: Dict[str, Optional[Path]] = {}

    def worker_a() -> None:
        barrier.wait(timeout=5.0)
        results["a"] = mgr.acquire_artifact(spec_a, ArtifactVariant.RELEASE, downloader_a)

    def worker_b() -> None:
        barrier.wait(timeout=5.0)
        results["b"] = mgr.acquire_artifact(spec_b, ArtifactVariant.RELEASE, downloader_b)

    t1 = threading.Thread(target=worker_a)
    t2 = threading.Thread(target=worker_b)

    t1.start()
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    path_a = results.get("a")
    path_b = results.get("b")
    assert path_a is not None and path_a.is_file()
    assert path_b is not None and path_b.is_file()
    assert max_concurrent == 2


def test_failed_owner_released_and_retried_safely(tmp_path: Path) -> None:
    """When the first owner fails or is cancelled, lock is released and subsequent caller retries cleanly."""
    cache_dir = tmp_path / "cache"
    mgr = CacheManager(cache_dir=cache_dir)
    content = b"reliable_data"
    spec = _make_spec("whatevergreen", version="1.6.6", content=content)

    call_count = 0

    def failing_transport(url: str, dest: Path) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError("Simulated connection dropped during transfer")
        dest.write_bytes(content)

    downloader = Downloader(transport=failing_transport)

    # First attempt fails
    with pytest.raises(ArtifactDownloadError, match="Failed to download"):
        mgr.acquire_artifact(spec, ArtifactVariant.RELEASE, downloader)

    # Cache must still be clean and unlocked
    assert not mgr.has_valid_artifact(spec, ArtifactVariant.RELEASE)
    artifact_lock = mgr.get_artifact_lock_file(spec, ArtifactVariant.RELEASE)
    assert not artifact_lock.exists()

    # Second attempt succeeds without corrupted state
    path = mgr.acquire_artifact(spec, ArtifactVariant.RELEASE, downloader)
    assert path.is_file()
    assert compute_file_sha256(path) == hashlib.sha256(content).hexdigest()
    assert call_count == 2


def test_cancelled_waiting_caller_aborts_cleanly(tmp_path: Path) -> None:
    """A caller waiting for a per-artifact lock aborts immediately if its cancel condition triggers."""
    cache_dir = tmp_path / "cache"
    mgr = CacheManager(cache_dir=cache_dir, poll_interval=0.01)
    content = b"data_for_cancel_test"
    spec = _make_spec("applealc", version="1.8.8", content=content)

    download_started = threading.Event()

    def slow_transport(url: str, dest: Path) -> None:
        download_started.set()
        time.sleep(0.3)
        dest.write_bytes(content)

    downloader = Downloader(transport=slow_transport)

    cancel_flag = False

    def cancel_check() -> bool:
        return cancel_flag

    def slow_worker() -> None:
        mgr.acquire_artifact(spec, ArtifactVariant.RELEASE, downloader)

    t1 = threading.Thread(target=slow_worker)
    t1.start()

    assert download_started.wait(timeout=5.0)

    # t2 tries to acquire but cancels while waiting
    t2_error: Optional[Exception] = None

    def waiting_worker() -> None:
        nonlocal t2_error
        try:
            mgr.acquire_artifact(spec, ArtifactVariant.RELEASE, downloader, cancel=cancel_check)
        except Exception as e:
            t2_error = e

    t2 = threading.Thread(target=waiting_worker)
    t2.start()

    time.sleep(0.05)
    cancel_flag = True

    t2.join(timeout=5.0)
    t1.join(timeout=5.0)

    assert isinstance(t2_error, ArtifactDownloadError)
    assert "cancelled" in str(t2_error).lower()
    # First caller succeeded in publishing
    assert mgr.has_valid_artifact(spec, ArtifactVariant.RELEASE)


def test_active_temp_files_protected_from_clear_cache(tmp_path: Path) -> None:
    """Active .part and .tmp files are preserved during clear_cache(keep_active_downloads=True)."""
    cache_dir = tmp_path / "cache"
    mgr = CacheManager(cache_dir=cache_dir)
    downloads = cache_dir / "downloads"

    # Create an old published artifact that SHOULD be cleared
    published = downloads / "old_published_artifact.zip"
    published.write_bytes(b"published_data")

    # Create an active in-flight .part file
    active_part = downloads / ".opencore_download.zip.12345.part"
    active_part.write_bytes(b"partial_in_flight_download")

    # Clear cache
    mgr.clear_cache(keep_active_downloads=True)

    # Published artifact is deleted
    assert not published.exists()
    # Active temporary part file is protected
    assert active_part.exists()


def test_orphan_cleanup_protects_active_and_removes_stale(tmp_path: Path) -> None:
    """_cleanup_orphans preserves recently modified or locked temp files and removes stale ones."""
    cache_dir = tmp_path / "cache"
    mgr = CacheManager(cache_dir=cache_dir)
    downloads = cache_dir / "downloads"

    # 1. Stale orphan (>60s old, no lock)
    stale_orphan = downloads / ".stale_orphan.tmp"
    stale_orphan.write_bytes(b"stale")
    past_time = time.time() - 3600.0
    os.utime(stale_orphan, (past_time, past_time))

    # 2. Recent active temp file (<60s)
    recent_active = downloads / ".recent_active.part"
    recent_active.write_bytes(b"recent")

    # Run cleanup
    mgr._cleanup_orphans(max_age_seconds=60.0)

    assert not stale_orphan.exists()
    assert recent_active.exists()


def test_is_temp_file_active_with_stale_mtime_and_live_lock(tmp_path: Path) -> None:
    """Active .part files older than 60s are preserved if an active artifact lock exists, and deleted when lock dies."""
    import json
    import socket

    cache_dir = tmp_path / "cache"
    alive_pids = {4242}
    mgr = CacheManager(cache_dir=cache_dir, liveness_check=lambda pid: pid in alive_pids)
    downloads = cache_dir / "downloads"

    spec = _make_spec("opencorepkg", version="1.0.0", content=b"data")
    lock_file = mgr.get_artifact_lock_file(spec, ArtifactVariant.RELEASE)
    lock_meta = {
        "pid": 4242,
        "token": "active_token_4242",
        "hostname": socket.gethostname(),
        "acquired_at": time.time(),
        "artifact_id": spec.id,
        "version": spec.version,
        "variant": ArtifactVariant.RELEASE.value,
    }
    lock_file.write_text(json.dumps(lock_meta), encoding="utf-8")

    # Create an in-flight .part file with mtime in the past (>60s)
    target_path = mgr.get_artifact_cache_path(spec, ArtifactVariant.RELEASE)
    part_file = downloads / f".{target_path.name}.test.part"
    part_file.write_bytes(b"partial_stale_data")
    past_time = time.time() - 3600.0
    os.utime(part_file, (past_time, past_time))

    # 1. With live lock owner, file is considered active and protected from cleanup
    assert mgr.is_temp_file_active(part_file, max_age_seconds=60.0) is True
    mgr._cleanup_orphans(max_age_seconds=60.0)
    assert part_file.exists()

    # 2. When the lock owner process dies, file is no longer active and is cleaned up
    alive_pids.clear()
    assert mgr.is_temp_file_active(part_file, max_age_seconds=60.0) is False
    mgr._cleanup_orphans(max_age_seconds=60.0)
    assert not part_file.exists()


def test_active_download_streaming_cancellation(tmp_path: Path) -> None:
    """Cancellation requested during streaming download immediately raises ArtifactDownloadError."""
    target_file = tmp_path / "target.zip"
    art = DependencyArtifact(
        asset_name="cancel_test.zip",
        source_url="https://github.com/example/test/releases/download/v1/test.zip",
        sha256="abc",
        size_bytes=1000,
    )

    downloader = Downloader()
    with pytest.raises(ArtifactDownloadError, match="cancelled"):
        downloader.download_artifact(art, target_file, cancel=lambda: True)


def test_prune_cache_synchronizes_index(tmp_path: Path) -> None:
    """_prune_cache_locked removes unlinked artifacts from index.json."""
    cache_dir = tmp_path / "cache"
    # Small cache size (150 bytes)
    mgr = CacheManager(cache_dir=cache_dir, max_cache_bytes=150)

    spec1 = _make_spec("dep1", "1.0", content=b"A" * 100)
    spec2 = _make_spec("dep2", "1.0", content=b"B" * 100)

    def _write_a(url: str, p: Path) -> None:
        p.write_bytes(b"A" * 100)

    def _write_b(url: str, p: Path) -> None:
        p.write_bytes(b"B" * 100)

    d1 = Downloader(transport=_write_a)
    d2 = Downloader(transport=_write_b)

    p1 = mgr.acquire_artifact(spec1, ArtifactVariant.RELEASE, d1)
    time.sleep(0.05)
    # Adding spec2 exceeds 150 bytes; p1 (older) should be pruned
    p2 = mgr.acquire_artifact(spec2, ArtifactVariant.RELEASE, d2)

    index = mgr._load_index()
    key1 = f"{spec1.id}:{spec1.version}:{ArtifactVariant.RELEASE.value}"
    key2 = f"{spec2.id}:{spec2.version}:{ArtifactVariant.RELEASE.value}"

    assert key2 in index
    assert key1 not in index
    assert not p1.exists()
    assert p2.exists()


def _mp_worker_task(cache_dir_str: str, spec_dict: Dict[str, Any], content: bytes, barrier: Any, queue: Any) -> None:
    """Top-level worker function for multiprocessing test."""
    try:
        mgr = CacheManager(cache_dir=Path(cache_dir_str), poll_interval=0.02)
        spec = DependencySpec.from_dict(spec_dict)

        def slow_transport(url: str, dest: Path) -> None:
            queue.put(("DOWNLOAD_STARTED", os.getpid()))
            time.sleep(0.2)
            dest.write_bytes(content)

        downloader = Downloader(transport=slow_transport)
        barrier.wait(timeout=10.0)
        acquired_path = mgr.acquire_artifact(spec, ArtifactVariant.RELEASE, downloader)
        queue.put(("SUCCESS", str(acquired_path), compute_file_sha256(acquired_path)))
    except Exception as e:
        queue.put(("ERROR", str(e), ""))


def test_multiprocess_acquisition_same_artifact(tmp_path: Path) -> None:
    """Real separate OS processes acquiring the same artifact concurrently coordinate safely."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    content = b"multiprocess_test_data_999"
    spec = _make_spec("multiprocdep", version="2.0.0", content=content)

    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    queue = ctx.Queue()

    p1 = ctx.Process(
        target=_mp_worker_task,
        args=(str(cache_dir), spec.to_dict(), content, barrier, queue),
    )
    p2 = ctx.Process(
        target=_mp_worker_task,
        args=(str(cache_dir), spec.to_dict(), content, barrier, queue),
    )

    try:
        p1.start()
        p2.start()

        p1.join(timeout=15.0)
        p2.join(timeout=15.0)

        assert p1.exitcode == 0
        assert p2.exitcode == 0

        # We expect 1 DOWNLOAD_STARTED and 2 SUCCESS results
        events = []
        for _ in range(3):
            events.append(queue.get(timeout=5.0))

        download_events = [e for e in events if e[0] == "DOWNLOAD_STARTED"]
        success_events = [e for e in events if e[0] == "SUCCESS"]

        assert len(download_events) == 1, f"Expected exactly 1 download across processes, got {len(download_events)}"
        assert len(success_events) == 2, f"Expected both processes to succeed, got {len(success_events)}"

        for status, path_str, sha in success_events:
            assert Path(path_str).is_file()
            assert sha == hashlib.sha256(content).hexdigest()
    finally:
        for p in (p1, p2):
            if p.is_alive():
                p.terminate()
                p.join()

