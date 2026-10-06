"""Snapshot-bound governed meta/risk population adapters (Pass D Repair 2).

The governed canonical question cycle must never let a meta (G2) or risk
(R3/R4/R5) evaluator independently reopen storage or fall back to a legacy
loader.  This module reconstructs the two governed populations those questions
require deterministically from the raw bound datasets of one immutable snapshot:

* the HD14 G2 lineage population  -> ``decision_trace`` + completed
  ``shadow_runtime`` lifecycles (identity ``(entity_id, canonical_opportunity_id)``)
* the HD10 R3/R4/R5 risk population -> ``RiskPolicyEvidence`` built from
  ``decision_trace`` + completed ``shadow_runtime`` lifecycles

No filesystem scan, no live S3 read, no legacy ``research_shadow_trades``
loader, no global mutable singleton.  The same snapshot ID is propagated by the
caller; these builders only ever read the already-verified bound records passed
in and therefore can never mix snapshots.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from research_engine.control_plane.risk_policy_evidence import (
    RiskPolicyEvidence,
    build_risk_policy_evidence,
)
from research_engine.data_access.shadow_runtime_ingestion import (
    reconstruct_completed_shadow_trades,
)
from research_engine.registry.risk_policy_adjudication import BASELINE_RISK_POLICY_V1

# A missing bound outcome stream is an observation gap, never a silent empty
# universe and never a zero-risk population.
_MISSING_SHADOW_RUNTIME = (
    "governed snapshot does not bind shadow_runtime; "
    "meta/risk outcome population cannot be reconstructed"
)
_MISSING_DECISION_TRACE = (
    "governed snapshot does not bind decision_trace; "
    "meta/risk decision population cannot be reconstructed"
)


@dataclass(frozen=True)
class GovernedLineagePopulation:
    """G2 meta population: the decision and outcome records of one snapshot.

    Identity is the frozen HD14 composite ``(entity_id, canonical_opportunity_id)``.
    ``outcome_records`` are the raw ``shadow_runtime`` events (the same authority
    the G2 runner's snapshot path already consumes); the runner collapses
    OPEN/CLOSE lifecycles exactly as before, so no scientific semantics change.
    """

    decision_records: tuple[dict[str, Any], ...]
    outcome_records: tuple[dict[str, Any], ...]
    identity_fields: tuple[str, str] = ("entity_id", "canonical_opportunity_id")
    decision_source: str = "decision_trace"
    outcome_source: str = "shadow_runtime"
    missing_evidence: tuple[str, ...] = ()

    @property
    def population_count(self) -> int:
        """Number of distinct decision identities in the meta population."""
        return len(self.decision_records)


def _snapshot_rows(
    decision_records: Iterable[Mapping[str, Any]],
    shadow_runtime_records: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], tuple[str, ...]]:
    decisions = [dict(item) for item in decision_records]
    shadow = [dict(item) for item in shadow_runtime_records]
    missing: list[str] = []
    if not shadow:
        missing.append(_MISSING_SHADOW_RUNTIME)
    if not decisions:
        # An empty decision trace is a real governable state for G2 (every
        # outcome becomes a MISSING orphan); it is not fatal for risk evidence
        # (records are excluded with an explicit reason).  Do not fabricate it.
        missing.append(_MISSING_DECISION_TRACE)
    return decisions, shadow, tuple(missing)


def build_governed_lineage_population(
    decision_records: Iterable[Mapping[str, Any]],
    shadow_runtime_records: Iterable[Mapping[str, Any]],
) -> GovernedLineagePopulation:
    """Build the snapshot-bound G2 lineage population from bound datasets."""
    decisions, shadow, missing = _snapshot_rows(decision_records, shadow_runtime_records)
    return GovernedLineagePopulation(
        decision_records=tuple(decisions),
        outcome_records=tuple(shadow),
        missing_evidence=missing,
    )


def build_governed_risk_evidence(
    decision_records: Iterable[Mapping[str, Any]],
    shadow_runtime_records: Iterable[Mapping[str, Any]],
) -> RiskPolicyEvidence:
    """Build the snapshot-bound HD10 risk population (R3/R4/R5).

    Mirrors ``load_governed_risk_evidence`` exactly except that the legacy
    ``research_shadow_trades`` dataset is never consulted: the only admissible
    outcome authority is the completed ``shadow_runtime`` lifecycle of the bound
    snapshot.  Missing/empty sources therefore fail closed inside
    ``build_risk_policy_evidence`` (explicit exclusions, never zero risk).
    """
    decisions, shadow, _ = _snapshot_rows(decision_records, shadow_runtime_records)
    outcomes = reconstruct_completed_shadow_trades(shadow)
    return build_risk_policy_evidence(
        decisions,
        outcomes,
        baseline_authority=deepcopy(BASELINE_RISK_POLICY_V1),
    )


__all__ = [
    "GovernedLineagePopulation",
    "build_governed_lineage_population",
    "build_governed_risk_evidence",
]
