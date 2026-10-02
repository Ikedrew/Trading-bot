"""Deterministic, side-effect-free HISTORICAL prop rule EVALUATOR (Block 3B).

This module reads a typed :class:`~core.risk.prop_rule_state.AccountEvaluationState`
and an exact 3A rule pack, and produces an immutable
:class:`EvaluationResult` per rule. It performs NO live enforcement.

WHAT THIS MODULE IS NOT
-----------------------
* It does not block, close or modify a trade.
* It does not call a kill switch, a runtime guard, a scanner or a strategy.
* It does not import MetaTrader5 and never calls a broker. Runtime data
  acquisition belongs upstream, in the Block 2 telemetry layer.
* It reads no wall clock. Every instant is injected through the
  :class:`EvaluationContext`, so the same inputs always produce the same outputs.

DETERMINISM
-----------
:func:`derive_evaluation_id` hashes the rule pack, the rule, the account, the
evaluation instant (which encodes the rule period), and the exact evidence
lineage. Identical evidence therefore yields an identical ``evaluation_id``, and
any material evidence change yields a new one. Nothing random, nothing ordered by
insertion.

FIVE OUTCOMES, NOT TRUE/FALSE
-----------------------------
``PASS`` / ``BREACH`` / ``INDETERMINATE`` / ``UNSUPPORTED`` / ``NOT_APPLICABLE``.
A rule whose evidence is missing is INDETERMINATE, never silently PASS. A rule
whose system support is absent is UNSUPPORTED, never approximated.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields as dataclass_fields
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from core.risk.prop_rule_contracts import PnLComponents
from core.risk.prop_rule_enums import (
    CurrencySemantics,
    DailyResetPolicy,
    DrawdownKind,
    EvaluationSupport,
    LimitBasis,
    RuleType,
)
from core.risk.prop_rule_telemetry import TelemetryRequirement
from core.risk.prop_rule_values import Limit, RuleBase
from core.risk.prop_rule_state import (
    UTC,
    AccountEvaluationState,
    AccountKey,
    EconomicCalendarSource,
    InactivityState,
    MarketSessionSource,
    RuleDayDefinition,
    StateStatus,
    TradingDayCriterion,
    TradingDayHistory,
    content_hash,
    derive_inactivity_state,
    parse_inactive_days,
    resolve_criterion,
)

#: Money comparisons use a tolerance ONLY for float representation noise. It is
#: never used to soften a limit.
MONEY_EPSILON = 1e-9


class EvaluationStatus(str, Enum):
    """The outcome of evaluating ONE rule against ONE state."""

    PASS = "PASS"
    BREACH = "BREACH"
    #: Evidence is missing, stale or ambiguous. NOT a pass and NOT a breach.
    INDETERMINATE = "INDETERMINATE"
    #: This system cannot evaluate the rule at all (missing external source).
    UNSUPPORTED = "UNSUPPORTED"
    #: The rule does not apply to this account/phase/scope.
    NOT_APPLICABLE = "NOT_APPLICABLE"


#: Codes explaining WHY a result has its status. Never a free-text reason.
class ExplanationCode(str, Enum):
    """Machine-readable reason codes attached to every result."""

    WITHIN_LIMIT = "WITHIN_LIMIT"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    TARGET_MET = "TARGET_MET"
    TARGET_NOT_MET = "TARGET_NOT_MET"
    REQUIREMENT_MET = "REQUIREMENT_MET"
    REQUIREMENT_NOT_MET = "REQUIREMENT_NOT_MET"
    RULE_DISABLED = "RULE_DISABLED"
    RULE_NOT_EFFECTIVE = "RULE_NOT_EFFECTIVE"
    STATE_FIELD_UNAVAILABLE = "STATE_FIELD_UNAVAILABLE"
    START_OF_DAY_ANCHOR_UNAVAILABLE = "START_OF_DAY_ANCHOR_UNAVAILABLE"
    INITIAL_ANCHOR_UNAVAILABLE = "INITIAL_ANCHOR_UNAVAILABLE"
    INITIAL_ANCHOR_CONFLICT = "INITIAL_ANCHOR_CONFLICT"
    HIGH_WATER_UNAVAILABLE = "HIGH_WATER_UNAVAILABLE"
    HIGH_WATER_COVERAGE_GAP = "HIGH_WATER_COVERAGE_GAP"
    LEDGER_INCOMPLETE = "LEDGER_INCOMPLETE"
    TRADING_DAY_CRITERION_UNSPECIFIED = "TRADING_DAY_CRITERION_UNSPECIFIED"
    TRADING_DAY_HISTORY_UNAVAILABLE = "TRADING_DAY_HISTORY_UNAVAILABLE"
    INACTIVITY_BASIS_UNDECLARED = "INACTIVITY_BASIS_UNDECLARED"
    INACTIVITY_STATE_INDETERMINATE = "INACTIVITY_STATE_INDETERMINATE"
    EXTERNAL_SOURCE_REQUIRED = "EXTERNAL_SOURCE_REQUIRED"
    TELEMETRY_MISSING = "TELEMETRY_MISSING"
    TELEMETRY_STALE = "TELEMETRY_STALE"
    RISK_TOTAL_NOT_AUTHORITATIVE = "RISK_TOTAL_NOT_AUTHORITATIVE"
    CORRELATION_NOT_COMPLETE = "CORRELATION_NOT_COMPLETE"
    CONSISTENCY_DENOMINATOR_UNAVAILABLE = "CONSISTENCY_DENOMINATOR_UNAVAILABLE"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    CONVERSION_SOURCE_UNAVAILABLE = "CONVERSION_SOURCE_UNAVAILABLE"
    RULE_TYPE_UNSUPPORTED = "RULE_TYPE_UNSUPPORTED"
    SCOPE_NOT_APPLICABLE = "SCOPE_NOT_APPLICABLE"
    PHASE_DEPENDENCY_UNMET = "PHASE_DEPENDENCY_UNMET"



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# EVALUATION CONTEXT Ã¢â‚¬â€ the exact, typed binding of every input
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


@dataclass(frozen=True)
class EvaluationContext:
    """Everything ONE rule evaluation is allowed to read. Nothing else.

    There are no loose dict joins: the context binds the exact rule pack, the
    exact account state, the exact instant, the exact Block 2 telemetry lineage,
    the exact historical state lineage, and OPTIONAL external sources.

    EXTERNAL SOURCES ARE OPTIONAL ON PURPOSE
    -----------------------------------------
    A missing economic calendar or market-session calendar does not make the
    context invalid; it makes the DEPENDENT rule INDETERMINATE / UNSUPPORTED.
    Block 3B ships no implementation of either, and no timestamp is fabricated.
    """

    rule_pack: Any
    state: AccountEvaluationState
    evaluated_at_utc: datetime
    rule_day_definition: RuleDayDefinition
    #: Exact evidence lineage folded into the evaluation identity.
    state_lineage: Mapping[str, Any] = field(default_factory=dict)
    #: Block 2 telemetry record ids consumed, for audit.
    telemetry_lineage: Mapping[str, Any] = field(default_factory=dict)
    economic_calendar: EconomicCalendarSource | None = None
    market_sessions: MarketSessionSource | None = None
    #: Optional externally supplied FX conversion (never guessed).
    fx_conversion_source: str | None = None
    context_id: str = ""

    def __post_init__(self) -> None:
        if self.rule_pack is None:
            raise ValueError("EVALUATION_CONTEXT_REQUIRES_RULE_PACK")
        if not isinstance(self.state, AccountEvaluationState):
            raise ValueError("EVALUATION_CONTEXT_REQUIRES_ACCOUNT_STATE")
        if self.evaluated_at_utc.tzinfo is None:
            raise ValueError("EVALUATION_CONTEXT_REQUIRES_AWARE_INSTANT")
        if not self.context_id:
            object.__setattr__(self, "context_id", self.derive_context_id())

    def derive_context_id(self) -> str:
        payload = {
            "rule_pack_id": getattr(self.rule_pack, "rule_pack_id", None),
            "account": self.state.account.to_dict(),
            "evaluated_at_utc": self.evaluated_at_utc,
            "rule_day_definition": self.rule_day_definition.key(),
            "state_lineage": dict(self.state_lineage),
            "telemetry_lineage": dict(self.telemetry_lineage),
        }
        return "evc_" + content_hash(payload)[:32]

    @property
    def account(self) -> AccountKey:
        return self.state.account

    @property
    def rule_day(self) -> date:
        return self.state.rule_day

    def rules_in_force(self) -> tuple[RuleBase, ...]:
        """Only the rules that were actually in force at the evaluated instant."""
        return self.rule_pack.rules_effective_at(self.evaluated_at_utc)

    def has_external_calendar(self) -> bool:
        return self.economic_calendar is not None

    def has_market_sessions(self) -> bool:
        return self.market_sessions is not None


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# EVALUATION RESULT Ã¢â‚¬â€ typed, immutable, five-valued
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


@dataclass(frozen=True)
class EvaluationResult:
    """The immutable outcome of evaluating ONE rule at ONE instant.

    ``current_value``, ``limit_value`` and ``remaining_buffer`` are the rule's
    own units (money, lots, positions or days) and are ``None`` when the rule
    could not be measured. ``evidence_used`` names every record the decision
    actually read, so a result can always be reproduced from durable evidence
    alone.
    """

    evaluation_id: str
    rule_pack_id: str
    rule_id: str
    rule_type: RuleType
    account_id: str
    evaluated_at_utc: datetime
    status: EvaluationStatus
    rule_day: date
    timezone_name: str
    unit: str
    current_value: float | None = None
    limit_value: float | None = None
    remaining_buffer: float | None = None
    breach: bool = False
    support_status: EvaluationSupport = EvaluationSupport.NOT_IMPLEMENTED
    evidence_used: tuple[str, ...] = ()
    unavailable_requirements: tuple[TelemetryRequirement, ...] = ()
    explanation_code: ExplanationCode = ExplanationCode.WITHIN_LIMIT
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.evaluation_id:
            raise ValueError("EVALUATION_RESULT_REQUIRES_ID")
        if self.evaluated_at_utc.tzinfo is None:
            raise ValueError("EVALUATION_RESULT_REQUIRES_AWARE_INSTANT")
        object.__setattr__(self, "evidence_used", tuple(self.evidence_used))
        object.__setattr__(
            self, "unavailable_requirements", tuple(self.unavailable_requirements)
        )
        if self.status is EvaluationStatus.BREACH and not self.breach:
            raise ValueError("BREACH_STATUS_REQUIRES_BREACH_FLAG")
        if self.status is not EvaluationStatus.BREACH and self.breach:
            raise ValueError("BREACH_FLAG_REQUIRES_BREACH_STATUS")

    @property
    def is_decidable(self) -> bool:
        """Whether this result actually answers the rule's question."""
        return self.status in (EvaluationStatus.PASS, EvaluationStatus.BREACH)

    def to_dict(self) -> dict[str, Any]:
        payload = {f.name: getattr(self, f.name) for f in dataclass_fields(self)}
        payload["status"] = self.status.value
        payload["rule_type"] = self.rule_type.value
        payload["support_status"] = self.support_status.value
        payload["explanation_code"] = self.explanation_code.value
        payload["evaluated_at_utc"] = self.evaluated_at_utc.astimezone(timezone.utc).isoformat()
        payload["rule_day"] = self.rule_day.isoformat()
        payload["unavailable_requirements"] = [r.value for r in self.unavailable_requirements]
        return payload



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# DAILY P&L CONTRACT Ã¢â‚¬â€ explicit component selection, never pre-collapsed
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


