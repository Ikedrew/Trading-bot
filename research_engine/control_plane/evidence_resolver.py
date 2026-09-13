"""Read-only, per-question CURRENT evidence resolution.

The resolver loads each logical dataset at most once per snapshot, constructs
the analytical population declared by a canonical question, and evaluates the
registry's requirement vocabulary without running an experiment.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
import math
from typing import Any, Callable, Mapping

from research_engine.data_quality.classifier import DataEpoch, classify_record


_PHYSICAL_DATASETS = {
    "execution_results_v1": "execution_results",
    "protection_audit_v1": "protection_audit",
    "execution_attempts_v1": "execution_attempts",
    "risk_deviation_v1": "risk_deviation",
}
_CANONICAL_V1_SOURCES = frozenset({
    "management_actions", "horizon_candidates", "strategy_candidates",
    "portfolio_rankings", "portfolio_shadow", "execution_results_v1",
    "execution_context", "protection_audit_v1", "execution_attempts_v1",
    "risk_deviation_v1", "opportunities", "assessments",
})
_RUNNER_SUPPLEMENTAL_SOURCES = {
    # run_opp_1 loads these directly even though its registry data_sources tuple
    # currently omits them.  They are inputs, not additional requirements.
    "OPP-1": ("opportunities", "assessments"),
}
_JOIN_FIELDS = (
    "canonical_opportunity_id", "entity_id", "correlation_id", "trade_id",
    "cycle_id", "position_ticket",
)
_FIELD_ALIASES = {
    "r_multiple": ("r_multiple", "pnl_r_multiple", "pnl_r", "r_multiple_realised"),
    "pnl_r_multiple": ("pnl_r_multiple", "pnl_r", "r_multiple"),
    "r_multiple_realised": ("r_multiple_realised", "live_r_multiple"),
    "shadow_r_multiple": ("shadow_r_multiple", "pnl_r_multiple", "pnl_r"),
    "live_r_multiple": ("live_r_multiple", "r_multiple_realised"),
    "strategy": ("strategy", "strategy_id", "strategy_family", "selected_strategy"),
    "pattern": ("pattern", "pattern_name"),
    "score": ("score", "score_strategy", "overall_score", "signal_score"),
    "trade_horizon": ("trade_horizon", "evaluated_horizon", "horizon"),
    "evaluated_horizon": ("evaluated_horizon", "trade_horizon", "horizon"),
    "h4_regime": ("h4_regime", "regime"),
    "regime": ("regime", "h4_regime"),
    "entry_time": ("entry_time", "entry_timestamp", "opened_at", "bar_time"),
    "exit_timestamp": ("exit_timestamp", "closed_at", "exit_time"),
    "session_state": ("session_state", "session"),
    "slippage": ("slippage", "slippage_points"),
    "position_size": ("position_size", "volume", "risk_fraction"),
}
_COVERAGE_FIELDS = {
    "lineage_coverage": ("canonical_opportunity_id", "entity_id", "correlation_id"),
    "outcome_coverage": ("r_multiple",),
    "pattern_coverage": ("pattern",),
    "strategy_coverage": ("strategy",),
    "horizon_coverage": ("trade_horizon",),
    "h4_regime_coverage": ("h4_regime",),
    "market_phase_coverage": ("market_phase",),
}
_COUNT_RULES = frozenset({
    "sample_size", "ranking_rows", "ranked_candidates", "ranking_cycles",
    "assessed_opportunities",
})


@dataclass(frozen=True)
class RequirementResult:
    type: str
    name: str
    required: Any
    current: Any
    satisfied: bool | None
    reason: str
    blocking: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "required": self.required,
            "current": self.current,
            "satisfied": self.satisfied,
            "reason": self.reason,
            "blocking": self.blocking,
        }


@dataclass
class DatasetSlice:
    source: str
    available: bool
    records: list[dict[str, Any]] = field(default_factory=list)
    current_records: list[dict[str, Any]] = field(default_factory=list)
    transitional_count: int = 0
    legacy_count: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "available": self.available,
            "total_records": len(self.records),
            "current_records": len(self.current_records),
            "transitional_excluded": self.transitional_count,
            "legacy_excluded": self.legacy_count,
            "error": self.error,
        }


@dataclass
class EvidenceResolution:
    question_id: str
    sources: list[dict[str, Any]]
    usable_count: int | None
    excluded_count: int
    metrics: dict[str, Any]
    requirements: list[RequirementResult]
    error: str = ""

    def requirements_dict(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.requirements]


class EvidenceSnapshot:
    """Run-scoped dataset cache; no cache is persisted."""

    def __init__(
        self,
        datasets: Mapping[str, list[dict[str, Any]]] | None = None,
        loader: Callable[[str], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._datasets = dict(datasets) if datasets is not None else None
        self._loader = loader or self._load_live
        self._cache: dict[str, DatasetSlice] = {}
        self.load_counts: Counter[str] = Counter()

    def get(self, source: str) -> DatasetSlice:
        if source in self._cache:
            return self._cache[source]
        self.load_counts[source] += 1
        if self._datasets is not None:
            if source not in self._datasets:
                result = DatasetSlice(source=source, available=False, error="Dataset not supplied")
                self._cache[source] = result
                return result
            records = list(self._datasets[source])
        else:
            try:
                records = list(self._loader(source))
            except Exception as exc:
                result = DatasetSlice(
                    source=source,
                    available=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
                self._cache[source] = result
                return result

        current: list[dict[str, Any]] = []
        transitional = 0
        legacy = 0
        for record in records:
            epoch = _record_epoch(record, source)
            if epoch == DataEpoch.CURRENT:
                current.append(record)
            elif epoch == DataEpoch.TRANSITIONAL:
                transitional += 1
            else:
                legacy += 1
        result = DatasetSlice(
            source=source,
            available=True,
            records=records,
            current_records=current,
            transitional_count=transitional,
            legacy_count=legacy,
        )
        self._cache[source] = result
        return result

    @staticmethod
    def _load_live(source: str) -> list[dict[str, Any]]:
        if source == "shadow_trades":
            from research_engine.data_access.s3_source import get_default_source
            from research_engine.data_access.shadow_runtime_ingestion import (
                ingest_completed_shadow_trades,
            )

            return [
                *ingest_completed_shadow_trades(),
                *get_default_source().read_dataset("research_shadow_trades"),
            ]
        from research_engine.data_access.s3_source import get_default_source

        return get_default_source().read_dataset(_PHYSICAL_DATASETS.get(source, source))


def _recursive_value(record: Any, names: tuple[str, ...]) -> Any:
    if not isinstance(record, dict):
        return None
    for name in names:
        if name in record and _present(record[name]):
            return record[name]
    for value in record.values():
        if isinstance(value, dict):
            found = _recursive_value(value, names)
            if _present(found):
                return found
    return None


def _value(record: dict[str, Any], field_name: str) -> Any:
    return _recursive_value(record, _FIELD_ALIASES.get(field_name, (field_name,)))


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _known(value: Any) -> bool:
    """Return whether a value is usable evidence, not an explicit placeholder."""
    return _present(value) and not (
        isinstance(value, str) and value.strip().upper() in {"UNKNOWN", "TRANSITIONAL"}
    )


def _record_epoch(record: dict[str, Any], source: str) -> DataEpoch:
    explicit = _recursive_value(record, ("data_epoch", "epoch"))
    if explicit:
        value = str(explicit).upper()
        if value in ("CURRENT", "CURRENT_ONLY", "SHADOW_TRADES_CURRENT"):
            return DataEpoch.CURRENT
        if value == "TRANSITIONAL":
            return DataEpoch.TRANSITIONAL
        return DataEpoch.LEGACY
    if source in _CANONICAL_V1_SOURCES:
        return DataEpoch.CURRENT
    return classify_record(record)


def _normalise(record: dict[str, Any], source: str) -> dict[str, Any]:
    result = dict(record)
    fields = set(_JOIN_FIELDS)
    fields.update(_FIELD_ALIASES)
    fields.update({
        "market_phase", "mfe_r", "mae_r", "exit_reason", "bars_held",
        "trade_state_progression", "terminal_stage", "terminal_reason", "ev",
        "policy_trade_allowed", "components", "p_success", "schema_version",
        "rank_position", "selection_status", "rank_score", "result_ok", "retcode",
        "action_type", "action_reason", "state", "overall_score", "confidence",
        "selected", "candidate_id", "spread", "protection_status",
        "broker_confirmed_sl", "broker_confirmed_tp", "requested_sl", "requested_tp",
        "risk_classification", "risk_deviation", "actual_risk_R", "planned_risk_R",
    })
    for name in fields:
        value = _value(record, name)
        if _present(value):
            result.setdefault(name, value)
    r_value = _value(record, "r_multiple")
    if _present(r_value):
        result.setdefault("r_multiple", r_value)
        if source == "shadow_trades":
            result.setdefault("shadow_r_multiple", r_value)
        if source == "trade_truth":
            result.setdefault("live_r_multiple", r_value)
            result.setdefault("r_multiple_realised", r_value)
    return result


def _join_key(record: dict[str, Any]) -> tuple[str, str] | None:
    for field_name in _JOIN_FIELDS:
        value = _value(record, field_name)
        if _present(value):
            return field_name, str(value)
    return None


def _merge_population(slices: list[DatasetSlice]) -> tuple[list[dict[str, Any]], int]:
    if not slices:
        return [], 0
    primary = [_normalise(record, slices[0].source) for record in slices[0].current_records]
    excluded_ambiguous = 0
    for dataset in slices[1:]:
        index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for raw in dataset.current_records:
            record = _normalise(raw, dataset.source)
            for field_name in _JOIN_FIELDS:
                value = _value(record, field_name)
                if _present(value):
                    index[(field_name, str(value))].append(record)
        merged: list[dict[str, Any]] = []
        for row in primary:
            matches: dict[int, dict[str, Any]] = {}
            for field_name in _JOIN_FIELDS:
                value = _value(row, field_name)
                if not _present(value):
                    continue
                for match in index.get((field_name, str(value)), []):
                    matches[id(match)] = match
            if len(matches) == 1:
                combined = dict(row)
                for key, value in next(iter(matches.values())).items():
                    if _present(value):
                        combined.setdefault(key, value)
                merged.append(combined)
            elif len(matches) > 1:
                excluded_ambiguous += 1
            else:
                merged.append(row)
        primary = merged
    return primary, excluded_ambiguous


def _portfolio_candidates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ranking in records:
        for candidate in ranking.get("candidates", []) or []:
            row = dict(candidate)
            row.setdefault("cycle_id", ranking.get("cycle_id"))
            rows.append(_normalise(row, "portfolio_rankings"))
    return rows


def _horizon_comparable(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    no_lineage = 0
    for raw in records:
        row = _normalise(raw, "shadow_trades")
        opp = _value(row, "canonical_opportunity_id")
        if not _present(opp):
            no_lineage += 1
            continue
        groups[str(opp)].append(row)
    comparable: list[dict[str, Any]] = []
    for opp, rows in groups.items():
        primary = [row for row in rows if str(_value(row, "shadow_type") or "") == "PRIMARY_HORIZON_SIMULATION" and _present(_value(row, "r_multiple"))]
        alternatives = [row for row in rows if str(_value(row, "shadow_type") or "") == "HORIZON_ALTERNATIVE" and _present(_value(row, "r_multiple"))]
        if len(primary) == 1 and alternatives:
            comparable.append({"canonical_opportunity_id": opp, "selected": primary[0], "alternatives": alternatives})
    return comparable, {"opportunities_total": len(groups), "excluded_no_lineage": no_lineage}


def _strat_pairs(candidate_records: list[dict[str, Any]], shadow_records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    outcomes: dict[str, list[Any]] = defaultdict(list)
    for raw in shadow_records:
        row = _normalise(raw, "shadow_trades")
        if str(_value(row, "shadow_type") or "") != "PRIMARY_HORIZON_SIMULATION":
            continue
        opp = _value(row, "canonical_opportunity_id")
        outcome = _value(row, "r_multiple")
        if _present(opp) and _present(outcome):
            outcomes[str(opp)].append(outcome)
    pairs: list[dict[str, Any]] = []
    excluded = 0
    seen: set[str] = set()
    for raw in candidate_records:
        row = _normalise(raw, "strategy_candidates")
        candidate_id = str(_value(row, "candidate_id") or "")
        if not candidate_id or candidate_id in seen or _value(row, "selected") is not True:
            continue
        seen.add(candidate_id)
        opp = str(_value(row, "canonical_opportunity_id") or "")
        confidence = _value(row, "confidence")
        if not opp or not _present(confidence) or len(outcomes.get(opp, [])) != 1:
            excluded += 1
            continue
        row["r_multiple"] = outcomes[opp][0]
        pairs.append(row)
    return pairs, excluded


def _opportunity_population(by_source: Mapping[str, DatasetSlice]) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    """Mirror OPP-1's classified, outcome-bearing opportunity population."""
    status_by_opportunity: dict[str, set[str]] = defaultdict(set)
    for raw in by_source["horizon_candidates"].current_records:
        row = _normalise(raw, "horizon_candidates")
        opportunity_id = str(_value(row, "canonical_opportunity_id") or "")
        status = str(_value(row, "selection_status") or "").upper()
        if opportunity_id and status:
            status_by_opportunity[opportunity_id].add(status)

    outcomes: dict[str, Any] = {}
    for raw in by_source["shadow_trades"].current_records:
        row = _normalise(raw, "shadow_trades")
        opportunity_id = str(_value(row, "canonical_opportunity_id") or "")
        outcome = _value(row, "r_multiple")
        if opportunity_id and _known(outcome):
            outcomes.setdefault(opportunity_id, outcome)

    assessments: dict[str, dict[str, Any]] = {}
    for raw in by_source["assessments"].current_records:
        row = _normalise(raw, "assessments")
        opportunity_id = str(_value(row, "opportunity_id") or "")
        if opportunity_id:
            assessments.setdefault(opportunity_id, row)

    accepted = {"SELECTED", "PROMOTED", "EXECUTED", "REJECTED", "INELIGIBLE", "NOT_APPLICABLE"}
    rows: list[dict[str, Any]] = []
    classified = 0
    for raw in by_source["opportunities"].current_records:
        row = _normalise(raw, "opportunities")
        local_id = str(_value(row, "opportunity_id") or "")
        canonical_id = str(_value(row, "canonical_opportunity_id") or local_id)
        if not canonical_id or not status_by_opportunity.get(canonical_id, set()).intersection(accepted):
            continue
        classified += 1
        if canonical_id not in outcomes:
            continue
        combined = dict(row)
        if local_id in assessments:
            for key, value in assessments[local_id].items():
                if _present(value):
                    combined.setdefault(key, value)
        combined["r_multiple"] = outcomes[canonical_id]
        rows.append(combined)
    total = len(by_source["opportunities"].current_records)
    metrics = {
        "opportunities_total": total,
        "classified_opportunities": classified,
        "opportunities_with_outcomes": len(rows),
        "assessments_total": len(by_source["assessments"].current_records),
    }
    return rows, total - len(rows), metrics


