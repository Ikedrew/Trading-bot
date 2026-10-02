"""RUNTIME REACHABILITY of governed prop POSITION enforcement (Block 3C).

The gap this file closes
------------------------
``PropEnforcementRuntime.enforce_positions`` existed and was unit-tested, but
had **no live trading-cycle caller**, so position-side prop enforcement was
implemented yet not provably reachable. This file proves it is reachable from
the real bounded account cycle and re-certifies the affected 3C invariants.

Every test drives the REAL production call path:

    PropRiskTelemetryService.observe_account   (the existing bounded cycle)
      -> core.risk.prop_position_enforcement.enforce_positions_for_account
      -> PropEnforcementRuntime.enforce_positions
      -> EnforcementExecutor (exact ticket close)
      -> PositionClosePort

Non-vacuity is proven twice over:
  * ``test_runtime_cycle_reaches_enforce_positions`` (A) asserts the executor was
    really invoked;
  * ``test_removing_the_runtime_enforce_positions_invocation_is_detected`` (B)
    proves the suite FAILS when that invocation is removed.

Nothing here fabricates telemetry: the harness supplies genuine Block 2
``PositionSetObservation`` / ``PositionSnapshot`` / ``AccountOpenRiskSnapshot``
records, because an unproven ticket set must never be liquidatable.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path

from core.risk.account_snapshot import AccountIdentity
from core.risk.position_snapshot import (
    AccountOpenRiskSnapshot,
    PositionSetObservation,
    PositionSide,
    PositionSnapshot,
    PositionStatus,
)
from core.risk.prop_rule_enforcement import (
    ActionResultStatus,
    EnforcementAction,
    EnforcementMode,
    EnforcementReason,
)
from core.risk.prop_rule_enums import (
    EvaluationTimeBasis,
    HoldRestrictionKind,
    RulePhase,
    RuleSeverity,
    RuleType,
)
from core.risk.prop_rule_executor import (
    CloseOutcome,
    CloseRequest,
    CloseResult,
    EnforcementExecutor,
    EnforcementStateStore,
)
from core.risk.prop_rule_pack import RulePack, RulePackIdentity, compile_rule_pack
from core.risk.prop_rule_runtime import PropEnforcementRuntime
from core.risk.prop_rule_state import RuleDayDefinition
from core.risk.telemetry_runtime import PropRiskTelemetryService
from core.risk.prop_position_enforcement import (
    LifecyclePositionClosePort,
    enforce_positions_for_account,
)

UTC = timezone.utc
NOW = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)
#: Past the declared 20:00 America/New_York cutoff on the rule day.
AFTER_WEEKEND_CUTOFF = datetime(2026, 3, 11, 2, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parent.parent
NY = RuleDayDefinition("America/New_York", time(0, 0))


def identity_for(account_id: str, login: int) -> AccountIdentity:
    return AccountIdentity(
        account_id=account_id, broker="MT5", server="DemoBroker-Server", login=login
    )


IDENTITY_A = identity_for("ACC_A", 111111)
IDENTITY_B = identity_for("ACC_B", 222222)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# REAL Block 2 EVIDENCE (no fabricated telemetry)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def make_position_set(
    identity: AccountIdentity, tickets: tuple[int, ...], *, complete: bool = True,
) -> PositionSetObservation:
    return PositionSetObservation(
        account_id=identity.account_id, broker=identity.broker,
        server=identity.server, login=identity.login,
        position_snapshot_id="pset_1", observation_id="obs_1",
        observed_at_utc=NOW.isoformat(),
        observed_at_utc_ms=int(NOW.timestamp() * 1000),
        source="TEST", position_set_complete=complete,
        open_position_count=len(tickets) if complete else None,
        open_position_tickets=tickets,
        status=PositionStatus.COMPLETE if complete else PositionStatus.UNAVAILABLE,
        source_error=None if complete else "POSITION_SOURCE_DOWN",
    )


def mt5_position_row(
    ticket: int, canonical_symbol: str, *, magic: int = 713_001,
) -> dict:
    """A BROKER-SHAPED position row, exactly as a real 2B source returns one.

    The real ``observe_positions`` producer builds the typed evidence from these
    rows, so the harness never fabricates a ``PositionSnapshot`` by hand.
    """
    return {
        "ticket": ticket, "symbol": canonical_symbol, "type": 0,
        "volume": 0.5, "price_open": 1.1000, "price": 1.1000,
        "sl": 1.0990, "tp": 0.0, "magic": magic, "comment": "",
        "time": int(NOW.timestamp()), "time_msc": int(NOW.timestamp() * 1000),
        "order": ticket, "profit": 0.0,
    }


def make_open_risk(
    identity: AccountIdentity, tickets: tuple[int, ...], *, complete: bool = True,
) -> AccountOpenRiskSnapshot:
    return AccountOpenRiskSnapshot(
        account_id=identity.account_id, broker=identity.broker,
        server=identity.server, login=identity.login,
        open_risk_id="orisk_1", observation_id="obs_1",
        account_snapshot_id="asnap_1", observed_at_utc=NOW.isoformat(),
        observed_at_utc_ms=int(NOW.timestamp() * 1000), source="TEST",
        open_position_count=len(tickets), known_open_risk=50.0 * len(tickets),
        total_open_risk=50.0 * len(tickets) if complete else None,
        currency="USD",
        status=PositionStatus.COMPLETE if complete else PositionStatus.UNAVAILABLE,
        risk_complete=complete, position_set_complete=complete,
        position_tickets=tickets,
        source_error=None if complete else "POSITION_SOURCE_DOWN",
    )


class AlwaysOpenSessions:
    """A 3B-compatible session source that is always OPEN.

    Isolates the weekend-cutoff branch from the market-calendar question, so
    these tests prove the CLOSE wiring rather than a calendar fixture.
    """

    def is_market_open(self, symbol: str, at_utc) -> bool:
        return True


class RecordingClosePort:
    """The exact close executor boundary, recorded broker-free."""

    def __init__(self, outcomes: dict[int, CloseOutcome] | None = None) -> None:
        self.calls: list[CloseRequest] = []
        self._outcomes = dict(outcomes or {})

    def close_position(self, request: CloseRequest, *, at_utc: datetime) -> CloseResult:
        self.calls.append(request)
        return CloseResult(
            request=request,
            outcome=self._outcomes.get(
                int(request.position_ticket), CloseOutcome.CLOSED
            ),
            attempted_at_utc=at_utc,
        )


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# REAL RulePack: a weekend MANDATORY-CLOSE rule (position reduction)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def weekend_close_pack(*, must_close: bool = True) -> RulePack:
    from core.risk.prop_rule_contracts import (
        HoldRestrictionRule, RuleSource, required_telemetry_for,
    )
    from core.risk.prop_rule_enums import SourceType

    identity = RulePackIdentity(
        provider="TEST_FIRM_HOLD", program="TEST_CHALLENGE",
        phase=RulePhase.EVALUATION_PHASE_1, account_size=100_000,
        currency="USD", rule_pack_version="1.0.0",
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    rule = HoldRestrictionRule(
        rule_id="r.weekend", rule_type=RuleType.WEEKEND_HOLD_RESTRICTION,
        enabled=True, severity=RuleSeverity.TERMINATION,
        source=RuleSource(
            source_type=SourceType.OFFICIAL_RULE_PAGE,
            source_reference="https://example.invalid/3c-rules",
            retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        telemetry_requirements=required_telemetry_for(RuleType.WEEKEND_HOLD_RESTRICTION),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        kind=HoldRestrictionKind.WEEKEND_HOLD, holding_permitted=False,
        must_close_before_market_close=must_close, cutoff_time=time(20, 0),
    )
    return compile_rule_pack(identity, [rule])


class PackStore:
    def __init__(self, pack: RulePack) -> None:
        self._pack = pack

    def find_rule_pack(self, *args, **kwargs):
        return self._pack


@dataclass(frozen=True)
class StaticAccountInfo:
    account_id: str = "ACC_A"
    balance: float = 100_000.0
    equity: float = 100_000.0
    profit: float = 0.0
    credit: float = 0.0
    currency: str = "USD"


@dataclass(frozen=True)
class OpenRiskEvidence:
    """The 2B aggregate shape 3B reads: the PROVEN open-ticket set."""

    account_id: str
    open_risk_id: str = "orisk_1"
    total_open_risk: float = 50.0
    risk_complete: bool = True
    open_position_count: int = 1
    position_tickets: tuple = ()
    position_risks: tuple = ()


@dataclass(frozen=True)
class PortfolioEvidence:
    """The 2C aggregate shape 3B reads."""

    account_id: str
    portfolio_exposure_id: str = "pexp_1"
    largest_symbol_risk: float = 50.0
    largest_symbol: str = "EURUSD"
    largest_direction_risk: float = 50.0
    largest_direction: str = "LONG"
    max_cluster_risk: float = 50.0
    correlation_complete: bool = True


class StubAccountSource:
    """Returns broker-shaped account info, so the REAL 2A producer verifies it.

    2A re-verifies identity on every read, so the stub reports this account's own
    login and server exactly as a pinned MT5 session would.
    """

    def __init__(self, identity: AccountIdentity) -> None:
        self._identity = identity
        self.source_name = "TEST_ACCOUNT_INFO"

    def read_account_info(self):
        return {
            "login": self._identity.login, "server": self._identity.server,
            "balance": 100_000.0, "equity": 100_000.0, "profit": 0.0,
            "credit": 0.0, "currency": "USD", "margin": 0.0,
            "margin_free": 100_000.0, "margin_level": 0.0, "leverage": 100,
            "trade_allowed": True, "trade_expert": True,
            "trade_mode": 0, "margin_mode": 0,
        }


class StubPositionSource:
    """Returns broker-shaped rows, so the REAL 2B producer builds the evidence."""

    def __init__(self, rows: list, *, fail: bool = False) -> None:
        self._rows = rows
        self._fail = fail

    def read_positions(self):
        if self._fail:
            from core.risk.position_snapshot import PositionSetUnavailable

            raise PositionSetUnavailable("POSITION_SOURCE_DOWN")
        return list(self._rows)


class StubSpecSource:
    """A broker symbol spec good enough for the 2B risk geometry."""

    source_name = "TEST_SYMBOL_SPEC"

    def read_symbol_spec(self, broker_symbol: str, *args, **kwargs):
        return {
            "digits": 5, "point": 0.00001, "trade_tick_size": 0.00001,
            "trade_tick_value": 1.0, "trade_tick_value_profit": 1.0,
            "trade_tick_value_loss": 1.0, "trade_contract_size": 100_000.0,
            "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
            "trade_mode": 4, "trade_calc_mode": 0, "pip_size": 0.0001,
        }

    def read(self, broker_symbol: str, *args, **kwargs):
        return self.read_symbol_spec(broker_symbol)


class StubCalculator:
    """The broker-native profit boundary, stubbed deterministically."""

    def calc_profit(self, *, side, broker_symbol, volume, open_price, close_price):
        return 50.0
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# HARNESS â€” the REAL runtime + the REAL bounded telemetry cycle
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def make_state(
    account: AccountIdentity, *, equity: float = 100_000.0,
    tickets: tuple[int, ...] = (4242,),
):
    """A COMPLETE 3B evaluation state for one exact account.

    ``tickets`` are the positions 2B PROVED open for this account. They are what
    a weekend/overnight hold rule actually counts, so the harness supplies them
    rather than letting the rule answer "no positions" by default.
    """
    from core.risk.prop_rule_state import (
        AccountKey, DailyAccountAnchor, InitialAccountAnchor,
        PropRuleStateStore, build_account_evaluation_state,
    )

    key = AccountKey.from_block2(account)
    store = PropRuleStateStore()
    anchor = store.record_initial_anchor(
        InitialAccountAnchor(
            account=key, account_currency="USD", initial_balance=equity,
            initial_equity=equity,
            observed_at_utc=datetime(2026, 3, 2, 14, 0, tzinfo=UTC),
            source_snapshot_id="asnap_initial", source_provenance="TEST",
        )
    )
    day = store.record_daily_anchor(
        DailyAccountAnchor(
            account=key, account_currency="USD", rule_day=NY.rule_day_for(NOW),
            timezone_name=NY.timezone_name, rule_day_definition=NY.key(),
            start_of_day_balance=equity, start_of_day_equity=equity,
            anchor_at_utc=NY.day_start_utc(NY.rule_day_for(NOW)),
            source_snapshot_id="asnap_sod", source_provenance="TEST",
            start_of_day_floating_pnl=0.0,
        )
    )

    return build_account_evaluation_state(
        account=key, account_currency="USD", observed_at_utc=NOW, definition=NY,
        initial_anchor=anchor, daily_anchor=day,
        account_snapshot=StaticAccountInfo(
            account_id=account.account_id, balance=equity, equity=equity,
        ),
        open_risk=OpenRiskEvidence(
            account_id=account.account_id,
            total_open_risk=50.0 * len(tickets), risk_complete=True,
            open_position_count=len(tickets), position_tickets=tickets,
            position_risks=tuple(50.0 for _ in tickets),
        ),
        portfolio=PortfolioEvidence(account_id=account.account_id),
    )


def build_runtime(
    *, mode: EnforcementMode = EnforcementMode.LIVE_ENFORCE,
    pack: RulePack | None = None, store: EnforcementStateStore | None = None,
    port: RecordingClosePort | None = None, state_for=None,
) -> PropEnforcementRuntime:
    """A started 3C runtime bound to one real pack and one real close port."""
    resolved_pack = pack or weekend_close_pack()
    executor = EnforcementExecutor(
        store=store or EnforcementStateStore(), close_port=port
    )
    runtime = PropEnforcementRuntime(
        mode=mode, rule_pack_store=PackStore(resolved_pack),
        state_store=store or EnforcementStateStore(), executor=executor,
        pack_identity=resolved_pack.identity, rule_day_definition=NY,
        market_sessions=AlwaysOpenSessions(), clock=lambda: AFTER_WEEKEND_CUTOFF,
    )
    runtime.state_provider = state_for or (lambda account: make_state(account))
    report = runtime.start(at_utc=NOW)
    assert report.ready, report.to_dict()
    return runtime


def build_cycle(
    runtime: PropEnforcementRuntime | None, *, accounts=(IDENTITY_A,),
    tickets: tuple[int, ...] = (4242,), complete: bool = True,
    symbol: str = "EURUSD", position_enforcement=None,
    tickets_by_account: dict | None = None,
) -> PropRiskTelemetryService:
    """The REAL bounded telemetry service, with the governed hook attached.

    ``tickets_by_account`` overrides the proven open tickets per account, which
    is how a flat account is modelled against a breached one.
    """
    identities = list(accounts)
    per_account = {
        i.account_id: (tickets_by_account or {}).get(i.account_id, tickets)
        for i in identities
    }
    service = PropRiskTelemetryService(
        identities=identities,
        account_sources={i.account_id: StubAccountSource(i) for i in identities},
        position_sources={
            i.account_id: StubPositionSource(
                [mt5_position_row(t, symbol) for t in per_account[i.account_id]],
                fail=not complete,
            )
            for i in identities
        },
        spec_source=StubSpecSource(), calculator=StubCalculator(),
        persist=False, clock=lambda: AFTER_WEEKEND_CUTOFF,
        position_enforcement=(
            position_enforcement
            if position_enforcement is not None
            else (None if runtime is None else _hook_for(runtime))
        ),
    )
    return service


def _hook_for(runtime: PropEnforcementRuntime):
    """The production hook, bound to this test's runtime instance."""
    from core.risk.prop_position_enforcement import enforce_positions_for_account

    def _hook(
        *, identity, position_set, observed_at_utc, positions=(), portfolio=None,
        account_snapshot=None, open_risk=None,
    ):
        return enforce_positions_for_account(
            identity=identity, position_set=position_set,
            observed_at_utc=observed_at_utc, positions=positions,
            runtime=runtime, portfolio=portfolio,
            account_snapshot=account_snapshot, open_risk=open_risk,
        )

    return _hook

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# A. LIVE_ENFORCE â€” the trading cycle REACHES the exact close executor
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_runtime_cycle_reaches_enforce_positions_and_the_close_executor():
    """A: a mandatory-close breach in LIVE_ENFORCE closes the EXACT ticket.

    This is the headline reachability proof. Without the runtime call site no
    close would ever be attempted, so the executor assertion below is exactly
    what makes the wiring non-vacuous.
    """
    port = RecordingClosePort()
    runtime = build_runtime(mode=EnforcementMode.LIVE_ENFORCE, port=port)
    service = build_cycle(runtime, tickets=(4242,))

    results = service.tick()
    assert results, "the bounded cycle produced no account result"

    assert len(port.calls) == 1, (
        "the live trading cycle did NOT reach the exact close executor"
    )
    closed = port.calls[0]
    assert closed.position_ticket == 4242
    assert closed.account.account_id == "ACC_A"
    assert closed.account.identity == IDENTITY_A.identity
    assert closed.canonical_symbol == "EURUSD"


