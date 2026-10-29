"""
Market State Engine — System stability evaluator.

Evaluates whether the market environment is STRUCTURED enough to trade.
Does NOT evaluate direction, signals, or trade quality.
Only answers: "Is this a tradeable environment?"

Market States:
    🟢 STRUCTURED — stable delta, low flip rate, consistent scoring
    🟡 TRANSITIONAL — moderate instability, mixed signals
    🔴 CHOP — no edge, high noise, execution blocked

This layer is INDEPENDENT of:
    - Strategy classification (A/B/C)
    - Scoring (neutral or strategy)
    - Execution/risk decisions

It provides a stability signal consumed by the Execution Policy Engine.

Design: deterministic, no learning, no adaptation.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MarketState(str, Enum):
    """System stability classification."""
    STRUCTURED = "STRUCTURED"
    TRANSITIONAL = "TRANSITIONAL"
    CHOP = "CHOP"


@dataclass(frozen=True)
class MarketStateResult:
    """Immutable output of market state evaluation."""
    state: MarketState
    confidence: float           # 0.0–1.0 how certain is this classification
    delta_stability: float      # 0.0–1.0 (1.0 = very stable delta)
    flip_rate: float            # 0.0–1.0 (0.0 = no flips, 1.0 = constant flipping)
    score_consistency: float    # 0.0–1.0 (1.0 = scores very consistent)
    reasoning: str


class MarketStateEngine:
    """
    Tracks rolling market stability metrics and classifies state.

    Maintains a sliding window of recent observations to detect:
    - Delta stability (score_strategy - score_neutral over time)
    - Strategy flip rate (how often classification changes A↔B↔C)
    - Score consistency (variance of recent scores)

    Usage:
        engine = MarketStateEngine()
        result = engine.evaluate(score_neutral, score_strategy, strategy_type)
    """

    def __init__(self, window_size: int = 20) -> None:
        self._window = window_size
        self._deltas: deque[float] = deque(maxlen=window_size)
        self._strategies: deque[str] = deque(maxlen=window_size)
        self._neutral_scores: deque[float] = deque(maxlen=window_size)
        self._strategy_scores: deque[float] = deque(maxlen=window_size)

    def evaluate(
        self,
        score_neutral: float,
        score_strategy: float,
        strategy_type: str,
    ) -> MarketStateResult:
        """
        Evaluate market state based on current + historical observations.

        Args:
            score_neutral: Global-weighted composite score this cycle
            score_strategy: Strategy-weighted composite score this cycle
            strategy_type: Current strategy classification (A/B/C string)

        Returns:
            MarketStateResult with state classification
        """
        delta = score_strategy - score_neutral

        # Record observations
        self._deltas.append(delta)
        self._strategies.append(strategy_type)
        self._neutral_scores.append(score_neutral)
        self._strategy_scores.append(score_strategy)

        # Need minimum observations for meaningful classification
        if len(self._deltas) < 5:
            return MarketStateResult(
                state=MarketState.TRANSITIONAL,
                confidence=0.3,
                delta_stability=0.5,
                flip_rate=0.0,
                score_consistency=0.5,
                reasoning="Insufficient observations (warm-up period)",
            )

        # ─── COMPUTE METRICS ──────────────────────────────────────────

        delta_stability = self._compute_delta_stability()
        flip_rate = self._compute_flip_rate()
        score_consistency = self._compute_score_consistency()

        # ─── CLASSIFY STATE ───────────────────────────────────────────

        state, confidence, reasoning = self._classify(
            delta_stability, flip_rate, score_consistency,
            score_neutral, score_strategy,
        )

        return MarketStateResult(
            state=state,
            confidence=confidence,
            delta_stability=round(delta_stability, 4),
            flip_rate=round(flip_rate, 4),
            score_consistency=round(score_consistency, 4),
            reasoning=reasoning,
        )

    def _compute_delta_stability(self) -> float:
        """How stable is the delta (strategy - neutral) over time? 1.0 = very stable."""
        if len(self._deltas) < 3:
            return 0.5
        deltas = list(self._deltas)
        mean = sum(deltas) / len(deltas)
        variance = sum((d - mean) ** 2 for d in deltas) / len(deltas)
        std = variance ** 0.5
        # Normalize: std of 0 = perfect stability, std > 0.1 = unstable
        stability = max(0.0, 1.0 - (std / 0.10))
        return min(1.0, stability)

    def _compute_flip_rate(self) -> float:
        """How often does strategy classification change? 0.0 = never, 1.0 = every cycle."""
        if len(self._strategies) < 3:
            return 0.0
        strategies = list(self._strategies)
        flips = sum(1 for i in range(1, len(strategies)) if strategies[i] != strategies[i - 1])
        return flips / (len(strategies) - 1)

    def _compute_score_consistency(self) -> float:
        """How consistent are neutral scores over time? 1.0 = very consistent."""
        if len(self._neutral_scores) < 3:
            return 0.5
        scores = list(self._neutral_scores)
        mean = sum(scores) / len(scores)
        if mean <= 0:
            return 0.0
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        std = variance ** 0.5
        # Normalize: CV (coefficient of variation) based
        cv = std / max(mean, 0.001)
        consistency = max(0.0, 1.0 - (cv / 0.5))
        return min(1.0, consistency)

    def _classify(
        self,
        delta_stability: float,
        flip_rate: float,
        score_consistency: float,
        score_neutral: float,
        score_strategy: float,
    ) -> tuple[MarketState, float, str]:
        """Deterministic state classification from metrics."""

        # ─── CHOP (no edge) ───────────────────────────────────────────
        # Low scores + high instability + frequent flipping
        if (score_neutral < 0.25 and flip_rate > 0.5) or \
           (delta_stability < 0.3 and score_consistency < 0.3):
            confidence = min(1.0, (1.0 - delta_stability) * 0.5 + flip_rate * 0.5)
            return MarketState.CHOP, round(confidence, 3), \
                f"Low scores ({score_neutral:.3f}) + high instability (flip={flip_rate:.2f}, delta_std={1-delta_stability:.2f})"

        # ─── STRUCTURED (clear edge) ─────────────────────────────────
        # Stable delta + low flip rate + consistent scores
        if delta_stability >= 0.6 and flip_rate <= 0.25 and score_consistency >= 0.5:
            confidence = min(1.0, delta_stability * 0.4 + (1.0 - flip_rate) * 0.3 + score_consistency * 0.3)
            return MarketState.STRUCTURED, round(confidence, 3), \
                f"Stable environment (delta_stab={delta_stability:.2f}, flips={flip_rate:.2f}, consistency={score_consistency:.2f})"

        # ─── TRANSITIONAL (default middle ground) ─────────────────────
        confidence = 0.5
        return MarketState.TRANSITIONAL, confidence, \
            f"Mixed stability signals (delta_stab={delta_stability:.2f}, flips={flip_rate:.2f}, consistency={score_consistency:.2f})"


# ─── MODULE-LEVEL SINGLETON ───────────────────────────────────────────────────

_market_state_engine: MarketStateEngine | None = None


def get_market_state_engine() -> MarketStateEngine:
    """Get or create singleton market state engine."""
    global _market_state_engine
    if _market_state_engine is None:
        _market_state_engine = MarketStateEngine(window_size=20)
    return _market_state_engine
