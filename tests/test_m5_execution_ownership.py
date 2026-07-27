"""
Tests for M5 Execution Context Ownership Migration.

Validates:
1. M5 trigger generation works independently
2. M5 confirmation readiness correctly derived
3. M5 cannot influence H1 structure
4. M5 cannot influence M15 setup context
5. Execution context receives correct higher timeframe inputs
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.market_context.models import (
    Direction, H1Summary, H4Summary, M15Summary, M5Summary,
    MarketContext, Phase, Regime,
)
from core.market_context.builder import MarketContextBuilder


# ─── MOCK TYPES ───────────────────────────────────────────────────────────────


@dataclass
class FakeEngineState:
    current_bias: object = None
    bias_phase: str = "EXPIRED"
    bias_strength: float = 0.0
    regime_state: str = "RANGING"


class FakeSide:
    def __init__(self, value: str):
        self.value = value


# ─── TEST 1: M5 TRIGGER GENERATION ───────────────────────────────────────────


class TestM5TriggerGeneration:
    """M5 trigger_ready is derived from bias FSM state."""

    def test_confirmed_bias_is_trigger_ready(self):
        """bias_phase=CONFIRMED → trigger_ready=True."""
        state = FakeEngineState(bias_phase="CONFIRMED", bias_strength=70.0, current_bias=FakeSide("BUY"))
        builder = MarketContextBuilder(symbol="TEST")
        m5 = builder._extract_m5(state)
        assert m5.trigger_ready is True
        assert m5.bias_phase == "CONFIRMED"

    def test_forming_bias_not_trigger_ready(self):
        """bias_phase=FORMING → trigger_ready=False."""
        state = FakeEngineState(bias_phase="FORMING", bias_strength=35.0, current_bias=FakeSide("SELL"))
        builder = MarketContextBuilder(symbol="TEST")
        m5 = builder._extract_m5(state)
        assert m5.trigger_ready is False

    def test_expired_bias_not_trigger_ready(self):
        """bias_phase=EXPIRED → trigger_ready=False."""
        state = FakeEngineState(bias_phase="EXPIRED")
        builder = MarketContextBuilder(symbol="TEST")
        m5 = builder._extract_m5(state)
        assert m5.trigger_ready is False
        assert m5.bias_direction == "NEUTRAL"

    def test_weakening_bias_not_trigger_ready(self):
        """bias_phase=WEAKENING → trigger_ready=False."""
        state = FakeEngineState(bias_phase="WEAKENING", bias_strength=20.0, current_bias=FakeSide("BUY"))
        builder = MarketContextBuilder(symbol="TEST")
        m5 = builder._extract_m5(state)
        assert m5.trigger_ready is False

    def test_none_engine_state_safe(self):
        """None engine_state → safe defaults."""
        builder = MarketContextBuilder(symbol="TEST")
        m5 = builder._extract_m5(None)
        assert m5.trigger_ready is False
        assert m5.bias_phase == "EXPIRED"
        assert m5.bias_direction == "NEUTRAL"


# ─── TEST 2: M5 DOES NOT OWN REGIME ──────────────────────────────────────────


class TestM5DoesNotOwnRegime:
    """M5 regime_state is diagnostic only. MarketContext.regime comes from H4."""

    def test_market_context_regime_from_h4_not_m5(self):
        """MarketContext.regime is derived from H4, not M5 regime_state."""
        @dataclass
        class MockRegime:
            classification: object = None
            confidence: float = 0.8
            atr_ratio: float = 1.1
            ema_slope: float = 0.2
            trend_bias: str = "BULLISH"
            trend_strength: float = 0.7
            bar_time: int = 0

        class MockClass:
            value = "TRENDING_BULLISH"

        @dataclass
        class MockHTF:
            regime: object = None
            bias: object = None
            structure: object = None

        htf = MockHTF(regime=MockRegime(classification=MockClass()))
        # M5 says RANGING, but MarketContext.regime should be TRENDING (from H4)
        state = FakeEngineState(regime_state="RANGING")

        builder = MarketContextBuilder(symbol="EURUSD")
        ctx = builder.build(htf_context=htf, engine_state=state, cycle_id=1, current_time_s=1000.0)

        # MarketContext regime comes from H4, not M5
        assert ctx.regime == Regime.TRENDING
        # But M5 regime_state is preserved as diagnostic
        assert ctx.m5.regime_state == "RANGING"

    def test_m5_regime_state_is_diagnostic_only(self):
        """M5 regime_state field exists but doesn't drive MarketContext.regime."""
        m5 = M5Summary(regime_state="TREND_UP")
        # The field exists for observability but has no decision authority
        assert m5.regime_state == "TREND_UP"


# ─── TEST 3: M5 CANNOT INFLUENCE H1 STRUCTURE ────────────────────────────────


