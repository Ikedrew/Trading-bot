"""
Tests for decision ledger integrity — ensuring every EXECUTE path
produces a decision_ledger record.

Covers:
    1. Successful execution creates EXECUTE ledger entry
    2. Failed execution attempt (executed=False) creates NO_TRADE ledger entry
    3. Exception during execution creates NO_TRADE ledger entry (not orphan audit)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.runtime.decision_recorder import DecisionRecorder
from core.decision_ledger import DecisionOutcome


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _make_recorder() -> tuple[DecisionRecorder, MagicMock]:
    """Create a recorder with a mock ledger."""
    mock_ledger = MagicMock()
    recorder = DecisionRecorder(mock_ledger)
    return recorder, mock_ledger


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1: Successful execution creates EXECUTE ledger entry
# ═══════════════════════════════════════════════════════════════════════════════

class TestSuccessfulExecution:
    def test_execute_decision_writes_ledger(self):
        """EXECUTE decision that fills writes EXECUTE to ledger."""
        recorder, mock_ledger = _make_recorder()
        decision = recorder.init_cycle(
            symbol="GBPUSD", cycle_id=100, regime="TRENDING",
            context_snapshot_id="ctx_100", drawdown_pct=0.0, daily_loss_pct=0.0,
        )

        # Simulate EXECUTE path
        decision["decision"] = DecisionOutcome.EXECUTE
        decision["reason"] = "all_guards_passed"
        decision["signal_score"] = 6.0
        recorder.finalize(cycle_start=1784800000.0)

        assert mock_ledger.record.called
        call_kwargs = mock_ledger.record.call_args[1]
        assert call_kwargs["decision"] == DecisionOutcome.EXECUTE
        assert call_kwargs["reason"] == "all_guards_passed"

    def test_finalize_only_writes_once(self):
        """Calling finalize() twice does not duplicate ledger writes."""
        recorder, mock_ledger = _make_recorder()
        decision = recorder.init_cycle(
            symbol="EURUSD", cycle_id=200, regime="RANGE",
            context_snapshot_id="ctx_200", drawdown_pct=0.0, daily_loss_pct=0.0,
        )
        decision["decision"] = DecisionOutcome.EXECUTE
        decision["reason"] = "all_guards_passed"

        recorder.finalize(cycle_start=1784800000.0)
        recorder.finalize(cycle_start=1784800000.0)  # Second call

        assert mock_ledger.record.call_count == 1  # Only one write


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: Failed execution attempt creates NO_TRADE ledger entry
# ═══════════════════════════════════════════════════════════════════════════════

class TestFailedExecution:
    def test_execution_not_attempted_writes_no_trade(self):
        """exec_outcome.executed=False → ledger gets NO_TRADE with reason."""
        recorder, mock_ledger = _make_recorder()
        decision = recorder.init_cycle(
            symbol="USDCHF", cycle_id=300, regime="TRANSITIONAL",
            context_snapshot_id="ctx_300", drawdown_pct=0.0, daily_loss_pct=0.0,
        )

        # Simulate the fixed path: execution failed → finalize with failure reason
        decision["decision"] = DecisionOutcome.NO_TRADE
        decision["reason"] = "execution_not_attempted:MT5 connection lost"
        decision["risk_flag"] = "execution_failure"
        decision["signal_score"] = 5.0
        decision["pattern_state"] = "detected"
        recorder.finalize(cycle_start=1784800000.0)

        assert mock_ledger.record.called
        call_kwargs = mock_ledger.record.call_args[1]
        assert call_kwargs["decision"] == DecisionOutcome.NO_TRADE
        assert "execution_not_attempted" in call_kwargs["reason"]

    def test_broker_rejected_writes_no_trade(self):
        """result.ok=False → ledger gets NO_TRADE with broker_rejected reason."""
        recorder, mock_ledger = _make_recorder()
        decision = recorder.init_cycle(
            symbol="NZDUSD", cycle_id=400, regime="TRENDING",
            context_snapshot_id="ctx_400", drawdown_pct=0.0, daily_loss_pct=0.0,
        )

        decision["decision"] = DecisionOutcome.NO_TRADE
        decision["reason"] = "execution_failed:broker_rejected"
        decision["risk_flag"] = "execution_failure"
        recorder.finalize(cycle_start=1784800000.0)

        assert mock_ledger.record.called
        assert "broker_rejected" in mock_ledger.record.call_args[1]["reason"]


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: Exception during execution creates NO_TRADE (no orphan)
# ═══════════════════════════════════════════════════════════════════════════════

class TestExceptionDuringExecution:
    def test_exception_path_finalizes_decision(self):
        """Exception in execute path → ledger written with exception reason."""
        recorder, mock_ledger = _make_recorder()
        decision = recorder.init_cycle(
            symbol="AUDUSD", cycle_id=500, regime="TRENDING",
            context_snapshot_id="ctx_500", drawdown_pct=0.0, daily_loss_pct=0.0,
        )

        # Simulate the fixed exception path
        assert not recorder.is_written  # Not yet written

        decision["decision"] = DecisionOutcome.NO_TRADE
        decision["reason"] = "exception_in_execute_path:RuntimeError:connection timeout"
        decision["risk_flag"] = "execution_exception"
        recorder.finalize(cycle_start=1784800000.0)

        assert recorder.is_written
        assert mock_ledger.record.called
        assert "exception_in_execute_path" in mock_ledger.record.call_args[1]["reason"]

    def test_is_written_prevents_double_write(self):
        """If decision already finalized, exception handler doesn't double-write."""
        recorder, mock_ledger = _make_recorder()
        decision = recorder.init_cycle(
            symbol="USDJPY", cycle_id=600, regime="RANGE",
            context_snapshot_id="ctx_600", drawdown_pct=0.0, daily_loss_pct=0.0,
        )

        # First finalization (normal path)
        decision["decision"] = DecisionOutcome.EXECUTE
        decision["reason"] = "all_guards_passed"
        recorder.finalize(cycle_start=1784800000.0)

        assert recorder.is_written  # Already written

        # Exception handler checks is_written before attempting second write
        # This simulates: if not _decision_recorder.is_written: ...
        if not recorder.is_written:
            decision["decision"] = DecisionOutcome.NO_TRADE
            decision["reason"] = "exception_after_fill"
            recorder.finalize(cycle_start=1784800000.0)

        # Should still only be one write
        assert mock_ledger.record.call_count == 1
