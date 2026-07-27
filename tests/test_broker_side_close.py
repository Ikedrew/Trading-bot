"""
Tests for broker-side position close reconciliation.

Covers:
1. Broker closes SL before bot detects → no infinite retry, trade_truth written
2. Broker closes TP before bot detects → correct profit recorded
3. Manual close from MT5 terminal → local state reconciles
4. Position disappears after restart → resync handles orphan
5. Existing successful close path still works
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.trade_management.manager import TradeStateManager
from core.trade_management.config import TradeManagementConfig
from core.trade_management.position import Position, PositionStatus
from core.trade_management.events import TradeLifecycleEvent, TradeEvent
from execution.mt5_execution import ExecutionResult
from risk.models import OrderIntent
from strategy.signals import Side


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _cfg(**overrides) -> TradeManagementConfig:
    defaults = dict(
        break_even_trigger_rr=0.0,
        break_even_buffer_rr=0.0,
        trailing_step=0.0,
        trailing_start_rr=0.0,
        partial_tp_fraction=0.0,
        partial_tp_path_fraction=0.0,
        max_time_in_trade_seconds=0.0,
    )
    defaults.update(overrides)
    return TradeManagementConfig(**defaults)


def _make_position(symbol="EURUSD", side=Side.BUY, entry=1.10, sl=1.09, tp=1.12,
                   ticket=12345, volume=0.01) -> Position:
    return Position(
        position_id=f"pos_{ticket}",
        symbol=symbol,
        side=side,
        magic=713001,
        entry_price=entry,
        initial_sl=sl,
        initial_tp=tp,
        stop_loss=sl,
        take_profit=tp,
        volume=volume,
        open_time=time.time() - 300,
        status=PositionStatus.OPEN,
        mt5_ticket=ticket,
    )


def _mock_execution(close_result: ExecutionResult) -> MagicMock:
    mock = MagicMock()
    mock.close_position.return_value = close_result
    return mock


# ─── TEST 1: BROKER SL CLOSE BEFORE BOT DETECTS ──────────────────────────────

class TestBrokerSLClose:
    def test_position_not_found_marks_closed(self):
        """When broker returns POSITION_NOT_FOUND, position is marked CLOSED locally."""
        execution = _mock_execution(
            ExecutionResult(ok=False, retcode=-1, deal=0, order=0, comment="POSITION_NOT_FOUND")
        )
        listener = MagicMock()
        tm = TradeStateManager(_cfg(), listener=listener, execution=execution)

        pos = _make_position(side=Side.BUY, entry=1.10, sl=1.09, tp=1.12, ticket=99999)
        tm._by_id[pos.position_id] = pos

        # Simulate tick where bid drops below SL
        tm.on_price_update("EURUSD", bid=1.0899, ask=1.0900, time_s=time.time())

        # Position should be CLOSED (not stuck in retry loop)
        assert pos.status == PositionStatus.CLOSED
        assert pos.closed_time is not None

    def test_no_retry_queue_on_position_not_found(self):
        """POSITION_NOT_FOUND should NOT enter the close retry queue."""
        execution = _mock_execution(
            ExecutionResult(ok=False, retcode=-1, deal=0, order=0, comment="POSITION_NOT_FOUND")
        )
        tm = TradeStateManager(_cfg(), execution=execution)

        pos = _make_position(ticket=88888)
        tm._by_id[pos.position_id] = pos

        tm.on_price_update("EURUSD", bid=1.0899, ask=1.0900, time_s=time.time())

        assert len(tm._close_retry_queue) == 0

    def test_lifecycle_event_emitted_on_broker_close(self):
        """ON_TRADE_CLOSE event is emitted so trade_truth can be persisted."""
        execution = _mock_execution(
            ExecutionResult(ok=False, retcode=-1, deal=0, order=0, comment="POSITION_NOT_FOUND")
        )
        listener = MagicMock()
        tm = TradeStateManager(_cfg(), listener=listener, execution=execution)

        pos = _make_position(ticket=77777)
        tm._by_id[pos.position_id] = pos

        tm.on_price_update("EURUSD", bid=1.0899, ask=1.0900, time_s=time.time())

        # Verify ON_TRADE_CLOSE was emitted (enables journal persistence)
        close_events = [
            call for call in listener.on_trade_event.call_args_list
            if call[0][0].kind == TradeLifecycleEvent.ON_TRADE_CLOSE
        ]
        assert len(close_events) == 1


# ─── TEST 2: BROKER TP CLOSE BEFORE BOT DETECTS ──────────────────────────────

class TestBrokerTPClose:
    def test_tp_hit_position_not_found(self):
        """Broker TP fills before bot detects → position closes cleanly."""
        execution = _mock_execution(
            ExecutionResult(ok=False, retcode=-1, deal=0, order=0, comment="POSITION_NOT_FOUND")
        )
        tm = TradeStateManager(_cfg(), execution=execution)

        pos = _make_position(side=Side.BUY, entry=1.10, sl=1.09, tp=1.12, ticket=66666)
        tm._by_id[pos.position_id] = pos

        # Simulate tick where ask crosses above TP (BUY → exit at bid)
        tm.on_price_update("EURUSD", bid=1.1201, ask=1.1202, time_s=time.time())

        assert pos.status == PositionStatus.CLOSED


# ─── TEST 3: MANUAL CLOSE FROM MT5 TERMINAL ──────────────────────────────────

class TestManualClose:
    def test_manual_close_reconciles(self):
        """Position manually closed in MT5 → detected on next tick, marked closed."""
        execution = _mock_execution(
            ExecutionResult(ok=False, retcode=-1, deal=0, order=0, comment="POSITION_NOT_FOUND")
        )
        tm = TradeStateManager(_cfg(), execution=execution)

        # Position is BUY at 1.10, SL=1.09, current price between SL and TP
        # But broker reports POSITION_NOT_FOUND (manual close happened)
        pos = _make_position(side=Side.BUY, entry=1.10, sl=1.09, tp=1.12, ticket=55555)
        # Set SL very close so tick triggers exit detection
        pos.stop_loss = 1.1050
        tm._by_id[pos.position_id] = pos

        # Tick at 1.1049 (below stop_loss 1.1050)
        tm.on_price_update("EURUSD", bid=1.1049, ask=1.1050, time_s=time.time())

        assert pos.status == PositionStatus.CLOSED


# ─── TEST 4: RETRY QUEUE CLEARS ON POSITION_NOT_FOUND ─────────────────────────

class TestRetryQueueReconciliation:
    def test_retry_queue_position_not_found_completes(self):
        """If retry gets POSITION_NOT_FOUND, remove from queue and mark closed."""
        execution = _mock_execution(
            ExecutionResult(ok=False, retcode=-1, deal=0, order=0, comment="POSITION_NOT_FOUND")
        )
        tm = TradeStateManager(_cfg(), execution=execution)

        pos = _make_position(ticket=44444)
        tm._by_id[pos.position_id] = pos

        # Manually add to retry queue (simulating prior genuine failure)
        from core.trade_management.manager import _CloseRetryEntry
        tm._close_retry_queue[pos.position_id] = _CloseRetryEntry(
            position_id=pos.position_id,
            symbol="EURUSD",
            position_ticket=44444,
            volume=None,
            kind=TradeLifecycleEvent.ON_STOP_LOSS_HIT,
            prices=(1.0899, 1.0900),
            detail={},
            retry_count=0,
            last_attempt_time=time.time(),
        )

        tm.drain_close_retry_queue()

        # Queue should be empty
        assert len(tm._close_retry_queue) == 0
        # Position should be CLOSED
        assert pos.status == PositionStatus.CLOSED


# ─── TEST 5: EXISTING SUCCESSFUL CLOSE PATH ──────────────────────────────────

class TestNormalCloseStillWorks:
    def test_successful_broker_close(self):
        """Normal close path: broker confirms close → position closed locally."""
        execution = _mock_execution(
            ExecutionResult(ok=True, retcode=10009, deal=999, order=888, comment="closed")
        )
        listener = MagicMock()
        tm = TradeStateManager(_cfg(), listener=listener, execution=execution)

        pos = _make_position(ticket=33333)
        tm._by_id[pos.position_id] = pos

        tm.on_price_update("EURUSD", bid=1.0899, ask=1.0900, time_s=time.time())

        assert pos.status == PositionStatus.CLOSED
        assert len(tm._close_retry_queue) == 0

    def test_genuine_failure_still_retries(self):
        """Non-POSITION_NOT_FOUND failures still enter retry queue."""
        execution = _mock_execution(
            ExecutionResult(ok=False, retcode=-1, deal=0, order=0, comment="CONNECTION_LOST")
        )
        tm = TradeStateManager(_cfg(), execution=execution)

        pos = _make_position(ticket=22222)
        tm._by_id[pos.position_id] = pos

        tm.on_price_update("EURUSD", bid=1.0899, ask=1.0900, time_s=time.time())

        # Should be queued for retry (not closed)
        assert pos.status == PositionStatus.OPEN
        assert len(tm._close_retry_queue) == 1


# ─── TEST 6: STARTUP RESYNC ORPHAN ───────────────────────────────────────────

class TestStartupResyncOrphan:
    def test_resync_marks_orphan_closed(self):
        """resync_positions marks internal orphans as CLOSED with closed_time."""
        from core.mt5_connection import resync_positions

        tm = TradeStateManager(_cfg())
        pos = _make_position(ticket=11111)
        tm._by_id[pos.position_id] = pos

        # Mock: broker has NO positions for this symbol
        with patch("core.mt5_connection.mt5_call", return_value=[]):
            resync_positions(tm, "EURUSD", 713001)

        assert pos.status == PositionStatus.CLOSED
        assert pos.closed_time is not None
