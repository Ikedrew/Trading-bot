"""
V10-M1: Regime Expectancy

Question: "Does market regime classification predict trade outcomes?"

Groups trades by regime and computes per-regime metrics to determine
whether certain market environments produce better or worse results.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from research_engine.v10.base import compute_metrics, classify_confidence, timestamp_now
from research_engine.v10.dataset import DatasetView, load_trades, enrich_with_decision_trace


def run(view: DatasetView = DatasetView.FULL, trades: list[dict] | None = None) -> dict[str, Any]:
    """
    Run V10-M1: Regime Expectancy.

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

    # Enrich with regime from decision trace if not already present
    trades_needing_regime = [t for t in trades if not t.get("regime") and not t.get("dt_regime")]
    if trades_needing_regime:
        enrich_with_decision_trace(trades)

    n_total = len(trades)
    if n_total == 0:
        return _empty_report(view)

    baseline = compute_metrics(trades)
    baseline_exp = baseline["expectancy_r"]

    # Resolve regime field (prefer dt_regime if available)
    for t in trades:
        if not t.get("_regime_resolved"):
            t["_regime_resolved"] = t.get("dt_regime") or t.get("regime") or "UNKNOWN"

    # Coverage
    trades_with_regime = [t for t in trades if t["_regime_resolved"] and t["_regime_resolved"] != "UNKNOWN"]
    trades_without = [t for t in trades if t["_regime_resolved"] == "UNKNOWN"]
    regime_coverage = len(trades_with_regime) / n_total if n_total > 0 else 0

    # Group by regime
    regime_groups: dict[str, list[dict]] = {}
    for t in trades_with_regime:
        regime_groups.setdefault(t["_regime_resolved"], []).append(t)

    # Compute per-regime metrics
    regime_results = []
    for regime, group in sorted(regime_groups.items(), key=lambda x: -len(x[1])):
        n = len(group)
        metrics = compute_metrics(group)
        vs_baseline = metrics["expectancy_r"] - baseline_exp

        # Confidence interval
        r_vals = [t.get("realised_r", 0) for t in group]
        if n > 1:
            std_r = statistics.stdev(r_vals)
            se_r = std_r / math.sqrt(n)
            ci_lower = metrics["average_r"] - 1.96 * se_r
            ci_upper = metrics["average_r"] + 1.96 * se_r
        else:
            se_r = 0
            ci_lower = ci_upper = metrics["average_r"]

        # Exit breakdown
        sl_count = sum(1 for t in group if t.get("exit_reason_validated", "") == "STOP_LOSS")
        tp_count = sum(1 for t in group if t.get("exit_reason_validated", "") == "TAKE_PROFIT")

        regime_results.append({
            "regime": regime,
            "trade_count": n,
            "pct_of_total": round(n / n_total, 4),
            "confidence": classify_confidence(n),
            "win_rate": metrics["win_rate"],
            "loss_rate": metrics["loss_rate"],
            "average_r": metrics["average_r"],
            "median_r": metrics["median_r"],
            "expectancy_r": metrics["expectancy_r"],
            "vs_baseline": round(vs_baseline, 4),
            "total_pnl": metrics["total_pnl"],
            "average_pnl": metrics["average_pnl"],
            "profit_factor": metrics["profit_factor"],
            "ci_95_lower": round(ci_lower, 4),
            "ci_95_upper": round(ci_upper, 4),
            "sl_exits": sl_count,
            "tp_exits": tp_count,
            "tp_rate": round(tp_count / n, 4) if n > 0 else 0,
        })

    # Conclusion
    if len(regime_results) < 2:
        conclusion = "INCONCLUSIVE"
        conclusion_reason = "Fewer than 2 regimes with data — cannot compare"
    else:
        best = max(regime_results, key=lambda x: x["expectancy_r"])
        worst = min(regime_results, key=lambda x: x["expectancy_r"])
        spread = best["expectancy_r"] - worst["expectancy_r"]

        # Check if any regime has CI entirely above/below zero
        sig_positive = [r for r in regime_results if r["ci_95_lower"] > 0 and r["confidence"] != "LOW"]
        sig_negative = [r for r in regime_results if r["ci_95_upper"] < 0 and r["confidence"] != "LOW"]

        if spread > 0.5 and (sig_positive or sig_negative):
            conclusion = "REGIMES_SHOW_DIFFERENT_EXPECTANCY"
            conclusion_reason = (
                f"Spread of {spread:.2f}R between best ({best['regime']}: {best['expectancy_r']:+.2f}R) "
                f"and worst ({worst['regime']}: {worst['expectancy_r']:+.2f}R)"
            )
        elif spread > 0.3:
            conclusion = "REGIMES_SHOW_DIFFERENT_EXPECTANCY"
            conclusion_reason = (
                f"Meaningful spread ({spread:.2f}R): {best['regime']} outperforms {worst['regime']}"
            )
        elif all(abs(r["expectancy_r"] - baseline_exp) < 0.15 for r in regime_results):
            conclusion = "NO_REGIME_DIFFERENCE"
            conclusion_reason = "All regimes perform within 0.15R of baseline — no meaningful differentiation"
        else:
            conclusion = "INCONCLUSIVE"
            conclusion_reason = f"Some spread ({spread:.2f}R) but insufficient confidence to declare difference"

    report = {
        "research_id": "V10-M1",
        "title": "Regime Expectancy",
        "generated_utc": timestamp_now(),
        "dataset_view": view.value,
        "sample_size": n_total,
        "regime_coverage": round(regime_coverage, 4),
        "trades_with_regime": len(trades_with_regime),
        "trades_without_regime": len(trades_without),
        "baseline_expectancy_r": baseline_exp,
        "conclusion": conclusion,
        "conclusion_reason": conclusion_reason,
        "regimes_analysed": len(regime_results),
        "regime_results": regime_results,
        "metrics": baseline,
    }

    report["markdown"] = _build_markdown(report)
    return report


