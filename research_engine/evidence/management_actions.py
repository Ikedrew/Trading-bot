"""
Management action evidence — trade-management effectiveness research (B).

Research questions enabled:
    - Which management actions were initiated while each position was open?
    - Did SLTP_MODIFY / PARTIAL_CLOSE / CLOSE interventions improve or harm
      realised outcomes (joined to trade_truth at the trade grain)?
    - What is the action_type / action_reason distribution and per-trade
      management intensity?
    - Does the action_reason (intent) differ from trade_truth's single
      exit_reason (result) — i.e. management intent vs outcome?

RESEARCH GRAIN: one record = one management ACTION initiated by the
management layer (before the broker call). ONE trade may have many management
actions; these are execution-side diagnostics, NOT separate trade outcomes.

Temporal classification: management actions occur AFTER decision and BEFORE /
during the trade — never usable as pre-decision features.

Join keys (preferred order): trade_id, correlation_id, canonical_opportunity_id,
decision_id, observation_id, cycle_id.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from research_engine.data_access.loaders import load_management_actions
from research_engine.evidence.base import (
    counter_summary,
    disposition_of,
    lineage_coverage,
    numeric_stats,
)

_DATASET = "management_actions"


def management_actions_evidence(
    symbol: str | None = None,
) -> dict[str, Any]:
    """Build the management-action evidence report from canonical S3 records."""
    disp = disposition_of(_DATASET)
    records = load_management_actions(symbol)

    action_types = counter_summary(r.get("action_type", "") for r in records)
    action_reasons = counter_summary(r.get("action_reason", "") for r in records)

    # Management intensity: number of management actions per trade_id.
    per_trade: dict[str, int] = defaultdict(int)
    for rec in records:
        tid = rec.get("trade_id")
        if tid:
            per_trade[str(tid)] += 1
    actions_per_trade = (
        numeric_stats(list(per_trade.values())) if per_trade else {"count": 0}
    )

    # Per-action-type statistics.
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        by_type[str(rec.get("action_type", "") or "?")].append(rec)
    by_type_summary = {
        t: {
            "count": len(recs),
            "reason_distribution": counter_summary(
                r.get("action_reason", "") for r in recs
            ),
            "requested_volume": numeric_stats(
                r.get("requested_volume") for r in recs
            ),
        }
        for t, recs in sorted(by_type.items())
    }

    lineage = lineage_coverage(
        records,
        ("trade_id", "correlation_id", "canonical_opportunity_id",
         "decision_id", "observation_id", "cycle_id"),
    )

    return {
        "dataset": _DATASET,
        "record_count": len(records),
        "disposition_status": disp.status.value,
        "temporal_availability": disp.temporal_availability.value,
        "research_purpose": disp.research_purpose,
        "lineage_coverage": lineage,
        "analysis": {
            "action_type_distribution": action_types,
            "action_reason_distribution": action_reasons,
            "actions_per_trade": actions_per_trade,
            "by_action_type": by_type_summary,
        },
        "guard_notes": [
            "Grain = individual management ACTION initiated pre-broker-call; "
            "one trade may have many actions. Never treat actions as separate "
            "trade outcomes.",
            "trade_id may be null on some management-retry SLTP/CLOSE attempts; "
            "canonical_opportunity_id / correlation_id are reliable join keys.",
            "AFTER_DECISION_BEFORE_OUTCOME — management/research-side evidence "
            "only; outcome effects analysed at trade grain via trade_truth / "
            "trade_journal.",
        ],
    }
