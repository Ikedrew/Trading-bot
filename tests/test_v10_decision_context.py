"""Tests for V10 Decision Context."""

import pytest
from core.v10.decision_context import V10DecisionContext
from core.v10.market_state import V10MarketState, H4State, H1State, M15State, M5State, RegimeState, LocationState
from core.v10.opportunity_assessment import OpportunityAssessment, OpportunityQuality
from core.v10.strategy_family import StrategyDecision, StrategyFamily
from core.v10.horizon_assessment import HorizonDecision, HorizonType, MovementExpectation, TradeLifecycle
from core.v10.entry_model import EntryDecision, EntryStatus, TradeDirection, EntryMethod, StopReference, TargetReference
from core.v10.risk_model import RiskDecision, RiskProfile, TradeGeometry
from core.v10.execution_model import ExecutionDecision, OrderDetails


class TestImmutability:
    def test_context_is_frozen(self):
        ctx = V10DecisionContext.empty("EURUSD", 1000.0)
        with pytest.raises(Exception):
            ctx.symbol = "GBPUSD"  # type: ignore

    def test_with_methods_return_new_instance(self):
        ctx1 = V10DecisionContext.empty("EURUSD", 1000.0)
        state = V10MarketState(symbol="EURUSD", timestamp_utc=1000.0)
        ctx2 = ctx1.with_market_state(state)
        # Original unchanged
        assert ctx1.market_state is None
        # New has the state
        assert ctx2.market_state is not None

    def test_stages_accumulate(self):
        ctx = V10DecisionContext.empty("EURUSD", 1000.0)
        ctx = ctx.with_market_state(V10MarketState(symbol="EURUSD"))
        assert "market_state" in ctx.completed_stages
        ctx = ctx.with_opportunity(OpportunityAssessment(opportunity_state="VALID", directional_bias="BEARISH"))
        assert "opportunity" in ctx.completed_stages
        assert "market_state" in ctx.completed_stages  # Still there


class TestProgressiveBuilding:
    def test_empty_has_no_stages(self):
        ctx = V10DecisionContext.empty("EURUSD", 1000.0)
        assert ctx.completed_stages == ()
        assert ctx.market_state is None

    def test_full_chain_preserves_all(self):
        ctx = V10DecisionContext.empty("EURUSD", 1000.0)
        ctx = ctx.with_market_state(V10MarketState(symbol="EURUSD", h4=H4State(trend="BEARISH")))
        ctx = ctx.with_opportunity(OpportunityAssessment(opportunity_state="VALID", directional_bias="BEARISH"))
        ctx = ctx.with_strategy(StrategyDecision(strategy_family="MEAN_REVERSION", strategy_confidence=0.8))
        ctx = ctx.with_horizon(HorizonDecision(horizon_type="SCALP"))
        ctx = ctx.with_entry(EntryDecision(entry_status="READY", trade_direction="SELL", entry_price=1.09, risk_distance=0.001, reward_distance=0.002, expected_rr=2.0, stop_reference=StopReference(price=1.091), target_reference=TargetReference(price=1.088)))
        ctx = ctx.with_risk(RiskDecision(approved=True, risk_profile=RiskProfile(position_size=0.25)))
        ctx = ctx.with_execution(ExecutionDecision(approved=True, order_details=OrderDetails(direction="SELL", volume=0.25)))

        assert len(ctx.completed_stages) == 7
        assert ctx.market_state.h4.trend == "BEARISH"
        assert ctx.opportunity.directional_bias == "BEARISH"
        assert ctx.strategy.strategy_family == "MEAN_REVERSION"
        assert ctx.approved is True


class TestTerminalStage:
    def test_invalid_opportunity_sets_terminal(self):
        ctx = V10DecisionContext.empty("EURUSD", 1000.0)
        ctx = ctx.with_market_state(V10MarketState())
        ctx = ctx.with_opportunity(OpportunityAssessment(opportunity_state="INVALID"))
        assert ctx.terminal_stage == "opportunity"
        assert ctx.approved is False

    def test_no_strategy_sets_terminal(self):
        ctx = V10DecisionContext.empty("EURUSD", 1000.0)
        ctx = ctx.with_market_state(V10MarketState())
        ctx = ctx.with_opportunity(OpportunityAssessment(opportunity_state="VALID"))
        ctx = ctx.with_strategy(StrategyDecision(strategy_family=StrategyFamily.NONE.value))
        assert ctx.terminal_stage == "strategy"

    def test_risk_rejected_sets_terminal(self):
        ctx = V10DecisionContext.empty("EURUSD", 1000.0)
        ctx = ctx.with_risk(RiskDecision(approved=False, rejection_reason="Daily loss"))
        assert ctx.terminal_stage == "risk"

    def test_execution_rejected_sets_terminal(self):
        ctx = V10DecisionContext.empty("EURUSD", 1000.0)
        ctx = ctx.with_execution(ExecutionDecision(approved=False, rejection_reason="Spread"))
        assert ctx.terminal_stage == "execution"


class TestProperties:
    def test_direction_from_entry(self):
        ctx = V10DecisionContext.empty("EURUSD", 1000.0)
        ctx = ctx.with_entry(EntryDecision(trade_direction="BUY"))
        assert ctx.direction == "BUY"

    def test_direction_from_opportunity_fallback(self):
        ctx = V10DecisionContext.empty("EURUSD", 1000.0)
        ctx = ctx.with_opportunity(OpportunityAssessment(directional_bias="BEARISH"))
        assert ctx.direction == "BEARISH"

    def test_strategy_family_property(self):
        ctx = V10DecisionContext.empty("EURUSD", 1000.0)
        ctx = ctx.with_strategy(StrategyDecision(strategy_family="TREND_CONTINUATION"))
        assert ctx.strategy_family == "TREND_CONTINUATION"

    def test_horizon_type_property(self):
        ctx = V10DecisionContext.empty("EURUSD", 1000.0)
        ctx = ctx.with_horizon(HorizonDecision(horizon_type="INTRADAY"))
        assert ctx.horizon_type == "INTRADAY"


class TestSerialisation:
    def test_to_dict_contains_all_stages(self):
        ctx = V10DecisionContext.empty("EURUSD", 1000.0)
        ctx = ctx.with_market_state(V10MarketState(symbol="EURUSD"))
        ctx = ctx.with_opportunity(OpportunityAssessment(opportunity_state="VALID"))
        d = ctx.to_dict()
        assert d["symbol"] == "EURUSD"
        assert d["market_state"] is not None
        assert d["opportunity"] is not None
        assert d["strategy"] is None  # Not yet added
        assert "completed_stages" in d

    def test_to_dict_no_legacy_fields(self):
        ctx = V10DecisionContext.empty("EURUSD", 1000.0)
        d = ctx.to_dict()
        import json
        s = json.dumps(d, default=str)
        assert "composite_score" not in s
        assert "grade" not in s
        assert "pattern_gate" not in s


class TestPipelineIntegration:
    def test_pipeline_produces_decision_context(self):
        from core.market_understanding.models import MarketUnderstanding, H1Understanding, M5Understanding
        from core.v10.pipeline import V10Pipeline
        from core.v10.broker_context import BrokerContext
        from core.v10.risk_model import AccountContext

        mu = MarketUnderstanding(symbol="EURUSD", timestamp_utc=1000.0, confidence=0.5)
        pipeline = V10Pipeline()
        result = pipeline.process(mu, None, AccountContext(), BrokerContext())

        assert result.decision_context is not None
        assert result.decision_context.symbol == "EURUSD"
        assert "market_state" in result.decision_context.completed_stages
        assert "opportunity" in result.decision_context.completed_stages
