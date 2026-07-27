"""
OpportunityAssessment — Frozen analytical snapshot at the analysis-policy boundary.

Captures the complete market understanding AFTER all evidence has been evaluated
and BEFORE any policy, risk, or execution decision is made.

CONTAINS ONLY:
    - Market analysis outputs (scores, classifications, components)
    - Pattern identity and quality
    - Strategy classification and confidence
    - Regime and market state assessment
    - HTF context summary
    - Placeholder sections for future subsystems (reasoning, uncertainty, evidence)

NEVER CONTAINS:
    - Policy decisions (trade_allowed, block_reason)
    - Risk calculations (SL, TP, volume, OrderIntent)
    - Execution results (fills, slippage, broker state)
    - Account state (equity, drawdown, positions)
    - Expected value (derived from policy-level probability estimates)

INVARIANT:
    This object is FROZEN after construction. No downstream component may mutate it.
    It represents "what the market looks like" — not "what to do about it."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OpportunityAssessment:
    """
    Complete analytical state at the moment market analysis finishes.

    Constructed inside run_new_engine() immediately after MarketStateEngine.evaluate()
    and immediately before compute_execution_policy().
    """

    # ─── IDENTITY ─────────────────────────────────────────────────────
    symbol: str
    cycle_id: int
    bar_time: int              # Unix seconds of the closed bar being evaluated

    # ─── PATTERN ──────────────────────────────────────────────────────
    pattern: str               # Authoritative pattern name (e.g., "THREE_WHITE_SOLDIERS")
    side: str                  # Trade direction implied by pattern ("BUY" or "SELL")
    pattern_quality: float     # 0.0–1.0 (from _score_pattern_quality)

    # ─── STRATEGY CLASSIFICATION ──────────────────────────────────────
    selected_strategy: str | None   # "REVERSAL" | "CONTINUATION" | "FALSE_BREAK" | None
    strategy_confidence: float      # Activation weight (0.0–1.0)
    regime: str                     # Regime classification ("TRENDING" | "RANGE" | "TRANSITIONAL")
    regime_confidence: float        # Regime classifier confidence (0.0–1.0)
    eligible_strategies: tuple[str, ...]  # Strategies that passed eligibility
    weights_used: str               # "strategy_specific" or "global_fallback"

    # ─── SCORING (10-factor analysis result) ──────────────────────────
    components: dict[str, float]    # All 10 component scores (each 0.0–1.0)
    score_neutral: float            # Global-weighted composite
    score_strategy: float           # Strategy-weighted composite
    score_delta: float              # score_strategy - score_neutral

    # ─── MARKET STATE ─────────────────────────────────────────────────
    market_state: str               # "STRUCTURED" | "TRANSITIONAL" | "CHOP"
    market_state_confidence: float  # MarketStateEngine confidence
    delta_stability: float          # Score delta stability metric

    # ─── CONTEXT (what scoring components reflect) ────────────────────
    bias_alignment: float           # Pattern vs bias FSM alignment
    trend_alignment: float          # Price vs EMA trend
    chop_clarity: float             # Inverse of candle overlap noise
    volatility_quality: float       # Directional quality of recent volatility
    confirmation_pre: float         # Candle body quality preview
    htf_alignment: float            # H1 bias + M15 structure combined
    h4_alignment: float             # H4 regime alignment

    # ─── ENTITY IDENTITY (opportunity lifecycle linkage) ──────────────
    # Received from the opportunity lifecycle — never generated here.
    # Links this assessment to decision_audit, decision_ledger, trade_truth.
    entity_id: str = ""             # f"{symbol}_{bar_time}" — set by run_new_engine()

    # ─── REASONING (Future: Reasoning Engine) ─────────────────────────
    # Natural-language explanation of WHY this opportunity was assessed
    # as it was. Will be populated by a future reasoning subsystem.
    reasoning: str | None = None                    # Future: Reasoning Engine

    # ─── EVIDENCE CONTRIBUTION (populated by Attribution Engine) ─────
    # Per-factor contribution breakdown explaining which evidence
    # supported or contradicted the opportunity thesis.
    # Populated via dataclasses.replace() after attribution computation.
    evidence_contributions: tuple[Any, ...] = ()    # Populated by: core.attribution.engine

    # ─── SUPPORTING / CONTRADICTING EVIDENCE (Future: Evidence Tracker) ─
    # Explicit lists of evidence factors that support or contradict
    # the assessed opportunity direction.
    supporting_evidence: tuple[str, ...] = ()       # Future: Evidence Tracker
    contradicting_evidence: tuple[str, ...] = ()    # Future: Evidence Tracker

    # ─── ALTERNATIVE THESIS (Future: Multi-Hypothesis Engine) ──────────
    # An alternative interpretation of the same market data that the
    # primary assessment did not select.
    alternative_thesis: str | None = None            # Future: Multi-Hypothesis Engine

    # ─── UNCERTAINTY (populated by Uncertainty Engine) ───────────────
    # Quantified assessment of how ambiguous the opportunity is.
    # Populated via dataclasses.replace() after uncertainty computation.
    # 0.0 = very clear opportunity, 1.0 = highly ambiguous.
    uncertainty_score: float | None = None           # Populated by: core.uncertainty.engine
    confidence_modifier: float | None = None         # Populated by: core.uncertainty.engine

    def to_dict(self) -> dict[str, Any]:
        """Serialize to flat dict for persistence."""
        return {
            "symbol": self.symbol,
            "cycle_id": self.cycle_id,
            "bar_time": self.bar_time,
            "entity_id": self.entity_id,
            "pattern": self.pattern,
            "side": self.side,
            "pattern_quality": self.pattern_quality,
            "selected_strategy": self.selected_strategy,
            "strategy_confidence": round(self.strategy_confidence, 4),
            "regime": self.regime,
            "regime_confidence": round(self.regime_confidence, 4),
            "eligible_strategies": list(self.eligible_strategies),
            "weights_used": self.weights_used,
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "score_neutral": round(self.score_neutral, 4),
            "score_strategy": round(self.score_strategy, 4),
            "score_delta": round(self.score_delta, 4),
            "market_state": self.market_state,
            "market_state_confidence": round(self.market_state_confidence, 4),
            "delta_stability": round(self.delta_stability, 4),
            "bias_alignment": round(self.bias_alignment, 4),
            "trend_alignment": round(self.trend_alignment, 4),
            "chop_clarity": round(self.chop_clarity, 4),
            "volatility_quality": round(self.volatility_quality, 4),
            "confirmation_pre": round(self.confirmation_pre, 4),
            "htf_alignment": round(self.htf_alignment, 4),
            "h4_alignment": round(self.h4_alignment, 4),
            # Placeholder fields (None/empty = not yet implemented)
            "reasoning": self.reasoning,
            "evidence_contributions": list(self.evidence_contributions),
            "supporting_evidence": list(self.supporting_evidence),
            "contradicting_evidence": list(self.contradicting_evidence),
            "alternative_thesis": self.alternative_thesis,
            "uncertainty_score": self.uncertainty_score,
            "confidence_modifier": self.confidence_modifier,
        }
