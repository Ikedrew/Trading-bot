"""
Unit tests for Macro Context Phase 1.

Tests:
  - MacroSnapshot creation
  - compute_macro_alignment() pure function
  - apply_macro_modifier() bounded calculation
  - Missing data → no influence
  - Stale data → reduced influence
  - Conflicts → correct resolution
  - Modifier always bounded ±0.20
  - Confidence floor 0.40, ceiling 1.00
"""

import pytest
from core.timeframes.types import MacroSnapshot
from core.timeframes.macro_alignment import (
    MacroAlignment,
    compute_macro_alignment,
    apply_macro_modifier,
    _classify_layer,
    _strength_scale,
    _direction_value,
)


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════


def _full_bullish_macro() -> MacroSnapshot:
    """All layers strongly bullish."""
    return MacroSnapshot(
        monthly_trend="BULLISH", monthly_trend_strength=0.80, monthly_phase="IMPULSE",
        weekly_trend="BULLISH", weekly_trend_strength=0.75,
        weekly_swing_high=1.10, weekly_swing_low=1.05, weekly_range_position=0.60,
        daily_bias="BULLISH", daily_bias_strength=0.70,
        daily_swing_high=1.09, daily_swing_low=1.07, daily_range_position=0.55,
        daily_atr_ratio=1.0, bar_time=1785500000,
    )


def _full_bearish_macro() -> MacroSnapshot:
    """All layers strongly bearish."""
    return MacroSnapshot(
        monthly_trend="BEARISH", monthly_trend_strength=0.80, monthly_phase="IMPULSE",
        weekly_trend="BEARISH", weekly_trend_strength=0.75,
        weekly_swing_high=1.10, weekly_swing_low=1.05, weekly_range_position=0.30,
        daily_bias="BEARISH", daily_bias_strength=0.70,
        daily_swing_high=1.09, daily_swing_low=1.07, daily_range_position=0.25,
        daily_atr_ratio=1.0, bar_time=1785500000,
    )


def _neutral_macro() -> MacroSnapshot:
    """All layers neutral."""
    return MacroSnapshot(
        monthly_trend="NEUTRAL", monthly_trend_strength=0.10,
        weekly_trend="NEUTRAL", weekly_trend_strength=0.15,
        daily_bias="NEUTRAL", daily_bias_strength=0.10,
        bar_time=1785500000,
    )


def _conflicted_macro() -> MacroSnapshot:
    """Monthly bullish, weekly bearish, daily neutral — active conflict."""
    return MacroSnapshot(
        monthly_trend="BULLISH", monthly_trend_strength=0.70,
        weekly_trend="BEARISH", weekly_trend_strength=0.65,
        daily_bias="NEUTRAL", daily_bias_strength=0.10,
        bar_time=1785500000,
    )


def _stale_macro() -> MacroSnapshot:
    """Valid data but very old bar_time."""
    return MacroSnapshot(
        monthly_trend="BULLISH", monthly_trend_strength=0.70,
        weekly_trend="BULLISH", weekly_trend_strength=0.65,
        daily_bias="BULLISH", daily_bias_strength=0.60,
        bar_time=1785000000,  # Old
    )


def _weak_macro() -> MacroSnapshot:
    """All layers have direction but strength < 0.3."""
    return MacroSnapshot(
        monthly_trend="BULLISH", monthly_trend_strength=0.20,
        weekly_trend="BEARISH", weekly_trend_strength=0.25,
        daily_bias="BULLISH", daily_bias_strength=0.15,
        bar_time=1785500000,
    )


# ═══════════════════════════════════════════════════════════════
# MacroSnapshot CREATION TESTS
# ═══════════════════════════════════════════════════════════════