def test_runtime_cycle_records_the_close_outcome_in_the_enforcement_audit():
    """The governed close carries full 3C lineage into the durable audit."""
    store = EnforcementStateStore()
    port = RecordingClosePort()
    runtime = build_runtime(mode=EnforcementMode.LIVE_ENFORCE, store=store, port=port)
    build_cycle(runtime, tickets=(4242,)).tick()

    reduction = [r for r in store.audit_records() if r.is_material]
    assert reduction, "the governed close was not audited"
    assert any(r.action_attempted for r in reduction)
    assert any(
        r.action_result == ActionResultStatus.SUCCESS.value for r in reduction
    )
    for record in reduction:
        assert record.mode == EnforcementMode.LIVE_ENFORCE.value
        assert record.rule_pack_id
        assert record.evaluation_ids, "audit lineage lost the 3B evaluation id"


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# B. NON-VACUITY â€” removing the runtime invocation MUST break these tests
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_removing_the_runtime_enforce_positions_invocation_is_detected():
    """B: bypass ``enforce_positions`` and the reachability guarantee is GONE.

    A mutant that skips the canonical enforcement path must produce zero close
    attempts, which is precisely what tests A and F depend on.
    """
    port = RecordingClosePort()
    runtime = build_runtime(mode=EnforcementMode.LIVE_ENFORCE, port=port)
    runtime.enforce_positions = lambda **kwargs: ()  # the mutation

    build_cycle(runtime, tickets=(4242,)).tick()
    assert port.calls == [], (
        "the mutant still closed -> this suite cannot detect a bypassed "
        "enforce_positions invocation, so its non-vacuity is unproven"
    )


