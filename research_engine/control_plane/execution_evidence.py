"""Governed account-execution evidence shared by X3, EXEC1, and X6.

This module supplies evidence, identity, strict joins, and provenance only.  It
does not implement a question estimand, scientific classification, readiness,
or report ownership.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Iterable, Mapping, Sequence

from research_engine.control_plane.evidence_provenance import (
    EvidenceSelection,
    attest_current_subset,
    build_evidence_provenance,
    evidence_digest,
    select_current_evidence,
)
RESULT_SOURCE = "execution_results_v1"
CONTEXT_SOURCE = "execution_context"
TRACE_SOURCE = "decision_trace_v1"
MEASURED_SLIPPAGE_SEMANTIC = "measured_execution_slippage"


class ExecutionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ExecutionObservation:
    """One account/broker execution result attached to governed context."""

    observation_identity: tuple[tuple[str, str], ...]
    cluster_id: str
    result: dict[str, Any]
    context: dict[str, Any]
    decision_trace: dict[str, Any] | None
    status: ExecutionStatus
    absolute_measured_slippage: float | None
    session_state: Any
    spread: Any
    spread_atr_ratio: Any
    bid: Any
    ask: Any
    action: Any
    volatility_state: Any
    result_fields: dict[str, Any]
    _result_source_record: dict[str, Any]
    _context_source_record: dict[str, Any]
    _trace_source_record: dict[str, Any] | None


@dataclass(frozen=True)
class GovernedExecutionEvidence:
    """CURRENT-selected, joined execution population plus deterministic audit."""

    observations: tuple[ExecutionObservation, ...]
    account_result_count: int
    distinct_decision_count: int
    clusters: dict[str, tuple[tuple[tuple[str, str], ...], ...]]
    component_accounting: dict[str, dict[str, Any]]
    exclusion_accounting: dict[str, int]
    provenance: dict[str, Any]
    decision_trace_required: bool
    _result_inputs: tuple[dict[str, Any], ...]
    _context_inputs: tuple[dict[str, Any], ...]
    _trace_inputs: tuple[dict[str, Any], ...]


def interpret_execution_status(value: Any) -> ExecutionStatus:
    """Interpret only explicit booleans; missing/coerced values remain UNKNOWN."""
    if value is True:
        return ExecutionStatus.SUCCESS
    if value is False:
        return ExecutionStatus.FAILURE
    return ExecutionStatus.UNKNOWN


def measured_absolute_slippage(record: Mapping[str, Any]) -> float | None:
    """Return absolute producer-measured slippage, never reconstructed slippage."""
    if record.get("slippage_semantic") != MEASURED_SLIPPAGE_SEMANTIC:
        return None
    value = record.get("slippage")
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return abs(number) if math.isfinite(number) else None


def _path(record: Mapping[str, Any], *names: str) -> Any:
    value: Any = record
    for name in names:
        if not isinstance(value, Mapping) or name not in value:
            return None
        value = value[name]
    return value


def _first(record: Mapping[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value = _path(record, *path)
        if value is not None and value != "":
            return value
    return None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _nonzero_text(value: Any) -> str:
    text = _text(value)
    return "" if text in {"", "0", "0.0", "None"} else text


def _result_fact(record: Mapping[str, Any], name: str) -> Any:
    paths: dict[str, tuple[tuple[str, ...], ...]] = {
        "correlation_id": (("correlation_id",), ("identity", "correlation_id")),
        "canonical_opportunity_id": (("canonical_opportunity_id",), ("identity", "canonical_opportunity_id")),
        "decision_id": (("decision_id",), ("identity", "decision_id")),
        "execution_id": (("execution_id",), ("identity", "execution_id")),
        "trade_id": (("trade_id",), ("identity", "trade_id")),
        "symbol": (
            ("canonical_symbol",), ("symbol",),
            ("identity", "canonical_symbol"), ("identity", "symbol"),
        ),
        "account_id": (("account_id",), ("identity", "account_id")),
        "broker": (("broker",), ("identity", "broker")),
        "broker_server": (("broker_server",), ("identity", "broker_server")),
        "broker_symbol": (("broker_symbol",), ("identity", "broker_symbol")),
        "order_ticket": (("order_ticket",), ("order",), ("response", "order_id")),
        "deal_ticket": (("deal_ticket",), ("deal",), ("response", "deal_id")),
        "position_ticket": (("position_ticket",), ("identity", "position_ticket")),
        "entry_reference": (("entry_reference",), ("request", "entry_reference")),
        "fill_price": (("fill_price",), ("fill", "price")),
        "requested_volume": (("request", "volume"), ("volume",)),
        "submitted_volume": (("submission", "volume"),),
        "requested_sl": (("request", "sl"), ("requested_sl",)),
        "submitted_sl": (("submission", "sl"), ("sl",)),
        "requested_tp": (("request", "tp"), ("requested_tp",)),
        "submitted_tp": (("submission", "tp"), ("tp",)),
    }
    return _first(record, *paths[name])


def _observation_identity(record: Mapping[str, Any]) -> tuple[tuple[str, str], ...] | None:
    correlation_id = _nonzero_text(_result_fact(record, "correlation_id"))
    if not correlation_id:
        return None
    execution_id = _nonzero_text(_result_fact(record, "execution_id"))
    if execution_id:
        return (("correlation_id", correlation_id), ("execution_id", execution_id))
    account_id = _nonzero_text(_result_fact(record, "account_id"))
    if account_id:
        return (("correlation_id", correlation_id), ("account_id", account_id))
    for name in ("order_ticket", "deal_ticket", "position_ticket"):
        value = _nonzero_text(_result_fact(record, name))
        if value:
            return (("correlation_id", correlation_id), (name, value))
    return None


_RESULT_CONFLICT_FIELDS = (
    "canonical_opportunity_id", "decision_id", "execution_id", "trade_id",
    "symbol", "account_id", "broker",
    "broker_server", "broker_symbol", "order_ticket", "deal_ticket", "position_ticket",
    "fill_price",
)


def _is_supplemental_result(record: Mapping[str, Any]) -> bool:
    markers = (record.get("source"), record.get("record_type"), record.get("comment"))
    return any(str(value or "").strip().lower() == "protection_verification" for value in markers)


def _explicit_conflict(left: Mapping[str, Any], right: Mapping[str, Any], field: str) -> bool:
    left_value = _nonzero_text(_result_fact(left, field))
    right_value = _nonzero_text(_result_fact(right, field))
    return bool(left_value and right_value and left_value != right_value)


def _result_group_conflicts(records: Sequence[Mapping[str, Any]]) -> bool:
    for index, left in enumerate(records):
        for right in records[index + 1:]:
            if any(_explicit_conflict(left, right, field) for field in _RESULT_CONFLICT_FIELDS):
                return True
            if _is_supplemental_result(left) or _is_supplemental_result(right):
                continue
            left_status = interpret_execution_status(left.get("result_ok"))
            right_status = interpret_execution_status(right.get("result_ok"))
            if (
                left_status is not ExecutionStatus.UNKNOWN
                and right_status is not ExecutionStatus.UNKNOWN
                and left_status is not right_status
            ):
                return True
            for field in ("retcode", "slippage", "slippage_semantic"):
                lv, rv = left.get(field), right.get(field)
                if lv is not None and rv is not None and str(lv) != str(rv):
                    return True
    return False


def _record_digest(record: Mapping[str, Any]) -> str:
    return evidence_digest((record,))


def _digestible_records(
    records: Sequence[dict[str, Any]], exclusions: Counter[str], *, label: str,
) -> tuple[dict[str, Any], ...]:
    """Quarantine records that cannot cross deterministic JSON provenance."""
    accepted: list[dict[str, Any]] = []
    for record in records:
        try:
            _record_digest(record)
        except (TypeError, ValueError):
            exclusions[f"malformed_invalid_{label}_record"] += 1
        else:
            accepted.append(record)
    return tuple(accepted)


def _result_representative(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def rank(record: Mapping[str, Any]) -> tuple[int, str]:
        supplemental = 1 if _is_supplemental_result(record) else 0
        return supplemental, _record_digest(record)
    return min(records, key=rank)


def _deduplicate_results(
    records: Sequence[dict[str, Any]], exclusions: Counter[str],
) -> list[tuple[tuple[tuple[str, str], ...], dict[str, Any]]]:
    grouped: dict[tuple[tuple[str, str], ...], list[dict[str, Any]]] = defaultdict(list)
    for raw in records:
        correlation_id = _nonzero_text(_result_fact(raw, "correlation_id"))
        if not correlation_id:
            exclusions["missing_correlation_id"] += 1
            continue
        identity = _observation_identity(raw)
        if identity is None:
            exclusions["missing_account_execution_identity"] += 1
            continue
        grouped[identity].append(raw)

    accepted: list[tuple[tuple[tuple[str, str], ...], dict[str, Any]]] = []
    for identity in sorted(grouped):
        group = grouped[identity]
        unique_by_digest: dict[str, dict[str, Any]] = {}
        for record in group:
            digest = _record_digest(record)
            if digest in unique_by_digest:
                exclusions["duplicate_persistence"] += 1
            else:
                unique_by_digest[digest] = record
        unique = list(unique_by_digest.values())
        if _result_group_conflicts(unique):
            exclusions["conflicting_duplicate_identity"] += len(group)
            continue
        primary = [record for record in unique if not _is_supplemental_result(record)]
        if not primary:
            exclusions["supplemental_non_observation"] += len(group)
            continue
        if len(unique) > 1:
            exclusions["supplemental_non_observation"] += len(unique) - 1
        accepted.append((identity, _result_representative(primary)))
    return accepted


def _group_single_authority(
    records: Sequence[dict[str, Any]], exclusions: Counter[str], *, label: str,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        correlation_id = _nonzero_text(_first(record, ("correlation_id",), ("identity", "correlation_id")))
        if not correlation_id:
            exclusions[f"missing_{label}_correlation_id"] += 1
            continue
        grouped[correlation_id].append(record)
    result: dict[str, dict[str, Any]] = {}
    for correlation_id in sorted(grouped):
        unique = {_record_digest(record): record for record in grouped[correlation_id]}
        exclusions[f"duplicate_{label}_persistence"] += len(grouped[correlation_id]) - len(unique)
        if len(unique) != 1:
            exclusions[f"ambiguous_conflicting_{label}"] += len(grouped[correlation_id])
            continue
        result[correlation_id] = next(iter(unique.values()))
    return result


def _identity_conflict(left: Mapping[str, Any], right: Mapping[str, Any]) -> str | None:
    checks = (
        ("canonical_opportunity_id", "canonical_opportunity_conflict"),
        ("symbol", "canonical_symbol_conflict"),
        ("decision_id", "decision_identity_conflict"),
    )
    for field, reason in checks:
        paths = (
            (("canonical_symbol",), ("symbol",), ("identity", "canonical_symbol"), ("identity", "symbol"))
            if field == "symbol" else ((field,), ("identity", field))
        )
        lv = _first(left, *paths)
        rv = _first(right, *paths)
        if lv is not None and lv != "" and rv is not None and rv != "" and str(lv) != str(rv):
            return reason
    return None


def _component_accounting(selection: EvidenceSelection) -> dict[str, Any]:
    component = selection.component
    counts = component["epoch_counts"]
    return {
        "input_records": component["input_records"],
        "accepted_current_records": counts["CURRENT"],
        "stale_records_excluded": counts["TRANSITIONAL"] + counts["LEGACY"],
        "schema_incompatible_records_excluded": counts["INCOMPATIBLE"],
        "records_excluded": component["records_excluded"],
        "digest": component["digest"],
        "state": component["state"],
    }


def _result_fields(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "result_ok": record.get("result_ok"),
        "retcode": record.get("retcode"),
        "comment": record.get("comment"),
        "slippage": record.get("slippage"),
        "slippage_semantic": record.get("slippage_semantic"),
        **{name: _result_fact(record, name) for name in (
            "entry_reference", "fill_price", "requested_volume", "submitted_volume",
            "requested_sl", "submitted_sl", "requested_tp", "submitted_tp",
            "account_id", "broker", "broker_server", "symbol", "broker_symbol",
            "order_ticket", "deal_ticket", "position_ticket", "correlation_id",
            "canonical_opportunity_id", "decision_id", "execution_id", "trade_id",
        )},
    }


def _observation_token(observation: ExecutionObservation) -> str:
    return evidence_digest(({
        "identity": observation.observation_identity,
        "cluster": observation.cluster_id,
        "status": observation.status,
        "absolute_measured_slippage": observation.absolute_measured_slippage,
        "session_state": observation.session_state,
        "spread": observation.spread,
        "spread_atr_ratio": observation.spread_atr_ratio,
        "bid": observation.bid,
        "ask": observation.ask,
        "action": observation.action,
        "volatility_state": observation.volatility_state,
        "result_fields": observation.result_fields,
        "result": observation._result_source_record,
        "context": observation._context_source_record,
        "trace": observation._trace_source_record,
    },))


def attest_execution_analytical_subset(
    evidence: GovernedExecutionEvidence,
    observations: Iterable[ExecutionObservation],
) -> dict[str, Any]:
    """Attest an exact duplicate-preserving subset of the governed join output."""
    selected = list(observations)
    available = Counter(_observation_token(item) for item in evidence.observations)
    requested = Counter(_observation_token(item) for item in selected)
    if requested - available:
        raise ValueError("Analytical execution subset is not part of the governed joined population")

    result_records = [item._result_source_record for item in selected]
    context_by_digest = {
        _record_digest(item._context_source_record): item._context_source_record
        for item in selected
    }
    selections = [
        attest_current_subset(RESULT_SOURCE, evidence._result_inputs, result_records),
        attest_current_subset(CONTEXT_SOURCE, evidence._context_inputs, context_by_digest.values()),
    ]
    if evidence.decision_trace_required:
        trace_by_digest = {
            _record_digest(item._trace_source_record): item._trace_source_record
            for item in selected if item._trace_source_record is not None
        }
        selections.append(
            attest_current_subset(TRACE_SOURCE, evidence._trace_inputs, trace_by_digest.values())
        )
    return build_evidence_provenance(*selections)


def build_governed_execution_evidence(
    execution_results: Iterable[Mapping[str, Any]],
    execution_contexts: Iterable[Mapping[str, Any]],
    decision_traces: Iterable[Mapping[str, Any]] | None = None,
    *,
    require_decision_trace: bool = False,
) -> GovernedExecutionEvidence:
    """Select CURRENT execution evidence and construct the strict joined population."""
    supplied_results = tuple(deepcopy(dict(item)) for item in execution_results)
    supplied_contexts = tuple(deepcopy(dict(item)) for item in execution_contexts)
    supplied_traces = tuple(deepcopy(dict(item)) for item in (decision_traces or ()))
    exclusions: Counter[str] = Counter()
    result_inputs = _digestible_records(supplied_results, exclusions, label="result")
    context_inputs = _digestible_records(supplied_contexts, exclusions, label="context")
    trace_inputs = _digestible_records(supplied_traces, exclusions, label="decision_trace")
    result_selection = select_current_evidence(RESULT_SOURCE, result_inputs)
    context_selection = select_current_evidence(CONTEXT_SOURCE, context_inputs)
    trace_selection = select_current_evidence(TRACE_SOURCE, trace_inputs) if require_decision_trace else None

    current_results = result_selection.records_for_analysis()
    current_contexts = context_selection.records_for_analysis()
    current_traces = trace_selection.records_for_analysis() if trace_selection else []
    deduplicated_results = _deduplicate_results(current_results, exclusions)
    contexts = _group_single_authority(current_contexts, exclusions, label="context")
    traces = (
        _group_single_authority(current_traces, exclusions, label="decision_trace")
        if require_decision_trace else {}
    )

    observations: list[ExecutionObservation] = []
    for identity, raw_result in deduplicated_results:
        correlation_id = dict(identity)["correlation_id"]
        raw_context = contexts.get(correlation_id)
        if raw_context is None:
            reason = (
                "ambiguous_conflicting_context"
                if any(_nonzero_text(_first(row, ("correlation_id",))) == correlation_id for row in current_contexts)
                else "missing_context"
            )
            exclusions[reason] += 1
            continue
        conflict = _identity_conflict(raw_result, raw_context)
        if conflict:
            exclusions[conflict] += 1
            continue

        raw_trace = None
        if require_decision_trace:
            raw_trace = traces.get(correlation_id)
            if raw_trace is None:
                reason = (
                    "ambiguous_conflicting_decision_trace"
                    if any(_nonzero_text(_first(row, ("correlation_id",))) == correlation_id for row in current_traces)
                    else "missing_decision_trace"
                )
                exclusions[reason] += 1
                continue
            conflict = _identity_conflict(raw_result, raw_trace) or _identity_conflict(raw_context, raw_trace)
            if conflict:
                exclusions[conflict] += 1
                continue

        # Keep the governed producer records intact.  The generic compatibility
        # normaliser can derive requested-vs-fill slippage; this foundation is
        # deliberately stricter and exposes only producer-measured slippage via
        # ``absolute_measured_slippage``.
        result = deepcopy(raw_result)
        context = deepcopy(raw_context)
        trace = deepcopy(raw_trace) if raw_trace is not None else None
        market_access = raw_context.get("market_access") if isinstance(raw_context.get("market_access"), Mapping) else {}
        observations.append(ExecutionObservation(
            observation_identity=identity,
            cluster_id=correlation_id,
            result=result,
            context=context,
            decision_trace=trace,
            status=interpret_execution_status(raw_result.get("result_ok")),
            absolute_measured_slippage=measured_absolute_slippage(raw_result),
            session_state=market_access.get("session_state"),
            spread=market_access.get("spread"),
            spread_atr_ratio=market_access.get("spread_atr_ratio"),
            bid=market_access.get("bid"),
            ask=market_access.get("ask"),
            action=raw_trace.get("action") if raw_trace is not None else None,
            volatility_state=(
                _path(raw_trace, "v10_market_state", "regime", "volatility_state")
                if raw_trace is not None else None
            ),
            result_fields=_result_fields(raw_result),
            _result_source_record=deepcopy(raw_result),
            _context_source_record=deepcopy(raw_context),
            _trace_source_record=deepcopy(raw_trace),
        ))

    observations.sort(key=lambda item: item.observation_identity)
    clusters: dict[str, list[tuple[tuple[str, str], ...]]] = defaultdict(list)
    for observation in observations:
        clusters[observation.cluster_id].append(observation.observation_identity)
    frozen_clusters = {
        key: tuple(sorted(value)) for key, value in sorted(clusters.items())
    }
    component_accounting = {
        RESULT_SOURCE: _component_accounting(result_selection),
        CONTEXT_SOURCE: _component_accounting(context_selection),
    }
    component_accounting[RESULT_SOURCE]["malformed_invalid_records_excluded"] = (
        len(supplied_results) - len(result_inputs)
    )
    component_accounting[RESULT_SOURCE]["input_records_including_malformed"] = len(supplied_results)
    component_accounting[CONTEXT_SOURCE]["malformed_invalid_records_excluded"] = (
        len(supplied_contexts) - len(context_inputs)
    )
    component_accounting[CONTEXT_SOURCE]["input_records_including_malformed"] = len(supplied_contexts)
    if trace_selection is not None:
        component_accounting[TRACE_SOURCE] = _component_accounting(trace_selection)
        component_accounting[TRACE_SOURCE]["malformed_invalid_records_excluded"] = (
            len(supplied_traces) - len(trace_inputs)
        )
        component_accounting[TRACE_SOURCE]["input_records_including_malformed"] = len(supplied_traces)

    placeholder = GovernedExecutionEvidence(
        observations=tuple(observations),
        account_result_count=len(observations),
        distinct_decision_count=len(frozen_clusters),
        clusters=frozen_clusters,
        component_accounting=component_accounting,
        exclusion_accounting=dict(sorted((key, value) for key, value in exclusions.items() if value)),
        provenance={},
        decision_trace_required=require_decision_trace,
        _result_inputs=result_inputs,
        _context_inputs=context_inputs,
        _trace_inputs=trace_inputs,
    )
    provenance = attest_execution_analytical_subset(placeholder, observations)
    return GovernedExecutionEvidence(
        **{**placeholder.__dict__, "provenance": provenance}
    )


__all__ = [
    "CONTEXT_SOURCE",
    "ExecutionObservation",
    "ExecutionStatus",
    "GovernedExecutionEvidence",
    "MEASURED_SLIPPAGE_SEMANTIC",
    "RESULT_SOURCE",
    "TRACE_SOURCE",
    "attest_execution_analytical_subset",
    "build_governed_execution_evidence",
    "interpret_execution_status",
    "measured_absolute_slippage",
]
