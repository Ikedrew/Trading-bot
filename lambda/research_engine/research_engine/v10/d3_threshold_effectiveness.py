"""V10-D3: Decision Threshold Effectiveness — Lambda version."""
from __future__ import annotations
import math, statistics
from typing import Any
from research_engine.v10.base import compute_metrics, classify_confidence, timestamp_now
from research_engine.v10.dataset import DatasetView, load_trades

_THRESHOLDS = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]

def run(view: DatasetView = DatasetView.FULL, trades: list[dict] | None = None) -> dict[str, Any]:
    if trades is not None:
        from research_engine.v10.dataset import _filter_view, _compute_r, _classify_instrument
        for t in trades:
            if not t.get("instrument_class"):
                t["instrument_class"] = _classify_instrument(t.get("symbol",""))
            if "realised_r" not in t:
                _compute_r(t)
        trades = _filter_view(trades, view)
    else:
        trades = load_trades(view)

    n_total = len(trades)
    if n_total == 0:
        return {"research_id":"V10-D3","dataset_view":view.value,"sample_size":0,
                "conclusion":"NO_DATA","metrics":{"count":0},"threshold_results":[],
                "markdown":f"# V10-D3: No data for {view.value}"}

    baseline = compute_metrics(trades)
    for t in trades:
        t["_score"] = t.get("dt_score") or t.get("score") or 0

    results = []
    for thresh in _THRESHOLDS:
        subset = [t for t in trades if t["_score"] >= thresh]
        n = len(subset)
        if n == 0:
            results.append({"threshold":thresh,"trades":0,"retained_pct":0,"win_rate":0,
                "expectancy_r":0,"profit_factor":0,"confidence":"NONE"})
            continue
        m = compute_metrics(subset)
        r_vals = [t.get("realised_r",0) for t in subset]
        se = statistics.stdev(r_vals)/math.sqrt(n) if n > 1 else 0
        results.append({
            "threshold":thresh, "trades":n, "retained_pct":round(n/n_total,4),
            "win_rate":m["win_rate"], "average_r":m["average_r"], "median_r":m["median_r"],
            "expectancy_r":m["expectancy_r"], "profit_factor":m["profit_factor"],
            "total_pnl":m["total_pnl"], "confidence":m["confidence"],
            "ci_lower":round(m["average_r"]-1.96*se,4), "ci_upper":round(m["average_r"]+1.96*se,4),
        })

    valid = [r for r in results if r["trades"] >= 10]
    best = max(valid, key=lambda x: x["expectancy_r"]) if valid else None

    if best and best["expectancy_r"] > baseline["expectancy_r"] + 0.2:
        conclusion = "RAISE_THRESHOLD"
        reason = f"Threshold {best['threshold']:.2f} → {best['expectancy_r']:+.2f}R (baseline {baseline['expectancy_r']:+.2f}R)"
    elif best and abs(best["expectancy_r"] - baseline["expectancy_r"]) < 0.1:
        conclusion = "KEEP_CURRENT_THRESHOLD"
        reason = "No threshold significantly improves"
    else:
        conclusion = "INCONCLUSIVE"
        reason = "Mixed results"

    report = {"research_id":"V10-D3","title":"Decision Threshold Effectiveness",
        "generated_utc":timestamp_now(),"dataset_view":view.value,"sample_size":n_total,
        "conclusion":conclusion,"conclusion_reason":reason,
        "recommended_threshold":best["threshold"] if best else 0,
        "metrics":baseline,"threshold_results":results}

    md = [f"# V10-D3: Threshold Effectiveness ({view.value})","",
          f"Sample: {n_total} | Baseline exp: {baseline['expectancy_r']:+.4f}R","",
          f"## {conclusion}","",reason,"",
          "## Thresholds","",
          "| Thresh | N | Ret% | Win% | Exp R | PF | Conf |",
          "|---|---|---|---|---|---|---|"]
    for r in results:
        if r["trades"] == 0: continue
        pf = f"{r['profit_factor']:.1f}" if r["profit_factor"] < 900 else "inf"
        md.append(f"| {r['threshold']:.2f} | {r['trades']} | {r['retained_pct']:.0%} | "
                  f"{r['win_rate']:.0%} | {r['expectancy_r']:+.2f} | {pf} | {r['confidence']} |")
    report["markdown"] = "\n".join(md)
    return report
