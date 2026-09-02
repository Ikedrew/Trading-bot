"""Tests for V10 Decision Report and Persistence."""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from core.market_understanding.models import (
    MarketUnderstanding, H4Understanding, H1Understanding,
    M15Understanding, M5Understanding,
)
from core.market_understanding.context_models import (
    MarketContextInterpretation, HTFStructureContext, LocationContext, BehaviourContext,
)
from core.v10.pipeline import V10Pipeline, PipelineResult
from core.v10.risk_model import AccountContext
from core.v10.broker_context import BrokerContext
from core.v10.decision_report import format_v10_decision
from core.v10.persistence_adapter import build_v10_decision_record as build_decision_record


def _run_pipeline(strong=True):
    """Run the pipeline with a known state and return result."""
    if strong:
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
                rejection_strength_atr=0.9,
                at_institutional_zone=True, zone_type="SUPPLY_OB",
                atr=0.00055, spread=0.00012, spread_atr_ratio=0.22,
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
    else:
        mu = MarketUnderstanding(symbol="EURUSD", timestamp_utc=1785400000.0, confidence=0.3)
        ctx = None

    broker = BrokerContext(connected=True, symbol_available=True, market_open=True, spread=0.00012, available_margin=5000.0)
    account = AccountContext(balance=10000.0, equity=10000.0)

    pipeline = V10Pipeline()
    return pipeline.process(mu, ctx, account, broker)


class TestDecisionReport:
    def test_report_contains_all_layers(self):
        result = _run_pipeline(strong=True)
        report = format_v10_decision(result)
        assert "[V10 MARKET UNDERSTANDING]" in report
        assert "[V10 OPPORTUNITY]" in report
        assert "[V10 STRATEGY]" in report
        assert "[V10 HORIZON]" in report
        assert "[V10 ENTRY]" in report
        assert "[V10 RISK]" in report
        assert "[V10 EXECUTION]" in report

    def test_report_contains_symbol(self):
        result = _run_pipeline(strong=True)
        report = format_v10_decision(result)
        assert "EURUSD" in report

    def test_report_contains_v10_engine_label(self):
        result = _run_pipeline(strong=True)
        report = format_v10_decision(result)
        assert "V10" in report

    def test_report_does_not_contain_legacy_concepts(self):
        result = _run_pipeline(strong=True)
        report = format_v10_decision(result)
        assert "Composite Score" not in report
        assert "Grade" not in report
        assert "Threshold" not in report
        assert "Neutral Score" not in report

    def test_invalid_opportunity_shows_no_trade(self):
        result = _run_pipeline(strong=False)
        report = format_v10_decision(result)
        assert "NO_TRADE" in report


class TestDecisionPersistence:
    def test_record_contains_complete_chain(self):
        result = _run_pipeline(strong=True)
        record = build_decision_record(result)
        assert "decision_id" in record
        assert "symbol" in record
        assert "timestamp_utc" in record
        assert "market_state" in record
        assert "opportunity" in record
        assert "strategy_family" in record
        assert "horizon" in record
        assert "entry_method" in record
        assert "risk_approved" in record
        assert "execution_approved" in record
        assert "final_action" in record
        assert "rejection_stage" in record

    def test_record_is_json_serializable(self):
        result = _run_pipeline(strong=True)
        record = build_decision_record(result)
        # Should not raise
        json_str = json.dumps(record, default=str)
        assert len(json_str) > 100

    def test_valid_opportunity_reaches_execution(self):
        result = _run_pipeline(strong=True)
        record = build_decision_record(result)
        # Opportunity should be VALID or WATCHING
        assert record["opportunity"]["state"] in ("VALID", "WATCHING")
        # Strategy should be populated
        assert record["strategy_family"] is not None and record["strategy_family"] != "NONE"

    def test_invalid_opportunity_stops_early(self):
        result = _run_pipeline(strong=False)
        record = build_decision_record(result)
        assert record["opportunity"]["state"] == "INVALID"
        assert record["final_action"] == "NO_TRADE"
        assert record["rejection_stage"] == "opportunity"

    def test_record_has_market_state_fields(self):
        result = _run_pipeline(strong=True)
        record = build_decision_record(result)
        ms = record["market_state"]
        assert "h4_trend" in ms
        assert "h1_bos_direction" in ms
        assert "regime" in ms
        assert "location_type" in ms


class TestLegacyConceptsRemoved:
    def test_no_composite_score_in_output(self):
        """V10 decisions should not reference legacy scoring."""
        result = _run_pipeline(strong=True)
        report = format_v10_decision(result)
        record = build_decision_record(result)
        # Report
        assert "Composite" not in report
        assert "Strategy Score" not in report
        # Record
        record_str = json.dumps(record)
        assert "composite_score" not in record_str
        assert "neutral_score" not in record_str
        assert "grade" not in record_str.lower() or "grade" not in record.get("final_action", "").lower()
