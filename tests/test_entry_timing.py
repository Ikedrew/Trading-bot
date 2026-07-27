"""
Tests for Entry Timing Classification + Decision Audit Integration.

Validates:
- EARLY entry classification (strong body, low wick, STRONG confirmation)
- MID entry classification (balanced metrics)
- LATE entry classification (weak body, high wick)
- Default behavior with missing data
- Integration with decision audit record
- No execution logic affected (observational only)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry_timing import classify_entry_timing
from core.decision_audit import (
    _extract_confirmation,
    _classify_entry_timing_from_decision,
    _build_audit_record,
)
from strategy.signals import Side


# --- HELPERS ------------------------------------------------------------------

def _mock_engine_state():
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
    passed=True,
    evaluated=True,
):
    confirmation = MagicMock()
    confirmation.evaluated = evaluated
    confirmation.strength = strength
    confirmation.body_pct = body_pct
    confirmation.wick_ratio = wick_ratio
    confirmation.close_location = close_location
    confirmation.reason = "test"
    confirmation.passed = passed

    dec = MagicMock()
    dec.should_trade = True
    dec.reason = "ok"
    dec.bias = Side.BUY
    dec.score = 6
    dec.bias_phase = "CONFIRMED"
    dec.bias_validation_score = 4
    dec.structure_ok = True
    dec.patterns = ["BULLISH_ENGULFING"]
    dec.intent = None

    unified = MagicMock()
    unified.decision = dec
    unified.confirmation = confirmation
    unified.last_completed_stage = "complete"
    unified.bar_context = None

    return unified


# -------------------------------------------------------------------------------
# ENTRY TIMING CLASSIFICATION UNIT TESTS
# -------------------------------------------------------------------------------


class TestClassifyEarlyEntry:
    """EARLY: STRONG + high body (>=70%) + low wick (<=30%)."""

    def test_strong_high_body_low_wick_is_early(self):
        """Classic early entry: 80% body, 20% wick, STRONG."""
        result = classify_entry_timing(
            confirmation_strength="STRONG",
            body_pct=0.80,
            wick_ratio=0.20,
            close_location=0.95,
        )
        assert result == "EARLY"

    def test_boundary_70_body_30_wick_is_early(self):
        """Boundary case: exactly 70% body, 30% wick, STRONG."""
        result = classify_entry_timing(
            confirmation_strength="STRONG",
            body_pct=0.70,
            wick_ratio=0.30,
            close_location=0.85,
        )
        assert result == "EARLY"

    def test_full_body_candle_is_early(self):
        """100% body (marubozu) is EARLY."""
        result = classify_entry_timing(
            confirmation_strength="STRONG",
            body_pct=1.0,
            wick_ratio=0.0,
            close_location=1.0,
        )
        assert result == "EARLY"

    def test_weak_strength_prevents_early(self):
        """WEAK confirmation cannot be EARLY even with good metrics."""
        result = classify_entry_timing(
            confirmation_strength="WEAK",
            body_pct=0.80,
            wick_ratio=0.20,
            close_location=0.95,
        )
        assert result != "EARLY"  # Should be MID

    def test_high_wick_prevents_early(self):
        """High wick ratio (>30%) prevents EARLY even with STRONG."""
        result = classify_entry_timing(
            confirmation_strength="STRONG",
            body_pct=0.75,
            wick_ratio=0.35,
            close_location=0.80,
        )
        assert result != "EARLY"


class TestClassifyMidEntry:
    """MID: Balanced metrics between EARLY and LATE thresholds."""

    def test_balanced_metrics_is_mid(self):
        """60% body, 35% wick = MID (not EARLY, not LATE)."""
        result = classify_entry_timing(
            confirmation_strength="STRONG",
            body_pct=0.60,
            wick_ratio=0.35,
            close_location=0.75,
        )
        assert result == "MID"

    def test_weak_with_good_body_is_mid(self):
        """WEAK + 65% body + moderate wick = MID."""
        result = classify_entry_timing(
            confirmation_strength="WEAK",
            body_pct=0.65,
            wick_ratio=0.35,
            close_location=0.70,
        )
        assert result == "MID"

    def test_strong_with_moderate_body_is_mid(self):
        """STRONG + 65% body + 35% wick = MID (body too low for EARLY)."""
        result = classify_entry_timing(
            confirmation_strength="STRONG",
            body_pct=0.65,
            wick_ratio=0.35,
            close_location=0.80,
        )
        assert result == "MID"

    def test_56_body_40_wick_is_mid(self):
        """56% body, 40% wick — above LATE threshold for body, below for wick."""
        result = classify_entry_timing(
            confirmation_strength="STRONG",
            body_pct=0.56,
            wick_ratio=0.40,
            close_location=0.70,
        )
        assert result == "MID"


class TestClassifyLateEntry:
    """LATE: Low body (<=55%) + high wick (>=45%)."""

    def test_low_body_high_wick_is_late(self):
        """45% body, 55% wick = LATE."""
        result = classify_entry_timing(
            confirmation_strength="WEAK",
            body_pct=0.45,
            wick_ratio=0.55,
            close_location=0.55,
        )
        assert result == "LATE"

    def test_boundary_55_body_45_wick_is_late(self):
        """Boundary: exactly 55% body, 45% wick = LATE."""
        result = classify_entry_timing(
            confirmation_strength="STRONG",
            body_pct=0.55,
            wick_ratio=0.45,
            close_location=0.60,
        )
        assert result == "LATE"

    def test_doji_like_is_late(self):
        """Very small body (30%), large wick = LATE."""
        result = classify_entry_timing(
            confirmation_strength="WEAK",
            body_pct=0.30,
            wick_ratio=0.70,
            close_location=0.45,
        )
        assert result == "LATE"

    def test_strong_with_poor_metrics_still_late(self):
        """STRONG confirmation but poor candle metrics = still LATE."""
        result = classify_entry_timing(
            confirmation_strength="STRONG",
            body_pct=0.50,
            wick_ratio=0.50,
            close_location=0.5,
        )
        assert result == "LATE"


class TestClassifyDefaultBehavior:
    """Missing data defaults to MID."""

    def test_none_body_pct_defaults_mid(self):
        """Missing body_pct ? MID."""
        result = classify_entry_timing(
            confirmation_strength="STRONG",
            body_pct=None,
            wick_ratio=0.25,
            close_location=0.9,
        )
        assert result == "MID"

    def test_none_wick_ratio_defaults_mid(self):
        """Missing wick_ratio ? MID."""
        result = classify_entry_timing(
            confirmation_strength="STRONG",
            body_pct=0.80,
            wick_ratio=None,
            close_location=0.9,
        )
        assert result == "MID"

    def test_all_none_defaults_mid(self):
        """All fields None ? MID."""
        result = classify_entry_timing(
            confirmation_strength=None,
            body_pct=None,
            wick_ratio=None,
            close_location=None,
        )
        assert result == "MID"

    def test_no_arguments_defaults_mid(self):
        """No arguments provided ? MID."""
        result = classify_entry_timing()
        assert result == "MID"


# -------------------------------------------------------------------------------
# DECISION AUDIT INTEGRATION TESTS
# -------------------------------------------------------------------------------


class TestDecisionAuditEntryTiming:

    def test_early_entry_in_audit_record(self):
        """EARLY entry timing appears in audit record."""
        decision = _mock_decision_with_confirmation(
            strength="STRONG", body_pct=0.80, wick_ratio=0.20, close_location=0.95,
        )
        state = _mock_engine_state()
        candles = [_mock_candle()]

        with patch("core.decision_audit.config") as mock_cfg:
            mock_cfg.TIMEFRAME = 5
            record = _build_audit_record(
                symbol="EURUSD", cycle_id=1, decision=decision,
                engine_state=state, candles=candles, closed_i=0, runtime_mode="LIVE",
            )

        assert record["entry_timing"] == "EARLY"

    def test_mid_entry_in_audit_record(self):
        """MID entry timing appears in audit record."""
        decision = _mock_decision_with_confirmation(
            strength="STRONG", body_pct=0.62, wick_ratio=0.35, close_location=0.75,
        )
        state = _mock_engine_state()
        candles = [_mock_candle()]

        with patch("core.decision_audit.config") as mock_cfg:
            mock_cfg.TIMEFRAME = 5
            record = _build_audit_record(
                symbol="EURUSD", cycle_id=2, decision=decision,
                engine_state=state, candles=candles, closed_i=0, runtime_mode="LIVE",
            )

        assert record["entry_timing"] == "MID"

    def test_late_entry_in_audit_record(self):
        """LATE entry timing appears in audit record."""
        decision = _mock_decision_with_confirmation(
            strength="WEAK", body_pct=0.48, wick_ratio=0.52, close_location=0.55,
        )
        state = _mock_engine_state()
        candles = [_mock_candle()]

        with patch("core.decision_audit.config") as mock_cfg:
            mock_cfg.TIMEFRAME = 5
            record = _build_audit_record(
                symbol="EURUSD", cycle_id=3, decision=decision,
                engine_state=state, candles=candles, closed_i=0, runtime_mode="LIVE",
            )

        assert record["entry_timing"] == "LATE"

    def test_none_timing_when_confirmation_missing(self):
        """entry_timing is None when confirmation not available."""
        dec = MagicMock()
        dec.should_trade = True
        dec.reason = "ok"
        dec.bias = Side.BUY
        dec.score = 6
        dec.bias_phase = "CONFIRMED"
        dec.bias_validation_score = 4
        dec.structure_ok = True
        dec.patterns = []
        dec.intent = None

        unified = MagicMock()
        unified.decision = dec
        unified.last_completed_stage = "complete"
        unified.bar_context = None
        del unified.confirmation  # No confirmation at all

        state = _mock_engine_state()
        candles = [_mock_candle()]

        with patch("core.decision_audit.config") as mock_cfg:
            mock_cfg.TIMEFRAME = 5
            record = _build_audit_record(
                symbol="EURUSD", cycle_id=4, decision=unified,
                engine_state=state, candles=candles, closed_i=0, runtime_mode="LIVE",
            )

        assert record["entry_timing"] is None

    def test_none_timing_when_confirmation_not_passed(self):
        """entry_timing is None when confirmation evaluated but not passed (INVALID)."""
        decision = _mock_decision_with_confirmation(
            strength="INVALID", body_pct=0.30, wick_ratio=0.70,
            close_location=0.4, passed=False, evaluated=True,
        )
        state = _mock_engine_state()
        candles = [_mock_candle()]

        with patch("core.decision_audit.config") as mock_cfg:
            mock_cfg.TIMEFRAME = 5
            record = _build_audit_record(
                symbol="EURUSD", cycle_id=5, decision=decision,
                engine_state=state, candles=candles, closed_i=0, runtime_mode="LIVE",
            )

        assert record["entry_timing"] is None

    def test_record_serializes_with_entry_timing(self):
        """Full record with entry_timing serializes to JSON cleanly."""
        import json

        decision = _mock_decision_with_confirmation(
            strength="STRONG", body_pct=0.85, wick_ratio=0.15, close_location=0.92,
        )
        state = _mock_engine_state()
        candles = [_mock_candle()]

        with patch("core.decision_audit.config") as mock_cfg:
            mock_cfg.TIMEFRAME = 5
            record = _build_audit_record(
                symbol="EURUSD", cycle_id=10, decision=decision,
                engine_state=state, candles=candles, closed_i=0, runtime_mode="LIVE",
            )

        json_str = json.dumps(record, default=str)
        assert '"entry_timing"' in json_str
        assert '"EARLY"' in json_str

    def test_cohort_dimensions_present(self):
        """Record contains all fields needed for cohort analysis."""
        decision = _mock_decision_with_confirmation(
            strength="WEAK", body_pct=0.52, wick_ratio=0.42, close_location=0.65,
        )
        state = _mock_engine_state()
        candles = [_mock_candle()]

        with patch("core.decision_audit.config") as mock_cfg:
            mock_cfg.TIMEFRAME = 5
            record = _build_audit_record(
                symbol="EURUSD", cycle_id=20, decision=decision,
                engine_state=state, candles=candles, closed_i=0, runtime_mode="LIVE",
            )

        # All cohort analysis dimensions present
        assert "confirmation" in record
        assert record["confirmation"]["strength"] == "WEAK"
        assert record["confirmation"]["body_pct"] == 0.52
        assert record["confirmation"]["wick_ratio"] == 0.42
        assert record["confirmation"]["close_location"] == 0.65
        assert "entry_timing" in record
        assert record["entry_timing"] in ("EARLY", "MID", "LATE")
        assert "score" in record
        assert "should_trade" in record
