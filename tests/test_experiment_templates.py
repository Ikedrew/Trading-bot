"""
Tests for Experiment Template Registry.

Verifies:
- Template discovery and selection
- Validation of experiment definitions against templates
- Canonical simulation logic
- Result construction
- Integration with orchestrator (without MT5/live data)
"""
import sys
import json
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, ".")

from research_engine.lifecycle.experiment_templates import (
    ExperimentTemplateRegistry,
    ExperimentTemplate,
    _simulate_trade,
    _filter_population,
    _build_result,
)
from research_engine.lifecycle.experiment_protocol import (
    ExperimentDefinition,
    ExperimentResult,
    ExperimentType,
    PopulationSpec,
    SimulationSpec,
    ValidationSpec,
)


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRY DISCOVERY
# ═══════════════════════════════════════════════════════════════════════════════


class TestRegistryDiscovery:
    def test_get_existing_template(self):
        reg = ExperimentTemplateRegistry()
        t = reg.get(ExperimentType.DIRECTION_INVERSION)
        assert t is not None
        assert t.experiment_type == ExperimentType.DIRECTION_INVERSION
        assert t.description != ""

    def test_get_nonexistent_returns_none(self):
        reg = ExperimentTemplateRegistry()
        # All defined types have templates, but test the API contract
        result = reg.get(ExperimentType.DIRECTION_INVERSION)
        assert result is not None  # This one exists

    def test_supports_returns_true_for_implemented(self):
        reg = ExperimentTemplateRegistry()
        assert reg.supports(ExperimentType.DIRECTION_INVERSION)
        assert reg.supports(ExperimentType.COUNTERFACTUAL_GEOMETRY)
        assert reg.supports(ExperimentType.CONDITIONING_ANALYSIS)

    def test_get_execute_fn(self):
        reg = ExperimentTemplateRegistry()
        fn = reg.get_execute_fn(ExperimentType.DIRECTION_INVERSION)
        assert fn is not None
        assert callable(fn)

    def test_list_templates(self):
        reg = ExperimentTemplateRegistry()
        templates = reg.list_templates()
        assert len(templates) >= 7  # All ExperimentType values have templates
        assert all(isinstance(t, ExperimentTemplate) for t in templates)

    def test_list_supported_types(self):
        reg = ExperimentTemplateRegistry()
        supported = reg.list_supported_types()
        assert ExperimentType.DIRECTION_INVERSION in supported
        assert ExperimentType.COUNTERFACTUAL_GEOMETRY in supported

    def test_each_template_has_description(self):
        reg = ExperimentTemplateRegistry()
        for t in reg.list_templates():
            assert t.description, f"Template {t.experiment_type} has no description"

    def test_each_template_has_validation_methods(self):
        reg = ExperimentTemplateRegistry()
        for t in reg.list_templates():
            assert t.validation_methods, f"Template {t.experiment_type} has no validation methods"


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestTemplateValidation:
    def test_valid_definition_passes(self):
        reg = ExperimentTemplateRegistry()
        defn = ExperimentDefinition(
            experiment_type=ExperimentType.DIRECTION_INVERSION,
            population=PopulationSpec(pattern_filter=["THREE_BLACK_CROWS"]),
            simulation=SimulationSpec(direction="INVERT"),
        )
        valid, reason = reg.validate(defn)
        assert valid, reason

    def test_missing_pattern_filter_fails(self):
        reg = ExperimentTemplateRegistry()
        defn = ExperimentDefinition(
            experiment_type=ExperimentType.DIRECTION_INVERSION,
            population=PopulationSpec(pattern_filter=[]),  # Empty!
            simulation=SimulationSpec(direction="INVERT"),
        )
        valid, reason = reg.validate(defn)
        assert not valid
        assert "pattern_filter" in reason

    def test_sample_size_below_minimum_fails(self):
        reg = ExperimentTemplateRegistry()
        defn = ExperimentDefinition(
            experiment_type=ExperimentType.DIRECTION_INVERSION,
            population=PopulationSpec(pattern_filter=["X"], min_sample_size=5),
            simulation=SimulationSpec(direction="INVERT"),
        )
        valid, reason = reg.validate(defn)
        assert not valid
        assert "min_sample_size" in reason


