"""
Tests for G3: Instance Lock — Prevent Duplicate Runtime.

Covers:
- Lock acquired successfully on first attempt
- Second acquisition fails (instance already running)
- Stale lock (dead PID) is recovered automatically
- Lock release allows re-acquisition
- Race condition: atomic creation prevents double-acquire
- Lock file contains valid JSON with PID
- Lock release only removes own lock
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.runtime.instance_lock import (
    acquire_instance_lock,
    release_instance_lock,
    is_lock_stale,
    _is_pid_alive,
)


# ─── FIXTURES ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def use_temp_lock(tmp_path):
    """Redirect lock file to temp directory and reset state."""
    lock_file = str(tmp_path / "trading.lock")
    import core.runtime.instance_lock as mod
    mod._lock_acquired = False
    with patch("core.runtime.instance_lock._get_lock_path", return_value=lock_file):
        yield lock_file
    # Cleanup
    mod._lock_acquired = False
    if os.path.exists(lock_file):
        os.remove(lock_file)


# ─── TEST: SUCCESSFUL ACQUISITION ─────────────────────────────────────────────

class TestAcquireLock:
    def test_first_acquire_succeeds(self, use_temp_lock):
        """First lock acquisition returns True."""
        result = acquire_instance_lock()
        assert result is True

    def test_lock_file_created(self, use_temp_lock):
        """Lock file is created on successful acquisition."""
        acquire_instance_lock()
        assert os.path.exists(use_temp_lock)

    def test_lock_file_contains_pid(self, use_temp_lock):
        """Lock file contains current process PID."""
        acquire_instance_lock()
        with open(use_temp_lock, "r") as f:
            data = json.load(f)
        assert data["pid"] == os.getpid()
        assert "started_at" in data
        assert "hostname" in data

    def test_lock_file_valid_json(self, use_temp_lock):
        """Lock file is valid JSON."""
        acquire_instance_lock()
        with open(use_temp_lock, "r") as f:
            data = json.load(f)
        assert isinstance(data, dict)


# ─── TEST: SECOND ACQUISITION BLOCKED ─────────────────────────────────────────

class TestBlockDuplicate:
    def test_second_acquire_fails(self, use_temp_lock):
        """Second acquisition attempt returns False (blocked)."""
        # First acquire
        assert acquire_instance_lock() is True

        # Reset internal flag to simulate a different process
        import core.runtime.instance_lock as mod
        mod._lock_acquired = False

        # Second acquire — lock file exists, PID alive (it's us)
        result = acquire_instance_lock()
        assert result is False

    def test_existing_lock_with_alive_pid_blocks(self, use_temp_lock):
        """If lock file has a live PID, acquisition is blocked."""
        # Write lock with current PID (alive)
        with open(use_temp_lock, "w") as f:
            json.dump({"pid": os.getpid(), "started_at": 1000}, f)

        result = acquire_instance_lock()
        assert result is False


# ─── TEST: STALE LOCK RECOVERY ─────────────────────────────────────────────────

class TestStaleLock:
    def test_stale_lock_detected(self, use_temp_lock):
        """Lock file with dead PID is detected as stale."""
        # PID 99999999 is almost certainly not alive
        with open(use_temp_lock, "w") as f:
            json.dump({"pid": 99999999, "started_at": 1000}, f)

        with patch("core.runtime.instance_lock._is_pid_alive", return_value=False):
            assert is_lock_stale() is True

    def test_stale_lock_recovered(self, use_temp_lock):
        """Stale lock is removed and new lock acquired."""
        # Create stale lock
        with open(use_temp_lock, "w") as f:
            json.dump({"pid": 99999999, "started_at": 1000}, f)

        with patch("core.runtime.instance_lock._is_pid_alive", return_value=False):
            result = acquire_instance_lock()

        assert result is True
        # Verify new lock has our PID
        with open(use_temp_lock, "r") as f:
            data = json.load(f)
        assert data["pid"] == os.getpid()

    def test_corrupted_lock_file_treated_as_stale(self, use_temp_lock):
        """Unreadable lock file is treated as stale and recovered."""
        with open(use_temp_lock, "w") as f:
            f.write("{{not valid json")

        result = acquire_instance_lock()
        assert result is True


# ─── TEST: RELEASE ─────────────────────────────────────────────────────────────

class TestReleaseLock:
    def test_release_removes_file(self, use_temp_lock):
        """Release removes the lock file."""
        acquire_instance_lock()
        assert os.path.exists(use_temp_lock)

        release_instance_lock()
        assert not os.path.exists(use_temp_lock)

    def test_release_allows_reacquire(self, use_temp_lock):
        """After release, lock can be acquired again."""
        acquire_instance_lock()
        release_instance_lock()

        result = acquire_instance_lock()
        assert result is True

    def test_release_without_acquire_is_safe(self, use_temp_lock):
        """Releasing without prior acquire does not crash."""
        release_instance_lock()  # Should not raise

    def test_release_only_removes_own_lock(self, use_temp_lock):
        """Release does not remove lock owned by different PID."""
        # Write lock with different PID
        with open(use_temp_lock, "w") as f:
            json.dump({"pid": 99999, "started_at": 1000}, f)

        import core.runtime.instance_lock as mod
        mod._lock_acquired = True  # Pretend we acquired

        release_instance_lock()
        # File should still exist (different PID)
        assert os.path.exists(use_temp_lock)


# ─── TEST: RACE CONDITION ──────────────────────────────────────────────────────

class TestRaceCondition:
    def test_atomic_create_prevents_race(self, use_temp_lock):
        """If another process creates the file between check and create, we fail safely."""
        # Simulate: file doesn't exist during check, but appears during create
        original_open = os.open

        call_count = [0]
        def _racing_open(path, flags, *args, **kwargs):
            if "trading.lock" in path and (flags & os.O_EXCL):
                call_count[0] += 1
                if call_count[0] == 1:
                    # First time: simulate race — file already exists
                    raise FileExistsError("Race condition")
            return original_open(path, flags, *args, **kwargs)

        with patch("core.runtime.instance_lock.os.open", side_effect=_racing_open):
            with patch("os.path.exists", return_value=False):
                result = acquire_instance_lock()

        assert result is False


# ─── TEST: PID ALIVE CHECK ─────────────────────────────────────────────────────

class TestPidAlive:
    def test_current_pid_is_alive(self):
        """Current process PID reports as alive."""
        assert _is_pid_alive(os.getpid()) is True

    def test_zero_pid_is_not_alive(self):
        """PID 0 reports as not alive."""
        assert _is_pid_alive(0) is False

    def test_negative_pid_is_not_alive(self):
        """Negative PID reports as not alive."""
        assert _is_pid_alive(-1) is False
