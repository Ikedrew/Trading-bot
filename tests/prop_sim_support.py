"""SIMULATED GOVERNED PROP PACKS + E2E harness support (Block 3D).

WHAT THIS MODULE IS
-------------------
Block 3D is the END-TO-END ACCEPTANCE of the prop-compliance runtime. It is NOT
another architecture block. This module therefore contains NO new engine, NO new
registry, NO new service and NO simplified duplicate implementation.

It contains exactly three things:

1. ``PACK_STATIC`` / ``PACK_TRAILING``
   Explicitly SYNTHETIC governed rule packs, compiled from the EXISTING Block 3A
   contracts via the EXISTING ``compile_rule_pack``. Every rule carries
   ``SourceType.MANUAL_ASSUMPTION`` provenance with a ``synthetic://`` reference,
   so nothing here can ever be mistaken for a real prop firm's rules.

2. A deterministic simulated account model
   ACCOUNT_A / ACCOUNT_B with exact ``(account_id, broker, server, login)``
   identity and explicit, replayable balance / equity / floating / positions.
   Every number is an explicit scenario fixture. There is no randomness anywhere.

3. Mocked EXTERNAL BOUNDARIES ONLY
   ``SimulatedAccountSource`` (2A broker account observation),
   ``SimulatedPositionSource`` (2B broker position observation),
   ``SimulatedSymbolSpecSource`` / ``SimulatedProfitCalculator`` (2B broker maths),
   ``ScriptedCalendarProvider`` (economic calendar), ``ScriptedSessionProvider``
   (market sessions) and ``RecordingBroker`` (broker order/close transport).

THE REAL PRODUCTION CHAIN IS NEVER REIMPLEMENTED
------------------------------------------------
Every cycle runs the genuine modules, in this exact order:

    PropRiskTelemetryService.observe_account        (Block 2A/2B/2C, real)
      -> capture_account_snapshot / observe_positions / PortfolioExposureProducer
    enforce_positions_for_account                   (3C, real)
      -> PropEnforcementRuntime.enforce_positions   (3C, real)
        -> PropRuleStateProvider                     (3C, real, production factory)
          -> build_account_evaluation_state          (3B, real)
        -> evaluate_pack                             (3B, real)
        -> compile_decision / coalesce_decisions     (3C, real)
        -> EnforcementExecutor                       (3C, real)
          -> PositionClosePort -> RecordingBroker    (mocked transport)
    prop_enforcement_gate                            (3C, real)
      -> PropEnforcementRuntime.authorize_entry      (3C, real)

CLOCK
-----
There is no wall clock anywhere. ``SimulationClock`` is the single injected source
of time; every cycle is one frozen instant shared by 2A, 2B and 2C.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from core.risk.account_snapshot import AccountIdentity
from core.risk.prop_rule_contracts import (
    DailyLossRule,
    DrawdownRule,
    HoldRestrictionRule,
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
    BreachKind,
    CurrencySemantics,
    DailyResetPolicy,
    DayCountBasis,
    DrawdownKind,
    EvaluationTimeBasis,
    HoldRestrictionKind,
    LimitBasis,
    NewsImportance,
    RulePhase,
    RuleSeverity,
    RuleType,
    SourceType,
)
from core.risk.prop_rule_external import (
    EconomicCalendarSnapshot,
    EconomicEvent,
    MarketSession,
    MarketSessionAnswer,
    MarketState,
    SourceError,
    SourceFreshness,
    SourceProvenance,
)
from core.risk.prop_rule_executor import CloseOutcome, CloseRequest, CloseResult
from core.risk.prop_rule_pack import (
    RulePack,
    RulePackIdentity,
    RulePackStore,
    compile_rule_pack,
)
from core.risk.prop_rule_projection import PlannedOrder
from core.risk.prop_rule_state import AccountKey, RuleDayDefinition
from core.risk.prop_rule_values import Limit

UTC = timezone.utc

#: Every rule day in the simulation is bounded in ``America/New_York``.
RULE_DAY = RuleDayDefinition("America/New_York", time(0, 0))

#: A fixed, explicit "now" used as the pack effective instant. Never wall clock.
PACK_EFFECTIVE_FROM = datetime(2026, 1, 1, tzinfo=UTC)

ACCOUNT_CURRENCY = CurrencySemantics.ACCOUNT_CURRENCY

#: The exact simulated account size both packs describe.
ACCOUNT_SIZE = 25_000

#: Every synthetic source is labelled so it can never pass as an official one.
SYNTHETIC_SOURCE = RuleSource(
    source_type=SourceType.MANUAL_ASSUMPTION,
    source_reference="synthetic://block3d/pack_static",
    notes=(
        "SYNTHETIC TEST FIXTURE for Block 3D acceptance. "
        "NOT a real prop firm. Machinery validation only."
    ),
)

SYNTHETIC_SOURCE_TRAILING = RuleSource(
    source_type=SourceType.MANUAL_ASSUMPTION,
    source_reference="synthetic://block3d/pack_trailing",
    notes=(
        "SYNTHETIC TEST FIXTURE for Block 3D acceptance. "
        "NOT a real prop firm. Machinery validation only."
    ),
)


def _pct(basis: LimitBasis, value: float) -> Limit:
    return Limit(basis=basis, value=value, currency_semantics=ACCOUNT_CURRENCY)


# ==========================================================================
# THE TWO SYNTHETIC GOVERNED PACKS
# ==========================================================================


def _static_identity(*, version="1.0.0", phase=RulePhase.EVALUATION_PHASE_1,
                     effective_from=PACK_EFFECTIVE_FROM, effective_to=None
                     ) -> RulePackIdentity:
    return RulePackIdentity(
        provider="SYNTHETIC_FIRM_STATIC", program="SYNTHETIC_CHALLENGE",
        phase=phase, account_size=ACCOUNT_SIZE, currency="USD",
        rule_pack_version=version, effective_from=effective_from,
        effective_to=effective_to,
    )


def _trailing_identity(*, version="1.0.0", phase=RulePhase.EVALUATION_PHASE_1,
                       effective_from=PACK_EFFECTIVE_FROM) -> RulePackIdentity:
    return RulePackIdentity(
        provider="SYNTHETIC_FIRM_TRAILING", program="SYNTHETIC_CHALLENGE",
        phase=phase, account_size=ACCOUNT_SIZE, currency="USD",
        rule_pack_version=version, effective_from=effective_from,
    )


PACK_STATIC_IDENTITY = _static_identity()


def static_daily_loss_rule() -> DailyLossRule:
    """PACK_STATIC daily loss: 5% of start-of-day BALANCE, realised only."""
    return DailyLossRule(
        rule_id="pack_static.daily_loss", rule_type=RuleType.DAILY_LOSS_LIMIT,
        enabled=True, severity=RuleSeverity.TERMINATION, source=SYNTHETIC_SOURCE,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.DAILY_LOSS_LIMIT),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name=RULE_DAY.timezone_name,
        limit=_pct(LimitBasis.PERCENT_START_OF_DAY_BALANCE, 5.0),
        reset_policy=DailyResetPolicy.CALENDAR_DAY, reset_time=time(0, 0),
        breach_kind=BreachKind.HARD, breach_persists_until_reset=True,
        pnl_components=PnLComponents(
            include_closed_pnl=True, include_floating_pnl=False,
            include_commission=True, include_swap=True, include_fees=True,
        ),
    )


def trailing_daily_loss_rule() -> DailyLossRule:
    """PACK_TRAILING daily loss: 4% of SOD balance INCLUDING floating P&L."""
    return DailyLossRule(
        rule_id="pack_trailing.daily_loss", rule_type=RuleType.DAILY_LOSS_LIMIT,
        enabled=True, severity=RuleSeverity.TERMINATION,
        source=SYNTHETIC_SOURCE_TRAILING, effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.DAILY_LOSS_LIMIT),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name=RULE_DAY.timezone_name,
        limit=_pct(LimitBasis.PERCENT_START_OF_DAY_BALANCE, 4.0),
        reset_policy=DailyResetPolicy.CALENDAR_DAY, reset_time=time(0, 0),
        breach_kind=BreachKind.HARD, breach_persists_until_reset=True,
        pnl_components=PnLComponents(
            include_closed_pnl=True, include_floating_pnl=True,
            include_commission=True, include_swap=True, include_fees=True,
        ),
    )


def static_max_loss_rule() -> DrawdownRule:
    """PACK_STATIC static maximum loss: 10% of the INITIAL BALANCE anchor."""
    return DrawdownRule(
        rule_id="pack_static.max_loss", rule_type=RuleType.STATIC_DRAWDOWN,
        enabled=True, severity=RuleSeverity.TERMINATION, source=SYNTHETIC_SOURCE,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.STATIC_DRAWDOWN),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name=RULE_DAY.timezone_name, kind=DrawdownKind.STATIC,
        limit=_pct(LimitBasis.PERCENT_INITIAL_BALANCE, 10.0),
        anchor=LimitBasis.PERCENT_INITIAL_BALANCE,
    )


def trailing_dd_rule() -> DrawdownRule:
    """PACK_TRAILING trailing max DD: 8% of HIGH-WATER EQUITY."""
    return DrawdownRule(
        rule_id="pack_trailing.max_loss", rule_type=RuleType.TRAILING_DRAWDOWN,
        enabled=True, severity=RuleSeverity.TERMINATION,
        source=SYNTHETIC_SOURCE_TRAILING, effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.TRAILING_DRAWDOWN),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name=RULE_DAY.timezone_name, kind=DrawdownKind.TRAILING,
        limit=_pct(LimitBasis.PERCENT_HIGH_WATER_EQUITY, 8.0),
        anchor=LimitBasis.PERCENT_HIGH_WATER_EQUITY,
        trail_reference="EQUITY", trail_update_cadence="CONTINUOUS",
        trail_locks_at_breach=True,
    )


def static_profit_target_rule() -> ProfitTargetRule:
    """PACK_STATIC profit target: 8% of initial balance, min 5 trading days."""
    return ProfitTargetRule(
        rule_id="pack_static.profit_target", rule_type=RuleType.PROFIT_TARGET,
        enabled=True, severity=RuleSeverity.ADVISORY, source=SYNTHETIC_SOURCE,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.PROFIT_TARGET),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
        timezone_name=RULE_DAY.timezone_name,
        limit=_pct(LimitBasis.PERCENT_INITIAL_BALANCE, 8.0),
        counts_floating_profit=False, minimum_days_required=5,
        # The profit target's min-day dependency is only decidable when the
        # qualifying criterion is declared, exactly as 3B requires. Declaring
        # it here is explicit, never inferred.
        unknown_payload={"qualifying_criterion": "ANY_CLOSED_TRADE"},
    )


def static_min_days_rule() -> TradingDayRule:
    """PACK_STATIC minimum trading days: 5, any closed trade qualifies."""
    return TradingDayRule(
        rule_id="pack_static.min_trading_days", rule_type=RuleType.MIN_TRADING_DAYS,
        enabled=True, severity=RuleSeverity.WARNING, source=SYNTHETIC_SOURCE,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.MIN_TRADING_DAYS),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
        timezone_name=RULE_DAY.timezone_name,
        limit=Limit(basis=LimitBasis.TRADING_DAYS, value=5.0),
        day_count_basis=DayCountBasis.TRADING_DAYS,
        qualifying_criterion="ANY_CLOSED_TRADE",
    )


def static_open_risk_rule(*, pct: float = 3.0) -> OpenRiskLimitRule:
    """PACK_STATIC max open risk: 3% of current equity."""
    return OpenRiskLimitRule(
        rule_id="pack_static.max_open_risk", rule_type=RuleType.MAX_OPEN_RISK,
        enabled=True, severity=RuleSeverity.BREACH, source=SYNTHETIC_SOURCE,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.MAX_OPEN_RISK),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name=RULE_DAY.timezone_name,
        limit=_pct(LimitBasis.PERCENT_CURRENT_EQUITY, pct), scope_kind="TOTAL",
    )


def static_position_limit_rule(*, count: int = 5) -> PositionLimitRule:
    """PACK_STATIC maximum open positions: 5."""
    return PositionLimitRule(
        rule_id="pack_static.max_positions",
        rule_type=RuleType.MAX_OPEN_POSITIONS, enabled=True,
        severity=RuleSeverity.BREACH, source=SYNTHETIC_SOURCE,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.MAX_OPEN_POSITIONS),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name=RULE_DAY.timezone_name,
        limit=Limit(basis=LimitBasis.POSITION_COUNT, value=float(count)),
    )


def static_weekend_allowed_rule() -> HoldRestrictionRule:
    """PACK_STATIC explicitly PERMITS weekend holding (absence is declared)."""
    return HoldRestrictionRule(
        rule_id="pack_static.weekend_hold",
        rule_type=RuleType.WEEKEND_HOLD_RESTRICTION, enabled=True,
        severity=RuleSeverity.ADVISORY, source=SYNTHETIC_SOURCE,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(
            RuleType.WEEKEND_HOLD_RESTRICTION),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name=RULE_DAY.timezone_name,
        kind=HoldRestrictionKind.WEEKEND_HOLD, holding_permitted=True,
    )


def trailing_weekend_close_rule(*, must_close: bool = True) -> HoldRestrictionRule:
    """PACK_TRAILING mandatory weekend close at a governed 20:00 NY cutoff."""
    return HoldRestrictionRule(
        rule_id="pack_trailing.weekend_hold",
        rule_type=RuleType.WEEKEND_HOLD_RESTRICTION, enabled=True,
        severity=RuleSeverity.TERMINATION, source=SYNTHETIC_SOURCE_TRAILING,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(
            RuleType.WEEKEND_HOLD_RESTRICTION),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name=RULE_DAY.timezone_name,
        kind=HoldRestrictionKind.WEEKEND_HOLD, holding_permitted=False,
        must_close_before_market_close=must_close, cutoff_time=time(20, 0),
    )


def trailing_news_rule() -> NewsTradingRestrictionRule:
    """PACK_TRAILING news blackout requiring a real external calendar."""
    return NewsTradingRestrictionRule(
        rule_id="pack_trailing.news",
        rule_type=RuleType.NEWS_TRADING_RESTRICTION, enabled=True,
        severity=RuleSeverity.BREACH, source=SYNTHETIC_SOURCE_TRAILING,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(
            RuleType.NEWS_TRADING_RESTRICTION),
        evaluation_time_basis=EvaluationTimeBasis.EVENT_DRIVEN,
        timezone_name=RULE_DAY.timezone_name,
        blackouts_high_impact=True, importance_levels=(NewsImportance.HIGH,),
        pre_event_blackout_minutes=2, post_event_blackout_minutes=2,
        prohibit_opening_trades=True, prohibit_closing_trades=False,
        prohibit_holding_through_event=True, requires_economic_calendar=True,
    )


def trailing_correlation_rule(*, pct: float = 1.5) -> OpenRiskLimitRule:
    """PACK_TRAILING correlated-cluster risk limit (% of current equity)."""
    return OpenRiskLimitRule(
        rule_id="pack_trailing.max_correlated_risk",
        rule_type=RuleType.MAX_CORRELATED_RISK, enabled=True,
        severity=RuleSeverity.BREACH, source=SYNTHETIC_SOURCE_TRAILING,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.MAX_CORRELATED_RISK),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name=RULE_DAY.timezone_name,
        limit=_pct(LimitBasis.PERCENT_CURRENT_EQUITY, pct),
        scope_kind="CORRELATION_CLUSTER", correlation_cluster_scoped=True,
    )


def trailing_directional_rule(*, pct: float = 3.0) -> OpenRiskLimitRule:
    """PACK_TRAILING same-direction exposure limit (% of current equity)."""
    return OpenRiskLimitRule(
        rule_id="pack_trailing.max_directional_risk",
        rule_type=RuleType.MAX_DIRECTIONAL_RISK, enabled=True,
        severity=RuleSeverity.BREACH, source=SYNTHETIC_SOURCE_TRAILING,
        effective_from=PACK_EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.MAX_DIRECTIONAL_RISK),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name=RULE_DAY.timezone_name,
        limit=_pct(LimitBasis.PERCENT_CURRENT_EQUITY, pct),
        scope_kind="DIRECTIONAL", direction_scoped=True,
    )


def build_pack_static(*, version="1.0.0", phase=RulePhase.EVALUATION_PHASE_1,
                     effective_from=PACK_EFFECTIVE_FROM, effective_to=None,
                     max_open_risk_pct=3.0, max_positions=5) -> RulePack:
    """PACK_STATIC: $25,000 USD, 5% daily, 10% static max loss, 8% target.

    Explicitly declares weekend holding ALLOWED and declares NO news restriction,
    which is itself the explicit statement that news trading is allowed.
    """
    return compile_rule_pack(
        _static_identity(version=version, phase=phase,
                         effective_from=effective_from,
                         effective_to=effective_to),
        [
            static_daily_loss_rule(), static_max_loss_rule(),
            static_profit_target_rule(), static_min_days_rule(),
            static_open_risk_rule(pct=max_open_risk_pct),
            static_position_limit_rule(count=max_positions),
            static_weekend_allowed_rule(),
        ],
        notes="SYNTHETIC PACK_STATIC -- Block 3D acceptance, not a real firm.",
    )


def build_pack_trailing(*, version="1.0.0", phase=RulePhase.EVALUATION_PHASE_1,
                        effective_from=PACK_EFFECTIVE_FROM,
                        max_open_risk_pct=2.0, weekend_must_close=True,
                        include_news=True, include_correlation=True,
                        include_directional=True, correlated_pct=1.5,
                        directional_pct=3.0) -> RulePack:
    """PACK_TRAILING: $25,000 USD, 4% daily incl. floating, 8% trailing DD."""
    rules = [
        trailing_daily_loss_rule(), trailing_dd_rule(),
        static_open_risk_rule(pct=max_open_risk_pct), static_min_days_rule(),
        trailing_weekend_close_rule(must_close=weekend_must_close),
    ]
    if include_correlation:
        rules.append(trailing_correlation_rule(pct=correlated_pct))
    if include_directional:
        rules.append(trailing_directional_rule(pct=directional_pct))
    if include_news:
        rules.append(trailing_news_rule())
    return compile_rule_pack(
        _trailing_identity(version=version, phase=phase,
                           effective_from=effective_from), rules,
        notes="SYNTHETIC PACK_TRAILING -- Block 3D acceptance, not a real firm.",
    )


# ==========================================================================
# DETERMINISTIC CLOCK
# ==========================================================================


class SimulationClock:
    """The ONE injected time source. No wall clock is ever read.

    Every advance is explicit: minutes, hours, rule days, or a DST-aware step.
    One cycle is always one frozen instant.
    """

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("SIMULATION_CLOCK_REQUIRES_AWARE_INSTANT")
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    @property
    def now(self) -> datetime:
        return self._now

    def set(self, moment: datetime) -> datetime:
        if moment.tzinfo is None:
            raise ValueError("SIMULATION_CLOCK_REQUIRES_AWARE_INSTANT")
        self._now = moment
        return self._now

    def advance(self, *, minutes=0, hours=0, days=0, seconds=0) -> datetime:
        self._now = self._now + timedelta(minutes=minutes, hours=hours,
                                         days=days, seconds=seconds)
        return self._now

    def advance_to_next_rule_day(self) -> datetime:
        """Advance to midday on the exact next governed rule day."""
        current = RULE_DAY.rule_day_for(self._now)
        self._now = (RULE_DAY.day_start_utc(current + timedelta(days=1))
                     + timedelta(hours=17))
        return self._now

    def advance_to_dst_boundary(self) -> datetime:
        """Advance just past the 2026 US spring-forward transition."""
        return self.set(datetime(2026, 3, 8, 7, 30, tzinfo=UTC))


# ==========================================================================
# DETERMINISTIC SIMULATED ACCOUNTS
# ==========================================================================


@dataclass(frozen=True)
class SimulatedPosition:
    """One explicit, replayable open position."""

    ticket: int
    symbol: str
    side: str = "BUY"
    volume: float = 0.10
    open_price: float = 1.1000
    current_price: float = 1.1000
    stop_loss: float = 0.0
    take_profit: float = 0.0

    @property
    def protected(self) -> bool:
        return self.stop_loss > 0.0

    def broker_row(self, *, at_utc: datetime) -> dict:
        """The broker-shaped row the REAL 2B producer consumes."""
        return {
            "ticket": int(self.ticket), "symbol": str(self.symbol),
            "type": 0 if str(self.side).upper() == "BUY" else 1,
            "volume": float(self.volume), "price_open": float(self.open_price),
            "price": float(self.current_price), "sl": float(self.stop_loss),
            "tp": float(self.take_profit), "magic": 713001, "comment": "sim",
            "time": int(at_utc.timestamp()),
            "time_msc": int(at_utc.timestamp() * 1000),
            "order": int(self.ticket), "profit": 0.0,
        }


@dataclass
class SimulatedAccount:
    """One exact simulated account with fully explicit, replayable state."""

    account_id: str
    broker: str
    server: str
    login: int
    currency: str
    initial_balance: float
    balance: float
    equity: float
    floating_pnl: float = 0.0
    positions: tuple = ()
    #: Net realised P&L for the CURRENT rule day. Injected as explicit closed-trade
    #: events so 3B's own ledger projection does the summing, never this module.
    realised_pnl: float = 0.0
    commission: float = 0.0
    swap: float = 0.0
    fees: float = 0.0
    #: Broker observation boundary failure switches (mocked 2A / 2B boundaries).
    account_source_down: bool = False
    position_source_down: bool = False
    #: When True, positions are reported with NO stop loss (unprotected risk).
    strip_stops: bool = False
    #: True once a realised figure has already been reflected in ``balance``.
    realised_applied: bool = False

    @property
    def identity(self) -> AccountIdentity:
        return AccountIdentity(account_id=self.account_id, broker=self.broker,
                               server=self.server, login=self.login)

    @property
    def account_key(self) -> AccountKey:
        return AccountKey(self.account_id, self.broker, self.server, self.login)

    @property
    def tickets(self) -> tuple:
        return tuple(int(p.ticket) for p in self.positions)

    def position_for(self, ticket: int):
        for position in self.positions:
            if int(position.ticket) == int(ticket):
                return position
        return None

    def with_positions(self, positions: Iterable[SimulatedPosition]):
        return replace(self, positions=tuple(positions))

    def with_money(self, *, balance=None, equity=None, floating=None):
        return replace(
            self,
            balance=self.balance if balance is None else float(balance),
            equity=self.equity if equity is None else float(equity),
            floating_pnl=self.floating_pnl if floating is None else float(floating),
        )

    def with_realised(self, net: float, *, applied: bool = True):
        """Record this rule day's realised net P&L (explicit fixture, never random)."""
        return replace(self, realised_pnl=float(net), realised_applied=bool(applied))


