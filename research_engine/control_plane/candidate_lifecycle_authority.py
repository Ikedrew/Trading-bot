"""Repair Block 3 — ONE canonical candidate lifecycle over every store.

Audit 3 established that the research engine had THREE partially connected
candidate systems (continuous-optimisation lifecycle, Track-A candidate
registry, production application lifecycle) plus modern shadow governance, with
no single authoritative end-to-end state machine.

This module defines that single machine:

* ``CanonicalState`` — the one vocabulary the whole system is projected onto.
* ``LEGACY_STATE_MAP`` — lossless, explicit mapping from every historical
  status vocabulary.  Historical identities are NEVER silently migrated; the
  legacy value is always preserved alongside the canonical derivation.
* ``TRANSITION_AUTHORITY`` — WHO may make each transition (engine vs human vs
  governed application vs runtime monitor).
* ``CandidateLifecycleLedger`` — append-only history.  The canonical current
  state is a PROJECTION of this history plus the underlying authorities; the
  ledger itself is never rewritten in place.
* ``derive_canonical_lifecycle`` — deterministic derivation with explicit
  precedence, so two systems can never silently disagree.

This module never trades, never writes a policy file, never mutates a registry
and never grants live authority.  It is a read/record layer plus the state
vocabulary; the governed operations live in ``candidate_lifecycle_service``.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

LIFECYCLE_SCHEMA = "candidate_lifecycle_v1"
DEFAULT_LIFECYCLE_LEDGER = Path("data/research/governance/candidate_lifecycle.jsonl")


class CanonicalState:
    """The ONE canonical candidate lifecycle vocabulary."""

    PROPOSED = "PROPOSED"
    VALIDATION_QUEUED = "VALIDATION_QUEUED"
    VALIDATING = "VALIDATING"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    VALIDATED = "VALIDATED"
    FORWARD_VALIDATED = "FORWARD_VALIDATED"
    SHADOW_VALIDATION_ACTIVE = "SHADOW_VALIDATION_ACTIVE"
    SHADOW_VALIDATED = "SHADOW_VALIDATED"
    READY_FOR_PROMOTION_REVIEW = "READY_FOR_PROMOTION_REVIEW"
    REJECTED = "REJECTED"
    ACCEPTED = "ACCEPTED"
    APPROVED_NOT_DEPLOYED = "APPROVED_NOT_DEPLOYED"
    DEPLOYED = "DEPLOYED"
    VERIFIED = "VERIFIED"
    DISABLED = "DISABLED"
    REVOKED = "REVOKED"
    SUPERSEDED = "SUPERSEDED"
    ROLLED_BACK = "ROLLED_BACK"
    INVALIDATED_UPSTREAM = "INVALIDATED_UPSTREAM"
    BLOCKED_UPSTREAM_INVALIDATED = "BLOCKED_UPSTREAM_INVALIDATED"


CANONICAL_STATES = tuple(
    value for name, value in vars(CanonicalState).items()
    if not name.startswith("_") and isinstance(value, str)
)

# States in which a candidate may legitimately affect live trading.  This is a
# DERIVED convenience only: the runtime authority ledger is the real boundary.
LIVE_CAPABLE_STATES = frozenset({
    CanonicalState.DEPLOYED, CanonicalState.VERIFIED,
})

# Terminal / non-progressing states.
TERMINAL_STATES = frozenset({
    CanonicalState.REJECTED, CanonicalState.REVOKED, CanonicalState.SUPERSEDED,
    CanonicalState.ROLLED_BACK, CanonicalState.INVALIDATED_UPSTREAM,
    CanonicalState.BLOCKED_UPSTREAM_INVALIDATED,
})

# Human-approval-required marker used by the Research Lab.
REVIEW_STATE_NOTICE = "NOT LIVE / HUMAN APPROVAL REQUIRED"

_STATE_ORDER = {
    CanonicalState.PROPOSED: 0,
    CanonicalState.VALIDATION_QUEUED: 1,
    CanonicalState.VALIDATING: 2,
    CanonicalState.VALIDATION_FAILED: 3,
    CanonicalState.VALIDATED: 4,
    CanonicalState.FORWARD_VALIDATED: 5,
    CanonicalState.SHADOW_VALIDATION_ACTIVE: 6,
    CanonicalState.SHADOW_VALIDATED: 7,
    CanonicalState.READY_FOR_PROMOTION_REVIEW: 8,
    CanonicalState.ACCEPTED: 9,
    CanonicalState.APPROVED_NOT_DEPLOYED: 10,
    CanonicalState.DEPLOYED: 11,
    CanonicalState.VERIFIED: 12,
    CanonicalState.DISABLED: 13,
    CanonicalState.REVOKED: 14,
    CanonicalState.SUPERSEDED: 15,
    CanonicalState.ROLLED_BACK: 16,
    CanonicalState.REJECTED: 17,
    CanonicalState.INVALIDATED_UPSTREAM: 18,
    CanonicalState.BLOCKED_UPSTREAM_INVALIDATED: 19,
}


# ═══════════════════════════════════════════════════════════════════════════════
# LEGACY VOCABULARY COMPATIBILITY (explicit, lossless, non-migrating)
# ═══════════════════════════════════════════════════════════════════════════════

_TRACK_A = {
    "PROPOSED": CanonicalState.PROPOSED,
    "VALIDATING": CanonicalState.VALIDATING,
    "VALIDATED": CanonicalState.VALIDATED,
    "FORWARD_VALIDATED": CanonicalState.FORWARD_VALIDATED,
    "SHADOW_TESTING": CanonicalState.SHADOW_VALIDATION_ACTIVE,
    "SHADOW_VALIDATION_INSUFFICIENT_DATA": CanonicalState.SHADOW_VALIDATION_ACTIVE,
    "SHADOW_VALIDATION_ACTIVE": CanonicalState.SHADOW_VALIDATION_ACTIVE,
    "SHADOW_VALIDATED": CanonicalState.SHADOW_VALIDATED,
    "SHADOW_VALIDATION_INVALID": CanonicalState.VALIDATION_FAILED,
    "READY_FOR_PROMOTION_REVIEW": CanonicalState.READY_FOR_PROMOTION_REVIEW,
    "READY_FOR_REVIEW": CanonicalState.READY_FOR_PROMOTION_REVIEW,
    "ACCEPTED": CanonicalState.ACCEPTED,
    "REJECTED": CanonicalState.REJECTED,
    "FAILED_VALIDATION": CanonicalState.VALIDATION_FAILED,
    "REGRESSION_DETECTED": CanonicalState.VALIDATION_FAILED,
    "ARCHIVED": CanonicalState.REJECTED,
}

_CONTINUOUS = {
    "PROPOSED": CanonicalState.PROPOSED,
    "READY_FOR_TEST": CanonicalState.VALIDATION_QUEUED,
    "TESTING": CanonicalState.VALIDATING,
    "FORWARD_VALIDATED": CanonicalState.FORWARD_VALIDATED,
    "SHADOW_VALIDATION_ACTIVE": CanonicalState.SHADOW_VALIDATION_ACTIVE,
    "SHADOW_VALIDATED": CanonicalState.SHADOW_VALIDATED,
    "READY_FOR_PROMOTION_REVIEW": CanonicalState.READY_FOR_PROMOTION_REVIEW,
    "ACCEPTED": CanonicalState.ACCEPTED,
    "REJECTED": CanonicalState.REJECTED,
    "VALIDATION_FAILED": CanonicalState.VALIDATION_FAILED,
    "VALIDATED": CanonicalState.VALIDATED,
    "VALIDATING": CanonicalState.VALIDATING,
}

_APPLICATION = {
    "NOT_APPLIED": "",
    "APPROVED_NOT_DEPLOYED": CanonicalState.APPROVED_NOT_DEPLOYED,
    "DEPLOYED": CanonicalState.DEPLOYED,
    "VERIFIED": CanonicalState.VERIFIED,
    "ROLLED_BACK": CanonicalState.ROLLED_BACK,
    "FAILED": "",
    "UNKNOWN": "",
}

_SHADOW_GOVERNANCE = {
    "SHADOW_VALIDATION_ACTIVE": CanonicalState.SHADOW_VALIDATION_ACTIVE,
    "SHADOW_VALIDATED": CanonicalState.SHADOW_VALIDATED,
    "READY_FOR_PROMOTION_REVIEW": CanonicalState.READY_FOR_PROMOTION_REVIEW,
    "SHADOW_VALIDATION_INVALID": CanonicalState.VALIDATION_FAILED,
    "SHADOW_VALIDATION_INSUFFICIENT_DATA": CanonicalState.SHADOW_VALIDATION_ACTIVE,
}

_VALIDATION_QUEUE = {
    "QUEUED": CanonicalState.VALIDATION_QUEUED,
    "RUNNING": CanonicalState.VALIDATING,
    "COMPLETED": CanonicalState.VALIDATED,
    "FAILED": CanonicalState.VALIDATION_FAILED,
    "BLOCKED": CanonicalState.BLOCKED_UPSTREAM_INVALIDATED,
    "REVIEW_REQUIRED": CanonicalState.READY_FOR_PROMOTION_REVIEW,
}

PRODUCTION_AUTHORITY_MAP = {
    "ISSUED": CanonicalState.APPROVED_NOT_DEPLOYED,
    "ACTIVE": CanonicalState.VERIFIED,
    "DISABLED": CanonicalState.DISABLED,
    "REVOKED": CanonicalState.REVOKED,
    "SUPERSEDED": CanonicalState.SUPERSEDED,
    "ROLLED_BACK": CanonicalState.ROLLED_BACK,
}

LEGACY_STATE_MAP: dict[str, dict[str, str]] = {
    "TRACK_A_CANDIDATE_REGISTRY": _TRACK_A,
    "CONTINUOUS_OPTIMISATION_REGISTRY": _CONTINUOUS,
    "PRODUCTION_APPLICATION_LEDGER": _APPLICATION,
    "SHADOW_CANDIDATE_GOVERNANCE": _SHADOW_GOVERNANCE,
    "VALIDATION_QUEUE": _VALIDATION_QUEUE,
    "PRODUCTION_AUTHORITY": PRODUCTION_AUTHORITY_MAP,
}


class LifecycleMappingError(RuntimeError):
    """An unknown legacy status was presented for canonical mapping."""


def to_canonical(store: str, legacy_status: str) -> str:
    """Map a legacy status to the canonical vocabulary (fail closed).

    Returns "" when the legacy status carries no canonical meaning (e.g. an
    application row that has not yet been applied).  An UNKNOWN status for a
    known store is an error — never a silent default.
    """
    table = LEGACY_STATE_MAP.get(store)
    if table is None:
        raise LifecycleMappingError(f"UNKNOWN_LIFECYCLE_STORE:{store}")
    if legacy_status not in table:
        raise LifecycleMappingError(f"UNKNOWN_LEGACY_STATUS:{store}:{legacy_status}")
    return table[legacy_status]


# ═══════════════════════════════════════════════════════════════════════════════
# TRANSITION AUTHORITY — WHO may make each transition
# ═══════════════════════════════════════════════════════════════════════════════

class TransitionAuthority:
    """Named authorities permitted to commit a lifecycle transition."""

    ENGINE = "ENGINE"                    # autonomous research engine
    HUMAN = "HUMAN"                      # explicit human decision
    APPLICATION_SERVICE = "APPLICATION_SERVICE"  # governed deployment path
    RUNTIME_MONITOR = "RUNTIME_MONITOR"  # adverse-evidence / rollback governance
    UPSTREAM = "UPSTREAM"                # scientific basis invalidation


def _t(*authorities: str) -> frozenset[str]:
    return frozenset(authorities)


S = CanonicalState
_E, _H = TransitionAuthority.ENGINE, TransitionAuthority.HUMAN
_A, _M = TransitionAuthority.APPLICATION_SERVICE, TransitionAuthority.RUNTIME_MONITOR
_U = TransitionAuthority.UPSTREAM

TRANSITION_AUTHORITY: dict[str, dict[str, frozenset[str]]] = {
    S.PROPOSED: {
        S.VALIDATION_QUEUED: _t(_E),
        S.VALIDATING: _t(_E),
        S.SHADOW_VALIDATION_ACTIVE: _t(_E),
        S.REJECTED: _t(_H, _E),
        S.BLOCKED_UPSTREAM_INVALIDATED: _t(_U),
        S.INVALIDATED_UPSTREAM: _t(_U),
    },
    S.VALIDATION_QUEUED: {
        S.VALIDATING: _t(_E),
        S.VALIDATION_FAILED: _t(_E),
        S.REJECTED: _t(_H, _E),
        S.BLOCKED_UPSTREAM_INVALIDATED: _t(_U),
        S.INVALIDATED_UPSTREAM: _t(_U),
    },
    S.VALIDATING: {
        S.VALIDATED: _t(_E),
        S.VALIDATION_FAILED: _t(_E),
        S.REJECTED: _t(_H, _E),
        S.BLOCKED_UPSTREAM_INVALIDATED: _t(_U),
        S.INVALIDATED_UPSTREAM: _t(_U),
    },
    S.VALIDATION_FAILED: {
        S.VALIDATING: _t(_E),
        S.REJECTED: _t(_H, _E),
        S.INVALIDATED_UPSTREAM: _t(_U),
        S.BLOCKED_UPSTREAM_INVALIDATED: _t(_U),
    },
    S.VALIDATED: {
        S.FORWARD_VALIDATED: _t(_E),
        S.SHADOW_VALIDATION_ACTIVE: _t(_E),
        S.READY_FOR_PROMOTION_REVIEW: _t(_E),
        S.REJECTED: _t(_H, _E),
        S.INVALIDATED_UPSTREAM: _t(_U),
        S.BLOCKED_UPSTREAM_INVALIDATED: _t(_U),
    },
    S.FORWARD_VALIDATED: {
        S.SHADOW_VALIDATION_ACTIVE: _t(_E),
        S.READY_FOR_PROMOTION_REVIEW: _t(_E),
        S.REJECTED: _t(_H, _E),
        S.INVALIDATED_UPSTREAM: _t(_U),
        S.BLOCKED_UPSTREAM_INVALIDATED: _t(_U),
    },
    S.SHADOW_VALIDATION_ACTIVE: {
        S.SHADOW_VALIDATED: _t(_E),
        S.VALIDATION_FAILED: _t(_E),
        S.REJECTED: _t(_H, _E),
        S.INVALIDATED_UPSTREAM: _t(_U),
        S.BLOCKED_UPSTREAM_INVALIDATED: _t(_U),
    },
    S.SHADOW_VALIDATED: {
        S.READY_FOR_PROMOTION_REVIEW: _t(_E),
        S.SHADOW_VALIDATION_ACTIVE: _t(_E),
        S.REJECTED: _t(_H, _E),
        S.INVALIDATED_UPSTREAM: _t(_U),
        S.BLOCKED_UPSTREAM_INVALIDATED: _t(_U),
    },
    # READY_FOR_PROMOTION_REVIEW is NOT approval.  Only an explicit HUMAN
    # decision may leave it for ACCEPTED / REJECTED.
    S.READY_FOR_PROMOTION_REVIEW: {
        S.ACCEPTED: _t(_H),
        S.REJECTED: _t(_H),
        S.VALIDATING: _t(_E),          # revalidation required (stale evidence)
        S.INVALIDATED_UPSTREAM: _t(_U),
        S.BLOCKED_UPSTREAM_INVALIDATED: _t(_U),
    },
    S.ACCEPTED: {
        S.APPROVED_NOT_DEPLOYED: _t(_A),
        S.REVOKED: _t(_H, _M),
        S.INVALIDATED_UPSTREAM: _t(_U),
    },
    S.APPROVED_NOT_DEPLOYED: {
        S.DEPLOYED: _t(_A),
        S.VERIFIED: _t(_A),
        S.REVOKED: _t(_H, _M),
        S.SUPERSEDED: _t(_A, _H),
        S.INVALIDATED_UPSTREAM: _t(_U),
    },
    S.DEPLOYED: {
        S.VERIFIED: _t(_A),
        S.ROLLED_BACK: _t(_A, _H, _M),
        S.DISABLED: _t(_H, _M),
        S.REVOKED: _t(_H, _M),
        S.SUPERSEDED: _t(_H, _M),
        S.INVALIDATED_UPSTREAM: _t(_U),
    },
    S.VERIFIED: {
        S.ROLLED_BACK: _t(_A, _H, _M),
        S.DISABLED: _t(_H, _M),
        S.REVOKED: _t(_H, _M),
        S.SUPERSEDED: _t(_H, _M),
        S.INVALIDATED_UPSTREAM: _t(_U),
    },
    S.DISABLED: {
        S.VERIFIED: _t(_H),
        S.REVOKED: _t(_H, _M),
        S.SUPERSEDED: _t(_H, _M),
        S.ROLLED_BACK: _t(_H, _M),
    },
    S.REVOKED: {},
    S.SUPERSEDED: {},
    S.ROLLED_BACK: {},
    S.REJECTED: {},
    S.INVALIDATED_UPSTREAM: {},
    S.BLOCKED_UPSTREAM_INVALIDATED: {},
}


def authorities_for(from_state: str, to_state: str) -> frozenset[str]:
    return TRANSITION_AUTHORITY.get(from_state, {}).get(to_state, frozenset())


def is_authorized_transition(from_state: str, to_state: str, authority: str) -> bool:
    return authority in authorities_for(from_state, to_state)


def validate_lifecycle_transition(from_state: str, to_state: str, authority: str) -> None:
    """Raise when a transition is unknown or the authority is not permitted."""
    if from_state not in TRANSITION_AUTHORITY:
        raise ValueError(f"UNKNOWN_LIFECYCLE_STATE:{from_state}")
    if to_state not in CANONICAL_STATES:
        raise ValueError(f"UNKNOWN_LIFECYCLE_STATE:{to_state}")
    if from_state == to_state:
        return
    allowed = authorities_for(from_state, to_state)
    if not allowed:
        raise ValueError(f"LIFECYCLE_TRANSITION_REFUSED:{from_state}->{to_state}")
    if authority not in allowed:
        raise ValueError(
            f"LIFECYCLE_AUTHORITY_REFUSED:{from_state}->{to_state}:{authority}")


# ═══════════════════════════════════════════════════════════════════════════════
# APPEND-ONLY LIFECYCLE HISTORY
# ═══════════════════════════════════════════════════════════════════════════════

# Consequential transitions that MUST produce a durable append-only event.
REQUIRED_HISTORY_EVENTS = frozenset({
    "CANDIDATE_CREATED", "VALIDATION_QUEUED", "VALIDATION_STARTED",
    "VALIDATION_PASSED", "VALIDATION_FAILED", "SHADOW_STARTED", "SHADOW_COMPLETED",
    "READY_FOR_REVIEW", "HUMAN_ACCEPT", "HUMAN_REJECT", "APPROVED_NOT_DEPLOYED",
    "DEPLOYED", "VERIFIED", "DISABLED", "REVOKED", "SUPERSEDED",
    "ROLLBACK_REQUESTED", "ROLLED_BACK", "UPSTREAM_INVALIDATED",
    "BLOCKED_UPSTREAM_INVALIDATED", "PROMOTION_BLOCKED_STALE",
    "ADVERSE_EVIDENCE", "AUTHORITY_ISSUED", "AUTHORITY_ACTIVATED",
})

_LOCK = threading.RLock()


class LifecycleLedgerError(RuntimeError):
    """The append-only lifecycle ledger is corrupt or a transition was refused."""


@dataclass(frozen=True)
class CandidateLifecycleEvent:
    """ONE append-only lifecycle event."""

    candidate_id: str
    event: str
    from_state: str
    to_state: str
    authority: str
    occurred_at: str
    actor: str = ""
    reason: str = ""
    evidence: str = ""
    evidence_frontier: str = ""
    baseline_id: str = ""
    baseline_config_hash: str = ""
    treatment_signature: str = ""
    decision_id: str = ""
    application_id: str = ""
    authority_id: str = ""
    supersedes: str = ""
    superseded_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LIFECYCLE_SCHEMA,
            "candidate_id": self.candidate_id,
            "event": self.event,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "authority": self.authority,
            "occurred_at": self.occurred_at,
            "actor": self.actor,
            "reason": self.reason,
            "evidence": self.evidence,
            "evidence_frontier": self.evidence_frontier,
            "baseline_id": self.baseline_id,
            "baseline_config_hash": self.baseline_config_hash,
            "treatment_signature": self.treatment_signature,
            "decision_id": self.decision_id,
            "application_id": self.application_id,
            "authority_id": self.authority_id,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CandidateLifecycleEvent":
        fields = {name for name in cls.__dataclass_fields__}
        row = {name: data.get(name, "") for name in fields}
        for name in ("candidate_id", "event", "authority", "occurred_at"):
            if not isinstance(row[name], str) or not row[name].strip():
                raise LifecycleLedgerError(f"LIFECYCLE_ROW_MISSING_{name.upper()}")
        if row["event"] not in REQUIRED_HISTORY_EVENTS:
            raise LifecycleLedgerError(f"LIFECYCLE_EVENT_UNKNOWN:{row['event']}")
        for name in ("from_state", "to_state"):
            if row[name] and row[name] not in CANONICAL_STATES:
                raise LifecycleLedgerError(f"LIFECYCLE_STATE_UNKNOWN:{row[name]}")
        return cls(**row)


class CandidateLifecycleLedger:
    """Append-only JSONL history of consequential candidate transitions."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else DEFAULT_LIFECYCLE_LEDGER

    def rows(self) -> list[CandidateLifecycleEvent]:
        if not self.path.exists():
            return []
        out: list[CandidateLifecycleEvent] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            out.append(CandidateLifecycleEvent.from_dict(json.loads(line)))
        return out

    def history(self, candidate_id: str) -> list[CandidateLifecycleEvent]:
        return [row for row in self.rows() if row.candidate_id == candidate_id]

    def latest_state(self, candidate_id: str) -> str:
        history = self.history(candidate_id)
        return history[-1].to_state if history else ""

    def has_event(self, candidate_id: str, event: str) -> bool:
        return any(row.event == event for row in self.history(candidate_id))

    def append(self, event: CandidateLifecycleEvent) -> CandidateLifecycleEvent:
        """Validate then append ONE durable row.  Never truncates."""
        if event.event not in REQUIRED_HISTORY_EVENTS:
            raise LifecycleLedgerError(f"LIFECYCLE_EVENT_UNKNOWN:{event.event}")
        for name, value in (("from_state", event.from_state), ("to_state", event.to_state)):
            if value and value not in CANONICAL_STATES:
                raise LifecycleLedgerError(f"LIFECYCLE_STATE_UNKNOWN:{value}")
        if not isinstance(event.candidate_id, str) or not event.candidate_id.strip():
            raise LifecycleLedgerError("LIFECYCLE_ROW_MISSING_CANDIDATE_ID")
        if event.from_state and event.from_state != event.to_state:
            # Recording a transition requires a permitted authority.
            if event.authority in TransitionAuthority.__dict__.values():
                validate_lifecycle_transition(event.from_state, event.to_state,
                                              event.authority)
        with _LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(event.to_dict(), sort_keys=True,
                                 separators=(",", ":"), default=str) + "\n"
            fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, payload.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
        return event


