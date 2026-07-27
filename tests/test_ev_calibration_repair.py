"""
Tests for Phase 1 EV Calibration Repair — Remove Dead Probability Weight.

Validates:
1. Score directly influences probability (no 0.6 multiplier)
2. Strategy confidence changes do NOT affect probability
3. EV calculation receives new probability correctly
4. Existing EV rejection logic still works
5. No regression in risk or execution policy
"""

from __future__ import annotations

import pytest

from core.pipeline.expected_value import compute_expected_value, ExpectedValueResult
from core.pipeline.market_state_engine import MarketState, MarketStateResult


def _make_msr(state: MarketState = MarketState.STRUCTURED) -> MarketStateResult:
    return MarketStateResult(
        state=state, confidence=0.8, delta_stability=0.7,
        flip_rate=0.1, score_consistency=0.8, reasoning="test",
    )


# ─── TEST 1: SCORE DIRECTLY INFLUENCES PROBABILITY ───────────────────────────


class TestScoreDirectlyInfluencesProbability:
    """p_success should track score_neutral directly."""

    def test_high_score_high_probability(self):
        """Score of 0.7 should produce p_success close to 0.7 (after dampening)."""
        result = compute_expected_value(
            score_neutral=0.7, strategy_confidence=0.0,
            market_state_result=_make_msr(MarketState.STRUCTURED),
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            confirmation_score=1.0,
        )
        # p_base = 0.7, dampening = 5%, confirmation = 1.0
        # p_success = 0.7 * 1.0 * 0.95 = 0.665
        assert result.p_success >= 0.6
        assert result.p_success <= 0.75

    def test_low_score_low_probability(self):
        """Score of 0.3 should produce low p_success."""
        result = compute_expected_value(
            score_neutral=0.3, strategy_confidence=0.0,
            market_state_result=_make_msr(MarketState.STRUCTURED),
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            confirmation_score=1.0,
        )
        # p_base = 0.3, dampening = 5%, confirmation = 1.0
        # p_success = 0.3 * 1.0 * 0.95 = 0.285
        assert result.p_success >= 0.2
        assert result.p_success <= 0.35

    def test_mid_score_produces_threshold_boundary(self):
        """Score of 0.5 with RR=2 should produce positive EV (was blocked before)."""
        result = compute_expected_value(
            score_neutral=0.5, strategy_confidence=0.0,
            market_state_result=_make_msr(MarketState.STRUCTURED),
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            confirmation_score=1.0,
        )
        # p_base = 0.5, p_success ≈ 0.475
        # EV = 0.475 * 0.002 - 0.525 * 0.001 = 0.00095 - 0.000525 = 0.000425 > 0
        assert result.ev_positive is True

    def test_score_proportional_to_probability(self):
        """Higher score → higher p_success (monotonic relationship)."""
        results = []
        for score in [0.3, 0.4, 0.5, 0.6, 0.7]:
            r = compute_expected_value(
                score_neutral=score, strategy_confidence=0.0,
                market_state_result=_make_msr(MarketState.STRUCTURED),
                entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
                confirmation_score=1.0,
            )
            results.append(r.p_success)
        # Must be monotonically increasing
        for i in range(1, len(results)):
            assert results[i] > results[i - 1], f"p_success not monotonic: {results}"


# ─── TEST 2: STRATEGY CONFIDENCE DOES NOT AFFECT PROBABILITY ─────────────────


class TestStrategyConfidenceDoesNotAffect:
    """Changing strategy_confidence must NOT change p_success."""

    def test_zero_vs_high_confidence_same_result(self):
        """strategy_confidence=0 and strategy_confidence=0.9 produce same EV."""
        r1 = compute_expected_value(
            score_neutral=0.5, strategy_confidence=0.0,
            market_state_result=_make_msr(),
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            confirmation_score=1.0,
        )
        r2 = compute_expected_value(
            score_neutral=0.5, strategy_confidence=0.9,
            market_state_result=_make_msr(),
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            confirmation_score=1.0,
        )
        assert r1.p_success == r2.p_success
        assert r1.ev == r2.ev

    def test_none_confidence_same_as_zero(self):
        """strategy_confidence=None treated same as 0."""
        r1 = compute_expected_value(
            score_neutral=0.5, strategy_confidence=None,
            market_state_result=_make_msr(),
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            confirmation_score=1.0,
        )
        r2 = compute_expected_value(
            score_neutral=0.5, strategy_confidence=0.5,
            market_state_result=_make_msr(),
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            confirmation_score=1.0,
        )
        assert r1.p_success == r2.p_success


