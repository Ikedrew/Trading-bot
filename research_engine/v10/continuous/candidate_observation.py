"""Generic candidate observation registration, reconciliation and evidence accounting.

Wire 4 closes the autonomy blocker where candidate observation was effectively
hard-coded around the historical candidate ``OPT-DP1-002``.  This module is the
small, generic production layer that, for any eligible candidate created by the
authoritative optimisation registry, establishes a deterministic, persisted
observation registration and attributes candidate-specific governed evidence to
exactly one candidate treatment and its frozen baseline.

This module never trades, never mutates candidate status directly, never grants
live authority and never decides validation.  It only:

* discovers eligible candidates from the authoritative ``OptimisationRegistry``;
* registers each with a deterministic, restart-safe, idempotent identity;
* reconciles the persisted observation registrations on every cadence tick;
* selects governed counterfactual evidence by frozen candidate, plan, source
  snapshot, epoch, population scope and treatment identity.

The actual validation/forward-validation decision remains owned by the existing
Wire 3 production validation queue and executors.  This module produces
*evidence identity* and *sample accounting*; it never issues a verdict.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from research_engine.control_plane.governed_counterfactual_evidence import (
    BASELINE_POLICY_ID, COUNTERFACTUAL_EVIDENCE_CLASS,
    VALIDITY_ELIGIBLE, treatment_signature,
    verify_governed_counterfactual_binding,
)
from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.experiments.exit_policy_governed import _governed_population
from research_engine.registry.exit_policy_adjudication import CANDIDATE_POLICIES_V1
from research_engine.v10.base import timestamp_now
from research_engine.v10.optimisation.models import (
    OptimisationCandidate,
    ValidationPlan,
)
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry


OBSERVATION_REGISTRATION_SCHEMA = "candidate_observation_registration_v1"
DEFAULT_OBSERVATION_STORE_PATH = Path(
    "data/research/continuous/candidate_observations.json")

#: Statuses a candidate may hold while still being eligible for candidate-specific
#: observation.  Terminal, blocked, revoked, rolled-back and live-approved
#: statuses are deliberately excluded: observation never follows a candidate
#: that has left the governed research/shadow-validation trajectory.
OBSERVATION_ELIGIBLE_STATUSES = frozenset({
    "PROPOSED", "READY_FOR_TEST", "TESTING", "VALIDATION_QUEUED", "VALIDATING",
    "VALIDATED", "FORWARD_VALIDATED", "SHADOW_VALIDATION_ACTIVE",
    "SHADOW_VALIDATED", "READY_FOR_PROMOTION_REVIEW",
})

#: Statuses that must never be newly registered and, when observed, stop active
#: collection while historical evidence is preserved.
OBSERVATION_BLOCKED_STATUSES = frozenset({
    "REJECTED", "SUPERSEDED", "INVALIDATED_UPSTREAM", "BLOCKED_UPSTREAM_INVALIDATED",
    "DISABLED", "REVOKED", "ROLLED_BACK", "VALIDATION_FAILED",
    "ACCEPTED", "APPROVED_NOT_DEPLOYED", "DEPLOYED", "VERIFIED",
})


def treatment_hash(policy: Mapping[str, Any]) -> str:
    """Deterministic governed treatment identity (same rule as the bridge)."""
    return hashlib.sha256(canonical_json(dict(policy)).encode("utf-8")).hexdigest()


def default_policy_catalog() -> Mapping[str, Mapping[str, Any]]:
    """The governed HD09 candidate policy catalogue (``policy_id`` -> policy)."""
    return {str(item["policy_id"]): dict(item) for item in CANDIDATE_POLICIES_V1}


def _supported_population_scope(population: Mapping[str, Any]) -> bool:
    """Accept only the two frozen scopes already defined by governed research."""
    scope = dict(population)
    if (set(scope) == {"scope", "canonical_authority_question_id"}
            and scope["scope"] == "governed"
            and scope["canonical_authority_question_id"] in {"EX1", "EX9"}):
        return True
    question = str(scope.get("question_id") or "")
    return question in {"EX1", "EX9"} and scope == _governed_population(question)


@dataclass(frozen=True)
class ObservationEligibility:
    candidate_id: str
    eligible: bool
    reason: str = ""
    policy_id: str = ""
    baseline_id: str = ""


def observation_eligibility(
    candidate: OptimisationCandidate,
    plan: ValidationPlan | None,
    policy_catalog: Mapping[str, Mapping[str, Any]],
) -> ObservationEligibility:
    """Smallest governed eligibility rule for candidate-specific observation.

    A candidate is observation-eligible only when it has: a governed policy in
    the frozen catalogue, a treatment identity that matches that policy's
    deterministic hash, a valid baseline, a frozen validation plan, a status
    still inside the research/shadow-validation trajectory, and no live
    authority.  Anything else is reported with an explicit reason and is not
    registered.
    """
    cid = str(candidate.candidate_id or "")
    if not cid:
        return ObservationEligibility(cid, False, "MISSING_CANDIDATE_ID")
    status = str(candidate.status or "")
    if status in OBSERVATION_BLOCKED_STATUSES:
        return ObservationEligibility(cid, False, f"BLOCKED_STATUS:{status}",
                                      candidate.policy_id, candidate.baseline_id)
    if status not in OBSERVATION_ELIGIBLE_STATUSES:
        return ObservationEligibility(cid, False, f"UNKNOWN_STATUS:{status}",
                                      candidate.policy_id, candidate.baseline_id)
    policy_id = str(candidate.policy_id or "")
    if not policy_id:
        return ObservationEligibility(cid, False, "MISSING_POLICY_ID",
                                      policy_id, candidate.baseline_id)
    policy = policy_catalog.get(policy_id)
    if policy is None:
        return ObservationEligibility(cid, False, f"UNGOVERNED_POLICY:{policy_id}",
                                      policy_id, candidate.baseline_id)
    expected_hash = treatment_hash(policy)
    if not candidate.treatment_hash:
        return ObservationEligibility(cid, False, "MISSING_TREATMENT_HASH",
                                      policy_id, candidate.baseline_id)
    if str(candidate.treatment_hash) != expected_hash:
        return ObservationEligibility(cid, False, "TREATMENT_HASH_MISMATCH",
                                      policy_id, candidate.baseline_id)
    if not str(candidate.baseline_id or ""):
        return ObservationEligibility(cid, False, "MISSING_BASELINE_ID",
                                      policy_id, candidate.baseline_id)
    if plan is None:
        return ObservationEligibility(cid, False, "MISSING_VALIDATION_PLAN",
                                      policy_id, candidate.baseline_id)
    if int(plan.minimum_sample or 0) <= 0:
        return ObservationEligibility(cid, False, "INVALID_VALIDATION_PLAN_SAMPLE",
                                      policy_id, candidate.baseline_id)
    if not _supported_population_scope(candidate.target_population or {}):
        return ObservationEligibility(cid, False, "UNSUPPORTED_POPULATION_SCOPE",
                                      policy_id, candidate.baseline_id)
    binding = dict(candidate.shadow_binding or {})
    if binding.get("live_approved") is True:
        return ObservationEligibility(cid, False, "LIVE_APPROVED_FORBIDDEN",
                                      policy_id, candidate.baseline_id)
    return ObservationEligibility(cid, True, "ELIGIBLE", policy_id, candidate.baseline_id)


def observation_registration_id(
    candidate: OptimisationCandidate, plan: ValidationPlan | None = None,
    snapshot_id: str = "",
) -> str:
    """Deterministic observation-registration identity (no filename-derived id)."""
    return _registration_id(
        str(candidate.candidate_id or ""), str(candidate.policy_id or ""),
        str(candidate.treatment_hash or ""), str(candidate.baseline_id or ""),
        validation_plan_digest(plan) if plan else "",
        str((candidate.provenance or {}).get("snapshot_id") or snapshot_id),
        str((candidate.provenance or {}).get("investigation_epoch") or ""),
        hashlib.sha256(canonical_json(dict(candidate.target_population or {})).encode(
            "utf-8")).hexdigest(),
    )


def _registration_id(candidate_id: str, policy_id: str, treatment: str,
                     baseline_id: str, plan_digest: str, snapshot_id: str,
                     epoch: str, population_digest: str) -> str:
    material = {
        "schema": OBSERVATION_REGISTRATION_SCHEMA,
        "candidate_id": candidate_id,
        "policy_id": policy_id,
        "treatment_hash": treatment,
        "baseline_id": baseline_id,
        "validation_plan_digest": plan_digest,
        "source_snapshot_id": snapshot_id,
        "investigation_epoch": epoch,
        "target_population_digest": population_digest,
    }
    return "OBS-" + hashlib.sha256(
        canonical_json(material).encode("utf-8")).hexdigest()[:24].upper()


@dataclass
class CandidateObservationRegistration:
    """One candidate's persisted observation registration."""

    registration_id: str
    candidate_id: str
    policy_id: str
    treatment_hash: str
    baseline_id: str
    source_snapshot_id: str = ""
    validation_plan_digest: str = ""
    investigation_epoch: str = ""
    target_population_digest: str = ""
    target_population: dict[str, Any] = field(default_factory=dict)
    status: str = "ACTIVE"
    registered_at: str = ""
    deactivated_at: str = ""
    deactivation_reason: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "registration_id": self.registration_id,
            "candidate_id": self.candidate_id,
            "policy_id": self.policy_id,
            "treatment_hash": self.treatment_hash,
            "baseline_id": self.baseline_id,
            "source_snapshot_id": self.source_snapshot_id,
            "validation_plan_digest": self.validation_plan_digest,
            "investigation_epoch": self.investigation_epoch,
            "target_population_digest": self.target_population_digest,
            "target_population": dict(self.target_population),
            "status": self.status,
            "registered_at": self.registered_at,
            "deactivated_at": self.deactivated_at,
            "deactivation_reason": self.deactivation_reason,
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CandidateObservationRegistration":
        get = dict(value or {}).get
        return cls(
            registration_id=str(get("registration_id", "") or ""),
            candidate_id=str(get("candidate_id", "") or ""),
            policy_id=str(get("policy_id", "") or ""),
            treatment_hash=str(get("treatment_hash", "") or ""),
            baseline_id=str(get("baseline_id", "") or ""),
            source_snapshot_id=str(get("source_snapshot_id", "") or ""),
            validation_plan_digest=str(get("validation_plan_digest", "") or ""),
            investigation_epoch=str(get("investigation_epoch", "") or ""),
            target_population_digest=str(get("target_population_digest", "") or ""),
            target_population=dict(get("target_population", {}) or {}),
            status=str(get("status", "ACTIVE") or "ACTIVE"),
            registered_at=str(get("registered_at", "") or ""),
            deactivated_at=str(get("deactivated_at", "") or ""),
            deactivation_reason=str(get("deactivation_reason", "") or ""),
            history=list(get("history", []) or []),
        )


