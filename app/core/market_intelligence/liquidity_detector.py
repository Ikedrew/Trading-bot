"""
Liquidity Detector — Objective detection of liquidity pools and sweeps.

Detects:
    1. Equal Highs — multiple swing highs within tolerance (liquidity above)
    2. Equal Lows — multiple swing lows within tolerance (liquidity below)
    3. Previous Day High/Low — prior day extremes as liquidity levels
    4. Previous Session High/Low — prior session extremes
    5. Liquidity Sweeps — price exceeds pool then closes back inside

All definitions are explicit and measurable. No subjective interpretation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Equal highs/lows tolerance in pips
DEFAULT_TOLERANCE_PIPS = 3.0

# Minimum bars between touches to count as separate touches
MIN_TOUCH_SEPARATION = 5

# Minimum touches to form a liquidity pool
MIN_TOUCHES = 2

# Maximum age of a liquidity pool (M5 bars)
MAX_POOL_AGE_BARS = 200

# Sweep: max bars for close-back after exceedance
SWEEP_CLOSE_BACK_WINDOW = 3

# Session boundaries (UTC hours)
SESSION_BOUNDARIES = {
    "ASIA": (0, 7),
    "LONDON": (7, 12),
    "NY": (12, 17),
    "OFF": (17, 24),
}


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class LiquidityPool:
    """A detected liquidity pool (cluster of equal levels)."""
    level: float
    direction: str  # "ABOVE" (equal highs) or "BELOW" (equal lows)
    touches: int
    first_bar_index: int
    last_bar_index: int


@dataclass
class LiquiditySweep:
    """A detected sweep of a liquidity pool."""
    direction: str  # "BULLISH" (swept lows) or "BEARISH" (swept highs)
    swept_level: float
    exceedance_distance: float  # how far past the level
    bar_index: int


@dataclass
class SessionExtremes:
    """Previous session/day extremes."""
    prev_day_high: float = 0.0
    prev_day_low: float = 0.0
    prev_session_high: float = 0.0
    prev_session_low: float = 0.0
    prev_session_name: str = ""


@dataclass
class LiquiditySnapshot:
    """Complete liquidity state at observation time."""
    # Equal highs above
    equal_highs_above: bool = False
    equal_highs_price: float = 0.0
    equal_highs_distance_pips: float = 0.0
    equal_highs_count: int = 0

    # Equal lows below
    equal_lows_below: bool = False
    equal_lows_price: float = 0.0
    equal_lows_distance_pips: float = 0.0
    equal_lows_count: int = 0

    # Previous day
    prev_day_high: float = 0.0
    prev_day_low: float = 0.0
    distance_to_prev_day_high_pips: float = 0.0
    distance_to_prev_day_low_pips: float = 0.0
    prev_day_high_swept: bool = False
    prev_day_low_swept: bool = False

    # Previous session
    prev_session_high: float = 0.0
    prev_session_low: float = 0.0
    prev_session_name: str = ""
    distance_to_prev_session_high_pips: float = 0.0
    distance_to_prev_session_low_pips: float = 0.0
    prev_session_high_swept: bool = False
    prev_session_low_swept: bool = False

    # Sweep
    liquidity_sweep_just_occurred: bool = False
    sweep_direction: str = ""
    sweep_distance_pips: float = 0.0
    bars_since_sweep: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════


def detect_liquidity(
    candles: list,
    current_price: float,
    closed_index: int,
    symbol: str = "EURUSD",
    *,
    tolerance_pips: float = DEFAULT_TOLERANCE_PIPS,
    lookback: int = MAX_POOL_AGE_BARS,
) -> LiquiditySnapshot:
    """
    Detect all liquidity features from candle data.

    Args:
        candles: M5 candle history (must have .high, .low, .time attributes)
        current_price: Current mid price
        closed_index: Index of last closed bar
        symbol: For pip size determination
        tolerance_pips: Pip tolerance for equal level detection
        lookback: How many bars to scan

    Returns:
        LiquiditySnapshot with all liquidity features.
    """
    if not candles or closed_index < 10 or current_price <= 0:
        return LiquiditySnapshot()

    pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
    tolerance = tolerance_pips * pip_size

    # Window for analysis
    start = max(0, closed_index - lookback)
    window = candles[start:closed_index + 1]

    if len(window) < 10:
        return LiquiditySnapshot()

    # Detect equal highs/lows
    pools_above, pools_below = _detect_equal_levels(window, current_price, tolerance)

    # Find nearest pools
    eq_highs_above = False
    eq_highs_price = 0.0
    eq_highs_count = 0
    eq_highs_dist = 0.0

    if pools_above:
        nearest_above = min(pools_above, key=lambda p: p.level - current_price)
        eq_highs_above = True
        eq_highs_price = nearest_above.level
        eq_highs_count = nearest_above.touches
        eq_highs_dist = (nearest_above.level - current_price) / pip_size

    eq_lows_below = False
    eq_lows_price = 0.0
    eq_lows_count = 0
    eq_lows_dist = 0.0

    if pools_below:
        nearest_below = max(pools_below, key=lambda p: p.level)
        eq_lows_below = True
        eq_lows_price = nearest_below.level
        eq_lows_count = nearest_below.touches
        eq_lows_dist = (current_price - nearest_below.level) / pip_size

    # Detect session/day extremes
    extremes = _compute_session_extremes(candles, closed_index)

    dist_pdh = abs(current_price - extremes.prev_day_high) / pip_size if extremes.prev_day_high > 0 else 0.0
    dist_pdl = abs(current_price - extremes.prev_day_low) / pip_size if extremes.prev_day_low > 0 else 0.0
    dist_psh = abs(current_price - extremes.prev_session_high) / pip_size if extremes.prev_session_high > 0 else 0.0
    dist_psl = abs(current_price - extremes.prev_session_low) / pip_size if extremes.prev_session_low > 0 else 0.0

    # Detect sweeps
    sweep = _detect_sweep(
        window, current_price, closed_index - start,
        pools_above, pools_below, extremes, tolerance
    )

    # Check day/session sweep status
    pdh_swept = False
    pdl_swept = False
    psh_swept = False
    psl_swept = False

    if extremes.prev_day_high > 0:
        pdh_swept = any(c.high > extremes.prev_day_high + tolerance for c in window[-20:])
    if extremes.prev_day_low > 0:
        pdl_swept = any(c.low < extremes.prev_day_low - tolerance for c in window[-20:])
    if extremes.prev_session_high > 0:
        psh_swept = any(c.high > extremes.prev_session_high + tolerance for c in window[-20:])
    if extremes.prev_session_low > 0:
        psl_swept = any(c.low < extremes.prev_session_low - tolerance for c in window[-20:])

    return LiquiditySnapshot(
        equal_highs_above=eq_highs_above,
        equal_highs_price=round(eq_highs_price, 8),
        equal_highs_distance_pips=round(eq_highs_dist, 2),
        equal_highs_count=eq_highs_count,
        equal_lows_below=eq_lows_below,
        equal_lows_price=round(eq_lows_price, 8),
        equal_lows_distance_pips=round(eq_lows_dist, 2),
        equal_lows_count=eq_lows_count,
        prev_day_high=extremes.prev_day_high,
        prev_day_low=extremes.prev_day_low,
        distance_to_prev_day_high_pips=round(dist_pdh, 2),
        distance_to_prev_day_low_pips=round(dist_pdl, 2),
        prev_day_high_swept=pdh_swept,
        prev_day_low_swept=pdl_swept,
        prev_session_high=extremes.prev_session_high,
        prev_session_low=extremes.prev_session_low,
        prev_session_name=extremes.prev_session_name,
        distance_to_prev_session_high_pips=round(dist_psh, 2),
        distance_to_prev_session_low_pips=round(dist_psl, 2),
        prev_session_high_swept=psh_swept,
        prev_session_low_swept=psl_swept,
        liquidity_sweep_just_occurred=sweep is not None,
        sweep_direction=sweep.direction if sweep else "",
        sweep_distance_pips=round(sweep.exceedance_distance / pip_size, 2) if sweep else 0.0,
        bars_since_sweep=(closed_index - start - sweep.bar_index) if sweep else 0,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL — EQUAL LEVELS
# ═══════════════════════════════════════════════════════════════════════════════


def _detect_equal_levels(
    candles: list, current_price: float, tolerance: float
) -> tuple[list[LiquidityPool], list[LiquidityPool]]:
    """Detect equal highs (above price) and equal lows (below price)."""
    # Collect swing highs and lows (simple 1-bar pivots)
    swing_highs: list[tuple[int, float]] = []  # (index, price)
    swing_lows: list[tuple[int, float]] = []

    for i in range(1, len(candles) - 1):
        if candles[i].high > candles[i-1].high and candles[i].high > candles[i+1].high:
            swing_highs.append((i, candles[i].high))
        if candles[i].low < candles[i-1].low and candles[i].low < candles[i+1].low:
            swing_lows.append((i, candles[i].low))

    # Cluster equal highs
    pools_above: list[LiquidityPool] = []
    used_h: set[int] = set()

    for i, (idx_i, price_i) in enumerate(swing_highs):
        if i in used_h:
            continue
        if price_i <= current_price:
            continue  # Only interested in levels above price

        cluster_indices = [i]
        cluster_prices = [price_i]
        cluster_bar_indices = [idx_i]

        for j, (idx_j, price_j) in enumerate(swing_highs):
            if j <= i or j in used_h:
                continue
            if abs(idx_j - idx_i) < MIN_TOUCH_SEPARATION:
                continue  # Too close — same swing, not separate touch
            if abs(price_j - price_i) <= tolerance:
                cluster_indices.append(j)
                cluster_prices.append(price_j)
                cluster_bar_indices.append(idx_j)

        if len(cluster_prices) >= MIN_TOUCHES:
            for ci in cluster_indices:
                used_h.add(ci)
            level = sum(cluster_prices) / len(cluster_prices)
            pools_above.append(LiquidityPool(
                level=level,
                direction="ABOVE",
                touches=len(cluster_prices),
                first_bar_index=min(cluster_bar_indices),
                last_bar_index=max(cluster_bar_indices),
            ))

    # Cluster equal lows
    pools_below: list[LiquidityPool] = []
    used_l: set[int] = set()

    for i, (idx_i, price_i) in enumerate(swing_lows):
        if i in used_l:
            continue
        if price_i >= current_price:
            continue  # Only interested in levels below price

        cluster_indices = [i]
        cluster_prices = [price_i]
        cluster_bar_indices = [idx_i]

        for j, (idx_j, price_j) in enumerate(swing_lows):
            if j <= i or j in used_l:
                continue
            if abs(idx_j - idx_i) < MIN_TOUCH_SEPARATION:
                continue
            if abs(price_j - price_i) <= tolerance:
                cluster_indices.append(j)
                cluster_prices.append(price_j)
                cluster_bar_indices.append(idx_j)

        if len(cluster_prices) >= MIN_TOUCHES:
            for ci in cluster_indices:
                used_l.add(ci)
            level = sum(cluster_prices) / len(cluster_prices)
            pools_below.append(LiquidityPool(
                level=level,
                direction="BELOW",
                touches=len(cluster_prices),
                first_bar_index=min(cluster_bar_indices),
                last_bar_index=max(cluster_bar_indices),
            ))

    return pools_above, pools_below


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL — SESSION EXTREMES
# ═══════════════════════════════════════════════════════════════════════════════


def _compute_session_extremes(candles: list, closed_index: int) -> SessionExtremes:
    """Compute previous day and previous session highs/lows."""
    if closed_index < 1 or not candles:
        return SessionExtremes()

    current_bar = candles[min(closed_index, len(candles) - 1)]
    current_time = getattr(current_bar, "time", 0)
    if current_time <= 0:
        return SessionExtremes()

    try:
        current_dt = datetime.fromtimestamp(current_time, tz=timezone.utc)
    except (OSError, ValueError):
        return SessionExtremes()

    current_hour = current_dt.hour
    current_date = current_dt.date()

    # Determine current session
    current_session = _get_session(current_hour)

    # Find previous session boundaries
    prev_session_candles: list = []
    prev_day_candles: list = []
    prev_session_name = ""

    for i in range(closed_index, -1, -1):
        bar = candles[i]
        bar_time = getattr(bar, "time", 0)
        if bar_time <= 0:
            continue
        try:
            bar_dt = datetime.fromtimestamp(bar_time, tz=timezone.utc)
        except (OSError, ValueError):
            continue

        bar_date = bar_dt.date()
        bar_session = _get_session(bar_dt.hour)

        # Previous day candles
        if bar_date < current_date:
            from datetime import timedelta
            if bar_date >= current_date - timedelta(days=1):
                prev_day_candles.append(bar)

        # Previous session candles (different session from current)
        if bar_session != current_session and not prev_session_name:
            prev_session_name = bar_session

        if prev_session_name and bar_session == prev_session_name:
            prev_session_candles.append(bar)
        elif prev_session_name and bar_session != prev_session_name and prev_session_candles:
            break  # We've passed through the entire previous session

        # Limit scan
        if len(prev_day_candles) > 300 or (closed_index - i) > 500:
            break

    prev_day_high = max((c.high for c in prev_day_candles), default=0.0)
    prev_day_low = min((c.low for c in prev_day_candles), default=0.0) if prev_day_candles else 0.0
    prev_session_high = max((c.high for c in prev_session_candles), default=0.0)
    prev_session_low = min((c.low for c in prev_session_candles), default=0.0) if prev_session_candles else 0.0

    return SessionExtremes(
        prev_day_high=prev_day_high,
        prev_day_low=prev_day_low,
        prev_session_high=prev_session_high,
        prev_session_low=prev_session_low,
        prev_session_name=prev_session_name,
    )


def _get_session(hour: int) -> str:
    """Classify UTC hour into session name."""
    if 0 <= hour < 7:
        return "ASIA"
    elif 7 <= hour < 12:
        return "LONDON"
    elif 12 <= hour < 17:
        return "NY"
    else:
        return "OFF"


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL — SWEEP DETECTION
# ═══════════════════════════════════════════════════════════════════════════════


def _detect_sweep(
    candles: list,
    current_price: float,
    current_index: int,
    pools_above: list[LiquidityPool],
    pools_below: list[LiquidityPool],
    extremes: SessionExtremes,
    tolerance: float,
) -> LiquiditySweep | None:
    """
    Detect liquidity sweep in recent bars.

    Sweep = price exceeds a liquidity level, then closes back inside within
    SWEEP_CLOSE_BACK_WINDOW bars.
    """
    if current_index < 2:
        return None

    # Check recent bars for sweep of highs (bearish sweep — swept highs, reversed down)
    all_high_levels = [p.level for p in pools_above]
    if extremes.prev_day_high > 0:
        all_high_levels.append(extremes.prev_day_high)
    if extremes.prev_session_high > 0:
        all_high_levels.append(extremes.prev_session_high)

    all_low_levels = [p.level for p in pools_below]
    if extremes.prev_day_low > 0:
        all_low_levels.append(extremes.prev_day_low)
    if extremes.prev_session_low > 0:
        all_low_levels.append(extremes.prev_session_low)

    # Scan last few bars for sweep
    scan_start = max(0, current_index - SWEEP_CLOSE_BACK_WINDOW)

    for level in all_high_levels:
        if level <= 0:
            continue
        for i in range(scan_start, current_index + 1):
            bar = candles[i]
            # Bar exceeded the level (wick above)
            if bar.high > level + tolerance:
                # Check if close is back below
                if bar.close < level:
                    exceedance = bar.high - level
                    return LiquiditySweep(
                        direction="BEARISH",
                        swept_level=level,
                        exceedance_distance=exceedance,
                        bar_index=i,
                    )

    for level in all_low_levels:
        if level <= 0:
            continue
        for i in range(scan_start, current_index + 1):
            bar = candles[i]
            # Bar exceeded the level (wick below)
            if bar.low < level - tolerance:
                # Check if close is back above
                if bar.close > level:
                    exceedance = level - bar.low
                    return LiquiditySweep(
                        direction="BULLISH",
                        swept_level=level,
                        exceedance_distance=exceedance,
                        bar_index=i,
                    )

    return None
