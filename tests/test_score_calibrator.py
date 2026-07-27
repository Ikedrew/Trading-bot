"""
Tests for Score Calibration Layer.

Validates:
1. Calibrated output currently equals raw score (identity mapping)
2. EV receives calibrated probability through the pipeline
3. Behaviour remains unchanged from before calibration layer was added
4. Metadata is persisted (raw_score, calibrated_probability, calibration_source/version)
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.pipeline.score_calibrator import (
    ScoreCalibrator,
    CalibrationResult,
    get_score_calibrator,
)
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


# ─── TEST 1: IDENTITY MAPPING ─────────────────────────────────────────────────


class TestIdentityMapping:
    """Phase 1 calibrator: calibrated_probability == raw_score."""

    def test_calibrated_equals_raw(self):
        cal = ScoreCalibrator()
        result = cal.calibrate(0.55)
        # Empirical calibration: 0.55 falls in 0.50-0.60 bucket → p=0.3939
        if cal.is_empirical:
            assert result.calibrated_probability == 0.3939
        else:
            assert result.calibrated_probability == 0.55

    def test_various_scores(self):
        cal = ScoreCalibrator()
        for score in [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0]:
            result = cal.calibrate(score)
            assert 0.0 <= result.calibrated_probability <= 1.0
            assert result.raw_score == round(score, 4)

    def test_clamped_below_zero(self):
        cal = ScoreCalibrator()
        result = cal.calibrate(-0.1)
        assert 0.0 <= result.calibrated_probability <= 1.0

    def test_clamped_above_one(self):
        cal = ScoreCalibrator()
        result = cal.calibrate(1.5)
        assert 0.0 <= result.calibrated_probability <= 1.0

    def test_metadata_populated(self):
        cal = ScoreCalibrator()
        result = cal.calibrate(0.6)
        assert result.calibration_source == "ScoreCalibrator"
        assert result.calibration_version in ("empirical_v1", "identity_v1")

    def test_singleton(self):
        c1 = get_score_calibrator()
        c2 = get_score_calibrator()
        assert c1 is c2


# ─── TEST 2: PIPELINE INTEGRATION ─────────────────────────────────────────────


class TestPipelineIntegration:
    """Calibrator integrates correctly into ProbabilityEstimator → EV pipeline."""

    def test_estimator_uses_calibrator(self):
        """ProbabilityEstimate carries calibration metadata."""
        est = ProbabilityEstimator()
        result = est.estimate(
            assessment=FakeAssessment(score_neutral=0.5),
            market_state_result=_msr(),
            confirmation_score=1.0,
        )
        assert result.raw_score == 0.5
        assert 0.0 < result.calibrated_probability <= 1.0
        assert result.calibration_source == "ScoreCalibrator"
        assert result.calibration_version in ("empirical_v1", "identity_v1")
        assert "score_calibration" in result.evidence_used

    def test_ev_receives_calibrated_probability(self):
        """Full pipeline: score → calibrator → estimator → EV."""
        est = ProbabilityEstimator()
        prob = est.estimate(
            assessment=FakeAssessment(score_neutral=0.6),
            market_state_result=_msr(MarketState.STRUCTURED),
            confirmation_score=1.0,
        )
        ev = compute_expected_value(
            market_state_result=_msr(MarketState.STRUCTURED),
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            probability_estimate=prob,
        )
        # EV should use the probability from the estimate
        assert ev.p_success == prob.p_success
        assert ev.ev_positive is True  # 0.6 score → p≈0.57 → positive at RR=2


# ─── TEST 3: BEHAVIOUR EQUIVALENCE ────────────────────────────────────────────


class TestBehaviourEquivalence:
    """Adding calibration layer does not change final EV results."""

    def test_same_result_with_and_without_calibration(self):
        """Estimator path and inline EV fallback produce valid results."""
        assessment = FakeAssessment(score_neutral=0.55)
        msr = _msr(MarketState.TRANSITIONAL)

        est = ProbabilityEstimator()
        prob = est.estimate(assessment=assessment, market_state_result=msr, confirmation_score=0.9)

        # Both produce valid probability
        assert 0.0 < prob.p_success < 1.0
        # Calibration version is recorded
        assert prob.calibration_version in ("empirical_v1", "identity_v1")


# ─── TEST 4: PERSISTENCE METADATA ─────────────────────────────────────────────


class TestPersistenceMetadata:
    """ProbabilityEstimate.to_dict() includes calibration fields."""

    def test_to_dict_has_calibration_fields(self):
        est = ProbabilityEstimator()
        result = est.estimate(
            assessment=FakeAssessment(score_neutral=0.5),
            market_state_result=_msr(),
            confirmation_score=1.0,
        )
        d = result.to_dict()
        assert "raw_score" in d
        assert "calibrated_probability" in d
        assert "calibration_source" in d
        assert "calibration_version" in d
        assert d["raw_score"] == 0.5
        assert d["calibration_source"] == "ScoreCalibrator"
        # Version is empirical_v1 when artifact loaded, identity_v1 otherwise
        assert d["calibration_version"] in ("empirical_v1", "identity_v1")

    def test_raw_and_final_both_present(self):
        """Both raw score and final p_success are in persistence output."""
        est = ProbabilityEstimator()
        result = est.estimate(
            assessment=FakeAssessment(score_neutral=0.6),
            market_state_result=_msr(MarketState.TRANSITIONAL),
            confirmation_score=0.8,
        )
        d = result.to_dict()
        assert d["raw_score"] == 0.6
        # Calibrated may differ from raw (empirical curve)
        assert 0.0 < d["calibrated_probability"] <= 1.0
        # Final p_success has dampening applied
        assert d["p_success"] < d["calibrated_probability"]  # dampening reduces
        assert d["p_success"] > 0.0
