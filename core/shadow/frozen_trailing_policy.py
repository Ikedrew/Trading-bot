"""Shared frozen trailing transition used by replay and prospective shadowing."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping


@dataclass(frozen=True)
class FrozenTrailingStep:
    state: dict[str, Any]
    terminal: bool = False
    exit_reason: str = ""
    exit_price: float | None = None


def initialise_frozen_trailing(
    *, entry_price: float, stop_loss: float, take_profit: float,
    risk_distance: float, timeout_bars: int,
) -> dict[str, Any]:
    values = (entry_price, stop_loss, take_profit, risk_distance)
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("TRAILING_GEOMETRY_NONFINITE")
    if float(risk_distance) <= 0 or int(timeout_bars) < 1:
        raise ValueError("TRAILING_GEOMETRY_INVALID")
    return {
        "activation_reached": False,
        "favourable_extreme": float(entry_price),
        "current_trailing_level": None,
        "prior_effective_stop": float(stop_loss),
        "bars_elapsed": 0,
        "timeout_bars": int(timeout_bars),
    }


def advance_frozen_trailing(
    *, policy: Mapping[str, Any], entry_price: float, stop_loss: float,
    take_profit: float, risk_distance: float, direction: str,
    bar_high: float, bar_low: float, bar_close: float,
    prior_state: Mapping[str, Any],
) -> FrozenTrailingStep:
    """Apply the HD09 stop-first, effective-next-bar trailing contract."""
    if policy.get("policy_type") != "TRAILING":
        raise ValueError("FROZEN_TRAILING_POLICY_TYPE_MISMATCH")
    activation_r = float(policy["activation_r"])
    distance_r = float(policy["distance_r"])
    entry, original_stop, target, risk = map(
        float, (entry_price, stop_loss, take_profit, risk_distance))
    high, low, close = map(float, (bar_high, bar_low, bar_close))
    if not all(math.isfinite(value) for value in (
            activation_r, distance_r, entry, original_stop, target, risk,
            high, low, close)) or risk <= 0 or high < low:
        raise ValueError("FROZEN_TRAILING_INPUT_INVALID")
    side = str(direction or "").upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("FROZEN_TRAILING_DIRECTION_INVALID")

    state = dict(prior_state or {})
    required = {
        "activation_reached", "favourable_extreme", "prior_effective_stop",
        "bars_elapsed", "timeout_bars",
    }
    if not required.issubset(state):
        raise ValueError("FROZEN_TRAILING_STATE_INCOMPLETE")
    activated = bool(state["activation_reached"])
    extreme = float(state["favourable_extreme"])
    bars_elapsed = int(state["bars_elapsed"]) + 1
    timeout_bars = int(state["timeout_bars"])

    if not activated:
        effective_stop = original_stop
    elif side == "BUY":
        effective_stop = max(original_stop, extreme - distance_r * risk)
    else:
        effective_stop = min(original_stop, extreme + distance_r * risk)
    state["prior_effective_stop"] = effective_stop
    state["current_trailing_level"] = effective_stop if activated else None
    state["bars_elapsed"] = bars_elapsed

    if side == "BUY":
        if low <= effective_stop:
            return FrozenTrailingStep(state, True, "stop_loss", effective_stop)
        if high >= target:
            return FrozenTrailingStep(state, True, "take_profit", target)
    else:
        if high >= effective_stop:
            return FrozenTrailingStep(state, True, "stop_loss", effective_stop)
        if low <= target:
            return FrozenTrailingStep(state, True, "take_profit", target)
    if bars_elapsed >= timeout_bars:
        return FrozenTrailingStep(state, True, "timeout", close)

    extreme = max(extreme, high) if side == "BUY" else min(extreme, low)
    state["favourable_extreme"] = extreme
    if not activated:
        excursion = extreme - entry if side == "BUY" else entry - extreme
        activated = excursion >= activation_r * risk
    state["activation_reached"] = activated
    return FrozenTrailingStep(state)


__all__ = [
    "FrozenTrailingStep", "advance_frozen_trailing",
    "initialise_frozen_trailing",
]
