"""
Strategy Confidence Score System — Unified evaluation from all validation subsystems.

Aggregates outputs from:
    1. Walk-Forward Engine (time robustness)
    2. Shadow Execution Engine (execution value)
    3. Rule Interaction Engine (system stability)

Into a single weighted Strategy Confidence Score (0-100).

This module does NOT re-analyse data. It ONLY synthesises existing results.

Usage:
    from analysis.confidence_score import compute_confidence_score

    result = compute_confidence_score(
        walk_forward_path="analysis/reports/walk_forward.json",
        shadow_path="analysis/reports/shadow_execution.json",
        interactions_path="analysis/reports/rule_interactions.json",
    )
    print(result["overall_confidence"])
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# WEIGHTS
# ═══════════════════════════════════════════════════════════════════════════════

WEIGHT_TIME_ROBUSTNESS = 0.40    # Most important — does the edge persist over time?
WEIGHT_EXECUTION_VALUE = 0.35    # Does the rule set actually improve PnL?
WEIGHT_SYSTEM_STABILITY = 0.25   # Is the rule set internally coherent?

# Confidence thresholds
CONFIDENCE_HIGH = 75
CONFIDENCE_MEDIUM = 50
CONFIDENCE_LOW = 30


# ═══════════════════════════════════════════════════════════════════════════════
# INPUT LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def _load_json(path: str) -> dict[str, Any] | None:
    """Load a JSON report file. Returns None if missing."""
    p = Path(path)
    if not p.exists():
        logger.warning("[CONFIDENCE] File not found: %s", path)
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. TIME ROBUSTNESS SCORE (from walk-forward)
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_time_robustness(wf: dict[str, Any] | None) -> dict[str, Any]:
    """
    Derive time robustness score (0-100) from walk-forward results.

    Components:
        - Pattern stability average (40%)
        - Edge decay penalty (30%)
        - Regime consistency (30%)
    """
    if wf is None:
        return {"score": 0, "available": False, "reason": "walk_forward data unavailable"}

    stability_list = wf.get("pattern_stability", [])
    summary = wf.get("overall_summary", {})
    windows = wf.get("walk_forward_windows", [])

    if not stability_list:
        return {"score": 0, "available": True, "reason": "no_pattern_stability_data"}

    # 1. Average pattern stability score (0-100)
    stability_scores = [s.get("stability_score", 0) for s in stability_list]
    avg_stability = sum(stability_scores) / len(stability_scores) if stability_scores else 0

    # 2. Edge decay penalty: how many windows show degradation
    total_windows = len(windows)
    degraded = sum(
        1 for w in windows if w.get("delta_metrics", {}).get("pnl_degraded", False)
    )
    decay_ratio = degraded / total_windows if total_windows > 0 else 0
    edge_persistence = (1 - decay_ratio) * 100  # 100 = no decay

    # 3. Regime consistency: low sensitivity = good
    sensitivity_map = {"low": 100, "medium": 60, "high": 20, "unknown": 50}
    regime_scores = [
        sensitivity_map.get(s.get("regime_sensitivity", "unknown"), 50)
        for s in stability_list
    ]
    avg_regime = sum(regime_scores) / len(regime_scores) if regime_scores else 50

    # Combined score
    score = int(avg_stability * 0.40 + edge_persistence * 0.30 + avg_regime * 0.30)
    score = min(100, max(0, score))

    return {
        "score": score,
        "available": True,
        "components": {
            "avg_pattern_stability": round(avg_stability, 1),
            "edge_persistence": round(edge_persistence, 1),
            "regime_consistency": round(avg_regime, 1),
        },
        "edge_decay_detected": summary.get("overall_edge_decay", False),
        "patterns_assessed": len(stability_list),
        "windows_used": total_windows,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. EXECUTION VALUE SCORE (from shadow engine)
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_execution_value(shadow: dict[str, Any] | None) -> dict[str, Any]:
    """
    Derive execution value score (0-100) from shadow execution results.

    Components:
        - PnL improvement ratio (40%)
        - Loss avoidance efficiency (35%)
        - Divergence moderation (25%) — too much divergence = risky
    """
    if shadow is None:
        return {"score": 0, "available": False, "reason": "shadow_execution data unavailable"}

    baseline = shadow.get("baseline_results", {})
    shadow_res = shadow.get("shadow_results", {})
    divergence = shadow.get("divergence_metrics", {})

    baseline_pnl = baseline.get("total_pnl", 0)
    shadow_pnl = shadow_res.get("total_pnl", 0)
    baseline_trades = baseline.get("trades", 1)

    if baseline_trades == 0:
        return {"score": 0, "available": True, "reason": "no_baseline_trades"}

    # 1. PnL improvement (0-100): how much better is shadow vs baseline
    if baseline_pnl > 0:
        improvement_ratio = (shadow_pnl - baseline_pnl) / baseline_pnl
    elif baseline_pnl < 0:
        # Negative baseline: any improvement is very valuable
        improvement_ratio = (shadow_pnl - baseline_pnl) / abs(baseline_pnl)
    else:
        improvement_ratio = 1.0 if shadow_pnl > 0 else 0.0

    # Cap and scale: 0% improvement = 50, 20%+ improvement = 100, negative = 0-50
    pnl_score = min(100, max(0, 50 + improvement_ratio * 250))

    # 2. Loss avoidance efficiency (0-100)
    avoided = divergence.get("avoided_losses", 0)
    missed = divergence.get("missed_opportunities", 0)
    total_blocked = divergence.get("trades_diverged", 0)

    if total_blocked > 0:
        efficiency = avoided / total_blocked  # What fraction of blocks were losses
        loss_score = efficiency * 100
    else:
        loss_score = 50  # Neutral — no rules fired

    # 3. Divergence moderation (0-100): sweet spot is 5-25% divergence
    div_rate = divergence.get("divergence_rate", 0)
    if div_rate < 3:
        div_score = 30  # Too little impact — rules aren't doing anything
    elif div_rate <= 25:
        div_score = 100  # Sweet spot
    elif div_rate <= 40:
        div_score = 70  # Moderate — somewhat aggressive
    else:
        div_score = max(0, 100 - (div_rate - 25) * 2)  # Overly aggressive

    # Combined score
    score = int(pnl_score * 0.40 + loss_score * 0.35 + div_score * 0.25)
    score = min(100, max(0, score))

    return {
        "score": score,
        "available": True,
        "components": {
            "pnl_improvement_score": round(pnl_score, 1),
            "loss_avoidance_efficiency": round(loss_score, 1),
            "divergence_moderation": round(div_score, 1),
        },
        "pnl_delta": round(shadow_pnl - baseline_pnl, 2),
        "divergence_rate": div_rate,
        "avoided_losses": avoided,
        "missed_opportunities": missed,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SYSTEM STABILITY SCORE (from rule interactions)
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_system_stability(interactions: dict[str, Any] | None) -> dict[str, Any]:
    """
    Derive system stability score (0-100) from rule interaction analysis.

    Inverts risk metrics: low risk = high stability score.

    Components:
        - Rule stack risk (inverted) (40%)
        - Conflict severity (inverted) (35%)
        - Redundancy load (inverted) (25%)
    """
    if interactions is None:
        return {"score": 0, "available": False, "reason": "rule_interactions data unavailable"}

    risk = interactions.get("system_risk", {})
    conflicts = interactions.get("conflicts", [])
    redundancies = interactions.get("redundancies", [])

    # 1. Rule stack risk inverted (0-100 → 100-0)
    stack_risk = risk.get("rule_stack_risk_score", 0)
    stack_stability = 100 - stack_risk

    # 2. Conflict severity inverted
    if conflicts:
        avg_severity = sum(c.get("severity", 0) for c in conflicts) / len(conflicts)
        # Scale: 0 conflicts = 100, avg severity 100 = 0
        conflict_stability = max(0, 100 - avg_severity * len(conflicts) / 3)
    else:
        conflict_stability = 100

    # 3. Redundancy load inverted
    total_redundant = sum(r.get("count", 0) - 1 for r in redundancies)
    total_rules = interactions.get("metadata", {}).get("total_rules_analysed", 1)
    redundancy_ratio = total_redundant / max(total_rules, 1)
    redundancy_stability = max(0, 100 - redundancy_ratio * 150)

    # Combined score
    score = int(
        stack_stability * 0.40 +
        conflict_stability * 0.35 +
        redundancy_stability * 0.25
    )
    score = min(100, max(0, score))

    return {
        "score": score,
        "available": True,
        "components": {
            "stack_stability": round(stack_stability, 1),
            "conflict_stability": round(conflict_stability, 1),
            "redundancy_stability": round(redundancy_stability, 1),
        },
        "instability_flag": risk.get("instability_flag", False),
        "conflict_count": len(conflicts),
        "redundancy_clusters": len(redundancies),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 4. OVERALL STRATEGY CONFIDENCE SCORE
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_overall(
    time_robustness: dict[str, Any],
    execution_value: dict[str, Any],
    system_stability: dict[str, Any],
) -> dict[str, Any]:
    """
    Compute weighted overall confidence score.

    Weights:
        Time Robustness:   40% (most important)
        Execution Value:   35%
        System Stability:  25%

    Grade thresholds:
        A (75-100): High confidence — safe to promote rules
        B (50-74):  Moderate — shadow test longer
        C (30-49):  Low — significant concerns
        F (0-29):   Fail — do not deploy
    """
    tr_score = time_robustness.get("score", 0)
    ev_score = execution_value.get("score", 0)
    ss_score = system_stability.get("score", 0)

    # Count available subsystems for adjusted weighting
    available = sum([
        time_robustness.get("available", False),
        execution_value.get("available", False),
        system_stability.get("available", False),
    ])

    if available == 0:
        return {
            "score": 0,
            "grade": "F",
            "verdict": "NO_DATA",
            "reason": "No subsystem data available for scoring.",
        }

    # Weighted score
    overall = (
        tr_score * WEIGHT_TIME_ROBUSTNESS +
        ev_score * WEIGHT_EXECUTION_VALUE +
        ss_score * WEIGHT_SYSTEM_STABILITY
    )
    overall = int(min(100, max(0, overall)))

    # Grade assignment
    if overall >= CONFIDENCE_HIGH:
        grade = "A"
        verdict = "PROMOTE"
        reason = "High confidence. Strategy shows stable edge with positive execution impact and coherent rules."
    elif overall >= CONFIDENCE_MEDIUM:
        grade = "B"
        verdict = "SHADOW_EXTEND"
        reason = "Moderate confidence. Continue shadow testing to confirm stability before promotion."
    elif overall >= CONFIDENCE_LOW:
        grade = "C"
        verdict = "REVIEW"
        reason = "Low confidence. Significant concerns in one or more dimensions. Review before proceeding."
    else:
        grade = "F"
        verdict = "REJECT"
        reason = "Insufficient confidence. Strategy does not meet deployment criteria."

    # Override: if instability flag is set, cap at B regardless of score
    if system_stability.get("instability_flag", False) and grade == "A":
        grade = "B"
        verdict = "SHADOW_EXTEND"
        reason += " [CAPPED: system instability detected — resolve conflicts first]"

    return {
        "score": overall,
        "grade": grade,
        "verdict": verdict,
        "reason": reason,
        "weights_used": {
            "time_robustness": WEIGHT_TIME_ROBUSTNESS,
            "execution_value": WEIGHT_EXECUTION_VALUE,
            "system_stability": WEIGHT_SYSTEM_STABILITY,
        },
        "subsystems_available": available,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def compute_confidence_score(
    *,
    walk_forward_path: str = "analysis/reports/walk_forward.json",
    shadow_path: str = "analysis/reports/shadow_execution.json",
    interactions_path: str = "analysis/reports/rule_interactions.json",
) -> dict[str, Any]:
    """
    Compute unified Strategy Confidence Score from all validation subsystems.

    Args:
        walk_forward_path: Path to walk-forward results JSON
        shadow_path: Path to shadow execution results JSON
        interactions_path: Path to rule interactions results JSON

    Returns:
        {
            "metadata": {...},
            "time_robustness": {"score": 0-100, ...},
            "execution_value": {"score": 0-100, ...},
            "system_stability": {"score": 0-100, ...},
            "overall_confidence": {"score": 0-100, "grade": "A/B/C/F", "verdict": "..."},
        }
    """
    # Load inputs
    wf_data = _load_json(walk_forward_path)
    shadow_data = _load_json(shadow_path)
    interact_data = _load_json(interactions_path)

    # Compute sub-scores
    time_robustness = _compute_time_robustness(wf_data)
    execution_value = _compute_execution_value(shadow_data)
    system_stability = _compute_system_stability(interact_data)

    # Compute overall
    overall = _compute_overall(time_robustness, execution_value, system_stability)

    output = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "inputs": {
                "walk_forward": walk_forward_path,
                "shadow_execution": shadow_path,
                "rule_interactions": interactions_path,
            },
            "subsystems_available": overall.get("subsystems_available", 0),
        },
        "time_robustness": time_robustness,
        "execution_value": execution_value,
        "system_stability": system_stability,
        "overall_confidence": overall,
    }

    logger.info(
        "[CONFIDENCE] Score: %d/100 (Grade %s) — TR=%d EV=%d SS=%d → %s",
        overall["score"], overall["grade"],
        time_robustness["score"], execution_value["score"], system_stability["score"],
        overall["verdict"],
    )

    return output


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT & DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def export_results(results: dict[str, Any], path: str = "analysis/reports/confidence_score.json") -> str:
    """Export confidence score results to JSON."""
    filepath = Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info("[CONFIDENCE] Exported to %s", filepath)
    return str(filepath)


def print_results(results: dict[str, Any]) -> None:
    """Print human-readable confidence score dashboard."""
    tr = results.get("time_robustness", {})
    ev = results.get("execution_value", {})
    ss = results.get("system_stability", {})
    overall = results.get("overall_confidence", {})

    score = overall.get("score", 0)
    grade = overall.get("grade", "?")
    verdict = overall.get("verdict", "?")

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  STRATEGY CONFIDENCE SCORE")
    print("═══════════════════════════════════════════════════════════════")
    print()

    # Overall score display
    bar = "█" * (score // 5) + "░" * (20 - score // 5)
    print(f"  OVERALL: {score}/100  Grade: {grade}  [{bar}]")
    print(f"  Verdict: {verdict}")
    print(f"  {overall.get('reason', '')}")
    print()

    # Sub-scores
    print("─── DIMENSION SCORES ───────────────────────────────────────────")
    _print_dimension("Time Robustness", tr, WEIGHT_TIME_ROBUSTNESS)
    _print_dimension("Execution Value", ev, WEIGHT_EXECUTION_VALUE)
    _print_dimension("System Stability", ss, WEIGHT_SYSTEM_STABILITY)
    print()

    # Key metrics
    print("─── KEY METRICS ────────────────────────────────────────────────")
    if tr.get("available"):
        print(f"  Edge decay:        {'⚠ DETECTED' if tr.get('edge_decay_detected') else '✓ None'}")
        print(f"  Patterns assessed: {tr.get('patterns_assessed', 0)}")
    if ev.get("available"):
        print(f"  PnL delta:         {ev.get('pnl_delta', 0):+.2f}")
        print(f"  Avoided losses:    {ev.get('avoided_losses', 0)}")
        print(f"  Missed winners:    {ev.get('missed_opportunities', 0)}")
    if ss.get("available"):
        print(f"  Conflicts:         {ss.get('conflict_count', 0)}")
        print(f"  Instability:       {'⚠ YES' if ss.get('instability_flag') else '✓ No'}")
    print()
    print("═══════════════════════════════════════════════════════════════")


def _print_dimension(name: str, data: dict[str, Any], weight: float) -> None:
    """Print a single dimension score line."""
    score = data.get("score", 0)
    available = data.get("available", False)
    bar = "█" * (score // 10) + "░" * (10 - score // 10)
    status = "" if available else " [NO DATA]"
    print(f"  {name:<22} {score:>3}/100 {bar} (weight: {weight:.0%}){status}")

    # Show components if available
    components = data.get("components", {})
    if components:
        parts = [f"{k}={v:.0f}" for k, v in components.items()]
        print(f"  {'':22} └─ {', '.join(parts)}")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    wf_path = sys.argv[1] if len(sys.argv) > 1 else "analysis/reports/walk_forward.json"
    shadow_path = sys.argv[2] if len(sys.argv) > 2 else "analysis/reports/shadow_execution.json"
    interact_path = sys.argv[3] if len(sys.argv) > 3 else "analysis/reports/rule_interactions.json"
    output_path = sys.argv[4] if len(sys.argv) > 4 else "analysis/reports/confidence_score.json"

    results = compute_confidence_score(
        walk_forward_path=wf_path,
        shadow_path=shadow_path,
        interactions_path=interact_path,
    )

    print_results(results)
    export_results(results, output_path)
    print(f"  Report saved to: {output_path}")
