"""
Cross-Universe Interface.

Provides the trace → compare → classify → propose pipeline for
analysing relationships across the six analytical universes.

Components:
    - tracer: Retrieves lifecycle observations across universes by entity_id
    - comparison: Structures cross-universe observations for analysis
    - classifier: Assigns deterministic structural classifications
    - proposal: Produces governed research follow-up proposals
    - persistence: Persists lifecycle traces as immutable research artifacts
"""

from research_engine.v10.cross_universe.tracer import CrossUniverseTracer
from research_engine.v10.cross_universe.comparison import CrossUniverseComparison
from research_engine.v10.cross_universe.classifier import CrossUniverseClassifier
from research_engine.v10.cross_universe.proposal import ResearchProposal, ProposalGenerator
from research_engine.v10.cross_universe.persistence import LifecycleTraceStore

__all__ = [
    "CrossUniverseTracer",
    "CrossUniverseComparison",
    "CrossUniverseClassifier",
    "ResearchProposal",
    "ProposalGenerator",
    "LifecycleTraceStore",
]
