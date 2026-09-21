"""Account-specific risk/volume (E) — ACTIVE V10 methodology, per-account facts.

Active V10 production path (unchanged formula):
    core/v10/risk_engine.calculate_position_size_exact(
        risk_amount=account.balance * risk_pct,
        stop_distance=|entry - sl|,
        tick_value / tick_size / volume_min / volume_max / volume_step
        from THAT account's broker symbol spec)

This module only swaps WHICH account's facts feed that formula. No new
risk model is introduced. If the floored volume is below the broker
minimum, the account is BLOCKED (never rounded upward past intended risk).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.v10.risk_engine import (
    _get_risk_percentage,
    calculate_position_size_exact,
)


@dataclass(frozen=True)
class AccountVolumeResult:
    account_id: str
    broker_symbol: str | None
    volume: float
    blocked_reason: str = ""
    risk_pct: float = 0.0
    risk_amount: float = 0.0
    balance: float = 0.0
    # ─── Sizing evidence (auditable; never affects the number) ────────────
    entry_type: str = ""
    reference_entry: float = 0.0   # strategy/order entry that WAS the old sizing basis
    sizing_entry: float = 0.0      # entry price actually used for stop_distance
    bid: float = 0.0
    ask: float = 0.0
    sl: float = 0.0
    stop_distance: float = 0.0


# Entry types that fill at the CURRENT market and therefore must be sized off
# the live executable price, never a stale strategy reference.
_MARKET_ENTRY_TYPES = frozenset({"MARKET", ""})


def _risk_inputs(symbol_row: dict) -> dict[str, float] | None:
    try:
        return {
            "tick_value": float(symbol_row.get("trade_tick_value", 0.0) or 0.0),
            "tick_size": float(symbol_row.get("trade_tick_size", 0.0) or 0.0),
            "volume_min": float(symbol_row.get("volume_min", 0.0) or 0.0),
            "volume_max": float(symbol_row.get("volume_max", 0.0) or 0.0),
            "volume_step": float(symbol_row.get("volume_step", 0.0) or 0.0),
            # Broker minimum legal stop distance = trade_stops_level * point.
            "point": float(symbol_row.get("point", 0.0) or 0.0),
            "stops_level": float(symbol_row.get("trade_stops_level", 0.0) or 0.0),
        }
    except (TypeError, ValueError):
        return None


def _sizing_entry_for(
    *, entry_type: str, side: str, symbol_row: dict, target_entry: float,
) -> tuple[float, str]:
    """Resolve the entry price to size against.

    MARKET  → current executable broker price (ask for BUY, bid for SELL).
              Never falls back to the stale strategy entry_reference — a
              missing/invalid quote fails closed (returns 0.0).
    LIMIT/STOP → the intended pending order entry (existing semantic).
    """
    etype = str(entry_type or "MARKET").upper()
    if etype in _MARKET_ENTRY_TYPES:
        def _price(key: str) -> float:
            raw = symbol_row.get(key)
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return 0.0
            return value if value > 0 else 0.0
        # BUY fills at ASK, SELL fills at BID.
        price = _price("ask") if side == "BUY" else _price("bid")
        return price, "MARKET"
    # Pending order: the intended limit/stop price is the correct reference.
    try:
        return float(target_entry), etype
    except (TypeError, ValueError):
        return 0.0, etype


def _geometry_valid(*, side: str, sizing_entry: float, sl: float) -> bool:
    """Directional SL sanity — never let abs() mask an inverted stop.

    BUY  requires SL strictly BELOW the entry.
    SELL requires SL strictly ABOVE the entry.
    """
    if sizing_entry <= 0 or sl <= 0:
        return False
    if side == "BUY":
        return sl < sizing_entry
    if side == "SELL":
        return sl > sizing_entry
    return False


def volume_for_account(
    *,
    target: Any,
    snapshot: dict,
    symbol_row: dict | None,
    strategy_family: str = "",
    horizon_type: str = "SCALP",
) -> AccountVolumeResult:
    balance = float(snapshot.get("balance") or 0.0)
    risk_pct = _get_risk_percentage(strategy_family, horizon_type)
    risk_amount = balance * risk_pct
    side = str(getattr(target, "side", "") or "").upper()
    entry_type = str(getattr(target, "entry_type", "MARKET") or "MARKET").upper()
    reference_entry = float(getattr(target, "entry", 0.0) or 0.0)
    sl = float(getattr(target, "sl", 0.0) or 0.0)

    def _blocked(reason: str, *, sizing_entry: float = 0.0, bid: float = 0.0,
                 ask: float = 0.0, stop_distance: float = 0.0) -> AccountVolumeResult:
        return AccountVolumeResult(
            target.account_id, target.broker_symbol, 0.0, reason,
            risk_pct, risk_amount, balance,
            entry_type=entry_type, reference_entry=reference_entry,
            sizing_entry=sizing_entry, bid=bid, ask=ask, sl=sl,
            stop_distance=stop_distance)

    if balance <= 0:
        return _blocked("VOLUME_UNAVAILABLE")
    if not symbol_row or symbol_row.get("status") != "available":
        return _blocked("SYMBOL_UNAVAILABLE")
    inputs = _risk_inputs(symbol_row)
    if not inputs or inputs["tick_value"] <= 0 or inputs["tick_size"] <= 0:
        return _blocked("VOLUME_UNAVAILABLE")

    def _quote(key: str) -> float:
        try:
            v = float(symbol_row.get(key))
        except (TypeError, ValueError):
            return 0.0
        return v if v > 0 else 0.0
    bid, ask = _quote("bid"), _quote("ask")

    # ─── Entry-type-aware sizing reference ────────────────────────────────
    sizing_entry, resolved_type = _sizing_entry_for(
        entry_type=entry_type, side=side, symbol_row=symbol_row,
        target_entry=reference_entry)
    if resolved_type == "MARKET" and sizing_entry <= 0:
        # No valid executable quote → MUST NOT fall back to the stale entry
        # reference (that recreates the original oversizing defect).
        return _blocked("MARKET_PRICE_UNAVAILABLE", sizing_entry=0.0, bid=bid, ask=ask)

    # ─── Fail-closed directional geometry (before abs()) ──────────────────
    if not _geometry_valid(side=side, sizing_entry=sizing_entry, sl=sl):
        return _blocked("INVALID_STOP_GEOMETRY", sizing_entry=sizing_entry,
                        bid=bid, ask=ask)

    stop_distance = abs(sizing_entry - sl)
    if stop_distance <= 0:
        return _blocked("VOLUME_UNAVAILABLE", sizing_entry=sizing_entry,
                        bid=bid, ask=ask, stop_distance=0.0)

    # ─── Broker minimum legal stop distance ───────────────────────────────
    min_stop = inputs["stops_level"] * inputs["point"]
    if min_stop > 0 and stop_distance < min_stop:
        return _blocked("STOP_TOO_CLOSE", sizing_entry=sizing_entry,
                        bid=bid, ask=ask, stop_distance=stop_distance)

    volume = calculate_position_size_exact(
        risk_amount=risk_amount,
        stop_distance=stop_distance,
        tick_value=inputs["tick_value"],
        tick_size=inputs["tick_size"],
        volume_min=inputs["volume_min"],
        volume_max=inputs["volume_max"],
        volume_step=inputs["volume_step"],
    )
    if volume <= 0:
        # Exact calculator returns 0.0 when floored size < broker minimum.
        return _blocked("VOLUME_BELOW_MIN", sizing_entry=sizing_entry,
                        bid=bid, ask=ask, stop_distance=stop_distance)
    return AccountVolumeResult(
        target.account_id, target.broker_symbol, volume, "",
        risk_pct, risk_amount, balance,
        entry_type=entry_type, reference_entry=reference_entry,
        sizing_entry=sizing_entry, bid=bid, ask=ask, sl=sl,
        stop_distance=stop_distance)
