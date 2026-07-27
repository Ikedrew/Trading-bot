"""
Tests for core/strategies/ — Strategy Framework Foundation.

Verifies:
    - Strategy registry loads correctly
    - All strategy definitions are valid
    - Strategy families match Strategy Family Layer
    - Unknown strategies fail safely
    - Inactive strategies cannot activate
    - Diagnostics generate correctly
    - No existing trading behaviour changes
"""

import pytest

from core.strategy_family.models import StrategyFamily
from core.strategies import (
    STRATEGY_REGISTRY,
    EvidenceStatus,
    ExitModel,
    RiskModel,
    StrategyAuthority,
    StrategyDefinition,
    StrategyEvaluationResult,
    StrategyStatus,
    format_diagnostic_report,
    get_active_strategies,
    get_all_strategies,
    get_status_distribution,
    get_strategies_by_family,
    get_strategies_by_status,
    get_strategy,
    get_strategy_ids,
    get_summary_dict,
)


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestStrategyRegistry:
    """Tests for strategy registry loading and integrity."""

    def test_registry_loads(self):
        """Registry must be a non-empty dict."""
        assert isinstance(STRATEGY_REGISTRY, dict)
        assert len(STRATEGY_REGISTRY) == 5

    def test_all_strategy_ids_present(self):
        """All 5 expected strategies must be registered."""
        expected = [
            "range_reversal_v1",
            "liquidity_sweep_reversal_v1",
            "momentum_expansion_v1",
            "trend_pullback_continuation_v1",
            "range_breakout_v1",
        ]
        ids = get_strategy_ids()
        for strategy_id in expected:
            assert strategy_id in ids, f"Missing strategy: {strategy_id}"

    def test_get_strategy_by_id(self):
        """get_strategy returns correct definition."""
        s = get_strategy("range_reversal_v1")
        assert s is not None
        assert s.strategy_id == "range_reversal_v1"
        assert s.name == "Range Reversal V1"

    def test_unknown_strategy_returns_none(self):
        """Unknown strategy ID returns None, not raises."""
        assert get_strategy("totally_fake_strategy") is None

    def test_get_all_strategies_returns_list(self):
        """get_all_strategies returns complete list."""
        strategies = get_all_strategies()
        assert len(strategies) == 5
        assert all(isinstance(s, StrategyDefinition) for s in strategies)

    def test_get_strategy_ids(self):
        """get_strategy_ids returns all IDs."""
        ids = get_strategy_ids()
        assert len(ids) == 5
        assert "range_reversal_v1" in ids


class TestStrategyDefinitionValidity:
    """Tests that all strategy definitions have valid, complete data."""

    @pytest.fixture
    def all_strategies(self):
        return get_all_strategies()

    def test_all_have_strategy_id(self, all_strategies):
        """Every strategy must have a non-empty ID."""
        for s in all_strategies:
            assert s.strategy_id, f"Strategy missing ID: {s}"

    def test_all_have_name(self, all_strategies):
        """Every strategy must have a non-empty name."""
        for s in all_strategies:
            assert s.name, f"Strategy {s.strategy_id} missing name"

    def test_all_have_description(self, all_strategies):
        """Every strategy must have a non-empty description."""
        for s in all_strategies:
            assert s.description, f"Strategy {s.strategy_id} missing description"

    def test_all_have_valid_family(self, all_strategies):
        """Every strategy must reference a valid StrategyFamily."""
        for s in all_strategies:
            assert isinstance(s.strategy_family, StrategyFamily), (
                f"Strategy {s.strategy_id} has invalid family: {s.strategy_family}"
            )

    def test_all_have_valid_market_phases(self, all_strategies):
        """Every strategy must define at least one valid market phase."""
        for s in all_strategies:
            assert len(s.valid_market_phases) > 0, (
                f"Strategy {s.strategy_id} has no valid_market_phases"
            )

    def test_all_have_valid_status(self, all_strategies):
        """Every strategy must have a valid StrategyStatus."""
        for s in all_strategies:
            assert isinstance(s.status, StrategyStatus), (
                f"Strategy {s.strategy_id} has invalid status: {s.status}"
            )

    def test_all_have_risk_model(self, all_strategies):
        """Every strategy must have a RiskModel."""
        for s in all_strategies:
            assert isinstance(s.risk_model, RiskModel), (
                f"Strategy {s.strategy_id} missing RiskModel"
            )

    def test_all_have_exit_model(self, all_strategies):
        """Every strategy must have an ExitModel."""
        for s in all_strategies:
            assert isinstance(s.exit_model, ExitModel), (
                f"Strategy {s.strategy_id} missing ExitModel"
            )

    def test_all_have_evidence_status(self, all_strategies):
        """Every strategy must have an EvidenceStatus."""
        for s in all_strategies:
            assert isinstance(s.evidence_status, EvidenceStatus), (
                f"Strategy {s.strategy_id} missing EvidenceStatus"
            )

    def test_definitions_are_frozen(self):
        """Strategy definitions must be immutable."""
        s = get_strategy("range_reversal_v1")
        with pytest.raises(Exception):
            s.name = "Modified"  # type: ignore


