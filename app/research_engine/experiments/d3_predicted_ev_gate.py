"""D3: leakage-safe predicted-EV gate validation (R-multiple units).

D3 asks whether a PRE-DECISION predicted-EV signal has a useful relationship to
subsequent realised R.  The historical runner reported score-threshold
behaviour and treated ``policy_trade_allowed`` (a multi-gate field) as if it
were an isolated EV-gate treatment, so it could not evaluate the EV authority.

This runner defines a RESEARCH-DERIVED, versioned predicted-EV measure in
R-multiple units:

    predicted_ev_r_v1 = p_success * reward_r - (1 - p_success) * risk_r
    risk_r  = 1.0
    reward_r = pre-decision TP distance / pre-decision SL distance

It NEVER changes the production Stage-4 EV implementation and never overwrites
historical production ``ev`` values.  One CURRENT canonical opportunity is one
independent observation; account fanout and repeated horizons are collapsed
before any statistic.  Predictions are joined one-to-one to subsequent shadow
outcomes and evaluated on a strictly later chronological partition.  The
research EV threshold is selected on the discovery partition only.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
from statistics import median
from typing import Any, Iterable

from research_engine.experiments.experiment_base import (
    build_fingerprint,
    build_report,
    load_shadow_trades,
)
from research_engine.experiments.market_prediction_rw2 import (
    build_opportunity_observations,
)

D3_REPORT_FILENAME = "d3_predicted_ev_gate_validation_v1.json"
PREDICTED_EV_VERSION = "predicted_ev_r_v1"

RISK_R = 1.0
MINIMUM_TOTAL = 100
MINIMUM_DISCOVERY = 60
MINIMUM_VALIDATION = 40
MINIMUM_GROUP = 10
# Research EV threshold used only if a valid discovery-derived value cannot be
# computed; the canonical break-even for predicted EV in R units is 0.0.
DEFAULT_RESEARCH_THRESHOLD = 0.0


# Post-outcome fields must never enter predicted_ev_r.
_POST_OUTCOME_NAMES = frozenset({
    "realised_r", "r_multiple", "pnl_r_multiple", "mfe", "mfe_r", "mae",
    "mae_r", "close_reason", "exit_reason", "future_path", "future_regime",
    "broker_pnl", "won", "outcome_r",
})


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        try:
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _probability(value: Any) -> float | None:
    result = _finite(value)
    return result if result is not None and 0.0 <= result <= 1.0 else None


def _current(record: dict[str, Any]) -> bool:
    epoch = record.get("data_epoch", record.get("epoch"))
    if epoch is not None:
        return _text(epoch).upper() in {"CURRENT", "CURRENT_ONLY"}
    try:
        from core.production_data_contract import current_schema
        return record.get("schema_version") == current_schema("decision_trace")
    except (KeyError, TypeError):
        return False


def reward_r_from_geometry(entry: dict[str, Any]) -> float | None:
    """Derive reward_r from PRE-DECISION target/stop geometry (stop = 1R).

    Returns None on invalid geometry (missing prices, zero/negative SL
    distance, non-positive TP distance, non-finite values, ambiguous
    direction).  Never invents values.
    """
    if not isinstance(entry, dict):
        return None
    entry_price = _finite(entry.get("entry_price"))
    stop_price = _finite(entry.get("stop_price"))
    target_price = _finite(entry.get("target_price"))
    if entry_price is None or stop_price is None or target_price is None:
        return None
    risk_distance = abs(entry_price - stop_price)
    reward_distance = abs(target_price - entry_price)
    if risk_distance <= 0.0 or reward_distance <= 0.0:
        return None
    # Direction must be consistent: TP and SL on opposite sides of entry.
    if (target_price - entry_price) * (entry_price - stop_price) <= 0.0:
        return None
    reward_r = reward_distance / risk_distance
    return reward_r if math.isfinite(reward_r) and reward_r > 0.0 else None


def predicted_ev_r(p_success: float, reward_r: float) -> float:
    """predicted_ev_r_v1 = p_success * reward_r - (1 - p_success) * risk_r."""
    return p_success * reward_r - (1.0 - p_success) * RISK_R


def _prediction_row(record: dict[str, Any]) -> tuple[str, datetime | None, float | None, float | None, str]:
    """Extract (opportunity, timestamp, p_success, reward_r, version) from a
    CURRENT decision_trace record using PRE-DECISION authorities only."""
    identity = record.get("identity") if isinstance(record.get("identity"), dict) else {}
    opportunity = _text(record.get("canonical_opportunity_id") or identity.get("canonical_opportunity_id"))
    timestamp = _time(record.get("timestamp_utc"))
    prediction = _probability(record.get("p_success"))
    version = _text(record.get("p_success_model_version") or "heuristic_score_v1")
    reward_r = reward_r_from_geometry(record.get("v10_entry") or {})
    return opportunity, timestamp, prediction, reward_r, version


def build_paired_predictions(
    decision_records: Iterable[dict[str, Any]],
    shadow_records: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pair one PRE-DECISION predicted_ev_r per canonical opportunity with its
    subsequent realised R outcome.  Fanout/horizons collapse; missing outcomes
    excluded; conflicts and post-decision chronology fail closed."""
    outcomes, outcome_diagnostics = build_opportunity_observations(shadow_records)
    outcome_by_id = {row.canonical_opportunity_id: row for row in outcomes}

    prediction_groups: dict[str, set[tuple[datetime, float, float, str]]] = defaultdict(set)
    excluded_non_current = excluded_missing_prediction = excluded_invalid_geometry = 0
    for record in decision_records:
        if not _current(record):
            excluded_non_current += 1
            continue
        opportunity, timestamp, prediction, reward_r, version = _prediction_row(record)
        if not opportunity or timestamp is None or prediction is None:
            excluded_missing_prediction += 1
            continue
        if reward_r is None:
            excluded_invalid_geometry += 1
            continue
        prediction_groups[opportunity].add((timestamp, prediction, reward_r, version))

    paired: list[dict[str, Any]] = []
    conflicts: list[str] = list(outcome_diagnostics.get("ambiguous_opportunities", ()))
    missing_outcome = unmatched_prediction = 0
    for opportunity, predictions in sorted(prediction_groups.items()):
        if len(predictions) != 1:
            conflicts.append(opportunity)
            continue
        prediction_time, p_success, reward_r, version = next(iter(predictions))
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
        ev_r = predicted_ev_r(p_success, reward_r)
        if not math.isfinite(ev_r):
            conflicts.append(opportunity)
            continue
        paired.append({
            "canonical_opportunity_id": opportunity,
            "timestamp": prediction_time,
            "p_success": p_success,
            "reward_r": reward_r,
            "predicted_ev_r": ev_r,
            "outcome_r": outcome.outcome_r,
            "won": 1.0 if outcome.outcome_r > 0 else 0.0,
            "model_version": version,
        })
    paired.sort(key=lambda row: (row["timestamp"], row["canonical_opportunity_id"]))
    return paired, {
        **outcome_diagnostics,
        "prediction_opportunities": len(prediction_groups),
        "paired_opportunities": len(paired),
        "excluded_non_current_predictions": excluded_non_current,
        "excluded_missing_prediction": excluded_missing_prediction,
        "excluded_invalid_geometry": excluded_invalid_geometry,
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


def _group_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "mean_r": None, "median_r": None, "win_rate": None}
    outcomes = [row["outcome_r"] for row in rows]
    return {
        "n": len(rows),
        "mean_r": sum(outcomes) / len(outcomes),
        "median_r": median(outcomes),
        "win_rate": sum(1 for row in rows if row["outcome_r"] > 0) / len(rows),
    }


