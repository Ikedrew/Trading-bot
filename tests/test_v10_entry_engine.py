"""Tests for V10 Entry Engine."""

import pytest
from core.v10.market_state import (
    V10MarketState, H4State, H1State, M15State, M5State,
    RegimeState, LocationState, HTFAlignment,
)
from core.v10.opportunity_assessment import OpportunityAssessment, OpportunityQuality
from core.v10.strategy_family import StrategyDecision, StrategyFamily
from core.v10.horizon_assessment import HorizonDecision, HorizonType, MovementExpectation, TradeLifecycle
from core.v10.entry_model import EntryMethod, EntryStatus, TradeDirection
from core.v10.entry_engine import build_entry_decision


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

def _opp(bias="BEARISH", state="VALID"):
    return OpportunityAssessment(
        observation_id="entry_test", symbol="EURUSD", timestamp_utc=1000.0,
        opportunity_state=state, directional_bias=bias,
    )

def _strat(family: str):
    return StrategyDecision(
        opportunity_id="entry_test", symbol="EURUSD", timestamp_utc=1000.0,
        strategy_family=family, directional_context="BEARISH",
        strategy_confidence=0.7,
    )

def _horizon(horizon_type: str = "SCALP"):
    return HorizonDecision(
        opportunity_id="entry_test", symbol="EURUSD", timestamp_utc=1000.0,
        horizon_type=horizon_type,
        movement_expectation=MovementExpectation(5.0, 20.0, "PIPS"),
        trade_lifecycle=TradeLifecycle(30, "QUICK_REACTION"),
    )

def _sell_state():
    """Complete state supporting a SELL trade with structural levels."""
    return V10MarketState(
        symbol="EURUSD", timestamp_utc=1000.0,
        h4=H4State(trend="BEARISH", trend_strength=0.6),
        h1=H1State(
            dominant_trend="BEARISH", bos_confirmed=True, bos_direction="BEARISH",
            structural_clarity=0.8,
            swing_high=1.0950, swing_low=1.0850,
            supply_ob_high=1.0920, supply_ob_low=1.0915,
            demand_ob_high=1.0870, demand_ob_low=1.0865,
            session_high=1.0960, session_low=1.0840,
            equal_lows_level=1.0845,
        ),
        m15=M15State(
            swing_high=1.0910, swing_low=1.0875,
            refined_supply_ob_high=1.0908, refined_supply_ob_low=1.0905,
            pullback_active=True,
        ),
        m5=M5State(
            atr=0.0006, spread=0.00012, spread_atr_ratio=0.2,
            rejection_present=True, rejection_direction="BEARISH",
            rejection_strength_atr=0.9, confirmation_candle=True,
            at_institutional_zone=True, zone_type="SUPPLY_OB",
        ),
        regime=RegimeState(regime="RANGING", volatility_state="NEUTRAL"),
        location=LocationState(
            inside_institutional_zone=True, zone_quality=0.8,
            location_type="SUPPLY_OB", range_position=0.75,
        ),
        htf_alignment=HTFAlignment(macro_bias="BEARISH", structure_alignment=0.8),
    )

def _buy_state():
    """Complete state supporting a BUY trade."""
    return V10MarketState(
        symbol="EURUSD", timestamp_utc=1000.0,
        h4=H4State(trend="BULLISH", trend_strength=0.6),
        h1=H1State(
            dominant_trend="BULLISH", bos_confirmed=True, bos_direction="BULLISH",
            structural_clarity=0.75,
            swing_high=1.0950, swing_low=1.0850,
            demand_ob_high=1.0870, demand_ob_low=1.0865,
            supply_ob_high=1.0940, supply_ob_low=1.0935,
            session_high=1.0960, session_low=1.0840,
        ),
        m15=M15State(
            swing_high=1.0910, swing_low=1.0860,
            refined_demand_ob_high=1.0868, refined_demand_ob_low=1.0863,
            pullback_active=True,
        ),
        m5=M5State(
            atr=0.0006, spread=0.00012,
            rejection_present=True, rejection_direction="BULLISH",
            at_institutional_zone=True, zone_type="DEMAND_OB",
        ),
        regime=RegimeState(regime="TRENDING"),
        location=LocationState(
            inside_institutional_zone=True, zone_quality=0.75,
            location_type="DEMAND_OB", range_position=0.30,
        ),
        htf_alignment=HTFAlignment(macro_bias="BULLISH", structure_alignment=0.7),
    )


# ═══════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════


class TestTrendContinuation:
    def test_returns_limit_or_confirmation(self):
        state = _buy_state()
        opp = _opp(bias="BULLISH")
        strat = _strat(StrategyFamily.TREND_CONTINUATION.value)
        hz = _horizon("INTRADAY")
        result = build_entry_decision(state, opp, strat, hz)
        assert result.entry_method in (EntryMethod.CONFIRMATION_ENTRY.value, EntryMethod.LIMIT_ENTRY.value)
        assert result.trade_direction == TradeDirection.BUY.value


