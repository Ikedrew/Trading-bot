"""
Execution attempt evidence — execution-quality diagnostics (B).

Research questions enabled:
    - How many individual broker attempts occur per action type?
    - How frequent are retries (attempt_number > 1) and what is the retry reason
      distribution?
    - How often does the broker reject/requote (broker_result.ok == False) and
      what retcodes dominate?
    - What are the slippage / spread-at-attempt statistics?
    - How does the broker-result quality distribute across correlation_ids
      (execution-surface reliability)?

RESEARCH GRAIN: one record = one broker attempt. ONE trade may have MANY
attempts; attempts are NEVER treated as separate trade outcomes. Outcome joins
must happen at trade grain via correlation_id / broker_result.deal.

Temporal classification: attempts happen AFTER decision and BEFORE / during
the trade — never usable as pre-decision features.

Join keys (preferred order): correlation_id, canonical_opportunity_id,
decision_id, trade_id, broker_result.deal.
"""

from __future__ import annotations

from typing import Any

from research_engine.data_access.loaders import load_execution_attempts
from research_engine.evidence.base import (
    counter_summary,
    disposition_of,
    lineage_coverage,
    numeric_stats,
)

_DATASET = "execution_attempts"


def execution_attempt_evidence(
    symbol: str | None = None,
) -> dict[str, Any]:
    """Build the execution-attempt evidence report from canonical S3 records."""
    disp = disposition_of(_DATASET)
    records = load_execution_attempts(symbol)

    action_types = counter_summary(r.get("action_type", "") for r in records)

    retries = [r for r in records if int(r.get("attempt_number", 1) or 1) > 1]
    rejected = [r for r in records if (r.get("broker_result") or {}).get("ok") is False]

    retcodes = counter_summary(
        (r.get("broker_result") or {}).get("retcode") for r in records
    )
    retry_reasons = counter_summary(
        r.get("retry_reason") for r in records
    )

    # Attempts-per-trade proxy: group by correlation_id when present (best key).
    per_corr: dict[str, int] = {}
    for rec in records:
        cid = rec.get("correlation_id")
        if cid:
            per_corr[cid] = per_corr.get(cid, 0) + 1
    attempts_per_corr = numeric_stats(list(per_corr.values())) if per_corr else {"count": 0}

    slippage = numeric_stats(
        r.get("slippage") for r in records
    )
    spread = numeric_stats(
        r.get("spread_at_attempt") for r in records
    )

    lineage = lineage_coverage(
        records,
        ("correlation_id", "canonical_opportunity_id", "decision_id",
         "trade_id", "broker_result.deal"),
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
            "retry_count": len(retries),
            "broker_rejected_count": len(rejected),
            "rejection_rate": round(len(rejected) / len(records), 4) if records else 0.0,
            "retcode_distribution": retcodes,
            "retry_reason_distribution": retry_reasons,
            "attempts_per_correlation_id": attempts_per_corr,
            "slippage_stats": slippage,
            "spread_at_attempt_stats": spread,
        },
        "guard_notes": [
            "Grain = individual broker attempt; one trade may have many attempts. "
            "Never treat attempts as separate trade outcomes.",
            "trade_id is null for ENTRY attempts; join ENTRY attempts to the "
            "realised trade via broker_result.deal == trade_truth ticket or via "
            "correlation_id when present.",
            "AFTER_DECISION_BEFORE_OUTCOME — execution-side evidence only.",
        ],
    }