"""
V10 Research Domains — Four specialised research lenses over the canonical Research Universe.

Domains:
    Trade     — "What happened to trades?"
    Decision  — "Was the decision-making correct?"
    Market    — "What conditions existed?"
    Strategy  — "Does the strategy logic make sense?"

The canonical Research Universe remains the single evidence source.
Domains are specialised lenses that filter, reshape, and ask domain-specific questions.
"""

from research_engine.v10.domains.base import ResearchDomain, DomainRegistry

__all__ = ["ResearchDomain", "DomainRegistry"]
