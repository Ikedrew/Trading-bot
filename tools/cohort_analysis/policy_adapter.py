"""
Policy Adapter — Attaches cohort-based management policies to trades AFTER entry.

PURE metadata enrichment. Does NOT modify:
- execution logic
- entry decisions
- scoring
- signals

This only ASSIGNS behavior metadata to an already-opened trade.
"""

from __future__ import annotations

from typing import Any

from tools.cohort_analysis.cohort_builder import build_cohort_from_trade
from tools.cohort_analysis.cohort_policy_registry import get_policy
from tools.cohort_analysis.cohort_policy_types import CohortKey, ManagementPolicy


def assign_policy_to_trade(trade: dict[str, Any], decision: Any) -> dict[str, Any]:
    """
    Assign a cohort-based management policy to a trade record.

    Called AFTER trade entry is confirmed. Does not influence
    the entry decision — only attaches post-entry metadata.

    Args:
        trade: Mutable trade record dict (enriched in-place).
        decision: UnifiedDecision object or dict-based audit record
                  used to derive CohortKey.

    Returns:
        The same trade dict with added "cohort" and "management_policy" fields.
    """
    cohort = build_cohort_from_trade(decision)
    policy = get_policy(cohort)

    trade["cohort"] = cohort
    trade["management_policy"] = policy

    return trade
