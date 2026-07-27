"""
Cohort Slicer — Groups trades by dimensions and computes performance metrics.

Dimensions:
  - confirmation_strength (STRONG / WEAK / INVALID)
  - entry_timing (EARLY / MID / LATE)
  - wick_ratio bands (clean / moderate / high)
  - body_pct bands

Metrics per cohort:
  - trade count
  - win rate
  - average RR
  - expectancy per trade
  - best/worst outcome

STRICTLY OFFLINE ANALYSIS — never imported by runtime code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ─── COHORT METRICS ───────────────────────────────────────────────────────────

@dataclass
class CohortMetrics:
    """Performance metrics for a group of trades."""

    label: str
    trade_count: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    total_rr: float = 0.0
    best_rr: float = 0.0
    worst_rr: float = 0.0
    outcomes: list[float] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        if self.trade_count == 0:
            return 0.0
        return round(self.wins / self.trade_count, 4)

    @property
    def avg_rr(self) -> float:
        if self.trade_count == 0:
            return 0.0
        return round(self.total_rr / self.trade_count, 3)

    @property
    def expectancy(self) -> float:
        """Expected value per trade (in RR units)."""
        if self.trade_count == 0:
            return 0.0
        return round(self.total_rr / self.trade_count, 3)

    @property
    def variance(self) -> float:
        """Variance of RR outcomes."""
        if len(self.outcomes) < 2:
            return 0.0
        mean = sum(self.outcomes) / len(self.outcomes)
        return round(sum((x - mean) ** 2 for x in self.outcomes) / len(self.outcomes), 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "trade_count": self.trade_count,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": self.win_rate,
            "avg_rr": self.avg_rr,
            "expectancy": self.expectancy,
            "variance": self.variance,
            "total_pnl": round(self.total_pnl, 2),
            "best_rr": self.best_rr,
            "worst_rr": self.worst_rr,
        }


# ─── SLICING FUNCTIONS ────────────────────────────────────────────────────────

def _add_to_cohort(cohort: CohortMetrics, record: dict[str, Any]) -> None:
    """Add a single trade record to a cohort's metrics."""
    cohort.trade_count += 1

    pnl = record.get("outcome_pnl")
    rr = record.get("outcome_rr")
    win = record.get("outcome_win")

    if win is True:
        cohort.wins += 1
    elif win is False:
        cohort.losses += 1

    if pnl is not None:
        cohort.total_pnl += pnl

    if rr is not None:
        # For losses, store as negative RR
        effective_rr = rr if win else -abs(rr) if rr != 0 else -1.0
        cohort.total_rr += effective_rr
        cohort.outcomes.append(effective_rr)
        cohort.best_rr = max(cohort.best_rr, effective_rr)
        cohort.worst_rr = min(cohort.worst_rr, effective_rr)
    elif win is not None:
        # No RR data but know outcome: approximate ±1R
        approx_rr = 2.0 if win else -1.0
        cohort.total_rr += approx_rr
        cohort.outcomes.append(approx_rr)


def slice_by_confirmation_strength(records: list[dict[str, Any]]) -> dict[str, CohortMetrics]:
    """
    Group trades by confirmation strength.

    Returns:
        Dict mapping "STRONG"/"WEAK"/"INVALID"/"UNKNOWN" → CohortMetrics
    """
    cohorts: dict[str, CohortMetrics] = {}

    for record in records:
        confirmation = record.get("confirmation") or {}
        strength = confirmation.get("strength", "UNKNOWN") or "UNKNOWN"

        if strength not in cohorts:
            cohorts[strength] = CohortMetrics(label=f"strength={strength}")

        _add_to_cohort(cohorts[strength], record)

    return cohorts


