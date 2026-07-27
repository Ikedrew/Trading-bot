"""
Tests for post-fill broker-side SL/TP protection verification.

Covers:
    Case 1: Order executes and SL/TP exist → protection_status = VERIFIED
    Case 2: Order executes but SL missing → warning/error triggered, correction attempted
    Case 3: Broker SL differs from requested → mismatch detected
    Case 4: Position recovery after restart → protection state verified
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.protection_verification import (
    verify_protection,
    ProtectionStatus,
    ProtectionVerificationResult,
    _query_broker_position,
    _values_match,
    _attempt_correction,
)


# ═══════════════════════════════════════════════════════════════════════════════
# MOCK HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

class MockPosition:
    """Mock MT5 position object returned by positions_get."""

    def __init__(self, ticket: int, sl: float, tp: float, symbol: str = "EURUSD"):
        self.ticket = ticket
        self.sl = sl
        self.tp = tp
        self.symbol = symbol


class MockExecutionResult:
    """Mock execution result from position_modify_sl_tp."""

    def __init__(self, ok: bool, retcode: int = 0, comment: str = ""):
        self.ok = ok
        self.retcode = retcode
        self.comment = comment
        self.deal = 0
        self.order = 0


def _mock_execution_module(modify_ok: bool = True) -> MagicMock:
    """Create a mock execution module with position_modify_sl_tp."""
    module = MagicMock()
    module.position_modify_sl_tp.return_value = MockExecutionResult(
        ok=modify_ok,
        retcode=10009 if modify_ok else 10016,
        comment="done" if modify_ok else "invalid_stops",
    )
    return module


# ═══════════════════════════════════════════════════════════════════════════════
# CASE 1: Order executes and SL/TP exist → VERIFIED
# ═══════════════════════════════════════════════════════════════════════════════

class TestProtectionVerified:
    """When broker confirms SL/TP match requested values."""

    @patch("core.protection_verification.mt5_call")
    @patch("core.protection_verification._persist_result")
    def test_sl_tp_verified_exact_match(self, mock_persist, mock_mt5_call):
        """SL and TP exactly match requested values → VERIFIED."""
        mock_mt5_call.return_value = [MockPosition(ticket=12345, sl=1.10500, tp=1.11000)]

        result = verify_protection(
            symbol="EURUSD",
            position_ticket=12345,
            requested_sl=1.10500,
            requested_tp=1.11000,
            correlation_id="COR-TEST-001",
        )

        assert result.protection_status == ProtectionStatus.VERIFIED.value
        assert result.broker_confirmed_sl == 1.10500
        assert result.broker_confirmed_tp == 1.11000
        assert result.requested_sl == 1.10500
        assert result.requested_tp == 1.11000
        assert result.correction_attempted is False
        assert result.protection_failure_reason == ""
        mock_persist.assert_called_once()

    @patch("core.protection_verification.mt5_call")
    @patch("core.protection_verification._persist_result")
    def test_sl_tp_verified_within_tolerance(self, mock_persist, mock_mt5_call):
        """SL/TP within floating-point tolerance → VERIFIED."""
        # Tiny float difference that should still count as matching
        mock_mt5_call.return_value = [MockPosition(
            ticket=12345,
            sl=1.1050000000001,  # epsilon difference
            tp=1.1099999999999,
        )]

        result = verify_protection(
            symbol="EURUSD",
            position_ticket=12345,
            requested_sl=1.10500,
            requested_tp=1.11000,
            correlation_id="COR-TEST-002",
        )

        assert result.protection_status == ProtectionStatus.VERIFIED.value

    @patch("core.protection_verification.mt5_call")
    @patch("core.protection_verification._persist_result")
    def test_no_sl_requested_no_sl_present_is_verified(self, mock_persist, mock_mt5_call):
        """If no SL was requested (0.0) and broker has none → VERIFIED."""
        mock_mt5_call.return_value = [MockPosition(ticket=12345, sl=0.0, tp=1.11000)]

        result = verify_protection(
            symbol="EURUSD",
            position_ticket=12345,
            requested_sl=0.0,
            requested_tp=1.11000,
        )

        assert result.protection_status == ProtectionStatus.VERIFIED.value


# ═══════════════════════════════════════════════════════════════════════════════
# CASE 2: Order executes but SL missing → correction attempted
# ═══════════════════════════════════════════════════════════════════════════════

class TestProtectionMissing:
    """When broker has no SL/TP despite one being requested."""

    @patch("core.protection_verification.mt5_call")
    @patch("core.protection_verification._persist_result")
    def test_sl_missing_correction_succeeds(self, mock_persist, mock_mt5_call):
        """SL missing on broker, correction succeeds → CORRECTED."""
        # First call: SL missing
        # Second call (re-verify after correction): SL present
        mock_mt5_call.side_effect = [
            [MockPosition(ticket=12345, sl=0.0, tp=1.11000)],  # Initial check
            [MockPosition(ticket=12345, sl=1.10500, tp=1.11000)],  # Re-verify
        ]

        execution = _mock_execution_module(modify_ok=True)

        result = verify_protection(
            symbol="EURUSD",
            position_ticket=12345,
            requested_sl=1.10500,
            requested_tp=1.11000,
            correlation_id="COR-TEST-003",
            execution_module=execution,
        )

        assert result.protection_status == ProtectionStatus.CORRECTED.value
        assert result.correction_attempted is True
        assert result.correction_success is True
        execution.position_modify_sl_tp.assert_called_once_with(
            symbol="EURUSD",
            position_ticket=12345,
            sl=1.10500,
            tp=1.11000,
        )

    @patch("core.protection_verification.mt5_call")
    @patch("core.protection_verification._persist_result")
    @patch("core.protection_verification._emit_discord_alert")
    def test_sl_missing_correction_fails(self, mock_discord, mock_persist, mock_mt5_call):
        """SL missing on broker, correction fails → FAILED_UNPROTECTED."""
        mock_mt5_call.return_value = [MockPosition(ticket=12345, sl=0.0, tp=1.11000)]

        execution = _mock_execution_module(modify_ok=False)

        result = verify_protection(
            symbol="EURUSD",
            position_ticket=12345,
            requested_sl=1.10500,
            requested_tp=1.11000,
            correlation_id="COR-TEST-004",
            execution_module=execution,
        )

        assert result.protection_status == ProtectionStatus.FAILED_UNPROTECTED.value
        assert result.correction_attempted is True
        assert result.correction_success is False
        assert "missing" in result.protection_failure_reason.lower() or "broker_sl=0.0" in result.protection_failure_reason
        # Discord alert emitted for critical failure
        mock_discord.assert_called_once()

    @patch("core.protection_verification.mt5_call")
    @patch("core.protection_verification._persist_result")
    def test_tp_missing_correction_succeeds(self, mock_persist, mock_mt5_call):
        """TP missing on broker, correction succeeds → CORRECTED."""
        mock_mt5_call.side_effect = [
            [MockPosition(ticket=12345, sl=1.10500, tp=0.0)],  # TP missing
            [MockPosition(ticket=12345, sl=1.10500, tp=1.11000)],  # After correction
        ]

        execution = _mock_execution_module(modify_ok=True)

        result = verify_protection(
            symbol="EURUSD",
            position_ticket=12345,
            requested_sl=1.10500,
            requested_tp=1.11000,
            correlation_id="COR-TEST-005",
            execution_module=execution,
        )

        assert result.protection_status == ProtectionStatus.CORRECTED.value
        assert result.correction_success is True

    @patch("core.protection_verification.mt5_call")
    @patch("core.protection_verification._persist_result")
    def test_both_sl_tp_missing(self, mock_persist, mock_mt5_call):
        """Both SL and TP missing → correction attempted."""
        mock_mt5_call.side_effect = [
            [MockPosition(ticket=12345, sl=0.0, tp=0.0)],
            [MockPosition(ticket=12345, sl=1.10500, tp=1.11000)],
        ]

        execution = _mock_execution_module(modify_ok=True)

        result = verify_protection(
            symbol="EURUSD",
            position_ticket=12345,
            requested_sl=1.10500,
            requested_tp=1.11000,
            execution_module=execution,
        )

        assert result.protection_status == ProtectionStatus.CORRECTED.value
        assert result.correction_attempted is True

    @patch("core.protection_verification.mt5_call")
    @patch("core.protection_verification._persist_result")
    def test_no_execution_module_cannot_correct(self, mock_persist, mock_mt5_call):
        """SL missing but no execution module available → FAILED_UNPROTECTED."""
        mock_mt5_call.return_value = [MockPosition(ticket=12345, sl=0.0, tp=1.11000)]

        result = verify_protection(
            symbol="EURUSD",
            position_ticket=12345,
            requested_sl=1.10500,
            requested_tp=1.11000,
            execution_module=None,
        )

        assert result.protection_status == ProtectionStatus.FAILED_UNPROTECTED.value
        assert result.correction_attempted is True
        assert result.correction_success is False


# ═══════════════════════════════════════════════════════════════════════════════
# CASE 3: Broker SL differs from requested → mismatch detected
# ═══════════════════════════════════════════════════════════════════════════════

class TestProtectionMismatch:
    """When broker has SL/TP but they differ from requested values."""

    @patch("core.protection_verification.mt5_call")
    @patch("core.protection_verification._persist_result")
    def test_sl_mismatch_correction_succeeds(self, mock_persist, mock_mt5_call):
        """Broker SL differs significantly from requested, correction works → MISMATCH_CORRECTED."""
        mock_mt5_call.side_effect = [
            [MockPosition(ticket=12345, sl=1.10200, tp=1.11000)],  # Wrong SL
            [MockPosition(ticket=12345, sl=1.10500, tp=1.11000)],  # After correction
        ]

        execution = _mock_execution_module(modify_ok=True)

        result = verify_protection(
            symbol="EURUSD",
            position_ticket=12345,
            requested_sl=1.10500,
            requested_tp=1.11000,
            execution_module=execution,
        )

        assert result.protection_status == ProtectionStatus.MISMATCH_CORRECTED.value
        assert result.broker_confirmed_sl == 1.10500  # After correction
        assert result.correction_attempted is True
        assert result.correction_success is True

    @patch("core.protection_verification.mt5_call")
    @patch("core.protection_verification._persist_result")
    def test_tp_mismatch_correction_fails(self, mock_persist, mock_mt5_call):
        """Broker TP differs, correction fails → FAILED_MISMATCH."""
        mock_mt5_call.return_value = [MockPosition(ticket=12345, sl=1.10500, tp=1.10800)]

        execution = _mock_execution_module(modify_ok=False)

        result = verify_protection(
            symbol="EURUSD",
            position_ticket=12345,
            requested_sl=1.10500,
            requested_tp=1.11000,
            execution_module=execution,
        )

        assert result.protection_status == ProtectionStatus.FAILED_MISMATCH.value
        assert result.correction_attempted is True
        assert result.correction_success is False
        assert "mismatch" in result.protection_failure_reason.lower()

    @patch("core.protection_verification.mt5_call")
    @patch("core.protection_verification._persist_result")
    def test_sl_slightly_different_due_to_broker_rounding(self, mock_persist, mock_mt5_call):
        """If broker rounds SL to different precision but within tolerance → VERIFIED."""
        # 1e-7 difference should be within tolerance (1e-6)
        mock_mt5_call.return_value = [MockPosition(
            ticket=12345, sl=1.105000001, tp=1.11000,
        )]

        result = verify_protection(
            symbol="EURUSD",
            position_ticket=12345,
            requested_sl=1.10500,
            requested_tp=1.11000,
        )

        # Within tolerance should still be VERIFIED
        assert result.protection_status == ProtectionStatus.VERIFIED.value


# ═══════════════════════════════════════════════════════════════════════════════
# CASE 4: Position recovery after restart → protection state verified
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecoveryProtection:
    """Protection verification during startup recovery scenarios."""

    @patch("core.protection_verification.mt5_call")
    @patch("core.protection_verification._persist_result")
    def test_position_not_found_after_fill(self, mock_persist, mock_mt5_call):
        """Position ticket not found on broker → POSITION_NOT_FOUND."""
        mock_mt5_call.side_effect = [
            None,  # First attempt: None
            None,  # Second attempt: None
            None,  # Third attempt: None
            None,  # Fallback by symbol: None
        ]

        result = verify_protection(
            symbol="EURUSD",
            position_ticket=99999,
            requested_sl=1.10500,
            requested_tp=1.11000,
        )

        assert result.protection_status == ProtectionStatus.POSITION_NOT_FOUND.value
        assert "not found" in result.protection_failure_reason.lower()

    @patch("core.protection_verification.mt5_call")
    @patch("core.protection_verification._persist_result")
    def test_position_found_on_retry(self, mock_persist, mock_mt5_call):
        """Position not found on first attempt but appears on retry → VERIFIED."""
        mock_mt5_call.side_effect = [
            None,  # First attempt: not found
            [MockPosition(ticket=12345, sl=1.10500, tp=1.11000)],  # Second attempt: found
        ]

        result = verify_protection(
            symbol="EURUSD",
            position_ticket=12345,
            requested_sl=1.10500,
            requested_tp=1.11000,
        )

        assert result.protection_status == ProtectionStatus.VERIFIED.value
        assert result.attempts == 2

    @patch("core.protection_verification.mt5_call")
    @patch("core.protection_verification._persist_result")
    def test_recovered_position_with_protection(self, mock_persist, mock_mt5_call):
        """Recovered position already has SL/TP on broker → VERIFIED."""
        mock_mt5_call.return_value = [MockPosition(ticket=55555, sl=163.166, tp=163.194)]

        result = verify_protection(
            symbol="USDJPY",
            position_ticket=55555,
            requested_sl=163.166,
            requested_tp=163.194,
            correlation_id="RECOVERY-55555",
        )

        assert result.protection_status == ProtectionStatus.VERIFIED.value
        assert result.correlation_id == "RECOVERY-55555"

    @patch("core.protection_verification.mt5_call")
    @patch("core.protection_verification._persist_result")
    def test_verification_error_on_exception(self, mock_persist, mock_mt5_call):
        """If MT5 raises an exception during verification → VERIFICATION_ERROR."""
        mock_mt5_call.side_effect = Exception("MT5 connection lost")

        result = verify_protection(
            symbol="EURUSD",
            position_ticket=12345,
            requested_sl=1.10500,
            requested_tp=1.11000,
        )

        assert result.protection_status == ProtectionStatus.VERIFICATION_ERROR.value
        assert "exception" in result.protection_failure_reason.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT TESTS FOR INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

class TestValueMatching:
    """Test the _values_match helper."""

    def test_exact_match(self):
        assert _values_match(1.10500, 1.10500, 1e-6) is True

    def test_within_tolerance(self):
        assert _values_match(1.10500, 1.1050005, 1e-6) is True

    def test_outside_tolerance(self):
        assert _values_match(1.10500, 1.10510, 1e-6) is False

    def test_zero_expected_always_matches(self):
        """If no protection was requested (0.0), any actual value is OK."""
        assert _values_match(0.0, 0.0, 1e-6) is True
        assert _values_match(1.5, 0.0, 1e-6) is True

    def test_zero_actual_nonzero_expected_fails(self):
        """If protection was requested but broker has 0.0 → mismatch."""
        assert _values_match(0.0, 1.10500, 1e-6) is False


class TestAttemptCorrection:
    """Test the _attempt_correction helper."""

    def test_correction_with_no_module(self):
        """No execution module → cannot correct."""
        ok = _attempt_correction(
            symbol="EURUSD",
            position_ticket=12345,
            target_sl=1.10500,
            target_tp=1.11000,
            execution_module=None,
        )
        assert ok is False

    def test_correction_success(self):
        """Execution module returns ok=True → correction successful."""
        execution = _mock_execution_module(modify_ok=True)
        ok = _attempt_correction(
            symbol="EURUSD",
            position_ticket=12345,
            target_sl=1.10500,
            target_tp=1.11000,
            execution_module=execution,
        )
        assert ok is True
        execution.position_modify_sl_tp.assert_called_once()

    def test_correction_failure(self):
        """Execution module returns ok=False → correction failed."""
        execution = _mock_execution_module(modify_ok=False)
        ok = _attempt_correction(
            symbol="EURUSD",
            position_ticket=12345,
            target_sl=1.10500,
            target_tp=1.11000,
            execution_module=execution,
        )
        assert ok is False

    def test_correction_exception_returns_false(self):
        """If execution module raises → returns False safely."""
        execution = MagicMock()
        execution.position_modify_sl_tp.side_effect = RuntimeError("connection lost")
        ok = _attempt_correction(
            symbol="EURUSD",
            position_ticket=12345,
            target_sl=1.10500,
            target_tp=1.11000,
            execution_module=execution,
        )
        assert ok is False


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION: PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════

class TestProtectionPersistence:
    """Test that results are persisted to JSONL."""

    @patch("core.protection_verification.mt5_call")
    def test_result_persisted_to_file(self, mock_mt5_call, tmp_path):
        """Verification result is written to JSONL file."""
        mock_mt5_call.return_value = [MockPosition(ticket=12345, sl=1.10500, tp=1.11000)]

        # Patch the _LOCAL_DIR to use tmp_path
        with patch("core.protection_verification._LOCAL_DIR", str(tmp_path / "protection_audit")):
            result = verify_protection(
                symbol="EURUSD",
                position_ticket=12345,
                requested_sl=1.10500,
                requested_tp=1.11000,
            )

        # Check file was created
        audit_dir = tmp_path / "protection_audit" / "EURUSD"
        files = list(audit_dir.glob("*.jsonl"))
        assert len(files) == 1

        # Check content is valid JSON
        with open(files[0]) as f:
            content = f.read().strip()
            record = json.loads(content)

        assert record["symbol"] == "EURUSD"
        assert record["position_ticket"] == 12345
        assert record["protection_status"] == "VERIFIED"
        assert record["broker_confirmed_sl"] == 1.10500
        assert record["broker_confirmed_tp"] == 1.11000
