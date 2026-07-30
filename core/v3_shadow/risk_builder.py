"""
V3 Risk Assessment Builder — Evaluates risk geometry from horizon + context.

Consumes HorizonAssessment + V3MarketContext and produces RiskAssessment.
Evaluates:
    1. Stop distance (from horizon structure source)
    2. Target distance (from liquidity/zone targets)
    3. Risk/reward ratio
    4. Spread-to-risk ratio (critical cost metric)
    5. Overall risk quality

Initial assumptions are RESEARCH PRIORS. The research engine validates
which cost configurations produce positive outcomes.

Does NOT create trade signals or determine entry timing.
"""

from __future__ import annotations

import logging
from typing import Any

from core.v3_shadow.context_models import V3MarketContext
from core.v3_shadow.horizon_models import (
    HorizonAssessment,
    PROFILES,
    SCALP,
    INTRADAY,
    EXTENDED,
    NO_HORIZON,
)
from core.v3_shadow.risk_models import (
    RiskAssessment,
    ACCEPTABLE_RISK,
    MARGINAL_RISK,
    POOR_RISK,
    INSUFFICIENT_RISK_DATA,
    _RISK_SCHEMA_VERSION,
)

logger = logging.getLogger(__name__)

# Research priors: spread/risk thresholds (to be validated by outcomes)
_ACCEPTABLE_SPREAD_RISK = 0.20   # ≤20% of risk consumed by spread
_MARGINAL_SPREAD_RISK = 0.35     # 20-35% marginal
# >35% = poor (from V2 research: 48% was definitively non-viable)


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════


