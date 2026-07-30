"""
V3 Entry Assessment Builder — Evaluates confirmation behaviour from context + candles.

Consumes V3MarketContext + HorizonAssessment + OpportunityAssessment.
Evaluates multiple trigger hypotheses as candidates.
Research determines which confirmation produces the best outcomes.

Does NOT create trade signals. Only observes and classifies confirmation behaviour.
"""

from __future__ import annotations

import logging
from typing import Any

from core.v3_shadow.context_models import V3MarketContext
from core.v3_shadow.opportunity_models import (
    OpportunityAssessment,
    HIGH_QUALITY_CONTEXT,
    INTERESTING_CONTEXT,
    INSUFFICIENT_CONTEXT,
    LOW_QUALITY_CONTEXT,
)
from core.v3_shadow.horizon_models import HorizonAssessment, NO_HORIZON
from core.v3_shadow.entry_models import (
    EntryAssessment,
    EntryCandidate,
    VALID_ENTRY_CONFIRMATION,
    WEAK_ENTRY_CONFIRMATION,
    NO_ENTRY_CONFIRMATION,
    INSUFFICIENT_ENTRY_DATA,
    TRIGGER_BOS,
    TRIGGER_CHOCH,
    TRIGGER_DISPLACEMENT,
    TRIGGER_REJECTION,
    TRIGGER_RETEST,
    TRIGGER_MOMENTUM,
    TRIGGER_NONE,
    BEHAVIOUR_UNKNOWN,
    get_behaviour_type,
    _ENTRY_SCHEMA_VERSION,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════


def build_entry_assessment(
    market_context: V3MarketContext,
    opportunity: OpportunityAssessment,
    horizon: HorizonAssessment,
    *,
    current_price: float = 0.0,
) -> EntryAssessment:
    """
    Evaluate confirmation behaviour for an opportunity.

    Evaluates all trigger hypotheses. Records which are present.
    Does NOT decide whether to trade.
    """
    # Gate: no horizon or insufficient opportunity → no entry assessment
    if horizon.selected_horizon == NO_HORIZON:
        return EntryAssessment(
            symbol=market_context.symbol,
            timestamp_utc=market_context.timestamp_utc,
            entry_state=INSUFFICIENT_ENTRY_DATA,
            observations=["No horizon — entry assessment not applicable"],
        )

    if opportunity.assessment_state in (INSUFFICIENT_CONTEXT, LOW_QUALITY_CONTEXT):
        return EntryAssessment(
            symbol=market_context.symbol,
            timestamp_utc=market_context.timestamp_utc,
            entry_state=INSUFFICIENT_ENTRY_DATA,
            observations=["Insufficient context — no entry evaluation"],
        )

    # Determine direction from context
    direction = _derive_direction(market_context)

    # Evaluate all trigger candidates
    candidates = [
        _evaluate_bos(market_context, direction),
        _evaluate_displacement(market_context, direction),
        _evaluate_rejection(market_context, direction),
        _evaluate_momentum(market_context, direction),
        _evaluate_retest(market_context, direction),
    ]

    # Find best trigger
    detected = [c for c in candidates if c.detected]
    if detected:
        best = max(detected, key=lambda c: c.strength)
        primary_trigger = best.trigger_type
        trigger_tf = best.timeframe
        trigger_strength = best.strength
    else:
        best = None
        primary_trigger = TRIGGER_NONE
        trigger_tf = ""
        trigger_strength = 0.0

    # Entry location
    entry_at_zone = market_context.location.inside_institutional_zone

    # Alignment scores
    location_alignment = _compute_location_alignment(market_context, direction)
    horizon_alignment = _compute_horizon_alignment(horizon, detected)

    # Quality score
    quality = _compute_quality(
        trigger_strength, location_alignment, horizon_alignment,
        len(detected), entry_at_zone
    )

    # Classify state
    entry_state = _classify_entry(quality, detected, trigger_strength)

    # Evidence
    factors: list[str] = []
    conflicts: list[str] = []
    for c in detected:
        factors.extend(c.supporting)
    for c in candidates:
        if not c.detected:
            conflicts.extend(c.conflicting)

    # Confidence
    confidence = opportunity.confidence * 0.4 + quality * 0.6

    observations = _generate_observations(
        entry_state, primary_trigger, trigger_tf, direction, detected)

    return EntryAssessment(
        symbol=market_context.symbol,
        timestamp_utc=market_context.timestamp_utc,
        schema_version=_ENTRY_SCHEMA_VERSION,
        direction=direction,
        entry_state=entry_state,
        entry_behaviour_type=get_behaviour_type(primary_trigger),
        primary_trigger=primary_trigger,
        trigger_timeframe=trigger_tf,
        trigger_strength=round(trigger_strength, 4),
        entry_price=current_price,
        entry_at_zone=entry_at_zone,
        location_alignment=round(location_alignment, 4),
        horizon_alignment=round(horizon_alignment, 4),
        entry_quality_score=round(quality, 4),
        candidates=candidates,
        confidence=round(confidence, 4),
        supporting_factors=factors[:10],
        conflicting_factors=conflicts[:5],
        observations=observations,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# DIRECTION DERIVATION
# ═══════════════════════════════════════════════════════════════════════════════


def _derive_direction(ctx: V3MarketContext) -> str:
    """Derive trade direction from context alignment (NOT from entry)."""
    htf = ctx.htf_structure
    loc = ctx.location

    # Institutional alignment is strongest signal
    if loc.institutional_alignment in ("BULLISH", "BEARISH"):
        return loc.institutional_alignment

    # Macro bias
    if htf.macro_bias in ("BULLISH", "BEARISH"):
        return htf.macro_bias

    # BOS direction
    if htf.bos_active and htf.bos_direction:
        return htf.bos_direction

    return "NEUTRAL"


# ═══════════════════════════════════════════════════════════════════════════════
# TRIGGER EVALUATIONS (competing hypotheses)
# ═══════════════════════════════════════════════════════════════════════════════


def _evaluate_bos(ctx: V3MarketContext, direction: str) -> EntryCandidate:
    """Evaluate BOS confirmation hypothesis."""
    htf = ctx.htf_structure
    detected = htf.bos_active
    strength = 0.0
    supporting: list[str] = []
    conflicting: list[str] = []

    if detected:
        # BOS aligns with derived direction?
        if htf.bos_direction == direction:
            strength = 0.8
            supporting.append(f"BOS {htf.bos_direction} aligns with direction")
        else:
            strength = 0.3
            conflicting.append(f"BOS {htf.bos_direction} opposes direction {direction}")
    else:
        conflicting.append("No BOS confirmation")

    return EntryCandidate(
        trigger_type=TRIGGER_BOS,
        detected=detected,
        strength=round(strength, 4),
        timeframe="H1",
        direction=htf.bos_direction if detected else "",
        supporting=supporting,
        conflicting=conflicting,
    )


def _evaluate_displacement(ctx: V3MarketContext, direction: str) -> EntryCandidate:
    """Evaluate displacement candle hypothesis."""
    m15 = ctx.htf_structure  # Using available displacement data
    m5_beh = ctx.behaviour
    detected = m5_beh.displacement_active
    strength = 0.0
    supporting: list[str] = []
    conflicting: list[str] = []

    if detected:
        mag = m5_beh.displacement_magnitude_atr
        if m5_beh.displacement_direction == direction:
            strength = min(0.9, 0.5 + mag * 0.1)
            supporting.append(f"Displacement {m5_beh.displacement_direction} ({mag:.1f} ATR)")
        else:
            strength = 0.2
            conflicting.append(f"Displacement opposes direction")
    else:
        conflicting.append("No displacement detected")

    return EntryCandidate(
        trigger_type=TRIGGER_DISPLACEMENT,
        detected=detected,
        strength=round(strength, 4),
        timeframe="M5",
        direction=m5_beh.displacement_direction if detected else "",
        supporting=supporting,
        conflicting=conflicting,
    )


def _evaluate_rejection(ctx: V3MarketContext, direction: str) -> EntryCandidate:
    """Evaluate rejection candle hypothesis."""
    # M5Understanding has rejection data
    # Access via behaviour since M5 rejection feeds into context
    m5 = getattr(ctx, "_m5_raw", None)  # Not directly available — use what we have
    # The MarketUnderstanding.m5.rejection_present flows into behaviour observations
    # For now, check if momentum direction suggests rejection (reversal after wick)
    beh = ctx.behaviour
    loc = ctx.location

    # Rejection is implied when: at institutional zone + momentum shift
    detected = loc.inside_institutional_zone and beh.momentum_strength > 0.4
    strength = 0.0
    supporting: list[str] = []
    conflicting: list[str] = []

    if detected:
        if beh.momentum_direction == direction:
            strength = 0.7
            supporting.append(f"Rejection implied: at zone + momentum {direction}")
        else:
            strength = 0.3
            conflicting.append("Momentum opposes expected direction at zone")
    else:
        if not loc.inside_institutional_zone:
            conflicting.append("Not at institutional zone for rejection")

    return EntryCandidate(
        trigger_type=TRIGGER_REJECTION,
        detected=detected,
        strength=round(strength, 4),
        timeframe="M5",
        direction=direction if detected and strength > 0.5 else "",
        supporting=supporting,
        conflicting=conflicting,
    )


def _evaluate_momentum(ctx: V3MarketContext, direction: str) -> EntryCandidate:
    """Evaluate momentum shift hypothesis."""
    beh = ctx.behaviour
    detected = (
        beh.momentum_direction in ("BULLISH", "BEARISH") and
        beh.momentum_strength > 0.5
    )
    strength = 0.0
    supporting: list[str] = []
    conflicting: list[str] = []

    if detected:
        if beh.momentum_direction == direction:
            strength = 0.6
            supporting.append(f"Momentum {beh.momentum_direction} ({beh.momentum_strength:.2f})")
        else:
            strength = 0.2
            conflicting.append(f"Momentum opposes: {beh.momentum_direction} vs {direction}")
            detected = False  # Opposing momentum = not a valid trigger
    else:
        conflicting.append("No clear momentum shift")

    return EntryCandidate(
        trigger_type=TRIGGER_MOMENTUM,
        detected=detected,
        strength=round(strength, 4),
        timeframe="M5",
        direction=beh.momentum_direction if detected else "",
        supporting=supporting,
        conflicting=conflicting,
    )


def _evaluate_retest(ctx: V3MarketContext, direction: str) -> EntryCandidate:
    """Evaluate retest entry hypothesis (price returned to broken level)."""
    htf = ctx.htf_structure
    loc = ctx.location

    # Retest: BOS occurred AND price is back at institutional zone
    detected = htf.bos_active and loc.inside_institutional_zone
    strength = 0.0
    supporting: list[str] = []
    conflicting: list[str] = []

    if detected:
        if htf.bos_direction == direction:
            strength = 0.85
            supporting.append(f"Retest: BOS {htf.bos_direction} + back at zone")
        else:
            strength = 0.3
            conflicting.append("BOS direction mismatches")
    else:
        if not htf.bos_active:
            conflicting.append("No BOS for retest")
        if not loc.inside_institutional_zone:
            conflicting.append("Not at zone for retest")

    return EntryCandidate(
        trigger_type=TRIGGER_RETEST,
        detected=detected,
        strength=round(strength, 4),
        timeframe="M15",
        direction=htf.bos_direction if detected and strength > 0.5 else "",
        supporting=supporting,
        conflicting=conflicting,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ALIGNMENT SCORING
# ═══════════════════════════════════════════════════════════════════════════════


def _compute_location_alignment(ctx: V3MarketContext, direction: str) -> float:
    """How well does entry align with location context."""
    loc = ctx.location
    score = 0.0

    if loc.inside_institutional_zone:
        score += 0.4
    if loc.institutional_alignment == direction:
        score += 0.3
    if loc.premium_discount == "DISCOUNT" and direction == "BULLISH":
        score += 0.15
    elif loc.premium_discount == "PREMIUM" and direction == "BEARISH":
        score += 0.15
    if loc.zone_quality > 0.5:
        score += 0.15

    return min(1.0, score)


def _compute_horizon_alignment(horizon: HorizonAssessment, detected: list) -> float:
    """How well does entry align with horizon expectations."""
    score = 0.3  # Baseline
    if detected:
        score += 0.3  # Confirmation exists
    if horizon.confidence > 0.5:
        score += 0.2
    if len(detected) >= 2:
        score += 0.2  # Multiple confirmations
    return min(1.0, score)


def _compute_quality(
    trigger_strength: float,
    location_alignment: float,
    horizon_alignment: float,
    num_triggers: int,
    at_zone: bool,
) -> float:
    """Composite entry quality score."""
    quality = 0.0
    quality += trigger_strength * 0.4
    quality += location_alignment * 0.3
    quality += horizon_alignment * 0.2
    if at_zone:
        quality += 0.1
    return min(1.0, quality)


# ═══════════════════════════════════════════════════════════════════════════════
# STATE CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════


def _classify_entry(quality: float, detected: list, trigger_strength: float) -> str:
    """Classify entry state from quality and trigger presence."""
    if not detected:
        return NO_ENTRY_CONFIRMATION
    if quality >= 0.6 and trigger_strength >= 0.6:
        return VALID_ENTRY_CONFIRMATION
    if quality >= 0.3 or trigger_strength >= 0.4:
        return WEAK_ENTRY_CONFIRMATION
    return NO_ENTRY_CONFIRMATION


# ═══════════════════════════════════════════════════════════════════════════════
# OBSERVATIONS
# ═══════════════════════════════════════════════════════════════════════════════


def _generate_observations(
    state: str, trigger: str, tf: str, direction: str, detected: list
) -> list[str]:
    """Generate human-readable observations."""
    obs = [f"Entry: {state}"]
    if trigger != TRIGGER_NONE:
        obs.append(f"Trigger: {trigger} ({tf})")
    if direction:
        obs.append(f"Direction: {direction}")
    if detected:
        obs.append(f"Confirmations: {len(detected)}")
    return obs
