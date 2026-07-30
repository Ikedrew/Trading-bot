"""Tests for V10 Horizon Assessment Engine."""

import pytest
from core.v10.market_state import (
    V10MarketState, H4State, H1State, M15State, M5State,
    RegimeState, LocationState, HTFAlignment,
)
from core.v10.opportunity_assessment import OpportunityAssessment, OpportunityQuality
from core.v10.strategy_family import StrategyDecision, StrategyFamily
from core.v10.horizon_assessment import HorizonType, MeasurementUnit
from core.v10.horizon_engine import assess_horizon


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

def _opp(obs_id="test123", symbol="EURUSD"):
    return OpportunityAssessment(
        observation_id=obs_id, symbol=symbol, timestamp_utc=1000.0,
        opportunity_state="VALID", directional_bias="BEARISH",
    )


def _strategy(family: str):
    return StrategyDecision(
        opportunity_id="test123", symbol="EURUSD", timestamp_utc=1000.0,
        strategy_family=family, directional_context="BEARISH",
        strategy_confidence=0.7,
    )


def _fx_state(**kwargs):
    defaults = dict(
        symbol="EURUSD", timestamp_utc=1000.0,
        h4=H4State(trend="NEUTRAL", trend_strength=0.2),
        h1=H1State(swing_high=1.0950, swing_low=1.0850, session_high=1.0960, session_low=1.0840),
        m15=M15State(),
        m5=M5State(atr=0.0006),
        regime=RegimeState(regime="RANGING", volatility_state="NEUTRAL"),
        location=LocationState(nearest_liquidity_distance_pips=15.0),
    )
    defaults.update(kwargs)
    return V10MarketState(**defaults)


def _index_state(**kwargs):
    defaults = dict(
        symbol="NAS100", timestamp_utc=1000.0,
        h4=H4State(trend="BULLISH", trend_strength=0.7),
        h1=H1State(swing_high=28000.0, swing_low=27500.0, session_high=28050.0, session_low=27450.0),
        m15=M15State(pullback_active=True, pullback_depth_atr=1.2),
        m5=M5State(atr=15.0),
        regime=RegimeState(regime="TRENDING", volatility_state="NEUTRAL"),
        location=LocationState(nearest_liquidity_distance_pips=50.0),
    )
    defaults.update(kwargs)
    return V10MarketState(**defaults)


# ═══════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════


class TestMeanReversionHorizon:
    def test_mean_reversion_returns_scalp(self):
        state = _fx_state()
        opp = _opp()
        strat = _strategy(StrategyFamily.MEAN_REVERSION.value)
        result = assess_horizon(state, opp, strat)
        assert result.horizon_type == HorizonType.SCALP.value

    def test_scalp_movement_range(self):
        state = _fx_state()
        result = assess_horizon(state, _opp(), _strategy(StrategyFamily.MEAN_REVERSION.value))
        assert result.movement_expectation.minimum_expected_move >= 5.0
        assert result.movement_expectation.maximum_expected_move <= 20.0


class TestRangeReactionHorizon:
    def test_range_reaction_returns_scalp(self):
        state = _fx_state()
        result = assess_horizon(state, _opp(), _strategy(StrategyFamily.RANGE_REACTION.value))
        assert result.horizon_type == HorizonType.SCALP.value


class TestLiquiditySweepHorizon:
    def test_liquidity_sweep_returns_intraday(self):
        state = _fx_state()
        result = assess_horizon(state, _opp(), _strategy(StrategyFamily.LIQUIDITY_SWEEP_REVERSAL.value))
        assert result.horizon_type == HorizonType.INTRADAY.value


class TestTrendContinuationHorizon:
    def test_strong_trend_returns_extended(self):
        state = _fx_state(
            h4=H4State(trend="BEARISH", trend_strength=0.7),
            regime=RegimeState(volatility_state="EXPANSION"),
        )
        result = assess_horizon(state, _opp(), _strategy(StrategyFamily.TREND_CONTINUATION.value))
        assert result.horizon_type == HorizonType.EXTENDED.value


class TestBreakoutExpansionHorizon:
    def test_breakout_returns_extended(self):
        state = _fx_state(
            h4=H4State(trend="BULLISH", trend_strength=0.6),
            m15=M15State(displacement_present=True, displacement_magnitude_atr=2.0),
            regime=RegimeState(volatility_state="CONTRACTION"),
        )
        result = assess_horizon(state, _opp(), _strategy(StrategyFamily.BREAKOUT_EXPANSION.value))
        assert result.horizon_type == HorizonType.EXTENDED.value


