"""Governed scientific re-entry, question-scoped recertification and versioning.

Stage 4 control-plane authority that lets ONE OR MORE affected questions
re-enter science after a legitimate governed change (implementation repair,
new evidence epoch, schema/data recovery, dependency resolution, governed
method repair) without rerunning or replacing unaffected questions.

The original 70-question V1 certification and the V1 assured findings are
immutable historical snapshots.  Re-entry appends a new certification
version and a new assured-finding version; it never mutates superseded
content, never rewrites the global certification, and never invalidates the
other questions.  V2 means NEWER CERTIFIED KNOWLEDGE, not a better result.

Governed lifecycle (no step may be skipped)::

    code/data change
      -> governed re-entry authorization
      -> question-scoped scientific rerun
      -> question-scoped assurance revalidation (VERIFIED required)
      -> versioned certification (V(n+1))
      -> versioned assured finding (V(n+1)); V(n) becomes SUPERSEDED

Durable authority lives in ``scientific_reentry_state.json``.  Dated audit
mirrors under ``analysis/assurance`` are derived views, never the authority.
This module performs no S3/live reads and does not start Q71+.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

from research_engine.control_plane import assured_epistemic_findings as A
from research_engine.control_plane import gap_governance as G
from research_engine.control_plane import stage4_data_versioning as V
from research_engine.control_plane import stage4_dataset_snapshot as D
from research_engine.control_plane import stage4_identity as I
from research_engine.control_plane import stage4_satisfaction as SD
from research_engine.control_plane.evidence_resolver import (
    authoritative_evidence_schema,
)
from research_engine.control_plane.final_assurance_certification import (
    FinalAssuranceCertification,
)
from research_engine.control_plane.historical_research_pass import run_questions
from research_engine.control_plane.report_ownership import canonical_report_owner
from research_engine.registry.research_question_registry import (
    REGISTRY,
    REGISTRY_BY_ID,
)

REENTRY_SCHEMA = 2
STAGE = "STAGE4_SCIENTIFIC_REENTRY"
STATE_PATH = Path("research_engine/control_plane/scientific_reentry_state.json")
AUDIT_STAMP = "20260929"
AUDIT_JSON = Path(f"analysis/assurance/stage4_scientific_reentry_{AUDIT_STAMP}.json")
AUDIT_MD = Path(f"analysis/assurance/stage4_scientific_reentry_{AUDIT_STAMP}.md")

BASELINE_CERTIFICATION_PATH = A.CERTIFICATION_PATH
BASELINE_FINDINGS_PATH = Path(
    "analysis/assurance/assured_epistemic_findings_20260928.json")
BASELINE_CERTIFICATION_FINGERPRINT = A.EXPECTED_CERTIFICATION_FINGERPRINT
BASELINE_MANIFEST_PATH = A.MANIFEST_PATH
REGISTRY_VERSION = "research_question_registry_v1"
QUESTION_CONTRACT_PREFIX = "QREG"
EVIDENCE_CONTRACT_ID = "STAGE4_GOVERNED_EVIDENCE_CHECKPOINT"
QUESTION_COUNT = 70
QUESTION_IDS: tuple[str, ...] = tuple(question.id for question in REGISTRY)
QUESTION_ID_SET = frozenset(QUESTION_IDS)

AUTH_REQUESTED = "REQUESTED"
AUTH_AUTHORIZED = "AUTHORIZED"
AUTH_RESULT_RECORDED = "SCIENTIFIC_RESULT_RECORDED"
AUTH_ASSURANCE_RECORDED = "ASSURANCE_RECORDED"
AUTH_PUBLISHED = "PUBLISHED"
ALLOWED_AUTHORIZATION_STATES = frozenset({
    AUTH_REQUESTED, AUTH_AUTHORIZED, AUTH_RESULT_RECORDED,
    AUTH_ASSURANCE_RECORDED, AUTH_PUBLISHED})

ELIGIBLE = "ELIGIBLE"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
ELIGIBILITY_INVALID = "INVALID"
ELIGIBILITY_STATES = frozenset({ELIGIBLE, NOT_ELIGIBLE, ELIGIBILITY_INVALID})
ELIGIBILITY_EVALUATOR_VERSION = "stage4_reentry_eligibility_v1"
AUTHORIZATION_EVALUATOR_VERSION = "stage4_reentry_authorization_v1"
LEGACY_UNGOVERNED = "LEGACY_UNGOVERNED"
_SDEC_PATTERN = re.compile(r"^SDEC-[A-F0-9]{32}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
TRIGGER_IMPLEMENTATION_REPAIR = "IMPLEMENTATION_REPAIR"
TRIGGER_DATA_THRESHOLD_REACHED = "DATA_THRESHOLD_REACHED"
TRIGGER_SCHEMA_COLLECTION_RECOVERED = "SCHEMA_COLLECTION_RECOVERED"
TRIGGER_DEPENDENCY_RESOLVED = "DEPENDENCY_RESOLVED"
TRIGGER_GOVERNED_METHOD_REPAIR = "GOVERNED_METHOD_REPAIR"
TRIGGER_NEW_EVIDENCE_EPOCH = "NEW_EVIDENCE_EPOCH"
ALLOWED_TRIGGERS: tuple[str, ...] = (
    TRIGGER_IMPLEMENTATION_REPAIR,
    TRIGGER_DATA_THRESHOLD_REACHED,
    TRIGGER_SCHEMA_COLLECTION_RECOVERED,
    TRIGGER_DEPENDENCY_RESOLVED,
    TRIGGER_GOVERNED_METHOD_REPAIR,
    TRIGGER_NEW_EVIDENCE_EPOCH,
)
GAP_COUPLED_TRIGGERS = frozenset({
    TRIGGER_IMPLEMENTATION_REPAIR,
    TRIGGER_DATA_THRESHOLD_REACHED,
    TRIGGER_SCHEMA_COLLECTION_RECOVERED,
})
TRIGGER_GAP_TYPE = {
    TRIGGER_IMPLEMENTATION_REPAIR: G.GAP_TYPE_IMPL,
    TRIGGER_DATA_THRESHOLD_REACHED: G.GAP_TYPE_DATA,
    TRIGGER_SCHEMA_COLLECTION_RECOVERED: G.GAP_TYPE_DATA,
}
EVIDENCE_CHANGE_TRIGGERS = frozenset({
    TRIGGER_DATA_THRESHOLD_REACHED,
    TRIGGER_SCHEMA_COLLECTION_RECOVERED,
    TRIGGER_NEW_EVIDENCE_EPOCH,
})
CONTRACT_EVOLUTION_TRIGGERS = frozenset({
    TRIGGER_GOVERNED_METHOD_REPAIR,
    TRIGGER_NEW_EVIDENCE_EPOCH,
})
SCIENTIFIC_STATES = frozenset({
    "COMPLETE", "NEGATIVE_RESULT", "NO_EFFECT", "INSUFFICIENT_DATA",
    "WAITING_DATA", "HISTORICALLY_UNANSWERABLE", "IMPLEMENTATION_BLOCKED",
})
ASSURANCE_STATES = frozenset({"VERIFIED", "FAILED"})
DEPENDENCY_DISALLOWED_STATES = ("IMPLEMENTATION_BLOCKED",)
DEPENDENCY_REQUIREMENT: dict[str, Any] = {
    "required_current_certification": True,
    "required_assurance_status": "VERIFIED",
    "min_certification_version": 1,
    "disallowed_scientific_states": list(DEPENDENCY_DISALLOWED_STATES),
}
PRE_EXISTING_BASELINE_DRIFT = (
    "PRE_EXISTING_BASELINE_DRIFT: operational-baseline assertions expect "
    "(60,10)/(58,12) while the repository currently returns (63,7); "
    "reproduced on a clean checkout before this task and deliberately not "
    "repaired here.")


class ReentryError(RuntimeError):
    """A governed re-entry invariant was violated (always fail closed)."""


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, default=str).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReentryError(f"{path} is not a JSON object")
    return value


def _utc_now(now: str | None = None) -> str:
    if now is not None:
        return str(now)
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _registry_authority(registry_version: str):
    if registry_version == REGISTRY_VERSION:
        return REGISTRY, REGISTRY_BY_ID, registry_fingerprint
    from research_engine.control_plane import stage4_registry_successor as successor
    if registry_version == successor.REGISTRY_VERSION:
        return successor.REGISTRY_V2, successor.REGISTRY_V2_BY_ID, successor.registry_fingerprint
    raise ReentryError(f"UNKNOWN_REGISTRY_VERSION:{registry_version}")


def question_definition_material(question_id: str, *,
                                 registry_version: str = REGISTRY_VERSION) -> dict[str, Any]:
    """Immutable structural definition of one canonical question."""
    qid = str(question_id).strip().upper()
    _, by_id, _ = _registry_authority(registry_version)
    question = by_id.get(qid)
    if question is None:
        raise ReentryError(f"UNKNOWN_QUESTION:{qid}")
    return {
        "question_id": qid,
        "title": question.title,
        "description": question.description,
        "category": question.category,
        "runner_module": question.runner_module,
        "runner_function": question.runner_function,
        "report_filename": question.report_filename,
        "required_fields": question.required_fields,
        "validation_rules": question.validation_rules,
        "data_sources": [str(getattr(source, "value", source))
                         for source in question.data_sources],
        "depends_on": list(question.depends_on),
        "scientific_owner_id": question.scientific_owner_id,
        "legacy_ids": list(question.legacy_ids),
    }


def registry_fingerprint() -> str:
    """Deterministic fingerprint over the whole canonical question bank."""
    return _fingerprint({question.id: question_definition_material(question.id)
                         for question in REGISTRY})


def scientific_owner_id(question_id: str, *, registry_version: str = REGISTRY_VERSION) -> str:
    _, by_id, _ = _registry_authority(registry_version)
    question = by_id[str(question_id).strip().upper()]
    return str(question.scientific_owner_id or question.id)


def governed_runner_id(question_id: str, *, registry_version: str = REGISTRY_VERSION) -> str:
    """Only the registry-bound runner may be authorized for a question."""
    _, by_id, _ = _registry_authority(registry_version)
    owner_id = scientific_owner_id(question_id, registry_version=registry_version)
    owner = by_id[owner_id]
    if not owner.runner_module or not owner.runner_function:
        raise ReentryError(f"RUNNER_NOT_GOVERNED:{question_id}")
    return f"{owner.runner_module}.{owner.runner_function}"


def evidence_contract_material(question_id: str, *,
                               registry_version: str = REGISTRY_VERSION) -> dict[str, Any]:
    _, by_id, _ = _registry_authority(registry_version)
    question = by_id[str(question_id).strip().upper()]
    schemas = {}
    for source in question.data_sources:
        name = str(getattr(source, "value", source))
        schemas[name] = str(authoritative_evidence_schema(name) or "UNRESOLVED")
    extension = None
    if registry_version != REGISTRY_VERSION:
        from research_engine.control_plane.stage4_registry_successor import EVIDENCE_CONTRACT_EXTENSIONS
        extension = EVIDENCE_CONTRACT_EXTENSIONS.get(question.id)
    return {
        "evidence_contract_id": EVIDENCE_CONTRACT_ID,
        "evidence_contract_version": (
            __import__("research_engine.control_plane.stage4_registry_successor", fromlist=["EVIDENCE_CONTRACT_VERSION"])
            .EVIDENCE_CONTRACT_VERSION.get(question.id, "v1")
            if registry_version != REGISTRY_VERSION else "v1"),
        "source_schemas": schemas,
        "registry_version": registry_version,
        "contract_extension": extension,
    }


def question_contract_pins(question_id: str, *,
                           registry_version: str = REGISTRY_VERSION) -> dict[str, Any]:
    qid = str(question_id).strip().upper()
    definition = question_definition_material(qid, registry_version=registry_version)
    evidence = evidence_contract_material(qid, registry_version=registry_version)
    runner_id = governed_runner_id(qid, registry_version=registry_version)
    contract_version = 1
    if registry_version != REGISTRY_VERSION:
        from research_engine.control_plane.stage4_registry_successor import QUESTION_CONTRACT_VERSION
        contract_version = int(QUESTION_CONTRACT_VERSION.get(qid, 1))
    _, _, registry_fp = _registry_authority(registry_version)
    return {
        "question_id": qid,
        "question_contract_id": f"{QUESTION_CONTRACT_PREFIX}-{qid}",
        "question_contract_version": contract_version,
        "question_contract_fingerprint": _fingerprint(definition),
        "evidence_contract_id": evidence["evidence_contract_id"],
        "evidence_contract_version": evidence["evidence_contract_version"],
        "evidence_contract_fingerprint": _fingerprint(evidence),
        "runner_id": runner_id,
        "runner_contract_fingerprint": _fingerprint(
            {"runner_id": runner_id, "registry_version": registry_version}),
        "registry_version": registry_version,
        "registry_fingerprint": registry_fp(),
    }


def population_fingerprint(material: Mapping[str, Any]) -> str:
    """Deterministic population fingerprint from governed counts."""
    keys = ("candidate_count", "usable_count", "analytical_count",
            "exclusion_count", "unexplained_count")
    return _fingerprint({key: material.get(key) for key in keys})


def evidence_epoch_fingerprint(epoch: V.EvidenceEpoch) -> str:
    """Canonical content hash for a registered evidence epoch."""
    return _fingerprint(epoch.to_dict())


def certification_identity(question_id: str, version: int) -> str:
    return f"{str(question_id).strip().upper()}:c{int(version)}"


@dataclass(frozen=True)
class ReentryEligibility:
    """Derived permission to request re-entry; never caller-declared."""

    reentry_eligibility_id: str
    observation_requirement_id: str
    satisfaction_decision_id: str
    evidence_set_ids: tuple[str, ...]
    dataset_snapshot_ids: tuple[str, ...]
    evidence_epoch: str | None
    eligibility_state: str
    eligibility_reason: str
    evaluated_at: str
    evaluator_version: str
    eligibility_fingerprint: str

    def material(self) -> dict[str, Any]:
        return {
            "observation_requirement_id": self.observation_requirement_id,
            "satisfaction_decision_id": self.satisfaction_decision_id,
            "evidence_set_ids": list(self.evidence_set_ids),
            "dataset_snapshot_ids": list(self.dataset_snapshot_ids),
            "evidence_epoch": self.evidence_epoch,
            "eligibility_state": self.eligibility_state,
            "eligibility_reason": self.eligibility_reason,
            "evaluator_version": self.evaluator_version,
        }

    def derived_id(self) -> str:
        return "REEL-" + _fingerprint(self.material())[:32].upper()

    def fingerprint_material(self) -> dict[str, Any]:
        return {"reentry_eligibility_id": self.reentry_eligibility_id,
                "evaluated_at": self.evaluated_at, **self.material()}

    def derived_fingerprint(self) -> str:
        return _fingerprint(self.fingerprint_material())

    def verify(self) -> "ReentryEligibility":
        if self.eligibility_state not in ELIGIBILITY_STATES:
            raise ReentryError("ELIGIBILITY_STATE_UNKNOWN")
        if self.reentry_eligibility_id != self.derived_id():
            raise ReentryError("REENTRY_ELIGIBILITY_ID_MISMATCH")
        if self.eligibility_fingerprint != self.derived_fingerprint():
            raise ReentryError("REENTRY_ELIGIBILITY_FINGERPRINT_MISMATCH")
        return self

    def to_dict(self) -> dict[str, Any]:
        return {**self.fingerprint_material(),
                "eligibility_fingerprint": self.eligibility_fingerprint}

    @classmethod
    def create(
        cls, *, observation_requirement_id: str,
        satisfaction_decision_id: str, evidence_set_ids: Sequence[str],
        dataset_snapshot_ids: Sequence[str], evidence_epoch: str | None,
        eligibility_state: str, eligibility_reason: str, evaluated_at: str,
    ) -> "ReentryEligibility":
        values = dict(
            observation_requirement_id=observation_requirement_id,
            satisfaction_decision_id=satisfaction_decision_id,
            evidence_set_ids=tuple(evidence_set_ids),
            dataset_snapshot_ids=tuple(dataset_snapshot_ids),
            evidence_epoch=evidence_epoch,
            eligibility_state=eligibility_state,
            eligibility_reason=eligibility_reason,
            evaluated_at=evaluated_at,
            evaluator_version=ELIGIBILITY_EVALUATOR_VERSION,
        )
        shell = object.__new__(cls)
        for key, value in values.items():
            object.__setattr__(shell, key, value)
        object.__setattr__(shell, "reentry_eligibility_id", "")
        object.__setattr__(shell, "eligibility_fingerprint", "")
        object.__setattr__(shell, "reentry_eligibility_id", shell.derived_id())
        object.__setattr__(shell, "eligibility_fingerprint",
                           shell.derived_fingerprint())
        return shell.verify()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReentryEligibility":
        return cls(
            reentry_eligibility_id=str(value.get("reentry_eligibility_id") or ""),
            observation_requirement_id=str(
                value.get("observation_requirement_id") or ""),
            satisfaction_decision_id=str(
                value.get("satisfaction_decision_id") or ""),
            evidence_set_ids=tuple(value.get("evidence_set_ids") or ()),
            dataset_snapshot_ids=tuple(value.get("dataset_snapshot_ids") or ()),
            evidence_epoch=(None if value.get("evidence_epoch") is None else
                            str(value.get("evidence_epoch"))),
            eligibility_state=str(value.get("eligibility_state") or ""),
            eligibility_reason=str(value.get("eligibility_reason") or ""),
            evaluated_at=str(value.get("evaluated_at") or ""),
            evaluator_version=str(value.get("evaluator_version") or ""),
            eligibility_fingerprint=str(
                value.get("eligibility_fingerprint") or ""),
        ).verify()


@dataclass(frozen=True)
class ReentryAuthorization:
    """Immutable issuance record bound to one eligible decision."""

    reentry_authorization_id: str
    reentry_eligibility_id: str
    satisfaction_decision_id: str
    observation_requirement_id: str
    reentry_id: str
    authorization_state: str
    authorized_at: str
    evaluator_version: str
    authorization_fingerprint: str

    def material(self) -> dict[str, Any]:
        return {
            "reentry_eligibility_id": self.reentry_eligibility_id,
            "satisfaction_decision_id": self.satisfaction_decision_id,
            "observation_requirement_id": self.observation_requirement_id,
            "reentry_id": self.reentry_id,
            "authorization_state": self.authorization_state,
            "evaluator_version": self.evaluator_version,
        }

    def derived_id(self) -> str:
        return "REAUTH-" + _fingerprint(self.material())[:32].upper()

    def derived_fingerprint(self) -> str:
        return _fingerprint({"reentry_authorization_id":
                             self.reentry_authorization_id,
                             "authorized_at": self.authorized_at,
                             **self.material()})

    def verify(self) -> "ReentryAuthorization":
        if self.authorization_state != AUTH_AUTHORIZED:
            raise ReentryError("AUTHORIZATION_RECORD_STATE_INVALID")
        if self.reentry_authorization_id != self.derived_id():
            raise ReentryError("REENTRY_AUTHORIZATION_ID_MISMATCH")
        if self.authorization_fingerprint != self.derived_fingerprint():
            raise ReentryError("REENTRY_AUTHORIZATION_FINGERPRINT_MISMATCH")
        return self

    def to_dict(self) -> dict[str, Any]:
        return {"reentry_authorization_id": self.reentry_authorization_id,
                "authorized_at": self.authorized_at,
                **self.material(),
                "authorization_fingerprint": self.authorization_fingerprint}

    @classmethod
    def create(cls, *, eligibility: ReentryEligibility, reentry_id: str,
               authorized_at: str) -> "ReentryAuthorization":
        values = dict(
            reentry_eligibility_id=eligibility.reentry_eligibility_id,
            satisfaction_decision_id=eligibility.satisfaction_decision_id,
            observation_requirement_id=eligibility.observation_requirement_id,
            reentry_id=str(reentry_id), authorization_state=AUTH_AUTHORIZED,
            authorized_at=authorized_at,
            evaluator_version=AUTHORIZATION_EVALUATOR_VERSION,
        )
        shell = object.__new__(cls)
        for key, value in values.items():
            object.__setattr__(shell, key, value)
        object.__setattr__(shell, "reentry_authorization_id", "")
        object.__setattr__(shell, "authorization_fingerprint", "")
        object.__setattr__(shell, "reentry_authorization_id", shell.derived_id())
        object.__setattr__(shell, "authorization_fingerprint",
                           shell.derived_fingerprint())
        return shell.verify()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReentryAuthorization":
        return cls(
            reentry_authorization_id=str(
                value.get("reentry_authorization_id") or ""),
            reentry_eligibility_id=str(
                value.get("reentry_eligibility_id") or ""),
            satisfaction_decision_id=str(
                value.get("satisfaction_decision_id") or ""),
            observation_requirement_id=str(
                value.get("observation_requirement_id") or ""),
            reentry_id=str(value.get("reentry_id") or ""),
            authorization_state=str(value.get("authorization_state") or ""),
            authorized_at=str(value.get("authorized_at") or ""),
            evaluator_version=str(value.get("evaluator_version") or ""),
            authorization_fingerprint=str(
                value.get("authorization_fingerprint") or ""),
        ).verify()


@dataclass(frozen=True)
class ReentryRequest:
    """A governed, fully pinned request for one question to re-enter science.

    Every field is required so that execution authority can never be free
    text: the trigger class, the superseded finding/certification, the
    question/evidence contracts, the runner identity, the evidence epoch and
    the dependency state are all pinned before any science may run.
    """
    reentry_id: str
    question_id: str
    reason: str
    trigger_type: str
    previous_finding_id: str
    previous_finding_version: int
    previous_certification_fingerprint: str
    question_contract_id: str
    question_contract_version: int
    question_contract_fingerprint: str
    evidence_contract_id: str
    evidence_contract_version: str
    evidence_contract_fingerprint: str
    runner_id: str
    runner_contract_fingerprint: str
    evidence_epoch: str
    evidence_fingerprint: str
    observation_requirement_id: str
    satisfaction_decision_id: str
    registry_version: str
    registry_fingerprint: str
    gap_work_item_id: str | None
    dependency_state: dict[str, Any]
    requested_at: str
    authorization_state: str = AUTH_REQUESTED
    contract_evolution: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in self.__dict__.items()}

    def fingerprint(self) -> str:
        return _fingerprint(self.to_dict())


def build_reentry_request(
        *,
        reentry_id: str,
        question_id: str,
        reason: str,
        trigger_type: str,
        evidence_epoch: str,
        observation_requirement_id: str,
        satisfaction_decision_id: str,
        evidence_fingerprint: str | None = None,
        evidence_epochs: Mapping[str, V.EvidenceEpoch] | None = None,
        gap_work_item_id: str | None = None,
        contract_evolution: dict[str, Any] | None = None,
        registry_version: str = REGISTRY_VERSION,
        requested_at: str | None = None,
        state: Mapping[str, Any] | None = None) -> ReentryRequest:
    """Assemble a fully pinned request from the current governed state."""
    qid = str(question_id).strip().upper()
    pins = question_contract_pins(qid, registry_version=registry_version)
    previous_finding_version = 1
    previous_finding_id = A.finding_identity(qid, 1)
    previous_certification_fingerprint = BASELINE_CERTIFICATION_FINGERPRINT
    if state is not None:
        finding = current_finding(state, qid)
        certification = current_certification(state, qid)
        previous_finding_version = int(finding["finding_version"])
        previous_finding_id = str(finding["finding_id"])
        previous_certification_fingerprint = str(
            certification["certification_fingerprint"])
    resolved_evidence_fingerprint = evidence_fingerprint
    if resolved_evidence_fingerprint is None:
        epochs = evidence_epochs
        if epochs is None:
            epochs = canonical_reentry_authorities()[3]
        epoch = epochs.get(str(evidence_epoch))
        if epoch is None:
            raise ReentryError("EVIDENCE_EPOCH_UNREGISTERED")
        resolved_evidence_fingerprint = evidence_epoch_fingerprint(epoch)
    return ReentryRequest(
        reentry_id=str(reentry_id),
        question_id=qid,
        reason=str(reason),
        trigger_type=str(trigger_type),
        previous_finding_id=previous_finding_id,
        previous_finding_version=previous_finding_version,
        previous_certification_fingerprint=previous_certification_fingerprint,
        question_contract_id=pins["question_contract_id"],
        question_contract_version=int(pins["question_contract_version"]),
        question_contract_fingerprint=pins["question_contract_fingerprint"],
        evidence_contract_id=pins["evidence_contract_id"],
        evidence_contract_version=pins["evidence_contract_version"],
        evidence_contract_fingerprint=pins["evidence_contract_fingerprint"],
        runner_id=pins["runner_id"],
        runner_contract_fingerprint=pins["runner_contract_fingerprint"],
        evidence_epoch=str(evidence_epoch),
        evidence_fingerprint=str(resolved_evidence_fingerprint),
        observation_requirement_id=str(observation_requirement_id),
        satisfaction_decision_id=str(satisfaction_decision_id),
        registry_version=pins["registry_version"],
        registry_fingerprint=pins["registry_fingerprint"],
        gap_work_item_id=gap_work_item_id,
        dependency_state={},
        requested_at=_utc_now(requested_at),
        contract_evolution=contract_evolution,
    )


def bootstrap_state(
        *,
        certification_path: Path | str = BASELINE_CERTIFICATION_PATH,
        findings_path: Path | str = BASELINE_FINDINGS_PATH) -> dict[str, Any]:
    """Bootstrap the V1 history from the frozen V1 artifacts.

    The current certified state is adopted, never rewritten: 70 question
    certifications at version 1, 70 assured findings at version 1, zero
    superseded and zero V2.  Migration changes no scientific state; the
    global V1 certification fingerprint is carried through unchanged.
    """
    certification = _read_json(Path(certification_path))
    if certification.get("certification_fingerprint") != BASELINE_CERTIFICATION_FINGERPRINT:
        raise ReentryError("BASELINE_CERTIFICATION_FINGERPRINT_CHANGED")
    if int(certification.get("question_count") or 0) != QUESTION_COUNT:
        raise ReentryError("BASELINE_CERTIFICATION_NOT_EXACTLY_70")
    findings_store = _read_json(Path(findings_path))
    if findings_store.get("certification_fingerprint") != BASELINE_CERTIFICATION_FINGERPRINT:
        raise ReentryError("BASELINE_FINDINGS_NOT_CERTIFIED_BY_V1")
    findings_by_id = {str(item["question_id"]): item
                      for item in findings_store.get("findings", ())}
    if set(findings_by_id) != set(QUESTION_IDS):
        raise ReentryError("BASELINE_FINDINGS_ID_SET_MISMATCH")

    certifications: dict[str, list[dict[str, Any]]] = {}
    finding_history: dict[str, list[dict[str, Any]]] = {}
    scientific_results: dict[str, list[dict[str, Any]]] = {}
    scientific_states: dict[str, str] = {}
    for qid in QUESTION_IDS:
        row = next(item for item in certification["certifications"]
                   if str(item["question_id"]) == qid)
        finding_row = findings_by_id[qid]
        if str(finding_row.get("scientific_state")) != str(row.get("scientific_state")):
            raise ReentryError(f"BASELINE_STATE_DISAGREEMENT:{qid}")
        scientific_states[qid] = str(row.get("scientific_state"))
        certifications[qid] = [_certification_record(
            row, version=1, supersedes=None, superseded_by=None,
            status="CURRENT", reentry_id=None,
            population_material=_population_material_from_row(row),
            contract_material=_contract_material_from_row(row))]
        finding_history[qid] = [_finding_record(
            finding_row, version=1, supersedes=None, superseded_by=None,
            status="CURRENT", reentry_id=None,
            certification_fingerprint=certifications[qid][0][
                "certification_fingerprint"])]
        scientific_results[qid] = [{
            "scientific_result_id": f"{qid}:r1",
            "question_id": qid,
            "scientific_result_version": 1,
            "result_status": A.FINDING_STATUS_CURRENT,
            "supersedes": None,
            "superseded_by": None,
            "result_fingerprint": str(row.get("result_fingerprint") or ""),
            "scientific_state": str(row.get("scientific_state") or ""),
            "evidence_epoch": str(row.get("evidence_epoch") or ""),
            "evidence_fingerprint": str(row.get("evidence_fingerprint") or ""),
            "reentry_id": None,
        }]

    state: dict[str, Any] = {
        "schema": REENTRY_SCHEMA,
        "stage": STAGE,
        "baseline": {
            "certification_fingerprint": BASELINE_CERTIFICATION_FINGERPRINT,
            "certification_path": str(certification_path),
            "findings_path": str(findings_path),
            "findings_store_fingerprint": findings_store.get("store_fingerprint"),
            "question_count": QUESTION_COUNT,
            "certification_version": 1,
            "finding_version": 1,
            "superseded_certification_count": 0,
            "superseded_finding_count": 0,
            "scientific_states": scientific_states,
            "scientific_state_counts": dict(
                certification.get("scientific_state_counts") or {}),
            "registry_version": REGISTRY_VERSION,
            "registry_fingerprint": registry_fingerprint(),
            "immutable": True,
        },
        "question_certifications": certifications,
        "assured_findings": finding_history,
        "scientific_results": scientific_results,
        "reentry_events": [],
        "reentry_eligibilities": [],
        "reentry_authorizations": [],
        "supersession_events": [],
        "governed_work_items": [],
        "dependency_edges": G.gap_dependency_edges(G.build_store()),
        "q71_started": False,
        "live_or_s3_reads": False,
        "global_certification_mutated": False,
        "v1_findings_mutated": False,
    }
    return _refresh_fingerprints(state)


def _population_material_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_count": row.get("candidate_count"),
        "usable_count": row.get("usable_count"),
        "analytical_count": row.get("analytical_count"),
        "exclusion_count": row.get("exclusion_count"),
        "unexplained_count": row.get("unexplained_count"),
        "population_match": row.get("population_match"),
    }


def _contract_material_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "question_id": row.get("question_id"),
        "canonical_question": row.get("canonical_question"),
        "runner_method": row.get("runner_method"),
        "registry_version": REGISTRY_VERSION,
    }


def _refresh_fingerprints(state: dict[str, Any]) -> dict[str, Any]:
    """Recompute content, view and durable store fingerprints."""
    for rows in state["question_certifications"].values():
        for row in rows:
            row["content_fingerprint"] = _fingerprint({
                key: value for key, value in row.items()
                if key not in {"content_fingerprint", "certification_status",
                               "superseded_by",
                               "superseded_by_certification_id"}})
    for rows in state["assured_findings"].values():
        for row in rows:
            row["content_fingerprint"] = _fingerprint({
                key: value for key, value in row.items()
                if key not in {"content_fingerprint", "finding_status",
                               "superseded_by"}})
    state["counts"] = {
        "questions": len(state["question_certifications"]),
        "certification_versions": sum(
            len(v) for v in state["question_certifications"].values()),
        "certification_superseded": sum(
            1 for rows in state["question_certifications"].values()
            for row in rows
            if row.get("certification_status") == A.FINDING_STATUS_SUPERSEDED),
        "finding_versions": sum(
            len(v) for v in state["assured_findings"].values()),
        "finding_superseded": sum(
            1 for rows in state["assured_findings"].values()
            for row in rows
            if row.get("finding_status") == A.FINDING_STATUS_SUPERSEDED),
        "scientific_result_versions": sum(
            len(v) for v in state.get("scientific_results", {}).values()),
        "reentry_events": len(state["reentry_events"]),
        "reentry_eligibilities": len(state.get("reentry_eligibilities", ())),
        "reentry_eligible": sum(
            1 for row in state.get("reentry_eligibilities", ())
            if row.get("eligibility_state") == ELIGIBLE),
        "reentry_authorizations": len(
            state.get("reentry_authorizations", ())),
        "governed_reentry_executions": sum(
            1 for event in state.get("reentry_events", ())
            if event.get("reentry_authorization_id")
            and event.get("authorization_state") in {
                AUTH_RESULT_RECORDED, AUTH_ASSURANCE_RECORDED, AUTH_PUBLISHED}),
        "legacy_ungoverned_reentry_events": sum(
            1 for event in state.get("reentry_events", ())
            if event.get("governance_classification") == LEGACY_UNGOVERNED),
        "supersession_events": len(state["supersession_events"]),
    }
    projection = effective_current_state(state)
    state["effective_state_fingerprint"] = projection["projection_fingerprint"]
    state["effective_question_count"] = projection["question_count"]
    state["store_fingerprint"] = _fingerprint({
        key: value for key, value in state.items() if key != "store_fingerprint"})
    return state


def current_certification(state: Mapping[str, Any],
                          question_id: str) -> dict[str, Any]:
    """The single CURRENT certification version for one question."""
    qid = str(question_id).strip().upper()
    rows = list(state.get("question_certifications", {}).get(qid, ()))
    current = [row for row in rows
               if row.get("certification_status") == A.FINDING_STATUS_CURRENT]
    if not current:
        raise ReentryError(f"{qid}:ZERO_CURRENT_CERTIFICATION")
    if len(current) > 1:
        raise ReentryError(f"{qid}:DUPLICATE_CURRENT_CERTIFICATION")
    return current[0]


def current_finding(state: Mapping[str, Any],
                    question_id: str) -> dict[str, Any]:
    """The single CURRENT assured finding version for one question."""
    return dict(A.select_current_finding(
        str(question_id).strip().upper(),
        list(state.get("assured_findings", {}).get(
            str(question_id).strip().upper(), ()))))


def _stored_finding(state: Mapping[str, Any],
                    question_id: str) -> dict[str, Any]:
    """The live stored CURRENT finding record (mutable in place)."""
    qid = str(question_id).strip().upper()
    matches = [row for row in state["assured_findings"][qid]
               if row.get("finding_status") == A.FINDING_STATUS_CURRENT]
    if len(matches) != 1:
        raise ReentryError(f"{qid}:DUPLICATE_OR_MISSING_CURRENT_FINDING")
    return matches[0]


def certification_history(state: Mapping[str, Any],
                          question_id: str) -> list[dict[str, Any]]:
    qid = str(question_id).strip().upper()
    return [dict(row) for row in
            sorted(state.get("question_certifications", {}).get(qid, ()),
                   key=lambda item: int(item["certification_version"]))]


def finding_history(state: Mapping[str, Any],
                    question_id: str) -> list[dict[str, Any]]:
    qid = str(question_id).strip().upper()
    return [dict(row) for row in
            sorted(state.get("assured_findings", {}).get(qid, ()),
                   key=lambda item: int(item["finding_version"]))]


def canonical_reentry_authorities() -> tuple[
        SD.SatisfactionDecisionRegistry, dict[str, I.EvidenceSet],
        D.DatasetSnapshotRegistry, dict[str, V.EvidenceEpoch]]:
    """Resolve every persisted authority used by live re-entry."""
    snapshots = D.bootstrap_registry()
    evidence_sets = SD.governed_evidence_map(snapshots)
    satisfaction = SD.SatisfactionDecisionRegistry.load(SD.STATE_PATH)
    # Import lazily: closeout constructs the governed epoch overlay and does
    # not import scientific re-entry itself.
    from research_engine.control_plane import stage4_observation_closeout as C
    matrix = json.loads(C.MATRIX_PATH.read_text(encoding="utf-8"))
    versions = C.build_version_registry(matrix, snapshots)
    epochs = {epoch.epoch_id: epoch for epoch in versions.epochs()}
    return satisfaction, evidence_sets, snapshots, epochs


def _eligibility(
        request: ReentryRequest, *,
        satisfaction_registry: SD.SatisfactionDecisionRegistry,
        evidence_sets: Mapping[str, I.EvidenceSet],
        snapshot_registry: D.DatasetSnapshotRegistry,
        evidence_epochs: Mapping[str, V.EvidenceEpoch],
        evaluated_at: str) -> ReentryEligibility:
    rid = str(request.observation_requirement_id or "")
    decision_id = str(request.satisfaction_decision_id or "")

    def result(state: str, reason: str,
               decision: SD.GovernedSatisfactionDecision | None = None,
               epoch: str | None = None) -> ReentryEligibility:
        return ReentryEligibility.create(
            observation_requirement_id=rid,
            satisfaction_decision_id=decision_id,
            evidence_set_ids=(() if decision is None else
                              decision.evidence_set_ids),
            dataset_snapshot_ids=(() if decision is None else
                                  decision.dataset_snapshot_ids),
            evidence_epoch=epoch, eligibility_state=state,
            eligibility_reason=reason, evaluated_at=evaluated_at)

    try:
        I.validate_requirement_id(rid)
    except I.Stage4IdentityError as exc:
        return result(ELIGIBILITY_INVALID, "INVALID_REQUIREMENT_ID:" + str(exc))
    if not _SDEC_PATTERN.fullmatch(decision_id):
        return result(ELIGIBILITY_INVALID, "MALFORMED_SATISFACTION_DECISION_ID")
    try:
        decision = satisfaction_registry.get(decision_id)
    except SD.SatisfactionDecisionError:
        return result(ELIGIBILITY_INVALID, "UNKNOWN_SATISFACTION_DECISION_ID")
    if decision.observation_requirement_id != rid:
        return result(ELIGIBILITY_INVALID, "SATISFACTION_REQUIREMENT_MISMATCH",
                      decision)
    current = satisfaction_registry.current_for_requirement(rid)
    if current is None or current.satisfaction_decision_id != decision_id:
        return result(NOT_ELIGIBLE, "SATISFACTION_DECISION_SUPERSEDED", decision)
    try:
        satisfaction_registry.verify(
            decision_id, evidence_sets=evidence_sets,
            snapshot_registry=snapshot_registry)
    except (SD.SatisfactionDecisionError, D.DatasetSnapshotError,
            I.Stage4IdentityError) as exc:
        return result(ELIGIBILITY_INVALID,
                      "SATISFACTION_DECISION_VERIFICATION_FAILED:" + str(exc),
                      decision)
    if decision.decision != SD.SATISFIED:
        return result(
            NOT_ELIGIBLE, "SATISFACTION_DECISION_NOT_SATISFIED:"
            + decision.decision, decision)

    epoch_id = str(request.evidence_epoch or "")
    epoch = evidence_epochs.get(epoch_id)
    if epoch is None:
        return result(ELIGIBILITY_INVALID, "EVIDENCE_EPOCH_UNREGISTERED",
                      decision, epoch_id)
    if epoch.status != V.EvidenceEpoch.STATUS_FROZEN:
        return result(ELIGIBILITY_INVALID, "EVIDENCE_EPOCH_NOT_FROZEN",
                      decision, epoch_id)
    if rid not in epoch.observation_requirements:
        return result(ELIGIBILITY_INVALID, "EVIDENCE_EPOCH_REQUIREMENT_MISMATCH",
                      decision, epoch_id)
    if tuple(decision.evidence_set_ids) != (epoch.evidence_set.evidence_set_id,):
        return result(ELIGIBILITY_INVALID, "EVIDENCE_EPOCH_SET_MISMATCH",
                      decision, epoch_id)
    if tuple(decision.dataset_snapshot_ids) != tuple(epoch.dataset_snapshot_ids):
        return result(ELIGIBILITY_INVALID, "EVIDENCE_EPOCH_SNAPSHOT_MISMATCH",
                      decision, epoch_id)
    for snapshot_id in epoch.dataset_snapshot_ids:
        snapshot = snapshot_registry.require(snapshot_id)
        if (snapshot.dataset_name != epoch.dataset
                or snapshot.schema_version != epoch.schema_version
                or snapshot.schema_generation != epoch.schema_generation
                or snapshot.producer_version != epoch.producer_version
                or snapshot.producer_fingerprint != epoch.producer_fingerprint):
            return result(ELIGIBILITY_INVALID,
                          "EVIDENCE_EPOCH_LINEAGE_MISMATCH", decision, epoch_id)
    submitted_fingerprint = str(request.evidence_fingerprint or "")
    if not _SHA256_PATTERN.fullmatch(submitted_fingerprint):
        return result(ELIGIBILITY_INVALID, "EVIDENCE_FINGERPRINT_MALFORMED",
                      decision, epoch_id)
    expected_fingerprint = _fingerprint(epoch.to_dict())
    if submitted_fingerprint != expected_fingerprint:
        return result(ELIGIBILITY_INVALID, "EVIDENCE_FINGERPRINT_MISMATCH",
                      decision, epoch_id)
    return result(ELIGIBLE, "CURRENT_VERIFIED_SATISFIED_DECISION",
                  decision, epoch_id)


def evaluate_reentry_eligibility(
        request: ReentryRequest, *, evaluated_at: str | None = None,
        satisfaction_registry: SD.SatisfactionDecisionRegistry | None = None,
        evidence_sets: Mapping[str, I.EvidenceSet] | None = None,
        snapshot_registry: D.DatasetSnapshotRegistry | None = None,
        evidence_epochs: Mapping[str, V.EvidenceEpoch] | None = None,
        ) -> ReentryEligibility:
    """Derive eligibility from canonical authorities, never caller labels."""
    if any(value is None for value in (
            satisfaction_registry, evidence_sets, snapshot_registry,
            evidence_epochs)):
        canonical = canonical_reentry_authorities()
        if satisfaction_registry is None:
            satisfaction_registry = canonical[0]
        if evidence_sets is None:
            evidence_sets = canonical[1]
        if snapshot_registry is None:
            snapshot_registry = canonical[2]
        if evidence_epochs is None:
            evidence_epochs = canonical[3]
    return _eligibility(
        request, satisfaction_registry=satisfaction_registry,
        evidence_sets=dict(evidence_sets), snapshot_registry=snapshot_registry,
        evidence_epochs=dict(evidence_epochs),
        evaluated_at=_utc_now(evaluated_at))


def _fixture_reentry_authorities(
        *, requirement_id: str = "OR-01", fixture_id: str = "FIXTURE",
        observed: int = 1, required: int = 1,
        supersedes: str | None = None,
        ) -> tuple[SD.SatisfactionDecisionRegistry,
                   dict[str, I.EvidenceSet], D.DatasetSnapshotRegistry,
                   dict[str, V.EvidenceEpoch],
                   SD.GovernedSatisfactionDecision]:
    """Isolated positive-path authority used only by tests/demonstrations."""
    marker = _fingerprint({"fixture_id": fixture_id})[:16]
    producer_fingerprint = _fingerprint({"producer": fixture_id})
    snapshot = D.freeze_population(
        dataset_name="shadow_runtime", schema_version="shadow_runtime_v1",
        schema_generation=1, generation_state=D.GENERATION_CONFIRMED,
        generation_evidence="isolated re-entry fixture",
        identity_grain="one synthetic governed event",
        identity_grain_evidence="isolated re-entry fixture",
        producer_version="fixture-producer-v1",
        producer_fingerprint=producer_fingerprint,
        source_boundaries=("fixture=" + marker,),
        population_filters=("scope=isolated-test-only",),
        population_class=D.GOVERNED_REQUIREMENT_POPULATION,
        observation_requirements=(requirement_id,),
        records=({"fixture": marker},),
        frozen_at="2026-09-30T00:00:00Z")
    epoch = V.EvidenceEpoch(
        epoch_id="FIXTURE-EPOCH-" + marker.upper(),
        dataset="shadow_runtime", dataset_version="shadow_runtime_v1",
        schema_generation=1, producer_version="fixture-producer-v1",
        producer_fingerprint=producer_fingerprint,
        collection_start="2026-09-30T00:00:00Z",
        observation_requirements=(requirement_id,), questions=(),
        canonical_identities=("fixture_id",),
        evidence_contract_versions={requirement_id: "fixture-v1"},
        predecessor_epoch_id=None, status=V.EvidenceEpoch.STATUS_FROZEN,
        dataset_snapshot_ids=(snapshot.dataset_snapshot_id,))
    evidence = epoch.evidence_set
    snapshots = D.DatasetSnapshotRegistry((snapshot,))
    policy = SD.ThresholdPolicy.create(
        observation_requirement_id=requirement_id,
        rules=({"threshold_id": requirement_id + "-FIXTURE-COUNT",
                "field": "sample_count", "operator": ">=",
                "value": required},),
        authority="isolated test fixture", source="isolated test fixture")
    decision = SD.evaluate(
        observation_requirement_id=requirement_id,
        evidence_set_ids=(evidence.evidence_set_id,),
        threshold_policy=policy,
        evaluation_metrics={"sample_count": observed},
        evidence_sets={evidence.evidence_set_id: evidence},
        snapshot_registry=snapshots,
        evaluated_at="2026-09-30T00:00:00Z",
        supersedes_satisfaction_decision_id=supersedes)
    registry = SD.SatisfactionDecisionRegistry(policies=(policy,))
    registry.register(decision)
    return (registry, {evidence.evidence_set_id: evidence}, snapshots,
            {epoch.epoch_id: epoch}, decision)


def authorize_reentry(
        state: Mapping[str, Any], request: ReentryRequest, *,
        now: str | None = None,
        gap_store: Mapping[str, Any] | None = None,
        satisfaction_registry: SD.SatisfactionDecisionRegistry | None = None,
        evidence_sets: Mapping[str, I.EvidenceSet] | None = None,
        snapshot_registry: D.DatasetSnapshotRegistry | None = None,
        evidence_epochs: Mapping[str, V.EvidenceEpoch] | None = None,
        ) -> dict[str, Any]:
    """Authorize one governed re-entry, or fail closed with no partial state.

    Execution authority exists only after a governed trigger, a re-entry-ready
    work item, satisfied dependencies and pinned contracts have all been
    proven against the current effective state.  A code or data change alone
    can never update truth.
    """
    validate_state(state)
    _validate_request_against_state(state, request)
    eligibility = evaluate_reentry_eligibility(
        request, evaluated_at=now,
        satisfaction_registry=satisfaction_registry,
        evidence_sets=evidence_sets, snapshot_registry=snapshot_registry,
        evidence_epochs=evidence_epochs)
    if eligibility.eligibility_state != ELIGIBLE:
        raise ReentryError(
            "REENTRY_NOT_ELIGIBLE:" + eligibility.eligibility_state + ":"
            + eligibility.eligibility_reason)
    work_item_id, work_item = _resolve_work_item(request, state, gap_store)
    dependencies = dependency_state_for(state, request.question_id)
    if request.dependency_state and request.dependency_state != dependencies:
        raise ReentryError(f"{request.question_id}:DEPENDENCY_STATE_STALE")
    unmet = sorted(name for name, snapshot in dependencies.items()
                   if not snapshot["satisfied"])
    if unmet:
        raise ReentryError(
            f"{request.question_id}:DEPENDENCY_UNRESOLVED:{','.join(unmet)}")
    existing_authorization = next((row for row in state.get(
        "reentry_authorizations", ()) if row.get("satisfaction_decision_id") ==
        request.satisfaction_decision_id), None)
    if existing_authorization is not None:
        if (str(existing_authorization.get("reentry_id")) == request.reentry_id
                and any(str(event.get("reentry_id")) == request.reentry_id
                        for event in state.get("reentry_events", ()))):
            return deepcopy(dict(state))
        raise ReentryError("SATISFACTION_DECISION_ALREADY_AUTHORIZED")
    # A re-entry id is a permanent audit identity.  Refuse to reuse one that
    # already exists - in particular a legacy pre-Refinement-4 id, whose
    # event is auditable history and must never be shadowed by new work.
    if any(str(event.get("reentry_id")) == request.reentry_id
           for event in state.get("reentry_events", ())):
        raise ReentryError(f"REENTRY_ID_ALREADY_USED:{request.reentry_id}")
    authorized_at = _utc_now(now)
    authorization = ReentryAuthorization.create(
        eligibility=eligibility, reentry_id=request.reentry_id,
        authorized_at=authorized_at)
    nxt = deepcopy(dict(state))
    nxt.setdefault("reentry_eligibilities", []).append(eligibility.to_dict())
    nxt.setdefault("reentry_authorizations", []).append(authorization.to_dict())
    nxt["reentry_events"].append({
        "reentry_id": str(request.reentry_id),
        "question_id": str(request.question_id),
        "reason": str(request.reason),
        "trigger_type": str(request.trigger_type),
        "requested_at": str(request.requested_at),
        "authorized_at": authorized_at,
        "authorization_state": AUTH_AUTHORIZED,
        "governance_classification": "GOVERNED_DECISION_AUTHORIZED",
        "observation_requirement_id": eligibility.observation_requirement_id,
        "satisfaction_decision_id": eligibility.satisfaction_decision_id,
        "reentry_eligibility_id": eligibility.reentry_eligibility_id,
        "reentry_authorization_id": authorization.reentry_authorization_id,
        "work_item_id": work_item_id,
        "work_item_status": str(work_item.get("status")),
        "gap_type": str(work_item.get("gap_type")),
        "previous_finding_id": str(request.previous_finding_id),
        "previous_finding_version": int(request.previous_finding_version),
        "previous_certification_fingerprint": str(
            request.previous_certification_fingerprint),
        "question_contract_id": str(request.question_contract_id),
        "question_contract_version": int(request.question_contract_version),
        "question_contract_fingerprint": str(
            request.question_contract_fingerprint),
        "evidence_contract_id": str(request.evidence_contract_id),
        "evidence_contract_version": str(request.evidence_contract_version),
        "evidence_contract_fingerprint": str(
            request.evidence_contract_fingerprint),
        "runner_id": str(request.runner_id),
        "runner_contract_fingerprint": str(request.runner_contract_fingerprint),
        "evidence_epoch": str(request.evidence_epoch),
        "evidence_fingerprint": str(request.evidence_fingerprint),
        "registry_version": str(request.registry_version),
        "registry_fingerprint": str(request.registry_fingerprint),
        "contract_evolution": request.contract_evolution,
        "dependency_state": dependencies,
        "scope": [str(request.question_id)],
        "request_fingerprint": request.fingerprint(),
    })
    return _refresh_fingerprints(nxt)


def plan_scoped_run(state: Mapping[str, Any], reentry_id: str, *,
                    additional_question_ids: Sequence[str] = ()) -> list[str]:
    """The exact, ordered, dependency-necessary question set to execute.

    Ordinary re-entry runs one question.  When a governed dependency requires
    a prerequisite in the same governed run, the ordered set widens only as
    far as dependency necessity demands - never to the full 70-question bank.
    """
    event = _event(state, reentry_id)
    if str(event.get("authorization_state")) != AUTH_AUTHORIZED:
        raise ReentryError(f"{reentry_id}:NOT_AUTHORIZED")
    if not str(event.get("reentry_authorization_id") or ""):
        raise ReentryError(f"{reentry_id}:LEGACY_UNGOVERNED_NOT_EXECUTABLE")
    requested = [str(event["question_id"])]
    for qid in additional_question_ids:
        qid = str(qid).strip().upper()
        if qid not in QUESTION_ID_SET:
            raise ReentryError(f"{qid}:UNKNOWN_QUESTION")
        if qid not in requested:
            requested.append(qid)
    ordered: list[str] = []
    for qid in requested:
        for edge in state.get("dependency_edges", ()):
            if str(edge.get("dependent_question_id")) == qid:
                prerequisite = str(edge["prerequisite_question_id"])
                if prerequisite in requested and prerequisite not in ordered:
                    ordered.append(prerequisite)
        if qid not in ordered:
            ordered.append(qid)
    return ordered


def execute_scoped_run(
        state: Mapping[str, Any], reentry_id: str, *,
        question_ids: Sequence[str] | None = None,
        runner: Callable[..., Mapping[str, Any]] | None = None,
        now: str | None = None) -> dict[str, Any]:
    """Run ONLY the scoped questions through the frozen-evidence machinery.

    The default runner is the question-scoped historical-pass entry point, so
    frozen evidence, exact governed population resolution, report authority
    and deterministic fingerprints are reused unchanged.  No S3/live fallback
    and no unrelated question execution is possible here.
    """
    validate_state(state)
    scope = plan_scoped_run(
        state, reentry_id,
        additional_question_ids=() if question_ids is None else question_ids)
    event = _event(state, reentry_id)
    if runner is None:
        dependency_finding = None
        dependency = event.get("dependency_state", {}).get("L6")
        if isinstance(dependency, Mapping) and dependency.get("satisfied"):
            dependency_finding = current_finding(state, "L6")
        coordinator = run_questions(
            scope, registry_version=str(event["registry_version"]),
            runner_context={"dependency_finding": dependency_finding},
            reports_dir=Path("analysis/reports/reentry") / str(reentry_id))
    else:
        coordinator = runner(scope)
    manifest = coordinator.scoped_manifest()
    manifest_path = None
    if runner is None:
        manifest_path = Path("analysis/assurance/reentry") / (
            str(reentry_id) + "_scientific_manifest.json")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    executed = sorted(manifest.get("scope", ()))
    if executed != sorted(coordinator.scope):
        raise ReentryError(f"{reentry_id}:SCOPED_EXECUTION_MISMATCH")
    if not set(executed).issubset(set(scope)):
        raise ReentryError(f"{reentry_id}:EXECUTED_OUT_OF_SCOPE")
    rows = {str(row["question_id"]): dict(row)
            for row in manifest.get("questions", ())}
    missing = [qid for qid in scope if qid not in rows]
    if missing:
        raise ReentryError(
            f"{reentry_id}:SCOPED_RESULT_MISSING:{','.join(missing)}")
    from research_engine.control_plane.stage4_implementation_repairs import GOVERNED_USABLE
    for qid in scope:
        if runner is None and qid in GOVERNED_USABLE:
            actual = rows[qid].get("runner_analytical_population")
            if actual != GOVERNED_USABLE[qid]:
                raise ReentryError(
                    f"{qid}:RUNNER_ANALYTICAL_POPULATION_MISMATCH:"
                    f"{actual}!={GOVERNED_USABLE[qid]}")
            if rows[qid].get("runner_failed"):
                raise ReentryError(f"{qid}:RUNNER_FAILED")
            if rows[qid].get("scientific_state") == "IMPLEMENTATION_BLOCKED":
                raise ReentryError(f"{qid}:IMPLEMENTATION_STILL_BLOCKED")
    for qid, row in rows.items():
        report = row.get("current_report_path")
        if report:
            owner = canonical_report_owner(str(report))
            if owner is None and str(event["registry_version"]) != REGISTRY_VERSION:
                from research_engine.control_plane.stage4_registry_successor import canonical_report_owner as successor_owner
                owner = successor_owner(str(report))
            if owner is not None and owner not in {
                    qid, scientific_owner_id(
                        qid, registry_version=str(event["registry_version"]))}:
                raise ReentryError(f"{qid}:REPORT_NOT_OWNED:{report}")
    nxt = deepcopy(dict(state))
    target = _event(nxt, reentry_id)
    target["scientific_result"] = {
        "scoped_manifest_fingerprint": manifest["fingerprint"],
        "scoped_manifest_path": str(manifest_path) if manifest_path else "",
        "scope": scope,
        "rows": {qid: {
            "scientific_state": rows[qid]["scientific_state"],
            "result_fingerprint": rows[qid]["result_fingerprint"],
            "usable_historical_population": rows[qid][
                "usable_historical_population"],
            "current_report_path": rows[qid]["current_report_path"],
            "runner_executed": rows[qid].get("runner_executed"),
            "runner_analytical_population": rows[qid].get(
                "runner_analytical_population"),
        } for qid in scope},
        "executed_question_ids": executed,
        "unrelated_questions_executed": 0,
        "live_or_s3_reads": False,
    }
    target["authorization_state"] = AUTH_RESULT_RECORDED
    target["executed_at"] = _utc_now(now)
    return _refresh_fingerprints(nxt)


def _event(nxt: Mapping[str, Any], reentry_id: str) -> dict[str, Any]:
    event = next((item for item in nxt["reentry_events"]
                  if str(item["reentry_id"]) == str(reentry_id)), None)
    if event is None:
        raise ReentryError(f"REENTRY_NOT_FOUND:{reentry_id}")
    return event


def _validate_request_against_state(
        state: Mapping[str, Any], request: ReentryRequest) -> None:
    """Every fail-closed precondition for executing a scoped rerun."""
    qid = str(request.question_id).strip().upper()
    if qid not in QUESTION_ID_SET:
        raise ReentryError(f"{qid}:UNKNOWN_QUESTION")
    if str(request.trigger_type) not in ALLOWED_TRIGGERS:
        raise ReentryError(f"{qid}:TRIGGER_NOT_GOVERNED:{request.trigger_type}")
    if str(request.authorization_state) != AUTH_REQUESTED:
        raise ReentryError(f"{qid}:REQUEST_ALREADY_AUTHORIZED")
    try:
        I.validate_requirement_id(request.observation_requirement_id)
    except I.Stage4IdentityError as exc:
        raise ReentryError(str(exc)) from exc
    if not str(request.satisfaction_decision_id or ""):
        raise ReentryError(f"{qid}:SATISFACTION_DECISION_ID_REQUIRED")
    if str(state.get("baseline", {}).get("certification_fingerprint")
           ) != BASELINE_CERTIFICATION_FINGERPRINT:
        raise ReentryError("BASELINE_CERTIFICATION_FINGERPRINT_CHANGED")
    pins = question_contract_pins(
        qid, registry_version=str(request.registry_version))
    evolved = bool(request.contract_evolution)
    if int(pins["question_contract_version"]) > 1:
        if not evolved:
            raise ReentryError(f"{qid}:CONTRACT_EVOLUTION_RECORD_REQUIRED")
        evolution = dict(request.contract_evolution or {})
        if (int(evolution.get("from_version", 0)) != 1
                or int(evolution.get("to_version", 0)) != int(
                    pins["question_contract_version"])
                or not evolution.get("governance_ref")):
            raise ReentryError(f"{qid}:CONTRACT_EVOLUTION_RECORD_INVALID")
    if str(request.question_contract_id) != pins["question_contract_id"]:
        raise ReentryError(f"{qid}:QUESTION_CONTRACT_ID_MISMATCH")
    if int(request.question_contract_version) != int(
            pins["question_contract_version"]):
        raise ReentryError(f"{qid}:QUESTION_CONTRACT_VERSION_MISMATCH")
    if str(request.question_contract_fingerprint) != pins[
            "question_contract_fingerprint"] and not evolved:
        raise ReentryError(f"{qid}:QUESTION_CONTRACT_FINGERPRINT_MISMATCH")
    if str(request.evidence_contract_fingerprint) != pins[
            "evidence_contract_fingerprint"] and not evolved:
        raise ReentryError(f"{qid}:EVIDENCE_CONTRACT_FINGERPRINT_MISMATCH")
    if str(request.runner_id) != pins["runner_id"]:
        raise ReentryError(f"{qid}:RUNNER_IDENTITY_NOT_AUTHORIZED")
    if str(request.runner_contract_fingerprint) != pins[
            "runner_contract_fingerprint"] and not evolved:
        raise ReentryError(f"{qid}:RUNNER_CONTRACT_FINGERPRINT_MISMATCH")
    if str(request.registry_fingerprint) != pins["registry_fingerprint"]:
        raise ReentryError(f"{qid}:REGISTRY_FINGERPRINT_MISMATCH")
    if str(request.evidence_epoch).upper() in {"", "LEGACY"}:
        raise ReentryError(f"{qid}:EVIDENCE_EPOCH_INVALID")
    if not str(request.evidence_fingerprint).strip():
        raise ReentryError(f"{qid}:EVIDENCE_FINGERPRINT_MISSING")
    certification = current_certification(state, qid)
    finding = current_finding(state, qid)
    if int(request.previous_finding_version) != int(finding["finding_version"]):
        raise ReentryError(f"{qid}:PREVIOUS_FINDING_VERSION_MISMATCH")
    if str(request.previous_finding_id) != str(finding["finding_id"]):
        raise ReentryError(f"{qid}:PREVIOUS_FINDING_ID_MISMATCH")
    if str(request.previous_certification_fingerprint) != str(
            certification["certification_fingerprint"]):
        raise ReentryError(f"{qid}:PREVIOUS_CERTIFICATION_FINGERPRINT_MISMATCH")
    if str(finding.get("scientific_state")) not in SCIENTIFIC_STATES:
        raise ReentryError(f"{qid}:SCIENTIFIC_STATE_UNKNOWN")


def _dependency_requirements(state: Mapping[str, Any],
                             question_id: str) -> list[dict[str, Any]]:
    qid = str(question_id).strip().upper()
    return [dict(edge) for edge in state.get("dependency_edges", ())
            if str(edge.get("dependent_question_id")) == qid]


def dependency_state_for(state: Mapping[str, Any],
                         question_id: str) -> dict[str, Any]:
    """Snapshot the CURRENT certified state of every prerequisite."""
    snapshot: dict[str, Any] = {}
    for edge in _dependency_requirements(state, question_id):
        prerequisite = str(edge["prerequisite_question_id"])
        certification = current_certification(state, prerequisite)
        finding = current_finding(state, prerequisite)
        snapshot[prerequisite] = {
            "prerequisite_question_id": prerequisite,
            "certification_version": int(certification["certification_version"]),
            "certification_status": certification["certification_status"],
            "scientific_state": certification["scientific_state"],
            "assurance_status": certification["assurance_status"],
            "finding_version": int(finding["finding_version"]),
            "requirement": dict(DEPENDENCY_REQUIREMENT),
            "satisfied": _dependency_satisfied(edge, certification),
        }
    return snapshot


def _dependency_satisfied(edge: Mapping[str, Any],
                          certification: Mapping[str, Any]) -> bool:
    requirement = dict(DEPENDENCY_REQUIREMENT)
    if str(certification.get("certification_status")) != A.FINDING_STATUS_CURRENT:
        return False
    if str(certification.get("assurance_status")) != requirement[
            "required_assurance_status"]:
        return False
    if int(certification.get("certification_version") or 0) < int(
            requirement["min_certification_version"]):
        return False
    if str(certification.get("scientific_state")) in requirement[
            "disallowed_scientific_states"]:
        return False
    return True


def _resolve_work_item(
        request: ReentryRequest, state: Mapping[str, Any],
        gap_store: Mapping[str, Any] | None = None,
) -> tuple[str, Mapping[str, Any]]:
    """Locate the governed work item and check it reached re-entry readiness."""
    if not str(request.gap_work_item_id or "").strip():
        raise ReentryError(f"{request.question_id}:WORK_ITEM_REQUIRED")
    wid = str(request.gap_work_item_id)
    store = G.build_store() if gap_store is None else gap_store
    try:
        item = G.get_work_item(store, wid)
    except G.GapGovernanceError as error:
        raise ReentryError(f"{request.question_id}:{error}")
    if str(request.trigger_type) in TRIGGER_GAP_TYPE:
        if str(item.get("gap_type")) != TRIGGER_GAP_TYPE[str(request.trigger_type)]:
            raise ReentryError(f"{wid}:TRIGGER_GAP_TYPE_MISMATCH")
        if request.question_id not in [
                str(value) for value in item.get("affected_question_ids", ())]:
            raise ReentryError(f"{wid}:WORK_ITEM_DOES_NOT_AFFECT_QUESTION")
    if str(item.get("status")) not in G.reentry_ready_statuses():
        raise ReentryError(
            f"{wid}:GAP_NOT_REENTRY_READY:{item.get('status')}")
    if state.get("q71_started") is not False:
        raise ReentryError("Q71_NOT_STARTED")
    return wid, item


def validate_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed on every certification/finding history invariant."""
    if state.get("schema") != REENTRY_SCHEMA:
        raise ReentryError("REENTRY_STATE_SCHEMA_MISMATCH")
    for qid in QUESTION_IDS:
        if qid not in state.get("question_certifications", {}):
            raise ReentryError(f"{qid}:MISSING_CERTIFICATION_HISTORY")
        if qid not in state.get("assured_findings", {}):
            raise ReentryError(f"{qid}:MISSING_FINDING_HISTORY")
        certifications = sorted(
            state["question_certifications"][qid],
            key=lambda item: int(item["certification_version"]))
        versions = [int(row["certification_version"]) for row in certifications]
        if versions != list(range(1, len(certifications) + 1)):
            raise ReentryError(f"{qid}:CERTIFICATION_VERSION_REGRESSION")
        current = [row for row in certifications
                   if row.get("certification_status") == A.FINDING_STATUS_CURRENT]
        if len(current) != 1:
            raise ReentryError(f"{qid}:DUPLICATE_OR_MISSING_CURRENT_CERTIFICATION")
        if int(current[0]["certification_version"]) != versions[-1]:
            raise ReentryError(f"{qid}:CURRENT_CERTIFICATION_NOT_LATEST")
        for index, row in enumerate(certifications, start=1):
            expected_supersedes = (None if index == 1
                                   else certification_identity(qid, index - 1))
            if row.get("supersedes_certification_id") != expected_supersedes:
                raise ReentryError(f"{qid}:CERTIFICATION_PREDECESSOR_MISSING")
            expected_successor = (None if index == len(certifications)
                                  else certification_identity(qid, index + 1))
            if row.get("superseded_by") != expected_successor:
                raise ReentryError(f"{qid}:CERTIFICATION_SUPERSESSION_BROKEN")
        # The finding history validator owns cycle/duplicate/version checks.
        A.validate_finding_history(qid, state["assured_findings"][qid])
        latest_finding = current_finding(state, qid)
        if str(latest_finding.get("certification_fingerprint")) != str(
                current[0].get("certification_fingerprint")):
            raise ReentryError(f"{qid}:FINDING_CERTIFICATION_FINGERPRINT_MISMATCH")
        for row in state["assured_findings"][qid]:
            if str(row.get("scientific_state")) not in SCIENTIFIC_STATES:
                raise ReentryError(f"{qid}:SCIENTIFIC_STATE_UNKNOWN")
        result_rows = state.get("scientific_results", {}).get(qid)
        if result_rows is not None:
            result_versions = [int(row["scientific_result_version"])
                               for row in result_rows]
            if result_versions != list(range(1, len(result_rows) + 1)):
                raise ReentryError(f"{qid}:SCIENTIFIC_RESULT_VERSION_REGRESSION")
            current_results = [row for row in result_rows
                               if row.get("result_status") == A.FINDING_STATUS_CURRENT]
            if len(current_results) != 1 or result_versions[-1] != int(
                    current_results[0]["scientific_result_version"]):
                raise ReentryError(f"{qid}:DUPLICATE_OR_MISSING_CURRENT_SCIENTIFIC_RESULT")
    eligibilities: dict[str, ReentryEligibility] = {}
    for row in state.get("reentry_eligibilities", ()):
        record = ReentryEligibility.from_dict(row)
        if record.reentry_eligibility_id in eligibilities:
            raise ReentryError("DUPLICATE_REENTRY_ELIGIBILITY_ID")
        eligibilities[record.reentry_eligibility_id] = record
    authorizations: dict[str, ReentryAuthorization] = {}
    authorized_decisions: set[str] = set()
    for row in state.get("reentry_authorizations", ()):
        record = ReentryAuthorization.from_dict(row)
        if record.reentry_authorization_id in authorizations:
            raise ReentryError("DUPLICATE_REENTRY_AUTHORIZATION_ID")
        if record.satisfaction_decision_id in authorized_decisions:
            raise ReentryError("DUPLICATE_DECISION_AUTHORIZATION")
        eligibility = eligibilities.get(record.reentry_eligibility_id)
        if eligibility is None or eligibility.eligibility_state != ELIGIBLE:
            raise ReentryError("AUTHORIZATION_WITHOUT_ELIGIBILITY")
        if (eligibility.satisfaction_decision_id !=
                record.satisfaction_decision_id):
            raise ReentryError("AUTHORIZATION_ELIGIBILITY_MISMATCH")
        authorizations[record.reentry_authorization_id] = record
        authorized_decisions.add(record.satisfaction_decision_id)
    seen_reentry_ids: set[str] = set()
    for event in state.get("reentry_events", ()):
        reentry_id = str(event.get("reentry_id") or "")
        if not reentry_id or reentry_id in seen_reentry_ids:
            raise ReentryError("DUPLICATE_OR_MISSING_REENTRY_ID")
        seen_reentry_ids.add(reentry_id)
        if str(event.get("authorization_state")) not in ALLOWED_AUTHORIZATION_STATES:
            raise ReentryError(
                f"{event.get('reentry_id')}:AUTHORIZATION_STATE_UNKNOWN")
        authorization_id = str(event.get("reentry_authorization_id") or "")
        if authorization_id:
            authorization = authorizations.get(authorization_id)
            if authorization is None:
                raise ReentryError(reentry_id + ":AUTHORIZATION_RECORD_MISSING")
            if (authorization.reentry_id != reentry_id
                    or authorization.satisfaction_decision_id != str(
                        event.get("satisfaction_decision_id"))):
                raise ReentryError(reentry_id + ":AUTHORIZATION_EVENT_MISMATCH")
        elif event.get("governance_classification") != LEGACY_UNGOVERNED:
            raise ReentryError(reentry_id + ":UNCLASSIFIED_LEGACY_REENTRY")
    if state.get("q71_started") is not False:
        raise ReentryError("Q71_MUST_NOT_BE_STARTED")
    if state.get("live_or_s3_reads") is not False:
        raise ReentryError("LIVE_OR_S3_READS_NOT_PERMITTED")
    expected = _fingerprint({
        key: value for key, value in state.items() if key != "store_fingerprint"})
    if state.get("store_fingerprint") != expected:
        raise ReentryError("STORE_FINGERPRINT_MISMATCH")
    return dict(state)


