"""
V10-OQ1: Opportunity Quality Analysis

Question: "Are V10 opportunities predictive of successful trades, or is the
opportunity layer creating low-quality candidates?"

Analyses opportunity-level metrics (score, components, pattern, regime) against
realised trade outcomes to determine if higher-quality opportunities produce
better results.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from research_engine.v10.base import compute_metrics, classify_confidence, timestamp_now
from research_engine.v10.dataset import DatasetView, load_trades, enrich_with_decision_trace


def run(view: DatasetView = DatasetView.FULL, trades: list[dict] | None = None) -> dict[str, Any]:
    """
    Run V10-OQ1: Opportunity Quality Analysis.

    Args:
        view: Dataset view
        trades: Pre-loaded trades (optional)

    Returns:
        Structured report dict.
    """
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

    # Enrich with decision trace (provides components + score)
    enriched_count = enrich_with_decision_trace(trades)

    n_total = len(trades)
    if n_total == 0:
        return _empty_report(view)

    baseline = compute_metrics(trades)

    # ─── QUALITY BUCKETS (using score as quality proxy) ───────
    for t in trades:
        t["_quality_score"] = t.get("dt_score") or t.get("score") or 0

    # Tercile split
    scores = sorted(t["_quality_score"] for t in trades)
    n_s = len(scores)
    q33 = scores[n_s // 3] if n_s >= 3 else 0.5
    q66 = scores[2 * n_s // 3] if n_s >= 3 else 0.7

    quality_groups = {
        "LOW": [t for t in trades if t["_quality_score"] < q33],
        "MEDIUM": [t for t in trades if q33 <= t["_quality_score"] < q66],
        "HIGH": [t for t in trades if t["_quality_score"] >= q66],
    }

    quality_analysis = {}
    for level, group in quality_groups.items():
        if not group:
            continue
        metrics = compute_metrics(group)
        r_vals = [t.get("realised_r", 0) for t in group]
        if len(r_vals) > 1:
            se = statistics.stdev(r_vals) / math.sqrt(len(r_vals))
            ci_lo = metrics["average_r"] - 1.96 * se
            ci_hi = metrics["average_r"] + 1.96 * se
        else:
            ci_lo = ci_hi = metrics["average_r"]

        quality_analysis[level] = {
            "count": metrics["count"],
            "score_range": f"{min(t['_quality_score'] for t in group):.3f}-{max(t['_quality_score'] for t in group):.3f}",
            "win_rate": metrics["win_rate"],
            "average_r": metrics["average_r"],
            "median_r": metrics["median_r"],
            "expectancy_r": metrics["expectancy_r"],
            "profit_factor": metrics["profit_factor"],
            "confidence": metrics["confidence"],
            "ci_lower": round(ci_lo, 4),
            "ci_upper": round(ci_hi, 4),
        }

    # ─── COMPONENT ANALYSIS ──────────────────────────────────
    # Gather components and correlate with R
    all_components: dict[str, list[tuple[float, float]]] = {}
    for t in trades:
        comps = t.get("dt_components") or {}
        r = t.get("realised_r", 0)
        for comp, val in comps.items():
            if isinstance(val, (int, float)) and val != 0:
                all_components.setdefault(comp, []).append((val, r))

    component_results = {}
    for comp, pairs in sorted(all_components.items(), key=lambda x: -len(x[1])):
        if len(pairs) < 5:
            continue
        comp_vals = [p[0] for p in pairs]
        r_vals = [p[1] for p in pairs]
        corr = _correlation(comp_vals, r_vals)

        # High vs low comparison
        sorted_p = sorted(pairs, key=lambda x: x[0])
        half = len(sorted_p) // 2
        low_r = statistics.mean([r for _, r in sorted_p[:half]]) if half > 0 else 0
        high_r = statistics.mean([r for _, r in sorted_p[half:]]) if len(sorted_p) - half > 0 else 0

        component_results[comp] = {
            "sample_size": len(pairs),
            "correlation": round(corr, 4),
            "low_half_r": round(low_r, 4),
            "high_half_r": round(high_r, 4),
            "spread": round(high_r - low_r, 4),
            "predictive": abs(corr) > 0.1 or abs(high_r - low_r) > 0.2,
        }

    # ─── PATTERN BREAKDOWN ────────────────────────────────────
    pattern_groups: dict[str, list] = {}
    for t in trades:
        pattern_groups.setdefault(t.get("pattern", "UNKNOWN"), []).append(t)

    pattern_quality = {}
    for pattern, group in sorted(pattern_groups.items(), key=lambda x: -len(x[1])):
        if len(group) < 3:
            continue
        avg_score = statistics.mean([t["_quality_score"] for t in group])
        metrics = compute_metrics(group)
        pattern_quality[pattern] = {
            "count": len(group),
            "avg_quality_score": round(avg_score, 4),
            "win_rate": metrics["win_rate"],
            "average_r": metrics["average_r"],
            "expectancy_r": metrics["expectancy_r"],
        }

    # ─── REGIME BREAKDOWN ─────────────────────────────────────
    regime_groups: dict[str, list] = {}
    for t in trades:
        reg = t.get("dt_regime") or t.get("regime") or "UNKNOWN"
        if reg != "UNKNOWN":
            regime_groups.setdefault(reg, []).append(t)

    regime_quality = {}
    for regime, group in sorted(regime_groups.items(), key=lambda x: -len(x[1])):
        if len(group) < 3:
            continue
        avg_score = statistics.mean([t["_quality_score"] for t in group])
        metrics = compute_metrics(group)
        regime_quality[regime] = {
            "count": len(group),
            "avg_quality_score": round(avg_score, 4),
            "win_rate": metrics["win_rate"],
            "average_r": metrics["average_r"],
            "expectancy_r": metrics["expectancy_r"],
        }

    # ─── WINNER vs LOSER PROFILE ─────────────────────────────
    winners = [t for t in trades if t.get("realised_r", 0) > 0]
    losers = [t for t in trades if t.get("realised_r", 0) <= 0]

    winner_profile = {
        "count": len(winners),
        "avg_score": round(statistics.mean([t["_quality_score"] for t in winners]), 4) if winners else 0,
        "avg_duration_min": round(statistics.mean([t.get("duration_seconds", 0) for t in winners]) / 60, 1) if winners else 0,
    }
    loser_profile = {
        "count": len(losers),
        "avg_score": round(statistics.mean([t["_quality_score"] for t in losers]), 4) if losers else 0,
        "avg_duration_min": round(statistics.mean([t.get("duration_seconds", 0) for t in losers]) / 60, 1) if losers else 0,
    }

    # ─── CONCLUSION ──────────────────────────────────────────
    quality_predicts = False
    if "HIGH" in quality_analysis and "LOW" in quality_analysis:
        high_exp = quality_analysis["HIGH"]["expectancy_r"]
        low_exp = quality_analysis["LOW"]["expectancy_r"]
        quality_predicts = high_exp > low_exp + 0.15

    predictive_components = sum(1 for v in component_results.values() if v["predictive"])
    score_gap = winner_profile["avg_score"] - loser_profile["avg_score"]

    if quality_predicts and predictive_components >= 3:
        conclusion = "OPPORTUNITY_LAYER_PREDICTIVE"
        conclusion_reason = f"High quality outperforms low by {quality_analysis.get('HIGH', {}).get('expectancy_r', 0) - quality_analysis.get('LOW', {}).get('expectancy_r', 0):.2f}R, {predictive_components} predictive components"
    elif quality_predicts or score_gap > 0.02:
        conclusion = "OPPORTUNITY_LAYER_PREDICTIVE"
        conclusion_reason = f"Higher quality opportunities produce better outcomes (score gap winners-losers: {score_gap:+.4f})"
    elif score_gap < -0.02:
        conclusion = "OPPORTUNITIES_LOW_QUALITY"
        conclusion_reason = f"Higher-scored opportunities perform WORSE (inverted signal, gap={score_gap:+.4f})"
    elif all(abs(v["correlation"]) < 0.05 for v in component_results.values()) if component_results else True:
        conclusion = "OPPORTUNITY_LAYER_NOT_PREDICTIVE"
        conclusion_reason = "No component or score level predicts outcomes"
    else:
        conclusion = "INCONCLUSIVE"
        conclusion_reason = "Mixed signals from quality analysis"

    report = {
        "research_id": "V10-OQ1",
        "title": "Opportunity Quality Analysis",
        "generated_utc": timestamp_now(),
        "dataset_view": view.value,
        "sample_size": n_total,
        "enriched": enriched_count,
        "conclusion": conclusion,
        "conclusion_reason": conclusion_reason,
        "metrics": baseline,
        "quality_buckets": quality_analysis,
        "quality_thresholds": {"q33": round(q33, 4), "q66": round(q66, 4)},
        "component_analysis": component_results,
        "pattern_quality": pattern_quality,
        "regime_quality": regime_quality,
        "winner_vs_loser": {"winners": winner_profile, "losers": loser_profile, "score_gap": round(score_gap, 4)},
    }

    report["markdown"] = _build_markdown(report)
    return report


def _correlation(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 3:
        return 0.0
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    sx = (sum((x - mx) ** 2 for x in xs) / n) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys) / n) ** 0.5
    return cov / (sx * sy) if sx > 0 and sy > 0 else 0.0


def _empty_report(view: DatasetView) -> dict[str, Any]:
    return {
        "research_id": "V10-OQ1", "title": "Opportunity Quality Analysis",
        "generated_utc": timestamp_now(), "dataset_view": view.value,
        "sample_size": 0, "conclusion": "NO_DATA",
        "metrics": {"count": 0},
        "markdown": f"# V10-OQ1: No data for {view.value}",
    }


def _build_markdown(report: dict) -> str:
    md = []
    md.append(f"# V10-OQ1: Opportunity Quality ({report['dataset_view']})")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append(f"Sample: {report['sample_size']} trades | Enriched: {report['enriched']}")
    md.append("")
    md.append(f"## Conclusion: {report['conclusion']}")
    md.append("")
    md.append(report["conclusion_reason"])
    md.append("")

    # Quality buckets
    md.append("## Quality Buckets")
    md.append("")
    md.append("| Level | N | Score Range | Win% | Avg R | Expectancy | PF | 95% CI |")
    md.append("|---|---|---|---|---|---|---|---|")
    for level in ["LOW", "MEDIUM", "HIGH"]:
        if level in report["quality_buckets"]:
            q = report["quality_buckets"][level]
            pf = f"{q['profit_factor']:.1f}" if q["profit_factor"] < 900 else "inf"
            md.append(f"| {level} | {q['count']} | {q['score_range']} | {q['win_rate']:.0%} | "
                      f"{q['average_r']:+.2f} | {q['expectancy_r']:+.2f} | {pf} | "
                      f"[{q['ci_lower']:+.2f}, {q['ci_upper']:+.2f}] |")

    # Winner vs loser
    wl = report["winner_vs_loser"]
    md.append("")
    md.append(f"## Winner vs Loser Profile")
    md.append(f"- Winners avg score: {wl['winners']['avg_score']:.4f} | duration: {wl['winners']['avg_duration_min']:.0f} min")
    md.append(f"- Losers avg score: {wl['losers']['avg_score']:.4f} | duration: {wl['losers']['avg_duration_min']:.0f} min")
    md.append(f"- Score gap: {wl['score_gap']:+.4f}")

    # Components
    if report["component_analysis"]:
        md.append("")
        md.append("## Component Predictive Power")
        md.append("")
        md.append("| Component | Corr | Low R | High R | Spread | Predictive |")
        md.append("|---|---|---|---|---|---|")
        for comp, stats in sorted(report["component_analysis"].items(), key=lambda x: -abs(x[1]["correlation"])):
            md.append(f"| {comp} | {stats['correlation']:+.3f} | {stats['low_half_r']:+.2f} | "
                      f"{stats['high_half_r']:+.2f} | {stats['spread']:+.2f} | {'YES' if stats['predictive'] else 'no'} |")

    # Pattern
    if report["pattern_quality"]:
        md.append("")
        md.append("## By Pattern")
        md.append("")
        md.append("| Pattern | N | Avg Quality | Win% | Exp R |")
        md.append("|---|---|---|---|---|")
        for p, s in report["pattern_quality"].items():
            md.append(f"| {p} | {s['count']} | {s['avg_quality_score']:.3f} | {s['win_rate']:.0%} | {s['expectancy_r']:+.2f} |")

    # Regime
    if report["regime_quality"]:
        md.append("")
        md.append("## By Regime")
        md.append("")
        md.append("| Regime | N | Avg Quality | Win% | Exp R |")
        md.append("|---|---|---|---|---|")
        for r, s in report["regime_quality"].items():
            md.append(f"| {r} | {s['count']} | {s['avg_quality_score']:.3f} | {s['win_rate']:.0%} | {s['expectancy_r']:+.2f} |")

    md.append("")
    md.append("---")
    md.append(f"*{report['enriched']}/{report['sample_size']} trades enriched*")
    return "\n".join(md)