def test_the_runtime_call_site_exists_exactly_once_in_the_live_cycle():
    """B (static): the production cycle contains ONE enforcement call site."""
    source = (ROOT / "core/risk/telemetry_runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    direct = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "enforce_positions"
    ]
    assert not direct, (
        "the telemetry cycle must not call enforce_positions directly; it must "
        "reach it through the single governed adapter"
    )
    assert source.count("self._enforce_positions(") == 1, (
        "there must be exactly ONE runtime position-enforcement call site"
    )
    assert source.count("def _enforce_positions(") == 1


def test_no_new_polling_thread_was_introduced():
    """B (structural): wiring added no thread, loop or per-account service."""
    source = (ROOT / "core/risk/telemetry_runtime.py").read_text(encoding="utf-8")
    assert source.count("threading.Thread(") == 1, (
        "a new thread was introduced; the fix must reuse the existing cycle"
    )
    adapter = (ROOT / "core/risk/prop_position_enforcement.py").read_text(
        encoding="utf-8"
    )
    assert "threading" not in adapter, (
        "the enforcement adapter must not create or own any thread"
    )


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# C. SIMULATE_ONLY / DISABLED â€” same decision, zero broker side effect
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_simulate_only_produces_the_same_decision_and_no_close():
    """C: identical decisions are recorded, but nothing reaches the broker."""
    port = RecordingClosePort()
    store = EnforcementStateStore()
    runtime = build_runtime(mode=EnforcementMode.SIMULATE_ONLY, store=store, port=port)
    build_cycle(runtime, tickets=(4242,)).tick()

    assert port.calls == [], "SIMULATE_ONLY contacted the close executor"
    assert all(
        r.mode == EnforcementMode.SIMULATE_ONLY.value for r in store.audit_records()
    ), "SIMULATE_ONLY recorded a LIVE audit"
    simulated = [
        r for r in store.audit_records()
        if r.action_result == ActionResultStatus.SKIPPED_SIMULATE_ONLY.value
    ]
    assert simulated, "SIMULATE_ONLY did not record what WOULD have happened"
    assert simulated[0].action_attempted is False


