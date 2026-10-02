"""Governed POSITION ENFORCEMENT at the live account cycle boundary (Block 3C).

WHY THIS MODULE EXISTS
----------------------
:func:`core.risk.prop_rule_runtime.PropEnforcementRuntime.enforce_positions` is
the canonical 3C decision->action path for rules that explicitly demand action
on EXISTING positions (weekend/overnight mandatory close, explicit position
reduction, governed account liquidation). It was implemented and tested but had
**no live caller**, so position-side enforcement was not runtime-reachable.

The fix is deliberately the smallest possible: ONE call site, at the narrowest
existing bounded account cycle. It is NOT a new thread, not a per-account
thread, and not a per-rule or per-provider anything.

THE CALL SITE
-------------
:class:`~core.risk.telemetry_runtime.PropRiskTelemetryService.observe_account`,
immediately AFTER the 2A -> 2B -> 2C chain produced this account's evidence and
BEFORE the result is returned. That boundary already satisfies every
precondition simultaneously:

* exact account identity is known (the cycle's own ``AccountIdentity``);
* fresh 2A/2B/2C telemetry is available, from ONE frozen instant;
* the active 3C rule pack is the runtime's own resolved pack;
* the exact open tickets were just proven by 2B for this same account;
* no broker shutdown has begun - ``main.py`` stops this cycle BEFORE it stops
  the enforcement runtime and BEFORE it releases the MT5 session.

WHAT THIS MODULE IS NOT
-----------------------
* It decides nothing. Every action it performs was already demanded by 3B and
  compiled into an :class:`EnforceDecision` by 3C. There is no rule arithmetic
  here and no new close policy.
* It never contacts a broker directly. A close travels
  ``EnforceDecision -> EnforcementExecutor -> PositionClosePort``.
* It never fabricates positions. An incomplete 2B position set means the exact
  open tickets are NOT proven, so no close is attempted at all.

THE RUNTIME CHAIN, UNCHANGED
----------------------------
::

    RulePack
      -> 3B EvaluationResult
      -> 3C EnforceDecision
      -> enforce_positions          (this module's only call site)
      -> exact close executor       (EnforcementExecutor)
      -> PositionClosePort          (one account/ticket-safe boundary)
      -> ActionResult + enforcement audit
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Sequence

from core.risk.prop_rule_enforcement import EnforcementMode
from core.risk.prop_rule_executor import CloseOutcome, CloseRequest, CloseResult
from core.risk.prop_rule_state import AccountKey
from core.risk.prop_rule_state_provider import CycleEvidence

logger = logging.getLogger(__name__)

#: Returned when prop enforcement is not configured for this deployment at all.
SKIPPED_NO_RUNTIME = "PROP_ENFORCEMENT_RUNTIME_NOT_CONFIGURED"
#: Returned when the runtime is explicitly configured off.
SKIPPED_DISABLED = "PROP_MODE_DISABLED"
#: Returned when 2B could not PROVE the complete open-ticket set.
SKIPPED_INCOMPLETE_POSITION_SET = "POSITION_SET_NOT_COMPLETE"
#: Returned when the adapter itself failed. Never a close.
SKIPPED_ADAPTER_ERROR = "PROP_POSITION_ENFORCEMENT_ERROR"


def account_key_for(identity: Any) -> AccountKey:
    """The exact :class:`AccountKey` for one cycle's account identity.

    Built from the cycle's own identity, never inferred from a ticket or a
    symbol, so one account can never satisfy another.
    """
    return AccountKey.from_block2(identity)


def _proven_open_tickets(position_set: Any) -> tuple:
    """The exact open-ticket set 2B PROVED, or nothing.

    ``PositionSetObservation`` names this ``position_tickets``; the open-risk
    aggregate spells it ``open_position_tickets``. Either spelling is accepted,
    but an absent or empty set yields NO tickets, never a guess.
    """
    for name in ("position_tickets", "open_position_tickets"):
        value = getattr(position_set, name, None)
        if value:
            return tuple(value)
    return ()


def enforce_positions_for_account(
    *,
    identity: Any,
    position_set: Any,
    observed_at_utc: datetime,
    positions: Sequence[Any] = (),
    runtime: Any = None,
    state: Any = None,
    portfolio: Any = None,
    account_snapshot: Any = None,
    open_risk: Any = None,
) -> tuple[CloseResult, ...]:
    """Run governed position enforcement for EXACTLY ONE account, once.

    This is the whole runtime wiring. It is per-account and holds no shared
    mutable account state: no shared ticket set, no cross-account short-circuit.

    Returns the exact per-ticket results. An empty tuple means "no governed
    position action was required or possible" - it never means "unknown".
    """
    account = account_key_for(identity)
    if runtime is None:
        from core.risk.prop_rule_runtime import prop_enforcement_runtime

        runtime = prop_enforcement_runtime()
    if runtime is None:
        logger.debug("[PROP_POSITION_ENFORCEMENT] skipped=%s", SKIPPED_NO_RUNTIME)
        return ()
    if getattr(runtime, "mode", None) is EnforcementMode.DISABLED:
        logger.debug("[PROP_POSITION_ENFORCEMENT] skipped=%s", SKIPPED_DISABLED)
        return ()

    # An incomplete 2B position set means the exact open tickets are NOT proven.
    # Guessing here would be the blanket liquidation this system must never do on
    # uncertainty, so nothing is attempted.
    if not bool(getattr(position_set, "position_set_complete", False)):
        logger.warning(
            "[PROP_POSITION_ENFORCEMENT] skipped=%s account=%s",
            SKIPPED_INCOMPLETE_POSITION_SET,
            account.account_id,
        )
        return ()

    open_tickets = tuple(
        int(t) for t in _proven_open_tickets(position_set)
    )
    ticket_symbols: dict[int, str] = {}
    for row in positions or ():
        ticket = getattr(row, "position_ticket", None)
        if ticket is None:
            continue
        ticket_symbols[int(ticket)] = str(getattr(row, "canonical_symbol", "") or "")

    try:
        # The EXACT 2A/2B/2C objects this cycle produced are carried through to
        # the 3B state provider under the SAME frozen instant. Nothing is
        # re-read by timestamp, and no wall clock is consulted downstream.
        return tuple(
            runtime.enforce_positions(
                account=account,
                state=state,
                at_utc=observed_at_utc,
                open_tickets=open_tickets,
                ticket_symbols=ticket_symbols,
                portfolio=portfolio,
                evidence=CycleEvidence(
                    observed_at_utc=observed_at_utc,
                    account_snapshot=account_snapshot,
                    open_risk=open_risk,
                    position_set=position_set,
                    portfolio=portfolio,
                ),
            )
        )
    except Exception as exc:  # enforcement must never kill the telemetry cycle
        logger.exception(
            "[PROP_POSITION_ENFORCEMENT] %s account=%s error=%s",
            SKIPPED_ADAPTER_ERROR, account.account_id, type(exc).__name__,
        )
        return ()


def build_position_enforcement_hook():
    """Return the keyword-only callable the telemetry cycle invokes.

    Injected rather than imported at module scope so the enforcement package
    keeps no dependency on the telemetry package, and so a test can observe the
    exact runtime call site.
    """

    def _hook(
        *, identity: Any, position_set: Any, observed_at_utc: datetime,
        positions: Sequence[Any] = (), portfolio: Any = None,
        account_snapshot: Any = None, open_risk: Any = None,
    ) -> tuple[CloseResult, ...]:
        return enforce_positions_for_account(
            identity=identity,
            position_set=position_set,
            observed_at_utc=observed_at_utc,
            positions=positions,
            portfolio=portfolio,
            account_snapshot=account_snapshot,
            open_risk=open_risk,
        )

    return _hook


#: Detail fragments that mean the close can NEVER succeed as asked.
_TERMINAL_MARKERS = (
    "EXECUTION_DISABLED", "BROKER_TRADING_DISABLED", "OWNERSHIP",
    "IDENTITY_MISMATCH", "SYMBOL_OR_TICK_UNAVAILABLE",
    "CLOSE_VOLUME_EXCEEDS_POSITION", "TERMINAL_NOT_RUNNING",
    "INVALID_ACCOUNT_CONFIGURATION", "LIFECYCLE_OPERATION_NOT_ALLOWED",
)


def _outcome_from_detail(detail: str) -> CloseOutcome:
    """Classify a failure WITHOUT a broker: identity is terminal, transport is not.

    A vanished position is a successful terminal outcome, because the position
    this system wanted gone IS gone.
    """
    text = str(detail or "")
    if "POSITION_NOT_FOUND" in text or "ALREADY_CLOSED" in text:
        return CloseOutcome.ALREADY_CLOSED
    if any(marker in text for marker in _TERMINAL_MARKERS):
        return CloseOutcome.TERMINAL_FAILURE
    return CloseOutcome.RETRYABLE_FAILURE


def _as_int(value: Any) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class LifecyclePositionClosePort:
    """The ONE production :class:`PositionClosePort`.

    It reuses the EXISTING account-owned lifecycle IPC boundary
    (:class:`core.accounts.lifecycle.LifecycleRouter`) that every other position
    close in this codebase already uses, so a governed close cannot become a
    second, divergent close path. It imports no MT5 symbol and issues no order
    itself; the pinned per-account worker performs the send.

    ACCOUNT AND TICKET SAFETY, RESTATED AT THE BOUNDARY
    ---------------------------------------------------
    The request carries the full ``(account_id, broker, server, login)`` tuple
    plus the ticket. Both are re-verified inside the worker against the pinned
    account configuration, and the position is re-read before anything is sent,
    so a vanished position is reported ``ALREADY_CLOSED`` rather than a failure.
    """

    def __init__(
        self,
        *,
        router: Any = None,
        magic: int | None = None,
        deviation: int = 20,
        close_enabled: bool | None = None,
    ) -> None:
        self._router = router
        self._magic = magic
        self._deviation = int(deviation)
        self._close_enabled = close_enabled

    # -- internals ---------------------------------------------------------
    def _resolved_magic(self) -> int:
        if self._magic is not None:
            return int(self._magic)
        from core.config import BOT_MAGIC

        return int(BOT_MAGIC)

    def _close_allowed(self) -> bool:
        if self._close_enabled is not None:
            return bool(self._close_enabled)
        from core.config import POSITION_CLOSE_ENABLED

        return bool(POSITION_CLOSE_ENABLED)

    def _get_router(self) -> Any:
        if self._router is not None:
            return self._router
        from core.accounts.lifecycle import LifecycleRouter

        return LifecycleRouter()

    @staticmethod
    def _ownership(request: CloseRequest, broker_symbol: str) -> Any:
        from core.position_ownership import PositionOwnership

        return PositionOwnership(
            account_id=request.account.account_id,
            broker=request.account.broker,
            broker_server=request.account.server,
            position_ticket=int(request.position_ticket),
            canonical_symbol=request.canonical_symbol or None,
            broker_symbol=broker_symbol,
        )

    # -- the port ----------------------------------------------------------
    def close_position(self, request: CloseRequest, *, at_utc: datetime) -> CloseResult:
        """Close EXACTLY this ticket on EXACTLY this account, or explain why not."""
        if not self._close_allowed():
            return CloseResult(
                request=request,
                outcome=CloseOutcome.TERMINAL_FAILURE,
                detail="POSITION_CLOSE_DISABLED",
                attempted_at_utc=at_utc,
            )
        broker_symbol = self._resolve_broker_symbol(request)
        if not broker_symbol:
            # An unresolvable broker symbol is a terminal identity problem, not a
            # reason to guess and close something else.
            return CloseResult(
                request=request,
                outcome=CloseOutcome.TERMINAL_FAILURE,
                detail="BROKER_SYMBOL_UNAVAILABLE",
                attempted_at_utc=at_utc,
            )
        try:
            router = self._get_router()
            owner = self._ownership(request, broker_symbol)
            value = router.read(
                owner, "close",
                magic=self._resolved_magic(), deviation=self._deviation,
                allowed=True, volume=request.volume,
            )
        except Exception as exc:
            detail = str(exc) or type(exc).__name__
            return CloseResult(
                request=request,
                outcome=_outcome_from_detail(detail),
                detail=detail,
                attempted_at_utc=at_utc,
            )
        return self._to_result(request, value, at_utc)

    def _resolve_broker_symbol(self, request: CloseRequest) -> str:
        """Resolve the canonical symbol to its broker name for this account.

        The explicit per-account configured mapping wins; the process-level
        resolver is only a fallback. An unresolvable symbol stays empty so the
        caller fails closed instead of sending against the wrong instrument.
        """
        canonical = str(request.canonical_symbol or "")
        if not canonical:
            return ""
        try:
            from core.accounts.config import load_accounts

            for config in load_accounts():
                if tuple(config.identity) != request.account.identity:
                    continue
                for name, broker_symbol in tuple(config.symbol_map or ()):
                    if name == canonical and str(broker_symbol).strip():
                        return str(broker_symbol).strip()
                break
        except Exception:  # unresolvable configuration is not a broker failure
            return ""
        try:
            from core.symbol_resolver import broker_symbol_for

            return str(broker_symbol_for(canonical) or "")
        except Exception:
            return ""

    @staticmethod
    def _to_result(
        request: CloseRequest, value: Any, at_utc: datetime,
    ) -> CloseResult:
        """Map the lifecycle worker's typed reply onto a broker-free outcome."""
        if value is None:
            return CloseResult(
                request=request, outcome=CloseOutcome.RETRYABLE_FAILURE,
                detail="LIFECYCLE_CLOSE_UNAVAILABLE", attempted_at_utc=at_utc,
            )
        data = value if isinstance(value, dict) else getattr(value, "__dict__", {})
        ok = bool(data.get("ok"))
        comment = str(data.get("comment") or "")
        retcode = data.get("retcode")
        if ok:
            return CloseResult(
                request=request, outcome=CloseOutcome.CLOSED, detail=comment,
                broker_retcode=_as_int(retcode),
                broker_deal=_as_int(data.get("deal")),
                broker_order=_as_int(data.get("order")),
                attempted_at_utc=at_utc,
            )
        if "POSITION_NOT_FOUND" in comment or "ALREADY_CLOSED" in comment:
            # The position is gone either way. That is a SUCCESSFUL terminal
            # outcome, and the executor records it so a repeat cycle is idempotent.
            return CloseResult(
                request=request, outcome=CloseOutcome.ALREADY_CLOSED,
                detail=comment or "POSITION_NOT_FOUND",
                broker_retcode=_as_int(retcode), attempted_at_utc=at_utc,
            )
        return CloseResult(
            request=request,
            outcome=_outcome_from_detail(comment or "LIFECYCLE_CLOSE_FAILED"),
            detail=comment or "LIFECYCLE_CLOSE_FAILED",
            broker_retcode=_as_int(retcode), attempted_at_utc=at_utc,
        )


__all__ = [
    "LifecyclePositionClosePort",
    "SKIPPED_ADAPTER_ERROR",
    "SKIPPED_DISABLED",
    "SKIPPED_INCOMPLETE_POSITION_SET",
    "SKIPPED_NO_RUNTIME",
    "account_key_for",
    "build_position_enforcement_hook",
    "enforce_positions_for_account",
]
