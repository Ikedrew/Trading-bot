"""
Tests for A3: Correlation Guard — Currency Exposure + Pair Clustering.

Covers:
- Currency exposure calculation
- Per-currency limit blocking
- Correlation group blocking
- Opposite direction offset
- Guard disabled ? always allows
- Unknown symbol handled gracefully
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from risk.correlation_guard import (
    check_correlation,
    compute_currency_exposure,
    CorrelationGuardResult,
    _decompose_symbol,
)
from strategy.signals import Side


def _pos(symbol="EURUSD", side=Side.BUY, volume=0.01):
    """Create a mock position."""
    p = MagicMock()
    p.symbol = symbol
    p.side = side
    p.volume = volume
    return p


@pytest.fixture(autouse=True)
def default_config():
    with patch("risk.correlation_guard._is_enabled", return_value=True), \
         patch("risk.correlation_guard._get_max_currency_exposure", return_value=0.03), \
         patch("risk.correlation_guard._get_max_group_positions", return_value=2), \
         patch("risk.correlation_guard._get_correlation_groups", return_value=[
             ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"],
             ["USDJPY", "USDCHF", "USDCAD"],
         ]):
        yield


class TestCurrencyExposure:
    def test_single_buy_exposure(self):
        """Single BUY EURUSD ? +EUR, -USD."""
        positions = [_pos("EURUSD", Side.BUY, 0.01)]
        exp = compute_currency_exposure(positions)
        assert exp["EUR"] == pytest.approx(0.01)
        assert exp["USD"] == pytest.approx(-0.01)

    def test_single_sell_exposure(self):
        """Single SELL EURUSD ? -EUR, +USD."""
        positions = [_pos("EURUSD", Side.SELL, 0.01)]
        exp = compute_currency_exposure(positions)
        assert exp["EUR"] == pytest.approx(-0.01)
        assert exp["USD"] == pytest.approx(0.01)

    def test_stacked_usd_short(self):
        """Multiple USD-short positions stack exposure."""
        positions = [
            _pos("EURUSD", Side.BUY, 0.01),
            _pos("GBPUSD", Side.BUY, 0.01),
            _pos("AUDUSD", Side.BUY, 0.01),
        ]
        exp = compute_currency_exposure(positions)
        assert exp["USD"] == pytest.approx(-0.03)

    def test_opposite_directions_offset(self):
        """BUY EURUSD + SELL GBPUSD ? USD partially offsets."""
        positions = [
            _pos("EURUSD", Side.BUY, 0.01),   # USD -0.01
            _pos("GBPUSD", Side.SELL, 0.01),  # USD +0.01
        ]
        exp = compute_currency_exposure(positions)
        assert exp["USD"] == pytest.approx(0.0)


class TestCurrencyLimitBlock:
    def test_exceeds_limit_blocked(self):
        """3rd USD-short trade would exceed 0.03 limit ? blocked."""
        positions = [
            _pos("EURUSD", Side.BUY, 0.01),
            _pos("GBPUSD", Side.BUY, 0.01),
        ]
        # Current USD exposure = -0.02. Adding AUDUSD BUY 0.01 ? -0.03
        # Proposed would make USD = -0.03. With 0.02 existing + 0.01 proposed:
        # abs(-0.03) = 0.03 which is NOT > 0.03 (equal, not exceeded)
        # Need to push over: use 0.02 volume
        result = check_correlation(
            symbol="AUDUSD", direction="BUY", volume=0.02,
            open_positions=positions,
        )
        # -0.02 existing + -0.02 proposed = -0.04 > 0.03 limit
        assert result.allowed is False
        assert "CURRENCY_LIMIT" in result.reason
        assert "USD" in result.reason

    def test_within_limit_allowed(self):
        """Single trade within limit ? allowed."""
        positions = [_pos("EURUSD", Side.BUY, 0.01)]
        result = check_correlation(
            symbol="GBPUSD", direction="BUY", volume=0.01,
            open_positions=positions,
        )
        # USD would be -0.02 (within 0.03 limit)
        assert result.allowed is True


class TestGroupBlock:
    def test_group_limit_reached_blocks(self):
        """2 positions in same group ? 3rd blocked."""
        positions = [
            _pos("EURUSD", Side.BUY, 0.01),
            _pos("GBPUSD", Side.BUY, 0.01),
        ]
        result = check_correlation(
            symbol="AUDUSD", direction="BUY", volume=0.01,
            open_positions=positions,
        )
        # Group has 2 open (EURUSD + GBPUSD), limit is 2 ? blocked
        assert result.allowed is False
        assert "GROUP_LIMIT" in result.reason

    def test_different_group_allowed(self):
        """Positions in different group ? no group block."""
        positions = [
            _pos("EURUSD", Side.BUY, 0.01),
            _pos("GBPUSD", Side.BUY, 0.01),
        ]
        # USDJPY is in a different group
        result = check_correlation(
            symbol="USDJPY", direction="BUY", volume=0.01,
            open_positions=positions,
        )
        # Group check passes (USDJPY group has 0 open)
        # Currency check: USD would get +0.01 (from USDJPY BUY base=USD)
        # Net USD: -0.02 (from EUR+GBP) + 0.01 (from JPY trade) = -0.01 ? fine
        assert result.allowed is True


class TestDisabled:
    def test_disabled_always_allows(self):
        """Guard disabled ? all trades allowed."""
        with patch("risk.correlation_guard._is_enabled", return_value=False):
            positions = [_pos("EURUSD", Side.BUY, 0.10)]
            result = check_correlation(
                symbol="GBPUSD", direction="BUY", volume=0.10,
                open_positions=positions,
            )
            assert result.allowed is True


class TestEdgeCases:
    def test_no_open_positions_allowed(self):
        """No open positions ? always allowed."""
        result = check_correlation(
            symbol="EURUSD", direction="BUY", volume=0.01,
            open_positions=[],
        )
        assert result.allowed is True

    def test_unknown_symbol_allowed(self):
        """Unknown symbol (can't decompose) ? allowed (no block)."""
        result = check_correlation(
            symbol="UNKNOWN_XYZ", direction="BUY", volume=0.01,
            open_positions=[],
        )
        assert result.allowed is True

    def test_symbol_decomposition(self):
        """Known pairs decompose correctly."""
        assert _decompose_symbol("EURUSD") == ("EUR", "USD")
        assert _decompose_symbol("USDJPY") == ("USD", "JPY")
        assert _decompose_symbol("GBPUSD") == ("GBP", "USD")