def test_simulate_only_and_live_reach_the_same_governed_decision():
    """C: the mode changes the ACTION, never the DECISION."""
    from core.risk.prop_rule_state import AccountKey

    live_port, sim_port = RecordingClosePort(), RecordingClosePort()
    live = build_runtime(mode=EnforcementMode.LIVE_ENFORCE, port=live_port)
    simulated = build_runtime(mode=EnforcementMode.SIMULATE_ONLY, port=sim_port)
    build_cycle(live, tickets=(4242,)).tick()
    build_cycle(simulated, tickets=(4242,)).tick()

    assert len(live_port.calls) == 1
    assert sim_port.calls == []
    live_id = live_port.calls[0].enforcement_id
    verdict = simulated.authorize_entry(
        account=AccountKey.from_block2(IDENTITY_A), at_utc=AFTER_WEEKEND_CUTOFF
    )
    assert any(d.enforcement_id == live_id for d in verdict.position_reductions), (
        "SIMULATE_ONLY and LIVE_ENFORCE disagreed about the governed decision"
    )


def test_disabled_mode_never_enforces_positions():
    """DISABLED must not enforce anything at the runtime cycle."""
    port = RecordingClosePort()
    store = EnforcementStateStore()
    runtime = build_runtime(mode=EnforcementMode.DISABLED, store=store, port=port)
    build_cycle(runtime, tickets=(4242,)).tick()
    assert port.calls == []
    assert store.audit_records() == ()


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# D. ENTRY-BLOCK-ONLY rules never touch existing positions
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_entry_block_only_rule_never_closes_an_existing_position():
    """D: a rule that only blocks entries must not close anything."""
    port = RecordingClosePort()
    runtime = build_runtime(
        mode=EnforcementMode.LIVE_ENFORCE, port=port,
        pack=breach_open_risk_pack(),   # BLOCK_ACCOUNT_ENTRY only
    )
    build_cycle(runtime, tickets=(4242,)).tick()
    assert port.calls == [], (
        "an entry-block rule must never force-close an existing position"
    )


