"""Tests for M5 entry confirmation signals: local_bos and confirmation_candle.

Proves:
1. local_bos correctly wired from swing_context BOS detection
2. confirmation_candle detects strong directional candles
3. Neither produces false positives on neutral/weak data
"""

import sys
from pathlib import Path
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ─── CANDLE MOCK ──────────────────────────────────────────────────────────────

@dataclass
class MockCandle:
    time: int = 0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0


def _make_candles(n: int, base: float = 1.0, atr: float = 0.001) -> list[MockCandle]:
    """Generate n neutral candles with given ATR."""
    candles = []
    for i in range(n):
        o = base + i * 0.00001
        candles.append(MockCandle(
            time=1000 + i * 300,
            open=o,
            high=o + atr * 0.5,
            low=o - atr * 0.5,
            close=o + 0.00001,
        ))
    return candles


# ═══════════════════════════════════════════════════════════════════════════════
# LOCAL BOS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestLocalBos:
    """Verify local_bos is wired from swing_context BOS detection."""

    def test_bullish_bos_detected(self):
        """When price closes above last swing high 2+ times → local_bos=True, direction=BULLISH."""
        from core.market_understanding.builders import build_m5_understanding

        # Build 55 candles with clear bullish structure:
        # Create swing highs that get broken at the end
        candles = []
        base = 1.08000
        for i in range(55):
            o = base + i * 0.00002
            candles.append(MockCandle(
                time=1000 + i * 300,
                open=o,
                high=o + 0.0005,
                low=o - 0.0003,
                close=o + 0.0004,
            ))

        # Create a clear swing high in the middle (pivot needs 3 bars each side)
        # Set bar 30 as a swing high pivot
        pivot_idx = 30
        pivot_high = base + 0.0050  # Clearly above neighbours
        candles[pivot_idx] = MockCandle(
            time=candles[pivot_idx].time,
            open=pivot_high - 0.0002,
            high=pivot_high,
            low=pivot_high - 0.0005,
            close=pivot_high - 0.0001,
        )
        # Make surrounding bars lower
        for offset in range(1, 4):
            lower = pivot_high - 0.0010 - offset * 0.0002
            candles[pivot_idx - offset] = MockCandle(
                time=candles[pivot_idx - offset].time,
                open=lower, high=lower + 0.0003, low=lower - 0.0003, close=lower + 0.0001,
            )
            candles[pivot_idx + offset] = MockCandle(
                time=candles[pivot_idx + offset].time,
                open=lower, high=lower + 0.0003, low=lower - 0.0003, close=lower + 0.0001,
            )

        # Also create a second swing high at bar 20 (lower than bar 30 = HH pattern)
        pivot2_idx = 20
        pivot2_high = pivot_high - 0.0015
        candles[pivot2_idx] = MockCandle(
            time=candles[pivot2_idx].time,
            open=pivot2_high - 0.0002,
            high=pivot2_high,
            low=pivot2_high - 0.0005,
            close=pivot2_high - 0.0001,
        )
        for offset in range(1, 4):
            lower = pivot2_high - 0.0010 - offset * 0.0002
            candles[pivot2_idx - offset] = MockCandle(
                time=candles[pivot2_idx - offset].time,
                open=lower, high=lower + 0.0003, low=lower - 0.0003, close=lower + 0.0001,
            )
            candles[pivot2_idx + offset] = MockCandle(
                time=candles[pivot2_idx + offset].time,
                open=lower, high=lower + 0.0003, low=lower - 0.0003, close=lower + 0.0001,
            )

        # Last 2 candles close ABOVE the swing high (BOS confirmation)
        for i in range(-2, 0):
            candles[i] = MockCandle(
                time=candles[i].time,
                open=pivot_high + 0.0001,
                high=pivot_high + 0.0008,
                low=pivot_high - 0.0001,
                close=pivot_high + 0.0005,  # Closes above swing high
            )

        result = build_m5_understanding(candles=candles, bid=1.085, ask=1.0852)
        assert result.local_bos is True
        assert result.local_bos_direction == "BULLISH"

    def test_bearish_bos_detected(self):
        """When price closes below last swing low 2+ times → local_bos=True, direction=BEARISH."""
        from core.market_understanding.builders import build_m5_understanding

        candles = []
        base = 1.08000
        for i in range(55):
            o = base - i * 0.00002
            candles.append(MockCandle(
                time=1000 + i * 300,
                open=o,
                high=o + 0.0003,
                low=o - 0.0005,
                close=o - 0.0004,
            ))

        # Create swing lows that get broken
        pivot_idx = 30
        pivot_low = base - 0.0050
        candles[pivot_idx] = MockCandle(
            time=candles[pivot_idx].time,
            open=pivot_low + 0.0002,
            high=pivot_low + 0.0005,
            low=pivot_low,
            close=pivot_low + 0.0001,
        )
        for offset in range(1, 4):
            higher = pivot_low + 0.0010 + offset * 0.0002
            candles[pivot_idx - offset] = MockCandle(
                time=candles[pivot_idx - offset].time,
                open=higher, high=higher + 0.0003, low=higher - 0.0003, close=higher - 0.0001,
            )
            candles[pivot_idx + offset] = MockCandle(
                time=candles[pivot_idx + offset].time,
                open=higher, high=higher + 0.0003, low=higher - 0.0003, close=higher - 0.0001,
            )

        # Second swing low (higher than first = LL pattern)
        pivot2_idx = 20
        pivot2_low = pivot_low + 0.0015
        candles[pivot2_idx] = MockCandle(
            time=candles[pivot2_idx].time,
            open=pivot2_low + 0.0002,
            high=pivot2_low + 0.0005,
            low=pivot2_low,
            close=pivot2_low + 0.0001,
        )
        for offset in range(1, 4):
            higher = pivot2_low + 0.0010 + offset * 0.0002
            candles[pivot2_idx - offset] = MockCandle(
                time=candles[pivot2_idx - offset].time,
                open=higher, high=higher + 0.0003, low=higher - 0.0003, close=higher - 0.0001,
            )
            candles[pivot2_idx + offset] = MockCandle(
                time=candles[pivot2_idx + offset].time,
                open=higher, high=higher + 0.0003, low=higher - 0.0003, close=higher - 0.0001,
            )

        # Last 2 candles close BELOW swing low
        for i in range(-2, 0):
            candles[i] = MockCandle(
                time=candles[i].time,
                open=pivot_low - 0.0001,
                high=pivot_low + 0.0001,
                low=pivot_low - 0.0008,
                close=pivot_low - 0.0005,
            )

        result = build_m5_understanding(candles=candles, bid=1.074, ask=1.0742)
        assert result.local_bos is True
        assert result.local_bos_direction == "BEARISH"

    def test_no_bos_remains_false(self):
        """Ranging/neutral candles with no BOS → local_bos=False."""
        from core.market_understanding.builders import build_m5_understanding

        # 55 flat candles — no swing structure to break
        candles = _make_candles(55, base=1.08, atr=0.0005)
        result = build_m5_understanding(candles=candles, bid=1.08, ask=1.0802)
        assert result.local_bos is False
        assert result.local_bos_direction == ""

    def test_insufficient_candles_remains_false(self):
        """Less than 50 candles → no BOS computation → False."""
        from core.market_understanding.builders import build_m5_understanding

        candles = _make_candles(30, base=1.08, atr=0.0005)
        result = build_m5_understanding(candles=candles, bid=1.08, ask=1.0802)
        assert result.local_bos is False


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIRMATION CANDLE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfirmationCandle:
    """Verify confirmation_candle detects strong directional candles."""

    def test_valid_bullish_confirmation(self):
        """Large bullish body (>60% range, >1.2x prev, >0.4 ATR) → True."""
        from core.market_understanding.builders import build_m5_understanding

        atr = 0.001  # 10 pips ATR
        candles = _make_candles(20, base=1.08, atr=atr)

        # Previous candle: small body
        candles[-2] = MockCandle(
            time=900, open=1.0800, high=1.0805, low=1.0795, close=1.0801,
        )  # body = 0.0001

        # Last candle: strong bullish
        # body=0.0008, range=0.0012 → ratio=0.667 ✓
        # prev_body=0.0001 → 0.0008 > 0.0001*1.2 ✓
        # 0.0008 > 0.4*0.001=0.0004 ✓
        candles[-1] = MockCandle(
            time=1200, open=1.0800, high=1.0812, low=1.0800, close=1.0808,
        )  # body=0.0008, range=0.0012

        result = build_m5_understanding(candles=candles, bid=1.0808, ask=1.0810)
        assert result.confirmation_candle is True

    def test_valid_bearish_confirmation(self):
        """Large bearish body (>60% range, >1.2x prev, >0.4 ATR) → True."""
        from core.market_understanding.builders import build_m5_understanding

        atr = 0.001
        candles = _make_candles(20, base=1.08, atr=atr)

        # Previous: small body
        candles[-2] = MockCandle(
            time=900, open=1.0800, high=1.0805, low=1.0795, close=1.0799,
        )  # body = 0.0001

        # Last: strong bearish
        # body=0.0007 (open > close), range=0.0012
        candles[-1] = MockCandle(
            time=1200, open=1.0807, high=1.0811, low=1.0799, close=1.0800,
        )  # body=0.0007, range=0.0012, ratio=0.58... need to adjust

        # Better: body=0.0008, range=0.0011
        candles[-1] = MockCandle(
            time=1200, open=1.0808, high=1.0810, low=1.0799, close=1.0800,
        )  # body=0.0008, range=0.0011, ratio=0.727 ✓

        result = build_m5_understanding(candles=candles, bid=1.08, ask=1.0802)
        assert result.confirmation_candle is True

    def test_weak_candle_rejected(self):
        """Small body candle (body < 60% range) → False."""
        from core.market_understanding.builders import build_m5_understanding

        atr = 0.001
        candles = _make_candles(20, base=1.08, atr=atr)

        # Previous: normal body
        candles[-2] = MockCandle(
            time=900, open=1.0800, high=1.0808, low=1.0795, close=1.0805,
        )  # body=0.0005

        # Last: doji-like (body << range)
        # body=0.0001, range=0.0010 → ratio=0.10 ✗
        candles[-1] = MockCandle(
            time=1200, open=1.0800, high=1.0805, low=1.0795, close=1.0801,
        )  # body=0.0001, range=0.0010

        result = build_m5_understanding(candles=candles, bid=1.08, ask=1.0802)
        assert result.confirmation_candle is False

    def test_candle_not_exceeding_previous_rejected(self):
        """Body not exceeding previous body × 1.2 → False."""
        from core.market_understanding.builders import build_m5_understanding

        atr = 0.001
        candles = _make_candles(20, base=1.08, atr=atr)

        # Previous: already large body
        candles[-2] = MockCandle(
            time=900, open=1.0800, high=1.0812, low=1.0798, close=1.0810,
        )  # body=0.0010

        # Last: same size body (doesn't exceed prev × 1.2)
        # body=0.0010, but needs > 0.0012 to pass
        candles[-1] = MockCandle(
            time=1200, open=1.0810, high=1.0822, low=1.0808, close=1.0820,
        )  # body=0.0010, range=0.0014, ratio=0.71 ✓ but body <= prev*1.2

        result = build_m5_understanding(candles=candles, bid=1.082, ask=1.0822)
        assert result.confirmation_candle is False

    def test_tiny_candle_below_atr_threshold_rejected(self):
        """Body below 0.4 × ATR → False even if body/range ratio is high."""
        from core.market_understanding.builders import build_m5_understanding

        atr = 0.002  # Large ATR (20 pips)
        candles = _make_candles(20, base=1.08, atr=atr)

        # Previous: tiny body
        candles[-2] = MockCandle(
            time=900, open=1.0800, high=1.0802, low=1.0798, close=1.0800,
        )  # body=0

        # Last: body=0.0003, range=0.0004 → ratio=0.75 ✓
        # but body=0.0003 < 0.4*0.002=0.0008 ✗
        candles[-1] = MockCandle(
            time=1200, open=1.0800, high=1.0803, low=1.0799, close=1.0803,
        )  # body=0.0003

        result = build_m5_understanding(candles=candles, bid=1.08, ask=1.0802)
        assert result.confirmation_candle is False
