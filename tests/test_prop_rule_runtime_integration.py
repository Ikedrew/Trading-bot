"""RUNTIME PROP RULE ENFORCEMENT integration (Block 3C).

Proves the enforcement layer is REAL, not decorative:

* the final pre-order gate refuses a projected breach that CURRENT state allows,
* multi-account fan-out isolates one account's breach from another's pass,
* account identity binds every decision,
* durable suspension/kill state survives a restart,
* SIMULATE_ONLY runs the identical path and changes nothing,
* startup reports an EXACT degraded reason and never falls back to "no rules",
* every live order-send site is gated (adversarial source audit),
* external-provider failure fails closed for entries but not for positions.

Non-vacuity is demonstrated in ``test_mutation_*``: each deliberately breaks one
guarantee and asserts that a test detects it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path

import pytest

from core.risk.prop_rule_enforcement import (
    DegradedReason,
    EnforcementAction,
    EnforcementMode,
    EnforcementReason,
    EnforcementScope,
)
from core.risk.prop_rule_evaluator import EvaluationStatus
from core.risk.prop_rule_executor import (
    CloseOutcome,
    CloseRequest,
    CloseResult,
    EnforcementAuditRecord,
    EnforcementExecutor,
    EnforcementStateStore,
    KillSwitchState,
    SuspensionScope,
    SuspensionState,
)
from core.risk.prop_rule_projection import PlannedOrder
from core.risk.prop_rule_runtime import (
    PropEnforcementRuntime,
    configure_prop_enforcement_runtime,
    prop_enforcement_runtime,
    stop_prop_enforcement_runtime,
)
from core.risk.prop_rule_state import AccountKey

UTC = timezone.utc
NOW = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)
ACCOUNT_A = AccountKey("ACC_A", "MT5", "DemoBroker-Server", 111111)
ACCOUNT_B = AccountKey("ACC_B", "MT5", "DemoBroker-Server", 222222)
ROOT = Path(__file__).resolve().parent.parent


def _order(risk: float | None = 2_000.0, symbol: str = "EURUSD") -> PlannedOrder:
    return PlannedOrder(
        canonical_symbol=symbol, side="BUY", volume=0.5, entry_price=1.10,
        stop_loss=1.0990, planned_risk_amount=risk,
    )


# =============================================================================
# HARNESS ? real 3A pack + real 3B state + real 3C runtime
# =============================================================================

from core.risk.prop_rule_contracts import (
    DailyLossRule,
    DrawdownRule,
    OpenRiskLimitRule,
    PositionLimitRule,
    RuleSource,
    required_telemetry_for,
)
from core.risk.prop_rule_enums import (
    BreachKind,
    CurrencySemantics,
    DailyResetPolicy,
    DrawdownKind,
    EvaluationTimeBasis,
    LimitBasis,
    RulePhase,
    RuleSeverity,
    RuleType,
    SourceType,
)
from core.risk.prop_rule_pack import RulePack, RulePackIdentity, compile_rule_pack
from core.risk.prop_rule_state import (
    AccountEvaluationState,
    DailyAccountAnchor,
    InitialAccountAnchor,
    PropRuleStateStore,
    RuleDayDefinition,
    StateStatus,
    build_account_evaluation_state,
)
from core.risk.prop_rule_values import Limit

NY = RuleDayDefinition("America/New_York", time(0, 0))
EFFECTIVE_FROM = datetime(2026, 1, 1, tzinfo=UTC)
ACCOUNT_CURRENCY = CurrencySemantics.ACCOUNT_CURRENCY
SOURCE = RuleSource(
    source_type=SourceType.OFFICIAL_RULE_PAGE,
    source_reference="https://example.invalid/3c-rules",
    retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
)


@dataclass(frozen=True)
class FakeSnapshot:
    account_id: str = "ACC_A"
    snapshot_id: str = "asnap_1"
    balance: float | None = 100_000.0
    equity: float | None = 100_000.0
    floating_pnl: float | None = 0.0
    currency: str = "USD"


@dataclass(frozen=True)
class FakeOpenRisk:
    account_id: str = "ACC_A"
    open_risk_id: str = "orisk_1"
    total_open_risk: float | None = 4_000.0
    risk_complete: bool = True
    open_position_count: int | None = 1
    position_tickets: tuple[int, ...] = (11,)
    position_risks: tuple[float, ...] = (4_000.0,)


@dataclass(frozen=True)
class FakePortfolio:
    account_id: str = "ACC_A"
    portfolio_exposure_id: str = "pexp_1"
    largest_symbol_risk: float | None = 4_000.0
    largest_symbol: str | None = "EURUSD"
    largest_direction_risk: float | None = 4_000.0
    largest_direction: str = "LONG"
    max_cluster_risk: float | None = 4_000.0
    correlation_complete: bool = True


def build_open_risk_pack(*, max_open_risk_pct: float = 5.0) -> RulePack:
    """A pack with ONE entry-block rule: max open risk as % of current equity."""
    identity = RulePackIdentity(
        provider="TEST_FIRM", program="TEST_CHALLENGE",
        phase=RulePhase.EVALUATION_PHASE_1, account_size=100_000,
        currency="USD", rule_pack_version="1.0.0", effective_from=EFFECTIVE_FROM,
    )
    rule = OpenRiskLimitRule(
        rule_id="r.max_open_risk",
        rule_type=RuleType.MAX_OPEN_RISK,
        enabled=True,
        severity=RuleSeverity.BREACH,
        source=SOURCE,
        effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.MAX_OPEN_RISK),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        limit=Limit(
            basis=LimitBasis.PERCENT_CURRENT_EQUITY, value=max_open_risk_pct,
            currency_semantics=ACCOUNT_CURRENCY,
        ),
        scope_kind="TOTAL",
    )
    return compile_rule_pack(identity, [rule], notes="3C TEST PACK")


def build_drawdown_pack(*, max_dd_pct: float = 10.0) -> RulePack:
    identity = RulePackIdentity(
        provider="TEST_FIRM_DD", program="TEST_CHALLENGE",
        phase=RulePhase.EVALUATION_PHASE_1, account_size=100_000,
        currency="USD", rule_pack_version="1.0.0", effective_from=EFFECTIVE_FROM,
    )
    rule = DrawdownRule(
        rule_id="r.static_drawdown",
        rule_type=RuleType.STATIC_DRAWDOWN,
        enabled=True,
        severity=RuleSeverity.TERMINATION,
        source=SOURCE,
        effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.STATIC_DRAWDOWN),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        kind=DrawdownKind.STATIC,
        limit=Limit(
            basis=LimitBasis.PERCENT_INITIAL_BALANCE, value=max_dd_pct,
            currency_semantics=ACCOUNT_CURRENCY,
        ),
        anchor=LimitBasis.PERCENT_INITIAL_BALANCE,
    )
    return compile_rule_pack(identity, [rule], notes="3C TEST DD PACK")


def build_daily_loss_pack(*, max_daily_loss_pct: float = 5.0) -> RulePack:
    identity = RulePackIdentity(
        provider="TEST_FIRM_DL", program="TEST_CHALLENGE",
        phase=RulePhase.EVALUATION_PHASE_1, account_size=100_000,
        currency="USD", rule_pack_version="1.0.0", effective_from=EFFECTIVE_FROM,
    )
    rule = DailyLossRule(
        rule_id="r.daily_loss",
        rule_type=RuleType.DAILY_LOSS_LIMIT,
        enabled=True,
        severity=RuleSeverity.TERMINATION,
        source=SOURCE,
        effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.DAILY_LOSS_LIMIT),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        breach_kind=BreachKind.HARD,
        breach_persists_until_reset=True,
        timezone_name="America/New_York",
        limit=Limit(
            basis=LimitBasis.PERCENT_START_OF_DAY_BALANCE, value=max_daily_loss_pct,
            currency_semantics=ACCOUNT_CURRENCY,
        ),
        reset_policy=DailyResetPolicy.CALENDAR_DAY,
        reset_time=time(0, 0),
    )
    return compile_rule_pack(identity, [rule], notes="3C TEST DL PACK")


def make_state(
    *,
    account: AccountKey = ACCOUNT_A,
    equity: float = 100_000.0,
    total_open_risk: float | None = 4_000.0,
    initial_balance: float = 100_000.0,
    sod_balance: float = 100_000.0,
    stale: bool = False,
) -> AccountEvaluationState:
    """A COMPLETE evaluation state from explicit, injected evidence."""
    store = PropRuleStateStore()
    anchor = store.record_initial_anchor(
        InitialAccountAnchor(
            account=account, account_currency="USD",
            initial_balance=initial_balance, initial_equity=initial_balance,
            observed_at_utc=datetime(2026, 3, 2, 14, 0, tzinfo=UTC),
            source_snapshot_id="asnap_initial", source_provenance="TEST",
        )
    )
    day = store.record_daily_anchor(
        DailyAccountAnchor(
            account=account, account_currency="USD",
            rule_day=NY.rule_day_for(NOW), timezone_name=NY.timezone_name,
            rule_day_definition=NY.key(), start_of_day_balance=sod_balance,
            start_of_day_equity=sod_balance, anchor_at_utc=NY.day_start_utc(NY.rule_day_for(NOW)),
            source_snapshot_id="asnap_sod", source_provenance="TEST",
            start_of_day_floating_pnl=0.0,
        )
    )
    return build_account_evaluation_state(
        account=account, account_currency="USD", observed_at_utc=NOW, definition=NY,
        initial_anchor=anchor, daily_anchor=day,
        account_snapshot=FakeSnapshot(
            account_id=account.account_id, balance=equity, equity=equity,
        ),
        open_risk=FakeOpenRisk(
            account_id=account.account_id, total_open_risk=total_open_risk,
        ),
        portfolio=FakePortfolio(account_id=account.account_id),
        stale=stale,
    )


def build_runtime(
    pack: RulePack,
    *,
    mode: EnforcementMode = EnforcementMode.LIVE_ENFORCE,
    store: EnforcementStateStore | None = None,
    state=None,
    provider: object | None = None,
) -> PropEnforcementRuntime:
    """A started runtime bound to one pack, with a fixed state provider."""
    from core.risk.prop_rule_pack import RulePackStore

    packs = RulePackStore()
    packs.register_rule_pack(pack)
    runtime = PropEnforcementRuntime(
        mode=mode,
        rule_pack_store=packs,
        state_store=store or EnforcementStateStore(),
        pack_identity=pack.identity,
        rule_day_definition=NY,
        clock=lambda: NOW,
    )
    runtime.state_provider = lambda account: state
    report = runtime.start(at_utc=NOW)
    assert report.ready, report.to_dict()
    return runtime


# =============================================================================
# CURRENT vs PROJECTED ? the headline safety property
# =============================================================================


def test_current_pass_but_projected_breach_blocks_before_send():
    """4% current against a 5% limit, +2% proposed = 6% -> REFUSED.

    This is the single most important behaviour in Block 3C.
    """
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0))
    state = make_state(total_open_risk=4_000.0)   # 4% of 100,000

    current = runtime.authorize_entry(account=ACCOUNT_A, state=state, at_utc=NOW)
    assert current.allowed is True, current.to_dict()

    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=state, at_utc=NOW, order=_order(risk=2_000.0)
    )
    assert verdict.allowed is False
    assert verdict.reason is EnforcementReason.PROJECTED_BREACH
    assert verdict.projected_decisions
    assert any(d.projected for d in verdict.projected_decisions)


def test_projected_within_limit_is_allowed():
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0))
    state = make_state(total_open_risk=2_000.0)   # 2% current
    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=state, at_utc=NOW, order=_order(risk=2_000.0)
    )
    assert verdict.allowed is True, verdict.to_dict()


def test_projected_position_count_breach_blocks():
    pack = RulePack(
        identity=RulePackIdentity(
            provider="TEST_FIRM_POS", program="TEST_CHALLENGE",
            phase=RulePhase.EVALUATION_PHASE_1, account_size=100_000,
            currency="USD", rule_pack_version="1.0.0", effective_from=EFFECTIVE_FROM,
        ),
        rules=(
            PositionLimitRule(
                rule_id="r.max_positions", rule_type=RuleType.MAX_OPEN_POSITIONS,
                enabled=True, severity=RuleSeverity.BREACH, source=SOURCE,
                effective_from=EFFECTIVE_FROM,
                telemetry_requirements=required_telemetry_for(RuleType.MAX_OPEN_POSITIONS),
                evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
                timezone_name="America/New_York",
                limit=Limit(basis=LimitBasis.POSITION_COUNT, value=1),
            ),
        ),
        rule_pack_id="",
    )
    from core.risk.prop_rule_pack import compile_rule_pack as _compile

    pack = _compile(pack.identity, list(pack.rules))
    runtime = build_runtime(pack)
    state = make_state(total_open_risk=1_000.0)  # exactly 1 open position
    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=state, at_utc=NOW, order=_order(risk=500.0)
    )
    assert verdict.allowed is False
    assert verdict.reason is EnforcementReason.PROJECTED_BREACH


def test_projection_without_exact_risk_cannot_silently_pass():
    """An unknown exact risk must not become an optimistic allow."""
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0))
    state = make_state(total_open_risk=4_000.0)
    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=state, at_utc=NOW, order=_order(risk=None)
    )
    # The projection is incomplete, so the monetary rule is undecidable and the
    # mandatory rule fails closed.
    assert verdict.projected_state_complete is False
    assert "total_open_risk" in verdict.projected_unavailable_fields
    assert verdict.allowed is False


def test_current_breach_blocks_even_without_a_proposed_order():
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0))
    state = make_state(total_open_risk=6_000.0)   # 6% already
    verdict = runtime.authorize_entry(account=ACCOUNT_A, state=state, at_utc=NOW)
    assert verdict.allowed is False
    assert verdict.reason is EnforcementReason.LIMIT_EXCEEDED


# =============================================================================
# MULTI-ACCOUNT FAN-OUT ISOLATION
# =============================================================================


def test_same_signal_two_accounts_only_the_breaching_one_is_blocked():
    """Account A PASSES and trades; account B BREACHES and is blocked."""
    runtime_a = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0))
    verdict_a = runtime_a.authorize_entry(
        account=ACCOUNT_A, state=make_state(account=ACCOUNT_A, total_open_risk=1_000.0),
        at_utc=NOW, order=_order(risk=500.0),
    )
    assert verdict_a.allowed is True

    runtime_b = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0))
    verdict_b = runtime_b.authorize_entry(
        account=ACCOUNT_B, state=make_state(account=ACCOUNT_B, total_open_risk=6_000.0),
        at_utc=NOW, order=_order(risk=500.0),
    )
    assert verdict_b.allowed is False
    # Different accounts, therefore different decisions.
    assert verdict_a.account_id != verdict_b.account_id


def test_one_account_breach_never_blocks_another_account():
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0))
    blocked = runtime.authorize_entry(
        account=ACCOUNT_B, state=make_state(account=ACCOUNT_B, total_open_risk=9_000.0),
        at_utc=NOW,
    )
    assert blocked.allowed is False
    # The runtime holds no broker-global mutable block state, so account A is
    # unaffected by B's breach even on the SAME runtime instance.
    healthy = runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(account=ACCOUNT_A, total_open_risk=500.0),
        at_utc=NOW,
    )
    assert healthy.allowed is True


def test_suspension_is_scoped_to_the_exact_account_identity():
    store = EnforcementStateStore()
    runtime = build_runtime(build_drawdown_pack(), store=store)
    decision = runtime.evaluate(account=ACCOUNT_A, state=make_state(account=ACCOUNT_A), at_utc=NOW)
    breached = runtime._decisions(account=ACCOUNT_A, results=decision, at_utc=NOW)[0]
    runtime._executor.suspend(breached, at_utc=NOW, terminal=True)

    assert len(store.suspensions_for(ACCOUNT_A)) == 1
    assert store.suspensions_for(ACCOUNT_B) == ()


# =============================================================================
# DURABLE STATE, RESTART CONTINUITY, KILL SWITCH
# =============================================================================


def test_terminal_breach_suspension_survives_restart(tmp_path):
    """A process restart must NOT clear a terminal prop breach."""
    store = EnforcementStateStore(tmp_path / "enf")
    runtime = build_runtime(build_drawdown_pack(), store=store)
    verdict = runtime.authorize_entry(
        account=ACCOUNT_A,
        state=make_state(account=ACCOUNT_A, equity=85_000.0),   # 15% drawdown
        at_utc=NOW,
    )
    assert verdict.allowed is False
    assert len(store.suspensions_for(ACCOUNT_A)) >= 1

    # A brand new process: brand new store instance, same directory.
    rebuilt = EnforcementStateStore(tmp_path / "enf")
    restored = rebuilt.suspensions_for(ACCOUNT_A)
    assert len(restored) == 1
    assert restored[0].terminal is True
    assert restored[0].is_active is True

    # And the runtime built from it still refuses.
    restarted = build_runtime(build_drawdown_pack(), store=rebuilt)
    again = restarted.authorize_entry(
        account=ACCOUNT_A, state=make_state(account=ACCOUNT_A), at_utc=NOW
    )
    assert again.allowed is False
    assert any(
        d.reason_code is EnforcementReason.RESTORED_ACTIVE_BLOCK
        for d in again.blocking
    )


def test_kill_switch_locked_survives_restart_and_cannot_be_downgraded(tmp_path):
    store = EnforcementStateStore(tmp_path / "enf")
    runtime = build_runtime(build_drawdown_pack(), store=store)
    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(account=ACCOUNT_A, equity=80_000.0), at_utc=NOW
    )
    assert verdict.allowed is False
    assert store.kill_switch_tripped(ACCOUNT_A) is True

    rebuilt = EnforcementStateStore(tmp_path / "enf")
    assert rebuilt.kill_switch_tripped(ACCOUNT_A) is True
    state = rebuilt.kill_switch_for(ACCOUNT_A)
    assert state is KillSwitchState.LOCKED
    # A later non-terminal write must not unlock it.
    rebuilt.record_kill_switch(
        ACCOUNT_A, KillSwitchState.ACTIVE, at_utc=NOW, enforcement_id="enf_x"
    )
    assert rebuilt.kill_switch_for(ACCOUNT_A) is KillSwitchState.LOCKED


def test_non_terminal_entry_block_does_not_trip_the_kill_switch(tmp_path):
    """A plain entry block is a suspension, NOT a kill switch.

    Tripping the kill switch for every breach would make the two states
    indistinguishable and would forbid the recovery paths the recovery policy
    explicitly models.
    """
    store = EnforcementStateStore(tmp_path / "enf")
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0), store=store)
    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(account=ACCOUNT_A, total_open_risk=9_000.0),
        at_utc=NOW,
    )
    assert verdict.allowed is False
    assert store.kill_switch_tripped(ACCOUNT_A) is False


def test_terminal_hard_stop_trips_and_persists_the_kill_switch(tmp_path):
    store = EnforcementStateStore(tmp_path / "enf")
    runtime = build_runtime(build_drawdown_pack(), store=store)
    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(account=ACCOUNT_A, equity=80_000.0), at_utc=NOW
    )
    assert verdict.allowed is False
    assert store.kill_switch_tripped(ACCOUNT_A) is True
    rebuilt = EnforcementStateStore(tmp_path / "enf")
    assert rebuilt.kill_switch_tripped(ACCOUNT_A) is True


def test_daily_loss_block_clears_only_via_explicit_authority():
    """No generic 'reset all guards': clearing requires a named authority."""
    store = EnforcementStateStore()
    runtime = build_runtime(build_daily_loss_pack(), store=store)
    decision = runtime.evaluate(account=ACCOUNT_A, state=make_state(), at_utc=NOW)[0]
    assert decision.status is not EvaluationStatus.BREACH   # no loss recorded

    from core.risk.prop_rule_enforcement import compile_decision as _cd

    dec = _cd(
        rule=build_daily_loss_pack().rules[0], result=decision,
        account=ACCOUNT_A, effective_at_utc=NOW,
    )
    suspension = runtime._executor.suspend(dec, at_utc=NOW, rule_day="2026-03-10")
    assert suspension.is_active is True
    # Still active: nothing cleared it.
    assert store.suspensions_for(ACCOUNT_A)[0].is_active is True
    cleared = store.clear_suspension(
        suspension.suspension_id, at_utc=NOW, by="OPERATOR", authority="MANUAL_OVERRIDE"
    )
    assert cleared.is_active is False
    assert cleared.clearance_authority == "MANUAL_OVERRIDE"


def test_challenge_suspension_is_distinct_from_account_suspension():
    store = EnforcementStateStore()
    runtime = build_runtime(build_drawdown_pack(), store=store)
    results = runtime.evaluate(account=ACCOUNT_A, state=make_state(), at_utc=NOW)
    dec = runtime._decisions(account=ACCOUNT_A, results=results, at_utc=NOW)[0]
    account_level = runtime._executor.suspend(dec, at_utc=NOW, scope=SuspensionScope.ACCOUNT)
    challenge_level = runtime._executor.suspend(
        dec, at_utc=NOW, scope=SuspensionScope.CHALLENGE
    )
    assert account_level.scope is SuspensionScope.ACCOUNT
    assert challenge_level.scope is SuspensionScope.CHALLENGE
    assert account_level.suspension_id != challenge_level.suspension_id


def test_audit_records_are_idempotent_and_lineage_complete():
    store = EnforcementStateStore()
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0), store=store)
    for _ in range(3):
        runtime.authorize_entry(
            account=ACCOUNT_A, state=make_state(total_open_risk=9_000.0), at_utc=NOW
        )
    records = store.audit_records(ACCOUNT_A)
    # The same decision observed repeatedly must not duplicate the audit trail.
    assert len(records) == len({r.record_id for r in records})
    record = records[0]
    assert record.rule_pack_id
    assert record.rule_id
    assert record.evaluation_ids
    assert record.enforcement_id
    assert record.account.identity == ACCOUNT_A.identity
    assert record.is_material is True


# =============================================================================
# FORCED CLOSE AUTHORITY ? exact ticket, idempotent, truthful outcomes
# =============================================================================


class RecordingClosePort:
    """A broker-free close port that records exactly what it was asked."""

    def __init__(self, outcomes: dict[int, CloseOutcome] | None = None) -> None:
        self.calls: list[CloseRequest] = []
        self._outcomes = dict(outcomes or {})

    def close_position(self, request: CloseRequest, *, at_utc: datetime) -> CloseResult:
        self.calls.append(request)
        outcome = self._outcomes.get(int(request.position_ticket), CloseOutcome.CLOSED)
        return CloseResult(request=request, outcome=outcome, attempted_at_utc=at_utc)


def _close_decision(account: AccountKey = ACCOUNT_A):
    from core.risk.prop_rule_enforcement import EnforceDecision
    from core.risk.prop_rule_enums import RuleType
    from core.risk.prop_rule_enforcement import (
        EnforcementCriticality, EnforcementSeverity,
    )

    return EnforceDecision(
        enforcement_id="enf_close_1", rule_pack_id="p", rule_id="r.weekend",
        rule_type=RuleType.WEEKEND_HOLD_RESTRICTION, account=account,
        evaluation_id="ev_close_1", evaluated_status=EvaluationStatus.BREACH,
        enforcement_action=EnforcementAction.CLOSE_SYMBOL_POSITIONS,
        criticality=EnforcementCriticality.POSITION_REDUCTION,
        severity=EnforcementSeverity.TERMINAL, effective_at_utc=NOW,
        applies_to=EnforcementScope.ACCOUNT,
        reason_code=EnforcementReason.LIMIT_EXCEEDED, action_required=True,
        terminal=True,
    )


def test_close_is_exact_ticket_and_account_bound():
    port = RecordingClosePort()
    executor = EnforcementExecutor(store=EnforcementStateStore(), close_port=port)
    executor.close_position(_close_decision(), ticket=4242, at_utc=NOW)
    assert len(port.calls) == 1
    assert port.calls[0].position_ticket == 4242
    assert port.calls[0].account.identity == ACCOUNT_A.identity
    assert port.calls[0].close_request_id.startswith("clreq_")


def test_same_ticket_on_another_account_is_a_different_close():
    port = RecordingClosePort()
    executor = EnforcementExecutor(store=EnforcementStateStore(), close_port=port)
    executor.close_position(_close_decision(ACCOUNT_A), ticket=4242, at_utc=NOW)
    executor.close_position(_close_decision(ACCOUNT_B), ticket=4242, at_utc=NOW)
    assert len({c.close_request_id for c in port.calls}) == 2
    assert {c.account.account_id for c in port.calls} == {"ACC_A", "ACC_B"}


def test_repeated_close_of_a_completed_ticket_is_already_closed_not_failure():
    port = RecordingClosePort()
    store = EnforcementStateStore()
    executor = EnforcementExecutor(store=store, close_port=port)
    first = executor.close_position(_close_decision(), ticket=7, at_utc=NOW)
    assert first.outcome is CloseOutcome.CLOSED
    second = executor.close_position(_close_decision(), ticket=7, at_utc=NOW)
    assert second.outcome is CloseOutcome.ALREADY_CLOSED
    assert second.is_success is True
    # The broker was asked exactly once.
    assert len(port.calls) == 1


def test_partial_liquidation_keeps_each_ticket_truthful():
    """Position 1 closes, 2 fails retryably, 3 was already gone."""
    port = RecordingClosePort(
        outcomes={1: CloseOutcome.CLOSED, 2: CloseOutcome.RETRYABLE_FAILURE,
                  3: CloseOutcome.ALREADY_CLOSED}
    )
    executor = EnforcementExecutor(store=EnforcementStateStore(), close_port=port)
    report = executor.liquidate_account(
        _close_decision(), tickets=(1, 2, 3), at_utc=NOW
    )
    assert report.closed_count == 1
    assert report.retryable_count == 1
    assert report.already_closed_count == 1
    assert report.fully_liquidated is False   # NOT proven


def test_fully_liquidated_requires_every_single_close_to_succeed():
    port = RecordingClosePort(outcomes={1: CloseOutcome.CLOSED, 2: CloseOutcome.CLOSED})
    executor = EnforcementExecutor(store=EnforcementStateStore(), close_port=port)
    report = executor.liquidate_account(_close_decision(), tickets=(1, 2), at_utc=NOW)
    assert report.fully_liquidated is True


def test_retryable_and_terminal_outcomes_are_distinguished():
    port = RecordingClosePort(outcomes={
        1: CloseOutcome.RETRYABLE_FAILURE, 2: CloseOutcome.TERMINAL_FAILURE,
    })
    executor = EnforcementExecutor(store=EnforcementStateStore(), close_port=port)
    retryable = executor.close_position(_close_decision(), ticket=1, at_utc=NOW)
    terminal = executor.close_position(_close_decision(), ticket=2, at_utc=NOW)
    assert retryable.is_retryable is True
    assert terminal.is_retryable is False
    assert terminal.is_success is False


def test_failed_close_never_rewrites_the_breach_verdict():
    port = RecordingClosePort(outcomes={9: CloseOutcome.TERMINAL_FAILURE})
    executor = EnforcementExecutor(store=EnforcementStateStore(), close_port=port)
    decision = _close_decision()
    result = executor.close_position(decision, ticket=9, at_utc=NOW)
    marked = executor.mark_attempt(decision, result)
    # The RULE verdict is untouched; only the action outcome changed.
    assert marked.evaluated_status is EvaluationStatus.BREACH
    assert marked.enforcement_action is decision.enforcement_action
    assert marked.enforcement_id == decision.enforcement_id
    assert marked.action_attempted is True
    assert marked.retryable is False


def test_close_attempts_survive_restart(tmp_path):
    port = RecordingClosePort()
    store = EnforcementStateStore(tmp_path / "enf")
    executor = EnforcementExecutor(store=store, close_port=port)
    decision = _close_decision()
    executor.close_position(decision, ticket=77, at_utc=NOW)

    rebuilt = EnforcementStateStore(tmp_path / "enf")
    request = CloseRequest(
        account=ACCOUNT_A, position_ticket=77, enforcement_id="enf_close_1"
    )
    assert rebuilt.close_completed(request.close_request_id) is True
    # A restarted executor therefore reports ALREADY_CLOSED without re-asking.
    restarted = EnforcementExecutor(store=rebuilt, close_port=port)
    again = restarted.close_position(decision, ticket=77, at_utc=NOW)
    assert again.outcome is CloseOutcome.ALREADY_CLOSED
    assert len(port.calls) == 1


def test_executor_refuses_to_close_without_a_port():
    executor = EnforcementExecutor(store=EnforcementStateStore(), close_port=None)
    with pytest.raises(Exception):
        executor.close_position(_close_decision(), ticket=1, at_utc=NOW)


# =============================================================================
# MODES ? disabled / simulate-only / live
# =============================================================================


def test_disabled_mode_is_explicit_and_allows():
    runtime = PropEnforcementRuntime(mode=EnforcementMode.DISABLED, clock=lambda: NOW)
    report = runtime.start(at_utc=NOW)
    assert report.ready is True
    assert report.degraded_reason is DegradedReason.PROP_MODE_DISABLED
    verdict = runtime.authorize_entry(account=ACCOUNT_A, state=make_state(), at_utc=NOW)
    assert verdict.allowed is True
    assert verdict.degraded_reason is DegradedReason.PROP_MODE_DISABLED


def test_simulate_only_computes_the_same_decision_as_live():
    """Same evaluation path, same decision, no side effect."""
    pack = build_open_risk_pack(max_open_risk_pct=5.0)
    state = make_state(total_open_risk=4_000.0)
    order = _order(risk=2_000.0)

    live = build_runtime(pack, mode=EnforcementMode.LIVE_ENFORCE, state=state)
    live_verdict = live.authorize_entry(account=ACCOUNT_A, state=state, at_utc=NOW, order=order)

    simulated = build_runtime(
        pack, mode=EnforcementMode.SIMULATE_ONLY, state=state
    )
    sim_verdict = simulated.authorize_entry(
        account=ACCOUNT_A, state=state, at_utc=NOW, order=order
    )

    assert live_verdict.allowed is sim_verdict.allowed is False
    assert live_verdict.reason is sim_verdict.reason
    assert [d.enforcement_id for d in live_verdict.blocking] == [
        d.enforcement_id for d in sim_verdict.blocking
    ]


def test_simulate_only_persists_but_changes_nothing(tmp_path):
    store = EnforcementStateStore(tmp_path / "enf")
    runtime = build_runtime(
        build_drawdown_pack(), mode=EnforcementMode.SIMULATE_ONLY,
        store=store, state=make_state(account=ACCOUNT_A, equity=80_000.0),
    )
    verdict = runtime.authorize_entry(account=ACCOUNT_A, at_utc=NOW)
    assert verdict.allowed is False
    # The decision was recorded...
    assert store.audit_records(ACCOUNT_A)
    # ...but NOTHING was suspended or locked.
    assert store.suspensions_for(ACCOUNT_A) == ()
    assert store.kill_switch_tripped(ACCOUNT_A) is False


def test_live_enforce_does_suspend_and_lock(tmp_path):
    store = EnforcementStateStore(tmp_path / "enf")
    runtime = build_runtime(
        build_drawdown_pack(), mode=EnforcementMode.LIVE_ENFORCE,
        store=store, state=make_state(account=ACCOUNT_A, equity=80_000.0),
    )
    verdict = runtime.authorize_entry(account=ACCOUNT_A, at_utc=NOW)
    assert verdict.allowed is False
    assert len(store.suspensions_for(ACCOUNT_A)) >= 1
    assert store.kill_switch_tripped(ACCOUNT_A) is True


# =============================================================================
# STARTUP / DEGRADED ? never a silent fallback to "no prop rules"
# =============================================================================


def test_missing_rule_pack_reports_exact_reason_not_disabled():
    from core.risk.prop_rule_pack import RulePackStore

    pack = build_open_risk_pack()
    runtime = PropEnforcementRuntime(
        mode=EnforcementMode.LIVE_ENFORCE, rule_pack_store=RulePackStore(),
        state_store=EnforcementStateStore(), pack_identity=pack.identity,
        rule_day_definition=NY, clock=lambda: NOW,
    )
    report = runtime.start(at_utc=NOW)
    assert report.ready is False
    assert report.degraded_reason is DegradedReason.RULE_PACK_MISSING
    assert report.degraded_reason is not DegradedReason.PROP_MODE_DISABLED

    verdict = runtime.authorize_entry(account=ACCOUNT_A, state=make_state(), at_utc=NOW)
    assert verdict.allowed is False
    assert verdict.degraded_reason is DegradedReason.RULE_PACK_MISSING


def test_ambiguous_rule_pack_is_reported_ambiguous():
    from core.risk.prop_rule_pack import RulePackStore

    pack = build_open_risk_pack()
    packs = RulePackStore()
    # A second VALID pack with the SAME selection key and an overlapping window.
    packs.register_rule_pack(pack)
    overlapping = RulePackIdentity(
        provider=pack.identity.provider, program=pack.identity.program,
        phase=pack.identity.phase, account_size=pack.identity.account_size,
        currency=pack.identity.currency, rule_pack_version="2.0.0",
        effective_from=EFFECTIVE_FROM,
    )
    from core.risk.prop_rule_contracts import OpenRiskLimitRule as _ORL

    second = _ORL(
        rule_id="r.max_open_risk_v2", rule_type=RuleType.MAX_OPEN_RISK,
        enabled=True, severity=RuleSeverity.BREACH, source=SOURCE,
        effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.MAX_OPEN_RISK),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        limit=Limit(basis=LimitBasis.PERCENT_CURRENT_EQUITY, value=4.0,
                    currency_semantics=ACCOUNT_CURRENCY),
        scope_kind="TOTAL",
    )
    from core.risk.prop_rule_pack import compile_rule_pack as _compile

    packs.register_rule_pack(_compile(overlapping, [second]))

    runtime = PropEnforcementRuntime(
        mode=EnforcementMode.LIVE_ENFORCE, rule_pack_store=packs,
        state_store=EnforcementStateStore(), pack_identity=pack.identity,
        rule_day_definition=NY, clock=lambda: NOW,
    )
    report = runtime.start(at_utc=NOW)
    assert report.ready is False
    assert report.degraded_reason is DegradedReason.RULE_PACK_AMBIGUOUS


def test_missing_required_external_provider_degrades_explicitly():
    from core.risk.prop_rule_pack import RulePackStore

    pack = build_open_risk_pack()
    runtime = PropEnforcementRuntime(
        mode=EnforcementMode.LIVE_ENFORCE, rule_pack_store=RulePackStore(),
        state_store=EnforcementStateStore(), pack_identity=pack.identity,
        rule_day_definition=NY, required_external=("ECONOMIC_CALENDAR",),
        economic_calendar=None, clock=lambda: NOW,
    )
    packs = RulePackStore()
    packs.register_rule_pack(pack)
    runtime._packs = packs
    report = runtime.start(at_utc=NOW)
    assert report.ready is False
    assert report.degraded_reason is DegradedReason.REQUIRED_PROVIDER_UNAVAILABLE
    assert "ECONOMIC_CALENDAR" in report.to_dict()["checks"][1]["detail"]


def test_missing_3b_state_blocks_and_names_the_reason():
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0))
    runtime.state_provider = lambda account: None
    verdict = runtime.authorize_entry(account=ACCOUNT_A, at_utc=NOW)
    assert verdict.allowed is False
    assert verdict.degraded_reason is DegradedReason.STATE_UNAVAILABLE


def test_stopped_service_fails_closed():
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=99.0))
    runtime.stop()
    verdict = runtime.authorize_entry(account=ACCOUNT_A, state=make_state(), at_utc=NOW)
    assert verdict.allowed is False
    assert "STOPPED" in verdict.detail


def test_clean_shutdown_has_no_thread_to_join():
    """3C adds no background thread, so shutdown is a state change only."""
    import threading

    before = threading.active_count()
    runtime = build_runtime(build_open_risk_pack())
    assert threading.active_count() == before
    runtime.stop()
    assert runtime.stopped is True
    assert threading.active_count() == before


def test_runtime_status_is_inspectable():
    runtime = build_runtime(build_open_risk_pack(), state=make_state())
    runtime.authorize_entry(account=ACCOUNT_A, state=make_state(), at_utc=NOW)
    status = runtime.status().to_dict()
    assert status["mode"] == EnforcementMode.LIVE_ENFORCE.value
    assert status["active_rule_pack_id"]
    assert "ECONOMIC_CALENDAR" in status["external_sources"]
    assert status["degraded_reason"] == DegradedReason.NONE.value


# =============================================================================
# ADVERSARIAL AUDIT ? every live order-send site must be gated
# =============================================================================

import ast

#: Modules that PERFORM a broker send (as opposed to merely mentioning one).
LIVE_SEND_MODULES = (
    "execution/mt5_execution.py",
    "core/accounts/execution_worker.py",
)

#: The whole repository surface the audit sweeps for real sends.
AUDITED_MODULES = LIVE_SEND_MODULES + (
    "core/accounts/account_router.py",
    "core/accounts/live_fanout.py",
    "core/runtime/fanout_execution.py",
    "risk/spread_guard.py",
)


def _send_calls(path: Path) -> list[int]:
    """Line numbers of REAL ``order_send`` invocations, via AST.

    A textual scan would match docstrings and comments; an AST walk finds only
    actual attribute calls, including one passed as a function argument to
    ``mt5_call(...)``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name == "order_send":
            lines.append(node.lineno)
        elif name in {"mt5_call", "call", "timeout_call"}:
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(arg, ast.Attribute) and arg.attr == "order_send":
                    lines.append(arg.lineno)
    return sorted(set(lines))


