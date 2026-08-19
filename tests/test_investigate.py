"""
Tests for ResearchOrchestrator.investigate() — the unified entry point.

Tests: template selection, validation, execution, placebo, challenge, conclude,
report, catalogue, audit, governance, failure handling, idempotency.
"""
import sys
import json
from unittest.mock import patch, MagicMock
from collections import defaultdict

import pytest

sys.path.insert(0, ".")

from research_engine.lifecycle.orchestrator import ResearchOrchestrator, InvestigationResult
from research_engine.lifecycle.hypothesis import (
    Hypothesis, HypothesisCategory, HypothesisStatus, ConclusionType,
)
from research_engine.lifecycle.experiment_protocol import (
    ExperimentDefinition, ExperimentResult, ExperimentType,
    PopulationSpec, SimulationSpec, ValidationSpec,
)
from research_engine.lifecycle.experiment_catalogue import ExperimentLifecycle


@pytest.fixture
def isolated_orch(tmp_path, monkeypatch):
    """Orchestrator with all persistence isolated to tmp_path."""
    monkeypatch.setattr("research_engine.lifecycle.registry._REGISTRY_DIR", tmp_path)
    monkeypatch.setattr("research_engine.lifecycle.registry._REGISTRY_FILE", tmp_path / "reg.json")
    monkeypatch.setattr("research_engine.lifecycle.registry._AUDIT_LOG", tmp_path / "audit.jsonl")
    monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_DIR", tmp_path)
    monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_FILE", tmp_path / "cat.json")
    monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._AUDIT_LOG", tmp_path / "audit.jsonl")
    orch = ResearchOrchestrator()
    orch._knowledge_path = tmp_path / "km.json"
    return orch


def _mock_population():
    """Synthetic population for testing."""
    return [
        {"symbol": "EURUSD", "cid": f"COR-{i}", "dir": "SELL", "entry": 1.085,
         "sl": 1.086, "tp": 1.083, "time": 1784739300 + i * 300,
         "pattern": "THREE_BLACK_CROWS", "score": 0.6}
        for i in range(50)
    ]


def _mock_candles():
    """Synthetic candles that produce deterministic outcomes."""
    return [{"high": 1.086, "low": 1.083, "close": 1.084}] * 60


def _patch_data():
    """Context manager patches for data loading."""
    return [
        patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
              return_value=_mock_population()),
        patch("research_engine.lifecycle.experiment_templates._load_candles",
              return_value=_mock_candles()),
    ]


def _make_hypothesis(orch):
    return orch.detect_and_register(
        title="Test Investigation Hypothesis",
        description="Testing the investigate() method",
        claim="Inversion produces positive R",
        null_hypothesis="Inversion has no effect",
        category=HypothesisCategory.DIRECTION_INVERSION,
        multiple_testing_count=10,
    )