# ═══════════════════════════════════════════════════════════════════════════════
# FAMILY ALIGNMENT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestFamilyAlignment:
    """Tests that strategy families match the Strategy Family Layer."""

    def test_reversal_strategies_in_reversal_family(self):
        """Reversal strategies must reference REVERSAL family."""
        reversal = get_strategies_by_family(StrategyFamily.REVERSAL)
        assert len(reversal) == 2
        ids = [s.strategy_id for s in reversal]
        assert "range_reversal_v1" in ids
        assert "liquidity_sweep_reversal_v1" in ids

    def test_momentum_strategies_in_momentum_family(self):
        """Momentum strategies must reference MOMENTUM family."""
        momentum = get_strategies_by_family(StrategyFamily.MOMENTUM)
        assert len(momentum) == 1
        assert momentum[0].strategy_id == "momentum_expansion_v1"

    def test_continuation_strategies_in_continuation_family(self):
        """Continuation strategies must reference CONTINUATION family."""
        continuation = get_strategies_by_family(StrategyFamily.CONTINUATION)
        assert len(continuation) == 1
        assert continuation[0].strategy_id == "trend_pullback_continuation_v1"

    def test_breakout_strategies_in_breakout_family(self):
        """Breakout strategies must reference BREAKOUT family."""
        breakout = get_strategies_by_family(StrategyFamily.BREAKOUT)
        assert len(breakout) == 1
        assert breakout[0].strategy_id == "range_breakout_v1"

    def test_mean_reversion_family_has_no_strategies(self):
        """MEAN_REVERSION family should have no strategies yet."""
        mr = get_strategies_by_family(StrategyFamily.MEAN_REVERSION)
        assert mr == []


# ═══════════════════════════════════════════════════════════════════════════════
# STATUS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestStrategyStatus:
    """Tests for strategy lifecycle status."""

    def test_all_strategies_are_hypothesis(self):
        """All current strategies must be in HYPOTHESIS status."""
        for s in get_all_strategies():
            assert s.status == StrategyStatus.HYPOTHESIS, (
                f"Strategy {s.strategy_id} should be HYPOTHESIS, is {s.status.value}"
            )

    def test_no_active_strategies(self):
        """There must be zero ACTIVE strategies."""
        active = get_active_strategies()
        assert active == []

    def test_status_distribution(self):
        """Status distribution must show 5 HYPOTHESIS, 0 everything else."""
        dist = get_status_distribution()
        assert dist["HYPOTHESIS"] == 5
        assert dist["ACTIVE"] == 0
        assert dist["RESEARCHING"] == 0
        assert dist["SHADOW_TESTING"] == 0
        assert dist["VALIDATED"] == 0
        assert dist["DISABLED"] == 0

    def test_get_by_status(self):
        """get_strategies_by_status returns correct subset."""
        hypotheses = get_strategies_by_status(StrategyStatus.HYPOTHESIS)
        assert len(hypotheses) == 5

        active = get_strategies_by_status(StrategyStatus.ACTIVE)
        assert active == []

    def test_is_hypothesis_property(self):
        """is_hypothesis property works correctly."""
        s = get_strategy("range_reversal_v1")
        assert s.is_hypothesis is True
        assert s.is_active is False

    def test_can_activate_property_false_for_hypothesis(self):
        """can_activate must be False for HYPOTHESIS strategies."""
        for s in get_all_strategies():
            assert s.can_activate is False


