"""
Unit tests for filter_hit_classifier — diagnostic category classification.

Tests:
    - Each new engine reason maps to correct filter key
    - Each old pipeline stage maps to correct filter key
    - Reason-based fallback classification works
    - Unknown reasons default to market_context
    - No stage match + no reason match returns None
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.runtime.filter_hit_classifier import (
    classify_new_engine_reason,
    classify_old_pipeline_drop,
    FilterHitResult,
)


# ─── NEW ENGINE REASON CLASSIFICATION ─────────────────────────────────────────


class TestClassifyNewEngineReason:
    """New engine reason → filter key mapping."""

    def test_strategy_activation_failed(self):
        r = classify_new_engine_reason("strategy_activation_failed:no_bias")
        assert r.filter_key == "structure/bias"

    def test_score_below_threshold(self):
        r = classify_new_engine_reason("score_below_threshold:4.2<4.6")
        assert r.filter_key == "score_reject"

    def test_policy_blocked_chop(self):
        r = classify_new_engine_reason("policy_blocked:CHOP_DETECTED")
        assert r.filter_key == "market_context"

    def test_policy_blocked_neutral_score(self):
        r = classify_new_engine_reason("policy_blocked:NEUTRAL_SCORE")
        assert r.filter_key == "score_reject"

    def test_policy_blocked_confidence(self):
        r = classify_new_engine_reason("policy_blocked:CONFIDENCE_TOO_LOW")
        assert r.filter_key == "structure/bias"

    def test_policy_blocked_other(self):
        r = classify_new_engine_reason("policy_blocked:UNKNOWN_REASON")
        assert r.filter_key == "market_context"

    def test_swing_blocked(self):
        r = classify_new_engine_reason("swing_blocked:opposing_swing")
        assert r.filter_key == "trend_filter"

    def test_ev_policy_blocked_negative(self):
        r = classify_new_engine_reason("ev_policy_blocked:NEGATIVE_EXPECTED_VALUE")
        assert r.filter_key == "score_reject"

    def test_ev_policy_blocked_rr_below(self):
        r = classify_new_engine_reason("ev_policy_blocked:RR_BELOW_1.5")
        assert r.filter_key == "trade_quality"

    def test_ev_policy_blocked_other(self):
        r = classify_new_engine_reason("ev_policy_blocked:OTHER")
        assert r.filter_key == "score_reject"

    def test_risk_rejected(self):
        r = classify_new_engine_reason("risk_rejected:spread_too_wide")
        assert r.filter_key == "trade_quality"

    def test_data_invalid(self):
        r = classify_new_engine_reason("data_invalid:insufficient_bars")
        assert r.filter_key == "market_context"

    def test_no_viable_pattern(self):
        r = classify_new_engine_reason("no_viable_pattern")
        assert r.filter_key == "pattern"

    def test_unknown_defaults_to_market_context(self):
        r = classify_new_engine_reason("some_completely_unknown_reason")
        assert r.filter_key == "market_context"

    def test_empty_string_defaults_to_market_context(self):
        r = classify_new_engine_reason("")
        assert r.filter_key == "market_context"


# ─── OLD PIPELINE DROP CLASSIFICATION ─────────────────────────────────────────


class TestClassifyOldPipelineDrop:
    """Old pipeline stage/reason → filter key mapping."""

    def test_market_context_stage(self):
        r = classify_old_pipeline_drop("market_context", "")
        assert r is not None
        assert r.filter_key == "market_context"

    def test_structure_analysis_stage(self):
        r = classify_old_pipeline_drop("structure_analysis", "")
        assert r is not None
        assert r.filter_key == "structure/bias"

    def test_confirmations_stage(self):
        r = classify_old_pipeline_drop("confirmations", "")
        assert r is not None
        assert r.filter_key == "confirmation"

    def test_trade_quality_stage(self):
        r = classify_old_pipeline_drop("trade_quality", "")
        assert r is not None
        assert r.filter_key == "trade_quality"

    def test_scoring_engine_stage(self):
        r = classify_old_pipeline_drop("scoring_engine", "")
        assert r is not None
        assert r.filter_key == "score_reject"

    def test_stability_gate_stage(self):
        r = classify_old_pipeline_drop("stability_gate", "")
        assert r is not None
        assert r.filter_key == "stability_gate"

    def test_build_intent_stage(self):
        r = classify_old_pipeline_drop("build_intent", "")
        assert r is not None
        assert r.filter_key == "trade_quality"

    def test_unknown_stage_pattern_reason(self):
        r = classify_old_pipeline_drop("unknown_stage", "no_pattern_found")
        assert r is not None
        assert r.filter_key == "pattern"

    def test_unknown_stage_chop_reason(self):
        r = classify_old_pipeline_drop("unknown_stage", "chop_detected")
        assert r is not None
        assert r.filter_key == "chop_filter"

    def test_unknown_stage_trend_reason(self):
        r = classify_old_pipeline_drop("unknown_stage", "trend_opposing")
        assert r is not None
        assert r.filter_key == "trend_filter"

    def test_unknown_stage_bias_reason(self):
        r = classify_old_pipeline_drop("unknown_stage", "bias_not_confirmed")
        assert r is not None
        assert r.filter_key == "structure/bias"

    def test_no_match_returns_none(self):
        r = classify_old_pipeline_drop("unknown_stage", "completely_unknown_reason")
        assert r is None


# ─── RESULT TYPE ──────────────────────────────────────────────────────────────


class TestFilterHitResult:
    """FilterHitResult is a frozen dataclass."""

    def test_is_frozen(self):
        r = FilterHitResult(filter_key="test")
        with pytest.raises(Exception):
            r.filter_key = "changed"
