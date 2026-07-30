"""Phase J.6.1/J.6.2 — S3 Pollution Prevention & Observation ID Hardening Tests.

Proves that test artifacts, stale timestamps, and invalid observation_ids
are rejected BEFORE reaching S3 transport. Prevents recurrence of the
'test_EURUSD_1000' and 'no_boto' pollution incidents.
"""

import pytest
from unittest.mock import patch
from core.v10.s3_writer import (
    upload_decision, upload_execution, upload_outcome, upload_events,
    _validate_for_upload, _is_valid_symbol, _validate_event,
    validate_observation_id,
    FORBIDDEN_PREFIXES,
)


# ═══════════════════════════════════════════════════════════════
# VALID RECORD HELPER
# ═══════════════════════════════════════════════════════════════

def _valid_record(symbol="EURUSD", timestamp=1785475200.0, obs_id="a1b2c3d4e5f60001"):
    """Minimal valid decision record that passes all validation."""
    return {
        "schema_version": "v10_decision_v1",
        "observation_id": obs_id,
        "decision_id": obs_id,
        "correlation_id": f"cor_{obs_id}",
        "symbol": symbol,
        "timestamp_utc": timestamp,
        "engine_version": "V10",
        "final_action": "NO_TRADE",
        "market_state": {"regime": "RANGING"},
        "opportunity": {"state": "INVALID"},
        "risk_approved": False,
        "execution_approved": False,
        "lineage": {"engine": "V10"},
    }


# ═══════════════════════════════════════════════════════════════
# TIMESTAMP VALIDATION ON DECISIONS
# ═══════════════════════════════════════════════════════════════

class TestTimestampRejection:
    """Timestamps outside 2025-2030 must be rejected for ALL datasets."""

    def test_epoch_timestamp_rejected(self):
        """timestamp=0 must never reach S3."""
        record = _valid_record(timestamp=0)
        assert _validate_for_upload(record, "decision") is False

    def test_1970_timestamp_rejected(self):
        """timestamp=1000 (1970-01-01) must be rejected — the actual pollution case."""
        record = _valid_record(timestamp=1000.0)
        assert _validate_for_upload(record, "decision") is False

    def test_2020_timestamp_rejected(self):
        """timestamp from 2020 is before 2025 range — rejected."""
        record = _valid_record(timestamp=1577836800.0)  # 2020-01-01
        assert _validate_for_upload(record, "decision") is False

    def test_2025_timestamp_accepted(self):
        """timestamp from 2025 is within range — accepted."""
        record = _valid_record(timestamp=1735689601.0)  # 2025-01-01 + 1s
        assert _validate_for_upload(record, "decision") is True

    def test_2026_timestamp_accepted(self):
        """Current production timestamp — accepted."""
        record = _valid_record(timestamp=1785475200.0)  # 2026-07-30
        assert _validate_for_upload(record, "decision") is True

    def test_2031_timestamp_rejected(self):
        """Impossible future timestamp — rejected."""
        record = _valid_record(timestamp=1925000000.0)  # ~2031
        assert _validate_for_upload(record, "decision") is False

    def test_execution_timestamp_validated(self):
        """upload_execution also validates timestamp."""
        record = _valid_record(timestamp=1000.0)
        assert _validate_for_upload(record, "execution") is False

    def test_outcome_timestamp_validated(self):
        """upload_outcome also validates timestamp."""
        record = _valid_record(timestamp=1000.0)
        assert _validate_for_upload(record, "outcome") is False


# ═══════════════════════════════════════════════════════════════
# OBSERVATION ID PREFIX REJECTION
# ═══════════════════════════════════════════════════════════════