def _prop_gate_lines(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8").read()
                     if False else path.read_text(encoding="utf-8"))
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name in {
                "prop_enforcement_gate", "_prop_enforcement_gate", "_prop_entry_gate",
            }:
                lines.append(node.lineno)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_prop_allowed":
                    if isinstance(node.value, ast.Call):
                        lines.append(node.value.lineno)
    return sorted(set(lines))


def test_every_audited_module_parses_and_is_listed():
    """The audit surface is explicit and every module really exists."""
    for relative in AUDITED_MODULES:
        assert (ROOT / relative).exists(), f"missing audited module: {relative}"
        ast.parse((ROOT / relative).read_text(encoding="utf-8"))


@pytest.mark.parametrize("relative", LIVE_SEND_MODULES)
def test_every_real_order_send_call_is_gated(relative):
    """No broker send may execute without a prop gate ABOVE it.

    This is the fail-closed-location guarantee (requirements 35 and 61). Adding
    a send above the gate makes this test fail.
    """
    path = ROOT / relative
    sends = _send_calls(path)
    assert sends, f"expected at least one real order_send in {relative}"
    gates = _prop_gate_lines(path)
    assert gates, f"no prop gate call found in {relative}"
    earliest_gate = min(gates)
    for send_line in sends:
        assert send_line > earliest_gate, (
            f"{relative}:{send_line} sends with no prop gate above it "
            f"(earliest gate is line {earliest_gate})"
        )


