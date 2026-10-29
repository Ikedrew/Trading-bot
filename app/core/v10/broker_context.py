"""V10 Broker Context — Runtime truth from MT5 terminal.

Contains FACTS about broker/symbol/market state.
No trading decisions. No strategy. No risk decisions.

All values default to "unavailable" (False/0.0) — causing
downstream engines to reject cleanly rather than guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BrokerContext:
    """Live MT5 broker and symbol state — single source of truth."""

    # ─── Connection ───────────────────────────────────────────
    connected: bool = False
    server: str = ""
    terminal_name: str = ""

    # ─── Symbol metadata ──────────────────────────────────────
    symbol: str = ""
    symbol_available: bool = False
    market_open: bool = False
    trade_mode: int = 0                   # MT5 SYMBOL_TRADE_MODE enum
    execution_mode: int = 0               # MT5 SYMBOL_TRADE_EXEMODE enum

    # ─── Pricing ──────────────────────────────────────────────
    bid: float = 0.0
    ask: float = 0.0
    spread: float = 0.0                   # ask - bid in price units
    digits: int = 0                       # Decimal precision (e.g., 5 for EURUSD)
    point: float = 0.0                    # Minimum price change (e.g., 0.00001)

    # ─── Trading specification ────────────────────────────────
    contract_size: float = 0.0            # Units per lot (e.g., 100000 for FX)
    tick_size: float = 0.0               # Minimum price step
    tick_value: float = 0.0              # Monetary value per tick per lot ($)

    # ─── Volume rules ─────────────────────────────────────────
    volume_min: float = 0.0               # Minimum order size in lots
    volume_max: float = 0.0               # Maximum order size in lots
    volume_step: float = 0.0             # Lot increment (e.g., 0.01)

    # ─── Execution restrictions ───────────────────────────────
    stops_level: int = 0                  # Min SL/TP distance in points
    freeze_level: int = 0                 # Freeze zone distance in points

    # ─── Account state (from same MT5 call) ───────────────────
    available_margin: float = 0.0
    existing_positions: int = 0
    account_balance: float = 0.0

    @property
    def available(self) -> bool:
        """True if broker data was successfully read."""
        return self.connected and self.symbol_available

    def to_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "symbol": self.symbol,
            "symbol_available": self.symbol_available,
            "market_open": self.market_open,
            "bid": self.bid,
            "ask": self.ask,
            "spread": self.spread,
            "digits": self.digits,
            "point": self.point,
            "contract_size": self.contract_size,
            "tick_size": self.tick_size,
            "tick_value": self.tick_value,
            "volume_min": self.volume_min,
            "volume_max": self.volume_max,
            "volume_step": self.volume_step,
            "stops_level": self.stops_level,
            "freeze_level": self.freeze_level,
            "available_margin": self.available_margin,
            "existing_positions": self.existing_positions,
            "account_balance": self.account_balance,
        }
