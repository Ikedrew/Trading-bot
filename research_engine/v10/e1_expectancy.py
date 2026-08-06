"""
V10-E1: True System Expectancy

Question: "Does the bot have a real trading edge after execution?"

Runs against any dataset view. Computes complete expectancy analysis
with confidence intervals and distribution breakdown.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from research_engine.v10.base import compute_metrics, classify_confidence, timestamp_now
from research_engine.v10.dataset import DatasetView, load_trades


def run(view: DatasetView = DatasetView.FULL, trades: list[dict] | None = None) -> dict[str, Any]:
    """
    Run V10-E1: True System Expectancy.

    Args:
        view: Dataset view to analyse
        trades: Pre-loaded trades (optional, for testing/Lambda)

    Returns:
        Structured report dict ready for JSON serialization.
    """
    if trades is None:
        trades = load_trades(view)

    n = len(trades)
    if n == 0:
        return _empty_report(view)

    # Core metrics
    metrics = compute_metrics(trades)

    # R distribution buckets
    r_vals = [t.get("realised_r", 0) for t in trades]
    buckets = {
        "< -2R": sum(1 for r in r_vals if r < -2),
        "-2R to -1R": sum(1 for r in r_vals if -2 <= r < -1),
        "-1R to 0R": sum(1 for r in r_vals if -1 <= r < 0),
        "0R to 1R": sum(1 for r in r_vals if 0 <= r < 1),
        "1R to 2R": sum(1 for r in r_vals if 1 <= r < 2),
        "2R+": sum(1 for r in r_vals if r >= 2),
    }

    # Win/loss streaks
    max_win_streak, max_loss_streak = _calc_streaks(trades)

    # Confidence interval (95%)
    if n > 1:
        std_r = statistics.stdev(r_vals)
        se_r = std_r / math.sqrt(n)
        ci_lower = metrics["average_r"] - 1.96 * se_r
        ci_upper = metrics["average_r"] + 1.96 * se_r
    else:
        std_r = se_r = 0
        ci_lower = ci_upper = metrics["average_r"]

    # Conclusion
    if n < 30:
        conclusion = "INCONCLUSIVE"
        conclusion_reason = f"Insufficient sample ({n} trades, need 30+)"
    elif ci_lower > 0:
        conclusion = "POSITIVE_EXPECTANCY_SIGNAL"
        conclusion_reason = f"95% CI entirely above zero [{ci_lower:.4f}, {ci_upper:.4f}]"
    elif ci_upper < 0:
        conclusion = "NEGATIVE_EXPECTANCY_SIGNAL"
        conclusion_reason = f"95% CI entirely below zero [{ci_lower:.4f}, {ci_upper:.4f}]"
    else:
        conclusion = "INCONCLUSIVE"
        conclusion_reason = f"95% CI spans zero [{ci_lower:.4f}, {ci_upper:.4f}]"

    # By exit reason
    exit_groups: dict[str, list] = {}
    for t in trades:
        reason = t.get("exit_reason_validated", t.get("close_reason", "UNKNOWN"))
        exit_groups.setdefault(reason, []).append(t)
    exit_analysis = {reason: compute_metrics(group) for reason, group in exit_groups.items()}

    # By pattern
    pattern_groups: dict[str, list] = {}
    for t in trades:
        p = t.get("pattern", "UNKNOWN")
        pattern_groups.setdefault(p, []).append(t)
    pattern_analysis = {
        p: {"count": len(g), "avg_r": round(statistics.mean([t["realised_r"] for t in g]), 4),
            "win_rate": round(sum(1 for t in g if t["realised_r"] > 0) / len(g), 4)}
        for p, g in sorted(pattern_groups.items(), key=lambda x: -len(x[1]))
    }

    # Build report
    report = {
        "research_id": "V10-E1",
        "title": "True System Expectancy",
        "generated_utc": timestamp_now(),
        "dataset_view": view.value,
        "sample_size": n,
        "conclusion": conclusion,
        "conclusion_reason": conclusion_reason,
        "metrics": metrics,
        "confidence_interval": {
            "standard_error": round(se_r, 4),
            "ci_95_lower": round(ci_lower, 4),
            "ci_95_upper": round(ci_upper, 4),
        },
        "distribution": {
            "r_buckets": buckets,
            "longest_win_streak": max_win_streak,
            "longest_loss_streak": max_loss_streak,
        },
        "by_exit_reason": exit_analysis,
        "by_pattern": pattern_analysis,
    }

    # Markdown
    report["markdown"] = _build_markdown(report)
    return report


def _calc_streaks(trades: list[dict]) -> tuple[int, int]:
    max_win = max_loss = current_win = current_loss = 0
    for t in trades:
        if t.get("realised_r", 0) > 0:
            current_win += 1
            current_loss = 0
            max_win = max(max_win, current_win)
        else:
            current_loss += 1
            current_win = 0
            max_loss = max(max_loss, current_loss)
    return max_win, max_loss


def _empty_report(view: DatasetView) -> dict[str, Any]:
    return {
        "research_id": "V10-E1",
        "title": "True System Expectancy",
        "generated_utc": timestamp_now(),
        "dataset_view": view.value,
        "sample_size": 0,
        "conclusion": "NO_DATA",
        "conclusion_reason": f"No trades available for view {view.value}",
        "metrics": {"count": 0},
        "markdown": f"# V10-E1: No data available for {view.value}",
    }


def _build_markdown(report: dict) -> str:
    m = report["metrics"]
    ci = report["confidence_interval"]
    md = []
    md.append(f"# V10-E1: True System Expectancy ({report['dataset_view']})")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append(f"Sample: {report['sample_size']} trades | View: {report['dataset_view']}")
    md.append("")
    md.append(f"## Conclusion: {report['conclusion']}")
    md.append("")
    md.append(f"{report['conclusion_reason']}")
    md.append("")
    md.append("## Metrics")
    md.append("")
    md.append("| Metric | Value |")
    md.append("|---|---|")
    md.append(f"| Trade count | {m['count']} |")
    md.append(f"| Win rate | {m['win_rate']:.1%} |")
    md.append(f"| Average R | {m['average_r']:.4f} |")
    md.append(f"| Expectancy | {m['expectancy_r']:.4f} R/trade |")
    md.append(f"| Profit factor | {m['profit_factor']:.2f} |")
    md.append(f"| Total PnL | ${m['total_pnl']:.2f} |")
    md.append(f"| 95% CI | [{ci['ci_95_lower']:.4f}, {ci['ci_95_upper']:.4f}] |")
    md.append("")
    md.append("## By Pattern")
    md.append("")
    md.append("| Pattern | N | Avg R | Win% |")
    md.append("|---|---|---|---|")
    for p, stats in report["by_pattern"].items():
        md.append(f"| {p} | {stats['count']} | {stats['avg_r']:+.2f} | {stats['win_rate']:.0%} |")
    md.append("")
    md.append("---")
    md.append(f"*Confidence: {m.get('confidence', 'N/A')}*")
    return "\n".join(md)
