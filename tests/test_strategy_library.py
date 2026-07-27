"""
Tests for core/strategies/library/ — Strategy Knowledge Library.

Verifies:
    - Every strategy exists
    - Every strategy has a family
    - Every strategy has hypothesis
    - Every strategy has conditions
    - Every family has strategies
    - Unknown strategy returns None
    - Registry immutable
    - No execution imports
    - No decision pipeline changes
"""

import pytest

from core.strategies.library import (
    FAMILY_DEFINITIONS,
    STRATEGY_LIBRARY,
    ConfidenceLevel,
    EvidenceStatus,
    FamilyDefinition,
    StrategyDefinition,
    StrategyFamily,
    context_query_report,
    get_all_family_definitions,
    get_all_strategies,
    get_family_definition,
    get_library_summary,
    get_strategies_by_family,
    get_strategies_for_context,
    get_strategies_for_phase,
    get_strategies_for_regime,
    get_strategy,
    get_strategy_ids,
    strategy_library_report,
)


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRY COMPLETENESS
# ═══════════════════════════════════════════════════════════════════════════════


class TestRegistryCompleteness:
    """Every expected strategy must be present."""

    EXPECTED_STRATEGIES = [
        "range_reversal_v1",
        "liquidity_sweep_reversal_v1",
        "exhaustion_reversal_v1",
        "momentum_expansion_v1",
        "trend_acceleration_v1",
        "impulse_followthrough_v1",
        "trend_pullback_v1",
        "moving_average_pullback_v1",
        "structure_retest_v1",
        "range_breakout_v1",
        "volatility_breakout_v1",
        "structure_breakout_v1",
        "statistical_fade_v1",
        "range_mean_reversion_v1",
        "volatility_snapback_v1",
        "bos_continuation_v1",
        "choch_transition_v1",
    ]

    def test_total_strategy_count(self):
        """Library must contain exactly 17 strategies."""
        assert len(STRATEGY_LIBRARY) == 17

    def test_all_expected_strategies_exist(self):
        """Every named strategy must be present."""
        ids = get_strategy_ids()
        for sid in self.EXPECTED_STRATEGIES:
            assert sid in ids, f"Missing strategy: {sid}"

    def test_get_strategy_returns_definition(self):
        """get_strategy returns correct StrategyDefinition."""
        s = get_strategy("range_reversal_v1")
        assert s is not None
        assert isinstance(s, StrategyDefinition)
        assert s.strategy_id == "range_reversal_v1"

    def test_unknown_strategy_returns_none(self):
        """Unknown strategy ID returns None safely."""
        assert get_strategy("totally_fake_strategy_xyz") is None

    def test_get_all_strategies_returns_list(self):
        """get_all_strategies returns all 17."""
        all_s = get_all_strategies()
        assert len(all_s) == 17
        assert all(isinstance(s, StrategyDefinition) for s in all_s)


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY VALIDITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestStrategyValidity:
    """Every strategy must have required fields populated."""

    @pytest.fixture
    def all_strategies(self):
        return get_all_strategies()

    def test_all_have_family(self, all_strategies):
        """Every strategy must have a valid StrategyFamily."""
        for s in all_strategies:
            assert isinstance(s.family, StrategyFamily), (
                f"{s.strategy_id} has invalid family: {s.family}"
            )

    def test_all_have_hypothesis(self, all_strategies):
        """Every strategy must have a non-empty hypothesis."""
        for s in all_strategies:
            assert s.hypothesis, f"{s.strategy_id} missing hypothesis"
            assert len(s.hypothesis) > 10, f"{s.strategy_id} hypothesis too short"

    def test_all_have_description(self, all_strategies):
        """Every strategy must have a non-empty description."""
        for s in all_strategies:
            assert s.description, f"{s.strategy_id} missing description"

    def test_all_have_name(self, all_strategies):
        """Every strategy must have a non-empty name."""
        for s in all_strategies:
            assert s.name, f"{s.strategy_id} missing name"

    def test_all_have_conditions(self, all_strategies):
        """Every strategy must have at least one required condition."""
        for s in all_strategies:
            assert len(s.required_conditions) > 0, (
                f"{s.strategy_id} has no required_conditions"
            )

    def test_all_have_invalid_conditions(self, all_strategies):
        """Every strategy must have at least one invalid condition."""
        for s in all_strategies:
            assert len(s.invalid_conditions) > 0, (
                f"{s.strategy_id} has no invalid_conditions"
            )

    def test_all_have_market_phases(self, all_strategies):
        """Every strategy must define at least one valid market phase."""
        for s in all_strategies:
            assert len(s.valid_market_phases) > 0, (
                f"{s.strategy_id} has no valid_market_phases"
            )

    def test_all_have_valid_regimes(self, all_strategies):
        """Every strategy must define at least one valid regime."""
        for s in all_strategies:
            assert len(s.valid_regimes) > 0, (
                f"{s.strategy_id} has no valid_regimes"
            )

    def test_all_are_hypothesis_status(self, all_strategies):
        """All strategies must currently be HYPOTHESIS."""
        for s in all_strategies:
            assert s.evidence_status == EvidenceStatus.HYPOTHESIS, (
                f"{s.strategy_id} should be HYPOTHESIS, is {s.evidence_status.value}"
            )

    def test_definitions_are_frozen(self):
        """Strategy definitions must be immutable."""
        s = get_strategy("range_reversal_v1")
        with pytest.raises(Exception):
            s.name = "Modified"  # type: ignore


