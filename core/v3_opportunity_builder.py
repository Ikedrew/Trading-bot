"""
V3OpportunityBuilder — Constructs V3Opportunity observations focused on market location
and liquidity context.

This is an OBSERVATION layer. It does NOT:
    - Make trading decisions
    - Modify scores or confidence
    - Block or gate any trade
    - Import execution, risk, or pipeline modules

It extracts location and liquidity information from available market data
to produce a V3Opportunity record for research into whether precise market
positioning predicts trade outcomes.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.v3_opportunity import V3Opportunity, _SCHEMA_VERSION

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/v3_opportunities"


def build_v3_opportunity(
    *,
    symbol: str,
    timestamp_utc: float,
    correlation_id: str = "",
    # Price data
    price: float = 0.0,
    bid: float = 0.0,
    ask: float = 0.0,
    atr: float = 0.0,
    session: str = "",
    # Market context (from MarketContext or htf_context)
    market_context: Any = None,
    # Candles for displacement/rejection analysis
    candles: list | None = None,
    closed_index: int = -1,
    # Detector snapshots (from market_intelligence)
    liquidity_snapshot: Any = None,
    fvg_snapshot: Any = None,
    ob_snapshot: Any = None,
) -> V3Opportunity:
    """
    Build a V3Opportunity from available market state.

    Extracts location and liquidity information from MarketContext and candles.
    Falls back to zeros for unavailable fields.
    Never raises — returns partial observation on any failure.
    """
    opp_id = f"v3_{symbol}_{int(timestamp_utc)}_{uuid.uuid4().hex[:8]}"

    # Mid price
    if price <= 0 and bid > 0 and ask > 0:
        price = (bid + ask) / 2
    spread = abs(ask - bid) if bid > 0 and ask > 0 else 0.0

    pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001

    # ─── Extract from MarketContext ───────────────────────────────────
    h4_swing_high = 0.0
    h4_swing_low = 0.0
    h1_swing_high = 0.0
    h1_swing_low = 0.0
    h1_last_bos_price = 0.0
    m15_swing_high = 0.0
    m15_swing_low = 0.0
    nearest_support_price = 0.0
    nearest_resistance_price = 0.0
    nearest_support_tf = ""
    nearest_resistance_tf = ""

    if market_context is not None:
        # H4 swings — H4Summary does not have swing levels yet
        h4 = getattr(market_context, "h4", None)
        if h4:
            h4_swing_high = float(getattr(h4, "swing_high", 0) or 0)
            h4_swing_low = float(getattr(h4, "swing_low", 0) or 0)

        # H1 swings and BOS (from H1Summary.swing_high/swing_low)
        h1 = getattr(market_context, "h1", None)
        if h1:
            h1_swing_high = float(getattr(h1, "swing_high", 0) or 0)
            h1_swing_low = float(getattr(h1, "swing_low", 0) or 0)
            h1_last_bos_price = float(getattr(h1, "bos_price", 0) or 0)

        # M15 (swing_high/swing_low from M15Summary)
        m15 = getattr(market_context, "m15", None)
        if m15:
            m15_swing_high = float(getattr(m15, "swing_high", 0) or 0)
            m15_swing_low = float(getattr(m15, "swing_low", 0) or 0)
            nearest_support_price = float(getattr(m15, "nearest_support", 0) or 0)
            nearest_resistance_price = float(getattr(m15, "nearest_resistance", 0) or 0)
            if nearest_support_price > 0:
                nearest_support_tf = "M15"
            if nearest_resistance_price > 0:
                nearest_resistance_tf = "M15"

    # ─── Compute range positions ──────────────────────────────────────
    h4_range_pos = _range_position(price, h4_swing_low, h4_swing_high)
    h1_range_pos = _range_position(price, h1_swing_low, h1_swing_high)
    m15_range_pos = _range_position(price, m15_swing_low, m15_swing_high)

    # ─── Compute distances in pips ────────────────────────────────────
    h4_dist_high = _distance_pips(price, h4_swing_high, pip_size)
    h4_dist_low = _distance_pips(price, h4_swing_low, pip_size)
    h1_dist_high = _distance_pips(price, h1_swing_high, pip_size)
    h1_dist_low = _distance_pips(price, h1_swing_low, pip_size)
    h1_dist_bos = _distance_pips(price, h1_last_bos_price, pip_size)

    support_dist = _distance_pips(price, nearest_support_price, pip_size) if nearest_support_price > 0 else 0.0
    resistance_dist = _distance_pips(price, nearest_resistance_price, pip_size) if nearest_resistance_price > 0 else 0.0

    # ─── Spread/risk ratio ────────────────────────────────────────────
    # Use distance to nearest support as proxy for stop distance
    stop_proxy = support_dist * pip_size if support_dist > 0 else atr if atr > 0 else 0.0
    spread_risk = spread / stop_proxy if stop_proxy > 0 else 0.0

    # ─── Displacement and rejection from candles ──────────────────────
    displacement_into_level = False
    displacement_mag_atr = 0.0
    rejection_present = False
    rejection_body = 0.0
    rejection_wick_atr = 0.0
    bars_at_level = 0
    consolidation_range = 0.0

    if candles and closed_index > 0 and atr > 0:
        try:
            idx = min(closed_index, len(candles) - 1)
            c = candles[idx]
            c_range = c.high - c.low
            body = abs(c.close - c.open)

            # Rejection candle: long wick relative to body
            if c_range > 0:
                upper_wick = c.high - max(c.open, c.close)
                lower_wick = min(c.open, c.close) - c.low
                max_wick = max(upper_wick, lower_wick)
                if max_wick > body * 1.5:
                    rejection_present = True
                    rejection_body = body / c_range
                    rejection_wick_atr = max_wick / atr if atr > 0 else 0.0

            # Displacement: large move into level (>1.5 ATR)
            if c_range > atr * 1.5:
                displacement_into_level = True
                displacement_mag_atr = c_range / atr

            # Consolidation detection: count bars in tight range
            if idx >= 3:
                recent_highs = [candles[i].high for i in range(max(0, idx - 4), idx + 1)]
                recent_lows = [candles[i].low for i in range(max(0, idx - 4), idx + 1)]
                cons_range = max(recent_highs) - min(recent_lows)
                consolidation_range = cons_range / pip_size
                if cons_range < atr * 0.8:
                    bars_at_level = min(5, idx)
        except Exception:
            pass

    return V3Opportunity(
        opportunity_id=opp_id,
        correlation_id=correlation_id,
        timestamp_utc=timestamp_utc,
        symbol=symbol,
        timeframe="M5",
        schema_version=_SCHEMA_VERSION,
        # Price position
        price_at_observation=price,
        h4_swing_high=h4_swing_high,
        h4_swing_low=h4_swing_low,
        h4_range_position=round(h4_range_pos, 4),
        h4_distance_from_high_pips=round(h4_dist_high, 2),
        h4_distance_from_low_pips=round(h4_dist_low, 2),
        h1_swing_high=h1_swing_high,
        h1_swing_low=h1_swing_low,
        h1_range_position=round(h1_range_pos, 4),
        h1_distance_from_high_pips=round(h1_dist_high, 2),
        h1_distance_from_low_pips=round(h1_dist_low, 2),
        h1_last_bos_price=h1_last_bos_price,
        h1_distance_from_bos_pips=round(h1_dist_bos, 2),
        m15_swing_high=m15_swing_high,
        m15_swing_low=m15_swing_low,
        m15_range_position=round(m15_range_pos, 4),
        # Support/Resistance
        nearest_support_price=nearest_support_price,
        nearest_support_distance_pips=round(support_dist, 2),
        nearest_support_timeframe=nearest_support_tf,
        nearest_resistance_price=nearest_resistance_price,
        nearest_resistance_distance_pips=round(resistance_dist, 2),
        nearest_resistance_timeframe=nearest_resistance_tf,
        # Displacement
        displacement_into_level=displacement_into_level,
        displacement_magnitude_atr=round(displacement_mag_atr, 4),
        rejection_candle_present=rejection_present,
        rejection_body_ratio=round(rejection_body, 4),
        rejection_wick_atr_ratio=round(rejection_wick_atr, 4),
        bars_at_current_level=bars_at_level,
        consolidation_range_pips=round(consolidation_range, 2),
        # Execution
        bid=bid,
        ask=ask,
        spread=spread,
        spread_risk_ratio=round(spread_risk, 6),
        atr=atr,
        session=session,
        # Liquidity (from detector snapshot)
        equal_highs_above=getattr(liquidity_snapshot, "equal_highs_above", False) if liquidity_snapshot else False,
        equal_highs_distance_pips=getattr(liquidity_snapshot, "equal_highs_distance_pips", 0.0) if liquidity_snapshot else 0.0,
        equal_highs_count=getattr(liquidity_snapshot, "equal_highs_count", 0) if liquidity_snapshot else 0,
        equal_lows_below=getattr(liquidity_snapshot, "equal_lows_below", False) if liquidity_snapshot else False,
        equal_lows_distance_pips=getattr(liquidity_snapshot, "equal_lows_distance_pips", 0.0) if liquidity_snapshot else 0.0,
        equal_lows_count=getattr(liquidity_snapshot, "equal_lows_count", 0) if liquidity_snapshot else 0,
        prev_session_high=getattr(liquidity_snapshot, "prev_session_high", 0.0) if liquidity_snapshot else 0.0,
        prev_session_low=getattr(liquidity_snapshot, "prev_session_low", 0.0) if liquidity_snapshot else 0.0,
        distance_to_prev_session_high_pips=getattr(liquidity_snapshot, "distance_to_prev_session_high_pips", 0.0) if liquidity_snapshot else 0.0,
        distance_to_prev_session_low_pips=getattr(liquidity_snapshot, "distance_to_prev_session_low_pips", 0.0) if liquidity_snapshot else 0.0,
        prev_session_high_swept=getattr(liquidity_snapshot, "prev_session_high_swept", False) if liquidity_snapshot else False,
        prev_session_low_swept=getattr(liquidity_snapshot, "prev_session_low_swept", False) if liquidity_snapshot else False,
        prev_day_high=getattr(liquidity_snapshot, "prev_day_high", 0.0) if liquidity_snapshot else 0.0,
        prev_day_low=getattr(liquidity_snapshot, "prev_day_low", 0.0) if liquidity_snapshot else 0.0,
        distance_to_prev_day_high_pips=getattr(liquidity_snapshot, "distance_to_prev_day_high_pips", 0.0) if liquidity_snapshot else 0.0,
        distance_to_prev_day_low_pips=getattr(liquidity_snapshot, "distance_to_prev_day_low_pips", 0.0) if liquidity_snapshot else 0.0,
        prev_day_high_swept=getattr(liquidity_snapshot, "prev_day_high_swept", False) if liquidity_snapshot else False,
        prev_day_low_swept=getattr(liquidity_snapshot, "prev_day_low_swept", False) if liquidity_snapshot else False,
        liquidity_sweep_just_occurred=getattr(liquidity_snapshot, "liquidity_sweep_just_occurred", False) if liquidity_snapshot else False,
        sweep_direction=getattr(liquidity_snapshot, "sweep_direction", "") if liquidity_snapshot else "",
        sweep_distance_pips=getattr(liquidity_snapshot, "sweep_distance_pips", 0.0) if liquidity_snapshot else 0.0,
        bars_since_sweep=getattr(liquidity_snapshot, "bars_since_sweep", 0) if liquidity_snapshot else 0,
        # FVG (from detector snapshot)
        nearest_fvg_above_price=getattr(fvg_snapshot, "nearest_fvg_above_price", 0.0) if fvg_snapshot else 0.0,
        nearest_fvg_above_distance_pips=getattr(fvg_snapshot, "nearest_fvg_above_distance_pips", 0.0) if fvg_snapshot else 0.0,
        fvg_above_filled_pct=getattr(fvg_snapshot, "fvg_above_filled_pct", 0.0) if fvg_snapshot else 0.0,
        nearest_fvg_below_price=getattr(fvg_snapshot, "nearest_fvg_below_price", 0.0) if fvg_snapshot else 0.0,
        nearest_fvg_below_distance_pips=getattr(fvg_snapshot, "nearest_fvg_below_distance_pips", 0.0) if fvg_snapshot else 0.0,
        fvg_below_filled_pct=getattr(fvg_snapshot, "fvg_below_filled_pct", 0.0) if fvg_snapshot else 0.0,
        price_inside_fvg=getattr(fvg_snapshot, "price_inside_fvg", False) if fvg_snapshot else False,
        fvg_direction_if_inside=getattr(fvg_snapshot, "fvg_direction_if_inside", "") if fvg_snapshot else "",
        total_unfilled_fvgs_above=getattr(fvg_snapshot, "total_unfilled_fvgs_above", 0) if fvg_snapshot else 0,
        total_unfilled_fvgs_below=getattr(fvg_snapshot, "total_unfilled_fvgs_below", 0) if fvg_snapshot else 0,
        # Order Blocks (from detector snapshot)
        nearest_demand_ob_price=getattr(ob_snapshot, "nearest_demand_ob_price", 0.0) if ob_snapshot else 0.0,
        nearest_demand_ob_distance_pips=getattr(ob_snapshot, "nearest_demand_ob_distance_pips", 0.0) if ob_snapshot else 0.0,
        demand_ob_timeframe=getattr(ob_snapshot, "demand_ob_timeframe", "") if ob_snapshot else "",
        demand_ob_mitigated=getattr(ob_snapshot, "demand_ob_mitigated", False) if ob_snapshot else False,
        demand_ob_strength=getattr(ob_snapshot, "demand_ob_strength", 0.0) if ob_snapshot else 0.0,
        nearest_supply_ob_price=getattr(ob_snapshot, "nearest_supply_ob_price", 0.0) if ob_snapshot else 0.0,
        nearest_supply_ob_distance_pips=getattr(ob_snapshot, "nearest_supply_ob_distance_pips", 0.0) if ob_snapshot else 0.0,
        supply_ob_timeframe=getattr(ob_snapshot, "supply_ob_timeframe", "") if ob_snapshot else "",
        supply_ob_mitigated=getattr(ob_snapshot, "supply_ob_mitigated", False) if ob_snapshot else False,
        supply_ob_strength=getattr(ob_snapshot, "supply_ob_strength", 0.0) if ob_snapshot else 0.0,
        price_inside_ob=getattr(ob_snapshot, "price_inside_ob", False) if ob_snapshot else False,
        ob_type_if_inside=getattr(ob_snapshot, "ob_type_if_inside", "") if ob_snapshot else "",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════


def persist_v3_opportunity(opp: V3Opportunity) -> bool:
    """
    Persist a V3Opportunity to local JSONL. Fire-and-forget.

    Storage: logs/v3_opportunities/{SYMBOL}/{YYYY-MM-DD}.jsonl
    Never raises. Never blocks trading.
    """
    try:
        symbol = opp.symbol or "UNKNOWN"
        ts = opp.timestamp_utc

        if ts > 1_000_000_000:
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(opp.to_dict(), separators=(",", ":"), default=str)

        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        return True
    except Exception as exc:
        logger.debug("[V3_OPP_PERSIST] failed: %s", exc)
        return False


def read_v3_opportunities(*, symbol: str | None = None) -> list[dict[str, Any]]:
    """Read persisted V3Opportunities. For research queries."""
    base = Path(_LOCAL_DIR)
    if not base.exists():
        return []

    results: list[dict[str, Any]] = []
    dirs = [base / symbol] if symbol else [d for d in base.iterdir() if d.is_dir()]

    for dir_path in dirs:
        if not dir_path.exists():
            continue
        for filepath in sorted(dir_path.glob("*.jsonl")):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            try:
                                results.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
            except OSError:
                continue

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════


def _range_position(price: float, low: float, high: float) -> float:
    """Compute where price sits in a range (0=at low, 1=at high)."""
    if high <= low or price <= 0:
        return 0.0
    if price <= low:
        return 0.0
    if price >= high:
        return 1.0
    return (price - low) / (high - low)


def _distance_pips(price: float, level: float, pip_size: float) -> float:
    """Compute absolute distance in pips."""
    if price <= 0 or level <= 0 or pip_size <= 0:
        return 0.0
    return abs(price - level) / pip_size
