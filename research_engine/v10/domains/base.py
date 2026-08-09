"""
Research Domains — Base interface and registry.

Every domain implements a stable contract:
    - identity (domain_id, name)
    - observation type / grain
    - population builder (from canonical universe)
    - question listing
    - coverage reporting
    - segmentation filters
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ResearchDomain(ABC):
    """
    Abstract base class for research domains.

    Each domain is a specialised lens over the canonical Research Universe.
    """

    @property
    @abstractmethod
    def domain_id(self) -> str:
        """Short identifier (e.g., 'trade', 'decision', 'market', 'strategy')."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable domain name."""
        ...

    @property
    @abstractmethod
    def observation_type(self) -> str:
        """What 1 row represents (e.g., 'completed_trade', 'decision_event')."""
        ...

    @abstractmethod
    def build_population(self, universe_events: list[dict]) -> list[dict]:
        """
        Build the domain-specific population from canonical universe events.

        May reshape, filter, or expand the universe events into
        domain-specific observation rows.

        Returns:
            List of domain-specific observations.
        """
        ...

    @abstractmethod
    def get_questions(self) -> list[dict[str, Any]]:
        """
        Return registered research questions for this domain.

        Each question: {"id": str, "category": str, "name": str, "status": str}
        """
        ...

    @abstractmethod
    def coverage_report(self, universe_events: list[dict]) -> dict[str, Any]:
        """
        Report data coverage for this domain.

        Returns:
            {
                "domain": str,
                "total_observations": int,
                "complete": int,
                "partial": int,
                "missing": int,
                "coverage_status": "AVAILABLE" | "PARTIAL" | "MISSING",
                "gaps": [str],
            }
        """
        ...

    def get_segmentation_filters(self) -> list[str]:
        """Return supported segmentation filter names for this domain."""
        return []

    def metadata(self) -> dict[str, Any]:
        """Return domain metadata."""
        return {
            "domain_id": self.domain_id,
            "name": self.name,
            "observation_type": self.observation_type,
            "segmentation_filters": self.get_segmentation_filters(),
        }


class DomainRegistry:
    """Registry of all research domains."""

    def __init__(self):
        self._domains: dict[str, ResearchDomain] = {}

    def register(self, domain: ResearchDomain) -> None:
        self._domains[domain.domain_id] = domain

    def get(self, domain_id: str) -> ResearchDomain | None:
        return self._domains.get(domain_id)

    def all(self) -> list[ResearchDomain]:
        return list(self._domains.values())

    def resolve_question_domain(self, question_id: str) -> ResearchDomain | None:
        """Find which domain owns a question ID."""
        for domain in self._domains.values():
            for q in domain.get_questions():
                if q["id"] == question_id:
                    return domain
        return None


def get_default_registry() -> DomainRegistry:
    """Build the default domain registry with all four domains."""
    from research_engine.v10.domains.trade import TradeDomain
    from research_engine.v10.domains.decision import DecisionDomain
    from research_engine.v10.domains.market import MarketDomain
    from research_engine.v10.domains.strategy import StrategyDomain

    registry = DomainRegistry()
    registry.register(TradeDomain())
    registry.register(DecisionDomain())
    registry.register(MarketDomain())
    registry.register(StrategyDomain())
    return registry
