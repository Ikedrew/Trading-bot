"""Prop rule-pack controlled vocabulary (Block 3A).

Pure enum vocabulary shared by the canonical prop rule model. Every concept a
rule can express is an explicit named value here, so a rule pack never has to
fall back on an untyped string or an invented default.

This module defines vocabularies ONLY. It contains no rule logic, no evaluation,
no side effect and no trading dependency.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping


class RuleType(str, Enum):
    """Every prop rule concept the model can represent.

    Absence is explicit: a firm with no news rule simply declares no
    ``NEWS_TRADING_RESTRICTION`` rule. No default is ever invented.
    """

    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    DAILY_PROFIT_LIMIT = "DAILY_PROFIT_LIMIT"
    MAX_DRAWDOWN = "MAX_DRAWDOWN"
    TRAILING_DRAWDOWN = "TRAILING_DRAWDOWN"
    STATIC_DRAWDOWN = "STATIC_DRAWDOWN"
    PROFIT_TARGET = "PROFIT_TARGET"
    MIN_TRADING_DAYS = "MIN_TRADING_DAYS"
    MAX_TRADING_DAYS = "MAX_TRADING_DAYS"
    INACTIVITY_RULE = "INACTIVITY_RULE"
    MAX_POSITION_SIZE = "MAX_POSITION_SIZE"
    MAX_OPEN_POSITIONS = "MAX_OPEN_POSITIONS"
    MAX_OPEN_RISK = "MAX_OPEN_RISK"
    MAX_RISK_PER_POSITION = "MAX_RISK_PER_POSITION"
    MAX_RISK_PER_SYMBOL = "MAX_RISK_PER_SYMBOL"
    MAX_CORRELATED_RISK = "MAX_CORRELATED_RISK"
    MAX_DIRECTIONAL_RISK = "MAX_DIRECTIONAL_RISK"
    NEWS_TRADING_RESTRICTION = "NEWS_TRADING_RESTRICTION"
    WEEKEND_HOLD_RESTRICTION = "WEEKEND_HOLD_RESTRICTION"
    OVERNIGHT_HOLD_RESTRICTION = "OVERNIGHT_HOLD_RESTRICTION"
    CONSISTENCY_RULE = "CONSISTENCY_RULE"
    EA_AUTOMATION_PERMISSION = "EA_AUTOMATION_PERMISSION"
    COPY_TRADING_RESTRICTION = "COPY_TRADING_RESTRICTION"
    IP_DEVICE_LOCATION_RESTRICTION = "IP_DEVICE_LOCATION_RESTRICTION"
    PAYOUT_ELIGIBILITY = "PAYOUT_ELIGIBILITY"
    PAYOUT_FREQUENCY = "PAYOUT_FREQUENCY"
    MIN_PROFIT_FOR_PAYOUT = "MIN_PROFIT_FOR_PAYOUT"
    REFUND_RULE = "REFUND_RULE"
    RESET_RULE = "RESET_RULE"
    UNKNOWN_EXTENSION = "UNKNOWN_EXTENSION"


class LimitBasis(str, Enum):
    """WHAT a numeric limit is a percentage OF.

    "5% drawdown" is not a rule. "5% of initial balance" is.
    """

    ABSOLUTE_MONEY = "ABSOLUTE_MONEY"
    PERCENT_INITIAL_BALANCE = "PERCENT_INITIAL_BALANCE"
    PERCENT_INITIAL_EQUITY = "PERCENT_INITIAL_EQUITY"
    PERCENT_CURRENT_BALANCE = "PERCENT_CURRENT_BALANCE"
    PERCENT_CURRENT_EQUITY = "PERCENT_CURRENT_EQUITY"
    PERCENT_START_OF_DAY_BALANCE = "PERCENT_START_OF_DAY_BALANCE"
    PERCENT_START_OF_DAY_EQUITY = "PERCENT_START_OF_DAY_EQUITY"
    PERCENT_HIGH_WATER_EQUITY = "PERCENT_HIGH_WATER_EQUITY"
    PERCENT_HIGH_WATER_BALANCE = "PERCENT_HIGH_WATER_BALANCE"
    LOT_COUNT = "LOT_COUNT"
    POSITION_COUNT = "POSITION_COUNT"
    RISK_PERCENT = "RISK_PERCENT"
    CALENDAR_DAYS = "CALENDAR_DAYS"
    TRADING_DAYS = "TRADING_DAYS"


#: Bases whose value is a money amount and therefore demands currency semantics.
MONETARY_BASES: frozenset[LimitBasis] = frozenset(
    {
        LimitBasis.ABSOLUTE_MONEY,
        LimitBasis.PERCENT_INITIAL_BALANCE,
        LimitBasis.PERCENT_INITIAL_EQUITY,
        LimitBasis.PERCENT_CURRENT_BALANCE,
        LimitBasis.PERCENT_CURRENT_EQUITY,
        LimitBasis.PERCENT_START_OF_DAY_BALANCE,
        LimitBasis.PERCENT_START_OF_DAY_EQUITY,
        LimitBasis.PERCENT_HIGH_WATER_EQUITY,
        LimitBasis.PERCENT_HIGH_WATER_BALANCE,
        LimitBasis.RISK_PERCENT,
    }
)

#: Bases expressed as a percentage. The value must be > 0 and <= 100.
PERCENT_BASES: frozenset[LimitBasis] = frozenset(
    b for b in LimitBasis if b.name.startswith("PERCENT_")
)

#: Bases expressed as a count. The value must be a positive whole number.
COUNT_BASES: frozenset[LimitBasis] = frozenset(
    {
        LimitBasis.LOT_COUNT,
        LimitBasis.POSITION_COUNT,
        LimitBasis.CALENDAR_DAYS,
        LimitBasis.TRADING_DAYS,
    }
)


class CurrencySemantics(str, Enum):
    """Which money a monetary rule is denominated in."""

    ACCOUNT_CURRENCY = "ACCOUNT_CURRENCY"
    FIXED_RULE_CURRENCY = "FIXED_RULE_CURRENCY"
    CONVERTED_REFERENCE_CURRENCY = "CONVERTED_REFERENCE_CURRENCY"


class RulePhase(str, Enum):
    """Challenge phase. Phases are NEVER flattened together."""

    EVALUATION_PHASE_1 = "EVALUATION_PHASE_1"
    EVALUATION_PHASE_2 = "EVALUATION_PHASE_2"
    FUNDED = "FUNDED"
    SIMULATED_FUNDED = "SIMULATED_FUNDED"
    EXPRESS = "EXPRESS"
    INSTANT = "INSTANT"


class RulePackStatus(str, Enum):
    """Usability of a whole rule pack. Never silently coerced to usable."""

    VALID = "VALID"
    INCOMPLETE = "INCOMPLETE"
    INVALID = "INVALID"
    UNSUPPORTED = "UNSUPPORTED"
    AMBIGUOUS = "AMBIGUOUS"
    EXPIRED = "EXPIRED"


#: Only VALID may be used to evaluate a challenge. Everything else fails closed.
USABLE_PACK_STATUSES: frozenset[RulePackStatus] = frozenset({RulePackStatus.VALID})


class RuleStatus(str, Enum):
    """Usability of ONE rule inside a pack."""

    VALID = "VALID"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID = "INVALID"
    DISABLED = "DISABLED"


class RuleSeverity(str, Enum):
    """Consequence a breach is expected to have, as stated by the source."""

    ADVISORY = "ADVISORY"
    WARNING = "WARNING"
    BREACH = "BREACH"
    TERMINATION = "TERMINATION"


class BreachKind(str, Enum):
    """Whether crossing a limit is a soft warning or a hard breach."""

    SOFT = "SOFT"
    HARD = "HARD"


class EvaluationSupport(str, Enum):
    """Whether this system can evaluate the rule TODAY.

    This is NOT rule validity. A perfectly valid news rule is still
    ``REQUIRES_EXTERNAL_SOURCE`` because no economic calendar is wired yet.
    """

    SUPPORTED_BY_CURRENT_TELEMETRY = "SUPPORTED_BY_CURRENT_TELEMETRY"
    REQUIRES_NEW_STATE = "REQUIRES_NEW_STATE"
    REQUIRES_EXTERNAL_SOURCE = "REQUIRES_EXTERNAL_SOURCE"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    UNSUPPORTED = "UNSUPPORTED"


class EvaluationTimeBasis(str, Enum):
    """When a rule is measured, which fixes the timezone it needs."""

    CONTINUOUS_INTRADAY = "CONTINUOUS_INTRADAY"
    END_OF_DAY = "END_OF_DAY"
    PER_TRADE = "PER_TRADE"
    PER_DAY = "PER_DAY"
    EVENT_DRIVEN = "EVENT_DRIVEN"
    NOT_TIME_SENSITIVE = "NOT_TIME_SENSITIVE"


#: Time bases that bind to an explicit IANA timezone.
TIMEZONE_BOUND_TIME_BASES: frozenset[EvaluationTimeBasis] = frozenset(
    {
        EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        EvaluationTimeBasis.END_OF_DAY,
        EvaluationTimeBasis.PER_DAY,
        EvaluationTimeBasis.EVENT_DRIVEN,
    }
)


class PrecedenceLevel(int, Enum):
    """Deterministic override order. Higher wins. Never "last one wins"."""

    FIRM_DEFAULT = 10
    PROGRAM = 20
    PHASE = 30
    ACCOUNT_SIZE = 40
    INSTRUMENT = 50


class SourceType(str, Enum):
    """Provenance kind for a governed definition."""

    CONTRACT_TERMS = "CONTRACT_TERMS"
    OFFICIAL_RULE_PAGE = "OFFICIAL_RULE_PAGE"
    OFFICIAL_FAQ = "OFFICIAL_FAQ"
    OFFICIAL_SUPPORT_CLARIFICATION = "OFFICIAL_SUPPORT_CLARIFICATION"
    THIRD_PARTY_COMPARISON = "THIRD_PARTY_COMPARISON"
    MANUAL_ASSUMPTION = "MANUAL_ASSUMPTION"
    IMPORTED_SNAPSHOT = "IMPORTED_SNAPSHOT"


#: Higher number == LOWER authority. Disagreement is exposed, never reconciled.
SOURCE_AUTHORITY: Mapping[SourceType, int] = {
    SourceType.CONTRACT_TERMS: 100,
    SourceType.OFFICIAL_RULE_PAGE: 90,
    SourceType.OFFICIAL_FAQ: 80,
    SourceType.OFFICIAL_SUPPORT_CLARIFICATION: 70,
    SourceType.IMPORTED_SNAPSHOT: 50,
    SourceType.THIRD_PARTY_COMPARISON: 40,
    SourceType.MANUAL_ASSUMPTION: 10,
}

#: Sources that may be presented as an official provider statement.
OFFICIAL_SOURCE_TYPES: frozenset[SourceType] = frozenset(
    {
        SourceType.CONTRACT_TERMS,
        SourceType.OFFICIAL_RULE_PAGE,
        SourceType.OFFICIAL_FAQ,
        SourceType.OFFICIAL_SUPPORT_CLARIFICATION,
    }
)


class NewsImportance(str, Enum):
    """Economic-calendar event importance levels."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class HoldRestrictionKind(str, Enum):
    WEEKEND_HOLD = "WEEKEND_HOLD"
    OVERNIGHT_HOLD = "OVERNIGHT_HOLD"


