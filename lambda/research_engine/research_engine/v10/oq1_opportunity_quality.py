"""V10-OQ1: Opportunity Quality Analysis — Lambda version."""
from __future__ import annotations
import math, statistics
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
        return {"research_id":"V10-OQ1","dataset_view":view.value,"sample_size":0,
                "conclusion":"NO_DATA","metrics":{"count":0},
                "markdown":f"# V10-OQ1: No data for {view.value}"}

    baseline = compute_metrics(trades)
    for t in trades:
        t["_qs"] = t.get("dt_score") or t.get("score") or 0

    # Tercile
    scores = sorted(t["_qs"] for t in trades)
    q33 = scores[len(scores)//3] if len(scores)>=3 else 0.5
    q66 = scores[2*len(scores)//3] if len(scores)>=3 else 0.7

    groups = {"LOW":[t for t in trades if t["_qs"]<q33],
              "MEDIUM":[t for t in trades if q33<=t["_qs"]<q66],
              "HIGH":[t for t in trades if t["_qs"]>=q66]}

    qa = {}
    for level, g in groups.items():
        if not g: continue
        m = compute_metrics(g)
        qa[level] = {"count":m["count"],"win_rate":m["win_rate"],"average_r":m["average_r"],
            "expectancy_r":m["expectancy_r"],"profit_factor":m["profit_factor"],"confidence":m["confidence"]}

    # Components
    comps_data: dict[str,list] = {}
    for t in trades:
        for k,v in (t.get("dt_components") or {}).items():
            if isinstance(v,(int,float)) and v!=0:
                comps_data.setdefault(k,[]).append((v,t.get("realised_r",0)))

    comp_results = {}
    for comp, pairs in sorted(comps_data.items(), key=lambda x:-len(x[1])):
        if len(pairs)<5: continue
        cv=[p[0] for p in pairs]; rv=[p[1] for p in pairs]
        corr = _corr(cv,rv)
        comp_results[comp] = {"sample":len(pairs),"correlation":round(corr,4),"predictive":abs(corr)>0.1}

    # Winner/loser
    winners = [t for t in trades if t.get("realised_r",0)>0]
    losers = [t for t in trades if t.get("realised_r",0)<=0]
    w_score = statistics.mean([t["_qs"] for t in winners]) if winners else 0
    l_score = statistics.mean([t["_qs"] for t in losers]) if losers else 0
    gap = w_score - l_score

    # Conclusion
    high_exp = qa.get("HIGH",{}).get("expectancy_r",0)
    low_exp = qa.get("LOW",{}).get("expectancy_r",0)
    quality_predicts = high_exp > low_exp + 0.15 if "HIGH" in qa and "LOW" in qa else False

    if quality_predicts or gap > 0.02:
        conclusion = "OPPORTUNITY_LAYER_PREDICTIVE"
        reason = f"Higher quality predicts better outcomes (gap={gap:+.4f})"
    elif gap < -0.02:
        conclusion = "OPPORTUNITIES_LOW_QUALITY"
        reason = f"Higher-scored opps perform WORSE (gap={gap:+.4f})"
    else:
        conclusion = "INCONCLUSIVE"
        reason = "No clear quality-outcome relationship"

    report = {"research_id":"V10-OQ1","title":"Opportunity Quality Analysis",
        "generated_utc":timestamp_now(),"dataset_view":view.value,"sample_size":n,
        "conclusion":conclusion,"conclusion_reason":reason,"metrics":baseline,
        "quality_buckets":qa,"component_analysis":comp_results,
        "winner_vs_loser":{"score_gap":round(gap,4),"w_score":round(w_score,4),"l_score":round(l_score,4)}}

    md = [f"# V10-OQ1: Opportunity Quality ({view.value})","",
          f"Sample: {n} | Gap: {gap:+.4f}","",f"## {conclusion}","",reason,"",
          "## Quality Buckets","","| Level | N | Win% | Exp R | PF |","|---|---|---|---|---|"]
    for lv in ["LOW","MEDIUM","HIGH"]:
        if lv in qa:
            s=qa[lv]; pf=f"{s['profit_factor']:.1f}" if s['profit_factor']<900 else "inf"
            md.append(f"| {lv} | {s['count']} | {s['win_rate']:.0%} | {s['expectancy_r']:+.2f} | {pf} |")
    if comp_results:
        md.extend(["","## Components","","| Comp | Corr | Predictive |","|---|---|---|"])
        for c,v in sorted(comp_results.items(),key=lambda x:-abs(x[1]["correlation"])):
            md.append(f"| {c} | {v['correlation']:+.3f} | {'YES' if v['predictive'] else 'no'} |")
    report["markdown"]="\n".join(md)
    return report

def _corr(xs,ys):
    if len(xs)<3: return 0.0
    n=len(xs);mx=sum(xs)/n;my=sum(ys)/n
    cov=sum((x-mx)*(y-my) for x,y in zip(xs,ys))/n
    sx=(sum((x-mx)**2 for x in xs)/n)**0.5;sy=(sum((y-my)**2 for y in ys)/n)**0.5
    return cov/(sx*sy) if sx>0 and sy>0 else 0.0