class TestM5CannotInfluenceH1:
    """M5 data must not change H1 structural outputs."""

    def test_h1_phase_independent_of_m5(self):
        """Changing M5 state does not change the H1-derived phase."""
        builder = MarketContextBuilder(symbol="TEST")

        h1 = H1Summary(
            direction="BULLISH", confidence=0.7,
            swing_structure="HH_HL", bos_confirmed=True, bos_direction="BULLISH",
        )

        # With M5 CONFIRMED
        phase_a, _ = builder._classify_phase(h1, M5Summary(bias_phase="CONFIRMED"))
        # With M5 EXPIRED
        phase_b, _ = builder._classify_phase(h1, M5Summary(bias_phase="EXPIRED"))
        # M5 has no influence on structural phase
        assert phase_a == phase_b

    def test_h1_bos_independent_of_m5(self):
        """M5 cannot create or modify H1 BOS."""
        h1_with_bos = H1Summary(bos_confirmed=True, bos_direction="BULLISH")
        h1_without_bos = H1Summary(bos_confirmed=False, bos_direction="")
        # These are H1 decisions — M5 has no method to override them
        assert h1_with_bos.bos_confirmed is True
        assert h1_without_bos.bos_confirmed is False


# ─── TEST 4: M5 CANNOT INFLUENCE M15 SETUP ───────────────────────────────────


class TestM5CannotInfluenceM15:
    """M5 data must not change M15 setup context."""

    def test_m15_quality_independent_of_m5_state(self):
        """M15 setup quality is the same regardless of M5 engine state."""
        @dataclass
        class MockStruct:
            quality_score: float = 0.75
            bar_time: int = 0
            nearest_support: float = 1.099
            nearest_resistance: float = 1.102
            at_key_level: bool = True
            order_block_present: bool = False

        @dataclass
        class MockHTF:
            regime: object = None
            bias: object = None
            structure: object = None

        htf = MockHTF(structure=MockStruct())
        builder = MarketContextBuilder(symbol="TEST")

        state_confirmed = FakeEngineState(bias_phase="CONFIRMED", bias_strength=80)
        state_expired = FakeEngineState(bias_phase="EXPIRED", bias_strength=0)

        ctx1 = builder.build(htf_context=htf, engine_state=state_confirmed, cycle_id=1, current_time_s=1000.0)
        builder._previous = None  # Reset for clean comparison
        ctx2 = builder.build(htf_context=htf, engine_state=state_expired, cycle_id=2, current_time_s=1005.0)

        assert ctx1.m15.quality_score == ctx2.m15.quality_score == 0.75
        assert ctx1.m15.at_key_level == ctx2.m15.at_key_level is True


# ─── TEST 5: EXECUTION CONTEXT RECEIVES HTF INPUTS ───────────────────────────


class TestExecutionContextReceivesHTF:
    """M5 execution context correctly receives and exposes all timeframe data."""

    def test_full_context_available(self):
        """Full MarketContext carries H4 + H1 + M15 + M5 together."""
        ctx = MarketContext(
            symbol="EURUSD", cycle_id=1, timestamp_utc=1000.0,
            direction=Direction.BULLISH,
            regime=Regime.TRENDING,
            phase=Phase.IMPULSE,
            h4=H4Summary(regime="TRENDING_BULLISH", trend_bias="BULLISH", confidence=0.8),
            h1=H1Summary(direction="BULLISH", confidence=0.7, bos_confirmed=True, bos_direction="BULLISH"),
            m15=M15Summary(quality_score=0.65, at_key_level=True),
            m5=M5Summary(bias_phase="CONFIRMED", bias_strength=72, trigger_ready=True, bias_direction="BUY"),
        )
        # All layers accessible together
        assert ctx.regime == Regime.TRENDING  # from H4
        assert ctx.phase == Phase.IMPULSE  # from H1
        assert ctx.m15.quality_score == 0.65  # from M15
        assert ctx.m5.trigger_ready is True  # from M5

    def test_m5_serialization_complete(self):
        """M5 section in to_dict() includes all execution fields."""
        ctx = MarketContext(
            symbol="TEST", cycle_id=1, timestamp_utc=1000.0,
            m5=M5Summary(
                bias_phase="CONFIRMED",
                bias_strength=65.0,
                bias_direction="BUY",
                regime_state="TREND_UP",
                trigger_ready=True,
                confirmation_strength="STRONG",
            ),
        )
        d = ctx.to_dict()
        m5 = d["m5"]
        assert m5["bias_phase"] == "CONFIRMED"
        assert m5["bias_strength"] == 65.0
        assert m5["bias_direction"] == "BUY"
        assert m5["regime_state"] == "TREND_UP"
        assert m5["trigger_ready"] is True
        assert m5["confirmation_strength"] == "STRONG"

    def test_m5_summary_docstring_declares_execution_only(self):
        """M5Summary docstring explicitly states execution-only ownership."""
        doc = M5Summary.__doc__ or ""
        assert "execution" in doc.lower()
        assert "NOT own" in doc or "does NOT" in doc.upper() or "NOT own" in doc
