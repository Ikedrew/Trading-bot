"""
Tests for I1: Emergency Kill Switch.

Covers:
- Flag file absent → trading allowed
- Flag file present → entries blocked
- Transition detection: ACTIVATED logged once
- Transition detection: DEACTIVATED logged once
- Fail-safe: file check error → block entries
- No repeated logging (same state → no log)
- Flag appears mid-run → immediate effect
- Flag removed mid-run → immediate recovery
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.kill_switch import (
    is_kill_switch_active,
    reset_kill_switch_state,
    _get_kill_switch_path,
)


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    """Reset kill switch state and use temp directory."""
    reset_kill_switch_state()
    yield tmp_path
    reset_kill_switch_state()


# ─── TEST: BASIC BEHAVIOUR ────────────────────────────────────────────────────

class TestBasicBehaviour:
    def test_no_flag_file_allows_trading(self, tmp_path):
        """When flag file does not exist, kill switch is inactive."""
        flag_path = str(tmp_path / "kill_switch.flag")
        with patch("core.kill_switch._get_kill_switch_path", return_value=flag_path):
            assert is_kill_switch_active() is False

    def test_flag_file_present_blocks_trading(self, tmp_path):
        """When flag file exists, kill switch is active."""
        flag_path = tmp_path / "kill_switch.flag"
        flag_path.write_text("HALT")
        with patch("core.kill_switch._get_kill_switch_path", return_value=str(flag_path)):
            assert is_kill_switch_active() is True

    def test_flag_removed_resumes_trading(self, tmp_path):
        """Removing flag file deactivates kill switch."""
        flag_path = tmp_path / "kill_switch.flag"
        flag_path.write_text("HALT")
        with patch("core.kill_switch._get_kill_switch_path", return_value=str(flag_path)):
            assert is_kill_switch_active() is True
            flag_path.unlink()
            assert is_kill_switch_active() is False


# ─── TEST: TRANSITION DETECTION ───────────────────────────────────────────────

class TestTransitions:
    def test_activation_detected(self, tmp_path, caplog):
        """FRESH→ACTIVE transition is logged."""
        flag_path = tmp_path / "kill_switch.flag"
        with patch("core.kill_switch._get_kill_switch_path", return_value=str(flag_path)):
            import logging
            with caplog.at_level(logging.INFO):
                # First check: not active (establishes baseline)
                is_kill_switch_active()
                # Create flag
                flag_path.write_text("HALT")
                # Second check: detects transition
                is_kill_switch_active()

            assert any("KILL_SWITCH_ACTIVATED" in r.message for r in caplog.records)

    def test_deactivation_detected(self, tmp_path, caplog):
        """ACTIVE→INACTIVE transition is logged."""
        flag_path = tmp_path / "kill_switch.flag"
        flag_path.write_text("HALT")
        with patch("core.kill_switch._get_kill_switch_path", return_value=str(flag_path)):
            import logging
            with caplog.at_level(logging.INFO):
                # First check: active (establishes baseline)
                is_kill_switch_active()
                # Remove flag
                flag_path.unlink()
                # Second check: detects recovery
                is_kill_switch_active()

            assert any("KILL_SWITCH_DEACTIVATED" in r.message for r in caplog.records)

    def test_no_repeated_logging_same_state(self, tmp_path, caplog):
        """Same state on consecutive checks does not produce transition logs."""
        flag_path = tmp_path / "kill_switch.flag"
        with patch("core.kill_switch._get_kill_switch_path", return_value=str(flag_path)):
            import logging
            with caplog.at_level(logging.INFO):
                # Multiple checks with flag absent
                is_kill_switch_active()
                is_kill_switch_active()
                is_kill_switch_active()

            transition_logs = [r for r in caplog.records
                             if "ACTIVATED" in r.message or "DEACTIVATED" in r.message]
            assert len(transition_logs) == 0


# ─── TEST: FAIL-SAFE ──────────────────────────────────────────────────────────

class TestFailSafe:
    def test_os_error_no_prior_state_blocks(self, caplog):
        """File system error with no prior state → fail-closed (block entries)."""
        with patch("os.path.exists", side_effect=OSError("Permission denied")):
            import logging
            with caplog.at_level(logging.WARNING):
                result = is_kill_switch_active()

            assert result is True
            assert any("FAILSAFE" in r.message for r in caplog.records)

    def test_os_error_with_prior_inactive_uses_last_state(self, tmp_path, caplog):
        """File system error after known-inactive state → uses last known (allows trading)."""
        flag_path = tmp_path / "kill_switch.flag"
        with patch("core.kill_switch._get_kill_switch_path", return_value=str(flag_path)):
            import logging
            # First call: establish prior state as inactive (file absent)
            result1 = is_kill_switch_active()
            assert result1 is False

            # Now simulate filesystem error
            with patch("os.path.exists", side_effect=OSError("Disk latency")):
                with caplog.at_level(logging.WARNING):
                    result2 = is_kill_switch_active()

            # Should use last known state (False = allow trading)
            assert result2 is False
            assert any("IO_ERROR" in r.message and "last_known_state" in r.message for r in caplog.records)

    def test_os_error_with_prior_active_stays_blocked(self, tmp_path, caplog):
        """File system error after known-active state → uses last known (keeps blocking)."""
        flag_path = tmp_path / "kill_switch.flag"
        flag_path.write_text("HALT")
        with patch("core.kill_switch._get_kill_switch_path", return_value=str(flag_path)):
            import logging
            # First call: establish prior state as active (file exists)
            result1 = is_kill_switch_active()
            assert result1 is True

            # Now simulate filesystem error
            with patch("os.path.exists", side_effect=OSError("Disk error")):
                with caplog.at_level(logging.WARNING):
                    result2 = is_kill_switch_active()

            # Should use last known state (True = keep blocking)
            assert result2 is True


# ─── TEST: IMMEDIATE EFFECT ───────────────────────────────────────────────────

class TestImmediateEffect:
    def test_flag_appears_blocks_immediately(self, tmp_path):
        """Flag appearing mid-run blocks on next check."""
        flag_path = tmp_path / "kill_switch.flag"
        with patch("core.kill_switch._get_kill_switch_path", return_value=str(flag_path)):
            assert is_kill_switch_active() is False
            # Operator creates flag
            flag_path.write_text("")
            # Very next check catches it
            assert is_kill_switch_active() is True

    def test_flag_removed_resumes_immediately(self, tmp_path):
        """Flag removal resumes on next check."""
        flag_path = tmp_path / "kill_switch.flag"
        flag_path.write_text("")
        with patch("core.kill_switch._get_kill_switch_path", return_value=str(flag_path)):
            assert is_kill_switch_active() is True
            # Operator removes flag
            flag_path.unlink()
            # Very next check resumes
            assert is_kill_switch_active() is False
