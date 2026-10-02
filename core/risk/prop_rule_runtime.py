

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from core.risk.prop_rule_enforcement import (
    ActionResultStatus,
    EnforcementCriticality,
    DegradedReason,
    EffectiveEnforcement,
    EnforceDecision,
    EnforcementAction,
    EnforcementError,
    EnforcementMode,
    EnforcementReason,
    EnforcementScope,
    EnforcementSeverity,
    compile_decision,
    coalesce_decisions,
)
from core.risk.prop_rule_evaluator import (
    EvaluationContext,
    EvaluationResult,
    evaluate_pack,
)
from core.risk.prop_rule_external import (
    EconomicCalendarSnapshot,
    GovernedCalendarSource,
    MarketSessionAnswer,
    SourceFreshness,
)
from core.risk.prop_rule_executor import (
    CloseOutcome,
    CloseResult,
    EnforcementAuditRecord,
    EnforcementExecutor,
    EnforcementStateStore,
    SuspensionScope,
)
from core.risk.prop_rule_projection import (
    ExposureProjection,
    PlannedOrder,
    ProjectionError,
    build_projected_state,
    project_exposure,
)
from core.risk.prop_rule_state import AccountEvaluationState, AccountKey

UTC = timezone.utc
logger = logging.getLogger(__name__)


def _provider_accepts_context(provider: Any) -> bool:
    """Whether a state provider takes the frozen instant and cycle evidence.

    The production provider does, so it never reads a clock. A minimal provider
    that takes only the account keeps working unchanged.
    """
    try:
        parameters = inspect.signature(provider).parameters
    except (TypeError, ValueError):
        return False
    return "evidence" in parameters or "at_utc" in parameters


@dataclass(frozen=True)
class EntryVerdict:
    """The authoritative answer for ONE account and ONE proposed position.

    A verdict is always explicit. There is no "allowed because we did not look".
    """

    account: AccountKey
    symbol: str
    allowed: bool
    reason: EnforcementReason
    detail: str
    mode: EnforcementMode
    evaluated_at_utc: datetime
    rule_pack_id: str = ""
    degraded_reason: DegradedReason = DegradedReason.NONE
    #: Every decision considered, including NO_ACTION, for full lineage.
    decisions: tuple[EnforceDecision, ...] = ()
    #: Only the ones that actually block this position.
    blocking: tuple[EnforceDecision, ...] = ()
    #: POSITION-REDUCTION decisions. Kept separate from ``blocking`` because
    #: closing existing risk and refusing new risk are different decisions.
    position_reductions: tuple[EnforceDecision, ...] = ()
    #: The projected-evaluation decisions, when a proposed order was supplied.
    projected_decisions: tuple[EnforceDecision, ...] = ()
    projected_state_complete: bool = False
    projected_unavailable_fields: tuple[str, ...] = ()

    @property
    def account_id(self) -> str:
        """Convenience accessor for the EXACT account this verdict binds."""
        return self.account.account_id

    @property
    def block_reason_text(self) -> str:
        """A stable, machine-greppable block code for the order path."""
        if self.allowed:
            return "PROP_ALLOW"
        if self.degraded_reason is not DegradedReason.NONE:
            return f"PROP_DEGRADED:{self.degraded_reason.value}"
        if self.blocking:
            return "PROP_BLOCK:" + ",".join(
                sorted({d.reason_code.value for d in self.blocking})
            )
        return f"PROP_BLOCK:{self.reason.value}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account.account_id,
            "symbol": self.symbol,
            "allowed": self.allowed,
            "reason": self.reason.value,
            "block_reason_text": self.block_reason_text,
            "detail": self.detail,
            "mode": self.mode.value,
            "evaluated_at_utc": self.evaluated_at_utc.isoformat(),
            "rule_pack_id": self.rule_pack_id,
            "degraded_reason": self.degraded_reason.value,
            "decisions": [d.enforcement_id for d in self.decisions],
            "blocking": [d.enforcement_id for d in self.blocking],
            "projected_decisions": [d.enforcement_id for d in self.projected_decisions],
            "projected_state_complete": self.projected_state_complete,
            "projected_unavailable_fields": list(self.projected_unavailable_fields),
        }


def _aggregate_action_result(results: Sequence[CloseResult]) -> ActionResultStatus:
    """The ONE truthful status for a multi-ticket close.

    A partial liquidation is never summarised as a success, and a single
    retryable ticket keeps the whole decision retryable rather than terminal.
    """
    if not results:
        return ActionResultStatus.NOT_ATTEMPTED
    if all(r.is_success for r in results):
        if all(r.outcome is CloseOutcome.ALREADY_CLOSED for r in results):
            return ActionResultStatus.ALREADY_CLOSED
        return ActionResultStatus.SUCCESS
    if any(r.is_retryable for r in results):
        return ActionResultStatus.FAILED_RETRYABLE
    return ActionResultStatus.FAILED_TERMINAL


