"""
Unit tests for Macro Alignment Decision Log formatter.

Tests:
  - Complete macro data produces full block
  - Partial data shows UNAVAILABLE for missing layers
  - None alignment/macro shows all UNAVAILABLE
  - Disabled macro shows DISABLED message
  - Opposing layers display negative contributions
  - Conflicted alignment shows Conflict: True
"""

import pytest
from core.timeframes.types import MacroSnapshot
from core.timeframes.macro_alignment import MacroAlignment, compute_macro_alignment
from core.timeframes.macro_decision_log import format_macro_alignment_log


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════


def _full_bullish_macro() -> MacroSnapshot:
    return MacroSnapshot(
        monthly_trend="BULLISH", monthly_trend_strength=0.82, monthly_phase="IMPULSE",
        weekly_trend="BULLISH", weekly_trend_strength=0.74,
        weekly_swing_high=1.10, weekly_swing_low=1.05, weekly_range_position=0.60,
        daily_bias="BEARISH", daily_bias_strength=0.61,
        daily_swing_high=1.09, daily_swing_low=1.07, daily_range_position=0.55,
        daily_atr_ratio=1.0, bar_time=1785500000,
    )


def _partial_macro() -> MacroSnapshot:
    """Only daily has data."""
    return MacroSnapshot(
        monthly_trend="", monthly_trend_strength=0.0,
        weekly_trend="", weekly_trend_strength=0.0,
        daily_bias="BULLISH", daily_bias_strength=0.65,
        bar_time=1785500000,
    )


def _conflicted_macro() -> MacroSnapshot:
    return MacroSnapshot(
        monthly_trend="BULLISH", monthly_trend_strength=0.70,
        weekly_trend="BEARISH", weekly_trend_strength=0.65,
        daily_bias="NEUTRAL", daily_bias_strength=0.10,
        bar_time=1785500000,
    )


# ═══════════════════════════════════════════════════════════════
# COMPLETE DATA TESTS
# ═══════════════════════════════════════════════════════════════


class TestCompleteData:
    def test_contains_direction(self):
        macro = _full_bullish_macro()
        alignment = compute_macro_alignment(macro, "BULLISH")
        output = format_macro_alignment_log(alignment, macro, "BULLISH", 0.61, 0.65)
        assert "Direction: BUY" in output

    def test_contains_monthly_line(self):
        macro = _full_bullish_macro()
        alignment = compute_macro_alignment(macro, "BULLISH")
        output = format_macro_alignment_log(alignment, macro, "BULLISH", 0.61, 0.65)
        assert "MN1" in output
        assert "BULLISH" in output
        assert "strength=0.82" in output

    def test_contains_weekly_line(self):
        macro = _full_bullish_macro()
        alignment = compute_macro_alignment(macro, "BULLISH")
        output = format_macro_alignment_log(alignment, macro, "BULLISH", 0.61, 0.65)
        assert "W1" in output
        assert "strength=0.74" in output

    def test_contains_daily_line(self):
        macro = _full_bullish_macro()
        alignment = compute_macro_alignment(macro, "BULLISH")
        output = format_macro_alignment_log(alignment, macro, "BULLISH", 0.61, 0.65)
        assert "D1" in output
        assert "strength=0.61" in output

    def test_contains_alignment_state(self):
        macro = _full_bullish_macro()
        alignment = compute_macro_alignment(macro, "BULLISH")
        output = format_macro_alignment_log(alignment, macro, "BULLISH", 0.61, 0.65)
        assert "Alignment State" in output
        assert alignment.alignment_state in output

    def test_contains_primary_driver(self):
        macro = _full_bullish_macro()
        alignment = compute_macro_alignment(macro, "BULLISH")
        output = format_macro_alignment_log(alignment, macro, "BULLISH", 0.61, 0.65)
        assert "Primary Driver" in output

    def test_contains_data_quality(self):
        macro = _full_bullish_macro()
        alignment = compute_macro_alignment(macro, "BULLISH")
        output = format_macro_alignment_log(alignment, macro, "BULLISH", 0.61, 0.65)
        assert "Data Quality" in output

    def test_contains_confidence_values(self):
        macro = _full_bullish_macro()
        alignment = compute_macro_alignment(macro, "BULLISH")
        output = format_macro_alignment_log(alignment, macro, "BULLISH", 0.61, 0.65)
        assert "Base Confidence : 0.61" in output
        assert "Final Confidence: 0.65" in output
        assert "Macro Modifier" in output

    def test_sell_direction_label(self):
        macro = _full_bullish_macro()
        alignment = compute_macro_alignment(macro, "BEARISH")
        output = format_macro_alignment_log(alignment, macro, "BEARISH", 0.70, 0.60)
        assert "Direction: SELL" in output


