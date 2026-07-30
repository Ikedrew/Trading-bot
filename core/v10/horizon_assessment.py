"""V10 Horizon Assessment — Expected movement magnitude model.

Answers: "What size of move is realistically expected for this opportunity?"

Does NOT contain:
  - Entry/stop/target prices
  - Execution decisions
  - Strategy selection (already done)
  - Risk parameters

Contains:
  - Horizon classification (SCALP / INTRADAY / EXTENDED)
  - Movement expectation (min/max in appropriate unit)
  - Trade lifecycle (expected duration, holding style)
  - Supporting factors and reasoning
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HorizonType(str, Enum):
    SCALP = "SCALP"
    INTRADAY = "INTRADAY"
    EXTENDED = "EXTENDED"


class MeasurementUnit(str, Enum):
    PIPS = "PIPS"
    POINTS = "POINTS"
    ATR_MULTIPLE = "ATR_MULTIPLE"


@dataclass(frozen=True)
class MovementExpectation:
    """Expected price movement range."""
    minimum_expected_move: float = 0.0
    maximum_expected_move: float = 0.0
    measurement_unit: str = MeasurementUnit.ATR_MULTIPLE.value


@dataclass(frozen=True)
class TradeLifecycle:
    """Expected trade duration and holding style."""
    expected_duration_minutes: int = 0
    holding_style: str = ""               # QUICK_REACTION / INTRADAY_DEVELOPMENT / EXTENDED_CONTINUATION


@dataclass(frozen=True)
class HorizonDecision:
    """
    Immutable horizon assessment for a given opportunity.

    Answers: "What magnitude of movement should this trade target?"
    """

    # Identity
    opportunity_id: str = ""
    symbol: str = ""
    timestamp_utc: float = 0.0

    # Classification
    horizon_type: str = HorizonType.SCALP.value
    movement_expectation: MovementExpectation = field(default_factory=MovementExpectation)
    trade_lifecycle: TradeLifecycle = field(default_factory=TradeLifecycle)

    # Context
    supporting_factors: dict[str, Any] = field(default_factory=dict)
    reasoning: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "horizon_type": self.horizon_type,
            "movement_expectation": {
                "minimum_expected_move": self.movement_expectation.minimum_expected_move,
                "maximum_expected_move": self.movement_expectation.maximum_expected_move,
                "measurement_unit": self.movement_expectation.measurement_unit,
            },
            "trade_lifecycle": {
                "expected_duration_minutes": self.trade_lifecycle.expected_duration_minutes,
                "holding_style": self.trade_lifecycle.holding_style,
            },
            "supporting_factors": dict(self.supporting_factors),
            "reasoning": list(self.reasoning),
        }
