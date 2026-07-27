"""
Tests for A1: Daily Loss Limit Guard.

Covers:
- Below threshold → allowed
- At threshold → blocked
- Above threshold → blocked
- Equity unavailable → fail-closed (blocked)
- Persistence: save and restore state
- Restart after trigger: remains blocked
- New day: resets and allows trading
- Profit day: allowed throughout
- Guard disabled: always allows
- Triggered state persists to disk
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from risk.daily_loss_guard import (
    DailyLossGuard,
    DailyLossResult,
    REJECT_DAILY_LOSS_EXCEEDED,
    REJECT_EQUITY_UNAVAILABLE,
    _load_state,
    _persist_state,
    _DailyLossState,
    _today_str,
)


# ─── FIXTURES ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def use_temp_state(tmp_path):
    """Redirect state file to temp directory."""
    state_file = tmp_path / "daily_loss_state.json"
    with patch("risk.daily_loss_guard._get_state_path", return_value=state_file), \
         patch("risk.daily_loss_guard._is_enabled", return_value=True), \
         patch("risk.daily_loss_guard._get_threshold", return_value=4.0), \
         patch("risk.daily_loss_guard._get_reset_hour_utc", return_value=0):
        yield state_file


def _mock_equity(equity: float):
    """Create a mock MT5 account_info with given equity."""
    acct = MagicMock()
    acct.equity = equity
    return acct


# ─── TEST: THRESHOLD LOGIC ────────────────────────────────────────────────────

class TestThresholdLogic:
    def test_below_threshold_allowed(self, use_temp_state):
        """Daily loss below threshold → trading allowed."""
        with patch("risk.daily_loss_guard.mt5_call") as mock_call:
            # First call: establish baseline at 100000
            mock_call.return_value = _mock_equity(100000.0)
            guard = DailyLossGuard()
            r1 = guard.check()
            assert r1.allowed is True

            # Second call: equity at 97000 = 3% loss (< 4% threshold)
            mock_call.return_value = _mock_equity(97000.0)
            r2 = guard.check()
            assert r2.allowed is True
            assert r2.daily_loss_pct == pytest.approx(3.0, abs=0.01)

    def test_at_threshold_blocked(self, use_temp_state):
        """Daily loss exactly at threshold → blocked."""
        with patch("risk.daily_loss_guard.mt5_call") as mock_call:
            mock_call.return_value = _mock_equity(100000.0)
            guard = DailyLossGuard()
            guard.check()  # Establish baseline

            mock_call.return_value = _mock_equity(96000.0)  # Exactly 4%
            r = guard.check()
            assert r.allowed is False
            assert r.reason == REJECT_DAILY_LOSS_EXCEEDED
            assert r.daily_loss_pct == pytest.approx(4.0, abs=0.01)

    def test_above_threshold_blocked(self, use_temp_state):
        """Daily loss above threshold → blocked."""
        with patch("risk.daily_loss_guard.mt5_call") as mock_call:
            mock_call.return_value = _mock_equity(100000.0)
            guard = DailyLossGuard()
            guard.check()

            mock_call.return_value = _mock_equity(94000.0)  # 6% loss
            r = guard.check()
            assert r.allowed is False
            assert r.daily_loss_pct == pytest.approx(6.0, abs=0.01)

    def test_profit_day_always_allowed(self, use_temp_state):
        """Equity above start → always allowed (negative loss = profit)."""
        with patch("risk.daily_loss_guard.mt5_call") as mock_call:
            mock_call.return_value = _mock_equity(100000.0)
            guard = DailyLossGuard()
            guard.check()

            mock_call.return_value = _mock_equity(105000.0)  # +5% profit
            r = guard.check()
            assert r.allowed is True
            assert r.daily_loss_pct < 0  # Negative = profit


# ─── TEST: FAIL-CLOSED ────────────────────────────────────────────────────────

class TestFailClosed:
    def test_equity_none_blocks(self, use_temp_state):
        """MT5 returns None → fail-closed."""
        with patch("risk.daily_loss_guard.mt5_call", return_value=None):
            guard = DailyLossGuard()
            r = guard.check()
            assert r.allowed is False
            assert r.reason == REJECT_EQUITY_UNAVAILABLE

    def test_equity_exception_blocks(self, use_temp_state):
        """MT5 raises → fail-closed."""
        with patch("risk.daily_loss_guard.mt5_call", side_effect=RuntimeError("crash")):
            guard = DailyLossGuard()
            r = guard.check()
            assert r.allowed is False
            assert r.reason == REJECT_EQUITY_UNAVAILABLE


# ─── TEST: PERSISTENCE ────────────────────────────────────────────────────────

class TestPersistence:
    def test_state_persists_on_trigger(self, use_temp_state):
        """Triggered state is persisted to disk."""
        with patch("risk.daily_loss_guard.mt5_call") as mock_call:
            mock_call.return_value = _mock_equity(100000.0)
            guard = DailyLossGuard()
            guard.check()

            mock_call.return_value = _mock_equity(95000.0)  # 5% loss > 4%
            guard.check()

        # Verify file exists with triggered=true
        data = json.loads(use_temp_state.read_text())
        assert data["limit_triggered"] is True
        assert data["daily_start_equity"] == 100000.0

    def test_state_restored_on_restart(self, use_temp_state):
        """Triggered state survives restart."""
        today = _today_str()
        # Write triggered state
        state = _DailyLossState(
            date=today,
            daily_start_equity=100000.0,
            limit_triggered=True,
            last_updated=time.time(),
        )
        _persist_state(state)

        # New guard instance (simulates restart)
        with patch("risk.daily_loss_guard.mt5_call") as mock_call:
            mock_call.return_value = _mock_equity(95000.0)
            guard = DailyLossGuard()
            assert guard.is_triggered is True

            r = guard.check()
            assert r.allowed is False

    def test_corrupt_state_handled(self, use_temp_state):
        """Corrupted state file → reinitialises safely."""
        use_temp_state.write_text("{{invalid json")

        with patch("risk.daily_loss_guard.mt5_call") as mock_call:
            mock_call.return_value = _mock_equity(100000.0)
            guard = DailyLossGuard()
            r = guard.check()
            # Should reinitialise and allow
            assert r.allowed is True


# ─── TEST: DAY RESET ──────────────────────────────────────────────────────────

class TestDayReset:
    def test_new_day_resets_limit(self, use_temp_state):
        """New trading day clears triggered state."""
        # Write yesterday's triggered state
        state = _DailyLossState(
            date="2020-01-01",  # Old date
            daily_start_equity=100000.0,
            limit_triggered=True,
            last_updated=time.time(),
        )
        _persist_state(state)

        with patch("risk.daily_loss_guard.mt5_call") as mock_call:
            mock_call.return_value = _mock_equity(96000.0)
            guard = DailyLossGuard()

            # State from old date → should be discarded on init
            assert guard.is_triggered is False

            # First check establishes new baseline
            r = guard.check()
            assert r.allowed is True
            assert r.daily_start_equity == 96000.0

    def test_same_day_preserves_baseline(self, use_temp_state):
        """Within same day, baseline stays fixed at day start."""
        today = _today_str()
        state = _DailyLossState(
            date=today,
            daily_start_equity=100000.0,
            limit_triggered=False,
            last_updated=time.time(),
        )
        _persist_state(state)

        with patch("risk.daily_loss_guard.mt5_call") as mock_call:
            mock_call.return_value = _mock_equity(102000.0)  # Equity went up
            guard = DailyLossGuard()
            r = guard.check()

            # Baseline is still 100000 (not 102000)
            assert r.daily_start_equity == 100000.0


# ─── TEST: DISABLED GUARD ─────────────────────────────────────────────────────

class TestDisabledGuard:
    def test_disabled_always_allows(self, use_temp_state):
        """When disabled, all checks pass."""
        with patch("risk.daily_loss_guard._is_enabled", return_value=False), \
             patch("risk.daily_loss_guard.mt5_call") as mock_call:
            mock_call.return_value = _mock_equity(50000.0)
            guard = DailyLossGuard()
            r = guard.check()
            assert r.allowed is True


# ─── TEST: RESTART SIMULATION ─────────────────────────────────────────────────

class TestRestartSimulation:
    def test_full_restart_cycle(self, use_temp_state):
        """Trigger → restart → verify still blocked."""
        today = _today_str()

        # Session 1: trigger limit
        with patch("risk.daily_loss_guard.mt5_call") as mock_call:
            mock_call.return_value = _mock_equity(100000.0)
            guard1 = DailyLossGuard()
            guard1.check()

            mock_call.return_value = _mock_equity(95000.0)  # 5% > 4%
            r1 = guard1.check()
            assert r1.allowed is False

        # Session 2: restart (new instance, same file)
        with patch("risk.daily_loss_guard.mt5_call") as mock_call:
            mock_call.return_value = _mock_equity(95500.0)  # Still below start
            guard2 = DailyLossGuard()

            # Must still be blocked (limit was triggered and persisted)
            r2 = guard2.check()
            assert r2.allowed is False
            assert r2.reason == REJECT_DAILY_LOSS_EXCEEDED
