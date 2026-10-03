"""Governed RUNTIME PROP RULE ENFORCEMENT authority (Block 3C).

Block 3A owns the immutable rule contract. Block 3B owns the deterministic,
side-effect-free evaluation. This module owns the THIRD question:

    given the exact active RulePack and the exact current evaluation state,
    what is the runtime PERMITTED or REQUIRED to do?

It converts 3B :class:`EvaluationResult` objects into immutable, account-scoped
:class:`EnforceDecision` records and resolves them into one deterministic
effective decision set.

WHAT THIS MODULE IS NOT
-----------------------
* It does not evaluate rules. Every status here is CONSUMED from 3B.
* It performs NO arithmetic on daily loss, drawdown, high-water, trading days,
  open risk or consistency ratios. :data:`FORBIDDEN_OPERATIONS` is the
  machine-checkable statement of that boundary.
* It does not import MetaTrader5 and never calls a broker.
* It reads no wall clock for a decision. The instant is injected.

DETERMINISM
-----------
:func:`derive_enforcement_id` hashes the rule pack, the rule, the exact account
identity, the source ``evaluation_id``, the action and the scope. The same
evaluation demanding the same action therefore yields the SAME enforcement id on
every cycle, which is what makes the runtime idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass, fields as dataclass_fields, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from core.risk.prop_rule_enums import RuleSeverity, RuleType
from core.risk.prop_rule_evaluator import EvaluationResult, EvaluationStatus
from core.risk.prop_rule_state import AccountKey, content_hash


class EnforcementError(RuntimeError):
    """The enforcement contract was violated (fail closed, never repair)."""


class EnforcementAction(str, Enum):
    """What the runtime is permitted or required to do.

    Each value means EXACTLY one thing. An action is never overloaded to mean
    "block, and also close", and never used to mean two different things in two
    different criticality classes.
    """

    ALLOW = "ALLOW"
    NO_ACTION = "NO_ACTION"
    BLOCK_NEW_ENTRY = "BLOCK_NEW_ENTRY"
    BLOCK_SYMBOL_ENTRY = "BLOCK_SYMBOL_ENTRY"
    BLOCK_ACCOUNT_ENTRY = "BLOCK_ACCOUNT_ENTRY"
    BLOCK_ALL_ENTRIES = "BLOCK_ALL_ENTRIES"
    CLOSE_POSITION = "CLOSE_POSITION"
    CLOSE_SYMBOL_POSITIONS = "CLOSE_SYMBOL_POSITIONS"
    CLOSE_ACCOUNT_POSITIONS = "CLOSE_ACCOUNT_POSITIONS"
    SUSPEND_ACCOUNT = "SUSPEND_ACCOUNT"
    SUSPEND_CHALLENGE = "SUSPEND_CHALLENGE"
    KILL_SWITCH = "KILL_SWITCH"
    REQUIRES_EXTERNAL_SOURCE = "REQUIRES_EXTERNAL_SOURCE"
    INDETERMINATE_BLOCK = "INDETERMINATE_BLOCK"


#: Deterministic action precedence, MOST restrictive first.
#:
#: Rule iteration order can never change behaviour: the effective action is the
#: highest-precedence action present, ties broken by scope breadth and then by a
#: stable id. See :func:`coalesce_decisions`.
ACTION_PRECEDENCE: tuple[EnforcementAction, ...] = (
    EnforcementAction.KILL_SWITCH,
    EnforcementAction.SUSPEND_CHALLENGE,
    EnforcementAction.SUSPEND_ACCOUNT,
    EnforcementAction.CLOSE_POSITION,
    EnforcementAction.CLOSE_SYMBOL_POSITIONS,
    EnforcementAction.CLOSE_ACCOUNT_POSITIONS,
    EnforcementAction.BLOCK_ALL_ENTRIES,
    EnforcementAction.BLOCK_ACCOUNT_ENTRY,
    EnforcementAction.BLOCK_SYMBOL_ENTRY,
    EnforcementAction.BLOCK_NEW_ENTRY,
    EnforcementAction.INDETERMINATE_BLOCK,
    EnforcementAction.REQUIRES_EXTERNAL_SOURCE,
    EnforcementAction.ALLOW,
    EnforcementAction.NO_ACTION,
)

#: Rank used for ordering. Lower rank == more restrictive == applied first.
ACTION_RANK: Mapping[EnforcementAction, int] = {
    action: index for index, action in enumerate(ACTION_PRECEDENCE)
}

#: Actions that prevent a NEW position from being opened.
ENTRY_BLOCKING_ACTIONS: frozenset[EnforcementAction] = frozenset(
    {
        EnforcementAction.KILL_SWITCH,
        EnforcementAction.SUSPEND_CHALLENGE,
        EnforcementAction.SUSPEND_ACCOUNT,
        EnforcementAction.BLOCK_NEW_ENTRY,
        EnforcementAction.BLOCK_SYMBOL_ENTRY,
        EnforcementAction.BLOCK_ACCOUNT_ENTRY,
        EnforcementAction.BLOCK_ALL_ENTRIES,
        EnforcementAction.INDETERMINATE_BLOCK,
        EnforcementAction.REQUIRES_EXTERNAL_SOURCE,
    }
)

#: Actions that require reducing or eliminating EXISTING exposure.
POSITION_REDUCTION_ACTIONS: frozenset[EnforcementAction] = frozenset(
    {
        EnforcementAction.CLOSE_POSITION,
        EnforcementAction.CLOSE_SYMBOL_POSITIONS,
        EnforcementAction.CLOSE_ACCOUNT_POSITIONS,
    }
)

#: Actions that halt the account/challenge itself.
HALT_ACTIONS: frozenset[EnforcementAction] = frozenset(
    {
        EnforcementAction.SUSPEND_ACCOUNT,
        EnforcementAction.SUSPEND_CHALLENGE,
        EnforcementAction.KILL_SWITCH,
    }
)


class EnforcementScope(str, Enum):
    """What the decision is scoped to.

    Scope is what makes a per-symbol rule different from an account-wide rule,
    and it is carried explicitly instead of implied by free text.
    """

    ACCOUNT = "ACCOUNT"
    SYMBOL = "SYMBOL"
    SYMBOL_SET = "SYMBOL_SET"
    POSITION = "POSITION"
    CORRELATION_CLUSTER = "CORRELATION_CLUSTER"
    DIRECTION = "DIRECTION"
    CHALLENGE = "CHALLENGE"


class EnforcementCriticality(str, Enum):
    """How much runtime authority a rule carries.

    Criticality is a property of the RULE CONTRACT (3A), declared once, and is
    what decides whether an undecidable evaluation fails closed.
    """

    HARD_STOP = "HARD_STOP"
    ENTRY_BLOCK = "ENTRY_BLOCK"
    POSITION_REDUCTION = "POSITION_REDUCTION"
    ACCOUNT_SUSPEND = "ACCOUNT_SUSPEND"
    ADVISORY = "ADVISORY"
    EXTERNAL_DEPENDENCY = "EXTERNAL_DEPENDENCY"


class EnforcementSeverity(str, Enum):
    """Consequence the runtime attaches to executing this decision."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    TERMINAL = "TERMINAL"


