"""V10 MarketState Builder — Constructs V10MarketState from existing V3 outputs.

Consumes:
  - MarketUnderstanding (core.v3_shadow.models)
  - V3MarketContext (core.v3_shadow.context_models)
  - TimeframeContext (core.v10.timeframe_context) [optional — new V10 path]

Produces:
  - V10MarketState (unified single source of truth)

Does NOT modify V3 pipeline. Runs alongside as an aggregator.
"""

from __future__ import annotations

from core.v3_shadow.models import MarketUnderstanding
from core.v3_shadow.context_models import V3MarketContext
from core.v10.market_state import (
    V10MarketState,
    H4State, H1State, M15State, M5State,
    RegimeState, LocationState, HTFAlignment,
)


def build_v10_market_state(
    understanding: MarketUnderstanding,
    context: V3MarketContext | None = None,
) -> V10MarketState:
    """
    Build a V10MarketState from existing V3 pipeline outputs.

    Args:
        understanding: Raw multi-timeframe observations
        context: Interpreted market context (optional — derived if missing)

    Returns:
        Immutable V10MarketState snapshot
    """
    # ─── H4 ────────────────────────────────────────────────────
    h4 = H4State(
        trend=understanding.h4.trend,
        trend_strength=understanding.h4.trend_strength,
        market_phase=understanding.h4.market_phase,
        structure_type=understanding.h4.structure_type,
        swing_high=understanding.h4.swing_high,
        swing_low=understanding.h4.swing_low,
        last_bos_direction=understanding.h4.last_bos_direction,
        atr=understanding.h4.atr,
        volatility_state=understanding.h4.volatility_state,
        atr_percentile=understanding.h4.atr_percentile,
        major_liquidity_above=understanding.h4.major_liquidity_above,
        major_liquidity_below=understanding.h4.major_liquidity_below,
    )

    # ─── H1 ────────────────────────────────────────────────────
    h1 = H1State(
        dominant_trend=understanding.h1.dominant_trend,
        structure_type=understanding.h1.structure_type,
        structural_clarity=understanding.h1.structural_clarity,
        bos_confirmed=understanding.h1.bos_confirmed,
        bos_direction=understanding.h1.bos_direction,
        bos_level=understanding.h1.bos_level,
        choch_detected=understanding.h1.choch_detected,
        choch_direction=understanding.h1.choch_direction,
        swing_high=understanding.h1.swing_high,
        swing_low=understanding.h1.swing_low,
        demand_ob_high=understanding.h1.active_demand_ob_high,
        demand_ob_low=understanding.h1.active_demand_ob_low,
        supply_ob_high=understanding.h1.active_supply_ob_high,
        supply_ob_low=understanding.h1.active_supply_ob_low,
        nearest_fvg_above=understanding.h1.nearest_fvg_above,
        nearest_fvg_below=understanding.h1.nearest_fvg_below,
        equal_highs_level=understanding.h1.equal_highs_level,
        equal_lows_level=understanding.h1.equal_lows_level,
        session_high=understanding.h1.session_high,
        session_low=understanding.h1.session_low,
    )

    # ─── M15 ───────────────────────────────────────────────────
    m15 = M15State(
        internal_bos=understanding.m15.internal_bos,
        internal_bos_direction=understanding.m15.internal_bos_direction,
        internal_choch=understanding.m15.internal_choch,
        pullback_active=understanding.m15.pullback_active,
        pullback_depth_atr=understanding.m15.pullback_depth_atr,
        retracement_pct=understanding.m15.retracement_pct,
        displacement_present=understanding.m15.displacement_present,
        displacement_direction=understanding.m15.expected_direction,
        displacement_magnitude_atr=understanding.m15.displacement_magnitude_atr,
        refined_demand_ob_high=understanding.m15.refined_demand_ob_high,
        refined_demand_ob_low=understanding.m15.refined_demand_ob_low,
        refined_supply_ob_high=understanding.m15.refined_supply_ob_high,
        refined_supply_ob_low=understanding.m15.refined_supply_ob_low,
        nearest_fvg=understanding.m15.nearest_fvg,
        swing_high=understanding.m15.swing_high,
        swing_low=understanding.m15.swing_low,
        range_position=understanding.m15.range_position,
    )

    # ─── M5 ────────────────────────────────────────────────────
    m5 = M5State(
        local_bos=understanding.m5.local_bos,
        local_bos_direction=understanding.m5.local_bos_direction,
        momentum_direction=understanding.m5.momentum_direction,
        momentum_strength=understanding.m5.momentum_strength,
        rejection_present=understanding.m5.rejection_present,
        rejection_direction=understanding.m5.rejection_direction,
        rejection_strength_atr=understanding.m5.rejection_strength_atr,
        at_institutional_zone=understanding.m5.at_institutional_zone,
        zone_type=understanding.m5.zone_type,
        confirmation_candle=understanding.m5.confirmation_candle,
        atr=understanding.m5.atr,
        spread=understanding.m5.spread,
        spread_atr_ratio=understanding.m5.spread_atr_ratio,
    )

    # ─── Derived layers (from V3MarketContext if available) ────
    if context:
        regime = RegimeState(
            regime=context.behaviour.regime,
            regime_confidence=context.behaviour.regime_confidence,
            volatility_state=context.behaviour.volatility_state,
            volatility_level=context.behaviour.volatility_level,
            expansion_state=context.behaviour.expansion_state,
            compression_bars=context.behaviour.compression_bars,
            momentum_direction=context.behaviour.momentum_direction,
            momentum_strength=context.behaviour.momentum_strength,
        )
        location = LocationState(
            location_type=context.location.location_type,
            inside_institutional_zone=context.location.inside_institutional_zone,
            zone_quality=context.location.zone_quality,
            zone_mitigated=context.location.zone_mitigated,
            premium_discount=context.location.premium_discount,
            range_position=context.location.range_position,
            liquidity_above=context.location.liquidity_above,
            liquidity_below=context.location.liquidity_below,
            nearest_liquidity_direction=context.location.nearest_liquidity_direction,
            nearest_liquidity_distance_pips=context.location.nearest_liquidity_distance_pips,
            demand_zones_nearby=context.location.demand_zones_nearby,
            supply_zones_nearby=context.location.supply_zones_nearby,
            fvg_zones_nearby=context.location.fvg_zones_nearby,
        )
        htf_alignment = HTFAlignment(
            macro_bias=context.htf_structure.macro_bias,
            macro_bias_strength=context.htf_structure.macro_bias_strength,
            structure_alignment=context.htf_structure.structure_alignment,
            authority_timeframe=context.htf_structure.authority_timeframe,
            phase_alignment=context.htf_structure.phase_alignment,
        )
        confidence = context.overall_confidence
    else:
        # Derive minimal regime/location from raw understanding
        regime = RegimeState(
            momentum_direction=understanding.m5.momentum_direction,
            momentum_strength=understanding.m5.momentum_strength,
        )
        location = LocationState(
            range_position=understanding.m15.range_position,
            inside_institutional_zone=understanding.m5.at_institutional_zone,
            location_type=understanding.m5.zone_type,
        )
        htf_alignment = HTFAlignment()
        confidence = understanding.confidence

    # ─── Observations ──────────────────────────────────────────
    observations = list(understanding.observations)
    if context:
        observations.extend(context.observations)

    return V10MarketState(
        symbol=understanding.symbol,
        timestamp_utc=understanding.timestamp_utc,
        h4=h4,
        h1=h1,
        m15=m15,
        m5=m5,
        regime=regime,
        location=location,
        htf_alignment=htf_alignment,
        confidence=confidence,
        observations=observations,
    )


