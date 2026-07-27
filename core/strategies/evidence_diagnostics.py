"""
Strategy Evidence Diagnostics — Formatted reporting for evidence state.

No side effects. No execution logic. Research output only.
"""

from __future__ import annotations

from typing import Any

from core.strategies.evidence_store import StrategyEvidenceStore
from core.strategies.research_queries import (
    get_strategy_evidence_summary,
)


def strategy_evidence_report(store: StrategyEvidenceStore) -> str:
    """
    Generate a formatted report of strategy evidence state.

    Shows total observations, linked outcomes, and per-strategy performance.
    """
    summary = get_strategy_evidence_summary(store)

    lines = [
        "",
        "=" * 55,
        "STRATEGY EVIDENCE SUMMARY",
        "=" * 55,
        "",
        f"  Total Observations:   {summary['total_observations']}",
        f"  Outcomes Linked:      {summary['total_resolved']}",
        f"  Pending Outcomes:     {summary['total_pending']}",
        f"  Strategies Observed:  {summary['strategies_observed']}",
        "",
    ]

    # Per-strategy breakdown
    if summary["per_strategy"]:
        lines.append("  Per Strategy:")
        for sid, data in summary["per_strategy"].items():
            resolved = data["resolved"]
            wins = data["wins"]
            losses = data["losses"]
            obs = data["observations"]
            win_pct = f"{wins/resolved*100:.1f}%" if resolved > 0 else "N/A"
            lines.append(
                f"    {sid:<36} obs={obs:>4} "
                f"resolved={resolved:>4} W={wins} L={losses} "
                f"WR={win_pct}"
            )
        lines.append("")

    # Per-family breakdown
    if summary["per_family"]:
        lines.append("  Per Family:")
        for fam, data in summary["per_family"].items():
            n = data["sample_size"]
            wr = f"{data['win_rate']*100:.1f}%"
            avg_r = f"{data['average_r']:+.3f}R"
            lines.append(f"    {fam:<16} n={n:>4} WR={wr:>6} AvgR={avg_r}")
        lines.append("")

    lines.append("=" * 55)
    return "\n".join(lines)


def strategy_detail_report(
    store: StrategyEvidenceStore,
    strategy_id: str,
) -> str:
    """
    Generate a detailed report for a single strategy.

    Shows conditions, phase performance, and evidence quality.
    """
    stats = store.get_strategy_statistics(strategy_id)
    records = store.get_records_for_strategy(strategy_id)
    resolved = [r for r in records if r.is_resolved]

    lines = [
        "",
        "-" * 55,
        f"  Strategy: {strategy_id}",
        f"  Sample Size: {stats['sample_size']}",
        f"  Win Rate: {stats['win_rate']*100:.1f}%" if stats['sample_size'] > 0 else "  Win Rate: N/A",
        f"  Average R: {stats['average_r']:+.4f}" if stats['sample_size'] > 0 else "  Average R: N/A",
        f"  Expectancy: {stats['expectancy']:+.4f}" if stats['sample_size'] > 0 else "  Expectancy: N/A",
        f"  Confidence: {stats['confidence']}",
        "",
    ]

    # Phase breakdown
    phases: dict[str, list] = {}
    for r in resolved:
        if r.market_phase not in phases:
            phases[r.market_phase] = []
        phases[r.market_phase].append(r)

    if phases:
        lines.append("  By Phase:")
        for phase, phase_records in sorted(phases.items()):
            n = len(phase_records)
            wins = sum(1 for r in phase_records if r.is_win)
            avg_r = sum(r.realised_r for r in phase_records) / n if n > 0 else 0
            wr = f"{wins/n*100:.1f}%" if n > 0 else "N/A"
            lines.append(
                f"    {phase:<16} n={n:>3} WR={wr:>6} AvgR={avg_r:+.3f}R"
            )
        lines.append("")

    lines.append("-" * 55)
    return "\n".join(lines)
