"""
Macro Context Alignment — Pure interpretation function.

Computes how MN/W1/D1 macro context aligns with a proposed trade direction.
Produces a bounded confidence modifier (±0.20 max) and alignment classification.

This module is a PURE FUNCTION — no side effects, no imports from pipeline,
no MT5 calls, no state mutation.

Ownership: core/timeframes/macro_alignment.py
Dependencies: core/timeframes/types.py (MacroSnapshot only)
Must NOT import from: strategy_engine, pipeline, live_scanner, persistence
"""

from __future__ import annotations

from dataclasses import dataclass
from core.timeframes.types import MacroSnapshot


# ═══════════════════════════════════════════════════════════════
# OUTPUT TYPE
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class MacroAlignment:
    """
    Interpretation of macro context relative to a trade direction.

    Produced by: compute_macro_alignment()
    Consumed by: strategy engine (post-selection confidence modifier), persistence
    """

    monthly_alignment: str = "NEUTRAL"       # ALIGNED / OPPOSING / NEUTRAL
    weekly_alignment: str = "NEUTRAL"        # ALIGNED / OPPOSING / NEUTRAL
    daily_alignment: str = "NEUTRAL"         # ALIGNED / OPPOSING / NEUTRAL
    alignment_state: str = "NEUTRAL"         # FA / SA / PA / NEUTRAL / CONFLICTED / PO / SO / FO
    confidence_modifier: float = 0.0         # -0.20 to +0.20
    primary_influence: str = "NONE"          # MONTHLY / WEEKLY / DAILY / NONE
    is_conflicted: bool = False              # True if layers actively disagree
    data_quality: str = "UNAVAILABLE"        # COMPLETE / PARTIAL / STALE / UNAVAILABLE
    raw_score: float = 0.0                   # Weighted score before scaling
    narrative: str = ""                      # Human-readable one-liner


# ═══════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════

# Weights per layer (D1 most relevant to intraday, MN least)
_WEIGHT_MONTHLY = 0.25
_WEIGHT_WEEKLY = 0.35
_WEIGHT_DAILY = 0.40

# Strength threshold — below this, treat as NEUTRAL (too weak to be a signal)
_STRENGTH_THRESHOLD = 0.3

# Maximum strength contribution scaling (strength 0.7+ = full contribution)
_STRENGTH_FULL = 0.7

# Modifier scaling factor (raw_score ±1.0 → modifier ±0.15)
_MODIFIER_SCALE = 0.15

# Hard caps
_MODIFIER_CAP = 0.20
_CONFIDENCE_FLOOR = 0.40
_CONFIDENCE_CEILING = 1.00

# Conflict penalty (applied when layers actively disagree)
_CONFLICT_PENALTY = 0.033

# Staleness cap (when data is stale, max modifier is reduced)
_STALE_MODIFIER_CAP = 0.05


# ═══════════════════════════════════════════════════════════════
# MAIN FUNCTION
# ═══════════════════════════════════════════════════════════════


def compute_macro_alignment(
    macro: MacroSnapshot | None,
    trade_direction: str,
    current_time: float = 0.0,
) -> MacroAlignment:
    """
    Pure function: macro data + trade direction → alignment assessment.

    Args:
        macro: The MacroSnapshot (None = unavailable)
        trade_direction: "BULLISH" or "BEARISH"
        current_time: Current unix timestamp (for staleness check). 0 = skip staleness.

    Returns:
        MacroAlignment with bounded confidence_modifier.

    Contract:
        - Never gates strategy selection
        - Only modifies confidence (bounded ±0.20, floor 0.40)
        - Missing/weak data → NEUTRAL (no influence)
        - Pure function — no side effects
    """
    if macro is None:
        return MacroAlignment(
            data_quality="UNAVAILABLE",
            narrative="Macro context unavailable",
        )

    if not trade_direction or trade_direction not in ("BULLISH", "BEARISH"):
        return MacroAlignment(
            data_quality="PARTIAL",
            narrative="No trade direction for alignment computation",
        )

    # 1. Classify each layer
    mn_class = _classify_layer(macro.monthly_trend, macro.monthly_trend_strength, trade_direction)
    w1_class = _classify_layer(macro.weekly_trend, macro.weekly_trend_strength, trade_direction)
    d1_class = _classify_layer(macro.daily_bias, macro.daily_bias_strength, trade_direction)

    # 2. Compute strength-scaled contribution per layer
    mn_value = _direction_value(mn_class) * _strength_scale(macro.monthly_trend_strength)
    w1_value = _direction_value(w1_class) * _strength_scale(macro.weekly_trend_strength)
    d1_value = _direction_value(d1_class) * _strength_scale(macro.daily_bias_strength)

    # 3. Weighted raw score
    raw_score = (
        mn_value * _WEIGHT_MONTHLY +
        w1_value * _WEIGHT_WEEKLY +
        d1_value * _WEIGHT_DAILY
    )

    # 4. Detect conflict (aligned AND opposing present simultaneously)
    classifications = [mn_class, w1_class, d1_class]
    has_aligned = "ALIGNED" in classifications
    has_opposing = "OPPOSING" in classifications
    is_conflicted = has_aligned and has_opposing

    # 5. Apply conflict penalty (pushes toward slight opposition)
    if is_conflicted and abs(raw_score) < 0.15:
        if raw_score >= 0:
            raw_score -= _CONFLICT_PENALTY
        else:
            raw_score += _CONFLICT_PENALTY  # Toward zero, then past

    # 6. Assess data quality
    data_quality = _assess_data_quality(macro, current_time)

    # 7. Scale to modifier range and apply caps
    modifier = raw_score * _MODIFIER_SCALE

    # Apply staleness cap if data is stale
    if data_quality == "STALE":
        modifier = max(-_STALE_MODIFIER_CAP, min(_STALE_MODIFIER_CAP, modifier))
    else:
        modifier = max(-_MODIFIER_CAP, min(_MODIFIER_CAP, modifier))

    # 8. Derive alignment state label
    alignment_state = _derive_state(raw_score, is_conflicted)

    # 9. Determine primary influence
    contributions = {
        "MONTHLY": abs(mn_value * _WEIGHT_MONTHLY),
        "WEEKLY": abs(w1_value * _WEIGHT_WEEKLY),
        "DAILY": abs(d1_value * _WEIGHT_DAILY),
    }
    primary = max(contributions, key=contributions.get) if any(v > 0 for v in contributions.values()) else "NONE"

    # 10. Build narrative
    narrative = _build_narrative(alignment_state, primary, modifier, is_conflicted)

    return MacroAlignment(
        monthly_alignment=mn_class,
        weekly_alignment=w1_class,
        daily_alignment=d1_class,
        alignment_state=alignment_state,
        confidence_modifier=round(modifier, 4),
        primary_influence=primary,
        is_conflicted=is_conflicted,
        data_quality=data_quality,
        raw_score=round(raw_score, 4),
        narrative=narrative,
    )


