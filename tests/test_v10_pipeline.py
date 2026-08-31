"""Tests for V10 Pipeline Orchestrator."""

import pytest
from core.v3_shadow.models import (
    MarketUnderstanding, H4Understanding, H1Understanding,
    M15Understanding, M5Understanding,
)
from core.v3_shadow.context_models import (
    V3MarketContext, HTFStructureContext, LocationContext, BehaviourContext,
)
from core.v10.risk_model import AccountContext
from core.v10.broker_context import BrokerContext
from core.v10.pipeline import V10Pipeline, PipelineResult
from core.v10.strategy_family import StrategyFamily
from core.v10.entry_model import EntryStatus
from core.identity.canonical import make_canonical_opportunity_id, mint_observation_id


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

def _strong_understanding():
    """Market state that should produce a VALID opportunity."""
    return MarketUnderstanding(
        symbol="EURUSD",
        timestamp_utc=1785400000.0,
        confidence=0.85,
        h4=H4Understanding(
            trend="NEUTRAL", trend_strength=0.15,
            market_phase="CONSOLIDATION",
        ),
        h1=H1Understanding(
            bos_confirmed=True, bos_direction="BEARISH",
            dominant_trend="BEARISH", structure_type="LH_LL",
            structural_clarity=0.80,
            swing_high=1.0920, swing_low=1.0850,
            active_supply_ob_high=1.0910, active_supply_ob_low=1.0905,
            active_demand_ob_high=1.0860, active_demand_ob_low=1.0855,
            session_high=1.0930, session_low=1.0840,
            equal_lows_level=1.0845,
        ),
        m15=M15Understanding(
            pullback_active=True, pullback_depth_atr=1.3,
            retracement_pct=0.55, range_position=0.75,
            swing_high=1.0905, swing_low=1.0870,
            refined_supply_ob_high=1.0903, refined_supply_ob_low=1.0900,
        ),
        m5=M5Understanding(
            momentum_direction="NEUTRAL", momentum_strength=0.2,
            rejection_present=True, rejection_direction="BEARISH",
            rejection_strength_atr=0.9,
            at_institutional_zone=True, zone_type="SUPPLY_OB",
            atr=0.00055, spread=0.00012, spread_atr_ratio=0.22,
        ),
    )


def _strong_context():
    """V3MarketContext supporting the opportunity."""
    return V3MarketContext(
        symbol="EURUSD",
        timestamp_utc=1785400000.0,
        htf_structure=HTFStructureContext(
            macro_bias="NEUTRAL", macro_bias_strength=0.15,
            structure_alignment=0.30,
            bos_active=True, bos_direction="BEARISH",
        ),
        location=LocationContext(
            location_type="SUPPLY_OB",
            inside_institutional_zone=True,
            premium_discount="PREMIUM", range_position=0.75,
            zone_quality=0.85,
            liquidity_below=True,
            nearest_liquidity_direction="BELOW",
            nearest_liquidity_distance_pips=12.0,
            supply_zones_nearby=1, demand_zones_nearby=1,
        ),
        behaviour=BehaviourContext(
            regime="RANGING", regime_confidence=0.6,
            volatility_state="NEUTRAL", volatility_level=0.5,
            momentum_direction="NEUTRAL", momentum_strength=0.2,
        ),
        overall_confidence=0.8,
    )


def _weak_understanding():
    """Market state that should produce INVALID opportunity."""
    return MarketUnderstanding(
        symbol="EURUSD",
        timestamp_utc=1785400000.0,
        confidence=0.3,
        h4=H4Understanding(trend="NEUTRAL", trend_strength=0.05),
        h1=H1Understanding(
            dominant_trend="NEUTRAL", structural_clarity=0.2,
            bos_confirmed=False,
        ),
        m15=M15Understanding(),
        m5=M5Understanding(
            atr=0.0005, spread=0.00015, spread_atr_ratio=0.3,
        ),
    )


def _good_account():
    return AccountContext(
        balance=10000.0, equity=10000.0,
        current_open_risk_pct=0.0, open_positions=0,
        daily_loss_pct=0.0, symbols_with_positions=[],
    )


def _good_broker():
    return BrokerContext(
        connected=True, symbol_available=True, market_open=True,
        spread=0.00012, available_margin=5000.0,
    )


def _bad_broker():
    return BrokerContext(
        connected=False, symbol_available=False, market_open=False,
        spread=0.001, available_margin=0.0,
    )


# ═══════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════

