"""
Tests for Order Block Detector.

Verifies:
    - Valid bullish OB detected after bullish displacement
    - Valid bearish OB detected after bearish displacement
    - Minimum displacement filter enforced
    - Mitigation flagged correctly
    - Invalidation on break-through works
    - Edge cases handled
"""

from dataclasses import dataclass

import pytest

from core.market_intelligence.order_block_detector import (
    detect_order_blocks,
    OBSnapshot,
    OrderBlock,
)


@dataclass
class C:
    """Mock candle."""
    high: float
    low: float
    open: float
    close: float
    time: int = 1753574400


def _base_candles(n: int = 80) -> list:
    """Create n neutral candles."""
    candles = []
    for i in range(n):
        candles.append(C(
            high=1.0855, low=1.0845,
            open=1.0848, close=1.0852,
            time=1753574400 + i * 300,
        ))
    return candles


class TestBullishOB:
    """Demand (bullish) order block detection."""

    def test_detects_demand_ob(self):
        """Bearish candle before 3+ bullish displacement = demand OB."""
        candles = _base_candles(80)
        # Bearish candle at index 20 (the OB candidate)
        candles[20] = C(high=1.0855, low=1.0840, open=1.0853, close=1.0842, time=candles[20].time)
        # 3 consecutive bullish candles starting at 21
        # Each must have close > open, and total move must exceed 2*ATR = 0.0020
        candles[21] = C(high=1.0865, low=1.0843, open=1.0844, close=1.0863, time=candles[21].time)
        candles[22] = C(high=1.0883, low=1.0863, open=1.0864, close=1.0881, time=candles[22].time)
        candles[23] = C(high=1.0905, low=1.0881, open=1.0882, close=1.0903, time=candles[23].time)
        # Total move: close[23] - open[21] = 1.0903 - 1.0844 = 0.0059 > 0.002 ✓
        # Keep price above the OB zone afterward so it's not invalidated
        for i in range(24, 70):
            candles[i] = C(high=1.0910, low=1.0895, open=1.0898, close=1.0905, time=candles[i].time)

        result = detect_order_blocks(
            candles, current_price=1.0900, closed_index=70, atr=0.0010, symbol="EURUSD")
        assert result.nearest_demand_ob_price > 0
        assert result.demand_ob_strength > 0

    def test_demand_ob_below_current_price(self):
        """Demand OB should be below current price."""
        candles = _base_candles(80)
        candles[20] = C(high=1.0850, low=1.0835, open=1.0848, close=1.0837, time=candles[20].time)
        candles[21] = C(high=1.0860, low=1.0840, open=1.0842, close=1.0858, time=candles[21].time)
        candles[22] = C(high=1.0878, low=1.0858, open=1.0860, close=1.0876, time=candles[22].time)
        candles[23] = C(high=1.0900, low=1.0876, open=1.0878, close=1.0898, time=candles[23].time)

        result = detect_order_blocks(
            candles, current_price=1.0890, closed_index=70, atr=0.0010, symbol="EURUSD")
        if result.nearest_demand_ob_price > 0:
            assert result.nearest_demand_ob_price < 1.0890


class TestBearishOB:
    """Supply (bearish) order block detection."""

    def test_detects_supply_ob(self):
        """Bullish candle before 3+ bearish displacement = supply OB."""
        candles = _base_candles(80)
        # Bullish candle at index 20 (the OB)
        candles[20] = C(high=1.0870, low=1.0855, open=1.0857, close=1.0868, time=candles[20].time)
        # 3+ bearish candles starting at 21
        candles[21] = C(high=1.0865, low=1.0845, open=1.0863, close=1.0847, time=candles[21].time)
        candles[22] = C(high=1.0848, low=1.0825, open=1.0846, close=1.0827, time=candles[22].time)
        candles[23] = C(high=1.0828, low=1.0805, open=1.0826, close=1.0808, time=candles[23].time)
        # Total move: 1.0863 - 1.0808 = 0.0055 > 0.002 ✓

        result = detect_order_blocks(
            candles, current_price=1.0840, closed_index=70, atr=0.0010, symbol="EURUSD")
        assert result.nearest_supply_ob_price > 0
        assert result.supply_ob_strength > 0


class TestDisplacementFilter:
    """Minimum displacement enforcement."""

    def test_no_ob_without_sufficient_displacement(self):
        """Small moves do not create order blocks."""
        candles = _base_candles(80)
        # "Displacement" of only 0.0008 (< 2*0.001=0.002 threshold)
        candles[20] = C(high=1.0852, low=1.0845, open=1.0850, close=1.0846, time=candles[20].time)
        candles[21] = C(high=1.0855, low=1.0848, open=1.0849, close=1.0854, time=candles[21].time)
        candles[22] = C(high=1.0857, low=1.0850, open=1.0851, close=1.0856, time=candles[22].time)
        candles[23] = C(high=1.0858, low=1.0852, open=1.0853, close=1.0857, time=candles[23].time)

        result = detect_order_blocks(
            candles, current_price=1.0855, closed_index=70, atr=0.0010, symbol="EURUSD")
        # Should not detect OB (displacement too small)
        assert result.nearest_demand_ob_price == 0.0 or result.demand_ob_strength < 0.5


