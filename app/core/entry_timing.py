"""
Entry Timing Classification — Post-trade analytical tagging.

Classifies each trade entry into timing buckets for cohort analysis:
  - EARLY: Fast displacement, strong body, low wick (momentum entry)
  - MID: Balanced confirmation, moderate metrics (continuation/retest)
  - LATE: Mature confirmation, high wick, extended structure (delayed entry)

IMPORTANT:
  This module is STRICTLY OBSERVATIONAL.
  It MUST NOT influence scoring, execution, or risk decisions.
  It exists solely for post-trade analysis and cohort grouping.

Classification is derived from:
  - confirmation_strength (STRONG / WEAK / INVALID)
  - body_pct (body as fraction of candle range)
  - wick_ratio (combined wick as fraction of range)
  - close_location (where close sits in the range, 0.0=low, 1.0=high)
"""

from __future__ import annotations

from typing import Literal

# ─── ENTRY TIMING TYPES ───────────────────────────────────────────────────────

EntryTiming = Literal["EARLY", "MID", "LATE"]

# ─── CLASSIFICATION THRESHOLDS ────────────────────────────────────────────────

# EARLY: Strong conviction, fast displacement, clean body, minimal wick
_EARLY_MIN_BODY_PCT = 0.70       # Body dominates the candle (70%+ of range)
_EARLY_MAX_WICK_RATIO = 0.30     # Minimal wick rejection
_EARLY_STRENGTH_REQUIRED = "STRONG"

# LATE: Weak or delayed confirmation, high wick, extended candle
_LATE_MAX_BODY_PCT = 0.55        # Body is smaller relative to range
_LATE_MIN_WICK_RATIO = 0.45      # Significant wick presence

# MID: Everything between EARLY and LATE thresholds


# ─── CLASSIFICATION FUNCTION ──────────────────────────────────────────────────

def classify_entry_timing(
    *,
    confirmation_strength: str | None = None,
    body_pct: float | None = None,
    wick_ratio: float | None = None,
    close_location: float | None = None,
) -> EntryTiming:
    """
    Classify entry timing bucket from confirmation metrics.

    This is a pure analytical function — NEVER affects execution.

    Args:
        confirmation_strength: "STRONG", "WEAK", or "INVALID"
        body_pct: Body as fraction of candle range (0.0–1.0)
        wick_ratio: Combined wick as fraction of range (0.0–1.0)
        close_location: Where close sits in range (0.0–1.0)

    Returns:
        "EARLY", "MID", or "LATE"

    Default: "MID" when data is insufficient for classification.
    """
    # Default to MID when data is missing
    if body_pct is None or wick_ratio is None:
        return "MID"

    # EARLY: Strong + high body dominance + low wick = fast displacement entry
    if (
        confirmation_strength == _EARLY_STRENGTH_REQUIRED
        and body_pct >= _EARLY_MIN_BODY_PCT
        and wick_ratio <= _EARLY_MAX_WICK_RATIO
    ):
        return "EARLY"

    # LATE: Weak body + high wick = delayed/extended entry
    if (
        body_pct <= _LATE_MAX_BODY_PCT
        and wick_ratio >= _LATE_MIN_WICK_RATIO
    ):
        return "LATE"

    # MID: Everything else (balanced confirmation)
    return "MID"