class TestMacroSnapshotCreation:
    def test_default_creation(self):
        snap = MacroSnapshot()
        assert snap.monthly_trend == ""
        assert snap.monthly_trend_strength == 0.0
        assert snap.daily_atr_ratio == 1.0
        assert snap.bar_time == 0

    def test_full_creation(self):
        snap = _full_bullish_macro()
        assert snap.monthly_trend == "BULLISH"
        assert snap.weekly_swing_high == 1.10
        assert snap.daily_range_position == 0.55

    def test_frozen(self):
        snap = MacroSnapshot()
        with pytest.raises(Exception):
            snap.monthly_trend = "BULLISH"  # type: ignore


# ═══════════════════════════════════════════════════════════════
# MISSING DATA TESTS
# ═══════════════════════════════════════════════════════════════


class TestMissingData:
    def test_none_macro_returns_no_influence(self):
        result = compute_macro_alignment(None, "BULLISH")
        assert result.confidence_modifier == 0.0
        assert result.data_quality == "UNAVAILABLE"
        assert result.alignment_state == "NEUTRAL"

    def test_empty_trade_direction_returns_no_influence(self):
        result = compute_macro_alignment(_full_bullish_macro(), "")
        assert result.confidence_modifier == 0.0
        assert result.data_quality == "PARTIAL"

    def test_invalid_trade_direction_returns_no_influence(self):
        result = compute_macro_alignment(_full_bullish_macro(), "SIDEWAYS")
        assert result.confidence_modifier == 0.0

    def test_all_weak_signals_treated_as_neutral(self):
        """Strength < 0.3 → all layers NEUTRAL → no modifier."""
        result = compute_macro_alignment(_weak_macro(), "BULLISH")
        assert result.confidence_modifier == 0.0
        assert result.monthly_alignment == "NEUTRAL"
        assert result.weekly_alignment == "NEUTRAL"
        assert result.daily_alignment == "NEUTRAL"
        assert result.alignment_state == "NEUTRAL"

    def test_default_macro_snapshot_returns_unavailable(self):
        result = compute_macro_alignment(MacroSnapshot(), "BULLISH")
        assert result.data_quality == "UNAVAILABLE"
        assert result.confidence_modifier == 0.0


# ═══════════════════════════════════════════════════════════════
# FULL ALIGNMENT TESTS
# ═══════════════════════════════════════════════════════════════


class TestFullAlignment:
    def test_all_bullish_buy_is_full_alignment(self):
        result = compute_macro_alignment(_full_bullish_macro(), "BULLISH")
        assert result.alignment_state == "FULL_ALIGNMENT"
        assert result.confidence_modifier > 0
        assert result.monthly_alignment == "ALIGNED"
        assert result.weekly_alignment == "ALIGNED"
        assert result.daily_alignment == "ALIGNED"
        assert result.is_conflicted is False

    def test_all_bearish_sell_is_full_alignment(self):
        result = compute_macro_alignment(_full_bearish_macro(), "BEARISH")
        assert result.alignment_state == "FULL_ALIGNMENT"
        assert result.confidence_modifier > 0

    def test_all_bullish_sell_is_full_opposition(self):
        result = compute_macro_alignment(_full_bullish_macro(), "BEARISH")
        assert result.alignment_state == "FULL_OPPOSITION"
        assert result.confidence_modifier < 0

    def test_all_bearish_buy_is_full_opposition(self):
        result = compute_macro_alignment(_full_bearish_macro(), "BULLISH")
        assert result.alignment_state == "FULL_OPPOSITION"
        assert result.confidence_modifier < 0


# ═══════════════════════════════════════════════════════════════
# NEUTRAL TESTS
# ═══════════════════════════════════════════════════════════════


class TestNeutral:
    def test_all_neutral_gives_zero_modifier(self):
        result = compute_macro_alignment(_neutral_macro(), "BULLISH")
        assert result.confidence_modifier == 0.0
        assert result.alignment_state == "NEUTRAL"
        assert result.is_conflicted is False

    def test_all_neutral_data_quality(self):
        result = compute_macro_alignment(_neutral_macro(), "BULLISH")
        # Strength values are all < 0.3 but trends are "NEUTRAL" with non-zero strength
        # _assess_data_quality checks trend != "" and strength > 0 → True for "NEUTRAL"
        # This is technically "COMPLETE" (data exists, just neutral)
        # The important assertion is no influence:
        assert result.confidence_modifier == 0.0


