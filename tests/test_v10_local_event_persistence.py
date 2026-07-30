"""Verify local event persistence writes all 8 stages to JSONL."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch
from core.v10.pipeline import V10Pipeline
from core.v10.risk_model import AccountContext
from core.v10.broker_context import BrokerContext
from core.v10.persistence_adapter import persist_v10_full, _EVENTS_DIR
from core.v3_shadow.models import MarketUnderstanding, H1Understanding, M5Understanding


def _run_and_persist(tmp_path):
    """Run pipeline and persist to tmp_path."""
    mu = MarketUnderstanding(
        symbol="EURUSD", timestamp_utc=1785400000.0,
        h1=H1Understanding(bos_confirmed=True, bos_direction="BEARISH",
                           dominant_trend="BEARISH", structural_clarity=0.8,
                           swing_high=1.095, swing_low=1.085,
                           active_supply_ob_high=1.094, active_supply_ob_low=1.0935,
                           active_demand_ob_high=1.086, active_demand_ob_low=1.0855),
        m5=M5Understanding(atr=0.0006, rejection_present=True, rejection_direction="BEARISH",
                           at_institutional_zone=True, zone_type="SUPPLY_OB"),
    )
    account = AccountContext(balance=10000.0, equity=10000.0)
    broker = BrokerContext(connected=True, symbol_available=True, market_open=True,
                           symbol="EURUSD", spread=0.00012, available_margin=5000.0,
                           tick_value=1.0, tick_size=0.00001,
                           volume_min=0.01, volume_max=100.0, volume_step=0.01, point=0.00001)

    result = V10Pipeline().process(mu, None, account, broker)

    # Patch output dirs to use tmp_path
    with patch("core.v10.persistence_adapter._OUTPUT_DIR", str(tmp_path / "decisions")):
        with patch("core.v10.persistence_adapter._EVENTS_DIR", str(tmp_path / "events")):
            persist_v10_full(result, cycle_id=9999)

    return result


class TestLocalEventPersistence:

    def test_events_file_created(self, tmp_path):
        _run_and_persist(tmp_path)
        events_dir = tmp_path / "events" / "EURUSD"
        assert events_dir.exists()
        files = list(events_dir.glob("*.jsonl"))
        assert len(files) == 1

    def test_all_8_stages_written(self, tmp_path):
        result = _run_and_persist(tmp_path)
        events_file = tmp_path / "events" / "EURUSD" / "2026-07-30.jsonl"
        lines = events_file.read_text().strip().split("\n")
        obs_id = result.opportunity.observation_id
        events = [json.loads(l) for l in lines if obs_id in l]
        assert len(events) == 8

    def test_event_types_correct(self, tmp_path):
        result = _run_and_persist(tmp_path)
        events_file = tmp_path / "events" / "EURUSD" / "2026-07-30.jsonl"
        lines = events_file.read_text().strip().split("\n")
        obs_id = result.opportunity.observation_id
        events = [json.loads(l) for l in lines if obs_id in l]
        types = [e["event_type"] for e in events]
        assert "V10_MARKET_STATE_COMPLETE" in types
        assert "V10_OPPORTUNITY_COMPLETE" in types
        assert "V10_STRATEGY_COMPLETE" in types
        assert "V10_HORIZON_COMPLETE" in types
        assert "V10_ENTRY_COMPLETE" in types
        assert "V10_RISK_COMPLETE" in types
        assert "V10_EXECUTION_COMPLETE" in types
        assert "V10_DECISION_COMPLETE" in types

    def test_correlation_id_shared(self, tmp_path):
        result = _run_and_persist(tmp_path)
        events_file = tmp_path / "events" / "EURUSD" / "2026-07-30.jsonl"
        lines = events_file.read_text().strip().split("\n")
        obs_id = result.opportunity.observation_id
        events = [json.loads(l) for l in lines if obs_id in l]
        # All share same correlation_id
        cor_ids = set(e["correlation_id"] for e in events)
        assert len(cor_ids) == 1
        assert obs_id in cor_ids

    def test_decision_file_also_created(self, tmp_path):
        _run_and_persist(tmp_path)
        decision_dir = tmp_path / "decisions" / "EURUSD"
        assert decision_dir.exists()
        files = list(decision_dir.glob("*.jsonl"))
        assert len(files) == 1

    def test_decision_contains_all_stages(self, tmp_path):
        _run_and_persist(tmp_path)
        decision_file = tmp_path / "decisions" / "EURUSD" / "2026-07-30.jsonl"
        lines = decision_file.read_text().strip().split("\n")
        record = json.loads(lines[-1])
        # Composite record has all stage data
        assert "market_state" in record
        assert "opportunity" in record
        assert "strategy_family" in record
        assert "horizon" in record
        assert "entry_status" in record
        assert "risk_approved" in record
        assert "execution_approved" in record
        assert "final_action" in record
