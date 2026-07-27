"""
Edge Optimisation Recommendation Engine — Final decision layer for strategy improvement.

Translates validated system outputs into concrete, ranked, actionable changes
that improve profitability and/or stability.

This module ONLY synthesises existing outputs. It does NOT:
    - Run backtests or simulations
    - Generate new rules
    - Simulate regimes
    - Evaluate live data

Usage:
    from analysis.edge_optimiser import generate_recommendations

    result = generate_recommendations()
    print(result["priority_stack"])
    print(result["recommended_actions"])
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# INPUT LOADING
# ═══════════════════════════════════════════════════════════════════════════════

_DEFAULT_PATHS = {
    "walk_forward": "analysis/reports/walk_forward.json",
    "shadow": "analysis/reports/shadow_execution.json",
    "interactions": "analysis/reports/rule_interactions.json",
    "compression": "analysis/reports/rule_compression.json",
    "compression_validation": "analysis/reports/compression_validation.json",
    "stress_test": "analysis/reports/regime_stress_test.json",
    "confidence": "analysis/reports/confidence_score.json",
    "drift": "analysis/reports/drift_monitor.json",
}


def _load(path: str) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_all(paths: dict[str, str] | None = None) -> dict[str, Any | None]:
    """Load all system outputs."""
    p = paths or _DEFAULT_PATHS
    return {name: _load(path) for name, path in p.items()}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. PROFIT LEAK DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def _find_profit_leaks(data: dict[str, Any | None]) -> list[dict[str, Any]]:
    """
    Find where PnL is being lost:
        - Patterns with negative expectancy
        - Regimes with degradation
        - Rule conflicts causing missed winners
        - Over-filtering from compression
    """
    leaks: list[dict[str, Any]] = []

    # From stress test: regimes with edge decay
    stress = data.get("stress_test")
    if stress:
        for regime in stress.get("regime_results", []):
            decay = regime.get("edge_decay", 0)
            if decay > 25:
                leaks.append({
                    "source": f"regime_decay:{regime['regime_type']}",
                    "impact": f"{decay:.1f}% edge decay under {regime['regime_type']} conditions",
                    "severity": min(100, int(decay)),
                    "category": "regime_vulnerability",
                    "detail": {
                        "regime": regime["regime_type"],
                        "pnl_change": regime.get("pnl_change", 0),
                        "stability": regime.get("stability_score", 0),
                    },
                })

    # From shadow execution: missed winners (rules blocking profitable trades)
    shadow = data.get("shadow")
    if shadow:
        divergence = shadow.get("divergence_metrics", {})
        missed = divergence.get("missed_opportunities", 0)
        if missed > 0:
            leaks.append({
                "source": "rule_over_filtering",
                "impact": f"{missed} profitable trades blocked by rules",
                "severity": min(80, missed * 5),
                "category": "over_filtering",
                "detail": {"missed_winners": missed, "divergence_rate": divergence.get("divergence_rate", 0)},
            })

    # From rule interactions: conflicts causing instability
    interactions = data.get("interactions")
    if interactions:
        conflicts = interactions.get("conflicts", [])
        high_sev = [c for c in conflicts if c.get("severity", 0) >= 70]
        if high_sev:
            leaks.append({
                "source": "rule_conflicts",
                "impact": f"{len(high_sev)} high-severity rule conflicts creating decision ambiguity",
                "severity": min(90, len(high_sev) * 15),
                "category": "rule_conflict",
                "detail": {"conflict_count": len(high_sev), "targets": list(set(c.get("target", "") for c in high_sev))},
            })

    # From compression validation: edge lost during compression
    comp_val = data.get("compression_validation")
    if comp_val:
        perf = comp_val.get("performance_delta", {})
        pnl_change = perf.get("pnl_change", 0)
        if pnl_change < -10:
            leaks.append({
                "source": "compression_edge_loss",
                "impact": f"Compression lost {abs(pnl_change):.2f} PnL vs pre-compression",
                "severity": min(70, int(abs(pnl_change) / 10)),
                "category": "over_compression",
                "detail": {"pnl_lost": pnl_change},
            })

    # From walk-forward: patterns with degradation across windows
    wf = data.get("walk_forward")
    if wf:
        for pat in wf.get("pattern_stability", []):
            if pat.get("stability_score", 100) < 40:
                leaks.append({
                    "source": f"unstable_pattern:{pat['pattern']}",
                    "impact": f"{pat['pattern']} has low stability ({pat['stability_score']}/100) — possible overfit",
                    "severity": 100 - pat["stability_score"],
                    "category": "pattern_instability",
                    "detail": pat,
                })

    leaks.sort(key=lambda l: l["severity"], reverse=True)
    return leaks


# ═══════════════════════════════════════════════════════════════════════════════
# 2. EDGE OPPORTUNITY ZONES
# ═══════════════════════════════════════════════════════════════════════════════

def _find_opportunity_zones(data: dict[str, Any | None]) -> list[dict[str, Any]]:
    """
    Find where performance is strongest but underutilised:
        - High expectancy patterns with low volume
        - Regimes with consistent performance
        - Rules that are overly restrictive
    """
    zones: list[dict[str, Any]] = []

    # From stress test: best-performing regimes
    stress = data.get("stress_test")
    if stress:
        best_regime = stress.get("best_case_regime", "")
        for regime in stress.get("regime_results", []):
            if regime.get("stability_score", 0) >= 90:
                zones.append({
                    "area": f"strong_regime:{regime['regime_type']}",
                    "reason": f"System maintains {regime['stability_score']}/100 stability under {regime['regime_type']}",
                    "expected_gain": f"Increase allocation during {regime['regime_type']} conditions",
                    "confidence": regime["stability_score"],
                })

    # From walk-forward: highly stable patterns
    wf = data.get("walk_forward")
    if wf:
        for pat in wf.get("pattern_stability", []):
            if pat.get("stability_score", 0) >= 80 and pat.get("all_windows_profitable", False):
                zones.append({
                    "area": f"stable_pattern:{pat['pattern']}",
                    "reason": f"{pat['pattern']} profitable in all windows (stability={pat['stability_score']})",
                    "expected_gain": "Loosen entry filters to capture more trades from this pattern",
                    "confidence": pat["stability_score"],
                })

    # From shadow: rules with high avoided-loss and zero missed-winners
    shadow = data.get("shadow")
    if shadow:
        for rule in shadow.get("rule_impact_analysis", shadow.get("per_rule", [])):
            avoided = rule.get("losses_prevented", rule.get("avoided_losses", 0))
            missed = rule.get("winners_removed", rule.get("missed_opportunities", 0))
            if avoided > 5 and missed == 0:
                zones.append({
                    "area": f"perfect_filter:{rule.get('rule_id', '?')}",
                    "reason": f"Rule blocks {avoided} losses with 0 missed winners — ideal filter",
                    "expected_gain": "Confirm and strengthen this rule; consider expanding its scope",
                    "confidence": 85,
                })

    # From confidence: high time-robustness sub-score
    conf = data.get("confidence")
    if conf:
        tr = conf.get("time_robustness", {})
        if tr.get("score", 0) >= 80:
            zones.append({
                "area": "time_robust_edge",
                "reason": f"Time robustness score {tr['score']}/100 — edge is stable over time",
                "expected_gain": "System can increase trade frequency without time-decay risk",
                "confidence": tr["score"],
            })

    zones.sort(key=lambda z: z.get("confidence", 0), reverse=True)
    return zones


# ═══════════════════════════════════════════════════════════════════════════════
# 3. STRUCTURAL IMPROVEMENT ACTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _generate_actions(
    leaks: list[dict[str, Any]],
    zones: list[dict[str, Any]],
    data: dict[str, Any | None],
) -> list[dict[str, Any]]:
    """
    Convert insights into concrete, actionable recommendations.

    Each action specifies: what to change, where, expected impact, and risk.
    """
    actions: list[dict[str, Any]] = []

    # Actions from profit leaks
    for leak in leaks:
        cat = leak.get("category", "")
        severity = leak.get("severity", 0)

        if cat == "regime_vulnerability":
            detail = leak.get("detail", {})
            regime = detail.get("regime", "unknown")
            actions.append({
                "action": f"Add regime-aware position sizing for {regime} conditions",
                "target": regime,
                "type": "RISK_REDUCTION",
                "expected_pnl_impact": round(abs(detail.get("pnl_change", 0)) * 0.5, 2),
                "risk_change": "reduced — lower exposure in vulnerable regime",
                "confidence": min(80, 100 - severity),
                "source": "regime_stress_test",
                "priority": severity,
            })

        elif cat == "over_filtering" and severity > 20:
            detail = leak.get("detail", {})
            actions.append({
                "action": "Review rule specificity — consider relaxing pattern matching in profitable contexts",
                "target": "rule_system",
                "type": "EDGE_EXPANSION",
                "expected_pnl_impact": round(detail.get("missed_winners", 0) * 25, 2),
                "risk_change": "slightly increased — allowing more trades",
                "confidence": 55,
                "source": "shadow_execution",
                "priority": severity,
            })

        elif cat == "rule_conflict":
            detail = leak.get("detail", {})
            targets = detail.get("targets", [])
            actions.append({
                "action": f"Resolve rule conflicts on {', '.join(targets)} — consolidate opposing rules",
                "target": ", ".join(targets),
                "type": "STABILITY_FIX",
                "expected_pnl_impact": 0,
                "risk_change": "reduced — elimination of decision ambiguity",
                "confidence": 90,
                "source": "rule_interactions",
                "priority": severity,
            })

        elif cat == "pattern_instability":
            detail = leak.get("detail", {})
            pattern = detail.get("pattern", "?")
            actions.append({
                "action": f"Add regime gate to {pattern} — only trade in validated regime contexts",
                "target": pattern,
                "type": "RISK_REDUCTION",
                "expected_pnl_impact": round(abs(detail.get("worst_window_pnl", 0)) * 0.3, 2),
                "risk_change": "reduced — prevents trading in unfavourable conditions",
                "confidence": 65,
                "source": "walk_forward",
                "priority": severity,
            })

    # Actions from opportunity zones
    for zone in zones:
        area = zone.get("area", "")
        conf = zone.get("confidence", 0)

        if "stable_pattern" in area:
            pattern = area.split(":")[1] if ":" in area else "?"
            actions.append({
                "action": f"Reduce confluence threshold for {pattern} by 10% — pattern proven stable",
                "target": pattern,
                "type": "EDGE_EXPANSION",
                "expected_pnl_impact": round(conf * 0.3, 2),
                "risk_change": "marginal increase — justified by high stability score",
                "confidence": conf,
                "source": "walk_forward",
                "priority": conf,
            })

        elif "perfect_filter" in area:
            actions.append({
                "action": "Expand scope of high-precision blocking rule to related patterns",
                "target": area.split(":")[1] if ":" in area else "rule",
                "type": "EDGE_EXPANSION",
                "expected_pnl_impact": round(conf * 0.2, 2),
                "risk_change": "minimal — rule already proven with zero false positives",
                "confidence": conf,
                "source": "shadow_execution",
                "priority": conf,
            })

        elif "time_robust" in area:
            actions.append({
                "action": "Increase maximum concurrent position count or reduce cooldown period",
                "target": "position_management",
                "type": "VOLUME_INCREASE",
                "expected_pnl_impact": round(conf * 0.5, 2),
                "risk_change": "moderate increase — compensated by time-stable edge",
                "confidence": min(70, conf),
                "source": "confidence_score",
                "priority": conf * 0.8,
            })

    # Sort by priority
    actions.sort(key=lambda a: a.get("priority", 0), reverse=True)

    # Assign confidence-adjusted rank
    for i, action in enumerate(actions, 1):
        action["rank"] = i

    return actions


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SYSTEM SUMMARY & PRIORITY STACK
# ═══════════════════════════════════════════════════════════════════════════════

def _build_summary(
    leaks: list[dict[str, Any]],
    zones: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    data: dict[str, Any | None],
) -> dict[str, Any]:
    """Build system summary with current state and fastest improvement path."""
    conf = data.get("confidence")
    drift = data.get("drift")

    # Current edge state
    if conf:
        overall = conf.get("overall_confidence", {})
        grade = overall.get("grade", "?")
        score = overall.get("score", 0)
        edge_state = f"Grade {grade} ({score}/100) — {overall.get('verdict', 'unknown')}"
    else:
        edge_state = "Unknown — confidence score not available"

    # Main constraint
    if leaks:
        main_constraint = leaks[0]["source"]
    elif drift and drift.get("overall_drift_score", 0) > 25:
        main_constraint = f"live_drift (score={drift['overall_drift_score']})"
    else:
        main_constraint = "none_critical"

    # Fastest path
    if actions:
        top = actions[0]
        fastest = f"{top['action']} (expected +{top['expected_pnl_impact']:.2f}, confidence={top['confidence']})"
    else:
        fastest = "System is well-optimised — no immediate actions required."

    return {
        "current_edge_state": edge_state,
        "main_constraint": main_constraint,
        "fastest_path_to_improvement": fastest,
        "total_leaks_found": len(leaks),
        "total_opportunities": len(zones),
        "total_actions": len(actions),
        "system_health": "healthy" if not leaks else "leaking" if leaks[0]["severity"] < 50 else "critical",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def generate_recommendations(
    *,
    paths: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Generate ranked edge optimisation recommendations from all system outputs.

    Returns:
        {
            "profit_leaks": [...],
            "opportunity_zones": [...],
            "recommended_actions": [...],
            "priority_stack": [...],
            "system_summary": {...},
        }
    """
    data = _load_all(paths)

    # Run analyses
    leaks = _find_profit_leaks(data)
    zones = _find_opportunity_zones(data)
    actions = _generate_actions(leaks, zones, data)
    summary = _build_summary(leaks, zones, actions, data)

    # Priority stack: top 5 actions by rank
    priority_stack = [a["action"] for a in actions[:5]]

    output = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "inputs_loaded": [k for k, v in data.items() if v is not None],
            "inputs_missing": [k for k, v in data.items() if v is None],
        },
        "profit_leaks": leaks,
        "opportunity_zones": zones,
        "recommended_actions": actions,
        "priority_stack": priority_stack,
        "system_summary": summary,
    }

    logger.info(
        "[OPTIMISE] Complete — %d leaks, %d opportunities, %d actions, health=%s",
        len(leaks), len(zones), len(actions), summary["system_health"],
    )

    return output


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT & DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def export_results(results: dict[str, Any], path: str = "analysis/reports/edge_optimiser.json") -> str:
    filepath = Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    return str(filepath)


