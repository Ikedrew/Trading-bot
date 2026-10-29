"""Wave 6.1B — persisted baseline-transition impact history.

Connects the PURE Wave 6.1A classifier
(research_engine.lifecycle.candidate_impact_classifier) to persisted
lifecycle truth. This module answers ONE question:

    Which exact historical candidate and which exact VERIFIED N → N+1
    production transition does a Wave 6.1A impact classification belong
    to, and can that impact truth survive restart without rewriting
    history?

CONTRACT:
    - The VERIFIED transition is reconstructed ONLY from persisted Wave 5
      truth: the append-only ApplicationLedger (control_plane.
      application_ledger) and the fsynced ApplicationService operation
      file (control_plane.application_service). The current active
      baseline pointer is NEVER consulted. Anything missing, ambiguous,
      or unverified FAILS CLOSED.
    - Candidate historical truth is the persisted CandidateRegistry
      baseline binding plus the persisted historical evaluation
      provenance (baseline_id, config_hash, treatment_id, frozen
      treatment_spec). The mutable CandidateRecord.change_definition is
      NEVER consulted. Candidate @ N remains Candidate @ N permanently.
    - Eligibility is exact: a candidate is classified against a
      transition ONLY when candidate.baseline_id ==
      transition.from_baseline_id AND candidate.baseline_config_hash ==
      transition.from_baseline_config_hash. A Candidate @ N is never
      treated as a Candidate @ N+1.
    - Classification semantics, scope parsing, overlap mathematics and
      reason codes belong ENTIRELY to Wave 6.1A. This module only binds
      history and persists the result.
    - Impact identity is deterministic (canonical JSON + sha256) over
      candidate identity + candidate historical baseline + exact
      transition identity. No random UUID, no wall clock.
    - The impact store is append-only: historical records are never
      overwritten, duplicate identical assessments are idempotent, and
      the same identity with conflicting persisted content FAILS CLOSED.
    - ZERO scientific side effects: no evidence action, no candidate
      lifecycle mutation, no evaluation/recommendation/decision/
      application mutation, no deployment, no baseline/production
      mutation. Impact history only.

CHRONOLOGY (HARDENING 1.2):
    No wall-clock timestamp is persisted, and none is required for
    scientific correctness. Canonical chronology is deterministic and
    reconstructable after restart/restore from persisted truth alone:

      - the verified baseline sequence (from_baseline_id -> to_baseline_id,
        i.e. N -> N+1) plus the transition references that bind this record
        to that exact transition (impact_id, application_id, from/to
        baseline identity), and
      - the append order of the append-only store.

    Canonical listing/chain order is the deterministic key
    (candidate_baseline_id, to_baseline_id, impact_id) -- never wall-clock
    and never the active-baseline pointer.

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
from pathlib import Path
from typing import Any

from research_engine.control_plane.application_ledger import (
    ApplicationLedger,
    ApplicationState,
)
from research_engine.lifecycle.candidate_impact_classifier import (
    CandidateImpactResult,
    classify_candidate_impact,
)
from research_engine.lifecycle.treatment_provenance import validate_treatment_spec

__all__ = [
    "TransitionNotVerifiedError",
    "CandidateHistoricalProvenanceError",
    "CandidateBaselineMismatchError",
    "ImpactConflictError",
    "VerifiedBaselineTransition",
    "CandidateHistoricalIdentity",
    "CandidateBaselineImpactRecord",
    "CandidateImpactHistoryStore",
    "reconstruct_verified_transition",
    "resolve_candidate_historical_identity",
    "compute_impact_id",
    "assess_candidate_impact",
]


# ─── Fail-closed errors ───────────────────────────────────────────────────────


class TransitionNotVerifiedError(ValueError):
    """The exact VERIFIED N → N+1 transition cannot be proven from persisted
    Wave 5 truth. Impact assessment is not authorized (fail closed)."""


class CandidateHistoricalProvenanceError(ValueError):
    """The candidate's persisted historical truth (baseline binding, config
    hash, frozen treatment provenance) cannot be established (fail closed)."""


class CandidateBaselineMismatchError(ValueError):
    """The candidate does not belong to the transition's from-baseline
    (baseline_id or config-hash mismatch). Fail closed; no intermediate
    candidate history is manufactured."""


class ImpactConflictError(ValueError):
    """The same impact identity carries conflicting persisted content.
    Historical truth is never overwritten (fail closed)."""


# ─── Canonical JSON / digest conventions (existing project convention) ────────


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _scope_to_dict(scope: Any) -> dict[str, Any] | None:
    """Serialize a Wave 6.1A NormalizedScope (or None when INDETERMINATE)."""
    if scope is None:
        return None
    return {
        "symbols": list(scope.symbols) if scope.symbols is not None else None,
        "patterns": list(scope.patterns) if scope.patterns is not None else None,
    }


# ─── Verified baseline transition (persisted Wave 5 truth ONLY) ───────────────

# Ledger identity fields that must be identical across every row of the
# application's history and match the persisted operation authorization.
_TRANSITION_IDENTITIES = (
    "candidate_id",
    "recommendation_id",
    "evaluation_id",
    "treatment_id",
    "baseline_id",
    "baseline_config_hash",
)


@dataclass(frozen=True)
class VerifiedBaselineTransition:
    """The exact VERIFIED transition Baseline N → governed application →
    verified production change → Baseline N+1, derived from persisted Wave 5
    truth. Not a new authority — a deterministic projection of the existing
    application ledger + deployment operation authorities."""

    from_baseline_id: str
    from_baseline_config_hash: str
    to_baseline_id: str
    to_baseline_config_hash: str
    application_id: str
    deployed_treatment_id: str
    deployed_treatment_spec: str
    operation_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_baseline_id": self.from_baseline_id,
            "from_baseline_config_hash": self.from_baseline_config_hash,
            "to_baseline_id": self.to_baseline_id,
            "to_baseline_config_hash": self.to_baseline_config_hash,
            "application_id": self.application_id,
            "deployed_treatment_id": self.deployed_treatment_id,
            "deployed_treatment_spec": self.deployed_treatment_spec,
            "operation_id": self.operation_id,
        }


def _load_operation(operations_dir: str | Path, application_id: str) -> dict | None:
    """Load the persisted ApplicationService operation file using the
    service's OWN path convention (content digest of the application id).
    Reuses the existing authority instead of inventing a second one."""
    from research_engine.control_plane.application_service import digest

    if not isinstance(application_id, str) or not application_id.strip():
        raise TransitionNotVerifiedError("Application ID required")
    path = Path(operations_dir) / (digest(application_id) + ".json")
    if not path.is_file():
        return None
    try:
        op = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return op if isinstance(op, dict) else None


def reconstruct_verified_transition(
    application_id: str,
    *,
    application_path: str | Path,
    operations_dir: str | Path,
) -> VerifiedBaselineTransition:
    """Reconstruct the exact VERIFIED N → N+1 transition for ONE application.

    Fail closed unless persisted Wave 5 truth proves ALL of: exactly one
    VERIFIED ledger row with a complete identity tuple and a frozen canonical
    deployed treatment_spec; every ledger row of that application carrying the
    identical identity; an APPROVED_NOT_DEPLOYED row for the same identity;
    and a COMPLETED persisted operation whose authorized application row
    matches the ledger, whose verification evidence digest matches the ledger
    row, and whose snapshot/old_snapshot prove the exact from/to baseline
    identity pair. The active-baseline pointer is never consulted.
    """
    ledger = ApplicationLedger(application_path)
    apps = [r for r in ledger.list_all() if r.application_id == application_id]
    if not apps:
        raise TransitionNotVerifiedError(
            f"No application history for {application_id!r}")

    verified = [r for r in apps if r.state == ApplicationState.VERIFIED]
    if len(verified) != 1:
        raise TransitionNotVerifiedError(
            f"Application {application_id!r} does not have exactly one "
            "VERIFIED row")
    row = verified[0]

    for name in _TRANSITION_IDENTITIES:
        if not str(getattr(row, name, "") or "").strip():
            raise TransitionNotVerifiedError(
                f"VERIFIED application row missing {name!r}")
    for other in apps:
        if (any(getattr(other, name) != getattr(row, name)
                for name in _TRANSITION_IDENTITIES)
                or other.treatment_spec != row.treatment_spec):
            raise TransitionNotVerifiedError(
                f"Application history conflict for {application_id!r}")
    if not any(r.state == ApplicationState.APPROVED_NOT_DEPLOYED for r in apps):
        raise TransitionNotVerifiedError(
            f"Application {application_id!r} has no APPROVED_NOT_DEPLOYED row")

    try:
        deployed_spec = validate_treatment_spec(
            row.treatment_spec, row.treatment_id, required=True)
    except ValueError as exc:
        raise TransitionNotVerifiedError(
            f"Deployed frozen treatment_spec unavailable: {exc}") from exc

    op = _load_operation(operations_dir, application_id)
    if op is None:
        raise TransitionNotVerifiedError(
            f"Persisted deployment operation missing for {application_id!r}")
    if op.get("phase") != "COMPLETED":
        raise TransitionNotVerifiedError(
            f"Deployment operation not COMPLETED (phase={op.get('phase')!r})")

    approved = next(
        r for r in apps if r.state == ApplicationState.APPROVED_NOT_DEPLOYED)
    if op.get("application") != approved.to_dict():
        raise TransitionNotVerifiedError(
            "Persisted authorization changed between ledger and operation")
    if op.get("operation_id") != row.deployment_reference:
        raise TransitionNotVerifiedError("Foreign deployment ledger reference")
    verification = op.get("verification")
    if not isinstance(verification, dict) or (
            _digest(verification) != row.verification_evidence):
        raise TransitionNotVerifiedError(
            "Verification evidence does not match the VERIFIED ledger row")

    old_snapshot = op.get("old_snapshot")
    snapshot = op.get("snapshot")
    if not isinstance(old_snapshot, dict) or not isinstance(snapshot, dict):
        raise TransitionNotVerifiedError("Operation lacks snapshot truth")
    if (old_snapshot.get("snapshot_id") != row.baseline_id
            or old_snapshot.get("config_hash") != row.baseline_config_hash):
        raise TransitionNotVerifiedError(
            "Operation old_snapshot does not match the ledger from-baseline")
    to_id = snapshot.get("snapshot_id")
    to_hash = snapshot.get("config_hash")
    if not isinstance(to_id, str) or not to_id.strip():
        raise TransitionNotVerifiedError(
            "Operation to-baseline missing snapshot_id")
    if not isinstance(to_hash, str) or not to_hash.strip():
        raise TransitionNotVerifiedError(
            "Operation to-baseline missing config_hash")
    if to_id == row.baseline_id:
        raise TransitionNotVerifiedError(
            "To-baseline equals from-baseline; not a transition")

    return VerifiedBaselineTransition(
        from_baseline_id=row.baseline_id,
        from_baseline_config_hash=row.baseline_config_hash,
        to_baseline_id=to_id,
        to_baseline_config_hash=to_hash,
        application_id=application_id,
        deployed_treatment_id=row.treatment_id,
        deployed_treatment_spec=deployed_spec,
        operation_id=str(op.get("operation_id", "")),
    )


# ─── Candidate historical authority ───────────────────────────────────────────


@dataclass(frozen=True)
class CandidateHistoricalIdentity:
    """The persisted historical truth of ONE candidate: its baseline binding,
    baseline config identity, and frozen treatment provenance. Immutable:
    Candidate @ N remains Candidate @ N permanently."""

    candidate_id: str
    baseline_id: str
    baseline_config_hash: str
    treatment_id: str
    treatment_spec: str


def _read_evaluation_rows(evaluations_dir: str | Path, candidate_id: str) -> list[dict]:
    if (not isinstance(candidate_id, str) or not candidate_id.strip()
            or Path(candidate_id).name != candidate_id
            or "/" in candidate_id or "\\" in candidate_id):
        raise CandidateHistoricalProvenanceError("Unsafe candidate ID")
    path = Path(evaluations_dir) / f"{candidate_id}.jsonl"
    if not path.is_file():
        raise CandidateHistoricalProvenanceError(
            f"No persisted evaluation history for candidate {candidate_id!r}")
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CandidateHistoricalProvenanceError(
                f"Corrupt evaluation history for {candidate_id!r}") from exc
        if not isinstance(row, dict):
            raise CandidateHistoricalProvenanceError(
                f"Corrupt evaluation history for {candidate_id!r}")
        rows.append(row)
    return rows


def resolve_candidate_historical_identity(
    candidate_id: str,
    *,
    registry_dir: str | Path,
    evaluations_dir: str | Path,
) -> CandidateHistoricalIdentity:
    """Resolve the candidate's persisted historical truth. The mutable
    CandidateRecord.change_definition is NEVER consulted."""
    from research_engine.v10.candidates.candidate_registry import CandidateRegistry

    record = CandidateRegistry(str(registry_dir)).get(candidate_id)
    if record is None:
        raise CandidateHistoricalProvenanceError(
            f"Unknown candidate {candidate_id!r}")
    if not isinstance(record.baseline_id, str) or not record.baseline_id.strip():
        raise CandidateHistoricalProvenanceError(
            f"Candidate {candidate_id!r} has no persisted baseline binding")

    rows = _read_evaluation_rows(evaluations_dir, candidate_id)
    proven = [
        r for r in rows
        if isinstance(r.get("treatment_id"), str) and r["treatment_id"].strip()
        and isinstance(r.get("treatment_spec"), str) and r["treatment_spec"].strip()
    ]
    if not proven:
        raise CandidateHistoricalProvenanceError(
            f"Candidate {candidate_id!r} has no frozen treatment provenance")

    identities = {
        (
            str(r.get("baseline_id", "")),
            str(r.get("config_hash", "")),
            r["treatment_id"],
            r["treatment_spec"],
        )
        for r in proven
    }
    if len(identities) != 1:
        raise CandidateHistoricalProvenanceError(
            f"Ambiguous historical provenance for candidate {candidate_id!r}")
    baseline_id, config_hash, treatment_id, spec = identities.pop()

    if baseline_id != record.baseline_id:
        raise CandidateHistoricalProvenanceError(
            "Evaluation baseline binding does not match the candidate registry")
    if not config_hash.strip():
        raise CandidateHistoricalProvenanceError(
            "Historical baseline config identity unavailable")
    try:
        spec = validate_treatment_spec(spec, treatment_id, required=True)
    except ValueError as exc:
        raise CandidateHistoricalProvenanceError(
            f"Frozen candidate treatment_spec invalid: {exc}") from exc

    return CandidateHistoricalIdentity(
        candidate_id=candidate_id,
        baseline_id=baseline_id,
        baseline_config_hash=config_hash,
        treatment_id=treatment_id,
        treatment_spec=spec,
    )


def _ensure_from_baseline_eligibility(
    candidate: CandidateHistoricalIdentity,
    transition: VerifiedBaselineTransition,
) -> None:
    """Candidate @ N is classified ONLY against transitions FROM N."""
    if candidate.baseline_id != transition.from_baseline_id:
        raise CandidateBaselineMismatchError(
            f"Candidate {candidate.candidate_id!r} is bound to baseline "
            f"{candidate.baseline_id!r}, not the transition from-baseline "
            f"{transition.from_baseline_id!r}")
    if candidate.baseline_config_hash != transition.from_baseline_config_hash:
        raise CandidateBaselineMismatchError(
            f"Candidate {candidate.candidate_id!r} historical config hash does "
            "not match the transition from-baseline config identity")


# ─── Persisted impact record + deterministic identity ─────────────────────────

_IMPACT_IDENTITY_FIELDS = (
    "candidate_id",
    "candidate_baseline_id",
    "candidate_baseline_config_hash",
    "from_baseline_id",
    "from_baseline_config_hash",
    "to_baseline_id",
    "to_baseline_config_hash",
    "application_id",
    "candidate_treatment_id",
    "deployed_treatment_id",
)


def compute_impact_id(**identity: str) -> str:
    """Deterministic impact identity binding candidate identity + candidate
    historical baseline + exact verified transition identity. Canonical JSON
    + SHA256 (existing project convention). No random UUID, no wall clock."""
    missing = [f for f in _IMPACT_IDENTITY_FIELDS if f not in identity]
    if missing:
        raise ValueError(f"Impact identity missing {missing}")
    extra = [k for k in identity if k not in _IMPACT_IDENTITY_FIELDS]
    if extra:
        raise ValueError(f"Impact identity has unexpected fields {extra}")
    if any(not str(identity[f]).strip() for f in _IMPACT_IDENTITY_FIELDS):
        raise ValueError("Impact identity fields must be non-empty")
    return "CBI-" + _digest({f: identity[f] for f in _IMPACT_IDENTITY_FIELDS})


@dataclass(frozen=True)
class CandidateBaselineImpactRecord:
    """The smallest immutable record of Candidate @ N + verified N → N+1
    transition + Wave 6.1A classification result. Large objects (frozen
    treatment_spec texts, snapshots) are NOT duplicated: they remain in their
    existing persisted authorities and stay reconstructable through the
    identity fields below."""

    impact_id: str
    candidate_id: str
    candidate_baseline_id: str
    candidate_baseline_config_hash: str
    from_baseline_id: str
    from_baseline_config_hash: str
    to_baseline_id: str
    to_baseline_config_hash: str
    application_id: str
    candidate_treatment_id: str
    deployed_treatment_id: str
    classification: str
    reason_codes: tuple[str, ...]
    candidate_scope: dict[str, Any] | None
    deployed_scope: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "impact_id": self.impact_id,
            "candidate_id": self.candidate_id,
            "candidate_baseline_id": self.candidate_baseline_id,
            "candidate_baseline_config_hash": self.candidate_baseline_config_hash,
            "from_baseline_id": self.from_baseline_id,
            "from_baseline_config_hash": self.from_baseline_config_hash,
            "to_baseline_id": self.to_baseline_id,
            "to_baseline_config_hash": self.to_baseline_config_hash,
            "application_id": self.application_id,
            "candidate_treatment_id": self.candidate_treatment_id,
            "deployed_treatment_id": self.deployed_treatment_id,
            "classification": self.classification,
            "reason_codes": list(self.reason_codes),
            "candidate_scope": self.candidate_scope,
            "deployed_scope": self.deployed_scope,
        }

    def canonical_json(self) -> str:
        return _canonical(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CandidateBaselineImpactRecord":
        shape = cls(
            impact_id="", candidate_id="", candidate_baseline_id="",
            candidate_baseline_config_hash="", from_baseline_id="",
            from_baseline_config_hash="", to_baseline_id="",
            to_baseline_config_hash="", application_id="",
            candidate_treatment_id="", deployed_treatment_id="",
            classification="", reason_codes=(), candidate_scope=None,
            deployed_scope=None,
        ).to_dict()
        if not isinstance(data, dict) or set(data) != set(shape):
            raise ValueError("Malformed candidate impact record")
        reason_codes = data["reason_codes"]
        if (not isinstance(reason_codes, list)
                or not all(isinstance(c, str) for c in reason_codes)):
            raise ValueError("Malformed impact reason_codes")
        for field in ("candidate_scope", "deployed_scope"):
            scope = data[field]
            if scope is not None and (
                    not isinstance(scope, dict)
                    or set(scope) != {"symbols", "patterns"}):
                raise ValueError(f"Malformed impact {field}")
        return cls(
            impact_id=data["impact_id"],
            candidate_id=data["candidate_id"],
            candidate_baseline_id=data["candidate_baseline_id"],
            candidate_baseline_config_hash=data["candidate_baseline_config_hash"],
            from_baseline_id=data["from_baseline_id"],
            from_baseline_config_hash=data["from_baseline_config_hash"],
            to_baseline_id=data["to_baseline_id"],
            to_baseline_config_hash=data["to_baseline_config_hash"],
            application_id=data["application_id"],
            candidate_treatment_id=data["candidate_treatment_id"],
            deployed_treatment_id=data["deployed_treatment_id"],
            classification=data["classification"],
            reason_codes=tuple(reason_codes),
            candidate_scope=data["candidate_scope"],
            deployed_scope=data["deployed_scope"],
        )


# ─── Durable append-only impact history store ─────────────────────────────────

_IMPACT_DIR = "data/research/lifecycle/impact_history"
_IMPACT_FILE = "candidate_impact_history.jsonl"


class CandidateImpactHistoryStore:
    """Append-only, deterministic impact history. Existing records are NEVER
    overwritten; a duplicate identical assessment is idempotent; the same
    identity with conflicting content FAILS CLOSED. There is deliberately no
    'current impact' field: per-transition history is preserved."""

    def __init__(self, impact_dir: str | Path | None = None) -> None:
        self._dir = Path(impact_dir or _IMPACT_DIR)
        self._records: list[CandidateBaselineImpactRecord] = []
        self._load()

    @property
    def path(self) -> Path:
        return self._dir / _IMPACT_FILE

    def get(self, impact_id: str) -> CandidateBaselineImpactRecord | None:
        for record in self._records:
            if record.impact_id == impact_id:
                return record
        return None

    def list_all(self) -> list[CandidateBaselineImpactRecord]:
        return list(self._records)

    def append(self, record: CandidateBaselineImpactRecord) -> bool:
        """Persist ONE impact record. True when newly appended; False for an
        idempotent duplicate; raises ImpactConflictError on conflicting
        content under the same identity."""
        if not isinstance(record, CandidateBaselineImpactRecord):
            raise ValueError("Not a candidate impact record")
        existing = self.get(record.impact_id)
        if existing is not None:
            if existing.to_dict() != record.to_dict():
                raise ImpactConflictError(
                    f"Impact identity {record.impact_id!r} already persisted "
                    "with conflicting content")
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
        by_id: dict[str, CandidateBaselineImpactRecord] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = CandidateBaselineImpactRecord.from_dict(json.loads(line))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(
                    f"Corrupt impact history in {self.path}: {exc}") from exc
            existing = by_id.get(record.impact_id)
            if existing is not None and existing.to_dict() != record.to_dict():
                raise ImpactConflictError(
                    f"Conflicting persisted content for impact "
                    f"{record.impact_id!r}")
            if existing is None:
                by_id[record.impact_id] = record
        self._records = list(by_id.values())


# ─── Assessment orchestration (6.1A remains classification authority) ─────────


def assess_candidate_impact(
    candidate_id: str,
    application_id: str,
    *,
    registry_dir: str | Path,
    evaluations_dir: str | Path,
    application_path: str | Path,
    operations_dir: str | Path,
    impact_dir: str | Path | None = None,
) -> CandidateBaselineImpactRecord:
    """Classify ONE historical candidate against ONE exact VERIFIED
    N → N+1 transition and persist the immutable impact record.

    Zero scientific side effects: reads the persisted lifecycle authorities
    and appends ONLY to the impact history store. Classification is delegated
    ENTIRELY to the Wave 6.1A pure classifier.
    """
    transition = reconstruct_verified_transition(
        application_id, application_path=application_path,
        operations_dir=operations_dir)
    candidate = resolve_candidate_historical_identity(
        candidate_id, registry_dir=registry_dir, evaluations_dir=evaluations_dir)
    _ensure_from_baseline_eligibility(candidate, transition)

    # Wave 6.1A is the sole classification authority: scope parsing, overlap
    # mathematics, classification rules and reason codes are NOT duplicated.
    result: CandidateImpactResult = classify_candidate_impact(
        candidate.treatment_id,
        candidate.treatment_spec,
        transition.deployed_treatment_id,
        transition.deployed_treatment_spec,
    )

    impact_id = compute_impact_id(
        candidate_id=candidate.candidate_id,
        candidate_baseline_id=candidate.baseline_id,
        candidate_baseline_config_hash=candidate.baseline_config_hash,
        from_baseline_id=transition.from_baseline_id,
        from_baseline_config_hash=transition.from_baseline_config_hash,
        to_baseline_id=transition.to_baseline_id,
        to_baseline_config_hash=transition.to_baseline_config_hash,
        application_id=transition.application_id,
        candidate_treatment_id=candidate.treatment_id,
        deployed_treatment_id=transition.deployed_treatment_id,
    )
    record = CandidateBaselineImpactRecord(
        impact_id=impact_id,
        candidate_id=candidate.candidate_id,
        candidate_baseline_id=candidate.baseline_id,
        candidate_baseline_config_hash=candidate.baseline_config_hash,
        from_baseline_id=transition.from_baseline_id,
        from_baseline_config_hash=transition.from_baseline_config_hash,
        to_baseline_id=transition.to_baseline_id,
        to_baseline_config_hash=transition.to_baseline_config_hash,
        application_id=transition.application_id,
        candidate_treatment_id=candidate.treatment_id,
        deployed_treatment_id=transition.deployed_treatment_id,
        classification=result.classification.value,
        reason_codes=result.reason_codes,
        candidate_scope=_scope_to_dict(result.candidate_scope),
        deployed_scope=_scope_to_dict(result.deployed_scope),
    )
    CandidateImpactHistoryStore(impact_dir).append(record)
    return record







