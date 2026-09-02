"""Phase G — V10 Research Dataset Completeness Tests.

Verifies that stored decision records contain all fields needed
for a researcher to answer: "Why did this trade happen? Was the reasoning correct?"
"""

import pytest
import json
from core.v10.pipeline import V10Pipeline
from core.v10.risk_model import AccountContext
from core.v10.broker_context import BrokerContext
from core.v10.persistence_adapter import build_v10_decision_record
from core.market_understanding.models import (
    MarketUnderstanding, H4Understanding, H1Understanding,
    M15Understanding, M5Understanding,
)
from core.market_understanding.context_models import (
    MarketContextInterpretation, HTFStructureContext, LocationContext, BehaviourContext,
)


def _execute_pipeline():
    """Run pipeline with strong context that should produce a complete record."""
    mu = MarketUnderstanding(
        symbol="EURUSD", timestamp_utc=1785400000.0, confidence=0.85,
        h4=H4Understanding(trend="NEUTRAL", trend_strength=0.15, market_phase="CONSOLIDATION",
                           atr=0.004, volatility_state="NEUTRAL"),
        h1=H1Understanding(
            bos_confirmed=True, bos_direction="BEARISH",
            dominant_trend="BEARISH", structure_type="LH_LL", structural_clarity=0.80,
            swing_high=1.0920, swing_low=1.0850,
            active_supply_ob_high=1.0910, active_supply_ob_low=1.0905,
            active_demand_ob_high=1.0860, active_demand_ob_low=1.0855,
            session_high=1.0930, session_low=1.0840,
            equal_lows_level=1.0845,
        ),
        m15=M15Understanding(pullback_active=True, pullback_depth_atr=1.3,
                             retracement_pct=0.55, range_position=0.75,
                             swing_high=1.0905, swing_low=1.0870),
        m5=M5Understanding(
            momentum_direction="NEUTRAL", momentum_strength=0.2,
            rejection_present=True, rejection_direction="BEARISH",
            rejection_strength_atr=0.9,
            at_institutional_zone=True, zone_type="SUPPLY_OB",
            atr=0.00055, spread=0.00012, spread_atr_ratio=0.22,
        ),
    )
    ctx = MarketContextInterpretation(
        symbol="EURUSD", timestamp_utc=1785400000.0,
        htf_structure=HTFStructureContext(macro_bias="NEUTRAL", structure_alignment=0.30,
                                         bos_active=True, bos_direction="BEARISH"),
        location=LocationContext(
            location_type="SUPPLY_OB", inside_institutional_zone=True,
            premium_discount="PREMIUM", range_position=0.75, zone_quality=0.85,
            liquidity_below=True, nearest_liquidity_distance_pips=12.0,
        ),
        behaviour=BehaviourContext(regime="RANGING", volatility_state="NEUTRAL",
                                   momentum_direction="NEUTRAL", momentum_strength=0.2),
        overall_confidence=0.8,
    )
    account = AccountContext(balance=10000.0, equity=10000.0, leverage=100, currency="USD")
    broker = BrokerContext(
        connected=True, symbol_available=True, market_open=True, symbol="EURUSD",
        spread=0.00012, available_margin=5000.0, account_balance=10000.0,
        tick_value=1.0, tick_size=0.00001, contract_size=100000.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01,
        point=0.00001, digits=5, stops_level=10,
    )
    pipeline = V10Pipeline()
    return pipeline.process(mu, ctx, account, broker)


def _reject_pipeline():
    """Run pipeline with weak context that produces NO_TRADE."""
    mu = MarketUnderstanding(symbol="GBPUSD", timestamp_utc=1785500000.0, confidence=0.3)
    account = AccountContext(balance=10000.0, equity=10000.0)
    broker = BrokerContext(connected=True, symbol_available=True, market_open=True,
                           spread=0.0002, available_margin=5000.0)
    return V10Pipeline().process(mu, None, account, broker)


# ═══════════════════════════════════════════════════════════════
# DECISION RECORD COMPLETENESS
# ═══════════════════════════════════════════════════════════════

