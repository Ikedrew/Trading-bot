"""Phase J.2 — V10 Persistence Truth Audit.

Verifies that terminal output, PipelineResult, DecisionContext, and
persisted JSON all agree on every decision field. One truth.
"""

import pytest
from core.v10.pipeline import V10Pipeline
from core.v10.risk_model import AccountContext
from core.v10.broker_context import BrokerContext
from core.v10.decision_report import format_v10_decision
from core.v10.persistence_adapter import build_v10_decision_record
from core.market_understanding.models import (
    MarketUnderstanding, H4Understanding, H1Understanding,
    M15Understanding, M5Understanding,
)
from core.market_understanding.context_models import (
    MarketContextInterpretation, HTFStructureContext, LocationContext, BehaviourContext,
)


def _strong_pipeline():
    """Produces a VALID opportunity with strategy selection."""
    mu = MarketUnderstanding(
        symbol="EURUSD", timestamp_utc=1785400000.0, confidence=0.85,
        h4=H4Understanding(trend="NEUTRAL", trend_strength=0.15),
        h1=H1Understanding(
            bos_confirmed=True, bos_direction="BEARISH",
            dominant_trend="BEARISH", structural_clarity=0.80,
            swing_high=1.0920, swing_low=1.0850,
            active_supply_ob_high=1.0910, active_supply_ob_low=1.0905,
            active_demand_ob_high=1.0860, active_demand_ob_low=1.0855,
            session_high=1.0930, session_low=1.0840,
        ),
        m15=M15Understanding(pullback_active=True, pullback_depth_atr=1.3, range_position=0.75),
        m5=M5Understanding(
            rejection_present=True, rejection_direction="BEARISH",
            rejection_strength_atr=0.9, at_institutional_zone=True,
            zone_type="SUPPLY_OB", atr=0.00055, spread=0.00012,
        ),
    )
    ctx = MarketContextInterpretation(
        symbol="EURUSD", timestamp_utc=1785400000.0,
        htf_structure=HTFStructureContext(macro_bias="NEUTRAL", structure_alignment=0.30, bos_active=True, bos_direction="BEARISH"),
        location=LocationContext(location_type="SUPPLY_OB", inside_institutional_zone=True, premium_discount="PREMIUM", range_position=0.75, zone_quality=0.85, liquidity_below=True),
        behaviour=BehaviourContext(regime="RANGING", volatility_state="NEUTRAL", momentum_direction="NEUTRAL"),
        overall_confidence=0.8,
    )
    account = AccountContext(balance=10000.0, equity=10000.0)
    broker = BrokerContext(connected=True, symbol_available=True, market_open=True, symbol="EURUSD",
                           spread=0.00012, available_margin=5000.0,
                           tick_value=1.0, tick_size=0.00001,
                           volume_min=0.01, volume_max=100.0, volume_step=0.01, point=0.00001)
    return V10Pipeline().process(mu, ctx, account, broker)


def _weak_pipeline():
    """Produces INVALID opportunity (NO_TRADE at opportunity stage)."""
    mu = MarketUnderstanding(symbol="GBPUSD", timestamp_utc=1785400000.0)
    return V10Pipeline().process(mu, None, AccountContext(balance=10000.0), BrokerContext())


class TestFinalActionConsistency:
    """All representations must agree on EXECUTE vs NO_TRADE."""

    def test_strong_all_agree(self):
        result = _strong_pipeline()
        report = format_v10_decision(result)
        record = build_v10_decision_record(result)
        ctx = result.decision_context

        # Determine expected action
        action = "EXECUTE" if result.approved else "NO_TRADE"

        # PipelineResult
        assert result.approved == (action == "EXECUTE")
        # DecisionContext
        assert ctx.final_action == action
        # Persistence record
        assert record["final_action"] == action
        # Terminal report
        assert action in report

    def test_weak_all_agree(self):
        result = _weak_pipeline()
        report = format_v10_decision(result)
        record = build_v10_decision_record(result)
        ctx = result.decision_context

        assert ctx.final_action == "NO_TRADE"
        assert record["final_action"] == "NO_TRADE"
        assert "NO_TRADE" in report