def print_results(results: dict[str, Any]) -> None:
    leaks = results.get("profit_leaks", [])
    zones = results.get("opportunity_zones", [])
    actions = results.get("recommended_actions", [])
    stack = results.get("priority_stack", [])
    summary = results.get("system_summary", {})

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  EDGE OPTIMISATION RECOMMENDATIONS")
    print("═══════════════════════════════════════════════════════════════")
    print(f"  State:  {summary.get('current_edge_state', '?')}")
    print(f"  Health: {summary.get('system_health', '?')}")
    print(f"  Constraint: {summary.get('main_constraint', '?')}")
    print()

    # Priority stack
    if stack:
        print("─── PRIORITY STACK (do these first) ────────────────────────────")
        for i, action in enumerate(stack, 1):
            print(f"  {i}. {action}")
        print()

    # Profit leaks
    if leaks:
        print(f"─── PROFIT LEAKS ({len(leaks)}) ───────────────────────────────────")
        for leak in leaks[:5]:
            bar = "●" * (leak["severity"] // 20) + "○" * (5 - leak["severity"] // 20)
            print(f"  {bar} [{leak['severity']:>3}] {leak['source']}")
            print(f"        {leak['impact']}")
        print()

    # Opportunity zones
    if zones:
        print(f"─── OPPORTUNITY ZONES ({len(zones)}) ──────────────────────────────")
        for zone in zones[:5]:
            print(f"  ✦ {zone['area']} (conf={zone['confidence']})")
            print(f"    {zone['reason']}")
            print(f"    → {zone['expected_gain']}")
        print()

    # Recommended actions
    if actions:
        print(f"─── ACTIONS ({len(actions)}) ──────────────────────────────────────")
        for a in actions[:7]:
            print(f"  #{a['rank']} [{a['type']}] conf={a['confidence']}")
            print(f"     {a['action']}")
            print(f"     Target: {a['target']} | PnL: +{a['expected_pnl_impact']:.2f} | Risk: {a['risk_change'][:40]}")
            print()

    # Fastest path
    print("─── FASTEST PATH TO IMPROVEMENT ────────────────────────────────")
    print(f"  → {summary.get('fastest_path_to_improvement', 'None identified')}")
    print()
    print("═══════════════════════════════════════════════════════════════")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    output_path = sys.argv[1] if len(sys.argv) > 1 else "analysis/reports/edge_optimiser.json"

    results = generate_recommendations()

    print_results(results)
    export_results(results, output_path)
    print(f"  Report saved to: {output_path}")