class DailyPnlSelection:
    """The result of selecting a 3A :class:`PnLComponents` against real state.

    A rule that includes commission but excludes swap gets EXACTLY that. Nothing
    is pre-collapsed, and any INCLUDED component that is unknown makes the whole
    selection INDETERMINATE rather than zero.
    """

    __slots__ = ("components", "value", "unavailable", "detail", "available")

    def __init__(
        self,
        *,
        components: PnLComponents,
        value: float | None,
        unavailable: tuple[TelemetryRequirement, ...] = (),
        available: bool = True,
        detail: str = "",
    ) -> None:
        self.components = components
        self.value = value
        self.unavailable = tuple(unavailable)
        self.available = available and value is not None
        self.detail = detail

    @property
    def is_available(self) -> bool:
        return self.available and self.value is not None

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"DailyPnlSelection(value={self.value}, available={self.is_available})"


def select_daily_pnl(
    *,
    components: PnLComponents,
    state: AccountEvaluationState,
) -> DailyPnlSelection:
    """Select the exact P&L a rule's components describe.

    COMPONENT SEMANTICS (signed, exactly as the source reported them)
    -----------------------------------------------------------------
    * closed P&L      -> ``net_realised_pnl`` (gross + commission + swap + fees)
    * floating        -> the ACCOUNT-level broker value from Block 2A
    * commission/swap/fees are applied ONLY when the rule includes them, and each
      is required to be known. A rule that excludes them is unaffected by their
      being unknown; a rule that includes them cannot proceed without them.
    """
    ledger = state.daily_ledger
    if ledger is None:
        return DailyPnlSelection(
            components=components, value=None,
            unavailable=(TelemetryRequirement.REALISED_DAILY_PNL,),
            available=False, detail="DAILY_LEDGER_UNAVAILABLE",
        )
    if components.include_closed_pnl and ledger.net_realised_pnl is None:
        return DailyPnlSelection(
            components=components, value=None,
            unavailable=(TelemetryRequirement.REALISED_DAILY_PNL,),
            available=False, detail="NET_REALISED_PNL_UNKNOWN",
        )
    if components.include_closed_pnl and not ledger.is_complete:
        return DailyPnlSelection(
            components=components, value=None,
            unavailable=(TelemetryRequirement.REALISED_DAILY_PNL,),
            available=False, detail="LEDGER_COMPONENTS_INCOMPLETE",
        )
    if components.include_floating_pnl and state.floating_pnl is None:
        return DailyPnlSelection(
            components=components, value=None,
            unavailable=(TelemetryRequirement.FLOATING_PNL,),
            available=False, detail="FLOATING_PNL_UNAVAILABLE",
        )

    total = 0.0
    if components.include_closed_pnl:
        total += float(ledger.net_realised_pnl or 0.0)
    if components.include_floating_pnl:
        total += float(state.floating_pnl or 0.0)
    # Costs are already inside net_realised_pnl when the rule includes them.
    # A rule that includes closed P&L but EXCLUDES a cost must have that cost
    # added back explicitly, which requires the cost to be known.
    if components.include_closed_pnl:
        for included, value, requirement in (
            (components.include_commission, ledger.commission, TelemetryRequirement.COMMISSION_AND_FEES),
            (components.include_swap, ledger.swap, TelemetryRequirement.SWAP),
            (components.include_fees, ledger.fees, TelemetryRequirement.COMMISSION_AND_FEES),
        ):
            if not included:
                if value is None:
                    return DailyPnlSelection(
                        components=components, value=None, unavailable=(requirement,),
                        available=False, detail="EXCLUDED_COST_UNKNOWN_CANNOT_ADD_BACK",
                    )
                total -= float(value)
    return DailyPnlSelection(
        components=components, value=round(total, 8) + 0.0, available=True,
        detail="COMPONENTS_SELECTED_EXPLICITLY",
    )



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# CANONICAL DERIVED DAILY P&L VALUES
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


#: The four canonical derived daily P&L values. Each is produced ONLY through
#: explicit component selection -- never by collapsing costs before evaluation.
DAILY_PNL_CONTRACTS: Mapping[str, PnLComponents] = {
    "REALISED_ONLY": PnLComponents(
        include_closed_pnl=True, include_floating_pnl=False,
        include_commission=True, include_swap=True, include_fees=True,
    ),
    "REALISED_PLUS_FLOATING": PnLComponents(
        include_closed_pnl=True, include_floating_pnl=True,
        include_commission=True, include_swap=True, include_fees=True,
    ),
    "REALISED_PLUS_COSTS": PnLComponents(
        include_closed_pnl=True, include_floating_pnl=False,
        include_commission=True, include_swap=True, include_fees=True,
    ),
    "REALISED_PLUS_FLOATING_PLUS_COSTS": PnLComponents(
        include_closed_pnl=True, include_floating_pnl=True,
        include_commission=True, include_swap=True, include_fees=True,
    ),
}


def derived_daily_pnl(contract: str, state: AccountEvaluationState) -> DailyPnlSelection:
    """Evaluate a canonical derived P&L contract by NAME.

    A rule never uses this to bypass its own declared components; it exists so a
    caller can ask for a named contract explicitly and get the same explicit
    component selection the evaluator would perform.
    """
    components = DAILY_PNL_CONTRACTS.get(str(contract).upper())
    if components is None:
        return DailyPnlSelection(
            components=PnLComponents(), value=None, available=False,
            detail=f"UNKNOWN_PNL_CONTRACT:{contract}",
        )
    return select_daily_pnl(components=components, state=state)


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# EVALUATION IDENTITY Ã¢â‚¬â€ deterministic, evidence-sensitive
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def derive_evaluation_id(
    *,
    rule_pack_id: str,
    rule_id: str,
    account: AccountKey,
    evaluated_at_utc: datetime,
    rule_day: date,
    timezone_name: str,
    evidence_lineage: Mapping[str, Any],
) -> str:
    """Deterministic identity of ONE rule evaluation.

    The instant is included, and the instant encodes the rule PERIOD (a rule day
    for daily rules, an intraday instant for continuous ones), so the same rule
    evaluated at a different time is a different evaluation. The evidence lineage
    is included, so ANY material evidence change produces a NEW id while the same
    exact evidence reproduces the same one.
    """
    payload = {
        "rule_pack_id": rule_pack_id,
        "rule_id": rule_id,
        "account": account.to_dict(),
        "evaluated_at_utc": evaluated_at_utc.astimezone(UTC),
        "rule_day": rule_day,
        "timezone_name": timezone_name,
        "evidence_lineage": dict(evidence_lineage),
    }
    return "ev_" + content_hash(payload)[:32]


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# RESULT CONSTRUCTION
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def _result(
    context: EvaluationContext,
    rule: RuleBase,
    status: EvaluationStatus,
    *,
    unit: str,
    explanation: ExplanationCode,
    current_value: float | None = None,
    limit_value: float | None = None,
    remaining_buffer: float | None = None,
    support_status: EvaluationSupport = EvaluationSupport.NOT_IMPLEMENTED,
    evidence: Sequence[str] = (),
    unavailable: Sequence[TelemetryRequirement] = (),
    detail: str = "",
    evidence_lineage: Mapping[str, Any] | None = None,
) -> EvaluationResult:
    """Build an immutable result with a deterministic id."""
    state = context.state
    lineage: dict[str, Any] = {
        "context_id": context.context_id,
        "state_status": state.status.value,
        "unavailable_fields": list(state.unavailable_fields),
        "initial_anchor_id": state.initial_anchor.anchor_id if state.initial_anchor else None,
        "daily_anchor_id": state.daily_anchor.anchor_id if state.daily_anchor else None,
        "ledger_id": state.daily_ledger.ledger_id if state.daily_ledger else None,
        "account_snapshot_id": state.account_snapshot_id,
        "open_risk_snapshot_id": state.open_risk_snapshot_id,
        "portfolio_exposure_id": state.portfolio_exposure_id,
        "state_lineage": dict(context.state_lineage),
    }
    if evidence_lineage:
        lineage.update(dict(evidence_lineage))
    evaluation_id = derive_evaluation_id(
        rule_pack_id=getattr(context.rule_pack, "rule_pack_id", "") or "",
        rule_id=rule.rule_id,
        account=state.account,
        evaluated_at_utc=context.evaluated_at_utc,
        rule_day=state.rule_day,
        timezone_name=state.rule_timezone,
        evidence_lineage=lineage,
    )
    return EvaluationResult(
        evaluation_id=evaluation_id,
        rule_pack_id=getattr(context.rule_pack, "rule_pack_id", "") or "",
        rule_id=rule.rule_id,
        rule_type=rule.rule_type,
        account_id=state.account.account_id,
        evaluated_at_utc=context.evaluated_at_utc,
        status=status,
        rule_day=state.rule_day,
        timezone_name=state.rule_timezone,
        unit=unit,
        current_value=current_value,
        limit_value=limit_value,
        remaining_buffer=remaining_buffer,
        breach=status is EvaluationStatus.BREACH,
        support_status=support_status,
        evidence_used=tuple(evidence),
        unavailable_requirements=tuple(unavailable),
        explanation_code=explanation,
        detail=detail,
    )



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# LIMIT RESOLUTION Ã¢â‚¬â€ explicit basis, explicit currency, no substitution
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


