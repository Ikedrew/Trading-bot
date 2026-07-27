"""
Unit tests for Multi-Timeframe Authority type definitions (Phase 1).

Validates:
- Immutability of frozen dataclasses
- Enum correctness
- HTFContext.is_populated logic
- HTFInfluence.is_blocking and .has_influence logic
- Default values
"""

from __future__ import annotations

import pytest

from core.timeframes.types import (
    BiasDirection,
    BiasSnapshot,
    HTFContext,
    HTFInfluence,
    RegimeClassification,
    RegimeSnapshot,
    StructureSnapshot,
)


# ─── ENUM TESTS ───────────────────────────────────────────────────────────────


class TestRegimeClassification:
    def test_all_values_exist(self):
        assert RegimeClassification.TRENDING_BULLISH.value == "TRENDING_BULLISH"
        assert RegimeClassification.TRENDING_BEARISH.value == "TRENDING_BEARISH"
        assert RegimeClassification.RANGING.value == "RANGING"
        assert RegimeClassification.VOLATILE.value == "VOLATILE"
        assert RegimeClassification.TRANSITIONAL.value == "TRANSITIONAL"

    def test_enum_count(self):
        assert len(RegimeClassification) == 5


class TestBiasDirection:
    def test_all_values_exist(self):
        assert BiasDirection.BULLISH.value == "BULLISH"
        assert BiasDirection.BEARISH.value == "BEARISH"
        assert BiasDirection.NEUTRAL.value == "NEUTRAL"

    def test_enum_count(self):
        assert len(BiasDirection) == 3


# ─── SNAPSHOT IMMUTABILITY TESTS ──────────────────────────────────────────────


class TestRegimeSnapshot:
    def test_frozen(self):
        snap = RegimeSnapshot(
            classification=RegimeClassification.RANGING,
            confidence=0.8,
            bar_time=1000,
            atr_ratio=1.2,
            ema_slope=0.01,
        )
        with pytest.raises(Exception):
            snap.confidence = 0.5  # type: ignore[misc]

    def test_fields(self):
        snap = RegimeSnapshot(
            classification=RegimeClassification.TRENDING_BULLISH,
            confidence=0.95,
            bar_time=12345,
            atr_ratio=1.5,
            ema_slope=0.02,
        )
        assert snap.classification == RegimeClassification.TRENDING_BULLISH
        assert snap.confidence == 0.95
        assert snap.bar_time == 12345
        assert snap.atr_ratio == 1.5
        assert snap.ema_slope == 0.02


class TestBiasSnapshot:
    def test_frozen(self):
        snap = BiasSnapshot(
            direction=BiasDirection.BULLISH,
            confidence=0.7,
            bar_time=2000,
            ema_position=0.5,
            swing_structure="HH_HL",
        )
        with pytest.raises(Exception):
            snap.direction = BiasDirection.BEARISH  # type: ignore[misc]

    def test_fields(self):
        snap = BiasSnapshot(
            direction=BiasDirection.BEARISH,
            confidence=0.6,
            bar_time=3000,
            ema_position=-0.3,
            swing_structure="LH_LL",
        )
        assert snap.direction == BiasDirection.BEARISH
        assert snap.confidence == 0.6
        assert snap.swing_structure == "LH_LL"


class TestStructureSnapshot:
    def test_frozen(self):
        snap = StructureSnapshot(
            quality_score=0.8,
            bar_time=4000,
            nearest_support=1.0800,
            nearest_resistance=1.0900,
            at_key_level=True,
            order_block_present=False,
        )
        with pytest.raises(Exception):
            snap.quality_score = 0.5  # type: ignore[misc]

    def test_fields(self):
        snap = StructureSnapshot(
            quality_score=0.45,
            bar_time=5000,
            nearest_support=1.0750,
            nearest_resistance=1.0850,
            at_key_level=False,
            order_block_present=True,
        )
        assert snap.quality_score == 0.45
        assert snap.at_key_level is False
        assert snap.order_block_present is True


# ─── HTFCONTEXT TESTS ─────────────────────────────────────────────────────────


class TestHTFContext:
    def test_empty_context_not_populated(self):
        ctx = HTFContext()
        assert ctx.is_populated is False

    def test_context_with_regime_is_populated(self):
        regime = RegimeSnapshot(
            classification=RegimeClassification.RANGING,
            confidence=0.8,
            bar_time=1000,
            atr_ratio=1.0,
            ema_slope=0.0,
        )
        ctx = HTFContext(regime=regime)
        assert ctx.is_populated is True

    def test_context_with_bias_is_populated(self):
        bias = BiasSnapshot(
            direction=BiasDirection.BULLISH,
            confidence=0.7,
            bar_time=2000,
            ema_position=0.5,
            swing_structure="HH_HL",
        )
        ctx = HTFContext(bias=bias)
        assert ctx.is_populated is True

    def test_context_frozen(self):
        ctx = HTFContext()
        with pytest.raises(Exception):
            ctx.regime = None  # type: ignore[misc]


# ─── HTFINFLUENCE TESTS ───────────────────────────────────────────────────────


class TestHTFInfluence:
    def test_default_no_influence(self):
        inf = HTFInfluence()
        assert inf.score_adjustment == 0.0
        assert inf.min_score_adjustment == 0.0
        assert inf.directional_block is False
        assert inf.structural_block is False
        assert inf.is_blocking is False
        assert inf.has_influence is False

    def test_score_adjustment_has_influence(self):
        inf = HTFInfluence(score_adjustment=0.5)
        assert inf.has_influence is True
        assert inf.is_blocking is False

    def test_directional_block_is_blocking(self):
        inf = HTFInfluence(directional_block=True, block_reason="h1_contradiction")
        assert inf.is_blocking is True
        assert inf.has_influence is True

    def test_structural_block_is_blocking(self):
        inf = HTFInfluence(structural_block=True, block_reason="m15_quality_low")
        assert inf.is_blocking is True

    def test_frozen(self):
        inf = HTFInfluence()
        with pytest.raises(Exception):
            inf.score_adjustment = 1.0  # type: ignore[misc]
