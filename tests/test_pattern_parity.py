"""Parity validation: old inline engine vs new registry engine."""

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.mt5_data import Candle
from strategy.signals import Side, Signal
from patterns.registry import load_all_patterns, detect_all

# Ensure patterns are loaded
load_all_patterns()

# ─── OLD INLINE ENGINE (preserved for parity check) ──────────────────────────

def _old_scan_one_bar(candles):
    out = []
    for i, c in enumerate(candles):
        body = abs(c.close - c.open)
        upper_wick = c.high - max(c.open, c.close)
        lower_wick = min(c.open, c.close) - c.low
        total_range = c.high - c.low
        if total_range < 1e-12:
            continue
        if lower_wick > body * 2 and upper_wick < body and body / total_range < 0.4:
            if c.close > c.open:
                out.append(Signal("HAMMER", Side.BUY, i, c.time))
            else:
                out.append(Signal("HANGING_MAN", Side.SELL, i, c.time))
        if upper_wick > body * 2 and lower_wick < body and body / total_range < 0.4:
            if c.close > c.open:
                out.append(Signal("INVERTED_HAMMER", Side.BUY, i, c.time))
            else:
                out.append(Signal("SHOOTING_STAR", Side.SELL, i, c.time))
    return out


def _old_evaluate_closed_bar(candles, closed_index):
    seq = list(candles)
    if closed_index <= 0 or closed_index >= len(seq):
        return []
    out = []
    one_bar = _old_scan_one_bar([seq[closed_index]])
    for s in one_bar:
        out.append(Signal(s.pattern, s.side, closed_index, seq[closed_index].time))
    prev, last = seq[closed_index - 1], seq[closed_index]
    if prev.close < prev.open and last.close > last.open and last.open < prev.close and last.close > prev.open:
        out.append(Signal("BULLISH_ENGULFING", Side.BUY, closed_index, last.time))
    if prev.close > prev.open and last.close < last.open and last.open > prev.close and last.close < prev.open:
        out.append(Signal("BEARISH_ENGULFING", Side.SELL, closed_index, last.time))
    body_prev = abs(prev.close - prev.open)
    if body_prev >= 1e-12:
        if prev.close > prev.open and last.close < last.open and abs(prev.high - last.high) < 0.001:
            out.append(Signal("TWEEZER_TOP", Side.SELL, closed_index, last.time))
        if prev.close < prev.open and last.close > last.open and abs(prev.low - last.low) < 0.001:
            out.append(Signal("TWEEZER_BOTTOM", Side.BUY, closed_index, last.time))
    if closed_index >= 2:
        first, second, third = seq[closed_index - 2], seq[closed_index - 1], seq[closed_index]
        if first.close < first.open and abs(second.close - second.open) < (second.high - second.low) * 0.3 and third.close > third.open and third.close > second.open:
            out.append(Signal("MORNING_STAR", Side.BUY, closed_index, third.time))
        if first.close > first.open and abs(second.close - second.open) < (second.high - second.low) * 0.3 and third.close < third.open and third.close < second.open:
            out.append(Signal("EVENING_STAR", Side.SELL, closed_index, third.time))
        if first.close > first.open and second.close > second.open and third.close > third.open and second.close > first.close and third.close > second.close:
            out.append(Signal("THREE_WHITE_SOLDIERS", Side.BUY, closed_index, third.time))
        if first.close < first.open and second.close < second.open and third.close < third.open and second.close < first.close and third.close < second.close:
            out.append(Signal("THREE_BLACK_CROWS", Side.SELL, closed_index, third.time))
        if first.close < first.open and second.close > second.open and second.low > first.low and second.high < first.high and third.close > third.open and third.close > second.open:
            out.append(Signal("THREE_INSIDE_UP", Side.BUY, closed_index, third.time))
        if first.close > first.open and second.close < second.open and second.low > first.low and second.high < first.high and third.close < third.open and third.close < second.open:
            out.append(Signal("THREE_INSIDE_DOWN", Side.SELL, closed_index, third.time))
    # Dedupe (same logic)
    seen = set()
    deduped = []
    for s in sorted(out, key=lambda x: (x.bar_index, x.pattern)):
        key = (s.bar_index, s.side)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)
    return deduped


# ─── TEST CANDLE DATA ─────────────────────────────────────────────────────────

def make_candle(t, o, h, l, c):
    return Candle(time=t, open=o, high=h, low=l, close=c, tick_volume=0)


