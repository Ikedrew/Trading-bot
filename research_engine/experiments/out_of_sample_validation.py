"""
E5 — Out-of-Sample Validation Experiment.

Question:
    Does the measured edge survive on unseen market data using
    walk-forward testing with rolling windows?

Implements:
    - Train/validation split
    - Rolling walk-forward windows
    - Expanding window
    - Stability score
    - Drift detection

This module is PURELY RESEARCH. It does NOT modify trading logic.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from research_engine.experiments.experiment_base import (
    ReadinessStatus,
    build_fingerprint,
    build_report,
    check_readiness,
    compute_confidence,
    extract_r_multiples,
    load_shadow_trades,
    persist_report,
    update_knowledge_map,
)

_MIN_SAMPLES = 80  # Need enough for meaningful train/test split
_TRAIN_FRACTION = 0.60
_NUM_ROLLING_WINDOWS = 5
_STABILITY_THRESHOLD = 0.70  # 70% of windows must show positive EV


def _compute_window_ev(r_values: list[float]) -> dict[str, Any]:
    """Compute EV and win rate for a window of R-multiples."""
    if not r_values:
        return {"ev": 0.0, "win_rate": 0.0, "n": 0, "positive": False}
    n = len(r_values)
    ev = sum(r_values) / n
    wr = sum(1 for r in r_values if r > 0) / n
    return {"ev": round(ev, 4), "win_rate": round(wr, 4), "n": n, "positive": ev > 0}


def _train_test_split(r_values: list[float]) -> dict[str, Any]:
    """Simple chronological train/test split."""
    n = len(r_values)
    split_idx = int(n * _TRAIN_FRACTION)
    train = r_values[:split_idx]
    test = r_values[split_idx:]
    train_stats = _compute_window_ev(train)
    test_stats = _compute_window_ev(test)
    drift = abs(train_stats["ev"] - test_stats["ev"])
    return {
        "train": train_stats,
        "test": test_stats,
        "drift": round(drift, 4),
        "edge_survives": test_stats["positive"],
    }


def _rolling_walk_forward(r_values: list[float], num_windows: int) -> list[dict[str, Any]]:
    """Rolling walk-forward: divide into windows and test each."""
    n = len(r_values)
    window_size = n // num_windows
    if window_size < 10:
        return []

    windows: list[dict[str, Any]] = []
    for i in range(num_windows):
        start = i * window_size
        end = start + window_size if i < num_windows - 1 else n
        window_data = r_values[start:end]
        stats = _compute_window_ev(window_data)
        stats["window"] = i + 1
        stats["start_idx"] = start
        stats["end_idx"] = end
        windows.append(stats)

    return windows


def _expanding_window(r_values: list[float], num_checkpoints: int = 5) -> list[dict[str, Any]]:
    """Expanding window: test at increasing data sizes."""
    n = len(r_values)
    results: list[dict[str, Any]] = []
    for i in range(1, num_checkpoints + 1):
        end_idx = int(n * i / num_checkpoints)
        subset = r_values[:end_idx]
        stats = _compute_window_ev(subset)
        stats["data_fraction"] = round(i / num_checkpoints, 2)
        stats["records"] = end_idx
        results.append(stats)
    return results


def run_out_of_sample_validation(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Run E5: Out-of-Sample Validation experiment."""
    if shadow_trades is None:
        shadow_trades = load_shadow_trades()

    status, reason, coverage = check_readiness(
        shadow_trades, min_samples=_MIN_SAMPLES, require_lineage=True, require_outcome=True,
    )
    if status != ReadinessStatus.READY:
        return build_report(
            question_id="E5", status=status, overall={"reason": reason},
            confidence="INSUFFICIENT_DATA",
            dataset={"records_available": len(shadow_trades), "coverage": coverage},
            fingerprint=build_fingerprint(0, len(shadow_trades)), recommendation="WAIT", warnings=[reason],
        )

    r_values = extract_r_multiples(shadow_trades)
    n = len(r_values)
    if n < _MIN_SAMPLES:
        return build_report(
            question_id="E5", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"reason": f"Only {n} R-multiples (need {_MIN_SAMPLES})"},
            confidence="INSUFFICIENT_DATA", dataset={"r_multiples": n},
            fingerprint=build_fingerprint(n, len(shadow_trades) - n), recommendation="WAIT",
        )

    # Analyses
    split_result = _train_test_split(r_values)
    rolling_windows = _rolling_walk_forward(r_values, _NUM_ROLLING_WINDOWS)
    expanding = _expanding_window(r_values)

    # Stability score: fraction of rolling windows with positive EV
    positive_windows = sum(1 for w in rolling_windows if w["positive"]) if rolling_windows else 0
    total_windows = len(rolling_windows) if rolling_windows else 1
    stability_score = positive_windows / total_windows

    # Overall EV
    overall_ev = sum(r_values) / n
    train_ev = split_result["train"]["ev"]
    test_ev = split_result["test"]["ev"]

    # Drift: difference between train and test
    drift = split_result["drift"]
    drift_significant = drift > abs(overall_ev) * 0.5  # Drift > 50% of overall EV = concerning

    confidence = compute_confidence(n, split_result["edge_survives"] and stability_score >= _STABILITY_THRESHOLD)

    # Recommendation
    if split_result["edge_survives"] and stability_score >= _STABILITY_THRESHOLD:
        recommendation = "PROMOTE"
        finding = f"Edge survives out-of-sample. Train EV={train_ev:+.4f}R, Test EV={test_ev:+.4f}R. Stability={stability_score:.0%}."
    elif split_result["edge_survives"]:
        recommendation = "MONITOR"
        finding = f"Edge survives test but stability is low ({stability_score:.0%}). More data needed."
    else:
        recommendation = "REJECT"
        finding = f"Edge does NOT survive out-of-sample. Train={train_ev:+.4f}R, Test={test_ev:+.4f}R. Possible overfit."

    report = build_report(
        question_id="E5", status=ReadinessStatus.COMPLETE,
        overall={
            "in_sample_ev": round(train_ev, 4),
            "out_of_sample_ev": round(test_ev, 4),
            "overall_ev": round(overall_ev, 4),
            "drift": round(drift, 4),
            "drift_significant": drift_significant,
            "stability_score": round(stability_score, 4),
            "edge_survives": split_result["edge_survives"],
            "split_result": split_result,
            "rolling_windows": rolling_windows,
            "expanding_window": expanding,
        },
        confidence=confidence,
        dataset={"total_records": len(shadow_trades), "r_multiples_used": n, "train_size": int(n * _TRAIN_FRACTION), "test_size": n - int(n * _TRAIN_FRACTION), "coverage": coverage},
        fingerprint=build_fingerprint(n, len(shadow_trades) - n),
        recommendation=recommendation,
        assumptions=[f"Train/test split: {_TRAIN_FRACTION:.0%}/{1-_TRAIN_FRACTION:.0%} chronological", f"Rolling windows: {_NUM_ROLLING_WINDOWS}", f"Stability threshold: {_STABILITY_THRESHOLD:.0%} positive windows"],
        warnings=[w for w in [f"Drift detected: {drift:.4f}" if drift_significant else "", f"Low stability: {stability_score:.0%}" if stability_score < _STABILITY_THRESHOLD else ""] if w],
        provenance={"experiment_module": "research_engine.experiments.out_of_sample_validation", "registry_id": "E5", "function": "run_out_of_sample_validation", "pipeline": "Question → Experiment → Dataset → Output → Knowledge → Command Centre"},
    )

    persist_report(report, "e5_out_of_sample.json")
    update_knowledge_map("E5", finding, recommendation)
    return report


if __name__ == "__main__":
    result = run_out_of_sample_validation()
    o = result.get("overall", {})
    print(f"E5: train={o.get('in_sample_ev', '?')} test={o.get('out_of_sample_ev', '?')} stability={o.get('stability_score', '?')} rec={result.get('recommendation')}")
