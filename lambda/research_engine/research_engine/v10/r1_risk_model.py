"""V10-R1: Risk Model Effectiveness — Lambda version."""
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
        return {"research_id":"V10-R1","dataset_view":view.value,"sample_size":0,
                "conclusion":"NO_DATA","metrics":{"count":0},
                "markdown":f"# V10-R1: No data for {view.value}"}

    for t in trades:
        e=t.get("entry_price",0); s=t.get("stop_loss",0); tp=t.get("take_profit",0)
        t["_rd"]=abs(e-s) if e>0 and s>0 else 0
        t["_rwd"]=abs(tp-e) if e>0 and tp>0 else 0
        t["_rr"]=round(t["_rwd"]/t["_rd"],2) if t["_rd"]>0 else 0
        t["_sp"]=round(100*t["_rd"]/e,4) if e>0 else 0

    baseline = compute_metrics(trades)
    winners=[t for t in trades if t.get("realised_r",0)>0]
    losers=[t for t in trades if t.get("realised_r",0)<=0]
    sl_ex=[t for t in trades if t.get("exit_reason_validated","")=="STOP_LOSS"]
    tp_ex=[t for t in trades if t.get("exit_reason_validated","")=="TAKE_PROFIT"]

    # RR buckets
    rr_a={}
    for lb,lo,hi in [("0-1R",0,1),("1-2R",1,2),("2-3R",2,3),("3-5R",3,5),("5R+",5,999)]:
        g=[t for t in trades if lo<=t["_rr"]<hi]
        if g:
            m=compute_metrics(g)
            rr_a[lb]={"count":m["count"],"win_rate":m["win_rate"],"average_r":m["average_r"],
                "expectancy_r":m["expectancy_r"],"profit_factor":m["profit_factor"]}

    # Stop classification
    sc={"reasonable":0,"too_tight":0,"too_wide":0}
    for t in sl_ex:
        r=t.get("realised_r",0); d=t.get("duration_seconds",0)
        if r>-0.5: sc["too_tight"]+=1
        elif d>7200 and r<-0.8: sc["too_wide"]+=1
        else: sc["reasonable"]+=1
    n_sl=max(len(sl_ex),1)
    sl_rate=len(sl_ex)/n

    # Target
    wr=[t.get("realised_r",0) for t in winners]
    ta={"avg_winner_r":round(statistics.mean(wr),4) if wr else 0,
        "tp_hit_rate":round(len(tp_ex)/n,4),
        "avg_tp_r":round(statistics.mean([t.get("realised_r",0) for t in tp_ex]),4) if tp_ex else 0}

    # Loss concentration
    sl_sorted=sorted(losers,key=lambda t:t.get("final_pnl",0))
    t5=sl_sorted[:5]
    tl=sum(t.get("final_pnl",0) for t in losers)
    t5p=sum(t.get("final_pnl",0) for t in t5)

    # Conclusion
    tight_pct=sc["too_tight"]/n_sl
    if sl_rate>0.75 and tight_pct>0.25:
        conclusion="STOPS_NEED_REVIEW"; reason=f"SL={sl_rate:.0%}, {tight_pct:.0%} too tight"
    elif sl_rate>0.75:
        conclusion="STOPS_NEED_REVIEW"; reason=f"SL rate {sl_rate:.0%}"
    elif ta["tp_hit_rate"]<0.15:
        conclusion="TARGETS_TOO_AMBITIOUS"; reason=f"TP rate {ta['tp_hit_rate']:.0%}"
    elif sl_rate<0.65:
        conclusion="RISK_MODEL_EFFECTIVE"; reason="Balanced SL/TP rates"
    else:
        conclusion="INCONCLUSIVE"; reason="Mixed"

    report={"research_id":"V10-R1","title":"Risk Model Effectiveness",
        "generated_utc":timestamp_now(),"dataset_view":view.value,"sample_size":n,
        "conclusion":conclusion,"conclusion_reason":reason,"metrics":baseline,
        "rr_analysis":rr_a,
        "stop_analysis":{"sl_rate":round(sl_rate,4),"tp_rate":round(len(tp_ex)/n,4),
            "classification":sc,"tight_pct":round(tight_pct,4)},
        "target_analysis":ta,
        "loss_analysis":{"total_loss":round(tl,2),"top5_loss":round(t5p,2),
            "top5_pct":round(t5p/tl,4) if tl!=0 else 0}}

    md=[f"# V10-R1: Risk Model ({view.value})","",f"n={n} | {conclusion}","",reason,"",
        "## R:R Buckets","","| Bucket | N | Win% | Exp R |","|---|---|---|---|"]
    for lb,s in rr_a.items():
        md.append(f"| {lb} | {s['count']} | {s['win_rate']:.0%} | {s['expectancy_r']:+.2f} |")
    md.extend(["",f"## Stop: SL={sl_rate:.0%} tight={sc['too_tight']} reasonable={sc['reasonable']}",
               f"## Target: TP rate={ta['tp_hit_rate']:.0%} avg winner={ta['avg_winner_r']:+.2f}R",
               f"## Loss: top5=${t5p:.2f} ({t5p/tl:.0%} of total)" if tl!=0 else ""])
    report["markdown"]="\n".join(md)
    return report
