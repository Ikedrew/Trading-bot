"""
Tests for the management_actions dataset — every trade-management action
initiated by the trade-management layer is persisted as its own record.

Covers:
- SLTP_MODIFY creates one management-action record
- PARTIAL_CLOSE creates one management-action record
- CLOSE creates one management-action record
- A failed/rejected broker action still has its management-action record
- Existing trade_id and available lineage are propagated unchanged
- Missing lineage remains null rather than being invented
- Persistence failure cannot affect management execution
- Existing execution-attempt persistence remains unchanged
- Retried actions are preserved as separately initiated management actions

The dataset is observational only: no trading or trade-management behaviour
changes are exercised or asserted beyond the pre-existing contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.trade_management.config import TradeManagementConfig
from core.trade_management.manager import TradeStateManager
from core.trade_management.position import Position, PositionStatus
from core.trade_management.events import TradeLifecycleEvent
from core.trade_identity import TradeIdentity
from execution.mt5_execution import ExecutionResult, MT5Execution
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
    trade_identity: TradeIdentity | None = None,
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
        trade_identity=trade_identity,
    )


def _ok_result() -> ExecutionResult:
    return ExecutionResult(ok=True, retcode=10009, deal=99, order=88, comment="done")


def _fail_result() -> ExecutionResult:
    return ExecutionResult(ok=False, retcode=10004, deal=0, order=0, comment="requote")


def _read_management_records(tmpdir) -> list[dict]:
    files = list(Path(tmpdir).rglob("*.jsonl"))
    assert len(files) == 1, f"expected exactly one management_actions file, got {files}"
    lines = files[0].read_text().strip().split("\n")
    return [json.loads(l) for l in lines if l.strip()]


def _mock_tick(bid=1.08500, ask=1.08502):
    t = MagicMock()
    t.bid = bid
    t.ask = ask
    return t


def _mock_result(retcode, deal=0, order=0, comment="", price=None):
    r = MagicMock()
    r.retcode = retcode
    r.deal = deal
    r.order = order
    r.comment = comment
    r.price = price
    return r


# --- 1. SLTP_MODIFY ------------------------------------------------------------

class TestSltpModify:
    def test_sltp_modify_creates_one_management_action_record(self, tmpdir):
        """A SLTP_MODIFY initiation produces exactly one management_actions record."""
        mock_exec = MagicMock()
        mock_exec.position_modify_sl_tp.return_value = _ok_result()

        mgr = TradeStateManager(_cfg(), execution=mock_exec)
        pos = _make_position()
        mgr._by_id[pos.position_id] = pos

        with patch("core.persistence.management_actions_writer._LOCAL_DIR", str(tmpdir)), \
             patch("core.persistence.management_actions_writer._write_s3"):
            mgr._push_stops_to_server_if_possible(pos, action_reason="SL_MOVED_BREAKEVEN")

        records = _read_management_records(tmpdir)
        assert len(records) == 1
        record = records[0]
        assert record["schema_version"] == "management_actions_v1"
        assert record["action_type"] == "SLTP_MODIFY"
        assert record["action_reason"] == "SL_MOVED_BREAKEVEN"
        assert record["symbol"] == "EURUSD"
        assert record["requested_sl"] == pytest.approx(1.0950)
        assert record["requested_tp"] == pytest.approx(1.1100)
        assert record["requested_volume"] is None
        assert record["engine"] == "V10"
        assert record["management_action_id"]
        assert record["timestamp_utc"]
        assert record["timestamp_unix"] > 0
        # Management behaviour unchanged: broker modify still issued exactly once
        assert mock_exec.position_modify_sl_tp.call_count == 1

    def test_break_even_path_initiates_sltp_modify_end_to_end(self, tmpdir):
        """The real break-even path (on_price_update) initiates one SLTP_MODIFY record."""
        mock_exec = MagicMock()
        mock_exec.position_modify_sl_tp.return_value = _ok_result()

        # entry=1.1000, initial_sl=1.0950 -> R=0.005; trigger_rr=1.0 -> trigger at 1.1050
        cfg = _cfg(break_even_trigger_rr=1.0, break_even_buffer_rr=0.0)
        mgr = TradeStateManager(cfg, execution=mock_exec)
        pos = _make_position(entry=1.1000, sl=1.0950, tp=1.1100)
        mgr._by_id[pos.position_id] = pos

        with patch("core.persistence.management_actions_writer._LOCAL_DIR", str(tmpdir)), \
             patch("core.persistence.management_actions_writer._write_s3"):
            mgr.on_price_update("EURUSD", bid=1.1055, ask=1.1057, time_s=2000.0)

        records = _read_management_records(tmpdir)
        assert len(records) == 1
        assert records[0]["action_type"] == "SLTP_MODIFY"
        assert records[0]["action_reason"] == "SL_MOVED_BREAKEVEN"


# --- 2. PARTIAL_CLOSE -----------------------------------------------------------

class TestPartialClose:
    def test_partial_close_creates_one_management_action_record(self, tmpdir):
        """A PARTIAL_CLOSE initiation produces exactly one management_actions record."""
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = _ok_result()

        cfg = _cfg(partial_tp_fraction=0.5, partial_tp_path_fraction=0.5)
        mgr = TradeStateManager(cfg, execution=mock_exec)
        pos = _make_position(entry=1.1000, sl=1.0950, tp=1.1100, volume=0.10)
        mgr._by_id[pos.position_id] = pos

        with patch("core.persistence.management_actions_writer._LOCAL_DIR", str(tmpdir)), \
             patch("core.persistence.management_actions_writer._write_s3"):
            mgr._maybe_partial(pos, bid=1.1051, ask=1.1053, ts=2000.0, cfg=cfg)

        records = _read_management_records(tmpdir)
        assert len(records) == 1
        record = records[0]
        assert record["action_type"] == "PARTIAL_CLOSE"
        assert record["action_reason"] == "PARTIAL_TP"
        assert record["requested_volume"] == pytest.approx(0.05)
        assert record["requested_sl"] is None  # no SL requested by a close action
        assert record["requested_tp"] is None
        # Management behaviour unchanged: broker partial close still issued once
        assert mock_exec.close_position.call_count == 1


# --- 3. CLOSE --------------------------------------------------------------------

class TestClose:
    def test_close_creates_one_management_action_record(self, tmpdir):
        """A CLOSE initiation produces exactly one management_actions record."""
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = _ok_result()

        mgr = TradeStateManager(_cfg(), execution=mock_exec)
        pos = _make_position()
        mgr._by_id[pos.position_id] = pos

        with patch("core.persistence.management_actions_writer._LOCAL_DIR", str(tmpdir)), \
             patch("core.persistence.management_actions_writer._write_s3"):
            mgr._close_local(pos, TradeLifecycleEvent.ON_STOP_LOSS_HIT, (1.095, 1.096), 2000.0, {})

        records = _read_management_records(tmpdir)
        assert len(records) == 1
        record = records[0]
        assert record["action_type"] == "CLOSE"
        assert record["action_reason"] == "stop_loss"
        assert record["requested_sl"] is None
        assert record["requested_tp"] is None
        assert record["requested_volume"] is None
        # Management behaviour unchanged: broker close still issued once
        assert mock_exec.close_position.call_count == 1


# --- 4. FAILED / REJECTED BROKER ACTION -------------------------------------------

class TestFailedBrokerAction:
    def test_failed_broker_action_still_has_management_action_record(self, tmpdir):
        """Broker rejection still leaves a management-action record, and the
        existing failure behaviour (local state unchanged, retry queued) is intact."""
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = _fail_result()
        mock_exec.position_modify_sl_tp.return_value = _fail_result()

        mgr = TradeStateManager(_cfg(), execution=mock_exec)
        pos = _make_position()
        mgr._by_id[pos.position_id] = pos

        with patch("core.persistence.management_actions_writer._LOCAL_DIR", str(tmpdir)), \
             patch("core.persistence.management_actions_writer._write_s3"):
            # Failed close
            mgr._close_local(pos, TradeLifecycleEvent.ON_STOP_LOSS_HIT, (1.095, 1.096), 2000.0, {})
            # Failed SLTP modify
            mgr._push_stops_to_server_if_possible(pos, action_reason="SL_MOVED_TRAILING")

        records = _read_management_records(tmpdir)
        assert len(records) == 2
        assert {r["action_type"] for r in records} == {"CLOSE", "SLTP_MODIFY"}
        # Pre-existing failure behaviour unchanged
        assert pos.status == PositionStatus.OPEN  # close failed -> still open
        assert pos.position_id in mgr._close_retry_queue  # close retry queued
        assert pos.mt5_ticket in mgr._sltp_retry_queue  # sltp retry queued


# --- 5/6. LINEAGE -----------------------------------------------------------------

class TestLineagePropagation:
    def test_trade_id_and_lineage_propagated_unchanged(self, tmpdir):
        """Existing trade_id and available lineage are propagated verbatim."""
        ti = TradeIdentity(
            correlation_id="COR-20260830-EURUSD-ab12",
            decision_id="DEC-42",
            canonical_opportunity_id="OPP-42",
            observation_id="OBS-42",
            cycle_id=7,
        )
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = _ok_result()

        mgr = TradeStateManager(_cfg(), execution=mock_exec)
        pos = _make_position(position_id="pos_777", trade_identity=ti)
        mgr._by_id[pos.position_id] = pos

        with patch("core.persistence.management_actions_writer._LOCAL_DIR", str(tmpdir)), \
             patch("core.persistence.management_actions_writer._write_s3"):
            mgr._close_local(pos, TradeLifecycleEvent.ON_STOP_LOSS_HIT, (1.095, 1.096), 2000.0, {})

        record = _read_management_records(tmpdir)[0]
        assert record["trade_id"] == "pos_777"  # position identity, verbatim
        assert record["decision_id"] == "DEC-42"
        assert record["canonical_opportunity_id"] == "OPP-42"
        assert record["observation_id"] == "OBS-42"
        assert record["correlation_id"] == "COR-20260830-EURUSD-ab12"
        assert record["cycle_id"] == 7

    def test_missing_lineage_remains_null(self, tmpdir):
        """Positions without trade_identity (e.g. recovered) persist null lineage —
        no IDs are invented; trade_id still comes from the position identity."""
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = _ok_result()

        mgr = TradeStateManager(_cfg(), execution=mock_exec)
        pos = _make_position(position_id="pos_888", trade_identity=None)
        assert pos.trade_identity is None
        mgr._by_id[pos.position_id] = pos

        with patch("core.persistence.management_actions_writer._LOCAL_DIR", str(tmpdir)), \
             patch("core.persistence.management_actions_writer._write_s3"):
            mgr._close_local(pos, TradeLifecycleEvent.ON_TAKE_PROFIT_HIT, (1.110, 1.111), 2000.0, {})

        record = _read_management_records(tmpdir)[0]
        assert record["trade_id"] == "pos_888"  # always available from the position
        assert record["decision_id"] is None
        assert record["canonical_opportunity_id"] is None
        assert record["observation_id"] is None
        assert record["correlation_id"] is None
        assert record["cycle_id"] == 0


# --- 7. PERSISTENCE FAILURE SAFETY -------------------------------------------------

class TestPersistenceFailureSafety:
    def test_persistence_failure_cannot_affect_management_execution(self, tmpdir):
        """If the writer explodes, the management action still executes normally."""
        mock_exec = MagicMock()
        mock_exec.close_position.return_value = _ok_result()
        mock_exec.position_modify_sl_tp.return_value = _ok_result()

        mgr = TradeStateManager(_cfg(), execution=mock_exec)
        pos = _make_position()
        mgr._by_id[pos.position_id] = pos

        with patch(
            "core.persistence.management_actions_writer.persist_management_action",
            side_effect=RuntimeError("writer exploded"),
        ):
            # Must not raise, must not alter management behaviour
            mgr._close_local(pos, TradeLifecycleEvent.ON_STOP_LOSS_HIT, (1.095, 1.096), 2000.0, {})
            mgr._push_stops_to_server_if_possible(pos, action_reason="SL_MOVED_BREAKEVEN")

        assert pos.status == PositionStatus.CLOSED
        assert mock_exec.close_position.call_count == 1
        assert mock_exec.position_modify_sl_tp.call_count == 1


# --- RETRY ACTIONS ARE PRESERVED SEPARATELY ----------------------------------------

class TestRetryActionsPreservedSeparately:
    def test_retry_drain_persists_separate_management_action(self, tmpdir):
        """A retried close is its own separately initiated management action —
        never collapsed with the original."""
        mock_exec = MagicMock()
        mock_exec.close_position.side_effect = [_fail_result(), _ok_result()]

        mgr = TradeStateManager(_cfg(), execution=mock_exec)
        pos = _make_position()
        mgr._by_id[pos.position_id] = pos

        with patch("core.persistence.management_actions_writer._LOCAL_DIR", str(tmpdir)), \
             patch("core.persistence.management_actions_writer._write_s3"):
            mgr._close_local(pos, TradeLifecycleEvent.ON_STOP_LOSS_HIT, (1.095, 1.096), 2000.0, {})
            mgr.drain_close_retry_queue()

        records = _read_management_records(tmpdir)
        assert len(records) == 2
        assert all(r["action_type"] == "CLOSE" for r in records)
        reasons = {r["action_reason"] for r in records}
        assert "stop_loss" in reasons  # original initiation
        assert "RETRY" in reasons  # retry initiation
        ids = {r["management_action_id"] for r in records}
        assert len(ids) == 2  # distinct management actions


# --- 8. EXECUTION-ATTEMPT PERSISTENCE REMAINS UNCHANGED -----------------------------

class TestExecutionAttemptPersistenceUnchanged:
    def test_close_via_real_execution_layer_persists_both_datasets(self, tmpdir):
        """Driving a close through the REAL MT5Execution layer proves the
        execution_attempts dataset still records the broker call unchanged,
        alongside the new management_actions record."""
        exec_engine = MT5Execution()
        pos_mock = MagicMock()
        pos_mock.type = 0  # BUY position -> close with SELL
        pos_mock.magic = 713001
        pos_mock.volume = 0.10
        pos_mock.side = Side.BUY
        call_sequence = [
            (pos_mock,),                     # ownership check positions_get
            (pos_mock,),                     # position details positions_get
            _mock_tick(),                    # symbol_info_tick
            _mock_result(10009, deal=55, order=66, comment="Done", price=1.095),
        ]
        call_idx = [0]

        def _side_effect(fn, *args, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            return call_sequence[idx] if idx < len(call_sequence) else None

        attempts_dir = str(Path(tmpdir) / "attempts")
        mgmt_dir = str(Path(tmpdir) / "mgmt")

        mgr = TradeStateManager(_cfg(), execution=exec_engine)
        pos = _make_position(position_id="pos_4242", mt5_ticket=4242)
        mgr._by_id[pos.position_id] = pos

        with patch("execution.mt5_execution.mt5_call", side_effect=_side_effect), \
             patch("execution.mt5_execution.mt5.TRADE_RETCODE_DONE", 10009), \
             patch("execution.mt5_execution.mt5.ORDER_TYPE_BUY", 0), \
             patch("execution.mt5_execution.mt5.ORDER_TYPE_SELL", 1), \
             patch("execution.mt5_execution._filling_mode", return_value=1), \
             patch("core.position_ownership.enforce_position_ownership", return_value=True), \
             patch("core.persistence.execution_attempts_writer._LOCAL_DIR", attempts_dir), \
             patch("core.persistence.execution_attempts_writer._write_s3"), \
             patch("core.persistence.management_actions_writer._LOCAL_DIR", mgmt_dir), \
             patch("core.persistence.management_actions_writer._write_s3"):
            mgr._close_local(pos, TradeLifecycleEvent.ON_STOP_LOSS_HIT, (1.095, 1.096), 2000.0, {})

        # execution_attempts dataset: unchanged — still exactly one broker-attempt record
        attempt_files = list(Path(attempts_dir).rglob("*.jsonl"))
        assert len(attempt_files) == 1
        attempts = [json.loads(l) for l in attempt_files[0].read_text().strip().split("\n") if l.strip()]
        assert len(attempts) == 1
        assert attempts[0]["action_type"] == "CLOSE"
        assert attempts[0]["trade_id"] == "pos_4242"
        assert attempts[0]["broker_result"]["ok"] is True

        # management_actions dataset: the management layer's initiation record
        mgmt_files = list(Path(mgmt_dir).rglob("*.jsonl"))
        assert len(mgmt_files) == 1
        mgmt = [json.loads(l) for l in mgmt_files[0].read_text().strip().split("\n") if l.strip()]
        assert len(mgmt) == 1
        assert mgmt[0]["action_type"] == "CLOSE"
        assert mgmt[0]["trade_id"] == "pos_4242"
        assert mgmt[0]["schema_version"] == "management_actions_v1"
