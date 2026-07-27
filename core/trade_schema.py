"""
Canonical Trade Schema — Single source of truth for trade representation.

Import-safe for ALL layers (runtime, offline analysis, logging, testing).
NO execution logic. NO scoring logic. NO risk logic. PURE data contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class TradeOutcome(str, Enum):
    """Trade result classification."""

    WIN = "WIN"
    LOSS = "LOSS"
    BREAKEVEN = "BREAKEVEN"


@dataclass(frozen=True)
class CanonicalTradeEvent:
    """
    Immutable canonical representation of a trade used across live + offline systems.

    This is the single data contract for trade records. All systems that produce
    or consume trade data should map to/from this schema.
    """

    # ─── IDENTITY ─────────────────────────────────────────────────────
    trade_id: str
    symbol: str

    # ─── TIMING ───────────────────────────────────────────────────────
    entry_time: str
    exit_time: str | None = None

    # ─── EXECUTION ────────────────────────────────────────────────────
    entry_price: float = 0.0
    exit_price: float | None = None
    position_size: float = 0.0

    # ─── RISK / RESULT ────────────────────────────────────────────────
    entry_r: float = 0.0
    final_r: float | None = None
    mfe: float = 0.0
    mae: float = 0.0
    outcome: TradeOutcome | None = None

    # ─── CONFIRMATION CONTEXT ─────────────────────────────────────────
    confirmation_strength: str = "UNKNOWN"
    entry_timing: str = "UNKNOWN"
    market_regime: str = "UNKNOWN"

    # ─── MANAGEMENT FLAGS ─────────────────────────────────────────────
    breakeven_triggered: bool = False
    trailing_triggered: bool = False
    partials_taken: list[float] = field(default_factory=list)


def to_dict(event: CanonicalTradeEvent) -> dict[str, Any]:
    """
    Convert CanonicalTradeEvent to a JSON-serializable dict.

    Handles enum conversion for TradeOutcome.
    """
    data = asdict(event)
    if data.get("outcome") is not None:
        data["outcome"] = data["outcome"].value if hasattr(data["outcome"], "value") else str(data["outcome"])
    return data
