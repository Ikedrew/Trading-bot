"""
Voter Influence + Reliability Tracker — Phase 3 Step 3.

Measures how much each voter influences decisions and how reliable they are over time.
Purely observational. NEVER modifies voter logic, weights, confluence, or execution.

Ownership: core/voters/influence_tracker.py
Mutability: Rolling history (internal only, never affects decisions)
Dependencies: VoteResult, ConfluenceDecision only
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from core.voters.types import VoteResult
from core.voters.confluence_engine import ConfluenceDecision

logger = logging.getLogger(__name__)

VOTER_NAMES = ("bias", "structure", "session", "spread", "volatility")
_ROLLING_WINDOW = 100  # Track last N decisions for reliability


@dataclass(frozen=True)
class VoterInfluenceSnapshot:
    """Per-decision influence metrics for all voters."""
    influence_map: dict[str, float]          # voter → signed influence score
    dominant_influencers: list[str]          # top 1-2 aligned voters
    weakest_voters: list[str]               # lowest magnitude voters


@dataclass(frozen=True)
class VoterReliabilitySnapshot:
    """Rolling reliability metrics for all voters."""
    reliability_scores: dict[str, float]     # voter → 0.0-1.0
    consistency_scores: dict[str, float]     # voter → 0.0-1.0 (low variance = high)
    classifications: dict[str, str]          # voter → "high_reliability" / "moderate" / "low"


def _sign(score: float) -> int:
    if score > 0.05:
        return 1
    elif score < -0.05:
        return -1
    return 0


def compute_influence(
    *,
    bias_vote: VoteResult,
    structure_vote: VoteResult,
    session_vote: VoteResult,
    spread_vote: VoteResult,
    volatility_vote: VoteResult,
    confluence: ConfluenceDecision,
) -> VoterInfluenceSnapshot:
    """
    Compute per-decision influence for each voter.
    Influence = alignment with confluence direction × confidence × score magnitude.
    """
    votes = {
        "bias": bias_vote,
        "structure": structure_vote,
        "session": session_vote,
        "spread": spread_vote,
        "volatility": volatility_vote,
    }

    confluence_sign = _sign(confluence.score)
    influence_map: dict[str, float] = {}

    for name, vote in votes.items():
        voter_sign = _sign(vote.score)
        # Influence: positive if aligned with confluence, negative if opposing
        if confluence_sign == 0:
            influence = 0.0
        elif voter_sign == confluence_sign:
            influence = abs(vote.score) * vote.confidence
        elif voter_sign == -confluence_sign:
            influence = -abs(vote.score) * vote.confidence
        else:
            influence = 0.0
        influence_map[name] = round(influence, 4)

    # Dominant: top 2 positive influence
    sorted_pos = sorted(
        [(n, s) for n, s in influence_map.items() if s > 0],
        key=lambda x: x[1], reverse=True,
    )
    dominant = [n for n, _ in sorted_pos[:2]]

    # Weakest: lowest magnitude
    sorted_mag = sorted(influence_map.items(), key=lambda x: abs(x[1]))
    weakest = [n for n, _ in sorted_mag[:2]]

    return VoterInfluenceSnapshot(
        influence_map=influence_map,
        dominant_influencers=dominant,
        weakest_voters=weakest,
    )


class VoterReliabilityTracker:
    """
    Rolling reliability tracker. Accumulates alignment history per voter.
    Single-threaded, process-lifetime. Reset between sessions if needed.
    """

    def __init__(self) -> None:
        self._history: dict[str, deque[bool]] = {
            name: deque(maxlen=_ROLLING_WINDOW) for name in VOTER_NAMES
        }

    def record(
        self,
        *,
        bias_vote: VoteResult,
        structure_vote: VoteResult,
        session_vote: VoteResult,
        spread_vote: VoteResult,
        volatility_vote: VoteResult,
        confluence: ConfluenceDecision,
    ) -> None:
        """Record alignment for this decision cycle."""
        votes = {
            "bias": bias_vote,
            "structure": structure_vote,
            "session": session_vote,
            "spread": spread_vote,
            "volatility": volatility_vote,
        }
        confluence_sign = _sign(confluence.score)
        for name, vote in votes.items():
            aligned = (_sign(vote.score) == confluence_sign) if confluence_sign != 0 else True
            self._history[name].append(aligned)

    def get_snapshot(self) -> VoterReliabilitySnapshot:
        """Compute current reliability metrics from rolling history."""
        reliability: dict[str, float] = {}
        consistency: dict[str, float] = {}
        classifications: dict[str, str] = {}

        for name in VOTER_NAMES:
            history = self._history[name]
            if not history:
                reliability[name] = 0.5
                consistency[name] = 0.5
                classifications[name] = "moderate_reliability"
                continue

            # Reliability: fraction of aligned decisions
            aligned_count = sum(1 for h in history if h)
            rel = aligned_count / len(history)
            reliability[name] = round(rel, 3)

            # Consistency: 1 - variance proxy (how stable is alignment)
            # Low variance = high consistency
            if len(history) >= 5:
                recent = list(history)[-20:]
                recent_rate = sum(1 for h in recent if h) / len(recent) if recent else 0.5
                variance_proxy = abs(rel - recent_rate)
                consistency[name] = round(max(0.0, 1.0 - variance_proxy * 4), 3)
            else:
                consistency[name] = 0.5

            # Classification
            if rel >= 0.75:
                classifications[name] = "high_reliability"
            elif rel >= 0.5:
                classifications[name] = "moderate_reliability"
            else:
                classifications[name] = "low_reliability"

            # Append stability qualifier
            if consistency[name] >= 0.7:
                classifications[name] += "_stable"
            else:
                classifications[name] += "_volatile"

        return VoterReliabilitySnapshot(
            reliability_scores=reliability,
            consistency_scores=consistency,
            classifications=classifications,
        )

    def reset(self) -> None:
        """Reset all history (between sessions)."""
        for name in VOTER_NAMES:
            self._history[name].clear()


# Module-level singleton
voter_reliability_tracker = VoterReliabilityTracker()


def emit_influence_log(symbol: str, influence: VoterInfluenceSnapshot, reliability: VoterReliabilitySnapshot) -> None:
    """Emit structured influence + reliability log. Never raises."""
    try:
        inf_parts = " ".join(f"{n}={s:+.2f}" for n, s in influence.influence_map.items())
        rel_parts = " ".join(
            f"{n}={reliability.reliability_scores.get(n, 0):.2f}"
            for n in VOTER_NAMES
        )
        logger.debug(
            "[INFLUENCE] symbol=%s %s dominant=%s weakest=%s",
            symbol, inf_parts,
            ",".join(influence.dominant_influencers) or "none",
            ",".join(influence.weakest_voters) or "none",
        )
        logger.debug(
            "[RELIABILITY] symbol=%s %s",
            symbol, rel_parts,
        )
    except Exception:
        pass