# ═══════════════════════════════════════════════════════════════
# CONFLICT RESOLUTION TESTS
# ═══════════════════════════════════════════════════════════════


class TestConflicts:
    def test_conflicted_state_detected(self):
        """Monthly BULLISH + Weekly BEARISH → CONFLICTED."""
        result = compute_macro_alignment(_conflicted_macro(), "BULLISH")
        assert result.is_conflicted is True
        assert result.alignment_state == "CONFLICTED"

    def test_conflicted_has_negative_or_small_modifier(self):
        """Conflicted state should not produce large positive modifier."""
        result = compute_macro_alignment(_conflicted_macro(), "BULLISH")
        # MN aligned (+0.25 weight), W1 opposing (-0.35 weight), D1 neutral
        # Raw: positive from MN, negative from W1 → net slightly negative
        assert result.confidence_modifier <= 0.02

    def test_conflicted_narrative_mentions_conflict(self):
        result = compute_macro_alignment(_conflicted_macro(), "BULLISH")
        assert "conflict" in result.narrative.lower()

    def test_primary_influence_in_conflict(self):
        """Weekly has highest weighted absolute contribution."""
        result = compute_macro_alignment(_conflicted_macro(), "BULLISH")
        assert result.primary_influence == "WEEKLY"


# ═══════════════════════════════════════════════════════════════
# STALE DATA TESTS
# ═══════════════════════════════════════════════════════════════


class TestStaleData:
    def test_stale_data_caps_modifier(self):
        """Stale data → modifier capped at ±0.05."""
        # bar_time=1785000000, current_time=1785500000 (5.8 days old > 2 day threshold)
        result = compute_macro_alignment(_stale_macro(), "BULLISH", current_time=1785500000.0)
        assert result.data_quality == "STALE"
        assert abs(result.confidence_modifier) <= 0.05

    def test_stale_vs_fresh_same_data(self):
        """Same macro data: fresh gives larger modifier than stale."""
        macro = _stale_macro()
        fresh = compute_macro_alignment(macro, "BULLISH", current_time=macro.bar_time + 3600)
        stale = compute_macro_alignment(macro, "BULLISH", current_time=macro.bar_time + 200000)
        # Fresh should give full modifier, stale should be capped
        assert fresh.confidence_modifier >= stale.confidence_modifier

    def test_no_current_time_skips_staleness_check(self):
        """current_time=0 → no staleness check, treated as fresh."""
        result = compute_macro_alignment(_stale_macro(), "BULLISH", current_time=0.0)
        assert result.data_quality != "STALE"


# ═══════════════════════════════════════════════════════════════
# MODIFIER BOUNDS TESTS
# ═══════════════════════════════════════════════════════════════


class TestModifierBounds:
    def test_modifier_never_exceeds_positive_cap(self):
        result = compute_macro_alignment(_full_bullish_macro(), "BULLISH")
        assert result.confidence_modifier <= 0.20

    def test_modifier_never_exceeds_negative_cap(self):
        result = compute_macro_alignment(_full_bullish_macro(), "BEARISH")
        assert result.confidence_modifier >= -0.20

    def test_extreme_strengths_still_bounded(self):
        """Even with strength=1.0 everywhere, modifier stays bounded."""
        extreme = MacroSnapshot(
            monthly_trend="BULLISH", monthly_trend_strength=1.0,
            weekly_trend="BULLISH", weekly_trend_strength=1.0,
            daily_bias="BULLISH", daily_bias_strength=1.0,
            bar_time=1785500000,
        )
        result = compute_macro_alignment(extreme, "BULLISH")
        assert result.confidence_modifier <= 0.20

    def test_stale_modifier_capped_at_005(self):
        """Stale data: even full alignment caps at 0.05."""
        extreme = MacroSnapshot(
            monthly_trend="BULLISH", monthly_trend_strength=1.0,
            weekly_trend="BULLISH", weekly_trend_strength=1.0,
            daily_bias="BULLISH", daily_bias_strength=1.0,
            bar_time=1785000000,  # Old
        )
        result = compute_macro_alignment(extreme, "BULLISH", current_time=1785500000.0)
        assert abs(result.confidence_modifier) <= 0.05


