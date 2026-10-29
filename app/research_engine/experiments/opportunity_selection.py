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
from collections import defaultdict
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
# Reuse the canonical CURRENT shadow outcome pairing (one observation per
# canonical_opportunity_id; account fanout / repeated horizons collapse; missing
# outcomes excluded; conflicts fail closed) established in RW2/RW3/D5.
from research_engine.experiments.market_prediction_rw2 import (
    build_opportunity_observations,
)

_MIN_OPPORTUNITIES = 10       # minimum opportunities to analyse
_MIN_OUTCOME_OPPS = 5         # minimum with outcomes

# Selection status values on horizon_candidates
_PROMOTED_STATUSES = frozenset({"SELECTED", "PROMOTED", "EXECUTED"})
_REJECTED_STATUSES = frozenset({"REJECTED", "INELIGIBLE", "NOT_APPLICABLE"})

# OPP-1 canonical contract (promoted-vs-rejected opportunity expectancy).
OPP1_REPORT_FILENAME = "opp1_opportunity_selection.json"
_OPP1_MIN_TOTAL = 100         # valid paired canonical opportunities
_OPP1_MIN_DISCOVERY = 60
_OPP1_MIN_VALIDATION = 40
_OPP1_MIN_GROUP = 15          # per promoted/rejected group for a directional claim


def classify_opportunity_membership(
    horizon_candidates: list[dict[str, Any]],
) -> tuple[dict[str, str], list[str]]:
    """Deterministic PRE-OUTCOME promoted/rejected membership keyed by the
    canonical_opportunity_id on horizon_candidates.

    Returns (membership, conflicts). An opportunity presenting BOTH a promoted
    and a rejected horizon status is ambiguous and fails closed. Membership is
    never derived from realised outcome; the canonical opportunity id is the
    only join key (no legacy opportunity_id fallback).
    """
    seen: dict[str, set[str]] = defaultdict(set)
    for hc in horizon_candidates:
        opp = str(hc.get("canonical_opportunity_id", "") or "")
        if not opp:
            continue
        status = str(hc.get("selection_status", "") or "").upper()
        if status in _PROMOTED_STATUSES:
            seen[opp].add("PROMOTED")
        elif status in _REJECTED_STATUSES:
            seen[opp].add("REJECTED")
    membership: dict[str, str] = {}
    conflicts: list[str] = []
    for opp, classes in seen.items():
        if len(classes) != 1:
            conflicts.append(opp)  # both promoted and rejected -> ambiguous
            continue
        membership[opp] = next(iter(classes))
    return membership, sorted(conflicts)


