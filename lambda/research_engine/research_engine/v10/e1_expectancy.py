"""V10-E1: True System Expectancy — Lambda-compatible version."""
from __future__ import annotations
import math, statistics
from typing import Any
from research_engine.v10.base import compute_metrics, classify_confidence, timestamp_now
from research_engine.v10.dataset import DatasetView, load_trades

def run(view: DatasetView = DatasetView.FULL, trades: list[dict] | None = None) -> dict[str, Any]:
    """Run V10-E1 analysis."""
    if trades is not None:
        # Apply view filter to pre-loaded trades
        from research_engine.v10.dataset import _filter_view, _compute_r
        for t in trades:
            if not t.get("instrument_class"):
                from research_engine.v10.dataset import _classify_instrument
                t["instrument_class"] = _classify_instrument(t.get("symbol", ""))
            if "realised_r" not in t:
                _compute_r(t)
        trades = _filter_view(trades, view)
    else:
        trades = load_trades(view)

    n = len(trades)
    if n == 0:
        return {"research_id": "V10-E1", "dataset_view": view.value, "sample_size": 0,
                "conclusion": "NO_DATA", "metrics": {"count": 0},
                "markdown": f"# V10-E1: No data for {view.value}"}

    metrics = compute_metrics(trades)
    r_vals = [t.get("realised_r", 0) for t in trades]

    # Distribution
    buckets = {
        "< -2R": sum(1 for r in r_vals if r < -2),
        "-2R to -1R": sum(1 for r in r_vals if -2 <= r < -1),
        "-1R to 0R": sum(1 for r in r_vals if -1 <= r < 0),
        "0R to 1R": sum(1 for r in r_vals if 0 <= r < 1),
        "1R to 2R": sum(1 for r in r_vals if 1 <= r < 2),
        "2R+": sum(1 for r in r_vals if r >= 2),
    }

    # CI
    if n > 1:
        std_r = statistics.stdev(r_vals)
        se_r = std_r / math.sqrt(n)
        ci_lower = metrics["average_r"] - 1.96 * se_r
        ci_upper = metrics["average_r"] + 1.96 * se_r
    else:
        se_r = 0; ci_lower = ci_upper = metrics["average_r"]

    # Conclusion
    if n < 30:
        conclusion = "INCONCLUSIVE"; conclusion_reason = f"Insufficient sample ({n} trades)"
    elif ci_lower > 0:
        conclusion = "POSITIVE_EXPECTANCY_SIGNAL"; conclusion_reason = f"95% CI above zero [{ci_lower:.4f}, {ci_upper:.4f}]"
    elif ci_upper < 0:
        conclusion = "NEGATIVE_EXPECTANCY_SIGNAL"; conclusion_reason = f"95% CI below zero [{ci_lower:.4f}, {ci_upper:.4f}]"
    else:
        conclusion = "INCONCLUSIVE"; conclusion_reason = f"95% CI spans zero [{ci_lower:.4f}, {ci_upper:.4f}]"

    # By pattern
    pattern_groups: dict[str, list] = {}
    for t in trades:
        pattern_groups.setdefault(t.get("pattern", "UNKNOWN"), []).append(t)
    pattern_analysis = {
        p: {"count": len(g), "avg_r": round(statistics.mean([t["realised_r"] for t in g]), 4),
            "win_rate": round(sum(1 for t in g if t["realised_r"] > 0) / len(g), 4)}
        for p, g in sorted(pattern_groups.items(), key=lambda x: -len(x[1]))
    }

    report = {
        "research_id": "V10-E1", "title": "True System Expectancy",
        "generated_utc": timestamp_now(), "dataset_view": view.value,
        "sample_size": n, "conclusion": conclusion, "conclusion_reason": conclusion_reason,
        "metrics": metrics,
        "confidence_interval": {"standard_error": round(se_r, 4), "ci_95_lower": round(ci_lower, 4), "ci_95_upper": round(ci_upper, 4)},
        "distribution": {"r_buckets": buckets},
        "by_pattern": pattern_analysis,
    }

    # Markdown
    md = [f"# V10-E1: True System Expectancy ({view.value})", "",
          f"Generated: {report['generated_utc']}", f"Sample: {n} trades", "",
          f"## Conclusion: {conclusion}", "", conclusion_reason, "",
          "## Metrics", "", "| Metric | Value |", "|---|---|",
          f"| Trades | {n} |", f"| Win rate | {metrics['win_rate']:.1%} |",
          f"| Expectancy | {metrics['expectancy_r']:.4f} R |",
          f"| Profit Factor | {metrics['profit_factor']:.2f} |",
          f"| Total PnL | ${metrics['total_pnl']:.2f} |",
          f"| 95% CI | [{ci_lower:.4f}, {ci_upper:.4f}] |", "",
          "## By Pattern", "", "| Pattern | N | Avg R | Win% |", "|---|---|---|---|"]
    for p, s in pattern_analysis.items():
        md.append(f"| {p} | {s['count']} | {s['avg_r']:+.2f} | {s['win_rate']:.0%} |")
    report["markdown"] = "\n".join(md)
    return report
