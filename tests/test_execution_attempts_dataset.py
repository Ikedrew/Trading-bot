"""
Tests for execution_attempts dataset — every broker call is persisted.

Covers:
- First attempt receives attempt_number=1
- Retry receives incremented attempt number
- Every broker attempt creates a separate record
- Intermediate failed attempts are persisted
- Final successful attempt is persisted
- retry_reason preserved correctly
- Failed attempts have no fabricated fill price
- Successful slippage calculated using existing convention
- Lineage preserved
- action_type is correct
- Writer failure does not affect execution behaviour
- Records written to correct symbol/date partition
- JSONL records are valid
- Attempt IDs are unique
- Each attempt persists its own market snapshot (retry records the refreshed bid/ask, not the original)
- Existing retry behaviour unchanged
- Broker protection fields (protection_status / broker_confirmed_sl / broker_confirmed_tp)
  stay null unless genuine confirmation is supplied; requested SL/TP are never
  mislabelled as broker-confirmed (MT5 order_send does not echo confirmed SL/TP)
- Non-entry broker actions captured: CLOSE, PARTIAL_CLOSE and SLTP_MODIFY each
  produce attempt records with correct action_type, preserved trade_id, failure
  records for rejected/failed attempts, and persistence that cannot affect execution
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution.mt5_execution import (
    MT5Execution,
    _execution_metrics,
    _recent_intents,
    get_execution_metrics,
)
from execution.execution_orchestrator import ExecutionOrchestrator
from core.persistence.execution_attempts_writer import _SCHEMA_VERSION
from risk.models import OrderIntent
from strategy.signals import Side


# --- FIXTURES -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_metrics():
    _execution_metrics["total_submitted"] = 0
    _execution_metrics["total_success"] = 0
    _execution_metrics["total_failed"] = 0
    _execution_metrics["total_blocked"] = 0
    _execution_metrics["requote_retry_count"] = 0
    _execution_metrics["timeout_retry_count"] = 0
    _execution_metrics["total_retries"] = 0
    _execution_metrics["latency_sum_ms"] = 0.0
    _execution_metrics["latency_count"] = 0
    _execution_metrics["retcodes"] = {}
    _recent_intents.clear()
    with patch("execution.mt5_execution._cfg.DRY_RUN", False):
        yield


@pytest.fixture
def intent():
    return OrderIntent(
        symbol="EURUSD",
        side=Side.BUY,
        volume=0.01,
        entry_reference=1.08500,
        sl=1.08400,
        tp=1.08700,
        pattern="TEST_PATTERN",
    )


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


def _place_market_with_mocks(exec_engine, intent, call_sequence, tmpdir):
    """Helper: run place_market with properly mocked MT5 dependencies."""
    call_idx = [0]

    def _side_effect(fn, *args, **kwargs):
        idx = call_idx[0]
        call_idx[0] += 1
        return call_sequence[idx] if idx < len(call_sequence) else None

    with patch("execution.mt5_execution.mt5_call", side_effect=_side_effect), \
         patch("execution.mt5_execution.mt5.TRADE_RETCODE_DONE", 10009), \
         patch("execution.mt5_execution.mt5.ORDER_TYPE_BUY", 0), \
         patch("execution.mt5_execution._validate_order", return_value=(True, "")), \
         patch("execution.mt5_execution._filling_mode", return_value=1), \
         patch("execution.mt5_execution.check_spread") as mock_spread, \
         patch("execution.mt5_execution._is_duplicate_intent", return_value=False), \
         patch("core.persistence.execution_attempts_writer._LOCAL_DIR", tmpdir):
        mock_spread.return_value = MagicMock(allowed=True)
        return exec_engine.place_market(intent)


def _execute_with_mocks(exec_engine, intent, call_sequence, tmpdir, **kwargs):
    """Helper: run execute() with properly mocked MT5 dependencies."""
    call_idx = [0]

    def _side_effect(fn, *args, **kwargs):
        idx = call_idx[0]
        call_idx[0] += 1
        return call_sequence[idx] if idx < len(call_sequence) else None

    with patch("execution.mt5_execution.mt5_call", side_effect=_side_effect), \
         patch("execution.mt5_execution.mt5.TRADE_RETCODE_DONE", 10009), \
         patch("execution.mt5_execution.mt5.ORDER_TYPE_BUY", 0), \
         patch("execution.mt5_execution._validate_order", return_value=(True, "")), \
         patch("execution.mt5_execution._filling_mode", return_value=1), \
         patch("execution.mt5_execution.check_spread") as mock_spread, \
         patch("execution.mt5_execution._is_duplicate_intent", return_value=False), \
         patch("core.persistence.execution_attempts_writer._LOCAL_DIR", tmpdir):
        mock_spread.return_value = MagicMock(allowed=True)
        return exec_engine.execute(order_intent=intent, **kwargs)


# --- TEST 1: First attempt receives attempt_number=1 --------------------------

class TestFirstAttempt:
    def test_first_attempt_number_is_one(self, intent):
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            _place_market_with_mocks(
                exec_engine, intent,
                [_mock_tick(), _mock_result(10009, deal=1, order=123, comment="Done", price=1.08501)],
                tmpdir,
            )
            files = list(Path(tmpdir).rglob("*.jsonl"))
            assert len(files) == 1
            lines = files[0].read_text().strip().split("\n")
            assert len(lines) == 1
            record = json.loads(lines[0])
            assert record["attempt_number"] == 1
            assert record["retry_reason"] is None
            assert record["broker_result"]["ok"] is True


# --- TEST 2: Retry receives incremented attempt number ------------------------

class TestRetryAttemptNumber:
    @patch("execution.mt5_execution._time.sleep")
    def test_timeout_retry_has_attempt_number_two(self, mock_sleep, intent):
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            _place_market_with_mocks(
                exec_engine, intent,
                [
                    _mock_tick(),
                    _mock_result(10006, comment="Timeout"),
                    _mock_tick(),
                    _mock_result(10009, deal=1, order=123, comment="Done", price=1.08501),
                ],
                tmpdir,
            )
            files = list(Path(tmpdir).rglob("*.jsonl"))
            lines = files[0].read_text().strip().split("\n")
            assert len(lines) == 2
            records = [json.loads(l) for l in lines]
            assert records[0]["attempt_number"] == 1
            assert records[1]["attempt_number"] == 2


# --- TEST 3: Every broker attempt creates a separate record -------------------

class TestEveryAttemptRecorded:
    @patch("execution.mt5_execution._time.sleep")
    def test_two_attempts_timeout_then_success(self, mock_sleep, intent):
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            _place_market_with_mocks(
                exec_engine, intent,
                [
                    _mock_tick(),
                    _mock_result(10006, comment="Timeout"),
                    _mock_tick(),
                    _mock_result(10009, deal=1, order=123, comment="Done", price=1.08501),
                ],
                tmpdir,
            )
            files = list(Path(tmpdir).rglob("*.jsonl"))
            lines = files[0].read_text().strip().split("\n")
            assert len(lines) == 2


# --- TEST 4: Intermediate failed attempts are persisted -----------------------

class TestFailedAttemptsPersisted:
    @patch("execution.mt5_execution._time.sleep")
    def test_failed_first_attempt_persisted(self, mock_sleep, intent):
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _place_market_with_mocks(
                exec_engine, intent,
                [
                    _mock_tick(),
                    _mock_result(10006, comment="Timeout"),
                    _mock_tick(),
                    _mock_result(10006, comment="Timeout again"),
                ],
                tmpdir,
            )
            assert result.ok is False
            files = list(Path(tmpdir).rglob("*.jsonl"))
            lines = files[0].read_text().strip().split("\n")
            assert len(lines) == 2
            records = [json.loads(l) for l in lines]
            assert records[0]["broker_result"]["ok"] is False
            assert records[0]["broker_result"]["retcode"] == 10006
            assert records[1]["broker_result"]["ok"] is False
            assert records[1]["broker_result"]["retcode"] == 10006


# --- TEST 5: Final successful attempt is persisted ----------------------------

class TestSuccessfulAttemptPersisted:
    def test_successful_attempt_persisted(self, intent):
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            _place_market_with_mocks(
                exec_engine, intent,
                [_mock_tick(), _mock_result(10009, deal=1, order=123, comment="Done", price=1.08501)],
                tmpdir,
            )
            files = list(Path(tmpdir).rglob("*.jsonl"))
            lines = files[0].read_text().strip().split("\n")
            assert len(lines) == 1
            record = json.loads(lines[0])
            assert record["broker_result"]["ok"] is True
            assert record["broker_result"]["fill_price"] == 1.08501


# --- TEST 6: retry_reason preserved correctly ---------------------------------

class TestRetryReason:
    @patch("execution.mt5_execution._time.sleep")
    def test_requote_retry_reason(self, mock_sleep, intent):
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            _place_market_with_mocks(
                exec_engine, intent,
                [
                    _mock_tick(),
                    _mock_result(10004, comment="Requote"),
                    _mock_tick(),
                    _mock_result(10009, deal=1, order=123, comment="Done", price=1.08501),
                ],
                tmpdir,
            )
            files = list(Path(tmpdir).rglob("*.jsonl"))
            lines = files[0].read_text().strip().split("\n")
            records = [json.loads(l) for l in lines]
            assert records[0]["retry_reason"] is None
            assert records[1]["retry_reason"] == "REQUOTE"

    @patch("execution.mt5_execution._time.sleep")
    def test_timeout_retry_reason(self, mock_sleep, intent):
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            _place_market_with_mocks(
                exec_engine, intent,
                [
                    _mock_tick(),
                    _mock_result(10006, comment="Timeout"),
                    _mock_tick(),
                    _mock_result(10009, deal=1, order=123, comment="Done", price=1.08501),
                ],
                tmpdir,
            )
            files = list(Path(tmpdir).rglob("*.jsonl"))
            lines = files[0].read_text().strip().split("\n")
            records = [json.loads(l) for l in lines]
            assert records[0]["retry_reason"] is None
            assert records[1]["retry_reason"] == "TIMEOUT"


# --- TEST 7: Failed attempts have no fabricated fill price --------------------

class TestNoFabricatedFillPrice:
    def test_failed_attempt_fill_price_is_null(self, intent):
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            _place_market_with_mocks(
                exec_engine, intent,
                [_mock_tick(), _mock_result(10014, comment="Invalid stops", price=None)],
                tmpdir,
            )
            files = list(Path(tmpdir).rglob("*.jsonl"))
            lines = files[0].read_text().strip().split("\n")
            record = json.loads(lines[0])
            assert record["broker_result"]["fill_price"] is None
            assert record["slippage"] is None


# --- TEST 8: Successful slippage calculated -----------------------------------

class TestSlippageCalculation:
    def test_slippage_calculated_for_successful_fill(self, intent):
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            _place_market_with_mocks(
                exec_engine, intent,
                [_mock_tick(), _mock_result(10009, deal=1, order=123, comment="Done", price=1.08503)],
                tmpdir,
            )
            files = list(Path(tmpdir).rglob("*.jsonl"))
            lines = files[0].read_text().strip().split("\n")
            record = json.loads(lines[0])
            assert record["broker_result"]["ok"] is True
            assert record["slippage"] == pytest.approx(0.00003, abs=1e-6)


# --- TEST 9: Lineage preserved ------------------------------------------------

class TestLineage:
    def test_lineage_fields_present(self, intent):
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            _execute_with_mocks(
                exec_engine, intent,
                [_mock_tick(), _mock_result(10009, deal=1, order=123, comment="Done", price=1.08501)],
                tmpdir,
                decision_id="DEC-123",
                correlation_id="COR-456",
                cycle_id=42,
                canonical_opportunity_id="EURUSD*1784800000*TEST",
                observation_id="EURUSD.M5.1784800000",
                action_type="ENTRY",
            )
            files = list(Path(tmpdir).rglob("*.jsonl"))
            lines = files[0].read_text().strip().split("\n")
            record = json.loads(lines[0])
            assert record["decision_id"] == "DEC-123"
            assert record["correlation_id"] == "COR-456"
            assert record["cycle_id"] == 42
            assert record["canonical_opportunity_id"] == "EURUSD*1784800000*TEST"
            assert record["observation_id"] == "EURUSD.M5.1784800000"
            assert record["action_type"] == "ENTRY"



# --- TEST 10: action_type is correct ------------------------------------------

class TestActionType:
    def test_action_type_entry(self, intent):
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            _execute_with_mocks(
                exec_engine, intent,
                [_mock_tick(), _mock_result(10009, deal=1, order=123, comment="Done", price=1.08501)],
                tmpdir,
            )
            files = list(Path(tmpdir).rglob("*.jsonl"))
            lines = files[0].read_text().strip().split("\n")
            record = json.loads(lines[0])
            assert record["action_type"] == "ENTRY"


# --- TEST 11: Writer failure does not affect execution ------------------------

class TestWriterFailureIsolation:
    def test_execution_unchanged_when_writer_fails(self, intent):
        exec_engine = MT5Execution()
        with patch("core.persistence.execution_attempts_writer.persist_execution_attempt") as mock_writer:
            mock_writer.side_effect = RuntimeError("Disk full")
            result = _place_market_with_mocks(
                exec_engine, intent,
                [_mock_tick(), _mock_result(10009, deal=1, order=123, comment="Done", price=1.08501)],
                "/tmp/fake",
            )
        assert result.ok is True
        assert result.deal == 1
        assert result.order == 123


# --- TEST 12: Existing execution_results persistence remains intact -----------

class TestExecutionResultsIntact:
    @patch("core.persistence.execution_result_writer.persist_execution_result")
    def test_execution_result_still_persisted(self, mock_persist_result, intent):
        from execution.execution_orchestrator import ExecutionOrchestrator
        exec_engine = MT5Execution()
        orchestrator = ExecutionOrchestrator(exec_engine, MagicMock())

        call_idx = [0]
        call_sequence = [_mock_tick(), _mock_result(10009, deal=1, order=123, comment="Done", price=1.08501)]

        def _side_effect(fn, *args, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            return call_sequence[idx] if idx < len(call_sequence) else None

        with patch("execution.mt5_execution.mt5_call", side_effect=_side_effect), \
             patch("execution.mt5_execution.mt5.TRADE_RETCODE_DONE", 10009), \
             patch("execution.mt5_execution.mt5.ORDER_TYPE_BUY", 0), \
             patch("execution.mt5_execution._validate_order", return_value=(True, "")), \
             patch("execution.mt5_execution._filling_mode", return_value=1), \
             patch("execution.mt5_execution.check_spread") as mock_spread, \
             patch("execution.mt5_execution._is_duplicate_intent", return_value=False), \
             patch("core.persistence.execution_attempts_writer._LOCAL_DIR", "/tmp/fake"):
            mock_spread.return_value = MagicMock(allowed=True)
            orchestrator.execute_trade(
                intent=intent,
                symbol="EURUSD",
                cycle_id=5,
                decision_id="dec_123",
                correlation_id="cor_456",
                entity_id="ent_789",
                observation_id="obs_999",
                canonical_opportunity_id="cop_111",
                bid_at_execution=1.08500,
                ask_at_execution=1.08502,
                risk_distance=0.001,
                mt5_state="CONNECTED",
            )

        mock_persist_result.assert_called_once()


# --- TEST 13: Records written to correct symbol/date partition ----------------

class TestPartition:
    def test_written_to_symbol_date_partition(self, intent):
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            _place_market_with_mocks(
                exec_engine, intent,
                [_mock_tick(), _mock_result(10009, deal=1, order=123, comment="Done", price=1.08501)],
                tmpdir,
            )
            files = list(Path(tmpdir).rglob("*.jsonl"))
            assert len(files) == 1
            parts = files[0].relative_to(tmpdir).parts
            assert parts[0] == "EURUSD"
            assert len(parts) == 2


# --- TEST 14: JSONL records are valid -----------------------------------------

class TestJsonValidity:
    def test_records_are_valid_json(self, intent):
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            _place_market_with_mocks(
                exec_engine, intent,
                [_mock_tick(), _mock_result(10009, deal=1, order=123, comment="Done", price=1.08501)],
                tmpdir,
            )
            files = list(Path(tmpdir).rglob("*.jsonl"))
            lines = files[0].read_text().strip().split("\n")
            for line in lines:
                record = json.loads(line)
                assert "attempt_id" in record
                assert "schema_version" in record
                assert record["schema_version"] == _SCHEMA_VERSION


# --- TEST 15: Attempt IDs are unique ------------------------------------------

class TestAttemptIdUniqueness:
    @patch("execution.mt5_execution._time.sleep")
    def test_attempt_ids_are_unique(self, mock_sleep, intent):
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            _place_market_with_mocks(
                exec_engine, intent,
                [
                    _mock_tick(),
                    _mock_result(10004, comment="Requote"),
                    _mock_tick(),
                    _mock_result(10009, deal=1, order=123, comment="Done", price=1.08501),
                ],
                tmpdir,
            )
            files = list(Path(tmpdir).rglob("*.jsonl"))
            lines = files[0].read_text().strip().split("\n")
            records = [json.loads(l) for l in lines]
            ids = [r["attempt_id"] for r in records]
            assert len(ids) == len(set(ids))


# --- TEST 16: Attempt market snapshot reflects its own order_send() call ------

class TestAttemptMarketSnapshot:
    """Each broker attempt is persisted with the bid/ask that belonged to that
    specific order_send() call.

    Regression: a retry refreshes the tick before re-submitting, so the retry
    attempt must be persisted with the *refreshed* prices (tick B) — never the
    original attempt's prices (tick A). spread_at_attempt therefore reflects
    each attempt's own bid/ask.
    """

    def _read_records(self, tmpdir):
        """Read the persisted attempts keyed by attempt_number."""
        files = list(Path(tmpdir).rglob("*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text().strip().split("\n")
        return {r["attempt_number"]: r for r in (json.loads(l) for l in lines)}

    def test_requote_retry_persists_attempt_specific_bid_ask(self, intent):
        """REQUOTE: attempt #1 records tick A, retry records refreshed tick B."""
        tick_a = _mock_tick(bid=1.08500, ask=1.08502)
        requote = _mock_result(10004, comment="Requote")
        tick_b = _mock_tick(bid=1.08510, ask=1.08514)
        success = _mock_result(10009, deal=1, order=123, comment="Done", price=1.08514)

        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            _place_market_with_mocks(
                exec_engine, intent,
                [tick_a, requote, tick_b, success],
                tmpdir,
            )
            attempts = self._read_records(tmpdir)

        assert set(attempts) == {1, 2}

        # Attempt #1 carried the original tick (A)
        a1 = attempts[1]
        assert a1["bid_at_attempt"] == 1.08500
        assert a1["ask_at_attempt"] == 1.08502
        assert a1["spread_at_attempt"] == round(1.08502 - 1.08500, 8)

        # Attempt #2 (retry) carried the refreshed tick (B)
        a2 = attempts[2]
        assert a2["bid_at_attempt"] == 1.08510
        assert a2["ask_at_attempt"] == 1.08514
        assert a2["spread_at_attempt"] == round(1.08514 - 1.08510, 8)

        # The retry did NOT reuse the original attempt's snapshot
        assert (a1["bid_at_attempt"], a1["ask_at_attempt"]) != (
            a2["bid_at_attempt"], a2["ask_at_attempt"],
        )

    @patch("execution.mt5_execution._time.sleep")
    def test_timeout_retry_persists_attempt_specific_bid_ask(self, mock_sleep, intent):
        """TIMEOUT: attempt #1 records tick A, retry records refreshed tick B."""
        tick_a = _mock_tick(bid=1.08500, ask=1.08502)
        timeout = _mock_result(10006, comment="Timeout")
        tick_b = _mock_tick(bid=1.08508, ask=1.08511)
        success = _mock_result(10009, deal=1, order=123, comment="Done", price=1.08511)

        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            _place_market_with_mocks(
                exec_engine, intent,
                [tick_a, timeout, tick_b, success],
                tmpdir,
            )
            attempts = self._read_records(tmpdir)

        assert set(attempts) == {1, 2}

        assert attempts[1]["bid_at_attempt"] == 1.08500
        assert attempts[1]["ask_at_attempt"] == 1.08502
        assert attempts[1]["spread_at_attempt"] == round(1.08502 - 1.08500, 8)

        assert attempts[2]["bid_at_attempt"] == 1.08508
        assert attempts[2]["ask_at_attempt"] == 1.08511
        assert attempts[2]["spread_at_attempt"] == round(1.08511 - 1.08508, 8)

        assert (attempts[1]["bid_at_attempt"], attempts[1]["ask_at_attempt"]) != (
            attempts[2]["bid_at_attempt"], attempts[2]["ask_at_attempt"],
        )
        mock_sleep.assert_called_once_with(1.0)