def select_research_threshold(discovery: list[dict[str, Any]]) -> tuple[float, str]:
    """Select the EV threshold from the DISCOVERY partition only.

    The canonical break-even for predicted EV in R units is 0.0.  We verify on
    discovery that the 0.0 break-even actually partitions discovery into two
    non-empty groups; if it does not, we fall back to the discovery median
    predicted_ev_r so both groups exist.  The validation partition is never
    consulted here.
    """
    if not discovery:
        return DEFAULT_RESEARCH_THRESHOLD, "research_break_even_r_0"
    evs = [row["predicted_ev_r"] for row in discovery]
    eligible = sum(1 for value in evs if value >= DEFAULT_RESEARCH_THRESHOLD)
    if 0 < eligible < len(discovery):
        return DEFAULT_RESEARCH_THRESHOLD, "research_break_even_r_0"
    return median(evs), "research_discovery_median_r"


def analyse(
    decision_records: Iterable[dict[str, Any]],
    shadow_records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    paired, diagnostics = build_paired_predictions(decision_records, shadow_records)
    discovery, validation = chronological_split(paired)
    versions = sorted({row["model_version"] for row in paired})
    threshold, threshold_provenance = select_research_threshold(discovery)

    eligible = [row for row in validation if row["predicted_ev_r"] >= threshold]
    ineligible = [row for row in validation if row["predicted_ev_r"] < threshold]
    eligible_stats = _group_stats(eligible)
    ineligible_stats = _group_stats(ineligible)
    expectancy_difference = (
        eligible_stats["mean_r"] - ineligible_stats["mean_r"]
        if eligible_stats["mean_r"] is not None and ineligible_stats["mean_r"] is not None
        else None
    )
    ev_values = [row["predicted_ev_r"] for row in paired]
    distribution = {
        "min": min(ev_values) if ev_values else None,
        "median": median(ev_values) if ev_values else None,
        "max": max(ev_values) if ev_values else None,
    }

    if diagnostics["conflicting_opportunities"]:
        status = "BLOCKED"
        reason = "Conflicting prediction, geometry, outcome, or chronology for canonical opportunity"
    elif len(versions) > 1:
        status = "BLOCKED"
        reason = "Mixed p_success model versions cannot share one predicted-EV claim"
    elif len(paired) < MINIMUM_TOTAL or len(discovery) < MINIMUM_DISCOVERY or len(validation) < MINIMUM_VALIDATION:
        status = "WAITING_DATA"
        reason = (
            f"paired/discovery/validation={len(paired)}/{len(discovery)}/{len(validation)}; "
            f"need {MINIMUM_TOTAL}/{MINIMUM_DISCOVERY}/{MINIMUM_VALIDATION}"
        )
    elif eligible_stats["n"] < MINIMUM_GROUP or ineligible_stats["n"] < MINIMUM_GROUP:
        status = "WAITING_DATA"
        reason = (
            f"validation EV-eligible/ineligible={eligible_stats['n']}/{ineligible_stats['n']}; "
            f"need >= {MINIMUM_GROUP} in each group"
        )
    else:
        status = "COMPLETE"
        reason = "Chronological predicted-EV gate evaluation completed on later unseen opportunities"

    model_version = versions[0] if len(versions) == 1 else "MIXED_OR_UNKNOWN"
    # D2 semantics preserved: p_success is an UNCALIBRATED heuristic unless D2's
    # separate calibration evidence passes.  D3 never asserts calibrated EV.
    probability_calibration_state = (
        "UNCALIBRATED" if model_version in {"heuristic_score_v1", "MIXED_OR_UNKNOWN"}
        else "SEE_D2_CALIBRATION_REPORT"
    )
    overall = {
        "canonical_question": "D3",
        "research_classification": "observational_predictive_gate",
        "causal_claim": "NONE — observational predicted-EV gate evaluation, not a counterfactual EV-gate treatment",
        "unit_of_analysis": "one canonical_opportunity_id",
        "evidence_authority": "CURRENT decision_trace p_success + v10_entry geometry paired to CURRENT shadow outcome",
        "predicted_ev_measure": {
            "version": PREDICTED_EV_VERSION,
            "formula": "p_success * reward_r - (1 - p_success) * risk_r",
            "risk_r": RISK_R,
            "reward_r": "pre-decision TP distance / pre-decision SL distance",
            "units": "R-multiple",
            "provenance": "research-derived; production Stage-4 ev untouched",
        },
        "p_success_model_version": model_version,
        "probability_calibration_state": probability_calibration_state,
        "probability_calibration_authority": "D2 (d2_paired_probability_calibration_v1.json)",
        "predicted_ev_r_distribution": distribution,
        "threshold": {
            "value": threshold,
            "version": PREDICTED_EV_VERSION,
            "provenance": threshold_provenance,
            "authoritative": False,
            "selected_from": "discovery_partition_only",
        },
        "ev_eligible_validation": eligible_stats,
        "ev_ineligible_validation": ineligible_stats,
        "expectancy_difference_r": expectancy_difference,
        "discovery": {"n": len(discovery)},
        "later_unseen_validation": {"n": len(validation)},
        "completion_reason": reason,
        "diagnostics": diagnostics,
        "sufficiency": {
            "minimum_total": MINIMUM_TOTAL,
            "minimum_discovery": MINIMUM_DISCOVERY,
            "minimum_validation": MINIMUM_VALIDATION,
            "minimum_per_group": MINIMUM_GROUP,
        },
    }
    confidence = "MEDIUM" if status == "COMPLETE" else "INSUFFICIENT_DATA"
    outcome_evaluation = (
        f"EV-eligible mean R {eligible_stats['mean_r']:+.4f} vs EV-ineligible {ineligible_stats['mean_r']:+.4f} "
        f"(difference {expectancy_difference:+.4f}R)"
        if status == "COMPLETE" else f"{status}: {reason}"
    )
    return build_report(
        question_id="D3",
        status=status,
        overall=overall,
        confidence=confidence,
        dataset={"source": "decision_trace+shadow_trades", "sample_size": len(paired), "independent_observations": len(paired)},
        fingerprint=build_fingerprint(len(paired), max(0, diagnostics["prediction_opportunities"] - len(paired)), "decision_trace+shadow_trades", confidence, "CURRENT"),
        recommendation=(f"OBSERVATIONAL FINDING ({probability_calibration_state} probability): {outcome_evaluation}" if status == "COMPLETE" else f"{status}: {reason}"),
        assumptions=[
            "predicted_ev_r uses only pre-decision p_success and pre-decision TP/SL geometry (R units).",
            "risk_r=1.0; reward_r=TP distance/SL distance; invalid geometry fails closed.",
            "R>0 is the win label for descriptive win rate; missing R is excluded, never imputed.",
            "Account fanout and repeated shadow horizons are collapsed within canonical opportunity.",
            "Observational gate evaluation only; policy_trade_allowed is NOT treated as isolated EV-gate treatment.",
            "predicted_ev_r is research-derived and versioned; production Stage-4 ev is never modified.",
        ],
        provenance={
            "experiment_module": __name__, "registry_id": "D3",
            "report_filename": D3_REPORT_FILENAME, "evidence_epoch": "CURRENT",
            "predicted_ev_version": PREDICTED_EV_VERSION,
            "partition": "deterministic chronological 60/40 by timestamp groups",
            "threshold_provenance": threshold_provenance,
        },
    )


def run_d3() -> dict[str, Any]:
    from research_engine.data_access.s3_source import get_default_source
    decisions = get_default_source().read_dataset("decision_trace")
    return analyse(decisions, load_shadow_trades(epoch="CURRENT"))
