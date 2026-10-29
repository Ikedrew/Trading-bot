"""
OrderIntent — The approved execution instruction at the Risk→Execution boundary.

Represents: "An approved order that may be sent to the broker."

OrderIntent is NOT:
    - A strategy object
    - A signal object
    - A market analysis object
    - A policy decision

It contains ONLY:
    - What to trade (symbol, side)
    - How much (volume)
    - Entry reference (for slippage tracking)
    - Risk boundaries (SL, TP)
    - Order type (MARKET, LIMIT, etc.)
    - Observability metadata (pattern tag, risk_id)

INVARIANT: Frozen after construction. Produced by RiskManager, consumed by Execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from strategy.signals import Side


@dataclass(frozen=True)
class OrderIntent:
    """
    Immutable execution instruction produced by the Risk layer.

    Consumed by MT5Execution.execute() — execution does not interpret
    any field for analytical purposes. All fields serve execution mechanics
    or observability.
    """
    # ─── EXECUTION IDENTITY ───────────────────────────────────────────
    symbol: str                          # Instrument name (canonical "EURUSD" or broker-resolved "EURUSD_SB")
    side: Side                           # Trade direction (BUY or SELL)
    volume: float                        # Position size (lots)

    # ─── PRICE LEVELS ─────────────────────────────────────────────────
    entry_reference: float               # Reference price at risk computation time (slippage tracking)
    sl: float                            # Stop-loss price
    tp: float                            # Take-profit price

    # ─── ORDER TYPE ───────────────────────────────────────────────────
    entry_type: str = "MARKET"           # "MARKET" | "LIMIT" | "STOP" (currently always MARKET)

    # ─── OBSERVABILITY METADATA ───────────────────────────────────────
    pattern: str = ""                    # Pattern tag for broker comment (e.g., "BULLISH_ENGULFING")
    risk_id: str = ""                    # Unique risk computation ID (for audit trail linkage)
    metadata: dict[str, Any] = field(default_factory=dict)  # Arbitrary observability context

