"""Enforcement ACTION EXECUTION and DURABLE STATE (Block 3C).

This module performs the runtime actions 3C decides on, and persists the state
that must survive a restart. It is deliberately separated from
:mod:`core.risk.prop_rule_enforcement`, which only decides.

THE FOUR RESPONSIBILITIES, KEPT SEPARATE
----------------------------------------
A. decide      -> prop_rule_enforcement (no side effects)
B. convert     -> prop_rule_enforcement (no side effects)
C. execute     -> THIS module
D. persist     -> THIS module

NO ``mt5.order_send`` APPEARS IN THIS MODULE, AND INDEED NO MT5 SYMBOL AT ALL.
A forced close is performed by an injected, account-scoped
:class:`PositionClosePort`. That is what makes a close attempt testable without
a broker, and what guarantees there is exactly ONE account-safe execution
boundary rather than scattered broker calls.

IDEMPOTENCY
-----------
Every close request carries a deterministic ``close_request_id`` derived from
the enforcement id and the ticket. A repeated runtime cycle therefore asks for
the SAME close, and an already-closed ticket records ``ALREADY_CLOSED`` rather
than a failure.

RETRY SEMANTICS
---------------
``RETRYABLE`` and ``TERMINAL`` are decided by the port's typed outcome, never by
a retry counter inside this module. A vanished position is a TERMINAL success
(``ALREADY_CLOSED``); a connection failure is RETRYABLE; an identity mismatch is
TERMINAL. There is no tight retry loop: the caller decides when to come back.
"""



from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields as dataclass_fields, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from core.risk.prop_rule_enforcement import (
    ActionResultStatus,
    EnforceDecision,
    EnforcementAction,
    EnforcementError,
    RecoveryClass,
)
from core.risk.prop_rule_state import AccountKey, content_hash

UTC = timezone.utc


class ExecutionError(RuntimeError):
    """The action could not be attempted safely (fail closed)."""


# ═════════════════════════════════════════════════════════════════════════════
# CLOSE AUTHORITY — the narrow, account-scoped, ticket-exact boundary
# ═════════════════════════════════════════════════════════════════════════════


class CloseOutcome(str, Enum):
    """A broker-free classification of what a close attempt actually did."""

    CLOSED = "CLOSED"
    #: The position was already gone. A SUCCESSFUL terminal outcome, not a fault.
    ALREADY_CLOSED = "ALREADY_CLOSED"
    #: Temporary broker/transport failure. A later cycle may try again.
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    #: The close can never succeed as asked (bad identity, unknown ticket).
    TERMINAL_FAILURE = "TERMINAL_FAILURE"

    @property
    def is_success(self) -> bool:
        return self in (CloseOutcome.CLOSED, CloseOutcome.ALREADY_CLOSED)

    @property
    def is_retryable(self) -> bool:
        return self is CloseOutcome.RETRYABLE_FAILURE


@dataclass(frozen=True)
class CloseRequest:
    """One exact, idempotent, account-scoped close request.

    Identity is the FULL ``(account_id, broker, server, login)`` tuple plus the
    ticket. A ticket number alone is meaningless across accounts: ticket 555 on
    account A and ticket 555 on account B are different positions.
    """

    account: AccountKey
    position_ticket: int
    enforcement_id: str
    canonical_symbol: str = ""
    reason: str = ""
    volume: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.account, AccountKey):
            raise ExecutionError("CLOSE_REQUEST_REQUIRES_ACCOUNT_KEY")
        if int(self.position_ticket) <= 0:
            raise ExecutionError("CLOSE_REQUEST_REQUIRES_POSITIVE_TICKET")
        if not str(self.enforcement_id or "").strip():
            raise ExecutionError("CLOSE_REQUEST_REQUIRES_ENFORCEMENT_ID")

    @property
    def close_request_id(self) -> str:
        """Deterministic, idempotent identity of THIS close request."""
        payload = {
            "account": self.account.to_dict(),
            "position_ticket": int(self.position_ticket),
            "enforcement_id": self.enforcement_id,
            "volume": self.volume,
        }
        return "clreq_" + content_hash(payload)[:32]

    def to_dict(self) -> dict[str, Any]:
        return {
            "close_request_id": self.close_request_id,
            "account": self.account.to_dict(),
            "account_id": self.account.account_id,
            "position_ticket": int(self.position_ticket),
            "enforcement_id": self.enforcement_id,
            "canonical_symbol": self.canonical_symbol,
            "reason": self.reason,
            "volume": self.volume,
        }