class TestObservationIdPrefixRejection:
    """observation_ids starting with forbidden prefixes must be rejected."""

    def test_test_prefix_rejected(self):
        record = _valid_record(obs_id="test_EURUSD_1000")
        assert _validate_for_upload(record, "decision") is False

    def test_TEST_prefix_rejected(self):
        record = _valid_record(obs_id="TEST_something_x")
        assert _validate_for_upload(record, "decision") is False

    def test_mock_prefix_rejected(self):
        record = _valid_record(obs_id="mock_observation1")
        assert _validate_for_upload(record, "decision") is False

    def test_fake_prefix_rejected(self):
        record = _valid_record(obs_id="FAKE_data_001abc")
        assert _validate_for_upload(record, "decision") is False

    def test_dummy_prefix_rejected(self):
        record = _valid_record(obs_id="dummy_record_xyz")
        assert _validate_for_upload(record, "decision") is False

    def test_fixture_prefix_rejected(self):
        record = _valid_record(obs_id="fixture_test_abc")
        assert _validate_for_upload(record, "decision") is False

    def test_debug_prefix_rejected(self):
        record = _valid_record(obs_id="DEBUG_session_42")
        assert _validate_for_upload(record, "decision") is False

    def test_short_id_rejected(self):
        """IDs shorter than 12 chars are test artifacts."""
        record = _valid_record(obs_id="no_boto")
        assert _validate_for_upload(record, "decision") is False

    def test_very_short_id_rejected(self):
        record = _valid_record(obs_id="abc")
        assert _validate_for_upload(record, "decision") is False

    def test_numeric_id_rejected(self):
        """Purely numeric IDs (raw timestamps) rejected."""
        record = _valid_record(obs_id="1785475200000")
        assert _validate_for_upload(record, "decision") is False

    def test_production_hash_id_accepted(self):
        """Real production observation_id (sha256 prefix) is accepted."""
        record = _valid_record(obs_id="7cb8c27c0d5d1234")
        assert _validate_for_upload(record, "decision") is True

    def test_empty_observation_id_rejected(self):
        """Empty obs_id rejected by validator."""
        record = _valid_record(obs_id="")
        result = _validate_for_upload(record, "decision")
        assert result is False


# ═══════════════════════════════════════════════════════════════
# FULL UPLOAD PATH REJECTION
# ═══════════════════════════════════════════════════════════════

class TestUploadPathRejection:
    """upload_decision/execution/outcome must reject polluted records."""

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    def test_upload_decision_rejects_stale_timestamp(self, mock_s3):
        record = _valid_record(timestamp=1000.0)
        result = upload_decision(record)
        assert result is False
        mock_s3.assert_not_called()

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    def test_upload_decision_rejects_test_prefix(self, mock_s3):
        record = _valid_record(obs_id="test_EURUSD_1000")
        result = upload_decision(record)
        assert result is False
        mock_s3.assert_not_called()

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    def test_upload_execution_rejects_stale_timestamp(self, mock_s3):
        record = _valid_record(timestamp=1000.0)
        result = upload_execution(record)
        assert result is False
        mock_s3.assert_not_called()

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    def test_upload_outcome_rejects_stale_timestamp(self, mock_s3):
        record = _valid_record(timestamp=1000.0)
        result = upload_outcome(record)
        assert result is False
        mock_s3.assert_not_called()

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    def test_valid_record_reaches_transport(self, mock_s3):
        """A fully valid record DOES reach _append_to_s3."""
        record = _valid_record()
        result = upload_decision(record)
        assert result is True
        mock_s3.assert_called_once()


# ═══════════════════════════════════════════════════════════════
# EXACT REPRODUCTION OF POLLUTION INCIDENT
# ═══════════════════════════════════════════════════════════════

class TestPollutionIncidentPrevention:
    """Reproduce the exact record that polluted v10-engine and prove it's now blocked."""

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    def test_exact_polluted_record_blocked(self, mock_s3):
        """The exact record found in s3://v10-engine/...decisions_test_EURUSD_1000.jsonl"""
        polluted_record = {
            "schema_version": "v10_decision_v1",
            "observation_id": "test_EURUSD_1000",
            "decision_id": "test_EURUSD_1000",
            "correlation_id": "cor_EURUSD_1000",
            "symbol": "EURUSD",
            "timestamp_utc": 1000.0,
            "engine_version": "V10",
            "final_action": "NO_TRADE",
            "market_state": {"regime": "RANGING", "h4_trend": "NEUTRAL"},
            "opportunity": {"state": "INVALID"},
            "risk_approved": False,
            "execution_approved": False,
            "lineage": {"engine": "V10"},
        }
        result = upload_decision(polluted_record)
        assert result is False, "Polluted record must be rejected"
        mock_s3.assert_not_called()

    def test_polluted_record_fails_timestamp(self):
        """timestamp=1000.0 is the first rejection reason."""
        polluted_record = _valid_record(timestamp=1000.0, obs_id="test_EURUSD_1000")
        assert _validate_for_upload(polluted_record, "decision") is False

    def test_polluted_record_fails_observation_id(self):
        """Even with valid timestamp, 'test_' prefix is rejected."""
        polluted_record = _valid_record(timestamp=1785475200.0, obs_id="test_EURUSD_1000")
        assert _validate_for_upload(polluted_record, "decision") is False



