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
* attributes candidate-specific governed counterfactual evidence (by
  ``policy_id``) and reports per-candidate sample accounting.

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

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
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
    binding = dict(candidate.shadow_binding or {})
    if binding.get("live_approved") is True:
        return ObservationEligibility(cid, False, "LIVE_APPROVED_FORBIDDEN",
                                      policy_id, candidate.baseline_id)
    return ObservationEligibility(cid, True, "ELIGIBLE", policy_id, candidate.baseline_id)


def observation_registration_id(candidate: OptimisationCandidate) -> str:
    """Deterministic observation-registration identity (no filename-derived id)."""
    material = {
        "schema": OBSERVATION_REGISTRATION_SCHEMA,
        "candidate_id": str(candidate.candidate_id or ""),
        "policy_id": str(candidate.policy_id or ""),
        "treatment_hash": str(candidate.treatment_hash or ""),
        "baseline_id": str(candidate.baseline_id or ""),
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
            reg = CandidateObservationRegistration.from_dict(value)
            if reg.candidate_id != cid:
                raise ValueError("CANDIDATE_OBSERVATION_REGISTRATION_KEY_MISMATCH")
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

        rid = observation_registration_id(candidate)
        plan_digest = validation_plan_digest(plan) if plan else ""
        prior = existing.get(cid)
        if prior is not None:
            if prior.registration_id != rid:
                raise ValueError("CANDIDATE_OBSERVATION_IDENTITY_CONFLICT:" + cid)
            if prior.status != "ACTIVE":
                prior.status = "ACTIVE"
                prior.deactivated_at = ""
                prior.deactivation_reason = ""
                _record_history(prior, "REACTIVATED")
            continue
        reg = CandidateObservationRegistration(
            registration_id=rid,
            candidate_id=cid,
            policy_id=eligibility.policy_id,
            treatment_hash=str(candidate.treatment_hash or ""),
            baseline_id=eligibility.baseline_id,
            source_snapshot_id=str(snapshot_id or ""),
            validation_plan_digest=plan_digest,
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
    """Attribute candidate-specific governed evidence and report sample counts.

    ``evidence_store`` is duck-typed: any object exposing ``load_latest()`` that
    returns a ``GovernedCounterfactualEvidence`` with ``rows_for_policy`` works,
    which is exactly the Wire 3 production validation authority's store.  Sample
    counts are therefore derived from the same immutable evidence the production
    validator consumes, never from a parallel collection mechanism.
    """
    latest = None
    if evidence_store is not None:
        try:
            latest = evidence_store.load_latest()
        except Exception:
            latest = None
    result: dict[str, dict[str, Any]] = {}
    for cid, reg in registrations.items():
        rows = ()
        if latest is not None and hasattr(latest, "rows_for_policy"):
            rows = latest.rows_for_policy(reg.policy_id)
        result[cid] = {
            "candidate_id": cid,
            "policy_id": reg.policy_id,
            "registration_status": reg.status,
            "sample_count": len(rows),
            "evidence_dataset_id": str(getattr(latest, "dataset_id", "") or ""),
            "snapshot_id": str(getattr(latest, "snapshot_id", "") or ""),
            "content_digest": str(getattr(latest, "content_digest", "") or ""),
            "row_digests": [str(row.row_digest) for row in rows],
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
    "reconcile_candidate_observations",
    "treatment_hash",
    "validation_plan_digest",
]
