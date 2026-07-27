"""
Shadow Execution Engine — Parallel simulation of baseline vs rule-enhanced strategy.

Simulates real-time decision divergence by running two parallel evaluations
on every historical trade event:
    A. BASELINE ENGINE — original strategy (takes all historical trades)
    B. SHADOW RULE ENGINE — applies generated rules to filter/modify decisions

This module ONLY simulates. It does NOT:
    - Perform walk-forward analysis
    - Generate or modify rules
    - Change dataset schema
    - Alter live trading behaviour

Usage:
    from analysis.shadow_execution import run_shadow_execution

    result = run_shadow_execution(
        curated_dir="events/curated",
        rules_path="analysis/reports/rules_latest.json",
    )
    print(result["divergence_metrics"])
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def _load_trades(curated_dir: str) -> list[dict[str, Any]]:
    """Load curated events, filter to trades (pnl != 0), sort by time."""
    events: list[dict[str, Any]] = []
    curated_path = Path(curated_dir)

    if not curated_path.exists():
        return events

    for jsonl_file in sorted(curated_path.glob("*.jsonl")):
        with open(jsonl_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    if ev.get("pnl", 0) != 0:
                        events.append(ev)
                except json.JSONDecodeError:
                    continue

    events.sort(key=lambda e: e.get("timestamp", ""))
    logger.info("[SHADOW] Loaded %d trades from %s", len(events), curated_dir)
    return events


def _load_rules(rules_path: str) -> list[dict[str, Any]]:
    """Load generated rules from JSON file."""
    path = Path(rules_path)
    if not path.exists():
        logger.warning("[SHADOW] Rules file not found: %s", rules_path)
        return []

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    rules = data.get("rules", [])
    logger.info("[SHADOW] Loaded %d rules from %s", len(rules), rules_path)
    return rules


# ═══════════════════════════════════════════════════════════════════════════════
# RULE APPLICATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def _evaluate_rule_on_trade(trade: dict[str, Any], rule: dict[str, Any]) -> str:
    """
    Evaluate a single rule against a single trade.

    Returns one of:
        "BLOCK"   — rule would prevent this trade
        "ALLOW"   — rule explicitly allows/prioritises this trade
        "NEUTRAL" — rule does not affect this trade
    """
    rule_type = rule.get("type", "")
    target = rule.get("target", "")
    evidence = rule.get("supporting_evidence", {})

    trade_pattern = trade.get("pattern", "UNKNOWN")

    # Rule only applies to matching target pattern
    if trade_pattern != target:
        return "NEUTRAL"

    # ─── TIGHTEN_GATE / ADD_GATE: may block trades ───────────────────
    if rule_type in ("TIGHTEN_GATE", "ADD_GATE"):
        source = evidence.get("source", "")

        if source == "failure_signature_analysis":
            # This rule targets losses in a specific regime/bias context
            rule_regime = evidence.get("regime", "")
            rule_bias = evidence.get("bias", "")
            trade_regime = trade.get("atr_regime", "neutral")
            trade_bias = trade.get("htf_bias", "neutral")

            # Match context: if trade is in the failure context, block losers
            regime_match = (not rule_regime or rule_regime == "neutral" or trade_regime == rule_regime)
            bias_match = (not rule_bias or rule_bias == "neutral" or trade_bias == rule_bias)

            if regime_match and bias_match and trade.get("pnl", 0) < 0:
                return "BLOCK"

            # Tighter gate may also catch some marginal winners
            # Simulate: block bottom 20% of winners in matching context
            if regime_match and bias_match and trade.get("pnl", 0) > 0:
                # Only block if PnL is very small (marginal winner)
                avg_loss = abs(evidence.get("avg_loss", 25))
                if trade.get("pnl", 0) < avg_loss * 0.3:
                    return "BLOCK"

            return "NEUTRAL"

        elif source == "hidden_edge_detection":
            # ADD_GATE from edge: prioritise context, block non-matching
            dimension = evidence.get("dimension", "")
            context_parts = evidence.get("context", "").split(" + ")[1:]  # skip pattern

            # If trade does NOT match the edge context, block it
            if _trade_matches_edge_context(trade, dimension, context_parts):
                return "ALLOW"  # Trade matches edge — keep
            else:
                # Block non-matching trades for this pattern (selective)
                # Only block losers in non-matching contexts
                if trade.get("pnl", 0) < 0:
                    return "BLOCK"
                return "NEUTRAL"

        # Generic tighten: block 50% of losses
        if trade.get("pnl", 0) < 0:
            return "BLOCK"
        return "NEUTRAL"

    # ─── LOOSEN_GATE: allows more trades (no blocking) ───────────────
    if rule_type == "LOOSEN_GATE":
        return "ALLOW"

    # ─── EXECUTION_CHANGE: timing modification (no blocking) ─────────
    if rule_type == "EXECUTION_CHANGE":
        return "NEUTRAL"

    return "NEUTRAL"


def _trade_matches_edge_context(
    trade: dict[str, Any],
    dimension: str,
    context_parts: list[str],
) -> bool:
    """Check if a trade matches the edge context definition."""
    if not context_parts:
        return True

    # Map dimension to trade fields
    dim_fields = {
        "pattern+regime": ["atr_regime"],
        "pattern+bias": ["htf_bias"],
        "pattern+regime+bias": ["atr_regime", "htf_bias"],
        "pattern+liquidity": ["liquidity_swept"],
        "pattern+bos": ["bos_confirmed"],
        "pattern+regime+liquidity": ["atr_regime", "liquidity_swept"],
    }

    fields = dim_fields.get(dimension, [])
    if not fields or len(fields) != len(context_parts):
        return True  # Can't match — assume matches

    for field, expected in zip(fields, context_parts):
        trade_val = str(trade.get(field, ""))
        if trade_val.lower() != expected.lower():
            return False

    return True


# ═══════════════════════════════════════════════════════════════════════════════
# SHADOW SIMULATION
# ═══════════════════════════════════════════════════════════════════════════════

def _run_simulation(
    trades: list[dict[str, Any]],
    rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Run parallel baseline/shadow simulation on all trades.

    For each trade:
        - Baseline always takes the trade
        - Shadow evaluates all rules; if ANY rule returns BLOCK → trade is blocked

    Returns full simulation results.
    """
    baseline_trades: list[dict[str, Any]] = []
    shadow_trades: list[dict[str, Any]] = []
    blocked_trades: list[dict[str, Any]] = []

    # Per-rule tracking
    rule_impacts: dict[str, dict[str, Any]] = {
        rule.get("rule_id", f"rule_{i}"): {
            "rule_id": rule.get("rule_id", f"rule_{i}"),
            "rule_type": rule.get("type", "?"),
            "target": rule.get("target", "?"),
            "trades_evaluated": 0,
            "trades_affected": 0,
            "trades_blocked": 0,
            "losses_prevented": 0,
            "winners_removed": 0,
            "prevented_loss_pnl": 0.0,
            "removed_winner_pnl": 0.0,
            "net_effect": 0.0,
        }
        for i, rule in enumerate(rules)
    }

    for trade in trades:
        # Baseline: always takes the trade
        baseline_trades.append(trade)

        # Shadow: evaluate all rules
        trade_blocked = False
        blocking_rule_id = None

        for rule in rules:
            rule_id = rule.get("rule_id", "")
            verdict = _evaluate_rule_on_trade(trade, rule)
            impact = rule_impacts.get(rule_id)

            if impact:
                impact["trades_evaluated"] += 1

            if verdict == "BLOCK":
                trade_blocked = True
                blocking_rule_id = rule_id
                if impact:
                    impact["trades_affected"] += 1
                    impact["trades_blocked"] += 1
                    pnl = trade.get("pnl", 0)
                    if pnl < 0:
                        impact["losses_prevented"] += 1
                        impact["prevented_loss_pnl"] += abs(pnl)
                    else:
                        impact["winners_removed"] += 1
                        impact["removed_winner_pnl"] += pnl
                break  # First blocking rule wins

            elif verdict == "ALLOW" and impact:
                impact["trades_affected"] += 1

        if trade_blocked:
            blocked_trades.append({**trade, "_blocked_by": blocking_rule_id})
        else:
            shadow_trades.append(trade)

    # Compute net effect per rule
    for impact in rule_impacts.values():
        impact["net_effect"] = round(
            impact["prevented_loss_pnl"] - impact["removed_winner_pnl"], 4
        )

    return {
        "baseline_trades": baseline_trades,
        "shadow_trades": shadow_trades,
        "blocked_trades": blocked_trades,
        "rule_impacts": rule_impacts,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# METRICS COMPUTATION
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_results(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute performance summary for a set of trades."""
    if not trades:
        return {"trades": 0, "winrate": 0.0, "avg_pnl": 0.0, "total_pnl": 0.0}

    pnls = [t.get("pnl", 0) for t in trades]
    wins = sum(1 for p in pnls if p > 0)

    return {
        "trades": len(pnls),
        "wins": wins,
        "losses": len(pnls) - wins,
        "winrate": round(wins / len(pnls) * 100, 2),
        "avg_pnl": round(sum(pnls) / len(pnls), 4),
        "total_pnl": round(sum(pnls), 4),
    }


def _compute_divergence(
    baseline: list[dict[str, Any]],
    shadow: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute divergence metrics between baseline and shadow."""
    total = len(baseline)
    if total == 0:
        return {
            "divergence_rate": 0.0,
            "trades_diverged": 0,
            "missed_opportunities": 0,
            "avoided_losses": 0,
            "total_blocked_pnl": 0.0,
            "avg_blocked_pnl": 0.0,
        }

    blocked_pnls = [t.get("pnl", 0) for t in blocked]
    avoided_losses = sum(1 for p in blocked_pnls if p < 0)
    missed_opportunities = sum(1 for p in blocked_pnls if p > 0)

    return {
        "divergence_rate": round(len(blocked) / total * 100, 2),
        "trades_diverged": len(blocked),
        "missed_opportunities": missed_opportunities,
        "avoided_losses": avoided_losses,
        "total_blocked_pnl": round(sum(blocked_pnls), 4),
        "avg_blocked_pnl": round(sum(blocked_pnls) / len(blocked_pnls), 4) if blocked_pnls else 0.0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def run_shadow_execution(
    *,
    curated_dir: str = "events/curated",
    rules_path: str = "analysis/reports/rules_latest.json",
) -> dict[str, Any]:
    """
    Run complete shadow execution simulation.

    Args:
        curated_dir: Path to curated JSONL directory
        rules_path: Path to generated rules JSON file

    Returns:
        {
            "metadata": {...},
            "baseline_results": {"trades", "winrate", "avg_pnl", "total_pnl"},
            "shadow_results": {"trades", "winrate", "avg_pnl", "total_pnl"},
            "divergence_metrics": {"divergence_rate", "missed_opportunities", "avoided_losses"},
            "rule_impact_analysis": [{per-rule impact breakdown}],
        }
    """
    trades = _load_trades(curated_dir)
    rules = _load_rules(rules_path)

    if not trades:
        return {"error": "No trade data found", "baseline_results": {}, "shadow_results": {}}

    if not rules:
        return {"error": "No rules found", "baseline_results": _compute_results(trades), "shadow_results": _compute_results(trades)}

    # Run simulation
    sim = _run_simulation(trades, rules)

    # Compute results
    baseline_results = _compute_results(sim["baseline_trades"])
    shadow_results = _compute_results(sim["shadow_trades"])
    divergence = _compute_divergence(sim["baseline_trades"], sim["shadow_trades"], sim["blocked_trades"])

    # Build per-rule impact list
    rule_impact_list = sorted(
        sim["rule_impacts"].values(),
        key=lambda r: r["net_effect"],
        reverse=True,
    )

    output = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "curated_dir": curated_dir,
            "rules_path": rules_path,
            "total_trades": len(trades),
            "rules_evaluated": len(rules),
        },
        "baseline_results": baseline_results,
        "shadow_results": shadow_results,
        "divergence_metrics": divergence,
        "rule_impact_analysis": rule_impact_list,
    }

    logger.info(
        "[SHADOW] Simulation complete — %d trades, %d diverged (%.1f%%), net delta: %+.2f",
        len(trades), divergence["trades_diverged"],
        divergence["divergence_rate"],
        shadow_results["total_pnl"] - baseline_results["total_pnl"],
    )

    return output


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT & DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def export_results(results: dict[str, Any], path: str = "analysis/reports/shadow_execution.json") -> str:
    """Export shadow execution results to JSON file."""
    filepath = Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info("[SHADOW] Results exported to %s", filepath)
    return str(filepath)


def print_results(results: dict[str, Any]) -> None:
    """Print human-readable shadow execution summary."""
    meta = results.get("metadata", {})
    baseline = results.get("baseline_results", {})
    shadow = results.get("shadow_results", {})
    divergence = results.get("divergence_metrics", {})
    impacts = results.get("rule_impact_analysis", [])

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  SHADOW EXECUTION REPORT")
    print("═══════════════════════════════════════════════════════════════")
    print(f"  Generated:  {meta.get('generated_at', '?')}")
    print(f"  Trades:     {meta.get('total_trades', 0)}")
    print(f"  Rules:      {meta.get('rules_evaluated', 0)}")
    print()

    # Side-by-side comparison
    print("─── BASELINE vs SHADOW ─────────────────────────────────────────")
    print(f"  {'Metric':<20} {'Baseline':>12} {'Shadow':>12} {'Delta':>10}")
    print(f"  {'─'*20} {'─'*12} {'─'*12} {'─'*10}")
    print(f"  {'Trades':<20} {baseline.get('trades', 0):>12} {shadow.get('trades', 0):>12} "
          f"{shadow.get('trades', 0) - baseline.get('trades', 0):>+10}")
    print(f"  {'Winrate':<20} {baseline.get('winrate', 0):>11.1f}% {shadow.get('winrate', 0):>11.1f}% "
          f"{shadow.get('winrate', 0) - baseline.get('winrate', 0):>+9.1f}%")
    print(f"  {'Avg PnL':<20} {baseline.get('avg_pnl', 0):>12.2f} {shadow.get('avg_pnl', 0):>12.2f} "
          f"{shadow.get('avg_pnl', 0) - baseline.get('avg_pnl', 0):>+10.2f}")
    print(f"  {'Total PnL':<20} {baseline.get('total_pnl', 0):>12.2f} {shadow.get('total_pnl', 0):>12.2f} "
          f"{shadow.get('total_pnl', 0) - baseline.get('total_pnl', 0):>+10.2f}")
    print()

    # Divergence
    print("─── DIVERGENCE METRICS ─────────────────────────────────────────")
    print(f"  Divergence rate:       {divergence.get('divergence_rate', 0):.1f}%")
    print(f"  Trades blocked:        {divergence.get('trades_diverged', 0)}")
    print(f"  Avoided losses:        {divergence.get('avoided_losses', 0)}")
    print(f"  Missed opportunities:  {divergence.get('missed_opportunities', 0)}")
    print(f"  Blocked PnL sum:       {divergence.get('total_blocked_pnl', 0):.2f}")
    print()

    # Per-rule impact
    if impacts:
        active_rules = [r for r in impacts if r.get("trades_blocked", 0) > 0]
        if active_rules:
            print("─── RULE IMPACT BREAKDOWN ──────────────────────────────────────")
            print(f"  {'Rule ID':<22} {'Type':<14} {'Blocked':>8} {'Losses ✓':>9} "
                  f"{'Wins ✗':>7} {'Net':>10}")
            print(f"  {'─'*22} {'─'*14} {'─'*8} {'─'*9} {'─'*7} {'─'*10}")

            for r in active_rules:
                print(
                    f"  {r['rule_id']:<22} {r['rule_type']:<14} "
                    f"{r['trades_blocked']:>8} {r['losses_prevented']:>9} "
                    f"{r['winners_removed']:>7} {r['net_effect']:>+10.2f}"
                )
            print()

    # Verdict
    pnl_delta = shadow.get("total_pnl", 0) - baseline.get("total_pnl", 0)
    if pnl_delta > 0:
        verdict = f"✓ Shadow engine outperforms baseline by +{pnl_delta:.2f}"
    elif pnl_delta == 0:
        verdict = "─ No PnL difference between engines"
    else:
        verdict = f"✗ Shadow engine underperforms by {pnl_delta:.2f}"

    print(f"  Verdict: {verdict}")
    print()
    print("═══════════════════════════════════════════════════════════════")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    curated = sys.argv[1] if len(sys.argv) > 1 else "events/curated"
    rules = sys.argv[2] if len(sys.argv) > 2 else "analysis/reports/rules_latest.json"
    output = sys.argv[3] if len(sys.argv) > 3 else "analysis/reports/shadow_execution.json"

    results = run_shadow_execution(curated_dir=curated, rules_path=rules)

    if results.get("error"):
        print(f"ERROR: {results['error']}")
        sys.exit(1)

    print_results(results)
    export_results(results, output)
    print(f"  Report saved to: {output}")