class TestFullPipelineApproved:
    def test_strong_context_flows_through_all_layers(self):
        pipeline = V10Pipeline()
        result = pipeline.process(
            _strong_understanding(), _strong_context(), _good_account(), _good_broker()
        )
        # Should reach execution (may be approved or waiting depending on entry)
        assert isinstance(result, PipelineResult)
        assert result.market_state.symbol == "EURUSD"
        assert result.opportunity.opportunity_state in ("VALID", "WATCHING")
        assert result.strategy.strategy_family != ""

    def test_approved_result_has_execution(self):
        pipeline = V10Pipeline()
        result = pipeline.process(
            _strong_understanding(), _strong_context(), _good_account(), _good_broker()
        )
        # If full pipeline approves, execution should have order details
        if result.approved:
            assert result.execution.order_details.symbol == "EURUSD"
            assert result.execution.order_details.volume > 0

    def test_strategy_lineage_uses_parent_opportunity_canonical_id(
        self, monkeypatch, tmp_path
    ):
        import core.persistence.opportunity_writer as opportunity_writer
        import core.persistence.strategy_candidates_writer as candidates_writer
        import core.v10.pipeline as pipeline_module

        monkeypatch.setattr(candidates_writer, "_LOCAL_DIR", str(tmp_path))
        monkeypatch.setattr(
            opportunity_writer,
            "persist_opportunity_from_v10",
            lambda **kwargs: True,
        )

        captured = {}
        original_select_strategy = pipeline_module.select_strategy

        def _capture_lineage(market_state, opportunity, lineage=None):
            captured["market_state"] = market_state
            captured["opportunity"] = opportunity
            captured["lineage"] = lineage
            return original_select_strategy(
                market_state, opportunity, lineage=lineage
            )

        monkeypatch.setattr(pipeline_module, "select_strategy", _capture_lineage)

        result = V10Pipeline().process(
            _strong_understanding(), _strong_context(), _good_account(), _good_broker()
        )

        expected_opportunity_id = make_canonical_opportunity_id(
            symbol=result.market_state.symbol,
            bar_time=result.market_state.timestamp_utc,
            pattern=result.opportunity.opportunity_type or "NONE",
        )
        expected_observation_id = mint_observation_id(
            symbol=result.market_state.symbol,
            bar_time=result.market_state.timestamp_utc,
            timeframe="M5",
        )

        assert captured["lineage"]["canonical_opportunity_id"] == expected_opportunity_id
        assert captured["lineage"]["observation_id"] == expected_observation_id
        assert captured["lineage"]["canonical_opportunity_id"] != expected_observation_id


class TestPipelineStopsAtInvalidOpportunity:
    def test_weak_market_stops_early(self):
        pipeline = V10Pipeline()
        result = pipeline.process(
            _weak_understanding(), None, _good_account(), _good_broker()
        )
        assert result.opportunity.opportunity_state == "INVALID"
        assert result.strategy.strategy_family == StrategyFamily.NONE.value
        assert not result.approved
        assert result.rejection_stage == "opportunity"


class TestPipelineStopsAtRisk:
    def test_exceeded_daily_loss_blocks(self):
        pipeline = V10Pipeline()
        bad_account = AccountContext(
            balance=10000.0, equity=9500.0,
            daily_loss_pct=0.05,  # 5% > 4% limit
            open_positions=0,
        )
        result = pipeline.process(
            _strong_understanding(), _strong_context(), bad_account, _good_broker()
        )
        # If opportunity is valid and entry constructed, risk should block
        if result.entry.entry_status != EntryStatus.INVALID.value:
            assert not result.risk.approved
            assert result.rejection_stage == "risk"

    def test_max_positions_blocks(self):
        pipeline = V10Pipeline()
        full_account = AccountContext(
            balance=10000.0, equity=10000.0,
            open_positions=3,  # At limit
            daily_loss_pct=0.0,
        )
        result = pipeline.process(
            _strong_understanding(), _strong_context(), full_account, _good_broker()
        )
        if result.entry.entry_status != EntryStatus.INVALID.value:
            assert not result.risk.approved


class TestPipelineStopsAtExecution:
    def test_bad_broker_blocks_execution(self):
        pipeline = V10Pipeline()
        result = pipeline.process(
            _strong_understanding(), _strong_context(), _good_account(), _bad_broker()
        )
        assert not result.execution.approved
        if result.entry.entry_status != EntryStatus.INVALID.value and result.risk.approved:
            assert result.rejection_stage == "execution"


class TestPipelineReturnsCorrectly:
    def test_returns_pipeline_result(self):
        pipeline = V10Pipeline()
        result = pipeline.process(_strong_understanding(), _strong_context(), _good_account(), _good_broker())
        assert isinstance(result, PipelineResult)

    def test_all_layers_populated(self):
        pipeline = V10Pipeline()
        result = pipeline.process(_strong_understanding(), _strong_context(), _good_account(), _good_broker())
        assert result.market_state is not None
        assert result.opportunity is not None
        assert result.strategy is not None
        assert result.horizon is not None
        assert result.entry is not None
        assert result.risk is not None
        assert result.execution is not None

    def test_rejection_stage_empty_when_approved(self):
        pipeline = V10Pipeline()
        result = pipeline.process(_strong_understanding(), _strong_context(), _good_account(), _good_broker())
        if result.approved:
            assert result.rejection_stage == ""

    def test_works_without_context(self):
        """Pipeline works with just MarketUnderstanding (no V3MarketContext)."""
        pipeline = V10Pipeline()
        result = pipeline.process(_strong_understanding(), None, _good_account(), _good_broker())
        assert isinstance(result, PipelineResult)

    def test_works_with_defaults(self):
        """Pipeline works with minimal arguments."""
        pipeline = V10Pipeline()
        result = pipeline.process(_strong_understanding())
        assert isinstance(result, PipelineResult)
        # Without broker context, execution should reject (not connected)
        assert not result.execution.approved