class TestDecisionRecordMarketFields:
    """Market state must be fully captured."""

    def test_has_h4_context(self):
        record = build_v10_decision_record(_execute_pipeline())
        ms = record["market_state"]
        assert ms["h4_trend"] is not None
        assert ms["h4_phase"] is not None

    def test_has_h1_structure(self):
        record = build_v10_decision_record(_execute_pipeline())
        ms = record["market_state"]
        assert "h1_bos_direction" in ms
        assert ms["h1_structural_clarity"] is not None

    def test_has_regime(self):
        record = build_v10_decision_record(_execute_pipeline())
        ms = record["market_state"]
        assert ms["regime"] is not None

    def test_has_volatility(self):
        record = build_v10_decision_record(_execute_pipeline())
        ms = record["market_state"]
        assert ms["volatility_state"] is not None

    def test_has_location(self):
        record = build_v10_decision_record(_execute_pipeline())
        ms = record["market_state"]
        assert ms["location_type"] is not None
        assert "inside_zone" in ms
        assert "range_position" in ms


class TestDecisionRecordOpportunity:
    def test_has_state(self):
        record = build_v10_decision_record(_execute_pipeline())
        assert record["opportunity"]["state"] in ("VALID", "WATCHING", "INVALID")

    def test_has_quality_components(self):
        record = build_v10_decision_record(_execute_pipeline())
        opp = record["opportunity"]
        assert "overall_quality" in opp
        assert "location_score" in opp
        assert "structure_score" in opp
        assert "behaviour_score" in opp
        assert "formation_score" in opp

    def test_has_reasoning(self):
        record = build_v10_decision_record(_execute_pipeline())
        assert isinstance(record["opportunity"]["reasoning"], list)


class TestDecisionRecordStrategy:
    def test_has_strategy_family(self):
        record = build_v10_decision_record(_execute_pipeline())
        assert record["strategy_family"] is not None

    def test_has_confidence(self):
        record = build_v10_decision_record(_execute_pipeline())
        assert record["strategy_confidence"] is not None


class TestDecisionRecordHorizon:
    def test_has_horizon_type(self):
        record = build_v10_decision_record(_execute_pipeline())
        assert record["horizon"] is not None

    def test_has_expected_move(self):
        record = build_v10_decision_record(_execute_pipeline())
        assert record["horizon_min_move"] is not None
        assert record["horizon_max_move"] is not None
        assert record["horizon_unit"] is not None


class TestDecisionRecordEntry:
    def test_has_entry_method(self):
        record = build_v10_decision_record(_execute_pipeline())
        # May be None if entry was INVALID
        assert "entry_method" in record

    def test_has_stop_target(self):
        record = build_v10_decision_record(_execute_pipeline())
        assert "stop_price" in record
        assert "target_price" in record

    def test_has_rr(self):
        record = build_v10_decision_record(_execute_pipeline())
        assert "expected_rr" in record


class TestDecisionRecordRisk:
    def test_has_risk_approval(self):
        record = build_v10_decision_record(_execute_pipeline())
        assert "risk_approved" in record
        assert isinstance(record["risk_approved"], bool)

    def test_has_position_size(self):
        record = build_v10_decision_record(_execute_pipeline())
        assert "position_size" in record


class TestDecisionRecordExecution:
    def test_has_execution_approval(self):
        record = build_v10_decision_record(_execute_pipeline())
        assert "execution_approved" in record


class TestDecisionRecordSnapshots:
    def test_has_account_snapshot(self):
        result = _execute_pipeline()
        record = build_v10_decision_record(result)
        if result.account_snapshot and result.account_snapshot.available:
            assert record["account_snapshot"] is not None
            assert "balance" in record["account_snapshot"]

    def test_has_broker_snapshot(self):
        result = _execute_pipeline()
        record = build_v10_decision_record(result)
        if result.broker_snapshot and result.broker_snapshot.available:
            assert record["broker_snapshot"] is not None
            assert "tick_value" in record["broker_snapshot"]
            assert "spread" in record["broker_snapshot"]


