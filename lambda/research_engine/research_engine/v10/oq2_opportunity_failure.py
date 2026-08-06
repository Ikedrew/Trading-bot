"""V10-OQ2: Opportunity vs Outcome Failure — Lambda version."""
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
        return {"research_id":"V10-OQ2","dataset_view":view.value,"sample_size":0,
                "conclusion":"NO_DATA","metrics":{"count":0},
                "markdown":f"# V10-OQ2: No data for {view.value}"}

    baseline = compute_metrics(trades)
    for t in trades:
        t["_s"] = t.get("dt_score") or t.get("score") or 0

    winners = [t for t in trades if t.get("realised_r",0)>0]
    losers = [t for t in trades if t.get("realised_r",0)<=0]
    n_l = max(len(losers),1)

    # Quality terciles
    sc = sorted(t["_s"] for t in trades)
    q33 = sc[len(sc)//3] if len(sc)>=3 else 0.5
    q66 = sc[2*len(sc)//3] if len(sc)>=3 else 0.7

    qa = {}
    for lv, g in [("LOW",[t for t in trades if t["_s"]<q33]),
                  ("MEDIUM",[t for t in trades if q33<=t["_s"]<q66]),
                  ("HIGH",[t for t in trades if t["_s"]>=q66])]:
        if g:
            m = compute_metrics(g)
            qa[lv] = {"count":m["count"],"win_rate":m["win_rate"],"expectancy_r":m["expectancy_r"]}

    # Failure classification
    fs = {"OPPORTUNITY_FAILURE":0,"DECISION_FAILURE":0,"ENTRY_FAILURE":0,"RISK_FAILURE":0}
    for t in losers:
        s=t["_s"]; r=t.get("realised_r",0); d=t.get("duration_seconds",0)
        if s < q33: fs["OPPORTUNITY_FAILURE"] += 1
        elif d < 300 and r <= -0.8: fs["ENTRY_FAILURE"] += 1
        elif r > -0.6: fs["RISK_FAILURE"] += 1
        else: fs["DECISION_FAILURE"] += 1

    fd = {k:{"count":v,"pct":round(v/n_l,4)} for k,v in fs.items()}

    # Timing
    tc = {"IMMEDIATE":0,"DELAYED":0,"NORMAL":0}
    for t in losers:
        d=t.get("duration_seconds",0); r=t.get("realised_r",0)
        if d<300: tc["IMMEDIATE"]+=1
        elif r>-0.5: tc["DELAYED"]+=1
        else: tc["NORMAL"]+=1

    # Score comparison
    w_s = statistics.mean([t["_s"] for t in winners]) if winners else 0
    l_s = statistics.mean([t["_s"] for t in losers]) if losers else 0

    # Conclusion
    dom = max(fs.items(), key=lambda x:x[1])
    dp = dom[1]/n_l
    if dom[0]=="OPPORTUNITY_FAILURE" and dp>0.35:
        conclusion="OPPORTUNITY_SELECTION_FAILURE"; reason=f"{dp:.0%} from low quality"
    elif dom[0]=="RISK_FAILURE" and dp>0.30:
        conclusion="RISK_LAYER_FAILURE"; reason=f"{dp:.0%} stops too tight"
    elif dom[0]=="ENTRY_FAILURE" and dp>0.25:
        conclusion="ENTRY_TIMING_FAILURE"; reason=f"{dp:.0%} immediate failures"
    elif dom[0]=="DECISION_FAILURE" and dp>0.35:
        conclusion="DECISION_LAYER_FAILURE"; reason=f"{dp:.0%} good opps still lose"
    else:
        conclusion="MIXED_FAILURE"; reason=f"No dominant: {dict(fs)}"

    report = {"research_id":"V10-OQ2","title":"Opportunity vs Outcome Failure",
        "generated_utc":timestamp_now(),"dataset_view":view.value,"sample_size":n,
        "winners":len(winners),"losers":len(losers),
        "conclusion":conclusion,"conclusion_reason":reason,"metrics":baseline,
        "quality_analysis":qa,"failure_distribution":fd,
        "timing_analysis":{k:{"count":v,"pct":round(v/n_l,4)} for k,v in tc.items()},
        "score_comparison":{"winner":round(w_s,4),"loser":round(l_s,4),"gap":round(w_s-l_s,4)}}

    md = [f"# V10-OQ2: Failure Analysis ({view.value})","",
          f"n={n} | {len(winners)}W {len(losers)}L","",f"## {conclusion}","",reason,"",
          "## Failure Stages","","| Stage | Count | % |","|---|---|---|"]
    for k,v in fd.items():
        md.append(f"| {k} | {v['count']} | {v['pct']:.0%} |")
    md.extend(["",f"## Score gap: winners={w_s:.4f} losers={l_s:.4f} gap={w_s-l_s:+.4f}"])
    report["markdown"]="\n".join(md)
    return report
