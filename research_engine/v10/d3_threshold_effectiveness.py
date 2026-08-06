"""
V10-D3: Decision Threshold Effectiveness

Question: "What minimum score threshold produces the best risk-adjusted outcomes?"

Tests hypothetical score thresholds and measures how filtering affects
expectancy, win rate, and profit factor.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from research_engine.v10.base import compute_metrics, classify_confidence, timestamp_now
from research_engine.v10.dataset import DatasetView, load_trades, enrich_with_decision_trace


_THRESHOLDS = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]


def run(view: DatasetView = DatasetView.FULL, trades: list[dict] | None = None) -> dict[str, Any]:
    """
    Run V10-D3: Decision Threshold Effectiveness.

    Args:
        view: Dataset view
        trades: Pre-loaded trades (optional)

    Returns:
        Structured report dict.
    """
    if trades is not None:
        from research_engine.v10.dataset import _filter_view, _compute_r, _classify_instrument
        for t in trades:
            if not t.get("instrument_class"):
                t["instrument_class"] = _classify_instrument(t.get("symbol", ""))
            if "realised_r" not in t:
                _compute_r(t)
        trades = _filter_view(trades, view)
    else:
        trades = load_trades(view)

    # Enrich with decision trace scores
    enriched_count = enrich_with_decision_trace(trades)

    n_total = len(trades)
    if n_total == 0:
        return _empty_report(view)

    baseline = compute_metrics(trades)

    # Resolve score per trade
    for t in trades:
        t["_score"] = t.get("dt_score") or t.get("score") or 0

    # ─── TEST EACH THRESHOLD ─────────────────────────────────
    threshold_results = []
    for thresh in _THRESHOLDS:
        subset = [t for t in trades if t["_score"] >= thresh]
        n = len(subset)
        if n == 0:
            threshold_results.append({
                "threshold": thresh, "trades": 0, "retained_pct": 0,
                "win_rate": 0, "average_r": 0, "median_r": 0,
                "expectancy_r": 0, "profit_factor": 0, "total_pnl": 0,
                "confidence": "NONE", "ci_lower": 0, "ci_upper": 0,
            })
            continue

        metrics = compute_metrics(subset)
        retained_pct = n / n_total

        # Confidence interval
        r_vals = [t.get("realised_r", 0) for t in subset]
        if n > 1:
            se = statistics.stdev(r_vals) / math.sqrt(n)
            ci_lower = metrics["average_r"] - 1.96 * se
            ci_upper = metrics["average_r"] + 1.96 * se
        else:
            ci_lower = ci_upper = metrics["average_r"]

        threshold_results.append({
            "threshold": thresh,
            "trades": n,
            "retained_pct": round(retained_pct, 4),
            "win_rate": metrics["win_rate"],
            "average_r": metrics["average_r"],
            "median_r": metrics["median_r"],
            "expectancy_r": metrics["expectancy_r"],
            "profit_factor": metrics["profit_factor"],
            "total_pnl": metrics["total_pnl"],
            "largest_winner": metrics["largest_winner"],
            "largest_loser": metrics["largest_loser"],
            "confidence": metrics["confidence"],
            "ci_lower": round(ci_lower, 4),
            "ci_upper": round(ci_upper, 4),
        })

    # Also add baseline (no filter)
    baseline_entry = {
        "threshold": 0.0,
        "trades": n_total,
        "retained_pct": 1.0,
        "win_rate": baseline["win_rate"],
        "average_r": baseline["average_r"],
        "median_r": baseline["median_r"],
        "expectancy_r": baseline["expectancy_r"],
        "profit_factor": baseline["profit_factor"],
        "total_pnl": baseline["total_pnl"],
        "largest_winner": baseline["largest_winner"],
        "largest_loser": baseline["largest_loser"],
        "confidence": baseline["confidence"],
        "ci_lower": 0, "ci_upper": 0,
    }

    # ─── IDENTIFY BEST THRESHOLDS ────────────────────────────
    valid_results = [r for r in threshold_results if r["trades"] >= 10]

    best_expectancy = max(valid_results, key=lambda x: x["expectancy_r"]) if valid_results else None
    best_pf = max(valid_results, key=lambda x: x["profit_factor"] if x["profit_factor"] < 900 else 0) if valid_results else None

    # Highest threshold with statistical reliability (n >= 30)
    reliable = [r for r in threshold_results if r["trades"] >= 30]
    best_reliable = max(reliable, key=lambda x: x["expectancy_r"]) if reliable else None

    # Threshold where sample becomes too small
    too_small_threshold = None
    for r in threshold_results:
        if r["trades"] < 10 and r["threshold"] > 0:
            too_small_threshold = r["threshold"]
            break

    # ─── CONCLUSION ───────────────────────────────────────────
    current_improvement = 0
    if best_expectancy and best_expectancy["expectancy_r"] > baseline["expectancy_r"] + 0.2:
        if best_expectancy["confidence"] in ("HIGH", "MEDIUM"):
            conclusion = "RAISE_THRESHOLD"
            conclusion_reason = (
                f"Threshold {best_expectancy['threshold']:.2f} produces {best_expectancy['expectancy_r']:+.2f}R "
                f"vs baseline {baseline['expectancy_r']:+.2f}R (+{best_expectancy['expectancy_r'] - baseline['expectancy_r']:.2f}R improvement)"
            )
            current_improvement = best_expectancy["expectancy_r"] - baseline["expectancy_r"]
        else:
            conclusion = "RAISE_THRESHOLD"
            conclusion_reason = (
                f"Threshold {best_expectancy['threshold']:.2f} shows improvement but sample is {best_expectancy['confidence']}"
            )
    elif best_expectancy and abs(best_expectancy["expectancy_r"] - baseline["expectancy_r"]) < 0.1:
        conclusion = "KEEP_CURRENT_THRESHOLD"
        conclusion_reason = "No threshold significantly improves over baseline"
    elif not valid_results:
        conclusion = "INCONCLUSIVE"
        conclusion_reason = "No threshold produces enough trades (10+) for reliable comparison"
    else:
        conclusion = "INCONCLUSIVE"
        conclusion_reason = "Mixed results — no clear optimal threshold"

    recommended_threshold = best_expectancy["threshold"] if best_expectancy else 0.0

    report = {
        "research_id": "V10-D3",
        "title": "Decision Threshold Effectiveness",
        "generated_utc": timestamp_now(),
        "dataset_view": view.value,
        "sample_size": n_total,
        "enriched": enriched_count,
        "conclusion": conclusion,
        "conclusion_reason": conclusion_reason,
        "recommended_threshold": recommended_threshold,
        "improvement_r": round(current_improvement, 4),
        "metrics": baseline,
        "baseline": baseline_entry,
        "threshold_results": threshold_results,
        "best_expectancy_threshold": best_expectancy,
        "best_profit_factor_threshold": best_pf,
        "best_reliable_threshold": best_reliable,
        "too_small_threshold": too_small_threshold,
    }

    report["markdown"] = _build_markdown(report)
    return report


def _empty_report(view: DatasetView) -> dict[str, Any]:
    return {
        "research_id": "V10-D3", "title": "Decision Threshold Effectiveness",
        "generated_utc": timestamp_now(), "dataset_view": view.value,
        "sample_size": 0, "conclusion": "NO_DATA",
        "metrics": {"count": 0}, "threshold_results": [],
        "markdown": f"# V10-D3: No data for {view.value}",
    }


def _build_markdown(report: dict) -> str:
    md = []
    md.append(f"# V10-D3: Decision Threshold Effectiveness ({report['dataset_view']})")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append(f"Sample: {report['sample_size']} trades")
    md.append("")
    md.append(f"## Conclusion: {report['conclusion']}")
    md.append("")
    md.append(report["conclusion_reason"])
    if report.get("recommended_threshold"):
        md.append(f"\n**Recommended threshold: {report['recommended_threshold']:.2f}**")
    md.append("")
    md.append("## Threshold Comparison")
    md.append("")
    md.append("| Threshold | Trades | Retained | Win% | Avg R | Expectancy | PF | Conf | 95% CI |")
    md.append("|---|---|---|---|---|---|---|---|---|")

    # Baseline row
    b = report["baseline"]
    md.append(f"| *baseline* | {b['trades']} | 100% | {b['win_rate']:.0%} | "
              f"{b['average_r']:+.2f} | {b['expectancy_r']:+.2f} | {b['profit_factor']:.1f} | {b['confidence']} | — |")

    for r in report["threshold_results"]:
        if r["trades"] == 0:
            continue
        pf = f"{r['profit_factor']:.1f}" if r["profit_factor"] < 900 else "inf"
        marker = " **←**" if r == report.get("best_expectancy_threshold") else ""
        md.append(
            f"| {r['threshold']:.2f} | {r['trades']} | {r['retained_pct']:.0%} | "
            f"{r['win_rate']:.0%} | {r['average_r']:+.2f} | {r['expectancy_r']:+.2f} | "
            f"{pf} | {r['confidence']} | [{r['ci_lower']:+.2f}, {r['ci_upper']:+.2f}] |{marker}"
        )

    md.append("")
    if report.get("too_small_threshold"):
        md.append(f"*Sample becomes too small (< 10 trades) at threshold {report['too_small_threshold']:.2f}*")
    md.append("")
    md.append("---")
    md.append(f"*{report['enriched']}/{report['sample_size']} trades enriched with decision trace scores*")
    return "\n".join(md)