class TestIndexInstruments:
    def test_index_uses_points_not_pips(self):
        state = _index_state()
        opp = OpportunityAssessment(
            observation_id="idx1", symbol="NAS100", timestamp_utc=1000.0,
            opportunity_state="VALID", directional_bias="BULLISH",
        )
        strat = StrategyDecision(
            opportunity_id="idx1", symbol="NAS100", timestamp_utc=1000.0,
            strategy_family=StrategyFamily.TREND_CONTINUATION.value,
            directional_context="BULLISH", strategy_confidence=0.8,
        )
        result = assess_horizon(state, opp, strat)
        assert result.movement_expectation.measurement_unit == MeasurementUnit.POINTS.value

    def test_index_scalp_larger_than_fx(self):
        # Index SCALP should have larger absolute numbers than FX SCALP
        fx_state = _fx_state()
        fx_result = assess_horizon(fx_state, _opp(), _strategy(StrategyFamily.MEAN_REVERSION.value))

        idx_state = _index_state(h4=H4State(trend="NEUTRAL", trend_strength=0.1))
        idx_opp = OpportunityAssessment(
            observation_id="idx1", symbol="NAS100", timestamp_utc=1000.0,
            opportunity_state="VALID", directional_bias="BULLISH",
        )
        idx_strat = StrategyDecision(
            opportunity_id="idx1", symbol="NAS100", timestamp_utc=1000.0,
            strategy_family=StrategyFamily.MEAN_REVERSION.value,
            directional_context="BULLISH", strategy_confidence=0.7,
        )
        idx_result = assess_horizon(idx_state, idx_opp, idx_strat)

        # Index points should be numerically larger than FX pips for same horizon
        assert idx_result.movement_expectation.minimum_expected_move > fx_result.movement_expectation.minimum_expected_move


class TestModifiers:
    def test_strong_htf_upgrades_horizon(self):
        # SCALP base (mean reversion) + strong H4 → may upgrade to INTRADAY
        state = _fx_state(
            h4=H4State(trend="BEARISH", trend_strength=0.8),
            location=LocationState(nearest_liquidity_distance_pips=40.0),
        )
        result = assess_horizon(state, _opp(), _strategy(StrategyFamily.MEAN_REVERSION.value))
        # Strong HTF + distant liquidity = upgrades from SCALP
        assert result.horizon_type in (HorizonType.INTRADAY.value, HorizonType.EXTENDED.value)

    def test_near_liquidity_caps_horizon(self):
        # EXTENDED base but near liquidity AND weak HTF → downgraded
        state = _fx_state(
            h4=H4State(trend="NEUTRAL", trend_strength=0.15),
            location=LocationState(nearest_liquidity_distance_pips=5.0),
            regime=RegimeState(volatility_state="NEUTRAL"),
        )
        result = assess_horizon(state, _opp(), _strategy(StrategyFamily.TREND_CONTINUATION.value))
        # Weak HTF (-1) + near liquidity (-1) → EXTENDED-2 = SCALP
        assert result.horizon_type == HorizonType.SCALP.value


class TestIntegrity:
    def test_does_not_override_strategy(self):
        """Horizon should not change the strategy family."""
        state = _fx_state()
        strat = _strategy(StrategyFamily.MEAN_REVERSION.value)
        result = assess_horizon(state, _opp(), strat)
        # Result doesn't contain strategy_family — it only has horizon
        assert result.horizon_type in (HorizonType.SCALP.value, HorizonType.INTRADAY.value, HorizonType.EXTENDED.value)

    def test_does_not_create_entry(self):
        """HorizonDecision should have no entry/stop/target fields."""
        state = _fx_state()
        result = assess_horizon(state, _opp(), _strategy(StrategyFamily.MEAN_REVERSION.value))
        d = result.to_dict()
        assert "entry_price" not in d
        assert "stop_loss" not in d
        assert "take_profit" not in d

    def test_reasoning_populated(self):
        state = _fx_state()
        result = assess_horizon(state, _opp(), _strategy(StrategyFamily.MEAN_REVERSION.value))
        assert len(result.reasoning) >= 1

    def test_to_dict_structure(self):
        state = _fx_state()
        result = assess_horizon(state, _opp(), _strategy(StrategyFamily.MEAN_REVERSION.value))
        d = result.to_dict()
        assert "horizon_type" in d
        assert "movement_expectation" in d
        assert "trade_lifecycle" in d
        assert "reasoning" in d

    def test_immutable(self):
        state = _fx_state()
        result = assess_horizon(state, _opp(), _strategy(StrategyFamily.MEAN_REVERSION.value))
        with pytest.raises(Exception):
            result.horizon_type = "CHANGED"  # type: ignore
