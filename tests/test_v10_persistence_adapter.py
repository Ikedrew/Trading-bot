"""Tests for V10 Persistence Adapter."""

import pytest
import json
from core.market_understanding.models import (
    MarketUnderstanding, H4Understanding, H1Understanding,
    M15Understanding, M5Understanding,
)
from core.market_understanding.context_models import (
    MarketContextInterpretation, HTFStructureContext, LocationContext, BehaviourContext,
)
from core.v10.pipeline import V10Pipeline
from core.v10.risk_model import AccountContext
from core.v10.broker_context import BrokerContext
from core.v10.persistence_adapter import (
    build_v10_decision_record, build_v10_ledger_entry,
)


def _strong_result():
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
            zone_type="SUPPLY_OB", atr=0.00055, spread=0.00012, spread_atr_ratio=0.22,
        ),
    )
    ctx = MarketContextInterpretation(
        symbol="EURUSD", timestamp_utc=1785400000.0,
        htf_structure=HTFStructureContext(macro_bias="NEUTRAL", structure_alignment=0.30, bos_active=True, bos_direction="BEARISH"),
        location=LocationContext(
            location_type="SUPPLY_OB", inside_institutional_zone=True,
            premium_discount="PREMIUM", range_position=0.75, zone_quality=0.85,
            liquidity_below=True, nearest_liquidity_distance_pips=12.0,
        ),
        behaviour=BehaviourContext(regime="RANGING", volatility_state="NEUTRAL", momentum_direction="NEUTRAL", momentum_strength=0.2),
        overall_confidence=0.8,
    )
    broker = BrokerContext(connected=True, symbol_available=True, market_open=True, spread=0.00012, available_margin=5000.0)
    account = AccountContext(balance=10000.0, equity=10000.0)
    pipeline = V10Pipeline()
    return pipeline.process(mu, ctx, account, broker)


def _weak_result():
    mu = MarketUnderstanding(symbol="GBPUSD", timestamp_utc=1785400000.0, confidence=0.3)
    broker = BrokerContext(connected=True, symbol_available=True, market_open=True, spread=0.0002, available_margin=5000.0)
    account = AccountContext(balance=10000.0, equity=10000.0)
    pipeline = V10Pipeline()
    return pipeline.process(mu, None, account, broker)


class TestV10DecisionRecord:
    def test_execute_persists_completely(self):
        result = _strong_result()
        record = build_v10_decision_record(result, cycle_id=42)
        # All mandatory fields present
        assert record["decision_id"] != ""
        assert record["correlation_id"] != ""
        assert record["symbol"] == "EURUSD"
        assert record["timestamp_utc"] == 1785400000.0
        assert record["engine_version"] == "V10"
        assert record["schema_version"] == "v10_decision_v1"

    def test_no_trade_persists_completely(self):
        result = _weak_result()
        record = build_v10_decision_record(result, cycle_id=1)
        assert record["final_action"] == "NO_TRADE"
        assert record["rejection_stage"] is not None
        assert record["symbol"] == "GBPUSD"
        assert record["engine_version"] == "V10"

    def test_rejection_stage_recorded(self):
        result = _weak_result()
        record = build_v10_decision_record(result)
        assert record["rejection_stage"] == "opportunity"
        assert record["rejection_reason"] is not None

    def test_strategy_family_recorded(self):
        result = _strong_result()
        record = build_v10_decision_record(result)
        # Should have a strategy (MEAN_REVERSION or similar)
        if record["final_action"] != "NO_TRADE" or record["rejection_stage"] not in ("opportunity", "strategy"):
            assert record["strategy_family"] is not None

    def test_horizon_recorded(self):
        result = _strong_result()
        record = build_v10_decision_record(result)
        if record["strategy_family"]:
            assert record["horizon"] is not None

    def test_entry_method_recorded(self):
        result = _strong_result()
        record = build_v10_decision_record(result)
        if record["entry_status"] != "INVALID":
            assert record["entry_method"] is not None

    def test_decision_id_unique(self):
        r1 = _strong_result()
        r2 = _weak_result()
        rec1 = build_v10_decision_record(r1)
        rec2 = build_v10_decision_record(r2)
        assert rec1["decision_id"] != rec2["decision_id"]

    def test_no_legacy_fields(self):
        result = _strong_result()
        record = build_v10_decision_record(result)
        record_str = json.dumps(record)
        assert "composite_score" not in record_str
        assert "neutral_score" not in record_str
        assert "grade" not in record_str
        assert "pattern_gate" not in record_str

    def test_is_json_serializable(self):
        result = _strong_result()
        record = build_v10_decision_record(result, cycle_id=42)
        json_str = json.dumps(record, default=str)
        assert len(json_str) > 200

    def test_market_state_always_present(self):
        result = _weak_result()
        record = build_v10_decision_record(result)
        ms = record["market_state"]
        assert "h4_trend" in ms
        assert "regime" in ms
        assert "location_type" in ms


class TestV10LedgerEntry:
    def test_ledger_entry_compatible(self):
        result = _strong_result()
        entry = build_v10_ledger_entry(result, cycle_id=42)
        # Standard ledger fields
        assert "symbol" in entry
        assert "decision" in entry
        assert "timestamp" in entry
        assert "engine_version" in entry
        assert entry["engine_version"] == "V10"

    def test_ledger_has_v10_extension(self):
        result = _strong_result()
        entry = build_v10_ledger_entry(result, cycle_id=42)
        assert "v10" in entry
        v10 = entry["v10"]
        assert "strategy_family" in v10
        assert "horizon" in v10
        assert "entry_method" in v10
        assert "decision_id" in v10

    def test_execute_has_intent(self):
        result = _strong_result()
        if result.approved:
            entry = build_v10_ledger_entry(result)
            assert entry.get("execution_intent") is not None
            intent = entry["execution_intent"]
            assert "direction" in intent
            assert "entry_price" in intent
            assert "stop_loss" in intent

    def test_no_trade_has_reason(self):
        result = _weak_result()
        entry = build_v10_ledger_entry(result)
        assert entry["decision"] == "NO_TRADE"
        assert entry["reason"] != ""


class TestResearchCompatibility:
    def test_record_supports_strategy_research(self):
        """Research can filter by strategy_family."""
        result = _strong_result()
        record = build_v10_decision_record(result)
        # Researchers can ask: "what happened after MEAN_REVERSION?"
        assert "strategy_family" in record

    def test_record_supports_horizon_research(self):
        """Research can compare SCALP vs INTRADAY performance."""
        result = _strong_result()
        record = build_v10_decision_record(result)
        assert "horizon" in record

    def test_record_supports_rejection_research(self):
        """Research can study which rejections were correct."""
        result = _weak_result()
        record = build_v10_decision_record(result)
        assert "rejection_stage" in record
        assert "rejection_reason" in record
        assert record["opportunity"]["state"] == "INVALID"
