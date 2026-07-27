"""
Lifecycle Join — Reconstructs the complete path from market opportunity to realised outcome.

Joins across datasets using the identity chain:
    opportunity_id → assessment_id → cycle_id → decision_id → correlation_id → trade_id

Produces a unified LifecycleRecord for each opportunity that contains
whatever data is available at each stage (supports partial lifecycles).

This module is PURELY RESEARCH. It does NOT:
    - Affect trading decisions
    - Modify runtime behaviour
    - Write to persistence
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA MODEL
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class LifecycleQuality:
    """Quality assessment of a lifecycle join."""
    stages_present: list[str] = field(default_factory=list)
    stages_missing: list[str] = field(default_factory=list)
    completeness: float = 0.0       # 0.0–1.0 (fraction of stages present)
    orphan_type: str = ""           # "" if not orphan, else which stage has no successor
    duplicate_count: int = 0        # >0 if multiple records matched at any stage
    join_path: str = ""             # Description of keys used

    @property
    def is_complete(self) -> bool:
        return self.completeness >= 0.99

    @property
    def is_orphan(self) -> bool:
        return self.orphan_type != ""


@dataclass
class LifecycleRecord:
    """Unified view of one opportunity's complete lifecycle."""

    # Core identity
    opportunity_id: str = ""
    symbol: str = ""
    cycle_id: int = 0
    entity_id: str = ""

    # Stages (None = not available/not reached)
    opportunity: dict[str, Any] | None = None
    assessment: dict[str, Any] | None = None
    ranking: dict[str, Any] | None = None      # The candidate entry from portfolio ranking
    shadow: dict[str, Any] | None = None       # Shadow comparison for this cycle
    decision: dict[str, Any] | None = None     # Decision ledger entry
    execution: dict[str, Any] | None = None    # Execution result
    outcome: dict[str, Any] | None = None      # Trade truth

    # Quality
    quality: LifecycleQuality = field(default_factory=LifecycleQuality)

    # Derived (for quick research queries)
    final_state: str = ""           # EXECUTED / REJECTED / EXPIRED / UNKNOWN
    rejection_reason: str = ""
    r_multiple: float | None = None
    pnl: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "symbol": self.symbol,
            "cycle_id": self.cycle_id,
            "entity_id": self.entity_id,
            "opportunity": self.opportunity,
            "assessment": self.assessment,
            "ranking": self.ranking,
            "shadow": self.shadow,
            "decision": self.decision,
            "execution": self.execution,
            "outcome": self.outcome,
            "quality": {
                "stages_present": self.quality.stages_present,
                "stages_missing": self.quality.stages_missing,
                "completeness": self.quality.completeness,
                "orphan_type": self.quality.orphan_type,
                "duplicate_count": self.quality.duplicate_count,
                "join_path": self.quality.join_path,
            },
            "final_state": self.final_state,
            "rejection_reason": self.rejection_reason,
            "r_multiple": self.r_multiple,
            "pnl": self.pnl,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# JOIN ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

_ALL_STAGES = ["opportunity", "assessment", "ranking", "decision", "execution", "outcome"]


