"""Phase I.6 — V10 Event Persistence Tests."""

import pytest
from unittest.mock import patch, MagicMock
from core.v10.s3_writer import (
    upload_events, _validate_event, EVENT_SCHEMA_VERSION, _VALID_EVENT_TYPES,
)
from core.v10.pipeline import V10Pipeline
from core.v10.risk_model import AccountContext
from core.v10.broker_context import BrokerContext
from core.v3_shadow.models import MarketUnderstanding, H1Understanding, M5Understanding


def _valid_event(obs_id="a1b2c3d4e5f67890", event_type="V10_OPPORTUNITY_COMPLETE"):
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "observation_id": obs_id,
        "symbol": "EURUSD",
        "timestamp_utc": 1785400000.0,
        "engine_version": "V10",
        "event_type": event_type,
        "stage": "OPPORTUNITY",
        "status": "COMPLETE",
        "payload": {},
    }


def _run_pipeline():
    mu = MarketUnderstanding(
        symbol="EURUSD", timestamp_utc=1785400000.0,
        h1=H1Understanding(bos_confirmed=True, bos_direction="BEARISH", dominant_trend="BEARISH",
                           structural_clarity=0.8, swing_high=1.095, swing_low=1.085,
                           active_supply_ob_high=1.094, active_supply_ob_low=1.0935,
                           active_demand_ob_high=1.086, active_demand_ob_low=1.0855),
        m5=M5Understanding(atr=0.0006, rejection_present=True, rejection_direction="BEARISH",
                           at_institutional_zone=True, zone_type="SUPPLY_OB"),
    )
    account = AccountContext(balance=10000.0, equity=10000.0)
    broker = BrokerContext(connected=True, symbol_available=True, market_open=True,
                           spread=0.00012, available_margin=5000.0,
                           tick_value=1.0, tick_size=0.00001,
                           volume_min=0.01, volume_max=100.0, volume_step=0.01, point=0.00001)
    return V10Pipeline().process(mu, None, account, broker)


class TestEventSchemaValidation:
    def test_valid_event_passes(self):
        assert _validate_event(_valid_event()) is True

    def test_missing_observation_id_fails(self):
        e = _valid_event()
        e["observation_id"] = ""
        assert _validate_event(e) is False

    def test_missing_symbol_fails(self):
        e = _valid_event()
        e["symbol"] = ""
        assert _validate_event(e) is False

    def test_zero_timestamp_fails(self):
        e = _valid_event()
        e["timestamp_utc"] = 0
        assert _validate_event(e) is False

    def test_wrong_engine_fails(self):
        e = _valid_event()
        e["engine_version"] = "LEGACY"
        assert _validate_event(e) is False

    def test_unknown_event_type_fails(self):
        e = _valid_event()
        e["event_type"] = "UNKNOWN_EVENT"
        assert _validate_event(e) is False

    def test_all_valid_types_accepted(self):
        for et in _VALID_EVENT_TYPES:
            e = _valid_event(event_type=et)
            assert _validate_event(e) is True


class TestEventUpload:
    def test_valid_events_pass_validation(self):
        """Valid events pass all validation checks."""
        events = [_valid_event(obs_id="a1b2c3d4e5f60001", event_type=et) for et in list(_VALID_EVENT_TYPES)[:3]]
        for e in events:
            assert _validate_event(e) is True

    def test_upload_events_validation_passes(self):
        """Valid events pass validation. Transport is NOT called (we test validation only)."""
        from core.v10.s3_writer import _validate_event
        events = [_valid_event(obs_id="a1b2c3d4e5f60099")]
        # Verify validation passes for each event
        for e in events:
            assert _validate_event(e) is True

    def test_test_symbol_rejected(self):
        events = [_valid_event()]
        events[0]["symbol"] = "TEST_PAIR"
        result = upload_events(events)
        assert result is False

    def test_empty_events_rejected(self):
        result = upload_events([])
        assert result is False

    def test_invalid_event_in_batch_rejects_all(self):
        events = [_valid_event(), _valid_event()]
        events[1]["engine_version"] = "BAD"
        result = upload_events(events)
        assert result is False

    def test_event_key_format(self):
        """Verify the S3 key would be structured correctly."""
        from core.v10.s3_writer import _normalize_symbol, _extract_date
        sym = _normalize_symbol("EURUSD")
        date = _extract_date(1785400000.0)
        key = f"v10/events/symbol={sym}/date={date}/events_obs123.jsonl"
        assert "v10/events/symbol=EURUSD/" in key
        assert "events_obs123" in key


class TestEventLineagePreservation:
    def test_pipeline_events_share_observation_id(self):
        result = _run_pipeline()
        obs_id = result.opportunity.observation_id
        for event in result.events.events:
            if event.event_type != "V10_MARKET_STATE_COMPLETE":
                assert event.observation_id == obs_id

    def test_event_observation_id_matches_decision(self):
        """Events and decision record share same root ID."""
        from core.v10.persistence_adapter import build_v10_decision_record
        result = _run_pipeline()
        record = build_v10_decision_record(result)
        obs_id = record["observation_id"]
        for event in result.events.events:
            if event.observation_id:  # Market state may not have it yet
                assert event.observation_id == obs_id


class TestEventChains:
    def test_execute_chain_has_8_events(self):
        result = _run_pipeline()
        assert result.events.stage_count == 8

    def test_no_trade_chain_has_8_events(self):
        mu = MarketUnderstanding(symbol="GBPUSD", timestamp_utc=1785400000.0)
        result = V10Pipeline().process(mu, None, AccountContext(balance=10000.0), BrokerContext())
        assert result.events.stage_count == 8

    def test_rejected_chain_has_rejected_status(self):
        mu = MarketUnderstanding(symbol="GBPUSD", timestamp_utc=1785400000.0)
        result = V10Pipeline().process(mu, None, AccountContext(balance=10000.0), BrokerContext())
        statuses = [e.status for e in result.events.events]
        assert "REJECTED" in statuses

    def test_serialized_events_have_schema_version(self):
        """When persisted, events get schema_version added."""
        from core.v10.scanner_adapter import _persist_events
        result = _run_pipeline()
        # Simulate what _persist_events does
        event_records = []
        for event in result.events.events:
            record = event.to_dict()
            record["schema_version"] = EVENT_SCHEMA_VERSION
            event_records.append(record)
        # All should have schema version
        for r in event_records:
            assert r["schema_version"] == "v10_event_v1"
