"""
Observer — Central query interface for the System Intelligence Layer.

Provides structured answers to questions about the trading system by
reading existing persistence, configuration, and runtime state.

Usage:
    from system_intelligence import Observer

    obs = Observer()
    obs.state()           # Is the bot running? What config is active?
    obs.health()          # Are datasets healthy?
    obs.explain("EURUSD") # Why did the latest decision happen?
    obs.config()          # What configuration is active?
    obs.trades()          # Recent trade outcomes
    obs.guards()          # Which guards are blocking trades?
"""

from __future__ import annotations

from typing import Any

from system_intelligence.state import get_runtime_state
from system_intelligence.config_reader import get_config_snapshot
from system_intelligence.health import get_dataset_health
from system_intelligence.explain import explain_latest_decision, explain_trade
from system_intelligence.trades import get_recent_trades, get_trade_summary
from system_intelligence.guards import get_guard_statistics
from system_intelligence.domains import route_question, DOMAINS


class Observer:
    """
    Read-only system intelligence interface.

    Every method returns a structured dict. Never modifies system state.
    Degrades gracefully — returns partial answers rather than raising.
    """

    def state(self) -> dict[str, Any]:
        """Is the bot running? What environment? What status?"""
        return get_runtime_state()

    def config(self) -> dict[str, Any]:
        """What configuration is currently active?"""
        return get_config_snapshot()

    def health(self) -> dict[str, Any]:
        """Are all 24 datasets receiving records? Freshness check."""
        return get_dataset_health()

    def explain(self, symbol: str) -> dict[str, Any]:
        """Why did the latest decision for this symbol happen?"""
        return explain_latest_decision(symbol)

    def explain_by_trade(self, trade_id: str) -> dict[str, Any]:
        """Why did this specific trade win/lose?"""
        return explain_trade(trade_id)

    def trades(self, days: int = 7) -> dict[str, Any]:
        """Recent trade outcomes and summary statistics."""
        return get_trade_summary(days=days)

    def guards(self) -> dict[str, Any]:
        """Which guards are blocking the most trades?"""
        return get_guard_statistics()

    def route(self, question: str) -> dict[str, Any]:
        """Route a question to the correct evidence domain(s)."""
        matches = route_question(question)
        if not matches:
            return {
                "question": question,
                "routed": False,
                "suggestion": "Try asking about: decisions, risk, execution, config, health, patterns, research, trades.",
            }
        result = {
            "question": question,
            "routed": True,
            "domains": [],
        }
        for name, domain, score in matches:
            result["domains"].append({
                "domain": domain.name,
                "relevance": round(score, 1),
                "description": domain.description,
                "evidence_sources": domain.evidence_sources,
                "authority_files": domain.authority_files,
                "answers": domain.answers,
            })
        return result

    def domains_list(self) -> dict[str, str]:
        """List all architecture domains the Observer understands."""
        return {name: d.description for name, d in DOMAINS.items()}