def slice_by_entry_timing(records: list[dict[str, Any]]) -> dict[str, CohortMetrics]:
    """
    Group trades by entry timing classification.

    Returns:
        Dict mapping "EARLY"/"MID"/"LATE"/"UNKNOWN" → CohortMetrics
    """
    cohorts: dict[str, CohortMetrics] = {}

    for record in records:
        timing = record.get("entry_timing", "UNKNOWN") or "UNKNOWN"

        if timing not in cohorts:
            cohorts[timing] = CohortMetrics(label=f"timing={timing}")

        _add_to_cohort(cohorts[timing], record)

    return cohorts


def slice_by_wick_ratio_band(records: list[dict[str, Any]]) -> dict[str, CohortMetrics]:
    """
    Group trades by wick_ratio bands:
      - "clean" (0.0–0.2): Minimal wick rejection
      - "moderate" (0.2–0.4): Some wick presence
      - "high" (0.4–1.0): Significant wick / rejection

    Returns:
        Dict mapping band label → CohortMetrics
    """
    cohorts = {
        "clean_0.0-0.2": CohortMetrics(label="wick=clean(0.0-0.2)"),
        "moderate_0.2-0.4": CohortMetrics(label="wick=moderate(0.2-0.4)"),
        "high_0.4-1.0": CohortMetrics(label="wick=high(0.4-1.0)"),
        "unknown": CohortMetrics(label="wick=unknown"),
    }

    for record in records:
        confirmation = record.get("confirmation") or {}
        wick_ratio = confirmation.get("wick_ratio")

        if wick_ratio is None:
            band = "unknown"
        elif wick_ratio < 0.2:
            band = "clean_0.0-0.2"
        elif wick_ratio < 0.4:
            band = "moderate_0.2-0.4"
        else:
            band = "high_0.4-1.0"

        _add_to_cohort(cohorts[band], record)

    return cohorts


def slice_by_body_pct_band(records: list[dict[str, Any]]) -> dict[str, CohortMetrics]:
    """
    Group trades by body_pct bands:
      - "low" (0.0–0.55): Weak body
      - "moderate" (0.55–0.70): Moderate body
      - "high" (0.70–1.0): Strong body

    Returns:
        Dict mapping band label → CohortMetrics
    """
    cohorts = {
        "low_0.0-0.55": CohortMetrics(label="body=low(0.0-0.55)"),
        "moderate_0.55-0.70": CohortMetrics(label="body=moderate(0.55-0.70)"),
        "high_0.70-1.0": CohortMetrics(label="body=high(0.70-1.0)"),
        "unknown": CohortMetrics(label="body=unknown"),
    }

    for record in records:
        confirmation = record.get("confirmation") or {}
        body_pct = confirmation.get("body_pct")

        if body_pct is None:
            band = "unknown"
        elif body_pct < 0.55:
            band = "low_0.0-0.55"
        elif body_pct < 0.70:
            band = "moderate_0.55-0.70"
        else:
            band = "high_0.70-1.0"

        _add_to_cohort(cohorts[band], record)

    return cohorts


def build_interaction_matrix(
    records: list[dict[str, Any]],
) -> dict[str, dict[str, CohortMetrics]]:
    """
    Build 3x3 interaction matrix: confirmation_strength × entry_timing.

    Returns:
        Nested dict: matrix[strength][timing] → CohortMetrics

    Structure:
        matrix["STRONG"]["EARLY"] = CohortMetrics(...)
        matrix["STRONG"]["MID"] = CohortMetrics(...)
        matrix["WEAK"]["LATE"] = CohortMetrics(...)
        etc.
    """
    strengths = ("STRONG", "WEAK", "INVALID")
    timings = ("EARLY", "MID", "LATE")

    matrix: dict[str, dict[str, CohortMetrics]] = {}
    for s in strengths:
        matrix[s] = {}
        for t in timings:
            matrix[s][t] = CohortMetrics(label=f"{s}×{t}")

    for record in records:
        confirmation = record.get("confirmation") or {}
        strength = confirmation.get("strength", "UNKNOWN") or "UNKNOWN"
        timing = record.get("entry_timing", "UNKNOWN") or "UNKNOWN"

        if strength in strengths and timing in timings:
            _add_to_cohort(matrix[strength][timing], record)

    return matrix