class TestLiquiditySweep:
    def test_requires_confirmation(self):
        state = _sell_state()
        opp = _opp(bias="BEARISH")
        strat = _strat(StrategyFamily.LIQUIDITY_SWEEP_REVERSAL.value)
        hz = _horizon("INTRADAY")
        result = build_entry_decision(state, opp, strat, hz)
        assert result.entry_method == EntryMethod.CONFIRMATION_ENTRY.value


class TestFalseBreak:
    def test_uses_break_entry(self):
        state = _sell_state()
        opp = _opp(bias="BEARISH")
        strat = _strat(StrategyFamily.FALSE_BREAK.value)
        hz = _horizon("INTRADAY")
        result = build_entry_decision(state, opp, strat, hz)
        assert result.entry_method == EntryMethod.BREAK_ENTRY.value


class TestBreakoutExpansion:
    def test_uses_break_entry(self):
        state = _sell_state()  # Has full structural levels
        opp = _opp(bias="BEARISH")
        strat = _strat(StrategyFamily.BREAKOUT_EXPANSION.value)
        hz = _horizon("EXTENDED")
        result = build_entry_decision(state, opp, strat, hz)
        assert result.entry_method == EntryMethod.BREAK_ENTRY.value


class TestMeanReversion:
    def test_can_use_limit_from_zone(self):
        state = _sell_state()
        opp = _opp(bias="BEARISH")
        strat = _strat(StrategyFamily.MEAN_REVERSION.value)
        hz = _horizon("SCALP")
        result = build_entry_decision(state, opp, strat, hz)
        # Inside zone with quality >= 0.7 → LIMIT_ENTRY
        assert result.entry_method == EntryMethod.LIMIT_ENTRY.value


class TestDirectionAuthority:
    def test_m5_cannot_create_trade_without_htf(self):
        """Invalid opportunity = no entry regardless of M5 state."""
        state = _sell_state()
        opp = _opp(bias="BEARISH", state="INVALID")
        strat = _strat(StrategyFamily.MEAN_REVERSION.value)
        hz = _horizon("SCALP")
        result = build_entry_decision(state, opp, strat, hz)
        assert result.entry_status == EntryStatus.INVALID.value

    def test_direction_from_opportunity_not_m5(self):
        """Direction comes from opportunity bias, not M5 momentum."""
        state = _sell_state()
        # M5 has BEARISH rejection but opportunity says BULLISH
        opp = _opp(bias="BULLISH")
        strat = _strat(StrategyFamily.TREND_CONTINUATION.value)
        hz = _horizon("INTRADAY")
        result = build_entry_decision(state, opp, strat, hz)
        assert result.trade_direction == TradeDirection.BUY.value


class TestStopPlacement:
    def test_sell_stop_uses_structural_invalidation(self):
        state = _sell_state()
        opp = _opp(bias="BEARISH")
        strat = _strat(StrategyFamily.MEAN_REVERSION.value)
        hz = _horizon("SCALP")
        result = build_entry_decision(state, opp, strat, hz)
        # Stop should be above supply structure
        assert result.stop_reference.price > 0
        assert "above" in result.stop_reference.structure_source.lower() or "above" in result.stop_reference.reasoning.lower()

    def test_buy_stop_below_structure(self):
        state = _buy_state()
        opp = _opp(bias="BULLISH")
        strat = _strat(StrategyFamily.TREND_CONTINUATION.value)
        hz = _horizon("INTRADAY")
        result = build_entry_decision(state, opp, strat, hz)
        assert result.stop_reference.price > 0
        assert "below" in result.stop_reference.structure_source.lower() or "below" in result.stop_reference.reasoning.lower()


class TestTargetRespect:
    def test_scalp_uses_nearby_target(self):
        state = _sell_state()
        opp = _opp(bias="BEARISH")
        strat = _strat(StrategyFamily.MEAN_REVERSION.value)
        hz = _horizon("SCALP")
        result = build_entry_decision(state, opp, strat, hz)
        # SCALP target should be nearby demand zone or M15 swing
        assert result.target_reference.price > 0

    def test_rr_calculates_correctly(self):
        state = _sell_state()
        opp = _opp(bias="BEARISH")
        strat = _strat(StrategyFamily.MEAN_REVERSION.value)
        hz = _horizon("SCALP")
        result = build_entry_decision(state, opp, strat, hz)
        if result.entry_status != EntryStatus.INVALID.value:
            assert result.expected_rr >= 1.0
            assert result.risk_distance > 0
            assert result.reward_distance > 0


class TestInvalidCases:
    def test_invalid_opportunity_produces_invalid(self):
        state = _sell_state()
        opp = _opp(state="INVALID")
        result = build_entry_decision(state, opp, _strat(StrategyFamily.NONE.value), _horizon())
        assert result.entry_status == EntryStatus.INVALID.value

    def test_no_strategy_produces_invalid(self):
        state = _sell_state()
        opp = _opp()
        strat = _strat(StrategyFamily.NONE.value)
        result = build_entry_decision(state, opp, strat, _horizon())
        assert result.entry_status == EntryStatus.INVALID.value

    def test_immutable(self):
        state = _sell_state()
        result = build_entry_decision(state, _opp(), _strat(StrategyFamily.MEAN_REVERSION.value), _horizon())
        with pytest.raises(Exception):
            result.entry_status = "CHANGED"  # type: ignore
