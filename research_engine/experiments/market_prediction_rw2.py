"""RW2 leakage-safe market prediction runners for M1, M3, M7, M8 and M11.

The independent grain is one CURRENT canonical opportunity.  Shadow horizons
and account fanout are collapsed before any statistic is calculated.  The
predictive tests are deliberately small: category means learned on an earlier
chronological partition are evaluated on later, unseen opportunities.  No
random split and no post-outcome predictor are used.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math
from typing import Any, Callable, Iterable

from research_engine.experiments.experiment_base import (
    build_fingerprint,
    build_report,
    load_shadow_trades,
)


@dataclass(frozen=True)
class PredictionContract:
    question_id: str
    minimum_total: int
    minimum_discovery: int
    minimum_validation: int
    minimum_cell: int
    candidate_name: str
    baseline_name: str


CONTRACTS = {
    "M1": PredictionContract("M1", 60, 30, 20, 5, "h4_regime", "intercept"),
    "M3": PredictionContract("M3", 90, 50, 30, 8, "h4_regime+market_phase", "h4_regime"),
    "M7": PredictionContract("M7", 90, 50, 30, 8, "h4_regime+market_phase", "best_single_context"),
    "M8": PredictionContract("M8", 60, 30, 20, 5, "market_phase_transition", "intercept"),
    "M11": PredictionContract("M11", 120, 60, 40, 10, "regime+phase+bias", "pattern"),
}

REPORT_FILENAMES = {
    "M1": "m1_h4_regime_prediction_v1.json",
    "M3": "m3_phase_incremental_prediction_v1.json",
    "M7": "m7_regime_phase_prediction_v1.json",
    "M8": "m8_phase_transition_prediction_v1.json",
    "M11": "m11_context_vs_pattern_prediction_v1.json",
}

_POST_OUTCOME_NAMES = frozenset({
    "realised_r", "r_multiple", "pnl_r_multiple", "mfe", "mfe_r", "mae",
    "mae_r", "close_reason", "exit_reason", "future_path", "future_regime",
    "broker_pnl",
})
_REGIMES = frozenset({"TRENDING", "RANGING", "TRANSITIONAL"})
_PHASES = frozenset({"IMPULSE", "PULLBACK", "CONSOLIDATION", "EXHAUSTION", "REVERSAL"})
_BIASES = frozenset({"BULLISH", "BEARISH", "NEUTRAL"})


@dataclass(frozen=True)
class OpportunityObservation:
    canonical_opportunity_id: str
    decision_time: datetime
    outcome_r: float | None
    h4_regime: str = ""
    market_phase: str = ""
    h1_bias: str = ""
    pattern: str = ""
    symbol: str = ""
    entity_id: str = ""
    phase_transition: str = ""


def _nested(record: dict[str, Any], *path: str) -> Any:
    value: Any = record
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _first(record: dict[str, Any], paths: Iterable[tuple[str, ...]]) -> Any:
    for path in paths:
        value = _nested(record, *path)
        if value is not None and value != "":
            return value
    return None


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


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _is_current(record: dict[str, Any]) -> bool:
    epoch = _first(record, (("data_epoch",), ("epoch",), ("provenance", "data_epoch")))
    return epoch is None or _text(epoch).upper() in {"CURRENT", "CURRENT_ONLY"}


def _is_current_authority(record: dict[str, Any], dataset: str) -> bool:
    epoch = _first(record, (("data_epoch",), ("epoch",), ("provenance", "data_epoch")))
    if epoch is not None:
        return _text(epoch).upper() in {"CURRENT", "CURRENT_ONLY"}
    try:
        from core.production_data_contract import current_schema
        return _text(record.get("schema_version")) == current_schema(dataset)
    except (KeyError, TypeError):
        return False


def _shadow_value(record: dict[str, Any], name: str) -> Any:
    paths = {
        "opportunity": (("identity", "canonical_opportunity_id"), ("canonical_opportunity_id",)),
        "entity": (("identity", "entity_id"), ("entity_id",)),
        "symbol": (("identity", "symbol"), ("decision_snapshot", "symbol"), ("symbol",)),
        "time": (("decision_snapshot", "timestamp_decision_utc"), ("timestamp_decision_utc",), ("entry_time",)),
        "regime": (("decision_snapshot", "h4_regime"), ("h4_regime",)),
        "phase": (("decision_snapshot", "market_phase"), ("market_phase",)),
        "bias": (("decision_snapshot", "h1_bias"), ("h1_bias",)),
        "pattern": (("decision_snapshot", "pattern"), ("pattern",)),
        "horizon": (("identity", "evaluated_horizon"), ("decision_snapshot", "trade_horizon"), ("evaluated_horizon",), ("trade_horizon",)),
        "outcome": (("simulated_outcome", "pnl_r_multiple"), ("pnl_r_multiple",)),
    }
    return _first(record, paths[name])


def build_opportunity_observations(
    shadow_records: Iterable[dict[str, Any]],
) -> tuple[list[OpportunityObservation], dict[str, Any]]:
    """Collapse shadow horizons/accounts to one deterministic opportunity row."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    excluded_epoch = excluded_lineage = 0
    for record in shadow_records:
        if not _is_current(record):
            excluded_epoch += 1
            continue
        opportunity = _text(_shadow_value(record, "opportunity"))
        if not opportunity:
            excluded_lineage += 1
            continue
        groups[opportunity].append(record)

    observations: list[OpportunityObservation] = []
    conflicts: list[str] = []
    missing_time = 0
    for opportunity, rows in sorted(groups.items()):
        values: dict[str, set[str]] = {}
        for name in ("entity", "symbol", "time", "regime", "phase", "bias", "pattern"):
            values[name] = {_text(_shadow_value(row, name)) for row in rows if _text(_shadow_value(row, name))}
        # Predictors and time are immutable pre-decision facts. Conflicting
        # values make the canonical opportunity ambiguous and fail closed.
        if any(len(values[name]) > 1 for name in ("entity", "symbol", "time", "regime", "phase", "bias", "pattern")):
            conflicts.append(opportunity)
            continue
        decision_time = _time(next(iter(values["time"]), None))
        if decision_time is None:
            missing_time += 1
            continue
        outcomes_by_horizon: dict[str, set[float]] = defaultdict(set)
        for position, row in enumerate(rows):
            outcome_value = _number(_shadow_value(row, "outcome"))
            if outcome_value is not None:
                horizon = _text(_shadow_value(row, "horizon")) or f"UNDECLARED-{position}"
                outcomes_by_horizon[horizon].add(outcome_value)
        if any(len(values_for_horizon) > 1 for values_for_horizon in outcomes_by_horizon.values()):
            conflicts.append(opportunity)
            continue
        known_outcomes = [next(iter(values_for_horizon)) for values_for_horizon in outcomes_by_horizon.values()]
        # Multiple horizons are repeated measurements. Their opportunity-level
        # mean is one label; missing horizons are not imputed.
        outcome = sum(known_outcomes) / len(known_outcomes) if known_outcomes else None
        observations.append(OpportunityObservation(
            canonical_opportunity_id=opportunity,
            decision_time=decision_time,
            outcome_r=outcome,
            h4_regime=next(iter(values["regime"]), ""),
            market_phase=next(iter(values["phase"]), ""),
            h1_bias=next(iter(values["bias"]), ""),
            pattern=next(iter(values["pattern"]), ""),
            symbol=next(iter(values["symbol"]), ""),
            entity_id=next(iter(values["entity"]), ""),
        ))
    observations.sort(key=lambda row: (row.decision_time, row.canonical_opportunity_id))
    return observations, {
        "raw_shadow_rows": sum(len(rows) for rows in groups.values()),
        "canonical_opportunities": len(groups),
        "observations": len(observations),
        "missing_outcomes": sum(row.outcome_r is None for row in observations),
        "excluded_non_current": excluded_epoch,
        "excluded_missing_lineage": excluded_lineage,
        "excluded_missing_time": missing_time,
        "ambiguous_opportunities": tuple(conflicts),
        "repeated_rows_collapsed": sum(max(0, len(rows) - 1) for rows in groups.values()),
    }