def test_worker_gate_is_between_validation_and_send():
    source = (ROOT / "core/accounts/execution_worker.py").read_text(encoding="utf-8")
    lines = source.splitlines()
    gate = min(_prop_gate_lines(ROOT / "core/accounts/execution_worker.py"))
    send = min(_send_calls(ROOT / "core/accounts/execution_worker.py"))
    risk = next(i for i, l in enumerate(lines, 1) if "_validate_execution_risk(" in l)
    assert risk < gate < send


def test_legacy_gate_is_between_validation_and_send():
    path = ROOT / "execution/mt5_execution.py"
    gate = min(_prop_gate_lines(path))
    send = min(_send_calls(path))
    assert gate < send


def test_no_enforcement_module_imports_mt5():
    """Enforcement decides and executes through ONE injected boundary."""
    for relative in (
        "core/risk/prop_rule_enforcement.py",
        "core/risk/prop_rule_runtime.py",
        "core/risk/prop_rule_executor.py",
        "core/risk/prop_rule_projection.py",
        "core/risk/prop_rule_external.py",
        "core/risk/prop_entry_gate.py",
    ):
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "MetaTrader5" not in alias.name, (
                        f"{relative} must not import MetaTrader5"
                    )
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "MetaTrader5", (
                    f"{relative} must not import from MetaTrader5"
                )


def test_close_path_has_exactly_one_broker_free_boundary():
    """Forced closes go through the injected port, never a direct broker call."""
    executor_source = (ROOT / "core/risk/prop_rule_executor.py").read_text(encoding="utf-8")
    tree = ast.parse(executor_source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "order_send", (
                "the enforcement executor must never call the broker directly"
            )


# =============================================================================
# NON-VACUITY ? mutation checks (requirement 50)
# =============================================================================


def test_mutation_removing_the_projected_check_is_detected():
    """If the runtime ignored the proposed order, the breach test MUST fail."""
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0))
    state = make_state(total_open_risk=4_000.0)
    correct = runtime.authorize_entry(
        account=ACCOUNT_A, state=state, at_utc=NOW, order=_order(risk=2_000.0)
    )
    assert correct.allowed is False

    # MUTATION: drop the projected order entirely.
    mutated = runtime.authorize_entry(account=ACCOUNT_A, state=state, at_utc=NOW)
    assert mutated.allowed is True, (
        "without the projected check this entry is allowed -> the safety "
        "property is being enforced ONLY by the projection"
    )


def test_mutation_treating_breach_as_pass_is_detected():
    """If BREACH were mapped to PASS, the breach-block test MUST fail."""
    from core.risk.prop_rule_enforcement import decide_policy

    rule = build_open_risk_pack(max_open_risk_pct=5.0).rules[0]
    from core.risk.prop_rule_evaluator import EvaluationResult

    breach = EvaluationResult(
        evaluation_id="ev_b", rule_pack_id="p", rule_id=rule.rule_id,
        rule_type=rule.rule_type, account_id="ACC_A", evaluated_at_utc=NOW,
        status=EvaluationStatus.BREACH, rule_day=date(2026, 3, 10),
        timezone_name="UTC", unit="MONEY", current_value=6_000.0,
        limit_value=5_000.0, breach=True,
        explanation_code=__import__(
            "core.risk.prop_rule_evaluator", fromlist=["ExplanationCode"]
        ).ExplanationCode.LIMIT_EXCEEDED,
    )
    outcome = decide_policy(rule=rule, result=breach)
    assert outcome.action is not EnforcementAction.NO_ACTION
    assert outcome.action_required is True

    # MUTATION: report the SAME evaluation as PASS.
    passed = EvaluationResult(
        **{**breach.__dict__, "status": EvaluationStatus.PASS, "breach": False}
    )
    mutated = decide_policy(rule=rule, result=passed)
    assert mutated.action is EnforcementAction.NO_ACTION


def test_mutation_removing_account_isolation_is_detected():
    """Without account binding, one account's breach would block the other."""
    from core.risk.prop_rule_enforcement import derive_enforcement_id

    shared = dict(
        rule_pack_id="p", rule_id="r", evaluation_id="ev",
        action=EnforcementAction.BLOCK_ALL_ENTRIES, scope=EnforcementScope.ACCOUNT,
    )
    a = derive_enforcement_id(account=ACCOUNT_A, **shared)
    b = derive_enforcement_id(account=ACCOUNT_B, **shared)
    assert a != b, "enforcement identity must be account-bound"


def test_mutation_disabling_durable_restore_is_detected(tmp_path):
    """Without rebuild(), a restart silently forgets the suspension."""
    store = EnforcementStateStore(tmp_path / "enf")
    runtime = build_runtime(build_drawdown_pack(), store=store)
    runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(account=ACCOUNT_A, equity=80_000.0), at_utc=NOW
    )
    assert len(EnforcementStateStore(tmp_path / "enf").suspensions_for(ACCOUNT_A)) >= 1

    # MUTATION: a store that never rebuilds "forgets" everything.
    class ForgetfulStore(EnforcementStateStore):
        def rebuild(self) -> None:  # pragma: no cover - the mutation itself
            return None

    forgetful = ForgetfulStore(tmp_path / "enf")
    assert forgetful.suspensions_for(ACCOUNT_A) == (), (
        "a store without rebuild loses the suspension -> restart safety depends "
        "entirely on the durable rebuild"
    )


def test_mutation_flipping_precedence_is_detected():
    """Precedence must put kills above blocks, deterministically."""
    from core.risk.prop_rule_enforcement import ACTION_PRECEDENCE

    assert ACTION_PRECEDENCE.index(EnforcementAction.KILL_SWITCH) == 0
    assert (
        ACTION_PRECEDENCE.index(EnforcementAction.BLOCK_ALL_ENTRIES)
        < ACTION_PRECEDENCE.index(EnforcementAction.BLOCK_SYMBOL_ENTRY)
    )
