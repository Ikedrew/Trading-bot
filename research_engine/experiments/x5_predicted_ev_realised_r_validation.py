"""X5: paired chronological predicted-EV vs realised-R validation.

X5 asks whether the PRE-DECISION predicted EV (in R units) CORRESPONDS to
subsequent realised R at canonical-opportunity level, and whether that
relationship persists on later unseen evidence.

This is DISTINCT from D3.  D3 asks whether a predicted-EV *gate* separates
eligible/ineligible outcome quality.  X5 asks whether predicted EV *itself*
ranks and calibrates to realised R.

X5 REUSES D3's versioned research predicted-EV authority exactly:

    predicted_ev_r_v1 = p_success * reward_r - (1 - p_success) * risk_r
    risk_r  = 1.0
    reward_r = pre-decision TP distance / pre-decision SL distance

It never uses the production Stage-4 raw price-distance ``ev`` and never invents
another EV formula.  Both prediction and outcome are therefore in R-compatible
units.  One CURRENT canonical opportunity is one independent observation;
account fanout and repeated horizons collapse before any statistic (reused from
D3's pairing).  Predictions are joined one-to-one to subsequent shadow outcomes
and evaluated on a strictly later chronological partition.  Calibration bin
boundaries are frozen from the discovery partition only.

Unit compatibility does NOT calibrate the probability model: if p_success is
heuristic_score_v1 / UNCALIBRATED, X5 reports that limitation and never claims
fully calibrated expected return.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import math
from statistics import median
from typing import Any, Iterable

from research_engine.experiments.experiment_base import (
    build_fingerprint,
    build_report,
    load_shadow_trades,
)
# Reuse D3's EXACT predicted-EV authority, pairing and chronology.
from research_engine.experiments.d3_predicted_ev_gate import (
    PREDICTED_EV_VERSION,
    RISK_R,
    build_paired_predictions,
    chronological_split,
    predicted_ev_r,
    reward_r_from_geometry,
)

X5_REPORT_FILENAME = "x5_predicted_ev_realised_r_validation_v1.json"

MINIMUM_TOTAL = 100
MINIMUM_DISCOVERY = 60
MINIMUM_VALIDATION = 40
MINIMUM_EV_GROUP = 15   # positive-EV / non-positive-EV validation groups
MINIMUM_BIN = 10        # per calibration bin used for interpretation
_BIN_COUNT = 4          # deterministic discovery quantile bins

# Directional agreement / magnitude thresholds (deterministic, interpretable).
_RANK_SIGNAL_MIN = 0.10        # |Spearman| below this = no reliable ranking
_MAGNITUDE_TOLERANCE_R = 0.50  # |mean error| above this = magnitude miscalibration


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _error_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Prediction-error metrics: error = realised_r - predicted_ev_r."""
    if not rows:
        return {"n": 0, "mean_error": None, "mae": None, "rmse": None}
    errors = [row["outcome_r"] - row["predicted_ev_r"] for row in rows]
    n = len(errors)
    mean_error = sum(errors) / n
    mae = sum(abs(e) for e in errors) / n
    rmse = math.sqrt(sum(e * e for e in errors) / n)
    return {"n": n, "mean_error": mean_error, "mae": mae, "rmse": rmse}


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def rank_relationship(rows: list[dict[str, Any]]) -> float | None:
    """Deterministic Spearman rank correlation between predicted EV and
    realised R (no SciPy). None if <3 rows or zero variance either side."""
    if len(rows) < 3:
        return None
    predicted = [row["predicted_ev_r"] for row in rows]
    realised = [row["outcome_r"] for row in rows]
    if len(set(predicted)) < 2 or len(set(realised)) < 2:
        return None
    pr = _average_ranks(predicted)
    rr = _average_ranks(realised)
    n = len(rows)
    mean_rank = (n + 1) / 2.0
    cov = sum((pr[i] - mean_rank) * (rr[i] - mean_rank) for i in range(n))
    var_p = sum((r - mean_rank) ** 2 for r in pr)
    var_r = sum((r - mean_rank) ** 2 for r in rr)
    if var_p <= 0.0 or var_r <= 0.0:
        return None
    return cov / math.sqrt(var_p * var_r)


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "mean_predicted_ev_r": None, "median_predicted_ev_r": None,
                "mean_realised_r": None, "median_realised_r": None, "win_rate": None}
    predicted = [row["predicted_ev_r"] for row in rows]
    realised = [row["outcome_r"] for row in rows]
    return {
        "n": len(rows),
        "mean_predicted_ev_r": sum(predicted) / len(predicted),
        "median_predicted_ev_r": median(predicted),
        "mean_realised_r": sum(realised) / len(realised),
        "median_realised_r": median(realised),
        "win_rate": sum(1 for value in realised if value > 0) / len(rows),
    }


