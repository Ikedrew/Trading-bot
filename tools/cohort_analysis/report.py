"""
Cohort Analysis Report Generator — Produces formatted analysis output.

Consumes sliced cohort data and generates:
- Summary tables
- Interaction matrix
- Key insights
- Data-driven conclusions

STRICTLY OFFLINE — never imported by runtime code.
"""

from __future__ import annotations

from typing import Any

from tools.cohort_analysis.slicer import CohortMetrics


def format_cohort_table(
    cohorts: dict[str, CohortMetrics],
    title: str = "Cohort Analysis",
) -> str:
    """Format cohort metrics as a readable table string."""
    lines = [
        f"\n{'═' * 80}",
        f"  {title}",
        f"{'═' * 80}",
        f"  {'Cohort':<25} {'Trades':>7} {'Win%':>7} {'Avg RR':>8} {'Expect':>8} {'Var':>7} {'PnL':>10}",
        f"  {'─' * 75}",
    ]

    for key in sorted(cohorts.keys()):
        c = cohorts[key]
        if c.trade_count == 0:
            continue
        lines.append(
            f"  {c.label:<25} {c.trade_count:>7d} "
            f"{c.win_rate * 100:>6.1f}% {c.avg_rr:>8.3f} "
            f"{c.expectancy:>8.3f} {c.variance:>7.3f} "
            f"{c.total_pnl:>10.2f}"
        )

    lines.append(f"{'═' * 80}\n")
    return "\n".join(lines)


def format_interaction_matrix(
    matrix: dict[str, dict[str, CohortMetrics]],
) -> str:
    """Format the strength × timing interaction matrix."""
    lines = [
        f"\n{'═' * 80}",
        "  Interaction Matrix: Confirmation Strength × Entry Timing",
        f"{'═' * 80}",
        "",
        f"  {'':15} {'EARLY':>15} {'MID':>15} {'LATE':>15}",
        f"  {'─' * 62}",
    ]

    for strength in ("STRONG", "WEAK", "INVALID"):
        row_parts = [f"  {strength:<15}"]
        for timing in ("EARLY", "MID", "LATE"):
            c = matrix[strength][timing]
            if c.trade_count == 0:
                row_parts.append(f"{'—':>15}")
            else:
                cell = f"{c.trade_count}t/{c.win_rate * 100:.0f}%/{c.expectancy:.2f}R"
                row_parts.append(f"{cell:>15}")
        lines.append("".join(row_parts))

    lines.append("")
    lines.append("  Format: trades / win_rate / expectancy(R)")
    lines.append(f"{'═' * 80}\n")
    return "\n".join(lines)


