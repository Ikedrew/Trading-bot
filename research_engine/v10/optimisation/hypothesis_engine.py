"""
Optimisation Bridge — Hypothesis Engine.

Converts research findings into structured, testable hypotheses.
Does NOT invent unsupported changes.
"""

from __future__ import annotations

from typing import Any

from research_engine.v10.optimisation.models import ResearchHypothesis
from research_engine.v10.research_governance.models import ResearchFinding


# Component mapping: question → likely target component
_COMPONENT_MAP = {
    "R1": "RiskManager",
    "R2": "StopPlacement",
    "R3": "TargetPlacement",
    "R4": "RiskManager",
    "R5": "RiskManager",
    "D1": "DecisionScoring",
    "D2": "EVCalculation",
    "D3": "DecisionThreshold",
    "E1": "SystemOverall",
    "E2": "PatternDetection",
    "M1": "RegimeFilter",
    "C1": "SessionFilter",
    "S1": "StrategySelection",
    "OQ1": "OpportunityScoring",
    "OQ2": "OpportunityScoring",
}


class HypothesisEngine:
    """
    Converts validated research findings into testable hypotheses.

    Rules:
        - Only creates hypotheses from findings with sufficient evidence
        - Does NOT invent specific parameter values
        - Preserves the source question and evidence link
        - Labels the target component for investigation
    """

    def from_finding(self, finding: ResearchFinding) -> ResearchHypothesis | None:
        """
        Generate a hypothesis from a research finding.

        Returns None if the finding doesn't support hypothesis generation
        (e.g., INCONCLUSIVE with no directional signal).
        """
        # Only generate from findings with actionable signal
        if finding.decision_status in ("INVESTIGATE",) and finding.confidence_level == "LOW":
            return None  # Not enough signal

        statement = self._generate_statement(finding)
        target = _COMPONENT_MAP.get(finding.question_id, "Unknown")
        expected = self._expected_effect(finding)

        return ResearchHypothesis(
            hypothesis_id=f"HYP_{finding.question_id}_{finding.finding_id[-8:] if len(finding.finding_id) > 8 else finding.finding_id}",
            source_finding=finding.finding_id,
            source_question=finding.question_id,
            domain=finding.question_id[0] if finding.question_id else "",
            statement=statement,
            target_component=target,
            expected_effect=expected,
            confidence=finding.confidence_level,
            evidence_strength=finding.evidence_maturity,
            status="PROPOSED",
        )

    def from_campaign_findings(self, findings: list[Any]) -> list[ResearchHypothesis]:
        """Generate hypotheses from a list of campaign findings."""
        hypotheses = []
        for f in findings:
            # Accept both ResearchFinding and CampaignFinding-like dicts
            if hasattr(f, "question_id"):
                finding = self._to_research_finding(f)
            else:
                continue
            hyp = self.from_finding(finding)
            if hyp:
                hypotheses.append(hyp)
        return hypotheses

    def _generate_statement(self, finding: ResearchFinding) -> str:
        """Generate a hypothesis statement from the finding evidence."""
        qid = finding.question_id
        value = finding.result_value
        name = finding.question_name

        if finding.decision_status == "EARLY_FAILURE":
            return f"{name} shows significant underperformance. Investigation required."
        elif finding.decision_status == "REJECTED":
            return f"{name} hypothesis is not supported. Current approach may be suboptimal."
        elif finding.decision_status in ("PROMISING", "CONTINUE_TESTING"):
            return f"{name} shows positive direction ({value:+.3f}). Continue validation."
        elif finding.decision_status == "SUPPORTED":
            return f"{name} is confirmed. Consider implementation of improvements."
        else:
            return f"{name} requires further investigation (effect: {value:+.3f})."

    def _expected_effect(self, finding: ResearchFinding) -> str:
        """Describe expected effect without inventing specific values."""
        if finding.result_value > 0:
            return "Maintain or extend positive effect"
        elif finding.result_value < -0.1:
            return "Reduce negative impact on expectancy"
        return "Clarify effect direction"

    def _to_research_finding(self, obj: Any) -> ResearchFinding:
        """Convert campaign finding or similar object to ResearchFinding."""
        return ResearchFinding(
            finding_id=getattr(obj, "finding_id", "") or f"{getattr(obj, 'question_id', '')}_camp",
            question_id=getattr(obj, "question_id", ""),
            question_name=getattr(obj, "question_name", ""),
            sample_size=getattr(obj, "sample_size", 0),
            result_value=getattr(obj, "result_value", 0),
            confidence_level=getattr(obj, "confidence", "LOW"),
            evidence_maturity=getattr(obj, "evidence_maturity", ""),
            decision_status=getattr(obj, "decision_status", ""),
            recommendation=getattr(obj, "recommendation", ""),
            limitations=getattr(obj, "limitations", []),
        )
