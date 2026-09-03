"""
Tests for B2: Trade Management Broker Close Fix.

Covers:
- Full close: broker success ? local state closed
- Full close: broker failure ? local state remains OPEN, retry queued
- Partial close: broker success ? local volume reduced
- Partial close: broker failure ? local volume unchanged, retry queued
- Retry drain: successful retry closes locally
- Retry drain: max retries exhausted ? abandoned
- No execution layer: falls through to local-only (DRY_RUN safety)
- No MT5 ticket: falls through to local-only
- Idempotent retry queue (no duplicate entries)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.trade_management.config import TradeManagementConfig
from core.trade_management.manager import TradeStateManager, _MAX_RETRIES
from core.trade_management.position import Position, PositionStatus
from core.trade_management.events import TradeLifecycleEvent
from execution.mt5_execution import ExecutionResult
from strategy.signals import Side


# --- FIXTURES -----------------------------------------------------------------

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


def _make_position(
    position_id: str = "pos_001",
    symbol: str = "EURUSD",
    side: Side = Side.BUY,
    entry: float = 1.1000,
    sl: float = 1.0950,
    tp: float = 1.1100,
    volume: float = 0.10,
    mt5_ticket: int = 12345,
) -> Position:
    return Position(
        position_id=position_id,
        symbol=symbol,
        side=side,
        magic=713001,
        entry_price=entry,
        initial_sl=sl,
        initial_tp=tp,
        stop_loss=sl,
        take_profit=tp,
        volume=volume,
        open_time=1000.0,
        status=PositionStatus.OPEN,
        mt5_ticket=mt5_ticket,
        deal_id=mt5_ticket,
        order_id=99999,
        max_favourable_price=entry,
    )


def _ok_result() -> ExecutionResult:
    return ExecutionResult(ok=True, retcode=10009, deal=99, order=88, comment="done")


def _fail_result() -> ExecutionResult:
    return ExecutionResult(ok=False, retcode=10004, deal=0, order=0, comment="requote")


# --- FULL CLOSE TESTS ---------------------------------------------------------

class TestFullCloseBrokerSuccess:
    def test_broker_close_success_marks_closed(self):
        """When broker confirms close, local state is CLOSED."""
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = _ok_result()

        mgr = TradeStateManager(_cfg(), execution=mock_exec)
        pos = _make_position()
        mgr._by_id[pos.position_id] = pos

        mgr._close_local(pos, TradeLifecycleEvent.ON_STOP_LOSS_HIT, (1.095, 1.096), 2000.0, {})

        assert pos.status == PositionStatus.CLOSED
        mock_exec.close_position.assert_called_once_with(
            symbol="EURUSD",
            position_ticket=12345,
            volume=None,
            trade_id="pos_001",
        )

    def test_broker_close_success_no_retry_queued(self):
        """Successful close should not queue any retry."""
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = _ok_result()

        mgr = TradeStateManager(_cfg(), execution=mock_exec)
        pos = _make_position()
        mgr._by_id[pos.position_id] = pos

        mgr._close_local(pos, TradeLifecycleEvent.ON_MANAGEMENT_EXIT, (1.10, 1.101), 2000.0, {"reason": "max_time"})

        assert len(mgr._close_retry_queue) == 0


class TestFullCloseBrokerFailure:
    def test_broker_close_failure_stays_open(self):
        """When broker rejects close, position remains OPEN locally."""
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = _fail_result()

        mgr = TradeStateManager(_cfg(), execution=mock_exec)
        pos = _make_position()
        mgr._by_id[pos.position_id] = pos

        mgr._close_local(pos, TradeLifecycleEvent.ON_STOP_LOSS_HIT, (1.095, 1.096), 2000.0, {})

        assert pos.status == PositionStatus.OPEN

    def test_broker_close_failure_queues_retry(self):
        """Failed close is queued for retry."""
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = _fail_result()

        mgr = TradeStateManager(_cfg(), execution=mock_exec)
        pos = _make_position()
        mgr._by_id[pos.position_id] = pos

        mgr._close_local(pos, TradeLifecycleEvent.ON_STOP_LOSS_HIT, (1.095, 1.096), 2000.0, {})

        assert pos.position_id in mgr._close_retry_queue
        entry = mgr._close_retry_queue[pos.position_id]
        assert entry.volume is None  # Full close
        assert entry.retry_count == 0

    def test_duplicate_close_not_double_queued(self):
        """Repeated close attempts for same position don't create duplicate queue entries."""
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = _fail_result()

        mgr = TradeStateManager(_cfg(), execution=mock_exec)
        pos = _make_position()
        mgr._by_id[pos.position_id] = pos

        mgr._close_local(pos, TradeLifecycleEvent.ON_STOP_LOSS_HIT, (1.095, 1.096), 2000.0, {})
        mgr._close_local(pos, TradeLifecycleEvent.ON_STOP_LOSS_HIT, (1.094, 1.095), 2100.0, {})

        assert len(mgr._close_retry_queue) == 1