def account_a(**overrides) -> SimulatedAccount:
    """ACCOUNT_A: compliant baseline, $25,000 USD, no open positions."""
    base = dict(account_id="ACC_A", broker="MT5", server="SimBroker-Server",
                login=111111, currency="USD", initial_balance=25_000.0,
                balance=25_000.0, equity=25_000.0)
    base.update(overrides)
    return SimulatedAccount(**base)


def account_b(**overrides) -> SimulatedAccount:
    """ACCOUNT_B: second exact identity for fan-out and isolation proofs."""
    base = dict(account_id="ACC_B", broker="MT5", server="SimBroker-Server",
                login=222222, currency="USD", initial_balance=25_000.0,
                balance=25_000.0, equity=25_000.0)
    base.update(overrides)
    return SimulatedAccount(**base)


# ==========================================================================
# MOCKED EXTERNAL BOUNDARIES
# ==========================================================================


class SimulatedAccountSource:
    """Mocked 2A broker account observation. The REAL 2A producer validates it."""

    source_name = "SIM_BROKER_ACCOUNT_INFO"

    def __init__(self, account: SimulatedAccount) -> None:
        self._account = account

    def read_account_info(self) -> dict:
        if self._account.account_source_down:
            from core.risk.account_snapshot import AccountSourceUnavailable
            raise AccountSourceUnavailable("SIMULATED_ACCOUNT_SOURCE_DOWN")
        a = self._account
        return {
            "login": a.login, "server": a.server, "balance": float(a.balance),
            "equity": float(a.equity), "profit": float(a.floating_pnl),
            "credit": 0.0, "currency": a.currency, "margin": 0.0,
            "margin_free": float(a.equity), "margin_level": 0.0,
            "leverage": 100, "trade_allowed": True, "trade_expert": True,
            "trade_mode": 0, "margin_mode": 0,
        }