def record_lifecycle_event(
    *,
    candidate_id: str,
    event: str,
    from_state: str,
    to_state: str,
    authority: str,
    actor: str = "",
    reason: str = "",
    evidence: str = "",
    evidence_frontier: str = "",
    baseline_id: str = "",
    baseline_config_hash: str = "",
    treatment_signature: str = "",
    decision_id: str = "",
    application_id: str = "",
    authority_id: str = "",
    supersedes: str = "",
    superseded_by: str = "",
    ledger: CandidateLifecycleLedger | None = None,
) -> CandidateLifecycleEvent:
    """Append one governed lifecycle event (the ONLY writer of history)."""
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise LifecycleLedgerError("MISSING_CANDIDATE_ID")
    store = ledger or CandidateLifecycleLedger()
    record = CandidateLifecycleEvent(
        candidate_id=candidate_id,
        event=event,
        from_state=from_state,
        to_state=to_state,
        authority=authority,
        occurred_at=datetime.now(timezone.utc).isoformat(),
        actor=actor,
        reason=reason,
        evidence=evidence,
        evidence_frontier=evidence_frontier,
        baseline_id=baseline_id,
        baseline_config_hash=baseline_config_hash,
        treatment_signature=treatment_signature,
        decision_id=decision_id,
        application_id=application_id,
        authority_id=authority_id,
        supersedes=supersedes,
        superseded_by=superseded_by,
    )
    return store.append(record)


