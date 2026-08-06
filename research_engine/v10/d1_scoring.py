"""
V10-D1: Scoring Components Predictive Power

Question: "Do V10 decision scores and components predict realised trade outcomes?"

Enriches trades with decision_trace data, then analyses whether higher
scores/components correlate with better R-multiples.

Handles two component schemas separately:
    Legacy (10 components): bias_alignment, confirmation_pre, etc.
    V10 (4 dimensions): location_score, structure_score, behaviour_score, formation_score
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from research_engine.v10.base import compute_metrics, classify_confidence, timestamp_now
from research_engine.v10.dataset import DatasetView, load_trades, enrich_with_decision_trace


_LEGACY_COMPONENTS = frozenset({
    "bias_alignment", "bias_stability", "chop_clarity", "confirmation_pre",
    "h4_alignment", "htf_alignment", "market_quality", "pattern_quality",
    "trend_alignment", "volatility_quality",
})

_V10_COMPONENTS = frozenset({
    "location_score", "structure_score", "behaviour_score", "formation_score",
})


def run(view: DatasetView = DatasetView.FULL, trades: list[dict] | None = None) -> dict[str, Any]:
    """
    Run V10-D1: Scoring Components Predictive Power.

    Args:
        view: Dataset view to analyse
        trades: Pre-loaded trades (optional, for Lambda)

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

    # Enrich with decision trace data
    enriched_count = enrich_with_decision_trace(trades)

    n_total = len(trades)
    if n_total == 0:
        return _empty_report(view)

    baseline = compute_metrics(trades)

    # ─── OVERALL SCORE ANALYSIS ───────────────────────────────
    score_buckets = [
        ("0.0-0.3", 0.0, 0.3),
        ("0.3-0.5", 0.3, 0.5),
        ("0.5-0.6", 0.5, 0.6),
        ("0.6-0.7", 0.6, 0.7),
        ("0.7-1.0", 0.7, 1.0),
    ]
    score_analysis = {}
    for label, lo, hi in score_buckets:
        group = [t for t in trades if lo <= (t.get("dt_score") or 0) < hi]
        if group:
            metrics = compute_metrics(group)
            score_analysis[label] = {
                "count": metrics["count"],
                "win_rate": metrics["win_rate"],
                "average_r": metrics["average_r"],
                "median_r": metrics["median_r"],
                "expectancy_r": metrics["expectancy_r"],
                "profit_factor": metrics["profit_factor"],
                "confidence": metrics["confidence"],
            }

    # Check monotonicity (higher score = better R)
    score_means = [(k, v["average_r"]) for k, v in score_analysis.items() if v["count"] > 0]
    monotonic = len(score_means) >= 2 and all(
        score_means[i][1] <= score_means[i + 1][1]
        for i in range(len(score_means) - 1)
    )

    # Calibration gap: high score avg R vs low score avg R
    high_score = [t for t in trades if (t.get("dt_score") or 0) >= 0.6]
    low_score = [t for t in trades if (t.get("dt_score") or 0) < 0.4]
    high_r = statistics.mean([t.get("realised_r", 0) for t in high_score]) if high_score else 0
    low_r = statistics.mean([t.get("realised_r", 0) for t in low_score]) if low_score else 0
    calibration_gap = high_r - low_r

    # ─── COMPONENT ANALYSIS ───────────────────────────────────
    # Gather all components present
    all_components: dict[str, list[tuple[float, float]]] = {}
    for t in trades:
        comps = t.get("dt_components") or {}
        r = t.get("realised_r", 0)
        for comp, val in comps.items():
            if isinstance(val, (int, float)) and val != 0:
                all_components.setdefault(comp, []).append((val, r))

    # Analyse each component
    legacy_analysis = {}
    v10_analysis = {}

    for comp, pairs in sorted(all_components.items(), key=lambda x: -len(x[1])):
        if len(pairs) < 5:
            continue

        comp_vals = [p[0] for p in pairs]
        r_vals = [p[1] for p in pairs]
        corr = _correlation(comp_vals, r_vals)

        # Tercile split
        sorted_pairs = sorted(pairs, key=lambda x: x[0])
        n_c = len(sorted_pairs)
        third = n_c // 3
        low_r_avg = statistics.mean([r for _, r in sorted_pairs[:third]]) if third > 0 else 0
        mid_r_avg = statistics.mean([r for _, r in sorted_pairs[third:2*third]]) if third > 0 else 0
        high_r_avg = statistics.mean([r for _, r in sorted_pairs[2*third:]]) if n_c - 2*third > 0 else 0
        spread = high_r_avg - low_r_avg

        entry = {
            "sample_size": len(pairs),
            "correlation": round(corr, 4),
            "low_tercile_r": round(low_r_avg, 4),
            "mid_tercile_r": round(mid_r_avg, 4),
            "high_tercile_r": round(high_r_avg, 4),
            "spread": round(spread, 4),
            "signal": "positive" if spread > 0.2 else ("negative" if spread < -0.2 else "neutral"),
        }

        if comp in _LEGACY_COMPONENTS:
            legacy_analysis[comp] = entry
        elif comp in _V10_COMPONENTS:
            v10_analysis[comp] = entry
        else:
            legacy_analysis[comp] = entry  # Unknown → treat as legacy

    # Rank by |correlation|
    ranked_legacy = sorted(legacy_analysis.items(), key=lambda x: abs(x[1]["correlation"]), reverse=True)
    ranked_v10 = sorted(v10_analysis.items(), key=lambda x: abs(x[1]["correlation"]), reverse=True)

    # ─── CONCLUSION ───────────────────────────────────────────
    strong_components = [c for c, v in all_components.items()
                        if c in legacy_analysis and abs(legacy_analysis[c]["correlation"]) > 0.1]
    strong_v10 = [c for c, v in v10_analysis.items() if abs(v["correlation"]) > 0.1]

    if calibration_gap > 0.5 and monotonic:
        conclusion = "SCORE_IS_PREDICTIVE"
        conclusion_reason = f"Higher scores predict better R (gap={calibration_gap:.2f}R, monotonic={monotonic})"
    elif calibration_gap > 0.2 or len(strong_components) >= 2:
        conclusion = "SCORE_IS_PREDICTIVE"
        conclusion_reason = f"Calibration gap={calibration_gap:.2f}R, {len(strong_components)} components with |corr|>0.1"
    elif all(abs(v["correlation"]) < 0.05 for v in legacy_analysis.values()):
        conclusion = "SCORE_HAS_NO_PREDICTIVE_POWER"
        conclusion_reason = "No component shows meaningful correlation with outcomes"
    else:
        conclusion = "INCONCLUSIVE"
        conclusion_reason = "Mixed signals — some components correlate but overall pattern unclear"

    report = {
        "research_id": "V10-D1",
        "title": "Scoring Components Predictive Power",
        "generated_utc": timestamp_now(),
        "dataset_view": view.value,
        "sample_size": n_total,
        "enriched_with_decision_trace": enriched_count,
        "conclusion": conclusion,
        "conclusion_reason": conclusion_reason,
        "metrics": baseline,
        "score_analysis": score_analysis,
        "calibration": {
            "high_score_avg_r": round(high_r, 4),
            "high_score_count": len(high_score),
            "low_score_avg_r": round(low_r, 4),
            "low_score_count": len(low_score),
            "calibration_gap": round(calibration_gap, 4),
            "monotonic": monotonic,
        },
        "legacy_components": {
            "count": len(legacy_analysis),
            "components": dict(ranked_legacy),
        },
        "v10_components": {
            "count": len(v10_analysis),
            "components": dict(ranked_v10),
        },
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
        "research_id": "V10-D1", "title": "Scoring Components Predictive Power",
        "generated_utc": timestamp_now(), "dataset_view": view.value,
        "sample_size": 0, "conclusion": "NO_DATA",
        "metrics": {"count": 0}, "score_analysis": {},
        "markdown": f"# V10-D1: No data for {view.value}",
    }