@dataclass(frozen=True)
class CloseResult:
    """The exact, truthful result of one close attempt."""

    request: CloseRequest
    outcome: CloseOutcome
    detail: str = ""
    broker_retcode: int | None = None
    broker_deal: int | None = None
    broker_order: int | None = None
    attempted_at_utc: datetime | None = None

    @property
    def is_success(self) -> bool:
        return self.outcome.is_success

    @property
    def is_retryable(self) -> bool:
        return self.outcome.is_retryable

    def to_status(self) -> ActionResultStatus:
        return {
            CloseOutcome.CLOSED: ActionResultStatus.SUCCESS,
            CloseOutcome.ALREADY_CLOSED: ActionResultStatus.ALREADY_CLOSED,
            CloseOutcome.RETRYABLE_FAILURE: ActionResultStatus.FAILED_RETRYABLE,
            CloseOutcome.TERMINAL_FAILURE: ActionResultStatus.FAILED_TERMINAL,
        }[self.outcome]

    def to_dict(self) -> dict[str, Any]:
        return {
            "close_request_id": self.request.close_request_id,
            "account_id": self.request.account.account_id,
            "position_ticket": int(self.request.position_ticket),
            "enforcement_id": self.request.enforcement_id,
            "outcome": self.outcome.value,
            "detail": self.detail,
            "broker_retcode": self.broker_retcode,
            "broker_deal": self.broker_deal,
            "broker_order": self.broker_order,
            "attempted_at_utc": (
                self.attempted_at_utc.isoformat() if self.attempted_at_utc else None
            ),
        }


@runtime_checkable
class PositionClosePort(Protocol):
    """The single account-safe execution boundary for a forced close.

    Implementations must re-read the CURRENT position, confirm the account and
    ticket still match, and report an already-vanished position as
    ``ALREADY_CLOSED`` rather than as a failure. No MT5 symbol appears anywhere in
    the enforcement package, so a forced close can only happen through here.
    """

    def close_position(self, request: CloseRequest, *, at_utc: datetime) -> CloseResult:
        ...


# ═════════════════════════════════════════════════════════════════════════════
# DURABLE ENFORCEMENT STATE — account suspension, challenge, kill switch, audit
# ═════════════════════════════════════════════════════════════════════════════


class KillSwitchState(str, Enum):
    """Kill-switch lifecycle. TRIGGERED/LOCKED survive a process restart."""

    #: Armed and available, not currently tripped.
    ACTIVE = "ACTIVE"
    #: Tripped by a governed decision; entries refused.
    TRIGGERED = "TRIGGERED"
    #: Tripped, but an explicit authority may clear it.
    RECOVERABLE = "RECOVERABLE"
    #: Tripped permanently. No runtime path can clear this.
    LOCKED = "LOCKED"


class SuspensionScope(str, Enum):
    """ACCOUNT is a runtime halt; CHALLENGE is a program-level failure.

    They are persisted separately because "this account is temporarily blocked"
    and "this challenge has permanently failed" are different facts with
    different clearance authorities.
    """

    ACCOUNT = "ACCOUNT"
    CHALLENGE = "CHALLENGE"


