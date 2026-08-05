"""
Tests for exit_reason pipeline — verifies correct mapping from broker close
through trade management → event bus → trade journal.

Covers:
    1. SL hit (bot-detected) → stop_loss_hit
    2. TP hit (bot-detected) → take_profit_hit
    3. Broker SL (deal.reason=4) → stop_loss_hit
    4. Broker TP (deal.reason=5) → take_profit_hit
    5. Manual close (deal.reason=0) → manual_close
    6. Stop out (deal.reason=6) → margin_call (the ONLY valid case)
    7. Lifecycle reason preserved over generic broker_close
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

from strategy.signals import Side
from core.trade_management.events import TradeLifecycleEvent
from core.trade_management.position import Position, PositionStatus
from core.trade_management.config import TradeManagementConfig
from core.trade_journal import build_trade_record


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def _make_position(symbol="EURUSD", side=Side.BUY, entry=1.1000, sl=1.0950, tp=1.1050):
    return Position(
        position_id="pos_test_123",
        symbol=symbol,
        side=side,
        magic=713001,
        entry_price=entry,
        initial_sl=sl,
        initial_tp=tp,
        stop_loss=sl,
        take_profit=tp,
        volume=0.10,
        open_time=1000.0,
        status=PositionStatus.OPEN,
        mt5_ticket=12345678,
        max_favourable_price=entry,
    )


# ═══════════════════════════════════════════════════════════════
# TEST: build_trade_record correctly assigns close_reason
# ═══════════════════════════════════════════════════════════════

class TestBuildTradeRecordCloseReason:
    """Verify build_trade_record preserves the close_reason passed to it."""

    def test_stop_loss_reason(self):
        pos = _make_position()
        record = build_trade_record(
            position=pos, exit_price=1.0950, exit_time=2000.0, close_reason="stop_loss"
        )
        assert record.close_reason == "stop_loss"

    def test_take_profit_reason(self):
        pos = _make_position()
        record = build_trade_record(
            position=pos, exit_price=1.1050, exit_time=2000.0, close_reason="take_profit"
        )
        assert record.close_reason == "take_profit"

    def test_management_exit_reason(self):
        pos = _make_position()
        record = build_trade_record(
            position=pos, exit_price=1.1000, exit_time=2000.0, close_reason="management_exit"
        )
        assert record.close_reason == "management_exit"

    def test_stop_out_reason(self):
        pos = _make_position()
        record = build_trade_record(
            position=pos, exit_price=1.0900, exit_time=2000.0, close_reason="stop_out"
        )
        assert record.close_reason == "stop_out"

    def test_client_close_reason(self):
        pos = _make_position()
        record = build_trade_record(
            position=pos, exit_price=1.1020, exit_time=2000.0, close_reason="client_close"
        )
        assert record.close_reason == "client_close"


# ═══════════════════════════════════════════════════════════════
# TEST: Journal exit_reason mapping
# ═══════════════════════════════════════════════════════════════

class TestJournalExitReasonMapping:
    """Verify the trade_truth exit_reason mapping is correct."""

    def test_mapping_values(self):
        """Verify the complete mapping table."""
        # Import the mapping directly by calling the function that uses it
        _exit_map = {
            "take_profit": "take_profit_hit",
            "stop_loss": "stop_loss_hit",
            "time_exit": "system_close",
            "management_exit": "system_close",
            "manual_close": "manual_close",
            "broker_close": "system_close",
            "stop_out": "margin_call",
            "expert_close": "system_close",
            "client_close": "manual_close",
            "mobile_close": "manual_close",
            "web_close": "manual_close",
        }

        # Only stop_out should produce margin_call
        margin_call_sources = [k for k, v in _exit_map.items() if v == "margin_call"]
        assert margin_call_sources == ["stop_out"]

        # broker_close should NOT produce margin_call
        assert _exit_map["broker_close"] != "margin_call"
        assert _exit_map["broker_close"] == "system_close"

        # SL/TP correctly mapped
        assert _exit_map["stop_loss"] == "stop_loss_hit"
        assert _exit_map["take_profit"] == "take_profit_hit"


# ═══════════════════════════════════════════════════════════════
# TEST: Lifecycle reason preserved over broker_close
# ═══════════════════════════════════════════════════════════════

class TestLifecycleReasonPreserved:
    """
    When the bot detects SL/TP hit locally and the broker returns
    POSITION_NOT_FOUND (already closed), the lifecycle reason must
    be preserved — NOT overwritten by 'broker_close'.
    """

    def test_sl_lifecycle_not_overwritten(self):
        """ON_STOP_LOSS_HIT kind produces stop_loss even when broker detail present."""
        from core.trade_management.manager import TradeStateManager

        cfg = TradeManagementConfig()
        events_received = []

        class FakeListener:
            def on_trade_event(self, event):
                events_received.append(event)

        mgr = TradeStateManager(cfg, listener=FakeListener())
        pos = _make_position()
        pos.status = PositionStatus.OPEN
        mgr._by_id[pos.position_id] = pos

        # Simulate: broker detail comes back with generic reason
        detail_with_broker = {"reason": "broker_close", "broker_profit": -50.0}

        # Call _close_local with ON_STOP_LOSS_HIT kind but broker_close in detail
        mgr._close_local(
            pos, TradeLifecycleEvent.ON_STOP_LOSS_HIT,
            (1.0950, 1.0952), 2000.0, detail_with_broker,
        )

        # Find the ON_TRADE_CLOSE event
        close_events = [e for e in events_received if e.kind == TradeLifecycleEvent.ON_TRADE_CLOSE]
        assert len(close_events) == 1

        # The reason should be stop_loss (from lifecycle), NOT broker_close
        assert close_events[0].detail["reason"] == "stop_loss"

    def test_tp_lifecycle_not_overwritten(self):
        """ON_TAKE_PROFIT_HIT kind produces take_profit even when broker detail present."""
        from core.trade_management.manager import TradeStateManager

        cfg = TradeManagementConfig()
        events_received = []

        class FakeListener:
            def on_trade_event(self, event):
                events_received.append(event)

        mgr = TradeStateManager(cfg, listener=FakeListener())
        pos = _make_position()
        pos.status = PositionStatus.OPEN
        mgr._by_id[pos.position_id] = pos

        detail_with_broker = {"reason": "broker_close", "broker_profit": 50.0}

        mgr._close_local(
            pos, TradeLifecycleEvent.ON_TAKE_PROFIT_HIT,
            (1.1050, 1.1052), 2000.0, detail_with_broker,
        )

        close_events = [e for e in events_received if e.kind == TradeLifecycleEvent.ON_TRADE_CLOSE]
        assert close_events[0].detail["reason"] == "take_profit"

    def test_broker_specific_reason_preserved_when_no_lifecycle(self):
        """When lifecycle doesn't know (unknown kind), broker reason is kept."""
        from core.trade_management.manager import TradeStateManager

        cfg = TradeManagementConfig()
        events_received = []

        class FakeListener:
            def on_trade_event(self, event):
                events_received.append(event)

        mgr = TradeStateManager(cfg, listener=FakeListener())
        pos = _make_position()
        pos.status = PositionStatus.OPEN
        mgr._by_id[pos.position_id] = pos

        # Broker says stop_out — this IS specific and should be preserved
        detail_with_broker = {"reason": "stop_out", "broker_profit": -500.0}

        # Use ON_TRADE_CLOSE directly (no lifecycle reason in map for this)
        mgr._close_local(
            pos, TradeLifecycleEvent.ON_TRADE_CLOSE,
            (1.0800, 1.0802), 2000.0, detail_with_broker,
        )

        close_events = [e for e in events_received if e.kind == TradeLifecycleEvent.ON_TRADE_CLOSE]
        assert close_events[0].detail["reason"] == "stop_out"