# --- PARTIAL CLOSE TESTS ------------------------------------------------------

class TestPartialCloseBrokerSuccess:
    def test_partial_close_success_reduces_volume(self):
        """Broker confirms partial ? local volume reduced."""
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = _ok_result()

        mgr = TradeStateManager(
            _cfg(partial_tp_fraction=0.5, partial_tp_path_fraction=0.5),
            execution=mock_exec,
        )
        pos = _make_position(volume=0.10, entry=1.1000, tp=1.1100, side=Side.BUY)
        mgr._by_id[pos.position_id] = pos

        # Trigger partial: bid must be >= entry + (tp-entry)*path = 1.1000 + 0.005 = 1.1050
        result = mgr._maybe_partial(pos, bid=1.1060, ask=1.1062, ts=3000.0)

        assert pos.volume == pytest.approx(0.05, abs=1e-8)
        assert pos.status == PositionStatus.PARTIAL
        mock_exec.close_position.assert_called_once_with(
            symbol="EURUSD",
            position_ticket=12345,
            volume=pytest.approx(0.05, abs=1e-8),
            trade_id="pos_001",
        )

    def test_partial_close_success_no_retry(self):
        """Successful partial should not queue retry."""
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = _ok_result()

        mgr = TradeStateManager(
            _cfg(partial_tp_fraction=0.5, partial_tp_path_fraction=0.5),
            execution=mock_exec,
        )
        pos = _make_position(volume=0.10, entry=1.1000, tp=1.1100, side=Side.BUY)
        mgr._by_id[pos.position_id] = pos

        mgr._maybe_partial(pos, bid=1.1060, ask=1.1062, ts=3000.0)

        assert len(mgr._close_retry_queue) == 0


class TestPartialCloseBrokerFailure:
    def test_partial_close_failure_volume_unchanged(self):
        """Broker rejects partial ? local volume NOT changed."""
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = _fail_result()

        mgr = TradeStateManager(
            _cfg(partial_tp_fraction=0.5, partial_tp_path_fraction=0.5),
            execution=mock_exec,
        )
        pos = _make_position(volume=0.10, entry=1.1000, tp=1.1100, side=Side.BUY)
        mgr._by_id[pos.position_id] = pos

        mgr._maybe_partial(pos, bid=1.1060, ask=1.1062, ts=3000.0)

        assert pos.volume == pytest.approx(0.10, abs=1e-8)
        assert pos.status == PositionStatus.OPEN

    def test_partial_close_failure_queues_retry(self):
        """Failed partial close queues retry with correct volume."""
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = _fail_result()

        mgr = TradeStateManager(
            _cfg(partial_tp_fraction=0.5, partial_tp_path_fraction=0.5),
            execution=mock_exec,
        )
        pos = _make_position(volume=0.10, entry=1.1000, tp=1.1100, side=Side.BUY)
        mgr._by_id[pos.position_id] = pos

        mgr._maybe_partial(pos, bid=1.1060, ask=1.1062, ts=3000.0)

        assert pos.position_id in mgr._close_retry_queue
        entry = mgr._close_retry_queue[pos.position_id]
        assert entry.volume == pytest.approx(0.05, abs=1e-8)  # 50% of 0.10


# --- RETRY DRAIN TESTS --------------------------------------------------------