# ═══════════════════════════════════════════════════════════════
# PARTIAL DATA TESTS
# ═══════════════════════════════════════════════════════════════


class TestPartialData:
    def test_missing_layers_show_unavailable(self):
        macro = _partial_macro()
        alignment = compute_macro_alignment(macro, "BULLISH")
        output = format_macro_alignment_log(alignment, macro, "BULLISH", 0.60, 0.63)
        assert "MN1 : UNAVAILABLE" in output
        assert "W1  : UNAVAILABLE" in output
        # D1 should have data
        assert "BULLISH" in output
        assert "strength=0.65" in output

    def test_partial_still_shows_confidence(self):
        macro = _partial_macro()
        alignment = compute_macro_alignment(macro, "BULLISH")
        output = format_macro_alignment_log(alignment, macro, "BULLISH", 0.60, 0.63)
        assert "Base Confidence : 0.60" in output
        assert "Final Confidence: 0.63" in output


# ═══════════════════════════════════════════════════════════════
# UNAVAILABLE DATA TESTS
# ═══════════════════════════════════════════════════════════════


class TestUnavailableData:
    def test_none_alignment_shows_all_unavailable(self):
        output = format_macro_alignment_log(None, None, "BULLISH", 0.70, 0.70)
        assert "MN1 : UNAVAILABLE" in output
        assert "W1  : UNAVAILABLE" in output
        assert "D1  : UNAVAILABLE" in output
        assert "Alignment State : UNAVAILABLE" in output

    def test_none_alignment_confidence_unchanged(self):
        output = format_macro_alignment_log(None, None, "BULLISH", 0.70, 0.70)
        assert "Base Confidence : 0.70" in output
        assert "Macro Modifier  : +0.00" in output
        assert "Final Confidence: 0.70" in output

    def test_none_macro_with_direction(self):
        output = format_macro_alignment_log(None, None, "BEARISH", 0.55, 0.55)
        assert "Direction: SELL" in output


# ═══════════════════════════════════════════════════════════════
# DISABLED TESTS
# ═══════════════════════════════════════════════════════════════


class TestDisabled:
    def test_disabled_shows_message(self):
        macro = _full_bullish_macro()
        alignment = compute_macro_alignment(macro, "BULLISH")
        output = format_macro_alignment_log(alignment, macro, "BULLISH", 0.70, 0.70, enabled=False)
        assert "Macro Context: DISABLED" in output

    def test_disabled_does_not_show_layers(self):
        output = format_macro_alignment_log(None, None, "BULLISH", 0.70, 0.70, enabled=False)
        assert "MN1" not in output
        assert "W1" not in output
        assert "D1" not in output
        assert "Base Confidence" not in output


# ═══════════════════════════════════════════════════════════════
# OPPOSING / CONFLICTED TESTS
# ═══════════════════════════════════════════════════════════════


class TestOpposingConflicted:
    def test_opposing_shows_negative_contribution(self):
        macro = _full_bullish_macro()
        # D1 is BEARISH while trade is BUY → opposing
        alignment = compute_macro_alignment(macro, "BULLISH")
        output = format_macro_alignment_log(alignment, macro, "BULLISH", 0.70, 0.68)
        # D1 line should show negative contribution
        lines = output.split("\n")
        d1_line = [l for l in lines if l.startswith("D1")][0]
        assert "contribution=-" in d1_line

    def test_conflicted_shows_true(self):
        macro = _conflicted_macro()
        alignment = compute_macro_alignment(macro, "BULLISH")
        output = format_macro_alignment_log(alignment, macro, "BULLISH", 0.60, 0.58)
        assert "Conflict        : True" in output

    def test_non_conflicted_shows_false(self):
        # All aligned → no conflict
        macro = MacroSnapshot(
            monthly_trend="BULLISH", monthly_trend_strength=0.70,
            weekly_trend="BULLISH", weekly_trend_strength=0.65,
            daily_bias="BULLISH", daily_bias_strength=0.60,
            bar_time=1785500000,
        )
        alignment = compute_macro_alignment(macro, "BULLISH")
        output = format_macro_alignment_log(alignment, macro, "BULLISH", 0.70, 0.80)
        assert "Conflict        : False" in output

    def test_full_opposition_negative_modifier(self):
        macro = MacroSnapshot(
            monthly_trend="BEARISH", monthly_trend_strength=0.80,
            weekly_trend="BEARISH", weekly_trend_strength=0.75,
            daily_bias="BEARISH", daily_bias_strength=0.70,
            bar_time=1785500000,
        )
        alignment = compute_macro_alignment(macro, "BULLISH")
        output = format_macro_alignment_log(alignment, macro, "BULLISH", 0.70, 0.55)
        assert "Macro Modifier  : -" in output
        assert "FULL_OPPOSITION" in output
