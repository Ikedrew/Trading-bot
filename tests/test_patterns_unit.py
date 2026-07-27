"""
Unit tests for candlestick pattern detection — deterministic input → expected output.

Tests each pattern module via registry.detect_all() with synthetic candle data.
No randomness. No market dependency. No production code modification.
"""

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.mt5_data import Candle
from strategy.signals import Side
from patterns.registry import load_all_patterns, detect_all

load_all_patterns()


def C(t, o, h, l, c):
    """Shorthand candle constructor."""
    return Candle(time=t, open=o, high=h, low=l, close=c, tick_volume=0)


def names(signals):
    """Extract pattern names from signal list."""
    return [s.pattern for s in signals]


def has(signals, pattern, side=None):
    """Check if a specific pattern (and optionally side) is in signals."""
    for s in signals:
        if s.pattern == pattern:
            if side is None or s.side == side:
                return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# 1-BAR PATTERNS
# ═══════════════════════════════════════════════════════════════════════════════

class TestHammer:
    def test_valid_hammer(self):
        # Long lower wick, small body at top, bullish close
        # body=0.004, lower_wick=0.01, upper_wick=0.001, range=0.015
        candles = [C(1, 1.10, 1.11, 1.09, 1.10), C(2, 1.100, 1.105, 1.090, 1.104)]
        signals = detect_all(candles, 1)
        assert has(signals, "HAMMER", Side.BUY), f"Expected HAMMER, got {names(signals)}"

    def test_hammer_rejected_short_wick(self):
        # Lower wick NOT > 2x body → no hammer
        # body=0.01, lower_wick=0.01 (equal, not >2x)
        candles = [C(1, 1.10, 1.11, 1.09, 1.10), C(2, 1.100, 1.110, 1.090, 1.100)]
        signals = detect_all(candles, 1)
        assert not has(signals, "HAMMER"), f"Should not detect HAMMER, got {names(signals)}"

    def test_hammer_rejected_large_upper_wick(self):
        # Upper wick >= body → not a hammer
        candles = [C(1, 1.10, 1.11, 1.09, 1.10), C(2, 1.100, 1.115, 1.085, 1.104)]
        signals = detect_all(candles, 1)
        assert not has(signals, "HAMMER"), f"Should not detect HAMMER, got {names(signals)}"


class TestHangingMan:
    def test_valid_hanging_man(self):
        # Same geometry as hammer but bearish close
        candles = [C(1, 1.10, 1.11, 1.09, 1.10), C(2, 1.104, 1.105, 1.090, 1.100)]
        signals = detect_all(candles, 1)
        assert has(signals, "HANGING_MAN", Side.SELL), f"Expected HANGING_MAN, got {names(signals)}"


class TestInvertedHammer:
    def test_valid_inverted_hammer(self):
        # Long upper wick, small body at bottom, bullish close
        candles = [C(1, 1.10, 1.11, 1.09, 1.10), C(2, 1.095, 1.115, 1.094, 1.096)]
        signals = detect_all(candles, 1)
        assert has(signals, "INVERTED_HAMMER", Side.BUY), f"Expected INVERTED_HAMMER, got {names(signals)}"

    def test_inverted_hammer_rejected_short_upper_wick(self):
        # Upper wick NOT > 2x body
        candles = [C(1, 1.10, 1.11, 1.09, 1.10), C(2, 1.095, 1.105, 1.094, 1.100)]
        signals = detect_all(candles, 1)
        assert not has(signals, "INVERTED_HAMMER"), f"Should not detect, got {names(signals)}"


class TestShootingStar:
    def test_valid_shooting_star(self):
        # Long upper wick, small body at bottom, bearish close
        candles = [C(1, 1.10, 1.11, 1.09, 1.10), C(2, 1.096, 1.115, 1.094, 1.095)]
        signals = detect_all(candles, 1)
        assert has(signals, "SHOOTING_STAR", Side.SELL), f"Expected SHOOTING_STAR, got {names(signals)}"


# ═══════════════════════════════════════════════════════════════════════════════
# 2-BAR PATTERNS
# ═══════════════════════════════════════════════════════════════════════════════

class TestBullishEngulfing:
    def test_valid_bullish_engulfing(self):
        # Bearish prev, bullish current that fully engulfs
        candles = [C(1, 1.10, 1.10, 1.08, 1.08), C(2, 1.07, 1.12, 1.07, 1.12)]
        signals = detect_all(candles, 1)
        assert has(signals, "BULLISH_ENGULFING", Side.BUY), f"Expected BULLISH_ENGULFING, got {names(signals)}"

    def test_bullish_engulfing_rejected_not_engulfing(self):
        # Current body does NOT exceed previous open
        candles = [C(1, 1.10, 1.10, 1.08, 1.08), C(2, 1.07, 1.09, 1.07, 1.09)]
        signals = detect_all(candles, 1)
        assert not has(signals, "BULLISH_ENGULFING"), f"Should not detect, got {names(signals)}"

    def test_bullish_engulfing_rejected_same_direction(self):
        # Both candles bullish → not engulfing
        candles = [C(1, 1.08, 1.10, 1.08, 1.10), C(2, 1.09, 1.12, 1.09, 1.12)]
        signals = detect_all(candles, 1)
        assert not has(signals, "BULLISH_ENGULFING"), f"Should not detect, got {names(signals)}"


