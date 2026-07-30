"""V10 Opportunity Engine — Evaluates whether an opportunity is forming.

Consumes V10MarketState only. Does not read raw candles or create indicators.
Produces OpportunityAssessment.

Evaluation dimensions:
  1. LOCATION: Is price at a meaningful institutional level?
  2. STRUCTURE: Does H1 structure support a directional thesis?
  3. BEHAVIOUR: Does the environment support movement?
  4. FORMATION: Is M15 showing a meaningful market reaction?
"""

from __future__ import annotations

import hashlib
import time
from core.v10.market_state import V10MarketState
from core.v10.opportunity_assessment import OpportunityAssessment, OpportunityQuality


def assess_opportunity(state: V10MarketState) -> OpportunityAssessment:
    """
    Evaluate V10MarketState for opportunity presence.

    Returns an immutable OpportunityAssessment.
    """
    supporting: list[str] = []
    conflicting: list[str] = []
    reasoning: list[str] = []

    # ─── 1. LOCATION ──────────────────────────────────────────
    location_score = _evaluate_location(state, supporting, conflicting, reasoning)

    # ─── 2. STRUCTURE ─────────────────────────────────────────
    structure_score, directional_bias = _evaluate_structure(state, supporting, conflicting, reasoning)

    # ─── 3. BEHAVIOUR ─────────────────────────────────────────
    behaviour_score = _evaluate_behaviour(state, supporting, conflicting, reasoning)

    # ─── 4. FORMATION ─────────────────────────────────────────
    formation_score, opportunity_type = _evaluate_formation(state, supporting, conflicting, reasoning)

    # ─── COMPOSITE QUALITY ────────────────────────────────────
    # Weighted: location 35%, structure 30%, behaviour 15%, formation 20%
    overall = (
        location_score * 0.35
        + structure_score * 0.30
        + behaviour_score * 0.15
        + formation_score * 0.20
    )

    quality = OpportunityQuality(
        location_score=location_score,
        structure_score=structure_score,
        behaviour_score=behaviour_score,
        formation_score=formation_score,
        overall_quality=round(overall, 4),
    )

    # ─── STATE DETERMINATION ──────────────────────────────────
    if overall >= 0.60 and location_score >= 0.4 and structure_score >= 0.3:
        opportunity_state = "VALID"
    elif overall >= 0.40 and location_score >= 0.2:
        opportunity_state = "WATCHING"
    else:
        opportunity_state = "INVALID"

    # If no meaningful type identified, override to INVALID
    if opportunity_type == "NONE":
        opportunity_state = "INVALID"

    # ─── OBSERVATION ID ───────────────────────────────────────
    obs_id = _generate_observation_id(state.symbol, state.timestamp_utc)

    return OpportunityAssessment(
        observation_id=obs_id,
        symbol=state.symbol,
        timestamp_utc=state.timestamp_utc,
        opportunity_state=opportunity_state,
        directional_bias=directional_bias,
        opportunity_type=opportunity_type,
        quality=quality,
        reasoning=reasoning,
        supporting_factors=supporting,
        conflicting_factors=conflicting,
    )


# ═══════════════════════════════════════════════════════════════
# DIMENSION EVALUATORS
# ═══════════════════════════════════════════════════════════════


