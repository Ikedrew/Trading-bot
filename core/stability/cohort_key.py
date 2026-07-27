"""
Cohort Key Builder — Pure string composer for cohort classification.

Fully isolated. No side effects. No logging. No engine imports.
Not a scoring system. Not a validator. Not a classifier.
Only a mapper: existing fields → deterministic cohort key string.
"""

from __future__ import annotations

from typing import Any


def build_cohort_key(decision: Any) -> str:
    """
    Build a deterministic cohort classification key from decision attributes.

    Reads only:
        decision.confirmation_strength
        decision.entry_timing
        decision.market_regime

    Output format:
        "{confirmation_strength}+{entry_timing}+{market_regime}"

    Always returns exactly 3 segments joined by "+".
    Missing or falsy values default to "UNKNOWN".
    All values normalized to uppercase stripped strings.

    Args:
        decision: Any object with optional confirmation_strength,
                  entry_timing, and market_regime attributes.

    Returns:
        Cohort key string, e.g. "STRONG+EARLY+TRENDING".
    """
    strength = _normalize(getattr(decision, "confirmation_strength", None))
    timing = _normalize(getattr(decision, "entry_timing", None))
    regime = _normalize(getattr(decision, "market_regime", None))

    return f"{strength}+{timing}+{regime}"


def _normalize(value: Any) -> str:
    """Normalize a value to uppercase stripped string. Falsy → 'UNKNOWN'."""
    if value is None:
        return "UNKNOWN"

    result = str(value).upper().strip()

    if not result:
        return "UNKNOWN"

    return result
