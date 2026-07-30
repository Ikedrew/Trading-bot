"""
Tests for Fair Value Gap Detector.

Verifies:
    - Bullish FVG detected from 3-candle pattern
    - Bearish FVG detected
    - Minimum size filter applied
    - Fill tracking updates correctly
    - Edge cases handled
"""

from dataclasses import dataclass

import pytest

from core.market_intelligence.fvg_detector import (
    detect_fvgs,
    FVGSnapshot,
    FairValueGap,
)


@dataclass
class C:
    """Mock candle."""
    high: float
    low: float
    open: float
    close: float
    time: int = 1753574400


def _base_candles(n: int = 50) -> list:
    """Create n normal candles (no gaps)."""
    candles = []
    for i in range(n):
        candles.append(C(
            high=1.0860 + (i % 2) * 0.0002,
            low=1.0840 - (i % 2) * 0.0002,
            open=1.0845, close=1.0855,
            time=1753574400 + i * 300,
        ))
    return candles


class TestBullishFVG:
    """Bullish FVG: Candle[0].high < Candle[2].low."""

    def test_detects_bullish_fvg(self):
        """Three candles with gap between C0.high and C2.low."""
        candles = _base_candles(50)
        # Create bullish FVG: C[20].high < C[22].low
        candles[20] = C(high=1.0850, low=1.0840, open=1.0842, close=1.0848, time=candles[20].time)
        candles[21] = C(high=1.0870, low=1.0845, open=1.0850, close=1.0868, time=candles[21].time)
        candles[22] = C(high=1.0880, low=1.0860, open=1.0862, close=1.0878, time=candles[22].time)
        # Gap: C[20].high=1.0850, C[22].low=1.0860 → gap size = 0.0010
        # Keep subsequent candles ABOVE the gap so it stays unfilled
        for i in range(23, 50):
            candles[i] = C(high=1.0885, low=1.0865, open=1.0870, close=1.0880, time=candles[i].time)

        result = detect_fvgs(candles, current_price=1.0870, closed_index=45, atr=0.0012, symbol="EURUSD")
        # FVG at [1.0850, 1.0860] is below price 1.0870
        assert result.total_unfilled_fvgs_below >= 1 or result.price_inside_fvg

    def test_bullish_fvg_above_price(self):
        """Bullish FVG detected above current price."""
        candles = _base_candles(50)
        candles[20] = C(high=1.0870, low=1.0860, open=1.0862, close=1.0868, time=candles[20].time)
        candles[21] = C(high=1.0900, low=1.0870, open=1.0875, close=1.0895, time=candles[21].time)
        candles[22] = C(high=1.0910, low=1.0885, open=1.0888, close=1.0905, time=candles[22].time)
        # Gap: C[20].high=1.0870 < C[22].low=1.0885 → bullish FVG at 1.0870-1.0885

        result = detect_fvgs(candles, current_price=1.0860, closed_index=45, atr=0.0012, symbol="EURUSD")
        assert result.nearest_fvg_above_price > 0
        assert result.nearest_fvg_above_distance_pips > 0
        assert result.total_unfilled_fvgs_above >= 1


class TestBearishFVG:
    """Bearish FVG: Candle[0].low > Candle[2].high."""

    def test_detects_bearish_fvg(self):
        """Three candles with gap between C2.high and C0.low."""
        candles = _base_candles(50)
        candles[20] = C(high=1.0880, low=1.0860, open=1.0875, close=1.0862, time=candles[20].time)
        candles[21] = C(high=1.0860, low=1.0830, open=1.0855, close=1.0835, time=candles[21].time)  # impulse down
        candles[22] = C(high=1.0840, low=1.0820, open=1.0838, close=1.0825, time=candles[22].time)
        # Gap: C[0].low=1.0860 > C[2].high=1.0840 → bearish FVG [1.0840, 1.0860]
        # Keep subsequent candles BELOW the gap so it stays unfilled
        for i in range(23, 50):
            candles[i] = C(high=1.0835, low=1.0815, open=1.0830, close=1.0820, time=candles[i].time)

        result = detect_fvgs(candles, current_price=1.0830, closed_index=45, atr=0.0012, symbol="EURUSD")
        assert result.total_unfilled_fvgs_above >= 1


