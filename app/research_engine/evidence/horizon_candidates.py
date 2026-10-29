"""
Horizon candidate evidence — selected-vs-rejected horizon comparison (A).

Research questions enabled:
    - Which horizons were considered for each opportunity and why?
    - Is the SELECTED horizon actually distinct from the REJECTED/INELIGIBLE
      alternatives (confidence / eligibility)?
    - Are eligibility gates consistent (e.g. INTRADAY/EXTENDED requiring HTF
      alignment)? Which reason dominates rejection?
    - Does the selected horizon outperform counterfactual horizons when joined
      to realised outcomes (trade_journal) at the canonical_opportunity_id
      grain?

Temporal classification: horizon candidates are produced at decision time
(BEFORE_DECISION) — safe as pre-decision evidence.

Join keys (preferred order): canonical_opportunity_id, observation_id,
entity_id, correlation_id, cycle_id.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from research_engine.data_access.loaders import load_horizon_candidates
from research_engine.evidence.base import (
    counter_summary,
    disposition_of,
    lineage_coverage,
    numeric_stats,
)

_DATASET = "horizon_candidates"


def horizon_candidate_evidence(
    symbol: str | None = None,
) -> dict[str, Any]:
    """Build the horizon candidate evidence report from canonical S3 records."""
    disp = disposition_of(_DATASET)
    records = load_horizon_candidates(symbol)

    selection_status = counter_summary(r.get("selection_status", "") for r in records)
    horizons = counter_summary(r.get("horizon", "") for r in records)

    # Opportunities evaluated.
    opp_ids = {r.get("canonical_opportunity_id") for r in records}
    opp_ids.discard(None)
    opp_ids.discard("")

    # Eligibility by horizon.
    eligible_by_horizon: dict[str, dict[str, Any]] = {}
    by_horizon: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        by_horizon[str(rec.get("horizon", "") or "?").upper()].append(rec)
    for h, hrecs in sorted(by_horizon.items()):
        eligible_by_horizon[h] = {
            "candidates": len(hrecs),
            "eligible": sum(1 for r in hrecs if r.get("eligible")),
            "selected": sum(
                1 for r in hrecs if r.get("selection_status") == "SELECTED"
            ),
            "confidence": numeric_stats(r.get("confidence") for r in hrecs),
        }

    # Selected-vs-rejected comparison (SELECTED vs REJECTED/INELIGIBLE).
    selected = [r for r in records if r.get("selection_status") == "SELECTED"]
    rejected = [r for r in records if r.get("selection_status") in ("REJECTED", "INELIGIBLE")]
    selected_vs_rejected = {
        "selected_count": len(selected),
        "rejected_ineligible_count": len(rejected),
        "selected_confidence": numeric_stats(r.get("confidence") for r in selected),
        "rejected_confidence": numeric_stats(r.get("confidence") for r in rejected),
    }

    # Dominant rejection reasons (INELIGIBLE/REJECTED records).
    rejection_reasons = counter_summary(
        str(r.get("reasoning", "") or "")[:200] for r in rejected
    )

    lineage = lineage_coverage(
        records,
        ("canonical_opportunity_id", "observation_id", "entity_id",
         "correlation_id", "cycle_id"),
    )

    return {
        "dataset": _DATASET,
        "record_count": len(records),
        "disposition_status": disp.status.value,
        "temporal_availability": disp.temporal_availability.value,
        "research_purpose": disp.research_purpose,
        "lineage_coverage": lineage,
        "analysis": {
            "opportunities_evaluated": len(opp_ids),
            "selection_status_distribution": selection_status,
            "horizon_distribution": horizons,
            "eligibility_and_selection_by_horizon": eligible_by_horizon,
            "selected_vs_rejected": selected_vs_rejected,
            "dominant_rejection_reasons": rejection_reasons,
        },
        "guard_notes": [
            "Produced at decision time (BEFORE_DECISION) — safe as pre-decision "
            "evidence.",
            "decision_id may be empty; canonical_opportunity_id is the reliable "
            "join key.",
            "Research observes candidates only; horizon selection is untouched.",
        ],
    }