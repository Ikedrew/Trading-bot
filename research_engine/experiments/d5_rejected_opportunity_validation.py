"""D5: leakage-safe rejected/no-trade opportunity counterfactual validation.

D5 asks whether rejected / no-trade canonical opportunities subsequently show
outcome characteristics indicating the rejection logic is filtering harmful
opportunities (GOOD_FILTERING), discarding useful opportunities
(MISSED_OPPORTUNITY_SIGNAL), or showing no reliable difference.

REJECTION AUTHORITY & TAXONOMY (d5_rejection_taxonomy_v1)
--------------------------------------------------------
The canonical pre-outcome rejection authority is the decision_trace triple
``action`` / ``terminal_stage`` / ``terminal_reason``.  A rejection is
``action == "NO_TRADE"`` with a resolved terminal stage.  ``terminal_stage`` is
derived by the producer (``core/decision_trace.py::_classify_terminal_stage``)
from ``terminal_reason`` and follows the canonical pipeline order
``_STAGE_ORDER``.  D5 maps that producer stage to one deterministic class:

    A. STRATEGY_SIGNAL_REJECTION  — pattern/strategy/score/policy/swing/EV
       judged the opportunity unsuitable.
    B. RISK_EXECUTION_BLOCK       — risk or data/plan eligibility prevented it.
    C. UNKNOWN                    — rejection is genuine but the stage cannot be
       safely assigned; retained, never forced into A.

PATTERN_REJECT / NO_TRADE / RISK_BLOCK are NOT treated as interchangeable: the
producer stage decides the class.  Multiple reasons for one opportunity never
double-count; the earliest ``_STAGE_ORDER`` stage is the deterministic primary
classification.

The subsequent outcome is the SAME CURRENT shadow realised R used by D2/D3/D4.
It is a COUNTERFACTUAL / SIMULATED research outcome, never a broker fill or
realised P&L.  One canonical_opportunity_id is one independent observation;
account fanout and repeated horizons collapse first.  Missing outcomes are
excluded and never imputed.  Production rejection logic is never modified.
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

D5_REPORT_FILENAME = "d5_rejected_opportunity_validation_v1.json"
REJECTION_TAXONOMY_VERSION = "d5_rejection_taxonomy_v1"
EVIDENCE_CLASS = "COUNTERFACTUAL_SHADOW_SIMULATED_OUTCOME"

# Canonical producer stage order (mirrors core/decision_trace.py::_STAGE_ORDER)
# used ONLY as deterministic rejection precedence. Earlier stage wins.
_STAGE_ORDER = (
    "pattern_detection",
    "strategy_classification",
    "scoring",
    "policy_pre",
    "swing",
    "data_validation",
    "risk",
    "ev_policy",
    "execute",
)
_STAGE_RANK = {stage: index for index, stage in enumerate(_STAGE_ORDER)}

# Deterministic producer-stage -> D5 rejection class map.
_STAGE_TO_CLASS = {
    "pattern_detection": "STRATEGY_SIGNAL_REJECTION",
    "strategy_classification": "STRATEGY_SIGNAL_REJECTION",
    "scoring": "STRATEGY_SIGNAL_REJECTION",
    "policy_pre": "STRATEGY_SIGNAL_REJECTION",
    "swing": "STRATEGY_SIGNAL_REJECTION",
    "ev_policy": "STRATEGY_SIGNAL_REJECTION",
    "data_validation": "RISK_EXECUTION_BLOCK",
    "risk": "RISK_EXECUTION_BLOCK",
}
# execute is NOT a rejection; unknown/error/anything else -> UNKNOWN.
_UNKNOWN_CLASS = "UNKNOWN"

MINIMUM_TOTAL = 100
MINIMUM_DISCOVERY = 60
MINIMUM_VALIDATION = 40
MINIMUM_SUBGROUP = 15


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


def _current(record: dict[str, Any]) -> bool:
    epoch = record.get("data_epoch", record.get("epoch"))
    if epoch is not None:
        return _text(epoch).upper() in {"CURRENT", "CURRENT_ONLY"}
    try:
        from core.production_data_contract import current_schema
        return record.get("schema_version") == current_schema("decision_trace")
    except (KeyError, TypeError):
        return False


def classify_rejection_stage(terminal_stage: str) -> str:
    """Deterministically map a producer terminal_stage to a D5 rejection class.

    Unknown / unmapped genuine-rejection stages become UNKNOWN; they are never
    guessed into a strategy bucket.
    """
    return _STAGE_TO_CLASS.get(_text(terminal_stage), _UNKNOWN_CLASS)


def _is_rejection(record: dict[str, Any]) -> bool:
    """True only for a genuine pre-outcome NO_TRADE rejection (not EXECUTE)."""
    action = _text(record.get("action")).upper()
    if action == "EXECUTE":
        return False
    stage = _text(record.get("terminal_stage")).lower()
    if stage == "execute":
        return False
    # A genuine rejection must be NO_TRADE with some resolved stage/reason.
    return action == "NO_TRADE" and bool(stage or _text(record.get("terminal_reason")))


def _rejection_row(record: dict[str, Any]) -> tuple[str, datetime | None, str, str, str]:
    """Extract (opportunity, rejection_time, terminal_stage, reason, class)."""
    identity = record.get("identity") if isinstance(record.get("identity"), dict) else {}
    opportunity = _text(record.get("canonical_opportunity_id") or identity.get("canonical_opportunity_id"))
    timestamp = _time(record.get("timestamp_utc"))
    stage = _text(record.get("terminal_stage")) or "unknown"
    reason = _text(record.get("terminal_reason"))
    rejection_class = classify_rejection_stage(stage)
    return opportunity, timestamp, stage, reason, rejection_class


def _stage_rank(stage: str) -> int:
    """Canonical precedence: earlier pipeline stage wins. Unknown ranks last."""
    return _STAGE_RANK.get(_text(stage), len(_STAGE_ORDER) + 1)


def _pre_decision_context(record: dict[str, Any]) -> tuple[str, str, str]:
    regime = _text(record.get("regime") or record.get("h4_regime")) or "UNKNOWN"
    phase = _text(record.get("market_phase") or record.get("market_state")) or "UNKNOWN"
    strategy = _text(record.get("strategy") or record.get("selected_strategy")) or "UNKNOWN"
    return regime, phase, strategy


def build_rejected_observations(
    decision_records: Iterable[dict[str, Any]],
    shadow_records: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build one independent rejected-opportunity observation per canonical
    opportunity, paired to its subsequent CURRENT shadow (counterfactual) R.

    - Only genuine pre-outcome NO_TRADE rejections enter the population.
    - Multiple rejection reasons for one opportunity collapse to ONE primary
      classification via canonical stage precedence (earliest _STAGE_ORDER
      stage). Ties on rank with conflicting classes fail closed.
    - Account fanout / repeated horizons collapse within the opportunity
      (reused via build_opportunity_observations for the outcome side).
    - Missing outcomes are excluded (never imputed).
    - Rejection timestamp must precede the outcome; else the observation fails
      closed.
    """
    outcomes, outcome_diagnostics = build_opportunity_observations(shadow_records)
    outcome_by_id = {row.canonical_opportunity_id: row for row in outcomes}

    # Gather every rejection reason per opportunity (diagnostics preserved).
    reasons_by_opp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    excluded_non_current = excluded_not_rejection = excluded_missing_lineage = 0
    for record in decision_records:
        if not _current(record):
            excluded_non_current += 1
            continue
        if not _is_rejection(record):
            excluded_not_rejection += 1
            continue
        opportunity, timestamp, stage, reason, rejection_class = _rejection_row(record)
        if not opportunity or timestamp is None:
            excluded_missing_lineage += 1
            continue
        regime, phase, strategy = _pre_decision_context(record)
        reasons_by_opp[opportunity].append({
            "timestamp": timestamp, "terminal_stage": stage, "terminal_reason": reason,
            "rejection_class": rejection_class, "stage_rank": _stage_rank(stage),
            "h4_regime": regime, "market_phase": phase, "strategy": strategy,
        })

    observations: list[dict[str, Any]] = []
    conflicts: list[str] = list(outcome_diagnostics.get("ambiguous_opportunities", ()))
    rejection_only = missing_outcome = 0
    multi_reason_opps = 0
    for opportunity, reasons in sorted(reasons_by_opp.items()):
        # Deterministic primary rejection = earliest canonical stage.
        best_rank = min(item["stage_rank"] for item in reasons)
        primary_candidates = [item for item in reasons if item["stage_rank"] == best_rank]
        primary_classes = {item["rejection_class"] for item in primary_candidates}
        if len(reasons) > 1:
            multi_reason_opps += 1
        # Contradictory primary authorities at the same rank -> fail closed.
        if len(primary_classes) > 1:
            conflicts.append(opportunity)
            continue
        primary = primary_candidates[0]
        # The rejection timestamp used for chronology is the earliest rejection.
        rejection_time = min(item["timestamp"] for item in reasons)
        outcome = outcome_by_id.get(opportunity)
        # A rejection with no valid subsequent outcome is frequency-only.
        if outcome is None or outcome.outcome_r is None:
            rejection_only += 1
            if outcome is not None and outcome.outcome_r is None:
                missing_outcome += 1
            continue
        if rejection_time > outcome.decision_time:
            conflicts.append(opportunity)
            continue
        observations.append({
            "canonical_opportunity_id": opportunity,
            "rejection_timestamp": rejection_time,
            "timestamp": rejection_time,
            "terminal_stage": primary["terminal_stage"],
            "terminal_reason": primary["terminal_reason"],
            "rejection_class": primary["rejection_class"],
            "rejection_authority": "decision_trace.action+terminal_stage+terminal_reason",
            "taxonomy_version": REJECTION_TAXONOMY_VERSION,
            "all_reasons": tuple(sorted({item["terminal_reason"] for item in reasons if item["terminal_reason"]})),
            "outcome_r": outcome.outcome_r,
            "outcome_timestamp": outcome.decision_time,
            "won": 1.0 if outcome.outcome_r > 0 else 0.0,
            "h4_regime": primary["h4_regime"],
            "market_phase": primary["market_phase"],
            "strategy": primary["strategy"],
        })
    observations.sort(key=lambda row: (row["timestamp"], row["canonical_opportunity_id"]))
    return observations, {
        **outcome_diagnostics,
        "rejected_opportunities": len(reasons_by_opp),
        "paired_rejected_opportunities": len(observations),
        "rejection_only_no_outcome": rejection_only,
        "missing_outcomes_excluded": missing_outcome,
        "multi_reason_opportunities": multi_reason_opps,
        "excluded_non_current": excluded_non_current,
        "excluded_not_rejection": excluded_not_rejection,
        "excluded_missing_lineage": excluded_missing_lineage,
        "conflicting_opportunities": tuple(sorted(set(conflicts))),
    }