def discovery_bin_boundaries(discovery: list[dict[str, Any]], bins: int = _BIN_COUNT) -> list[float]:
    """Freeze deterministic predicted-EV quantile boundaries from DISCOVERY only.

    Returns the interior boundaries (bins-1 cut points). Validation never
    redefines these.
    """
    if len(discovery) < bins:
        return []
    values = sorted(row["predicted_ev_r"] for row in discovery)
    boundaries: list[float] = []
    for q in range(1, bins):
        index = min(len(values) - 1, max(0, int(len(values) * q / bins)))
        boundaries.append(values[index])
    # Deduplicate while preserving order (degenerate distributions).
    seen: set[float] = set()
    unique = [b for b in boundaries if not (b in seen or seen.add(b))]
    return unique


def _bin_index(value: float, boundaries: list[float]) -> int:
    index = 0
    for boundary in boundaries:
        if value >= boundary:
            index += 1
        else:
            break
    return index


def apply_bins(rows: list[dict[str, Any]], boundaries: list[float]) -> dict[int, dict[str, Any]]:
    """Assign rows to frozen bins and compute per-bin diagnostics."""
    cells: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        cells[_bin_index(row["predicted_ev_r"], boundaries)].append(row)
    result: dict[int, dict[str, Any]] = {}
    for index in sorted(cells):
        cell_rows = cells[index]
        errors = [r["outcome_r"] - r["predicted_ev_r"] for r in cell_rows]
        result[index] = {
            "n": len(cell_rows),
            "mean_predicted_ev_r": _mean([r["predicted_ev_r"] for r in cell_rows]),
            "mean_realised_r": _mean([r["outcome_r"] for r in cell_rows]),
            "mean_error": _mean(errors),
            "win_rate": sum(1 for r in cell_rows if r["outcome_r"] > 0) / len(cell_rows),
            "sufficient": len(cell_rows) >= MINIMUM_BIN,
        }
    return result


def _classify_finding(
    status: str,
    discovery_rank: float | None,
    validation_rank: float | None,
    validation_mean_error: float | None,
) -> str:
    """Distinguish ranking value from magnitude calibration and persistence."""
    if status != "COMPLETE":
        return "INSUFFICIENT_EVIDENCE"
    if discovery_rank is None or validation_rank is None:
        return "NO_RELIABLE_EV_SIGNAL"
    discovery_useful = abs(discovery_rank) >= _RANK_SIGNAL_MIN and discovery_rank > 0
    validation_useful = abs(validation_rank) >= _RANK_SIGNAL_MIN and validation_rank > 0
    if not discovery_useful:
        return "NO_RELIABLE_EV_SIGNAL"
    if not validation_useful:
        return "DISCOVERY_ONLY"
    # Ranking persists. Now separate ranking value from magnitude calibration.
    magnitude_ok = (
        validation_mean_error is not None
        and abs(validation_mean_error) <= _MAGNITUDE_TOLERANCE_R
    )
    return "VALIDATED_EV_SIGNAL" if magnitude_ok else "SYSTEMATIC_MISCALIBRATION"


