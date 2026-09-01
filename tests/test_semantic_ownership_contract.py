"""Architecture tests for additive semantic-ownership persistence fields."""

from __future__ import annotations

from types import SimpleNamespace as NS

from core.decision_ledger import DecisionOutcome, build_ledger_entry
from core.decision_trace import build_decision_trace
from core.trade_truth import build_trade_truth
from core.v10.persistence_adapter import build_v10_ledger_entry


def _v10_rejected_at_risk():
    quality = NS(
        overall_quality=0.81, location_score=0.1, structure_score=0.2,
        behaviour_score=0.3, formation_score=0.4,
    )
    opportunity = NS(
        observation_id="v10-observation", opportunity_state="VALID",
        directional_bias="BUY", opportunity_type="PULLBACK",
        quality=quality, reasoning=[],
    )
    risk_profile = NS(
        risk_percentage=0.5, position_size=0.1, max_loss_amount=50.0,
    )
    return NS(
        approved=False, rejection_stage="risk", opportunity=opportunity,
        risk=NS(approved=False, rejection_reason="structured risk rejection",
                risk_profile=risk_profile),
        strategy=NS(strategy_family="CONTINUATION", strategy_confidence=0.7,
                    directional_context="BUY", reasoning=[]),
        horizon=NS(horizon_type="INTRADAY", movement_expectation=NS(
            minimum_expected_move=1.0, maximum_expected_move=2.0,
            measurement_unit="R"), trade_lifecycle=NS(expected_duration_minutes=60)),
        entry=NS(trade_direction="BUY", entry_method="MARKET", entry_status="VALID",
                 entry_price=1.1, stop_reference=NS(price=1.0),
                 target_reference=NS(price=1.3), risk_distance=0.1,
                 reward_distance=0.2, expected_rr=2.0),
        execution=NS(approved=False, rejection_reason="not reached",
                     order_details=NS(order_type="", volume=0.0)),
        market_state=NS(symbol="EURUSD", timestamp_utc=1.0,
                        regime=NS(regime="TRENDING")),
        account_snapshot=None, broker_snapshot=None,
    )


def test_structural_pipeline_stage_overrides_reason_text_in_trace():
    pipeline = _v10_rejected_at_risk()
    trace = build_decision_trace(
        engine_result={
            "symbol": "EURUSD", "action": "EXECUTE",
            "reason": "no_viable_pattern", "v10_pipeline_result": pipeline,
        },
        v10_pipeline_result=pipeline,
    )
    assert trace.action == "NO_TRADE"
    assert trace.terminal_stage == "risk"
    assert trace.terminal_reason == "structured risk rejection"


def test_score_families_are_qualified_and_legacy_score_declares_semantics():
    assessment = build_ledger_entry(
        symbol="EURUSD", cycle_id=1, decision=DecisionOutcome.NO_TRADE,
        signal_score=0.42,
        signal_score_semantic="assessment_strategy_weighted_score",
        assessment_strategy_weighted_score=0.42,
    )
    opportunity = build_v10_ledger_entry(_v10_rejected_at_risk())
    assert assessment["assessment_strategy_weighted_score"] == 0.42
    assert assessment["opportunity_overall_quality_score"] is None
    assert opportunity["opportunity_overall_quality_score"] == 0.81
    assert opportunity["assessment_strategy_weighted_score"] is None
    assert opportunity["signal_score_semantic"] == "opportunity_overall_quality_score"


def test_trade_truth_unknown_execution_metrics_are_null_with_provenance():
    truth = build_trade_truth(
        trade_id="t1", correlation_id="c1", symbol="EURUSD",
        entry_fill_price=1.1, exit_fill_price=1.2, volume_executed=0.1,
        entry_timestamp_broker=10.0, exit_timestamp_broker=20.0,
        pnl_realised=10.0, r_multiple_realised=1.0,
        field_provenance={"entry_fill_price": "broker"},
    )
    assert truth["execution"]["slippage_entry"] is None
    assert truth["execution"]["spread_at_entry"] is None
    assert truth["outcome"]["commission"] is None
    assert truth["provenance"]["fields"]["entry_fill_price"] == "broker"
    assert "slippage_entry" not in truth["provenance"]["fields"]


def test_measured_zero_is_not_converted_to_unknown():
    truth = build_trade_truth(
        trade_id="t1", correlation_id="c1", symbol="EURUSD",
        entry_fill_price=1.1, exit_fill_price=1.2, volume_executed=0.1,
        entry_timestamp_broker=10.0, exit_timestamp_broker=20.0,
        pnl_realised=0.0, r_multiple_realised=0.0,
        commission=0.0, slippage_entry=0.0,
        field_provenance={"commission": "broker", "slippage_entry": "broker"},
    )
    assert truth["execution"]["slippage_entry"] == 0.0
    assert truth["outcome"]["commission"] == 0.0
