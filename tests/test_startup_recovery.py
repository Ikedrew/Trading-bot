"""
Tests for D3: Position State Reconciliation on Startup.

Covers:
- Positions discovered and registered
- Empty broker state ? no-op
- Duplicate protection (no double-registration)
- Correct field mapping from broker position
- Management continuity (recovered positions respond to price updates)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.runtime.startup_recovery import recover_positions_on_startup
from core.trade_management.manager import TradeStateManager
from core.trade_management.config import TradeManagementConfig
from core.trade_management.position import PositionStatus
from strategy.signals import Side


def _cfg():
    return TradeManagementConfig()


def _mock_broker_position(ticket=12345, symbol="EURUSD", magic=713001,
                          type_=0, volume=0.10, price_open=1.10000,
                          sl=1.09500, tp=1.11000, time_=1717400000,
                          price_current=1.10050):
    """Create a mock broker position object (mimics MT5 positions_get result)."""
    bp = MagicMock()
    bp.ticket = ticket
    bp.symbol = symbol
    bp.magic = magic
    bp.type = type_  # 0=BUY, 1=SELL
    bp.volume = volume
    bp.price_open = price_open
    bp.sl = sl
    bp.tp = tp
    bp.time = time_
    bp.price_current = price_current
    return bp


class TestPositionDiscovery:
    def test_recovers_broker_positions(self):
        """Open broker positions are registered into TradeStateManager."""
        tm = TradeStateManager(_cfg())
        bp = _mock_broker_position(ticket=100, magic=713001)

        with patch("core.runtime.startup_recovery.mt5_call", return_value=[bp]), \
             patch("core.runtime.startup_recovery.mt5") as mock_mt5:
            mock_mt5.ORDER_TYPE_BUY = 0
            count = recover_positions_on_startup(trade_manager=tm, symbol="EURUSD", magic=713001)

        assert count == 1
        assert len(tm.positions_open()) == 1
        pos = tm.positions_open()[0]
        assert pos.mt5_ticket == 100
        assert pos.symbol == "EURUSD"
        assert pos.side == Side.BUY
        assert pos.entry_price == 1.10000
        assert pos.volume == 0.10

    def test_multiple_positions_recovered(self):
        """Multiple positions are all recovered."""
        tm = TradeStateManager(_cfg())
        positions = [
            _mock_broker_position(ticket=101, symbol="EURUSD"),
            _mock_broker_position(ticket=102, symbol="EURUSD"),
            _mock_broker_position(ticket=103, symbol="EURUSD"),
        ]

        with patch("core.runtime.startup_recovery.mt5_call", return_value=positions), \
             patch("core.runtime.startup_recovery.mt5") as mock_mt5:
            mock_mt5.ORDER_TYPE_BUY = 0
            count = recover_positions_on_startup(trade_manager=tm, symbol="EURUSD", magic=713001)

        assert count == 3
        assert len(tm.positions_open()) == 3

    def test_sell_position_detected(self):
        """SELL positions have correct side."""
        tm = TradeStateManager(_cfg())
        bp = _mock_broker_position(ticket=200, type_=1)  # ORDER_TYPE_SELL

        with patch("core.runtime.startup_recovery.mt5_call", return_value=[bp]), \
             patch("core.runtime.startup_recovery.mt5") as mock_mt5:
            mock_mt5.ORDER_TYPE_BUY = 0
            recover_positions_on_startup(trade_manager=tm, symbol="EURUSD", magic=713001)

        assert tm.positions_open()[0].side == Side.SELL


class TestEmptyBrokerState:
    def test_no_positions_returns_zero(self):
        """No broker positions ? graceful no-op."""
        tm = TradeStateManager(_cfg())

        with patch("core.runtime.startup_recovery.mt5_call", return_value=[]):
            count = recover_positions_on_startup(trade_manager=tm, symbol="EURUSD", magic=713001)

        assert count == 0
        assert len(tm.positions_open()) == 0

    def test_none_response_returns_zero(self):
        """MT5 returns None ? graceful no-op."""
        tm = TradeStateManager(_cfg())

        with patch("core.runtime.startup_recovery.mt5_call", return_value=None):
            count = recover_positions_on_startup(trade_manager=tm, symbol="EURUSD", magic=713001)

        assert count == 0

    def test_wrong_magic_filtered(self):
        """Positions with different magic are ignored."""
        tm = TradeStateManager(_cfg())
        bp = _mock_broker_position(ticket=300, magic=999999)

        with patch("core.runtime.startup_recovery.mt5_call", return_value=[bp]), \
             patch("core.runtime.startup_recovery.mt5") as mock_mt5:
            mock_mt5.ORDER_TYPE_BUY = 0
            count = recover_positions_on_startup(trade_manager=tm, symbol="EURUSD", magic=713001)

        assert count == 0

    def test_no_trade_manager_noop(self):
        """None trade_manager ? no-op, no crash."""
        count = recover_positions_on_startup(trade_manager=None, symbol="EURUSD", magic=713001)
        assert count == 0


class TestDuplicateProtection:
    def test_no_double_registration(self):
        """Same position recovered twice ? only registered once."""
        tm = TradeStateManager(_cfg())
        bp = _mock_broker_position(ticket=400, magic=713001)

        with patch("core.runtime.startup_recovery.mt5_call", return_value=[bp]), \
             patch("core.runtime.startup_recovery.mt5") as mock_mt5:
            mock_mt5.ORDER_TYPE_BUY = 0
            recover_positions_on_startup(trade_manager=tm, symbol="EURUSD", magic=713001)
            # Call again (simulates double-startup)
            recover_positions_on_startup(trade_manager=tm, symbol="EURUSD", magic=713001)

        assert len(tm.positions_open()) == 1

    def test_already_tracked_skipped(self):
        """Position already in TradeStateManager ? not duplicated."""
        tm = TradeStateManager(_cfg())

        # Pre-register position
        from core.trade_management.position import Position
        existing = Position(
            position_id="pos_500", symbol="EURUSD", side=Side.BUY,
            magic=713001, entry_price=1.1, initial_sl=1.09, initial_tp=1.11,
            stop_loss=1.09, take_profit=1.11, volume=0.1, open_time=1000.0,
            status=PositionStatus.OPEN, mt5_ticket=500,
        )
        tm._by_id["pos_500"] = existing

        bp = _mock_broker_position(ticket=500, magic=713001)
        with patch("core.runtime.startup_recovery.mt5_call", return_value=[bp]), \
             patch("core.runtime.startup_recovery.mt5") as mock_mt5:
            mock_mt5.ORDER_TYPE_BUY = 0
            count = recover_positions_on_startup(trade_manager=tm, symbol="EURUSD", magic=713001)

        assert count == 0  # Already tracked
        assert len(tm.positions_open()) == 1


class TestManagementContinuity:
    def test_recovered_position_responds_to_price_update(self):
        """Recovered position is managed by on_price_update (trailing/BE)."""
        cfg = TradeManagementConfig(
            break_even_trigger_rr=1.0,
            break_even_buffer_rr=0.00005,
        )
        tm = TradeStateManager(cfg)
        bp = _mock_broker_position(
            ticket=600, magic=713001, price_open=1.10000,
            sl=1.09500, tp=1.11000, price_current=1.10600,
        )

        with patch("core.runtime.startup_recovery.mt5_call", return_value=[bp]), \
             patch("core.runtime.startup_recovery.mt5") as mock_mt5:
            mock_mt5.ORDER_TYPE_BUY = 0
            recover_positions_on_startup(trade_manager=tm, symbol="EURUSD", magic=713001)

        pos = tm.positions_open()[0]
        original_sl = pos.stop_loss

        # Simulate price update that should trigger BE
        # Entry=1.10000, SL=1.09500, risk=0.005. BE trigger at 1R = entry + 0.005 = 1.10500
        # Price at 1.10600 > 1.10500 ? BE should move SL to entry + buffer
        tm.on_price_update("EURUSD", 1.10600, 1.10620, 1717401000.0)

        # SL should have moved (BE logic engaged)
        assert pos.stop_loss >= pos.entry_price  # Moved to at least breakeven
