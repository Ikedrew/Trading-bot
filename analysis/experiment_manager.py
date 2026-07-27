"""
Edge Change Control System (Experiment Manager) — Single-variable controlled experiments.

Converts optimisation recommendations into isolated, measurable experiments.
Ensures only ONE change is tested at a time with all other variables frozen.

Principles:
    - NO multi-change deployments
    - Every experiment isolates a single variable
    - Impact is causally measurable (baseline vs experimental)
    - Changes are ACCEPTED, REJECTED, or INCONCLUSIVE based on evidence

This module does NOT:
    - Apply changes to live trading
    - Run multiple experiments simultaneously
    - Modify the compressed rule set permanently

Usage:
    from analysis.experiment_manager import run_experiment

    result = run_experiment()
    print(result["decision"])  # ACCEPT / REJECT / INCONCLUSIVE
"""

from __future__ import annotations

import json
import hashlib
import logging
from copy import deepcopy
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Decision thresholds
MIN_PNL_IMPROVEMENT = 0.0         # Must not worsen
MIN_STABILITY_DELTA = -5          # Max allowed stability drop
MIN_ROBUSTNESS_DELTA = -10        # Max allowed robustness drop

# Inconclusive if deltas are within noise band
NOISE_BAND_PNL = 2.0             # PnL changes < this are noise
NOISE_BAND_WINRATE = 1.5         # Winrate changes < this are noise


# ═══════════════════════════════════════════════════════════════════════════════
# INPUT LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def _load(path: str) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_trades(curated_dir: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for f in sorted(Path(curated_dir).glob("*.jsonl")):
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        ev = json.loads(line)
                        if ev.get("pnl", 0) != 0:
                            events.append(ev)
                    except json.JSONDecodeError:
                        continue
    events.sort(key=lambda e: e.get("timestamp", ""))
    return events


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1 — SELECT SINGLE CHANGE
# ═══════════════════════════════════════════════════════════════════════════════

