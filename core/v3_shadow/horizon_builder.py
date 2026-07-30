"""
V3 Horizon Assessment Builder — Classifies expected movement profile.

Evaluates ALL horizons as competing movement HYPOTHESES.
Does NOT hard-code "Inside OB = INTRADAY". Instead, each horizon is
evaluated with an initial research prior that the research engine can
validate or update based on observed outcomes.

DESIGN PRINCIPLE:
    The initial plausibility values are RESEARCH PRIORS, not fixed logic.
    They represent starting assumptions before sufficient outcome data exists.
    They are NOT predictions, execution rules, or permanent weights.

    Future research may discover:
        - SCALP: most common outcome from OB reactions
        - INTRADAY: less frequent but higher expectancy
        - EXTENDED: rare but significant when HTF alignment exists

    The system adapts based on observed outcomes vs predicted horizons.

RESEARCH FEEDBACK:
    Every HorizonAssessment preserves all candidates so the research engine
    can compare: Predicted Horizon vs Actual Movement Distribution.

Does NOT create entries, execute trades, or predict direction.
"""

from __future__ import annotations

import logging
from typing import Any

from core.v3_shadow.context_models import V3MarketContext
from core.v3_shadow.opportunity_models import (
    OpportunityAssessment,
    HIGH_QUALITY_CONTEXT,
    INTERESTING_CONTEXT,
    MIXED_CONTEXT,
    LOW_QUALITY_CONTEXT,
    INSUFFICIENT_CONTEXT,
)
from core.v3_shadow.horizon_models import (
    HorizonAssessment,
    HorizonCandidate,
    HorizonProfile,
    PROFILES,
    SCALP,
    INTRADAY,
    EXTENDED,
    NO_HORIZON,
    SCALP_PROFILE,
    INTRADAY_PROFILE,
    EXTENDED_PROFILE,
    _HORIZON_SCHEMA_VERSION,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════


def build_horizon_assessment(
    market_context: V3MarketContext,
    opportunity: OpportunityAssessment,
) -> HorizonAssessment:
    """
    Classify the expected movement profile for an opportunity.

    Evaluates all three horizons as candidates. Selects most plausible.
    Preserves all evaluations for research.
    """
    # Gate: insufficient/low-quality → no horizon
    if opportunity.assessment_state in (INSUFFICIENT_CONTEXT, LOW_QUALITY_CONTEXT):
        return HorizonAssessment(
            symbol=market_context.symbol,
            timestamp_utc=market_context.timestamp_utc,
            opportunity_state=opportunity.assessment_state,
            selected_horizon=NO_HORIZON,
            observations=[f"No horizon: {opportunity.assessment_state}"],
        )

    # Evaluate each horizon as a candidate
    scalp_candidate = _evaluate_scalp(market_context, opportunity)
    intraday_candidate = _evaluate_intraday(market_context, opportunity)
    extended_candidate = _evaluate_extended(market_context, opportunity)

    candidates = [scalp_candidate, intraday_candidate, extended_candidate]

    # Select most plausible
    best = max(candidates, key=lambda c: c.plausibility)

    # If best plausibility is very low, no horizon
    if best.plausibility < 0.2:
        return HorizonAssessment(
            symbol=market_context.symbol,
            timestamp_utc=market_context.timestamp_utc,
            opportunity_state=opportunity.assessment_state,
            selected_horizon=NO_HORIZON,
            candidates=candidates,
            observations=["No horizon plausible (all < 0.2)"],
        )

    # Build from winning candidate
    profile = PROFILES[best.horizon]
    spread_risk = _estimate_spread_risk(market_context, profile)
    vol_fit = _assess_volatility_fit(spread_risk, profile)
    duration_class = _duration_class(profile)

    # Combine all supporting/conflicting
    all_supporting = list(best.supporting_factors)
    all_conflicting = list(best.conflicting_factors)

    observations = [
        f"Horizon: {best.horizon} (plausibility {best.plausibility:.2f})",
        f"Expected: {best.expected_move_min_pips:.0f}-{best.expected_move_max_pips:.0f} pips",
        f"Stop: {profile.stop_source} ({profile.typical_stop_pips_min}-{profile.typical_stop_pips_max} pips)",
        f"Duration: {duration_class}",
        f"Volatility: {vol_fit}",
    ]

    return HorizonAssessment(
        symbol=market_context.symbol,
        timestamp_utc=market_context.timestamp_utc,
        schema_version=_HORIZON_SCHEMA_VERSION,
        opportunity_state=opportunity.assessment_state,
        selected_horizon=best.horizon,
        expected_move_min_pips=best.expected_move_min_pips,
        expected_move_max_pips=best.expected_move_max_pips,
        structure_timeframe=profile.stop_source.split("_")[0],  # M5/M15/H1
        stop_framework=profile.stop_source,
        target_framework=best.target_framework,
        duration_class=duration_class,
        management_profile=profile.management_style,
        volatility_fit=vol_fit,
        spread_risk_estimate=round(spread_risk, 4),
        candidates=candidates,
        confidence=round(best.plausibility * opportunity.confidence, 4),
        supporting_factors=all_supporting,
        conflicting_factors=all_conflicting,
        observations=observations,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PER-HORIZON EVALUATION
# ═══════════════════════════════════════════════════════════════════════════════


def _evaluate_scalp(ctx: V3MarketContext, opp: OpportunityAssessment) -> HorizonCandidate:
    """Evaluate SCALP plausibility (initial research prior = 0.30)."""
    factors: list[str] = []
    conflicts: list[str] = []
    # Initial research prior: SCALP is always somewhat plausible as a
    # short-term reaction. This value is a starting assumption, not a rule.
    # Research will determine actual frequency of scalp-sized outcomes.
    plausibility = 0.3

    loc = ctx.location
    beh = ctx.behaviour

    # Scalp is more plausible when:
    # - Near zone (reaction play)
    if loc.demand_zones_nearby > 0 or loc.supply_zones_nearby > 0:
        plausibility += 0.15
        factors.append("Near institutional zone")

    # - Inside zone (could be scalp OR intraday — scalp captures first reaction)
    if loc.inside_institutional_zone:
        plausibility += 0.1
        factors.append("Inside zone (first reaction)")

    # - Strong momentum (quick moves)
    if beh.momentum_strength > 0.6:
        plausibility += 0.1
        factors.append(f"Strong momentum ({beh.momentum_strength:.2f})")

    # - Displacement active (fast market)
    if beh.displacement_active:
        plausibility += 0.1
        factors.append("Displacement (fast market)")

    # Scalp is less plausible when:
    # - Contracting volatility (no movement)
    if beh.volatility_state == "CONTRACTION":
        plausibility -= 0.15
        conflicts.append("Contracting volatility limits quick moves")

    # - Open space (nothing to react to)
    if not loc.inside_institutional_zone and loc.demand_zones_nearby == 0 and loc.supply_zones_nearby == 0:
        plausibility -= 0.1
        conflicts.append("No nearby zones for reaction")

    # Target framework
    target = "NEARBY_LIQUIDITY" if loc.liquidity_above or loc.liquidity_below else "FIXED_RR"

    return HorizonCandidate(
        horizon=SCALP,
        plausibility=max(0.0, min(1.0, plausibility)),
        expected_move_min_pips=SCALP_PROFILE.expected_move_min_pips,
        expected_move_max_pips=SCALP_PROFILE.expected_move_max_pips,
        stop_framework=SCALP_PROFILE.stop_source,
        target_framework=target,
        supporting_factors=factors,
        conflicting_factors=conflicts,
    )


def _evaluate_intraday(ctx: V3MarketContext, opp: OpportunityAssessment) -> HorizonCandidate:
    """Evaluate INTRADAY plausibility (initial research prior = 0.20)."""
    factors: list[str] = []
    conflicts: list[str] = []
    # Initial research prior: INTRADAY requires more context evidence.
    # This prior reflects that sustained 20-50 pip moves are less common
    # than short reactions. Research will calibrate actual frequency.
    plausibility = 0.2

    loc = ctx.location
    htf = ctx.htf_structure
    beh = ctx.behaviour

    # Intraday is more plausible when:
    # - Inside institutional zone with quality
    if loc.inside_institutional_zone and loc.zone_quality > 0.5:
        plausibility += 0.2
        factors.append(f"Inside quality zone ({loc.zone_quality:.2f})")

    # - Clear structural alignment
    if htf.bos_active:
        plausibility += 0.15
        factors.append(f"BOS confirmed: {htf.bos_direction}")

    # - Discount/premium position (directional space available)
    if loc.premium_discount in ("DISCOUNT", "PREMIUM"):
        plausibility += 0.1
        factors.append(f"Position: {loc.premium_discount}")

    # - Liquidity targets available (room to move)
    if loc.liquidity_above and loc.liquidity_below:
        plausibility += 0.1
        factors.append("Liquidity targets both directions")
    elif loc.liquidity_above or loc.liquidity_below:
        plausibility += 0.05
        factors.append(f"Liquidity target {loc.nearest_liquidity_direction}")

    # - Institutional alignment
    if loc.institutional_alignment in ("BULLISH", "BEARISH"):
        plausibility += 0.1
        factors.append(f"Institutional alignment: {loc.institutional_alignment}")

    # Intraday is less plausible when:
    # - No structural support
    if not htf.bos_active and htf.macro_bias in ("NEUTRAL", "CONFLICTED", ""):
        plausibility -= 0.1
        conflicts.append("No structural support for sustained move")

    # - Contracting volatility
    if beh.volatility_state == "CONTRACTION":
        plausibility -= 0.1
        conflicts.append("Volatility contraction limits intraday range")

    # - Equilibrium (no directional bias in range)
    if loc.premium_discount == "EQUILIBRIUM":
        plausibility -= 0.05
        conflicts.append("Equilibrium position (no directional edge)")

    # Target framework
    if loc.liquidity_above or loc.liquidity_below:
        target = "LIQUIDITY_TARGET"
    elif loc.supply_zones_nearby > 0 or loc.demand_zones_nearby > 0:
        target = "OPPOSING_ZONE"
    else:
        target = "FIXED_RR"

    return HorizonCandidate(
        horizon=INTRADAY,
        plausibility=max(0.0, min(1.0, plausibility)),
        expected_move_min_pips=INTRADAY_PROFILE.expected_move_min_pips,
        expected_move_max_pips=INTRADAY_PROFILE.expected_move_max_pips,
        stop_framework=INTRADAY_PROFILE.stop_source,
        target_framework=target,
        supporting_factors=factors,
        conflicting_factors=conflicts,
    )


def _evaluate_extended(ctx: V3MarketContext, opp: OpportunityAssessment) -> HorizonCandidate:
    """Evaluate EXTENDED plausibility (initial research prior = 0.10)."""
    factors: list[str] = []
    conflicts: list[str] = []
    # Initial research prior: EXTENDED movements (50+ pips) are rare events
    # requiring strong HTF evidence. This low prior ensures extended is only
    # selected when substantial evidence accumulates. Research will determine
    # how often institutional zone entries produce extended continuations.
    plausibility = 0.1

    htf = ctx.htf_structure
    loc = ctx.location
    beh = ctx.behaviour

    # Extended is more plausible when:
    # - H4 authority with clear trend
    if htf.authority_timeframe == "H4" and htf.macro_bias in ("BULLISH", "BEARISH"):
        plausibility += 0.25
        factors.append(f"H4 authority: {htf.macro_bias}")

    # - Strong structural alignment
    if htf.structure_alignment > 0.6:
        plausibility += 0.15
        factors.append(f"Strong alignment ({htf.structure_alignment:.2f})")

    # - BOS with macro agreement
    if htf.bos_active and htf.macro_bias in ("BULLISH", "BEARISH"):
        plausibility += 0.1
        factors.append("BOS + macro agreement")

    # - High quality opportunity
    if opp.assessment_state == HIGH_QUALITY_CONTEXT:
        plausibility += 0.1
        factors.append("High quality context")

    # - Expanding volatility
    if beh.volatility_state == "EXPANSION" or beh.displacement_active:
        plausibility += 0.1
        factors.append("Expanding/displacement environment")

    # Extended is less plausible when:
    # - Ranging regime
    if beh.regime == "RANGING":
        plausibility -= 0.15
        conflicts.append("Ranging regime limits extended moves")

    # - Conflicted macro
    if htf.macro_bias == "CONFLICTED":
        plausibility -= 0.2
        conflicts.append("Macro conflict prevents sustained direction")

    # - Mixed context
    if opp.assessment_state == MIXED_CONTEXT:
        plausibility -= 0.1
        conflicts.append("Mixed context insufficient for extended thesis")

    # - No H4 authority
    if htf.authority_timeframe != "H4":
        plausibility -= 0.1
        conflicts.append("No H4 authority")

    target = "MAJOR_LIQUIDITY" if loc.liquidity_above or loc.liquidity_below else "STRUCTURAL_TARGET"

    return HorizonCandidate(
        horizon=EXTENDED,
        plausibility=max(0.0, min(1.0, plausibility)),
        expected_move_min_pips=EXTENDED_PROFILE.expected_move_min_pips,
        expected_move_max_pips=EXTENDED_PROFILE.expected_move_max_pips,
        stop_framework=EXTENDED_PROFILE.stop_source,
        target_framework=target,
        supporting_factors=factors,
        conflicting_factors=conflicts,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════


def _estimate_spread_risk(ctx: V3MarketContext, profile: HorizonProfile) -> float:
    """Estimate spread/risk ratio for a profile."""
    spread_pips = 1.0  # Default 1 pip for major FX
    mid_stop = (profile.typical_stop_pips_min + profile.typical_stop_pips_max) / 2
    if mid_stop <= 0:
        return 1.0
    return spread_pips / mid_stop


def _assess_volatility_fit(spread_risk: float, profile: HorizonProfile) -> str:
    """Assess whether volatility/spread fits the horizon."""
    if spread_risk <= profile.max_spread_risk_ratio:
        return "SUITABLE"
    elif spread_risk <= profile.max_spread_risk_ratio * 1.5:
        return "MARGINAL"
    else:
        return "UNSUITABLE"


def _duration_class(profile: HorizonProfile) -> str:
    """Map profile to duration class."""
    if profile.expected_duration_max_minutes <= 60:
        return "SHORT"
    elif profile.expected_duration_max_minutes <= 600:
        return "MEDIUM"
    else:
        return "LONG"