# ═══════════════════════════════════════════════════════════════
# CENTRAL VALIDATOR UNIT TESTS
# ═══════════════════════════════════════════════════════════════

class TestValidateObservationId:
    """Unit tests for the central validate_observation_id() function."""

    def test_valid_16_hex(self):
        valid, _ = validate_observation_id("7cb8c27c0d5d1234")
        assert valid is True

    def test_valid_12_chars(self):
        valid, _ = validate_observation_id("abcdef123456")
        assert valid is True

    def test_empty_rejected(self):
        valid, reason = validate_observation_id("")
        assert valid is False
        assert "empty" in reason

    def test_short_7_chars_rejected(self):
        valid, reason = validate_observation_id("no_boto")
        assert valid is False
        assert "too short" in reason

    def test_short_3_chars_rejected(self):
        valid, reason = validate_observation_id("abc")
        assert valid is False
        assert "too short" in reason

    def test_test_prefix_rejected(self):
        valid, reason = validate_observation_id("test_EURUSD_1000")
        assert valid is False
        assert "forbidden prefix" in reason

    def test_mock_prefix_rejected(self):
        valid, reason = validate_observation_id("MOCK_obs_12345")
        assert valid is False
        assert "forbidden prefix" in reason

    def test_numeric_only_rejected(self):
        valid, reason = validate_observation_id("1785475200000")
        assert valid is False
        assert "numeric" in reason

    def test_exactly_12_chars_accepted(self):
        valid, _ = validate_observation_id("aabbccdd1122")
        assert valid is True

    def test_11_chars_rejected(self):
        valid, reason = validate_observation_id("aabbccdd112")
        assert valid is False
        assert "too short" in reason


# ═══════════════════════════════════════════════════════════════
# EVENT OBSERVATION ID VALIDATION
# ═══════════════════════════════════════════════════════════════

class TestEventObservationIdValidation:
    """Events with invalid observation_ids must be rejected."""

    def _event(self, obs_id="a1b2c3d4e5f67890"):
        return {
            "schema_version": "v10_event_v1",
            "observation_id": obs_id,
            "symbol": "EURUSD",
            "timestamp_utc": 1785400000.0,
            "engine_version": "V10",
            "event_type": "V10_OPPORTUNITY_COMPLETE",
            "stage": "OPPORTUNITY",
            "status": "COMPLETE",
            "payload": {},
        }

    def test_valid_event_passes(self):
        assert _validate_event(self._event()) is True

    def test_short_obs_id_rejected(self):
        assert _validate_event(self._event(obs_id="no_boto")) is False

    def test_test_prefix_rejected(self):
        assert _validate_event(self._event(obs_id="test_event_data")) is False

    def test_numeric_obs_id_rejected(self):
        assert _validate_event(self._event(obs_id="1785400000000")) is False

    def test_empty_obs_id_rejected(self):
        assert _validate_event(self._event(obs_id="")) is False


# ═══════════════════════════════════════════════════════════════
# EXECUTION & OUTCOME OBSERVATION ID VALIDATION
# ═══════════════════════════════════════════════════════════════

class TestExecutionOutcomeObservationId:
    """Execution and outcome records must also validate observation_id."""

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    def test_execution_short_obs_id_rejected(self, mock_s3):
        record = _valid_record(obs_id="short")
        result = upload_execution(record)
        assert result is False
        mock_s3.assert_not_called()

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    def test_execution_valid_obs_id_passes(self, mock_s3):
        record = _valid_record()
        result = upload_execution(record)
        assert result is True
        mock_s3.assert_called_once()

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    def test_outcome_test_prefix_rejected(self, mock_s3):
        record = _valid_record(obs_id="test_outcome_abc")
        result = upload_outcome(record)
        assert result is False
        mock_s3.assert_not_called()

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    def test_outcome_valid_obs_id_passes(self, mock_s3):
        record = _valid_record()
        result = upload_outcome(record)
        assert result is True
        mock_s3.assert_called_once()

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    def test_execution_numeric_obs_id_rejected(self, mock_s3):
        record = _valid_record(obs_id="9999999999999")
        result = upload_execution(record)
        assert result is False
        mock_s3.assert_not_called()