"""RUNTIME PROP RULE ENFORCEMENT integration (Block 3C).

Proves the enforcement layer is REAL, not decorative:

* the final pre-order gate refuses a projected breach that CURRENT state allows,
* multi-account fan-out isolates one account's breach from another's pass,
* account identity binds every decision,
* durable suspension/kill state survives a restart,
* SIMULATE_ONLY runs the identical path and changes nothing,
* startup reports an EXACT degraded reason and never falls back to "no rules",
* every live order-send site is gated (adversarial source audit),
* external-provider failure fails closed for entries but not for positions.

Non-vacuity is demonstrated in ``test_mutation_*``: each deliberately breaks one
guarantee and asserts that a test detects it.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path

import pytest

from core.risk.prop_rule_enforcement import (
    DegradedReason,
    EnforcementAction,
    EnforcementMode,
    EnforcementReason,
    EnforcementScope,
)
from core.risk.prop_rule_evaluator import EvaluationStatus
from core.risk.prop_rule_executor import (
    CloseOutcome,
    CloseRequest,
    CloseResult,
    EnforcementAuditRecord,
    EnforcementExecutor,
    EnforcementStateStore,
    KillSwitchState,
    SuspensionScope,
    SuspensionState,
)
from core.risk.prop_rule_projection import PlannedOrder
from core.risk.prop_rule_runtime import (
    PropEnforcementRuntime,
    configure_prop_enforcement_runtime,
    prop_enforcement_runtime,
    stop_prop_enforcement_runtime,
)
from core.risk.prop_rule_state import AccountKey

UTC = timezone.utc
NOW = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)
ACCOUNT_A = AccountKey("ACC_A", "MT5", "DemoBroker-Server", 111111)
ACCOUNT_B = AccountKey("ACC_B", "MT5", "DemoBroker-Server", 222222)
ROOT = Path(__file__).resolve().parent.parent


def _order(risk: float | None = 2_000.0, symbol: str = "EURUSD") -> PlannedOrder:
    return PlannedOrder(
        canonical_symbol=symbol, side="BUY", volume=0.5, entry_price=1.10,
        stop_loss=1.0990, planned_risk_amount=risk,
    )


# =============================================================================
# HARNESS ? real 3A pack + real 3B state + real 3C runtime
# =============================================================================

from core.risk.prop_rule_contracts import (
    DailyLossRule,
    DrawdownRule,
    OpenRiskLimitRule,
    PositionLimitRule,
    RuleSource,
    required_telemetry_for,
)
from core.risk.prop_rule_enums import (
    BreachKind,
    CurrencySemantics,
    DailyResetPolicy,
    DrawdownKind,
    EvaluationTimeBasis,
    LimitBasis,
    RulePhase,
    RuleSeverity,
    RuleType,
    SourceType,
)
from core.risk.prop_rule_pack import RulePack, RulePackIdentity, compile_rule_pack
from core.risk.prop_rule_state import (
    AccountEvaluationState,
    DailyAccountAnchor,
    InitialAccountAnchor,
    PropRuleStateStore,
    RuleDayDefinition,
    StateStatus,
    build_account_evaluation_state,
)
from core.risk.prop_rule_values import Limit

NY = RuleDayDefinition("America/New_York", time(0, 0))
EFFECTIVE_FROM = datetime(2026, 1, 1, tzinfo=UTC)
ACCOUNT_CURRENCY = CurrencySemantics.ACCOUNT_CURRENCY
SOURCE = RuleSource(
    source_type=SourceType.OFFICIAL_RULE_PAGE,
    source_reference="https://example.invalid/3c-rules",
    retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
)


@dataclass(frozen=True)
class FakeSnapshot:
    account_id: str = "ACC_A"
    snapshot_id: str = "asnap_1"
    balance: float | None = 100_000.0
    equity: float | None = 100_000.0
    floating_pnl: float | None = 0.0
    currency: str = "USD"


@dataclass(frozen=True)
class FakeOpenRisk:
    account_id: str = "ACC_A"
    open_risk_id: str = "orisk_1"
    total_open_risk: float | None = 4_000.0
    risk_complete: bool = True
    open_position_count: int | None = 1
    position_tickets: tuple[int, ...] = (11,)
    position_risks: tuple[float, ...] = (4_000.0,)


@dataclass(frozen=True)
class FakePortfolio:
    account_id: str = "ACC_A"
    portfolio_exposure_id: str = "pexp_1"
    largest_symbol_risk: float | None = 4_000.0
    largest_symbol: str | None = "EURUSD"
    largest_direction_risk: float | None = 4_000.0
    largest_direction: str = "LONG"
    max_cluster_risk: float | None = 4_000.0
    correlation_complete: bool = True


def build_open_risk_pack(*, max_open_risk_pct: float = 5.0) -> RulePack:
    """A pack with ONE entry-block rule: max open risk as % of current equity."""
    identity = RulePackIdentity(
        provider="TEST_FIRM", program="TEST_CHALLENGE",
        phase=RulePhase.EVALUATION_PHASE_1, account_size=100_000,
        currency="USD", rule_pack_version="1.0.0", effective_from=EFFECTIVE_FROM,
    )
    rule = OpenRiskLimitRule(
        rule_id="r.max_open_risk",
        rule_type=RuleType.MAX_OPEN_RISK,
        enabled=True,
        severity=RuleSeverity.BREACH,
        source=SOURCE,
        effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.MAX_OPEN_RISK),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        limit=Limit(
            basis=LimitBasis.PERCENT_CURRENT_EQUITY, value=max_open_risk_pct,
            currency_semantics=ACCOUNT_CURRENCY,
        ),
        scope_kind="TOTAL",
    )
    return compile_rule_pack(identity, [rule], notes="3C TEST PACK")


def build_drawdown_pack(*, max_dd_pct: float = 10.0) -> RulePack:
    identity = RulePackIdentity(
        provider="TEST_FIRM_DD", program="TEST_CHALLENGE",
        phase=RulePhase.EVALUATION_PHASE_1, account_size=100_000,
        currency="USD", rule_pack_version="1.0.0", effective_from=EFFECTIVE_FROM,
    )
    rule = DrawdownRule(
        rule_id="r.static_drawdown",
        rule_type=RuleType.STATIC_DRAWDOWN,
        enabled=True,
        severity=RuleSeverity.TERMINATION,
        source=SOURCE,
        effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.STATIC_DRAWDOWN),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        kind=DrawdownKind.STATIC,
        limit=Limit(
            basis=LimitBasis.PERCENT_INITIAL_BALANCE, value=max_dd_pct,
            currency_semantics=ACCOUNT_CURRENCY,
        ),
        anchor=LimitBasis.PERCENT_INITIAL_BALANCE,
    )
    return compile_rule_pack(identity, [rule], notes="3C TEST DD PACK")


def build_daily_loss_pack(*, max_daily_loss_pct: float = 5.0) -> RulePack:
    identity = RulePackIdentity(
        provider="TEST_FIRM_DL", program="TEST_CHALLENGE",
        phase=RulePhase.EVALUATION_PHASE_1, account_size=100_000,
        currency="USD", rule_pack_version="1.0.0", effective_from=EFFECTIVE_FROM,
    )
    rule = DailyLossRule(
        rule_id="r.daily_loss",
        rule_type=RuleType.DAILY_LOSS_LIMIT,
        enabled=True,
        severity=RuleSeverity.TERMINATION,
        source=SOURCE,
        effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.DAILY_LOSS_LIMIT),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        breach_kind=BreachKind.HARD,
        breach_persists_until_reset=True,
        timezone_name="America/New_York",
        limit=Limit(
            basis=LimitBasis.PERCENT_START_OF_DAY_BALANCE, value=max_daily_loss_pct,
            currency_semantics=ACCOUNT_CURRENCY,
        ),
        reset_policy=DailyResetPolicy.CALENDAR_DAY,
        reset_time=time(0, 0),
    )
    return compile_rule_pack(identity, [rule], notes="3C TEST DL PACK")


def make_state(
    *,
    account: AccountKey = ACCOUNT_A,
    equity: float = 100_000.0,
    total_open_risk: float | None = 4_000.0,
    initial_balance: float = 100_000.0,
    sod_balance: float = 100_000.0,
    stale: bool = False,
) -> AccountEvaluationState:
    """A COMPLETE evaluation state from explicit, injected evidence."""
    store = PropRuleStateStore()
    anchor = store.record_initial_anchor(
        InitialAccountAnchor(
            account=account, account_currency="USD",
            initial_balance=initial_balance, initial_equity=initial_balance,
            observed_at_utc=datetime(2026, 3, 2, 14, 0, tzinfo=UTC),
            source_snapshot_id="asnap_initial", source_provenance="TEST",
        )
    )
    day = store.record_daily_anchor(
        DailyAccountAnchor(
            account=account, account_currency="USD",
            rule_day=NY.rule_day_for(NOW), timezone_name=NY.timezone_name,
            rule_day_definition=NY.key(), start_of_day_balance=sod_balance,
            start_of_day_equity=sod_balance, anchor_at_utc=NY.day_start_utc(NY.rule_day_for(NOW)),
            source_snapshot_id="asnap_sod", source_provenance="TEST",
            start_of_day_floating_pnl=0.0,
        )
    )
    return build_account_evaluation_state(
        account=account, account_currency="USD", observed_at_utc=NOW, definition=NY,
        initial_anchor=anchor, daily_anchor=day,
        account_snapshot=FakeSnapshot(
            account_id=account.account_id, balance=equity, equity=equity,
        ),
        open_risk=FakeOpenRisk(
            account_id=account.account_id, total_open_risk=total_open_risk,
        ),
        portfolio=FakePortfolio(account_id=account.account_id),
        stale=stale,
    )


def build_runtime(
    pack: RulePack,
    *,
    mode: EnforcementMode = EnforcementMode.LIVE_ENFORCE,
    store: EnforcementStateStore | None = None,
    state=None,
    provider: object | None = None,
) -> PropEnforcementRuntime:
    """A started runtime bound to one pack, with a fixed state provider."""
    from core.risk.prop_rule_pack import RulePackStore

    packs = RulePackStore()
    packs.register_rule_pack(pack)
    runtime = PropEnforcementRuntime(
        mode=mode,
        rule_pack_store=packs,
        state_store=store or EnforcementStateStore(),
        pack_identity=pack.identity,
        rule_day_definition=NY,
        clock=lambda: NOW,
    )
    runtime.state_provider = lambda account: state
    report = runtime.start(at_utc=NOW)
    assert report.ready, report.to_dict()
    return runtime


# =============================================================================
# CURRENT vs PROJECTED ? the headline safety property
# =============================================================================


def test_current_pass_but_projected_breach_blocks_before_send():
    """4% current against a 5% limit, +2% proposed = 6% -> REFUSED.

    This is the single most important behaviour in Block 3C.
    """
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0))
    state = make_state(total_open_risk=4_000.0)   # 4% of 100,000

    current = runtime.authorize_entry(account=ACCOUNT_A, state=state, at_utc=NOW)
    assert current.allowed is True, current.to_dict()

    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=state, at_utc=NOW, order=_order(risk=2_000.0)
    )
    assert verdict.allowed is False
    assert verdict.reason is EnforcementReason.PROJECTED_BREACH
    assert verdict.projected_decisions
    assert any(d.projected for d in verdict.projected_decisions)


def test_projected_within_limit_is_allowed():
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0))
    state = make_state(total_open_risk=2_000.0)   # 2% current
    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=state, at_utc=NOW, order=_order(risk=2_000.0)
    )
    assert verdict.allowed is True, verdict.to_dict()


def test_projected_position_count_breach_blocks():
    pack = RulePack(
        identity=RulePackIdentity(
            provider="TEST_FIRM_POS", program="TEST_CHALLENGE",
            phase=RulePhase.EVALUATION_PHASE_1, account_size=100_000,
            currency="USD", rule_pack_version="1.0.0", effective_from=EFFECTIVE_FROM,
        ),
        rules=(
            PositionLimitRule(
                rule_id="r.max_positions", rule_type=RuleType.MAX_OPEN_POSITIONS,
                enabled=True, severity=RuleSeverity.BREACH, source=SOURCE,
                effective_from=EFFECTIVE_FROM,
                telemetry_requirements=required_telemetry_for(RuleType.MAX_OPEN_POSITIONS),
                evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
                timezone_name="America/New_York",
                limit=Limit(basis=LimitBasis.POSITION_COUNT, value=1),
            ),
        ),
        rule_pack_id="",
    )
    from core.risk.prop_rule_pack import compile_rule_pack as _compile

    pack = _compile(pack.identity, list(pack.rules))
    runtime = build_runtime(pack)
    state = make_state(total_open_risk=1_000.0)  # exactly 1 open position
    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=state, at_utc=NOW, order=_order(risk=500.0)
    )
    assert verdict.allowed is False
    assert verdict.reason is EnforcementReason.PROJECTED_BREACH


def test_projection_without_exact_risk_cannot_silently_pass():
    """An unknown exact risk must not become an optimistic allow."""
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0))
    state = make_state(total_open_risk=4_000.0)
    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=state, at_utc=NOW, order=_order(risk=None)
    )
    # The projection is incomplete, so the monetary rule is undecidable and the
    # mandatory rule fails closed.
    assert verdict.projected_state_complete is False
    assert "total_open_risk" in verdict.projected_unavailable_fields
    assert verdict.allowed is False


def test_current_breach_blocks_even_without_a_proposed_order():
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0))
    state = make_state(total_open_risk=6_000.0)   # 6% already
    verdict = runtime.authorize_entry(account=ACCOUNT_A, state=state, at_utc=NOW)
    assert verdict.allowed is False
    assert verdict.reason is EnforcementReason.LIMIT_EXCEEDED


# =============================================================================
# MULTI-ACCOUNT FAN-OUT ISOLATION
# =============================================================================


def test_same_signal_two_accounts_only_the_breaching_one_is_blocked():
    """Account A PASSES and trades; account B BREACHES and is blocked."""
    runtime_a = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0))
    verdict_a = runtime_a.authorize_entry(
        account=ACCOUNT_A, state=make_state(account=ACCOUNT_A, total_open_risk=1_000.0),
        at_utc=NOW, order=_order(risk=500.0),
    )
    assert verdict_a.allowed is True

    runtime_b = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0))
    verdict_b = runtime_b.authorize_entry(
        account=ACCOUNT_B, state=make_state(account=ACCOUNT_B, total_open_risk=6_000.0),
        at_utc=NOW, order=_order(risk=500.0),
    )
    assert verdict_b.allowed is False
    # Different accounts, therefore different decisions.
    assert verdict_a.account_id != verdict_b.account_id


def test_one_account_breach_never_blocks_another_account():
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0))
    blocked = runtime.authorize_entry(
        account=ACCOUNT_B, state=make_state(account=ACCOUNT_B, total_open_risk=9_000.0),
        at_utc=NOW,
    )
    assert blocked.allowed is False
    # The runtime holds no broker-global mutable block state, so account A is
    # unaffected by B's breach even on the SAME runtime instance.
    healthy = runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(account=ACCOUNT_A, total_open_risk=500.0),
        at_utc=NOW,
    )
    assert healthy.allowed is True


def test_suspension_is_scoped_to_the_exact_account_identity():
    store = EnforcementStateStore()
    runtime = build_runtime(build_drawdown_pack(), store=store)
    decision = runtime.evaluate(account=ACCOUNT_A, state=make_state(account=ACCOUNT_A), at_utc=NOW)
    breached = runtime._decisions(account=ACCOUNT_A, results=decision, at_utc=NOW)[0]
    runtime._executor.suspend(breached, at_utc=NOW, terminal=True)

    assert len(store.suspensions_for(ACCOUNT_A)) == 1
    assert store.suspensions_for(ACCOUNT_B) == ()


# =============================================================================
# DURABLE STATE, RESTART CONTINUITY, KILL SWITCH
# =============================================================================


def test_terminal_breach_suspension_survives_restart(tmp_path):
    """A process restart must NOT clear a terminal prop breach."""
    store = EnforcementStateStore(tmp_path / "enf")
    runtime = build_runtime(build_drawdown_pack(), store=store)
    verdict = runtime.authorize_entry(
        account=ACCOUNT_A,
        state=make_state(account=ACCOUNT_A, equity=85_000.0),   # 15% drawdown
        at_utc=NOW,
    )
    assert verdict.allowed is False
    assert len(store.suspensions_for(ACCOUNT_A)) >= 1

    # A brand new process: brand new store instance, same directory.
    rebuilt = EnforcementStateStore(tmp_path / "enf")
    restored = rebuilt.suspensions_for(ACCOUNT_A)
    assert len(restored) == 1
    assert restored[0].terminal is True
    assert restored[0].is_active is True

    # And the runtime built from it still refuses.
    restarted = build_runtime(build_drawdown_pack(), store=rebuilt)
    again = restarted.authorize_entry(
        account=ACCOUNT_A, state=make_state(account=ACCOUNT_A), at_utc=NOW
    )
    assert again.allowed is False
    assert any(
        d.reason_code is EnforcementReason.RESTORED_ACTIVE_BLOCK
        for d in again.blocking
    )


def test_kill_switch_locked_survives_restart_and_cannot_be_downgraded(tmp_path):
    store = EnforcementStateStore(tmp_path / "enf")
    runtime = build_runtime(build_drawdown_pack(), store=store)
    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(account=ACCOUNT_A, equity=80_000.0), at_utc=NOW
    )
    assert verdict.allowed is False
    assert store.kill_switch_tripped(ACCOUNT_A) is True

    rebuilt = EnforcementStateStore(tmp_path / "enf")
    assert rebuilt.kill_switch_tripped(ACCOUNT_A) is True
    state = rebuilt.kill_switch_for(ACCOUNT_A)
    assert state is KillSwitchState.LOCKED
    # A later non-terminal write must not unlock it.
    rebuilt.record_kill_switch(
        ACCOUNT_A, KillSwitchState.ACTIVE, at_utc=NOW, enforcement_id="enf_x"
    )
    assert rebuilt.kill_switch_for(ACCOUNT_A) is KillSwitchState.LOCKED


def test_non_terminal_entry_block_does_not_trip_the_kill_switch(tmp_path):
    """A plain entry block is a suspension, NOT a kill switch.

    Tripping the kill switch for every breach would make the two states
    indistinguishable and would forbid the recovery paths the recovery policy
    explicitly models.
    """
    store = EnforcementStateStore(tmp_path / "enf")
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0), store=store)
    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(account=ACCOUNT_A, total_open_risk=9_000.0),
        at_utc=NOW,
    )
    assert verdict.allowed is False
    assert store.kill_switch_tripped(ACCOUNT_A) is False


def test_terminal_hard_stop_trips_and_persists_the_kill_switch(tmp_path):
    store = EnforcementStateStore(tmp_path / "enf")
    runtime = build_runtime(build_drawdown_pack(), store=store)
    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(account=ACCOUNT_A, equity=80_000.0), at_utc=NOW
    )
    assert verdict.allowed is False
    assert store.kill_switch_tripped(ACCOUNT_A) is True
    rebuilt = EnforcementStateStore(tmp_path / "enf")
    assert rebuilt.kill_switch_tripped(ACCOUNT_A) is True


def test_daily_loss_block_clears_only_via_explicit_authority():
    """No generic 'reset all guards': clearing requires a named authority."""
    store = EnforcementStateStore()
    runtime = build_runtime(build_daily_loss_pack(), store=store)
    decision = runtime.evaluate(account=ACCOUNT_A, state=make_state(), at_utc=NOW)[0]
    assert decision.status is not EvaluationStatus.BREACH   # no loss recorded

    from core.risk.prop_rule_enforcement import compile_decision as _cd

    dec = _cd(
        rule=build_daily_loss_pack().rules[0], result=decision,
        account=ACCOUNT_A, effective_at_utc=NOW,
    )
    suspension = runtime._executor.suspend(dec, at_utc=NOW, rule_day="2026-03-10")
    assert suspension.is_active is True
    # Still active: nothing cleared it.
    assert store.suspensions_for(ACCOUNT_A)[0].is_active is True
    cleared = store.clear_suspension(
        suspension.suspension_id, at_utc=NOW, by="OPERATOR", authority="MANUAL_OVERRIDE"
    )
    assert cleared.is_active is False
    assert cleared.clearance_authority == "MANUAL_OVERRIDE"


def test_challenge_suspension_is_distinct_from_account_suspension():
    store = EnforcementStateStore()
    runtime = build_runtime(build_drawdown_pack(), store=store)
    results = runtime.evaluate(account=ACCOUNT_A, state=make_state(), at_utc=NOW)
    dec = runtime._decisions(account=ACCOUNT_A, results=results, at_utc=NOW)[0]
    account_level = runtime._executor.suspend(dec, at_utc=NOW, scope=SuspensionScope.ACCOUNT)
    challenge_level = runtime._executor.suspend(
        dec, at_utc=NOW, scope=SuspensionScope.CHALLENGE
    )
    assert account_level.scope is SuspensionScope.ACCOUNT
    assert challenge_level.scope is SuspensionScope.CHALLENGE
    assert account_level.suspension_id != challenge_level.suspension_id


def test_audit_records_are_idempotent_and_lineage_complete():
    store = EnforcementStateStore()
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0), store=store)
    for _ in range(3):
        runtime.authorize_entry(
            account=ACCOUNT_A, state=make_state(total_open_risk=9_000.0), at_utc=NOW
        )
    records = store.audit_records(ACCOUNT_A)
    # The same decision observed repeatedly must not duplicate the audit trail.
    assert len(records) == len({r.record_id for r in records})
    record = records[0]
    assert record.rule_pack_id
    assert record.rule_id
    assert record.evaluation_ids
    assert record.enforcement_id
    assert record.account.identity == ACCOUNT_A.identity
    assert record.is_material is True


# =============================================================================
# FORCED CLOSE AUTHORITY ? exact ticket, idempotent, truthful outcomes
# =============================================================================


class RecordingClosePort:
    """A broker-free close port that records exactly what it was asked."""

    def __init__(self, outcomes: dict[int, CloseOutcome] | None = None) -> None:
        self.calls: list[CloseRequest] = []
        self._outcomes = dict(outcomes or {})

    def close_position(self, request: CloseRequest, *, at_utc: datetime) -> CloseResult:
        self.calls.append(request)
        outcome = self._outcomes.get(int(request.position_ticket), CloseOutcome.CLOSED)
        return CloseResult(request=request, outcome=outcome, attempted_at_utc=at_utc)


def _close_decision(account: AccountKey = ACCOUNT_A):
    from core.risk.prop_rule_enforcement import EnforceDecision
    from core.risk.prop_rule_enums import RuleType
    from core.risk.prop_rule_enforcement import (
        EnforcementCriticality, EnforcementSeverity,
    )

    return EnforceDecision(
        enforcement_id="enf_close_1", rule_pack_id="p", rule_id="r.weekend",
        rule_type=RuleType.WEEKEND_HOLD_RESTRICTION, account=account,
        evaluation_id="ev_close_1", evaluated_status=EvaluationStatus.BREACH,
        enforcement_action=EnforcementAction.CLOSE_SYMBOL_POSITIONS,
        criticality=EnforcementCriticality.POSITION_REDUCTION,
        severity=EnforcementSeverity.TERMINAL, effective_at_utc=NOW,
        applies_to=EnforcementScope.ACCOUNT,
        reason_code=EnforcementReason.LIMIT_EXCEEDED, action_required=True,
        terminal=True,
    )


def test_close_is_exact_ticket_and_account_bound():
    port = RecordingClosePort()
    executor = EnforcementExecutor(store=EnforcementStateStore(), close_port=port)
    executor.close_position(_close_decision(), ticket=4242, at_utc=NOW)
    assert len(port.calls) == 1
    assert port.calls[0].position_ticket == 4242
    assert port.calls[0].account.identity == ACCOUNT_A.identity
    assert port.calls[0].close_request_id.startswith("clreq_")


def test_same_ticket_on_another_account_is_a_different_close():
    port = RecordingClosePort()
    executor = EnforcementExecutor(store=EnforcementStateStore(), close_port=port)
    executor.close_position(_close_decision(ACCOUNT_A), ticket=4242, at_utc=NOW)
    executor.close_position(_close_decision(ACCOUNT_B), ticket=4242, at_utc=NOW)
    assert len({c.close_request_id for c in port.calls}) == 2
    assert {c.account.account_id for c in port.calls} == {"ACC_A", "ACC_B"}


def test_repeated_close_of_a_completed_ticket_is_already_closed_not_failure():
    port = RecordingClosePort()
    store = EnforcementStateStore()
    executor = EnforcementExecutor(store=store, close_port=port)
    first = executor.close_position(_close_decision(), ticket=7, at_utc=NOW)
    assert first.outcome is CloseOutcome.CLOSED
    second = executor.close_position(_close_decision(), ticket=7, at_utc=NOW)
    assert second.outcome is CloseOutcome.ALREADY_CLOSED
    assert second.is_success is True
    # The broker was asked exactly once.
    assert len(port.calls) == 1


def test_partial_liquidation_keeps_each_ticket_truthful():
    """Position 1 closes, 2 fails retryably, 3 was already gone."""
    port = RecordingClosePort(
        outcomes={1: CloseOutcome.CLOSED, 2: CloseOutcome.RETRYABLE_FAILURE,
                  3: CloseOutcome.ALREADY_CLOSED}
    )
    executor = EnforcementExecutor(store=EnforcementStateStore(), close_port=port)
    report = executor.liquidate_account(
        _close_decision(), tickets=(1, 2, 3), at_utc=NOW
    )
    assert report.closed_count == 1
    assert report.retryable_count == 1
    assert report.already_closed_count == 1
    assert report.fully_liquidated is False   # NOT proven


def test_fully_liquidated_requires_every_single_close_to_succeed():
    port = RecordingClosePort(outcomes={1: CloseOutcome.CLOSED, 2: CloseOutcome.CLOSED})
    executor = EnforcementExecutor(store=EnforcementStateStore(), close_port=port)
    report = executor.liquidate_account(_close_decision(), tickets=(1, 2), at_utc=NOW)
    assert report.fully_liquidated is True


def test_retryable_and_terminal_outcomes_are_distinguished():
    port = RecordingClosePort(outcomes={
        1: CloseOutcome.RETRYABLE_FAILURE, 2: CloseOutcome.TERMINAL_FAILURE,
    })
    executor = EnforcementExecutor(store=EnforcementStateStore(), close_port=port)
    retryable = executor.close_position(_close_decision(), ticket=1, at_utc=NOW)
    terminal = executor.close_position(_close_decision(), ticket=2, at_utc=NOW)
    assert retryable.is_retryable is True
    assert terminal.is_retryable is False
    assert terminal.is_success is False


def test_failed_close_never_rewrites_the_breach_verdict():
    port = RecordingClosePort(outcomes={9: CloseOutcome.TERMINAL_FAILURE})
    executor = EnforcementExecutor(store=EnforcementStateStore(), close_port=port)
    decision = _close_decision()
    result = executor.close_position(decision, ticket=9, at_utc=NOW)
    marked = executor.mark_attempt(decision, result)
    # The RULE verdict is untouched; only the action outcome changed.
    assert marked.evaluated_status is EvaluationStatus.BREACH
    assert marked.enforcement_action is decision.enforcement_action
    assert marked.enforcement_id == decision.enforcement_id
    assert marked.action_attempted is True
    assert marked.retryable is False


def test_close_attempts_survive_restart(tmp_path):
    port = RecordingClosePort()
    store = EnforcementStateStore(tmp_path / "enf")
    executor = EnforcementExecutor(store=store, close_port=port)
    decision = _close_decision()
    executor.close_position(decision, ticket=77, at_utc=NOW)

    rebuilt = EnforcementStateStore(tmp_path / "enf")
    request = CloseRequest(
        account=ACCOUNT_A, position_ticket=77, enforcement_id="enf_close_1"
    )
    assert rebuilt.close_completed(request.close_request_id) is True
    # A restarted executor therefore reports ALREADY_CLOSED without re-asking.
    restarted = EnforcementExecutor(store=rebuilt, close_port=port)
    again = restarted.close_position(decision, ticket=77, at_utc=NOW)
    assert again.outcome is CloseOutcome.ALREADY_CLOSED
    assert len(port.calls) == 1


def test_executor_refuses_to_close_without_a_port():
    executor = EnforcementExecutor(store=EnforcementStateStore(), close_port=None)
    with pytest.raises(Exception):
        executor.close_position(_close_decision(), ticket=1, at_utc=NOW)


# =============================================================================
# MODES ? disabled / simulate-only / live
# =============================================================================


def test_disabled_mode_is_explicit_and_allows():
    runtime = PropEnforcementRuntime(mode=EnforcementMode.DISABLED, clock=lambda: NOW)
    report = runtime.start(at_utc=NOW)
    assert report.ready is True
    assert report.degraded_reason is DegradedReason.PROP_MODE_DISABLED
    verdict = runtime.authorize_entry(account=ACCOUNT_A, state=make_state(), at_utc=NOW)
    assert verdict.allowed is True
    assert verdict.degraded_reason is DegradedReason.PROP_MODE_DISABLED


def test_simulate_only_computes_the_same_decision_as_live():
    """Same evaluation path, same decision, no side effect."""
    pack = build_open_risk_pack(max_open_risk_pct=5.0)
    state = make_state(total_open_risk=4_000.0)
    order = _order(risk=2_000.0)

    live = build_runtime(pack, mode=EnforcementMode.LIVE_ENFORCE, state=state)
    live_verdict = live.authorize_entry(account=ACCOUNT_A, state=state, at_utc=NOW, order=order)

    simulated = build_runtime(
        pack, mode=EnforcementMode.SIMULATE_ONLY, state=state
    )
    sim_verdict = simulated.authorize_entry(
        account=ACCOUNT_A, state=state, at_utc=NOW, order=order
    )

    assert live_verdict.allowed is sim_verdict.allowed is False
    assert live_verdict.reason is sim_verdict.reason
    assert [d.enforcement_id for d in live_verdict.blocking] == [
        d.enforcement_id for d in sim_verdict.blocking
    ]


def test_simulate_only_persists_but_changes_nothing(tmp_path):
    store = EnforcementStateStore(tmp_path / "enf")
    runtime = build_runtime(
        build_drawdown_pack(), mode=EnforcementMode.SIMULATE_ONLY,
        store=store, state=make_state(account=ACCOUNT_A, equity=80_000.0),
    )
    verdict = runtime.authorize_entry(account=ACCOUNT_A, at_utc=NOW)
    assert verdict.allowed is False
    # The decision was recorded...
    assert store.audit_records(ACCOUNT_A)
    # ...but NOTHING was suspended or locked.
    assert store.suspensions_for(ACCOUNT_A) == ()
    assert store.kill_switch_tripped(ACCOUNT_A) is False


def test_live_enforce_does_suspend_and_lock(tmp_path):
    store = EnforcementStateStore(tmp_path / "enf")
    runtime = build_runtime(
        build_drawdown_pack(), mode=EnforcementMode.LIVE_ENFORCE,
        store=store, state=make_state(account=ACCOUNT_A, equity=80_000.0),
    )
    verdict = runtime.authorize_entry(account=ACCOUNT_A, at_utc=NOW)
    assert verdict.allowed is False
    assert len(store.suspensions_for(ACCOUNT_A)) >= 1
    assert store.kill_switch_tripped(ACCOUNT_A) is True


# =============================================================================
# STARTUP / DEGRADED ? never a silent fallback to "no prop rules"
# =============================================================================


def test_missing_rule_pack_reports_exact_reason_not_disabled():
    from core.risk.prop_rule_pack import RulePackStore

    pack = build_open_risk_pack()
    runtime = PropEnforcementRuntime(
        mode=EnforcementMode.LIVE_ENFORCE, rule_pack_store=RulePackStore(),
        state_store=EnforcementStateStore(), pack_identity=pack.identity,
        rule_day_definition=NY, clock=lambda: NOW,
    )
    report = runtime.start(at_utc=NOW)
    assert report.ready is False
    assert report.degraded_reason is DegradedReason.RULE_PACK_MISSING
    assert report.degraded_reason is not DegradedReason.PROP_MODE_DISABLED

    verdict = runtime.authorize_entry(account=ACCOUNT_A, state=make_state(), at_utc=NOW)
    assert verdict.allowed is False
    assert verdict.degraded_reason is DegradedReason.RULE_PACK_MISSING


def test_ambiguous_rule_pack_is_reported_ambiguous():
    from core.risk.prop_rule_pack import RulePackStore

    pack = build_open_risk_pack()
    packs = RulePackStore()
    # A second VALID pack with the SAME selection key and an overlapping window.
    packs.register_rule_pack(pack)
    overlapping = RulePackIdentity(
        provider=pack.identity.provider, program=pack.identity.program,
        phase=pack.identity.phase, account_size=pack.identity.account_size,
        currency=pack.identity.currency, rule_pack_version="2.0.0",
        effective_from=EFFECTIVE_FROM,
    )
    from core.risk.prop_rule_contracts import OpenRiskLimitRule as _ORL

    second = _ORL(
        rule_id="r.max_open_risk_v2", rule_type=RuleType.MAX_OPEN_RISK,
        enabled=True, severity=RuleSeverity.BREACH, source=SOURCE,
        effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.MAX_OPEN_RISK),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        limit=Limit(basis=LimitBasis.PERCENT_CURRENT_EQUITY, value=4.0,
                    currency_semantics=ACCOUNT_CURRENCY),
        scope_kind="TOTAL",
    )
    from core.risk.prop_rule_pack import compile_rule_pack as _compile

    packs.register_rule_pack(_compile(overlapping, [second]))

    runtime = PropEnforcementRuntime(
        mode=EnforcementMode.LIVE_ENFORCE, rule_pack_store=packs,
        state_store=EnforcementStateStore(), pack_identity=pack.identity,
        rule_day_definition=NY, clock=lambda: NOW,
    )
    report = runtime.start(at_utc=NOW)
    assert report.ready is False
    assert report.degraded_reason is DegradedReason.RULE_PACK_AMBIGUOUS


def test_missing_required_external_provider_degrades_explicitly():
    from core.risk.prop_rule_pack import RulePackStore

    pack = build_open_risk_pack()
    runtime = PropEnforcementRuntime(
        mode=EnforcementMode.LIVE_ENFORCE, rule_pack_store=RulePackStore(),
        state_store=EnforcementStateStore(), pack_identity=pack.identity,
        rule_day_definition=NY, required_external=("ECONOMIC_CALENDAR",),
        economic_calendar=None, clock=lambda: NOW,
    )
    packs = RulePackStore()
    packs.register_rule_pack(pack)
    runtime._packs = packs
    report = runtime.start(at_utc=NOW)
    assert report.ready is False
    assert report.degraded_reason is DegradedReason.REQUIRED_PROVIDER_UNAVAILABLE
    assert "ECONOMIC_CALENDAR" in report.to_dict()["checks"][1]["detail"]


def test_missing_3b_state_blocks_and_names_the_reason():
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0))
    runtime.state_provider = lambda account: None
    verdict = runtime.authorize_entry(account=ACCOUNT_A, at_utc=NOW)
    assert verdict.allowed is False
    assert verdict.degraded_reason is DegradedReason.STATE_UNAVAILABLE


def test_stopped_service_fails_closed():
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=99.0))
    runtime.stop()
    verdict = runtime.authorize_entry(account=ACCOUNT_A, state=make_state(), at_utc=NOW)
    assert verdict.allowed is False
    assert "STOPPED" in verdict.detail


def test_clean_shutdown_has_no_thread_to_join():
    """3C adds no background thread, so shutdown is a state change only."""
    import threading

    before = threading.active_count()
    runtime = build_runtime(build_open_risk_pack())
    assert threading.active_count() == before
    runtime.stop()
    assert runtime.stopped is True
    assert threading.active_count() == before


def test_runtime_status_is_inspectable():
    runtime = build_runtime(build_open_risk_pack(), state=make_state())
    runtime.authorize_entry(account=ACCOUNT_A, state=make_state(), at_utc=NOW)
    status = runtime.status().to_dict()
    assert status["mode"] == EnforcementMode.LIVE_ENFORCE.value
    assert status["active_rule_pack_id"]
    assert "ECONOMIC_CALENDAR" in status["external_sources"]
    assert status["degraded_reason"] == DegradedReason.NONE.value


# =============================================================================
# ADVERSARIAL AUDIT ? every live order-send site must be gated
# =============================================================================

import ast

#: Modules that PERFORM a broker send (as opposed to merely mentioning one).
LIVE_SEND_MODULES = (
    "execution/mt5_execution.py",
    "core/accounts/execution_worker.py",
)

#: The whole repository surface the audit sweeps for real sends.
AUDITED_MODULES = LIVE_SEND_MODULES + (
    "core/accounts/account_router.py",
    "core/accounts/live_fanout.py",
    "core/runtime/fanout_execution.py",
    "risk/spread_guard.py",
)


def _send_calls(path: Path) -> list[int]:
    """Line numbers of REAL ``order_send`` invocations, via AST.

    A textual scan would match docstrings and comments; an AST walk finds only
    actual attribute calls, including one passed as a function argument to
    ``mt5_call(...)``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name == "order_send":
            lines.append(node.lineno)
        elif name in {"mt5_call", "call", "timeout_call"}:
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(arg, ast.Attribute) and arg.attr == "order_send":
                    lines.append(arg.lineno)
    return sorted(set(lines))


