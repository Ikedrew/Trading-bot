"""
Structured risk decision types — accept or reject with full context.

Replaces raw `OrderIntent | None` returns with typed, inspectable outcomes.
Downstream consumers can pattern-match on `accepted` field.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union

from risk.levels import RiskRejection
from risk.models import OrderIntent


@dataclass(frozen=True)
class RiskAccepted:
    """Risk layer approved the trade — intent is ready for execution."""
    accepted: Literal[True] = True
    intent: OrderIntent = None  # type: ignore[assignment]


@dataclass(frozen=True)
class RiskRejected:
    """Risk layer rejected the trade — rejection contains reason + context."""
    accepted: Literal[False] = False
    rejection: RiskRejection = None  # type: ignore[assignment]


# Union type for downstream consumers
RiskDecision = Union[RiskAccepted, RiskRejected]


def accept(intent: OrderIntent) -> RiskAccepted:
    """Create an accepted risk decision."""
    return RiskAccepted(accepted=True, intent=intent)


def reject(rejection: RiskRejection) -> RiskRejected:
    """Create a rejected risk decision."""
    return RiskRejected(accepted=False, rejection=rejection)