def validation_plan_digest(plan: ValidationPlan) -> str:
    """Deterministic frozen-plan identity for a candidate's validation plan."""
    return hashlib.sha256(
        canonical_json(plan.to_dict()).encode("utf-8")).hexdigest()


def observation_registration_problem(
    reg: CandidateObservationRegistration | None,
    candidate: OptimisationCandidate,
    plan: Mapping[str, Any],
) -> str:
    """Check the persisted registration against the authoritative frozen inputs."""
    if reg is None:
        return "CANDIDATE_OBSERVATION_REGISTRATION_MISSING"
    if reg.status != "ACTIVE":
        return "CANDIDATE_OBSERVATION_REGISTRATION_INACTIVE"
    provenance = candidate.provenance or {}
    scope_digest = hashlib.sha256(canonical_json(dict(
        candidate.target_population or {})).encode("utf-8")).hexdigest()
    plan_digest = hashlib.sha256(canonical_json(dict(plan)).encode("utf-8")).hexdigest()
    expected = _registration_id(
        candidate.candidate_id, candidate.policy_id, candidate.treatment_hash,
        candidate.baseline_id, plan_digest, str(provenance.get("snapshot_id") or ""),
        str(provenance.get("investigation_epoch") or ""), scope_digest,
    )
    if (reg.registration_id != expected or reg.candidate_id != candidate.candidate_id
            or reg.policy_id != candidate.policy_id
            or reg.treatment_hash != candidate.treatment_hash
            or reg.baseline_id != candidate.baseline_id
            or reg.validation_plan_digest != plan_digest
            or reg.source_snapshot_id != str(provenance.get("snapshot_id") or "")
            or reg.investigation_epoch != str(provenance.get("investigation_epoch") or "")
            or reg.target_population_digest != scope_digest
            or reg.target_population != dict(candidate.target_population or {})):
        return "CANDIDATE_OBSERVATION_REGISTRATION_IDENTITY_MISMATCH"
    if not reg.source_snapshot_id or not reg.investigation_epoch:
        return "CANDIDATE_OBSERVATION_REGISTRATION_LEGACY_INCOMPLETE"
    if not _supported_population_scope(reg.target_population):
        return "CANDIDATE_OBSERVATION_POPULATION_SCOPE_UNSUPPORTED"
    return ""