@dataclass
class PropEnforcementRuntimeStatus:
    """Inspectable enforcement status. Local observability only."""

    mode: EnforcementMode = EnforcementMode.DISABLED
    ready: bool = False
    active_rule_pack_id: str = ""
    degraded_reason: DegradedReason = DegradedReason.NONE
    degraded_detail: str = ""
    last_evaluated_at_utc: str = ""
    last_enforcement_id: str = ""
    last_block_reason: str = ""
    external_sources: dict[str, str] = field(default_factory=dict)
    account_states: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "ready": self.ready,
            "active_rule_pack_id": self.active_rule_pack_id,
            "degraded_reason": self.degraded_reason.value,
            "degraded_detail": self.degraded_detail,
            "last_evaluated_at_utc": self.last_evaluated_at_utc,
            "last_enforcement_id": self.last_enforcement_id,
            "last_block_reason": self.last_block_reason,
            "external_sources": dict(self.external_sources),
            "account_states": dict(self.account_states),
        }


# =============================================================================
# STARTUP READINESS
# =============================================================================

@dataclass(frozen=True)
class ReadinessCheck:
    """One startup requirement, and whether it is satisfied."""

    name: str
    satisfied: bool
    reason: DegradedReason = DegradedReason.NONE
    detail: str = ""


@dataclass(frozen=True)
class ReadinessReport:
    """The COMPLETE compliance-readiness picture at startup.

    ``ready`` is True only when every required check passed. A missing pack is
    reported as ``RULE_PACK_MISSING``; it is NEVER reported as "prop mode is
    disabled", which is a separate, explicitly configured state.
    """

    ready: bool
    mode: EnforcementMode
    checks: tuple[ReadinessCheck, ...] = ()

    @property
    def degraded_reason(self) -> DegradedReason:
        """The exact reason enforcement is not fully active.

        A FAILED check always wins. When every check passed but prop mode is
        explicitly disabled, that explicit reason is reported, so a reader can
        never confuse "intentionally off" with "nothing configured".
        """
        for check in self.checks:
            if not check.satisfied and check.reason is not DegradedReason.NONE:
                return check.reason
        for check in self.checks:
            if check.satisfied and check.reason is not DegradedReason.NONE:
                return check.reason
        return DegradedReason.NONE

    @property
    def failures(self) -> tuple[ReadinessCheck, ...]:
        return tuple(c for c in self.checks if not c.satisfied)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "mode": self.mode.value,
            "degraded_reason": self.degraded_reason.value,
            "checks": [
                {
                    "name": c.name,
                    "satisfied": c.satisfied,
                    "reason": c.reason.value,
                    "detail": c.detail,
                }
                for c in self.checks
            ],
        }


# =============================================================================
# THE RUNTIME
# =============================================================================

