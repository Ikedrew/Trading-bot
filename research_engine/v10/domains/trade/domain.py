"""Trade Domain — observation grain is 1 completed trade."""

from __future__ import annotations
from typing import Any
from research_engine.v10.domains.base import ResearchDomain

_QUESTIONS = [
    # OUTCOME
    {"id": "E1", "category": "outcome", "name": "True System Expectancy", "status": "active"},
    {"id": "E2", "category": "outcome", "name": "Pattern Performance", "status": "active"},
    {"id": "E3", "category": "outcome", "name": "Performance Stability Over Time", "status": "draft"},
    {"id": "E4", "category": "outcome", "name": "Profit Concentration", "status": "draft"},
    # RISK
    {"id": "R1", "category": "risk", "name": "Risk Model Effectiveness", "status": "active"},
    {"id": "R2", "category": "risk", "name": "Stop Placement Effectiveness", "status": "active"},
    {"id": "R3", "category": "risk", "name": "Target Effectiveness", "status": "draft"},
    {"id": "R4", "category": "risk", "name": "MAE/MFE Characteristics", "status": "draft"},
    {"id": "R5", "category": "risk", "name": "Theoretical vs Realised R:R", "status": "draft"},
    # EXECUTION
    {"id": "X1", "category": "execution", "name": "Execution Quality Impact", "status": "draft"},
    {"id": "X2", "category": "execution", "name": "Slippage Effect on Expectancy", "status": "draft"},
    {"id": "X3", "category": "execution", "name": "Spread Effect on Expectancy", "status": "draft"},
    # STABILITY
    {"id": "T1", "category": "stability", "name": "Edge Stability Over Time", "status": "draft"},
    {"id": "T2", "category": "stability", "name": "Post-Success Degradation", "status": "draft"},
]


class TradeDomain(ResearchDomain):
    @property
    def domain_id(self) -> str:
        return "trade"

    @property
    def name(self) -> str:
        return "Trade-Centric"

    @property
    def observation_type(self) -> str:
        return "completed_trade"

    def build_population(self, universe_events: list[dict]) -> list[dict]:
        """Trade domain uses the canonical universe directly — 1:1 mapping."""
        return universe_events

    def get_questions(self) -> list[dict[str, Any]]:
        return _QUESTIONS

    def coverage_report(self, universe_events: list[dict]) -> dict[str, Any]:
        total = len(universe_events)
        complete = sum(1 for e in universe_events
                       if e.get("quality", {}).get("data_completeness") == "COMPLETE")
        return {
            "domain": self.domain_id,
            "total_observations": total,
            "complete": complete,
            "partial": total - complete,
            "missing": 0,
            "coverage_status": "AVAILABLE" if total > 0 else "MISSING",
            "gaps": [],
        }

    def get_segmentation_filters(self) -> list[str]:
        return ["instrument", "session", "regime", "volatility", "confidence", "score_bucket"]