def _trace_context(record: dict[str, Any]) -> tuple[str, datetime | None, str, str, str]:
    opportunity = _text(_first(record, (("canonical_opportunity_id",), ("identity", "canonical_opportunity_id"))))
    timestamp = _time(_first(record, (("timestamp_utc",), ("timestamp_decision_utc",), ("decision", "timestamp_utc"))))
    regime = _text(_first(record, (("v10_market_state", "regime", "regime"), ("h4_regime",))))
    phase = _text(_first(record, (("v10_market_state", "h4", "market_phase"), ("market_phase",))))
    bias = _text(_first(record, (("v10_market_state", "h1", "dominant_trend"), ("h1_bias",))))
    return opportunity, timestamp, regime, phase, bias


def join_decision_context(
    observations: list[OpportunityObservation], decision_records: Iterable[dict[str, Any]],
) -> tuple[list[OpportunityObservation], dict[str, Any]]:
    index: dict[str, list[tuple[datetime | None, str, str, str]]] = defaultdict(list)
    for record in decision_records:
        if not _is_current_authority(record, "decision_trace"):
            continue
        opportunity, timestamp, regime, phase, bias = _trace_context(record)
        if opportunity:
            index[opportunity].append((timestamp, regime, phase, bias))
    joined: list[OpportunityObservation] = []
    conflicts: list[str] = []
    unmatched = 0
    for row in observations:
        contexts = set(index.get(row.canonical_opportunity_id, ()))
        if len(contexts) != 1:
            unmatched += not contexts
            conflicts.extend([row.canonical_opportunity_id] if len(contexts) > 1 else [])
            continue
        timestamp, regime, phase, bias = next(iter(contexts))
        if timestamp is None or timestamp > row.decision_time:
            conflicts.append(row.canonical_opportunity_id)
            continue
        if any(snapshot and authoritative and snapshot != authoritative for snapshot, authoritative in (
            (row.h4_regime, regime), (row.market_phase, phase), (row.h1_bias, bias),
        )):
            conflicts.append(row.canonical_opportunity_id)
            continue
        if not (regime and phase and bias):
            unmatched += 1
            continue
        joined.append(replace(row, h4_regime=regime, market_phase=phase, h1_bias=bias))
    return joined, {"unmatched_decision_context": unmatched, "decision_context_conflicts": tuple(conflicts)}


