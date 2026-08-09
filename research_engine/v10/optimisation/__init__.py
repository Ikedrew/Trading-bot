"""
V10 Optimisation Bridge.

Controlled bridge between research findings and testable optimisation candidates.
Does NOT automatically change bot parameters or deploy changes.

Usage:
    from research_engine.v10.optimisation import HypothesisEngine, OptimisationRegistry

    engine = HypothesisEngine()
    hypothesis = engine.from_finding(finding)

    registry = OptimisationRegistry()
    registry.add_hypothesis(hypothesis)
"""

from research_engine.v10.optimisation.models import (
    ResearchHypothesis, OptimisationCandidate, ValidationPlan, ChangeRisk,
)
from research_engine.v10.optimisation.hypothesis_engine import HypothesisEngine
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry

__all__ = [
    "ResearchHypothesis",
    "OptimisationCandidate",
    "ValidationPlan",
    "ChangeRisk",
    "HypothesisEngine",
    "OptimisationRegistry",
]
