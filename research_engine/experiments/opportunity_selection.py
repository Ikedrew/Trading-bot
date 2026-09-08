"""
OPP-1 — Opportunity Selection Quality.

Question:
    Were the opportunities promoted into the decision pipeline better
    than the opportunities rejected or filtered out?

This is an OPPORTUNITY-LEVEL question, distinct from portfolio-level
selection (PORT-1). It evaluates the quality of the opportunity-intake
and assessment pipeline, not the cross-symbol ranker.

Primary evidence:
    - opportunities (what appeared)
    - assessments (how good was each)
    - horizon_candidates (promoted/rejected status)
    - shadow_runtime (outcomes, where available)

This module is PURELY RESEARCH. It does NOT modify trading logic.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from research_engine.data_access.loaders import (
    load_opportunities,
    load_assessments,
    load_horizon_candidates,
    load_strategy_candidates,
)
from research_engine.data_access.shadow_runtime_ingestion import (
    ingest_completed_shadow_trades,
)
from research_engine.experiments.experiment_base import (
    ReadinessStatus,
    compute_confidence,
)
from research_engine.experiments.selection_analysis import (
    build_selection_report,
    group_stats,
    index_by,
    load_quarantine_boundary,
    numeric,
    shadow_outcome_for_opportunity,
)

_MIN_OPPORTUNITIES = 10       # minimum opportunities to analyse
_MIN_OUTCOME_OPPS = 5         # minimum with outcomes

# Selection status values on horizon_candidates
_PROMOTED_STATUSES = frozenset({"SELECTED", "PROMOTED", "EXECUTED"})
_REJECTED_STATUSES = frozenset({"REJECTED", "INELIGIBLE", "NOT_APPLICABLE"})


def run_opp_1(
    opportunities: list[dict[str, Any]] | None = None,
    assessments: list[dict[str, Any]] | None = None,
    horizon_candidates: list[dict[str, Any]] | None = None,
    shadow_trades: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    OPP-1: Opportunity selection quality.

    Determines whether opportunities promoted into the decision pipeline
    (those with a SELECTED horizon candidate) outperform opportunities
    that were rejected or filtered out.

    This is an OPPORTUNITY-LEVEL question using the opportunity intake
    and assessment layers, NOT portfolio/ranking data.
    """
    if opportunities is None:
        opportunities = load_opportunities()
    if assessments is None:
        assessments = load_assessments()
    if horizon_candidates is None:
        horizon_candidates = load_horizon_candidates()
    if shadow_trades is None:
        shadow_trades = ingest_completed_shadow_trades()

    boundary = load_quarantine_boundary()
    total_opps = len(opportunities)

    if total_opps < _MIN_OPPORTUNITIES:
        return build_selection_report(
            question_id="OPP-1", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={"reason": f"Only {total_opps} opportunities available (need {_MIN_OPPORTUNITIES})"},
            confidence="INSUFFICIENT_DATA",
            dataset={"opportunities_read": total_opps, "assessments_read": len(assessments)},
            recommendation="WAIT", source="opportunities",
            records_used=total_opps, records_excluded=0,
            warnings=["Insufficient opportunity data"],
            module="research_engine.experiments.opportunity_selection",
        )

    # Index assessments by opportunity_id
    assessment_by_opp = index_by(assessments, lambda r: str(r.get("opportunity_id", "")) or "")

    # Index horizon_candidates by canonical_opportunity_id + determine promotion
    promoted_opps: set[str] = set()
    rejected_opps: set[str] = set()
    candidate_conversion: dict[str, int] = {}  # opportunity_id -> candidate count

    for hc in horizon_candidates:
        opp = str(hc.get("canonical_opportunity_id", "") or "")
        if not opp:
            continue
        status = str(hc.get("selection_status", "") or "")
        candidate_conversion[opp] = candidate_conversion.get(opp, 0) + 1
        if status in _PROMOTED_STATUSES:
            promoted_opps.add(opp)
        elif status in _REJECTED_STATUSES:
            rejected_opps.add(opp)

    # Shadow outcomes by canonical_opportunity_id
    outcome_by_opp: dict[str, float] = {}
    for s in shadow_trades:
        opp = str(s.get("canonical_opportunity_id", "") or "")
        if not opp:
            continue
        if opp in outcome_by_opp:
            continue
        r = numeric(s, "simulated_outcome", "pnl_r_multiple") or numeric(s, "pnl_r")
        if r is not None:
            outcome_by_opp[opp] = r

    # Classify each opportunity as promoted or rejected based on its
    # horizon_candidates status. Use the opportunity's canonical ID
    # to join to horizon_candidates.
    promoted_rs: list[float] = []
    rejected_rs: list[float] = []
    assessment_score_buckets: dict[str, list[float]] = {}
    promoted_with_assessment = 0
    rejected_with_assessment = 0
    all_opportunities_classified = 0
    opportunities_with_outcome = 0

    for opp in opportunities:
        opp_id = str(opp.get("opportunity_id", "") or "")
        canonical_id = str(opp.get("canonical_opportunity_id", "") or opp.get("opportunity_id", "") or "")
        if not opp_id and not canonical_id:
            continue

        # Determine if promoted or rejected
        cid = canonical_id
        is_promoted = cid in promoted_opps
        is_rejected = cid in rejected_opps

        if not is_promoted and not is_rejected:
            continue

        all_opportunities_classified += 1
        r = outcome_by_opp.get(cid)
        if r is not None:
            opportunities_with_outcome += 1

        # Assessment evidence
        assessment_list = assessment_by_opp.get(opp_id) or []
        assessment = assessment_list[0] if assessment_list else None
        if assessment:
            if is_promoted:
                promoted_with_assessment += 1
            else:
                rejected_with_assessment += 1

            # Bucket by score_strategy
            score = numeric(assessment, "score_strategy")
            if score is not None:
                bucket = _score_bucket(score)
                assessment_score_buckets.setdefault(bucket, []).append(r if r is not None else 0.0)

        # Outcome comparison
        if r is not None:
            if is_promoted:
                promoted_rs.append(r)
            else:
                rejected_rs.append(r)

    if opportunities_with_outcome < _MIN_OUTCOME_OPPS:
        return build_selection_report(
            question_id="OPP-1", status=ReadinessStatus.INSUFFICIENT_DATA,
            overall={
                "reason": f"Only {opportunities_with_outcome} opportunities with outcomes (need {_MIN_OUTCOME_OPPS})",
                "classified": all_opportunities_classified,
                "promoted": len(promoted_opps),
                "rejected": len(rejected_opps),
            },
            confidence="INSUFFICIENT_DATA",
            dataset={
                "opportunities_read": total_opps,
                "classified": all_opportunities_classified,
                "with_outcomes": opportunities_with_outcome,
            },
            recommendation="WAIT", source="opportunities",
            records_used=total_opps, records_excluded=0,
            warnings=["Insufficient outcome-bearing opportunity classifications"],
            module="research_engine.experiments.opportunity_selection",
        )

    # Conversion rate
    n_opps_with_candidates = len(candidate_conversion)
    opp_to_candidate_rate = n_opps_with_candidates / max(total_opps, 1)

    promoted_count = len([o for o in opportunities
                          if str(o.get("canonical_opportunity_id", "") or o.get("opportunity_id", "") or "")
                          in promoted_opps])
    rejected_count = len([o for o in opportunities
                          if str(o.get("canonical_opportunity_id", "") or o.get("opportunity_id", "") or "")
                          in rejected_opps])

    overall = {
        "opportunities_read": total_opps,
        "assessments_read": len(assessments),
        "horizon_candidates_read": len(horizon_candidates),
        "classified_opportunities": all_opportunities_classified,
        "promoted_opportunities": promoted_count,
        "rejected_opportunities": rejected_count,
        "opportunities_with_outcome": opportunities_with_outcome,
        "opp_to_candidate_rate": round(opp_to_candidate_rate, 4),
        "promoted_outcome": group_stats(promoted_rs),
        "rejected_outcome": group_stats(rejected_rs),
        "candidate_conversion_rate": round(
            n_opps_with_candidates / max(total_opps, 1), 4,
        ),
        "promoted_vs_rejected_delta": _delta_stats(promoted_rs, rejected_rs),
        "score_bucket_outcomes": {
            bucket: group_stats(rs)
            for bucket, rs in sorted(assessment_score_buckets.items())
        },
        "quarantine_boundary": boundary.describe(),
    }

    promoted_mean = group_stats(promoted_rs).get("mean_r", 0) or 0
    rejected_mean = group_stats(rejected_rs).get("mean_r", 0) or 0
    n_comparable = min(len(promoted_rs), len(rejected_rs))
    confidence = compute_confidence(n_comparable, promoted_mean > rejected_mean)

    if confidence == "INSUFFICIENT_DATA":
        recommendation = "WAIT"
    elif promoted_mean > rejected_mean and confidence in ("HIGH", "MEDIUM"):
        recommendation = "PROMOTE"
        finding = (
            f"Promoted opportunities ({promoted_mean:+.3f}R) outperform rejected "
            f"({rejected_mean:+.3f}R). Opp-to-candidate conversion: {opp_to_candidate_rate:.0%}."
        )
    elif promoted_mean > rejected_mean:
        recommendation = "MONITOR"
        finding = (
            f"Promoted opportunities ({promoted_mean:+.3f}R) marginally outperform rejected "
            f"({rejected_mean:+.3f}R) - low confidence."
        )
    else:
        recommendation = "REJECT"
        finding = (
            f"Promoted opportunities ({promoted_mean:+.3f}R) do NOT outperform rejected "
            f"({rejected_mean:+.3f}R) - pipeline may be filtering incorrectly."
        )

    report = build_selection_report(
        question_id="OPP-1", status=ReadinessStatus.COMPLETE,
        overall=overall, confidence=confidence,
        dataset={
            "opportunities_read": total_opps,
            "assessments_read": len(assessments),
            "horizon_candidates_read": len(horizon_candidates),
            "classified": all_opportunities_classified,
            "promoted_with_outcome": len(promoted_rs),
            "rejected_with_outcome": len(rejected_rs),
            "quarantine_boundary": boundary.describe(),
        },
        recommendation=recommendation, source="opportunities",
        records_used=total_opps,
        records_excluded=total_opps - all_opportunities_classified,
        assumptions=[
            "Primary evidence = opportunities + assessments + horizon_candidates (not portfolio_rankings)",
            "Opportunity classified as 'promoted' when a SELECTED horizon_candidate exists",
            "Shadow outcome is the PRIMARY_HORIZON simulation for that opportunity",
            "Assessment-score buckets use score_strategy from the assessment record",
            "This is an OPPORTUNITY-level question about the intake/assessment pipeline",
        ],
        module="research_engine.experiments.opportunity_selection",
    )

    return report


# ==============================================================================
# HELPERS
# ==============================================================================


def _score_bucket(score: float) -> str:
    """Categorise a score (0-1 range) into a named bucket."""
    if score >= 0.8:
        return "HIGH_0.80+"
    if score >= 0.6:
        return "MEDIUM_0.60-0.79"
    if score >= 0.4:
        return "LOW_0.40-0.59"
    return "POOR_<0.40"


def _delta_stats(
    promoted: list[float],
    rejected: list[float],
) -> dict[str, Any] | None:
    """Compute promoted-vs-rejected delta statistics."""
    if not promoted or not rejected:
        return None
    p_mean = statistics.mean(promoted)
    r_mean = statistics.mean(rejected)
    return {
        "promoted_mean_r": round(p_mean, 4),
        "rejected_mean_r": round(r_mean, 4),
        "mean_delta": round(p_mean - r_mean, 4),
        "n_promoted": len(promoted),
        "n_rejected": len(rejected),
    }