# Test cases covering all pattern types
test_cases = [
    # Hammer (bullish close, long lower wick)
    [make_candle(1, 1.10, 1.11, 1.05, 1.09), make_candle(2, 1.10, 1.105, 1.05, 1.104)],
    # Shooting star (bearish close, long upper wick)
    [make_candle(1, 1.10, 1.11, 1.09, 1.10), make_candle(2, 1.10, 1.16, 1.095, 1.096)],
    # Bullish engulfing
    [make_candle(1, 1.10, 1.10, 1.08, 1.08), make_candle(2, 1.07, 1.12, 1.07, 1.12)],
    # Bearish engulfing
    [make_candle(1, 1.08, 1.12, 1.08, 1.12), make_candle(2, 1.13, 1.13, 1.06, 1.06)],
    # Three white soldiers
    [make_candle(1, 1.00, 1.02, 1.00, 1.02), make_candle(2, 1.02, 1.04, 1.02, 1.04), make_candle(3, 1.04, 1.06, 1.04, 1.06)],
    # Three black crows
    [make_candle(1, 1.06, 1.06, 1.04, 1.04), make_candle(2, 1.04, 1.04, 1.02, 1.02), make_candle(3, 1.02, 1.02, 1.00, 1.00)],
    # Morning star
    [make_candle(1, 1.10, 1.10, 1.06, 1.06), make_candle(2, 1.06, 1.07, 1.05, 1.0605), make_candle(3, 1.06, 1.10, 1.06, 1.09)],
    # Evening star
    [make_candle(1, 1.06, 1.10, 1.06, 1.10), make_candle(2, 1.10, 1.11, 1.09, 1.1005), make_candle(3, 1.10, 1.10, 1.06, 1.07)],
    # No pattern (flat candles)
    [make_candle(1, 1.10, 1.10, 1.10, 1.10), make_candle(2, 1.10, 1.10, 1.10, 1.10), make_candle(3, 1.10, 1.10, 1.10, 1.10)],
]


# ─── PARITY CHECK ─────────────────────────────────────────────────────────────

def normalize(signals):
    """Sort signals for comparison."""
    return sorted([(s.pattern, s.side.name, s.bar_index) for s in signals])


passed = 0
failed = 0

for i, candles in enumerate(test_cases):
    for closed_i in range(1, len(candles)):
        old = _old_evaluate_closed_bar(candles, closed_i)
        new = detect_all(candles, closed_i)
        # Apply same deduplication to new (registry doesn't dedupe)
        seen = set()
        new_deduped = []
        for s in sorted(new, key=lambda x: (x.bar_index, x.pattern)):
            key = (s.bar_index, s.side)
            if key in seen:
                continue
            seen.add(key)
            new_deduped.append(s)

        old_norm = normalize(old)
        new_norm = normalize(new_deduped)

        if old_norm == new_norm:
            passed += 1
        else:
            failed += 1
            print(f"FAIL case={i} closed_i={closed_i}")
            print(f"  OLD: {old_norm}")
            print(f"  NEW: {new_norm}")
            old_set = set(old_norm)
            new_set = set(new_norm)
            missing = old_set - new_set
            extra = new_set - old_set
            if missing:
                print(f"  MISSING from new: {missing}")
            if extra:
                print(f"  EXTRA in new: {extra}")

print(f"\nPARITY RESULT: {passed} passed, {failed} failed")
if failed == 0:
    print("PASS — Old engine and registry engine produced identical outputs.")
else:
    print("FAIL — Mismatch detected.")


# ─── PYTEST-COMPATIBLE WRAPPER ────────────────────────────────────────────────

def test_parity_all_cases():
    """Pytest wrapper: verify old inline engine matches registry engine for all test cases."""
    for i, candles in enumerate(test_cases):
        for closed_i in range(1, len(candles)):
            old = _old_evaluate_closed_bar(candles, closed_i)
            new = detect_all(candles, closed_i)
            # Apply same deduplication to new
            seen = set()
            new_deduped = []
            for s in sorted(new, key=lambda x: (x.bar_index, x.pattern)):
                key = (s.bar_index, s.side)
                if key in seen:
                    continue
                seen.add(key)
                new_deduped.append(s)

            old_norm = normalize(old)
            new_norm = normalize(new_deduped)
            assert old_norm == new_norm, f"Parity mismatch case={i} closed_i={closed_i}: OLD={old_norm} NEW={new_norm}"
