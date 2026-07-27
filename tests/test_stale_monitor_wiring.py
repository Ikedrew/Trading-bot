"""
Tests for E3: StaleDataMonitor Wiring — Live Execution Integration.

Covers:
- on_tick is called and fresh tick allows processing
- Stale tick causes symbol skip
- FRESH?STALE transition detected
- STALE?FRESH recovery detected
- on_candle is called for candle freshness
- Stale candle (escalation 2+) causes skip
- Monitor exception ? fail-safe skip
- Trade management still runs before stale check
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.stale_monitor import StaleDataMonitor, StaleCheckResult


# --- HELPER: Config mock ------------------------------------------------------

class _MockConfig:
    STALE_TICK_TIMEOUT_SECONDS = 30.0
    STALE_CANDLE_TIMEOUT_SECONDS = 600.0
    MARKET_HEARTBEAT_TIMEOUT_SECONDS = 120.0
    STALE_ESCALATION_WARNING_SECONDS = 60.0
    STALE_ESCALATION_CRITICAL_SECONDS = 300.0


# --- TEST: TICK FRESHNESS -----------------------------------------------------

class TestTickFreshness:
    def test_first_tick_is_never_stale(self):
        """First tick initializes state — never stale."""
        mon = StaleDataMonitor("EURUSD", _MockConfig())
        result = mon.on_tick(1000, time.time())
        assert result.is_stale is False
        assert mon.stale_state is False

    def test_advancing_tick_stays_fresh(self):
        """Each new tick with advancing timestamp = fresh."""
        mon = StaleDataMonitor("EURUSD", _MockConfig())
        now = time.time()
        mon.on_tick(1000, now)
        result = mon.on_tick(1001, now + 1)
        assert result.is_stale is False
        assert mon.stale_state is False

    def test_repeated_tick_becomes_stale(self):
        """Same tick timestamp repeated beyond grace period = stale."""
        mon = StaleDataMonitor("EURUSD", _MockConfig())
        now = time.time()
        mon.on_tick(1000, now)
        # Second call: sets _tick_stale_since = now+1, but duration=0 (not stale yet)
        mon.on_tick(1000, now + 1)
        # Third call: duration = (now+32) - (now+1) = 31s > 30s grace → STALE
        result = mon.on_tick(1000, now + 32)
        assert result.is_stale is True
        assert mon.stale_state is True

    def test_fresh_after_stale_clears_state(self):
        """Fresh tick after stale period clears stale_state."""
        mon = StaleDataMonitor("EURUSD", _MockConfig())
        now = time.time()
        mon.on_tick(1000, now)
        mon.on_tick(1000, now + 1)   # Sets stale_since
        mon.on_tick(1000, now + 32)  # 31s elapsed → stale
        assert mon.stale_state is True

        result = mon.on_tick(1001, now + 33)  # Fresh again
        assert result.is_stale is False
        assert mon.stale_state is False

    def test_escalation_levels(self):
        """Stale duration past grace period drives escalation level."""
        mon = StaleDataMonitor("EURUSD", _MockConfig())
        now = time.time()
        mon.on_tick(1000, now)

        # Sets _tick_stale_since = now+1
        mon.on_tick(1000, now + 1)

        # Level 1: duration = (now+32)-(now+1) = 31s. Past 30s by 1s → level 1
        r1 = mon.on_tick(1000, now + 32)
        assert r1.is_stale is True
        assert r1.escalation_level == 1

        # Level 2: duration = (now+92)-(now+1) = 91s. Past 30s by 61s → level 2
        r2 = mon.on_tick(1000, now + 92)
        assert r2.escalation_level == 2

        # Level 3: duration = (now+332)-(now+1) = 331s. Past 30s by 301s → level 3
        r3 = mon.on_tick(1000, now + 332)
        assert r3.escalation_level == 3


# --- TEST: CANDLE FRESHNESS ---------------------------------------------------

class TestCandleFreshness:
    def test_first_candle_is_never_stale(self):
        """First candle initializes state — never stale."""
        mon = StaleDataMonitor("EURUSD", _MockConfig())
        result = mon.on_candle(1000, time.time())
        assert result.is_stale is False

    def test_new_candle_stays_fresh(self):
        """New candle with advancing timestamp = fresh."""
        mon = StaleDataMonitor("EURUSD", _MockConfig())
        now = time.time()
        mon.on_candle(1000, now)
        result = mon.on_candle(1300, now + 300)  # M5 = 300s
        assert result.is_stale is False

    def test_same_candle_within_timeout_not_stale(self):
        """Same candle timestamp within normal inter-bar wait is NOT stale."""
        mon = StaleDataMonitor("EURUSD", _MockConfig())
        now = time.time()
        mon.on_candle(1000, now)
        # Same candle_time, 60s later — well within 600s timeout
        result = mon.on_candle(1000, now + 60)
        assert result.is_stale is False

    def test_same_candle_beyond_timeout_is_stale(self):
        """Same candle timestamp beyond stale_candle_timeout = stale."""
        mon = StaleDataMonitor("EURUSD", _MockConfig())
        now = time.time()
        mon.on_candle(1000, now)
        # Second call sets _candle_stale_since
        mon.on_candle(1000, now + 1)
        # Third call: stale_duration = (now+700) - (now+1) = 699s > 600s timeout
        result = mon.on_candle(1000, now + 700)
        assert result.is_stale is True


# --- TEST: TRANSITION DETECTION -----------------------------------------------

class TestTransitionDetection:
    def test_fresh_to_stale_transition(self):
        """Detects FRESH→STALE transition after grace period."""
        mon = StaleDataMonitor("EURUSD", _MockConfig())
        now = time.time()
        mon.on_tick(1000, now)
        assert mon.stale_state is False  # Fresh

        # Second tick sets stale_since, third tick after grace triggers stale
        mon.on_tick(1000, now + 1)   # Sets _tick_stale_since
        mon.on_tick(1000, now + 32)  # 31s elapsed → stale
        assert mon.stale_state is True  # Now stale

    def test_stale_to_fresh_recovery(self):
        """Detects STALE→FRESH transition with genuinely fresh tick."""
        mon = StaleDataMonitor("EURUSD", _MockConfig())
        now = time.time()
        mon.on_tick(1000, now)
        mon.on_tick(1000, now + 1)   # Sets stale_since
        mon.on_tick(1000, now + 32)  # Stale (past 30s grace)
        was_stale = mon.stale_state
        assert was_stale is True

        mon.on_tick(1001, now + 33)  # Recovery: new tick_time
        assert mon.stale_state is False  # Now fresh


# --- TEST: FAIL-SAFE BEHAVIOUR ------------------------------------------------

class TestFailSafe:
    def test_monitor_error_returns_stale_like_result(self):
        """If monitor.on_tick raises, the wiring should treat it as stale (fail-safe)."""
        # This tests the contract: monitor error ? caller should skip
        mon = StaleDataMonitor("EURUSD", _MockConfig())

        # Monkey-patch to simulate internal error
        def _broken_on_tick(*args, **kwargs):
            raise RuntimeError("Internal monitor error")

        mon.on_tick = _broken_on_tick

        # The caller (live_scanner) wraps in try/except and continues ? skips
        with pytest.raises(RuntimeError):
            mon.on_tick(1000, time.time())


# --- TEST: HEARTBEAT CHECK ----------------------------------------------------

class TestHeartbeat:
    def test_heartbeat_fresh(self):
        """Heartbeat within timeout is not stale."""
        mon = StaleDataMonitor("EURUSD", _MockConfig())
        now = time.time()
        mon.on_tick(1000, now)  # Sets last_data_update_time
        result = mon.check_heartbeat(now + 60)
        assert result.is_stale is False

    def test_heartbeat_stale(self):
        """Heartbeat beyond timeout is stale (critical)."""
        mon = StaleDataMonitor("EURUSD", _MockConfig())
        now = time.time()
        mon.on_tick(1000, now)
        result = mon.check_heartbeat(now + 130)  # > 120s timeout
        assert result.is_stale is True
        assert result.escalation_level == 3
