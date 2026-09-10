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


def _risk_inputs(symbol_row: dict) -> dict[str, float] | None:
    try:
        return {
            "tick_value": float(symbol_row.get("trade_tick_value", 0.0) or 0.0),
            "tick_size": float(symbol_row.get("trade_tick_size", 0.0) or 0.0),
            "volume_min": float(symbol_row.get("volume_min", 0.0) or 0.0),
            "volume_max": float(symbol_row.get("volume_max", 0.0) or 0.0),
            "volume_step": float(symbol_row.get("volume_step", 0.0) or 0.0),
        }
    except (TypeError, ValueError):
        return None


def volume_for_account(
    *,
    target: Any,
    snapshot: dict,
    symbol_row: dict | None,
    strategy_family: str = "",
    horizon_type: str = "SCALP",
) -> AccountVolumeResult:
    balance = float(snapshot.get("balance") or 0.0)
    stop_distance = abs(float(target.entry) - float(target.sl))
    risk_pct = _get_risk_percentage(strategy_family, horizon_type)
    risk_amount = balance * risk_pct
    if balance <= 0 or stop_distance <= 0:
        return AccountVolumeResult(target.account_id, target.broker_symbol, 0.0,
                                   "VOLUME_UNAVAILABLE", risk_pct, risk_amount, balance)
    if not symbol_row or symbol_row.get("status") != "available":
        return AccountVolumeResult(target.account_id, target.broker_symbol, 0.0,
                                   "SYMBOL_UNAVAILABLE", risk_pct, risk_amount, balance)
    inputs = _risk_inputs(symbol_row)
    if not inputs or inputs["tick_value"] <= 0 or inputs["tick_size"] <= 0:
        return AccountVolumeResult(target.account_id, target.broker_symbol, 0.0,
                                   "VOLUME_UNAVAILABLE", risk_pct, risk_amount, balance)
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
        return AccountVolumeResult(target.account_id, target.broker_symbol, 0.0,
                                   "VOLUME_BELOW_MIN", risk_pct, risk_amount, balance)
    return AccountVolumeResult(target.account_id, target.broker_symbol, volume,
                               "", risk_pct, risk_amount, balance)