def migrate_legacy_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Add R4 authority without inventing lineage for historical events."""
    if state.get("schema") == REENTRY_SCHEMA:
        return dict(state)
    if state.get("schema") != 1:
        raise ReentryError("REENTRY_STATE_SCHEMA_MISMATCH")
    nxt = deepcopy(dict(state))
    nxt["schema"] = REENTRY_SCHEMA
    nxt["reentry_authorizations"] = []
    for event in nxt.get("reentry_events", ()):
        event["governance_classification"] = LEGACY_UNGOVERNED
        event["legacy_resolution_state"] = "LEGACY_UNRESOLVED"
        # Deliberately no satisfaction/eligibility/authorization identity:
        # the exact historical decision material did not exist.

    satisfaction, evidence, snapshots, _epochs = canonical_reentry_authorities()
    eligibility_rows: list[dict[str, Any]] = []
    for rid in I.CANONICAL_REQUIREMENT_IDS:
        decision = satisfaction.current_for_requirement(rid)
        if decision is None:
            continue
        try:
            satisfaction.verify(
                decision.satisfaction_decision_id,
                evidence_sets=evidence, snapshot_registry=snapshots)
            reason = ("SATISFACTION_DECISION_NOT_SATISFIED:"
                      + decision.decision if decision.decision != SD.SATISFIED
                      else "EVIDENCE_EPOCH_CONTEXT_REQUIRED")
            state_name = NOT_ELIGIBLE
        except SD.SatisfactionDecisionError as exc:
            reason = "SATISFACTION_DECISION_VERIFICATION_FAILED:" + str(exc)
            state_name = ELIGIBILITY_INVALID
        eligibility_rows.append(ReentryEligibility.create(
            observation_requirement_id=rid,
            satisfaction_decision_id=decision.satisfaction_decision_id,
            evidence_set_ids=decision.evidence_set_ids,
            dataset_snapshot_ids=decision.dataset_snapshot_ids,
            evidence_epoch=None, eligibility_state=state_name,
            eligibility_reason=reason,
            evaluated_at="2026-09-30T00:00:00Z").to_dict())
    nxt["reentry_eligibilities"] = eligibility_rows
    return _refresh_fingerprints(nxt)


def load_state(path: Path | str = STATE_PATH) -> dict[str, Any]:
    state = _read_json(Path(path))
    if state.get("schema") == 1:
        state = migrate_legacy_state(state)
    return validate_state(state)


def save_state(state: Mapping[str, Any],
               path: Path | str = STATE_PATH) -> Path:
    """Atomically persist the durable re-entry authority."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return target


