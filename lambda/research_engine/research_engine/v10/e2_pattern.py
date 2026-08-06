"""V10-E2: Pattern Expectancy — Lambda-compatible version."""
from __future__ import annotations
import statistics
from typing import Any
from research_engine.v10.base import compute_metrics, classify_confidence, timestamp_now
from research_engine.v10.dataset import DatasetView, load_trades

def run(view: DatasetView = DatasetView.FULL, trades: list[dict] | None = None) -> dict[str, Any]:
    """Run V10-E2: Pattern Expectancy."""
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
        return {"research_id": "V10-E2", "dataset_view": view.value, "sample_size": 0,
                "conclusion": "NO_DATA", "metrics": {"count": 0}, "pattern_results": [],
                "markdown": f"# V10-E2: No data for {view.value}"}

    baseline = compute_metrics(trades)
    baseline_exp = baseline["expectancy_r"]

    pattern_groups: dict[str, list] = {}
    for t in trades:
        pattern_groups.setdefault(t.get("pattern", "UNKNOWN"), []).append(t)

    pattern_results = []
    for pattern, group in sorted(pattern_groups.items(), key=lambda x: -len(x[1])):
        n = len(group)
        metrics = compute_metrics(group)
        vs_baseline = metrics["expectancy_r"] - baseline_exp
        sl_count = sum(1 for t in group if t.get("exit_reason_validated", "") == "STOP_LOSS")
        tp_count = sum(1 for t in group if t.get("exit_reason_validated", "") == "TAKE_PROFIT")
        pattern_results.append({
            "pattern": pattern, "trade_count": n,
            "pct_of_total": round(n / n_total, 4),
            "confidence": classify_confidence(n),
            "win_rate": metrics["win_rate"], "average_r": metrics["average_r"],
            "median_r": metrics["median_r"], "expectancy_r": metrics["expectancy_r"],
            "vs_baseline": round(vs_baseline, 4), "total_pnl": metrics["total_pnl"],
            "profit_factor": metrics["profit_factor"],
            "average_win_r": metrics["average_win_r"], "average_loss_r": metrics["average_loss_r"],
            "sl_exits": sl_count, "tp_exits": tp_count,
            "tp_rate": round(tp_count / n, 4) if n > 0 else 0,
        })

    positive_med = [p for p in pattern_results if p["expectancy_r"] > 0 and p["confidence"] in ("HIGH", "MEDIUM")]
    positive_low = [p for p in pattern_results if p["expectancy_r"] > 0.3 and p["confidence"] == "LOW"]
    if positive_med and any(p["expectancy_r"] > 0.3 for p in positive_med):
        conclusion = "CLEAR_PATTERN_EDGE_IDENTIFIED"
        conclusion_reason = f"{len(positive_med)} pattern(s) with medium+ confidence positive expectancy"
    elif positive_med or positive_low:
        conclusion = "SOME_PATTERNS_SHOW_PROMISE"
        conclusion_reason = f"{len(positive_med)} medium+ positive, {len(positive_low)} promising low-sample"
    elif all(abs(p["expectancy_r"]) < 0.2 for p in pattern_results if p["confidence"] != "LOW"):
        conclusion = "NO_PATTERN_DIFFERENCE_DETECTED"
        conclusion_reason = "No meaningful differences between patterns"
    else:
        conclusion = "INSUFFICIENT_SAMPLE"
        conclusion_reason = "Most patterns have LOW confidence"

    report = {
        "research_id": "V10-E2", "title": "Pattern Expectancy",
        "generated_utc": timestamp_now(), "dataset_view": view.value,
        "sample_size": n_total, "baseline_expectancy_r": baseline_exp,
        "conclusion": conclusion, "conclusion_reason": conclusion_reason,
        "patterns_analysed": len(pattern_results),
        "pattern_results": pattern_results, "metrics": baseline,
    }

    # Markdown
    md = [f"# V10-E2: Pattern Expectancy ({view.value})", "",
          f"Sample: {n_total} | Baseline: {baseline_exp:.4f}R", "",
          f"## Conclusion: {conclusion}", "", conclusion_reason, "",
          "## Patterns", "",
          "| Pattern | N | Conf | Win% | Exp R | vs Base |",
          "|---|---|---|---|---|---|"]
    for p in pattern_results:
        sign = "+" if p["vs_baseline"] > 0 else ""
        md.append(f"| {p['pattern']} | {p['trade_count']} | {p['confidence']} | "
                  f"{p['win_rate']:.0%} | {p['expectancy_r']:+.2f} | {sign}{p['vs_baseline']:.2f} |")
    report["markdown"] = "\n".join(md)
    return report