class ActionResultStatus(str, Enum):
    """Outcome of ATTEMPTING an enforcement action.

    Kept strictly separate from the rule status: "the rule is breached" and "our
    attempt to act on it failed" are two different facts and both stay visible.
    """

    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    SUCCESS = "SUCCESS"
    ALREADY_CLOSED = "ALREADY_CLOSED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    SKIPPED_SIMULATE_ONLY = "SKIPPED_SIMULATE_ONLY"
    SKIPPED_NOT_REQUIRED = "SKIPPED_NOT_REQUIRED"


class RecoveryClass(str, Enum):
    """Whether and how an enforcement state may clear itself.

    A terminal breach never clears on restart. A rule-day-scoped block clears
    only on a genuine governed rule-day boundary. Nothing is ever "reset all".
    """

    MANUAL = "MANUAL"
    RULE_DAY_RESET = "RULE_DAY_RESET"
    ON_RULE_PASS = "ON_RULE_PASS"
    EXTERNAL_WINDOW = "EXTERNAL_WINDOW"


class EnforcementMode(str, Enum):
    """How the runtime treats the decisions it computes."""

    LIVE_ENFORCE = "LIVE_ENFORCE"
    SIMULATE_ONLY = "SIMULATE_ONLY"
    DISABLED = "DISABLED"


class DegradedReason(str, Enum):
    """Exactly why enforcement is not at full compliance readiness."""

    NONE = "NONE"
    PROP_MODE_DISABLED = "PROP_MODE_DISABLED"
    RULE_PACK_MISSING = "RULE_PACK_MISSING"
    RULE_PACK_AMBIGUOUS = "RULE_PACK_AMBIGUOUS"
    RULE_PACK_NOT_USABLE = "RULE_PACK_NOT_USABLE"
    STATE_UNAVAILABLE = "STATE_UNAVAILABLE"
    REQUIRED_PROVIDER_UNAVAILABLE = "REQUIRED_PROVIDER_UNAVAILABLE"


class EnforcementReason(str, Enum):
    """Machine-readable reason codes. Never free text."""

    WITHIN_LIMIT = "WITHIN_LIMIT"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    TARGET_MET = "TARGET_MET"
    REQUIREMENT_NOT_MET = "REQUIREMENT_NOT_MET"
    RULE_NOT_APPLICABLE = "RULE_NOT_APPLICABLE"
    RULE_DISABLED = "RULE_DISABLED"
    RULE_NOT_EFFECTIVE = "RULE_NOT_EFFECTIVE"
    EVIDENCE_INDETERMINATE = "EVIDENCE_INDETERMINATE"
    EXTERNAL_SOURCE_UNAVAILABLE = "EXTERNAL_SOURCE_UNAVAILABLE"
    EXTERNAL_SOURCE_STALE = "EXTERNAL_SOURCE_STALE"
    RULE_TYPE_UNSUPPORTED = "RULE_TYPE_UNSUPPORTED"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    CONVERSION_SOURCE_UNAVAILABLE = "CONVERSION_SOURCE_UNAVAILABLE"
    STATE_CONFLICT = "STATE_CONFLICT"
    STATE_STALE = "STATE_STALE"
    STATE_FIELD_UNAVAILABLE = "STATE_FIELD_UNAVAILABLE"
    PROJECTED_BREACH = "PROJECTED_BREACH"
    NO_RULE_IN_FORCE = "NO_RULE_IN_FORCE"
    RESTORED_TERMINAL_SUSPENSION = "RESTORED_TERMINAL_SUSPENSION"
    RESTORED_ACTIVE_BLOCK = "RESTORED_ACTIVE_BLOCK"
    PENDING_RETRYABLE_ACTION = "PENDING_RETRYABLE_ACTION"


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# CRITICALITY MODEL â€” rule type -> enforcement criticality
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