def ensure_bootstrapped(path: Path | str = STATE_PATH,
                        *, force: bool = False) -> dict[str, Any]:
    """Load the durable authority, bootstrapping V1 history when absent."""
    target = Path(path)
    if target.is_file() and not force:
        return load_state(target)
    state = bootstrap_state()
    validate_state(state)
    save_state(state, target)
    return state


def effective_current_state(state: Mapping[str, Any],
                            *, validate: bool = True) -> dict[str, Any]:
    """Deterministic 70-question projection of the current research state.

    This is baseline certification + latest per-question certification
    overlays + latest CURRENT assured finding.  It never regenerates the
    historical snapshot and never touches unaffected questions.
    """
    rows: list[dict[str, Any]] = []
    for qid in QUESTION_IDS:
        certification = current_certification(state, qid)
        finding = current_finding(state, qid)
        if str(certification.get("scientific_state")) != str(
                finding.get("scientific_state")):
            raise ReentryError(f"{qid}:CERTIFICATION_FINDING_STATE_DISAGREEMENT")
        if str(certification.get("certification_fingerprint")) != str(
                finding.get("certification_fingerprint")):
            raise ReentryError(f"{qid}:CERTIFICATION_FINDING_LINK_MISSING")
        rows.append({
            "question_id": qid,
            "certification_id": certification["certification_id"],
            "certification_version": int(certification["certification_version"]),
            "certification_status": certification["certification_status"],
            "finding_id": finding["finding_id"],
            "finding_version": int(finding["finding_version"]),
            "finding_status": finding["finding_status"],
            "scientific_state": certification["scientific_state"],
            "assurance_status": certification["assurance_status"],
            "scientific_result_fingerprint": certification[
                "scientific_result_fingerprint"],
            "evidence_fingerprint": certification["evidence_fingerprint"],
            "evidence_epoch": certification["evidence_epoch"],
            "certification_fingerprint": certification[
                "certification_fingerprint"],
            "reentry_id": certification.get("reentry_id"),
        })
    if len(rows) != QUESTION_COUNT:
        raise ReentryError("EFFECTIVE_STATE_NOT_EXACTLY_70")
    versions: dict[str, int] = {}
    for row in rows:
        key = str(row["certification_version"])
        versions[key] = versions.get(key, 0) + 1
    material = {
        "schema": REENTRY_SCHEMA,
        "stage": "STAGE4_EFFECTIVE_CURRENT_STATE",
        "baseline_certification_fingerprint": BASELINE_CERTIFICATION_FINGERPRINT,
        "question_count": len(rows),
        "questions": rows,
        "certification_version_counts": dict(sorted(versions.items())),
        "at_version_1": versions.get("1", 0),
        "at_version_2": versions.get("2", 0),
        "q71_started": bool(state.get("q71_started", False)),
        "live_or_s3_reads": bool(state.get("live_or_s3_reads", False)),
    }
    return {**material, "projection_fingerprint": _fingerprint(material)}