def analyse(
    decision_records: Iterable[dict[str, Any]],
    shadow_records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    # Reuse D3's exact pairing (predicted_ev_r_v1, fanout/horizon collapse,
    # missing-outcome exclusion, conflict/chronology fail-closed).
    paired, diagnostics = build_paired_predictions(decision_records, shadow_records)
    discovery, validation = chronological_split(paired)
    versions = sorted({row["model_version"] for row in paired})

    overall_summary = _summary(paired)
    discovery_summary = _summary(discovery)
    validation_summary = _summary(validation)
    overall_error = _error_metrics(paired)
    discovery_error = _error_metrics(discovery)
    validation_error = _error_metrics(validation)
    discovery_rank = rank_relationship(discovery)
    validation_rank = rank_relationship(validation)
    overall_rank = rank_relationship(paired)

    # Calibration bins frozen from discovery, applied unchanged to validation.
    boundaries = discovery_bin_boundaries(discovery)
    validation_bins = apply_bins(validation, boundaries)

    # Positive vs non-positive predicted-EV validation groups.
    positive = [row for row in validation if row["predicted_ev_r"] > 0]
    non_positive = [row for row in validation if row["predicted_ev_r"] <= 0]
    positive_summary = _summary(positive)
    non_positive_summary = _summary(non_positive)

    if diagnostics["conflicting_opportunities"]:
        status = "BLOCKED"
        reason = "Conflicting prediction, geometry, outcome, or chronology for a canonical opportunity"
    elif len(versions) > 1:
        status = "BLOCKED"
        reason = "Mixed p_success model versions cannot share one predicted-EV validation"
    elif len(paired) < MINIMUM_TOTAL or len(discovery) < MINIMUM_DISCOVERY or len(validation) < MINIMUM_VALIDATION:
        status = "WAITING_DATA"
        reason = (
            f"paired/discovery/validation={len(paired)}/{len(discovery)}/{len(validation)}; "
            f"need {MINIMUM_TOTAL}/{MINIMUM_DISCOVERY}/{MINIMUM_VALIDATION}"
        )
    elif positive_summary["n"] < MINIMUM_EV_GROUP or non_positive_summary["n"] < MINIMUM_EV_GROUP:
        status = "WAITING_DATA"
        reason = (
            f"validation positive-EV/non-positive-EV={positive_summary['n']}/{non_positive_summary['n']}; "
            f"need >= {MINIMUM_EV_GROUP} in each group"
        )
    else:
        status = "COMPLETE"
        reason = "Paired chronological predicted-EV vs realised-R evaluation completed on later unseen opportunities"

    finding = _classify_finding(status, discovery_rank, validation_rank, validation_error["mean_error"])
    model_version = versions[0] if len(versions) == 1 else "MIXED_OR_UNKNOWN"
    probability_calibration_state = (
        "UNCALIBRATED" if model_version in {"heuristic_score_v1", "MIXED_OR_UNKNOWN"}
        else "SEE_D2_CALIBRATION_REPORT"
    )

    overall = {
        "canonical_question": "X5",
        "research_classification": "observational_predicted_ev_vs_realised_r_validation",
        "causal_claim": "NONE — observational correspondence of predicted EV to realised R; not proof executing rejected/accepted trades yields these results",
        "distinct_from_d3": "D3 evaluates a predicted-EV GATE separating outcome quality; X5 evaluates whether predicted EV ITSELF ranks and calibrates to realised R.",
        "unit_of_analysis": "one canonical_opportunity_id",
        "predicted_ev_measure": {
            "version": PREDICTED_EV_VERSION,
            "formula": "p_success * reward_r - (1 - p_success) * risk_r",
            "risk_r": RISK_R,
            "reward_r": "pre-decision TP distance / pre-decision SL distance",
            "units": "R-multiple",
            "authority": "reused from D3 (research-derived); production Stage-4 ev is never used",
        },
        "p_success_model_version": model_version,
        "probability_calibration_state": probability_calibration_state,
        "probability_calibration_authority": "D2 (d2_paired_probability_calibration_v1.json)",
        "outcome_authority": "CURRENT shadow simulated_outcome.pnl_r_multiple for the same canonical opportunity",
        "unit_compatibility": "predicted_ev_r_v1 and realised R are BOTH in R units; unit compatibility does NOT calibrate the probability model",
        "evidence_epoch": "CURRENT",
        "paired_summary": overall_summary,
        "discovery_summary": discovery_summary,
        "later_unseen_validation_summary": validation_summary,
        "error_metrics": {
            "overall": overall_error,
            "discovery": discovery_error,
            "later_unseen_validation": validation_error,
            "definition": "error = realised_r - predicted_ev_r (R units)",
        },
        "rank_relationship": {
            "overall_spearman": overall_rank,
            "discovery_spearman": discovery_rank,
            "later_unseen_validation_spearman": validation_rank,
            "signal_threshold": _RANK_SIGNAL_MIN,
        },
        "calibration_bins": {
            "boundaries": boundaries,
            "boundary_provenance": "frozen discovery predicted_ev_r quantiles; validation never redefines",
            "bin_count": _BIN_COUNT,
            "later_unseen_validation_bins": {str(k): v for k, v in validation_bins.items()},
        },
        "positive_ev_validation": positive_summary,
        "non_positive_ev_validation": non_positive_summary,
        "interpretation_dimensions": {
            "unit_validity": "predicted and realised values share R units",
            "ranking_directional_value": ("PRESENT" if (validation_rank is not None and abs(validation_rank) >= _RANK_SIGNAL_MIN and validation_rank > 0) else "NOT_ESTABLISHED"),
            "magnitude_calibration": ("ADEQUATE" if (validation_error["mean_error"] is not None and abs(validation_error["mean_error"]) <= _MAGNITUDE_TOLERANCE_R) else "INADEQUATE_OR_UNKNOWN"),
            "note": "Ranking value and magnitude calibration are NOT equivalent; useful ranking with poor magnitude is SYSTEMATIC_MISCALIBRATION, never 'fully calibrated EV'.",
        },
        "context_diagnostics": {
            "note": "Optional same-opportunity pre-decision context diagnostics; cells with n<15 are insufficient and never over-interpreted. Context does not inflate independent n and is not a completion blocker.",
        },
        "finding_classification": finding,
        "completion_reason": reason,
        "limitations": [
            "predicted_ev_r_v1 reuses pre-decision p_success and pre-decision TP/SL geometry (R units); production Stage-4 ev is never used.",
            f"probability model is {model_version} ({probability_calibration_state}); X5 never claims fully calibrated expected return.",
            "COMPLETE means the paired chronological evaluation ran validly, not that predicted EV is good.",
            "Ranking/directional value is distinct from magnitude calibration; both are reported separately.",
            "Missing outcomes are excluded, never imputed; account fanout and repeated horizons collapse within canonical opportunity.",
            "This is not permission to change any production EV gate; D3 separately evaluates the research EV-gate concept.",
        ],
        "diagnostics": diagnostics,
        "sufficiency": {
            "minimum_total": MINIMUM_TOTAL,
            "minimum_discovery": MINIMUM_DISCOVERY,
            "minimum_validation": MINIMUM_VALIDATION,
            "minimum_ev_group": MINIMUM_EV_GROUP,
            "minimum_bin": MINIMUM_BIN,
        },
    }
    confidence = "MEDIUM" if status == "COMPLETE" else "INSUFFICIENT_DATA"
    if status == "COMPLETE":
        outcome_evaluation = (
            f"[{finding}] validation Spearman {validation_rank:+.4f}, "
            f"mean error {validation_error['mean_error']:+.4f}R, RMSE {validation_error['rmse']:.4f}R"
        )
    else:
        outcome_evaluation = f"{status}: {reason}"

    return build_report(
        question_id="X5",
        status=status,
        overall=overall,
        confidence=confidence,
        dataset={"source": "decision_trace+shadow_trades", "sample_size": len(paired), "independent_observations": len(paired)},
        fingerprint=build_fingerprint(len(paired), max(0, diagnostics["prediction_opportunities"] - len(paired)), "decision_trace+shadow_trades", confidence, "CURRENT"),
        recommendation=(f"OBSERVATIONAL FINDING ({probability_calibration_state} probability): {outcome_evaluation}" if status == "COMPLETE" else f"{status}: {reason}"),
        assumptions=[
            "Predicted EV authority is D3's predicted_ev_r_v1; production Stage-4 raw ev cannot substitute.",
            "ev/expected_value/score/score_strategy/score_neutral/confidence are rejected as EV substitutes.",
            "One canonical_opportunity_id is one independent observation; account fanout and repeated horizons collapse first.",
            "Prediction must precede outcome; missing outcomes are excluded, never imputed.",
            "Both prediction and outcome are in R units; unit compatibility does not calibrate the probability model.",
            "Calibration bin boundaries are frozen from discovery only; validation never redefines them.",
            "Ranking/directional value is evaluated separately from magnitude calibration.",
            "Research only; no production EV/threshold/probability/trading change is made.",
        ],
        provenance={
            "experiment_module": __name__, "registry_id": "X5",
            "report_filename": X5_REPORT_FILENAME, "evidence_epoch": "CURRENT",
            "predicted_ev_version": PREDICTED_EV_VERSION,
            "predicted_ev_authority": "reused from research_engine.experiments.d3_predicted_ev_gate",
            "partition": "deterministic chronological 60/40 by timestamp groups",
            "bin_boundary_provenance": "discovery_partition_only",
        },
    )


def run_x5() -> dict[str, Any]:
    from research_engine.data_access.s3_source import get_default_source
    decisions = get_default_source().read_dataset("decision_trace")
    return analyse(decisions, load_shadow_trades(epoch="CURRENT"))