def _empty_report(view: DatasetView) -> dict[str, Any]:
    return {
        "research_id": "V10-M1", "title": "Regime Expectancy",
        "generated_utc": timestamp_now(), "dataset_view": view.value,
        "sample_size": 0, "conclusion": "NO_DATA",
        "conclusion_reason": f"No trades for view {view.value}",
        "metrics": {"count": 0}, "regime_results": [],
        "markdown": f"# V10-M1: No data for {view.value}",
    }


def _build_markdown(report: dict) -> str:
    md = []
    md.append(f"# V10-M1: Regime Expectancy ({report['dataset_view']})")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append(f"Sample: {report['sample_size']} trades | Regime coverage: {report['regime_coverage']:.0%}")
    md.append(f"Baseline: {report['baseline_expectancy_r']:.4f} R/trade")
    md.append("")
    md.append(f"## Conclusion: {report['conclusion']}")
    md.append("")
    md.append(report["conclusion_reason"])
    md.append("")
    md.append("## Regime Performance")
    md.append("")
    md.append("| Regime | N | Conf | Win% | Avg R | Exp R | vs Base | TP% | PF | 95% CI |")
    md.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in report["regime_results"]:
        sign = "+" if r["vs_baseline"] > 0 else ""
        pf = f"{r['profit_factor']:.1f}" if r["profit_factor"] < 900 else "inf"
        md.append(
            f"| {r['regime']} | {r['trade_count']} | {r['confidence']} | "
            f"{r['win_rate']:.0%} | {r['average_r']:+.2f} | {r['expectancy_r']:+.2f} | "
            f"{sign}{r['vs_baseline']:.2f} | {r['tp_rate']:.0%} | {pf} | "
            f"[{r['ci_95_lower']:+.2f}, {r['ci_95_upper']:+.2f}] |"
        )
    md.append("")
    md.append("## Confidence Notes")
    md.append("")
    for r in report["regime_results"]:
        if r["confidence"] == "LOW":
            md.append(f"- **{r['regime']}** (n={r['trade_count']}): insufficient sample")
    md.append("")
    md.append("---")
    md.append(f"*{report['trades_with_regime']}/{report['sample_size']} trades with regime data*")
    return "\n".join(md)
