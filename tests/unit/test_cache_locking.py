"""Deterministic unit tests for ownership-safe cache locking and liveness checking."""

import json
import os
from pathlib import Path
import socket
import time
import pytest

from macloader.dependencies.cache import CacheManager, is_process_alive


def _write_lock(lock_path: Path, pid: int, token: str, age_seconds: float = 0.0, hostname: str = socket.gethostname()) -> None:
    data = {
        "pid": pid,
        "token": token,
        "hostname": hostname,
        "acquired_at": time.time() - age_seconds,
    }
    lock_path.write_text(json.dumps(data), encoding="utf-8")


def test_is_process_alive_helper() -> None:
    # Current process is definitely alive
    assert is_process_alive(os.getpid()) is True
    # Non-positive PIDs are not alive
    assert is_process_alive(0) is False
    assert is_process_alive(-1) is False
    # Extremely large PID (overflow check)
    assert is_process_alive(2147483648) is False
    # Far-fetched PID that does not exist
    assert is_process_alive(9999999) is False


def test_lock_live_owner_past_stale_threshold_is_not_stolen(tmp_path: Path) -> None:
    """A long-running live owner must not lose its lock after 5 minutes (or 10,000s)."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_file = cache_dir / ".cache.lock"

    # Pre-create lock held by live PID 4242, acquired 10,000 seconds ago (old threshold was 300s)
    _write_lock(lock_file, pid=4242, token="live_token_123", age_seconds=10000.0)

    # Attempting to initialize CacheManager or acquire lock with live owner raises TimeoutError
    with pytest.raises(TimeoutError, match="Timed out waiting for dependency cache lock"):
        CacheManager(
            cache_dir=cache_dir,
            lock_timeout=0.1,
            poll_interval=0.02,
            liveness_check=lambda pid: True,
        )

    # The lock must NOT have been stolen or replaced
    assert lock_file.exists()
    content = json.loads(lock_file.read_text(encoding="utf-8"))
    assert content["token"] == "live_token_123"
    assert content["pid"] == 4242


def test_lock_dead_owner_is_safely_reclaimed(tmp_path: Path) -> None:
    """When the recorded owner PID is proven dead, the lock is safely reclaimed."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_file = cache_dir / ".cache.lock"

    # Initialize manager first
    mgr = CacheManager(
        cache_dir=cache_dir,
        lock_timeout=1.0,
        poll_interval=0.02,
        liveness_check=lambda pid: False,
    )

    # Now write a dead lock file held by PID 99999
    _write_lock(lock_file, pid=99999, token="dead_token_999", age_seconds=10.0)

    acquired = False
    with mgr._locked():
        acquired = True
        # Verify the lock now belongs to this process
        content = json.loads(lock_file.read_text(encoding="utf-8"))
        assert content["pid"] == os.getpid()
        assert content["token"] != "dead_token_999"

    assert acquired is True
    # Clean release upon normal exit
    assert not lock_file.exists()


def test_corrupted_or_empty_lock_reclaimed_after_grace_period(tmp_path: Path) -> None:
    """A 0-byte or unparseable lock file older than grace period is safely reclaimed."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_file = cache_dir / ".cache.lock"

    # Initialize manager
    mgr = CacheManager(cache_dir=cache_dir, lock_timeout=1.0, poll_interval=0.02)

    # Create a 0-byte corrupt lock file with mtime in the past
    lock_file.write_bytes(b"")
    past_time = time.time() - 10.0
    os.utime(lock_file, (past_time, past_time))

    acquired = False
    with mgr._locked():
        acquired = True
        assert lock_file.exists()
        content = json.loads(lock_file.read_text(encoding="utf-8"))
        assert content["pid"] == os.getpid()

    assert acquired is True
    assert not lock_file.exists()


def test_remote_host_lease_expiry(tmp_path: Path) -> None:
    """Locks from remote hosts expire after remote_lease_seconds."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_file = cache_dir / ".cache.lock"

    mgr = CacheManager(
        cache_dir=cache_dir,
        lock_timeout=0.1,
        poll_interval=0.02,
        remote_lease_seconds=50.0,
    )

    # Remote lock within lease: must NOT be stolen (TimeoutError)
    _write_lock(lock_file, pid=123, token="remote_tok", age_seconds=10.0, hostname="other-node.internal")
    with pytest.raises(TimeoutError):
        with mgr._locked():
            pass

    # Remote lock past lease: must be reclaimed
    _write_lock(lock_file, pid=123, token="stale_remote_tok", age_seconds=100.0, hostname="other-node.internal")
    with mgr._locked(timeout=1.0):
        content = json.loads(lock_file.read_text(encoding="utf-8"))
        assert content["token"] != "stale_remote_tok"


def test_former_owner_cannot_release_replacement_owner_lock(tmp_path: Path) -> None:
    """A former owner cannot release a replacement owner's lock."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_file = cache_dir / ".cache.lock"

    mgr = CacheManager(cache_dir=cache_dir, lock_timeout=1.0, poll_interval=0.02)

    cm = mgr._locked()
    # Owner A acquires the lock
    cm.__enter__()
    assert lock_file.exists()

    # Simulate lock being stolen/replaced by Owner B with a different token
    replacement_token = "replacement_owner_token_B"
    _write_lock(lock_file, pid=8888, token=replacement_token, age_seconds=0.0)

    # Owner A exits its context manager
    cm.__exit__(None, None, None)

    # The lock must NOT have been unlinked by Owner A!
    assert lock_file.exists()
    content = json.loads(lock_file.read_text(encoding="utf-8"))
    assert content["token"] == replacement_token
    assert content["pid"] == 8888


def test_atomic_reclaim_restores_live_lock_on_race(tmp_path: Path) -> None:
    """Atomic reclaim restores the lock if another owner took it before replacement."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_file = cache_dir / ".cache.lock"

    mgr = CacheManager(cache_dir=cache_dir)
    # Lock is currently held by Token X
    _write_lock(lock_file, pid=111, token="token_X")

    # If reclaim expected "dead_token_Y", it must return False and restore Token X
    result = mgr._atomic_reclaim(expected_token="dead_token_Y")
    assert result is False
    assert lock_file.exists()
    content = json.loads(lock_file.read_text(encoding="utf-8"))
    assert content["token"] == "token_X"


def test_non_blocking_zero_timeout(tmp_path: Path) -> None:
    """Zero timeout succeeds when lock is free and immediately raises when contested."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    mgr = CacheManager(cache_dir=cache_dir)
    # Succeeds when free
    with mgr._locked(timeout=0.0):
        # Nested attempt with timeout=0.0 immediately raises TimeoutError
        with pytest.raises(TimeoutError):
            with mgr._locked(timeout=0.0):
                pass


def test_contention_timeout_when_lock_held_by_live_process(tmp_path: Path) -> None:
    """Contention timeout is properly enforced when a live owner holds the lock."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    mgr1 = CacheManager(cache_dir=cache_dir, lock_timeout=1.0, poll_interval=0.02)
    mgr2 = CacheManager(
        cache_dir=cache_dir,
        lock_timeout=0.1,
        poll_interval=0.02,
        liveness_check=lambda pid: True,
    )

    with mgr1._locked():
        # While mgr1 holds lock, mgr2 must time out
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            with mgr2._locked():
                pass
        elapsed = time.monotonic() - start
        assert elapsed >= 0.1

    # After mgr1 releases, mgr2 can acquire without error
    with mgr2._locked():
        assert (cache_dir / ".cache.lock").exists()

    assert not (cache_dir / ".cache.lock").exists()
