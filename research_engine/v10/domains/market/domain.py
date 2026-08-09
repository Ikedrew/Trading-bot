"""Market Domain — observation grain is 1 market-state observation at trade time."""

from __future__ import annotations
from typing import Any
from research_engine.v10.domains.base import ResearchDomain

_QUESTIONS = [
    # REGIME
    {"id": "M1", "category": "regime", "name": "Regime Expectancy", "status": "active"},
    {"id": "M2", "category": "regime", "name": "Regime Damage Analysis", "status": "draft"},
    {"id": "M3", "category": "regime", "name": "Regime Transition Failure Prediction", "status": "draft"},
    {"id": "M4", "category": "regime", "name": "Regime Classification Predictiveness", "status": "draft"},
    {"id": "M5", "category": "regime", "name": "Regime Stability", "status": "draft"},
    # SESSION
    {"id": "C1", "category": "session", "name": "Session Effectiveness", "status": "draft"},
    {"id": "C2", "category": "session", "name": "Session-Regime Interaction", "status": "draft"},
    {"id": "C3", "category": "session", "name": "Session-Volatility Interaction", "status": "draft"},
    {"id": "C4", "category": "session", "name": "Session Execution Quality", "status": "draft"},
    # CONDITIONS
    {"id": "M6", "category": "conditions", "name": "Volatility Predicts Expectancy", "status": "draft"},
    {"id": "M7", "category": "conditions", "name": "Trend Strength Predicts Expectancy", "status": "draft"},
    {"id": "M8", "category": "conditions", "name": "HTF Alignment Improves Outcomes", "status": "draft"},
    {"id": "M9", "category": "conditions", "name": "Market Structure Predicts Quality", "status": "draft"},
    {"id": "M10", "category": "conditions", "name": "Optimal Condition Combinations", "status": "draft"},
]


class MarketDomain(ResearchDomain):
    @property
    def domain_id(self) -> str:
        return "market"

    @property
    def name(self) -> str:
        return "Market-Centric"

    @property
    def observation_type(self) -> str:
        return "market_state_at_trade"

    def build_population(self, universe_events: list[dict]) -> list[dict]:
        """
        Build market observations from universe events.

        Each trade's market context becomes a market observation
        linked to its trade outcome.
        """
        observations = []
        for e in universe_events:
            mkt = e.get("market", {})
            ex = e.get("execution", {})

            observations.append({
                "market_event_id": f"mkt_{ex.get('ticket', '')}",
                "trade_id": e.get("trade_id", ""),
                "symbol": ex.get("symbol", ""),
                "timestamp": ex.get("entry_time", 0),
                "session": mkt.get("session", ""),
                "regime": mkt.get("regime", ""),
                "volatility": mkt.get("volatility", ""),
                "trend_state": mkt.get("trend_state", ""),
                "higher_timeframe_bias": mkt.get("higher_timeframe_bias", ""),
                "h4_phase": mkt.get("h4_phase", ""),
                "h1_clarity": mkt.get("h1_clarity", 0),
                "trade_outcome_r": ex.get("r_multiple", 0),
                "trade_pnl": ex.get("net_realised_pnl", 0),
            })
        return observations

    def get_questions(self) -> list[dict[str, Any]]:
        return _QUESTIONS

    def coverage_report(self, universe_events: list[dict]) -> dict[str, Any]:
        total = len(universe_events)
        with_regime = sum(1 for e in universe_events if e.get("market", {}).get("regime"))
        with_session = sum(1 for e in universe_events if e.get("market", {}).get("session"))
        with_volatility = sum(1 for e in universe_events if e.get("market", {}).get("volatility"))

        gaps = []
        if with_volatility < total:
            gaps.append(f"{total - with_volatility} trades missing volatility classification")
        if with_regime < total:
            gaps.append(f"{total - with_regime} trades missing regime classification")

        complete = min(with_regime, with_session, with_volatility)
        return {
            "domain": self.domain_id,
            "total_observations": total,
            "complete": complete,
            "partial": total - complete,
            "missing": 0,
            "coverage_status": "AVAILABLE" if with_regime == total else "PARTIAL",
            "gaps": gaps,
        }

    def get_segmentation_filters(self) -> list[str]:
        return ["instrument", "session", "regime", "volatility"]
