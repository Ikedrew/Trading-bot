"""
Drift Report — Generates human-readable system health report.

NO system changes. NO parameter updates. PURE reporting layer.
"""

from __future__ import annotations

from typing import Any

from core.drift.drift_baseline import CohortBaseline
from core.drift.current_snapshot import CurrentCohortState
from core.drift.drift_scoring import DriftMetrics
from core.drift.drift_classifier import DriftStatus
from core.drift.drift_alerts import DriftAlert
from core.drift.regime_drift_detector import RegimeDriftReport


# ─── REPORT ENTRY ─────────────────────────────────────────────────────────────

class CohortReportEntry:
    """Assembled data for one cohort row in the report."""

    def __init__(
        self,
        cohort_key: str,
        metrics: DriftMetrics,
        status: DriftStatus,
        baseline: CohortBaseline | None = None,
        current: CurrentCohortState | None = None,
    ):
        self.cohort_key = cohort_key
        self.metrics = metrics
        self.status = status
        self.baseline = baseline
        self.current = current


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def generate_drift_report(
    entries: list[CohortReportEntry],
    regime_report: RegimeDriftReport | None = None,
    alerts: list[DriftAlert] | None = None,
) -> str:
    """
    Generate complete markdown drift health report.

    Sections:
    1. Executive Summary
    2. Cohort Health Table
    3. Healthiest Cohorts
    4. Degrading Cohorts
    5. Broken Edge Signals
    6. Regime Instability
    7. Risk Section
    8. Recommended Investigation Areas

    Args:
        entries: List of CohortReportEntry (one per cohort).
        regime_report: Optional RegimeDriftReport for regime section.
        alerts: Optional list of DriftAlert for risk section.

    Returns:
        Formatted markdown report string.
    """
    lines: list[str] = []

    _section_header(lines)
    _section_executive_summary(lines, entries, regime_report)
    _section_cohort_table(lines, entries)
    _section_healthiest(lines, entries)
    _section_degrading(lines, entries)
    _section_broken_edge(lines, entries)
    _section_regime_instability(lines, regime_report)
    _section_risk(lines, entries, alerts)
    _section_investigation(lines, entries, regime_report)

    return "\n".join(lines)


# ─── SECTIONS ─────────────────────────────────────────────────────────────────

def _section_header(lines: list[str]) -> None:
    lines.append("# Drift Health Report")
    lines.append("")


def _section_executive_summary(
    lines: list[str],
    entries: list[CohortReportEntry],
    regime_report: RegimeDriftReport | None,
) -> None:
    lines.append("## Executive Summary")
    lines.append("")

    total = len(entries)
    stable = sum(1 for e in entries if e.status == DriftStatus.STABLE)
    warning = sum(1 for e in entries if e.status == DriftStatus.WARNING)
    degraded = sum(1 for e in entries if e.status == DriftStatus.DEGRADED)
    broken = sum(1 for e in entries if e.status == DriftStatus.BROKEN_EDGE)

    lines.append(f"**Cohorts monitored:** {total}")
    lines.append(f"**Stable:** {stable} | **Warning:** {warning} | **Degraded:** {degraded} | **Broken Edge:** {broken}")
    lines.append("")

    # Overall verdict
    if broken > 0:
        lines.append("**System Health: CRITICAL** — One or more cohorts have lost their edge.")
    elif degraded > 0:
        lines.append("**System Health: DEGRADED** — Active performance deterioration detected.")
    elif warning > 0:
        lines.append("**System Health: CAUTION** — Minor drift observed, monitoring advised.")
    else:
        lines.append("**System Health: HEALTHY** — All cohorts performing within baseline bounds.")
    lines.append("")

    if regime_report and regime_report.regime_shift_detected:
        lines.append(f"**Regime Alert:** {regime_report.likely_cause}")
        lines.append("")


def _section_cohort_table(lines: list[str], entries: list[CohortReportEntry]) -> None:
    lines.append("## Cohort Health Table")
    lines.append("")
    lines.append("| Cohort | Status | Drift Score | Exp Drift | WR Drift | Direction |")
    lines.append("|--------|--------|-------------|-----------|----------|-----------|")

    sorted_entries = sorted(entries, key=lambda e: e.metrics.composite_drift_score, reverse=True)

    for e in sorted_entries:
        m = e.metrics
        status_icon = _status_icon(e.status)
        lines.append(
            f"| {e.cohort_key} | {status_icon} {e.status.value} "
            f"| {m.composite_drift_score:.3f} "
            f"| {m.expectancy_drift:+.3f}R "
            f"| {m.winrate_drift:+.3f} "
            f"| {m.direction} |"
        )

    lines.append("")


def _section_healthiest(lines: list[str], entries: list[CohortReportEntry]) -> None:
    lines.append("## Healthiest Cohorts")
    lines.append("")

    healthy = [
        e for e in entries
        if e.status == DriftStatus.STABLE and e.current is not None
    ]
    healthy.sort(key=lambda e: e.current.current_expectancy if e.current else 0, reverse=True)

    if not healthy:
        lines.append("*No fully stable cohorts with current data.*")
        lines.append("")
        return

    for e in healthy[:5]:
        curr = e.current
        if curr:
            lines.append(
                f"- **{e.cohort_key}** — expectancy {curr.current_expectancy:.3f}R, "
                f"win rate {curr.current_win_rate:.1%}, "
                f"{curr.sample_size} recent trades"
            )

    lines.append("")


