"""
Tests for Migration 2 — Trend Alignment Authority from M5 EMA50 → H1 Phase.

Validates:
1. H1 authority overrides M5 EMA50
2. M5 fallback still works
3. Startup/missing context handled safely
4. Serialization includes new fields
5. DecisionTrace persists correctly
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest

from core.decision_trace import build_decision_trace, DecisionTrace


# ─── MOCK TYPES ───────────────────────────────────────────────────────────────

@dataclass
class FakeCandle:
    open: float = 1.1000
    high: float = 1.1010
    low: float = 1.0990
    close: float = 1.1005
    time: int = 1000
    tick_volume: int = 100
    real_volume: int = 100
    spread: int = 1


class FakeBiasDirection:
    def __init__(self, value: str):
        self.value = value


@dataclass
class FakeBiasSnapshot:
    direction: object = None
    confidence: float = 0.7
    bar_time: int = 0
    ema_position: float = 0.5
    swing_structure: str = "HH_HL"


@dataclass
class FakeHTFContext:
    regime: object = None
    bias: object = None
    structure: object = None


# ─── TEST 1: H1 AUTHORITY OVERRIDES M5 ───────────────────────────────────────


class TestH1AuthorityOverridesM5:
    """H1 Phase direction is used for trend alignment when available."""

    def test_h1_bullish_buy_aligned(self):
        """H1 BULLISH + BUY pattern → high score (aligned)."""
        from core.pipeline.new_engine import _score_trend_alignment
        from strategy.signals import Signal, Side

        pattern = Signal(pattern="HAMMER", side=Side.BUY, bar_index=60, bar_time=1000, confidence=0.7)
        candles = [FakeCandle(time=i * 300) for i in range(65)]
        htf = FakeHTFContext(bias=FakeBiasSnapshot(direction=FakeBiasDirection("BULLISH"), confidence=0.8))

        with patch("core.config.MARKET_CONTEXT_ENABLED", True):
            score = _score_trend_alignment(pattern, candles, 60, type("cfg", (), {"TREND_EMA_PERIOD": 50}), htf)

        assert score >= 0.6  # Aligned: 0.6 + 0.4*confidence

    def test_h1_bearish_buy_counter(self):
        """H1 BEARISH + BUY pattern → low score (counter-trend)."""
        from core.pipeline.new_engine import _score_trend_alignment
        from strategy.signals import Signal, Side

        pattern = Signal(pattern="HAMMER", side=Side.BUY, bar_index=60, bar_time=1000, confidence=0.7)
        candles = [FakeCandle(time=i * 300) for i in range(65)]
        htf = FakeHTFContext(bias=FakeBiasSnapshot(direction=FakeBiasDirection("BEARISH"), confidence=0.8))

        with patch("core.config.MARKET_CONTEXT_ENABLED", True):
            score = _score_trend_alignment(pattern, candles, 60, type("cfg", (), {"TREND_EMA_PERIOD": 50}), htf)

        assert score <= 0.3  # Counter-trend

    def test_h1_neutral_returns_neutral(self):
        """H1 NEUTRAL → 0.5 score."""
        from core.pipeline.new_engine import _score_trend_alignment
        from strategy.signals import Signal, Side

        pattern = Signal(pattern="HAMMER", side=Side.BUY, bar_index=60, bar_time=1000, confidence=0.7)
        candles = [FakeCandle(time=i * 300) for i in range(65)]
        htf = FakeHTFContext(bias=FakeBiasSnapshot(direction=FakeBiasDirection("NEUTRAL"), confidence=0.5))

        with patch("core.config.MARKET_CONTEXT_ENABLED", True):
            score = _score_trend_alignment(pattern, candles, 60, type("cfg", (), {"TREND_EMA_PERIOD": 50}), htf)

        assert score == 0.5

    def test_h1_bearish_sell_aligned(self):
        """H1 BEARISH + SELL pattern → high score (aligned)."""
        from core.pipeline.new_engine import _score_trend_alignment
        from strategy.signals import Signal, Side

        pattern = Signal(pattern="SHOOTING_STAR", side=Side.SELL, bar_index=60, bar_time=1000, confidence=0.7)
        candles = [FakeCandle(time=i * 300) for i in range(65)]
        htf = FakeHTFContext(bias=FakeBiasSnapshot(direction=FakeBiasDirection("BEARISH"), confidence=0.9))

        with patch("core.config.MARKET_CONTEXT_ENABLED", True):
            score = _score_trend_alignment(pattern, candles, 60, type("cfg", (), {"TREND_EMA_PERIOD": 50}), htf)

        assert score >= 0.6  # Aligned


# ─── TEST 2: M5 FALLBACK WORKS ───────────────────────────────────────────────


class TestM5FallbackWorks:
    """M5 EMA50 is used when H1 context is unavailable."""

    def test_no_htf_context_uses_m5(self):
        """htf_context=None → M5 EMA50 logic."""
        from core.pipeline.new_engine import _score_trend_alignment
        from strategy.signals import Signal, Side

        # Create candles where price is clearly above EMA (all ascending)
        candles = [FakeCandle(close=1.0 + i * 0.001, time=i * 300) for i in range(65)]
        pattern = Signal(pattern="HAMMER", side=Side.BUY, bar_index=60, bar_time=1000, confidence=0.7)

        score = _score_trend_alignment(pattern, candles, 60, type("cfg", (), {"TREND_EMA_PERIOD": 50}), None)
        # Price ascending → close > EMA → BUY aligned → 1.0
        assert score == 1.0

    def test_market_context_disabled_uses_m5(self):
        """MARKET_CONTEXT_ENABLED=False → M5 fallback even with HTF context."""
        from core.pipeline.new_engine import _score_trend_alignment
        from strategy.signals import Signal, Side

        candles = [FakeCandle(close=1.0 + i * 0.001, time=i * 300) for i in range(65)]
        pattern = Signal(pattern="HAMMER", side=Side.BUY, bar_index=60, bar_time=1000, confidence=0.7)
        htf = FakeHTFContext(bias=FakeBiasSnapshot(direction=FakeBiasDirection("BEARISH"), confidence=0.9))

        with patch("core.config.MARKET_CONTEXT_ENABLED", False):
            score = _score_trend_alignment(pattern, candles, 60, type("cfg", (), {"TREND_EMA_PERIOD": 50}), htf)

        # M5 EMA: price ascending → 1.0 (ignores H1 BEARISH because disabled)
        assert score == 1.0

    def test_h1_bias_none_falls_back_to_m5(self):
        """HTF context present but bias is None → M5 fallback."""
        from core.pipeline.new_engine import _score_trend_alignment
        from strategy.signals import Signal, Side

        candles = [FakeCandle(close=1.0 + i * 0.001, time=i * 300) for i in range(65)]
        pattern = Signal(pattern="HAMMER", side=Side.BUY, bar_index=60, bar_time=1000, confidence=0.7)
        htf = FakeHTFContext(bias=None)

        with patch("core.config.MARKET_CONTEXT_ENABLED", True):
            score = _score_trend_alignment(pattern, candles, 60, type("cfg", (), {"TREND_EMA_PERIOD": 50}), htf)

        # No H1 data → falls through to M5 EMA
        assert score == 1.0


# ─── TEST 3: SAFE STARTUP BEHAVIOUR ──────────────────────────────────────────


class TestSafeStartup:
    """Missing or corrupt context handled gracefully."""

    def test_insufficient_candles_neutral(self):
        """Less than EMA period candles → 0.5 neutral."""
        from core.pipeline.new_engine import _score_trend_alignment
        from strategy.signals import Signal, Side

        candles = [FakeCandle(time=i * 300) for i in range(10)]
        pattern = Signal(pattern="HAMMER", side=Side.BUY, bar_index=8, bar_time=1000, confidence=0.7)

        score = _score_trend_alignment(pattern, candles, 8, type("cfg", (), {"TREND_EMA_PERIOD": 50}), None)
        assert score == 0.5

    def test_garbage_htf_context_no_crash(self):
        """Garbage htf_context doesn't crash — falls back to M5."""
        from core.pipeline.new_engine import _score_trend_alignment
        from strategy.signals import Signal, Side

        candles = [FakeCandle(close=1.0 + i * 0.001, time=i * 300) for i in range(65)]
        pattern = Signal(pattern="HAMMER", side=Side.BUY, bar_index=60, bar_time=1000, confidence=0.7)

        with patch("core.config.MARKET_CONTEXT_ENABLED", True):
            score = _score_trend_alignment(pattern, candles, 60, type("cfg", (), {"TREND_EMA_PERIOD": 50}), "not_a_real_context")

        # Should not crash — falls back to M5
        assert 0.0 <= score <= 1.0


