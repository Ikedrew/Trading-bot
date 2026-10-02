"""Canonical prop RULE MODEL / RULE PACK contract (Block 3A).

Covers the governed rule-pack contract end to end: deterministic identity,
explicit numeric basis, explicit timezone, explicit currency semantics,
precedence and conflict detection, provenance and source authority, phase/tier
identity, support status and telemetry mapping, immutable serialisation,
effective-date semantics, store lookup and the fail-closed guarantees.

Block 3A IS MODELLING ONLY. The suite therefore also asserts the ABSENCE of
runtime side effects and of any trading, guard, broker or execution dependency.

All instants are explicit. Nothing here sleeps, reads a wall clock or touches a
broker.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import date, datetime, time, timedelta, timezone
import importlib
import json
import sys

import pytest

from core.risk.prop_rule_contracts import (
    RULE_TYPE_CONTRACTS,
    RULE_TYPE_SUPPORT,
    AutomationPermissionRule,
    ConsistencyRule,
    CopyTradingRestrictionRule,
    DailyLossRule,
    DailyProfitRule,
    DrawdownRule,
    HoldRestrictionRule,
    InactivityRule,
    IPRestrictionRule,
    NewsTradingRestrictionRule,
    OpenRiskLimitRule,
    PayoutRule,
    PnLComponents,
    PositionLimitRule,
    ProfitTargetRule,
    RefundRule,
    ResetRule,
    RuleSource,
    ScopeOverride,
    TradingDayRule,
    UnknownRule,
    required_telemetry_for,
    support_for,
    support_matrix,
)
from core.risk.prop_rule_enums import (
    SOURCE_AUTHORITY,
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
    RulePackStatus,
    RulePhase,
    RuleSeverity,
    RuleStatus,
    RuleType,
    SourceType,
)
from core.risk.prop_rule_examples import (
    PACK_EFFECTIVE_FROM,
    SYNTHETIC_SOURCE,
    SYNTHETIC_SOURCE_B,
    build_pack_a,
    build_pack_b,
    build_packs,
    pack_a_identity,
    pack_b_identity,
)
from core.risk.prop_rule_pack import (
    SCHEMA_VERSION,
    AmbiguousRulePackError,
    FATAL_CONFLICT_CODES,
    RulePack,
    RulePackConflictError,
    RulePackIdentity,
    RulePackNotFound,
    RulePackStore,
    canonical_json,
    canonical_rule_order,
    compile_rule_pack,
    derive_rule_pack_id,
    resolve_precedence,
    rule_pack_from_dict,
    rule_pack_from_json,
    rule_pack_to_dict,
    rule_pack_to_json,
    validate_rule_pack,
)
from core.risk.prop_rule_telemetry import (
    TelemetryRequirement,
    block2_source_for,
    required_block2_telemetry,
    unsatisfied_requirements,
)
from core.risk.prop_rule_values import (
    Limit,
    RuleBase,
    RulePackValidationError,
)

UTC = timezone.utc
NOT_EFFECTIVE = datetime(2025, 6, 1, tzinfo=UTC)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# TEST BUILDERS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def official_source(reference: str = "https://example.invalid/rules") -> RuleSource:
    return RuleSource(
        source_type=SourceType.OFFICIAL_RULE_PAGE,
        source_reference=reference,
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_effective_date=date(2026, 1, 1),
    )


def pack_identity(**overrides) -> RulePackIdentity:
    base = dict(
        provider="TEST_FIRM",
        program="TEST_CHALLENGE",
        phase=RulePhase.EVALUATION_PHASE_1,
        account_size=100_000,
        currency="USD",
        rule_pack_version="1.0.0",
        effective_from=PACK_EFFECTIVE_FROM,
    )
    base.update(overrides)
    return RulePackIdentity(**base)


def pct(basis: LimitBasis, value: float, **kwargs) -> Limit:
    kwargs.setdefault("currency_semantics", CurrencySemantics.ACCOUNT_CURRENCY)
    return Limit(basis=basis, value=value, **kwargs)


def daily_loss_rule(rule_id: str = "r.daily", value: float = 5.0, **kwargs) -> DailyLossRule:
    params = dict(
        rule_id=rule_id,
        rule_type=RuleType.DAILY_LOSS_LIMIT,
        enabled=True,
        severity=RuleSeverity.TERMINATION,
        source=kwargs.pop("source", official_source()),
        effective_from=kwargs.pop("effective_from", PACK_EFFECTIVE_FROM),
        telemetry_requirements=required_telemetry_for(RuleType.DAILY_LOSS_LIMIT),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        breach_persists_until_reset=True,
        limit=pct(LimitBasis.PERCENT_START_OF_DAY_BALANCE, value),
        reset_policy=DailyResetPolicy.CALENDAR_DAY,
        reset_time=time(0, 0),
    )
    params.update(kwargs)
    return DailyLossRule(**params)


def drawdown_rule(kind: DrawdownKind = DrawdownKind.STATIC, value: float = 10.0,
                  **kwargs) -> DrawdownRule:
    rule_type = RuleType.STATIC_DRAWDOWN if kind is DrawdownKind.STATIC else RuleType.TRAILING_DRAWDOWN
    anchor = (
        LimitBasis.PERCENT_INITIAL_BALANCE
        if kind is DrawdownKind.STATIC
        else LimitBasis.PERCENT_HIGH_WATER_EQUITY
    )
    params = dict(
        rule_id="r.dd",
        rule_type=rule_type,
        enabled=True,
        severity=RuleSeverity.TERMINATION,
        source=kwargs.pop("source", official_source()),
        effective_from=kwargs.pop("effective_from", PACK_EFFECTIVE_FROM),
        telemetry_requirements=required_telemetry_for(rule_type),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        kind=kind,
        limit=pct(anchor, value),
        anchor=anchor,
    )
    if kind is DrawdownKind.TRAILING:
        params["trail_reference"] = "EQUITY"
        params["trail_update_cadence"] = "EOD"
    params.update(kwargs)
    return DrawdownRule(**params)


def open_risk_rule(rule_id: str = "r.risk", value: float = 5.0, **kwargs) -> OpenRiskLimitRule:
    rule_type = kwargs.pop("rule_type", RuleType.MAX_OPEN_RISK)
    params = dict(
        rule_id=rule_id,
        rule_type=rule_type,
        enabled=True,
        severity=RuleSeverity.BREACH,
        source=kwargs.pop("source", official_source()),
        effective_from=kwargs.pop("effective_from", PACK_EFFECTIVE_FROM),
        telemetry_requirements=required_telemetry_for(rule_type),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        limit=pct(LimitBasis.PERCENT_INITIAL_BALANCE, value),
    )
    params.update(kwargs)
    return OpenRiskLimitRule(**params)


def trading_day_rule(rule_type: RuleType = RuleType.MIN_TRADING_DAYS,
                     value: int = 5, **kwargs) -> TradingDayRule:
    params = dict(
        rule_id="r.days",
        rule_type=rule_type,
        enabled=True,
        severity=RuleSeverity.ADVISORY,
        source=kwargs.pop("source", official_source()),
        effective_from=kwargs.pop("effective_from", PACK_EFFECTIVE_FROM),
        telemetry_requirements=required_telemetry_for(rule_type),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
        timezone_name="America/New_York",
        limit=Limit(basis=LimitBasis.TRADING_DAYS, value=value),
        qualifying_criterion="ANY_CLOSED_TRADE",
    )
    params.update(kwargs)
    return TradingDayRule(**params)


def simple_pack(rules, identity: RulePackIdentity | None = None, **kwargs) -> RulePack:
    return compile_rule_pack(identity or pack_identity(), rules, **kwargs)



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 1-6  DETERMINISTIC IDENTITY
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_01_rule_pack_id_is_deterministic():
    """The same governed rules always produce the same id."""
    first = simple_pack([daily_loss_rule()])
    second = simple_pack([daily_loss_rule()])
    assert first.rule_pack_id == second.rule_pack_id
    assert first.rule_pack_id.startswith("prp1:")


def test_02_same_semantic_content_same_id_regardless_of_field_order():
    """Canonical order independence: declaration order and key order are irrelevant."""
    rules = [daily_loss_rule(), drawdown_rule()]
    forward = simple_pack(rules)
    reversed_pack = simple_pack(list(reversed(rules)))
    assert forward.rule_pack_id == reversed_pack.rule_pack_id
    # 5.0 and 5 are the same semantic limit, so they hash identically.
    assert (
        simple_pack([daily_loss_rule(value=5)]).rule_pack_id
        == simple_pack([daily_loss_rule(value=5.0)]).rule_pack_id
    )


def test_03_changed_rule_produces_a_new_id():
    """Any material rule change yields a new pack id and version."""
    base = simple_pack([daily_loss_rule(value=5.0)])
    changed = simple_pack([daily_loss_rule(value=6.0)])
    assert base.rule_pack_id != changed.rule_pack_id
    # Immutability: the old pack object is untouched and still resolves.
    assert base.rules[0].limit.value == 5.0


def test_04_phase_change_produces_a_new_id():
    phase1 = simple_pack([daily_loss_rule()], pack_identity(phase=RulePhase.EVALUATION_PHASE_1))
    phase2 = simple_pack([daily_loss_rule()], pack_identity(phase=RulePhase.EVALUATION_PHASE_2))
    funded = simple_pack([daily_loss_rule()], pack_identity(phase=RulePhase.FUNDED))
    ids = {phase1.rule_pack_id, phase2.rule_pack_id, funded.rule_pack_id}
    assert len(ids) == 3


def test_05_account_tier_change_produces_a_new_id():
    """A percentage rule does not imply identical absolute limits across tiers."""
    base = simple_pack([daily_loss_rule()], pack_identity(account_size=50_000))
    bigger = simple_pack([daily_loss_rule()], pack_identity(account_size=100_000))
    assert base.rule_pack_id != bigger.rule_pack_id
    assert base.identity.tier_label == "50K"
    assert bigger.identity.tier_label == "100K"


def test_06_provider_and_program_identity_are_exact_and_required():
    rules = [daily_loss_rule()]
    assert (
        derive_rule_pack_id(pack_identity(provider="A"), rules)
        != derive_rule_pack_id(pack_identity(provider="B"), rules)
    )
    assert (
        derive_rule_pack_id(pack_identity(program="P1"), rules)
        != derive_rule_pack_id(pack_identity(program="P2"), rules)
    )
    for field, value in (("provider", ""), ("program", "  "), ("currency", "US"),
                         ("rule_pack_version", "")):
        with pytest.raises(RulePackValidationError):
            pack_identity(**{field: value})


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 6-9, 36-38  LIMIT BASIS / CURRENCY SEMANTICS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_07_limit_requires_an_explicit_basis():
    """A bare number is not a rule: the basis is mandatory."""
    with pytest.raises(RulePackValidationError):
        Limit(basis=None, value=5.0)  # type: ignore[arg-type]
    assert pct(LimitBasis.PERCENT_INITIAL_BALANCE, 5.0).basis is LimitBasis.PERCENT_INITIAL_BALANCE


def test_08_percentage_basis_defines_what_it_is_a_percentage_of():
    start_of_day = pct(LimitBasis.PERCENT_START_OF_DAY_BALANCE, 5.0)
    high_water = pct(LimitBasis.PERCENT_HIGH_WATER_EQUITY, 5.0)
    assert start_of_day.is_percentage_of(LimitBasis.PERCENT_START_OF_DAY_BALANCE)
    assert not start_of_day.is_percentage_of(LimitBasis.PERCENT_HIGH_WATER_EQUITY)
    # Same 5%, different basis => a materially different rule.
    assert not start_of_day.same_semantics_as(high_water)


def test_09_daily_loss_absolute_money_limit_is_supported():
    absolute = Limit(
        basis=LimitBasis.ABSOLUTE_MONEY,
        value=500.0,
        currency_semantics=CurrencySemantics.FIXED_RULE_CURRENCY,
        rule_currency="USD",
    )
    rule = daily_loss_rule(limit=absolute)
    assert rule.limit.basis is LimitBasis.ABSOLUTE_MONEY
    assert rule.limit.rule_currency == "USD"


def test_10_daily_loss_percentage_requires_currency_semantics():
    with pytest.raises(RulePackValidationError) as exc:
        Limit(basis=LimitBasis.PERCENT_INITIAL_BALANCE, value=5.0)
    assert "CURRENCY_SEMANTICS" in str(exc.value)


def test_11_fixed_rule_currency_and_conversion_semantics_are_explicit():
    with pytest.raises(RulePackValidationError):
        Limit(
            basis=LimitBasis.ABSOLUTE_MONEY,
            value=100.0,
            currency_semantics=CurrencySemantics.FIXED_RULE_CURRENCY,
        )
    with pytest.raises(RulePackValidationError) as exc:
        Limit(
            basis=LimitBasis.ABSOLUTE_MONEY,
            value=100.0,
            currency_semantics=CurrencySemantics.CONVERTED_REFERENCE_CURRENCY,
            rule_currency="USD",
        )
    assert "CONVERSION_SOURCE" in str(exc.value)
    converted = Limit(
        basis=LimitBasis.ABSOLUTE_MONEY,
        value=100.0,
        currency_semantics=CurrencySemantics.CONVERTED_REFERENCE_CURRENCY,
        rule_currency="USD",
        conversion_source="GOVERNED_FX_FEED_V1",
    )
    assert converted.conversion_source == "GOVERNED_FX_FEED_V1"



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 12-17  DRAWDOWN (STATIC vs TRAILING, NEVER BLENDED)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_12_static_drawdown_is_anchored_to_initial_balance():
    rule = drawdown_rule(DrawdownKind.STATIC, value=10.0)
    assert rule.kind is DrawdownKind.STATIC
    assert rule.anchor is LimitBasis.PERCENT_INITIAL_BALANCE
    assert rule.limit.basis is LimitBasis.PERCENT_INITIAL_BALANCE
    assert not rule.is_trailing


def test_13_trailing_drawdown_is_anchored_to_high_water_equity():
    rule = drawdown_rule(DrawdownKind.TRAILING, value=8.0)
    assert rule.kind is DrawdownKind.TRAILING
    assert rule.anchor is LimitBasis.PERCENT_HIGH_WATER_EQUITY
    assert rule.trail_reference == "EQUITY"
    assert rule.trail_update_cadence == "EOD"
    assert rule.is_trailing


def test_14_high_water_basis_differs_from_initial_basis():
    static = drawdown_rule(DrawdownKind.STATIC, rule_id="r.dd.static")
    trailing = drawdown_rule(DrawdownKind.TRAILING, rule_id="r.dd.trailing")
    assert static.limit.basis is not trailing.limit.basis
    # An 8% trailing and an 8% static limit are NOT the same rule.
    pack = simple_pack([static, trailing])
    codes = {c.code for c in pack.conflicts}
    assert "TRAILING_AND_STATIC_DRAWDOWN_BOTH_DECLARED" in codes
    assert pack.status is RulePackStatus.AMBIGUOUS
    assert not pack.is_usable


def test_15_static_drawdown_may_not_declare_trail_semantics():
    with pytest.raises(RulePackValidationError) as exc:
        drawdown_rule(DrawdownKind.STATIC, trail_reference="EQUITY")
    assert "STATIC_DRAWDOWN_MUST_NOT_DECLARE_TRAIL" in str(exc.value)


def test_16_trailing_drawdown_requires_reference_and_cadence():
    with pytest.raises(RulePackValidationError) as exc:
        drawdown_rule(DrawdownKind.TRAILING, trail_reference=None)
    assert "TRAIL_REFERENCE" in str(exc.value)
    with pytest.raises(RulePackValidationError) as exc:
        drawdown_rule(DrawdownKind.TRAILING, trail_update_cadence=None)
    assert "TRAIL_CADENCE" in str(exc.value)


def test_17_drawdown_kind_and_anchor_must_agree():
    with pytest.raises(RulePackValidationError) as exc:
        drawdown_rule(DrawdownKind.STATIC, anchor=LimitBasis.PERCENT_HIGH_WATER_EQUITY)
    assert "STATIC_DRAWDOWN_ANCHOR_MUST_BE_INITIAL" in str(exc.value)
    with pytest.raises(RulePackValidationError) as exc:
        drawdown_rule(DrawdownKind.TRAILING, anchor=LimitBasis.PERCENT_INITIAL_BALANCE)
    assert "TRAILING_DRAWDOWN_ANCHOR_MUST_BE_HIGH_WATER" in str(exc.value)



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 9  DAILY-LOSS RESET SEMANTICS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_18_daily_loss_requires_an_explicit_reset_timezone():
    with pytest.raises(RulePackValidationError) as exc:
        daily_loss_rule(timezone_name=None)
    assert "DAILY_RESET_TIMEZONE_REQUIRED" in str(exc.value)


def test_19_daily_reset_clock_and_policy_are_modelled_not_computed():
    rule = daily_loss_rule()
    assert rule.reset_policy is DailyResetPolicy.CALENDAR_DAY
    assert rule.reset_time == time(0, 0)
    assert rule.breach_persists_until_reset is True
    with pytest.raises(RulePackValidationError):
        daily_loss_rule(reset_policy=DailyResetPolicy.NONE, reset_time=time(0, 0))


def test_20_daily_loss_pnl_components_are_explicit():
    closed_only = daily_loss_rule()
    assert closed_only.pnl_components.include_floating_pnl is False
    with_floating = daily_loss_rule(pnl_components=PnLComponents(include_floating_pnl=True))
    assert with_floating.pnl_components.include_floating_pnl is True
    # Different evidence semantics => a different governed rule => a new id.
    assert simple_pack([closed_only]).rule_pack_id != simple_pack([with_floating]).rule_pack_id


def test_21_daily_profit_limit_is_a_separate_rule_kind():
    rule = DailyProfitRule(
        rule_id="r.daily_profit",
        rule_type=RuleType.DAILY_PROFIT_LIMIT,
        enabled=True,
        severity=RuleSeverity.BREACH,
        source=official_source(),
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.DAILY_PROFIT_LIMIT),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        limit=pct(LimitBasis.PERCENT_START_OF_DAY_EQUITY, 4.0),
    )
    assert rule.rule_type is RuleType.DAILY_PROFIT_LIMIT
    assert simple_pack([rule]).has_rule(RuleType.DAILY_PROFIT_LIMIT)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 13  PROFIT TARGET
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_22_profit_target_states_amount_basis_and_timing():
    rule = ProfitTargetRule(
        rule_id="r.target",
        rule_type=RuleType.PROFIT_TARGET,
        enabled=True,
        severity=RuleSeverity.ADVISORY,
        source=official_source(),
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.PROFIT_TARGET),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
        timezone_name="America/New_York",
        limit=pct(LimitBasis.PERCENT_INITIAL_BALANCE, 8.0),
    )
    assert rule.limit.basis is LimitBasis.PERCENT_INITIAL_BALANCE
    assert rule.evaluation_timing is EvaluationTimeBasis.END_OF_DAY
    assert rule.counts_floating_profit is False
    absolute = ProfitTargetRule(
        rule_id="r.target_abs",
        rule_type=RuleType.PROFIT_TARGET,
        enabled=True,
        severity=RuleSeverity.ADVISORY,
        source=official_source(),
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.PROFIT_TARGET),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
        timezone_name="America/New_York",
        limit=Limit(
            basis=LimitBasis.ABSOLUTE_MONEY,
            value=8000.0,
            currency_semantics=CurrencySemantics.ACCOUNT_CURRENCY,
        ),
    )
    assert absolute.limit.is_monetary


def test_23_profit_target_minimum_days_dependency_requires_day_telemetry():
    with pytest.raises(RulePackValidationError) as exc:
        ProfitTargetRule(
            rule_id="r.target_days",
            rule_type=RuleType.PROFIT_TARGET,
            enabled=True,
            severity=RuleSeverity.ADVISORY,
            source=official_source(),
            effective_from=PACK_EFFECTIVE_FROM,
            evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
            timezone_name="America/New_York",
            limit=pct(LimitBasis.PERCENT_INITIAL_BALANCE, 8.0),
            minimum_days_required=5,
        )
    assert "TRADING_DAY_TELEMETRY" in str(exc.value)



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 10  TRADING DAY CONTRACT
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_24_min_trading_days_is_explicit():
    rule = trading_day_rule(RuleType.MIN_TRADING_DAYS, value=5)
    assert rule.rule_type is RuleType.MIN_TRADING_DAYS
    assert rule.limit.basis is LimitBasis.TRADING_DAYS
    assert rule.limit.value == 5.0
    assert rule.qualifying_criterion == "ANY_CLOSED_TRADE"
    assert rule.consecutive is ConsecutiveMode.NON_CONSECUTIVE
    assert rule.timezone_name == "America/New_York"


def test_25_max_trading_days_and_calendar_basis():
    rule = trading_day_rule(
        RuleType.MAX_TRADING_DAYS,
        value=30,
        limit=Limit(basis=LimitBasis.CALENDAR_DAYS, value=30),
        day_count_basis=DayCountBasis.CALENDAR_DAYS,
        consecutive=ConsecutiveMode.CONSECUTIVE,
    )
    assert rule.rule_type is RuleType.MAX_TRADING_DAYS
    assert rule.day_count_basis is DayCountBasis.CALENDAR_DAYS
    assert rule.consecutive is ConsecutiveMode.CONSECUTIVE


def test_26_trading_day_criterion_is_never_guessed():
    """An ambiguous source leaves the criterion unset; the pack is INCOMPLETE."""
    rule = trading_day_rule(qualifying_criterion=None)
    pack = simple_pack([rule])
    codes = {c.code for c in pack.conflicts}
    assert "TRADING_DAY_CRITERION_UNSPECIFIED" in codes
    assert pack.status is RulePackStatus.INCOMPLETE
    assert not pack.is_usable


def test_27_trading_day_limit_basis_is_validated():
    with pytest.raises(RulePackValidationError) as exc:
        trading_day_rule(limit=Limit(basis=LimitBasis.LOT_COUNT, value=5))
    assert "TRADING_DAY_LIMIT_BASIS_INVALID" in str(exc.value)


def test_28_inactivity_rule_requires_semantics():
    with pytest.raises(RulePackValidationError) as exc:
        InactivityRule(
            rule_id="r.inactivity",
            rule_type=RuleType.INACTIVITY_RULE,
            enabled=True,
            severity=RuleSeverity.WARNING,
            source=official_source(),
            effective_from=PACK_EFFECTIVE_FROM,
            telemetry_requirements=required_telemetry_for(RuleType.INACTIVITY_RULE),
            evaluation_time_basis=EvaluationTimeBasis.PER_DAY,
            timezone_name="America/New_York",
        )
    assert "INACTIVITY_RULE_REQUIRES_SEMANTICS" in str(exc.value)
    rule = InactivityRule(
        rule_id="r.inactivity",
        rule_type=RuleType.INACTIVITY_RULE,
        enabled=True,
        severity=RuleSeverity.WARNING,
        source=official_source(),
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.INACTIVITY_RULE),
        evaluation_time_basis=EvaluationTimeBasis.PER_DAY,
        timezone_name="America/New_York",
        max_inactive_duration="P7D",
    )
    assert rule.max_inactive_duration == "P7D"



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 11-12  POSITION / LOT / OPEN-RISK (KEPT DISTINCT)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_29_max_lot_rule_uses_lot_count_basis():
    rule = PositionLimitRule(
        rule_id="r.lots",
        rule_type=RuleType.MAX_POSITION_SIZE,
        enabled=True,
        severity=RuleSeverity.BREACH,
        source=official_source(),
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.MAX_POSITION_SIZE),
        limit=Limit(basis=LimitBasis.LOT_COUNT, value=2.0),
    )
    assert rule.limit.basis is LimitBasis.LOT_COUNT
    assert not rule.limit.is_monetary


def test_30_max_position_count_is_a_distinct_basis():
    rule = PositionLimitRule(
        rule_id="r.count",
        rule_type=RuleType.MAX_OPEN_POSITIONS,
        enabled=True,
        severity=RuleSeverity.BREACH,
        source=official_source(),
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.MAX_OPEN_POSITIONS),
        limit=Limit(basis=LimitBasis.POSITION_COUNT, value=5.0),
    )
    assert rule.limit.basis is LimitBasis.POSITION_COUNT
    with pytest.raises(RulePackValidationError) as exc:
        PositionLimitRule(
            rule_id="r.bad",
            rule_type=RuleType.MAX_POSITION_SIZE,
            enabled=True,
            severity=RuleSeverity.BREACH,
            source=official_source(),
            effective_from=PACK_EFFECTIVE_FROM,
            telemetry_requirements=required_telemetry_for(RuleType.MAX_POSITION_SIZE),
            limit=pct(LimitBasis.PERCENT_INITIAL_BALANCE, 5.0),
        )
    assert "POSITION_LIMIT_BASIS_INVALID" in str(exc.value)


def test_31_symbol_specific_and_asset_class_overrides_are_explicit():
    symbol_rule = open_risk_rule(
        "r.risk_gold", applies_to_symbols=("XAUUSD",), precedence=PrecedenceLevel.INSTRUMENT
    )
    assert symbol_rule.is_instrument_specific
    assert symbol_rule.precedence is PrecedenceLevel.INSTRUMENT
    override = ScopeOverride(symbols=("XAUUSD",))
    assert override.matches("XAUUSD")
    assert not override.matches("EURUSD")
    with pytest.raises(RulePackValidationError) as exc:
        ScopeOverride()
    assert "SCOPE_OVERRIDE_MUST_NARROW_SOMETHING" in str(exc.value)


def test_32_open_risk_limit_is_monetary_and_block2_supported():
    """Max open risk is the direct consumer of accepted Block 2B/2C telemetry."""
    rule = open_risk_rule()
    assert rule.limit.is_monetary
    assert support_for(RuleType.MAX_OPEN_RISK) is EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY
    assert TelemetryRequirement.OPEN_RISK_TOTAL in rule.telemetry_requirements
    assert block2_source_for(TelemetryRequirement.OPEN_RISK_TOTAL) == "2B"


def test_33_correlated_risk_requires_correlation_telemetry():
    with pytest.raises(RulePackValidationError) as exc:
        open_risk_rule(
            "r.corr",
            rule_type=RuleType.MAX_CORRELATED_RISK,
            correlation_cluster_scoped=True,
            telemetry_requirements=(TelemetryRequirement.OPEN_RISK_TOTAL,),
        )
    assert "CORRELATION_TELEMETRY" in str(exc.value)
    good = open_risk_rule(
        "r.corr", rule_type=RuleType.MAX_CORRELATED_RISK, correlation_cluster_scoped=True
    )
    assert TelemetryRequirement.SYMBOL_CORRELATION_CLUSTERS in good.telemetry_requirements


def test_34_directional_and_per_symbol_risk_are_separate_rule_types():
    directional = open_risk_rule("r.dir", rule_type=RuleType.MAX_DIRECTIONAL_RISK)
    per_symbol = open_risk_rule("r.sym", rule_type=RuleType.MAX_RISK_PER_SYMBOL)
    pack = simple_pack([directional, per_symbol])
    assert pack.has_rule(RuleType.MAX_DIRECTIONAL_RISK)
    assert pack.has_rule(RuleType.MAX_RISK_PER_SYMBOL)
    assert len(pack.rules_of_type(RuleType.MAX_DIRECTIONAL_RISK)) == 1



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 13  NEWS RESTRICTION
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def news_rule(**kwargs) -> NewsTradingRestrictionRule:
    params = dict(
        rule_id="r.news",
        rule_type=RuleType.NEWS_TRADING_RESTRICTION,
        enabled=True,
        severity=RuleSeverity.BREACH,
        source=official_source(),
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.NEWS_TRADING_RESTRICTION),
        evaluation_time_basis=EvaluationTimeBasis.EVENT_DRIVEN,
        timezone_name="Europe/London",
        blackouts_high_impact=True,
        importance_levels=(NewsImportance.HIGH,),
        pre_event_blackout_minutes=2,
        post_event_blackout_minutes=2,
    )
    params.update(kwargs)
    return NewsTradingRestrictionRule(**params)


def test_35_news_rule_requires_an_explicit_blackout_window():
    """A source that bans news but states no window is AMBIGUOUS, never guessed."""
    rule = news_rule(pre_event_blackout_minutes=None, post_event_blackout_minutes=None)
    assert rule.blackout_window_unspecified is True
    assert not rule.has_explicit_blackout
    pack = simple_pack([rule])
    codes = {c.code for c in pack.conflicts}
    assert "NEWS_BLACKOUT_WINDOW_UNSPECIFIED" in codes
    assert pack.status is RulePackStatus.AMBIGUOUS
    assert not pack.is_usable


def test_36_news_rule_states_full_blackout_semantics():
    rule = news_rule(affected_symbols=("EURUSD",), affected_currencies=("USD",))
    assert rule.pre_event_blackout_minutes == 2
    assert rule.post_event_blackout_minutes == 2
    assert rule.has_explicit_blackout
    assert rule.prohibit_opening_trades is True
    assert rule.prohibit_closing_trades is False
    assert rule.prohibit_modifying_sl_tp is False
    assert rule.prohibit_holding_through_event is True
    assert rule.requires_economic_calendar is True
    assert rule.affected_symbols == ("EURUSD",)


def test_37_news_blackout_minutes_are_range_validated():
    with pytest.raises(RulePackValidationError) as exc:
        news_rule(pre_event_blackout_minutes=-1)
    assert "BLACKOUT_MINUTES_OUT_OF_RANGE" in str(exc.value)
    with pytest.raises(RulePackValidationError):
        news_rule(post_event_blackout_minutes=5000)



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 14  WEEKEND / OVERNIGHT HOLD (NEVER IMPLYING EACH OTHER)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def hold_rule(kind: HoldRestrictionKind, holding: bool = False, **kwargs) -> HoldRestrictionRule:
    rule_type = (
        RuleType.WEEKEND_HOLD_RESTRICTION
        if kind is HoldRestrictionKind.WEEKEND_HOLD
        else RuleType.OVERNIGHT_HOLD_RESTRICTION
    )
    params = dict(
        rule_id=f"r.hold.{kind.value.lower()}",
        rule_type=rule_type,
        enabled=True,
        severity=RuleSeverity.TERMINATION,
        source=official_source(),
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(rule_type),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="Europe/London",
        kind=kind,
        holding_permitted=holding,
    )
    params.update(kwargs)
    return HoldRestrictionRule(**params)


def test_38_weekend_hold_forbidden_requires_cutoff_timezone_and_clock():
    with pytest.raises(RulePackValidationError) as exc:
        hold_rule(HoldRestrictionKind.WEEKEND_HOLD, timezone_name=None)
    assert "CUTOFF_TIMEZONE" in str(exc.value)
    with pytest.raises(RulePackValidationError) as exc:
        hold_rule(
            HoldRestrictionKind.WEEKEND_HOLD,
            must_close_before_market_close=True,
            cutoff_time=None,
        )
    assert "REQUIRES_CUTOFF_TIME" in str(exc.value)
    rule = hold_rule(
        HoldRestrictionKind.WEEKEND_HOLD,
        must_close_before_market_close=True,
        cutoff_time=time(20, 0),
    )
    assert rule.kind is HoldRestrictionKind.WEEKEND_HOLD
    assert rule.cutoff_time == time(20, 0)


def test_39_overnight_hold_is_a_separate_rule_from_weekend_hold():
    """'No weekend hold' must NOT silently become 'no overnight hold'."""
    weekend = hold_rule(
        HoldRestrictionKind.WEEKEND_HOLD,
        must_close_before_market_close=True,
        cutoff_time=time(20, 0),
    )
    pack = simple_pack([weekend])
    assert pack.has_rule(RuleType.WEEKEND_HOLD_RESTRICTION)
    # Explicit absence: the pack declares no overnight rule at all.
    assert not pack.has_rule(RuleType.OVERNIGHT_HOLD_RESTRICTION)
    overnight = hold_rule(
        HoldRestrictionKind.OVERNIGHT_HOLD,
        must_close_before_market_close=True,
        cutoff_time=time(23, 55),
    )
    both = simple_pack([weekend, overnight])
    assert both.has_rule(RuleType.WEEKEND_HOLD_RESTRICTION)
    assert both.has_rule(RuleType.OVERNIGHT_HOLD_RESTRICTION)
    assert both.status is RulePackStatus.VALID


def test_40_weekend_hold_exceptions_and_crypto_exemption_are_modelled():
    rule = hold_rule(
        HoldRestrictionKind.WEEKEND_HOLD,
        must_close_before_market_close=True,
        cutoff_time=time(20, 0),
        allowed_asset_classes=("CRYPTO",),
        exceptions=("BTCUSD",),
        crypto_exempt=True,
    )
    assert rule.allowed_asset_classes == ("CRYPTO",)
    assert rule.exceptions == ("BTCUSD",)
    assert rule.crypto_exempt is True
    assert rule.pending_orders_included is True



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 15  AUTOMATION / EA / COPY TRADING PERMISSION
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def automation_rule(**kwargs) -> AutomationPermissionRule:
    params = dict(
        rule_id="r.ea",
        rule_type=RuleType.EA_AUTOMATION_PERMISSION,
        enabled=True,
        severity=RuleSeverity.BREACH,
        source=official_source(),
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.EA_AUTOMATION_PERMISSION),
        evaluation_time_basis=EvaluationTimeBasis.NOT_TIME_SENSITIVE,
    )
    params.update(kwargs)
    return AutomationPermissionRule(**params)


def fully_specified_ea() -> AutomationPermissionRule:
    return automation_rule(
        ea_allowed=AutomationVerdict.ALLOWED,
        algo_allowed=AutomationVerdict.ALLOWED,
        hft_allowed=AutomationVerdict.FORBIDDEN,
        latency_arbitrage_allowed=AutomationVerdict.FORBIDDEN,
        external_signal_allowed=AutomationVerdict.FORBIDDEN,
        requires_vps=AutomationVerdict.ALLOWED,
        requires_supported_platform=AutomationVerdict.ALLOWED,
    )


def test_41_ea_permission_explicit_allow_and_forbid():
    allowed = fully_specified_ea()
    assert not allowed.has_unspecified_verdict
    assert simple_pack([allowed]).status is RulePackStatus.VALID
    forbidden = automation_rule(
        ea_allowed=AutomationVerdict.FORBIDDEN,
        algo_allowed=AutomationVerdict.FORBIDDEN,
    )
    assert forbidden.ea_allowed is AutomationVerdict.FORBIDDEN


def test_42_unspecified_automation_verdict_is_ambiguous_not_permission():
    """Vague marketing wording must never be inferred as ALLOWED."""
    rule = automation_rule(ea_allowed=AutomationVerdict.UNSPECIFIED)
    assert rule.has_unspecified_verdict
    pack = simple_pack([rule])
    codes = {c.code for c in pack.conflicts}
    assert "UNSPECIFIED_AUTOMATION_VERDICT" in codes
    assert pack.status is RulePackStatus.AMBIGUOUS
    assert not pack.is_usable


def test_43_copy_trading_rule_is_independent_of_ea_permission():
    copy_rule = CopyTradingRestrictionRule(
        rule_id="r.copy",
        rule_type=RuleType.COPY_TRADING_RESTRICTION,
        enabled=True,
        severity=RuleSeverity.BREACH,
        source=official_source(),
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.COPY_TRADING_RESTRICTION),
        evaluation_time_basis=EvaluationTimeBasis.NOT_TIME_SENSITIVE,
        copy_trading_allowed=AutomationVerdict.FORBIDDEN,
        trade_copier_allowed=AutomationVerdict.FORBIDDEN,
        account_mirroring_allowed=AutomationVerdict.FORBIDDEN,
        external_signal_allowed=AutomationVerdict.FORBIDDEN,
        max_linked_accounts=1,
    )
    assert copy_rule.max_linked_accounts == 1
    assert not copy_rule.has_unspecified_verdict
    pack = simple_pack([fully_specified_ea(), copy_rule])
    assert pack.status is RulePackStatus.VALID
    assert pack.has_rule(RuleType.COPY_TRADING_RESTRICTION)
    with pytest.raises(RulePackValidationError) as exc:
        CopyTradingRestrictionRule(
            rule_id="r.copy_bad",
            rule_type=RuleType.COPY_TRADING_RESTRICTION,
            enabled=True,
            severity=RuleSeverity.BREACH,
            source=official_source(),
            effective_from=PACK_EFFECTIVE_FROM,
            telemetry_requirements=required_telemetry_for(RuleType.COPY_TRADING_RESTRICTION),
            evaluation_time_basis=EvaluationTimeBasis.NOT_TIME_SENSITIVE,
            max_linked_accounts=0,
        )
    assert "MAX_LINKED_ACCOUNTS_MUST_BE_POSITIVE" in str(exc.value)


def test_44_ip_device_location_restriction_is_optional_and_explicit():
    rule = IPRestrictionRule(
        rule_id="r.ip",
        rule_type=RuleType.IP_DEVICE_LOCATION_RESTRICTION,
        enabled=True,
        severity=RuleSeverity.WARNING,
        source=official_source(),
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.IP_DEVICE_LOCATION_RESTRICTION),
        evaluation_time_basis=EvaluationTimeBasis.NOT_TIME_SENSITIVE,
        ip_change_allowed=AutomationVerdict.FORBIDDEN,
        device_limit=2,
        location_restricted=AutomationVerdict.FORBIDDEN,
        vps_required=AutomationVerdict.ALLOWED,
        allowed_locations=("EU",),
    )
    assert rule.device_limit == 2
    assert rule.allowed_locations == ("EU",)
    # A firm with no IP rule simply declares none: explicit absence.
    assert not simple_pack([rule]).has_rule(RuleType.COPY_TRADING_RESTRICTION)



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 16  CONSISTENCY RULE
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def consistency_rule(**kwargs) -> ConsistencyRule:
    params = dict(
        rule_id="r.consistency",
        rule_type=RuleType.CONSISTENCY_RULE,
        enabled=True,
        severity=RuleSeverity.WARNING,
        source=official_source(),
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.CONSISTENCY_RULE),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
        timezone_name="Europe/London",
        metric="BEST_DAY_SHARE_OF_TOTAL_PROFIT",
        limit=pct(LimitBasis.PERCENT_CURRENT_BALANCE, 40.0),
        numerator_definition="BEST_SINGLE_DAY_NET_PROFIT",
        denominator_definition="TOTAL_NET_PROFIT_ACROSS_ALL_QUALIFYING_DAYS",
    )
    params.update(kwargs)
    return ConsistencyRule(**params)


def test_45_consistency_rule_requires_numerator_and_denominator():
    """'Consistency = 30%' is not a rule without its numerator and denominator."""
    with pytest.raises(RulePackValidationError) as exc:
        consistency_rule(numerator_definition="")
    assert "CONSISTENCY_NUMERATOR_REQUIRED" in str(exc.value)
    with pytest.raises(RulePackValidationError) as exc:
        consistency_rule(denominator_definition="  ")
    assert "CONSISTENCY_DENOMINATOR_REQUIRED" in str(exc.value)
    rule = consistency_rule()
    assert rule.numerator_definition == "BEST_SINGLE_DAY_NET_PROFIT"
    assert rule.denominator_definition == "TOTAL_NET_PROFIT_ACROSS_ALL_QUALIFYING_DAYS"
    assert rule.window is DayCountBasis.TRADING_DAYS
    assert rule.applies_to_payout is True
    assert rule.applies_to_challenge_pass is False


def test_46_consistency_limit_must_be_a_percentage():
    with pytest.raises(RulePackValidationError) as exc:
        consistency_rule(limit=Limit(basis=LimitBasis.TRADING_DAYS, value=5))
    assert "CONSISTENCY_LIMIT_MUST_BE_PERCENTAGE" in str(exc.value)


def test_47_consistency_metric_variants_are_representable():
    for metric in (
        "BEST_DAY_SHARE_OF_TOTAL_PROFIT",
        "LARGEST_DAY_CONTRIBUTION",
        "LOT_SIZE_CONSISTENCY",
        "RISK_CONSISTENCY",
        "DAILY_PROFIT_CONCENTRATION",
        "PAYOUT_CONSISTENCY",
    ):
        rule = consistency_rule(metric=metric)
        assert rule.metric == metric
        assert rule.limit.is_percentage



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 17  PAYOUT / REFUND / RESET
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def payout_rule(**kwargs) -> PayoutRule:
    params = dict(
        rule_id="r.payout",
        rule_type=RuleType.PAYOUT_ELIGIBILITY,
        enabled=True,
        severity=RuleSeverity.ADVISORY,
        source=official_source(),
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.PAYOUT_ELIGIBILITY),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
        timezone_name="Europe/London",
        cadence=PayoutCadence.BIWEEKLY,
        minimum_profit=pct(LimitBasis.PERCENT_INITIAL_BALANCE, 1.0),
    )
    params.update(kwargs)
    return PayoutRule(**params)


def test_48_payout_cadence_and_delays_are_explicit():
    rule = payout_rule(first_payout_delay_days=14, subsequent_payout_interval_days=14)
    assert rule.cadence is PayoutCadence.BIWEEKLY
    assert rule.first_payout_delay_days == 14
    assert rule.subsequent_payout_interval_days == 14
    with pytest.raises(RulePackValidationError) as exc:
        payout_rule(cadence=PayoutCadence.CUSTOM, subsequent_payout_interval_days=None)
    assert "CUSTOM_CADENCE_REQUIRES_INTERVAL" in str(exc.value)
    with pytest.raises(RulePackValidationError) as exc:
        payout_rule(cadence=PayoutCadence.NONE, subsequent_payout_interval_days=7)
    assert "CADENCE_NONE_CONFLICTS_WITH_INTERVAL" in str(exc.value)


def test_49_payout_minimum_profit_and_buffer_require_currency_semantics():
    with pytest.raises(RulePackValidationError):
        payout_rule(minimum_profit=Limit(basis=LimitBasis.ABSOLUTE_MONEY, value=200.0))
    rule = payout_rule(
        minimum_profit=Limit(
            basis=LimitBasis.ABSOLUTE_MONEY,
            value=200.0,
            currency_semantics=CurrencySemantics.ACCOUNT_CURRENCY,
        ),
        minimum_buffer=pct(LimitBasis.PERCENT_INITIAL_BALANCE, 0.5),
        minimum_profitable_days=2,
    )
    assert rule.minimum_profit.is_monetary
    assert rule.minimum_profitable_days == 2
    assert rule.minimum_buffer.is_percentage


def test_50_payout_requires_minimum_profit_when_a_cadence_exists():
    with pytest.raises(RulePackValidationError) as exc:
        payout_rule(minimum_profit=None)
    assert "PAYOUT_REQUIRES_MINIMUM_PROFIT" in str(exc.value)


def test_51_payout_profit_split_and_consistency_dependency_are_validated():
    with pytest.raises(RulePackValidationError) as exc:
        payout_rule(profit_split_percent=0)
    assert "PROFIT_SPLIT_OUT_OF_RANGE" in str(exc.value)
    with pytest.raises(RulePackValidationError) as exc:
        payout_rule(profit_split_percent=150)
    assert "PROFIT_SPLIT_OUT_OF_RANGE" in str(exc.value)
    # A consistency dependency must be backed by daily-profit evidence.
    with pytest.raises(RulePackValidationError) as exc:
        PayoutRule(
            rule_id="r.payout_no_series",
            rule_type=RuleType.PAYOUT_ELIGIBILITY,
            enabled=True,
            severity=RuleSeverity.ADVISORY,
            source=official_source(),
            effective_from=PACK_EFFECTIVE_FROM,
            evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
            timezone_name="Europe/London",
            cadence=PayoutCadence.BIWEEKLY,
            minimum_profit=pct(LimitBasis.PERCENT_INITIAL_BALANCE, 1.0),
            consistency_dependency=True,
        )
    assert "DAILY_PROFIT_SERIES" in str(exc.value)
    rule = payout_rule(profit_split_percent=80.0, consistency_dependency=True)
    assert rule.profit_split_percent == 80.0
    assert TelemetryRequirement.DAILY_PROFIT_SERIES in rule.telemetry_requirements


def test_52_payout_is_separate_from_challenge_pass_rules():
    """Payout eligibility is a different contract question from passing a phase."""
    pack = simple_pack([payout_rule()])
    assert pack.has_rule(RuleType.PAYOUT_ELIGIBILITY)
    assert not pack.has_rule(RuleType.PROFIT_TARGET)
    assert not pack.has_rule(RuleType.MIN_TRADING_DAYS)


def test_53_refund_and_reset_rules_are_modelled():
    refund = RefundRule(
        rule_id="r.refund",
        rule_type=RuleType.REFUND_RULE,
        enabled=True,
        severity=RuleSeverity.ADVISORY,
        source=official_source(),
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.REFUND_RULE),
        evaluation_time_basis=EvaluationTimeBasis.NOT_TIME_SENSITIVE,
        refundable=True,
        refund_trigger="CHALLENGE_FAILURE",
        refund_amount=pct(LimitBasis.PERCENT_INITIAL_BALANCE, 100.0),
    )
    assert refund.refund_trigger == "CHALLENGE_FAILURE"
    with pytest.raises(RulePackValidationError) as exc:
        RefundRule(
            rule_id="r.refund_bad",
            rule_type=RuleType.REFUND_RULE,
            enabled=True,
            severity=RuleSeverity.ADVISORY,
            source=official_source(),
            effective_from=PACK_EFFECTIVE_FROM,
            telemetry_requirements=required_telemetry_for(RuleType.REFUND_RULE),
            evaluation_time_basis=EvaluationTimeBasis.NOT_TIME_SENSITIVE,
            refundable=True,
            refund_trigger="",
        )
    assert "REFUND_TRIGGER_REQUIRED" in str(exc.value)
    reset = ResetRule(
        rule_id="r.reset",
        rule_type=RuleType.RESET_RULE,
        enabled=True,
        severity=RuleSeverity.ADVISORY,
        source=official_source(),
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.RESET_RULE),
        evaluation_time_basis=EvaluationTimeBasis.NOT_TIME_SENSITIVE,
        reset_permitted=True,
        reset_fee=Limit(
            basis=LimitBasis.ABSOLUTE_MONEY,
            value=0.0,
            currency_semantics=CurrencySemantics.ACCOUNT_CURRENCY,
        ),
        max_resets=0,
    )
    assert reset.max_resets == 0
    assert simple_pack([refund, reset]).status is RulePackStatus.VALID



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 24, 58  PRECEDENCE / OVERRIDE MODEL
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_54_precedence_levels_are_ordered_firm_to_instrument():
    assert (
        PrecedenceLevel.FIRM_DEFAULT.value
        < PrecedenceLevel.PROGRAM.value
        < PrecedenceLevel.PHASE.value
        < PrecedenceLevel.ACCOUNT_SIZE.value
        < PrecedenceLevel.INSTRUMENT.value
    )


def test_55_higher_precedence_overrides_lower_deterministically():
    firm = daily_loss_rule("r.dl", value=5.0, precedence=PrecedenceLevel.FIRM_DEFAULT)
    program = daily_loss_rule("r.dl", value=6.0, precedence=PrecedenceLevel.PROGRAM)
    phase = daily_loss_rule("r.dl", value=7.0, precedence=PrecedenceLevel.PHASE)
    resolved = resolve_precedence([firm, program, phase])
    assert len(resolved) == 1
    assert resolved[0].limit.value == 7.0
    # Resolution does not depend on declaration order.
    assert resolve_precedence([phase, firm, program])[0].limit.value == 7.0
    assert resolve_precedence([program, phase, firm])[0].limit.value == 7.0


def test_56_same_precedence_conflict_is_reported_not_resolved():
    """'Last one wins' is never a resolution: the pack fails closed."""
    a = daily_loss_rule("r.dl_a", value=5.0, precedence=PrecedenceLevel.PROGRAM)
    b = daily_loss_rule("r.dl_b", value=6.0, precedence=PrecedenceLevel.PROGRAM)
    pack = simple_pack([a, b])
    codes = {c.code for c in pack.conflicts}
    assert "SAME_PRECEDENCE_CONFLICT" in codes
    assert pack.status is RulePackStatus.AMBIGUOUS
    assert not pack.is_usable
    with pytest.raises(RulePackConflictError):
        pack.assert_usable()


def test_57_duplicate_rule_ids_are_fatal():
    pack = simple_pack([daily_loss_rule("r.same", 5.0), daily_loss_rule("r.same", 5.0)])
    codes = {c.code for c in pack.conflicts}
    assert "DUPLICATE_RULE_ID" in codes
    assert pack.status is RulePackStatus.INVALID


def test_58_account_size_and_phase_overrides_change_identity_not_order():
    phase_override = daily_loss_rule("r.dl", value=4.0, precedence=PrecedenceLevel.PHASE)
    firm_default = daily_loss_rule("r.dl", value=5.0, precedence=PrecedenceLevel.FIRM_DEFAULT)
    assert pack_identity(account_size=25_000).tier_label == "25K"
    assert resolve_precedence([firm_default, phase_override])[0].limit.value == 4.0
    # A tier change is a different identity, never a merge.
    assert (
        simple_pack([firm_default], pack_identity(account_size=25_000)).rule_pack_id
        != simple_pack([firm_default], pack_identity(account_size=100_000)).rule_pack_id
    )


def test_59_instrument_scope_is_a_separate_precedence_dimension():
    firm_wide = daily_loss_rule("r.dl", 5.0, precedence=PrecedenceLevel.FIRM_DEFAULT)
    gold_only = daily_loss_rule(
        "r.dl_gold", 3.0, precedence=PrecedenceLevel.INSTRUMENT, applies_to_symbols=("XAUUSD",)
    )
    resolved = resolve_precedence([firm_wide, gold_only])
    # Narrower scope coexists; it does not overwrite the firm-wide rule.
    assert len(resolved) == 2
    assert {r.limit.value for r in resolved} == {5.0, 3.0}



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 26  CONFLICT / DEFECT DETECTION (FAIL CLOSED)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_60_negative_and_out_of_range_thresholds_are_rejected():
    with pytest.raises(RulePackValidationError) as exc:
        pct(LimitBasis.PERCENT_INITIAL_BALANCE, -5.0)
    assert "NEGATIVE_THRESHOLD" in str(exc.value)
    with pytest.raises(RulePackValidationError) as exc:
        pct(LimitBasis.PERCENT_INITIAL_BALANCE, 0.0)
    assert "ZERO_PERCENT_THRESHOLD" in str(exc.value)
    with pytest.raises(RulePackValidationError) as exc:
        pct(LimitBasis.PERCENT_INITIAL_BALANCE, 150.0)
    assert "ABOVE_100" in str(exc.value)
    with pytest.raises(RulePackValidationError) as exc:
        Limit(basis=LimitBasis.TRADING_DAYS, value=0)
    assert "COUNT_THRESHOLD_MUST_BE_POSITIVE" in str(exc.value)
    with pytest.raises(RulePackValidationError) as exc:
        pct(LimitBasis.PERCENT_INITIAL_BALANCE, float("nan"))
    assert "NON_FINITE" in str(exc.value)
    with pytest.raises(RulePackValidationError) as exc:
        pct(LimitBasis.PERCENT_INITIAL_BALANCE, float("inf"))
    assert "NON_FINITE" in str(exc.value)


def test_61_missing_percentage_basis_and_missing_timezone_fail_closed():
    with pytest.raises(RulePackValidationError) as exc:
        Limit(basis="PERCENT_SOMETHING", value=5.0)  # type: ignore[arg-type]
    assert "LIMIT_BASIS_REQUIRED" in str(exc.value)
    rule = RuleBase(
        rule_id="r.bare",
        rule_type=RuleType.PROFIT_TARGET,
        enabled=True,
        severity=RuleSeverity.ADVISORY,
        source=official_source(),
        effective_from=PACK_EFFECTIVE_FROM,
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
        timezone_name=None,
    )
    pack = simple_pack([rule])
    codes = {c.code for c in pack.conflicts}
    assert "MISSING_TIMEZONE" in codes
    assert pack.status is RulePackStatus.INCOMPLETE
    assert not pack.is_usable


def test_62_bad_effective_dates_are_rejected_at_construction():
    with pytest.raises(RulePackValidationError) as exc:
        RulePackIdentity(
            provider="F", program="P", phase=RulePhase.FUNDED, account_size=100_000,
            currency="USD", rule_pack_version="1.0.0",
            effective_from=datetime(2026, 6, 1, tzinfo=UTC),
            effective_to=datetime(2026, 1, 1, tzinfo=UTC),
        )
    assert "EFFECTIVE_TO_BEFORE_EFFECTIVE_FROM" in str(exc.value)
    with pytest.raises(RulePackValidationError) as exc:
        RulePackIdentity(
            provider="F", program="P", phase=RulePhase.FUNDED, account_size=100_000,
            currency="USD", rule_pack_version="1.0.0",
            effective_from=datetime(2026, 1, 1),
        )
    assert "NAIVE_DATETIME" in str(exc.value)
    with pytest.raises(RulePackValidationError) as exc:
        pack_identity(account_size=0)
    assert "ACCOUNT_SIZE_MUST_BE_POSITIVE" in str(exc.value)
    with pytest.raises(RulePackValidationError) as exc:
        pack_identity(account_size="100000")  # type: ignore[arg-type]
    assert "ACCOUNT_SIZE_MUST_BE_INT" in str(exc.value)


def test_63_unknown_timezone_is_rejected_and_never_defaults_to_local():
    with pytest.raises(RulePackValidationError) as exc:
        daily_loss_rule(timezone_name="Mars/Olympus_Mons")
    assert "UNKNOWN_IANA_TIMEZONE" in str(exc.value)
    assert daily_loss_rule(timezone_name="UTC").timezone_name == "UTC"



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 27-28  PROVENANCE AND SOURCE AUTHORITY
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_64_source_provenance_is_mandatory_and_recorded():
    source = official_source()
    assert source.source_type is SourceType.OFFICIAL_RULE_PAGE
    assert source.retrieved_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert source.source_effective_date == date(2026, 1, 1)
    with pytest.raises(RulePackValidationError) as exc:
        RuleSource(source_type=SourceType.OFFICIAL_RULE_PAGE, source_reference="  ")
    assert "SOURCE_REFERENCE_REQUIRED" in str(exc.value)
    with pytest.raises(RulePackValidationError) as exc:
        RuleSource(
            source_type=SourceType.OFFICIAL_RULE_PAGE,
            source_reference="x",
            retrieved_at=datetime(2026, 1, 1),  # naive
        )
    assert "NAIVE_DATETIME" in str(exc.value)


def test_65_source_authority_ordering_is_explicit():
    assert (
        SOURCE_AUTHORITY[SourceType.CONTRACT_TERMS]
        > SOURCE_AUTHORITY[SourceType.OFFICIAL_RULE_PAGE]
        > SOURCE_AUTHORITY[SourceType.OFFICIAL_FAQ]
        > SOURCE_AUTHORITY[SourceType.OFFICIAL_SUPPORT_CLARIFICATION]
        > SOURCE_AUTHORITY[SourceType.THIRD_PARTY_COMPARISON]
        > SOURCE_AUTHORITY[SourceType.MANUAL_ASSUMPTION]
    )


def test_66_a_rule_is_official_only_when_its_source_says_so():
    official = RuleSource(
        source_type=SourceType.OFFICIAL_RULE_PAGE, source_reference="https://firm/rules"
    )
    assumption = RuleSource(
        source_type=SourceType.MANUAL_ASSUMPTION, source_reference="analyst-note"
    )
    third_party = RuleSource(
        source_type=SourceType.THIRD_PARTY_COMPARISON, source_reference="blog"
    )
    assert official.is_official
    assert not assumption.is_official
    assert not third_party.is_official
    assert official.authority_rank > assumption.authority_rank


def test_67_conflicting_sources_are_exposed_not_reconciled():
    """Two sources stating different limits must surface, never be merged."""
    faq = RuleSource(source_type=SourceType.OFFICIAL_FAQ, source_reference="https://firm/faq")
    rule_page = RuleSource(
        source_type=SourceType.OFFICIAL_RULE_PAGE, source_reference="https://firm/rules"
    )
    a = daily_loss_rule("r.dl_faq", value=5.0, source=faq, precedence=PrecedenceLevel.PROGRAM)
    b = daily_loss_rule("r.dl_page", value=4.0, source=rule_page, precedence=PrecedenceLevel.PROGRAM)
    pack = simple_pack([a, b])
    codes = {c.code for c in pack.conflicts}
    assert "SAME_PRECEDENCE_CONFLICT" in codes
    # The authority ordering is reported so a human can resolve it.
    disagreement = [c for c in pack.conflicts if c.code == "SOURCE_DISAGREEMENT"]
    assert disagreement
    assert "OFFICIAL_RULE_PAGE" in disagreement[0].detail
    assert pack.status is RulePackStatus.AMBIGUOUS
    assert not pack.is_usable



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 22-23  TELEMETRY REQUIREMENTS AND EVALUATION SUPPORT
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_68_every_rule_type_declares_a_contract_and_a_support_status():
    for rule_type in RuleType:
        assert rule_type in RULE_TYPE_CONTRACTS
        assert rule_type in RULE_TYPE_SUPPORT
        status, requirements = RULE_TYPE_SUPPORT[rule_type]
        assert isinstance(status, EvaluationSupport)
        assert isinstance(requirements, tuple)
    assert len(support_matrix()) == len(RuleType)


def test_69_rule_support_status_is_separate_from_pack_validity():
    """A perfectly valid news rule is still REQUIRES_EXTERNAL_SOURCE."""
    assert support_for(RuleType.NEWS_TRADING_RESTRICTION) is (
        EvaluationSupport.REQUIRES_EXTERNAL_SOURCE
    )
    pack = simple_pack([news_rule()])
    # The pack itself is VALID...
    assert pack.status is RulePackStatus.VALID
    # ...while the evaluation is honestly declared unsupported.
    assert pack.evaluation_support()[RuleType.NEWS_TRADING_RESTRICTION] is (
        EvaluationSupport.REQUIRES_EXTERNAL_SOURCE
    )


def test_70_requires_external_source_is_surfaced():
    pack = simple_pack([news_rule()])
    assert TelemetryRequirement.ECONOMIC_CALENDAR in pack.external_source_requirements()
    assert support_for(RuleType.IP_DEVICE_LOCATION_RESTRICTION) is (
        EvaluationSupport.REQUIRES_EXTERNAL_SOURCE
    )


def test_71_requires_new_state_is_declared_for_state_dependent_rules():
    for rule_type in (
        RuleType.DAILY_LOSS_LIMIT,
        RuleType.MAX_DRAWDOWN,
        RuleType.TRAILING_DRAWDOWN,
        RuleType.PROFIT_TARGET,
        RuleType.MIN_TRADING_DAYS,
        RuleType.CONSISTENCY_RULE,
    ):
        assert support_for(rule_type) is EvaluationSupport.REQUIRES_NEW_STATE
    pack = simple_pack([daily_loss_rule()])
    assert TelemetryRequirement.START_OF_DAY_BALANCE in pack.unsatisfied_requirements()
    assert block2_source_for(TelemetryRequirement.START_OF_DAY_BALANCE) is None


def test_72_block2_supported_rules_are_identified_exactly():
    """Max open risk and correlation risk are supported by accepted Block 2."""
    for rule_type in (
        RuleType.MAX_OPEN_RISK,
        RuleType.MAX_CORRELATED_RISK,
        RuleType.MAX_DIRECTIONAL_RISK,
        RuleType.MAX_OPEN_POSITIONS,
        RuleType.MAX_POSITION_SIZE,
    ):
        assert support_for(rule_type) is EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY
    pack = simple_pack([open_risk_rule()])
    assert pack.supportable_now() == (RuleType.MAX_OPEN_RISK,)
    assert "2B" in pack.block2_dependencies()


def test_73_min_trading_days_requires_durable_day_count_state():
    assert support_for(RuleType.MIN_TRADING_DAYS) is EvaluationSupport.REQUIRES_NEW_STATE
    assert block2_source_for(TelemetryRequirement.TRADING_DAY_HISTORY) is None
    pack = simple_pack([trading_day_rule()])
    assert TelemetryRequirement.TRADING_DAY_HISTORY in pack.unsatisfied_requirements()


def test_74_unsatisfied_requirements_exclude_block2_satisfied_evidence():
    requirements = (
        TelemetryRequirement.ACCOUNT_EQUITY,      # 2A
        TelemetryRequirement.OPEN_RISK_TOTAL,    # 2B
        TelemetryRequirement.SYMBOL_CORRELATION_CLUSTERS,  # 2C
        TelemetryRequirement.HIGH_WATER_EQUITY,  # new state
    )
    assert unsatisfied_requirements(requirements) == (TelemetryRequirement.HIGH_WATER_EQUITY,)
    assert required_block2_telemetry(requirements) == ("2A", "2B", "2C")


def test_75_not_implemented_and_unsupported_statuses_exist_and_are_distinct():
    assert support_for(RuleType.EA_AUTOMATION_PERMISSION) is EvaluationSupport.NOT_IMPLEMENTED
    assert support_for(RuleType.UNKNOWN_EXTENSION) is EvaluationSupport.UNSUPPORTED


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 39  FORWARD COMPATIBILITY â€” UNKNOWN RULES ARE PRESERVED
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_76_unknown_rule_type_is_preserved_never_dropped():
    unknown = UnknownRule(
        rule_id="r.future",
        rule_type=RuleType.UNKNOWN_EXTENSION,
        enabled=True,
        severity=RuleSeverity.BREACH,
        source=official_source(),
        effective_from=PACK_EFFECTIVE_FROM,
        evaluation_time_basis=EvaluationTimeBasis.NOT_TIME_SENSITIVE,
        raw_rule_type="QUANTUM_LEVERAGE_VAULT_RULE",
        raw_payload={"leverage": 500, "tier": "gold"},
    )
    assert unknown.raw_rule_type == "QUANTUM_LEVERAGE_VAULT_RULE"
    assert unknown.status is RuleStatus.UNSUPPORTED
    pack = simple_pack([unknown])
    # Preserved in the pack...
    assert len(pack.rules_of_type(RuleType.UNKNOWN_EXTENSION)) == 1
    # ...but the pack can never be used.
    assert pack.status is RulePackStatus.UNSUPPORTED
    assert not pack.is_usable
    with pytest.raises(RulePackConflictError):
        pack.assert_usable()
    # Round-trip keeps the unknown rule verbatim.
    restored = rule_pack_from_json(rule_pack_to_json(pack))
    assert restored.rules[0].raw_rule_type == "QUANTUM_LEVERAGE_VAULT_RULE"
    assert restored.rules[0].raw_payload == {"leverage": 500, "tier": "gold"}


def test_77_unknown_rule_requires_a_raw_type():
    with pytest.raises(RulePackValidationError) as exc:
        UnknownRule(
            rule_id="r.future",
            rule_type=RuleType.UNKNOWN_EXTENSION,
            enabled=True,
            severity=RuleSeverity.BREACH,
            source=official_source(),
            effective_from=PACK_EFFECTIVE_FROM,
            evaluation_time_basis=EvaluationTimeBasis.NOT_TIME_SENSITIVE,
            raw_rule_type="",
        )
    assert "UNKNOWN_RULE_REQUIRES_RAW_TYPE" in str(exc.value)



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 37-38  SERIALISATION AND SCHEMA
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_78_serialisation_is_deterministic_and_schema_versioned():
    pack = build_pack_a()
    text = rule_pack_to_json(pack)
    assert rule_pack_to_json(pack) == text
    payload = json.loads(text)
    assert payload["schema"] == SCHEMA_VERSION
    assert payload["rule_pack_id"] == pack.rule_pack_id
    assert payload["status"] == "VALID"


def test_79_json_round_trip_preserves_identity_and_semantics():
    for pack in build_packs():
        restored = rule_pack_from_json(rule_pack_to_json(pack))
        assert restored.rule_pack_id == pack.rule_pack_id
        assert restored.content_hash == pack.content_hash
        assert restored.identity == pack.identity
        assert len(restored.rules) == len(pack.rules)
        assert restored.status is pack.status
        assert restored.block2_dependencies() == pack.block2_dependencies()
        # A second round trip is a fixed point.
        assert rule_pack_to_json(restored) == rule_pack_to_json(pack)


def test_80_field_ordering_never_alters_identity():
    pack = build_pack_b()
    payload = rule_pack_to_dict(pack)
    reordered = json.loads(json.dumps(payload, sort_keys=False))
    assert rule_pack_from_dict(reordered).rule_pack_id == pack.rule_pack_id
    # The canonical encoding is itself order-independent.
    assert canonical_json(payload) == rule_pack_to_json(pack)


def test_81_round_trip_detects_tampering_and_bad_schema():
    pack = build_pack_a()
    payload = rule_pack_to_dict(pack)
    payload["rule_pack_id"] = "prp1:deadbeef"
    with pytest.raises(RulePackValidationError) as exc:
        rule_pack_from_dict(payload)
    assert "RULE_PACK_ID_MISMATCH" in str(exc.value)
    payload = rule_pack_to_dict(pack)
    payload["schema"] = "prop_rule_pack_v99"
    with pytest.raises(RulePackValidationError) as exc:
        rule_pack_from_dict(payload)
    assert "UNSUPPORTED_RULE_PACK_SCHEMA" in str(exc.value)
    with pytest.raises(RulePackValidationError) as exc:
        rule_pack_from_json("{not json")
    assert "RULE_PACK_JSON_INVALID" in str(exc.value)


def test_82_unknown_rule_contract_is_rejected_not_degraded():
    payload = rule_pack_to_dict(build_pack_a())
    payload["rules"][0]["contract"] = "TotallyUnknownRule"
    with pytest.raises(RulePackValidationError) as exc:
        rule_pack_from_dict(payload)
    assert "UNKNOWN_RULE_CONTRACT" in str(exc.value)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 5, 29  IMMUTABILITY
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_83_rule_packs_and_rules_are_immutable_dataclasses():
    pack = build_pack_a()
    with pytest.raises(FrozenInstanceError):
        pack.notes = "mutated"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        pack.identity.provider = "OTHER"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        pack.rules[0].limit = pct(LimitBasis.PERCENT_INITIAL_BALANCE, 99.0)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        pack.rules[0].enabled = False  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        pack.identity.account_size = 500_000  # type: ignore[misc]
    # Rules and limits are frozen dataclasses too.
    assert all(
        rule.__dataclass_params__.frozen for rule in pack.rules
    )
    assert Limit.__dataclass_params__.frozen is True
    assert RuleSource.__dataclass_params__.frozen is True


def test_84_changing_a_firm_rule_produces_a_new_pack_and_keeps_the_old():
    """Historical evaluation must stay bound to the pack that was in force."""
    old = build_pack_a()
    new = build_pack_a(pack_a_identity(version="2.0.0"))
    assert old.rule_pack_id != new.rule_pack_id
    assert old.identity.rule_pack_version == "1.0.0"
    assert new.identity.rule_pack_version == "2.0.0"
    # The old pack is untouched and still authoritative for its own window.
    assert old.rules[0].limit.value == 5.0
    assert old.content_hash != new.content_hash



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 30-31  STORE, HISTORICAL LOOKUP, AMBIGUITY
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_85_store_registers_and_retrieves_by_exact_id():
    store = RulePackStore()
    pack = build_pack_a()
    store.register_rule_pack(pack)
    assert store.get_rule_pack(pack.rule_pack_id).rule_pack_id == pack.rule_pack_id
    assert pack.rule_pack_id in store
    assert len(store) == 1
    with pytest.raises(RulePackNotFound):
        store.get_rule_pack("prp1:nonexistent")


def test_86_historical_version_lookup_returns_the_pack_in_force():
    store = RulePackStore()
    v1 = build_pack_a(
        pack_a_identity(
            version="1.0.0",
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
            effective_to=datetime(2026, 7, 1, tzinfo=UTC),
        )
    )
    v2 = build_pack_a(
        pack_a_identity(
            version="2.0.0",
            effective_from=datetime(2026, 7, 1, tzinfo=UTC),
            effective_to=datetime(2027, 1, 1, tzinfo=UTC),
        )
    )
    store.register_rule_pack(v1)
    store.register_rule_pack(v2)
    in_2026_q1 = store.find_rule_pack(
        "SYNTHETIC_FIRM_A", "SYNTHETIC_CHALLENGE", RulePhase.EVALUATION_PHASE_1,
        100_000, datetime(2026, 3, 1, tzinfo=UTC),
    )
    in_2026_q3 = store.find_rule_pack(
        "SYNTHETIC_FIRM_A", "SYNTHETIC_CHALLENGE", RulePhase.EVALUATION_PHASE_1,
        100_000, datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert in_2026_q1.rule_pack_id == v1.rule_pack_id
    assert in_2026_q3.rule_pack_id == v2.rule_pack_id
    # 2026 rules are never retroactively applied to 2025 data.
    with pytest.raises(RulePackNotFound):
        store.find_rule_pack(
            "SYNTHETIC_FIRM_A", "SYNTHETIC_CHALLENGE", RulePhase.EVALUATION_PHASE_1,
            100_000, NOT_EFFECTIVE,
        )
    assert len(store.list_versions(
        "SYNTHETIC_FIRM_A", "SYNTHETIC_CHALLENGE", RulePhase.EVALUATION_PHASE_1, 100_000
    )) == 2


def test_87_ambiguous_lookup_raises_instead_of_nearest_match():
    store = RulePackStore()
    # Two different packs whose effective windows overlap on 2026-06-15.
    store.register_rule_pack(build_pack_a(pack_a_identity(version="1.0.0")))
    store.register_rule_pack(build_pack_a(
        pack_a_identity(version="1.5.0", effective_from=datetime(2026, 1, 1, tzinfo=UTC))
    ))
    with pytest.raises(AmbiguousRulePackError) as exc:
        store.find_rule_pack(
            "SYNTHETIC_FIRM_A", "SYNTHETIC_CHALLENGE", RulePhase.EVALUATION_PHASE_1,
            100_000, datetime(2026, 6, 15, tzinfo=UTC),
        )
    assert "MULTIPLE_RULE_PACKS_MATCH" in str(exc.value)


def test_88_unknown_identity_raises_rather_than_defaulting():
    store = RulePackStore()
    store.register_rule_pack(build_pack_a())
    with pytest.raises(RulePackNotFound):
        store.find_rule_pack(
            "UNKNOWN_FIRM", "UNKNOWN_PROGRAM", RulePhase.FUNDED, 100_000,
            datetime(2026, 3, 1, tzinfo=UTC),
        )
    with pytest.raises(RulePackNotFound):
        store.find_rule_pack(
            "SYNTHETIC_FIRM_A", "SYNTHETIC_CHALLENGE", RulePhase.FUNDED, 100_000,
            datetime(2026, 3, 1, tzinfo=UTC),
        )
    with pytest.raises(RulePackValidationError) as exc:
        store.find_rule_pack(
            "SYNTHETIC_FIRM_A", "SYNTHETIC_CHALLENGE", RulePhase.EVALUATION_PHASE_1,
            100_000, datetime(2026, 3, 1),
        )
    assert "AS_OF_NAIVE_DATETIME" in str(exc.value)


def test_89_duplicate_registration_is_idempotent_or_hard_fails():
    store = RulePackStore()
    pack = build_pack_a()
    store.register_rule_pack(pack)
    store.register_rule_pack(pack)  # idempotent
    assert len(store) == 1
    with pytest.raises(RulePackValidationError):
        store.register_rule_pack("not a pack")  # type: ignore[arg-type]
    with pytest.raises(RulePackValidationError):
        store.register_rule_pack(RulePack(identity=pack.identity, rules=()))


def test_90_phase_and_tier_lookups_are_separate_never_merged():
    store = RulePackStore()
    phase1 = build_pack_a(pack_a_identity(phase=RulePhase.EVALUATION_PHASE_1))
    funded = build_pack_a(pack_a_identity(phase=RulePhase.FUNDED))
    tier50 = build_pack_a(pack_a_identity(account_size=50_000))
    for pack in (phase1, funded, tier50):
        store.register_rule_pack(pack)
    as_of = datetime(2026, 3, 1, tzinfo=UTC)
    assert store.find_rule_pack(
        "SYNTHETIC_FIRM_A", "SYNTHETIC_CHALLENGE", RulePhase.FUNDED, 100_000, as_of
    ).rule_pack_id == funded.rule_pack_id
    assert store.find_rule_pack(
        "SYNTHETIC_FIRM_A", "SYNTHETIC_CHALLENGE", RulePhase.EVALUATION_PHASE_1, 50_000, as_of
    ).rule_pack_id == tier50.rule_pack_id


def test_91_pack_level_validity_summary():
    pack = build_pack_b()
    assert validate_rule_pack(pack) is RulePackStatus.VALID
    assert store_status_summary(pack) == "VALID"


def store_status_summary(pack: RulePack) -> str:
    return pack.status.value



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 18-20, 32  PHASES, TIERS, CURRENCY, TIMEZONE
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_92_all_phases_are_modelled_and_never_flattened():
    phases = {p.value for p in RulePhase}
    assert {
        "EVALUATION_PHASE_1", "EVALUATION_PHASE_2", "FUNDED",
        "SIMULATED_FUNDED", "EXPRESS", "INSTANT",
    } <= phases
    identity = pack_identity(phase=RulePhase.FUNDED, platform="MT5", jurisdiction="EU")
    assert identity.phase is RulePhase.FUNDED
    assert identity.platform == "MT5"
    assert identity.jurisdiction == "EU"


def test_93_account_tiers_are_exact_and_do_not_assume_equal_absolute_limits():
    for size, label in ((25_000, "25K"), (50_000, "50K"), (100_000, "100K")):
        assert pack_identity(account_size=size).tier_label == label
    # A 5% daily loss on a 25k account is a materially different absolute money
    # amount than on a 100k account, so identity must differ.
    rules = [daily_loss_rule()]
    assert (
        derive_rule_pack_id(pack_identity(account_size=25_000), rules)
        != derive_rule_pack_id(pack_identity(account_size=100_000), rules)
    )


def test_94_currency_and_platform_are_part_of_identity():
    rules = [daily_loss_rule()]
    base = pack_identity()
    assert (
        derive_rule_pack_id(base, rules) != derive_rule_pack_id(pack_identity(currency="GBP"), rules)
    )
    assert (
        derive_rule_pack_id(base, rules)
        != derive_rule_pack_id(pack_identity(platform="cTrader"), rules)
    )
    with pytest.raises(RulePackValidationError) as exc:
        pack_identity(currency="DOLLARS")
    assert "ISO4217" in str(exc.value)


def test_95_every_time_sensitive_rule_binds_an_explicit_iana_timezone():
    for pack in build_packs():
        for rule in pack.rules:
            if rule.requires_timezone:
                assert rule.timezone_name, f"{rule.rule_id} has no timezone"
                assert "/" in rule.timezone_name or rule.timezone_name == "UTC"


def test_96_rules_effective_at_respects_effective_dates():
    """2026 rules are never retroactively applied to 2025 data."""
    later = daily_loss_rule(
        "r.dl_future",
        value=3.0,
        effective_from=datetime(2026, 7, 1, tzinfo=UTC),
        effective_to=datetime(2027, 1, 1, tzinfo=UTC),
    )
    pack = simple_pack([later])
    assert later.is_effective_at(datetime(2026, 8, 1, tzinfo=UTC))
    assert not later.is_effective_at(datetime(2026, 3, 1, tzinfo=UTC))
    assert pack.rules_effective_at(datetime(2026, 8, 1, tzinfo=UTC))
    assert not pack.rules_effective_at(NOT_EFFECTIVE)
    with pytest.raises(RulePackValidationError):
        later.is_effective_at(datetime(2026, 8, 1))


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 32  BUILT-IN TEST PACKS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_97_builtin_packs_are_valid_and_bind_every_limit_explicitly():
    for pack in build_packs():
        assert pack.status is RulePackStatus.VALID
        assert pack.is_usable
        for rule in pack.rules:
            limit = getattr(rule, "limit", None)
            if isinstance(limit, Limit):
                assert limit.basis is not None
                if limit.is_monetary:
                    assert limit.currency_semantics is not None


def test_98_pack_a_matches_its_specified_semantics():
    pack = build_pack_a()
    loss = pack.rules_of_type(RuleType.DAILY_LOSS_LIMIT)[0]
    assert loss.limit.basis is LimitBasis.PERCENT_START_OF_DAY_BALANCE
    assert loss.limit.value == 5.0
    drawdown = pack.rules_of_type(RuleType.STATIC_DRAWDOWN)[0]
    assert drawdown.limit.basis is LimitBasis.PERCENT_INITIAL_BALANCE
    assert drawdown.limit.value == 10.0
    target = pack.rules_of_type(RuleType.PROFIT_TARGET)[0]
    assert target.limit.value == 8.0
    days = pack.rules_of_type(RuleType.MIN_TRADING_DAYS)[0]
    assert days.limit.value == 5.0
    weekend = pack.rules_of_type(RuleType.WEEKEND_HOLD_RESTRICTION)[0]
    assert weekend.holding_permitted is True
    news = pack.rules_of_type(RuleType.NEWS_TRADING_RESTRICTION)[0]
    assert news.blackouts_high_impact is False


def test_99_pack_b_matches_its_specified_semantics():
    pack = build_pack_b()
    loss = pack.rules_of_type(RuleType.DAILY_LOSS_LIMIT)[0]
    assert loss.limit.value == 4.0
    assert loss.pnl_components.include_floating_pnl is True
    trailing = pack.rules_of_type(RuleType.TRAILING_DRAWDOWN)[0]
    assert trailing.limit.basis is LimitBasis.PERCENT_HIGH_WATER_EQUITY
    assert trailing.limit.value == 8.0
    target = pack.rules_of_type(RuleType.PROFIT_TARGET)[0]
    assert target.limit.value == 10.0
    weekend = pack.rules_of_type(RuleType.WEEKEND_HOLD_RESTRICTION)[0]
    assert weekend.holding_permitted is False
    news = pack.rules_of_type(RuleType.NEWS_TRADING_RESTRICTION)[0]
    assert news.pre_event_blackout_minutes == 2
    assert news.post_event_blackout_minutes == 2
    consistency = pack.rules_of_type(RuleType.CONSISTENCY_RULE)[0]
    assert consistency.numerator_definition
    assert consistency.denominator_definition


def test_100_builtin_packs_encode_no_real_prop_firm():
    """3A builds the generic model; no real firm is encoded from memory."""
    for pack in build_packs():
        assert pack.identity.provider.startswith("SYNTHETIC_")
        for rule in pack.rules:
            assert rule.source.source_type is SourceType.MANUAL_ASSUMPTION
            assert not rule.source.is_official
            assert "SYNTHETIC" in rule.source.notes



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 35, 54-56  NO RUNTIME ENFORCEMENT â€” MODELING IS SIDE-EFFECT FREE
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

_RULE_MODEL_MODULES = (
    "core.risk.prop_rule_enums",
    "core.risk.prop_rule_telemetry",
    "core.risk.prop_rule_values",
    "core.risk.prop_rule_contracts",
    "core.risk.prop_rule_pack",
    "core.risk.prop_rule_examples",
)

#: Nothing in the canonical rule model may reach into the trading runtime.
FORBIDDEN_DEPENDENCIES = (
    "MetaTrader5",
    "risk.guards",
    "risk.manager",
    "risk.daily_loss_guard",
    "risk.drawdown_guard",
    "core.prop_firm_rules",
    "core.execution",
    "core.mt5_timeout",
    "core.config",
    "core.daily_reset",
    "core.equity_curve_tracker",
    "core.challenge_progress_tracker",
    "main",
    "strategy",
    "risk.decision",
    "core.trade_management",
    "core.canonical_delivery",
    "core.production_data_contract",
)


def _module_source(name: str) -> str:
    import pathlib

    return pathlib.Path(sys.modules[name].__file__).read_text(encoding="utf-8")


def _module_ast(name: str):
    import ast
    import pathlib

    return ast.parse(pathlib.Path(sys.modules[name].__file__).read_text(encoding="utf-8"))


def _imported_modules(name: str) -> set[str]:
    """Every module this file imports, from its AST (not from the process)."""
    import ast

    found: set[str] = set()
    for node in ast.walk(_module_ast(name)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _called_and_referenced_names(name: str) -> set[str]:
    """Identifiers actually used in CODE, excluding comments and docstrings."""
    import ast

    names: set[str] = set()
    for node in ast.walk(_module_ast(name)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name.split(".")[-1])
    return names


def test_101_rule_model_imports_no_trading_or_guard_module():
    """Block 3A must not import any broker, guard, execution or config module."""
    for name in _RULE_MODEL_MODULES:
        importlib.import_module(name)
    for name in _RULE_MODEL_MODULES:
        imported = _imported_modules(name)
        for forbidden in FORBIDDEN_DEPENDENCIES:
            for module in imported:
                assert not module.startswith(forbidden), (
                    f"{name} imports forbidden module {module}"
                )


def test_101b_importing_the_rule_model_loads_no_broker_or_trading_module():
    """A clean interpreter that imports 3A must load no trading runtime at all."""
    import subprocess

    script = (
        "import sys\n"
        f"for m in {_RULE_MODEL_MODULES!r}:\n"
        "    __import__(m)\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in "
        "{'MetaTrader5', 'risk', 'strategy', 'execution', 'data', 'main'}]\n"
        "print(','.join(sorted(bad)))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=180
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "", (
        f"importing the rule model loaded trading modules: {completed.stdout}"
    )


def test_102_rule_model_code_contains_no_trading_call_or_mutation():
    """No order placement, position mutation, kill switch or enforcement call.

    Checked against the AST so prose in docstrings cannot mask or fake a result.
    """
    forbidden = (
        "order_send",
        "positions_get",
        "close_position",
        "modify_position",
        "kill_switch",
        "pause_bot",
        "block_trade",
        "reject_trade",
        "check_prop_firm_rules",
        "evaluate_rule",
        "enforce_rule",
        "OrderIntent",
        "execute_trade",
        "close_trade",
    )
    for name in _RULE_MODEL_MODULES:
        used = _called_and_referenced_names(name)
        for token in forbidden:
            assert token not in used, f"{name} references forbidden code symbol {token!r}"



def test_103_compiling_and_querying_packs_invokes_no_guard_and_writes_no_file(tmp_path):
    """Compilation, lookup, serialisation and validation have no side effect."""
    import builtins

    opened: list[str] = []
    real_open = builtins.open

    def tracking_open(file, *args, **kwargs):  # type: ignore[no-untyped-def]
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    store = RulePackStore()
    packs = build_packs()
    for pack in packs:
        store.register_rule_pack(pack)
        text = rule_pack_to_json(pack)
        restored = rule_pack_from_json(text)
        assert restored.rule_pack_id == pack.rule_pack_id
        _ = pack.status, pack.required_telemetry(), pack.evaluation_support()
        _ = pack.unsatisfied_requirements(), pack.external_source_requirements()
    store.list_versions(
        "SYNTHETIC_FIRM_A", "SYNTHETIC_CHALLENGE", RulePhase.EVALUATION_PHASE_1, 100_000
    )

    builtins.open = tracking_open
    try:
        for pack in packs:
            pack.assert_usable()
            rule_pack_to_json(pack)
            rule_pack_from_json(rule_pack_to_json(pack))
            compile_rule_pack(pack.identity, pack.rules)
    finally:
        builtins.open = real_open

    # Nothing under the working tree was written.
    assert not [p for p in opened if "logs" in p or "runtime" in p or ".json" in p]


def test_104_rule_model_defines_no_enforcement_entry_point():
    """There is deliberately no 'apply rules' or 'check rules' function in 3A."""
    import core.risk.prop_rule_pack as pack_module

    callable_names = [
        name
        for name in dir(pack_module)
        if not name.startswith("_")
        and callable(getattr(pack_module, name))
        and getattr(getattr(pack_module, name), "__module__", "") == pack_module.__name__
    ]
    for banned in ("evaluate", "enforce", "apply_rules", "check_rules", "block", "execute"):
        assert not any(banned in n.lower() for n in callable_names), (
            f"rule model exposes an enforcement-shaped API: {banned}"
        )
    # What it does expose is identity, compilation, validation and storage.
    for expected in (
        "RulePack",
        "RulePackIdentity",
        "RulePackStore",
        "compile_rule_pack",
        "validate_rule_pack",
        "derive_rule_pack_id",
    ):
        assert expected in dir(pack_module)


def test_105_rule_model_does_not_read_the_clock_or_environment():
    """Determinism: no wall clock, no environment variable, no randomness."""
    import ast

    for name in _RULE_MODEL_MODULES:
        for node in ast.walk(_module_ast(name)):
            if isinstance(node, ast.Call):
                func = node.func
                # datetime.now(...) / time.time(...) would break determinism.
                if isinstance(func, ast.Attribute) and func.attr in {"now", "utcnow", "time"}:
                    owner = getattr(func.value, "id", None)
                    assert owner not in {"datetime", "time"}, (
                        f"{name} reads the wall clock via {owner}.{func.attr}"
                    )
                if isinstance(func, ast.Name) and func.id in {
                    "uuid4", "uuid1", "randint", "random", "shuffle", "choice",
                }:
                    raise AssertionError(f"{name} uses non-determinism: {func.id}")
            if isinstance(node, ast.Attribute) and node.attr == "environ":
                raise AssertionError(f"{name} reads the environment")
    # Two independently built packs are byte-identical, so nothing drifted.
    assert rule_pack_to_json(build_pack_a()) == rule_pack_to_json(build_pack_a())
    assert build_pack_a().rule_pack_id == build_pack_a().rule_pack_id


def test_106_no_production_v1_telemetry_dataset_was_created():
    """Rule packs are configuration authority, not live telemetry."""
    from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY

    for dataset in PRODUCTION_SCHEMA_REGISTRY:
        assert "rule" not in dataset



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# 41  SUPPORT MATRIX (this matrix defines Block 3B/3C scope)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_107_support_matrix_classifies_every_rule_type():
    """No rule type may be unclassified: each is supported, state-bound,
    external-source-bound, not implemented, or explicitly unsupported."""
    matrix = support_matrix()
    assert set(matrix) == set(RuleType)
    by_status: dict[EvaluationSupport, list[str]] = {}
    for rule_type, status in matrix.items():
        by_status.setdefault(status, []).append(rule_type.value)

    # Supported today by accepted Block 2 telemetry.
    assert EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY in by_status
    assert set(by_status[EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY]) >= {
        "MAX_OPEN_RISK", "MAX_CORRELATED_RISK", "MAX_DIRECTIONAL_RISK",
        "MAX_OPEN_POSITIONS", "MAX_POSITION_SIZE",
    }
    # Needs new durable state (Block 3B).
    assert set(by_status[EvaluationSupport.REQUIRES_NEW_STATE]) >= {
        "DAILY_LOSS_LIMIT", "MAX_DRAWDOWN", "TRAILING_DRAWDOWN", "STATIC_DRAWDOWN",
        "PROFIT_TARGET", "MIN_TRADING_DAYS", "MAX_TRADING_DAYS", "CONSISTENCY_RULE",
        "WEEKEND_HOLD_RESTRICTION", "OVERNIGHT_HOLD_RESTRICTION", "PAYOUT_ELIGIBILITY",
    }
    # Needs an external source entirely.
    assert set(by_status[EvaluationSupport.REQUIRES_EXTERNAL_SOURCE]) >= {
        "NEWS_TRADING_RESTRICTION", "IP_DEVICE_LOCATION_RESTRICTION",
    }
    # Not implemented yet / unknown.
    assert "EA_AUTOMATION_PERMISSION" in by_status[EvaluationSupport.NOT_IMPLEMENTED]
    assert "UNKNOWN_EXTENSION" in by_status[EvaluationSupport.UNSUPPORTED]


def test_108_support_matrix_is_stable_and_read_only():
    matrix = support_matrix()
    matrix[RuleType.DAILY_LOSS_LIMIT] = EvaluationSupport.UNSUPPORTED
    # The exported matrix is a fresh dict each call; the contract is unchanged.
    assert support_for(RuleType.DAILY_LOSS_LIMIT) is EvaluationSupport.REQUIRES_NEW_STATE
    assert support_matrix()[RuleType.DAILY_LOSS_LIMIT] is (
        EvaluationSupport.REQUIRES_NEW_STATE
    )


def test_109_builtin_packs_report_their_exact_block3b_gap():
    """The unsatisfied-evidence list is the concrete Block 3B build list."""
    pack_a = build_pack_a()
    assert TelemetryRequirement.START_OF_DAY_BALANCE in pack_a.unsatisfied_requirements()
    assert TelemetryRequirement.INITIAL_BALANCE in pack_a.unsatisfied_requirements()
    assert TelemetryRequirement.TRADING_DAY_HISTORY in pack_a.unsatisfied_requirements()
    pack_b = build_pack_b()
    assert TelemetryRequirement.HIGH_WATER_EQUITY in pack_b.unsatisfied_requirements()
    assert TelemetryRequirement.ECONOMIC_CALENDAR in pack_b.external_source_requirements()
    # Both still consume accepted Block 2 evidence today.
    for pack in (pack_a, pack_b):
        assert set(pack.block2_dependencies()) <= {"2A", "2B", "2C", "2D"}
