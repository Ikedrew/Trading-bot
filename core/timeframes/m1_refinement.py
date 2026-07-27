"""
Multi-Timeframe Authority — M1 Refinement Layer (Placeholder).

Responsibility: Optional execution timing refinement using M1 data.
Consulted ONLY after M5 pipeline decides should_trade=True.
NEVER generates signals, modifies scoring, or influences trade/no-trade decision.

Ownership: core/timeframes/m1_refinement.py
Dependencies: types.py, data.mt5_data.Candle
Must NOT import from: cache.py, integration.py, engine.py

Phase 1: Interface definition only. Disabled by default.
Phase 5+: Entry timing optimization logic.
"""

from __future__ import annotations

from dataclasses import dataclass

from data.mt5_data import Candle


@dataclass(frozen=True)
class RefinementContext:
    """
    M1 refinement data for entry timing optimization.

    Consulted only AFTER M5 decides to trade.
    Cannot change the trade/no-trade decision.
    """

    available: bool = False
    recent_m1_candles: tuple[Candle, ...] = ()
    suggested_entry_offset: float = 0.0  # price offset suggestion (0 = no refinement)


def get_refinement_context(candles: list[Candle]) -> RefinementContext:
    """
    Build M1 refinement context for entry timing.

    Args:
        candles: Recent M1 candles (MTF_M1_CANDLE_COUNT bars)

    Returns:
        RefinementContext with timing suggestions.

    Contract:
        - NEVER generates trade signals
        - NEVER modifies scoring
        - NEVER influences trade/no-trade decision
        - Only provides context for entry precision

    Phase 1: Returns unavailable placeholder.
    """
    if not candles:
        return RefinementContext(available=False)

    # Phase 1: placeholder — refinement not available
    return RefinementContext(available=False)