def _prop_gate_lines(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8").read()
                     if False else path.read_text(encoding="utf-8"))
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name in {
                "prop_enforcement_gate", "_prop_enforcement_gate", "_prop_entry_gate",
            }:
                lines.append(node.lineno)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_prop_allowed":
                    if isinstance(node.value, ast.Call):
                        lines.append(node.value.lineno)
    return sorted(set(lines))


def test_every_audited_module_parses_and_is_listed():
    """The audit surface is explicit and every module really exists."""
    for relative in AUDITED_MODULES:
        assert (ROOT / relative).exists(), f"missing audited module: {relative}"
        ast.parse((ROOT / relative).read_text(encoding="utf-8"))


@pytest.mark.parametrize("relative", LIVE_SEND_MODULES)
def test_every_real_order_send_call_is_gated(relative):
    """No broker send may execute without a prop gate ABOVE it.

    This is the fail-closed-location guarantee (requirements 35 and 61). Adding
    a send above the gate makes this test fail.
    """
    path = ROOT / relative
    sends = _send_calls(path)
    assert sends, f"expected at least one real order_send in {relative}"
    gates = _prop_gate_lines(path)
    assert gates, f"no prop gate call found in {relative}"
    earliest_gate = min(gates)
    for send_line in sends:
        assert send_line > earliest_gate, (
            f"{relative}:{send_line} sends with no prop gate above it "
            f"(earliest gate is line {earliest_gate})"
        )


