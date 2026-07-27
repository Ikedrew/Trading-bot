"""
Conflict Classification System — Phase 3 Step 2.

Analyses WHY voters disagree in trading decisions.
Classifies conflict types, severity, and impact on final decision.

Purely observational. NEVER modifies voter logic, weights, confluence, or execution.

Ownership: core/voters/conflict_classification.py
Mutability: NONE (pure functions)
Dependencies: VoteResult, ConfluenceDecision only
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from core.voters.types import VoteResult
from core.voters.confluence_engine import ConfluenceDecision

logger = logging.getLogger(__name__)

# Threshold for "strong" directional signal
_STRONG_THRESHOLD = 0.5


@dataclass(frozen=True)
class ConflictAnalysis:
    """
    Per-decision conflict classification output.

    conflict_types: list of detected conflict categories
    severity: overall conflict severity
    impact: how much conflict degrades the decision
    conflict_map: which voters conflict with which
    primary_driver: dominant source of disagreement
    """

    conflict_types: list[str]
    severity: Literal["none", "low", "medium", "high"]
    impact: Literal["none", "minimal", "moderate", "strong"]
    conflict_map: dict[str, list[str]]
    primary_driver: str


def _sign(score: float) -> int:
    if score > 0.05:
        return 1
    elif score < -0.05:
        return -1
    return 0


def _is_strong(score: float) -> bool:
    return abs(score) >= _STRONG_THRESHOLD


def classify_conflicts(
    *,
    bias_vote: VoteResult,
    structure_vote: VoteResult,
    session_vote: VoteResult,
    spread_vote: VoteResult,
    volatility_vote: VoteResult,
    confluence: ConfluenceDecision,
) -> ConflictAnalysis:
    """
    Classify voter conflicts for a single decision cycle.

    Pure function: no state, no side effects, deterministic.
    """
    conflicts: list[str] = []
    conflict_map: dict[str, list[str]] = {}

    bias_s = _sign(bias_vote.score)
    struct_s = _sign(structure_vote.score)
    session_s = _sign(session_vote.score)
    spread_s = _sign(spread_vote.score)
    vol_s = _sign(volatility_vote.score)

    # A. Bias vs Structure Conflict
    if bias_s != 0 and struct_s != 0 and bias_s != struct_s:
        conflicts.append("bias_vs_structure")
        conflict_map.setdefault("bias", []).append("structure")
        conflict_map.setdefault("structure", []).append("bias")

    # B. Volatility vs Structure Conflict
    if vol_s != 0 and struct_s != 0 and vol_s != struct_s:
        conflicts.append("volatility_vs_structure")
        conflict_map.setdefault("volatility", []).append("structure")
        conflict_map.setdefault("structure", []).append("volatility")

    # C. Spread vs Direction Conflict
    # Spread negative but directional voters positive (or vice versa)
    directional_consensus = bias_s if bias_s != 0 else struct_s
    if spread_s < 0 and directional_consensus > 0:
        conflicts.append("spread_vs_direction")
        conflict_map.setdefault("spread", []).append("bias")
    elif spread_s < 0 and directional_consensus < 0:
        conflicts.append("spread_vs_direction")
        conflict_map.setdefault("spread", []).append("bias")

    # D. Session Misalignment
    if session_s < 0 and (bias_s > 0 or struct_s > 0):
        conflicts.append("session_misalignment")
        conflict_map.setdefault("session", []).append("bias" if bias_s > 0 else "structure")

    # E. Multi-Voter Fragmentation (3+ voters disagree with each other)
    signs = [bias_s, struct_s, session_s, spread_s, vol_s]
    positive = sum(1 for s in signs if s > 0)
    negative = sum(1 for s in signs if s < 0)
    neutral = sum(1 for s in signs if s == 0)
    if positive >= 2 and negative >= 2:
        conflicts.append("multi_voter_fragmentation")

    # Severity scoring
    if len(conflicts) == 0:
        severity: Literal["none", "low", "medium", "high"] = "none"
    elif len(conflicts) == 1:
        # Check if strong signals involved
        if _is_strong(bias_vote.score) and _is_strong(structure_vote.score) and "bias_vs_structure" in conflicts:
            severity = "high"
        else:
            severity = "low"
    elif len(conflicts) == 2:
        severity = "medium"
    else:
        severity = "high"

    # Impact classification
    if severity == "none":
        impact: Literal["none", "minimal", "moderate", "strong"] = "none"
    elif severity == "low":
        impact = "minimal"
    elif severity == "medium":
        impact = "moderate"
    else:
        # High severity: check if confluence score is borderline
        if abs(confluence.score) < 0.5:
            impact = "strong"
        else:
            impact = "moderate"

    # Primary driver: voter with most conflicts
    driver_counts = {k: len(v) for k, v in conflict_map.items()}
    primary_driver = max(driver_counts, key=driver_counts.get) if driver_counts else "none"

    return ConflictAnalysis(
        conflict_types=conflicts,
        severity=severity,
        impact=impact,
        conflict_map=conflict_map,
        primary_driver=primary_driver,
    )


def emit_conflict_log(symbol: str, analysis: ConflictAnalysis) -> None:
    """Emit structured conflict log. Never raises."""
    try:
        if analysis.severity == "none":
            return  # No conflicts — skip log
        logger.debug(
            "[CONFLICT] symbol=%s types=%s severity=%s impact=%s primary_driver=%s",
            symbol,
            analysis.conflict_types,
            analysis.severity,
            analysis.impact,
            analysis.primary_driver,
        )
    except Exception:
        pass
