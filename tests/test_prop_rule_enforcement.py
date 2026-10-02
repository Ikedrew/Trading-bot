"""Runtime PROP RULE ENFORCEMENT contract (Block 3C).

Covers the enforcement authority end to end: the five evaluation statuses
mapping to EXPLICIT actions, the criticality model, deterministic enforcement
identity, idempotency, action precedence, action coalescing, scope-aware
symbol/account blocking, recovery classification, the external-source freshness
model, and the guarantee that 3C duplicates none of 3B's rule arithmetic.

Everything is pure and deterministic: no broker, no wall clock, no threads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone

import pytest

from core.risk.prop_rule_contracts import (
    DailyLossRule,
    DrawdownRule,
    HoldRestrictionRule,
    OpenRiskLimitRule,
    PnLComponents,
    PositionLimitRule,
    RuleSource,
    required_telemetry_for,
)
from core.risk.prop_rule_enums import (
    BreachKind,
    CurrencySemantics,
    DailyResetPolicy,
    DrawdownKind,
    EvaluationSupport,
    EvaluationTimeBasis,
    HoldRestrictionKind,
    LimitBasis,
    PrecedenceLevel,
    RulePhase,
    RuleSeverity,
    RuleType,
    SourceType,
)
from core.risk.prop_rule_evaluator import (
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    ExplanationCode,
    evaluate_rule,
)
from core.risk.prop_rule_enforcement import (
    ACTION_PRECEDENCE,
    ACTION_RANK,
    ENTRY_BLOCKING_ACTIONS,
    FORBIDDEN_OPERATIONS,
    POSITION_REDUCTION_ACTIONS,
    EnforceDecision,
    EnforcementAction,
    EnforcementCriticality,
    EnforcementError,
    EnforcementMode,
    EnforcementReason,
    EnforcementScope,
    EnforcementSeverity,
    RecoveryClass,
    coalesce_decisions,
    compile_decision,
    criticality_for,
    decide_policy,
    derive_enforcement_id,
    is_mandatory_for_safe_operation,
)
from core.risk.prop_rule_external import (
    EconomicCalendarSnapshot,
    EconomicEvent,
    MarketSession,
    MarketSessionAnswer,
    MarketState,
    SourceFreshness,
    SourceProvenance,
    unavailable_calendar,
)
from core.risk.prop_rule_state import AccountKey
from core.risk.prop_rule_values import Limit

UTC = timezone.utc
EVAL_AT = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)
ACCOUNT = AccountKey("ACC_A", "MT5", "DemoBroker-Server", 111111)
ACCOUNT_B = AccountKey("ACC_B", "MT5", "DemoBroker-Server", 222222)
EFFECTIVE_FROM = datetime(2026, 1, 1, tzinfo=UTC)

SOURCE = RuleSource(
    source_type=SourceType.OFFICIAL_RULE_PAGE,
    source_reference="https://example.invalid/rules",
    retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
)


# ??? builders ?????????????????????????????????????????????????????????????????


def _daily_loss_rule(value: float = 5.0, **kwargs) -> DailyLossRule:
    params = dict(
        rule_id="r.daily_loss",
        rule_type=RuleType.DAILY_LOSS_LIMIT,
        enabled=True,
        severity=RuleSeverity.TERMINATION,
        source=SOURCE,
        effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.DAILY_LOSS_LIMIT),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        breach_kind=BreachKind.HARD,
        timezone_name="America/New_York",
        limit=Limit(
            basis=LimitBasis.PERCENT_START_OF_DAY_BALANCE,
            value=value,
            currency_semantics=CurrencySemantics.ACCOUNT_CURRENCY,
        ),
        reset_policy=DailyResetPolicy.CALENDAR_DAY,
        reset_time=time(0, 0),
    )
    params.update(kwargs)
    return DailyLossRule(**params)


def _drawdown_rule(kind: DrawdownKind = DrawdownKind.STATIC, value: float = 10.0, **kwargs):
    anchor = (
        LimitBasis.PERCENT_INITIAL_BALANCE
        if kind is DrawdownKind.STATIC
        else LimitBasis.PERCENT_HIGH_WATER_EQUITY
    )
    params = dict(
        rule_id="r.drawdown",
        rule_type=(
            RuleType.STATIC_DRAWDOWN if kind is DrawdownKind.STATIC else RuleType.TRAILING_DRAWDOWN
        ),
        enabled=True,
        severity=RuleSeverity.TERMINATION,
        source=SOURCE,
        effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.STATIC_DRAWDOWN),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        kind=kind,
        limit=Limit(
            basis=anchor, value=value,
            currency_semantics=CurrencySemantics.ACCOUNT_CURRENCY,
        ),
        anchor=anchor,
    )
    if kind is DrawdownKind.TRAILING:
        params.update(trail_reference="EQUITY", trail_update_cadence="EOD")
    params.update(kwargs)
    return DrawdownRule(**params)


def _open_risk_rule(value: float = 5.0, scope: str = "TOTAL", **kwargs) -> OpenRiskLimitRule:
    rule_type = {
        "TOTAL": RuleType.MAX_OPEN_RISK,
        "PER_SYMBOL": RuleType.MAX_RISK_PER_SYMBOL,
        "CORRELATION_CLUSTER": RuleType.MAX_CORRELATED_RISK,
        "DIRECTIONAL": RuleType.MAX_DIRECTIONAL_RISK,
    }[scope]
    params = dict(
        rule_id=f"r.open_risk.{scope.lower()}",
        rule_type=rule_type,
        enabled=True,
        severity=RuleSeverity.BREACH,
        source=SOURCE,
        effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(rule_type),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        limit=Limit(
            basis=LimitBasis.PERCENT_CURRENT_EQUITY, value=value,
            currency_semantics=CurrencySemantics.ACCOUNT_CURRENCY,
        ),
        scope_kind=scope,
    )
    params.update(kwargs)
    return OpenRiskLimitRule(**params)


def _position_rule(value: float = 3.0, basis=LimitBasis.POSITION_COUNT, **kwargs):
    rule_type = (
        RuleType.MAX_POSITION_SIZE if basis is LimitBasis.LOT_COUNT
        else RuleType.MAX_OPEN_POSITIONS
    )
    params = dict(
        rule_id="r.position_limit",
        rule_type=rule_type,
        enabled=True,
        severity=RuleSeverity.BREACH,
        source=SOURCE,
        effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(rule_type),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        limit=Limit(basis=basis, value=value),
    )
    params.update(kwargs)
    return PositionLimitRule(**params)


def _hold_rule(holding_permitted: bool, must_close: bool, **kwargs) -> HoldRestrictionRule:
    params = dict(
        rule_id="r.weekend_hold",
        rule_type=RuleType.WEEKEND_HOLD_RESTRICTION,
        enabled=True,
        severity=RuleSeverity.TERMINATION,
        source=SOURCE,
        effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.WEEKEND_HOLD_RESTRICTION),
        evaluation_time_basis=EvaluationTimeBasis.CONTINUOUS_INTRADAY,
        timezone_name="America/New_York",
        kind=HoldRestrictionKind.WEEKEND_HOLD,
        holding_permitted=holding_permitted,
        must_close_before_market_close=must_close,
        cutoff_time=time(20, 0),
    )
    params.update(kwargs)
    return HoldRestrictionRule(**params)


def make_result(
    rule,
    status: EvaluationStatus,
    *,
    account_id: str = "ACC_A",
    rule_id: str | None = None,
    current: float | None = None,
    limit: float | None = None,
    explanation: ExplanationCode = ExplanationCode.WITHIN_LIMIT,
    evaluation_id: str = "ev_test_1",
    rule_pack_id: str = "pack_test",
) -> EvaluationResult:
    """Build an immutable 3B-shaped result without re-running the evaluator."""
    return EvaluationResult(
        evaluation_id=evaluation_id,
        rule_pack_id=rule_pack_id,
        rule_id=rule_id or rule.rule_id,
        rule_type=rule.rule_type,
        account_id=account_id,
        evaluated_at_utc=EVAL_AT,
        status=status,
        rule_day=date(2026, 3, 10),
        timezone_name="America/New_York",
        unit="MONEY",
        current_value=current,
        limit_value=limit,
        breach=status is EvaluationStatus.BREACH,
        support_status=EvaluationSupport.SUPPORTED_BY_CURRENT_TELEMETRY,
        explanation_code=explanation,
        detail="TEST",
    )


def _minimal_state(**overrides):
    """A minimal, explicit evaluation state for projection assertions."""
    from core.risk.prop_rule_state import (
        AccountEvaluationState,
        HighWaterState,
        StateStatus,
    )

    base = dict(
        account=ACCOUNT,
        account_currency="USD",
        rule_pack_id="pack_test",
        evaluation_id="eval_current",
        observed_at_utc=EVAL_AT,
        observed_at_utc_ms=1773150000000,
        rule_day=date(2026, 3, 10),
        rule_timezone="America/New_York",
        rule_day_definition="America/New_York@00:00:00",
        current_balance=100_000.0,
        current_equity=100_000.0,
        high_water_balance=100_000.0,
        high_water_equity=100_000.0,
        total_open_risk=1_000.0,
        position_count=1,
        largest_position_risk=1_000.0,
        largest_lot_size=0.3,
        largest_symbol_risk=1_000.0,
        largest_symbol="EURUSD",
        correlated_risk=1_000.0,
        directional_risk=1_000.0,
        largest_direction="LONG",
        status=StateStatus.COMPLETE,
    )
    base.update(overrides)
    return AccountEvaluationState(**base)



# =============================================================================
# 1-5  STATUS -> ACTION POLICY
# =============================================================================


def test_pass_never_blocks():
    rule = _daily_loss_rule()
    outcome = decide_policy(rule=rule, result=make_result(rule, EvaluationStatus.PASS))
    assert outcome.action is EnforcementAction.NO_ACTION
    assert outcome.action_required is False
    assert outcome.terminal is False


def test_breach_of_daily_loss_blocks_all_entries_terminally():
    rule = _daily_loss_rule()
    outcome = decide_policy(rule=rule, result=make_result(rule, EvaluationStatus.BREACH))
    assert outcome.action is EnforcementAction.BLOCK_ALL_ENTRIES
    assert outcome.criticality is EnforcementCriticality.HARD_STOP
    assert outcome.action_required is True
    assert outcome.terminal is True
    assert outcome.recovery is RecoveryClass.RULE_DAY_RESET


def test_indeterminate_daily_loss_blocks_new_risk_but_never_liquidates():
    """The mandatory distinction: block NEW RISK, do NOT force-close."""
    rule = _daily_loss_rule()
    outcome = decide_policy(
        rule=rule,
        result=make_result(rule, EvaluationStatus.INDETERMINATE,
                           explanation=ExplanationCode.TELEMETRY_MISSING),
    )
    assert outcome.action is EnforcementAction.INDETERMINATE_BLOCK
    assert outcome.action_required is True
    assert outcome.action not in POSITION_REDUCTION_ACTIONS
    assert outcome.fail_closed_reason == EnforcementReason.EVIDENCE_INDETERMINATE.value


def test_indeterminate_advisory_rule_does_not_block():
    """Criticality is explicit: an undecidable ADVISORY rule blocks nothing."""
    from core.risk.prop_rule_contracts import ProfitTargetRule

    rule = ProfitTargetRule(
        rule_id="r.target",
        rule_type=RuleType.PROFIT_TARGET,
        enabled=True,
        severity=RuleSeverity.ADVISORY,
        source=SOURCE,
        effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.PROFIT_TARGET),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
        timezone_name="America/New_York",
        limit=Limit(
            basis=LimitBasis.PERCENT_INITIAL_BALANCE, value=8.0,
            currency_semantics=CurrencySemantics.ACCOUNT_CURRENCY,
        ),
    )
    outcome = decide_policy(rule=rule, result=make_result(rule, EvaluationStatus.INDETERMINATE))
    assert outcome.criticality is EnforcementCriticality.ADVISORY
    assert outcome.action is EnforcementAction.NO_ACTION
    assert outcome.action_required is False


def test_not_applicable_is_no_action():
    rule = _daily_loss_rule()
    outcome = decide_policy(rule=rule, result=make_result(rule, EvaluationStatus.NOT_APPLICABLE))
    assert outcome.action is EnforcementAction.NO_ACTION


def test_unsupported_mandatory_rule_fails_closed():
    rule = _open_risk_rule()
    outcome = decide_policy(rule=rule, result=make_result(rule, EvaluationStatus.UNSUPPORTED))
    assert outcome.action is EnforcementAction.INDETERMINATE_BLOCK
    assert outcome.fail_closed_reason == EnforcementReason.RULE_TYPE_UNSUPPORTED.value


def test_profit_target_breach_is_not_an_implicit_stop():
    from core.risk.prop_rule_contracts import ProfitTargetRule

    rule = ProfitTargetRule(
        rule_id="r.target",
        rule_type=RuleType.PROFIT_TARGET,
        enabled=True,
        severity=RuleSeverity.ADVISORY,
        source=SOURCE,
        effective_from=EFFECTIVE_FROM,
        telemetry_requirements=required_telemetry_for(RuleType.PROFIT_TARGET),
        evaluation_time_basis=EvaluationTimeBasis.END_OF_DAY,
        timezone_name="America/New_York",
        limit=Limit(
            basis=LimitBasis.PERCENT_INITIAL_BALANCE, value=8.0,
            currency_semantics=CurrencySemantics.ACCOUNT_CURRENCY,
        ),
    )
    # Reaching the target is a BREACH of the requirement, not a risk violation.
    outcome = decide_policy(
        rule=rule,
        result=make_result(rule, EvaluationStatus.BREACH,
                           explanation=ExplanationCode.TARGET_MET),
    )
    assert outcome.action is EnforcementAction.NO_ACTION
    assert outcome.reason is EnforcementReason.TARGET_MET


def test_weekend_close_required_produces_close_action():
    rule = _hold_rule(holding_permitted=False, must_close=True)
    outcome = decide_policy(rule=rule, result=make_result(rule, EvaluationStatus.BREACH))
    assert outcome.action in POSITION_REDUCTION_ACTIONS


def test_weekend_entry_only_rule_never_closes():
    """The rule blocks holding but does not demand a close -> block entries only."""
    rule = _hold_rule(holding_permitted=False, must_close=False)
    outcome = decide_policy(rule=rule, result=make_result(rule, EvaluationStatus.BREACH))
    assert outcome.action is EnforcementAction.BLOCK_NEW_ENTRY
    assert outcome.action not in POSITION_REDUCTION_ACTIONS


# =============================================================================
# 6-7  DETERMINISTIC IDENTITY AND IDEMPOTENCY
# =============================================================================


def test_enforcement_id_is_deterministic_for_same_evidence_and_action():
    rule = _daily_loss_rule()
    result = make_result(rule, EvaluationStatus.BREACH)
    a = compile_decision(rule=rule, result=result, account=ACCOUNT, effective_at_utc=EVAL_AT)
    b = compile_decision(rule=rule, result=result, account=ACCOUNT, effective_at_utc=EVAL_AT)
    assert a.enforcement_id == b.enforcement_id
    assert a.enforcement_id.startswith("enf_")


def test_enforcement_id_changes_with_action():
    rule = _daily_loss_rule()
    base = dict(
        rule_pack_id="p", rule_id="r", account=ACCOUNT, evaluation_id="ev",
    )
    one = derive_enforcement_id(action=EnforcementAction.BLOCK_ALL_ENTRIES,
                               scope=EnforcementScope.ACCOUNT, **base)
    two = derive_enforcement_id(action=EnforcementAction.INDETERMINATE_BLOCK,
                               scope=EnforcementScope.ACCOUNT, **base)
    assert one != two


def test_enforcement_id_is_account_scoped():
    rule = _daily_loss_rule()
    result = make_result(rule, EvaluationStatus.BREACH)
    a = compile_decision(rule=rule, result=result, account=ACCOUNT, effective_at_utc=EVAL_AT)
    b = compile_decision(rule=rule, result=result, account=ACCOUNT_B, effective_at_utc=EVAL_AT)
    assert a.enforcement_id != b.enforcement_id


def test_same_action_on_different_evaluation_yields_new_id():
    """A material evidence change is a genuinely new decision, not a duplicate."""
    rule = _daily_loss_rule()
    one = compile_decision(
        rule=rule, result=make_result(rule, EvaluationStatus.BREACH, evaluation_id="ev_1"),
        account=ACCOUNT, effective_at_utc=EVAL_AT,
    )
    two = compile_decision(
        rule=rule, result=make_result(rule, EvaluationStatus.BREACH, evaluation_id="ev_2"),
        account=ACCOUNT, effective_at_utc=EVAL_AT,
    )
    assert one.enforcement_id != two.enforcement_id


def test_blocking_action_requires_action_required():
    """A block that does nothing would be a silent pass; it is rejected."""
    rule = _daily_loss_rule()
    result = make_result(rule, EvaluationStatus.BREACH)
    decision = compile_decision(
        rule=rule, result=result, account=ACCOUNT, effective_at_utc=EVAL_AT
    )
    with pytest.raises(EnforcementError):
        EnforceDecision(
            enforcement_id=decision.enforcement_id,
            rule_pack_id=decision.rule_pack_id,
            rule_id=decision.rule_id,
            rule_type=decision.rule_type,
            account=ACCOUNT,
            evaluation_id="ev",
            evaluated_status=EvaluationStatus.BREACH,
            enforcement_action=EnforcementAction.BLOCK_ALL_ENTRIES,
            criticality=EnforcementCriticality.HARD_STOP,
            severity=EnforcementSeverity.TERMINAL,
            effective_at_utc=EVAL_AT,
            applies_to=EnforcementScope.ACCOUNT,
            reason_code=EnforcementReason.LIMIT_EXCEEDED,
            action_required=False,
        )


# =============================================================================
# 8-10  SCOPE, SYMBOL BLOCKING, ACCOUNT ISOLATION
# =============================================================================


def test_account_wide_rule_blocks_every_symbol():
    rule = _daily_loss_rule()
    decision = compile_decision(
        rule=rule, result=make_result(rule, EvaluationStatus.BREACH),
        account=ACCOUNT, effective_at_utc=EVAL_AT,
    )
    assert decision.applies_to is EnforcementScope.ACCOUNT
    assert decision.covers_symbol("EURUSD")
    assert decision.covers_symbol("XAUUSD")


def test_symbol_scoped_rule_blocks_only_that_symbol():
    rule = _open_risk_rule(scope="PER_SYMBOL", applies_to_symbols=("EURUSD",))
    decision = compile_decision(
        rule=rule, result=make_result(rule, EvaluationStatus.BREACH),
        account=ACCOUNT, effective_at_utc=EVAL_AT,
    )
    assert decision.applies_to is EnforcementScope.SYMBOL
    assert decision.enforcement_action is EnforcementAction.BLOCK_SYMBOL_ENTRY
    assert decision.symbols == ("EURUSD",)
    assert decision.covers_symbol("EURUSD")
    assert not decision.covers_symbol("XAUUSD")


def test_decisions_never_cross_accounts():
    rule = _daily_loss_rule()
    result = make_result(rule, EvaluationStatus.BREACH)
    a = compile_decision(rule=rule, result=result, account=ACCOUNT, effective_at_utc=EVAL_AT)
    b = compile_decision(rule=rule, result=result, account=ACCOUNT_B, effective_at_utc=EVAL_AT)
    assert a.account_identity != b.account_identity
    assert a.account_id == "ACC_A"
    assert b.account_id == "ACC_B"


def test_effective_set_is_per_account():
    rule_a = _daily_loss_rule()
    breach_a = compile_decision(
        rule=rule_a, result=make_result(rule_a, EvaluationStatus.BREACH),
        account=ACCOUNT, effective_at_utc=EVAL_AT,
    )
    rule_b = _daily_loss_rule()
    pass_b = compile_decision(
        rule=rule_b, result=make_result(rule_b, EvaluationStatus.PASS),
        account=ACCOUNT_B, effective_at_utc=EVAL_AT,
    )
    set_a = coalesce_decisions([breach_a], account=ACCOUNT, effective_at_utc=EVAL_AT)
    set_b = coalesce_decisions([pass_b], account=ACCOUNT_B, effective_at_utc=EVAL_AT)
    assert set_a.is_blocked
    assert not set_b.is_blocked


# =============================================================================
# 20-24  PRECEDENCE AND COALESCING
# =============================================================================


def test_action_precedence_is_strictly_ordered():
    ranks = [ACTION_RANK[a] for a in ACTION_PRECEDENCE]
    assert ranks == sorted(ranks)
    assert ACTION_RANK[EnforcementAction.KILL_SWITCH] < ACTION_RANK[EnforcementAction.ALLOW]
    assert (
        ACTION_RANK[EnforcementAction.BLOCK_ALL_ENTRIES]
        < ACTION_RANK[EnforcementAction.BLOCK_SYMBOL_ENTRY]
    )


def test_kill_switch_outranks_every_block():
    assert all(
        ACTION_RANK[EnforcementAction.KILL_SWITCH] < ACTION_RANK[other]
        for other in ACTION_PRECEDENCE
        if other is not EnforcementAction.KILL_SWITCH
    )


def _blocking_decision(rule, action, scope, *, account=ACCOUNT, rule_id=None, symbols=()):
    return compile_decision(
        rule=rule,
        result=make_result(rule, EvaluationStatus.BREACH, rule_id=rule_id or rule.rule_id),
        account=account,
        effective_at_utc=EVAL_AT,
    )


def test_most_restrictive_action_wins_regardless_of_iteration_order():
    kill = EnforceDecision(
        enforcement_id="enf_kill", rule_pack_id="p", rule_id="r.kill",
        rule_type=RuleType.DAILY_LOSS_LIMIT, account=ACCOUNT, evaluation_id="ev_k",
        evaluated_status=EvaluationStatus.BREACH,
        enforcement_action=EnforcementAction.KILL_SWITCH,
        criticality=EnforcementCriticality.HARD_STOP,
        severity=EnforcementSeverity.TERMINAL, effective_at_utc=EVAL_AT,
        applies_to=EnforcementScope.ACCOUNT, reason_code=EnforcementReason.LIMIT_EXCEEDED,
        action_required=True, terminal=True,
    )
    block_rule = _daily_loss_rule()
    block = compile_decision(
        rule=block_rule, result=make_result(block_rule, EvaluationStatus.BREACH),
        account=ACCOUNT, effective_at_utc=EVAL_AT,
    )
    forward = coalesce_decisions([block, kill], account=ACCOUNT, effective_at_utc=EVAL_AT)
    reverse = coalesce_decisions([kill, block], account=ACCOUNT, effective_at_utc=EVAL_AT)
    assert forward.effective_action is EnforcementAction.KILL_SWITCH
    assert reverse.effective_action is EnforcementAction.KILL_SWITCH
    assert forward.primary.enforcement_id == reverse.primary.enforcement_id


def test_identical_actions_are_coalesced_into_one_transition():
    """Three rules demanding the same block -> ONE transition, three refs."""
    decisions = []
    for index in range(3):
        rule = _daily_loss_rule(rule_id=f"r.daily_loss.{index}")
        decisions.append(
            compile_decision(
                rule=rule,
                result=make_result(rule, EvaluationStatus.BREACH,
                                   rule_id=rule.rule_id, evaluation_id=f"ev_{index}"),
                account=ACCOUNT, effective_at_utc=EVAL_AT,
            )
        )
    effective = coalesce_decisions(decisions, account=ACCOUNT, effective_at_utc=EVAL_AT)
    block_transitions = [
        t for t in effective.transitions
        if t.enforcement_action is EnforcementAction.BLOCK_ALL_ENTRIES
    ]
    assert len(block_transitions) == 1
    assert len(effective.all_decisions) == 3
    assert len(block_transitions[0].source_evaluation_ids) == 3


def test_multiple_simultaneous_breaches_produce_one_deterministic_set():
    daily = _daily_loss_rule()
    drawdown = _drawdown_rule()
    risk = _open_risk_rule()
    decisions = [
        compile_decision(rule=r, result=make_result(r, EvaluationStatus.BREACH),
                         account=ACCOUNT, effective_at_utc=EVAL_AT)
        for r in (daily, drawdown, risk)
    ]
    first = coalesce_decisions(decisions, account=ACCOUNT, effective_at_utc=EVAL_AT)
    second = coalesce_decisions(list(reversed(decisions)), account=ACCOUNT, effective_at_utc=EVAL_AT)
    assert first.effective_action == second.effective_action
    assert [d.enforcement_id for d in first.transitions] == [d.enforcement_id for d in second.transitions]
    # Every source evaluation stays referenced somewhere.
    assert sum(len(d.source_evaluation_ids) for d in first.all_decisions) == 3


def test_no_action_decisions_are_not_runtime_transitions():
    rule = _daily_loss_rule()
    decision = compile_decision(
        rule=rule, result=make_result(rule, EvaluationStatus.PASS),
        account=ACCOUNT, effective_at_utc=EVAL_AT,
    )
    effective = coalesce_decisions([decision], account=ACCOUNT, effective_at_utc=EVAL_AT)
    assert effective.transitions == ()
    assert effective.effective_action is EnforcementAction.ALLOW
    assert not effective.is_blocked
    assert len(effective.all_decisions) == 1


# =============================================================================
# CRITICALITY MODEL ? every rule type classified, none escalated silently
# =============================================================================


def test_every_rule_type_has_an_explicit_criticality():
    from core.risk.prop_rule_enums import RuleType as RT

    assert set(criticality_for.__globals__["RULE_CRITICALITY"]) == set(RT)


def test_unmodelled_rule_type_defaults_to_advisory_not_hard_stop():
    from core.risk.prop_rule_enums import RuleType as RT

    assert criticality_for(RT.UNKNOWN_EXTENSION) is EnforcementCriticality.ADVISORY


def test_daily_loss_and_drawdown_are_hard_stops():
    assert criticality_for(RuleType.DAILY_LOSS_LIMIT) is EnforcementCriticality.HARD_STOP
    for rule_type in (RuleType.MAX_DRAWDOWN, RuleType.STATIC_DRAWDOWN, RuleType.TRAILING_DRAWDOWN):
        assert criticality_for(rule_type) is EnforcementCriticality.HARD_STOP


def test_open_risk_and_position_limits_are_entry_blocks():
    for rule_type in (
        RuleType.MAX_OPEN_RISK, RuleType.MAX_RISK_PER_SYMBOL,
        RuleType.MAX_CORRELATED_RISK, RuleType.MAX_DIRECTIONAL_RISK,
        RuleType.MAX_OPEN_POSITIONS, RuleType.MAX_POSITION_SIZE,
    ):
        assert criticality_for(rule_type) is EnforcementCriticality.ENTRY_BLOCK


def test_profit_target_and_min_days_are_advisory_never_entry_blocks():
    assert criticality_for(RuleType.PROFIT_TARGET) is EnforcementCriticality.ADVISORY
    assert criticality_for(RuleType.MIN_TRADING_DAYS) is EnforcementCriticality.ADVISORY


def test_max_days_and_inactivity_suspend_the_account():
    assert criticality_for(RuleType.MAX_TRADING_DAYS) is EnforcementCriticality.ACCOUNT_SUSPEND
    assert criticality_for(RuleType.INACTIVITY_RULE) is EnforcementCriticality.ACCOUNT_SUSPEND


def test_news_is_an_external_dependency():
    assert criticality_for(RuleType.NEWS_TRADING_RESTRICTION) is EnforcementCriticality.EXTERNAL_DEPENDENCY


def test_advisory_rules_are_not_mandatory_for_safe_operation():
    assert is_mandatory_for_safe_operation(RuleType.PROFIT_TARGET) is False
    assert is_mandatory_for_safe_operation(RuleType.CONSISTENCY_RULE) is False
    assert is_mandatory_for_safe_operation(RuleType.DAILY_LOSS_LIMIT) is True


def test_recovery_class_differs_by_rule_family():
    from core.risk.prop_rule_enforcement import recovery_for

    assert recovery_for(RuleType.DAILY_LOSS_LIMIT, EnforcementCriticality.HARD_STOP) is RecoveryClass.RULE_DAY_RESET
    assert recovery_for(RuleType.STATIC_DRAWDOWN, EnforcementCriticality.HARD_STOP) is RecoveryClass.MANUAL
    assert recovery_for(RuleType.NEWS_TRADING_RESTRICTION, EnforcementCriticality.EXTERNAL_DEPENDENCY) is RecoveryClass.EXTERNAL_WINDOW


# =============================================================================
# EXTERNAL SOURCE FRESHNESS ? stale is not empty
# =============================================================================


def _freshness(kind: SourceFreshness, *, age_seconds: int = 0) -> SourceProvenance:
    return SourceProvenance(
        provider_name="TEST_CALENDAR",
        source_reference="test://calendar",
        retrieved_at_utc=EVAL_AT,
        freshness=kind,
        max_age_seconds=600,
    )


def test_only_fresh_source_is_usable():
    assert SourceFreshness.FRESH.is_usable is True
    for kind in (SourceFreshness.STALE, SourceFreshness.UNAVAILABLE, SourceFreshness.INVALID):
        assert kind.is_usable is False


def test_freshness_is_re_evaluated_at_read_time():
    stale_later = _freshness(SourceFreshness.FRESH)
    later = EVAL_AT.replace(hour=23)
    assert stale_later.evaluate(now_utc=EVAL_AT) is SourceFreshness.FRESH
    assert stale_later.evaluate(now_utc=later) is SourceFreshness.STALE


def test_unavailable_calendar_is_not_an_empty_fresh_window():
    """The mandatory distinction: 'I could not check' is not 'nothing scheduled'."""
    unavailable = unavailable_calendar(
        "TEST_CALENDAR", retrieved_at_utc=EVAL_AT, max_age_seconds=600,
        detail="PROVIDER_DOWN",
    )
    assert unavailable.freshness is SourceFreshness.UNAVAILABLE
    assert unavailable.events == ()
    assert unavailable.is_decidable(now_utc=EVAL_AT) is False

    genuine_empty = EconomicCalendarSnapshot(provenance=_freshness(SourceFreshness.FRESH))
    assert genuine_empty.events == ()
    assert genuine_empty.is_decidable(now_utc=EVAL_AT) is True


def test_stale_calendar_is_not_decidable_even_with_events():
    snapshot = EconomicCalendarSnapshot(
        provenance=_freshness(SourceFreshness.STALE),
        events=(EconomicEvent("e1", EVAL_AT, "HIGH", ("USD",)),),
    )
    assert snapshot.is_decidable(now_utc=EVAL_AT) is False
    assert len(snapshot.events) == 1


def test_economic_event_requires_identity_and_instant():
    with pytest.raises(Exception):
        EconomicEvent("", EVAL_AT, "HIGH", ("USD",))
    with pytest.raises(Exception):
        EconomicEvent("e1", datetime(2026, 3, 10, 15, 0), "HIGH", ("USD",))


def test_economic_event_affects_either_leg_of_a_currency_pair():
    event = EconomicEvent("e1", EVAL_AT, "HIGH", ("USD",))
    assert event.affects_symbol("EURUSD")
    assert not event.affects_symbol("GBPCHF")


def test_market_session_requires_explicit_provenance():
    with pytest.raises(Exception):
        MarketSession("s", "EURUSD", "X", "UTC", None, provenance="")


def test_market_session_is_never_inferred_from_the_symbol_name():
    session = MarketSession(
        "s1", "EURUSD", "LSE", "Europe/London",
        __import__("datetime").time(17, 0), __import__("datetime").time(8, 0),
        provenance="governed://lse",
    )
    assert session.exchange_calendar == "LSE"
    assert session.provenance == "governed://lse"
    # Saturday is CLOSED because the declared weekday set excludes it.
    assert session.state_at(datetime(2026, 3, 7, 12, 0, tzinfo=UTC)) is MarketState.CLOSED
    assert session.state_at(datetime(2026, 3, 6, 12, 0, tzinfo=UTC)) is MarketState.OPEN


def test_unknown_market_state_is_distinct_from_closed():
    answer = MarketSessionAnswer(
        provenance=_freshness(SourceFreshness.UNAVAILABLE), state=MarketState.UNKNOWN
    )
    assert answer.state is MarketState.UNKNOWN
    assert answer.is_decidable(now_utc=EVAL_AT) is False


# =============================================================================
# NO RULE MATH DUPLICATION (requirement 37 / 59)
# =============================================================================


def test_enforcement_module_never_reimplements_block3b_arithmetic():
    from pathlib import Path

    import core.risk.prop_rule_enforcement as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    forbidden_calls = (
        "select_daily_pnl",
        "resolve_limit",
        "current_for_basis",
        "derive_inactivity_state",
        "project_daily_ledger",
        "HighWaterState",
        "derive_evaluation_id",
        "daily_ledger.",
        "high_water.",
    )
    for token in forbidden_calls:
        assert token not in source, f"3C must not call/reimplement 3B arithmetic: {token}"
    # And the declared boundary is present and non-empty.
    assert len(FORBIDDEN_OPERATIONS) >= 6


def test_projection_module_does_not_compare_against_limits():
    from pathlib import Path

    import core.risk.prop_rule_projection as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    # Projection computes exposure only; 3B owns every limit comparison.
    for token in ("LimitBasis.", "PERCENT_", "> rule.limit", "resolve_limit"):
        assert token not in source, f"projection must not compare limits: {token}"


def test_projection_carries_anchors_through_unchanged():
    """A pending order must not move anchors, high-water or the ledger."""
    from dataclasses import replace as dc_replace

    from core.risk.prop_rule_projection import (
        ExposureProjection,
        PlannedOrder,
        build_projected_state,
    )

    order = PlannedOrder(
        canonical_symbol="EURUSD", side="BUY", volume=0.5, entry_price=1.10,
        stop_loss=1.0990, planned_risk_amount=250.0,
    )
    projection = ExposureProjection(
        total_open_risk=750.0, position_count=3, largest_position_risk=300.0,
        largest_lot_size=0.6, symbol_risk=650.0, largest_symbol_risk=650.0,
        cluster_risk=650.0, directional_risk=650.0,
    )
    base = _minimal_state()
    projected = build_projected_state(
        state=base, order=order, projection=projection, observed_at_utc=EVAL_AT
    )
    assert projected.initial_balance == base.initial_balance
    assert projected.daily_anchor is base.daily_anchor
    assert projected.high_water is base.high_water
    assert projected.daily_ledger is base.daily_ledger
    assert projected.trading_day_history is base.trading_day_history
    # Only exposure moved.
    assert projected.total_open_risk == 750.0
    assert projected.position_count == 3
    assert projected.evaluation_id != base.evaluation_id


def test_projection_without_exact_risk_stays_unavailable():
    from core.risk.prop_rule_projection import PlannedOrder, project_exposure

    order = PlannedOrder(
        canonical_symbol="EURUSD", side="BUY", volume=0.5, entry_price=1.10,
        stop_loss=1.0990, planned_risk_amount=None,
    )
    assert order.has_exact_risk is False
    projection = project_exposure(state=_minimal_state(), order=order)
    assert "total_open_risk" in projection.unavailable_fields
    assert projection.total_open_risk is None
    assert projection.is_complete is False


def test_projection_combines_exact_planned_risk_with_current_exposure():
    from core.risk.prop_rule_projection import PlannedOrder, project_exposure

    order = PlannedOrder(
        canonical_symbol="EURUSD", side="BUY", volume=0.5, entry_price=1.10,
        stop_loss=1.0990, planned_risk_amount=2_000.0,
    )
    projection = project_exposure(state=_minimal_state(), order=order)
    assert projection.total_open_risk == 3_000.0     # 1,000 current + 2,000 proposed
    assert projection.position_count == 2           # 1 open + 1 proposed
    assert projection.largest_position_risk == 2_000.0
    assert projection.largest_lot_size == 0.5


def test_planned_order_rejects_impossible_geometry():
    from core.risk.prop_rule_projection import PlannedOrder, ProjectionError

    with pytest.raises(ProjectionError):
        PlannedOrder("EURUSD", "SIDEWAYS", 0.5, 1.10, 1.09)
    with pytest.raises(ProjectionError):
        PlannedOrder("EURUSD", "BUY", 0.0, 1.10, 1.09)
    with pytest.raises(ProjectionError):
        PlannedOrder("", "BUY", 0.5, 1.10, 1.09)
    with pytest.raises(ProjectionError):
        PlannedOrder("EURUSD", "BUY", 0.5, 1.10, 1.09, planned_risk_amount=-1.0)
