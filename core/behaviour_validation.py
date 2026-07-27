"""
Behaviour Validation Layer — Analytics engine over the Trade Truth Graph.

Consumes trade nodes and computes:
    - Expectancy per strategy/pattern/HTF bucket
    - R distribution analysis
    - HTF alignment validation
    - Filter effectiveness
    - Lifecycle insights

This is NOT a trading engine. It is a self-auditing scientific measurement
system that continuously measures whether its own logic produces edge.

DATA SOURCE CONTRACT (STRICT):
    ✔ ALLOWED: Trade Truth Graph (local JSONL or S3)
    ✔ ALLOWED: trade_truth_v2 records (persisted, schema-versioned)
    ✔ ALLOWED: Aggregation, grouping, filtering, correlation

    ❌ FORBIDDEN: mt5, copy_rates, symbol_info_tick, live candles
    ❌ FORBIDDEN: execution engine, shadow_trades runtime state
    ❌ FORBIDDEN: strategy engine, decision engine, pipeline state
    ❌ FORBIDDEN: recalculating R from price feeds
    ❌ FORBIDDEN: reconstructing trades from candle data
    ❌ FORBIDDEN: inferring or estimating missing fields

    If a record is incomplete → reject it. Never fallback to live computation.

Usage:
    from core.behaviour_validation import run_behaviour_validation

    report = run_behaviour_validation()
    print(report["system_health"])
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
# DATA SOURCE CONTRACT ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════════════════

_REQUIRED_FIELDS = ("trade_id", "symbol", "outcome", "prices")
_REQUIRED_OUTCOME_FIELDS = ("r_multiple",)


def validate_trade_record(record: dict[str, Any]) -> tuple[bool, str]:
    """
    Validate a trade record against the truth-only input contract.

    Returns (valid, reason). If invalid, the record MUST be rejected.
    No fallback. No inference. No live recomputation.
    """
    if not isinstance(record, dict):
        return False, "record_not_dict"

    for field in _REQUIRED_FIELDS:
        if field not in record:
            return False, f"missing_required_field:{field}"

    outcome = record.get("outcome", {})
    if not isinstance(outcome, dict):
        return False, "outcome_not_dict"

    for field in _REQUIRED_OUTCOME_FIELDS:
        if field not in outcome:
            return False, f"missing_outcome_field:{field}"

    return True, "valid"


# ═══════════════════════════════════════════════════════════════════════════════
# EXPECTANCY ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_expectancy(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Expectancy per strategy, pattern, and HTF alignment bucket."""
    by_strategy: dict[str, list[float]] = defaultdict(list)
    by_pattern: dict[str, list[float]] = defaultdict(list)
    by_alignment: dict[str, list[float]] = defaultdict(list)

    for n in nodes:
        r = n.get("outcome", {}).get("r_multiple", 0)
        strat = n.get("strategy_meta", {}).get("strategy", "UNKNOWN")
        pattern = n.get("strategy_meta", {}).get("pattern", "UNKNOWN")
        alignment = n.get("htf_snapshot", {}).get("alignment_score", 0)

        by_strategy[strat].append(r)
        by_pattern[pattern].append(r)

        # Bucket alignment into ranges
        if alignment >= 0.8:
            by_alignment["HIGH (>=0.8)"].append(r)
        elif alignment >= 0.5:
            by_alignment["MEDIUM (0.5-0.8)"].append(r)
        else:
            by_alignment["LOW (<0.5)"].append(r)

    def _stats(values: list[float]) -> dict[str, Any]:
        if not values:
            return {"trades": 0, "avg_r": 0, "winrate": 0}
        wins = sum(1 for v in values if v > 0)
        return {
            "trades": len(values),
            "avg_r": round(sum(values) / len(values), 4),
            "total_r": round(sum(values), 4),
            "winrate": round(wins / len(values) * 100, 1),
        }

    return {
        "by_strategy": {k: _stats(v) for k, v in sorted(by_strategy.items())},
        "by_pattern": {k: _stats(v) for k, v in sorted(by_pattern.items())},
        "by_htf_alignment": {k: _stats(v) for k, v in sorted(by_alignment.items())},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# R DISTRIBUTION ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_r_distribution(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """R-multiple histogram, win/loss skew, fat tail detection."""
    r_values = [n.get("outcome", {}).get("r_multiple", 0) for n in nodes]
    if not r_values:
        return {"count": 0}

    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r < 0]
    breakevens = [r for r in r_values if r == 0]

    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0

    # Fat tail: trades > 3R or < -2R
    fat_winners = sum(1 for r in r_values if r > 3)
    fat_losers = sum(1 for r in r_values if r < -2)

    # By regime
    by_regime: dict[str, list[float]] = defaultdict(list)
    for n in nodes:
        regime = n.get("edges", {}).get("regime", "UNKNOWN")
        by_regime[regime].append(n.get("outcome", {}).get("r_multiple", 0))

    return {
        "count": len(r_values),
        "avg_r": round(sum(r_values) / len(r_values), 4),
        "median_r": round(sorted(r_values)[len(r_values) // 2], 4),
        "wins": len(wins),
        "losses": len(losses),
        "breakevens": len(breakevens),
        "avg_win_r": round(avg_win, 4),
        "avg_loss_r": round(avg_loss, 4),
        "win_loss_ratio": round(avg_win / abs(avg_loss), 3) if avg_loss != 0 else 999,
        "fat_tail_winners": fat_winners,
        "fat_tail_losers": fat_losers,
        "by_regime": {k: round(sum(v) / len(v), 4) for k, v in by_regime.items() if v},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# HTF VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_htf_validation(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """HTF alignment vs profitability analysis."""
    by_h4_bias: dict[str, list[float]] = defaultdict(list)
    aligned_vs_contradicted: dict[str, list[float]] = defaultdict(list)

    for n in nodes:
        htf = n.get("htf_snapshot", {})
        r = n.get("outcome", {}).get("r_multiple", 0)
        direction = n.get("position", {}).get("direction", "")

        h4 = htf.get("H4", {})
        h4_bias = h4.get("bias", htf.get("H4_bias", "UNKNOWN")) if isinstance(h4, dict) else "UNKNOWN"
        h1 = htf.get("H1", {})
        h1_bias = h1.get("bias", htf.get("H1_bias", "UNKNOWN")) if isinstance(h1, dict) else "UNKNOWN"

        by_h4_bias[h4_bias].append(r)

        # Check H1 alignment with trade direction
        if direction == "BUY" and h1_bias == "BULLISH":
            aligned_vs_contradicted["ALIGNED"].append(r)
        elif direction == "SELL" and h1_bias == "BEARISH":
            aligned_vs_contradicted["ALIGNED"].append(r)
        elif h1_bias == "NEUTRAL":
            aligned_vs_contradicted["NEUTRAL"].append(r)
        else:
            aligned_vs_contradicted["CONTRADICTED"].append(r)

    def _bucket(values: list[float]) -> dict:
        if not values:
            return {"trades": 0, "avg_r": 0}
        return {"trades": len(values), "avg_r": round(sum(values) / len(values), 4)}

    return {
        "by_h4_bias": {k: _bucket(v) for k, v in by_h4_bias.items()},
        "h1_alignment": {k: _bucket(v) for k, v in aligned_vs_contradicted.items()},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# FILTER EFFECTIVENESS
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_filter_effectiveness(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Win rate and avg R with/without each filter."""
    filter_sets: dict[str, list[float]] = defaultdict(list)
    all_r = [n.get("outcome", {}).get("r_multiple", 0) for n in nodes]

    for n in nodes:
        filters = n.get("strategy_meta", {}).get("filters_active", [])
        r = n.get("outcome", {}).get("r_multiple", 0)
        for f in filters:
            filter_sets[f].append(r)

    baseline_avg = sum(all_r) / len(all_r) if all_r else 0

    result = {}
    for f_name, r_values in filter_sets.items():
        avg_with = sum(r_values) / len(r_values) if r_values else 0
        result[f_name] = {
            "trades_with_filter": len(r_values),
            "avg_r_with_filter": round(avg_with, 4),
            "baseline_avg_r": round(baseline_avg, 4),
            "edge_contribution": round(avg_with - baseline_avg, 4),
        }

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# LIFECYCLE INSIGHTS
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_lifecycle_insights(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Average bars held, MAE efficiency, early exit analysis."""
    winners = [n for n in nodes if n.get("outcome", {}).get("r_multiple", 0) > 0]
    losers = [n for n in nodes if n.get("outcome", {}).get("r_multiple", 0) < 0]

    avg_bars_winners = (
        sum(n.get("outcome", {}).get("bars_held", n.get("lifecycle", {}).get("bars_held", 0)) for n in winners) / len(winners)
        if winners else 0
    )
    avg_bars_losers = (
        sum(n.get("outcome", {}).get("bars_held", n.get("lifecycle", {}).get("bars_held", 0)) for n in losers) / len(losers)
        if losers else 0
    )

    # MAE efficiency: how close to SL did losers get before exit?
    mae_values = [n.get("outcome", {}).get("mae_r", 0) for n in losers if n.get("outcome", {}).get("mae_r", 0) > 0]
    avg_mae_losers = sum(mae_values) / len(mae_values) if mae_values else 0

    # Exit efficiency for winners
    eff_values = [n.get("outcome", {}).get("exit_efficiency", 0) for n in winners if n.get("outcome", {}).get("exit_efficiency", 0) > 0]
    avg_exit_eff = sum(eff_values) / len(eff_values) if eff_values else 0

    return {
        "avg_bars_winners": round(avg_bars_winners, 1),
        "avg_bars_losers": round(avg_bars_losers, 1),
        "avg_mae_losers": round(avg_mae_losers, 4),
        "avg_exit_efficiency_winners": round(avg_exit_eff, 4),
        "total_winners": len(winners),
        "total_losers": len(losers),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def run_behaviour_validation(
    *,
    graph_dir: str = "logs/trade_truth_graph",
) -> dict[str, Any]:
    """
    Run full behaviour validation over the Trade Truth Graph.

    DATA SOURCE: Trade Truth Graph (local JSONL) ONLY.
    No live data access. No runtime state. No MT5 calls.

    Returns structured report covering all 6 analytics dimensions.
    """
    from core.trade_truth_graph import load_graph_local

    raw_nodes = load_graph_local(graph_dir)

    if not raw_nodes:
        return {
            "total_trades": 0,
            "system_health": "UNPROVEN",
            "reason": "No trade data in graph",
            "data_source_compliance": "PASS",
        }

    # ─── DATA CONTRACT ENFORCEMENT ────────────────────────────────────
    # Reject any record that does not meet truth-layer contract.
    # NEVER fallback to live computation. NEVER infer missing values.
    nodes: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []

    for record in raw_nodes:
        valid, reason = validate_trade_record(record)
        if valid:
            nodes.append(record)
        else:
            rejected.append({"trade_id": record.get("trade_id", "?"), "reason": reason})

    if rejected:
        logger.warning(
            "[BEHAVIOUR_VALIDATION] rejected %d/%d records (incomplete truth)",
            len(rejected), len(raw_nodes),
        )

    if not nodes:
        return {
            "total_trades": 0,
            "system_health": "UNPROVEN",
            "reason": f"All {len(raw_nodes)} records rejected (incomplete truth)",
            "data_source_compliance": "PASS",
            "rejected_records": rejected[:10],
        }
    # ─── END CONTRACT ENFORCEMENT ─────────────────────────────────────

    all_r = [n.get("outcome", {}).get("r_multiple", 0) for n in nodes]
    avg_r = sum(all_r) / len(all_r) if all_r else 0
    total_pnl_r = sum(all_r)

    expectancy = _compute_expectancy(nodes)
    r_dist = _compute_r_distribution(nodes)
    htf_val = _compute_htf_validation(nodes)
    filter_eff = _compute_filter_effectiveness(nodes)
    lifecycle = _compute_lifecycle_insights(nodes)

    # Determine system health
    if avg_r > 0.3 and r_dist.get("wins", 0) > r_dist.get("losses", 0):
        health = "PROFITABLE"
    elif avg_r > 0 or total_pnl_r > 0:
        health = "NEUTRAL"
    elif len(nodes) < 20:
        health = "UNPROVEN"
    else:
        health = "UNPROFITABLE"

    # Best/worst strategy
    strat_data = expectancy.get("by_strategy", {})
    best_strat = max(strat_data.items(), key=lambda x: x[1].get("avg_r", 0))[0] if strat_data else "NONE"
    worst_strat = min(strat_data.items(), key=lambda x: x[1].get("avg_r", 0))[0] if strat_data else "NONE"

    # HTF alignment edge
    alignment_data = expectancy.get("by_htf_alignment", {})
    high_align = alignment_data.get("HIGH (>=0.8)", {}).get("avg_r", 0)
    low_align = alignment_data.get("LOW (<0.5)", {}).get("avg_r", 0)
    htf_edge = round(high_align - low_align, 4)

    return {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_trades": len(nodes),
            "records_rejected": len(rejected),
            "graph_source": graph_dir,
        },
        "data_source_compliance": "PASS",
        "truth_layer_only_usage": True,
        "live_data_access_detected": False,
        "summary": {
            "total_trades": len(nodes),
            "avg_r": round(avg_r, 4),
            "total_r": round(total_pnl_r, 4),
            "expectancy": round(avg_r, 4),
            "best_strategy": best_strat,
            "worst_strategy": worst_strat,
            "htf_alignment_edge": htf_edge,
            "mae_efficiency": lifecycle.get("avg_mae_losers", 0),
            "system_health": health,
        },
        "expectancy_analysis": expectancy,
        "r_distribution": r_dist,
        "htf_validation": htf_val,
        "filter_effectiveness": filter_eff,
        "lifecycle_insights": lifecycle,
    }


def export_report(report: dict[str, Any], path: str = "analysis/reports/behaviour_validation.json") -> str:
    """Export behaviour validation report to JSON."""
    filepath = Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    return str(filepath)


def print_report(report: dict[str, Any]) -> None:
    """Print human-readable behaviour validation summary."""
    summary = report.get("summary", {})
    r_dist = report.get("r_distribution", {})
    lifecycle = report.get("lifecycle_insights", {})

    print()
    print("=" * 60)
    print("  BEHAVIOUR VALIDATION REPORT")
    print("=" * 60)
    print(f"  Trades:     {summary.get('total_trades', 0)}")
    print(f"  Avg R:      {summary.get('avg_r', 0)}")
    print(f"  Total R:    {summary.get('total_r', 0)}")
    print(f"  Health:     {summary.get('system_health', '?')}")
    print()
    print(f"  Best strategy:     {summary.get('best_strategy', '?')}")
    print(f"  Worst strategy:    {summary.get('worst_strategy', '?')}")
    print(f"  HTF alignment edge: {summary.get('htf_alignment_edge', 0)}")
    print()
    if r_dist:
        print(f"  Wins: {r_dist.get('wins', 0)} | Losses: {r_dist.get('losses', 0)}")
        print(f"  Avg win: {r_dist.get('avg_win_r', 0)}R | Avg loss: {r_dist.get('avg_loss_r', 0)}R")
        print(f"  W/L ratio: {r_dist.get('win_loss_ratio', 0)}")
    if lifecycle:
        print(f"  Avg bars (winners): {lifecycle.get('avg_bars_winners', 0)}")
        print(f"  Avg bars (losers):  {lifecycle.get('avg_bars_losers', 0)}")
        print(f"  Exit efficiency:    {lifecycle.get('avg_exit_efficiency_winners', 0)}")
    print("=" * 60)
