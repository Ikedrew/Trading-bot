"""
Tests for A4: Daily Trade Limit Guard.

Covers:
- Global limit blocks when total reached
- Per-symbol limit blocks when symbol count reached
- Symbol independence (one symbol's limit doesn't block another)
- Restart persistence (counters survive reload)
- Daily reset integration (D4 clears counters)
- Failed orders don't increment counters
- Disabled guard always allows
- Day boundary auto-reset
- Duplicate registration prevention (only caller responsibility)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from risk.daily_trade_limit import (
    DailyTradeLimitManager,
    DailyTradeLimitResult,
    REJECT_GLOBAL_LIMIT,
    REJECT_SYMBOL_LIMIT,
    _load_state,
    _persist_state,
    _current_day_key,
)


# --- FIXTURES -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def use_temp_state(tmp_path):
    """Redirect state file to temp directory and set known config."""
    state_file = tmp_path / "daily_trade_limit_state.json"
    with patch("risk.daily_trade_limit._get_state_path", return_value=state_file), \
         patch("risk.daily_trade_limit._is_enabled", return_value=True), \
         patch("risk.daily_trade_limit._get_max_total", return_value=20), \
         patch("risk.daily_trade_limit._get_max_per_symbol", return_value=5), \
         patch("risk.daily_trade_limit._current_day_key", return_value="2026-06-06"):
        yield state_file


# --- TEST 1: GLOBAL LIMIT -----------------------------------------------------

class TestGlobalLimit:
    def test_blocks_at_global_limit(self, use_temp_state):
        """When total trades reach MAX_TRADES_PER_DAY_TOTAL, all entries blocked."""
        with patch("risk.daily_trade_limit._get_max_total", return_value=3):
            mgr = DailyTradeLimitManager()

            # Trade 1, 2, 3 — all allowed
            for i in range(3):
                r = mgr.can_open_trade("EURUSD")
                assert r.allowed is True, f"Trade {i+1} should be allowed"
                mgr.record_trade_open("EURUSD")

            # Trade 4 — blocked
            r = mgr.can_open_trade("EURUSD")
            assert r.allowed is False
            assert r.reason == REJECT_GLOBAL_LIMIT
            assert r.remaining_total == 0

    def test_global_limit_blocks_all_symbols(self, use_temp_state):
        """Global limit reached blocks even symbols with zero trades."""
        with patch("risk.daily_trade_limit._get_max_total", return_value=3):
            mgr = DailyTradeLimitManager()

            # Exhaust global limit with different symbols
            mgr.record_trade_open("EURUSD")
            mgr.record_trade_open("GBPUSD")
            mgr.record_trade_open("AUDUSD")

            # Even USDJPY (0 symbol trades) is blocked by global limit
            r = mgr.can_open_trade("USDJPY")
            assert r.allowed is False
            assert r.reason == REJECT_GLOBAL_LIMIT


# --- TEST 2: PER-SYMBOL LIMIT -------------------------------------------------

class TestPerSymbolLimit:
    def test_blocks_at_symbol_limit(self, use_temp_state):
        """When symbol trades reach MAX_TRADES_PER_DAY_PER_SYMBOL, that symbol blocked."""
        with patch("risk.daily_trade_limit._get_max_per_symbol", return_value=2):
            mgr = DailyTradeLimitManager()

            # Two EURUSD trades
            mgr.record_trade_open("EURUSD")
            mgr.record_trade_open("EURUSD")

            # Third EURUSD — blocked
            r = mgr.can_open_trade("EURUSD")
            assert r.allowed is False
            assert r.reason == REJECT_SYMBOL_LIMIT
            assert r.remaining_symbol == 0

    def test_remaining_counts_correct(self, use_temp_state):
        """Remaining counts are accurate after trades."""
        with patch("risk.daily_trade_limit._get_max_total", return_value=10), \
             patch("risk.daily_trade_limit._get_max_per_symbol", return_value=3):
            mgr = DailyTradeLimitManager()

            mgr.record_trade_open("EURUSD")
            mgr.record_trade_open("EURUSD")

            r = mgr.can_open_trade("EURUSD")
            assert r.allowed is True
            assert r.remaining_total == 8  # 10 - 2
            assert r.remaining_symbol == 1  # 3 - 2


# --- TEST 3: SYMBOL INDEPENDENCE ----------------------------------------------

class TestSymbolIndependence:
    def test_symbol_limit_does_not_block_other_symbols(self, use_temp_state):
        """EURUSD reaching its limit must NOT block GBPUSD."""
        with patch("risk.daily_trade_limit._get_max_per_symbol", return_value=2):
            mgr = DailyTradeLimitManager()

            # Exhaust EURUSD limit
            mgr.record_trade_open("EURUSD")
            mgr.record_trade_open("EURUSD")

            # EURUSD blocked
            r_eur = mgr.can_open_trade("EURUSD")
            assert r_eur.allowed is False

            # GBPUSD still allowed
            r_gbp = mgr.can_open_trade("GBPUSD")
            assert r_gbp.allowed is True
            assert r_gbp.remaining_symbol == 2  # Fresh symbol

    def test_multiple_symbols_tracked_independently(self, use_temp_state):
        """Each symbol has its own counter."""
        with patch("risk.daily_trade_limit._get_max_per_symbol", return_value=2):
            mgr = DailyTradeLimitManager()

            mgr.record_trade_open("EURUSD")
            mgr.record_trade_open("GBPUSD")
            mgr.record_trade_open("EURUSD")

            # EURUSD at limit
            assert mgr.can_open_trade("EURUSD").allowed is False
            # GBPUSD still has 1 remaining
            r = mgr.can_open_trade("GBPUSD")
            assert r.allowed is True
            assert r.remaining_symbol == 1


# --- TEST 4: RESTART PERSISTENCE ----------------------------------------------

class TestRestartPersistence:
    def test_counters_survive_restart(self, use_temp_state):
        """After restart, trade counters are preserved exactly."""
        # Session 1: open trades
        mgr1 = DailyTradeLimitManager()
        mgr1.record_trade_open("EURUSD")
        mgr1.record_trade_open("EURUSD")
        mgr1.record_trade_open("GBPUSD")

        # Session 2: new instance (simulates restart)
        mgr2 = DailyTradeLimitManager()
        assert mgr2.total_trades_today == 3
        assert mgr2.per_symbol_counts == {"EURUSD": 2, "GBPUSD": 1}

    def test_counters_exact_after_restart(self, use_temp_state):
        """Counters are exactly what was persisted — no drift."""
        with patch("risk.daily_trade_limit._get_max_total", return_value=5):
            mgr1 = DailyTradeLimitManager()
            for _ in range(4):
                mgr1.record_trade_open("AUDUSD")

            # Reload
            mgr2 = DailyTradeLimitManager()
            assert mgr2.total_trades_today == 4
            assert mgr2.per_symbol_counts["AUDUSD"] == 4

            # One more should be allowed
            r = mgr2.can_open_trade("AUDUSD")
            assert r.allowed is True
            assert r.remaining_total == 1

    def test_state_file_format(self, use_temp_state):
        """State file contains expected JSON structure."""
        mgr = DailyTradeLimitManager()
        mgr.record_trade_open("EURUSD")
        mgr.record_trade_open("GBPUSD")

        data = json.loads(use_temp_state.read_text())
        assert data["current_day_key"] == "2026-06-06"
        assert data["total_trades_today"] == 2
        assert data["per_symbol"]["EURUSD"] == 1
        assert data["per_symbol"]["GBPUSD"] == 1
        assert "last_updated" in data


# --- TEST 5: DAILY RESET INTEGRATION ------------------------------------------

class TestDailyResetIntegration:
    def test_reset_clears_all_counters(self, use_temp_state):
        """D4 reset clears total and per-symbol counters."""
        mgr = DailyTradeLimitManager()
        mgr.record_trade_open("EURUSD")
        mgr.record_trade_open("EURUSD")
        mgr.record_trade_open("GBPUSD")

        assert mgr.total_trades_today == 3

        # Trigger D4 reset
        mgr.reset()

        assert mgr.total_trades_today == 0
        assert mgr.per_symbol_counts == {}

    def test_reset_persists_immediately(self, use_temp_state):
        """Reset state is persisted to disk."""
        mgr = DailyTradeLimitManager()
        mgr.record_trade_open("EURUSD")
        mgr.reset()

        # Verify file has zeroed state
        data = json.loads(use_temp_state.read_text())
        assert data["total_trades_today"] == 0
        assert data["per_symbol"] == {}

    def test_after_reset_trading_allowed(self, use_temp_state):
        """After D4 reset, all symbols allowed again."""
        with patch("risk.daily_trade_limit._get_max_total", return_value=3):
            mgr = DailyTradeLimitManager()

            # Exhaust limit
            mgr.record_trade_open("EURUSD")
            mgr.record_trade_open("EURUSD")
            mgr.record_trade_open("EURUSD")
            assert mgr.can_open_trade("EURUSD").allowed is False

            # Reset
            mgr.reset()

            # Now allowed again
            r = mgr.can_open_trade("EURUSD")
            assert r.allowed is True
            assert r.remaining_total == 3

    def test_day_boundary_auto_reset(self, use_temp_state):
        """When day changes, counters auto-reset on next check."""
        # Setup: state from yesterday
        yesterday_state = {
            "current_day_key": "2026-06-05",
            "total_trades_today": 15,
            "per_symbol": {"EURUSD": 5, "GBPUSD": 10},
            "last_updated": time.time(),
        }
        use_temp_state.write_text(json.dumps(yesterday_state))

        # Today is 2026-06-06 (from fixture patch)
        mgr = DailyTradeLimitManager()

        # Should have reset because day changed
        assert mgr.total_trades_today == 0
        assert mgr.per_symbol_counts == {}


# --- TEST 6: FAILED ORDERS ----------------------------------------------------

class TestFailedOrders:
    def test_failed_order_does_not_increment(self, use_temp_state):
        """Only record_trade_open increments. If not called, counters unchanged."""
        mgr = DailyTradeLimitManager()

        # Check is allowed (simulating intent to trade)
        r = mgr.can_open_trade("EURUSD")
        assert r.allowed is True

        # Simulate broker rejection: do NOT call record_trade_open

        # Counters remain at zero
        assert mgr.total_trades_today == 0
        assert mgr.per_symbol_counts == {}

    def test_multiple_failed_orders_no_increment(self, use_temp_state):
        """Multiple failed orders leave counters unchanged."""
        mgr = DailyTradeLimitManager()

        # 10 attempts, all fail (no record_trade_open called)
        for _ in range(10):
            mgr.can_open_trade("EURUSD")

        assert mgr.total_trades_today == 0

    def test_mix_of_success_and_failure(self, use_temp_state):
        """Only successful fills count. Failures are invisible to guard."""
        with patch("risk.daily_trade_limit._get_max_total", return_value=3):
            mgr = DailyTradeLimitManager()

            # Success
            mgr.can_open_trade("EURUSD")
            mgr.record_trade_open("EURUSD")  # count: 1

            # Failure (no record)
            mgr.can_open_trade("EURUSD")
            # broker rejects — no record_trade_open

            # Success
            mgr.can_open_trade("GBPUSD")
            mgr.record_trade_open("GBPUSD")  # count: 2

            # Failure (no record)
            mgr.can_open_trade("AUDUSD")
            # broker rejects — no record_trade_open

            # Success
            mgr.can_open_trade("AUDUSD")
            mgr.record_trade_open("AUDUSD")  # count: 3

            # Now blocked
            r = mgr.can_open_trade("EURUSD")
            assert r.allowed is False
            assert mgr.total_trades_today == 3


# --- TEST: DISABLED GUARD -----------------------------------------------------

class TestDisabledGuard:
    def test_disabled_always_allows(self, use_temp_state):
        """When disabled, all checks pass regardless of count."""
        with patch("risk.daily_trade_limit._is_enabled", return_value=False), \
             patch("risk.daily_trade_limit._get_max_total", return_value=1):
            mgr = DailyTradeLimitManager()
            mgr.record_trade_open("EURUSD")
            mgr.record_trade_open("EURUSD")

            r = mgr.can_open_trade("EURUSD")
            assert r.allowed is True
            assert r.reason == "DISABLED"


# --- TEST: EDGE CASES ---------------------------------------------------------

class TestEdgeCases:
    def test_no_prior_state_file(self, use_temp_state):
        """First run (no state file) starts with zero counters."""
        mgr = DailyTradeLimitManager()
        assert mgr.total_trades_today == 0
        assert mgr.per_symbol_counts == {}

        r = mgr.can_open_trade("EURUSD")
        assert r.allowed is True

    def test_corrupted_state_file(self, use_temp_state):
        """Corrupted state ? starts fresh (zero counters)."""
        use_temp_state.write_text("{{invalid json garbage")

        mgr = DailyTradeLimitManager()
        assert mgr.total_trades_today == 0
        r = mgr.can_open_trade("EURUSD")
        assert r.allowed is True

    def test_zero_limits_blocks_everything(self, use_temp_state):
        """If limits set to 0, nothing is allowed."""
        with patch("risk.daily_trade_limit._get_max_total", return_value=0):
            mgr = DailyTradeLimitManager()
            r = mgr.can_open_trade("EURUSD")
            assert r.allowed is False
            assert r.reason == REJECT_GLOBAL_LIMIT

    def test_record_persists_every_time(self, use_temp_state):
        """Each record_trade_open triggers a file write."""
        mgr = DailyTradeLimitManager()

        mgr.record_trade_open("EURUSD")
        data1 = json.loads(use_temp_state.read_text())
        assert data1["total_trades_today"] == 1

        mgr.record_trade_open("EURUSD")
        data2 = json.loads(use_temp_state.read_text())
        assert data2["total_trades_today"] == 2

    def test_symbol_names_case_sensitive(self, use_temp_state):
        """Symbol names are case-sensitive (EURUSD != eurusd)."""
        with patch("risk.daily_trade_limit._get_max_per_symbol", return_value=1):
            mgr = DailyTradeLimitManager()
            mgr.record_trade_open("EURUSD")

            # Same symbol — blocked
            assert mgr.can_open_trade("EURUSD").allowed is False
            # Different case — treated as different symbol
            assert mgr.can_open_trade("eurusd").allowed is True
