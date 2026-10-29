"""
Order Block Detector — Displacement-based institutional zone detection.

Definition:
    An order block is the LAST opposite-direction candle before a significant
    displacement move that breaks structure.

Rules (strict, not subjective):
    1. Find displacement: 3+ consecutive same-direction candles, total move > 2.0 ATR
    2. The last candle of OPPOSITE direction immediately before the displacement = OB
    3. OB zone = [candle.low, candle.high]
    4. Strength = displacement_size / (ATR * 4), capped at 1.0
    5. Mitigation: price has returned to the OB zone (any bar close inside)
    6. Invalidation: price closes THROUGH the entire OB zone, or age > 500 bars
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Minimum displacement to qualify as OB-forming move
MIN_DISPLACEMENT_ATR = 2.0

# Minimum consecutive candles in displacement direction
MIN_DISPLACEMENT_BARS = 3

# Maximum age before OB expires (M5 bars)
MAX_OB_AGE_BARS = 500

# Maximum active OBs per direction
MAX_ACTIVE_OBS = 3


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class OrderBlock:
    """A detected order block zone."""
    direction: str  # "DEMAND" (bullish OB) or "SUPPLY" (bearish OB)
    high: float
    low: float
    midpoint: float
    size: float
    strength: float  # 0-1 based on displacement quality
    creation_bar_index: int
    mitigated: bool = False
    invalidated: bool = False


@dataclass
class OBSnapshot:
    """Complete order block state at observation time."""
    # Nearest demand OB (below price — bullish zone)
    nearest_demand_ob_price: float = 0.0
    nearest_demand_ob_distance_pips: float = 0.0
    demand_ob_timeframe: str = "M5"
    demand_ob_mitigated: bool = False
    demand_ob_strength: float = 0.0

    # Nearest supply OB (above price — bearish zone)
    nearest_supply_ob_price: float = 0.0
    nearest_supply_ob_distance_pips: float = 0.0
    supply_ob_timeframe: str = "M5"
    supply_ob_mitigated: bool = False
    supply_ob_strength: float = 0.0

    # Current position
    price_inside_ob: bool = False
    ob_type_if_inside: str = ""  # "DEMAND" or "SUPPLY"


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════


def detect_order_blocks(
    candles: list,
    current_price: float,
    closed_index: int,
    atr: float,
    symbol: str = "EURUSD",
    *,
    lookback: int = MAX_OB_AGE_BARS,
    min_displacement_atr: float = MIN_DISPLACEMENT_ATR,
) -> OBSnapshot:
    """
    Detect order blocks from candle data.

    Args:
        candles: M5 candle history
        current_price: Current mid price
        closed_index: Index of last closed bar
        atr: Current ATR in price units
        symbol: For pip size
        lookback: How many bars to scan
        min_displacement_atr: Minimum move size to form OB

    Returns:
        OBSnapshot with all OB features.
    """
    if not candles or closed_index < 10 or current_price <= 0 or atr <= 0:
        return OBSnapshot()

    pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
    min_displacement = atr * min_displacement_atr

    start = max(0, closed_index - lookback)
    window = candles[start:closed_index + 1]

    if len(window) < MIN_DISPLACEMENT_BARS + 2:
        return OBSnapshot()

    # Detect all order blocks
    obs: list[OrderBlock] = []

    for i in range(1, len(window) - MIN_DISPLACEMENT_BARS):
        # Check for bullish displacement starting at i
        bullish_ob = _check_bullish_displacement(window, i, min_displacement, atr, start)
        if bullish_ob:
            obs.append(bullish_ob)

        # Check for bearish displacement starting at i
        bearish_ob = _check_bearish_displacement(window, i, min_displacement, atr, start)
        if bearish_ob:
            obs.append(bearish_ob)

    # Update mitigation and invalidation
    for ob in obs:
        _update_ob_status(ob, candles, ob.creation_bar_index, closed_index)

    # Filter active OBs (not invalidated, not expired)
    active = [
        ob for ob in obs
        if not ob.invalidated
        and (closed_index - ob.creation_bar_index) <= MAX_OB_AGE_BARS
    ]

    # Separate demand (below price) and supply (above price)
    demand_obs = sorted(
        [ob for ob in active if ob.direction == "DEMAND" and ob.midpoint < current_price],
        key=lambda ob: current_price - ob.midpoint
    )[:MAX_ACTIVE_OBS]

    supply_obs = sorted(
        [ob for ob in active if ob.direction == "SUPPLY" and ob.midpoint > current_price],
        key=lambda ob: ob.midpoint - current_price
    )[:MAX_ACTIVE_OBS]

    # Nearest demand OB
    nearest_demand = demand_obs[0] if demand_obs else None
    # Nearest supply OB
    nearest_supply = supply_obs[0] if supply_obs else None

    # Check if price is inside any OB
    inside_ob = False
    inside_type = ""
    for ob in active:
        if ob.low <= current_price <= ob.high:
            inside_ob = True
            inside_type = ob.direction
            break

    return OBSnapshot(
        nearest_demand_ob_price=nearest_demand.midpoint if nearest_demand else 0.0,
        nearest_demand_ob_distance_pips=round((current_price - nearest_demand.high) / pip_size, 2) if nearest_demand else 0.0,
        demand_ob_timeframe="M5",
        demand_ob_mitigated=nearest_demand.mitigated if nearest_demand else False,
        demand_ob_strength=round(nearest_demand.strength, 4) if nearest_demand else 0.0,
        nearest_supply_ob_price=nearest_supply.midpoint if nearest_supply else 0.0,
        nearest_supply_ob_distance_pips=round((nearest_supply.low - current_price) / pip_size, 2) if nearest_supply else 0.0,
        supply_ob_timeframe="M5",
        supply_ob_mitigated=nearest_supply.mitigated if nearest_supply else False,
        supply_ob_strength=round(nearest_supply.strength, 4) if nearest_supply else 0.0,
        price_inside_ob=inside_ob,
        ob_type_if_inside=inside_type,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL — DISPLACEMENT DETECTION
# ═══════════════════════════════════════════════════════════════════════════════


def _check_bullish_displacement(
    candles: list, start_idx: int, min_displacement: float, atr: float, global_offset: int
) -> OrderBlock | None:
    """
    Check for bullish displacement starting at start_idx.

    Bullish: 3+ consecutive bullish candles with total move > threshold.
    OB = last bearish candle before the displacement.
    """
    # Need at least MIN_DISPLACEMENT_BARS bars ahead
    if start_idx + MIN_DISPLACEMENT_BARS > len(candles):
        return None

    # Check consecutive bullish candles
    consecutive = 0
    move_start = candles[start_idx].open
    move_end = candles[start_idx].close

    for j in range(start_idx, min(start_idx + 8, len(candles))):
        if candles[j].close > candles[j].open:
            consecutive += 1
            move_end = candles[j].close
        else:
            break

    if consecutive < MIN_DISPLACEMENT_BARS:
        return None

    total_move = move_end - move_start
    if total_move < min_displacement:
        return None

    # Find the OB: last bearish candle before displacement
    ob_candle = None
    ob_idx = start_idx - 1
    while ob_idx >= 0:
        if candles[ob_idx].close < candles[ob_idx].open:
            ob_candle = candles[ob_idx]
            break
        ob_idx -= 1

    if ob_candle is None:
        return None

    strength = min(1.0, total_move / (atr * 4))

    return OrderBlock(
        direction="DEMAND",
        high=ob_candle.high,
        low=ob_candle.low,
        midpoint=(ob_candle.high + ob_candle.low) / 2,
        size=ob_candle.high - ob_candle.low,
        strength=strength,
        creation_bar_index=ob_idx + global_offset,
    )


def _check_bearish_displacement(
    candles: list, start_idx: int, min_displacement: float, atr: float, global_offset: int
) -> OrderBlock | None:
    """
    Check for bearish displacement starting at start_idx.

    Bearish: 3+ consecutive bearish candles with total move > threshold.
    OB = last bullish candle before the displacement.
    """
    if start_idx + MIN_DISPLACEMENT_BARS > len(candles):
        return None

    consecutive = 0
    move_start = candles[start_idx].open
    move_end = candles[start_idx].close

    for j in range(start_idx, min(start_idx + 8, len(candles))):
        if candles[j].close < candles[j].open:
            consecutive += 1
            move_end = candles[j].close
        else:
            break

    if consecutive < MIN_DISPLACEMENT_BARS:
        return None

    total_move = move_start - move_end  # Bearish: start > end
    if total_move < min_displacement:
        return None

    # Find the OB: last bullish candle before displacement
    ob_candle = None
    ob_idx = start_idx - 1
    while ob_idx >= 0:
        if candles[ob_idx].close > candles[ob_idx].open:
            ob_candle = candles[ob_idx]
            break
        ob_idx -= 1

    if ob_candle is None:
        return None

    strength = min(1.0, total_move / (atr * 4))

    return OrderBlock(
        direction="SUPPLY",
        high=ob_candle.high,
        low=ob_candle.low,
        midpoint=(ob_candle.high + ob_candle.low) / 2,
        size=ob_candle.high - ob_candle.low,
        strength=strength,
        creation_bar_index=ob_idx + global_offset,
    )


def _update_ob_status(ob: OrderBlock, candles: list, creation_idx: int, closed_idx: int) -> None:
    """Update OB mitigation and invalidation status."""
    for i in range(creation_idx + 1, min(closed_idx + 1, len(candles))):
        bar = candles[i]

        # Mitigation: price has returned to the zone (close inside)
        if not ob.mitigated:
            if ob.low <= bar.close <= ob.high:
                ob.mitigated = True

        # Invalidation: price closes THROUGH the entire zone
        if ob.direction == "DEMAND":
            # Demand OB invalidated if price closes below the OB low
            if bar.close < ob.low:
                ob.invalidated = True
                return
        else:
            # Supply OB invalidated if price closes above the OB high
            if bar.close > ob.high:
                ob.invalidated = True
                return