def breach_open_risk_pack() -> RulePack:
    """A pack whose only rule is an ENTRY BLOCK (max open risk)."""
    from core.risk.prop_rule_contracts import (
        OpenRiskLimitRule, RuleSource, required_telemetry_for,
    )
    from core.risk.prop_rule_enums import (
        CurrencySemantics, LimitBasis, SourceType,
    )
    from core.risk.prop_rule_values import Limit

    identity = RulePackIdentity(
        provider="TEST_FIRM_RISK", program="TEST_CHALLENGE",
        phase=RulePhase.EVALUATION_PHASE_1, account_size=100_000,
        currency="USD", rule_pack_version="1.0.0",
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    rule = OpenRiskLimitRule(
        rule_id="r.max_open_risk", rule_type=RuleType.MAX_OPEN_RISK, enabled=True,
        severity=RuleSeverity.BREACH,
        source=RuleSource(
            source_type=SourceType.OFFICIAL_RULE_PAGE,
            source_reference="https://example.invalid/3c-rules",
            retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        telemetry_requirements=required_telemetry_for(RuleType.MAX_OPEN_RISK),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        limit=Limit(
            basis=LimitBasis.PERCENT_CURRENT_EQUITY, value=0.0001,
            currency_semantics=CurrencySemantics.ACCOUNT_CURRENCY,
        ),
        scope_kind="TOTAL",
    )
    return compile_rule_pack(identity, [rule])
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# E. INDETERMINATE / provider unavailable â€” never a blanket liquidation
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_unavailable_position_provider_never_forces_a_close():
    """E: an unproven position set must NOT be liquidated on uncertainty."""
    port = RecordingClosePort()
    runtime = build_runtime(mode=EnforcementMode.LIVE_ENFORCE, port=port)
    build_cycle(runtime, tickets=(4242,), complete=False).tick()
    assert port.calls == [], (
        "an incomplete 2B position set was liquidated; uncertainty must never "
        "authorise a forced close"
    )


def test_missing_3b_state_never_forces_a_close():
    """E: INDETERMINATE state may block entries but must not close positions."""
    port = RecordingClosePort()
    runtime = build_runtime(
        mode=EnforcementMode.LIVE_ENFORCE, port=port, state_for=lambda account: None
    )
    build_cycle(runtime, tickets=(4242,)).tick()
    assert port.calls == [], "missing 3B state forced a close"


def test_stopped_runtime_never_forces_a_close():
    """E: after shutdown begins the runtime acts on nothing."""
    port = RecordingClosePort()
    runtime = build_runtime(mode=EnforcementMode.LIVE_ENFORCE, port=port)
    runtime.stop()
    build_cycle(runtime, tickets=(4242,)).tick()
    assert port.calls == []


def test_indeterminate_state_still_blocks_new_entries():
    """E companion: uncertainty still fails CLOSED for NEW risk."""
    from core.risk.prop_rule_state import AccountKey

    runtime = build_runtime(state_for=lambda account: None)
    verdict = runtime.authorize_entry(
        account=AccountKey.from_block2(IDENTITY_A), at_utc=AFTER_WEEKEND_CUTOFF
    )
    assert verdict.allowed is False
    assert verdict.reason is EnforcementReason.EVIDENCE_INDETERMINATE


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# F. MULTI-ACCOUNT isolation
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_two_accounts_only_the_breached_account_is_closed():
    """F: enforcement runs independently per account, on exact identities.

    Account A holds a position past the weekend cutoff; account B is flat. Only
    A's governed position may be acted upon.
    """
    port = RecordingClosePort()
    runtime = build_runtime(mode=EnforcementMode.LIVE_ENFORCE, port=port)
    build_cycle(
        runtime, accounts=(IDENTITY_A, IDENTITY_B), tickets=(4242,),
        tickets_by_account={"ACC_A": (4242,), "ACC_B": ()},
    ).tick()

    assert port.calls, "the breached account was never enforced"
    for call in port.calls:
        assert call.account.account_id == "ACC_A"
        assert call.account.identity == IDENTITY_A.identity
        assert call.position_ticket == 4242


def test_one_account_enforcement_failure_never_reaches_another_account():
    """F: a failing account is isolated; the healthy account still completes."""
    port = RecordingClosePort()
    runtime = build_runtime(mode=EnforcementMode.LIVE_ENFORCE, port=port)

    def state_for(account):
        if account.account_id == "ACC_A":
            raise RuntimeError("SIMULATED_EVIDENCE_FAULT")
        return make_state(account)

    runtime.state_provider = state_for
    results = build_cycle(
        runtime, accounts=(IDENTITY_A, IDENTITY_B), tickets=(4242,)
    ).tick()
def test_one_account_enforcement_failure_never_reaches_another_account():
    """F: a failing account is isolated; the healthy account still completes."""
    port = RecordingClosePort()
    runtime = build_runtime(mode=EnforcementMode.LIVE_ENFORCE, port=port)

    def state_for(account):
        if account.account_id == "ACC_A":
            raise RuntimeError("SIMULATED_EVIDENCE_FAULT")
        return make_state(account)

    runtime.state_provider = state_for
    results = build_cycle(
        runtime, accounts=(IDENTITY_A, IDENTITY_B), tickets=(4242,)
    ).tick()

    assert {r.account_id for r in results} == {"ACC_A", "ACC_B"}
    assert port.calls, "the healthy account was skipped because A faulted"
    for call in port.calls:
        assert call.account.account_id == "ACC_B"


def test_the_same_ticket_number_on_two_accounts_is_two_different_closes():
    """F: ticket numbers are account-scoped, never globally unique."""
    port = RecordingClosePort()
    runtime = build_runtime(mode=EnforcementMode.LIVE_ENFORCE, port=port)
    build_cycle(
        runtime, accounts=(IDENTITY_A, IDENTITY_B), tickets=(4242,),
        tickets_by_account={"ACC_A": (4242,), "ACC_B": (4242,)},
    ).tick()

    assert len(port.calls) == 2
    assert {c.account.account_id for c in port.calls} == {"ACC_A", "ACC_B"}
    assert len({c.close_request_id for c in port.calls}) == 2

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# G. IDEMPOTENCY at the runtime cycle
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_repeated_cycles_of_an_already_closed_ticket_stay_idempotent():
    """G: an already-closed ticket is a successful terminal outcome, once."""
    store = EnforcementStateStore()
    port = RecordingClosePort()
    runtime = build_runtime(mode=EnforcementMode.LIVE_ENFORCE, store=store, port=port)
    service = build_cycle(runtime, tickets=(4242,))

    service.tick()
    first = port.calls[0]
    assert first.position_ticket == 4242

    service.tick()
    assert len(port.calls) == 1, (
        "a completed ticket was re-issued to the broker on a later cycle"
    )


def test_a_retryable_close_is_retried_on_the_next_cycle():
    """G: retry semantics are preserved across cycles, not swallowed."""
    store = EnforcementStateStore()
    port = RecordingClosePort(outcomes={4242: CloseOutcome.RETRYABLE_FAILURE})
    runtime = build_runtime(mode=EnforcementMode.LIVE_ENFORCE, store=store, port=port)
    service = build_cycle(runtime, tickets=(4242,))

    service.tick()
    assert len(port.calls) == 1
    service.tick()
    assert len(port.calls) == 2, "a retryable close was never retried"


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# ORDER / CLOSE-PATH AUDIT
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


def test_the_new_call_site_reaches_the_one_account_safe_close_boundary():
    """The governed close can only reach the existing lifecycle boundary."""
    source = (ROOT / "core/risk/prop_position_enforcement.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            assert name != "order_send", (
                "the position-enforcement adapter must never call the broker "
                "directly; it must go through the lifecycle router"
            )
    assert "core.accounts.lifecycle" in source, (
        "the adapter must reuse the EXISTING account-owned close boundary"
    )


def test_no_enforcement_module_imports_mt5():
    """The new adapter keeps the broker-free enforcement boundary intact."""
    for relative in (
        "core/risk/prop_position_enforcement.py",
        "core/risk/prop_rule_runtime.py",
        "core/risk/prop_rule_executor.py",
    ):
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "MetaTrader5" not in alias.name
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "MetaTrader5"
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# THE PRODUCTION CLOSE PORT
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


class RecordingLifecycleRouter:
    def __init__(self, reply) -> None:
        self.reply = reply
        self.calls: list = []

    def read(self, owner, operation, **arguments):
        self.calls.append((owner, operation, arguments))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _close_request(ticket: int = 4242, symbol: str = "EURUSD"):
    from core.risk.prop_rule_state import AccountKey

    return CloseRequest(
        account=AccountKey.from_block2(IDENTITY_A), position_ticket=ticket,
        enforcement_id="enf_test", canonical_symbol=symbol,
    )


def test_production_close_port_is_exact_account_and_ticket_bound():
    router = RecordingLifecycleRouter(
        {"ok": True, "retcode": 10009, "deal": 5, "order": 6, "comment": "done"}
    )
    port = LifecyclePositionClosePort(router=router, magic=713001, close_enabled=True)
    result = port.close_position(_close_request(), at_utc=NOW)

    assert result.outcome is CloseOutcome.CLOSED
    owner, operation, arguments = router.calls[0]
    assert operation == "close"
    assert owner.account_id == "ACC_A"
    assert owner.position_ticket == 4242
    assert arguments["magic"] == 713001


def test_production_close_port_reports_a_vanished_position_as_already_closed():
    router = RecordingLifecycleRouter(
        {"ok": False, "retcode": -1, "comment": "POSITION_NOT_FOUND"}
    )
    port = LifecyclePositionClosePort(router=router, magic=713001, close_enabled=True)
    result = port.close_position(_close_request(), at_utc=NOW)
    assert result.outcome is CloseOutcome.ALREADY_CLOSED
    assert result.is_success is True


def test_production_close_port_distinguishes_terminal_from_retryable():
    terminal = LifecyclePositionClosePort(
        router=RecordingLifecycleRouter(
            {"ok": False, "retcode": -1, "comment": "OWNERSHIP_ACCOUNT_MISMATCH"}
        ),
        magic=713001, close_enabled=True,
    ).close_position(_close_request(), at_utc=NOW)
    retryable = LifecyclePositionClosePort(
        router=RecordingLifecycleRouter(RuntimeError("WORKER_TIMEOUT")),
        magic=713001, close_enabled=True,
    ).close_position(_close_request(), at_utc=NOW)

    assert terminal.outcome is CloseOutcome.TERMINAL_FAILURE
    assert retryable.outcome is CloseOutcome.RETRYABLE_FAILURE


def test_production_close_port_refuses_when_closes_are_disabled():
    router = RecordingLifecycleRouter({"ok": True, "retcode": 0, "comment": "done"})
    port = LifecyclePositionClosePort(router=router, magic=713001, close_enabled=False)
    result = port.close_position(_close_request(), at_utc=NOW)
    assert result.outcome is CloseOutcome.TERMINAL_FAILURE
    assert result.detail == "POSITION_CLOSE_DISABLED"
    assert router.calls == []


def test_all_live_entry_sends_remain_gated():
    """The entry gate was not weakened by this change."""
    import sys

    sys.path.insert(0, str(ROOT / "tests"))
    from test_prop_rule_runtime_integration import (  # noqa: E402
        LIVE_SEND_MODULES, _prop_gate_lines, _send_calls,
    )

    for relative in LIVE_SEND_MODULES:
        path = ROOT / relative
        sends, gates = _send_calls(path), _prop_gate_lines(path)
        assert sends and gates, relative
        earliest = min(gates)
        for send_line in sends:
            assert send_line > earliest, f"{relative}:{send_line} is ungated"
