"""Phase I.5 — V10 Event & Timestamp Integrity Tests."""

import pytest
from core.v10.pipeline import V10Pipeline
from core.v10.risk_model import AccountContext
from core.v10.broker_context import BrokerContext
from core.v10.pipeline_events import (
    STAGE_ORDER, PipelineEventCollector,
    validate_timestamp, validate_record_timestamp,
)
from core.v3_shadow.models import MarketUnderstanding, H1Understanding, M5Understanding


def _run():
    mu = MarketUnderstanding(symbol="EURUSD", timestamp_utc=1785400000.0, confidence=0.8,
        h1=H1Understanding(bos_confirmed=True, bos_direction="BEARISH", dominant_trend="BEARISH",
                           structural_clarity=0.8, swing_high=1.095, swing_low=1.085,
                           active_supply_ob_high=1.094, active_supply_ob_low=1.0935,
                           active_demand_ob_high=1.086, active_demand_ob_low=1.0855),
        m5=M5Understanding(atr=0.0006, rejection_present=True, rejection_direction="BEARISH",
                           at_institutional_zone=True, zone_type="SUPPLY_OB"))
    account = AccountContext(balance=10000.0, equity=10000.0)
    broker = BrokerContext(connected=True, symbol_available=True, market_open=True,
                           spread=0.00012, available_margin=5000.0,
                           tick_value=1.0, tick_size=0.00001,
                           volume_min=0.01, volume_max=100.0, volume_step=0.01, point=0.00001)
    return V10Pipeline().process(mu, None, account, broker)


def _run_reject():
    mu = MarketUnderstanding(symbol="GBPUSD", timestamp_utc=1785400000.0)
    return V10Pipeline().process(mu, None, AccountContext(balance=10000.0), BrokerContext())


class TestTimestampValidation:
    def test_valid_timestamp(self):
        valid, reason = validate_timestamp(1785400000.0)
        assert valid

    def test_zero_rejected(self):
        valid, _ = validate_timestamp(0)
        assert not valid

    def test_none_rejected(self):
        valid, _ = validate_timestamp(None)
        assert not valid

    def test_1970_rejected(self):
        valid, _ = validate_timestamp(100.0)  # 1970
        assert not valid

    def test_far_future_rejected(self):
        valid, _ = validate_timestamp(9999999999.0)  # ~2286
        assert not valid

    def test_record_validation(self):
        result = validate_record_timestamp({"observation_id": "x", "timestamp_utc": 1785400000.0})
        assert result["status"] == "PASS"
        assert "2026" in result["converted_date"]

    def test_record_zero_fails(self):
        result = validate_record_timestamp({"observation_id": "x", "timestamp_utc": 0})
        assert result["status"] == "FAIL"


class TestPipelineEventEmission:
    def test_events_emitted(self):
        result = _run()
        assert result.events is not None
        assert result.events.stage_count >= 8

    def test_decision_complete_emitted(self):
        result = _run()
        assert result.events.complete

    def test_all_stages_present(self):
        result = _run()
        emitted_types = [e.event_type for e in result.events.events]
        for stage in STAGE_ORDER:
            assert stage in emitted_types, f"Missing: {stage}"

    def test_observation_id_consistent(self):
        result = _run()
        obs_id = result.opportunity.observation_id
        assert obs_id != ""
        # All events after opportunity should share the same observation_id
        for event in result.events.events:
            if event.event_type != "V10_MARKET_STATE_COMPLETE":
                assert event.observation_id == obs_id

    def test_event_ordering_valid(self):
        result = _run()
        valid, violations = result.events.validate_ordering()
        assert valid, f"Ordering violations: {violations}"


class TestNoTradeEventPath:
    def test_rejected_emits_events(self):
        result = _run_reject()
        assert result.events is not None
        assert result.events.complete  # DECISION_COMPLETE always emitted

    def test_rejected_has_rejection_status(self):
        result = _run_reject()
        # At least one event should have status REJECTED
        statuses = [e.status for e in result.events.events]
        assert "REJECTED" in statuses

    def test_rejected_ordering_valid(self):
        result = _run_reject()
        valid, violations = result.events.validate_ordering()
        assert valid


class TestEventPayload:
    def test_events_have_symbol(self):
        result = _run()
        for event in result.events.events:
            assert event.symbol == "EURUSD"

    def test_events_have_timestamp(self):
        result = _run()
        for event in result.events.events:
            assert event.timestamp_utc > 0

    def test_events_have_engine_version(self):
        result = _run()
        for event in result.events.events:
            assert event.engine_version == "V10"

    def test_opportunity_event_has_state(self):
        result = _run()
        opp_events = [e for e in result.events.events if e.event_type == "V10_OPPORTUNITY_COMPLETE"]
        assert len(opp_events) == 1
        assert "state" in opp_events[0].payload

    def test_strategy_event_has_family(self):
        result = _run()
        strat_events = [e for e in result.events.events if e.event_type == "V10_STRATEGY_COMPLETE"]
        assert len(strat_events) == 1
        assert "family" in strat_events[0].payload


class TestEventCollectorUnit:
    def test_ordering_violation_detected(self):
        collector = PipelineEventCollector("obs1", "EURUSD", 1000.0)
        # Emit out of order: STRATEGY before OPPORTUNITY
        collector.emit("V10_STRATEGY_COMPLETE")
        collector.emit("V10_OPPORTUNITY_COMPLETE")
        valid, violations = collector.validate_ordering()
        assert not valid
        assert len(violations) > 0

    def test_correct_order_passes(self):
        collector = PipelineEventCollector("obs1", "EURUSD", 1000.0)
        collector.emit("V10_MARKET_STATE_COMPLETE")
        collector.emit("V10_OPPORTUNITY_COMPLETE")
        collector.emit("V10_STRATEGY_COMPLETE")
        valid, _ = collector.validate_ordering()
        assert valid

    def test_no_duplicate_detection(self):
        collector = PipelineEventCollector("obs1", "EURUSD", 1000.0)
        collector.emit("V10_MARKET_STATE_COMPLETE")
        collector.emit("V10_MARKET_STATE_COMPLETE")  # Duplicate
        assert collector.stage_count == 2  # Allowed but noted