def _certification_record(
        row: Mapping[str, Any], *, version: int, supersedes: str | None,
        superseded_by: str | None, status: str, reentry_id: str | None,
        population_material: Mapping[str, Any],
        contract_material: Mapping[str, Any]) -> dict[str, Any]:
    qid = str(row["question_id"])
    record = {
        "certification_id": certification_identity(qid, version),
        "question_id": qid,
        "certification_version": int(version),
        "previous_certification_version": None if version <= 1 else int(version - 1),
        "supersedes_certification_id": supersedes,
        "superseded_by_certification_id": superseded_by,
        "certification_status": status,
        "superseded_by": superseded_by,
        "scientific_result_fingerprint": str(row.get("result_fingerprint") or ""),
        "evidence_fingerprint": str(row.get("evidence_fingerprint") or ""),
        "evidence_epoch": str(row.get("evidence_epoch") or ""),
        "population_fingerprint": population_fingerprint(population_material),
        "population_material": dict(population_material),
        "contract_fingerprint": _fingerprint(contract_material),
        "contract_material": dict(contract_material),
        "assurance_status": str(row.get("assurance_status") or "FAILED"),
        "scientific_state": str(row.get("scientific_state") or ""),
        "checks": dict(row.get("checks") or {}),
        "reentry_id": reentry_id,
        "certified_at": str(row.get("certified_at") or ""),
    }
    record["certification_fingerprint"] = _fingerprint(record)
    record["content_fingerprint"] = _fingerprint({
        key: value for key, value in record.items()
        if key not in {"content_fingerprint", "certification_fingerprint"}})
    return record