class SimulatedPositionSource:
    """Mocked 2B broker position observation (rows stamped with the cycle instant)."""

    def __init__(self, account: SimulatedAccount, *, at_utc=None) -> None:
        self._account = account
        self._at = at_utc

    def set_instant(self, at_utc: datetime) -> None:
        self._at = at_utc

    def read_positions(self) -> list:
        a = self._account
        if a.position_source_down:
            from core.risk.position_snapshot import PositionSetUnavailable
            raise PositionSetUnavailable("SIMULATED_POSITION_SOURCE_DOWN")
        instant = self._at or PACK_EFFECTIVE_FROM
        rows = []
        for position in a.positions:
            sl = 0.0 if a.strip_stops else float(position.stop_loss)
            rows.append(replace(position, stop_loss=sl).broker_row(
                at_utc=instant))
        return rows


class SimulatedSymbolSpecSource:
    """Deterministic broker symbol specification (a mocked external boundary)."""

    source_name = "SIM_SYMBOL_SPEC"

    def read_symbol_spec(self, broker_symbol: str, *a, **k) -> dict:
        return {
            "digits": 5, "point": 0.00001, "trade_tick_size": 0.00001,
            "trade_tick_value": 1.0, "trade_tick_value_profit": 1.0,
            "trade_tick_value_loss": 1.0, "trade_contract_size": 100_000.0,
            "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
            "trade_mode": 4, "trade_calc_mode": 0, "pip_size": 0.0001,
        }

    def read(self, broker_symbol: str, *a, **k) -> dict:
        return self.read_symbol_spec(broker_symbol)