def _population(question: Any, slices: list[DatasetSlice]) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    by_source = {dataset.source: dataset for dataset in slices}
    metrics: dict[str, Any] = {}
    if question.id == "D6":
        rankings = by_source["portfolio_rankings"].current_records
        rows = _portfolio_candidates(rankings)
        metrics.update(ranking_rows=len(rankings), ranking_cycles=len(rankings), ranked_candidates=len(rows))
        return rows, 0, metrics
    if question.id == "PORT-1":
        rankings = by_source["portfolio_rankings"].current_records
        rows = [_normalise(row, "portfolio_rankings") for row in rankings]
        metrics.update(ranking_rows=len(rows), ranking_cycles=len(rows), ranked_candidates=len(_portfolio_candidates(rankings)))
        return rows, 0, metrics
    if question.id == "HORIZON-1":
        rows, extra = _horizon_comparable(by_source["shadow_trades"].current_records)
        metrics.update(extra, matched_pairs=len(rows))
        return rows, extra["excluded_no_lineage"], metrics
    if question.id == "STRAT-1":
        rows, excluded = _strat_pairs(
            by_source["strategy_candidates"].current_records,
            by_source["shadow_trades"].current_records,
        )
        metrics["matched_pairs"] = len(rows)
        return rows, excluded, metrics
    if question.id == "OPP-1":
        return _opportunity_population(by_source)
    if question.id == "X2":
        rows = [
            *[_normalise(row, "execution_results_v1") for row in by_source["execution_results_v1"].current_records],
            *[_normalise(row, "execution_attempts_v1") for row in by_source["execution_attempts_v1"].current_records],
        ]
        return rows, 0, metrics
    rows, ambiguous = _merge_population(slices)
    return rows, ambiguous, metrics


