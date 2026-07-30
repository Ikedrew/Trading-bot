"""
V3 Opportunity Assessment Builder — Evaluates market context quality.

Consumes V3MarketContext and produces OpportunityAssessment.
Evaluates three dimensions:
    1. Structural alignment (HTF agreement, BOS, phase)
    2. Location quality (institutional zones, premium/discount)
    3. Behaviour compatibility (regime, volatility, momentum)

Does NOT produce trade signals. Only describes environment quality.
"""

from __future__ import annotations

import logging
from typing import Any

from core.v3_shadow.context_models import V3MarketContext
from core.v3_shadow.opportunity_models import (
    OpportunityAssessment,
    AlignmentResult,
    HIGH_QUALITY_CONTEXT,
    INTERESTING_CONTEXT,
    MIXED_CONTEXT,
    LOW_QUALITY_CONTEXT,
    INSUFFICIENT_CONTEXT,
    _OPPORTUNITY_SCHEMA_VERSION,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════


def build_opportunity_assessment(market_context: V3MarketContext) -> OpportunityAssessment:
    """
    Evaluate whether the current market context represents a meaningful environment.

    Args:
        market_context: V3MarketContext from Phase 2

    Returns:
        OpportunityAssessment — immutable quality description.
    """
    # Evaluate three dimensions
    structure = _evaluate_structure(market_context)
    location = _evaluate_location(market_context)
    behaviour = _evaluate_behaviour(market_context)

    # Combine evidence
    all_factors = list(structure.factors) + list(location.factors) + list(behaviour.factors)
    all_conflicts = list(structure.conflicts) + list(location.conflicts) + list(behaviour.conflicts)
    all_missing = list(structure.missing) + list(location.missing) + list(behaviour.missing)

    # Context quality (weighted: location strongest per research)
    # Location: 50%, Structure: 30%, Behaviour: 20%
    context_quality = (
        location.score * 0.50 +
        structure.score * 0.30 +
        behaviour.score * 0.20
    )

    # Determine assessment state
    assessment_state = _classify_state(context_quality, all_factors, all_conflicts, all_missing)

    # Confidence based on data availability
    confidence = market_context.overall_confidence

    # Generate observations
    observations = _generate_observations(assessment_state, structure, location, behaviour)

    return OpportunityAssessment(
        symbol=market_context.symbol,
        timestamp_utc=market_context.timestamp_utc,
        schema_version=_OPPORTUNITY_SCHEMA_VERSION,
        assessment_state=assessment_state,
        confidence=round(confidence, 4),
        context_quality=round(context_quality, 4),
        structure_alignment=structure,
        location_alignment=location,
        behaviour_alignment=behaviour,
        supporting_factors=all_factors,
        conflicting_factors=all_conflicts,
        missing_information=all_missing,
        observations=observations,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. STRUCTURAL ALIGNMENT
# ═══════════════════════════════════════════════════════════════════════════════


def _evaluate_structure(ctx: V3MarketContext) -> AlignmentResult:
    """Evaluate higher timeframe structural alignment."""
    htf = ctx.htf_structure
    factors: list[str] = []
    conflicts: list[str] = []
    missing: list[str] = []
    score = 0.0

    # Macro bias clarity
    if htf.macro_bias in ("BULLISH", "BEARISH"):
        factors.append(f"Clear macro bias: {htf.macro_bias}")
        score += 0.3
    elif htf.macro_bias == "CONFLICTED":
        conflicts.append("H4/H1 directional conflict")
        score += 0.05
    elif htf.macro_bias == "NEUTRAL":
        # Neutral is not a conflict — just less informative
        score += 0.1

    # BOS confirmation
    if htf.bos_active:
        factors.append(f"BOS confirmed: {htf.bos_direction}")
        score += 0.3
    else:
        missing.append("No BOS confirmation")

    # Structure alignment score
    if htf.structure_alignment > 0.6:
        factors.append(f"Strong structural alignment ({htf.structure_alignment:.2f})")
        score += 0.2
    elif htf.structure_alignment > 0.3:
        score += 0.1

    # Authority known
    if htf.authority_timeframe and htf.authority_timeframe != "UNKNOWN":
        factors.append(f"Authority: {htf.authority_timeframe}")
        score += 0.1
    else:
        missing.append("No clear authority timeframe")

    # Phase alignment
    if htf.phase_alignment == "ALIGNED":
        factors.append("Phase aligned with structure")
        score += 0.1
    elif htf.phase_alignment == "CONFLICTED":
        conflicts.append("Phase conflicts with structure")

    score = min(1.0, score)
    return AlignmentResult(score=round(score, 4), factors=factors, conflicts=conflicts, missing=missing)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LOCATION QUALITY
# ═══════════════════════════════════════════════════════════════════════════════


def _evaluate_location(ctx: V3MarketContext) -> AlignmentResult:
    """
    Evaluate market location quality.

    This is the STRONGEST V3 research area.
    Inside Order Block = +0.071R (only positive finding).
    """
    loc = ctx.location
    factors: list[str] = []
    conflicts: list[str] = []
    missing: list[str] = []
    score = 0.0

    # Inside institutional zone (strongest signal)
    if loc.inside_institutional_zone:
        factors.append(f"Inside institutional zone: {loc.location_type}")
        score += 0.4
    else:
        # Not inside zone — lower quality but not necessarily conflicting
        if loc.demand_zones_nearby > 0 or loc.supply_zones_nearby > 0:
            factors.append("Near institutional zone")
            score += 0.15

    # Premium / Discount
    if loc.premium_discount == "DISCOUNT":
        factors.append("Discount location")
        score += 0.2
    elif loc.premium_discount == "PREMIUM":
        # Premium is less favourable per research (WR=38.9% vs 62.7% discount)
        conflicts.append("Premium location (historically weaker)")
        score += 0.05
    elif loc.premium_discount == "EQUILIBRIUM":
        score += 0.1
    else:
        missing.append("Range position unknown")

    # Institutional alignment (demand+discount = best per research)
    if loc.institutional_alignment in ("BULLISH", "BEARISH"):
        factors.append(f"Institutional alignment: {loc.institutional_alignment}")
        score += 0.2
    elif loc.inside_institutional_zone:
        # Zone present but alignment unclear
        score += 0.1

    # Zone quality
    if loc.zone_quality > 0.7:
        factors.append(f"High zone quality ({loc.zone_quality:.2f})")
        score += 0.1
    elif loc.zone_quality > 0.4:
        score += 0.05

    # Liquidity context (targets available)
    if loc.liquidity_above and loc.liquidity_below:
        factors.append("Liquidity targets both directions")
        score += 0.1
    elif loc.liquidity_above or loc.liquidity_below:
        factors.append(f"Liquidity target {loc.nearest_liquidity_direction}")
        score += 0.05

    # Mitigated zone (lower quality)
    if loc.zone_mitigated:
        conflicts.append("Zone already mitigated")
        score -= 0.1

    score = max(0.0, min(1.0, score))
    return AlignmentResult(score=round(score, 4), factors=factors, conflicts=conflicts, missing=missing)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. BEHAVIOUR COMPATIBILITY
# ═══════════════════════════════════════════════════════════════════════════════


def _evaluate_behaviour(ctx: V3MarketContext) -> AlignmentResult:
    """
    Evaluate market behaviour compatibility.

    NOTE: V3 research has NOT validated this layer as predictive.
    It provides context but should NOT dominate the assessment.
    """
    beh = ctx.behaviour
    factors: list[str] = []
    conflicts: list[str] = []
    missing: list[str] = []
    score = 0.3  # Start at neutral (not penalizing by default)

    # Momentum alignment
    if beh.momentum_direction in ("BULLISH", "BEARISH") and beh.momentum_strength > 0.5:
        factors.append(f"Clear momentum: {beh.momentum_direction} ({beh.momentum_strength:.2f})")
        score += 0.2
    elif beh.momentum_direction == "NEUTRAL":
        # Neutral momentum is OK — doesn't confirm or conflict
        pass

    # Volatility suitability
    if beh.volatility_state == "NEUTRAL":
        factors.append("Normal volatility")
        score += 0.1
    elif beh.volatility_state == "EXPANSION":
        factors.append("Expanding volatility")
        score += 0.15
    elif beh.volatility_state == "CONTRACTION":
        conflicts.append("Contracting volatility (may limit movement)")
        score -= 0.05

    # Displacement (strong directional move)
    if beh.displacement_active:
        factors.append(f"Displacement active ({beh.displacement_magnitude_atr:.1f} ATR)")
        score += 0.15

    # Expansion state
    if beh.expansion_state == "EXPANDING":
        factors.append("Market expanding")
        score += 0.1
    elif beh.expansion_state == "COMPRESSING":
        # Compression before expansion is common — not necessarily bad
        factors.append("Market compressing (potential energy)")
        score += 0.05

    # Regime (informational — research hasn't validated)
    if beh.regime == "TRENDING":
        factors.append("Trending regime")
        score += 0.1
    elif beh.regime == "VOLATILE":
        conflicts.append("Volatile regime (unpredictable)")
        score -= 0.1

    score = max(0.0, min(1.0, score))
    return AlignmentResult(score=round(score, 4), factors=factors, conflicts=conflicts, missing=missing)


# ═══════════════════════════════════════════════════════════════════════════════
# STATE CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════


def _classify_state(
    quality: float,
    factors: list[str],
    conflicts: list[str],
    missing: list[str],
) -> str:
    """Classify the overall assessment state from quality score and evidence."""
    # Insufficient data
    if len(factors) == 0 and len(missing) > 3:
        return INSUFFICIENT_CONTEXT

    # High quality: strong score with limited conflicts
    if quality >= 0.70 and len(conflicts) <= 1:
        return HIGH_QUALITY_CONTEXT

    # Interesting: moderate score or strong with some conflicts
    if quality >= 0.50 or (quality >= 0.40 and len(factors) >= 3):
        return INTERESTING_CONTEXT

    # Mixed: some supporting, some conflicting
    if len(factors) >= 2 and len(conflicts) >= 2:
        return MIXED_CONTEXT

    # Low quality: weak score
    if quality < 0.30:
        return LOW_QUALITY_CONTEXT

    return MIXED_CONTEXT


# ═══════════════════════════════════════════════════════════════════════════════
# OBSERVATION GENERATION
# ═══════════════════════════════════════════════════════════════════════════════


def _generate_observations(
    state: str,
    structure: AlignmentResult,
    location: AlignmentResult,
    behaviour: AlignmentResult,
) -> list[str]:
    """Generate human-readable observations."""
    obs: list[str] = [f"Assessment: {state}"]

    if structure.factors:
        obs.append(f"Structure: {'; '.join(structure.factors[:2])}")
    if location.factors:
        obs.append(f"Location: {'; '.join(location.factors[:2])}")
    if behaviour.factors:
        obs.append(f"Behaviour: {'; '.join(behaviour.factors[:2])}")

    if structure.conflicts or location.conflicts:
        all_c = structure.conflicts + location.conflicts + behaviour.conflicts
        if all_c:
            obs.append(f"Conflicts: {'; '.join(all_c[:2])}")

    return obs