class CandidateObservationStore:
    """Atomic, idempotent, restart-safe persistence for observation registrations.

    One candidate (``candidate_id``) maps to exactly one registration.  A
    registration can never be silently overwritten: an identity collision raises
    and a status change is recorded as history, never as a loss of the prior
    record.
    """

    def __init__(self, path: Path | str = DEFAULT_OBSERVATION_STORE_PATH) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, CandidateObservationRegistration]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("CANDIDATE_OBSERVATION_STORE_CORRUPT") from exc
        if not isinstance(payload, dict) or payload.get(
                "schema") != OBSERVATION_REGISTRATION_SCHEMA:
            raise ValueError("CANDIDATE_OBSERVATION_STORE_STRUCTURE_INVALID")
        rows = payload.get("registrations")
        if not isinstance(rows, dict):
            raise ValueError("CANDIDATE_OBSERVATION_STORE_STRUCTURE_INVALID")
        result: dict[str, CandidateObservationRegistration] = {}
        for cid, value in rows.items():
            if not isinstance(value, dict):
                raise ValueError("CANDIDATE_OBSERVATION_STORE_STRUCTURE_INVALID")
            reg = CandidateObservationRegistration.from_dict(value)
            if reg.candidate_id != cid:
                raise ValueError("CANDIDATE_OBSERVATION_REGISTRATION_KEY_MISMATCH")
            if (reg.validation_plan_digest and reg.target_population_digest
                    and reg.registration_id != _registration_id(
                        reg.candidate_id, reg.policy_id, reg.treatment_hash,
                        reg.baseline_id, reg.validation_plan_digest,
                        reg.source_snapshot_id, reg.investigation_epoch,
                        reg.target_population_digest)):
                raise ValueError("CANDIDATE_OBSERVATION_REGISTRATION_IDENTITY_INVALID")
            result[cid] = reg
        return result

    def save(self, registrations: Mapping[str, CandidateObservationRegistration]) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "schema": OBSERVATION_REGISTRATION_SCHEMA,
            "registrations": {cid: reg.to_dict() for cid, reg in registrations.items()},
        }
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, indent=2, sort_keys=True, default=str)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return str(self.path)


