"""V10-D2: EV Calibration — Lambda version."""
from __future__ import annotations
import statistics
from typing import Any
from research_engine.v10.base import compute_metrics, classify_confidence, timestamp_now
from research_engine.v10.dataset import DatasetView, load_trades

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

    n = len(trades)
    if n == 0:
        return {"research_id":"V10-D2","dataset_view":view.value,"sample_size":0,
                "conclusion":"NO_DATA","metrics":{"count":0},"markdown":f"# V10-D2: No data for {view.value}"}

    baseline = compute_metrics(trades)
    # Use dt_ev/dt_p_success if present (pre-enriched for Lambda)
    ev_trades = [t for t in trades if (t.get("dt_ev") or 0) != 0 or (t.get("dt_p_success") or 0) > 0]
    if not ev_trades:
        # Fallback: try ev/p_success directly
        ev_trades = [t for t in trades if (t.get("ev") or 0) != 0]

    if not ev_trades:
        return {"research_id":"V10-D2","dataset_view":view.value,"sample_size":n,
                "conclusion":"INCONCLUSIVE","conclusion_reason":"No EV data available in trades",
                "metrics":baseline,"markdown":"# V10-D2: No EV data available"}

    # Probability calibration
    p_buckets = [("0.0-0.2",0,0.2),("0.2-0.3",0.2,0.3),("0.3-0.4",0.3,0.4),("0.4-1.0",0.4,1.0)]
    prob_cal = {}
    for label, lo, hi in p_buckets:
        g = [t for t in ev_trades if lo <= (t.get("dt_p_success") or t.get("p_success") or 0) < hi]
        if not g: continue
        actual = sum(1 for t in g if t.get("realised_r",0)>0)/len(g)
        pred = statistics.mean([t.get("dt_p_success") or t.get("p_success") or 0 for t in g])
        err = actual - pred
        prob_cal[label] = {"count":len(g),"predicted":round(pred,4),"actual":round(actual,4),
            "error":round(err,4),"direction":"underconfident" if err>0.05 else ("overconfident" if err<-0.05 else "calibrated")}

    # EV gate
    pos_ev = [t for t in ev_trades if (t.get("dt_ev") or t.get("ev") or 0)>0]
    neg_ev = [t for t in ev_trades if (t.get("dt_ev") or t.get("ev") or 0)<0]
    pos_r = statistics.mean([t.get("realised_r",0) for t in pos_ev]) if pos_ev else 0
    neg_r = statistics.mean([t.get("realised_r",0) for t in neg_ev]) if neg_ev else 0
    gap = pos_r - neg_r

    # Conclusion
    under = sum(1 for v in prob_cal.values() if v["direction"]=="underconfident")
    over = sum(1 for v in prob_cal.values() if v["direction"]=="overconfident")
    if gap > 0.3 and under <= 1:
        conclusion = "EV_CALIBRATED"; reason = f"EV gap {gap:.2f}R, probability reasonably calibrated"
    elif under >= 2:
        conclusion = "EV_UNDERCONFIDENT"; reason = f"{under} buckets underconfident (actual > predicted)"
    elif over >= 2:
        conclusion = "EV_OVERCONFIDENT"; reason = f"{over} buckets overconfident"
    else:
        conclusion = "INCONCLUSIVE"; reason = "Mixed calibration signals"

    report = {"research_id":"V10-D2","title":"EV Calibration","generated_utc":timestamp_now(),
        "dataset_view":view.value,"sample_size":n,"ev_trades":len(ev_trades),
        "conclusion":conclusion,"conclusion_reason":reason,"metrics":baseline,
        "probability_calibration":prob_cal,
        "ev_gate":{"positive_ev":len(pos_ev),"negative_ev":len(neg_ev),
                   "pos_avg_r":round(pos_r,4),"neg_avg_r":round(neg_r,4),"gap":round(gap,4)}}

    md = [f"# V10-D2: EV Calibration ({view.value})","",
          f"Sample: {n} | EV data: {len(ev_trades)}","",f"## {conclusion}","",reason,"",
          "## Probability Calibration","","| Bucket | N | Predicted | Actual | Error | Dir |","|---|---|---|---|---|---|"]
    for l,s in prob_cal.items():
        md.append(f"| {l} | {s['count']} | {s['predicted']:.1%} | {s['actual']:.1%} | {s['error']:+.1%} | {s['direction']} |")
    md.extend(["",f"## EV Gate: pos={len(pos_ev)} ({pos_r:+.2f}R) neg={len(neg_ev)} ({neg_r:+.2f}R) gap={gap:+.2f}R"])
    report["markdown"] = "\n".join(md)
    return report
