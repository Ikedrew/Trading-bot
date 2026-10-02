"""Synthetic built-in rule packs PACK_A and PACK_B (Block 3A).

These packs exist to VALIDATE THE MODEL, not to describe any real prop firm.
They are deliberately fabricated and clearly labelled as synthetic. Encoding a
real firm (Funding Pips, FTMO, Alpha Capital, ...) from memory is explicitly out
of scope for 3A: that requires official-source verification, provenance capture
and an exact account/program selection first.

PACK_A exercises: static drawdown, start-of-day-balance daily loss, percentage
profit target, minimum trading days, weekend holding ALLOWED, news ALLOWED.
PACK_B exercises: trailing drawdown on high-water equity, a daily loss that
INCLUDES floating P&L, news blackout with an explicit +/- window, no weekend
hold, and a consistency rule with an explicit numerator and denominator.

Both bind every limit to an explicit basis, an explicit currency and an explicit
IANA timezone. Neither enforces anything.
"""

from __future__ import annotations

from datetime import datetime, time, timezone

from core.risk.prop_rule_contracts import (
    ConsistencyRule,
    DailyLossRule,
    DrawdownRule,
    HoldRestrictionRule,
    NewsTradingRestrictionRule,
    ProfitTargetRule,
    RuleSource,
    TradingDayRule,
    required_telemetry_for,
)
from core.risk.prop_rule_enums import (
    AutomationVerdict,
    BreachKind,
    ConsecutiveMode,
    CurrencySemantics,
    DailyResetPolicy,
    DayCountBasis,
    DrawdownKind,
    EvaluationTimeBasis,
    HoldRestrictionKind,
    LimitBasis,
    NewsImportance,
    PrecedenceLevel,
    RulePhase,
    RuleSeverity,
    RuleType,
    SourceType,
)
from core.risk.prop_rule_pack import RulePack, RulePackIdentity, compile_rule_pack
from core.risk.prop_rule_telemetry import TelemetryRequirement
from core.risk.prop_rule_values import Limit

#: Fixed, explicit instants. No wall clock is ever read while building a pack.
PACK_EFFECTIVE_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: Both packs use the SAME declared source kind, so a reader can never mistake a
#: synthetic pack for an official one.
SYNTHETIC_SOURCE = RuleSource(
    source_type=SourceType.MANUAL_ASSUMPTION,
    source_reference="synthetic://block3a/pack_a",
    notes="SYNTHETIC TEST FIXTURE. Not a real prop firm. Model validation only.",
)

SYNTHETIC_SOURCE_B = RuleSource(
    source_type=SourceType.MANUAL_ASSUMPTION,
    source_reference="synthetic://block3a/pack_b",
    notes="SYNTHETIC TEST FIXTURE. Not a real prop firm. Model validation only.",
)

ACCOUNT_CURRENCY = CurrencySemantics.ACCOUNT_CURRENCY


def _percent(basis: LimitBasis, value: float) -> Limit:
    return Limit(basis=basis, value=value, currency_semantics=ACCOUNT_CURRENCY)


def pack_a_identity(version: str = "1.0.0", **overrides) -> RulePackIdentity:
    base = dict(
        provider="SYNTHETIC_FIRM_A",
        program="SYNTHETIC_CHALLENGE",
        phase=RulePhase.EVALUATION_PHASE_1,
        account_size=100_000,
        currency="USD",
        rule_pack_version=version,
        effective_from=PACK_EFFECTIVE_FROM,
    )
    base.update(overrides)
    return RulePackIdentity(**base)


def pack_b_identity(version: str = "1.0.0", **overrides) -> RulePackIdentity:
    base = dict(
        provider="SYNTHETIC_FIRM_B",
        program="SYNTHETIC_CHALLENGE",
        phase=RulePhase.EVALUATION_PHASE_1,
        account_size=100_000,
        currency="USD",
        rule_pack_version=version,
        effective_from=PACK_EFFECTIVE_FROM,
    )
    base.update(overrides)
    return RulePackIdentity(**base)