def build_risk_assessment(
    market_context: V3MarketContext,
    horizon: HorizonAssessment,
    *,
    spread_pips: float = 0.0,
) -> RiskAssessment:
    """
    Evaluate risk geometry viability for an opportunity.

    Args:
        market_context: V3MarketContext (for volatility, location, liquidity)
        horizon: HorizonAssessment (provides movement expectations + stop framework)
        spread_pips: Current spread in pips (from bid/ask)

    Returns:
        RiskAssessment — immutable risk geometry evaluation.
    """
    # Gate: no horizon = no risk assessment
    if horizon.selected_horizon == NO_HORIZON:
        return RiskAssessment(
            symbol=market_context.symbol,
            timestamp_utc=market_context.timestamp_utc,
            horizon=NO_HORIZON,
            risk_state=INSUFFICIENT_RISK_DATA,
            observations=["No horizon selected — risk assessment not applicable"],
        )

    profile = PROFILES.get(horizon.selected_horizon)
    if profile is None:
        return RiskAssessment(
            symbol=market_context.symbol,
            timestamp_utc=market_context.timestamp_utc,
            horizon=horizon.selected_horizon,
            risk_state=INSUFFICIENT_RISK_DATA,
            observations=["Unknown horizon profile"],
        )

    # ─── Stop distance estimation ─────────────────────────────────────
    stop_pips = _estimate_stop(profile, market_context)

    # ─── Target distance estimation ───────────────────────────────────
    target_pips, target_source = _estimate_target(profile, market_context, stop_pips)

    # ─── Risk/Reward ──────────────────────────────────────────────────
    rr = target_pips / stop_pips if stop_pips > 0 else 0.0

    # ─── Spread/Risk analysis ─────────────────────────────────────────
    if spread_pips <= 0:
        spread_pips = 1.0  # Default 1 pip for major FX pairs
    spread_risk = spread_pips / stop_pips if stop_pips > 0 else 1.0

    # ─── Cost-adjusted expectancy estimate ────────────────────────────
    # Rough: assumes 50% WR (neutral prior), RR from geometry, spread deducted
    base_wr = 0.50
    raw_expectancy = (base_wr * rr) - (1 - base_wr)
    cost_adjusted = raw_expectancy - spread_risk

    # ─── Risk quality scoring ─────────────────────────────────────────
    factors: list[str] = []
    conflicts: list[str] = []

    quality = 0.0

    # RR quality
    if rr >= 3.0:
        quality += 0.3
        factors.append(f"Strong RR ({rr:.1f}:1)")
    elif rr >= 2.0:
        quality += 0.2
        factors.append(f"Adequate RR ({rr:.1f}:1)")
    elif rr >= 1.5:
        quality += 0.1
        factors.append(f"Minimum RR ({rr:.1f}:1)")
    else:
        conflicts.append(f"Weak RR ({rr:.1f}:1)")

    # Spread/risk quality (most critical from V3 research)
    if spread_risk <= _ACCEPTABLE_SPREAD_RISK:
        quality += 0.35
        factors.append(f"Low spread impact ({spread_risk:.0%} of risk)")
    elif spread_risk <= _MARGINAL_SPREAD_RISK:
        quality += 0.15
        factors.append(f"Moderate spread impact ({spread_risk:.0%})")
    else:
        conflicts.append(f"High spread impact ({spread_risk:.0%} — historical failure zone)")

    # Stop logic
    if stop_pips >= profile.typical_stop_pips_min:
        quality += 0.15
        factors.append(f"Stop within profile range ({stop_pips:.1f} pips)")
    else:
        conflicts.append(f"Stop too tight ({stop_pips:.1f} < {profile.typical_stop_pips_min})")

    # Expected movement vs stop
    move_to_risk = horizon.expected_move_min_pips / stop_pips if stop_pips > 0 else 0
    if move_to_risk >= 2.0:
        quality += 0.1
        factors.append(f"Expected move covers risk well ({move_to_risk:.1f}x)")
    elif move_to_risk < 1.0:
        conflicts.append(f"Expected move barely covers risk ({move_to_risk:.1f}x)")

    # Cost-adjusted expectancy
    if cost_adjusted > 0:
        quality += 0.1
        factors.append(f"Positive cost-adjusted expectancy estimate ({cost_adjusted:+.3f})")
    elif cost_adjusted > -0.1:
        factors.append(f"Near-breakeven ({cost_adjusted:+.3f})")
    else:
        conflicts.append(f"Negative expectancy ({cost_adjusted:+.3f})")

    quality = max(0.0, min(1.0, quality))

    # ─── Risk state classification ────────────────────────────────────
    risk_state = _classify_risk(quality, spread_risk, rr, factors, conflicts)

    # ─── Confidence ───────────────────────────────────────────────────
    confidence = horizon.confidence * 0.7 + quality * 0.3

    # ─── Observations ─────────────────────────────────────────────────
    observations = [
        f"Risk: {risk_state}",
        f"Geometry: stop={stop_pips:.1f} target={target_pips:.1f} RR={rr:.1f}:1",
        f"Cost: spread={spread_pips:.1f} spread/risk={spread_risk:.0%}",
        f"Expectancy estimate: {cost_adjusted:+.3f}",
    ]

    return RiskAssessment(
        symbol=market_context.symbol,
        timestamp_utc=market_context.timestamp_utc,
        schema_version=_RISK_SCHEMA_VERSION,
        horizon=horizon.selected_horizon,
        expected_move_min_pips=horizon.expected_move_min_pips,
        expected_move_max_pips=horizon.expected_move_max_pips,
        stop_distance_pips=round(stop_pips, 2),
        stop_source=profile.stop_source,
        target_distance_pips=round(target_pips, 2),
        target_source=target_source,
        risk_reward_ratio=round(rr, 3),
        spread_cost_pips=round(spread_pips, 4),
        spread_to_risk_ratio=round(spread_risk, 4),
        cost_adjusted_expectancy=round(cost_adjusted, 4),
        risk_quality_score=round(quality, 4),
        risk_state=risk_state,
        supporting_factors=factors,
        conflicting_factors=conflicts,
        confidence=round(confidence, 4),
        observations=observations,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL
# ═══════════════════════════════════════════════════════════════════════════════


def _estimate_stop(profile: Any, ctx: V3MarketContext) -> float:
    """Estimate stop distance in pips from horizon profile and context."""
    # Use midpoint of profile's typical range as initial estimate
    mid_stop = (profile.typical_stop_pips_min + profile.typical_stop_pips_max) / 2

    # Adjust based on volatility context
    if ctx.behaviour.volatility_state == "EXPANSION":
        # Wider stop in expanding markets
        mid_stop *= 1.2
    elif ctx.behaviour.volatility_state == "CONTRACTION":
        # Tighter stop acceptable in contracting markets
        mid_stop *= 0.85

    return max(profile.typical_stop_pips_min, mid_stop)


def _estimate_target(profile: Any, ctx: V3MarketContext, stop_pips: float) -> tuple[float, str]:
    """Estimate target distance in pips and source."""
    loc = ctx.location

    # Prefer liquidity-based targets (from research: most meaningful)
    if loc.nearest_liquidity_distance_pips > 0 and loc.nearest_liquidity_distance_pips > stop_pips:
        return loc.nearest_liquidity_distance_pips, "LIQUIDITY_TARGET"

    # Opposing zone target
    # Use expected move range from profile as proxy
    mid_rr = (profile.typical_rr_min + profile.typical_rr_max) / 2
    target_from_rr = stop_pips * mid_rr

    # Prefer the expected move midpoint if it's realistic
    expected_mid = (profile.expected_move_min_pips + profile.expected_move_max_pips) / 2
    if expected_mid > stop_pips:
        return expected_mid, "EXPECTED_MOVEMENT"

    return target_from_rr, "FIXED_RR"


def _classify_risk(
    quality: float,
    spread_risk: float,
    rr: float,
    factors: list[str],
    conflicts: list[str],
) -> str:
    """Classify risk state from quality and geometry."""
    # Hard gates: spread/risk > 35% is always poor (from V2 research)
    if spread_risk > _MARGINAL_SPREAD_RISK:
        return POOR_RISK

    # RR below 1.5 is poor regardless of other factors
    if rr < 1.5 and rr > 0:
        return POOR_RISK

    # Quality-based classification
    if quality >= 0.60:
        return ACCEPTABLE_RISK
    elif quality >= 0.35:
        return MARGINAL_RISK
    elif len(factors) == 0:
        return INSUFFICIENT_RISK_DATA
    else:
        return POOR_RISK
