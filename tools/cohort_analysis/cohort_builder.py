"""
Cohort Builder — Converts trade/decision objects into CohortKey for policy lookup.

PURE mapping layer. NO trading logic. NO scoring changes. NO side effects.
"""

from __future__ import annotations

from typing import Any

from tools.cohort_analysis.cohort_policy_types import CohortKey


def build_cohort_from_trade(decision: Any) -> CohortKey:
    """
    Extract CohortKey from a trade decision object or dict.

    Handles:
      - UnifiedDecision objects (with .confirmation, .entry_timing attributes)
      - Dict-based audit records (with nested "confirmation" dict)

    Fallbacks:
      - confirmation_strength → "STRONG"
      - entry_timing → "MID"
      - market_regime → "UNKNOWN"

    Args:
        decision: UnifiedDecision object, dict audit record, or any object
                  with confirmation/entry_timing/engine_state attributes.

    Returns:
        CohortKey with resolved dimensions.
    """
    strength = _extract_strength(decision)
    timing = _extract_timing(decision)
    regime = _extract_regime(decision)

    return CohortKey(
        confirmation_strength=strength,
        entry_timing=timing,
        market_regime=regime,
    )


def _extract_strength(decision: Any) -> str:
    """Extract confirmation_strength with fallback."""
    # Dict-based record
    if isinstance(decision, dict):
        conf = decision.get("confirmation") or {}
        return conf.get("strength") or "STRONG"

    # Object with .confirmation attribute
    confirmation = getattr(decision, "confirmation", None)
    if confirmation is not None:
        strength = getattr(confirmation, "strength", None)
        if strength:
            return str(strength)

    return "STRONG"


def _extract_timing(decision: Any) -> str:
    """Extract entry_timing with fallback."""
    # Dict-based record
    if isinstance(decision, dict):
        return decision.get("entry_timing") or "MID"

    # Object attribute
    timing = getattr(decision, "entry_timing", None)
    if timing:
        return str(timing)

    return "MID"


def _extract_regime(decision: Any) -> str:
    """Extract market_regime with fallback."""
    # Dict-based record
    if isinstance(decision, dict):
        # Direct field
        regime = decision.get("market_regime")
        if regime:
            return regime
        # Nested in engine_state
        engine_state = decision.get("engine_state") or {}
        raw = engine_state.get("regime_state", "")
        return _normalize_regime(raw)

    # Object with .market_regime
    regime = getattr(decision, "market_regime", None)
    if regime:
        return str(regime)

    # Object with .engine_state
    engine_state = getattr(decision, "engine_state", None)
    if engine_state is not None:
        raw = getattr(engine_state, "regime_state", "")
        return _normalize_regime(raw)

    return "UNKNOWN"


def _normalize_regime(raw: str) -> str:
    """Normalize regime state string to TRENDING / RANGING / UNKNOWN."""
    if not raw:
        return "UNKNOWN"
    upper = raw.upper()
    if upper in ("TRENDING", "TREND_UP", "TREND_DOWN"):
        return "TRENDING"
    if upper in ("RANGING", "CHOPPY"):
        return "RANGING"
    return "UNKNOWN"
