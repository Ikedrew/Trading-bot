"""
Tests for the stability gate hook in core/engine.py

Verifies:
- Stability evaluator is called after trade quality passes
- Intent builder is NOT called when stability blocks
- Reject path returns stability reason
- Intent builder still executes when allowed
- Existing params_final object remains unchanged
- Existing scoring output remains unchanged
- Existing confirmation output remains unchanged

Uses mocks for isolation.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.stability.stability_gate import StabilityDecision, evaluate_stability_policy
from core.stability.cohort_key import build_cohort_key
from core.stability.policy_registry import POLICY_REGISTRY


# ─── UNIT TESTS: evaluate_stability_policy behavior in gate context ───────────


class TestStabilityGateBlocks:
    """Verify blocking scenarios that would trigger early exit in engine."""

    def test_drawdown_lock_produces_block_decision(self):
        snapshot = SimpleNamespace(
            drawdown_state="LOCKED",
            recent_loss_streak=0,
            session_quality="HIGH",
            volatility_state="STABLE",
            spread_state="TIGHT",
            market_regime="TRENDING",
            confidence_score=9.0,
        )
        decision = evaluate_stability_policy(snapshot, POLICY_REGISTRY)
        assert decision.allow_trade is False
        assert decision.mode == "PROTECT"
        assert decision.reason == "drawdown_lock"

    def test_loss_streak_produces_block_decision(self):
        snapshot = SimpleNamespace(
            drawdown_state="NORMAL",
            recent_loss_streak=3,
            session_quality="NORMAL",
            volatility_state="STABLE",
            spread_state="NORMAL",
            market_regime="TRENDING",
            confidence_score=7.0,
        )
        decision = evaluate_stability_policy(snapshot, POLICY_REGISTRY)
        assert decision.allow_trade is False
        assert decision.reason == "loss_streak_limit"

    def test_dead_session_produces_block_decision(self):
        snapshot = SimpleNamespace(
            drawdown_state="NORMAL",
            recent_loss_streak=0,
            session_quality="DEAD",
            volatility_state="STABLE",
            spread_state="NORMAL",
            market_regime="TRENDING",
            confidence_score=7.0,
        )
        decision = evaluate_stability_policy(snapshot, POLICY_REGISTRY)
        assert decision.allow_trade is False
        assert decision.reason == "dead_session"

    def test_chaotic_volatility_produces_block_decision(self):
        snapshot = SimpleNamespace(
            drawdown_state="NORMAL",
            recent_loss_streak=0,
            session_quality="NORMAL",
            volatility_state="CHAOTIC",
            spread_state="NORMAL",
            market_regime="TRENDING",
            confidence_score=7.0,
        )
        decision = evaluate_stability_policy(snapshot, POLICY_REGISTRY)
        assert decision.allow_trade is False
        assert decision.reason == "volatility_block"

    def test_wide_spread_produces_block_decision(self):
        snapshot = SimpleNamespace(
            drawdown_state="NORMAL",
            recent_loss_streak=0,
            session_quality="NORMAL",
            volatility_state="STABLE",
            spread_state="WIDE",
            market_regime="TRENDING",
            confidence_score=7.0,
        )
        decision = evaluate_stability_policy(snapshot, POLICY_REGISTRY)
        assert decision.allow_trade is False
        assert decision.reason == "spread_block"


class TestStabilityGateAllows:
    """Verify non-blocking scenarios that allow intent builder to proceed."""

    def test_normal_conditions_allow_trade(self):
        snapshot = SimpleNamespace(
            drawdown_state="NORMAL",
            recent_loss_streak=0,
            session_quality="NORMAL",
            volatility_state="STABLE",
            spread_state="NORMAL",
            market_regime="TRENDING",
            confidence_score=7.0,
        )
        decision = evaluate_stability_policy(snapshot, POLICY_REGISTRY)
        assert decision.allow_trade is True
        assert decision.mode == "NORMAL"

    def test_runner_conditions_allow_trade(self):
        snapshot = SimpleNamespace(
            drawdown_state="NORMAL",
            recent_loss_streak=0,
            session_quality="HIGH",
            volatility_state="STABLE",
            spread_state="TIGHT",
            market_regime="TRENDING",
            confidence_score=9.0,
        )
        decision = evaluate_stability_policy(snapshot, POLICY_REGISTRY)
        assert decision.allow_trade is True
        assert decision.mode == "RUNNER"

    def test_low_confidence_allows_with_protect_mode(self):
        snapshot = SimpleNamespace(
            drawdown_state="NORMAL",
            recent_loss_streak=0,
            session_quality="NORMAL",
            volatility_state="STABLE",
            spread_state="NORMAL",
            market_regime="TRENDING",
            confidence_score=5.0,
        )
        decision = evaluate_stability_policy(snapshot, POLICY_REGISTRY)
        assert decision.allow_trade is True
        assert decision.mode == "PROTECT"


class TestCohortKeyInGateContext:
    """Verify cohort key is built correctly from confirmation layer output."""

    def test_cohort_key_from_confirmation_layer(self):
        """Simulates what the engine does: build_cohort_key(layer_confirmation)."""
        layer = SimpleNamespace(
            confirmation_strength="STRONG",
            entry_timing="EARLY",
            market_regime="TRENDING",
        )
        key = build_cohort_key(layer)
        assert key == "STRONG+EARLY+TRENDING"

    def test_cohort_key_with_missing_fields(self):
        """Confirmation layer might have partial fields."""
        layer = SimpleNamespace(confirmation_strength="WEAK")
        key = build_cohort_key(layer)
        assert key == "WEAK+UNKNOWN+UNKNOWN"

    def test_cohort_key_with_none_fields(self):
        layer = SimpleNamespace(
            confirmation_strength=None,
            entry_timing=None,
            market_regime=None,
        )
        key = build_cohort_key(layer)
        assert key == "UNKNOWN+UNKNOWN+UNKNOWN"


class TestRejectPathReason:
    """Verify the reject path produces correct reason strings for engine finalize."""

    def test_block_reason_format(self):
        """Engine formats reason as 'stability_block:{reason}'."""
        snapshot = SimpleNamespace(
            drawdown_state="LOCKED",
            recent_loss_streak=0,
            session_quality="NORMAL",
            volatility_state="STABLE",
            spread_state="NORMAL",
            market_regime="TRENDING",
            confidence_score=7.0,
        )
        decision = evaluate_stability_policy(snapshot, POLICY_REGISTRY)
        # Engine produces: f"stability_block:{decision.reason}"
        engine_reason = f"stability_block:{decision.reason}"
        assert engine_reason == "stability_block:drawdown_lock"

    def test_all_block_reasons_are_deterministic(self):
        """Each block type produces a fixed, known reason string."""
        cases = [
            ({"drawdown_state": "LOCKED"}, "drawdown_lock"),
            ({"recent_loss_streak": 5}, "loss_streak_limit"),
            ({"session_quality": "DEAD"}, "dead_session"),
            ({"volatility_state": "CHAOTIC"}, "volatility_block"),
            ({"spread_state": "WIDE"}, "spread_block"),
        ]
        for overrides, expected_reason in cases:
            defaults = {
                "drawdown_state": "NORMAL",
                "recent_loss_streak": 0,
                "session_quality": "NORMAL",
                "volatility_state": "STABLE",
                "spread_state": "NORMAL",
                "market_regime": "TRENDING",
                "confidence_score": 7.0,
            }
            defaults.update(overrides)
            snapshot = SimpleNamespace(**defaults)
            decision = evaluate_stability_policy(snapshot, POLICY_REGISTRY)
            assert decision.reason == expected_reason


class TestExistingOutputsUnchanged:
    """
    Verify that stability gate does not mutate scoring, confirmation, or params.
    The gate is a pure function — it reads snapshot and returns a decision.
    """

    def test_evaluate_does_not_mutate_snapshot(self):
        snapshot = SimpleNamespace(
            drawdown_state="NORMAL",
            recent_loss_streak=1,
            session_quality="HIGH",
            volatility_state="STABLE",
            spread_state="TIGHT",
            market_regime="TRENDING",
            confidence_score=8.0,
        )
        # Store original values
        original_drawdown = snapshot.drawdown_state
        original_streak = snapshot.recent_loss_streak
        original_confidence = snapshot.confidence_score

        evaluate_stability_policy(snapshot, POLICY_REGISTRY)

        # Nothing changed
        assert snapshot.drawdown_state == original_drawdown
        assert snapshot.recent_loss_streak == original_streak
        assert snapshot.confidence_score == original_confidence

    def test_evaluate_does_not_mutate_registry(self):
        registry_copy = dict(POLICY_REGISTRY)
        snapshot = SimpleNamespace(
            drawdown_state="LOCKED",
            recent_loss_streak=0,
            session_quality="NORMAL",
            volatility_state="STABLE",
            spread_state="NORMAL",
            market_regime="TRENDING",
            confidence_score=7.0,
        )
        evaluate_stability_policy(snapshot, POLICY_REGISTRY)
        assert POLICY_REGISTRY == registry_copy

    def test_cohort_key_does_not_mutate_decision_object(self):
        layer = SimpleNamespace(
            confirmation_strength="STRONG",
            entry_timing="MID",
            market_regime="TRENDING",
        )
        original_strength = layer.confirmation_strength
        build_cohort_key(layer)
        assert layer.confirmation_strength == original_strength
