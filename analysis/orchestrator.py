"""
Trading Decision Orchestrator — Final unified decision from all system outputs.

Routes precomputed module outputs through a strict priority filter chain
and produces ONE trading decision: TRADE / NO_TRADE / SHADOW_MODE.

This module does NOT:
    - Run computation or analysis
    - Modify rules or strategy
    - Simulate or backtest
    - Re-evaluate data

It ONLY synthesises existing outputs into a single actionable decision.

Usage:
    from analysis.orchestrator import make_decision

    decision = make_decision()
    print(decision["decision"])  # TRADE / NO_TRADE / SHADOW_MODE
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# INPUT PATHS
# ═══════════════════════════════════════════════════════════════════════════════

_DEFAULT_PATHS = {
    "drift": "analysis/reports/drift_monitor.json",
    "confidence": "analysis/reports/confidence_score.json",
    "walk_forward": "analysis/reports/walk_forward.json",
    "shadow": "analysis/reports/shadow_execution.json",
    "compression_validation": "analysis/reports/compression_validation.json",
    "stress_test": "analysis/reports/regime_stress_test.json",
    "experiment": "analysis/reports/experiment_result.json",
    "optimiser": "analysis/reports/edge_optimiser.json",
    "compression": "analysis/reports/rule_compression.json",
}


def _load(path: str) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════════════════════
# DECISION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def make_decision(*, paths: dict[str, str] | None = None) -> dict[str, Any]:
    """
    Produce a single unified trading decision by routing all system outputs
    through a strict priority filter chain.

    Priority order:
        1. Live safety (drift monitor)
        2. System confidence
        3. Strategy validation (walk-forward + shadow)
        4. Edge validity (compression validation)
        5. Regime robustness (stress test)
        6. Experiment results (override check)

    Returns:
        Strict JSON decision object.
    """
    p = paths or _DEFAULT_PATHS
    data = {name: _load(path) for name, path in p.items()}

    # Accumulate decision state
    decision = "TRADE"
    confidence = 100
    risk_state = "STABLE"
    reasoning: list[str] = []
    blocking: list[str] = []
    supporting: list[str] = []

    # ─── FILTER 1: LIVE SAFETY ────────────────────────────────────────
    drift = data.get("drift")
    if drift:
        drift_risk = drift.get("risk_state", "STABLE")
        drift_mode = drift.get("system_action", {}).get("mode", "LIVE")
        drift_score = drift.get("overall_drift_score", 0)

        if drift_risk == "BROKEN_REGIME" or drift_mode == "DISABLED":
            decision = "NO_TRADE"
            risk_state = "BROKEN"
            confidence = 0
            blocking.append("Live drift monitor: BROKEN_REGIME — system invalid")
            reasoning.append("Market regime has broken beyond validated envelope")
        elif drift_risk == "DEGRADED" or drift_mode == "SHADOW":
            decision = "SHADOW_MODE"
            risk_state = "DEGRADED"
            confidence = min(confidence, 30)
            blocking.append("Live drift: DEGRADED — shadow mode only")
            reasoning.append("Significant market drift detected — live execution unsafe")
        elif drift_risk == "WATCH" or drift_mode == "WATCH":
            risk_state = "WATCH"
            confidence = min(confidence, 70)
            reasoning.append(f"Early drift detected (score={drift_score}) — reduced confidence")
        else:
            risk_state = "STABLE"
            supporting.append(f"Live drift stable (score={drift_score})")
    else:
        reasoning.append("Drift monitor unavailable — proceeding with caution")
        confidence = min(confidence, 80)

    # Early exit if blocked
    if decision == "NO_TRADE":
        return _build_output(decision, confidence, risk_state, reasoning, blocking, supporting, data)

    # ─── FILTER 2: SYSTEM CONFIDENCE ─────────────────────────────────
    conf_data = data.get("confidence")
    if conf_data:
        overall = conf_data.get("overall_confidence", {})
        conf_score = overall.get("score", 0)

        if conf_score < 50:
            decision = "NO_TRADE"
            blocking.append(f"Confidence score {conf_score}/100 < 50 threshold")
            reasoning.append(f"System confidence too low ({conf_score}/100) for live trading")
            confidence = conf_score
        elif conf_score < 75:
            if decision == "TRADE":
                decision = "SHADOW_MODE"
            confidence = min(confidence, conf_score)
            reasoning.append(f"Confidence {conf_score}/100 — eligible for shadow only")
        else:
            confidence = min(confidence, conf_score)
            supporting.append(f"System confidence {conf_score}/100 — healthy")
    else:
        confidence = min(confidence, 60)
        reasoning.append("Confidence score unavailable — defaulting conservative")

    if decision == "NO_TRADE":
        return _build_output(decision, confidence, risk_state, reasoning, blocking, supporting, data)

    # ─── FILTER 3: STRATEGY VALIDATION ───────────────────────────────
    wf = data.get("walk_forward")
    shadow = data.get("shadow")

    wf_stable = False
    shadow_positive = False

    if wf:
        summary = wf.get("overall_summary", {})
        wf_stable = not summary.get("overall_edge_decay", True)
        if wf_stable:
            supporting.append("Walk-forward: edge stable across time windows")
        else:
            reasoning.append("Walk-forward: edge decay detected")

    if shadow:
        shadow_pnl = shadow.get("shadow_results", {}).get("total_pnl", 0)
        baseline_pnl = shadow.get("baseline_results", {}).get("total_pnl", 0)
        shadow_positive = shadow_pnl >= baseline_pnl
        if shadow_positive:
            supporting.append(f"Shadow engine outperforms baseline (+{shadow_pnl - baseline_pnl:.0f})")
        else:
            reasoning.append("Shadow engine underperforms baseline")

    if not wf_stable and not shadow_positive:
        decision = "NO_TRADE"
        blocking.append("Both walk-forward and shadow show instability")
        reasoning.append("Strategy validation failed on both dimensions")
    elif not wf_stable or not shadow_positive:
        if decision == "TRADE":
            decision = "SHADOW_MODE"
        reasoning.append("Mixed validation signals — shadow mode appropriate")

    if decision == "NO_TRADE":
        return _build_output(decision, confidence, risk_state, reasoning, blocking, supporting, data)

    # ─── FILTER 4: EDGE VALIDITY ─────────────────────────────────────
    comp_val = data.get("compression_validation")
    if comp_val:
        summary = comp_val.get("summary", {})
        if not summary.get("compression_valid", True):
            decision = "NO_TRADE"
            blocking.append("Compression validation: edge loss detected")
            reasoning.append("Compressed rules lost meaningful edge — unsafe to trade")
        else:
            supporting.append("Compression validation: edge preserved")

    if decision == "NO_TRADE":
        return _build_output(decision, confidence, risk_state, reasoning, blocking, supporting, data)

    # ─── FILTER 5: REGIME ROBUSTNESS ─────────────────────────────────
    stress = data.get("stress_test")
    if stress:
        robustness = stress.get("overall_robustness_score", 0)
        worst = stress.get("regime_results", [])
        worst_score = min((r.get("stability_score", 100) for r in worst), default=100)

        if worst_score < 50:
            if decision == "TRADE":
                decision = "SHADOW_MODE"
            reasoning.append(f"Worst regime stability {worst_score}/100 < 50 — shadow mode")
        elif robustness >= 70:
            supporting.append(f"Regime robustness {robustness}/100 — all regimes viable")
            confidence = min(confidence, robustness)
        else:
            confidence = min(confidence, robustness)

    # ─── FILTER 6: EXPERIMENT OVERRIDE ───────────────────────────────
    experiment = data.get("experiment")
    if experiment:
        exp_decision = experiment.get("decision", "")
        if exp_decision == "REJECT":
            reasoning.append("Latest experiment REJECTED — change not applied")
        elif exp_decision == "ACCEPT":
            supporting.append("Latest experiment ACCEPTED — improvement validated")

    return _build_output(decision, confidence, risk_state, reasoning, blocking, supporting, data)


# ═══════════════════════════════════════════════════════════════════════════════
# OUTPUT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def _build_output(
    decision: str,
    confidence: int,
    risk_state: str,
    reasoning: list[str],
    blocking: list[str],
    supporting: list[str],
    data: dict[str, Any | None],
) -> dict[str, Any]:
    """Assemble the final strict-format decision object."""

    # Position action
    if decision == "TRADE":
        if risk_state == "WATCH":
            position_action = "REDUCE_SIZE"
        else:
            position_action = "ENTER"
    elif decision == "SHADOW_MODE":
        position_action = "SKIP"
    else:
        position_action = "SKIP"

    # Active strategy (from compression)
    comp = data.get("compression")
    if comp:
        rules = comp.get("final_rule_set", [])
        targets = list(set(r.get("target", "") for r in rules))
        active_strategy = f"Compressed rules ({len(rules)}) targeting: {', '.join(targets)}"
    else:
        active_strategy = "Default strategy (no compressed rules loaded)"

    # Edge summary
    optimiser = data.get("optimiser")
    strength = ""
    weakness = ""
    limitation = ""

    if optimiser:
        summary = optimiser.get("system_summary", {})
        zones = optimiser.get("opportunity_zones", [])
        leaks = optimiser.get("profit_leaks", [])

        if zones:
            strength = zones[0].get("reason", "")[:80]
        if leaks:
            weakness = leaks[0].get("impact", "")[:80]
        limitation = summary.get("main_constraint", "")

    confidence = min(100, max(0, confidence))

    return {
        "decision": decision,
        "confidence": confidence,
        "risk_state": risk_state,
        "active_strategy": active_strategy,
        "reasoning": reasoning[:5],
        "blocking_factors": blocking,
        "supporting_factors": supporting,
        "recommended_position_action": position_action,
        "edge_summary": {
            "strength": strength,
            "weakness": weakness,
            "current_limitation": limitation,
        },
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT & DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def export_decision(result: dict[str, Any], path: str = "analysis/reports/trading_decision.json") -> str:
    filepath = Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    return str(filepath)


def print_decision(result: dict[str, Any]) -> None:
    decision = result.get("decision", "?")
    confidence = result.get("confidence", 0)
    risk = result.get("risk_state", "?")
    action = result.get("recommended_position_action", "?")

    icons = {"TRADE": "✓", "NO_TRADE": "✗", "SHADOW_MODE": "◐"}
    icon = icons.get(decision, "?")

    print()
    print("═══════════════════════════════════════════════════════════════")
    print(f"  {icon} DECISION: {decision}")
    print("═══════════════════════════════════════════════════════════════")
    print(f"  Confidence:  {confidence}/100")
    print(f"  Risk State:  {risk}")
    print(f"  Action:      {action}")
    print(f"  Strategy:    {result.get('active_strategy', '?')[:60]}")
    print()

    # Reasoning
    reasoning = result.get("reasoning", [])
    if reasoning:
        print("─── REASONING ──────────────────────────────────────────────────")
        for r in reasoning:
            print(f"  • {r}")
        print()

    # Blocking
    blocking = result.get("blocking_factors", [])
    if blocking:
        print("─── BLOCKING ───────────────────────────────────────────────────")
        for b in blocking:
            print(f"  ✗ {b}")
        print()

    # Supporting
    supporting = result.get("supporting_factors", [])
    if supporting:
        print("─── SUPPORTING ─────────────────────────────────────────────────")
        for s in supporting:
            print(f"  ✓ {s}")
        print()

    # Edge summary
    edge = result.get("edge_summary", {})
    if any(edge.values()):
        print("─── EDGE ───────────────────────────────────────────────────────")
        if edge.get("strength"):
            print(f"  Strength:   {edge['strength']}")
        if edge.get("weakness"):
            print(f"  Weakness:   {edge['weakness']}")
        if edge.get("current_limitation"):
            print(f"  Limitation: {edge['current_limitation']}")
        print()

    print("═══════════════════════════════════════════════════════════════")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    output = sys.argv[1] if len(sys.argv) > 1 else "analysis/reports/trading_decision.json"

    result = make_decision()
    print_decision(result)
    export_decision(result, output)
    print(f"  Decision saved to: {output}")