class _Unresolved(Exception):
    """Internal: a limit could not be resolved against the available state."""

    def __init__(self, code: ExplanationCode, detail: str,
                 unavailable: Sequence[TelemetryRequirement] = ()) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.unavailable = tuple(unavailable)


#: Which state field each monetary percentage basis is measured against.
_BASIS_FIELD: Mapping[LimitBasis, tuple[str, TelemetryRequirement]] = {
    LimitBasis.PERCENT_INITIAL_BALANCE: ("initial_balance", TelemetryRequirement.INITIAL_BALANCE),
    LimitBasis.PERCENT_INITIAL_EQUITY: ("initial_equity", TelemetryRequirement.INITIAL_EQUITY),
    LimitBasis.PERCENT_CURRENT_BALANCE: ("current_balance", TelemetryRequirement.ACCOUNT_BALANCE),
    LimitBasis.PERCENT_CURRENT_EQUITY: ("current_equity", TelemetryRequirement.ACCOUNT_EQUITY),
    LimitBasis.PERCENT_START_OF_DAY_BALANCE: (
        "start_of_day_balance", TelemetryRequirement.START_OF_DAY_BALANCE,
    ),
    LimitBasis.PERCENT_START_OF_DAY_EQUITY: (
        "start_of_day_equity", TelemetryRequirement.START_OF_DAY_EQUITY,
    ),
    LimitBasis.PERCENT_HIGH_WATER_BALANCE: (
        "high_water_balance", TelemetryRequirement.HIGH_WATER_EQUITY,
    ),
    LimitBasis.PERCENT_HIGH_WATER_EQUITY: (
        "high_water_equity", TelemetryRequirement.HIGH_WATER_EQUITY,
    ),
}

#: The current-quantity field each basis compares against.
_BASIS_CURRENT: Mapping[LimitBasis, str] = {
    LimitBasis.PERCENT_INITIAL_BALANCE: "current_balance",
    LimitBasis.PERCENT_INITIAL_EQUITY: "current_equity",
    LimitBasis.PERCENT_START_OF_DAY_BALANCE: "current_balance",
    LimitBasis.PERCENT_START_OF_DAY_EQUITY: "current_equity",
    LimitBasis.PERCENT_HIGH_WATER_BALANCE: "current_balance",
    LimitBasis.PERCENT_HIGH_WATER_EQUITY: "current_equity",
}


def _check_currency(limit: Limit, state: AccountEvaluationState) -> None:
    """Fail closed on a currency the account does not hold.

    A FIXED_RULE_CURRENCY limit on a different account currency, or a conversion
    that was never supplied, is INDETERMINATE -- never silently compared.
    """
    semantics = limit.currency_semantics
    if semantics is None:
        return
    if semantics is CurrencySemantics.ACCOUNT_CURRENCY:
        return
    if semantics is CurrencySemantics.FIXED_RULE_CURRENCY:
        rule_currency = (limit.rule_currency or "").strip().upper()
        if rule_currency and rule_currency != state.account_currency:
            raise _Unresolved(
                ExplanationCode.CURRENCY_MISMATCH,
                f"RULE_CURRENCY_{rule_currency}_ACCOUNT_{state.account_currency}",
                (TelemetryRequirement.EXTERNAL_FX_CONVERSION,),
            )
        return
    raise _Unresolved(
        ExplanationCode.CONVERSION_SOURCE_UNAVAILABLE,
        f"CONVERTED_REFERENCE_CURRENCY_REQUIRES_{limit.conversion_source}",
        (TelemetryRequirement.EXTERNAL_FX_CONVERSION,),
    )


def resolve_limit(limit: Limit, state: AccountEvaluationState) -> float:
    """Resolve a limit's MONEY value against the exact state it names.

    The basis is never substituted. A percentage basis resolves against the field
    3A named, and if that field is unavailable the result is INDETERMINATE rather
    than measured against a neighbouring field.
    """
    _check_currency(limit, state)
    if limit.basis is LimitBasis.ABSOLUTE_MONEY:
        return float(limit.value)
    if limit.basis in COUNT_BASES_RESOLVED:
        return float(limit.value)
    target = _BASIS_FIELD.get(limit.basis)
    if target is None:
        raise _Unresolved(
            ExplanationCode.RULE_TYPE_UNSUPPORTED, f"LIMIT_BASIS_UNRESOLVABLE:{limit.basis.value}"
        )
    field_name, requirement = target
    value = getattr(state, field_name, None)
    if value is None:
        code = {
            "start_of_day_balance": ExplanationCode.START_OF_DAY_ANCHOR_UNAVAILABLE,
            "start_of_day_equity": ExplanationCode.START_OF_DAY_ANCHOR_UNAVAILABLE,
            "initial_balance": ExplanationCode.INITIAL_ANCHOR_UNAVAILABLE,
            "initial_equity": ExplanationCode.INITIAL_ANCHOR_UNAVAILABLE,
            "high_water_balance": ExplanationCode.HIGH_WATER_UNAVAILABLE,
            "high_water_equity": ExplanationCode.HIGH_WATER_UNAVAILABLE,
        }.get(field_name, ExplanationCode.STATE_FIELD_UNAVAILABLE)
        raise _Unresolved(code, f"BASIS_FIELD_UNAVAILABLE:{field_name}", (requirement,))
    return float(limit.value) / 100.0 * float(value)


#: Bases that are already absolute counts and need no state resolution.
COUNT_BASES_RESOLVED: frozenset[LimitBasis] = frozenset(
    {
        LimitBasis.LOT_COUNT,
        LimitBasis.POSITION_COUNT,
        LimitBasis.CALENDAR_DAYS,
        LimitBasis.TRADING_DAYS,
    }
)


def current_for_basis(limit: Limit, state: AccountEvaluationState) -> float:
    """The current quantity a basis compares against, exactly as named."""
    if limit.basis is LimitBasis.ABSOLUTE_MONEY:
        raise _Unresolved(
            ExplanationCode.RULE_TYPE_UNSUPPORTED, "ABSOLUTE_MONEY_HAS_NO_CURRENT_BASIS"
        )
    field_name = _BASIS_CURRENT.get(limit.basis)
    if field_name is None:
        raise _Unresolved(
            ExplanationCode.RULE_TYPE_UNSUPPORTED, f"LIMIT_BASIS_UNRESOLVABLE:{limit.basis.value}"
        )
    value = getattr(state, field_name, None)
    if value is None:
        raise _Unresolved(
            ExplanationCode.STATE_FIELD_UNAVAILABLE, f"CURRENT_FIELD_UNAVAILABLE:{field_name}"
        )
    return float(value)





# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# DAILY LOSS / DAILY PROFIT
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def evaluate_daily_loss(context: EvaluationContext, rule: Any) -> EvaluationResult:
    """Historical daily-loss evaluation. NO runtime blocking.

    Respects the exact threshold basis, the reset timezone, the start-of-day
    anchor, and the rule's own P&L component selection. The loss is measured as
    the SELECTED daily P&L; the threshold is the limit resolved against the
    anchor the rule names.
    """
    state = context.state
    support = EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY
    if rule.reset_policy is DailyResetPolicy.NONE:
        return _result(
            context, rule, EvaluationStatus.NOT_APPLICABLE, unit="MONEY",
            explanation=ExplanationCode.SCOPE_NOT_APPLICABLE, support_status=support,
            detail="DAILY_RESET_POLICY_NONE",
        )
    if state.daily_anchor is None:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="MONEY",
            explanation=ExplanationCode.START_OF_DAY_ANCHOR_UNAVAILABLE, support_status=support,
            unavailable=(TelemetryRequirement.START_OF_DAY_BALANCE, TelemetryRequirement.START_OF_DAY_EQUITY),
            detail="NO_AUTHORITATIVE_ANCHOR_AT_RESET",
        )
    try:
        limit_value = resolve_limit(rule.limit, state)
        current = current_for_basis(rule.limit, state)
    except _Unresolved as exc:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="MONEY",
            explanation=exc.code, support_status=support, unavailable=exc.unavailable, detail=exc.detail,
        )
    selection = select_daily_pnl(components=rule.pnl_components, state=state)
    if not selection.is_available:
        code = (
            ExplanationCode.START_OF_DAY_ANCHOR_UNAVAILABLE
            if state.daily_anchor is None
            else ExplanationCode.LEDGER_INCOMPLETE
        )
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="MONEY",
            explanation=code, support_status=support, unavailable=selection.unavailable,
            detail=selection.detail,
            limit_value=limit_value, current_value=current,
        )
    measured = float(selection.value or 0.0)
    loss = max(0.0, -measured)
    buffer = round(limit_value - loss, 8) + 0.0
    status = EvaluationStatus.BREACH if loss > limit_value + MONEY_EPSILON else EvaluationStatus.PASS
    return _result(
        context, rule, status, unit="MONEY",
        explanation=(ExplanationCode.LIMIT_EXCEEDED if status is EvaluationStatus.BREACH
                     else ExplanationCode.WITHIN_LIMIT),
        current_value=loss, limit_value=limit_value, remaining_buffer=buffer,
        support_status=support,
        evidence=_evidence_for(state),
        evidence_lineage={"pnl_contract": selection.components.to_dict()},
        detail=(
            f"BASIS={rule.limit.basis.value}:RESET={state.rule_timezone}"
            f"@{state.rule_day_definition}:SELECTED_PNL={measured}"
        ),
    )


def evaluate_daily_profit(context: EvaluationContext, rule: Any) -> EvaluationResult:
    """Historical daily-profit-cap evaluation."""
    state = context.state
    support = EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY
    if rule.reset_policy is DailyResetPolicy.NONE:
        return _result(
            context, rule, EvaluationStatus.NOT_APPLICABLE, unit="MONEY",
            explanation=ExplanationCode.SCOPE_NOT_APPLICABLE, support_status=support,
            detail="DAILY_RESET_POLICY_NONE",
        )
    try:
        limit_value = resolve_limit(rule.limit, state)
    except _Unresolved as exc:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="MONEY",
            explanation=exc.code, support_status=support, unavailable=exc.unavailable, detail=exc.detail,
        )
    selection = select_daily_pnl(components=rule.pnl_components, state=state)
    if not selection.is_available:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="MONEY",
            explanation=ExplanationCode.LEDGER_INCOMPLETE, support_status=support,
            unavailable=selection.unavailable, detail=selection.detail, limit_value=limit_value,
        )
    measured = max(0.0, float(selection.value or 0.0))
    buffer = round(limit_value - measured, 8) + 0.0
    status = EvaluationStatus.BREACH if measured > limit_value + MONEY_EPSILON else EvaluationStatus.PASS
    return _result(
        context, rule, status, unit="MONEY",
        explanation=(ExplanationCode.LIMIT_EXCEEDED if status is EvaluationStatus.BREACH
                     else ExplanationCode.WITHIN_LIMIT),
        current_value=measured, limit_value=limit_value, remaining_buffer=buffer,
        support_status=support, evidence=_evidence_for(state),
    )



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# DRAWDOWN Ã¢â‚¬â€ static against the initial anchor, trailing against high water
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def evaluate_static_drawdown(context: EvaluationContext, rule: Any) -> EvaluationResult:
    """Static drawdown against the EXACT anchor 3A names.

    ``PERCENT_INITIAL_BALANCE`` is compared against current BALANCE and
    ``PERCENT_INITIAL_EQUITY`` against current EQUITY. Neither is substituted for
    the other, and the anchor is the immutable initial anchor, never a recomputed
    balance.
    """
    state = context.state
    support = EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY
    if state.initial_anchor is None:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="PERCENT",
            explanation=ExplanationCode.INITIAL_ANCHOR_UNAVAILABLE, support_status=support,
            unavailable=(TelemetryRequirement.INITIAL_BALANCE, TelemetryRequirement.INITIAL_EQUITY),
            detail="NO_DURABLE_INITIAL_ANCHOR",
        )
    try:
        limit_value = resolve_limit(rule.limit, state)
        current = current_for_basis(rule.limit, state)
    except _Unresolved as exc:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="PERCENT",
            explanation=exc.code, support_status=support, unavailable=exc.unavailable, detail=exc.detail,
        )
    anchor_field, _requirement = _BASIS_FIELD[rule.limit.basis]
    anchor_value = float(getattr(state, anchor_field))
    if anchor_value <= 0:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="PERCENT",
            explanation=ExplanationCode.INITIAL_ANCHOR_UNAVAILABLE, support_status=support,
            detail="INITIAL_ANCHOR_NOT_POSITIVE",
        )
    # Drawdown is the percentage fall from the immutable anchor to the current
    # value of the quantity the basis names. No substitution, no netting.
    drawdown = max(0.0, (anchor_value - current) / anchor_value * 100.0)
    buffer = round(float(rule.limit.value) - drawdown, 8) + 0.0
    status = (
        EvaluationStatus.BREACH
        if drawdown > float(rule.limit.value) + MONEY_EPSILON
        else EvaluationStatus.PASS
    )
    return _result(
        context, rule, status, unit="PERCENT",
        explanation=(ExplanationCode.LIMIT_EXCEEDED if status is EvaluationStatus.BREACH
                     else ExplanationCode.WITHIN_LIMIT),
        current_value=drawdown, limit_value=float(rule.limit.value), remaining_buffer=buffer,
        support_status=support, evidence=_evidence_for(state),
        detail=f"ANCHOR={rule.anchor.value if rule.anchor else rule.limit.basis.value}",
    )


def evaluate_trailing_drawdown(context: EvaluationContext, rule: Any) -> EvaluationResult:
    """Trailing drawdown using durable high-water state.

    Balance and equity high water are SEPARATE and selected by the rule's anchor.
    If the required trail state is unavailable, or the high-water carries a
    declared coverage gap, the result is INDETERMINATE rather than an optimistic
    PASS.
    """
    state = context.state
    support = EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY
    high_water = state.high_water
    if high_water is None or state.high_water_balance is None and state.high_water_equity is None:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="PERCENT",
            explanation=ExplanationCode.HIGH_WATER_UNAVAILABLE, support_status=support,
            unavailable=(TelemetryRequirement.HIGH_WATER_EQUITY,),
            detail="NO_HIGH_WATER_STATE",
        )
    if high_water.gap.value not in {"NONE"}:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="PERCENT",
            explanation=ExplanationCode.HIGH_WATER_COVERAGE_GAP, support_status=support,
            unavailable=(TelemetryRequirement.HIGH_WATER_EQUITY,),
            detail=f"HIGH_WATER_GAP:{high_water.gap.value}:{high_water.gap_detail}",
        )
    try:
        current = current_for_basis(rule.limit, state)
        peak = float(getattr(state, _BASIS_FIELD[rule.limit.basis][0]))
    except _Unresolved as exc:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="PERCENT",
            explanation=exc.code, support_status=support, unavailable=exc.unavailable, detail=exc.detail,
        )
    if peak <= 0:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="PERCENT",
            explanation=ExplanationCode.HIGH_WATER_UNAVAILABLE, support_status=support,
            detail="HIGH_WATER_NOT_POSITIVE",
        )
    drawdown = max(0.0, (peak - current) / peak * 100.0)
    buffer = round(float(rule.limit.value) - drawdown, 8) + 0.0
    status = (
        EvaluationStatus.BREACH
        if drawdown > float(rule.limit.value) + MONEY_EPSILON
        else EvaluationStatus.PASS
    )
    return _result(
        context, rule, status, unit="PERCENT",
        explanation=(ExplanationCode.LIMIT_EXCEEDED if status is EvaluationStatus.BREACH
                     else ExplanationCode.WITHIN_LIMIT),
        current_value=drawdown, limit_value=float(rule.limit.value), remaining_buffer=buffer,
        support_status=support, evidence=_evidence_for(state),
        detail=(
            f"TRAIL_REFERENCE={rule.trail_reference}:CADENCE={rule.trail_update_cadence}"
            f":LOCKS_AT_BREACH={rule.trail_locks_at_breach}"
        ),
    )



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# PROFIT TARGET
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def evaluate_profit_target(context: EvaluationContext, rule: Any) -> EvaluationResult:
    """Evaluate cumulative challenge profit against the exact target basis.

    No challenge-completion ACTION is taken; this only reports whether the target
    is met. A minimum-trading-day dependency that is not yet satisfied is
    INDETERMINATE (PHASE_DEPENDENCY_UNMET), never a failure.

    CUMULATIVE PROFIT is the sum of the AUTHORITATIVE daily net realised P&L
    series up to and including the current rule day, plus floating P&L only when
    the rule counts it. It is never inferred from a single balance delta.
    """
    state = context.state
    support = EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY

    if rule.minimum_days_required:
        history = state.trading_day_history
        criterion = resolve_rule_criterion(rule)
        if history is None or criterion is TradingDayCriterion.UNSPECIFIED:
            return _result(
                context, rule, EvaluationStatus.INDETERMINATE, unit="PERCENT",
                explanation=ExplanationCode.TRADING_DAY_CRITERION_UNSPECIFIED, support_status=support,
                unavailable=(TelemetryRequirement.TRADING_DAY_HISTORY,),
                detail="PROFIT_TARGET_MIN_DAYS_DEPENDENCY_UNRESOLVABLE",
            )
        counted = history.count_qualifying(criterion, up_to_rule_day=state.rule_day)
        if counted is None or counted < int(rule.minimum_days_required):
            return _result(
                context, rule, EvaluationStatus.INDETERMINATE, unit="PERCENT",
                explanation=ExplanationCode.PHASE_DEPENDENCY_UNMET, support_status=support,
                current_value=float(counted or 0), limit_value=float(rule.minimum_days_required),
                unavailable=(TelemetryRequirement.TRADING_DAY_HISTORY,),
                detail="MIN_TRADING_DAYS_NOT_YET_MET",
            )

    try:
        target_value = resolve_limit(rule.limit, state)
    except _Unresolved as exc:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="PERCENT",
            explanation=exc.code, support_status=support, unavailable=exc.unavailable, detail=exc.detail,
        )

    history = state.trading_day_history
    if history is None:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="PERCENT",
            explanation=ExplanationCode.LEDGER_INCOMPLETE, support_status=support,
            unavailable=(TelemetryRequirement.DAILY_PROFIT_SERIES,),
            limit_value=target_value, detail="NO_DURABLE_DAILY_PROFIT_SERIES",
        )
    series = history.daily_profit_series(up_to_rule_day=state.rule_day)
    if not series:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="PERCENT",
            explanation=ExplanationCode.LEDGER_INCOMPLETE, support_status=support,
            unavailable=(TelemetryRequirement.DAILY_PROFIT_SERIES,),
            limit_value=target_value, detail="EMPTY_DAILY_PROFIT_SERIES",
        )
    cumulative = round(sum(value for _day, value in series), 8) + 0.0
    if rule.counts_floating_profit:
        if state.floating_pnl is None:
            return _result(
                context, rule, EvaluationStatus.INDETERMINATE, unit="PERCENT",
                explanation=ExplanationCode.LEDGER_INCOMPLETE, support_status=support,
                unavailable=(TelemetryRequirement.FLOATING_PNL,),
                limit_value=target_value, detail="FLOATING_REQUIRED_BUT_UNAVAILABLE",
            )
        cumulative = round(cumulative + float(state.floating_pnl), 8) + 0.0

    buffer = round(target_value - cumulative, 8) + 0.0
    met = cumulative >= target_value - MONEY_EPSILON
    return _result(
        context, rule, EvaluationStatus.PASS if met else EvaluationStatus.INDETERMINATE,
        unit="PERCENT",
        explanation=(ExplanationCode.TARGET_MET if met else ExplanationCode.TARGET_NOT_MET),
        current_value=cumulative, limit_value=target_value, remaining_buffer=buffer,
        support_status=support, evidence=_evidence_for(state),
        detail=(
            f"TARGET_BASIS={rule.limit.basis.value}:COUNTS_FLOATING={rule.counts_floating_profit}"
            f":DAYS_IN_SERIES={len(series)}"
        ),
    )



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# TRADING DAY + INACTIVITY
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def _trading_day_thresholds(rule: Any) -> dict[str, Any]:
    """Thresholds the rule declares for its qualifying criterion.

    Only thresholds the rule ACTUALLY states are passed through. A missing
    threshold is ``None``, which makes the affected criterion INDETERMINATE
    rather than defaulting to zero.
    """
    minimum_profit = getattr(rule, "minimum_profit", None)
    lot_based = getattr(rule, "minimum_lot", None)
    return {
        "minimum_closed_trades": getattr(rule, "minimum_closed_trades", None),
        "minimum_volume": float(lot_based) if lot_based is not None else None,
        "minimum_pnl": float(minimum_profit.value) if minimum_profit is not None else None,
    }


