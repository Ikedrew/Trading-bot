"""
Unit tests for runtime_guard_chain — post-engine guard orchestration.

Tests:
    - All guards pass
    - Daily trade limit blocks first
    - Trade cooldown blocks second
    - Correlation guard blocks third
    - Guard ordering preserved (short-circuit)
    - GuardChainResult contains correct metadata
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from risk.runtime_guard_chain import evaluate_runtime_guards, GuardChainResult


# ─── FIXTURES ─────────────────────────────────────────────────────────────────


def _make_intent(side="BUY", volume=0.01):
    intent = MagicMock()
    intent.side.name = side
    intent.volume = volume
    return intent


def _make_config():
    cfg = MagicMock()
    cfg.RISK_PER_TRADE_PERCENT = 1.0
    return cfg


def _make_engine_state():
    es = MagicMock()
    es.regime_state = "TRENDING"
    return es


def _all_guards_pass():
    """Return mocks where all guards pass."""
    daily_trade_limit = MagicMock()
    daily_trade_limit.can_open_trade.return_value = MagicMock(allowed=True)

    trade_cooldown = MagicMock()
    trade_cooldown.can_open_trade.return_value = True

    return daily_trade_limit, trade_cooldown


# ─── TESTS ────────────────────────────────────────────────────────────────────


class TestAllGuardsPass:
    """All guards pass — allowed=True."""

    @patch("risk.runtime_guard_chain.check_weekend_gate")
    @patch("risk.runtime_guard_chain.check_prop_firm_gate")
    @patch("risk.runtime_guard_chain.check_consistency_gate")
    @patch("risk.runtime_guard_chain.check_challenge_gate")
    @patch("risk.runtime_guard_chain.check_regime")
    @patch("risk.runtime_guard_chain.check_portfolio_exposure")
    @patch("risk.runtime_guard_chain.check_correlation")
    def test_all_pass(self, mock_corr, mock_exposure, mock_regime,
                      mock_challenge, mock_consistency, mock_prop, mock_weekend):
        mock_corr.return_value = MagicMock(allowed=True)
        mock_exposure.return_value = MagicMock(allowed=True)
        mock_regime.return_value = MagicMock(allowed=True)
        mock_challenge.return_value = MagicMock(allowed=True)
        mock_consistency.return_value = MagicMock(allowed=True)
        mock_prop.return_value = MagicMock(allowed=True)
        mock_weekend.return_value = MagicMock(allowed=True)

        dtl, cooldown = _all_guards_pass()

        with patch("core.pipeline.control_layer.control_gate", return_value=(True, "")):
            result = evaluate_runtime_guards(
                symbol="EURUSD",
                intent=_make_intent(),
                daily_trade_limit=dtl,
                trade_cooldown=cooldown,
                all_open_positions=[],
                candles=[MagicMock()],
                closed_i=0,
                htf_context=None,
                engine_state=_make_engine_state(),
                config=_make_config(),
            )

        assert result.allowed is True
        assert result.guard_name == ""


class TestDailyTradeLimitBlocks:
    """Daily trade limit blocks first."""

    def test_dtl_blocks(self):
        dtl = MagicMock()
        dtl.can_open_trade.return_value = MagicMock(allowed=False, reason="limit_reached")
        cooldown = MagicMock()

        result = evaluate_runtime_guards(
            symbol="EURUSD",
            intent=_make_intent(),
            daily_trade_limit=dtl,
            trade_cooldown=cooldown,
            all_open_positions=[],
            candles=[MagicMock()],
            closed_i=0,
            htf_context=None,
            engine_state=_make_engine_state(),
            config=_make_config(),
        )

        assert result.allowed is False
        assert result.guard_name == "daily_trade_limit"
        assert result.rejection_code == "A4_daily_trade_limit"
        assert result.filter_key == "daily_trade_limit"
        # Cooldown should not have been checked
        cooldown.can_open_trade.assert_not_called()


class TestTradeCooldownBlocks:
    """Trade cooldown blocks second."""

    def test_cooldown_blocks(self):
        dtl = MagicMock()
        dtl.can_open_trade.return_value = MagicMock(allowed=True)

        cooldown = MagicMock()
        cooldown.can_open_trade.return_value = False
        cooldown.get_remaining_cooldown.return_value = 45.0

        result = evaluate_runtime_guards(
            symbol="EURUSD",
            intent=_make_intent(),
            daily_trade_limit=dtl,
            trade_cooldown=cooldown,
            all_open_positions=[],
            candles=[MagicMock()],
            closed_i=0,
            htf_context=None,
            engine_state=_make_engine_state(),
            config=_make_config(),
        )

        assert result.allowed is False
        assert result.guard_name == "trade_cooldown"
        assert result.rejection_code == "B1_trade_cooldown"
        assert result.metadata["remaining_s"] == 45.0


class TestCorrelationGuardBlocks:
    """Correlation guard blocks third."""

    @patch("risk.runtime_guard_chain.check_correlation")
    def test_correlation_blocks(self, mock_corr):
        mock_corr.return_value = MagicMock(allowed=False, reason="correlated_GBPUSD")
        dtl, cooldown = _all_guards_pass()

        result = evaluate_runtime_guards(
            symbol="EURUSD",
            intent=_make_intent(),
            daily_trade_limit=dtl,
            trade_cooldown=cooldown,
            all_open_positions=[],
            candles=[MagicMock()],
            closed_i=0,
            htf_context=None,
            engine_state=_make_engine_state(),
            config=_make_config(),
        )

        assert result.allowed is False
        assert result.guard_name == "correlation_guard"
        assert result.rejection_code == "A3_correlation_guard"
        assert result.filter_key == "correlation"


class TestGuardOrdering:
    """Guards are evaluated in correct order — first failure short-circuits."""

    @patch("risk.runtime_guard_chain.check_correlation")
    def test_dtl_before_cooldown_before_correlation(self, mock_corr):
        """If DTL passes and cooldown passes, correlation is checked."""
        mock_corr.return_value = MagicMock(allowed=False, reason="blocked")
        dtl, cooldown = _all_guards_pass()

        result = evaluate_runtime_guards(
            symbol="EURUSD",
            intent=_make_intent(),
            daily_trade_limit=dtl,
            trade_cooldown=cooldown,
            all_open_positions=[],
            candles=[MagicMock()],
            closed_i=0,
            htf_context=None,
            engine_state=_make_engine_state(),
            config=_make_config(),
        )

        # DTL was checked
        dtl.can_open_trade.assert_called_once()
        # Cooldown was checked
        cooldown.can_open_trade.assert_called_once()
        # Correlation was checked and blocked
        mock_corr.assert_called_once()
        assert result.guard_name == "correlation_guard"
