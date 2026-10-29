"""
Voter System — Shared types.

All voters return VoteResult. No voter may return trade decisions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VoteResult:
    """
    Immutable output from a voter.

    score: -2.0 to +2.0 (negative = against trade, positive = for trade)
    confidence: 0.0 to 1.0 (how certain the voter is)
    reason: human-readable explanation
    """

    score: float
    confidence: float
    reason: str
