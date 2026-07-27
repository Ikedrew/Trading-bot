"""
Tests for stability_policy field in decision audit output.

Verifies:
- stability_policy appears in audit payload
- Existing audit fields remain unchanged
- Missing attribute falls back to "NORMAL_MODE"
- Audit output remains serializable
- No execution path changes occur

Pure observability enhancement validation.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from enum import Enum

from core.decision_audit import _build_audit_record


# ─── TEST FIXTURES ────────────────────────────────────────────────────────────


class _Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


def _make_decision(stability_policy=None, **kwargs):
    """Create a minimal UnifiedDecision-like object for audit testing."""
    defaults = {
        "should_trade": True,
        "reason": "approved",
        "bias": _Side.BUY,
        "score": 8,
        "bias_phase": "MATURE",
        "bias_validation_score": 0.9,
        "structure_ok": True,
        "patterns": ["bullish_engulfing"],
        "intent": None,
    }
    defaults.update(kwargs)
    inner_decision = SimpleNamespace(**defaults)

    bar_ctx = SimpleNamespace(bid=1.10000, ask=1.10020)
    confirmation = SimpleNamespace(evaluated=True, passed=True, strength="STRONG", body_pct=0.8, wick_ratio=0.2, close_location=0.9, reason=None)

    decision = SimpleNamespace(
        decision=inner_decision,
        bar_context=bar_ctx,
        confirmation=confirmation,
        last_completed_stage="complete",
    )

    if stability_policy is not None:
        decision.stability_policy = stability_policy

    return decision


def _make_engine_state():
    return SimpleNamespace(
        current_bias=_Side.BUY,
        bias_phase="MATURE",
        bias_strength=0.85,
        bias_age_seconds=300.0,
        regime_state="trending",
        volatility_filter=0.1,
        bias_confirmation_score=0.9,
        bias_confirmation_count=3,
        bias_contradiction_count=0,
    )


def _make_candles(n=10):
    return [SimpleNamespace(time=1000 + i, open=1.1, high=1.102, low=1.098, close=1.101) for i in range(n)]


# ─── TESTS: stability_policy IN AUDIT ─────────────────────────────────────────


class TestStabilityPolicyInAudit:
    def test_stability_policy_appears_in_record(self):
        decision = _make_decision(stability_policy="RUNNER_MODE")
        record = _build_audit_record(
            symbol="EURUSD",
            cycle_id=1,
            decision=decision,
            engine_state=_make_engine_state(),
            candles=_make_candles(),
            closed_i=5,
            runtime_mode="LIVE",
        )
        assert "stability_policy" in record
        assert record["stability_policy"] == "RUNNER_MODE"

    def test_protect_mode_in_record(self):
        decision = _make_decision(stability_policy="PROTECT_MODE")
        record = _build_audit_record(
            symbol="GBPUSD",
            cycle_id=2,
            decision=decision,
            engine_state=_make_engine_state(),
            candles=_make_candles(),
            closed_i=5,
            runtime_mode="LIVE",
        )
        assert record["stability_policy"] == "PROTECT_MODE"

    def test_block_mode_in_record(self):
        decision = _make_decision(stability_policy="BLOCK_MODE")
        record = _build_audit_record(
            symbol="USDJPY",
            cycle_id=3,
            decision=decision,
            engine_state=_make_engine_state(),
            candles=_make_candles(),
            closed_i=5,
            runtime_mode="LIVE",
        )
        assert record["stability_policy"] == "BLOCK_MODE"

    def test_normal_mode_in_record(self):
        decision = _make_decision(stability_policy="NORMAL_MODE")
        record = _build_audit_record(
            symbol="EURUSD",
            cycle_id=4,
            decision=decision,
            engine_state=_make_engine_state(),
            candles=_make_candles(),
            closed_i=5,
            runtime_mode="LIVE",
        )
        assert record["stability_policy"] == "NORMAL_MODE"


class TestFallbackBehavior:
    def test_missing_attribute_falls_back_to_normal_mode(self):
        """Decision without stability_policy attribute uses NORMAL_MODE."""
        decision = _make_decision()  # No stability_policy set
        # Remove the attribute if accidentally set
        if hasattr(decision, "stability_policy"):
            delattr(decision, "stability_policy")

        record = _build_audit_record(
            symbol="EURUSD",
            cycle_id=5,
            decision=decision,
            engine_state=_make_engine_state(),
            candles=_make_candles(),
            closed_i=5,
            runtime_mode="LIVE",
        )
        assert record["stability_policy"] == "NORMAL_MODE"


class TestExistingFieldsUnchanged:
    def test_existing_fields_remain_present(self):
        decision = _make_decision(stability_policy="RUNNER_MODE")
        record = _build_audit_record(
            symbol="EURUSD",
            cycle_id=6,
            decision=decision,
            engine_state=_make_engine_state(),
            candles=_make_candles(),
            closed_i=5,
            runtime_mode="LIVE",
        )
        # Core fields still present and correct
        assert record["symbol"] == "EURUSD"
        assert record["runtime_mode"] == "LIVE"
        assert record["cycle_id"] == 6
        assert record["should_trade"] is True
        assert record["score"] == 8
        assert record["side"] == "BUY"
        assert record["last_completed_stage"] == "complete"
        assert record["engine_state"]["regime_state"] == "trending"

    def test_confirmation_field_unchanged(self):
        decision = _make_decision(stability_policy="PROTECT_MODE")
        record = _build_audit_record(
            symbol="EURUSD",
            cycle_id=7,
            decision=decision,
            engine_state=_make_engine_state(),
            candles=_make_candles(),
            closed_i=5,
            runtime_mode="LIVE",
        )
        conf = record["confirmation"]
        assert conf is not None
        assert conf["strength"] == "STRONG"
        assert conf["body_pct"] == 0.8
        assert conf["wick_ratio"] == 0.2


class TestSerializability:
    def test_audit_record_is_json_serializable(self):
        decision = _make_decision(stability_policy="RUNNER_MODE")
        record = _build_audit_record(
            symbol="EURUSD",
            cycle_id=8,
            decision=decision,
            engine_state=_make_engine_state(),
            candles=_make_candles(),
            closed_i=5,
            runtime_mode="LIVE",
        )
        # Must not raise
        output = json.dumps(record, default=str)
        assert isinstance(output, str)
        assert len(output) > 0

    def test_serialized_output_contains_stability_policy(self):
        decision = _make_decision(stability_policy="BLOCK_MODE")
        record = _build_audit_record(
            symbol="EURUSD",
            cycle_id=9,
            decision=decision,
            engine_state=_make_engine_state(),
            candles=_make_candles(),
            closed_i=5,
            runtime_mode="LIVE",
        )
        output = json.dumps(record, default=str)
        parsed = json.loads(output)
        assert parsed["stability_policy"] == "BLOCK_MODE"


class TestNoExecutionPathChanges:
    def test_record_structure_identical_except_new_field(self):
        """Adding stability_policy does not remove or alter any existing keys."""
        decision_with = _make_decision(stability_policy="RUNNER_MODE")
        decision_without = _make_decision()
        if hasattr(decision_without, "stability_policy"):
            delattr(decision_without, "stability_policy")

        record_with = _build_audit_record(
            symbol="EURUSD", cycle_id=10, decision=decision_with,
            engine_state=_make_engine_state(), candles=_make_candles(),
            closed_i=5, runtime_mode="LIVE",
        )
        record_without = _build_audit_record(
            symbol="EURUSD", cycle_id=10, decision=decision_without,
            engine_state=_make_engine_state(), candles=_make_candles(),
            closed_i=5, runtime_mode="LIVE",
        )

        # Both have stability_policy (with falls back to NORMAL_MODE for without)
        assert "stability_policy" in record_with
        assert "stability_policy" in record_without

        # All other keys are identical (excluding timestamp which varies)
        keys_with = set(record_with.keys())
        keys_without = set(record_without.keys())
        assert keys_with == keys_without