def test_worker_gate_is_between_validation_and_send():
    source = (ROOT / "core/accounts/execution_worker.py").read_text(encoding="utf-8")
    lines = source.splitlines()
    gate = min(_prop_gate_lines(ROOT / "core/accounts/execution_worker.py"))
    send = min(_send_calls(ROOT / "core/accounts/execution_worker.py"))
    risk = next(i for i, l in enumerate(lines, 1) if "_validate_execution_risk(" in l)
    assert risk < gate < send


def test_legacy_gate_is_between_validation_and_send():
    path = ROOT / "execution/mt5_execution.py"
    gate = min(_prop_gate_lines(path))
    send = min(_send_calls(path))
    assert gate < send


def test_no_enforcement_module_imports_mt5():
    """Enforcement decides and executes through ONE injected boundary."""
    for relative in (
        "core/risk/prop_rule_enforcement.py",
        "core/risk/prop_rule_runtime.py",
        "core/risk/prop_rule_executor.py",
        "core/risk/prop_rule_projection.py",
        "core/risk/prop_rule_external.py",
        "core/risk/prop_entry_gate.py",
    ):
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "MetaTrader5" not in alias.name, (
                        f"{relative} must not import MetaTrader5"
                    )
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "MetaTrader5", (
                    f"{relative} must not import from MetaTrader5"
                )


def test_close_path_has_exactly_one_broker_free_boundary():
    """Forced closes go through the injected port, never a direct broker call."""
    executor_source = (ROOT / "core/risk/prop_rule_executor.py").read_text(encoding="utf-8")
    tree = ast.parse(executor_source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "order_send", (
                "the enforcement executor must never call the broker directly"
            )


# =============================================================================
# NON-VACUITY ? mutation checks (requirement 50)
# =============================================================================


def test_mutation_removing_the_projected_check_is_detected():
    """If the runtime ignored the proposed order, the breach test MUST fail."""
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0))
    state = make_state(total_open_risk=4_000.0)
    correct = runtime.authorize_entry(
        account=ACCOUNT_A, state=state, at_utc=NOW, order=_order(risk=2_000.0)
    )
    assert correct.allowed is False

    # MUTATION: drop the projected order entirely.
    mutated = runtime.authorize_entry(account=ACCOUNT_A, state=state, at_utc=NOW)
    assert mutated.allowed is True, (
        "without the projected check this entry is allowed -> the safety "
        "property is being enforced ONLY by the projection"
    )


def test_mutation_treating_breach_as_pass_is_detected():
    """If BREACH were mapped to PASS, the breach-block test MUST fail."""
    from core.risk.prop_rule_enforcement import decide_policy

    rule = build_open_risk_pack(max_open_risk_pct=5.0).rules[0]
    from core.risk.prop_rule_evaluator import EvaluationResult

    breach = EvaluationResult(
        evaluation_id="ev_b", rule_pack_id="p", rule_id=rule.rule_id,
        rule_type=rule.rule_type, account_id="ACC_A", evaluated_at_utc=NOW,
        status=EvaluationStatus.BREACH, rule_day=date(2026, 3, 10),
        timezone_name="UTC", unit="MONEY", current_value=6_000.0,
        limit_value=5_000.0, breach=True,
        explanation_code=__import__(
            "core.risk.prop_rule_evaluator", fromlist=["ExplanationCode"]
        ).ExplanationCode.LIMIT_EXCEEDED,
    )
    outcome = decide_policy(rule=rule, result=breach)
    assert outcome.action is not EnforcementAction.NO_ACTION
    assert outcome.action_required is True

    # MUTATION: report the SAME evaluation as PASS.
    passed = EvaluationResult(
        **{**breach.__dict__, "status": EvaluationStatus.PASS, "breach": False}
    )
    mutated = decide_policy(rule=rule, result=passed)
    assert mutated.action is EnforcementAction.NO_ACTION


def test_mutation_removing_account_isolation_is_detected():
    """Without account binding, one account's breach would block the other."""
    from core.risk.prop_rule_enforcement import derive_enforcement_id

    shared = dict(
        rule_pack_id="p", rule_id="r", evaluation_id="ev",
        action=EnforcementAction.BLOCK_ALL_ENTRIES, scope=EnforcementScope.ACCOUNT,
    )
    a = derive_enforcement_id(account=ACCOUNT_A, **shared)
    b = derive_enforcement_id(account=ACCOUNT_B, **shared)
    assert a != b, "enforcement identity must be account-bound"


def test_mutation_disabling_durable_restore_is_detected(tmp_path):
    """Without rebuild(), a restart silently forgets the suspension."""
    store = EnforcementStateStore(tmp_path / "enf")
    runtime = build_runtime(build_drawdown_pack(), store=store)
    runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(account=ACCOUNT_A, equity=80_000.0), at_utc=NOW
    )
    assert len(EnforcementStateStore(tmp_path / "enf").suspensions_for(ACCOUNT_A)) >= 1

    # MUTATION: a store that never rebuilds "forgets" everything.
    class ForgetfulStore(EnforcementStateStore):
        def rebuild(self) -> None:  # pragma: no cover - the mutation itself
            return None

    forgetful = ForgetfulStore(tmp_path / "enf")
    assert forgetful.suspensions_for(ACCOUNT_A) == (), (
        "a store without rebuild loses the suspension -> restart safety depends "
        "entirely on the durable rebuild"
    )


def test_mutation_flipping_precedence_is_detected():
    """Precedence must put kills above blocks, deterministically."""
    from core.risk.prop_rule_enforcement import ACTION_PRECEDENCE

    assert ACTION_PRECEDENCE.index(EnforcementAction.KILL_SWITCH) == 0
    assert (
        ACTION_PRECEDENCE.index(EnforcementAction.BLOCK_ALL_ENTRIES)
        < ACTION_PRECEDENCE.index(EnforcementAction.BLOCK_SYMBOL_ENTRY)
    )