def evaluate_trading_day_rule(context: EvaluationContext, rule: Any) -> EvaluationResult:
    """MIN / MAX trading days from DURABLE history only. No wall-clock guess."""
    state = context.state
    support = EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY
    history = state.trading_day_history
    criterion = resolve_rule_criterion(rule)
    if history is None:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="DAYS",
            explanation=ExplanationCode.TRADING_DAY_HISTORY_UNAVAILABLE, support_status=support,
            unavailable=(TelemetryRequirement.TRADING_DAY_HISTORY,),
            detail="NO_DURABLE_TRADING_DAY_HISTORY",
        )
    if criterion is TradingDayCriterion.UNSPECIFIED:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="DAYS",
            explanation=ExplanationCode.TRADING_DAY_CRITERION_UNSPECIFIED, support_status=support,
            unavailable=(TelemetryRequirement.TRADING_DAY_HISTORY,),
            detail="QUALIFYING_CRITERION_NOT_DECLARED_BY_RULE",
        )
    counted = history.count_qualifying(
        criterion, up_to_rule_day=state.rule_day, **_trading_day_thresholds(rule)
    )
    if counted is None:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="DAYS",
            explanation=ExplanationCode.TRADING_DAY_CRITERION_UNSPECIFIED, support_status=support,
            detail="CRITERION_THRESHOLDS_UNRESOLVABLE",
        )
    limit_value = float(rule.limit.value)
    if rule.rule_type is RuleType.MAX_TRADING_DAYS:
        met = counted <= limit_value
        status = EvaluationStatus.PASS if met else EvaluationStatus.BREACH
        explanation = ExplanationCode.REQUIREMENT_MET if met else ExplanationCode.REQUIREMENT_NOT_MET
    else:
        # A minimum not yet reached is NOT a breach: the challenge has simply not
        # accumulated enough days yet, so it stays INDETERMINATE.
        met = counted >= limit_value
        status = EvaluationStatus.PASS if met else EvaluationStatus.INDETERMINATE
        explanation = (
            ExplanationCode.REQUIREMENT_MET if met else ExplanationCode.PHASE_DEPENDENCY_UNMET
        )
    return _result(
        context, rule, status, unit="DAYS", explanation=explanation,
        current_value=float(counted), limit_value=limit_value,
        remaining_buffer=round(abs(limit_value - counted), 8) + 0.0,
        support_status=support, evidence=_evidence_for(state),
        detail=f"CRITERION={criterion.value}:CONSECUTIVE={rule.consecutive.value}",
    )



#: Inactivity counting bases a rule may declare.
INACTIVITY_BASES: frozenset[str] = frozenset({"CALENDAR_DAYS", "TRADING_DAYS"})


def resolve_rule_criterion(rule: Any) -> TradingDayCriterion:
    """The trading-day qualifying criterion a rule EXPLICITLY declares.

    Read from the rule's typed ``qualifying_criterion`` field when the 3A contract
    has one, otherwise from the rule's preserved extension payload under the
    explicit key ``qualifying_criterion``. An absent or unrecognised value yields
    :attr:`TradingDayCriterion.UNSPECIFIED`, which keeps the dependent evaluation
    INDETERMINATE rather than guessing what "a trading day" meant.
    """
    raw = getattr(rule, "qualifying_criterion", None)
    if raw is None:
        payload = getattr(rule, "unknown_payload", None) or {}
        raw = payload.get("qualifying_criterion") if hasattr(payload, "get") else None
    return resolve_criterion(raw)


def resolve_inactivity_basis(rule: Any) -> str | None:
    """The inactivity counting basis the rule EXPLICITLY declares.

    3A's :class:`InactivityRule` has no typed basis field, so the basis is read
    from a rule's preserved extension payload under the explicit key
    ``inactivity_basis``. Anything absent or unrecognised returns ``None``, which
    keeps the evaluation INDETERMINATE.

    This is deliberately NOT a default: whether weekends count is a real
    contractual question, and guessing it would silently misstate inactivity.
    """
    basis = getattr(rule, "inactivity_basis", None)
    if basis is None:
        payload = getattr(rule, "unknown_payload", None) or {}
        basis = payload.get("inactivity_basis") if hasattr(payload, "get") else None
    if basis is None:
        return None
    text = str(basis).strip().upper()
    return text if text in INACTIVITY_BASES else None


def evaluate_inactivity(context: EvaluationContext, rule: Any) -> EvaluationResult:
    """Inactivity from durable trading-day history, in the rule's declared basis.

    A rule that does not declare its basis yields INDETERMINATE. Weekends are
    never assumed to count: only a declared ``TRADING_DAYS`` basis excludes them,
    and only because it walks RECORDED days.
    """
    state = context.state
    support = EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY
    history = state.trading_day_history
    criterion = resolve_rule_criterion(rule)
    basis = resolve_inactivity_basis(rule)
    if history is None:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="DAYS",
            explanation=ExplanationCode.TRADING_DAY_HISTORY_UNAVAILABLE, support_status=support,
            unavailable=(TelemetryRequirement.TRADING_DAY_HISTORY,),
            detail="NO_DURABLE_TRADING_DAY_HISTORY",
        )
    inactivity: InactivityState = derive_inactivity_state(
        account=state.account, account_currency=state.account_currency, history=history,
        definition=context.rule_day_definition, current_rule_day=state.rule_day,
        criterion=criterion, basis=basis,
    )
    if not inactivity.is_evaluable:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="DAYS",
            explanation=(
                ExplanationCode.INACTIVITY_BASIS_UNDECLARED
                if inactivity.basis is None
                else ExplanationCode.INACTIVITY_STATE_INDETERMINATE
            ),
            support_status=support, unavailable=(TelemetryRequirement.TRADING_DAY_HISTORY,),
            detail=inactivity.detail,
        )
    max_days = parse_inactive_days(rule.max_inactive_duration)
    if max_days is None:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="DAYS",
            explanation=ExplanationCode.INACTIVITY_STATE_INDETERMINATE, support_status=support,
            detail="MAX_INACTIVE_DURATION_UNPARSEABLE",
        )
    current = float(inactivity.inactivity_days or 0)
    if rule.requires_daily_activity:
        limit_value = 0.0
        status = EvaluationStatus.PASS if current == 0.0 else EvaluationStatus.BREACH
    else:
        limit_value = float(max_days)
        status = (
            EvaluationStatus.BREACH if current > limit_value + MONEY_EPSILON
            else EvaluationStatus.PASS
        )
    return _result(
        context, rule, status, unit="DAYS",
        explanation=(ExplanationCode.LIMIT_EXCEEDED if status is EvaluationStatus.BREACH
                     else ExplanationCode.WITHIN_LIMIT),
        current_value=current, limit_value=limit_value,
        remaining_buffer=round(limit_value - current, 8) + 0.0,
        support_status=support, evidence=_evidence_for(state),
        detail=(
            f"BASIS={inactivity.basis}:LAST_QUALIFYING="
            f"{inactivity.last_qualifying_day.isoformat() if inactivity.last_qualifying_day else 'NONE'}"
        ),
    )


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# POSITION / LOT / OPEN RISK Ã¢â‚¬â€ from exact Block 2B and 2C telemetry
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def _telemetry_unavailable(state: AccountEvaluationState, field_name: str) -> bool:
    return not state.has(field_name)


