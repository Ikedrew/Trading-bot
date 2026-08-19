"""
Calibration Report — Human-readable explanation of parameter changes.

NO execution logic. NO system mutation. Pure report generation.
"""

from __future__ import annotations

from typing import Any

from core.calibration.calibration_engine import CalibrationRecommendation
from core.calibration.calibration_aggregator import GlobalCalibrationPlan
from core.calibration.calibration_guard import GuardResult


def generate_calibration_report(
    recommendations: list[CalibrationRecommendation],
    approved: list[GuardResult],
    rejected: list[GuardResult],
    plan: GlobalCalibrationPlan,
) -> str:
    """
    Generate complete markdown calibration report.

    Args:
        recommendations: All cohort recommendations produced.
        approved: Guard-approved recommendations.
        rejected: Guard-rejected recommendations.
        plan: Final aggregated calibration plan.

    Returns:
        Formatted markdown report string.
    """
    lines: list[str] = []

    # Header
    lines.append("# Calibration Report")
    lines.append("")
    lines.append(f"**Cohorts analyzed:** {len(recommendations)}")
    lines.append(f"**Approved:** {len(approved)}")
    lines.append(f"**Rejected:** {len(rejected)}")
    lines.append("")

    # Final plan
    lines.append("## Proposed Parameter Changes")
    lines.append("")
    lines.append("| Parameter | Before | After |")
    lines.append("|-----------|--------|-------|")
    lines.append(f"| Break-Even RR | 0.0 (disabled) | {plan.final_break_even_rr} |")
    lines.append(f"| Trailing Start RR | 0.0 (disabled) | {plan.final_trailing_start_rr} |")
    lines.append(f"| Trailing Step | 0.0 (disabled) | {plan.final_trailing_step} |")
    lines.append(f"| Partial TP | OFF | {'ON' if plan.final_partial_tp_state else 'OFF'} |")
    lines.append("")

    # Best performers
    lines.append("## Best Performing Cohorts")
    lines.append("")
    best = sorted(
        [r for r in recommendations if r.expectancy > 0],
        key=lambda r: r.expectancy,
        reverse=True,
    )[:5]
    if best:
        for r in best:
            lines.append(f"- **{r.cohort_key}** — expectancy {r.expectancy:.3f}R, "
                         f"{r.sample_size} trades, confidence {r.confidence_score:.2f}")
            lines.append(f"  - BE: {r.recommended_break_even_rr}, "
                         f"Trail: {r.recommended_trailing_start_rr}, "
                         f"Partial: {'ON' if r.recommended_partial_tp else 'OFF'}")
    else:
        lines.append("*No positive expectancy cohorts found.*")
    lines.append("")

    # Worst performers
    lines.append("## Worst Performing Cohorts")
    lines.append("")
    worst = sorted(
        [r for r in recommendations if r.expectancy <= 0],
        key=lambda r: r.expectancy,
    )[:5]
    if worst:
        for r in worst:
            lines.append(f"- **{r.cohort_key}** — expectancy {r.expectancy:.3f}R, "
                         f"{r.sample_size} trades, variance {r.variance:.3f}")
    else:
        lines.append("*All cohorts have positive expectancy.*")
    lines.append("")

    # Rejected recommendations
    if rejected:
        lines.append("## Rejected Adjustments")
        lines.append("")
        for gr in rejected:
            rec = gr.recommendation
            lines.append(f"- **{rec.cohort_key}** — REJECTED")
            for reason in gr.rejections:
                lines.append(f"  - {reason}")
        lines.append("")

    # Risk warnings
    lines.append("## Risk Warnings")
    lines.append("")
    warnings = _detect_warnings(recommendations, plan)
    if warnings:
        for w in warnings:
            lines.append(f"- ⚠️ {w}")
    else:
        lines.append("*No risk warnings detected.*")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    lines.append(f"Based on {sum(r.sample_size for r in recommendations)} total trades "
                 f"across {len(recommendations)} cohorts, the calibration engine recommends "
                 f"activating trade management with the parameters above.")
    lines.append("")
    if plan.applied_cohorts:
        lines.append(f"**Applied cohorts:** {', '.join(plan.applied_cohorts)}")
    lines.append("")

    return "\n".join(lines)


def _detect_warnings(
    recommendations: list[CalibrationRecommendation],
    plan: GlobalCalibrationPlan,
) -> list[str]:
    """Detect overfitting signals and risk conditions."""
    warnings: list[str] = []

    # Low total sample
    total_trades = sum(r.sample_size for r in recommendations)
    if total_trades < 50:
        warnings.append(f"Low total sample size ({total_trades} trades). Results may be unreliable.")

    # High variance cohorts dominating
    high_var = [r for r in recommendations if r.variance > 2.0 and r.sample_size >= 10]
    if high_var:
        warnings.append(f"{len(high_var)} cohort(s) have high variance (>2.0). "
                        f"Parameter stability uncertain.")

    # All cohorts small
    small = [r for r in recommendations if r.sample_size < 20]
    if len(small) > len(recommendations) * 0.7:
        warnings.append("Majority of cohorts have fewer than 20 trades. "
                        "Consider extending data collection period.")

    # Negative expectancy present
    negative = [r for r in recommendations if r.expectancy < 0]
    if negative:
        warnings.append(f"{len(negative)} cohort(s) show negative expectancy. "
                        f"Consider filtering these setups from execution.")

    # Extreme parameter shift
    if plan.final_break_even_rr > 1.5:
        warnings.append(f"Proposed BE trigger ({plan.final_break_even_rr}R) is aggressive. "
                        f"May leave profits unprotected.")

    return warnings
