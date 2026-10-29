"""
V10-OQ2: Opportunity vs Outcome Failure Analysis

Question: "When trades lose, is it because of poor opportunity detection,
decision failure, entry timing, or risk model failure?"

Classifies losing trades by failure stage to identify the weakest link
in the V10 pipeline.
"""

from __future__ import annotations

import statistics
from typing import Any

from research_engine.v10.base import compute_metrics, classify_confidence, timestamp_now
from research_engine.v10.dataset import DatasetView, load_trades, enrich_with_decision_trace


def run(view: DatasetView = DatasetView.FULL, trades: list[dict] | None = None) -> dict[str, Any]:
    """Run V10-OQ2: Opportunity vs Outcome Failure Analysis."""
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

    baseline = compute_metrics(trades)

    # Resolve scores
    for t in trades:
        t["_score"] = t.get("dt_score") or t.get("score") or 0

    winners = [t for t in trades if t.get("realised_r", 0) > 0]
    losers = [t for t in trades if t.get("realised_r", 0) <= 0]

    # ─── 1. QUALITY BUCKETS ──────────────────────────────────
    scores = sorted(t["_score"] for t in trades)
    q33 = scores[len(scores) // 3] if len(scores) >= 3 else 0.5
    q66 = scores[2 * len(scores) // 3] if len(scores) >= 3 else 0.7

    quality_groups = {
        "LOW": [t for t in trades if t["_score"] < q33],
        "MEDIUM": [t for t in trades if q33 <= t["_score"] < q66],
        "HIGH": [t for t in trades if t["_score"] >= q66],
    }

    quality_analysis = {}
    for level, group in quality_groups.items():
        if not group:
            continue
        m = compute_metrics(group)
        loss_pct = sum(1 for t in group if t.get("realised_r", 0) <= 0) / len(group)
        quality_analysis[level] = {
            "count": m["count"],
            "win_rate": m["win_rate"],
            "loss_pct": round(loss_pct, 4),
            "average_r": m["average_r"],
            "expectancy_r": m["expectancy_r"],
            "avg_score": round(statistics.mean([t["_score"] for t in group]), 4),
        }

    # ─── 2. FAILURE STAGE CLASSIFICATION ─────────────────────
    failure_stages = {
        "OPPORTUNITY_FAILURE": 0,
        "DECISION_FAILURE": 0,
        "ENTRY_FAILURE": 0,
        "RISK_FAILURE": 0,
    }

    for t in losers:
        score = t["_score"]
        r = t.get("realised_r", 0)
        duration = t.get("duration_seconds", 0)

        if score < q33:
            # Low quality opportunity → opportunity layer failed to filter
            failure_stages["OPPORTUNITY_FAILURE"] += 1
        elif duration < 300 and r <= -0.8:
            # High/medium score but immediate SL hit → entry timing
            failure_stages["ENTRY_FAILURE"] += 1
        elif r > -0.6:
            # Barely lost → risk was too tight (stop grazed)
            failure_stages["RISK_FAILURE"] += 1
        else:
            # Good opportunity/score but standard loss → decision layer
            failure_stages["DECISION_FAILURE"] += 1

    n_losers = max(len(losers), 1)
    failure_distribution = {
        k: {"count": v, "pct": round(v / n_losers, 4)}
        for k, v in failure_stages.items()
    }

    # ─── 3. HIGH QUALITY LOSSES ──────────────────────────────
    high_quality_losses = [t for t in losers if t["_score"] >= q66]
    hq_loss_analysis = {
        "count": len(high_quality_losses),
        "pct_of_all_losses": round(len(high_quality_losses) / n_losers, 4),
        "avg_r": round(statistics.mean([t.get("realised_r", 0) for t in high_quality_losses]), 4) if high_quality_losses else 0,
        "avg_score": round(statistics.mean([t["_score"] for t in high_quality_losses]), 4) if high_quality_losses else 0,
    }

    # ─── 4. SCORE COMPARISON ─────────────────────────────────
    score_comparison = {
        "winner_avg_score": round(statistics.mean([t["_score"] for t in winners]), 4) if winners else 0,
        "loser_avg_score": round(statistics.mean([t["_score"] for t in losers]), 4) if losers else 0,
        "gap": 0,
    }
    score_comparison["gap"] = round(
        score_comparison["winner_avg_score"] - score_comparison["loser_avg_score"], 4
    )

    # Component comparison (if available)
    comp_comparison = {}
    all_comps = set()
    for t in trades:
        all_comps.update((t.get("dt_components") or {}).keys())

    for comp in sorted(all_comps):
        w_vals = [t.get("dt_components", {}).get(comp, 0) for t in winners if comp in (t.get("dt_components") or {})]
        l_vals = [t.get("dt_components", {}).get(comp, 0) for t in losers if comp in (t.get("dt_components") or {})]
        if w_vals and l_vals:
            w_mean = statistics.mean(w_vals)
            l_mean = statistics.mean(l_vals)
            comp_comparison[comp] = {
                "winner_avg": round(w_mean, 4),
                "loser_avg": round(l_mean, 4),
                "gap": round(w_mean - l_mean, 4),
                "direction": "winners_higher" if w_mean > l_mean else "losers_higher",
            }

    # ─── 5. TIMING ANALYSIS ─────────────────────────────────
    timing_classes = {"IMMEDIATE_FAILURE": 0, "DELAYED_FAILURE": 0, "NORMAL_FAILURE": 0}
    for t in losers:
        dur = t.get("duration_seconds", 0)
        r = t.get("realised_r", 0)
        if dur < 300:
            timing_classes["IMMEDIATE_FAILURE"] += 1
        elif r > -0.5:
            # Small loss after longer time → may have been favourable then reversed
            timing_classes["DELAYED_FAILURE"] += 1
        else:
            timing_classes["NORMAL_FAILURE"] += 1

    timing_analysis = {
        k: {"count": v, "pct": round(v / n_losers, 4)}
        for k, v in timing_classes.items()
    }

    # ─── CONCLUSION ──────────────────────────────────────────
    dominant_failure = max(failure_stages.items(), key=lambda x: x[1])
    dominant_pct = dominant_failure[1] / n_losers

    if dominant_failure[0] == "OPPORTUNITY_FAILURE" and dominant_pct > 0.35:
        conclusion = "OPPORTUNITY_SELECTION_FAILURE"
        conclusion_reason = f"{dominant_pct:.0%} of losses from low-quality opportunities — filter is too permissive"
    elif dominant_failure[0] == "RISK_FAILURE" and dominant_pct > 0.30:
        conclusion = "RISK_LAYER_FAILURE"
        conclusion_reason = f"{dominant_pct:.0%} of losses are barely-grazed stops — risk model too tight"
    elif dominant_failure[0] == "ENTRY_FAILURE" and dominant_pct > 0.25:
        conclusion = "ENTRY_TIMING_FAILURE"
        conclusion_reason = f"{dominant_pct:.0%} of losses are immediate after entry — timing issue"
    elif dominant_failure[0] == "DECISION_FAILURE" and dominant_pct > 0.35:
        conclusion = "DECISION_LAYER_FAILURE"
        conclusion_reason = f"{dominant_pct:.0%} of losses from medium/high quality opportunities — decision/execution gap"
    else:
        conclusion = "MIXED_FAILURE"
        conclusion_reason = f"No single dominant failure: {', '.join(f'{k}={v}' for k, v in failure_stages.items())}"

    report = {
        "research_id": "V10-OQ2",
        "title": "Opportunity vs Outcome Failure",
        "generated_utc": timestamp_now(),
        "dataset_view": view.value,
        "sample_size": n_total,
        "enriched": enriched_count,
        "winners": len(winners),
        "losers": len(losers),
        "conclusion": conclusion,
        "conclusion_reason": conclusion_reason,
        "metrics": baseline,
        "quality_analysis": quality_analysis,
        "failure_distribution": failure_distribution,
        "high_quality_losses": hq_loss_analysis,
        "score_comparison": score_comparison,
        "component_comparison": comp_comparison,
        "timing_analysis": timing_analysis,
    }

    report["markdown"] = _build_markdown(report)
    return report


def _empty_report(view: DatasetView) -> dict[str, Any]:
    return {
        "research_id": "V10-OQ2", "title": "Opportunity vs Outcome Failure",
        "generated_utc": timestamp_now(), "dataset_view": view.value,
        "sample_size": 0, "conclusion": "NO_DATA",
        "metrics": {"count": 0},
        "markdown": f"# V10-OQ2: No data for {view.value}",
    }


def _build_markdown(report: dict) -> str:
    md = []
    md.append(f"# V10-OQ2: Opportunity vs Outcome Failure ({report['dataset_view']})")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append(f"Sample: {report['sample_size']} trades ({report['winners']} wins, {report['losers']} losses)")
    md.append("")
    md.append(f"## Conclusion: {report['conclusion']}")
    md.append("")
    md.append(report["conclusion_reason"])
    md.append("")

    md.append("## Quality vs Outcome")
    md.append("")
    md.append("| Level | N | Win% | Loss% | Avg R | Expectancy |")
    md.append("|---|---|---|---|---|---|")
    for level in ["LOW", "MEDIUM", "HIGH"]:
        if level in report["quality_analysis"]:
            q = report["quality_analysis"][level]
            md.append(f"| {level} | {q['count']} | {q['win_rate']:.0%} | {q['loss_pct']:.0%} | "
                      f"{q['average_r']:+.2f} | {q['expectancy_r']:+.2f} |")

    md.append("")
    md.append("## Failure Stage Classification")
    md.append("")
    md.append("| Stage | Count | % of Losses | Description |")
    md.append("|---|---|---|---|")
    descriptions = {
        "OPPORTUNITY_FAILURE": "Low quality opportunity passed through",
        "DECISION_FAILURE": "Good opportunity but standard loss",
        "ENTRY_FAILURE": "Immediate SL hit after entry",
        "RISK_FAILURE": "Stop barely grazed (too tight)",
    }
    for stage, stats in report["failure_distribution"].items():
        md.append(f"| {stage} | {stats['count']} | {stats['pct']:.0%} | {descriptions.get(stage, '')} |")

    md.append("")
    md.append("## Score Comparison (Winners vs Losers)")
    sc = report["score_comparison"]
    md.append(f"- Winners avg score: {sc['winner_avg_score']:.4f}")
    md.append(f"- Losers avg score: {sc['loser_avg_score']:.4f}")
    md.append(f"- Gap: {sc['gap']:+.4f}")

    md.append("")
    md.append("## Timing Analysis")
    md.append("")
    md.append("| Type | Count | % |")
    md.append("|---|---|---|")
    for typ, stats in report["timing_analysis"].items():
        md.append(f"| {typ} | {stats['count']} | {stats['pct']:.0%} |")

    hql = report["high_quality_losses"]
    md.append("")
    md.append(f"## High Quality Losses: {hql['count']} ({hql['pct_of_all_losses']:.0%} of all losses, avg R={hql['avg_r']:+.2f})")

    md.append("")
    md.append("---")
    md.append(f"*{report['enriched']}/{report['sample_size']} trades enriched*")
    return "\n".join(md)