def _build_markdown(report: dict) -> str:
    cal = report["calibration"]
    md = []
    md.append(f"# V10-D1: Scoring Predictive Power ({report['dataset_view']})")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append(f"Sample: {report['sample_size']} trades | Enriched: {report['enriched_with_decision_trace']}")
    md.append("")
    md.append(f"## Conclusion: {report['conclusion']}")
    md.append("")
    md.append(report["conclusion_reason"])
    md.append("")
    md.append("## Overall Score vs Outcome")
    md.append("")
    md.append("| Score Range | N | Win% | Avg R | Expectancy | PF | Conf |")
    md.append("|---|---|---|---|---|---|---|")
    for label, stats in report["score_analysis"].items():
        pf = f"{stats['profit_factor']:.1f}" if stats["profit_factor"] < 900 else "inf"
        md.append(f"| {label} | {stats['count']} | {stats['win_rate']:.0%} | "
                  f"{stats['average_r']:+.2f} | {stats['expectancy_r']:+.2f} | {pf} | {stats['confidence']} |")
    md.append("")
    md.append(f"Calibration gap: {cal['calibration_gap']:.4f}R | Monotonic: {cal['monotonic']}")
    md.append("")

    # Legacy components
    if report["legacy_components"]["count"] > 0:
        md.append("## Legacy Components (10-factor scoring)")
        md.append("")
        md.append("| Component | Corr | Low R | Mid R | High R | Spread | Signal |")
        md.append("|---|---|---|---|---|---|---|")
        for comp, stats in report["legacy_components"]["components"].items():
            md.append(f"| {comp} | {stats['correlation']:+.3f} | {stats['low_tercile_r']:+.2f} | "
                      f"{stats['mid_tercile_r']:+.2f} | {stats['high_tercile_r']:+.2f} | "
                      f"{stats['spread']:+.2f} | {stats['signal']} |")
        md.append("")

    # V10 components
    if report["v10_components"]["count"] > 0:
        md.append("## V10 Components (4-dimension quality)")
        md.append("")
        md.append("| Component | Corr | Low R | Mid R | High R | Spread | Signal |")
        md.append("|---|---|---|---|---|---|---|")
        for comp, stats in report["v10_components"]["components"].items():
            md.append(f"| {comp} | {stats['correlation']:+.3f} | {stats['low_tercile_r']:+.2f} | "
                      f"{stats['mid_tercile_r']:+.2f} | {stats['high_tercile_r']:+.2f} | "
                      f"{stats['spread']:+.2f} | {stats['signal']} |")
        md.append("")

    md.append("---")
    md.append(f"*{report['enriched_with_decision_trace']}/{report['sample_size']} trades enriched with decision trace*")
    return "\n".join(md)
