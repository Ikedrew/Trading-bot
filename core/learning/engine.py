"""
Learning Engine — analyses completed decisions for calibration quality.

Pipeline:
    Historical Decision (from ledger)
        ↓
    Reasoning (what we believed)
        ↓
    Evidence Attribution (what supported it)
        ↓
    Uncertainty (how sure we were)
        ↓
    Actual Outcome (what happened)
        ↓
    LearningRecord (was the belief calibrated?)

This engine does NOT:
    - Adjust weights
    - Modify thresholds
    - Change trading behaviour
    - Reinforce outcomes blindly

It ONLY produces observations about decision quality.

Usage:
    from core.learning import analyse_decision

    record = analyse_decision(
        decision_record=ledger_entry,
        outcome_record=trade_result,
    )
"""

from __future__ import annotations

from typing import Any

from core.learning.model import LearningRecord


# ─── CALIBRATION THRESHOLDS (observation boundaries, NOT trading thresholds) ──
# These determine how we CLASSIFY calibration quality for learning.
# They have ZERO effect on trading decisions.

_HIGH_EVIDENCE_QUALITY = 0.65      # Above this = strong evidence
_LOW_EVIDENCE_QUALITY = 0.35       # Below this = weak evidence
_HIGH_UNCERTAINTY = 0.50           # Above this = highly uncertain
_LOW_UNCERTAINTY = 0.20            # Below this = quite certain


def analyse_decision(
    *,
    decision_record: dict[str, Any],
    outcome_record: dict[str, Any],
) -> LearningRecord:
    """
    Analyse a completed decision against its actual outcome.

    Produces a LearningRecord that describes calibration quality.
    Never raises — returns degraded record on error.
    Never modifies any upstream state.

    Args:
        decision_record: Historical ledger entry (from decision_ledger)
        outcome_record: Trade result or market outcome
            Expected keys: "outcome" ("WIN"|"LOSS"|"BREAKEVEN"|"MISSED"|"BLOCKED"),
                          "pnl" (optional float), "hit_tp" (optional bool),
                          "hit_sl" (optional bool), "max_adverse" (optional float)

    Returns:
        LearningRecord (frozen observation)
    """
    try:
        return _build_learning_record(decision_record, outcome_record)
    except Exception:
        return LearningRecord(
            decision_id=decision_record.get("correlation_id", "unknown"),
            thesis="Analysis failed",
            evidence_quality=0.0,
            uncertainty_score=1.0,
            outcome=outcome_record.get("outcome", "UNKNOWN"),
            calibration_result="UNKNOWN",
            insights=("Learning analysis failed",),
            metadata={"error": True},
        )


def _build_learning_record(
    decision_record: dict[str, Any],
    outcome_record: dict[str, Any],
) -> LearningRecord:
    """Internal analysis — may raise."""

    # ─── EXTRACT DECISION STATE ───────────────────────────────────────
    decision_id = decision_record.get("correlation_id", "") or decision_record.get("context_snapshot_id", "")
    reasoning = decision_record.get("reasoning") or {}
    uncertainty = decision_record.get("uncertainty") or {}
    attribution = decision_record.get("score_attribution") or {}

    thesis = reasoning.get("primary_thesis", "No thesis recorded")
    supporting = reasoning.get("supporting_evidence", [])
    contradicting = reasoning.get("contradicting_evidence", [])

    uncertainty_score = uncertainty.get("uncertainty_score", 0.5)
    total_score = attribution.get("total_score", decision_record.get("signal_score", 0.0))

    # ─── COMPUTE EVIDENCE QUALITY ─────────────────────────────────────
    # Ratio of supporting vs total evidence (support strength)
    n_support = len(supporting)
    n_contradict = len(contradicting)
    n_total = n_support + n_contradict
    if n_total > 0:
        evidence_quality = n_support / n_total
    else:
        evidence_quality = 0.5  # No evidence either way

    # ─── EXTRACT OUTCOME ──────────────────────────────────────────────
    outcome = outcome_record.get("outcome", "UNKNOWN")

    # ─── ASSESS CALIBRATION ───────────────────────────────────────────
    calibration = _assess_calibration(
        evidence_quality=evidence_quality,
        uncertainty_score=uncertainty_score,
        outcome=outcome,
    )

    # ─── GENERATE INSIGHTS ────────────────────────────────────────────
    insights = _generate_insights(
        reasoning=reasoning,
        uncertainty=uncertainty,
        attribution=attribution,
        outcome=outcome,
        calibration=calibration,
        evidence_quality=evidence_quality,
    )

    return LearningRecord(
        decision_id=decision_id,
        thesis=thesis,
        evidence_quality=round(evidence_quality, 4),
        uncertainty_score=round(uncertainty_score, 4),
        outcome=outcome,
        calibration_result=calibration,
        insights=tuple(insights),
        metadata={
            "symbol": decision_record.get("symbol", ""),
            "cycle_id": decision_record.get("cycle_id", 0),
            "decision": decision_record.get("decision", ""),
            "signal_score": total_score,
            "n_supporting": n_support,
            "n_contradicting": n_contradict,
        },
    )


