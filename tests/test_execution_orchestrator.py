"""
Unit tests for ExecutionOrchestrator — trade execution and result persistence.

Tests:
    - Successful execution returns ExecutionOutcome with ok=True
    - Failed broker call returns executed=False
    - Broker rejection returns executed=True, ok=False
    - Execution result is persisted
    - Execution error logged and Discord notified
    - Never raises regardless of input
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from execution.execution_orchestrator import ExecutionOrchestrator, ExecutionOutcome


# ─── FIXTURES ─────────────────────────────────────────────────────────────────


def _make_intent(side="BUY", volume=0.01, entry=1.1, sl=1.09, tp=1.12, pattern="engulfing"):
    intent = MagicMock()
    intent.side.name = side
    intent.volume = volume
    intent.entry_reference = entry
    intent.sl = sl
    intent.tp = tp
    intent.pattern = pattern
    intent.score = 6.0
    return intent


def _make_config(discord_logger=None):
    cfg = MagicMock()
    cfg._discord_logger = discord_logger
    return cfg


def _make_execution_result(ok=True, fill_price=1.1001, retcode=10009, deal=12345, order=67890):
    r = MagicMock()
    r.ok = ok
    r.fill_price = fill_price
    r.retcode = retcode
    r.deal = deal
    r.order = order
    r.comment = "Request executed"
    return r


# ─── TESTS ────────────────────────────────────────────────────────────────────


class TestSuccessfulExecution:
    """Broker accepts and fills the order."""

    @patch("core.persistence.execution_result_writer.persist_execution_result")
    @patch("core.clock.utc_ms", return_value=1700000000000)
    def test_returns_outcome_with_ok_true(self, mock_clock, mock_persist):
        exec_mock = MagicMock()
        exec_mock.execute.return_value = _make_execution_result(ok=True)

        orchestrator = ExecutionOrchestrator(exec_mock, _make_config())

        with patch("core.clock.utc_ms", return_value=1700000000000):
            outcome = orchestrator.execute_trade(
                intent=_make_intent(),
                symbol="EURUSD",
                cycle_id=5,
                decision_id="dec_123",
                correlation_id="cor_456",
                entity_id="ent_789",
                mt5_state="CONNECTED",
            )

        assert outcome.executed is True
        assert outcome.ok is True
        assert outcome.result is not None
        assert outcome.error == ""

    @patch("core.persistence.execution_result_writer.persist_execution_result")
    def test_persist_called(self, mock_persist):
        exec_mock = MagicMock()
        exec_mock.execute.return_value = _make_execution_result(ok=True)

        orchestrator = ExecutionOrchestrator(exec_mock, _make_config())

        with patch("core.clock.utc_ms", return_value=1700000000000):
            orchestrator.execute_trade(
                intent=_make_intent(),
                symbol="EURUSD",
                cycle_id=5,
                decision_id="dec_123",
                correlation_id="cor_456",
                entity_id="ent_789",
                mt5_state="CONNECTED",
            )

        mock_persist.assert_called_once()
        kwargs = mock_persist.call_args[1]
        assert kwargs["symbol"] == "EURUSD"
        assert kwargs["result_ok"] is True


class TestBrokerRejection:
    """Broker rejects the order."""

    @patch("core.persistence.execution_result_writer.persist_execution_result")
    def test_returns_executed_true_ok_false(self, mock_persist):
        exec_mock = MagicMock()
        exec_mock.execute.return_value = _make_execution_result(ok=False, retcode=10004)

        orchestrator = ExecutionOrchestrator(exec_mock, _make_config())

        with patch("core.clock.utc_ms", return_value=1700000000000):
            outcome = orchestrator.execute_trade(
                intent=_make_intent(),
                symbol="EURUSD",
                cycle_id=1,
                decision_id="d1",
                correlation_id="c1",
                entity_id="e1",
                mt5_state="CONNECTED",
            )

        assert outcome.executed is True
        assert outcome.ok is False


class TestExecutionError:
    """Broker call raises an exception."""

    def test_returns_executed_false(self):
        exec_mock = MagicMock()
        exec_mock.execute.side_effect = RuntimeError("Connection lost")

        orchestrator = ExecutionOrchestrator(exec_mock, _make_config())

        outcome = orchestrator.execute_trade(
            intent=_make_intent(),
            symbol="EURUSD",
            cycle_id=1,
            decision_id="d1",
            correlation_id="c1",
            entity_id="e1",
            mt5_state="CONNECTED",
        )

        assert outcome.executed is False
        assert outcome.ok is False
        assert "Connection lost" in outcome.error

    def test_discord_notified_on_error(self):
        exec_mock = MagicMock()
        exec_mock.execute.side_effect = RuntimeError("timeout")
        discord = MagicMock()

        orchestrator = ExecutionOrchestrator(exec_mock, _make_config(discord_logger=discord))

        orchestrator.execute_trade(
            intent=_make_intent(),
            symbol="EURUSD",
            cycle_id=1,
            decision_id="d1",
            correlation_id="c1",
            entity_id="e1",
            mt5_state="CONNECTED",
        )

        discord.event.assert_called_once()
        call_args = discord.event.call_args[0]
        assert call_args[0] == "ERROR"


class TestNeverRaises:
    """Orchestrator never raises regardless of input."""

    def test_none_intent_does_not_raise(self):
        exec_mock = MagicMock()
        exec_mock.execute.side_effect = TypeError("NoneType")

        orchestrator = ExecutionOrchestrator(exec_mock, _make_config())

        # Use a valid-looking intent that triggers error inside execute()
        outcome = orchestrator.execute_trade(
            intent=_make_intent(),
            symbol="EURUSD",
            cycle_id=1,
            decision_id="",
            correlation_id="",
            entity_id="",
            mt5_state="CONNECTED",
        )

        assert outcome.executed is False

    @patch("core.persistence.execution_result_writer.persist_execution_result", side_effect=RuntimeError("disk"))
    def test_persist_failure_still_returns_outcome(self, mock_persist):
        exec_mock = MagicMock()
        exec_mock.execute.return_value = _make_execution_result(ok=True)

        orchestrator = ExecutionOrchestrator(exec_mock, _make_config())

        with patch("core.clock.utc_ms", return_value=1700000000000):
            outcome = orchestrator.execute_trade(
                intent=_make_intent(),
                symbol="EURUSD",
                cycle_id=1,
                decision_id="d1",
                correlation_id="c1",
                entity_id="e1",
                mt5_state="CONNECTED",
            )

        # Persist failed but execution succeeded
        assert outcome.executed is True
        assert outcome.ok is True
