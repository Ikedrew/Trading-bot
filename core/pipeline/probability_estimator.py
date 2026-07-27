"""
Probability Estimator — Dedicated authority for p_success estimation.

This is the ONLY component responsible for producing probability estimates.
The EV engine consumes these estimates — it does not create them.

Architecture:
    OpportunityAssessment → ProbabilityEstimator → ProbabilityEstimate → EV Engine

Phase 1: Score-based estimation (preserves current behaviour).
Future phases will add:
    - Pattern empirical win rates
    - Regime-conditional probability
    - Symbol/session statistics
    - Calibrated probability models

Design: deterministic, no learning, no adaptation (Phase 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.pipeline.market_state_engine import MarketState, MarketStateResult


# ─── ESTIMATOR VERSION ────────────────────────────────────────────────────────

_ESTIMATOR_VERSION = "score_v1"


# ─── UNCERTAINTY COEFFICIENTS (per market state) ──────────────────────────────

_UNCERTAINTY_DAMPENING = {
    MarketState.STRUCTURED: 0.05,
    MarketState.TRANSITIONAL: 0.20,
    MarketState.CHOP: 0.25,
}

_P_MIN = 0.10
_P_MAX = 0.85


# ─── OUTPUT MODEL ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProbabilityEstimate:
    """
    Output of probability estimation.

    This is the single interface between probability logic and EV calculation.
    The EV engine receives this object and does not interpret scores or patterns.
    """
    p_success: float                    # Estimated probability of trade success (0.0–1.0)
    p_failure: float                    # 1.0 - p_success
    source: str = ""                    # Which estimator produced this
    model_version: str = ""             # Version identifier for tracking
    confidence: float | None = None     # Meta-confidence in the estimate itself
    evidence_used: tuple[str, ...] = () # What inputs contributed
    raw_score: float = 0.0             # Pre-calibration raw composite score
    calibrated_probability: float = 0.0 # Post-calibration base probability (before dampening)
    calibration_source: str = ""        # Which calibrator was used
    calibration_version: str = ""       # Calibrator version
    uncertainty_dampening: float = 0.0  # Dampening applied after calibration

    def to_dict(self) -> dict[str, Any]:
        """Serialize for persistence."""
        return {
            "p_success": round(self.p_success, 4),
            "p_failure": round(self.p_failure, 4),
            "source": self.source,
            "model_version": self.model_version,
            "confidence": round(self.confidence, 4) if self.confidence is not None else None,
            "evidence_used": list(self.evidence_used),
            "raw_score": round(self.raw_score, 4),
            "calibrated_probability": round(self.calibrated_probability, 4),
            "calibration_source": self.calibration_source,
            "calibration_version": self.calibration_version,
            "uncertainty_dampening": round(self.uncertainty_dampening, 4),
        }


# ─── ESTIMATOR ────────────────────────────────────────────────────────────────


class ProbabilityEstimator:
    """
    Dedicated probability estimation authority.

    Phase 1 (score_v1): p_success = score × confirmation × (1 - dampening)
    This preserves exact current behaviour while establishing the interface.

    Future phases will swap the estimation logic without changing the interface.
    """

    def estimate(
        self,
        *,
        assessment: Any,
        market_state_result: MarketStateResult,
        confirmation_score: float = 1.0,
    ) -> ProbabilityEstimate:
        """
        Produce a probability estimate for a trade opportunity.

        Flow: score → ScoreCalibrator → dampening → ProbabilityEstimate

        Args:
            assessment: OpportunityAssessment (provides score_neutral)
            market_state_result: Market stability classification
            confirmation_score: Candle quality at entry (0.0–1.0)

        Returns:
            ProbabilityEstimate with p_success and metadata.
        """
        from core.pipeline.score_calibrator import get_score_calibrator

        # Extract score from assessment
        score_neutral = getattr(assessment, "score_neutral", 0.0) if assessment else 0.0

        # ─── SCORE CALIBRATION (dedicated layer) ──────────────────────
        calibrator = get_score_calibrator()
        calibration = calibrator.calibrate(score_neutral)
        p_base = calibration.calibrated_probability

        # Market state uncertainty dampening
        state = market_state_result.state
        dampening = _UNCERTAINTY_DAMPENING.get(state, 0.20)

        # Confirmation modifier (1.0 = no reduction, 0.0 = halves probability)
        confirmation_modifier = 0.5 + (0.5 * confirmation_score)

        # Adjusted probability = calibrated × confirmation × (1 - uncertainty)
        p_success = p_base * confirmation_modifier * (1.0 - dampening)

        # Clamp to realistic bounds
        p_success = max(_P_MIN, min(_P_MAX, p_success))
        p_success = round(p_success, 4)

        p_failure = round(1.0 - p_success, 4)

        # Evidence tracking
        evidence = ["score_neutral", "score_calibration"]
        if dampening > 0:
            evidence.append(f"market_state_{state.value}")
        if confirmation_score < 1.0:
            evidence.append("confirmation_quality")

        return ProbabilityEstimate(
            p_success=p_success,
            p_failure=p_failure,
            source="ProbabilityEstimator",
            model_version=_ESTIMATOR_VERSION,
            confidence=None,  # Phase 1: no meta-confidence
            evidence_used=tuple(evidence),
            raw_score=calibration.raw_score,
            calibrated_probability=calibration.calibrated_probability,
            calibration_source=calibration.calibration_source,
            calibration_version=calibration.calibration_version,
            uncertainty_dampening=dampening,
        )


# ─── MODULE-LEVEL SINGLETON ───────────────────────────────────────────────────

_estimator: ProbabilityEstimator | None = None


def get_probability_estimator() -> ProbabilityEstimator:
    """Get or create singleton probability estimator."""
    global _estimator
    if _estimator is None:
        _estimator = ProbabilityEstimator()
    return _estimator