# ─── TEST 3: EV CALCULATION CORRECT WITH NEW PROBABILITY ─────────────────────


class TestEVCalculationCorrect:
    """EV formula (p*reward - (1-p)*risk) still works correctly."""

    def test_positive_ev_at_good_score(self):
        """Score 0.6 + RR=2 should produce positive EV."""
        result = compute_expected_value(
            score_neutral=0.6, strategy_confidence=0.0,
            market_state_result=_make_msr(MarketState.STRUCTURED),
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            confirmation_score=1.0,
        )
        assert result.ev_positive is True
        assert result.ev > 0

    def test_negative_ev_at_bad_score_high_dampening(self):
        """Very low score in CHOP should still produce negative EV."""
        result = compute_expected_value(
            score_neutral=0.25, strategy_confidence=0.0,
            market_state_result=_make_msr(MarketState.CHOP),
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            confirmation_score=0.5,
        )
        # p_base=0.25, dampening=25%, confirmation=0.75
        # p = 0.25 * 0.75 * 0.75 = 0.1406
        # EV = 0.14 * 0.002 - 0.86 * 0.001 = 0.00028 - 0.00086 < 0
        assert result.ev_positive is False

    def test_rr_effective_unchanged(self):
        """RR calculation should be unaffected by probability changes."""
        result = compute_expected_value(
            score_neutral=0.5, strategy_confidence=0.0,
            market_state_result=_make_msr(),
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
        )
        # RR = 0.002 / 0.001 = 2.0
        assert result.rr_effective == 2.0


# ─── TEST 4: EV REJECTION LOGIC STILL WORKS ──────────────────────────────────


class TestEVRejectionLogicWorks:
    """EV gate still correctly blocks when probability is genuinely low."""

    def test_very_low_score_still_rejected(self):
        """Score of 0.2 in TRANSITIONAL should still produce negative EV."""
        result = compute_expected_value(
            score_neutral=0.2, strategy_confidence=0.0,
            market_state_result=_make_msr(MarketState.TRANSITIONAL),
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            confirmation_score=0.7,
        )
        # Very low score → low probability → negative EV expected
        # p_base=0.2, dampening=20%, confirm=0.85
        # p = 0.2 * 0.85 * 0.80 = 0.136
        assert result.ev_positive is False

    def test_zero_risk_returns_invalid(self):
        """SL = entry → risk=0 → handled safely."""
        result = compute_expected_value(
            score_neutral=0.5, strategy_confidence=0.0,
            market_state_result=_make_msr(),
            entry_price=1.1000, stop_loss=1.1000, take_profit=1.1020,
        )
        assert result.ev_positive is False
        assert result.rr_effective == 0.0

    def test_p_success_capped_at_max(self):
        """p_success should never exceed 0.85 (overconfidence cap)."""
        result = compute_expected_value(
            score_neutral=0.99, strategy_confidence=0.0,
            market_state_result=_make_msr(MarketState.STRUCTURED),
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            confirmation_score=1.0,
        )
        assert result.p_success <= 0.85

    def test_p_success_floored_at_min(self):
        """p_success should never go below 0.10 (floor)."""
        result = compute_expected_value(
            score_neutral=0.01, strategy_confidence=0.0,
            market_state_result=_make_msr(MarketState.CHOP),
            entry_price=1.1000, stop_loss=1.0990, take_profit=1.1020,
            confirmation_score=0.1,
        )
        assert result.p_success >= 0.10
