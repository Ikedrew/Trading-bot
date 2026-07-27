"""
Tests for Phase 4C.1 — H1 Structure Data Completion.

Verifies that BiasSnapshot exposes last_swing_high and last_swing_low
from the existing H1 swing structure calculation.
"""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.timeframes.h1_bias import analyze_bias, _swing_structure
from core.timeframes.types import BiasSnapshot, BiasDirection
from data.mt5_data import Candle


# ═══════════════════════════════════════════════════════════════════════════════
# CANDLE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _make_candle(o: float, h: float, l: float, c: float, t: int = 0) -> Candle:
    return Candle(time=t, open=o, high=h, low=l, close=c, tick_volume=100)


def _trending_bullish_candles(n: int = 30) -> list[Candle]:
    """Generate bullish trending H1 candles with clear swing highs/lows."""
    candles = []
    base = 1.2800
    for i in range(n):
        # Create wave pattern within uptrend: 3 up, 2 down, repeat
        wave_pos = i % 5
        trend_offset = i * 0.0005  # Overall upward drift
        if wave_pos < 3:
            # Up wave — each bar higher
            wave_offset = wave_pos * 0.0012
        else:
            # Down wave (pullback) — each bar lower
            wave_offset = 0.0024 - (wave_pos - 2) * 0.0008

        price = base + trend_offset + wave_offset
        o = price
        c = price + 0.0004 if wave_pos < 3 else price - 0.0003
        h = max(o, c) + 0.0003
        l = min(o, c) - 0.0003
        candles.append(_make_candle(o, h, l, c, t=1784800000 + i * 3600))
    return candles


def _ranging_candles(n: int = 30) -> list[Candle]:
    """Generate ranging H1 candles with alternating highs/lows."""
    candles = []
    base = 1.3000
    for i in range(n):
        # Range: oscillate around base
        offset = 0.0020 * (1 if i % 4 < 2 else -1)
        o = base + offset
        c = base - offset * 0.5
        h = max(o, c) + 0.0005
        l = min(o, c) - 0.0005
        candles.append(_make_candle(o, h, l, c, t=1784800000 + i * 3600))
    return candles


def _insufficient_candles() -> list[Candle]:
    """Only 5 candles — not enough for reliable swing detection."""
    return [_make_candle(1.3, 1.301, 1.299, 1.3005, t=i * 3600) for i in range(5)]


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1: Trending market produces swing levels
# ═══════════════════════════════════════════════════════════════════════════════

class TestTrendingSwingLevels:
    def test_bullish_trend_has_swing_high(self):
        candles = _trending_bullish_candles(30)
        result = analyze_bias(candles)

        assert result.last_swing_high is not None
        assert result.last_swing_high > 0

    def test_bullish_trend_has_swing_low(self):
        candles = _trending_bullish_candles(30)
        result = analyze_bias(candles)

        assert result.last_swing_low is not None
        assert result.last_swing_low > 0

    def test_swing_high_above_swing_low(self):
        candles = _trending_bullish_candles(30)
        result = analyze_bias(candles)

        if result.last_swing_high is not None and result.last_swing_low is not None:
            assert result.last_swing_high > result.last_swing_low


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: Ranging market produces swing levels
# ═══════════════════════════════════════════════════════════════════════════════

class TestRangingSwingLevels:
    def test_ranging_has_swing_levels(self):
        candles = _ranging_candles(30)
        result = analyze_bias(candles)

        # Ranging market should still detect swing points
        # (may have None if oscillations don't create clear local extrema)
        assert isinstance(result, BiasSnapshot)
        # At minimum, the fields exist on the object
        assert hasattr(result, "last_swing_high")
        assert hasattr(result, "last_swing_low")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: Insufficient data returns None
# ═══════════════════════════════════════════════════════════════════════════════

class TestInsufficientData:
    def test_few_candles_returns_none(self):
        candles = _insufficient_candles()
        result = analyze_bias(candles)

        # With insufficient data, swing levels should be None
        assert result.last_swing_high is None
        assert result.last_swing_low is None
        assert result.direction == BiasDirection.NEUTRAL

    def test_empty_candles_returns_neutral(self):
        result = analyze_bias([_make_candle(1.3, 1.301, 1.299, 1.3005)])
        assert result.direction == BiasDirection.NEUTRAL
        assert result.last_swing_high is None
        assert result.last_swing_low is None


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4: _swing_structure returns price levels
# ═══════════════════════════════════════════════════════════════════════════════

class TestSwingStructureFunction:
    def test_returns_four_values(self):
        candles = _trending_bullish_candles(25)
        result = _swing_structure(candles, 20)
        assert len(result) == 4  # (structure_type, strength, last_high, last_low)

    def test_swing_prices_are_floats(self):
        candles = _trending_bullish_candles(25)
        structure_type, strength, high, low = _swing_structure(candles, 20)
        if high is not None:
            assert isinstance(high, float)
        if low is not None:
            assert isinstance(low, float)

    def test_insufficient_lookback_returns_none(self):
        candles = [_make_candle(1.3, 1.301, 1.299, 1.3005) for _ in range(3)]
        structure_type, strength, high, low = _swing_structure(candles, 10)
        assert structure_type == "MIXED"
        assert high is None
        assert low is None


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 5: Backward compatibility
# ═══════════════════════════════════════════════════════════════════════════════

class TestBackwardCompatibility:
    def test_bias_snapshot_has_all_original_fields(self):
        candles = _trending_bullish_candles(30)
        result = analyze_bias(candles)

        # All original fields still present
        assert hasattr(result, "direction")
        assert hasattr(result, "confidence")
        assert hasattr(result, "bar_time")
        assert hasattr(result, "ema_position")
        assert hasattr(result, "swing_structure")
        assert hasattr(result, "bos_confirmed")
        assert hasattr(result, "bos_direction")

    def test_new_fields_default_to_none(self):
        # Construct BiasSnapshot without new fields (simulates old code)
        snap = BiasSnapshot(
            direction=BiasDirection.BULLISH,
            confidence=0.7,
            bar_time=1784800000,
            ema_position=0.5,
            swing_structure="HH_HL",
        )
        # Defaults should be None
        assert snap.last_swing_high is None
        assert snap.last_swing_low is None