@dataclass(frozen=True)
class SuspensionState:
    """A durable, account-scoped (or challenge-scoped) enforcement block.

    There is NO silent auto-clear. ``cleared_at_utc`` is set only by an explicit
    clearance call that records WHO cleared it and under what authority.
    """

    suspension_id: str
    account: AccountKey
    scope: SuspensionScope
    rule_pack_id: str
    triggering_enforcement_id: str
    reason: str
    triggered_at_utc: datetime
    recovery: RecoveryClass
    terminal: bool = False
    #: The rule day this suspension was raised on, for RULE_DAY_RESET recovery.
    rule_day: str = ""
    cleared_at_utc: datetime | None = None
    cleared_by: str = ""
    clearance_authority: str = ""

    def __post_init__(self) -> None:
        if not str(self.suspension_id or "").strip():
            raise EnforcementError("SUSPENSION_REQUIRES_ID")
        if self.triggered_at_utc.tzinfo is None:
            raise EnforcementError("SUSPENSION_REQUIRES_AWARE_INSTANT")
        if self.cleared_at_utc is not None and not str(self.cleared_by or "").strip():
            raise EnforcementError("SUSPENSION_CLEARANCE_REQUIRES_AUTHORITY")

    @property
    def is_active(self) -> bool:
        return self.cleared_at_utc is None

    @property
    def clears_on_new_rule_day(self) -> bool:
        return self.recovery is RecoveryClass.RULE_DAY_RESET and not self.terminal

    def cleared(self, *, at_utc: datetime, by: str, authority: str) -> "SuspensionState":
        return replace(
            self,
            cleared_at_utc=at_utc,
            cleared_by=by,
            clearance_authority=authority,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            f.name: getattr(self, f.name) for f in dataclass_fields(self)
        }
        payload["account"] = self.account.to_dict()
        payload["scope"] = self.scope.value
        payload["recovery"] = self.recovery.value
        payload["triggered_at_utc"] = self.triggered_at_utc.isoformat()
        payload["cleared_at_utc"] = (
            self.cleared_at_utc.isoformat() if self.cleared_at_utc else None
        )
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SuspensionState":
        return cls(
            suspension_id=str(payload["suspension_id"]),
            account=AccountKey.from_dict(payload["account"]),
            scope=SuspensionScope(payload["scope"]),
            rule_pack_id=str(payload["rule_pack_id"]),
            triggering_enforcement_id=str(payload["triggering_enforcement_id"]),
            reason=str(payload["reason"]),
            triggered_at_utc=datetime.fromisoformat(payload["triggered_at_utc"]),
            recovery=RecoveryClass(payload["recovery"]),
            terminal=bool(payload.get("terminal", False)),
            rule_day=str(payload.get("rule_day", "")),
            cleared_at_utc=(
                datetime.fromisoformat(payload["cleared_at_utc"])
                if payload.get("cleared_at_utc")
                else None
            ),
            cleared_by=str(payload.get("cleared_by", "")),
            clearance_authority=str(payload.get("clearance_authority", "")),
        )


def record_to_identity(record: Any) -> dict[str, Any]:
    """A hashable, JSON-safe identity view of an audit record's fields.

    Used to derive ``record_id`` so the SAME decision on the SAME evidence
    always produces the SAME id, on any machine, after any restart.
    """
    account = record.get("account") if isinstance(record, dict) else None
    return {
        "enforcement_id": record["enforcement_id"],
        "evaluation_ids": list(record["evaluation_ids"]),
        "account": account.to_dict() if hasattr(account, "to_dict") else account,
        "rule_pack_id": record["rule_pack_id"],
        "rule_id": record["rule_id"],
        "evaluated_status": record["evaluated_status"],
        "enforcement_action": record["enforcement_action"],
        "applies_to": record["applies_to"],
        "symbols": list(record["symbols"]),
        "recorded_at_utc": (
            record["recorded_at_utc"].isoformat()
            if hasattr(record["recorded_at_utc"], "isoformat")
            else record["recorded_at_utc"]
        ),
        "mode": record["mode"],
    }


