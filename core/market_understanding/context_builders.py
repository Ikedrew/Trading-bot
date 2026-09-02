"""
Market Context Builders — Transform MarketUnderstanding into structured context (V1).

Three independent builders + orchestrator:
    build_htf_structure_context() → HTFStructureContext
    build_location_context() → LocationContext
    build_behaviour_context() → BehaviourContext
    build_market_context_interpretation() → MarketContextInterpretation (orchestrates all)

Each builder has ONE responsibility: interpret one aspect of the market.
No builder knows about execution, risk, entries, or strategy.
"""

from __future__ import annotations

import logging
from typing import Any

from core.market_understanding.models import MarketUnderstanding
from core.market_understanding.context_models import (
    HTFStructureContext,
    LocationContext,
    BehaviourContext,
    MarketContextInterpretation,
    _CONTEXT_SCHEMA_VERSION,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. HTF STRUCTURE BUILDER
# ═══════════════════════════════════════════════════════════════════════════════


def build_htf_structure_context(mu: MarketUnderstanding) -> HTFStructureContext:
    """
    Interpret higher timeframe structural authority from MarketUnderstanding.

    Determines: macro bias, dominant structure, authority timeframe, alignment.
    """
    h4 = mu.h4
    h1 = mu.h1
    m15 = mu.m15
    observations: list[str] = []

    # ─── Macro bias (H4 + H1 agreement) ──────────────────────────────
    h4_direction = h4.trend.upper() if h4.trend else ""
    h1_direction = h1.dominant_trend.upper() if h1.dominant_trend else ""

    if h4_direction and h1_direction:
        if h4_direction == h1_direction and h4_direction in ("BULLISH", "BEARISH"):
            macro_bias = h4_direction
            macro_strength = min(1.0, (h4.trend_strength + h1.structural_clarity) / 2)
            observations.append(f"H4+H1 agree: {macro_bias}")
        elif h4_direction == "NEUTRAL":
            macro_bias = h1_direction if h1_direction in ("BULLISH", "BEARISH") else "NEUTRAL"
            macro_strength = h1.structural_clarity * 0.7
        else:
            macro_bias = "CONFLICTED"
            macro_strength = 0.3
            observations.append(f"Conflict: H4={h4_direction} vs H1={h1_direction}")
    elif h1_direction in ("BULLISH", "BEARISH"):
        macro_bias = h1_direction
        macro_strength = h1.structural_clarity * 0.6
    else:
        macro_bias = "NEUTRAL"
        macro_strength = 0.2

    # ─── Dominant structure ───────────────────────────────────────────
    dominant_structure = h1.structure_type or h4.structure_type or "MIXED"

    # ─── Authority timeframe ──────────────────────────────────────────
    if h4.trend and h4.trend != "NEUTRAL" and h4.trend_strength > 0.5:
        authority_tf = "H4"
    elif h1.bos_confirmed:
        authority_tf = "H1"
    elif m15.swing_high > 0 and m15.swing_low > 0:
        authority_tf = "M15"
    else:
        authority_tf = "UNKNOWN"

    # ─── BOS / CHOCH ─────────────────────────────────────────────────
    bos_active = h1.bos_confirmed
    bos_direction = h1.bos_direction
    choch_active = False  # CHOCH detection not yet in H1Understanding
    choch_direction = ""

    if bos_active:
        observations.append(f"H1 BOS: {bos_direction}")

    # ─── Phase alignment ─────────────────────────────────────────────
    if h4.market_phase and h1.dominant_trend:
        if h4.market_phase in ("IMPULSE",) and h1.dominant_trend in ("BULLISH", "BEARISH"):
            phase_alignment = "ALIGNED"
        elif h4.market_phase in ("CONSOLIDATION", "DISTRIBUTION"):
            phase_alignment = "NEUTRAL"
        else:
            phase_alignment = "CONFLICTED" if macro_bias == "CONFLICTED" else "NEUTRAL"
    else:
        phase_alignment = "NEUTRAL"

    # ─── Structure alignment score ────────────────────────────────────
    alignment_score = 0.0
    if h1.bos_confirmed:
        alignment_score += 0.3
    if dominant_structure in ("HH_HL", "LH_LL"):
        alignment_score += 0.3
    if macro_bias in ("BULLISH", "BEARISH"):
        alignment_score += 0.2
    if phase_alignment == "ALIGNED":
        alignment_score += 0.2
    alignment_score = min(1.0, alignment_score)

    # ─── Confidence ───────────────────────────────────────────────────
    confidence = 0.0
    if h1.swing_high > 0:
        confidence += 0.3
    if h4.trend:
        confidence += 0.3
    if h1.bos_confirmed:
        confidence += 0.2
    if h1.structural_clarity > 0.5:
        confidence += 0.2
    confidence = min(1.0, confidence)

    return HTFStructureContext(
        macro_bias=macro_bias,
        macro_bias_strength=round(macro_strength, 4),
        dominant_structure=dominant_structure,
        authority_timeframe=authority_tf,
        bos_active=bos_active,
        bos_direction=bos_direction,
        choch_active=choch_active,
        choch_direction=choch_direction,
        phase_alignment=phase_alignment,
        structure_alignment=round(alignment_score, 4),
        confidence=round(confidence, 4),
        observations=observations,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LOCATION BUILDER
# ═══════════════════════════════════════════════════════════════════════════════


def build_location_context(mu: MarketUnderstanding) -> LocationContext:
    """
    Interpret market location from MarketUnderstanding.

    Determines: zone type, premium/discount, institutional alignment, liquidity state.
    """
    m5 = mu.m5
    m15 = mu.m15
    h1 = mu.h1
    observations: list[str] = []

    # ─── Zone positioning ─────────────────────────────────────────────
    inside_zone = m5.at_institutional_zone
    location_type = m5.zone_type if inside_zone else "OPEN_SPACE"

    if inside_zone:
        observations.append(f"Inside: {location_type}")

    # ─── Premium / Discount ───────────────────────────────────────────
    range_pos = m15.range_position
    if range_pos > 0:
        if range_pos < 0.33:
            premium_discount = "DISCOUNT"
        elif range_pos > 0.67:
            premium_discount = "PREMIUM"
        else:
            premium_discount = "EQUILIBRIUM"
        observations.append(f"Position: {premium_discount} ({range_pos:.2f})")
    else:
        premium_discount = ""

    # ─── Institutional alignment ──────────────────────────────────────
    # BULLISH alignment: at demand zone in discount
    # BEARISH alignment: at supply zone in premium
    institutional_alignment = "NEUTRAL"
    if inside_zone:
        if "DEMAND" in location_type and premium_discount == "DISCOUNT":
            institutional_alignment = "BULLISH"
        elif "SUPPLY" in location_type and premium_discount == "PREMIUM":
            institutional_alignment = "BEARISH"
        elif "DEMAND" in location_type:
            institutional_alignment = "BULLISH"
        elif "SUPPLY" in location_type:
            institutional_alignment = "BEARISH"

    if institutional_alignment != "NEUTRAL":
        observations.append(f"Institutional: {institutional_alignment}")

    # ─── Zone quality ─────────────────────────────────────────────────
    zone_quality = 0.0
    zone_mitigated = False
    if inside_zone:
        # Higher quality for unmitigated zones in discount/premium areas
        zone_quality = 0.5
        if premium_discount in ("DISCOUNT", "PREMIUM"):
            zone_quality += 0.2
        if m15.displacement_present:
            zone_quality += 0.15
        if m5.rejection_present:
            zone_quality += 0.15
        zone_quality = min(1.0, zone_quality)

    # ─── Liquidity context ────────────────────────────────────────────
    liquidity_above = h1.equal_highs_level > 0 or h1.session_high > 0
    liquidity_below = h1.equal_lows_level > 0 or h1.session_low > 0

    if liquidity_above and liquidity_below:
        nearest_direction = "ABOVE"  # Default to above target
    elif liquidity_above:
        nearest_direction = "ABOVE"
    elif liquidity_below:
        nearest_direction = "BELOW"
    else:
        nearest_direction = ""

    if liquidity_above:
        observations.append("Liquidity above")
    if liquidity_below:
        observations.append("Liquidity below")

    # ─── Zone counts ──────────────────────────────────────────────────
    demand_nearby = 1 if m15.refined_demand_ob_high > 0 else 0
    supply_nearby = 1 if m15.refined_supply_ob_high > 0 else 0
    fvg_nearby = 1 if m15.nearest_fvg > 0 else 0

    # ─── Confidence ───────────────────────────────────────────────────
    confidence = 0.0
    if m15.swing_high > 0 and m15.swing_low > 0:
        confidence += 0.3
    if inside_zone:
        confidence += 0.3
    if liquidity_above or liquidity_below:
        confidence += 0.2
    if premium_discount:
        confidence += 0.2
    confidence = min(1.0, confidence)

    return LocationContext(
        location_type=location_type,
        inside_institutional_zone=inside_zone,
        premium_discount=premium_discount,
        range_position=round(range_pos, 4),
        institutional_alignment=institutional_alignment,
        zone_quality=round(zone_quality, 4),
        zone_mitigated=zone_mitigated,
        liquidity_above=liquidity_above,
        liquidity_below=liquidity_below,
        nearest_liquidity_direction=nearest_direction,
        demand_zones_nearby=demand_nearby,
        supply_zones_nearby=supply_nearby,
        fvg_zones_nearby=fvg_nearby,
        confidence=round(confidence, 4),
        observations=observations,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. BEHAVIOUR BUILDER
# ═══════════════════════════════════════════════════════════════════════════════


def build_behaviour_context(mu: MarketUnderstanding) -> BehaviourContext:
    """
    Interpret market behaviour from MarketUnderstanding.

    Determines: regime, volatility, momentum, expansion/compression.
    Note: V3 research has NOT validated this layer as predictive.
    """
    h4 = mu.h4
    m5 = mu.m5
    m15 = mu.m15
    observations: list[str] = []

    # ─── Regime ───────────────────────────────────────────────────────
    if h4.trend and h4.trend in ("BULLISH", "BEARISH"):
        regime = "TRENDING"
    elif h4.volatility_state == "EXPANSION":
        regime = "VOLATILE"
    elif h4.market_phase == "CONSOLIDATION":
        regime = "RANGING"
    else:
        regime = "RANGING"  # Default when no strong signal

    regime_confidence = h4.trend_strength if regime == "TRENDING" else 0.5
    observations.append(f"Regime: {regime}")

    # ─── Volatility ───────────────────────────────────────────────────
    volatility_state = h4.volatility_state or "NEUTRAL"
    volatility_level = 0.5  # Default neutral
    if volatility_state == "EXPANSION":
        volatility_level = 0.8
    elif volatility_state == "CONTRACTION":
        volatility_level = 0.2

    # ─── Momentum ─────────────────────────────────────────────────────
    momentum_direction = m5.momentum_direction or "NEUTRAL"
    momentum_strength = m5.momentum_strength

    if momentum_direction != "NEUTRAL":
        observations.append(f"Momentum: {momentum_direction} ({momentum_strength:.2f})")

    # ─── Expansion / Compression ──────────────────────────────────────
    expansion_state = "NEUTRAL"
    compression_bars = 0

    if m15.displacement_present:
        expansion_state = "EXPANDING"
        observations.append("Expanding (displacement)")
    elif m15.pullback_active and m15.pullback_depth_atr < 0.5:
        expansion_state = "COMPRESSING"
        observations.append("Compressing (shallow pullback)")

    # ─── Displacement ─────────────────────────────────────────────────
    displacement_active = m15.displacement_present
    displacement_direction = ""
    displacement_mag = m15.displacement_magnitude_atr

    if displacement_active and m5.momentum_direction:
        displacement_direction = m5.momentum_direction

    # ─── Confidence ───────────────────────────────────────────────────
    confidence = 0.0
    if h4.trend or h4.volatility_state:
        confidence += 0.4
    if m5.atr > 0:
        confidence += 0.3
    if momentum_direction != "NEUTRAL":
        confidence += 0.3
    confidence = min(1.0, confidence)

    return BehaviourContext(
        regime=regime,
        regime_confidence=round(regime_confidence, 4),
        volatility_state=volatility_state,
        volatility_level=round(volatility_level, 4),
        momentum_direction=momentum_direction,
        momentum_strength=round(momentum_strength, 4),
        expansion_state=expansion_state,
        compression_bars=compression_bars,
        displacement_active=displacement_active,
        displacement_direction=displacement_direction,
        displacement_magnitude_atr=round(displacement_mag, 4),
        confidence=round(confidence, 4),
        observations=observations,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════


def build_market_context_interpretation(mu: MarketUnderstanding) -> MarketContextInterpretation:
    """
    Build complete MarketContextInterpretation from MarketUnderstanding.

    Orchestrates three independent context builders.
    Never raises — returns partial context on failure.
    """
    try:
        htf = build_htf_structure_context(mu)
    except Exception:
        htf = HTFStructureContext()

    try:
        location = build_location_context(mu)
    except Exception:
        location = LocationContext()

    try:
        behaviour = build_behaviour_context(mu)
    except Exception:
        behaviour = BehaviourContext()

    # Overall confidence = geometric mean of three layers
    confidences = [htf.confidence, location.confidence, behaviour.confidence]
    non_zero = [c for c in confidences if c > 0]
    if non_zero:
        overall = sum(non_zero) / len(non_zero)
    else:
        overall = 0.0

    # Combined observations
    all_obs = []
    if htf.observations:
        all_obs.extend([f"[HTF] {o}" for o in htf.observations])
    if location.observations:
        all_obs.extend([f"[LOC] {o}" for o in location.observations])
    if behaviour.observations:
        all_obs.extend([f"[BEH] {o}" for o in behaviour.observations])

    return MarketContextInterpretation(
        symbol=mu.symbol,
        timestamp_utc=mu.timestamp_utc,
        schema_version=_CONTEXT_SCHEMA_VERSION,
        htf_structure=htf,
        location=location,
        behaviour=behaviour,
        overall_confidence=round(overall, 4),
        observations=all_obs,
    )
