"""
NEW Shadow Runtime — Simulation assumptions.

Every OPEN event explicitly declares the full simulation model (contract §14).
Assumptions are DATA, never hidden implementation behaviour. Any change to any
value below that alters simulated outcomes MUST bump
``SIMULATION_MODEL_VERSION`` (models.py) so research populations never silently
mix incompatible simulation models.
"""

from __future__ import annotations

from typing import Any

from core.shadow.models import TIMEOUT_BARS

DEFAULT_CHECKPOINT_INTERVAL = 12  # bars (~1 hour on M5)


def build_assumptions(
    *,
    horizon: str,
    entry_price_basis: str,
    checkpoint_interval: int = DEFAULT_CHECKPOINT_INTERVAL,
) -> dict[str, Any]:
    """
    Build the immutable simulation-assumption block for one simulation.

    Args:
        horizon: SCALP | INTRADAY | EXTENDED (timeout derives from profile holds).
        entry_price_basis: "ASK" (BUY) or "BID" (SELL).
        checkpoint_interval: PROGRESS cadence in closed bars.
    """
    if horizon not in TIMEOUT_BARS:
        raise ValueError(f"unknown horizon: {horizon!r}")
    if entry_price_basis not in ("ASK", "BID"):
        raise ValueError(f"invalid entry price basis: {entry_price_basis!r}")
    return {
        "simulation_model": "EXACT_FILL_COUNTERFACTUAL",
        "fill_model": "EXACT_PRICE",
        "same_bar_exit_policy": "SL_FIRST",
        "slippage_policy": "ZERO",
        "commission_policy": "ZERO",
        "spread_policy": "ZERO_COST",
        "position_size_model": "R_NORMALISED",
        "entry_price_basis": entry_price_basis,
        "timeout_bars": TIMEOUT_BARS[horizon],
        "pip_convention": "JPY_AWARE_PIP_SIZE",
        "checkpoint_interval_bars": int(checkpoint_interval),
    }