# ═══════════════════════════════════════════════════════════════
# NO_TRADE PRESERVATION
# ═══════════════════════════════════════════════════════════════

class TestNoTradePreservation:
    def test_rejected_has_observation_id(self):
        record = build_v10_decision_record(_reject_pipeline())
        assert record["decision_id"] != ""

    def test_rejected_has_rejection_stage(self):
        record = build_v10_decision_record(_reject_pipeline())
        assert record["rejection_stage"] is not None
        assert record["rejection_stage"] != ""

    def test_rejected_has_rejection_reason(self):
        record = build_v10_decision_record(_reject_pipeline())
        assert record["rejection_reason"] is not None

    def test_rejected_has_market_state(self):
        record = build_v10_decision_record(_reject_pipeline())
        assert record["market_state"] is not None
        assert "regime" in record["market_state"]

    def test_rejected_preserves_opportunity_state(self):
        record = build_v10_decision_record(_reject_pipeline())
        assert record["opportunity"]["state"] == "INVALID"


# ═══════════════════════════════════════════════════════════════
# SERIALISATION COMPLETENESS
# ═══════════════════════════════════════════════════════════════

class TestSerialisationCompleteness:
    def test_record_is_json_serializable(self):
        record = build_v10_decision_record(_execute_pipeline())
        json_str = json.dumps(record, default=str)
        assert len(json_str) > 500  # Should be substantial

    def test_no_none_decision_id(self):
        record = build_v10_decision_record(_execute_pipeline())
        assert record["decision_id"] is not None
        assert record["decision_id"] != ""

    def test_no_none_symbol(self):
        record = build_v10_decision_record(_execute_pipeline())
        assert record["symbol"] is not None
        assert record["symbol"] != ""

    def test_no_none_timestamp(self):
        record = build_v10_decision_record(_execute_pipeline())
        assert record["timestamp_utc"] > 0

    def test_no_none_final_action(self):
        record = build_v10_decision_record(_execute_pipeline())
        assert record["final_action"] in ("EXECUTE", "NO_TRADE")

    def test_schema_version_present(self):
        record = build_v10_decision_record(_execute_pipeline())
        assert record["schema_version"] == "v10_decision_v1"

    def test_engine_version_present(self):
        record = build_v10_decision_record(_execute_pipeline())
        assert record["engine_version"] == "V10"


# ═══════════════════════════════════════════════════════════════
# RESEARCH RECONSTRUCTIBILITY
# ═══════════════════════════════════════════════════════════════

class TestResearchReconstructibility:
    """A researcher must be able to answer WHY from the stored record alone."""

    def test_can_determine_why_trade_happened(self):
        record = build_v10_decision_record(_execute_pipeline())
        # Researcher needs: opportunity reasoning + strategy + quality
        assert len(record["opportunity"]["reasoning"]) > 0 or record["opportunity"]["state"] == "INVALID"
        assert record["strategy_family"] is not None or record["rejection_stage"] in ("opportunity", "strategy")

    def test_can_determine_why_trade_rejected(self):
        record = build_v10_decision_record(_reject_pipeline())
        assert record["rejection_stage"] != ""
        assert record["rejection_reason"] != ""
        # Can reconstruct: "rejected at opportunity stage because INVALID"

    def test_can_determine_market_conditions(self):
        record = build_v10_decision_record(_execute_pipeline())
        ms = record["market_state"]
        # All these let researcher understand what market looked like:
        assert ms["h4_trend"] is not None
        assert ms["regime"] is not None
        assert ms["location_type"] is not None
        assert ms["inside_zone"] is not None

    def test_can_determine_risk_was_correct(self):
        record = build_v10_decision_record(_execute_pipeline())
        # With broker_snapshot, researcher can check if conditions were bad
        # Even without outcome, they can verify sizing was appropriate
        assert "risk_approved" in record
        assert "position_size" in record
