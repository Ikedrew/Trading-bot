"""
Market Context Layer — Unified market interpretation service.

Produces a single authoritative MarketContext per symbol per cycle.
Does NOT influence trading decisions (Phase 1: shadow/observability only).
"""

from core.market_context.models import MarketContext, Direction, Regime, Phase
from core.market_context.builder import MarketContextBuilder

__all__ = [
    "MarketContext",
    "MarketContextBuilder",
    "Direction",
    "Regime",
    "Phase",
]
