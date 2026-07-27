"""
Tests for core/stability/stability_gate.py

100% deterministic. No mocking required.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.stability.stability_gate import StabilityDecision, evaluate_stability_policy


# ─── DEFAULT POLICY REGISTRY ─────────────────────────────────────────────────

DEFAULT_REGISTRY = {
    "max_loss_streak": 3,
    "blocked_drawdown_states": ["LOCKED"],
    "blocked_sessions": ["DEAD"],
    "blocked_volatility": ["CHAOTIC"],
    "blocked_spread": ["WIDE"],
    "runner_confidence_min": 8.5,
    "protect_confidence_max": 6.0,
}


def _make_snapshot(**kwargs) -> SimpleNamespace:
    """Create a snapshot-like object with given attributes."""
    defaults = {
        "drawdown_state": "NORMAL",
        "recent_loss_streak": 0,
        "session_quality": "NORMAL",
        "volatility_state": "STABLE",
        "spread_state": "NORMAL",
        "market_regime": "UNKNOWN",
        "trade_frequency_state": "NORMAL",
        "confidence_score": 7.0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ─── A. HARD BLOCK TESTS ─────────────────────────────────────────────────────


class TestDrawdownLockBlock:
    def test_drawdown_locked_blocks_trade(self):
        snapshot = _make_snapshot(drawdown_state="LOCKED")
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.allow_trade is False
        assert result.mode == "PROTECT"
        assert result.reason == "drawdown_lock"

    def test_drawdown_normal_does_not_block(self):
        snapshot = _make_snapshot(drawdown_state="NORMAL")
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.allow_trade is True


class TestLossStreakBlock:
    def test_loss_streak_at_limit_blocks(self):
        snapshot = _make_snapshot(recent_loss_streak=3)
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.allow_trade is False
        assert result.mode == "PROTECT"
        assert result.reason == "loss_streak_limit"

    def test_loss_streak_above_limit_blocks(self):
        snapshot = _make_snapshot(recent_loss_streak=5)
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.allow_trade is False
        assert result.reason == "loss_streak_limit"

    def test_loss_streak_below_limit_passes(self):
        snapshot = _make_snapshot(recent_loss_streak=2)
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.allow_trade is True


class TestDeadSessionBlock:
    def test_dead_session_blocks(self):
        snapshot = _make_snapshot(session_quality="DEAD")
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.allow_trade is False
        assert result.mode == "PROTECT"
        assert result.reason == "dead_session"

    def test_normal_session_passes(self):
        snapshot = _make_snapshot(session_quality="NORMAL")
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.allow_trade is True


class TestVolatilityBlock:
    def test_chaotic_volatility_blocks(self):
        snapshot = _make_snapshot(volatility_state="CHAOTIC")
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.allow_trade is False
        assert result.mode == "PROTECT"
        assert result.reason == "volatility_block"

    def test_stable_volatility_passes(self):
        snapshot = _make_snapshot(volatility_state="STABLE")
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.allow_trade is True


class TestSpreadBlock:
    def test_wide_spread_blocks(self):
        snapshot = _make_snapshot(spread_state="WIDE")
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.allow_trade is False
        assert result.mode == "PROTECT"
        assert result.reason == "spread_block"

    def test_normal_spread_passes(self):
        snapshot = _make_snapshot(spread_state="NORMAL")
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.allow_trade is True


# ─── B. RUNNER PATH TESTS ────────────────────────────────────────────────────


class TestRunnerPath:
    def test_all_runner_conditions_met(self):
        snapshot = _make_snapshot(
            confidence_score=9.0,
            market_regime="TRENDING",
            session_quality="HIGH",
            volatility_state="STABLE",
            spread_state="TIGHT",
        )
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.allow_trade is True
        assert result.mode == "RUNNER"
        assert result.reason == "high_stability"

    def test_runner_fails_if_confidence_too_low(self):
        snapshot = _make_snapshot(
            confidence_score=8.0,
            market_regime="TRENDING",
            session_quality="HIGH",
            volatility_state="STABLE",
            spread_state="TIGHT",
        )
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        # confidence 8.0 > protect_confidence_max 6.0, so NORMAL not RUNNER
        assert result.mode != "RUNNER"

    def test_runner_fails_if_not_trending(self):
        snapshot = _make_snapshot(
            confidence_score=9.0,
            market_regime="RANGING",
            session_quality="HIGH",
            volatility_state="STABLE",
            spread_state="TIGHT",
        )
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.mode != "RUNNER"

    def test_runner_fails_if_session_not_high(self):
        snapshot = _make_snapshot(
            confidence_score=9.0,
            market_regime="TRENDING",
            session_quality="NORMAL",
            volatility_state="STABLE",
            spread_state="TIGHT",
        )
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.mode != "RUNNER"

    def test_runner_fails_if_spread_not_tight(self):
        snapshot = _make_snapshot(
            confidence_score=9.0,
            market_regime="TRENDING",
            session_quality="HIGH",
            volatility_state="STABLE",
            spread_state="NORMAL",
        )
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.mode != "RUNNER"


# ─── C. PROTECTIVE MODE TESTS ────────────────────────────────────────────────


class TestProtectiveMode:
    def test_low_confidence_triggers_protect(self):
        snapshot = _make_snapshot(confidence_score=5.0)
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.allow_trade is True
        assert result.mode == "PROTECT"
        assert result.reason == "low_confidence"

    def test_confidence_at_threshold_triggers_protect(self):
        snapshot = _make_snapshot(confidence_score=6.0)
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.allow_trade is True
        assert result.mode == "PROTECT"
        assert result.reason == "low_confidence"

    def test_confidence_above_threshold_is_normal(self):
        snapshot = _make_snapshot(confidence_score=7.0)
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.allow_trade is True
        assert result.mode == "NORMAL"


# ─── D. DEFAULT PATH TESTS ───────────────────────────────────────────────────


class TestDefaultPath:
    def test_normal_conditions_return_stable(self):
        snapshot = _make_snapshot(confidence_score=7.5)
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.allow_trade is True
        assert result.mode == "NORMAL"
        assert result.reason == "stable"


# ─── E. SAFE DEFAULTS TESTS ──────────────────────────────────────────────────


class TestSafeDefaults:
    def test_missing_snapshot_attributes_use_defaults(self):
        """Snapshot with no attributes at all should not crash."""
        snapshot = SimpleNamespace()  # No attributes
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        # Should return NORMAL with safe defaults (confidence defaults to 5.0 <= 6.0 → PROTECT)
        assert result.allow_trade is True
        assert result.mode == "PROTECT"
        assert result.reason == "low_confidence"

    def test_empty_registry_uses_defaults(self):
        """Empty registry should use safe built-in defaults."""
        snapshot = _make_snapshot(confidence_score=7.0)
        result = evaluate_stability_policy(snapshot, {})
        assert result.allow_trade is True
        assert result.mode == "NORMAL"
        assert result.reason == "stable"

    def test_partial_snapshot_does_not_crash(self):
        """Snapshot with only some attributes works."""
        snapshot = SimpleNamespace(drawdown_state="NORMAL", confidence_score=9.0)
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.allow_trade is True

    def test_partial_registry_does_not_crash(self):
        """Registry with only some keys works."""
        snapshot = _make_snapshot(recent_loss_streak=5)
        partial = {"max_loss_streak": 3}
        result = evaluate_stability_policy(snapshot, partial)
        assert result.allow_trade is False
        assert result.reason == "loss_streak_limit"


# ─── F. PRIORITY ORDER TESTS ─────────────────────────────────────────────────


class TestPriorityOrder:
    def test_drawdown_takes_priority_over_loss_streak(self):
        """Drawdown lock checked before loss streak."""
        snapshot = _make_snapshot(drawdown_state="LOCKED", recent_loss_streak=5)
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.reason == "drawdown_lock"

    def test_loss_streak_takes_priority_over_dead_session(self):
        snapshot = _make_snapshot(recent_loss_streak=4, session_quality="DEAD")
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.reason == "loss_streak_limit"

    def test_dead_session_takes_priority_over_volatility(self):
        snapshot = _make_snapshot(session_quality="DEAD", volatility_state="CHAOTIC")
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.reason == "dead_session"

    def test_volatility_takes_priority_over_spread(self):
        snapshot = _make_snapshot(volatility_state="CHAOTIC", spread_state="WIDE")
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.reason == "volatility_block"

    def test_blocks_take_priority_over_runner(self):
        """Even with perfect runner conditions, a block wins."""
        snapshot = _make_snapshot(
            drawdown_state="LOCKED",
            confidence_score=9.5,
            market_regime="TRENDING",
            session_quality="HIGH",
            volatility_state="STABLE",
            spread_state="TIGHT",
        )
        result = evaluate_stability_policy(snapshot, DEFAULT_REGISTRY)
        assert result.allow_trade is False
        assert result.reason == "drawdown_lock"