class TestBearishEngulfing:
    def test_valid_bearish_engulfing(self):
        # Bullish prev, bearish current that fully engulfs
        candles = [C(1, 1.08, 1.12, 1.08, 1.12), C(2, 1.13, 1.13, 1.06, 1.06)]
        signals = detect_all(candles, 1)
        assert has(signals, "BEARISH_ENGULFING", Side.SELL), f"Expected BEARISH_ENGULFING, got {names(signals)}"


class TestTweezerTop:
    def test_valid_tweezer_top(self):
        # Bullish prev, bearish current, matching highs within 0.001
        candles = [C(1, 1.08, 1.12, 1.08, 1.11), C(2, 1.11, 1.1201, 1.09, 1.09)]
        signals = detect_all(candles, 1)
        assert has(signals, "TWEEZER_TOP", Side.SELL), f"Expected TWEEZER_TOP, got {names(signals)}"

    def test_tweezer_top_rejected_highs_too_far(self):
        # Highs differ by more than 0.001
        candles = [C(1, 1.08, 1.12, 1.08, 1.11), C(2, 1.11, 1.125, 1.09, 1.09)]
        signals = detect_all(candles, 1)
        assert not has(signals, "TWEEZER_TOP"), f"Should not detect, got {names(signals)}"


class TestTweezerBottom:
    def test_valid_tweezer_bottom(self):
        # Bearish prev, bullish current, matching lows within 0.001
        candles = [C(1, 1.12, 1.12, 1.08, 1.09), C(2, 1.09, 1.11, 1.0801, 1.11)]
        signals = detect_all(candles, 1)
        assert has(signals, "TWEEZER_BOTTOM", Side.BUY), f"Expected TWEEZER_BOTTOM, got {names(signals)}"


# ═══════════════════════════════════════════════════════════════════════════════
# 3-BAR PATTERNS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMorningStar:
    def test_valid_morning_star(self):
        # Bearish first, small-body second, bullish third closing above second open
        candles = [
            C(1, 1.10, 1.10, 1.06, 1.06),       # bearish
            C(2, 1.06, 1.07, 1.05, 1.0605),      # small body (< 30% of range)
            C(3, 1.06, 1.10, 1.06, 1.09),        # bullish, closes above second.open
        ]
        signals = detect_all(candles, 2)
        assert has(signals, "MORNING_STAR", Side.BUY), f"Expected MORNING_STAR, got {names(signals)}"

    def test_morning_star_rejected_large_middle_body(self):
        # Middle candle body >= 30% of its range → not a star
        candles = [
            C(1, 1.10, 1.10, 1.06, 1.06),
            C(2, 1.06, 1.08, 1.05, 1.075),       # body = 0.015, range = 0.03 → 50%
            C(3, 1.07, 1.10, 1.07, 1.09),
        ]
        signals = detect_all(candles, 2)
        assert not has(signals, "MORNING_STAR"), f"Should not detect, got {names(signals)}"


class TestEveningStar:
    def test_valid_evening_star(self):
        # Bullish first, small-body second, bearish third closing below second open
        candles = [
            C(1, 1.06, 1.10, 1.06, 1.10),       # bullish
            C(2, 1.10, 1.11, 1.09, 1.1005),     # small body
            C(3, 1.10, 1.10, 1.06, 1.07),       # bearish, closes below second.open
        ]
        signals = detect_all(candles, 2)
        assert has(signals, "EVENING_STAR", Side.SELL), f"Expected EVENING_STAR, got {names(signals)}"


class TestThreeWhiteSoldiers:
    def test_valid_three_white_soldiers(self):
        candles = [
            C(1, 1.00, 1.02, 1.00, 1.02),
            C(2, 1.02, 1.04, 1.02, 1.04),
            C(3, 1.04, 1.06, 1.04, 1.06),
        ]
        signals = detect_all(candles, 2)
        assert has(signals, "THREE_WHITE_SOLDIERS", Side.BUY), f"Expected THREE_WHITE_SOLDIERS, got {names(signals)}"

    def test_three_white_soldiers_rejected_non_progressive(self):
        # Third close NOT higher than second → invalid
        candles = [
            C(1, 1.00, 1.02, 1.00, 1.02),
            C(2, 1.02, 1.04, 1.02, 1.04),
            C(3, 1.04, 1.05, 1.03, 1.035),      # close < second.close
        ]
        signals = detect_all(candles, 2)
        assert not has(signals, "THREE_WHITE_SOLDIERS"), f"Should not detect, got {names(signals)}"


