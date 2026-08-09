"""
V10 Research Governance & Statistical Confidence.

Ensures research conclusions are trustworthy, prioritised, and resistant
to false discoveries.

Usage:
    from research_engine.v10.research_governance import validate_finding, rank_findings

    finding = validate_finding(experiment_result)
    ranked = rank_findings(all_findings)
"""

from research_engine.v10.research_governance.confidence_engine import ConfidenceEngine
from research_engine.v10.research_governance.sample_validator import SampleValidator
from research_engine.v10.research_governance.finding_ranker import FindingRanker, rank_findings
from research_engine.v10.research_governance.models import ResearchFinding, validate_finding
from research_engine.v10.research_governance.evidence_maturity import assess_maturity, assess_decision, next_validation_step
from research_engine.v10.research_governance.progressive_validator import (
    FindingHistory, compare_baseline_candidate, evaluate_optimisation,
)

__all__ = [
    "ConfidenceEngine",
    "SampleValidator",
    "FindingRanker",
    "ResearchFinding",
    "validate_finding",
    "rank_findings",
    "assess_maturity",
    "assess_decision",
    "next_validation_step",
    "FindingHistory",
    "compare_baseline_candidate",
    "evaluate_optimisation",
]