RULE_CRITICALITY: Mapping[RuleType, EnforcementCriticality] = {
    # Hard stops: crossing these ends the challenge or the trading day.
    RuleType.DAILY_LOSS_LIMIT: EnforcementCriticality.HARD_STOP,
    RuleType.MAX_DRAWDOWN: EnforcementCriticality.HARD_STOP,
    RuleType.STATIC_DRAWDOWN: EnforcementCriticality.HARD_STOP,
    RuleType.TRAILING_DRAWDOWN: EnforcementCriticality.HARD_STOP,
    # Entry blocks: adding new exposure is unsafe; existing exposure is not
    # itself a violation of these rules.
    RuleType.DAILY_PROFIT_LIMIT: EnforcementCriticality.ENTRY_BLOCK,
    RuleType.MAX_POSITION_SIZE: EnforcementCriticality.ENTRY_BLOCK,
    RuleType.MAX_OPEN_POSITIONS: EnforcementCriticality.ENTRY_BLOCK,
    RuleType.MAX_OPEN_RISK: EnforcementCriticality.ENTRY_BLOCK,
    RuleType.MAX_RISK_PER_POSITION: EnforcementCriticality.ENTRY_BLOCK,
    RuleType.MAX_RISK_PER_SYMBOL: EnforcementCriticality.ENTRY_BLOCK,
    RuleType.MAX_CORRELATED_RISK: EnforcementCriticality.ENTRY_BLOCK,
    RuleType.MAX_DIRECTIONAL_RISK: EnforcementCriticality.ENTRY_BLOCK,
    # Position reduction: only ever when the exact rule contract says so.
    RuleType.WEEKEND_HOLD_RESTRICTION: EnforcementCriticality.POSITION_REDUCTION,
    RuleType.OVERNIGHT_HOLD_RESTRICTION: EnforcementCriticality.POSITION_REDUCTION,
    # Account suspension until an explicit clearance authority acts.
    RuleType.MAX_TRADING_DAYS: EnforcementCriticality.ACCOUNT_SUSPEND,
    RuleType.INACTIVITY_RULE: EnforcementCriticality.ACCOUNT_SUSPEND,
    # External dependencies: evidence this system does not own.
    RuleType.NEWS_TRADING_RESTRICTION: EnforcementCriticality.EXTERNAL_DEPENDENCY,
    RuleType.IP_DEVICE_LOCATION_RESTRICTION: EnforcementCriticality.EXTERNAL_DEPENDENCY,
    RuleType.EA_AUTOMATION_PERMISSION: EnforcementCriticality.EXTERNAL_DEPENDENCY,
    RuleType.COPY_TRADING_RESTRICTION: EnforcementCriticality.EXTERNAL_DEPENDENCY,
    RuleType.REFUND_RULE: EnforcementCriticality.EXTERNAL_DEPENDENCY,
    RuleType.RESET_RULE: EnforcementCriticality.EXTERNAL_DEPENDENCY,
    RuleType.PAYOUT_ELIGIBILITY: EnforcementCriticality.EXTERNAL_DEPENDENCY,
    RuleType.PAYOUT_FREQUENCY: EnforcementCriticality.EXTERNAL_DEPENDENCY,
    RuleType.MIN_PROFIT_FOR_PAYOUT: EnforcementCriticality.EXTERNAL_DEPENDENCY,
    # Eligibility / completion, NOT risk breaches. Reaching a profit target is
    # never automatically a stop, and a minimum day count never blocks entry.
    RuleType.PROFIT_TARGET: EnforcementCriticality.ADVISORY,
    RuleType.MIN_TRADING_DAYS: EnforcementCriticality.ADVISORY,
    RuleType.CONSISTENCY_RULE: EnforcementCriticality.ADVISORY,
    RuleType.UNKNOWN_EXTENSION: EnforcementCriticality.ADVISORY,
}

#: Criticalities whose whole purpose is a source this system does not own.
EXTERNAL_CRITICALITIES: frozenset[EnforcementCriticality] = frozenset(
    {EnforcementCriticality.EXTERNAL_DEPENDENCY}
)

#: Criticalities where an undecidable evaluation must prevent NEW exposure.
#:
#: NOTE: none of these authorise liquidating existing positions. Refusing to ADD
#: risk and forcing existing risk OUT are different decisions, and only the
#: latter ever closes a position.
FAIL_CLOSED_CRITICALITIES: frozenset[EnforcementCriticality] = frozenset(
    {
        EnforcementCriticality.HARD_STOP,
        EnforcementCriticality.ENTRY_BLOCK,
        EnforcementCriticality.POSITION_REDUCTION,
        EnforcementCriticality.ACCOUNT_SUSPEND,
    }
)

#: Criticalities where an undecidable evaluation is merely recorded.
ADVISORY_CRITICALITY: frozenset[EnforcementCriticality] = frozenset(
    {EnforcementCriticality.ADVISORY}
)


def criticality_for(rule_type: RuleType) -> EnforcementCriticality:
    """The declared criticality of a rule type.

    An unmodelled rule type is ADVISORY by default rather than silently
    escalating to a hard stop; 3A's own conflict detection is what surfaces a
    rule the model does not understand.
    """
    return RULE_CRITICALITY.get(rule_type, EnforcementCriticality.ADVISORY)


def severity_from_rule(rule_severity: RuleSeverity | None) -> EnforcementSeverity:
    """Map 3A's declared severity onto the runtime severity vocabulary."""
    if rule_severity is RuleSeverity.TERMINATION:
        return EnforcementSeverity.TERMINAL
    if rule_severity is RuleSeverity.BREACH:
        return EnforcementSeverity.CRITICAL
    if rule_severity is RuleSeverity.WARNING:
        return EnforcementSeverity.WARNING
    return EnforcementSeverity.INFO


def is_mandatory_for_safe_operation(
    rule_type: RuleType, criticality: EnforcementCriticality | None = None
) -> bool:
    """Whether an UNSUPPORTED evaluation of this rule must fail closed.

    A rule the pack declares with a non-advisory criticality is mandatory for
    safe operation: if this system cannot evaluate it, it cannot prove the
    account is safe, so new exposure is refused.
    """
    resolved = criticality or criticality_for(rule_type)
    return resolved in FAIL_CLOSED_CRITICALITIES


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# ENFORCEMENT DECISION â€” the immutable runtime record
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


