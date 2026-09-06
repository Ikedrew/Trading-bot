"""
Research Engine Evidence Registry — connects consumed datasets to their evidence
consumers so a completeness audit can prove that every DIRECTLY_CONSUMED /
SUPPORTING_CONSUMED dataset has a registered consumer.

The four Step-4 connected datasets (horizon_candidates, strategy_candidates,
execution_attempts, management_actions) each have an evidence consumer here.
The two intentionally-excluded datasets (events, position_excursion) have NO
consumer by design — that is an explicit, documented disposition, not a defect.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from research_engine.evidence.execution_attempts import execution_attempt_evidence
from research_engine.evidence.horizon_candidates import horizon_candidate_evidence
from research_engine.evidence.management_actions import management_actions_evidence
from research_engine.evidence.strategy_candidates import strategy_candidate_evidence

# Canonical list of datasets that ARE consumed by Research Engine evidence
# consumers (status DIRECTLY_CONSUMED or SUPPORTING_CONSUMED).
CONSUMED_EVIDENCE_DATASETS: tuple[str, ...] = (
    "horizon_candidates",
    "strategy_candidates",
    "execution_attempts",
    "management_actions",
)

# dataset -> evidence consumer callable
DATASET_EVIDENCE_CONSUMERS: dict[str, Callable[..., dict[str, Any]]] = {
    "horizon_candidates": horizon_candidate_evidence,
    "strategy_candidates": strategy_candidate_evidence,
    "execution_attempts": execution_attempt_evidence,
    "management_actions": management_actions_evidence,
}


def run_dataset_evidence(*, symbol: str | None = None) -> dict[str, dict[str, Any]]:
    """Run every registered evidence consumer and return a dataset-keyed report.

    Returns a dict mapping each consumed dataset name to its evidence report
    (as produced by the consumer function).
    """
    return {
        ds: fn(symbol=symbol)
        for ds, fn in DATASET_EVIDENCE_CONSUMERS.items()
    }
