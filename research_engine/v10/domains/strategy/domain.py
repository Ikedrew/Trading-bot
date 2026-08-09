"""Strategy Domain — observation grain is 1 strategy/opportunity observation."""

from __future__ import annotations
from typing import Any
from research_engine.v10.domains.base import ResearchDomain

_QUESTIONS = [
    # PERFORMANCE
    {"id": "S1", "category": "performance", "name": "Strategy Family Performance", "status": "draft"},
    {"id": "S2", "category": "performance", "name": "Strategy Family Failure Analysis", "status": "draft"},
    {"id": "S3", "category": "performance", "name": "Strategy-Regime Interaction", "status": "draft"},
    {"id": "S4", "category": "performance", "name": "Strategy Confidence Predictiveness", "status": "draft"},
    # CONSTRUCTION
    {"id": "S5", "category": "construction", "name": "Strategy Condition Importance", "status": "draft"},
    {"id": "S6", "category": "construction", "name": "Redundant Conditions", "status": "draft"},
    {"id": "S7", "category": "construction", "name": "Optimal Condition Combinations", "status": "draft"},
    {"id": "S8", "category": "construction", "name": "Minimum Necessary Conditions", "status": "draft"},
    # OPPORTUNITY
    {"id": "OQ1", "category": "opportunity", "name": "Opportunity Quality Predictiveness", "status": "active"},
    {"id": "OQ2", "category": "opportunity", "name": "Opportunity Failure Analysis", "status": "active"},
    {"id": "OQ3", "category": "opportunity", "name": "Opportunity Success Conditions", "status": "draft"},
    {"id": "OQ4", "category": "opportunity", "name": "Opportunity Quality Deterioration", "status": "draft"},
    {"id": "OQ5", "category": "opportunity", "name": "Strategy-Opportunity Alignment", "status": "draft"},
]


class StrategyDomain(ResearchDomain):
    @property
    def domain_id(self) -> str:
        return "strategy"

    @property
    def name(self) -> str:
        return "Strategy-Centric"

    @property
    def observation_type(self) -> str:
        return "strategy_observation"

    def build_population(self, universe_events: list[dict]) -> list[dict]:
        """
        Build strategy observations from universe events.

        Each trade produces a strategy observation with its
        strategy context and outcome.
        """
        observations = []
        for e in universe_events:
            strat = e.get("strategy", {})
            ex = e.get("execution", {})
            dec = e.get("decision", {})

            observations.append({
                "strategy_event_id": f"strat_{ex.get('ticket', '')}",
                "trade_id": e.get("trade_id", ""),
                "symbol": ex.get("symbol", ""),
                "timestamp": ex.get("entry_time", 0),
                "strategy_family": strat.get("family", ""),
                "pattern": strat.get("pattern", ""),
                "conditions_met": strat.get("conditions_met", 0),
                "strategy_confidence": strat.get("strategy_confidence", 0),
                "opportunity_quality": strat.get("opportunity_quality", 0),
                "opportunity_type": strat.get("opportunity_type", ""),
                "components": dec.get("components", {}),
                "trade_outcome_r": ex.get("r_multiple", 0),
                "trade_pnl": ex.get("net_realised_pnl", 0),
            })
        return observations

    def get_questions(self) -> list[dict[str, Any]]:
        return _QUESTIONS

    def coverage_report(self, universe_events: list[dict]) -> dict[str, Any]:
        total = len(universe_events)
        with_family = sum(1 for e in universe_events if e.get("strategy", {}).get("family"))
        with_pattern = sum(1 for e in universe_events if e.get("strategy", {}).get("pattern"))
        with_quality = sum(1 for e in universe_events
                          if e.get("strategy", {}).get("opportunity_quality", 0) > 0)

        gaps = []
        if with_family < total:
            gaps.append(f"{total - with_family} trades missing strategy family")
        if with_quality < total:
            gaps.append(f"{total - with_quality} trades missing opportunity quality")

        complete = min(with_family, with_pattern)
        return {
            "domain": self.domain_id,
            "total_observations": total,
            "complete": complete,
            "partial": total - complete,
            "missing": 0,
            "coverage_status": "AVAILABLE" if with_pattern == total else "PARTIAL",
            "gaps": gaps,
        }

    def get_segmentation_filters(self) -> list[str]:
        return ["instrument", "regime", "confidence", "score_bucket"]
