"""
Strategy Family Diagnostics — Formatted output for observability and debugging.

Provides human-readable diagnostic reports showing:
    - Current operating mode
    - Active vs inactive families
    - Pattern distribution by family
    - Loaded research rules (if any)
    - Validation status

No side effects. Pure reporting.
"""

from __future__ import annotations

from core.strategy_family.authority import StrategyFamilyAuthority
from core.strategy_family.models import StrategyFamily
from core.strategy_family.registry import (
    FAMILY_REGISTRY,
    get_family_distribution,
    get_patterns_for_family,
)


def format_diagnostic_report(authority: StrategyFamilyAuthority) -> str:
    """
    Generate a formatted diagnostic report of the Strategy Family Authority state.

    Args:
        authority: The StrategyFamilyAuthority instance to report on.

    Returns:
        Multi-line formatted string suitable for logging or display.

    Example output:
        ═══════════════════════════════════════════════════
        STRATEGY FAMILY AUTHORITY — DIAGNOSTIC REPORT
        ═══════════════════════════════════════════════════

        Mode: PASSTHROUGH

        Active Families (have patterns):
          REVERSAL          12 patterns
          MOMENTUM           2 patterns

        Inactive Families (no patterns):
          CONTINUATION       0 patterns
          BREAKOUT           0 patterns
          MEAN_REVERSION     0 patterns

        Pattern Distribution:
          REVERSAL:        12 (86%)
          MOMENTUM:         2 (14%)
          CONTINUATION:     0 ( 0%)
          BREAKOUT:         0 ( 0%)
          MEAN_REVERSION:   0 ( 0%)

        Total Patterns Classified: 14

        Research Rules: None loaded
        ═══════════════════════════════════════════════════
    """
    diag = authority.get_diagnostic()
    distribution = get_family_distribution()
    total = sum(distribution.values())

    lines = [
        "",
        "=" * 55,
        "STRATEGY FAMILY AUTHORITY — DIAGNOSTIC REPORT",
        "=" * 55,
        "",
        f"  Mode: {diag['mode']}",
        "",
    ]

    # Active families
    active = [f for f in StrategyFamily if distribution.get(f.value, 0) > 0]
    if active:
        lines.append("  Active Families (have patterns):")
        for f in active:
            count = distribution[f.value]
            lines.append(f"    {f.value:<20} {count:>2} patterns")
    else:
        lines.append("  Active Families: None")
    lines.append("")

    # Inactive families
    inactive = [f for f in StrategyFamily if distribution.get(f.value, 0) == 0]
    if inactive:
        lines.append("  Inactive Families (no patterns):")
        for f in inactive:
            lines.append(f"    {f.value:<20}  0 patterns")
    lines.append("")

    # Distribution with percentages
    lines.append("  Pattern Distribution:")
    for f in StrategyFamily:
        count = distribution.get(f.value, 0)
        pct = (count / total * 100) if total > 0 else 0
        lines.append(f"    {f.value + ':':<18} {count:>2} ({pct:>3.0f}%)")
    lines.append("")
    lines.append(f"  Total Patterns Classified: {total}")
    lines.append("")

    # Research rules
    if diag["rules_loaded"] > 0:
        lines.append(f"  Research Rules: {diag['rules_loaded']} phase(s) loaded")
        for phase, families in diag["phase_rules"].items():
            lines.append(f"    {phase} -> {families}")
    else:
        lines.append("  Research Rules: None loaded")

    # Validation status
    if diag.get("validation"):
        lines.append("")
        lines.append("  Validation Status:")
        for key, val in diag["validation"].items():
            status = "PASSED" if val["is_valid"] else "FAILED"
            lines.append(
                f"    [{status}] {val['source']} — "
                f"n={val['sample_size']}/{val['minimum_required']}, "
                f"p={val['p_value']:.4f}, "
                f"walk_forward={val['walk_forward']}"
            )

    lines.append("")
    lines.append("=" * 55)
    lines.append("")

    return "\n".join(lines)


def format_pattern_report() -> str:
    """
    Generate a formatted report listing all patterns grouped by family.

    Returns:
        Multi-line formatted string showing pattern assignments.
    """
    lines = [
        "",
        "PATTERN → FAMILY ASSIGNMENTS",
        "-" * 40,
        "",
    ]

    for family in StrategyFamily:
        patterns = get_patterns_for_family(family)
        if patterns:
            lines.append(f"  {family.value} ({len(patterns)} patterns):")
            for p in sorted(patterns):
                lines.append(f"    - {p}")
        else:
            lines.append(f"  {family.value} (no patterns — future expansion)")
        lines.append("")

    return "\n".join(lines)


def get_summary_dict(authority: StrategyFamilyAuthority) -> dict:
    """
    Return a machine-readable summary suitable for JSON serialisation.

    Useful for research reports and command centre integration.
    """
    distribution = get_family_distribution()
    total = sum(distribution.values())

    return {
        "mode": authority.mode,
        "total_patterns": total,
        "family_distribution": distribution,
        "active_families": [
            f.value for f in StrategyFamily if distribution.get(f.value, 0) > 0
        ],
        "inactive_families": [
            f.value for f in StrategyFamily if distribution.get(f.value, 0) == 0
        ],
        "patterns_by_family": {
            f.value: get_patterns_for_family(f) for f in StrategyFamily
        },
        "library_assessment": _assess_library(distribution, total),
    }


def _assess_library(distribution: dict[str, int], total: int) -> dict:
    """Assess whether the library is balanced or skewed."""
    if total == 0:
        return {"status": "EMPTY", "note": "No patterns registered"}

    dominant_family = max(distribution, key=lambda k: distribution[k])
    dominant_pct = distribution[dominant_family] / total * 100

    if dominant_pct >= 80:
        status = "HEAVILY_SKEWED"
        note = (
            f"Library is {dominant_pct:.0f}% {dominant_family}. "
            f"Other families lack pattern detectors."
        )
    elif dominant_pct >= 60:
        status = "MODERATELY_SKEWED"
        note = f"Library leans toward {dominant_family} ({dominant_pct:.0f}%)"
    else:
        status = "BALANCED"
        note = "Pattern library covers multiple families"

    return {
        "status": status,
        "dominant_family": dominant_family,
        "dominant_percentage": round(dominant_pct, 1),
        "note": note,
    }