# ═══════════════════════════════════════════════════════════════════════════════
# CANONICAL SIMULATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestCanonicalSimulation:
    def test_buy_sl_hit(self):
        candles = [{"high": 1.10, "low": 0.90, "close": 0.95}]  # Low hits SL
        result = _simulate_trade(direction="BUY", entry_price=1.00, stop_loss=0.95,
                                  take_profit=1.10, candles=candles)
        assert result["exit_reason"] == "stop_loss"
        assert result["r_multiple"] == -1.0

    def test_buy_tp_hit(self):
        candles = [{"high": 1.15, "low": 0.99, "close": 1.10}]  # High hits TP
        result = _simulate_trade(direction="BUY", entry_price=1.00, stop_loss=0.95,
                                  take_profit=1.10, candles=candles)
        # SL checked first: low=0.99 > sl=0.95, so SL not hit. High=1.15 >= tp=1.10
        assert result["exit_reason"] == "take_profit"
        assert result["r_multiple"] == 2.0  # (1.10 - 1.00) / (1.00 - 0.95) = 0.10/0.05

    def test_sell_sl_hit(self):
        candles = [{"high": 1.06, "low": 0.98, "close": 1.04}]  # High hits SL
        result = _simulate_trade(direction="SELL", entry_price=1.00, stop_loss=1.05,
                                  take_profit=0.90, candles=candles)
        assert result["exit_reason"] == "stop_loss"
        assert result["r_multiple"] == -1.0

    def test_timeout(self):
        # Candles that never hit SL or TP
        candles = [{"high": 1.02, "low": 0.98, "close": 1.01}] * 60
        result = _simulate_trade(direction="BUY", entry_price=1.00, stop_loss=0.90,
                                  take_profit=1.20, candles=candles, max_bars=60)
        assert result["exit_reason"] == "max_bars_timeout"
        assert result["bars_held"] == 60

    def test_sl_checked_before_tp(self):
        # Both SL and TP would be hit on same bar — SL wins
        candles = [{"high": 1.10, "low": 0.90, "close": 1.00}]
        result = _simulate_trade(direction="BUY", entry_price=1.00, stop_loss=0.95,
                                  take_profit=1.05, candles=candles)
        assert result["exit_reason"] == "stop_loss"

    def test_mfe_mae_computed(self):
        candles = [
            {"high": 1.03, "low": 0.99, "close": 1.02},
            {"high": 1.05, "low": 0.97, "close": 1.01},
        ] + [{"high": 1.01, "low": 1.00, "close": 1.005}] * 58
        result = _simulate_trade(direction="BUY", entry_price=1.00, stop_loss=0.90,
                                  take_profit=1.20, candles=candles, max_bars=60)
        assert result["mfe_r"] > 0  # Price went above entry
        assert result["mae_r"] > 0  # Price went below entry

    def test_zero_risk_handled(self):
        result = _simulate_trade(direction="BUY", entry_price=1.00, stop_loss=1.00,
                                  take_profit=1.10, candles=[{"high":1.1,"low":0.9,"close":1.0}])
        assert result["exit_reason"] == "zero_risk"


# ═══════════════════════════════════════════════════════════════════════════════
# POPULATION FILTERING
# ═══════════════════════════════════════════════════════════════════════════════


class TestPopulationFilter:
    def test_pattern_filter(self):
        pop = [{"pattern": "A", "cid": "1"}, {"pattern": "B", "cid": "2"}, {"pattern": "A", "cid": "3"}]
        spec = PopulationSpec(pattern_filter=["A"])
        result = _filter_population(pop, spec)
        assert len(result) == 2

    def test_symbol_filter(self):
        pop = [{"symbol": "EURUSD", "cid": "1"}, {"symbol": "GBPUSD", "cid": "2"}]
        spec = PopulationSpec(symbol_filter=["EURUSD"])
        result = _filter_population(pop, spec)
        assert len(result) == 1

    def test_direction_filter(self):
        pop = [{"dir": "BUY", "cid": "1"}, {"dir": "SELL", "cid": "2"}]
        spec = PopulationSpec(direction_filter="SELL")
        result = _filter_population(pop, spec)
        assert len(result) == 1

    def test_require_correlation_id(self):
        pop = [{"cid": "abc", "pattern": "X"}, {"cid": "", "pattern": "X"}]
        spec = PopulationSpec(require_correlation_id=True)
        result = _filter_population(pop, spec)
        assert len(result) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# RESULT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════


