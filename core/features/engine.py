"""
Feature Engine — Pure market signal computation from M5 candles + tick data.

This module is the ONLY source of M5-derived structure/volatility/execution signals.
It NEVER reads EngineState, FSM counters, or scoring outputs.
It NEVER mutates any state.
It is a pure function: same inputs → same outputs.

Ownership: core/features/engine.py
Called by: process_bar() after State Preparation, before Snapshot creation
"""

from __future__ import annotations

from data.mt5_data import Candle
from core.features.bundle import FeatureBundle


# ─── ATR COMPUTATION (Wilder smoothing) ───────────────────────────────────────

def _compute_atr(candles: list[Candle], period: int = 14) -> float:
    """Compute ATR at the last bar using Wilder smoothing. Returns 0.0 if insufficient data."""
    if len(candles) < 2:
        return 0.0

    tr_values: list[float] = [candles[0].high - candles[0].low]
    for i in range(1, len(candles)):
        high = candles[i].high
        low = candles[i].low
        prev_close = candles[i - 1].close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_values.append(tr)

    if len(tr_values) < period:
        return sum(tr_values) / len(tr_values) if tr_values else 0.0

    # Wilder smoothing
    atr = sum(tr_values[:period]) / period
    for tr in tr_values[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _compute_atr_ratio(candles: list[Candle], period: int = 14, avg_window: int = 50) -> float:
    """Compute ATR ratio: current ATR / rolling average ATR over avg_window bars."""
    if len(candles) < period + 1:
        return 1.0

    # Compute ATR series
    tr_values: list[float] = [candles[0].high - candles[0].low]
    for i in range(1, len(candles)):
        high = candles[i].high
        low = candles[i].low
        prev_close = candles[i - 1].close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_values.append(tr)

    if len(tr_values) < period:
        return 1.0

    atr_series: list[float] = []
    atr = sum(tr_values[:period]) / period
    for i, tr in enumerate(tr_values):
        if i < period:
            atr = sum(tr_values[:i + 1]) / (i + 1)
        else:
            atr = (atr * (period - 1) + tr) / period
        atr_series.append(atr)

    current_atr = atr_series[-1]
    window = min(avg_window, len(atr_series))
    avg_atr = sum(atr_series[-window:]) / window if window > 0 else current_atr

    return current_atr / avg_atr if avg_atr > 0 else 1.0


# ─── CANDLE OVERLAP RATIO ─────────────────────────────────────────────────────

def _compute_overlap_ratio(candles: list[Candle], lookback: int = 5) -> float:
    """
    Compute adjacent candle overlap fraction over last N bars.
    Returns 0.0–1.0 where 1.0 = all candles fully overlap (maximum chop).
    """
    if len(candles) < lookback + 1:
        return 0.0

    window = candles[-(lookback):]
    if len(window) < 2:
        return 0.0

    overlap_hits = 0
    pairs = len(window) - 1

    for i in range(1, len(window)):
        prev = window[i - 1]
        cur = window[i]

        overlap = min(prev.high, cur.high) - max(prev.low, cur.low)
        if overlap <= 0:
            continue

        prev_range = prev.high - prev.low
        cur_range = cur.high - cur.low
        denom = min(prev_range, cur_range)
        if denom <= 0:
            continue

        if overlap / denom >= 0.5:
            overlap_hits += 1

    return overlap_hits / pairs if pairs > 0 else 0.0


# ─── SWING DETECTION (2-bar L/R confirmation) ─────────────────────────────────

def _count_swing_highs(candles: list[Candle], lookback: int = 20) -> int:
    """Count swing highs in last N bars using 2-bar left/right confirmation."""
    if len(candles) < lookback:
        window = candles
    else:
        window = candles[-lookback:]

    count = 0
    for i in range(2, len(window) - 2):
        if (window[i].high > window[i - 1].high and
            window[i].high > window[i - 2].high and
            window[i].high > window[i + 1].high and
            window[i].high > window[i + 2].high):
            count += 1
    return count


def _count_swing_lows(candles: list[Candle], lookback: int = 20) -> int:
    """Count swing lows in last N bars using 2-bar left/right confirmation."""
    if len(candles) < lookback:
        window = candles
    else:
        window = candles[-lookback:]

    count = 0
    for i in range(2, len(window) - 2):
        if (window[i].low < window[i - 1].low and
            window[i].low < window[i - 2].low and
            window[i].low < window[i + 1].low and
            window[i].low < window[i + 2].low):
            count += 1
    return count


# ─── STRUCTURE CLARITY ─────────────────────────────────────────────────────────

def _compute_structure_clarity(candles: list[Candle], lookback: int = 20) -> float:
    """
    Compute structure clarity: average swing amplitude normalized by ATR.
    Returns 0.0–1.0 where 1.0 = very clear swings relative to noise.
    """
    if len(candles) < lookback:
        window = candles
    else:
        window = candles[-lookback:]

    if len(window) < 5:
        return 0.0

    # Find swing highs and lows
    swing_highs: list[float] = []
    swing_lows: list[float] = []

    for i in range(2, len(window) - 2):
        if (window[i].high > window[i - 1].high and
            window[i].high > window[i - 2].high and
            window[i].high > window[i + 1].high and
            window[i].high > window[i + 2].high):
            swing_highs.append(window[i].high)
        if (window[i].low < window[i - 1].low and
            window[i].low < window[i - 2].low and
            window[i].low < window[i + 1].low and
            window[i].low < window[i + 2].low):
            swing_lows.append(window[i].low)

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return 0.2  # Insufficient structure

    # Average swing amplitude
    high_diffs = [abs(swing_highs[i] - swing_highs[i - 1]) for i in range(1, len(swing_highs))]
    low_diffs = [abs(swing_lows[i] - swing_lows[i - 1]) for i in range(1, len(swing_lows))]

    avg_swing = (sum(high_diffs) / len(high_diffs) + sum(low_diffs) / len(low_diffs)) / 2.0

    # ATR for normalization
    atr = _compute_atr(window, min(14, len(window) - 1))
    if atr <= 0:
        return 0.0

    normalized = avg_swing / atr

    # Map to 0.0–1.0
    if normalized > 2.0:
        return 0.9
    elif normalized > 1.0:
        return 0.6 + (normalized - 1.0) * 0.3
    elif normalized > 0.5:
        return 0.3 + (normalized - 0.5) * 0.6
    else:
        return normalized * 0.6


# ─── SWEEP DETECTION ──────────────────────────────────────────────────────────

def _detect_sweeps(candles: list[Candle], lookback: int = 5) -> tuple[float | None, float | None]:
    """
    Detect liquidity sweeps: wick beyond recent swing high/low.
    Returns (sweep_high_price, sweep_low_price) or None if no sweep.
    """
    if len(candles) < lookback + 1:
        return None, None

    window = candles[-(lookback + 1):-1]  # lookback bars before current
    current = candles[-1]

    max_high = max(c.high for c in window)
    min_low = min(c.low for c in window)

    sweep_high = current.high if current.high > max_high else None
    sweep_low = current.low if current.low < min_low else None

    return sweep_high, sweep_low


# ─── MAIN ENTRY POINT ─────────────────────────────────────────────────────────

def compute_features(
    candles: list[Candle],
    closed_i: int,
    bid: float,
    ask: float,
    *,
    symbol: str = "",
) -> FeatureBundle:
    """
    Compute all market-derived features from M5 candles + tick data.

    This is a PURE FUNCTION:
      - No EngineState access
      - No FSM counters
      - No scoring outputs
      - No side effects (except structured event emission)
      - Deterministic: same inputs → same outputs

    Args:
        candles: Full M5 candle array
        closed_i: Index of last closed bar
        bid: Current bid price
        ask: Current ask price
        symbol: Trading symbol (for event emission; optional for backward compat)

    Returns:
        Frozen FeatureBundle with all computed features.
    """
    # Slice to closed bar (inclusive)
    bars = candles[:closed_i + 1] if closed_i < len(candles) else candles

    # Volatility features
    m5_atr_14 = _compute_atr(bars, 14)
    m5_atr_ratio = _compute_atr_ratio(bars, 14, 50)
    overlap_ratio = _compute_overlap_ratio(bars, 5)

    # Execution features
    spread = ask - bid

    # Structure features
    swing_highs = _count_swing_highs(bars, 20)
    swing_lows = _count_swing_lows(bars, 20)
    clarity = _compute_structure_clarity(bars, 20)

    # Liquidity features
    sweep_high, sweep_low = _detect_sweeps(bars, 5)

    bundle = FeatureBundle(
        m5_atr_14=round(m5_atr_14, 8),
        m5_atr_ratio=round(m5_atr_ratio, 4),
        candle_overlap_ratio=round(overlap_ratio, 4),
        spread=round(spread, 8),
        m5_swing_high_count=swing_highs,
        m5_swing_low_count=swing_lows,
        m5_structure_clarity=round(clarity, 4),
        last_sweep_high=sweep_high,
        last_sweep_low=sweep_low,
    )

    # ─── UNIFIED EVENT STREAM: FEATURE_UPDATE (Layer 2) ───────────────
    # DESIGN: Only emit when features MATERIALLY change from previous state.
    # This prevents per-tick noise. A material change = state transition
    # that could influence downstream decisions.
    if symbol:
        try:
            from core.event_stream import emit_feature_update
            _changed_fields = _detect_material_change(symbol, bundle)
            if _changed_fields:
                emit_feature_update(symbol, {
                    "atr_14": bundle.m5_atr_14,
                    "atr_ratio": bundle.m5_atr_ratio,
                    "overlap_ratio": bundle.candle_overlap_ratio,
                    "spread": bundle.spread,
                    "swing_high_count": bundle.m5_swing_high_count,
                    "swing_low_count": bundle.m5_swing_low_count,
                    "structure_clarity": bundle.m5_structure_clarity,
                    "sweep_high": bundle.last_sweep_high,
                    "sweep_low": bundle.last_sweep_low,
                    "closed_i": closed_i,
                    "trigger": "bar_close",
                    "material_changes": _changed_fields,
                })
        except Exception:
            pass  # Event emission must never affect feature computation
    # ─── END UNIFIED EVENT STREAM ─────────────────────────────────────

    return bundle


# ─── MATERIALITY GATE (per-symbol previous-state cache) ───────────────────────
# Only emit FEATURE_UPDATE when features change beyond noise thresholds.
# This ensures the event stream captures state transitions, not repetitive values.

_PREVIOUS_FEATURES: dict[str, dict[str, float | int | None]] = {}

# Thresholds: feature must change by at least this much to count as material.
_MATERIALITY_THRESHOLDS: dict[str, float] = {
    "atr_ratio": 0.10,           # 10% relative change in volatility regime
    "overlap_ratio": 0.15,       # 15% absolute change in chop detection
    "structure_clarity": 0.10,   # 10% absolute change in structure readability
    "spread": 0.0,               # Always track spread (impacts execution cost)
}


def _detect_material_change(symbol: str, bundle: FeatureBundle) -> list[str]:
    """
    Compare current features to previous emission for this symbol.
    Returns list of field names that changed materially.
    Empty list = no event should be emitted.
    """
    current = {
        "atr_14": bundle.m5_atr_14,
        "atr_ratio": bundle.m5_atr_ratio,
        "overlap_ratio": bundle.candle_overlap_ratio,
        "spread": bundle.spread,
        "swing_high_count": bundle.m5_swing_high_count,
        "swing_low_count": bundle.m5_swing_low_count,
        "structure_clarity": bundle.m5_structure_clarity,
        "sweep_high": bundle.last_sweep_high,
        "sweep_low": bundle.last_sweep_low,
    }

    prev = _PREVIOUS_FEATURES.get(symbol)
    if prev is None:
        # First computation for this symbol — always emit
        _PREVIOUS_FEATURES[symbol] = current
        return ["initial_computation"]

    changed: list[str] = []

    # ATR ratio: volatility regime shift
    if abs((current["atr_ratio"] or 0) - (prev.get("atr_ratio") or 0)) >= _MATERIALITY_THRESHOLDS["atr_ratio"]:
        changed.append("atr_ratio")

    # Overlap ratio: chop regime change
    if abs((current["overlap_ratio"] or 0) - (prev.get("overlap_ratio") or 0)) >= _MATERIALITY_THRESHOLDS["overlap_ratio"]:
        changed.append("overlap_ratio")

    # Structure clarity: readability shift
    if abs((current["structure_clarity"] or 0) - (prev.get("structure_clarity") or 0)) >= _MATERIALITY_THRESHOLDS["structure_clarity"]:
        changed.append("structure_clarity")

    # Swing counts changed (discrete — any change is material)
    if current["swing_high_count"] != prev.get("swing_high_count"):
        changed.append("swing_high_count")
    if current["swing_low_count"] != prev.get("swing_low_count"):
        changed.append("swing_low_count")

    # Sweep events (None → value = new sweep detected)
    if current["sweep_high"] is not None and prev.get("sweep_high") is None:
        changed.append("sweep_high_detected")
    if current["sweep_low"] is not None and prev.get("sweep_low") is None:
        changed.append("sweep_low_detected")

    if changed:
        _PREVIOUS_FEATURES[symbol] = current

    return changed