class SimulatedProfitCalculator:
    """Exact, deterministic broker risk maths (a mocked external boundary).

    A 100-point (0.00100) stop on 0.10 lots is exactly 100.00 account-currency
    units, so every position's monetary risk is an exact, replayable number
    and never an estimate from pips.
    """

    VALUE_PER_POINT_PER_LOT = 1.0

    def calc_profit(self, *, side, broker_symbol, volume,
                    open_price, close_price) -> float:
        direction = 1.0 if str(side).upper() == "BUY" else -1.0
        points = (float(close_price) - float(open_price)) * 100_000.0
        return round(direction * points * float(volume)
                     * self.VALUE_PER_POINT_PER_LOT, 8)


class ScriptedCalendarProvider:
    """Deterministic economic calendar with explicit, scriptable freshness.

    ``STALE`` and ``UNAVAILABLE`` are real states here, never "no events".
    """

    def __init__(self, events=(), *, freshness=SourceFreshness.FRESH,
                 max_age_seconds=900, provider_name="SIM_ECONOMIC_CALENDAR",
                 raise_on_fetch=False) -> None:
        self._events = tuple(events)
        self._freshness = freshness
        self._max_age = max_age_seconds
        self._name = provider_name
        self._raise = raise_on_fetch

    def set_events(self, events) -> None:
        self._events = tuple(events)

    def set_freshness(self, freshness) -> None:
        self._freshness = freshness

    def fetch(self, *, start_utc, end_utc) -> EconomicCalendarSnapshot:
        if self._raise:
            raise SourceError("SIMULATED_CALENDAR_DOWN")
        return EconomicCalendarSnapshot(
            provenance=SourceProvenance(
                provider_name=self._name,
                source_reference=f"{self._name}://scripted",
                retrieved_at_utc=end_utc, freshness=self._freshness,
                max_age_seconds=self._max_age,
                detail="SYNTHETIC TEST CALENDAR"),
            events=self._events)


