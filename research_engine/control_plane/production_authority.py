"""Repair Block 3 — THE single governed production authority for candidates.

Audit 3 established that candidate metadata (``live_approved``), a syntactically
valid policy file, or any individual registry row could not independently prove
that a candidate is allowed to affect live trading.  This module closes that
gap with ONE append-only governed authority ledger.

Design rules (deliberate, fail-closed):

* The ledger is APPEND-ONLY.  The effective state of an authority is the fold
  (last row) over its events; history can never be rewritten in place.
* Authority is NEVER derived from candidate metadata, a policy file's
  existence, or a registry status.  It exists only because the governed
  promotion path wrote it: human ACCEPT -> approval record
  (APPROVED_NOT_DEPLOYED) -> ``ApplicationService.execute()`` -> VERIFIED ->
  ``activate_authority``.
* ``ISSUED`` is *not* runtime authority.  Only ``ACTIVE`` is runtime-effective.
* ``REVOKED`` / ``SUPERSEDED`` / ``ROLLED_BACK`` are terminal; ``DISABLED``
  temporarily removes runtime authority while retaining deployment history.
* ``verify_runtime_authority`` is the runtime consumption boundary primitive.
  Anything missing/ambiguous/unknown fails closed.

This module never trades, never calls a broker, never writes a policy file and
never mutates a candidate registry.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

DEFAULT_AUTHORITY_PATH = Path("data/research/governance/production_authority.jsonl")

AUTHORITY_SCHEMA = "production_authority_v1"


class AuthorityStatus:
    """Lifecycle states of ONE governed production authority."""

    ISSUED = "ISSUED"                # governed approval exists; NOT runtime-effective
    ACTIVE = "ACTIVE"                # deployed + verified; runtime-effective
    DISABLED = "DISABLED"            # temporarily not runtime-effective
    REVOKED = "REVOKED"              # permanently withdrawn
    SUPERSEDED = "SUPERSEDED"        # replaced by a newer governed candidate
    ROLLED_BACK = "ROLLED_BACK"      # deployment reverted to predecessor


ALL_STATUSES = frozenset({
    AuthorityStatus.ISSUED, AuthorityStatus.ACTIVE, AuthorityStatus.DISABLED,
    AuthorityStatus.REVOKED, AuthorityStatus.SUPERSEDED, AuthorityStatus.ROLLED_BACK,
})

# The ONLY status that permits a candidate to affect live trading.
RUNTIME_EFFECTIVE_STATUSES = frozenset({AuthorityStatus.ACTIVE})

# Explicit transition authority.  Anything not listed is refused (fail closed).
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    AuthorityStatus.ISSUED: frozenset({
        AuthorityStatus.ACTIVE, AuthorityStatus.DISABLED, AuthorityStatus.REVOKED,
        AuthorityStatus.SUPERSEDED,
    }),
    AuthorityStatus.ACTIVE: frozenset({
        AuthorityStatus.DISABLED, AuthorityStatus.REVOKED,
        AuthorityStatus.SUPERSEDED, AuthorityStatus.ROLLED_BACK,
    }),
    AuthorityStatus.DISABLED: frozenset({
        AuthorityStatus.ACTIVE, AuthorityStatus.REVOKED,
        AuthorityStatus.SUPERSEDED, AuthorityStatus.ROLLED_BACK,
    }),
    AuthorityStatus.REVOKED: frozenset(),
    AuthorityStatus.SUPERSEDED: frozenset(),
    AuthorityStatus.ROLLED_BACK: frozenset(),
}

_EVENTS = frozenset({
    "ISSUED", "ACTIVATED", "DISABLED", "REVOKED", "SUPERSEDED", "ROLLED_BACK",
})

_LOCK = threading.RLock()


class ProductionAuthorityError(RuntimeError):
    """The governed production-authority ledger is corrupt or a transition is illegal."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def authority_id_for(application_id: str) -> str:
    """Deterministic authority identity — one authority per governed application."""
    if not isinstance(application_id, str) or not application_id.strip():
        raise ProductionAuthorityError("MISSING_APPLICATION_ID")
    return "AUTH-" + hashlib.sha256(application_id.encode("utf-8")).hexdigest()[:24].upper()


