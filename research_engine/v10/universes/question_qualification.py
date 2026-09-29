"""Stage IV Wave 4 question-level evidence qualification.

This module is an assurance overlay.  It derives one evidence contract from the
canonical 70-question registry, then qualifies caller-supplied Wave 2 and Wave
3 artifacts.  It never reads or mutates research answers, lifecycle state,
production evidence, or trading configuration.

The canonical registry and its effective definitions remain the question
authority.  The small dataset-to-universe table below is an integration adapter
between that legacy registry vocabulary and the Wave 1 analytical topology; it
is not a second question bank.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from research_engine.registry.definition_validator import (
    build_definitions_from_registry,
    get_question_health,
    validate_all_definitions,
)
from research_engine.registry.master_repair_ledger import MASTER_REPAIR_LEDGER
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.v10.universes.assurance import (
    expected_active_universes,
    get_universe_contract,
)
from research_engine.v10.universes.assurance_consumption_gate import (
    ALLOWED_SCIENTIFIC_STATES,
    EVIDENCE_COLLECTING_SCIENTIFIC_STATES,
    SETTLED_SCIENTIFIC_STATES,
    admit as _admit_downstream,
    gate_report,
)
from research_engine.v10.universes.assurance_provenance import (
    ACCOUNTING_SCHEMA_VERSION,
    BlockerRecord,
    DatasetSubstitution,
    ExclusionReason,
    FieldResolution,
    GOVERNED_REQUIREMENT_AUTHORITIES,
    HistoricalExhaustionStatus,
    OriginStage,
    OriginType,
    ResultAvailability,
    SourceAccounting,
    assert_accounting_conservation,
    assert_ledger_reconciles,
    build_evidence_accounting,
    build_report_authority,
    build_source_accounting,
    deduplicate_blockers,
    evaluate_dataset_substitution,
    evaluate_field_resolution,
    evaluate_historical_exhaustion,
    ledger_category_counts,
    ledger_evidence_reference_count,
    make_blocker,
    requirement_authority_is_governed,
    resolve_contract_fields,
    supports_future_data,
)
from research_engine.v10.universes.evidence_integrity import (
    EvidenceBatch,
    EvidenceManifest,
    IntegrityFinding,
    IntegrityReport,
    IntegrityStatus,
    ReconstructedArtifact,
)
from research_engine.v10.universes.models import Universe
from research_engine.v10.universes.reconciliation import (
    ReconciliationReport,
    ReconciliationResult,
    RelationshipStatus,
    get_reconciliation_rules,
)


QUALIFICATION_SCHEMA_VERSION = 4
CANONICAL_QUESTION_COUNT = 70



class QuestionContractError(ValueError):
    """A question contract is incomplete, stale, or non-canonical."""


class QualificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    PARTIAL = "PARTIAL"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    INDETERMINATE = "INDETERMINATE"


class SufficiencyState(str, Enum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class StatisticalState(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"
    NOT_REQUIRED = "NOT_REQUIRED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SufficiencyAssessment:
    structural: str
    population: str
    resolution: str
    lineage: str
    integrity: str
    reconciliation: str
    statistical: str


@dataclass(frozen=True)
class QuestionEvidenceContract:
    question_id: str
    canonical_question_reference: str
    question_fingerprint: str
    question_wording: str
    category: str
    phenomenon: str
    population: str
    resolution: str
    time_horizon: str
    required_universes: tuple[str, ...]
    required_datasets: tuple[str, ...]
    required_relationships: tuple[str, ...]
    required_lineage: tuple[str, ...]
    required_fields: tuple[str, ...]
    terminality: str
    minimum_population_condition: str
    minimum_sample: int | None
    exclusions: tuple[str, ...]
    historical_boundaries: tuple[str, ...]
    known_limitations: tuple[str, ...]
    implementation: str
    dependencies: tuple[str, ...]
    definition_health: str
    contract_fingerprint: str
    schema: int = QUALIFICATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _native(asdict(self))


@dataclass(frozen=True)
class QuestionEvidenceInput:
    """Question-specific facts not inferable from manifests.

    Explicit values are intentional: a manifest proves physical evidence, but
    cannot by itself prove that every symbol/broker/date intended by a question
    was observed or that an aggregate has the required analytical grain.
    """

    question_id: str
    contract_fingerprint: str = ""
    existing_state: str = ""
    existing_result: Any = None
    actual_population: int | None = None
    expected_population: int | None = None
    population_complete: bool | None = None
    collection_functional: bool | None = None
    observed_resolution: str = ""
    observed_fields: tuple[str, ...] = ()
    available_lineage: tuple[str, ...] = ()
    evidence_scope: Mapping[str, Any] = field(default_factory=dict)
    supported_scope: Mapping[str, Any] = field(default_factory=dict)
    unsupported_scope: Mapping[str, Any] = field(default_factory=dict)
    exclusions: tuple[str, ...] = ()
    historical_limitations: tuple[str, ...] = ()
    statistical_state: StatisticalState = StatisticalState.UNKNOWN
    negative_result: bool = False
    negative_observable: bool | None = None
    evidence_references: tuple[str, ...] = ()
    record_accounting: Mapping[str, Any] = field(default_factory=dict)
    #: explicit per-field resolution provenance; absence is resolved from
    #: ``observed_fields`` and can only ever yield EXACT or UNAVAILABLE.
    field_resolutions: tuple[FieldResolution, ...] = ()
    #: explicit dataset substitution provenance (governed aliases/projections
    #: or Wave 2 reconstruction) used to satisfy a required dataset.
    dataset_substitutions: tuple[DatasetSubstitution, ...] = ()
    #: which *upstream* authority vouches for each claimable requirement.
    requirement_authorities: Mapping[str, str] = field(default_factory=dict)



@dataclass(frozen=True)
class QuestionQualification:
    question_id: str
    question_fingerprint: str
    contract_fingerprint: str
    existing_research_state: str
    existing_research_result: Any
    qualification_status: str
    phenomenon: str
    required_population: str
    required_resolution: str
    required_universes: tuple[str, ...]
    required_relationships: tuple[str, ...]
    required_lineage: tuple[str, ...]
    actual_population: int
    evidence_scope: Mapping[str, Any]
    sufficiency: SufficiencyAssessment
    integrity_state: tuple[str, ...]
    reconciliation_state: tuple[str, ...]
    historical_limitations: tuple[str, ...]
    reconstruction_involvement: tuple[str, ...]
    supported_scope: Mapping[str, Any]
    unsupported_scope: Mapping[str, Any]
    reason_codes: tuple[str, ...]
    explanation: str
    evidence_references: tuple[str, ...]
    blocker_ids: tuple[str, ...]
    blocker_provenance: Mapping[str, tuple[str, ...]]
    record_accounting: Mapping[str, Any]
    #: deduplicated per-blocker provenance ledger for this question
    blockers: tuple[BlockerRecord, ...] = ()
    #: candidate/used/excluded/unexplained accounting over historical evidence
    evidence_accounting: Mapping[str, Any] = field(default_factory=dict)
    field_resolutions: tuple[FieldResolution, ...] = ()
    dataset_substitutions: tuple[DatasetSubstitution, ...] = ()
    report_authority: Mapping[str, Any] = field(default_factory=dict)
    downstream_gate: Mapping[str, Any] = field(default_factory=dict)
    qualification_fingerprint: str = ""
    schema: int = QUALIFICATION_SCHEMA_VERSION


    def to_dict(self) -> dict[str, Any]:
        return _native(asdict(self))


@dataclass(frozen=True)
class AssuranceContradiction:
    question_id: str
    existing_state: str
    qualification_status: str
    contradiction_type: str
    reason: str


@dataclass(frozen=True)
class QuestionQualificationReport:
    qualifications: tuple[QuestionQualification, ...]
    contradictions: tuple[AssuranceContradiction, ...]
    counts: Mapping[str, int]
    blocker_counts: Mapping[str, int]
    contract_set_fingerprint: str
    report_fingerprint: str
    blocker_ledger: tuple[BlockerRecord, ...] = ()
    unique_blocker_count: int = 0
    question_blocker_reference_count: int = 0
    evidence_reference_count: int = 0
    q71_gate: Mapping[str, Any] = field(default_factory=dict)
    schema: int = QUALIFICATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "question_authority": "research_engine.registry.research_question_registry.REGISTRY",
            "question_count": len(self.qualifications),
            "contract_set_fingerprint": self.contract_set_fingerprint,
            "counts": dict(self.counts),
            "blocker_counts": dict(self.blocker_counts),
            "unique_blocker_count": self.unique_blocker_count,
            "question_blocker_reference_count": self.question_blocker_reference_count,
            "evidence_reference_count": self.evidence_reference_count,
            "blocker_ledger": [item.to_dict() for item in self.blocker_ledger],
            "q71_gate": dict(self.q71_gate),
            "contradictions": [_native(asdict(item)) for item in self.contradictions],
            "qualifications": [item.to_dict() for item in self.qualifications],
            "report_fingerprint": self.report_fingerprint,
        }

    def semantic_material(self) -> dict[str, Any]:
        """The exact payload protected by ``report_fingerprint``."""
        return {
            "contract_set_fingerprint": self.contract_set_fingerprint,
            "qualifications": [item.to_dict() for item in self.qualifications],
            "counts": dict(sorted(self.counts.items())),
            "blocker_counts": dict(sorted(self.blocker_counts.items())),
            "unique_blocker_count": self.unique_blocker_count,
            "question_blocker_reference_count": self.question_blocker_reference_count,
            "evidence_reference_count": self.evidence_reference_count,
            "blocker_ledger": [item.to_dict() for item in self.blocker_ledger],
            "q71_gate": dict(self.q71_gate),
            "contradictions": [_native(asdict(item)) for item in self.contradictions],
        }


_DATASET_UNIVERSES: Mapping[str, tuple[Universe, ...]] = {
    "shadow_trades": (Universe.SHADOW_OUTCOME,),
    "shadow_runtime": (Universe.SHADOW_OUTCOME,),
    "decision_trace": (Universe.DECISION,),
    "trade_truth": (Universe.EXECUTION, Universe.OUTCOME),
    "management_actions": (Universe.EXECUTION, Universe.OUTCOME),
    "market_context": (Universe.MARKET,),
    "execution_context": (Universe.EXECUTION,),
    "slippage_journal": (Universe.EXECUTION,),
    "execution_results": (Universe.EXECUTION,),
    "execution_attempts": (Universe.EXECUTION,),
    "protection_audit": (Universe.RISK,),
    "risk_deviation": (Universe.RISK,),
    "strategy_candidates": (Universe.STRATEGY,),
    "horizon_candidates": (Universe.STRATEGY,),
    "portfolio_rankings": (Universe.STRATEGY, Universe.DECISION),
    "portfolio_shadow": (Universe.STRATEGY, Universe.SHADOW_OUTCOME),
    "equity_curve": (Universe.OUTCOME,),
}

_AMBIGUOUS_INTEGRITY = frozenset({
    IntegrityStatus.IDENTITY_CONFLICT.value,
    IntegrityStatus.BROKEN_CONTINUITY.value,
    IntegrityStatus.ORDERING_VIOLATION.value,
    IntegrityStatus.AMBIGUOUS.value,
    IntegrityStatus.SOURCE_MODIFIED.value,
})
_ABSENT_INTEGRITY = frozenset({
    IntegrityStatus.MISSING_EXPECTED.value,
    IntegrityStatus.UNRECONSTRUCTABLE.value,
    IntegrityStatus.MANIFEST_SOURCE_MISSING.value,
})
_DEGRADED_INTEGRITY = frozenset({
    IntegrityStatus.DUPLICATE.value,
    IntegrityStatus.STALE.value,
    IntegrityStatus.DELAYED.value,
    IntegrityStatus.COUNTERPART_MISSING.value,
    IntegrityStatus.HISTORICAL_LIMITATION.value,
    IntegrityStatus.NEW_UNMANIFESTED.value,
})
_AMBIGUOUS_RELATIONSHIPS = frozenset({
    RelationshipStatus.CARDINALITY_CONFLICT.value,
    RelationshipStatus.IDENTITY_MISMATCH.value,
    RelationshipStatus.SEMANTIC_MISMATCH.value,
    RelationshipStatus.TIMING_MISMATCH.value,
    RelationshipStatus.BLOCKED_BY_INTEGRITY.value,
    RelationshipStatus.AMBIGUOUS.value,
    RelationshipStatus.ORPHAN_EVIDENCE.value,
})
_ABSENT_RELATIONSHIPS = frozenset({
    RelationshipStatus.MISSING_COUNTERPART.value,
    RelationshipStatus.INSUFFICIENT_EVIDENCE.value,
})
_DEGRADED_RELATIONSHIPS = frozenset({
    RelationshipStatus.PENDING.value,
    RelationshipStatus.EXTRA_COUNTERPART.value,
    RelationshipStatus.HISTORICAL_LIMITATION.value,
})


def _relationship_is_fatal(item: ReconciliationResult, contract: "QuestionEvidenceContract") -> bool:
    """May this Wave 3 result block *this* question?

    Relevance, not proximity: a NOT_REQUIRED relationship never blocks.  A
    required relationship that is ambiguous, absent, or degraded must still be
    treated as relevant even when Wave 3 marked ``dependent_impact`` as false,
    because false impact is only a non-blocking optimization for cleanly
    satisfied relationships and never suppresses a contract-breaking mismatch.
    """
    if item.relationship_status == RelationshipStatus.NOT_REQUIRED.value:
        return False
    if item.expectation_state and str(item.expectation_state).upper() == "NOT_REQUIRED":
        return False
    if item.rule_id not in set(contract.required_relationships):
        return False
    if item.relationship_status in set(_AMBIGUOUS_RELATIONSHIPS) | set(_ABSENT_RELATIONSHIPS) | set(_DEGRADED_RELATIONSHIPS):
        return True
    if item.dependent_impact is False:
        return False
    return True



def _native(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_native(item) for item in value]
    return value


def _canonical(value: Any) -> str:
    return json.dumps(_native(value), sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _aggregate_reference(kind: str, values: Iterable[Any]) -> str:
    """Return one stable reference for a potentially very large evidence set."""
    material = tuple(sorted({str(value) for value in values if value not in (None, "")}))
    return f"{kind}:{len(material)}:{_hash(material)}"


#: Reasons that describe a *settled* outcome rather than a blocker.
_NON_BLOCKER_REASONS = frozenset({"QUALIFIED", "VERIFIED_INSUFFICIENT_DATA"})
# Only these scientific states are recognized as valid governance-backed states
# for a verified result.  A raw string outside this set must never be treated as
# a legitimate scientific conclusion path.
_GOVERNED_SCIENTIFIC_STATES = frozenset(ALLOWED_SCIENTIFIC_STATES)


def _bucket(
    records: Iterable[Any], key_fn: Any,
) -> list[tuple[Any, list[Any]]]:
    """Group records deterministically by a material identity key."""
    grouped: dict[Any, list[Any]] = {}
    for record in records:
        grouped.setdefault(key_fn(record), []).append(record)
    return [(key, grouped[key]) for key in sorted(grouped, key=str)]


def _exact_references(kind: str, ids: Iterable[Any], limit: int = 8) -> tuple[str, ...]:
    """Exact upstream ids when few, one deterministic aggregate when many."""
    values = list(dict.fromkeys(str(item) for item in ids if item not in (None, "")))
    if not values:
        return ()
    if len(values) <= limit:
        return tuple(values)
    return (f"{kind}:{len(values)}:{_hash(values)}",)



def _is_exact_reconstruction(item: ReconstructedArtifact) -> bool:
    """Exact evidence cannot simultaneously declare source or information loss."""
    return bool(item.exact and item.source_complete and not item.information_loss)


def _dataset_key(name: str) -> str:
    value = str(name).strip().lower()
    if value.endswith("_v1"):
        value = value[:-3]
    if value == "research_shadow_trades":
        return "shadow_trades"
    return value


def universes_for_dataset(name: str) -> tuple[Universe, ...]:
    """Map canonical production dataset vocabulary to Wave 1 universes."""
    if _dataset_key(name) == "decision_trace":
        return (Universe.DECISION, Universe.MARKET, Universe.STRATEGY, Universe.RISK)
    return _DATASET_UNIVERSES.get(_dataset_key(name), ())


def _question_fingerprint(question: Any, definition: Any) -> str:
    return _hash({"question": question.to_dict(), "effective_definition": definition.to_dict()})


def _contract_payload(item: QuestionEvidenceContract) -> dict[str, Any]:
    """Return the semantic material protected by ``contract_fingerprint``."""
    return {
        "question_id": item.question_id,
        "question_fingerprint": item.question_fingerprint,
        "phenomenon": item.phenomenon,
        "population": item.population,
        "resolution": item.resolution,
        "universes": item.required_universes,
        "datasets": item.required_datasets,
        "relationships": item.required_relationships,
        "lineage": item.required_lineage,
        "fields": item.required_fields,
        "terminality": item.terminality,
        "minimum": item.minimum_population_condition,
        "epoch": item.time_horizon,
    }


def _infer_universes(
    question: Any,
    datasets: Sequence[str] | None = None,
    fields: Sequence[str] | None = None,
) -> tuple[Universe, ...]:
    if question.id in {"G1", "G3", "L6"}:
        return expected_active_universes()
    result: set[Universe] = set()
    source_names = tuple(datasets) if datasets is not None else tuple(source.value for source in question.data_sources)
    for source in source_names:
        result.update(_DATASET_UNIVERSES.get(_dataset_key(source), ()))
    field_text = " ".join(fields if fields is not None else question.required_fields).lower()
    if "regime" in field_text or "market_phase" in field_text or "session" in field_text or "volatility" in field_text:
        result.add(Universe.MARKET)
    if "strategy" in field_text or "horizon" in field_text:
        result.add(Universe.STRATEGY)
    if any(token in field_text for token in ("risk", "stop", "drawdown", "sizing")):
        result.add(Universe.RISK)
    if any(token in field_text for token in ("r_multiple", "pnl", "exit_reason", "mfe", "mae")):
        result.add(Universe.OUTCOME if "trade_truth" in {_dataset_key(s) for s in source_names} else Universe.SHADOW_OUTCOME)
    active = set(expected_active_universes())
    return tuple(universe for universe in expected_active_universes() if universe in result & active)


def _infer_resolution(question: Any, population: str) -> str:
    material = f"{question.description} {population} {' '.join(question.required_fields)}".lower()
    for token, label in (
        ("research cycle", "research cycle"),
        ("candidate", "candidate"),
        ("excursion", "intratrade excursion path"),
        ("canonical opportunity", "canonical opportunity"),
        ("opportunit", "canonical opportunity"),
        ("lifecycle", "lifecycle"),
        ("per-trade", "completed trade"),
        ("per trade", "completed trade"),
        ("completed trade", "completed trade"),
        ("decision", "terminal decision"),
        ("trade", "completed trade"),
        ("symbol", "symbol bucket"),
    ):
        if token in material:
            return label
    sources = {_dataset_key(source.value) for source in question.data_sources}
    if "shadow_trades" in sources:
        return "completed simulated trade per canonical shadow lifecycle"
    if "decision_trace" in sources:
        return "terminal decision per analytical entity"
    return "one canonical record at the governed runner's unit of analysis"


def _infer_terminality(question: Any, resolution: str) -> str:
    material = f"{question.description} {' '.join(question.required_fields)} {resolution}".lower()
    if "excursion" in material or "mfe" in material or "mae" in material:
        return "terminal outcome with complete ordered excursion history"
    if "shadow" in material or "r_multiple" in material or "exit" in material:
        return "terminal outcome"
    if "decision" in material:
        return "terminal decision"
    return "as declared by the canonical completion rule"


def _contract_relationships(universes: Sequence[Universe]) -> tuple[str, ...]:
    selected = set(universes)
    result = []
    for rule in get_reconciliation_rules():
        if rule.source_universe in selected and rule.target_universe in selected:
            result.append(rule.rule_id)
    return tuple(sorted(set(result)))


def _lineage(universes: Sequence[Universe], definition: Any, question: Any) -> tuple[str, ...]:
    fields: set[str] = set()
    for universe in universes:
        fields.update(get_universe_contract(universe).identity.primary_fields)
    if definition.join_contract:
        fields.update(definition.join_contract.join_keys)
    fields.update(
        field_name for field_name in question.required_fields
        if field_name.endswith("_id") or field_name in {"symbol", "account", "broker"}
    )
    return tuple(sorted(fields))


def _minimum_condition(definition: Any, question: Any) -> str:
    if definition.completion_rule and definition.completion_rule.description:
        return definition.completion_rule.description
    if definition.minimum_sample is not None:
        return f"at least {definition.minimum_sample} qualifying observations"
    if question.validation_rules:
        return "; ".join(rule.description or f"{rule.field} {rule.operator} {rule.threshold}" for rule in question.validation_rules)
    return "structural evidence must be observable; statistical sufficiency is reported separately"


def build_question_evidence_contracts() -> tuple[QuestionEvidenceContract, ...]:
    """Derive exactly one contract per canonical question, in registry order."""
    definitions = build_definitions_from_registry(REGISTRY)
    health = validate_all_definitions(definitions)
    contracts: list[QuestionEvidenceContract] = []
    for question in REGISTRY:
        definition = definitions[question.id]
        required_datasets = tuple(dict.fromkeys(
            authority.dataset for authority in definition.evidence_authorities
        )) or tuple(source.value for source in question.data_sources)
        authority_fields = tuple(dict.fromkeys(
            field_name.strip()
            for authority in definition.evidence_authorities
            for field_name in str(authority.field_path or "").split(",")
            if field_name.strip()
        ))
        required_fields = authority_fields or tuple(question.required_fields)
        universes = _infer_universes(question, required_datasets, required_fields)
        population = definition.population_definition.strip() or (
            "Canonical records from "
            + ", ".join(source.value for source in question.data_sources)
            + " satisfying the registry-required fields and governed runner filters."
        )
        resolution = _infer_resolution(question, population)
        q_fingerprint = _question_fingerprint(question, definition)
        limitations: list[str] = []
        if not definition.population_definition.strip():
            limitations.append("effective canonical definition does not declare a specific population")
        if not definition.metric_definition.strip():
            limitations.append("effective canonical definition does not declare a specific metric")
        for universe in universes:
            limitations.extend(
                f"{universe.value}: {item}" for item in get_universe_contract(universe).limitations
            )
        payload = {
            "question_id": question.id,
            "question_fingerprint": q_fingerprint,
            "phenomenon": definition.research_intent.strip() or question.description,
            "population": population,
            "resolution": resolution,
            "universes": [item.value for item in universes],
            "datasets": required_datasets,
            "relationships": _contract_relationships(universes),
            "lineage": _lineage(universes, definition, question),
            "fields": required_fields,
            "terminality": _infer_terminality(question, resolution),
            "minimum": _minimum_condition(definition, question),
            "epoch": definition.epoch_requirement,
        }
        contracts.append(QuestionEvidenceContract(
            question_id=question.id,
            canonical_question_reference=f"REGISTRY_BY_ID[{question.id!r}]",
            question_fingerprint=q_fingerprint,
            question_wording=question.description,
            category=question.category.value,
            phenomenon=payload["phenomenon"],
            population=population,
            resolution=resolution,
            time_horizon=definition.epoch_requirement,
            required_universes=tuple(item.value for item in universes),
            required_datasets=required_datasets,
            required_relationships=payload["relationships"],
            required_lineage=payload["lineage"],
            required_fields=required_fields,
            terminality=payload["terminality"],
            minimum_population_condition=payload["minimum"],
            minimum_sample=definition.minimum_sample,
            exclusions=("records outside the effective definition/runner filters",),
            historical_boundaries=(f"epoch_requirement={definition.epoch_requirement}",),
            known_limitations=tuple(dict.fromkeys(limitations)),
            implementation=(
                f"SCIENTIFIC_ALIAS:{question.scientific_owner_id}"
                if question.scientific_owner_id else
                f"{question.runner_module}.{question.runner_function}"
                if question.runner_module and question.runner_function else "NO_GOVERNED_RUNNER"
            ),
            dependencies=tuple(question.depends_on),
            definition_health=get_question_health(health[question.id]),
            contract_fingerprint=_hash(payload),
        ))
    validate_question_evidence_contracts(contracts)
    return tuple(contracts)


def validate_question_evidence_contracts(
    contracts: Iterable[QuestionEvidenceContract],
) -> None:
    """Fail closed on incomplete, duplicate, extra, or stale contracts."""
    items = tuple(contracts)
    ids = [item.question_id for item in items]
    if len(ids) != len(set(ids)):
        raise QuestionContractError("duplicate question evidence contract")
    canonical_ids = tuple(question.id for question in REGISTRY)
    if len(canonical_ids) != CANONICAL_QUESTION_COUNT:
        raise QuestionContractError(f"canonical question count changed: {len(canonical_ids)}")
    if set(ids) != set(canonical_ids):
        raise QuestionContractError(
            f"contract coverage mismatch: missing={sorted(set(canonical_ids)-set(ids))}, "
            f"extra={sorted(set(ids)-set(canonical_ids))}"
        )
    definitions = build_definitions_from_registry(REGISTRY)
    active = {item.value for item in expected_active_universes()}
    valid_rules = {rule.rule_id for rule in get_reconciliation_rules()}
    for item in items:
        for name in ("canonical_question_reference", "question_wording", "phenomenon", "population", "resolution", "terminality", "minimum_population_condition"):
            if not str(getattr(item, name)).strip():
                raise QuestionContractError(f"{item.question_id}: missing {name}")
        if not item.required_universes:
            raise QuestionContractError(f"{item.question_id}: no required universe")
        if set(item.required_universes) - active:
            raise QuestionContractError(f"{item.question_id}: invalid universe")
        if set(item.required_relationships) - valid_rules:
            raise QuestionContractError(f"{item.question_id}: invalid relationship")
        question = REGISTRY_BY_ID[item.question_id]
        expected_question_fp = _question_fingerprint(question, definitions[item.question_id])
        if item.question_fingerprint != expected_question_fp:
            raise QuestionContractError(f"{item.question_id}: stale question fingerprint")
        if item.contract_fingerprint != _hash(_contract_payload(item)):
            raise QuestionContractError(f"{item.question_id}: stale contract fingerprint")
        expected_datasets = tuple(dict.fromkeys(
            authority.dataset for authority in definitions[item.question_id].evidence_authorities
        )) or tuple(source.value for source in question.data_sources)
        if item.required_datasets != expected_datasets:
            raise QuestionContractError(f"{item.question_id}: datasets diverge from effective canonical definition")
        if item.minimum_sample is not None and item.minimum_sample < 0:
            raise QuestionContractError(f"{item.question_id}: contradictory minimum sample")


class QualificationEngine:
    """Qualify canonical questions against explicit Wave 2/3 artifacts."""

    def __init__(
        self,
        *,
        inputs: Mapping[str, QuestionEvidenceInput] | None = None,
        batches: Sequence[EvidenceBatch] = (),
        integrity_report: IntegrityReport | None = None,
        reconciliation_report: ReconciliationReport | None = None,
        contracts: Sequence[QuestionEvidenceContract] | None = None,
    ) -> None:
        self.contracts = tuple(contracts or build_question_evidence_contracts())
        validate_question_evidence_contracts(self.contracts)
        self._contracts = {item.question_id: item for item in self.contracts}
        self.inputs = dict(inputs or {})
        unknown = set(self.inputs) - set(self._contracts)
        if unknown:
            raise QuestionContractError(f"inputs reference non-canonical questions: {sorted(unknown)}")
        for qid, value in self.inputs.items():
            if value.question_id != qid:
                raise QuestionContractError(f"input key/id mismatch for {qid}")
            if value.contract_fingerprint and value.contract_fingerprint != self._contracts[qid].contract_fingerprint:
                raise QuestionContractError(f"{qid}: stale contract fingerprint")
        self.batches = tuple(batches)
        self.integrity_report = integrity_report or IntegrityReport((), ())
        self.reconciliation_report = reconciliation_report or ReconciliationReport((), {}, {}, ())
        self._cache: dict[str, QuestionQualification] = {}
        self._reconstruction_reference_cache: dict[tuple[str, ...], str] = {}
        self._manifest_cache: dict[tuple[str, ...], tuple[EvidenceManifest, ...]] = {}
        self._batch_cache: dict[tuple[str, ...], tuple[EvidenceBatch, ...]] = {}
        self._finding_cache: dict[tuple[tuple[str, ...], tuple[str, ...]], tuple[IntegrityFinding, ...]] = {}
        self._reconciliation_cache: dict[tuple[str, ...], tuple[ReconciliationResult, ...]] = {}
        self._reconstruction_cache: dict[tuple[str, ...], tuple[ReconstructedArtifact, ...]] = {}

    def _input(self, qid: str) -> QuestionEvidenceInput:
        if qid in self.inputs:
            return self.inputs[qid]
        ledger = MASTER_REPAIR_LEDGER[qid]
        return QuestionEvidenceInput(
            question_id=qid,
            existing_state=ledger.current_operational_state_if_known,
        )

    @staticmethod
    def _relevant_dataset(name: str, required: Sequence[str]) -> bool:
        key = _dataset_key(name)
        return key in {_dataset_key(item) for item in required}

    def _manifests(self, contract: QuestionEvidenceContract) -> tuple[EvidenceManifest, ...]:
        key = tuple(sorted({_dataset_key(item) for item in contract.required_datasets}))
        if key not in self._manifest_cache:
            self._manifest_cache[key] = tuple(
                item for item in self.integrity_report.manifests
                if _dataset_key(item.dataset) in key
            )
        return self._manifest_cache[key]

    def _batches(self, contract: QuestionEvidenceContract) -> tuple[EvidenceBatch, ...]:
        key = tuple(sorted({_dataset_key(item) for item in contract.required_datasets}))
        if key not in self._batch_cache:
            self._batch_cache[key] = tuple(
                item for item in self.batches if _dataset_key(item.dataset) in key
            )
        return self._batch_cache[key]

    def _findings(self, contract: QuestionEvidenceContract) -> tuple[IntegrityFinding, ...]:
        universes = set(contract.required_universes)
        datasets = tuple(sorted({_dataset_key(item) for item in contract.required_datasets}))
        key = (datasets, tuple(sorted(universes)))
        if key not in self._finding_cache:
            self._finding_cache[key] = tuple(
                item for item in self.integrity_report.findings
                if (
                    _dataset_key(item.dataset) in datasets
                    if item.dataset else item.universe in universes
                )
            )
        return self._finding_cache[key]

    def _reconciliations(self, contract: QuestionEvidenceContract) -> tuple[ReconciliationResult, ...]:
        key = tuple(sorted(set(contract.required_relationships)))
        if key not in self._reconciliation_cache:
            required = set(key)
            self._reconciliation_cache[key] = tuple(
                item for item in self.reconciliation_report.results if item.rule_id in required
            )
        return self._reconciliation_cache[key]

    def _reconstructions(self, contract: QuestionEvidenceContract) -> tuple[ReconstructedArtifact, ...]:
        key = tuple(sorted({_dataset_key(item) for item in contract.required_datasets}))
        if key not in self._reconstruction_cache:
            self._reconstruction_cache[key] = tuple(
                item for item in self.integrity_report.reconstructions
                if _dataset_key(item.target_dataset) in key
            )
        return self._reconstruction_cache[key]

    def _reconstruction_reference(
        self, items: Sequence[ReconstructedArtifact],
    ) -> str:
        key = tuple(sorted({_dataset_key(item.target_dataset) for item in items}))
        if not key:
            return ""
        if key not in self._reconstruction_reference_cache:
            self._reconstruction_reference_cache[key] = _aggregate_reference(
                "reconstructions",
                (
                    f"{item.target_dataset}:{item.reconstruction_rule}:{item.rule_version}:"
                    f"{_hash(item.source_identities)}"
                    for item in items
                ),
            )
        return self._reconstruction_reference_cache[key]

    def _loss_is_relevant(self, contract: QuestionEvidenceContract, item: ReconstructedArtifact) -> bool:
        if not item.information_loss:
            return False
        if _dataset_key(item.target_dataset) not in {_dataset_key(name) for name in contract.required_datasets}:
            return False
        return True

    def _dataset_substitutions(
        self,
        contract: QuestionEvidenceContract,
        reconstructions: Sequence[ReconstructedArtifact],
        supplied: QuestionEvidenceInput,
        direct_keys: set[str],
    ) -> tuple[tuple[DatasetSubstitution, bool, str], ...]:
        required = {_dataset_key(name) for name in contract.required_datasets}
        substitutions: list[tuple[DatasetSubstitution, bool, str]] = []
        seen: set[str] = set()

        for item in supplied.dataset_substitutions:
            key = _dataset_key(item.requested_source)
            if key in seen:
                continue
            seen.add(key)
            permitted = not bool(item.information_loss) or not item.question_relevant_loss
            accepted, reason = evaluate_dataset_substitution(item, permitted=permitted)
            substitutions.append((item, accepted, reason))

        for item in reconstructions:
            requested = _dataset_key(item.target_dataset)
            if requested not in required or requested in direct_keys or requested in seen:
                continue
            permitted = not bool(item.information_loss)
            substitution = DatasetSubstitution(
                requested_source=item.target_dataset,
                actual_source=item.source_datasets[0] if item.source_datasets else item.target_dataset,
                substitution_authority="WAVE2_RECONSTRUCTION_RULE",
                reconstruction_rule=item.reconstruction_rule,
                information_loss=tuple(item.information_loss),
                question_relevant_loss=not permitted,
                governed=True,
            )
            accepted, reason = evaluate_dataset_substitution(substitution, permitted=permitted)
            substitutions.append((substitution, accepted, reason))
            seen.add(requested)

        return tuple(substitutions)

    def _requirement_authorities(self, supplied: QuestionEvidenceInput) -> dict[str, str]:
        default = {
            "lineage": "PERSISTED_LINEAGE_EVIDENCE",
            "resolution": "GOVERNED_SOURCE_RESOLUTION",
            "integrity": "WAVE2_INTEGRITY_REPORT",
            "reconciliation": "WAVE3_RECONCILIATION_REPORT",
            "historical_exhaustiveness": "RECORD_ACCOUNTING",
        }
        explicit = dict(supplied.requirement_authorities)
        return {key: str(explicit.get(key) or default[key]) for key in default}

    def _report_authority(self, supplied: QuestionEvidenceInput, actual_population: int) -> dict[str, Any]:
        evidence_present = bool(actual_population > 0)
        state = str(supplied.existing_state or "").upper()
        if state == "COMPLETE" and evidence_present:
            validity = "VALID_CURRENT"
        elif state == "COMPLETE":
            validity = "MISSING"
        elif state in {"INSUFFICIENT_DATA", "WAITING_DATA"}:
            validity = "STALE" if evidence_present else "UNKNOWN"
        else:
            validity = "UNKNOWN"
        return build_report_authority(
            report_path=str(supplied.existing_result or ""),
            report_fingerprint_value=supplied.existing_result,
            report_validity=validity,
            evidence_epoch="CURRENT" if evidence_present else "",
            ownership_authority=self._requirement_authorities(supplied).get("resolution", "GOVERNED_SOURCE_RESOLUTION"),
            evidence_present=evidence_present,
        )

    def _evidence_accounting(
        self,
        contract: QuestionEvidenceContract,
        supplied: QuestionEvidenceInput,
        substitutions: Sequence[tuple[DatasetSubstitution, bool, str]],
    ) -> dict[str, Any]:
        explicit = dict(supplied.record_accounting or {})

        manifests = self._manifests(contract)
        batches = self._batches(contract)
        actual_population = supplied.actual_population
        if actual_population is None:
            actual_population = sum(item.record_count for item in manifests)
            if not manifests:
                actual_population = sum(len(item.records) for item in batches)

        raw_sources: list[SourceAccounting] = []
        for source_value in explicit.get("sources", ()):
            raw_sources.append(build_source_accounting(
                source_name=str(source_value.get("source") or ""),
                candidate_records=int(source_value.get("total_records", 0) or 0),
                used_records=int(source_value.get("current_records", 0) or 0),
                exclusion_reason_counts=dict(
                    source_value.get("exclusion_reason_counts") or {}
                ),
                source_present=source_value.get("available") is True,
                source_required=True,
            ))

        population = dict(explicit.get("population_stage") or {})
        population_candidate = population.get("candidate_records")
        population_used = int(population.get("used_records", actual_population) or 0)
        population_reasons = dict(population.get("exclusion_reason_counts") or {})

        # OPP-1's denominator is an independently governed population of
        # canonical opportunities. Raw horizon and outcome rows overlap through
        # a join and must never be added together to manufacture its denominator.
        if (
            contract.question_id == "OPP-1"
            and population_candidate is not None
            and explicit.get("candidate_denominator_authority")
            == "OPP1_GOVERNED_CANONICAL_OPPORTUNITY_POPULATION"
        ):
            sources = [build_source_accounting(
                source_name="OPP-1:canonical_opportunity_population",
                candidate_records=int(population_candidate),
                used_records=population_used,
                exclusion_reason_counts=population_reasons,
                source_present=True,
                source_required=True,
                reconstruction_used=any(
                    item.actual_source == "shadow_runtime"
                    for item, _accepted, _reason in substitutions
                ),
            )]
        elif raw_sources:
            sources = raw_sources
        else:
            sources = [build_source_accounting(
                source_name=contract.question_id,
                candidate_records=actual_population,
                used_records=actual_population,
                exclusion_reason_counts={},
                source_present=actual_population > 0,
                source_required=True,
            )]

        if population_candidate is None:
            population = {
                "candidate_records": 0,
                "used_records": 0,
                "excluded_records": 0,
                "unexplained_records": 0,
                "exclusion_reason_counts": {},
            }
        else:
            population_excluded = sum(int(value) for value in population_reasons.values())
            population = {
                "candidate_records": int(population_candidate),
                "used_records": population_used,
                "excluded_records": population_excluded,
                "unexplained_records": (
                    int(population_candidate) - population_used - population_excluded
                ),
                "exclusion_reason_counts": population_reasons,
            }

        source_resolved = bool(raw_sources) and all(
            item.source_present and item.unexplained_records == 0
            for item in raw_sources
        )
        population_resolved = (
            population_candidate is not None
            and int(population["unexplained_records"]) == 0
        )
        resolved = bool(
            explicit.get("resolved", source_resolved and population_resolved)
            and source_resolved
            and population_resolved
        ) if explicit else True

        accounting = build_evidence_accounting(
            sources=sources,
            used_records=population_used,
            population_stage=population,
            historical_exhaustion_status=HistoricalExhaustionStatus.ACCOUNTING_UNRESOLVED,
            historical_exhaustion_reason="historical exhaustion has not been derived",
            resolved=resolved,
        )
        if sources is not raw_sources and raw_sources:
            accounting["upstream_source_accounting"] = [
                item.to_dict() for item in raw_sources
            ]
        accounting["candidate_denominator_authority"] = str(
            explicit.get("candidate_denominator_authority") or ""
        )
        accounting["candidate_denominator_details"] = dict(
            explicit.get("candidate_denominator_details") or {}
        )
        exhaustion_status, exhaustion_reason = evaluate_historical_exhaustion(
            accounting, required_sources=contract.required_datasets,
        )
        accounting["historical_exhaustion_status"] = exhaustion_status
        accounting["historical_exhaustion_reason"] = exhaustion_reason
        assert_accounting_conservation(accounting)
        return accounting

    def qualify_question(self, question_id: str) -> QuestionQualification:
        if question_id not in self._contracts:
            raise KeyError(f"unknown canonical question {question_id!r}")
        if question_id not in self._cache:
            self._cache[question_id] = self._qualify(self._contracts[question_id], self._input(question_id))
        return self._cache[question_id]

    def qualify_questions(self, question_ids: Iterable[str]) -> tuple[QuestionQualification, ...]:
        ids = tuple(question_ids)
        if len(ids) != len(set(ids)):
            raise QuestionContractError("duplicate question requested")
        return tuple(self.qualify_question(qid) for qid in ids)

    def qualify_all(self) -> QuestionQualificationReport:
        values = tuple(self.qualify_question(item.question_id) for item in self.contracts)
        contradictions = tuple(
            AssuranceContradiction(
                question_id=item.question_id,
                existing_state=item.existing_research_state,
                qualification_status=item.qualification_status,
                contradiction_type="EXISTING_RESULT_EXCEEDS_ASSURANCE",
                reason=item.explanation,
            )
            for item in values
            if item.existing_research_state.upper() == "COMPLETE"
            and item.qualification_status != QualificationStatus.VERIFIED.value
        )
        counts = Counter(item.qualification_status for item in values)
        for status in QualificationStatus:
            counts.setdefault(status.value, 0)
        ledger = deduplicate_blockers(blockers for item in values for blockers in item.blockers)
        blocker_counts = ledger_category_counts(ledger)
        references = {item.question_id: tuple(item.blocker_ids) for item in values}
        assert_ledger_reconciles(ledger, references, blocker_counts)
        contract_set_fp = _hash([item.contract_fingerprint for item in self.contracts])
        report = QuestionQualificationReport(
            qualifications=values,
            contradictions=contradictions,
            counts=dict(sorted(counts.items())),
            blocker_counts=dict(blocker_counts),
            contract_set_fingerprint=contract_set_fp,
            report_fingerprint="",
            blocker_ledger=ledger,
            unique_blocker_count=len(ledger),
            question_blocker_reference_count=sum(len(ids) for ids in references.values()),
            evidence_reference_count=ledger_evidence_reference_count(ledger),
            q71_gate=gate_report([item.to_dict() for item in values]),
        )
        report = replace(report, report_fingerprint=_hash(report.semantic_material()))
        return report

    def trace(self, question_id: str) -> dict[str, Any]:
        contract = self._contracts[question_id]
        qualification = self.qualify_question(question_id)
        return {
            "canonical_question": REGISTRY_BY_ID[question_id].to_dict(),
            "evidence_contract": contract.to_dict(),
            "actual_evidence": {
                "manifests": [item.to_dict() for item in self._manifests(contract)],
                "integrity_findings": [item.to_dict() for item in self._findings(contract)],
                "reconciliation_results": [item.to_dict() for item in self._reconciliations(contract)],
                "reconstructions": [item.to_dict() for item in self._reconstructions(contract)],
                "input": _native(asdict(self._input(question_id))),
            },
            "qualification": qualification.to_dict(),
        }

    def questions_affected_by_finding(self, finding_id_or_fingerprint: str) -> tuple[str, ...]:
        findings = [
            item for item in self.integrity_report.findings
            if item.finding_id == finding_id_or_fingerprint or item.fingerprint == finding_id_or_fingerprint
        ]
        reconciliations = [
            item for item in self.reconciliation_report.results
            if item.fingerprint == finding_id_or_fingerprint or finding_id_or_fingerprint in item.root_finding_ids
        ]
        affected = set()
        for contract in self.contracts:
            if any(
                (
                    self._relevant_dataset(item.dataset, contract.required_datasets)
                    if item.dataset else item.universe in contract.required_universes
                ) for item in findings
            ):
                affected.add(contract.question_id)
            if any(item.rule_id in contract.required_relationships for item in reconciliations):
                affected.add(contract.question_id)
        return tuple(item.question_id for item in self.contracts if item.question_id in affected)

    def blockers_for_question(self, question_id: str) -> tuple[str, ...]:
        item = self.qualify_question(question_id)
        return () if item.qualification_status == QualificationStatus.VERIFIED.value else item.reason_codes

    def _qualify(self, contract: QuestionEvidenceContract, supplied: QuestionEvidenceInput) -> QuestionQualification:
        manifests = self._manifests(contract)
        batches = self._batches(contract)
        findings = self._findings(contract)
        reconciliations = self._reconciliations(contract)
        reconstructions = self._reconstructions(contract)
        manifest_keys = {_dataset_key(item.dataset) for item in manifests if item.record_count > 0}
        batch_keys = {_dataset_key(item.dataset) for item in batches if item.records}
        required_keys = {_dataset_key(item) for item in contract.required_datasets}
        direct_keys = manifest_keys | batch_keys
        substitutions = self._dataset_substitutions(contract, reconstructions, supplied, direct_keys)
        accepted_substitution_keys = {
            _dataset_key(item.requested_source)
            for item, accepted, _reason in substitutions
            if accepted
        }
        present_keys = direct_keys | accepted_substitution_keys
        missing_keys = sorted(required_keys - present_keys)
        actual_population = supplied.actual_population
        if actual_population is None:
            actual_population = sum(item.record_count for item in manifests)
            if not manifests:
                actual_population = sum(len(item.records) for item in batches)

        observed_fields = set(supplied.observed_fields)
        if not observed_fields and batches:
            for batch in batches:
                for record in batch.records:
                    observed_fields.update(str(key) for key in record)
        field_resolutions = resolve_contract_fields(
            contract.required_fields, supplied.field_resolutions, sorted(observed_fields),
        )
        field_evaluations = {
            item.required_field: evaluate_field_resolution(item)
            for item in field_resolutions
        }
        known_field_state = bool(observed_fields) or bool(supplied.field_resolutions)
        unsatisfied_fields = sorted(
            name for name, (ok, _reason) in field_evaluations.items() if not ok
        )
        missing_fields = unsatisfied_fields if known_field_state else []
        ungoverned_fields = sorted(
            item.required_field for item in field_resolutions
            if item.resolution_type != "UNAVAILABLE"
            and not field_evaluations[item.required_field][0]
        )

        integrity_states = tuple(sorted(set(item.status for item in findings))) or ("NO_RELEVANT_FINDINGS",)
        relationship_states = tuple(sorted(set(item.relationship_status for item in reconciliations)))
        if not contract.required_relationships:
            relationship_states = (RelationshipStatus.NOT_REQUIRED.value,)
        elif not relationship_states:
            relationship_states = ("NOT_ASSESSED",)

        ambiguous_integrity = [item for item in findings if item.status in _AMBIGUOUS_INTEGRITY]
        absent_integrity = [item for item in findings if item.status in _ABSENT_INTEGRITY]
        degraded_integrity = [item for item in findings if item.status in _DEGRADED_INTEGRITY]
        # Relevance gate: an upstream ambiguity may only block this question when
        # it is relevant to a required contract element.  NOT_REQUIRED never
        # blocks, and Wave 3's dependent_impact=False is respected unless the
        # contract explicitly proves the relationship still matters here.
        fatal_reconciliations = [item for item in reconciliations if _relationship_is_fatal(item, contract)]
        ambiguous_relationship = [
            item for item in fatal_reconciliations if item.relationship_status in _AMBIGUOUS_RELATIONSHIPS
        ]
        absent_relationship = [
            item for item in fatal_reconciliations if item.relationship_status in _ABSENT_RELATIONSHIPS
        ]
        degraded_relationship = [
            item for item in fatal_reconciliations if item.relationship_status in _DEGRADED_RELATIONSHIPS
        ]
        ignored_fingerprints = {item.fingerprint for item in fatal_reconciliations}
        ignored_relationship = [item for item in reconciliations if item.fingerprint not in ignored_fingerprints]

        structural = SufficiencyState.PASS
        if missing_keys or missing_fields or absent_integrity:
            structural = SufficiencyState.FAIL
        elif not required_keys:
            structural = SufficiencyState.UNKNOWN

        if supplied.population_complete is True:
            population = SufficiencyState.PASS
        elif supplied.population_complete is False:
            population = SufficiencyState.PARTIAL if actual_population > 0 else SufficiencyState.FAIL
        elif supplied.expected_population is not None:
            population = (
                SufficiencyState.PASS if actual_population >= supplied.expected_population
                else SufficiencyState.PARTIAL if actual_population > 0 else SufficiencyState.FAIL
            )
        else:
            population = SufficiencyState.UNKNOWN if actual_population > 0 else SufficiencyState.FAIL

        if supplied.observed_resolution:
            resolution = (
                SufficiencyState.PASS
                if supplied.observed_resolution.strip().lower() == contract.resolution.strip().lower()
                else SufficiencyState.PARTIAL if actual_population > 0 else SufficiencyState.FAIL
            )
        else:
            resolution = SufficiencyState.UNKNOWN if actual_population > 0 else SufficiencyState.FAIL

        if not contract.required_lineage:
            lineage = SufficiencyState.NOT_APPLICABLE
        else:
            available = set(supplied.available_lineage)
            if set(contract.required_lineage) <= available:
                lineage = SufficiencyState.PASS
            elif available:
                lineage = SufficiencyState.PARTIAL
            else:
                lineage = SufficiencyState.UNKNOWN if actual_population > 0 else SufficiencyState.FAIL

        if ambiguous_integrity:
            integrity = SufficiencyState.UNKNOWN
        elif absent_integrity:
            integrity = SufficiencyState.FAIL
        elif degraded_integrity:
            integrity = SufficiencyState.PARTIAL
        else:
            integrity = SufficiencyState.PASS if present_keys else SufficiencyState.FAIL

        if not contract.required_relationships:
            reconciliation = SufficiencyState.NOT_APPLICABLE
        elif ambiguous_relationship:
            reconciliation = SufficiencyState.UNKNOWN
        elif absent_relationship:
            reconciliation = SufficiencyState.FAIL
        elif degraded_relationship:
            reconciliation = SufficiencyState.PARTIAL
        elif reconciliations and all(item.relationship_status in {RelationshipStatus.RECONCILED.value, RelationshipStatus.NOT_REQUIRED.value} for item in reconciliations):
            reconciliation = SufficiencyState.PASS
        else:
            reconciliation = SufficiencyState.UNKNOWN

        # No self-certification: a requirement may only be satisfied by an
        # upstream authoritative source.  The question definition, the evidence
        # contract, or the Wave 4 result itself never counts as evidence.
        authorities = self._requirement_authorities(supplied)
        self_certified: list[str] = []
        if resolution is SufficiencyState.PASS and not requirement_authority_is_governed(
            "resolution", authorities["resolution"]
        ):
            resolution = SufficiencyState.UNKNOWN
            self_certified.append("SELF_CERTIFIED_RESOLUTION")
        if lineage is SufficiencyState.PASS and not requirement_authority_is_governed(
            "lineage", authorities["lineage"]
        ):
            lineage = SufficiencyState.UNKNOWN
            self_certified.append("SELF_CERTIFIED_LINEAGE")
        if integrity is SufficiencyState.PASS and not requirement_authority_is_governed(
            "integrity", authorities["integrity"]
        ):
            integrity = SufficiencyState.UNKNOWN
            self_certified.append("SELF_CERTIFIED_INTEGRITY")
        if reconciliation is SufficiencyState.PASS and not requirement_authority_is_governed(
            "reconciliation", authorities["reconciliation"]
        ):
            reconciliation = SufficiencyState.UNKNOWN
            self_certified.append("SELF_CERTIFIED_RECONCILIATION")

        statistical = supplied.statistical_state
        if contract.minimum_sample is not None and statistical is StatisticalState.UNKNOWN:
            if structural is SufficiencyState.PASS:
                statistical = StatisticalState.SUFFICIENT if actual_population >= contract.minimum_sample else StatisticalState.INSUFFICIENT
        if contract.minimum_sample is None and statistical is StatisticalState.UNKNOWN:
            statistical = StatisticalState.NOT_REQUIRED

        historical = tuple(dict.fromkeys(
            list(supplied.historical_limitations)
            + [item.observed_condition for item in findings if item.status == IntegrityStatus.HISTORICAL_LIMITATION.value]
            + [text for item in reconciliations for text in item.historical_limitations]
        ))
        reconstruction_notes = tuple(dict.fromkeys(
            f"{item.target_dataset}:{'EXACT' if _is_exact_reconstruction(item) else 'LOSSY'}:{item.reconstruction_rule}"
            for item in reconstructions
        ))
        relevant_lossy = [
            item for item in reconstructions
            if not _is_exact_reconstruction(item) and self._loss_is_relevant(contract, item)
        ]
        lossy_reconstruction = bool(relevant_lossy)
        reconstruction_reference = self._reconstruction_reference(reconstructions)

        # ---- historical record accounting and exhaustion ---------------------------
        evidence_accounting = self._evidence_accounting(
            contract, supplied, substitutions,
        )
        accounting = evidence_accounting
        accounting_resolved = bool(evidence_accounting.get("resolved"))
        accounting_balanced = (
            accounting_resolved
            and int(evidence_accounting.get("unexplained_records", 0) or 0) == 0
        )
        historical_authority = requirement_authority_is_governed(
            "historical_exhaustiveness", authorities["historical_exhaustiveness"]
        )
        exhaustion_status = str(evidence_accounting.get("historical_exhaustion_status", ""))
        future_data_requested = supplied.existing_state.upper() in {"INSUFFICIENT_DATA", "WAITING_DATA"}

        report_authority = self._report_authority(supplied, actual_population)
        ungoverned_substitutions = [
            item for item, _accepted, _reason in substitutions
            if not item.governed
        ]

        reasons: list[str] = []
        if ambiguous_integrity:
            reasons.append("INTEGRITY_AMBIGUITY")
        if ambiguous_relationship:
            reasons.append("RECONCILIATION_AMBIGUITY")
        if supplied.negative_result and supplied.negative_observable is not True:
            reasons.append("NEGATIVE_NOT_OBSERVABLE")
        reasons.extend(self_certified)
        if not accounting_resolved:
            reasons.append("HISTORICAL_RECORD_ACCOUNTING_INCOMPLETE")
        elif not accounting_balanced:
            reasons.append("UNEXPLAINED_EVIDENCE_RECORDS")
        elif not historical_authority:
            reasons.append("SELF_CERTIFIED_HISTORICAL_EXHAUSTIVENESS")
        if future_data_requested and not supports_future_data(exhaustion_status):
            reasons.append("FUTURE_DATA_REQUESTED_BEFORE_HISTORICAL_EXHAUSTION")
        if contract.definition_health in {"UNDER_SPECIFIED", "INVALID"}:
            reasons.append("CONTRACT_UNDER_SPECIFIED")
        if contract.definition_health == "SEMANTIC_MISMATCH":
            reasons.append("IMPLEMENTATION_SEMANTIC_MISMATCH")
        if contract.implementation == "NO_GOVERNED_RUNNER":
            reasons.append("IMPLEMENTATION_UNAVAILABLE")
        result_validity = str(report_authority.get("report_validity") or "")
        if result_validity == "INVALIDATED":
            reasons.append("CURRENT_RESULT_INVALIDATED")
        if ungoverned_fields:
            reasons.append("UNGOVERNED_FIELD_SUBSTITUTION")
        if ungoverned_substitutions:
            reasons.append("UNGOVERNED_DATASET_SUBSTITUTION")
        indeterminate = bool(reasons)
        if result_validity in {"MISSING", "LEGACY", "STALE", "UNKNOWN"}:
            reasons.append("CURRENT_RESULT_UNAVAILABLE")

        if missing_keys:
            reasons.append("ESSENTIAL_DATASET_ABSENT")
        if missing_fields:
            reasons.append("REQUIRED_FIELDS_ABSENT")
        if absent_integrity:
            reasons.append("ESSENTIAL_EVIDENCE_UNRECONSTRUCTABLE")
        if absent_relationship:
            reasons.append("REQUIRED_RELATIONSHIP_ABSENT")
        if population is SufficiencyState.FAIL:
            reasons.append("REQUIRED_POPULATION_ABSENT")
        if resolution is SufficiencyState.FAIL:
            reasons.append("REQUIRED_RESOLUTION_UNAVAILABLE")
        if lineage is SufficiencyState.FAIL:
            reasons.append("REQUIRED_LINEAGE_UNAVAILABLE")
        if reconciliation is SufficiencyState.FAIL:
            reasons.append("REQUIRED_RECONCILIATION_FAILED")
        unavailable = (
            structural is SufficiencyState.FAIL
            or population is SufficiencyState.FAIL
            or resolution is SufficiencyState.FAIL
            or lineage is SufficiencyState.FAIL
            or reconciliation is SufficiencyState.FAIL
            or contract.implementation == "NO_GOVERNED_RUNNER"
            or result_validity in {"MISSING", "LEGACY", "STALE", "UNKNOWN"}
        )

        partial = any(value is SufficiencyState.PARTIAL for value in (population, resolution, lineage)) or bool(supplied.unsupported_scope)
        degraded = bool(degraded_integrity or degraded_relationship or historical or lossy_reconstruction) or any(
            value is SufficiencyState.UNKNOWN for value in (population, resolution, lineage, reconciliation)
        )

        insufficient_result = supplied.existing_state.upper() in {"INSUFFICIENT_DATA", "WAITING_DATA"}
        # A direct insufficient-data result is still a verified outcome when the
        # collection pipeline is functional and the evidence is otherwise sound.
        # ``CURRENT_RESULT_UNAVAILABLE`` is only a current-state report validity
        # issue, not a reason to downgrade an otherwise valid insufficient-data
        # assertion.
        if insufficient_result and supplied.collection_functional is True and statistical is StatisticalState.INSUFFICIENT:
            unavailable = False
            degraded = False
            partial = False
        verified_insufficient = (
            insufficient_result
            and supplied.collection_functional is True
            and statistical is StatisticalState.INSUFFICIENT
            and not (indeterminate or unavailable or partial or degraded)
        )
        if statistical is StatisticalState.INSUFFICIENT and not verified_insufficient:
            reasons.append("STATISTICAL_INSUFFICIENCY")
            degraded = True
        if insufficient_result and supplied.collection_functional is False:
            unavailable = False
            reasons.append("COLLECTION_NOT_FUNCTIONAL")
            degraded = True

        if indeterminate:
            status = QualificationStatus.INDETERMINATE
        elif unavailable:
            status = QualificationStatus.UNAVAILABLE
        elif partial:
            status = QualificationStatus.PARTIAL
        elif degraded:
            status = QualificationStatus.DEGRADED
        elif verified_insufficient:
            status = QualificationStatus.VERIFIED
            reasons.append("VERIFIED_INSUFFICIENT_DATA")
        else:
            status = QualificationStatus.VERIFIED
            reasons.append("QUALIFIED")

        # Explicit mismatch states must outrank generic degradation.  A required
        # relationship that is ambiguous, absent, or degraded is an
        # indeterminate qualification even when the broader evidence set remains
        # otherwise complete.
        if ambiguous_relationship or absent_relationship:
            status = QualificationStatus.INDETERMINATE
        elif degraded_relationship and status == QualificationStatus.DEGRADED:
            status = QualificationStatus.DEGRADED

        # Ensure downstream gating and reporting consume the status enum as a
        # string, not an enum instance, while preserving the value in persisted
        # payloads for deterministic comparisons.
        status_value = status.value if isinstance(status, Enum) else str(status)

        if population is SufficiencyState.PARTIAL:
            reasons.append("POPULATION_SUBSET_ONLY")
        if resolution is SufficiencyState.PARTIAL:
            reasons.append("RESOLUTION_MISMATCH")
        if lineage is SufficiencyState.PARTIAL:
            reasons.append("LINEAGE_PARTIAL")
        if historical:
            reasons.append("HISTORICAL_LIMITATION")
        if lossy_reconstruction:
            reasons.append("LOSSY_RECONSTRUCTION")
        if degraded_integrity:
            reasons.append("INTEGRITY_WARNING")
        if degraded_relationship:
            reasons.append("RECONCILIATION_WARNING")
        reasons = list(dict.fromkeys(reasons))

        contract_scope = tuple(
            [f"universe:{item}" for item in contract.required_universes]
            + [f"dataset:{item}" for item in contract.required_datasets]
            + [f"relationship:{item}" for item in contract.required_relationships]
        )
        origins: dict[str, list[dict[str, Any]]] = {}

        def _origin(code: str, **kwargs: Any) -> None:
            origins.setdefault(code, []).append(kwargs)

        for code, records in (
            ("INTEGRITY_AMBIGUITY", ambiguous_integrity),
            ("ESSENTIAL_EVIDENCE_UNRECONSTRUCTABLE", absent_integrity),
            ("INTEGRITY_WARNING", degraded_integrity),
        ):
            for key, members in _bucket(
                records,
                lambda item: (item.status, str(item.dataset or ""), str(item.universe or "")),
            ):
                status, dataset, universe = key
                ids = [item.finding_id for item in members]
                _origin(
                    code,
                    origin_stage=OriginStage.WAVE2,
                    origin_type=OriginType.INTEGRITY_FINDING,
                    origin_id=ids[0] if len(ids) == 1 else f"wave2:{status}:{dataset or universe}",
                    dataset=dataset or None,
                    universe=universe or None,
                    affected_record_count=len(members),
                    evidence_references=_exact_references("wave2-finding", ids),
                    relevance_scope=(f"dataset:{dataset}" if dataset else f"universe:{universe}",),
                    required_by_contract=contract_scope,
                    affected_population=dataset or universe,
                    dependent_impact=None,
                )

        for code, records in (
            ("RECONCILIATION_AMBIGUITY", ambiguous_relationship),
            ("REQUIRED_RELATIONSHIP_ABSENT", absent_relationship),
            ("RECONCILIATION_WARNING", degraded_relationship),
        ):
            for key, members in _bucket(
                records, lambda item: (item.rule_id, item.relationship_status),
            ):
                rule_id, status = key
                ids = [item.fingerprint for item in members]
                _origin(
                    code,
                    origin_stage=OriginStage.WAVE3,
                    origin_type=OriginType.RECONCILIATION_RESULT,
                    origin_id=ids[0] if len(ids) == 1 else f"wave3:{rule_id}:{status}",
                    relationship=rule_id,
                    affected_record_count=len(members),
                    evidence_references=_exact_references("wave3-result", ids),
                    relevance_scope=(f"relationship:{rule_id}",),
                    required_by_contract=contract_scope,
                    affected_population=(
                        f"{members[0].source_universe}->{members[0].target_universe}"
                    ),
                    dependent_impact=members[0].dependent_impact,
                )

        for key, members in _bucket(
            relevant_lossy,
            lambda item: (item.target_dataset, item.reconstruction_rule, item.rule_version),
        ):
            dataset, rule, version = key
            _origin(
                "LOSSY_RECONSTRUCTION",
                origin_stage=OriginStage.WAVE2,
                origin_type=OriginType.RECONSTRUCTION,
                origin_id=f"wave2-reconstruction:{dataset}:{rule}:{version}",
                dataset=dataset,
                affected_record_count=len(members),
                evidence_references=(_aggregate_reference(
                    "reconstructions",
                    (
                        f"{item.target_dataset}:{item.reconstruction_rule}:"
                        f"{item.rule_version}:{_hash(item.source_identities)}"
                        for item in members
                    ),
                ),),
                relevance_scope=(f"dataset:{dataset}",),
                required_by_contract=contract_scope,
                affected_population=dataset,
                dependent_impact=None,
            )

        for name in missing_keys:
            _origin(
                "ESSENTIAL_DATASET_ABSENT",
                origin_stage=OriginStage.WAVE4_REQUIREMENT,
                origin_type=OriginType.EVIDENCE_REQUIREMENT,
                origin_id=f"contract:{contract.contract_fingerprint}#dataset:{name}",
                dataset=name,
                evidence_references=(f"dataset:{name}",),
                relevance_scope=(f"dataset:{name}",),
                required_by_contract=contract_scope,
                affected_population=name,
            )
        for name in missing_fields:
            _origin(
                "REQUIRED_FIELDS_ABSENT",
                origin_stage=OriginStage.WAVE4_REQUIREMENT,
                origin_type=OriginType.FIELD_RESOLUTION,
                origin_id=f"contract:{contract.contract_fingerprint}#field:{name}",
                required_field=name,
                evidence_references=(f"field:{name}",),
                relevance_scope=(f"field:{name}",),
                required_by_contract=contract_scope,
                affected_population=name,
            )
        for name in ungoverned_fields:
            _origin(
                "UNGOVERNED_FIELD_SUBSTITUTION",
                origin_stage=OriginStage.WAVE4_REQUIREMENT,
                origin_type=OriginType.FIELD_RESOLUTION,
                origin_id=f"contract:{contract.contract_fingerprint}#field:{name}",
                required_field=name,
                evidence_references=(f"field-resolution:{name}",),
                relevance_scope=(f"field:{name}",),
                required_by_contract=contract_scope,
                affected_population=name,
            )
        for item in ungoverned_substitutions:
            _origin(
                "UNGOVERNED_DATASET_SUBSTITUTION",
                origin_stage=OriginStage.WAVE4_REQUIREMENT,
                origin_type=OriginType.DATASET_SUBSTITUTION,
                origin_id=f"contract:{contract.contract_fingerprint}#substitution:{item.requested_source}",
                dataset=item.requested_source,
                evidence_references=(f"dataset-substitution:{item.requested_source}",),
                relevance_scope=(f"dataset:{item.requested_source}",),
                required_by_contract=contract_scope,
                affected_population=item.actual_source,
            )

        answer_reference = (
            f"{contract.question_id}:{report_authority.get('report_fingerprint') or 'none'}"
        )
        for code in ("CURRENT_RESULT_INVALIDATED", "CURRENT_RESULT_UNAVAILABLE"):
            _origin(
                code,
                origin_stage=OriginStage.REPORT_VALIDITY,
                origin_type=OriginType.REPORT_AUTHORITY,
                origin_id=(
                    report_authority.get("report_path")
                    or report_authority.get("report_fingerprint")
                    or f"answer:{answer_reference}"
                ),
                evidence_references=(f"answer:{answer_reference}",),
                required_by_contract=(f"epoch:{contract.time_horizon}",),
                affected_population=str(report_authority.get("result_availability") or ""),
            )

        accounting_reference = f"record-accounting:{contract.question_id}:{_hash(evidence_accounting)}"
        for code, origin_type in (
            ("HISTORICAL_RECORD_ACCOUNTING_INCOMPLETE", OriginType.RECORD_ACCOUNTING),
            ("UNEXPLAINED_EVIDENCE_RECORDS", OriginType.RECORD_ACCOUNTING),
            ("SELF_CERTIFIED_HISTORICAL_EXHAUSTIVENESS", OriginType.RECORD_ACCOUNTING),
            ("FUTURE_DATA_REQUESTED_BEFORE_HISTORICAL_EXHAUSTION", OriginType.RECORD_ACCOUNTING),
        ):
            _origin(
                code,
                origin_stage=OriginStage.HISTORICAL_ACCOUNTING,
                origin_type=origin_type,
                origin_id=accounting_reference,
                evidence_references=(accounting_reference,),
                required_by_contract=contract_scope,
                affected_population=str(
                    evidence_accounting.get("historical_exhaustion_status") or ""
                ),
                affected_record_count=int(evidence_accounting.get("unexplained_records", 0) or 0),
            )

        for code in reasons:
            if code in origins:
                continue
            if code.startswith("SELF_CERTIFIED_"):
                stage, origin_type = OriginStage.WAVE4_REQUIREMENT, OriginType.EVIDENCE_REQUIREMENT
            elif code == "STATISTICAL_INSUFFICIENCY" or code.startswith("VERIFIED_INSUFFICIENT"):
                stage, origin_type = (
                    OriginStage.STATISTICAL_SUFFICIENCY, OriginType.STATISTICAL_ASSESSMENT,
                )
            elif code.startswith("IMPLEMENTATION_") or code == "CONTRACT_UNDER_SPECIFIED":
                stage, origin_type = (
                    OriginStage.IMPLEMENTATION, OriginType.IMPLEMENTATION_BINDING,
                )
            else:
                stage, origin_type = OriginStage.WAVE4_REQUIREMENT, OriginType.EVIDENCE_REQUIREMENT
            _origin(
                code,
                origin_stage=stage,
                origin_type=origin_type,
                origin_id=f"contract:{contract.contract_fingerprint}#{code}",
                evidence_references=(f"contract:{contract.contract_fingerprint}",),
                required_by_contract=contract_scope,
                affected_population=contract.population,
            )

        blockers = deduplicate_blockers([
            make_blocker(
                question_id=contract.question_id,
                reason_code=code,
                fatal_to_question=True,
                **entry,
            )
            for code in reasons
            if code not in _NON_BLOCKER_REASONS
            for entry in origins.get(code, ())
        ])
        blocker_ids = tuple(item.blocker_id for item in blockers)
        blocker_provenance: dict[str, tuple[str, ...]] = {
            code: tuple(item.blocker_id for item in blockers if item.reason_code == code)
            for code in reasons
        }
        for code in reasons:
            if code not in _NON_BLOCKER_REASONS and not blocker_provenance[code]:
                raise QuestionContractError(
                    f"{contract.question_id}: reason {code} persisted without blocker provenance"
                )





        evidence_refs = list(supplied.evidence_references)
        evidence_refs.extend(item.fingerprint for item in manifests)
        if findings:
            evidence_refs.append(_aggregate_reference(
                "integrity-findings", (item.finding_id for item in findings),
            ))
        if reconciliations:
            evidence_refs.append(_aggregate_reference(
                "reconciliation-results", (item.fingerprint for item in reconciliations),
            ))
        if reconstructions:
            evidence_refs.append(reconstruction_reference)
        for item, _accepted, _reason in substitutions:
            evidence_refs.append(
                f"dataset-substitution:{item.requested_source}:{item.substitution_authority or 'NONE'}"
            )
        evidence_refs.append(f"report-authority:{contract.question_id}:{report_authority.get('report_fingerprint') or 'none'}")
        evidence_refs = list(dict.fromkeys(evidence_refs))
        evidence_scope = dict(supplied.evidence_scope)
        if not evidence_scope:
            evidence_scope = {
                "manifest_scopes": [dict(item.scope) for item in manifests],
                "observation_windows": [
                    {"start": item.observation_start, "end": item.observation_end} for item in manifests
                ],
                "record_count": actual_population,
                "datasets": sorted(present_keys),
            }
        unsupported = dict(supplied.unsupported_scope)
        if missing_keys:
            unsupported.setdefault("missing_datasets", missing_keys)
        if missing_fields:
            unsupported.setdefault("missing_fields", missing_fields)
        supported = dict(supplied.supported_scope)
        if not supported and actual_population > 0:
            supported = {"scope": "observed evidence only", "record_count": actual_population}

        assessment = SufficiencyAssessment(
            structural=structural.value,
            population=population.value,
            resolution=resolution.value,
            lineage=lineage.value,
            integrity=integrity.value,
            reconciliation=reconciliation.value,
            statistical=statistical.value,
        )
        explanation = (
            f"{status_value}: " + ", ".join(reasons)
            + f"; observed={actual_population}; required datasets={list(contract.required_datasets)}"
        )
        raw_scientific_state = str(supplied.existing_state or "").upper()
        scientific_state = (
            raw_scientific_state
            if raw_scientific_state in _GOVERNED_SCIENTIFIC_STATES
            else "UNCLASSIFIED"
        )
        if (
            scientific_state in EVIDENCE_COLLECTING_SCIENTIFIC_STATES
            and not supports_future_data(exhaustion_status)
        ):
            scientific_state = "UNCLASSIFIED"
        gate_decision = _admit_downstream(
            question_id=contract.question_id,
            assurance_state=status_value,
            scientific_state=scientific_state,
        )
        downstream_gate = {
            "assurance_state": status_value,
            "scientific_state": scientific_state,
            "admitted": gate_decision.admitted,
            "consumable_as_scientific_truth": gate_decision.scientific_truth_consumable,
            "permitted_hypothesis_classes": tuple(gate_decision.permitted_hypothesis_classes),
            "reason": gate_decision.reason,
        }
        semantic = {
            "question_fingerprint": contract.question_fingerprint,
            "contract_fingerprint": contract.contract_fingerprint,
            "existing_state": supplied.existing_state,
            "existing_result": supplied.existing_result,
            "status": status_value,
            "sufficiency": asdict(assessment),
            "integrity": integrity_states,
            "reconciliation": relationship_states,
            "historical": historical,
            "reconstruction": reconstruction_notes,
            "supported_scope": supported,
            "unsupported_scope": unsupported,
            "reasons": reasons,
            "evidence_references": evidence_refs,
            "blocker_provenance": blocker_provenance,
            "record_accounting": accounting,
            "evidence_accounting": evidence_accounting,
            "blockers": [item.to_dict() for item in blockers],
            "field_resolutions": [item.to_dict() for item in field_resolutions],
            "dataset_substitutions": [item.to_dict() for item, _a, _r in substitutions],
            "report_authority": report_authority,
            "downstream_gate": downstream_gate,
            "requirement_authorities": dict(sorted(authorities.items())),
            "relationship_relevance_ignored": tuple(
                item.fingerprint for item in ignored_relationship
            ),
        }
        return QuestionQualification(
            question_id=contract.question_id,
            question_fingerprint=contract.question_fingerprint,
            contract_fingerprint=contract.contract_fingerprint,
            existing_research_state=supplied.existing_state or "NOT_SUPPLIED",
            existing_research_result=supplied.existing_result,
            qualification_status=status_value,
            phenomenon=contract.phenomenon,
            required_population=contract.population,
            required_resolution=contract.resolution,
            required_universes=contract.required_universes,
            required_relationships=contract.required_relationships,
            required_lineage=contract.required_lineage,
            actual_population=actual_population,
            evidence_scope=evidence_scope,
            sufficiency=assessment,
            integrity_state=integrity_states,
            reconciliation_state=relationship_states,
            historical_limitations=historical,
            reconstruction_involvement=reconstruction_notes,
            supported_scope=supported,
            unsupported_scope=unsupported,
            reason_codes=tuple(reasons),
            explanation=explanation,
            evidence_references=tuple(evidence_refs),
            blocker_ids=tuple(blocker_ids),
            blocker_provenance=blocker_provenance,
            record_accounting=evidence_accounting,
            blockers=blockers,
            evidence_accounting=evidence_accounting,
            field_resolutions=field_resolutions,
            dataset_substitutions=tuple(item for item, _accepted, _reason in substitutions),
            report_authority=report_authority,
            downstream_gate=downstream_gate,
            qualification_fingerprint=_hash(semantic),
        )


def describe_question_qualification() -> dict[str, Any]:
    contracts = build_question_evidence_contracts()
    return {
        "schema": QUALIFICATION_SCHEMA_VERSION,
        "question_authority": "research_engine.registry.research_question_registry.REGISTRY",
        "question_count": len(contracts),
        "statuses": [item.value for item in QualificationStatus],
        "precedence": [
            QualificationStatus.INDETERMINATE.value,
            QualificationStatus.UNAVAILABLE.value,
            QualificationStatus.PARTIAL.value,
            QualificationStatus.DEGRADED.value,
            QualificationStatus.VERIFIED.value,
        ],
        "contracts": [item.to_dict() for item in contracts],
    }


__all__ = [
    "CANONICAL_QUESTION_COUNT",
    "QUALIFICATION_SCHEMA_VERSION",
    "QualificationEngine",
    "QualificationStatus",
    "AssuranceContradiction",
    "QuestionContractError",
    "QuestionEvidenceContract",
    "QuestionEvidenceInput",
    "QuestionQualification",
    "QuestionQualificationReport",
    "StatisticalState",
    "SufficiencyAssessment",
    "SufficiencyState",
    "build_question_evidence_contracts",
    "describe_question_qualification",
    "validate_question_evidence_contracts",
    "universes_for_dataset",
]