def apply_macro_modifier(
    base_confidence: float,
    macro_modifier: float,
) -> float:
    """
    Apply macro modifier to base confidence with floor/ceiling constraints.

    Args:
        base_confidence: Strategy engine's raw confidence (0.0–1.0)
        macro_modifier: From MacroAlignment.confidence_modifier

    Returns:
        Final confidence clamped to [0.40, 1.00]
    """
    final = base_confidence + macro_modifier
    return max(_CONFIDENCE_FLOOR, min(_CONFIDENCE_CEILING, final))


# ═══════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════


def _classify_layer(trend: str, strength: float, trade_direction: str) -> str:
    """Classify a single macro layer as ALIGNED/OPPOSING/NEUTRAL relative to trade direction."""
    if not trend or trend == "NEUTRAL" or strength < _STRENGTH_THRESHOLD:
        return "NEUTRAL"

    if trend == trade_direction:
        return "ALIGNED"

    # Trend is non-empty, not NEUTRAL, not matching direction → opposing
    return "OPPOSING"


def _direction_value(classification: str) -> float:
    """Map classification to numeric value for weighted calculation."""
    if classification == "ALIGNED":
        return 1.0
    elif classification == "OPPOSING":
        return -1.0
    return 0.0


def _strength_scale(strength: float) -> float:
    """
    Scale contribution by signal strength.

    Strength < 0.3: treated as NEUTRAL (returns 0 via _classify_layer, but guard here too)
    Strength 0.3–0.7: proportional contribution
    Strength >= 0.7: full contribution (capped at 1.0)
    """
    if strength < _STRENGTH_THRESHOLD:
        return 0.0
    return min(1.0, strength / _STRENGTH_FULL)


def _assess_data_quality(macro: MacroSnapshot, current_time: float) -> str:
    """Assess quality of macro data."""
    # Check if any data exists at all
    has_monthly = macro.monthly_trend != "" and macro.monthly_trend_strength > 0
    has_weekly = macro.weekly_trend != "" and macro.weekly_trend_strength > 0
    has_daily = macro.daily_bias != "" and macro.daily_bias_strength > 0

    if not has_monthly and not has_weekly and not has_daily:
        return "UNAVAILABLE"

    # Staleness check (D1 older than 2 days = stale)
    if current_time > 0 and macro.bar_time > 0:
        age_seconds = current_time - macro.bar_time
        if age_seconds > 172800:  # 2 days
            return "STALE"

    if has_monthly and has_weekly and has_daily:
        return "COMPLETE"

    return "PARTIAL"


def _derive_state(raw_score: float, is_conflicted: bool) -> str:
    """Derive human-readable alignment state from raw score."""
    if is_conflicted and abs(raw_score) < 0.15:
        return "CONFLICTED"

    if raw_score >= 0.70:
        return "FULL_ALIGNMENT"
    elif raw_score >= 0.40:
        return "STRONG_ALIGNMENT"
    elif raw_score >= 0.15:
        return "PARTIAL_ALIGNMENT"
    elif raw_score > -0.15:
        return "NEUTRAL"
    elif raw_score > -0.40:
        return "PARTIAL_OPPOSITION"
    elif raw_score > -0.70:
        return "STRONG_OPPOSITION"
    else:
        return "FULL_OPPOSITION"


def _build_narrative(state: str, primary: str, modifier: float, conflicted: bool) -> str:
    """Build a human-readable one-liner describing macro alignment."""
    direction = "supports" if modifier > 0 else "opposes" if modifier < 0 else "neutral for"

    if state == "FULL_ALIGNMENT":
        return f"All macro layers support direction (primary: {primary})"
    elif state == "FULL_OPPOSITION":
        return f"All macro layers oppose direction (primary: {primary})"
    elif state == "CONFLICTED":
        return f"Macro layers conflict — {primary} is dominant influence"
    elif state == "NEUTRAL":
        return "Macro context is neutral — no directional signal"
    else:
        return f"Macro {direction} trade (primary: {primary}, state: {state})"
