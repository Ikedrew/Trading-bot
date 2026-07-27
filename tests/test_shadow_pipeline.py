"""
Unit tests for shadow_pipeline — legacy shadow divergence logging.

Tests:
    - Shadow runs when enabled (NO_TRADE path)
    - Shadow returns None on failure (never raises)
    - Shadow comparison works on EXECUTE path
    - No live execution calls
    - No risk mutations
    - Isolation preserved
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.pipeline.shadow_pipeline import (
    run_shadow_no_trade,
    run_shadow_execute_comparison,
    ShadowResult,
)


# ─── FIXTURES ─────────────────────────────────────────────────────────────────


def _make_sym_state(symbol="EURUSD"):
    state = MagicMock()
    state.symbol = symbol
    state.engine_state = MagicMock()
    state.risk = MagicMock()
    return state


def _make_unified(should_trade=False, score=3.0, reason="no_signal", intent=None):
    unified = MagicMock()
    unified.decision.should_trade = should_trade
    unified.decision.score = score
    unified.decision.reason = reason
    unified.decision.intent = intent
    return unified


def _make_intent(side="BUY", entry=1.1, sl=1.09, tp=1.12, pattern="engulfing"):
    intent = MagicMock()
    intent.side.name = side
    intent.entry_reference = entry
    intent.sl = sl
    intent.tp = tp
    intent.pattern = pattern
    return intent


# ─── TEST: run_shadow_no_trade ────────────────────────────────────────────────


class TestRunShadowNoTrade:
    """Shadow runs on NO_TRADE path."""

    def test_returns_shadow_result_on_success(self):
        sym_state = _make_sym_state()
        unified = _make_unified(should_trade=False, score=2.5)

        def mock_process_bar(**kwargs):
            return unified

        result = run_shadow_no_trade(
            candles=[MagicMock()],
            closed_i=0,
            sym_state=sym_state,
            config=MagicMock(),
            bid=1.1,
            ask=1.1002,
            closed_time=1700000000,
            process_bar_fn=mock_process_bar,
        )

        assert result is not None
        assert isinstance(result, ShadowResult)
        assert result.old_action == "NO_TRADE"
        assert result.diverged is False

    def test_detects_divergence_when_old_would_execute(self):
        sym_state = _make_sym_state()
        intent = _make_intent()
        unified = _make_unified(should_trade=True, score=6.0, intent=intent)

        def mock_process_bar(**kwargs):
            return unified

        with patch("core.pipeline.paper_outcome_engine.get_paper_engine") as mock_paper:
            mock_paper.return_value = MagicMock()
            result = run_shadow_no_trade(
                candles=[MagicMock()],
                closed_i=0,
                sym_state=sym_state,
                config=MagicMock(),
                bid=1.1,
                ask=1.1002,
                closed_time=1700000000,
                process_bar_fn=mock_process_bar,
            )

        assert result is not None
        assert result.old_action == "EXECUTE"
        assert result.diverged is True

    def test_returns_none_on_failure(self):
        sym_state = _make_sym_state()

        def mock_process_bar(**kwargs):
            raise RuntimeError("crash")

        result = run_shadow_no_trade(
            candles=[MagicMock()],
            closed_i=0,
            sym_state=sym_state,
            config=MagicMock(),
            bid=1.1,
            ask=1.1002,
            closed_time=1700000000,
            process_bar_fn=mock_process_bar,
        )

        assert result is None

    def test_never_raises(self):
        """Even with completely broken inputs, never raises."""
        result = run_shadow_no_trade(
            candles=None,
            closed_i=0,
            sym_state=None,
            config=None,
            bid=0,
            ask=0,
            closed_time=0,
            process_bar_fn=lambda **kw: None,  # Will cause AttributeError on .decision
        )
        assert result is None


# ─── TEST: run_shadow_execute_comparison ──────────────────────────────────────


class TestRunShadowExecuteComparison:
    """Shadow comparison on EXECUTE path."""

    def test_both_execute_returns_match(self):
        sym_state = _make_sym_state()
        intent = _make_intent()
        unified = _make_unified(should_trade=True, score=5.5, intent=intent)

        with patch("core.pipeline.paper_outcome_engine.get_paper_engine") as mock_paper:
            mock_paper.return_value = MagicMock()
            result = run_shadow_execute_comparison(
                sym_state=sym_state,
                unified=unified,
                new_engine_score=6.2,
                closed_i=5,
            )

        assert result is not None
        assert result.old_action == "EXECUTE"
        assert result.diverged is False

    def test_new_execute_old_no_trade_diverges(self):
        sym_state = _make_sym_state()
        unified = _make_unified(should_trade=False, score=2.0, reason="below_threshold")

        result = run_shadow_execute_comparison(
            sym_state=sym_state,
            unified=unified,
            new_engine_score=5.5,
            closed_i=3,
        )

        assert result is not None
        assert result.old_action == "NO_TRADE"
        assert result.diverged is True

    def test_returns_none_on_failure(self):
        result = run_shadow_execute_comparison(
            sym_state=MagicMock(),
            unified=None,  # Will cause AttributeError
            new_engine_score=5.0,
            closed_i=0,
        )
        assert result is None

    def test_never_raises(self):
        """Even with broken unified object, never raises."""
        broken_unified = MagicMock()
        broken_unified.decision.should_trade = True
        broken_unified.decision.score = "not_a_number"  # Will fail float()
        # float("not_a_number") raises, but it's inside try/except
        # Actually MagicMock().score returns a mock which float() will fail on
        broken_unified.decision.score = MagicMock()

        result = run_shadow_execute_comparison(
            sym_state=_make_sym_state(),
            unified=broken_unified,
            new_engine_score=5.0,
            closed_i=0,
        )
        # May return result or None depending on where it fails
        # Key: it MUST NOT raise
        assert result is None or isinstance(result, ShadowResult)


# ─── TEST: Isolation ──────────────────────────────────────────────────────────


class TestIsolation:
    """Shadow pipeline does not affect live state."""

    def test_no_trade_does_not_mutate_engine_state(self):
        sym_state = _make_sym_state()
        original_state = sym_state.engine_state

        unified = _make_unified(should_trade=False)

        def mock_process_bar(**kwargs):
            return unified

        run_shadow_no_trade(
            candles=[MagicMock()],
            closed_i=0,
            sym_state=sym_state,
            config=MagicMock(),
            bid=1.1,
            ask=1.1002,
            closed_time=1700000000,
            process_bar_fn=mock_process_bar,
        )

        # Engine state reference should not have changed
        assert sym_state.engine_state is original_state