# â”€â”€â”€ PACK A â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def build_pack_a(identity: RulePackIdentity | None = None) -> RulePack:
    """SYNTHETIC pack: 5% daily loss of start-of-day balance, 10% STATIC
    drawdown of initial balance, 8% profit target, 5 minimum trading days,
    weekend holding ALLOWED, news ALLOWED.

    "News allowed" and "weekend hold allowed" are modelled as PERMISSIVE rules
    with ``enabled=True`` and no blackout window, rather than as the mere
    absence of a restriction, so the permission is explicit and auditable.
    """
    identity = identity or pack_a_identity()

    daily_loss = DailyLossRule(
        rule_id="pack_a.daily_loss",
        rule_type=RuleType.DAILY_LOSS_LIMIT,
        enabled=True,
        severity=RuleSeverity.TERMINATION,
        source=SYNTHETIC_SOURCE,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.DAILY_LOSS_LIMIT),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        breach_kind=BreachKind.HARD,
        breach_persists_until_reset=True,
        timezone_name="America/New_York",
        limit=_percent(LimitBasis.PERCENT_START_OF_DAY_BALANCE, 5.0),
        reset_policy=DailyResetPolicy.CALENDAR_DAY,
        reset_time=time(0, 0),
    )

    static_drawdown = DrawdownRule(
        rule_id="pack_a.static_drawdown",
        rule_type=RuleType.STATIC_DRAWDOWN,
        enabled=True,
        severity=RuleSeverity.TERMINATION,
        source=SYNTHETIC_SOURCE,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.STATIC_DRAWDOWN),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        kind=DrawdownKind.STATIC,
        limit=_percent(LimitBasis.PERCENT_INITIAL_BALANCE, 10.0),
        anchor=LimitBasis.PERCENT_INITIAL_BALANCE,
    )

    profit_target = ProfitTargetRule(
        rule_id="pack_a.profit_target",
        rule_type=RuleType.PROFIT_TARGET,
        enabled=True,
        severity=RuleSeverity.ADVISORY,
        source=SYNTHETIC_SOURCE,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.PROFIT_TARGET),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
        timezone_name="America/New_York",
        limit=_percent(LimitBasis.PERCENT_INITIAL_BALANCE, 8.0),
        counts_floating_profit=False,
        evaluation_timing=EvaluationTimeBasis.END_OF_DAY,
    )

    min_days = TradingDayRule(
        rule_id="pack_a.min_trading_days",
        rule_type=RuleType.MIN_TRADING_DAYS,
        enabled=True,
        severity=RuleSeverity.ADVISORY,
        source=SYNTHETIC_SOURCE,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.MIN_TRADING_DAYS),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
        timezone_name="America/New_York",
        limit=Limit(basis=LimitBasis.TRADING_DAYS, value=5),
        day_count_basis=DayCountBasis.TRADING_DAYS,
        consecutive=ConsecutiveMode.NON_CONSECUTIVE,
        qualifying_criterion="ANY_CLOSED_TRADE",
    )

    weekend_allowed = HoldRestrictionRule(
        rule_id="pack_a.weekend_hold",
        rule_type=RuleType.WEEKEND_HOLD_RESTRICTION,
        enabled=True,
        severity=RuleSeverity.ADVISORY,
        source=SYNTHETIC_SOURCE,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.WEEKEND_HOLD_RESTRICTION),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        kind=HoldRestrictionKind.WEEKEND_HOLD,
        holding_permitted=True,
        timezone_name="America/New_York",
    )

    news_allowed = NewsTradingRestrictionRule(
        rule_id="pack_a.news",
        rule_type=RuleType.NEWS_TRADING_RESTRICTION,
        enabled=True,
        severity=RuleSeverity.ADVISORY,
        source=SYNTHETIC_SOURCE,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.NEWS_TRADING_RESTRICTION),
        evaluation_time_basis=EvaluationTimeBasis.EVENT_DRIVEN,
        timezone_name="America/New_York",
        blackouts_high_impact=False,
        importance_levels=(NewsImportance.HIGH,),
        prohibit_opening_trades=False,
        prohibit_holding_through_event=False,
        requires_economic_calendar=False,
    )

    return compile_rule_pack(
        identity,
        [daily_loss, static_drawdown, profit_target, min_days, weekend_allowed, news_allowed],
        notes="SYNTHETIC PACK_A -- model validation only, not a real firm.",
    )



