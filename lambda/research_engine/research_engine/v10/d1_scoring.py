"""V10-D1: Scoring Components Predictive Power — Lambda version.
Note: For Lambda, trades must be pre-enriched with dt_score/dt_components
since decision_trace files are not loaded from S3 in this version.
The local enrich_with_decision_trace() handles this for local runs."""
from __future__ import annotations
import statistics
from typing import Any
from research_engine.v10.base import compute_metrics, classify_confidence, timestamp_now
from research_engine.v10.dataset import DatasetView, load_trades

_LEGACY = frozenset({"bias_alignment","bias_stability","chop_clarity","confirmation_pre",
    "h4_alignment","htf_alignment","market_quality","pattern_quality","trend_alignment","volatility_quality"})
_V10 = frozenset({"location_score","structure_score","behaviour_score","formation_score"})

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
        return {"research_id":"V10-D1","dataset_view":view.value,"sample_size":0,
                "conclusion":"NO_DATA","metrics":{"count":0},"markdown":f"# V10-D1: No data for {view.value}"}

    # Note: In Lambda, dt_score/dt_components should already be in the dataset
    # (pre-enriched). If not, they'll be 0/{} and the analysis will reflect that.
    baseline = compute_metrics(trades)

    # Score buckets
    buckets = [("0.0-0.3",0,0.3),("0.3-0.5",0.3,0.5),("0.5-0.6",0.5,0.6),("0.6-0.7",0.6,0.7),("0.7-1.0",0.7,1.0)]
    score_analysis = {}
    for label, lo, hi in buckets:
        g = [t for t in trades if lo <= (t.get("dt_score") or t.get("score") or 0) < hi]
        if g:
            m = compute_metrics(g)
            score_analysis[label] = {"count":m["count"],"win_rate":m["win_rate"],"average_r":m["average_r"],
                "expectancy_r":m["expectancy_r"],"profit_factor":m["profit_factor"],"confidence":m["confidence"]}

    # Calibration
    hi_trades = [t for t in trades if (t.get("dt_score") or t.get("score") or 0) >= 0.6]
    lo_trades = [t for t in trades if (t.get("dt_score") or t.get("score") or 0) < 0.4]
    hi_r = statistics.mean([t.get("realised_r",0) for t in hi_trades]) if hi_trades else 0
    lo_r = statistics.mean([t.get("realised_r",0) for t in lo_trades]) if lo_trades else 0
    gap = hi_r - lo_r

    # Components
    all_comps: dict[str, list[tuple[float,float]]] = {}
    for t in trades:
        comps = t.get("dt_components") or {}
        r = t.get("realised_r", 0)
        for k, v in comps.items():
            if isinstance(v, (int,float)) and v != 0:
                all_comps.setdefault(k, []).append((v, r))

    def analyse_comp(pairs):
        if len(pairs) < 5: return None
        cv = [p[0] for p in pairs]; rv = [p[1] for p in pairs]
        corr = _corr(cv, rv)
        sp = sorted(pairs, key=lambda x: x[0]); n_c=len(sp); th=n_c//3
        lo_a = statistics.mean([r for _,r in sp[:th]]) if th>0 else 0
        hi_a = statistics.mean([r for _,r in sp[2*th:]]) if n_c-2*th>0 else 0
        spread = hi_a - lo_a
        return {"sample_size":len(pairs),"correlation":round(corr,4),
                "low_tercile_r":round(lo_a,4),"high_tercile_r":round(hi_a,4),
                "spread":round(spread,4),
                "signal":"positive" if spread>0.2 else ("negative" if spread<-0.2 else "neutral")}

    legacy_a = {}; v10_a = {}
    for comp, pairs in sorted(all_comps.items(), key=lambda x:-len(x[1])):
        result = analyse_comp(pairs)
        if result is None: continue
        if comp in _LEGACY: legacy_a[comp] = result
        elif comp in _V10: v10_a[comp] = result
        else: legacy_a[comp] = result

    # Conclusion
    strong = sum(1 for v in legacy_a.values() if abs(v["correlation"]) > 0.1)
    if gap > 0.3 or strong >= 3:
        conclusion = "SCORE_IS_PREDICTIVE"
        reason = f"Calibration gap={gap:.2f}R, {strong} components with |corr|>0.1"
    elif all(abs(v["correlation"]) < 0.05 for v in legacy_a.values()) if legacy_a else True:
        conclusion = "SCORE_HAS_NO_PREDICTIVE_POWER"
        reason = "No component correlates with outcomes"
    else:
        conclusion = "INCONCLUSIVE"
        reason = "Mixed signals"

    report = {"research_id":"V10-D1","title":"Scoring Components Predictive Power",
        "generated_utc":timestamp_now(),"dataset_view":view.value,"sample_size":n,
        "conclusion":conclusion,"conclusion_reason":reason,"metrics":baseline,
        "score_analysis":score_analysis,
        "calibration":{"gap":round(gap,4),"high_r":round(hi_r,4),"low_r":round(lo_r,4)},
        "legacy_components":dict(sorted(legacy_a.items(),key=lambda x:-abs(x[1]["correlation"]))),
        "v10_components":dict(sorted(v10_a.items(),key=lambda x:-abs(x[1]["correlation"])))}

    md = [f"# V10-D1: Scoring Predictive Power ({view.value})","",
          f"Sample: {n} | Gap: {gap:.2f}R","",f"## {conclusion}","",reason,"",
          "## Score Buckets","","| Range | N | Win% | Exp R |","|---|---|---|---|"]
    for l, s in score_analysis.items():
        md.append(f"| {l} | {s['count']} | {s['win_rate']:.0%} | {s['expectancy_r']:+.2f} |")
    if legacy_a:
        md.extend(["","## Components","","| Component | Corr | Spread | Signal |","|---|---|---|---|"])
        for c, v in sorted(legacy_a.items(), key=lambda x:-abs(x[1]["correlation"])):
            md.append(f"| {c} | {v['correlation']:+.3f} | {v['spread']:+.2f} | {v['signal']} |")
    report["markdown"] = "\n".join(md)
    return report

def _corr(xs, ys):
    if len(xs)<3: return 0.0
    n=len(xs); mx=sum(xs)/n; my=sum(ys)/n
    cov=sum((x-mx)*(y-my) for x,y in zip(xs,ys))/n
    sx=(sum((x-mx)**2 for x in xs)/n)**0.5; sy=(sum((y-my)**2 for y in ys)/n)**0.5
    return cov/(sx*sy) if sx>0 and sy>0 else 0.0
