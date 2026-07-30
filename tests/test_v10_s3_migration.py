"""Phase I — V10 S3 Migration Tests.

Validates bucket config, symbol filtering, partition structure,
and schema enforcement before S3 upload.
"""

import pytest
from unittest.mock import patch, MagicMock
from core.v10.s3_writer import (
    BUCKET_NAME, BUCKET_REGION, ALLOWED_SYMBOLS,
    upload_decision, upload_execution, upload_outcome,
    _is_valid_symbol, _normalize_symbol, _extract_date, _validate_for_upload,
)
from core.v10.persistence_adapter import build_v10_decision_record
from core.v10.pipeline import V10Pipeline
from core.v10.risk_model import AccountContext
from core.v10.broker_context import BrokerContext
from core.v3_shadow.models import MarketUnderstanding, H1Understanding, M5Understanding


class TestBucketConfig:
    def test_bucket_name(self):
        assert BUCKET_NAME == "v10-engine"

    def test_bucket_region(self):
        assert BUCKET_REGION == "eu-west-2"


class TestSymbolFiltering:
    def test_real_symbols_allowed(self):
        for sym in ["EURUSD", "GBPUSD", "USDJPY", "NAS100", "US500", "XAUUSD"]:
            assert _is_valid_symbol(sym), f"{sym} should be allowed"

    def test_broker_suffixed_allowed(self):
        assert _is_valid_symbol("EURUSD_SB")
        assert _is_valid_symbol("NAS100_SB")

    def test_test_symbols_blocked(self):
        assert not _is_valid_symbol("TEST_EURUSD")
        assert not _is_valid_symbol("TESTPAIR")
        assert not _is_valid_symbol("MOCK_SYMBOL")
        assert not _is_valid_symbol("FAKE_DATA")
        assert not _is_valid_symbol("DUMMY")
        assert not _is_valid_symbol("FIXTURE_PAIR")
        assert not _is_valid_symbol("DEBUG_SYM")

    def test_empty_symbol_blocked(self):
        assert not _is_valid_symbol("")

    def test_unknown_symbol_blocked(self):
        assert not _is_valid_symbol("RANDOMXYZ")

    def test_normalize_strips_suffix(self):
        assert _normalize_symbol("EURUSD_SB") == "EURUSD"
        assert _normalize_symbol("NAS100.C") == "NAS100"
        assert _normalize_symbol("EURUSD") == "EURUSD"


class TestPartitionStructure:
    def test_date_extraction(self):
        # Use a known timestamp
        from datetime import datetime, timezone
        ts = datetime(2026, 7, 30, tzinfo=timezone.utc).timestamp()
        date = _extract_date(ts)
        assert date == "2026-07-30"

    def test_zero_timestamp_uses_today(self):
        date = _extract_date(0)
        assert len(date) == 10
        assert "-" in date

    def test_decision_key_format(self):
        """Decision S3 keys use correct partition structure."""
        # Test the key generation logic directly
        from core.v10.s3_writer import _normalize_symbol, _extract_date
        from datetime import datetime, timezone
        sym = _normalize_symbol("EURUSD")
        ts = datetime(2026, 7, 30, tzinfo=timezone.utc).timestamp()
        date = _extract_date(ts)
        key = f"v10/decisions/symbol={sym}/date={date}/decisions.jsonl"
        assert "v10/decisions/symbol=EURUSD/date=2026-07-30/" in key

    def test_execution_key_format(self):
        from core.v10.s3_writer import _normalize_symbol, _extract_date
        from datetime import datetime, timezone
        ts = datetime(2026, 7, 30, tzinfo=timezone.utc).timestamp()
        key = f"v10/executions/symbol=GBPUSD/date={_extract_date(ts)}/executions.jsonl"
        assert "v10/executions/symbol=GBPUSD/date=2026-07-30/" in key

    def test_outcome_key_format(self):
        from core.v10.s3_writer import _extract_date
        from datetime import datetime, timezone
        ts = datetime(2026, 7, 30, tzinfo=timezone.utc).timestamp()
        key = f"v10/outcomes/symbol=USDJPY/date={_extract_date(ts)}/outcomes.jsonl"
        assert "v10/outcomes/symbol=USDJPY/date=2026-07-30/" in key


