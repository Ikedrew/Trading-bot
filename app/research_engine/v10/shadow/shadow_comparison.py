"""
Shadow Optimisation — Comparison metrics engine.

Computes aggregate performance differences between baseline and shadow results.
"""

from __future__ import annotations

import statistics
from typing import Any

from research_engine.v10.shadow.models import ShadowComparison
from research_engine.v10.research_governance.evidence_maturity import (
    assess_maturity, assess_decision, estimate_consistency, next_validation_step,
)
from research_engine.v10.research_governance.confidence_engine import ConfidenceEngine


def compute_shadow_metrics(comparisons: list[ShadowComparison]) -> dict[str, Any]:
    """
    Compute aggregate metrics from shadow comparisons.

    Returns baseline metrics, shadow metrics, and deltas.
    """
    if not comparisons:
        return {"count": 0}

    # Separate by decision type
    both_execute = [c for c in comparisons
                    if c.baseline_decision == "EXECUTE" and c.shadow_decision == "EXECUTE"]
    shadow_only = [c for c in comparisons
                   if c.baseline_decision == "NO_TRADE" and c.shadow_decision == "EXECUTE"]
    baseline_only = [c for c in comparisons
                     if c.baseline_decision == "EXECUTE" and c.shadow_decision == "NO_TRADE"]

    # Baseline metrics (from trades that actually executed)
    baseline_rs = [c.baseline_r for c in comparisons if c.baseline_decision == "EXECUTE"]
    shadow_rs = [c.shadow_r for c in comparisons if c.shadow_decision == "EXECUTE"]

    b_count = len(baseline_rs)
    s_count = len(shadow_rs)

    baseline_metrics = _compute_group(baseline_rs)
    shadow_metrics = _compute_group(shadow_rs)

    # Deltas
    delta_exp = shadow_metrics["expectancy_r"] - baseline_metrics["expectancy_r"]
    delta_wr = shadow_metrics["win_rate"] - baseline_metrics["win_rate"]

    return {
        "count": len(comparisons),
        "both_execute": len(both_execute),
        "shadow_only_trades": len(shadow_only),
        "baseline_only_trades": len(baseline_only),
        "baseline": baseline_metrics,
        "shadow": shadow_metrics,
        "delta": {
            "expectancy_r": round(delta_exp, 4),
            "win_rate": round(delta_wr, 4),
            "trade_count": s_count - b_count,
        },
    }


def evaluate_shadow_evidence(comparisons: list[ShadowComparison]) -> dict[str, Any]:
    """
    Apply progressive governance to shadow evidence.

    Returns maturity, confidence, decision, next step.
    """
    metrics = compute_shadow_metrics(comparisons)
    if metrics["count"] == 0:
        return {"maturity": "EXPLORATORY", "confidence": "LOW",
                "decision": "INVESTIGATE", "next_step": "Collect observations."}

    shadow = metrics["shadow"]
    delta = metrics["delta"]
    sample = shadow.get("count", 0)
    effect = delta.get("expectancy_r", 0)

    consistency = estimate_consistency(shadow)
    maturity = assess_maturity(sample, abs(effect), consistency)

    decision_result = assess_decision(
        sample_size=sample,
        effect_size=effect,
        confidence_score=0.5,
        maturity=maturity,
        is_deterioration=effect < -0.1,
    )

    ce = ConfidenceEngine()
    conf = ce.assess(sample_size=sample, effect_size=abs(effect),
                     recommendation=decision_result["status"])

    step = next_validation_step(decision_result["status"], maturity, sample)

    return {
        "maturity": maturity,
        "confidence": conf["confidence"],
        "confidence_score": conf["score"],
        "decision": decision_result["status"],
        "reason": decision_result["reason"],
        "next_step": step,
        "metrics": metrics,
    }


def _compute_group(r_values: list[float]) -> dict[str, Any]:
    """Compute performance metrics from R-multiple list."""
    if not r_values:
        return {"count": 0, "expectancy_r": 0, "win_rate": 0, "profit_factor": 0}

    n = len(r_values)
    winners = [r for r in r_values if r > 0]
    losers = [r for r in r_values if r <= 0]
    win_rate = len(winners) / n
    avg_win = statistics.mean(winners) if winners else 0
    avg_loss = statistics.mean(losers) if losers else 0
    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

    gross_win = sum(r for r in r_values if r > 0)
    gross_loss = abs(sum(r for r in r_values if r < 0))
    pf = gross_win / gross_loss if gross_loss > 0 else (999 if gross_win > 0 else 0)

    return {
        "count": n,
        "expectancy_r": round(expectancy, 4),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(pf, 2),
        "average_r": round(statistics.mean(r_values), 4),
        "total_r": round(sum(r_values), 4),
    }
