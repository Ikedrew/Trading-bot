"""
Policy Validation Report — Compares simulated expectancy vs real trade outcomes.

Generates evidence for whether cohort policies are valid, overfitted, or underexploited.

STRICTLY ANALYTICAL — no live system interaction, no trading engine modifications.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any


# ─── RESULT TYPES ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CohortValidation:
    """Validation result for a single cohort."""

    cohort_id: str
    sample_size: int
    avg_real_r: float
    avg_simulated_r: float
    real_variance: float
    expectancy_gap: float
    verdict: str  # "VALIDATED" / "UNDEREXPLOITED" / "OVERFITTED"


@dataclass(frozen=True)
class PolicyAction:
    """Recommended action for a cohort policy."""

    cohort_id: str
    current_policy: str
    action: str  # "INCREASE" / "REDUCE" / "DISABLE" / "MAINTAIN"
    reason: str


# ─── COHORT AGGREGATION ───────────────────────────────────────────────────────

def aggregate_by_cohort(
    enriched_trades: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """
    Aggregate real and simulated outcomes per cohort.

    Args:
        enriched_trades: List of trade dicts with cohort_id, r_multiple,
                         and optionally counterfactual.break_even.outcome_r
                         or simulated_r field.

    Returns:
        Dict mapping cohort_id → {real_outcomes, simulated_outcomes, sample_size}
    """
    cohorts: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "real_outcomes": [],
        "simulated_outcomes": [],
    })

    for trade in enriched_trades:
        cohort_id = trade.get("cohort_id", "UNKNOWN")

        # Real outcome
        real_r = trade.get("r_multiple") or trade.get("final_r")
        if real_r is not None:
            cohorts[cohort_id]["real_outcomes"].append(float(real_r))

        # Simulated outcome (from Phase 4A counterfactual or explicit field)
        sim_r = _extract_simulated_r(trade)
        if sim_r is not None:
            cohorts[cohort_id]["simulated_outcomes"].append(float(sim_r))

    return dict(cohorts)


def _extract_simulated_r(trade: dict[str, Any]) -> float | None:
    """Extract simulated R from counterfactual data or explicit field."""
    # Direct field
    if trade.get("simulated_r") is not None:
        return float(trade["simulated_r"])

    # From Phase 4A counterfactual (use trailing as best alternative)
    cf = trade.get("counterfactual")
    if cf and isinstance(cf, dict):
        trail = cf.get("trailing_stop", {})
        if trail.get("exit_r") is not None:
            return float(trail["exit_r"])
        be = cf.get("break_even", {})
        if be.get("outcome_r") is not None:
            return float(be["outcome_r"])

    return None


# ─── DELTA ANALYSIS ───────────────────────────────────────────────────────────

def compute_validation(cohort_data: dict[str, dict[str, Any]]) -> list[CohortValidation]:
    """
    Compute expectancy gap and verdict per cohort.

    Args:
        cohort_data: Output from aggregate_by_cohort().

    Returns:
        List of CohortValidation results.
    """
    results: list[CohortValidation] = []

    for cohort_id, data in cohort_data.items():
        real = data["real_outcomes"]
        simulated = data["simulated_outcomes"]

        if not real:
            continue

        sample_size = len(real)
        avg_real = sum(real) / len(real)

        # Variance
        mean = avg_real
        variance = sum((x - mean) ** 2 for x in real) / len(real) if len(real) > 1 else 0.0

        # Simulated average (use real as fallback if no simulation data)
        avg_sim = sum(simulated) / len(simulated) if simulated else avg_real

        gap = avg_real - avg_sim

        # Verdict
        if abs(gap) <= 0.2:
            verdict = "VALIDATED"
        elif gap > 0.2:
            verdict = "UNDEREXPLOITED"
        else:
            verdict = "OVERFITTED"

        results.append(CohortValidation(
            cohort_id=cohort_id,
            sample_size=sample_size,
            avg_real_r=round(avg_real, 4),
            avg_simulated_r=round(avg_sim, 4),
            real_variance=round(variance, 4),
            expectancy_gap=round(gap, 4),
            verdict=verdict,
        ))

    return results


# ─── POLICY RECOMMENDATIONS ──────────────────────────────────────────────────

def generate_policy_actions(
    validations: list[CohortValidation],
    current_policies: dict[str, str] | None = None,
) -> list[PolicyAction]:
    """
    Generate policy adjustment recommendations from validation results.

    Args:
        validations: List of CohortValidation.
        current_policies: Optional mapping cohort_id → current policy name.

    Returns:
        List of PolicyAction recommendations.
    """
    actions: list[PolicyAction] = []

    for v in validations:
        policy_name = (current_policies or {}).get(v.cohort_id, "STANDARD")

        if v.sample_size < 5:
            actions.append(PolicyAction(
                cohort_id=v.cohort_id,
                current_policy=policy_name,
                action="MAINTAIN",
                reason=f"Insufficient sample ({v.sample_size} trades). Keep current policy.",
            ))
            continue

        if v.verdict == "UNDEREXPLOITED":
            actions.append(PolicyAction(
                cohort_id=v.cohort_id,
                current_policy=policy_name,
                action="INCREASE",
                reason=f"Real R ({v.avg_real_r:.2f}) exceeds model ({v.avg_simulated_r:.2f}). "
                       f"Gap: +{v.expectancy_gap:.2f}R. Consider more aggressive trailing/targets.",
            ))
        elif v.verdict == "OVERFITTED":
            if v.avg_real_r < 0:
                actions.append(PolicyAction(
                    cohort_id=v.cohort_id,
                    current_policy=policy_name,
                    action="DISABLE",
                    reason=f"Real expectancy negative ({v.avg_real_r:.2f}R). "
                           f"Gap: {v.expectancy_gap:.2f}R. Consider filtering this cohort entirely.",
                ))
            else:
                actions.append(PolicyAction(
                    cohort_id=v.cohort_id,
                    current_policy=policy_name,
                    action="REDUCE",
                    reason=f"Model overestimates ({v.avg_simulated_r:.2f}R vs real {v.avg_real_r:.2f}R). "
                           f"Gap: {v.expectancy_gap:.2f}R. Tighten targets or reduce position.",
                ))
        else:
            actions.append(PolicyAction(
                cohort_id=v.cohort_id,
                current_policy=policy_name,
                action="MAINTAIN",
                reason=f"Model validated. Real ({v.avg_real_r:.2f}R) ≈ Simulated ({v.avg_simulated_r:.2f}R). "
                       f"Gap: {v.expectancy_gap:+.2f}R.",
            ))

    return actions


# ─── REPORT GENERATION ────────────────────────────────────────────────────────

def generate_validation_report(enriched_trades: list[dict[str, Any]]) -> str:
    """
    Generate full policy validation report from enriched trade data.

    Args:
        enriched_trades: List of trade dicts with cohort_id, r_multiple,
                         and optionally counterfactual simulation data.

    Returns:
        Formatted report string.
    """
    cohort_data = aggregate_by_cohort(enriched_trades)
    validations = compute_validation(cohort_data)
    actions = generate_policy_actions(validations)

    lines = [
        f"\n{'═' * 80}",
        "  POLICY VALIDATION REPORT",
        f"{'═' * 80}",
        "",
        f"  {'Cohort':<30} {'Sim R':>7} {'Real R':>7} {'Gap':>7} {'N':>5} {'Verdict':<15}",
        f"  {'─' * 75}",
    ]

    for v in sorted(validations, key=lambda x: x.expectancy_gap, reverse=True):
        lines.append(
            f"  {v.cohort_id:<30} {v.avg_simulated_r:>7.3f} {v.avg_real_r:>7.3f} "
            f"{v.expectancy_gap:>+7.3f} {v.sample_size:>5d} {v.verdict:<15}"
        )

    lines.append("")
    lines.append(f"  {'─' * 75}")
    lines.append("  RECOMMENDED ACTIONS:")
    lines.append(f"  {'─' * 75}")

    for a in actions:
        lines.append(f"  [{a.action:>8}] {a.cohort_id}: {a.reason}")

    lines.append(f"\n{'═' * 80}\n")
    return "\n".join(lines)
