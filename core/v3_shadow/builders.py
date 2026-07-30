"""
V3 MarketUnderstanding Builders — Convert raw market data into structured observations.

Each builder has one responsibility: describe one timeframe objectively.
No builder knows about execution, risk, entries, or strategy selection.

Architecture:
    H4UnderstandingBuilder → H4Understanding
    H1UnderstandingBuilder → H1Understanding
    M15UnderstandingBuilder → M15Understanding
    M5UnderstandingBuilder → M5Understanding
    M1UnderstandingBuilder → M1Understanding
    MarketUnderstandingBuilder → MarketUnderstanding (orchestrates all)
"""

from __future__ import annotations

import logging
from typing import Any

from core.v3_shadow.models import (
    MarketUnderstanding,
    H4Understanding,
    H1Understanding,
    M15Understanding,
    M5Understanding,
    M1Understanding,
    _SCHEMA_VERSION,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# H4 BUILDER
# ═══════════════════════════════════════════════════════════════════════════════


def build_h4_understanding(
    *,
    candles: list | None = None,
    htf_context: Any = None,
    market_context: Any = None,
) -> H4Understanding:
    """
    Build H4 understanding from available data.

    Sources: HTFContext.regime (RegimeSnapshot), MarketContext.h4, or H4 candles.
    """
    trend = ""
    trend_strength = 0.0
    market_phase = ""
    swing_high = 0.0
    swing_low = 0.0
    structure_type = ""
    atr = 0.0
    volatility_state = ""

    # Extract from MarketContext if available
    if market_context is not None:
        h4 = getattr(market_context, "h4", None)
        if h4:
            regime = getattr(h4, "regime", "") or ""
            if "TRENDING" in regime.upper():
                trend = getattr(h4, "trend_bias", "NEUTRAL") or "NEUTRAL"
                trend_strength = float(getattr(h4, "trend_strength", 0) or 0)
            elif "RANG" in regime.upper():
                trend = "NEUTRAL"
            volatility_state = (
                "EXPANSION" if float(getattr(h4, "atr_ratio", 1.0) or 1.0) > 1.3
                else "CONTRACTION" if float(getattr(h4, "atr_ratio", 1.0) or 1.0) < 0.7
                else "NEUTRAL"
            )
            swing_high = float(getattr(h4, "swing_high", 0) or 0)
            swing_low = float(getattr(h4, "swing_low", 0) or 0)

    # Extract from HTFContext if MarketContext unavailable
    if not trend and htf_context is not None:
        regime_snap = getattr(htf_context, "regime", None)
        if regime_snap:
            classification = getattr(regime_snap, "classification", None)
            if classification:
                regime_str = classification.value if hasattr(classification, "value") else str(classification)
                if "TRENDING" in regime_str.upper():
                    trend = getattr(regime_snap, "trend_bias", "NEUTRAL") or "NEUTRAL"
                    trend_strength = float(getattr(regime_snap, "trend_strength", 0) or 0)
                elif "RANG" in regime_str.upper():
                    trend = "NEUTRAL"
            atr_ratio = float(getattr(regime_snap, "atr_ratio", 1.0) or 1.0)
            volatility_state = (
                "EXPANSION" if atr_ratio > 1.3
                else "CONTRACTION" if atr_ratio < 0.7
                else "NEUTRAL"
            )

    # Derive market phase from MarketContext if available
    if market_context is not None:
        phase = getattr(market_context, "phase", None)
        if phase and hasattr(phase, "value"):
            market_phase = phase.value

    return H4Understanding(
        trend=trend,
        trend_strength=round(trend_strength, 4),
        market_phase=market_phase,
        swing_high=swing_high,
        swing_low=swing_low,
        structure_type=structure_type,
        atr=atr,
        volatility_state=volatility_state,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# H1 BUILDER
# ═══════════════════════════════════════════════════════════════════════════════


def build_h1_understanding(
    *,
    candles: list | None = None,
    htf_context: Any = None,
    market_context: Any = None,
    liquidity_snapshot: Any = None,
) -> H1Understanding:
    """
    Build H1 understanding from available data.

    Sources: HTFContext.bias (BiasSnapshot), MarketContext.h1, liquidity detector.
    """
    bos_confirmed = False
    bos_direction = ""
    bos_level = 0.0
    dominant_trend = ""
    swing_high = 0.0
    swing_low = 0.0
    structure_type = ""
    structural_clarity = 0.0

    if market_context is not None:
        h1 = getattr(market_context, "h1", None)
        if h1:
            dominant_trend = getattr(h1, "direction", "") or ""
            bos_confirmed = bool(getattr(h1, "bos_confirmed", False))
            bos_direction = getattr(h1, "bos_direction", "") or ""
            bos_level = float(getattr(h1, "bos_level", 0) or 0)
            swing_high = float(getattr(h1, "swing_high", 0) or 0)
            swing_low = float(getattr(h1, "swing_low", 0) or 0)
            structure_type = getattr(h1, "swing_structure", "") or ""
            structural_clarity = float(getattr(h1, "confidence", 0) or 0)

    elif htf_context is not None:
        bias_snap = getattr(htf_context, "bias", None)
        if bias_snap:
            direction = getattr(bias_snap, "direction", None)
            dominant_trend = direction.value if direction and hasattr(direction, "value") else ""
            bos_confirmed = bool(getattr(bias_snap, "bos_confirmed", False))
            bos_direction = getattr(bias_snap, "bos_direction", "") or ""
            bos_level = float(getattr(bias_snap, "bos_level", 0) or 0)
            swing_high = float(getattr(bias_snap, "last_swing_high", 0) or 0)
            swing_low = float(getattr(bias_snap, "last_swing_low", 0) or 0)
            structure_type = getattr(bias_snap, "swing_structure", "") or ""
            structural_clarity = float(getattr(bias_snap, "confidence", 0) or 0)

    # Liquidity from detector
    session_high = 0.0
    session_low = 0.0
    equal_highs = 0.0
    equal_lows = 0.0
    if liquidity_snapshot is not None:
        session_high = float(getattr(liquidity_snapshot, "prev_session_high", 0) or 0)
        session_low = float(getattr(liquidity_snapshot, "prev_session_low", 0) or 0)
        if getattr(liquidity_snapshot, "equal_highs_above", False):
            equal_highs = float(getattr(liquidity_snapshot, "equal_highs_price", 0) or 0)
        if getattr(liquidity_snapshot, "equal_lows_below", False):
            equal_lows = float(getattr(liquidity_snapshot, "equal_lows_price", 0) or 0)

    return H1Understanding(
        bos_confirmed=bos_confirmed,
        bos_direction=bos_direction,
        bos_level=bos_level,
        dominant_trend=dominant_trend,
        swing_high=swing_high,
        swing_low=swing_low,
        structure_type=structure_type,
        structural_clarity=round(structural_clarity, 4),
        equal_highs_level=equal_highs,
        equal_lows_level=equal_lows,
        session_high=session_high,
        session_low=session_low,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# M15 BUILDER
# ═══════════════════════════════════════════════════════════════════════════════


def build_m15_understanding(
    *,
    candles: list | None = None,
    market_context: Any = None,
    ob_snapshot: Any = None,
    fvg_snapshot: Any = None,
    current_price: float = 0.0,
    atr: float = 0.0,
) -> M15Understanding:
    """
    Build M15 understanding from available data.

    Sources: MarketContext.m15, OB detector, FVG detector, candles.
    """
    swing_high = 0.0
    swing_low = 0.0
    range_position = 0.0
    internal_bos = False
    displacement_present = False
    displacement_mag = 0.0

    if market_context is not None:
        m15 = getattr(market_context, "m15", None)
        if m15:
            swing_high = float(getattr(m15, "swing_high", 0) or getattr(m15, "nearest_resistance", 0) or 0)
            swing_low = float(getattr(m15, "swing_low", 0) or getattr(m15, "nearest_support", 0) or 0)

    # Range position
    if swing_high > swing_low and current_price > 0:
        if current_price <= swing_low:
            range_position = 0.0
        elif current_price >= swing_high:
            range_position = 1.0
        else:
            range_position = (current_price - swing_low) / (swing_high - swing_low)

    # Pullback detection from candles
    pullback_active = False
    pullback_depth = 0.0
    retracement_pct = 0.0
    if candles and len(candles) > 10 and atr > 0:
        recent = candles[-5:]
        prev = candles[-10:-5]
        recent_low = min(c.low for c in recent)
        prev_high = max(c.high for c in prev)
        prev_low = min(c.low for c in prev)
        impulse = prev_high - prev_low
        if impulse > 0 and recent_low < prev_high:
            pullback = prev_high - recent_low
            pullback_depth = pullback / atr
            retracement_pct = pullback / impulse if impulse > 0 else 0
            if pullback_depth > 0.5:
                pullback_active = True

    # Refined OB zones
    demand_ob_high = 0.0
    demand_ob_low = 0.0
    supply_ob_high = 0.0
    supply_ob_low = 0.0
    if ob_snapshot is not None:
        demand_ob_high = float(getattr(ob_snapshot, "nearest_demand_ob_price", 0) or 0)
        supply_ob_high = float(getattr(ob_snapshot, "nearest_supply_ob_price", 0) or 0)

    # Displacement from last candle
    if candles and len(candles) > 1 and atr > 0:
        last = candles[-1]
        c_range = last.high - last.low
        if c_range > atr * 1.5:
            displacement_present = True
            displacement_mag = c_range / atr

    return M15Understanding(
        internal_bos=internal_bos,
        pullback_active=pullback_active,
        pullback_depth_atr=round(pullback_depth, 4),
        retracement_pct=round(retracement_pct, 4),
        refined_demand_ob_high=demand_ob_high,
        refined_supply_ob_high=supply_ob_high,
        range_position=round(range_position, 4),
        swing_high=swing_high,
        swing_low=swing_low,
        displacement_present=displacement_present,
        displacement_magnitude_atr=round(displacement_mag, 4),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# M5 BUILDER
# ═══════════════════════════════════════════════════════════════════════════════


def build_m5_understanding(
    *,
    candles: list | None = None,
    current_price: float = 0.0,
    bid: float = 0.0,
    ask: float = 0.0,
    ob_snapshot: Any = None,
    fvg_snapshot: Any = None,
) -> M5Understanding:
    """
    Build M5 execution environment description.

    Does NOT generate entries. Only describes.
    """
    atr = 0.0
    spread = abs(ask - bid) if bid > 0 and ask > 0 else 0.0
    rejection_present = False
    rejection_direction = ""
    rejection_strength = 0.0
    local_bos = False
    momentum_direction = ""
    momentum_strength = 0.0

    if candles and len(candles) > 14:
        recent = candles[-14:]
        atr = sum(c.high - c.low for c in recent) / len(recent)

        # Last candle rejection
        last = candles[-1]
        c_range = last.high - last.low
        body = abs(last.close - last.open)
        if c_range > 0 and atr > 0:
            upper_wick = last.high - max(last.open, last.close)
            lower_wick = min(last.open, last.close) - last.low
            max_wick = max(upper_wick, lower_wick)
            if max_wick > body * 1.5 and max_wick > atr * 0.5:
                rejection_present = True
                rejection_direction = "BULLISH" if lower_wick > upper_wick else "BEARISH"
                rejection_strength = max_wick / atr

        # Momentum (last 5 candles)
        last5 = candles[-5:]
        bullish = sum(1 for c in last5 if c.close > c.open)
        bearish = sum(1 for c in last5 if c.close < c.open)
        if bullish > bearish + 1:
            momentum_direction = "BULLISH"
            momentum_strength = bullish / 5
        elif bearish > bullish + 1:
            momentum_direction = "BEARISH"
            momentum_strength = bearish / 5
        else:
            momentum_direction = "NEUTRAL"
            momentum_strength = 0.0

    # At institutional zone?
    at_zone = False
    zone_type = ""
    if ob_snapshot is not None:
        if getattr(ob_snapshot, "price_inside_ob", False):
            at_zone = True
            zone_type = f"{getattr(ob_snapshot, 'ob_type_if_inside', 'OB')}_OB"
    if not at_zone and fvg_snapshot is not None:
        if getattr(fvg_snapshot, "price_inside_fvg", False):
            at_zone = True
            zone_type = f"{getattr(fvg_snapshot, 'fvg_direction_if_inside', 'FVG')}_FVG"

    spread_atr = spread / atr if atr > 0 else 0.0

    return M5Understanding(
        local_bos=local_bos,
        momentum_direction=momentum_direction,
        momentum_strength=round(momentum_strength, 4),
        rejection_present=rejection_present,
        rejection_direction=rejection_direction,
        rejection_strength_atr=round(rejection_strength, 4),
        at_institutional_zone=at_zone,
        zone_type=zone_type,
        atr=atr,
        spread=spread,
        spread_atr_ratio=round(spread_atr, 6),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# M1 BUILDER (Research only)
# ═══════════════════════════════════════════════════════════════════════════════


def build_m1_understanding(
    *,
    candles: list | None = None,
    bid: float = 0.0,
    ask: float = 0.0,
) -> M1Understanding:
    """
    Build M1 research-only precision layer.

    Experimental. Not used in any decision.
    """
    spread = abs(ask - bid) if bid > 0 and ask > 0 else 0.0

    if not candles or len(candles) < 5:
        return M1Understanding(spread_at_observation=spread)

    recent = candles[-5:]
    recent_high = max(c.high for c in recent)
    recent_low = min(c.low for c in recent)
    pip_size = 0.0001  # Default for non-JPY
    micro_range = (recent_high - recent_low) / pip_size

    # Velocity: net move over last 5 candles normalized by range
    net_move = abs(candles[-1].close - candles[-5].open)
    total_range = sum(c.high - c.low for c in recent)
    velocity = net_move / total_range if total_range > 0 else 0.0

    return M1Understanding(
        spread_at_observation=spread,
        recent_high=recent_high,
        recent_low=recent_low,
        micro_range_pips=round(micro_range, 2),
        candle_velocity=round(velocity, 4),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════


def build_market_understanding(
    *,
    symbol: str,
    timestamp_utc: float,
    candles: list | None = None,
    htf_context: Any = None,
    market_context: Any = None,
    bid: float = 0.0,
    ask: float = 0.0,
    liquidity_snapshot: Any = None,
    fvg_snapshot: Any = None,
    ob_snapshot: Any = None,
) -> MarketUnderstanding:
    """
    Build complete MarketUnderstanding from all available sources.

    Orchestrates per-timeframe builders and combines into single immutable snapshot.
    Never raises — returns partial understanding on failure.
    """
    current_price = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0

    # Compute ATR from M5 candles
    atr = 0.0
    if candles and len(candles) > 14:
        recent = candles[-14:]
        atr = sum(c.high - c.low for c in recent) / len(recent)

    # Build each layer
    try:
        h4 = build_h4_understanding(
            htf_context=htf_context, market_context=market_context)
    except Exception:
        h4 = H4Understanding()

    try:
        h1 = build_h1_understanding(
            htf_context=htf_context, market_context=market_context,
            liquidity_snapshot=liquidity_snapshot)
    except Exception:
        h1 = H1Understanding()

    try:
        m15 = build_m15_understanding(
            candles=candles, market_context=market_context,
            ob_snapshot=ob_snapshot, fvg_snapshot=fvg_snapshot,
            current_price=current_price, atr=atr)
    except Exception:
        m15 = M15Understanding()

    try:
        m5 = build_m5_understanding(
            candles=candles, current_price=current_price,
            bid=bid, ask=ask,
            ob_snapshot=ob_snapshot, fvg_snapshot=fvg_snapshot)
    except Exception:
        m5 = M5Understanding()

    try:
        m1 = build_m1_understanding(candles=candles, bid=bid, ask=ask)
    except Exception:
        m1 = M1Understanding()

    # Compute overall confidence (based on data availability)
    confidence = _compute_confidence(h4, h1, m15, m5, candles)

    # Generate observations
    observations = _generate_observations(h4, h1, m15, m5)

    return MarketUnderstanding(
        symbol=symbol,
        timestamp_utc=timestamp_utc,
        schema_version=_SCHEMA_VERSION,
        confidence=round(confidence, 4),
        h4=h4,
        h1=h1,
        m15=m15,
        m5=m5,
        m1=m1,
        observations=observations,
    )


def _compute_confidence(
    h4: H4Understanding, h1: H1Understanding,
    m15: M15Understanding, m5: M5Understanding,
    candles: list | None,
) -> float:
    """Compute confidence based on data availability (0-1)."""
    score = 0.0
    # H4 data present
    if h4.trend or h4.volatility_state:
        score += 0.2
    # H1 structure present
    if h1.swing_high > 0 or h1.bos_confirmed:
        score += 0.25
    # M15 range known
    if m15.swing_high > 0 and m15.swing_low > 0:
        score += 0.25
    # M5 candle data
    if candles and len(candles) > 50:
        score += 0.2
    elif candles and len(candles) > 14:
        score += 0.1
    # M5 ATR available
    if m5.atr > 0:
        score += 0.1
    return min(1.0, score)


def _generate_observations(
    h4: H4Understanding, h1: H1Understanding,
    m15: M15Understanding, m5: M5Understanding,
) -> list[str]:
    """Generate human-readable observation notes."""
    obs: list[str] = []

    if h4.trend:
        obs.append(f"H4: {h4.trend} ({h4.volatility_state})")
    if h1.bos_confirmed:
        obs.append(f"H1: BOS {h1.bos_direction}")
    if h1.dominant_trend:
        obs.append(f"H1: trend={h1.dominant_trend}")
    if m15.pullback_active:
        obs.append(f"M15: pullback ({m15.pullback_depth_atr:.1f} ATR)")
    if m15.displacement_present:
        obs.append(f"M15: displacement ({m15.displacement_magnitude_atr:.1f} ATR)")
    if m5.at_institutional_zone:
        obs.append(f"M5: at {m5.zone_type}")
    if m5.rejection_present:
        obs.append(f"M5: rejection {m5.rejection_direction}")
    if m5.momentum_direction and m5.momentum_direction != "NEUTRAL":
        obs.append(f"M5: momentum {m5.momentum_direction}")

    return obs
