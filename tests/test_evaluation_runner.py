"""
Unit tests for evaluation_runner — consolidated evaluation boundary.

Tests:
    - Disabled evaluation returns immediately
    - NO_TRADE action triggers shadow_no_trade
    - EXECUTE action triggers legacy_shadow_runner
    - Never raises
    - Returns EvaluationResult
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.evaluation.evaluation_runner import evaluate, EvaluationContext, EvaluationResult


def _make_ctx(action="EXECUTE", shadow_enabled=True):
    cfg = MagicMock()
    cfg.ENABLE_LEGACY_SHADOW_PIPELINE = shadow_enabled
    cfg.MTF_SHADOW_MODE = False
    return EvaluationContext(
        cycle_id=1, symbol="EURUSD", closed_time=1700000000,
        candles=[MagicMock()], closed_i=0, bid=1.1, ask=1.1002,
        config=cfg, risk=MagicMock(), engine_state=MagicMock(),
        htf_context=None, new_engine_result={"action": action},
        new_engine_score=5.5, new_engine_action=action,
    )


class TestDisabledEvaluation:
    def test_returns_immediately_when_disabled(self):
        ctx = _make_ctx(shadow_enabled=False)
        result = evaluate(ctx)
        assert isinstance(result, EvaluationResult)
        assert result.ran is False
        assert result.legacy_unified is None


class TestExecuteAction:
    @patch("core.evaluation.legacy_shadow_runner.run_legacy_shadow", return_value=MagicMock())
    @patch("core.pipeline.shadow_pipeline.run_shadow_execute_comparison")
    def test_execute_calls_legacy_shadow(self, mock_compare, mock_legacy):
        ctx = _make_ctx(action="EXECUTE")
        result = evaluate(ctx)
        assert result.ran is True
        mock_legacy.assert_called_once()
        mock_compare.assert_called_once()


class TestNoTradeAction:
    @patch("core.pipeline.shadow_pipeline.run_shadow_no_trade")
    def test_no_trade_calls_shadow_no_trade(self, mock_shadow):
        ctx = _make_ctx(action="NO_TRADE")
        result = evaluate(ctx)
        assert result.ran is True
        mock_shadow.assert_called_once()


class TestNeverRaises:
    @patch("core.evaluation.legacy_shadow_runner.run_legacy_shadow", side_effect=RuntimeError("crash"))
    def test_execute_path_crash_swallowed(self, mock_legacy):
        ctx = _make_ctx(action="EXECUTE")
        result = evaluate(ctx)
        assert result.ran is True
        assert result.legacy_unified is None