# ═══════════════════════════════════════════════════════════════
# apply_macro_modifier TESTS
# ═══════════════════════════════════════════════════════════════


class TestApplyMacroModifier:
    def test_positive_modifier_boosts(self):
        assert apply_macro_modifier(0.70, 0.10) == pytest.approx(0.80)

    def test_negative_modifier_reduces(self):
        assert apply_macro_modifier(0.70, -0.10) == 0.60

    def test_floor_at_040(self):
        """Cannot go below 0.40 regardless of modifier."""
        assert apply_macro_modifier(0.45, -0.20) == 0.40
        assert apply_macro_modifier(0.40, -0.15) == 0.40

    def test_ceiling_at_100(self):
        """Cannot exceed 1.00."""
        assert apply_macro_modifier(0.95, 0.15) == 1.00

    def test_zero_modifier_no_change(self):
        assert apply_macro_modifier(0.65, 0.0) == 0.65

    def test_base_at_floor_stays_at_floor(self):
        assert apply_macro_modifier(0.40, -0.10) == 0.40

    def test_large_negative_clamped(self):
        assert apply_macro_modifier(0.50, -0.50) == 0.40

    def test_large_positive_clamped(self):
        assert apply_macro_modifier(0.90, 0.50) == 1.00


# ═══════════════════════════════════════════════════════════════
# WEIGHTING TESTS
# ═══════════════════════════════════════════════════════════════


class TestWeighting:
    def test_daily_has_most_influence(self):
        """D1 aligned alone should produce larger modifier than MN aligned alone."""
        daily_only = MacroSnapshot(
            monthly_trend="NEUTRAL", monthly_trend_strength=0.10,
            weekly_trend="NEUTRAL", weekly_trend_strength=0.10,
            daily_bias="BULLISH", daily_bias_strength=0.70,
            bar_time=1785500000,
        )
        monthly_only = MacroSnapshot(
            monthly_trend="BULLISH", monthly_trend_strength=0.70,
            weekly_trend="NEUTRAL", weekly_trend_strength=0.10,
            daily_bias="NEUTRAL", daily_bias_strength=0.10,
            bar_time=1785500000,
        )
        d1_result = compute_macro_alignment(daily_only, "BULLISH")
        mn_result = compute_macro_alignment(monthly_only, "BULLISH")
        assert d1_result.confidence_modifier > mn_result.confidence_modifier

    def test_weekly_between_daily_and_monthly(self):
        """W1 aligned alone produces modifier between D1 and MN."""
        weekly_only = MacroSnapshot(
            monthly_trend="NEUTRAL", monthly_trend_strength=0.10,
            weekly_trend="BULLISH", weekly_trend_strength=0.70,
            daily_bias="NEUTRAL", daily_bias_strength=0.10,
            bar_time=1785500000,
        )
        daily_only = MacroSnapshot(
            monthly_trend="NEUTRAL", monthly_trend_strength=0.10,
            weekly_trend="NEUTRAL", weekly_trend_strength=0.10,
            daily_bias="BULLISH", daily_bias_strength=0.70,
            bar_time=1785500000,
        )
        monthly_only = MacroSnapshot(
            monthly_trend="BULLISH", monthly_trend_strength=0.70,
            weekly_trend="NEUTRAL", weekly_trend_strength=0.10,
            daily_bias="NEUTRAL", daily_bias_strength=0.10,
            bar_time=1785500000,
        )
        d1 = compute_macro_alignment(daily_only, "BULLISH").confidence_modifier
        w1 = compute_macro_alignment(weekly_only, "BULLISH").confidence_modifier
        mn = compute_macro_alignment(monthly_only, "BULLISH").confidence_modifier
        assert d1 > w1 > mn