@dataclass(frozen=True)
class EnforcementAuditRecord:
    """One immutable, append-only record of a material enforcement decision.

    COMPLETE LINEAGE, NO MISSING HOP
    -------------------------------
    ``rule_pack_id`` -> ``rule_id`` -> ``evaluation_id`` -> ``enforcement_id``
    -> the attempted action and its exact result are all present, together with
    the evidence ids and the broker response when there was one. A reader can
    always walk the chain back to the rule and forward to what happened.
    """

    record_id: str
    enforcement_id: str
    evaluation_ids: tuple[str, ...]
    account: AccountKey
    rule_pack_id: str
    rule_id: str
    rule_type: str
    evaluated_status: str
    enforcement_action: str
    criticality: str
    severity: str
    applies_to: str
    symbols: tuple[str, ...]
    position_ticket: int | None
    evidence_used: tuple[str, ...]
    recorded_at_utc: datetime
    mode: str
    action_attempted: bool = False
    action_result: str = ActionResultStatus.NOT_ATTEMPTED.value
    broker_detail: str = ""
    retryable: bool = False
    terminal: bool = False
    fail_closed_reason: str = ""
    external_source_provenance: str = ""

    @property
    def is_material(self) -> bool:
        """Whether this decision changed (or attempted to change) runtime state."""
        return self.enforcement_action not in ("NO_ACTION", "ALLOW")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            f.name: getattr(self, f.name) for f in dataclass_fields(self)
        }
        payload["account"] = self.account.to_dict()
        payload["account_id"] = self.account.account_id
        payload["evaluation_ids"] = list(self.evaluation_ids)
        payload["symbols"] = list(self.symbols)
        payload["evidence_used"] = list(self.evidence_used)
        payload["recorded_at_utc"] = self.recorded_at_utc.isoformat()
        return payload

    @classmethod
    def from_decision(
        cls,
        *,
        decision: EnforceDecision,
        mode: str,
        recorded_at_utc: datetime,
        external_source_provenance: str = "",
    ) -> "EnforcementAuditRecord":
        payload = {
            "enforcement_id": decision.enforcement_id,
            "evaluation_ids": tuple(decision.source_evaluation_ids),
            "account": decision.account,
            "rule_pack_id": decision.rule_pack_id,
            "rule_id": decision.rule_id,
            "rule_type": decision.rule_type.value,
            "evaluated_status": decision.evaluated_status.value,
            "enforcement_action": decision.enforcement_action.value,
            "criticality": decision.criticality.value,
            "severity": decision.severity.value,
            "applies_to": decision.applies_to.value,
            "symbols": tuple(decision.symbols),
            "position_ticket": decision.position_ticket,
            "evidence_used": tuple(decision.evidence_used),
            "recorded_at_utc": recorded_at_utc,
            "mode": mode,
            "action_attempted": decision.action_attempted,
            "action_result": decision.action_result.value,
            "broker_detail": decision.detail,
            "retryable": decision.retryable,
            "terminal": decision.terminal,
            "fail_closed_reason": decision.fail_closed_reason,
            "external_source_provenance": external_source_provenance,
        }
        # The id is derived from the SERIALISED form so a rebuild from disk
        # reproduces exactly the same record id.
        identity = content_hash(record_to_identity(payload))
        return cls(record_id="audit_" + identity[:32], **payload)


# ═════════════════════════════════════════════════════════════════════════════
# DURABLE STORE — append-only, account-isolated, restart-reconstructable
# ═════════════════════════════════════════════════════════════════════════════

#: The operational durable families 3C owns. Deliberately NOT Production V1
#: datasets: these are per-account operational enforcement state with a
#: different lifecycle, and nothing in 3C justifies widening that namespace.
ENFORCEMENT_FAMILIES: tuple[str, ...] = (
    "suspension",
    "kill_switch",
    "enforcement_audit",
    "close_attempt",
)