class ScriptedSessionProvider:
    """Deterministic market-session answers (a mocked external boundary)."""

    def __init__(self, *, state=MarketState.OPEN,
                 freshness=SourceFreshness.FRESH, closed_weekdays=(5, 6),
                 close_local_time=time(20, 0),
                 provider_name="SIM_MARKET_SESSION",
                 raise_on_query=False) -> None:
        self._state = state
        self._freshness = freshness
        self._closed_weekdays = tuple(closed_weekdays)
        self._close = close_local_time
        self._name = provider_name
        self._raise = raise_on_query

    def set_freshness(self, freshness) -> None:
        self._freshness = freshness

    def set_state(self, state) -> None:
        self._state = state

    def state_for(self, *, canonical_symbol, at_utc) -> MarketSessionAnswer:
        if self._raise:
            raise SourceError("SIMULATED_SESSION_SOURCE_DOWN")
        session = MarketSession(
            session_id="sim_session", canonical_symbol=canonical_symbol or "*",
            exchange_calendar="SIM", timezone_name=RULE_DAY.timezone_name,
            close_local_time=self._close,
            trading_weekdays=tuple(d for d in range(7)
                                   if d not in self._closed_weekdays),
            holiday_dates=())
        state = self._state
        if state is MarketState.OPEN:
            state = session.state_at(at_utc)
        return MarketSessionAnswer(
            provenance=SourceProvenance(
                provider_name=self._name,
                source_reference=f"{self._name}://scripted",
                retrieved_at_utc=at_utc, freshness=self._freshness,
                max_age_seconds=900,
                detail="SYNTHETIC TEST SESSION CALENDAR"),
            state=state, session=session)


