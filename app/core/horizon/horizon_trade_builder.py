"""
Horizon Trade Builder — Constructs hypothetical trade parameters per horizon.

Converts (Opportunity + Horizon + Market Context) into shadow trade intents
with horizon-appropriate SL/TP values.

SCALP:   SL from M5 candle geometry. TP = entry ± risk × 2.0
INTRADAY: SL from M15 structure levels. TP = entry ± risk × 3.0
EXTENDED: SL from H1 swing levels. TP = entry ± risk × 4.0

This module is PURELY RESEARCH. It does NOT:
    - Modify live execution
    - Change RiskManager behaviour
    - Create real OrderIntents
    - Affect position management
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# HORIZON TRADE MODEL
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class HorizonTrade:
    """Hypothetical trade parameters for one horizon. Research only."""

    symbol: str
    horizon: str                    # "SCALP" | "INTRADAY" | "EXTENDED"
    direction: str                  # "BUY" | "SELL"
    entry: float                    # Market price at detection time
    stop_loss: float                # Horizon-appropriate SL
    take_profit: float              # Horizon-appropriate TP
    risk_distance: float            # abs(entry - stop_loss)
    rr: float                       # reward/risk ratio
    sl_source: str                  # Where SL was derived from
    reasoning: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "horizon": self.horizon,
            "direction": self.direction,
            "entry": round(self.entry, 8),
            "stop_loss": round(self.stop_loss, 8),
            "take_profit": round(self.take_profit, 8),
            "risk_distance": round(self.risk_distance, 8),
            "rr": round(self.rr, 4),
            "sl_source": self.sl_source,
            "reasoning": self.reasoning,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# HORIZON RR TARGETS
# ═══════════════════════════════════════════════════════════════════════════════

_HORIZON_RR = {
    "SCALP": 2.0,
    "INTRADAY": 3.0,
    "EXTENDED": 4.0,
}

_SL_BUFFER_SCALP = 0.0002         # Same as current M5 SL_BUFFER
_SL_BUFFER_INTRADAY = 0.0003      # Slightly wider for M15 structure
_SL_BUFFER_EXTENDED = 0.0005      # Wider for H1 swing levels


# ═══════════════════════════════════════════════════════════════════════════════
# BUILDER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def build_horizon_trade(
    *,
    horizon: str,
    symbol: str,
    direction: str,
    entry_price: float,
    # M5 context (for SCALP)
    m5_candle_high: float | None = None,
    m5_candle_low: float | None = None,
    # M15 context (for INTRADAY)
    m15_nearest_support: float | None = None,
    m15_nearest_resistance: float | None = None,
    # H1 context (for EXTENDED)
    h1_last_swing_high: float | None = None,
    h1_last_swing_low: float | None = None,
) -> HorizonTrade | None:
    """
    Build a hypothetical trade for the given horizon.

    Returns None if required structure data is unavailable for the horizon.

    Args:
        horizon: "SCALP" | "INTRADAY" | "EXTENDED"
        symbol: Trading pair
        direction: "BUY" or "SELL"
        entry_price: Market price at detection (bid for SELL, ask for BUY)
        m5_candle_high/low: Trigger candle extremes (SCALP SL source)
        m15_nearest_support/resistance: M15 structure levels (INTRADAY SL source)
        h1_last_swing_high/low: H1 swing levels (EXTENDED SL source)

    Returns:
        HorizonTrade with complete SL/TP/RR, or None if data insufficient.
    """
    if horizon == "SCALP":
        return _build_scalp(symbol, direction, entry_price, m5_candle_high, m5_candle_low)
    elif horizon == "INTRADAY":
        return _build_intraday(symbol, direction, entry_price, m15_nearest_support, m15_nearest_resistance)
    elif horizon == "EXTENDED":
        return _build_extended(symbol, direction, entry_price, h1_last_swing_high, h1_last_swing_low)
    else:
        return None


def build_all_horizon_trades(
    *,
    eligible_horizons: list[str],
    symbol: str,
    direction: str,
    entry_price: float,
    m5_candle_high: float | None = None,
    m5_candle_low: float | None = None,
    m15_nearest_support: float | None = None,
    m15_nearest_resistance: float | None = None,
    h1_last_swing_high: float | None = None,
    h1_last_swing_low: float | None = None,
) -> list[HorizonTrade]:
    """
    Build hypothetical trades for ALL eligible horizons.

    Returns list of successfully constructed trades (skips horizons
    where required data is missing).
    """
    trades: list[HorizonTrade] = []
    for horizon in eligible_horizons:
        trade = build_horizon_trade(
            horizon=horizon,
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            m5_candle_high=m5_candle_high,
            m5_candle_low=m5_candle_low,
            m15_nearest_support=m15_nearest_support,
            m15_nearest_resistance=m15_nearest_resistance,
            h1_last_swing_high=h1_last_swing_high,
            h1_last_swing_low=h1_last_swing_low,
        )
        if trade is not None:
            trades.append(trade)
    return trades


# ─── NEW Shadow Runtime support (additive — used by core/shadow/runtime.py) ───

SL_BUFFERS = {
    "SCALP": _SL_BUFFER_SCALP,
    "INTRADAY": _SL_BUFFER_INTRADAY,
    "EXTENDED": _SL_BUFFER_EXTENDED,
}
"""SL buffer applied per horizon (price units). Persisted as provenance."""

_SL_INPUT_REQUIREMENTS = {
    "SCALP": {"SELL": "m5_candle_high", "BUY": "m5_candle_low"},
    "INTRADAY": {"SELL": "m15_nearest_resistance", "BUY": "m15_nearest_support"},
    "EXTENDED": {"SELL": "h1_last_swing_high", "BUY": "h1_last_swing_low"},
}


def horizon_missing_inputs(
    horizon: str,
    direction: str,
    *,
    m5_candle_high: float | None = None,
    m5_candle_low: float | None = None,
    m15_nearest_support: float | None = None,
    m15_nearest_resistance: float | None = None,
    h1_last_swing_high: float | None = None,
    h1_last_swing_low: float | None = None,
) -> list[str]:
    """
    Report which construction inputs are missing for this horizon/direction.

    Mirrors the exact conditions that make _build_* return None, so the NEW
    Shadow Runtime can record ELIGIBLE_BUT_UNCONSTRUCTIBLE with the precise
    missing dependency instead of the horizon silently disappearing.
    """
    levels = {
        "m5_candle_high": m5_candle_high,
        "m5_candle_low": m5_candle_low,
        "m15_nearest_support": m15_nearest_support,
        "m15_nearest_resistance": m15_nearest_resistance,
        "h1_last_swing_high": h1_last_swing_high,
        "h1_last_swing_low": h1_last_swing_low,
    }
    required = _SL_INPUT_REQUIREMENTS.get(horizon, {}).get(direction.upper())
    if required is None:
        return [f"horizon:{horizon}"]
    missing = [name for name, v in levels.items() if v is None]
    # The SL-critical input first; other absent levels are informational.
    ordered = ([required] if required in missing else []) + [
        m for m in missing if m != required
    ]
    return ordered



# ═══════════════════════════════════════════════════════════════════════════════
# PER-HORIZON BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def _build_scalp(
    symbol: str, direction: str, entry: float,
    candle_high: float | None, candle_low: float | None,
) -> HorizonTrade | None:
    """SCALP: SL from M5 candle geometry. TP at 2:1 RR."""
    if direction == "SELL":
        if candle_high is None:
            return None
        sl = candle_high + _SL_BUFFER_SCALP
        risk = sl - entry
        if risk <= 0:
            return None
        tp = entry - risk * _HORIZON_RR["SCALP"]
        reasoning = ["SL: M5 candle high + buffer", f"TP: entry - risk * {_HORIZON_RR['SCALP']}"]
    elif direction == "BUY":
        if candle_low is None:
            return None
        sl = candle_low - _SL_BUFFER_SCALP
        risk = entry - sl
        if risk <= 0:
            return None
        tp = entry + risk * _HORIZON_RR["SCALP"]
        reasoning = ["SL: M5 candle low - buffer", f"TP: entry + risk * {_HORIZON_RR['SCALP']}"]
    else:
        return None

    return HorizonTrade(
        symbol=symbol,
        horizon="SCALP",
        direction=direction,
        entry=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_distance=abs(entry - sl),
        rr=_HORIZON_RR["SCALP"],
        sl_source="M5_CANDLE_GEOMETRY",
        reasoning=reasoning,
    )


def _build_intraday(
    symbol: str, direction: str, entry: float,
    nearest_support: float | None, nearest_resistance: float | None,
) -> HorizonTrade | None:
    """INTRADAY: SL from M15 structure. TP at 3:1 RR."""
    if direction == "SELL":
        if nearest_resistance is None:
            return None
        sl = nearest_resistance + _SL_BUFFER_INTRADAY
        risk = sl - entry
        if risk <= 0:
            return None
        tp = entry - risk * _HORIZON_RR["INTRADAY"]
        reasoning = ["SL: M15 nearest resistance + buffer", f"TP: entry - risk * {_HORIZON_RR['INTRADAY']}"]
    elif direction == "BUY":
        if nearest_support is None:
            return None
        sl = nearest_support - _SL_BUFFER_INTRADAY
        risk = entry - sl
        if risk <= 0:
            return None
        tp = entry + risk * _HORIZON_RR["INTRADAY"]
        reasoning = ["SL: M15 nearest support - buffer", f"TP: entry + risk * {_HORIZON_RR['INTRADAY']}"]
    else:
        return None

    return HorizonTrade(
        symbol=symbol,
        horizon="INTRADAY",
        direction=direction,
        entry=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_distance=abs(entry - sl),
        rr=_HORIZON_RR["INTRADAY"],
        sl_source="M15_STRUCTURE",
        reasoning=reasoning,
    )


def _build_extended(
    symbol: str, direction: str, entry: float,
    last_swing_high: float | None, last_swing_low: float | None,
) -> HorizonTrade | None:
    """EXTENDED: SL from H1 swing levels. TP at 4:1 RR."""
    if direction == "SELL":
        if last_swing_high is None:
            return None
        sl = last_swing_high + _SL_BUFFER_EXTENDED
        risk = sl - entry
        if risk <= 0:
            return None
        tp = entry - risk * _HORIZON_RR["EXTENDED"]
        reasoning = ["SL: H1 last swing high + buffer", f"TP: entry - risk * {_HORIZON_RR['EXTENDED']}"]
    elif direction == "BUY":
        if last_swing_low is None:
            return None
        sl = last_swing_low - _SL_BUFFER_EXTENDED
        risk = entry - sl
        if risk <= 0:
            return None
        tp = entry + risk * _HORIZON_RR["EXTENDED"]
        reasoning = ["SL: H1 last swing low - buffer", f"TP: entry + risk * {_HORIZON_RR['EXTENDED']}"]
    else:
        return None

    return HorizonTrade(
        symbol=symbol,
        horizon="EXTENDED",
        direction=direction,
        entry=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_distance=abs(entry - sl),
        rr=_HORIZON_RR["EXTENDED"],
        sl_source="H1_SWING_STRUCTURE",
        reasoning=reasoning,
    )
