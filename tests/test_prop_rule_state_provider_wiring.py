"""PRODUCTION 3B STATE-PROVIDER WIRING for Block 3C.

THE GAP THIS FILE CLOSES
------------------------
``PropEnforcementRuntime.state_for`` was reachable, but its ``state_provider``
was only ever supplied by a TEST. A LIVE deployment therefore satisfied
``LIVE_ENFORCE`` + a valid rule pack and still reached ``enforce_positions``
with no governed 3B state at all: every position-reduction evaluation was
silently undecidable.

These tests drive the REAL production path:

    PropRiskTelemetryService.observe_account   (bounded cycle, ONE instant)
      -> enforce_positions_for_account
      -> PropEnforcementRuntime.enforce_positions
      -> PropRuleStateProvider                 (the production provider)
      -> build_account_evaluation_state(...)   (existing 3B)
      -> evaluate_pack(...)                     (existing 3B evaluator)
      -> EnforceDecision -> EnforcementExecutor -> PositionClosePort

and NO test here hand-injects a ``state_provider``.
"""

from __future__ import annotations

import ast
from datetime import date, datetime, time, timezone
from pathlib import Path

import pytest

from core.risk.account_snapshot import AccountIdentity
from core.risk.prop_rule_enforcement import DegradedReason, EnforcementMode
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
from core.risk.prop_rule_pack import (
    RulePack,
    RulePackIdentity,
    RulePackStore,
    compile_rule_pack,
)
from core.risk.prop_rule_runtime import PropEnforcementRuntime
from core.risk.prop_rule_state import (
    AccountKey,
    ClosedTradeEvent,
    CloseEventKind,
    RuleDayDefinition,
)
from core.risk.prop_rule_state_provider import (
    CycleEvidence,
    PropRuleStateProvider,
    STATE_CURRENCY_MISMATCH,
    STATE_INSTANT_MISMATCH,
    STATE_PACK_AMBIGUOUS,
    STATE_PACK_STORE_UNCONFIGURED,
    production_state_provider,
)
from core.risk.telemetry_runtime import PropRiskTelemetryService

