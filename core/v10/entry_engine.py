"""V10 Entry Engine — Constructs trade plan from validated opportunity.

Consumes the full V10 pipeline context:
  - V10MarketState
  - OpportunityAssessment
  - StrategyDecision
  - HorizonDecision

Produces EntryDecision with structural stop/target placement.

Key rules:
  - Direction comes from OpportunityAssessment (H1 authority)
  - Stop comes from STRUCTURE (invalidation level)
  - Target respects HORIZON (SCALP/INTRADAY/EXTENDED)
  - Entry method determined by strategy family
  - M5 provides timing confirmation, NEVER directional authority
"""

from __future__ import annotations

from core.v10.market_state import V10MarketState
from core.v10.opportunity_assessment import OpportunityAssessment
from core.v10.strategy_family import StrategyDecision, StrategyFamily
from core.v10.horizon_assessment import HorizonDecision, HorizonType
from core.v10.entry_model import (
    EntryDecision, TradeDirection, EntryMethod, EntryStatus,
    EntryZone, StopReference, TargetReference,
)


def build_entry_decision(
    state: V10MarketState,
    opportunity: OpportunityAssessment,
    strategy: StrategyDecision,
    horizon: HorizonDecision,
) -> EntryDecision:
    """
    Build a complete trade plan from the V10 pipeline context.

    Returns INVALID if conditions don't support a viable trade.
    """
    reasoning: list[str] = []

    # ─── GATE: Only build entries for valid opportunities ─────
    if opportunity.opportunity_state == "INVALID":
        return _invalid_entry(opportunity, state, ["Opportunity is INVALID"])

    if strategy.strategy_family == StrategyFamily.NONE.value:
        return _invalid_entry(opportunity, state, ["No strategy family selected"])

    # ─── DIRECTION (from H1 authority via opportunity) ────────
    direction = _resolve_direction(opportunity)
    if direction == TradeDirection.NONE.value:
        return _invalid_entry(opportunity, state, ["No directional bias available"])
    reasoning.append(f"Direction: {direction} (from H1 structural authority)")

    # ─── ENTRY METHOD (from strategy family) ──────────────────
    entry_method = _select_entry_method(strategy, state)
    reasoning.append(f"Entry method: {entry_method} (strategy={strategy.strategy_family})")

    # ─── ENTRY PRICE / ZONE ───────────────────────────────────
    entry_price, entry_zone = _determine_entry(direction, entry_method, state)

    # ─── STOP (from structure — "what proves idea wrong?") ────
    stop = _determine_stop(direction, state)
    if stop.price == 0:
        return _invalid_entry(opportunity, state, ["No structural stop available"])
    reasoning.append(f"Stop: {stop.price:.5f} ({stop.reasoning})")

    # ─── TARGET (respects horizon) ────────────────────────────
    target = _determine_target(direction, horizon, state)
    reasoning.append(f"Target: {target.price:.5f} ({target.reasoning})")

    # ─── RISK GEOMETRY ────────────────────────────────────────
    risk_distance = abs(entry_price - stop.price) if entry_price > 0 and stop.price > 0 else 0
    reward_distance = abs(target.price - entry_price) if entry_price > 0 and target.price > 0 else 0
    expected_rr = reward_distance / risk_distance if risk_distance > 0 else 0

    # ─── VALIDATE GEOMETRY ────────────────────────────────────
    if risk_distance == 0:
        return _invalid_entry(opportunity, state, ["Zero risk distance — invalid geometry"])

    if expected_rr < 1.0:
        return _invalid_entry(opportunity, state, [f"R:R too low ({expected_rr:.2f}) — minimum 1.0"])

    # ─── ENTRY STATUS ─────────────────────────────────────────
    if entry_method == EntryMethod.CONFIRMATION_ENTRY.value:
        # Need M5 confirmation — check if present
        has_confirmation = state.m5.rejection_present or state.m5.confirmation_candle or state.m5.local_bos
        entry_status = EntryStatus.READY.value if has_confirmation else EntryStatus.WAITING.value
        if not has_confirmation:
            reasoning.append("Waiting for M5 confirmation (rejection/BOS/candle)")
    elif entry_method == EntryMethod.LIMIT_ENTRY.value:
        # Limit entry — ready if price is at zone
        entry_status = EntryStatus.READY.value if state.location.inside_institutional_zone else EntryStatus.WAITING.value
    else:
        # Break entry — check for displacement/break event
        has_break = state.m15.displacement_present or state.m5.local_bos
        entry_status = EntryStatus.READY.value if has_break else EntryStatus.WAITING.value

    reasoning.append(f"Status: {entry_status}")

    return EntryDecision(
        opportunity_id=opportunity.observation_id,
        symbol=state.symbol,
        timestamp_utc=state.timestamp_utc,
        trade_direction=direction,
        entry_method=entry_method,
        entry_status=entry_status,
        entry_price=entry_price,
        entry_zone=entry_zone,
        stop_reference=stop,
        target_reference=target,
        risk_distance=risk_distance,
        reward_distance=reward_distance,
        expected_rr=round(expected_rr, 2),
        reasoning=reasoning,
    )


