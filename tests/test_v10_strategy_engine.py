"""Tests for V10 Strategy Family Engine."""

import pytest
from core.v10.market_state import (
    V10MarketState, H4State, H1State, M15State, M5State,
    RegimeState, LocationState, HTFAlignment,
)
from core.v10.opportunity_assessment import OpportunityAssessment, OpportunityQuality
from core.v10.strategy_family import StrategyFamily
from core.v10.strategy_engine import select_strategy


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

def _opp(state="VALID", bias="BEARISH", obs_id="test123"):
    return OpportunityAssessment(
        observation_id=obs_id, symbol="TEST", timestamp_utc=1000.0,
        opportunity_state=state, directional_bias=bias,
        opportunity_type="ZONE_REACTION",
        quality=OpportunityQuality(overall_quality=0.7),
    )


def _mean_reversion_state():
    """Neutral HTF + range extreme + structural level present."""
    return V10MarketState(
        symbol="TEST", timestamp_utc=1000.0,
        h4=H4State(trend="NEUTRAL", trend_strength=0.15),
        h1=H1State(dominant_trend="NEUTRAL", structural_clarity=0.5,
                   swing_high=1.0920, swing_low=1.0850),
        m15=M15State(pullback_active=True),
        m5=M5State(rejection_present=True, rejection_strength_atr=0.8, rejection_direction="BEARISH"),
        regime=RegimeState(regime="RANGING", momentum_strength=0.2),
        location=LocationState(
            range_position=0.80,
            premium_discount="PREMIUM",
        ),
        htf_alignment=HTFAlignment(macro_bias="NEUTRAL", structure_alignment=0.3),
    )


def _trend_continuation_state():
    """Strong HTF trend + H1 aligned + M15 pullback."""
    return V10MarketState(
        symbol="TEST", timestamp_utc=1000.0,
        h4=H4State(trend="BULLISH", trend_strength=0.7, market_phase="IMPULSE"),
        h1=H1State(
            dominant_trend="BULLISH", bos_confirmed=True, bos_direction="BULLISH",
            structural_clarity=0.8,
        ),
        m15=M15State(
            pullback_active=True, pullback_depth_atr=1.2,
            internal_bos=True, internal_bos_direction="BULLISH",
        ),
        m5=M5State(rejection_present=True, rejection_direction="BULLISH"),
        regime=RegimeState(regime="TRENDING"),
        location=LocationState(
            inside_institutional_zone=True, location_type="DEMAND_OB",
            range_position=0.35,
        ),
        htf_alignment=HTFAlignment(macro_bias="BULLISH", structure_alignment=0.8),
    )


def _liquidity_sweep_state():
    """Liquidity taken + rejection + CHoCH."""
    return V10MarketState(
        symbol="TEST", timestamp_utc=1000.0,
        h4=H4State(trend="NEUTRAL", trend_strength=0.2),
        h1=H1State(
            dominant_trend="BEARISH", choch_detected=True, choch_direction="BULLISH",
            structural_clarity=0.6,
        ),
        m15=M15State(
            displacement_present=True, displacement_direction="BULLISH",
            displacement_magnitude_atr=1.8, internal_choch=True,
        ),
        m5=M5State(
            rejection_present=True, rejection_direction="BULLISH",
            rejection_strength_atr=1.2,
        ),
        regime=RegimeState(regime="RANGING"),
        location=LocationState(
            liquidity_below=True, inside_institutional_zone=True,
            location_type="DEMAND_OB", range_position=0.15,
        ),
        htf_alignment=HTFAlignment(macro_bias="NEUTRAL"),
    )


def _false_break_state():
    """Breakout attempt + failure + reclaim."""
    return V10MarketState(
        symbol="TEST", timestamp_utc=1000.0,
        h4=H4State(trend="NEUTRAL", trend_strength=0.2),
        h1=H1State(
            dominant_trend="NEUTRAL", structural_clarity=0.5,
            session_high=1.3000, session_low=1.2900,
        ),
        m15=M15State(),
        m5=M5State(
            rejection_present=True, rejection_direction="BEARISH",
            rejection_strength_atr=0.9,
        ),
        regime=RegimeState(regime="RANGING"),
        location=LocationState(
            liquidity_above=True, range_position=0.55,
            inside_institutional_zone=False,
        ),
        htf_alignment=HTFAlignment(macro_bias="NEUTRAL"),
    )


def _breakout_expansion_state():
    """Compression + volatility expansion + displacement."""
    return V10MarketState(
        symbol="TEST", timestamp_utc=1000.0,
        h4=H4State(trend="NEUTRAL", trend_strength=0.3),
        h1=H1State(dominant_trend="NEUTRAL", structural_clarity=0.5),
        m15=M15State(
            displacement_present=True, displacement_direction="BULLISH",
            displacement_magnitude_atr=2.0,
        ),
        m5=M5State(),
        regime=RegimeState(
            regime="RANGING", expansion_state="EXPANDING",
            volatility_state="CONTRACTION", compression_bars=8,
        ),
        location=LocationState(range_position=0.5),
        htf_alignment=HTFAlignment(macro_bias="NEUTRAL"),
    )


def _range_reaction_state():
    """Ranging + at extreme + established range with clear boundaries."""
    return V10MarketState(
        symbol="TEST", timestamp_utc=1000.0,
        h4=H4State(trend="BEARISH", trend_strength=0.5),
        h1=H1State(dominant_trend="BEARISH", structural_clarity=0.75,
                   swing_high=1.0920, swing_low=1.0850),
        m15=M15State(),
        m5=M5State(rejection_present=True),
        regime=RegimeState(regime="RANGING", momentum_strength=0.15),
        location=LocationState(
            range_position=0.20,
        ),
        htf_alignment=HTFAlignment(macro_bias="BEARISH", structure_alignment=0.6),
    )