# ═══════════════════════════════════════════════════════════════════════════════
# AUTHORITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestStrategyAuthority:
    """Tests for the StrategyAuthority."""

    def setup_method(self):
        self.authority = StrategyAuthority()

    def test_default_mode_is_observation(self):
        """Default mode must be OBSERVATION."""
        assert self.authority.mode == "OBSERVATION"

    def test_get_available_strategies(self):
        """get_available_strategies returns all registered."""
        strategies = self.authority.get_available_strategies()
        assert len(strategies) == 5

    def test_get_by_family(self):
        """get_by_family delegates correctly."""
        reversal = self.authority.get_by_family(StrategyFamily.REVERSAL)
        assert len(reversal) == 2

    def test_get_by_id(self):
        """get_by_id returns correct strategy."""
        s = self.authority.get_by_id("momentum_expansion_v1")
        assert s is not None
        assert s.strategy_family == StrategyFamily.MOMENTUM

    def test_get_by_id_unknown(self):
        """get_by_id returns None for unknown."""
        assert self.authority.get_by_id("fake") is None

    def test_safety_check_passes(self):
        """verify_no_active_strategies must return True."""
        assert self.authority.verify_no_active_strategies() is True


class TestContextEvaluation:
    """Tests for evaluate_context method."""

    def setup_method(self):
        self.authority = StrategyAuthority()

    def test_evaluate_returns_results_for_all(self):
        """evaluate_context must return one result per strategy."""
        results = self.authority.evaluate_context(regime="RANGE", phase="REVERSAL")
        assert len(results) == 5
        assert all(isinstance(r, StrategyEvaluationResult) for r in results)

    def test_reversal_phase_matches_reversal_strategies(self):
        """Reversal phase should mark reversal strategies as eligible."""
        results = self.authority.evaluate_context(regime="RANGE", phase="REVERSAL")
        reversal_results = [r for r in results if r.strategy_id == "range_reversal_v1"]
        assert len(reversal_results) == 1
        assert reversal_results[0].eligible is True

    def test_impulse_phase_matches_momentum(self):
        """Impulse phase should mark momentum strategy as eligible."""
        results = self.authority.evaluate_context(regime="TRENDING", phase="IMPULSE")
        momentum_results = [r for r in results if r.strategy_id == "momentum_expansion_v1"]
        assert len(momentum_results) == 1
        assert momentum_results[0].eligible is True

    def test_phase_mismatch_marks_ineligible(self):
        """Strategy with non-matching phase should be ineligible."""
        results = self.authority.evaluate_context(regime="RANGE", phase="PULLBACK")
        # range_reversal_v1 valid phases are REVERSAL, EXHAUSTION — not PULLBACK
        reversal_results = [r for r in results if r.strategy_id == "range_reversal_v1"]
        assert reversal_results[0].eligible is False

    def test_empty_phase_all_eligible(self):
        """No phase filter means all non-disabled strategies are eligible."""
        results = self.authority.evaluate_context(regime="RANGE", phase="")
        eligible_count = sum(1 for r in results if r.eligible)
        assert eligible_count == 5

    def test_evaluation_mode_in_metadata(self):
        """Evaluation results should include mode metadata."""
        results = self.authority.evaluate_context(phase="REVERSAL")
        for r in results:
            assert r.metadata["mode"] == "OBSERVATION"

    def test_evaluation_confidence_is_zero(self):
        """In OBSERVATION mode, confidence should be 0 (no research backing)."""
        results = self.authority.evaluate_context(phase="REVERSAL")
        for r in results:
            assert r.confidence == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# RESEARCH PROMOTION GATE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestResearchPromotionGate:
    """Tests that strategies cannot activate without valid evidence."""

    def setup_method(self):
        self.authority = StrategyAuthority()

    def test_no_evidence_cannot_promote(self):
        """Strategy with no evidence cannot be promoted."""
        evidence = EvidenceStatus(
            sample_size=0,
            expectancy_r=0.0,
            p_value=1.0,
            walk_forward_validated=False,
            out_of_sample_validated=False,
        )
        result = self.authority.load_research_validation("range_reversal_v1", evidence)
        assert result is False

    def test_insufficient_sample_cannot_promote(self):
        """Strategy with too few samples cannot be promoted."""
        evidence = EvidenceStatus(
            sample_size=50,
            expectancy_r=0.15,
            p_value=0.03,
            walk_forward_validated=True,
            out_of_sample_validated=True,
        )
        result = self.authority.load_research_validation("range_reversal_v1", evidence)
        assert result is False

    def test_negative_expectancy_cannot_promote(self):
        """Strategy with negative EV cannot be promoted."""
        evidence = EvidenceStatus(
            sample_size=200,
            expectancy_r=-0.05,
            p_value=0.03,
            walk_forward_validated=True,
            out_of_sample_validated=True,
        )
        result = self.authority.load_research_validation("range_reversal_v1", evidence)
        assert result is False

    def test_high_p_value_cannot_promote(self):
        """Strategy with p >= 0.05 cannot be promoted."""
        evidence = EvidenceStatus(
            sample_size=200,
            expectancy_r=0.15,
            p_value=0.10,
            walk_forward_validated=True,
            out_of_sample_validated=True,
        )
        result = self.authority.load_research_validation("range_reversal_v1", evidence)
        assert result is False

    def test_no_walk_forward_cannot_promote(self):
        """Strategy without walk-forward cannot be promoted."""
        evidence = EvidenceStatus(
            sample_size=200,
            expectancy_r=0.15,
            p_value=0.03,
            walk_forward_validated=False,
            out_of_sample_validated=True,
        )
        result = self.authority.load_research_validation("range_reversal_v1", evidence)
        assert result is False

    def test_no_out_of_sample_cannot_promote(self):
        """Strategy without OOS validation cannot be promoted."""
        evidence = EvidenceStatus(
            sample_size=200,
            expectancy_r=0.15,
            p_value=0.03,
            walk_forward_validated=True,
            out_of_sample_validated=False,
        )
        result = self.authority.load_research_validation("range_reversal_v1", evidence)
        assert result is False

    def test_full_valid_evidence_promotes(self):
        """Strategy with all criteria met should return True."""
        evidence = EvidenceStatus(
            sample_size=200,
            expectancy_r=0.15,
            p_value=0.003,
            walk_forward_validated=True,
            out_of_sample_validated=True,
            experiment_sources=("M10_strategy_family_per_phase",),
        )
        result = self.authority.load_research_validation("range_reversal_v1", evidence)
        assert result is True

    def test_unknown_strategy_cannot_promote(self):
        """Unknown strategy ID should return False."""
        evidence = EvidenceStatus(
            sample_size=200,
            expectancy_r=0.15,
            p_value=0.003,
            walk_forward_validated=True,
            out_of_sample_validated=True,
        )
        result = self.authority.load_research_validation("nonexistent_strategy", evidence)
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# DIAGNOSTICS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestDiagnostics:
    """Tests for diagnostic output."""

    def setup_method(self):
        self.authority = StrategyAuthority()

    def test_diagnostic_dict_structure(self):
        """get_diagnostic must return expected structure."""
        diag = self.authority.get_diagnostic()
        assert diag["mode"] == "OBSERVATION"
        assert diag["total_strategies"] == 5
        assert diag["active_count"] == 0
        assert diag["safety_check"] is True
        assert "strategies_by_family" in diag
        assert "status_distribution" in diag
        assert "strategies" in diag

    def test_diagnostic_report_contains_mode(self):
        """Formatted report must show current mode."""
        report = format_diagnostic_report(self.authority)
        assert "OBSERVATION" in report

    def test_diagnostic_report_contains_strategies(self):
        """Formatted report must list strategy IDs."""
        report = format_diagnostic_report(self.authority)
        assert "range_reversal_v1" in report
        assert "momentum_expansion_v1" in report
        assert "range_breakout_v1" in report

    def test_diagnostic_report_contains_families(self):
        """Formatted report must show family groupings."""
        report = format_diagnostic_report(self.authority)
        assert "REVERSAL" in report
        assert "MOMENTUM" in report

    def test_diagnostic_report_contains_status(self):
        """Formatted report must show status distribution."""
        report = format_diagnostic_report(self.authority)
        assert "HYPOTHESIS" in report

    def test_summary_dict_structure(self):
        """get_summary_dict must return machine-readable structure."""
        summary = get_summary_dict(self.authority)
        assert summary["mode"] == "OBSERVATION"
        assert summary["total_strategies"] == 5
        assert summary["active_count"] == 0
        assert summary["safety_passed"] is True
        assert "evidence_gaps" in summary
        assert "pattern_gaps" in summary
        assert "warnings" in summary

    def test_summary_identifies_evidence_gaps(self):
        """All strategies should be in evidence_gaps (none have evidence)."""
        summary = get_summary_dict(self.authority)
        assert len(summary["evidence_gaps"]) == 5

    def test_summary_identifies_pattern_gaps(self):
        """Strategies without trigger patterns should appear in pattern_gaps."""
        summary = get_summary_dict(self.authority)
        # trend_pullback_continuation_v1 and range_breakout_v1 have no patterns
        assert "trend_pullback_continuation_v1" in summary["pattern_gaps"]
        assert "range_breakout_v1" in summary["pattern_gaps"]


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestModels:
    """Tests for data model correctness."""

    def test_strategy_status_enum_values(self):
        """All 6 status values must exist."""
        assert StrategyStatus.HYPOTHESIS.value == "HYPOTHESIS"
        assert StrategyStatus.RESEARCHING.value == "RESEARCHING"
        assert StrategyStatus.SHADOW_TESTING.value == "SHADOW_TESTING"
        assert StrategyStatus.VALIDATED.value == "VALIDATED"
        assert StrategyStatus.ACTIVE.value == "ACTIVE"
        assert StrategyStatus.DISABLED.value == "DISABLED"
        assert len(StrategyStatus) == 6

    def test_evidence_status_has_evidence(self):
        """has_evidence should be True when sample_size > 0."""
        no_evidence = EvidenceStatus(sample_size=0)
        assert no_evidence.has_evidence is False

        has_evidence = EvidenceStatus(sample_size=10)
        assert has_evidence.has_evidence is True

    def test_evidence_status_activation_criteria(self):
        """meets_activation_criteria checks all fields."""
        valid = EvidenceStatus(
            sample_size=100,
            expectancy_r=0.1,
            p_value=0.04,
            walk_forward_validated=True,
            out_of_sample_validated=True,
        )
        assert valid.meets_activation_criteria is True

        # Edge: exactly 100 samples, just below p threshold
        edge = EvidenceStatus(
            sample_size=100,
            expectancy_r=0.001,
            p_value=0.049,
            walk_forward_validated=True,
            out_of_sample_validated=True,
        )
        assert edge.meets_activation_criteria is True

    def test_evaluation_result_reason_summary(self):
        """reason_summary joins reasons with semicolons."""
        r = StrategyEvaluationResult(
            strategy_id="test",
            eligible=True,
            reasons=("Phase matches", "Mode: OBSERVATION"),
        )
        assert "Phase matches" in r.reason_summary
        assert "Mode: OBSERVATION" in r.reason_summary

    def test_evaluation_result_empty_reasons(self):
        """Empty reasons should produce default message."""
        r = StrategyEvaluationResult(strategy_id="test", eligible=True, reasons=())
        assert r.reason_summary == "No reasons provided"


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoTradingBehaviourChange:
    """
    Verify that the strategy framework has zero impact on trading behaviour.

    These tests confirm the architectural boundary is maintained.
    """

    def test_no_strategies_are_active(self):
        """Zero strategies should be ACTIVE."""
        assert get_active_strategies() == []

    def test_authority_observation_mode(self):
        """Authority must be in OBSERVATION mode by default."""
        authority = StrategyAuthority()
        assert authority.mode == "OBSERVATION"

    def test_safety_verification_passes(self):
        """Safety check must confirm no active strategies."""
        authority = StrategyAuthority()
        assert authority.verify_no_active_strategies() is True

    def test_evaluate_does_not_mutate_registry(self):
        """Evaluating context must not change any strategy state."""
        authority = StrategyAuthority()
        before = [(s.strategy_id, s.status) for s in get_all_strategies()]
        authority.evaluate_context(regime="RANGE", phase="REVERSAL")
        after = [(s.strategy_id, s.status) for s in get_all_strategies()]
        assert before == after