# ═══════════════════════════════════════════════════════════════
# INTERNAL LOGIC
# ═══════════════════════════════════════════════════════════════


def _invalid_entry(opp: OpportunityAssessment, state: V10MarketState, reasons: list[str]) -> EntryDecision:
    return EntryDecision(
        opportunity_id=opp.observation_id,
        symbol=state.symbol,
        timestamp_utc=state.timestamp_utc,
        trade_direction=TradeDirection.NONE.value,
        entry_status=EntryStatus.INVALID.value,
        reasoning=reasons,
    )


def _resolve_direction(opp: OpportunityAssessment) -> str:
    """Direction comes from opportunity bias (H1 authority)."""
    if opp.directional_bias == "BULLISH":
        return TradeDirection.BUY.value
    elif opp.directional_bias == "BEARISH":
        return TradeDirection.SELL.value
    return TradeDirection.NONE.value


def _select_entry_method(strategy: StrategyDecision, state: V10MarketState) -> str:
    """Select entry method based on strategy family."""
    family = strategy.strategy_family

    # Break entry strategies
    if family in (StrategyFamily.FALSE_BREAK.value, StrategyFamily.BREAKOUT_EXPANSION.value):
        return EntryMethod.BREAK_ENTRY.value

    # Confirmation-required strategies
    if family == StrategyFamily.LIQUIDITY_SWEEP_REVERSAL.value:
        return EntryMethod.CONFIRMATION_ENTRY.value

    # Limit/confirmation for zone-based strategies
    if family in (StrategyFamily.MEAN_REVERSION.value, StrategyFamily.RANGE_REACTION.value):
        if state.location.inside_institutional_zone and state.location.zone_quality >= 0.7:
            return EntryMethod.LIMIT_ENTRY.value
        return EntryMethod.CONFIRMATION_ENTRY.value

    # Trend continuation: limit if at zone, else confirmation
    if family == StrategyFamily.TREND_CONTINUATION.value:
        if state.location.inside_institutional_zone:
            return EntryMethod.LIMIT_ENTRY.value
        return EntryMethod.CONFIRMATION_ENTRY.value

    return EntryMethod.CONFIRMATION_ENTRY.value


def _determine_entry(direction: str, method: str, state: V10MarketState) -> tuple[float, EntryZone]:
    """Determine entry price and zone."""
    zone = EntryZone()

    if method == EntryMethod.LIMIT_ENTRY.value:
        # Use institutional zone boundaries
        if direction == TradeDirection.BUY.value:
            # Buy at demand zone
            if state.h1.demand_ob_high > 0:
                entry_price = state.h1.demand_ob_high
                zone = EntryZone(
                    upper_bound=state.h1.demand_ob_high,
                    lower_bound=state.h1.demand_ob_low,
                    source="H1_DEMAND_OB",
                )
            elif state.m15.refined_demand_ob_high > 0:
                entry_price = state.m15.refined_demand_ob_high
                zone = EntryZone(
                    upper_bound=state.m15.refined_demand_ob_high,
                    lower_bound=state.m15.refined_demand_ob_low,
                    source="M15_DEMAND_OB",
                )
            else:
                entry_price = state.h1.swing_low if state.h1.swing_low > 0 else 0
        else:
            # Sell at supply zone
            if state.h1.supply_ob_high > 0:
                entry_price = state.h1.supply_ob_low
                zone = EntryZone(
                    upper_bound=state.h1.supply_ob_high,
                    lower_bound=state.h1.supply_ob_low,
                    source="H1_SUPPLY_OB",
                )
            elif state.m15.refined_supply_ob_high > 0:
                entry_price = state.m15.refined_supply_ob_low
                zone = EntryZone(
                    upper_bound=state.m15.refined_supply_ob_high,
                    lower_bound=state.m15.refined_supply_ob_low,
                    source="M15_SUPPLY_OB",
                )
            else:
                entry_price = state.h1.swing_high if state.h1.swing_high > 0 else 0
    else:
        # Market/confirmation entry — use current level approximation
        # (In live: would use bid/ask. In model: use midpoint of M5 context)
        if state.m5.atr > 0:
            # Approximate: midpoint of H1 swing
            if state.h1.swing_high > 0 and state.h1.swing_low > 0:
                entry_price = (state.h1.swing_high + state.h1.swing_low) / 2
            else:
                entry_price = 0
        else:
            entry_price = 0

    return entry_price, zone