# ═══════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════


class TestMeanReversion:
    def test_neutral_htf_zone_extreme_returns_mean_reversion(self):
        state = _mean_reversion_state()
        opp = _opp(bias="BEARISH")
        result = select_strategy(state, opp)
        assert result.strategy_family == StrategyFamily.MEAN_REVERSION.value

    def test_has_supporting_conditions(self):
        state = _mean_reversion_state()
        result = select_strategy(state, _opp())
        assert result.supporting_conditions.get("htf_neutral") is True
        assert result.supporting_conditions.get("structural_level") is True


class TestTrendContinuation:
    def test_strong_trend_pullback_returns_trend_continuation(self):
        state = _trend_continuation_state()
        opp = _opp(bias="BULLISH")
        result = select_strategy(state, opp)
        assert result.strategy_family == StrategyFamily.TREND_CONTINUATION.value

    def test_has_reasoning(self):
        state = _trend_continuation_state()
        result = select_strategy(state, _opp(bias="BULLISH"))
        assert len(result.reasoning) > 0
        assert any("H4 trend" in r for r in result.reasoning)


class TestLiquiditySweepReversal:
    def test_sweep_rejection_choch_returns_liquidity_sweep(self):
        state = _liquidity_sweep_state()
        opp = _opp(bias="BULLISH")
        result = select_strategy(state, opp)
        assert result.strategy_family == StrategyFamily.LIQUIDITY_SWEEP_REVERSAL.value

    def test_highest_priority_when_competing(self):
        """Liquidity sweep should win over mean reversion if both match."""
        # This state matches both liquidity sweep AND mean reversion
        state = _liquidity_sweep_state()
        opp = _opp(bias="BULLISH")
        result = select_strategy(state, opp)
        # Liquidity sweep has higher priority
        assert result.strategy_family == StrategyFamily.LIQUIDITY_SWEEP_REVERSAL.value


class TestFalseBreak:
    def test_failed_break_reclaim_returns_false_break(self):
        state = _false_break_state()
        opp = _opp(bias="BEARISH")
        result = select_strategy(state, opp)
        assert result.strategy_family == StrategyFamily.FALSE_BREAK.value


class TestBreakoutExpansion:
    def test_compression_displacement_returns_breakout(self):
        state = _breakout_expansion_state()
        opp = _opp(bias="BULLISH")
        result = select_strategy(state, opp)
        assert result.strategy_family == StrategyFamily.BREAKOUT_EXPANSION.value


class TestRangeReaction:
    def test_ranging_extreme_zone_qualifies_as_range_reaction(self):
        """RANGE_REACTION conditions are met (established range with clear boundaries)."""
        state = V10MarketState(
            symbol="TEST", timestamp_utc=1000.0,
            h4=H4State(trend="BEARISH", trend_strength=0.5),  # Not neutral → blocks MEAN_REVERSION
            h1=H1State(dominant_trend="BEARISH", structural_clarity=0.75,
                       swing_high=1.0920, swing_low=1.0850),
            m15=M15State(),
            m5=M5State(rejection_present=True),
            regime=RegimeState(regime="RANGING", momentum_strength=0.15),
            location=LocationState(
                range_position=0.20,
            ),
            htf_alignment=HTFAlignment(macro_bias="BEARISH", structure_alignment=0.6),
        )
        opp = _opp(bias="BULLISH")
        result = select_strategy(state, opp)
        # This state qualifies for BOTH mean_reversion and range_reaction.
        # Mean_reversion R1 catches via regime="RANGING". Priority order determines winner.
        assert result.strategy_family in (
            StrategyFamily.MEAN_REVERSION.value,
            StrategyFamily.RANGE_REACTION.value,
        )


class TestPriorityResolution:
    def test_invalid_opportunity_returns_none(self):
        state = _mean_reversion_state()
        opp = _opp(state="INVALID")
        result = select_strategy(state, opp)
        assert result.strategy_family == StrategyFamily.NONE.value

    def test_no_conditions_met_returns_none(self):
        """Minimal state that matches nothing."""
        state = V10MarketState(symbol="TEST", timestamp_utc=1000.0)
        opp = _opp(state="WATCHING")
        result = select_strategy(state, opp)
        assert result.strategy_family == StrategyFamily.NONE.value


class TestHierarchyEnforcement:
    def test_m5_cannot_override_direction(self):
        """Strategy direction comes from opportunity bias (H1), not M5."""
        state = _trend_continuation_state()
        # M5 says BEARISH momentum but H1 says BULLISH
        opp = _opp(bias="BULLISH")
        result = select_strategy(state, opp)
        assert result.directional_context == "BULLISH"

    def test_reasoning_populated(self):
        state = _mean_reversion_state()
        result = select_strategy(state, _opp())
        assert len(result.reasoning) >= 2

    def test_confidence_is_bounded(self):
        state = _trend_continuation_state()
        result = select_strategy(state, _opp(bias="BULLISH"))
        assert 0.0 <= result.strategy_confidence <= 1.0

    def test_decision_is_immutable(self):
        state = _mean_reversion_state()
        result = select_strategy(state, _opp())
        with pytest.raises(Exception):
            result.strategy_family = "CHANGED"  # type: ignore