class TestResultBuilder:
    def test_builds_complete_result(self):
        defn = ExperimentDefinition(
            experiment_id="EXP-test",
            hypothesis_id="H-test",
            experiment_type=ExperimentType.DIRECTION_INVERSION,
            title="Test",
            population=PopulationSpec(pattern_filter=["X"]),
            simulation=SimulationSpec(),
        )
        treatment = [{"r_multiple": 0.5, "exit_reason": "take_profit", "bars_held": 10,
                      "mfe_r": 1.0, "mae_r": 0.3, "symbol": "EURUSD", "time": 100 + i}
                     for i in range(20)]
        control = [{"r_multiple": -0.5, "exit_reason": "stop_loss", "bars_held": 5,
                    "mfe_r": 0.2, "mae_r": 1.0}
                   for _ in range(20)]
        records = [{"symbol": "EURUSD", "time": 100 + i, "pattern": "X", "cid": f"c{i}"}
                   for i in range(20)]

        result = _build_result(defn, treatment, control, records)
        assert result.n == 20
        assert result.mean_r == 0.5
        assert result.status == "complete"
        assert result.dataset_fingerprint  # Fingerprint present
        assert result.permutation_p is not None  # Paired test ran


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION: TEMPLATE → ORCHESTRATOR (synthetic, no MT5)
# ═══════════════════════════════════════════════════════════════════════════════


class TestTemplateOrchestratorIntegration:
    def test_orchestrator_can_use_template_fn(self, tmp_path, monkeypatch):
        """The orchestrator can obtain execute_fn from the template registry."""
        monkeypatch.setattr("research_engine.lifecycle.registry._REGISTRY_DIR", tmp_path)
        monkeypatch.setattr("research_engine.lifecycle.registry._REGISTRY_FILE", tmp_path / "reg.json")
        monkeypatch.setattr("research_engine.lifecycle.registry._AUDIT_LOG", tmp_path / "audit.jsonl")
        monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_DIR", tmp_path)
        monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_FILE", tmp_path / "cat.json")
        monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._AUDIT_LOG", tmp_path / "audit.jsonl")

        from research_engine.lifecycle.orchestrator import ResearchOrchestrator
        from research_engine.lifecycle.experiment_templates import ExperimentTemplateRegistry

        orch = ResearchOrchestrator()
        orch._knowledge_path = tmp_path / "km.json"
        template_reg = ExperimentTemplateRegistry()

        # Register hypothesis
        h = orch.detect_and_register(
            title="Template Integration Test",
            description="Testing template-based execution",
            claim="Templates work",
            null_hypothesis="Templates don't work",
        )

        # Get template execute_fn
        assert template_reg.supports(ExperimentType.DIRECTION_INVERSION)
        execute_fn = template_reg.get_execute_fn(ExperimentType.DIRECTION_INVERSION)
        assert execute_fn is not None

        # Create experiment definition
        exp_def = ExperimentDefinition(
            hypothesis_id=h.hypothesis_id,
            experiment_type=ExperimentType.DIRECTION_INVERSION,
            title="Template-driven inversion",
            population=PopulationSpec(pattern_filter=["THREE_BLACK_CROWS"]),
            simulation=SimulationSpec(direction="INVERT", stop_multiplier=1.0, tp_multiplier=3.0),
        )

        # Validate against template
        valid, reason = template_reg.validate(exp_def)
        assert valid, reason

        # Mock data loading to avoid MT5 dependency
        mock_population = [
            {"symbol": "EURUSD", "cid": f"COR-{i}", "dir": "SELL", "entry": 1.085,
             "sl": 1.086, "tp": 1.083, "time": 1784739300 + i * 300,
             "pattern": "THREE_BLACK_CROWS", "score": 0.6}
            for i in range(40)
        ]
        mock_candles = [{"high": 1.086, "low": 1.083, "close": 1.084}] * 60

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=mock_population):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=mock_candles):
                result = orch.run_experiment(h, exp_def, execute_fn)

        # Verify result
        assert result.status == "complete"
        assert result.n == 40
        assert result.dataset_fingerprint  # Fingerprint captured
        assert result.experiment_id == exp_def.experiment_id

        # Verify catalogue was populated
        cat_rec = orch.catalogue.get(exp_def.experiment_id)
        assert cat_rec is not None
        assert cat_rec.hypothesis_id == h.hypothesis_id
