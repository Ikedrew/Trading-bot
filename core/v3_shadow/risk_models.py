"""
V3 Risk Assessment Model — Evaluates risk geometry viability.

Determines whether the risk structure of an opportunity is logically acceptable
given the expected movement profile, stop placement, and transaction costs.

It does NOT:
    - Create trade signals
    - Override opportunity or horizon decisions
    - Determine entry timing or direction
    - Calculate position sizing for execution

It answers: "If we are considering this opportunity, does the risk
structure make sense?"

Risk geometry is another HYPOTHESIS to validate through research.
The research engine will determine which risk configurations
produce positive cost-adjusted outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_RISK_SCHEMA_VERSION = "v3_risk_assessment_v1"


# ═══════════════════════════════════════════════════════════════════════════════
# RISK STATES
# ═══════════════════════════════════════════════════════════════════════════════

ACCEPTABLE_RISK = "ACCEPTABLE_RISK"          # Geometry supports participation
MARGINAL_RISK = "MARGINAL_RISK"              # Possible but requires more evidence
POOR_RISK = "POOR_RISK"                      # Cost or geometry makes participation unlikely
INSUFFICIENT_RISK_DATA = "INSUFFICIENT_RISK_DATA"  # Not enough information


# ═══════════════════════════════════════════════════════════════════════════════
# RISK ASSESSMENT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RiskAssessment:
    """
    Immutable assessment of risk geometry viability.

    Evaluates: expected movement vs risk required vs trading costs.
    Produced each cycle from HorizonAssessment + V3MarketContext.
    """
    # Identity
    symbol: str = ""
    timestamp_utc: float = 0.0
    schema_version: str = _RISK_SCHEMA_VERSION

    # Horizon context
    horizon: str = ""                        # SCALP / INTRADAY / EXTENDED / NO_HORIZON

    # Expected movement
    expected_move_min_pips: float = 0.0
    expected_move_max_pips: float = 0.0

    # Stop geometry
    stop_distance_pips: float = 0.0          # Estimated stop from structure
    stop_source: str = ""                    # M5_STRUCTURE / M15_STRUCTURE / H1_STRUCTURE

    # Target geometry
    target_distance_pips: float = 0.0        # Estimated target
    target_source: str = ""                  # LIQUIDITY_TARGET / OPPOSING_ZONE / FIXED_RR

    # Risk/Reward
    risk_reward_ratio: float = 0.0           # target / stop

    # Cost analysis
    spread_cost_pips: float = 0.0            # Current spread
    spread_to_risk_ratio: float = 0.0        # spread / stop (critical V3 metric)
    cost_adjusted_expectancy: float = 0.0    # Rough: (wr * rr - (1-wr)) - spread_ratio

    # Quality
    risk_quality_score: float = 0.0          # 0-1 composite
    risk_state: str = INSUFFICIENT_RISK_DATA

    # Evidence
    supporting_factors: list[str] = field(default_factory=list)
    conflicting_factors: list[str] = field(default_factory=list)

    # Confidence
    confidence: float = 0.0

    # Observations
    observations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSONL persistence."""
        return {
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "horizon": self.horizon,
            "expected_move_min_pips": round(self.expected_move_min_pips, 1),
            "expected_move_max_pips": round(self.expected_move_max_pips, 1),
            "stop_distance_pips": round(self.stop_distance_pips, 2),
            "stop_source": self.stop_source,
            "target_distance_pips": round(self.target_distance_pips, 2),
            "target_source": self.target_source,
            "risk_reward_ratio": round(self.risk_reward_ratio, 3),
            "spread_cost_pips": round(self.spread_cost_pips, 4),
            "spread_to_risk_ratio": round(self.spread_to_risk_ratio, 4),
            "cost_adjusted_expectancy": round(self.cost_adjusted_expectancy, 4),
            "risk_quality_score": round(self.risk_quality_score, 4),
            "risk_state": self.risk_state,
            "supporting_factors": list(self.supporting_factors),
            "conflicting_factors": list(self.conflicting_factors),
            "confidence": round(self.confidence, 4),
            "observations": list(self.observations),
        }
