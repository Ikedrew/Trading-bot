"""V10 Horizon Engine — Determines expected movement category.

Consumes V10MarketState + OpportunityAssessment + StrategyDecision.
Produces HorizonDecision.

Logic:
  - Strategy family suggests base horizon
  - HTF context can upgrade/downgrade
  - Volatility conditions adjust magnitude
  - Available structural space constrains targets

Does NOT create entries, stops, targets, or execution decisions.
"""

from __future__ import annotations

from core.v10.market_state import V10MarketState
from core.v10.opportunity_assessment import OpportunityAssessment
from core.v10.strategy_family import StrategyDecision, StrategyFamily
from core.v10.horizon_assessment import (
    HorizonDecision, HorizonType, MovementExpectation,
    TradeLifecycle, MeasurementUnit,
)
from core.instrument_utils import get_instrument_class, InstrumentClass


def assess_horizon(
    state: V10MarketState,
    opportunity: OpportunityAssessment,
    strategy: StrategyDecision,
) -> HorizonDecision:
    """
    Determine the expected movement horizon for the given opportunity.

    Returns an immutable HorizonDecision.
    """
    reasoning: list[str] = []
    factors: dict[str, any] = {}

    # Determine measurement unit based on instrument
    unit = _get_measurement_unit(state.symbol)
    factors["instrument_class"] = get_instrument_class(state.symbol).value
    factors["measurement_unit"] = unit

    # Base horizon from strategy family
    base_horizon = _strategy_base_horizon(strategy.strategy_family)
    factors["strategy_family"] = strategy.strategy_family
    factors["base_horizon"] = base_horizon.value
    reasoning.append(f"Strategy {strategy.strategy_family} → base {base_horizon.value}")

    # Evaluate modifiers
    htf_modifier = _evaluate_htf_support(state, reasoning, factors)
    vol_modifier = _evaluate_volatility(state, reasoning, factors)
    space_modifier = _evaluate_available_space(state, reasoning, factors)

    # Final horizon (modifiers can upgrade or downgrade)
    total_modifier = htf_modifier + vol_modifier + space_modifier
    final_horizon = _apply_modifiers(base_horizon, total_modifier)
    factors["total_modifier"] = total_modifier

    if final_horizon != base_horizon:
        reasoning.append(f"Adjusted: {base_horizon.value} → {final_horizon.value} (modifier={total_modifier:+d})")

    # Movement expectation
    movement = _calculate_movement(final_horizon, state, unit)

    # Trade lifecycle
    lifecycle = _calculate_lifecycle(final_horizon)

    return HorizonDecision(
        opportunity_id=opportunity.observation_id,
        symbol=state.symbol,
        timestamp_utc=state.timestamp_utc,
        horizon_type=final_horizon.value,
        movement_expectation=movement,
        trade_lifecycle=lifecycle,
        supporting_factors=factors,
        reasoning=reasoning,
    )


# ═══════════════════════════════════════════════════════════════
# INTERNAL LOGIC
# ═══════════════════════════════════════════════════════════════

def _get_measurement_unit(symbol: str) -> str:
    """Determine appropriate measurement unit for the instrument."""
    inst_class = get_instrument_class(symbol)
    if inst_class in (InstrumentClass.INDEX,):
        return MeasurementUnit.POINTS.value
    elif inst_class in (InstrumentClass.FX_MAJOR, InstrumentClass.FX_JPY):
        return MeasurementUnit.PIPS.value
    else:
        return MeasurementUnit.ATR_MULTIPLE.value


def _strategy_base_horizon(strategy_family: str) -> HorizonType:
    """Map strategy family to default horizon."""
    scalp_strategies = {
        StrategyFamily.MEAN_REVERSION.value,
        StrategyFamily.RANGE_REACTION.value,
    }
    intraday_strategies = {
        StrategyFamily.LIQUIDITY_SWEEP_REVERSAL.value,
        StrategyFamily.FALSE_BREAK.value,
    }
    extended_strategies = {
        StrategyFamily.TREND_CONTINUATION.value,
        StrategyFamily.BREAKOUT_EXPANSION.value,
    }

    if strategy_family in scalp_strategies:
        return HorizonType.SCALP
    elif strategy_family in intraday_strategies:
        return HorizonType.INTRADAY
    elif strategy_family in extended_strategies:
        return HorizonType.EXTENDED
    else:
        return HorizonType.SCALP