class RecordingBroker:
    """The mocked broker TRANSPORT: records every action, contacts nothing.

    Exact ``(account_id, broker, server, login, ticket)`` identity is preserved on
    every recorded call, so a same-ticket cross-account collision is detectable.
    """

    def __init__(self, *, close_outcomes=None,
                 default_close_outcome=CloseOutcome.CLOSED) -> None:
        self.attempted_entries = []
        self.blocked_entries = []
        self.accepted_entries = []
        self.close_requests = []
        self.close_results = []
        self._close_outcomes = dict(close_outcomes or {})
        self._default = default_close_outcome

    # -- order side ---------------------------------------------------------

    def send_entry(self, *, account, symbol, allowed, block_code,
                   order=None) -> bool:
        """Record one order-send attempt and its exact governed outcome."""
        record = {
            "account_identity": account.identity,
            "account_id": account.account_id,
            "symbol": symbol, "allowed": bool(allowed),
            "block_code": block_code,
            "order": order.identity() if order is not None else None,
        }
        self.attempted_entries.append(record)
        if not allowed:
            self.blocked_entries.append(record)
            return False
        self.accepted_entries.append(record)
        return True

    # -- close side (the PositionClosePort protocol) -------------------------

    def set_close_outcome(self, ticket: int, outcome) -> None:
        self._close_outcomes[int(ticket)] = outcome

    def close_position(self, request, *, at_utc) -> CloseResult:
        self.close_requests.append(request)
        outcome = self._close_outcomes.get(int(request.position_ticket),
                                           self._default)
        result = CloseResult(request=request, outcome=outcome,
                             detail=f"SIMULATED_{outcome.value}",
                             attempted_at_utc=at_utc)
        self.close_results.append(result)
        return result

    @property
    def closed_pairs(self) -> tuple:
        """Exact (account identity, ticket) pairs that reached a close."""
        return tuple((r.request.account.identity, int(r.request.position_ticket))
                     for r in self.close_results if r.is_success)