class PropEnforcementRuntime:
    """The ONE managed prop-rule enforcement authority for a live process.

    Every collaborator is injected: the 3A pack store, the 3B state store, the
    external providers, the correlation model and the close port. The runtime
    itself owns no clock, no broker connection and no thread.

    STARTUP
    -------
    :meth:`start` verifies the pack, restores durable suspension/kill state and
    checks every required external provider. Trading must not occur before this
    returns ``ready=True`` in ``LIVE_ENFORCE`` mode.
    """

    def __init__(
        self,
        *,
        mode: EnforcementMode = EnforcementMode.DISABLED,
        rule_pack_store: Any = None,
        state_store: EnforcementStateStore | None = None,
        executor: EnforcementExecutor | None = None,
        close_port: Any = None,
        pack_identity: Any = None,
        rule_day_definition: Any = None,
        economic_calendar: Any = None,
        market_sessions: Any = None,
        correlation_model: Any = None,
        required_external: Any = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.mode = mode
        self._packs = rule_pack_store
        self._state_store = state_store or EnforcementStateStore()
        self._executor = executor or EnforcementExecutor(
            store=self._state_store, close_port=close_port
        )
        self._pack_identity = pack_identity
        self._rule_day = rule_day_definition
        # Providers are wrapped in their GOVERNED adapters, which is where the
        # freshness guarantee is enforced on the way into Block 3B.
        self._calendar = (
            GovernedCalendarSource(economic_calendar)
            if economic_calendar is not None
            else None
        )
        self._sessions = market_sessions
        self._model = correlation_model
        self._required_external = tuple(required_external or ())
        self._clock = clock
        self._active_pack: Any = None
        self._status = PropEnforcementRuntimeStatus(mode=mode)
        self._stopped = False

    # -- lifecycle -----------------------------------------------------------

    def start(self, *, at_utc: datetime) -> ReadinessReport:
        """Verify readiness and restore durable state. Idempotent."""
        self._stopped = False
        if self.mode is EnforcementMode.DISABLED:
            report = ReadinessReport(
                ready=True,
                mode=self.mode,
                checks=(
                    ReadinessCheck(
                        "prop_mode", True, DegradedReason.PROP_MODE_DISABLED,
                        "PROP_MODE_DISABLED_EXPLICITLY_CONFIGURED",
                    ),
                ),
            )
            self._status.ready = True
            self._status.degraded_reason = DegradedReason.PROP_MODE_DISABLED
            return report

        checks: list[ReadinessCheck] = [
            self._check_rule_pack(at_utc),
            self._check_required_providers(at_utc),
            self._check_state_provider(),
        ]
        self._restore_durable_state(at_utc)
        report = ReadinessReport(
            ready=all(c.satisfied for c in checks), mode=self.mode, checks=tuple(checks)
        )
        if self.mode is not EnforcementMode.DISABLED:
            self.external_freshness(at_utc=at_utc)
        self._status.ready = report.ready
        self._status.degraded_reason = report.degraded_reason
        for failure in report.failures:
            self._status.degraded_detail = f"{failure.name}:{failure.detail}"
        return report

    def stop(self) -> None:
        """Stop the service cleanly.

        There is no background thread to join, so shutdown is just marking the
        service stopped. Any enforcement in flight fails closed from here on.
        """
        self._stopped = True
        self._status.ready = False

    @property
    def stopped(self) -> bool:
        return self._stopped

    # -- startup checks -------------------------------------------------------

    def _check_rule_pack(self, at_utc: datetime) -> ReadinessCheck:
        """Exactly one VALID pack must be in force, or the exact reason."""
        if self._packs is None or self._pack_identity is None:
            return ReadinessCheck(
                "rule_pack", False, DegradedReason.RULE_PACK_MISSING,
                "NO_RULE_PACK_STORE_OR_IDENTITY_CONFIGURED",
            )
        identity = self._pack_identity
        try:
            pack = self._packs.find_rule_pack(
                identity.provider,
                identity.program,
                identity.phase,
                identity.account_size,
                at_utc,
                currency=identity.currency,
                platform=identity.platform,
            )
        except Exception as exc:
            reason = (
                DegradedReason.RULE_PACK_AMBIGUOUS
                if "MULTIPLE_RULE_PACKS_MATCH" in str(exc)
                else DegradedReason.RULE_PACK_MISSING
            )
            return ReadinessCheck("rule_pack", False, reason, str(exc)[:200])
        if not getattr(pack, "is_usable", False):
            return ReadinessCheck(
                "rule_pack", False, DegradedReason.RULE_PACK_NOT_USABLE,
                f"PACK_STATUS={getattr(pack, 'status', 'UNKNOWN')}",
            )
        self._active_pack = pack
        self._status.active_rule_pack_id = pack.rule_pack_id
        return ReadinessCheck(
            "rule_pack", True, DegradedReason.NONE, pack.rule_pack_id
        )

    def _check_required_providers(self, at_utc: datetime) -> ReadinessCheck:
        """Every provider the active pack REQUIRES must be present and fresh.

        Absence is degraded with its exact reason; it never degrades silently to
        "no prop rules".
        """
        missing: list[str] = []
        for name in self._required_external:
            if name == "ECONOMIC_CALENDAR" and self._calendar is None:
                missing.append(name)
            elif name == "MARKET_SESSIONS" and self._sessions is None:
                missing.append(name)
        if missing:
            return ReadinessCheck(
                "external_providers", False,
                DegradedReason.REQUIRED_PROVIDER_UNAVAILABLE,
                ",".join(sorted(missing)),
            )
        return ReadinessCheck("external_providers", True)

    def _check_state_provider(self) -> ReadinessCheck:
        """A LIVE/SIMULATE deployment must have a governed 3B state provider.

        Without one, ``state_for`` returns ``None`` forever and EVERY
        position-reduction evaluation would be undecidable while startup still
        reported itself healthy. That is exactly the silent gap this check
        closes, so absence is reported as ``STATE_UNAVAILABLE`` and is never
        mistaken for ``PROP_MODE_DISABLED``.
        """
        if getattr(self, "state_provider", None) is None:
            return ReadinessCheck(
                "state_provider", False, DegradedReason.STATE_UNAVAILABLE,
                "NO_PRODUCTION_3B_STATE_PROVIDER_CONFIGURED",
            )
        return ReadinessCheck("state_provider", True)

    def _restore_durable_state(self, at_utc: datetime) -> None:
        """Rebuild suspension/kill state so a restart cannot clear a breach."""
        self._state_store.rebuild()
        for suspension in self._state_store.all_suspensions():
            if not suspension.is_active:
                continue
            self._status.account_states[suspension.account.account_id] = (
                "TERMINAL_SUSPENDED" if suspension.terminal else "SUSPENDED"
            )

    # -- external source status ----------------------------------------------

    def external_freshness(self, *, at_utc: datetime) -> dict[str, str]:
        """Current freshness label for each configured external source."""
        status: dict[str, str] = {}
        if self._calendar is not None:
            try:
                snapshot = self._calendar.fetch(
                    start_utc=at_utc, end_utc=at_utc
                )
                status["ECONOMIC_CALENDAR"] = snapshot.freshness.value
            except Exception as exc:
                status["ECONOMIC_CALENDAR"] = SourceFreshness.UNAVAILABLE.value
                logger.warning("[PROP_ENFORCEMENT] calendar unavailable: %s", type(exc).__name__)
        else:
            status["ECONOMIC_CALENDAR"] = SourceFreshness.UNAVAILABLE.value
        if self._sessions is not None:
            try:
                answer = self._sessions.state_for(
                    canonical_symbol="*", at_utc=at_utc
                )
                status["MARKET_SESSIONS"] = answer.freshness.value
            except Exception:
                status["MARKET_SESSIONS"] = SourceFreshness.UNAVAILABLE.value
        else:
            status["MARKET_SESSIONS"] = SourceFreshness.UNAVAILABLE.value
        self._status.external_sources = dict(status)
        return status

    # -- evaluation ----------------------------------------------------------

    def evaluate(
        self,
        *,
        account: AccountKey,
        state: AccountEvaluationState,
        at_utc: datetime,
        portfolio: Any = None,
    ) -> tuple[EvaluationResult, ...]:
        """Ask 3B to evaluate the active pack against the current state.

        3C performs no rule arithmetic of its own: this is the same
        ``evaluate_pack`` the historical evaluator uses.
        """
        if self._active_pack is None:
            raise EnforcementError("NO_ACTIVE_RULE_PACK")
        context = EvaluationContext(
            rule_pack=self._active_pack,
            state=state,
            evaluated_at_utc=at_utc,
            rule_day_definition=self._rule_day,
            # The mode is deliberately NOT part of evaluation lineage: a
            # SIMULATE_ONLY run must produce byte-identical evaluations and
            # decisions to a LIVE_ENFORCE run on the same evidence.
            state_lineage={"block": "3C_RUNTIME"},
            economic_calendar=self._calendar,
            market_sessions=self._sessions,
        )
        results = evaluate_pack(context)
        self._status.last_evaluated_at_utc = at_utc.isoformat()
        return results

    def _decisions(
        self,
        *,
        account: AccountKey,
        results: Sequence[EvaluationResult],
        at_utc: datetime,
        projected: bool = False,
    ) -> tuple[EnforceDecision, ...]:
        """Compile 3B results into enforcement decisions, in rule-id order."""
        by_id = {r.rule_id: r for r in (self._active_pack.rules if self._active_pack else ())}
        decisions: list[EnforceDecision] = []
        for result in sorted(results, key=lambda r: r.rule_id):
            decisions.append(
                compile_decision(
                    rule=by_id.get(result.rule_id),
                    result=result,
                    account=account,
                    effective_at_utc=at_utc,
                    projected=projected,
                )
            )
        return tuple(decisions)

    def _restored_blocking(self, account: AccountKey) -> tuple[EnforceDecision, ...]:
        """Synthesise decisions for suspensions/kill state restored on startup.

        A restored terminal breach must still BLOCK after a restart, even
        though the rule that caused it may not be re-breaching yet.
        """
        blocking: list[EnforceDecision] = []
        if self._state_store.kill_switch_tripped(account):
            blocking.append(
                self._restored_decision(
                    account,
                    EnforcementAction.KILL_SWITCH,
                    EnforcementReason.RESTORED_TERMINAL_SUSPENSION,
                    "KILL_SWITCH_TRIPPED",
                )
            )
        for suspension in self._state_store.suspensions_for(account):
            action = (
                EnforcementAction.SUSPEND_CHALLENGE
                if suspension.scope is SuspensionScope.CHALLENGE
                else EnforcementAction.SUSPEND_ACCOUNT
            )
            blocking.append(
                self._restored_decision(
                    account,
                    action,
                    EnforcementReason.RESTORED_ACTIVE_BLOCK,
                    f"SUSPENSION={suspension.suspension_id}:TERMINAL={suspension.terminal}",
                    rule_pack_id=suspension.rule_pack_id,
                    enforcement_id=suspension.triggering_enforcement_id,
                )
            )
        return tuple(blocking)

    def _restored_decision(
        self,
        account: AccountKey,
        action: EnforcementAction,
        reason: EnforcementReason,
        detail: str,
        *,
        rule_pack_id: str = "",
        enforcement_id: str = "",
    ) -> EnforceDecision:
        from core.risk.prop_rule_enforcement import derive_enforcement_id
        from core.risk.prop_rule_enums import RuleType
        from core.risk.prop_rule_evaluator import EvaluationStatus
        from core.risk.prop_rule_state import content_hash

        pack_id = rule_pack_id or self._status.active_rule_pack_id
        eid = enforcement_id or ("enf_" + content_hash(
            {"account": account.to_dict(), "restored": reason.value}
        )[:32])
        return EnforceDecision(
            enforcement_id=eid,
            rule_pack_id=pack_id,
            rule_id="restored.enforcement_state",
            rule_type=RuleType.UNKNOWN_EXTENSION,
            account=account,
            evaluation_id=eid,
            evaluated_status=EvaluationStatus.BREACH,
            enforcement_action=action,
            criticality=EnforcementCriticality.HARD_STOP,
            severity=EnforcementSeverity.TERMINAL,
            effective_at_utc=self._now(),
            applies_to=EnforcementScope.ACCOUNT,
            reason_code=reason,
            action_required=True,
            terminal=True,
            detail=detail,
        )

    def state_for(
        self,
        account: AccountKey,
        *,
        at_utc: datetime | None = None,
        evidence: Any = None,
    ) -> AccountEvaluationState | None:
        """Assemble the current 3B evaluation state for one exact account.

        The ONE production provider is
        :class:`~core.risk.prop_rule_state_provider.PropRuleStateProvider`. It is
        given the caller's evaluation instant and this cycle's 2A/2B/2C evidence
        so the state is coherent with the cycle that asked for it.

        Returning ``None`` is an explicit unavailability, never a fabricated
        compliant state. The entry gate then blocks NEW risk with the exact
        degraded reason and position enforcement performs NO close.
        """
        provider = getattr(self, "state_provider", None)
        if provider is None:
            return None
        try:
            if _provider_accepts_context(provider):
                return provider(account, at_utc=at_utc, evidence=evidence)
            # A minimal provider that takes only the account is still supported.
            return provider(account)
        except Exception as exc:
            logger.error(
                "[PROP_ENFORCEMENT] state provider failed for %s: %s",
                account.account_id, type(exc).__name__,
            )
            return None

    def _now(self) -> datetime:
        if self._clock is not None:
            return self._clock()
        return datetime.now(UTC)

    # -- THE ENTRY GATE ------------------------------------------------------

    def authorize_entry(
        self,
        *,
        account: AccountKey,
        at_utc: datetime,
        state: AccountEvaluationState | None = None,
        order: PlannedOrder | None = None,
        portfolio: Any = None,
        evidence: Any = None,
    ) -> EntryVerdict:
        """The authoritative may-this-account-trade answer for ONE position.

        ORDER OF OPERATIONS
        -------------------
        1. ``DISABLED`` mode  -> allow; prop enforcement is explicitly off.
        2. Stopped / no pack -> BLOCKED with the exact degraded reason.
        3. Evaluate the CURRENT state with 3B and compile decisions.
        4. Fold in suspensions and kill state restored from durable storage.
        5. When an order is supplied, project the post-fill state and evaluate
           THAT with the SAME 3B evaluators, so a trade that would itself create
           the breach is refused.
        6. Coalesce deterministically and answer.

        SIMULATE_ONLY runs this exact path and changes nothing.
        """
        symbol = order.canonical_symbol if order is not None else ""

        if self.mode is EnforcementMode.DISABLED:
            return EntryVerdict(
                account=account, symbol=symbol, allowed=True,
                reason=EnforcementReason.RULE_NOT_APPLICABLE,
                detail="PROP_MODE_DISABLED", mode=self.mode, evaluated_at_utc=at_utc,
                degraded_reason=DegradedReason.PROP_MODE_DISABLED,
            )

        if self._stopped:
            return EntryVerdict(
                account=account, symbol=symbol, allowed=False,
                reason=EnforcementReason.EVIDENCE_INDETERMINATE,
                detail="ENFORCEMENT_SERVICE_STOPPED", mode=self.mode,
                evaluated_at_utc=at_utc,
                degraded_reason=DegradedReason.STATE_UNAVAILABLE,
            )

        if self._active_pack is None:
            reason = (
                DegradedReason.RULE_PACK_MISSING
                if self._status.degraded_reason is DegradedReason.NONE
                else self._status.degraded_reason
            )
            return EntryVerdict(
                account=account, symbol=symbol, allowed=False,
                reason=EnforcementReason.NO_RULE_IN_FORCE,
                detail=self._status.degraded_detail or "NO_ACTIVE_RULE_PACK",
                mode=self.mode, evaluated_at_utc=at_utc, degraded_reason=reason,
            )

        if state is None:
            state = self.state_for(account, at_utc=at_utc, evidence=evidence)
        if state is None:
            return EntryVerdict(
                account=account, symbol=symbol, allowed=False,
                reason=EnforcementReason.EVIDENCE_INDETERMINATE,
                detail="PROP_3B_STATE_UNAVAILABLE", mode=self.mode,
                evaluated_at_utc=at_utc,
                degraded_reason=DegradedReason.STATE_UNAVAILABLE,
            )

        results = self.evaluate(
            account=account, state=state, at_utc=at_utc, portfolio=portfolio
        )
        decisions = list(self._decisions(
            account=account, results=results, at_utc=at_utc
        ))
        decisions.extend(self._restored_blocking(account))

        projected_decisions: tuple[EnforceDecision, ...] = ()
        projection: ExposureProjection | None = None
        if order is not None:
            projection = project_exposure(
                state=state, order=order,
                correlation_model=self._model, portfolio=portfolio,
            )
            projected_state = build_projected_state(
                state=state, order=order, projection=projection,
                observed_at_utc=at_utc,
            )
            projected_results = self.evaluate(
                account=account, state=projected_state, at_utc=at_utc,
                portfolio=portfolio,
            )
            projected_decisions = self._decisions(
                account=account, results=projected_results, at_utc=at_utc,
                projected=True,
            )

        current = coalesce_decisions(
            decisions, account=account, effective_at_utc=at_utc
        )
        projected = coalesce_decisions(
            projected_decisions, account=account, effective_at_utc=at_utc
        )
        blocking = list(current.blocks(symbol))
        blocking.extend(projected.blocks(symbol))
        reductions = list(current.position_actions())
        reductions.extend(projected.position_actions())
        blocking = sorted(set(blocking), key=lambda d: (d.precedence, d.enforcement_id))
        verdict = self._resolve(
            account=account,
            symbol=symbol,
            at_utc=at_utc,
            current=current,
            projected_effective=projected,
            blocking=tuple(blocking),
            reductions=tuple(reductions),
            decisions=tuple(decisions),
            projected_decisions=projected_decisions,
            projection=projection,
        )
        self._persist(verdict)
        return verdict

    def _resolve(
        self,
        *,
        account: AccountKey,
        symbol: str,
        at_utc: datetime,
        current: EffectiveEnforcement,
        projected_effective: EffectiveEnforcement,
        blocking: tuple[EnforceDecision, ...],
        reductions: tuple[EnforceDecision, ...],
        decisions: tuple[EnforceDecision, ...],
        projected_decisions: tuple[EnforceDecision, ...],
        projection: ExposureProjection | None,
    ) -> EntryVerdict:
        """Collapse the two coalesced sets into ONE explicit verdict."""
        allowed = not blocking
        if allowed:
            reason = EnforcementReason.WITHIN_LIMIT
            detail = (
                f"PROJECTED_OK:{projection.to_dict()['total_open_risk']}"
                if projection is not None
                else "CURRENT_STATE_COMPLIANT"
            )
        else:
            head = blocking[0]
            reason = head.reason_code
            detail = ",".join(
                sorted({f"{d.rule_id}:{d.enforcement_action.value}" for d in blocking})
            )[:400]
        return EntryVerdict(
            account=account,
            symbol=symbol,
            allowed=allowed,
            reason=reason,
            detail=detail,
            mode=self.mode,
            evaluated_at_utc=at_utc,
            rule_pack_id=self._status.active_rule_pack_id,
            decisions=decisions,
            blocking=tuple(blocking),
            position_reductions=tuple(sorted(
                set(reductions), key=lambda d: (d.precedence, d.enforcement_id)
            )),
            projected_decisions=projected_decisions,
            projected_state_complete=(projection.is_complete if projection else True),
            projected_unavailable_fields=(
                projection.unavailable_fields if projection else ()
            ),
        )

    def _persist(self, verdict: EntryVerdict) -> None:
        """Persist every MATERIAL decision, honouring the mode.

        In ``SIMULATE_ONLY`` the identical decisions are recorded with mode
        ``SIMULATE_ONLY`` and NOTHING is changed. In ``LIVE_ENFORCE`` a blocking
        suspension/kill decision is also applied to durable state.
        """
        mode_label = self.mode.value
        for decision in verdict.blocking or verdict.decisions:
            if not decision.action_required:
                continue
            record = EnforcementAuditRecord.from_decision(
                decision=decision, mode=mode_label,
                recorded_at_utc=decision.effective_at_utc,
            )
            self._state_store.record_audit(record)
        if verdict.blocking:
            self._status.last_enforcement_id = verdict.blocking[0].enforcement_id
        self._status.last_block_reason = verdict.block_reason_text
        self._status.mode = self.mode

        if self.mode is not EnforcementMode.LIVE_ENFORCE:
            return
        for decision in verdict.blocking:
            action = decision.enforcement_action
            if action is EnforcementAction.SUSPEND_ACCOUNT:
                self._executor.suspend(decision, at_utc=decision.effective_at_utc)
            elif action is EnforcementAction.SUSPEND_CHALLENGE:
                self._executor.suspend(
                    decision, at_utc=decision.effective_at_utc,
                    scope=SuspensionScope.CHALLENGE,
                )
            elif action is EnforcementAction.KILL_SWITCH:
                self._executor.trigger_kill_switch(
                    decision, at_utc=decision.effective_at_utc,
                    locked=bool(decision.terminal),
                )
            elif action in (
                EnforcementAction.BLOCK_ALL_ENTRIES,
                EnforcementAction.BLOCK_ACCOUNT_ENTRY,
            ) and decision.terminal:
                # A terminal HARD STOP does three things durably: it suspends the
                # account, and it LOCKS the kill switch. The lock is what makes a
                # process restart unable to clear the breach.
                self._executor.suspend(decision, at_utc=decision.effective_at_utc)
                self._executor.trigger_kill_switch(
                    decision, at_utc=decision.effective_at_utc, locked=True
                )

    # -- position enforcement (cycle boundary) --------------------------------

    def enforce_positions(
        self,
        *,
        account: AccountKey,
        state: AccountEvaluationState | None = None,
        at_utc: datetime,
        open_tickets: Any = (),
        ticket_symbols: Any = None,
        portfolio: Any = None,
        evidence: Any = None,
    ) -> tuple[CloseResult, ...]:
        """Run the position-reduction actions the current state demands.

        Called at a bounded cycle boundary AFTER a material state change. It
        returns the exact per-ticket results; it never invents a close the rules
        did not demand, and it is a no-op unless a POSITION_REDUCTION action
        fired.

        THE THREE MODES
        ----------------
        ``LIVE_ENFORCE``  the demanded close is executed through the injected
                          :class:`PositionClosePort` and the truthful outcome is
                          recorded in the enforcement audit.
        ``SIMULATE_ONLY`` the IDENTICAL decisions are compiled and recorded with
                          mode ``SIMULATE_ONLY`` and action result
                          ``SKIPPED_SIMULATE_ONLY``. No broker is contacted and
                          no durable close is recorded, so a later LIVE cycle
                          still performs the close exactly once.
        ``DISABLED``      nothing is evaluated and nothing is recorded.

        INDETERMINATE EVIDENCE NEVER LIQUIDATES
        ---------------------------------------
        A missing rule pack, a stopped service or an unavailable 3B state all
        return no results. Refusing to add NEW risk in that situation is the
        entry gate's job; forcing EXISTING risk out is a different decision and
        is only ever taken when a governed rule explicitly demanded it.

        ``state`` is optional: when omitted the runtime resolves it through the
        existing :meth:`state_for` seam, so a cycle adapter never has to build
        3B state itself.
        """
        if self.mode is EnforcementMode.DISABLED:
            return ()
        if self._stopped:
            logger.error(
                "[PROP_ENFORCEMENT] service stopped - no position action account=%s",
                account.account_id,
            )
            return ()
        if self._active_pack is None:
            logger.error(
                "[PROP_ENFORCEMENT] no active rule pack - no position action account=%s",
                account.account_id,
            )
            return ()
        if state is None:
            state = self.state_for(account, at_utc=at_utc, evidence=evidence)
        if state is None:
            # Indeterminate evidence blocks NEW risk (the entry gate's job) but
            # must NEVER liquidate existing risk on its own: forcing EXISTING
            # positions out is only ever taken when a governed rule demanded it.
            logger.warning(
                "[PROP_ENFORCEMENT] 3B state unavailable - no position action account=%s",
                account.account_id,
            )
            return ()

        evaluations = self.evaluate(
            account=account, state=state, at_utc=at_utc, portfolio=portfolio
        )
        decisions = self._decisions(
            account=account, results=evaluations, at_utc=at_utc
        )
        effective = coalesce_decisions(
            decisions, account=account, effective_at_utc=at_utc
        )
        symbols = {int(k): str(v) for k, v in dict(ticket_symbols or {}).items()}

        results: list[CloseResult] = []
        for decision in effective.position_actions():
            tickets = self._governed_tickets(
                decision, open_tickets=open_tickets, symbols=symbols
            )
            if not tickets:
                continue
            if self.mode is EnforcementMode.SIMULATE_ONLY:
                self._record_position_outcome(decision, (), at_utc=at_utc, simulated=True)
                continue
            for ticket in tickets:
                results.append(
                    self._executor.close_position(
                        decision, ticket=int(ticket), at_utc=at_utc,
                        canonical_symbol=symbols.get(int(ticket), ""),
                    )
                )
            self._record_position_outcome(
                decision, results, at_utc=at_utc, simulated=False
            )
        return tuple(results)

    @staticmethod
    def _governed_tickets(
        decision: EnforceDecision,
        *,
        open_tickets: Any,
        symbols: Mapping[int, str],
    ) -> tuple[int, ...]:
        """The exact tickets ONE position-reduction decision governs.

        A ticket-scoped decision names its own ticket. A symbol- or
        account-scoped liquidation covers only tickets this account PROVED open,
        and only those whose canonical symbol is in scope: a ticket whose symbol
        is unknown is never swept into a liquidation by accident.
        """
        if decision.enforcement_action is EnforcementAction.CLOSE_POSITION:
            ticket = decision.position_ticket
            return (int(ticket),) if ticket is not None else ()
        scope = {str(s).upper() for s in decision.symbols}
        proven = tuple(int(t) for t in open_tickets)
        if not scope:
            return proven
        return tuple(t for t in proven if str(symbols.get(t, "")).upper() in scope)

    def _record_position_outcome(
        self,
        decision: EnforceDecision,
        results: Sequence[CloseResult],
        *,
        at_utc: datetime,
        simulated: bool,
    ) -> None:
        """Persist ONE truthful audit record for this decision.

        The enforcement audit record identity deliberately excludes the ticket and
        the action result, so a multi-ticket liquidation is ONE decision and is
        recorded once with the aggregate outcome rather than once per ticket.
        """
        if simulated:
            marked = decision.with_attempt(
                attempted=False,
                result=ActionResultStatus.SKIPPED_SIMULATE_ONLY,
                retryable=False,
                terminal=False,
                detail="SIMULATE_ONLY:NO_BROKER_ACTION",
            )
        elif not results:
            return
        else:
            marked = decision.with_attempt(
                attempted=True,
                result=_aggregate_action_result(results),
                retryable=any(r.is_retryable for r in results),
                terminal=(
                    not all(r.is_success for r in results)
                    and not any(r.is_retryable for r in results)
                ),
                detail="TICKETS=" + ",".join(
                    f"{r.request.position_ticket}:{r.outcome.value}" for r in results
                )[:300],
            )
        self._state_store.record_audit(
            EnforcementAuditRecord.from_decision(
                decision=marked, mode=self.mode.value, recorded_at_utc=at_utc,
            )
        )

    def status(self) -> PropEnforcementRuntimeStatus:
        """Inspectable enforcement status. No dashboard required."""
        return self._status


# =============================================================================
# PROCESS-WIDE SINGLETON ? the ONE managed service
# =============================================================================

_RUNTIME: "PropEnforcementRuntime | None" = None


def configure_prop_enforcement_runtime(
    runtime: "PropEnforcementRuntime | None",
) -> "PropEnforcementRuntime | None":
    """Install (or clear) the process-wide enforcement runtime.

    Exactly one runtime exists per process, mirroring the Block 2 telemetry
    service pattern: one managed service, never one per rule, per account or per
    provider.
    """
    global _RUNTIME
    _RUNTIME = runtime
    return _RUNTIME


def prop_enforcement_runtime() -> "PropEnforcementRuntime | None":
    """The configured runtime, or ``None`` when prop mode is not wired."""
    return _RUNTIME


def start_prop_enforcement_runtime(
    *,
    at_utc: datetime,
    mode: EnforcementMode = EnforcementMode.DISABLED,
    **kwargs: Any,
) -> tuple["PropEnforcementRuntime | None", ReadinessReport]:
    """Build, configure and START the runtime. Idempotent per process."""
    if prop_enforcement_runtime() is not None:
        existing = prop_enforcement_runtime()
        assert existing is not None
        return existing, ReadinessReport(True, existing.mode)
    runtime = PropEnforcementRuntime(mode=mode, **kwargs)
    report = runtime.start(at_utc=at_utc)
    configure_prop_enforcement_runtime(runtime)
    return runtime, report


def stop_prop_enforcement_runtime() -> bool:
    """Stop the runtime cleanly. Returns True when stopped or already stopped."""
    runtime = prop_enforcement_runtime()
    if runtime is None:
        return True
    runtime.stop()
    configure_prop_enforcement_runtime(None)
    return True


def prop_enforcement_status() -> dict[str, Any]:
    """Inspectable enforcement status as a plain dict. No dashboard needed."""
    runtime = prop_enforcement_runtime()
    if runtime is None:
        return {
            "mode": EnforcementMode.DISABLED.value,
            "ready": False,
            "degraded_reason": DegradedReason.NONE.value,
            "degraded_detail": "PROP_ENFORCEMENT_RUNTIME_NOT_CONFIGURED",
            "external_sources": {},
            "account_states": {},
        }
    return runtime.status().to_dict()