def generate_insights(
    strength_cohorts: dict[str, CohortMetrics],
    timing_cohorts: dict[str, CohortMetrics],
    wick_cohorts: dict[str, CohortMetrics],
    body_cohorts: dict[str, CohortMetrics],
    matrix: dict[str, dict[str, CohortMetrics]],
) -> str:
    """Generate key statistical insights from cohort data."""
    lines = [
        f"\n{'═' * 80}",
        "  KEY INSIGHTS",
        f"{'═' * 80}",
        "",
    ]

    # Find best/worst cohorts
    all_cohorts: list[CohortMetrics] = []
    for cohorts in [strength_cohorts, timing_cohorts, wick_cohorts, body_cohorts]:
        all_cohorts.extend(c for c in cohorts.values() if c.trade_count >= 3)

    if not all_cohorts:
        lines.append("  Insufficient data for insights (need ≥3 trades per cohort).")
        lines.append(f"{'═' * 80}\n")
        return "\n".join(lines)

    best = max(all_cohorts, key=lambda c: c.expectancy)
    worst = min(all_cohorts, key=lambda c: c.expectancy)
    highest_var = max(all_cohorts, key=lambda c: c.variance)

    lines.append(f"  Highest expectancy:  {best.label} → {best.expectancy:.3f}R "
                 f"({best.trade_count} trades, {best.win_rate * 100:.1f}% WR)")
    lines.append(f"  Lowest expectancy:   {worst.label} → {worst.expectancy:.3f}R "
                 f"({worst.trade_count} trades, {worst.win_rate * 100:.1f}% WR)")
    lines.append(f"  Highest variance:    {highest_var.label} → σ²={highest_var.variance:.4f} "
                 f"({highest_var.trade_count} trades)")
    lines.append("")

    # Confirmation strength comparison
    strong = strength_cohorts.get("STRONG")
    weak = strength_cohorts.get("WEAK")
    if strong and weak and strong.trade_count >= 3 and weak.trade_count >= 3:
        lines.append("  ─── Confirmation Strength ───")
        delta = strong.expectancy - weak.expectancy
        lines.append(f"  STRONG vs WEAK: Δexpectancy = {delta:+.3f}R")
        if delta > 0.2:
            lines.append("  → STRONG significantly outperforms WEAK")
        elif delta < -0.2:
            lines.append("  → WEAK unexpectedly outperforms STRONG (investigate)")
        else:
            lines.append("  → No significant difference between STRONG and WEAK")
        lines.append("")

    # Entry timing comparison
    early = timing_cohorts.get("EARLY")
    mid = timing_cohorts.get("MID")
    late = timing_cohorts.get("LATE")
    if any(c and c.trade_count >= 3 for c in [early, mid, late]):
        lines.append("  ─── Entry Timing ───")
        for name, cohort in [("EARLY", early), ("MID", mid), ("LATE", late)]:
            if cohort and cohort.trade_count >= 3:
                lines.append(f"  {name}: expectancy={cohort.expectancy:.3f}R, "
                             f"WR={cohort.win_rate * 100:.1f}%, var={cohort.variance:.4f}")
        lines.append("")

    # Wick ratio insight
    clean = wick_cohorts.get("clean_0.0-0.2")
    high = wick_cohorts.get("high_0.4-1.0")
    if clean and high and clean.trade_count >= 3 and high.trade_count >= 3:
        lines.append("  ─── Wick Ratio ───")
        lines.append(f"  Clean (0-20%): WR={clean.win_rate * 100:.1f}%, expect={clean.expectancy:.3f}R")
        lines.append(f"  High (40-100%): WR={high.win_rate * 100:.1f}%, expect={high.expectancy:.3f}R")
        if high.win_rate < clean.win_rate - 0.1:
            lines.append("  → High wick ratio correlates with lower win rate")
        lines.append("")

    lines.append(f"{'═' * 80}\n")
    return "\n".join(lines)


