"""
Tests for decision audit confirmation metrics persistence.

Validates:
- Confirmation fields persist when present in UnifiedDecision
- Legacy decisions without structured confirmation serialize cleanly
- Missing confirmation object does not crash
- All new fields appear in the audit record
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.decision_audit import _build_audit_record, _extract_confirmation
from strategy.signals import Side


# --- HELPERS ------------------------------------------------------------------

def _mock_engine_state():
    """Create a mock EngineState."""
    state = MagicMock()
    state.current_bias = Side.BUY
    state.bias_phase = "CONFIRMED"
    state.bias_strength = 70.0
    state.bias_age_seconds = 300.0
    state.regime_state = "TRENDING"
    state.volatility_filter = 0.0
    state.bias_confirmation_score = 4.0
    state.bias_confirmation_count = 3
    state.bias_contradiction_count = 0
    return state


def _mock_candle():
    """Create a mock candle."""
    c = MagicMock()
    c.time = 1000
    c.open = 1.1000
    c.high = 1.1050
    c.low = 1.0950
    c.close = 1.1040
    return c


def _mock_decision_with_confirmation(
    strength="STRONG",
    body_pct=0.75,
    wick_ratio=0.25,
    close_location=0.9,
    reason="pattern confirmed",
    passed=True,
    evaluated=True,
):
    """Create a mock UnifiedDecision with structured confirmation."""
    # Build confirmation object
    confirmation = MagicMock()
    confirmation.evaluated = evaluated
    confirmation.strength = strength
    confirmation.body_pct = body_pct
    confirmation.wick_ratio = wick_ratio
    confirmation.close_location = close_location
    confirmation.reason = reason
    confirmation.passed = passed

    # Build decision object
    dec = MagicMock()
    dec.should_trade = True
    dec.reason = "complete"
    dec.bias = Side.BUY
    dec.score = 6
    dec.bias_phase = "CONFIRMED"
    dec.bias_validation_score = 4
    dec.structure_ok = True
    dec.patterns = ["BULLISH_ENGULFING"]
    dec.intent = None

    # Build unified decision
    unified = MagicMock()
    unified.decision = dec
    unified.confirmation = confirmation
    unified.last_completed_stage = "complete"
    unified.bar_context = None

    return unified


def _mock_decision_without_confirmation():
    """Create a mock UnifiedDecision WITHOUT confirmation object (legacy)."""
    dec = MagicMock()
    dec.should_trade = True
    dec.reason = "complete"
    dec.bias = Side.BUY
    dec.score = 6
    dec.bias_phase = "CONFIRMED"
    dec.bias_validation_score = 4
    dec.structure_ok = True
    dec.patterns = ["BULLISH_ENGULFING"]
    dec.intent = None

    unified = MagicMock()
    unified.decision = dec
    unified.last_completed_stage = "complete"
    unified.bar_context = None
    # No confirmation attribute at all
    del unified.confirmation

    return unified


def _mock_decision_with_unevaluated_confirmation():
    """Create a mock UnifiedDecision with default (unevaluated) confirmation."""
    confirmation = MagicMock()
    confirmation.evaluated = False
    confirmation.strength = ""
    confirmation.body_pct = 0.0
    confirmation.wick_ratio = 0.0
    confirmation.close_location = 0.0
    confirmation.reason = ""
    confirmation.passed = False

    dec = MagicMock()
    dec.should_trade = False
    dec.reason = "market_context"
    dec.bias = None
    dec.score = 0
    dec.bias_phase = ""
    dec.bias_validation_score = 0
    dec.structure_ok = False
    dec.patterns = None
    dec.intent = None

    unified = MagicMock()
    unified.decision = dec
    unified.confirmation = confirmation
    unified.last_completed_stage = "market_context"
    unified.bar_context = None

    return unified


# --- _extract_confirmation TESTS ----------------------------------------------

class TestExtractConfirmation:

    def test_extracts_all_fields_when_present(self):
        """All confirmation fields are extracted correctly."""
        decision = _mock_decision_with_confirmation(
            strength="STRONG",
            body_pct=0.72,
            wick_ratio=0.28,
            close_location=0.85,
            reason="pattern confirmed",
            passed=True,
        )

        result = _extract_confirmation(decision)

        assert result is not None
        assert result["strength"] == "STRONG"
        assert result["body_pct"] == 0.72
        assert result["wick_ratio"] == 0.28
        assert result["close_location"] == 0.85
        assert result["reason"] == "pattern confirmed"
        assert result["passed"] is True

    def test_extracts_weak_confirmation(self):
        """WEAK confirmation fields extracted."""
        decision = _mock_decision_with_confirmation(
            strength="WEAK",
            body_pct=0.52,
            wick_ratio=0.48,
            close_location=0.65,
            reason="weak confirmation (52% body)",
            passed=True,
        )

        result = _extract_confirmation(decision)

        assert result is not None
        assert result["strength"] == "WEAK"
        assert result["body_pct"] == 0.52
        assert result["passed"] is True

    def test_extracts_invalid_confirmation(self):
        """INVALID confirmation fields extracted."""
        decision = _mock_decision_with_confirmation(
            strength="INVALID",
            body_pct=0.25,
            wick_ratio=0.75,
            close_location=0.3,
            reason="body too weak (25% < 45%)",
            passed=False,
            evaluated=True,
        )

        result = _extract_confirmation(decision)

        assert result is not None
        assert result["strength"] == "INVALID"
        assert result["body_pct"] == 0.25
        assert result["passed"] is False

    def test_returns_none_when_no_confirmation_attr(self):
        """Missing confirmation attribute returns None (no crash)."""
        decision = _mock_decision_without_confirmation()

        result = _extract_confirmation(decision)

        assert result is None

    def test_returns_none_when_not_evaluated(self):
        """Unevaluated confirmation (pipeline halted early) returns None."""
        decision = _mock_decision_with_unevaluated_confirmation()

        result = _extract_confirmation(decision)

        assert result is None

    def test_returns_none_for_none_decision(self):
        """None decision object returns None."""
        result = _extract_confirmation(None)

        assert result is None


# --- _build_audit_record INTEGRATION TESTS ------------------------------------

class TestBuildAuditRecordConfirmation:

    def test_confirmation_present_in_record(self):
        """Confirmation metrics appear in audit record when available."""
        decision = _mock_decision_with_confirmation(
            strength="STRONG",
            body_pct=0.80,
            wick_ratio=0.20,
            close_location=0.95,
        )
        state = _mock_engine_state()
        candles = [_mock_candle()]

        with patch("core.decision_audit.config") as mock_cfg:
            mock_cfg.TIMEFRAME = 5
            mock_cfg.DECISION_AUDIT_ENABLED = True

            record = _build_audit_record(
                symbol="EURUSD",
                cycle_id=42,
                decision=decision,
                engine_state=state,
                candles=candles,
                closed_i=0,
                runtime_mode="LIVE",
            )

        assert "confirmation" in record
        assert record["confirmation"] is not None
        assert record["confirmation"]["strength"] == "STRONG"
        assert record["confirmation"]["body_pct"] == 0.80
        assert record["confirmation"]["wick_ratio"] == 0.20
        assert record["confirmation"]["close_location"] == 0.95

    def test_confirmation_none_when_missing(self):
        """Confirmation is None in audit record when not available."""
        decision = _mock_decision_without_confirmation()
        state = _mock_engine_state()
        candles = [_mock_candle()]

        with patch("core.decision_audit.config") as mock_cfg:
            mock_cfg.TIMEFRAME = 5
            mock_cfg.DECISION_AUDIT_ENABLED = True

            record = _build_audit_record(
                symbol="EURUSD",
                cycle_id=42,
                decision=decision,
                engine_state=state,
                candles=candles,
                closed_i=0,
                runtime_mode="LIVE",
            )

        assert "confirmation" in record
        assert record["confirmation"] is None

    def test_confirmation_none_when_unevaluated(self):
        """Confirmation is None when pipeline halted before confirmation stage."""
        decision = _mock_decision_with_unevaluated_confirmation()
        state = _mock_engine_state()
        candles = [_mock_candle()]

        with patch("core.decision_audit.config") as mock_cfg:
            mock_cfg.TIMEFRAME = 5
            mock_cfg.DECISION_AUDIT_ENABLED = True

            record = _build_audit_record(
                symbol="EURUSD",
                cycle_id=42,
                decision=decision,
                engine_state=state,
                candles=candles,
                closed_i=0,
                runtime_mode="LIVE",
            )

        assert "confirmation" in record
        assert record["confirmation"] is None

    def test_record_serializes_to_json_with_confirmation(self):
        """Full record with confirmation can be serialized to JSON."""
        import json

        decision = _mock_decision_with_confirmation(
            strength="WEAK",
            body_pct=0.55,
            wick_ratio=0.45,
            close_location=0.7,
            reason="weak confirmation (55% body)",
        )
        state = _mock_engine_state()
        candles = [_mock_candle()]

        with patch("core.decision_audit.config") as mock_cfg:
            mock_cfg.TIMEFRAME = 5
            mock_cfg.DECISION_AUDIT_ENABLED = True

            record = _build_audit_record(
                symbol="EURUSD",
                cycle_id=99,
                decision=decision,
                engine_state=state,
                candles=candles,
                closed_i=0,
                runtime_mode="REPLAY",
            )

        # Should not raise
        json_str = json.dumps(record, default=str)
        assert "confirmation" in json_str
        assert "WEAK" in json_str
        assert "0.55" in json_str

    def test_record_serializes_to_json_without_confirmation(self):
        """Legacy record without confirmation serializes cleanly."""
        import json

        decision = _mock_decision_without_confirmation()
        state = _mock_engine_state()
        candles = [_mock_candle()]

        with patch("core.decision_audit.config") as mock_cfg:
            mock_cfg.TIMEFRAME = 5
            mock_cfg.DECISION_AUDIT_ENABLED = True

            record = _build_audit_record(
                symbol="EURUSD",
                cycle_id=1,
                decision=decision,
                engine_state=state,
                candles=candles,
                closed_i=0,
                runtime_mode="LIVE",
            )

        # Should not raise
        json_str = json.dumps(record, default=str)
        assert '"confirmation": null' in json_str or '"confirmation":null' in json_str

    def test_other_fields_unchanged(self):
        """Adding confirmation doesn't affect other existing fields."""
        decision = _mock_decision_with_confirmation()
        state = _mock_engine_state()
        candles = [_mock_candle()]

        with patch("core.decision_audit.config") as mock_cfg:
            mock_cfg.TIMEFRAME = 5
            mock_cfg.DECISION_AUDIT_ENABLED = True

            record = _build_audit_record(
                symbol="GBPUSD",
                cycle_id=10,
                decision=decision,
                engine_state=state,
                candles=candles,
                closed_i=0,
                runtime_mode="LIVE",
            )

        # Existing fields still present and correct
        assert record["symbol"] == "GBPUSD"
        assert record["cycle_id"] == 10
        assert record["runtime_mode"] == "LIVE"
        assert record["should_trade"] is True
        assert record["score"] == 6
        assert record["engine_state"]["bias_phase"] == "CONFIRMED"
        assert record["last_completed_stage"] == "complete"
