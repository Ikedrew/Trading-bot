"""Wave 6.2B — canonical evidence-continuity / revalidation state.

Answers ONE scientific question:

    After assessing Candidate X's historical N-era observations against the
    exact verified N -> N+1 impact record using Wave 6.2A, what is Candidate
    X's canonical, durable evidence-continuity / revalidation state for
    Baseline N+1?

Think:

    Candidate X @ N
    + exact CandidateBaselineImpactRecord (Wave 6.1B) for the verified N -> N+1
    + zero or more Wave 6.2A EvidenceEligibilityDecision records
    -> CandidateEvidenceContinuityState

CONTRACT:
    - This step SUMMARISES 6.2A eligibility decisions. It is NOT evidence
      aggregation: it never mixes historical N evidence with fresh N+1
      evidence, never builds an evaluation population, never rebuilds pairs,
      never reruns or creates evaluations (that belongs to Wave 6.2C or
      later). It records only what is scientifically permitted/required.
    - Historical evidence keeps baseline_id = N permanently; fresh evidence,
      when it eventually exists, belongs to N+1. Neither population is
      rewritten, relabelled, migrated or deleted.
    - Every 6.2A decision included in ONE continuity state must describe the
      SAME candidate, the SAME historical baseline (identity and config),
      the SAME target baseline, the SAME impact_id and the SAME treatment
      identity. Mixed provenance FAILS CLOSED (never inferred from current
      mutable candidate/baseline state).
    - Observation identity is the canonical opportunity identity
      (``correlation_id``) that Wave 6.2A echoes from the persisted historical
      observation. Never list position, object identity, timestamps, random
      UUIDs, symbol or candidate_id alone. The same observation is never
      counted twice; the same observation identity with a CONFLICTING
      eligibility result FAILS CLOSED.
    - Aggregation precedence is conservative and deterministic -- no majority
      voting and no percentages:
        1. any INDETERMINATE             -> BLOCKED_INDETERMINATE
        2. else any REVALIDATION_REQUIRED -> REVALIDATION_REQUIRED
        3. else any NOT_DIRECTLY_ELIGIBLE -> REVALIDATION_REQUIRED
        4. else all DIRECTLY_ELIGIBLE     -> CONTINUITY_ALLOWED
        5. else zero decisions            -> NO_HISTORICAL_EVIDENCE
      One unresolved observation can never be hidden by many directly
      eligible ones.
    - Counts are descriptive accounting ONLY (never weights or reuse
      percentages) and must satisfy, for the deduplicated population:
        total == directly + revalidation_required + not_directly + indeterminate
      Accounting that cannot be proven FAILS CLOSED.
    - Continuity identity is deterministic (canonical JSON + sha256) over the
      exact historical provenance AND the exact assessed population (the
      per-observation 6.2A conclusions). Identical inputs always produce an
      identical identity/state; a legitimately different assessed population
      produces a DIFFERENT snapshot that never overwrites the earlier one. No
      clocks, no randomness.
    - The continuity store is append-only: historical snapshots are never
      overwritten, an identical duplicate write is idempotent, and the same
      identity with conflicting persisted content FAILS CLOSED.
    - ZERO scientific side effects: no evidence mutation, no baseline /
      candidate / lifecycle mutation, no evaluation, recommendation, human
      decision, application, deployment or production mutation. Continuity
      history only.

CHRONOLOGY (HARDENING 1.2):
    No wall-clock timestamp is persisted, and none is required for
    scientific correctness. Canonical chronology is deterministic and
    reconstructable after restart/restore from persisted truth alone:

      - the verified baseline sequence (historical_baseline_id ->
        target_baseline_id, i.e. N -> N+1) plus the exact transition
        references (impact_id, application_id) that bind this snapshot to
        one persisted 6.1B impact record, and
      - the append order of the append-only store.

    Canonical listing/chain order is the deterministic key
    (historical_baseline_id, target_baseline_id, continuity_id) -- never
    wall-clock and never the active-baseline pointer.

    Wall-clock time is descriptive-only and MUST NOT be added to
    to_dict() / canonical_json(): that exact shape is simultaneously the
    persisted serialization, the idempotent-duplicate oracle and the
    fail-closed conflict check, so a non-deterministic field would break
    deduplication, equality and restart determinism.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from research_engine.lifecycle.candidate_evidence_eligibility import (
    EvidenceEligibilityDecision,
    EvidenceEligibilityStatus,
)
from research_engine.lifecycle.candidate_impact_history import (
    CandidateBaselineImpactRecord,
    CandidateImpactHistoryStore,
)

__all__ = [
    "ContinuityBindingError",
    "ContinuityObservationIdentityError",
    "ContinuityAccountingError",
    "ContinuityConflictError",
    "EvidenceContinuityState",
    "ObservationEligibilitySummary",
    "CandidateEvidenceContinuityState",
    "CandidateEvidenceContinuityStore",
    "CandidateEvidenceContinuityChain",
    "compute_continuity_id",
    "build_candidate_evidence_continuity_state",
    "assess_candidate_evidence_continuity",
    "reconstruct_evidence_continuity_chain",
    "ALL_ASSESSED_OBSERVATIONS_DIRECTLY_ELIGIBLE",
    "REVALIDATION_REQUIRED_EVIDENCE",
    "NOT_DIRECTLY_ELIGIBLE_EVIDENCE",
    "UNRESOLVED_INDETERMINATE_EVIDENCE",
    "MIXED_ELIGIBILITY_POPULATION",
    "NO_HISTORICAL_OBSERVATIONS_ASSESSED",
]


# ─── Fail-closed errors ───────────────────────────────────────────────────────


class ContinuityBindingError(ValueError):
    """A 6.2A decision (or the impact record) does not describe the exact
    Candidate X @ N / verified N -> N+1 transition this continuity state
    belongs to. Provenance is never inferred from current mutable state and
    mixed provenance is never aggregated (fail closed)."""


class ContinuityObservationIdentityError(ValueError):
    """Historical observations cannot be identified deterministically, or the
    same observation identity carries conflicting eligibility results. Fail
    closed rather than inventing identity or silently dropping evidence."""


class ContinuityAccountingError(ValueError):
    """Eligibility statuses or population accounting are malformed, or the
    accounting invariant cannot be proven (fail closed). Counts are
    descriptive accounting only."""


class ContinuityConflictError(ValueError):
    """The same continuity identity carries conflicting persisted content.
    Historical truth is never overwritten (fail closed)."""


# ─── Canonical continuity states ──────────────────────────────────────────────


class EvidenceContinuityState(str, Enum):
    """Canonical, durable evidence-continuity / revalidation state for
    Candidate X @ N -> Baseline N+1. Purely categorical: no percentages.

    Mapping note: the names are the ones the wave contract specifies; they are
    deliberately distinct from the 6.2A per-observation statuses (the aggregate
    REVALIDATION_REQUIRED means "the population as a whole requires fresh N+1
    validation", not "one observation was so marked")."""

    CONTINUITY_ALLOWED = "CONTINUITY_ALLOWED"
    REVALIDATION_REQUIRED = "REVALIDATION_REQUIRED"
    BLOCKED_INDETERMINATE = "BLOCKED_INDETERMINATE"
    NO_HISTORICAL_EVIDENCE = "NO_HISTORICAL_EVIDENCE"


# ─── Continuity-level reason codes ────────────────────────────────────────────
# These explain WHY the aggregate state exists. They complement -- never replace
# -- the per-observation 6.2A reason codes, which are summarised verbatim in
# reason_code_counts.

ALL_ASSESSED_OBSERVATIONS_DIRECTLY_ELIGIBLE = (
    "ALL_ASSESSED_OBSERVATIONS_DIRECTLY_ELIGIBLE"
)
REVALIDATION_REQUIRED_EVIDENCE = "REVALIDATION_REQUIRED_EVIDENCE"
NOT_DIRECTLY_ELIGIBLE_EVIDENCE = "NOT_DIRECTLY_ELIGIBLE_EVIDENCE"
UNRESOLVED_INDETERMINATE_EVIDENCE = "UNRESOLVED_INDETERMINATE_EVIDENCE"
MIXED_ELIGIBILITY_POPULATION = "MIXED_ELIGIBILITY_POPULATION"
NO_HISTORICAL_OBSERVATIONS_ASSESSED = "NO_HISTORICAL_OBSERVATIONS_ASSESSED"

_CONTINUITY_REASON_ORDER = (
    ALL_ASSESSED_OBSERVATIONS_DIRECTLY_ELIGIBLE,
    REVALIDATION_REQUIRED_EVIDENCE,
    NOT_DIRECTLY_ELIGIBLE_EVIDENCE,
    UNRESOLVED_INDETERMINATE_EVIDENCE,
    MIXED_ELIGIBILITY_POPULATION,
    NO_HISTORICAL_OBSERVATIONS_ASSESSED,
)


def _ordered_reasons(reasons: Iterable[str]) -> tuple[str, ...]:
    """Sort continuity reason codes into the fixed canonical emission order."""
    rank = {code: i for i, code in enumerate(_CONTINUITY_REASON_ORDER)}
    present = [r for r in reasons if r in rank]
    return tuple(sorted(set(present), key=rank.__getitem__))


# ── Canonical JSON / digest conventions (existing project convention) ────────


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


# ─── Eligibility status / decision-content normalization ──────────────────────


def _normalize_status(status: Any) -> EvidenceEligibilityStatus:
    """Normalize a 6.2A eligibility status, failing closed on malformed values."""
    if isinstance(status, EvidenceEligibilityStatus):
        return status
    if isinstance(status, str):
        try:
            return EvidenceEligibilityStatus(status)
        except ValueError:
            pass
    raise ContinuityAccountingError(f"Malformed eligibility status: {status!r}")


def _decision_content(
    decision: EvidenceEligibilityDecision,
    status: EvidenceEligibilityStatus,
) -> dict[str, Any]:
    """Full canonical content of ONE 6.2A decision. Used ONLY to detect the same
    observation identity carrying conflicting eligibility results; never used
    as an evidence weight."""
    return {
        "candidate_id": decision.candidate_id,
        "historical_baseline_id": decision.historical_baseline_id,
        "target_baseline_id": decision.target_baseline_id,
        "impact_id": decision.impact_id,
        "classification": decision.classification,
        "status": status.value,
        "reason_codes": list(decision.reason_codes),
        "may_contribute_directly": decision.may_contribute_directly,
        "fresh_evidence_required": decision.fresh_evidence_required,
        "limitations": list(decision.limitations),
        "correlation_id": decision.correlation_id,
        "treatment_id": decision.treatment_id,
    }


# ─── Per-observation eligibility summary (what 6.2A concluded) ────────────────


@dataclass(frozen=True)
class ObservationEligibilitySummary:
    """What 6.2A concluded for ONE historical observation, keyed by the
    canonical opportunity identity (``correlation_id``). Descriptive summary
    only: this is NOT an evaluation population, NOT combined with fresh N+1
    evidence and NOT a weight."""

    correlation_id: str
    status: str
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ObservationEligibilitySummary":
        shape = {"correlation_id": "", "status": "", "reason_codes": []}
        if not isinstance(data, dict) or set(data) != set(shape):
            raise ContinuityAccountingError("Malformed observation eligibility summary")
        if not _non_empty_str(data["correlation_id"]):
            raise ContinuityObservationIdentityError(
                "Observation eligibility summary lacks canonical observation identity")
        _normalize_status(data["status"])
        reason_codes = data["reason_codes"]
        if (not isinstance(reason_codes, list)
                or not all(_non_empty_str(c) for c in reason_codes)):
            raise ContinuityAccountingError("Malformed observation reason codes")
        return cls(
            correlation_id=data["correlation_id"],
            status=str(data["status"]),
            reason_codes=tuple(reason_codes),
        )


# ─── Deterministic aggregation (conservative precedence, no voting) ───────────


def _assert_accounting(*, total: int, counts: dict[str, int]) -> None:
    """Prove the exact population accounting invariant; fail closed otherwise."""
    for name, value in counts.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ContinuityAccountingError(f"Malformed count {name}={value!r}")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ContinuityAccountingError(
            f"Malformed total_observations_assessed={total!r}")
    if total != sum(counts.values()):
        raise ContinuityAccountingError(
            "Population accounting cannot be proven: total_observations_assessed "
            f"({total}) != sum of eligibility counts ({sum(counts.values())})")


def _resolve_state(
    *,
    total: int,
    directly: int,
    revalidation: int,
    not_directly: int,
    indeterminate: int,
) -> EvidenceContinuityState:
    """Conservative deterministic precedence. One unresolved observation can
    never be hidden by many directly eligible ones."""
    if total == 0:
        return EvidenceContinuityState.NO_HISTORICAL_EVIDENCE
    if indeterminate > 0:
        return EvidenceContinuityState.BLOCKED_INDETERMINATE
    if revalidation > 0 or not_directly > 0:
        return EvidenceContinuityState.REVALIDATION_REQUIRED
    return EvidenceContinuityState.CONTINUITY_ALLOWED


def _continuity_reasons(
    *,
    total: int,
    directly: int,
    revalidation: int,
    not_directly: int,
    indeterminate: int,
) -> tuple[str, ...]:
    """Deterministic reason-code summary for the aggregate state."""
    if total == 0:
        return _ordered_reasons([NO_HISTORICAL_OBSERVATIONS_ASSESSED])
    reasons = []
    if directly == total:
        reasons.append(ALL_ASSESSED_OBSERVATIONS_DIRECTLY_ELIGIBLE)
    if revalidation > 0:
        reasons.append(REVALIDATION_REQUIRED_EVIDENCE)
    if not_directly > 0:
        reasons.append(NOT_DIRECTLY_ELIGIBLE_EVIDENCE)
    if indeterminate > 0:
        reasons.append(UNRESOLVED_INDETERMINATE_EVIDENCE)
    if 0 < directly < total:
        reasons.append(MIXED_ELIGIBILITY_POPULATION)
    return _ordered_reasons(reasons)


def _continuity_flags(
    state: EvidenceContinuityState, *, directly: int
) -> tuple[bool, bool]:
    """(direct_historical_contribution_allowed, fresh_evidence_required).

    CONTINUITY_ALLOWED    -> (True, False)
    REVALIDATION_REQUIRED -> (directly > 0, True); direct contribution remains
                             permitted ONLY for the subset of observations
                             individually marked DIRECTLY_ELIGIBLE
    BLOCKED_INDETERMINATE -> (False, True)   # aggregate-state boundary
    NO_HISTORICAL_EVIDENCE-> (False, True)
    """
    if state is EvidenceContinuityState.CONTINUITY_ALLOWED:
        return True, False
    if state is EvidenceContinuityState.REVALIDATION_REQUIRED:
        return (directly > 0), True
    return False, True


def _direct_contribution_ids(
    summaries: Iterable[ObservationEligibilitySummary],
    state: EvidenceContinuityState,
) -> tuple[str, ...]:
    """The exact subset of historical observations permitted to contribute
    directly (empty at the aggregate boundary when continuity is blocked)."""
    if state not in (
        EvidenceContinuityState.CONTINUITY_ALLOWED,
        EvidenceContinuityState.REVALIDATION_REQUIRED,
    ):
        return ()
    return tuple(sorted(
        s.correlation_id for s in summaries
        if s.status == EvidenceEligibilityStatus.DIRECTLY_ELIGIBLE.value
    ))


_STATUS_COUNT_KEYS = {
    EvidenceEligibilityStatus.DIRECTLY_ELIGIBLE.value: "directly_eligible_count",
    EvidenceEligibilityStatus.REVALIDATION_REQUIRED.value: (
        "revalidation_required_count"
    ),
    EvidenceEligibilityStatus.NOT_DIRECTLY_ELIGIBLE.value: (
        "not_directly_eligible_count"
    ),
    EvidenceEligibilityStatus.INDETERMINATE.value: "indeterminate_count",
}


def _counts_from_summaries(
    summaries: Iterable[ObservationEligibilitySummary],
) -> dict[str, int]:
    """Descriptive accounting derived from the deduplicated population. Counts
    are NEVER evidence weights or reuse percentages."""
    counts = {name: 0 for name in _STATUS_COUNT_KEYS.values()}
    for summary in summaries:
        status = _normalize_status(summary.status)
        counts[_STATUS_COUNT_KEYS[status.value]] += 1
    return counts


def _reason_code_counts(
    summaries: Iterable[ObservationEligibilitySummary],
) -> dict[str, int]:
    """Deterministic per-reason-code summary across the deduplicated population."""
    tally: dict[str, int] = {}
    for summary in summaries:
        for code in summary.reason_codes:
            tally[code] = tally.get(code, 0) + 1
    return {code: tally[code] for code in sorted(tally)}


# ─── Deterministic continuity identity ────────────────────────────────────────

# The identity binds the EXACT historical provenance plus the EXACT assessed
# population (per-observation 6.2A conclusions), so materially different
# assessed populations can never collide. Counts / reason summaries / flags are
# deterministic functions of that content and are therefore not re-bound.
_CONTINUITY_IDENTITY_FIELDS = (
    "candidate_id",
    "historical_baseline_id",
    "historical_baseline_config_hash",
    "target_baseline_id",
    "target_baseline_config_hash",
    "impact_id",
    "application_id",
    "candidate_treatment_id",
    "continuity_state",
    "observation_states",
)

_SUMMARY_IDENTITY_KEYS = ("correlation_id", "status", "reason_codes")


def compute_continuity_id(**identity: Any) -> str:
    """Deterministic continuity identity over Candidate X @ N + exact verified
    N -> N+1 transition identity + exact assessed historical population.
    Canonical JSON + SHA256 (existing project convention). No random UUID, no
    wall clock."""
    missing = [f for f in _CONTINUITY_IDENTITY_FIELDS if f not in identity]
    if missing:
        raise ValueError(f"Continuity identity missing {missing}")
    extra = [k for k in identity if k not in _CONTINUITY_IDENTITY_FIELDS]
    if extra:
        raise ValueError(f"Continuity identity has unexpected fields {extra}")
    for field in _CONTINUITY_IDENTITY_FIELDS:
        if field == "observation_states":
            continue
        if not _non_empty_str(identity[field]):
            raise ValueError("Continuity identity fields must be non-empty")
    if identity["continuity_state"] not in EvidenceContinuityState.__members__:
        raise ValueError(
            f"Continuity identity has unknown continuity_state "
            f"{identity['continuity_state']!r}")

    states = identity["observation_states"]
    if not isinstance(states, (list, tuple)):
        raise ValueError("Continuity identity observation_states must be a sequence")
    normalized = []
    for entry in states:
        if not isinstance(entry, dict) or set(entry) != set(_SUMMARY_IDENTITY_KEYS):
            raise ValueError(f"Malformed continuity identity observation: {entry!r}")
        if not _non_empty_str(entry["correlation_id"]):
            raise ContinuityObservationIdentityError(
                "Continuity identity observation lacks canonical identity")
        _normalize_status(entry["status"])
        normalized.append({
            "correlation_id": entry["correlation_id"],
            "status": entry["status"],
            "reason_codes": list(entry["reason_codes"]),
        })
    return "ECC-" + _digest({
        field: (normalized if field == "observation_states" else identity[field])
        for field in _CONTINUITY_IDENTITY_FIELDS
    })


# ─── The continuity state record ──────────────────────────────────────────────


@dataclass(frozen=True)
class CandidateEvidenceContinuityState:
    """The canonical, durable evidence-continuity / revalidation snapshot for
    exactly ONE Candidate X @ historical baseline N and ONE verified N -> N+1
    transition.

    It binds the exact historical provenance (never resolved from current
    mutable candidate/baseline state), the exact 6.1B impact identity, the exact
    assessed historical population, what 6.2A concluded per observation, the
    canonical continuity state, deterministic accounting and the
    direct-contribution / fresh-evidence flags. It performs NO evaluation and
    NEVER mixes historical N evidence with fresh N+1 evidence."""

    continuity_id: str
    state: EvidenceContinuityState
    candidate_id: str
    historical_baseline_id: str
    historical_baseline_config_hash: str
    target_baseline_id: str
    target_baseline_config_hash: str
    impact_id: str
    application_id: str
    candidate_treatment_id: str
    total_observations_assessed: int
    directly_eligible_count: int
    revalidation_required_count: int
    not_directly_eligible_count: int
    indeterminate_count: int
    observation_states: tuple[ObservationEligibilitySummary, ...]
    reason_codes: tuple[str, ...]
    reason_code_counts: dict[str, int]
    direct_contribution_correlation_ids: tuple[str, ...]
    direct_historical_contribution_allowed: bool
    fresh_evidence_required: bool

    def _counts(self) -> dict[str, int]:
        return {
            "directly_eligible_count": self.directly_eligible_count,
            "revalidation_required_count": self.revalidation_required_count,
            "not_directly_eligible_count": self.not_directly_eligible_count,
            "indeterminate_count": self.indeterminate_count,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "continuity_id": self.continuity_id,
            "state": self.state.value,
            "candidate_id": self.candidate_id,
            "historical_baseline_id": self.historical_baseline_id,
            "historical_baseline_config_hash": self.historical_baseline_config_hash,
            "target_baseline_id": self.target_baseline_id,
            "target_baseline_config_hash": self.target_baseline_config_hash,
            "impact_id": self.impact_id,
            "application_id": self.application_id,
            "candidate_treatment_id": self.candidate_treatment_id,
            "total_observations_assessed": self.total_observations_assessed,
            "directly_eligible_count": self.directly_eligible_count,
            "revalidation_required_count": self.revalidation_required_count,
            "not_directly_eligible_count": self.not_directly_eligible_count,
            "indeterminate_count": self.indeterminate_count,
            "observation_states": [s.to_dict() for s in self.observation_states],
            "reason_codes": list(self.reason_codes),
            "reason_code_counts": dict(self.reason_code_counts),
            "direct_contribution_correlation_ids": list(
                self.direct_contribution_correlation_ids),
            "direct_historical_contribution_allowed": (
                self.direct_historical_contribution_allowed),
            "fresh_evidence_required": self.fresh_evidence_required,
        }

    def canonical_json(self) -> str:
        return _canonical(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CandidateEvidenceContinuityState":
        """Fail-closed reload of a persisted continuity snapshot."""
        shape = cls(
            continuity_id="", state=EvidenceContinuityState.NO_HISTORICAL_EVIDENCE,
            candidate_id="", historical_baseline_id="",
            historical_baseline_config_hash="", target_baseline_id="",
            target_baseline_config_hash="", impact_id="", application_id="",
            candidate_treatment_id="", total_observations_assessed=0,
            directly_eligible_count=0, revalidation_required_count=0,
            not_directly_eligible_count=0, indeterminate_count=0,
            observation_states=(), reason_codes=(), reason_code_counts={},
            direct_contribution_correlation_ids=(),
            direct_historical_contribution_allowed=False,
            fresh_evidence_required=False,
        ).to_dict()
        if not isinstance(data, dict) or set(data) != set(shape):
            raise ContinuityAccountingError("Malformed evidence continuity record")
        try:
            state = EvidenceContinuityState(data["state"])
        except ValueError as exc:
            raise ContinuityAccountingError(
                f"Malformed continuity state: {data['state']!r}") from exc

        for field in (
            "continuity_id", "candidate_id", "historical_baseline_id",
            "historical_baseline_config_hash", "target_baseline_id",
            "target_baseline_config_hash", "impact_id", "application_id",
            "candidate_treatment_id",
        ):
            if not _non_empty_str(data[field]):
                raise ContinuityBindingError(
                    f"Continuity record missing required provenance {field!r}")

        raw_states = data["observation_states"]
        if not isinstance(raw_states, list):
            raise ContinuityAccountingError("Malformed observation_states")
        summaries = tuple(
            ObservationEligibilitySummary.from_dict(s) for s in raw_states)
        ids = [s.correlation_id for s in summaries]
        if len(set(ids)) != len(ids):
            raise ContinuityObservationIdentityError(
                "Conflicting duplicate observation identity in continuity record")
        if ids != sorted(ids):
            raise ContinuityObservationIdentityError(
                "Observation states are not in canonical order")

        counts = {
            "directly_eligible_count": data["directly_eligible_count"],
            "revalidation_required_count": data["revalidation_required_count"],
            "not_directly_eligible_count": data["not_directly_eligible_count"],
            "indeterminate_count": data["indeterminate_count"],
        }
        total = data["total_observations_assessed"]
        _assert_accounting(total=total, counts=counts)
        derived = _counts_from_summaries(summaries)
        if derived != counts:
            raise ContinuityAccountingError(
                "Population accounting cannot be proven from observation states")
        if total != len(summaries):
            raise ContinuityAccountingError(
                "total_observations_assessed does not match the deduplicated "
                "assessed observation population")

        counts_kw = dict(
            total=total, directly=counts["directly_eligible_count"],
            revalidation=counts["revalidation_required_count"],
            not_directly=counts["not_directly_eligible_count"],
            indeterminate=counts["indeterminate_count"],
        )
        reason_codes = data["reason_codes"]
        if (not isinstance(reason_codes, list)
                or not all(_non_empty_str(c) for c in reason_codes)):
            raise ContinuityAccountingError("Malformed continuity reason_codes")
        if _resolve_state(**counts_kw) is not state:
            raise ContinuityAccountingError(
                "Continuity state does not follow from the persisted accounting")
        if tuple(reason_codes) != _continuity_reasons(**counts_kw):
            raise ContinuityAccountingError(
                "Continuity reason codes do not follow from the persisted accounting")

        reason_code_counts = data["reason_code_counts"]
        if not isinstance(reason_code_counts, dict) or not all(
            _non_empty_str(k)
            and not isinstance(v, bool) and isinstance(v, int) and v >= 0
            for k, v in reason_code_counts.items()
        ):
            raise ContinuityAccountingError("Malformed reason_code_counts")

        direct_ids = data["direct_contribution_correlation_ids"]
        if (not isinstance(direct_ids, list)
                or not all(_non_empty_str(i) for i in direct_ids)):
            raise ContinuityAccountingError(
                "Malformed direct_contribution_correlation_ids")
        allowed = data["direct_historical_contribution_allowed"]
        fresh = data["fresh_evidence_required"]
        if not isinstance(allowed, bool) or not isinstance(fresh, bool):
            raise ContinuityAccountingError("Malformed continuity flags")
        if (allowed, fresh) != _continuity_flags(
                state, directly=counts["directly_eligible_count"]):
            raise ContinuityAccountingError(
                "Continuity contribution/fresh-evidence flags do not obey the "
                "canonical semantics")
        if tuple(direct_ids) != _direct_contribution_ids(summaries, state):
            raise ContinuityAccountingError(
                "Direct-contribution subset does not follow from the persisted "
                "observation states")
        if not set(direct_ids).issubset(set(ids)):
            raise ContinuityAccountingError(
                "Direct-contribution subset is not part of the assessed population")

        return cls(
            continuity_id=data["continuity_id"],
            state=state,
            candidate_id=data["candidate_id"],
            historical_baseline_id=data["historical_baseline_id"],
            historical_baseline_config_hash=data["historical_baseline_config_hash"],
            target_baseline_id=data["target_baseline_id"],
            target_baseline_config_hash=data["target_baseline_config_hash"],
            impact_id=data["impact_id"],
            application_id=data["application_id"],
            candidate_treatment_id=data["candidate_treatment_id"],
            total_observations_assessed=total,
            directly_eligible_count=counts["directly_eligible_count"],
            revalidation_required_count=counts["revalidation_required_count"],
            not_directly_eligible_count=counts["not_directly_eligible_count"],
            indeterminate_count=counts["indeterminate_count"],
            observation_states=summaries,
            reason_codes=tuple(reason_codes),
            reason_code_counts=dict(reason_code_counts),
            direct_contribution_correlation_ids=tuple(direct_ids),
            direct_historical_contribution_allowed=allowed,
            fresh_evidence_required=fresh,
        )


# ─── Binding ONE 6.2A decision to the exact historical chain ──────────────────


def _bind_decision(
    decision: EvidenceEligibilityDecision,
    impact_record: CandidateBaselineImpactRecord,
) -> tuple[dict[str, Any], EvidenceEligibilityStatus]:
    """Verify that ONE 6.2A decision describes the exact Candidate X @ N and the
    exact verified N -> N+1 transition, then return its canonical content.
    Missing or foreign provenance FAILS CLOSED and is never inferred from
    current mutable candidate/baseline state."""
    if not isinstance(decision, EvidenceEligibilityDecision):
        raise ContinuityBindingError("Not an evidence eligibility decision")
    status = _normalize_status(decision.status)

    if not _non_empty_str(decision.correlation_id):
        raise ContinuityObservationIdentityError(
            "Eligibility decision carries no canonical observation identity "
            "(correlation_id); durable aggregation cannot prove duplicate "
            "observation safety")
    if decision.candidate_id != impact_record.candidate_id:
        raise ContinuityBindingError(
            f"Decision candidate_id={decision.candidate_id!r} != impact "
            f"candidate_id={impact_record.candidate_id!r}")
    if (decision.historical_baseline_id != impact_record.from_baseline_id
            or decision.historical_baseline_id != impact_record.candidate_baseline_id):
        raise ContinuityBindingError(
            f"Decision historical_baseline_id={decision.historical_baseline_id!r} "
            "does not match the impact historical baseline "
            f"({impact_record.candidate_baseline_id!r})")
    if decision.target_baseline_id != impact_record.to_baseline_id:
        raise ContinuityBindingError(
            f"Decision target_baseline_id={decision.target_baseline_id!r} != "
            f"impact to_baseline_id={impact_record.to_baseline_id!r}")
    if decision.impact_id != impact_record.impact_id:
        raise ContinuityBindingError(
            f"Decision impact_id={decision.impact_id!r} != "
            f"impact_id={impact_record.impact_id!r}; decisions from different "
            "transitions are never aggregated")
    if (decision.treatment_id is not None
            and decision.treatment_id != impact_record.candidate_treatment_id):
        raise ContinuityBindingError(
            f"Decision treatment_id={decision.treatment_id!r} does not match "
            "the candidate treatment identity "
            f"{impact_record.candidate_treatment_id!r}")
    reasons = decision.reason_codes
    if (not isinstance(reasons, (tuple, list))
            or not all(_non_empty_str(r) for r in reasons)):
        raise ContinuityAccountingError("Malformed eligibility reason codes")
    return _decision_content(decision, status), status


# ─── The builder (pure: summarise 6.2A, never aggregate evidence) ─────────────


def build_candidate_evidence_continuity_state(
    *,
    impact_record: CandidateBaselineImpactRecord,
    decisions: Iterable[EvidenceEligibilityDecision],
    target_baseline_config_hash: str | None = None,
    impact_store: CandidateImpactHistoryStore | None = None,
) -> CandidateEvidenceContinuityState:
    """Summarise ZERO or more 6.2A eligibility decisions for exactly ONE
    Candidate X @ historical baseline N and ONE exact verified N -> N+1 impact
    record into the canonical continuity / revalidation state.

    Pure and deterministic: identical inputs always produce an identical
    record. Performs no persistence, mutates nothing, and never combines
    historical N evidence with fresh N+1 evidence.

    When ``impact_store`` is supplied, the impact record must already be
    persisted with EXACTLY this content: the 6.1B chain is verified, never
    assumed.
    """
    if not isinstance(impact_record, CandidateBaselineImpactRecord):
        raise ContinuityBindingError("Not a candidate baseline impact record")

    for name in (
        "impact_id", "candidate_id", "candidate_baseline_id",
        "candidate_baseline_config_hash", "from_baseline_id",
        "from_baseline_config_hash", "to_baseline_id",
        "to_baseline_config_hash", "application_id", "candidate_treatment_id",
    ):
        if not _non_empty_str(getattr(impact_record, name, "")):
            raise ContinuityBindingError(
                f"Impact record missing {name!r}; continuity provenance cannot "
                "be bound")

    if (impact_record.candidate_baseline_id != impact_record.from_baseline_id
            or impact_record.candidate_baseline_config_hash
            != impact_record.from_baseline_config_hash):
        raise ContinuityBindingError(
            "Impact record does not bind the candidate historical baseline to "
            "the transition from-baseline")

    if (target_baseline_config_hash is not None
            and target_baseline_config_hash != impact_record.to_baseline_config_hash):
        raise ContinuityBindingError(
            f"Target config hash {target_baseline_config_hash!r} != the exact "
            "N -> N+1 transition target config hash")

    if impact_store is not None:
        persisted = impact_store.get(impact_record.impact_id)
        if persisted is None:
            raise ContinuityBindingError(
                f"Impact record {impact_record.impact_id!r} is not persisted; "
                "the exact 6.1B chain is unavailable")
        if persisted.to_dict() != impact_record.to_dict():
            raise ContinuityBindingError(
                f"Impact identity {impact_record.impact_id!r} carries "
                "conflicting persisted content")

    # ─── Bind + deduplicate the 6.2A decisions by observation identity ──────
    by_observation: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        content, _status = _bind_decision(decision, impact_record)
        correlation_id = content["correlation_id"]
        existing = by_observation.get(correlation_id)
        if existing is None:
            by_observation[correlation_id] = content
        elif existing != content:
            raise ContinuityObservationIdentityError(
                f"Observation {correlation_id!r} carries conflicting "
                "eligibility results; duplicate-identity evidence is never "
                "silently merged or dropped")

    observation_states = tuple(
        ObservationEligibilitySummary(
            correlation_id=correlation_id,
            status=by_observation[correlation_id]["status"],
            reason_codes=tuple(by_observation[correlation_id]["reason_codes"]),
        )
        for correlation_id in sorted(by_observation)
    )

    # ─── Descriptive accounting (never weights) + proven invariant ──────────
    counts = _counts_from_summaries(observation_states)
    total = len(observation_states)
    _assert_accounting(total=total, counts=counts)
    counts_kw = dict(
        total=total,
        directly=counts["directly_eligible_count"],
        revalidation=counts["revalidation_required_count"],
        not_directly=counts["not_directly_eligible_count"],
        indeterminate=counts["indeterminate_count"],
    )

    state = _resolve_state(**counts_kw)
    allowed, fresh = _continuity_flags(
        state, directly=counts["directly_eligible_count"])

    continuity_id = compute_continuity_id(
        candidate_id=impact_record.candidate_id,
        historical_baseline_id=impact_record.candidate_baseline_id,
        historical_baseline_config_hash=impact_record.candidate_baseline_config_hash,
        target_baseline_id=impact_record.to_baseline_id,
        target_baseline_config_hash=impact_record.to_baseline_config_hash,
        impact_id=impact_record.impact_id,
        application_id=impact_record.application_id,
        candidate_treatment_id=impact_record.candidate_treatment_id,
        continuity_state=state.value,
        observation_states=[s.to_dict() for s in observation_states],
    )

    return CandidateEvidenceContinuityState(
        continuity_id=continuity_id,
        state=state,
        candidate_id=impact_record.candidate_id,
        historical_baseline_id=impact_record.candidate_baseline_id,
        historical_baseline_config_hash=impact_record.candidate_baseline_config_hash,
        target_baseline_id=impact_record.to_baseline_id,
        target_baseline_config_hash=impact_record.to_baseline_config_hash,
        impact_id=impact_record.impact_id,
        application_id=impact_record.application_id,
        candidate_treatment_id=impact_record.candidate_treatment_id,
        total_observations_assessed=total,
        directly_eligible_count=counts["directly_eligible_count"],
        revalidation_required_count=counts["revalidation_required_count"],
        not_directly_eligible_count=counts["not_directly_eligible_count"],
        indeterminate_count=counts["indeterminate_count"],
        observation_states=observation_states,
        reason_codes=_continuity_reasons(**counts_kw),
        reason_code_counts=_reason_code_counts(observation_states),
        direct_contribution_correlation_ids=_direct_contribution_ids(
            observation_states, state),
        direct_historical_contribution_allowed=allowed,
        fresh_evidence_required=fresh,
    )


# ─── Minimal continuity authority: durable, append-only, deterministic ────────

_CONTINUITY_DIR = "data/research/lifecycle/evidence_continuity"
_CONTINUITY_FILE = "candidate_evidence_continuity.jsonl"


class CandidateEvidenceContinuityStore:
    """Append-only, deterministic evidence-continuity history: the ONE new
    minimal authority this wave adds. Historical snapshots are NEVER
    overwritten; an identical duplicate write is idempotent; the same
    continuity identity with conflicting persisted content FAILS CLOSED.
    There is deliberately no 'current continuity' field: history is preserved
    per (candidate, historical baseline, target baseline), so a later baseline
    transition can never overwrite earlier continuity history."""

    def __init__(self, continuity_dir: str | Path | None = None) -> None:
        self._dir = Path(continuity_dir or _CONTINUITY_DIR)
        self._records: list[CandidateEvidenceContinuityState] = []
        self._load()

    @property
    def path(self) -> Path:
        return self._dir / _CONTINUITY_FILE

    def get(self, continuity_id: str) -> CandidateEvidenceContinuityState | None:
        for record in self._records:
            if record.continuity_id == continuity_id:
                return record
        return None

    def list_all(self) -> list[CandidateEvidenceContinuityState]:
        return list(self._records)

    def list_for_impact(self, impact_id: str) -> list[CandidateEvidenceContinuityState]:
        return [r for r in self._records if r.impact_id == impact_id]

    def list_for_candidate(
        self, candidate_id: str
    ) -> list[CandidateEvidenceContinuityState]:
        records = [r for r in self._records if r.candidate_id == candidate_id]
        return sorted(
            records,
            key=lambda r: (
                r.historical_baseline_id, r.target_baseline_id, r.continuity_id),
        )

    def append(self, record: CandidateEvidenceContinuityState) -> bool:
        """Persist ONE continuity snapshot. True when newly appended; False for
        an idempotent duplicate; raises ContinuityConflictError on conflicting
        content under the same deterministic identity."""
        if not isinstance(record, CandidateEvidenceContinuityState):
            raise ValueError("Not an evidence continuity record")
        if not _non_empty_str(record.continuity_id):
            raise ContinuityBindingError("Continuity record lacks an identity")
        existing = self.get(record.continuity_id)
        if existing is not None:
            if existing.to_dict() != record.to_dict():
                raise ContinuityConflictError(
                    f"Continuity identity {record.continuity_id!r} already "
                    "persisted with conflicting content")
            return False
        self._dir.mkdir(parents=True, exist_ok=True)
        line = record.canonical_json() + "\n"
        fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        self._records.append(record)
        return True

    def _load(self) -> None:
        if not self.path.is_file():
            return
        by_id: dict[str, CandidateEvidenceContinuityState] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = CandidateEvidenceContinuityState.from_dict(json.loads(line))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(
                    f"Corrupt continuity history in {self.path}: {exc}") from exc
            existing = by_id.get(record.continuity_id)
            if existing is not None and existing.to_dict() != record.to_dict():
                raise ContinuityConflictError(
                    f"Conflicting persisted content for continuity "
                    f"{record.continuity_id!r}")
            if existing is None:
                by_id[record.continuity_id] = record
        self._records = list(by_id.values())


# ─── Orchestration (continuity history is the ONLY thing persisted) ───────────


def assess_candidate_evidence_continuity(
    *,
    impact_record: CandidateBaselineImpactRecord,
    decisions: Iterable[EvidenceEligibilityDecision],
    continuity_dir: str | Path | None = None,
    target_baseline_config_hash: str | None = None,
    impact_store: CandidateImpactHistoryStore | None = None,
) -> CandidateEvidenceContinuityState:
    """Build Candidate X @ N's continuity/revalidation state for the exact
    verified N -> N+1 transition and append it to the append-only continuity
    history.

    Zero scientific side effects: no evidence, baseline, candidate, lifecycle,
    evaluation, recommendation, decision, application, deployment or production
    mutation. Eligibility science remains entirely Wave 6.1A/6.1B/6.2A's.
    """
    state = build_candidate_evidence_continuity_state(
        impact_record=impact_record,
        decisions=decisions,
        target_baseline_config_hash=target_baseline_config_hash,
        impact_store=impact_store,
    )
    CandidateEvidenceContinuityStore(continuity_dir).append(state)
    return state


# ─── Read-only historical-chain projection (no second history database) ───────


@dataclass(frozen=True)
class CandidateEvidenceContinuityChain:
    """Read-only projection of the persisted historical chain for ONE candidate:

        Candidate X @ N -> 6.1B impact record -> 6.2B continuity state

    This is NOT a second experiment-history database: frozen treatment specs,
    baseline snapshots and verified application truth remain in their existing
    authorities (reconstructable through 6.1B)."""

    candidate_id: str
    impact_records: tuple[CandidateBaselineImpactRecord, ...]
    continuity_states: tuple[CandidateEvidenceContinuityState, ...]

    def continuity_states_for_impact(
        self, impact_id: str
    ) -> tuple[CandidateEvidenceContinuityState, ...]:
        return tuple(
            s for s in self.continuity_states if s.impact_id == impact_id)

    def impact_for_state(
        self, continuity_id: str
    ) -> CandidateBaselineImpactRecord | None:
        for state in self.continuity_states:
            if state.continuity_id == continuity_id:
                for record in self.impact_records:
                    if record.impact_id == state.impact_id:
                        return record
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "impact_records": [r.to_dict() for r in self.impact_records],
            "continuity_states": [s.to_dict() for s in self.continuity_states],
        }


def reconstruct_evidence_continuity_chain(
    candidate_id: str,
    *,
    impact_dir: str | Path | None = None,
    continuity_dir: str | Path | None = None,
) -> CandidateEvidenceContinuityChain:
    """Reconstruct ONE candidate's persisted historical chain from the existing
    authorities only: the 6.1B impact history store and the 6.2B continuity
    history store. Fail closed if any continuity snapshot references an impact
    record that is not persisted, or if it does not bind the exact persisted
    impact chain."""
    if not _non_empty_str(candidate_id):
        raise ContinuityBindingError("Candidate ID required")

    impacts = [
        r for r in CandidateImpactHistoryStore(impact_dir).list_all()
        if r.candidate_id == candidate_id
    ]
    states = [
        s for s in CandidateEvidenceContinuityStore(continuity_dir).list_all()
        if s.candidate_id == candidate_id
    ]
    by_impact = {r.impact_id: r for r in impacts}
    for state in states:
        record = by_impact.get(state.impact_id)
        if record is None:
            raise ContinuityBindingError(
                f"Continuity state {state.continuity_id!r} references impact "
                f"{state.impact_id!r}, which is not persisted for candidate "
                f"{candidate_id!r}")
        if (state.historical_baseline_id != record.candidate_baseline_id
                or state.historical_baseline_config_hash
                != record.candidate_baseline_config_hash
                or state.target_baseline_id != record.to_baseline_id
                or state.target_baseline_config_hash
                != record.to_baseline_config_hash
                or state.application_id != record.application_id
                or state.candidate_treatment_id != record.candidate_treatment_id):
            raise ContinuityBindingError(
                f"Continuity state {state.continuity_id!r} does not bind the "
                "exact persisted impact chain")

    return CandidateEvidenceContinuityChain(
        candidate_id=candidate_id,
        impact_records=tuple(sorted(
            impacts,
            key=lambda r: (r.candidate_baseline_id, r.to_baseline_id, r.impact_id),
        )),
        continuity_states=tuple(sorted(
            states,
            key=lambda s: (
                s.historical_baseline_id, s.target_baseline_id, s.continuity_id),
        )),
    )
