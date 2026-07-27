"""
Tests for E2: Process Watchdog / Heartbeat System.

Covers:
- Heartbeat file written correctly
- Heartbeat timestamp updated
- Heartbeat pid recorded
- Watchdog: healthy heartbeat = no restart
- Watchdog: stale heartbeat = restart triggered
- Watchdog: missing heartbeat = restart triggered
- Watchdog: shutdown status = no restart
- Crash-loop protection: restart limit enforced
- Crash-loop protection: lockout activated
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.heartbeat import (
    write_heartbeat,
    read_heartbeat,
    get_heartbeat_age,
    STATUS_RUNNING,
    STATUS_SHUTDOWN,
    STATUS_STARTING,
    STATUS_DEGRADED,
)
from core.watchdog import (
    ProcessWatchdog,
    WatchdogConfig,
    RestartEvent,
)


# ─── FIXTURES ─────────────────────────────────────────────────────────────────

@pytest.fixture
def hb_path(tmp_path):
    """Provide a temp heartbeat file path."""
    return tmp_path / "heartbeat.json"


@pytest.fixture
def watchdog_cfg(hb_path):
    """Create a watchdog config pointing to temp heartbeat."""
    return WatchdogConfig(
        heartbeat_file=str(hb_path),
        poll_interval_seconds=1.0,
        stale_threshold_seconds=5.0,
        max_restarts_per_hour=3,
        bot_start_command=["python", "-c", "import time; time.sleep(1)"],
    )


# ─── TEST: HEARTBEAT FILE WRITTEN ─────────────────────────────────────────────

class TestHeartbeatWrite:
    def test_heartbeat_written(self, hb_path):
        """Heartbeat file is created with correct structure."""
        with patch("core.heartbeat._get_heartbeat_path", return_value=hb_path):
            result = write_heartbeat(status=STATUS_RUNNING, cycle_id=42, symbols=3)

        assert result is True
        assert hb_path.exists()

        data = json.loads(hb_path.read_text())
        assert data["status"] == "RUNNING"
        assert data["cycle_id"] == 42
        assert data["symbols"] == 3

    def test_timestamp_updated(self, hb_path):
        """Heartbeat timestamp is current."""
        before = time.time()
        with patch("core.heartbeat._get_heartbeat_path", return_value=hb_path):
            write_heartbeat(status=STATUS_RUNNING)
        after = time.time()

        data = json.loads(hb_path.read_text())
        assert before <= data["timestamp"] <= after

    def test_pid_recorded(self, hb_path):
        """Heartbeat includes current process PID."""
        with patch("core.heartbeat._get_heartbeat_path", return_value=hb_path):
            write_heartbeat(status=STATUS_RUNNING)

        data = json.loads(hb_path.read_text())
        assert data["pid"] == os.getpid()

    def test_shutdown_status_written(self, hb_path):
        """SHUTDOWN status written correctly."""
        with patch("core.heartbeat._get_heartbeat_path", return_value=hb_path):
            write_heartbeat(status=STATUS_SHUTDOWN)

        data = json.loads(hb_path.read_text())
        assert data["status"] == "SHUTDOWN"

    def test_disabled_heartbeat_skips(self, hb_path):
        """When disabled, no file is written."""
        with patch("core.heartbeat._get_heartbeat_path", return_value=hb_path), \
             patch("core.heartbeat._is_enabled", return_value=False):
            write_heartbeat(status=STATUS_RUNNING)

        assert not hb_path.exists()

    def test_read_heartbeat(self, hb_path):
        """read_heartbeat returns correct data."""
        with patch("core.heartbeat._get_heartbeat_path", return_value=hb_path):
            write_heartbeat(status=STATUS_RUNNING, cycle_id=99)
            data = read_heartbeat(hb_path)

        assert data is not None
        assert data["cycle_id"] == 99

    def test_heartbeat_age(self, hb_path):
        """get_heartbeat_age returns age in seconds."""
        with patch("core.heartbeat._get_heartbeat_path", return_value=hb_path):
            write_heartbeat(status=STATUS_RUNNING)
            age = get_heartbeat_age(hb_path)

        assert age is not None
        assert 0 <= age < 2.0  # Should be very fresh


# ─── TEST: WATCHDOG — HEALTHY ──────────────────────────────────────────────────

class TestWatchdogHealthy:
    def test_healthy_heartbeat_no_restart(self, hb_path, watchdog_cfg):
        """Fresh heartbeat → HEALTHY → no restart."""
        # Write fresh heartbeat
        hb_path.write_text(json.dumps({
            "timestamp": time.time(),
            "status": "RUNNING",
            "pid": 12345,
        }))

        wd = ProcessWatchdog(config=watchdog_cfg)
        health = wd.check_health()
        should, reason = wd.should_restart()

        assert health == "HEALTHY"
        assert should is False
        assert reason == "HEALTHY"


# ─── TEST: WATCHDOG — STALE ───────────────────────────────────────────────────

class TestWatchdogStale:
    def test_stale_heartbeat_triggers_restart(self, hb_path, watchdog_cfg):
        """Stale heartbeat (>threshold) → STALE → restart needed."""
        # Write old heartbeat (200s ago, threshold is 5s)
        hb_path.write_text(json.dumps({
            "timestamp": time.time() - 200,
            "status": "RUNNING",
            "pid": 12345,
        }))

        wd = ProcessWatchdog(config=watchdog_cfg)
        health = wd.check_health()
        should, reason = wd.should_restart()

        assert health == "STALE"
        assert should is True
        assert reason == "STALE"


# ─── TEST: WATCHDOG — MISSING ─────────────────────────────────────────────────

class TestWatchdogMissing:
    def test_missing_heartbeat_triggers_restart(self, hb_path, watchdog_cfg):
        """Missing heartbeat file → MISSING → restart needed."""
        # Don't create the file
        wd = ProcessWatchdog(config=watchdog_cfg)
        health = wd.check_health()
        should, reason = wd.should_restart()

        assert health == "MISSING"
        assert should is True
        assert reason == "MISSING"

    def test_corrupt_heartbeat_triggers_restart(self, hb_path, watchdog_cfg):
        """Corrupted heartbeat file → MISSING → restart needed."""
        hb_path.write_text("{{invalid json")

        wd = ProcessWatchdog(config=watchdog_cfg)
        health = wd.check_health()

        assert health == "MISSING"


# ─── TEST: WATCHDOG — SHUTDOWN ─────────────────────────────────────────────────

class TestWatchdogShutdown:
    def test_shutdown_status_no_restart(self, hb_path, watchdog_cfg):
        """SHUTDOWN status → do not restart."""
        hb_path.write_text(json.dumps({
            "timestamp": time.time() - 300,  # Old, but status=SHUTDOWN
            "status": "SHUTDOWN",
            "pid": 12345,
        }))

        wd = ProcessWatchdog(config=watchdog_cfg)
        health = wd.check_health()
        should, reason = wd.should_restart()

        assert health == "SHUTDOWN"
        assert should is False
        assert reason == "GRACEFUL_SHUTDOWN"


# ─── TEST: CRASH-LOOP PROTECTION ──────────────────────────────────────────────

class TestCrashLoopProtection:
    def test_restart_limit_enforced(self, hb_path, watchdog_cfg):
        """After MAX_RESTARTS_PER_HOUR restarts, lockout activates."""
        watchdog_cfg.max_restarts_per_hour = 3

        wd = ProcessWatchdog(config=watchdog_cfg)

        # Simulate 3 recent restarts
        now = time.time()
        wd._restart_timestamps.extend([now - 100, now - 50, now - 10])

        # Write stale heartbeat
        hb_path.write_text(json.dumps({
            "timestamp": time.time() - 200,
            "status": "RUNNING",
            "pid": 12345,
        }))

        should, reason = wd.should_restart()

        assert should is False
        assert reason == "LOCKOUT_ENTERED"
        assert wd.is_locked_out is True

    def test_lockout_persists(self, hb_path, watchdog_cfg):
        """Once locked out, subsequent checks remain blocked."""
        watchdog_cfg.max_restarts_per_hour = 2

        wd = ProcessWatchdog(config=watchdog_cfg)
        wd._lockout = True

        hb_path.write_text(json.dumps({
            "timestamp": time.time() - 200,
            "status": "RUNNING",
        }))

        health = wd.check_health()
        should, reason = wd.should_restart()

        assert health == "LOCKOUT"
        assert should is False
        assert reason == "LOCKOUT_ACTIVE"

    def test_old_restarts_expire(self, hb_path, watchdog_cfg):
        """Restarts older than 1 hour don't count toward limit."""
        watchdog_cfg.max_restarts_per_hour = 3

        wd = ProcessWatchdog(config=watchdog_cfg)

        # Simulate 3 restarts all >1hr ago
        old = time.time() - 4000
        wd._restart_timestamps.extend([old, old + 10, old + 20])

        assert wd.restart_count_last_hour == 0

    def test_restart_count_accurate(self, hb_path, watchdog_cfg):
        """restart_count_last_hour counts only recent restarts."""
        wd = ProcessWatchdog(config=watchdog_cfg)

        now = time.time()
        # 2 recent, 1 old
        wd._restart_timestamps.extend([
            now - 5000,  # Old (>1hr)
            now - 100,   # Recent
            now - 50,    # Recent
        ])

        assert wd.restart_count_last_hour == 2


# ─── TEST: RESTART EXECUTION ──────────────────────────────────────────────────

class TestRestartExecution:
    def test_restart_starts_new_process(self, hb_path, watchdog_cfg):
        """restart_bot() launches a new subprocess."""
        watchdog_cfg.bot_start_command = ["python", "-c", "pass"]

        wd = ProcessWatchdog(config=watchdog_cfg)

        # Write stale heartbeat for age calculation
        hb_path.write_text(json.dumps({"timestamp": time.time() - 100, "status": "RUNNING"}))

        result = wd.restart_bot(reason="STALE_HEARTBEAT")

        assert result is True
        assert wd._bot_process is not None
        assert wd.restart_count_last_hour == 1

        # Cleanup
        if wd._bot_process.poll() is None:
            wd._bot_process.terminate()
            wd._bot_process.wait(timeout=5)
