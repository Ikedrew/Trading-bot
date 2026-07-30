"""Phase J.7 — Production S3 Write Guard Tests.

Verifies that the _production_write_guard blocks all S3 writes unless
ENGINE_MODE=V10, LIVE_MODE=True, ALLOW_PRODUCTION_S3_WRITE=True.
"""

import pytest
from unittest.mock import patch, MagicMock
from core.v10.s3_writer import (
    _production_write_guard, _is_test_environment,
    upload_decision, upload_execution, upload_outcome, upload_events,
)


def _valid_record():
    """Record that passes all validation (symbol, timestamp, obs_id, schema)."""
    return {
        "schema_version": "v10_decision_v1",
        "observation_id": "a1b2c3d4e5f67890",
        "decision_id": "a1b2c3d4e5f67890",
        "correlation_id": "cor_a1b2c3d4e5f67890",
        "symbol": "EURUSD",
        "timestamp_utc": 1785475200.0,
        "engine_version": "V10",
        "final_action": "NO_TRADE",
        "market_state": {"regime": "RANGING"},
        "opportunity": {"state": "INVALID"},
        "risk_approved": False,
        "execution_approved": False,
        "lineage": {"engine": "V10"},
    }


def _valid_event():
    return {
        "schema_version": "v10_event_v1",
        "observation_id": "a1b2c3d4e5f67890",
        "symbol": "EURUSD",
        "timestamp_utc": 1785400000.0,
        "engine_version": "V10",
        "event_type": "V10_OPPORTUNITY_COMPLETE",
        "stage": "OPPORTUNITY",
        "status": "COMPLETE",
        "payload": {},
    }


# ═══════════════════════════════════════════════════════════════
# GUARD FUNCTION UNIT TESTS
# ═══════════════════════════════════════════════════════════════

class TestProductionWriteGuard:
    """Direct tests of _production_write_guard() with patched config attributes."""

    @patch("core.v10.s3_writer._is_test_environment", return_value=False)
    @patch("core.config.ENGINE_MODE", "V10")
    @patch("core.config.LIVE_MODE", True)
    @patch("core.config.ALLOW_PRODUCTION_S3_WRITE", True)
    def test_all_conditions_met_allowed(self, mock_env):
        allowed, reason = _production_write_guard()
        assert allowed is True
        assert reason == "V10_LIVE"

    def test_pytest_environment_always_blocked(self):
        """Under pytest, guard blocks even with all production flags True."""
        # No mocking of _is_test_environment — it sees real pytest
        allowed, reason = _production_write_guard()
        assert allowed is False
        assert "test_environment" in reason

    @patch("core.v10.s3_writer._is_test_environment", return_value=False)
    @patch("core.config.ENGINE_MODE", "LEGACY")
    @patch("core.config.LIVE_MODE", True)
    @patch("core.config.ALLOW_PRODUCTION_S3_WRITE", True)
    def test_legacy_mode_blocked(self, mock_env):
        allowed, reason = _production_write_guard()
        assert allowed is False
        assert "ENGINE_MODE=LEGACY" in reason

    @patch("core.v10.s3_writer._is_test_environment", return_value=False)
    @patch("core.config.ENGINE_MODE", "V10")
    @patch("core.config.LIVE_MODE", False)
    @patch("core.config.ALLOW_PRODUCTION_S3_WRITE", True)
    def test_live_mode_false_blocked(self, mock_env):
        allowed, reason = _production_write_guard()
        assert allowed is False
        assert "LIVE_MODE=False" in reason

    @patch("core.v10.s3_writer._is_test_environment", return_value=False)
    @patch("core.config.ENGINE_MODE", "V10")
    @patch("core.config.LIVE_MODE", True)
    @patch("core.config.ALLOW_PRODUCTION_S3_WRITE", False)
    def test_write_flag_false_blocked(self, mock_env):
        allowed, reason = _production_write_guard()
        assert allowed is False
        assert "ALLOW_PRODUCTION_S3_WRITE=False" in reason

    @patch("core.v10.s3_writer._is_test_environment", return_value=False)
    @patch("core.config.ENGINE_MODE", "LEGACY")
    @patch("core.config.LIVE_MODE", False)
    @patch("core.config.ALLOW_PRODUCTION_S3_WRITE", False)
    def test_all_false_blocked(self, mock_env):
        allowed, reason = _production_write_guard()
        assert allowed is False


# ═══════════════════════════════════════════════════════════════
# UPLOAD PATH GUARD INTEGRATION
# ═══════════════════════════════════════════════════════════════