def publish_new_version(
        state: Mapping[str, Any], reentry_id: str, *,
        now: str | None = None) -> dict[str, Any]:
    """Publish the new certification and assured-finding version(s).

    Requires the full governed sequence: an authorized re-entry, a recorded
    scoped scientific result and a VERIFIED scoped assurance recertification.
    Superseded versions keep their content; only lifecycle metadata changes,
    and every supersession is also recorded append-only.
    """
    validate_state(state)
    event = _event(state, reentry_id)
    if str(event.get("authorization_state")) != AUTH_ASSURANCE_RECORDED:
        raise ReentryError(f"{reentry_id}:ASSURANCE_NOT_RECORDED")
    if str(event["assurance"]["assurance_status"]) != "VERIFIED":
        raise ReentryError(f"{reentry_id}:ASSURANCE_NOT_VERIFIED")
    nxt = deepcopy(dict(state))
    if "scientific_results" not in nxt:
        nxt["scientific_results"] = {}
        for existing_qid, certification_rows in nxt["question_certifications"].items():
            nxt["scientific_results"][existing_qid] = [{
                "scientific_result_id": f"{existing_qid}:r{row['certification_version']}",
                "question_id": existing_qid,
                "scientific_result_version": int(row["certification_version"]),
                "result_status": row["certification_status"],
                "supersedes": (None if int(row["certification_version"]) == 1 else
                               f"{existing_qid}:r{int(row['certification_version']) - 1}"),
                "superseded_by": (None if row["certification_status"] == A.FINDING_STATUS_CURRENT else
                                  f"{existing_qid}:r{int(row['certification_version']) + 1}"),
                "result_fingerprint": row["scientific_result_fingerprint"],
                "scientific_state": row["scientific_state"],
                "evidence_epoch": row["evidence_epoch"],
                "evidence_fingerprint": row["evidence_fingerprint"],
                "reentry_id": row.get("reentry_id"),
            } for row in certification_rows]
    target = _event(nxt, reentry_id)
    scope = list(event["assurance"]["scope"])
    published_at = _utc_now(now)
    published: dict[str, Any] = {}
    for qid in scope:
        assurance_row = event["assurance"]["certifications"][qid]
        result_row = event["scientific_result"]["rows"][qid]
        previous_certification = current_certification(nxt, qid)
        previous_finding = _stored_finding(nxt, qid)
        is_subject = qid == str(event["question_id"])
        if is_subject and str(previous_finding["finding_id"]) != str(
                event["previous_finding_id"]):
            raise ReentryError(f"{qid}:STALE_SUPERSESSION_TARGET")
        if is_subject and str(
                previous_certification["certification_fingerprint"]) != str(
                event["previous_certification_fingerprint"]):
            raise ReentryError(f"{qid}:STALE_CERTIFICATION_CANNOT_OVERWRITE")
        if str(assurance_row.get("result_fingerprint")) != str(
                result_row.get("result_fingerprint")):
            raise ReentryError(f"{qid}:RESULT_FINGERPRINT_MISMATCH")
        # A new version must record genuinely new certified knowledge.  A pure
        # no-op (same state, same result, same evidence) cannot be published;
        # a same-state re-entry under a new evidence epoch remains valid.
        if (str(previous_finding["scientific_state"]) == str(
                assurance_row.get("scientific_state"))
                and str(previous_finding["scientific_result_fingerprint"]) == str(
                    result_row.get("result_fingerprint"))
                and str(previous_finding["evidence_fingerprint"]) == str(
                    event["evidence_fingerprint"])):
            raise ReentryError(
                f"{qid}:REENTRY_WITHOUT_NEW_EVIDENCE_OR_STATE")
        version = int(previous_certification["certification_version"]) + 1
        previous_result = next(row for row in nxt["scientific_results"][qid]
                               if row["result_status"] == A.FINDING_STATUS_CURRENT)
        if int(previous_result["scientific_result_version"]) + 1 != version:
            raise ReentryError(f"{qid}:SCIENTIFIC_RESULT_VERSION_GAP")
        new_result = {
            "scientific_result_id": f"{qid}:r{version}",
            "question_id": qid,
            "scientific_result_version": version,
            "result_status": A.FINDING_STATUS_CURRENT,
            "supersedes": previous_result["scientific_result_id"],
            "superseded_by": None,
            "result_fingerprint": str(result_row.get("result_fingerprint") or ""),
            "scientific_state": str(assurance_row.get("scientific_state") or ""),
            "evidence_epoch": str(event["evidence_epoch"]),
            "evidence_fingerprint": str(event["evidence_fingerprint"]),
            "reentry_id": str(reentry_id),
            "report_path": str(result_row.get("current_report_path") or ""),
            "runner_analytical_population": result_row.get("runner_analytical_population"),
            "question_contract_version": int(event["question_contract_version"]),
            "registry_version": str(event["registry_version"]),
            "dependency_state": deepcopy(event.get("dependency_state", {})),
        }
        new_certification = _certification_record(
            assurance_row, version=version,
            supersedes=str(previous_certification["certification_id"]),
            superseded_by=None, status=A.FINDING_STATUS_CURRENT,
            reentry_id=str(reentry_id),
            population_material={
                "candidate_count": assurance_row.get("candidate_count"),
                "usable_count": assurance_row.get("usable_count"),
                "analytical_count": assurance_row.get("analytical_count"),
                "exclusion_count": assurance_row.get("exclusion_count"),
                "unexplained_count": assurance_row.get("unexplained_count"),
                "population_match": assurance_row.get("population_match"),
            },
            contract_material={
                "question_id": qid,
                "question_contract_version": event["question_contract_version"],
                "question_contract_fingerprint": event["question_contract_fingerprint"],
                "evidence_contract_version": event["evidence_contract_version"],
                "evidence_contract_fingerprint": event["evidence_contract_fingerprint"],
                "runner_id": event["runner_id"],
                "registry_version": event["registry_version"],
                "registry_fingerprint": event["registry_fingerprint"],
            })

        new_finding = _finding_record(
            assurance_row, version=version,
            supersedes=str(previous_finding["finding_id"]), superseded_by=None,
            status=A.FINDING_STATUS_CURRENT, reentry_id=str(reentry_id),
            certification_fingerprint=new_certification[
                "certification_fingerprint"])
        previous_certification["certification_status"] = A.FINDING_STATUS_SUPERSEDED
        previous_certification["superseded_by"] = new_certification[
            "certification_id"]
        previous_certification["superseded_by_certification_id"] = (
            new_certification["certification_id"])
        previous_finding["finding_status"] = A.FINDING_STATUS_SUPERSEDED
        previous_finding["superseded_by"] = new_finding["finding_id"]
        previous_result["result_status"] = A.FINDING_STATUS_SUPERSEDED
        previous_result["superseded_by"] = new_result["scientific_result_id"]
        nxt["scientific_results"][qid].append(new_result)
        nxt["question_certifications"][qid].append(new_certification)
        nxt["assured_findings"][qid].append(new_finding)
        nxt["supersession_events"].append({
            "event_id": f"SUP-{reentry_id}-{qid}",
            "reentry_id": str(reentry_id),
            "question_id": qid,
            "trigger_type": str(event["trigger_type"]),
            "reason": str(event["reason"]),
            "superseded_certification_id": str(
                previous_certification["certification_id"]),
            "superseding_certification_id": str(
                new_certification["certification_id"]),
            "superseded_finding_id": str(previous_finding["finding_id"]),
            "superseding_finding_id": str(new_finding["finding_id"]),
            "previous_scientific_state": str(
                previous_finding["scientific_state"]),
            "new_scientific_state": str(new_finding["scientific_state"]),
            "previous_evidence_epoch": str(previous_finding["evidence_epoch"]),
            "new_evidence_epoch": str(new_finding["evidence_epoch"]),
            "previous_evidence_fingerprint": str(
                previous_finding["evidence_fingerprint"]),
            "new_evidence_fingerprint": str(new_finding["evidence_fingerprint"]),
            "at": published_at,
            "append_only": True,
        })
        published[qid] = {
            "certification_id": new_certification["certification_id"],
            "certification_version": version,
            "scientific_result_id": new_result["scientific_result_id"],
            "scientific_result_version": version,
            "finding_id": new_finding["finding_id"],
            "finding_version": version,
            "scientific_state": new_finding["scientific_state"],
            "assurance_status": new_finding["assurance_status"],
            "certification_fingerprint": new_certification[
                "certification_fingerprint"],
        }
    target["published"] = published
    target["published_at"] = published_at
    target["authorization_state"] = AUTH_PUBLISHED
    return _refresh_fingerprints(nxt)