def join_lifecycle(
    *,
    opportunities: list[dict[str, Any]],
    assessments: list[dict[str, Any]] | None = None,
    rankings: list[dict[str, Any]] | None = None,
    shadow_comparisons: list[dict[str, Any]] | None = None,
    decisions: list[dict[str, Any]] | None = None,
    executions: list[dict[str, Any]] | None = None,
    trade_truths: list[dict[str, Any]] | None = None,
) -> list[LifecycleRecord]:
    """
    Join all intelligence datasets into unified lifecycle records.

    Starts from Opportunities (the broadest dataset) and joins downstream
    stages using available identity keys.

    Args:
        opportunities: All opportunity records
        assessments: Assessment records (joined via opportunity_id)
        rankings: Portfolio ranking records (joined via cycle_id + candidate matching)
        shadow_comparisons: Shadow comparison records (joined via cycle_id)
        decisions: Decision ledger records (joined via entity_id + cycle_id)
        executions: Execution result records (joined via correlation_id or decision_id)
        trade_truths: Trade truth records (joined via correlation_id)

    Returns:
        List of LifecycleRecord, one per opportunity.
    """
    # Build indexes for efficient lookup
    _assessment_idx = _index_by_key(assessments or [], "opportunity_id")
    _ranking_idx = _index_rankings_by_cycle(rankings or [])
    _shadow_idx = _index_by_key(shadow_comparisons or [], "cycle_id", key_transform=int)
    _decision_idx = _index_decisions(decisions or [])
    _execution_idx = _index_by_key(executions or [], "correlation_id")
    _truth_idx = _index_by_key(trade_truths or [], _truth_correlation_key)

    results: list[LifecycleRecord] = []

    for opp in opportunities:
        record = _build_lifecycle_record(
            opp=opp,
            assessment_idx=_assessment_idx,
            ranking_idx=_ranking_idx,
            shadow_idx=_shadow_idx,
            decision_idx=_decision_idx,
            execution_idx=_execution_idx,
            truth_idx=_truth_idx,
        )
        results.append(record)

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _build_lifecycle_record(
    *,
    opp: dict[str, Any],
    assessment_idx: dict[str, list[dict]],
    ranking_idx: dict[int, list[dict]],
    shadow_idx: dict[Any, list[dict]],
    decision_idx: dict[str, list[dict]],
    execution_idx: dict[str, list[dict]],
    truth_idx: dict[str, list[dict]],
) -> LifecycleRecord:
    """Build one lifecycle record from an opportunity and indexed downstream data."""

    opp_id = opp.get("opportunity_id", "")
    symbol = opp.get("symbol", "")
    cycle_id = int(opp.get("cycle_id", 0))
    entity_id = opp.get("entity_id", "")

    record = LifecycleRecord(
        opportunity_id=opp_id,
        symbol=symbol,
        cycle_id=cycle_id,
        entity_id=entity_id,
        opportunity=opp,
    )

    stages_present = ["opportunity"]
    stages_missing = []
    join_keys_used = []
    duplicates = 0

    # ─── JOIN: Assessment (via opportunity_id) ────────────────────────
    assessments = assessment_idx.get(opp_id, [])
    if assessments:
        record.assessment = assessments[0]
        stages_present.append("assessment")
        join_keys_used.append(f"opportunity_id={opp_id}")
        if len(assessments) > 1:
            duplicates += len(assessments) - 1
    else:
        stages_missing.append("assessment")

    # ─── JOIN: Ranking (via cycle_id + symbol match in candidates) ────
    cycle_rankings = ranking_idx.get(cycle_id, [])
    candidate_entry = None
    for ranking in cycle_rankings:
        for cand in ranking.get("candidates", []):
            if cand.get("symbol") == symbol and cand.get("opportunity_id", "").startswith(symbol):
                candidate_entry = cand
                break
        if candidate_entry:
            break

    if candidate_entry:
        record.ranking = candidate_entry
        stages_present.append("ranking")
        join_keys_used.append(f"cycle_id={cycle_id}+symbol={symbol}")
    else:
        stages_missing.append("ranking")

    # ─── JOIN: Shadow Comparison (via cycle_id) ───────────────────────
    shadows = shadow_idx.get(cycle_id, [])
    if shadows:
        record.shadow = shadows[0]
        # Shadow is cycle-level, not per-opportunity — always present if cycle had candidates
    # Shadow is optional — doesn't count as missing stage

    # ─── JOIN: Decision (via entity_id + cycle_id) ────────────────────
    decision_key = f"{entity_id}_{cycle_id}"
    dec_matches = decision_idx.get(decision_key, [])
    if not dec_matches:
        # Fallback: try entity_id alone
        dec_matches = decision_idx.get(entity_id, [])
    if dec_matches:
        record.decision = dec_matches[0]
        stages_present.append("decision")
        join_keys_used.append(f"entity_id={entity_id}")
        if len(dec_matches) > 1:
            duplicates += len(dec_matches) - 1
    else:
        stages_missing.append("decision")

    # ─── JOIN: Execution (via correlation_id from decision) ───────────
    correlation_id = ""
    if record.decision:
        correlation_id = record.decision.get("correlation_id", "")

    if correlation_id:
        exec_matches = execution_idx.get(correlation_id, [])
        if exec_matches:
            record.execution = exec_matches[0]
            stages_present.append("execution")
            join_keys_used.append(f"correlation_id={correlation_id}")
            if len(exec_matches) > 1:
                duplicates += len(exec_matches) - 1
        else:
            stages_missing.append("execution")
    else:
        stages_missing.append("execution")

    # ─── JOIN: Trade Truth (via correlation_id) ───────────────────────
    if correlation_id:
        truth_matches = truth_idx.get(correlation_id, [])
        if truth_matches:
            record.outcome = truth_matches[0]
            stages_present.append("outcome")
            join_keys_used.append(f"correlation_id={correlation_id}")

            # Extract outcome metrics
            outcome_section = truth_matches[0].get("outcome", {})
            record.r_multiple = outcome_section.get("r_multiple_realised")
            record.pnl = outcome_section.get("pnl_realised")
        else:
            stages_missing.append("outcome")
    else:
        stages_missing.append("outcome")

    # ─── DETERMINE FINAL STATE ────────────────────────────────────────
    opp_state = opp.get("state", "")
    if opp_state == "EXECUTED" or record.outcome is not None:
        record.final_state = "EXECUTED"
    elif opp_state == "REJECTED":
        record.final_state = "REJECTED"
        record.rejection_reason = opp.get("rejection_reason", "")
    elif opp_state == "EXPIRED":
        record.final_state = "EXPIRED"
    else:
        record.final_state = "UNKNOWN"

    # ─── QUALITY ASSESSMENT ───────────────────────────────────────────
    completeness = len(stages_present) / len(_ALL_STAGES)
    orphan_type = ""
    if "assessment" in stages_missing and "decision" in stages_missing:
        orphan_type = "opportunity_without_assessment"
    elif "execution" in stages_missing and "outcome" in stages_missing and record.final_state == "EXECUTED":
        orphan_type = "executed_without_evidence"

    record.quality = LifecycleQuality(
        stages_present=stages_present,
        stages_missing=stages_missing,
        completeness=round(completeness, 4),
        orphan_type=orphan_type,
        duplicate_count=duplicates,
        join_path=" → ".join(join_keys_used),
    )

    return record


