"""
Tests for core/strategies/ — Strategy Condition Evaluation Layer.

Verifies:
    - A strategy can be evaluated without affecting trading
    - Missing data is reported correctly
    - Conditions fail safely
    - No production pipeline changes occur
    - All evaluation logic dispatches correctly
    - Diagnostics generate expected output
"""

import pytest

from core.strategies.conditions import (
    Condition,
    ConditionCategory,
    ConditionEvaluation,
    ConditionResult,
    StrategyConditionSet,
    get_all_condition_sets,
    get_conditions_for_strategy,
    has_conditions,
    STRATEGY_CONDITIONS,
)
from core.strategies.condition_evaluator import (
    ConditionEvaluationResult,
    StrategyConditionEvaluator,
    build_market_snapshot,
)
from core.strategies.evaluation_diagnostics import (
    format_evaluation_report,
    format_full_evaluation_report,
    get_evaluation_summary,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CONDITION REGISTRY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestConditionRegistry:
    """Tests for condition definitions per strategy."""

    def test_all_five_strategies_have_conditions(self):
        """All 5 registered strategies must have condition sets."""
        expected = [
            "range_reversal_v1",
            "liquidity_sweep_reversal_v1",
            "momentum_expansion_v1",
            "trend_pullback_continuation_v1",
            "range_breakout_v1",
        ]
        for sid in expected:
            assert has_conditions(sid), f"Missing conditions for {sid}"

    def test_unknown_strategy_returns_none(self):
        """Unknown strategy returns None."""
        assert get_conditions_for_strategy("fake_strategy") is None

    def test_all_conditions_have_required_fields(self):
        """Every condition must have name, category, data_field."""
        for cs in get_all_condition_sets():
            for c in cs.all_conditions:
                assert c.name, f"Condition missing name in {cs.strategy_id}"
                assert c.category, f"Condition {c.name} missing category"
                assert c.data_field, f"Condition {c.name} missing data_field"

    def test_environment_conditions_exist(self):
        """Each strategy must have at least one environment condition."""
        for cs in get_all_condition_sets():
            assert len(cs.environment_conditions) > 0, (
                f"{cs.strategy_id} has no environment conditions"
            )

    def test_condition_set_properties(self):
        """StrategyConditionSet properties compute correctly."""
        cs = get_conditions_for_strategy("range_reversal_v1")
        assert len(cs.all_conditions) == len(cs.environment_conditions) + len(cs.entry_conditions)
        assert len(cs.required_conditions) > 0
        # Optional conditions exist (no_strong_momentum, structure_quality)
        assert len(cs.optional_conditions) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# EVALUATOR — RANGE REVERSAL STRATEGY
# ═══════════════════════════════════════════════════════════════════════════════


class TestRangeReversalEvaluation:
    """Tests evaluating range_reversal_v1 against various market states."""

    def setup_method(self):
        self.evaluator = StrategyConditionEvaluator()

    def test_fully_met_conditions(self):
        """All conditions satisfied returns FULLY_MET."""
        snapshot = build_market_snapshot(
            regime="RANGING",
            phase="REVERSAL",
            m15_at_key_level=True,
            m5_bias_strength=40.0,
            pattern_detected="HAMMER",
            m15_quality_score=0.6,
        )
        result = self.evaluator.evaluate("range_reversal_v1", snapshot)

        assert result.overall_status == "FULLY_MET"
        assert result.eligible_by_phase is True
        assert result.confidence > 0.0
        assert result.conditions_failed == 0

    def test_wrong_regime_fails(self):
        """Wrong regime makes strategy NOT_MET."""
        snapshot = build_market_snapshot(
            regime="TRENDING",
            phase="REVERSAL",
            m15_at_key_level=True,
            pattern_detected="HAMMER",
            m15_quality_score=0.6,
        )
        result = self.evaluator.evaluate("range_reversal_v1", snapshot)

        assert result.overall_status == "NOT_MET"
        assert result.eligible_by_phase is False

    def test_wrong_phase_fails(self):
        """Wrong phase makes strategy NOT_MET."""
        snapshot = build_market_snapshot(
            regime="RANGING",
            phase="IMPULSE",
            m15_at_key_level=True,
            pattern_detected="HAMMER",
        )
        result = self.evaluator.evaluate("range_reversal_v1", snapshot)

        assert result.eligible_by_phase is False
        assert result.overall_status == "NOT_MET"

    def test_missing_pattern_fails(self):
        """No pattern detected fails the pattern condition."""
        snapshot = build_market_snapshot(
            regime="RANGING",
            phase="REVERSAL",
            m15_at_key_level=True,
        )
        result = self.evaluator.evaluate("range_reversal_v1", snapshot)

        # Pattern is required but missing — should report missing data
        assert "pattern_detected" in result.missing_data or result.conditions_failed > 0

    def test_not_at_key_level_fails(self):
        """at_key_level=False fails the location condition."""
        snapshot = build_market_snapshot(
            regime="RANGING",
            phase="REVERSAL",
            m15_at_key_level=False,
            pattern_detected="HAMMER",
        )
        result = self.evaluator.evaluate("range_reversal_v1", snapshot)

        assert result.conditions_failed >= 1
        assert result.overall_status == "NOT_MET"

    def test_high_bias_strength_optional_fail(self):
        """High bias strength fails optional condition but strategy still passes."""
        snapshot = build_market_snapshot(
            regime="RANGING",
            phase="REVERSAL",
            m15_at_key_level=True,
            m5_bias_strength=85.0,
            pattern_detected="HAMMER",
            m15_quality_score=0.6,
        )
        result = self.evaluator.evaluate("range_reversal_v1", snapshot)

        # Optional condition fails but required pass
        assert result.overall_status == "FULLY_MET"
        assert result.conditions_failed >= 1  # Optional fail counts


# ═══════════════════════════════════════════════════════════════════════════════
# EVALUATOR — MOMENTUM EXPANSION STRATEGY
# ═══════════════════════════════════════════════════════════════════════════════


class TestMomentumExpansionEvaluation:
    """Tests evaluating momentum_expansion_v1."""

    def setup_method(self):
        self.evaluator = StrategyConditionEvaluator()

    def test_fully_met_momentum(self):
        """Momentum conditions fully satisfied."""
        snapshot = build_market_snapshot(
            regime="TRENDING",
            phase="IMPULSE",
            h1_direction="BULLISH",
            pattern_detected="THREE_WHITE_SOLDIERS",
            h4_trend_strength=0.7,
            h1_bos_confirmed=True,
        )
        result = self.evaluator.evaluate("momentum_expansion_v1", snapshot)

        assert result.eligible_by_phase is True
        assert result.overall_status == "FULLY_MET"

    def test_wrong_regime_for_momentum(self):
        """Momentum needs TRENDING regime."""
        snapshot = build_market_snapshot(
            regime="RANGING",
            phase="IMPULSE",
            h1_direction="BULLISH",
            pattern_detected="THREE_WHITE_SOLDIERS",
        )
        result = self.evaluator.evaluate("momentum_expansion_v1", snapshot)

        assert result.eligible_by_phase is False
        assert result.overall_status == "NOT_MET"

    def test_neutral_h1_fails_bias(self):
        """NEUTRAL H1 direction fails bias alignment."""
        snapshot = build_market_snapshot(
            regime="TRENDING",
            phase="IMPULSE",
            h1_direction="NEUTRAL",
            pattern_detected="THREE_WHITE_SOLDIERS",
        )
        result = self.evaluator.evaluate("momentum_expansion_v1", snapshot)

        assert result.conditions_failed >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# EVALUATOR — STRATEGIES WITH UNAVAILABLE DATA
# ═══════════════════════════════════════════════════════════════════════════════


class TestUnavailableConditions:
    """Tests for strategies that have conditions requiring unavailable data."""

    def setup_method(self):
        self.evaluator = StrategyConditionEvaluator()

    def test_liquidity_sweep_has_unavailable(self):
        """liquidity_sweep_reversal_v1 has unavailable liquidity data."""
        snapshot = build_market_snapshot(
            regime="RANGING",
            phase="REVERSAL",
            m15_at_key_level=True,
            pattern_detected="HAMMER",
        )
        result = self.evaluator.evaluate("liquidity_sweep_reversal_v1", snapshot)

        assert len(result.unavailable_conditions) > 0
        assert "liquidity_levels_available" in result.unavailable_conditions

    def test_continuation_has_unavailable_patterns(self):
        """trend_pullback_continuation_v1 has no patterns in library."""
        snapshot = build_market_snapshot(
            regime="TRENDING",
            phase="PULLBACK",
            h1_direction="BULLISH",
        )
        result = self.evaluator.evaluate("trend_pullback_continuation_v1", snapshot)

        assert "continuation_pattern_detected" in result.unavailable_conditions

    def test_breakout_has_unavailable_data(self):
        """range_breakout_v1 has unavailable range and pattern data."""
        snapshot = build_market_snapshot(
            regime="RANGING",
            phase="CONSOLIDATION",
        )
        result = self.evaluator.evaluate("range_breakout_v1", snapshot)

        assert len(result.unavailable_conditions) >= 2

    def test_unavailable_conditions_are_not_applicable(self):
        """Unavailable conditions should be NOT_APPLICABLE, not FAILED."""
        snapshot = build_market_snapshot(
            regime="TRENDING",
            phase="PULLBACK",
            h1_direction="BULLISH",
            m15_quality_score=0.5,
        )
        result = self.evaluator.evaluate("trend_pullback_continuation_v1", snapshot)

        na_evals = [
            e for e in result.evaluations
            if e.result == ConditionResult.NOT_APPLICABLE
        ]
        assert len(na_evals) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# EVALUATOR — MISSING DATA HANDLING
# ═══════════════════════════════════════════════════════════════════════════════


class TestMissingDataHandling:
    """Tests that missing data is reported, not treated as failure."""

    def setup_method(self):
        self.evaluator = StrategyConditionEvaluator()

    def test_empty_snapshot_reports_missing(self):
        """Empty snapshot should report all fields as missing."""
        snapshot = build_market_snapshot()  # All defaults (empty strings, 0, False)
        result = self.evaluator.evaluate("range_reversal_v1", snapshot)

        # regime="" and phase="" should be MISSING_DATA
        assert len(result.missing_data) > 0

    def test_partial_data_reports_gaps(self):
        """Partial data shows what's available and what's missing."""
        snapshot = build_market_snapshot(
            regime="RANGING",
            phase="REVERSAL",
            # No at_key_level, no pattern — these should show
        )
        result = self.evaluator.evaluate("range_reversal_v1", snapshot)

        # at_key_level=False is data (falsy), pattern="" is missing
        assert "pattern_detected" in result.missing_data

    def test_missing_data_does_not_crash(self):
        """Evaluator should never raise, only report."""
        snapshot = {}  # Completely empty dict
        result = self.evaluator.evaluate("range_reversal_v1", snapshot)

        assert isinstance(result, ConditionEvaluationResult)
        assert result.strategy_id == "range_reversal_v1"

    def test_unknown_strategy_handled(self):
        """Unknown strategy returns safe result."""
        snapshot = build_market_snapshot(regime="RANGING", phase="REVERSAL")
        result = self.evaluator.evaluate("nonexistent_strategy", snapshot)

        assert result.overall_status == "NO_CONDITIONS_DEFINED"
        assert result.conditions_checked == 0


# ═══════════════════════════════════════════════════════════════════════════════
# EVALUATOR — EVALUATE ALL
# ═══════════════════════════════════════════════════════════════════════════════


class TestEvaluateAll:
    """Tests for evaluate_all method."""

    def setup_method(self):
        self.evaluator = StrategyConditionEvaluator()

    def test_evaluate_all_returns_all_strategies(self):
        """evaluate_all returns one result per registered strategy."""
        snapshot = build_market_snapshot(
            regime="RANGING",
            phase="REVERSAL",
            m15_at_key_level=True,
            pattern_detected="HAMMER",
        )
        results = self.evaluator.evaluate_all(snapshot)

        assert len(results) == 5
        ids = [r.strategy_id for r in results]
        assert "range_reversal_v1" in ids
        assert "momentum_expansion_v1" in ids

    def test_only_matching_strategies_eligible(self):
        """Only strategies matching the phase should be eligible."""
        snapshot = build_market_snapshot(
            regime="RANGING",
            phase="REVERSAL",
            m15_at_key_level=True,
            pattern_detected="HAMMER",
        )
        results = self.evaluator.evaluate_all(snapshot)

        eligible = [r for r in results if r.eligible_by_phase]
        # range_reversal_v1 and liquidity_sweep_reversal_v1 should be eligible
        eligible_ids = [r.strategy_id for r in eligible]
        assert "range_reversal_v1" in eligible_ids
        assert "liquidity_sweep_reversal_v1" in eligible_ids
        # momentum should NOT be eligible (needs IMPULSE)
        assert "momentum_expansion_v1" not in eligible_ids


# ═══════════════════════════════════════════════════════════════════════════════
# EVALUATOR — INDIVIDUAL CONDITION TYPE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestConditionEvaluationTypes:
    """Tests for each evaluation dispatch type."""

    def setup_method(self):
        self.evaluator = StrategyConditionEvaluator()

    def test_enum_match_passes(self):
        """enum_match with value in expected_values passes."""
        condition = Condition(
            name="test_enum",
            description="test",
            category=ConditionCategory.ENVIRONMENT,
            data_field="regime",
            evaluator_key="enum_match",
            expected_values=("RANGING", "TRANSITIONAL"),
            comparison="in",
        )
        snapshot = {"regime": "RANGING"}
        result = self.evaluator._evaluate_condition(condition, snapshot)
        assert result.passed

    def test_enum_match_fails(self):
        """enum_match with value not in expected_values fails."""
        condition = Condition(
            name="test_enum",
            description="test",
            category=ConditionCategory.ENVIRONMENT,
            data_field="regime",
            evaluator_key="enum_match",
            expected_values=("RANGING",),
            comparison="in",
        )
        snapshot = {"regime": "TRENDING"}
        result = self.evaluator._evaluate_condition(condition, snapshot)
        assert not result.passed

    def test_bool_check_true(self):
        """bool_check with True value passes."""
        condition = Condition(
            name="test_bool",
            description="test",
            category=ConditionCategory.LOCATION,
            data_field="m15.at_key_level",
            evaluator_key="bool_check",
            comparison="bool_true",
        )
        snapshot = {"m15.at_key_level": True}
        result = self.evaluator._evaluate_condition(condition, snapshot)
        assert result.passed

    def test_bool_check_false(self):
        """bool_check with False value fails."""
        condition = Condition(
            name="test_bool",
            description="test",
            category=ConditionCategory.LOCATION,
            data_field="m15.at_key_level",
            evaluator_key="bool_check",
            comparison="bool_true",
        )
        snapshot = {"m15.at_key_level": False}
        result = self.evaluator._evaluate_condition(condition, snapshot)
        assert not result.passed

    def test_numeric_gte_passes(self):
        """numeric_threshold with value >= threshold passes."""
        condition = Condition(
            name="test_num",
            description="test",
            category=ConditionCategory.STRUCTURE,
            data_field="m15.quality_score",
            evaluator_key="numeric_threshold",
            threshold=0.3,
            comparison="gte",
        )
        snapshot = {"m15.quality_score": 0.5}
        result = self.evaluator._evaluate_condition(condition, snapshot)
        assert result.passed

    def test_numeric_gte_fails(self):
        """numeric_threshold with value < threshold fails."""
        condition = Condition(
            name="test_num",
            description="test",
            category=ConditionCategory.STRUCTURE,
            data_field="m15.quality_score",
            evaluator_key="numeric_threshold",
            threshold=0.3,
            comparison="gte",
        )
        snapshot = {"m15.quality_score": 0.1}
        result = self.evaluator._evaluate_condition(condition, snapshot)
        assert not result.passed

    def test_numeric_lte_passes(self):
        """numeric_threshold lte with value <= threshold passes."""
        condition = Condition(
            name="test_num",
            description="test",
            category=ConditionCategory.MOMENTUM,
            data_field="m5.bias_strength",
            evaluator_key="numeric_threshold",
            threshold=70.0,
            comparison="lte",
        )
        snapshot = {"m5.bias_strength": 50.0}
        result = self.evaluator._evaluate_condition(condition, snapshot)
        assert result.passed

    def test_pattern_family_check_passes(self):
        """Pattern in expected triggers passes."""
        condition = Condition(
            name="test_pattern",
            description="test",
            category=ConditionCategory.PATTERN,
            data_field="pattern_detected",
            evaluator_key="pattern_family_check",
            expected_values=("HAMMER", "SHOOTING_STAR"),
            comparison="in",
        )
        snapshot = {"pattern_detected": "HAMMER"}
        result = self.evaluator._evaluate_condition(condition, snapshot)
        assert result.passed

    def test_pattern_family_check_fails(self):
        """Pattern not in expected triggers fails."""
        condition = Condition(
            name="test_pattern",
            description="test",
            category=ConditionCategory.PATTERN,
            data_field="pattern_detected",
            evaluator_key="pattern_family_check",
            expected_values=("HAMMER", "SHOOTING_STAR"),
            comparison="in",
        )
        snapshot = {"pattern_detected": "THREE_WHITE_SOLDIERS"}
        result = self.evaluator._evaluate_condition(condition, snapshot)
        assert not result.passed

    def test_bias_alignment_passes(self):
        """Directional bias (not NEUTRAL) passes."""
        condition = Condition(
            name="test_bias",
            description="test",
            category=ConditionCategory.MOMENTUM,
            data_field="h1.direction",
            evaluator_key="bias_alignment_check",
            comparison="not_neutral",
        )
        snapshot = {"h1.direction": "BULLISH"}
        result = self.evaluator._evaluate_condition(condition, snapshot)
        assert result.passed

    def test_bias_alignment_neutral_fails(self):
        """NEUTRAL direction fails bias alignment."""
        condition = Condition(
            name="test_bias",
            description="test",
            category=ConditionCategory.MOMENTUM,
            data_field="h1.direction",
            evaluator_key="bias_alignment_check",
            comparison="not_neutral",
        )
        snapshot = {"h1.direction": "NEUTRAL"}
        result = self.evaluator._evaluate_condition(condition, snapshot)
        assert not result.passed

    def test_unavailable_evaluator(self):
        """Unavailable evaluator returns NOT_APPLICABLE."""
        condition = Condition(
            name="test_unavailable",
            description="test",
            category=ConditionCategory.LOCATION,
            data_field="liquidity_levels",
            evaluator_key="unavailable",
            comparison="eq",
        )
        snapshot = {"liquidity_levels": "anything"}
        result = self.evaluator._evaluate_condition(condition, snapshot)
        assert result.result == ConditionResult.NOT_APPLICABLE


# ═══════════════════════════════════════════════════════════════════════════════
# DIAGNOSTICS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestEvaluationDiagnostics:
    """Tests for diagnostic report generation."""

    def setup_method(self):
        self.evaluator = StrategyConditionEvaluator()
        self.snapshot = build_market_snapshot(
            regime="RANGING",
            phase="REVERSAL",
            m15_at_key_level=True,
            m5_bias_strength=40.0,
            pattern_detected="HAMMER",
            m15_quality_score=0.6,
        )

    def test_single_report_contains_strategy_name(self):
        """Single report must show strategy ID."""
        result = self.evaluator.evaluate("range_reversal_v1", self.snapshot)
        report = format_evaluation_report(result)
        assert "range_reversal_v1" in report

    def test_single_report_contains_status(self):
        """Single report must show overall status."""
        result = self.evaluator.evaluate("range_reversal_v1", self.snapshot)
        report = format_evaluation_report(result)
        assert result.overall_status in report

    def test_single_report_contains_conditions(self):
        """Single report must show condition names."""
        result = self.evaluator.evaluate("range_reversal_v1", self.snapshot)
        report = format_evaluation_report(result)
        assert "regime_is_ranging" in report
        assert "at_key_level" in report

    def test_single_report_contains_pass_fail_icons(self):
        """Report must contain pass/fail indicators."""
        result = self.evaluator.evaluate("range_reversal_v1", self.snapshot)
        report = format_evaluation_report(result)
        assert "[PASS]" in report

    def test_full_report_contains_all_strategies(self):
        """Full report must mention all strategies."""
        results = self.evaluator.evaluate_all(self.snapshot)
        report = format_full_evaluation_report(results)
        assert "range_reversal_v1" in report
        assert "momentum_expansion_v1" in report
        assert "range_breakout_v1" in report

    def test_full_report_has_summary_table(self):
        """Full report must have summary section."""
        results = self.evaluator.evaluate_all(self.snapshot)
        report = format_full_evaluation_report(results)
        assert "SUMMARY" in report

    def test_summary_dict_structure(self):
        """get_evaluation_summary returns expected structure."""
        results = self.evaluator.evaluate_all(self.snapshot)
        summary = get_evaluation_summary(results)

        assert summary["total_strategies"] == 5
        assert "phase_eligible" in summary
        assert "fully_met" in summary
        assert "partially_met" in summary
        assert "not_met" in summary
        assert "strategies" in summary
        assert "range_reversal_v1" in summary["strategies"]

    def test_summary_identifies_eligible(self):
        """Summary must identify phase-eligible strategies."""
        results = self.evaluator.evaluate_all(self.snapshot)
        summary = get_evaluation_summary(results)

        assert "range_reversal_v1" in summary["phase_eligible"]
        assert "momentum_expansion_v1" not in summary["phase_eligible"]


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY TESTS — NO TRADING IMPACT
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoTradingImpact:
    """Verify the condition evaluator has zero impact on trading."""

    def setup_method(self):
        self.evaluator = StrategyConditionEvaluator()

    def test_evaluation_is_pure_observation(self):
        """Evaluating conditions must not modify any registry state."""
        from core.strategies.registry import STRATEGY_REGISTRY, get_all_strategies
        before = [(s.strategy_id, s.status.value) for s in get_all_strategies()]

        snapshot = build_market_snapshot(
            regime="RANGING", phase="REVERSAL",
            m15_at_key_level=True, pattern_detected="HAMMER",
        )
        self.evaluator.evaluate_all(snapshot)

        after = [(s.strategy_id, s.status.value) for s in get_all_strategies()]
        assert before == after

    def test_evaluation_result_is_frozen(self):
        """ConditionEvaluationResult must be immutable."""
        snapshot = build_market_snapshot(regime="RANGING", phase="REVERSAL")
        result = self.evaluator.evaluate("range_reversal_v1", snapshot)

        with pytest.raises(Exception):
            result.overall_status = "CHANGED"  # type: ignore

    def test_multiple_evaluations_independent(self):
        """Multiple evaluations must not affect each other."""
        snapshot1 = build_market_snapshot(regime="RANGING", phase="REVERSAL")
        snapshot2 = build_market_snapshot(regime="TRENDING", phase="IMPULSE")

        r1 = self.evaluator.evaluate("range_reversal_v1", snapshot1)
        r2 = self.evaluator.evaluate("range_reversal_v1", snapshot2)

        # Results should differ
        assert r1.eligible_by_phase != r2.eligible_by_phase

    def test_build_market_snapshot_is_dict(self):
        """build_market_snapshot returns a plain dict."""
        snapshot = build_market_snapshot(regime="RANGING")
        assert isinstance(snapshot, dict)
        assert snapshot["regime"] == "RANGING"


# ═══════════════════════════════════════════════════════════════════════════════
# RESULT PROPERTIES
# ═══════════════════════════════════════════════════════════════════════════════


class TestResultProperties:
    """Tests for ConditionEvaluationResult computed properties."""

    def setup_method(self):
        self.evaluator = StrategyConditionEvaluator()

    def test_pass_rate_calculation(self):
        """pass_rate should be passed/checked."""
        snapshot = build_market_snapshot(
            regime="RANGING",
            phase="REVERSAL",
            m15_at_key_level=True,
            pattern_detected="HAMMER",
            m15_quality_score=0.6,
        )
        result = self.evaluator.evaluate("range_reversal_v1", snapshot)
        expected_rate = result.conditions_passed / result.conditions_checked
        assert abs(result.pass_rate - expected_rate) < 0.001

    def test_pass_rate_zero_for_empty(self):
        """pass_rate should be 0 when nothing checked."""
        snapshot = build_market_snapshot()
        result = self.evaluator.evaluate("nonexistent_strategy", snapshot)
        assert result.pass_rate == 0.0

    def test_metadata_contains_counts(self):
        """Metadata should contain total/required/optional counts."""
        snapshot = build_market_snapshot(regime="RANGING", phase="REVERSAL")
        result = self.evaluator.evaluate("range_reversal_v1", snapshot)

        assert "total_conditions" in result.metadata
        assert "required_count" in result.metadata
        assert "optional_count" in result.metadata
        assert result.metadata["total_conditions"] > 0