def _record_history(reg: CandidateObservationRegistration,
                    event: str, **extra: Any) -> None:
    reg.history.append({"event": event, "at": timestamp_now(), **extra})


def reconcile_candidate_observations(
    registry: OptimisationRegistry,
    store: CandidateObservationStore,
    policy_catalog: Mapping[str, Mapping[str, Any]] | None = None,
    snapshot_id: str = "",
) -> dict[str, Any]:
    """Idempotent, restart-safe observation registration reconciliation.

    * newly eligible candidates are registered exactly once;
    * already-registered candidates are preserved (never duplicated);
    * candidates that left the eligible trajectory are deactivated (history kept);
    * superseded/rejected/invalid candidates are never newly registered.
    """
    catalog = dict(policy_catalog or default_policy_catalog())
    existing = store.load()
    registered: list[str] = []
    deactivated: list[dict[str, str]] = []
    blocked: list[dict[str, str]] = []

    seen: set[str] = set()
    for candidate in sorted(registry.list_candidates(), key=lambda c: c.candidate_id):
        cid = str(candidate.candidate_id or "")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        plan = registry.get_plan(cid)
        eligibility = observation_eligibility(candidate, plan, catalog)
        if not eligibility.eligible:
            blocked.append({"candidate_id": cid, "reason": eligibility.reason})
            prior = existing.get(cid)
            if prior is not None and prior.status == "ACTIVE":
                prior.status = "INACTIVE"
                prior.deactivated_at = timestamp_now()
                prior.deactivation_reason = eligibility.reason
                _record_history(prior, "DEACTIVATED", reason=eligibility.reason)
                deactivated.append({"candidate_id": cid,
                                    "reason": eligibility.reason})
            continue

        source_snapshot_id = str((candidate.provenance or {}).get("snapshot_id") or snapshot_id or "")
        epoch = str((candidate.provenance or {}).get("investigation_epoch") or "")
        population = dict(candidate.target_population or {})
        population_digest = hashlib.sha256(
            canonical_json(population).encode("utf-8")).hexdigest()
        rid = observation_registration_id(candidate, plan, snapshot_id)
        plan_digest = validation_plan_digest(plan) if plan else ""
        prior = existing.get(cid)
        if prior is not None:
            if prior.registration_id != rid:
                raise ValueError("CANDIDATE_OBSERVATION_IDENTITY_CONFLICT:" + cid)
            if (prior.policy_id != eligibility.policy_id
                    or prior.treatment_hash != str(candidate.treatment_hash or "")
                    or prior.baseline_id != eligibility.baseline_id
                    or prior.validation_plan_digest != plan_digest
                    or prior.source_snapshot_id != source_snapshot_id
                    or prior.investigation_epoch != epoch
                    or prior.target_population_digest != population_digest
                    or prior.target_population != population):
                raise ValueError("CANDIDATE_OBSERVATION_IDENTITY_CONFLICT:" + cid)
            if prior.status != "ACTIVE":
                blocked.append({"candidate_id": cid, "reason": "REGISTRATION_INACTIVE"})
            continue
        reg = CandidateObservationRegistration(
            registration_id=rid,
            candidate_id=cid,
            policy_id=eligibility.policy_id,
            treatment_hash=str(candidate.treatment_hash or ""),
            baseline_id=eligibility.baseline_id,
            source_snapshot_id=source_snapshot_id,
            validation_plan_digest=plan_digest,
            investigation_epoch=epoch,
            target_population_digest=population_digest,
            target_population=population,
            status="ACTIVE",
            registered_at=timestamp_now(),
        )
        _record_history(reg, "REGISTERED")
        existing[cid] = reg
        registered.append(cid)

    store.save(existing)
    return {
        "registered": registered,
        "deactivated": deactivated,
        "blocked": blocked,
        "active_count": sum(1 for reg in existing.values() if reg.status == "ACTIVE"),
        "total_count": len(existing),
    }


