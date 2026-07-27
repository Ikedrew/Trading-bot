"""
Human Review Loop — produces aggregate learning summaries for human decision-making.

Learning produces INSIGHTS, not AUTOMATIC MODIFICATIONS.

This module generates reports that a human/system owner reviews to decide
whether changes to weights, thresholds, or strategy are justified.

Usage:
    from core.learning.review import generate_review_summary

    summary = generate_review_summary(
        learning_records=records,
        decision_records=decisions,
    )
    # summary.observations → human-readable findings
    # summary.recommendations → suggested areas to investigate (NOT auto-applied)

NEVER:
    - Automatically modifies weights
    - Changes thresholds
    - Adjusts strategy parameters
    - Optimizes on single trades

Learning requires: samples, distributions, repeated patterns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.learning.calibration import (
    analyse_confidence_calibration,
    analyse_evidence_performance,
    analyse_uncertainty_calibration,
)


@dataclass(frozen=True)
class ReviewSummary:
    """
    Aggregate learning summary for human review.

    Contains observations and areas to investigate — NOT automated changes.
    """
    period: str                          # e.g., "2026-07-01 to 2026-07-10"
    total_decisions_analysed: int
    observations: tuple[str, ...]        # What was observed
    recommendations: tuple[str, ...]     # What to investigate (human decides)
    confidence_calibration: dict[str, Any]
    evidence_performance: dict[str, Any]
    uncertainty_calibration: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "total_decisions_analysed": self.total_decisions_analysed,
            "observations": list(self.observations),
            "recommendations": list(self.recommendations),
            "confidence_calibration": self.confidence_calibration,
            "evidence_performance": self.evidence_performance,
            "uncertainty_calibration": self.uncertainty_calibration,
            "metadata": self.metadata,
        }

    def format_for_human(self) -> str:
        """Format as plain-text report for Discord/log/terminal."""
        lines = [
            f"=== LEARNING REVIEW: {self.period} ===",
            f"Decisions analysed: {self.total_decisions_analysed}",
            "",
            "OBSERVATIONS:",
        ]
        for obs in self.observations:
            lines.append(f"  • {obs}")

        lines.append("")
        lines.append("RECOMMENDATIONS (for human review):")
        for rec in self.recommendations:
            lines.append(f"  → {rec}")

        return "\n".join(lines)


def generate_review_summary(
    *,
    learning_records: list[dict[str, Any]],
    decision_records: list[dict[str, Any]],
    period: str = "",
) -> ReviewSummary:
    """
    Generate an aggregate learning summary for human review.

    Combines calibration, evidence performance, and uncertainty analysis
    into a single report with observations and recommendations.

    Args:
        learning_records: LearningRecord.to_dict() outputs
        decision_records: Ledger entries with score_attribution + outcome
        period: Human-readable period description

    Returns:
        ReviewSummary (frozen, for human consumption)
    """
    if not learning_records and not decision_records:
        return ReviewSummary(
            period=period or "no data",
            total_decisions_analysed=0,
            observations=("No data available for review",),
            recommendations=(),
            confidence_calibration={},
            evidence_performance={},
            uncertainty_calibration={},
        )

    # Run all three analyses
    cal = analyse_confidence_calibration(learning_records)
    ev = analyse_evidence_performance(decision_records)
    unc = analyse_uncertainty_calibration(learning_records)

    # Collect observations from all analyses
    observations: list[str] = []
    observations.extend(cal.insights)
    observations.extend(ev.insights)
    observations.extend(unc.insights)

    # Generate recommendations (areas to investigate — NOT auto-applied)
    recommendations = _generate_recommendations(cal, ev, unc, learning_records)

    return ReviewSummary(
        period=period,
        total_decisions_analysed=len(learning_records) + len(decision_records),
        observations=tuple(observations),
        recommendations=tuple(recommendations),
        confidence_calibration=cal.to_dict(),
        evidence_performance=ev.to_dict(),
        uncertainty_calibration=unc.to_dict(),
    )


def _generate_recommendations(
    cal: Any,
    ev: Any,
    unc: Any,
    records: list[dict[str, Any]],
) -> list[str]:
    """
    Generate human-actionable recommendations based on analysis.

    These are SUGGESTIONS for investigation, not automated changes.
    """
    recs: list[str] = []

    # Overconfidence recommendation
    if cal.overconfidence_rate >= 0.25:
        recs.append(
            f"INVESTIGATE: Overconfidence rate is {cal.overconfidence_rate:.0%}. "
            "Review whether scoring produces false high-confidence signals."
        )

    # Evidence performance recommendations
    for fr in ev.factor_reports:
        if fr.get("correlation") == "negative" and fr.get("avg_contribution", 0) >= 0.08:
            name = fr.get("name", "?")
            recs.append(
                f"INVESTIGATE: {name} has negative correlation with outcomes. "
                "Consider whether its weight is appropriate."
            )

    # Uncertainty recommendations
    if unc.uncertainty_predictive:
        recs.append(
            "OBSERVATION: Uncertainty measurement is predictive. "
            "Consider whether policy should consume uncertainty_score."
        )
    elif hasattr(unc, "high_uncertainty_win_rate") and unc.high_uncertainty_win_rate > 0.6:
        recs.append(
            "OBSERVATION: High-uncertainty trades still profitable. "
            "Uncertainty measurement may be too conservative."
        )

    # Sample size warning
    n = len(records)
    if n < 30:
        recs.append(
            f"CAUTION: Only {n} decisions analysed. "
            "Minimum 30–50 needed for reliable statistical patterns."
        )

    # Calibration recommendation
    if cal.calibration_rate < 0.5 and cal.total_decisions >= 20:
        recs.append(
            "PRIORITY: Calibration rate below 50%. "
            "System's confidence does not match outcomes — investigate scoring model."
        )

    return recs