# --- TEST 17: Existing trade_id propagated verbatim into execution_attempts ---

class TestTradeIdPropagation:
    """An existing trade_id is propagated through the execution call chain into
    execution_attempts — never generated, derived, or invented by the writer.

    The canonical live trade identity (``pos_{deal}``) is owned by
    ``TradeStateManager`` (``Position.position_id``) — the same value the trade
    journal uses via ``build_trade_record(trade_id=position.position_id)``.
    For CLOSE / SLTP_MODIFY attempts that identity already exists before the
    broker call and MUST be persisted on the attempt verbatim.  For ENTRY
    attempts the identity only materialises after a successful fill, so
    ``trade_id`` stays null there.
    """

    @staticmethod
    def _read_all_records(tmpdir):
        files = list(Path(tmpdir).rglob("*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text().strip().split("\n")
        return [json.loads(l) for l in lines]

    def test_close_position_persists_supplied_trade_id(self):
        """CLOSE attempt is persisted with the caller's existing trade_id."""
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._close_with_mocks(exec_engine, tmpdir, trade_id="pos_12345")
            assert result.ok is True
            records = self._read_all_records(tmpdir)

        assert len(records) == 1
        record = records[0]
        # The EXISTING trade identity is preserved verbatim...
        assert record["trade_id"] == "pos_12345"
        assert record["action_type"] == "CLOSE"
        assert record["attempt_number"] == 1
        assert record["broker_result"]["deal"] == 999
        # ...and NO other/different trade_id was invented anywhere in the file.
        assert all(r["trade_id"] == "pos_12345" for r in records)

    def test_modify_persists_supplied_trade_id(self):
        """SLTP_MODIFY attempt is persisted with the caller's existing trade_id."""
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._modify_with_mocks(exec_engine, tmpdir, trade_id="pos_12345")
            assert result.ok is True
            records = self._read_all_records(tmpdir)

        assert len(records) == 1
        record = records[0]
        assert record["trade_id"] == "pos_12345"
        assert record["action_type"] == "SLTP_MODIFY"
        assert record["attempt_number"] == 1
        assert all(r["trade_id"] == "pos_12345" for r in records)

    def test_entry_attempt_keeps_trade_id_null_when_not_available(self, intent):
        """ENTRY has no trade identity yet — trade_id stays null, not invented."""
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            _place_market_with_mocks(
                exec_engine, intent,
                [_mock_tick(), _mock_result(10009, deal=1, order=123, comment="Done", price=1.08501)],
                tmpdir,
            )
            records = self._read_all_records(tmpdir)

        assert len(records) == 1
        assert records[0]["trade_id"] is None
        assert records[0]["attempt_number"] == 1
        assert records[0]["action_type"] == "ENTRY"
# --- helpers -------------------------------------------------------------

    def _close_with_mocks(self, exec_engine, tmpdir, *, trade_id=""):
        """Drive close_position() with fully mocked MT5 dependencies."""
        pos = MagicMock()
        pos.type = 0            # BUY position -> close with SELL
        pos.magic = 713001
        pos.volume = 0.01
        pos.side = Side.BUY
        call_sequence = [
            (pos,),                                     # ownership check positions_get
            (pos,),                                     # position details positions_get
            _mock_tick(),                               # symbol_info_tick
            _mock_result(10009, deal=999, order=888, comment="Done", price=1.085),
        ]
        call_idx = [0]

        def _side_effect(fn, *args, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            return call_sequence[idx] if idx < len(call_sequence) else None

        with patch("execution.mt5_execution.mt5_call", side_effect=_side_effect), \
             patch("execution.mt5_execution.mt5.TRADE_RETCODE_DONE", 10009), \
             patch("execution.mt5_execution.mt5.ORDER_TYPE_BUY", 0), \
             patch("execution.mt5_execution.mt5.ORDER_TYPE_SELL", 1), \
             patch("execution.mt5_execution._filling_mode", return_value=1), \
             patch("core.position_ownership.enforce_position_ownership", return_value=True), \
             patch("core.persistence.execution_attempts_writer._LOCAL_DIR", tmpdir):
            return exec_engine.close_position(
                symbol="EURUSD",
                position_ticket=12345,
                volume=None,
                decision_id="DEC-1",
                correlation_id="COR-1",
                cycle_id=7,
                canonical_opportunity_id="OPP-1",
                observation_id="OBS-1",
                trade_id=trade_id,
            )

    def _modify_with_mocks(self, exec_engine, tmpdir, *, trade_id=""):
        """Drive position_modify_sl_tp() with fully mocked MT5 dependencies."""
        pos = MagicMock()
        pos.magic = 713001
        call_sequence = [
            (pos,),                                     # ownership check positions_get
            _mock_tick(),                               # symbol_info_tick
            _mock_result(10009, deal=999, order=888, comment="Done"),
        ]
        call_idx = [0]

        def _side_effect(fn, *args, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            return call_sequence[idx] if idx < len(call_sequence) else None

        with patch("execution.mt5_execution.mt5_call", side_effect=_side_effect), \
             patch("execution.mt5_execution.mt5.TRADE_RETCODE_DONE", 10009), \
             patch("core.position_ownership.enforce_position_ownership", return_value=True), \
             patch("core.persistence.execution_attempts_writer._LOCAL_DIR", tmpdir):
            return exec_engine.position_modify_sl_tp(
                symbol="EURUSD",
                position_ticket=12345,
                sl=1.08400,
                tp=1.08700,
                decision_id="DEC-1",
                correlation_id="COR-1",
                cycle_id=7,
                canonical_opportunity_id="OPP-1",
                observation_id="OBS-1",
                trade_id=trade_id,
            )
# --- TEST 18: Broker protection fields are never invented ----------------------

class TestBrokerProtectionFields:
    """protection_status / broker_confirmed_sl / broker_confirmed_tp.

    The MT5 ``order_send`` result carries only retcode/deal/order/comment —
    it does NOT echo back broker-confirmed SL/TP.  Submitting sl/tp in the
    request is not confirmation.  Therefore at every attempt point the
    persisted protection fields must stay null unless genuine confirmation
    is supplied, and the requested SL/TP must never be mislabelled as
    broker-confirmed.
    """

    @staticmethod
    def _read_all_records(tmpdir):
        files = list(Path(tmpdir).rglob("*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text().strip().split("\n")
        return [json.loads(l) for l in lines]

    def test_unavailable_protection_remains_null(self, intent):
        """Successful ENTRY with requested SL/TP: no broker confirmation exists
        at order_send() time, so all protection fields stay null."""
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            _place_market_with_mocks(
                exec_engine, intent,
                [_mock_tick(), _mock_result(10009, deal=1, order=123, comment="Done", price=1.08501)],
                tmpdir,
            )
            records = self._read_all_records(tmpdir)

        assert len(records) == 1
        record = records[0]
        assert record["protection_status"] is None
        assert record["broker_confirmed_sl"] is None
        assert record["broker_confirmed_tp"] is None
        # Requested SL/TP are still recorded — as *requested*, separately.
        assert record["requested_sl"] == pytest.approx(1.08400)
        assert record["requested_tp"] == pytest.approx(1.08700)

    def test_requested_sl_tp_not_labelled_broker_confirmed(self, intent):
        """The requested SL/TP values must never appear as broker-confirmed
        values in any attempt record (ENTRY and retry paths)."""
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as tmpdir:
            _place_market_with_mocks(
                exec_engine, intent,
                [
                    _mock_tick(),
                    _mock_result(10004, comment="Requote"),
                    _mock_tick(bid=1.08510, ask=1.08512),
                    _mock_result(10009, deal=2, order=124, comment="Done", price=1.08511),
                ],
                tmpdir,
            )
            records = self._read_all_records(tmpdir)

        assert len(records) == 2
        for record in records:
            assert record["protection_status"] is None
            assert record["broker_confirmed_sl"] is None
            assert record["broker_confirmed_tp"] is None
            # Neither record mislabels the requested levels as confirmed.
            assert record["broker_confirmed_sl"] != record["requested_sl"]
            assert record["broker_confirmed_tp"] != record["requested_tp"]

    def test_genuine_confirmation_persisted_verbatim_when_supplied(self, tmpdir):
        """When genuine broker-confirmed protection IS available upstream
        (post-fill verify_protection audit), the writer persists it verbatim —
        and never generates or alters it."""
        from core.persistence.execution_attempts_writer import persist_execution_attempt

        with patch("core.persistence.execution_attempts_writer._LOCAL_DIR", str(tmpdir)):
            ok = persist_execution_attempt(
                attempt_id="ATT-PROT-1",
                symbol="EURUSD",
                action_type="ENTRY",
                attempt_number=1,
                requested_sl=1.08400,
                requested_tp=1.08700,
                broker_ok=True,
                retcode=10009,
                protection_status="VERIFIED",
                broker_confirmed_sl=1.08401,
                broker_confirmed_tp=1.08699,
            )
        assert ok is True
        records = self._read_all_records(tmpdir)

        assert len(records) == 1
        record = records[0]
        assert record["protection_status"] == "VERIFIED"
        assert record["broker_confirmed_sl"] == pytest.approx(1.08401)
        assert record["broker_confirmed_tp"] == pytest.approx(1.08699)
        # Confirmed ≠ requested: the writer did not substitute one for the other.
        assert record["broker_confirmed_sl"] != record["requested_sl"]
        assert record["broker_confirmed_tp"] != record["requested_tp"]

    def test_writer_defaults_keep_protection_null(self, tmpdir):
        """A plain attempt record (no confirmation supplied) writes nulls —
        never fabricated defaults."""
        from core.persistence.execution_attempts_writer import persist_execution_attempt

        with patch("core.persistence.execution_attempts_writer._LOCAL_DIR", str(tmpdir)):
            ok = persist_execution_attempt(
                attempt_id="ATT-PROT-2",
                symbol="EURUSD",
                action_type="SLTP_MODIFY",
                attempt_number=1,
                requested_sl=1.08400,
                requested_tp=1.08700,
            )
        assert ok is True
        records = self._read_all_records(tmpdir)

        assert len(records) == 1
        record = records[0]
        assert record["protection_status"] is None
        assert record["broker_confirmed_sl"] is None
        assert record["broker_confirmed_tp"] is None

# --- TEST 19: Non-entry broker actions are captured ----------------------------

class TestNonEntryAttemptCapture:
    """CLOSE / PARTIAL_CLOSE / SLTP_MODIFY broker calls produce
    execution_attempts records with correct action_type, preserved trade_id,
    failure records for failed attempts, and fire-and-forget persistence that
    can never affect execution."""

    @staticmethod
    def _read_all_records(tmpdir):
        files = list(Path(tmpdir).rglob("*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text().strip().split("\n")
        return [json.loads(l) for l in lines]

    def _run_close(self, tmpdir, *, trade_id="pos_777", volume=None, send_result="__OK__"):
        """Drive close_position() with mocked MT5; send_result is the
        order_send return (None simulates order_send failure)."""
        exec_engine = MT5Execution()
        pos = MagicMock()
        pos.type = 0            # BUY position -> close with SELL
        pos.magic = 713001
        pos.volume = 0.01
        pos.side = Side.BUY
        if send_result == "__OK__":
            send_result = _mock_result(10009, deal=55, order=66, comment="Done", price=1.085)
        call_sequence = [
            (pos,),                                     # ownership check positions_get
            (pos,),                                     # position details positions_get
            _mock_tick(),                               # symbol_info_tick
            send_result,
        ]
        call_idx = [0]

        def _side_effect(fn, *args, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            return call_sequence[idx] if idx < len(call_sequence) else None

        with patch("execution.mt5_execution.mt5_call", side_effect=_side_effect), \
             patch("execution.mt5_execution.mt5.TRADE_RETCODE_DONE", 10009), \
             patch("execution.mt5_execution.mt5.ORDER_TYPE_BUY", 0), \
             patch("execution.mt5_execution.mt5.ORDER_TYPE_SELL", 1), \
             patch("execution.mt5_execution._filling_mode", return_value=1), \
             patch("core.position_ownership.enforce_position_ownership", return_value=True), \
             patch("core.persistence.execution_attempts_writer._LOCAL_DIR", tmpdir):
            return exec_engine.close_position(
                symbol="EURUSD",
                position_ticket=4242,
                volume=volume,
                decision_id="DEC-9",
                correlation_id="COR-9",
                cycle_id=3,
                canonical_opportunity_id="OPP-9",
                observation_id="OBS-9",
                trade_id=trade_id,
            )

    def _run_modify(self, tmpdir, *, trade_id="pos_777", send_result="__OK__"):
        """Drive position_modify_sl_tp() with mocked MT5; send_result is the
        order_send return (None simulates order_send failure)."""
        exec_engine = MT5Execution()
        pos = MagicMock()
        pos.magic = 713001
        if send_result == "__OK__":
            send_result = _mock_result(10009, deal=55, order=66, comment="Done")
        call_sequence = [
            (pos,),                                     # ownership check positions_get
            _mock_tick(),                               # symbol_info_tick
            send_result,
        ]
        call_idx = [0]

        def _side_effect(fn, *args, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            return call_sequence[idx] if idx < len(call_sequence) else None

        with patch("execution.mt5_execution.mt5_call", side_effect=_side_effect), \
             patch("execution.mt5_execution.mt5.TRADE_RETCODE_DONE", 10009), \
             patch("core.position_ownership.enforce_position_ownership", return_value=True), \
             patch("core.persistence.execution_attempts_writer._LOCAL_DIR", tmpdir):
            return exec_engine.position_modify_sl_tp(
                symbol="EURUSD",
                position_ticket=4242,
                sl=1.08400,
                tp=1.08700,
                decision_id="DEC-9",
                correlation_id="COR-9",
                cycle_id=3,
                canonical_opportunity_id="OPP-9",
                observation_id="OBS-9",
                trade_id=trade_id,
            )

    def test_close_produces_attempt(self, tmpdir):
        """A full CLOSE broker call produces one execution_attempt record."""
        result = self._run_close(tmpdir, trade_id="pos_777", volume=None)
        assert result.ok is True
        records = self._read_all_records(tmpdir)

        assert len(records) == 1
        record = records[0]
        assert record["action_type"] == "CLOSE"
        assert record["trade_id"] == "pos_777"
        assert record["attempt_number"] == 1
        assert record["broker_result"]["ok"] is True
        assert record["broker_result"]["retcode"] == 10009

    def test_partial_close_produces_attempt_with_action_type(self, tmpdir):
        """A partial close (volume supplied) produces its own record labelled
        PARTIAL_CLOSE, with the existing trade_id preserved."""
        result = self._run_close(tmpdir, trade_id="pos_777", volume=0.005)
        assert result.ok is True
        records = self._read_all_records(tmpdir)

        assert len(records) == 1
        record = records[0]
        assert record["action_type"] == "PARTIAL_CLOSE"
        assert record["trade_id"] == "pos_777"
        assert record["broker_result"]["ok"] is True

    def test_sltp_modify_produces_attempt(self, tmpdir):
        """An SLTP_MODIFY broker call produces one execution_attempt record."""
        result = self._run_modify(tmpdir, trade_id="pos_777")
        assert result.ok is True
        records = self._read_all_records(tmpdir)

        assert len(records) == 1
        record = records[0]
        assert record["action_type"] == "SLTP_MODIFY"
        assert record["trade_id"] == "pos_777"
        assert record["broker_result"]["ok"] is True

    def test_close_broker_failure_produces_attempt_record(self, tmpdir):
        """A rejected CLOSE still produces its attempt record with the real
        broker retcode/comment and ok=False (no fabricated fill)."""
        result = self._run_close(
            tmpdir, trade_id="pos_777", volume=None,
            send_result=_mock_result(10031, comment="Invalid request"),
        )
        assert result.ok is False
        records = self._read_all_records(tmpdir)

        assert len(records) == 1
        record = records[0]
        assert record["action_type"] == "CLOSE"
        assert record["trade_id"] == "pos_777"
        assert record["broker_result"]["ok"] is False
        assert record["broker_result"]["retcode"] == 10031
        assert record["broker_result"]["comment"] == "Invalid request"

    def test_modify_send_none_produces_failure_attempt(self, tmpdir):
        """order_send() returning None for SLTP_MODIFY persists a failure
        attempt (retcode -1, no fabricated fill price)."""
        result = self._run_modify(tmpdir, trade_id="pos_777", send_result=None)
        assert result.ok is False
        records = self._read_all_records(tmpdir)

        assert len(records) == 1
        record = records[0]
        assert record["action_type"] == "SLTP_MODIFY"
        assert record["trade_id"] == "pos_777"
        assert record["broker_result"]["ok"] is False
        assert record["broker_result"]["retcode"] == -1
        assert record["broker_result"]["fill_price"] is None
        assert str(record["broker_result"]["comment"]).startswith("modify_none:")

    def test_persistence_failure_cannot_affect_close_execution(self, tmpdir):
        """If the attempts writer raises, close_position still executes and
        returns its normal result (fire-and-forget observability)."""
        with patch(
            "core.persistence.execution_attempts_writer.persist_execution_attempt",
            side_effect=RuntimeError("writer exploded"),
        ):
            result = self._run_close(tmpdir, trade_id="pos_777", volume=None)

        assert result.ok is True
        assert result.retcode == 10009
        # And nothing was persisted.
        assert list(Path(tmpdir).rglob("*.jsonl")) == []

# --- TEST 17: Existing retry behaviour is unchanged ---------------------------

class TestRetryBehaviourUnchanged:
    @patch("execution.mt5_execution._time.sleep")
    def test_requote_still_retries_once(self, mock_sleep, intent):
        exec_engine = MT5Execution()
        result = _place_market_with_mocks(
            exec_engine, intent,
            [
                _mock_tick(),
                _mock_result(10004, comment="Requote"),
                _mock_tick(),
                _mock_result(10009, deal=1, order=123, comment="Done", price=1.08501),
            ],
            "/tmp/fake",
        )
        assert result.ok is True
        metrics = get_execution_metrics()
        assert metrics["total_retries"] == 1

    def test_hard_reject_no_retry(self, intent):
        exec_engine = MT5Execution()
        result = _place_market_with_mocks(
            exec_engine, intent,
            [_mock_tick(), _mock_result(10014, comment="Invalid stops")],
            "/tmp/fake",
        )
        assert result.ok is False


# --- TEST 17: Execution result persistence via orchestrator -------------------

class TestOrchestratorResultPersistence:
    """TEST 17: The ExecutionOrchestrator still persists exactly ONE final
    execution_result per execution action, while execution_attempts records
    the individual broker attempts (including retries).

    Verifies the dataset split contract:
      - execution_attempts  → one record per actual ``mt5.order_send()`` call
      - execution_results   → one record per orchestrator execute_trade() call
      - a broker retry does NOT duplicate the execution_result
    """

    @staticmethod
    def _read_all_records(tmpdir):
        files = sorted(Path(tmpdir).rglob("*.jsonl"))
        records = []
        for f in files:
            for line in f.read_text().strip().split("\n"):
                if line:
                    records.append(json.loads(line))
        return records

    @pytest.fixture
    def _intent(self):
        return OrderIntent(
            symbol="EURUSD",
            side=Side.BUY,
            volume=0.01,
            entry_reference=1.08500,
            sl=1.08400,
            tp=1.08700,
            pattern="TEST_PATTERN",
        )

    def _run_orchestrator(self, exec_engine, call_sequence, attempts_dir, results_dir, intent):
        """Drive ExecutionOrchestrator.execute_trade() with mocked MT5 and
        both persistence writers redirected to isolated temp dirs."""
        call_idx = [0]

        def _side_effect(fn, *args, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            return call_sequence[idx] if idx < len(call_sequence) else None

        with patch("execution.mt5_execution.mt5_call", side_effect=_side_effect), \
             patch("execution.mt5_execution.mt5.TRADE_RETCODE_DONE", 10009), \
             patch("execution.mt5_execution.mt5.ORDER_TYPE_BUY", 0), \
             patch("execution.mt5_execution._validate_order", return_value=(True, "")), \
             patch("execution.mt5_execution._filling_mode", return_value=1), \
             patch("execution.mt5_execution.check_spread") as mock_spread, \
             patch("execution.mt5_execution._is_duplicate_intent", return_value=False), \
             patch("execution.mt5_execution._time.sleep"), \
             patch("core.persistence.execution_attempts_writer._LOCAL_DIR", attempts_dir), \
             patch("core.persistence.execution_result_writer._LOCAL_DIR", results_dir):
            mock_spread.return_value = MagicMock(allowed=True)
            orchestrator = ExecutionOrchestrator(exec_engine, MagicMock())
            return orchestrator.execute_trade(
                intent=intent,
                symbol="EURUSD",
                cycle_id=42,
                decision_id="DEC-42",
                correlation_id="COR-42",
                entity_id="ENT-42",
                observation_id="OBS-42",
                canonical_opportunity_id="OPP-42",
                mt5_state="CONNECTED",
            )

    def test_retry_produces_two_attempts_but_one_execution_result(self, _intent):
        """A requote retry yields 2 execution_attempts but still exactly 1
        execution_result for the final action — no duplication."""
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as attempts_dir, \
             tempfile.TemporaryDirectory() as results_dir:
            outcome = self._run_orchestrator(
                exec_engine,
                [
                    _mock_tick(),                                                   # attempt 1 tick
                    _mock_result(10004, comment="Requote"),                         # attempt 1 → requote
                    _mock_tick(bid=1.08510, ask=1.08512),                           # retry tick
                    _mock_result(10009, deal=7, order=8, comment="Done", price=1.08511),  # attempt 2 → filled
                ],
                attempts_dir,
                results_dir,
                _intent,
            )
            attempts = self._read_all_records(attempts_dir)
            results = self._read_all_records(results_dir)

        assert outcome.executed is True
        assert outcome.ok is True

        # Per-attempt records: one per actual broker call
        assert len(attempts) == 2
        assert [a["attempt_number"] for a in attempts] == [1, 2]
        assert attempts[0]["retry_reason"] is None
        assert attempts[1]["retry_reason"] == "REQUOTE"
        assert attempts[0]["broker_result"]["retcode"] == 10004
        assert attempts[1]["broker_result"]["retcode"] == 10009
        assert all(a["action_type"] == "ENTRY" for a in attempts)

        # Final result: persisted ONCE for the orchestrator action
        assert len(results) == 1
        result = results[0]
        assert result["result_ok"] is True
        assert result["retcode"] == 10009
        assert result["deal"] == 7
        assert result["order_ticket"] == 8
        assert result["decision_id"] == "DEC-42"
        assert result["canonical_opportunity_id"] == "OPP-42"

    def test_single_attempt_produces_one_attempt_and_one_result(self, _intent):
        """No-retry success: 1 execution_attempt + exactly 1 execution_result."""
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as attempts_dir, \
             tempfile.TemporaryDirectory() as results_dir:
            outcome = self._run_orchestrator(
                exec_engine,
                [
                    _mock_tick(),
                    _mock_result(10009, deal=1, order=2, comment="Done", price=1.08501),
                ],
                attempts_dir,
                results_dir,
                _intent,
            )
            attempts = self._read_all_records(attempts_dir)
            results = self._read_all_records(results_dir)

        assert outcome.ok is True
        assert len(attempts) == 1
        assert attempts[0]["attempt_number"] == 1
        assert len(results) == 1
        assert results[0]["result_ok"] is True

    def test_rejected_broker_action_still_persists_one_result(self, _intent):
        """A hard broker rejection (no retry) persists 1 attempt + 1 failed
        execution_result — every orchestrator action yields a final result."""
        exec_engine = MT5Execution()
        with tempfile.TemporaryDirectory() as attempts_dir, \
             tempfile.TemporaryDirectory() as results_dir:
            outcome = self._run_orchestrator(
                exec_engine,
                [
                    _mock_tick(),
                    _mock_result(10014, comment="Invalid stops"),
                ],
                attempts_dir,
                results_dir,
                _intent,
            )
            attempts = self._read_all_records(attempts_dir)
            results = self._read_all_records(results_dir)

        assert outcome.executed is True
        assert outcome.ok is False
        assert len(attempts) == 1
        assert attempts[0]["broker_result"]["retcode"] == 10014
        assert len(results) == 1
        assert results[0]["result_ok"] is False
        assert results[0]["retcode"] == 10014