def planned_order(*, symbol="EURUSD", side="BUY", volume=0.10,
                  entry_price=1.1000, stop_loss=1.0900,
                  risk_amount=None) -> PlannedOrder:
    """A broker-normalised proposed order with EXACT planned risk.

    ``risk_amount`` defaults to the exact risk implied by the stop distance at the
    simulated calculator's rate, so projection is never estimated from pips.
    """
    points = abs(float(entry_price) - float(stop_loss)) * 100_000.0
    exact = round(points * float(volume)
                  * SimulatedProfitCalculator.VALUE_PER_POINT_PER_LOT, 8)
    return PlannedOrder(
        canonical_symbol=symbol, side=side, volume=volume,
        entry_price=entry_price, stop_loss=stop_loss,
        planned_risk_amount=exact if risk_amount is None else risk_amount)


def economic_event(*, event_id, at_utc, importance=NewsImportance.HIGH,
                   currencies=("USD",), source="SIM_CALENDAR") -> EconomicEvent:
    """One explicit, synthetic calendar event."""
    return EconomicEvent(
        event_id=event_id, event_at_utc=at_utc, importance=importance,
        affected_currencies=tuple(currencies),
        affected_symbols=(), name=f"SIM {event_id}")


def pack_store(*packs) -> RulePackStore:
    """A store holding the exact packs under acceptance."""
    store = RulePackStore()
    for pack in packs:
        store.register_rule_pack(pack)
    return store


__all__ = [
    "ACCOUNT_CURRENCY", "ACCOUNT_SIZE", "PACK_EFFECTIVE_FROM", "RULE_DAY",
    "PACK_STATIC_IDENTITY", "RecordingBroker", "ScriptedCalendarProvider",
    "ScriptedSessionProvider", "SimulationClock", "SimulatedAccount",
    "SimulatedAccountSource", "SimulatedPosition", "SimulatedPositionSource",
    "SimulatedProfitCalculator", "SimulatedSymbolSpecSource", "account_a",
    "account_b", "build_pack_static", "build_pack_trailing", "economic_event",
    "pack_store", "planned_order",
]