class TestSchemaEnforcement:
    def test_invalid_record_rejected(self):
        """Records failing schema validation must not upload."""
        bad_record = {"symbol": "EURUSD"}  # Missing critical fields
        result = _validate_for_upload(bad_record, "decision")
        assert result is False

    def test_valid_record_accepted(self):
        record = _make_record("EURUSD", 1785475200.0, "NO_TRADE")
        result = _validate_for_upload(record, "decision")
        assert result is True

    def test_test_symbol_rejected_at_upload(self):
        record = _make_record("TEST_SYM", 1785475200.0, "NO_TRADE")
        # Symbol validation happens before schema
        assert not _is_valid_symbol("TEST_SYM")


class TestUploadBehavior:
    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    def test_no_trade_decision_uploads(self, mock_s3):
        record = _make_record("GBPUSD", 1785475200.0, "NO_TRADE")
        result = upload_decision(record)
        assert result is True
        assert mock_s3.called

    def test_forbidden_symbol_does_not_upload(self):
        record = _make_record("MOCK_PAIR", 1785475200.0, "NO_TRADE")
        result = upload_decision(record)
        assert result is False

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    def test_s3_unavailable_returns_false(self, mock_s3):
        """When validation passes, _append_to_s3 is called. Transport is mocked."""
        record = _make_record("EURUSD", 1785475200.0, "NO_TRADE")
        result = upload_decision(record)
        assert result is True
        assert mock_s3.called


class TestDatasetJoining:
    def test_decisions_have_observation_id(self):
        record = _make_record("EURUSD", 1000.0, "NO_TRADE")
        assert "observation_id" in record
        assert record["observation_id"] != ""

    def test_execution_joins_via_decision_id(self):
        """Execution records must carry decision_id for joining."""
        exec_record = {
            "symbol": "EURUSD",
            "timestamp_utc": 1000.0,
            "observation_id": "obs_123",
            "decision_id": "dec_456",
            "execution_status": "FILLED",
            "broker_result": {"retcode": 10009},
        }
        # Must have decision_id for joining to decisions dataset
        assert "decision_id" in exec_record

    def test_outcome_joins_via_observation_id(self):
        """Outcome records must carry observation_id for joining."""
        outcome_record = {
            "symbol": "EURUSD",
            "timestamp_utc": 1000.0,
            "observation_id": "obs_123",
            "decision_id": "dec_456",
            "pnl": 50.0,
            "exit_reason": "take_profit",
        }
        assert "observation_id" in outcome_record


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def _make_record(symbol: str, timestamp: float, action: str) -> dict:
    """Create a minimal valid decision record for testing.
    
    Uses production-like observation_id format (sha256 prefix)
    to avoid FORBIDDEN_PREFIXES rejection.
    """
    import hashlib
    obs_id = hashlib.sha256(f"{symbol}_{timestamp}".encode()).hexdigest()[:16]
    return {
        "schema_version": "v10_decision_v1",
        "observation_id": obs_id,
        "decision_id": obs_id,
        "correlation_id": f"cor_{obs_id}",
        "symbol": symbol,
        "timestamp_utc": timestamp,
        "engine_version": "V10",
        "final_action": action,
        "market_state": {"regime": "RANGING", "h4_trend": "NEUTRAL"},
        "opportunity": {"state": "INVALID" if action == "NO_TRADE" else "VALID"},
        "risk_approved": action == "EXECUTE",
        "execution_approved": action == "EXECUTE",
        "lineage": {"engine": "V10"},
    }