class AutomationVerdict(str, Enum):
    """Explicit policy verdict. ``UNSPECIFIED`` is a real, modelled state."""

    ALLOWED = "ALLOWED"
    FORBIDDEN = "FORBIDDEN"
    UNSPECIFIED = "UNSPECIFIED"


class DrawdownKind(str, Enum):
    """Static and trailing drawdown are never blended."""

    STATIC = "STATIC"
    TRAILING = "TRAILING"


class DailyResetPolicy(str, Enum):
    """Whether a daily limit resets, and how the day is bounded."""

    NONE = "NONE"
    CALENDAR_DAY = "CALENDAR_DAY"
    ROLLING_24H = "ROLLING_24H"
    EXCHANGE_DAY = "EXCHANGE_DAY"
    SESSION_DAY = "SESSION_DAY"


class DayCountBasis(str, Enum):
    CALENDAR_DAYS = "CALENDAR_DAYS"
    BUSINESS_DAYS = "BUSINESS_DAYS"
    TRADING_DAYS = "TRADING_DAYS"


class ConsecutiveMode(str, Enum):
    CONSECUTIVE = "CONSECUTIVE"
    NON_CONSECUTIVE = "NON_CONSECUTIVE"


class PayoutCadence(str, Enum):
    NONE = "NONE"
    WEEKLY = "WEEKLY"
    BIWEEKLY = "BIWEEKLY"
    MONTHLY = "MONTHLY"
    CUSTOM = "CUSTOM"