def _evaluate_location(
    state: V10MarketState,
    supporting: list[str],
    conflicting: list[str],
    reasoning: list[str],
) -> float:
    """Evaluate: is price at a meaningful institutional level?
    
    Uses ONLY data populated in production:
      - H1 swing_high / swing_low (from BiasSnapshot pivot detection)
      - M15 range_position (0=discount extreme, 1=premium extreme)
      - H1 BOS direction (structural authority)
      - M5 rejection (confirms but never defines)
    
    "At institutional zone" means: price is at a structural extreme
    where institutional interest is expected (swing boundary areas).
    
    H1 OB fields are NOT used (dead fields in production — no tracker exists).
    """
    score = 0.0
    h1 = state.h1
    m15 = state.m15
    m5 = state.m5
    loc = state.location

    # ─── Range position (primary location signal) ──────────────
    # In production: populated from M15 candle analysis
    # Fallback: LocationState.range_position from V3 context
    range_pos = m15.range_position if m15.range_position > 0 else loc.range_position

    # ─── H1 Swing Levels (structural boundaries) ──────────────
    has_swing_structure = h1.swing_high > 0 and h1.swing_low > 0

    if not has_swing_structure and range_pos == 0:
        conflicting.append("No H1 swing structure and no range data")
        return 0.0

    # ─── At Structural Extreme (price near swing boundary) ─────
    at_premium_extreme = range_pos >= 0.78
    at_discount_extreme = range_pos <= 0.22

    if at_premium_extreme:
        # Price at H1 swing high area — potential supply reaction
        score += 0.40
        supporting.append(f"Price at premium extreme (range_pos={range_pos:.2f})")
        reasoning.append("At H1 premium boundary — institutional supply expected")
    elif at_discount_extreme:
        # Price at H1 swing low area — potential demand reaction
        score += 0.40
        supporting.append(f"Price at discount extreme (range_pos={range_pos:.2f})")
        reasoning.append("At H1 discount boundary — institutional demand expected")
    elif range_pos >= 0.65 or range_pos <= 0.35:
        # Approaching extreme — partial credit
        score += 0.20
        label = "premium" if range_pos >= 0.65 else "discount"
        supporting.append(f"Approaching {label} zone (range_pos={range_pos:.2f})")
    else:
        # Equilibrium — no location edge
        conflicting.append(f"Price in equilibrium (range_pos={range_pos:.2f})")

    # ─── H1 BOS Alignment with Position ───────────────────────
    # Bearish BOS + price at premium = strong supply location
    # Bullish BOS + price at discount = strong demand location
    if h1.bos_confirmed:
        if h1.bos_direction == "BEARISH" and range_pos >= 0.65:
            score += 0.20
            supporting.append("Bearish BOS + premium position (aligned for sell)")
        elif h1.bos_direction == "BULLISH" and range_pos <= 0.35:
            score += 0.20
            supporting.append("Bullish BOS + discount position (aligned for buy)")
        elif h1.bos_direction == "BEARISH" and range_pos <= 0.35:
            # Bearish BOS at discount = continuation, less location value
            score += 0.05
        elif h1.bos_direction == "BULLISH" and range_pos >= 0.65:
            score += 0.05

    # ─── H1 Swing Level Proximity (liquidity) ─────────────────
    if h1.equal_highs_level > 0 or h1.equal_lows_level > 0:
        score += 0.10
        supporting.append("Liquidity pool present (equal highs/lows)")

    if h1.session_high > 0 or h1.session_low > 0:
        if range_pos >= 0.85 or range_pos <= 0.15:
            score += 0.10
            supporting.append("Near session extreme — high liquidity area")

    # ─── M15 Pullback Confirmation ────────────────────────────
    # Pullback into zone area adds confidence
    if m15.pullback_active and m15.pullback_depth_atr >= 0.8:
        if at_premium_extreme or at_discount_extreme:
            score += 0.10
            supporting.append(f"M15 pullback into zone (depth={m15.pullback_depth_atr:.1f} ATR)")

    # ─── M5 Rejection CONFIRMS location (never defines it) ────
    if m5.rejection_present and (at_premium_extreme or at_discount_extreme):
        if at_premium_extreme and m5.rejection_direction == "BEARISH":
            score += 0.10
            supporting.append("M5 bearish rejection at premium extreme")
        elif at_discount_extreme and m5.rejection_direction == "BULLISH":
            score += 0.10
            supporting.append("M5 bullish rejection at discount extreme")

    return min(score, 1.0)


def _evaluate_structure(
    state: V10MarketState,
    supporting: list[str],
    conflicting: list[str],
    reasoning: list[str],
) -> tuple[float, str]:
    """Evaluate: does H1 structure provide directional evidence?"""
    score = 0.0
    bias = "NEUTRAL"

    h1 = state.h1
    htf = state.htf_alignment

    # BOS provides directional authority
    if h1.bos_confirmed:
        score += 0.40
        bias = h1.bos_direction if h1.bos_direction else "NEUTRAL"
        supporting.append(f"H1 BOS confirmed: {h1.bos_direction}")
        reasoning.append(f"Structure break {h1.bos_direction}")

    # CHoCH overrides BOS direction
    if h1.choch_detected:
        score += 0.30
        bias = h1.choch_direction if h1.choch_direction else bias
        supporting.append(f"H1 CHoCH detected: {h1.choch_direction}")
        reasoning.append(f"Character change {h1.choch_direction}")

    # Structural clarity
    if h1.structural_clarity >= 0.7:
        score += 0.20
        supporting.append(f"High structural clarity: {h1.structural_clarity:.2f}")
    elif h1.structural_clarity < 0.4:
        score -= 0.10
        conflicting.append(f"Low structural clarity: {h1.structural_clarity:.2f}")

    # HTF alignment
    if htf.structure_alignment >= 0.7:
        score += 0.10
        supporting.append("HTF alignment strong")
    elif htf.structure_alignment < 0.3:
        conflicting.append("HTF alignment weak/conflicted")

    # If no BOS/CHoCH, derive bias from dominant trend
    if bias == "NEUTRAL" and h1.dominant_trend in ("BULLISH", "BEARISH"):
        bias = h1.dominant_trend
        score += 0.15

    return min(max(score, 0.0), 1.0), bias