class TestCloseRetryDrain:
    def test_retry_success_closes_position(self):
        """Successful retry marks position closed and removes from queue."""
        mock_exec = MagicMock()
        # First call fails, second succeeds
        mock_exec.close_position.side_effect = [_fail_result(), _ok_result()]

        mgr = TradeStateManager(_cfg(), execution=mock_exec)
        pos = _make_position()
        mgr._by_id[pos.position_id] = pos

        # Trigger close ? fails ? queued
        mgr._close_local(pos, TradeLifecycleEvent.ON_STOP_LOSS_HIT, (1.095, 1.096), 2000.0, {})
        assert pos.status == PositionStatus.OPEN
        assert len(mgr._close_retry_queue) == 1

        # Drain retry ? succeeds
        mgr.drain_close_retry_queue()
        assert pos.status == PositionStatus.CLOSED
        assert len(mgr._close_retry_queue) == 0

    def test_retry_success_merges_broker_close_facts(self):
        """Successful full-close retry reconciles genuine broker close facts.

        Regression (broker-close forensic audit, Issue 1): a successful retry
        must reconcile broker close history via the SAME canonical mechanism as
        the normal close path and merge broker_profit/commission/swap plus the
        broker exit info into the ON_TRADE_CLOSE detail, so trade_journal /
        trade_truth receive the same broker-derived accounting semantics as
        normal closes.
        """
        listener = MagicMock()
        mock_exec = MagicMock()
        mock_exec.close_position.side_effect = [_fail_result(), _ok_result()]

        brok = {
            "reason": "stop_loss",
            "broker_exit_price": 1.0951,
            "broker_exit_time": 2500.0,
            "broker_profit": -12.34,
            "broker_commission": -0.05,
            "broker_swap": 0.0,
            "broker_deal_id": 777,
            "broker_deal_reason": 4,
            "broker_comment": "[sl]",
        }

        mgr = TradeStateManager(_cfg(), listener=listener, execution=mock_exec)
        pos = _make_position()
        mgr._by_id[pos.position_id] = pos
        mgr._query_broker_close_history = MagicMock(return_value=brok)

        # First attempt fails -> queued; drain retry succeeds
        mgr._close_local(pos, TradeLifecycleEvent.ON_STOP_LOSS_HIT, (1.095, 1.096), 2000.0, {})
        assert pos.status == PositionStatus.OPEN
        assert len(mgr._close_retry_queue) == 1

        mgr.drain_close_retry_queue()
        assert pos.status == PositionStatus.CLOSED
        assert len(mgr._close_retry_queue) == 0

        # The canonical broker-history query must have been used on the retry
        mgr._query_broker_close_history.assert_called_with(pos)

        close_events = [
            e for e in (c.args[0] for c in listener.on_trade_event.call_args_list)
            if e.kind == TradeLifecycleEvent.ON_TRADE_CLOSE
        ]
        assert close_events, "ON_TRADE_CLOSE must be emitted after a successful retry"
        d = close_events[-1].detail
        assert d["broker_profit"] == -12.34
        assert d["broker_commission"] == -0.05
        assert d["broker_swap"] == 0.0
        assert d["broker_exit_price"] == 1.0951
        assert d["broker_exit_time"] == 2500.0
        assert d["broker_deal_id"] == 777
        assert d["broker_deal_reason"] == 4
        # Close reason/context preserved
        assert d["reason"] == "stop_loss"

    def test_retry_success_without_broker_truth_no_placeholders(self):
        """When broker history cannot be resolved, a successful retry must NOT
        introduce monetary placeholders (e.g. the old -0.09/-0.02 constants)."""
        listener = MagicMock()
        mock_exec = MagicMock()
        mock_exec.close_position.side_effect = [_fail_result(), _ok_result()]

        mgr = TradeStateManager(_cfg(), listener=listener, execution=mock_exec)
        pos = _make_position()
        mgr._by_id[pos.position_id] = pos
        mgr._query_broker_close_history = MagicMock(return_value=None)

        mgr._close_local(pos, TradeLifecycleEvent.ON_TAKE_PROFIT_HIT, (1.109, 1.110), 2000.0, {})
        assert len(mgr._close_retry_queue) == 1

        mgr.drain_close_retry_queue()
        assert pos.status == PositionStatus.CLOSED

        close_events = [
            e for e in (c.args[0] for c in listener.on_trade_event.call_args_list)
            if e.kind == TradeLifecycleEvent.ON_TRADE_CLOSE
        ]
        assert close_events
        d = close_events[-1].detail
        # No fabricated monetary facts
        assert "broker_profit" not in d
        assert "broker_commission" not in d
        assert "broker_swap" not in d
        assert d.get("realised_pnl", None) != -0.09
        # Lifecycle reason preserved
        assert d["reason"] == "take_profit"

    def test_retry_partial_success_reduces_volume(self):
        """Successful retry for partial close reduces volume."""
        mock_exec = MagicMock()
        mock_exec.close_position.side_effect = [_fail_result(), _ok_result()]

        mgr = TradeStateManager(
            _cfg(partial_tp_fraction=0.5, partial_tp_path_fraction=0.5),
            execution=mock_exec,
        )
        pos = _make_position(volume=0.10, entry=1.1000, tp=1.1100, side=Side.BUY)
        mgr._by_id[pos.position_id] = pos

        # Trigger partial ? fails ? queued
        mgr._maybe_partial(pos, bid=1.1060, ask=1.1062, ts=3000.0)
        assert pos.volume == pytest.approx(0.10)
        assert len(mgr._close_retry_queue) == 1

        # Drain retry ? succeeds
        mgr.drain_close_retry_queue()
        assert pos.volume == pytest.approx(0.05, abs=1e-8)
        assert pos.status == PositionStatus.PARTIAL
        assert len(mgr._close_retry_queue) == 0

    def test_retry_exhausted_abandons(self):
        """After max retries, entry is removed and position stays open."""
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = _fail_result()

        mgr = TradeStateManager(_cfg(), execution=mock_exec)
        pos = _make_position()
        mgr._by_id[pos.position_id] = pos

        # Trigger close ? fails ? queued
        mgr._close_local(pos, TradeLifecycleEvent.ON_STOP_LOSS_HIT, (1.095, 1.096), 2000.0, {})

        # Drain N times until exhausted
        for _ in range(_MAX_RETRIES):
            mgr.drain_close_retry_queue()

        assert pos.status == PositionStatus.OPEN
        assert len(mgr._close_retry_queue) == 0


