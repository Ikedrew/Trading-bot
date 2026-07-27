"""
Unit tests for DecisionRecorder — decision lifecycle management.

Tests:
    - init_cycle creates fresh state
    - mutate sets fields correctly
    - finalize writes to ledger exactly once (idempotent)
    - invariant enforcement (None decision → forced NO_TRADE)
    - invariant enforcement (empty reason → forced reason_not_set)
    - ledger write error does not raise
    - decision dict accessible via property
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.runtime.decision_recorder import DecisionRecorder
from core.decision_ledger import DecisionOutcome


# ─── TESTS ────────────────────────────────────────────────────────────────────


class TestInitCycle:
    """init_cycle creates fresh decision state."""

    def test_creates_dict_with_defaults(self):
        ledger = MagicMock()
        recorder = DecisionRecorder(ledger)

        d = recorder.init_cycle(
            symbol="EURUSD",
            cycle_id=5,
            regime="TRENDING",
            context_snapshot_id="ctx123",
            drawdown_pct=2.5,
            daily_loss_pct=1.0,
        )

        assert d["symbol"] == "EURUSD"
        assert d["cycle_id"] == 5
        assert d["decision"] is None
        assert d["reason"] == ""
        assert d["signal_score"] == 0.0
        assert d["regime"] == "TRENDING"
        assert d["drawdown_pct"] == 2.5
        assert d["daily_loss_pct"] == 1.0

    def test_returns_same_dict_as_property(self):
        ledger = MagicMock()
        recorder = DecisionRecorder(ledger)

        d = recorder.init_cycle(
            symbol="EURUSD", cycle_id=1, regime="unknown",
            context_snapshot_id="", drawdown_pct=0, daily_loss_pct=0,
        )

        assert recorder.decision is d


class TestMutate:
    """mutate sets fields on current decision."""

    def test_mutate_sets_fields(self):
        ledger = MagicMock()
        recorder = DecisionRecorder(ledger)
        recorder.init_cycle(
            symbol="EURUSD", cycle_id=1, regime="unknown",
            context_snapshot_id="", drawdown_pct=0, daily_loss_pct=0,
        )

        recorder.mutate(decision=DecisionOutcome.NO_TRADE, reason="test_reason")

        assert recorder.decision["decision"] == DecisionOutcome.NO_TRADE
        assert recorder.decision["reason"] == "test_reason"

    def test_mutate_multiple_fields(self):
        ledger = MagicMock()
        recorder = DecisionRecorder(ledger)
        recorder.init_cycle(
            symbol="EURUSD", cycle_id=1, regime="unknown",
            context_snapshot_id="", drawdown_pct=0, daily_loss_pct=0,
        )

        recorder.mutate(
            decision=DecisionOutcome.EXECUTE,
            reason="all_guards_passed",
            signal_score=6.5,
            pattern_state="detected",
        )

        assert recorder.decision["decision"] == DecisionOutcome.EXECUTE
        assert recorder.decision["signal_score"] == 6.5


class TestFinalize:
    """finalize writes to ledger exactly once."""

    def test_writes_to_ledger(self):
        ledger = MagicMock()
        recorder = DecisionRecorder(ledger)
        recorder.init_cycle(
            symbol="EURUSD", cycle_id=3, regime="TRENDING",
            context_snapshot_id="ctx1", drawdown_pct=1.0, daily_loss_pct=0.5,
        )
        recorder.mutate(decision=DecisionOutcome.NO_TRADE, reason="no_signal")

        recorder.finalize(cycle_start=time.time() - 0.1)

        ledger.record.assert_called_once()
        kwargs = ledger.record.call_args[1]
        assert kwargs["symbol"] == "EURUSD"
        assert kwargs["cycle_id"] == 3
        assert kwargs["decision"] == DecisionOutcome.NO_TRADE
        assert kwargs["reason"] == "no_signal"

    def test_idempotent_only_writes_once(self):
        ledger = MagicMock()
        recorder = DecisionRecorder(ledger)
        recorder.init_cycle(
            symbol="EURUSD", cycle_id=1, regime="unknown",
            context_snapshot_id="", drawdown_pct=0, daily_loss_pct=0,
        )
        recorder.mutate(decision=DecisionOutcome.NO_TRADE, reason="x")

        recorder.finalize(cycle_start=time.time())
        recorder.finalize(cycle_start=time.time())
        recorder.finalize(cycle_start=time.time())

        assert ledger.record.call_count == 1

    def test_is_written_property(self):
        ledger = MagicMock()
        recorder = DecisionRecorder(ledger)
        recorder.init_cycle(
            symbol="EURUSD", cycle_id=1, regime="unknown",
            context_snapshot_id="", drawdown_pct=0, daily_loss_pct=0,
        )
        recorder.mutate(decision=DecisionOutcome.NO_TRADE, reason="x")

        assert recorder.is_written is False
        recorder.finalize(cycle_start=time.time())
        assert recorder.is_written is True


class TestInvariantEnforcement:
    """Invariants are enforced at finalization."""

    def test_none_decision_forced_to_no_trade(self):
        ledger = MagicMock()
        recorder = DecisionRecorder(ledger)
        recorder.init_cycle(
            symbol="EURUSD", cycle_id=1, regime="unknown",
            context_snapshot_id="", drawdown_pct=0, daily_loss_pct=0,
        )
        # Don't set decision — leave as None

        recorder.finalize(cycle_start=time.time())

        kwargs = ledger.record.call_args[1]
        assert kwargs["decision"] == DecisionOutcome.NO_TRADE
        assert "INVARIANT_VIOLATION" in kwargs["reason"]

    def test_empty_reason_forced(self):
        ledger = MagicMock()
        recorder = DecisionRecorder(ledger)
        recorder.init_cycle(
            symbol="EURUSD", cycle_id=1, regime="unknown",
            context_snapshot_id="", drawdown_pct=0, daily_loss_pct=0,
        )
        recorder.mutate(decision=DecisionOutcome.NO_TRADE)
        # reason left as ""

        recorder.finalize(cycle_start=time.time())

        kwargs = ledger.record.call_args[1]
        assert kwargs["reason"] == "reason_not_set"


class TestErrorHandling:
    """Ledger write errors don't propagate."""

    def test_ledger_error_does_not_raise(self):
        ledger = MagicMock()
        ledger.record.side_effect = RuntimeError("DB down")
        recorder = DecisionRecorder(ledger)
        recorder.init_cycle(
            symbol="EURUSD", cycle_id=1, regime="unknown",
            context_snapshot_id="", drawdown_pct=0, daily_loss_pct=0,
        )
        recorder.mutate(decision=DecisionOutcome.NO_TRADE, reason="test")

        # Should not raise
        recorder.finalize(cycle_start=time.time())

    def test_new_cycle_after_error(self):
        """Can start a new cycle after a failed finalization."""
        ledger = MagicMock()
        ledger.record.side_effect = [RuntimeError("fail"), None]
        recorder = DecisionRecorder(ledger)

        # First cycle — fails
        recorder.init_cycle(
            symbol="EURUSD", cycle_id=1, regime="unknown",
            context_snapshot_id="", drawdown_pct=0, daily_loss_pct=0,
        )
        recorder.mutate(decision=DecisionOutcome.NO_TRADE, reason="x")
        recorder.finalize(cycle_start=time.time())

        # Second cycle — succeeds
        recorder.init_cycle(
            symbol="EURUSD", cycle_id=2, regime="unknown",
            context_snapshot_id="", drawdown_pct=0, daily_loss_pct=0,
        )
        recorder.mutate(decision=DecisionOutcome.EXECUTE, reason="all_guards_passed")
        recorder.finalize(cycle_start=time.time())

        assert ledger.record.call_count == 2
