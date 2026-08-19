"""
Tests for Investigation Contracts — category-driven experiment semantics.

Verifies that each TriggerCategory produces the correct:
- experiment type
- population filter
- simulation spec (direction, stop, TP)
- conditioning variable
- supported/unsupported status
"""
import sys

import pytest

sys.path.insert(0, ".")

from research_engine.lifecycle.investigation_contracts import (
    INVESTIGATION_CONTRACTS,
    InvestigationContract,
    build_experiment_from_trigger,
    get_contract,
)
from research_engine.lifecycle.finding_trigger import (
    FindingTrigger,
    TriggerCategory,
)
from research_engine.lifecycle.experiment_protocol import ExperimentType, SimulationSpec


def _make_trigger(category, **kw):
    defaults = {
        "title": "Test",
        "observation": "test obs",
        "suggested_claim": "claim",
        "suggested_null": "null",
        "sample_size": 50,
    }
    defaults.update(kw)
    return FindingTrigger(category=category, **defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# CONTRACT MAPPING CORRECTNESS
# ═══════════════════════════════════════════════════════════════════════════════


class TestContractMapping:
    def test_poor_pattern_uses_invert(self):
        c = get_contract(TriggerCategory.POOR_PATTERN_PERFORMANCE)
        assert c.direction == "INVERT"
        assert c.experiment_type == ExperimentType.DIRECTION_INVERSION
        assert c.requires_pattern_filter is True

    def test_direction_asymmetry_uses_invert(self):
        c = get_contract(TriggerCategory.DIRECTION_ASYMMETRY)
        assert c.direction == "INVERT"
        assert c.experiment_type == ExperimentType.DIRECTION_INVERSION
        assert c.requires_pattern_filter is True

    def test_strong_pattern_uses_same(self):
        c = get_contract(TriggerCategory.STRONG_PATTERN_PERFORMANCE)
        assert c.direction == "SAME"
        assert c.requires_pattern_filter is True

    def test_regime_uses_same_full_population(self):
        c = get_contract(TriggerCategory.REGIME_ANOMALY)
        assert c.direction == "SAME"
        assert c.population_scope == "FULL"
        assert c.requires_pattern_filter is False
        assert c.conditioning_variable == "regime"

    def test_symbol_uses_same_symbol_filter(self):
        c = get_contract(TriggerCategory.SYMBOL_ANOMALY)
        assert c.direction == "SAME"
        assert c.requires_symbol_filter is True
        assert c.conditioning_variable == "symbol"

    def test_score_uses_same_full_population(self):
        c = get_contract(TriggerCategory.SCORE_MONOTONICITY)
        assert c.direction == "SAME"
        assert c.population_scope == "FULL"
        assert c.requires_pattern_filter is False
        assert c.conditioning_variable == "score"

    def test_geometry_uses_same_counterfactual(self):
        c = get_contract(TriggerCategory.GEOMETRY_ANOMALY)
        assert c.direction == "SAME"
        assert c.experiment_type == ExperimentType.COUNTERFACTUAL_GEOMETRY
        assert c.conditioning_variable == "risk_distance"

    def test_temporal_uses_same_full(self):
        c = get_contract(TriggerCategory.TEMPORAL_INSTABILITY)
        assert c.direction == "SAME"
        assert c.population_scope == "FULL"
        assert c.conditioning_variable == "time"

    def test_execution_is_unsupported(self):
        c = get_contract(TriggerCategory.EXECUTION_ANOMALY)
        assert c.supported is False
        assert c.unsupported_reason != ""

    def test_knowledge_contradiction_is_unsupported(self):
        c = get_contract(TriggerCategory.KNOWLEDGE_CONTRADICTION)
        assert c.supported is False


# ═══════════════════════════════════════════════════════════════════════════════
# EXPERIMENT DEFINITION BUILDING
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuildExperiment:
    def test_poor_pattern_builds_correctly(self):
        trigger = _make_trigger(TriggerCategory.POOR_PATTERN_PERFORMANCE,
                                suggested_patterns=["THREE_BLACK_CROWS"])
        defn, err = build_experiment_from_trigger(trigger, hypothesis_id="H-1")
        assert defn is not None
        assert err == ""
        assert defn.population.pattern_filter == ["THREE_BLACK_CROWS"]
        assert defn.simulation.direction == "INVERT"
        assert defn.simulation.tp_multiplier == 3.0
        assert defn.experiment_type == ExperimentType.DIRECTION_INVERSION

    def test_direction_asymmetry_builds_correctly(self):
        trigger = _make_trigger(TriggerCategory.DIRECTION_ASYMMETRY,
                                suggested_patterns=["TWEEZER_TOP"])
        defn, err = build_experiment_from_trigger(trigger, hypothesis_id="H-2")
        assert defn is not None
        assert defn.simulation.direction == "INVERT"
        assert defn.population.pattern_filter == ["TWEEZER_TOP"]

    def test_regime_builds_full_population(self):
        trigger = _make_trigger(TriggerCategory.REGIME_ANOMALY,
                                evidence={"regime": "TRENDING"})
        defn, err = build_experiment_from_trigger(trigger, hypothesis_id="H-3")
        assert defn is not None
        assert defn.population.pattern_filter == []  # Full population
        assert defn.simulation.direction == "SAME"
        assert defn.experiment_type == ExperimentType.CONDITIONING_ANALYSIS

    def test_symbol_builds_with_symbol_filter(self):
        trigger = _make_trigger(TriggerCategory.SYMBOL_ANOMALY,
                                evidence={"symbol": "NAS100"})
        defn, err = build_experiment_from_trigger(trigger, hypothesis_id="H-4")
        assert defn is not None
        assert defn.population.symbol_filter == ["NAS100"]
        assert defn.population.pattern_filter == []
        assert defn.simulation.direction == "SAME"

    def test_symbol_without_evidence_fails(self):
        trigger = _make_trigger(TriggerCategory.SYMBOL_ANOMALY, evidence={})
        defn, err = build_experiment_from_trigger(trigger, hypothesis_id="H-5")
        assert defn is None
        assert "symbol" in err.lower()

    def test_score_builds_full_population(self):
        trigger = _make_trigger(TriggerCategory.SCORE_MONOTONICITY)
        defn, err = build_experiment_from_trigger(trigger, hypothesis_id="H-6")
        assert defn is not None
        assert defn.population.pattern_filter == []
        assert defn.simulation.direction == "SAME"

    def test_geometry_builds_counterfactual(self):
        trigger = _make_trigger(TriggerCategory.GEOMETRY_ANOMALY)
        defn, err = build_experiment_from_trigger(trigger, hypothesis_id="H-7")
        assert defn is not None
        assert defn.experiment_type == ExperimentType.COUNTERFACTUAL_GEOMETRY
        assert defn.simulation.direction == "SAME"
        assert defn.simulation.stop_multiplier == 1.5

    def test_temporal_builds_full_population(self):
        trigger = _make_trigger(TriggerCategory.TEMPORAL_INSTABILITY)
        defn, err = build_experiment_from_trigger(trigger, hypothesis_id="H-8")
        assert defn is not None
        assert defn.population.pattern_filter == []
        assert defn.simulation.direction == "SAME"

    def test_execution_anomaly_fails_unsupported(self):
        trigger = _make_trigger(TriggerCategory.EXECUTION_ANOMALY)
        defn, err = build_experiment_from_trigger(trigger, hypothesis_id="H-9")
        assert defn is None
        assert "not supported" in err.lower() or "unsupported" in err.lower()

    def test_knowledge_contradiction_fails_unsupported(self):
        trigger = _make_trigger(TriggerCategory.KNOWLEDGE_CONTRADICTION)
        defn, err = build_experiment_from_trigger(trigger, hypothesis_id="H-10")
        assert defn is None

    def test_no_accidental_inversion_for_same_direction(self):
        """Verify SAME direction experiments don't accidentally invert."""
        for cat in [TriggerCategory.REGIME_ANOMALY, TriggerCategory.SYMBOL_ANOMALY,
                    TriggerCategory.SCORE_MONOTONICITY, TriggerCategory.TEMPORAL_INSTABILITY]:
            trigger = _make_trigger(cat, evidence={"symbol": "TEST", "regime": "X"})
            defn, _ = build_experiment_from_trigger(trigger, hypothesis_id="H-X")
            if defn:
                assert defn.simulation.direction == "SAME", \
                    f"{cat.value} produced direction={defn.simulation.direction}, expected SAME"


# ═══════════════════════════════════════════════════════════════════════════════
# TEMPLATE VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestTemplateValidation:
    def test_empty_pattern_filter_valid_for_conditioning(self):
        """CONDITIONING_ANALYSIS accepts empty pattern_filter (population-wide)."""
        from research_engine.lifecycle.experiment_templates import ExperimentTemplateRegistry
        from research_engine.lifecycle.experiment_protocol import (
            ExperimentDefinition, PopulationSpec, SimulationSpec,
        )
        reg = ExperimentTemplateRegistry()
        defn = ExperimentDefinition(
            experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
            population=PopulationSpec(pattern_filter=[], min_sample_size=30),
            simulation=SimulationSpec(direction="SAME"),
        )
        valid, reason = reg.validate(defn)
        assert valid, f"Empty pattern_filter should be valid for CONDITIONING_ANALYSIS: {reason}"

    def test_empty_pattern_filter_invalid_for_inversion(self):
        """DIRECTION_INVERSION still requires pattern_filter."""
        from research_engine.lifecycle.experiment_templates import ExperimentTemplateRegistry
        from research_engine.lifecycle.experiment_protocol import (
            ExperimentDefinition, PopulationSpec, SimulationSpec,
        )
        reg = ExperimentTemplateRegistry()
        defn = ExperimentDefinition(
            experiment_type=ExperimentType.DIRECTION_INVERSION,
            population=PopulationSpec(pattern_filter=[], min_sample_size=30),
            simulation=SimulationSpec(direction="INVERT"),
        )
        valid, reason = reg.validate(defn)
        assert not valid
        assert "pattern_filter" in reason.lower() or "DIRECTION_INVERSION" in reason

    def test_symbol_filter_sufficient_for_conditioning(self):
        """symbol_filter alone is sufficient for non-pattern experiments."""
        from research_engine.lifecycle.experiment_templates import ExperimentTemplateRegistry
        from research_engine.lifecycle.experiment_protocol import (
            ExperimentDefinition, PopulationSpec, SimulationSpec,
        )
        reg = ExperimentTemplateRegistry()
        defn = ExperimentDefinition(
            experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
            population=PopulationSpec(pattern_filter=[], symbol_filter=["NAS100"], min_sample_size=30),
            simulation=SimulationSpec(direction="SAME"),
        )
        valid, reason = reg.validate(defn)
        assert valid, reason