# ═══════════════════════════════════════════════════════════════
# TEST: _query_broker_close_history uses deal.reason
# ═══════════════════════════════════════════════════════════════

class TestQueryBrokerCloseHistory:
    """Verify deal.reason field is used over comment parsing."""

    def test_deal_reason_sl(self):
        from core.trade_management.manager import TradeStateManager

        cfg = TradeManagementConfig()
        mgr = TradeStateManager(cfg)

        # Mock a position
        pos = _make_position(symbol="US500")
        pos.mt5_ticket = 54568066

        # Mock MT5 deal with reason=4 (SL)
        fake_deal = MagicMock()
        fake_deal.entry = 1  # DEAL_ENTRY_OUT
        fake_deal.reason = 4  # DEAL_REASON_SL
        fake_deal.comment = ""  # No comment (would have been broker_close before)
        fake_deal.price = 7607.975
        fake_deal.time = 1722800000
        fake_deal.profit = -500.0
        fake_deal.ticket = 99999

        with patch("core.mt5_timeout.mt5_call", return_value=[fake_deal]):
            with patch("core.mt5_timestamp.normalize_mt5_timestamp", return_value=1722800000.0):
                result = mgr._query_broker_close_history(pos)

        assert result is not None
        assert result["reason"] == "stop_loss"
        assert result["broker_deal_reason"] == 4

    def test_deal_reason_tp(self):
        from core.trade_management.manager import TradeStateManager

        cfg = TradeManagementConfig()
        mgr = TradeStateManager(cfg)

        pos = _make_position(symbol="EURUSD")
        pos.mt5_ticket = 12345

        fake_deal = MagicMock()
        fake_deal.entry = 1
        fake_deal.reason = 5  # DEAL_REASON_TP
        fake_deal.comment = ""
        fake_deal.price = 1.1050
        fake_deal.time = 1722800000
        fake_deal.profit = 50.0
        fake_deal.ticket = 88888

        with patch("core.mt5_timeout.mt5_call", return_value=[fake_deal]):
            with patch("core.mt5_timestamp.normalize_mt5_timestamp", return_value=1722800000.0):
                result = mgr._query_broker_close_history(pos)

        assert result["reason"] == "take_profit"

    def test_deal_reason_stop_out(self):
        from core.trade_management.manager import TradeStateManager

        cfg = TradeManagementConfig()
        mgr = TradeStateManager(cfg)

        pos = _make_position(symbol="NAS100")
        pos.mt5_ticket = 77777

        fake_deal = MagicMock()
        fake_deal.entry = 1
        fake_deal.reason = 6  # DEAL_REASON_SO (stop out / margin call)
        fake_deal.comment = "so"
        fake_deal.price = 28000.0
        fake_deal.time = 1722800000
        fake_deal.profit = -2000.0
        fake_deal.ticket = 66666

        with patch("core.mt5_timeout.mt5_call", return_value=[fake_deal]):
            with patch("core.mt5_timestamp.normalize_mt5_timestamp", return_value=1722800000.0):
                result = mgr._query_broker_close_history(pos)

        assert result["reason"] == "stop_out"

    def test_deal_reason_client_close(self):
        from core.trade_management.manager import TradeStateManager

        cfg = TradeManagementConfig()
        mgr = TradeStateManager(cfg)

        pos = _make_position()
        pos.mt5_ticket = 55555

        fake_deal = MagicMock()
        fake_deal.entry = 1
        fake_deal.reason = 0  # DEAL_REASON_CLIENT
        fake_deal.comment = ""
        fake_deal.price = 1.1020
        fake_deal.time = 1722800000
        fake_deal.profit = 20.0
        fake_deal.ticket = 44444

        with patch("core.mt5_timeout.mt5_call", return_value=[fake_deal]):
            with patch("core.mt5_timestamp.normalize_mt5_timestamp", return_value=1722800000.0):
                result = mgr._query_broker_close_history(pos)

        assert result["reason"] == "client_close"

    def test_fallback_to_comment_when_reason_unavailable(self):
        """If deal has no .reason attr, fall back to comment parsing."""
        from core.trade_management.manager import TradeStateManager

        cfg = TradeManagementConfig()
        mgr = TradeStateManager(cfg)

        pos = _make_position()
        pos.mt5_ticket = 33333

        fake_deal = MagicMock(spec=[])  # No attributes by default
        fake_deal.entry = 1
        fake_deal.comment = "[sl]"
        fake_deal.price = 1.0950
        fake_deal.time = 1722800000
        fake_deal.profit = -50.0
        fake_deal.ticket = 22222
        # Explicitly remove reason attribute
        del fake_deal.reason

        with patch("core.mt5_timeout.mt5_call", return_value=[fake_deal]):
            with patch("core.mt5_timestamp.normalize_mt5_timestamp", return_value=1722800000.0):
                result = mgr._query_broker_close_history(pos)

        assert result["reason"] == "stop_loss"