def _make_definition(hypothesis_id):
    return ExperimentDefinition(
        hypothesis_id=hypothesis_id,
        experiment_type=ExperimentType.DIRECTION_INVERSION,
        title="Test Direction Inversion",
        population=PopulationSpec(pattern_filter=["THREE_BLACK_CROWS"], min_sample_size=30),
        simulation=SimulationSpec(direction="INVERT", stop_multiplier=1.0, tp_multiplier=3.0, max_bars=60),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SUCCESSFUL INVESTIGATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestSuccessfulInvestigation:
    def test_complete_lifecycle_one_call(self, isolated_orch):
        """investigate() executes the full lifecycle in a single call."""
        h = _make_hypothesis(isolated_orch)
        defn = _make_definition(h.hypothesis_id)

        # Mock placebo populations (simple: one pattern that also inverts positive)
        placebo_pops = {"OTHER_PATTERN": _mock_population()[:30]}

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_population()):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                result = isolated_orch.investigate(
                    hypothesis=h,
                    experiment_type=ExperimentType.DIRECTION_INVERSION,
                    experiment_definition=defn,
                    placebo_populations=placebo_pops,
                )

        assert result.status == "complete"
        assert result.hypothesis_id == h.hypothesis_id
        assert result.experiment_id == defn.experiment_id
        assert result.conclusion in ("VALIDATED", "REJECTED", "INCONCLUSIVE")
        assert result.classification in ("GREEN", "AMBER", "RED")
        assert result.governance_status in ("BLOCKED", "AWAITING_HUMAN_APPROVAL")
        assert result.next_recommended_action != ""

    def test_experiment_result_populated(self, isolated_orch):
        """investigate() produces a populated ExperimentResult."""
        h = _make_hypothesis(isolated_orch)
        defn = _make_definition(h.hypothesis_id)

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_population()):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                result = isolated_orch.investigate(h, ExperimentType.DIRECTION_INVERSION, defn)

        assert result.experiment_result is not None
        assert result.experiment_result.n > 0
        assert result.experiment_result.dataset_fingerprint

    def test_catalogue_populated(self, isolated_orch):
        """investigate() auto-populates the ExperimentCatalogue."""
        h = _make_hypothesis(isolated_orch)
        defn = _make_definition(h.hypothesis_id)

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_population()):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                isolated_orch.investigate(h, ExperimentType.DIRECTION_INVERSION, defn)

        cat_rec = isolated_orch.catalogue.get(defn.experiment_id)
        assert cat_rec is not None
        assert cat_rec.status == ExperimentLifecycle.COMPLETED

    def test_report_generated(self, isolated_orch):
        """investigate() generates a report."""
        h = _make_hypothesis(isolated_orch)
        defn = _make_definition(h.hypothesis_id)

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_population()):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                result = isolated_orch.investigate(h, ExperimentType.DIRECTION_INVERSION, defn)

        assert result.report_text != ""
        assert "Research Report" in result.report_text
        assert h.hypothesis_id in result.report_text

    def test_knowledge_map_updated(self, isolated_orch, tmp_path):
        """investigate() updates the knowledge map."""
        h = _make_hypothesis(isolated_orch)
        defn = _make_definition(h.hypothesis_id)

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_population()):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                isolated_orch.investigate(h, ExperimentType.DIRECTION_INVERSION, defn)

        km = json.loads(isolated_orch._knowledge_path.read_text(encoding="utf-8"))
        assert h.hypothesis_id in km.get("lifecycle_findings", {})

    def test_validation_methods_recorded(self, isolated_orch):
        """investigate() records which validation methods were applied."""
        h = _make_hypothesis(isolated_orch)
        defn = _make_definition(h.hypothesis_id)

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_population()):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                result = isolated_orch.investigate(h, ExperimentType.DIRECTION_INVERSION, defn)

        assert "bootstrap_ci" in result.validation_performed
        assert "permutation_test_paired" in result.validation_performed


# ═══════════════════════════════════════════════════════════════════════════════
# PLACEBO HANDLING
# ═══════════════════════════════════════════════════════════════════════════════


class TestPlaceboHandling:
    def test_placebo_executed_when_required(self, isolated_orch):
        """DIRECTION_INVERSION template requires placebo — verify it runs."""
        h = _make_hypothesis(isolated_orch)
        defn = _make_definition(h.hypothesis_id)
        placebo_pops = {"PAT_A": _mock_population()[:25], "PAT_B": _mock_population()[:25]}

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_population()):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                result = isolated_orch.investigate(h, ExperimentType.DIRECTION_INVERSION, defn,
                                                   placebo_populations=placebo_pops)

        assert result.placebo_performed

    def test_failed_placebo_affects_conclusion(self, isolated_orch):
        """If all placebos are positive, conclusion should reflect this."""
        h = _make_hypothesis(isolated_orch)
        defn = _make_definition(h.hypothesis_id)
        # All placebo patterns positive = placebo fails
        placebo_pops = {f"PAT_{i}": _mock_population()[:25] for i in range(5)}

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_population()):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                result = isolated_orch.investigate(h, ExperimentType.DIRECTION_INVERSION, defn,
                                                   placebo_populations=placebo_pops)

        # If placebo fails, conclusion should be REJECTED (or at least not GREEN)
        assert result.classification != "GREEN" or not result.placebo_outcome or result.placebo_outcome.placebo_passes


