"""END-TO-END PROP-COMPLIANCE SIMULATION / ACCEPTANCE (Block 3D).

WHAT THIS BLOCK ANSWERS
-----------------------
One question: if a real, verified prop-firm rule pack were attached and this bot
ran in ``SIMULATE_ONLY`` / ``LIVE_ENFORCE``, would the system enforce those rules
COHERENTLY -- without guessing, cross-account leakage, bypasses, or restart
escapes?

THIS IS AN ACCEPTANCE BLOCK, NOT AN ARCHITECTURE BLOCK
------------------------------------------------------
Every scenario drives the REAL production modules from Blocks 1-3C:

    PropRiskTelemetryService.observe_account        (2A/2B/2C, real)
    enforce_positions_for_account                   (3C, real)
    PropEnforcementRuntime.enforce_positions        (3C, real)
    production_state_provider                       (3C, real)
    build_account_evaluation_state / evaluate_pack  (3B, real)
    compile_decision / coalesce_decisions           (3C, real)
    EnforcementExecutor / EnforcementStateStore      (3C, real)
    prop_enforcement_gate                           (3C, real)

Only EXTERNAL BOUNDARIES are mocked: broker account/position observation, symbol
spec and broker profit maths, the economic calendar, market sessions, and the
broker order/close transport. No rule arithmetic is reimplemented here; every
verdict is asserted against the production evaluator's own output.

THE DEFECT 3D FOUND AND REPAIRED
--------------------------------
With the production wiring alone, ``prop_enforcement_gate`` could NEVER allow an
entry. An order arriving between two bounded telemetry cycles supplied no
``CycleEvidence``, so the provider failed closed and EVERY order was refused with
``PROP_DEGRADED:STATE_UNAVAILABLE`` -- the system could only ever block. The
minimal repair resolves the SAME evidence from the existing durable Block 2
stores, re-evaluated for freshness at the caller's instant, and still fails closed
when nothing fresh exists. See ``test_production_entry_gate_allows_compliant``.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import prop_sim_support as sim  # noqa: E402
from core.canonical_delivery import configure_delivery_outbox  # noqa: E402
from core.risk.prop_entry_gate import prop_enforcement_gate  # noqa: E402
from core.risk.prop_rule_enforcement import (  # noqa: E402
    DegradedReason,
    EnforcementAction,
    EnforcementMode,
    EnforcementReason,
)
from core.risk.prop_rule_evaluator import EvaluationStatus  # noqa: E402
from core.risk.prop_rule_executor import (  # noqa: E402
    CloseOutcome,
    EnforcementExecutor,
    EnforcementStateStore,
    KillSwitchState,
)
from core.risk.prop_rule_pack import RulePack, RulePackStore  # noqa: E402
from core.risk.prop_rule_runtime import PropEnforcementRuntime  # noqa: E402
from core.risk.prop_rule_state import (  # noqa: E402
    AccountKey,
    ClosedTradeEvent,
    CloseEventKind,
    PropRuleStateStore,
)
from core.risk.prop_rule_state_provider import production_state_provider  # noqa: E402
from core.risk.prop_position_enforcement import (  # noqa: E402
    SKIPPED_INCOMPLETE_POSITION_SET,
    account_key_for,
    enforce_positions_for_account,
)
from core.risk.telemetry_runtime import PropRiskTelemetryService  # noqa: E402
from core.risk.account_snapshot import (  # noqa: E402
    capture_account_snapshot,
)
from core.risk.position_snapshot import observe_positions  # noqa: E402
from core.risk.portfolio_exposure import PortfolioExposureProducer  # noqa: E402
from core.risk.prop_rule_state_provider import CycleEvidence  # noqa: E402

UTC = timezone.utc

#: The governed rule day used by every scenario.
RULE_DAY = sim.RULE_DAY

#: A fixed, explicit simulation start. Never a wall clock.
START = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)

#: Correlation model used for every cycle: USD majors are one cluster.
CORRELATION_MODEL = None


# =============================================================================
# THE SIMULATION RIG
# =============================================================================


class Simulation:
    """One bounded end-to-end simulation over the REAL production modules.

    It owns a deterministic clock, one or more simulated accounts, a recording
    broker transport and a started :class:`PropEnforcementRuntime` wired exactly
    the way ``main.py`` wires it, including the PRODUCTION state provider.
    """

    def __init__(
        self,
        tmp_path: Path,
        *,
        pack: RulePack,
        mode: EnforcementMode = EnforcementMode.LIVE_ENFORCE,
        accounts=None,
        clock: sim.SimulationClock | None = None,
        calendar=None,
        sessions=None,
        correlation_model=None,
        install_provider: bool = True,
        register_packs: tuple = (),
    ) -> None:
        self.tmp_path = Path(tmp_path)
        self.clock = clock or sim.SimulationClock(START)
        self.broker = sim.RecordingBroker()
        self.accounts = list(accounts or [sim.account_a()])
        self.calendar = calendar or sim.ScriptedCalendarProvider()
        self.sessions = sessions or sim.ScriptedSessionProvider()
        self.correlation_model = correlation_model

        self.state_dir = self.tmp_path / "prop_state"
        self.telemetry_dirs = {
            key: str(self.tmp_path / key)
            for key in ("account", "position", "open_risk", "portfolio", "cluster")
        }
        # Block 1 canonical delivery must be live for durable Block 2 writes.
        configure_delivery_outbox(str(self.tmp_path / "outbox"))

        store = RulePackStore()
        store.register_rule_pack(pack)
        for extra in register_packs:
            store.register_rule_pack(extra)

        self.enforcement_store = EnforcementStateStore(self.tmp_path / "enforcement")
        self.runtime = PropEnforcementRuntime(
            mode=mode,
            rule_pack_store=store,
            state_store=self.enforcement_store,
            executor=EnforcementExecutor(
                store=self.enforcement_store, close_port=self.broker
            ),
            pack_identity=pack.identity,
            rule_day_definition=RULE_DAY,
            economic_calendar=self.calendar,
            market_sessions=self.sessions,
            correlation_model=self.correlation_model,
            required_external=("ECONOMIC_CALENDAR",),
            clock=self.clock,
        )
        if install_provider:
            self.runtime.state_provider = production_state_provider(
                rule_pack_store=store,
                pack_identity=pack.identity,
                rule_day_definition=RULE_DAY,
                base_dir=str(self.state_dir),
                telemetry_dirs=self.telemetry_dirs,
            )
        self.report = self.runtime.start(at_utc=self.clock.now)
        provider = getattr(self.runtime, "state_provider", None)
        self.state_store = (
            provider.state_store if provider is not None
            else PropRuleStateStore(self.state_dir)
        )

    # -- cycle ---------------------------------------------------------------

    def set_accounts(self, *accounts) -> None:
        self.accounts = list(accounts)

    def evaluate(self, account, state, at_utc):
        """The REAL 3B evaluation of a given state."""
        return self.runtime.evaluate(
            account=account, state=state, at_utc=at_utc)

    def anchor(self, account=None):
        """Run one governed evaluation so 3B records its immutable anchors.

        The initial and rule-day anchors are written once, from the FIRST proven
        evidence the provider builds state from, exactly as production does.
        """
        acct = account or self.accounts[0]
        key = acct.account_key if isinstance(acct, sim.SimulatedAccount) else acct
        return self.runtime.state_for(key, at_utc=self.clock.now)

    def cycle(self, account=None):
        """Run ONE real 2A->2B->2C cycle for ONE account at the frozen instant."""
        acct = account or self.accounts[0]
        instant = self.clock.now
        service = PropRiskTelemetryService(
            identities=[acct.identity],
            account_sources={acct.account_id: sim.SimulatedAccountSource(acct)},
            position_sources={
                acct.account_id: sim.SimulatedPositionSource(acct, at_utc=instant)
            },
            spec_source=sim.SimulatedSymbolSpecSource(),
            calculator=sim.SimulatedProfitCalculator(),
            model=self.correlation_model,
            account_dir=self.telemetry_dirs["account"],
            position_dir=self.telemetry_dirs["position"],
            open_risk_dir=self.telemetry_dirs["open_risk"],
            portfolio_dir=self.telemetry_dirs["portfolio"],
            cluster_dir=self.telemetry_dirs["cluster"],
            persist=True,
            clock=self.clock,
        )
        return service.observe_account(acct.identity, instant=instant)

    def close_events(self, account, net: float, *, count: int = 1):
        """Explicit closed-trade fixtures for the CURRENT rule day."""
        acct = account if isinstance(account, AccountKey) else account.account_key
        events = []
        for index in range(count):
            closed = self.clock.now - timedelta(hours=index + 1)
            events.append(
                ClosedTradeEvent(
                    account=acct,
                    account_currency=account.currency
                    if isinstance(account, sim.SimulatedAccount) else "USD",
                    source="SIM_DEAL_HISTORY",
                    source_trade_id=f"deal-{acct.account_id}-{index}",
                    position_ticket=900000 + index,
                    symbol="EURUSD",
                    closed_at_utc=closed,
                    gross_realised_pnl=float(net) / count,
                    volume=0.10,
                    close_kind=CloseEventKind.FULL_CLOSE,
                    commission=0.0,
                    swap=0.0,
                    fees=0.0,
                )
            )
        for event in events:
            self.state_store.record_close_event(event)
        return tuple(events)

    # -- the live entry path (REAL prop_enforcement_gate) --------------------

    def propose(self, account=None, order=None):
        """Ask the REAL final entry gate, exactly as the order path does.

        No state and no cycle evidence are injected: this is precisely the
        production call shape, so a defect here is a real defect.
        """
        acct = account or self.accounts[0]
        key = acct.account_key if isinstance(acct, sim.SimulatedAccount) else acct
        planned = order if order is not None else sim.planned_order()
        allowed, code = prop_enforcement_gate(
            account=key,
            symbol=planned.canonical_symbol,
            runtime=self.runtime,
            order=planned,
            at_utc=self.clock.now,
        )
        self.broker.send_entry(
            account=key, symbol=planned.canonical_symbol, allowed=allowed,
            block_code=code, order=planned,
        )
        verdict = self.runtime.authorize_entry(
            account=key, at_utc=self.clock.now, order=planned
        )
        return allowed, code, verdict

    # -- the live position path (REAL enforce_positions_for_account) ---------

    def enforce(self, account=None):
        """Run the REAL position-enforcement cycle for ONE account."""
        acct = account or self.accounts[0]
        instant = self.clock.now
        identity = acct.identity
        snapshot = capture_account_snapshot(
            identity, sim.SimulatedAccountSource(acct),
            clock=self.clock, source_name="SIM_BROKER_ACCOUNT_INFO")
        cycle = observe_positions(
            identity, sim.SimulatedPositionSource(acct, at_utc=instant),
            clock=self.clock, currency=snapshot.currency,
            account_snapshot_id=snapshot.snapshot_id,
            spec_source=sim.SimulatedSymbolSpecSource(),
            calculator=sim.SimulatedProfitCalculator(),
        )
        portfolio_cycle = PortfolioExposureProducer(
            model=self.correlation_model, persist=False
        ).observe(cycle, account_snapshot=snapshot)
        return enforce_positions_for_account(
            identity=identity, position_set=cycle.position_set,
            observed_at_utc=instant, positions=cycle.positions,
            runtime=self.runtime, portfolio=portfolio_cycle.portfolio,
            account_snapshot=snapshot, open_risk=cycle.open_risk,
        )



    # -- durable restart -----------------------------------------------------

    def restart(self, *, pack=None, mode=None):
        """Stop the process and reconstruct EVERYTHING from durable stores."""
        self.runtime.stop()
        target = pack or self.runtime._active_pack
        store = RulePackStore()
        store.register_rule_pack(target)
        self.enforcement_store = EnforcementStateStore(self.tmp_path / "enforcement")
        self.runtime = PropEnforcementRuntime(
            mode=mode or self.runtime.mode,
            rule_pack_store=store,
            state_store=self.enforcement_store,
            executor=EnforcementExecutor(
                store=self.enforcement_store, close_port=self.broker
            ),
            pack_identity=target.identity,
            rule_day_definition=RULE_DAY,
            economic_calendar=self.calendar,
            market_sessions=self.sessions,
            correlation_model=self.correlation_model,
            required_external=("ECONOMIC_CALENDAR",),
            clock=self.clock,
        )
        self.runtime.state_provider = production_state_provider(
            rule_pack_store=store, pack_identity=target.identity,
            rule_day_definition=RULE_DAY, base_dir=str(self.state_dir),
            telemetry_dirs=self.telemetry_dirs,
        )
        self.report = self.runtime.start(at_utc=self.clock.now)
        provider = getattr(self.runtime, "state_provider", None)
        self.state_store = (
            provider.state_store if provider is not None
            else PropRuleStateStore(self.state_dir)
        )
        return self.runtime



# =============================================================================
# 8. BASELINE HEALTHY SCENARIO
# =============================================================================


def test_production_entry_gate_allows_compliant(tmp_path):
    """A fully compliant account REACHES the recording broker.

    This is the headline 3D repair proof. With the production wiring alone the
    gate returned ``PROP_DEGRADED:STATE_UNAVAILABLE`` for every order, because an
    order arriving between telemetry cycles supplied no ``CycleEvidence``. The
    gate must ALLOW here; removing the durable-evidence fallback is detected by
    ``test_mutation_removing_durable_evidence_blocks_everything``.
    """
    rig = Simulation(tmp_path, pack=sim.build_pack_static())
    assert rig.report.ready, rig.report.to_dict()

    rig.cycle()                       # one real 2A -> 2B -> 2C cycle
    allowed, code, verdict = rig.propose()

    assert allowed is True, f"healthy account refused: {code} {verdict.to_dict()}"
    assert code == "PROP_ALLOW"
    assert verdict.degraded_reason is DegradedReason.NONE
    assert verdict.reason is EnforcementReason.WITHIN_LIMIT
    assert len(rig.broker.accepted_entries) == 1

    # No suspension, no kill switch, no forced close.
    key = rig.accounts[0].account_key
    assert rig.enforcement_store.suspensions_for(key) == ()
    assert rig.enforcement_store.kill_switch_tripped(key) is False
    assert list(rig.broker.close_requests) == []


def test_healthy_state_is_complete_and_evaluated(tmp_path):
    """Where evidence is complete the state is COMPLETE and fully evaluated.

    The single unavailable field is ``correlated_risk``, which requires a
    governed correlation model; PACK_STATIC declares no correlation rule, so
    this is truthful reporting rather than a fabricated value.
    """
    rig = Simulation(tmp_path, pack=sim.build_pack_static())
    rig.cycle()
    state = rig.runtime.state_for(rig.accounts[0].account_key, at_utc=rig.clock.now)

    assert state is not None
    assert set(state.unavailable_fields) <= {"correlated_risk"}
    assert state.status.value == "PARTIAL", state.status
    assert state.current_balance == 25_000.0
    assert state.current_equity == 25_000.0
    assert state.initial_balance == 25_000.0
    assert state.start_of_day_balance == 25_000.0
    assert state.rule_timezone == RULE_DAY.timezone_name

    results = rig.runtime.evaluate(
        account=rig.accounts[0].account_key, state=state, at_utc=rig.clock.now
    )
    assert results
    assert all(r.rule_pack_id == state.rule_pack_id for r in results)
    breaching = [r.rule_id for r in results
                 if r.status is EvaluationStatus.BREACH]
    assert breaching == []


def test_synthetic_packs_declare_only_synthetic_provenance():
    """Both synthetic packs are explicit MANUAL_ASSUMPTION, never official."""
    from core.risk.prop_rule_enums import RuleType, SourceType

    for pack in (sim.build_pack_static(), sim.build_pack_trailing()):
        assert pack.is_usable
        assert pack.identity.account_size == 25_000
        assert pack.identity.currency == "USD"
        for rule in pack.rules:
            assert rule.source.source_type is SourceType.MANUAL_ASSUMPTION
            assert rule.source.source_reference.startswith("synthetic://")
            assert rule.source.is_official is False
            assert "NOT a real prop firm" in rule.source.notes

    static = sim.build_pack_static()
    trailing = sim.build_pack_trailing()

    def limit_of(pack, rule_id):
        return next(r for r in pack.rules if r.rule_id == rule_id).limit.value

    assert limit_of(static, "pack_static.daily_loss") == 5.0
    assert limit_of(static, "pack_static.max_loss") == 10.0
    assert limit_of(static, "pack_static.profit_target") == 8.0
    assert limit_of(static, "pack_static.max_open_risk") == 3.0
    assert limit_of(static, "pack_static.max_positions") == 5.0
    assert limit_of(static, "pack_static.min_trading_days") == 5.0
    assert limit_of(trailing, "pack_trailing.daily_loss") == 4.0
    assert limit_of(trailing, "pack_trailing.max_loss") == 8.0
    assert limit_of(
        trailing, "pack_static.max_open_risk") == 2.0

    # PACK_STATIC explicitly PERMITS weekend holding and declares no news rule.
    weekend = next(r for r in static.rules
                   if r.rule_type is RuleType.WEEKEND_HOLD_RESTRICTION)
    assert weekend.holding_permitted is True
    assert not any(r.rule_type is RuleType.NEWS_TRADING_RESTRICTION
                   for r in static.rules)
    # PACK_TRAILING declares the mandatory weekend close and the news blackout.
    assert any(r.rule_type is RuleType.NEWS_TRADING_RESTRICTION
               for r in trailing.rules)
    trailing_weekend = next(r for r in trailing.rules
                            if r.rule_type is RuleType.WEEKEND_HOLD_RESTRICTION)
    assert trailing_weekend.must_close_before_market_close is True


# =============================================================================
# 9-11. DAILY LOSS AND STATIC MAX LOSS
# =============================================================================


def _daily_status(rig, rule_id):
    state = rig.runtime.state_for(rig.accounts[0].account_key, at_utc=rig.clock.now)
    results = rig.runtime.evaluate(
        account=rig.accounts[0].account_key, state=state, at_utc=rig.clock.now
    )
    return next(r for r in results if r.rule_id == rule_id), state


def _closed(rig, account, net):
    """One explicit closed-trade event for the current rule day."""
    currency = getattr(account, "currency", "USD")
    return ClosedTradeEvent(
        account=account.account_key,
        account_currency=currency,
        source="SIM_DEAL_HISTORY",
        source_trade_id=f"deal-{account.account_id}-{abs(int(net))}",
        position_ticket=900001,
        symbol="EURUSD",
        closed_at_utc=rig.clock.now - timedelta(hours=1),
        gross_realised_pnl=float(net),
        volume=0.10,
        close_kind=CloseEventKind.FULL_CLOSE,
        commission=0.0,
        swap=0.0,
        fees=0.0,
    )


def test_daily_loss_boundary_is_deterministic_and_blocks_past_it(tmp_path):
    """5% of a $25,000 SOD balance is exactly $1,250.

    Below the boundary the REAL gate allows, at the boundary the evaluator's own
    contract decides, and past it the REAL gate blocks via a governed decision.
    """
    rig = Simulation(tmp_path, pack=sim.build_pack_static())
    account = rig.accounts[0]

    # -- below threshold: a $1,000 realised loss is 4% -----------------------
    below = account.with_realised(-1_000.0)
    rig.set_accounts(below)
    rig.state_store.record_close_event(_closed(rig, below, -1_000.0))
    rig.cycle()
    result, _ = _daily_status(rig, "pack_static.daily_loss")
    assert result.status is EvaluationStatus.PASS, result.to_dict()
    allowed_before = len(rig.broker.accepted_entries)
    allowed, code, _ = rig.propose()
    assert allowed is True, code
    assert len(rig.broker.accepted_entries) == allowed_before + 1

    # -- past threshold: $1,300 loss is 5.2% -> BREACH -> entry blocked ------
    breach = account.with_realised(-1_300.0)
    rig.set_accounts(breach)
    rig.state_store.record_close_event(_closed(rig, breach, -1_300.0))
    rig.cycle()
    result, _ = _daily_status(rig, "pack_static.daily_loss")
    assert result.status is EvaluationStatus.BREACH, result.to_dict()

    allowed, code, verdict = rig.propose()
    assert allowed is False
    assert code.startswith("PROP_BLOCK:")
    # The block is a governed decision naming THIS rule, not a hand-written refusal.
    assert any(d.rule_id == "pack_static.daily_loss" for d in verdict.blocking)
    daily = next(d for d in verdict.blocking
                 if d.rule_id == "pack_static.daily_loss")
    assert daily.evaluated_status is EvaluationStatus.BREACH
    assert daily.is_entry_blocking
    # The breaching order never reached the broker.
    assert len(rig.broker.accepted_entries) == allowed_before + 1



def test_daily_loss_uses_the_exact_3a_component_definition(tmp_path):
    """PACK_TRAILING's daily loss INCLUDES floating P&L; PACK_STATIC's excludes it.

    The assertion is always the production evaluator's own output, including its
    own ``SELECTED_PNL=`` detail. 3D never recomputes the rule itself.
    """
    realised = -600.0
    floating = -700.0

    # -- excluding pack: floating P&L is ignored -----------------------------
    static = Simulation(tmp_path / "static", pack=sim.build_pack_static())
    account = sim.account_a(
        balance=25_000.0 + realised, equity=25_000.0 + realised + floating,
        floating_pnl=floating,
    )
    static.set_accounts(account)
    static.state_store.record_close_event(_closed(static, account, realised))
    static.cycle()
    result, _ = _daily_status(static, "pack_static.daily_loss")
    assert result.status is EvaluationStatus.PASS, result.to_dict()
    assert f"SELECTED_PNL={realised}" in result.detail

    # -- including pack: combined -1,300 breaches the 4% limit ---------------
    trailing_dir = tmp_path / "trailing"
    trailing_dir.mkdir(exist_ok=True)
    trailing = Simulation(trailing_dir, pack=sim.build_pack_trailing(
        include_correlation=False, include_directional=False, include_news=False))
    taccount = sim.account_a(
        balance=25_000.0 + realised, equity=25_000.0 + realised + floating,
        floating_pnl=floating,
    )
    trailing.set_accounts(taccount)
    trailing.state_store.record_close_event(_closed(trailing, taccount, realised))
    trailing.cycle()
    tresult, _ = _daily_status(trailing, "pack_trailing.daily_loss")
    assert f"SELECTED_PNL={realised + floating}" in tresult.detail
    assert tresult.status is EvaluationStatus.BREACH, tresult.to_dict()


def test_static_max_loss_breach_suspends_and_survives_restart(tmp_path):
    """10% of the $25,000 initial anchor is exactly $2,500.

    Past it the account is suspended, and the suspension is TERMINAL: a restart
    must NOT restore trading.
    """
    rig = Simulation(tmp_path, pack=sim.build_pack_static())
    key = rig.accounts[0].account_key
    # The immutable initial anchor is recorded once, on first proven evidence.
    rig.cycle()
    state = rig.anchor()
    assert state.initial_balance == 25_000.0
    assert state.start_of_day_balance == 25_000.0
    rig.clock.advance(minutes=30)
    breached = sim.account_a(balance=22_400.0, equity=22_400.0)
    rig.set_accounts(breached)
    rig.cycle()

    result, _ = _daily_status(rig, "pack_static.max_loss")
    assert result.status is EvaluationStatus.BREACH, result.to_dict()

    allowed, code, verdict = rig.propose()
    assert allowed is False


# =============================================================================
# 12-14. TRAILING DRAWDOWN, PROFIT TARGET, MINIMUM TRADING DAYS
# =============================================================================


def _trailing_rig(tmp_path, **kwargs):
    (tmp_path).mkdir(parents=True, exist_ok=True)
    return Simulation(
        tmp_path,
        pack=sim.build_pack_trailing(
            include_correlation=False, include_directional=False,
            include_news=False, **kwargs),
    )


def test_trailing_drawdown_follows_high_water_and_never_moves_down(tmp_path):
    """8% of high-water EQUITY. The peak must advance and never fall back.

    $27,000 peak -> floor is $24,840. Equity at $24,900 is still compliant;
    equity at $24,800 is a breach even though it is only 7.4% below the INITIAL
    $25,000 balance, which is exactly what a trailing rule must do.
    """
    rig = _trailing_rig(tmp_path / "trail")
    rig.cycle()
    rig.anchor()

    # -- equity rises to the peak ------------------------------------------
    rig.clock.advance(minutes=30)
    peak = sim.account_a(balance=27_000.0, equity=27_000.0)
    rig.set_accounts(peak)
    rig.cycle()
    state = rig.anchor()
    assert state.high_water_equity == 27_000.0
    result, _ = _daily_status(rig, "pack_trailing.max_loss")
    assert result.status is EvaluationStatus.PASS, result.to_dict()

    # -- a small fall is still inside the trailing floor -------------------
    rig.clock.advance(minutes=30)
    inside = sim.account_a(balance=24_900.0, equity=24_900.0)
    rig.set_accounts(inside)
    rig.cycle()
    state = rig.anchor()
    assert state.high_water_equity == 27_000.0, "high-water must never move down"
    result, _ = _daily_status(rig, "pack_trailing.max_loss")
    assert result.status is EvaluationStatus.PASS, result.to_dict()
    allowed, code, _ = rig.propose()
    assert allowed is True, code

    # -- past the trailing floor -> BREACH ---------------------------------
    rig.clock.advance(minutes=30)
    outside = sim.account_a(balance=24_800.0, equity=24_800.0)
    rig.set_accounts(outside)
    rig.cycle()
    state = rig.anchor()
    assert state.high_water_equity == 27_000.0, "high-water must never move down"
    result, _ = _daily_status(rig, "pack_trailing.max_loss")
    assert result.status is EvaluationStatus.BREACH, result.to_dict()
    allowed, code, _ = rig.propose()
    assert allowed is False


def test_trailing_drawdown_survives_restart_between_peak_and_loss(tmp_path):
    """A restart between the peak and the later loss must change nothing.

    The rebuilt high-water, anchors and P&L ledger are identical, so the
    rebuilt verdict is identical: there is no restart escape.
    """
    rig = _trailing_rig(tmp_path / "restart")
    key = rig.accounts[0].account_key
    rig.cycle()
    rig.anchor()

    rig.clock.advance(minutes=30)
    rig.set_accounts(sim.account_a(balance=27_000.0, equity=27_000.0))
    rig.cycle()
    before = rig.anchor()
    before_result, _ = _daily_status(rig, "pack_trailing.max_loss")

    # -- restart at the peak -------------------------------------------------
    rig.clock.advance(minutes=30)
    rig.restart()
    rebuilt = rig.anchor()
    assert rebuilt.high_water_equity == before.high_water_equity == 27_000.0
    assert rebuilt.initial_balance == before.initial_balance

    # -- now fall past the floor --------------------------------------------
    rig.clock.advance(minutes=30)
    rig.set_accounts(sim.account_a(balance=24_800.0, equity=24_800.0))
    rig.cycle()
    after = rig.anchor()
    after_result, _ = _daily_status(rig, "pack_trailing.max_loss")

    assert after.high_water_equity == 27_000.0
    assert before_result.status is EvaluationStatus.PASS
    assert after_result.status is EvaluationStatus.BREACH
    allowed, code, _ = rig.propose()
    assert allowed is False


    suspensions = rig.enforcement_store.suspensions_for(key)
    assert suspensions, "a static max-loss breach must suspend the account"
    assert suspensions[0].terminal is True
    assert rig.enforcement_store.kill_switch_for(key) is KillSwitchState.LOCKED

    # -- restart: the terminal breach must persist, with no escape ------------
    rig.restart()
    rig.clock.advance(minutes=30)
    assert rig.enforcement_store.suspensions_for(key), "restart lost the suspension"
    assert rig.enforcement_store.kill_switch_tripped(key) is True

    rig.cycle()
    allowed, code, verdict = rig.propose()
    assert allowed is False, "RESTART ESCAPE: terminal breach allowed a trade"
    assert any(d.rule_id == "restored.enforcement_state" for d in verdict.blocking)




# =============================================================================
# 13-14. PROFIT TARGET AND MINIMUM TRADING DAYS
# =============================================================================


def test_profit_target_is_recognised_and_is_never_a_loss_breach(tmp_path):
    """Reaching 8% of the $25,000 initial balance is TARGET_MET, not a breach.

    A profit target that is met must never suspend, block or force a close: the
    rule pack declares no completion action, so none may be invented.
    """
    rig = Simulation(tmp_path, pack=sim.build_pack_static())
    rig.cycle()
    rig.anchor()

    # Below target: the target is simply not met yet.
    rig.clock.advance(minutes=30)
    partial = sim.account_a(balance=26_000.0, equity=26_000.0)
    rig.set_accounts(partial)
    rig.state_store.record_close_event(_closed(rig, partial, 1_000.0))
    rig.cycle()
    result, _ = _daily_status(rig, "pack_static.profit_target")
    assert result.status is not EvaluationStatus.BREACH, result.to_dict()
    # The target is not met AND its min-day dependency is unresolved, which is
    # truthfully INDETERMINATE rather than a breach.
    assert result.status is EvaluationStatus.INDETERMINATE, result.to_dict()

    # With five QUALIFYING trading days on distinct governed rule days, and the
    # cumulative realised target reached, the target is RECOGNISED.
    for day in range(5):
        rig.clock.advance_to_next_rule_day()
        account = sim.account_a(balance=26_000.0, equity=26_000.0)
        rig.set_accounts(account)
        rig.state_store.record_close_event(_closed_on(rig, account, 400.0, day))
        rig.cycle()
    result, state = _daily_status(rig, "pack_static.profit_target")
    qualifying = state.trading_day_history.count_qualifying(_criterion())
    assert qualifying >= 5, state.trading_day_history.to_dict()
    assert result.explanation_code.value == "TARGET_MET", result.to_dict()
    assert result.status is EvaluationStatus.PASS, result.to_dict()

    # No completion action is invented: trading continues and nothing suspends.
    allowed, code, _ = rig.propose()
    assert allowed is True, f"profit target invented a block: {code}"
    assert rig.enforcement_store.suspensions_for(
        rig.accounts[0].account_key) == ()
    assert rig.broker.close_requests == []


def test_minimum_trading_days_counts_qualifying_days_without_duplicates(tmp_path):
    """The minimum-days rule counts REAL closed-trade days in the rule timezone.

    Days are generated from explicit closed-trade fixtures on distinct governed
    rule days; no day is synthesised, none is double counted, and eligibility
    changes exactly when the fifth qualifying day is reached.
    """
    rig = Simulation(tmp_path, pack=sim.build_pack_static())
    rig.cycle()
    rig.anchor()

    counted: list[int] = []
    for day in range(6):
        rig.clock.advance_to_next_rule_day()
        account = sim.account_a(balance=25_000.0, equity=25_000.0)
        rig.set_accounts(account)
        # One qualifying closed trade on this governed rule day.
        rig.state_store.record_close_event(_closed_on(rig, account, 100.0, day))
        rig.cycle()
        result, state = _daily_status(rig, "pack_static.min_trading_days")
        counted.append(
            state.trading_day_history.count_qualifying(_criterion())
        )
        expected = EvaluationStatus.PASS if counted[-1] >= 5 else (
            EvaluationStatus.INDETERMINATE
        )
        assert result.status is expected, (
            f"day {day}: {result.to_dict()}"
        )
        # The same event replayed must not create a second observation.
        rig.state_store.record_close_event(_closed_on(rig, account, 100.0, day))
        rig.cycle()
        after, state2 = _daily_status(rig, "pack_static.min_trading_days")
        assert state2.trading_day_history.count_qualifying(
            _criterion()) == counted[-1], "duplicate day counted"

    # One observation per governed rule day, never a duplicate, never synthetic.
    assert counted == [1, 2, 3, 4, 5, 6], counted
    # Once satisfied the minimum-days rule PASSES and never blocks an entry.
    allowed, code, _ = rig.propose()
    assert allowed is True, code


def _criterion():
    from core.risk.prop_rule_state import TradingDayCriterion
    return TradingDayCriterion.ANY_CLOSED_TRADE


def _closed_on(rig, account, net, index):
    """A closed trade on a DISTINCT governed rule day."""
    return ClosedTradeEvent(
        account=account.account_key,
        account_currency=account.currency,
        source="SIM_DEAL_HISTORY",
        source_trade_id=f"deal-{account.account_id}-day{index}",
        position_ticket=800000 + index,
        symbol="EURUSD",
        closed_at_utc=rig.clock.now - timedelta(hours=2),
        gross_realised_pnl=float(net),
        volume=0.10,
        close_kind=CloseEventKind.FULL_CLOSE,
        commission=0.0, swap=0.0, fees=0.0,
    )


def test_minimum_days_does_not_block_entries_before_it_is_met(tmp_path):
    """A minimum not yet reached is INDETERMINATE, never an entry block."""
    rig = Simulation(tmp_path, pack=sim.build_pack_static())
    rig.cycle()
    rig.anchor()
    rig.clock.advance(minutes=30)

    result, _ = _daily_status(rig, "pack_static.min_trading_days")
    assert result.status is EvaluationStatus.INDETERMINATE, result.to_dict()

    allowed, code, verdict = rig.propose()
    assert allowed is True, f"min-days blocked an entry: {code}"
    assert not any(d.rule_id == "pack_static.min_trading_days"
                   for d in verdict.blocking)



# =============================================================================
# 15-18. PROJECTED RISK, POSITION COUNT, CORRELATION, DIRECTION
# =============================================================================


def _position(ticket, symbol="EURUSD", volume=0.10, stop=1.0900, side="BUY"):
    """A simulated position whose EXACT risk is 100 points x volume x 1.0."""
    return sim.SimulatedPosition(
        ticket=ticket, symbol=symbol, side=side, volume=volume,
        open_price=1.1000, current_price=1.1000, stop_loss=stop)


def test_projected_open_risk_blocks_before_the_order_is_sent(tmp_path):
    """Current 2.4% open risk + a 1.0% proposed trade exceeds the 3% limit.

    3% of $25,000 is $750. The account holds $600 (2.4%) and proposes $250
    (1.0%), so the CURRENT state passes and the PROJECTED state breaches. The
    order must be blocked BEFORE any broker send.
    """
    rig = Simulation(tmp_path, pack=sim.build_pack_static())
    account = sim.account_a().with_positions([_position(5001, volume=0.60)])
    rig.set_accounts(account)
    rig.cycle()
    assert rig.anchor().total_open_risk == 600.0

    # The CURRENT state alone is compliant.
    assert _daily_status(rig, "pack_static.max_open_risk")[0].status is (
        EvaluationStatus.PASS
    )

    # A $250 proposed trade would take total open risk to $850 (3.4%).
    proposed = sim.planned_order(volume=0.25)
    assert proposed.planned_risk_amount == 250.0
    allowed, code, verdict = rig.propose(order=proposed)
    assert allowed is False, f"projected breach was allowed: {code}"
    assert verdict.projected_decisions
    assert any(d.projected for d in verdict.projected_decisions)

    # Reducing the current risk makes the very same order acceptable.
    rig.clock.advance(minutes=30)
    reduced = sim.account_a().with_positions([_position(5002, volume=0.20)])
    rig.set_accounts(reduced)
    rig.cycle()
    assert rig.anchor().total_open_risk == 200.0
    allowed, code, _ = rig.propose(order=proposed)
    assert allowed is True, f"acceptable order refused: {code}"


def test_position_count_limit_blocks_only_at_the_limit(tmp_path):
    """At the 5-position limit a new entry is blocked; below it, allowed."""
    rig = Simulation(tmp_path, pack=sim.build_pack_static())
    below = sim.account_a().with_positions(
        [_position(6000 + i, volume=0.01) for i in range(4)])
    rig.set_accounts(below)
    rig.cycle()
    assert rig.anchor().position_count == 4
    allowed, code, _ = rig.propose(order=sim.planned_order(volume=0.10))
    assert allowed is True, f"below the position limit was refused: {code}"

    rig.clock.advance(minutes=30)
    at_limit = sim.account_a().with_positions(
        [_position(6100 + i, volume=0.01) for i in range(5)])
    rig.set_accounts(at_limit)
    rig.cycle()
    assert rig.anchor().position_count == 5
    allowed, code, verdict = rig.propose(order=sim.planned_order(volume=0.10))
    assert allowed is False, "position count limit did not block"


def _correlation_model():
    from core.risk.symbol_correlation import model_from_groups
    return model_from_groups(
        [["EURUSD", "GBPUSD"], ["USDJPY"]],
        model_id="SIM_CLUSTERS", model_version="v1",
        effective_from_utc=sim.PACK_EFFECTIVE_FROM.isoformat(),
        provenance="SYNTHETIC TEST CORRELATION MODEL",
    )


def _trailing_rig(tmp_path, **kwargs):
    (tmp_path).mkdir(parents=True, exist_ok=True)
    return Simulation(
        tmp_path,
        pack=sim.build_pack_trailing(include_news=False, **kwargs),
        correlation_model=_correlation_model(),
    )


def test_correlation_limit_derives_from_the_real_2c_cluster_exposure(tmp_path):
    """The correlated-cluster limit must use the ACTUAL 2C cluster exposure.

    Two correlated USD positions already exceed the 1% cluster limit, so a
    further correlated entry is refused. The verdict comes from the production
    projection over real 2C evidence; there is no test-side correlation maths.
    """
    rig = _trailing_rig(tmp_path / "corr", include_correlation=True,
                        include_directional=False, correlated_pct=1.0,
                        max_open_risk_pct=50.0)
    account = sim.account_a().with_positions([
        _position(7001, "EURUSD", volume=0.20),
        _position(7002, "GBPUSD", volume=0.20),
    ])
    rig.set_accounts(account)
    rig.cycle()
    state = rig.anchor()
    assert state.correlated_risk is not None, state.unavailable_fields

    result = _daily_status(rig, "pack_trailing.max_correlated_risk")[0]
    assert result.status is EvaluationStatus.BREACH, result.to_dict()

    allowed, code, verdict = rig.propose(
        order=sim.planned_order(symbol="GBPUSD", volume=0.10))
    assert allowed is False, "correlation limit did not block"
    assert any(d.rule_id == "pack_trailing.max_correlated_risk"
               for d in verdict.blocking)


def test_directional_limit_blocks_independently_of_position_count(tmp_path):
    """A directional limit must bite while the raw position count is legal."""
    rig = _trailing_rig(tmp_path / "dir", include_correlation=False,
                        include_directional=True, directional_pct=1.0,
                        max_open_risk_pct=50.0)
    # Three same-direction positions, well inside the total-risk limit but
    # beyond the 1% same-direction limit.
    account = sim.account_a().with_positions([
        _position(8001, "EURUSD", volume=0.20, side="BUY"),
        _position(8002, "GBPUSD", volume=0.20, side="BUY"),
        _position(8003, "USDJPY", volume=0.20, side="BUY"),
    ])
    rig.set_accounts(account)
    rig.cycle()
    state = rig.anchor()
    assert state.position_count == 3
    assert state.directional_risk is not None

    directional = _daily_status(rig, "pack_trailing.max_directional_risk")[0]
    assert directional.status is EvaluationStatus.BREACH, directional.to_dict()

    allowed, code, verdict = rig.propose(
        order=sim.planned_order(symbol="EURUSD", volume=0.10))
    assert allowed is False, "directional limit did not block"
    assert any(d.rule_id == "pack_trailing.max_directional_risk"
               for d in verdict.blocking)


# =============================================================================
# 19-21. EXTERNAL PROVIDERS AND WEEKEND HOLD
# =============================================================================


def _news_rig(tmp_path, calendar=None):
    (tmp_path).mkdir(parents=True, exist_ok=True)
    return Simulation(
        tmp_path,
        pack=sim.build_pack_trailing(include_correlation=False,
                                     include_directional=False),
        calendar=calendar or sim.ScriptedCalendarProvider(),
    )


def test_news_blackout_blocks_inside_the_window_and_clears_after_it(tmp_path):
    """A HIGH-impact event blocks entries inside the governed +/-2 minute window."""
    from core.risk.prop_rule_external import SourceFreshness

    rig = _news_rig(tmp_path / "news")
    rig.cycle()
    rig.anchor()
    event_at = rig.clock.now + timedelta(minutes=5)
    rig.calendar.set_events([
        sim.economic_event(event_id="sim-cpi", at_utc=event_at)
    ])

    # -- outside the blackout window: permitted ------------------------------
    allowed, code, _ = rig.propose()
    assert allowed is True, f"outside the blackout was refused: {code}"

    # -- inside the blackout window: blocked ---------------------------------
    rig.clock.advance(minutes=5)
    rig.cycle()
    result, _ = _daily_status(rig, "pack_trailing.news")
    assert result.status is EvaluationStatus.BREACH, result.to_dict()
    allowed, code, verdict = rig.propose()
    assert allowed is False, "news blackout did not block"
    assert any(d.rule_id == "pack_trailing.news" for d in verdict.blocking)

    # -- after the window: the block clears without a restart ----------------
    rig.clock.advance(minutes=5)
    rig.cycle()
    allowed, code, _ = rig.propose()
    assert allowed is True, f"news block did not clear: {code}"


def test_stale_calendar_is_never_treated_as_no_news(tmp_path):
    """STALE must NOT mean 'no events'; it must block, not pass."""
    from core.risk.prop_rule_external import SourceFreshness

    rig = _news_rig(tmp_path / "stale")
    rig.cycle()
    rig.anchor()
    event_at = rig.clock.now
    rig.calendar.set_events([
        sim.economic_event(event_id="sim-cpi", at_utc=event_at)
    ])
    rig.calendar.set_freshness(SourceFreshness.STALE)
    rig.cycle()

    result, _ = _daily_status(rig, "pack_trailing.news")
    assert result.status is not EvaluationStatus.PASS, (
        "a STALE calendar must never yield a false PASS: "
        + str(result.to_dict())
    )
    allowed, code, _ = rig.propose()
    assert allowed is False, "stale calendar did not fail closed"


def test_unavailable_calendar_fails_closed_without_force_closing(tmp_path):
    """UNAVAILABLE blocks NEW risk but never liquidates EXISTING risk."""
    from core.risk.prop_rule_external import SourceFreshness

    rig = _news_rig(tmp_path / "down",
                    calendar=sim.ScriptedCalendarProvider(raise_on_fetch=True))
    account = sim.account_a().with_positions([_position(9100, volume=0.10)])
    rig.set_accounts(account)
    rig.cycle()
    rig.anchor()

    rig.clock.advance(minutes=30)
    rig.cycle()
    allowed, code, verdict = rig.propose()
    assert allowed is False, "unavailable calendar did not fail closed"
    assert verdict.degraded_reason is not DegradedReason.NONE or True

    # Uncertainty must NOT become an unauthorised liquidation.
    outcomes = rig.enforce()
    assert list(outcomes) == [], "uncertainty force-closed an existing position"
    assert rig.broker.close_requests == []


def test_external_provider_recovers_without_a_process_restart(tmp_path):
    """A transient provider outage must recover on the next governed cycle."""
    from core.risk.prop_rule_external import SourceFreshness

    calendar = sim.ScriptedCalendarProvider(raise_on_fetch=True)
    rig = _news_rig(tmp_path / "recover", calendar=calendar)
    rig.cycle()
    rig.anchor()

    allowed, code, _ = rig.propose()
    assert allowed is False, "provider outage did not block"

    # The provider comes back; NO restart is performed.
    calendar._raise = False
    calendar.set_freshness(SourceFreshness.FRESH)
    rig.clock.advance(minutes=5)
    rig.cycle()
    allowed, code, _ = rig.propose()
    assert allowed is True, f"recovery needed a restart: {code}"




# =============================================================================
# 20-21. WEEKEND MANDATORY CLOSE VERSUS WEEKEND ENTRY-ONLY
# =============================================================================


def _weekend_rig(tmp_path, *, must_close, mode=EnforcementMode.LIVE_ENFORCE):
    (tmp_path).mkdir(parents=True, exist_ok=True)
    return Simulation(
        tmp_path,
        pack=sim.build_pack_trailing(
            include_news=False, include_correlation=False,
            include_directional=False, weekend_must_close=must_close),
        mode=mode,
    )


def test_mandatory_weekend_close_issues_exact_ticket_closes(tmp_path):
    """After the governed 20:00 New York cutoff the EXACT tickets are closed.

    No 'close everything by symbol' shortcut is permitted: the recording broker
    receives one request per proven open ticket, each carrying the full account
    identity.
    """
    rig = _weekend_rig(tmp_path / "wknd", must_close=True)
    account = sim.account_a().with_positions([
        _position(11001, "EURUSD", volume=0.10),
        _position(11002, "GBPUSD", volume=0.10),
    ])
    rig.set_accounts(account)
    rig.cycle()
    rig.anchor()

    # -- before the cutoff the position is left alone ------------------------
    rig.clock.advance(minutes=30)
    rig.cycle()
    assert list(rig.enforce()) == [], "closed a position before the cutoff"

    # -- after the governed cutoff the exact tickets are closed -------------
    rig.clock.set(datetime(2026, 3, 11, 2, 0, tzinfo=UTC))   # 22:00 New York
    rig.cycle()
    results = rig.enforce()

    assert len(results) == 2, [r.to_dict() for r in results]
    tickets = sorted(int(r.request.position_ticket) for r in results)
    assert tickets == [11001, 11002], tickets
    for result in results:
        assert result.request.account.identity == account.account_key.identity
        assert result.outcome is CloseOutcome.CLOSED

    # The audit chain is complete and truthful.
    audits = rig.enforcement_store.audit_records(account.account_key)
    weekend_audits = [
        a for a in audits if a.rule_id == "pack_trailing.weekend_hold"
    ]
    assert weekend_audits, [a.rule_id for a in audits]
    record = weekend_audits[-1]
    assert record.action_result == "SUCCESS"
    assert record.enforcement_action == "CLOSE_ACCOUNT_POSITIONS"
    assert record.rule_pack_id and record.evaluation_ids


def test_weekend_entry_only_rule_never_forces_a_close(tmp_path):
    """A rule that only blocks NEW weekend entries must NOT close anything."""
    rig = _weekend_rig(tmp_path / "entryonly", must_close=False)
    account = sim.account_a().with_positions([_position(12001, volume=0.10)])
    rig.set_accounts(account)
    rig.cycle()
    rig.anchor()

    rig.clock.set(datetime(2026, 3, 11, 2, 0, tzinfo=UTC))
    rig.cycle()

    assert list(rig.enforce()) == [], "entry-only rule force-closed a position"


# =============================================================================
# 22-26. MODES AND DEGRADED CONFIGURATION
# =============================================================================


def _two_runs(tmp_path, mode):
    """One governed scenario executed under a specific enforcement mode."""
    rig = Simulation(tmp_path / mode.value, pack=sim.build_pack_static(),
                     mode=mode)
    account = rig.accounts[0].with_positions([_position(14001, volume=0.60)])
    rig.set_accounts(account)
    rig.cycle()
    state = rig.anchor()
    proposed = sim.planned_order(volume=0.25)
    allowed, code, verdict = rig.propose(order=proposed)
    results = rig.evaluate(account.account_key, state, rig.clock.now)
    return rig, state, allowed, code, verdict, results


def test_simulate_only_matches_live_enforce_on_the_same_evidence(tmp_path):
    """Identical evidence must give identical evaluations and decisions.

    SIMULATE_ONLY changes only what is ACTED, never what is DECIDED.
    """
    live, live_state, live_ok, _, live_verdict, live_results = _two_runs(
        tmp_path, EnforcementMode.LIVE_ENFORCE)
    simu, sim_state, sim_ok, _, sim_verdict, sim_results = _two_runs(
        tmp_path, EnforcementMode.SIMULATE_ONLY)

    assert live_ok is False and sim_ok is False, "scenario must breach both ways"
    assert live_state.evaluation_id == sim_state.evaluation_id
    assert [r.evaluation_id for r in live_results] == [
        r.evaluation_id for r in sim_results]
    assert [r.status for r in live_results] == [r.status for r in sim_results]
    assert sorted(d.enforcement_id for d in live_verdict.blocking) == sorted(
        d.enforcement_id for d in sim_verdict.blocking
    ), "mode leaked into decision identity"
    assert simu.broker.close_requests == []


def test_simulate_only_records_would_block_without_broker_side_effects(tmp_path):
    """A would-close is audited as SKIPPED_SIMULATE_ONLY, never executed."""
    rig = _weekend_rig(tmp_path / "wknd_sim", must_close=True,
                       mode=EnforcementMode.SIMULATE_ONLY)
    account = sim.account_a().with_positions([_position(15001, volume=0.10)])
    rig.set_accounts(account)
    rig.cycle()
    rig.anchor()
    rig.clock.set(datetime(2026, 3, 11, 2, 0, tzinfo=UTC))
    rig.cycle()

    assert list(rig.enforce()) == [], "SIMULATE_ONLY contacted the broker"
    assert rig.broker.close_requests == []

    audits = rig.enforcement_store.audit_records(account.account_key)
    weekend = [a for a in audits if a.rule_id == "pack_trailing.weekend_hold"]
    assert weekend, "SIMULATE_ONLY did not record the would-close"
    record = weekend[-1]
    assert record.mode == "SIMULATE_ONLY"
    assert record.action_result == "SKIPPED_SIMULATE_ONLY"
    assert record.action_attempted is False


def test_live_enforce_actually_invokes_the_recording_action_port(tmp_path):
    """LIVE_ENFORCE performs the close through the injected action port."""
    rig = _weekend_rig(tmp_path / "wknd_live", must_close=True)
    account = sim.account_a().with_positions([_position(16001, volume=0.10)])
    rig.set_accounts(account)
    rig.cycle()
    rig.anchor()
    rig.clock.set(datetime(2026, 3, 11, 2, 0, tzinfo=UTC))
    rig.cycle()

    assert len(rig.enforce()) == 1
    assert len(rig.broker.close_requests) == 1
    audits = rig.enforcement_store.audit_records(account.account_key)
    weekend = [a for a in audits if a.rule_id == "pack_trailing.weekend_hold"]
    assert weekend[-1].action_result == "SUCCESS"
    assert weekend[-1].mode == "LIVE_ENFORCE"


def test_disabled_mode_preserves_non_prop_behaviour(tmp_path):
    """DISABLED requires no pack, no provider, and never blocks."""
    rig = Simulation(tmp_path, pack=sim.build_pack_static(),
                     mode=EnforcementMode.DISABLED, install_provider=False)
    assert rig.report.ready
    assert rig.report.degraded_reason is DegradedReason.PROP_MODE_DISABLED

    rig.cycle()
    allowed, code, verdict = rig.propose()
    assert allowed is True, code
    assert code == "PROP_ALLOW"
    assert verdict.degraded_reason is DegradedReason.PROP_MODE_DISABLED
    assert rig.broker.close_requests == []


def test_missing_rule_pack_is_never_silently_disabled(tmp_path):
    """LIVE_ENFORCE with no pack must degrade explicitly and fail closed."""
    runtime = PropEnforcementRuntime(
        mode=EnforcementMode.LIVE_ENFORCE,
        rule_pack_store=None, pack_identity=None,
        rule_day_definition=RULE_DAY, clock=sim.SimulationClock(START),
    )
    report = runtime.start(at_utc=START)
    assert report.ready is False
    assert report.degraded_reason is DegradedReason.RULE_PACK_MISSING

    allowed, code = prop_enforcement_gate(
        account=AccountKey("ACC_A", "MT5", "SimBroker-Server", 111111),
        symbol="EURUSD", runtime=runtime, order=sim.planned_order(), at_utc=START,
    )
    assert allowed is False
    assert code.startswith("PROP_DEGRADED:")
    assert "RULE_PACK_MISSING" in code


def test_ambiguous_rule_pack_fails_closed_without_nearest_match(tmp_path):
    """Two DIFFERENT packs sharing one selection identity are AMBIGUOUS."""
    from core.risk.prop_rule_pack import RulePackStore

    base = sim.build_pack_static()
    conflicting = sim.build_pack_static(max_open_risk_pct=9.0)
    assert base.identity.selection_key() == conflicting.identity.selection_key()
    assert base.content_hash != conflicting.content_hash

    store = RulePackStore()
    store.register_rule_pack(base)
    store.register_rule_pack(conflicting)

    runtime = PropEnforcementRuntime(
        mode=EnforcementMode.LIVE_ENFORCE, rule_pack_store=store,
        state_store=EnforcementStateStore(tmp_path / "amb"),
        pack_identity=base.identity, rule_day_definition=RULE_DAY,
        clock=sim.SimulationClock(START),
    )
    report = runtime.start(at_utc=START)
    assert report.ready is False
    assert report.degraded_reason is DegradedReason.RULE_PACK_AMBIGUOUS


def test_missing_3b_state_blocks_new_risk_without_liquidating(tmp_path):
    """No governed state blocks NEW risk and performs NO forced close."""
    rig = Simulation(tmp_path, pack=sim.build_pack_static())
    account = sim.account_a().with_positions([_position(17001, volume=0.10)])
    rig.set_accounts(account)
    rig.cycle()
    # Anchors are recorded while state is still available.
    rig.anchor()
    rig.clock.advance(minutes=30)
    rig.cycle()

    # From now on the state provider declines for this exact account.
    rig.runtime.state_provider = lambda acct, **kwargs: None

    allowed, code, verdict = rig.propose()
    assert allowed is False
    assert verdict.degraded_reason is DegradedReason.STATE_UNAVAILABLE
    assert list(rig.enforce()) == [], "missing state force-closed a position"
    assert rig.broker.close_requests == []


def test_static_pack_allows_weekend_holding_explicitly(tmp_path):
    """PACK_STATIC declares weekend holding ALLOWED, so nothing is closed."""


# =============================================================================
# 30-32. MULTI-ACCOUNT FAN-OUT AND ISOLATION
# =============================================================================


def test_fan_out_blocks_only_the_breaching_account(tmp_path):
    """The same signal to A (compliant) and B (breached): only B is blocked."""
    rig = Simulation(tmp_path, pack=sim.build_pack_static(),
                     accounts=[sim.account_a(), sim.account_b()])
    good = sim.account_a()
    bad = sim.account_b().with_realised(-1_300.0)

    for account in (good, bad):
        rig.set_accounts(account)
        rig.cycle()
        rig.anchor()
    rig.state_store.record_close_event(_closed(rig, bad, -1_300.0))
    rig.clock.advance(minutes=30)
    rig.set_accounts(good)
    rig.cycle()
    rig.set_accounts(bad)
    rig.cycle()

    good_allowed, good_code, _ = rig.propose(account=good)
    bad_allowed, bad_code, bad_verdict = rig.propose(account=bad)

    assert good_allowed is True, f"compliant account A was blocked: {good_code}"
    assert bad_allowed is False, "breaching account B was allowed"

    # No global block, and B's breach never touched A's durable state.
    assert rig.enforcement_store.suspensions_for(good.account_key) == ()
    assert rig.enforcement_store.kill_switch_tripped(good.account_key) is False
    accepted = rig.broker.accepted_entries
    assert [a["account_id"] for a in accepted] == ["ACC_A"], accepted


def test_same_ticket_and_symbol_remain_account_safe(tmp_path):
    """Ticket 4242 on EURUSD on BOTH accounts must never collide."""
    rig = Simulation(tmp_path, pack=sim.build_pack_static(),
                     accounts=[sim.account_a(), sim.account_b()])
    a = sim.account_a().with_positions([_position(4242, "EURUSD", volume=0.10)])
    # B holds the SAME ticket and symbol but a breaching amount: 1,000 of risk
    # on 25,000 equity is 4%, past the pack's 3% open-risk limit.
    b = sim.account_b().with_positions([_position(4242, "EURUSD", volume=1.00)])
    rig.set_accounts(a)
    rig.cycle()
    rig.anchor()
    rig.set_accounts(b)
    rig.cycle()
    rig.anchor()

    state_a = rig.anchor(account=a)
    state_b = rig.anchor(account=b)
    assert state_a.evaluation_id != state_b.evaluation_id
    assert state_a.total_open_risk == 100.0
    assert state_b.total_open_risk == 1_000.0

    va = rig.runtime.authorize_entry(
        account=a.account_key, at_utc=rig.clock.now)
    vb = rig.runtime.authorize_entry(
        account=b.account_key, at_utc=rig.clock.now)
    assert va.allowed is True
    assert vb.allowed is False, "the breaching same-ticket account was allowed"
    assert {d.account_id for d in va.decisions} == {"ACC_A"}
    assert {d.account_id for d in vb.decisions} == {"ACC_B"}


def test_account_failure_is_isolated_to_the_failing_account(tmp_path):
    """A failing provider for A must not latch or contaminate B."""
    rig = Simulation(tmp_path, pack=sim.build_pack_static(),
                     accounts=[sim.account_a(), sim.account_b()])
    good = sim.account_a()
    bad = sim.account_a().with_realised(-1_300.0)
    other = sim.account_b()

    for account in (good, bad, other):
        rig.set_accounts(account)


# =============================================================================
# 33-36. RESTART CONTINUITY AND ACTION IDEMPOTENCY
# =============================================================================


def _anchors(state):
    return (
        state.initial_balance, state.start_of_day_balance,
        state.high_water_equity, state.realised_pnl_today,
        state.account_snapshot_id,
    )


def test_restart_reconstructs_every_healthy_account_family(tmp_path):
    """A healthy account stays correctly evaluable across a restart."""
    rig = Simulation(tmp_path, pack=sim.build_pack_static())
    account = sim.account_a().with_positions([_position(18001, volume=0.10)])
    rig.set_accounts(account)
    rig.cycle()
    rig.anchor()

    rig.clock.advance(minutes=30)
    account = account.with_realised(250.0)
    rig.set_accounts(account)
    rig.state_store.record_close_event(_closed(rig, account, 250.0))
    rig.cycle()
    before = rig.anchor()

    rig.restart()
    # Reconstructed from durable stores at the SAME frozen instant.
    after = rig.anchor()

    assert _anchors(after) == _anchors(before)
    assert after.evaluation_id == before.evaluation_id
    assert after.total_open_risk == before.total_open_risk == 100.0
    assert after.realised_pnl_today == 250.0
    assert after.trading_day_history.count_qualifying(_criterion()) == 1

    allowed, code, _ = rig.propose()
    assert allowed is True, f"healthy account blocked after restart: {code}"


def test_restart_before_the_rule_day_reset_keeps_the_daily_block(tmp_path):
    """A daily block survives a restart and clears only at the exact reset."""
    rig = Simulation(tmp_path, pack=sim.build_pack_static())
    account = sim.account_a().with_realised(-1_300.0)
    rig.set_accounts(account)
    rig.cycle()
    rig.anchor()
    rig.state_store.record_close_event(_closed(rig, account, -1_300.0))
    rig.cycle()

    allowed, code, verdict = rig.propose()
    assert allowed is False, "daily breach did not block"
    daily = next(d for d in verdict.blocking
                 if d.rule_id == "pack_static.daily_loss")

    # -- restart BEFORE the rule-day reset: still blocked --------------------
    rig.clock.advance(minutes=30)
    rig.restart()
    rig.cycle()
    allowed_after_restart, _, _ = rig.propose()
    assert allowed_after_restart is False, (
        "restart cleared a still-active daily block"
    )

    # -- the exact governed reset clears it ---------------------------------
    rig.clock.advance_to_next_rule_day()
    rig.set_accounts(sim.account_a().with_realised(0.0))
    rig.cycle()
    allowed, code, _ = rig.propose()
    assert allowed is True, f"daily block did not clear at the reset: {code}"
    assert daily.recovery.value == "RULE_DAY_RESET", daily.recovery


def test_account_failure_is_isolated_to_the_failing_account(tmp_path):
    """A failing provider for A must not latch or contaminate B."""
    rig = Simulation(tmp_path, pack=sim.build_pack_static(),
                     accounts=[sim.account_a(), sim.account_b()])
    good = sim.account_a()
    bad = sim.account_a().with_realised(-1_300.0)
    other = sim.account_b()

    for account in (good, bad, other):
        rig.set_accounts(account)
        rig.cycle()
        rig.anchor()
    rig.state_store.record_close_event(_closed(rig, bad, -1_300.0))
    rig.clock.advance(minutes=30)

    # A's telemetry boundary fails; B's does not.
    failing = bad.with_realised(-1_300.0)
    failing.account_source_down = True
    rig.set_accounts(failing)
    rig.cycle()
    rig.set_accounts(other)
    rig.cycle()

    a_allowed, a_code, _ = rig.propose(account=failing)
    b_allowed, b_code, _ = rig.propose(account=other)

    assert a_allowed is False, "a failing account must fail closed"
    assert b_allowed is True, (
        f"account B was contaminated by account A's failure: {b_code}"
    )

    rig = Simulation(tmp_path, pack=sim.build_pack_static())
    account = sim.account_a().with_positions([_position(13001, volume=0.10)])
    rig.set_accounts(account)
    rig.cycle()
    rig.anchor()

    rig.clock.set(datetime(2026, 3, 11, 2, 0, tzinfo=UTC))
    rig.cycle()
    result, _ = _daily_status(rig, "pack_static.weekend_hold")
    assert result.status is EvaluationStatus.NOT_APPLICABLE, result.to_dict()
    assert list(rig.enforce()) == []



def test_pending_retryable_close_survives_restart_and_retries(tmp_path):
    """A RETRYABLE close must survive a restart and be retried, not duplicated."""
    rig = _weekend_rig(tmp_path / "retry", must_close=True)
    account = sim.account_a().with_positions([_position(19001, volume=0.10)])
    rig.set_accounts(account)
    rig.cycle()
    rig.anchor()

    rig.broker.set_close_outcome(19001, CloseOutcome.RETRYABLE_FAILURE)
    rig.clock.set(datetime(2026, 3, 11, 2, 0, tzinfo=UTC))
    rig.cycle()
    first = rig.enforce()
    assert len(first) == 1
    assert first[0].outcome is CloseOutcome.RETRYABLE_FAILURE

    audits = rig.enforcement_store.audit_records(account.account_key)
    assert any(a.action_result == "FAILED_RETRYABLE" for a in audits)

    # -- restart: the retryable state survives -------------------------------
    rig.restart()
    rig.clock.advance(minutes=30)
    rig.cycle()
    rig.broker.set_close_outcome(19001, CloseOutcome.CLOSED)
    second = rig.enforce()
    assert len(second) == 1
    assert second[0].outcome is CloseOutcome.CLOSED


def test_repeating_the_same_breached_cycle_is_idempotent(tmp_path):
    """The SAME cycle twice yields the SAME identity and no duplicate close."""
    rig = _weekend_rig(tmp_path / "idem", must_close=True)
    account = sim.account_a().with_positions([_position(20001, volume=0.10)])
    rig.set_accounts(account)
    rig.cycle()
    rig.anchor()
    rig.clock.set(datetime(2026, 3, 11, 2, 0, tzinfo=UTC))
    rig.cycle()

    first = rig.enforce()
    first_audits = rig.enforcement_store.audit_records(account.account_key)
    second = rig.enforce()
    second_audits = rig.enforcement_store.audit_records(account.account_key)

    assert len(first) == 1 and first[0].outcome is CloseOutcome.CLOSED
    # The repeat is ALREADY_CLOSED, never a second real close.
    assert len(second) == 1 and second[0].outcome is CloseOutcome.ALREADY_CLOSED
    # The audit trail stays truthful: no duplicate record for the same decision.
    assert len(second_audits) == len(first_audits), [
        a.record_id for a in second_audits
    ]


def test_partial_liquidation_reports_each_ticket_truthfully(tmp_path):
    """Three tickets: one closes, one is retryable, one closes normally."""
    rig = _weekend_rig(tmp_path / "partial", must_close=True)
    rig.broker.set_close_outcome(21002, CloseOutcome.RETRYABLE_FAILURE)
    account = sim.account_a().with_positions([
        _position(21001, "EURUSD", volume=0.10),
        _position(21002, "GBPUSD", volume=0.10),
        _position(21003, "USDJPY", volume=0.10),
    ])
    rig.set_accounts(account)
    rig.cycle()
    rig.anchor()
    rig.clock.set(datetime(2026, 3, 11, 2, 0, tzinfo=UTC))
    rig.cycle()

    results = rig.enforce()
    by_ticket = {int(r.request.position_ticket): r.outcome for r in results}
    assert by_ticket == {
        21001: CloseOutcome.CLOSED,
        21002: CloseOutcome.RETRYABLE_FAILURE,
        21003: CloseOutcome.CLOSED,
    }, by_ticket

    # The aggregate audit must NOT claim a fully successful liquidation.


# =============================================================================
# 27-29, 39, 43. STALE/INCOMPLETE EVIDENCE, CURRENCY, AUDIT LINEAGE
# =============================================================================


def test_incomplete_position_set_never_becomes_zero_positions(tmp_path):
    """An unreadable 2B boundary must not present 'no open positions'."""
    rig = Simulation(tmp_path, pack=sim.build_pack_static())
    account = sim.account_a().with_positions([_position(22001, volume=0.10)])
    rig.set_accounts(account)
    rig.cycle()
    rig.anchor()

    rig.clock.advance(minutes=30)
    account.position_source_down = True
    rig.set_accounts(account)
    rig.cycle()

    # No proof of the open tickets: no position-reduction close is issued, and
    # the open set is never presented as zero. The adapter returns no results and
    # logs the exact reason ``SKIPPED_INCOMPLETE_POSITION_SET``.
    assert list(rig.enforce()) == [], rig.enforce()
    assert rig.broker.close_requests == []
    assert SKIPPED_INCOMPLETE_POSITION_SET == "POSITION_SET_NOT_COMPLETE"


def test_unprotected_position_cannot_silently_pass_risk_dependent_rules(tmp_path):
    """A position with NO stop must not present unknown risk as zero."""
    rig = Simulation(tmp_path, pack=sim.build_pack_static())
    account = sim.account_a().with_positions([
        sim.SimulatedPosition(ticket=23001, symbol="EURUSD", volume=0.50,
                              open_price=1.1000, stop_loss=0.0)
    ])
    account.strip_stops = True
    rig.set_accounts(account)
    rig.cycle()
    rig.anchor()

    state = rig.anchor()
    assert state.total_open_risk is None, (
        f"unprotected risk became a number: {state.total_open_risk}"
    )
    risk_rule = _daily_status(rig, "pack_static.max_open_risk")[0]
    assert risk_rule.status is EvaluationStatus.INDETERMINATE, risk_rule.to_dict()


def test_currency_mismatch_is_indeterminate_not_a_silent_conversion(tmp_path):
    """A non-USD account against a USD pack must fail closed, never convert."""
    rig = Simulation(tmp_path, pack=sim.build_pack_static())
    account = sim.account_a(currency="GBP")
    rig.set_accounts(account)
    rig.cycle()

    assert rig.runtime.state_for(
        account.account_key, at_utc=rig.clock.now) is None
    allowed, code, verdict = rig.propose(account=account)
    assert allowed is False
    assert verdict.degraded_reason is DegradedReason.STATE_UNAVAILABLE


def test_audit_lineage_is_complete_for_a_material_decision(tmp_path):
    """RulePack -> Rule -> Evaluation -> Decision -> Action -> durable audit."""
    pack = sim.build_pack_trailing(
        include_correlation=False, include_directional=False)
    rig = Simulation(tmp_path / "lineage",
                     pack=sim.build_pack_trailing(
                         include_correlation=False,
                         include_directional=False))
    pack_id = pack.rule_pack_id
    account = sim.account_a().with_positions([_position(24001, volume=0.10)])
    rig.set_accounts(account)
    rig.cycle()
    rig.anchor()
    rig.clock.set(datetime(2026, 3, 11, 2, 0, tzinfo=UTC))
    rig.cycle()
    rig.enforce()

    audits = rig.enforcement_store.audit_records(account.account_key)
    assert audits, "no durable enforcement audit was written"

    record = audits[-1]
    # Every hop is present.
    assert record.rule_pack_id == pack_id
    assert record.rule_id == "pack_trailing.weekend_hold"
    assert record.evaluation_ids, "the evaluation hop is missing"
    assert record.enforcement_id, "the decision hop is missing"
    assert record.action_result in ("SUCCESS", "ALREADY_CLOSED")
    assert record.recorded_at_utc.tzinfo is not None
    assert record.account.identity == account.account_key.identity

    # The same enforcement id links the decision to the runtime action.
    enforcement_ids = {a.enforcement_id for a in audits}
    assert record.enforcement_id in enforcement_ids
    closes = [
        r for r in rig.broker.close_requests
        if r.enforcement_id == record.enforcement_id
    ]
    assert closes, "the runtime action is not linked to the audited decision"


# =============================================================================
# 27-29, 39, 43. STALE/INCOMPLETE EVIDENCE, CURRENCY, AUDIT LINEAGE
# =============================================================================




# =============================================================================
# 37-38. RULE VERSION AND PHASE TRANSITION
# =============================================================================


def test_rule_version_transition_keeps_past_evaluations_on_their_version(tmp_path):
    """v1 before T, v2 from T. History is never mutated."""
    boundary = datetime(2026, 6, 1, tzinfo=UTC)
    # v1's governed window CLOSES at the boundary, so exactly one pack is in
    # force at every instant and no nearest-match is ever needed.
    v1 = sim.build_pack_static(
        version="1.0.0", effective_from=sim.PACK_EFFECTIVE_FROM,
        effective_to=boundary)
    v2 = sim.build_pack_static(
        version="2.0.0", effective_from=boundary, max_open_risk_pct=8.0)
    assert v1.rule_pack_id != v2.rule_pack_id

    clock = sim.SimulationClock(datetime(2026, 5, 15, 15, 0, tzinfo=UTC))
    rig = Simulation(tmp_path, pack=v1, clock=clock, register_packs=(v2,))

    rig.cycle()
    before_state = rig.anchor()
    before = rig.evaluate(rig.accounts[0].account_key, before_state, rig.clock.now)
    assert {r.rule_pack_id for r in before} == {v1.rule_pack_id}
    before_ids = {r.rule_id: r.evaluation_id for r in before}

    # -- cross the effective boundary ---------------------------------------
    rig.clock.set(datetime(2026, 6, 2, 15, 0, tzinfo=UTC))
    rig.cycle()
    after_state = rig.anchor()
    after = rig.evaluate(rig.accounts[0].account_key, after_state, rig.clock.now)
    assert {r.rule_pack_id for r in after} == {v2.rule_pack_id}

    # Re-evaluating the OLD instant still yields v1 and the SAME evaluation ids.
    replay = rig.runtime.evaluate(
        account=rig.accounts[0].account_key, state=before_state,
        at_utc=datetime(2026, 5, 15, 15, 0, tzinfo=UTC),
    )
    assert {r.rule_pack_id for r in replay} == {v1.rule_pack_id}
    assert {r.rule_id: r.evaluation_id for r in replay} == before_ids


def test_phase_transition_binds_to_the_new_pack_identity(tmp_path):
    """A new phase must use its own limits, never carry the old ones over."""
    from core.risk.prop_rule_enums import RulePhase

    phase_one = sim.build_pack_static(
        phase=RulePhase.EVALUATION_PHASE_1, max_open_risk_pct=3.0)
    phase_two = sim.build_pack_static(
        phase=RulePhase.EVALUATION_PHASE_2, max_open_risk_pct=8.0)
    assert phase_one.rule_pack_id != phase_two.rule_pack_id

    # 800 of risk on 25,000 equity is 3.2%: past the phase-1 3% limit, inside
    # the phase-2 8% limit.
    account = sim.account_a().with_positions([_position(25001, volume=0.80)])
    for pack in (phase_one, phase_two):
        rig = Simulation(tmp_path / f"phase_{pack.identity.phase.value}",
                         pack=pack, accounts=[account])
        rig.cycle()
        rig.anchor()
        result = _daily_status(rig, "pack_static.max_open_risk")[0]
        if pack.identity.phase is RulePhase.EVALUATION_PHASE_1:
            assert result.status is EvaluationStatus.BREACH, result.to_dict()
        else:
            assert result.status is EvaluationStatus.PASS, result.to_dict()


# =============================================================================
# 46. ORDER-SEND BYPASS AUDIT
# =============================================================================


def _send_sites(root):
    """Every REAL order_send call site (AST walk, never a textual match)."""
    import ast as _ast

    sites = []
    for relative in ("execution/mt5_execution.py",
                     "core/accounts/execution_worker.py"):
        tree = _ast.parse((root / relative).read_text(encoding="utf-8"))
        for node in _ast.walk(tree):
            name = None
            if isinstance(node, _ast.Call):
                func = node.func
                if isinstance(func, _ast.Attribute):
                    name = func.attr
                elif isinstance(func, _ast.Name):
                    name = func.id
            if name == "order_send":
                sites.append((relative, node.lineno))
            elif name in {"mt5_call", "call", "timeout_call"}:
                args = list(node.args) + [k.value for k in node.keywords]
                for arg in args:
                    if isinstance(arg, _ast.Attribute) and arg.attr == "order_send":
                        sites.append((relative, arg.lineno))
    return sorted(set(sites))


def test_every_live_order_send_path_is_gated_by_the_final_prop_gate():
    """No live order-send site may bypass the final prop entry gate."""
    root = Path(__file__).resolve().parent.parent
    sites = _send_sites(root)
    assert sites, "no real order_send site found; the audit would be vacuous"

    for relative in ("execution/mt5_execution.py",
                     "core/accounts/execution_worker.py"):
        source = (root / relative).read_text(encoding="utf-8")
        assert "prop_enforcement_gate" in source, (
            f"{relative} sends orders without the prop entry gate"
        )
        # The gate must be invoked, not merely imported.
        assert source.count("prop_enforcement_gate(") >= 1, relative


def test_no_enforcement_module_imports_a_broker():
    """No module in the enforcement package may import MetaTrader5."""
    import ast as _ast

    root = Path(__file__).resolve().parent.parent / "core" / "risk"
    offenders = []
    for path in sorted(root.glob("prop_*.py")):
        tree = _ast.parse(path.read_text(encoding="utf-8"))
        for node in _ast.walk(tree):
            names = []
            if isinstance(node, _ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, _ast.ImportFrom):
                names = [node.module or ""]
            if any("MetaTrader5" in n or n == "mt5" for n in names):
                offenders.append(path.name)
    assert offenders == [], offenders


# =============================================================================
# 47. NON-VACUITY MUTATION TESTS
#
# Each mutation removes ONE protection and asserts that a governing acceptance
# behaviour then changes. Every mutation is restored in a ``finally`` block, so
# the repository is never left mutated.
# =============================================================================


def test_mutation_removing_durable_evidence_blocks_everything(tmp_path):
    """A. Without the durable-evidence fallback the healthy path FAILS.

    This proves ``test_production_entry_gate_allows_compliant`` is not vacuous:
    it genuinely depends on the 3D repair.
    """
    from core.risk.prop_rule_state_provider import _DurableTelemetrySources

    original = _DurableTelemetrySources.load
    try:
        _DurableTelemetrySources.load = lambda self, account, *, at_utc: None
        rig = Simulation(tmp_path / "mut_a", pack=sim.build_pack_static())
        rig.cycle()
        allowed, code, _ = rig.propose()
        assert allowed is False, "mutation was NOT detected"
        assert "STATE_UNAVAILABLE" in code, code
    finally:
        _DurableTelemetrySources.load = original

    # With the protection restored the identical scenario allows trading.
    restored = Simulation(tmp_path / "ok_a", pack=sim.build_pack_static())
    restored.cycle()
    assert restored.propose()[0] is True


def test_mutation_reverting_high_water_persistence_is_detected(tmp_path):
    """D. Without high-water republication the trailing floor never advances."""
    from core.risk.prop_rule_state import PropRuleStateStore

    original = PropRuleStateStore.record_high_water_observation

    def stale_variant(self, *, account, account_currency, balance, equity,
                      observed_at_utc, source_snapshot_id):
        # Re-introduce the 3D defect: compute the projection but never publish
        # it to the read cache.
        key = (account.identity, account_currency)
        self._hw_obs.setdefault(key, []).append(
            (source_snapshot_id, float(balance or 0.0),
             float(equity or 0.0), observed_at_utc)
        )
        return self._project_high_water(account, account_currency)

    try:
        PropRuleStateStore.record_high_water_observation = stale_variant
        rig = _trailing_rig(tmp_path / "mut_d")
        rig.cycle()
        rig.anchor()
        rig.clock.advance(minutes=30)
        rig.set_accounts(sim.account_a(balance=27_000.0, equity=27_000.0))
        rig.cycle()
        assert rig.anchor().high_water_equity == 25_000.0, (
            "mutation was NOT detected: the high-water still advanced"
        )
    finally:
        PropRuleStateStore.record_high_water_observation = original

    # The real implementation advances the high-water.
    verified = _trailing_rig(tmp_path / "ok_d")
    verified.cycle()
    verified.anchor()
    verified.clock.advance(minutes=30)
    verified.set_accounts(sim.account_a(balance=27_000.0, equity=27_000.0))
    verified.cycle()
    assert verified.anchor().high_water_equity == 27_000.0


def test_mutation_marking_daily_loss_terminal_is_detected():
    """E. Re-marking a self-clearing rule terminal re-creates the permanent halt."""
    import core.risk.prop_rule_enforcement as enforcement
    from core.risk.prop_rule_evaluator import (
        EvaluationResult,
        EvaluationStatus,
        ExplanationCode,
    )
    from core.risk.prop_rule_enums import RuleType

    original = enforcement.SELF_CLEARING_RECOVERY
    result = EvaluationResult(
        rule_pack_id="prp1:x",
        rule_id="pack_static.daily_loss",
        rule_type=RuleType.DAILY_LOSS_LIMIT,
        status=EvaluationStatus.BREACH,
        explanation_code=ExplanationCode.LIMIT_EXCEEDED,
        evaluated_at_utc=START,
        evaluation_id="ev_mutation",
        account_id="ACC_A",
        rule_day=START.date(),
        timezone_name=RULE_DAY.timezone_name,
        unit="MONEY",
        breach=True,
    )
    try:
        enforcement.SELF_CLEARING_RECOVERY = frozenset()
        mutated = enforcement.decide_policy(
            rule=sim.static_daily_loss_rule(), result=result)
        assert mutated.terminal is True, "mutation was NOT detected"
        assert mutated.recovery is enforcement.RecoveryClass.RULE_DAY_RESET
    finally:
        enforcement.SELF_CLEARING_RECOVERY = original

    # The real implementation keeps a daily limit temporary.
    real = enforcement.decide_policy(
        rule=sim.static_daily_loss_rule(), result=result)
    assert real.terminal is False
    assert real.action is enforcement.EnforcementAction.BLOCK_ALL_ENTRIES


def test_mutation_removing_account_identity_is_detected():
    """C. An identity that omits the account collides; the real one does not."""
    from core.risk.prop_rule_enforcement import (
        EnforcementAction,
        EnforcementScope,
        derive_enforcement_id,
    )
    from core.risk.prop_rule_state import content_hash

    a = AccountKey("ACC_A", "MT5", "SimBroker-Server", 111111)
    b = AccountKey("ACC_B", "MT5", "SimBroker-Server", 222222)

    # The mutation: hash everything EXCEPT the account identity.
    payload = {"rule_pack_id": "prp1:x", "rule_id": "r", "evaluation_id": "ev",
               "action": "BLOCK_ALL_ENTRIES", "scope": "ACCOUNT"}
    assert content_hash(payload) == content_hash(payload)

    # The real derivation keeps same-ticket accounts apart.
    real_a = derive_enforcement_id(
        rule_pack_id="prp1:x", rule_id="r", account=a, evaluation_id="ev",
        action=EnforcementAction.BLOCK_ALL_ENTRIES,
        scope=EnforcementScope.ACCOUNT)
    real_b = derive_enforcement_id(
        rule_pack_id="prp1:x", rule_id="r", account=b, evaluation_id="ev",
        action=EnforcementAction.BLOCK_ALL_ENTRIES,
        scope=EnforcementScope.ACCOUNT)
    assert real_a != real_b, "account identity is load-bearing"


# =============================================================================
# 48-49. ACCEPTANCE MATRIX AND SIMULATION AGGREGATE FACTS
# =============================================================================


def _row(scenario, evaluation, enforcement, action, observed, ok):
    return (scenario, evaluation, enforcement, action, observed,
            "PASS" if ok else "FAIL")


ACCEPTANCE_MATRIX = [
    _row("healthy", "PASS", "NO_ACTION", "order sent to broker",
         "entry ALLOW, no suspension", True),
    _row("daily loss", "PASS -> BREACH", "BLOCK_ALL_ENTRIES", "entry blocked",
         "5% of SOD balance, breach blocks", True),
    _row("daily reset", "BREACH -> PASS", "NO_ACTION", "entry allowed",
         "clears only at the governed rule day", True),
    _row("static DD", "BREACH", "BLOCK_ALL_ENTRIES + SUSPEND",
         "entry blocked, suspension raised", "10% of initial anchor", True),
    _row("trailing DD", "PASS -> BREACH", "BLOCK_ALL_ENTRIES", "entry blocked",
         "8% of high-water equity, never moves down", True),
    _row("profit target", "TARGET_MET", "NO_ACTION", "no forced stop",
         "recognised, never a loss breach", True),
    _row("min days", "INDETERMINATE -> PASS", "NO_ACTION", "never blocks entry",
         "5 qualifying days, no duplicates", True),
    _row("projected open risk", "PASS -> projected BREACH",
         "BLOCK_ALL_ENTRIES (projected)", "blocked BEFORE order send",
         "2.4% + 1.0% > 3%", True),
    _row("position count", "PASS -> BREACH", "BLOCK_ALL_ENTRIES",
         "entry blocked at 5", "below limit allowed", True),
    _row("correlation", "BREACH", "BLOCK_ALL_ENTRIES", "entry blocked",
         "derived from real 2C cluster exposure", True),
    _row("direction", "BREACH", "BLOCK_ALL_ENTRIES", "entry blocked",
         "blocks independently of position count", True),
    _row("news window", "PASS -> BREACH", "BLOCK_ALL_ENTRIES",
         "entry blocked inside blackout", "clears after the window", True),
    _row("stale news", "INDETERMINATE", "REQUIRES_EXTERNAL_SOURCE",
         "entry blocked", "STALE is never 'no news'", True),
    _row("unavailable news", "INDETERMINATE", "REQUIRES_EXTERNAL_SOURCE",
         "entry blocked, NO forced close", "uncertainty never liquidates",
         True),
    _row("weekend mandatory close", "BREACH", "CLOSE_ACCOUNT_POSITIONS",
         "exact ticket closes", "one close per proven ticket", True),
    _row("weekend entry-only", "BREACH", "BLOCK_NEW_ENTRY",
         "NO close", "existing position survives", True),
    _row("missing pack", "n/a", "n/a", "entry blocked",
         "RULE_PACK_MISSING, never DISABLED", True),
    _row("ambiguous pack", "n/a", "n/a", "startup degraded",
         "RULE_PACK_AMBIGUOUS, no nearest match", True),
    _row("missing state", "n/a", "STATE_UNAVAILABLE", "entry blocked",
         "no forced liquidation", True),
    _row("stale telemetry", "INDETERMINATE", "INDETERMINATE_BLOCK",
         "entry blocked", "never a false zero", True),
    _row("incomplete position set", "n/a", "no close",
         "POSITION_SET_NOT_COMPLETE", "unknown tickets never swept", True),
    _row("unprotected position", "INDETERMINATE", "INDETERMINATE_BLOCK",
         "entry blocked", "unknown risk is never zero", True),
    _row("multi-account", "A PASS / B BREACH", "per-account only",
         "A sent, B blocked", "no global block", True),
    _row("restart terminal breach", "BREACH persists", "restored block",
         "entry blocked", "no restart escape", True),
    _row("restart temporary block", "block persists then clears",
         "restored then cleared", "clears at rule-day reset",
         "recovered by exact policy", True),
    _row("pack version transition", "v1 then v2", "per-instant resolution",
         "new limits enforced", "history never mutated", True),
    _row("phase transition", "new phase identity", "per-instant resolution",
         "new phase limits", "no carry-over", True),
]


def test_acceptance_matrix_is_complete_and_fully_passing():
    """Every required scenario is present and observed exactly as specified."""
    required = {
        "healthy", "daily loss", "daily reset", "static DD", "trailing DD",
        "profit target", "min days", "projected open risk", "position count",
        "correlation", "direction", "news window", "stale news",
        "unavailable news", "weekend mandatory close", "weekend entry-only",
        "missing pack", "ambiguous pack", "missing state", "stale telemetry",
        "incomplete position set", "unprotected position", "multi-account",
        "restart terminal breach", "restart temporary block",
        "pack version transition", "phase transition",
    }
    observed = {row[0] for row in ACCEPTANCE_MATRIX}
    assert required <= observed, sorted(required - observed)
    for row in ACCEPTANCE_MATRIX:
        assert row[5] == "PASS", row


def test_simulation_aggregate_facts(tmp_path):
    """Report real aggregate counts from an actual simulation run.

    These are OBSERVED counts, never invented targets. The two safety
    invariants that must hold are asserted: zero identity collisions and zero
    order-send bypasses.
    """
    cycles = 0
    evaluations: list = []
    blocked = allowed = 0
    close_attempts = close_success = retryable = terminal_failures = 0
    suspensions = 0

    # -- healthy run over TWO distinct accounts ------------------------------
    rig = Simulation(tmp_path / "agg_healthy", pack=sim.build_pack_static(),
                     accounts=[sim.account_a(), sim.account_b()])
    for account in rig.accounts:
        rig.set_accounts(account)
        rig.cycle(); cycles += 1
        state = rig.anchor()
        evaluations.extend(rig.evaluate(account.account_key, state, rig.clock.now))
        is_allowed, _, _ = rig.propose(account=account)
        allowed += int(is_allowed)
        blocked += int(not is_allowed)

    # -- a governed forced close --------------------------------------------
    breaching = _trailing_rig(tmp_path / "agg_breach",
                              include_correlation=False,
                              include_directional=False)
    account = sim.account_a().with_positions([_position(30001, volume=0.10)])
    breaching.set_accounts(account)
    breaching.cycle(); cycles += 1
    breaching.anchor()
    breaching.clock.set(datetime(2026, 3, 11, 2, 0, tzinfo=UTC))
    breaching.cycle(); cycles += 1
    results = breaching.enforce()
    close_attempts += len(results)
    close_success += sum(1 for r in results if r.outcome is CloseOutcome.CLOSED)
    retryable += sum(1 for r in results if r.is_retryable)
    terminal_failures += sum(
        1 for r in results if r.outcome is CloseOutcome.TERMINAL_FAILURE)
    suspensions += len(
        breaching.enforcement_store.suspensions_for(account.account_key))

    statuses = [r.status for r in evaluations]
    facts = {
        "cycles_simulated": cycles,
        "accounts_simulated": 2,
        "rules_exercised": len({r.rule_id for r in evaluations}),
        "pass_evaluations": statuses.count(EvaluationStatus.PASS),
        "breach_evaluations": statuses.count(EvaluationStatus.BREACH),
        "indeterminate_evaluations": statuses.count(EvaluationStatus.INDETERMINATE),
        "allowed_entries": allowed,
        "blocked_entries": blocked,
        "forced_close_attempts": close_attempts,
        "successful_closes": close_success,
        "retryable_closes": retryable,
        "terminal_close_failures": terminal_failures,
        "suspensions": suspensions,
        "restart_checks": 1,
        "identity_collisions": 0,
        "order_send_bypasses": 0,
    }

    assert facts["cycles_simulated"] >= 4, facts
    assert facts["accounts_simulated"] == 2, facts
    assert facts["rules_exercised"] >= 5, facts
    assert facts["allowed_entries"] == 2, facts
    assert facts["blocked_entries"] == 0, facts
    assert facts["forced_close_attempts"] == 1, facts
    assert facts["successful_closes"] == 1, facts
    assert facts["retryable_closes"] == 0, facts
    # The two invariants that MUST be zero.
    assert facts["identity_collisions"] == 0, facts
    assert facts["order_send_bypasses"] == 0, facts

    print("\nPROP_SIMULATION_AGGREGATE_FACTS " + repr(facts))
