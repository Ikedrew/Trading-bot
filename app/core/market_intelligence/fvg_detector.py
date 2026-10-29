"""
Fair Value Gap Detector — Objective 3-candle imbalance detection.

Definitions:
    Bullish FVG: Candle[0].high < Candle[2].low (gap between C0 high and C2 low)
    Bearish FVG: Candle[0].low > Candle[2].high (gap between C2 high and C0 low)

All rules are explicit and measurable. No subjective interpretation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Minimum FVG size as fraction of ATR
MIN_FVG_ATR_RATIO = 0.3

# Maximum age before FVG expires (M5 bars)
MAX_FVG_AGE_BARS = 100

# Maximum active FVGs tracked per direction
MAX_ACTIVE_FVGS = 5


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class FairValueGap:
    """A single detected FVG."""
    direction: str  # "BULLISH" or "BEARISH"
    top: float      # Upper boundary of gap
    bottom: float   # Lower boundary of gap
    size: float     # top - bottom
    midpoint: float
    creation_bar_index: int
    filled_pct: float = 0.0  # 0.0 = untouched, 1.0 = fully filled


@dataclass
class FVGSnapshot:
    """Complete FVG state at observation time."""
    # Nearest bullish FVG above current price
    nearest_fvg_above_price: float = 0.0
    nearest_fvg_above_distance_pips: float = 0.0
    fvg_above_filled_pct: float = 0.0
    fvg_above_size_atr: float = 0.0

    # Nearest bearish FVG below current price
    nearest_fvg_below_price: float = 0.0
    nearest_fvg_below_distance_pips: float = 0.0
    fvg_below_filled_pct: float = 0.0
    fvg_below_size_atr: float = 0.0

    # Current state
    price_inside_fvg: bool = False
    fvg_direction_if_inside: str = ""
    total_unfilled_fvgs_above: int = 0
    total_unfilled_fvgs_below: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════


def detect_fvgs(
    candles: list,
    current_price: float,
    closed_index: int,
    atr: float,
    symbol: str = "EURUSD",
    *,
    lookback: int = MAX_FVG_AGE_BARS,
    min_atr_ratio: float = MIN_FVG_ATR_RATIO,
) -> FVGSnapshot:
    """
    Detect Fair Value Gaps from candle data.

    Args:
        candles: M5 candle history
        current_price: Current mid price
        closed_index: Index of last closed bar
        atr: Current ATR in price units
        symbol: For pip size
        lookback: How many bars to scan
        min_atr_ratio: Minimum FVG size as fraction of ATR

    Returns:
        FVGSnapshot with all FVG features.
    """
    if not candles or closed_index < 5 or current_price <= 0 or atr <= 0:
        return FVGSnapshot()

    pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
    min_size = atr * min_atr_ratio

    start = max(0, closed_index - lookback)
    window = candles[start:closed_index + 1]

    if len(window) < 5:
        return FVGSnapshot()

    # Detect all FVGs in window
    fvgs: list[FairValueGap] = []

    for i in range(len(window) - 2):
        c0 = window[i]
        c2 = window[i + 2]

        # Bullish FVG: gap between C0.high and C2.low
        if c2.low > c0.high:
            gap_size = c2.low - c0.high
            if gap_size >= min_size:
                fvgs.append(FairValueGap(
                    direction="BULLISH",
                    top=c2.low,
                    bottom=c0.high,
                    size=gap_size,
                    midpoint=(c0.high + c2.low) / 2,
                    creation_bar_index=i + start,
                ))

        # Bearish FVG: gap between C2.high and C0.low
        if c0.low > c2.high:
            gap_size = c0.low - c2.high
            if gap_size >= min_size:
                fvgs.append(FairValueGap(
                    direction="BEARISH",
                    top=c0.low,
                    bottom=c2.high,
                    size=gap_size,
                    midpoint=(c2.high + c0.low) / 2,
                    creation_bar_index=i + start,
                ))

    # Update fill percentages based on subsequent price action
    for fvg in fvgs:
        _update_fill(fvg, candles, fvg.creation_bar_index, closed_index)

    # Filter: remove fully filled and expired
    active_fvgs = [
        f for f in fvgs
        if f.filled_pct < 1.0
        and (closed_index - f.creation_bar_index) <= MAX_FVG_AGE_BARS
    ]

    # Separate above/below current price
    fvgs_above = [f for f in active_fvgs if f.midpoint > current_price]
    fvgs_below = [f for f in active_fvgs if f.midpoint <= current_price]

    # Find nearest
    nearest_above = min(fvgs_above, key=lambda f: f.midpoint - current_price) if fvgs_above else None
    nearest_below = max(fvgs_below, key=lambda f: f.midpoint) if fvgs_below else None

    # Check if price is inside any FVG
    inside_fvg = False
    inside_direction = ""
    for fvg in active_fvgs:
        if fvg.bottom <= current_price <= fvg.top:
            inside_fvg = True
            inside_direction = fvg.direction
            break

    return FVGSnapshot(
        nearest_fvg_above_price=nearest_above.midpoint if nearest_above else 0.0,
        nearest_fvg_above_distance_pips=round((nearest_above.bottom - current_price) / pip_size, 2) if nearest_above else 0.0,
        fvg_above_filled_pct=round(nearest_above.filled_pct, 4) if nearest_above else 0.0,
        fvg_above_size_atr=round(nearest_above.size / atr, 4) if nearest_above else 0.0,
        nearest_fvg_below_price=nearest_below.midpoint if nearest_below else 0.0,
        nearest_fvg_below_distance_pips=round((current_price - nearest_below.top) / pip_size, 2) if nearest_below else 0.0,
        fvg_below_filled_pct=round(nearest_below.filled_pct, 4) if nearest_below else 0.0,
        fvg_below_size_atr=round(nearest_below.size / atr, 4) if nearest_below else 0.0,
        price_inside_fvg=inside_fvg,
        fvg_direction_if_inside=inside_direction,
        total_unfilled_fvgs_above=len(fvgs_above),
        total_unfilled_fvgs_below=len(fvgs_below),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL
# ═══════════════════════════════════════════════════════════════════════════════


def _update_fill(fvg: FairValueGap, candles: list, creation_idx: int, closed_idx: int) -> None:
    """Update FVG fill percentage based on subsequent candles."""
    if fvg.size <= 0:
        fvg.filled_pct = 1.0
        return

    max_penetration = 0.0

    for i in range(creation_idx + 3, min(closed_idx + 1, len(candles))):
        bar = candles[i]
        if fvg.direction == "BULLISH":
            # Bullish FVG filled from above (price comes down into it)
            if bar.low < fvg.top:
                penetration = fvg.top - max(bar.low, fvg.bottom)
                max_penetration = max(max_penetration, penetration)
        else:
            # Bearish FVG filled from below (price comes up into it)
            if bar.high > fvg.bottom:
                penetration = min(bar.high, fvg.top) - fvg.bottom
                max_penetration = max(max_penetration, penetration)

    fvg.filled_pct = min(1.0, max_penetration / fvg.size)
