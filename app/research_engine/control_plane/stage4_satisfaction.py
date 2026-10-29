"""Persisted, content-bound Stage 4 satisfaction decisions.

Dataset snapshots answer *which immutable population was inspected*.  This
module answers the separate question *did that population satisfy this exact
observation requirement under this exact threshold policy?*  Decisions are
derived, immutable, fingerprinted, and revalidated on every read.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from research_engine.control_plane import stage4_dataset_snapshot as D
from research_engine.control_plane import stage4_identity as I
from research_engine.control_plane import stage4_observation_thresholds as T


ROOT = Path(__file__).resolve().parents[2]
STAMP = "20260929"
STATE_PATH = (
    ROOT / "research_engine" / "control_plane" /
    "stage4_satisfaction_decision_state.json"
)
ASSURANCE_JSON_PATH = (
    ROOT / "analysis" / "assurance" /
    f"stage4_satisfaction_decisions_{STAMP}.json"
)
ASSURANCE_MD_PATH = ASSURANCE_JSON_PATH.with_suffix(".md")

STORE_SCHEMA = 1
POLICY_VERSION = "1"
EVALUATOR_VERSION = "stage4_satisfaction_evaluator_v1"

SATISFIED = "SATISFIED"
NOT_SATISFIED = "NOT_SATISFIED"
INDETERMINATE = "INDETERMINATE"
INVALID = "INVALID"
DECISION_STATES = frozenset({SATISFIED, NOT_SATISFIED, INDETERMINATE, INVALID})

RESULT_PASSED = "PASSED"
RESULT_FAILED = "FAILED"
RESULT_MISSING = "MISSING"
RESULT_INVALID = "INVALID"
RESULT_STATES = frozenset({
    RESULT_PASSED, RESULT_FAILED, RESULT_MISSING, RESULT_INVALID,
})

EVIDENCE_VERIFIED = "VERIFIED"
EVIDENCE_MISSING = "MISSING"
EVIDENCE_LEGACY_UNRESOLVED = "LEGACY_UNRESOLVED"

# OR-13's historical shapes have disjoint closed vocabularies.  OR-08, OR-14,
# and OR-15 explicitly require future collection.  Audit-boundary snapshots
# therefore cannot be used to manufacture satisfaction for these contracts.
FUTURE_ONLY_REQUIREMENTS = frozenset({"OR-08", "OR-13", "OR-14", "OR-15"})


class SatisfactionDecisionError(RuntimeError):
    """A governed satisfaction invariant failed closed."""


def _canonical(value: Any) -> str:
    return I.canonical_json(value)


def _fingerprint(value: Any) -> str:
    return I.fingerprint(value)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


@dataclass(frozen=True)
class ThresholdPolicy:
    """Immutable requirement-specific threshold policy."""

    threshold_policy_id: str
    threshold_policy_version: str
    observation_requirement_id: str
    requirement_fingerprint: str
    classification: str
    authority: str
    source: str
    rules_json: tuple[str, ...]

    @classmethod
    def create(
        cls, *, observation_requirement_id: str,
        rules: Sequence[Mapping[str, Any]], authority: str,
        source: str, classification: str = T.THRESHOLD_GOVERNED,
        version: str = POLICY_VERSION,
    ) -> "ThresholdPolicy":
        rid = I.validate_requirement_id(observation_requirement_id)
        if not str(version or "").strip():
            raise SatisfactionDecisionError("MISSING_THRESHOLD_POLICY_VERSION")
        if not str(authority or "").strip() or not str(source or "").strip():
            raise SatisfactionDecisionError("MISSING_THRESHOLD_POLICY_AUTHORITY")
        requirement_fp = I.requirement_authority().semantic_fingerprint(rid)
        normalized: list[str] = []
        for number, raw in enumerate(rules, 1):
            if not isinstance(raw, Mapping):
                raise SatisfactionDecisionError("THRESHOLD_RULE_NOT_AN_OBJECT")
            rule = dict(raw)
            rule.setdefault("threshold_id", f"{rid}-TH-{number:03d}")
            if not str(rule.get("field") or ""):
                raise SatisfactionDecisionError("THRESHOLD_RULE_WITHOUT_FIELD")
            if str(rule.get("operator") or "") not in {
                    ">=", ">", "<=", "<", "==", "!="}:
                raise SatisfactionDecisionError("UNKNOWN_THRESHOLD_COMPARATOR")
            if "value" not in rule:
                raise SatisfactionDecisionError("THRESHOLD_RULE_WITHOUT_VALUE")
            normalized.append(_canonical(rule))
        material = {
            "observation_requirement_id": rid,
            "requirement_fingerprint": requirement_fp,
            "threshold_policy_version": str(version),
            "classification": str(classification),
            "authority": str(authority),
            "source": str(source),
            "rules": [json.loads(item) for item in normalized],
        }
        return cls(
            threshold_policy_id="TPOL-" + _fingerprint(material)[:24].upper(),
            threshold_policy_version=str(version),
            observation_requirement_id=rid,
            requirement_fingerprint=requirement_fp,
            classification=str(classification), authority=str(authority),
            source=str(source), rules_json=tuple(normalized),
        )

    @property
    def rules(self) -> tuple[dict[str, Any], ...]:
        return tuple(json.loads(item) for item in self.rules_json)

    def identity_material(self) -> dict[str, Any]:
        return {
            "observation_requirement_id": self.observation_requirement_id,
            "requirement_fingerprint": self.requirement_fingerprint,
            "threshold_policy_version": self.threshold_policy_version,
            "classification": self.classification,
            "authority": self.authority,
            "source": self.source,
            "rules": list(self.rules),
        }

    def to_dict(self) -> dict[str, Any]:
        return {"threshold_policy_id": self.threshold_policy_id,
                **self.identity_material()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ThresholdPolicy":
        policy = cls.create(
            observation_requirement_id=str(
                value.get("observation_requirement_id") or ""),
            rules=value.get("rules") or (),
            authority=str(value.get("authority") or ""),
            source=str(value.get("source") or ""),
            classification=str(value.get("classification") or ""),
            version=str(value.get("threshold_policy_version") or ""),
        )
        if policy.threshold_policy_id != value.get("threshold_policy_id"):
            raise SatisfactionDecisionError("THRESHOLD_POLICY_ID_MISMATCH")
        if policy.requirement_fingerprint != value.get("requirement_fingerprint"):
            raise SatisfactionDecisionError("THRESHOLD_REQUIREMENT_MISMATCH")
        return policy


def threshold_policy_for_requirement(
        requirement_id: str,
        authority: I.RequirementAuthority | None = None) -> ThresholdPolicy:
    """Consolidate existing Stage 4 threshold machinery into one OR policy."""
    resolved_authority = authority or I.requirement_authority()
    rid = I.validate_requirement_id(requirement_id, authority=resolved_authority)
    requirement = resolved_authority.require(rid)
    if rid in T.OR_CONTRACT_THRESHOLDS:
        resolved = T.resolve_observation_requirement_threshold(rid)
        rules = []
        for number, item in enumerate(resolved["rules"], 1):
            rule = dict(item)
            rule["threshold_id"] = f"{rid}-CONTRACT-{number:03d}"
            rules.append(rule)
        return ThresholdPolicy.create(
            observation_requirement_id=rid, rules=rules,
            authority=str(resolved["authority"]),
            source=str(resolved["threshold_source"]),
            classification=str(resolved["classification"]),
        )

    rules: list[dict[str, Any]] = []
    authorities: set[str] = set()
    sources: set[str] = set()
    classifications: set[str] = set()
    for question_id in sorted(set(requirement.get("questions") or ())):
        resolved = T.resolve_threshold(str(question_id))
        authorities.add(str(resolved.get("authority") or ""))
        sources.add(str(resolved.get("threshold_source") or ""))
        classifications.add(str(resolved.get("classification") or ""))
        for number, item in enumerate(resolved.get("rules") or (), 1):
            rule = dict(item)
            rule["question_id"] = str(question_id)
            rule["threshold_id"] = f"{question_id}-TH-{number:03d}"
            rules.append(rule)
    classification = (T.THRESHOLD_GOVERNED if classifications == {
        T.THRESHOLD_GOVERNED} and rules else
        T.METHOD_THRESHOLD_DEFINITION_REQUIRED)
    return ThresholdPolicy.create(
        observation_requirement_id=rid, rules=rules,
        authority=" + ".join(sorted(authorities)) or "UNRESOLVED",
        source=" + ".join(sorted(sources)) or "UNRESOLVED",
        classification=classification,
    )


@dataclass(frozen=True)
class ThresholdResult:
    threshold_id: str
    metric_key: str
    required_value: Any
    observed_value: Any
    comparator: str
    result_state: str
    passed: bool | None
    evidence_reference: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.result_state not in RESULT_STATES:
            raise SatisfactionDecisionError("UNKNOWN_THRESHOLD_RESULT_STATE")
        expected = ({RESULT_PASSED: True, RESULT_FAILED: False,
                     RESULT_MISSING: None, RESULT_INVALID: None}
                    [self.result_state])
        if self.passed is not expected:
            raise SatisfactionDecisionError("THRESHOLD_RESULT_BOOLEAN_MISMATCH")

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold_id": self.threshold_id,
            "metric_key": self.metric_key,
            "required_value": self.required_value,
            "observed_value": self.observed_value,
            "comparator": self.comparator,
            "result_state": self.result_state,
            "passed": self.passed,
            "evidence_reference": list(self.evidence_reference),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ThresholdResult":
        return cls(
            threshold_id=str(value.get("threshold_id") or ""),
            metric_key=str(value.get("metric_key") or ""),
            required_value=value.get("required_value"),
            observed_value=value.get("observed_value"),
            comparator=str(value.get("comparator") or ""),
            result_state=str(value.get("result_state") or ""),
            passed=value.get("passed"),
            evidence_reference=tuple(
                str(item) for item in value.get("evidence_reference") or ()),
        )


def _compare(observed: Any, comparator: str, required: Any) -> bool:
    if comparator == ">=":
        return observed >= required
    if comparator == ">":
        return observed > required
    if comparator == "<=":
        return observed <= required
    if comparator == "<":
        return observed < required
    if comparator == "==":
        return observed == required
    if comparator == "!=":
        return observed != required
    raise SatisfactionDecisionError("UNKNOWN_THRESHOLD_COMPARATOR")


def _evaluate_thresholds(
        policy: ThresholdPolicy, metrics: Mapping[str, Any],
        evidence_references: Sequence[str]) -> tuple[ThresholdResult, ...]:
    rows: list[ThresholdResult] = []
    for rule in policy.rules:
        field = str(rule["field"])
        required = rule["value"]
        comparator = str(rule["operator"])
        if field not in metrics:
            state, passed, observed = RESULT_MISSING, None, None
        else:
            observed = metrics[field]
            try:
                passed = _compare(observed, comparator, required)
                state = RESULT_PASSED if passed else RESULT_FAILED
            except (TypeError, ValueError):
                state, passed = RESULT_INVALID, None
        rows.append(ThresholdResult(
            threshold_id=str(rule["threshold_id"]), metric_key=field,
            required_value=required, observed_value=observed,
            comparator=comparator, result_state=state, passed=passed,
            evidence_reference=tuple(sorted(set(evidence_references))),
        ))
    return tuple(rows)


def _derive_decision(
        *, evidence_validation_state: str, policy_classification: str,
        threshold_results: Sequence[ThresholdResult],
        future_only_historical_evidence: bool) -> tuple[str, str]:
    if evidence_validation_state not in {
            EVIDENCE_VERIFIED, EVIDENCE_MISSING, EVIDENCE_LEGACY_UNRESOLVED}:
        return INVALID, "Evidence identity or lineage is structurally invalid."
    if evidence_validation_state == EVIDENCE_LEGACY_UNRESOLVED:
        return INDETERMINATE, "Historical evidence population is unresolved."
    if evidence_validation_state == EVIDENCE_MISSING:
        return INDETERMINATE, "Required governed evidence is missing."
    if future_only_historical_evidence:
        return INDETERMINATE, (
            "A future-only requirement cannot be satisfied by historical "
            "audit-boundary evidence.")
    if policy_classification != T.THRESHOLD_GOVERNED:
        return INDETERMINATE, "The governed threshold policy is unresolved."
    if not threshold_results:
        return INDETERMINATE, "The threshold policy contains no evaluable rules."
    if any(row.result_state == RESULT_INVALID for row in threshold_results):
        return INVALID, "At least one threshold comparison is invalid."
    if any(row.result_state == RESULT_MISSING for row in threshold_results):
        return INDETERMINATE, "At least one required observed metric is missing."
    if all(row.result_state == RESULT_PASSED for row in threshold_results):
        return SATISFIED, "All governed threshold rules passed."
    return NOT_SATISFIED, "At least one governed threshold rule failed."


@dataclass(frozen=True)
class GovernedSatisfactionDecision:
    satisfaction_decision_id: str
    observation_requirement_id: str
    requirement_fingerprint: str
    evidence_set_ids: tuple[str, ...]
    evidence_set_fingerprints: tuple[str, ...]
    dataset_snapshot_ids: tuple[str, ...]
    schema_versions: tuple[str, ...]
    schema_generations: tuple[int | None, ...]
    producer_identities: tuple[tuple[str, str | None, str | None], ...]
    threshold_policy_id: str
    threshold_policy_version: str
    policy_classification: str
    evaluation_metrics_json: str
    threshold_results: tuple[ThresholdResult, ...]
    evidence_validation_state: str
    future_only_historical_evidence: bool
    decision: str
    decision_reason: str
    evaluated_at: str
    evaluator_version: str
    supersedes_satisfaction_decision_id: str | None
    decision_fingerprint: str

    def __post_init__(self) -> None:
        try:
            rid = I.validate_requirement_id(self.observation_requirement_id)
        except I.Stage4IdentityError as exc:
            raise SatisfactionDecisionError(str(exc)) from exc
        if rid != self.observation_requirement_id:
            raise SatisfactionDecisionError("REQUIREMENT_ID_NOT_CANONICAL")
        if not self.evaluator_version:
            raise SatisfactionDecisionError("MISSING_THRESHOLD_EVALUATOR_VERSION")
        if not self.evaluated_at:
            raise SatisfactionDecisionError("MISSING_DECISION_EVALUATED_AT")
        derived, reason = _derive_decision(
            evidence_validation_state=self.evidence_validation_state,
            policy_classification=self.policy_classification,
            threshold_results=self.threshold_results,
            future_only_historical_evidence=(
                self.future_only_historical_evidence),
        )
        if self.decision != derived or self.decision_reason != reason:
            raise SatisfactionDecisionError("DECISION_DERIVATION_MISMATCH")
        if self.decision not in DECISION_STATES:
            raise SatisfactionDecisionError("UNKNOWN_SATISFACTION_DECISION")
        if self.satisfaction_decision_id != self.derived_id():
            raise SatisfactionDecisionError("SATISFACTION_DECISION_ID_MISMATCH")
        if self.decision_fingerprint != self.derived_fingerprint():
            raise SatisfactionDecisionError("DECISION_FINGERPRINT_MISMATCH")

    @property
    def evaluation_metrics(self) -> dict[str, Any]:
        value = json.loads(self.evaluation_metrics_json)
        if not isinstance(value, dict):
            raise SatisfactionDecisionError("EVALUATION_METRICS_NOT_AN_OBJECT")
        return value

    def identity_material(self) -> dict[str, Any]:
        """All content-bound decision material; deliberately clock-free."""
        return {
            "observation_requirement_id": self.observation_requirement_id,
            "requirement_fingerprint": self.requirement_fingerprint,
            "evidence_set_ids": list(self.evidence_set_ids),
            "evidence_set_fingerprints": list(self.evidence_set_fingerprints),
            "dataset_snapshot_ids": list(self.dataset_snapshot_ids),
            "schema_versions": list(self.schema_versions),
            "schema_generations": list(self.schema_generations),
            "producer_identities": [list(row) for row in self.producer_identities],
            "threshold_policy_id": self.threshold_policy_id,
            "threshold_policy_version": self.threshold_policy_version,
            "policy_classification": self.policy_classification,
            "evaluation_metrics": self.evaluation_metrics,
            "threshold_results": [row.to_dict() for row in self.threshold_results],
            "evidence_validation_state": self.evidence_validation_state,
            "future_only_historical_evidence": (
                self.future_only_historical_evidence),
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "evaluator_version": self.evaluator_version,
            "supersedes_satisfaction_decision_id": (
                self.supersedes_satisfaction_decision_id),
        }

    def derived_id(self) -> str:
        return "SDEC-" + _fingerprint(self.identity_material())[:32].upper()

    def fingerprint_material(self) -> dict[str, Any]:
        return {"satisfaction_decision_id": self.satisfaction_decision_id,
                "evaluated_at": self.evaluated_at, **self.identity_material()}

    def derived_fingerprint(self) -> str:
        return _fingerprint(self.fingerprint_material())

    def to_dict(self) -> dict[str, Any]:
        return {**self.fingerprint_material(),
                "decision_fingerprint": self.decision_fingerprint}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GovernedSatisfactionDecision":
        return cls(
            satisfaction_decision_id=str(
                value.get("satisfaction_decision_id") or ""),
            observation_requirement_id=str(
                value.get("observation_requirement_id") or ""),
            requirement_fingerprint=str(value.get("requirement_fingerprint") or ""),
            evidence_set_ids=tuple(str(x) for x in value.get("evidence_set_ids") or ()),
            evidence_set_fingerprints=tuple(
                str(x) for x in value.get("evidence_set_fingerprints") or ()),
            dataset_snapshot_ids=tuple(
                str(x) for x in value.get("dataset_snapshot_ids") or ()),
            schema_versions=tuple(str(x) for x in value.get("schema_versions") or ()),
            schema_generations=tuple(value.get("schema_generations") or ()),
            producer_identities=tuple(
                (str(row[0]), None if row[1] is None else str(row[1]),
                 None if row[2] is None else str(row[2]))
                for row in value.get("producer_identities") or ()),
            threshold_policy_id=str(value.get("threshold_policy_id") or ""),
            threshold_policy_version=str(
                value.get("threshold_policy_version") or ""),
            policy_classification=str(value.get("policy_classification") or ""),
            evaluation_metrics_json=_canonical(value.get("evaluation_metrics") or {}),
            threshold_results=tuple(ThresholdResult.from_dict(row) for row in
                                    value.get("threshold_results") or ()),
            evidence_validation_state=str(
                value.get("evidence_validation_state") or ""),
            future_only_historical_evidence=bool(
                value.get("future_only_historical_evidence", False)),
            decision=str(value.get("decision") or ""),
            decision_reason=str(value.get("decision_reason") or ""),
            evaluated_at=str(value.get("evaluated_at") or ""),
            evaluator_version=str(value.get("evaluator_version") or ""),
            supersedes_satisfaction_decision_id=(
                None if value.get("supersedes_satisfaction_decision_id") is None
                else str(value["supersedes_satisfaction_decision_id"])),
            decision_fingerprint=str(value.get("decision_fingerprint") or ""),
        )


def _new_decision(**kwargs: Any) -> GovernedSatisfactionDecision:
    shell = object.__new__(GovernedSatisfactionDecision)
    for field, value in kwargs.items():
        object.__setattr__(shell, field, value)
    object.__setattr__(shell, "satisfaction_decision_id", "")
    object.__setattr__(shell, "decision_fingerprint", "")
    decision_id = GovernedSatisfactionDecision.derived_id(shell)
    object.__setattr__(shell, "satisfaction_decision_id", decision_id)
    decision_fp = GovernedSatisfactionDecision.derived_fingerprint(shell)
    object.__setattr__(shell, "decision_fingerprint", decision_fp)
    GovernedSatisfactionDecision.__post_init__(shell)
    return shell


def evaluate(
    *, observation_requirement_id: str, evidence_set_ids: Sequence[str],
    threshold_policy: ThresholdPolicy, evaluation_metrics: Mapping[str, Any],
    evidence_sets: Mapping[str, I.EvidenceSet],
    snapshot_registry: D.DatasetSnapshotRegistry,
    evaluated_at: str, evaluator_version: str = EVALUATOR_VERSION,
    supersedes_satisfaction_decision_id: str | None = None,
) -> GovernedSatisfactionDecision:
    """Independently derive a decision; callers cannot supply its state."""
    try:
        rid = I.validate_requirement_id(observation_requirement_id)
    except I.Stage4IdentityError as exc:
        raise SatisfactionDecisionError(str(exc)) from exc
    if threshold_policy.observation_requirement_id != rid:
        raise SatisfactionDecisionError("THRESHOLD_POLICY_REQUIREMENT_MISMATCH")
    if not evaluator_version:
        raise SatisfactionDecisionError("MISSING_THRESHOLD_EVALUATOR_VERSION")
    if not str(evaluated_at or "").strip():
        raise SatisfactionDecisionError("MISSING_DECISION_EVALUATED_AT")
    if not isinstance(evaluation_metrics, Mapping):
        raise SatisfactionDecisionError("EVALUATION_METRICS_NOT_AN_OBJECT")

    requested = tuple(sorted(set(str(item) for item in evidence_set_ids)))
    evidence_fps: list[str] = []
    snapshot_ids: set[str] = set()
    evidence_state = EVIDENCE_VERIFIED if requested else EVIDENCE_MISSING
    for evidence_id in requested:
        evidence = evidence_sets.get(evidence_id)
        if evidence is None:
            evidence_state = "UNKNOWN_EVIDENCE_SET"
            continue
        evidence_fps.append(evidence.content_fingerprint)
        if rid not in evidence.observation_requirement_ids:
            evidence_state = "EVIDENCE_REQUIREMENT_MISMATCH"
        if not evidence.dataset_snapshot_ids:
            evidence_state = EVIDENCE_LEGACY_UNRESOLVED
        validation = D.validate_evidence_identity(
            evidence, registry=snapshot_registry,
            require_dataset_snapshots=True,
            require_uniform_schema=True, require_uniform_generation=True,
        )
        snapshot_ids.update(validation.dataset_snapshot_ids)
        if not validation.valid:
            if (validation.state == D.IDENTITY_LEGACY_UNRESOLVED
                    or (validation.state == D.SNAPSHOT_MISSING
                        and not evidence.dataset_snapshot_ids)):
                evidence_state = EVIDENCE_LEGACY_UNRESOLVED
            else:
                evidence_state = validation.state

    snapshots: list[D.DatasetSnapshot] = []
    for snapshot_id in sorted(snapshot_ids):
        try:
            snapshots.append(snapshot_registry.require(snapshot_id))
        except D.DatasetSnapshotError:
            evidence_state = D.SNAPSHOT_UNKNOWN

    # Cross-set compatibility matters too: individually valid evidence sets
    # cannot be combined if they reinterpret one dataset under two generations.
    for dataset_name in sorted({row.dataset_name for row in snapshots}):
        rows = [row for row in snapshots if row.dataset_name == dataset_name]
        if len({row.schema_version for row in rows}) > 1:
            evidence_state = D.SNAPSHOT_INCOMPATIBLE
        generations = {row.schema_generation for row in rows
                       if row.schema_generation is not None}
        if len(generations) > 1:
            evidence_state = D.GENERATION_MIXED
        producers = {(row.producer_version, row.producer_fingerprint)
                     for row in rows}
        if len(producers) > 1:
            evidence_state = D.PRODUCER_MISMATCH
    if evidence_state == EVIDENCE_VERIFIED and any(
            row.producer_version is None or row.producer_fingerprint is None
            for row in snapshots):
        evidence_state = EVIDENCE_LEGACY_UNRESOLVED

    future_historical = rid in FUTURE_ONLY_REQUIREMENTS and bool(snapshots) and all(
        row.population_class == D.AUDIT_BOUNDARY_POPULATION for row in snapshots)
    results = _evaluate_thresholds(
        threshold_policy, evaluation_metrics, requested)
    decision, reason = _derive_decision(
        evidence_validation_state=evidence_state,
        policy_classification=threshold_policy.classification,
        threshold_results=results,
        future_only_historical_evidence=future_historical,
    )
    return _new_decision(
        observation_requirement_id=rid,
        requirement_fingerprint=I.requirement_authority().semantic_fingerprint(rid),
        evidence_set_ids=requested,
        # ``requested`` is sorted, and fingerprints were appended in that same
        # order, preserving the ID -> membership-fingerprint relationship.
        evidence_set_fingerprints=tuple(evidence_fps),
        dataset_snapshot_ids=tuple(sorted(snapshot_ids)),
        schema_versions=tuple(row.schema_version for row in sorted(
            snapshots, key=lambda item: item.dataset_snapshot_id)),
        schema_generations=tuple(row.schema_generation for row in sorted(
            snapshots, key=lambda item: item.dataset_snapshot_id)),
        producer_identities=tuple(
            (row.dataset_name, row.producer_version, row.producer_fingerprint)
            for row in sorted(snapshots, key=lambda item: item.dataset_snapshot_id)),
        threshold_policy_id=threshold_policy.threshold_policy_id,
        threshold_policy_version=threshold_policy.threshold_policy_version,
        policy_classification=threshold_policy.classification,
        evaluation_metrics_json=_canonical(dict(evaluation_metrics)),
        threshold_results=results,
        evidence_validation_state=evidence_state,
        future_only_historical_evidence=future_historical,
        decision=decision, decision_reason=reason,
        evaluated_at=str(evaluated_at), evaluator_version=str(evaluator_version),
        supersedes_satisfaction_decision_id=(
            supersedes_satisfaction_decision_id),
    )


class SatisfactionDecisionRegistry:
    """Collision-safe immutable history plus non-destructive current pointers."""

    def __init__(self, *, policies: Iterable[ThresholdPolicy],
                 decisions: Iterable[GovernedSatisfactionDecision] = ()) -> None:
        self._policies: dict[str, ThresholdPolicy] = {}
        self._decisions: dict[str, GovernedSatisfactionDecision] = {}
        self._current: dict[str, str] = {}
        for policy in policies:
            existing = self._policies.get(policy.threshold_policy_id)
            if existing and existing.to_dict() != policy.to_dict():
                raise SatisfactionDecisionError("CONFLICTING_THRESHOLD_POLICY_ID")
            self._policies[policy.threshold_policy_id] = policy
        for decision in decisions:
            self.register(decision)

    def register(self, decision: GovernedSatisfactionDecision
                 ) -> GovernedSatisfactionDecision:
        existing = self._decisions.get(decision.satisfaction_decision_id)
        if existing is not None:
            if existing.to_dict() != decision.to_dict():
                raise SatisfactionDecisionError(
                    "CONFLICTING_SATISFACTION_DECISION_ID")
            return existing
        policy = self._policies.get(decision.threshold_policy_id)
        if policy is None:
            raise SatisfactionDecisionError("UNKNOWN_THRESHOLD_POLICY_ID")
        if policy.observation_requirement_id != decision.observation_requirement_id:
            raise SatisfactionDecisionError("THRESHOLD_POLICY_REQUIREMENT_MISMATCH")
        current_id = self._current.get(decision.observation_requirement_id)
        supersedes = decision.supersedes_satisfaction_decision_id
        if supersedes is not None:
            old = self._decisions.get(supersedes)
            if old is None:
                raise SatisfactionDecisionError("UNKNOWN_SUPERSEDED_DECISION")
            if old.observation_requirement_id != decision.observation_requirement_id:
                raise SatisfactionDecisionError("SUPERSESSION_REQUIREMENT_MISMATCH")
            if current_id != supersedes:
                raise SatisfactionDecisionError("SUPERSESSION_NOT_CURRENT")
        elif current_id is not None:
            raise SatisfactionDecisionError("SUPERSESSION_REQUIRED")
        self._decisions[decision.satisfaction_decision_id] = decision
        self._current[decision.observation_requirement_id] = (
            decision.satisfaction_decision_id)
        return decision

    def get(self, decision_id: str) -> GovernedSatisfactionDecision:
        try:
            return self._decisions[str(decision_id)]
        except KeyError:
            raise SatisfactionDecisionError(
                "UNKNOWN_SATISFACTION_DECISION_ID:" + str(decision_id)) from None

    def current_for_requirement(
            self, requirement_id: str) -> GovernedSatisfactionDecision | None:
        rid = I.validate_requirement_id(requirement_id)
        decision_id = self._current.get(rid)
        return None if decision_id is None else self.get(decision_id)

    def history_for_requirement(
            self, requirement_id: str) -> tuple[GovernedSatisfactionDecision, ...]:
        rid = I.validate_requirement_id(requirement_id)
        # Dict insertion order is the immutable registration/supersession
        # sequence.  Sorting by an ID would destroy lineage order when two
        # evaluations share a timestamp.
        return tuple(row for row in self._decisions.values()
                     if row.observation_requirement_id == rid)

    def verify(
        self, decision_id: str, *, evidence_sets: Mapping[str, I.EvidenceSet],
        snapshot_registry: D.DatasetSnapshotRegistry,
    ) -> GovernedSatisfactionDecision:
        stored = self.get(decision_id)
        policy = self._policies.get(stored.threshold_policy_id)
        if policy is None:
            raise SatisfactionDecisionError("UNKNOWN_THRESHOLD_POLICY_ID")
        expected = evaluate(
            observation_requirement_id=stored.observation_requirement_id,
            evidence_set_ids=stored.evidence_set_ids,
            threshold_policy=policy,
            evaluation_metrics=stored.evaluation_metrics,
            evidence_sets=evidence_sets, snapshot_registry=snapshot_registry,
            evaluated_at=stored.evaluated_at,
            evaluator_version=stored.evaluator_version,
            supersedes_satisfaction_decision_id=(
                stored.supersedes_satisfaction_decision_id),
        )
        if expected.to_dict() != stored.to_dict():
            raise SatisfactionDecisionError("DECISION_REPRODUCTION_MISMATCH")
        return stored

    def to_dict(self) -> dict[str, Any]:
        body = {
            "schema": STORE_SCHEMA,
            "threshold_policies": [row.to_dict() for row in sorted(
                self._policies.values(), key=lambda row: row.threshold_policy_id)],
            # Registration order is supersession order and is deterministic;
            # sorting by content hash could put a successor before its parent.
            "decisions": [row.to_dict() for row in self._decisions.values()],
            "current_by_requirement": dict(sorted(self._current.items())),
        }
        return {**body, "store_fingerprint": _fingerprint(body)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SatisfactionDecisionRegistry":
        if value.get("schema") != STORE_SCHEMA:
            raise SatisfactionDecisionError("UNKNOWN_SATISFACTION_STORE_SCHEMA")
        body = {key: value.get(key) for key in (
            "schema", "threshold_policies", "decisions",
            "current_by_requirement")}
        if value.get("store_fingerprint") != _fingerprint(body):
            raise SatisfactionDecisionError("SATISFACTION_STORE_FINGERPRINT_MISMATCH")
        policies = [ThresholdPolicy.from_dict(row) for row in
                    value.get("threshold_policies") or ()]
        decisions = [GovernedSatisfactionDecision.from_dict(row) for row in
                     value.get("decisions") or ()]
        registry = cls(policies=policies, decisions=decisions)
        if registry._current != dict(value.get("current_by_requirement") or {}):
            raise SatisfactionDecisionError("SATISFACTION_CURRENT_INDEX_MISMATCH")
        return registry

    def save(self, path: Path = STATE_PATH) -> None:
        _write_json(path, self.to_dict())

    @classmethod
    def load(cls, path: Path = STATE_PATH) -> "SatisfactionDecisionRegistry":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise SatisfactionDecisionError("SATISFACTION_STORE_UNREADABLE") from exc
        if not isinstance(value, Mapping):
            raise SatisfactionDecisionError("SATISFACTION_STORE_NOT_AN_OBJECT")
        return cls.from_dict(value)


def governed_evidence_map(
        registry: D.DatasetSnapshotRegistry | None = None,
        ) -> dict[str, I.EvidenceSet]:
    resolved = registry or D.bootstrap_registry()
    return {row.evidence_set_id: row for row in D.governed_evidence_sets(resolved)}


def build_registry(
        snapshot_registry: D.DatasetSnapshotRegistry | None = None,
        evaluated_at: str = "2026-09-29T00:00:00Z",
        ) -> SatisfactionDecisionRegistry:
    """Build truthful current decisions; never fabricate historical metrics."""
    snapshots = snapshot_registry or D.bootstrap_registry()
    evidence = governed_evidence_map(snapshots)
    authority = I.requirement_authority()
    policies = [threshold_policy_for_requirement(rid, authority)
                for rid in authority.ids]
    registry = SatisfactionDecisionRegistry(policies=policies)
    for policy in policies:
        rid = policy.observation_requirement_id
        evidence_ids = sorted(
            row.evidence_set_id for row in evidence.values()
            if rid in row.observation_requirement_ids)
        registry.register(evaluate(
            observation_requirement_id=rid,
            evidence_set_ids=evidence_ids,
            threshold_policy=policy,
            evaluation_metrics={},
            evidence_sets=evidence, snapshot_registry=snapshots,
            evaluated_at=evaluated_at,
        ))
    return registry


def persist_authority(
        snapshot_registry: D.DatasetSnapshotRegistry | None = None,
        ) -> SatisfactionDecisionRegistry:
    snapshots = snapshot_registry or D.bootstrap_registry()
    evidence = governed_evidence_map(snapshots)
    registry = build_registry(snapshots)
    # Prove every record from governed material before writing it.
    for rid in I.CANONICAL_REQUIREMENT_IDS:
        decision = registry.current_for_requirement(rid)
        assert decision is not None
        registry.verify(decision.satisfaction_decision_id,
                        evidence_sets=evidence, snapshot_registry=snapshots)
    registry.save(STATE_PATH)
    reloaded = SatisfactionDecisionRegistry.load(STATE_PATH)
    for rid in I.CANONICAL_REQUIREMENT_IDS:
        decision = reloaded.current_for_requirement(rid)
        assert decision is not None
        reloaded.verify(decision.satisfaction_decision_id,
                        evidence_sets=evidence, snapshot_registry=snapshots)
    document = reloaded.to_dict()
    document["authority"] = {
        "description": "Persisted governed Stage 4 satisfaction decisions",
        "historical_compatibility": (
            "No legacy satisfaction boolean was converted into a governed "
            "decision. Missing metrics and unresolved lineage remain "
            "INDETERMINATE; invalid identity remains INVALID."),
        "reentry_behavior_changed": False,
    }
    _write_json(ASSURANCE_JSON_PATH, document)
    lines = [
        "# Stage 4 governed satisfaction decisions", "",
        "Decisions are content-bound, immutable, and independently revalidated.",
        "Legacy labels are threshold inputs only; they are not final authority.",
        "", "| Requirement | Decision | Decision ID | Evidence sets |",
        "|---|---|---|---|",
    ]
    for rid in I.CANONICAL_REQUIREMENT_IDS:
        row = reloaded.current_for_requirement(rid)
        assert row is not None
        lines.append(
            f"| {rid} | {row.decision} | {row.satisfaction_decision_id} | "
            f"{', '.join(row.evidence_set_ids) or 'none'} |")
    ASSURANCE_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return reloaded


__all__ = [
    "ASSURANCE_JSON_PATH", "ASSURANCE_MD_PATH", "DECISION_STATES",
    "EVALUATOR_VERSION", "FUTURE_ONLY_REQUIREMENTS",
    "GovernedSatisfactionDecision", "INDETERMINATE", "INVALID",
    "NOT_SATISFIED", "SATISFIED", "STATE_PATH",
    "SatisfactionDecisionError", "SatisfactionDecisionRegistry",
    "ThresholdPolicy", "ThresholdResult", "build_registry", "evaluate",
    "governed_evidence_map", "persist_authority",
    "threshold_policy_for_requirement",
]


if __name__ == "__main__":
    result = persist_authority()
    print(STATE_PATH)
    print({rid: result.current_for_requirement(rid).decision
           for rid in I.CANONICAL_REQUIREMENT_IDS})
