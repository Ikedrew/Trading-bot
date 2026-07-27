"""
Cross-Layer Intelligence Synthesis — Phase 3 Step 4.

Synthesizes outputs from Agreement, Conflict, Influence, and Reliability
into a unified "system state understanding".

Purely observational. NEVER modifies decisions or voter behaviour.

Ownership: core/voters/system_synthesis.py
Mutability: Rolling stability trend (internal only)
Dependencies: AgreementAnalysis, ConflictAnalysis, VoterInfluenceSnapshot, VoterReliabilitySnapshot
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from core.voters.agreement_analysis import AgreementAnalysis
from core.voters.conflict_classification import ConflictAnalysis
from core.voters.influence_tracker import VoterInfluenceSnapshot, VoterReliabilitySnapshot, VOTER_NAMES

logger = logging.getLogger(__name__)

_STABILITY_WINDOW = 20  # Rolling window for trend detection


@dataclass(frozen=True)
class SystemSynthesis:
    """
    Unified system state understanding for a single decision cycle.
    """
    system_state: Literal["coherent", "tensioned", "unstable", "degenerate"]
    decision_integrity_score: float  # 0.0–1.0
    dominance_profile: str           # e.g. "bias-dominant", "balanced"
    stability_trend: Literal["improving", "degrading", "stable"]
    notes: list[str]


def _compute_system_state(
    agreement: AgreementAnalysis,
    conflict: ConflictAnalysis,
    reliability: VoterReliabilitySnapshot,
) -> Literal["coherent", "tensioned", "unstable", "degenerate"]:
    """Classify overall system state from sub-metrics."""
    high_agreement = agreement.confluence_agreement_score >= 0.7
    low_conflict = conflict.severity in ("none", "low")
    high_reliability = all(
        reliability.reliability_scores.get(n, 0) >= 0.6 for n in VOTER_NAMES
    )

    if high_agreement and low_conflict and high_reliability:
        return "coherent"
    elif agreement.confluence_agreement_score >= 0.5 and conflict.severity in ("none", "low", "medium"):
        return "tensioned"
    elif conflict.severity == "high" or agreement.confluence_agreement_score < 0.4:
        return "unstable"
    else:
        # Fragmented: no dominant cluster + low reliability
        avg_rel = sum(reliability.reliability_scores.values()) / max(len(reliability.reliability_scores), 1)
        if avg_rel < 0.5 and agreement.confluence_agreement_score < 0.5:
            return "degenerate"
        return "unstable"


def _compute_integrity(
    agreement: AgreementAnalysis,
    conflict: ConflictAnalysis,
    reliability: VoterReliabilitySnapshot,
) -> float:
    """Compute decision integrity score (0.0–1.0)."""
    # Components
    agreement_component = agreement.confluence_agreement_score  # 0–1

    conflict_penalty = {"none": 0.0, "low": 0.05, "medium": 0.15, "high": 0.3}.get(conflict.severity, 0.0)

    avg_reliability = sum(reliability.reliability_scores.values()) / max(len(reliability.reliability_scores), 1)

    integrity = (agreement_component * 0.4 + avg_reliability * 0.4 + (1.0 - conflict_penalty) * 0.2)
    return round(max(0.0, min(1.0, integrity)), 3)


def _compute_dominance(influence: VoterInfluenceSnapshot) -> str:
    """Identify which voter domain dominates the decision."""
    if not influence.influence_map:
        return "balanced"

    sorted_inf = sorted(influence.influence_map.items(), key=lambda x: abs(x[1]), reverse=True)
    top = sorted_inf[0]
    second = sorted_inf[1] if len(sorted_inf) > 1 else ("none", 0.0)

    # If top voter has >2x the influence of second → dominant
    if abs(top[1]) > abs(second[1]) * 2 and abs(top[1]) > 0.2:
        return f"{top[0]}_dominant"

    # If top two are close → combined profile
    if abs(top[1]) > 0.15 and abs(second[1]) > 0.1:
        return f"{top[0]}_{second[0]}_balanced"

    return "balanced"


class StabilityTrendTracker:
    """Tracks system state over time to detect stability drift."""

    def __init__(self) -> None:
        self._history: deque[float] = deque(maxlen=_STABILITY_WINDOW)

    def record(self, integrity_score: float) -> None:
        self._history.append(integrity_score)

    def get_trend(self) -> Literal["improving", "degrading", "stable"]:
        if len(self._history) < 5:
            return "stable"

        recent = list(self._history)
        first_half = recent[:len(recent) // 2]
        second_half = recent[len(recent) // 2:]

        avg_first = sum(first_half) / len(first_half)
        avg_second = sum(second_half) / len(second_half)

        delta = avg_second - avg_first
        if delta > 0.05:
            return "improving"
        elif delta < -0.05:
            return "degrading"
        return "stable"

    def reset(self) -> None:
        self._history.clear()


# Module-level singleton
stability_tracker = StabilityTrendTracker()


def compute_synthesis(
    *,
    agreement: AgreementAnalysis,
    conflict: ConflictAnalysis,
    influence: VoterInfluenceSnapshot,
    reliability: VoterReliabilitySnapshot,
) -> SystemSynthesis:
    """
    Synthesize all intelligence layers into unified system state.
    Pure function for classification; uses stability_tracker for trend only.
    """
    system_state = _compute_system_state(agreement, conflict, reliability)
    integrity = _compute_integrity(agreement, conflict, reliability)
    dominance = _compute_dominance(influence)

    # Record for trend tracking
    stability_tracker.record(integrity)
    trend = stability_tracker.get_trend()

    # Notes
    notes: list[str] = []
    if system_state == "coherent":
        notes.append("high agreement across core voters")
    if conflict.severity == "high":
        notes.append(f"high conflict: {conflict.conflict_types}")
    if influence.dominant_influencers:
        notes.append(f"dominated by {','.join(influence.dominant_influencers)}")
    if any(v < 0.5 for v in reliability.reliability_scores.values()):
        weak = [n for n, v in reliability.reliability_scores.items() if v < 0.5]
        notes.append(f"low reliability: {','.join(weak)}")

    return SystemSynthesis(
        system_state=system_state,
        decision_integrity_score=integrity,
        dominance_profile=dominance,
        stability_trend=trend,
        notes=notes,
    )


def emit_synthesis_log(symbol: str, synthesis: SystemSynthesis) -> None:
    """Emit structured synthesis log. Never raises."""
    try:
        logger.debug(
            "[SYNTHESIS] symbol=%s state=%s integrity=%.3f dominance=%s trend=%s notes=%s",
            symbol,
            synthesis.system_state,
            synthesis.decision_integrity_score,
            synthesis.dominance_profile,
            synthesis.stability_trend,
            " | ".join(synthesis.notes) if synthesis.notes else "none",
        )
    except Exception:
        pass
