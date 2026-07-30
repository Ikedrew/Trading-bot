"""V10 Strategy Family — Strategy classification model.

Represents the engine's decision about WHAT TYPE of trade an opportunity is.

Does NOT contain:
  - Entry/stop/target
  - Execution logic
  - Position sizing
  - Risk management

Contains:
  - Which strategy family matches the opportunity
  - Directional context (from H1 authority)
  - Confidence and reasoning
  - Supporting conditions map
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StrategyFamily(str, Enum):
    """Classified strategy families in priority order."""
    LIQUIDITY_SWEEP_REVERSAL = "LIQUIDITY_SWEEP_REVERSAL"
    FALSE_BREAK = "FALSE_BREAK"
    TREND_CONTINUATION = "TREND_CONTINUATION"
    BREAKOUT_EXPANSION = "BREAKOUT_EXPANSION"
    MEAN_REVERSION = "MEAN_REVERSION"
    RANGE_REACTION = "RANGE_REACTION"
    NONE = "NONE"


# Priority order (index 0 = highest priority)
STRATEGY_PRIORITY = [
    StrategyFamily.LIQUIDITY_SWEEP_REVERSAL,
    StrategyFamily.FALSE_BREAK,
    StrategyFamily.TREND_CONTINUATION,
    StrategyFamily.BREAKOUT_EXPANSION,
    StrategyFamily.MEAN_REVERSION,
    StrategyFamily.RANGE_REACTION,
]


@dataclass(frozen=True)
class StrategyDecision:
    """
    Immutable strategy classification for a given opportunity.

    Answers: "What type of market behaviour does this opportunity represent?"
    """

    # Identity
    opportunity_id: str = ""
    symbol: str = ""
    timestamp_utc: float = 0.0

    # Classification
    strategy_family: str = StrategyFamily.NONE.value
    directional_context: str = ""         # BULLISH / BEARISH / NEUTRAL
    strategy_confidence: float = 0.0      # 0.0–1.0

    # Reasoning
    reasoning: list[str] = field(default_factory=list)

    # Supporting conditions (which rules were satisfied)
    supporting_conditions: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "strategy_family": self.strategy_family,
            "directional_context": self.directional_context,
            "strategy_confidence": round(self.strategy_confidence, 4),
            "reasoning": list(self.reasoning),
            "supporting_conditions": dict(self.supporting_conditions),
        }
