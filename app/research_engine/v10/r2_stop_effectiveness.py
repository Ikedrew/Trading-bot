"""
V10-R2: Stop Placement Effectiveness

Question: "Is the current stop loss placement reducing expectancy by being
too tight, too wide, or incorrectly positioned?"

Analyses stop distance distribution, classifies stop outcomes, simulates
alternative stop widths, and breaks down by context.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from research_engine.v10.base import compute_metrics, classify_confidence, timestamp_now
from research_engine.v10.dataset import DatasetView, load_trades, enrich_with_decision_trace


# Approximate M5 ATR per symbol (pips) for ATR ratio estimation
_APPROX_ATR = {
    "EURUSD": 4.0, "GBPUSD": 5.5, "AUDUSD": 3.5, "NZDUSD": 3.5,
    "USDCAD": 4.0, "USDCHF": 4.0, "USDJPY": 5.0, "XAUUSD": 150.0,
    "US500": 5.0, "NAS100": 20.0,
}


def run(view: DatasetView = DatasetView.FULL, trades: list[dict] | None = None) -> dict[str, Any]:
    """Run V10-R2: Stop Placement Effectiveness."""
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

    enriched_count = enrich_with_decision_trace(trades)

    n_total = len(trades)
    if n_total == 0:
        return _empty_report(view)

    # Compute stop fields
    from research_engine.v10.dataset import _classify_instrument
    for t in trades:
        entry = t.get("entry_price", 0)
        sl = t.get("stop_loss", 0)
        sym = t.get("symbol", "")
        t["_sd"] = abs(entry - sl) if entry > 0 and sl > 0 else 0
        t["_sp"] = round(100 * t["_sd"] / entry, 4) if entry > 0 else 0
        # Pip size approximation
        inst = t.get("instrument_class", "") or _classify_instrument(sym)
        if inst == "FX_JPY":
            pip = 0.01
        elif inst in ("FX_MAJOR",):
            pip = 0.0001
        elif inst == "INDEX":
            pip = 0.1
        else:
            pip = 0.01
        t["_stop_pips"] = round(t["_sd"] / pip, 1) if pip > 0 else 0
        # ATR ratio
        atr = _APPROX_ATR.get(sym, 5.0)
        t["_atr_ratio"] = round(t["_stop_pips"] / atr, 2) if atr > 0 else 0

    baseline = compute_metrics(trades)
    sl_exits = [t for t in trades if t.get("exit_reason_validated", "") == "STOP_LOSS"]
    tp_exits = [t for t in trades if t.get("exit_reason_validated", "") == "TAKE_PROFIT"]
    winners = [t for t in trades if t.get("realised_r", 0) > 0]
    losers = [t for t in trades if t.get("realised_r", 0) <= 0]

    # ─── 1. CURRENT STOP ANALYSIS ────────────────────────────
    all_pips = [t["_stop_pips"] for t in trades if t["_stop_pips"] > 0]
    all_pcts = [t["_sp"] for t in trades if t["_sp"] > 0]
    current_stop = {
        "mean_pips": round(statistics.mean(all_pips), 1) if all_pips else 0,
        "median_pips": round(statistics.median(all_pips), 1) if all_pips else 0,
        "min_pips": round(min(all_pips), 1) if all_pips else 0,
        "max_pips": round(max(all_pips), 1) if all_pips else 0,
        "mean_pct": round(statistics.mean(all_pcts), 4) if all_pcts else 0,
        "sl_hit_pct": round(len(sl_exits) / n_total, 4),
        "tp_hit_pct": round(len(tp_exits) / n_total, 4),
        "avg_r_sl_exits": round(statistics.mean([t.get("realised_r", 0) for t in sl_exits]), 4) if sl_exits else 0,
        "avg_r_tp_exits": round(statistics.mean([t.get("realised_r", 0) for t in tp_exits]), 4) if tp_exits else 0,
        "winner_mean_pips": round(statistics.mean([t["_stop_pips"] for t in winners if t["_stop_pips"] > 0]), 1) if winners else 0,
        "loser_mean_pips": round(statistics.mean([t["_stop_pips"] for t in losers if t["_stop_pips"] > 0]), 1) if losers else 0,
    }

    # ─── 2. STOP DISTANCE BUCKETS (tercile) ──────────────────
    sorted_by_stop = sorted([t for t in trades if t["_stop_pips"] > 0], key=lambda t: t["_stop_pips"])
    n_s = len(sorted_by_stop)
    third = n_s // 3
    stop_groups = {
        "TIGHT": sorted_by_stop[:third],
        "MEDIUM": sorted_by_stop[third:2*third],
        "WIDE": sorted_by_stop[2*third:],
    }
    stop_bucket_analysis = {}
    for label, group in stop_groups.items():
        if not group:
            continue
        m = compute_metrics(group)
        pips = [t["_stop_pips"] for t in group]
        stop_bucket_analysis[label] = {
            "count": m["count"],
            "stop_range_pips": f"{min(pips):.1f}-{max(pips):.1f}",
            "win_rate": m["win_rate"],
            "average_r": m["average_r"],
            "expectancy_r": m["expectancy_r"],
            "avg_win_r": m["average_win_r"],
            "avg_loss_r": m["average_loss_r"],
            "confidence": m["confidence"],
        }

    # ─── 3. STOP EFFICIENCY CLASSIFICATION ───────────────────
    stop_classes = {"TOO_TIGHT": 0, "REASONABLE": 0, "TOO_WIDE": 0}
    for t in sl_exits:
        r = t.get("realised_r", 0)
        dur = t.get("duration_seconds", 0)
        if r > -0.6:
            stop_classes["TOO_TIGHT"] += 1
        elif dur > 7200 and r < -0.8:
            stop_classes["TOO_WIDE"] += 1
        else:
            stop_classes["REASONABLE"] += 1

    n_sl = max(len(sl_exits), 1)
    stop_efficiency = {k: {"count": v, "pct": round(v / n_sl, 4)} for k, v in stop_classes.items()}

    # ─── 4. COUNTERFACTUAL SIMULATION ────────────────────────
    # Estimate: how many "TOO_TIGHT" losses would survive with wider stops
    tight_losses = stop_classes["TOO_TIGHT"]
    fast_reasonable = sum(1 for t in sl_exits if t.get("duration_seconds", 0) < 600
                         and -1.0 <= t.get("realised_r", 0) <= -0.6)
    simulation = {}
    for mult_label, estimated_save_pct in [("1.25x", 0.3), ("1.5x", 0.6), ("2.0x", 0.8)]:
        saved = int(tight_losses * estimated_save_pct) + int(fast_reasonable * estimated_save_pct * 0.3)
        new_winners = len(winners) + saved
        new_win_rate = new_winners / n_total if n_total > 0 else 0
        # Estimate R improvement: each saved trade goes from ~-1R to ~+1R = +2R swing
        r_improvement = saved * 2.0 / n_total
        current_avg_r = baseline["average_r"]
        new_avg_r = current_avg_r + r_improvement
        simulation[mult_label] = {
            "estimated_trades_saved": saved,
            "new_win_rate": round(new_win_rate, 4),
            "estimated_new_avg_r": round(new_avg_r, 4),
            "improvement_r": round(r_improvement, 4),
            "confidence": "ESTIMATE",
        }

    # ─── 5. CONTEXT BREAKDOWN ────────────────────────────────
    # By regime
    regime_stops = {}
    for t in trades:
        reg = t.get("dt_regime") or t.get("regime") or "UNKNOWN"
        if reg != "UNKNOWN":
            regime_stops.setdefault(reg, []).append(t)
    context_regime = {}
    for reg, group in sorted(regime_stops.items(), key=lambda x: -len(x[1])):
        if len(group) < 5:
            continue
        m = compute_metrics(group)
        sl_n = sum(1 for t in group if t.get("exit_reason_validated", "") == "STOP_LOSS")
        context_regime[reg] = {
            "count": len(group),
            "mean_stop_pips": round(statistics.mean([t["_stop_pips"] for t in group if t["_stop_pips"] > 0]), 1),
            "sl_rate": round(sl_n / len(group), 4),
            "win_rate": m["win_rate"],
            "expectancy_r": m["expectancy_r"],
        }

    # By pattern
    pattern_stops = {}
    for t in trades:
        p = t.get("pattern", "UNKNOWN")
        pattern_stops.setdefault(p, []).append(t)
    context_pattern = {}
    for pat, group in sorted(pattern_stops.items(), key=lambda x: -len(x[1])):
        if len(group) < 5:
            continue
        m = compute_metrics(group)
        sl_n = sum(1 for t in group if t.get("exit_reason_validated", "") == "STOP_LOSS")
        context_pattern[pat] = {
            "count": len(group),
            "mean_stop_pips": round(statistics.mean([t["_stop_pips"] for t in group if t["_stop_pips"] > 0]), 1),
            "sl_rate": round(sl_n / len(group), 4),
            "win_rate": m["win_rate"],
            "expectancy_r": m["expectancy_r"],
        }

    # ─── 6. LOSS BEHAVIOUR ───────────────────────────────────
    loss_r_vals = [t.get("realised_r", 0) for t in losers]
    early_stops = sum(1 for t in sl_exits if t.get("duration_seconds", 0) < 300)
    loss_behaviour = {
        "total_losers": len(losers),
        "avg_loss_r": round(statistics.mean(loss_r_vals), 4) if loss_r_vals else 0,
        "early_stop_outs": early_stops,
        "early_stop_pct": round(early_stops / n_sl, 4),
        "largest_loss_r": round(min(loss_r_vals), 4) if loss_r_vals else 0,
    }

    # ─── CONCLUSION ──────────────────────────────────────────
    sl_rate = len(sl_exits) / n_total
    tight_pct = stop_classes["TOO_TIGHT"] / n_sl

    # Check if tight stops dominate AND wider stops produce better R
    wide_group = stop_groups.get("WIDE", [])
    tight_group = stop_groups.get("TIGHT", [])
    wide_exp = stop_bucket_analysis.get("WIDE", {}).get("expectancy_r", 0)
    tight_exp = stop_bucket_analysis.get("TIGHT", {}).get("expectancy_r", 0)

    if sl_rate > 0.75 and tight_pct > 0.25:
        conclusion = "STOP_TOO_TIGHT"
        conclusion_reason = f"SL rate {sl_rate:.0%}, {tight_pct:.0%} stops barely grazed. Wider stops estimated to save {simulation.get('1.5x', {}).get('estimated_trades_saved', 0)} trades."
    elif wide_exp > tight_exp + 0.3 and len(wide_group) >= 10:
        conclusion = "STOP_TOO_TIGHT"
        conclusion_reason = f"Wide stops outperform tight by {wide_exp - tight_exp:.2f}R — current stops inside market noise"
    elif sl_rate < 0.65:
        conclusion = "STOP_MODEL_EFFECTIVE"
        conclusion_reason = f"SL rate {sl_rate:.0%} is acceptable, stops are reasonably placed"
    elif any(v["sl_rate"] > 0.85 for v in context_regime.values()):
        conclusion = "STOP_MODEL_CONTEXT_DEPENDENT"
        conclusion_reason = "Some regimes have 85%+ SL rate — stop width should adapt to context"
    else:
        conclusion = "INSUFFICIENT_DATA"
        conclusion_reason = "Mixed signals — need more data"

    report = {
        "research_id": "V10-R2",
        "title": "Stop Placement Effectiveness",
        "generated_utc": timestamp_now(),
        "dataset_view": view.value,
        "sample_size": n_total,
        "enriched": enriched_count,
        "conclusion": conclusion,
        "conclusion_reason": conclusion_reason,
        "metrics": baseline,
        "current_stop_analysis": current_stop,
        "stop_bucket_analysis": stop_bucket_analysis,
        "stop_efficiency": stop_efficiency,
        "counterfactual_simulation": simulation,
        "context_regime": context_regime,
        "context_pattern": context_pattern,
        "loss_behaviour": loss_behaviour,
    }

    report["markdown"] = _build_markdown(report)
    return report


def _empty_report(view: DatasetView) -> dict[str, Any]:
    return {
        "research_id": "V10-R2", "title": "Stop Placement Effectiveness",
        "generated_utc": timestamp_now(), "dataset_view": view.value,
        "sample_size": 0, "conclusion": "NO_DATA",
        "metrics": {"count": 0},
        "markdown": f"# V10-R2: No data for {view.value}",
    }


def _build_markdown(report: dict) -> str:
    cs = report["current_stop_analysis"]
    md = []
    md.append(f"# V10-R2: Stop Placement Effectiveness ({report['dataset_view']})")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append(f"Sample: {report['sample_size']} trades")
    md.append("")
    md.append(f"## Conclusion: {report['conclusion']}")
    md.append("")
    md.append(report["conclusion_reason"])
    md.append("")

    md.append("## Current Stop Profile")
    md.append("")
    md.append(f"| Metric | Value |")
    md.append(f"|---|---|")
    md.append(f"| Mean stop | {cs['mean_pips']:.1f} pips |")
    md.append(f"| Median stop | {cs['median_pips']:.1f} pips |")
    md.append(f"| SL hit rate | {cs['sl_hit_pct']:.0%} |")
    md.append(f"| TP hit rate | {cs['tp_hit_pct']:.0%} |")
    md.append(f"| Winner avg stop | {cs['winner_mean_pips']:.1f} pips |")
    md.append(f"| Loser avg stop | {cs['loser_mean_pips']:.1f} pips |")

    md.append("")
    md.append("## Stop Distance Buckets")
    md.append("")
    md.append("| Bucket | N | Range | Win% | Avg R | Expectancy |")
    md.append("|---|---|---|---|---|---|")
    for label, stats in report["stop_bucket_analysis"].items():
        md.append(f"| {label} | {stats['count']} | {stats['stop_range_pips']} | "
                  f"{stats['win_rate']:.0%} | {stats['average_r']:+.2f} | {stats['expectancy_r']:+.2f} |")

    md.append("")
    md.append("## Stop Efficiency (SL exits only)")
    md.append("")
    md.append("| Classification | Count | % |")
    md.append("|---|---|---|")
    for cls, stats in report["stop_efficiency"].items():
        md.append(f"| {cls} | {stats['count']} | {stats['pct']:.0%} |")

    md.append("")
    md.append("## Counterfactual Simulation")
    md.append("")
    md.append("| Wider By | Trades Saved | New Win% | R Improvement |")
    md.append("|---|---|---|---|")
    for mult, stats in report["counterfactual_simulation"].items():
        md.append(f"| {mult} | {stats['estimated_trades_saved']} | {stats['new_win_rate']:.0%} | {stats['improvement_r']:+.2f}R |")

    if report["context_regime"]:
        md.append("")
        md.append("## By Regime")
        md.append("")
        md.append("| Regime | N | Avg Stop | SL Rate | Win% | Exp R |")
        md.append("|---|---|---|---|---|---|")
        for reg, stats in report["context_regime"].items():
            md.append(f"| {reg} | {stats['count']} | {stats['mean_stop_pips']:.1f} | "
                      f"{stats['sl_rate']:.0%} | {stats['win_rate']:.0%} | {stats['expectancy_r']:+.2f} |")

    md.append("")
    md.append("---")
    return "\n".join(md)