def _market_context(record: dict[str, Any]) -> tuple[str, str, str, datetime | None, str]:
    opportunity = _text(_first(record, (("canonical_opportunity_id",), ("identity", "canonical_opportunity_id"))))
    entity = _text(_first(record, (("entity_id",), ("identity", "entity_id"))))
    symbol = _text(_first(record, (("symbol",), ("identity", "symbol"))))
    timestamp = _time(_first(record, (("timestamp",), ("bar_time",), ("timestamp_utc",))))
    phase = _text(_first(record, (("market_phase",), ("phase",), ("h4", "market_phase"))))
    return opportunity, entity, symbol, timestamp, phase


def join_market_context(
    observations: list[OpportunityObservation], market_records: Iterable[dict[str, Any]],
) -> tuple[list[OpportunityObservation], dict[str, Any]]:
    by_opportunity: dict[str, list[tuple[str, datetime, str]]] = defaultdict(list)
    by_entity: dict[str, list[tuple[str, datetime, str]]] = defaultdict(list)
    history: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    for record in market_records:
        if not _is_current_authority(record, "market_context"):
            continue
        opportunity, entity, symbol, timestamp, phase = _market_context(record)
        if timestamp is None or not phase:
            continue
        value = (symbol, timestamp, phase)
        if opportunity:
            by_opportunity[opportunity].append(value)
        if entity:
            by_entity[entity].append(value)
        if symbol:
            history[symbol].append((timestamp, phase))
    for rows in history.values():
        rows.sort()

    joined: list[OpportunityObservation] = []
    conflicts: list[str] = []
    unmatched = 0
    for row in observations:
        direct = set(by_opportunity.get(row.canonical_opportunity_id, []))
        via_entity = set(by_entity.get(row.entity_id, [])) if row.entity_id else set()
        if direct and via_entity and direct != via_entity:
            conflicts.append(row.canonical_opportunity_id)
            continue
        candidates = list(direct or via_entity)
        if len(candidates) != 1:
            unmatched += not candidates
            conflicts.extend([row.canonical_opportunity_id] if len(candidates) > 1 else [])
            continue
        symbol, context_time, phase = candidates[0]
        if context_time > row.decision_time or (row.symbol and symbol and row.symbol != symbol):
            conflicts.append(row.canonical_opportunity_id)
            continue
        # Snapshot context is provenance only; disagreement blocks rather than
        # allowing the embedded value to replace canonical market_context.
        if row.market_phase and row.market_phase != phase:
            conflicts.append(row.canonical_opportunity_id)
            continue
        prior = [(ts, value) for ts, value in history.get(symbol or row.symbol, ()) if ts < context_time]
        if not prior:
            unmatched += 1
            continue
        previous_phase = prior[-1][1]
        joined.append(replace(row, market_phase=phase, phase_transition=f"{previous_phase}->{phase}"))
    return joined, {"unmatched_market_context": unmatched, "market_context_conflicts": tuple(conflicts)}


