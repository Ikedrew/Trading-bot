"""
V10-D2: EV Calibration

Question: "Are V10's expected value and probability estimates calibrated to real outcomes?"

Tests whether higher predicted probability/EV corresponds to better actual results.
Identifies whether the system is overconfident, underconfident, or well calibrated.
"""

from __future__ import annotations

import statistics
from typing import Any

from research_engine.v10.base import compute_metrics, classify_confidence, timestamp_now
from research_engine.v10.dataset import DatasetView, load_trades, enrich_with_decision_trace


def run(view: DatasetView = DatasetView.FULL, trades: list[dict] | None = None) -> dict[str, Any]:
    """
    Run V10-D2: EV Calibration.

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

    # Enrich with decision trace
    enriched_count = enrich_with_decision_trace(trades)

    n_total = len(trades)
    if n_total == 0:
        return _empty_report(view)

    baseline = compute_metrics(trades)

    # Split: trades WITH EV data vs without
    ev_trades = [t for t in trades if (t.get("dt_ev") or 0) != 0 or (t.get("dt_p_success") or 0) > 0]
    no_ev_trades = [t for t in trades if t not in ev_trades]

    # ─── PROBABILITY CALIBRATION ─────────────────────────────
    p_buckets = [("0.0-0.2", 0.0, 0.2), ("0.2-0.3", 0.2, 0.3), ("0.3-0.4", 0.3, 0.4), ("0.4-0.6", 0.4, 0.6), ("0.6-1.0", 0.6, 1.0)]
    probability_calibration = {}
    for label, lo, hi in p_buckets:
        group = [t for t in ev_trades if lo <= (t.get("dt_p_success") or 0) < hi]
        if not group:
            continue
        actual_win = sum(1 for t in group if t.get("realised_r", 0) > 0) / len(group)
        avg_predicted = statistics.mean([t.get("dt_p_success") or 0 for t in group])
        avg_r = statistics.mean([t.get("realised_r", 0) for t in group])
        error = actual_win - avg_predicted
        probability_calibration[label] = {
            "count": len(group),
            "avg_predicted_p": round(avg_predicted, 4),
            "actual_win_rate": round(actual_win, 4),
            "calibration_error": round(error, 4),
            "direction": "underconfident" if error > 0.05 else ("overconfident" if error < -0.05 else "calibrated"),
            "average_r": round(avg_r, 4),
            "confidence": classify_confidence(len(group)),
        }

    # ─── EV CALIBRATION ──────────────────────────────────────
    pos_ev = [t for t in ev_trades if (t.get("dt_ev") or 0) > 0]
    neg_ev = [t for t in ev_trades if (t.get("dt_ev") or 0) < 0]

    ev_analysis = {
        "total_with_ev": len(ev_trades),
        "positive_ev_count": len(pos_ev),
        "negative_ev_count": len(neg_ev),
        "positive_ev_avg_r": round(statistics.mean([t.get("realised_r", 0) for t in pos_ev]), 4) if pos_ev else 0,
        "negative_ev_avg_r": round(statistics.mean([t.get("realised_r", 0) for t in neg_ev]), 4) if neg_ev else 0,
        "positive_ev_win_rate": round(sum(1 for t in pos_ev if t.get("realised_r", 0) > 0) / max(len(pos_ev), 1), 4),
        "negative_ev_win_rate": round(sum(1 for t in neg_ev if t.get("realised_r", 0) > 0) / max(len(neg_ev), 1), 4),
    }
    ev_gap = ev_analysis["positive_ev_avg_r"] - ev_analysis["negative_ev_avg_r"]
    ev_analysis["ev_gap_r"] = round(ev_gap, 4)

    # Average predicted vs realised
    if ev_trades:
        avg_predicted_ev = statistics.mean([t.get("dt_ev") or 0 for t in ev_trades])
        avg_realised_r = statistics.mean([t.get("realised_r", 0) for t in ev_trades])
        ev_analysis["avg_predicted_ev"] = round(avg_predicted_ev, 6)
        ev_analysis["avg_realised_r"] = round(avg_realised_r, 4)
        ev_analysis["prediction_bias"] = round(avg_realised_r - avg_predicted_ev, 4)

    # ─── EV GATE ANALYSIS ────────────────────────────────────
    # What would happen if we only took positive EV trades?
    gate_analysis = {
        "if_positive_ev_only": compute_metrics(pos_ev) if pos_ev else {"count": 0},
        "if_negative_ev_only": compute_metrics(neg_ev) if neg_ev else {"count": 0},
        "all_ev_trades": compute_metrics(ev_trades) if ev_trades else {"count": 0},
    }

    # ─── SCORE vs EV COMPARISON ──────────────────────────────
    # Which is more predictive: score or EV?
    r_vals = [t.get("realised_r", 0) for t in ev_trades]
    score_vals = [t.get("dt_score") or 0 for t in ev_trades]
    ev_vals_numeric = [t.get("dt_ev") or 0 for t in ev_trades]
    p_vals = [t.get("dt_p_success") or 0 for t in ev_trades]

    predictor_comparison = {
        "score_correlation": round(_corr(score_vals, r_vals), 4),
        "ev_correlation": round(_corr(ev_vals_numeric, r_vals), 4),
        "p_success_correlation": round(_corr(p_vals, r_vals), 4),
    }
    best_predictor = max(predictor_comparison.items(), key=lambda x: abs(x[1]))

    # ─── CONCLUSION ──────────────────────────────────────────
    overconfident_count = sum(1 for v in probability_calibration.values() if v["direction"] == "overconfident")
    underconfident_count = sum(1 for v in probability_calibration.values() if v["direction"] == "underconfident")
    calibrated_count = sum(1 for v in probability_calibration.values() if v["direction"] == "calibrated")

    if ev_gap > 0.3 and calibrated_count >= underconfident_count:
        conclusion = "EV_CALIBRATED"
        conclusion_reason = f"Positive EV outperforms negative by {ev_gap:.2f}R and probability estimates are reasonable"
    elif underconfident_count > overconfident_count and underconfident_count >= 2:
        conclusion = "EV_UNDERCONFIDENT"
        conclusion_reason = f"System predicts lower win probability than actual ({underconfident_count} buckets underconfident)"
    elif overconfident_count > underconfident_count and overconfident_count >= 2:
        conclusion = "EV_OVERCONFIDENT"
        conclusion_reason = f"System predicts higher win probability than actual ({overconfident_count} buckets overconfident)"
    elif abs(predictor_comparison["ev_correlation"]) < 0.05 and abs(predictor_comparison["score_correlation"]) < 0.05:
        conclusion = "EV_NOT_PREDICTIVE"
        conclusion_reason = "Neither EV nor score correlate meaningfully with outcomes"
    else:
        conclusion = "INCONCLUSIVE"
        conclusion_reason = "Mixed signals — some calibration issues but no clear pattern"

    report = {
        "research_id": "V10-D2",
        "title": "EV Calibration",
        "generated_utc": timestamp_now(),
        "dataset_view": view.value,
        "sample_size": n_total,
        "ev_trades": len(ev_trades),
        "non_ev_trades": len(no_ev_trades),
        "enriched": enriched_count,
        "conclusion": conclusion,
        "conclusion_reason": conclusion_reason,
        "metrics": baseline,
        "probability_calibration": probability_calibration,
        "ev_analysis": ev_analysis,
        "gate_analysis": gate_analysis,
        "predictor_comparison": predictor_comparison,
        "best_predictor": {"name": best_predictor[0], "correlation": best_predictor[1]},
    }

    report["markdown"] = _build_markdown(report)
    return report


def _corr(xs: list[float], ys: list[float]) -> float:
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
        "research_id": "V10-D2", "title": "EV Calibration",
        "generated_utc": timestamp_now(), "dataset_view": view.value,
        "sample_size": 0, "conclusion": "NO_DATA",
        "metrics": {"count": 0},
        "markdown": f"# V10-D2: No data for {view.value}",
    }


def _build_markdown(report: dict) -> str:
    md = []
    md.append(f"# V10-D2: EV Calibration ({report['dataset_view']})")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append(f"Sample: {report['sample_size']} total | {report['ev_trades']} with EV data")
    md.append("")
    md.append(f"## Conclusion: {report['conclusion']}")
    md.append("")
    md.append(report["conclusion_reason"])
    md.append("")

    # Probability calibration
    md.append("## Probability Calibration")
    md.append("")
    md.append("| P Bucket | N | Predicted | Actual Win% | Error | Direction | Avg R |")
    md.append("|---|---|---|---|---|---|---|")
    for label, stats in report["probability_calibration"].items():
        md.append(f"| {label} | {stats['count']} | {stats['avg_predicted_p']:.1%} | "
                  f"{stats['actual_win_rate']:.1%} | {stats['calibration_error']:+.1%} | "
                  f"{stats['direction']} | {stats['average_r']:+.2f} |")

    # EV analysis
    md.append("")
    md.append("## EV Analysis")
    md.append("")
    ev = report["ev_analysis"]
    md.append(f"| Metric | Value |")
    md.append(f"|---|---|")
    md.append(f"| Positive EV trades | {ev['positive_ev_count']} (avg R: {ev['positive_ev_avg_r']:+.4f}) |")
    md.append(f"| Negative EV trades | {ev['negative_ev_count']} (avg R: {ev['negative_ev_avg_r']:+.4f}) |")
    md.append(f"| EV gap | {ev['ev_gap_r']:+.4f}R |")
    if "avg_predicted_ev" in ev:
        md.append(f"| Avg predicted EV | {ev['avg_predicted_ev']:.6f} |")
        md.append(f"| Avg realised R | {ev['avg_realised_r']:+.4f} |")

    # Predictor comparison
    md.append("")
    md.append("## Predictor Comparison")
    md.append("")
    md.append("| Predictor | Correlation with R |")
    md.append("|---|---|")
    for name, corr in sorted(report["predictor_comparison"].items(), key=lambda x: -abs(x[1])):
        md.append(f"| {name} | {corr:+.4f} |")
    md.append(f"\nBest: **{report['best_predictor']['name']}** ({report['best_predictor']['correlation']:+.4f})")

    md.append("")
    md.append("---")
    md.append(f"*{report['ev_trades']}/{report['sample_size']} trades have EV/probability data*")
    return "\n".join(md)
