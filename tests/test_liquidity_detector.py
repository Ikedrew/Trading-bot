"""
Tests for Liquidity Detector.

Verifies:
    - Equal highs detected correctly
    - Equal lows detected correctly
    - Previous day levels calculated correctly
    - Sweeps detected correctly
    - Edge cases handled
"""

from dataclasses import dataclass

import pytest

from core.market_intelligence.liquidity_detector import (
    detect_liquidity,
    LiquiditySnapshot,
    _detect_equal_levels,
    _get_session,
)


@dataclass
class C:
    """Mock candle."""
    high: float
    low: float
    open: float
    close: float
    time: int = 1753574400


def _make_candles(n: int, base_high: float = 1.086, base_low: float = 1.084) -> list:
    """Create n candles with slight variation."""
    candles = []
    for i in range(n):
        candles.append(C(
            high=base_high + (i % 3) * 0.0001,
            low=base_low - (i % 3) * 0.0001,
            open=base_low + 0.001,
            close=base_low + 0.0012,
            time=1753574400 + i * 300,
        ))
    return candles


class TestEqualHighs:
    """Equal highs (liquidity above) detection."""

    def test_detects_equal_highs(self):
        """Two swing highs within tolerance form a pool."""
        candles = _make_candles(50, base_high=1.0850, base_low=1.0830)
        # Place two clear swing highs at similar level, separated by > 5 bars
        candles[10] = C(high=1.0880, low=1.0840, open=1.0845, close=1.0855, time=candles[10].time)
        candles[20] = C(high=1.0881, low=1.0840, open=1.0845, close=1.0855, time=candles[20].time)
        # Make surrounding bars lower so pivots are confirmed
        for i in [9, 11, 19, 21]:
            candles[i] = C(high=1.0860, low=1.0840, open=1.0845, close=1.0850, time=candles[i].time)

        result = detect_liquidity(candles, current_price=1.0850, closed_index=45, symbol="EURUSD")
        assert result.equal_highs_above is True
        assert result.equal_highs_count >= 2
        assert result.equal_highs_distance_pips > 0

    def test_no_equal_highs_when_spread_too_wide(self):
        """Highs too far apart do not form a pool at the SAME level."""
        candles = _make_candles(50, base_high=1.0850, base_low=1.0830)
        # Two highs 40 pips apart (only one above price forms pool if it has a partner)
        candles[10] = C(high=1.0880, low=1.0840, open=1.0845, close=1.0850, time=candles[10].time)
        candles[20] = C(high=1.0920, low=1.0840, open=1.0845, close=1.0850, time=candles[20].time)
        for i in [9, 11, 19, 21]:
            candles[i] = C(high=1.0860, low=1.0840, open=1.0845, close=1.0850, time=candles[i].time)

        result = detect_liquidity(candles, current_price=1.0850, closed_index=45, symbol="EURUSD")
        # The two specific highs at 1.0880 and 1.0920 should NOT cluster together
        # (40 pips apart > 3 pip tolerance). They may still form pools with
        # OTHER nearby highs from _make_candles, but not with each other.
        # Key assertion: no single pool contains BOTH 1.088 and 1.092
        assert result.equal_highs_price != pytest.approx(1.090, abs=0.002)


class TestEqualLows:
    """Equal lows (liquidity below) detection."""

    def test_detects_equal_lows(self):
        """Two swing lows within tolerance form a pool below."""
        candles = _make_candles(50, base_high=1.0870, base_low=1.0850)
        candles[10] = C(high=1.0870, low=1.0820, open=1.0850, close=1.0860, time=candles[10].time)
        candles[20] = C(high=1.0870, low=1.0821, open=1.0850, close=1.0860, time=candles[20].time)
        for i in [9, 11, 19, 21]:
            candles[i] = C(high=1.0870, low=1.0840, open=1.0850, close=1.0855, time=candles[i].time)

        result = detect_liquidity(candles, current_price=1.0850, closed_index=45, symbol="EURUSD")
        assert result.equal_lows_below is True
        assert result.equal_lows_count >= 2
        assert result.equal_lows_distance_pips > 0


class TestPreviousDayLevels:
    """Previous day high/low detection."""

    def test_prev_day_computed(self):
        """Previous day extremes computed from candle timestamps."""
        # Create candles spanning two days
        # Day 1: timestamps for 2025-07-27 (various hours)
        # Day 2: timestamps for 2025-07-28 (current)
        from datetime import datetime, timezone, timedelta
        day1_start = int(datetime(2025, 7, 27, 10, 0, tzinfo=timezone.utc).timestamp())
        day2_start = int(datetime(2025, 7, 28, 10, 0, tzinfo=timezone.utc).timestamp())

        candles = []
        # Day 1 candles (48 bars = 4 hours at M5)
        for i in range(48):
            candles.append(C(
                high=1.0870 + (i % 5) * 0.0002,
                low=1.0830 - (i % 5) * 0.0002,
                open=1.0850, close=1.0855,
                time=day1_start + i * 300,
            ))
        # Day 2 candles
        for i in range(20):
            candles.append(C(
                high=1.0860, low=1.0840,
                open=1.0850, close=1.0855,
                time=day2_start + i * 300,
            ))

        result = detect_liquidity(candles, current_price=1.0850, closed_index=len(candles)-1, symbol="EURUSD")
        # Previous day should have extremes from day 1
        assert result.prev_day_high > 0
        assert result.prev_day_low > 0
        assert result.distance_to_prev_day_high_pips > 0