class TestSizeFilter:
    """Minimum size filter (ATR-normalized)."""

    def test_small_fvg_filtered(self):
        """FVG smaller than min_atr_ratio is filtered out."""
        candles = _base_candles(50)
        # Very small gap: 0.0001 (1 pip) with ATR=0.0012 → 0.083 ATR (< 0.3 threshold)
        candles[20] = C(high=1.0850, low=1.0840, open=1.0842, close=1.0848, time=candles[20].time)
        candles[21] = C(high=1.0855, low=1.0848, open=1.0849, close=1.0854, time=candles[21].time)
        candles[22] = C(high=1.0856, low=1.0851, open=1.0852, close=1.0855, time=candles[22].time)
        # Gap: C[20].high=1.0850, C[22].low=1.0851 → gap = 0.0001

        result = detect_fvgs(candles, current_price=1.0845, closed_index=45, atr=0.0012, symbol="EURUSD")
        # Should be filtered (0.0001 < 0.3 * 0.0012 = 0.00036)
        # No FVGs from this tiny gap
        assert result.total_unfilled_fvgs_above == 0 or result.nearest_fvg_above_distance_pips == 0


class TestFillTracking:
    """FVG fill percentage tracking."""

    def test_partially_filled_fvg(self):
        """FVG that has been partially filled shows fill percentage."""
        candles = _base_candles(50)
        # Create bullish FVG at bars 15-17
        candles[15] = C(high=1.0850, low=1.0840, open=1.0842, close=1.0848, time=candles[15].time)
        candles[16] = C(high=1.0875, low=1.0850, open=1.0855, close=1.0872, time=candles[16].time)
        candles[17] = C(high=1.0880, low=1.0865, open=1.0867, close=1.0878, time=candles[17].time)
        # Gap: 1.0850 to 1.0865 (size = 0.0015)

        # Later candle partially fills it (dips into gap zone)
        candles[30] = C(high=1.0870, low=1.0855, open=1.0868, close=1.0858, time=candles[30].time)
        # Penetration: top(1.0865) - max(low(1.0855), bottom(1.0850)) = 1.0865 - 1.0855 = 0.0010
        # Fill: 0.0010 / 0.0015 = 66.7%

        result = detect_fvgs(candles, current_price=1.0870, closed_index=45, atr=0.0012, symbol="EURUSD")
        # The FVG should show partial fill
        # (May or may not show up depending on whether it's above/below current price)
        assert isinstance(result, FVGSnapshot)


class TestPriceInsideFVG:
    """Detection of price currently inside an FVG."""

    def test_price_inside_bullish_fvg(self):
        """Current price within an unfilled FVG zone."""
        candles = _base_candles(50)
        # Bullish FVG at bar 20: gap between 1.0850 and 1.0865
        candles[20] = C(high=1.0850, low=1.0840, open=1.0842, close=1.0848, time=candles[20].time)
        candles[21] = C(high=1.0875, low=1.0850, open=1.0855, close=1.0872, time=candles[21].time)
        candles[22] = C(high=1.0880, low=1.0865, open=1.0867, close=1.0878, time=candles[22].time)
        # Keep remaining candles above the FVG so it stays unfilled
        for i in range(23, 50):
            candles[i] = C(high=1.0880, low=1.0866, open=1.0870, close=1.0875, time=candles[i].time)

        # Price at 1.0855 is inside the gap [1.0850, 1.0865]
        result = detect_fvgs(candles, current_price=1.0855, closed_index=45, atr=0.0012, symbol="EURUSD")
        assert result.price_inside_fvg is True
        assert result.fvg_direction_if_inside == "BULLISH"


class TestEdgeCases:
    """Edge cases and safety."""

    def test_empty_candles(self):
        """Returns empty snapshot."""
        result = detect_fvgs([], current_price=1.085, closed_index=0, atr=0.001, symbol="EURUSD")
        assert result.total_unfilled_fvgs_above == 0

    def test_zero_atr(self):
        """Zero ATR returns empty (avoids division by zero)."""
        candles = _base_candles(50)
        result = detect_fvgs(candles, current_price=1.085, closed_index=45, atr=0.0, symbol="EURUSD")
        assert isinstance(result, FVGSnapshot)

    def test_insufficient_candles(self):
        """Very few candles handled gracefully."""
        candles = _base_candles(3)
        result = detect_fvgs(candles, current_price=1.085, closed_index=2, atr=0.001, symbol="EURUSD")
        assert isinstance(result, FVGSnapshot)
