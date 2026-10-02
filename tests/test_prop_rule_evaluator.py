"""Deterministic historical prop rule EVALUATOR (Block 3B).

Covers the evaluation contract end to end: the typed context, the five-valued
immutable result, daily loss (realised / floating / fees), static and trailing
drawdown, profit target, trading days and inactivity, position and open-risk
limits from exact Block 2 telemetry, correlation and directional risk, the
external-source foundations (news / weekend hold), consistency, the support
matrix, deterministic evaluation identity, historical replay across a pack
version boundary, and the no-side-effect guarantees.

The evaluator is READ-ONLY with respect to trading runtime: these tests also
assert it calls no broker and no runtime guard.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from core.risk.prop_rule_contracts import (
    DailyLossRule,
    DrawdownRule,
    HoldRestrictionRule,
    InactivityRule,
    NewsTradingRestrictionRule,
    OpenRiskLimitRule,
    PnLComponents,
    PositionLimitRule,
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
    EvaluationSupport,
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
from core.risk.prop_rule_examples import build_pack_a, build_pack_b, pack_a_identity
from core.risk.prop_rule_evaluator import (
    DAILY_PNL_CONTRACTS,
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    ExplanationCode,
    derived_daily_pnl,
    derive_evaluation_id,
    evaluate_pack,
    evaluate_rule,
    replay_account_evaluations,
    resolve_limit,
    rule_type_evaluation_support,
    select_daily_pnl,
    support_matrix_3b,
)
from core.risk.prop_rule_state import (
    AccountEvaluationState,
    AccountKey,
    CloseEventKind,
    ClosedTradeEvent,
    HighWaterState,
    InitialAccountAnchor,
    PropRuleStateStore,
    RuleDayDefinition,
    StateStatus,
    build_account_evaluation_state,
    rules_effective_at,
)
from core.risk.prop_rule_values import Limit
from core.risk.prop_rule_pack import RulePackStore

UTC = timezone.utc
RULE_DAY = date(2026, 3, 10)
EVAL_AT = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)   # 10:00 New York
NY = RuleDayDefinition("America/New_York", time(0, 0))

ACCOUNT = AccountKey("ACC_A", "MT5", "DemoBroker-Server", 111111)
ACCOUNT_B = AccountKey("ACC_B", "MT5", "DemoBroker-Server", 222222)

SOURCE = RuleSource(
    source_type=SourceType.OFFICIAL_RULE_PAGE,
    source_reference="https://example.invalid/rules",
    retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
)
EFFECTIVE_FROM = datetime(2026, 1, 1, tzinfo=UTC)



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# BUILDERS Ã¢â‚¬â€ synthetic Block 2 telemetry evidence objects
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


@dataclass(frozen=True)
class FakeSnapshot:
    """Stand-in for a Block 2A AccountSnapshot."""

    account_id: str = "ACC_A"
    snapshot_id: str = "asnap_1"
    balance: float | None = 100_000.0
    equity: float | None = 100_000.0
    floating_pnl: float | None = 0.0
    currency: str = "USD"


@dataclass(frozen=True)
class FakeOpenRisk:
    """Stand-in for a Block 2B AccountOpenRiskSnapshot."""

    account_id: str = "ACC_A"
    open_risk_id: str = "orisk_1"
    total_open_risk: float | None = 500.0
    risk_complete: bool = True
    open_position_count: int | None = 2
    position_tickets: tuple[int, ...] = (11, 12)
    position_risks: tuple[float, ...] = (300.0, 200.0)


@dataclass(frozen=True)
class FakePositionSet:
    position_set_complete: bool = True
    positions: tuple[object, ...] = ()


@dataclass(frozen=True)
class FakePosition:
    volume: float = 0.5


@dataclass(frozen=True)
class FakePortfolio:
    """Stand-in for a Block 2C PortfolioExposure."""

    account_id: str = "ACC_A"
    portfolio_exposure_id: str = "pexp_1"
    total_open_risk: float | None = 500.0
    risk_complete: bool = True
    largest_symbol_risk: float | None = 400.0
    largest_symbol: str | None = "EURUSD"
    largest_direction_risk: float | None = 350.0
    largest_direction: str = "LONG"
    max_cluster_risk: float | None = 450.0
    correlation_complete: bool = True


def initial_anchor(account: AccountKey = ACCOUNT, balance: float = 100_000.0) -> InitialAccountAnchor:
    return InitialAccountAnchor(
        account=account, account_currency="USD", initial_balance=balance, initial_equity=balance,
        observed_at_utc=datetime(2026, 3, 2, 14, tzinfo=UTC),
        source_snapshot_id="asnap_initial", source_provenance="BLOCK_2A_ACCOUNT_SNAPSHOT",
    )


def daily_anchor(account: AccountKey = ACCOUNT, balance: float = 100_000.0):
    from core.risk.prop_rule_state import DailyAccountAnchor

    return DailyAccountAnchor(
        account=account, account_currency="USD", rule_day=RULE_DAY,
        timezone_name=NY.timezone_name, rule_day_definition=NY.key(),
        start_of_day_balance=balance, start_of_day_equity=balance,
        anchor_at_utc=NY.day_start_utc(RULE_DAY), source_snapshot_id="asnap_sod",
        source_provenance="BLOCK_2A_ACCOUNT_SNAPSHOT", start_of_day_floating_pnl=0.0,
    )



def close_event(
    *,
    account: AccountKey = ACCOUNT,
    source_trade_id: str = "deal-1",
    ticket: int | None = 555,
    closed_at: datetime | None = None,
    gross: float = -250.0,
    commission: float | None = -3.0,
    swap: float | None = -1.0,
    fees: float | None = 0.0,
    volume: float | None = 0.1,
) -> ClosedTradeEvent:
    return ClosedTradeEvent(
        account=account, account_currency="USD", source="MT5_HISTORY_DEAL",
        source_trade_id=source_trade_id, position_ticket=ticket, symbol="EURUSD",
        closed_at_utc=closed_at or datetime(2026, 3, 10, 14, 0, tzinfo=UTC),
        gross_realised_pnl=gross, volume=volume, close_kind=CloseEventKind.FULL_CLOSE,
        commission=commission, swap=swap, fees=fees, duration_seconds=900.0,
    )


def high_water(balance: float = 100_000.0, equity: float = 100_000.0) -> HighWaterState:
    return HighWaterState.empty(ACCOUNT, "USD").advance(
        balance=balance, equity=equity,
        observed_at_utc=datetime(2026, 3, 2, 14, tzinfo=UTC), source_snapshot_id="asnap_hw",
    )


def make_state(
    *,
    account: AccountKey = ACCOUNT,
    balance: float | None = 100_000.0,
    equity: float | None = 100_000.0,
    floating: float | None = 0.0,
    sod_balance: float | None = 100_000.0,
    initial_balance: float | None = 100_000.0,
    events: tuple[ClosedTradeEvent, ...] = (),
    with_anchor: bool = True,
    with_daily_anchor: bool = True,
    with_high_water: bool = True,
    with_open_risk: bool = True,
    with_portfolio: bool = True,
    position_volumes: tuple[float, ...] = (0.5, 0.3),
    risk_complete: bool = True,
    total_open_risk: float | None = 500.0,
    correlation_complete: bool = True,
    stale: bool = False,
) -> AccountEvaluationState:
    """Assemble an evaluation state from explicit, injected evidence."""
    store = PropRuleStateStore()
    anchor = None
    if with_anchor and initial_balance is not None:
        anchor = store.record_initial_anchor(initial_anchor(account, initial_balance))
    anchor_day = None
    if with_daily_anchor and sod_balance is not None:
        anchor_day = store.record_daily_anchor(daily_anchor(account, sod_balance))
    for event in events:
        store.record_close_event(event)
    hw = high_water(initial_balance or 100_000.0, initial_balance or 100_000.0) if with_high_water else None
    return build_account_evaluation_state(
        account=account, account_currency="USD", observed_at_utc=EVAL_AT, definition=NY,
        initial_anchor=anchor, daily_anchor=anchor_day,
        account_snapshot=FakeSnapshot(account_id=account.account_id, balance=balance, equity=equity,
                                      floating_pnl=floating) if balance is not None or equity is not None else None,
        open_risk=FakeOpenRisk(account_id=account.account_id, total_open_risk=total_open_risk,
                               risk_complete=risk_complete) if with_open_risk else None,
        position_set=FakePositionSet(positions=tuple(FakePosition(v) for v in position_volumes)),
        portfolio=FakePortfolio(account_id=account.account_id, correlation_complete=correlation_complete)
        if with_portfolio else None,
        close_events=store.close_events_for(account), high_water=hw, stale=stale,
    )


def pct(basis: LimitBasis, value: float, **kwargs) -> Limit:
    kwargs.setdefault("currency_semantics", CurrencySemantics.ACCOUNT_CURRENCY)
    return Limit(basis=basis, value=value, **kwargs)


def daily_loss_rule(
    value: float = 5.0,
    basis: LimitBasis = LimitBasis.PERCENT_START_OF_DAY_BALANCE,
    components: PnLComponents | None = None,
    **kwargs,
) -> DailyLossRule:
    params = dict(
        rule_id="r.daily_loss", rule_type=RuleType.DAILY_LOSS_LIMIT, enabled=True,
        severity=RuleSeverity.TERMINATION, source=SOURCE, effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.DAILY_LOSS_LIMIT),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        breach_persists_until_reset=True, timezone_name="America/New_York",
        limit=pct(basis, value), reset_policy=DailyResetPolicy.CALENDAR_DAY, reset_time=time(0, 0),
    )
    if components is not None:
        params["pnl_components"] = components
    params.update(kwargs)
    return DailyLossRule(**params)


def context_for(rule_pack, state: AccountEvaluationState, evaluated_at: datetime = EVAL_AT, **kwargs) -> EvaluationContext:
    return EvaluationContext(
        rule_pack=rule_pack, state=state, evaluated_at_utc=evaluated_at,
        rule_day_definition=NY, **kwargs,
    )


def evaluate_one(rule_pack, rule, state, evaluated_at: datetime = EVAL_AT, **kwargs) -> EvaluationResult:
    return evaluate_rule(context_for(rule_pack, state, evaluated_at, **kwargs), rule)



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 1-2  DAILY P&L CONTRACT AND EXPLICIT COMPONENT SELECTION
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def test_daily_pnl_contract_exposes_the_four_canonical_derivations():
    assert set(DAILY_PNL_CONTRACTS) == {
        "REALISED_ONLY", "REALISED_PLUS_FLOATING",
        "REALISED_PLUS_COSTS", "REALISED_PLUS_FLOATING_PLUS_COSTS",
    }
    for components in DAILY_PNL_CONTRACTS.values():
        assert isinstance(components, PnLComponents)


def test_derived_daily_pnl_realised_only_uses_net_realised():
    state = make_state(events=(close_event(gross=100.0, commission=-3.0, swap=-1.0, fees=-0.5),))
    selection = derived_daily_pnl("REALISED_ONLY", state)
    assert selection.is_available
    assert selection.value == 95.5


def test_derived_daily_pnl_including_floating_adds_the_account_value():
    state = make_state(floating=-250.0, events=(close_event(gross=100.0),))
    assert derived_daily_pnl("REALISED_ONLY", state).value == 96.0
    # Floating comes from the ACCOUNT-level broker value, not summed positions.
    assert derived_daily_pnl("REALISED_PLUS_FLOATING", state).value == 96.0 - 250.0


def test_pnl_selection_is_unavailable_when_an_included_cost_is_unknown():
    state = make_state(events=(close_event(gross=100.0, commission=None),))
    selection = derived_daily_pnl("REALISED_ONLY", state)
    # "Unknown commission" is NOT "zero commission".
    assert selection.is_available is False
    assert selection.value is None
    assert selection.detail in {"LEDGER_COMPONENTS_INCOMPLETE", "NET_REALISED_PNL_UNKNOWN"}


def test_pnl_selection_is_unavailable_when_required_floating_is_missing():
    state = make_state(floating=None, events=(close_event(gross=100.0),))
    selection = derived_daily_pnl("REALISED_PLUS_FLOATING", state)
    assert selection.is_available is False
    assert selection.detail == "FLOATING_PNL_UNAVAILABLE"


def test_unknown_daily_pnl_contract_is_rejected_not_defaulted():
    selection = derived_daily_pnl("SOMETHING_ELSE", make_state())
    assert selection.is_available is False
    assert selection.detail.startswith("UNKNOWN_PNL_CONTRACT")


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 3  DAILY LOSS EVALUATION
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def test_daily_loss_realised_only_passes_within_the_limit():
    pack = build_pack_a()
    rule = daily_loss_rule(5.0)
    # 5% of 100,000 = 5,000 allowed; the realised loss is 254.
    state = make_state(events=(close_event(gross=-250.0),))
    result = evaluate_one(pack, rule, state)
    assert result.status is EvaluationStatus.PASS
    assert result.unit == "MONEY"
    assert result.limit_value == 5_000.0
    assert result.current_value == 254.0
    assert result.remaining_buffer == 4_746.0
    assert result.explanation_code is ExplanationCode.WITHIN_LIMIT


def test_daily_loss_breach_is_detected_exactly():
    pack = build_pack_a()
    state = make_state(events=(close_event(gross=-6_000.0),))
    result = evaluate_one(pack, daily_loss_rule(5.0), state)
    assert result.status is EvaluationStatus.BREACH
    assert result.breach is True
    assert result.current_value == 6_004.0
    assert result.remaining_buffer < 0
    assert result.explanation_code is ExplanationCode.LIMIT_EXCEEDED


def test_daily_loss_uses_the_exact_threshold_basis_the_rule_names():
    pack = build_pack_a()
    state = make_state(events=(close_event(gross=-1_000.0),))
    # 2% of start-of-day BALANCE = 2,000.
    balance_based = evaluate_one(pack, daily_loss_rule(2.0), state)
    assert balance_based.limit_value == 2_000.0
    # 2% of start-of-day EQUITY, with equity anchored at 200,000 = 4,000.
    equity_state = make_state(sod_balance=200_000.0, events=(close_event(gross=-1_000.0),))
    equity_based = evaluate_one(
        pack, daily_loss_rule(2.0, basis=LimitBasis.PERCENT_START_OF_DAY_EQUITY), equity_state
    )
    assert equity_based.limit_value == 4_000.0
    # Same loss, different basis, different limit: no substitution.
    assert balance_based.limit_value != equity_based.limit_value



def test_daily_loss_including_floating_uses_the_floating_component():
    pack = build_pack_a()
    floating_rule = daily_loss_rule(
        1.0, components=PnLComponents(
            include_closed_pnl=True, include_floating_pnl=True,
            include_commission=True, include_swap=True, include_fees=True,
        ),
    )
    # Realised is small, but floating alone breaches the 1,000 limit.
    state = make_state(floating=-2_000.0, events=(close_event(gross=-10.0),))
    result = evaluate_one(pack, floating_rule, state)
    assert result.status is EvaluationStatus.BREACH
    assert result.current_value == 2_014.0
    # The realised-only rule does NOT see floating, so it passes.
    assert evaluate_one(pack, daily_loss_rule(1.0), state).status is EvaluationStatus.PASS


def test_daily_loss_including_fees_respects_the_fee_component():
    pack = build_pack_a()
    fees_rule = daily_loss_rule(
        1.0, components=PnLComponents(
            include_closed_pnl=True, include_floating_pnl=False,
            include_commission=True, include_swap=True, include_fees=True,
        ),
    )
    with_fees = make_state(events=(close_event(gross=-990.0, commission=0.0, swap=0.0, fees=-30.0),))
    result = evaluate_one(pack, fees_rule, with_fees)
    # Gross 990 alone would be inside 1,000; the 30 fee pushes it over.
    assert result.current_value == 1_020.0
    assert result.status is EvaluationStatus.BREACH


def test_daily_loss_excluding_a_cost_adds_it_back_explicitly():
    pack = build_pack_a()
    # The rule includes closed P&L but EXCLUDES swap, so swap is added back.
    exclude_swap = daily_loss_rule(
        1.0, components=PnLComponents(
            include_closed_pnl=True, include_floating_pnl=False,
            include_commission=True, include_swap=False, include_fees=True,
        ),
    )
    state = make_state(events=(close_event(gross=-1_100.0, commission=0.0, swap=-50.0, fees=0.0),))
    result = evaluate_one(pack, exclude_swap, state)
    # Without the add-back this would read 1,150; excluding swap gives 1,100.
    assert result.current_value == 1_100.0
    assert result.status is EvaluationStatus.BREACH


def test_daily_loss_without_an_anchor_is_indeterminate_not_a_pass():
    """No authoritative snapshot at reset means no invented anchor."""
    pack = build_pack_a()
    state = make_state(with_daily_anchor=False, events=(close_event(gross=-9_000.0),))
    result = evaluate_one(pack, daily_loss_rule(5.0), state)
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.START_OF_DAY_ANCHOR_UNAVAILABLE
    assert result.breach is False


def test_daily_loss_does_not_silently_use_yesterdays_anchor():
    """A different rule day's anchor is never borrowed for today's rule day."""
    pack = build_pack_a()
    state = make_state(sod_balance=None, events=(close_event(gross=-250.0),))
    result = evaluate_one(pack, daily_loss_rule(5.0), state)
    assert result.status is EvaluationStatus.INDETERMINATE
    assert state.start_of_day_balance is None


def test_daily_loss_with_an_unknown_cost_is_indeterminate():
    pack = build_pack_a()
    state = make_state(events=(close_event(gross=-250.0, swap=None),))
    result = evaluate_one(pack, daily_loss_rule(5.0), state)
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.LEDGER_INCOMPLETE


def test_daily_loss_with_a_no_reset_policy_is_not_applicable():
    pack = build_pack_a()
    rule = daily_loss_rule(5.0, reset_policy=DailyResetPolicy.NONE, reset_time=None)
    result = evaluate_one(pack, rule, make_state(events=(close_event(gross=-9_000.0),)))
    assert result.status is EvaluationStatus.NOT_APPLICABLE
    assert result.explanation_code is ExplanationCode.SCOPE_NOT_APPLICABLE



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 4-5  STATIC AND TRAILING DRAWDOWN
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def static_dd_rule(value: float = 10.0, anchor=LimitBasis.PERCENT_INITIAL_BALANCE, **kwargs) -> DrawdownRule:
    return DrawdownRule(
        rule_id="r.static_dd", rule_type=RuleType.STATIC_DRAWDOWN, enabled=True,
        severity=RuleSeverity.TERMINATION, source=SOURCE, effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.STATIC_DRAWDOWN),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York", kind=DrawdownKind.STATIC,
        limit=pct(anchor, value), anchor=anchor, **kwargs,
    )


def trailing_dd_rule(
    value: float = 8.0,
    anchor=LimitBasis.PERCENT_HIGH_WATER_EQUITY,
    trail_reference: str = "EQUITY",
    **kwargs,
) -> DrawdownRule:
    return DrawdownRule(
        rule_id="r.trailing_dd", rule_type=RuleType.TRAILING_DRAWDOWN, enabled=True,
        severity=RuleSeverity.TERMINATION, source=SOURCE, effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.TRAILING_DRAWDOWN),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York", kind=DrawdownKind.TRAILING,
        limit=pct(anchor, value), anchor=anchor, trail_reference=trail_reference,
        trail_update_cadence="EOD", **kwargs,
    )


def test_static_drawdown_on_balance_uses_the_initial_balance_anchor():
    pack = build_pack_a()
    state = make_state(balance=92_000.0, equity=92_000.0)
    result = evaluate_one(pack, static_dd_rule(10.0), state)
    # 8% fall from the immutable 100,000 anchor, against a 10% limit.
    assert result.status is EvaluationStatus.PASS
    assert result.current_value == pytest.approx(8.0)
    assert result.limit_value == 10.0
    assert result.remaining_buffer == pytest.approx(2.0)


def test_static_drawdown_breaches_past_the_limit():
    pack = build_pack_a()
    state = make_state(balance=85_000.0, equity=85_000.0)
    result = evaluate_one(pack, static_dd_rule(10.0), state)
    assert result.status is EvaluationStatus.BREACH
    assert result.current_value == pytest.approx(15.0)


def test_static_drawdown_on_equity_is_a_different_rule():
    pack = build_pack_a()
    # Balance intact but equity down: only the EQUITY-anchored rule breaches.
    state = make_state(balance=100_000.0, equity=85_000.0)
    on_balance = evaluate_one(pack, static_dd_rule(10.0), state)
    on_equity = evaluate_one(
        pack, static_dd_rule(10.0, anchor=LimitBasis.PERCENT_INITIAL_EQUITY), state
    )
    assert on_balance.status is EvaluationStatus.PASS
    assert on_equity.status is EvaluationStatus.BREACH
    assert on_equity.current_value == pytest.approx(15.0)


def test_static_drawdown_without_an_initial_anchor_is_indeterminate():
    pack = build_pack_a()
    state = make_state(with_anchor=False, initial_balance=None, balance=85_000.0, equity=85_000.0)
    result = evaluate_one(pack, static_dd_rule(10.0), state)
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.INITIAL_ANCHOR_UNAVAILABLE


def test_trailing_drawdown_uses_the_equity_high_water():
    pack = build_pack_b()
    state = make_state(balance=100_000.0, equity=94_000.0, initial_balance=100_000.0)
    # The high-water equity is 100,000, so the fall to 94,000 is 6%.
    result = evaluate_one(pack, trailing_dd_rule(8.0), state)
    assert result.status is EvaluationStatus.PASS
    assert result.current_value == pytest.approx(6.0)
    assert "TRAIL_REFERENCE=EQUITY" in result.detail
    assert "CADENCE=EOD" in result.detail


def test_trailing_drawdown_breaches_below_the_high_water():
    pack = build_pack_b()
    state = make_state(balance=100_000.0, equity=85_000.0)
    result = evaluate_one(pack, trailing_dd_rule(8.0), state)
    assert result.status is EvaluationStatus.BREACH
    assert result.current_value == pytest.approx(15.0)


def test_trailing_drawdown_on_balance_high_water_is_separate():
    pack = build_pack_b()
    # Equity high but balance untouched: the BALANCE high-water rule must pass.
    state = make_state(balance=100_000.0, equity=100_000.0)
    on_balance = evaluate_one(
        pack, trailing_dd_rule(8.0, anchor=LimitBasis.PERCENT_HIGH_WATER_BALANCE,
                               trail_reference="BALANCE"), state
    )
    assert on_balance.status is EvaluationStatus.PASS
    assert on_balance.current_value == 0.0


def test_trailing_drawdown_without_high_water_state_is_indeterminate():
    """Section 20: required trail state unavailable means INDETERMINATE."""
    pack = build_pack_b()
    state = make_state(with_high_water=False, balance=85_000.0, equity=85_000.0)
    result = evaluate_one(pack, trailing_dd_rule(8.0), state)
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.HIGH_WATER_UNAVAILABLE


def test_trailing_drawdown_with_a_declared_coverage_gap_is_indeterminate():
    """An unseen period must not be assumed to have had no higher equity."""
    from core.risk.prop_rule_state import HighWaterGapKind

    pack = build_pack_b()
    gapped = high_water().note_coverage_gap("NO_OBSERVATION_2026-03-05")
    state = make_state(balance=96_000.0, equity=96_000.0)
    state = replace(state, high_water=gapped)
    result = evaluate_one(pack, trailing_dd_rule(8.0), state)
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.HIGH_WATER_COVERAGE_GAP



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 6  PROFIT TARGET
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def profit_target_rule(value: float = 8.0, **kwargs) -> ProfitTargetRule:
    params = dict(
        rule_id="r.profit_target", rule_type=RuleType.PROFIT_TARGET, enabled=True,
        severity=RuleSeverity.ADVISORY, source=SOURCE, effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.PROFIT_TARGET),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
        timezone_name="America/New_York",
        limit=pct(LimitBasis.PERCENT_INITIAL_BALANCE, value), counts_floating_profit=False,
    )
    params.update(kwargs)
    return ProfitTargetRule(**params)


def test_profit_target_reports_met_from_the_durable_profit_series():
    pack = build_pack_a()
    # Two profitable days totalling 9,000 against an 8,000 target.
    state = make_state(events=(
        close_event(source_trade_id="d1", closed_at=datetime(2026, 3, 9, 14, tzinfo=UTC),
                    gross=5_000.0, commission=0.0, swap=0.0, fees=0.0),
        close_event(source_trade_id="d2", closed_at=datetime(2026, 3, 10, 14, tzinfo=UTC),
                    gross=4_000.0, commission=0.0, swap=0.0, fees=0.0),
    ))
    result = evaluate_one(pack, profit_target_rule(8.0), state)
    assert result.status is EvaluationStatus.PASS
    assert result.explanation_code is ExplanationCode.TARGET_MET
    assert result.limit_value == 8_000.0
    assert result.current_value == 9_000.0


def test_profit_target_not_met_is_not_a_breach():
    """Failing to reach a target is a normal state, never a violation."""
    pack = build_pack_a()
    state = make_state(events=(close_event(gross=1_000.0),))
    result = evaluate_one(pack, profit_target_rule(8.0), state)
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.TARGET_NOT_MET
    assert result.breach is False
    # The series is NET realised (1,000 gross less 4 of cost), not gross.
    assert result.current_value == 996.0
    assert result.remaining_buffer == pytest.approx(7_004.0)


def test_profit_target_takes_no_completion_action():
    pack = build_pack_a()
    state = make_state(events=(close_event(gross=9_000.0, commission=0.0, swap=0.0, fees=0.0),))
    result = evaluate_one(pack, profit_target_rule(8.0), state)
    # A factual "met" verdict only. No event, flag or action is produced.
    assert result.status is EvaluationStatus.PASS
    assert "action" not in result.to_dict()
    assert set(result.to_dict()) == set(result.__dataclass_fields__)


def test_profit_target_counting_floating_includes_it_explicitly():
    pack = build_pack_a()
    events = (close_event(gross=1_000.0, commission=0.0, swap=0.0, fees=0.0),)
    without = evaluate_one(pack, profit_target_rule(8.0), state=make_state(events=events))
    with_float = evaluate_one(
        pack, profit_target_rule(8.0, counts_floating_profit=True),
        state=make_state(events=events, floating=8_000.0),
    )
    assert without.current_value == 1_000.0
    assert with_float.current_value == 9_000.0
    assert with_float.status is EvaluationStatus.PASS


def test_profit_target_with_a_minimum_day_dependency_is_indeterminate():
    """3A's ProfitTargetRule carries no qualifying criterion, so the dependency
    cannot be resolved: it stays INDETERMINATE rather than being assumed met."""
    pack = build_pack_a()
    rule = ProfitTargetRule(
        rule_id="r.pt_min_days", rule_type=RuleType.PROFIT_TARGET, enabled=True,
        severity=RuleSeverity.ADVISORY, source=SOURCE, effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.PROFIT_TARGET),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY, timezone_name="America/New_York",
        limit=pct(LimitBasis.PERCENT_INITIAL_BALANCE, 8.0), counts_floating_profit=False,
        minimum_days_required=5,
    )
    state = make_state(events=(close_event(gross=9_000.0, commission=0.0, swap=0.0, fees=0.0),))
    result = evaluate_one(pack, rule, state)
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.TRADING_DAY_CRITERION_UNSPECIFIED


def test_profit_target_min_day_dependency_blocks_a_met_target():
    pack = build_pack_a()
    rule = ProfitTargetRule(
        rule_id="r.pt_min_days2", rule_type=RuleType.PROFIT_TARGET, enabled=True,
        severity=RuleSeverity.ADVISORY, source=SOURCE, effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.PROFIT_TARGET),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY, timezone_name="America/New_York",
        limit=pct(LimitBasis.PERCENT_INITIAL_BALANCE, 8.0), counts_floating_profit=False,
        minimum_days_required=5, unknown_payload={"qualifying_criterion": "ANY_CLOSED_TRADE"},
    )
    state = make_state(events=(close_event(gross=9_000.0, commission=0.0, swap=0.0, fees=0.0),))
    result = evaluate_one(pack, rule, state)
    # Only one qualifying day exists, so the minimum-day gate is unmet even
    # though the profit target itself has been reached.
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.PHASE_DEPENDENCY_UNMET
    assert result.current_value == 1.0
    assert result.limit_value == 5.0


def test_profit_target_without_a_durable_series_is_indeterminate():
    pack = build_pack_a()
    result = evaluate_one(pack, profit_target_rule(8.0), make_state(events=()))
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.LEDGER_INCOMPLETE

def inactivity_rule(basis: str | None = None, window: str = "3d", **kwargs) -> InactivityRule:
    params = dict(
        rule_id="r.inactivity", rule_type=RuleType.INACTIVITY_RULE, enabled=True,
        severity=RuleSeverity.TERMINATION, source=SOURCE, effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.INACTIVITY_RULE),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY, timezone_name="America/New_York",
        max_inactive_duration=window, qualifying_criterion="ANY_CLOSED_TRADE",
    )
    if basis is not None:
        params["unknown_payload"] = {"inactivity_basis": basis}
    params.update(kwargs)
    return InactivityRule(**params)


def test_inactivity_rule_without_a_declared_basis_is_indeterminate():
    """Nothing is assumed about whether weekends count."""
    pack = build_pack_a()
    state = make_state(events=(
        close_event(source_trade_id="d1", closed_at=datetime(2026, 3, 6, 14, tzinfo=UTC)),
    ))
    result = evaluate_one(pack, inactivity_rule(basis=None), state)
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.INACTIVITY_BASIS_UNDECLARED


def test_inactivity_rule_with_an_unrecognised_basis_is_indeterminate():
    pack = build_pack_a()
    state = make_state(events=(
        close_event(source_trade_id="d1", closed_at=datetime(2026, 3, 6, 14, tzinfo=UTC)),
    ))
    result = evaluate_one(pack, inactivity_rule(basis="BUSINESS_DAYS_MAYBE"), state)
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.INACTIVITY_BASIS_UNDECLARED


def test_inactivity_breaches_when_the_declared_calendar_window_is_exceeded():
    pack = build_pack_a()
    # Last qualifying day is the 6th; today is the 10th -> 4 inactive days > 3.
    state = make_state(events=(
        close_event(source_trade_id="d1", closed_at=datetime(2026, 3, 6, 14, tzinfo=UTC)),
    ))
    result = evaluate_one(pack, inactivity_rule(basis="CALENDAR_DAYS"), state)
    assert result.status is EvaluationStatus.BREACH
    assert result.current_value == 4.0
    assert result.limit_value == 3.0
    assert "BASIS=CALENDAR_DAYS" in result.detail


def test_inactivity_passes_within_the_declared_window():
    pack = build_pack_a()
    state = make_state(events=(
        close_event(source_trade_id="d1", closed_at=datetime(2026, 3, 9, 14, tzinfo=UTC)),
    ))
    result = evaluate_one(pack, inactivity_rule(basis="CALENDAR_DAYS"), state)
    assert result.status is EvaluationStatus.PASS
    assert result.current_value == 1.0


def test_inactivity_with_an_unparseable_window_is_indeterminate():
    pack = build_pack_a()
    state = make_state(events=(
        close_event(source_trade_id="d1", closed_at=datetime(2026, 3, 9, 14, tzinfo=UTC)),
    ))
    result = evaluate_one(pack, inactivity_rule(basis="CALENDAR_DAYS", window="a while"), state)
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.INACTIVITY_STATE_INDETERMINATE



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 8  POSITION, LOT AND OPEN RISK FROM EXACT BLOCK 2 TELEMETRY
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def position_rule(basis: LimitBasis, value: float, **kwargs) -> PositionLimitRule:
    params = dict(
        rule_id=f"r.pos_{basis.value}", rule_type=(
            RuleType.MAX_POSITION_SIZE if basis is LimitBasis.LOT_COUNT
            else RuleType.MAX_OPEN_POSITIONS
        ),
        enabled=True, severity=RuleSeverity.TERMINATION, source=SOURCE,
        effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.MAX_POSITION_SIZE),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York", limit=Limit(basis=basis, value=value),
    )
    params.update(kwargs)
    return PositionLimitRule(**params)


def risk_rule(rule_type: RuleType, value: float, scope: str = "TOTAL", **kwargs) -> OpenRiskLimitRule:
    params = dict(
        rule_id=f"r.risk_{scope}", rule_type=rule_type, enabled=True,
        severity=RuleSeverity.TERMINATION, source=SOURCE, effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(rule_type),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        limit=Limit(basis=LimitBasis.ABSOLUTE_MONEY, value=value,
                    currency_semantics=CurrencySemantics.ACCOUNT_CURRENCY),
        scope_kind=scope,
    )
    params.update(kwargs)
    return OpenRiskLimitRule(**params)


def test_max_position_size_uses_block2_lot_size():
    pack = build_pack_a()
    result = evaluate_one(pack, position_rule(LimitBasis.LOT_COUNT, 1.0), make_state(position_volumes=(0.5, 0.3)))
    assert result.unit == "LOTS"
    assert result.current_value == 0.5
    assert result.status is EvaluationStatus.PASS


def test_max_position_size_breaches_above_the_lot_limit():
    pack = build_pack_a()
    result = evaluate_one(pack, position_rule(LimitBasis.LOT_COUNT, 1.0), make_state(position_volumes=(2.5, 0.3)))
    assert result.status is EvaluationStatus.BREACH
    assert result.current_value == 2.5


def test_max_open_positions_uses_block2_position_count():
    pack = build_pack_a()
    state = make_state()
    assert evaluate_one(pack, position_rule(LimitBasis.POSITION_COUNT, 3), state).status is EvaluationStatus.PASS
    assert evaluate_one(pack, position_rule(LimitBasis.POSITION_COUNT, 1), state).status is EvaluationStatus.BREACH


def test_max_open_risk_uses_the_block2b_authoritative_total():
    pack = build_pack_a()
    result = evaluate_one(pack, risk_rule(RuleType.MAX_OPEN_RISK, 1_000.0), make_state())
    assert result.unit == "MONEY"
    assert result.current_value == 500.0
    assert result.status is EvaluationStatus.PASS
    assert evaluate_one(pack, risk_rule(RuleType.MAX_OPEN_RISK, 100.0), make_state()).status is EvaluationStatus.BREACH


def test_per_position_risk_uses_the_largest_single_position():
    pack = build_pack_a()
    result = evaluate_one(pack, risk_rule(RuleType.MAX_RISK_PER_POSITION, 250.0, scope="PER_POSITION"), make_state())
    assert result.current_value == 300.0
    assert result.status is EvaluationStatus.BREACH


def test_symbol_risk_uses_the_block2c_symbol_exposure():
    pack = build_pack_a()
    result = evaluate_one(pack, risk_rule(RuleType.MAX_RISK_PER_SYMBOL, 500.0, scope="PER_SYMBOL"), make_state())
    assert result.current_value == 400.0
    assert result.status is EvaluationStatus.PASS
    assert "SCOPE=PER_SYMBOL" in result.detail



def correlated_rule(value: float = 500.0) -> OpenRiskLimitRule:
    return risk_rule(
        RuleType.MAX_CORRELATED_RISK, value, scope="CORRELATION_CLUSTER",
        correlation_cluster_scoped=True,
        telemetry_requirements=required_telemetry_for(RuleType.MAX_CORRELATED_RISK),
    )


def test_correlated_risk_uses_the_block2c_cluster_exposure():
    pack = build_pack_a()
    result = evaluate_one(pack, correlated_rule(), make_state())
    assert result.current_value == 450.0
    assert result.status is EvaluationStatus.PASS


def test_incomplete_correlation_is_indeterminate_not_an_understated_pass():
    pack = build_pack_a()
    result = evaluate_one(pack, correlated_rule(), make_state(correlation_complete=False))
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.CORRELATION_NOT_COMPLETE


def test_directional_risk_uses_the_block2c_direction_exposure():
    pack = build_pack_a()
    result = evaluate_one(pack, risk_rule(RuleType.MAX_DIRECTIONAL_RISK, 300.0, scope="DIRECTIONAL"), make_state())
    assert result.current_value == 350.0
    assert result.status is EvaluationStatus.BREACH


def test_partial_open_risk_floor_is_never_presented_as_a_total():
    """A risk_complete=False total must not be used as an authoritative total."""
    pack = build_pack_a()
    state = make_state(risk_complete=False, total_open_risk=None)
    result = evaluate_one(pack, risk_rule(RuleType.MAX_OPEN_RISK, 1_000.0), state)
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.TELEMETRY_MISSING
    assert result.current_value is None


def test_missing_position_telemetry_is_indeterminate():
    pack = build_pack_a()
    result = evaluate_one(pack, risk_rule(RuleType.MAX_OPEN_RISK, 1_000.0), make_state(with_open_risk=False))
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.TELEMETRY_MISSING


def test_missing_portfolio_telemetry_is_indeterminate():
    pack = build_pack_a()
    result = evaluate_one(
        pack, risk_rule(RuleType.MAX_RISK_PER_SYMBOL, 500.0, scope="PER_SYMBOL"),
        make_state(with_portfolio=False),
    )
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.TELEMETRY_MISSING


def test_stale_telemetry_is_indeterminate():
    pack = build_pack_a()
    result = evaluate_one(pack, risk_rule(RuleType.MAX_OPEN_RISK, 1_000.0), make_state(stale=True))
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.TELEMETRY_STALE


def test_unknown_risk_scope_is_unsupported_not_guessed():
    pack = build_pack_a()
    result = evaluate_one(pack, risk_rule(RuleType.MAX_OPEN_RISK, 1_000.0, scope="SOMETHING_ELSE"), make_state())
    assert result.status is EvaluationStatus.UNSUPPORTED
    assert result.explanation_code is ExplanationCode.RULE_TYPE_UNSUPPORTED



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 9  EXTERNAL-SOURCE FOUNDATIONS: news and hold restrictions
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def news_rule(blackout: int | None = 2, **kwargs) -> NewsTradingRestrictionRule:
    params = dict(
        rule_id="r.news", rule_type=RuleType.NEWS_TRADING_RESTRICTION, enabled=True,
        severity=RuleSeverity.BREACH, source=SOURCE, effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.NEWS_TRADING_RESTRICTION),
        evaluation_time_basis=EvaluationTimeBasis.EVENT_DRIVEN,
        timezone_name="America/New_York", blackouts_high_impact=True,
        importance_levels=(NewsImportance.HIGH,), requires_economic_calendar=True,
    )
    if blackout is not None:
        params["pre_event_blackout_minutes"] = blackout
        params["post_event_blackout_minutes"] = blackout
    params.update(kwargs)
    return NewsTradingRestrictionRule(**params)


def test_news_rule_is_unsupported_without_a_calendar_provider():
    """No economic calendar is faked, so no news verdict is invented."""
    pack = build_pack_a()
    result = evaluate_one(pack, news_rule(), make_state())
    assert result.status is EvaluationStatus.UNSUPPORTED
    assert result.explanation_code is ExplanationCode.EXTERNAL_SOURCE_REQUIRED
    assert result.support_status is EvaluationSupport.REQUIRES_EXTERNAL_SOURCE
    assert result.detail == "NO_ECONOMIC_CALENDAR_PROVIDER"


def test_news_rule_with_a_provider_evaluates_real_events():
    @dataclass(frozen=True)
    class Event:
        importance: str = "HIGH"

    class Calendar:
        def events_in_window(self, start_utc, end_utc):
            return [Event()]

    pack = build_pack_a()
    result = evaluate_one(pack, news_rule(), make_state(), economic_calendar=Calendar())
    assert result.status is EvaluationStatus.BREACH
    assert result.current_value == 1.0


def test_news_rule_with_a_failing_provider_is_indeterminate():
    class BrokenCalendar:
        def events_in_window(self, start_utc, end_utc):
            raise RuntimeError("provider down")

    pack = build_pack_a()
    result = evaluate_one(pack, news_rule(), make_state(), economic_calendar=BrokenCalendar())
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.EXTERNAL_SOURCE_REQUIRED


def test_news_rule_with_an_unspecified_blackout_window_is_indeterminate():
    pack = build_pack_a()
    result = evaluate_one(pack, news_rule(blackout=None), make_state())
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.detail == "BLACKOUT_WINDOW_NOT_SPECIFIED_BY_RULE"


def weekend_rule(permitted: bool, cutoff: time | None = time(20, 0)) -> HoldRestrictionRule:
    params = dict(
        rule_id="r.weekend", rule_type=RuleType.WEEKEND_HOLD_RESTRICTION, enabled=True,
        severity=RuleSeverity.TERMINATION, source=SOURCE, effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.WEEKEND_HOLD_RESTRICTION),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York", kind=HoldRestrictionKind.WEEKEND_HOLD,
        holding_permitted=permitted,
    )
    if not permitted:
        params["must_close_before_market_close"] = True
        params["cutoff_time"] = cutoff
    return HoldRestrictionRule(**params)


def test_weekend_hold_is_not_applicable_when_holding_is_permitted():
    pack = build_pack_a()
    result = evaluate_one(pack, weekend_rule(permitted=True), make_state())
    assert result.status is EvaluationStatus.NOT_APPLICABLE
    assert result.explanation_code is ExplanationCode.SCOPE_NOT_APPLICABLE


def test_weekend_hold_is_unsupported_without_a_session_calendar():
    """Exchange close times are never inferred from a symbol name."""
    pack = build_pack_b()
    result = evaluate_one(pack, weekend_rule(permitted=False), make_state())
    assert result.status is EvaluationStatus.UNSUPPORTED
    assert result.explanation_code is ExplanationCode.EXTERNAL_SOURCE_REQUIRED
    assert result.detail == "NO_MARKET_SESSION_CALENDAR_PROVIDER"


def test_weekend_hold_with_a_calendar_uses_the_exact_open_position_set():
    class Sessions:
        def is_market_open(self, symbol, at_utc):
            return False

    pack = build_pack_b()
    # 10:00 New York is past a 09:00 cutoff, with two positions proven open.
    result = evaluate_one(
        pack, weekend_rule(permitted=False, cutoff=time(9, 0)), make_state(),
        market_sessions=Sessions(),
    )
    assert result.status is EvaluationStatus.BREACH
    assert result.current_value == 2.0
    assert "ticket:11" in result.evidence_used



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 10  CONSISTENCY
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def consistency_rule(value: float = 40.0) -> Any:
    from core.risk.prop_rule_contracts import ConsistencyRule

    return ConsistencyRule(
        rule_id="r.consistency", rule_type=RuleType.CONSISTENCY_RULE, enabled=True,
        severity=RuleSeverity.BREACH, source=SOURCE, effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.CONSISTENCY_RULE),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY, timezone_name="America/New_York",
        limit=pct(LimitBasis.PERCENT_CURRENT_EQUITY, value),
        numerator_definition="BEST_DAY_NET_PROFIT",
        denominator_definition="TOTAL_NET_PROFIT",
    )


def test_consistency_evaluates_best_day_share_of_total_profit():
    pack = build_pack_b()
    # Days: 600, 300, 100 -> the best day is 60% of the 1,000 total.
    state = make_state(events=tuple(
        close_event(source_trade_id=f"d{index}", closed_at=datetime(2026, 3, 8 + index, 14, tzinfo=UTC),
                    gross=gross, commission=0.0, swap=0.0, fees=0.0)
        for index, gross in enumerate((600.0, 300.0, 100.0))
    ))
    result = evaluate_one(pack, consistency_rule(40.0), state)
    assert result.current_value == pytest.approx(60.0)
    assert result.status is EvaluationStatus.BREACH
    assert "BEST_DAY=2026-03-08" in result.detail


def test_consistency_is_indeterminate_on_a_partially_known_series():
    pack = build_pack_b()
    # The 9th has unknown swap, so the series cannot be treated as complete.
    state = make_state(events=(
        close_event(source_trade_id="d1", closed_at=datetime(2026, 3, 8, 14, tzinfo=UTC),
                    gross=600.0, commission=0.0, swap=0.0, fees=0.0),
        close_event(source_trade_id="d2", closed_at=datetime(2026, 3, 9, 14, tzinfo=UTC),
                    gross=300.0, commission=0.0, swap=None, fees=0.0),
    ))
    result = evaluate_one(pack, consistency_rule(40.0), state)
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.CONSISTENCY_DENOMINATOR_UNAVAILABLE


def test_consistency_is_indeterminate_when_the_denominator_is_zero():
    pack = build_pack_b()
    state = make_state(events=(
        close_event(source_trade_id="d1", closed_at=datetime(2026, 3, 8, 14, tzinfo=UTC),
                    gross=0.0, commission=0.0, swap=0.0, fees=0.0),
    ))
    result = evaluate_one(pack, consistency_rule(40.0), state)
    assert result.status is EvaluationStatus.INDETERMINATE
    assert "ZERO_DENOMINATOR" in result.detail


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 11  SUPPORT MATRIX COMPLETENESS
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def test_support_matrix_covers_every_3a_rule_type():
    matrix = support_matrix_3b()
    assert set(matrix) == set(RuleType)
    assert len(matrix) == 29


def test_every_rule_type_is_classified_into_exactly_one_bucket():
    allowed = {"EVALUATED", "REQUIRES_EXTERNAL", "NOT_EVALUATED"}
    for rule_type, classification in support_matrix_3b().items():
        assert classification in allowed, rule_type


def test_external_source_rules_are_not_silently_upgraded():
    """3A said these need an external source; a Protocol is not a data source."""
    for rule_type in (
        RuleType.NEWS_TRADING_RESTRICTION,
        RuleType.WEEKEND_HOLD_RESTRICTION,
        RuleType.OVERNIGHT_HOLD_RESTRICTION,
        RuleType.IP_DEVICE_LOCATION_RESTRICTION,
        RuleType.EA_AUTOMATION_PERMISSION,
        RuleType.COPY_TRADING_RESTRICTION,
        RuleType.REFUND_RULE,
        RuleType.RESET_RULE,
    ):
        assert rule_type_evaluation_support(rule_type) == "REQUIRES_EXTERNAL"


def test_rules_backed_by_new_durable_state_are_now_evaluated():
    for rule_type in (
        RuleType.DAILY_LOSS_LIMIT, RuleType.STATIC_DRAWDOWN, RuleType.TRAILING_DRAWDOWN,
        RuleType.PROFIT_TARGET, RuleType.MIN_TRADING_DAYS, RuleType.MAX_TRADING_DAYS,
        RuleType.INACTIVITY_RULE, RuleType.CONSISTENCY_RULE,
    ):
        assert rule_type_evaluation_support(rule_type) == "EVALUATED"


def test_a_disabled_rule_is_not_applicable_not_silently_dropped():
    pack = build_pack_a()
    rule = replace(daily_loss_rule(5.0), enabled=False)
    result = evaluate_one(pack, rule, make_state(events=(close_event(gross=-250.0),)))
    assert result.status is EvaluationStatus.NOT_APPLICABLE
    assert result.explanation_code is ExplanationCode.RULE_DISABLED


def test_a_rule_outside_its_effective_window_is_not_applicable():
    """No retroactive application of a rule that was not yet in force."""
    pack = build_pack_a()
    rule = replace(daily_loss_rule(5.0), effective_from=datetime(2027, 1, 1, tzinfo=UTC))
    result = evaluate_one(pack, rule, make_state(events=(close_event(gross=-250.0),)))
    assert result.status is EvaluationStatus.NOT_APPLICABLE
    assert result.explanation_code is ExplanationCode.RULE_NOT_EFFECTIVE


def test_a_conflicted_state_makes_every_rule_indeterminate():
    pack = build_pack_a()
    conflicted = replace(
        make_state(), status=StateStatus.CONFLICT, invalid_fields=("initial_anchor.account_currency",)
    )
    result = evaluate_one(pack, daily_loss_rule(5.0), conflicted)
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.INITIAL_ANCHOR_CONFLICT



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 12  DETERMINISTIC EVALUATION IDENTITY
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def test_same_inputs_produce_the_same_evaluation_id():
    pack = build_pack_a()
    rule = daily_loss_rule(5.0)
    state = make_state(events=(close_event(gross=-250.0),))
    first = evaluate_one(pack, rule, state)
    second = evaluate_one(build_pack_a(), daily_loss_rule(5.0), make_state(events=(close_event(gross=-250.0),)))
    assert first.evaluation_id == second.evaluation_id
    assert first.to_dict() == second.to_dict()


def test_changed_evidence_yields_a_new_evaluation_id():
    pack = build_pack_a()
    rule = daily_loss_rule(5.0)
    before = evaluate_one(pack, rule, make_state(events=(close_event(gross=-250.0),)))
    after = evaluate_one(pack, rule, make_state(events=(
        close_event(source_trade_id="d1", gross=-250.0),
        close_event(source_trade_id="d2", closed_at=datetime(2026, 3, 10, 15, tzinfo=UTC), gross=-100.0),
    )))
    assert before.evaluation_id != after.evaluation_id
    assert before.current_value != after.current_value


def test_a_different_evaluated_instant_yields_a_new_evaluation_id():
    pack = build_pack_a()
    rule = daily_loss_rule(5.0)
    state = make_state(events=(close_event(gross=-250.0),))
    first = evaluate_one(pack, rule, state, evaluated_at=EVAL_AT)
    second = evaluate_one(pack, rule, state, evaluated_at=EVAL_AT + timedelta(hours=1))
    assert first.evaluation_id != second.evaluation_id


def test_a_different_account_yields_a_new_evaluation_id():
    pack = build_pack_a()
    rule = daily_loss_rule(5.0)
    first = evaluate_one(pack, rule, make_state(events=(close_event(gross=-250.0),)))
    second = evaluate_one(
        pack, rule,
        make_state(account=ACCOUNT_B, events=(close_event(account=ACCOUNT_B, gross=-250.0),)),
    )
    assert first.evaluation_id != second.evaluation_id


def test_evaluation_id_derivation_is_pure():
    payload = dict(
        rule_pack_id="prp1:abc", rule_id="r.x", account=ACCOUNT,
        evaluated_at_utc=EVAL_AT, rule_day=RULE_DAY, timezone_name="America/New_York",
        evidence_lineage={"ledger_id": "dpl_1"},
    )
    assert derive_evaluation_id(**payload) == derive_evaluation_id(**payload)
    changed = derive_evaluation_id(**{**payload, "evidence_lineage": {"ledger_id": "dpl_2"}})
    assert changed != derive_evaluation_id(**payload)


def test_evaluation_result_is_immutable_and_serialisable():
    pack = build_pack_a()
    result = evaluate_one(pack, daily_loss_rule(5.0), make_state(events=(close_event(gross=-250.0),)))
    with pytest.raises(Exception):
        result.status = EvaluationStatus.PASS  # type: ignore[misc]
    payload = result.to_dict()
    assert payload["status"] in {s.value for s in EvaluationStatus}
    assert payload["rule_type"] == "DAILY_LOSS_LIMIT"
    assert payload["evaluated_at_utc"].endswith("+00:00")


def test_breach_flag_and_status_stay_consistent():
    from core.risk.prop_rule_evaluator import EvaluationResult as ER

    base = dict(
        evaluation_id="ev_x", rule_pack_id="p", rule_id="r", rule_type=RuleType.DAILY_LOSS_LIMIT,
        account_id="ACC_A", evaluated_at_utc=EVAL_AT, rule_day=RULE_DAY,
        timezone_name="America/New_York", unit="MONEY",
    )
    with pytest.raises(ValueError):
        ER(**base, status=EvaluationStatus.PASS, breach=True)
    with pytest.raises(ValueError):
        ER(**base, status=EvaluationStatus.BREACH, breach=False)



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 13  HISTORICAL REPLAY
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def versioned_store():
    """v1 through 2026-04-01, v2 from 2026-04-01."""
    store = RulePackStore()
    v1 = build_pack_a(pack_a_identity(
        version="1.0.0", effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        effective_to=datetime(2026, 4, 1, tzinfo=UTC),
    ))
    v2 = build_pack_a(pack_a_identity(
        version="2.0.0", effective_from=datetime(2026, 4, 1, tzinfo=UTC),
    ))
    store.register_rule_pack(v1)
    store.register_rule_pack(v2)
    return store, v1, v2


SELECT = ("SYNTHETIC_FIRM_A", "SYNTHETIC_CHALLENGE", RulePhase.EVALUATION_PHASE_1, 100_000)


def plain_state(moment: datetime) -> AccountEvaluationState:
    return build_account_evaluation_state(
        account=ACCOUNT, account_currency="USD", observed_at_utc=moment, definition=NY,
        initial_anchor=initial_anchor(), daily_anchor=daily_anchor(),
        account_snapshot=FakeSnapshot(), open_risk=FakeOpenRisk(), portfolio=FakePortfolio(),
    )


def test_replay_binds_each_instant_to_the_pack_in_force_then():
    """Section 42: no retroactive application across a version boundary."""
    store, v1, v2 = versioned_store()
    before_at = datetime(2026, 3, 10, 15, tzinfo=UTC)
    after_at = datetime(2026, 4, 10, 15, tzinfo=UTC)
    results = replay_account_evaluations(
        account=ACCOUNT,
        states_by_instant={before_at: plain_state(before_at), after_at: plain_state(after_at)},
        definition=NY,
        resolve_pack=lambda moment: rules_effective_at(store, *SELECT, moment),
    )
    assert {r.rule_pack_id for r in results} == {v1.rule_pack_id, v2.rule_pack_id}
    before_rows = [r for r in results if r.evaluated_at_utc == before_at]
    after_rows = [r for r in results if r.evaluated_at_utc == after_at]
    assert before_rows and all(r.rule_pack_id == v1.rule_pack_id for r in before_rows)
    assert after_rows and all(r.rule_pack_id == v2.rule_pack_id for r in after_rows)


def test_replay_is_ordered_and_deterministic():
    store, _v1, _v2 = versioned_store()
    instants = [datetime(2026, 3, day, 15, tzinfo=UTC) for day in (9, 10, 11)]
    states = {moment: plain_state(moment) for moment in instants}
    kwargs = dict(
        account=ACCOUNT, states_by_instant=states, definition=NY,
        resolve_pack=lambda moment: rules_effective_at(store, *SELECT, moment),
    )
    first = replay_account_evaluations(**kwargs)
    shuffled = dict(kwargs, states_by_instant=dict(reversed(list(states.items()))))
    second = replay_account_evaluations(**shuffled)
    assert [r.to_dict() for r in first] == [r.to_dict() for r in second]
    assert [r.evaluated_at_utc for r in first] == sorted(r.evaluated_at_utc for r in first)


def test_replay_is_side_effect_free_and_leaves_state_untouched(tmp_path):
    """Replay writes nothing and mutates nothing."""
    store, _v1, _v2 = versioned_store()
    moment = datetime(2026, 3, 10, 15, tzinfo=UTC)
    state = plain_state(moment)
    before = state.to_dict()
    before_files = sorted(p.name for p in tmp_path.iterdir()) if tmp_path.exists() else []
    replay_account_evaluations(
        account=ACCOUNT, states_by_instant={moment: state}, definition=NY,
        resolve_pack=lambda m: rules_effective_at(store, *SELECT, m),
    )
    assert state.to_dict() == before
    after_files = sorted(p.name for p in tmp_path.iterdir()) if tmp_path.exists() else []
    assert before_files == after_files


def test_replay_produces_one_result_per_rule_and_never_drops_one():
    store, _v1, _v2 = versioned_store()
    moment = datetime(2026, 3, 10, 15, tzinfo=UTC)
    pack = rules_effective_at(store, *SELECT, moment)
    results = replay_account_evaluations(
        account=ACCOUNT, states_by_instant={moment: plain_state(moment)}, definition=NY,
        resolve_pack=lambda m: pack,
    )
    assert len(results) == len(pack.rules_effective_at(moment))
    assert {r.rule_id for r in results} == {r.rule_id for r in pack.rules_effective_at(moment)}


def test_evaluate_pack_covers_every_rule_in_force():
    pack = build_pack_a()
    results = evaluate_pack(context_for(pack, make_state(events=(close_event(),))))
    assert {r.rule_id for r in results} == {r.rule_id for r in pack.rules_effective_at(EVAL_AT)}
    for result in results:
        assert result.status in set(EvaluationStatus)
        assert result.evaluation_id



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 14  SAME-INSTANT MULTI-ACCOUNT ISOLATION
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def test_two_accounts_at_the_same_instant_stay_isolated():
    """Section 36: same timestamp, same ticket, same symbol -> distinct state."""
    pack = build_pack_a()
    rule = daily_loss_rule(5.0)
    at = EVAL_AT
    state_a = build_account_evaluation_state(
        account=ACCOUNT, account_currency="USD", observed_at_utc=at, definition=NY,
        initial_anchor=initial_anchor(ACCOUNT, 100_000.0), daily_anchor=daily_anchor(ACCOUNT, 100_000.0),
        account_snapshot=FakeSnapshot(account_id=ACCOUNT.account_id, balance=97_000.0, equity=97_000.0),
        open_risk=FakeOpenRisk(account_id=ACCOUNT.account_id),
        portfolio=FakePortfolio(account_id=ACCOUNT.account_id),
        close_events=(close_event(account=ACCOUNT, ticket=777, gross=-1_000.0),),
    )
    state_b = build_account_evaluation_state(
        account=ACCOUNT_B, account_currency="USD", observed_at_utc=at, definition=NY,
        initial_anchor=initial_anchor(ACCOUNT_B, 50_000.0), daily_anchor=daily_anchor(ACCOUNT_B, 50_000.0),
        account_snapshot=FakeSnapshot(account_id=ACCOUNT_B.account_id, balance=49_500.0, equity=49_500.0),
        open_risk=FakeOpenRisk(account_id=ACCOUNT_B.account_id),
        portfolio=FakePortfolio(account_id=ACCOUNT_B.account_id),
        close_events=(close_event(account=ACCOUNT_B, ticket=777, gross=-100.0),),
    )
    result_a = evaluate_rule(context_for(pack, state_a, at), rule)
    result_b = evaluate_rule(context_for(pack, state_b, at), rule)
    assert result_a.account_id == ACCOUNT.account_id
    assert result_b.account_id == ACCOUNT_B.account_id
    assert result_a.evaluation_id != result_b.evaluation_id
    # A's 5% limit is 5,000; B's is 2,500. Neither leaks into the other.
    assert result_a.limit_value == 5_000.0
    assert result_b.limit_value == 2_500.0
    assert result_a.current_value == 1_004.0
    assert result_b.current_value == 104.0


def test_same_ticket_on_two_accounts_produces_two_distinct_ledger_events():
    from core.risk.prop_rule_state import project_daily_ledger

    events = (
        close_event(account=ACCOUNT, source_trade_id="deal-1", ticket=4242, gross=-500.0),
        close_event(account=ACCOUNT_B, source_trade_id="deal-1", ticket=4242, gross=-500.0),
    )
    assert events[0].event_id != events[1].event_id
    for account in (ACCOUNT, ACCOUNT_B):
        ledger = project_daily_ledger(
            account=account, account_currency="USD", definition=NY, rule_day=RULE_DAY, events=events
        )
        assert ledger.trade_count == 1
        assert ledger.gross_realised_pnl == -500.0


def test_a_fixed_currency_rule_against_a_different_account_currency_is_indeterminate():
    pack = build_pack_a()
    rule = daily_loss_rule(
        5.0, basis=LimitBasis.PERCENT_START_OF_DAY_BALANCE,
        limit=Limit(basis=LimitBasis.ABSOLUTE_MONEY, value=5_000.0,
                    currency_semantics=CurrencySemantics.FIXED_RULE_CURRENCY, rule_currency="EUR"),
    )
    result = evaluate_one(pack, rule, make_state(events=(close_event(gross=-250.0),)))
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.CURRENCY_MISMATCH


def test_a_converted_currency_rule_without_a_conversion_source_is_unsupported():
    pack = build_pack_a()
    rule = daily_loss_rule(
        5.0, basis=LimitBasis.PERCENT_START_OF_DAY_BALANCE,
        limit=Limit(basis=LimitBasis.ABSOLUTE_MONEY, value=5_000.0,
                    currency_semantics=CurrencySemantics.CONVERTED_REFERENCE_CURRENCY,
                    rule_currency="EUR", conversion_source="ECB_DAILY"),
    )
    result = evaluate_one(pack, rule, make_state(events=(close_event(gross=-250.0),)))
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is ExplanationCode.CONVERSION_SOURCE_UNAVAILABLE



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# 15  PURITY: NO BROKER, NO RUNTIME GUARD, NO WALL CLOCK
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def _code_tokens(module) -> str:
    """Code-only source text: comments and docstrings removed."""
    import io
    import tokenize

    source = Path(module.__file__).read_text(encoding="utf-8")
    out: list[str] = []
    prev_type = tokenize.INDENT
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in (tokenize.COMMENT, tokenize.STRING):
            is_docstring = token.type == tokenize.STRING and prev_type in (
                tokenize.INDENT, tokenize.NEWLINE, tokenize.NL, tokenize.DEDENT,
            )
            if token.type == tokenize.COMMENT or is_docstring:
                out.append("")
                prev_type = token.type
                continue
        if token.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
            out.append(token.string)
            prev_type = token.type
    return "\n".join(out)


def test_evaluator_has_no_direct_mt5_dependency():
    """Section 45: the pure evaluator consumes state objects, never MT5."""
    import core.risk.prop_rule_evaluator as module

    code = _code_tokens(module)
    for forbidden in ("MetaTrader5", "mt5_call", "account_info", "positions_get", "import mt5"):
        assert forbidden not in code, f"pure evaluator must not use {forbidden}"


def test_evaluator_calls_no_runtime_guard_or_kill_switch():
    """Section 44: read-only with respect to the trading runtime."""
    import core.risk.prop_rule_evaluator as module

    code = _code_tokens(module)
    for forbidden in (
        "kill_switch", "runtime_guard", "order_send", "close_position", "modify_position",
        "DailyLossGuard", "DrawdownGuard", "guard.check", "scanner",
    ):
        assert forbidden not in code, f"pure evaluator must not reference {forbidden}"


def test_evaluator_reads_no_wall_clock():
    import core.risk.prop_rule_evaluator as module

    code = _code_tokens(module)
    for forbidden in ("datetime.now(", "time.time(", "date.today(", "utcnow("):
        assert forbidden not in code, f"deterministic evaluator must not use {forbidden}"


def test_state_assembly_never_fabricates_a_missing_value():
    """A field that cannot be established is None and reported, never guessed."""
    state = make_state(floating=None, balance=None, equity=None)
    assert state.floating_pnl is None
    assert state.current_balance is None
    assert "floating_pnl" in state.unavailable_fields
    assert "current_balance" in state.unavailable_fields
    assert state.status is not StateStatus.COMPLETE


def test_floating_pnl_comes_from_the_account_snapshot_not_position_sums():
    """Section 10: the account-level broker value is authoritative."""
    state = make_state(floating=-123.45, position_volumes=(0.5, 0.3))
    assert state.floating_pnl == -123.45
    # The state's floating value is exactly what Block 2A reported.
    assert state.account_snapshot_id == "asnap_1"
