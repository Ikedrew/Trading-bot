"""Replay parity validation: legacy evaluate() vs new registry-based evaluate()."""

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.mt5_data import Candle
from strategy.signals import Side, Signal
from patterns.registry import load_all_patterns

load_all_patterns()


def make_candle(t, o, h, l, c):
    return Candle(time=t, open=o, high=h, low=l, close=c, tick_volume=0)


# ─── LEGACY evaluate() (preserved for parity) ────────────────────────────────

def _legacy_scan_two_bar(candles):
    out = []
    n = len(candles)
    for i in range(1, n):
        prev, last = candles[i - 1], candles[i]
        if prev.close < prev.open and last.close > last.open and last.open < prev.close and last.close > prev.open:
            out.append(Signal("BULLISH_ENGULFING", Side.BUY, i, last.time))
        if prev.close > prev.open and last.close < last.open and last.open > prev.close and last.close < prev.open:
            out.append(Signal("BEARISH_ENGULFING", Side.SELL, i, last.time))
        body_prev = abs(prev.close - prev.open)
        if body_prev < 1e-12:
            continue
        if prev.close > prev.open and last.close < last.open and abs(prev.high - last.high) < 0.001:
            out.append(Signal("TWEEZER_TOP", Side.SELL, i, last.time))
        if prev.close < prev.open and last.close > last.open and abs(prev.low - last.low) < 0.001:
            out.append(Signal("TWEEZER_BOTTOM", Side.BUY, i, last.time))
    return out


def _legacy_scan_three_bar(candles):
    out = []
    n = len(candles)
    for i in range(2, n):
        first, second, third = candles[i - 2], candles[i - 1], candles[i]
        if first.close < first.open and abs(second.close - second.open) < (second.high - second.low) * 0.3 and third.close > third.open and third.close > second.open:
            out.append(Signal("MORNING_STAR", Side.BUY, i, third.time))
        if first.close > first.open and abs(second.close - second.open) < (second.high - second.low) * 0.3 and third.close < third.open and third.close < second.open:
            out.append(Signal("EVENING_STAR", Side.SELL, i, third.time))
        if first.close > first.open and second.close > second.open and third.close > third.open and second.close > first.close and third.close > second.close:
            out.append(Signal("THREE_WHITE_SOLDIERS", Side.BUY, i, third.time))
        if first.close < first.open and second.close < second.open and third.close < third.open and second.close < first.close and third.close < second.close:
            out.append(Signal("THREE_BLACK_CROWS", Side.SELL, i, third.time))
        if first.close < first.open and second.close > second.open and second.low > first.low and second.high < first.high and third.close > third.open and third.close > second.open:
            out.append(Signal("THREE_INSIDE_UP", Side.BUY, i, third.time))
        if first.close > first.open and second.close < second.open and second.low > first.low and second.high < first.high and third.close < third.open and third.close < second.open:
            out.append(Signal("THREE_INSIDE_DOWN", Side.SELL, i, third.time))
    return out


def _dedupe(signals):
    seen = set()
    out = []
    for s in sorted(signals, key=lambda x: (x.bar_index, x.pattern)):
        key = (s.bar_index, s.side)
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def legacy_evaluate(candles):
    seq = list(candles)
    if len(seq) < 3:
        return []
    merged = _legacy_scan_two_bar(seq) + _legacy_scan_three_bar(seq)
    return _dedupe(merged)


# ─── NEW evaluate() (from strategy/signal_orchestrator.py) ───────────────────

from strategy.signal_orchestrator import evaluate as new_evaluate


# ─── TEST DATA ────────────────────────────────────────────────────────────────

test_sequences = [
    # Three white soldiers
    [make_candle(1, 1.00, 1.02, 1.00, 1.02), make_candle(2, 1.02, 1.04, 1.02, 1.04), make_candle(3, 1.04, 1.06, 1.04, 1.06)],
    # Three black crows
    [make_candle(1, 1.06, 1.06, 1.04, 1.04), make_candle(2, 1.04, 1.04, 1.02, 1.02), make_candle(3, 1.02, 1.02, 1.00, 1.00)],
    # Engulfing + morning star sequence
    [make_candle(1, 1.10, 1.10, 1.06, 1.06), make_candle(2, 1.06, 1.07, 1.05, 1.0605), make_candle(3, 1.06, 1.10, 1.06, 1.09), make_candle(4, 1.08, 1.08, 1.04, 1.04), make_candle(5, 1.03, 1.10, 1.03, 1.10)],
    # Mixed: engulfing at bar 2, soldiers at bar 4
    [make_candle(1, 1.10, 1.10, 1.08, 1.08), make_candle(2, 1.07, 1.12, 1.07, 1.12), make_candle(3, 1.12, 1.14, 1.12, 1.14), make_candle(4, 1.14, 1.16, 1.14, 1.16)],
    # No patterns (flat)
    [make_candle(1, 1.10, 1.10, 1.10, 1.10), make_candle(2, 1.10, 1.10, 1.10, 1.10), make_candle(3, 1.10, 1.10, 1.10, 1.10)],
    # Longer sequence with multiple patterns
    [make_candle(i, 1.0 + i*0.01, 1.0 + i*0.01 + 0.005, 1.0 + i*0.01 - 0.002, 1.0 + (i+1)*0.01) for i in range(10)],
]


# ─── PARITY CHECK ─────────────────────────────────────────────────────────────

def normalize(signals):
    return sorted([(s.pattern, s.side.name, s.bar_index, s.bar_time) for s in signals])


passed = 0
failed = 0

for i, candles in enumerate(test_sequences):
    old = legacy_evaluate(candles)
    new = new_evaluate(candles)

    old_norm = normalize(old)
    new_norm = normalize(new)

    if old_norm == new_norm:
        passed += 1
    else:
        failed += 1
        print(f"FAIL case={i} candles={len(candles)}")
        print(f"  OLD ({len(old)}): {old_norm}")
        print(f"  NEW ({len(new)}): {new_norm}")
        old_set = set(old_norm)
        new_set = set(new_norm)
        missing = old_set - new_set
        extra = new_set - old_set
        if missing:
            print(f"  MISSING from new: {missing}")
        if extra:
            print(f"  EXTRA in new: {extra}")

print(f"\nREPLAY PARITY RESULT: {passed} passed, {failed} failed")
if failed == 0:
    print("PASS — Legacy replay engine and registry replay engine produced identical outputs.")
else:
    print("FAIL — Replay mismatch detected.")


# ─── PYTEST-COMPATIBLE WRAPPER ────────────────────────────────────────────────

def test_evaluate_parity_all_sequences():
    """Pytest wrapper: verify legacy evaluate() matches registry-based evaluate() for all sequences."""
    for i, candles in enumerate(test_sequences):
        old = legacy_evaluate(candles)
        new = new_evaluate(candles)
        old_norm = normalize(old)
        new_norm = normalize(new)
        assert old_norm == new_norm, f"Replay parity mismatch case={i}: OLD={old_norm} NEW={new_norm}"