def _evaluate_htf_support(state: V10MarketState, reasoning: list[str], factors: dict) -> int:
    """HTF support can upgrade horizon. Returns modifier (-1, 0, +1)."""
    modifier = 0

    # Strong H4 trend supports extension
    if state.h4.trend in ("BULLISH", "BEARISH") and state.h4.trend_strength >= 0.6:
        modifier += 1
        factors["htf_support"] = True
        reasoning.append(f"Strong H4 trend ({state.h4.trend}, {state.h4.trend_strength:.2f}) supports extension")
    else:
        factors["htf_support"] = False

    # Weak/neutral H4 suggests shorter horizons
    if state.h4.trend_strength < 0.2:
        modifier -= 1
        reasoning.append("Weak H4 trend limits horizon")

    return modifier


def _evaluate_volatility(state: V10MarketState, reasoning: list[str], factors: dict) -> int:
    """Volatility conditions modify horizon. Returns modifier (-1, 0, +1)."""
    modifier = 0

    vol_state = state.regime.volatility_state
    factors["volatility"] = vol_state

    if vol_state == "EXPANSION":
        modifier += 1
        reasoning.append("Expanding volatility supports larger moves")
    elif vol_state == "CONTRACTION":
        # Contraction before breakout could mean extension
        if state.m15.displacement_present:
            modifier += 1
            reasoning.append("Contraction + displacement = breakout potential")
        else:
            modifier -= 1
            reasoning.append("Contraction without displacement limits movement")

    return modifier


def _evaluate_available_space(state: V10MarketState, reasoning: list[str], factors: dict) -> int:
    """Available structural space modifies horizon. Returns modifier (-1, 0, +1)."""
    modifier = 0

    # Check distance to opposing liquidity/zones
    has_space_above = state.h1.swing_high > 0 and state.h1.session_high > 0
    has_space_below = state.h1.swing_low > 0 and state.h1.session_low > 0
    factors["available_space"] = has_space_above or has_space_below

    # If liquidity is very close, cap at scalp
    liq_dist = state.location.nearest_liquidity_distance_pips
    if liq_dist > 0 and liq_dist < 10:
        modifier -= 1
        reasoning.append(f"Near liquidity ({liq_dist:.0f} pips) limits extension")
    elif liq_dist > 30:
        modifier += 1
        reasoning.append(f"Distant liquidity ({liq_dist:.0f} pips) allows extension")

    return modifier


def _apply_modifiers(base: HorizonType, modifier: int) -> HorizonType:
    """Apply modifier to upgrade/downgrade horizon (clamped to valid range)."""
    order = [HorizonType.SCALP, HorizonType.INTRADAY, HorizonType.EXTENDED]
    idx = order.index(base)
    new_idx = max(0, min(2, idx + modifier))
    return order[new_idx]


def _calculate_movement(horizon: HorizonType, state: V10MarketState, unit: str) -> MovementExpectation:
    """Calculate expected movement range for the horizon."""
    inst_class = get_instrument_class(state.symbol)

    if unit == MeasurementUnit.PIPS.value:
        # FX pip-based expectations
        if horizon == HorizonType.SCALP:
            return MovementExpectation(5.0, 20.0, unit)
        elif horizon == HorizonType.INTRADAY:
            return MovementExpectation(20.0, 50.0, unit)
        else:
            return MovementExpectation(50.0, 150.0, unit)

    elif unit == MeasurementUnit.POINTS.value:
        # Index point-based expectations
        if horizon == HorizonType.SCALP:
            return MovementExpectation(10.0, 50.0, unit)
        elif horizon == HorizonType.INTRADAY:
            return MovementExpectation(50.0, 150.0, unit)
        else:
            return MovementExpectation(150.0, 500.0, unit)

    else:
        # ATR-multiple (universal fallback)
        if horizon == HorizonType.SCALP:
            return MovementExpectation(0.5, 1.5, unit)
        elif horizon == HorizonType.INTRADAY:
            return MovementExpectation(1.5, 3.0, unit)
        else:
            return MovementExpectation(3.0, 8.0, unit)


def _calculate_lifecycle(horizon: HorizonType) -> TradeLifecycle:
    """Determine expected trade lifecycle."""
    if horizon == HorizonType.SCALP:
        return TradeLifecycle(
            expected_duration_minutes=30,
            holding_style="QUICK_REACTION",
        )
    elif horizon == HorizonType.INTRADAY:
        return TradeLifecycle(
            expected_duration_minutes=240,
            holding_style="INTRADAY_DEVELOPMENT",
        )
    else:
        return TradeLifecycle(
            expected_duration_minutes=720,
            holding_style="EXTENDED_CONTINUATION",
        )
