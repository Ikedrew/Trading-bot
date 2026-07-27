"""
Strategy Condition Evaluation Diagnostics — Formatted output
showing condition pass/fail status for each strategy.

Observation only. No side effects. No trading influence.

Example output:
    ═══════════════════════════════════════════════════
    STRATEGY CONDITION EVALUATION
    ═══════════════════════════════════════════════════

    Strategy: range_reversal_v1
    Status:   PARTIALLY_MET
    Phase:    ELIGIBLE

    Environment:
      ✅ regime_is_ranging          RANGING in ['RANGING']
      ✅ phase_is_reversal_or_...   REVERSAL in ['REVERSAL', 'EXHAUSTION']

    Conditions:
      ✅ at_key_level               True (truthy)
      ❌ no_strong_momentum_...     bias_strength=85 <= 70 → FAIL
      ✅ reversal_pattern_detected  HAMMER is in strategy triggers
      ⚠️  structure_quality_...     No data for 'm15.quality_score'

    Result: 3/4 passed | 1 missing
    ═══════════════════════════════════════════════════
"""

from __future__ import annotations

from typing import Any

from core.strategies.condition_evaluator import (
    ConditionEvaluationResult,
    StrategyConditionEvaluator,
)
from core.strategies.conditions import (
    ConditionCategory,
    ConditionEvaluation,
    ConditionResult,
)


# ═══════════════════════════════════════════════════════════════════════════════
# STATUS ICONS
# ═══════════════════════════════════════════════════════════════════════════════

_ICONS = {
    ConditionResult.PASSED: "[PASS]",
    ConditionResult.FAILED: "[FAIL]",
    ConditionResult.MISSING_DATA: "[????]",
    ConditionResult.NOT_APPLICABLE: "[N/A ]",
}


def _icon(result: ConditionResult) -> str:
    return _ICONS.get(result, "[????]")


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLE STRATEGY REPORT
# ═══════════════════════════════════════════════════════════════════════════════


def format_evaluation_report(result: ConditionEvaluationResult) -> str:
    """
    Generate a formatted diagnostic report for one strategy evaluation.

    Args:
        result: ConditionEvaluationResult from the evaluator.

    Returns:
        Multi-line formatted string showing pass/fail per condition.
    """
    lines = [
        "",
        "-" * 55,
        f"  Strategy: {result.strategy_id}",
        f"  Status:   {result.overall_status}",
        f"  Phase:    {'ELIGIBLE' if result.eligible_by_phase else 'NOT ELIGIBLE'}",
        f"  Confidence: {result.confidence:.0%}",
        "",
    ]

    # Group evaluations by category
    env_evals = [
        e for e in result.evaluations
        if e.condition.category == ConditionCategory.ENVIRONMENT
    ]
    entry_evals = [
        e for e in result.evaluations
        if e.condition.category != ConditionCategory.ENVIRONMENT
    ]

    if env_evals:
        lines.append("  Environment:")
        for e in env_evals:
            req = "*" if e.condition.required else " "
            icon = _icon(e.result)
            name = e.condition.name[:30]
            lines.append(f"    {icon}{req} {name:<32} {e.explanation}")
        lines.append("")

    if entry_evals:
        lines.append("  Conditions:")
        for e in entry_evals:
            req = "*" if e.condition.required else " "
            icon = _icon(e.result)
            name = e.condition.name[:30]
            lines.append(f"    {icon}{req} {name:<32} {e.explanation}")
        lines.append("")

    # Summary line
    summary_parts = [f"{result.conditions_passed}/{result.conditions_checked} passed"]
    if result.missing_data:
        summary_parts.append(f"{len(result.missing_data)} missing")
    if result.unavailable_conditions:
        summary_parts.append(f"{len(result.unavailable_conditions)} unavailable")
    if result.conditions_failed > 0:
        summary_parts.append(f"{result.conditions_failed} failed")

    lines.append(f"  Result: {' | '.join(summary_parts)}")
    lines.append("-" * 55)

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-STRATEGY REPORT
# ═══════════════════════════════════════════════════════════════════════════════


def format_full_evaluation_report(
    results: list[ConditionEvaluationResult],
) -> str:
    """
    Generate a complete diagnostic report for all strategy evaluations.

    Args:
        results: List of ConditionEvaluationResult from evaluate_all().

    Returns:
        Multi-line formatted report covering all strategies.
    """
    lines = [
        "",
        "=" * 55,
        "STRATEGY CONDITION EVALUATION — FULL REPORT",
        "=" * 55,
        "",
    ]

    # Summary table
    lines.append("  SUMMARY:")
    lines.append(f"  {'Strategy':<38} {'Status':<16} {'Pass'}")
    lines.append(f"  {'-'*38} {'-'*16} {'-'*6}")

    for r in results:
        pass_str = f"{r.conditions_passed}/{r.conditions_checked}"
        lines.append(f"  {r.strategy_id:<38} {r.overall_status:<16} {pass_str}")

    lines.append("")

    # Eligible strategies
    eligible = [r for r in results if r.eligible_by_phase]
    if eligible:
        lines.append(f"  Phase-eligible: {[r.strategy_id for r in eligible]}")
    else:
        lines.append("  Phase-eligible: None")

    fully_met = [r for r in results if r.overall_status == "FULLY_MET"]
    if fully_met:
        lines.append(f"  Fully met: {[r.strategy_id for r in fully_met]}")
    lines.append("")

    # Individual reports
    for r in results:
        lines.append(format_evaluation_report(r))

    lines.append("")
    lines.append("=" * 55)

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# MACHINE-READABLE SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════


def get_evaluation_summary(
    results: list[ConditionEvaluationResult],
) -> dict[str, Any]:
    """
    Return a machine-readable summary of all evaluations.

    Useful for research logging, JSON persistence, and command centre.
    """
    return {
        "total_strategies": len(results),
        "phase_eligible": [r.strategy_id for r in results if r.eligible_by_phase],
        "fully_met": [r.strategy_id for r in results if r.overall_status == "FULLY_MET"],
        "partially_met": [r.strategy_id for r in results if r.overall_status == "PARTIALLY_MET"],
        "not_met": [r.strategy_id for r in results if r.overall_status == "NOT_MET"],
        "incomplete": [r.strategy_id for r in results if r.overall_status == "INCOMPLETE"],
        "strategies": {
            r.strategy_id: {
                "status": r.overall_status,
                "eligible_by_phase": r.eligible_by_phase,
                "confidence": r.confidence,
                "passed": r.conditions_passed,
                "checked": r.conditions_checked,
                "failed": r.conditions_failed,
                "missing_data": list(r.missing_data),
                "unavailable": list(r.unavailable_conditions),
            }
            for r in results
        },
    }
