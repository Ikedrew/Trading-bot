"""V10 Opportunity Ranking — Active pre-execution ranking of trade candidates.

Answers: "Out of everything happening in the market right now,
          which opportunity deserves risk allocation first?"

This is NOT a shadow/observation layer. It sits IN the execution path:
    Phase 1: Evaluate all symbols → collect EXECUTE candidates
    Phase 2: rank_for_execution() → sorted OpportunityScore list
    Phase 3: Execute top-ranked candidate(s) that pass risk/broker gates

Does NOT bypass:
    - Risk engine (daily loss, max positions, correlation)
    - Execution engine (spread, broker, margin)
    - Guard chain (runtime safety checks)

ONLY decides: "Which opportunity gets attention first?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════
# SCORING WEIGHTS
# ═══════════════════════════════════════════════════════════════

WEIGHT_OPPORTUNITY_QUALITY = 0.40
WEIGHT_STRATEGY_CONFIDENCE = 0.20
WEIGHT_HTF_ALIGNMENT = 0.20
WEIGHT_SESSION_QUALITY = 0.10
WEIGHT_RISK_QUALITY = 0.10


# ═══════════════════════════════════════════════════════════════
# OPPORTUNITY SCORE MODEL
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class OpportunityScore:
    """Ranked score for a single execution candidate."""

    # Identity
    opportunity_id: str = ""
    symbol: str = ""
    direction: str = ""

    # Strategy context
    strategy_family: str = ""
    strategy_confidence: float = 0.0

    # Component scores (0.0-1.0 each)
    opportunity_quality: float = 0.0
    htf_alignment: float = 0.0
    session_quality: float = 0.0
    risk_quality: float = 0.0

    # Portfolio adjustment (from correlation/exposure context)
    portfolio_adjustment: float = 0.0

    # Final
    final_rank_score: float = 0.0
    rank_position: int = 0

    # Explainability
    ranking_reason: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "strategy_family": self.strategy_family,
            "strategy_confidence": round(self.strategy_confidence, 4),
            "opportunity_quality": round(self.opportunity_quality, 4),
            "htf_alignment": round(self.htf_alignment, 4),
            "session_quality": round(self.session_quality, 4),
            "risk_quality": round(self.risk_quality, 4),
            "portfolio_adjustment": round(self.portfolio_adjustment, 4),
            "final_rank_score": round(self.final_rank_score, 4),
            "rank_position": self.rank_position,
            "ranking_reason": list(self.ranking_reason),
        }


# ═══════════════════════════════════════════════════════════════
# EXECUTION CANDIDATE (collected from Phase 1)
# ═══════════════════════════════════════════════════════════════

@dataclass
class ExecutionCandidate:
    """
    All context needed to rank AND execute a trade candidate.

    Collected during Phase 1 (per-symbol evaluation).
    Consumed by Phase 2 (ranking) and Phase 3 (execution).
    """
    symbol: str = ""
    new_result: dict[str, Any] = field(default_factory=dict)
    pipeline_result: Any = None  # PipelineResult from V10
    exec_prep: Any = None  # Result of prepare_execution()
    sym_state: Any = None  # SymbolState reference
    bid: float = 0.0
    ask: float = 0.0
    closed_time: float = 0.0
    cycle_opportunities: list = field(default_factory=list)
    v10_obs_id: str = ""
    new_engine_htf: Any = None
    raw_patterns: list = field(default_factory=list)
    correlation_id: str = ""
    decision_id: str = ""
    engine_score: float = 0.0


# ═══════════════════════════════════════════════════════════════
# RANKING ENGINE
# ═══════════════════════════════════════════════════════════════

def rank_for_execution(
    candidates: list[ExecutionCandidate],
    portfolio_context: Any = None,
) -> list[OpportunityScore]:
    """
    Rank execution candidates from best to worst.

    This is the ACTIVE ranking function called BEFORE any trade is placed.

    Args:
        candidates: ExecutionCandidate objects collected during Phase 1
        portfolio_context: PortfolioContext from core.portfolio_ranking.context
                          (open positions, correlation, exposure)

    Returns:
        Sorted list of OpportunityScore (index 0 = highest ranked)
    """
    if not candidates:
        return []

    scores: list[OpportunityScore] = []

    for candidate in candidates:
        score = _compute_score(candidate, portfolio_context)
        scores.append(score)

    # Sort descending by final_rank_score
    scores.sort(key=lambda s: s.final_rank_score, reverse=True)

    # Assign rank positions
    ranked: list[OpportunityScore] = []
    for i, score in enumerate(scores):
        ranked.append(OpportunityScore(
            opportunity_id=score.opportunity_id,
            symbol=score.symbol,
            direction=score.direction,
            strategy_family=score.strategy_family,
            strategy_confidence=score.strategy_confidence,
            opportunity_quality=score.opportunity_quality,
            htf_alignment=score.htf_alignment,
            session_quality=score.session_quality,
            risk_quality=score.risk_quality,
            portfolio_adjustment=score.portfolio_adjustment,
            final_rank_score=score.final_rank_score,
            rank_position=i + 1,
            ranking_reason=score.ranking_reason,
        ))

    return ranked


def format_ranking_summary(scores: list[OpportunityScore]) -> str:
    """Format ranking results for logging."""
    if not scores:
        return "[RANKING] No execution candidates this cycle"

    lines = [f"[RANKING] {len(scores)} candidate(s):"]
    for s in scores:
        lines.append(
            f"  #{s.rank_position} {s.symbol} "
            f"score={s.final_rank_score:.4f} "
            f"(quality={s.opportunity_quality:.2f} "
            f"strat={s.strategy_confidence:.2f} "
            f"htf={s.htf_alignment:.2f} "
            f"rr={s.risk_quality:.2f})"
        )
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# INTERNAL SCORING
# ═══════════════════════════════════════════════════════════════

def _compute_score(candidate: ExecutionCandidate, portfolio_context: Any) -> OpportunityScore:
    """Compute OpportunityScore for one candidate."""
    reasons: list[str] = []
    nr = candidate.new_result
    pr = candidate.pipeline_result

    # ─── OPPORTUNITY QUALITY (40%) ────────────────────────────
    opp_quality = 0.0
    if pr is not None:
        try:
            opp_quality = pr.opportunity.quality.overall_quality
        except (AttributeError, TypeError):
            pass
    if opp_quality == 0:
        # Fallback: use engine score
        opp_quality = float(nr.get("score", 0.0) or 0.0)
    reasons.append(f"Opportunity quality: {opp_quality:.2f}")

    # ─── STRATEGY CONFIDENCE (20%) ────────────────────────────
    strat_conf = 0.0
    if pr is not None:
        try:
            strat_conf = pr.strategy.strategy_confidence
        except (AttributeError, TypeError):
            pass
    if strat_conf == 0:
        strat_conf = float(nr.get("strategy_confidence", 0.0) or 0.0)
    reasons.append(f"Strategy confidence: {strat_conf:.2f}")

    # ─── HTF ALIGNMENT (20%) ─────────────────────────────────
    htf_align = 0.0
    if pr is not None:
        try:
            htf_align = pr.market_state.htf_alignment.structure_alignment
        except (AttributeError, TypeError):
            pass
    if htf_align == 0:
        components = nr.get("components") or {}
        htf_align = float(components.get("htf_alignment", 0.0) or 0.0)
    reasons.append(f"HTF alignment: {htf_align:.2f}")

    # ─── SESSION QUALITY (10%) ────────────────────────────────
    session_q = _evaluate_session_quality(candidate)
    reasons.append(f"Session quality: {session_q:.2f}")

    # ─── RISK QUALITY (10%) ───────────────────────────────────
    risk_q = _evaluate_risk_quality(candidate, pr)
    reasons.append(f"Risk quality (R:R): {risk_q:.2f}")

    # ─── WEIGHTED COMPOSITE ───────────────────────────────────
    raw_score = (
        opp_quality * WEIGHT_OPPORTUNITY_QUALITY
        + strat_conf * WEIGHT_STRATEGY_CONFIDENCE
        + htf_align * WEIGHT_HTF_ALIGNMENT
        + session_q * WEIGHT_SESSION_QUALITY
        + risk_q * WEIGHT_RISK_QUALITY
    )

    # ─── PORTFOLIO ADJUSTMENT ─────────────────────────────────
    portfolio_adj = 0.0
    if portfolio_context is not None:
        portfolio_adj = _compute_portfolio_adjustment(
            candidate, portfolio_context, raw_score
        )
        if portfolio_adj != 0:
            reasons.append(f"Portfolio adjustment: {portfolio_adj:+.4f}")

    final_score = raw_score + portfolio_adj

    # ─── IDENTITY ─────────────────────────────────────────────
    opportunity_id = ""
    for opp in candidate.cycle_opportunities:
        if hasattr(opp, "opportunity_id"):
            opportunity_id = opp.opportunity_id
            break

    direction = nr.get("side", "") or ""
    strategy_family = nr.get("strategy", "") or ""

    return OpportunityScore(
        opportunity_id=opportunity_id,
        symbol=candidate.symbol,
        direction=direction,
        strategy_family=strategy_family,
        strategy_confidence=round(strat_conf, 4),
        opportunity_quality=round(opp_quality, 4),
        htf_alignment=round(htf_align, 4),
        session_quality=round(session_q, 4),
        risk_quality=round(risk_q, 4),
        portfolio_adjustment=round(portfolio_adj, 4),
        final_rank_score=round(final_score, 4),
        rank_position=0,  # Assigned after sort
        ranking_reason=reasons,
    )


def _evaluate_session_quality(candidate: ExecutionCandidate) -> float:
    """
    Score the trading session quality (0.0-1.0).

    London and NY overlap are best for FX.
    Asia is lower quality for most pairs.
    """
    from datetime import datetime, timezone

    try:
        hour = datetime.fromtimestamp(candidate.closed_time, tz=timezone.utc).hour
    except (ValueError, OSError, TypeError):
        return 0.5  # Unknown — neutral

    # London session (07-12 UTC): highest liquidity
    if 7 <= hour < 12:
        return 0.90
    # NY overlap (12-16 UTC): high liquidity
    if 12 <= hour < 16:
        return 0.85
    # Early NY (16-20 UTC): moderate
    if 16 <= hour < 20:
        return 0.60
    # Asia (0-7 UTC): lower for most pairs
    if 0 <= hour < 7:
        # Exception: JPY pairs benefit from Asia
        if "JPY" in candidate.symbol:
            return 0.75
        return 0.40
    # Off-hours
    return 0.30


def _evaluate_risk_quality(candidate: ExecutionCandidate, pipeline_result: Any) -> float:
    """
    Score risk geometry quality (0.0-1.0).

    Higher R:R = better risk quality.
    Normalised: 1.5 R:R → 0.5, 3.0 R:R → 1.0, below 1.0 → 0.0
    """
    expected_rr = 0.0
    if pipeline_result is not None:
        try:
            expected_rr = pipeline_result.entry.expected_rr
        except (AttributeError, TypeError):
            pass
    if expected_rr == 0:
        expected_rr = float(candidate.new_result.get("rr_effective", 0.0) or 0.0)

    if expected_rr <= 1.0:
        return 0.0
    if expected_rr >= 3.0:
        return 1.0
    # Linear interpolation between 1.0 and 3.0
    return round((expected_rr - 1.0) / 2.0, 4)


def _compute_portfolio_adjustment(
    candidate: ExecutionCandidate,
    portfolio_context: Any,
    raw_score: float,
) -> float:
    """
    Compute portfolio-aware adjustment using existing infrastructure.

    Penalises correlated exposure, rewards diversification.
    """
    try:
        from core.portfolio_ranking.context import enrich_candidate
        enrichment = enrich_candidate(
            symbol=candidate.symbol,
            direction=candidate.new_result.get("side", "") or "",
            rank_score=raw_score,
            portfolio_ctx=portfolio_context,
        )
        return enrichment.portfolio_adjustment
    except Exception:
        return 0.0
