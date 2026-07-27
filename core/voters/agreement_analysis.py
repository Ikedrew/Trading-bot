"""
Agreement Analysis Module — Phase 3 System Intelligence.

Purely observational. Measures how often voters agree or disagree
with each other and with the final confluence decision.

NEVER modifies voter logic, weights, confluence, or execution.
NEVER affects production behaviour.

Ownership: core/voters/agreement_analysis.py
Mutability: NONE (pure functions)
Dependencies: VoteResult, ConfluenceDecision only
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Literal

from core.voters.types import VoteResult
from core.voters.confluence_engine import ConfluenceDecision

logger = logging.getLogger(__name__)

# Voter names (canonical order)
VOTER_NAMES = ("bias", "structure", "session", "spread", "volatility")


@dataclass(frozen=True)
class AgreementAnalysis:
    """
    Per-decision agreement metrics.

    agreement_matrix: pairwise agreement (True = same sign, False = opposing)
    confluence_agreement_score: fraction of voters aligned with final decision (0.0–1.0)
    stability_flag: "stable" / "mixed" / "unstable"
    dominant_voters: voters most aligned with confluence direction
    conflicting_voters: voters opposing confluence direction
    """

    agreement_matrix: dict[str, bool]
    confluence_agreement_score: float
    stability_flag: Literal["stable", "mixed", "unstable"]
    dominant_voters: list[str]
    conflicting_voters: list[str]


def _sign(score: float) -> int:
    """Return directional sign: +1, -1, or 0."""
    if score > 0.05:
        return 1
    elif score < -0.05:
        return -1
    return 0


def _pair_key(a: str, b: str) -> str:
    """Canonical pair key (alphabetical order)."""
    return f"{min(a, b)}_vs_{max(a, b)}"


def compute_agreement(
    *,
    bias_vote: VoteResult,
    structure_vote: VoteResult,
    session_vote: VoteResult,
    spread_vote: VoteResult,
    volatility_vote: VoteResult,
    confluence: ConfluenceDecision,
) -> AgreementAnalysis:
    """
    Compute agreement metrics for a single decision cycle.

    Pure function: no state, no side effects, deterministic.

    Args:
        All 5 voter results + final confluence decision.

    Returns:
        AgreementAnalysis with pairwise matrix, score, stability, dominants.
    """
    votes = {
        "bias": bias_vote,
        "structure": structure_vote,
        "session": session_vote,
        "spread": spread_vote,
        "volatility": volatility_vote,
    }

    signs = {name: _sign(vote.score) for name, vote in votes.items()}
    confluence_sign = _sign(confluence.score)

    # 1. Pairwise agreement matrix
    agreement_matrix: dict[str, bool] = {}
    names = list(VOTER_NAMES)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            key = _pair_key(a, b)
            # Agreement: same sign (both positive, both negative, or both neutral)
            agreement_matrix[key] = (signs[a] == signs[b])

    # 2. Confluence agreement score
    aligned_count = sum(1 for s in signs.values() if s == confluence_sign)
    total = len(signs)
    confluence_agreement_score = aligned_count / total if total > 0 else 0.0

    # 3. Stability classification
    pairwise_agreements = sum(1 for v in agreement_matrix.values() if v)
    total_pairs = len(agreement_matrix)
    pairwise_rate = pairwise_agreements / total_pairs if total_pairs > 0 else 0.0

    if pairwise_rate >= 0.8:
        stability_flag: Literal["stable", "mixed", "unstable"] = "stable"
    elif pairwise_rate >= 0.5:
        stability_flag = "mixed"
    else:
        stability_flag = "unstable"

    # 4. Dominant and conflicting voters
    dominant_voters = [name for name, s in signs.items() if s == confluence_sign and s != 0]
    conflicting_voters = [name for name, s in signs.items() if s != 0 and s != confluence_sign]

    return AgreementAnalysis(
        agreement_matrix=agreement_matrix,
        confluence_agreement_score=round(confluence_agreement_score, 3),
        stability_flag=stability_flag,
        dominant_voters=dominant_voters,
        conflicting_voters=conflicting_voters,
    )


def emit_agreement_log(symbol: str, analysis: AgreementAnalysis) -> None:
    """
    Emit structured agreement log. Never raises.
    """
    try:
        pairs_agreed = sum(1 for v in analysis.agreement_matrix.values() if v)
        total_pairs = len(analysis.agreement_matrix)
        logger.debug(
            "[AGREEMENT] symbol=%s score=%.2f stability=%s "
            "pairs_agreed=%d/%d dominant=%s conflicting=%s",
            symbol,
            analysis.confluence_agreement_score,
            analysis.stability_flag,
            pairs_agreed, total_pairs,
            ",".join(analysis.dominant_voters) or "none",
            ",".join(analysis.conflicting_voters) or "none",
        )
    except Exception:
        pass