def treatment_signature(treatment_id: str, treatment_spec: str | None) -> str:
    """Frozen candidate treatment identity (never re-derived from mutable docs)."""
    if not isinstance(treatment_id, str) or not treatment_id.strip():
        raise ProductionAuthorityError("MISSING_TREATMENT_ID")
    return digest({"treatment_id": treatment_id, "treatment_spec": treatment_spec or ""})


def policy_hash(state: Mapping[str, Any]) -> str:
    """Hash of the exact runtime-effective policy state."""
    return digest(dict(state))


@dataclass(frozen=True)
class ProductionAuthorityRecord:
    """ONE append-only event row of the governed production authority ledger."""

    authority_id: str
    candidate_id: str
    decision_id: str
    application_id: str
    treatment_id: str
    treatment_signature: str
    baseline_id: str
    baseline_config_hash: str
    policy_hash: str
    status: str
    event: str
    occurred_at: str
    deployment_id: str = ""
    verification_status: str = ""
    actor: str = ""
    reason: str = ""
    supersedes: str = ""
    superseded_by: str = ""
    deployment_reference: str = ""
    evidence_frontier: str = ""
    treatment_scope: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": AUTHORITY_SCHEMA,
            "authority_id": self.authority_id,
            "candidate_id": self.candidate_id,
            "decision_id": self.decision_id,
            "application_id": self.application_id,
            "treatment_id": self.treatment_id,
            "treatment_signature": self.treatment_signature,
            "baseline_id": self.baseline_id,
            "baseline_config_hash": self.baseline_config_hash,
            "policy_hash": self.policy_hash,
            "deployment_id": self.deployment_id,
            "verification_status": self.verification_status,
            "status": self.status,
            "event": self.event,
            "occurred_at": self.occurred_at,
            "actor": self.actor,
            "reason": self.reason,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "deployment_reference": self.deployment_reference,
            "evidence_frontier": self.evidence_frontier,
            "treatment_scope": self.treatment_scope,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProductionAuthorityRecord":
        fields = {name for name in cls.__dataclass_fields__}
        row = {name: data.get(name, "") for name in fields}
        for name in ("authority_id", "candidate_id", "application_id", "status", "event"):
            if not isinstance(row[name], str) or not row[name].strip():
                raise ProductionAuthorityError(f"AUTHORITY_ROW_MISSING_{name.upper()}")
        if row["status"] not in ALL_STATUSES:
            raise ProductionAuthorityError(f"AUTHORITY_STATUS_INVALID:{row['status']}")
        if row["event"] not in _EVENTS:
            raise ProductionAuthorityError(f"AUTHORITY_EVENT_INVALID:{row['event']}")
        return cls(**row)


def validate_authority_transition(current: str, new: str) -> tuple[bool, str]:
    """Pure transition check used by the ledger and by callers/tests."""
    if new not in ALL_STATUSES:
        return False, f"AUTHORITY_STATUS_INVALID:{new}"
    if current not in ALL_STATUSES:
        return False, f"AUTHORITY_STATUS_INVALID:{current}"
    if current == new:
        return True, ""
    if new not in _ALLOWED_TRANSITIONS[current]:
        return False, f"AUTHORITY_TRANSITION_REFUSED:{current}->{new}"
    return True, ""


def canonical_scope(scope: Mapping[str, Any] | None) -> str:
    """Canonical, structural scope identity (never free text)."""
    if scope is None:
        return ""
    if not isinstance(scope, Mapping) or set(scope) != {"symbols", "patterns"}:
        raise ProductionAuthorityError("MALFORMED_SCOPE")

    def _norm(value: Any) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, (list, tuple)) or not value:
            raise ProductionAuthorityError("MALFORMED_SCOPE")
        return sorted({str(item) for item in value})

    return canonical({"symbols": _norm(scope.get("symbols")),
                      "patterns": _norm(scope.get("patterns"))})


def row_scope(record: ProductionAuthorityRecord) -> str:
    """Canonical scope identity carried by a governed authority record."""
    return record.treatment_scope


