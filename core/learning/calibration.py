"""
Calibration Analysis — aggregates LearningRecords into calibration insights.

Analyses batches of completed decisions to determine:
    - Is confidence calibrated? (high confidence → more wins?)
    - Is uncertainty predictive? (high uncertainty → worse outcomes?)
    - Which evidence factors correlate with outcomes?

This module generates INSIGHTS only. It does NOT:
    - Change weights
    - Modify thresholds
    - Adjust trading behaviour

Usage:
    from core.learning.calibration import (
        analyse_confidence_calibration,
        analyse_evidence_performance,
        analyse_uncertainty_calibration,
    )

    cal = analyse_confidence_calibration(records)
    ev = analyse_evidence_performance(decision_records)
    unc = analyse_uncertainty_calibration(records)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# CALIBRATION REPORT MODEL
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CalibrationReport:
    """Aggregate calibration analysis across multiple decisions."""
    total_decisions: int
    calibrated_count: int
    overconfident_count: int
    underconfident_count: int
    uncertain_correct_count: int
    uncertain_wrong_count: int
    calibration_rate: float              # calibrated / total (0.0–1.0)
    overconfidence_rate: float           # overconfident / total
    insights: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_decisions": self.total_decisions,
            "calibrated_count": self.calibrated_count,
            "overconfident_count": self.overconfident_count,
            "underconfident_count": self.underconfident_count,
            "uncertain_correct_count": self.uncertain_correct_count,
            "uncertain_wrong_count": self.uncertain_wrong_count,
            "calibration_rate": round(self.calibration_rate, 4),
            "overconfidence_rate": round(self.overconfidence_rate, 4),
            "insights": list(self.insights),
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class EvidencePerformanceReport:
    """Per-factor evidence performance analysis."""
    factor_reports: tuple[Any, ...]   # tuple of FactorReport dicts
    insights: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_reports": list(self.factor_reports),
            "insights": list(self.insights),
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class UncertaintyCalibrationReport:
    """Uncertainty vs outcome calibration analysis."""
    low_uncertainty_win_rate: float      # Win rate when uncertainty < threshold
    high_uncertainty_win_rate: float     # Win rate when uncertainty > threshold
    low_uncertainty_count: int
    high_uncertainty_count: int
    uncertainty_predictive: bool         # True if high uncertainty → worse outcomes
    insights: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "low_uncertainty_win_rate": round(self.low_uncertainty_win_rate, 4),
            "high_uncertainty_win_rate": round(self.high_uncertainty_win_rate, 4),
            "low_uncertainty_count": self.low_uncertainty_count,
            "high_uncertainty_count": self.high_uncertainty_count,
            "uncertainty_predictive": self.uncertainty_predictive,
            "insights": list(self.insights),
            "metadata": self.metadata,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# COMMIT 3: CONFIDENCE CALIBRATION ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def analyse_confidence_calibration(
    records: list[dict[str, Any]],
) -> CalibrationReport:
    """
    Aggregate calibration across a batch of LearningRecords.

    Answers: "Is the system's confidence calibrated?"

    Args:
        records: List of LearningRecord.to_dict() outputs

    Returns:
        CalibrationReport with rates and insights
    """
    if not records:
        return CalibrationReport(
            total_decisions=0, calibrated_count=0, overconfident_count=0,
            underconfident_count=0, uncertain_correct_count=0, uncertain_wrong_count=0,
            calibration_rate=0.0, overconfidence_rate=0.0, insights=(),
        )

    total = len(records)
    counts = {
        "CALIBRATED": 0,
        "OVERCONFIDENT": 0,
        "UNDERCONFIDENT": 0,
        "UNCERTAIN_CORRECT": 0,
        "UNCERTAIN_WRONG": 0,
        "NEUTRAL": 0,
        "UNKNOWN": 0,
    }

    for r in records:
        cal = r.get("calibration_result", "UNKNOWN")
        counts[cal] = counts.get(cal, 0) + 1

    cal_rate = counts["CALIBRATED"] / total if total > 0 else 0.0
    overconf_rate = counts["OVERCONFIDENT"] / total if total > 0 else 0.0

    insights: list[str] = []

    if cal_rate >= 0.70:
        insights.append(f"System is well-calibrated ({cal_rate:.0%} of decisions)")
    elif cal_rate >= 0.50:
        insights.append(f"System is moderately calibrated ({cal_rate:.0%})")
    else:
        insights.append(f"System is poorly calibrated ({cal_rate:.0%}) — beliefs do not match outcomes")

    if overconf_rate >= 0.30:
        insights.append(f"Overconfidence problem: {overconf_rate:.0%} of decisions were overconfident")
    elif overconf_rate >= 0.15:
        insights.append(f"Some overconfidence detected ({overconf_rate:.0%})")

    if counts["UNCERTAIN_CORRECT"] > counts["UNCERTAIN_WRONG"]:
        insights.append("Uncertainty measurement is predictive — high uncertainty correlates with losses")
    elif counts["UNCERTAIN_WRONG"] > counts["UNCERTAIN_CORRECT"] * 2:
        insights.append("Uncertainty may be excessive — many uncertain decisions still succeeded")

    if counts["UNDERCONFIDENT"] >= total * 0.20:
        insights.append(f"Potential excessive caution: {counts['UNDERCONFIDENT']} decisions succeeded despite low confidence")

    return CalibrationReport(
        total_decisions=total,
        calibrated_count=counts["CALIBRATED"],
        overconfident_count=counts["OVERCONFIDENT"],
        underconfident_count=counts["UNDERCONFIDENT"],
        uncertain_correct_count=counts["UNCERTAIN_CORRECT"],
        uncertain_wrong_count=counts["UNCERTAIN_WRONG"],
        calibration_rate=round(cal_rate, 4),
        overconfidence_rate=round(overconf_rate, 4),
        insights=tuple(insights),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# COMMIT 4: EVIDENCE PERFORMANCE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def analyse_evidence_performance(
    decision_records: list[dict[str, Any]],
) -> EvidencePerformanceReport:
    """
    Analyse which evidence factors correlate with positive/negative outcomes.

    Uses ScoreAttribution from decision records to identify:
        - Factors that correlate with wins (useful evidence)
        - Factors that correlate with losses when dominant (possible over-weighting)

    Generates insights. Does NOT automatically change weights.

    Args:
        decision_records: List of ledger entries with score_attribution + outcome

    Returns:
        EvidencePerformanceReport with per-factor analysis
    """
    if not decision_records:
        return EvidencePerformanceReport(
            factor_reports=(), insights=(), metadata={"n_records": 0},
        )

    # Collect per-factor stats: {factor_name: {"win_contributions": [], "loss_contributions": []}}
    factor_stats: dict[str, dict[str, list[float]]] = {}

    for rec in decision_records:
        attribution = rec.get("score_attribution") or {}
        contributions = attribution.get("contributions", [])
        outcome = rec.get("outcome", rec.get("decision", ""))

        is_win = outcome in ("WIN", "EXECUTE")
        is_loss = outcome == "LOSS"

        for c in contributions:
            name = c.get("name", "unknown")
            contribution = c.get("contribution", 0.0)

            if name not in factor_stats:
                factor_stats[name] = {"win_contributions": [], "loss_contributions": [], "all": []}

            factor_stats[name]["all"].append(contribution)
            if is_win:
                factor_stats[name]["win_contributions"].append(contribution)
            elif is_loss:
                factor_stats[name]["loss_contributions"].append(contribution)

    # Build per-factor reports
    factor_reports: list[dict[str, Any]] = []
    insights: list[str] = []

    for name, stats in sorted(factor_stats.items()):
        all_c = stats["all"]
        win_c = stats["win_contributions"]
        loss_c = stats["loss_contributions"]

        avg_contribution = sum(all_c) / len(all_c) if all_c else 0.0
        avg_win = sum(win_c) / len(win_c) if win_c else 0.0
        avg_loss = sum(loss_c) / len(loss_c) if loss_c else 0.0

        # Determine correlation direction
        if win_c and loss_c:
            if avg_win > avg_loss * 1.3:
                correlation = "positive"
            elif avg_loss > avg_win * 1.3:
                correlation = "negative"
            else:
                correlation = "neutral"
        else:
            correlation = "insufficient_data"

        report = {
            "name": name,
            "avg_contribution": round(avg_contribution, 4),
            "avg_win_contribution": round(avg_win, 4),
            "avg_loss_contribution": round(avg_loss, 4),
            "win_count": len(win_c),
            "loss_count": len(loss_c),
            "correlation": correlation,
        }
        factor_reports.append(report)

        # Generate insight for notable factors
        if correlation == "positive" and avg_contribution >= 0.08:
            insights.append(f"{name}: appears useful — higher contribution correlates with wins")
        elif correlation == "negative" and avg_contribution >= 0.08:
            insights.append(f"{name}: possible over-weighting — higher contribution correlates with losses")

    return EvidencePerformanceReport(
        factor_reports=tuple(factor_reports),
        insights=tuple(insights),
        metadata={"n_records": len(decision_records), "n_factors": len(factor_reports)},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# COMMIT 5: UNCERTAINTY CALIBRATION
# ═══════════════════════════════════════════════════════════════════════════════

_UNCERTAINTY_SPLIT = 0.40  # Threshold to separate "low" vs "high" uncertainty


def analyse_uncertainty_calibration(
    records: list[dict[str, Any]],
) -> UncertaintyCalibrationReport:
    """
    Analyse whether uncertainty_score correlates with outcomes.

    Answers: "Does higher uncertainty predict worse outcomes?"

    Args:
        records: List of LearningRecord.to_dict() outputs

    Returns:
        UncertaintyCalibrationReport with win rates by uncertainty level
    """
    if not records:
        return UncertaintyCalibrationReport(
            low_uncertainty_win_rate=0.0, high_uncertainty_win_rate=0.0,
            low_uncertainty_count=0, high_uncertainty_count=0,
            uncertainty_predictive=False, insights=(),
        )

    low_wins = 0
    low_total = 0
    high_wins = 0
    high_total = 0

    for r in records:
        u_score = r.get("uncertainty_score", 0.5)
        outcome = r.get("outcome", "")
        is_win = outcome in ("WIN", "BREAKEVEN")

        if u_score < _UNCERTAINTY_SPLIT:
            low_total += 1
            if is_win:
                low_wins += 1
        else:
            high_total += 1
            if is_win:
                high_wins += 1

    low_rate = low_wins / low_total if low_total > 0 else 0.0
    high_rate = high_wins / high_total if high_total > 0 else 0.0
    predictive = low_rate > high_rate and low_total >= 3 and high_total >= 3

    insights: list[str] = []

    if predictive:
        delta = low_rate - high_rate
        insights.append(
            f"Uncertainty is predictive: low-uncertainty win rate {low_rate:.0%} "
            f"vs high-uncertainty {high_rate:.0%} (delta={delta:.0%})"
        )
        insights.append("Higher uncertainty environments reduce edge")
    elif low_total >= 3 and high_total >= 3:
        insights.append(
            f"Uncertainty not yet predictive: low={low_rate:.0%} vs high={high_rate:.0%} "
            f"(need more data or recalibration)"
        )
    else:
        insights.append(f"Insufficient data: low_n={low_total} high_n={high_total} (need >= 3 each)")

    if high_total > 0 and high_rate > 0.6:
        insights.append("High-uncertainty trades still winning frequently — uncertainty may be excessive")

    if low_total > 0 and low_rate < 0.4:
        insights.append("Low-uncertainty trades losing frequently — confidence not justified")

    return UncertaintyCalibrationReport(
        low_uncertainty_win_rate=round(low_rate, 4),
        high_uncertainty_win_rate=round(high_rate, 4),
        low_uncertainty_count=low_total,
        high_uncertainty_count=high_total,
        uncertainty_predictive=predictive,
        insights=tuple(insights),
    )