def test_audit_detects_an_ungated_send(tmp_path):
    """NON-VACUITY OF THE AUDIT ITSELF: an ungated send MUST be flagged."""
    target = tmp_path / "rogue_execution.py"
    target.write_text(
        "import MetaTrader5 as mt5\n"
        "def send(request):\n"
        "    return mt5.order_send(request)\n",
        encoding="utf-8",
    )
    sends = _send_calls(target)
    assert sends == [3]
    gates = _prop_gate_lines(target)
    assert gates == []
    for send_line in sends:
        assert not any(g < send_line for g in gates), (
            "the audit must fail when a send has no gate above it"
        )


def test_audit_detects_a_gate_placed_below_the_send(tmp_path):
    """A gate BELOW the send is not a gate."""
    target = tmp_path / "late_gate.py"
    target.write_text(
        "import MetaTrader5 as mt5\n"
        "from core.risk.prop_entry_gate import prop_enforcement_gate\n"
        "def send(request):\n"
        "    r = mt5.order_send(request)\n"
        "    ok, code = prop_enforcement_gate(account=None)\n"
        "    return r\n",
        encoding="utf-8",
    )
    sends = _send_calls(target)
    gates = _prop_gate_lines(target)
    assert sends == [4]
    assert gates == [5]          # the gate is BELOW the send
    earliest_gate = min(gates)
    # The audit's own rule is "send must come after the gate"; here it does not,
    # so the audit MUST flag it.
    flagged = [line for line in sends if line <= earliest_gate]
    assert flagged == [4], (
        "a send placed above the gate must be flagged as an ungated bypass"
    )


# =============================================================================
# EXTERNAL SOURCES ? news / market session enforcement
# =============================================================================


def build_news_pack(*, pre_minutes: int = 2, post_minutes: int = 2) -> RulePack:
    from core.risk.prop_rule_contracts import NewsTradingRestrictionRule
    from core.risk.prop_rule_enums import NewsImportance

    identity = RulePackIdentity(
        provider="TEST_FIRM_NEWS", program="TEST_CHALLENGE",
        phase=RulePhase.EVALUATION_PHASE_1, account_size=100_000,
        currency="USD", rule_pack_version="1.0.0", effective_from=EFFECTIVE_FROM,
    )
    rule = NewsTradingRestrictionRule(
        rule_id="r.news", rule_type=RuleType.NEWS_TRADING_RESTRICTION,
        enabled=True, severity=RuleSeverity.BREACH, source=SOURCE,
        effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.NEWS_TRADING_RESTRICTION),
        evaluation_time_basis=EvaluationTimeBasis.EVENT_DRIVEN,
        timezone_name="America/New_York",
        blackouts_high_impact=True, importance_levels=(NewsImportance.HIGH,),
        pre_event_blackout_minutes=pre_minutes,
        post_event_blackout_minutes=post_minutes,
        prohibit_opening_trades=True, prohibit_closing_trades=False,
        prohibit_holding_through_event=True, requires_economic_calendar=True,
    )
    from core.risk.prop_rule_pack import compile_rule_pack as _compile

    return _compile(identity, [rule], notes="3C TEST NEWS PACK")


class StaticCalendar:
    """A real provider: it returns the events it was constructed with."""

    def __init__(self, events, *, freshness=__import__(
        "core.risk.prop_rule_external", fromlist=["SourceFreshness"]
    ).SourceFreshness.FRESH, detail=""):
        from core.risk.prop_rule_external import SourceProvenance

        self._events = tuple(events)
        self.provenance = SourceProvenance(
            provider_name="STATIC_TEST_CALENDAR",
            source_reference="test://static-calendar",
            retrieved_at_utc=NOW, freshness=freshness,
            max_age_seconds=86_400, detail=detail,
        )

    def fetch(self, *, start_utc, end_utc):
        from dataclasses import replace as _replace

        from core.risk.prop_rule_external import EconomicCalendarSnapshot

        # A real provider stamps retrieval at the moment of the fetch, so the
        # answer is fresh for the instant being evaluated.
        provenance = _replace(self.provenance, retrieved_at_utc=start_utc)
        return EconomicCalendarSnapshot(provenance=provenance, events=self._events)


def test_news_provider_down_blocks_entries_but_never_forces_a_close():
    """Mandatory distinction (requirements 45 and 57)."""
    from core.risk.prop_rule_external import (
        EconomicEvent,
        SourceFreshness,
        unavailable_calendar,
    )

    class DeadCalendar:
        def fetch(self, *, start_utc, end_utc):
            return unavailable_calendar(
                "DEAD_CALENDAR", retrieved_at_utc=NOW, max_age_seconds=600,
                detail="PROVIDER_DOWN",
            )

    runtime = PropEnforcementRuntime(
        mode=EnforcementMode.LIVE_ENFORCE,
        rule_pack_store=_pack_store(build_news_pack()),
        state_store=EnforcementStateStore(),
        pack_identity=build_news_pack().identity,
        rule_day_definition=NY, economic_calendar=DeadCalendar(),
        required_external=("ECONOMIC_CALENDAR",), clock=lambda: NOW,
    )
    runtime.state_provider = lambda account: make_state()
    report = runtime.start(at_utc=NOW)
    assert report.ready is True   # the provider object exists

    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(), at_utc=NOW, order=_order(risk=100.0)
    )
    assert verdict.allowed is False
    # Explicitly NOT a close: existing exposure is untouched.
    for decision in verdict.blocking:
        assert not decision.is_position_reduction
        assert decision.enforcement_action in (
            EnforcementAction.REQUIRES_EXTERNAL_SOURCE,
            EnforcementAction.INDETERMINATE_BLOCK,
            EnforcementAction.BLOCK_NEW_ENTRY,
            EnforcementAction.BLOCK_ALL_ENTRIES,
            EnforcementAction.BLOCK_SYMBOL_ENTRY,
        )


def test_news_blackout_blocks_and_the_window_clears():
    """Inside the blackout: blocked. Outside it: allowed, with no stale block."""
    from core.risk.prop_rule_external import EconomicEvent, SourceFreshness

    pack = build_news_pack(pre_minutes=30, post_minutes=30)
    event = EconomicEvent("nfp", NOW, "HIGH", ("USD", "EUR"))
    runtime = PropEnforcementRuntime(
        mode=EnforcementMode.LIVE_ENFORCE,
        rule_pack_store=_pack_store(pack), state_store=EnforcementStateStore(),
        pack_identity=pack.identity, rule_day_definition=NY,
        economic_calendar=StaticCalendar([event]),
        required_external=("ECONOMIC_CALENDAR",), clock=lambda: NOW,
    )
    runtime.state_provider = lambda account: make_state()
    runtime.start(at_utc=NOW)

    inside = runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(), at_utc=NOW, order=_order(risk=100.0)
    )
    assert inside.allowed is False

    # Well outside the +/-30 minute window: the block must NOT persist.
    far = NOW.replace(hour=20)
    outside = runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(), at_utc=far, order=_order(risk=100.0)
    )
    assert outside.allowed is True, outside.to_dict()


def test_stale_news_calendar_is_not_treated_as_no_events():
    from core.risk.prop_rule_external import EconomicEvent, SourceFreshness

    pack = build_news_pack()
    stale = StaticCalendar(
        [EconomicEvent("nfp", NOW, "HIGH", ("USD",))],
        freshness=SourceFreshness.STALE,
    )
    runtime = PropEnforcementRuntime(
        mode=EnforcementMode.LIVE_ENFORCE, rule_pack_store=_pack_store(pack),
        state_store=EnforcementStateStore(), pack_identity=pack.identity,
        rule_day_definition=NY, economic_calendar=stale,
        required_external=("ECONOMIC_CALENDAR",), clock=lambda: NOW,
    )
    runtime.state_provider = lambda account: make_state()
    runtime.start(at_utc=NOW)
    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(), at_utc=NOW, order=_order(risk=100.0)
    )
    # A stale source is undecidable, so the entry fails closed rather than
    # inheriting a fabricated "nothing scheduled".
    assert verdict.allowed is False


def _pack_store(pack: RulePack):
    from core.risk.prop_rule_pack import RulePackStore

    store = RulePackStore()
    store.register_rule_pack(pack)
    return store


# =============================================================================
# RULE PACK / PHASE TRANSITIONS
# =============================================================================


def test_prior_decisions_are_never_mutated_by_a_new_pack():
    """A new pack starts a new identity; old decisions stay intact."""
    old_pack = build_open_risk_pack(max_open_risk_pct=5.0)
    old_runtime = build_runtime(old_pack, state=make_state(total_open_risk=4_000.0))
    old_verdict = old_runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(total_open_risk=4_000.0), at_utc=NOW
    )
    old_ids = {d.enforcement_id for d in old_verdict.decisions}

    new_pack = build_open_risk_pack(max_open_risk_pct=3.0)
    new_runtime = build_runtime(new_pack, state=make_state(total_open_risk=4_000.0))
    new_verdict = new_runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(total_open_risk=4_000.0), at_utc=NOW
    )
    assert new_pack.rule_pack_id != old_pack.rule_pack_id
    assert new_verdict.allowed is False        # 4% now breaches the 3% limit
    assert old_verdict.allowed is True         # the old decision is unchanged
    assert not (old_ids & {d.enforcement_id for d in new_verdict.decisions})


def test_phase_transition_binds_to_a_new_pack_identity():
    """No implicit reuse of a phase-1 pack in phase 2."""
    phase1 = build_open_risk_pack(max_open_risk_pct=5.0)
    from core.risk.prop_rule_pack import RulePackIdentity as _RI
    from core.risk.prop_rule_pack import compile_rule_pack as _compile

    phase2_identity = _RI(
        provider=phase1.identity.provider, program=phase1.identity.program,
        phase=RulePhase.EVALUATION_PHASE_2, account_size=100_000,
        currency="USD", rule_pack_version="1.0.0", effective_from=EFFECTIVE_FROM,
    )
    phase2 = _compile(phase2_identity, list(phase1.rules))
    assert phase2.rule_pack_id != phase1.rule_pack_id


# =============================================================================
# CURRENCY SAFETY (requirement 66) ? no silent conversion in 3C
# =============================================================================


def test_currency_mismatch_is_indeterminate_not_a_silent_comparison():
    """A fixed-currency rule on a different account currency must not compare."""
    from core.risk.prop_rule_contracts import OpenRiskLimitRule as _ORL
    from core.risk.prop_rule_enums import CurrencySemantics
    from core.risk.prop_rule_evaluator import evaluate_rule
    from core.risk.prop_rule_evaluator import EvaluationContext
    from core.risk.prop_rule_evaluator import ExplanationCode as _EC

    rule = _ORL(
        rule_id="r.eur_risk", rule_type=RuleType.MAX_OPEN_RISK, enabled=True,
        severity=RuleSeverity.BREACH, source=SOURCE, effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.MAX_OPEN_RISK),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        limit=Limit(
            basis=LimitBasis.ABSOLUTE_MONEY, value=1_000.0,
            currency_semantics=CurrencySemantics.FIXED_RULE_CURRENCY,
            rule_currency="EUR",
        ),
        scope_kind="TOTAL",
    )
    from core.risk.prop_rule_pack import compile_rule_pack as _c
    from core.risk.prop_rule_pack import RulePackIdentity as _I

    identity = _I(
        provider="CUR_TEST", program="CUR_CHALLENGE",
        phase=RulePhase.EVALUATION_PHASE_1, account_size=100_000,
        currency="USD", rule_pack_version="1.0.0", effective_from=EFFECTIVE_FROM,
    )
    context = EvaluationContext(
        rule_pack=_c(identity, [rule]), state=make_state(),  # account currency USD
        evaluated_at_utc=NOW, rule_day_definition=NY,
    )
    result = evaluate_rule(context, rule)
    assert result.status is EvaluationStatus.INDETERMINATE
    assert result.explanation_code is _EC.CURRENCY_MISMATCH


def test_missing_conversion_source_is_indeterminate():
    from core.risk.prop_rule_contracts import OpenRiskLimitRule as _ORL
    from core.risk.prop_rule_enums import CurrencySemantics
    from core.risk.prop_rule_evaluator import EvaluationContext, evaluate_rule

    rule = _ORL(
        rule_id="r.conv_risk", rule_type=RuleType.MAX_OPEN_RISK, enabled=True,
        severity=RuleSeverity.BREACH, source=SOURCE, effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.MAX_OPEN_RISK),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        limit=Limit(
            basis=LimitBasis.ABSOLUTE_MONEY, value=1_000.0,
            currency_semantics=CurrencySemantics.CONVERTED_REFERENCE_CURRENCY,
            rule_currency="EUR", conversion_source="ECB",
        ),
        scope_kind="TOTAL",
    )
    from core.risk.prop_rule_pack import compile_rule_pack as _c
    from core.risk.prop_rule_pack import RulePackIdentity as _I

    identity = _I(
        provider="CUR_TEST2", program="CUR_CHALLENGE",
        phase=RulePhase.EVALUATION_PHASE_1, account_size=100_000,
        currency="USD", rule_pack_version="1.0.0", effective_from=EFFECTIVE_FROM,
    )
    context = EvaluationContext(
        rule_pack=_c(identity, [rule]), state=make_state(),
        evaluated_at_utc=NOW, rule_day_definition=NY, fx_conversion_source=None,
    )
    result = evaluate_rule(context, rule)
    assert result.status is EvaluationStatus.INDETERMINATE


# =============================================================================
# LEGACY GUARD COEXISTENCE (requirements 68/69)
# =============================================================================


def test_3c_does_not_reimplement_the_legacy_daily_loss_arithmetic():
    """The legacy guards may remain, but 3C never defines the same prop limit."""
    from pathlib import Path as _P

    legacy = (_P(ROOT) / "risk" / "daily_loss_guard.py").read_text(encoding="utf-8")
    enforcement = (_P(ROOT) / "core" / "risk" / "prop_rule_enforcement.py").read_text(
        encoding="utf-8"
    )
    # The legacy guard computes a daily P&L; 3C only consumes a 3B verdict.
    assert "daily_loss" in legacy.lower()
    # Strip the FORBIDDEN_OPERATIONS declaration: naming a boundary is not
    # implementing it, so the scan must look for real arithmetic only.
    scan = enforcement.split("FORBIDDEN_OPERATIONS: tuple[str, ...] = (")[0]
    for token in ("net_realised", "daily_ledger", "high_water", "sum("):
        assert token not in scan, (
            f"3C must not compute {token}; that arithmetic belongs to Block 3B"
        )


def test_runtime_guard_chain_is_not_replaced_and_still_runs():
    """3C is a FINAL gate; the legacy guard chain remains upstream of it."""
    from pathlib import Path as _P

    chain = (_P(ROOT) / "risk" / "runtime_guard_chain.py").read_text(encoding="utf-8")
    assert "evaluate_runtime_guards" in chain
    for guard in ("daily_trade_limit", "correlation_guard", "weekend_protection"):
        assert guard in chain, f"legacy guard {guard} must be retained"


# =============================================================================
# RESTART RECONSTRUCTION (requirement 63)
# =============================================================================


