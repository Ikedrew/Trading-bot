"""
Unit tests for risk/guards.py fail-closed behaviour.

Tests verify that MT5 position lookup failures block trading conservatively.
Uses mocking to simulate MT5 disconnect scenarios.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestGuardsFailClosed:
    """Test that unknown exposure state blocks trading."""

    def test_positions_get_returns_none_blocks_trading(self):
        """Scenario A: positions_get() returns None ? guard returns high count."""
        with patch("risk.guards.mt5") as mock_mt5:
            mock_mt5.positions_get.return_value = None
            mock_mt5.last_error.return_value = (-1, "no connection")

            from risk.guards import count_bot_positions, _FAIL_CLOSED_POSITION_COUNT
            result = count_bot_positions("EURUSD", 713001)
            assert result == _FAIL_CLOSED_POSITION_COUNT, f"Expected {_FAIL_CLOSED_POSITION_COUNT}, got {result}"

    def test_positions_get_raises_exception_blocks_trading(self):
        """Scenario B: positions_get() raises ? guard returns high count."""
        with patch("risk.guards.mt5") as mock_mt5:
            mock_mt5.positions_get.side_effect = RuntimeError("MT5 terminal crashed")

            from risk.guards import count_bot_positions, _FAIL_CLOSED_POSITION_COUNT
            result = count_bot_positions("EURUSD", 713001)
            assert result == _FAIL_CLOSED_POSITION_COUNT, f"Expected {_FAIL_CLOSED_POSITION_COUNT}, got {result}"

    def test_empty_positions_returns_zero(self):
        """Scenario C: valid empty response ? returns 0 (no positions)."""
        with patch("risk.guards.mt5") as mock_mt5:
            mock_mt5.positions_get.return_value = ()

            from risk.guards import count_bot_positions
            result = count_bot_positions("EURUSD", 713001)
            assert result == 0, f"Expected 0, got {result}"

    def test_valid_positions_counted_correctly(self):
        """Scenario C: valid positions returned ? counts by magic."""
        pos1 = MagicMock()
        pos1.magic = 713001
        pos2 = MagicMock()
        pos2.magic = 713001
        pos3 = MagicMock()
        pos3.magic = 999999  # different magic

        with patch("risk.guards.mt5") as mock_mt5:
            mock_mt5.positions_get.return_value = (pos1, pos2, pos3)

            from risk.guards import count_bot_positions
            result = count_bot_positions("EURUSD", 713001)
            assert result == 2, f"Expected 2, got {result}"

    def test_valid_positions_different_magic_returns_zero(self):
        """Valid positions but none match our magic ? returns 0."""
        pos1 = MagicMock()
        pos1.magic = 999999

        with patch("risk.guards.mt5") as mock_mt5:
            mock_mt5.positions_get.return_value = (pos1,)

            from risk.guards import count_bot_positions
            result = count_bot_positions("EURUSD", 713001)
            assert result == 0, f"Expected 0, got {result}"


# --- RUNNER -------------------------------------------------------------------

if __name__ == "__main__":
    test_classes = [TestGuardsFailClosed]
    passed = 0
    failed = 0
    errors = []

    for cls in test_classes:
        instance = cls()
        for method_name in dir(instance):
            if not method_name.startswith("test_"):
                continue
            try:
                getattr(instance, method_name)()
                passed += 1
            except AssertionError as e:
                failed += 1
                errors.append(f"  FAIL {cls.__name__}.{method_name}: {e}")
            except Exception as e:
                failed += 1
                errors.append(f"  ERROR {cls.__name__}.{method_name}: {type(e).__name__}: {e}")

    print(f"\nGUARDS FAIL-SAFE TESTS: {passed} passed, {failed} failed")
    if errors:
        for e in errors:
            print(e)
    else:
        print("ALL PASS")