# ═══════════════════════════════════════════════════════════════════════════════
# FAMILY COVERAGE
# ═══════════════════════════════════════════════════════════════════════════════


class TestFamilyCoverage:
    """Every family must have strategies defined."""

    def test_all_families_have_strategies(self):
        """Every StrategyFamily enum member must have at least one strategy."""
        for family in StrategyFamily:
            strategies = get_strategies_by_family(family)
            assert len(strategies) > 0, (
                f"Family {family.value} has no strategies"
            )

    def test_reversal_has_3(self):
        assert len(get_strategies_by_family(StrategyFamily.REVERSAL)) == 3

    def test_momentum_has_3(self):
        assert len(get_strategies_by_family(StrategyFamily.MOMENTUM)) == 3

    def test_continuation_has_3(self):
        assert len(get_strategies_by_family(StrategyFamily.CONTINUATION)) == 3

    def test_breakout_has_3(self):
        assert len(get_strategies_by_family(StrategyFamily.BREAKOUT)) == 3

    def test_mean_reversion_has_3(self):
        assert len(get_strategies_by_family(StrategyFamily.MEAN_REVERSION)) == 3

    def test_structure_has_2(self):
        assert len(get_strategies_by_family(StrategyFamily.STRUCTURE)) == 2

    def test_all_families_have_definitions(self):
        """Every family must have a FamilyDefinition."""
        for family in StrategyFamily:
            fam_def = get_family_definition(family)
            assert fam_def is not None, f"No definition for {family.value}"
            assert isinstance(fam_def, FamilyDefinition)
            assert fam_def.hypothesis

    def test_family_definitions_count(self):
        """Must have exactly 6 family definitions."""
        assert len(get_all_family_definitions()) == 6


# ═══════════════════════════════════════════════════════════════════════════════
# QUERY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestQueries:
    """Tests for query helpers."""

    def test_get_strategies_for_impulse_phase(self):
        """IMPULSE phase should return momentum + structure strategies."""
        results = get_strategies_for_phase("IMPULSE")
        ids = [s.strategy_id for s in results]
        assert "momentum_expansion_v1" in ids
        assert "impulse_followthrough_v1" in ids
        # Reversal strategies should NOT appear for IMPULSE
        assert "range_reversal_v1" not in ids

    def test_get_strategies_for_pullback_phase(self):
        """PULLBACK should return continuation strategies."""
        results = get_strategies_for_phase("PULLBACK")
        ids = [s.strategy_id for s in results]
        assert "trend_pullback_v1" in ids
        assert "moving_average_pullback_v1" in ids
        assert "structure_retest_v1" in ids

    def test_get_strategies_for_reversal_phase(self):
        """REVERSAL should return reversal + mean_reversion strategies."""
        results = get_strategies_for_phase("REVERSAL")
        ids = [s.strategy_id for s in results]
        assert "range_reversal_v1" in ids
        assert "liquidity_sweep_reversal_v1" in ids

    def test_get_strategies_for_trending_regime(self):
        """TRENDING regime should include momentum + continuation."""
        results = get_strategies_for_regime("TRENDING")
        ids = [s.strategy_id for s in results]
        assert "momentum_expansion_v1" in ids
        assert "trend_pullback_v1" in ids
        # Pure range strategies should not appear
        assert "range_mean_reversion_v1" not in ids

    def test_get_strategies_for_ranging_regime(self):
        """RANGING regime should include reversal + breakout + mean_reversion."""
        results = get_strategies_for_regime("RANGING")
        ids = [s.strategy_id for s in results]
        assert "range_reversal_v1" in ids
        assert "range_breakout_v1" in ids
        assert "range_mean_reversion_v1" in ids

    def test_context_query_phase_and_regime(self):
        """Combined query narrows results."""
        results = get_strategies_for_context(phase="IMPULSE", regime="TRENDING")
        ids = [s.strategy_id for s in results]
        assert "momentum_expansion_v1" in ids
        # Range reversal is not valid for IMPULSE + TRENDING
        assert "range_reversal_v1" not in ids

    def test_context_query_empty_returns_all(self):
        """Empty filters return all strategies."""
        results = get_strategies_for_context(phase="", regime="")
        assert len(results) == 17

    def test_context_query_no_match(self):
        """Non-existent phase returns empty."""
        results = get_strategies_for_context(phase="NONEXISTENT")
        assert results == []


