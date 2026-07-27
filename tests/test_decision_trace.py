"""
Tests for DecisionTrace — Phase 1 observability.

Verifies:
- Trace is built from engine result without errors
- All exit paths produce valid traces
- Component diagnostics are correct
- Closest-flip calculation works
- Persistence writes JSONL
- Never raises on bad input
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch
from dataclasses import dataclass

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.decision_trace import (
    DecisionTrace,
    build_decision_trace,
    persist_decision_trace,
    _classify_terminal_stage,
    _compute_component_diagnostics,
    _compute_stages_reached,
)


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _make_no_trade_result(reason="ev_policy_blocked: NEGATIVE_EXPECTED_VALUE"):
    return {
        "action": "NO_TRADE",
        "reason": reason,
        "entity_id": "EURUSD_1700000000",
        "symbol": "EURUSD",
        "cycle_id": 42,
        "score": 0.42,
        "score_neutral": 0.38,
        "score_strategy": 0.42,
        "components": {
            "pattern_quality": 0.81, "bias_alignment": 0.42,
            "market_quality": 0.65, "trend_alignment": 0.30,
            "chop_clarity": 0.75, "volatility_quality": 0.80,
            "bias_stability": 0.70, "confirmation_pre": 0.85,
            "htf_alignment": 0.50, "h4_alignment": 0.45,
        },
        "pattern": "HAMMER",
        "strategy": None,
        "strategy_confidence": 0.0,
        "activation_regime": "TRANSITIONAL",
        "activation_regime_confidence": 0.35,
        "market_state": "TRANSITIONAL",
        "market_state_confidence": 0.45,
        "weights_used": "global_fallback",
        "ev": -0.00003,
        "ev_positive": False,
        "p_success": 0.22,
        "rr_effective": 1.8,
        "confirmation_score": 0.55,
        "policy_reasoning": "EV negative",
        "assessment": None,
    }


def _make_execute_result():
    return {
        "action": "EXECUTE",
        "reason": "",
        "entity_id": "EURUSD_1700000000",
        "symbol": "EURUSD",
        "cycle_id": 42,
        "score": 0.68,
        "score_neutral": 0.62,
        "score_strategy": 0.68,
        "components": {
            "pattern_quality": 1.0, "bias_alignment": 0.80,
            "market_quality": 0.70, "trend_alignment": 0.90,
            "chop_clarity": 0.80, "volatility_quality": 0.85,
            "bias_stability": 0.75, "confirmation_pre": 0.90,
            "htf_alignment": 0.88, "h4_alignment": 0.72,
        },
        "pattern": "BULLISH_ENGULFING",
        "strategy": "CONTINUATION",
        "strategy_confidence": 0.72,
        "activation_regime": "TRENDING",
        "activation_regime_confidence": 0.85,
        "market_state": "STRUCTURED",
        "market_state_confidence": 0.78,
        "weights_used": "strategy_specific",
        "ev": 0.000045,
        "ev_positive": True,
        "p_success": 0.35,
        "rr_effective": 2.1,
        "confirmation_score": 0.85,
        "policy_reasoning": "EV positive, RR sufficient",
        "assessment": None,
    }


def _make_no_pattern_result():
    return {
        "action": "NO_TRADE",
        "reason": "no_viable_pattern",
        "entity_id": "EURUSD_1700000000",
        "score": 0.0,
        "components": {},
        "pattern": None,
        "strategy": None,
        "strategy_confidence": 0.0,
        "assessment": None,
    }


# ─── TESTS ────────────────────────────────────────────────────────────────────

class TestStageClassification:
    def test_no_viable_pattern(self):
        assert _classify_terminal_stage("no_viable_pattern", "NO_TRADE") == "pattern_detection"

    def test_ev_policy_blocked(self):
        assert _classify_terminal_stage("ev_policy_blocked: NEGATIVE_EXPECTED_VALUE", "NO_TRADE") == "ev_policy"

    def test_risk_rejected(self):
        assert _classify_terminal_stage("risk_rejected: SLTP_CALCULATION_FAILED", "NO_TRADE") == "risk"

    def test_swing_blocked(self):
        assert _classify_terminal_stage("swing_blocked: no_bos_confirmed", "NO_TRADE") == "swing"

    def test_execute(self):
        assert _classify_terminal_stage("", "EXECUTE") == "execute"

    def test_score_below(self):
        assert _classify_terminal_stage("score_below_threshold (0.30 < 0.35)", "NO_TRADE") == "scoring"

    def test_policy_blocked(self):
        assert _classify_terminal_stage("policy_blocked: NEUTRAL_SCORE_BELOW_MINIMUM", "NO_TRADE") == "policy_pre"


class TestStagesReached:
    def test_execute_reaches_all(self):
        reached, passed = _compute_stages_reached("execute", "EXECUTE")
        assert "execute" in reached
        assert "execute" in passed
        assert len(passed) == len(reached)

    def test_pattern_stops_early(self):
        reached, passed = _compute_stages_reached("pattern_detection", "NO_TRADE")
        assert reached == ("pattern_detection",)
        assert passed == ()

    def test_ev_policy_reaches_most(self):
        reached, passed = _compute_stages_reached("ev_policy", "NO_TRADE")
        assert "ev_policy" in reached
        assert "ev_policy" not in passed
        assert "risk" in passed


class TestComponentDiagnostics:
    def test_weakest_component(self):
        components = {"a": 0.9, "b": 0.2, "c": 0.7}
        weights = {"a": 0.4, "b": 0.3, "c": 0.3}
        diag = _compute_component_diagnostics(components, weights, 0.60, 0.35)
        assert diag["weakest_component"] == "b"
        assert diag["weakest_value"] == 0.2

    def test_largest_drag(self):
        components = {"a": 0.9, "b": 0.5, "c": 0.7}
        weights = {"a": 0.1, "b": 0.6, "c": 0.3}
        # drag(a) = 0.1*0.1=0.01, drag(b) = 0.6*0.5=0.30, drag(c) = 0.3*0.3=0.09
        diag = _compute_component_diagnostics(components, weights, 0.60, 0.35)
        assert diag["largest_drag_component"] == "b"

    def test_closest_flip(self):
        # score=0.34, threshold=0.35, gap=0.01
        components = {"a": 0.5, "b": 0.5}
        weights = {"a": 0.6, "b": 0.4}
        # delta(a) = 0.01/0.6 = 0.0167, delta(b) = 0.01/0.4 = 0.025
        diag = _compute_component_diagnostics(components, weights, 0.34, 0.35)
        assert diag["closest_flip_component"] == "a"
        assert diag["closest_flip_delta"] == pytest.approx(0.0167, abs=0.001)
        assert diag["flip_feasible"] is True

    def test_flip_infeasible(self):
        # score=0.10, threshold=0.35, gap=0.25 — too large for any single component
        components = {"a": 0.95, "b": 0.95}  # Already near max
        weights = {"a": 0.5, "b": 0.5}
        # delta(a) = 0.25/0.5 = 0.50 → target 1.45 > 1.0 — infeasible
        diag = _compute_component_diagnostics(components, weights, 0.10, 0.35)
        assert diag["flip_feasible"] is False
        assert diag["closest_flip_component"] is None

    def test_above_threshold_no_flip(self):
        diag = _compute_component_diagnostics({"a": 0.8}, {"a": 1.0}, 0.80, 0.35)
        assert diag["threshold_gap"] > 0
        assert diag["closest_flip_component"] is None


class TestBuildTrace:
    def test_no_trade_builds_correctly(self):
        trace = build_decision_trace(engine_result=_make_no_trade_result(), runtime_session_id="abc123")
        assert trace.action == "NO_TRADE"
        assert trace.terminal_stage == "ev_policy"
        assert trace.entity_id == "EURUSD_1700000000"
        assert trace.runtime_session_id == "abc123"
        assert trace.pattern_detected is True
        assert trace.ev == pytest.approx(-0.00003)

    def test_execute_builds_correctly(self):
        trace = build_decision_trace(engine_result=_make_execute_result())
        assert trace.action == "EXECUTE"
        assert trace.terminal_stage == "execute"
        assert "execute" in trace.stages_passed

    def test_no_pattern_builds_correctly(self):
        trace = build_decision_trace(engine_result=_make_no_pattern_result())
        assert trace.action == "NO_TRADE"
        assert trace.terminal_stage == "pattern_detection"
        assert trace.pattern_detected is False
        assert trace.components == {}

    def test_empty_result_no_crash(self):
        trace = build_decision_trace(engine_result={})
        assert trace.action == "NO_TRADE"
        assert trace.entity_id == ""

    def test_frozen(self):
        trace = build_decision_trace(engine_result=_make_no_trade_result())
        with pytest.raises(Exception):
            trace.action = "EXECUTE"


class TestTraceSerialization:
    def test_to_dict_produces_valid_json(self):
        trace = build_decision_trace(engine_result=_make_no_trade_result())
        d = trace.to_dict()
        # Verify JSON-serializable
        line = json.dumps(d, default=str)
        parsed = json.loads(line)
        assert parsed["entity_id"] == "EURUSD_1700000000"
        assert parsed["terminal_stage"] == "ev_policy"
        assert isinstance(parsed["stages_reached"], list)
        assert isinstance(parsed["components"], dict)


class TestPersistence:
    def test_persist_writes_jsonl(self, tmp_path):
        with patch("core.decision_trace._LOCAL_DIR", str(tmp_path)):
            trace = build_decision_trace(
                engine_result=_make_no_trade_result(),
                runtime_session_id="sess1",
            )
            persist_decision_trace(trace)

        files = list(tmp_path.rglob("*.jsonl"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["entity_id"] == "EURUSD_1700000000"
        assert record["terminal_stage"] == "ev_policy"

    def test_persist_never_raises(self, tmp_path):
        # Even with broken trace, should not raise
        trace = DecisionTrace(
            entity_id="", symbol="", cycle_id=0,
            timestamp_utc="broken",
        )
        # This should not raise even if path is invalid
        persist_decision_trace(trace)