def generate_conclusions(
    strength_cohorts: dict[str, CohortMetrics],
    timing_cohorts: dict[str, CohortMetrics],
    wick_cohorts: dict[str, CohortMetrics],
    matrix: dict[str, dict[str, CohortMetrics]],
) -> str:
    """Generate data-driven strategy conclusions."""
    lines = [
        f"\n{'═' * 80}",
        "  DATA-DRIVEN CONCLUSIONS",
        f"{'═' * 80}",
        "",
    ]

    # Q1: Should WEAK confirmations be traded?
    lines.append("  Q: Should WEAK confirmations be traded?")
    weak = strength_cohorts.get("WEAK")
    if weak and weak.trade_count >= 5:
        if weak.expectancy > 0:
            lines.append(f"  A: YES — WEAK expectancy is positive ({weak.expectancy:.3f}R) "
                         f"but monitor closely ({weak.trade_count} trades)")
        else:
            lines.append(f"  A: NO — WEAK expectancy is negative ({weak.expectancy:.3f}R) "
                         f"({weak.trade_count} trades)")
    else:
        lines.append(f"  A: INSUFFICIENT DATA — need more WEAK trades "
                     f"(have {weak.trade_count if weak else 0})")
    lines.append("")

    # Q2: Does EARLY entry outperform MID/LATE?
    lines.append("  Q: Does EARLY entry outperform MID/LATE?")
    early = timing_cohorts.get("EARLY")
    mid = timing_cohorts.get("MID")
    late = timing_cohorts.get("LATE")
    has_data = any(c and c.trade_count >= 3 for c in [early, mid, late])
    if has_data:
        best_timing = max(
            [(n, c) for n, c in [("EARLY", early), ("MID", mid), ("LATE", late)] if c and c.trade_count >= 3],
            key=lambda x: x[1].expectancy,
            default=None,
        )
        if best_timing:
            lines.append(f"  A: {best_timing[0]} has highest expectancy "
                         f"({best_timing[1].expectancy:.3f}R)")
    else:
        lines.append("  A: INSUFFICIENT DATA")
    lines.append("")

    # Q3: Is wick_ratio predictive of failure?
    lines.append("  Q: Is wick_ratio predictive of failure?")
    clean = wick_cohorts.get("clean_0.0-0.2")
    high_wick = wick_cohorts.get("high_0.4-1.0")
    if clean and high_wick and clean.trade_count >= 3 and high_wick.trade_count >= 3:
        wr_delta = clean.win_rate - high_wick.win_rate
        if wr_delta > 0.1:
            lines.append(f"  A: YES — clean wick WR={clean.win_rate * 100:.1f}% vs "
                         f"high wick WR={high_wick.win_rate * 100:.1f}% (Δ={wr_delta * 100:.1f}%)")
        else:
            lines.append(f"  A: NO significant difference "
                         f"(clean={clean.win_rate * 100:.1f}%, high={high_wick.win_rate * 100:.1f}%)")
    else:
        lines.append("  A: INSUFFICIENT DATA")
    lines.append("")

    # Q4: Best combination?
    lines.append("  Q: Which strength×timing combination produces best edge?")
    best_cell = None
    best_expect = -999.0
    for strength in ("STRONG", "WEAK"):
        for timing in ("EARLY", "MID", "LATE"):
            cell = matrix[strength][timing]
            if cell.trade_count >= 3 and cell.expectancy > best_expect:
                best_expect = cell.expectancy
                best_cell = f"{strength}×{timing}"
    if best_cell:
        lines.append(f"  A: {best_cell} — expectancy {best_expect:.3f}R")
    else:
        lines.append("  A: INSUFFICIENT DATA (need ≥3 trades per cell)")
    lines.append("")

    lines.append(f"{'═' * 80}\n")
    return "\n".join(lines)


def run_full_report(records: list[dict[str, Any]]) -> str:
    """
    Run complete cohort analysis and return formatted report.

    Args:
        records: Enriched audit records (with outcome data).

    Returns:
        Complete formatted report string.
    """
    from tools.cohort_analysis.slicer import (
        slice_by_confirmation_strength,
        slice_by_entry_timing,
        slice_by_wick_ratio_band,
        slice_by_body_pct_band,
        build_interaction_matrix,
    )

    strength_cohorts = slice_by_confirmation_strength(records)
    timing_cohorts = slice_by_entry_timing(records)
    wick_cohorts = slice_by_wick_ratio_band(records)
    body_cohorts = slice_by_body_pct_band(records)
    matrix = build_interaction_matrix(records)

    report_parts = [
        f"\n{'╔' + '═' * 78 + '╗'}",
        f"{'║' + ' COHORT ANALYSIS REPORT '.center(78) + '║'}",
        f"{'║' + f' Total trades analyzed: {len(records)} '.center(78) + '║'}",
        f"{'╚' + '═' * 78 + '╝'}",
        "",
        format_cohort_table(strength_cohorts, "1. Confirmation Strength Analysis"),
        format_cohort_table(timing_cohorts, "2. Entry Timing Analysis"),
        format_cohort_table(wick_cohorts, "3. Wick Ratio Band Analysis"),
        format_cohort_table(body_cohorts, "4. Body Strength Band Analysis"),
        format_interaction_matrix(matrix),
        generate_insights(strength_cohorts, timing_cohorts, wick_cohorts, body_cohorts, matrix),
        generate_conclusions(strength_cohorts, timing_cohorts, wick_cohorts, matrix),
    ]

    return "\n".join(report_parts)
