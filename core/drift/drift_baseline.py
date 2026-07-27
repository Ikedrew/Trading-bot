"""
Drift Baseline — Stores historical cohort performance baselines for comparison.

Used by the calibration system to detect performance drift:
    current_stats vs. historical_baseline → drift signal.

NO execution logic. NO live system modification. PURE data + computation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ─── BASELINE TYPE ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CohortBaseline:
    """Historical performance baseline for a single cohort."""

    cohort_key: str
    baseline_expectancy: float
    baseline_win_rate: float
    baseline_avg_r: float
    baseline_variance: float
    sample_size: int
    time_window: str  # e.g. "2024-01-01_to_2024-06-30"


# ─── CONFIGURATION ────────────────────────────────────────────────────────────

_MIN_SAMPLE_SIZE = 30  # Exclude cohorts with fewer trades


# ─── BASELINE BUILDER ─────────────────────────────────────────────────────────

def build_baselines(
    historical_cohort_data: dict[str, dict[str, Any]],
    min_sample_size: int = _MIN_SAMPLE_SIZE,
) -> dict[str, CohortBaseline]:
    """
    Build stable performance baselines from historical cohort data.

    Only includes cohorts meeting the minimum sample size threshold
    to prevent unstable baselines from polluting drift detection.

    Args:
        historical_cohort_data: Mapping of cohort_key → stats dict with:
            - expectancy (float): Average R per trade
            - win_rate (float): Win ratio 0.0–1.0
            - avg_r (float): Average R-multiple outcome
            - variance (float): Outcome variance
            - trade_count / sample_size (int): Number of historical trades
            - time_window (str, optional): Period description
        min_sample_size: Minimum trades required for inclusion (default: 30).

    Returns:
        Mapping of cohort_key → CohortBaseline for qualifying cohorts.
    """
    baselines: dict[str, CohortBaseline] = {}

    for cohort_key, stats in historical_cohort_data.items():
        sample_size = _extract_sample_size(stats)

        # Filter: exclude low-sample cohorts
        if sample_size < min_sample_size:
            continue

        expectancy = float(stats.get("expectancy", 0.0))
        win_rate = float(stats.get("win_rate", 0.0))
        avg_r = float(stats.get("avg_r", stats.get("avg_rr", 0.0)))
        variance = float(stats.get("variance", 0.0))
        time_window = str(stats.get("time_window", "UNSPECIFIED"))

        baselines[cohort_key] = CohortBaseline(
            cohort_key=cohort_key,
            baseline_expectancy=round(expectancy, 4),
            baseline_win_rate=round(win_rate, 4),
            baseline_avg_r=round(avg_r, 4),
            baseline_variance=round(variance, 4),
            sample_size=sample_size,
            time_window=time_window,
        )

    return baselines


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _extract_sample_size(stats: dict[str, Any]) -> int:
    """Extract sample size from stats dict, checking common field names."""
    size = stats.get("trade_count") or stats.get("sample_size", 0)
    return int(size) if size else 0