# â”€â”€â”€ PACK B â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def build_pack_b(identity: RulePackIdentity | None = None) -> RulePack:
    """SYNTHETIC pack: 4% daily loss INCLUDING floating P&L, 8% TRAILING
    drawdown of high-water equity, 10% profit target, no weekend hold, news
    blackout +/-2 minutes, and a consistency rule.

    The trailing drawdown declares its trail reference and cadence explicitly,
    and the consistency rule declares numerator and denominator explicitly, so
    neither can be mistaken for a bare percentage.
    """
    from core.risk.prop_rule_contracts import PnLComponents

    identity = identity or pack_b_identity()

    daily_loss = DailyLossRule(
        rule_id="pack_b.daily_loss",
        rule_type=RuleType.DAILY_LOSS_LIMIT,
        enabled=True,
        severity=RuleSeverity.TERMINATION,
        source=SYNTHETIC_SOURCE_B,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.DAILY_LOSS_LIMIT),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        breach_kind=BreachKind.HARD,
        breach_persists_until_reset=True,
        timezone_name="Europe/London",
        limit=_percent(LimitBasis.PERCENT_START_OF_DAY_EQUITY, 4.0),
        reset_policy=DailyResetPolicy.CALENDAR_DAY,
        reset_time=time(0, 0),
        pnl_components=PnLComponents(
            include_closed_pnl=True,
            include_floating_pnl=True,  # PACK_B explicitly counts floating P&L
            include_commission=True,
            include_swap=True,
            include_fees=True,
        ),
    )

    trailing_drawdown = DrawdownRule(
        rule_id="pack_b.trailing_drawdown",
        rule_type=RuleType.TRAILING_DRAWDOWN,
        enabled=True,
        severity=RuleSeverity.TERMINATION,
        source=SYNTHETIC_SOURCE_B,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.TRAILING_DRAWDOWN),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="Europe/London",
        kind=DrawdownKind.TRAILING,
        limit=_percent(LimitBasis.PERCENT_HIGH_WATER_EQUITY, 8.0),
        anchor=LimitBasis.PERCENT_HIGH_WATER_EQUITY,
        trail_reference="EQUITY",
        trail_update_cadence="EOD",
        trail_locks_at_breach=True,
    )

    profit_target = ProfitTargetRule(
        rule_id="pack_b.profit_target",
        rule_type=RuleType.PROFIT_TARGET,
        enabled=True,
        severity=RuleSeverity.ADVISORY,
        source=SYNTHETIC_SOURCE_B,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.PROFIT_TARGET),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
        timezone_name="Europe/London",
        limit=_percent(LimitBasis.PERCENT_INITIAL_BALANCE, 10.0),
        counts_floating_profit=False,
    )

    weekend_forbidden = HoldRestrictionRule(
        rule_id="pack_b.weekend_hold",
        rule_type=RuleType.WEEKEND_HOLD_RESTRICTION,
        enabled=True,
        severity=RuleSeverity.TERMINATION,
        source=SYNTHETIC_SOURCE_B,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.WEEKEND_HOLD_RESTRICTION),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        kind=HoldRestrictionKind.WEEKEND_HOLD,
        holding_permitted=False,
        must_close_before_market_close=True,
        cutoff_time=time(20, 0),
        timezone_name="Europe/London",
    )

    news_blackout = NewsTradingRestrictionRule(
        rule_id="pack_b.news",
        rule_type=RuleType.NEWS_TRADING_RESTRICTION,
        enabled=True,
        severity=RuleSeverity.BREACH,
        source=SYNTHETIC_SOURCE_B,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.NEWS_TRADING_RESTRICTION),
        evaluation_time_basis=EvaluationTimeBasis.EVENT_DRIVEN,
        timezone_name="Europe/London",
        blackouts_high_impact=True,
        importance_levels=(NewsImportance.HIGH,),
        pre_event_blackout_minutes=2,   # explicitly stated, never invented
        post_event_blackout_minutes=2,
        prohibit_opening_trades=True,
        prohibit_closing_trades=False,
        prohibit_holding_through_event=True,
        requires_economic_calendar=True,
    )

    consistency = ConsistencyRule(
        rule_id="pack_b.consistency",
        rule_type=RuleType.CONSISTENCY_RULE,
        enabled=True,
        severity=RuleSeverity.WARNING,
        source=SYNTHETIC_SOURCE_B,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.CONSISTENCY_RULE),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
        timezone_name="Europe/London",
        metric="BEST_DAY_SHARE_OF_TOTAL_PROFIT",
        limit=_percent(LimitBasis.PERCENT_CURRENT_BALANCE, 40.0),
        numerator_definition="BEST_SINGLE_DAY_NET_PROFIT",
        denominator_definition="TOTAL_NET_PROFIT_ACROSS_ALL_QUALIFYING_DAYS",
    )

    return compile_rule_pack(
        identity,
        [
            daily_loss,
            trailing_drawdown,
            profit_target,
            weekend_forbidden,
            news_blackout,
            consistency,
        ],
        notes="SYNTHETIC PACK_B -- model validation only, not a real firm.",
    )


def build_packs() -> tuple[RulePack, RulePack]:
    """Both synthetic built-in packs, in declaration order."""
    return build_pack_a(), build_pack_b()