def _section_degrading(lines: list[str], entries: list[CohortReportEntry]) -> None:
    lines.append("## Degrading Cohorts")
    lines.append("")

    degrading = [e for e in entries if e.status == DriftStatus.DEGRADED]

    if not degrading:
        lines.append("*No degrading cohorts detected.*")
        lines.append("")
        return

    for e in degrading:
        m = e.metrics
        lines.append(
            f"- **{e.cohort_key}** — drift score {m.composite_drift_score:.3f}, "
            f"expectancy shift {m.expectancy_drift:+.3f}R, "
            f"variance drift {m.variance_drift:.3f}"
        )

    lines.append("")


def _section_broken_edge(lines: list[str], entries: list[CohortReportEntry]) -> None:
    lines.append("## Broken Edge Signals")
    lines.append("")

    broken = [e for e in entries if e.status == DriftStatus.BROKEN_EDGE]

    if not broken:
        lines.append("*No broken edge signals.*")
        lines.append("")
        return

    for e in broken:
        m = e.metrics
        baseline_exp = e.baseline.baseline_expectancy if e.baseline else 0.0
        current_exp = e.current.current_expectancy if e.current else 0.0
        lines.append(
            f"- **{e.cohort_key}** — EDGE LOST"
        )
        lines.append(
            f"  - Baseline expectancy: {baseline_exp:.3f}R → Current: {current_exp:.3f}R"
        )
        lines.append(
            f"  - Composite drift: {m.composite_drift_score:.3f}, direction: {m.direction}"
        )

    lines.append("")


def _section_regime_instability(
    lines: list[str],
    regime_report: RegimeDriftReport | None,
) -> None:
    lines.append("## Regime Instability")
    lines.append("")

    if not regime_report or not regime_report.regime_shift_detected:
        lines.append("*No regime instability detected.*")
        lines.append("")
        return

    lines.append(f"**Likely cause:** {regime_report.likely_cause}")
    lines.append("")
    lines.append("**Affected cohorts:**")
    for affected in regime_report.affected_cohorts:
        lines.append(
            f"- {affected.cohort_key} — pattern: {affected.pattern}, "
            f"delta: {affected.expectancy_delta:+.3f}R"
        )
    lines.append("")


def _section_risk(
    lines: list[str],
    entries: list[CohortReportEntry],
    alerts: list[DriftAlert] | None,
) -> None:
    lines.append("## Risk Assessment")
    lines.append("")

    risks: list[str] = []

    broken_count = sum(1 for e in entries if e.status == DriftStatus.BROKEN_EDGE)
    degraded_count = sum(1 for e in entries if e.status == DriftStatus.DEGRADED)

    if broken_count > 0:
        risks.append(
            f"{broken_count} cohort(s) with broken edge. "
            "Capital at risk if these cohorts remain active."
        )

    if degraded_count > 0:
        risks.append(
            f"{degraded_count} cohort(s) degrading. "
            "Continued exposure may erode account equity."
        )

    # High variance across multiple cohorts
    high_var = [e for e in entries if e.metrics.variance_drift > 1.5]
    if high_var:
        risks.append(
            f"{len(high_var)} cohort(s) show significant variance increase. "
            "Market conditions may be structurally different from baseline period."
        )

    if alerts:
        critical_count = sum(1 for a in alerts if a.severity == "critical")
        if critical_count > 0:
            risks.append(f"{critical_count} critical alert(s) active.")

    if risks:
        for r in risks:
            lines.append(f"- ⚠️ {r}")
    else:
        lines.append("*No elevated risks identified.*")

    lines.append("")


def _section_investigation(
    lines: list[str],
    entries: list[CohortReportEntry],
    regime_report: RegimeDriftReport | None,
) -> None:
    lines.append("## Recommended Investigation Areas")
    lines.append("")

    recommendations: list[str] = []

    # Broken edges need immediate attention
    broken = [e for e in entries if e.status == DriftStatus.BROKEN_EDGE]
    if broken:
        cohorts = ", ".join(e.cohort_key for e in broken)
        recommendations.append(
            f"**Immediate:** Review edge validity for: {cohorts}. "
            "Consider temporary deactivation or position size reduction."
        )

    # Regime-driven issues
    if regime_report and regime_report.regime_shift_detected:
        recommendations.append(
            "**Regime filter:** Verify regime classification accuracy. "
            "Current performance suggests possible misclassification or structural market change."
        )

    # Degrading cohorts
    degraded = [e for e in entries if e.status == DriftStatus.DEGRADED]
    if degraded:
        recommendations.append(
            "**Calibration:** Run calibration engine on degraded cohorts to assess "
            "whether parameter adjustments can recover performance."
        )

    # Variance explosions
    high_var = [e for e in entries if e.metrics.variance_drift > 1.5]
    if high_var:
        recommendations.append(
            "**Stability:** Investigate variance increases. Possible causes: "
            "wider spreads, news-driven volatility, or strategy decay."
        )

    if recommendations:
        for i, rec in enumerate(recommendations, 1):
            lines.append(f"{i}. {rec}")
    else:
        lines.append("*No specific investigations recommended. System operating within bounds.*")

    lines.append("")


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _status_icon(status: DriftStatus) -> str:
    """Map status to emoji indicator."""
    if status == DriftStatus.STABLE:
        return "✅"
    if status == DriftStatus.WARNING:
        return "⚡"
    if status == DriftStatus.DEGRADED:
        return "⚠️"
    if status == DriftStatus.BROKEN_EDGE:
        return "🔴"
    return "❓"
