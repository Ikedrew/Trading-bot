"""V10-M1: Regime Expectancy — Lambda-compatible version."""
from __future__ import annotations
import math, statistics
from typing import Any
from research_engine.v10.base import compute_metrics, classify_confidence, timestamp_now
from research_engine.v10.dataset import DatasetView, load_trades

def run(view: DatasetView = DatasetView.FULL, trades: list[dict] | None = None) -> dict[str, Any]:
    """Run V10-M1: Regime Expectancy."""
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
        return {"research_id": "V10-M1", "dataset_view": view.value, "sample_size": 0,
                "conclusion": "NO_DATA", "metrics": {"count": 0}, "regime_results": [],
                "markdown": f"# V10-M1: No data for {view.value}"}

    baseline = compute_metrics(trades)
    baseline_exp = baseline["expectancy_r"]

    # Resolve regime
    for t in trades:
        t["_reg"] = t.get("dt_regime") or t.get("regime") or "UNKNOWN"

    with_regime = [t for t in trades if t["_reg"] != "UNKNOWN"]
    coverage = len(with_regime) / n_total if n_total > 0 else 0

    # Group
    groups: dict[str, list] = {}
    for t in with_regime:
        groups.setdefault(t["_reg"], []).append(t)

    regime_results = []
    for regime, group in sorted(groups.items(), key=lambda x: -len(x[1])):
        n = len(group)
        metrics = compute_metrics(group)
        vs_baseline = metrics["expectancy_r"] - baseline_exp
        r_vals = [t.get("realised_r", 0) for t in group]
        if n > 1:
            se = statistics.stdev(r_vals) / math.sqrt(n)
            ci_lo = metrics["average_r"] - 1.96 * se
            ci_hi = metrics["average_r"] + 1.96 * se
        else:
            ci_lo = ci_hi = metrics["average_r"]
        sl_n = sum(1 for t in group if t.get("exit_reason_validated", "") == "STOP_LOSS")
        tp_n = sum(1 for t in group if t.get("exit_reason_validated", "") == "TAKE_PROFIT")
        regime_results.append({
            "regime": regime, "trade_count": n,
            "pct_of_total": round(n / n_total, 4),
            "confidence": classify_confidence(n),
            "win_rate": metrics["win_rate"], "loss_rate": metrics["loss_rate"],
            "average_r": metrics["average_r"], "median_r": metrics["median_r"],
            "expectancy_r": metrics["expectancy_r"],
            "vs_baseline": round(vs_baseline, 4),
            "total_pnl": metrics["total_pnl"], "average_pnl": metrics["average_pnl"],
            "profit_factor": metrics["profit_factor"],
            "ci_95_lower": round(ci_lo, 4), "ci_95_upper": round(ci_hi, 4),
            "sl_exits": sl_n, "tp_exits": tp_n,
            "tp_rate": round(tp_n / n, 4) if n > 0 else 0,
        })

    # Conclusion
    if len(regime_results) < 2:
        conclusion = "INCONCLUSIVE"
        conclusion_reason = "Fewer than 2 regimes"
    else:
        best = max(regime_results, key=lambda x: x["expectancy_r"])
        worst = min(regime_results, key=lambda x: x["expectancy_r"])
        spread = best["expectancy_r"] - worst["expectancy_r"]
        if spread > 0.3:
            conclusion = "REGIMES_SHOW_DIFFERENT_EXPECTANCY"
            conclusion_reason = f"Spread {spread:.2f}R: {best['regime']} ({best['expectancy_r']:+.2f}) vs {worst['regime']} ({worst['expectancy_r']:+.2f})"
        elif all(abs(r["expectancy_r"] - baseline_exp) < 0.15 for r in regime_results):
            conclusion = "NO_REGIME_DIFFERENCE"
            conclusion_reason = "All regimes within 0.15R of baseline"
        else:
            conclusion = "INCONCLUSIVE"
            conclusion_reason = f"Some spread ({spread:.2f}R) but insufficient confidence"

    report = {
        "research_id": "V10-M1", "title": "Regime Expectancy",
        "generated_utc": timestamp_now(), "dataset_view": view.value,
        "sample_size": n_total, "regime_coverage": round(coverage, 4),
        "trades_with_regime": len(with_regime),
        "baseline_expectancy_r": baseline_exp,
        "conclusion": conclusion, "conclusion_reason": conclusion_reason,
        "regimes_analysed": len(regime_results),
        "regime_results": regime_results, "metrics": baseline,
    }

    # Markdown
    md = [f"# V10-M1: Regime Expectancy ({view.value})", "",
          f"Sample: {n_total} | Coverage: {coverage:.0%} | Baseline: {baseline_exp:.4f}R", "",
          f"## Conclusion: {conclusion}", "", conclusion_reason, "",
          "## Regimes", "",
          "| Regime | N | Conf | Win% | Exp R | vs Base | 95% CI |",
          "|---|---|---|---|---|---|---|"]
    for r in regime_results:
        sign = "+" if r["vs_baseline"] > 0 else ""
        md.append(f"| {r['regime']} | {r['trade_count']} | {r['confidence']} | "
                  f"{r['win_rate']:.0%} | {r['expectancy_r']:+.2f} | {sign}{r['vs_baseline']:.2f} | "
                  f"[{r['ci_95_lower']:+.2f}, {r['ci_95_upper']:+.2f}] |")
    report["markdown"] = "\n".join(md)
    return report