def _assess_calibration(
    *,
    evidence_quality: float,
    uncertainty_score: float,
    outcome: str,
) -> str:
    """
    Classify calibration quality based on belief vs outcome.

    Categories:
        CALIBRATED — confidence level matched outcome probability
        OVERCONFIDENT — high confidence but outcome failed
        UNDERCONFIDENT — low confidence but outcome succeeded
        UNCERTAIN_CORRECT — high uncertainty, outcome was indeed unpredictable
        UNCERTAIN_WRONG — high uncertainty, but outcome was actually clear
    """
    is_win = outcome in ("WIN", "BREAKEVEN")
    is_loss = outcome == "LOSS"
    was_confident = evidence_quality >= _HIGH_EVIDENCE_QUALITY and uncertainty_score <= _LOW_UNCERTAINTY
    was_uncertain = uncertainty_score >= _HIGH_UNCERTAINTY
    was_weak_evidence = evidence_quality <= _LOW_EVIDENCE_QUALITY

    if was_confident and is_win:
        return "CALIBRATED"
    elif was_confident and is_loss:
        return "OVERCONFIDENT"
    elif was_weak_evidence and is_win:
        return "UNDERCONFIDENT"
    elif was_uncertain and is_loss:
        return "UNCERTAIN_CORRECT"
    elif was_uncertain and is_win:
        return "UNCERTAIN_WRONG"
    elif is_win:
        return "CALIBRATED"
    elif is_loss and evidence_quality >= 0.5:
        return "OVERCONFIDENT"
    elif is_loss:
        return "CALIBRATED"  # Low evidence + loss = expected

    return "NEUTRAL"


def _generate_insights(
    *,
    reasoning: dict[str, Any],
    uncertainty: dict[str, Any],
    attribution: dict[str, Any],
    outcome: str,
    calibration: str,
    evidence_quality: float,
) -> list[str]:
    """
    Generate human-readable learning observations.

    These explain WHAT we can learn from this decision, not WHAT TO DO.
    """
    insights: list[str] = []

    # Calibration insight
    if calibration == "OVERCONFIDENT":
        insights.append("Decision was overconfident — evidence appeared strong but outcome failed")
    elif calibration == "UNDERCONFIDENT":
        insights.append("Decision was underconfident — weak evidence but outcome succeeded (possible luck)")
    elif calibration == "UNCERTAIN_CORRECT":
        insights.append("Uncertainty was justified — high ambiguity correlated with adverse outcome")
    elif calibration == "UNCERTAIN_WRONG":
        insights.append("Uncertainty was excessive — outcome was clearer than expected")
    elif calibration == "CALIBRATED":
        insights.append("Belief system was calibrated for this decision")

    # Evidence vs outcome
    contradictions = reasoning.get("contradicting_evidence", [])
    if contradictions and outcome == "LOSS":
        insights.append(f"Contradicting evidence ({len(contradictions)} factors) predicted the failure")
    elif contradictions and outcome == "WIN":
        insights.append(f"Trade succeeded despite {len(contradictions)} contradicting factors")

    # Uncertainty observation
    u_score = uncertainty.get("uncertainty_score", 0.5)
    if u_score >= _HIGH_UNCERTAINTY and outcome == "LOSS":
        insights.append("High uncertainty correctly signalled poor conditions")
    elif u_score <= _LOW_UNCERTAINTY and outcome == "WIN":
        insights.append("Low uncertainty correctly predicted favourable conditions")

    # Alternative thesis
    alt = reasoning.get("alternative_thesis")
    if alt and outcome == "LOSS":
        insights.append(f"Alternative thesis may have been correct: {alt}")

    # Top contributor observation
    contributions = attribution.get("contributions", [])
    if contributions and outcome == "WIN":
        top = contributions[0]
        insights.append(f"Top contributor ({top.get('name', '?')}) aligned with winning outcome")

    return insights
