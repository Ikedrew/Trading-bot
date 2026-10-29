"""
Shadow Optimisation — Virtual decision engine.

Applies candidate changes to determine what the candidate WOULD have decided.
NEVER executes anything. Pure calculation only.

SAFETY GUARANTEE: This module has NO imports from execution/, mt5_execution,
MetaTrader5, or any broker API. It cannot place orders.
"""

from __future__ import annotations

from typing import Any


def apply_candidate_decision(
    baseline_trade: dict[str, Any],
    change_definition: dict[str, Any],
) -> dict[str, Any]:
    """
    Calculate what the candidate would have done differently.

    Args:
        baseline_trade: The real trade/opportunity data
        change_definition: Candidate's proposed changes

    Returns:
        Shadow decision dict with modified parameters.
        Returns {"decision": "NO_TRADE"} if candidate would have filtered this trade.
    """
    entry = baseline_trade.get("entry_price", 0)
    stop = baseline_trade.get("stop_loss", 0)
    target = baseline_trade.get("take_profit", 0)
    direction = baseline_trade.get("direction", "")
    score = baseline_trade.get("score", 0) or baseline_trade.get("dt_score_strategy", 0)

    shadow_stop = stop
    shadow_target = target
    shadow_decision = "EXECUTE"

    # Apply stop multiplier
    if "stop_multiplier" in change_definition:
        multiplier = change_definition["stop_multiplier"]
        risk_distance = abs(entry - stop)
        new_risk = risk_distance * multiplier
        if direction == "BUY":
            shadow_stop = entry - new_risk
        elif direction == "SELL":
            shadow_stop = entry + new_risk

    # Apply target multiplier
    if "target_multiplier" in change_definition:
        multiplier = change_definition["target_multiplier"]
        reward_distance = abs(target - entry)
        new_reward = reward_distance * multiplier
        if direction == "BUY":
            shadow_target = entry + new_reward
        elif direction == "SELL":
            shadow_target = entry - new_reward

    # Apply score threshold filter
    if "score_threshold" in change_definition:
        if score < change_definition["score_threshold"]:
            shadow_decision = "NO_TRADE"

    # Apply regime filter
    if "regime_filter" in change_definition:
        regime = (baseline_trade.get("regime", "") or
                  baseline_trade.get("dt_v10_regime", "") or
                  baseline_trade.get("market", {}).get("regime", ""))
        if regime.upper() != change_definition["regime_filter"].upper():
            shadow_decision = "NO_TRADE"

    # Apply session filter
    if "session_filter" in change_definition:
        session = (baseline_trade.get("session", "") or
                   baseline_trade.get("market", {}).get("session", ""))
        if session.upper() != change_definition["session_filter"].upper():
            shadow_decision = "NO_TRADE"

    return {
        "decision": shadow_decision,
        "entry_price": entry,
        "stop_loss": round(shadow_stop, 6),
        "take_profit": round(shadow_target, 6),
        "direction": direction,
    }