def audit_record_from_dict(payload: Mapping[str, Any]) -> EnforcementAuditRecord:
    """Re-hydrate an audit record from its durable JSON form.

    JSON has no enums and no AccountKey, so every derived field is rebuilt
    explicitly rather than splatted, which keeps a restart byte-identical to the
    original in-memory record.
    """
    return EnforcementAuditRecord(
        record_id=str(payload["record_id"]),
        enforcement_id=str(payload["enforcement_id"]),
        evaluation_ids=tuple(payload["evaluation_ids"]),
        account=AccountKey.from_dict(payload["account"]),
        rule_pack_id=str(payload["rule_pack_id"]),
        rule_id=str(payload["rule_id"]),
        rule_type=str(payload["rule_type"]),
        evaluated_status=str(payload["evaluated_status"]),
        enforcement_action=str(payload["enforcement_action"]),
        criticality=str(payload["criticality"]),
        severity=str(payload["severity"]),
        applies_to=str(payload["applies_to"]),
        symbols=tuple(payload["symbols"]),
        position_ticket=payload.get("position_ticket"),
        evidence_used=tuple(payload.get("evidence_used") or ()),
        recorded_at_utc=datetime.fromisoformat(payload["recorded_at_utc"]),
        mode=str(payload["mode"]),
        action_attempted=bool(payload.get("action_attempted", False)),
        action_result=str(
            payload.get("action_result") or ActionResultStatus.NOT_ATTEMPTED.value
        ),
        broker_detail=str(payload.get("broker_detail", "")),
        retryable=bool(payload.get("retryable", False)),
        terminal=bool(payload.get("terminal", False)),
        fail_closed_reason=str(payload.get("fail_closed_reason", "")),
        external_source_provenance=str(payload.get("external_source_provenance", "")),
    )