def chronological_split(rows: list[OpportunityObservation], discovery_fraction: float = 0.60) -> tuple[list[OpportunityObservation], list[OpportunityObservation]]:
    ordered = sorted(rows, key=lambda row: (row.decision_time, row.canonical_opportunity_id))
    unique_times = sorted({row.decision_time for row in ordered})
    if len(unique_times) < 2:
        return ordered, []
    boundary_index = max(1, min(len(unique_times) - 1, int(len(unique_times) * discovery_fraction)))
    boundary = unique_times[boundary_index]
    return ([row for row in ordered if row.decision_time < boundary],
            [row for row in ordered if row.decision_time >= boundary])


def assert_leakage_safe_predictors(names: Iterable[str]) -> None:
    forbidden = sorted({_text(name).lower() for name in names}.intersection(_POST_OUTCOME_NAMES))
    if forbidden:
        raise ValueError(f"post-outcome predictor leakage: {', '.join(forbidden)}")


def _key(row: OpportunityObservation, names: tuple[str, ...]) -> str:
    return "|".join(_text(getattr(row, name)) for name in names)


def _fit_means(rows: list[OpportunityObservation], names: tuple[str, ...]) -> tuple[dict[str, float], float]:
    values: dict[str, list[float]] = defaultdict(list)
    outcomes = [row.outcome_r for row in rows if row.outcome_r is not None]
    for row in rows:
        if row.outcome_r is not None:
            values[_key(row, names)].append(row.outcome_r)
    return ({key: sum(group) / len(group) for key, group in values.items()}, sum(outcomes) / len(outcomes))


def _evaluate_model(
    discovery: list[OpportunityObservation], validation: list[OpportunityObservation], names: tuple[str, ...], minimum_cell: int,
) -> dict[str, Any]:
    discovery_counts = Counter(_key(row, names) for row in discovery)
    validation_counts = Counter(_key(row, names) for row in validation)
    eligible = sorted(key for key, count in discovery_counts.items()
                      if count >= minimum_cell and validation_counts[key] >= minimum_cell)
    insufficient = sorted((set(discovery_counts) | set(validation_counts)) - set(eligible))
    model, global_mean = _fit_means(discovery, names)
    scored = [row for row in validation if _key(row, names) in eligible]
    errors = [(row.outcome_r - model[_key(row, names)]) ** 2 for row in scored]
    return {
        "predictors": list(names),
        "eligible_cells": eligible,
        "insufficient_cells": insufficient,
        "discovery_cell_counts": dict(sorted(discovery_counts.items())),
        "validation_cell_counts": dict(sorted(validation_counts.items())),
        "validation_n": len(scored),
        "mse": sum(errors) / len(errors) if errors else None,
        "predictions": {row.canonical_opportunity_id: model[_key(row, names)] for row in scored},
        "discovery_global_mean": global_mean,
    }


def _compare(
    discovery: list[OpportunityObservation], validation: list[OpportunityObservation],
    candidate_names: tuple[str, ...], baseline_names: tuple[str, ...], minimum_cell: int,
) -> dict[str, Any]:
    assert_leakage_safe_predictors((*candidate_names, *baseline_names))
    candidate = _evaluate_model(discovery, validation, candidate_names, minimum_cell)
    if baseline_names:
        baseline = _evaluate_model(discovery, validation, baseline_names, minimum_cell)
    else:
        mean = sum(row.outcome_r for row in discovery if row.outcome_r is not None) / len(discovery)
        baseline = {
            "predictors": [], "eligible_cells": ["ALL"], "discovery_cell_counts": {"ALL": len(discovery)},
            "validation_cell_counts": {"ALL": len(validation)}, "validation_n": len(validation),
            "mse": sum((row.outcome_r - mean) ** 2 for row in validation) / len(validation),
            "predictions": {row.canonical_opportunity_id: mean for row in validation},
            "discovery_global_mean": mean,
        }
    shared = sorted(set(candidate["predictions"]).intersection(baseline["predictions"]))
    outcome_by_id = {row.canonical_opportunity_id: row.outcome_r for row in validation}
    improvements = [
        (outcome_by_id[oid] - baseline["predictions"][oid]) ** 2
        - (outcome_by_id[oid] - candidate["predictions"][oid]) ** 2
        for oid in shared
    ]
    mean_improvement = sum(improvements) / len(improvements) if improvements else None
    if len(improvements) >= 2:
        variance = sum((value - mean_improvement) ** 2 for value in improvements) / (len(improvements) - 1)
        margin = 1.96 * math.sqrt(variance / len(improvements))
        interval = [mean_improvement - margin, mean_improvement + margin]
    else:
        interval = [None, None]
    return {
        "candidate": candidate,
        "baseline": baseline,
        "paired_validation_n": len(shared),
        "validation_mse_improvement": mean_improvement,
        "validation_improvement_95pct_interval": interval,
        "predictive_evidence_supported": bool(interval[0] is not None and interval[0] > 0),
    }


