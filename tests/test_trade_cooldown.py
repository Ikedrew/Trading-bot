"""
Tests for B1: Trade Cooldown — Per-symbol, outcome-aware, persistent.

Covers:
- Base cooldown blocks re-entry
- Loss cooldown is longer than base
- Per-symbol isolation (different symbols independent)
- Persistence survives restart
- Expired cooldown allows entry
- No prior trade ? allowed
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

from risk.trade_cooldown import TradeCooldownManager, SymbolCooldownEntry


@pytest.fixture(autouse=True)
def use_temp_state(tmp_path):
    """Redirect cooldown state to temp directory."""
    state_file = tmp_path / "trade_cooldown_state.json"
    with patch("risk.trade_cooldown._get_state_path", return_value=state_file), \
         patch("risk.trade_cooldown._get_base_cooldown", return_value=300.0), \
         patch("risk.trade_cooldown._get_loss_cooldown", return_value=600.0):
        yield state_file


class TestBaseCooldown:
    def test_no_prior_trade_allowed(self, use_temp_state):
        """No prior trade for symbol ? allowed."""
        mgr = TradeCooldownManager()
        assert mgr.can_open_trade("EURUSD", time.time()) is True

    def test_recent_trade_blocked(self, use_temp_state):
        """Trade closed 100s ago with base cooldown 300s ? blocked."""
        mgr = TradeCooldownManager()
        now = time.time()
        mgr.record_trade_exit("EURUSD", "BUY", "WIN", exit_time=now - 100)
        assert mgr.can_open_trade("EURUSD", now) is False

    def test_expired_cooldown_allowed(self, use_temp_state):
        """Trade closed 400s ago with base cooldown 300s ? allowed."""
        mgr = TradeCooldownManager()
        now = time.time()
        mgr.record_trade_exit("EURUSD", "BUY", "WIN", exit_time=now - 400)
        assert mgr.can_open_trade("EURUSD", now) is True


class TestLossCooldown:
    def test_loss_cooldown_longer(self, use_temp_state):
        """Loss uses 600s cooldown. Trade closed 400s ago ? still blocked."""
        mgr = TradeCooldownManager()
        now = time.time()
        mgr.record_trade_exit("EURUSD", "BUY", "LOSS", exit_time=now - 400)
        # 400s < 600s loss cooldown ? blocked
        assert mgr.can_open_trade("EURUSD", now) is False

    def test_loss_cooldown_expired_allows(self, use_temp_state):
        """Loss cooldown 600s expired ? allowed."""
        mgr = TradeCooldownManager()
        now = time.time()
        mgr.record_trade_exit("EURUSD", "SELL", "LOSS", exit_time=now - 700)
        assert mgr.can_open_trade("EURUSD", now) is True

    def test_win_uses_shorter_cooldown(self, use_temp_state):
        """Win uses base 300s. Closed 400s ago ? allowed."""
        mgr = TradeCooldownManager()
        now = time.time()
        mgr.record_trade_exit("EURUSD", "BUY", "WIN", exit_time=now - 400)
        assert mgr.can_open_trade("EURUSD", now) is True


class TestPerSymbolIsolation:
    def test_different_symbols_independent(self, use_temp_state):
        """EURUSD cooldown does NOT affect GBPUSD."""
        mgr = TradeCooldownManager()
        now = time.time()
        mgr.record_trade_exit("EURUSD", "BUY", "LOSS", exit_time=now - 100)
        # EURUSD blocked (100s < 600s)
        assert mgr.can_open_trade("EURUSD", now) is False
        # GBPUSD NOT affected
        assert mgr.can_open_trade("GBPUSD", now) is True


class TestPersistence:
    def test_state_survives_restart(self, use_temp_state):
        """Cooldown persists and new instance loads it."""
        now = time.time()
        # Instance 1: record trade
        mgr1 = TradeCooldownManager()
        mgr1.record_trade_exit("EURUSD", "BUY", "LOSS", exit_time=now - 100)

        # Instance 2: restart (new object, same file)
        mgr2 = TradeCooldownManager()
        # Should still be blocked (100s < 600s loss cooldown)
        assert mgr2.can_open_trade("EURUSD", now) is False

    def test_expired_on_restart_allows(self, use_temp_state):
        """If cooldown expired by restart time ? allowed."""
        now = time.time()
        mgr1 = TradeCooldownManager()
        mgr1.record_trade_exit("EURUSD", "BUY", "WIN", exit_time=now - 500)

        mgr2 = TradeCooldownManager()
        assert mgr2.can_open_trade("EURUSD", now) is True

    def test_corrupted_file_handled(self, use_temp_state):
        """Corrupted state file ? start fresh (allow all)."""
        use_temp_state.write_text("{{invalid json")
        mgr = TradeCooldownManager()
        assert mgr.can_open_trade("EURUSD", time.time()) is True


class TestRemainingCooldown:
    def test_remaining_seconds(self, use_temp_state):
        """get_remaining_cooldown returns correct value."""
        mgr = TradeCooldownManager()
        now = time.time()
        mgr.record_trade_exit("EURUSD", "BUY", "LOSS", exit_time=now - 200)
        remaining = mgr.get_remaining_cooldown("EURUSD", now)
        # 600 - 200 = 400s remaining
        assert 390 < remaining < 410

    def test_no_cooldown_returns_zero(self, use_temp_state):
        """No prior trade ? 0 remaining."""
        mgr = TradeCooldownManager()
        assert mgr.get_remaining_cooldown("EURUSD", time.time()) == 0.0
