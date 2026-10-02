"""Typed prop rule contracts (Block 3A).

Every prop rule concept the system can represent has ONE explicit, immutable,
typed dataclass here. Each one states exactly what the rule means, what
evidence it needs, and which timezone/currency semantics it is bound to.

RULES ARE MODELLED, NOT CALCULATED
-----------------------------------
This module defines the SHAPE of every rule. It does not compute a daily loss,
a drawdown, a high-water mark, a trading-day count, a payout or a breach. Block
3B owns state and historical evaluation; Block 3C owns enforcement. Nothing here
imports a guard, a broker, an execution path or the clock.

DESIGN RULES
------------
* NO UNTYPE DICT SOUP. Every rule is a frozen, typed dataclass.
* ABSENCE IS EXPLICIT. A firm without a news rule declares no news rule; it does
  not get an invented permissive default.
* NO INVENTED NUMBERS. If a source does not state a blackout window, the field
  is ``None`` and the rule is AMBIGUOUS at validation -- minutes are never
  guessed.
* STATIC AND TRAILING DRAWDOWN ARE SEPARATE TYPES and can never be blended.
* WEEKEND HOLD AND OVERNIGHT HOLD ARE SEPARATE TYPES. "No weekend hold" does NOT
  imply "no overnight hold".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Mapping

from core.risk.prop_rule_enums import (
    AutomationVerdict,
    BreachKind,
    ConsecutiveMode,
    CurrencySemantics,
    DailyResetPolicy,
    DayCountBasis,
    DrawdownKind,
    EvaluationSupport,
    EvaluationTimeBasis,
    HoldRestrictionKind,
    LimitBasis,
    NewsImportance,
    PayoutCadence,
    PrecedenceLevel,
    RuleSeverity,
    RuleStatus,
    RuleType,
    SourceType,
)
from core.risk.prop_rule_telemetry import TelemetryRequirement
from core.risk.prop_rule_values import (
    Limit,
    RuleBase,
    RulePackValidationError,
    RuleSource,
    _require_text,
    validate_timezone,
)

# â”€â”€â”€ REUSABLE COMPONENTS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass(frozen=True)
class PnLComponents:
    """Exactly which P&L components a monetary limit is measured against.

    A rule that says "daily loss" without stating whether floating P&L, swap or
    commission count is not a usable rule; this type forces the statement.
    """

    include_closed_pnl: bool = True
    include_floating_pnl: bool = False
    include_commission: bool = True
    include_swap: bool = True
    include_fees: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "include_closed_pnl": self.include_closed_pnl,
            "include_floating_pnl": self.include_floating_pnl,
            "include_commission": self.include_commission,
            "include_swap": self.include_swap,
            "include_fees": self.include_fees,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PnLComponents":
        return cls(
            include_closed_pnl=bool(payload.get("include_closed_pnl", True)),
            include_floating_pnl=bool(payload.get("include_floating_pnl", False)),
            include_commission=bool(payload.get("include_commission", True)),
            include_swap=bool(payload.get("include_swap", True)),
            include_fees=bool(payload.get("include_fees", True)),
        )


@dataclass(frozen=True)
class ScopeOverride:
    """A rule narrowed to specific symbols and/or asset classes.

    This is the INSTRUMENT-level precedence layer. It is modelled separately
    from the rule itself so precedence resolution can be deterministic rather
    than "whichever was declared last".
    """

    symbols: tuple[str, ...] = ()
    asset_classes: tuple[str, ...] = ()
    applies_to_directions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbols", tuple(self.symbols))
        object.__setattr__(self, "asset_classes", tuple(self.asset_classes))
        object.__setattr__(self, "applies_to_directions", tuple(self.applies_to_directions))
        if not (self.symbols or self.asset_classes or self.applies_to_directions):
            raise RulePackValidationError("SCOPE_OVERRIDE_MUST_NARROW_SOMETHING")

    def matches(self, symbol: str | None, asset_class: str | None = None) -> bool:
        """Whether this override applies to a concrete instrument."""
        if self.symbols and symbol in self.symbols:
            return True
        if self.asset_classes and asset_class in self.asset_classes:
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbols": list(self.symbols),
            "asset_classes": list(self.asset_classes),
            "applies_to_directions": list(self.applies_to_directions),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ScopeOverride":
        return cls(
            symbols=tuple(payload.get("symbols", ())),
            asset_classes=tuple(payload.get("asset_classes", ())),
            applies_to_directions=tuple(payload.get("applies_to_directions", ())),
        )


# â”€â”€â”€ DAILY LOSS / DAILY PROFIT â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass(frozen=True)
class DailyLossRule(RuleBase):
    """Daily loss limit, modelled but NOT calculated.

    Exact semantics captured: threshold, basis (e.g. start-of-day BALANCE vs
    start-of-day EQUITY -- different firms genuinely differ), reset timezone and
    clock, which P&L components count, hard vs soft, and whether a breach
    persists through the day.

    The reset timezone is MANDATORY. A daily loss limit with no timezone cannot
    be evaluated, because "the day" is undefined without it.
    """

    limit: Limit = None  # type: ignore[assignment]
    reset_policy: DailyResetPolicy = DailyResetPolicy.CALENDAR_DAY
    reset_time: time | None = None
    pnl_components: PnLComponents = PnLComponents()
    breach_stops_trading_for_day: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.limit, Limit):
            raise RulePackValidationError("DAILY_LOSS_REQUIRES_LIMIT")
        if self.reset_policy is not DailyResetPolicy.NONE and not self.timezone_name:
            raise RulePackValidationError("DAILY_RESET_TIMEZONE_REQUIRED")
        if self.reset_policy is DailyResetPolicy.NONE and self.reset_time is not None:
            raise RulePackValidationError("RESET_TIME_WITHOUT_RESET_POLICY")

    @property
    def basis(self) -> LimitBasis:
        return self.limit.basis


@dataclass(frozen=True)
class DailyProfitRule(RuleBase):
    """Optional daily profit cap. Same contract shape as the daily loss rule."""

    limit: Limit = None  # type: ignore[assignment]
    reset_policy: DailyResetPolicy = DailyResetPolicy.CALENDAR_DAY
    reset_time: time | None = None
    pnl_components: PnLComponents = PnLComponents()

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.limit, Limit):
            raise RulePackValidationError("DAILY_PROFIT_REQUIRES_LIMIT")
        if self.reset_policy is not DailyResetPolicy.NONE and not self.timezone_name:
            raise RulePackValidationError("DAILY_RESET_TIMEZONE_REQUIRED")


# â”€â”€â”€ DRAWDOWN â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass(frozen=True)
class DrawdownRule(RuleBase):
    """Max drawdown, with STATIC and TRAILING expressed as DISTINCT kinds.

    A static drawdown is measured against a FIXED anchor (initial balance or
    initial equity). A trailing drawdown is measured against a MOVING high-water
    mark whose update cadence, reference quantity and lock behaviour are all
    explicit. The two are never blended into one ambiguous "max drawdown".
    """

    kind: DrawdownKind = DrawdownKind.STATIC
    limit: Limit = None  # type: ignore[assignment]
    anchor: LimitBasis | None = None
    trail_reference: str | None = None
    trail_update_cadence: str | None = None
    trail_locks_at_breach: bool = False
    trail_ceiling: LimitBasis | None = None
    pnl_components: PnLComponents = PnLComponents()

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.kind, DrawdownKind):
            raise RulePackValidationError("DRAWDOWN_KIND_REQUIRED")
        if not isinstance(self.limit, Limit):
            raise RulePackValidationError("DRAWDOWN_REQUIRES_LIMIT")
        if self.anchor is None:
            raise RulePackValidationError("DRAWDOWN_REQUIRES_REFERENCE_ANCHOR")
        if self.kind is DrawdownKind.STATIC:
            if self.trail_reference is not None or self.trail_update_cadence is not None:
                raise RulePackValidationError("STATIC_DRAWDOWN_MUST_NOT_DECLARE_TRAIL")
            if self.anchor not in {
                LimitBasis.PERCENT_INITIAL_BALANCE,
                LimitBasis.PERCENT_INITIAL_EQUITY,
            }:
                raise RulePackValidationError("STATIC_DRAWDOWN_ANCHOR_MUST_BE_INITIAL")
        else:  # TRAILING
            if self.anchor not in {
                LimitBasis.PERCENT_HIGH_WATER_EQUITY,
                LimitBasis.PERCENT_HIGH_WATER_BALANCE,
            }:
                raise RulePackValidationError("TRAILING_DRAWDOWN_ANCHOR_MUST_BE_HIGH_WATER")
            if not self.trail_reference:
                raise RulePackValidationError("TRAILING_DRAWDOWN_REQUIRES_TRAIL_REFERENCE")
            if not self.trail_update_cadence:
                raise RulePackValidationError("TRAILING_DRAWDOWN_REQUIRES_TRAIL_CADENCE")

    @property
    def is_trailing(self) -> bool:
        return self.kind is DrawdownKind.TRAILING



# â”€â”€â”€ PROFIT TARGET â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass(frozen=True)
class ProfitTargetRule(RuleBase):
    """Profit target for one phase, modelled but NOT evaluated.

    Captures amount vs percentage and its basis, whether floating profit counts
    (firms genuinely differ -- some only credit closed profit), whether the
    target is judged intraday or at close, and any dependency on a minimum
    number of trading days.
    """

    limit: Limit = None  # type: ignore[assignment]
    counts_floating_profit: bool = False
    evaluation_timing: EvaluationTimeBasis = EvaluationTimeBasis.END_OF_DAY
    minimum_days_required: int | None = None
    reset_on_phase_transition: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.limit, Limit):
            raise RulePackValidationError("PROFIT_TARGET_REQUIRES_LIMIT")
        if self.minimum_days_required is not None:
            if self.minimum_days_required <= 0:
                raise RulePackValidationError("MINIMUM_DAYS_MUST_BE_POSITIVE")
            if not self.telemetry_requirements:
                raise RulePackValidationError(
                    "MINIMUM_DAYS_REQUIRES_TRADING_DAY_TELEMETRY"
                )


# â”€â”€â”€ TRADING DAYS / INACTIVITY â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass(frozen=True)
class TradingDayRule(RuleBase):
    """Minimum or maximum trading days, plus explicit day-count semantics.

    "What counts as a trading day" is itself governed: the qualifying criterion
    (any order, minimum closed trades, minimum profit) and the timezone are
    explicit. When a source is ambiguous, ``qualifying_criterion`` is ``None``
    and the rule is AMBIGUOUS at validation rather than guessed.
    """

    limit: Limit = None  # type: ignore[assignment]
    day_count_basis: DayCountBasis = DayCountBasis.TRADING_DAYS
    consecutive: ConsecutiveMode = ConsecutiveMode.NON_CONSECUTIVE
    qualifying_criterion: str | None = None
    minimum_closed_trades: int | None = None
    minimum_profit: Limit | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.limit, Limit):
            raise RulePackValidationError("TRADING_DAY_RULE_REQUIRES_LIMIT")
        if self.limit.basis not in {LimitBasis.TRADING_DAYS, LimitBasis.CALENDAR_DAYS}:
            raise RulePackValidationError("TRADING_DAY_LIMIT_BASIS_INVALID")
        if self.minimum_closed_trades is not None and self.minimum_closed_trades <= 0:
            raise RulePackValidationError("MINIMUM_CLOSED_TRADES_MUST_BE_POSITIVE")
        if self.minimum_profit is not None and not isinstance(self.minimum_profit, Limit):
            raise RulePackValidationError("MINIMUM_PROFIT_MUST_BE_LIMIT")


@dataclass(frozen=True)
class InactivityRule(RuleBase):
    """Maximum permitted inactivity window, or a mandatory activity requirement."""

    max_inactive_duration: str | None = None
    requires_daily_activity: bool = False
    qualifying_criterion: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.max_inactive_duration and not self.requires_daily_activity:
            raise RulePackValidationError("INACTIVITY_RULE_REQUIRES_SEMANTICS")


# â”€â”€â”€ POSITION / LOT LIMITS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass(frozen=True)
class PositionLimitRule(RuleBase):
    """Lot and position-count limits.

    Deliberately DISTINCT from monetary open-risk limits: "max 2 lots" and
    "max $500 at risk" are different constraints with different evidence, and
    conflating them is a classic source of unsafe assumptions.
    """

    limit: Limit = None  # type: ignore[assignment]
    per_symbol: bool = False
    asset_class_limit: bool = False
    direction_specific: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.limit, Limit):
            raise RulePackValidationError("POSITION_LIMIT_REQUIRES_LIMIT")
        if self.limit.basis not in {LimitBasis.LOT_COUNT, LimitBasis.POSITION_COUNT}:
            raise RulePackValidationError("POSITION_LIMIT_BASIS_INVALID")
        if self.direction_specific and not self.applies_to_symbols:
            raise RulePackValidationError("DIRECTION_SPECIFIC_REQUIRES_SYMBOL_SCOPE")


@dataclass(frozen=True)
class OpenRiskLimitRule(RuleBase):
    """Monetary open-risk limits.

    Modelled now, enforced later. These rules are the direct consumers of the
    accepted Block 2B/2C telemetry: total open risk comes from 2B, per-symbol and
    correlated/directional risk come from 2C. The declared
    ``telemetry_requirements`` make that lineage explicit.
    """

    limit: Limit = None  # type: ignore[assignment]
    scope_kind: str = "TOTAL"
    correlation_cluster_scoped: bool = False
    direction_scoped: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.limit, Limit):
            raise RulePackValidationError("OPEN_RISK_RULE_REQUIRES_LIMIT")
        if not self.limit.is_monetary:
            raise RulePackValidationError("OPEN_RISK_LIMIT_MUST_BE_MONETARY_OR_PERCENT")
        if self.correlation_cluster_scoped and not any(
            r is TelemetryRequirement.SYMBOL_CORRELATION_CLUSTERS
            for r in self.telemetry_requirements
        ):
            raise RulePackValidationError("CORRELATION_SCOPE_REQUIRES_CORRELATION_TELEMETRY")



# â”€â”€â”€ NEWS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass(frozen=True)
class NewsTradingRestrictionRule(RuleBase):
    """News-trading restriction with EXACT blackout semantics.

    When a source does not state the blackout window, the fields stay ``None``
    and the rule is AMBIGUOUS. Minutes are NEVER invented to make a rule look
    complete. Requires an external economic calendar to ever be evaluated.
    """

    blackouts_high_impact: bool = True
    importance_levels: tuple[NewsImportance, ...] = (NewsImportance.HIGH,)
    pre_event_blackout_minutes: int | None = None
    post_event_blackout_minutes: int | None = None
    affected_currencies: tuple[str, ...] = ()
    affected_symbols: tuple[str, ...] = ()
    prohibit_opening_trades: bool = True
    prohibit_closing_trades: bool = False
    prohibit_modifying_sl_tp: bool = False
    prohibit_holding_through_event: bool = True
    requires_economic_calendar: bool = True
    blackout_window_unspecified: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "importance_levels", tuple(self.importance_levels))
        for level in self.importance_levels:
            if not isinstance(level, NewsImportance):
                raise RulePackValidationError("NEWS_IMPORTANCE_INVALID")
        for minutes in (self.pre_event_blackout_minutes, self.post_event_blackout_minutes):
            if minutes is not None and (minutes < 0 or minutes > 1440):
                raise RulePackValidationError("NEWS_BLACKOUT_MINUTES_OUT_OF_RANGE")
        if self.blackouts_high_impact and not self.has_explicit_blackout:
            # The source says news matters but never says the window. Record
            # that honestly rather than guessing a number.
            object.__setattr__(self, "blackout_window_unspecified", True)

    @property
    def has_explicit_blackout(self) -> bool:
        return (
            self.pre_event_blackout_minutes is not None
            or self.post_event_blackout_minutes is not None
        )



# â”€â”€â”€ WEEKEND / OVERNIGHT HOLD â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass(frozen=True)
class HoldRestrictionRule(RuleBase):
    """Weekend or overnight hold restriction, as SEPARATE rule kinds.

    "No weekend hold" does NOT mean "no overnight hold", and neither implies the
    other. Each is declared in its own rule with its own cutoff.
    """

    kind: HoldRestrictionKind = HoldRestrictionKind.WEEKEND_HOLD
    holding_permitted: bool = False
    must_close_before_market_close: bool = False
    cutoff_time: time | None = None
    allowed_asset_classes: tuple[str, ...] = ()
    exceptions: tuple[str, ...] = ()
    pending_orders_included: bool = True
    crypto_exempt: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.kind, HoldRestrictionKind):
            raise RulePackValidationError("HOLD_RESTRICTION_KIND_REQUIRED")
        object.__setattr__(self, "allowed_asset_classes", tuple(self.allowed_asset_classes))
        object.__setattr__(self, "exceptions", tuple(self.exceptions))
        if not self.holding_permitted:
            if not self.timezone_name:
                raise RulePackValidationError("HOLD_RESTRICTION_REQUIRES_CUTOFF_TIMEZONE")
            if self.must_close_before_market_close and self.cutoff_time is None:
                raise RulePackValidationError("MUST_CLOSE_REQUIRES_CUTOFF_TIME")

    @property
    def rule_type_for_kind(self) -> RuleType:
        return (
            RuleType.WEEKEND_HOLD_RESTRICTION
            if self.kind is HoldRestrictionKind.WEEKEND_HOLD
            else RuleType.OVERNIGHT_HOLD_RESTRICTION
        )


# â”€â”€â”€ AUTOMATION / COPY TRADING / IP â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass(frozen=True)
class AutomationPermissionRule(RuleBase):
    """Expert-advisor / algorithmic trading permission.

    No policy is inferred from vague marketing wording. An unclear source yields
    ``UNSPECIFIED``, which the validator surfaces as AMBIGUOUS rather than
    defaulting to either allowed or forbidden.
    """

    ea_allowed: AutomationVerdict = AutomationVerdict.UNSPECIFIED
    algo_allowed: AutomationVerdict = AutomationVerdict.UNSPECIFIED
    hft_allowed: AutomationVerdict = AutomationVerdict.UNSPECIFIED
    latency_arbitrage_allowed: AutomationVerdict = AutomationVerdict.UNSPECIFIED
    external_signal_allowed: AutomationVerdict = AutomationVerdict.UNSPECIFIED
    requires_vps: AutomationVerdict = AutomationVerdict.UNSPECIFIED
    requires_supported_platform: AutomationVerdict = AutomationVerdict.UNSPECIFIED
    supported_platforms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "supported_platforms", tuple(self.supported_platforms))
        for verdict in self.verdicts():
            if not isinstance(verdict, AutomationVerdict):
                raise RulePackValidationError("AUTOMATION_VERDICT_INVALID")

    def verdicts(self) -> tuple[AutomationVerdict, ...]:
        return (
            self.ea_allowed,
            self.algo_allowed,
            self.hft_allowed,
            self.latency_arbitrage_allowed,
            self.external_signal_allowed,
            self.requires_vps,
            self.requires_supported_platform,
        )

    @property
    def has_unspecified_verdict(self) -> bool:
        return any(v is AutomationVerdict.UNSPECIFIED for v in self.verdicts())


@dataclass(frozen=True)
class CopyTradingRestrictionRule(RuleBase):
    """Copy trading / trade copier / account mirroring policy."""

    copy_trading_allowed: AutomationVerdict = AutomationVerdict.UNSPECIFIED
    trade_copier_allowed: AutomationVerdict = AutomationVerdict.UNSPECIFIED
    account_mirroring_allowed: AutomationVerdict = AutomationVerdict.UNSPECIFIED
    external_signal_allowed: AutomationVerdict = AutomationVerdict.UNSPECIFIED
    max_linked_accounts: int | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.max_linked_accounts is not None and self.max_linked_accounts <= 0:
            raise RulePackValidationError("MAX_LINKED_ACCOUNTS_MUST_BE_POSITIVE")

    @property
    def has_unspecified_verdict(self) -> bool:
        return any(
            v is AutomationVerdict.UNSPECIFIED
            for v in (
                self.copy_trading_allowed,
                self.trade_copier_allowed,
                self.account_mirroring_allowed,
                self.external_signal_allowed,
            )
        )


@dataclass(frozen=True)
class IPRestrictionRule(RuleBase):
    """IP address / device / location restrictions, where a firm models them."""

    ip_change_allowed: AutomationVerdict = AutomationVerdict.UNSPECIFIED
    device_limit: int | None = None
    location_restricted: AutomationVerdict = AutomationVerdict.UNSPECIFIED
    vps_required: AutomationVerdict = AutomationVerdict.UNSPECIFIED
    allowed_locations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "allowed_locations", tuple(self.allowed_locations))
        if self.device_limit is not None and self.device_limit <= 0:
            raise RulePackValidationError("DEVICE_LIMIT_MUST_BE_POSITIVE")



# â”€â”€â”€ CONSISTENCY â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass(frozen=True)
class ConsistencyRule(RuleBase):
    """Consistency rule with EXPLICIT numerator and denominator.

    "30% consistency" is meaningless on its own. This type forces the question
    "30% OF WHAT, COMPARED TO WHAT" into the definition, so a rule can never be
    modelled as a bare percentage.
    """

    metric: str = "BEST_DAY_SHARE_OF_TOTAL_PROFIT"
    limit: Limit = None  # type: ignore[assignment]
    numerator_definition: str = ""
    denominator_definition: str = ""
    window: DayCountBasis = DayCountBasis.TRADING_DAYS
    applies_to_payout: bool = True
    applies_to_challenge_pass: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.limit, Limit):
            raise RulePackValidationError("CONSISTENCY_RULE_REQUIRES_LIMIT")
        if not self.limit.is_percentage:
            raise RulePackValidationError("CONSISTENCY_LIMIT_MUST_BE_PERCENTAGE")
        _require_text(self.metric, "CONSISTENCY_METRIC_REQUIRED")
        _require_text(self.numerator_definition, "CONSISTENCY_NUMERATOR_REQUIRED")
        _require_text(self.denominator_definition, "CONSISTENCY_DENOMINATOR_REQUIRED")


# â”€â”€â”€ PAYOUT / REFUND / RESET â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass(frozen=True)
class PayoutRule(RuleBase):
    """Payout eligibility and cadence, modelled but NOT calculated.

    Deliberately separate from challenge-pass rules: being eligible for a payout
    is a different contract question from passing an evaluation phase.
    """

    cadence: PayoutCadence = PayoutCadence.NONE
    minimum_profit: Limit | None = None
    minimum_profitable_days: int | None = None
    minimum_buffer: Limit | None = None
    consistency_dependency: bool = False
    first_payout_delay_days: int | None = None
    subsequent_payout_interval_days: int | None = None
    profit_split_percent: float | None = None
    requires_trading_days_rule: bool = False
    refundable_on_failure: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        for optional in (self.minimum_profit, self.minimum_buffer):
            if optional is not None and not isinstance(optional, Limit):
                raise RulePackValidationError("PAYOUT_LIMIT_MUST_BE_LIMIT")
        if self.minimum_profitable_days is not None and self.minimum_profitable_days <= 0:
            raise RulePackValidationError("MINIMUM_PROFITABLE_DAYS_MUST_BE_POSITIVE")
        if self.profit_split_percent is not None:
            if not 0 < self.profit_split_percent <= 100:
                raise RulePackValidationError("PROFIT_SPLIT_OUT_OF_RANGE")
        if self.cadence is PayoutCadence.CUSTOM:
            if not self.subsequent_payout_interval_days:
                raise RulePackValidationError("CUSTOM_CADENCE_REQUIRES_INTERVAL")
        if self.cadence is PayoutCadence.NONE and (
            self.subsequent_payout_interval_days is not None
        ):
            raise RulePackValidationError("CADENCE_NONE_CONFLICTS_WITH_INTERVAL")
        if self.minimum_profit is None and self.cadence is not PayoutCadence.NONE:
            raise RulePackValidationError("PAYOUT_REQUIRES_MINIMUM_PROFIT")
        if self.consistency_dependency and not any(
            r is TelemetryRequirement.DAILY_PROFIT_SERIES
            for r in self.telemetry_requirements
        ):
            raise RulePackValidationError("CONSISTENCY_DEPENDENCY_REQUIRES_DAILY_PROFIT_SERIES")


@dataclass(frozen=True)
class RefundRule(RuleBase):
    """Refund / fee-return policy."""

    refund_trigger: str = ""
    refund_amount: Limit | None = None
    refundable: bool = False
    fee_is_refundable: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.refundable:
            _require_text(self.refund_trigger, "REFUND_TRIGGER_REQUIRED")
        if self.refund_amount is not None and not isinstance(self.refund_amount, Limit):
            raise RulePackValidationError("REFUND_AMOUNT_MUST_BE_LIMIT")


@dataclass(frozen=True)
class ResetRule(RuleBase):
    """Whether and how a challenge attempt may be reset."""

    reset_permitted: bool = False
    reset_fee: Limit | None = None
    max_resets: int | None = None
    reset_cooldown_days: int | None = None
    resets_profit_history: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.reset_fee is not None and not isinstance(self.reset_fee, Limit):
            raise RulePackValidationError("RESET_FEE_MUST_BE_LIMIT")
        if self.max_resets is not None and self.max_resets < 0:
            raise RulePackValidationError("MAX_RESETS_MUST_BE_NON_NEGATIVE")


# â”€â”€â”€ FORWARD COMPATIBILITY â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass(frozen=True)
class UnknownRule(RuleBase):
    """A rule type this system does not recognise.

    Preserved VERBATIM rather than dropped, so a future rule can never vanish
    from a rule pack during a round-trip. It is always UNSUPPORTED and can never
    make a pack usable for evaluation.
    """

    raw_rule_type: str = ""
    raw_payload: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_text(self.raw_rule_type, "UNKNOWN_RULE_REQUIRES_RAW_TYPE")
        object.__setattr__(self, "status", RuleStatus.UNSUPPORTED)



# â”€â”€â”€ RULE TYPE REGISTRY â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

#: The ONE place a :class:`RuleType` maps to its concrete typed contract.
#: Compilation and validation both read this table, so a rule type cannot exist
#: without a class and a contract.
RULE_TYPE_CONTRACTS: Mapping[RuleType, type[RuleBase]] = {
    RuleType.DAILY_LOSS_LIMIT: DailyLossRule,
    RuleType.DAILY_PROFIT_LIMIT: DailyProfitRule,
    RuleType.MAX_DRAWDOWN: DrawdownRule,
    RuleType.STATIC_DRAWDOWN: DrawdownRule,
    RuleType.TRAILING_DRAWDOWN: DrawdownRule,
    RuleType.PROFIT_TARGET: ProfitTargetRule,
    RuleType.MIN_TRADING_DAYS: TradingDayRule,
    RuleType.MAX_TRADING_DAYS: TradingDayRule,
    RuleType.INACTIVITY_RULE: InactivityRule,
    RuleType.MAX_POSITION_SIZE: PositionLimitRule,
    RuleType.MAX_OPEN_POSITIONS: PositionLimitRule,
    RuleType.MAX_OPEN_RISK: OpenRiskLimitRule,
    RuleType.MAX_RISK_PER_POSITION: OpenRiskLimitRule,
    RuleType.MAX_RISK_PER_SYMBOL: OpenRiskLimitRule,
    RuleType.MAX_CORRELATED_RISK: OpenRiskLimitRule,
    RuleType.MAX_DIRECTIONAL_RISK: OpenRiskLimitRule,
    RuleType.NEWS_TRADING_RESTRICTION: NewsTradingRestrictionRule,
    RuleType.WEEKEND_HOLD_RESTRICTION: HoldRestrictionRule,
    RuleType.OVERNIGHT_HOLD_RESTRICTION: HoldRestrictionRule,
    RuleType.CONSISTENCY_RULE: ConsistencyRule,
    RuleType.EA_AUTOMATION_PERMISSION: AutomationPermissionRule,
    RuleType.COPY_TRADING_RESTRICTION: CopyTradingRestrictionRule,
    RuleType.IP_DEVICE_LOCATION_RESTRICTION: IPRestrictionRule,
    RuleType.PAYOUT_ELIGIBILITY: PayoutRule,
    RuleType.PAYOUT_FREQUENCY: PayoutRule,
    RuleType.MIN_PROFIT_FOR_PAYOUT: PayoutRule,
    RuleType.REFUND_RULE: RefundRule,
    RuleType.RESET_RULE: ResetRule,
    RuleType.UNKNOWN_EXTENSION: UnknownRule,
}


def contract_for(rule_type: RuleType) -> type[RuleBase]:
    """The concrete typed contract for a rule type.

    Raises rather than returning a generic dict-shaped fallback, so an unmodelled
    rule type can never be silently accepted.
    """
    try:
        return RULE_TYPE_CONTRACTS[rule_type]
    except KeyError as exc:  # pragma: no cover - guarded by enum completeness
        raise RulePackValidationError(f"UNMODELLED_RULE_TYPE:{rule_type}") from exc



#: Declared evidence and implementation-support status PER RULE TYPE.
#:
#: Support status is deliberately SEPARATE from rule validity: a perfectly valid
#: news rule is still ``REQUIRES_EXTERNAL_SOURCE`` because no economic calendar
#: exists yet. Block 2 supplies 2A/2B/2C/2D only; everything else is 3B scope.
RULE_TYPE_SUPPORT: Mapping[RuleType, tuple[EvaluationSupport, tuple[TelemetryRequirement, ...]]] = {
    RuleType.DAILY_LOSS_LIMIT: (
        EvaluationSupport.REQUIRES_NEW_STATE,
        (
            TelemetryRequirement.ACCOUNT_EQUITY,
            TelemetryRequirement.ACCOUNT_BALANCE,
            TelemetryRequirement.ACCOUNT_CURRENCY,
            TelemetryRequirement.START_OF_DAY_BALANCE,
            TelemetryRequirement.START_OF_DAY_EQUITY,
            TelemetryRequirement.REALISED_DAILY_PNL,
            TelemetryRequirement.FLOATING_PNL,
            TelemetryRequirement.COMMISSION_AND_FEES,
            TelemetryRequirement.SWAP,
            TelemetryRequirement.TIME_AND_TIMEZONE,
        ),
    ),
    RuleType.DAILY_PROFIT_LIMIT: (
        EvaluationSupport.REQUIRES_NEW_STATE,
        (
            TelemetryRequirement.ACCOUNT_EQUITY,
            TelemetryRequirement.REALISED_DAILY_PNL,
            TelemetryRequirement.FLOATING_PNL,
            TelemetryRequirement.TIME_AND_TIMEZONE,
        ),
    ),
    RuleType.MAX_DRAWDOWN: (
        EvaluationSupport.REQUIRES_NEW_STATE,
        (
            TelemetryRequirement.ACCOUNT_EQUITY,
            TelemetryRequirement.ACCOUNT_BALANCE,
            TelemetryRequirement.INITIAL_BALANCE,
            TelemetryRequirement.INITIAL_EQUITY,
            TelemetryRequirement.HIGH_WATER_EQUITY,
        ),
    ),
    RuleType.STATIC_DRAWDOWN: (
        EvaluationSupport.REQUIRES_NEW_STATE,
        (TelemetryRequirement.ACCOUNT_EQUITY,
         TelemetryRequirement.INITIAL_BALANCE,
         TelemetryRequirement.INITIAL_EQUITY),
    ),
    RuleType.TRAILING_DRAWDOWN: (
        EvaluationSupport.REQUIRES_NEW_STATE,
        (TelemetryRequirement.ACCOUNT_EQUITY, TelemetryRequirement.HIGH_WATER_EQUITY),
    ),
    RuleType.PROFIT_TARGET: (
        EvaluationSupport.REQUIRES_NEW_STATE,
        (
            TelemetryRequirement.ACCOUNT_EQUITY,
            TelemetryRequirement.ACCOUNT_BALANCE,
            TelemetryRequirement.REALISED_DAILY_PNL,
            TelemetryRequirement.FLOATING_PNL,
        ),
    ),
    RuleType.MIN_TRADING_DAYS: (
        EvaluationSupport.REQUIRES_NEW_STATE,
        (TelemetryRequirement.TRADING_DAY_HISTORY, TelemetryRequirement.DAILY_PROFIT_SERIES),
    ),
    RuleType.MAX_TRADING_DAYS: (
        EvaluationSupport.REQUIRES_NEW_STATE,
        (TelemetryRequirement.TRADING_DAY_HISTORY, TelemetryRequirement.TIME_AND_TIMEZONE),
    ),
    RuleType.INACTIVITY_RULE: (
        EvaluationSupport.REQUIRES_NEW_STATE,
        (TelemetryRequirement.TRADING_DAY_HISTORY, TelemetryRequirement.TIME_AND_TIMEZONE),
    ),
    RuleType.MAX_POSITION_SIZE: (
        EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY,
        (TelemetryRequirement.LOT_SIZE, TelemetryRequirement.OPEN_POSITIONS),
    ),
    RuleType.MAX_OPEN_POSITIONS: (
        EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY,
        (TelemetryRequirement.POSITION_COUNT, TelemetryRequirement.OPEN_POSITIONS),
    ),
    RuleType.MAX_OPEN_RISK: (
        EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY,
        (TelemetryRequirement.OPEN_RISK_TOTAL, TelemetryRequirement.OPEN_POSITIONS),
    ),
    RuleType.MAX_RISK_PER_POSITION: (
        EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY,
        (TelemetryRequirement.OPEN_RISK_PER_POSITION,),
    ),
    RuleType.MAX_RISK_PER_SYMBOL: (
        EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY,
        (TelemetryRequirement.OPEN_RISK_PER_SYMBOL,),
    ),
    RuleType.MAX_CORRELATED_RISK: (
        EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY,
        (TelemetryRequirement.SYMBOL_CORRELATION_CLUSTERS,
         TelemetryRequirement.OPEN_RISK_TOTAL),
    ),
    RuleType.MAX_DIRECTIONAL_RISK: (
        EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY,
        (TelemetryRequirement.DIRECTIONAL_EXPOSURE, TelemetryRequirement.OPEN_RISK_TOTAL),
    ),
    RuleType.NEWS_TRADING_RESTRICTION: (
        EvaluationSupport.REQUIRES_EXTERNAL_SOURCE,
        (TelemetryRequirement.ECONOMIC_CALENDAR, TelemetryRequirement.TIME_AND_TIMEZONE),
    ),
    RuleType.WEEKEND_HOLD_RESTRICTION: (
        EvaluationSupport.REQUIRES_NEW_STATE,
        (TelemetryRequirement.OPEN_POSITIONS, TelemetryRequirement.TIME_AND_TIMEZONE),
    ),
    RuleType.OVERNIGHT_HOLD_RESTRICTION: (
        EvaluationSupport.REQUIRES_NEW_STATE,
        (TelemetryRequirement.OPEN_POSITIONS, TelemetryRequirement.TIME_AND_TIMEZONE),
    ),
    RuleType.CONSISTENCY_RULE: (
        EvaluationSupport.REQUIRES_NEW_STATE,
        (TelemetryRequirement.DAILY_PROFIT_SERIES, TelemetryRequirement.TRADING_DAY_HISTORY),
    ),

    RuleType.EA_AUTOMATION_PERMISSION: (
        EvaluationSupport.NOT_IMPLEMENTED,
        (TelemetryRequirement.ACCOUNT_CREDENTIALS,),
    ),
    RuleType.COPY_TRADING_RESTRICTION: (
        EvaluationSupport.NOT_IMPLEMENTED,
        (TelemetryRequirement.ACCOUNT_CREDENTIALS,),
    ),
    RuleType.IP_DEVICE_LOCATION_RESTRICTION: (
        EvaluationSupport.REQUIRES_EXTERNAL_SOURCE,
        (TelemetryRequirement.ACCOUNT_CREDENTIALS,),
    ),
    RuleType.PAYOUT_ELIGIBILITY: (
        EvaluationSupport.REQUIRES_NEW_STATE,
        (TelemetryRequirement.CHALLENGE_PHASE_STATE, TelemetryRequirement.DAILY_PROFIT_SERIES),
    ),
    RuleType.PAYOUT_FREQUENCY: (
        EvaluationSupport.REQUIRES_NEW_STATE,
        (TelemetryRequirement.CHALLENGE_PHASE_STATE, TelemetryRequirement.TIME_AND_TIMEZONE),
    ),
    RuleType.MIN_PROFIT_FOR_PAYOUT: (
        EvaluationSupport.REQUIRES_NEW_STATE,
        (TelemetryRequirement.ACCOUNT_EQUITY, TelemetryRequirement.CHALLENGE_PHASE_STATE),
    ),
    RuleType.REFUND_RULE: (
        EvaluationSupport.NOT_IMPLEMENTED,
        (TelemetryRequirement.CHALLENGE_PHASE_STATE,),
    ),
    RuleType.RESET_RULE: (
        EvaluationSupport.NOT_IMPLEMENTED,
        (TelemetryRequirement.CHALLENGE_PHASE_STATE,),
    ),
    RuleType.UNKNOWN_EXTENSION: (
        EvaluationSupport.UNSUPPORTED,
        (TelemetryRequirement.NONE,),
    ),
}


def support_for(rule_type: RuleType) -> EvaluationSupport:
    """Whether this system can evaluate this rule type today."""
    return RULE_TYPE_SUPPORT[rule_type][0]


def required_telemetry_for(rule_type: RuleType) -> tuple[TelemetryRequirement, ...]:
    """The evidence this rule type needs before it can ever be evaluated."""
    return RULE_TYPE_SUPPORT[rule_type][1]


def support_matrix() -> dict[RuleType, EvaluationSupport]:
    """Full rule-type -> support-status matrix (Block 3B/3C scope input)."""
    return {rule_type: support for rule_type, (support, _) in RULE_TYPE_SUPPORT.items()}