def _descriptive(rows: list[OpportunityObservation], names: tuple[str, ...]) -> dict[str, Any]:
    groups: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        groups[_key(row, names)].append(row.outcome_r)
    return {key: {"n": len(values), "mean_r": sum(values) / len(values)} for key, values in sorted(groups.items())}


def analyse(
    question_id: str,
    shadow_records: Iterable[dict[str, Any]],
    *,
    market_context_records: Iterable[dict[str, Any]] = (),
    decision_trace_records: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    contract = CONTRACTS[question_id]
    shadow_records = list(shadow_records)
    market_context_records = list(market_context_records)
    decision_trace_records = list(decision_trace_records)
    observations, diagnostics = build_opportunity_observations(shadow_records)
    authority = "CURRENT shadow_trades decision_snapshot + outcome"
    if question_id == "M8":
        observations, joined = join_market_context(observations, market_context_records)
        diagnostics.update(joined)
        authority = "canonical CURRENT market_context joined to CURRENT shadow outcome"
    elif question_id == "M11":
        observations, joined = join_decision_context(observations, decision_trace_records)
        diagnostics.update(joined)
        authority = "canonical CURRENT decision_trace context joined to CURRENT shadow pattern/outcome"

    conflicts = tuple(diagnostics.get("ambiguous_opportunities", ())) + tuple(diagnostics.get("market_context_conflicts", ())) + tuple(diagnostics.get("decision_context_conflicts", ()))
    required = {
        "M1": ("h4_regime",),
        "M3": ("h4_regime", "market_phase"),
        "M7": ("h4_regime", "market_phase"),
        "M8": ("phase_transition",),
        "M11": ("h4_regime", "market_phase", "h1_bias", "pattern"),
    }[question_id]
    def canonical_values(row: OpportunityObservation) -> bool:
        return (
            (not row.h4_regime or row.h4_regime in _REGIMES)
            and (not row.market_phase or row.market_phase in _PHASES)
            and (not row.h1_bias or row.h1_bias in _BIASES)
        )

    usable = [row for row in observations if row.outcome_r is not None
              and all(_text(getattr(row, name)) for name in required)
              and canonical_values(row)]
    discovery, validation = chronological_split(usable)
    candidate_names = {
        "M1": ("h4_regime",), "M3": ("h4_regime", "market_phase"),
        "M7": ("h4_regime", "market_phase"), "M8": ("phase_transition",),
        "M11": ("h4_regime", "market_phase", "h1_bias"),
    }[question_id]
    baseline_names = {
        "M1": (), "M3": ("h4_regime",), "M7": ("h4_regime",),
        "M8": (), "M11": ("pattern",),
    }[question_id]

    comparison: dict[str, Any] = {}
    if conflicts:
        status = "BLOCKED"
        reason = f"Ambiguous/conflicting canonical joins for {len(set(conflicts))} opportunities"
    elif question_id == "M8" and not market_context_records:
        status = "BLOCKED"
        reason = "Canonical CURRENT market_context authority is absent; snapshot context cannot substitute"
    elif question_id == "M11" and not decision_trace_records:
        status = "BLOCKED"
        reason = "Canonical CURRENT decision_trace context authority is absent"
    elif len(usable) < contract.minimum_total or len(discovery) < contract.minimum_discovery or len(validation) < contract.minimum_validation:
        status = "WAITING_DATA"
        reason = (f"usable/discovery/validation={len(usable)}/{len(discovery)}/{len(validation)}; "
                  f"need {contract.minimum_total}/{contract.minimum_discovery}/{contract.minimum_validation}")
    else:
        if question_id == "M7":
            regime = _compare(discovery, validation, candidate_names, ("h4_regime",), contract.minimum_cell)
            phase = _compare(discovery, validation, candidate_names, ("market_phase",), contract.minimum_cell)
            regime_gain = regime.get("validation_mse_improvement")
            phase_gain = phase.get("validation_mse_improvement")
            comparison = regime if (regime_gain if regime_gain is not None else -math.inf) <= (phase_gain if phase_gain is not None else -math.inf) else phase
            comparison["single_context_comparisons"] = {"regime": regime, "phase": phase}
        else:
            comparison = _compare(discovery, validation, candidate_names, baseline_names, contract.minimum_cell)
        if len(comparison["candidate"]["eligible_cells"]) < 2 or comparison["paired_validation_n"] < contract.minimum_validation:
            status = "WAITING_DATA"
            reason = "Per-cell or paired later-validation sufficiency is not met"
        else:
            status = "COMPLETE"
            reason = "Canonical chronological prediction evaluation completed"

    descriptive = _descriptive(usable, candidate_names) if usable else {}
    overall = {
        "canonical_question": question_id,
        "research_classification": "predictive",
        "canonical_cells": {
            "h4_regime": sorted(_REGIMES), "market_phase": sorted(_PHASES),
            "h1_bias": sorted(_BIASES),
        },
        "unit_of_analysis": "one canonical_opportunity_id",
        "evidence_authority": authority,
        "descriptive_association": descriptive,
        "discovery": {"n": len(discovery), "period_end": discovery[-1].decision_time.isoformat() if discovery else None},
        "later_validation": {"n": len(validation), "period_start": validation[0].decision_time.isoformat() if validation else None},
        "predictive_evaluation": comparison,
        "predictive_evidence_supported": comparison.get("predictive_evidence_supported") if comparison else None,
        "completion_reason": reason,
        "sample_sufficiency": {
            "minimum_total": contract.minimum_total,
            "minimum_discovery": contract.minimum_discovery,
            "minimum_validation": contract.minimum_validation,
            "minimum_per_cell_in_each_partition": contract.minimum_cell,
            "usable_opportunities": len(usable),
        },
        "diagnostics": diagnostics,
    }
    confidence = "MEDIUM" if status == "COMPLETE" else "INSUFFICIENT_DATA"
    return build_report(
        question_id=question_id,
        status=status,
        overall=overall,
        confidence=confidence,
        dataset={"source": authority, "sample_size": len(usable), "independent_observations": len(usable)},
        fingerprint=build_fingerprint(len(usable), max(0, diagnostics["raw_shadow_rows"] - len(usable)), authority, confidence, "CURRENT"),
        recommendation=("FINDING: predictive evidence evaluated on later unseen opportunities" if status == "COMPLETE" else f"{status}: {reason}"),
        assumptions=[
            "All predictor values are frozen at or before decision time.",
            "Repeated horizons/account fanout are aggregated within canonical opportunity.",
            "Missing outcomes remain missing and are excluded, never imputed.",
        ],
        provenance={
            "experiment_module": __name__, "registry_id": question_id,
            "report_filename": REPORT_FILENAMES[question_id], "evidence_epoch": "CURRENT",
            "partition": "deterministic chronological 60/40 by timestamp groups",
        },
    )


def _load_dataset(name: str) -> list[dict[str, Any]]:
    from research_engine.data_access.s3_source import get_default_source
    return get_default_source().read_dataset(name)


def run_m1() -> dict[str, Any]:
    return analyse("M1", load_shadow_trades(epoch="CURRENT"))


def run_m3() -> dict[str, Any]:
    return analyse("M3", load_shadow_trades(epoch="CURRENT"))


def run_m7() -> dict[str, Any]:
    return analyse("M7", load_shadow_trades(epoch="CURRENT"))


def run_m8() -> dict[str, Any]:
    return analyse("M8", load_shadow_trades(epoch="CURRENT"), market_context_records=_load_dataset("market_context"))


def run_m11() -> dict[str, Any]:
    return analyse("M11", load_shadow_trades(epoch="CURRENT"), decision_trace_records=_load_dataset("decision_trace"))