def _evaluate_behaviour(
    state: V10MarketState,
    supporting: list[str],
    conflicting: list[str],
    reasoning: list[str],
) -> float:
    """Evaluate: does the environment support movement?"""
    score = 0.5  # Neutral starting point
    regime = state.regime

    # V10 research finding: NEUTRAL regime is BEST for this system
    if regime.regime in ("RANGING", "NEUTRAL", "TRANSITIONAL", ""):
        score += 0.20
        supporting.append("Neutral/ranging regime (historically favourable)")
    elif regime.regime == "TRENDING":
        score += 0.10
        supporting.append("Trending regime")
    elif regime.regime == "VOLATILE":
        score -= 0.20
        conflicting.append("Volatile regime — unpredictable")

    # Volatility state
    if regime.volatility_state == "NEUTRAL":
        score += 0.10
    elif regime.volatility_state == "EXPANSION":
        score += 0.15
        supporting.append("Volatility expanding — movement likely")
    elif regime.volatility_state == "CONTRACTION":
        score += 0.05
        supporting.append("Volatility contracting — breakout potential")

    # Expansion state
    if regime.expansion_state == "EXPANDING":
        score += 0.10

    return min(max(score, 0.0), 1.0)


def _evaluate_formation(
    state: V10MarketState,
    supporting: list[str],
    conflicting: list[str],
    reasoning: list[str],
) -> tuple[float, str]:
    """Evaluate: is M15 showing a meaningful market reaction?"""
    score = 0.0
    opp_type = "NONE"
    m15 = state.m15
    m5 = state.m5

    # Displacement (strong directional candle)
    if m15.displacement_present:
        score += 0.40
        opp_type = "STRUCTURE_SHIFT"
        supporting.append(f"M15 displacement: {m15.displacement_direction} ({m15.displacement_magnitude_atr:.1f} ATR)")
        reasoning.append(f"M15 displacement {m15.displacement_direction}")

    # Internal BOS on M15
    if m15.internal_bos:
        score += 0.25
        if opp_type == "NONE":
            opp_type = "STRUCTURE_SHIFT"
        supporting.append(f"M15 internal BOS: {m15.internal_bos_direction}")

    # Pullback into zone (zone reaction setup)
    # Use range_position to determine zone proximity (not dead inside_institutional_zone)
    _range_pos = m15.range_position if m15.range_position > 0 else state.location.range_position
    _at_structural_extreme = _range_pos >= 0.75 or _range_pos <= 0.25
    if m15.pullback_active and _at_structural_extreme:
        score += 0.30
        if opp_type == "NONE":
            opp_type = "ZONE_REACTION"
        supporting.append("Pullback into structural zone")
        reasoning.append("M15 pullback at structural extreme")

    # M5 rejection present (execution-layer confirmation of M15 formation)
    if m5.rejection_present:
        score += 0.20
        supporting.append(f"M5 rejection: {m5.rejection_direction} ({m5.rejection_strength_atr:.1f} ATR)")

    # M5 confirmation candle
    if m5.confirmation_candle:
        score += 0.10
        supporting.append("M5 confirmation candle")

    # No M15 pullback + no displacement = fresh impulse
    if not m15.pullback_active and not m15.displacement_present:
        # Check if it's a ranging/no-activity state
        if m15.internal_bos or m5.local_bos:
            score += 0.15
            if opp_type == "NONE":
                opp_type = "TREND_CONTINUATION"
        elif _at_structural_extreme:
            score += 0.10
            if opp_type == "NONE":
                opp_type = "RANGE_REACTION"

    # Classify remaining types
    if opp_type == "NONE" and score > 0.3:
        if m15.pullback_active and m15.retracement_pct > 0.5:
            opp_type = "TREND_CONTINUATION"
        elif _at_structural_extreme:
            opp_type = "ZONE_REACTION"

    return min(score, 1.0), opp_type


# ═══════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════


def _generate_observation_id(symbol: str, timestamp: float) -> str:
    """Generate a deterministic observation ID from symbol + timestamp."""
    raw = f"{symbol}_{timestamp}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
