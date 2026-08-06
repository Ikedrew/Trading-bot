"""
V10-R1: Risk Model Effectiveness

Question: "Is the V10 risk model converting opportunities into favourable
risk-adjusted outcomes?"

Analyses R:R effectiveness, stop placement, target capture, and loss
concentration to determine if risk geometry supports or hinders performance.
"""

from __future__ import annotations

import statistics
from typing import Any

from research_engine.v10.base import compute_metrics, classify_confidence, timestamp_now
from research_engine.v10.dataset import DatasetView, load_trades


def run(view: DatasetView = DatasetView.FULL, trades: list[dict] | None = None) -> dict[str, Any]:
    """Run V10-R1: Risk Model Effectiveness."""
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
        return _empty_report(view)

    # Compute geometry fields
    for t in trades:
        entry = t.get("entry_price", 0)
        sl = t.get("stop_loss", 0)
        tp = t.get("take_profit", 0)
        t["_risk_dist"] = abs(entry - sl) if entry > 0 and sl > 0 else 0
        t["_reward_dist"] = abs(tp - entry) if entry > 0 and tp > 0 else 0
        t["_rr"] = round(t["_reward_dist"] / t["_risk_dist"], 2) if t["_risk_dist"] > 0 else 0
        t["_stop_pct"] = round(100 * t["_risk_dist"] / entry, 4) if entry > 0 else 0

    baseline = compute_metrics(trades)
    winners = [t for t in trades if t.get("realised_r", 0) > 0]
    losers = [t for t in trades if t.get("realised_r", 0) <= 0]
    sl_exits = [t for t in trades if t.get("exit_reason_validated", "") == "STOP_LOSS"]
    tp_exits = [t for t in trades if t.get("exit_reason_validated", "") == "TAKE_PROFIT"]

    # ─── 1. R:R EFFECTIVENESS ─────────────────────────────────
    rr_buckets = [("0-1R", 0, 1), ("1-2R", 1, 2), ("2-3R", 2, 3), ("3-5R", 3, 5), ("5R+", 5, 999)]
    rr_analysis = {}
    for label, lo, hi in rr_buckets:
        group = [t for t in trades if lo <= t["_rr"] < hi]
        if group:
            m = compute_metrics(group)
            rr_analysis[label] = {
                "count": m["count"], "win_rate": m["win_rate"],
                "average_r": m["average_r"], "expectancy_r": m["expectancy_r"],
                "profit_factor": m["profit_factor"], "confidence": m["confidence"],
            }

    # ─── 2. STOP LOSS EFFECTIVENESS ──────────────────────────
    stop_classification = {"reasonable": 0, "too_tight": 0, "too_wide": 0}
    for t in sl_exits:
        r = t.get("realised_r", 0)
        dur = t.get("duration_seconds", 0)
        if r > -0.5:
            stop_classification["too_tight"] += 1
        elif dur > 7200 and r < -0.8:
            stop_classification["too_wide"] += 1
        else:
            stop_classification["reasonable"] += 1

    n_sl = max(len(sl_exits), 1)
    stop_analysis = {
        "total_sl_exits": len(sl_exits),
        "sl_rate": round(len(sl_exits) / n_total, 4),
        "tp_rate": round(len(tp_exits) / n_total, 4),
        "classification": {k: {"count": v, "pct": round(v / n_sl, 4)} for k, v in stop_classification.items()},
        "avg_stop_pct": round(statistics.mean([t["_stop_pct"] for t in trades if t["_stop_pct"] > 0]), 4) if trades else 0,
        "median_stop_pct": round(statistics.median([t["_stop_pct"] for t in trades if t["_stop_pct"] > 0]), 4) if trades else 0,
    }

    # Winner vs loser stop distance
    w_stops = [t["_stop_pct"] for t in winners if t["_stop_pct"] > 0]
    l_stops = [t["_stop_pct"] for t in losers if t["_stop_pct"] > 0]
    stop_analysis["winner_avg_stop_pct"] = round(statistics.mean(w_stops), 4) if w_stops else 0
    stop_analysis["loser_avg_stop_pct"] = round(statistics.mean(l_stops), 4) if l_stops else 0

    # ─── 3. TARGET EFFECTIVENESS ─────────────────────────────
    winner_r_vals = [t.get("realised_r", 0) for t in winners]
    winner_planned_rr = [t["_rr"] for t in winners if t["_rr"] > 0]
    target_analysis = {
        "avg_winner_r": round(statistics.mean(winner_r_vals), 4) if winner_r_vals else 0,
        "avg_planned_rr_winners": round(statistics.mean(winner_planned_rr), 2) if winner_planned_rr else 0,
        "avg_planned_rr_losers": round(statistics.mean([t["_rr"] for t in losers if t["_rr"] > 0]), 2) if losers else 0,
        "tp_hit_count": len(tp_exits),
        "tp_hit_rate": round(len(tp_exits) / n_total, 4),
        "avg_tp_r": round(statistics.mean([t.get("realised_r", 0) for t in tp_exits]), 4) if tp_exits else 0,
    }
    # Capture ratio: actual R captured / planned RR
    if winner_planned_rr and winner_r_vals:
        target_analysis["capture_ratio"] = round(
            statistics.mean(winner_r_vals) / statistics.mean(winner_planned_rr), 4
        ) if statistics.mean(winner_planned_rr) > 0 else 0

    # ─── 4. RISK EFFICIENCY ──────────────────────────────────
    loser_r_vals = [t.get("realised_r", 0) for t in losers]
    risk_efficiency = {
        "avg_win_r": round(statistics.mean(winner_r_vals), 4) if winner_r_vals else 0,
        "avg_loss_r": round(statistics.mean(loser_r_vals), 4) if loser_r_vals else 0,
        "win_loss_ratio": round(
            abs(statistics.mean(winner_r_vals) / statistics.mean(loser_r_vals)), 2
        ) if loser_r_vals and statistics.mean(loser_r_vals) != 0 else 0,
    }

    # ─── 5. LOSS CONCENTRATION ────────────────────────────────
    sorted_losses = sorted(losers, key=lambda t: t.get("final_pnl", 0))
    top5_losses = sorted_losses[:5]
    total_loss_pnl = sum(t.get("final_pnl", 0) for t in losers)
    top5_pnl = sum(t.get("final_pnl", 0) for t in top5_losses)
    loss_analysis = {
        "total_losers": len(losers),
        "total_loss_pnl": round(total_loss_pnl, 2),
        "top5_loss_pnl": round(top5_pnl, 2),
        "top5_pct_of_total": round(top5_pnl / total_loss_pnl, 4) if total_loss_pnl != 0 else 0,
        "largest_loss": round(sorted_losses[0].get("final_pnl", 0), 4) if sorted_losses else 0,
        "top5_details": [
            {"trade_id": t.get("trade_id", ""), "symbol": t.get("symbol", ""),
             "pnl": round(t.get("final_pnl", 0), 4), "r": t.get("realised_r", 0)}
            for t in top5_losses
        ],
    }

    # ─── CONCLUSION ──────────────────────────────────────────
    sl_rate = len(sl_exits) / n_total if n_total > 0 else 0
    tight_pct = stop_classification["too_tight"] / n_sl

    if sl_rate > 0.75 and tight_pct > 0.25:
        conclusion = "STOPS_NEED_REVIEW"
        conclusion_reason = f"SL rate {sl_rate:.0%} with {tight_pct:.0%} classified as too tight"
    elif sl_rate > 0.75:
        conclusion = "STOPS_NEED_REVIEW"
        conclusion_reason = f"SL hit rate {sl_rate:.0%} is very high — entries may be poorly timed"
    elif target_analysis["tp_hit_rate"] < 0.15:
        conclusion = "TARGETS_TOO_AMBITIOUS"
        conclusion_reason = f"TP rate only {target_analysis['tp_hit_rate']:.0%} — targets rarely reached"
    elif sl_rate < 0.65 and target_analysis["tp_hit_rate"] > 0.20:
        conclusion = "RISK_MODEL_EFFECTIVE"
        conclusion_reason = f"Balanced: SL={sl_rate:.0%}, TP={target_analysis['tp_hit_rate']:.0%}"
    else:
        conclusion = "INCONCLUSIVE"
        conclusion_reason = f"SL={sl_rate:.0%}, TP={target_analysis['tp_hit_rate']:.0%} — mixed signals"

    report = {
        "research_id": "V10-R1",
        "title": "Risk Model Effectiveness",
        "generated_utc": timestamp_now(),
        "dataset_view": view.value,
        "sample_size": n_total,
        "conclusion": conclusion,
        "conclusion_reason": conclusion_reason,
        "metrics": baseline,
        "rr_analysis": rr_analysis,
        "stop_analysis": stop_analysis,
        "target_analysis": target_analysis,
        "risk_efficiency": risk_efficiency,
        "loss_analysis": loss_analysis,
    }

    report["markdown"] = _build_markdown(report)
    return report