UTC = timezone.utc
NOW = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)
#: Past the declared 20:00 America/New_York cutoff on the rule day.
AFTER_CUTOFF = datetime(2026, 3, 11, 2, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parent.parent
NY = RuleDayDefinition("America/New_York", time(0, 0))
ACCOUNT_A_ID = "ACC_A"
ACCOUNT_B_ID = "ACC_B"
SHARED_TICKET = 4242


def identity_for(account_id: str, login: int) -> AccountIdentity:
    return AccountIdentity(
        account_id=account_id, broker="MT5", server="DemoBroker-Server", login=login
    )


# -- real Block 2 evidence producers ----------------------------------------

class StubAccountSource:
    """Broker-shaped account info, so the REAL 2A producer verifies it."""

    def __init__(self, identity: AccountIdentity, *, balance=100_000.0,
                 equity=100_000.0, currency="USD") -> None:
        self._identity = identity
        self._balance = balance
        self._equity = equity
        self._currency = currency
        self.source_name = "TEST_ACCOUNT_INFO"

    def read_account_info(self):
        return {
            "login": self._identity.login, "server": self._identity.server,
            "balance": self._balance, "equity": self._equity, "profit": 0.0,
            "credit": 0.0, "currency": self._currency, "margin": 0.0,
            "margin_free": self._equity, "margin_level": 0.0, "leverage": 100,
            "trade_allowed": True, "trade_expert": True,
            "trade_mode": 0, "margin_mode": 0,
        }


class StubPositionSource:
    """Broker-shaped rows, so the REAL 2B producer builds the evidence."""

    def __init__(self, rows: list) -> None:
        self._rows = rows

    def read_positions(self):
        return list(self._rows)


class StubSpecSource:
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
    def calc_profit(self, *, side, broker_symbol, volume, open_price, close_price):
        return 50.0


class AlwaysOpenSessions:
    def is_market_open(self, symbol: str, at_utc) -> bool:
        return True


class RecordingClosePort:
    """The exact close executor boundary, recorded broker-free."""

    def __init__(self) -> None:
        self.calls: list[CloseRequest] = []

    def close_position(self, request: CloseRequest, *, at_utc: datetime) -> CloseResult:
        self.calls.append(request)
        return CloseResult(
            request=request, outcome=CloseOutcome.CLOSED, attempted_at_utc=at_utc
        )


def mt5_position_row(ticket: int, symbol: str = "EURUSD") -> dict:
    return {
        "ticket": ticket, "symbol": symbol, "type": 0, "volume": 0.5,
        "price_open": 1.1000, "price": 1.1000, "sl": 1.0990, "tp": 0.0,
        "magic": 713_001, "comment": "", "time": int(NOW.timestamp()),
        "time_msc": int(NOW.timestamp() * 1000), "order": ticket, "profit": 0.0,
    }


IDENTITY_A = identity_for(ACCOUNT_A_ID, 111111)
IDENTITY_B = identity_for(ACCOUNT_B_ID, 222222)


# -- the real mandatory-close rule ------------------------------------------

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


def pack_store(pack: RulePack) -> RulePackStore:
    store = RulePackStore()
    store.register_rule_pack(pack)
    return store


# -- the PRODUCTION runtime + the PRODUCTION provider ----------------------

def production_runtime(
    *,
    mode: EnforcementMode = EnforcementMode.LIVE_ENFORCE,
    pack: RulePack | None = None,
    port: RecordingClosePort | None = None,
    state_dir: str | None = None,
    install_provider: bool = True,
) -> PropEnforcementRuntime:
    """A runtime wired EXACTLY the way ``main.py`` wires it in production.

    The provider comes from the production factory; no test-only object is ever
    assigned to ``runtime.state_provider``.
    """
    resolved = pack or weekend_close_pack()
    close_port = port or RecordingClosePort()
    enforcement_store = EnforcementStateStore()
    runtime = PropEnforcementRuntime(
        mode=mode,
        rule_pack_store=pack_store(resolved),
        state_store=enforcement_store,
        executor=EnforcementExecutor(
            store=enforcement_store, close_port=close_port
        ),
        pack_identity=resolved.identity,
        rule_day_definition=NY,
        market_sessions=AlwaysOpenSessions(),
        clock=lambda: AFTER_CUTOFF,
    )
    if install_provider:
        runtime.state_provider = production_state_provider(
            rule_pack_store=pack_store(resolved),
            pack_identity=resolved.identity,
            rule_day_definition=NY,
            base_dir=state_dir,
        )
    return runtime


def production_cycle(
    runtime: PropEnforcementRuntime | None, *,
    identities=(IDENTITY_A,), tickets=(SHARED_TICKET,),
    account_kwargs: dict | None = None,
) -> PropRiskTelemetryService:
    """The REAL bounded telemetry cycle with the PRODUCTION hook attached."""
    account_kwargs = account_kwargs or {}
    return PropRiskTelemetryService(
        identities=list(identities),
        account_sources={
            i.account_id: StubAccountSource(i, **account_kwargs.get(i.account_id, {}))
            for i in identities
        },
        position_sources={
            i.account_id: StubPositionSource([mt5_position_row(t) for t in tickets])
            for i in identities
        },
        spec_source=StubSpecSource(), calculator=StubCalculator(),
        persist=False, clock=lambda: AFTER_CUTOFF,
        position_enforcement=None if runtime is None else _production_hook(runtime),
    )


def _production_hook(runtime: PropEnforcementRuntime):
    """The REAL production hook from the 3C module, bound to this runtime."""
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


def spy_on_cycle_evidence(service: PropRiskTelemetryService) -> dict:
    """Capture the EXACT objects one real cycle produces, for direct provider use."""
    captured: dict = {}
    original = service._enforce_positions

    def _spy(ident, instant, position_set, positions, portfolio,
             account_snapshot=None, open_risk=None):
        captured.update(
            observed_at_utc=instant, account_snapshot=account_snapshot,
            open_risk=open_risk, position_set=position_set, portfolio=portfolio,
        )
        return original(ident, instant, position_set, positions, portfolio,
                        account_snapshot=account_snapshot, open_risk=open_risk)

    service._enforce_positions = _spy
    return captured


def evidence_from_capture(captured: dict) -> CycleEvidence:
    return CycleEvidence(
        observed_at_utc=captured["observed_at_utc"],
        account_snapshot=captured["account_snapshot"],
        open_risk=captured["open_risk"],
        position_set=captured["position_set"],
        portfolio=captured["portfolio"],
    )


# =============================================================================
# A. PRODUCTION REACHABILITY: cycle -> provider -> 3B -> 3C -> exact close
# =============================================================================


def test_production_wiring_reaches_the_exact_governed_close(tmp_path):
    """The headline path, with NO hand-injected test-only state_provider.

    LIVE_ENFORCE + the real mandatory-close pack + the production provider +
    fresh 2A/2B/2C from one bounded cycle must produce the exact ticket close.
    """
    port = RecordingClosePort()
    runtime = production_runtime(port=port, state_dir=str(tmp_path / "state"))
    report = runtime.start(at_utc=AFTER_CUTOFF)
    assert report.ready, report.to_dict()

    service = production_cycle(runtime)
    result = service.observe_account(IDENTITY_A)

    assert result.ok, result.error
    # The exact proven ticket was closed, through the exact close port.
    assert [c.position_ticket for c in port.calls] == [SHARED_TICKET]
    assert port.calls[0].account.account_id == ACCOUNT_A_ID


def test_production_provider_is_a_real_3b_state_object(tmp_path):
    """The provider returns the EXISTING 3B type, built by the existing builder."""
    from core.risk.prop_rule_state import AccountEvaluationState

    runtime = production_runtime(state_dir=str(tmp_path / "state"))
    runtime.start(at_utc=AFTER_CUTOFF)
    service = production_cycle(runtime)
    captured = spy_on_cycle_evidence(service)
    service.observe_account(IDENTITY_A)

    account = AccountKey.from_block2(IDENTITY_A)
    state = runtime.state_provider(account, evidence=evidence_from_capture(captured))
    assert isinstance(state, AccountEvaluationState)
    # Same frozen instant as the cycle, and the exact 2A/2B/2C lineage.
    assert state.observed_at_utc == captured["observed_at_utc"]
    assert state.account_snapshot_id == captured["account_snapshot"].snapshot_id
    assert state.open_risk_snapshot_id == captured["open_risk"].open_risk_id
    assert state.portfolio_exposure_id == captured["portfolio"].portfolio_exposure_id
    # Durable 3B state was established, not invented.
    assert state.initial_anchor is not None
    assert state.daily_anchor is not None
    assert state.high_water is not None


def test_provider_never_reads_a_wall_clock():
    """No wall-clock read may EXIST in the production provider (AST, not text)."""
    tree = ast.parse(
        (ROOT / "core/risk/prop_rule_state_provider.py").read_text(encoding="utf-8")
    )
    clocks = {"now", "time", "utcnow", "monotonic"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None)
            owner = getattr(getattr(func, "value", None), "id", None)
            if name in clocks and (owner in {"datetime", "time", "dt"} or name == "monotonic"):
                raise AssertionError(f"wall-clock read at line {node.lineno}")


# =============================================================================
# B. NON-VACUITY AND MODE SAFETY
# =============================================================================


def test_mutation_bypassing_production_provider_breaks_the_wiring_proof(tmp_path):
    """Controlled mutation: the headline readiness assertion must turn red."""
    runtime = production_runtime(state_dir=str(tmp_path / "state"))
    original = runtime.state_provider
    try:
        runtime.state_provider = None
        with pytest.raises(AssertionError):
            report = runtime.start(at_utc=AFTER_CUTOFF)
            assert report.ready, report.to_dict()
    finally:
        runtime.state_provider = original
    assert runtime.start(at_utc=AFTER_CUTOFF).ready


def test_unavailable_state_blocks_entry_but_never_force_closes():
    port = RecordingClosePort()
    runtime = production_runtime(port=port, install_provider=False)
    report = runtime.start(at_utc=AFTER_CUTOFF)
    assert not report.ready
    assert report.degraded_reason is DegradedReason.STATE_UNAVAILABLE

    service = production_cycle(runtime)
    assert service.observe_account(IDENTITY_A).ok
    assert port.calls == []
    verdict = runtime.authorize_entry(
        account=AccountKey.from_block2(IDENTITY_A), at_utc=AFTER_CUTOFF
    )
    assert not verdict.allowed
    assert verdict.degraded_reason is DegradedReason.STATE_UNAVAILABLE


def test_simulate_only_uses_same_evaluation_with_zero_broker_side_effect(tmp_path):
    live_port, sim_port = RecordingClosePort(), RecordingClosePort()
    live = production_runtime(port=live_port, state_dir=str(tmp_path / "live"))
    simulated = production_runtime(
        mode=EnforcementMode.SIMULATE_ONLY,
        port=sim_port,
        state_dir=str(tmp_path / "sim"),
    )
    assert live.start(at_utc=AFTER_CUTOFF).ready
    assert simulated.start(at_utc=AFTER_CUTOFF).ready

    decisions: dict[str, tuple] = {}
    for label, runtime in (("live", live), ("simulated", simulated)):
        original = runtime.evaluate

        def recording_evaluate(*args, _label=label, _original=original, **kwargs):
            result = tuple(_original(*args, **kwargs))
            decisions[_label] = result
            return result

        runtime.evaluate = recording_evaluate

    production_cycle(live).observe_account(IDENTITY_A)
    production_cycle(simulated).observe_account(IDENTITY_A)

    assert decisions["live"] == decisions["simulated"]
    assert [call.position_ticket for call in live_port.calls] == [SHARED_TICKET]
    assert sim_port.calls == []
    simulated_audits = simulated._state_store.audit_records()
    assert any(
        record.mode == EnforcementMode.SIMULATE_ONLY.value
        and record.action_result == "SKIPPED_SIMULATE_ONLY"
        for record in simulated_audits
    )


def test_disabled_mode_does_not_require_or_call_a_provider():
    runtime = PropEnforcementRuntime(mode=EnforcementMode.DISABLED)
    report = runtime.start(at_utc=AFTER_CUTOFF)
    assert report.ready
    assert report.degraded_reason is DegradedReason.PROP_MODE_DISABLED
    assert not hasattr(runtime, "state_provider")
    assert runtime.enforce_positions(
        account=AccountKey.from_block2(IDENTITY_A), at_utc=AFTER_CUTOFF,
        open_tickets=(SHARED_TICKET,),
    ) == ()


# =============================================================================
# C. RESTART RECONSTRUCTION
# =============================================================================


def _closed_trade(account: AccountKey, source_id: str = "deal-restart"):
    return ClosedTradeEvent(
        account=account, account_currency="USD", source="MT5_HISTORY_DEAL",
        source_trade_id=source_id, position_ticket=SHARED_TICKET,
        symbol="EURUSD", closed_at_utc=NOW, gross_realised_pnl=-125.0,
        volume=0.5, close_kind=CloseEventKind.FULL_CLOSE,
        commission=-2.0, swap=0.0, fees=0.0,
    )


def test_provider_restart_reconstructs_all_3b_families_and_outcome(tmp_path):
    state_dir = str(tmp_path / "state")
    pack = weekend_close_pack()
    first_provider = production_state_provider(
        rule_pack_store=pack_store(pack), pack_identity=pack.identity,
        rule_day_definition=NY, base_dir=state_dir,
    )
    service = production_cycle(None)
    capture = spy_on_cycle_evidence(service)
    assert service.observe_account(IDENTITY_A).ok
    evidence = evidence_from_capture(capture)
    account = AccountKey.from_block2(IDENTITY_A)
    first_provider.state_store.record_close_event(_closed_trade(account))
    before = first_provider(account, at_utc=AFTER_CUTOFF, evidence=evidence)
    assert before is not None

    restarted_provider = production_state_provider(
        rule_pack_store=pack_store(pack), pack_identity=pack.identity,
        rule_day_definition=NY, base_dir=state_dir,
    )
    after = restarted_provider(account, at_utc=AFTER_CUTOFF, evidence=evidence)
    assert after is not None
    assert after.initial_anchor.to_dict() == before.initial_anchor.to_dict()
    assert after.daily_anchor.to_dict() == before.daily_anchor.to_dict()
    assert after.high_water.to_dict() == before.high_water.to_dict()
    assert after.daily_ledger.to_dict() == before.daily_ledger.to_dict()
    assert after.trading_day_history.to_dict() == before.trading_day_history.to_dict()
    assert after.evaluation_id == before.evaluation_id

    first_port, restarted_port = RecordingClosePort(), RecordingClosePort()
    first_runtime = production_runtime(port=first_port, pack=pack)
    restarted_runtime = production_runtime(port=restarted_port, pack=pack)
    first_runtime.state_provider = first_provider
    restarted_runtime.state_provider = restarted_provider
    assert first_runtime.start(at_utc=AFTER_CUTOFF).ready
    assert restarted_runtime.start(at_utc=AFTER_CUTOFF).ready
    production_cycle(first_runtime).observe_account(IDENTITY_A)
    production_cycle(restarted_runtime).observe_account(IDENTITY_A)
    assert first_port.calls == restarted_port.calls
    assert [call.position_ticket for call in restarted_port.calls] == [SHARED_TICKET]


# =============================================================================
# D. MULTI-ACCOUNT ISOLATION AND SAME-CYCLE COHERENCE
# =============================================================================


def test_same_ticket_and_time_are_isolated_and_a_failure_cannot_contaminate_b(tmp_path):
    port = RecordingClosePort()
    runtime = production_runtime(port=port, state_dir=str(tmp_path / "state"))
    assert runtime.start(at_utc=AFTER_CUTOFF).ready
    service = production_cycle(
        runtime, identities=(IDENTITY_A, IDENTITY_B),
        account_kwargs={ACCOUNT_A_ID: {"currency": "EUR"}},
    )

    a_result = service.observe_account(IDENTITY_A, instant=AFTER_CUTOFF)
    b_result = service.observe_account(IDENTITY_B, instant=AFTER_CUTOFF)
    assert a_result.ok and b_result.ok
    assert runtime.state_provider.last_failure[ACCOUNT_A_ID].startswith(
        STATE_CURRENCY_MISMATCH
    )
    assert [call.account.identity for call in port.calls] == [
        AccountKey.from_block2(IDENTITY_B).identity
    ]
    assert [call.position_ticket for call in port.calls] == [SHARED_TICKET]
    assert runtime.state_provider.state_store.initial_anchor_for(
        AccountKey.from_block2(IDENTITY_A)
    ) is None
    assert runtime.state_provider.state_store.initial_anchor_for(
        AccountKey.from_block2(IDENTITY_B)
    ) is not None


def test_provider_receives_exact_frozen_instant_and_2a_2b_2c_objects(tmp_path):
    runtime = production_runtime(state_dir=str(tmp_path / "state"))
    provider = runtime.state_provider
    seen: dict = {}

    def observing_provider(account, *, at_utc=None, evidence=None):
        seen.update(account=account, at_utc=at_utc, evidence=evidence)
        return provider(account, at_utc=at_utc, evidence=evidence)

    runtime.state_provider = observing_provider
    assert runtime.start(at_utc=AFTER_CUTOFF).ready
    service = production_cycle(runtime)
    captured = spy_on_cycle_evidence(service)
    assert service.observe_account(IDENTITY_A, instant=AFTER_CUTOFF).ok

    evidence = seen["evidence"]
    assert seen["at_utc"] is AFTER_CUTOFF
    assert evidence.observed_at_utc is AFTER_CUTOFF
    assert evidence.account_snapshot is captured["account_snapshot"]
    assert evidence.open_risk is captured["open_risk"]
    assert evidence.position_set is captured["position_set"]
    assert evidence.portfolio is captured["portfolio"]


def test_provider_rejects_split_cycle_instants(tmp_path):
    pack = weekend_close_pack()
    provider = production_state_provider(
        rule_pack_store=pack_store(pack), pack_identity=pack.identity,
        rule_day_definition=NY, base_dir=str(tmp_path / "state"),
    )
    service = production_cycle(None)
    captured = spy_on_cycle_evidence(service)
    service.observe_account(IDENTITY_A, instant=AFTER_CUTOFF)
    evidence = evidence_from_capture(captured)
    account = AccountKey.from_block2(IDENTITY_A)
    assert provider(account, at_utc=NOW, evidence=evidence) is None
    assert provider.last_failure[ACCOUNT_A_ID] == STATE_INSTANT_MISMATCH


# =============================================================================
# E. RULE-PACK BINDING
# =============================================================================


def test_pack_binding_is_exact_and_missing_or_ambiguous_never_falls_back(tmp_path):
    pack = weekend_close_pack()
    service = production_cycle(None)
    captured = spy_on_cycle_evidence(service)
    service.observe_account(IDENTITY_A, instant=AFTER_CUTOFF)
    evidence = evidence_from_capture(captured)
    account = AccountKey.from_block2(IDENTITY_A)

    exact = production_state_provider(
        rule_pack_store=pack_store(pack), pack_identity=pack.identity,
        rule_day_definition=NY, base_dir=str(tmp_path / "exact"),
    )
    state = exact(account, at_utc=AFTER_CUTOFF, evidence=evidence)
    assert state is not None
    assert state.rule_pack_id == pack.rule_pack_id
    assert pack.identity.phase is RulePhase.EVALUATION_PHASE_1
    assert pack.identity.account_size == 100_000
    assert pack.identity.currency == "USD"

    missing = production_state_provider(
        rule_pack_store=None, pack_identity=pack.identity,
        rule_day_definition=NY, base_dir=str(tmp_path / "missing"),
    )
    assert missing(account, at_utc=AFTER_CUTOFF, evidence=evidence) is None
    assert missing.last_failure[ACCOUNT_A_ID] == STATE_PACK_STORE_UNCONFIGURED

    class AmbiguousStore:
        def find_rule_pack(self, *args, **kwargs):
            from core.risk.prop_rule_pack import AmbiguousRulePackError
            raise AmbiguousRulePackError("MULTIPLE_RULE_PACKS_MATCH")

    ambiguous = production_state_provider(
        rule_pack_store=AmbiguousStore(), pack_identity=pack.identity,
        rule_day_definition=NY, base_dir=str(tmp_path / "ambiguous"),
    )
    assert ambiguous(account, at_utc=AFTER_CUTOFF, evidence=evidence) is None
    assert ambiguous.last_failure[ACCOUNT_A_ID] == STATE_PACK_AMBIGUOUS


def test_main_contains_one_explicit_production_provider_wiring_call():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert source.count("build_production_enforcement_wiring(config)") == 1
    assert "weekend_close_pack" not in source
