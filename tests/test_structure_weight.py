"""Tests for Structure Weight Multiplier (SWM) in ConfluenceEngine."""

from __future__ import annotations

import pytest

from core.voters.confluence_engine import compute_structure_weight, compute_confluence
from core.voters.types import VoteResult


class TestScoreBasedWeighting:
    """Score → weight factor mapping."""

    def test_low_score_dampens(self):
        # < 1.5 → 0.80
        w = compute_structure_weight(0.5, "BUILDING")
        assert 0.75 <= w <= 0.85

    def test_mid_low_score_mild_dampening(self):
        # 1.5–3.0 → 0.95
        w = compute_structure_weight(2.0, "BUILDING")
        assert 0.90 <= w <= 1.00

    def test_mid_high_score_boosts(self):
        # 3.0–4.5 → 1.10
        w = compute_structure_weight(3.5, "BUILDING")
        assert 1.05 <= w <= 1.15

    def test_high_score_strong_boost(self):
        # > 4.5 → 1.20
        w = compute_structure_weight(5.0, "BUILDING")
        assert 1.15 <= w <= 1.25


class TestRegimeOverrideLayer:
    """Regime modifies the score-based weight."""

    def test_weak_reduces(self):
        w_weak = compute_structure_weight(3.0, "WEAK")
        w_building = compute_structure_weight(3.0, "BUILDING")
        assert w_weak < w_building

    def test_building_neutral(self):
        # BUILDING = 1.00 → no change from score factor alone
        w = compute_structure_weight(3.0, "BUILDING")
        # score_factor for 3.0 = 1.10, regime = 1.00 → 1.10
        assert w == pytest.approx(1.10, abs=0.01)

    def test_confirmed_amplifies(self):
        w_confirmed = compute_structure_weight(3.0, "CONFIRMED")
        w_building = compute_structure_weight(3.0, "BUILDING")
        assert w_confirmed > w_building

    def test_invalid_strongly_dampens(self):
        w = compute_structure_weight(4.0, "INVALID")
        # 1.10 * 0.70 = 0.77
        assert w < 0.80


class TestBounds:
    """SWM always within [0.60, 1.25]."""

    def test_floor_enforced(self):
        # Worst case: score < 1.5 (0.80) × INVALID (0.70) = 0.56 → clamped to 0.60
        w = compute_structure_weight(0.0, "INVALID")
        assert w == 0.60

    def test_ceiling_enforced(self):
        # Best case: score > 4.5 (1.20) × CONFIRMED (1.15) = 1.38 → clamped to 1.25
        w = compute_structure_weight(5.0, "CONFIRMED")
        assert w == 1.25

    def test_never_negative(self):
        for s in [-5.0, -1.0, 0.0, 1.0, 5.0]:
            for r in ["WEAK", "BUILDING", "CONFIRMED", "INVALID"]:
                assert compute_structure_weight(s, r) > 0

    def test_unknown_regime_neutral(self):
        w = compute_structure_weight(3.0, "UNKNOWN")
        w_building = compute_structure_weight(3.0, "BUILDING")
        assert w == w_building


class TestMonotonicity:
    """Increasing structure_score never reduces weight (same regime)."""

    def test_monotonic_building(self):
        scores = [0.0, 1.0, 1.5, 2.0, 3.0, 4.0, 4.5, 5.0]
        weights = [compute_structure_weight(s, "BUILDING") for s in scores]
        for i in range(len(weights) - 1):
            assert weights[i] <= weights[i + 1]

    def test_monotonic_confirmed(self):
        scores = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        weights = [compute_structure_weight(s, "CONFIRMED") for s in scores]
        for i in range(len(weights) - 1):
            assert weights[i] <= weights[i + 1]


class TestConfluenceIntegration:
    """SWM correctly modifies confluence output."""

    def test_strong_structure_amplifies_score(self):
        votes = dict(
            bias_vote=VoteResult(score=1.5, confidence=0.9, reason="confirmed"),
            structure_vote=VoteResult(score=1.2, confidence=0.8, reason="clear"),
            volatility_vote=VoteResult(score=0.5, confidence=0.7, reason="ok"),
            spread_vote=VoteResult(score=0.3, confidence=0.6, reason="ok"),
            session_vote=VoteResult(score=0.2, confidence=0.5, reason="ok"),
        )
        # Weak structure
        r_weak = compute_confluence(**votes, structure_score=0.5, structure_regime="WEAK")
        # Strong structure
        r_strong = compute_confluence(**votes, structure_score=5.0, structure_regime="CONFIRMED")
        assert abs(r_strong.score) > abs(r_weak.score)

    def test_invalid_structure_dampens_score(self):
        votes = dict(
            bias_vote=VoteResult(score=1.5, confidence=0.9, reason="confirmed"),
            structure_vote=VoteResult(score=1.2, confidence=0.8, reason="clear"),
            volatility_vote=VoteResult(score=0.5, confidence=0.7, reason="ok"),
            spread_vote=VoteResult(score=0.3, confidence=0.6, reason="ok"),
            session_vote=VoteResult(score=0.2, confidence=0.5, reason="ok"),
        )
        r_building = compute_confluence(**votes, structure_score=3.0, structure_regime="BUILDING")
        r_invalid = compute_confluence(**votes, structure_score=0.5, structure_regime="INVALID")
        assert abs(r_invalid.score) < abs(r_building.score)

    def test_backward_compatible_without_structure_params(self):
        """When structure_score/regime not passed, SWM is 1.0 (neutral)."""
        result = compute_confluence(
            bias_vote=VoteResult(score=1.5, confidence=0.9, reason="confirmed"),
            structure_vote=VoteResult(score=1.2, confidence=0.8, reason="clear"),
            volatility_vote=VoteResult(score=0.8, confidence=0.7, reason="ok"),
            spread_vote=VoteResult(score=0.6, confidence=0.9, reason="ok"),
            session_vote=VoteResult(score=0.8, confidence=0.9, reason="ok"),
        )
        # Without structure params, SWM = 1.0 → same as original behaviour
        assert result.action == "BUY"
        assert result.breakdown.get("structure_weight_multiplier") == 1.0

    def test_breakdown_contains_swm(self):
        result = compute_confluence(
            bias_vote=VoteResult(score=1.0, confidence=0.8, reason="ok"),
            structure_score=3.5,
            structure_regime="CONFIRMED",
        )
        assert "structure_weight_multiplier" in result.breakdown
