"""
Tests for B4: Position Ownership Validation.

Covers:
- Matching magic ? allowed
- Mismatched magic ? blocked (strict mode)
- Mismatched magic ? warned (non-strict mode)
- Modify SL blocked for foreign positions
- Close blocked for foreign positions
- Partial close blocked for foreign positions
- Startup scan detects foreign positions
- filter_owned_positions works correctly
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.position_ownership import (
    validate_position_ownership,
    enforce_position_ownership,
    filter_owned_positions,
    scan_foreign_positions,
    validate_ownership_config,
    REJECT_OWNERSHIP_VIOLATION,
)


# --- FIXTURES -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def default_config():
    """Set known magic number."""
    with patch("core.position_ownership._get_expected_magic", return_value=713001), \
         patch("core.position_ownership._is_strict", return_value=True):
        yield


# --- TEST: MATCHING MAGIC ------------------------------------------------------

class TestMatchingMagic:
    def test_matching_returns_true(self, default_config):
        """Position with matching magic ? ownership valid."""
        assert validate_position_ownership(713001, 713001) is True

    def test_enforce_matching_allows(self, default_config):
        """enforce with matching magic ? allowed (returns True)."""
        result = enforce_position_ownership(
            position_magic=713001,
            action="CLOSE",
            symbol="EURUSD",
            ticket=12345,
            expected_magic=713001,
        )
        assert result is True


# --- TEST: MISMATCHED MAGIC — STRICT MODE --------------------------------------

class TestMismatchStrict:
    def test_mismatched_returns_false(self, default_config):
        """Position with different magic ? ownership invalid."""
        assert validate_position_ownership(713002, 713001) is False

    def test_enforce_mismatched_blocks(self, default_config):
        """enforce with mismatched magic (strict) ? blocked."""
        result = enforce_position_ownership(
            position_magic=713002,
            action="CLOSE",
            symbol="EURUSD",
            ticket=99999,
            expected_magic=713001,
        )
        assert result is False

    def test_modify_sl_blocked(self, default_config):
        """MODIFY_SL_TP on foreign position ? blocked."""
        result = enforce_position_ownership(
            position_magic=713003,
            action="MODIFY_SL_TP",
            symbol="GBPUSD",
            ticket=55555,
        )
        assert result is False

    def test_close_blocked(self, default_config):
        """CLOSE on foreign position ? blocked."""
        result = enforce_position_ownership(
            position_magic=713002,
            action="CLOSE",
            symbol="AUDUSD",
            ticket=44444,
        )
        assert result is False

    def test_partial_close_blocked(self, default_config):
        """PARTIAL_CLOSE on foreign position ? blocked."""
        result = enforce_position_ownership(
            position_magic=999999,
            action="PARTIAL_CLOSE",
            symbol="USDJPY",
            ticket=33333,
        )
        assert result is False


# --- TEST: MISMATCHED MAGIC — NON-STRICT MODE ---------------------------------

class TestMismatchNonStrict:
    def test_non_strict_allows_with_warning(self, default_config):
        """Non-strict mode: mismatched magic ? allowed (warning only)."""
        with patch("core.position_ownership._is_strict", return_value=False):
            result = enforce_position_ownership(
                position_magic=713002,
                action="CLOSE",
                symbol="EURUSD",
                ticket=12345,
            )
        assert result is True  # Allowed in non-strict


# --- TEST: FILTER OWNED POSITIONS ---------------------------------------------

class TestFilterOwned:
    def test_filters_correctly(self, default_config):
        """Only positions matching magic are returned."""
        positions = [
            MagicMock(magic=713001, symbol="EURUSD"),
            MagicMock(magic=713002, symbol="GBPUSD"),
            MagicMock(magic=713001, symbol="AUDUSD"),
            MagicMock(magic=713003, symbol="USDJPY"),
        ]

        owned = filter_owned_positions(positions, expected_magic=713001)

        assert len(owned) == 2
        assert all(p.magic == 713001 for p in owned)

    def test_empty_list_returns_empty(self, default_config):
        """Empty input ? empty output."""
        owned = filter_owned_positions([], expected_magic=713001)
        assert owned == []

    def test_all_foreign_returns_empty(self, default_config):
        """All foreign ? empty list."""
        positions = [
            MagicMock(magic=713002),
            MagicMock(magic=713003),
        ]
        owned = filter_owned_positions(positions, expected_magic=713001)
        assert owned == []


# --- TEST: STARTUP SCAN -------------------------------------------------------

class TestStartupScan:
    def test_detects_foreign_positions(self, default_config):
        """Scan finds positions not belonging to our magic."""
        all_pos = [
            MagicMock(ticket=100, symbol="EURUSD", magic=713001, volume=0.01),
            MagicMock(ticket=200, symbol="GBPUSD", magic=713002, volume=0.02),
            MagicMock(ticket=300, symbol="AUDUSD", magic=713003, volume=0.01),
        ]

        with patch("core.mt5_timeout.mt5_call", return_value=all_pos):
            foreign = scan_foreign_positions()

        assert len(foreign) == 2
        assert foreign[0]["magic"] == 713002
        assert foreign[1]["magic"] == 713003

    def test_no_foreign_returns_empty(self, default_config):
        """All positions ours ? empty list."""
        all_pos = [
            MagicMock(ticket=100, symbol="EURUSD", magic=713001, volume=0.01),
        ]

        with patch("core.mt5_timeout.mt5_call", return_value=all_pos):
            foreign = scan_foreign_positions()

        assert foreign == []

    def test_empty_account_returns_empty(self, default_config):
        """No positions at all ? empty list."""
        with patch("core.mt5_timeout.mt5_call", return_value=()):
            foreign = scan_foreign_positions()

        assert foreign == []


# --- TEST: CONFIG VALIDATION --------------------------------------------------

class TestConfigValidation:
    def test_valid_config(self, default_config):
        """Valid magic passes."""
        errors = validate_ownership_config()
        assert errors == []

    def test_zero_magic_errors(self, default_config):
        """Zero magic generates error."""
        with patch("core.position_ownership._get_expected_magic", return_value=0):
            errors = validate_ownership_config()
        assert any("BOT_MAGIC" in e for e in errors)


# --- TEST: PRODUCTION INTEGRATION ---------------------------------------------

class TestProductionIntegration:
    def test_ownership_in_modify_sl_tp(self):
        """Ownership check exists in position_modify_sl_tp."""
        import inspect
        from execution.mt5_execution import MT5Execution
        source = inspect.getsource(MT5Execution.position_modify_sl_tp)
        assert "enforce_position_ownership" in source

    def test_ownership_in_close_position(self):
        """Ownership check exists in close_position."""
        import inspect
        from execution.mt5_execution import MT5Execution
        source = inspect.getsource(MT5Execution.close_position)
        assert "enforce_position_ownership" in source
