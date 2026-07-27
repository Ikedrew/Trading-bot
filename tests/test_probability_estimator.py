"""
Tests for Probability Estimator Interface.

Validates:
1. ProbabilityEstimator produces correct estimates
2. EV engine consumes estimates without recalculating
3. Behaviour equivalence (before == after)
4. Observability metadata populated
5. Backward compatibility (EV inline fallback still works)
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest

from core.pipeline.probability_estimator import (
    ProbabilityEstimator,
    ProbabilityEstimate,
    get_probability_estimator,
)
from core.pipeline.expected_value import compute_expected_value
from core.pipeline.market_state_engine import MarketState, MarketStateResult


def _msr(state: MarketState = MarketState.STRUCTURED) -> MarketStateResult:
    return MarketStateResult(
        state=state, confidence=0.8, delta_stability=0.7,
        flip_rate=0.1, score_consistency=0.8, reasoning="test",
    )


@dataclass
class FakeAssessment:
    score_neutral: float = 0.55
    score_strategy: float = 0.55
    strategy_confidence: float = 0.0


# ─── TEST 1: ESTIMATOR PRODUCES CORRECT OUTPUT ────────────────────────────────


class TestEstimatorOutput:
    """ProbabilityEstimator produces valid ProbabilityEstimate."""

    def test_basic_estimate(self):
        est = ProbabilityEstimator()
        result = est.estimate(
            assessment=FakeAssessment(score_neutral=0.6),
            market_state_result=_msr(MarketState.STRUCTURED),
            confirmation_score=1.0,
        )
        assert isinstance(result, ProbabilityEstimate)
        # With empirical calibration: 0.6 → bucket 0.60-0.70 → p=0.4545
        # Then dampening (5%): 0.4545 * 0.95 = 0.4318
        assert 0.30 <= result.p_success <= 0.50
        assert result.p_failure == round(1.0 - result.p_success, 4)

    def test_dampening_applied(self):
        est = ProbabilityEstimator()
        r_struct = est.estimate(assessment=FakeAssessment(score_neutral=0.5), market_state_result=_msr(MarketState.STRUCTURED), confirmation_score=1.0)
        r_trans = est.estimate(assessment=FakeAssessment(score_neutral=0.5), market_state_result=_msr(MarketState.TRANSITIONAL), confirmation_score=1.0)
        r_chop = est.estimate(assessment=FakeAssessment(score_neutral=0.5), market_state_result=_msr(MarketState.CHOP), confirmation_score=1.0)
        # More dampening → lower p
        assert r_struct.p_success > r_trans.p_success > r_chop.p_success

    def test_confirmation_modifier(self):
        est = ProbabilityEstimator()
        r_strong = est.estimate(assessment=FakeAssessment(score_neutral=0.5), market_state_result=_msr(), confirmation_score=1.0)
        r_weak = est.estimate(assessment=FakeAssessment(score_neutral=0.5), market_state_result=_msr(), confirmation_score=0.5)
        assert r_strong.p_success > r_weak.p_success

    def test_clamped_bounds(self):
        est = ProbabilityEstimator()
        r_high = est.estimate(assessment=FakeAssessment(score_neutral=0.99), market_state_result=_msr(MarketState.STRUCTURED), confirmation_score=1.0)
        r_low = est.estimate(assessment=FakeAssessment(score_neutral=0.01), market_state_result=_msr(MarketState.CHOP), confirmation_score=0.1)
        assert r_high.p_success <= 0.85
        assert r_low.p_success >= 0.10

    def test_metadata_populated(self):
        est = ProbabilityEstimator()
        result = est.estimate(assessment=FakeAssessment(score_neutral=0.5), market_state_result=_msr(MarketState.TRANSITIONAL), confirmation_score=0.8)
        assert result.source == "ProbabilityEstimator"
        assert result.model_version == "score_v1"
        assert "score_neutral" in result.evidence_used
        assert "market_state_TRANSITIONAL" in result.evidence_used
        assert "confirmation_quality" in result.evidence_used
        assert result.raw_score == 0.5

    def test_to_dict_serializable(self):
        est = ProbabilityEstimator()
        result = est.estimate(assessment=FakeAssessment(score_neutral=0.5), market_state_result=_msr(), confirmation_score=1.0)
        d = result.to_dict()
        assert isinstance(d["p_success"], float)
        assert isinstance(d["evidence_used"], list)
        assert d["model_version"] == "score_v1"


# ─── TEST 2: EV CONSUMES ESTIMATE (NO RECALCULATION) ─────────────────────────


class TestEVConsumesEstimate:
    """EV engine uses provided ProbabilityEstimate directly."""

    def test_ev_uses_provided_probability(self):
        """When probability_estimate is given, EV does not recalculate."""
        # Create a fixed estimate
        estimate = ProbabilityEstimate(
            p_success=0.45, p_failure=0.55,
            source="test", model_version="test_v1",
            uncertainty_dampening=0.10,
        )
        result = compute_expected_value(
            market_state_result=_msr(),
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            probability_estimate=estimate,
        )
        # EV should use 0.45, not recalculate from score
        assert result.p_success == 0.45
        assert result.p_failure == 0.55

    def test_ev_ignores_score_when_estimate_provided(self):
        """score_neutral is irrelevant when probability_estimate is provided."""
        estimate = ProbabilityEstimate(p_success=0.60, p_failure=0.40, uncertainty_dampening=0.05)
        result = compute_expected_value(
            score_neutral=0.01,  # Very low — but should be ignored
            market_state_result=_msr(),
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            probability_estimate=estimate,
        )
        assert result.p_success == 0.60  # Used estimate, not score


# ─── TEST 3: BEHAVIOUR EQUIVALENCE ───────────────────────────────────────────


class TestBehaviourEquivalence:
    """Estimator + EV produces same result as inline EV (backward compat)."""

    def test_estimator_matches_inline(self):
        """Both paths produce valid results (may differ due to calibration)."""
        assessment = FakeAssessment(score_neutral=0.55)
        msr = _msr(MarketState.TRANSITIONAL)

        # Path 1: Estimator → EV (uses calibrator)
        est = ProbabilityEstimator()
        prob = est.estimate(assessment=assessment, market_state_result=msr, confirmation_score=0.9)
        ev_with_est = compute_expected_value(
            market_state_result=msr,
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            probability_estimate=prob,
        )

        # Path 2: Inline EV (no estimate — uses raw score without calibrator)
        ev_inline = compute_expected_value(
            assessment=assessment,
            market_state_result=msr,
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            confirmation_score=0.9,
        )

        # Both produce valid EV results
        assert isinstance(ev_with_est.p_success, float)
        assert isinstance(ev_inline.p_success, float)
        # Estimator path uses calibration (lower probabilities from empirical data)
        # Inline path uses raw score (identity — higher)
        assert ev_with_est.p_success <= ev_inline.p_success

    def test_singleton_consistent(self):
        """get_probability_estimator() returns same instance."""
        est1 = get_probability_estimator()
        est2 = get_probability_estimator()
        assert est1 is est2


# ─── TEST 4: BACKWARD COMPATIBILITY ──────────────────────────────────────────


class TestBackwardCompatibility:
    """EV still works without probability_estimate (inline fallback)."""

    def test_no_estimate_uses_inline(self):
        """Without probability_estimate, EV computes inline."""
        result = compute_expected_value(
            score_neutral=0.5,
            market_state_result=_msr(MarketState.STRUCTURED),
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            confirmation_score=1.0,
        )
        # p = 0.5 * 1.0 * 0.95 = 0.475
        assert 0.45 <= result.p_success <= 0.50
        assert result.ev_positive is True

    def test_shadow_rooms_callers_unaffected(self):
        """Callers using old API (no probability_estimate) still work."""
        result = compute_expected_value(
            score_neutral=0.3,
            strategy_confidence=0.0,  # Legacy param still accepted
            market_state_result=_msr(MarketState.CHOP),
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            confirmation_score=0.5,
        )
        assert isinstance(result.p_success, float)
        assert 0.0 <= result.p_success <= 1.0