def chronological_split(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deterministic EARLIER=discovery / LATER=validation split by timestamp.

    One canonical opportunity stays entirely within one partition (each row is
    already one opportunity), mirroring D4.
    """
    unique_times = sorted({row["timestamp"] for row in rows})
    if len(unique_times) < 2:
        return rows, []
    index = max(1, min(len(unique_times) - 1, int(len(unique_times) * 0.60)))
    boundary = unique_times[index]
    return ([row for row in rows if row["timestamp"] < boundary],
            [row for row in rows if row["timestamp"] >= boundary])


def _group_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "mean_r": None, "median_r": None, "win_rate": None,
                "positive_r": 0, "negative_r": 0}
    outcomes = [row["outcome_r"] for row in rows]
    return {
        "n": len(rows),
        "mean_r": sum(outcomes) / len(outcomes),
        "median_r": median(outcomes),
        "win_rate": sum(1 for value in outcomes if value > 0) / len(rows),
        "positive_r": sum(1 for value in outcomes if value > 0),
        "negative_r": sum(1 for value in outcomes if value < 0),
    }


def _subgroup_stats(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """Per-subgroup stats; subgroups with n<MINIMUM_SUBGROUP are marked
    insufficient and never used for a directional claim."""
    cells: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        cells[row[key]].append(row)
    result: dict[str, Any] = {}
    for cell, cell_rows in sorted(cells.items()):
        stats = _group_stats(cell_rows)
        stats["sufficient"] = stats["n"] >= MINIMUM_SUBGROUP
        result[cell] = stats
    return result


def _direction(mean_r: float | None) -> str | None:
    if mean_r is None:
        return None
    if mean_r < 0:
        return "ADVERSE"      # rejected opportunities were bad -> good to reject
    if mean_r > 0:
        return "FAVOURABLE"   # rejected opportunities were good -> missed
    return "NEUTRAL"


def _classify_finding(
    status: str,
    discovery_mean: float | None,
    validation_mean: float | None,
) -> str:
    """Directional finding using discovery vs later validation persistence."""
    if status != "COMPLETE":
        return "INSUFFICIENT_EVIDENCE"
    discovery_direction = _direction(discovery_mean)
    validation_direction = _direction(validation_mean)
    if discovery_direction in (None, "NEUTRAL"):
        return "MIXED_OR_NO_SIGNAL"
    if validation_direction != discovery_direction:
        return "DISCOVERY_ONLY"
    # Same non-neutral direction persisted into later unseen validation.
    if validation_direction == "ADVERSE":
        return "GOOD_FILTERING"
    return "MISSED_OPPORTUNITY_SIGNAL"


def build_accepted_comparator(
    decision_records: Iterable[dict[str, Any]],
    shadow_records: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the ACCEPTED (EXECUTE) canonical-opportunity comparator population.

    Same independent unit, same CURRENT shadow outcome authority, same
    chronology and no-imputation rules as the rejected population.  Conflicting
    accepted evidence for one opportunity fails closed.  This is used ONLY when
    it can be established without inventing semantics; otherwise D5 reports the
    absolute counterfactual quality of rejected opportunities and states that
    comparative policy-effectiveness is not established.
    """
    outcomes, _ = build_opportunity_observations(shadow_records)
    outcome_by_id = {row.canonical_opportunity_id: row for row in outcomes}

    accepted_times: dict[str, set[datetime]] = defaultdict(set)
    for record in decision_records:
        if not _current(record):
            continue
        if _text(record.get("action")).upper() != "EXECUTE":
            continue
        identity = record.get("identity") if isinstance(record.get("identity"), dict) else {}
        opportunity = _text(record.get("canonical_opportunity_id") or identity.get("canonical_opportunity_id"))
        timestamp = _time(record.get("timestamp_utc"))
        if not opportunity or timestamp is None:
            continue
        accepted_times[opportunity].add(timestamp)

    accepted: list[dict[str, Any]] = []
    conflicts = 0
    for opportunity, times in sorted(accepted_times.items()):
        outcome = outcome_by_id.get(opportunity)
        if outcome is None or outcome.outcome_r is None:
            continue
        decision_time = min(times)
        if decision_time > outcome.decision_time:
            conflicts += 1
            continue
        accepted.append({
            "canonical_opportunity_id": opportunity,
            "timestamp": decision_time,
            "outcome_r": outcome.outcome_r,
            "won": 1.0 if outcome.outcome_r > 0 else 0.0,
        })
    accepted.sort(key=lambda row: (row["timestamp"], row["canonical_opportunity_id"]))
    return accepted, {"accepted_paired_opportunities": len(accepted), "accepted_conflicts": conflicts}


def analyse(
    decision_records: Iterable[dict[str, Any]],
    shadow_records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    decision_records = list(decision_records)
    shadow_records = list(shadow_records)
    rejected, diagnostics = build_rejected_observations(decision_records, shadow_records)
    discovery, validation = chronological_split(rejected)

    overall_stats = _group_stats(rejected)
    discovery_stats = _group_stats(discovery)
    validation_stats = _group_stats(validation)

    class_diagnostics = _subgroup_stats(rejected, "rejection_class")
    reason_diagnostics = _subgroup_stats(rejected, "terminal_reason")
    regime_diagnostics = _subgroup_stats(rejected, "h4_regime")
    phase_diagnostics = _subgroup_stats(rejected, "market_phase")
    strategy_diagnostics = _subgroup_stats(rejected, "strategy")

    class_frequencies = {cell: stats["n"] for cell, stats in class_diagnostics.items()}
    reason_frequencies = {cell: stats["n"] for cell, stats in reason_diagnostics.items()}

    # Accepted comparator (used only if scientifically valid on both sides).
    accepted, accepted_diagnostics = build_accepted_comparator(decision_records, shadow_records)
    _, accepted_validation = chronological_split(accepted)
    accepted_validation_stats = _group_stats(accepted_validation)
    comparator_valid = (
        validation_stats["n"] >= MINIMUM_SUBGROUP
        and accepted_validation_stats["n"] >= MINIMUM_SUBGROUP
    )
    if comparator_valid:
        comparator_status = "ESTABLISHED"
        comparator_difference_r = validation_stats["mean_r"] - accepted_validation_stats["mean_r"]
    else:
        comparator_status = "NOT_ESTABLISHED_INSUFFICIENT_ACCEPTED_VALIDATION_SAMPLE"
        comparator_difference_r = None

    if diagnostics["conflicting_opportunities"]:
        status = "BLOCKED"
        reason = "Conflicting rejection classification, outcome, or chronology for a canonical opportunity"
    elif len(rejected) < MINIMUM_TOTAL or len(discovery) < MINIMUM_DISCOVERY or len(validation) < MINIMUM_VALIDATION:
        status = "WAITING_DATA"
        reason = (
            f"paired-rejected/discovery/validation={len(rejected)}/{len(discovery)}/{len(validation)}; "
            f"need {MINIMUM_TOTAL}/{MINIMUM_DISCOVERY}/{MINIMUM_VALIDATION}"
        )
    else:
        status = "COMPLETE"
        reason = "Chronological rejected-opportunity counterfactual evaluation completed on later unseen opportunities"

    finding = _classify_finding(status, discovery_stats["mean_r"], validation_stats["mean_r"])

    overall = {
        "canonical_question": "D5",
        "research_classification": "observational_counterfactual_rejection_validation",
        "causal_claim": "NONE — counterfactual shadow evaluation of rejected opportunities; not broker truth and not proof production would realise the same result",
        "unit_of_analysis": "one canonical_opportunity_id",
        "evidence_class": EVIDENCE_CLASS,
        "rejection_authority": "decision_trace.action + terminal_stage + terminal_reason (producer-derived)",
        "rejection_taxonomy": {
            "version": REJECTION_TAXONOMY_VERSION,
            "classes": {
                "STRATEGY_SIGNAL_REJECTION": "pattern/strategy/score/policy/swing/ev_policy stages",
                "RISK_EXECUTION_BLOCK": "risk / data_validation eligibility stages",
                "UNKNOWN": "genuine rejection whose stage cannot be safely assigned",
            },
            "precedence": "earliest canonical pipeline stage (_STAGE_ORDER) is the primary classification; contradictory same-rank authorities fail closed",
            "stage_order": list(_STAGE_ORDER),
        },
        "outcome_authority": "CURRENT shadow simulated_outcome.pnl_r_multiple for the same canonical opportunity (counterfactual)",
        "evidence_epoch": "CURRENT",
        "total_rejected_opportunities": diagnostics["rejected_opportunities"],
        "paired_outcome_opportunities": len(rejected),
        "missing_outcome_count": diagnostics["missing_outcomes_excluded"],
        "rejection_only_no_outcome": diagnostics["rejection_only_no_outcome"],
        "rejected_outcome_statistics": overall_stats,
        "rejection_class_frequencies": class_frequencies,
        "rejection_reason_frequencies": reason_frequencies,
        "outcome_by_rejection_class": class_diagnostics,
        "outcome_by_rejection_reason": reason_diagnostics,
        "discovery": {"n": len(discovery), "mean_r": discovery_stats["mean_r"], "direction": _direction(discovery_stats["mean_r"])},
        "later_unseen_validation": {"n": len(validation), "mean_r": validation_stats["mean_r"], "direction": _direction(validation_stats["mean_r"])},
        "comparator": {
            "status": comparator_status,
            "accepted_validation": accepted_validation_stats,
            "rejected_minus_accepted_validation_mean_r": comparator_difference_r,
            "note": "Comparator uses accepted EXECUTE opportunities on the SAME CURRENT shadow outcome authority, same unit, same chronology, no imputation. Reported only when both validation sides reach the subgroup minimum; otherwise comparative policy-effectiveness is NOT established.",
        },
        "context_diagnostics": {
            "h4_regime": regime_diagnostics,
            "market_phase": phase_diagnostics,
            "strategy": strategy_diagnostics,
            "note": "Same-opportunity pre-decision context; cells with n<15 are marked insufficient and never over-interpreted. Context does not inflate independent n.",
        },
        "finding_classification": finding,
        "completion_reason": reason,
        "limitations": [
            "Shadow outcomes for rejected opportunities are counterfactual/simulated, not broker fills or realised P&L.",
            "COMPLETE means the chronological evaluation ran validly, not that the rejection logic is good or should change.",
            "Production rejection/score/EV/risk/execution logic is NOT modified by this research.",
            "Rejection-only opportunities without a valid subsequent outcome are frequency diagnostics only.",
            "Comparative policy-effectiveness is only claimed when a valid accepted comparator exists on both validation sides.",
            "Insufficient class/reason/context cells (n<15) are reported but not interpreted.",
        ],
        "diagnostics": diagnostics,
        "comparator_diagnostics": accepted_diagnostics,
        "sufficiency": {
            "minimum_total": MINIMUM_TOTAL,
            "minimum_discovery": MINIMUM_DISCOVERY,
            "minimum_validation": MINIMUM_VALIDATION,
            "minimum_subgroup": MINIMUM_SUBGROUP,
        },
    }
    confidence = "MEDIUM" if status == "COMPLETE" else "INSUFFICIENT_DATA"
    if status == "COMPLETE":
        outcome_evaluation = (
            f"[{finding}] rejected validation mean R {validation_stats['mean_r']:+.4f} "
            f"(discovery {discovery_stats['mean_r']:+.4f})"
        )
    else:
        outcome_evaluation = f"{status}: {reason}"

    return build_report(
        question_id="D5",
        status=status,
        overall=overall,
        confidence=confidence,
        dataset={"source": "decision_trace+shadow_trades", "sample_size": len(rejected), "independent_observations": len(rejected)},
        fingerprint=build_fingerprint(len(rejected), max(0, diagnostics["rejected_opportunities"] - len(rejected)), "decision_trace+shadow_trades", confidence, "CURRENT"),
        recommendation=(f"OBSERVATIONAL COUNTERFACTUAL FINDING: {outcome_evaluation}" if status == "COMPLETE" else f"{status}: {reason}"),
        assumptions=[
            "Rejection authority is decision_trace action+terminal_stage+terminal_reason; a genuine rejection is NO_TRADE with a resolved stage.",
            "PATTERN_REJECT / NO_TRADE / RISK_BLOCK are not blindly equivalent; producer stage decides the class.",
            "One canonical_opportunity_id is one independent observation; account fanout and repeated horizons collapse first.",
            "Multiple rejection reasons collapse to one primary class by earliest canonical stage; same-rank contradictions fail closed.",
            "Subsequent shadow R is a counterfactual research outcome, never a broker fill; rejected never implies loss or 0R.",
            "Missing outcomes are excluded, never imputed; rejection timestamp must precede the outcome.",
            "Taxonomy is fixed from producer semantics and is never outcome-optimised on validation.",
            "No production logic is changed and no rejected trade is executed.",
        ],
        provenance={
            "experiment_module": __name__, "registry_id": "D5",
            "report_filename": D5_REPORT_FILENAME, "evidence_epoch": "CURRENT",
            "rejection_taxonomy_version": REJECTION_TAXONOMY_VERSION,
            "evidence_class": EVIDENCE_CLASS,
            "partition": "deterministic chronological 60/40 by timestamp groups",
        },
    )


def run_d5() -> dict[str, Any]:
    from research_engine.data_access.s3_source import get_default_source
    decisions = get_default_source().read_dataset("decision_trace")
    return analyse(decisions, load_shadow_trades(epoch="CURRENT"))
