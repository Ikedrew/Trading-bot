"""
Opportunity Ranking Engine — Continuous priority ordering of trade candidates.

Ranks all evaluated candidates within a cycle by adjusted EV,
then selects the highest-ranked viable opportunity for execution.

This layer is an OVERLAY. It does NOT replace:
    - EV calculation
    - RR thresholds
    - Market state engine
    - Scoring system
    - Execution policy gates

It ONLY adds:
    - Rank scoring (EV adjusted by market state)
    - Sorting of candidates
    - Top-K selection logic
    - Comparative logging

Design: deterministic, no learning, no adaptation.
Principle: "Execute the highest-quality viable opportunity, not just any valid one."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.pipeline.market_state_engine import MarketState


# ─── MARKET STATE RANK MULTIPLIERS ───────────────────────────────────────────
# Adjusts raw EV into a rank_score that accounts for environment quality.

_RANK_MULTIPLIERS = {
    MarketState.STRUCTURED: 1.0,       # No penalty — full EV preserved
    MarketState.TRANSITIONAL: 0.65,    # Moderate penalty — environment uncertain
    MarketState.CHOP: 0.15,            # Severe penalty — nearly excluded
}


# ─── DATA STRUCTURES ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RankedCandidate:
    """A single trade candidate with rank information."""
    symbol: str
    pattern: str
    strategy: str                  # A_CONTINUATION / B_REVERSAL / C_FALSE_BREAK
    strategy_confidence: float
    score_neutral: float
    score_strategy: float
    ev: float
    rr_effective: float
    market_state: str
    rank_score: float              # EV × market_state_multiplier
    rank_position: int             # 1 = best, 2 = second, etc.
    eligible: bool                 # Passed all gates?
    block_reason: str | None       # Why not eligible (if blocked)
    selection_status: str          # "SELECTED" / "OUTRANKED" / "BLOCKED"


@dataclass
class OpportunityPool:
    """Collection of all candidates evaluated in a single cycle."""
    cycle_id: int
    candidates: list[RankedCandidate] = field(default_factory=list)
    selected: RankedCandidate | None = None

    @property
    def total_candidates(self) -> int:
        return len(self.candidates)

    @property
    def eligible_count(self) -> int:
        return sum(1 for c in self.candidates if c.eligible)

    def to_log_dict(self) -> dict[str, Any]:
        """Produce structured log output for S3 persistence."""
        return {
            "cycle_id": self.cycle_id,
            "total_candidates": self.total_candidates,
            "eligible_count": self.eligible_count,
            "selected_symbol": self.selected.symbol if self.selected else None,
            "selected_rank_score": self.selected.rank_score if self.selected else None,
            "candidates": [
                {
                    "symbol": c.symbol,
                    "pattern": c.pattern,
                    "strategy": c.strategy,
                    "ev": c.ev,
                    "rank_score": c.rank_score,
                    "rank_position": c.rank_position,
                    "eligible": c.eligible,
                    "selection_status": c.selection_status,
                    "block_reason": c.block_reason,
                }
                for c in self.candidates
            ],
        }


# ─── RANKING ENGINE ───────────────────────────────────────────────────────────

def compute_rank_score(ev: float, market_state: MarketState) -> float:
    """
    Compute rank score from EV adjusted by market state multiplier.

    Args:
        ev: Raw expected value from EV engine
        market_state: Current market stability classification

    Returns:
        rank_score (higher = better opportunity)
    """
    multiplier = _RANK_MULTIPLIERS.get(market_state, 0.5)
    return round(ev * multiplier, 8)


def rank_candidates(candidates: list[dict[str, Any]]) -> OpportunityPool:
    """
    Rank all trade candidates from a single cycle.

    Each candidate dict must contain:
        symbol, pattern, strategy, strategy_confidence,
        score_neutral, score_strategy, ev, ev_positive,
        rr_effective, market_state, policy_trade_allowed,
        block_reason (or None)

    Args:
        candidates: List of engine output dicts (one per symbol evaluated)

    Returns:
        OpportunityPool with sorted, ranked candidates and selection
    """
    if not candidates:
        return OpportunityPool(cycle_id=0)

    cycle_id = candidates[0].get("cycle_id", 0) if candidates else 0

    # Build ranked entries
    ranked: list[RankedCandidate] = []
    for c in candidates:
        market_state_str = c.get("market_state", "TRANSITIONAL")
        try:
            ms = MarketState(market_state_str)
        except (ValueError, KeyError):
            ms = MarketState.TRANSITIONAL

        ev = c.get("ev", 0.0) or 0.0
        rank_score = compute_rank_score(ev, ms)
        eligible = bool(c.get("policy_trade_allowed", False)) and c.get("action") == "EXECUTE"
        block_reason = c.get("block_reason") or c.get("reason") if not eligible else None

        ranked.append(RankedCandidate(
            symbol=c.get("symbol", "?"),
            pattern=c.get("pattern", "?"),
            strategy=c.get("strategy", "?"),
            strategy_confidence=c.get("strategy_confidence", 0.0),
            score_neutral=c.get("score_neutral", 0.0),
            score_strategy=c.get("score_strategy", 0.0),
            ev=ev,
            rr_effective=c.get("rr_effective", 0.0),
            market_state=market_state_str,
            rank_score=rank_score,
            rank_position=0,  # Set after sorting
            eligible=eligible,
            block_reason=block_reason,
            selection_status="PENDING",
        ))

    # Sort by rank_score descending (highest = best)
    ranked.sort(key=lambda x: x.rank_score, reverse=True)

    # Assign positions and selection status
    final: list[RankedCandidate] = []
    selected: RankedCandidate | None = None

    for i, candidate in enumerate(ranked):
        position = i + 1

        if candidate.eligible and selected is None:
            status = "SELECTED"
            selected_candidate = RankedCandidate(
                symbol=candidate.symbol,
                pattern=candidate.pattern,
                strategy=candidate.strategy,
                strategy_confidence=candidate.strategy_confidence,
                score_neutral=candidate.score_neutral,
                score_strategy=candidate.score_strategy,
                ev=candidate.ev,
                rr_effective=candidate.rr_effective,
                market_state=candidate.market_state,
                rank_score=candidate.rank_score,
                rank_position=position,
                eligible=True,
                block_reason=None,
                selection_status="SELECTED",
            )
            selected = selected_candidate
            final.append(selected_candidate)
        elif candidate.eligible:
            final.append(RankedCandidate(
                symbol=candidate.symbol,
                pattern=candidate.pattern,
                strategy=candidate.strategy,
                strategy_confidence=candidate.strategy_confidence,
                score_neutral=candidate.score_neutral,
                score_strategy=candidate.score_strategy,
                ev=candidate.ev,
                rr_effective=candidate.rr_effective,
                market_state=candidate.market_state,
                rank_score=candidate.rank_score,
                rank_position=position,
                eligible=True,
                block_reason=None,
                selection_status="OUTRANKED",
            ))
        else:
            final.append(RankedCandidate(
                symbol=candidate.symbol,
                pattern=candidate.pattern,
                strategy=candidate.strategy,
                strategy_confidence=candidate.strategy_confidence,
                score_neutral=candidate.score_neutral,
                score_strategy=candidate.score_strategy,
                ev=candidate.ev,
                rr_effective=candidate.rr_effective,
                market_state=candidate.market_state,
                rank_score=candidate.rank_score,
                rank_position=position,
                eligible=False,
                block_reason=candidate.block_reason,
                selection_status="BLOCKED",
            ))

    pool = OpportunityPool(cycle_id=cycle_id, candidates=final, selected=selected)
    return pool


def format_ranking_narrative(pool: OpportunityPool) -> str:
    """
    Format opportunity pool into human-readable ranking summary.

    Passive, read-only. Does not influence execution.
    """
    lines: list[str] = []
    lines.append("🏆 OPPORTUNITY RANKING")
    lines.append(f"  Candidates: {pool.total_candidates} | Eligible: {pool.eligible_count}")

    if pool.selected:
        lines.append(f"  Selected:   #{pool.selected.rank_position} {pool.selected.symbol} "
                     f"({pool.selected.strategy}) rank_score={pool.selected.rank_score:.8f}")
    else:
        lines.append(f"  Selected:   NONE (no eligible candidates)")

    lines.append("")

    if pool.candidates:
        lines.append("  Rank | Symbol       | Strategy        | EV          | Rank Score  | Status")
        lines.append("  " + "─" * 75)
        for c in pool.candidates[:10]:  # Show top 10 max
            _status_icon = {"SELECTED": "✔", "OUTRANKED": "○", "BLOCKED": "✖"}[c.selection_status]
            lines.append(
                f"  {c.rank_position:4d} | {c.symbol:12s} | {c.strategy:15s} | "
                f"{c.ev:+.6f} | {c.rank_score:.8f} | {_status_icon} {c.selection_status}"
            )
            if c.block_reason:
                lines.append(f"       |              |                 |             |             |   → {c.block_reason[:50]}")
        lines.append("")

    return "\n".join(lines)