def candidate_evidence_accounting(
    registrations: Mapping[str, CandidateObservationRegistration],
    evidence_store: Any,
) -> dict[str, dict[str, Any]]:
    """Count distinct governed opportunities in the frozen source population.

    Every immutable dataset is inspected.  The latest pointer is only a pointer
    to one artifact and cannot represent cumulative membership.  A policy row
    may be reused by candidates only when each registration independently pins
    the same snapshot, epoch, baseline, treatment and supported population scope.
    """
    artifacts = []
    store_error = ""
    if evidence_store is None:
        store_error = "EVIDENCE_UNAVAILABLE"
    else:
        try:
            for dataset_id in evidence_store.dataset_ids():
                artifact = evidence_store.load(dataset_id)
                if artifact is None:
                    raise ValueError("EVIDENCE_ARTIFACT_MISSING:" + dataset_id)
                verify_governed_counterfactual_binding(
                    evidence_store.binding_for(dataset_id),
                    snapshot_id=artifact.snapshot_id,
                    snapshot_fingerprint=artifact.snapshot_fingerprint,
                    investigation_epoch=artifact.investigation_epoch,
                    evidence=artifact,
                )
                artifacts.append(artifact)
        except Exception as exc:
            store_error = "EVIDENCE_AUTHORITY_INVALID:" + type(exc).__name__ + ":" + str(exc)
    result: dict[str, dict[str, Any]] = {}
    for cid, reg in registrations.items():
        reason = store_error
        members: dict[str, tuple[str, str, str]] = {}
        dataset_ids: set[str] = set()
        content_digests: dict[str, str] = {}
        matching_artifacts = 0
        if not reason and (reg.status != "ACTIVE" or reg.candidate_id != cid):
            reason = "REGISTRATION_INACTIVE_OR_INVALID"
        if not reason and (not reg.source_snapshot_id or not reg.investigation_epoch
                           or not reg.validation_plan_digest
                           or not reg.target_population_digest):
            reason = "LEGACY_REGISTRATION_IDENTITY_INCOMPLETE"
        if not reason and reg.registration_id != _registration_id(
                reg.candidate_id, reg.policy_id, reg.treatment_hash,
                reg.baseline_id, reg.validation_plan_digest,
                reg.source_snapshot_id, reg.investigation_epoch,
                reg.target_population_digest):
            reason = "REGISTRATION_IDENTITY_INVALID"
        if not reason and hashlib.sha256(canonical_json(
                dict(reg.target_population)).encode("utf-8")).hexdigest() != reg.target_population_digest:
            reason = "REGISTRATION_POPULATION_IDENTITY_INVALID"
        if not reason and not _supported_population_scope(reg.target_population):
            reason = "UNSUPPORTED_POPULATION_SCOPE"
        for artifact in artifacts if not reason else ():
            if (artifact.snapshot_id != reg.source_snapshot_id
                    or artifact.snapshot_id != reg.baseline_id
                    or (reg.investigation_epoch
                        and artifact.investigation_epoch != reg.investigation_epoch)):
                continue
            matching_artifacts += 1
            dataset_ids.add(artifact.dataset_id)
            content_digests[artifact.dataset_id] = artifact.content_digest
            if artifact.population_identity != COUNTERFACTUAL_EVIDENCE_CLASS:
                reason = "UNSUPPORTED_EVIDENCE_POPULATION"
                break
            if artifact.baseline_policy_id != BASELINE_POLICY_ID:
                reason = "EVIDENCE_BASELINE_MISMATCH"
                break
            for row in artifact.rows_for_policy(reg.policy_id):
                try:
                    valid_treatment = (
                        treatment_hash(row.treatment_parameters) == reg.treatment_hash
                        and row.treatment_signature == treatment_signature(
                            row.treatment_parameters))
                except Exception:
                    valid_treatment = False
                if (row.validity != VALIDITY_ELIGIBLE
                        or row.baseline_policy_id != artifact.baseline_policy_id
                        or not valid_treatment
                        or row.governed_policy_id != reg.policy_id
                        or not row.canonical_opportunity_id
                        or not all(row.lifecycle_identity)
                        or row.lifecycle_identity[1] != row.canonical_opportunity_id
                        or row.lifecycle_identity[2] != row.trade_horizon):
                    reason = "EVIDENCE_TREATMENT_OR_MEMBERSHIP_INVALID"
                    break
                member = row.canonical_opportunity_id
                identity = (row.row_digest, artifact.dataset_id,
                            canonical_json(list(row.lifecycle_identity)))
                if member in members and members[member][0] != row.row_digest:
                    reason = "CONFLICTING_OBSERVATION_IDENTITY"
                    break
                members[member] = identity
            if reason:
                break
        if not reason and not artifacts:
            reason = "EVIDENCE_UNAVAILABLE"
        if not reason and not matching_artifacts:
            reason = "NO_ELIGIBLE_EVIDENCE_FOR_REGISTRATION"
        result[cid] = {
            "candidate_id": cid,
            "policy_id": reg.policy_id,
            "registration_id": reg.registration_id,
            "registration_status": reg.status,
            "status": "ACCOUNTED" if not reason else "BLOCKED",
            "reason": reason,
            "sample_count": len(members) if not reason else 0,
            "validation_eligible_sample_count": (
                len(members) if not reason and matching_artifacts == 1 else 0),
            "validation_authority_status": (
                "SINGLE_DATASET" if not reason and matching_artifacts == 1
                else "AMBIGUOUS_MULTIPLE_DATASETS" if not reason else "BLOCKED"),
            "source_snapshot_id": reg.source_snapshot_id,
            "investigation_epoch": reg.investigation_epoch,
            "baseline_id": reg.baseline_id,
            "treatment_hash": reg.treatment_hash,
            "validation_plan_digest": reg.validation_plan_digest,
            "target_population_digest": reg.target_population_digest,
            "evidence_dataset_ids": sorted(dataset_ids) if not reason else [],
            "evidence_content_digests": content_digests if not reason else {},
            "opportunity_ids": sorted(members) if not reason else [],
            "row_digests": sorted(value[0] for value in members.values()) if not reason else [],
        }
    return result


__all__ = [
    "CandidateObservationRegistration",
    "CandidateObservationStore",
    "DEFAULT_OBSERVATION_STORE_PATH",
    "ObservationEligibility",
    "OBSERVATION_BLOCKED_STATUSES",
    "OBSERVATION_ELIGIBLE_STATUSES",
    "OBSERVATION_REGISTRATION_SCHEMA",
    "candidate_evidence_accounting",
    "default_policy_catalog",
    "observation_eligibility",
    "observation_registration_id",
    "observation_registration_problem",
    "reconcile_candidate_observations",
    "treatment_hash",
    "validation_plan_digest",
]
