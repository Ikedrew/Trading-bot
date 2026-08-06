"""V10-R2: Stop Placement Effectiveness — Lambda version."""
from __future__ import annotations
import statistics
from typing import Any
from research_engine.v10.base import compute_metrics, classify_confidence, timestamp_now
from research_engine.v10.dataset import DatasetView, load_trades

_ATR={"EURUSD":4,"GBPUSD":5.5,"AUDUSD":3.5,"NZDUSD":3.5,"USDCAD":4,"USDCHF":4,"USDJPY":5,"US500":5,"NAS100":20}

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
        return {"research_id":"V10-R2","dataset_view":view.value,"sample_size":0,
                "conclusion":"NO_DATA","metrics":{"count":0},"markdown":f"# V10-R2: No data for {view.value}"}

    from research_engine.v10.dataset import _classify_instrument
    for t in trades:
        e=t.get("entry_price",0); s=t.get("stop_loss",0); sym=t.get("symbol","")
        t["_sd"]=abs(e-s) if e>0 and s>0 else 0
        inst=t.get("instrument_class","") or _classify_instrument(sym)
        pip=0.01 if inst=="FX_JPY" else (0.0001 if "FX" in inst else 0.1)
        t["_sp"]=round(t["_sd"]/pip,1) if pip>0 else 0

    baseline = compute_metrics(trades)
    sl_ex=[t for t in trades if t.get("exit_reason_validated","")=="STOP_LOSS"]
    tp_ex=[t for t in trades if t.get("exit_reason_validated","")=="TAKE_PROFIT"]
    winners=[t for t in trades if t.get("realised_r",0)>0]
    losers=[t for t in trades if t.get("realised_r",0)<=0]
    n_sl=max(len(sl_ex),1)

    pips=[t["_sp"] for t in trades if t["_sp"]>0]
    cs={"mean":round(statistics.mean(pips),1) if pips else 0,
        "median":round(statistics.median(pips),1) if pips else 0,
        "sl_rate":round(len(sl_ex)/n,4),"tp_rate":round(len(tp_ex)/n,4),
        "w_stop":round(statistics.mean([t["_sp"] for t in winners if t["_sp"]>0]),1) if winners else 0,
        "l_stop":round(statistics.mean([t["_sp"] for t in losers if t["_sp"]>0]),1) if losers else 0}

    # Tercile
    sp=sorted([t for t in trades if t["_sp"]>0],key=lambda t:t["_sp"])
    th=len(sp)//3
    sb={}
    for lb,g in [("TIGHT",sp[:th]),("MEDIUM",sp[th:2*th]),("WIDE",sp[2*th:])]:
        if g:
            m=compute_metrics(g)
            sb[lb]={"count":m["count"],"win_rate":m["win_rate"],"expectancy_r":m["expectancy_r"]}

    # Classification
    sc={"TOO_TIGHT":0,"REASONABLE":0,"TOO_WIDE":0}
    for t in sl_ex:
        r=t.get("realised_r",0); d=t.get("duration_seconds",0)
        if r>-0.6: sc["TOO_TIGHT"]+=1
        elif d>7200 and r<-0.8: sc["TOO_WIDE"]+=1
        else: sc["REASONABLE"]+=1
    tight_pct=sc["TOO_TIGHT"]/n_sl

    # Conclusion
    sl_rate=len(sl_ex)/n
    if sl_rate>0.75 and tight_pct>0.25:
        conclusion="STOP_TOO_TIGHT"; reason=f"SL={sl_rate:.0%}, {tight_pct:.0%} too tight"
    elif sl_rate<0.65:
        conclusion="STOP_MODEL_EFFECTIVE"; reason=f"SL={sl_rate:.0%} acceptable"
    else:
        conclusion="INSUFFICIENT_DATA"; reason="Mixed"

    report={"research_id":"V10-R2","title":"Stop Placement Effectiveness",
        "generated_utc":timestamp_now(),"dataset_view":view.value,"sample_size":n,
        "conclusion":conclusion,"conclusion_reason":reason,"metrics":baseline,
        "current_stop":cs,"stop_buckets":sb,
        "efficiency":{k:{"count":v,"pct":round(v/n_sl,4)} for k,v in sc.items()}}

    md=[f"# V10-R2: Stop Effectiveness ({view.value})","",f"n={n} | {conclusion}","",reason,"",
        f"## Stop: mean={cs['mean']} med={cs['median']} SL={cs['sl_rate']:.0%} W={cs['w_stop']} L={cs['l_stop']}","",
        "## Buckets","","| Bucket | N | Win% | Exp R |","|---|---|---|---|"]
    for lb,s in sb.items():
        md.append(f"| {lb} | {s['count']} | {s['win_rate']:.0%} | {s['expectancy_r']:+.2f} |")
    md.extend(["",f"## Efficiency: tight={sc['TOO_TIGHT']} reasonable={sc['REASONABLE']} wide={sc['TOO_WIDE']}"])
    report["markdown"]="\n".join(md)
    return report
