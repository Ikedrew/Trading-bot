"""Tests for Structure Confidence Modifier — locks contract before integration."""

from __future__ import annotations

import pytest

from core.pipeline.structure_confidence import compute_structure_modifier


class TestScoreBandMapping:
    """Score → modifier mapping correctness."""

    def test_low_score_produces_suppression(self):
        # structure_score < 1.5 → score_factor = 0.70
        mod = compute_structure_modifier(0.5, "BUILDING")
        assert 0.60 <= mod <= 0.80

    def test_mid_low_score_produces_mild_suppression(self):
        # 1.5 ≤ structure_score < 3.0 → score_factor = 0.90
        mod = compute_structure_modifier(2.0, "BUILDING")
        assert 0.85 <= mod <= 0.95

    def test_mid_high_score_produces_mild_amplification(self):
        # 3.0 ≤ structure_score < 4.5 → score_factor = 1.05
        mod = compute_structure_modifier(3.5, "BUILDING")
        assert 1.00 <= mod <= 1.10

    def test_high_score_produces_amplification(self):
        # structure_score ≥ 4.5 → score_factor = 1.15
        mod = compute_structure_modifier(5.0, "BUILDING")
        assert 1.10 <= mod <= 1.25


class TestRegimeOverride:
    """Regime override correctness."""

    def test_weak_reduces_multiplier(self):
        # WEAK → regime_factor = 0.85
        mod_weak = compute_structure_modifier(3.0, "WEAK")
        mod_building = compute_structure_modifier(3.0, "BUILDING")
        assert mod_weak < mod_building

    def test_building_is_neutral(self):
        # BUILDING → regime_factor = 1.00 (no change from score alone)
        mod = compute_structure_modifier(3.0, "BUILDING")
        # score_factor for 3.0 = 1.05, regime = 1.00 → 1.05
        assert mod == pytest.approx(1.05, abs=0.01)

    def test_confirmed_boosts(self):
        # CONFIRMED → regime_factor = 1.10
        mod_confirmed = compute_structure_modifier(3.0, "CONFIRMED")
        mod_building = compute_structure_modifier(3.0, "BUILDING")
        assert mod_confirmed > mod_building

    def test_invalid_strongly_suppresses(self):
        # INVALID → regime_factor = 0.60
        mod = compute_structure_modifier(4.0, "INVALID")
        assert mod < 0.70  # 1.05 * 0.60 = 0.63


class TestCombinedBehaviour:
    """Combined score + regime interactions."""

    def test_confirmed_high_score_is_strongest(self):
        # Highest possible: score ≥ 4.5 (1.15) × CONFIRMED (1.10) = 1.265 → clamped to 1.25
        mod = compute_structure_modifier(5.0, "CONFIRMED")
        assert mod >= 1.20  # Near or at ceiling

    def test_invalid_overrides_high_score(self):
        # Even with high score, INVALID suppresses
        mod = compute_structure_modifier(5.0, "INVALID")
        assert mod < 0.75  # 1.15 * 0.60 = 0.69

    def test_weak_low_score_is_weakest(self):
        # Lowest possible: score < 1.5 (0.70) × WEAK (0.85) = 0.595 → clamped to 0.595
        mod = compute_structure_modifier(0.5, "WEAK")
        assert mod <= 0.65

    def test_invalid_low_score_hits_floor(self):
        # 0.70 * 0.60 = 0.42 → clamped to 0.50
        mod = compute_structure_modifier(0.5, "INVALID")
        assert mod == 0.50  # Floor


class TestBoundaryStability:
    """No NaN, no negatives, deterministic outputs."""

    def test_never_returns_nan(self):
        for score in [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 10.0, -1.0]:
            for regime in ["WEAK", "BUILDING", "CONFIRMED", "INVALID", "UNKNOWN"]:
                mod = compute_structure_modifier(score, regime)
                assert mod == mod  # NaN != NaN

    def test_never_returns_negative(self):
        for score in [-5.0, -1.0, 0.0, 0.5, 1.0]:
            for regime in ["WEAK", "BUILDING", "CONFIRMED", "INVALID"]:
                mod = compute_structure_modifier(score, regime)
                assert mod > 0

    def test_always_within_bounds(self):
        for score in [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 4.5, 5.0, 10.0]:
            for regime in ["WEAK", "BUILDING", "CONFIRMED", "INVALID"]:
                mod = compute_structure_modifier(score, regime)
                assert 0.50 <= mod <= 1.25

    def test_deterministic(self):
        for _ in range(10):
            m1 = compute_structure_modifier(2.5, "BUILDING")
            m2 = compute_structure_modifier(2.5, "BUILDING")
            assert m1 == m2

    def test_unknown_regime_treated_as_neutral(self):
        mod = compute_structure_modifier(3.0, "UNKNOWN_REGIME")
        # Unknown regime → factor 1.00, same as BUILDING
        mod_building = compute_structure_modifier(3.0, "BUILDING")
        assert mod == mod_building


class TestMonotonicity:
    """Increasing structure_score must never reduce multiplier (same regime)."""

    def test_monotonic_within_building(self):
        scores = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
        modifiers = [compute_structure_modifier(s, "BUILDING") for s in scores]
        for i in range(len(modifiers) - 1):
            assert modifiers[i] <= modifiers[i + 1], (
                f"Monotonicity violated: score={scores[i]}→{scores[i+1]}, "
                f"mod={modifiers[i]}→{modifiers[i+1]}"
            )

    def test_monotonic_within_confirmed(self):
        scores = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        modifiers = [compute_structure_modifier(s, "CONFIRMED") for s in scores]
        for i in range(len(modifiers) - 1):
            assert modifiers[i] <= modifiers[i + 1]

    def test_monotonic_within_weak(self):
        scores = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        modifiers = [compute_structure_modifier(s, "WEAK") for s in scores]
        for i in range(len(modifiers) - 1):
            assert modifiers[i] <= modifiers[i + 1]

    def test_monotonic_within_invalid(self):
        scores = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        modifiers = [compute_structure_modifier(s, "INVALID") for s in scores]
        for i in range(len(modifiers) - 1):
            assert modifiers[i] <= modifiers[i + 1]
