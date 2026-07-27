"""
Strategy Replay & Intelligence Module — Evidence-based strategy analysis.

Analyses historical trading events and produces structured intelligence reports
for strategy improvement. Answers:
    - Why do trades win or lose?
    - What conditions create edge?
    - What patterns are actually profitable in context?
    - What system rules should be improved?

Outputs:
    1. Pattern Expectancy Table — ranked pattern performance
    2. Contextual Edge Matrix — performance by market conditions
    3. Failure Signature Analysis — loss cluster profiles
    4. Hidden Edge Detection — high-confidence edge zones

Data Sources:
    - Local: events/curated/*.jsonl (immediate, no AWS needed)
    - AWS: Athena query against trading_bot.curated_events table

Usage:
    from analysis.strategy_replay import run_full_analysis, load_local_data

    # Local analysis (no AWS required)
    report = run_full_analysis()
    print(report["pattern_expectancy"])

    # AWS Athena analysis
    report = run_full_analysis(source="athena")

    # Export to JSON
    export_report(report, "analysis/reports/latest.json")

Rules:
    - Does NOT change trading logic
    - Does NOT introduce new filters
    - Does NOT modify data schema
    - Only analyses existing recorded behaviour
"""

from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_local_data(curated_dir: str = "events/curated") -> list[dict[str, Any]]:
    """
    Load curated events from local JSONL files.

    Returns list of flat event dicts conforming to curated schema.
    """
    events: list[dict[str, Any]] = []
    curated_path = Path(curated_dir)

    if not curated_path.exists():
        logger.warning("[REPLAY] Curated directory not found: %s", curated_dir)
        return events

    for jsonl_file in sorted(curated_path.glob("*.jsonl")):
        with open(jsonl_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    logger.info("[REPLAY] Loaded %d events from %s", len(events), curated_dir)
    return events


def load_athena_data(
    query: str | None = None,
    database: str = "trading_bot",
    table: str = "curated_events",
) -> list[dict[str, Any]]:
    """
    Load curated events from Athena query.

    Requires boto3 and valid AWS credentials.
    Falls back to local data if Athena unavailable.
    """
    if query is None:
        query = f"SELECT * FROM {table}"

    try:
        from data_pipeline.aws_glue_setup import run_athena_query
        result = run_athena_query(query, database=database)
        if result["status"] == "SUCCEEDED" and result.get("results"):
            # Convert string results back to typed values
            return [_type_cast_row(row) for row in result["results"]]
        else:
            logger.warning("[REPLAY] Athena query failed: %s", result.get("error", "unknown"))
            return []
    except ImportError:
        logger.warning("[REPLAY] AWS modules not available, use load_local_data()")
        return []


def _type_cast_row(row: dict[str, str]) -> dict[str, Any]:
    """Cast Athena string results back to proper types."""
    return {
        "timestamp": row.get("timestamp", ""),
        "symbol": row.get("symbol", ""),
        "event_type": row.get("event_type", ""),
        "pattern": row.get("pattern", "UNKNOWN"),
        "htf_bias": row.get("htf_bias", "neutral"),
        "liquidity_swept": row.get("liquidity_swept", "false").lower() == "true",
        "bos_confirmed": row.get("bos_confirmed", "false").lower() == "true",
        "atr_regime": row.get("atr_regime", "neutral"),
        "pnl": float(row.get("pnl", "0") or "0"),
        "trade_id": row.get("trade_id", ""),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 1: PATTERN EXPECTANCY TABLE
# ═══════════════════════════════════════════════════════════════════════════════

def compute_pattern_expectancy(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Compute pattern expectancy rankings.

    Output per pattern:
        - pattern: Pattern name
        - trades: Total trade count
        - wins: Number of winning trades
        - losses: Number of losing trades
        - winrate: Win percentage (0-100)
        - avg_pnl: Average PnL per trade
        - total_pnl: Sum of all PnL
        - avg_win: Average winning trade PnL
        - avg_loss: Average losing trade PnL
        - expectancy_rank: Rank by avg_pnl (1 = best)

    Only includes events with pnl != 0 (actual trades).
    """
    # Filter to actual trades
    trades = [e for e in events if e.get("pnl", 0) != 0]

    if not trades:
        return []

    # Group by pattern
    by_pattern: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        pattern = trade.get("pattern", "UNKNOWN")
        by_pattern[pattern].append(trade["pnl"])

    # Compute stats
    results = []
    for pattern, pnls in by_pattern.items():
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        results.append({
            "pattern": pattern,
            "trades": len(pnls),
            "wins": len(wins),
            "losses": len(losses),
            "winrate": round(len(wins) / len(pnls) * 100, 2) if pnls else 0.0,
            "avg_pnl": round(sum(pnls) / len(pnls), 4) if pnls else 0.0,
            "total_pnl": round(sum(pnls), 4),
            "avg_win": round(sum(wins) / len(wins), 4) if wins else 0.0,
            "avg_loss": round(sum(losses) / len(losses), 4) if losses else 0.0,
            "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if losses and sum(losses) != 0 else float("inf"),
        })

    # Sort by avg_pnl descending and assign rank
    results.sort(key=lambda x: x["avg_pnl"], reverse=True)
    for rank, row in enumerate(results, 1):
        row["expectancy_rank"] = rank

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 2: CONTEXTUAL EDGE MATRIX
# ═══════════════════════════════════════════════════════════════════════════════

def compute_contextual_edge_matrix(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """
    Compute performance grouped by market conditions.

    Groups:
        - pattern + htf_bias
        - pattern + liquidity_swept
        - pattern + bos_confirmed
        - pattern + atr_regime

    Output per group:
        {context_dimension: [{combination, trades, avg_pnl, winrate, total_pnl}]}

    Goal: identify WHERE patterns actually work.
    """
    trades = [e for e in events if e.get("pnl", 0) != 0]

    if not trades:
        return {}

    dimensions = {
        "pattern_x_htf_bias": lambda t: (t.get("pattern", "UNKNOWN"), t.get("htf_bias", "neutral")),
        "pattern_x_liquidity": lambda t: (t.get("pattern", "UNKNOWN"), str(t.get("liquidity_swept", False))),
        "pattern_x_bos": lambda t: (t.get("pattern", "UNKNOWN"), str(t.get("bos_confirmed", False))),
        "pattern_x_regime": lambda t: (t.get("pattern", "UNKNOWN"), t.get("atr_regime", "neutral")),
    }

    results: dict[str, list[dict[str, Any]]] = {}

    for dim_name, key_fn in dimensions.items():
        groups: dict[tuple, list[float]] = defaultdict(list)
        for trade in trades:
            key = key_fn(trade)
            groups[key].append(trade["pnl"])

        dim_results = []
        for (pattern, context), pnls in sorted(groups.items()):
            wins = [p for p in pnls if p > 0]
            dim_results.append({
                "pattern": pattern,
                "context": context,
                "trades": len(pnls),
                "avg_pnl": round(sum(pnls) / len(pnls), 4) if pnls else 0.0,
                "total_pnl": round(sum(pnls), 4),
                "winrate": round(len(wins) / len(pnls) * 100, 2) if pnls else 0.0,
            })

        # Sort by avg_pnl descending
        dim_results.sort(key=lambda x: x["avg_pnl"], reverse=True)
        results[dim_name] = dim_results

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 3: FAILURE SIGNATURE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def compute_failure_signatures(
    events: list[dict[str, Any]],
    *,
    min_losses: int = 3,
) -> list[dict[str, Any]]:
    """
    Identify conditions that correlate with losses.

    Finds loss clusters — combinations of (pattern, regime, bias, structure)
    that produce consistent drawdown.

    Args:
        events: Curated events
        min_losses: Minimum loss count to qualify as a "signature"

    Output per failure cluster:
        - signature: Description of the condition set
        - pattern, regime, bias: The context
        - loss_count: Number of losing trades
        - avg_loss: Average loss in this cluster
        - total_loss: Sum of losses
        - pct_of_all_losses: What % of total losses this cluster represents
        - severity_rank: Rank by total_loss (1 = worst cluster)
    """
    # Only losing trades
    losses = [e for e in events if e.get("pnl", 0) < 0]

    if not losses:
        return []

    total_loss_pnl = sum(e["pnl"] for e in losses)

    # Group by (pattern, regime, bias) to find loss clusters
    clusters: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for trade in losses:
        key = (
            trade.get("pattern", "UNKNOWN"),
            trade.get("atr_regime", "neutral"),
            trade.get("htf_bias", "neutral"),
        )
        clusters[key].append(trade["pnl"])

    # Filter to meaningful clusters
    results = []
    for (pattern, regime, bias), pnls in clusters.items():
        if len(pnls) < min_losses:
            continue

        cluster_total = sum(pnls)
        results.append({
            "pattern": pattern,
            "regime": regime,
            "bias": bias,
            "signature": f"{pattern} | {regime} | {bias}",
            "loss_count": len(pnls),
            "avg_loss": round(sum(pnls) / len(pnls), 4),
            "total_loss": round(cluster_total, 4),
            "max_loss": round(min(pnls), 4),
            "pct_of_all_losses": round(cluster_total / total_loss_pnl * 100, 2) if total_loss_pnl != 0 else 0.0,
        })

    # Sort by total_loss (most negative = worst)
    results.sort(key=lambda x: x["total_loss"])
    for rank, row in enumerate(results, 1):
        row["severity_rank"] = rank

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 4: HIDDEN EDGE DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def compute_hidden_edges(
    events: list[dict[str, Any]],
    *,
    min_trades: int = 5,
    min_winrate_above_baseline: float = 5.0,
) -> list[dict[str, Any]]:
    """
    Identify high-performing subsets (hidden edge zones).

    Finds context combinations where:
        - avg_pnl is significantly above dataset baseline
        - sample size is statistically meaningful (>= min_trades)
        - winrate exceeds dataset average by min_winrate_above_baseline

    Args:
        events: Curated events
        min_trades: Minimum trades to qualify
        min_winrate_above_baseline: Minimum winrate improvement over baseline (%)

    Output per edge zone:
        - context: Description of the condition set
        - trades: Number of trades
        - avg_pnl: Average PnL
        - winrate: Win percentage
        - baseline_avg_pnl: Dataset average for comparison
        - baseline_winrate: Dataset winrate for comparison
        - edge_pnl: Excess PnL vs baseline
        - edge_winrate: Excess winrate vs baseline
        - confidence: Simple confidence score (0-1)
    """
    trades = [e for e in events if e.get("pnl", 0) != 0]

    if len(trades) < min_trades:
        return []

    # Compute baseline
    all_pnls = [t["pnl"] for t in trades]
    baseline_avg = sum(all_pnls) / len(all_pnls)
    baseline_winrate = sum(1 for p in all_pnls if p > 0) / len(all_pnls) * 100

    # Generate all context combinations to test
    contexts: dict[str, dict[tuple, list[float]]] = {
        "pattern+regime": defaultdict(list),
        "pattern+bias": defaultdict(list),
        "pattern+regime+bias": defaultdict(list),
        "pattern+liquidity": defaultdict(list),
        "pattern+bos": defaultdict(list),
        "pattern+regime+liquidity": defaultdict(list),
    }

    for trade in trades:
        p = trade.get("pattern", "UNKNOWN")
        r = trade.get("atr_regime", "neutral")
        b = trade.get("htf_bias", "neutral")
        liq = str(trade.get("liquidity_swept", False))
        bos = str(trade.get("bos_confirmed", False))
        pnl = trade["pnl"]

        contexts["pattern+regime"][(p, r)].append(pnl)
        contexts["pattern+bias"][(p, b)].append(pnl)
        contexts["pattern+regime+bias"][(p, r, b)].append(pnl)
        contexts["pattern+liquidity"][(p, liq)].append(pnl)
        contexts["pattern+bos"][(p, bos)].append(pnl)
        contexts["pattern+regime+liquidity"][(p, r, liq)].append(pnl)

    # Find edges
    edges = []
    for dim_name, groups in contexts.items():
        for key, pnls in groups.items():
            if len(pnls) < min_trades:
                continue

            avg_pnl = sum(pnls) / len(pnls)
            winrate = sum(1 for p in pnls if p > 0) / len(pnls) * 100
            edge_pnl = avg_pnl - baseline_avg
            edge_winrate = winrate - baseline_winrate

            # Must beat baseline meaningfully
            if edge_winrate < min_winrate_above_baseline:
                continue
            if avg_pnl <= baseline_avg:
                continue

            # Simple confidence: combination of sample size and edge magnitude
            confidence = min(1.0, (len(pnls) / 30) * (edge_winrate / 20))

            context_str = " + ".join(str(k) for k in key)
            edges.append({
                "dimension": dim_name,
                "context": context_str,
                "context_parts": list(key),
                "trades": len(pnls),
                "avg_pnl": round(avg_pnl, 4),
                "winrate": round(winrate, 2),
                "total_pnl": round(sum(pnls), 4),
                "baseline_avg_pnl": round(baseline_avg, 4),
                "baseline_winrate": round(baseline_winrate, 2),
                "edge_pnl": round(edge_pnl, 4),
                "edge_winrate": round(edge_winrate, 2),
                "confidence": round(confidence, 3),
            })

    # Sort by confidence descending, then by edge_pnl
    edges.sort(key=lambda x: (-x["confidence"], -x["edge_pnl"]))

    return edges


# ═══════════════════════════════════════════════════════════════════════════════
# FULL ANALYSIS RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_full_analysis(
    *,
    source: str = "local",
    curated_dir: str = "events/curated",
    min_failure_losses: int = 3,
    min_edge_trades: int = 5,
    min_edge_winrate: float = 5.0,
) -> dict[str, Any]:
    """
    Run the complete strategy intelligence analysis.

    Args:
        source: "local" for local JSONL files, "athena" for AWS Athena
        curated_dir: Path to local curated directory (when source="local")
        min_failure_losses: Minimum losses for failure signature
        min_edge_trades: Minimum trades for hidden edge detection
        min_edge_winrate: Minimum winrate improvement for edge

    Returns:
        Complete analysis report with:
            - metadata: Analysis parameters and dataset stats
            - pattern_expectancy: Ranked pattern performance table
            - contextual_edge_matrix: Performance by market conditions
            - failure_signatures: Loss cluster profiles
            - hidden_edges: High-confidence edge zones
            - summary: Executive summary of key findings
    """
    # Load data
    if source == "athena":
        events = load_athena_data()
        if not events:
            logger.warning("[REPLAY] Athena returned no data, falling back to local")
            events = load_local_data(curated_dir)
    else:
        events = load_local_data(curated_dir)

    if not events:
        return {"error": "No data available for analysis"}

    # Filter to trades only for stats
    trades = [e for e in events if e.get("pnl", 0) != 0]

    # Run all analyses
    pattern_exp = compute_pattern_expectancy(events)
    edge_matrix = compute_contextual_edge_matrix(events)
    failures = compute_failure_signatures(events, min_losses=min_failure_losses)
    edges = compute_hidden_edges(events, min_trades=min_edge_trades, min_winrate_above_baseline=min_edge_winrate)

    # Build report
    report = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": source,
            "total_events": len(events),
            "total_trades": len(trades),
            "total_pnl": round(sum(t["pnl"] for t in trades), 4) if trades else 0.0,
            "overall_winrate": round(
                sum(1 for t in trades if t["pnl"] > 0) / len(trades) * 100, 2
            ) if trades else 0.0,
            "unique_patterns": len(set(t.get("pattern") for t in trades)),
            "unique_symbols": len(set(t.get("symbol") for t in trades)),
            "date_range": {
                "earliest": min((t.get("timestamp", "") for t in trades), default=""),
                "latest": max((t.get("timestamp", "") for t in trades), default=""),
            },
        },
        "pattern_expectancy": pattern_exp,
        "contextual_edge_matrix": edge_matrix,
        "failure_signatures": failures,
        "hidden_edges": edges,
        "summary": _build_summary(pattern_exp, edge_matrix, failures, edges, trades),
    }

    logger.info(
        "[REPLAY] Analysis complete — %d trades, %d patterns, %d edges, %d failure clusters",
        len(trades), len(pattern_exp), len(edges), len(failures),
    )

    return report


def _build_summary(
    pattern_exp: list[dict],
    edge_matrix: dict[str, list],
    failures: list[dict],
    edges: list[dict],
    trades: list[dict],
) -> dict[str, Any]:
    """Build executive summary of key findings."""
    summary: dict[str, Any] = {
        "key_findings": [],
        "top_patterns": [],
        "worst_patterns": [],
        "strongest_edges": [],
        "critical_failures": [],
    }

    if not trades:
        return summary

    # Top/bottom patterns
    if pattern_exp:
        profitable = [p for p in pattern_exp if p["avg_pnl"] > 0]
        unprofitable = [p for p in pattern_exp if p["avg_pnl"] < 0]
        summary["top_patterns"] = profitable[:3]
        summary["worst_patterns"] = unprofitable[-3:] if unprofitable else []

        if profitable:
            summary["key_findings"].append(
                f"{len(profitable)} of {len(pattern_exp)} patterns are profitable"
            )

    # Strongest edges
    if edges:
        high_conf = [e for e in edges if e["confidence"] >= 0.5]
        summary["strongest_edges"] = edges[:5]
        if high_conf:
            summary["key_findings"].append(
                f"{len(high_conf)} high-confidence edge zones detected"
            )

    # Critical failures
    if failures:
        summary["critical_failures"] = failures[:3]
        top_failure = failures[0]
        summary["key_findings"].append(
            f"Top loss cluster: {top_failure['signature']} "
            f"({top_failure['loss_count']} losses, avg={top_failure['avg_loss']:.2f})"
        )

    # Overall assessment
    total_pnl = sum(t["pnl"] for t in trades)
    if total_pnl > 0:
        summary["key_findings"].insert(0, f"System is net profitable (total PnL: {total_pnl:.2f})")
    else:
        summary["key_findings"].insert(0, f"System is net negative (total PnL: {total_pnl:.2f})")

    return summary


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT EXPORT
# ═══════════════════════════════════════════════════════════════════════════════

def export_report(report: dict[str, Any], output_path: str = "analysis/reports/latest.json") -> str:
    """
    Export analysis report to JSON file.

    Creates directories if needed. Returns the output file path.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    logger.info("[REPLAY] Report exported to %s", path)
    return str(path)


def print_report(report: dict[str, Any]) -> None:
    """Print a human-readable summary of the analysis report."""
    meta = report.get("metadata", {})
    summary = report.get("summary", {})

    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  STRATEGY INTELLIGENCE REPORT")
    print("═══════════════════════════════════════════════════════════════")
    print(f"  Generated: {meta.get('generated_at', '?')}")
    print(f"  Source:    {meta.get('source', '?')}")
    print(f"  Trades:    {meta.get('total_trades', 0)}")
    print(f"  Total PnL: {meta.get('total_pnl', 0):.2f}")
    print(f"  Winrate:   {meta.get('overall_winrate', 0):.1f}%")
    print(f"  Patterns:  {meta.get('unique_patterns', 0)}")
    print()

    # Key findings
    findings = summary.get("key_findings", [])
    if findings:
        print("─── KEY FINDINGS ───────────────────────────────────────────────")
        for finding in findings:
            print(f"  • {finding}")
        print()

    # Pattern expectancy
    patterns = report.get("pattern_expectancy", [])
    if patterns:
        print("─── PATTERN EXPECTANCY (ranked) ────────────────────────────────")
        print(f"  {'Pattern':<25} {'Trades':>7} {'WR%':>7} {'Avg PnL':>10} {'Total':>10} {'PF':>7}")
        print(f"  {'─' * 25} {'─' * 7} {'─' * 7} {'─' * 10} {'─' * 10} {'─' * 7}")
        for p in patterns[:10]:
            pf = f"{p['profit_factor']:.2f}" if p['profit_factor'] != float('inf') else "∞"
            print(
                f"  {p['pattern']:<25} {p['trades']:>7} {p['winrate']:>6.1f}% "
                f"{p['avg_pnl']:>10.2f} {p['total_pnl']:>10.2f} {pf:>7}"
            )
        print()

    # Hidden edges
    edges = report.get("hidden_edges", [])
    if edges:
        print("─── HIDDEN EDGES (top 5) ───────────────────────────────────────")
        print(f"  {'Context':<40} {'Trades':>7} {'Avg PnL':>10} {'WR%':>7} {'Conf':>6}")
        print(f"  {'─' * 40} {'─' * 7} {'─' * 10} {'─' * 7} {'─' * 6}")
        for e in edges[:5]:
            print(
                f"  {e['context']:<40} {e['trades']:>7} "
                f"{e['avg_pnl']:>10.2f} {e['winrate']:>6.1f}% {e['confidence']:>5.2f}"
            )
        print()

    # Failure clusters
    failures = report.get("failure_signatures", [])
    if failures:
        print("─── FAILURE SIGNATURES (worst 5) ───────────────────────────────")
        print(f"  {'Signature':<40} {'Losses':>7} {'Avg Loss':>10} {'Total':>10}")
        print(f"  {'─' * 40} {'─' * 7} {'─' * 10} {'─' * 10}")
        for f_item in failures[:5]:
            print(
                f"  {f_item['signature']:<40} {f_item['loss_count']:>7} "
                f"{f_item['avg_loss']:>10.2f} {f_item['total_loss']:>10.2f}"
            )
        print()

    print("═══════════════════════════════════════════════════════════════")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    source = sys.argv[1] if len(sys.argv) > 1 else "local"
    curated_dir = sys.argv[2] if len(sys.argv) > 2 else "events/curated"
    output = sys.argv[3] if len(sys.argv) > 3 else "analysis/reports/latest.json"

    report = run_full_analysis(source=source, curated_dir=curated_dir)

    if "error" in report:
        print(f"ERROR: {report['error']}")
        sys.exit(1)

    print_report(report)
    export_report(report, output)
    print(f"\n  Report saved to: {output}")
