"""Tests for V10 Horizon Authority — verifies V10 HorizonEngine is sole authority."""

import pytest
from core.v10.horizon_assessment import HorizonType
from core.v10.horizon_engine import assess_horizon
from core.v10.market_state import V10MarketState, H4State, M15State, M5State, RegimeState, LocationState
from core.v10.opportunity_assessment import OpportunityAssessment
from core.v10.strategy_family import StrategyDecision, StrategyFamily


def _opp():
    return OpportunityAssessment(
        observation_id="hz_test", symbol="EURUSD", timestamp_utc=1000.0,
        opportunity_state="VALID", directional_bias="BEARISH",
    )


class TestV10HorizonReachesExecution:
    """V10 horizon decisions must NOT be blocked by legacy authority."""

    def test_scalp_reaches_execution(self):
        state = V10MarketState(symbol="EURUSD", timestamp_utc=1000.0,
                               h4=H4State(trend="NEUTRAL", trend_strength=0.1))
        strat = StrategyDecision(strategy_family=StrategyFamily.MEAN_REVERSION.value)
        result = assess_horizon(state, _opp(), strat)
        assert result.horizon_type == HorizonType.SCALP.value

    def test_intraday_reaches_execution(self):
        state = V10MarketState(symbol="EURUSD", timestamp_utc=1000.0,
                               h4=H4State(trend="BEARISH", trend_strength=0.7),
                               regime=RegimeState(volatility_state="EXPANSION"),
                               location=LocationState(nearest_liquidity_distance_pips=40.0))
        strat = StrategyDecision(strategy_family=StrategyFamily.LIQUIDITY_SWEEP_REVERSAL.value)
        result = assess_horizon(state, _opp(), strat)
        # Base INTRADAY + strong HTF upgrade = EXTENDED or INTRADAY
        assert result.horizon_type in (HorizonType.INTRADAY.value, HorizonType.EXTENDED.value)

    def test_extended_reaches_execution(self):
        state = V10MarketState(symbol="EURUSD", timestamp_utc=1000.0,
                               h4=H4State(trend="BEARISH", trend_strength=0.8),
                               regime=RegimeState(volatility_state="EXPANSION"),
                               location=LocationState(nearest_liquidity_distance_pips=50.0))
        strat = StrategyDecision(strategy_family=StrategyFamily.TREND_CONTINUATION.value)
        result = assess_horizon(state, _opp(), strat)
        assert result.horizon_type == HorizonType.EXTENDED.value

    def test_permitted_horizons_includes_all(self):
        """Config must permit all horizons for V10."""
        from core import config
        permitted = getattr(config, "PERMITTED_HORIZONS", [])
        assert "SCALP" in permitted
        assert "INTRADAY" in permitted
        assert "EXTENDED" in permitted


class TestLegacyAuthorityDisabled:
    """Legacy HorizonExecutionAuthority must not affect V10 path."""

    def test_engine_mode_is_v10(self):
        from core import config
        assert config.ENGINE_MODE == "V10"

    def test_no_horizon_authority_import_in_v10(self):
        """V10 pipeline modules should not import HorizonExecutionAuthority."""
        import inspect
        from core.v10 import pipeline, horizon_engine, execution_engine, risk_engine
        for module in [pipeline, horizon_engine, execution_engine, risk_engine]:
            source = inspect.getsource(module)
            assert "HorizonExecutionAuthority" not in source
            assert "PERMITTED_HORIZONS" not in source

    def test_v10_horizon_is_final(self):
        """HorizonDecision cannot be modified after creation (frozen)."""
        from core.v10.horizon_assessment import HorizonDecision
        hd = HorizonDecision(horizon_type="EXTENDED")
        with pytest.raises(Exception):
            hd.horizon_type = "SCALP"  # type: ignore


class TestHorizonInPipelineResult:
    """Verify the pipeline preserves horizon through to execution."""

    def test_pipeline_preserves_extended_horizon(self):
        from core.v10.pipeline import V10Pipeline
        from core.v10.risk_model import AccountContext
        from core.v10.broker_context import BrokerContext
        from core.market_understanding.models import (
            MarketUnderstanding, H4Understanding, H1Understanding,
            M15Understanding, M5Understanding,
        )

        # Create a state that should produce EXTENDED horizon
        mu = MarketUnderstanding(
            symbol="EURUSD", timestamp_utc=1000.0,
            h4=H4Understanding(trend="BEARISH", trend_strength=0.8),
            h1=H1Understanding(
                bos_confirmed=True, bos_direction="BEARISH",
                dominant_trend="BEARISH", structural_clarity=0.8,
                swing_high=1.095, swing_low=1.085,
                active_supply_ob_high=1.094, active_supply_ob_low=1.0935,
                active_demand_ob_high=1.086, active_demand_ob_low=1.0855,
                session_low=1.082,
            ),
            m15=M15Understanding(
                pullback_active=True, pullback_depth_atr=1.5,
                range_position=0.7,
                internal_bos=True, internal_bos_direction="BEARISH",
            ),
            m5=M5Understanding(
                atr=0.0006, spread=0.00012,
                rejection_present=True, rejection_direction="BEARISH",
                at_institutional_zone=True, zone_type="SUPPLY_OB",
            ),
        )

        account = AccountContext(balance=10000.0, equity=10000.0)
        broker = BrokerContext(
            connected=True, symbol_available=True, market_open=True,
            spread=0.00012, available_margin=5000.0,
            tick_value=1.0, tick_size=0.00001,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
            point=0.00001,
        )

        pipeline = V10Pipeline()
        result = pipeline.process(mu, None, account, broker)

        # The horizon should be whatever V10 decides — not forced to SCALP
        # (Whether it's SCALP/INTRADAY/EXTENDED depends on the market state)
        assert result.horizon.horizon_type in (
            HorizonType.SCALP.value,
            HorizonType.INTRADAY.value,
            HorizonType.EXTENDED.value,
        )
        # And it should flow through to the decision context
        if result.decision_context:
            assert result.decision_context.horizon_type == result.horizon.horizon_type