# ─── TEST 4: DECISION TRACE SERIALIZATION ─────────────────────────────────────


class TestDecisionTraceSerialization:
    """New trend alignment fields persist correctly."""

    def test_h1_source_in_trace(self):
        result = {
            "action": "NO_TRADE", "reason": "ev_policy_blocked", "score": 0.4,
            "entity_id": "EURUSD_1234", "symbol": "EURUSD", "cycle_id": 1,
            "components": {}, "regime_source": "H4_MARKET_CONTEXT",
            "activation_regime": "TRENDING", "activation_regime_confidence": 0.8,
            "trend_alignment_source": "H1_PHASE",
            "trend_alignment_timeframe": "H1",
            "trend_alignment_confidence": 0.75,
        }
        trace = build_decision_trace(engine_result=result)
        d = trace.to_dict()
        assert d["trend_alignment_source"] == "H1_PHASE"
        assert d["trend_alignment_timeframe"] == "H1"
        assert d["trend_alignment_confidence"] == 0.75

    def test_m5_source_in_trace(self):
        result = {
            "action": "NO_TRADE", "reason": "score_below_threshold", "score": 0.3,
            "entity_id": "GBPUSD_5678", "symbol": "GBPUSD", "cycle_id": 2,
            "components": {}, "regime_source": "M5_CLASSIFIER",
            "activation_regime": "TRANSITIONAL", "activation_regime_confidence": 0.3,
            "trend_alignment_source": "M5_EMA50",
            "trend_alignment_timeframe": "M5",
            "trend_alignment_confidence": 0.0,
        }
        trace = build_decision_trace(engine_result=result)
        d = trace.to_dict()
        assert d["trend_alignment_source"] == "M5_EMA50"
        assert d["trend_alignment_timeframe"] == "M5"
        assert d["trend_alignment_confidence"] == 0.0

    def test_missing_fields_default_empty(self):
        result = {
            "action": "NO_TRADE", "reason": "no_viable_pattern", "score": 0.0,
            "entity_id": "X_1", "symbol": "X", "cycle_id": 0, "components": {},
        }
        trace = build_decision_trace(engine_result=result)
        d = trace.to_dict()
        assert d["trend_alignment_source"] == ""
        assert d["trend_alignment_timeframe"] == ""
        assert d["trend_alignment_confidence"] == 0.0

    def test_fields_are_strings_and_float(self):
        result = {
            "action": "NO_TRADE", "reason": "test", "score": 0.0,
            "entity_id": "T_1", "symbol": "T", "cycle_id": 0, "components": {},
            "trend_alignment_source": "H1_PHASE",
            "trend_alignment_timeframe": "H1",
            "trend_alignment_confidence": 0.88,
        }
        trace = build_decision_trace(engine_result=result)
        d = trace.to_dict()
        assert isinstance(d["trend_alignment_source"], str)
        assert isinstance(d["trend_alignment_timeframe"], str)
        assert isinstance(d["trend_alignment_confidence"], float)