def evaluate_position_limit(context: EvaluationContext, rule: Any) -> EvaluationResult:
    """MAX_POSITION_SIZE / MAX_OPEN_POSITIONS from exact Block 2B telemetry.

    Position state is NEVER re-derived here; the state already carries the
    authoritative Block 2B values.
    """
    state = context.state
    support = EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY
    if rule.limit.basis is LimitBasis.LOT_COUNT:
        if _telemetry_unavailable(state, "largest_lot_size"):
            return _result(
                context, rule, EvaluationStatus.INDETERMINATE, unit="LOTS",
                explanation=ExplanationCode.TELEMETRY_MISSING, support_status=support,
                unavailable=(TelemetryRequirement.LOT_SIZE, TelemetryRequirement.OPEN_POSITIONS),
                detail="BLOCK2B_LOT_SIZE_UNAVAILABLE",
            )
        current = float(state.largest_lot_size)
    else:
        if _telemetry_unavailable(state, "position_count"):
            return _result(
                context, rule, EvaluationStatus.INDETERMINATE, unit="POSITIONS",
                explanation=(
                    ExplanationCode.TELEMETRY_STALE if state.stale else ExplanationCode.TELEMETRY_MISSING
                ),
                support_status=support,
                unavailable=(TelemetryRequirement.POSITION_COUNT, TelemetryRequirement.OPEN_POSITIONS),
                detail="BLOCK2B_POSITION_COUNT_UNAVAILABLE",
            )
        current = float(state.position_count)
    limit_value = float(rule.limit.value)
    status = EvaluationStatus.BREACH if current > limit_value + MONEY_EPSILON else EvaluationStatus.PASS
    return _result(
        context, rule, status, unit=("LOTS" if rule.limit.basis is LimitBasis.LOT_COUNT else "POSITIONS"),
        explanation=(ExplanationCode.LIMIT_EXCEEDED if status is EvaluationStatus.BREACH
                     else ExplanationCode.WITHIN_LIMIT),
        current_value=current, limit_value=limit_value,
        remaining_buffer=round(limit_value - current, 8) + 0.0,
        support_status=support, evidence=_evidence_for(state),
    )



#: Which state field each open-risk scope reads.
_RISK_SCOPE_FIELD: Mapping[str, str] = {
    "TOTAL": "total_open_risk",
    "PER_POSITION": "largest_position_risk",
    "PER_SYMBOL": "largest_symbol_risk",
    "CORRELATION_CLUSTER": "correlated_risk",
    "DIRECTIONAL": "directional_risk",
}

#: Which telemetry requirement backs each scope, for the unavailable list.
_RISK_SCOPE_REQUIREMENT: Mapping[str, TelemetryRequirement] = {
    "TOTAL": TelemetryRequirement.OPEN_RISK_TOTAL,
    "PER_POSITION": TelemetryRequirement.OPEN_RISK_PER_POSITION,
    "PER_SYMBOL": TelemetryRequirement.OPEN_RISK_PER_SYMBOL,
    "CORRELATION_CLUSTER": TelemetryRequirement.SYMBOL_CORRELATION_CLUSTERS,
    "DIRECTIONAL": TelemetryRequirement.DIRECTIONAL_EXPOSURE,
}


def evaluate_open_risk_rule(context: EvaluationContext, rule: Any) -> EvaluationResult:
    """MAX_OPEN_RISK / PER_POSITION / PER_SYMBOL / CORRELATED / DIRECTIONAL risk.

    Every value comes from the accepted Block 2B/2C telemetry the state already
    carries. A partial risk floor is NEVER presented as an authoritative total, and
    an incomplete correlation model yields INDETERMINATE rather than an
    understated PASS.
    """
    state = context.state
    support = EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY
    scope = str(getattr(rule, "scope_kind", "TOTAL") or "TOTAL").upper()
    field_name = _RISK_SCOPE_FIELD.get(scope)
    if field_name is None:
        return _result(
            context, rule, EvaluationStatus.UNSUPPORTED, unit="MONEY",
            explanation=ExplanationCode.RULE_TYPE_UNSUPPORTED, support_status=support,
            detail=f"UNKNOWN_RISK_SCOPE:{scope}",
        )
    requirement = _RISK_SCOPE_REQUIREMENT[scope]
    if not state.has(field_name):
        if scope == "CORRELATION_CLUSTER":
            code = ExplanationCode.CORRELATION_NOT_COMPLETE
        elif state.stale:
            code = ExplanationCode.TELEMETRY_STALE
        else:
            code = ExplanationCode.TELEMETRY_MISSING
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="MONEY",
            explanation=code, support_status=support, unavailable=(requirement,),
            detail=f"BLOCK2_{scope}_RISK_UNAVAILABLE",
        )
    try:
        limit_value = resolve_limit(rule.limit, state)
    except _Unresolved as exc:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="MONEY",
            explanation=exc.code, support_status=support, unavailable=exc.unavailable, detail=exc.detail,
        )
    current = float(getattr(state, field_name))
    buffer = round(limit_value - current, 8) + 0.0
    status = EvaluationStatus.BREACH if current > limit_value + MONEY_EPSILON else EvaluationStatus.PASS
    return _result(
        context, rule, status, unit="MONEY",
        explanation=(ExplanationCode.LIMIT_EXCEEDED if status is EvaluationStatus.BREACH
                     else ExplanationCode.WITHIN_LIMIT),
        current_value=current, limit_value=limit_value, remaining_buffer=buffer,
        support_status=support, evidence=_evidence_for(state),
        detail=f"SCOPE={scope}:CURRENCY={state.account_currency}",
    )



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# EXTERNAL-SOURCE RULES Ã¢â‚¬â€ foundations only, never fabricated data
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def _matches_importance(event: Any, levels: Sequence[Any]) -> bool:
    """Whether a calendar event's importance is in the rule's declared levels."""
    importance = getattr(event, "importance", None)
    raw = getattr(importance, "value", importance)
    return raw in {getattr(level, "value", level) for level in levels}


def evaluate_news_rule(context: EvaluationContext, rule: Any) -> EvaluationResult:
    """News restriction foundation.

    Block 3B ships NO economic calendar. Without a real provider the result is
    UNSUPPORTED and no timestamp is invented. With a provider supplied, the
    blackout window the rule declares is applied to real events.
    """
    if not rule.has_explicit_blackout:
        # The rule itself is underspecified, so no verdict exists even if a
        # calendar were wired up. This is checked BEFORE the provider.
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="EVENTS",
            explanation=ExplanationCode.EXTERNAL_SOURCE_REQUIRED,
            support_status=EvaluationSupport.REQUIRES_EXTERNAL_SOURCE,
            unavailable=(TelemetryRequirement.ECONOMIC_CALENDAR,),
            detail="BLACKOUT_WINDOW_NOT_SPECIFIED_BY_RULE",
        )
    if context.economic_calendar is None:
        return _result(
            context, rule, EvaluationStatus.UNSUPPORTED, unit="EVENTS",
            explanation=ExplanationCode.EXTERNAL_SOURCE_REQUIRED,
            support_status=EvaluationSupport.REQUIRES_EXTERNAL_SOURCE,
            unavailable=(TelemetryRequirement.ECONOMIC_CALENDAR,),
            detail="NO_ECONOMIC_CALENDAR_PROVIDER",
        )
    start = context.evaluated_at_utc - timedelta(minutes=int(rule.pre_event_blackout_minutes or 0))
    end = context.evaluated_at_utc + timedelta(minutes=int(rule.post_event_blackout_minutes or 0))
    try:
        events = list(context.economic_calendar.events_in_window(start, end))
    except Exception as exc:  # a failing provider is INDETERMINATE, not a pass
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="EVENTS",
            explanation=ExplanationCode.EXTERNAL_SOURCE_REQUIRED,
            support_status=EvaluationSupport.REQUIRES_EXTERNAL_SOURCE,
            unavailable=(TelemetryRequirement.ECONOMIC_CALENDAR,),
            detail=f"CALENDAR_SOURCE_ERROR:{type(exc).__name__}",
        )
    blocking = [e for e in events if _matches_importance(e, rule.importance_levels)]
    return _result(
        context, rule,
        EvaluationStatus.BREACH if blocking else EvaluationStatus.PASS,
        unit="EVENTS",
        explanation=(ExplanationCode.LIMIT_EXCEEDED if blocking else ExplanationCode.WITHIN_LIMIT),
        current_value=float(len(blocking)), limit_value=0.0,
        support_status=EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY,
        detail=f"TZ={context.rule_day_definition.timezone_name}:EVENTS_IN_WINDOW={len(events)}",
    )



