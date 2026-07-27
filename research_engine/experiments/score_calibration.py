"""
Q20: Score Calibration Research — Is score calibrated to observed outcomes?

Compares model confidence (raw_score, calibrated_probability, p_success)
against actual shadow trade outcomes to measure calibration error.

Produces:
    - Calibration error by score bucket
    - Reliability assessment
    - Recommendation: PROMOTE_CALIBRATION | KEEP_CURRENT_MODEL | INSUFFICIENT_DATA

This experiment does NOT modify:
    - ScoreCalibrator
    - ProbabilityEstimator
    - EV Model

It only produces research findings for human review.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


_SHADOW_DIR = Path("logs/research_shadow_trades")
_TRACE_DIR = Path("logs/decision_trace")
_OUTPUT_DIR = Path("analysis/reports")

_MIN_SAMPLE_SIZE = 20  # Minimum trades per bucket for statistical validity


def _load_jsonl_tree(directory: Path) -> list[dict]:
    """Load all JSONL records from a directory tree."""
    records = []
    if not directory.exists():
        return records
    for item in sorted(directory.rglob("*.jsonl")):
        for line in item.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
    return records


def _bucket_score(score: float) -> str:
    """Assign score to a calibration bucket."""
    if score < 0.35:
        return "0.00-0.35"
    if score < 0.45:
        return "0.35-0.45"
    if score < 0.55:
        return "0.45-0.55"
    if score < 0.65:
        return "0.55-0.65"
    if score < 0.75:
        return "0.65-0.75"
    return "0.75-1.00"


def _analyse_score_buckets(shadow_outcomes: list[dict]) -> dict[str, Any]:
    """
    Score Bucket Calibration — Monotonicity analysis.

    Determines whether higher scores consistently represent higher win probability.
    Returns bucket table + monotonicity assessment + recommendation.
    """
    import math

    # Define analysis buckets
    bucket_edges = [
        ("0.40-0.50", 0.40, 0.50),
        ("0.50-0.60", 0.50, 0.60),
        ("0.60-0.70", 0.60, 0.70),
        ("0.70-0.80", 0.70, 0.80),
        ("0.80+", 0.80, 2.0),
    ]

    bucket_table: list[dict] = []
    win_rates: list[float] = []
    sufficient_buckets = 0

    for label, lo, hi in bucket_edges:
        records = [s for s in shadow_outcomes if lo <= s["score"] < hi]
        n = len(records)

        if n == 0:
            bucket_table.append({
                "bucket": label, "n": 0, "avg_score": 0, "predicted_p": 0,
                "actual_wr": 0, "avg_r": 0, "calibration_error": 0,
                "confidence_interval": None, "sufficient_data": False,
            })
            continue

        wins = sum(1 for r in records if r["win"])
        actual_wr = wins / n
        avg_score = statistics.mean([r["score"] for r in records])
        avg_r = statistics.mean([r["r_multiple"] for r in records])
        predicted_p = avg_score  # Identity calibration
        error = actual_wr - predicted_p  # Signed: positive = underpredicting

        # Wilson confidence interval (95%)
        z = 1.96
        p_hat = actual_wr
        denom = 1 + z * z / n
        centre = (p_hat + z * z / (2 * n)) / denom
        margin = z * math.sqrt((p_hat * (1 - p_hat) + z * z / (4 * n)) / n) / denom
        ci_low = round(max(0.0, centre - margin), 4)
        ci_high = round(min(1.0, centre + margin), 4)

        sufficient = n >= _MIN_SAMPLE_SIZE
        if sufficient:
            win_rates.append(actual_wr)
            sufficient_buckets += 1

        bucket_table.append({
            "bucket": label,
            "n": n,
            "avg_score": round(avg_score, 4),
            "predicted_p": round(predicted_p, 4),
            "actual_wr": round(actual_wr, 4),
            "avg_r": round(avg_r, 4),
            "calibration_error": round(error, 4),
            "confidence_interval": [ci_low, ci_high],
            "sufficient_data": sufficient,
        })

    # ─── MONOTONICITY ASSESSMENT ──────────────────────────────────────
    if sufficient_buckets < 2:
        monotonicity = "UNKNOWN"
        mono_reasoning = f"Only {sufficient_buckets} buckets with sufficient data"
    else:
        # Check if win rates are monotonically increasing
        increases = sum(1 for i in range(1, len(win_rates)) if win_rates[i] > win_rates[i - 1])
        decreases = sum(1 for i in range(1, len(win_rates)) if win_rates[i] < win_rates[i - 1])
        pairs = len(win_rates) - 1

        if pairs == 0:
            monotonicity = "UNKNOWN"
            mono_reasoning = "Only one valid bucket"
        elif increases == pairs:
            monotonicity = "MONOTONIC"
            mono_reasoning = f"All {pairs} transitions are increasing ({[round(w, 3) for w in win_rates]})"
        elif increases > decreases:
            monotonicity = "PARTIALLY_MONOTONIC"
            mono_reasoning = f"{increases}/{pairs} transitions increasing, {decreases} decreasing ({[round(w, 3) for w in win_rates]})"
        else:
            monotonicity = "NON_MONOTONIC"
            mono_reasoning = f"Only {increases}/{pairs} transitions increasing ({[round(w, 3) for w in win_rates]})"

    # ─── BUCKET RECOMMENDATION ────────────────────────────────────────
    if sufficient_buckets < 2:
        bucket_recommendation = "INSUFFICIENT_DATA"
    elif monotonicity == "MONOTONIC":
        bucket_recommendation = "CALIBRATION_READY"
    elif monotonicity == "PARTIALLY_MONOTONIC":
        bucket_recommendation = "CALIBRATION_READY"
    else:
        bucket_recommendation = "SCORE_MODEL_REVIEW_REQUIRED"

    return {
        "bucket_table": bucket_table,
        "monotonicity": monotonicity,
        "monotonicity_reasoning": mono_reasoning,
        "sufficient_buckets": sufficient_buckets,
        "win_rate_sequence": [round(w, 4) for w in win_rates],
        "recommendation": bucket_recommendation,
    }


def run() -> dict[str, Any]:
    """
    Execute Q20 score calibration analysis.

    Returns structured research result with calibration assessment.
    """
    shadows = _load_jsonl_tree(_SHADOW_DIR)
    traces = _load_jsonl_tree(_TRACE_DIR)

    # Extract shadow outcomes
    shadow_outcomes: list[dict] = []
    for s in shadows:
        ds = s.get("decision_snapshot", {})
        outcome = s.get("simulated_outcome", {})
        if not outcome:
            continue
        shadow_outcomes.append({
            "score": ds.get("score", 0.0),
            "pattern": ds.get("pattern", ""),
            "r_multiple": outcome.get("pnl_r_multiple", 0.0),
            "win": outcome.get("pnl_r_multiple", 0.0) > 0,
        })

    # Extract trace probability data (for predicted p_success)
    trace_probs: list[dict] = []
    for t in traces:
        if t.get("p_success") is not None and t.get("score_neutral", 0) > 0:
            trace_probs.append({
                "score": t.get("score_neutral", 0),
                "p_success": t.get("p_success", 0),
                "score_strategy": t.get("score_strategy", 0),
            })

    # ─── CALIBRATION BY SCORE BUCKET ──────────────────────────────────
    by_bucket: dict[str, list[dict]] = defaultdict(list)
    for s in shadow_outcomes:
        by_bucket[_bucket_score(s["score"])].append(s)

    calibration_table: list[dict] = []
    total_error = 0.0
    valid_buckets = 0

    for bucket in ["0.00-0.35", "0.35-0.45", "0.45-0.55", "0.55-0.65", "0.65-0.75", "0.75-1.00"]:
        records = by_bucket.get(bucket, [])
        n = len(records)
        if n == 0:
            calibration_table.append({
                "bucket": bucket, "n": 0, "predicted_p": 0, "actual_wr": 0,
                "calibration_error": 0, "sufficient_data": False,
            })
            continue

        actual_wr = sum(1 for r in records if r["win"]) / n
        avg_score = statistics.mean([r["score"] for r in records])
        # Current model: predicted_p ≈ score (identity calibration)
        predicted_p = avg_score
        error = abs(actual_wr - predicted_p)
        sufficient = n >= _MIN_SAMPLE_SIZE

        if sufficient:
            total_error += error
            valid_buckets += 1

        avg_r = statistics.mean([r["r_multiple"] for r in records])

        calibration_table.append({
            "bucket": bucket,
            "n": n,
            "predicted_p": round(predicted_p, 4),
            "actual_wr": round(actual_wr, 4),
            "calibration_error": round(error, 4),
            "avg_r": round(avg_r, 4),
            "sufficient_data": sufficient,
        })

    # ─── OVERALL METRICS ──────────────────────────────────────────────
    mean_calibration_error = total_error / max(valid_buckets, 1)

    overall_predicted = statistics.mean([t["p_success"] for t in trace_probs]) if trace_probs else 0
    overall_actual = sum(1 for s in shadow_outcomes if s["win"]) / max(len(shadow_outcomes), 1)
    overall_gap = overall_actual - overall_predicted

    # ─── RECOMMENDATION ───────────────────────────────────────────────
    if len(shadow_outcomes) < 50:
        recommendation = "INSUFFICIENT_DATA"
        reasoning = f"Only {len(shadow_outcomes)} shadow trades. Need 50+ for reliable calibration."
    elif mean_calibration_error > 0.10:
        recommendation = "PROMOTE_CALIBRATION"
        reasoning = (
            f"Mean calibration error = {mean_calibration_error:.4f} (>10%). "
            f"Score-based probability diverges significantly from observed outcomes. "
            f"Empirical calibration table should be applied to ScoreCalibrator."
        )
    else:
        recommendation = "KEEP_CURRENT_MODEL"
        reasoning = (
            f"Mean calibration error = {mean_calibration_error:.4f} (<=10%). "
            f"Current identity mapping is acceptably calibrated."
        )

    # ─── SCORE BUCKET CALIBRATION (monotonicity analysis) ─────────────
    bucket_analysis = _analyse_score_buckets(shadow_outcomes)

    # ─── BUILD CANONICAL REPORT ─────────────────────────────────────
    from research_engine.experiments.experiment_base import build_report, build_fingerprint

    report = build_report(
        question_id="Q20",
        status="COMPLETE" if len(shadow_outcomes) >= 50 else "INSUFFICIENT_DATA",
        overall={
            "shadow_trades_analysed": len(shadow_outcomes),
            "trace_probability_records": len(trace_probs),
            "valid_calibration_buckets": valid_buckets,
            "calibration_table": calibration_table,
            "overall_predicted_p": round(overall_predicted, 4),
            "overall_actual_wr": round(overall_actual, 4),
            "overall_gap": round(overall_gap, 4),
            "mean_calibration_error": round(mean_calibration_error, 4),
            "monotonicity": bucket_analysis.get("monotonicity", "UNKNOWN"),
            "finding": reasoning,
        },
        confidence="HIGH" if len(shadow_outcomes) >= 100 else "MEDIUM" if len(shadow_outcomes) >= 50 else "INSUFFICIENT_DATA",
        dataset={"source": "research_shadow_trades + decision_trace", "sample_size": len(shadow_outcomes)},
        fingerprint=build_fingerprint(len(shadow_outcomes), 0, "shadow_trades+decision_trace"),
        recommendation=recommendation,
        assumptions=["Identity calibration: predicted_p = score", "Bucket width: variable (0.10-0.25)"],
        provenance={"experiment_module": "research_engine.experiments.score_calibration", "registry_id": "Q20", "function": "run", "pipeline": "Question -> Experiment -> Dataset -> Output -> Knowledge -> Command Centre"},
    )

    # Persist
    try:
        from research_engine.experiments.experiment_base import persist_report as eb_persist
        eb_persist(report, "q20_score_calibration.json")
    except Exception:
        pass

    # Generate calibration artifact if promotion recommended
    try:
        from research_engine.report_builder import generate_calibration_artifact
        if recommendation == "PROMOTE_CALIBRATION":
            bucket_table = bucket_analysis.get("bucket_table", [])
            generate_calibration_artifact(bucket_table)
    except Exception:
        pass

    return report


if __name__ == "__main__":
    import pprint
    result = run()
    pprint.pprint(result)