# ═══════════════════════════════════════════════════════════════
# V10 PATH: Build from TimeframeContext (new authority model)
# ═══════════════════════════════════════════════════════════════

from core.v10.timeframe_context import TimeframeContext


def build_v10_from_timeframe_context(
    tf_ctx: TimeframeContext,
    context: V3MarketContext | None = None,
) -> V10MarketState:
    """
    Build V10MarketState from the new TimeframeContext authority model.

    This is the V10-native path. TimeframeContext enforces hierarchy;
    V3MarketContext provides supplementary regime/location data if available.
    """
    from core.v10.market_state import (
        H4State, H1State, M15State, M5State,
        RegimeState, LocationState, HTFAlignment,
    )

    h4 = H4State(
        trend=tf_ctx.h4.trend_state,
        trend_strength=tf_ctx.h4.trend_strength,
        market_phase=tf_ctx.h4.market_phase,
        structure_type=tf_ctx.h4.major_structure,
        swing_high=tf_ctx.h4.major_swing_high,
        swing_low=tf_ctx.h4.major_swing_low,
        last_bos_direction=tf_ctx.h4.last_bos_direction,
        atr=tf_ctx.h4.atr,
        volatility_state=tf_ctx.h4.volatility_state,
        atr_percentile=tf_ctx.h4.atr_percentile,
        major_liquidity_above=tf_ctx.h4.major_liquidity_above,
        major_liquidity_below=tf_ctx.h4.major_liquidity_below,
    )

    h1 = H1State(
        dominant_trend=tf_ctx.h1.structure_direction,
        structure_type=tf_ctx.h1.structure_type,
        structural_clarity=tf_ctx.h1.structural_clarity,
        bos_confirmed=tf_ctx.h1.bos_confirmed,
        bos_direction=tf_ctx.h1.bos_direction,
        choch_detected=tf_ctx.h1.choch_detected,
        choch_direction=tf_ctx.h1.choch_direction,
        swing_high=tf_ctx.h1.swing_high,
        swing_low=tf_ctx.h1.swing_low,
        demand_ob_high=tf_ctx.h1.demand_ob_high,
        demand_ob_low=tf_ctx.h1.demand_ob_low,
        supply_ob_high=tf_ctx.h1.supply_ob_high,
        supply_ob_low=tf_ctx.h1.supply_ob_low,
        nearest_fvg_above=tf_ctx.h1.nearest_fvg_above,
        nearest_fvg_below=tf_ctx.h1.nearest_fvg_below,
        equal_highs_level=tf_ctx.h1.equal_highs_level,
        equal_lows_level=tf_ctx.h1.equal_lows_level,
        session_high=tf_ctx.h1.session_high,
        session_low=tf_ctx.h1.session_low,
    )

    m15 = M15State(
        internal_bos=tf_ctx.m15.internal_bos,
        internal_bos_direction=tf_ctx.m15.internal_bos_direction,
        internal_choch=tf_ctx.m15.internal_choch,
        pullback_active=tf_ctx.m15.pullback_active,
        pullback_depth_atr=tf_ctx.m15.pullback_depth_atr,
        retracement_pct=tf_ctx.m15.retracement_pct,
        displacement_present=tf_ctx.m15.displacement_present,
        displacement_direction=tf_ctx.m15.displacement_direction,
        displacement_magnitude_atr=tf_ctx.m15.displacement_magnitude_atr,
        refined_demand_ob_high=tf_ctx.m15.refined_demand_ob_high,
        refined_demand_ob_low=tf_ctx.m15.refined_demand_ob_low,
        refined_supply_ob_high=tf_ctx.m15.refined_supply_ob_high,
        refined_supply_ob_low=tf_ctx.m15.refined_supply_ob_low,
        nearest_fvg=tf_ctx.m15.nearest_fvg,
        swing_high=tf_ctx.m15.swing_high,
        swing_low=tf_ctx.m15.swing_low,
        range_position=tf_ctx.m15.range_position,
    )

    m5 = M5State(
        local_bos=tf_ctx.m5.local_bos,
        local_bos_direction=tf_ctx.m5.local_bos_direction,
        momentum_direction=tf_ctx.m5.momentum_direction,
        momentum_strength=tf_ctx.m5.momentum_strength,
        rejection_present=tf_ctx.m5.rejection_present,
        rejection_direction=tf_ctx.m5.rejection_direction,
        rejection_strength_atr=tf_ctx.m5.rejection_strength_atr,
        at_institutional_zone=tf_ctx.m5.at_institutional_zone,
        zone_type=tf_ctx.m5.zone_type,
        confirmation_candle=tf_ctx.m5.confirmation_candle,
        atr=tf_ctx.m5.atr,
        spread=tf_ctx.m5.spread,
        spread_atr_ratio=tf_ctx.m5.spread_atr_ratio,
    )

    # Regime/location from V3MarketContext if provided
    if context:
        regime = RegimeState(
            regime=context.behaviour.regime,
            regime_confidence=context.behaviour.regime_confidence,
            volatility_state=context.behaviour.volatility_state,
            volatility_level=context.behaviour.volatility_level,
            expansion_state=context.behaviour.expansion_state,
            compression_bars=context.behaviour.compression_bars,
            momentum_direction=context.behaviour.momentum_direction,
            momentum_strength=context.behaviour.momentum_strength,
        )
        location = LocationState(
            location_type=context.location.location_type,
            inside_institutional_zone=context.location.inside_institutional_zone,
            zone_quality=context.location.zone_quality,
            zone_mitigated=context.location.zone_mitigated,
            premium_discount=context.location.premium_discount,
            range_position=context.location.range_position,
            liquidity_above=context.location.liquidity_above,
            liquidity_below=context.location.liquidity_below,
            nearest_liquidity_direction=context.location.nearest_liquidity_direction,
            nearest_liquidity_distance_pips=context.location.nearest_liquidity_distance_pips,
            demand_zones_nearby=context.location.demand_zones_nearby,
            supply_zones_nearby=context.location.supply_zones_nearby,
            fvg_zones_nearby=context.location.fvg_zones_nearby,
        )
        htf_alignment = HTFAlignment(
            macro_bias=context.htf_structure.macro_bias,
            macro_bias_strength=context.htf_structure.macro_bias_strength,
            structure_alignment=context.htf_structure.structure_alignment,
            authority_timeframe=context.htf_structure.authority_timeframe,
            phase_alignment=context.htf_structure.phase_alignment,
        )
        confidence = context.overall_confidence
    else:
        # Derive from timeframe context
        regime = RegimeState(
            regime=tf_ctx.h4.range_or_trend,
            volatility_state=tf_ctx.h4.volatility_state,
            momentum_direction=tf_ctx.m5.momentum_direction,
            momentum_strength=tf_ctx.m5.momentum_strength,
        )
        location = LocationState(
            inside_institutional_zone=tf_ctx.m5.at_institutional_zone,
            location_type=tf_ctx.m5.zone_type,
            premium_discount=tf_ctx.h1.premium_discount,
            range_position=tf_ctx.h1.range_position,
        )
        htf_alignment = HTFAlignment(
            macro_bias=tf_ctx.h4.trend_state,
            macro_bias_strength=tf_ctx.h4.trend_strength,
        )
        confidence = tf_ctx.h1.structural_clarity

    observations = list(tf_ctx.validation_notes)

    return V10MarketState(
        symbol=tf_ctx.symbol,
        timestamp_utc=tf_ctx.timestamp_utc,
        h4=h4, h1=h1, m15=m15, m5=m5,
        regime=regime, location=location, htf_alignment=htf_alignment,
        confidence=confidence,
        observations=observations,
    )
