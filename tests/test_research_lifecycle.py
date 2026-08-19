"""
Tests for the Research Lifecycle system.

Verifies:
- Hypothesis state machine transitions
- Registry CRUD and persistence
- Validation harness computations
- Placebo controller logic
- Governance gate enforcement
- Orchestrator end-to-end flow
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

from research_engine.lifecycle.hypothesis import (
    ConclusionType,
    Hypothesis,
    HypothesisCategory,
    HypothesisStatus,
)
from research_engine.lifecycle.experiment_protocol import (
    ExperimentDefinition,
    ExperimentResult,
    ExperimentType,
    PopulationSpec,
    SimulationSpec,
    ValidationSpec,
)
from research_engine.lifecycle.validation_harness import (
    bootstrap_ci,
    outlier_influence,
    permutation_test,
    symbol_robustness,
    temporal_stability,
)
from research_engine.lifecycle.placebo_controller import run_placebo_test, PlaceboResult
from research_engine.lifecycle.governance_gate import GovernanceGate


# ═══════════════════════════════════════════════════════════════════════════════
# HYPOTHESIS STATE MACHINE
# ═══════════════════════════════════════════════════════════════════════════════


class TestHypothesisStateMachine:
    def test_initial_state(self):
        h = Hypothesis(title="Test")
        assert h.status == HypothesisStatus.DETECTED

    def test_valid_transition_detected_to_registered(self):
        h = Hypothesis(title="Test")
        assert h.transition(HypothesisStatus.REGISTERED, reason="test")
        assert h.status == HypothesisStatus.REGISTERED
        assert len(h.transitions) == 1

    def test_valid_full_lifecycle(self):
        h = Hypothesis(title="Test")
        assert h.transition(HypothesisStatus.REGISTERED, reason="r1")
        assert h.transition(HypothesisStatus.TESTING, reason="r2")
        assert h.transition(HypothesisStatus.CHALLENGED, reason="r3")
        assert h.status == HypothesisStatus.CHALLENGED

    def test_invalid_skip_state(self):
        h = Hypothesis(title="Test")
        # Cannot skip from DETECTED to TESTING
        assert not h.transition(HypothesisStatus.TESTING, reason="skip")
        assert h.status == HypothesisStatus.DETECTED

    def test_conclude_validated(self):
        h = Hypothesis(title="Test")
        h.transition(HypothesisStatus.REGISTERED, reason="r")
        h.transition(HypothesisStatus.TESTING, reason="r")
        assert h.conclude(ConclusionType.VALIDATED, reason="evidence strong")
        assert h.status == HypothesisStatus.CONCLUDED
        assert h.conclusion_type == ConclusionType.VALIDATED

    def test_conclude_rejected(self):
        h = Hypothesis(title="Test")
        h.transition(HypothesisStatus.REGISTERED, reason="r")
        h.transition(HypothesisStatus.TESTING, reason="r")
        assert h.conclude(ConclusionType.REJECTED, reason="placebo fails")
        assert h.conclusion_type == ConclusionType.REJECTED

    def test_promotion_requires_human_approval(self):
        h = Hypothesis(title="Test")
        h.transition(HypothesisStatus.REGISTERED, reason="r")
        h.transition(HypothesisStatus.TESTING, reason="r")
        h.conclude(ConclusionType.VALIDATED, reason="strong")
        # Cannot promote without approval
        assert not h.transition(HypothesisStatus.PROMOTED, reason="auto")
        # Grant approval
        h.grant_human_approval(notes="approved by trader")
        assert h.transition(HypothesisStatus.PROMOTED, reason="human approved")
        assert h.status == HypothesisStatus.PROMOTED

    def test_cannot_promote_rejected(self):
        h = Hypothesis(title="Test")
        h.transition(HypothesisStatus.REGISTERED, reason="r")
        h.transition(HypothesisStatus.TESTING, reason="r")
        h.conclude(ConclusionType.REJECTED, reason="failed")
        # Cannot grant approval on rejected hypothesis
        assert not h.grant_human_approval()

    def test_serialisation_roundtrip(self):
        h = Hypothesis(title="Test", claim="X > Y", category=HypothesisCategory.PATTERN_SIGNAL)
        h.transition(HypothesisStatus.REGISTERED, reason="test")
        h.add_experiment("EXP-001", "primary")
        data = h.to_dict()
        h2 = Hypothesis.from_dict(data)
        assert h2.title == "Test"
        assert h2.claim == "X > Y"
        assert h2.status == HypothesisStatus.REGISTERED
        assert len(h2.experiments) == 1
        assert len(h2.transitions) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION HARNESS
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidationHarness:
    def test_bootstrap_ci_basic(self):
        vals = [1.0] * 50 + [-1.0] * 50
        lo, hi = bootstrap_ci(vals, seed=42)
        assert lo is not None
        assert lo < 0.1
        assert hi > -0.1

    def test_bootstrap_ci_insufficient(self):
        lo, hi = bootstrap_ci([1.0, 2.0])
        assert lo is None

    def test_permutation_test_identical(self):
        # Same distributions should give high p-value
        a = [0.1] * 50
        b = [0.1] * 50
        p = permutation_test(a, b, seed=42)
        assert p > 0.3  # Not significant

    def test_permutation_test_different(self):
        # Clearly different distributions
        a = [1.0] * 50
        b = [-1.0] * 50
        p = permutation_test(a, b, seed=42)
        assert p < 0.01  # Highly significant

    def test_outlier_influence(self):
        vals = [0.1] * 90 + [10.0] * 10
        result = outlier_influence(vals)
        assert result["survives_top10"]  # Still positive after removing top 10
        assert result["top10_contribution_pct"] > 50  # Top 10 contribute majority

    def test_symbol_robustness(self):
        records = [{"symbol": "A", "r_multiple": 0.5}] * 20 + \
                  [{"symbol": "B", "r_multiple": -0.3}] * 20
        result = symbol_robustness(records)
        assert result["symbols_positive"] == 1
        assert result["symbols_total"] == 2

    def test_temporal_stability(self):
        records = [{"time": i, "r_multiple": 0.1 if i % 2 == 0 else -0.1} for i in range(100)]
        result = temporal_stability(records, n_buckets=5)
        assert result["periods_total"] == 5


# ═══════════════════════════════════════════════════════════════════════════════
# PLACEBO CONTROLLER
# ═══════════════════════════════════════════════════════════════════════════════


class TestPlaceboController:
    def test_placebo_passes_when_few_positive(self):
        # Only 1 out of 5 controls positive → placebo passes
        def experiment_fn(pop, pat):
            if pat == "A":
                return [0.5] * 20  # Positive
            return [-0.3] * 20  # Negative

        pops = {chr(65 + i): [{}] * 30 for i in range(5)}
        result = run_placebo_test(
            hypothesis_id="H-test",
            experiment_fn=experiment_fn,
            control_populations=pops,
        )
        assert result.placebo_passes
        assert result.positive_fraction <= 0.5

    def test_placebo_fails_when_most_positive(self):
        # 4 out of 5 controls positive → placebo fails
        def experiment_fn(pop, pat):
            return [0.5] * 20  # All positive

        pops = {chr(65 + i): [{}] * 30 for i in range(5)}
        result = run_placebo_test(
            hypothesis_id="H-test",
            experiment_fn=experiment_fn,
            control_populations=pops,
        )
        assert not result.placebo_passes
        assert result.positive_fraction > 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# GOVERNANCE GATE
# ═══════════════════════════════════════════════════════════════════════════════


class TestGovernanceGate:
    def test_cannot_promote_non_concluded(self):
        gate = GovernanceGate()
        h = Hypothesis(title="Test")
        h.transition(HypothesisStatus.REGISTERED, reason="r")
        eligible, reason = gate.can_promote(h)
        assert not eligible

    def test_cannot_promote_rejected(self):
        gate = GovernanceGate()
        h = Hypothesis(title="Test")
        h.transition(HypothesisStatus.REGISTERED, reason="r")
        h.transition(HypothesisStatus.TESTING, reason="r")
        h.conclude(ConclusionType.REJECTED, reason="failed")
        eligible, reason = gate.can_promote(h)
        assert not eligible

    def test_can_promote_validated_with_experiments(self):
        gate = GovernanceGate()
        h = Hypothesis(title="Test")
        h.transition(HypothesisStatus.REGISTERED, reason="r")
        h.transition(HypothesisStatus.TESTING, reason="r")
        exp = h.add_experiment("EXP-1", "oos_validation")
        exp.status = "complete"
        h.conclude(ConclusionType.VALIDATED, reason="strong evidence")
        eligible, reason = gate.can_promote(h)
        assert eligible

    def test_approve_grants_promotion(self):
        gate = GovernanceGate()
        h = Hypothesis(title="Test")
        h.transition(HypothesisStatus.REGISTERED, reason="r")
        h.transition(HypothesisStatus.TESTING, reason="r")
        exp = h.add_experiment("EXP-1", "oos_validation")
        exp.status = "complete"
        h.conclude(ConclusionType.VALIDATED, reason="strong")
        
        with patch.object(gate, '_log_event'):
            result = gate.approve(h, actor="trader", reason="looks good")
        assert result
        assert h.human_approval_granted


# ═══════════════════════════════════════════════════════════════════════════════
# EXPERIMENT PROTOCOL
# ═══════════════════════════════════════════════════════════════════════════════


class TestExperimentProtocol:
    def test_experiment_definition_creation(self):
        exp = ExperimentDefinition(
            hypothesis_id="H-test",
            experiment_type=ExperimentType.DIRECTION_INVERSION,
            title="Invert TBC",
            population=PopulationSpec(pattern_filter=["THREE_BLACK_CROWS"]),
            simulation=SimulationSpec(direction="INVERT", stop_multiplier=1.0, tp_multiplier=3.0),
            validation=ValidationSpec(bonferroni_tests=24),
        )
        assert exp.experiment_id.startswith("EXP-")
        assert exp.population.pattern_filter == ["THREE_BLACK_CROWS"]
        assert exp.validation.bonferroni_tests == 24

    def test_experiment_result_passes_validation(self):
        r = ExperimentResult(
            mean_r=0.15,
            oos_mean_r=0.10,
            oos_n=50,
            placebo_passes=True,
            symbols_positive=5,
            symbols_total=10,
            periods_positive=3,
            periods_total=5,
        )
        assert r.passes_validation

    def test_experiment_result_fails_validation_placebo(self):
        r = ExperimentResult(
            mean_r=0.15,
            oos_mean_r=0.10,
            oos_n=50,
            placebo_passes=False,  # Placebo fails
            symbols_positive=5,
            symbols_total=10,
            periods_positive=3,
            periods_total=5,
        )
        assert not r.passes_validation



# ═══════════════════════════════════════════════════════════════════════════════
# PERMUTATION TEST — PAIRED (ISSUE 1)
# ═══════════════════════════════════════════════════════════════════════════════


class TestPermutationTestPaired:
    def test_observed_not_equal_null(self):
        """Observed treatment must differ from control by construction."""
        from research_engine.lifecycle.validation_harness import permutation_test_paired
        treatment = [1.0, 0.5, 0.3, 0.8, 0.2]
        control = [-0.5, -0.3, -0.1, -0.8, -0.2]
        p = permutation_test_paired(treatment, control, seed=42)
        # Strong positive treatment should give low p
        assert p < 0.1

    def test_permutation_changes_assignment(self):
        """Permutation must actually change direction labels."""
        from research_engine.lifecycle.validation_harness import permutation_test_paired
        # If we pass identical groups, it should raise ValueError
        with pytest.raises(ValueError, match="all paired differences are zero"):
            permutation_test_paired([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], seed=42)

    def test_identical_inputs_raises(self):
        """Same treatment and control is an invalid null model."""
        from research_engine.lifecycle.validation_harness import permutation_test_paired
        with pytest.raises(ValueError):
            permutation_test_paired([0.5]*10, [0.5]*10, seed=42)

    def test_p_value_in_range(self):
        """P-value must be in [0, 1]."""
        from research_engine.lifecycle.validation_harness import permutation_test_paired
        treatment = [0.5, -0.3, 0.8, -0.1, 0.2, 0.6, -0.4, 0.9, 0.1, -0.2]
        control = [-0.1, 0.2, -0.3, 0.1, -0.5, 0.3, -0.2, 0.4, -0.1, 0.0]
        p = permutation_test_paired(treatment, control, seed=42)
        assert 0.0 <= p <= 1.0

    def test_deterministic_with_seed(self):
        """Same seed produces same p-value."""
        from research_engine.lifecycle.validation_harness import permutation_test_paired
        treatment = [0.5, -0.3, 0.8, -0.1, 0.2]
        control = [-0.1, 0.2, -0.3, 0.1, -0.5]
        p1 = permutation_test_paired(treatment, control, seed=123)
        p2 = permutation_test_paired(treatment, control, seed=123)
        assert p1 == p2

    def test_invalid_null_warning_on_empty(self):
        """Empty inputs raise ValueError."""
        from research_engine.lifecycle.validation_harness import permutation_test_paired
        with pytest.raises(ValueError, match="non-empty"):
            permutation_test_paired([], [], seed=42)

    def test_unequal_length_raises(self):
        """Unequal length groups raise ValueError."""
        from research_engine.lifecycle.validation_harness import permutation_test_paired
        with pytest.raises(ValueError, match="equal length"):
            permutation_test_paired([1.0, 2.0], [3.0], seed=42)

    def test_clearly_different_gives_low_p(self):
        """Treatment clearly better than control → low p-value."""
        from research_engine.lifecycle.validation_harness import permutation_test_paired
        treatment = [2.0] * 30
        control = [-1.0] * 30
        p = permutation_test_paired(treatment, control, n_perms=2000, seed=42)
        assert p < 0.01

    def test_no_difference_gives_high_p(self):
        """Treatment ≈ control → high p-value."""
        from research_engine.lifecycle.validation_harness import permutation_test_paired
        import random
        rng = random.Random(42)
        treatment = [rng.gauss(0, 1) for _ in range(50)]
        control = [rng.gauss(0, 1) for _ in range(50)]
        # Make them have similar means (reshuffle so diffs are balanced)
        p = permutation_test_paired(treatment, control, n_perms=2000, seed=42)
        assert p > 0.1


# ═══════════════════════════════════════════════════════════════════════════════
# KNOWLEDGE MAP COMPATIBILITY (ISSUE 2)
# ═══════════════════════════════════════════════════════════════════════════════


class TestKnowledgeMapCompatibility:
    def _make_orchestrator(self, tmp_path):
        """Create orchestrator with tmp knowledge map path."""
        from research_engine.lifecycle.orchestrator import ResearchOrchestrator
        orch = ResearchOrchestrator()
        orch._knowledge_path = tmp_path / "research_knowledge.json"
        return orch

    def _make_hypothesis_and_result(self):
        h = Hypothesis(title="Test", hypothesis_id="H-test-001")
        h.transition(HypothesisStatus.REGISTERED, reason="r")
        h.transition(HypothesisStatus.TESTING, reason="r")
        h.conclude(ConclusionType.REJECTED, reason="placebo fails")
        r = ExperimentResult(
            experiment_id="EXP-001", hypothesis_id="H-test-001",
            n=100, mean_r=-0.05, classification="RED", win_rate=0.3,
            oos_mean_r=-0.02, placebo_passes=False,
        )
        return h, r

    def test_empty_knowledge_map(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        h, r = self._make_hypothesis_and_result()
        result = orch.update_knowledge_map(h, r)
        assert result is True
        data = json.loads(orch._knowledge_path.read_text(encoding="utf-8"))
        assert "lifecycle_findings" in data
        assert "H-test-001" in data["lifecycle_findings"]

    def test_existing_v2_format_preserved(self, tmp_path):
        """Existing research_knowledge_v2 format entries remain untouched."""
        orch = self._make_orchestrator(tmp_path)
        # Write existing v2 format
        existing = {
            "schema_version": "research_knowledge_v2",
            "findings": {"Q1": {"finding": "test", "status": "VALIDATED"}},
            "confirmed_facts": ["fact1"],
        }
        orch._knowledge_path.write_text(json.dumps(existing), encoding="utf-8")

        h, r = self._make_hypothesis_and_result()
        result = orch.update_knowledge_map(h, r)
        assert result is True

        data = json.loads(orch._knowledge_path.read_text(encoding="utf-8"))
        # Existing entries preserved
        assert data["findings"]["Q1"]["finding"] == "test"
        assert data["confirmed_facts"] == ["fact1"]
        # New lifecycle entry added in separate namespace
        assert "H-test-001" in data["lifecycle_findings"]

    def test_malformed_json_handled(self, tmp_path):
        """Malformed JSON creates backup and continues."""
        orch = self._make_orchestrator(tmp_path)
        orch._knowledge_path.write_text("{{not valid json", encoding="utf-8")

        h, r = self._make_hypothesis_and_result()
        result = orch.update_knowledge_map(h, r)
        assert result is True
        # Backup created
        assert orch._knowledge_path.with_suffix(".json.bak").exists()

    def test_multiple_lifecycle_findings(self, tmp_path):
        orch = self._make_orchestrator(tmp_path)
        h1, r1 = self._make_hypothesis_and_result()
        h2 = Hypothesis(title="Test2", hypothesis_id="H-test-002")
        h2.transition(HypothesisStatus.REGISTERED, reason="r")
        h2.transition(HypothesisStatus.TESTING, reason="r")
        h2.conclude(ConclusionType.VALIDATED, reason="strong")
        r2 = ExperimentResult(experiment_id="EXP-002", hypothesis_id="H-test-002",
                              n=200, mean_r=0.15, classification="GREEN", win_rate=0.4,
                              oos_mean_r=0.10, placebo_passes=True)

        orch.update_knowledge_map(h1, r1)
        orch.update_knowledge_map(h2, r2)

        data = json.loads(orch._knowledge_path.read_text(encoding="utf-8"))
        assert len(data["lifecycle_findings"]) == 2
        assert data["lifecycle_findings"]["H-test-001"]["classification"] == "RED"
        assert data["lifecycle_findings"]["H-test-002"]["classification"] == "GREEN"

    def test_repeated_insertion_updates(self, tmp_path):
        """Same hypothesis written twice updates rather than duplicates."""
        orch = self._make_orchestrator(tmp_path)
        h, r = self._make_hypothesis_and_result()
        orch.update_knowledge_map(h, r)
        orch.update_knowledge_map(h, r)

        data = json.loads(orch._knowledge_path.read_text(encoding="utf-8"))
        assert len(data["lifecycle_findings"]) == 1  # No duplicate


# ═══════════════════════════════════════════════════════════════════════════════
# GOVERNANCE GATE HARDENING (ISSUE 6)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGovernanceHardened:
    def test_cannot_bypass_to_promoted(self):
        """No programmatic path to PROMOTED without human approval."""
        h = Hypothesis(title="Test")
        h.transition(HypothesisStatus.REGISTERED, reason="r")
        h.transition(HypothesisStatus.TESTING, reason="r")
        h.conclude(ConclusionType.VALIDATED, reason="strong")
        # Try to promote without approval
        assert not h.transition(HypothesisStatus.PROMOTED, reason="auto")
        assert h.status == HypothesisStatus.CONCLUDED

    def test_cannot_grant_approval_on_inconclusive(self):
        """Cannot approve a hypothesis that isn't VALIDATED."""
        h = Hypothesis(title="Test")
        h.transition(HypothesisStatus.REGISTERED, reason="r")
        h.transition(HypothesisStatus.TESTING, reason="r")
        h.conclude(ConclusionType.INCONCLUSIVE, reason="mixed")
        assert not h.grant_human_approval()

    def test_cannot_grant_approval_before_conclusion(self):
        """Cannot approve during TESTING."""
        h = Hypothesis(title="Test")
        h.transition(HypothesisStatus.REGISTERED, reason="r")
        h.transition(HypothesisStatus.TESTING, reason="r")
        assert not h.grant_human_approval()

    def test_gate_cannot_approve_non_validated(self):
        """GovernanceGate.approve fails for rejected hypothesis."""
        from research_engine.lifecycle.governance_gate import GovernanceGate
        from unittest.mock import patch
        gate = GovernanceGate()
        h = Hypothesis(title="Test")
        h.transition(HypothesisStatus.REGISTERED, reason="r")
        h.transition(HypothesisStatus.TESTING, reason="r")
        h.conclude(ConclusionType.REJECTED, reason="failed")
        with patch.object(gate, '_log_event'):
            result = gate.approve(h, actor="hacker", reason="bypass attempt")
        assert not result
        assert not h.human_approval_granted



# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR ↔ CATALOGUE INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestOrchestratorCatalogueIntegration:
    """Verify that run_experiment automatically populates the ExperimentCatalogue."""

    def test_experiment_registered_in_catalogue(self, tmp_path, monkeypatch):
        """Running an experiment through orchestrator creates a catalogue record."""
        # Isolate persistence to tmp_path
        monkeypatch.setattr("research_engine.lifecycle.registry._REGISTRY_DIR", tmp_path)
        monkeypatch.setattr("research_engine.lifecycle.registry._REGISTRY_FILE", tmp_path / "registry.json")
        monkeypatch.setattr("research_engine.lifecycle.registry._AUDIT_LOG", tmp_path / "audit.jsonl")
        monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_DIR", tmp_path)
        monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_FILE", tmp_path / "exp_reg.json")
        monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._AUDIT_LOG", tmp_path / "audit.jsonl")

        from research_engine.lifecycle.orchestrator import ResearchOrchestrator
        from research_engine.lifecycle.experiment_protocol import (
            ExperimentDefinition, ExperimentResult, ExperimentType,
            PopulationSpec, SimulationSpec, ValidationSpec,
        )

        orch = ResearchOrchestrator()
        orch._knowledge_path = tmp_path / "km.json"

        # Register hypothesis
        h = orch.detect_and_register(
            title="Test Hypothesis",
            description="Testing catalogue integration",
            claim="X is better than Y",
            null_hypothesis="X = Y",
        )

        # Define experiment
        exp_def = ExperimentDefinition(
            hypothesis_id=h.hypothesis_id,
            experiment_type=ExperimentType.DIRECTION_INVERSION,
            title="Test Inversion Experiment",
            population=PopulationSpec(pattern_filter=["TEST_PATTERN"]),
            simulation=SimulationSpec(direction="INVERT", stop_multiplier=1.0, tp_multiplier=3.0),
        )

        # Execute (synthetic — returns fixed result)
        def mock_execute(exp_def):
            return ExperimentResult(
                experiment_id=exp_def.experiment_id,
                hypothesis_id=h.hypothesis_id,
                n=50, mean_r=0.15, total_r=7.5, win_rate=0.4,
                ci_lower=0.02, ci_upper=0.28,
                classification="AMBER",
                dataset_fingerprint={"content_hash": "test_hash_abc", "observation_count": 50},
            )

        result = orch.run_experiment(h, exp_def, mock_execute)

        # Verify catalogue was populated
        cat_rec = orch.catalogue.get(exp_def.experiment_id)
        assert cat_rec is not None, "Experiment not found in catalogue"
        assert cat_rec.hypothesis_id == h.hypothesis_id
        assert cat_rec.experiment_type == "DIRECTION_INVERSION"
        assert cat_rec.title == "Test Inversion Experiment"

        # Verify lifecycle reached COMPLETED
        from research_engine.lifecycle.experiment_catalogue import ExperimentLifecycle
        assert cat_rec.status == ExperimentLifecycle.COMPLETED

        # Verify result summary stored
        assert cat_rec.result_summary.get("n") == 50
        assert cat_rec.result_summary.get("mean_r") == 0.15

        # Verify fingerprint propagated
        assert cat_rec.dataset_fingerprint.get("content_hash") == "test_hash_abc"
        assert cat_rec.observation_count == 50

    def test_failed_experiment_recorded_in_catalogue(self, tmp_path, monkeypatch):
        """A failing experiment is recorded as FAILED in catalogue."""
        monkeypatch.setattr("research_engine.lifecycle.registry._REGISTRY_DIR", tmp_path)
        monkeypatch.setattr("research_engine.lifecycle.registry._REGISTRY_FILE", tmp_path / "registry.json")
        monkeypatch.setattr("research_engine.lifecycle.registry._AUDIT_LOG", tmp_path / "audit.jsonl")
        monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_DIR", tmp_path)
        monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_FILE", tmp_path / "exp_reg.json")
        monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._AUDIT_LOG", tmp_path / "audit.jsonl")

        from research_engine.lifecycle.orchestrator import ResearchOrchestrator
        from research_engine.lifecycle.experiment_protocol import (
            ExperimentDefinition, ExperimentType, PopulationSpec, SimulationSpec,
        )

        orch = ResearchOrchestrator()
        orch._knowledge_path = tmp_path / "km.json"

        h = orch.detect_and_register(
            title="Fail Test", description="d", claim="c", null_hypothesis="n",
        )

        exp_def = ExperimentDefinition(
            hypothesis_id=h.hypothesis_id,
            experiment_type=ExperimentType.ROBUSTNESS_CHECK,
            title="Failing experiment",
            population=PopulationSpec(pattern_filter=["X"]),
            simulation=SimulationSpec(),
        )

        def failing_execute(exp_def):
            raise RuntimeError("MT5 disconnected")

        result = orch.run_experiment(h, exp_def, failing_execute)
        assert result.status == "failed"

        # Verify catalogue records the failure
        from research_engine.lifecycle.experiment_catalogue import ExperimentLifecycle
        cat_rec = orch.catalogue.get(exp_def.experiment_id)
        assert cat_rec is not None
        assert cat_rec.status == ExperimentLifecycle.FAILED

    def test_report_path_propagates_to_catalogue(self, tmp_path, monkeypatch):
        """generate_report updates report_path on catalogue records."""
        monkeypatch.setattr("research_engine.lifecycle.registry._REGISTRY_DIR", tmp_path)
        monkeypatch.setattr("research_engine.lifecycle.registry._REGISTRY_FILE", tmp_path / "registry.json")
        monkeypatch.setattr("research_engine.lifecycle.registry._AUDIT_LOG", tmp_path / "audit.jsonl")
        monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_DIR", tmp_path)
        monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_FILE", tmp_path / "exp_reg.json")
        monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._AUDIT_LOG", tmp_path / "audit.jsonl")

        from research_engine.lifecycle.orchestrator import ResearchOrchestrator
        from research_engine.lifecycle.experiment_protocol import (
            ExperimentDefinition, ExperimentResult, ExperimentType,
            PopulationSpec, SimulationSpec,
        )

        orch = ResearchOrchestrator()
        orch._knowledge_path = tmp_path / "km.json"

        h = orch.detect_and_register(
            title="Report Test", description="d", claim="c", null_hypothesis="n",
        )
        exp_def = ExperimentDefinition(
            hypothesis_id=h.hypothesis_id,
            experiment_type=ExperimentType.DIRECTION_INVERSION,
            title="Report Test Exp",
            population=PopulationSpec(pattern_filter=["X"]),
            simulation=SimulationSpec(direction="INVERT"),
        )

        def mock_exec(e):
            return ExperimentResult(experiment_id=e.experiment_id,
                                    hypothesis_id=h.hypothesis_id, n=10, mean_r=0.1)

        result = orch.run_experiment(h, exp_def, mock_exec)

        # Generate report (needs hypothesis to be concluded)
        h.transition(HypothesisStatus.CHALLENGED, reason="test")
        h.conclude(ConclusionType.INCONCLUSIVE, reason="test")
        report = orch.generate_report(h, result)

        # Verify report_path was set on catalogue
        cat_rec = orch.catalogue.get(exp_def.experiment_id)
        assert cat_rec.report_path != ""
        assert h.hypothesis_id in cat_rec.report_path