def evaluate_hold_restriction(context: EvaluationContext, rule: Any) -> EvaluationResult:
    """Weekend / overnight hold restriction foundation.

    Market close times are NEVER inferred from a symbol name. Without a real
    session calendar the result is UNSUPPORTED. With one, the exact open position
    set and the rule's own cutoff in the rule timezone decide the outcome.
    """
    state = context.state
    if rule.holding_permitted:
        return _result(
            context, rule, EvaluationStatus.NOT_APPLICABLE, unit="POSITIONS",
            explanation=ExplanationCode.SCOPE_NOT_APPLICABLE,
            support_status=EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY,
            detail="HOLDING_EXPLICITLY_PERMITTED_BY_RULE",
        )
    if context.market_sessions is None:
        return _result(
            context, rule, EvaluationStatus.UNSUPPORTED, unit="POSITIONS",
            explanation=ExplanationCode.EXTERNAL_SOURCE_REQUIRED,
            support_status=EvaluationSupport.REQUIRES_EXTERNAL_SOURCE,
            unavailable=(TelemetryRequirement.BROKER_SYMBOL_SPEC,),
            detail="NO_MARKET_SESSION_CALENDAR_PROVIDER",
        )
    if not state.open_position_tickets:
        return _result(
            context, rule, EvaluationStatus.PASS, unit="POSITIONS",
            explanation=ExplanationCode.WITHIN_LIMIT,
            support_status=EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY,
            current_value=0.0, limit_value=0.0, detail="NO_OPEN_POSITIONS_PROVEN",
        )
    # Only the exact open set the state carries is considered.
    holdings = float(len(state.open_position_tickets))
    cutoff = rule.cutoff_time
    local = context.evaluated_at_utc.astimezone(context.rule_day_definition.zone)
    past_cutoff = cutoff is not None and local.time() >= cutoff
    status = EvaluationStatus.BREACH if past_cutoff else EvaluationStatus.PASS
    return _result(
        context, rule, status, unit="POSITIONS",
        explanation=(ExplanationCode.LIMIT_EXCEEDED if past_cutoff else ExplanationCode.WITHIN_LIMIT),
        current_value=holdings, limit_value=0.0,
        support_status=EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY,
        evidence=tuple(f"ticket:{t}" for t in state.open_position_tickets),
        detail=f"TZ={context.rule_day_definition.timezone_name}:CUTOFF={cutoff}:LOCAL={local.isoformat()}",
    )


def evaluate_consistency(context: EvaluationContext, rule: Any) -> EvaluationResult:
    """Consistency rule from the durable daily profit series.

    Evaluated ONLY when both the numerator and the denominator are authoritative.
    A partially-known series yields INDETERMINATE rather than a flattering ratio.
    """
    state = context.state
    support = EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY
    history = state.trading_day_history
    if history is None:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="PERCENT",
            explanation=ExplanationCode.CONSISTENCY_DENOMINATOR_UNAVAILABLE, support_status=support,
            unavailable=(TelemetryRequirement.DAILY_PROFIT_SERIES,),
            detail="NO_DURABLE_DAILY_PROFIT_SERIES",
        )
    series = history.daily_profit_series(up_to_rule_day=state.rule_day)
    if not series:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="PERCENT",
            explanation=ExplanationCode.CONSISTENCY_DENOMINATOR_UNAVAILABLE, support_status=support,
            unavailable=(TelemetryRequirement.DAILY_PROFIT_SERIES,),
            detail="EMPTY_DAILY_PROFIT_SERIES",
        )
    observed = len(history.records)
    if observed != len(series):
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="PERCENT",
            explanation=ExplanationCode.CONSISTENCY_DENOMINATOR_UNAVAILABLE, support_status=support,
            unavailable=(TelemetryRequirement.DAILY_PROFIT_SERIES,),
            detail=f"DAILY_SERIES_PARTIAL:{len(series)}_OF_{observed}",
        )
    total = sum(value for _day, value in series)
    best = max(series, key=lambda item: item[1])
    if total == 0.0:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="PERCENT",
            explanation=ExplanationCode.CONSISTENCY_DENOMINATOR_UNAVAILABLE, support_status=support,
            detail="TOTAL_PROFIT_ZERO_DENOMINATOR_UNDEFINED",
        )
    share = (best[1] / total) * 100.0 if total > 0 else None
    if share is None:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="PERCENT",
            explanation=ExplanationCode.CONSISTENCY_DENOMINATOR_UNAVAILABLE, support_status=support,
            detail="SHARE_NOT_DEFINED_FOR_NEGATIVE_TOTAL",
        )
    limit_value = float(rule.limit.value)
    status = EvaluationStatus.BREACH if share > limit_value + MONEY_EPSILON else EvaluationStatus.PASS
    return _result(
        context, rule, status, unit="PERCENT",
        explanation=(ExplanationCode.LIMIT_EXCEEDED if status is EvaluationStatus.BREACH
                     else ExplanationCode.WITHIN_LIMIT),
        current_value=share, limit_value=limit_value,
        remaining_buffer=round(limit_value - share, 8) + 0.0,
        support_status=support, evidence=_evidence_for(state),
        detail=f"METRIC={rule.metric}:BEST_DAY={best[0].isoformat()}:DAYS={len(series)}",
    )



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# PAYOUT / REFUND / RESET RULES Ã¢â‚¬â€ factual only, never triggering
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def evaluate_payout_rule(context: EvaluationContext, rule: Any) -> EvaluationResult:
    """Factual payout-eligibility evidence only. NEVER triggers a payout.

    Anything requiring account-lifecycle or external state this system does not
    hold returns INDETERMINATE / UNSUPPORTED rather than an optimistic PASS.
    """
    state = context.state
    if getattr(rule, "minimum_profit", None) is None:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="MONEY",
            explanation=ExplanationCode.STATE_FIELD_UNAVAILABLE,
            support_status=EvaluationSupport.REQUIRES_NEW_STATE,
            unavailable=(TelemetryRequirement.CHALLENGE_PHASE_STATE,),
            detail="PAYOUT_MINIMUM_PROFIT_NOT_DECLARED",
        )
    history = state.trading_day_history
    if history is None:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="MONEY",
            explanation=ExplanationCode.TRADING_DAY_HISTORY_UNAVAILABLE,
            support_status=EvaluationSupport.REQUIRES_NEW_STATE,
            unavailable=(
                TelemetryRequirement.CHALLENGE_PHASE_STATE,
                TelemetryRequirement.DAILY_PROFIT_SERIES,
            ),
            detail="PAYOUT_REQUIRES_DURABLE_PROFIT_SERIES",
        )
    if rule.requires_trading_days_rule and rule.minimum_profitable_days:
        profitable = sum(
            1 for _d, value in history.daily_profit_series(up_to_rule_day=state.rule_day) if value > 0
        )
        if profitable < int(rule.minimum_profitable_days):
            return _result(
                context, rule, EvaluationStatus.INDETERMINATE, unit="DAYS",
                explanation=ExplanationCode.PHASE_DEPENDENCY_UNMET,
                support_status=EvaluationSupport.REQUIRES_NEW_STATE,
                current_value=float(profitable), limit_value=float(rule.minimum_profitable_days),
                detail="MIN_PROFITABLE_DAYS_NOT_MET",
            )
    # Payout cadence itself is an account-lifecycle fact this block does not own.
    return _result(
        context, rule, EvaluationStatus.INDETERMINATE, unit="MONEY",
        explanation=ExplanationCode.STATE_FIELD_UNAVAILABLE,
        support_status=EvaluationSupport.REQUIRES_NEW_STATE,
        unavailable=(TelemetryRequirement.CHALLENGE_PHASE_STATE,),
        detail="PAYOUT_CADENCE_REQUIRES_ACCOUNT_LIFECYCLE_STATE",
    )


def evaluate_not_implemented(context: EvaluationContext, rule: Any) -> EvaluationResult:
    """A rule type this block deliberately does not evaluate.

    Returned UNSUPPORTED with the 3A support metadata intact. It is never
    silently dropped from a pack's results.
    """
    from core.risk.prop_rule_contracts import support_for

    return _result(
        context, rule, EvaluationStatus.UNSUPPORTED, unit="N/A",
        explanation=ExplanationCode.RULE_TYPE_UNSUPPORTED,
        support_status=support_for(rule.rule_type),
        unavailable=tuple(rule.telemetry_requirements),
        detail=f"RULE_TYPE={rule.rule_type.value}",
    )


def _evidence_for(state: AccountEvaluationState) -> tuple[str, ...]:
    """The exact durable records a decision read."""
    evidence: list[str] = []
    if state.initial_anchor is not None:
        evidence.append(f"initial_anchor:{state.initial_anchor.anchor_id}")
    if state.daily_anchor is not None:
        evidence.append(f"daily_anchor:{state.daily_anchor.anchor_id}")
    if state.daily_ledger is not None:
        evidence.append(f"ledger:{state.daily_ledger.ledger_id}")
    if state.account_snapshot_id:
        evidence.append(f"account_snapshot:{state.account_snapshot_id}")
    if state.open_risk_snapshot_id:
        evidence.append(f"open_risk:{state.open_risk_snapshot_id}")
    if state.portfolio_exposure_id:
        evidence.append(f"portfolio:{state.portfolio_exposure_id}")
    if state.high_water is not None and state.high_water.observation_count:
        evidence.append(f"high_water_observations:{state.high_water.observation_count}")
    return tuple(evidence)



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# SUPPORT MATRIX Ã¢â‚¬â€ every 3A rule type is classified, none disappears
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


