"""Tests for range_position evidence validity guard.

Proves:
  1. range_position == 0.0 (missing data) does NOT qualify as extreme
  2. range_position == 0.15 (legitimate discount) qualifies
  3. range_position >= 0.70 (premium) qualifies regardless
"""

import pytest
from core.v10.market_state import (
    V10MarketState, H4State, H1State, M15State, M5State,
    RegimeState, LocationState, HTFAlignment,
)
from core.v10.opportunity_assessment import OpportunityAssessment, OpportunityQuality
from core.v10.strategy_family import StrategyFamily
from core.v10.strategy_engine import select_strategy


def _opp(bias="BEARISH"):
    return OpportunityAssessment(
        observation_id="rp_guard_test", symbol="TEST", timestamp_utc=1000.0,
        opportunity_state="WATCHING", directional_bias=bias,
        quality=OpportunityQuality(overall_quality=0.5),
    )


def _mean_reversion_state(range_position=0.80):
    """MEAN_REVERSION qualifying state with configurable range_position."""
    return V10MarketState(
        symbol="TEST", timestamp_utc=1000.0,
        h4=H4State(trend="NEUTRAL", trend_strength=0.15),
        h1=H1State(structural_clarity=0.6, swing_high=1.09, swing_low=1.08),
        m5=M5State(rejection_present=True),
        regime=RegimeState(regime="RANGING", momentum_strength=0.2),
        location=LocationState(range_position=range_position),
        htf_alignment=HTFAlignment(macro_bias="NEUTRAL"),
    )


def _range_reaction_state(range_position=0.80):
    """RANGE_REACTION qualifying state with configurable range_position."""
    return V10MarketState(
        symbol="TEST", timestamp_utc=1000.0,
        h4=H4State(trend="NEUTRAL", trend_strength=0.2),
        h1=H1State(structural_clarity=0.75, swing_high=1.09, swing_low=1.08),
        m5=M5State(rejection_present=True),
        regime=RegimeState(regime="RANGING", momentum_strength=0.2),
        location=LocationState(range_position=range_position),
        htf_alignment=HTFAlignment(macro_bias="NEUTRAL"),
    )


class TestMeanReversionRangeGuard:
    """MEAN_REVERSION R2: range_position=0 must not qualify."""

    def test_zero_range_position_does_not_qualify(self):
        """range_position=0.0 (missing data) → strategy NOT selected."""
        state = _mean_reversion_state(range_position=0.0)
        result = select_strategy(state, _opp())
        assert result.strategy_family == StrategyFamily.NONE.value

    def test_legitimate_low_extreme_qualifies(self):
        """range_position=0.15 (genuine discount) → strategy selected."""
        state = _mean_reversion_state(range_position=0.15)
        result = select_strategy(state, _opp())
        assert result.strategy_family == StrategyFamily.MEAN_REVERSION.value

    def test_high_extreme_qualifies(self):
        """range_position=0.80 (premium) → strategy selected."""
        state = _mean_reversion_state(range_position=0.80)
        result = select_strategy(state, _opp())
        assert result.strategy_family == StrategyFamily.MEAN_REVERSION.value

    def test_mid_range_does_not_qualify_mean_reversion(self):
        """range_position=0.50 (equilibrium) → MEAN_REVERSION not selected (may get FALSE_BREAK)."""
        state = _mean_reversion_state(range_position=0.50)
        result = select_strategy(state, _opp())
        # Mean reversion requires extreme — 0.50 is NOT extreme
        assert result.strategy_family != StrategyFamily.MEAN_REVERSION.value

    def test_boundary_030_qualifies(self):
        """range_position=0.30 → at extreme threshold."""
        # Use state without rejection to avoid FALSE_BREAK competing
        state = V10MarketState(
            symbol="TEST", timestamp_utc=1000.0,
            h4=H4State(trend="NEUTRAL", trend_strength=0.15),
            h1=H1State(structural_clarity=0.6, swing_high=1.09, swing_low=1.08),
            m5=M5State(rejection_present=False),
            regime=RegimeState(regime="RANGING", momentum_strength=0.2),
            location=LocationState(range_position=0.30),
            htf_alignment=HTFAlignment(macro_bias="NEUTRAL"),
        )
        result = select_strategy(state, _opp())
        assert result.strategy_family == StrategyFamily.MEAN_REVERSION.value

    def test_boundary_001_qualifies(self):
        """range_position=0.01 (near zero but non-zero) → qualifies."""
        state = _mean_reversion_state(range_position=0.01)
        result = select_strategy(state, _opp())
        assert result.strategy_family == StrategyFamily.MEAN_REVERSION.value


class TestRangeReactionRangeGuard:
    """RANGE_REACTION R2: range_position=0 must not qualify."""

    def test_zero_range_position_does_not_qualify(self):
        state = _range_reaction_state(range_position=0.0)
        result = select_strategy(state, _opp())
        # Should NOT get RANGE_REACTION (or MEAN_REVERSION via same guard)
        assert result.strategy_family == StrategyFamily.NONE.value

    def test_legitimate_low_qualifies(self):
        state = _range_reaction_state(range_position=0.20)
        result = select_strategy(state, _opp())
        # May get MEAN_REVERSION or RANGE_REACTION (both qualify at 0.20)
        assert result.strategy_family in (
            StrategyFamily.MEAN_REVERSION.value,
            StrategyFamily.RANGE_REACTION.value,
        )

    def test_high_extreme_qualifies(self):
        state = _range_reaction_state(range_position=0.85)
        result = select_strategy(state, _opp())
        assert result.strategy_family in (
            StrategyFamily.MEAN_REVERSION.value,
            StrategyFamily.RANGE_REACTION.value,
        )
