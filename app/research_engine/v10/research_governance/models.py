"""
Research Governance — Finding model and validation entry point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research_engine.v10.research_intelligence.models import ExperimentResult


@dataclass
class ResearchFinding:
    """A validated research finding with governance metadata."""
    finding_id: str = ""
    question_id: str = ""
    question_name: str = ""
    hypothesis: str = ""

    # Population
    population_filters: dict[str, str] = field(default_factory=dict)
    population_description: str = ""

    # Sample
    sample_size: int = 0
    sample_status: str = ""  # VALID, LIMITED, INSUFFICIENT

    # Result
    result_metric: str = ""
    result_value: float = 0.0
    result_data: dict[str, Any] = field(default_factory=dict)

    # Confidence
    confidence_level: str = "LOW"  # HIGH, MEDIUM, LOW
    confidence_score: float = 0.0
    confidence_factors: list[str] = field(default_factory=list)

    # Status
    status: str = "INCONCLUSIVE"  # SUPPORTED, REJECTED, INCONCLUSIVE
    recommendation: str = ""
    limitations: list[str] = field(default_factory=list)

    # Priority (set by ranker)
    priority: str = ""  # HIGH, MEDIUM, LOW
    priority_score: float = 0.0

    # Evidence maturity (progressive governance)
    evidence_maturity: str = ""  # EXPLORATORY, EARLY, DEVELOPING, STRONG, LONG_RUN
    decision_status: str = ""  # INVESTIGATE, PROMISING, CONTINUE_TESTING, SUPPORTED, REJECTED, EARLY_FAILURE
    decision_reason: str = ""
    next_step: str = ""

    # Governance metadata
    governance_warnings: list[str] = field(default_factory=list)
    comparisons_context: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "question_id": self.question_id,
            "question_name": self.question_name,
            "hypothesis": self.hypothesis,
            "population": {
                "filters": self.population_filters,
                "description": self.population_description,
            },
            "sample": {
                "size": self.sample_size,
                "status": self.sample_status,
            },
            "result": {
                "metric": self.result_metric,
                "value": self.result_value,
                "data": self.result_data,
            },
            "confidence": {
                "level": self.confidence_level,
                "score": self.confidence_score,
                "factors": self.confidence_factors,
            },
            "evidence": {
                "maturity": self.evidence_maturity,
            },
            "decision": {
                "status": self.decision_status,
                "reason": self.decision_reason,
            },
            "status": self.status,
            "recommendation": self.recommendation,
            "limitations": self.limitations,
            "validation": {
                "status": self.decision_status,
                "next_step": self.next_step,
            },
            "priority": self.priority,
            "priority_score": self.priority_score,
            "governance_warnings": self.governance_warnings,
            "comparisons_context": self.comparisons_context,
        }


def validate_finding(experiment_result: ExperimentResult) -> ResearchFinding:
    """
    Convert an ExperimentResult into a validated ResearchFinding.

    Applies:
        - Sample validation
        - Confidence scoring
        - Evidence maturity assessment
        - Decision readiness evaluation
        - Next validation step generation
    """
    from research_engine.v10.research_governance.sample_validator import SampleValidator
    from research_engine.v10.research_governance.confidence_engine import ConfidenceEngine
    from research_engine.v10.research_governance.evidence_maturity import (
        assess_maturity, assess_decision, next_validation_step, estimate_consistency,
    )

    sv = SampleValidator()
    ce = ConfidenceEngine()

    # Sample validation
    sample_result = sv.validate(experiment_result.sample_size)

    # Extract key metric
    result_data = experiment_result.result or {}
    expectancy = result_data.get("expectancy_r", result_data.get("expectancy", 0))
    win_rate = result_data.get("win_rate", 0)
    primary_metric = expectancy if expectancy else win_rate
    metric_name = "expectancy_r" if expectancy else "win_rate"

    # Confidence
    conf = ce.assess(
        sample_size=experiment_result.sample_size,
        effect_size=abs(primary_metric) if primary_metric else 0,
        recommendation=experiment_result.recommendation,
        limitations=experiment_result.limitations,
    )

    # Evidence maturity (new)
    consistency = estimate_consistency(result_data)
    maturity = assess_maturity(
        experiment_result.sample_size,
        abs(primary_metric) if primary_metric else 0,
        consistency,
    )

    # Decision readiness (new)
    is_deterioration = primary_metric < -0.1 if primary_metric else False
    decision_result = assess_decision(
        sample_size=experiment_result.sample_size,
        effect_size=primary_metric or 0,
        confidence_score=conf["score"],
        maturity=maturity,
        is_deterioration=is_deterioration,
    )

    # Next step (new)
    next_step = next_validation_step(
        decision_result["status"], maturity, experiment_result.sample_size
    )

    # Status: use decision status as primary, override legacy logic
    status = decision_result["status"]

    return ResearchFinding(
        finding_id=f"{experiment_result.question_id}_{_filters_hash(experiment_result.filters_applied)}",
        question_id=experiment_result.question_id,
        question_name=experiment_result.question_name,
        hypothesis=f"Does {experiment_result.question_name} hold?",
        population_filters=experiment_result.filters_applied,
        population_description=experiment_result.segment_population,
        sample_size=experiment_result.sample_size,
        sample_status=sample_result["status"],
        result_metric=metric_name,
        result_value=round(primary_metric, 4) if primary_metric else 0,
        result_data=result_data,
        confidence_level=conf["confidence"],
        confidence_score=conf["score"],
        confidence_factors=conf["factors"],
        status=status,
        recommendation=experiment_result.recommendation,
        limitations=experiment_result.limitations,
        evidence_maturity=maturity,
        decision_status=decision_result["status"],
        decision_reason=decision_result["reason"],
        next_step=next_step,
    )


def _filters_hash(filters: dict[str, str]) -> str:
    """Create short hash from filters for finding_id."""
    if not filters:
        return "FULL"
    return "_".join(f"{k}_{v}" for k, v in sorted(filters.items()))
