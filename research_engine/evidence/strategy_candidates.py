"""
Strategy candidate evidence — rejected-vs-selected strategy analysis (A).

Research questions enabled:
    - Which strategies were considered for each opportunity and how were they
      ranked?
    - How close was the winner to the alternatives (confidence gap / rank)?
    - Which supporting conditions are observed surrounding each strategy?
    - Rejected-vs-selected comparison (candidate-set level, opportunity grain).

Counterfactual evidence not available in decision_trace (winner only) or
strategy_observations (single family per observation).

Temporal classification: produced at strategy-selection time (BEFORE_DECISION) —
safe as pre-decision evidence.

Join keys (preferred order): canonical_opportunity_id, observation_id,
correlation_id, decision_id, cycle_id. NOTE: some records carry empty
correlation_id / cycle_id=0 / null entity_id (live-observed); the analysis
reports the lineage coverage explicitly.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from research_engine.data_access.loaders import load_strategy_candidates
from research_engine.evidence.base import (
    counter_summary,
    disposition_of,
    lineage_coverage,
    numeric_stats,
)

_DATASET = "strategy_candidates"


def strategy_candidate_evidence(
    symbol: str | None = None,
) -> dict[str, Any]:
    """Build the strategy candidate evidence report from canonical S3 records."""
    disp = disposition_of(_DATASET)
    records = load_strategy_candidates(symbol)

    families = counter_summary(r.get("strategy_family", "") for r in records)
    selected_families = counter_summary(
        r.get("strategy_family", "")
        for r in records
        if r.get("selected") is True
    )

    # Candidate-set statistics at the opportunity grain.
    by_opp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        by_opp[str(rec.get("canonical_opportunity_id", "") or "?")].append(rec)

    set_sizes = [len(v) for v in by_opp.values()]
    set_stats = {
        "opportunities_with_candidates": len(by_opp),
        "candidates_per_opportunity": numeric_stats(set_sizes),
    }

    # Selected vs rejected comparison (per family confidence).
    selected = [r for r in records if r.get("selected") is True]
    rejected = [r for r in records if r.get("selected") is not True]
    selected_vs_rejected = {
        "selected_count": len(selected),
        "rejected_count": len(rejected),
        "selected_confidence": numeric_stats(r.get("confidence") for r in selected),
        "rejected_confidence": numeric_stats(r.get("confidence") for r in rejected),
        # rank distribution of the selected candidates (should always be 1)
        "selected_ranks": counter_summary(str(r.get("rank")) for r in selected),
    }

    # Rank gap: distance between winner confidence and nearest alternative.
    gaps: list[float] = []
    for opp_recs in by_opp.values():
        winners = {r.get("confidence") for r in opp_recs if r.get("selected") is True}
        others = [
            r.get("confidence")
            for r in opp_recs
            if r.get("selected") is not True and r.get("confidence") is not None
        ]
        for w in winners:
            if w is None or not others:
                continue
            gaps.append(w - max(others))
    rank_gap = numeric_stats(gaps) if gaps else {"count": 0}

    # Most common supporting conditions (overall).
    condition_counts: dict[str, int] = defaultdict(int)
    for rec in records:
        for cond, value in dict(rec.get("supporting_conditions") or {}).items():
            if value is True:
                condition_counts[cond] += 1
    supporting_conditions = {
        k: int(v) for k, v in sorted(condition_counts.items(), key=lambda kv: -kv[1])
    }

    lineage = lineage_coverage(
        records,
        ("canonical_opportunity_id", "observation_id", "correlation_id",
         "decision_id", "cycle_id"),
    )

    return {
        "dataset": _DATASET,
        "record_count": len(records),
        "disposition_status": disp.status.value,
        "temporal_availability": disp.temporal_availability.value,
        "research_purpose": disp.research_purpose,
        "lineage_coverage": lineage,
        "analysis": {
            "opportunity_grain": set_stats,
            "strategy_family_distribution": families,
            "selected_family_distribution": selected_families,
            "selected_vs_rejected": selected_vs_rejected,
            "winner_vs_best_alternative_confidence_gap": rank_gap,
            "supporting_condition_frequency": supporting_conditions,
        },
        "guard_notes": [
            "Produced at strategy-selection time (BEFORE_DECISION) — safe as "
            "pre-decision evidence.",
            "Some records carry empty correlation_id / cycle_id=0 / null "
            "entity_id; canonical_opportunity_id is the reliable join key.",
            "Research observes and analyses candidates only; candidate promotion "
            "thresholds are untouched.",
        ],
    }
