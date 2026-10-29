"""V10 Execution Model — Broker-ready order representation.

The final output of the V10 decision pipeline.
Execution CANNOT create, modify, or override trade decisions.
It only answers: "Can this approved plan be safely sent to the broker?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExecutionType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


@dataclass(frozen=True)
class OrderDetails:
    """Broker-ready order parameters."""
    symbol: str = ""
    direction: str = ""                   # BUY / SELL
    order_type: str = ExecutionType.MARKET.value
    volume: float = 0.0                   # Lots/contracts
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0


@dataclass(frozen=True)
class ExecutionProtection:
    """Safety parameters for order execution."""
    max_slippage_price: float = 0.0       # Maximum acceptable slippage in price units
    timeout_seconds: float = 5.0          # Order timeout
    retry_allowed: bool = False           # Whether to retry on failure


@dataclass(frozen=True)
class ExecutionDecision:
    """
    Final execution decision — immutable.

    This is the last gate before the broker receives an order.
    Cannot modify direction, stop, target, or any upstream decision.
    """

    # Identity
    opportunity_id: str = ""
    symbol: str = ""
    timestamp_utc: float = 0.0

    # Approval
    approved: bool = False
    rejection_reason: str = ""

    # Order (only populated if approved)
    order_details: OrderDetails = field(default_factory=OrderDetails)

    # Execution checks
    execution_checks: dict[str, bool] = field(default_factory=dict)

    # Protection
    protection: ExecutionProtection = field(default_factory=ExecutionProtection)

    # Reasoning
    reasoning: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "approved": self.approved,
            "rejection_reason": self.rejection_reason,
            "order_details": {
                "symbol": self.order_details.symbol,
                "direction": self.order_details.direction,
                "order_type": self.order_details.order_type,
                "volume": round(self.order_details.volume, 4),
                "entry_price": self.order_details.entry_price,
                "stop_loss": self.order_details.stop_loss,
                "take_profit": self.order_details.take_profit,
            },
            "execution_checks": dict(self.execution_checks),
            "protection": {
                "max_slippage_price": self.protection.max_slippage_price,
                "timeout_seconds": self.protection.timeout_seconds,
                "retry_allowed": self.protection.retry_allowed,
            },
            "reasoning": list(self.reasoning),
        }