def _finding_record(
        row: Mapping[str, Any], *, version: int, supersedes: str | None,
        superseded_by: str | None, status: str, reentry_id: str | None,
        certification_fingerprint: str) -> dict[str, Any]:
    qid = str(row["question_id"])
    record = {
        "finding_id": A.finding_identity(qid, version),
        "question_id": qid,
        "finding_version": int(version),
        "finding_status": status,
        "supersedes": supersedes,
        "superseded_by": superseded_by,
        "scientific_state": str(row.get("scientific_state") or ""),
        "assurance_status": str(row.get("assurance_status") or "FAILED"),
        "scientific_result_fingerprint": str(row.get("result_fingerprint") or ""),
        "evidence_fingerprint": str(row.get("evidence_fingerprint") or ""),
        "evidence_epoch": str(row.get("evidence_epoch") or ""),
        "certification_fingerprint": certification_fingerprint,
        "reentry_id": reentry_id,
    }
    record["content_fingerprint"] = _fingerprint({
        key: value for key, value in record.items()
        if key not in {"content_fingerprint", "finding_status", "superseded_by"}})
    return record



def certify_scoped_questions(
        manifest_path: Path | str, question_ids: Sequence[str], *,
        event: Mapping[str, Any]) -> dict[str, Any]:
    """Run the real question-scoped assurance recertification for a re-entry.

    This reuses the production certification checks verbatim through
    ``FinalAssuranceCertification.certify_scoped``.  The pinned contract
    fingerprints of the re-entry event are added so publication can prove the
    certification was produced under exactly the authorized contracts.
    """
    material = FinalAssuranceCertification(
        manifest_path=manifest_path,
        registry_version=str(event["registry_version"])).certify_scoped(
            list(question_ids))
    material["question_contract_fingerprint"] = str(
        event["question_contract_fingerprint"])
    material["evidence_contract_fingerprint"] = str(
        event["evidence_contract_fingerprint"])
    material["runner_contract_fingerprint"] = str(
        event["runner_contract_fingerprint"])
    material["registry_fingerprint"] = str(event["registry_fingerprint"])
    material["overall_assurance_status"] = (
        "VERIFIED" if all(
            str(row.get("assurance_status")) == "VERIFIED"
            for row in material.get("certifications", ())) else "FAILED")
    material["certification_fingerprint"] = _fingerprint({
        key: value for key, value in material.items()
        if key != "certification_fingerprint"})
    return material