@dataclass(frozen=True)
class EnforceDecision:
    """One immutable, account-scoped runtime enforcement decision.

    IDENTITY
    --------
    ``enforcement_id`` is derived, never generated. The same 3B evaluation
    demanding the same action for the same account and scope ALWAYS produces the
    same id, which is what makes repeated runtime cycles idempotent.

    NO LOOSE BOOLEANS
    -----------------
    ``action_required`` / ``action_attempted`` / ``action_result`` are separate
    typed fields. ``retryable`` and ``terminal`` describe the OUTCOME class, not
    the rule verdict. A BREACH whose close attempt failed is still a BREACH.
    """

    enforcement_id: str
    rule_pack_id: str
    rule_id: str
    rule_type: RuleType
    account: AccountKey
    evaluation_id: str
    evaluated_status: EvaluationStatus
    enforcement_action: EnforcementAction
    criticality: EnforcementCriticality
    severity: EnforcementSeverity
    effective_at_utc: datetime
    applies_to: EnforcementScope
    reason_code: EnforcementReason
    symbols: tuple[str, ...] = ()
    position_ticket: int | None = None
    direction: str = ""
    correlation_cluster: str = ""
    #: The exact 3B evidence lineage this decision was derived from.
    evidence_used: tuple[str, ...] = ()
    source_evaluation_ids: tuple[str, ...] = ()
    #: True when derived from a PROJECTED post-trade state rather than current.
    projected: bool = False
    current_value: float | None = None
    limit_value: float | None = None
    recovery: RecoveryClass = RecoveryClass.ON_RULE_PASS
    action_required: bool = False
    action_attempted: bool = False
    action_result: ActionResultStatus = ActionResultStatus.NOT_ATTEMPTED
    retryable: bool = False
    terminal: bool = False
    fail_closed_reason: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.enforcement_id:
            raise EnforcementError("ENFORCEMENT_DECISION_REQUIRES_ID")
        if not isinstance(self.account, AccountKey):
            raise EnforcementError("ENFORCEMENT_DECISION_REQUIRES_ACCOUNT_KEY")
        if self.effective_at_utc.tzinfo is None:
            raise EnforcementError("ENFORCEMENT_DECISION_REQUIRES_AWARE_INSTANT")
        if not isinstance(self.evaluated_status, EvaluationStatus):
            raise EnforcementError("ENFORCEMENT_DECISION_REQUIRES_EVALUATION_STATUS")
        if not isinstance(self.enforcement_action, EnforcementAction):
            raise EnforcementError("ENFORCEMENT_DECISION_REQUIRES_ACTION")
        if not isinstance(self.applies_to, EnforcementScope):
            raise EnforcementError("ENFORCEMENT_DECISION_REQUIRES_SCOPE")
        object.__setattr__(self, "symbols", tuple(self.symbols))
        object.__setattr__(self, "evidence_used", tuple(self.evidence_used))
        object.__setattr__(self, "source_evaluation_ids", tuple(self.source_evaluation_ids))
        if self.enforcement_action in ENTRY_BLOCKING_ACTIONS and not self.action_required:
            # A blocking action that does nothing would be a silent pass.
            raise EnforcementError(
                f"BLOCKING_ACTION_REQUIRES_ACTION:{self.enforcement_action.value}"
            )

    # â”€â”€ account-safe identity helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    @property
    def account_id(self) -> str:
        return self.account.account_id

    @property
    def account_identity(self) -> tuple[str, str, str, int]:
        """The FULL broker/server/login identity.

        The same ticket number on a different account is a different position,
        so this tuple â€” not the ticket alone â€” is what binds a decision.
        """
        return self.account.identity

    @property
    def is_entry_blocking(self) -> bool:
        return self.enforcement_action in ENTRY_BLOCKING_ACTIONS

    @property
    def is_position_reduction(self) -> bool:
        return self.enforcement_action in POSITION_REDUCTION_ACTIONS

    @property
    def is_halt(self) -> bool:
        return self.enforcement_action in HALT_ACTIONS

    @property
    def precedence(self) -> int:
        return ACTION_RANK[self.enforcement_action]

    def covers_symbol(self, symbol: str | None) -> bool:
        """Whether this decision applies to a concrete symbol."""
        if not self.is_entry_blocking:
            return False
        if self.applies_to in (EnforcementScope.ACCOUNT, EnforcementScope.CHALLENGE):
            return True
        if self.applies_to in (EnforcementScope.SYMBOL, EnforcementScope.SYMBOL_SET):
            return symbol is not None and str(symbol).upper() in {
                s.upper() for s in self.symbols
            }
        # Cluster / direction scopes cannot be decided from a bare symbol, so a
        # symbol query is answered conservatively: it IS covered.
        return True

    def covers_ticket(self, ticket: int | None) -> bool:
        """Whether this decision applies to a concrete ticket."""
        if not self.is_entry_blocking:
            return False
        if self.applies_to in (EnforcementScope.ACCOUNT, EnforcementScope.CHALLENGE):
            return True
        if self.applies_to is EnforcementScope.POSITION:
            return ticket is not None and int(ticket) == int(self.position_ticket)
        return True

    def with_attempt(
        self,
        *,
        attempted: bool,
        result: ActionResultStatus,
        retryable: bool,
        terminal: bool,
        detail: str = "",
    ) -> "EnforceDecision":
        """Record the OUTCOME of acting, without rewriting the rule verdict.

        The rule status, action and identity are preserved verbatim: a failed
        close attempt must never rewrite "the rule is breached".
        """
        return replace(
            self,
            action_attempted=attempted,
            action_result=result,
            retryable=bool(retryable),
            terminal=bool(terminal),
            detail=detail or self.detail,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            f.name: getattr(self, f.name) for f in dataclass_fields(self)
        }
        payload["account"] = self.account.to_dict()
        payload["account_id"] = self.account.account_id
        payload["rule_type"] = self.rule_type.value
        payload["evaluated_status"] = self.evaluated_status.value
        payload["enforcement_action"] = self.enforcement_action.value


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# DETERMINISTIC ENFORCEMENT IDENTITY
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def derive_enforcement_id(
    *,
    rule_pack_id: str,
    rule_id: str,
    account: AccountKey,
    evaluation_id: str,
    action: EnforcementAction,
    scope: EnforcementScope,
    symbols: tuple[str, ...] = (),
    position_ticket: int | None = None,
    projected: bool = False,
) -> str:
    """Deterministic identity of ONE enforcement decision.

    The instant is deliberately NOT part of the id. Two cycles evaluating the
    same rule against the same evidence and demanding the same action must
    yield the SAME id, otherwise every poll would mint a fresh "new" decision
    and idempotency would be impossible. Material evidence change is already
    captured upstream, because a changed 3B evaluation produces a new
    ``evaluation_id``.
    """
    payload = {
        "rule_pack_id": rule_pack_id,
        "rule_id": rule_id,
        "account": account.to_dict(),
        "evaluation_id": evaluation_id,
        "action": action.value,
        "scope": scope.value,
        "symbols": tuple(symbols),
        "position_ticket": position_ticket,
        "projected": bool(projected),
    }
    return "enf_" + content_hash(payload)[:32]


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# SCOPE RESOLUTION â€” a rule's declared scope becomes an explicit runtime scope
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def scope_for_rule(rule: Any) -> EnforcementScope:
    """The runtime scope a 3A rule's declared instrument narrowing implies.

    A rule narrowed to exactly one canonical symbol blocks that symbol. A rule
    narrowed to several becomes a SYMBOL_SET. An unnarrowed rule is ACCOUNT
    wide. Direction and correlation scopes are read from the rule's own fields
    when it declares them.
    """
    symbols = tuple(getattr(rule, "applies_to_symbols", ()) or ())
    if getattr(rule, "direction_specific", False):
        return EnforcementScope.DIRECTION
    if getattr(rule, "correlation_cluster_scoped", False):
        return EnforcementScope.CORRELATION_CLUSTER
    if len(symbols) == 1:
        return EnforcementScope.SYMBOL
    if len(symbols) > 1:
        return EnforcementScope.SYMBOL_SET
    return EnforcementScope.ACCOUNT


