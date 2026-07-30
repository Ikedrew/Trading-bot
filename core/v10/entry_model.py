"""V10 Entry Model — Structured trade plan from validated opportunity.

Answers: "How should this trade be constructed and entered?"

The decision hierarchy:
  H4/H1: WHY does this trade exist?
  M15:   WHERE is the opportunity forming?
  M5/M1: HOW do we enter efficiently?

M5/M1 NEVER decide whether to trade — only timing/method of an
already-approved idea from higher timeframes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TradeDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"


class EntryMethod(str, Enum):
    CONFIRMATION_ENTRY = "CONFIRMATION_ENTRY"
    LIMIT_ENTRY = "LIMIT_ENTRY"
    BREAK_ENTRY = "BREAK_ENTRY"


class EntryStatus(str, Enum):
    READY = "READY"
    WAITING = "WAITING"
    INVALID = "INVALID"


@dataclass(frozen=True)
class EntryZone:
    """Price zone for limit entries."""
    upper_bound: float = 0.0
    lower_bound: float = 0.0
    source: str = ""                      # e.g., "H1_SUPPLY_OB", "M15_DEMAND_FVG"


@dataclass(frozen=True)
class StopReference:
    """Structural stop loss placement."""
    price: float = 0.0
    structure_source: str = ""            # e.g., "below_H1_demand_OB", "above_M15_supply"
    reasoning: str = ""                   # e.g., "Below invalidation structure"


@dataclass(frozen=True)
class TargetReference:
    """Structural take profit placement."""
    price: float = 0.0
    structure_source: str = ""            # e.g., "H1_swing_high", "session_high"
    reasoning: str = ""                   # e.g., "Next liquidity target"


@dataclass(frozen=True)
class EntryDecision:
    """
    Immutable trade construction plan.

    Produced by the Entry Engine from the full V10 pipeline context.
    Consumed by risk model and execution policy (not yet implemented).
    """

    # Identity
    opportunity_id: str = ""
    symbol: str = ""
    timestamp_utc: float = 0.0

    # Core decision
    trade_direction: str = TradeDirection.NONE.value
    entry_method: str = EntryMethod.CONFIRMATION_ENTRY.value
    entry_status: str = EntryStatus.INVALID.value

    # Price levels
    entry_price: float = 0.0
    entry_zone: EntryZone = field(default_factory=EntryZone)
    stop_reference: StopReference = field(default_factory=StopReference)
    target_reference: TargetReference = field(default_factory=TargetReference)

    # Risk geometry
    risk_distance: float = 0.0
    reward_distance: float = 0.0
    expected_rr: float = 0.0

    # Context
    reasoning: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "trade_direction": self.trade_direction,
            "entry_method": self.entry_method,
            "entry_status": self.entry_status,
            "entry_price": self.entry_price,
            "entry_zone": {"upper_bound": self.entry_zone.upper_bound,
                           "lower_bound": self.entry_zone.lower_bound,
                           "source": self.entry_zone.source},
            "stop_reference": {"price": self.stop_reference.price,
                               "structure_source": self.stop_reference.structure_source,
                               "reasoning": self.stop_reference.reasoning},
            "target_reference": {"price": self.target_reference.price,
                                 "structure_source": self.target_reference.structure_source,
                                 "reasoning": self.target_reference.reasoning},
            "risk_distance": self.risk_distance,
            "reward_distance": self.reward_distance,
            "expected_rr": round(self.expected_rr, 2),
            "reasoning": list(self.reasoning),
        }