def build_opp1_observations(
    horizon_candidates: list[dict[str, Any]],
    shadow_trades: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One promoted/rejected opportunity observation per canonical opportunity,
    paired to its CURRENT shadow realised R.

    - Membership is PRE-OUTCOME (horizon_candidates selection_status), keyed by
      canonical_opportunity_id only.
    - Outcome pairing reuses build_opportunity_observations: account fanout and
      repeated horizons collapse; MISSING outcomes are excluded (never 0.0);
      conflicting outcomes fail closed.
    - An explicit realised 0.0R is a VALID outcome and is included.
    """
    membership, membership_conflicts = classify_opportunity_membership(horizon_candidates)
    outcomes, outcome_diagnostics = build_opportunity_observations(shadow_trades)
    outcome_by_id = {row.canonical_opportunity_id: row for row in outcomes}

    rows: list[dict[str, Any]] = []
    conflicts: list[str] = list(membership_conflicts) + list(outcome_diagnostics.get("ambiguous_opportunities", ()))
    missing_outcome = 0
    classified = 0
    for opp, group in sorted(membership.items()):
        classified += 1
        outcome = outcome_by_id.get(opp)
        if outcome is None or outcome.outcome_r is None:
            missing_outcome += 1
            continue
        rows.append({
            "canonical_opportunity_id": opp,
            "group": group,                       # PROMOTED | REJECTED
            "outcome_r": outcome.outcome_r,       # explicit 0.0 is valid & kept
            "won": 1.0 if outcome.outcome_r > 0 else 0.0,
            "h4_regime": getattr(outcome, "h4_regime", "") or "UNKNOWN",
            "market_phase": getattr(outcome, "market_phase", "") or "UNKNOWN",
            "strategy": getattr(outcome, "pattern", "") or "UNKNOWN",
            "_order": outcome.decision_time,
        })
    rows.sort(key=lambda row: (row["_order"], row["canonical_opportunity_id"]))
    diagnostics = {
        **outcome_diagnostics,
        "classified_opportunities": classified,
        "paired_opportunities": len(rows),
        "missing_outcomes_excluded": missing_outcome,
        "promoted_paired": sum(1 for r in rows if r["group"] == "PROMOTED"),
        "rejected_paired": sum(1 for r in rows if r["group"] == "REJECTED"),
        "conflicting_opportunities": tuple(sorted(set(conflicts))),
    }
    return rows, diagnostics


def _opp1_split(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    unique = sorted({row["_order"] for row in rows})
    if len(unique) < 2:
        return rows, []
    index = max(1, min(len(unique) - 1, int(len(unique) * 0.60)))
    boundary_key = unique[index]
    return ([r for r in rows if r["_order"] < boundary_key],
            [r for r in rows if r["_order"] >= boundary_key])


def _opp1_group_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Outcome stats over ONLY valid paired outcomes (no imputed zeros)."""
    if not rows:
        return {"n": 0, "mean_r": None, "median_r": None, "win_rate": None, "loss_rate": None, "stdev_r": None}
    values = [r["outcome_r"] for r in rows]
    return {
        "n": len(rows),
        "mean_r": round(sum(values) / len(values), 4),
        "median_r": round(statistics.median(values), 4),
        "win_rate": round(sum(1 for v in values if v > 0) / len(values), 4),
        "loss_rate": round(sum(1 for v in values if v < 0) / len(values), 4),
        "stdev_r": round(statistics.pstdev(values), 4) if len(values) > 1 else 0.0,
    }


def _opp1_classify(status: str, discovery_delta: float | None, validation_delta: float | None) -> str:
    """Promoted-minus-rejected expectancy delta, discovery vs later validation."""
    if status != "COMPLETE":
        return "INSUFFICIENT_EVIDENCE"
    if discovery_delta is None or validation_delta is None:
        return "NO_RELIABLE_SELECTION_SIGNAL"
    if discovery_delta > 0 and validation_delta > 0:
        return "PROMOTED_OUTPERFORMS_REJECTED"
    if discovery_delta > 0 and validation_delta <= 0:
        return "DISCOVERY_ONLY"
    if validation_delta < 0 and discovery_delta < 0:
        return "PROMOTED_UNDERPERFORMS_REJECTED"
    return "NO_RELIABLE_SELECTION_SIGNAL"


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
    and assessment layers, NOT portfolio/ranking data. One canonical
    opportunity is one independent observation; account fanout and repeated
    horizons collapse; MISSING outcomes are EXCLUDED (never 0.0). Research only.
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

    rows, diagnostics = build_opp1_observations(horizon_candidates, shadow_trades)
    discovery, validation = _opp1_split(rows)

    promoted = [r for r in rows if r["group"] == "PROMOTED"]
    rejected = [r for r in rows if r["group"] == "REJECTED"]
    promoted_stats = _opp1_group_stats(promoted)
    rejected_stats = _opp1_group_stats(rejected)

    def _delta(rws: list[dict[str, Any]]) -> float | None:
        p = [r["outcome_r"] for r in rws if r["group"] == "PROMOTED"]
        j = [r["outcome_r"] for r in rws if r["group"] == "REJECTED"]
        if len(p) < _OPP1_MIN_GROUP or len(j) < _OPP1_MIN_GROUP:
            return None
        return (sum(p) / len(p)) - (sum(j) / len(j))

    discovery_delta = _delta(discovery)
    validation_delta = _delta(validation)
    overall_delta = (
        promoted_stats["mean_r"] - rejected_stats["mean_r"]
        if promoted_stats["mean_r"] is not None and rejected_stats["mean_r"] is not None
        else None
    )
    paired = len(rows)

    if diagnostics["conflicting_opportunities"]:
        status = ReadinessStatus.BLOCKED
        reason = "Conflicting promoted/rejected membership or outcome for a canonical opportunity"
    elif paired < _OPP1_MIN_TOTAL or len(discovery) < _OPP1_MIN_DISCOVERY or len(validation) < _OPP1_MIN_VALIDATION:
        status = ReadinessStatus.INSUFFICIENT_DATA
        reason = (
            f"paired/discovery/validation={paired}/{len(discovery)}/{len(validation)}; "
            f"need {_OPP1_MIN_TOTAL}/{_OPP1_MIN_DISCOVERY}/{_OPP1_MIN_VALIDATION}"
        )
    elif promoted_stats["n"] < _OPP1_MIN_GROUP or rejected_stats["n"] < _OPP1_MIN_GROUP:
        status = ReadinessStatus.INSUFFICIENT_DATA
        reason = (
            f"promoted/rejected paired={promoted_stats['n']}/{rejected_stats['n']}; "
            f"need >= {_OPP1_MIN_GROUP} in each group"
        )
    else:
        status = ReadinessStatus.COMPLETE
        reason = "Chronological promoted-vs-rejected opportunity expectancy evaluation completed on later unseen opportunities"

    finding = _opp1_classify(status if isinstance(status, str) else str(status), discovery_delta, validation_delta)

    def _regime_cells(rws: list[dict[str, Any]]) -> dict[str, Any]:
        cells: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rws:
            cells[r["h4_regime"]].append(r)
        out: dict[str, Any] = {}
        for cell, cr in sorted(cells.items()):
            stats = _opp1_group_stats(cr)
            stats["sufficient"] = stats["n"] >= _OPP1_MIN_GROUP
            out[cell] = stats
        return out

    overall = {
        "canonical_question": "OPP-1",
        "hypothesis": "Opportunities promoted into the decision pipeline (SELECTED horizon candidate) have higher subsequent realised R than opportunities rejected/filtered out, and that advantage persists on later unseen opportunities.",
        "research_classification": "observational_promoted_vs_rejected_expectancy",
        "causal_claim": "NONE — observational opportunity-selection comparison; shadow outcomes are counterfactual/simulated",
        "distinct_from_port1_d6": "OPP-1 compares promoted vs rejected OPPORTUNITY expectancy (intake/assessment pipeline). D6 = rank ordering; PORT-1 = selected-vs-best cycle. Distinct runners/reports.",
        "unit_of_analysis": "one canonical_opportunity_id; account fanout and repeated horizons collapse",
        "canonical_identity_authority": "canonical_opportunity_id only (no legacy opportunity_id fallback)",
        "membership_authority": "pre-outcome horizon_candidates selection_status (PROMOTED vs REJECTED); never derived from realised outcome",
        "outcome_authority": "CURRENT shadow simulated_outcome.pnl_r_multiple joined to the same canonical_opportunity_id",
        "join_semantics": "deterministic canonical_opportunity_id join; one opportunity resolves to at most one independent outcome; conflicts fail closed; no positional/symbol-only/legacy joins",
        "missing_outcome_semantics": "MISSING outcomes are EXCLUDED and counted in missing_outcomes_excluded; never converted to 0.0/loss/success. Explicit realised 0.0R is VALID and included.",
        "evidence_epoch": "CURRENT",
        "opportunities_read": total_opps,
        "horizon_candidates_read": len(horizon_candidates),
        "classified_opportunities": diagnostics["classified_opportunities"],
        "paired_opportunities": paired,
        "missing_outcome_count": diagnostics["missing_outcomes_excluded"],
        "promoted_paired": diagnostics["promoted_paired"],
        "rejected_paired": diagnostics["rejected_paired"],
        "promoted_outcome": promoted_stats,
        "rejected_outcome": rejected_stats,
        "promoted_minus_rejected_mean_r": (round(overall_delta, 4) if overall_delta is not None else None),
        "discovery": {"n": len(discovery), "promoted_minus_rejected_mean_r": (round(discovery_delta, 4) if discovery_delta is not None else None)},
        "later_unseen_validation": {"n": len(validation), "promoted_minus_rejected_mean_r": (round(validation_delta, 4) if validation_delta is not None else None)},
        "context_diagnostics": {
            "h4_regime": _regime_cells(rows),
            "note": "Same-opportunity pre-outcome context; cells with n<15 are insufficient and never over-interpreted. Context does not inflate independent n.",
        },
        "finding_classification": finding,
        "completion_reason": reason,
        "limitations": [
            "Promoted/rejected membership is pre-outcome (horizon_candidates); never derived from realised outcome.",
            "MISSING outcomes are excluded, never imputed to 0.0; an explicit realised 0.0R is a valid observation.",
            "One canonical opportunity is one observation; account fanout and repeated horizons never inflate n.",
            "Shadow outcomes are counterfactual/simulated, not broker truth.",
            "COMPLETE means the chronological comparison ran validly, not that the pipeline is profitable.",
            "Research only; production opportunity generation/selection is never modified.",
        ],
        "diagnostics": diagnostics,
        "sufficiency": {
            "minimum_total": _OPP1_MIN_TOTAL,
            "minimum_discovery": _OPP1_MIN_DISCOVERY,
            "minimum_validation": _OPP1_MIN_VALIDATION,
            "minimum_group": _OPP1_MIN_GROUP,
        },
        "quarantine_boundary": boundary.describe(),
    }

    confidence = "MEDIUM" if status == ReadinessStatus.COMPLETE else "INSUFFICIENT_DATA"
    if status == ReadinessStatus.COMPLETE:
        recommendation = (
            f"OBSERVATIONAL FINDING [{finding}]: promoted {promoted_stats['mean_r']:+.4f}R vs "
            f"rejected {rejected_stats['mean_r']:+.4f}R (validation delta "
            + (f"{validation_delta:+.4f}R)" if validation_delta is not None else "N/A)")
        )
    else:
        recommendation = f"{getattr(status, 'value', status)}: {reason}"

    report = build_selection_report(
        question_id="OPP-1", status=status,
        overall=overall, confidence=confidence,
        dataset={
            "source": "horizon_candidates+shadow_trades",
            "sample_size": paired,
            "independent_observations": paired,
            "promoted_with_outcome": promoted_stats["n"],
            "rejected_with_outcome": rejected_stats["n"],
            "quarantine_boundary": boundary.describe(),
        },
        recommendation=recommendation, source="horizon_candidates+shadow_trades",
        records_used=paired,
        records_excluded=diagnostics["missing_outcomes_excluded"],
        assumptions=[
            "Independent unit = canonical_opportunity_id; account fanout and repeated horizons collapse.",
            "Promoted/rejected membership = pre-outcome horizon_candidates selection_status; canonical_opportunity_id is the only join key (no legacy opportunity_id fallback).",
            "Outcome = CURRENT shadow simulated_outcome.pnl_r_multiple for the same canonical opportunity.",
            "MISSING outcomes are excluded, never imputed to 0.0; explicit realised 0.0R remains valid.",
            "Conflicting membership or outcome for one opportunity fails closed.",
            "Deterministic chronological discovery/validation; a promoted advantage must persist on later unseen opportunities.",
            "OPP-1 is distinct from D6 and PORT-1; neither of their reports completes OPP-1.",
        ],
        module="research_engine.experiments.opportunity_selection",
    )

    return report


# All OPP-1 calculation now lives in the module-level pure helpers above
# (classify_opportunity_membership / build_opp1_observations / _opp1_group_stats
# / _opp1_split / _opp1_classify). The legacy _score_bucket/_delta_stats helpers
# — which imputed missing outcomes to 0.0 into score buckets — were removed.