def scope_symbols_for_rule(rule: Any) -> tuple[str, ...]:
    """The canonical symbols a scoped rule names, upper-cased and sorted."""
    return tuple(
        sorted(
            {
                str(s).strip().upper()
                for s in (getattr(rule, "applies_to_symbols", ()) or ())
                if str(s).strip()
            }
        )
    )


#: Per-scope entry-block action. A symbol-scoped rule MUST NOT block unrelated
#: symbols: carrying the scope on the decision is what makes that possible.
_SCOPE_ENTRY_ACTION: Mapping[EnforcementScope, EnforcementAction] = {
    EnforcementScope.ACCOUNT: EnforcementAction.BLOCK_ALL_ENTRIES,
    EnforcementScope.CHALLENGE: EnforcementAction.BLOCK_ALL_ENTRIES,
    EnforcementScope.SYMBOL: EnforcementAction.BLOCK_SYMBOL_ENTRY,
    EnforcementScope.SYMBOL_SET: EnforcementAction.BLOCK_SYMBOL_ENTRY,
    EnforcementScope.POSITION: EnforcementAction.BLOCK_SYMBOL_ENTRY,
    EnforcementScope.CORRELATION_CLUSTER: EnforcementAction.BLOCK_NEW_ENTRY,
    EnforcementScope.DIRECTION: EnforcementAction.BLOCK_NEW_ENTRY,
}

#: Per-scope position-reduction action.
_SCOPE_CLOSE_ACTION: Mapping[EnforcementScope, EnforcementAction] = {
    EnforcementScope.ACCOUNT: EnforcementAction.CLOSE_ACCOUNT_POSITIONS,
    EnforcementScope.CHALLENGE: EnforcementAction.CLOSE_ACCOUNT_POSITIONS,
    EnforcementScope.SYMBOL: EnforcementAction.CLOSE_SYMBOL_POSITIONS,
    EnforcementScope.SYMBOL_SET: EnforcementAction.CLOSE_SYMBOL_POSITIONS,
    EnforcementScope.POSITION: EnforcementAction.CLOSE_POSITION,
    EnforcementScope.CORRELATION_CLUSTER: EnforcementAction.CLOSE_SYMBOL_POSITIONS,
    EnforcementScope.DIRECTION: EnforcementAction.CLOSE_SYMBOL_POSITIONS,
}


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# REASON TRANSLATION â€” 3B explanation code -> 3C reason code
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

_REASON_FROM_EXPLANATION: Mapping[Any, EnforcementReason] = {
    "WITHIN_LIMIT": EnforcementReason.WITHIN_LIMIT,
    "LIMIT_EXCEEDED": EnforcementReason.LIMIT_EXCEEDED,
    "TARGET_MET": EnforcementReason.TARGET_MET,
    "TARGET_NOT_MET": EnforcementReason.WITHIN_LIMIT,
    "REQUIREMENT_MET": EnforcementReason.WITHIN_LIMIT,
    "REQUIREMENT_NOT_MET": EnforcementReason.REQUIREMENT_NOT_MET,
    "RULE_DISABLED": EnforcementReason.RULE_DISABLED,
    "RULE_NOT_EFFECTIVE": EnforcementReason.RULE_NOT_EFFECTIVE,
    "SCOPE_NOT_APPLICABLE": EnforcementReason.RULE_NOT_APPLICABLE,
    "STATE_FIELD_UNAVAILABLE": EnforcementReason.STATE_FIELD_UNAVAILABLE,
    "START_OF_DAY_ANCHOR_UNAVAILABLE": EnforcementReason.STATE_FIELD_UNAVAILABLE,
    "INITIAL_ANCHOR_UNAVAILABLE": EnforcementReason.STATE_FIELD_UNAVAILABLE,
    "INITIAL_ANCHOR_CONFLICT": EnforcementReason.STATE_CONFLICT,
    "HIGH_WATER_UNAVAILABLE": EnforcementReason.STATE_FIELD_UNAVAILABLE,
    "HIGH_WATER_COVERAGE_GAP": EnforcementReason.STATE_FIELD_UNAVAILABLE,
    "LEDGER_INCOMPLETE": EnforcementReason.STATE_FIELD_UNAVAILABLE,
    "TRADING_DAY_CRITERION_UNSPECIFIED": EnforcementReason.STATE_FIELD_UNAVAILABLE,
    "TRADING_DAY_HISTORY_UNAVAILABLE": EnforcementReason.STATE_FIELD_UNAVAILABLE,
    "INACTIVITY_BASIS_UNDECLARED": EnforcementReason.STATE_FIELD_UNAVAILABLE,
    "INACTIVITY_STATE_INDETERMINATE": EnforcementReason.STATE_FIELD_UNAVAILABLE,
    "EXTERNAL_SOURCE_REQUIRED": EnforcementReason.EXTERNAL_SOURCE_UNAVAILABLE,
    "TELEMETRY_MISSING": EnforcementReason.EVIDENCE_INDETERMINATE,
    "TELEMETRY_STALE": EnforcementReason.STATE_STALE,
    "RISK_TOTAL_NOT_AUTHORITATIVE": EnforcementReason.EVIDENCE_INDETERMINATE,
    "CORRELATION_NOT_COMPLETE": EnforcementReason.EVIDENCE_INDETERMINATE,
    "CONSISTENCY_DENOMINATOR_UNAVAILABLE": EnforcementReason.EVIDENCE_INDETERMINATE,
    "CURRENCY_MISMATCH": EnforcementReason.CURRENCY_MISMATCH,
    "CONVERSION_SOURCE_UNAVAILABLE": EnforcementReason.CONVERSION_SOURCE_UNAVAILABLE,
    "RULE_TYPE_UNSUPPORTED": EnforcementReason.RULE_TYPE_UNSUPPORTED,
    "PHASE_DEPENDENCY_UNMET": EnforcementReason.WITHIN_LIMIT,
}