# ═══════════════════════════════════════════════════════════════════════════════
# CANONICAL DERIVATION — ONE state, explicit precedence, explicit disagreements
# ═══════════════════════════════════════════════════════════════════════════════

# Store classification (Step 13): A = authoritative source of truth,
# B = compatibility/projection layer, C = legacy/diagnostic.
STORE_CLASSES: dict[str, str] = {
    "PRODUCTION_AUTHORITY": "AUTHORITATIVE",
    "PRODUCTION_APPLICATION_LEDGER": "AUTHORITATIVE",
    "HUMAN_DECISION_STORE": "AUTHORITATIVE",
    "CANDIDATE_LIFECYCLE_LEDGER": "AUTHORITATIVE",
    "TRACK_A_CANDIDATE_REGISTRY": "COMPATIBILITY",
    "CONTINUOUS_OPTIMISATION_REGISTRY": "COMPATIBILITY",
    "SHADOW_CANDIDATE_GOVERNANCE": "COMPATIBILITY",
    "VALIDATION_QUEUE": "COMPATIBILITY",
    "RESEARCH_LAB_PROJECTION": "PROJECTION",
    "LEGACY_DECISION_LEDGER": "LEGACY",
}


@dataclass(frozen=True)
class CanonicalLifecycle:
    """The ONE authoritative view of a candidate's current lifecycle position."""

    candidate_id: str
    state: str
    source: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    disagreements: tuple[str, ...] = field(default_factory=tuple)
    store_states: dict[str, str] = field(default_factory=dict)
    legacy_states: dict[str, str] = field(default_factory=dict)
    live_authority: bool = False
    runtime_effective: bool = False
    required_action: str = ""
    review_notice: str = ""
    authority_id: str = ""
    decision_id: str = ""
    application_id: str = ""
    deployment_id: str = ""
    evidence_frontier: str = ""
    baseline_id: str = ""
    baseline_config_hash: str = ""
    superseded_by: str = ""
    supersedes: str = ""
    history: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "canonical_state": self.state,
            "source": self.source,
            "reasons": list(self.reasons),
            "disagreements": list(self.disagreements),
            "store_states": dict(self.store_states),
            "legacy_states": dict(self.legacy_states),
            "live_authority": self.live_authority,
            "runtime_effective": self.runtime_effective,
            "required_action": self.required_action,
            "review_notice": self.review_notice,
            "authority_id": self.authority_id,
            "decision_id": self.decision_id,
            "application_id": self.application_id,
            "deployment_id": self.deployment_id,
            "evidence_frontier": self.evidence_frontier,
            "baseline_id": self.baseline_id,
            "baseline_config_hash": self.baseline_config_hash,
            "superseded_by": self.superseded_by,
            "supersedes": self.supersedes,
            "history": [dict(row) for row in self.history],
            "store_classes": dict(STORE_CLASSES),
        }


