"""
Calibration Engine — Translates cohort analysis into bounded parameter recommendations.

IMPORTANT:
  - NO execution logic
  - NO live trade modification
  - NO direct engine coupling
  - OUTPUT ONLY: CalibrationRecommendation objects

All recommendations are bounded (no extreme changes) and must be
manually reviewed or gated before applying to config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ─── RECOMMENDATION TYPE ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class CalibrationRecommendation:
    """Bounded parameter recommendation for a cohort."""

    # Cohort identity
    cohort_key: str

    # Current parameters (baseline)
    break_even_trigger_rr: float
    trailing_start_rr: float
    trailing_step: float
    partial_tp_enabled: bool

    # Recommended parameters
    recommended_break_even_rr: float
    recommended_trailing_start_rr: float
    recommended_trailing_step: float
    recommended_partial_tp: bool

    # Confidence metrics
    sample_size: int
    expectancy: float
    variance: float
    confidence_score: float  # 0.0–1.0


# ─── BOUNDS (safety limits) ───────────────────────────────────────────────────

_MIN_BE_RR = 0.5
_MAX_BE_RR = 2.0
_MIN_TRAIL_START = 0.5
_MAX_TRAIL_START = 3.0
_MIN_TRAIL_STEP = 0.0002
_MAX_TRAIL_STEP = 0.0020


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ─── CONFIDENCE CALCULATION ───────────────────────────────────────────────────

def _compute_confidence(sample_size: int, variance: float) -> float:
    """Compute confidence score (0–1) from sample size and variance."""
    # More trades + lower variance = higher confidence
    size_factor = min(1.0, sample_size / 30.0)  # Full confidence at 30+ trades
    var_penalty = min(0.5, variance * 0.1)       # High variance reduces confidence
    return round(max(0.0, size_factor - var_penalty), 3)


# ─── RULE-BASED ADJUSTMENT LOGIC ─────────────────────────────────────────────

def _recommend_for_cohort(
    cohort_key: str,
    expectancy: float,
    variance: float,
    sample_size: int,
    mfe_mean: float = 0.0,
) -> CalibrationRecommendation:
    """
    Generate bounded recommendation for a single cohort.

    Rules:
    - High expectancy + low variance → expand (wider trail, delayed BE)
    - Low expectancy + high variance → protect (early BE, tighter trail)
    - Negative expectancy → maximum protection
    - High variance regardless → reduce aggressiveness
    """
    # Baseline defaults (current system: all disabled = 0.0)
    base_be = 1.0
    base_trail_start = 1.5
    base_trail_step = 0.0005
    base_partial = False

    # Parse cohort dimensions
    parts = cohort_key.upper().split("+") if "+" in cohort_key else [cohort_key]
    strength = parts[0] if len(parts) > 0 else "UNKNOWN"
    timing = parts[1] if len(parts) > 1 else "UNKNOWN"
    regime = parts[2] if len(parts) > 2 else "UNKNOWN"

    # ─── STRONG + EARLY + TRENDING: Runner profile ────────────────
    if strength == "STRONG" and timing == "EARLY" and regime == "TRENDING":
        rec_be = 1.5           # Delayed BE — let momentum develop
        rec_trail_start = 1.0  # Start trailing early at 1R
        rec_trail_step = 0.0004  # Moderate trail distance
        rec_partial = False    # Don't cut runners

    # ─── STRONG + MID + TRENDING: Extension profile ───────────────
    elif strength == "STRONG" and timing == "MID":
        rec_be = 1.0           # Standard BE at 1R
        rec_trail_start = 1.5  # Trail after 1.5R
        rec_trail_step = 0.0005
        rec_partial = True     # Partial at TP1

    # ─── STRONG + LATE: Reduced runner ────────────────────────────
    elif strength == "STRONG" and timing == "LATE":
        rec_be = 0.8           # Earlier BE (momentum may be exhausting)
        rec_trail_start = 1.0
        rec_trail_step = 0.0006  # Tighter trail
        rec_partial = True

    # ─── WEAK + any + RANGING: Maximum protection ─────────────────
    elif strength == "WEAK" and regime == "RANGING":
        rec_be = 0.5           # Earliest possible BE
        rec_trail_start = 2.0  # Only trail if significant move
        rec_trail_step = 0.0008  # Tight
        rec_partial = True     # Aggressive partials

    # ─── WEAK + LATE: Protection priority ─────────────────────────
    elif strength == "WEAK" and timing == "LATE":
        rec_be = 0.5
        rec_trail_start = 2.5  # Very conservative trailing
        rec_trail_step = 0.0010
        rec_partial = True

    # ─── WEAK + MID + TRENDING: Cautious ──────────────────────────
    elif strength == "WEAK" and timing == "MID" and regime == "TRENDING":
        rec_be = 0.7
        rec_trail_start = 1.5
        rec_trail_step = 0.0006
        rec_partial = True

    # ─── Default: Standard profile ────────────────────────────────
    else:
        rec_be = base_be
        rec_trail_start = base_trail_start
        rec_trail_step = base_trail_step
        rec_partial = base_partial

    # ─── VARIANCE OVERRIDE: High variance = reduce aggressiveness ─
    if variance > 2.0:
        rec_be = max(_MIN_BE_RR, rec_be - 0.3)       # Earlier BE
        rec_trail_start = min(_MAX_TRAIL_START, rec_trail_start + 0.5)  # Later trail
        rec_trail_step = min(_MAX_TRAIL_STEP, rec_trail_step * 1.3)     # Tighter
        rec_partial = True

    # ─── NEGATIVE EXPECTANCY: Maximum safety ──────────────────────
    if expectancy < 0:
        rec_be = _MIN_BE_RR
        rec_trail_start = _MAX_TRAIL_START
        rec_trail_step = _MAX_TRAIL_STEP
        rec_partial = True

    # ─── Apply bounds ─────────────────────────────────────────────
    rec_be = _clamp(rec_be, _MIN_BE_RR, _MAX_BE_RR)
    rec_trail_start = _clamp(rec_trail_start, _MIN_TRAIL_START, _MAX_TRAIL_START)
    rec_trail_step = _clamp(rec_trail_step, _MIN_TRAIL_STEP, _MAX_TRAIL_STEP)

    confidence = _compute_confidence(sample_size, variance)

    return CalibrationRecommendation(
        cohort_key=cohort_key,
        break_even_trigger_rr=base_be,
        trailing_start_rr=base_trail_start,
        trailing_step=base_trail_step,
        partial_tp_enabled=base_partial,
        recommended_break_even_rr=round(rec_be, 3),
        recommended_trailing_start_rr=round(rec_trail_start, 3),
        recommended_trailing_step=round(rec_trail_step, 6),
        recommended_partial_tp=rec_partial,
        sample_size=sample_size,
        expectancy=round(expectancy, 4),
        variance=round(variance, 4),
        confidence_score=confidence,
    )


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def generate_cohort_recommendations(
    cohort_data: dict[str, dict[str, Any]],
) -> list[CalibrationRecommendation]:
    """
    Generate bounded parameter recommendations for all cohorts.

    Args:
        cohort_data: Dict mapping cohort_key (str) → stats dict with:
            - expectancy (float)
            - variance (float)
            - trade_count / sample_size (int)
            - mfe_mean (float, optional)

    Returns:
        List of CalibrationRecommendation (one per cohort with sufficient data).
    """
    recommendations: list[CalibrationRecommendation] = []

    for cohort_key, stats in cohort_data.items():
        sample_size = stats.get("trade_count") or stats.get("sample_size", 0)

        if sample_size < 3:
            continue  # Insufficient data

        expectancy = float(stats.get("expectancy", 0.0))
        variance = float(stats.get("variance", 0.0))
        mfe_mean = float(stats.get("mfe_mean", 0.0))

        rec = _recommend_for_cohort(
            cohort_key=cohort_key,
            expectancy=expectancy,
            variance=variance,
            sample_size=sample_size,
            mfe_mean=mfe_mean,
        )
        recommendations.append(rec)

    return recommendations
