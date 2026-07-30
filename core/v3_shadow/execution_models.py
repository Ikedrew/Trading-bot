"""
V3 Execution Assessment Model — Final decision delivery layer.

Records HOW a validated decision would be expressed in the market.
Does NOT create intelligence. Does NOT override any upstream layer.

It answers: "Given an approved V3 research decision, how would this
trade be executed and managed?"

The Execution Policy is deliberately boring. All intelligence has
already happened upstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_EXECUTION_SCHEMA_VERSION = "v3_execution_assessment_v1"


# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION STATES
# ═══════════════════════════════════════════════════════════════════════════════

READY_FOR_EXECUTION = "READY_FOR_EXECUTION"
EXECUTION_CONSTRAINED = "EXECUTION_CONSTRAINED"
NOT_EXECUTABLE = "NOT_EXECUTABLE"
SIMULATED_ONLY = "SIMULATED_ONLY"


# ═══════════════════════════════════════════════════════════════════════════════
# ORDER TYPES
# ═══════════════════════════════════════════════════════════════════════════════

ORDER_MARKET = "MARKET_ENTRY"
ORDER_LIMIT = "LIMIT_ENTRY"
ORDER_STOP = "STOP_ENTRY"


# ═══════════════════════════════════════════════════════════════════════════════
# MANAGEMENT PROFILES
# ═══════════════════════════════════════════════════════════════════════════════

MGMT_FAST = "FAST_MANAGEMENT"
MGMT_TRAIL_BREAKEVEN = "TRAIL_AND_BREAKEVEN"
MGMT_STRUCTURAL = "STRUCTURAL_MANAGEMENT"
MGMT_NONE = "NO_MANAGEMENT"


# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION ASSESSMENT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ExecutionAssessment:
    """
    Immutable record of how a V3 decision would be executed.

    Produced at the end of the V3 shadow pipeline each cycle.
    Records: direction, prices, order type, management, and quality metrics.
    """
    # Identity
    symbol: str = ""
    timestamp_utc: float = 0.0
    schema_version: str = _EXECUTION_SCHEMA_VERSION

    # Decision
    direction: str = ""                  # BULLISH / BEARISH / NEUTRAL
    execution_state: str = NOT_EXECUTABLE

    # Order
    order_type: str = ORDER_MARKET
    entry_price: float = 0.0
    stop_price: float = 0.0
    target_price: float = 0.0

    # Position
    position_size_lots: float = 0.0      # Research default (not real sizing)

    # Upstream context (for traceability)
    horizon: str = ""
    risk_state: str = ""
    entry_state: str = ""
    opportunity_state: str = ""

    # Execution quality
    spread_at_entry: float = 0.0         # Pips
    estimated_slippage: float = 0.0      # Pips (research estimate)
    total_entry_cost_pips: float = 0.0   # spread + slippage

    # Management
    management_profile: str = MGMT_NONE

    # Evidence
    supporting_factors: list[str] = field(default_factory=list)
    conflicting_factors: list[str] = field(default_factory=list)

    # Observations
    observations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSONL persistence."""
        return {
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "direction": self.direction,
            "execution_state": self.execution_state,
            "order_type": self.order_type,
            "entry_price": round(self.entry_price, 8),
            "stop_price": round(self.stop_price, 8),
            "target_price": round(self.target_price, 8),
            "position_size_lots": round(self.position_size_lots, 4),
            "horizon": self.horizon,
            "risk_state": self.risk_state,
            "entry_state": self.entry_state,
            "opportunity_state": self.opportunity_state,
            "spread_at_entry": round(self.spread_at_entry, 4),
            "estimated_slippage": round(self.estimated_slippage, 4),
            "total_entry_cost_pips": round(self.total_entry_cost_pips, 4),
            "management_profile": self.management_profile,
            "supporting_factors": list(self.supporting_factors),
            "conflicting_factors": list(self.conflicting_factors),
            "observations": list(self.observations),
        }
