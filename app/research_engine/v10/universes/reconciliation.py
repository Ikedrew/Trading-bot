"""Stage IV Wave 3 conditional cross-universe reconciliation.

The reconciler is an explicit, bounded assurance operation over supplied
evidence.  Wave 1 contracts remain the semantic authority and Wave 2 findings
remain the integrity authority.  No live producer, builder, or persisted source
is read or mutated here.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from research_engine.v10.universes.assurance import (
    expected_active_universes,
    get_universe_contract,
)
from research_engine.v10.universes.evidence_integrity import (
    EvidenceBatch,
    EvidenceManifest,
    IntegrityFinding,
    IntegrityStatus,
    ReconstructedArtifact,
    ReconstructionState,
)
from research_engine.v10.universes.models import Universe


RECONCILIATION_SCHEMA_VERSION = 1


class ExpectationState(str, Enum):
    REQUIRED = "REQUIRED"
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"
    BLOCKED_BY_INTEGRITY = "BLOCKED_BY_INTEGRITY"
    HISTORICAL_LIMITATION = "HISTORICAL_LIMITATION"


class RelationshipStatus(str, Enum):
    RECONCILED = "RECONCILED"
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    MISSING_COUNTERPART = "MISSING_COUNTERPART"
    EXTRA_COUNTERPART = "EXTRA_COUNTERPART"
    CARDINALITY_CONFLICT = "CARDINALITY_CONFLICT"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    SEMANTIC_MISMATCH = "SEMANTIC_MISMATCH"
    TIMING_MISMATCH = "TIMING_MISMATCH"
    BLOCKED_BY_INTEGRITY = "BLOCKED_BY_INTEGRITY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    HISTORICAL_LIMITATION = "HISTORICAL_LIMITATION"
    AMBIGUOUS = "AMBIGUOUS"
    ORPHAN_EVIDENCE = "ORPHAN_EVIDENCE"


class Cardinality(str, Enum):
    EXACTLY_ONE = "1:1"
    ZERO_OR_ONE = "1:0..1"
    ONE_OR_MANY = "1:1..many"


class MatchKind(str, Enum):
    DETERMINISTIC = "DETERMINISTIC"
    CONTRACT_FALLBACK = "CONTRACT_FALLBACK"
    NONE = "NONE"


class ComparisonKind(str, Enum):
    EXACT = "EXACT_EQUALITY"
    NORMALIZED = "NORMALIZED_EQUALITY"
    DERIVED = "DERIVED_AGREEMENT"


@dataclass(frozen=True)
class ReconciliationRule:
    rule_id: str
    source_universe: Universe
    target_universe: Universe
    source_condition: str
    target_expectation: str
    join_fields: tuple[str, ...]
    fallback_join_fields: tuple[str, ...]
    cardinality: Cardinality
    timing_condition: str
    legitimate_absence: str
    pending_condition: str
    integrity_prerequisites: tuple[str, ...]
    semantic_fields: tuple[str, ...]
    bidirectional_orphan_check: bool = True
    source_dataset: str = ""
    historical_limitation: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_universe"] = self.source_universe.value
        value["target_universe"] = self.target_universe.value
        value["cardinality"] = self.cardinality.value
        return _native(value)


@dataclass(frozen=True)
class ExpectationDecision:
    source_universe: str
    source_identity: Mapping[str, Any]
    target_universe: str
    state: str
    rule_id: str
    supporting_evidence: tuple[str, ...]
    integrity_prerequisites: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class SemanticCheck:
    comparison: str
    source_field: str
    target_field: str
    source_value: Any
    target_value: Any
    agrees: bool
    reason: str


@dataclass(frozen=True)
class ReconciliationTrace:
    source_evidence_reference: str
    contract_relationship: str
    condition_evaluated: str
    condition_evidence: tuple[str, ...]
    expectation_state: str
    join_identity: Mapping[str, Any]
    match_kind: str
    candidate_references: tuple[str, ...]
    integrity_blockers: tuple[str, ...]
    semantic_comparisons: tuple[Mapping[str, Any], ...]
    final_state: str


@dataclass(frozen=True)
class ReconciliationResult:
    rule_id: str
    source_universe: str
    source_identity: Mapping[str, Any]
    target_universe: str
    target_identities: tuple[Mapping[str, Any], ...]
    expectation_state: str
    observed_counterpart_count: int
    expected_cardinality: str
    relationship_status: str
    matched_join_key: Mapping[str, Any]
    match_kind: str
    semantic_checks: tuple[SemanticCheck, ...]
    integrity_blockers: tuple[str, ...]
    historical_limitations: tuple[str, ...]
    source_evidence_references: tuple[str, ...]
    counterpart_evidence_references: tuple[str, ...]
    provenance: tuple[str, ...]
    root_finding_ids: tuple[str, ...]
    dependent_impact: bool
    trace: ReconciliationTrace
    fingerprint: str
    schema: int = RECONCILIATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _native(asdict(self))


@dataclass(frozen=True)
class ReconciliationReport:
    results: tuple[ReconciliationResult, ...]
    counts: Mapping[str, int]
    rule_counts: Mapping[str, Mapping[str, int]]
    manifests: tuple[str, ...]
    schema: int = RECONCILIATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "wave1_authority": "research_engine.v10.universes.assurance",
            "wave2_authority": "research_engine.v10.universes.evidence_integrity",
            "active_universes": [item.value for item in expected_active_universes()],
            "results": [result.to_dict() for result in self.results],
            "counts": dict(self.counts),
            "rule_counts": {key: dict(value) for key, value in self.rule_counts.items()},
            "manifests": list(self.manifests),
        }


@dataclass(frozen=True)
class ReconciliationInput:
    batches: tuple[EvidenceBatch, ...]
    integrity_findings: tuple[IntegrityFinding, ...] = ()
    manifests: tuple[EvidenceManifest, ...] = ()
    reconstructions: tuple[ReconstructedArtifact, ...] = ()


@dataclass(frozen=True)
class _RecordRef:
    universe: Universe
    dataset: str
    record: Mapping[str, Any]
    provenance: str
    reference: str


def _native(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_native(item) for item in value]
    return value


def _canonical(value: Any) -> str:
    return json.dumps(_native(value), sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _value(record: Mapping[str, Any], name: str) -> Any:
    if name in record:
        return record.get(name)
    aliases = {
        "trade_id": (("identity", "trade_id"), ("identity", "shadow_trade_id")),
        "shadow_trade_id": (("identity", "shadow_trade_id"), ("identity", "trade_id")),
        "entity_id": (("identity", "entity_id"),),
        "correlation_id": (("identity", "correlation_id"),),
        "canonical_opportunity_id": (("identity", "canonical_opportunity_id"),),
        "symbol": (("identity", "symbol"),),
        "evaluated_horizon": (("identity", "evaluated_horizon"),),
        "entry_time": (("timestamps", "entry_timestamp_broker"),),
        "exit_time": (("timestamps", "exit_timestamp_broker"),),
        "r_multiple": (("outcome", "r_multiple_realised"), ("simulated_outcome", "pnl_r_multiple")),
        "exit_reason": (("exit", "exit_reason"), ("simulated_outcome", "exit_reason")),
        "exit_timestamp": (("simulated_outcome", "exit_timestamp"),),
        "timestamp_decision_utc": (("decision_snapshot", "timestamp_decision_utc"),),
        "direction": (("decision_snapshot", "direction"),),
    }
    for path in aliases.get(name, ()):
        current: Any = record
        for part in path:
            current = current.get(part) if isinstance(current, Mapping) else None
        if current not in (None, ""):
            return current
    return None


def _time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            number = float(value)
            if number > 100_000_000_000:
                number /= 1000.0
            return datetime.fromtimestamp(number, timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def _counterpart(source: Universe, target: Universe):
    direct = next((item for item in get_universe_contract(source).counterparts if item.universe is target), None)
    if direct is not None:
        return direct
    return next((item for item in get_universe_contract(target).counterparts if item.universe is source), None)


def _rule(
    rule_id: str,
    source: Universe,
    target: Universe,
    *,
    condition: str,
    expectation: str,
    cardinality: Cardinality,
    timing: str,
    absence: str,
    pending: str,
    semantics: tuple[str, ...],
    fallback: tuple[str, ...] = (),
    source_dataset: str = "",
) -> ReconciliationRule:
    counterpart = _counterpart(source, target)
    if counterpart is None and rule_id != "SHADOW_LIFECYCLE_OUTCOME":
        raise ValueError(f"Wave 1 has no counterpart authority for {source.value}->{target.value}")
    if rule_id == "SHADOW_LIFECYCLE_OUTCOME":
        contract = get_universe_contract(Universe.SHADOW_OUTCOME)
        if not any(dep.reference == "shadow_runtime" for dep in contract.dependencies):
            raise ValueError("Wave 1 does not declare shadow_runtime dependency")
        join_fields = ("shadow_trade_id", "canonical_opportunity_id", "evaluated_horizon")
    else:
        join_fields = tuple(counterpart.join_fields)
    source_contract = get_universe_contract(source)
    target_contract = get_universe_contract(target)
    contract_limitations = tuple(dict.fromkeys((*source_contract.limitations, *target_contract.limitations)))
    return ReconciliationRule(
        rule_id, source, target, condition, expectation, join_fields, fallback,
        cardinality, timing, absence, pending,
        ("valid canonical identity", "no blocking Wave 2 integrity finding"),
        semantics, True, source_dataset, "; ".join(contract_limitations),
    )


def get_reconciliation_rules() -> tuple[ReconciliationRule, ...]:
    """Return rules whose topology and primary joins are validated by Wave 1."""
    return (
        _rule(
            "DECISION_EXECUTION", Universe.DECISION, Universe.EXECUTION,
            condition="execution state proves a completed validated live trade",
            expectation="completed Execution evidence", cardinality=Cardinality.ZERO_OR_ONE,
            timing="decision event must not follow execution entry",
            absence="NO_TRADE, local block, or broker rejection has no completed Execution row",
            pending="dispatched, accepted, or open execution has not reached trade_truth completion",
            semantics=("symbol", "direction", "account_id", "broker"),
        ),
        _rule(
            "EXECUTION_OUTCOME", Universe.EXECUTION, Universe.OUTCOME,
            condition="every Execution-universe row is a completed valid trade",
            expectation="exact deterministic Outcome projection", cardinality=Cardinality.EXACTLY_ONE,
            timing="Outcome retains Execution entry/exit event times",
            absence="only legitimate when Execution itself is absent",
            pending="none for a completed Execution row",
            semantics=("trade_id", "symbol", "direction", "account_id", "broker", "entry_time", "exit_time", "entry_price", "exit_price", "r_multiple", "net_realised_pnl"),
        ),
        _rule(
            "DECISION_RISK", Universe.DECISION, Universe.RISK,
            condition="decision path reached the risk stage",
            expectation="one risk evaluation for the same entity", cardinality=Cardinality.ZERO_OR_ONE,
            timing="risk evaluation belongs to the same decision event/cycle",
            absence="path terminated before risk or risk was not required",
            pending="decision path explicitly records risk as pending",
            semantics=("symbol", "risk_approved"),
        ),
        _rule(
            "DECISION_STRATEGY", Universe.DECISION, Universe.STRATEGY,
            condition="decision path reached strategy evaluation",
            expectation="primary strategy observation or permitted decision-trace fallback",
            cardinality=Cardinality.ZERO_OR_ONE,
            timing="strategy observation must belong to the same entity/cycle",
            absence="path terminated before strategy or no eligible strategy was evaluated",
            pending="strategy evaluation explicitly remains pending",
            semantics=("symbol", "strategy_family", "direction"),
            fallback=("symbol", "cycle_id"),
        ),
        _rule(
            "DECISION_MARKET", Universe.DECISION, Universe.MARKET,
            condition="market context was captured for the decision or qualifying cycle",
            expectation="decision-linked context or explicit standalone symbol+cycle context",
            cardinality=Cardinality.ONE_OR_MANY,
            timing="decision-linked context cannot post-date its decision event",
            absence="no context was captured or pipeline ended before capture",
            pending="market collection explicitly remains pending",
            semantics=("symbol", "cycle_id", "regime"),
            fallback=("symbol", "cycle_id"),
        ),
        _rule(
            "SHADOW_LIFECYCLE_OUTCOME", Universe.SHADOW_OUTCOME, Universe.SHADOW_OUTCOME,
            condition="canonical shadow_runtime lifecycle has one valid OPEN and one valid CLOSE",
            expectation="one terminal Shadow Outcome", cardinality=Cardinality.EXACTLY_ONE,
            timing="OPEN precedes CLOSE and outcome exit time agrees with CLOSE",
            absence="no valid OPEN means no eligible lifecycle",
            pending="OPEN exists without CLOSE",
            semantics=("shadow_trade_id", "canonical_opportunity_id", "evaluated_horizon", "symbol", "direction", "r_multiple", "exit_reason"),
            source_dataset="shadow_runtime",
        ),
    )


def _identity(record: Mapping[str, Any], universe: Universe, dataset: str = "") -> dict[str, Any]:
    if universe is Universe.SHADOW_OUTCOME and dataset == "shadow_runtime":
        return {
            "shadow_trade_id": record.get("shadow_trade_id"),
            "canonical_opportunity_id": record.get("canonical_opportunity_id"),
            "evaluated_horizon": record.get("horizon"),
        }
    contract = get_universe_contract(universe)
    return {name: _value(record, name) for name in contract.identity.primary_fields}


def _record_ref(universe: Universe, dataset: str, record: Mapping[str, Any], provenance: str) -> _RecordRef:
    identity = _identity(record, universe, dataset)
    material = {"universe": universe.value, "dataset": dataset, "identity": identity, "record": record, "provenance": provenance}
    return _RecordRef(universe, dataset, record, provenance, f"EV-{_hash(material)[:20]}")


_BLOCKING_STATUSES = {
    IntegrityStatus.IDENTITY_CONFLICT.value,
    IntegrityStatus.BROKEN_CONTINUITY.value,
    IntegrityStatus.ORDERING_VIOLATION.value,
    IntegrityStatus.SOURCE_MODIFIED.value,
}
_INSUFFICIENT_STATUSES = {
    IntegrityStatus.MISSING_EXPECTED.value,
    IntegrityStatus.UNRECONSTRUCTABLE.value,
    IntegrityStatus.MANIFEST_SOURCE_MISSING.value,
}


def _finding_applies(finding: IntegrityFinding, ref: _RecordRef) -> bool:
    if finding.universe != ref.universe.value:
        return False
    if finding.dataset and finding.dataset != ref.dataset:
        return False
    if not finding.identity:
        return True
    compared = 0
    for key, expected in finding.identity.items():
        actual = _value(ref.record, key)
        if actual in (None, "") and ref.dataset == "shadow_runtime":
            actual = ref.record.get("horizon") if key == "evaluated_horizon" else ref.record.get(key)
        if actual not in (None, "") and expected not in (None, ""):
            compared += 1
            if str(actual) != str(expected):
                return False
    return compared > 0


def _integrity_state(ref: _RecordRef, findings: Sequence[IntegrityFinding]) -> tuple[ExpectationState | None, tuple[IntegrityFinding, ...]]:
    relevant = tuple(sorted((item for item in findings if _finding_applies(item, ref)), key=lambda item: item.fingerprint))
    if any(item.status in _BLOCKING_STATUSES for item in relevant):
        return ExpectationState.BLOCKED_BY_INTEGRITY, relevant
    if any(item.status == IntegrityStatus.HISTORICAL_LIMITATION.value for item in relevant):
        return ExpectationState.HISTORICAL_LIMITATION, relevant
    if any(item.status in _INSUFFICIENT_STATUSES or item.reconstruction_status == ReconstructionState.UNRECONSTRUCTABLE.value for item in relevant):
        return ExpectationState.UNKNOWN, relevant
    if any(item.status in {IntegrityStatus.AMBIGUOUS.value, IntegrityStatus.STALE.value} for item in relevant):
        return ExpectationState.UNKNOWN, relevant
    if ref.provenance == "RECONSTRUCTED_PARTIAL":
        return ExpectationState.UNKNOWN, relevant
    return None, relevant


def _target_scope_findings(
    rule: ReconciliationRule,
    source: _RecordRef,
    findings: Sequence[IntegrityFinding],
) -> tuple[IntegrityFinding, ...]:
    """Wave 2 target-scope state used when no counterpart record survives."""
    relevant: list[IntegrityFinding] = []
    for finding in findings:
        if finding.universe != rule.target_universe.value:
            continue
        if not finding.identity:
            relevant.append(finding)
            continue
        overlap = 0
        disagrees = False
        for name, expected in finding.identity.items():
            actual = _value(source.record, name)
            if actual not in (None, "") and expected not in (None, ""):
                overlap += 1
                if str(actual) != str(expected):
                    disagrees = True
                    break
        if overlap and not disagrees:
            relevant.append(finding)
    return tuple(sorted(relevant, key=lambda item: item.fingerprint))


def _stage_index(value: Any) -> int | None:
    text = str(value or "").upper()
    ordered = ("OPPORTUNITY", "STRATEGY", "ENTRY", "RISK", "EXECUTION", "COMPLETED")
    for index, name in enumerate(ordered):
        if name in text:
            return index
    return None


def _expectation(rule: ReconciliationRule, source: _RecordRef, findings: Sequence[IntegrityFinding]) -> ExpectationDecision:
    integrity_state, relevant = _integrity_state(source, findings)
    identity = _identity(source.record, source.universe, source.dataset)
    root_ids = tuple(item.finding_id for item in relevant)
    if integrity_state is ExpectationState.BLOCKED_BY_INTEGRITY:
        state, reason = integrity_state, "source identity/lifecycle/order is blocked by Wave 2 integrity"
    elif integrity_state is ExpectationState.HISTORICAL_LIMITATION:
        state, reason = integrity_state, "Wave 2 marks the source scope as historically limited"
    elif integrity_state is ExpectationState.UNKNOWN:
        state, reason = integrity_state, "Wave 2 cannot prove source evidence integrity/completeness"
    elif rule.rule_id == "EXECUTION_OUTCOME":
        state, reason = ExpectationState.REQUIRED, "Execution universe contains only completed validated trades"
    elif rule.rule_id == "DECISION_EXECUTION":
        action = str(source.record.get("action", "")).upper()
        execution_state = str(source.record.get("execution_state") or source.record.get("dispatch_status") or "").upper()
        if action != "EXECUTE":
            state, reason = ExpectationState.NOT_REQUIRED, "decision action does not authorize live execution"
        elif execution_state in {"NOT_ATTEMPTED", "BLOCKED", "BROKER_REJECTED", "REJECTED", "FAILED"}:
            state, reason = ExpectationState.NOT_REQUIRED, f"execution state {execution_state} cannot produce a completed trade"
        elif execution_state in {"DISPATCHED", "ACCEPTED", "OPEN", "PENDING", "SUBMITTED"}:
            state, reason = ExpectationState.PENDING, f"execution state {execution_state} is non-terminal"
        elif execution_state in {"COMPLETED", "CLOSED", "TRADE_TRUTH_RECORDED"}:
            state, reason = ExpectationState.REQUIRED, f"execution state {execution_state} proves terminal live evidence"
        else:
            state, reason = ExpectationState.UNKNOWN, "EXECUTE label alone does not prove dispatch or completion"
    elif rule.rule_id in {"DECISION_RISK", "DECISION_STRATEGY"}:
        name = "RISK" if rule.rule_id.endswith("RISK") else "STRATEGY"
        pending = str(source.record.get(f"{name.lower()}_state", "")).upper() == "PENDING"
        reached = {str(item).upper() for item in source.record.get("stages_reached", ())}
        stage = _stage_index(source.record.get("terminal_stage"))
        threshold = _stage_index(name)
        owned_value = source.record.get("risk_approved") if name == "RISK" else source.record.get("strategy_family")
        if pending:
            state, reason = ExpectationState.PENDING, f"{name.lower()} evaluation is explicitly pending"
        elif name in reached or owned_value not in (None, "") or (stage is not None and threshold is not None and stage >= threshold):
            state, reason = ExpectationState.REQUIRED, f"decision evidence proves {name.lower()} stage was reached"
        elif stage is not None and threshold is not None and stage < threshold:
            state, reason = ExpectationState.NOT_REQUIRED, f"decision terminated before {name.lower()} stage"
        else:
            state, reason = ExpectationState.UNKNOWN, f"decision does not prove whether {name.lower()} stage was reached"
    elif rule.rule_id == "DECISION_MARKET":
        market_state = str(source.record.get("market_state_status", "")).upper()
        if market_state == "PENDING":
            state, reason = ExpectationState.PENDING, "market context collection is pending"
        elif market_state in {"NOT_CAPTURED", "NOT_APPLICABLE"}:
            state, reason = ExpectationState.NOT_REQUIRED, f"market context is {market_state.lower()}"
        elif source.record.get("market_context_captured") is True or source.record.get("regime") not in (None, ""):
            state, reason = ExpectationState.REQUIRED, "decision proves market context was captured"
        else:
            state, reason = ExpectationState.UNKNOWN, "decision does not prove context capture"
    elif rule.rule_id == "SHADOW_LIFECYCLE_OUTCOME":
        lifecycle = source.record
        state_name = str(lifecycle.get("lifecycle_state", "")).upper()
        if state_name == "INTEGRITY_BLOCKED":
            state, reason = ExpectationState.BLOCKED_BY_INTEGRITY, "Wave 2 lifecycle aggregation is integrity-blocked"
        elif lifecycle.get("open_count") == 1 and lifecycle.get("close_count") == 1:
            state, reason = ExpectationState.REQUIRED, "one OPEN and one CLOSE prove a terminal lifecycle"
        elif lifecycle.get("open_count") == 1 and lifecycle.get("close_count") == 0:
            state, reason = ExpectationState.PENDING, "OPEN-only lifecycle remains active"
        elif lifecycle.get("open_count") == 0:
            state, reason = ExpectationState.NOT_REQUIRED, "no valid OPEN exists"
        else:
            state, reason = ExpectationState.BLOCKED_BY_INTEGRITY, "lifecycle cardinality is invalid"
    else:
        state, reason = ExpectationState.UNKNOWN, "no condition evaluator"
    fact_fields = {
        "DECISION_EXECUTION": ("action", "execution_state", "dispatch_status", "_execution_condition_source"),
        "EXECUTION_OUTCOME": ("trade_id", "exit_time", "r_multiple"),
        "DECISION_RISK": ("terminal_stage", "stages_reached", "risk_approved", "risk_state"),
        "DECISION_STRATEGY": ("terminal_stage", "stages_reached", "strategy_family", "strategy_state"),
        "DECISION_MARKET": ("market_context_captured", "market_state_status", "regime"),
        "SHADOW_LIFECYCLE_OUTCOME": ("open_count", "close_count", "lifecycle_state"),
    }[rule.rule_id]
    condition_facts = tuple(
        f"{name}={_canonical(source.record.get(name))}"
        for name in fact_fields if source.record.get(name) not in (None, "", (), [])
    )
    return ExpectationDecision(
        source.universe.value, identity, rule.target_universe.value, state.value,
        rule.rule_id, tuple(sorted(set((*root_ids, *condition_facts)))),
        rule.integrity_prerequisites, reason,
    )


def _lifecycle_refs(batch: EvidenceBatch) -> tuple[_RecordRef, ...]:
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for event in batch.records:
        if event.get("event_type") == "PLAN":
            continue
        key = (
            str(event.get("shadow_trade_id", "") or ""),
            str(event.get("canonical_opportunity_id", "") or ""),
            str(event.get("horizon", "") or ""),
        )
        groups[key].append(event)
    refs = []
    for key, events in sorted(groups.items()):
        opens = [item for item in events if item.get("event_type") == "OPEN"]
        closes = [item for item in events if item.get("event_type") == "CLOSE"]
        record: dict[str, Any] = {
            "shadow_trade_id": key[0], "canonical_opportunity_id": key[1],
            "evaluated_horizon": key[2], "horizon": key[2],
            "open_count": len(opens), "close_count": len(closes),
            "lifecycle_state": "INTEGRITY_BLOCKED" if len(opens) > 1 or len(closes) > 1 or (closes and not opens) else "VALID",
            "_event_references": tuple(sorted(_hash(item) for item in events)),
        }
        if opens:
            opened = opens[0]
            record.update({
                "symbol": opened.get("symbol"),
                "plan_id": opened.get("plan_id"),
                "direction": (opened.get("construction") or {}).get("direction"),
                "open_timestamp": opened.get("entry_market_time_utc_epoch_s") or opened.get("event_market_time_utc_epoch_s"),
            })
        if closes:
            closed = closes[0]
            record.update({
                "close_timestamp": closed.get("exit_market_time_utc_epoch_s") or closed.get("event_market_time_utc_epoch_s"),
                "r_multiple": (closed.get("outcome") or {}).get("pnl_r_multiple"),
                "exit_reason": closed.get("exit_reason"),
            })
        refs.append(_record_ref(Universe.SHADOW_OUTCOME, "shadow_runtime", record, "OBSERVED_ORIGINAL"))
    return tuple(refs)


def _records(inputs: ReconciliationInput) -> tuple[_RecordRef, ...]:
    refs: list[_RecordRef] = []
    for batch in inputs.batches:
        if batch.universe is Universe.SHADOW_OUTCOME and batch.dataset == "shadow_runtime":
            refs.extend(_lifecycle_refs(batch))
        else:
            refs.extend(_record_ref(batch.universe, batch.dataset, record, "OBSERVED_ORIGINAL") for record in batch.records)
    for artifact in inputs.reconstructions:
        try:
            universe = Universe(artifact.target_universe)
        except ValueError:
            continue
        provenance = "RECONSTRUCTED_EXACT" if artifact.exact and artifact.source_complete else "RECONSTRUCTED_PARTIAL"
        refs.append(_record_ref(universe, artifact.target_dataset, artifact.artifact, provenance))
    refs.sort(key=lambda item: item.reference)
    return tuple(refs)


def _join(
    rule: ReconciliationRule,
    source: _RecordRef,
    targets: Sequence[_RecordRef],
    target_index: Mapping[tuple[str, str], Sequence[_RecordRef]] | None = None,
) -> tuple[tuple[_RecordRef, ...], Mapping[str, Any], MatchKind, tuple[_RecordRef, ...]]:
    primary_fields = rule.join_fields
    # Shadow outcome normalisation names horizon as evaluated_horizon.
    primary = {name: (_value(source.record, name) if name != "evaluated_horizon" else (_value(source.record, name) or source.record.get("horizon"))) for name in primary_fields}
    usable = {key: value for key, value in primary.items() if value not in (None, "")}
    candidates: list[_RecordRef] = []
    identity_mismatches: list[_RecordRef] = []
    if usable:
        anchor = next(iter(usable))
        indexed_targets = (
            target_index.get((anchor, str(usable[anchor])), ())
            if target_index is not None
            else targets
        )
        for target in indexed_targets:
            target_value = _value(target.record, anchor)
            if target_value in (None, "") and anchor == "evaluated_horizon":
                target_value = target.record.get("horizon")
            if str(target_value) != str(usable[anchor]):
                continue
            disagreements = []
            for name, expected in usable.items():
                actual = _value(target.record, name)
                if actual in (None, "") and name == "evaluated_horizon":
                    actual = target.record.get("horizon")
                if actual not in (None, "") and str(actual) != str(expected):
                    disagreements.append(name)
            if disagreements:
                identity_mismatches.append(target)
            else:
                candidates.append(target)
    if candidates:
        if rule.rule_id == "DECISION_STRATEGY":
            primary = tuple(item for item in candidates if item.record.get("source") == "strategy_observations")
            if primary:
                superseded = tuple(item for item in candidates if item not in primary)
                return primary, usable, MatchKind.DETERMINISTIC, tuple((*identity_mismatches, *superseded))
            fallback_candidates = tuple(item for item in candidates if item.record.get("source") == "decision_trace")
            if fallback_candidates:
                return fallback_candidates, usable, MatchKind.CONTRACT_FALLBACK, tuple(identity_mismatches)
        return tuple(candidates), usable, MatchKind.DETERMINISTIC, tuple(identity_mismatches)
    fallback = {name: _value(source.record, name) for name in rule.fallback_join_fields}
    if rule.fallback_join_fields and all(value not in (None, "") for value in fallback.values()):
        fallback_anchor = next(iter(fallback))
        fallback_targets = (
            target_index.get((fallback_anchor, str(fallback[fallback_anchor])), ())
            if target_index is not None
            else targets
        )
        fallback_matches = tuple(
            target for target in fallback_targets
            if all(str(_value(target.record, name)) == str(value) for name, value in fallback.items())
        )
        if fallback_matches:
            return fallback_matches, fallback, MatchKind.CONTRACT_FALLBACK, tuple(identity_mismatches)
    return (), usable or fallback, MatchKind.NONE, tuple(identity_mismatches)


def _normalized(value: Any) -> Any:
    if isinstance(value, str):
        normalized = value.strip().upper().replace("MAX_BARS_TIMEOUT", "TIMEOUT")
        return {"LONG": "BUY", "SHORT": "SELL"}.get(normalized, normalized)
    return value


def _check(kind: ComparisonKind, sf: str, tf: str, source: Any, target: Any, reason: str = "") -> SemanticCheck:
    if kind is ComparisonKind.NORMALIZED:
        agrees = _normalized(source) == _normalized(target)
    elif kind is ComparisonKind.DERIVED:
        try:
            agrees = abs(float(source) - float(target)) <= 1e-12
        except (TypeError, ValueError):
            agrees = False
    else:
        agrees = source == target
    return SemanticCheck(kind.value, sf, tf, source, target, agrees, reason)


def _semantic_checks(rule: ReconciliationRule, source: _RecordRef, target: _RecordRef) -> tuple[SemanticCheck, ...]:
    checks: list[SemanticCheck] = []
    source_record, target_record = source.record, target.record
    if rule.rule_id == "EXECUTION_OUTCOME":
        for name in rule.semantic_fields:
            left, right = _value(source_record, name), _value(target_record, name)
            if left in (None, "") or right in (None, ""):
                continue
            kind = ComparisonKind.DERIVED if name in {"r_multiple", "net_realised_pnl", "entry_price", "exit_price"} else ComparisonKind.NORMALIZED if name in {"symbol", "direction"} else ComparisonKind.EXACT
            checks.append(_check(kind, name, name, left, right, "Outcome is an identity-preserving Execution projection"))
    elif rule.rule_id == "DECISION_RISK":
        if _value(source_record, "symbol") not in (None, "") and _value(target_record, "symbol") not in (None, ""):
            checks.append(_check(ComparisonKind.NORMALIZED, "symbol", "symbol", _value(source_record, "symbol"), _value(target_record, "symbol")))
        approved = source_record.get("risk_approved")
        control = target_record.get("risk_control_result")
        if approved is not None and control not in (None, ""):
            checks.append(_check(ComparisonKind.DERIVED, "risk_approved", "risk_control_result", bool(approved), str(control).upper() == "APPROVED", "boolean approval maps to APPROVED/BLOCKED"))
        if str(source_record.get("action", "")).upper() == "EXECUTE" and str(control).upper() == "BLOCKED":
            checks.append(SemanticCheck(ComparisonKind.DERIVED.value, "action", "risk_control_result", "EXECUTE", control, False, "an EXECUTE decision cannot carry a blocking risk evaluation"))
    elif rule.rule_id == "DECISION_STRATEGY":
        pairs = (("symbol", "symbol", ComparisonKind.NORMALIZED), ("strategy_family", "family", ComparisonKind.NORMALIZED), ("strategy_direction", "direction", ComparisonKind.NORMALIZED))
        for sf, tf, kind in pairs:
            left, right = _value(source_record, sf), _value(target_record, tf)
            if left not in (None, "") and right not in (None, ""):
                checks.append(_check(kind, sf, tf, left, right))
    elif rule.rule_id == "DECISION_MARKET":
        for name in ("symbol", "cycle_id", "regime"):
            left, right = _value(source_record, name), _value(target_record, name)
            if left not in (None, "") and right not in (None, ""):
                checks.append(_check(ComparisonKind.NORMALIZED if name != "cycle_id" else ComparisonKind.EXACT, name, name, left, right))
    elif rule.rule_id == "DECISION_EXECUTION":
        pairs = (
            ("symbol", "symbol"), ("strategy_direction", "direction"),
            ("account_id", "account_id"), ("broker", "broker"),
        )
        for sf, tf in pairs:
            left, right = _value(source_record, sf), _value(target_record, tf)
            if left not in (None, "") and right not in (None, ""):
                checks.append(_check(ComparisonKind.NORMALIZED, sf, tf, left, right))
    elif rule.rule_id == "SHADOW_LIFECYCLE_OUTCOME":
        pairs = (("symbol", "symbol", ComparisonKind.NORMALIZED), ("direction", "direction", ComparisonKind.NORMALIZED), ("r_multiple", "r_multiple", ComparisonKind.DERIVED), ("exit_reason", "exit_reason", ComparisonKind.NORMALIZED))
        for sf, tf, kind in pairs:
            left, right = _value(source_record, sf), _value(target_record, tf)
            if left not in (None, "") and right not in (None, ""):
                checks.append(_check(kind, sf, tf, left, right))
    return tuple(checks)


def _timing_mismatch(rule: ReconciliationRule, source: _RecordRef, target: _RecordRef) -> bool:
    if rule.rule_id == "DECISION_EXECUTION":
        before, after = _time(_value(source.record, "timestamp_utc")), _time(_value(target.record, "entry_time"))
    elif rule.rule_id == "EXECUTION_OUTCOME":
        # Exact timestamp equality is already a semantic projection check.
        return False
    elif rule.rule_id in {"DECISION_RISK", "DECISION_STRATEGY"}:
        before, after = _time(_value(target.record, "timestamp_utc")), _time(_value(source.record, "timestamp_utc"))
    elif rule.rule_id == "DECISION_MARKET" and target.record.get("source") == "decision_trace":
        before, after = _time(_value(target.record, "timestamp_utc")), _time(_value(source.record, "timestamp_utc"))
    elif rule.rule_id == "SHADOW_LIFECYCLE_OUTCOME":
        before, after = _time(source.record.get("open_timestamp")), _time(source.record.get("close_timestamp"))
    else:
        return False
    return before is not None and after is not None and before > after


_STATUS_PRECEDENCE = (
    RelationshipStatus.BLOCKED_BY_INTEGRITY,
    RelationshipStatus.HISTORICAL_LIMITATION,
    RelationshipStatus.INSUFFICIENT_EVIDENCE,
    RelationshipStatus.AMBIGUOUS,
    RelationshipStatus.NOT_REQUIRED,
    RelationshipStatus.PENDING,
    RelationshipStatus.IDENTITY_MISMATCH,
    RelationshipStatus.MISSING_COUNTERPART,
    RelationshipStatus.CARDINALITY_CONFLICT,
    RelationshipStatus.TIMING_MISMATCH,
    RelationshipStatus.SEMANTIC_MISMATCH,
    RelationshipStatus.RECONCILED,
)


def _result(
    rule: ReconciliationRule,
    source: _RecordRef,
    expectation: ExpectationDecision,
    targets: Sequence[_RecordRef],
    join_key: Mapping[str, Any],
    match_kind: MatchKind,
    status: RelationshipStatus,
    checks: Sequence[SemanticCheck] = (),
    blockers: Sequence[IntegrityFinding] = (),
    historical: Sequence[str] = (),
    mismatches: Sequence[_RecordRef] = (),
) -> ReconciliationResult:
    candidate_refs = tuple(sorted({item.reference for item in (*targets, *mismatches)}))
    target_ids = tuple(_identity(item.record, item.universe, item.dataset) for item in sorted(targets, key=lambda item: item.reference))
    blocker_ids = tuple(sorted(item.finding_id for item in blockers))
    provenance = tuple(sorted({source.provenance, *(item.provenance for item in targets)}))
    trace = ReconciliationTrace(
        source.reference, f"{rule.source_universe.value}->{rule.target_universe.value}",
        expectation.reason, expectation.supporting_evidence, expectation.state,
        _native(join_key), match_kind.value,
        candidate_refs, blocker_ids, tuple(_native(asdict(item)) for item in checks), status.value,
    )
    stable = {
        "rule_id": rule.rule_id, "source_reference": source.reference,
        "target_references": tuple(sorted(item.reference for item in targets)),
        "expectation": expectation.state, "status": status.value,
        "join_key": _native(join_key), "checks": [_native(asdict(item)) for item in checks],
        "blockers": blocker_ids, "historical": tuple(sorted(historical)),
    }
    fingerprint = _hash(stable)
    expected_cardinality = rule.cardinality.value
    if expectation.state == ExpectationState.REQUIRED.value and rule.cardinality is Cardinality.ZERO_OR_ONE:
        expected_cardinality = Cardinality.EXACTLY_ONE.value
    elif expectation.state == ExpectationState.NOT_REQUIRED.value:
        expected_cardinality = "0"
    elif expectation.state == ExpectationState.PENDING.value:
        expected_cardinality = "0..1"
    return ReconciliationResult(
        rule.rule_id, source.universe.value, _identity(source.record, source.universe, source.dataset),
        rule.target_universe.value, target_ids, expectation.state, len(targets),
        expected_cardinality, status.value, _native(join_key), match_kind.value,
        tuple(checks), blocker_ids, tuple(sorted(historical)),
        (source.reference,), tuple(sorted(item.reference for item in targets)), provenance,
        blocker_ids, bool(blocker_ids), trace, fingerprint,
    )


def _status_for_expectation(
    expectation: ExpectationDecision,
    blockers: Sequence[IntegrityFinding],
    provenance: str = "OBSERVED_ORIGINAL",
) -> RelationshipStatus | None:
    state = ExpectationState(expectation.state)
    if state is ExpectationState.BLOCKED_BY_INTEGRITY:
        return RelationshipStatus.BLOCKED_BY_INTEGRITY
    if state is ExpectationState.HISTORICAL_LIMITATION:
        return RelationshipStatus.HISTORICAL_LIMITATION
    if state is ExpectationState.UNKNOWN:
        if provenance == "RECONSTRUCTED_PARTIAL" or any(item.status in _INSUFFICIENT_STATUSES or item.reconstruction_status == ReconstructionState.UNRECONSTRUCTABLE.value for item in blockers):
            return RelationshipStatus.INSUFFICIENT_EVIDENCE
        return RelationshipStatus.AMBIGUOUS
    if state is ExpectationState.NOT_REQUIRED:
        return RelationshipStatus.NOT_REQUIRED
    if state is ExpectationState.PENDING:
        return RelationshipStatus.PENDING
    return None


class ReconciliationEngine:
    """Deterministic batch reconciler over Wave 1 contracts and Wave 2 state."""

    def __init__(self, inputs: ReconciliationInput):
        self.inputs = inputs
        self.rules = get_reconciliation_rules()
        self._refs = _records(inputs)
        self._findings_by_scope: dict[tuple[str, str], list[IntegrityFinding]] = defaultdict(list)
        self._findings_by_identity_value: dict[
            tuple[str, str, str], list[IntegrityFinding]
        ] = defaultdict(list)
        self._finding_identity_names: dict[str, set[str]] = defaultdict(set)
        for finding in inputs.integrity_findings:
            self._findings_by_scope[(finding.universe, finding.dataset)].append(finding)
            for name, value in finding.identity.items():
                self._finding_identity_names[finding.universe].add(name)
                if value not in (None, ""):
                    self._findings_by_identity_value[
                        (finding.universe, name, str(value))
                    ].append(finding)

    def _findings_for_ref(self, ref: _RecordRef) -> tuple[IntegrityFinding, ...]:
        """Return only findings that can apply to this record's scope."""
        scoped = self._findings_by_scope.get(
            (ref.universe.value, ref.dataset), ()
        )
        candidates: dict[str, IntegrityFinding] = {
            item.finding_id: item for item in scoped if not item.identity
        }
        for name in self._finding_identity_names.get(ref.universe.value, ()):
            value = _value(ref.record, name)
            if value in (None, "") and ref.dataset == "shadow_runtime":
                value = (
                    ref.record.get("horizon")
                    if name == "evaluated_horizon"
                    else ref.record.get(name)
                )
            if value in (None, ""):
                continue
            for item in self._findings_by_identity_value.get(
                (ref.universe.value, name, str(value)), ()
            ):
                if item.dataset == ref.dataset:
                    candidates[item.finding_id] = item
        return tuple(
            item for item in candidates.values() if _finding_applies(item, ref)
        )

    def _target_findings(
        self, rule: ReconciliationRule, source: _RecordRef
    ) -> tuple[IntegrityFinding, ...]:
        """Prefilter target findings without changing overlap semantics."""
        candidates: dict[str, IntegrityFinding] = {
            item.finding_id: item
            for (universe, _dataset), findings in self._findings_by_scope.items()
            if universe == rule.target_universe.value
            for item in findings
            if not item.identity
        }
        for name in self._finding_identity_names.get(
            rule.target_universe.value, ()
        ):
            value = _value(source.record, name)
            if value in (None, ""):
                continue
            for item in self._findings_by_identity_value.get(
                (rule.target_universe.value, name, str(value)), ()
            ):
                candidates[item.finding_id] = item
        return tuple(candidates.values())

    @staticmethod
    def _record_index(
        records: Sequence[_RecordRef], fields: Sequence[str]
    ) -> dict[tuple[str, str], tuple[_RecordRef, ...]]:
        mutable: dict[tuple[str, str], list[_RecordRef]] = defaultdict(list)
        for record in records:
            for name in fields:
                value = _value(record.record, name)
                if value in (None, "") and name == "evaluated_horizon":
                    value = record.record.get("horizon")
                if value not in (None, ""):
                    mutable[(name, str(value))].append(record)
        return {key: tuple(value) for key, value in mutable.items()}

    def reconcile_rule(self, rule_id: str) -> tuple[ReconciliationResult, ...]:
        rule = next(item for item in self.rules if item.rule_id == rule_id)
        sources = [item for item in self._refs if item.universe is rule.source_universe]
        if rule.rule_id == "EXECUTION_OUTCOME":
            sources = [item for item in sources if item.dataset != "execution_results"]
        if rule.source_dataset:
            sources = [item for item in sources if item.dataset == rule.source_dataset]
        targets = [item for item in self._refs if item.universe is rule.target_universe]
        execution_attempts: list[_RecordRef] = []
        if rule.rule_id == "DECISION_EXECUTION":
            execution_attempts = [item for item in targets if item.dataset == "execution_results"]
            targets = [item for item in targets if item.dataset != "execution_results"]
        if rule.rule_id == "SHADOW_LIFECYCLE_OUTCOME":
            targets = [item for item in targets if item.dataset != "shadow_runtime"]
        target_index = self._record_index(
            targets, tuple(dict.fromkeys((*rule.join_fields, *rule.fallback_join_fields)))
        )
        attempt_index = self._record_index(
            execution_attempts, ("entity_id", "correlation_id")
        )
        results: list[ReconciliationResult] = []
        matched_target_refs: set[str] = set()
        for source in sources:
            if rule.rule_id == "DECISION_EXECUTION" and not (
                source.record.get("execution_state") or source.record.get("dispatch_status")
            ):
                record = dict(source.record)
                matching_attempts = list({
                    attempt.reference: attempt
                    for name in ("entity_id", "correlation_id")
                    if _value(source.record, name) not in (None, "")
                    for attempt in attempt_index.get(
                        (name, str(_value(source.record, name))), ()
                    )
                }.values())
                terminal_matches, _, _, _ = _join(
                    rule, source, targets, target_index
                )
                if terminal_matches:
                    record["execution_state"] = "COMPLETED"
                    record["_execution_condition_source"] = "trade_truth"
                elif matching_attempts and any(attempt.record.get("result_ok") is True for attempt in matching_attempts):
                    record["execution_state"] = "DISPATCHED"
                    record["_execution_condition_source"] = "execution_results:accepted"
                elif matching_attempts and all(attempt.record.get("result_ok") is False for attempt in matching_attempts):
                    record["execution_state"] = "BROKER_REJECTED"
                    record["_execution_condition_source"] = "execution_results:rejected"
                source = _RecordRef(source.universe, source.dataset, record, source.provenance, source.reference)
            source_findings = self._findings_for_ref(source)
            expectation = _expectation(rule, source, source_findings)
            _, blockers = _integrity_state(source, source_findings)
            early_status = _status_for_expectation(expectation, blockers, source.provenance)
            matches, key, kind, mismatches = _join(
                rule, source, targets, target_index
            )
            # Identity-conflicting or contract-superseded candidates are still
            # causally linked to this result; do not emit a second orphan for
            # the same underlying discrepancy.
            matched_target_refs.update(item.reference for item in mismatches)
            if early_status in {
                RelationshipStatus.BLOCKED_BY_INTEGRITY,
                RelationshipStatus.HISTORICAL_LIMITATION,
                RelationshipStatus.INSUFFICIENT_EVIDENCE,
                RelationshipStatus.AMBIGUOUS,
            }:
                # Candidate targets are linked as dependent impact so the same
                # upstream defect does not also create misleading orphans.
                matched_target_refs.update(item.reference for item in (*matches, *mismatches))
                results.append(_result(
                    rule, source, expectation, matches, key, kind, early_status,
                    blockers=blockers,
                    historical=tuple(item.finding_id for item in blockers if item.status == IntegrityStatus.HISTORICAL_LIMITATION.value),
                    mismatches=mismatches,
                ))
                continue
            target_states = [
                _integrity_state(target, self._findings_for_ref(target))
                for target in matches
            ]
            combined_blockers = tuple(sorted(
                {item.finding_id: item for item in (*blockers, *(finding for _, findings in target_states for finding in findings))}.values(),
                key=lambda item: item.fingerprint,
            ))
            target_state_values = {state for state, _ in target_states if state is not None}
            target_early: RelationshipStatus | None = None
            if ExpectationState.BLOCKED_BY_INTEGRITY in target_state_values:
                target_early = RelationshipStatus.BLOCKED_BY_INTEGRITY
            elif ExpectationState.HISTORICAL_LIMITATION in target_state_values:
                target_early = RelationshipStatus.HISTORICAL_LIMITATION
            elif ExpectationState.UNKNOWN in target_state_values:
                target_early = (
                    RelationshipStatus.INSUFFICIENT_EVIDENCE
                    if any(item.provenance == "RECONSTRUCTED_PARTIAL" for item in matches)
                    or any(item.status in _INSUFFICIENT_STATUSES or item.reconstruction_status == ReconstructionState.UNRECONSTRUCTABLE.value for item in combined_blockers)
                    else RelationshipStatus.AMBIGUOUS
                )
            if target_early is not None:
                matched_target_refs.update(item.reference for item in matches)
                results.append(_result(
                    rule, source, expectation, matches, key, kind, target_early,
                    blockers=combined_blockers,
                    historical=tuple(item.finding_id for item in combined_blockers if item.status == IntegrityStatus.HISTORICAL_LIMITATION.value),
                    mismatches=mismatches,
                ))
                continue
            if early_status is RelationshipStatus.NOT_REQUIRED:
                status = RelationshipStatus.EXTRA_COUNTERPART if matches else RelationshipStatus.NOT_REQUIRED
                results.append(_result(rule, source, expectation, matches, key, kind, status, blockers=combined_blockers, mismatches=mismatches))
                matched_target_refs.update(item.reference for item in matches)
                continue
            if early_status is RelationshipStatus.PENDING:
                status = RelationshipStatus.RECONCILED if matches else RelationshipStatus.PENDING
                results.append(_result(rule, source, expectation, matches, key, kind, status, blockers=combined_blockers, mismatches=mismatches))
                matched_target_refs.update(item.reference for item in matches)
                continue
            if not matches:
                target_scope = _target_scope_findings(
                    rule, source, self._target_findings(rule, source)
                )
                all_blockers = tuple(sorted(
                    {item.finding_id: item for item in (*combined_blockers, *target_scope)}.values(),
                    key=lambda item: item.fingerprint,
                ))
                statuses = {item.status for item in target_scope}
                if statuses & _BLOCKING_STATUSES:
                    status = RelationshipStatus.BLOCKED_BY_INTEGRITY
                elif IntegrityStatus.HISTORICAL_LIMITATION.value in statuses:
                    status = RelationshipStatus.HISTORICAL_LIMITATION
                elif statuses & _INSUFFICIENT_STATUSES or any(item.reconstruction_status == ReconstructionState.UNRECONSTRUCTABLE.value for item in target_scope):
                    status = RelationshipStatus.INSUFFICIENT_EVIDENCE
                elif IntegrityStatus.AMBIGUOUS.value in statuses or IntegrityStatus.STALE.value in statuses:
                    status = RelationshipStatus.AMBIGUOUS
                else:
                    status = RelationshipStatus.IDENTITY_MISMATCH if mismatches else RelationshipStatus.MISSING_COUNTERPART
                results.append(_result(
                    rule, source, expectation, (), key, kind, status,
                    blockers=all_blockers,
                    historical=tuple(item.finding_id for item in all_blockers if item.status == IntegrityStatus.HISTORICAL_LIMITATION.value),
                    mismatches=mismatches,
                ))
                continue
            if rule.cardinality in {Cardinality.EXACTLY_ONE, Cardinality.ZERO_OR_ONE} and len(matches) > 1:
                results.append(_result(rule, source, expectation, matches, key, kind, RelationshipStatus.CARDINALITY_CONFLICT, blockers=combined_blockers))
                matched_target_refs.update(item.reference for item in matches)
                continue
            checks = tuple(check for target in matches for check in _semantic_checks(rule, source, target))
            timing = any(_timing_mismatch(rule, source, target) for target in matches)
            status = RelationshipStatus.TIMING_MISMATCH if timing else RelationshipStatus.SEMANTIC_MISMATCH if any(not item.agrees for item in checks) else RelationshipStatus.RECONCILED
            results.append(_result(rule, source, expectation, matches, key, kind, status, checks=checks, blockers=combined_blockers))
            matched_target_refs.update(item.reference for item in matches)

        if rule.bidirectional_orphan_check:
            for target in targets:
                if target.reference in matched_target_refs:
                    continue
                # Do not cascade an integrity-invalid target into an orphan.
                target_state, target_findings = _integrity_state(
                    target, self._findings_for_ref(target)
                )
                if target_state is ExpectationState.BLOCKED_BY_INTEGRITY:
                    continue
                if rule.rule_id in {"DECISION_MARKET", "DECISION_STRATEGY"} and not _value(target.record, "entity_id"):
                    continue  # contract permits standalone observations
                orphan_expectation = ExpectationDecision(
                    target.universe.value, _identity(target.record, target.universe, target.dataset),
                    rule.source_universe.value, ExpectationState.REQUIRED.value,
                    f"{rule.rule_id}_ORPHAN", (), rule.integrity_prerequisites,
                    "target evidence requires a valid parent under the bidirectional contract",
                )
                orphan_rule = ReconciliationRule(
                    f"{rule.rule_id}_ORPHAN", target.universe, rule.source_universe,
                    "target exists", "valid parent", rule.join_fields, rule.fallback_join_fields,
                    Cardinality.EXACTLY_ONE, rule.timing_condition, "standalone only where contract permits",
                    "none", rule.integrity_prerequisites, rule.semantic_fields, False,
                )
                results.append(_result(
                    orphan_rule, target, orphan_expectation, (), {}, MatchKind.NONE,
                    RelationshipStatus.ORPHAN_EVIDENCE, blockers=target_findings,
                ))
        results.sort(key=lambda item: item.fingerprint)
        return tuple(results)

    def reconcile_pair(self, source: Universe, target: Universe) -> tuple[ReconciliationResult, ...]:
        selected = [rule for rule in self.rules if rule.source_universe is source and rule.target_universe is target]
        return tuple(sorted((result for rule in selected for result in self.reconcile_rule(rule.rule_id)), key=lambda item: item.fingerprint))

    def reconcile_entity(self, identity: str) -> tuple[ReconciliationResult, ...]:
        all_results = self.reconcile_all().results
        return tuple(result for result in all_results if identity in {str(value) for value in result.source_identity.values()} or any(identity in {str(value) for value in target.values()} for target in result.target_identities))

    def reconcile_all(self) -> ReconciliationReport:
        results = tuple(sorted((result for rule in self.rules for result in self.reconcile_rule(rule.rule_id)), key=lambda item: item.fingerprint))
        counts = Counter(result.relationship_status for result in results)
        rule_counts: dict[str, Counter[str]] = defaultdict(Counter)
        for result in results:
            rule_counts[result.rule_id][result.relationship_status] += 1
        return ReconciliationReport(
            results, dict(sorted(counts.items())),
            {key: dict(sorted(value.items())) for key, value in sorted(rule_counts.items())},
            tuple(sorted(manifest.fingerprint for manifest in self.inputs.manifests)),
        )


def describe_reconciliation() -> dict[str, Any]:
    return {
        "schema": RECONCILIATION_SCHEMA_VERSION,
        "wave1_authority": "research_engine.v10.universes.assurance",
        "wave2_authority": "research_engine.v10.universes.evidence_integrity",
        "active_universes": [item.value for item in expected_active_universes()],
        "rules": [rule.to_dict() for rule in get_reconciliation_rules()],
        "conflict_precedence": [item.value for item in _STATUS_PRECEDENCE],
        "joins_by_symbol_or_time_proximity": False,
        "hot_path": False,
        "mutates_source_evidence": False,
    }


__all__ = [
    "Cardinality", "ComparisonKind", "ExpectationDecision", "ExpectationState",
    "MatchKind", "ReconciliationEngine", "ReconciliationInput",
    "ReconciliationReport", "ReconciliationResult", "ReconciliationRule",
    "ReconciliationTrace", "RelationshipStatus", "SemanticCheck",
    "describe_reconciliation", "get_reconciliation_rules",
]
