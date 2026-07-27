"""
Tests for scoring engine confirmation strength propagation.

Validates:
- STRONG confirmation gets +1.0 bonus (same as legacy behavior)
- WEAK confirmation gets +0.5 bonus (reduced)
- INVALID confirmation gets +0.0 bonus (no bonus)
- Legacy callers (no confirmation_strength param) default to STRONG
- Score difference between STRONG and WEAK is exactly 0.5
- run_scoring_engine accepts and forwards confirmation_strength
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategy.signals import Side, Signal
from core.pipeline.scoring_engine import calculate_confluence, run_scoring_engine
from core.pipeline_types import ScoreResult


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _signal(pattern: str = "BULLISH_ENGULFING", side: Side = Side.BUY) -> Signal:
    return Signal(pattern=pattern, side=side, bar_index=5, bar_time=1000)


# ─── calculate_confluence TESTS ───────────────────────────────────────────────

class TestCalculateConfluenceStrength:

    def test_strong_confirmation_gives_full_bonus(self):
        """STRONG confirmation adds +1.0 to score."""
        score = calculate_confluence(
            _signal(), Side.BUY, ema_ok=True, chop_ok=True, confirmed=True,
            confirmation_strength="STRONG",
        )
        # Base: 2.0 (bias) + 2.0 (strong pattern) + 1.0 (ema) + 1.0 (STRONG) = 6.0
        assert score == pytest.approx(6.0)

    def test_weak_confirmation_gives_half_bonus(self):
        """WEAK confirmation adds +0.5 to score."""
        score = calculate_confluence(
            _signal(), Side.BUY, ema_ok=True, chop_ok=True, confirmed=True,
            confirmation_strength="WEAK",
        )
        # Base: 2.0 (bias) + 2.0 (strong pattern) + 1.0 (ema) + 0.5 (WEAK) = 5.5
        assert score == pytest.approx(5.5)

    def test_invalid_confirmation_gives_no_bonus(self):
        """INVALID confirmation adds +0.0 to score."""
        score = calculate_confluence(
            _signal(), Side.BUY, ema_ok=True, chop_ok=True, confirmed=True,
            confirmation_strength="INVALID",
        )
        # Base: 2.0 (bias) + 2.0 (strong pattern) + 1.0 (ema) + 0.0 (INVALID) = 5.0
        assert score == pytest.approx(5.0)

    def test_strong_minus_weak_equals_half_point(self):
        """Difference between STRONG and WEAK is exactly 0.5."""
        strong = calculate_confluence(
            _signal(), Side.BUY, ema_ok=True, chop_ok=True, confirmed=True,
            confirmation_strength="STRONG",
        )
        weak = calculate_confluence(
            _signal(), Side.BUY, ema_ok=True, chop_ok=True, confirmed=True,
            confirmation_strength="WEAK",
        )
        assert (strong - weak) == pytest.approx(0.5)

    def test_strong_minus_invalid_equals_one_point(self):
        """Difference between STRONG and INVALID is exactly 1.0."""
        strong = calculate_confluence(
            _signal(), Side.BUY, ema_ok=True, chop_ok=True, confirmed=True,
            confirmation_strength="STRONG",
        )
        invalid = calculate_confluence(
            _signal(), Side.BUY, ema_ok=True, chop_ok=True, confirmed=True,
            confirmation_strength="INVALID",
        )
        assert (strong - invalid) == pytest.approx(1.0)

    def test_legacy_caller_no_strength_param_defaults_strong(self):
        """Calling without confirmation_strength defaults to STRONG (+1.0)."""
        # Legacy call: no confirmation_strength kwarg
        score_legacy = calculate_confluence(
            _signal(), Side.BUY, ema_ok=True, chop_ok=True, confirmed=True,
        )
        # Explicit STRONG call
        score_strong = calculate_confluence(
            _signal(), Side.BUY, ema_ok=True, chop_ok=True, confirmed=True,
            confirmation_strength="STRONG",
        )
        assert score_legacy == score_strong

    def test_weak_pattern_with_weak_confirmation(self):
        """Weak pattern + WEAK confirmation produces lower score."""
        score = calculate_confluence(
            _signal("HAMMER"), Side.BUY, ema_ok=True, chop_ok=True, confirmed=True,
            confirmation_strength="WEAK",
        )
        # Base: 2.0 (bias) + 1.0 (weak pattern) + 1.0 (ema) + 0.5 (WEAK) = 4.5
        assert score == pytest.approx(4.5)

    def test_weak_pattern_with_strong_confirmation(self):
        """Weak pattern + STRONG confirmation produces standard score."""
        score = calculate_confluence(
            _signal("HAMMER"), Side.BUY, ema_ok=True, chop_ok=True, confirmed=True,
            confirmation_strength="STRONG",
        )
        # Base: 2.0 (bias) + 1.0 (weak pattern) + 1.0 (ema) + 1.0 (STRONG) = 5.0
        assert score == pytest.approx(5.0)

    def test_unknown_strength_falls_back_to_confirmed_bool(self):
        """Unknown strength value falls back to confirmed bool logic."""
        score_confirmed = calculate_confluence(
            _signal(), Side.BUY, ema_ok=True, chop_ok=True, confirmed=True,
            confirmation_strength="UNKNOWN_VALUE",
        )
        score_not_confirmed = calculate_confluence(
            _signal(), Side.BUY, ema_ok=True, chop_ok=True, confirmed=False,
            confirmation_strength="UNKNOWN_VALUE",
        )
        # Unknown with confirmed=True → fallback +1.0
        # Unknown with confirmed=False → fallback +0.0
        assert (score_confirmed - score_not_confirmed) == pytest.approx(1.0)


# ─── run_scoring_engine INTEGRATION TESTS ─────────────────────────────────────

class TestRunScoringEngineStrength:

    def _make_snapshot(self):
        """Create a minimal mock StateSnapshot."""
        snap = MagicMock()
        snap.bias_age_seconds = 300.0
        snap.last_strong_impulse_direction = Side.BUY
        snap.current_bias = Side.BUY
        snap.last_sweep_low = None
        snap.last_sweep_high = None
        snap.bias_phase = "CONFIRMED"
        snap.bias_strength = 70.0
        snap.structure_score = 3.0
        snap.structure_regime = "BUILDING"
        return snap

    def _make_config(self):
        """Create a minimal mock config."""
        cfg = MagicMock()
        cfg.MIN_SCORE_TO_TRADE = 5
        cfg.MARKET_FILTER_LOOKBACK = 5
        return cfg

    def _make_candles(self, n=20):
        """Create minimal candles for volatility_penalty."""
        from data.mt5_data import Candle
        candles = []
        for i in range(n):
            candles.append(Candle(
                time=1000 + i * 300,
                open=1.10 + i * 0.002,
                high=1.10 + i * 0.002 + 0.003,
                low=1.10 + i * 0.002 - 0.001,
                close=1.10 + i * 0.002 + 0.002,
                tick_volume=100,
            ))
        return candles

    def test_run_scoring_engine_accepts_confirmation_strength(self):
        """run_scoring_engine accepts confirmation_strength parameter without error."""
        layer = ScoreResult()
        candles = self._make_candles()

        result = run_scoring_engine(
            signal=_signal(),
            evaluation_bias=Side.BUY,
            trend_aligned=True,
            candles=candles,
            closed_i=15,
            snapshot=self._make_snapshot(),
            config=self._make_config(),
            stability_score=0.9,
            bias_window_phase="MIDDLE",
            confluence_threshold_dynamic=5.0,
            pattern_names=["BULLISH_ENGULFING"],
            bias_validation_score=4,
            structure_ok=True,
            layer_score=layer,
            regime_state="TRENDING",
            confirmation_strength="STRONG",
        )

        # Should not crash; layer should be populated
        assert layer.evaluated is True
        assert layer.base_score > 0

    def test_strong_scores_higher_than_weak(self):
        """STRONG confirmation produces higher final score than WEAK."""
        candles = self._make_candles()
        snapshot = self._make_snapshot()
        cfg = self._make_config()

        layer_strong = ScoreResult()
        run_scoring_engine(
            signal=_signal(),
            evaluation_bias=Side.BUY,
            trend_aligned=True,
            candles=candles,
            closed_i=15,
            snapshot=snapshot,
            config=cfg,
            stability_score=0.9,
            bias_window_phase="MIDDLE",
            confluence_threshold_dynamic=5.0,
            pattern_names=["BULLISH_ENGULFING"],
            bias_validation_score=4,
            structure_ok=True,
            layer_score=layer_strong,
            regime_state="TRENDING",
            confirmation_strength="STRONG",
        )

        layer_weak = ScoreResult()
        run_scoring_engine(
            signal=_signal(),
            evaluation_bias=Side.BUY,
            trend_aligned=True,
            candles=candles,
            closed_i=15,
            snapshot=snapshot,
            config=cfg,
            stability_score=0.9,
            bias_window_phase="MIDDLE",
            confluence_threshold_dynamic=5.0,
            pattern_names=["BULLISH_ENGULFING"],
            bias_validation_score=4,
            structure_ok=True,
            layer_score=layer_weak,
            regime_state="TRENDING",
            confirmation_strength="WEAK",
        )

        assert layer_strong.final_score > layer_weak.final_score

    def test_confirmation_strength_in_breakdown(self):
        """confirmation_strength appears in the score breakdown dict."""
        layer = ScoreResult()
        candles = self._make_candles()

        run_scoring_engine(
            signal=_signal(),
            evaluation_bias=Side.BUY,
            trend_aligned=True,
            candles=candles,
            closed_i=15,
            snapshot=self._make_snapshot(),
            config=self._make_config(),
            stability_score=0.9,
            bias_window_phase="MIDDLE",
            confluence_threshold_dynamic=5.0,
            pattern_names=["BULLISH_ENGULFING"],
            bias_validation_score=4,
            structure_ok=True,
            layer_score=layer,
            regime_state="TRENDING",
            confirmation_strength="WEAK",
        )

        assert "confirmation_strength" in layer.breakdown
        assert layer.breakdown["confirmation_strength"] == "WEAK"

    def test_default_strength_is_strong(self):
        """Without confirmation_strength param, defaults to STRONG (backward compat)."""
        layer = ScoreResult()
        candles = self._make_candles()

        # Call WITHOUT confirmation_strength — should default to STRONG
        run_scoring_engine(
            signal=_signal(),
            evaluation_bias=Side.BUY,
            trend_aligned=True,
            candles=candles,
            closed_i=15,
            snapshot=self._make_snapshot(),
            config=self._make_config(),
            stability_score=0.9,
            bias_window_phase="MIDDLE",
            confluence_threshold_dynamic=5.0,
            pattern_names=["BULLISH_ENGULFING"],
            bias_validation_score=4,
            structure_ok=True,
            layer_score=layer,
            regime_state="TRENDING",
        )

        assert layer.breakdown["confirmation_strength"] == "STRONG"
