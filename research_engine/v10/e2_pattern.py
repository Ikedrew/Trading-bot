"""
V10-E2: Pattern Expectancy

Question: "Which trade patterns produce meaningful expectancy?"

Groups trades by pattern and computes per-pattern metrics with
confidence classification based on sample size.
"""

from __future__ import annotations

import statistics
from typing import Any

from research_engine.v10.base import compute_metrics, classify_confidence, timestamp_now
from research_engine.v10.dataset import DatasetView, load_trades


def run(view: DatasetView = DatasetView.FULL, trades: list[dict] | None = None) -> dict[str, Any]:
    """
    Run V10-E2: Pattern Expectancy.

    Args:
        view: Dataset view to analyse
        trades: Pre-loaded trades (optional, for Lambda)

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

    n_total = len(trades)
    if n_total == 0:
        return _empty_report(view)

    baseline = compute_metrics(trades)
    baseline_exp = baseline["expectancy_r"]

    # Group by pattern
    pattern_groups: dict[str, list[dict]] = {}
    for t in trades:
        p = t.get("pattern", "UNKNOWN")
        pattern_groups.setdefault(p, []).append(t)

    # Compute per-pattern metrics
    pattern_results = []
    for pattern, group in sorted(pattern_groups.items(), key=lambda x: -len(x[1])):
        n = len(group)
        metrics = compute_metrics(group)
        vs_baseline = metrics["expectancy_r"] - baseline_exp

        # Exit breakdown
        sl_count = sum(1 for t in group if t.get("exit_reason_validated", "") == "STOP_LOSS")
        tp_count = sum(1 for t in group if t.get("exit_reason_validated", "") == "TAKE_PROFIT")

        pattern_results.append({
            "pattern": pattern,
            "trade_count": n,
            "pct_of_total": round(n / n_total, 4),
            "confidence": classify_confidence(n),
            "win_rate": metrics["win_rate"],
            "average_r": metrics["average_r"],
            "median_r": metrics["median_r"],
            "expectancy_r": metrics["expectancy_r"],
            "vs_baseline": round(vs_baseline, 4),
            "total_pnl": metrics["total_pnl"],
            "profit_factor": metrics["profit_factor"],
            "average_win_r": metrics["average_win_r"],
            "average_loss_r": metrics["average_loss_r"],
            "sl_exits": sl_count,
            "tp_exits": tp_count,
            "tp_rate": round(tp_count / n, 4) if n > 0 else 0,
        })

    # Conclusion
    positive_med_high = [p for p in pattern_results if p["expectancy_r"] > 0 and p["confidence"] in ("HIGH", "MEDIUM")]
    positive_low = [p for p in pattern_results if p["expectancy_r"] > 0.3 and p["confidence"] == "LOW"]

    if positive_med_high and any(p["expectancy_r"] > 0.3 for p in positive_med_high):
        conclusion = "CLEAR_PATTERN_EDGE_IDENTIFIED"
        conclusion_reason = f"{len(positive_med_high)} pattern(s) show positive expectancy with medium+ confidence"
    elif positive_med_high or positive_low:
        conclusion = "SOME_PATTERNS_SHOW_PROMISE"
        conclusion_reason = f"{len(positive_med_high)} medium+ confidence positive, {len(positive_low)} promising but low-sample"
    elif all(abs(p["expectancy_r"]) < 0.2 for p in pattern_results if p["confidence"] != "LOW"):
        conclusion = "NO_PATTERN_DIFFERENCE_DETECTED"
        conclusion_reason = "No meaningful expectancy differences between patterns"
    else:
        conclusion = "INSUFFICIENT_SAMPLE"
        conclusion_reason = "Most patterns have LOW confidence — need more trades per pattern"

    report = {
        "research_id": "V10-E2",
        "title": "Pattern Expectancy",
        "generated_utc": timestamp_now(),
        "dataset_view": view.value,
        "sample_size": n_total,
        "baseline_expectancy_r": baseline_exp,
        "conclusion": conclusion,
        "conclusion_reason": conclusion_reason,
        "patterns_analysed": len(pattern_results),
        "pattern_results": pattern_results,
        "metrics": baseline,
        "summary": {
            "positive_expectancy": [p["pattern"] for p in pattern_results if p["expectancy_r"] > 0],
            "negative_expectancy": [p["pattern"] for p in pattern_results if p["expectancy_r"] < 0],
            "high_confidence": [p["pattern"] for p in pattern_results if p["confidence"] == "HIGH"],
            "medium_confidence": [p["pattern"] for p in pattern_results if p["confidence"] == "MEDIUM"],
            "low_confidence": [p["pattern"] for p in pattern_results if p["confidence"] == "LOW"],
        },
    }

    report["markdown"] = _build_markdown(report)
    return report


def _empty_report(view: DatasetView) -> dict[str, Any]:
    return {
        "research_id": "V10-E2", "title": "Pattern Expectancy",
        "generated_utc": timestamp_now(), "dataset_view": view.value,
        "sample_size": 0, "conclusion": "NO_DATA",
        "conclusion_reason": f"No trades available for view {view.value}",
        "metrics": {"count": 0}, "pattern_results": [],
        "markdown": f"# V10-E2: No data for {view.value}",
    }


def _build_markdown(report: dict) -> str:
    md = []
    md.append(f"# V10-E2: Pattern Expectancy ({report['dataset_view']})")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append(f"Sample: {report['sample_size']} trades | Baseline: {report['baseline_expectancy_r']:.4f} R/trade")
    md.append("")
    md.append(f"## Conclusion: {report['conclusion']}")
    md.append("")
    md.append(report["conclusion_reason"])
    md.append("")
    md.append("## Pattern Performance")
    md.append("")
    md.append("| Pattern | N | Conf | Win% | Avg R | Exp R | vs Base | TP% | PF |")
    md.append("|---|---|---|---|---|---|---|---|---|")
    for p in report["pattern_results"]:
        sign = "+" if p["vs_baseline"] > 0 else ""
        pf = f"{p['profit_factor']:.1f}" if p["profit_factor"] < 900 else "inf"
        md.append(
            f"| {p['pattern']} | {p['trade_count']} | {p['confidence']} | "
            f"{p['win_rate']:.0%} | {p['average_r']:+.2f} | {p['expectancy_r']:+.2f} | "
            f"{sign}{p['vs_baseline']:.2f} | {p['tp_rate']:.0%} | {pf} |"
        )
    md.append("")
    md.append("## Confidence Notes")
    md.append("")
    for p in report["pattern_results"]:
        if p["confidence"] == "LOW":
            md.append(f"- **{p['pattern']}** (n={p['trade_count']}): insufficient sample — cannot assess")
    md.append("")
    md.append("---")
    md.append(f"*Patterns with HIGH/MEDIUM confidence are statistically more reliable.*")
    return "\n".join(md)
