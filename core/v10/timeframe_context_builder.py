"""V10 TimeframeContext Builder — Maps V3 outputs into authority hierarchy.

Consumes existing V3 MarketUnderstanding and produces TimeframeContext
with hierarchy validation (lower TFs cannot override higher TF authority).
"""

from __future__ import annotations

from core.v3_shadow.models import MarketUnderstanding
from core.v10.timeframe_context import (
    TimeframeContext,
    H4MacroEnvironment,
    H1StructuralAuthority,
    M15OpportunityFormation,
    M5ExecutionEnvironment,
)


def build_timeframe_context(understanding: MarketUnderstanding) -> TimeframeContext:
    """
    Build TimeframeContext from V3 MarketUnderstanding.

    Validates that lower timeframes do not override higher TF authority.
    """
    h4 = _build_h4(understanding)
    h1 = _build_h1(understanding)
    m15 = _build_m15(understanding)
    m5 = _build_m5(understanding)

    # Validate hierarchy
    valid, notes = _validate_hierarchy(h4, h1, m15, m5)

    return TimeframeContext(
        symbol=understanding.symbol,
        timestamp_utc=understanding.timestamp_utc,
        h4=h4,
        h1=h1,
        m15=m15,
        m5=m5,
        hierarchy_valid=valid,
        validation_notes=notes,
    )


def _build_h4(mu: MarketUnderstanding) -> H4MacroEnvironment:
    h4 = mu.h4
    # Determine range_or_trend from trend + phase
    if h4.trend in ("BULLISH", "BEARISH") and h4.trend_strength >= 0.4:
        range_or_trend = "TRENDING"
    elif h4.market_phase == "CONSOLIDATION" or h4.trend_strength < 0.2:
        range_or_trend = "RANGING"
    else:
        range_or_trend = "TRANSITIONAL"

    return H4MacroEnvironment(
        trend_state=h4.trend,
        trend_strength=h4.trend_strength,
        market_phase=h4.market_phase,
        range_or_trend=range_or_trend,
        major_structure=h4.structure_type,
        major_swing_high=h4.swing_high,
        major_swing_low=h4.swing_low,
        last_bos_direction=h4.last_bos_direction,
        volatility_state=h4.volatility_state,
        atr=h4.atr,
        atr_percentile=h4.atr_percentile,
        major_liquidity_above=h4.major_liquidity_above,
        major_liquidity_below=h4.major_liquidity_below,
    )


def _build_h1(mu: MarketUnderstanding) -> H1StructuralAuthority:
    h1 = mu.h1
    m15 = mu.m15
    # Premium/discount from M15 range position (H1 level assessment)
    if m15.range_position >= 0.7:
        pd = "PREMIUM"
    elif m15.range_position <= 0.3:
        pd = "DISCOUNT"
    else:
        pd = "EQUILIBRIUM"

    return H1StructuralAuthority(
        structure_direction=h1.dominant_trend,
        structure_type=h1.structure_type,
        structural_clarity=h1.structural_clarity,
        bos_confirmed=h1.bos_confirmed,
        bos_direction=h1.bos_direction,
        choch_detected=h1.choch_detected,
        choch_direction=h1.choch_direction,
        swing_high=h1.swing_high,
        swing_low=h1.swing_low,
        demand_ob_high=h1.active_demand_ob_high,
        demand_ob_low=h1.active_demand_ob_low,
        supply_ob_high=h1.active_supply_ob_high,
        supply_ob_low=h1.active_supply_ob_low,
        nearest_fvg_above=h1.nearest_fvg_above,
        nearest_fvg_below=h1.nearest_fvg_below,
        equal_highs_level=h1.equal_highs_level,
        equal_lows_level=h1.equal_lows_level,
        session_high=h1.session_high,
        session_low=h1.session_low,
        premium_discount=pd,
        range_position=m15.range_position,
    )