class EnforcementStateStore:
    """Append-only, account-isolated, restart-reconstructable enforcement state.

    NOTHING here depends on process memory. :meth:`rebuild` reconstructs every
    family from the durable records, which is what stops a process restart from
    clearing a terminal prop breach.
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base_dir = Path(base_dir) if base_dir is not None else None
        self._suspensions: dict[str, SuspensionState] = {}
        self._kill_switches: dict[str, KillSwitchState] = {}
        self._audit: list[EnforcementAuditRecord] = []
        self._seen_audit: set[str] = set()
        self._completed_closes: set[str] = set()
        if self._base_dir is not None:
            self.rebuild()

    # ── paths ────────────────────────────────────────────────────────────────
    def _path(self, family: str, account: AccountKey | None) -> Path:
        if family not in ENFORCEMENT_FAMILIES:
            raise ExecutionError(f"UNKNOWN_ENFORCEMENT_FAMILY:{family}")
        assert self._base_dir is not None
        name = account.slug if account is not None else "_global"
        return self._base_dir / family / f"{name}.jsonl"

    def _append(
        self,
        family: str,
        account: AccountKey | None,
        payload: Mapping[str, Any],
    ) -> None:
        if self._base_dir is None:
            return
        path = self._path(family, account)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, (line + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:  # pragma: no cover - filesystem failure path
            raise ExecutionError(f"ENFORCEMENT_APPEND_FAILED:{family}:{exc}") from exc

    @staticmethod
    def _records(path: Path) -> list[Mapping[str, Any]]:
        if not path.exists():
            return []
        rows: list[Mapping[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if text:
                    rows.append(json.loads(text))
        return rows

    def _family_files(self, family: str) -> list[Path]:
        assert self._base_dir is not None
        directory = self._base_dir / family
        return sorted(directory.glob("*.jsonl")) if directory.exists() else []

    # ── suspensions ──────────────────────────────────────────────────────────
    def record_suspension(self, suspension: SuspensionState) -> SuspensionState:
        """Persist a suspension. Re-recording the SAME id is idempotent."""
        existing = self._suspensions.get(suspension.suspension_id)
        if existing is not None:
            return existing
        self._suspensions[suspension.suspension_id] = suspension
        self._append("suspension", suspension.account, suspension.to_dict())
        return suspension

    def clear_suspension(
        self, suspension_id: str, *, at_utc: datetime, by: str, authority: str
    ) -> SuspensionState:
        """Clear a suspension under an EXPLICIT authority. Never automatic."""
        existing = self._suspensions.get(suspension_id)
        if existing is None:
            raise ExecutionError(f"UNKNOWN_SUSPENSION:{suspension_id}")
        if not existing.is_active:
            return existing
        if existing.terminal and authority == "RUNTIME_AUTO":
            raise ExecutionError(f"TERMINAL_SUSPENSION_REQUIRES_AUTHORITY:{suspension_id}")
        cleared = existing.cleared(at_utc=at_utc, by=by, authority=authority)
        self._suspensions[suspension_id] = cleared
        self._append("suspension", cleared.account, cleared.to_dict())
        return cleared

    def suspensions_for(self, account: AccountKey) -> tuple[SuspensionState, ...]:
        """Active suspensions for one EXACT account identity, oldest first."""
        found = [
            s for s in self._suspensions.values()
            if s.account.identity == account.identity and s.is_active
        ]
        return tuple(sorted(found, key=lambda s: (s.triggered_at_utc, s.suspension_id)))

    def all_suspensions(self) -> tuple[SuspensionState, ...]:
        return tuple(sorted(self._suspensions.values(), key=lambda s: s.suspension_id))

    def suspension_id_for(
        self, enforcement_id: str, *, scope: str = "ACCOUNT"
    ) -> str:
        """Deterministic suspension id for one triggering enforcement decision.

        Because it is derived, the same enforcement decision can never create two
        different suspension records on repeated cycles. The SCOPE is part of the
        identity so an account-level block and a challenge-level failure raised by
        the SAME decision remain two distinct, separately-clearable states.
        """
        return "susp_" + content_hash(
            {"enforcement_id": enforcement_id, "scope": scope}
        )[:32]

    # ── kill switch ─────────────────────────────────────────────────────────
    def record_kill_switch(
        self, account: AccountKey, state: KillSwitchState, *, at_utc: datetime, enforcement_id: str
    ) -> KillSwitchState:
        """Persist a kill-switch transition.

        A terminal ``LOCKED`` state is never downgraded: once locked, a later
        TRIGGERED/ACTIVE write cannot unlock it, so a process restart cannot
        clear a terminal prop breach.
        """
        key = account.slug
        current = self._kill_switches.get(key, KillSwitchState.ACTIVE)
        if current is KillSwitchState.LOCKED and state is not KillSwitchState.LOCKED:
            return current
        if state is current:
            return current
        self._kill_switches[key] = state
        self._append(
            "kill_switch",
            account,
            {
                "account": account.to_dict(),
                "state": state.value,
                "at_utc": at_utc.isoformat(),
                "enforcement_id": enforcement_id,
            },
        )
        return state

    def kill_switch_for(self, account: AccountKey) -> KillSwitchState:
        return self._kill_switches.get(account.slug, KillSwitchState.ACTIVE)

    def kill_switch_tripped(self, account: AccountKey) -> bool:
        """Whether entries must be refused regardless of the rule evaluations."""
        return self.kill_switch_for(account) in (
            KillSwitchState.TRIGGERED,
            KillSwitchState.RECOVERABLE,
            KillSwitchState.LOCKED,
        )

    # ── audit ────────────────────────────────────────────────────────────────
    def record_audit(self, record: EnforcementAuditRecord) -> EnforcementAuditRecord:
        """Append one material decision. Re-recording the SAME id is a no-op.

        Idempotency is what stops an uncontrolled duplicate audit trail when the
        same decision is observed on many cycles.
        """
        if record.record_id in self._seen_audit:
            return record
        self._seen_audit.add(record.record_id)
        self._audit.append(record)
        self._append("enforcement_audit", record.account, record.to_dict())
        return record

    def audit_records(
        self, account: AccountKey | None = None
    ) -> tuple[EnforcementAuditRecord, ...]:
        rows = [
            r for r in self._audit
            if account is None or r.account.identity == account.identity
        ]
        return tuple(sorted(rows, key=lambda r: (r.recorded_at_utc, r.record_id)))

    # ── close idempotency ────────────────────────────────────────────────────
    def record_close_attempt(self, result: CloseResult, *, at_utc: datetime) -> CloseResult:
        """Persist a close attempt and remember terminal successes.

        Remembering a successful ``close_request_id`` is what makes a repeated
        cycle report the truth instead of re-issuing the same close forever.
        """
        if result.is_success:
            self._completed_closes.add(result.request.close_request_id)
        self._append("close_attempt", result.request.account, result.to_dict())
        return result

    def close_completed(self, close_request_id: str) -> bool:
        return close_request_id in self._completed_closes

    # ── restart ──────────────────────────────────────────────────────────────
    def rebuild(self) -> None:
        """Reconstruct every family from the durable records.

        Deterministic and order-independent: suspensions are replayed in
        recorded order so a later clearance supersedes an earlier trigger.
        """
        if self._base_dir is None:
            return
        self._suspensions.clear()
        self._kill_switches.clear()
        self._audit.clear()
        self._seen_audit.clear()
        self._completed_closes.clear()

        for path in self._family_files("suspension"):
            for row in self._records(path):
                suspension = SuspensionState.from_dict(row)
                self._suspensions[suspension.suspension_id] = suspension
        for path in self._family_files("kill_switch"):
            for row in self._records(path):
                account = AccountKey.from_dict(row["account"])
                state = KillSwitchState(row["state"])
                key = account.slug
                if self._kill_switches.get(key) is KillSwitchState.LOCKED and state is not KillSwitchState.LOCKED:
                    continue
                self._kill_switches[key] = state
        for path in self._family_files("enforcement_audit"):
            for row in self._records(path):
                record = audit_record_from_dict(row)
                if record.record_id in self._seen_audit:
                    continue
                self._seen_audit.add(record.record_id)
                self._audit.append(record)
        for path in self._family_files("close_attempt"):
            for row in self._records(path):
                if row.get("outcome") in (CloseOutcome.CLOSED.value, CloseOutcome.ALREADY_CLOSED.value):
                    self._completed_closes.add(str(row["close_request_id"]))


# ═════════════════════════════════════════════════════════════════════════════
# ENFORCEMENT EXECUTOR — turn decisions into runtime actions, exactly once
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class LiquidationReport:
    """Per-ticket truth for one account liquidation.

    ``fully_liquidated`` is True only when every ticket succeeded. A partial
    liquidation must never be reported as complete.
    """

    account: AccountKey
    results: tuple[CloseResult, ...] = ()

    @property
    def closed_count(self) -> int:
        return sum(1 for r in self.results if r.outcome is CloseOutcome.CLOSED)

    @property
    def already_closed_count(self) -> int:
        return sum(1 for r in self.results if r.outcome is CloseOutcome.ALREADY_CLOSED)

    @property
    def retryable_count(self) -> int:
        return sum(1 for r in self.results if r.is_retryable)

    @property
    def terminal_failure_count(self) -> int:
        return sum(1 for r in self.results if r.outcome is CloseOutcome.TERMINAL_FAILURE)

    @property
    def fully_liquidated(self) -> bool:
        return bool(self.results) and all(r.is_success for r in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account.account_id,
            "attempted": len(self.results),
            "closed": self.closed_count,
            "already_closed": self.already_closed_count,
            "retryable": self.retryable_count,
            "terminal_failures": self.terminal_failure_count,
            "fully_liquidated": self.fully_liquidated,
            "results": [r.to_dict() for r in self.results],
        }


class EnforcementExecutor:
    """Executes the runtime actions 3C decided on.

    It is a plain object with injected collaborators: a
    :class:`EnforcementStateStore` and a :class:`PositionClosePort`. No clock is
    read here, no broker is contacted, and nothing is retried internally. Every
    outcome is typed and persisted.
    """

    def __init__(
        self,
        *,
        store: EnforcementStateStore,
        close_port: PositionClosePort | None = None,
    ) -> None:
        self._store = store
        self._close_port = close_port

    # ── entry-side actions ───────────────────────────────────────────────────
    def suspend(
        self,
        decision: EnforceDecision,
        *,
        at_utc: datetime,
        rule_day: str = "",
        scope: SuspensionScope = SuspensionScope.ACCOUNT,
        terminal: bool = False,
    ) -> SuspensionState:
        """Persist an account/challenge suspension raised by ``decision``.

        Idempotent by construction: the suspension id is derived from the
        enforcement id, so repeated cycles add nothing new.
        """
        suspension = SuspensionState(
            suspension_id=self._store.suspension_id_for(
                decision.enforcement_id, scope=scope.value
            ),
            account=decision.account,
            scope=scope,
            rule_pack_id=decision.rule_pack_id,
            triggering_enforcement_id=decision.enforcement_id,
            reason=decision.reason_code.value,
            triggered_at_utc=at_utc,
            recovery=decision.recovery,
            terminal=bool(terminal or decision.terminal),
            rule_day=rule_day,
        )
        return self._store.record_suspension(suspension)

    def trigger_kill_switch(
        self, decision: EnforceDecision, *, at_utc: datetime, locked: bool = False
    ) -> KillSwitchState:
        """Trip the kill-switch durably for one account."""
        state = KillSwitchState.LOCKED if locked else KillSwitchState.TRIGGERED
        return self._store.record_kill_switch(
            decision.account,
            state,
            at_utc=at_utc,
            enforcement_id=decision.enforcement_id,
        )

    # -- close-side actions ---------------------------------------------------

    def close_position(
        self,
        decision: EnforceDecision,
        *,
        ticket: int,
        at_utc: datetime,
        canonical_symbol: str = "",
    ) -> CloseResult:
        """Close EXACTLY one ticket on EXACTLY one account.

        * The request carries the full account identity, so a ticket number can
          never be closed on the wrong account.
        * A close this executor already completed is reported ALREADY_CLOSED
          without calling the port again.
        * ALREADY_CLOSED is a SUCCESS: the position is gone either way.
        """
        if self._close_port is None:
            raise ExecutionError("NO_POSITION_CLOSE_PORT_CONFIGURED")
        request = CloseRequest(
            account=decision.account,
            position_ticket=int(ticket),
            enforcement_id=decision.enforcement_id,
            canonical_symbol=canonical_symbol,
            reason=decision.reason_code.value,
        )
        if self._store.close_completed(request.close_request_id):
            return CloseResult(
                request=request,
                outcome=CloseOutcome.ALREADY_CLOSED,
                detail="ENFORCEMENT_CLOSE_ALREADY_COMPLETED",
                attempted_at_utc=at_utc,
            )
        result = self._close_port.close_position(request, at_utc=at_utc)
        self._store.record_close_attempt(result, at_utc=at_utc)
        return result

    def liquidate_account(
        self,
        decision: EnforceDecision,
        *,
        tickets: Any,
        at_utc: datetime,
        canonical_symbols: Any = None,
    ) -> LiquidationReport:
        """Close every ticket individually, and report each one's own truth.

        One ticket failing never hides another ticket's success, and the report
        is only ``fully_liquidated`` when every single close succeeded.
        """
        symbols = dict(canonical_symbols or {})
        results: list[CloseResult] = []
        for ticket in tickets:
            try:
                results.append(
                    self.close_position(
                        decision,
                        ticket=int(ticket),
                        at_utc=at_utc,
                        canonical_symbol=symbols.get(int(ticket), ""),
                    )
                )
            except ExecutionError as exc:
                results.append(
                    CloseResult(
                        request=CloseRequest(
                            account=decision.account,
                            position_ticket=int(ticket),
                            enforcement_id=decision.enforcement_id,
                        ),
                        outcome=CloseOutcome.TERMINAL_FAILURE,
                        detail=str(exc),
                        attempted_at_utc=at_utc,
                    )
                )
        return LiquidationReport(account=decision.account, results=tuple(results))

    def mark_attempt(
        self, decision: EnforceDecision, result: CloseResult
    ) -> EnforceDecision:
        """Attach the truthful outcome WITHOUT altering the rule verdict."""
        return decision.with_attempt(
            attempted=True,
            result=result.to_status(),
            retryable=result.is_retryable,
            terminal=not result.is_success and not result.is_retryable,
            detail=result.detail,
        )
