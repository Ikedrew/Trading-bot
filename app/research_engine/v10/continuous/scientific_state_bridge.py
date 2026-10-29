"""Authoritative bridge from canonical question results to scientific state.

This module derives no thresholds and invents no interventions.  Findings
require an explicit scientific signal from a runner; candidates require an
exact policy already present in the governed policy catalogue.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import hashlib
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json, fingerprint
from research_engine.lifecycle.treatment_memory_store import TreatmentMemoryStore
from research_engine.lifecycle.treatment_equivalence import assess_treatment_equivalence
from research_engine.lifecycle.treatment_memory import (
    ChangeDirection,
    Horizon,
    TreatmentClass,
    TreatmentComponent,
    TreatmentSignature,
)
from research_engine.lifecycle.research_opportunity import OpportunitySubjectKind
from research_engine.registry.exit_policy_adjudication import CANDIDATE_POLICIES_V1
from research_engine.v10.continuous.question_cycle_state import (
    CanonicalQuestionCycleResult,
    CanonicalQuestionResult,
    DEFAULT_QUESTION_CYCLE_DIRECTORY,
    QuestionCycleStore,
)
from research_engine.v10.continuous.scientific_state_store import (
    BRIDGE_RUN_SCHEMA,
    DEFAULT_SCIENTIFIC_STATE_DIRECTORY,
    ScientificStateStore,
    ScientificStateStoreError,
)
from research_engine.v10.optimisation.models import (
    OptimisationCandidate,
    ResearchHypothesis,
    ValidationPlan,
    classify_change_risk,
)
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry


COMPLETE = "COMPLETE"
NEGATIVE_RESULT = "NEGATIVE_RESULT"
NO_SCIENTIFIC_STATE_CHANGE = "NO_SCIENTIFIC_STATE_CHANGE"
COMPLETED = "COMPLETED"
COMPLETED_WITH_REVIEW = "COMPLETED_WITH_REVIEW_REQUIRED"
COMPLETED_WITH_ITEM_FAILURES = "COMPLETED_WITH_ITEM_FAILURES"

_TERMINAL_CYCLE_STATUSES = {"COMPLETED", "COMPLETED_WITH_QUESTION_FAILURES"}
_LIVE_CANDIDATE_STATUSES = {
    "VALIDATED", "FORWARD_VALIDATED", "SHADOW_VALIDATION_ACTIVE",
    "SHADOW_VALIDATED", "READY_FOR_PROMOTION_REVIEW", "ACCEPTED",
}
_MUTABLE_RESEARCH_CANDIDATE_STATUSES = {"PROPOSED", "READY_FOR_TEST", "TESTING"}
_SIGNAL_CLASSIFICATIONS = {
    "SUPPORTED", "REJECTED", "NEGATIVE_RESULT", "NULL_RESULT",
    "EARLY_FAILURE", "PROMISING", "SCIENTIFICALLY_MEANINGFUL",
}
_ACTION_VALUES = {
    "FINDING_STRENGTHENED", "FINDING_WEAKENED", "FINDING_AMENDED",
    "FINDING_SUPERSEDED", "FINDING_INVALIDATED", "NO_FINDING_CHANGE",
}


class ScientificStateBridgeError(RuntimeError):
    """System-level failure; no bridge success may be reported."""


@dataclass(frozen=True)
class ScientificStateBridgeResult:
    bridge_run_id: str
    snapshot_id: str
    source_cycle_id: str
    question_changes_processed: tuple[str, ...]
    findings_created: tuple[str, ...] = ()
    findings_strengthened: tuple[str, ...] = ()
    findings_weakened: tuple[str, ...] = ()
    findings_amended: tuple[str, ...] = ()
    findings_invalidated: tuple[str, ...] = ()
    hypotheses_created: tuple[str, ...] = ()
    hypotheses_updated: tuple[str, ...] = ()
    hypotheses_invalidated: tuple[str, ...] = ()
    candidates_created: tuple[str, ...] = ()
    candidates_updated: tuple[str, ...] = ()
    candidate_design_required: tuple[str, ...] = ()
    duplicate_equivalent_treatments_suppressed: tuple[str, ...] = ()
    review_required: tuple[str, ...] = ()
    governance_signals: tuple[dict[str, Any], ...] = ()
    failures: tuple[dict[str, Any], ...] = ()
    status: str = COMPLETED
    started_at: str = ""
    completed_at: str = ""
    scientific_state_path: str = ""
    optimisation_registry_path: str = ""
    validation_handoff: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"run_schema": BRIDGE_RUN_SCHEMA, **asdict(self)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ScientificStateBridgeResult":
        if value.get("run_schema") != BRIDGE_RUN_SCHEMA:
            raise ScientificStateBridgeError("BRIDGE_RUN_SCHEMA_INVALID")
        fields = dict(value)
        fields.pop("run_schema", None)
        for name in (
            "question_changes_processed", "findings_created", "findings_strengthened",
            "findings_weakened", "findings_amended", "findings_invalidated",
            "hypotheses_created", "hypotheses_updated", "hypotheses_invalidated",
            "candidates_created", "candidates_updated", "candidate_design_required",
            "duplicate_equivalent_treatments_suppressed", "review_required",
            "governance_signals", "failures", "validation_handoff",
        ):
            fields[name] = tuple(fields.get(name) or ())
        return cls(**fields)


def _value(source: Any, name: str, default: Any = None) -> Any:
    return source.get(name, default) if isinstance(source, Mapping) else getattr(source, name, default)


def _normalise_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _conclusion(result: CanonicalQuestionResult) -> str:
    answer = result.substantive_answer
    if isinstance(answer, Mapping):
        for key in ("scientific_conclusion", "conclusion", "answer", "recommendation", "outcome"):
            if answer.get(key) not in (None, ""):
                return str(answer[key])
        return ""
    return str(answer or "")


def _metric(result: CanonicalQuestionResult, *names: str) -> Any:
    for name in names:
        if name in result.key_metrics and result.key_metrics[name] is not None:
            return result.key_metrics[name]
    if isinstance(result.substantive_answer, Mapping):
        for name in names:
            if result.substantive_answer.get(name) is not None:
                return result.substantive_answer[name]
    return None


def _explicit_scientific_signal(result: CanonicalQuestionResult) -> tuple[bool, str]:
    """Accept only a runner-declared signal or governed statistical decision."""
    if result.status not in {COMPLETE, NEGATIVE_RESULT} or not _conclusion(result):
        return False, "QUESTION_NOT_SCIENTIFICALLY_RESOLVED"
    explicit = _metric(result, "scientifically_meaningful")
    if explicit is True:
        return True, "RUNNER_DECLARED_MEANINGFUL"
    classification = str(_metric(
        result, "finding_classification", "scientific_classification", "decision_status"
    ) or "").upper()
    if classification in _SIGNAL_CLASSIFICATIONS:
        return True, "GOVERNED_CLASSIFICATION:" + classification
    statistical = result.statistical_output if isinstance(result.statistical_output, Mapping) else {}
    p_value = statistical.get("p_value")
    alpha = statistical.get("alpha")
    if isinstance(p_value, (int, float)) and isinstance(alpha, (int, float)) and p_value <= alpha:
        return True, "GOVERNED_SIGNIFICANCE_DECISION"
    ci = statistical.get("confidence_interval") or _metric(result, "confidence_interval")
    if isinstance(ci, Sequence) and not isinstance(ci, (str, bytes)) and len(ci) == 2:
        low, high = ci
        if isinstance(low, (int, float)) and isinstance(high, (int, float)) and (low > 0 or high < 0):
            return True, "CONFIDENCE_INTERVAL_EXCLUDES_NULL"
    return False, "NO_GOVERNED_GENERIC_SCIENTIFIC_THRESHOLD"


def _population(result: CanonicalQuestionResult) -> dict[str, Any]:
    value = _metric(result, "population", "population_filters", "target_population")
    return dict(value) if isinstance(value, Mapping) else {}


def _effect_direction(result: CanonicalQuestionResult) -> str:
    explicit = str(_metric(result, "effect_direction") or "").upper()
    if explicit:
        return explicit
    value = _metric(
        result, "effect_size", "expectancy_delta", "expectancy_r", "expectancy", "delta"
    )
    if isinstance(value, (int, float)):
        return "POSITIVE" if value > 0 else "NEGATIVE" if value < 0 else "NULL"
    return "UNKNOWN"


def _effect_size(result: CanonicalQuestionResult) -> Any:
    return _metric(
        result, "effect_size", "expectancy_delta", "expectancy_r", "expectancy", "delta"
    )


def _finding_identity(result: CanonicalQuestionResult) -> str:
    supplied = _metric(result, "source_finding_id", "finding_id")
    if supplied:
        return str(supplied)
    material = {
        "question_id": result.question_id,
        # The topic/population is the durable proposition lineage.  Its wording,
        # effect direction and support may change across versions without
        # minting a disconnected finding.
        "scientific_topic": str(_metric(
            result, "scientific_proposition_id", "finding_topic", "claim_key"
        ) or result.question_id),
        "population": _population(result),
    }
    return "F-B3-" + fingerprint(material)[:24].upper()


def _finding_action(previous: Mapping[str, Any] | None, result: CanonicalQuestionResult) -> str:
    explicit = str(_metric(result, "finding_action", "scientific_change") or "").upper()
    if explicit in _ACTION_VALUES:
        return explicit
    if previous is None:
        return "NEW_FINDING"
    old_direction = str(previous.get("effect_direction") or "UNKNOWN")
    direction = _effect_direction(result)
    if direction != old_direction and {direction, old_direction} <= {"POSITIVE", "NEGATIVE", "NULL"}:
        return "FINDING_INVALIDATED"
    if _normalise_text(previous.get("proposition")) != _normalise_text(_conclusion(result)):
        return "FINDING_AMENDED"
    old_confidence = str(previous.get("uncertainty") or "").upper()
    new_confidence = str(result.confidence or "").upper()
    ranks = {"": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
    if ranks.get(new_confidence, 0) > ranks.get(old_confidence, 0):
        return "FINDING_STRENGTHENED"
    if ranks.get(new_confidence, 0) < ranks.get(old_confidence, 0):
        return "FINDING_WEAKENED"
    # A sample-size-only or metric-only drift is deliberately not scientific change.
    return "NO_FINDING_CHANGE"


def _finding_record(
    finding_id: str, version: int, result: CanonicalQuestionResult,
    action: str, predecessor: Mapping[str, Any] | None,
) -> dict[str, Any]:
    now = result.evaluated_at
    return {
        "finding_id": finding_id,
        "finding_version": version,
        "source_question_ids": [result.question_id],
        "source_question_result_ids": [result.result_id],
        "snapshot_id": result.snapshot_id,
        "investigation_epoch": result.investigation_epoch,
        "proposition": _conclusion(result),
        "population": _population(result),
        "effect_direction": _effect_direction(result),
        "effect_size": _effect_size(result),
        "relevant_metrics": dict(result.key_metrics),
        "sample_n": result.sample_n,
        "uncertainty": result.confidence,
        "statistical_output": result.statistical_output,
        "evidence_references": list(result.evidence_references),
        "limitations": list(result.limitations),
        "status": action.removeprefix("FINDING_"),
        "predecessor_finding_version": (
            None if predecessor is None
            else f"{finding_id}:v{predecessor['finding_version']}"
        ),
        "created_at": now if predecessor is None else predecessor["created_at"],
        "updated_at": now,
        "lineage": {
            "question_result_id": result.result_id,
            "snapshot_fingerprint": result.snapshot_fingerprint,
            "runner": result.runner,
            "runner_version": result.runner_version,
        },
        "invalidation_or_supersession_reason": (
            str(_metric(result, "invalidation_reason", "supersession_reason") or "")
            if action in {"FINDING_INVALIDATED", "FINDING_SUPERSEDED"} else ""
        ),
    }


def _hypothesis_id(finding_id: str) -> str:
    return "HYP-B3-" + fingerprint({"finding_id": finding_id})[:24].upper()


def _candidate_id(hypothesis_id: str, policy_id: str, population: Mapping[str, Any]) -> str:
    return "OPT-B3-" + fingerprint({
        "hypothesis_id": hypothesis_id, "policy_id": policy_id, "population": population,
    })[:24].upper()


def _treatment_hash(policy: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(policy)).encode("utf-8")).hexdigest()


def _treatment_signature(
    question_id: str, policy: Mapping[str, Any], result: CanonicalQuestionResult,
) -> TreatmentSignature:
    policy_type = str(policy.get("policy_type") or "")
    if policy_type == "TRAILING":
        component = TreatmentComponent.STOP_GEOMETRY
        parameters = {
            "THRESHOLD": policy["activation_r"],
            "STOP_MULTIPLIER": policy["distance_r"],
        }
        direction = ChangeDirection.REPLACE
        treatment_class = TreatmentClass.GEOMETRY_MODIFICATION
    elif policy_type == "REDUCED_TP":
        component = TreatmentComponent.TARGET_GEOMETRY
        parameters = {"TARGET_MULTIPLIER": policy["target_cap_r"]}
        direction = ChangeDirection.NARROW
        treatment_class = TreatmentClass.GEOMETRY_MODIFICATION
    elif policy_type == "TIME_CAP":
        component = TreatmentComponent.TIMING
        parameters = {"MAX_HOLD_BARS": policy["bar_cap"]}
        direction = ChangeDirection.DECREASE
        treatment_class = TreatmentClass.CONDITIONING
    else:
        raise ScientificStateBridgeError(
            "GOVERNED_POLICY_NOT_REPRESENTABLE_IN_TREATMENT_MEMORY:" + policy_type)
    return TreatmentSignature.create(
        subject_kind=OpportunitySubjectKind.CANONICAL_QUESTION,
        subject_ref=question_id,
        component=component,
        change_parameters=parameters,
        direction=direction,
        baseline_semantics=str(_metric(result, "baseline_policy_id") or "CURRENT_POLICY"),
        alternative_semantics=str(policy["policy_id"]),
        treatment_class=treatment_class,
        horizon=Horizon.SCALP,
        label=str(policy["policy_id"]),
        provenance={"question_result_id": result.result_id},
        created_at=result.evaluated_at,
    )


def _load_cycle(
    cycle_input: Any, store: QuestionCycleStore,
) -> tuple[CanonicalQuestionCycleResult, dict[str, Any]]:
    if isinstance(cycle_input, CanonicalQuestionCycleResult):
        supplied = cycle_input
    elif isinstance(cycle_input, Mapping):
        supplied = CanonicalQuestionCycleResult.from_dict(cycle_input)
    elif isinstance(cycle_input, str):
        supplied = store.load_cycle(cycle_input)
        if supplied is None:
            raise ScientificStateBridgeError("SOURCE_CYCLE_NOT_FOUND:" + cycle_input)
    else:
        raise ScientificStateBridgeError("INVALID_SOURCE_CYCLE_TYPE")
    if supplied.cycle_status not in _TERMINAL_CYCLE_STATUSES or supplied.total_questions != 70:
        raise ScientificStateBridgeError("INVALID_SOURCE_CYCLE")
    persisted = store.load_cycle(supplied.cycle_id)
    if persisted is None or persisted.to_dict() != supplied.to_dict():
        raise ScientificStateBridgeError("SOURCE_CYCLE_IDENTITY_UNVERIFIED")
    projection = store.load_projection(supplied.cycle_id)
    if projection.get("cycle_snapshot_id") != supplied.snapshot_id:
        raise ScientificStateBridgeError("SOURCE_CYCLE_PROJECTION_MISMATCH")
    questions = projection.get("questions")
    if not isinstance(questions, Mapping) or len(questions) != 70:
        raise ScientificStateBridgeError("SOURCE_CYCLE_QUESTION_ACCOUNTING_INVALID")
    return supplied, projection


def _changed_results(
    cycle: CanonicalQuestionCycleResult, projection: Mapping[str, Any],
    store: QuestionCycleStore,
) -> dict[str, CanonicalQuestionResult]:
    results: dict[str, CanonicalQuestionResult] = {}
    for qid in cycle.changed_question_ids:
        entry = projection["questions"].get(qid)
        if not isinstance(entry, Mapping) or not isinstance(entry.get("result"), Mapping):
            raise ScientificStateBridgeError("MISSING_IMMUTABLE_QUESTION_RESULT:" + qid)
        result = CanonicalQuestionResult.from_dict(entry["result"])
        # The projection's governed evaluation identity selects the exact
        # immutable artifact.  A same-snapshot result published under a
        # different (or absent) evaluator identity is a different immutable
        # result and is never an acceptable substitute.
        immutable_result = store.load_question_result(
            qid, cycle.snapshot_id,
            result.evaluation_identity_digest or None,
        )
        if immutable_result is None or immutable_result.to_dict() != result.to_dict():
            raise ScientificStateBridgeError("MISSING_IMMUTABLE_QUESTION_RESULT:" + qid)
        if (immutable_result.evaluation_identity_digest
                != result.evaluation_identity_digest):
            raise ScientificStateBridgeError(
                "QUESTION_EVALUATION_IDENTITY_MISMATCH:" + qid)
        if result.result_id != cycle.result_ids.get(qid):
            raise ScientificStateBridgeError("QUESTION_RESULT_IDENTITY_MISMATCH:" + qid)
        if result.snapshot_id != cycle.snapshot_id:
            raise ScientificStateBridgeError("QUESTION_RESULT_SNAPSHOT_MISMATCH:" + qid)
        results[qid] = result
    return results


def _restore_registry_file(path: Path, original: bytes | None) -> None:
    if original is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".rollback.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _known_treatment(
    policy_id: str, treatment_hash: str, population: Mapping[str, Any],
    signature: TreatmentSignature, registry: OptimisationRegistry,
) -> OptimisationCandidate | None:
    for candidate in registry.list_candidates():
        equivalent = False
        stored_signature = candidate.provenance.get("treatment_signature")
        if isinstance(stored_signature, Mapping):
            try:
                equivalent = assess_treatment_equivalence(
                    signature, TreatmentSignature.from_dict(stored_signature)).deduplicates
            except Exception as exc:
                raise ScientificStateBridgeError(
                    "AUTHORITATIVE_TREATMENT_SIGNATURE_INVALID:" + candidate.candidate_id
                ) from exc
        if equivalent or candidate.policy_id == policy_id or (
            candidate.treatment_hash and candidate.treatment_hash == treatment_hash
        ):
            known_population = candidate.target_population or candidate.provenance.get("target_population", {})
            if not known_population or dict(known_population) == dict(population):
                return candidate
    return None


def _memory_assessment(
    policy_id: str, treatment_hash: str, snapshot_id: str,
    signature: TreatmentSignature,
    treatment_store: TreatmentMemoryStore,
) -> dict[str, Any]:
    """Consult Wave 7 immutable memory without pretending it grants execution."""
    if hasattr(treatment_store, "memories_for_signature"):
        matching = list(treatment_store.memories_for_signature(signature.signature_identity))
    else:
        matching = [
            memory for memory in treatment_store.memories()
            if policy_id in canonical_json(memory.to_dict())
            or treatment_hash in canonical_json(memory.to_dict())
        ]
    if not matching:
        return {"decision": "NO_APPLICABLE_MEMORY", "memory_ids": []}
    decisions = [
        decision for decision in treatment_store.decisions()
        if set(decision.memory_identities) & {row.memory_identity for row in matching}
    ]
    permitted = [row for row in decisions if row.was_permitted()]
    if permitted:
        return {
            "decision": "REVISIT_PERMITTED",
            "memory_ids": [row.memory_identity for row in matching],
            "decision_ids": [row.decision_identity for row in permitted],
            "snapshot_id": snapshot_id,
        }
    if any(row.is_evidence_bearing() for row in matching):
        return {
            "decision": "DUPLICATE_OR_REJECTED_TREATMENT",
            "memory_ids": [row.memory_identity for row in matching],
        }
    return {
        "decision": "NON_SUPPRESSING_INCOMPLETE_MEMORY",
        "memory_ids": [row.memory_identity for row in matching],
    }


def _validation_plan(
    candidate: OptimisationCandidate, result: CanonicalQuestionResult,
) -> ValidationPlan:
    criteria = _metric(result, "validation_criteria")
    criteria = dict(criteria) if isinstance(criteria, Mapping) else {}
    metrics = list(criteria.get("primary_metrics") or [])
    metrics += [item for item in criteria.get("secondary_metrics", []) if item not in metrics]
    minimum = result.minimum_required_n or result.sample_n or 0
    return ValidationPlan(
        candidate_id=candidate.candidate_id,
        baseline_id=candidate.baseline_id,
        created_at=result.evaluated_at,
        metrics=metrics,
        target_questions=[result.question_id],
        regression_questions=list(criteria.get("robustness_slices") or []),
        success_conditions=dict(criteria.get("success_conditions") or {}),
        failure_conditions=dict(criteria.get("failure_conditions") or {}),
        minimum_sample=int(criteria.get("required_sample") or minimum),
        notes=(
            "Frozen from governed question output."
            if criteria else
            "REVIEW_REQUIRED: no governed validation thresholds were supplied; none were invented."
        ),
    )


def _reconcile_opt_dp1_002(
    document: dict[str, Any], registry: OptimisationRegistry,
) -> tuple[str | None, dict[str, Any] | None]:
    hypothesis = registry.get_hypothesis("HYP-DP1-002")
    candidate = registry.get_candidate("OPT-DP1-002")
    if not hypothesis or not candidate or hypothesis.source_finding != "F-DP1-006":
        return None, None
    before = {
        "candidate_id": candidate.candidate_id,
        "hypothesis_id": hypothesis.hypothesis_id,
        "finding_id": hypothesis.source_finding,
        "treatment_hash": candidate.treatment_hash,
        "status": candidate.status,
        "shadow_binding": dict(candidate.shadow_binding),
    }
    document["reconciled_external_lineage"][candidate.candidate_id] = before
    return candidate.candidate_id, before


def run_scientific_state_bridge(
    question_cycle_result: Any,
    *,
    question_state_directory: Path | str = DEFAULT_QUESTION_CYCLE_DIRECTORY,
    scientific_state_directory: Path | str = DEFAULT_SCIENTIFIC_STATE_DIRECTORY,
    optimisation_registry_directory: Path | str = "data/research/optimisation",
    treatment_memory_path: Path | str | None = None,
    question_store: QuestionCycleStore | None = None,
    scientific_store: ScientificStateStore | None = None,
    optimisation_registry: OptimisationRegistry | None = None,
    treatment_store: TreatmentMemoryStore | None = None,
    policy_catalog: Sequence[Mapping[str, Any]] = CANDIDATE_POLICIES_V1,
    persistence_hook: Callable[[str], None] | None = None,
) -> ScientificStateBridgeResult:
    """Reconcile one immutable Block 2 cycle into governed scientific state."""
    qstore = question_store or QuestionCycleStore(question_state_directory)
    cycle, projection = _load_cycle(question_cycle_result, qstore)
    changed_results = _changed_results(cycle, projection, qstore)
    bridge_run_id = "SBRIDGE-" + fingerprint({
        "cycle_id": cycle.cycle_id,
        "snapshot_id": cycle.snapshot_id,
        "result_ids": {qid: cycle.result_ids[qid] for qid in sorted(changed_results)},
    })[:32].upper()
    sstore = scientific_store or ScientificStateStore(scientific_state_directory)
    existing = sstore.load_run(bridge_run_id)
    if existing is not None:
        return ScientificStateBridgeResult.from_dict(existing)

    registry = optimisation_registry or OptimisationRegistry(str(optimisation_registry_directory))
    if optimisation_registry is None:
        try:
            registry.load()
        except Exception as exc:
            raise ScientificStateBridgeError(
                f"AUTHORITATIVE_OPTIMISATION_REGISTRY_INVALID:{type(exc).__name__}:{exc}"
            ) from exc
    try:
        memory = treatment_store or TreatmentMemoryStore(treatment_memory_path)
    except Exception as exc:
        raise ScientificStateBridgeError(
            f"TREATMENT_MEMORY_CORRUPT:{type(exc).__name__}:{exc}"
        ) from exc
    policies = {str(row.get("policy_id")): dict(row) for row in policy_catalog}
    if len(policies) != len(tuple(policy_catalog)) or "" in policies:
        raise ScientificStateBridgeError("GOVERNED_POLICY_CATALOG_INVALID")

    document = sstore.document
    created: list[str] = []
    strengthened: list[str] = []
    weakened: list[str] = []
    amended: list[str] = []
    invalidated: list[str] = []
    hypotheses_created: list[str] = []
    hypotheses_updated: list[str] = []
    hypotheses_invalidated: list[str] = []
    candidates_created: list[str] = []
    candidates_updated: list[str] = []
    designs: list[str] = []
    suppressed: list[str] = []
    reviews: list[str] = []
    governance: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    handoff: list[dict[str, Any]] = []

    reconciled_id, reconciled_before = _reconcile_opt_dp1_002(document, registry)

    for qid in sorted(changed_results):
        result = changed_results[qid]
        item_document = deepcopy(document)
        item_hypotheses = deepcopy(registry._hypotheses)
        item_candidates = deepcopy(registry._candidates)
        item_plans = deepcopy(registry._plans)
        try:
            meaningful, reason = _explicit_scientific_signal(result)
            if not meaningful:
                if result.status in {COMPLETE, NEGATIVE_RESULT} and _conclusion(result):
                    reviews.append(qid + ":FINDING_DERIVATION:" + reason)
                continue
            finding_id = _finding_identity(result)
            history = document["findings"].setdefault(finding_id, [])
            previous = history[-1] if history else None
            action = _finding_action(previous, result)
            if action == "NO_FINDING_CHANGE":
                continue
            finding = _finding_record(finding_id, len(history) + 1, result, action, previous)
            history.append(finding)
            finding_ref = f"{finding_id}:v{finding['finding_version']}"
            document["current"]["findings"][finding_id] = finding_ref
            if action == "NEW_FINDING":
                created.append(finding_ref)
            elif action == "FINDING_STRENGTHENED":
                strengthened.append(finding_ref)
            elif action == "FINDING_WEAKENED":
                weakened.append(finding_ref)
            elif action in {"FINDING_AMENDED", "FINDING_SUPERSEDED"}:
                amended.append(finding_ref)
            elif action == "FINDING_INVALIDATED":
                invalidated.append(finding_ref)

            hypothesis_id = _hypothesis_id(finding_id)
            # Reconcile an explicitly named pre-existing hypothesis rather than minting a duplicate.
            supplied_hypothesis = _metric(result, "source_hypothesis_id", "hypothesis_id")
            if supplied_hypothesis and registry.get_hypothesis(str(supplied_hypothesis)):
                hypothesis_id = str(supplied_hypothesis)
            existing_hypothesis = registry.get_hypothesis(hypothesis_id)
            if existing_hypothesis is not None and existing_hypothesis.source_finding != finding_id:
                raise ScientificStateBridgeError(
                    "HYPOTHESIS_IDENTITY_COLLISION:" + hypothesis_id)
            mechanism = str(_metric(result, "mechanism") or "")
            if action == "FINDING_INVALIDATED":
                if existing_hypothesis:
                    existing_hypothesis.status = "INVALIDATED_UPSTREAM"
                    existing_hypothesis.version += 1
                    existing_hypothesis.history.append({
                        "version": existing_hypothesis.version,
                        "status": existing_hypothesis.status,
                        "finding_version": finding_ref,
                        "snapshot_id": cycle.snapshot_id,
                    })
                    hypotheses_invalidated.append(hypothesis_id)
                else:
                    reviews.append(finding_ref + ":NO_DEPENDENT_HYPOTHESIS_TO_INVALIDATE")
                    continue
            elif existing_hypothesis is None:
                hypothesis = ResearchHypothesis(
                    hypothesis_id=hypothesis_id,
                    source_finding=finding_id,
                    source_question=qid,
                    domain=qid.split("-")[0],
                    created_at=result.evaluated_at,
                    statement=_conclusion(result),
                    target_component=str(_metric(result, "target_component") or "Unknown"),
                    expected_effect=str(_metric(result, "expected_effect") or _effect_direction(result)),
                    confidence=str(result.confidence or "LOW"),
                    evidence_strength=str(_metric(result, "evidence_strength") or reason),
                    status="PROPOSED",
                    hypothesis_type=("MECHANISTIC_HYPOTHESIS" if mechanism else "OBSERVATIONAL_HYPOTHESIS"),
                    mechanism=mechanism,
                    mechanism_unknown=not bool(mechanism),
                    target_population=_population(result),
                    falsification_criteria=list(_metric(result, "falsification_criteria") or []),
                    required_evidence=list(result.evidence_datasets),
                    source_finding_versions=[finding_ref],
                    source_question_results=[result.result_id],
                    evidence_lineage={"snapshot_id": cycle.snapshot_id, "result_id": result.result_id},
                    history=[{"version": 1, "status": "PROPOSED", "finding_version": finding_ref}],
                )
                registry.add_hypothesis(hypothesis)
                hypotheses_created.append(hypothesis_id)
                if not mechanism:
                    reviews.append(hypothesis_id + ":MECHANISM_UNKNOWN")
            else:
                existing_hypothesis.version += 1
                existing_hypothesis.source_finding_versions.append(finding_ref)
                existing_hypothesis.source_question_results.append(result.result_id)
                existing_hypothesis.confidence = str(result.confidence or existing_hypothesis.confidence)
                if action == "FINDING_WEAKENED":
                    existing_hypothesis.status = "EVIDENCE_WEAKENED_REVIEW_REQUIRED"
                elif action in {"FINDING_AMENDED", "FINDING_SUPERSEDED"}:
                    existing_hypothesis.status = "AMENDED_REVIEW_REQUIRED"
                existing_hypothesis.history.append({
                    "version": existing_hypothesis.version,
                    "status": existing_hypothesis.status,
                    "finding_version": finding_ref,
                    "snapshot_id": cycle.snapshot_id,
                })
                hypotheses_updated.append(hypothesis_id)

            document["hypothesis_history"].setdefault(hypothesis_id, []).append({
                "hypothesis_id": hypothesis_id,
                "finding_version": finding_ref,
                "action": action,
                "snapshot_id": cycle.snapshot_id,
                "result_id": result.result_id,
            })
            document["current"]["hypotheses"][hypothesis_id] = finding_ref
            deps = document["dependencies"].setdefault(finding_id, {
                "hypotheses": [], "candidates": [],
            })
            if hypothesis_id not in deps["hypotheses"]:
                deps["hypotheses"].append(hypothesis_id)

            # Upstream deterioration propagates before any candidate design.
            if action in {"FINDING_WEAKENED", "FINDING_INVALIDATED"}:
                for candidate_id in list(deps["candidates"]):
                    candidate = registry.get_candidate(candidate_id)
                    if candidate is None:
                        raise ScientificStateBridgeError(
                            "DEPENDENCY_GRAPH_CANDIDATE_MISSING:" + candidate_id)
                    if candidate.status in _LIVE_CANDIDATE_STATUSES:
                        governance.append({
                            "candidate_id": candidate_id,
                            "signal": "UPSTREAM_" + action,
                            "action": "GOVERNANCE_REVIEW_REQUIRED_NO_RUNTIME_MUTATION",
                        })
                    elif candidate.status in _MUTABLE_RESEARCH_CANDIDATE_STATUSES:
                        if action == "FINDING_INVALIDATED":
                            candidate.status = "BLOCKED_UPSTREAM_INVALIDATED"
                        candidate.provenance["evidence_state"] = action
                        candidate.status_history.append({
                            "status": candidate.status,
                            "timestamp": result.evaluated_at,
                            "reason": action,
                        })
                        candidates_updated.append(candidate_id)
                if action == "FINDING_INVALIDATED":
                    continue

            policy_id = str(_metric(result, "governed_policy_id", "policy_id") or "")
            if not policy_id:
                designs.append(hypothesis_id)
                reviews.append(hypothesis_id + ":CANDIDATE_DESIGN_REQUIRED")
                continue
            policy = policies.get(policy_id)
            if policy is None:
                designs.append(hypothesis_id)
                reviews.append(hypothesis_id + ":UNGOVERNED_POLICY_ID:" + policy_id)
                continue
            supplied_parameters = _metric(result, "governed_policy_parameters", "treatment_parameters")
            if supplied_parameters is not None and dict(supplied_parameters) != policy:
                raise ScientificStateBridgeError("GOVERNED_POLICY_PARAMETERS_MISMATCH:" + policy_id)
            treatment_hash = _treatment_hash(policy)
            signature = _treatment_signature(qid, policy, result)
            known = _known_treatment(
                policy_id, treatment_hash, _population(result), signature, registry)
            if known is not None:
                suppressed.append(known.candidate_id)
                if known.candidate_id not in deps["candidates"]:
                    deps["candidates"].append(known.candidate_id)
                document["current"]["candidates"][known.candidate_id] = "EXTERNAL_OPTIMISATION_REGISTRY"
                continue
            memory_decision = _memory_assessment(
                policy_id, treatment_hash, cycle.snapshot_id, signature, memory)
            if memory_decision["decision"] == "DUPLICATE_OR_REJECTED_TREATMENT":
                suppressed.append(policy_id + ":TREATMENT_MEMORY")
                reviews.append(policy_id + ":REVISIT_NOT_AUTHORISED")
                continue
            candidate_id = _candidate_id(hypothesis_id, policy_id, _population(result))
            existing_candidate = registry.get_candidate(candidate_id)
            if existing_candidate is not None:
                if (
                    existing_candidate.hypothesis_id != hypothesis_id
                    or existing_candidate.policy_id != policy_id
                    or existing_candidate.treatment_hash != treatment_hash
                ):
                    raise ScientificStateBridgeError(
                        "DUPLICATE_CONFLICTING_CANDIDATE_IDENTITY:" + candidate_id)
                suppressed.append(candidate_id)
                continue
            criteria = _metric(result, "validation_criteria")
            if not isinstance(criteria, Mapping):
                reviews.append(candidate_id + ":VALIDATION_CRITERIA_REVIEW_REQUIRED")
            candidate = OptimisationCandidate(
                candidate_id=candidate_id,
                hypothesis_id=hypothesis_id,
                baseline_id=str(_metric(result, "baseline_id") or cycle.snapshot_id),
                created_at=result.evaluated_at,
                component=str(_metric(result, "target_component") or "Unknown"),
                changes={"policy_id": policy_id, "frozen_policy": policy},
                expected_outcome=str(_metric(result, "expected_effect") or _effect_direction(result)),
                risk_level=classify_change_risk(policy),
                status="PROPOSED",
                notes="Block 3 proposal only; validation and runtime activation are forbidden.",
                policy_id=policy_id,
                treatment_hash=treatment_hash,
                source_finding_versions=[finding_ref],
                source_question_results=[result.result_id],
                target_population=_population(result),
                validation_objective=str(_metric(result, "validation_objective") or _conclusion(result)),
                validation_requirements=dict(criteria or {}),
                limitations=[str(item) for item in result.limitations],
                provenance={
                    "source_cycle_id": cycle.cycle_id,
                    "snapshot_id": cycle.snapshot_id,
                    "investigation_epoch": cycle.investigation_epoch,
                    "finding_id": finding_id,
                    "finding_version": finding_ref,
                    "question_id": qid,
                    "question_result_id": result.result_id,
                    "policy_authority": (
                        "research_engine.registry.exit_policy_adjudication.CANDIDATE_POLICIES_V1"
                    ),
                    "treatment_memory": memory_decision,
                    "treatment_signature": signature.to_dict(),
                    "target_population": _population(result),
                },
            )
            plan = _validation_plan(candidate, result)
            registry.add_candidate(candidate)
            registry.add_plan(plan)
            candidates_created.append(candidate_id)
            deps["candidates"].append(candidate_id)
            document["current"]["candidates"][candidate_id] = finding_ref
            document["candidate_history"].setdefault(candidate_id, []).append({
                "candidate_id": candidate_id,
                "status": "PROPOSED",
                "finding_version": finding_ref,
                "hypothesis_id": hypothesis_id,
                "question_result_id": result.result_id,
                "snapshot_id": cycle.snapshot_id,
                "policy_id": policy_id,
                "treatment_hash": treatment_hash,
            })
            handoff.append({
                "candidate_id": candidate_id,
                "status": "PROPOSED",
                "policy_id": policy_id,
                "treatment_hash": treatment_hash,
                "validation_plan": plan.to_dict(),
                "source_finding_version": finding_ref,
                "source_hypothesis_id": hypothesis_id,
                "source_question_result_id": result.result_id,
            })
        except ScientificStateBridgeError:
            raise
        except Exception as exc:
            document = item_document
            registry._hypotheses = item_hypotheses
            registry._candidates = item_candidates
            registry._plans = item_plans
            failures.append({
                "question_id": qid,
                "category": type(exc).__name__,
                "reason": str(exc),
            })

    timestamp = cycle.completed_at
    material_change = any((
        created, strengthened, weakened, amended, invalidated,
        hypotheses_created, hypotheses_updated, hypotheses_invalidated,
        candidates_created, candidates_updated, designs, governance,
    ))
    status = (
        COMPLETED_WITH_ITEM_FAILURES if failures
        else NO_SCIENTIFIC_STATE_CHANGE if not material_change
        else COMPLETED_WITH_REVIEW if reviews else COMPLETED
    )
    result = ScientificStateBridgeResult(
        bridge_run_id=bridge_run_id,
        snapshot_id=cycle.snapshot_id,
        source_cycle_id=cycle.cycle_id,
        question_changes_processed=tuple(sorted(changed_results)),
        findings_created=tuple(created),
        findings_strengthened=tuple(strengthened),
        findings_weakened=tuple(weakened),
        findings_amended=tuple(amended),
        findings_invalidated=tuple(invalidated),
        hypotheses_created=tuple(hypotheses_created),
        hypotheses_updated=tuple(hypotheses_updated),
        hypotheses_invalidated=tuple(hypotheses_invalidated),
        candidates_created=tuple(candidates_created),
        candidates_updated=tuple(candidates_updated),
        candidate_design_required=tuple(designs),
        duplicate_equivalent_treatments_suppressed=tuple(sorted(set(suppressed))),
        review_required=tuple(reviews),
        governance_signals=tuple(governance),
        failures=tuple(failures),
        status=status,
        started_at=timestamp,
        completed_at=timestamp,
        scientific_state_path=str(sstore.state_path),
        optimisation_registry_path=str(registry._dir / "registry.json"),
        validation_handoff=tuple(handoff),
    )

    registry_path = registry._dir / "registry.json"
    original_registry = registry_path.read_bytes() if registry_path.exists() else None
    try:
        if persistence_hook:
            persistence_hook("before_registry")
        registry.save()
        if persistence_hook:
            persistence_hook("before_scientific_state")
        sstore.commit(document, result.to_dict())
    except Exception as exc:
        try:
            _restore_registry_file(registry_path, original_registry)
        except Exception as restore_exc:
            raise ScientificStateBridgeError(
                f"BRIDGE_PERSISTENCE_AND_REGISTRY_RECOVERY_FAILED:{exc}:{restore_exc}"
            ) from restore_exc
        raise ScientificStateBridgeError(
            f"BRIDGE_PERSISTENCE_FAILED:{type(exc).__name__}:{exc}"
        ) from exc

    # Historical consistency invariant: reconciliation may never mutate live binding.
    if reconciled_id and reconciled_before:
        after = registry.get_candidate(reconciled_id)
        if after is None or {
            "treatment_hash": after.treatment_hash,
            "status": after.status,
            "shadow_binding": dict(after.shadow_binding),
        } != {
            "treatment_hash": reconciled_before["treatment_hash"],
            "status": reconciled_before["status"],
            "shadow_binding": reconciled_before["shadow_binding"],
        }:
            raise ScientificStateBridgeError("OPT_DP1_002_RUNTIME_STATE_MUTATED")
    return result


__all__ = [
    "ScientificStateBridgeError",
    "ScientificStateBridgeResult",
    "run_scientific_state_bridge",
]