def _read_jsonl(path: str | Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    target = Path(path)
    if not target.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _latest_application_state(rows: Sequence[Mapping[str, Any]]) -> tuple[str, dict[str, Any]]:
    if not rows:
        return "", {}
    ordered = sorted(rows, key=lambda r: str(r.get("occurred_at") or ""))
    last = ordered[-1]
    return str(last.get("state") or ""), dict(last)


def _latest_decision(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    completed = [r for r in rows if str(r.get("outcome") or "COMPLETED") == "COMPLETED"]
    if not completed:
        return {}
    return dict(sorted(completed, key=lambda r: str(r.get("timestamp") or ""))[-1])


def _latest_validation_state(rows: Sequence[Mapping[str, Any]]) -> str:
    if not rows:
        return ""
    last = sorted(rows, key=lambda r: str(r.get("queued_at") or ""))[-1]
    return str(last.get("status") or "")


def _read_stores(
    *,
    candidate_id: str,
    registry_dir: str | None,
    optimisation_registry_dir: str | None,
    application_path: str | Path | None,
    authority_path: str | Path | None,
    decisions_dir: str | None,
    validation_queue_path: str | Path | None,
    lifecycle_ledger_path: str | Path | None,
    shadow_summary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Read every candidate authority once, read-only, without side effects."""
    from research_engine.control_plane.production_authority import (
        ProductionAuthorityLedger,
    )

    store_states: dict[str, str] = {}
    legacy_states: dict[str, str] = {}
    disagreements: list[str] = []

    track_a = None
    try:
        from research_engine.v10.candidates.candidate_registry import CandidateRegistry
        track_a = (CandidateRegistry(storage_dir=registry_dir) if registry_dir
                   else CandidateRegistry()).get(candidate_id)
    except Exception:
        track_a = None
    if track_a is not None:
        legacy_states["TRACK_A_CANDIDATE_REGISTRY"] = track_a.status
        store_states["TRACK_A_CANDIDATE_REGISTRY"] = to_canonical(
            "TRACK_A_CANDIDATE_REGISTRY", track_a.status)

    continuous = None
    try:
        from research_engine.v10.optimisation.optimisation_registry import (
            OptimisationRegistry,
        )
        registry = OptimisationRegistry(optimisation_registry_dir)
        registry.load()
        continuous = registry.get_candidate(candidate_id)
    except Exception:
        continuous = None
    if continuous is not None:
        legacy_states["CONTINUOUS_OPTIMISATION_REGISTRY"] = continuous.status
        try:
            store_states["CONTINUOUS_OPTIMISATION_REGISTRY"] = to_canonical(
                "CONTINUOUS_OPTIMISATION_REGISTRY", continuous.status)
        except LifecycleMappingError:
            disagreements.append(
                "UNKNOWN_LEGACY_STATUS:CONTINUOUS_OPTIMISATION_REGISTRY:"
                + str(continuous.status))

    application_rows = [row for row in _read_jsonl(application_path)
                        if row.get("candidate_id") == candidate_id]
    app_state, app_row = _latest_application_state(application_rows)
    if app_state:
        legacy_states["PRODUCTION_APPLICATION_LEDGER"] = app_state
        mapped = to_canonical("PRODUCTION_APPLICATION_LEDGER", app_state)
        if mapped:
            store_states["PRODUCTION_APPLICATION_LEDGER"] = mapped

    decision: dict[str, Any] = {}
    if decisions_dir is not None:
        decision = _latest_decision([
            row for row in _read_jsonl(Path(decisions_dir) / "decisions.jsonl")
            if row.get("candidate_id") == candidate_id])
    if decision:
        legacy_states["HUMAN_DECISION_STORE"] = str(decision.get("decision") or "")
        store_states["HUMAN_DECISION_STORE"] = (
            CanonicalState.ACCEPTED if decision.get("decision") == "ACCEPT"
            else CanonicalState.REJECTED if decision.get("decision") == "REJECT" else "")

    if shadow_summary:
        shadow_state = str(shadow_summary.get("status") or "")
        if shadow_state:
            legacy_states["SHADOW_CANDIDATE_GOVERNANCE"] = shadow_state
            try:
                mapped = to_canonical("SHADOW_CANDIDATE_GOVERNANCE", shadow_state)
                if mapped:
                    store_states["SHADOW_CANDIDATE_GOVERNANCE"] = mapped
            except LifecycleMappingError:
                disagreements.append(
                    "UNKNOWN_LEGACY_STATUS:SHADOW_CANDIDATE_GOVERNANCE:" + shadow_state)

    validation_state = _latest_validation_state(
        [row for row in _read_jsonl(validation_queue_path)
         if row.get("candidate_id") == candidate_id]
        if validation_queue_path is not None else [])
    if validation_state:
        legacy_states["VALIDATION_QUEUE"] = validation_state
        mapped = to_canonical("VALIDATION_QUEUE", validation_state)
        if mapped:
            store_states["VALIDATION_QUEUE"] = mapped

    authority = None
    try:
        rows = ProductionAuthorityLedger(authority_path).effective_for_candidate(candidate_id)
        authority = rows[-1] if rows else None
    except Exception as exc:
        disagreements.append(f"AUTHORITY_LEDGER_INVALID:{type(exc).__name__}")
    if authority is not None:
        legacy_states["PRODUCTION_AUTHORITY"] = authority.status
        store_states["PRODUCTION_AUTHORITY"] = to_canonical(
            "PRODUCTION_AUTHORITY", authority.status)

    history = CandidateLifecycleLedger(lifecycle_ledger_path).history(candidate_id)
    if history:
        store_states["CANDIDATE_LIFECYCLE_LEDGER"] = history[-1].to_state

    return {
        "store_states": {k: v for k, v in store_states.items() if v},
        "legacy_states": legacy_states,
        "disagreements": disagreements,
        "track_a": track_a,
        "continuous": continuous,
        "application_row": app_row,
        "decision": decision,
        "authority": authority,
        "history": tuple(row.to_dict() for row in history),
    }


def derive_canonical_lifecycle(
    *,
    candidate_id: str,
    registry_dir: str | None = None,
    optimisation_registry_dir: str | None = None,
    application_path: str | Path | None = None,
    authority_path: str | Path | None = None,
    decisions_dir: str | None = None,
    validation_queue_path: str | Path | None = None,
    lifecycle_ledger_path: str | Path | None = None,
    shadow_summary: Mapping[str, Any] | None = None,
    upstream_invalidated: bool = False,
    live_upstream_invalidated: bool = False,
) -> CanonicalLifecycle:
    """Derive ONE canonical lifecycle state from every candidate authority.

    Precedence (highest first):
        1. upstream scientific invalidation
        2. governed production authority ledger
        3. governed application ledger
        4. human decision store
        5. shadow candidate governance readiness
        6. validation queue
        7. continuous optimisation registry
        8. Track-A candidate registry
        9. PROPOSED (nothing recorded)

    Disagreements between stores are surfaced explicitly, never silently
    resolved in favour of the most convenient value.
    """
    read = _read_stores(
        candidate_id=candidate_id, registry_dir=registry_dir,
        optimisation_registry_dir=optimisation_registry_dir,
        application_path=application_path, authority_path=authority_path,
        decisions_dir=decisions_dir, validation_queue_path=validation_queue_path,
        lifecycle_ledger_path=lifecycle_ledger_path, shadow_summary=shadow_summary,
    )
    states: dict[str, str] = read["store_states"]
    disagreements: list[str] = list(read["disagreements"])
    reasons: list[str] = []
    authority = read["authority"]
    application_row: Mapping[str, Any] = read["application_row"]
    decision: Mapping[str, Any] = read["decision"]
    runtime_effective = bool(
        authority is not None
        and authority.status == "ACTIVE"
        and not authority.superseded_by)

    # ── 1. upstream scientific invalidation ─────────────────────────────────
    if upstream_invalidated or live_upstream_invalidated:
        reasons.append("UPSTREAM_SCIENTIFIC_BASIS_INVALIDATED")
        if runtime_effective:
            reasons.append("LIVE_AUTHORITY_REQUIRES_REVOKE_OR_ROLLBACK")
        return _finish(
            candidate_id=candidate_id, state=CanonicalState.INVALIDATED_UPSTREAM,
            source="UPSTREAM_INVALIDATION", reasons=reasons,
            disagreements=disagreements, read=read,
            runtime_effective=runtime_effective, decision=decision,
            authority=authority, application_row=application_row,
            required_action=("REVOKE_OR_ROLLBACK" if runtime_effective
                             else "BLOCK_CANDIDATE"),
        )

    # ── 2. governed production authority ledger ─────────────────────────────
    if authority is not None:
        mapped = states.get("PRODUCTION_AUTHORITY", "")
        if mapped in {
            CanonicalState.REVOKED, CanonicalState.SUPERSEDED,
            CanonicalState.ROLLED_BACK, CanonicalState.DISABLED,
        }:
            reasons.append(f"GOVERNED_AUTHORITY_{authority.status}")
            return _finish(
                candidate_id=candidate_id, state=mapped,
                source="PRODUCTION_AUTHORITY", reasons=reasons,
                disagreements=disagreements, read=read,
                runtime_effective=runtime_effective, decision=decision,
                authority=authority, application_row=application_row,
                required_action="NONE",
            )
        app_mapped = states.get("PRODUCTION_APPLICATION_LEDGER", "")
        if authority.status == "ACTIVE":
            reasons.append("GOVERNED_AUTHORITY_ACTIVE")
            if app_mapped != CanonicalState.VERIFIED:
                disagreements.append("ACTIVE_AUTHORITY_WITHOUT_VERIFIED_APPLICATION")
            return _finish(
                candidate_id=candidate_id,
                state=(CanonicalState.VERIFIED if app_mapped == CanonicalState.VERIFIED
                       else CanonicalState.DEPLOYED),
                source="PRODUCTION_AUTHORITY", reasons=reasons,
                disagreements=disagreements, read=read,
                runtime_effective=runtime_effective, decision=decision,
                authority=authority, application_row=application_row,
            )
        if authority.status == "ISSUED":
            reasons.append("GOVERNED_AUTHORITY_ISSUED_NOT_DEPLOYED")
            return _finish(
                candidate_id=candidate_id, state=CanonicalState.APPROVED_NOT_DEPLOYED,
                source="PRODUCTION_AUTHORITY", reasons=reasons,
                disagreements=disagreements, read=read,
                runtime_effective=runtime_effective, decision=decision,
                authority=authority, application_row=application_row,
            )

    # ── 3. governed application ledger ──────────────────────────────────────
    app_mapped = states.get("PRODUCTION_APPLICATION_LEDGER", "")
    if app_mapped:
        reasons.append(f"APPLICATION_LEDGER_{app_mapped}")
        if app_mapped in {CanonicalState.DEPLOYED, CanonicalState.VERIFIED}:
            disagreements.append("APPLICATION_WITHOUT_GOVERNED_AUTHORITY")
        return _finish(
            candidate_id=candidate_id, state=app_mapped, source="APPLICATION_LEDGER",
            reasons=reasons, disagreements=disagreements, read=read,
            runtime_effective=runtime_effective, decision=decision,
            authority=authority, application_row=application_row,
        )

    # ── 4. human decision store ─────────────────────────────────────────────
    if decision:
        verdict = str(decision.get("decision") or "")
        state = (CanonicalState.ACCEPTED if verdict == "ACCEPT"
                 else CanonicalState.REJECTED if verdict == "REJECT" else "")
        if state:
            reasons.append(f"HUMAN_DECISION_{verdict}")
            if verdict == "ACCEPT" and not application_row:
                reasons.append("ACCEPTED_WITHOUT_APPROVAL_RECORD")
            return _finish(
                candidate_id=candidate_id, state=state, source="HUMAN_DECISION_STORE",
                reasons=reasons, disagreements=disagreements, read=read,
                runtime_effective=runtime_effective, decision=decision,
                authority=authority, application_row=application_row,
            )

    # ── 5. shadow candidate governance readiness ────────────────────────────
    shadow_mapped = states.get("SHADOW_CANDIDATE_GOVERNANCE", "")
    if shadow_mapped:
        reasons.append(f"SHADOW_GOVERNANCE_{shadow_mapped}")
        return _finish(
            candidate_id=candidate_id, state=shadow_mapped,
            source="SHADOW_CANDIDATE_GOVERNANCE", reasons=reasons,
            disagreements=disagreements, read=read,
            runtime_effective=runtime_effective, decision=decision,
            authority=authority, application_row=application_row,
        )

    # ── 6. validation queue ─────────────────────────────────────────────────
    queue_mapped = states.get("VALIDATION_QUEUE", "")
    if queue_mapped:
        reasons.append(f"VALIDATION_QUEUE_{queue_mapped}")
        return _finish(
            candidate_id=candidate_id, state=queue_mapped, source="VALIDATION_QUEUE",
            reasons=reasons, disagreements=disagreements, read=read,
            runtime_effective=runtime_effective, decision=decision,
            authority=authority, application_row=application_row,
        )

    # ── 7/8. candidate registries (compatibility layers) ────────────────────
    track = states.get("TRACK_A_CANDIDATE_REGISTRY", "")
    cont = states.get("CONTINUOUS_OPTIMISATION_REGISTRY", "")
    if track and cont and track != cont:
        disagreements.append(f"REGISTRY_STATUS_DISAGREEMENT:{track}:{cont}")
    chosen = track or cont
    if chosen:
        reasons.append(f"CANDIDATE_REGISTRY_{chosen}")
        return _finish(
            candidate_id=candidate_id, state=chosen, source="CANDIDATE_REGISTRY",
            reasons=reasons, disagreements=disagreements, read=read,
            runtime_effective=runtime_effective, decision=decision,
            authority=authority, application_row=application_row,
        )

    # ── 9. nothing recorded ─────────────────────────────────────────────────
    if read["track_a"] is None and read["continuous"] is None and not authority:
        reasons.append("NO_RECORDED_CANDIDATE_STATE")
        return _finish(
            candidate_id=candidate_id, state=CanonicalState.PROPOSED, source="DEFAULT",
            reasons=reasons, disagreements=disagreements, read=read,
            runtime_effective=runtime_effective, decision=decision,
            authority=authority, application_row=application_row,
        )
    reasons.append("FALLBACK_PROPOSED")
    return _finish(
        candidate_id=candidate_id, state=CanonicalState.PROPOSED, source="FALLBACK",
        reasons=reasons, disagreements=disagreements, read=read,
        runtime_effective=runtime_effective, decision=decision,
        authority=authority, application_row=application_row,
    )


def _finish(
    *,
    candidate_id: str,
    state: str,
    source: str,
    reasons: list[str],
    disagreements: list[str],
    read: Mapping[str, Any],
    runtime_effective: bool,
    decision: Mapping[str, Any],
    authority: Any,
    application_row: Mapping[str, Any],
    required_action: str = "",
) -> CanonicalLifecycle:
    """Assemble the immutable canonical view (single exit point)."""
    track_a = read["track_a"]
    change = (getattr(track_a, "change_definition", {}) or {}) if track_a else {}
    return CanonicalLifecycle(
        candidate_id=candidate_id,
        state=state,
        source=source,
        reasons=tuple(reasons),
        disagreements=tuple(sorted(set(disagreements))),
        store_states=dict(read["store_states"]),
        legacy_states=dict(read["legacy_states"]),
        live_authority=runtime_effective,
        runtime_effective=runtime_effective,
        required_action=required_action,
        review_notice=(REVIEW_STATE_NOTICE
                       if state == CanonicalState.READY_FOR_PROMOTION_REVIEW else ""),
        authority_id=(getattr(authority, "authority_id", "") or ""),
        decision_id=(str(decision.get("decision_id")
                         or decision.get("recommendation_id") or "")
                     if decision else ""),
        application_id=(str(application_row.get("application_id") or "")
                        if application_row else ""),
        deployment_id=(getattr(authority, "deployment_id", "") or ""),
        evidence_frontier=(getattr(authority, "evidence_frontier", "") or ""),
        baseline_id=(getattr(authority, "baseline_id", "") or
                     getattr(track_a, "baseline_id", "") or ""),
        baseline_config_hash=(getattr(authority, "baseline_config_hash", "") or
                              str(change.get("baseline_config_hash", ""))),
        superseded_by=(getattr(authority, "superseded_by", "") or ""),
        supersedes=(getattr(authority, "supersedes", "") or ""),
        history=tuple(read["history"]),
    )


__all__ = [
    "CANONICAL_STATES", "CanonicalLifecycle", "CanonicalState",
    "CandidateLifecycleEvent", "CandidateLifecycleLedger", "LEGACY_STATE_MAP",
    "LIFECYCLE_SCHEMA", "LIVE_CAPABLE_STATES", "LifecycleLedgerError",
    "LifecycleMappingError", "PRODUCTION_AUTHORITY_MAP", "REQUIRED_HISTORY_EVENTS",
    "REVIEW_STATE_NOTICE", "STORE_CLASSES", "TERMINAL_STATES",
    "TRANSITION_AUTHORITY", "TransitionAuthority", "authorities_for",
    "derive_canonical_lifecycle", "is_authorized_transition", "record_lifecycle_event",
    "to_canonical", "validate_lifecycle_transition",
]