def _index_by_key(
    records: list[dict[str, Any]],
    key: str | Any,
    key_transform: Any = None,
) -> dict[Any, list[dict]]:
    """Build a lookup index from records by a specified key."""
    idx: dict[Any, list[dict]] = {}
    for r in records:
        if callable(key):
            k = key(r)
        else:
            k = r.get(key, "")
        if key_transform and k:
            try:
                k = key_transform(k)
            except (ValueError, TypeError):
                continue
        if k:
            idx.setdefault(k, []).append(r)
    return idx


def _index_rankings_by_cycle(rankings: list[dict[str, Any]]) -> dict[int, list[dict]]:
    """Index ranking records by cycle_id."""
    idx: dict[int, list[dict]] = {}
    for r in rankings:
        cycle = int(r.get("cycle_id", 0))
        if cycle:
            idx.setdefault(cycle, []).append(r)
    return idx


def _index_decisions(decisions: list[dict[str, Any]]) -> dict[str, list[dict]]:
    """Index decisions by entity_id and composite entity_id+cycle_id."""
    idx: dict[str, list[dict]] = {}
    for d in decisions:
        entity_id = d.get("entity_id", "")
        cycle_id = d.get("cycle_id", 0)
        if entity_id:
            idx.setdefault(entity_id, []).append(d)
            composite = f"{entity_id}_{cycle_id}"
            idx.setdefault(composite, []).append(d)
    return idx


def _truth_correlation_key(record: dict[str, Any]) -> str:
    """Extract correlation_id from trade_truth (nested in identity section)."""
    identity = record.get("identity", {})
    return identity.get("correlation_id", "") or record.get("correlation_id", "")
