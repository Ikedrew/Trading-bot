"""
Strategy Framework Diagnostics — Formatted output for observability.

Provides human-readable diagnostic reports showing:
    - Current operating mode
    - Registered strategies grouped by family
    - Status distribution
    - Evidence gaps
    - Safety verification

No side effects. Pure reporting.
"""

from __future__ import annotations

from core.strategy_family.models import StrategyFamily
from core.strategies.authority import StrategyAuthority
from core.strategies.models import StrategyDefinition, StrategyStatus
from core.strategies.registry import (
    get_all_strategies,
    get_status_distribution,
    get_strategies_by_family,
)


def format_diagnostic_report(authority: StrategyAuthority) -> str:
    """
    Generate a formatted diagnostic report of the Strategy Framework state.

    Args:
        authority: The StrategyAuthority instance to report on.

    Returns:
        Multi-line formatted string suitable for logging or display.
    """
    diag = authority.get_diagnostic()
    all_strategies = get_all_strategies()
    distribution = get_status_distribution()

    lines = [
        "",
        "=" * 55,
        "STRATEGY FRAMEWORK — DIAGNOSTIC REPORT",
        "=" * 55,
        "",
        f"  Mode: {diag['mode']}",
        f"  Total Strategies: {diag['total_strategies']}",
        f"  Safety Check: {'PASSED' if diag['safety_check'] else 'FAILED'}",
        "",
    ]

    # Strategies by family
    lines.append("  Registered Strategies:")
    for family in StrategyFamily:
        strategies = get_strategies_by_family(family)
        if strategies:
            lines.append(f"    {family.value}:")
            for s in strategies:
                status_marker = f"[{s.status.value}]"
                patterns_note = (
                    f" ({len(s.trigger_patterns)} patterns)"
                    if s.trigger_patterns else " (no patterns)"
                )
                lines.append(f"      - {s.strategy_id} {status_marker}{patterns_note}")
        else:
            lines.append(f"    {family.value}: (no strategies defined)")
    lines.append("")

    # Status distribution
    lines.append("  Status Distribution:")
    for status in StrategyStatus:
        count = distribution.get(status.value, 0)
        lines.append(f"    {status.value:<16} {count}")
    lines.append("")

    # Evidence summary
    lines.append("  Evidence Summary:")
    for s in all_strategies:
        ev = s.evidence_status
        if ev.has_evidence:
            lines.append(
                f"    {s.strategy_id}: n={ev.sample_size}, "
                f"EV={ev.expectancy_r:.3f}R, p={ev.p_value:.4f}"
            )
        else:
            lines.append(f"    {s.strategy_id}: NO EVIDENCE")
    lines.append("")

    # Gaps and warnings
    warnings = _identify_warnings(all_strategies)
    if warnings:
        lines.append("  Warnings:")
        for w in warnings:
            lines.append(f"    - {w}")
        lines.append("")

    lines.append("=" * 55)
    lines.append("")

    return "\n".join(lines)


def get_summary_dict(authority: StrategyAuthority) -> dict:
    """
    Return a machine-readable summary suitable for JSON serialisation.
    """
    all_strategies = get_all_strategies()
    distribution = get_status_distribution()

    return {
        "mode": authority.mode,
        "total_strategies": len(all_strategies),
        "status_distribution": distribution,
        "strategies_by_family": {
            f.value: [s.strategy_id for s in get_strategies_by_family(f)]
            for f in StrategyFamily
        },
        "active_count": distribution.get("ACTIVE", 0),
        "safety_passed": authority.verify_no_active_strategies(),
        "evidence_gaps": [
            s.strategy_id for s in all_strategies
            if not s.evidence_status.has_evidence
        ],
        "pattern_gaps": [
            s.strategy_id for s in all_strategies
            if not s.trigger_patterns
        ],
        "warnings": _identify_warnings(all_strategies),
    }


def _identify_warnings(strategies: list[StrategyDefinition]) -> list[str]:
    """Identify potential issues in the strategy framework."""
    warnings = []

    # Check for strategies without trigger patterns
    no_patterns = [s for s in strategies if not s.trigger_patterns]
    if no_patterns:
        warnings.append(
            f"{len(no_patterns)} strategies have no trigger patterns: "
            f"{[s.strategy_id for s in no_patterns]}"
        )

    # Check for active strategies (should be zero)
    active = [s for s in strategies if s.status == StrategyStatus.ACTIVE]
    if active:
        warnings.append(
            f"SAFETY: {len(active)} strategies are ACTIVE but should not be: "
            f"{[s.strategy_id for s in active]}"
        )

    # Check family coverage
    families_with_strategies = set(s.strategy_family for s in strategies)
    missing_families = set(StrategyFamily) - families_with_strategies
    if missing_families:
        warnings.append(
            f"No strategies defined for families: "
            f"{[f.value for f in missing_families]}"
        )

    return warnings
