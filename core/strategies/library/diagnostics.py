"""
Strategy Knowledge Library — Diagnostics.

Formatted reporting of library contents. No side effects.
No execution logic. No calculations.
"""

from __future__ import annotations

from typing import Any

from core.strategies.library.models import (
    EvidenceStatus,
    StrategyDefinition,
    StrategyFamily,
)
from core.strategies.library.registry import (
    FAMILY_DEFINITIONS,
    STRATEGY_LIBRARY,
    get_all_strategies,
    get_strategies_by_family,
    get_strategies_for_context,
)


def strategy_library_report() -> str:
    """
    Generate a formatted report of the Strategy Knowledge Library.

    Shows family distribution, strategy counts, and evidence status.
    """
    all_strategies = get_all_strategies()
    total = len(all_strategies)

    lines = [
        "",
        "=" * 55,
        "STRATEGY KNOWLEDGE LIBRARY — REPORT",
        "=" * 55,
        "",
        f"  Total Strategies: {total}",
        f"  Total Families:   {len(StrategyFamily)}",
        "",
        "  Family Distribution:",
    ]

    for family in StrategyFamily:
        strategies = get_strategies_by_family(family)
        count = len(strategies)
        lines.append(f"    {family.value:<16} {count} strategies")

    lines.append("")
    lines.append("  Strategies by Family:")

    for family in StrategyFamily:
        strategies = get_strategies_by_family(family)
        fam_def = FAMILY_DEFINITIONS.get(family)
        hypothesis = fam_def.hypothesis if fam_def else ""
        lines.append(f"")
        lines.append(f"    {family.value}:")
        lines.append(f"      Hypothesis: {hypothesis}")
        for s in strategies:
            status = f"[{s.evidence_status.value}]"
            lines.append(f"      - {s.strategy_id} {status}")

    lines.append("")
    lines.append("  Evidence Status:")
    for status in EvidenceStatus:
        count = sum(1 for s in all_strategies if s.evidence_status == status)
        if count > 0:
            lines.append(f"    {status.value:<16} {count}")

    lines.append("")
    lines.append("=" * 55)

    return "\n".join(lines)


def context_query_report(*, phase: str = "", regime: str = "") -> str:
    """
    Generate a report showing which strategies match a given context.

    Args:
        phase: Market phase to query (e.g. "IMPULSE")
        regime: Market regime to query (e.g. "TRENDING")

    Returns:
        Formatted report of eligible strategies with reasons.
    """
    results = get_strategies_for_context(phase=phase, regime=regime)

    lines = [
        "",
        "-" * 55,
        f"  Context Query: phase={phase or 'ANY'}, regime={regime or 'ANY'}",
        f"  Eligible Strategies: {len(results)}",
        "-" * 55,
        "",
    ]

    if not results:
        lines.append("  No strategies match this context.")
    else:
        for s in results:
            reasons = []
            if phase and phase in s.valid_market_phases:
                reasons.append(f"Valid during {phase} phase")
            if regime and regime in s.valid_regimes:
                reasons.append(f"Valid in {regime} regime")
            reason_str = "; ".join(reasons) if reasons else "Matches filters"

            lines.append(f"  {s.strategy_id}")
            lines.append(f"    Family:     {s.family_name}")
            lines.append(f"    Hypothesis: {s.hypothesis[:70]}...")
            lines.append(f"    Reason:     {reason_str}")
            lines.append(f"    Conditions: {list(s.required_conditions)}")
            lines.append("")

    lines.append("-" * 55)
    return "\n".join(lines)


def get_library_summary() -> dict[str, Any]:
    """
    Return a machine-readable summary of the library.

    Suitable for JSON serialisation, research reports, command centre.
    """
    all_strategies = get_all_strategies()

    return {
        "total_strategies": len(all_strategies),
        "total_families": len(StrategyFamily),
        "family_distribution": {
            f.value: len(get_strategies_by_family(f))
            for f in StrategyFamily
        },
        "evidence_distribution": {
            status.value: sum(
                1 for s in all_strategies if s.evidence_status == status
            )
            for status in EvidenceStatus
            if any(s.evidence_status == status for s in all_strategies)
        },
        "strategies": {
            s.strategy_id: {
                "family": s.family_name,
                "phases": list(s.valid_market_phases),
                "regimes": list(s.valid_regimes),
                "conditions": list(s.required_conditions),
                "evidence": s.evidence_status.value,
            }
            for s in all_strategies
        },
    }
