"""D2: leakage-safe paired calibration of pre-decision ``p_success``.

One CURRENT canonical opportunity is one independent observation.  Prediction
records are joined one-to-one to subsequent shadow outcomes and evaluated on a
strictly later chronological partition.  This module never changes the
production probability estimator.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
from typing import Any, Iterable

from research_engine.experiments.experiment_base import (
    build_fingerprint,
    build_report,
    load_shadow_trades,
)
from research_engine.experiments.market_prediction_rw2 import (
    build_opportunity_observations,
)

D2_REPORT_FILENAME = "d2_paired_probability_calibration_v1.json"
MINIMUM_TOTAL = 100
MINIMUM_DISCOVERY = 60
MINIMUM_VALIDATION = 40
MINIMUM_BIN = 10
RELIABILITY_BINS = tuple((index / 10, (index + 1) / 10) for index in range(10))


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _probability(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and 0.0 <= result <= 1.0 else None


def _current(record: dict[str, Any]) -> bool:
    epoch = record.get("data_epoch", record.get("epoch"))
    if epoch is not None:
        return _text(epoch).upper() in {"CURRENT", "CURRENT_ONLY"}
    try:
        from core.production_data_contract import current_schema
        return record.get("schema_version") == current_schema("decision_trace")
    except (KeyError, TypeError):
        return False


def _prediction_row(record: dict[str, Any]) -> tuple[str, datetime | None, float | None, str]:
    identity = record.get("identity") if isinstance(record.get("identity"), dict) else {}
    opportunity = _text(record.get("canonical_opportunity_id") or identity.get("canonical_opportunity_id"))
    timestamp = _time(record.get("timestamp_utc"))
    # No compatibility aliases: this exact field is the canonical pre-decision
    # probability authority for D2.
    prediction = _probability(record.get("p_success"))
    version = _text(record.get("p_success_model_version") or "heuristic_score_v1")
    return opportunity, timestamp, prediction, version


def build_paired_observations(
    decision_records: Iterable[dict[str, Any]],
    shadow_records: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    outcomes, outcome_diagnostics = build_opportunity_observations(shadow_records)
    outcome_by_id = {row.canonical_opportunity_id: row for row in outcomes}
    prediction_groups: dict[str, set[tuple[datetime, float, str]]] = defaultdict(set)
    excluded_non_current = excluded_missing_prediction = 0
    for record in decision_records:
        if not _current(record):
            excluded_non_current += 1
            continue
        opportunity, timestamp, prediction, version = _prediction_row(record)
        if not opportunity or timestamp is None or prediction is None:
            excluded_missing_prediction += 1
            continue
        prediction_groups[opportunity].add((timestamp, prediction, version))

    paired: list[dict[str, Any]] = []
    conflicts: list[str] = list(outcome_diagnostics.get("ambiguous_opportunities", ()))
    missing_outcome = unmatched_prediction = 0
    for opportunity, predictions in sorted(prediction_groups.items()):
        if len(predictions) != 1:
            conflicts.append(opportunity)
            continue
        prediction_time, prediction, version = next(iter(predictions))
        outcome = outcome_by_id.get(opportunity)
        if outcome is None:
            unmatched_prediction += 1
            continue
        if outcome.outcome_r is None:
            missing_outcome += 1
            continue
        if prediction_time > outcome.decision_time:
            conflicts.append(opportunity)
            continue
        paired.append({
            "canonical_opportunity_id": opportunity,
            "timestamp": prediction_time,
            "p_success": prediction,
            "won": 1.0 if outcome.outcome_r > 0 else 0.0,
            "outcome_r": outcome.outcome_r,
            "model_version": version,
        })
    paired.sort(key=lambda row: (row["timestamp"], row["canonical_opportunity_id"]))
    return paired, {
        **outcome_diagnostics,
        "prediction_opportunities": len(prediction_groups),
        "paired_opportunities": len(paired),
        "excluded_non_current_predictions": excluded_non_current,
        "excluded_missing_prediction": excluded_missing_prediction,
        "missing_outcomes_excluded": missing_outcome,
        "unmatched_predictions": unmatched_prediction,
        "conflicting_opportunities": tuple(sorted(set(conflicts))),
    }


def chronological_split(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    unique_times = sorted({row["timestamp"] for row in rows})
    if len(unique_times) < 2:
        return rows, []
    index = max(1, min(len(unique_times) - 1, int(len(unique_times) * 0.60)))
    boundary = unique_times[index]
    return ([row for row in rows if row["timestamp"] < boundary],
            [row for row in rows if row["timestamp"] >= boundary])


def _reliability(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for lower, upper in RELIABILITY_BINS:
        members = [row for row in rows if lower <= row["p_success"] < upper or (upper == 1.0 and row["p_success"] == 1.0)]
        if not members:
            continue
        result.append({
            "lower": lower,
            "upper": upper,
            "n": len(members),
            "mean_prediction": sum(row["p_success"] for row in members) / len(members),
            "observed_win_rate": sum(row["won"] for row in members) / len(members),
            "sufficient": len(members) >= MINIMUM_BIN,
        })
    return result


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    brier = sum((row["p_success"] - row["won"]) ** 2 for row in rows) / len(rows) if rows else None
    bins = _reliability(rows)
    sufficient = [item for item in bins if item["sufficient"]]
    calibration_error = (
        sum(item["n"] * abs(item["mean_prediction"] - item["observed_win_rate"]) for item in sufficient)
        / sum(item["n"] for item in sufficient)
        if sufficient else None
    )
    return {
        "n": len(rows),
        "brier_score": brier,
        "reliability_bins": bins,
        "sufficient_bins": len(sufficient),
        "expected_calibration_error": calibration_error,
    }


def analyse(
    decision_records: Iterable[dict[str, Any]],
    shadow_records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    paired, diagnostics = build_paired_observations(decision_records, shadow_records)
    discovery, validation = chronological_split(paired)
    discovery_metrics = _metrics(discovery)
    validation_metrics = _metrics(validation)
    versions = sorted({row["model_version"] for row in paired})

    if diagnostics["conflicting_opportunities"]:
        status = "BLOCKED"
        reason = "Conflicting prediction, outcome, or chronology for canonical opportunity"
    elif len(versions) > 1:
        status = "BLOCKED"
        reason = "Mixed p_success model versions cannot share one calibration claim"
    elif len(paired) < MINIMUM_TOTAL or len(discovery) < MINIMUM_DISCOVERY or len(validation) < MINIMUM_VALIDATION:
        status = "WAITING_DATA"
        reason = (
            f"paired/discovery/validation={len(paired)}/{len(discovery)}/{len(validation)}; "
            f"need {MINIMUM_TOTAL}/{MINIMUM_DISCOVERY}/{MINIMUM_VALIDATION}"
        )
    elif validation_metrics["sufficient_bins"] < 2:
        status = "WAITING_DATA"
        reason = "Later validation requires at least two reliability bins with adequate cell evidence"
    else:
        status = "COMPLETE"
        reason = "Paired chronological calibration evaluation completed"

    model_version = versions[0] if len(versions) == 1 else "MIXED_OR_UNKNOWN"
    # COMPLETE means calibration was evaluated, not that calibration passed.
    calibrated = bool(
        status == "COMPLETE"
        and validation_metrics["expected_calibration_error"] is not None
        and validation_metrics["expected_calibration_error"] <= 0.10
        and validation_metrics["brier_score"] is not None
        and validation_metrics["brier_score"] < 0.25
    )
    calibration_status = "VALIDATED_CALIBRATED" if calibrated else "UNCALIBRATED"
    overall = {
        "canonical_question": "D2",
        "research_classification": "predictive_calibration",
        "unit_of_analysis": "one canonical_opportunity_id",
        "evidence_authority": "CURRENT decision_trace.p_success paired to CURRENT shadow outcome",
        "model_version": model_version,
        "discovery": discovery_metrics,
        "later_unseen_validation": validation_metrics,
        "calibration_status": calibration_status,
        "completion_reason": reason,
        "diagnostics": diagnostics,
        "sufficiency": {
            "minimum_total": MINIMUM_TOTAL,
            "minimum_discovery": MINIMUM_DISCOVERY,
            "minimum_validation": MINIMUM_VALIDATION,
            "minimum_per_reliability_bin": MINIMUM_BIN,
        },
    }
    confidence = "MEDIUM" if status == "COMPLETE" else "INSUFFICIENT_DATA"
    return build_report(
        question_id="D2",
        status=status,
        overall=overall,
        confidence=confidence,
        dataset={"source": "decision_trace+shadow_trades", "sample_size": len(paired), "independent_observations": len(paired)},
        fingerprint=build_fingerprint(len(paired), diagnostics["prediction_opportunities"] - len(paired), "decision_trace+shadow_trades", confidence, "CURRENT"),
        recommendation=(f"FINDING: {model_version} is {calibration_status}" if status == "COMPLETE" else f"{status}: {reason}"),
        assumptions=[
            "p_success is frozen at or before the opportunity decision timestamp.",
            "R>0 is the declared binary success label; missing R is excluded.",
            "Account fanout and repeated shadow horizons are collapsed within canonical opportunity.",
        ],
        provenance={
            "experiment_module": __name__, "registry_id": "D2",
            "report_filename": D2_REPORT_FILENAME, "evidence_epoch": "CURRENT",
            "partition": "deterministic chronological 60/40 by timestamp groups",
        },
    )


def run_d2() -> dict[str, Any]:
    from research_engine.data_access.s3_source import get_default_source
    decisions = get_default_source().read_dataset("decision_trace")
    return analyse(decisions, load_shadow_trades(epoch="CURRENT"))