def _determine_stop(direction: str, state: V10MarketState) -> StopReference:
    """Determine structural stop placement — 'what proves idea wrong?'"""
    if direction == TradeDirection.BUY.value:
        # Stop BELOW support structure
        candidates = []
        if state.h1.demand_ob_low > 0:
            candidates.append((state.h1.demand_ob_low, "below_H1_demand_OB"))
        if state.m15.refined_demand_ob_low > 0:
            candidates.append((state.m15.refined_demand_ob_low, "below_M15_demand_OB"))
        if state.h1.swing_low > 0:
            candidates.append((state.h1.swing_low, "below_H1_swing_low"))
        if state.m15.swing_low > 0:
            candidates.append((state.m15.swing_low, "below_M15_swing_low"))

        if candidates:
            # Use the tightest structural level (highest price below entry)
            best = max(candidates, key=lambda x: x[0])
            # Add buffer below
            buffer = state.m5.atr * 0.5 if state.m5.atr > 0 else 0
            return StopReference(
                price=best[0] - buffer,
                structure_source=best[1],
                reasoning=f"Below invalidation: {best[1]}",
            )
    else:
        # Stop ABOVE resistance structure
        candidates = []
        if state.h1.supply_ob_high > 0:
            candidates.append((state.h1.supply_ob_high, "above_H1_supply_OB"))
        if state.m15.refined_supply_ob_high > 0:
            candidates.append((state.m15.refined_supply_ob_high, "above_M15_supply_OB"))
        if state.h1.swing_high > 0:
            candidates.append((state.h1.swing_high, "above_H1_swing_high"))
        if state.m15.swing_high > 0:
            candidates.append((state.m15.swing_high, "above_M15_swing_high"))

        if candidates:
            # Tightest structural level (lowest price above entry)
            best = min(candidates, key=lambda x: x[0])
            buffer = state.m5.atr * 0.5 if state.m5.atr > 0 else 0
            return StopReference(
                price=best[0] + buffer,
                structure_source=best[1],
                reasoning=f"Above invalidation: {best[1]}",
            )

    return StopReference()


def _determine_target(direction: str, horizon: HorizonDecision, state: V10MarketState) -> TargetReference:
    """Determine target respecting horizon constraints."""
    horizon_type = horizon.horizon_type

    if direction == TradeDirection.BUY.value:
        # Target ABOVE — find resistance targets
        if horizon_type == HorizonType.SCALP.value:
            # Nearest opposing level
            if state.h1.supply_ob_low > 0:
                return TargetReference(state.h1.supply_ob_low, "H1_supply_zone", "Nearest supply (SCALP)")
            if state.m15.swing_high > 0:
                return TargetReference(state.m15.swing_high, "M15_swing_high", "M15 swing high (SCALP)")
        elif horizon_type == HorizonType.INTRADAY.value:
            if state.h1.swing_high > 0:
                return TargetReference(state.h1.swing_high, "H1_swing_high", "H1 swing high (INTRADAY)")
            if state.h1.session_high > 0:
                return TargetReference(state.h1.session_high, "session_high", "Session high (INTRADAY)")
        else:  # EXTENDED
            if state.h1.equal_highs_level > 0:
                return TargetReference(state.h1.equal_highs_level, "equal_highs", "Liquidity above (EXTENDED)")
            if state.h1.session_high > 0:
                return TargetReference(state.h1.session_high, "session_high", "Session high (EXTENDED)")
            if state.h4.major_liquidity_above > 0:
                return TargetReference(state.h4.major_liquidity_above, "H4_liquidity", "Major liquidity (EXTENDED)")
    else:
        # Target BELOW — find support targets
        if horizon_type == HorizonType.SCALP.value:
            if state.h1.demand_ob_high > 0:
                return TargetReference(state.h1.demand_ob_high, "H1_demand_zone", "Nearest demand (SCALP)")
            if state.m15.swing_low > 0:
                return TargetReference(state.m15.swing_low, "M15_swing_low", "M15 swing low (SCALP)")
        elif horizon_type == HorizonType.INTRADAY.value:
            if state.h1.swing_low > 0:
                return TargetReference(state.h1.swing_low, "H1_swing_low", "H1 swing low (INTRADAY)")
            if state.h1.session_low > 0:
                return TargetReference(state.h1.session_low, "session_low", "Session low (INTRADAY)")
        else:  # EXTENDED
            if state.h1.equal_lows_level > 0:
                return TargetReference(state.h1.equal_lows_level, "equal_lows", "Liquidity below (EXTENDED)")
            if state.h1.session_low > 0:
                return TargetReference(state.h1.session_low, "session_low", "Session low (EXTENDED)")
            if state.h4.major_liquidity_below > 0:
                return TargetReference(state.h4.major_liquidity_below, "H4_liquidity", "Major liquidity (EXTENDED)")

    return TargetReference()