# ═══════════════════════════════════════════════════════════════════════════════
# DIAGNOSTICS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestDiagnostics:
    """Tests for diagnostic reporting."""

    def test_library_report_contains_families(self):
        """Report must mention all family names."""
        report = strategy_library_report()
        for family in StrategyFamily:
            assert family.value in report

    def test_library_report_contains_strategies(self):
        """Report must mention strategy IDs."""
        report = strategy_library_report()
        assert "range_reversal_v1" in report
        assert "momentum_expansion_v1" in report
        assert "bos_continuation_v1" in report

    def test_library_report_contains_counts(self):
        """Report must show strategy counts."""
        report = strategy_library_report()
        assert "17" in report or "Total Strategies: 17" in report

    def test_context_query_report_shows_eligible(self):
        """Context report must show eligible strategies."""
        report = context_query_report(phase="IMPULSE", regime="TRENDING")
        assert "momentum_expansion_v1" in report
        assert "Eligible" in report

    def test_context_query_report_no_match(self):
        """Context report with no match shows appropriate message."""
        report = context_query_report(phase="NONEXISTENT")
        assert "No strategies match" in report

    def test_library_summary_structure(self):
        """get_library_summary returns expected dict structure."""
        summary = get_library_summary()
        assert summary["total_strategies"] == 17
        assert summary["total_families"] == 6
        assert "REVERSAL" in summary["family_distribution"]
        assert summary["family_distribution"]["REVERSAL"] == 3
        assert summary["family_distribution"]["STRUCTURE"] == 2
        assert "HYPOTHESIS" in summary["evidence_distribution"]
        assert "range_reversal_v1" in summary["strategies"]


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY TESTS — NO EXECUTION IMPORTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoExecutionImports:
    """Verify the library has no execution or decision pipeline dependencies."""

    def test_no_pipeline_imports_in_models(self):
        """models.py must not import from execution/risk/pipeline."""
        import inspect
        import core.strategies.library.models as m
        source = inspect.getsource(m)
        forbidden = [
            "from core.pipeline",
            "from execution",
            "from risk",
            "import MetaTrader5",
            "from core.runtime",
        ]
        for f in forbidden:
            assert f not in source, f"models.py contains forbidden import: {f}"

    def test_no_pipeline_imports_in_registry(self):
        """registry.py must not import from execution/risk/pipeline."""
        import inspect
        import core.strategies.library.registry as r
        source = inspect.getsource(r)
        forbidden = [
            "from core.pipeline",
            "from execution",
            "from risk",
            "import MetaTrader5",
            "from core.runtime",
        ]
        for f in forbidden:
            assert f not in source, f"registry.py contains forbidden import: {f}"

    def test_no_pipeline_imports_in_diagnostics(self):
        """diagnostics.py must not import from execution/risk/pipeline."""
        import inspect
        import core.strategies.library.diagnostics as d
        source = inspect.getsource(d)
        forbidden = [
            "from core.pipeline",
            "from execution",
            "from risk",
            "import MetaTrader5",
            "from core.runtime",
        ]
        for f in forbidden:
            assert f not in source, f"diagnostics.py contains forbidden import: {f}"

    def test_registry_is_immutable_dict(self):
        """STRATEGY_LIBRARY dict should not allow casual modification of values."""
        s = STRATEGY_LIBRARY.get("range_reversal_v1")
        assert s is not None
        with pytest.raises(Exception):
            s.strategy_id = "hacked"  # type: ignore


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL PROPERTY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestModelProperties:
    """Tests for computed properties on models."""

    def test_family_name_property(self):
        s = get_strategy("range_reversal_v1")
        assert s.family_name == "REVERSAL"

    def test_is_hypothesis_property(self):
        s = get_strategy("range_reversal_v1")
        assert s.is_hypothesis is True
        assert s.is_active is False

    def test_phase_count_property(self):
        s = get_strategy("range_reversal_v1")
        assert s.phase_count == 2  # CONSOLIDATION, REVERSAL

    def test_condition_count_property(self):
        s = get_strategy("range_reversal_v1")
        assert s.condition_count == 4