class TestSweepDetection:
    """Liquidity sweep detection."""

    def test_bearish_sweep_of_highs(self):
        """Price exceeds high level then closes back below = bearish sweep."""
        candles = _make_candles(50, base_high=1.0860, base_low=1.0840)
        # Equal highs at 1.0880
        candles[10] = C(high=1.0880, low=1.0850, open=1.0855, close=1.0865, time=candles[10].time)
        candles[20] = C(high=1.0881, low=1.0850, open=1.0855, close=1.0865, time=candles[20].time)
        for i in [9, 11, 19, 21]:
            candles[i] = C(high=1.0865, low=1.0840, open=1.0845, close=1.0855, time=candles[i].time)

        # Sweep candle: wick above 1.0880, closes below
        candles[45] = C(high=1.0890, low=1.0855, open=1.0870, close=1.0860, time=candles[45].time)

        result = detect_liquidity(candles, current_price=1.0860, closed_index=46, symbol="EURUSD")
        assert result.liquidity_sweep_just_occurred is True
        assert result.sweep_direction == "BEARISH"
        assert result.sweep_distance_pips > 0

    def test_bullish_sweep_of_lows(self):
        """Price exceeds low level then closes back above = bullish sweep."""
        candles = _make_candles(50, base_high=1.0870, base_low=1.0850)
        # Equal lows at 1.0820
        candles[10] = C(high=1.0870, low=1.0820, open=1.0850, close=1.0860, time=candles[10].time)
        candles[20] = C(high=1.0870, low=1.0821, open=1.0850, close=1.0860, time=candles[20].time)
        for i in [9, 11, 19, 21]:
            candles[i] = C(high=1.0870, low=1.0840, open=1.0850, close=1.0855, time=candles[i].time)

        # Sweep candle: wick below 1.0820, closes above
        candles[45] = C(high=1.0860, low=1.0810, open=1.0840, close=1.0850, time=candles[45].time)

        result = detect_liquidity(candles, current_price=1.0850, closed_index=46, symbol="EURUSD")
        assert result.liquidity_sweep_just_occurred is True
        assert result.sweep_direction == "BULLISH"

    def test_no_sweep_without_close_back(self):
        """Break without close-back is NOT a sweep."""
        candles = _make_candles(50, base_high=1.0860, base_low=1.0840)
        candles[10] = C(high=1.0880, low=1.0850, open=1.0855, close=1.0865, time=candles[10].time)
        candles[20] = C(high=1.0881, low=1.0850, open=1.0855, close=1.0865, time=candles[20].time)
        for i in [9, 11, 19, 21]:
            candles[i] = C(high=1.0865, low=1.0840, open=1.0845, close=1.0855, time=candles[i].time)

        # Breakout candle: closes ABOVE the level (no close-back)
        candles[45] = C(high=1.0900, low=1.0870, open=1.0875, close=1.0895, time=candles[45].time)

        result = detect_liquidity(candles, current_price=1.0890, closed_index=46, symbol="EURUSD")
        # Should not be a sweep since close did not come back below
        assert result.liquidity_sweep_just_occurred is False


class TestEdgeCases:
    """Edge cases and safety."""

    def test_empty_candles(self):
        """Returns empty snapshot for no candles."""
        result = detect_liquidity([], current_price=1.085, closed_index=0, symbol="EURUSD")
        assert result.equal_highs_above is False
        assert result.equal_lows_below is False

    def test_insufficient_candles(self):
        """Works with very few candles."""
        candles = _make_candles(5)
        result = detect_liquidity(candles, current_price=1.085, closed_index=3, symbol="EURUSD")
        assert isinstance(result, LiquiditySnapshot)

    def test_zero_price(self):
        """Returns empty for zero price."""
        candles = _make_candles(50)
        result = detect_liquidity(candles, current_price=0.0, closed_index=45, symbol="EURUSD")
        assert result.equal_highs_above is False

    def test_session_classification(self):
        """Session hours classified correctly."""
        assert _get_session(3) == "ASIA"
        assert _get_session(9) == "LONDON"
        assert _get_session(14) == "NY"
        assert _get_session(20) == "OFF"