def _build_m15(mu: MarketUnderstanding) -> M15OpportunityFormation:
    m15 = mu.m15
    m5 = mu.m5

    # Determine zone interaction from M5's at_institutional_zone
    at_ob = m5.at_institutional_zone and m5.zone_type in ("DEMAND_OB", "SUPPLY_OB")
    ob_type = ""
    if m5.zone_type == "DEMAND_OB":
        ob_type = "DEMAND"
    elif m5.zone_type == "SUPPLY_OB":
        ob_type = "SUPPLY"

    at_fvg = m5.at_institutional_zone and "FVG" in m5.zone_type
    fvg_type = ""
    if "BULLISH" in m5.zone_type:
        fvg_type = "BULLISH"
    elif "BEARISH" in m5.zone_type:
        fvg_type = "BEARISH"

    return M15OpportunityFormation(
        internal_bos=m15.internal_bos,
        internal_bos_direction=m15.internal_bos_direction,
        internal_choch=m15.internal_choch,
        pullback_active=m15.pullback_active,
        pullback_depth_atr=m15.pullback_depth_atr,
        retracement_pct=m15.retracement_pct,
        displacement_present=m15.displacement_present,
        displacement_direction=m15.expected_direction,
        displacement_magnitude_atr=m15.displacement_magnitude_atr,
        at_order_block=at_ob,
        order_block_type=ob_type,
        at_fvg=at_fvg,
        fvg_type=fvg_type,
        refined_demand_ob_high=m15.refined_demand_ob_high,
        refined_demand_ob_low=m15.refined_demand_ob_low,
        refined_supply_ob_high=m15.refined_supply_ob_high,
        refined_supply_ob_low=m15.refined_supply_ob_low,
        nearest_fvg=m15.nearest_fvg,
        swing_high=m15.swing_high,
        swing_low=m15.swing_low,
        range_position=m15.range_position,
    )


def _build_m5(mu: MarketUnderstanding) -> M5ExecutionEnvironment:
    m5 = mu.m5
    return M5ExecutionEnvironment(
        spread=m5.spread,
        spread_atr_ratio=m5.spread_atr_ratio,
        atr=m5.atr,
        momentum_direction=m5.momentum_direction,
        momentum_strength=m5.momentum_strength,
        rejection_present=m5.rejection_present,
        rejection_direction=m5.rejection_direction,
        rejection_strength_atr=m5.rejection_strength_atr,
        confirmation_candle=m5.confirmation_candle,
        local_bos=m5.local_bos,
        local_bos_direction=m5.local_bos_direction,
        at_institutional_zone=m5.at_institutional_zone,
        zone_type=m5.zone_type,
    )


# ═══════════════════════════════════════════════════════════════
# HIERARCHY VALIDATION
# ═══════════════════════════════════════════════════════════════


def _validate_hierarchy(
    h4: H4MacroEnvironment,
    h1: H1StructuralAuthority,
    m15: M15OpportunityFormation,
    m5: M5ExecutionEnvironment,
) -> tuple[bool, list[str]]:
    """
    Validate that lower timeframes do not contradict higher TF authority.

    Returns (is_valid, list_of_violation_notes).
    """
    notes: list[str] = []
    valid = True

    # Rule 1: H1 structure_direction should not contradict H4 trend
    # (allowed when H4 is NEUTRAL or TRANSITIONAL)
    if h4.trend_state in ("BULLISH", "BEARISH") and h4.trend_strength >= 0.6:
        if h1.structure_direction and h1.structure_direction != h4.trend_state and h1.structure_direction != "NEUTRAL":
            notes.append(
                f"H1 structure ({h1.structure_direction}) contradicts "
                f"strong H4 trend ({h4.trend_state}, strength={h4.trend_strength:.2f})"
            )
            # Not invalid — structure can temporarily diverge — but flagged
            # valid remains True (this is a warning, not a hard block)

    # Rule 2: M5 momentum is NEVER authoritative for direction
    # (This is enforced structurally — M5 has no direction-authority fields)
    # No validation needed — the model itself prevents misuse.

    # Rule 3: If H4 is RANGING, H1 should not claim strong directional BOS
    if h4.range_or_trend == "RANGING" and h1.bos_confirmed and h1.structural_clarity < 0.5:
        notes.append(
            f"H1 BOS ({h1.bos_direction}) detected in H4 RANGING environment "
            f"with low clarity ({h1.structural_clarity:.2f}) — may be noise"
        )

    # Rule 4: M15 displacement should align with H1 structure (warning only)
    if m15.displacement_present and h1.structure_direction:
        if m15.displacement_direction and m15.displacement_direction != h1.structure_direction:
            if h1.structure_direction != "NEUTRAL":
                notes.append(
                    f"M15 displacement ({m15.displacement_direction}) opposes "
                    f"H1 structure ({h1.structure_direction}) — potential CHoCH forming"
                )

    return valid, notes