class TestUploadBlockedByGuard:
    """When guard blocks, upload functions return False without calling S3."""

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    @patch("core.v10.s3_writer._production_write_guard", return_value=(False, "LIVE_MODE=False"))
    def test_decision_blocked(self, mock_guard, mock_s3):
        result = upload_decision(_valid_record())
        assert result is False
        mock_s3.assert_not_called()

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    @patch("core.v10.s3_writer._production_write_guard", return_value=(False, "LIVE_MODE=False"))
    def test_execution_blocked(self, mock_guard, mock_s3):
        result = upload_execution(_valid_record())
        assert result is False
        mock_s3.assert_not_called()

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    @patch("core.v10.s3_writer._production_write_guard", return_value=(False, "LIVE_MODE=False"))
    def test_outcome_blocked(self, mock_guard, mock_s3):
        result = upload_outcome(_valid_record())
        assert result is False
        mock_s3.assert_not_called()

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    @patch("core.v10.s3_writer._production_write_guard", return_value=(False, "LIVE_MODE=False"))
    def test_events_blocked(self, mock_guard, mock_s3):
        result = upload_events([_valid_event()])
        assert result is False
        mock_s3.assert_not_called()


class TestUploadAllowedByGuard:
    """When guard allows, upload functions proceed to validation and S3."""

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    @patch("core.v10.s3_writer._production_write_guard", return_value=(True, "V10_LIVE"))
    def test_decision_allowed(self, mock_guard, mock_s3):
        result = upload_decision(_valid_record())
        assert result is True
        mock_s3.assert_called_once()

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    @patch("core.v10.s3_writer._production_write_guard", return_value=(True, "V10_LIVE"))
    def test_execution_allowed(self, mock_guard, mock_s3):
        result = upload_execution(_valid_record())
        assert result is True
        mock_s3.assert_called_once()

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    @patch("core.v10.s3_writer._production_write_guard", return_value=(True, "V10_LIVE"))
    def test_outcome_allowed(self, mock_guard, mock_s3):
        result = upload_outcome(_valid_record())
        assert result is True
        mock_s3.assert_called_once()


class TestGuardBeforeValidation:
    """Guard must fire BEFORE validation — even valid records are blocked in non-production."""

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    @patch("core.v10.s3_writer._production_write_guard", return_value=(False, "ENGINE_MODE=LEGACY"))
    def test_valid_record_still_blocked_in_legacy_mode(self, mock_guard, mock_s3):
        """A perfectly valid record is still blocked when guard fails."""
        result = upload_decision(_valid_record())
        assert result is False
        mock_s3.assert_not_called()

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    @patch("core.v10.s3_writer._production_write_guard", return_value=(False, "ALLOW_PRODUCTION_S3_WRITE=False"))
    def test_valid_record_blocked_when_flag_disabled(self, mock_guard, mock_s3):
        result = upload_decision(_valid_record())
        assert result is False
        mock_s3.assert_not_called()


class TestReplayModeBlocked:
    """Simulate replay/test environment where LIVE_MODE=False."""

    @patch("core.v10.s3_writer._append_to_s3", return_value=True)
    @patch("core.v10.s3_writer._is_test_environment", return_value=False)
    @patch("core.config.ENGINE_MODE", "V10")
    @patch("core.config.LIVE_MODE", False)
    @patch("core.config.ALLOW_PRODUCTION_S3_WRITE", True)
    def test_replay_mode_blocks_all_uploads(self, mock_env, mock_s3):
        """With LIVE_MODE=False in config, all uploads blocked."""
        r1 = upload_decision(_valid_record())
        r2 = upload_execution(_valid_record())
        r3 = upload_outcome(_valid_record())
        r4 = upload_events([_valid_event()])
        assert r1 is False
        assert r2 is False
        assert r3 is False
        assert r4 is False
        mock_s3.assert_not_called()

    def test_pytest_blocks_all_uploads_even_with_production_config(self):
        """Under pytest (real), all uploads blocked regardless of config."""
        # Don't mock _is_test_environment — pytest IS detected
        r1 = upload_decision(_valid_record())
        r2 = upload_execution(_valid_record())
        r3 = upload_outcome(_valid_record())
        r4 = upload_events([_valid_event()])
        assert r1 is False
        assert r2 is False
        assert r3 is False
        assert r4 is False



# ═══════════════════════════════════════════════════════════════
# TEST ENVIRONMENT DETECTION
# ═══════════════════════════════════════════════════════════════

class TestIsTestEnvironment:
    """Verify _is_test_environment() detects test/dev scenarios."""

    def test_detects_pytest(self):
        """When running under pytest, must return True."""
        assert _is_test_environment() is True

    def test_detects_pytest_env_var(self):
        """PYTEST_CURRENT_TEST env var triggers detection."""
        import os
        # Under pytest this is already set, but verify explicitly
        os.environ["PYTEST_CURRENT_TEST"] = "tests/test_foo.py::test_bar"
        try:
            assert _is_test_environment() is True
        finally:
            if "PYTEST_CURRENT_TEST" in os.environ:
                del os.environ["PYTEST_CURRENT_TEST"]

    def test_guard_blocks_under_pytest(self):
        """The production write guard ALWAYS blocks under pytest."""
        allowed, reason = _production_write_guard()
        assert allowed is False
        assert "test_environment" in reason