#: Rule types this block evaluates against durable state / Block 2 telemetry.
_EVALUATED_BY_3B: frozenset[RuleType] = frozenset(
    {
        RuleType.DAILY_LOSS_LIMIT,
        RuleType.DAILY_PROFIT_LIMIT,
        RuleType.MAX_DRAWDOWN,
        RuleType.STATIC_DRAWDOWN,
        RuleType.TRAILING_DRAWDOWN,
        RuleType.PROFIT_TARGET,
        RuleType.MIN_TRADING_DAYS,
        RuleType.MAX_TRADING_DAYS,
        RuleType.INACTIVITY_RULE,
        RuleType.MAX_POSITION_SIZE,
        RuleType.MAX_OPEN_POSITIONS,
        RuleType.MAX_OPEN_RISK,
        RuleType.MAX_RISK_PER_POSITION,
        RuleType.MAX_RISK_PER_SYMBOL,
        RuleType.MAX_CORRELATED_RISK,
        RuleType.MAX_DIRECTIONAL_RISK,
        RuleType.CONSISTENCY_RULE,
        RuleType.PAYOUT_ELIGIBILITY,
    }
)

#: Rule types whose evaluation needs an EXTERNAL source this block does not ship.
#:
#: 3A declared these REQUIRES_EXTERNAL_SOURCE or NOT_IMPLEMENTED. Block 3B does
#: NOT silently upgrade them merely because a Protocol now exists: a protocol is
#: an interface, not a data source, and no source is wired in this block.
_REQUIRES_EXTERNAL: frozenset[RuleType] = frozenset(
    {
        RuleType.NEWS_TRADING_RESTRICTION,
        RuleType.WEEKEND_HOLD_RESTRICTION,
        RuleType.OVERNIGHT_HOLD_RESTRICTION,
        RuleType.IP_DEVICE_LOCATION_RESTRICTION,
        RuleType.EA_AUTOMATION_PERMISSION,
        RuleType.COPY_TRADING_RESTRICTION,
        RuleType.REFUND_RULE,
        RuleType.RESET_RULE,
    }
)


def rule_type_evaluation_support(rule_type: RuleType) -> str:
    """Classify every 3A rule type for the 3B support matrix.

    * ``EVALUATED``          -- decided from durable state / Block 2 telemetry.
    * ``REQUIRES_EXTERNAL``  -- 3A said it needs an external source; not upgraded.
    * ``NOT_EVALUATED``      -- modelled but out of scope for this block.
    """
    if rule_type in _EVALUATED_BY_3B:
        return "EVALUATED"
    if rule_type in _REQUIRES_EXTERNAL:
        return "REQUIRES_EXTERNAL"
    return "NOT_EVALUATED"


def support_matrix_3b() -> dict[RuleType, str]:
    """The COMPLETE 3A -> 3B support matrix. Every rule type is present."""
    return {rule_type: rule_type_evaluation_support(rule_type) for rule_type in RuleType}


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# DISPATCH
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def evaluate_rule(context: EvaluationContext, rule: RuleBase) -> EvaluationResult:
    """Evaluate ONE rule against ONE state. Pure and read-only.

    ORDERING OF PRECONDITIONS
    -------------------------
    1. A rule that is disabled is NOT_APPLICABLE.
    2. A rule not in force at the evaluated instant is NOT_APPLICABLE, which is
       what stops a later rule being applied retroactively.
    3. A state in CONFLICT makes every rule INDETERMINATE: nothing is evaluated
       against contradictory evidence.
    """
    if not rule.enabled:
        return _result(
            context, rule, EvaluationStatus.NOT_APPLICABLE, unit="N/A",
            explanation=ExplanationCode.RULE_DISABLED,
            support_status=EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY,
            detail="RULE_DISABLED_IN_PACK",
        )
    if not rule.is_effective_at(context.evaluated_at_utc):
        return _result(
            context, rule, EvaluationStatus.NOT_APPLICABLE, unit="N/A",
            explanation=ExplanationCode.RULE_NOT_EFFECTIVE,
            support_status=EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY,
            detail="RULE_NOT_IN_FORCE_AT_EVALUATED_INSTANT",
        )
    if context.state.status is StateStatus.CONFLICT:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="N/A",
            explanation=ExplanationCode.INITIAL_ANCHOR_CONFLICT,
            support_status=EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY,
            detail=context.state.conflict_detail or "STATE_IN_CONFLICT",
        )
    if context.state.stale:
        return _result(
            context, rule, EvaluationStatus.INDETERMINATE, unit="N/A",
            explanation=ExplanationCode.TELEMETRY_STALE,
            support_status=EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY,
            detail="EVIDENCE_STALE",
        )

    handler = _HANDLERS.get(rule.rule_type, evaluate_not_implemented)
    return handler(context, rule)


def evaluate_pack(context: EvaluationContext) -> tuple[EvaluationResult, ...]:
    """Evaluate every rule in force in the bound pack, in deterministic order.

    Disabled and out-of-force rules still produce a NOT_APPLICABLE result, so no
    rule ever silently disappears from a pack's output.
    """
    return tuple(
        evaluate_rule(context, rule) for rule in sorted(context.rules_in_force(), key=lambda r: r.rule_id)
    )



#: The ONE dispatch table. A rule type absent here is UNSUPPORTED, never ignored.
_HANDLERS: Mapping[RuleType, Any] = {
    RuleType.DAILY_LOSS_LIMIT: evaluate_daily_loss,
    RuleType.DAILY_PROFIT_LIMIT: evaluate_daily_profit,
    RuleType.STATIC_DRAWDOWN: evaluate_static_drawdown,
    RuleType.MAX_DRAWDOWN: evaluate_static_drawdown,
    RuleType.TRAILING_DRAWDOWN: evaluate_trailing_drawdown,
    RuleType.PROFIT_TARGET: evaluate_profit_target,
    RuleType.MIN_TRADING_DAYS: evaluate_trading_day_rule,
    RuleType.MAX_TRADING_DAYS: evaluate_trading_day_rule,
    RuleType.INACTIVITY_RULE: evaluate_inactivity,
    RuleType.MAX_POSITION_SIZE: evaluate_position_limit,
    RuleType.MAX_OPEN_POSITIONS: evaluate_position_limit,
    RuleType.MAX_OPEN_RISK: evaluate_open_risk_rule,
    RuleType.MAX_RISK_PER_POSITION: evaluate_open_risk_rule,
    RuleType.MAX_RISK_PER_SYMBOL: evaluate_open_risk_rule,
    RuleType.MAX_CORRELATED_RISK: evaluate_open_risk_rule,
    RuleType.MAX_DIRECTIONAL_RISK: evaluate_open_risk_rule,
    RuleType.CONSISTENCY_RULE: evaluate_consistency,
    RuleType.PAYOUT_ELIGIBILITY: evaluate_payout_rule,
    RuleType.NEWS_TRADING_RESTRICTION: evaluate_news_rule,
    RuleType.WEEKEND_HOLD_RESTRICTION: evaluate_hold_restriction,
    RuleType.OVERNIGHT_HOLD_RESTRICTION: evaluate_hold_restriction,
}


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# HISTORICAL REPLAY Ã¢â‚¬â€ bounded, deterministic, side-effect free
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


#: Hard bound on one replay, so a bad range can never exhaust memory.
MAX_REPLAY_RESULTS = 200_000


def replay_account_evaluations(
    *,
    account: AccountKey,
    states_by_instant: Mapping[datetime, AccountEvaluationState],
    definition: RuleDayDefinition,
    resolve_pack: Any,
    evaluated_instants: Sequence[datetime] | None = None,
    economic_calendar: EconomicCalendarSource | None = None,
    market_sessions: MarketSessionSource | None = None,
    fx_conversion_source: str | None = None,
) -> tuple[EvaluationResult, ...]:
    """Bounded historical replay for ONE account over a set of instants.

    DETERMINISTIC AND SIDE-EFFECT FREE
    ---------------------------------
    * ``resolve_pack`` is called for each instant and must return the pack in
      force THEN, so a rule version boundary splits the output correctly with no
      retroactive application.
    * Results are ordered by ``(evaluated_at_utc, rule_id)``, so the output is
      byte-identical across runs.
    * Nothing is written, nothing is mutated, and no clock is read. Replay is a
      pure function of its inputs.

    The caller supplies ``states_by_instant``; this function never fetches data.
    """
    instants = sorted(
        evaluated_instants if evaluated_instants is not None else states_by_instant.keys()
    )
    results: list[EvaluationResult] = []
    for moment in instants:
        state = states_by_instant.get(moment)
        if state is None:
            continue
        pack = resolve_pack(moment)
        if pack is None:
            continue
        context = EvaluationContext(
            rule_pack=pack,
            state=state,
            evaluated_at_utc=moment,
            rule_day_definition=definition,
            state_lineage={"replay": True},
            economic_calendar=economic_calendar,
            market_sessions=market_sessions,
            fx_conversion_source=fx_conversion_source,
        )
        results.extend(evaluate_pack(context))
        if len(results) > MAX_REPLAY_RESULTS:
            raise ValueError(f"REPLAY_BOUND_EXCEEDED:{MAX_REPLAY_RESULTS}")
    return tuple(
        sorted(results, key=lambda r: (r.evaluated_at_utc, r.rule_id, r.evaluation_id))
    )