# --- FALLBACK / DRY-RUN TESTS ------------------------------------------------

class TestFallbackBehaviour:
    def test_no_execution_layer_closes_locally(self):
        """Without execution layer, close is local-only (DRY_RUN safety)."""
        mgr = TradeStateManager(_cfg(), execution=None)
        pos = _make_position()
        mgr._by_id[pos.position_id] = pos

        mgr._close_local(pos, TradeLifecycleEvent.ON_STOP_LOSS_HIT, (1.095, 1.096), 2000.0, {})

        assert pos.status == PositionStatus.CLOSED
        assert len(mgr._close_retry_queue) == 0

    def test_no_ticket_closes_locally(self):
        """Without MT5 ticket, close is local-only."""
        mock_exec = MagicMock()
        mgr = TradeStateManager(_cfg(), execution=mock_exec)
        pos = _make_position(mt5_ticket=0)
        mgr._by_id[pos.position_id] = pos

        mgr._close_local(pos, TradeLifecycleEvent.ON_MANAGEMENT_EXIT, (1.10, 1.101), 2000.0, {"reason": "max_time"})

        assert pos.status == PositionStatus.CLOSED
        mock_exec.close_position.assert_not_called()

    def test_none_ticket_closes_locally(self):
        """With mt5_ticket=None, close is local-only."""
        mock_exec = MagicMock()
        mgr = TradeStateManager(_cfg(), execution=mock_exec)
        pos = _make_position()
        pos.mt5_ticket = None
        mgr._by_id[pos.position_id] = pos

        mgr._close_local(pos, TradeLifecycleEvent.ON_TAKE_PROFIT_HIT, (1.11, 1.111), 2000.0, {})

        assert pos.status == PositionStatus.CLOSED
        mock_exec.close_position.assert_not_called()
