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
from dataclasses import asdict, dataclass, field
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


QUALIFICATION_SCHEMA_VERSION = 1
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
    qualification_fingerprint: str
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
    schema: int = QUALIFICATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "question_authority": "research_engine.registry.research_question_registry.REGISTRY",
            "question_count": len(self.qualifications),
            "contract_set_fingerprint": self.contract_set_fingerprint,
            "counts": dict(self.counts),
            "blocker_counts": dict(self.blocker_counts),
            "contradictions": [_native(asdict(item)) for item in self.contradictions],
            "qualifications": [item.to_dict() for item in self.qualifications],
            "report_fingerprint": self.report_fingerprint,
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
        return tuple(item for item in self.integrity_report.manifests if self._relevant_dataset(item.dataset, contract.required_datasets))

    def _batches(self, contract: QuestionEvidenceContract) -> tuple[EvidenceBatch, ...]:
        return tuple(item for item in self.batches if self._relevant_dataset(item.dataset, contract.required_datasets))

    def _findings(self, contract: QuestionEvidenceContract) -> tuple[IntegrityFinding, ...]:
        universes = set(contract.required_universes)
        return tuple(
            item for item in self.integrity_report.findings
            if (
                self._relevant_dataset(item.dataset, contract.required_datasets)
                if item.dataset else item.universe in universes
            )
        )

    def _reconciliations(self, contract: QuestionEvidenceContract) -> tuple[ReconciliationResult, ...]:
        required = set(contract.required_relationships)
        return tuple(item for item in self.reconciliation_report.results if item.rule_id in required)

    def _reconstructions(self, contract: QuestionEvidenceContract) -> tuple[ReconstructedArtifact, ...]:
        return tuple(
            item for item in self.integrity_report.reconstructions
            if self._relevant_dataset(item.target_dataset, contract.required_datasets)
        )

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
        blocker_counts = Counter(code for item in values for code in item.reason_codes if code != "QUALIFIED")
        contract_set_fp = _hash([item.contract_fingerprint for item in self.contracts])
        semantic = {
            "contract_set_fingerprint": contract_set_fp,
            "qualifications": [item.to_dict() for item in values],
            "counts": dict(sorted(counts.items())),
            "blocker_counts": dict(sorted(blocker_counts.items())),
            "contradictions": [_native(asdict(item)) for item in contradictions],
        }
        return QuestionQualificationReport(
            qualifications=values,
            contradictions=contradictions,
            counts=dict(sorted(counts.items())),
            blocker_counts=dict(sorted(blocker_counts.items())),
            contract_set_fingerprint=contract_set_fp,
            report_fingerprint=_hash(semantic),
        )

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
        exact_reconstruction_keys = {
            _dataset_key(item.target_dataset) for item in reconstructions if item.exact and item.source_complete
        }
        required_keys = {_dataset_key(item) for item in contract.required_datasets}
        present_keys = manifest_keys | batch_keys | exact_reconstruction_keys
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
        missing_fields = sorted(set(contract.required_fields) - observed_fields) if observed_fields else []

        integrity_states = tuple(sorted(set(item.status for item in findings))) or ("NO_RELEVANT_FINDINGS",)
        relationship_states = tuple(sorted(set(item.relationship_status for item in reconciliations)))
        if not contract.required_relationships:
            relationship_states = (RelationshipStatus.NOT_REQUIRED.value,)
        elif not relationship_states:
            relationship_states = ("NOT_ASSESSED",)

        ambiguous_integrity = [item for item in findings if item.status in _AMBIGUOUS_INTEGRITY]
        absent_integrity = [item for item in findings if item.status in _ABSENT_INTEGRITY]
        degraded_integrity = [item for item in findings if item.status in _DEGRADED_INTEGRITY]
        ambiguous_relationship = [item for item in reconciliations if item.relationship_status in _AMBIGUOUS_RELATIONSHIPS]
        absent_relationship = [item for item in reconciliations if item.relationship_status in _ABSENT_RELATIONSHIPS]
        degraded_relationship = [item for item in reconciliations if item.relationship_status in _DEGRADED_RELATIONSHIPS]

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
        reconstruction_notes = tuple(
            f"{item.target_dataset}:{'EXACT' if item.exact and item.source_complete else 'LOSSY'}:{item.reconstruction_rule}"
            for item in reconstructions
        )
        lossy_reconstruction = any(not item.exact or not item.source_complete or item.information_loss for item in reconstructions)

        reasons: list[str] = []
        blocker_ids: list[str] = []
        if ambiguous_integrity:
            reasons.append("INTEGRITY_AMBIGUITY")
            blocker_ids.extend(item.finding_id for item in ambiguous_integrity)
        if ambiguous_relationship:
            reasons.append("RECONCILIATION_AMBIGUITY")
            blocker_ids.extend(item.fingerprint for item in ambiguous_relationship)
        if supplied.negative_result and supplied.negative_observable is not True:
            reasons.append("NEGATIVE_NOT_OBSERVABLE")
        if contract.definition_health in {"UNDER_SPECIFIED", "INVALID"}:
            reasons.append("CONTRACT_UNDER_SPECIFIED")
        if contract.definition_health == "SEMANTIC_MISMATCH":
            reasons.append("IMPLEMENTATION_SEMANTIC_MISMATCH")
        if contract.implementation == "NO_GOVERNED_RUNNER":
            reasons.append("IMPLEMENTATION_UNAVAILABLE")
        result_validity = ""
        if isinstance(supplied.existing_result, Mapping):
            result_validity = str(supplied.existing_result.get("report_validity") or "").upper()
        if result_validity == "INVALIDATED":
            reasons.append("CURRENT_RESULT_INVALIDATED")
        indeterminate = bool(reasons)
        if result_validity in {"MISSING", "LEGACY", "STALE", "UNKNOWN"}:
            reasons.append("CURRENT_RESULT_UNAVAILABLE")

        if missing_keys:
            reasons.append("ESSENTIAL_DATASET_ABSENT")
        if missing_fields:
            reasons.append("REQUIRED_FIELDS_ABSENT")
        if absent_integrity:
            reasons.append("ESSENTIAL_EVIDENCE_UNRECONSTRUCTABLE")
            blocker_ids.extend(item.finding_id for item in absent_integrity)
        if absent_relationship:
            reasons.append("REQUIRED_RELATIONSHIP_ABSENT")
            blocker_ids.extend(item.fingerprint for item in absent_relationship)
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
        else:
            status = QualificationStatus.VERIFIED
            reasons.append("VERIFIED_INSUFFICIENT_DATA" if verified_insufficient else "QUALIFIED")

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
            blocker_ids.extend(item.finding_id for item in degraded_integrity)
        if degraded_relationship:
            reasons.append("RECONCILIATION_WARNING")
            blocker_ids.extend(item.fingerprint for item in degraded_relationship)
        reasons = list(dict.fromkeys(reasons))
        blocker_ids = list(dict.fromkeys(blocker_ids))

        evidence_refs = list(supplied.evidence_references)
        evidence_refs.extend(item.fingerprint for item in manifests)
        evidence_refs.extend(item.finding_id for item in findings)
        evidence_refs.extend(item.fingerprint for item in reconciliations)
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
            f"{status.value}: " + ", ".join(reasons)
            + f"; observed={actual_population}; required datasets={list(contract.required_datasets)}"
        )
        semantic = {
            "question_fingerprint": contract.question_fingerprint,
            "contract_fingerprint": contract.contract_fingerprint,
            "existing_state": supplied.existing_state,
            "existing_result": supplied.existing_result,
            "status": status.value,
            "sufficiency": asdict(assessment),
            "integrity": integrity_states,
            "reconciliation": relationship_states,
            "historical": historical,
            "reconstruction": reconstruction_notes,
            "supported_scope": supported,
            "unsupported_scope": unsupported,
            "reasons": reasons,
            "evidence_references": evidence_refs,
        }
        return QuestionQualification(
            question_id=contract.question_id,
            question_fingerprint=contract.question_fingerprint,
            contract_fingerprint=contract.contract_fingerprint,
            existing_research_state=supplied.existing_state or "NOT_SUPPLIED",
            existing_research_result=supplied.existing_result,
            qualification_status=status.value,
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