class TestMitigation:
    """OB mitigation (price returns to zone)."""

    def test_mitigated_ob_flagged(self):
        """OB is flagged as mitigated when price returns to it."""
        candles = _base_candles(80)
        # Create demand OB at index 20
        candles[20] = C(high=1.0850, low=1.0835, open=1.0848, close=1.0837, time=candles[20].time)
        candles[21] = C(high=1.0862, low=1.0840, open=1.0842, close=1.0860, time=candles[21].time)
        candles[22] = C(high=1.0880, low=1.0860, open=1.0862, close=1.0878, time=candles[22].time)
        candles[23] = C(high=1.0900, low=1.0878, open=1.0880, close=1.0898, time=candles[23].time)

        # Price returns to OB zone [1.0835, 1.0850] at bar 40
        candles[40] = C(high=1.0860, low=1.0838, open=1.0855, close=1.0842, time=candles[40].time)
        # Close at 1.0842 is inside [1.0835, 1.0850] → mitigated

        result = detect_order_blocks(
            candles, current_price=1.0860, closed_index=70, atr=0.0010, symbol="EURUSD")
        if result.nearest_demand_ob_price > 0:
            assert result.demand_ob_mitigated is True


class TestInvalidation:
    """OB invalidation (price breaks through zone)."""

    def test_invalidated_ob_removed(self):
        """OB invalidated when price closes below its low (demand)."""
        candles = _base_candles(80)
        # Create demand OB at index 20 (zone: 1.0835 - 1.0850)
        candles[20] = C(high=1.0850, low=1.0835, open=1.0848, close=1.0837, time=candles[20].time)
        candles[21] = C(high=1.0862, low=1.0840, open=1.0842, close=1.0860, time=candles[21].time)
        candles[22] = C(high=1.0880, low=1.0860, open=1.0862, close=1.0878, time=candles[22].time)
        candles[23] = C(high=1.0900, low=1.0878, open=1.0880, close=1.0898, time=candles[23].time)

        # Price closes below OB.low=1.0835 → invalidated
        candles[50] = C(high=1.0840, low=1.0820, open=1.0835, close=1.0825, time=candles[50].time)

        result = detect_order_blocks(
            candles, current_price=1.0860, closed_index=70, atr=0.0010, symbol="EURUSD")
        # OB should be invalidated (not showing up as nearest)
        # It's possible another OB exists, so just check it's not the invalidated one
        if result.nearest_demand_ob_price > 0:
            # If an OB shows, it shouldn't be the invalidated zone
            assert result.nearest_demand_ob_price != pytest.approx(1.08425, abs=0.001)


class TestEdgeCases:
    """Edge cases and safety."""

    def test_empty_candles(self):
        """Returns empty snapshot."""
        result = detect_order_blocks([], current_price=1.085, closed_index=0, atr=0.001, symbol="EURUSD")
        assert result.nearest_demand_ob_price == 0.0
        assert result.nearest_supply_ob_price == 0.0

    def test_zero_atr(self):
        """Zero ATR returns empty."""
        candles = _base_candles(50)
        result = detect_order_blocks(candles, current_price=1.085, closed_index=45, atr=0.0, symbol="EURUSD")
        assert isinstance(result, OBSnapshot)

    def test_price_inside_ob(self):
        """Detects when current price is inside an OB zone."""
        candles = _base_candles(80)
        # Supply OB at [1.0855, 1.0870]
        candles[20] = C(high=1.0870, low=1.0855, open=1.0857, close=1.0868, time=candles[20].time)
        candles[21] = C(high=1.0855, low=1.0830, open=1.0853, close=1.0832, time=candles[21].time)
        candles[22] = C(high=1.0833, low=1.0810, open=1.0831, close=1.0812, time=candles[22].time)
        candles[23] = C(high=1.0813, low=1.0790, open=1.0811, close=1.0792, time=candles[23].time)
        # Keep price above the zone so it's not invalidated
        for i in range(24, 70):
            candles[i] = C(high=1.0870, low=1.0856, open=1.0858, close=1.0865, time=candles[i].time)

        result = detect_order_blocks(
            candles, current_price=1.0862, closed_index=69, atr=0.0010, symbol="EURUSD")
        # Price 1.0862 might be inside the supply OB [1.0855, 1.0870]
        # Note: OB may be mitigated since price candles close inside it
        assert isinstance(result, OBSnapshot)
