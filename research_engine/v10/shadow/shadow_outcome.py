"""
Shadow Optimisation — Outcome calculation.

Calculates what WOULD have happened to a shadow trade given actual market data.
Uses the real exit price and determines R-multiple from shadow stop/target.

SAFETY: Pure math only. No broker interaction.
"""

from __future__ import annotations

from typing import Any


def calculate_shadow_outcome(
    shadow_decision: dict[str, Any],
    actual_exit_price: float,
    actual_exit_reason: str = "",
) -> dict[str, float]:
    """
    Calculate the shadow trade outcome using actual market data.

    Uses the REAL exit price (what actually happened in the market)
    and computes what R-multiple the shadow parameters would have produced.

    Args:
        shadow_decision: {"entry_price", "stop_loss", "take_profit", "direction"}
        actual_exit_price: The real price where the trade exited
        actual_exit_reason: "STOP_LOSS", "TAKE_PROFIT", "OTHER"

    Returns:
        {"r_multiple": float, "pnl_direction": float}
    """
    if shadow_decision.get("decision") == "NO_TRADE":
        return {"r_multiple": 0.0, "pnl_direction": 0.0}

    entry = shadow_decision.get("entry_price", 0)
    stop = shadow_decision.get("stop_loss", 0)
    target = shadow_decision.get("take_profit", 0)
    direction = shadow_decision.get("direction", "")

    if entry == 0 or stop == 0:
        return {"r_multiple": 0.0, "pnl_direction": 0.0}

    risk_distance = abs(entry - stop)
    if risk_distance == 0:
        return {"r_multiple": 0.0, "pnl_direction": 0.0}

    # Calculate price movement in trade direction
    if direction == "BUY":
        move = actual_exit_price - entry
    elif direction == "SELL":
        move = entry - actual_exit_price
    else:
        return {"r_multiple": 0.0, "pnl_direction": 0.0}

    # Check if shadow stop or target would have been hit first
    # (simplified: uses actual exit as proxy for where price went)
    r_multiple = round(move / risk_distance, 4)

    # Clamp to stop loss (can't lose more than -1R with a stop)
    # Unless stop was widened beyond where price actually went
    if direction == "BUY" and actual_exit_price <= stop:
        r_multiple = -1.0
    elif direction == "SELL" and actual_exit_price >= stop:
        r_multiple = -1.0

    return {
        "r_multiple": r_multiple,
        "pnl_direction": move,
    }


def calculate_baseline_outcome(
    baseline_trade: dict[str, Any],
) -> dict[str, float]:
    """Extract baseline R-multiple from the actual trade record."""
    r = baseline_trade.get("realised_r", 0) or baseline_trade.get("r_multiple", 0)
    pnl = baseline_trade.get("final_pnl", 0) or baseline_trade.get("net_realised_pnl", 0)
    return {"r_multiple": r, "pnl_direction": pnl}