class TestRejectionStageConsistency:
    """All representations must agree on where pipeline stopped."""

    def test_weak_rejection_stage_agrees(self):
        result = _weak_pipeline()
        record = build_v10_decision_record(result)
        ctx = result.decision_context

        stage = result.rejection_stage
        assert stage != ""

        # DecisionContext must agree
        assert ctx.terminal_stage == stage
        # Persistence must agree
        assert record["rejection_stage"] == stage

    def test_strong_no_rejection_or_consistent(self):
        result = _strong_pipeline()
        record = build_v10_decision_record(result)
        ctx = result.decision_context

        if result.approved:
            assert result.rejection_stage == ""
            assert ctx.terminal_stage == ""
            assert record["rejection_stage"] is None or record["rejection_stage"] == ""
        else:
            stage = result.rejection_stage
            assert ctx.terminal_stage == stage
            assert record["rejection_stage"] == stage


class TestStrategyConsistency:
    """Strategy family must be identical everywhere."""

    def test_strategy_family_agrees(self):
        result = _strong_pipeline()
        record = build_v10_decision_record(result)
        ctx = result.decision_context

        pipeline_strat = result.strategy.strategy_family
        ctx_strat = ctx.strategy_family
        record_strat = record["strategy_family"]

        # All must match (or all None/NONE for rejected)
        if pipeline_strat != "NONE":
            assert ctx_strat == pipeline_strat
            assert record_strat == pipeline_strat
        else:
            assert ctx_strat == "NONE"
            assert record_strat is None or record_strat == "NONE"


class TestHorizonConsistency:
    """Horizon type must be identical everywhere."""

    def test_horizon_agrees(self):
        result = _strong_pipeline()
        record = build_v10_decision_record(result)
        ctx = result.decision_context

        pipeline_hz = result.horizon.horizon_type
        ctx_hz = ctx.horizon_type
        record_hz = record["horizon"]

        if result.strategy.strategy_family != "NONE":
            assert ctx_hz == pipeline_hz
            assert record_hz == pipeline_hz


class TestObservationIdConsistency:
    """observation_id must be identical everywhere."""

    def test_observation_id_agrees(self):
        result = _strong_pipeline()
        record = build_v10_decision_record(result)
        ctx = result.decision_context

        obs_id = result.opportunity.observation_id
        assert obs_id != ""

        # Record
        assert record["observation_id"] == obs_id
        assert record["decision_id"] == obs_id
        # Context events
        if result.events:
            assert result.events.observation_id == obs_id


class TestExecutionStatusConsistency:
    """Execution approval must match across all representations."""

    def test_execution_status_agrees(self):
        result = _strong_pipeline()
        record = build_v10_decision_record(result)

        pipeline_exec = result.execution.approved
        record_exec = record["execution_approved"]

        assert pipeline_exec == record_exec


class TestNoContradictions:
    """Specifically test for the contradictions mentioned in the spec."""

    def test_no_opportunity_invalid_but_execution_rejected(self):
        """If opportunity is INVALID, rejection_stage must be 'opportunity', not 'execution'."""
        result = _weak_pipeline()
        if result.opportunity.opportunity_state == "INVALID":
            assert result.rejection_stage == "opportunity"
            assert result.rejection_stage != "execution"
            assert result.rejection_stage != "risk"

    def test_no_strategy_none_but_record_shows_family(self):
        """If strategy is NONE, persistence must also show None/NONE."""
        result = _weak_pipeline()
        record = build_v10_decision_record(result)
        if result.strategy.strategy_family == "NONE":
            assert record["strategy_family"] is None or record["strategy_family"] == "NONE"

    def test_no_horizon_mismatch(self):
        """Horizon in persistence must match pipeline horizon exactly."""
        result = _strong_pipeline()
        record = build_v10_decision_record(result)
        if result.strategy.strategy_family != "NONE":
            assert record["horizon"] == result.horizon.horizon_type

    def test_report_stopped_at_matches_context(self):
        """Terminal 'Stopped at:' must match DecisionContext.terminal_stage."""
        result = _weak_pipeline()
        report = format_v10_decision(result)
        ctx = result.decision_context
        if ctx.terminal_stage:
            assert f"Stopped at: {ctx.terminal_stage}" in report
