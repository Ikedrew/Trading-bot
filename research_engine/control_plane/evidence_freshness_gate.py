"""Repair Block 3 — final governed evidence-freshness / frontier gate.

Audit 3 established that no general latest-frontier / evidence-age check existed
at approval time, so a stale candidate could still be human-ACCEPTed into
production authority.

This module adds that final gate.  It runs BEFORE a human ACCEPT can become
production authority and BEFORE the governed approval record is written.

Freshness is decided by IDENTITY SUPERSESSION, never by guessing a time window:
the repository has no governed maximum-evidence-age policy, so this module
deliberately does not invent one.  It refuses promotion when:

* the candidate is not in a reviewable state;
* the candidate's baseline/config provenance is no longer current;
* the newest validation evidence is not the successful evidence (later
  contradictory/failed evidence supersedes it);
* required forward/shadow evidence is missing or its frontier is superseded;
* the recorded evidence frontier was explicitly superseded;
* another governed candidate already supersedes this one;
* an upstream finding/hypothesis has been invalidated.

Every check fails CLOSED: an unknown input is a refusal, never a pass.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

FRESHNESS_SCHEMA = "candidate_promotion_freshness_v1"

FRESH = "FRESH"
STALE = "STALE"
SUPERSEDED = "SUPERSEDED"
INVALIDATED_UPSTREAM = "INVALIDATED_UPSTREAM"

REVIEWABLE_STATES = frozenset({"READY_FOR_PROMOTION_REVIEW", "READY_FOR_REVIEW"})

# Validation decisions that contradict a successful result.
_CONTRADICTORY_DECISIONS = frozenset({
    "WORSENED", "REGRESSION", "REGRESSION_DETECTED", "FAILED", "FAILED_VALIDATION",
    "INVALID",
})
_SUCCESS_DECISIONS = frozenset({"IMPROVED", "VALIDATED", "FORWARD_VALIDATED"})


@dataclass(frozen=True)
class FreshnessVerdict:
    """Outcome of the final governed freshness gate."""

    ok: bool
    state: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    evidence_frontier: str = ""
    treatment_signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FRESHNESS_SCHEMA,
            "ok": self.ok,
            "state": self.state,
            "reasons": list(self.reasons),
            "evidence_frontier": self.evidence_frontier,
            "treatment_signature": self.treatment_signature,
        }


def _refuse(state: str, reasons: Sequence[str], **extra: Any) -> FreshnessVerdict:
    return FreshnessVerdict(ok=False, state=state, reasons=tuple(reasons), **extra)


def proposal_treatment_signature(change_definition: Mapping[str, Any] | None) -> str:
    """Deterministic identity of the CURRENT proposal treatment.

    Volatile bookkeeping keys are excluded so the signature describes the
    treatment itself, not when it was written.
    """
    if not isinstance(change_definition, Mapping) or not change_definition:
        return ""
    volatile = {"baseline_config_hash", "recorded_at", "created_at", "notes",
                "treatment_hash"}
    material = {str(k): v for k, v in change_definition.items() if k not in volatile}
    if not material:
        return ""
    payload = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _latest_evidence(validation_history: Iterable[Any]) -> tuple[str, str]:
    """Return (latest_decision, latest_success_decision) in recorded order."""
    latest = ""
    latest_success = ""
    for entry in validation_history or ():
        decision = str(getattr(entry, "decision", "") or
                       (entry.get("decision") if isinstance(entry, Mapping) else "") or "")
        if not decision:
            continue
        latest = decision
        if decision in _SUCCESS_DECISIONS:
            latest_success = decision
    return latest, latest_success


def evaluate_promotion_freshness(
    *,
    candidate_id: str,
    registry_dir: str | None = None,
    expected_treatment_signature: str = "",
    current_frontier: str = "",
    superseded_frontiers: Sequence[str] = (),
    candidate_frontier: str = "",
    shadow_frontier: str = "",
    require_shadow_evidence: bool = False,
    superseded_by_candidate: str = "",
    invalidated_upstream: Sequence[str] = (),
) -> FreshnessVerdict:
    """Run the final governed freshness gate for one candidate (fail closed)."""
    from research_engine.v10.candidates.candidate_registry import CandidateRegistry

    registry = (CandidateRegistry(storage_dir=registry_dir) if registry_dir
                else CandidateRegistry())
    candidate = registry.get(candidate_id)
    if candidate is None:
        return _refuse(STALE, [f"CANDIDATE_NOT_FOUND:{candidate_id}"])

    # 1. Reviewable state only.  Never promote from VALIDATING/SHADOW_*.
    if candidate.status not in REVIEWABLE_STATES:
        return _refuse(STALE, [f"NOT_REVIEWABLE_STATE:{candidate.status}"])

    # 2. Upstream scientific basis must still be valid.
    invalidated = sorted({str(item) for item in invalidated_upstream if str(item)})
    if invalidated:
        return _refuse(INVALIDATED_UPSTREAM,
                       [f"UPSTREAM_INVALIDATED:{item}" for item in invalidated])

    # 3. Baseline / config provenance must still be current.
    from research_engine.v10.baselines.baseline_authority import (
        validate_candidate_baseline,
    )

    baseline_ok, baseline_reason = validate_candidate_baseline(
        candidate.baseline_id,
        (candidate.change_definition or {}).get("baseline_config_hash", ""),
    )
    if not baseline_ok:
        return _refuse(STALE, [f"BASELINE_NOT_CURRENT:{baseline_reason}"])

    # 4. Treatment identity must still match the current proposal.
    signature = proposal_treatment_signature(candidate.change_definition)
    if not signature:
        return _refuse(STALE, ["PROPOSAL_TREATMENT_UNIDENTIFIABLE"])
    if expected_treatment_signature and expected_treatment_signature != signature:
        return _refuse(STALE, ["TREATMENT_SIGNATURE_DRIFT"])
    recorded_hash = (candidate.change_definition or {}).get("treatment_hash", "")
    if recorded_hash and recorded_hash != signature:
        return _refuse(STALE, ["TREATMENT_HASH_MISMATCH"])

    # 5. Validation evidence must exist and must not be superseded by later
    #    contradictory evidence.
    latest, latest_success = _latest_evidence(candidate.validation_history)
    if not latest_success:
        return _refuse(STALE, ["NO_SUCCESSFUL_VALIDATION_EVIDENCE"])
    if latest in _CONTRADICTORY_DECISIONS:
        return _refuse(STALE, [f"EVIDENCE_SUPERSEDED_BY:{latest}"])

    # 6. Required prospective/shadow evidence must be present and current.
    if require_shadow_evidence:
        if not shadow_frontier:
            return _refuse(STALE, ["MISSING_SHADOW_EVIDENCE_FRONTIER"])
        recorded_shadow = str(
            (getattr(candidate, "shadow_binding", {}) or {}).get("evidence_frontier") or ""
        )
        if recorded_shadow and recorded_shadow != shadow_frontier:
            return _refuse(STALE, ["SHADOW_EVIDENCE_FRONTIER_SUPERSEDED"])

    # 7. Evidence frontier identity supersession (no invented age window).
    superseded = {str(item) for item in superseded_frontiers if str(item)}
    frontier = candidate_frontier or str(
        (getattr(candidate, "shadow_binding", {}) or {}).get("evidence_frontier") or ""
    )
    if frontier and frontier in superseded:
        return _refuse(STALE, ["EVIDENCE_FRONTIER_SUPERSEDED"])
    if current_frontier and frontier and frontier != current_frontier:
        return _refuse(STALE, ["EVIDENCE_FRONTIER_NOT_CURRENT"])

    # 8. A newer governed candidate must not already supersede this one.
    if superseded_by_candidate:
        return _refuse(SUPERSEDED, [f"SUPERSEDED_BY:{superseded_by_candidate}"])

    return FreshnessVerdict(
        ok=True, state=FRESH, reasons=(),
        evidence_frontier=frontier or current_frontier,
        treatment_signature=signature,
    )


def candidate_requires_shadow_evidence(candidate: Any) -> bool:
    """Explicit proposal metadata only — never inferred from free text."""
    change = getattr(candidate, "change_definition", {}) or {}
    if not isinstance(change, Mapping):
        return False
    return bool(change.get("requires_shadow_evidence", False))


__all__ = [
    "FRESH", "FRESHNESS_SCHEMA", "FreshnessVerdict", "INVALIDATED_UPSTREAM",
    "REVIEWABLE_STATES", "STALE", "SUPERSEDED", "candidate_requires_shadow_evidence",
    "evaluate_promotion_freshness", "proposal_treatment_signature",
]