def reason_from_result(result: EvaluationResult, *, projected: bool = False) -> EnforcementReason:
    """Translate a 3B result's explanation into a 3C reason code.

    A PROJECTED breach is labelled distinctly so a reader can always tell
    "already violated" from "this order would violate it".
    """
    raw = getattr(getattr(result, "explanation_code", None), "value", None) or ""


    base = _REASON_FROM_EXPLANATION.get(raw)
    if base is None:
        # An unrecognised 3B explanation falls back to the STATUS, never to a
        # silent default.
        base = {
            EvaluationStatus.BREACH: EnforcementReason.LIMIT_EXCEEDED,
            EvaluationStatus.INDETERMINATE: EnforcementReason.EVIDENCE_INDETERMINATE,
            EvaluationStatus.UNSUPPORTED: EnforcementReason.EXTERNAL_SOURCE_UNAVAILABLE,
            EvaluationStatus.NOT_APPLICABLE: EnforcementReason.RULE_NOT_APPLICABLE,
        }.get(result.status, EnforcementReason.EVIDENCE_INDETERMINATE)
    if projected and result.status is EvaluationStatus.BREACH:
        return EnforcementReason.PROJECTED_BREACH
    return base
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# POLICY LAYER â€” evaluation status -> runtime action
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


@dataclass(frozen=True)
class PolicyOutcome:
    """The policy verdict for ONE (status, criticality, scope, rule) tuple.

    This is a pure function's output: no I/O, no clock, no broker. It is what
    makes the status-to-action mapping testable in isolation from the runtime.
    """

    action: EnforcementAction
    criticality: EnforcementCriticality
    applies_to: EnforcementScope
    recovery: RecoveryClass
    reason: EnforcementReason
    action_required: bool
    terminal: bool
    fail_closed_reason: str = ""


def recovery_for(rule_type: RuleType, criticality: EnforcementCriticality) -> RecoveryClass:
    """Which recovery class governs an action taken for this rule.

    Terminal hard stops (drawdown) are MANUAL. A daily limit clears at the next
    exact rule-day reset. Everything else clears when the governing rule itself
    stops demanding the action.
    """
    if rule_type in {
        RuleType.MAX_DRAWDOWN,
        RuleType.STATIC_DRAWDOWN,
        RuleType.TRAILING_DRAWDOWN,
    }:
        return RecoveryClass.MANUAL
    if rule_type is RuleType.DAILY_LOSS_LIMIT:
        return RecoveryClass.RULE_DAY_RESET
    if rule_type is RuleType.NEWS_TRADING_RESTRICTION:
        return RecoveryClass.EXTERNAL_WINDOW
    if criticality is EnforcementCriticality.ACCOUNT_SUSPEND:
        return RecoveryClass.MANUAL
    return RecoveryClass.ON_RULE_PASS


#: Recovery classes a rule day / event window clears WITHOUT human authority.
#: A suspension raised by such a rule is temporary by construction and must
#: never be recorded as terminal, or the recovery class becomes unreachable.
SELF_CLEARING_RECOVERY: frozenset[RecoveryClass] = frozenset(
    {RecoveryClass.RULE_DAY_RESET, RecoveryClass.EXTERNAL_WINDOW}
)


