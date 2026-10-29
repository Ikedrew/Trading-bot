"""
Research Feedback Generator.

Deterministic rules that transform completed ResearchFindings into
governed ResearchFeedback artifacts.

Rules are explicit. No LLM, no subjective scoring, no random selection.
Every feedback classification has a traceable deterministic rule.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from research_engine.v10.feedback.model import (
    FeedbackType,
    ResearchFeedback,
    SystemArea,
)


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM AREA MAPPING
# ═══════════════════════════════════════════════════════════════════════════════

# Maps question ID prefix → system area
_PREFIX_TO_AREA: dict[str, str] = {
    "SD-": SystemArea.OUTCOME.value,  # Shadow questions → Outcome area (counterfactual)
    "E-": SystemArea.EXECUTION.value,
    "D-": SystemArea.DECISION.value,
    "M-": SystemArea.MARKET.value,
    "S-": SystemArea.STRATEGY.value,
    "R-": SystemArea.RISK.value,
    "O-": SystemArea.OUTCOME.value,
}

# Cross-universe prefixes
_CROSS_PREFIXES = ("ED", "EM", "ES", "DM", "DS", "MS", "EDM", "EDS", "DMS", "EDMS")


def _determine_system_area(question_id: str, universes_used: list[str]) -> str:
    """Determine system area from question ID and universes used."""
    qid = question_id.upper()

    # Cross-universe questions
    if any(qid.startswith(prefix) for prefix in _CROSS_PREFIXES):
        return SystemArea.CROSS_UNIVERSE.value

    # Single-universe questions by prefix
    for prefix, area in _PREFIX_TO_AREA.items():
        if qid.startswith(prefix):
            return area

    # Fallback: derive from universes_used
    if len(universes_used) == 1:
        u = universes_used[0].upper()
        if u in [a.value for a in SystemArea]:
            return u

    if len(universes_used) > 1:
        return SystemArea.CROSS_UNIVERSE.value

    return SystemArea.UNKNOWN.value


# ═══════════════════════════════════════════════════════════════════════════════
# FEEDBACK TYPE CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════


def _classify_feedback_type(outcome: str, confidence: str) -> str:
    """
    Deterministic classification of finding into feedback type.

    Rules:
        POSITIVE + HIGH/MEDIUM       → CONFIRMED_STRENGTH
        POSITIVE + LOW               → OPPORTUNITY (promising but uncertain)
        NEGATIVE + HIGH/MEDIUM       → IDENTIFIED_WEAKNESS
        NEGATIVE + LOW               → UNCERTAINTY
        INCONCLUSIVE + INSUFFICIENT  → DATA_GAP
        INCONCLUSIVE + any           → UNCERTAINTY
        PREDICTIVE + any             → CONFIRMED_STRENGTH (the feature is predictive)
        NOT_PREDICTIVE + any         → IDENTIFIED_WEAKNESS
        WELL_CALIBRATED + any        → CONFIRMED_STRENGTH
        POORLY_CALIBRATED + any      → IDENTIFIED_WEAKNESS
        COMPLETED + any              → NO_ACTION (neutral result)
        anything else                → NO_ACTION
    """
    outcome_upper = (outcome or "").upper()
    confidence_upper = (confidence or "").upper()

    # Positive outcomes
    if outcome_upper == "POSITIVE":
        if confidence_upper in ("HIGH", "MEDIUM"):
            return FeedbackType.CONFIRMED_STRENGTH.value
        elif confidence_upper == "LOW":
            return FeedbackType.OPPORTUNITY.value
        else:
            return FeedbackType.UNCERTAINTY.value

    # Negative outcomes
    if outcome_upper == "NEGATIVE":
        if confidence_upper in ("HIGH", "MEDIUM"):
            return FeedbackType.IDENTIFIED_WEAKNESS.value
        elif confidence_upper == "LOW":
            return FeedbackType.UNCERTAINTY.value
        else:
            return FeedbackType.DATA_GAP.value

    # Inconclusive
    if outcome_upper == "INCONCLUSIVE":
        if confidence_upper == "INSUFFICIENT":
            return FeedbackType.DATA_GAP.value
        return FeedbackType.UNCERTAINTY.value

    # Predictive power
    if outcome_upper == "PREDICTIVE":
        return FeedbackType.CONFIRMED_STRENGTH.value
    if outcome_upper == "NOT_PREDICTIVE":
        return FeedbackType.IDENTIFIED_WEAKNESS.value

    # Calibration
    if outcome_upper == "WELL_CALIBRATED":
        return FeedbackType.CONFIRMED_STRENGTH.value
    if outcome_upper == "POORLY_CALIBRATED":
        return FeedbackType.IDENTIFIED_WEAKNESS.value

    # Neutral/completed
    if outcome_upper == "COMPLETED":
        return FeedbackType.NO_ACTION.value

    # Analysis failed
    if outcome_upper == "ANALYSIS_FAILED":
        return FeedbackType.RESEARCH_GAP.value

    return FeedbackType.NO_ACTION.value


# ═══════════════════════════════════════════════════════════════════════════════
# PROPOSAL ELIGIBILITY
# ═══════════════════════════════════════════════════════════════════════════════


def _assess_proposal_eligibility(
    feedback_type: str, confidence: str, sample_sizes: dict[str, Any]
) -> tuple[bool, str]:
    """
    Determine whether a feedback item is eligible to become a proposal.

    Returns (eligible, blocked_reason).

    Eligible only when:
        - feedback_type is IDENTIFIED_WEAKNESS or OPPORTUNITY
        - confidence is HIGH or MEDIUM
        - analytical sample >= minimum required
    """
    # Only weakness/opportunity can generate proposals
    if feedback_type not in (FeedbackType.IDENTIFIED_WEAKNESS.value, FeedbackType.OPPORTUNITY.value):
        return False, "feedback_type does not warrant a proposal"

    # Confidence must be sufficient
    if confidence not in ("HIGH", "MEDIUM"):
        return False, f"confidence is {confidence} (requires HIGH or MEDIUM)"

    # Sample must meet minimum
    analytical = sample_sizes.get("analytical_sample", 0)
    minimum = sample_sizes.get("minimum_required", 20)
    if isinstance(analytical, int) and isinstance(minimum, int) and analytical < minimum:
        return False, f"analytical_sample ({analytical}) < minimum_required ({minimum})"

    return True, ""


# ═══════════════════════════════════════════════════════════════════════════════
# INTERPRETATION
# ═══════════════════════════════════════════════════════════════════════════════


def _generate_interpretation(
    feedback_type: str,
    question_id: str,
    title: str,
    outcome: str,
    conclusion: str,
    system_area: str,
) -> str:
    """Generate a deterministic human-readable interpretation."""
    if feedback_type == FeedbackType.CONFIRMED_STRENGTH.value:
        return f"{title}: evidence supports current {system_area.lower()} behaviour ({outcome.lower()})."
    elif feedback_type == FeedbackType.IDENTIFIED_WEAKNESS.value:
        return f"{title}: evidence identifies a weakness in {system_area.lower()} ({outcome.lower()})."
    elif feedback_type == FeedbackType.OPPORTUNITY.value:
        return f"{title}: preliminary evidence suggests an opportunity in {system_area.lower()}."
    elif feedback_type == FeedbackType.UNCERTAINTY.value:
        return f"{title}: insufficient certainty to classify {system_area.lower()} behaviour."
    elif feedback_type == FeedbackType.DATA_GAP.value:
        return f"{title}: insufficient data to evaluate {system_area.lower()}."
    elif feedback_type == FeedbackType.RESEARCH_GAP.value:
        return f"{title}: analysis could not complete — further research methodology required."
    return f"{title}: no actionable feedback."


# ═══════════════════════════════════════════════════════════════════════════════
# GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════


class FeedbackGenerator:
    """
    Generates governed research feedback from completed findings.

    Deterministic. Read-only. Never modifies the trading system.

    Usage:
        generator = FeedbackGenerator()
        feedback = generator.from_finding(finding_dict)
        feedback_batch = generator.from_run(findings_list)
    """

    def from_finding(self, finding: dict[str, Any]) -> ResearchFeedback:
        """
        Generate feedback from a single persisted finding (dict form).

        Args:
            finding: A finding dict (from latest.json or history).

        Returns:
            ResearchFeedback artifact.
        """
        question_id = finding.get("question_id", "")
        title = finding.get("title", "")
        run_id = finding.get("run_id", "")
        outcome = finding.get("outcome", "")
        confidence = finding.get("confidence", "")
        conclusion = finding.get("conclusion", "")
        universes_used = finding.get("universes_used", [])
        sample_sizes = finding.get("sample_sizes", {})
        primary_metrics = finding.get("primary_metrics", {})

        # Classify
        system_area = _determine_system_area(question_id, universes_used)
        feedback_type = _classify_feedback_type(outcome, confidence)

        # Proposal eligibility
        eligible, blocked_reason = _assess_proposal_eligibility(
            feedback_type, confidence, sample_sizes
        )

        # Interpretation
        interpretation = _generate_interpretation(
            feedback_type, question_id, title, outcome, conclusion, system_area
        )

        # Evidence summary
        analytical = sample_sizes.get("analytical_sample", "?")
        population = sample_sizes.get("population", "?")
        evidence_summary = (
            f"outcome={outcome}, confidence={confidence}, "
            f"population={population}, analytical_sample={analytical}"
        )

        # Feedback ID
        feedback_id = f"fb_{question_id}_{run_id}" if run_id else f"fb_{question_id}_{uuid.uuid4().hex[:8]}"

        return ResearchFeedback(
            feedback_id=feedback_id,
            source_finding_id=f"{question_id}_{run_id}",
            run_id=run_id,
            question_id=question_id,
            finding_outcome=outcome,
            finding_confidence=confidence,
            system_area=system_area,
            feedback_type=feedback_type,
            interpretation=interpretation,
            evidence_summary=evidence_summary,
            affected_component=system_area,
            hypothesis="",
            recommended_research=[],
            proposal_eligible=eligible,
            proposal_blocked_reason=blocked_reason,
            question_version=finding.get("question_version", ""),
            analysis_version=finding.get("analysis_version", ""),
            universe_versions=finding.get("universe_versions", {}),
            population_versions=finding.get("population_versions", {}),
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def from_run(self, findings: list[dict[str, Any]]) -> list[ResearchFeedback]:
        """Generate feedback for all findings from a research run."""
        return [self.from_finding(f) for f in findings]

    def summary(self, feedbacks: list[ResearchFeedback]) -> dict[str, Any]:
        """Produce a summary of feedback items by type."""
        by_type: dict[str, int] = {}
        by_area: dict[str, int] = {}
        proposal_eligible_count = 0

        for fb in feedbacks:
            by_type[fb.feedback_type] = by_type.get(fb.feedback_type, 0) + 1
            by_area[fb.system_area] = by_area.get(fb.system_area, 0) + 1
            if fb.proposal_eligible:
                proposal_eligible_count += 1

        return {
            "total": len(feedbacks),
            "by_type": by_type,
            "by_area": by_area,
            "proposal_eligible": proposal_eligible_count,
            "strengths": by_type.get(FeedbackType.CONFIRMED_STRENGTH.value, 0),
            "weaknesses": by_type.get(FeedbackType.IDENTIFIED_WEAKNESS.value, 0),
            "opportunities": by_type.get(FeedbackType.OPPORTUNITY.value, 0),
            "uncertainties": by_type.get(FeedbackType.UNCERTAINTY.value, 0),
            "data_gaps": by_type.get(FeedbackType.DATA_GAP.value, 0),
        }