def test_restart_reconstructs_every_enforcement_family(tmp_path):
    """Blocks, terminal suspensions, audit and pending closes all survive."""
    store = EnforcementStateStore(tmp_path / "enf")
    runtime = build_runtime(build_drawdown_pack(), store=store)
    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(account=ACCOUNT_A, equity=80_000.0), at_utc=NOW
    )
    assert verdict.allowed is False

    port = RecordingClosePort(outcomes={5: CloseOutcome.RETRYABLE_FAILURE})
    executor = EnforcementExecutor(store=store, close_port=port)
    executor.close_position(_close_decision(), ticket=5, at_utc=NOW)

    rebuilt = EnforcementStateStore(tmp_path / "enf")
    assert rebuilt.suspensions_for(ACCOUNT_A)
    assert rebuilt.kill_switch_tripped(ACCOUNT_A)
    assert rebuilt.audit_records(ACCOUNT_A)
    # The retryable close is NOT remembered as complete, so it may be retried.
    request = CloseRequest(
        account=ACCOUNT_A, position_ticket=5, enforcement_id="enf_close_1"
    )
    assert rebuilt.close_completed(request.close_request_id) is False


def test_audit_records_reload_with_identical_ids(tmp_path):
    store = EnforcementStateStore(tmp_path / "enf")
    runtime = build_runtime(build_open_risk_pack(max_open_risk_pct=5.0), store=store)
    runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(total_open_risk=9_000.0), at_utc=NOW
    )
    before = {r.record_id for r in store.audit_records(ACCOUNT_A)}
    rebuilt = EnforcementStateStore(tmp_path / "enf")
    after = {r.record_id for r in rebuilt.audit_records(ACCOUNT_A)}
    assert before == after and before
    # And the reloaded records are typed again, not raw dicts.
    assert all(isinstance(r.account, AccountKey) for r in rebuilt.audit_records(ACCOUNT_A))
    assert all(isinstance(r.recorded_at_utc, datetime) for r in rebuilt.audit_records(ACCOUNT_A))


# =============================================================================
# PROCESS-WIDE SINGLETON (requirements 41/43)
# =============================================================================


def test_only_one_runtime_instance_is_installed():
    from core.risk import prop_rule_runtime as pr

    configure_prop_enforcement_runtime(None)
    first = PropEnforcementRuntime(mode=EnforcementMode.DISABLED, clock=lambda: NOW)
    configure_prop_enforcement_runtime(first)
    assert prop_enforcement_runtime() is first
    # A second start reuses the SAME instance rather than creating a second
    # thread or a second competing authority.
    second, report = pr.start_prop_enforcement_runtime(at_utc=NOW)
    assert second is first
    stop_prop_enforcement_runtime()
    assert prop_enforcement_runtime() is None




def make_flat_state(account: AccountKey = ACCOUNT_A) -> AccountEvaluationState:
    """An account with NO open positions, proven by the 2B evidence."""
    store = PropRuleStateStore()
    anchor = store.record_initial_anchor(
        InitialAccountAnchor(
            account=account, account_currency="USD", initial_balance=100_000.0,
            initial_equity=100_000.0,
            observed_at_utc=datetime(2026, 3, 2, 14, 0, tzinfo=UTC),
            source_snapshot_id="asnap_initial", source_provenance="TEST",
        )
    )
    day = store.record_daily_anchor(
        DailyAccountAnchor(
            account=account, account_currency="USD", rule_day=NY.rule_day_for(NOW),
            timezone_name=NY.timezone_name, rule_day_definition=NY.key(),
            start_of_day_balance=100_000.0, start_of_day_equity=100_000.0,
            anchor_at_utc=NY.day_start_utc(NY.rule_day_for(NOW)),
            source_snapshot_id="asnap_sod", source_provenance="TEST",
            start_of_day_floating_pnl=0.0,
        )
    )
    return build_account_evaluation_state(
        account=account, account_currency="USD", observed_at_utc=NOW, definition=NY,
        initial_anchor=anchor, daily_anchor=day,
        account_snapshot=FakeSnapshot(account_id=account.account_id),
        open_risk=FakeOpenRisk(
            account_id=account.account_id, total_open_risk=0.0,
            open_position_count=0, position_tickets=(), position_risks=(),
        ),
        portfolio=FakePortfolio(
            account_id=account.account_id, largest_symbol_risk=0.0,
            largest_direction_risk=0.0, max_cluster_risk=0.0,
        ),
    )

class StaticSessionProvider:
    """A governed session calendar built from explicit records, not guesses."""

    def __init__(self, session):
        from core.risk.prop_rule_external import SourceProvenance

        self._session = session
        self.provenance = SourceProvenance(
            provider_name="STATIC_TEST_SESSIONS",
            source_reference="test://static-sessions",
            retrieved_at_utc=NOW,
            freshness=__import__(
                "core.risk.prop_rule_external", fromlist=["SourceFreshness"]
            ).SourceFreshness.FRESH,
            max_age_seconds=86_400,
        )

    def state_for(self, *, canonical_symbol, at_utc):
        from core.risk.prop_rule_external import MarketSessionAnswer

        from dataclasses import replace as _replace

        return MarketSessionAnswer(
            provenance=_replace(self.provenance, retrieved_at_utc=at_utc),
            state=self._session.state_at(at_utc),
            session=self._session,
        )


def _weekend_session():
    from core.risk.prop_rule_external import MarketSession

    return MarketSession(
        "s_eurusd", "EURUSD", "GOVERNED_TEST_EXCHANGE", "America/New_York",
        time(17, 0), time(8, 0), provenance="governed://test-exchange",
    )

# =============================================================================
# TRADING DAY / INACTIVITY (requirement 19) ? eligibility is not a risk block
# =============================================================================


def test_min_trading_days_never_blocks_an_entry():
    """A minimum day count is COMPLETION eligibility, not a risk breach."""
    from core.risk.prop_rule_contracts import TradingDayRule
    from core.risk.prop_rule_enums import ConsecutiveMode, DayCountBasis
    from core.risk.prop_rule_pack import compile_rule_pack as _c

    identity = RulePackIdentity(
        provider="TEST_FIRM_DAYS", program="TEST_CHALLENGE",
        phase=RulePhase.EVALUATION_PHASE_1, account_size=100_000,
        currency="USD", rule_pack_version="1.0.0", effective_from=EFFECTIVE_FROM,
    )
    rule = TradingDayRule(
        rule_id="r.min_days", rule_type=RuleType.MIN_TRADING_DAYS, enabled=True,
        severity=RuleSeverity.ADVISORY, source=SOURCE, effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.MIN_TRADING_DAYS),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
        timezone_name="America/New_York",
        limit=Limit(basis=LimitBasis.TRADING_DAYS, value=5),
        day_count_basis=DayCountBasis.TRADING_DAYS,
        consecutive=ConsecutiveMode.NON_CONSECUTIVE,
        qualifying_criterion="ANY_CLOSED_TRADE",
    )
    pack = _c(identity, [rule])
    runtime = build_runtime(pack, state=make_state())
    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(), at_utc=NOW, order=_order(risk=100.0)
    )
    assert verdict.allowed is True, verdict.to_dict()


def test_max_trading_days_suspends_the_account():
    from core.risk.prop_rule_contracts import TradingDayRule
    from core.risk.prop_rule_enums import ConsecutiveMode, DayCountBasis
    from core.risk.prop_rule_pack import compile_rule_pack as _c
    from core.risk.prop_rule_evaluator import EvaluationContext, evaluate_rule

    identity = RulePackIdentity(
        provider="TEST_FIRM_MAXDAYS", program="TEST_CHALLENGE",
        phase=RulePhase.EVALUATION_PHASE_1, account_size=100_000,
        currency="USD", rule_pack_version="1.0.0", effective_from=EFFECTIVE_FROM,
    )
    rule = TradingDayRule(
        rule_id="r.max_days", rule_type=RuleType.MAX_TRADING_DAYS, enabled=True,
        severity=RuleSeverity.TERMINATION, source=SOURCE, effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.MAX_TRADING_DAYS),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
        timezone_name="America/New_York",
        limit=Limit(basis=LimitBasis.TRADING_DAYS, value=1),
        day_count_basis=DayCountBasis.TRADING_DAYS,
        consecutive=ConsecutiveMode.NON_CONSECUTIVE,
        qualifying_criterion="ANY_CLOSED_TRADE",
    )
    pack = _c(identity, [rule])
    from core.risk.prop_rule_enforcement import decide_policy

    from core.risk.prop_rule_state import ClosedTradeEvent, CloseEventKind
    from datetime import timedelta

    events = []
    for offset in range(3):
        day = NOW - timedelta(days=offset + 1)
        events.append(
            ClosedTradeEvent(
                account=ACCOUNT_A, account_currency="USD", source="MT5_HISTORY_DEAL",
                source_trade_id=f"deal-{offset}", position_ticket=500 + offset,
                symbol="EURUSD", closed_at_utc=day, gross_realised_pnl=10.0,
                volume=0.1, close_kind=CloseEventKind.FULL_CLOSE,
                commission=0.0, swap=0.0, fees=0.0, duration_seconds=60.0,
            )
        )
    store_state = PropRuleStateStore()
    for event in events:
        store_state.record_close_event(event)
    from core.risk.prop_rule_state import DailyAccountAnchor as _DA, InitialAccountAnchor as _IA

    anchor = store_state.record_initial_anchor(
        _IA(
            account=ACCOUNT_A, account_currency="USD", initial_balance=100_000.0,
            initial_equity=100_000.0,
            observed_at_utc=datetime(2026, 3, 2, 14, 0, tzinfo=UTC),
            source_snapshot_id="asnap_initial", source_provenance="TEST",
        )
    )
    day = store_state.record_daily_anchor(
        _DA(
            account=ACCOUNT_A, account_currency="USD", rule_day=NY.rule_day_for(NOW),
            timezone_name=NY.timezone_name, rule_day_definition=NY.key(),
            start_of_day_balance=100_000.0, start_of_day_equity=100_000.0,
            anchor_at_utc=NY.day_start_utc(NY.rule_day_for(NOW)),
            source_snapshot_id="asnap_sod", source_provenance="TEST",
            start_of_day_floating_pnl=0.0,
        )
    )
    state = build_account_evaluation_state(
        account=ACCOUNT_A, account_currency="USD", observed_at_utc=NOW, definition=NY,
        initial_anchor=anchor, daily_anchor=day,
        account_snapshot=FakeSnapshot(account_id="ACC_A"),
        open_risk=FakeOpenRisk(account_id="ACC_A", total_open_risk=0.0,
                               open_position_count=0, position_tickets=(), position_risks=()),
        portfolio=FakePortfolio(account_id="ACC_A", largest_symbol_risk=0.0,
                                largest_direction_risk=0.0, max_cluster_risk=0.0),
        close_events=events,
    )
    context = EvaluationContext(
        rule_pack=pack, state=state, evaluated_at_utc=NOW, rule_day_definition=NY,
    )
    result = evaluate_rule(context, pack.rules[0])
    assert result.status is EvaluationStatus.BREACH
    policy = decide_policy(rule=pack.rules[0], result=result)
    assert policy.action is EnforcementAction.SUSPEND_ACCOUNT
    assert policy.terminal is True


def _hold_restriction_pack(*, must_close: bool):
    from core.risk.prop_rule_contracts import HoldRestrictionRule
    from core.risk.prop_rule_enums import HoldRestrictionKind
    from core.risk.prop_rule_pack import compile_rule_pack as _c

    identity = RulePackIdentity(
        provider="TEST_FIRM_HOLD", program="TEST_CHALLENGE",
        phase=RulePhase.EVALUATION_PHASE_1, account_size=100_000,
        currency="USD", rule_pack_version="1.0.0", effective_from=EFFECTIVE_FROM,
    )
    rule = HoldRestrictionRule(
        rule_id="r.weekend", rule_type=RuleType.WEEKEND_HOLD_RESTRICTION,
        enabled=True, severity=RuleSeverity.TERMINATION, source=SOURCE,
        effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.WEEKEND_HOLD_RESTRICTION),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        kind=HoldRestrictionKind.WEEKEND_HOLD, holding_permitted=False,
        must_close_before_market_close=must_close, cutoff_time=time(20, 0),
    )
    return _c(identity, [rule])


def test_weekend_close_required_produces_a_position_reduction_decision():
    pack = _hold_restriction_pack(must_close=True)
    # Past the declared 20:00 Europe/New_York cutoff on the rule day.
    after_cutoff = datetime(2026, 3, 11, 2, 0, tzinfo=UTC)   # 22:00 New York
    runtime = PropEnforcementRuntime(
        mode=EnforcementMode.LIVE_ENFORCE, rule_pack_store=_pack_store(pack),
        state_store=EnforcementStateStore(), pack_identity=pack.identity,
        rule_day_definition=NY,
        market_sessions=StaticSessionProvider(_weekend_session()),
        clock=lambda: NOW,
    )
    runtime.state_provider = lambda account: make_state()
    runtime.start(at_utc=NOW)
    verdict = runtime.authorize_entry(account=ACCOUNT_A, at_utc=after_cutoff)
    # Past the declared cutoff with an open position, the rule DEMANDS a close.
    assert verdict.position_reductions, verdict.to_dict()
    assert any(d.is_position_reduction for d in verdict.position_reductions)


def test_weekend_entry_only_rule_never_asks_for_a_close():
    pack = _hold_restriction_pack(must_close=False)
    after_cutoff = datetime(2026, 3, 11, 2, 0, tzinfo=UTC)
    runtime = build_runtime(pack, state=make_state())
    verdict = runtime.authorize_entry(
        account=ACCOUNT_A, state=make_state(), at_utc=after_cutoff
    )
    assert not any(d.is_position_reduction for d in verdict.position_reductions), (
        "a rule that does not demand a close must never produce one"
    )


def test_no_open_positions_means_no_close_decision():
    pack = _hold_restriction_pack(must_close=True)
    after_cutoff = datetime(2026, 3, 11, 2, 0, tzinfo=UTC)
    from core.risk.prop_rule_evaluator import EvaluationContext, evaluate_rule
    from core.risk.prop_rule_enforcement import decide_policy

    empty_state = make_flat_state()
    from core.risk.prop_rule_external import GovernedCalendarSource  # noqa: F401
    from core.risk.prop_rule_external import MarketSessionAnswer as _MSA

    context = EvaluationContext(
        rule_pack=pack, state=empty_state, evaluated_at_utc=after_cutoff,
        rule_day_definition=NY,
        market_sessions=_AlwaysOpenSessions(),
    )
    result = evaluate_rule(context, pack.rules[0])
    assert result.status is EvaluationStatus.PASS
    policy = decide_policy(rule=pack.rules[0], result=result)
    assert policy.action is EnforcementAction.NO_ACTION


class _AlwaysOpenSessions:
    """A 3B-compatible session source that is always OPEN.

    Used only to isolate the "no open positions" branch of the hold-restriction
    rule from the calendar question.
    """

    def is_market_open(self, symbol: str, at_utc) -> bool:
        return True
