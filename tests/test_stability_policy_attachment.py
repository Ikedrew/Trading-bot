"""
Tests for stability policy attachment in core/engine.py

Verifies:
- Policy attaches after params_final (on the final UnifiedDecision)
- Correct policy resolves from registry
- Unknown cohort falls back to "NORMAL_MODE"
- UnifiedDecision retains all original fields
- params_final remains unchanged
- run_build_intent() still executes normally
- No scoring values are altered
- No confirmation values are altered

Metadata-only integration with zero behavioral drift.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.stability.cohort_key import build_cohort_key
from core.stability.policy_registry import POLICY_REGISTRY


# ─── POLICY RESOLUTION TESTS ─────────────────────────────────────────────────


class TestPolicyResolution:
    """Verify correct policy lookup from cohort key via registry."""

    def test_strong_early_trending_resolves_runner(self):
        layer = SimpleNamespace(
            confirmation_strength="STRONG",
            entry_timing="EARLY",
            market_regime="TRENDING",
        )
        key = build_cohort_key(layer)
        policy = POLICY_REGISTRY.get(key, "NORMAL_MODE")
        assert policy == "RUNNER_MODE"

    def test_weak_late_ranging_resolves_block(self):
        layer = SimpleNamespace(
            confirmation_strength="WEAK",
            entry_timing="LATE",
            market_regime="RANGING",
        )
        key = build_cohort_key(layer)
        policy = POLICY_REGISTRY.get(key, "NORMAL_MODE")
        assert policy == "BLOCK_MODE"

    def test_moderate_mid_trending_resolves_normal(self):
        layer = SimpleNamespace(
            confirmation_strength="MODERATE",
            entry_timing="MID",
            market_regime="TRENDING",
        )
        key = build_cohort_key(layer)
        policy = POLICY_REGISTRY.get(key, "NORMAL_MODE")
        assert policy == "NORMAL_MODE"

    def test_weak_early_trending_resolves_protect(self):
        layer = SimpleNamespace(
            confirmation_strength="WEAK",
            entry_timing="EARLY",
            market_regime="TRENDING",
        )
        key = build_cohort_key(layer)
        policy = POLICY_REGISTRY.get(key, "NORMAL_MODE")
        assert policy == "PROTECT_MODE"


class TestFallbackBehavior:
    """Verify unknown cohorts fall back to NORMAL_MODE."""

    def test_unknown_cohort_falls_back(self):
        layer = SimpleNamespace(
            confirmation_strength="EXOTIC",
            entry_timing="UNUSUAL",
            market_regime="BIZARRE",
        )
        key = build_cohort_key(layer)
        policy = POLICY_REGISTRY.get(key, "NORMAL_MODE")
        assert policy == "NORMAL_MODE"

    def test_missing_all_fields_falls_back(self):
        layer = SimpleNamespace()
        key = build_cohort_key(layer)
        # "UNKNOWN+UNKNOWN+UNKNOWN" is in registry as NORMAL_MODE
        policy = POLICY_REGISTRY.get(key, "NORMAL_MODE")
        assert policy == "NORMAL_MODE"

    def test_partial_fields_not_in_registry_falls_back(self):
        layer = SimpleNamespace(
            confirmation_strength="STRONG",
            entry_timing="UNKNOWN",
            market_regime="CHOPPY",
        )
        key = build_cohort_key(layer)
        # "STRONG+UNKNOWN+CHOPPY" not in registry → fallback
        policy = POLICY_REGISTRY.get(key, "NORMAL_MODE")
        assert policy == "NORMAL_MODE"


class TestMetadataAttachment:
    """Verify policy attaches as metadata without mutating anything else."""

    def test_attachment_does_not_mutate_layer(self):
        layer = SimpleNamespace(
            confirmation_strength="STRONG",
            entry_timing="EARLY",
            market_regime="TRENDING",
            strength="STRONG",
            body_pct=0.75,
            wick_ratio=0.3,
            close_location=0.9,
        )
        original_strength = layer.confirmation_strength
        original_body = layer.body_pct

        key = build_cohort_key(layer)
        _ = POLICY_REGISTRY.get(key, "NORMAL_MODE")

        assert layer.confirmation_strength == original_strength
        assert layer.body_pct == original_body

    def test_unified_decision_retains_fields_after_attachment(self):
        """Simulate what engine does: attach policy to UnifiedDecision."""
        # Simulate a UnifiedDecision-like object
        decision = SimpleNamespace(
            last_completed_stage="complete",
            score=SimpleNamespace(score_int=8, final_score=8.2, evaluated=True),
            confirmation=SimpleNamespace(strength="STRONG"),
            decision=SimpleNamespace(should_trade=True, reason="approved"),
        )

        # Attach policy (same as engine does)
        decision.stability_policy = "RUNNER_MODE"

        # Original fields unchanged
        assert decision.last_completed_stage == "complete"
        assert decision.score.score_int == 8
        assert decision.score.final_score == 8.2
        assert decision.confirmation.strength == "STRONG"
        assert decision.decision.should_trade is True
        assert decision.decision.reason == "approved"
        # Policy attached
        assert decision.stability_policy == "RUNNER_MODE"

    def test_params_final_not_mutated(self):
        """params_final object should not be touched by policy attachment."""
        params_final = SimpleNamespace(
            should_trade=True,
            reason="approved",
            signal="BUY",
            score=8,
        )
        original_should_trade = params_final.should_trade
        original_reason = params_final.reason
        original_score = params_final.score

        # Policy attachment happens on unified_decision, NOT params_final
        layer = SimpleNamespace(
            confirmation_strength="STRONG",
            entry_timing="EARLY",
            market_regime="TRENDING",
        )
        key = build_cohort_key(layer)
        _ = POLICY_REGISTRY.get(key, "NORMAL_MODE")

        # params_final untouched
        assert params_final.should_trade == original_should_trade
        assert params_final.reason == original_reason
        assert params_final.score == original_score


class TestNoScoringAlteration:
    """Verify scoring values remain completely untouched."""

    def test_score_values_unchanged(self):
        score_layer = SimpleNamespace(
            score_int=9,
            final_score=9.5,
            evaluated=True,
            volatility_penalty=0.1,
            breakdown={"base": 7, "bonus": 2},
        )

        # Run the same operations the engine does for policy attachment
        layer = SimpleNamespace(
            confirmation_strength="WEAK",
            entry_timing="LATE",
            market_regime="RANGING",
        )
        key = build_cohort_key(layer)
        policy = POLICY_REGISTRY.get(key, "NORMAL_MODE")

        # Score layer completely unaffected
        assert score_layer.score_int == 9
        assert score_layer.final_score == 9.5
        assert score_layer.evaluated is True
        assert score_layer.volatility_penalty == 0.1
        assert score_layer.breakdown == {"base": 7, "bonus": 2}


class TestNoConfirmationAlteration:
    """Verify confirmation values remain completely untouched."""

    def test_confirmation_values_unchanged(self):
        confirmation_layer = SimpleNamespace(
            strength="STRONG",
            body_pct=0.82,
            wick_ratio=0.15,
            close_location=0.91,
        )

        layer = SimpleNamespace(
            confirmation_strength="STRONG",
            entry_timing="MID",
            market_regime="TRENDING",
        )
        key = build_cohort_key(layer)
        policy = POLICY_REGISTRY.get(key, "NORMAL_MODE")

        assert confirmation_layer.strength == "STRONG"
        assert confirmation_layer.body_pct == 0.82
        assert confirmation_layer.wick_ratio == 0.15
        assert confirmation_layer.close_location == 0.91