def decide_policy(
    *,
    rule: Any,
    result: EvaluationResult,
    projected: bool = False,
) -> PolicyOutcome:
    """The ONE place a 3B status becomes a runtime action.

    The five statuses are never collapsed:

    ``PASS``             -> NO_ACTION. A compliant rule never blocks.
    ``NOT_APPLICABLE``   -> NO_ACTION. Absence is explicit in 3A, not inferred.
    ``BREACH``           -> the action this rule's criticality dictates, and for
                            POSITION_REDUCTION only when the exact rule
                            contract actually demands closing.
    ``INDETERMINATE``    -> fail closed per CRITICALITY. Mandatory rule => block
                            new exposure. Advisory rule => record only. NEVER a
                            blanket kill switch.
    ``UNSUPPORTED``      -> REQUIRES_EXTERNAL_SOURCE when the rule is an
                            external dependency, otherwise fail closed only if
                            the rule is mandatory for safe operation.
    """
    rule_type = result.rule_type
    criticality = criticality_for(rule_type)
    scope = scope_for_rule(rule)
    recovery = recovery_for(rule_type, criticality)
    reason = reason_from_result(result, projected=projected)
    status = result.status

    def _out(action: EnforcementAction, **kwargs: Any) -> PolicyOutcome:
        # `reason` is ALWAYS explicit: a policy outcome that does not say why
        # would force the caller to guess.
        return PolicyOutcome(
            action=action,
            criticality=criticality,
            applies_to=scope,
            recovery=recovery,
            reason=kwargs.pop("reason", None) or reason,
            **kwargs,
        )

    # â”€â”€ decided-compliant paths â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if status in (EvaluationStatus.PASS, EvaluationStatus.NOT_APPLICABLE):
        return _out(EnforcementAction.NO_ACTION, action_required=False, terminal=False)

    # â”€â”€ breach: the rule's own criticality dictates the action â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if status is EvaluationStatus.BREACH:
        if criticality is EnforcementCriticality.ADVISORY:
            # Reaching a profit target is NOT automatically a stop.
            return _out(EnforcementAction.NO_ACTION, action_required=False, terminal=False)
        if criticality is EnforcementCriticality.POSITION_REDUCTION:
            if not _rule_demands_close(rule):
                # The rule restricts holding but does not require a close, so we
                # block NEW entries and never touch open risk.
                return _out(
                    EnforcementAction.BLOCK_NEW_ENTRY, action_required=True, terminal=False
                )
            return _out(
                _SCOPE_CLOSE_ACTION[scope],
                action_required=True,
                terminal=getattr(rule, "severity", None) is RuleSeverity.TERMINATION,
            )
        if criticality is EnforcementCriticality.ACCOUNT_SUSPEND:
            return _out(
                EnforcementAction.SUSPEND_ACCOUNT, action_required=True, terminal=True
            )
        if criticality is EnforcementCriticality.EXTERNAL_DEPENDENCY:
            return _out(
                EnforcementAction.REQUIRES_EXTERNAL_SOURCE,
                action_required=True,
                terminal=False,
            )
        # HARD_STOP and ENTRY_BLOCK both refuse new exposure; the difference is
        # the recorded criticality and the durable suspension HARD_STOP also
        # triggers, not a different arithmetic.
        #
        # Block 3D REPAIR: TERMINAL is only correct when the rule's OWN recovery
        # class cannot clear itself. ``DAILY_LOSS_LIMIT`` carries
        # ``RULE_DAY_RESET`` precisely because a daily limit resets every rule
        # day; marking its breach terminal made that recovery class unreachable
        # (``SuspensionState.clears_on_new_rule_day`` requires a non-terminal
        # suspension) and turned a single bad day into a PERMANENT account halt.
        return _out(
            _SCOPE_ENTRY_ACTION[scope],
            action_required=True,
            terminal=(
                criticality is EnforcementCriticality.HARD_STOP
                and recovery not in SELF_CLEARING_RECOVERY
            ),
        )

    # â”€â”€ indeterminate: fail closed exactly where criticality says so â”€â”€â”€â”€â”€â”€â”€
    if status is EvaluationStatus.INDETERMINATE:
        if criticality in ADVISORY_CRITICALITY:
            return _out(EnforcementAction.NO_ACTION, action_required=False, terminal=False)
        if criticality in EXTERNAL_CRITICALITIES:
            return _out(
                EnforcementAction.REQUIRES_EXTERNAL_SOURCE,
                action_required=True,
                terminal=False,
                fail_closed_reason=EnforcementReason.EXTERNAL_SOURCE_UNAVAILABLE.value,
            )
        # Block NEW exposure. Do NOT liquidate: an undecidable evaluation is not
        # evidence that existing exposure must be closed.
        return _out(
            EnforcementAction.INDETERMINATE_BLOCK,
            action_required=True,
            terminal=False,
            fail_closed_reason=EnforcementReason.EVIDENCE_INDETERMINATE.value,
        )

    # â”€â”€ unsupported: fail closed only where the rule is mandatory â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if status is EvaluationStatus.UNSUPPORTED:
        if criticality in ADVISORY_CRITICALITY:
            return _out(EnforcementAction.NO_ACTION, action_required=False, terminal=False)
        if criticality in EXTERNAL_CRITICALITIES:
            return _out(
                EnforcementAction.REQUIRES_EXTERNAL_SOURCE,
                action_required=True,
                terminal=False,
                fail_closed_reason=EnforcementReason.EXTERNAL_SOURCE_UNAVAILABLE.value,
            )
        if is_mandatory_for_safe_operation(rule_type, criticality):
            return _out(
                EnforcementAction.INDETERMINATE_BLOCK,
                action_required=True,
                terminal=False,
                fail_closed_reason=EnforcementReason.RULE_TYPE_UNSUPPORTED.value,
            )
        return _out(EnforcementAction.NO_ACTION, action_required=False, terminal=False)

    # Unreachable for the closed 3B status enum; fail closed rather than guess.
    return _out(
        EnforcementAction.INDETERMINATE_BLOCK,
        action_required=True,
        terminal=False,
        fail_closed_reason="UNRECOGNISED_EVALUATION_STATUS",
    )


def _rule_demands_close(rule: Any) -> bool:
    """Whether the EXACT 3A hold-restriction contract requires closing.

    "No weekend hold" without an explicit "must close before market close"
    blocks new entries only. 3C never invents close behaviour the contract did
    not declare.
    """
    return bool(getattr(rule, "must_close_before_market_close", False))


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# DECISION COMPILATION â€” EvaluationResult(s) -> EnforceDecision(s)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def compile_decision(
    *,
    rule: Any,
    result: EvaluationResult,
    account: AccountKey,
    effective_at_utc: datetime,
    projected: bool = False,
    extra_evaluation_ids: tuple[str, ...] = (),
) -> EnforceDecision:
    """Compile ONE 3B result into ONE immutable enforcement decision.

    This function contains no rule arithmetic. It reads the verdict 3B already
    produced, applies the policy table, and binds the result to the exact
    account identity.
    """
    if result.evaluated_at_utc.tzinfo is None:
        raise EnforcementError("EVALUATION_RESULT_REQUIRED_AWARE_INSTANT")
    policy = decide_policy(rule=rule, result=result, projected=projected)
    scope = policy.applies_to
    symbols = scope_symbols_for_rule(rule)
    action = policy.action
    if action in ENTRY_BLOCKING_ACTIONS and scope is EnforcementScope.SYMBOL and not symbols:
        # A single-symbol scope with no resolvable symbol cannot be enforced
        # safely; widen to the account rather than silently not blocking.
        scope = EnforcementScope.ACCOUNT
    return EnforceDecision(
        enforcement_id=derive_enforcement_id(
            rule_pack_id=result.rule_pack_id,
            rule_id=result.rule_id,
            account=account,
            evaluation_id=result.evaluation_id,
            action=action,
            scope=scope,
            symbols=symbols,
            projected=projected,
        ),
        rule_pack_id=result.rule_pack_id,
        rule_id=result.rule_id,
        rule_type=result.rule_type,
        account=account,
        evaluation_id=result.evaluation_id,
        evaluated_status=result.status,
        enforcement_action=action,
        criticality=policy.criticality,
        severity=severity_from_rule(getattr(rule, "severity", None)),
        effective_at_utc=effective_at_utc,
        applies_to=scope,
        reason_code=policy.reason,
        symbols=symbols,
        correlation_cluster=str(
            getattr(result, "correlation_cluster", "") or ""
        ),
        evidence_used=tuple(result.evidence_used),
        source_evaluation_ids=(result.evaluation_id,) + tuple(extra_evaluation_ids),
        projected=projected,
        current_value=result.current_value,
        limit_value=result.limit_value,
        recovery=policy.recovery,
        action_required=policy.action_required,
        terminal=policy.terminal,
        fail_closed_reason=policy.fail_closed_reason,
        detail=result.detail,
    )


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# EFFECTIVE DECISION SET â€” precedence, coalescing, entry verdict
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def _sort_key(decision: EnforceDecision) -> tuple[Any, ...]:
    """Total, content-derived ordering.

    Ordering is by (action precedence, scope breadth, enforcement id). Because
    the id is a content hash, the ordering is identical on every process and
    every machine: rule iteration order can never change the outcome.
    """
    return (
        ACTION_RANK[decision.enforcement_action],
        _SCOPE_BREADTH[decision.applies_to],
        decision.enforcement_id,
    )