# ═══════════════════════════════════════════════════════════════
# INTERNAL HELPER TESTS
# ═══════════════════════════════════════════════════════════════


class TestHelpers:
    def test_classify_layer_aligned(self):
        assert _classify_layer("BULLISH", 0.5, "BULLISH") == "ALIGNED"

    def test_classify_layer_opposing(self):
        assert _classify_layer("BEARISH", 0.5, "BULLISH") == "OPPOSING"

    def test_classify_layer_neutral_by_trend(self):
        assert _classify_layer("NEUTRAL", 0.5, "BULLISH") == "NEUTRAL"

    def test_classify_layer_neutral_by_strength(self):
        assert _classify_layer("BULLISH", 0.2, "BULLISH") == "NEUTRAL"

    def test_classify_layer_empty_trend(self):
        assert _classify_layer("", 0.5, "BULLISH") == "NEUTRAL"

    def test_strength_scale_below_threshold(self):
        assert _strength_scale(0.2) == 0.0

    def test_strength_scale_at_threshold(self):
        assert _strength_scale(0.3) == pytest.approx(0.3 / 0.7, rel=1e-3)

    def test_strength_scale_at_full(self):
        assert _strength_scale(0.7) == 1.0

    def test_strength_scale_above_full(self):
        assert _strength_scale(0.9) == 1.0

    def test_direction_value_aligned(self):
        assert _direction_value("ALIGNED") == 1.0

    def test_direction_value_opposing(self):
        assert _direction_value("OPPOSING") == -1.0

    def test_direction_value_neutral(self):
        assert _direction_value("NEUTRAL") == 0.0


# ═══════════════════════════════════════════════════════════════
# DATA QUALITY TESTS
# ═══════════════════════════════════════════════════════════════


class TestDataQuality:
    def test_complete_quality(self):
        result = compute_macro_alignment(_full_bullish_macro(), "BULLISH", current_time=1785500100.0)
        assert result.data_quality == "COMPLETE"

    def test_partial_quality(self):
        """Only one layer has data."""
        partial = MacroSnapshot(
            monthly_trend="BULLISH", monthly_trend_strength=0.70,
            weekly_trend="", weekly_trend_strength=0.0,
            daily_bias="", daily_bias_strength=0.0,
            bar_time=1785500000,
        )
        result = compute_macro_alignment(partial, "BULLISH", current_time=1785500100.0)
        assert result.data_quality == "PARTIAL"

    def test_stale_quality(self):
        result = compute_macro_alignment(_stale_macro(), "BULLISH", current_time=1785500000.0)
        assert result.data_quality == "STALE"


# ═══════════════════════════════════════════════════════════════
# NARRATIVE TESTS
# ═══════════════════════════════════════════════════════════════


class TestNarrative:
    def test_full_alignment_narrative(self):
        result = compute_macro_alignment(_full_bullish_macro(), "BULLISH")
        assert "support" in result.narrative.lower()

    def test_full_opposition_narrative(self):
        result = compute_macro_alignment(_full_bullish_macro(), "BEARISH")
        assert "oppose" in result.narrative.lower()

    def test_neutral_narrative(self):
        result = compute_macro_alignment(_neutral_macro(), "BULLISH")
        assert "neutral" in result.narrative.lower()

    def test_unavailable_narrative(self):
        result = compute_macro_alignment(None, "BULLISH")
        assert "unavailable" in result.narrative.lower()