class TestThreeBlackCrows:
    def test_valid_three_black_crows(self):
        candles = [
            C(1, 1.06, 1.06, 1.04, 1.04),
            C(2, 1.04, 1.04, 1.02, 1.02),
            C(3, 1.02, 1.02, 1.00, 1.00),
        ]
        signals = detect_all(candles, 2)
        assert has(signals, "THREE_BLACK_CROWS", Side.SELL), f"Expected THREE_BLACK_CROWS, got {names(signals)}"

    def test_three_black_crows_rejected_bullish_candle(self):
        # One candle is bullish → invalid
        candles = [
            C(1, 1.06, 1.06, 1.04, 1.04),
            C(2, 1.03, 1.04, 1.02, 1.035),      # bullish
            C(3, 1.02, 1.02, 1.00, 1.00),
        ]
        signals = detect_all(candles, 2)
        assert not has(signals, "THREE_BLACK_CROWS"), f"Should not detect, got {names(signals)}"


class TestThreeInsideUp:
    def test_valid_three_inside_up(self):
        # Bearish first, bullish second inside first, bullish third confirms
        candles = [
            C(1, 1.10, 1.11, 1.05, 1.06),       # bearish, range 1.05-1.11
            C(2, 1.07, 1.09, 1.06, 1.08),       # bullish, inside (low>1.05, high<1.11)
            C(3, 1.08, 1.12, 1.08, 1.11),       # bullish, closes above second.open
        ]
        signals = detect_all(candles, 2)
        assert has(signals, "THREE_INSIDE_UP", Side.BUY), f"Expected THREE_INSIDE_UP, got {names(signals)}"

    def test_three_inside_up_rejected_not_inside(self):
        # Second bar NOT inside first (high exceeds first high)
        candles = [
            C(1, 1.10, 1.11, 1.05, 1.06),
            C(2, 1.07, 1.12, 1.06, 1.08),       # high=1.12 > first.high=1.11
            C(3, 1.08, 1.12, 1.08, 1.11),
        ]
        signals = detect_all(candles, 2)
        assert not has(signals, "THREE_INSIDE_UP"), f"Should not detect, got {names(signals)}"


class TestThreeInsideDown:
    def test_valid_three_inside_down(self):
        # Bullish first, bearish second inside first, bearish third confirms
        candles = [
            C(1, 1.06, 1.11, 1.05, 1.10),       # bullish, range 1.05-1.11
            C(2, 1.09, 1.10, 1.06, 1.07),       # bearish, inside
            C(3, 1.07, 1.07, 1.03, 1.04),       # bearish, closes below second.open
        ]
        signals = detect_all(candles, 2)
        assert has(signals, "THREE_INSIDE_DOWN", Side.SELL), f"Expected THREE_INSIDE_DOWN, got {names(signals)}"


# ═══════════════════════════════════════════════════════════════════════════════
# EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_flat_candle_no_detection(self):
        # Zero range candle → no patterns
        candles = [C(1, 1.10, 1.10, 1.10, 1.10), C(2, 1.10, 1.10, 1.10, 1.10)]
        signals = detect_all(candles, 1)
        assert signals == [], f"Expected no signals for flat candles, got {names(signals)}"

    def test_single_candle_at_index_0(self):
        # Index 0 with single candle → registry handles gracefully
        candles = [C(1, 1.10, 1.15, 1.05, 1.12)]
        signals = detect_all(candles, 0)
        # 1-bar patterns may fire at index 0 (registry level)
        # This is acceptable — orchestrator guards index 0 separately
        # Just verify no crash
        assert isinstance(signals, list)

    def test_confidence_present(self):
        # Verify confidence field is populated
        candles = [C(1, 1.10, 1.10, 1.08, 1.08), C(2, 1.07, 1.12, 1.07, 1.12)]
        signals = detect_all(candles, 1)
        for s in signals:
            assert 0.0 <= s.confidence <= 1.0, f"Confidence out of range: {s.confidence}"


# ═══════════════════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    test_classes = [
        TestHammer, TestHangingMan, TestInvertedHammer, TestShootingStar,
        TestBullishEngulfing, TestBearishEngulfing, TestTweezerTop, TestTweezerBottom,
        TestMorningStar, TestEveningStar,
        TestThreeWhiteSoldiers, TestThreeBlackCrows,
        TestThreeInsideUp, TestThreeInsideDown,
        TestEdgeCases,
    ]

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

    print(f"\nPATTERN UNIT TESTS: {passed} passed, {failed} failed")
    if errors:
        for e in errors:
            print(e)
    else:
        print("ALL PASS")
