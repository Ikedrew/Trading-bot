"""
Tests for confirm_signal refactor.

Validates:
- INVALID confirmation (body < 45%, too small range, direction mismatch)
- WEAK confirmation (45–59% body)
- STRONG confirmation (60%+ body)
- Backward compatibility (confirm_signal still returns tuple[bool, str])
- ConfirmationResult metrics (body_pct, wick_ratio, close_location)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.mt5_data import Candle
from strategy.signals import Side, Signal
from strategy.signal_orchestrator import (
    confirm_signal,
    confirm_signal_detailed,
    ConfirmationResult,
    ConfirmationStrength,
)


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _candle(o: float, h: float, l: float, c: float) -> Candle:
    return Candle(time=1000, open=o, high=h, low=l, close=c, tick_volume=100)


def _signal(side: Side = Side.BUY, bar_index: int = 0) -> Signal:
    return Signal(pattern="TEST_PATTERN", side=side, bar_index=bar_index, bar_time=1000)


# ─── STRONG CONFIRMATION TESTS (body >= 60%) ──────────────────────────────────

class TestStrongConfirmation:
    def test_large_body_bullish(self):
        """70% body bullish candle = STRONG."""
        # Range = 0.0100; body = 0.0070 (70%)
        c = _candle(o=1.1000, h=1.1100, l=1.1000, c=1.1070)
        result = confirm_signal_detailed(_signal(Side.BUY), [c])

        assert result.confirmed is True
        assert result.strength == ConfirmationStrength.STRONG
        assert result.body_pct >= 0.60
        assert "confirmed" in result.reason

    def test_large_body_bearish(self):
        """65% body bearish candle = STRONG."""
        # Range = 0.0100; body = 0.0065 (65%)
        c = _candle(o=1.1065, h=1.1100, l=1.1000, c=1.1000)
        result = confirm_signal_detailed(_signal(Side.SELL), [c])

        assert result.confirmed is True
        assert result.strength == ConfirmationStrength.STRONG
        assert result.body_pct >= 0.60

    def test_exactly_60_percent_is_strong(self):
        """Exactly 60% body = STRONG (boundary case)."""
        # Range = 0.0100; body = 0.0060 (60%)
        c = _candle(o=1.1000, h=1.1100, l=1.1000, c=1.1060)
        result = confirm_signal_detailed(_signal(Side.BUY), [c])

        assert result.confirmed is True
        assert result.strength == ConfirmationStrength.STRONG

    def test_full_body_candle(self):
        """100% body (no wicks) = STRONG."""
        c = _candle(o=1.1000, h=1.1050, l=1.1000, c=1.1050)
        result = confirm_signal_detailed(_signal(Side.BUY), [c])

        assert result.confirmed is True
        assert result.strength == ConfirmationStrength.STRONG
        assert result.body_pct == 1.0
        assert result.wick_ratio == 0.0


# ─── WEAK CONFIRMATION TESTS (body 45–59%) ───────────────────────────────────

class TestWeakConfirmation:
    def test_50_percent_body(self):
        """50% body = WEAK."""
        # Range = 0.0100; body = 0.0050 (50%)
        c = _candle(o=1.1025, h=1.1100, l=1.1000, c=1.1075)
        result = confirm_signal_detailed(_signal(Side.BUY), [c])

        assert result.confirmed is True
        assert result.strength == ConfirmationStrength.WEAK
        assert 0.45 <= result.body_pct < 0.60

    def test_45_percent_body(self):
        """Exactly 45% body = WEAK (boundary case)."""
        # Range = 0.0100; body = 0.0045 (45%)
        c = _candle(o=1.1025, h=1.1100, l=1.1000, c=1.1070)
        result = confirm_signal_detailed(_signal(Side.BUY), [c])

        assert result.confirmed is True
        assert result.strength == ConfirmationStrength.WEAK

    def test_59_percent_body(self):
        """59% body = WEAK (just below STRONG threshold)."""
        # Range = 0.0100; body = 0.0059 (59%)
        c = _candle(o=1.1020, h=1.1100, l=1.1000, c=1.1079)
        result = confirm_signal_detailed(_signal(Side.BUY), [c])

        assert result.confirmed is True
        assert result.strength == ConfirmationStrength.WEAK

    def test_weak_bearish(self):
        """50% body bearish = WEAK."""
        # Range = 0.0100; body = 0.0050 (50%)
        c = _candle(o=1.1075, h=1.1100, l=1.1000, c=1.1025)
        result = confirm_signal_detailed(_signal(Side.SELL), [c])

        assert result.confirmed is True
        assert result.strength == ConfirmationStrength.WEAK


# ─── INVALID CONFIRMATION TESTS ───────────────────────────────────────────────

class TestInvalidConfirmation:
    def test_body_below_45_percent(self):
        """30% body = INVALID (too weak)."""
        # Range = 0.0100; body = 0.0030 (30%)
        c = _candle(o=1.1035, h=1.1100, l=1.1000, c=1.1065)
        result = confirm_signal_detailed(_signal(Side.BUY), [c])

        assert result.confirmed is False
        assert result.strength == ConfirmationStrength.INVALID
        assert "body too weak" in result.reason

    def test_body_at_44_percent(self):
        """44% body = INVALID (just below weak threshold)."""
        # Range = 0.0100; body = 0.0044 (44%)
        c = _candle(o=1.1028, h=1.1100, l=1.1000, c=1.1072)
        result = confirm_signal_detailed(_signal(Side.BUY), [c])

        assert result.confirmed is False
        assert result.strength == ConfirmationStrength.INVALID

    def test_doji_candle(self):
        """Near-zero body (doji) = INVALID."""
        c = _candle(o=1.1050, h=1.1100, l=1.1000, c=1.1051)
        result = confirm_signal_detailed(_signal(Side.BUY), [c])

        assert result.confirmed is False
        assert result.strength == ConfirmationStrength.INVALID

    def test_range_too_small(self):
        """Candle range below minimum = INVALID."""
        # Range = 0.0003 (below 0.0005 threshold)
        c = _candle(o=1.1000, h=1.1003, l=1.1000, c=1.1002)
        result = confirm_signal_detailed(_signal(Side.BUY), [c])

        assert result.confirmed is False
        assert result.strength == ConfirmationStrength.INVALID
        assert "range too small" in result.reason

    def test_direction_mismatch_buy_on_bearish(self):
        """BUY signal on bearish candle = INVALID."""
        c = _candle(o=1.1080, h=1.1100, l=1.1000, c=1.1020)
        result = confirm_signal_detailed(_signal(Side.BUY), [c])

        assert result.confirmed is False
        assert result.strength == ConfirmationStrength.INVALID
        assert "bullish signal but bearish candle" in result.reason

    def test_direction_mismatch_sell_on_bullish(self):
        """SELL signal on bullish candle = INVALID."""
        c = _candle(o=1.1020, h=1.1100, l=1.1000, c=1.1080)
        result = confirm_signal_detailed(_signal(Side.SELL), [c])

        assert result.confirmed is False
        assert result.strength == ConfirmationStrength.INVALID
        assert "bearish signal but bullish candle" in result.reason

    def test_zero_range_candle(self):
        """Zero-range candle (flat) = INVALID."""
        c = _candle(o=1.1000, h=1.1000, l=1.1000, c=1.1000)
        result = confirm_signal_detailed(_signal(Side.BUY), [c])

        assert result.confirmed is False
        assert result.strength == ConfirmationStrength.INVALID


# ─── METRICS TESTS ────────────────────────────────────────────────────────────

class TestConfirmationMetrics:
    def test_body_pct_calculated_correctly(self):
        """body_pct = body / range."""
        # Range = 0.0100; body = 0.0080 (80%)
        c = _candle(o=1.1010, h=1.1100, l=1.1000, c=1.1090)
        result = confirm_signal_detailed(_signal(Side.BUY), [c])

        assert result.body_pct == pytest.approx(0.80, abs=0.01)

    def test_wick_ratio_calculated_correctly(self):
        """wick_ratio = (upper_wick + lower_wick) / range."""
        # Open=1.1020, Close=1.1080, High=1.1100, Low=1.1000
        # Upper wick = 1.1100 - 1.1080 = 0.0020
        # Lower wick = 1.1020 - 1.1000 = 0.0020
        # Range = 0.0100; wick_ratio = 0.0040 / 0.0100 = 0.40
        c = _candle(o=1.1020, h=1.1100, l=1.1000, c=1.1080)
        result = confirm_signal_detailed(_signal(Side.BUY), [c])

        assert result.wick_ratio == pytest.approx(0.40, abs=0.01)

    def test_close_location_at_top(self):
        """Close at top of range = close_location ≈ 1.0."""
        c = _candle(o=1.1000, h=1.1100, l=1.1000, c=1.1100)
        result = confirm_signal_detailed(_signal(Side.BUY), [c])

        assert result.close_location == pytest.approx(1.0, abs=0.01)

    def test_close_location_at_bottom(self):
        """Close at bottom of range = close_location ≈ 0.0."""
        c = _candle(o=1.1100, h=1.1100, l=1.1000, c=1.1000)
        result = confirm_signal_detailed(_signal(Side.SELL), [c])

        assert result.close_location == pytest.approx(0.0, abs=0.01)

    def test_close_location_midpoint(self):
        """Close at midpoint = close_location ≈ 0.5."""
        c = _candle(o=1.1000, h=1.1100, l=1.1000, c=1.1050)
        result = confirm_signal_detailed(_signal(Side.BUY), [c])

        assert result.close_location == pytest.approx(0.5, abs=0.01)


# ─── BACKWARD COMPATIBILITY TESTS ────────────────────────────────────────────

class TestBackwardCompatibility:
    def test_confirm_signal_returns_tuple(self):
        """confirm_signal() still returns (bool, str) for existing consumers."""
        c = _candle(o=1.1000, h=1.1100, l=1.1000, c=1.1080)
        result = confirm_signal(_signal(Side.BUY), [c])

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)

    def test_backward_compat_strong_returns_true(self):
        """Strong confirmation returns (True, reason) via legacy interface."""
        c = _candle(o=1.1000, h=1.1100, l=1.1000, c=1.1080)
        confirmed, reason = confirm_signal(_signal(Side.BUY), [c])

        assert confirmed is True

    def test_backward_compat_invalid_returns_false(self):
        """Invalid confirmation returns (False, reason) via legacy interface."""
        c = _candle(o=1.1080, h=1.1100, l=1.1000, c=1.1020)
        confirmed, reason = confirm_signal(_signal(Side.BUY), [c])

        assert confirmed is False
        assert "bullish signal but bearish candle" in reason

    def test_backward_compat_weak_body_now_invalid(self):
        """Previously-passing weak body (< 45%) now returns False (BREAKING CHANGE)."""
        # Range = 0.0100; body = 0.0030 (30%) — previously returned (True, "weak candle body")
        c = _candle(o=1.1035, h=1.1100, l=1.1000, c=1.1065)
        confirmed, reason = confirm_signal(_signal(Side.BUY), [c])

        # This is the intentional behavioral change: body < 45% is now INVALID
        assert confirmed is False

    def test_backward_compat_tiny_range_now_invalid(self):
        """Previously-passing tiny range now returns False (BREAKING CHANGE)."""
        # Range = 0.0003 — previously returned (True, "too small range")
        c = _candle(o=1.1000, h=1.1003, l=1.1000, c=1.1002)
        confirmed, reason = confirm_signal(_signal(Side.BUY), [c])

        # This is the intentional behavioral change: tiny range is now INVALID
        assert confirmed is False
