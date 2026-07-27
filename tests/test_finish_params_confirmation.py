"""
Tests for FinishParams confirmation metadata enrichment.

Validates:
- FinishParams accepts confirmation metadata fields
- Fields default to None (backward compatible)
- Legacy constructors still work
- Intent builder populates fields when provided
- Pipeline integration carries confirmation through to final params
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.pipeline.finish_params import FinishParams, finish_params_to_decision
from strategy.signals import Side, Signal


# --- FINISH PARAMS CONSTRUCTION TESTS -----------------------------------------

class TestFinishParamsConfirmationFields:

    def test_default_fields_are_none(self):
        """New confirmation fields default to None."""
        fp = FinishParams(should_trade=False, reason="test")

        assert fp.confirmation_strength is None
        assert fp.confirmation_body_pct is None
        assert fp.confirmation_wick_ratio is None
        assert fp.confirmation_close_location is None

    def test_fields_populated_when_provided(self):
        """Confirmation fields can be set explicitly."""
        fp = FinishParams(
            should_trade=True,
            reason="ok",
            confirmation_strength="STRONG",
            confirmation_body_pct=0.75,
            confirmation_wick_ratio=0.25,
            confirmation_close_location=0.90,
        )

        assert fp.confirmation_strength == "STRONG"
        assert fp.confirmation_body_pct == 0.75
        assert fp.confirmation_wick_ratio == 0.25
        assert fp.confirmation_close_location == 0.90

    def test_weak_confirmation_fields(self):
        """WEAK confirmation metadata stored correctly."""
        fp = FinishParams(
            should_trade=True,
            reason="ok",
            confirmation_strength="WEAK",
            confirmation_body_pct=0.52,
            confirmation_wick_ratio=0.48,
            confirmation_close_location=0.65,
        )

        assert fp.confirmation_strength == "WEAK"
        assert fp.confirmation_body_pct == 0.52

    def test_invalid_confirmation_fields(self):
        """INVALID confirmation metadata stored correctly."""
        fp = FinishParams(
            should_trade=False,
            reason="failed_confirmation:body too weak",
            confirmation_strength="INVALID",
            confirmation_body_pct=0.30,
            confirmation_wick_ratio=0.70,
            confirmation_close_location=0.4,
        )

        assert fp.confirmation_strength == "INVALID"
        assert fp.confirmation_body_pct == 0.30

    def test_frozen_dataclass_immutable(self):
        """FinishParams is frozen — fields cannot be modified after creation."""
        fp = FinishParams(
            should_trade=True,
            reason="ok",
            confirmation_strength="STRONG",
        )

        with pytest.raises(Exception):  # FrozenInstanceError
            fp.confirmation_strength = "WEAK"


# --- BACKWARD COMPATIBILITY TESTS --------------------------------------------

class TestFinishParamsBackwardCompat:

    def test_legacy_construction_no_confirmation(self):
        """Existing FinishParams construction without confirmation fields still works."""
        fp = FinishParams(
            should_trade=True,
            reason="ok",
            signal=Signal(pattern="HAMMER", side=Side.BUY, bar_index=5, bar_time=1000),
            intent=None,
            bias=Side.BUY,
            patterns=["HAMMER"],
            score=6,
            bias_phase="CONFIRMED",
            bias_validation_score=4,
            structure_ok=True,
            bias_strength=70.0,
            bias_age_seconds=300.0,
            bias_window_phase="MIDDLE",
            confluence_threshold_dynamic=5.0,
            regime_state="TRENDING",
            confluence_breakdown={"base_score": 6.0, "final_score": 6.0},
        )

        # All legacy fields work
        assert fp.should_trade is True
        assert fp.score == 6
        assert fp.bias_phase == "CONFIRMED"
        # New fields default to None
        assert fp.confirmation_strength is None

    def test_finish_params_to_decision_still_works(self):
        """finish_params_to_decision converts cleanly with new fields present."""
        fp = FinishParams(
            should_trade=True,
            reason="ok",
            signal=Signal(pattern="BULLISH_ENGULFING", side=Side.BUY, bar_index=5, bar_time=1000),
            intent=None,
            bias=Side.BUY,
            patterns=["BULLISH_ENGULFING"],
            score=6,
            confirmation_strength="STRONG",
            confirmation_body_pct=0.80,
        )

        decision = finish_params_to_decision(fp)

        # Decision still has correct fields
        assert decision.should_trade is True
        assert decision.reason == "ok"
        assert decision.score == 6

    def test_finish_params_to_decision_ignores_confirmation(self):
        """Decision dataclass does not receive confirmation fields (it's not in Decision)."""
        fp = FinishParams(
            should_trade=False,
            reason="test",
            confirmation_strength="WEAK",
            confirmation_body_pct=0.55,
        )

        decision = finish_params_to_decision(fp)

        # Decision should not have these fields (not part of Decision contract)
        assert not hasattr(decision, "confirmation_strength")
        assert decision.should_trade is False


# --- INTENT BUILDER INTEGRATION TESTS ----------------------------------------

class TestIntentBuilderConfirmationPassthrough:

    def test_intent_builder_passes_confirmation_to_finish_params(self):
        """run_build_intent populates confirmation fields in returned FinishParams."""
        from core.pipeline.intent_builder import run_build_intent
        from core.state.delta import StateDelta
        from core.pipeline_types import QualityResult

        # Mock dependencies
        mock_risk = MagicMock()
        mock_risk.build_intent.return_value = None  # Reject ? produces FinishParams with should_trade=False

        mock_snapshot = MagicMock()
        mock_snapshot.bias_phase = "CONFIRMED"
        mock_snapshot.bias_strength = 70.0
        mock_snapshot.bias_age_seconds = 300.0

        from data.mt5_data import Candle
        candles = [Candle(time=1000, open=1.10, high=1.105, low=1.095, close=1.104, tick_volume=100)]

        result = run_build_intent(
            risk=mock_risk,
            symbol="EURUSD",
            signal=Signal(pattern="HAMMER", side=Side.BUY, bar_index=0, bar_time=1000),
            candles=candles,
            closed_i=0,
            bid=1.10,
            ask=1.1002,
            current_time_s=1000.0,
            snapshot=mock_snapshot,
            delta=StateDelta(),
            evaluation_bias=Side.BUY,
            pattern_names=["HAMMER"],
            score_int=5,
            bias_validation_score=4,
            structure_ok=True,
            bias_window_phase="MIDDLE",
            confluence_threshold_dynamic=5.0,
            breakdown={"base_score": 5.0},
            regime_state="TRENDING",
            layer_quality=QualityResult(),
            confirmation_strength="STRONG",
            confirmation_body_pct=0.72,
            confirmation_wick_ratio=0.28,
            confirmation_close_location=0.85,
        )

        assert result.confirmation_strength == "STRONG"
        assert result.confirmation_body_pct == 0.72
        assert result.confirmation_wick_ratio == 0.28
        assert result.confirmation_close_location == 0.85

    def test_intent_builder_none_confirmation_when_not_provided(self):
        """run_build_intent returns None confirmation when not provided (backward compat)."""
        from core.pipeline.intent_builder import run_build_intent
        from core.state.delta import StateDelta
        from core.pipeline_types import QualityResult

        mock_risk = MagicMock()
        mock_risk.build_intent.return_value = None

        mock_snapshot = MagicMock()
        mock_snapshot.bias_phase = "CONFIRMED"
        mock_snapshot.bias_strength = 70.0
        mock_snapshot.bias_age_seconds = 300.0

        from data.mt5_data import Candle
        candles = [Candle(time=1000, open=1.10, high=1.105, low=1.095, close=1.104, tick_volume=100)]

        result = run_build_intent(
            risk=mock_risk,
            symbol="EURUSD",
            signal=Signal(pattern="HAMMER", side=Side.BUY, bar_index=0, bar_time=1000),
            candles=candles,
            closed_i=0,
            bid=1.10,
            ask=1.1002,
            current_time_s=1000.0,
            snapshot=mock_snapshot,
            delta=StateDelta(),
            evaluation_bias=Side.BUY,
            pattern_names=["HAMMER"],
            score_int=5,
            bias_validation_score=4,
            structure_ok=True,
            bias_window_phase="MIDDLE",
            confluence_threshold_dynamic=5.0,
            breakdown={"base_score": 5.0},
            regime_state="TRENDING",
            layer_quality=QualityResult(),
            # No confirmation_* params provided
        )

        assert result.confirmation_strength is None
        assert result.confirmation_body_pct is None
        assert result.confirmation_wick_ratio is None
        assert result.confirmation_close_location is None