def _empty_report(view: DatasetView) -> dict[str, Any]:
    return {
        "research_id": "V10-R1", "title": "Risk Model Effectiveness",
        "generated_utc": timestamp_now(), "dataset_view": view.value,
        "sample_size": 0, "conclusion": "NO_DATA",
        "metrics": {"count": 0},
        "markdown": f"# V10-R1: No data for {view.value}",
    }


def _build_markdown(report: dict) -> str:
    sa = report["stop_analysis"]
    ta = report["target_analysis"]
    re_ = report["risk_efficiency"]
    md = []
    md.append(f"# V10-R1: Risk Model Effectiveness ({report['dataset_view']})")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append(f"Sample: {report['sample_size']} trades")
    md.append("")
    md.append(f"## Conclusion: {report['conclusion']}")
    md.append("")
    md.append(report["conclusion_reason"])
    md.append("")

    md.append("## R:R Effectiveness")
    md.append("")
    md.append("| Bucket | N | Win% | Avg R | Expectancy | PF | Conf |")
    md.append("|---|---|---|---|---|---|---|")
    for label, stats in report["rr_analysis"].items():
        pf = f"{stats['profit_factor']:.1f}" if stats["profit_factor"] < 900 else "inf"
        md.append(f"| {label} | {stats['count']} | {stats['win_rate']:.0%} | "
                  f"{stats['average_r']:+.2f} | {stats['expectancy_r']:+.2f} | {pf} | {stats['confidence']} |")

    md.append("")
    md.append("## Stop Analysis")
    md.append("")
    md.append(f"- SL rate: {sa['sl_rate']:.0%} | TP rate: {sa['tp_rate']:.0%}")
    md.append(f"- Avg stop: {sa['avg_stop_pct']:.3f}% | Median: {sa['median_stop_pct']:.3f}%")
    md.append(f"- Winner avg stop: {sa['winner_avg_stop_pct']:.3f}% | Loser: {sa['loser_avg_stop_pct']:.3f}%")
    md.append(f"- Classification: reasonable={sa['classification']['reasonable']['count']} "
              f"tight={sa['classification']['too_tight']['count']} "
              f"wide={sa['classification']['too_wide']['count']}")

    md.append("")
    md.append("## Target Analysis")
    md.append("")
    md.append(f"- Avg winner R: {ta['avg_winner_r']:.2f} | Planned RR (winners): {ta['avg_planned_rr_winners']:.2f}")
    md.append(f"- TP hit rate: {ta['tp_hit_rate']:.0%} | Avg TP R: {ta['avg_tp_r']:.2f}")
    if "capture_ratio" in ta:
        md.append(f"- Capture ratio: {ta['capture_ratio']:.2f} (actual R / planned RR)")

    md.append("")
    md.append("## Risk Efficiency")
    md.append("")
    md.append(f"- Avg win: {re_['avg_win_r']:+.2f}R | Avg loss: {re_['avg_loss_r']:+.2f}R | Ratio: {re_['win_loss_ratio']:.2f}")

    md.append("")
    md.append("## Loss Concentration")
    la = report["loss_analysis"]
    md.append("")
    md.append(f"- Total loss PnL: ${la['total_loss_pnl']:.2f}")
    md.append(f"- Top 5 losses: ${la['top5_loss_pnl']:.2f} ({la['top5_pct_of_total']:.0%} of total)")
    md.append(f"- Largest single loss: ${la['largest_loss']:.2f}")

    md.append("")
    md.append("---")
    return "\n".join(md)