# ═══════════════════════════════════════════════════════════════════════════════
# FAILURE HANDLING
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailureHandling:
    def test_unsupported_type_rejected(self, isolated_orch):
        """Unsupported experiment type fails cleanly."""
        h = _make_hypothesis(isolated_orch)
        # PLACEBO_CONTROL doesn't have an execute_fn
        defn = ExperimentDefinition(
            hypothesis_id=h.hypothesis_id,
            experiment_type=ExperimentType.PLACEBO_CONTROL,
            title="Unsupported",
            population=PopulationSpec(pattern_filter=["X"]),
        )
        result = isolated_orch.investigate(h, ExperimentType.PLACEBO_CONTROL, defn)
        assert result.status == "failed"
        assert "Unsupported" in result.failure_reason or "execute_fn" in result.failure_reason

    def test_invalid_definition_rejected(self, isolated_orch):
        """Invalid definition (missing pattern_filter) fails before execution."""
        h = _make_hypothesis(isolated_orch)
        defn = ExperimentDefinition(
            hypothesis_id=h.hypothesis_id,
            experiment_type=ExperimentType.DIRECTION_INVERSION,
            title="Invalid",
            population=PopulationSpec(pattern_filter=[]),  # Empty = invalid
        )
        result = isolated_orch.investigate(h, ExperimentType.DIRECTION_INVERSION, defn)
        assert result.status == "failed"
        assert "pattern_filter" in result.failure_reason

    def test_execution_failure_recorded(self, isolated_orch):
        """Exception during execution produces failed investigation."""
        h = _make_hypothesis(isolated_orch)
        defn = _make_definition(h.hypothesis_id)

        # Return empty population so experiment produces 0 results → status=failed
        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=[]):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=[]):
                result = isolated_orch.investigate(h, ExperimentType.DIRECTION_INVERSION, defn)

        assert result.status == "failed"


# ═══════════════════════════════════════════════════════════════════════════════
# GOVERNANCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestGovernance:
    def test_no_automatic_promotion(self, isolated_orch):
        """investigate() NEVER promotes — governance always blocked."""
        h = _make_hypothesis(isolated_orch)
        defn = _make_definition(h.hypothesis_id)

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_population()):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                result = isolated_orch.investigate(h, ExperimentType.DIRECTION_INVERSION, defn)

        # Regardless of conclusion, should never be PROMOTED
        assert h.status != HypothesisStatus.PROMOTED
        assert not h.human_approval_granted


# ═══════════════════════════════════════════════════════════════════════════════
# AUDIT TRAIL
# ═══════════════════════════════════════════════════════════════════════════════


class TestAuditTrail:
    def test_audit_events_recorded(self, isolated_orch, tmp_path):
        """investigate() generates audit trail events."""
        h = _make_hypothesis(isolated_orch)
        defn = _make_definition(h.hypothesis_id)

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_population()):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                isolated_orch.investigate(h, ExperimentType.DIRECTION_INVERSION, defn)

        audit_file = tmp_path / "audit.jsonl"
        assert audit_file.exists()
        events = [json.loads(l)["event"] for l in audit_file.read_text(encoding="utf-8").strip().splitlines()]
        assert "INVESTIGATION_STARTED" in events
        assert "INVESTIGATION_COMPLETED" in events


# ═══════════════════════════════════════════════════════════════════════════════
# RESTART PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestRestartPersistence:
    def test_investigation_state_survives_restart(self, isolated_orch, tmp_path, monkeypatch):
        """Hypothesis and experiment persist across registry reload."""
        h = _make_hypothesis(isolated_orch)
        defn = _make_definition(h.hypothesis_id)

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_population()):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                isolated_orch.investigate(h, ExperimentType.DIRECTION_INVERSION, defn)

        # Simulate restart
        from research_engine.lifecycle.registry import InvestigationRegistry
        monkeypatch.setattr("research_engine.lifecycle.registry._REGISTRY_FILE", tmp_path / "reg.json")
        fresh_reg = InvestigationRegistry()
        reloaded = fresh_reg.get(h.hypothesis_id)
        assert reloaded is not None
        assert reloaded.status == HypothesisStatus.CONCLUDED
        assert reloaded.conclusion_type is not None