def _coverage(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> float:
    if not rows:
        return 0.0
    present = sum(1 for row in rows if any(_known(_value(row, name)) for name in fields))
    return round(present / len(rows), 4)


def _compare(current: float, operator: str, required: float) -> bool | None:
    operations = {
        ">=": lambda: current >= required,
        ">": lambda: current > required,
        "<=": lambda: current <= required,
        "<": lambda: current < required,
        "==": lambda: current == required,
        "!=": lambda: current != required,
    }
    return operations[operator]() if operator in operations else None


def _covered_days(rows: list[dict[str, Any]]) -> int:
    dates = set()
    for row in rows:
        value = _value(row, "entry_time") or _value(row, "timestamp")
        if not value:
            continue
        try:
            dates.add(datetime.fromisoformat(str(value).replace("Z", "+00:00")).date())
        except ValueError:
            continue
    return len(dates)


def resolve_question_evidence(question: Any, snapshot: EvidenceSnapshot) -> EvidenceResolution:
    """Resolve one question from cached CURRENT evidence only."""
    source_names = [source.value for source in question.data_sources]
    source_names.extend(_RUNNER_SUPPLEMENTAL_SOURCES.get(question.id, ()))
    slices = [snapshot.get(source) for source in dict.fromkeys(source_names)]
    source_dicts = [dataset.to_dict() for dataset in slices]
    errors = [f"{dataset.source}: {dataset.error}" for dataset in slices if dataset.error and dataset.available is False]
    requirements: list[RequirementResult] = []
    for dataset in slices:
        requirements.append(RequirementResult(
            type="dataset_presence",
            name=dataset.source,
            required=True,
            current=dataset.available,
            satisfied=dataset.available,
            reason=("Dataset loaded" if dataset.available else dataset.error or "Dataset unavailable"),
            blocking=not dataset.available,
        ))
    if errors:
        requirements.extend(
            RequirementResult(
                type="required_field",
                name=field_name,
                required="present",
                current=None,
                satisfied=None,
                reason="Cannot evaluate field until required datasets load",
                blocking=True,
            )
            for field_name in question.required_fields
        )
        for rule in question.validation_rules:
            if rule.field in _COVERAGE_FIELDS:
                requirement_type = "coverage"
            elif rule.field == "sample_size":
                requirement_type = "sample_size"
            elif rule.field in _COUNT_RULES:
                requirement_type = "population_count"
            else:
                requirement_type = "unsupported"
            requirements.append(RequirementResult(
                type=requirement_type,
                name=rule.field,
                required={"operator": rule.operator, "value": rule.threshold},
                current=None,
                satisfied=None,
                reason="Cannot evaluate requirement until required datasets load",
                blocking=requirement_type == "coverage",
            ))
        return EvidenceResolution(
            question_id=question.id,
            sources=source_dicts,
            usable_count=None,
            excluded_count=sum(len(item.records) for item in slices),
            metrics={},
            requirements=requirements,
            error="; ".join(errors),
        )

    rows, population_excluded, metrics = _population(question, slices)
    base_count = len(rows)
    metrics["total_current_population"] = base_count
    metrics["transitional_excluded"] = sum(item.transitional_count for item in slices)
    metrics["legacy_excluded"] = sum(item.legacy_count for item in slices)
    metrics["ambiguous_or_unmatched_excluded"] = population_excluded
    for metric_name, fields in _COVERAGE_FIELDS.items():
        metrics[metric_name] = _coverage(rows, fields)
    metrics["covered_days"] = _covered_days(rows)
    metrics["covered_sessions"] = len({str(_value(row, "session_state")) for row in rows if _present(_value(row, "session_state"))})

    missing_fields: list[str] = []
    for field_name in question.required_fields:
        count = sum(1 for row in rows if _known(_value(row, field_name)))
        satisfied = count > 0 if rows else False
        reason = f"Required field {field_name!r} present in {count}/{base_count} CURRENT analytical rows"
        requirements.append(RequirementResult(
            type="required_field",
            name=field_name,
            required="present",
            current=count,
            satisfied=satisfied,
            reason=reason,
            blocking=bool(rows) and not satisfied,
        ))
        if not satisfied:
            missing_fields.append(field_name)

    usable = [
        row for row in rows
        if all(_known(_value(row, field_name)) for field_name in question.required_fields)
    ]
    if question.id == "EX2":
        usable = [row for row in usable if float(_value(row, "mfe_r")) >= 0.5]
    usable_count = len(usable)
    metrics["total_eligible"] = usable_count
    metrics["excluded_missing_required_fields"] = base_count - usable_count

    categorical = [
        field_name for field_name in question.required_fields
        if field_name in {"strategy", "pattern", "trade_horizon", "h4_regime", "market_phase", "session_state", "action_type"}
    ]
    if len(categorical) >= 2 and usable:
        cells = Counter(tuple(str(_value(row, field_name)) for field_name in categorical) for row in usable)
        metrics["valid_combinations"] = len(cells)
        metrics["per_cell_min"] = min(cells.values())

    if question.id == "OPP-1":
        metrics["assessed_opportunities"] = len({
            str(_value(row, "opportunity_id")) for row in usable
            if _present(_value(row, "opportunity_id"))
        })

    for rule in question.validation_rules:
        if rule.field in _COVERAGE_FIELDS:
            current = metrics[rule.field]
            requirement_type = "coverage"
        elif rule.field in _COUNT_RULES:
            current = usable_count if rule.field == "sample_size" else metrics.get(rule.field)
            requirement_type = "sample_size" if rule.field == "sample_size" else "population_count"
        else:
            current = None
            requirement_type = "unsupported"
        satisfied = None if current is None else _compare(float(current), rule.operator, float(rule.threshold))
        if current is None:
            reason = f"Requirement {rule.field!r} is not implemented by the evidence resolver"
        else:
            reason = f"{rule.field}={current}; requires {rule.operator} {rule.threshold}"
        requirements.append(RequirementResult(
            type=requirement_type,
            name=rule.field,
            required={"operator": rule.operator, "value": rule.threshold},
            current=current,
            satisfied=satisfied,
            reason=reason,
            blocking=(requirement_type == "coverage" and satisfied is False),
        ))

    excluded = (
        sum(item.transitional_count + item.legacy_count for item in slices)
        + population_excluded
        + (base_count - usable_count)
    )
    return EvidenceResolution(
        question_id=question.id,
        sources=source_dicts,
        usable_count=usable_count,
        excluded_count=excluded,
        metrics=dict(sorted(metrics.items())),
        requirements=requirements,
    )