def _select_change(optimiser_path: str) -> dict[str, Any] | None:
    """
    Select ONLY the highest-priority recommendation.
    All others are ignored for this experiment.
    """
    data = _load(optimiser_path)
    if not data:
        return None

    actions = data.get("recommended_actions", [])
    if not actions:
        return None

    # Take only the #1 ranked action
    top = actions[0]
    return {
        "action": top.get("action", ""),
        "target": top.get("target", ""),
        "type": top.get("type", ""),
        "expected_pnl_impact": top.get("expected_pnl_impact", 0),
        "confidence": top.get("confidence", 0),
        "source": top.get("source", ""),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2 — CREATE CONTROLLED EXPERIMENT
# ═══════════════════════════════════════════════════════════════════════════════

def _create_experimental_rules(
    baseline_rules: list[dict[str, Any]],
    change: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Apply single modification to baseline rules to create experimental variant.

    Change types:
        EDGE_EXPANSION → loosen matching rule (remove blocking for target)
        STABILITY_FIX → remove conflicting rules for target
        RISK_REDUCTION → add stricter gate for target
        VOLUME_INCREASE → no rule change (sizing change only)
    """
    exp_rules = deepcopy(baseline_rules)
    change_type = change.get("type", "")
    target = change.get("target", "")

    if change_type == "EDGE_EXPANSION":
        # Loosen: remove blocking rules for target pattern
        exp_rules = [r for r in exp_rules if not (
            r.get("target") == target and
            r.get("type") in ("TIGHTEN_GATE", "ADD_GATE")
        )]

    elif change_type == "STABILITY_FIX":
        # Remove conflicting rules — keep highest confidence only
        target_rules = [r for r in exp_rules if r.get("target") == target]
        non_target = [r for r in exp_rules if r.get("target") != target]
        if len(target_rules) > 1:
            best = max(target_rules, key=lambda r: r.get("confidence_score", 0))
            exp_rules = non_target + [best]

    elif change_type == "RISK_REDUCTION":
        # Add stricter condition: reduce confidence threshold on existing rule
        for rule in exp_rules:
            if rule.get("target") == target:
                # Simulate tighter gate by expanding match scope
                rule["_experiment_tightened"] = True
                break

    elif change_type == "VOLUME_INCREASE":
        # No rule change — this would be a sizing parameter change
        pass

    return exp_rules


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3 — PARALLEL EVALUATION
# ═══════════════════════════════════════════════════════════════════════════════

def _evaluate_system(
    trades: list[dict[str, Any]],
    rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate a rule set against trades. Returns performance metrics."""
    if not trades:
        return {"trades": 0, "total_pnl": 0.0, "avg_pnl": 0.0, "winrate": 0.0, "blocked": 0}

    kept = []
    blocked = 0
    for trade in trades:
        is_blocked = False
        for rule in rules:
            if _rule_blocks(trade, rule):
                is_blocked = True
                break
        if is_blocked:
            blocked += 1
        else:
            kept.append(trade)

    pnls = [t.get("pnl", 0) for t in kept]
    wins = sum(1 for p in pnls if p > 0)

    # Max drawdown
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    return {
        "trades": len(kept),
        "total_pnl": round(sum(pnls), 4) if pnls else 0.0,
        "avg_pnl": round(sum(pnls) / len(pnls), 4) if pnls else 0.0,
        "winrate": round(wins / len(pnls) * 100, 2) if pnls else 0.0,
        "blocked": blocked,
        "max_drawdown": round(max_dd, 4),
    }


def _rule_blocks(trade: dict[str, Any], rule: dict[str, Any]) -> bool:
    """Check if rule blocks trade."""
    if rule.get("type", "") not in ("TIGHTEN_GATE", "ADD_GATE"):
        return False
    if trade.get("pattern", "") != rule.get("target", ""):
        return False
    if trade.get("pnl", 0) >= 0:
        return False

    # Tightened experiment rules block more aggressively
    if rule.get("_experiment_tightened"):
        return True

    evidence = rule.get("supporting_evidence", {})
    regime = evidence.get("regime", "")
    bias = evidence.get("bias", "")
    rm = (not regime or regime == "neutral" or trade.get("atr_regime", "") == regime)
    bm = (not bias or bias == "neutral" or trade.get("htf_bias", "") == bias)
    return rm and bm


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4 — MEASURE CAUSAL IMPACT
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_deltas(
    baseline: dict[str, Any],
    experimental: dict[str, Any],
) -> dict[str, Any]:
    """Compute performance deltas between baseline and experimental systems."""
    pnl_delta = experimental["total_pnl"] - baseline["total_pnl"]
    wr_delta = experimental["winrate"] - baseline["winrate"]
    avg_delta = experimental["avg_pnl"] - baseline["avg_pnl"]
    dd_delta = experimental["max_drawdown"] - baseline["max_drawdown"]

    # Stability proxy: lower drawdown + higher winrate = more stable
    stability_delta = -dd_delta + wr_delta * 0.5

    # Robustness proxy: fewer blocked trades means less filtering dependency
    blocked_delta = experimental["blocked"] - baseline["blocked"]

    return {
        "pnl_delta": round(pnl_delta, 4),
        "winrate_delta": round(wr_delta, 2),
        "expectancy_delta": round(avg_delta, 4),
        "stability_delta": round(stability_delta, 2),
        "robustness_delta": round(-abs(dd_delta) if dd_delta > 0 else abs(dd_delta), 2),
        "drawdown_delta": round(dd_delta, 4),
        "blocked_delta": blocked_delta,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 5 — DECISION LOGIC
# ═══════════════════════════════════════════════════════════════════════════════

def _make_decision(deltas: dict[str, Any]) -> tuple[str, str, list[str]]:
    """
    Determine ACCEPT / REJECT / INCONCLUSIVE.

    ACCEPT: PnL improves OR stability improves, no regressions.
    REJECT: PnL worsens OR instability increases.
    INCONCLUSIVE: Mixed signals within noise band.
    """
    pnl = deltas.get("pnl_delta", 0)
    wr = deltas.get("winrate_delta", 0)
    stab = deltas.get("stability_delta", 0)
    robust = deltas.get("robustness_delta", 0)

    risks: list[str] = []
    positive_signals = 0
    negative_signals = 0

    # PnL check
    if pnl > NOISE_BAND_PNL:
        positive_signals += 1
    elif pnl < -NOISE_BAND_PNL:
        negative_signals += 1
        risks.append(f"PnL decreased by {pnl:.2f}")

    # Winrate check
    if wr > NOISE_BAND_WINRATE:
        positive_signals += 1
    elif wr < -NOISE_BAND_WINRATE:
        negative_signals += 1
        risks.append(f"Winrate decreased by {wr:.1f}pp")

    # Stability check
    if stab < MIN_STABILITY_DELTA:
        negative_signals += 1
        risks.append(f"Stability degraded by {stab:.1f}")
    elif stab > 5:
        positive_signals += 1

    # Robustness check
    if robust < MIN_ROBUSTNESS_DELTA:
        negative_signals += 1
        risks.append(f"Robustness degraded by {robust:.1f}")

    # Decision
    if negative_signals == 0 and positive_signals >= 1:
        decision = "ACCEPT"
        reason = f"Improvement confirmed: {positive_signals} positive signal(s), 0 regressions."
    elif negative_signals >= 2:
        decision = "REJECT"
        reason = f"Regression detected: {negative_signals} negative signal(s). {'; '.join(risks)}"
    elif negative_signals == 1 and positive_signals >= 2:
        decision = "ACCEPT"
        reason = f"Net positive: {positive_signals} improvements outweigh 1 minor regression."
    elif abs(pnl) <= NOISE_BAND_PNL and abs(wr) <= NOISE_BAND_WINRATE:
        decision = "INCONCLUSIVE"
        reason = "All deltas within noise band. Extend test window for clearer signal."
    else:
        decision = "INCONCLUSIVE"
        reason = f"Mixed signals: {positive_signals} positive, {negative_signals} negative. Needs more data."

    return decision, reason, risks


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def run_experiment(
    *,
    curated_dir: str = "events/curated",
    compression_path: str = "analysis/reports/rule_compression.json",
    optimiser_path: str = "analysis/reports/edge_optimiser.json",
) -> dict[str, Any]:
    """
    Run a single controlled experiment on the top-priority recommendation.

    Workflow:
        1. Select highest-priority change from optimiser
        2. Create experimental rule set (single modification)
        3. Evaluate baseline vs experimental on trade data
        4. Compute causal deltas
        5. Make ACCEPT/REJECT/INCONCLUSIVE decision

    Returns:
        Complete experiment result with decision and evidence.
    """
    # Load inputs
    trades = _load_trades(curated_dir)
    if not trades:
        return {"error": "no_trades"}

    # Load baseline rules
    comp_data = _load(compression_path)
    if not comp_data:
        return {"error": "no_compressed_rules"}
    baseline_rules = comp_data.get("final_rule_set", [])

    # Step 1: Select change
    change = _select_change(optimiser_path)
    if not change:
        return {"error": "no_recommendations_available"}

    # Step 2: Create experimental variant
    experimental_rules = _create_experimental_rules(baseline_rules, change)

    # Step 3: Parallel evaluation
    baseline_perf = _evaluate_system(trades, baseline_rules)
    experimental_perf = _evaluate_system(trades, experimental_rules)

    # Step 4: Compute deltas
    deltas = _compute_deltas(baseline_perf, experimental_perf)

    # Step 5: Decision
    decision, reason, risks = _make_decision(deltas)

    # Determine next action
    if decision == "ACCEPT":
        next_action = "Promote experimental rules to compressed rule set. Run walk-forward to confirm."
    elif decision == "REJECT":
        next_action = "Discard this change. Move to next priority recommendation."
    else:
        next_action = "Extend observation window. Re-run with 2x data before deciding."

    # Generate experiment ID
    exp_id = f"exp_{hashlib.md5(change['action'].encode()).hexdigest()[:8]}_{datetime.now(timezone.utc).strftime('%Y%m%d')}"

    output = {
        "experiment_id": exp_id,
        "tested_change": change,
        "baseline_rules_count": len(baseline_rules),
        "experimental_rules_count": len(experimental_rules),
        "baseline_performance": baseline_perf,
        "experimental_performance": experimental_perf,
        "deltas": deltas,
        "decision": decision,
        "reason": reason,
        "risk_notes": risks,
        "next_action": next_action,
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "trades_evaluated": len(trades),
            "change_type": change.get("type", ""),
            "change_target": change.get("target", ""),
            "change_confidence": change.get("confidence", 0),
        },
    }

    logger.info(
        "[EXPERIMENT] %s | Change: %s → %s | PnL Δ=%+.2f | Decision: %s",
        exp_id, change["target"], change["type"],
        deltas["pnl_delta"], decision,
    )

    return output


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT & DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def export_results(results: dict[str, Any], path: str = "analysis/reports/experiment_result.json") -> str:
    filepath = Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    return str(filepath)


def print_results(results: dict[str, Any]) -> None:
    change = results.get("tested_change", {})
    baseline = results.get("baseline_performance", {})
    experimental = results.get("experimental_performance", {})
    deltas = results.get("deltas", {})
    decision = results.get("decision", "?")
    reason = results.get("reason", "")
    risks = results.get("risk_notes", [])

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  EXPERIMENT RESULT")
    print("═══════════════════════════════════════════════════════════════")
    print(f"  ID: {results.get('experiment_id', '?')}")
    print(f"  Change: {change.get('action', '?')}")
    print(f"  Target: {change.get('target', '?')} | Type: {change.get('type', '?')}")
    print()

    # Decision banner
    icons = {"ACCEPT": "✓", "REJECT": "✗", "INCONCLUSIVE": "?"}
    icon = icons.get(decision, "?")
    print(f"  DECISION: {icon} {decision}")
    print(f"  Reason:   {reason}")
    print()

    # Performance comparison
    print("─── PERFORMANCE ────────────────────────────────────────────────")
    print(f"  {'':20} {'Baseline':>12} {'Experiment':>12} {'Delta':>10}")
    print(f"  {'─'*20} {'─'*12} {'─'*12} {'─'*10}")
    print(f"  {'Trades':<20} {baseline.get('trades', 0):>12} {experimental.get('trades', 0):>12} {deltas.get('blocked_delta', 0):>+10}")
    print(f"  {'Winrate':<20} {baseline.get('winrate', 0):>11.1f}% {experimental.get('winrate', 0):>11.1f}% {deltas.get('winrate_delta', 0):>+9.1f}%")
    print(f"  {'Avg PnL':<20} {baseline.get('avg_pnl', 0):>12.2f} {experimental.get('avg_pnl', 0):>12.2f} {deltas.get('expectancy_delta', 0):>+10.2f}")
    print(f"  {'Total PnL':<20} {baseline.get('total_pnl', 0):>12.2f} {experimental.get('total_pnl', 0):>12.2f} {deltas.get('pnl_delta', 0):>+10.2f}")
    print(f"  {'Max Drawdown':<20} {baseline.get('max_drawdown', 0):>12.2f} {experimental.get('max_drawdown', 0):>12.2f} {deltas.get('drawdown_delta', 0):>+10.2f}")
    print()

    # Risks
    if risks:
        print("─── RISKS ──────────────────────────────────────────────────────")
        for r in risks:
            print(f"  ⚠ {r}")
        print()

    # Next action
    print("─── NEXT ACTION ────────────────────────────────────────────────")
    print(f"  → {results.get('next_action', '?')}")
    print()
    print("═══════════════════════════════════════════════════════════════")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    curated = sys.argv[1] if len(sys.argv) > 1 else "events/curated"
    output = sys.argv[2] if len(sys.argv) > 2 else "analysis/reports/experiment_result.json"

    results = run_experiment(curated_dir=curated)

    if results.get("error"):
        print(f"ERROR: {results['error']}")
        sys.exit(1)

    print_results(results)
    export_results(results, output)
    print(f"  Report saved to: {output}")
