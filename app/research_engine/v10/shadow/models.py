"""
Shadow Optimisation — Data models.

SAFETY: No model in this module holds a broker connection or execution capability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research_engine.v10.base import timestamp_now


class ShadowStatus:
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class ShadowCandidate:
    """A candidate actively being shadow-tested."""
    shadow_id: str
    candidate_id: str
    baseline_id: str = ""
    status: str = "ACTIVE"
    started_at: str = ""
    change_definition: dict[str, Any] = field(default_factory=dict)
    filters: dict[str, str] = field(default_factory=dict)
    target_questions: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.started_at:
            self.started_at = timestamp_now()
        if not self.metrics:
            self.metrics = {
                "opportunities_seen": 0,
                "eligible_trades": 0,
                "shadow_trades": 0,
                "completed_comparisons": 0,
            }

    def to_dict(self) -> dict[str, Any]:
        return {
            "shadow_id": self.shadow_id,
            "candidate_id": self.candidate_id,
            "baseline_id": self.baseline_id,
            "status": self.status,
            "started_at": self.started_at,
            "change_definition": self.change_definition,
            "filters": self.filters,
            "target_questions": self.target_questions,
            "metrics": self.metrics,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ShadowCandidate:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ShadowComparison:
    """One paired baseline/shadow observation for the same opportunity."""
    comparison_id: str
    shadow_id: str
    candidate_id: str
    opportunity_id: str = ""
    trade_id: str = ""
    symbol: str = ""
    direction: str = ""
    timestamp: str = ""

    # Baseline
    baseline_decision: str = ""  # EXECUTE or NO_TRADE
    baseline_entry: float = 0.0
    baseline_stop: float = 0.0
    baseline_target: float = 0.0
    baseline_r: float = 0.0
    baseline_pnl: float = 0.0

    # Shadow
    shadow_decision: str = ""
    shadow_entry: float = 0.0
    shadow_stop: float = 0.0
    shadow_target: float = 0.0
    shadow_r: float = 0.0
    shadow_pnl: float = 0.0

    # Derived
    difference_r: float = 0.0
    outcome_source: str = ""  # "live_trade" or "market_replay"

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = timestamp_now()
        self.difference_r = round(self.shadow_r - self.baseline_r, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "comparison_id": self.comparison_id,
            "shadow_id": self.shadow_id,
            "candidate_id": self.candidate_id,
            "opportunity_id": self.opportunity_id,
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "timestamp": self.timestamp,
            "baseline_decision": self.baseline_decision,
            "baseline_entry": self.baseline_entry,
            "baseline_stop": self.baseline_stop,
            "baseline_target": self.baseline_target,
            "baseline_r": self.baseline_r,
            "baseline_pnl": self.baseline_pnl,
            "shadow_decision": self.shadow_decision,
            "shadow_entry": self.shadow_entry,
            "shadow_stop": self.shadow_stop,
            "shadow_target": self.shadow_target,
            "shadow_r": self.shadow_r,
            "shadow_pnl": self.shadow_pnl,
            "difference_r": self.difference_r,
            "outcome_source": self.outcome_source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ShadowComparison:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
