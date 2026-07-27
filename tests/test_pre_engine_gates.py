"""
Unit tests for pre_engine_gates — pre-engine permission evaluation.

Tests:
    - Kill switch blocks first
    - Daily loss blocks second
    - Session guard blocks third
    - Pattern reject blocks fourth
    - All gates pass returns patterns
    - Gate ordering preserved (short-circuit)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.runtime.pre_engine_gates import evaluate_pre_engine_gates, GateResult


# ─── FIXTURES ─────────────────────────────────────────────────────────────────


def _make_candles():
    c1 = MagicMock()
    c1.time = 1700000000
    c1.high = 1.12
    c1.low = 1.09
    c1.close = 1.11
    c2 = MagicMock()
    c2.time = 1700000300
    c2.high = 1.13
    c2.low = 1.10
    c2.close = 1.12
    return [c1, c2]


def _make_pattern(name="engulfing"):
    p = MagicMock()
    p.pattern = name
    return p


# ─── TESTS ────────────────────────────────────────────────────────────────────


class TestKillSwitchGate:
    """Kill switch blocks first, before other gates."""

    def test_kill_switch_blocks(self):
        result = evaluate_pre_engine_gates(
            kill_active=True,
            daily_loss_blocked=False,
            candles=_make_candles(),
            closed_i=1,
            symbol="EURUSD",
            cycle_id=1,
            closed_time=1700000300,
        )
        assert result.allowed is False
        assert result.block_outcome == "KILL_SWITCH"
        assert result.block_reason == "kill_switch_active"

    @patch("risk.session_guard.check_session")
    def test_kill_switch_short_circuits_session(self, mock_session):
        """Session guard is NOT called when kill switch is active."""
        evaluate_pre_engine_gates(
            kill_active=True,
            daily_loss_blocked=False,
            candles=_make_candles(),
            closed_i=1,
            symbol="EURUSD",
            cycle_id=1,
            closed_time=1700000300,
        )
        mock_session.assert_not_called()


class TestDailyLossGate:
    """Daily loss blocks second."""

    def test_daily_loss_blocks(self):
        result = evaluate_pre_engine_gates(
            kill_active=False,
            daily_loss_blocked=True,
            candles=_make_candles(),
            closed_i=1,
            symbol="EURUSD",
            cycle_id=1,
            closed_time=1700000300,
        )
        assert result.allowed is False
        assert result.block_outcome == "DAILY_LOSS_BLOCK"
        assert result.block_risk_flag == "daily_loss"


class TestSessionGuard:
    """Session guard blocks third."""

    @patch("strategy.signal_orchestrator.evaluate_closed_bar")
    @patch("risk.session_guard.check_session")
    def test_session_blocks(self, mock_session, mock_patterns):
        mock_session.return_value = MagicMock(allowed=False, reason="market_closed")

        result = evaluate_pre_engine_gates(
            kill_active=False,
            daily_loss_blocked=False,
            candles=_make_candles(),
            closed_i=1,
            symbol="EURUSD",
            cycle_id=1,
            closed_time=1700000300,
        )
        assert result.allowed is False
        assert result.block_outcome == "SESSION_BLOCK"
        assert result.block_reason == "market_closed"
        # Pattern detection should NOT have been called
        mock_patterns.assert_not_called()


class TestPatternGate:
    """Pattern gate blocks fourth (no patterns detected)."""

    @patch("strategy.signal_orchestrator.evaluate_closed_bar")
    @patch("risk.session_guard.check_session")
    def test_no_patterns_blocks(self, mock_session, mock_patterns):
        mock_session.return_value = MagicMock(allowed=True)
        mock_patterns.return_value = []  # No patterns

        result = evaluate_pre_engine_gates(
            kill_active=False,
            daily_loss_blocked=False,
            candles=_make_candles(),
            closed_i=1,
            symbol="EURUSD",
            cycle_id=1,
            closed_time=1700000300,
        )
        assert result.allowed is False
        assert result.block_outcome == "PATTERN_REJECT"
        assert result.block_reason == "no_patterns_detected"


class TestAllGatesPass:
    """All gates pass — returns patterns."""

    @patch("strategy.signal_orchestrator.evaluate_closed_bar")
    @patch("risk.session_guard.check_session")
    def test_all_pass_returns_patterns(self, mock_session, mock_patterns):
        mock_session.return_value = MagicMock(allowed=True)
        patterns = [_make_pattern("engulfing"), _make_pattern("pin_bar")]
        mock_patterns.return_value = patterns

        result = evaluate_pre_engine_gates(
            kill_active=False,
            daily_loss_blocked=False,
            candles=_make_candles(),
            closed_i=1,
            symbol="EURUSD",
            cycle_id=1,
            closed_time=1700000300,
        )
        assert result.allowed is True
        assert result.raw_patterns is patterns
        assert len(result.raw_patterns) == 2
        assert result.block_outcome == ""

    @patch("strategy.signal_orchestrator.evaluate_closed_bar")
    @patch("risk.session_guard.check_session")
    def test_gate_result_is_dataclass(self, mock_session, mock_patterns):
        mock_session.return_value = MagicMock(allowed=True)
        mock_patterns.return_value = [_make_pattern()]

        result = evaluate_pre_engine_gates(
            kill_active=False,
            daily_loss_blocked=False,
            candles=_make_candles(),
            closed_i=1,
            symbol="EURUSD",
            cycle_id=1,
            closed_time=1700000300,
        )
        assert isinstance(result, GateResult)