def record_assurance(
        state: Mapping[str, Any], reentry_id: str, *,
        certification: Mapping[str, Any], now: str | None = None) -> dict[str, Any]:
    """Record the question-scoped assurance recertification for a re-entry.

    The certification must cover the executed scope, carry the pinned
    contract fingerprints, match the recorded scientific result fingerprint
    and be VERIFIED.  A successful scientific result on its own can never
    publish V2.
    """
    validate_state(state)
    event = _event(state, reentry_id)
    if str(event.get("authorization_state")) != AUTH_RESULT_RECORDED:
        raise ReentryError(f"{reentry_id}:SCIENTIFIC_RESULT_NOT_RECORDED")
    scope = list(event["scientific_result"]["scope"])
    certified = {str(row["question_id"]): row
                 for row in certification.get("certifications", ())}
    if set(certified) != set(scope):
        raise ReentryError(f"{reentry_id}:ASSURANCE_SCOPE_MISMATCH")
    fingerprint = str(certification.get("certification_fingerprint") or "")
    if not fingerprint or fingerprint != _fingerprint({
            key: value for key, value in certification.items()
            if key != "certification_fingerprint"}):
        raise ReentryError(f"{reentry_id}:ASSURANCE_FINGERPRINT_MISMATCH")
    if str(certification.get("stage")) != (
            "STAGE4_QUESTION_SCOPED_ASSURANCE_CERTIFICATION"):
        raise ReentryError(f"{reentry_id}:ASSURANCE_STAGE_NOT_SCOPED")
    for key, expected in (
            ("question_contract_fingerprint",
             event["question_contract_fingerprint"]),
            ("evidence_contract_fingerprint",
             event["evidence_contract_fingerprint"]),
            ("runner_contract_fingerprint", event["runner_contract_fingerprint"]),
            ("registry_fingerprint", event["registry_fingerprint"]),
    ):
        if str(certification.get(key) or "") != str(expected):
            raise ReentryError(f"{reentry_id}:ASSURANCE_CONTRACT_MISMATCH:{key}")
    if certification.get("global_errors"):
        raise ReentryError(f"{reentry_id}:ASSURANCE_GLOBAL_ERRORS_PRESENT")
    if certification.get("live_or_s3_reads") is not False:
        raise ReentryError(f"{reentry_id}:ASSURANCE_LIVE_READS_NOT_PERMITTED")
    for qid in scope:
        row = certified[qid]
        if str(row.get("assurance_status")) not in ASSURANCE_STATES:
            raise ReentryError(f"{reentry_id}:ASSURANCE_STATUS_UNKNOWN:{qid}")
        if str(row.get("scientific_state")) not in SCIENTIFIC_STATES:
            raise ReentryError(
                f"{reentry_id}:ASSURANCE_SCIENTIFIC_STATE_UNKNOWN:{qid}")
        expected_result = str(
            event["scientific_result"]["rows"][qid]["result_fingerprint"])
        if str(row.get("result_fingerprint")) != expected_result:
            raise ReentryError(
                f"{reentry_id}:ASSURANCE_RESULT_FINGERPRINT_MISMATCH")
        if not row.get("checks"):
            raise ReentryError(f"{reentry_id}:ASSURANCE_CHECKS_MISSING:{qid}")
    nxt = deepcopy(dict(state))
    target = _event(nxt, reentry_id)
    target["assurance"] = {
        "certification_fingerprint": fingerprint,
        "scope": scope,
        "assurance_status": str(
            certification.get("overall_assurance_status")
            or ("VERIFIED" if all(
                str(certified[qid]["assurance_status"]) == "VERIFIED"
                for qid in scope) else "FAILED")),
        "certifications": certified,
    }
    target["authorization_state"] = AUTH_ASSURANCE_RECORDED
    target["assurance_recorded_at"] = _utc_now(now)
    return _refresh_fingerprints(nxt)


def gap_resolution_linkage(state: Mapping[str, Any],
                           reentry_id: str) -> dict[str, Any]:
    """Explicit linkage evidence a gap needs before it may resolve."""
    event = _event(state, reentry_id)
    if str(event.get("authorization_state")) != AUTH_PUBLISHED:
        raise ReentryError(f"{reentry_id}:NOT_PUBLISHED")
    published = dict(event["published"])
    question_id = str(event["question_id"])
    if question_id not in published:
        raise ReentryError(f"{reentry_id}:QUESTION_NOT_PUBLISHED")
    entry = published[question_id]
    return {
        "reentry_id": str(reentry_id),
        "work_item_id": str(event["work_item_id"]),
        "scientific_result_fingerprint": str(
            event["scientific_result"]["rows"][question_id][
                "result_fingerprint"]),
        "certification_id": str(entry["certification_id"]),
        "finding_id": str(entry["finding_id"]),
        "assurance_status": "VERIFIED",
    }


def resolve_gap_via_reentry(
        gap_store: Mapping[str, Any], state: Mapping[str, Any],
        reentry_id: str) -> dict[str, Any]:
    """Resolve the governing gap only through a published re-entry linkage.

    A gap never marks itself resolved merely because a newer finding exists:
    the work item, the re-entry event, the scientific result, the versioned
    certification and the versioned assured finding must all be tied
    together explicitly.
    """
    linkage = gap_resolution_linkage(state, reentry_id)
    return G.resolve_via_reentry(deepcopy(dict(gap_store)),
                                 str(linkage["work_item_id"]), linkage)


def consume_scientific_truth(state: Mapping[str, Any],
                             question_id: str) -> Mapping[str, Any]:
    """Consume truth through the latest CURRENT assured finding only.

    Superseded versions are historical truth and are never consumed as the
    current scientific state of a question.
    """
    return A.consume_scientific_truth(current_finding(state, question_id))


def scientific_result_history(state: Mapping[str, Any],
                              question_id: str) -> dict[str, Any]:
    """Answer which report/result/evidence epoch produced each finding."""
    qid = str(question_id).strip().upper()
    result_history = state.get("scientific_results", {}).get(qid)
    result_by_version = {
        int(item["scientific_result_version"]): item
        for item in (result_history or ())}
    rows: list[dict[str, Any]] = []
    for finding in finding_history(state, qid):
        version = int(finding["finding_version"])
        result = result_by_version.get(version, {})
        rows.append({
            "finding_id": finding["finding_id"],
            "finding_version": int(finding["finding_version"]),
            "finding_status": finding["finding_status"],
            "scientific_result_id": result.get(
                "scientific_result_id", f"{qid}:r{version}"),
            "scientific_result_version": int(result.get(
                "scientific_result_version", version)),
            "scientific_result_status": result.get(
                "result_status", finding["finding_status"]),
            "scientific_state": finding["scientific_state"],
            "scientific_result_fingerprint": finding[
                "scientific_result_fingerprint"],
            "evidence_epoch": finding["evidence_epoch"],
            "evidence_fingerprint": finding["evidence_fingerprint"],
            "certification_id": certification_identity(
                qid, int(finding["finding_version"])),
            "certification_fingerprint": finding["certification_fingerprint"],
            "reentry_id": finding.get("reentry_id"),
            "runner_analytical_population": result.get(
                "runner_analytical_population"),
            "registry_version": result.get("registry_version", REGISTRY_VERSION),
        })
    changes = []
    for index in range(1, len(rows)):
        previous, current = rows[index - 1], rows[index]
        changed = sorted(
            key for key in ("scientific_state", "scientific_result_fingerprint",
                            "evidence_epoch", "evidence_fingerprint")
            if previous[key] != current[key])
        changes.append({
            "from_finding_id": previous["finding_id"],
            "to_finding_id": current["finding_id"],
            "changed_fields": changed,
        })
    return {"question_id": qid, "versions": rows, "changes": changes}


