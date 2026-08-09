"""Decision Domain — observation grain is 1 decision event."""

from __future__ import annotations
from typing import Any
from research_engine.v10.domains.base import ResearchDomain

_QUESTIONS = [
    # PREDICTION
    {"id": "D1", "category": "prediction", "name": "Score Predictive Power", "status": "active"},
    {"id": "D2", "category": "prediction", "name": "EV Calibration", "status": "active"},
    {"id": "D3", "category": "prediction", "name": "Decision Threshold Effectiveness", "status": "active"},
    {"id": "D4", "category": "prediction", "name": "Component Predictive Value", "status": "draft"},
    {"id": "D5", "category": "prediction", "name": "Component Disagreement Predicts Failure", "status": "draft"},
    # SELECTION
    {"id": "D6", "category": "selection", "name": "Executed vs Rejected Opportunity Quality", "status": "draft"},
    {"id": "D7", "category": "selection", "name": "Rejected Opportunity Actual Quality", "status": "draft"},
    {"id": "D8", "category": "selection", "name": "Best Opportunity Selection", "status": "draft"},
    {"id": "D9", "category": "selection", "name": "Selectivity vs Quality", "status": "draft"},
    # LIFECYCLE
    {"id": "D10", "category": "lifecycle", "name": "Decision Quality Deterioration Point", "status": "draft"},
    {"id": "D11", "category": "lifecycle", "name": "Detection to Execution Quality Change", "status": "draft"},
    {"id": "D12", "category": "lifecycle", "name": "Decision Consistency Across Conditions", "status": "draft"},
]


class DecisionDomain(ResearchDomain):
    @property
    def domain_id(self) -> str:
        return "decision"

    @property
    def name(self) -> str:
        return "Decision-Centric"

    @property
    def observation_type(self) -> str:
        return "decision_event"

    def build_population(self, universe_events: list[dict]) -> list[dict]:
        """
        Build decision observations from universe events.

        Currently: 1 decision per executed trade (from the decision block).
        Future: will also include NO_TRADE decisions from decision_trace logs.
        """
        decisions = []
        for e in universe_events:
            dec = e.get("decision", {})
            ex = e.get("execution", {})
            quality = e.get("quality", {})

            decisions.append({
                "decision_id": f"dec_{ex.get('ticket', '')}",
                "trade_id": e.get("trade_id", ""),
                "symbol": ex.get("symbol", ""),
                "timestamp": ex.get("entry_time", 0),
                "decision_type": "EXECUTE",
                "score": dec.get("score", 0),
                "confidence": dec.get("confidence", 0),
                "strategy": dec.get("strategy", ""),
                "components": dec.get("components", {}),
                "weakest_component": dec.get("weakest_component", ""),
                "ev": dec.get("ev"),
                "p_success": dec.get("p_success"),
                "eventual_outcome_r": ex.get("r_multiple", 0),
                "eventual_pnl": ex.get("net_realised_pnl", 0),
                "join_method": quality.get("join_method", ""),
            })
        return decisions

    def get_questions(self) -> list[dict[str, Any]]:
        return _QUESTIONS

    def coverage_report(self, universe_events: list[dict]) -> dict[str, Any]:
        total = len(universe_events)
        with_score = sum(1 for e in universe_events if e.get("decision", {}).get("score"))
        with_components = sum(1 for e in universe_events if e.get("decision", {}).get("components"))

        gaps = []
        if with_score < total:
            gaps.append(f"{total - with_score} trades missing decision score")
        # NO_TRADE decisions not yet available
        gaps.append("NO_TRADE decisions not yet in population (DATA GAP: requires decision_trace expansion)")

        return {
            "domain": self.domain_id,
            "total_observations": total,
            "complete": with_components,
            "partial": total - with_components,
            "missing": 0,
            "coverage_status": "PARTIAL",
            "gaps": gaps,
            "note": "Currently limited to EXECUTE decisions. NO_TRADE population pending.",
        }

    def get_segmentation_filters(self) -> list[str]:
        return ["instrument", "confidence", "score_bucket", "regime"]