#: How broad a scope is. A broader block subsumes a narrower one.
_SCOPE_BREADTH: Mapping[EnforcementScope, int] = {
    EnforcementScope.CHALLENGE: 0,
    EnforcementScope.ACCOUNT: 1,
    EnforcementScope.CORRELATION_CLUSTER: 2,
    EnforcementScope.DIRECTION: 2,
    EnforcementScope.SYMBOL_SET: 3,
    EnforcementScope.SYMBOL: 4,
    EnforcementScope.POSITION: 5,
}


@dataclass(frozen=True)
class EffectiveEnforcement:
    """The ONE deterministic effective decision set for an account+instant.

    Several rules may demand the same action at the same time. They are
    COALESCED into a single runtime transition carrying every supporting
    evaluation reference, rather than producing conflicting duplicate actions.
    """

    account: AccountKey
    effective_at_utc: datetime
    #: The most restrictive action demanded by ANY rule.
    effective_action: EnforcementAction
    #: Every coalesced transition, most restrictive first.
    transitions: tuple[EnforceDecision, ...] = ()
    #: Every decision considered, including NO_ACTION ones, for lineage.
    all_decisions: tuple[EnforceDecision, ...] = ()
    #: The single decision that carries the effective action, if any.
    primary: EnforceDecision | None = None

    @property
    def is_blocked(self) -> bool:
        """Whether new exposure is refused for this account at this instant."""
        return self.effective_action in ENTRY_BLOCKING_ACTIONS

    @property
    def blocked_symbols(self) -> tuple[str, ...]:
        """Symbols explicitly blocked by a symbol-scoped decision."""
        symbols: set[str] = set()
        for decision in self.transitions:
            if decision.enforcement_action in ENTRY_BLOCKING_ACTIONS and decision.symbols:
                symbols.update(decision.symbols)
        return tuple(sorted(symbols))

    @property
    def requires_position_reduction(self) -> bool:
        return any(d.is_position_reduction for d in self.transitions)

    @property
    def requires_halt(self) -> bool:
        return any(d.is_halt for d in self.transitions)

    def supporting_evaluation_ids(self, action: EnforcementAction) -> tuple[str, ...]:
        """Every 3B evaluation that contributed to one coalesced action."""
        found: list[str] = []
        for decision in self.transitions:
            if decision.enforcement_action is action:
                found.extend(decision.source_evaluation_ids)
        return tuple(sorted(set(found)))


    def position_actions(self) -> tuple[EnforceDecision, ...]:
        """Every POSITION-REDUCTION decision, in deterministic order.

        Kept separate from :meth:`blocks` on purpose: a close decision does not
        block an entry, so folding the two together would either hide a required
        close or falsely block an entry.
        """
        return tuple(d for d in self.transitions if d.is_position_reduction)

    def halt_actions(self) -> tuple[EnforceDecision, ...]:
        """Every account/challenge HALT decision, in deterministic order."""
        return tuple(d for d in self.transitions if d.is_halt)

    def blocks(self, symbol: str | None, ticket: int | None = None) -> tuple[EnforceDecision, ...]:
        """The exact decisions that block a specific proposed position."""
        return tuple(
            decision
            for decision in self.transitions
            if decision.is_entry_blocking
            and decision.covers_symbol(symbol)
            and decision.covers_ticket(ticket)
        )


def coalesce_decisions(
    decisions: "tuple[EnforceDecision, ...] | list[EnforceDecision]",
    *,
    account: AccountKey,
    effective_at_utc: datetime,
) -> EffectiveEnforcement:
    """Resolve MANY decisions into ONE deterministic effective decision set.

    COALESCING
    ----------
    Decisions demanding the SAME (action, scope, symbols) are merged into a
    single runtime transition that keeps every supporting rule and evaluation
    reference. Three rules all saying BLOCK_ALL_ENTRIES therefore produce one
    block with three supporting evaluations, not three conflicting actions.

    NO_ACTION AND ALLOW ARE DROPPED FROM ``transitions``
    -----------------------------------------------------
    They are not runtime actions; they stay in ``all_decisions`` for lineage so
    nothing is silently discarded.

    DETERMINISM
    -----------
    Order is (precedence, scope breadth, content id). The same input set always
    yields the same effective set, on any machine, in any rule order.
    """
    ordered = sorted(decisions, key=_sort_key)
    groups: dict[tuple[Any, ...], list[EnforceDecision]] = {}
    for decision in ordered:
        key = (
            decision.enforcement_action,
            decision.applies_to,
            decision.symbols,
        )
        groups.setdefault(key, []).append(decision)

    transitions: list[EnforceDecision] = []
    for (_, applies_to, symbols), members in groups.items():
        if not any(m.action_required for m in members):
            continue
        head = min(members, key=_sort_key)
        if len(members) == 1:
            transitions.append(head)
            continue
        # One block state transition, N supporting rule references.
        transitions.append(
            replace(
                head,
                source_evaluation_ids=tuple(
                    sorted({e for m in members for e in m.source_evaluation_ids})
                ),
                evidence_used=tuple(
                    sorted({e for m in members for e in m.evidence_used})
                ),
                detail="COALESCED:" + ",".join(sorted({m.rule_id for m in members})),
            )
        )
    transitions.sort(key=_sort_key)
    primary = transitions[0] if transitions else None
    return EffectiveEnforcement(
        account=account,
        effective_at_utc=effective_at_utc,
        effective_action=(
            primary.enforcement_action if primary is not None else EnforcementAction.ALLOW
        ),
        transitions=tuple(transitions),
        all_decisions=tuple(ordered),
        primary=primary,
    )


#: Operations Block 3C must never implement, because they belong to 3B.
#: A source-scan test asserts none of these are implemented in this module.
FORBIDDEN_OPERATIONS: tuple[str, ...] = (
    "daily_loss_arithmetic",
    "drawdown_arithmetic",
    "high_water_derivation",
    "trading_day_counting",
    "consistency_ratio",
    "open_risk_aggregation",
)