class _FixtureCoordinator:
    """Lightweight question-scoped coordinator used only by the fixture demo.

    It satisfies the same contract the scoped execution path consumes
    (``scope`` plus ``scoped_manifest()``) without loading the full frozen
    evidence snapshot, so the governed lifecycle can be demonstrated quickly
    and deterministically while the real eight gaps remain unrepaired.
    """

    def __init__(self, question_ids: Sequence[str], *,
                 scientific_state: str = "INSUFFICIENT_DATA") -> None:
        requested = [str(item).strip().upper() for item in question_ids]
        effective = set(requested)
        for qid in requested:
            question = REGISTRY_BY_ID.get(qid)
            if question is None:
                raise ReentryError(f"FIXTURE_UNKNOWN_QUESTION:{qid}")
            if question.scientific_owner_id:
                effective.add(str(question.scientific_owner_id))
        self.requested_scope = frozenset(requested)
        self.scope = frozenset(effective)
        self._scientific_state = scientific_state
        self.results: dict[str, Any] = {
            qid: _fixture_row(self._row(qid)) for qid in sorted(self.scope)}

    def _questions(self) -> list[Any]:
        return [REGISTRY_BY_ID[qid] for qid in sorted(self.scope)]

    def _row(self, qid: str) -> dict[str, Any]:
        question = REGISTRY_BY_ID[qid]
        if qid not in self.requested_scope:
            raise ReentryError(f"FIXTURE_RUN_OUT_OF_SCOPE:{qid}")
        return {
            "question_id": qid,
            "canonical_question": question.title,
            "initial_classification": "HISTORICAL_RUN_REQUIRED",
            "runner_status": "AVAILABLE",
            "runner_module": question.runner_module,
            "runner_function": question.runner_function,
            "runner_inputs": (),
            "candidate_historical_population": 100,
            "usable_historical_population": 100,
            "excluded_historical_population": 0,
            "unexplained_historical_population": 0,
            "historical_exhaustion_state": "EXHAUSTED",
            "current_report_before": "FIXTURE",
            "current_result_valid_before": False,
            "current_report_status": "COMPLETE",
            "current_report_path": "",
            "result_fingerprint": _fingerprint({
                "question_id": qid,
                "scientific_state": self._scientific_state,
                "fixture": True}),
            "scientific_state": self._scientific_state,
            "assurance_status": "NOT_REQUALIFIED",
            "next_action": "NONE",
            "reason": "governed re-entry fixture",
            "runner_executed": True,
            "runner_failed": False,
            "runner_analytical_population": 100,
        }

    def scoped_manifest(self) -> dict[str, Any]:
        rows = [asdict(self.results[qid]) for qid in sorted(self.scope)]
        if len(rows) != len(self.scope):
            raise ReentryError("FIXTURE_MANIFEST_INCOMPLETE")
        material = {
            "schema": 1,
            "stage": "STAGE4_HISTORICAL_SCOPED_RESEARCH_PASS",
            "requested_scope": sorted(self.requested_scope),
            "scope": sorted(self.scope),
            "scope_size": len(self.scope),
            "question_count": len(rows),
            "questions": rows,
            "live_or_s3_reads": False,
            "fixture": True,
        }
        return {**material, "fingerprint": _fingerprint(material)}


def _fixture_runner(scope: Sequence[str], *,
                    scientific_state: str = "INSUFFICIENT_DATA"
                    ) -> Callable[..., Any]:
    """Deterministic fixture runner proving the scoped re-entry lifecycle."""
    def runner(scope_arg: Sequence[str], **kwargs: Any) -> Any:
        return _FixtureCoordinator(list(scope_arg),
                                   scientific_state=scientific_state)
    return runner


def _fixture_row(row: Mapping[str, Any]) -> Any:
    from research_engine.control_plane.historical_research_pass import (
        HistoricalQuestionResult,
    )
    return HistoricalQuestionResult(**dict(row))

def _fixture_assurance(scope: Sequence[str], state: Mapping[str, Any],
                      reentry_id: str, *,
                      scientific_state: str = "INSUFFICIENT_DATA",
                      assurance_status: str = "VERIFIED") -> dict[str, Any]:
    event = _event(state, reentry_id)
    certifications = []
    for qid in scope:
        result = event["scientific_result"]["rows"][qid]
        certifications.append({
            "question_id": qid,
            "canonical_question": qid,
            "scientific_state": scientific_state,
            "assurance_status": assurance_status,
            "result_fingerprint": result["result_fingerprint"],
            "evidence_fingerprint": str(event["evidence_fingerprint"]),
            "evidence_epoch": str(event["evidence_epoch"]),
            "candidate_count": 100,
            "usable_count": 100,
            "analytical_count": 100,
            "exclusion_count": 0,
            "unexplained_count": 0,
            "population_match": True,
            "provenance_valid": True,
            "report_authority_valid": True,
            "accounting_valid": True,
            "historical_state_valid": True,
            "checks": {
                "canonical_definition": True,
                "evidence_contract": True,
                "exact_population": True,
                "accounting_conservation": True,
                "provenance": True,
                "report_ownership": True,
                "result_fingerprint": True,
                "evidence_epoch_current": True,
                "runner_validity": True,
                "lineage": True,
                "state_specific": True,
            },
        })
    material = {
        "schema": 1,
        "stage": "STAGE4_QUESTION_SCOPED_ASSURANCE_CERTIFICATION",
        "scope": list(scope),
        "question_count": len(scope),
        "question_scoped_recertification": True,
        "certifications": certifications,
        "global_errors": [],
        "assurance_status": assurance_status,
        "overall_assurance_status": assurance_status,
        "live_or_s3_reads": False,
        "new_research_evidence_introduced": False,
        "question_contract_fingerprint": str(
            event["question_contract_fingerprint"]),
        "evidence_contract_fingerprint": str(
            event["evidence_contract_fingerprint"]),
        "runner_contract_fingerprint": str(
            event["runner_contract_fingerprint"]),
        "registry_fingerprint": str(event["registry_fingerprint"]),
    }
    material["certification_fingerprint"] = _fingerprint({
        key: value for key, value in material.items()
        if key != "certification_fingerprint"})
    return material


def run_reentry_demo(*, question_id: str = "R1",
                     trigger_type: str = TRIGGER_IMPLEMENTATION_REPAIR,
                     reentry_id: str = "REENTRY-DEMO-R1",
                     now: str = "2026-09-29T00:00:00Z") -> dict[str, Any]:
    """Fixture-only V1 -> V2 demonstration; never publishes production truth.

    The real eight implementation gaps are still open, so this proves the
    generic mechanism on an in-memory copy of the durable state.  The
    durable store and every V1 artifact are left untouched.
    """
    state = bootstrap_state()
    baseline_projection = effective_current_state(state)
    baseline_v1_content = {
        qid: state["assured_findings"][qid][0]["content_fingerprint"]
        for qid in QUESTION_IDS}

    gap_store = G.build_store()
    work_item_id = f"GWI-{question_id}-IMPL"
    gap_store = G.apply_resolution_evidence(
        gap_store, work_item_id,
        sorted(G.PRE_RERUN_EVIDENCE[G.GAP_TYPE_IMPL]))
    gap_store = G.mark_reentry_ready(gap_store, work_item_id, reentry_id)

    satisfaction, evidence_sets, snapshots, epochs, decision = (
        _fixture_reentry_authorities(fixture_id=reentry_id))
    epoch = next(iter(epochs.values()))

    request = build_reentry_request(
        reentry_id=reentry_id,
        question_id=question_id,
        reason="Synthetic demonstration of the governed re-entry authority",
        trigger_type=trigger_type,
        evidence_epoch=epoch.epoch_id,
        evidence_fingerprint=evidence_epoch_fingerprint(epoch),
        observation_requirement_id=decision.observation_requirement_id,
        satisfaction_decision_id=decision.satisfaction_decision_id,
        gap_work_item_id=work_item_id,
        requested_at=now,
        state=state)
    state = authorize_reentry(
        state, request, now=now, gap_store=gap_store,
        satisfaction_registry=satisfaction, evidence_sets=evidence_sets,
        snapshot_registry=snapshots, evidence_epochs=epochs)
    state = execute_scoped_run(
        state, reentry_id, runner=_fixture_runner([question_id]), now=now)
    state = record_assurance(
        state, reentry_id,
        certification=_fixture_assurance([question_id], state, reentry_id),
        now=now)
    state = publish_new_version(state, reentry_id, now=now)
    gap_store = resolve_gap_via_reentry(gap_store, state, reentry_id)

    projection = effective_current_state(state)
    subject = QUESTION_IDS.index(question_id)
    demonstration = {
        "reentry_id": reentry_id,
        "question_id": question_id,
        "old_certification_version": int(
            baseline_projection["questions"][subject]["certification_version"]),
        "new_certification_version": int(
            current_certification(state, question_id)["certification_version"]),
        "old_finding_version": int(
            baseline_projection["questions"][subject]["finding_version"]),
        "new_finding_version": int(
            current_finding(state, question_id)["finding_version"]),
        "old_scientific_state": str(
            finding_history(state, question_id)[0]["scientific_state"]),
        "new_scientific_state": str(
            current_finding(state, question_id)["scientific_state"]),
        "effective_question_count": projection["question_count"],
        "unaffected_questions_changed": sum(
            1 for row in projection["questions"]
            if row["question_id"] != question_id
            and row["certification_version"] != 1),
        "supersession_valid": True,
        "gap_status": str(G.get_work_item(gap_store, work_item_id)["status"]),
        "baseline_projection_fingerprint": baseline_projection[
            "projection_fingerprint"],
        "effective_projection_fingerprint": projection["projection_fingerprint"],
        "result_history": scientific_result_history(state, question_id),
        "v1_content_preserved": all(
            state["assured_findings"][qid][0]["content_fingerprint"]
            == baseline_v1_content[qid] for qid in QUESTION_IDS),
        "fixture_only": True,
        "production_state_persisted": False,
    }
    return {"demonstration": demonstration, "state": state,
            "gap_store": gap_store}


def write_audit_artifacts(state: Mapping[str, Any],
                          demo: Mapping[str, Any] | None = None, *,
                          base: str = "analysis/assurance") -> dict[str, str]:
    """Dated audit mirrors; the durable state store remains the authority."""
    out = Path(base)
    out.mkdir(parents=True, exist_ok=True)
    projection = effective_current_state(state)
    material = {
        "schema": REENTRY_SCHEMA,
        "stage": STAGE,
        "store_fingerprint": state.get("store_fingerprint"),
        "effective_state_fingerprint": projection["projection_fingerprint"],
        "counts": state.get("counts"),
        "dependency_edges": state.get("dependency_edges"),
        "triggers": list(ALLOWED_TRIGGERS),
        "demonstration": dict(demo) if demo else None,
        "pre_existing_baseline_drift": PRE_EXISTING_BASELINE_DRIFT,
    }
    json_path = out / f"stage4_scientific_reentry_{AUDIT_STAMP}.json"
    json_path.write_text(json.dumps(material, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    lines = [
        f"# Stage 4 governed scientific re-entry ({AUDIT_STAMP})", "",
        f"- store: `{state.get('store_fingerprint')}`",
        f"- effective state: `{projection['projection_fingerprint']}`",
        f"- questions: {projection['question_count']}",
        f"- version counts: {projection['certification_version_counts']}",
        f"- baseline certification: `{BASELINE_CERTIFICATION_FINGERPRINT}`",
        "", "## Triggers", "",
    ]
    lines += [f"- {trigger}" for trigger in ALLOWED_TRIGGERS]
    lines += ["", "## Dependencies", ""]
    lines += [
        f"- {edge['prerequisite_question_id']} -> "
        f"{edge['dependent_question_id']} ({edge['source']})"
        for edge in state.get("dependency_edges", ())]
    if demo:
        lines += ["", "## Demonstration (fixture only)", ""]
        for key, value in sorted(dict(demo).items()):
            if key != "result_history":
                lines.append(f"- {key}: {value}")
    lines += ["", "## Pre-existing baseline drift", "",
              PRE_EXISTING_BASELINE_DRIFT, ""]
    md_path = out / f"stage4_scientific_reentry_{AUDIT_STAMP}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


__all__ = [
    "ALLOWED_TRIGGERS", "AUTH_ASSURANCE_RECORDED", "AUTH_AUTHORIZED",
    "AUTH_PUBLISHED", "AUTH_REQUESTED", "AUTH_RESULT_RECORDED",
    "AUDIT_JSON", "AUDIT_MD", "BASELINE_CERTIFICATION_FINGERPRINT",
    "DEPENDENCY_REQUIREMENT", "ELIGIBLE", "ELIGIBILITY_INVALID",
    "GAP_COUPLED_TRIGGERS", "LEGACY_UNGOVERNED", "NOT_ELIGIBLE",
    "ReentryAuthorization", "ReentryEligibility", "ReentryError",
    "ReentryRequest", "STATE_PATH", "STAGE", "TRIGGER_DATA_THRESHOLD_REACHED",
    "TRIGGER_DEPENDENCY_RESOLVED", "TRIGGER_GOVERNED_METHOD_REPAIR",
    "TRIGGER_IMPLEMENTATION_REPAIR", "TRIGGER_NEW_EVIDENCE_EPOCH",
    "TRIGGER_SCHEMA_COLLECTION_RECOVERED", "authorize_reentry",
    "bootstrap_state", "build_reentry_request", "certification_history",
    "certification_identity", "certify_scoped_questions",
    "consume_scientific_truth", "current_certification", "current_finding",
    "dependency_state_for", "effective_current_state",
    "ensure_bootstrapped", "evaluate_reentry_eligibility",
    "evidence_epoch_fingerprint",
    "execute_scoped_run", "finding_history", "gap_resolution_linkage",
    "load_state", "migrate_legacy_state", "plan_scoped_run", "population_fingerprint",
    "publish_new_version", "question_contract_pins", "record_assurance",
    "registry_fingerprint", "resolve_gap_via_reentry", "run_reentry_demo",
    "save_state", "scientific_result_history", "validate_state",
    "write_audit_artifacts",
]