class ProductionAuthorityLedger:
    """Append-only JSONL authority ledger with a folded effective-state view."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else DEFAULT_AUTHORITY_PATH

    # ─── read ────────────────────────────────────────────────────────────────
    def rows(self) -> list[ProductionAuthorityRecord]:
        if not self.path.exists():
            return []
        out: list[ProductionAuthorityRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            # Corruption is NOT silently skipped: unknown authority state must
            # never be interpreted as "no authority exists but maybe fine".
            out.append(ProductionAuthorityRecord.from_dict(json.loads(line)))
        return out

    def events_for(self, authority_id: str) -> list[ProductionAuthorityRecord]:
        return [row for row in self.rows() if row.authority_id == authority_id]

    def get(self, authority_id: str) -> ProductionAuthorityRecord | None:
        """Effective (folded) authority record, or None when unknown."""
        events = self.events_for(authority_id)
        return events[-1] if events else None

    def get_for_application(self, application_id: str) -> ProductionAuthorityRecord | None:
        if not isinstance(application_id, str) or not application_id.strip():
            return None
        return self.get(authority_id_for(application_id))

    def effective(self) -> list[ProductionAuthorityRecord]:
        """Latest row per authority id, in first-issue order."""
        latest: dict[str, ProductionAuthorityRecord] = {}
        order: list[str] = []
        for row in self.rows():
            if row.authority_id not in latest:
                order.append(row.authority_id)
            latest[row.authority_id] = row
        return [latest[key] for key in order]

    def effective_for_candidate(self, candidate_id: str) -> list[ProductionAuthorityRecord]:
        return [row for row in self.effective() if row.candidate_id == candidate_id]

    def runtime_effective(self) -> list[ProductionAuthorityRecord]:
        return [row for row in self.effective()
                if row.status in RUNTIME_EFFECTIVE_STATUSES]

    def runtime_effective_for_scope(self, scope: Mapping[str, Any] | None) -> list[ProductionAuthorityRecord]:
        """Every runtime-effective authority covering the SAME governed scope."""
        wanted = canonical_scope(scope)
        if not wanted:
            return []
        return [row for row in self.runtime_effective() if row.treatment_scope == wanted]

    # ─── write (append only) ─────────────────────────────────────────────────
    def append(self, record: ProductionAuthorityRecord) -> ProductionAuthorityRecord:
        """Validate then append ONE durable row.  Never truncates."""
        with _LOCK:
            current = self.get(record.authority_id)
            if current is None:
                if record.event != "ISSUED" or record.status != AuthorityStatus.ISSUED:
                    raise ProductionAuthorityError("AUTHORITY_MUST_BEGIN_WITH_ISSUED")
            else:
                ok, reason = validate_authority_transition(current.status, record.status)
                if not ok:
                    raise ProductionAuthorityError(reason)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = canonical(record.to_dict()) + "\n"
            fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, payload.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            return record


# ═══════════════════════════════════════════════════════════════════════════════
# GOVERNED TRANSITIONS
# ═══════════════════════════════════════════════════════════════════════════════

_REQUIRED_ISSUE_FIELDS = (
    "candidate_id", "decision_id", "application_id", "treatment_id",
    "treatment_signature", "baseline_id", "baseline_config_hash", "policy_hash",
)


def issue_authority(
    *,
    candidate_id: str,
    decision_id: str,
    application_id: str,
    treatment_id: str,
    treatment_spec: str | None,
    baseline_id: str,
    baseline_config_hash: str,
    policy_state: Mapping[str, Any],
    actor: str,
    reason: str = "",
    evidence_frontier: str = "",
    scope: Mapping[str, Any] | None = None,
    ledger: ProductionAuthorityLedger | None = None,
) -> ProductionAuthorityRecord:
    """Create the governed approval record (``APPROVED_NOT_DEPLOYED``).

    This is NOT runtime authority: status stays ISSUED until an explicit
    governed deployment verifies readback.
    """
    store = ledger or ProductionAuthorityLedger()
    payload = {
        "candidate_id": candidate_id,
        "decision_id": decision_id,
        "application_id": application_id,
        "treatment_id": treatment_id,
        "treatment_signature": treatment_signature(treatment_id, treatment_spec),
        "baseline_id": baseline_id,
        "baseline_config_hash": baseline_config_hash,
        "policy_hash": policy_hash(policy_state),
    }
    for name in _REQUIRED_ISSUE_FIELDS:
        value = payload[name]
        if not isinstance(value, str) or not value.strip():
            raise ProductionAuthorityError(f"MISSING_{name.upper()}")
    if not isinstance(actor, str) or not actor.strip():
        raise ProductionAuthorityError("MISSING_ACTOR")
    record = ProductionAuthorityRecord(
        authority_id=authority_id_for(application_id),
        status=AuthorityStatus.ISSUED,
        event="ISSUED",
        occurred_at=_now(),
        actor=actor,
        reason=reason,
        evidence_frontier=evidence_frontier,
        treatment_scope=canonical_scope(scope),
        **payload,
    )
    return store.append(record)


def activate_authority(
    *,
    application_id: str,
    deployment_id: str,
    verification_status: str,
    policy_state: Mapping[str, Any],
    deployment_reference: str = "",
    actor: str,
    reason: str = "",
    ledger: ProductionAuthorityLedger | None = None,
) -> ProductionAuthorityRecord:
    """Issue runtime authority after a verified governed deployment (fail closed)."""
    store = ledger or ProductionAuthorityLedger()
    current = store.get_for_application(application_id)
    if current is None:
        raise ProductionAuthorityError("NO_GOVERNED_AUTHORITY_TO_ACTIVATE")
    if not isinstance(deployment_id, str) or not deployment_id.strip():
        raise ProductionAuthorityError("MISSING_DEPLOYMENT_ID")
    if str(verification_status).upper() != "VERIFIED":
        raise ProductionAuthorityError("DEPLOYMENT_NOT_VERIFIED")
    actual_hash = policy_hash(policy_state)
    if actual_hash != current.policy_hash:
        raise ProductionAuthorityError("ACTIVATION_POLICY_HASH_MISMATCH")
    record = replace(
        current,
        status=AuthorityStatus.ACTIVE,
        event="ACTIVATED",
        occurred_at=_now(),
        actor=actor,
        reason=reason,
        deployment_id=deployment_id,
        verification_status="VERIFIED",
        deployment_reference=deployment_reference or current.deployment_reference,
        policy_hash=actual_hash,
    )
    return store.append(record)


def _terminal(
    *,
    application_id: str,
    status: str,
    event: str,
    actor: str,
    reason: str,
    supersedes: str = "",
    superseded_by: str = "",
    ledger: ProductionAuthorityLedger | None = None,
) -> ProductionAuthorityRecord:
    store = ledger or ProductionAuthorityLedger()
    current = store.get_for_application(application_id)
    if current is None:
        raise ProductionAuthorityError("NO_GOVERNED_AUTHORITY")
    if not isinstance(actor, str) or not actor.strip():
        raise ProductionAuthorityError("MISSING_ACTOR")
    record = replace(
        current,
        status=status,
        event=event,
        occurred_at=_now(),
        actor=actor,
        reason=reason,
        supersedes=supersedes or current.supersedes,
        superseded_by=superseded_by or current.superseded_by,
    )
    return store.append(record)


def disable_authority(*, application_id: str, actor: str, reason: str,
                      ledger: ProductionAuthorityLedger | None = None) -> ProductionAuthorityRecord:
    """Temporarily remove runtime authority; deployment history is retained."""
    return _terminal(application_id=application_id, status=AuthorityStatus.DISABLED,
                     event="DISABLED", actor=actor, reason=reason, ledger=ledger)


def revoke_authority(*, application_id: str, actor: str, reason: str,
                     ledger: ProductionAuthorityLedger | None = None) -> ProductionAuthorityRecord:
    """Permanently withdraw production authority."""
    return _terminal(application_id=application_id, status=AuthorityStatus.REVOKED,
                     event="REVOKED", actor=actor, reason=reason, ledger=ledger)


def mark_rolled_back(*, application_id: str, actor: str, reason: str,
                     ledger: ProductionAuthorityLedger | None = None) -> ProductionAuthorityRecord:
    """Deployment reverted to its predecessor; authority is no longer effective."""
    return _terminal(application_id=application_id, status=AuthorityStatus.ROLLED_BACK,
                     event="ROLLED_BACK", actor=actor, reason=reason, ledger=ledger)


def supersede_authority(
    *,
    predecessor_application_id: str,
    successor_application_id: str,
    actor: str,
    reason: str,
    ledger: ProductionAuthorityLedger | None = None,
) -> ProductionAuthorityRecord:
    """Explicit governed supersession.  No free-text or semantic guessing."""
    store = ledger or ProductionAuthorityLedger()
    if not isinstance(successor_application_id, str) or not successor_application_id.strip():
        raise ProductionAuthorityError("MISSING_SUCCESSOR")
    if successor_application_id == predecessor_application_id:
        raise ProductionAuthorityError("SUPERSESSION_REQUIRES_DISTINCT_SUCCESSOR")
    return _terminal(
        application_id=predecessor_application_id,
        status=AuthorityStatus.SUPERSEDED,
        event="SUPERSEDED",
        actor=actor,
        reason=reason,
        superseded_by=authority_id_for(successor_application_id),
        ledger=store,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# RUNTIME CONSUMPTION BOUNDARY
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class AuthorityVerification:
    """Result of verifying production authority at the runtime boundary."""

    ok: bool
    authority_id: str = ""
    status: str = ""
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "authority_id": self.authority_id,
                "status": self.status, "reasons": list(self.reasons)}


def _fail(*reasons: str) -> AuthorityVerification:
    return AuthorityVerification(ok=False, reasons=tuple(reasons))


def verify_runtime_authority(
    *,
    policy_state: Mapping[str, Any],
    ledger: ProductionAuthorityLedger | None = None,
    require_baseline: bool = False,
) -> AuthorityVerification:
    """Verify that a policy state may affect live trading.

    Returns ok=False with explicit reasons for ANY missing, ambiguous, unknown,
    stale, revoked, disabled, superseded or rolled-back authority.  Callers must
    fall back to NORMAL (no optimisation policy) when ok is False.
    """
    from core.optimisation_policy import (  # local import: avoid an import cycle
        DIRECTION_INVERSION_KIND, NORMAL_KIND,
    )

    if not isinstance(policy_state, Mapping):
        return _fail("MALFORMED_POLICY_STATE")
    kind = policy_state.get("kind")
    if kind == NORMAL_KIND:
        return _fail("POLICY_IS_NORMAL")
    if kind != DIRECTION_INVERSION_KIND:
        return _fail(f"UNSUPPORTED_POLICY_KIND:{kind}")

    application_id = policy_state.get("application_id")
    if not isinstance(application_id, str) or not application_id.strip():
        return _fail("MISSING_APPLICATION_IDENTITY")

    try:
        store = ledger or ProductionAuthorityLedger()
        record = store.get_for_application(application_id)
    except ProductionAuthorityError as exc:
        return _fail(f"AUTHORITY_LEDGER_INVALID:{exc}")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _fail(f"AUTHORITY_LEDGER_UNREADABLE:{type(exc).__name__}")

    if record is None:
        return _fail("NO_GOVERNED_AUTHORITY")

    reasons: list[str] = []
    if record.status not in RUNTIME_EFFECTIVE_STATUSES:
        reasons.append(f"AUTHORITY_NOT_RUNTIME_EFFECTIVE:{record.status}")
    if record.superseded_by:
        reasons.append("AUTHORITY_SUPERSEDED")
    if not record.decision_id.strip():
        reasons.append("MISSING_HUMAN_APPROVAL")
    if str(record.verification_status).upper() != "VERIFIED":
        reasons.append("DEPLOYMENT_NOT_VERIFIED")
    if not record.deployment_id.strip():
        reasons.append("MISSING_DEPLOYMENT_ID")

    candidate_id = policy_state.get("candidate_id")
    if isinstance(candidate_id, str) and candidate_id.strip():
        if candidate_id != record.candidate_id:
            reasons.append("CANDIDATE_IDENTITY_MISMATCH")
    else:
        # The runtime policy must carry the candidate identity it claims.
        reasons.append("POLICY_MISSING_CANDIDATE_IDENTITY")

    treatment_id = policy_state.get("treatment_id")
    if not isinstance(treatment_id, str) or treatment_id != record.treatment_id:
        reasons.append("TREATMENT_IDENTITY_MISMATCH")

    spec = policy_state.get("treatment_spec")
    if treatment_signature(record.treatment_id, spec) != record.treatment_signature:
        reasons.append("TREATMENT_SIGNATURE_MISMATCH")

    if policy_hash(policy_state) != record.policy_hash:
        reasons.append("POLICY_HASH_MISMATCH")

    reasons.extend(_baseline_reasons(record, require_baseline=require_baseline))

    if reasons:
        return AuthorityVerification(ok=False, authority_id=record.authority_id,
                                     status=record.status, reasons=tuple(reasons))
    return AuthorityVerification(ok=True, authority_id=record.authority_id,
                                 status=record.status, reasons=())


def _baseline_reasons(record: ProductionAuthorityRecord,
                      *, require_baseline: bool) -> list[str]:
    """Baseline/config freshness at the runtime boundary.

    The check is against the CURRENT production config identity (the same
    primitive the baseline authority uses), NOT against the active-baseline
    pointer id: a governed deployment legitimately ADVANCES the active baseline
    to the snapshot it just verified, so comparing pointer ids would make every
    freshly deployed authority look stale.

    An unresolvable config identity is reported as unverifiable — the governed
    authority ledger remains the primary gate — and becomes a hard failure when
    ``require_baseline`` is set.  A resolvable identity that DISAGREES with the
    governed approval is always a hard failure.
    """
    if not record.baseline_config_hash:
        return ["MISSING_AUTHORITY_BASELINE_PROVENANCE"] if require_baseline else []
    try:
        from core.research_events import compute_config_hash
    except ImportError:  # pragma: no cover - module is always present
        return ["CONFIG_IDENTITY_UNAVAILABLE"] if require_baseline else []
    try:
        current = compute_config_hash()
    except Exception:
        return ["CONFIG_IDENTITY_UNAVAILABLE"] if require_baseline else []
    if not isinstance(current, str) or not current or current == "UNKNOWN":
        return ["CONFIG_IDENTITY_UNAVAILABLE"] if require_baseline else []
    if current != record.baseline_config_hash:
        return ["CONFIG_DRIFT"]
    return []


def effective_authority_for_candidate(
    candidate_id: str, *, ledger: ProductionAuthorityLedger | None = None,
) -> ProductionAuthorityRecord | None:
    """Latest authority row for a candidate, if any."""
    store = ledger or ProductionAuthorityLedger()
    rows = store.effective_for_candidate(candidate_id)
    return rows[-1] if rows else None


def is_runtime_effective(candidate_id: str, *,
                         ledger: ProductionAuthorityLedger | None = None) -> bool:
    record = effective_authority_for_candidate(candidate_id, ledger=ledger)
    return bool(record and record.status in RUNTIME_EFFECTIVE_STATUSES)


def authority_history(candidate_id: str, *,
                      ledger: ProductionAuthorityLedger | None = None) -> list[dict[str, Any]]:
    store = ledger or ProductionAuthorityLedger()
    return [row.to_dict() for row in store.rows() if row.candidate_id == candidate_id]


__all__ = [
    "ALL_STATUSES", "AUTHORITY_SCHEMA", "AuthorityStatus", "AuthorityVerification",
    "DEFAULT_AUTHORITY_PATH", "ProductionAuthorityError", "ProductionAuthorityLedger",
    "ProductionAuthorityRecord", "RUNTIME_EFFECTIVE_STATUSES", "activate_authority",
    "authority_history", "authority_id_for", "canonical_scope", "disable_authority",
    "effective_authority_for_candidate", "is_runtime_effective", "issue_authority",
    "mark_rolled_back", "policy_hash", "revoke_authority", "row_scope",
    "supersede_authority", "treatment_signature", "validate_authority_transition",
    "verify_runtime_authority",
]
