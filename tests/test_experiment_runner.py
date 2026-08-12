"""
Tests for Item 11 Second Pass: Candidate Experiment Runner.

Covers:
- Experiment construction
- Baseline evaluation
- Candidate evaluation (population filter)
- Metrics calculation
- Comparison and delta
- Insufficient sample handling
- Code change blocking
- Counterfactual provenance
- ValidationResult integration
- PromotionGate integration
- Governance boundary
- Persistence
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.v10.proposals.model import (
    ChangeProposal,
    Candidate,
    ValidationStatus,
    PromotionStatus,
)
from research_engine.v10.proposals.generator import ProposalFactory
from research_engine.v10.proposals.experiment import (
    ExperimentRunner,
    ExperimentResult,
    CandidateType,
    calculate_metrics,
)
from research_engine.v10.proposals.promotion import PromotionGate


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


def make_population():
    """Simulate a realistic execution population."""
    return [
        {"trade_id": f"t{i}", "r_multiple": 2.0, "regime": "TRENDING", "exit_reason": "tp"}
        for i in range(30)
    ] + [
        {"trade_id": f"t{i+30}", "r_multiple": -1.0, "regime": "TRANSITIONAL", "exit_reason": "sl"}
        for i in range(50)
    ] + [
        {"trade_id": f"t{i+80}", "r_multiple": 1.5, "regime": "RANGING", "exit_reason": "tp"}
        for i in range(14)
    ]


def make_proposal():
    return ChangeProposal(
        proposal_id="prop_test_001",
        system_area="STRATEGY",
        problem_statement="Transitional regime trades lose money",
        hypothesis="Filtering out transitional trades improves expectancy",
    )


def make_candidate():
    return Candidate(
        candidate_id="cand_test_001",
        proposal_id="prop_test_001",
        description="Exclude TRANSITIONAL regime trades",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# METRICS CALCULATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestMetricsCalculation:

    def test_basic_metrics(self):
        pop = [{"r_multiple": 2.0}, {"r_multiple": -1.0}, {"r_multiple": 1.0}]
        m = calculate_metrics(pop)
        assert m["sample_size"] == 3
        assert m["wins"] == 2
        assert m["losses"] == 1
        assert m["mean_r"] is not None

    def test_empty_population(self):
        m = calculate_metrics([])
        assert m["sample_size"] == 0
        assert m["mean_r"] is None

    def test_all_metrics_present(self):
        pop = make_population()
        m = calculate_metrics(pop)
        assert "sample_size" in m
        assert "wins" in m
        assert "losses" in m
        assert "win_rate" in m
        assert "mean_r" in m
        assert "median_r" in m
        assert "total_r" in m
        assert "profit_factor" in m
        assert "max_drawdown_r" in m


# ═══════════════════════════════════════════════════════════════════════════════
# EXPERIMENT RUNNER
# ═══════════════════════════════════════════════════════════════════════════════


class TestExperimentRunner:

    def test_filter_experiment_completes(self):
        runner = ExperimentRunner()
        result = runner.run_filter_experiment(
            proposal=make_proposal(),
            candidate=make_candidate(),
            population=make_population(),
            candidate_filter=lambda r: r.get("regime") != "TRANSITIONAL",
        )
        assert result.status == "COMPLETED"
        assert result.baseline_population_size == 94
        assert result.candidate_population_size == 44  # 30 trending + 14 ranging

    def test_baseline_uses_full_population(self):
        runner = ExperimentRunner()
        result = runner.run_filter_experiment(
            proposal=make_proposal(),
            candidate=make_candidate(),
            population=make_population(),
            candidate_filter=lambda r: r.get("regime") != "TRANSITIONAL",
        )
        assert result.baseline_metrics["sample_size"] == 94

    def test_candidate_uses_filtered_population(self):
        runner = ExperimentRunner()
        result = runner.run_filter_experiment(
            proposal=make_proposal(),
            candidate=make_candidate(),
            population=make_population(),
            candidate_filter=lambda r: r.get("regime") != "TRANSITIONAL",
        )
        # Only trending (30) + ranging (14) = 44
        assert result.candidate_metrics["sample_size"] == 44

    def test_candidate_improves_expectancy(self):
        runner = ExperimentRunner()
        result = runner.run_filter_experiment(
            proposal=make_proposal(),
            candidate=make_candidate(),
            population=make_population(),
            candidate_filter=lambda r: r.get("regime") != "TRANSITIONAL",
        )
        # Removing losers should improve mean_r
        assert result.candidate_metrics["mean_r"] > result.baseline_metrics["mean_r"]
        assert result.delta_metrics["mean_r"] > 0

    def test_empty_population_blocked(self):
        runner = ExperimentRunner()
        result = runner.run_filter_experiment(
            proposal=make_proposal(),
            candidate=make_candidate(),
            population=[],
            candidate_filter=lambda r: True,
        )
        assert result.status == "BLOCKED"

    def test_code_change_blocked(self):
        runner = ExperimentRunner()
        result = runner.run_code_change_experiment(
            proposal=make_proposal(),
            candidate=make_candidate(),
        )
        assert result.status == "BLOCKED"
        assert "CODE_CHANGE" in result.blocked_reason

    def test_provenance_is_counterfactual(self):
        runner = ExperimentRunner()
        result = runner.run_filter_experiment(
            proposal=make_proposal(),
            candidate=make_candidate(),
            population=make_population(),
            candidate_filter=lambda r: True,
        )
        assert result.provenance == "COUNTERFACTUAL"

    def test_preserves_versions(self):
        runner = ExperimentRunner()
        result = runner.run_filter_experiment(
            proposal=make_proposal(),
            candidate=make_candidate(),
            population=make_population(),
            candidate_filter=lambda r: True,
            universe_versions={"EXECUTION": "abc123"},
            population_versions={"all_trades": "def456"},
        )
        assert result.universe_versions == {"EXECUTION": "abc123"}
        assert result.population_versions == {"all_trades": "def456"}


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidationIntegration:

    def test_experiment_to_validation_validated(self):
        runner = ExperimentRunner()
        experiment = runner.run_filter_experiment(
            proposal=make_proposal(),
            candidate=make_candidate(),
            population=make_population(),
            candidate_filter=lambda r: r.get("regime") != "TRANSITIONAL",
        )
        validation = runner.to_validation_result(experiment, target_metric="mean_r")
        assert validation.status == ValidationStatus.VALIDATED.value
        assert validation.improvement_detected is True

    def test_no_improvement_inconclusive(self):
        runner = ExperimentRunner()
        experiment = runner.run_filter_experiment(
            proposal=make_proposal(),
            candidate=make_candidate(),
            population=make_population(),
            candidate_filter=lambda r: True,  # Keep all — no change
        )
        validation = runner.to_validation_result(experiment, target_metric="mean_r")
        assert validation.status == ValidationStatus.INCONCLUSIVE.value

    def test_blocked_experiment_produces_blocked_validation(self):
        runner = ExperimentRunner()
        experiment = runner.run_filter_experiment(
            proposal=make_proposal(),
            candidate=make_candidate(),
            population=[],
            candidate_filter=lambda r: True,
        )
        validation = runner.to_validation_result(experiment)
        assert validation.status == ValidationStatus.BLOCKED.value

    def test_promotion_gate_after_validated_experiment(self):
        runner = ExperimentRunner()
        experiment = runner.run_filter_experiment(
            proposal=make_proposal(),
            candidate=make_candidate(),
            population=make_population(),
            candidate_filter=lambda r: r.get("regime") != "TRANSITIONAL",
            universe_versions={"EXECUTION": "abc"},
        )
        validation = runner.to_validation_result(experiment, target_metric="mean_r")

        gate = PromotionGate()
        decision = gate.evaluate(make_proposal(), make_candidate(), validation)
        assert decision.eligible is True
        assert decision.status == PromotionStatus.PROMOTION_ELIGIBLE.value

    def test_promotion_gate_after_blocked_experiment(self):
        runner = ExperimentRunner()
        experiment = runner.run_filter_experiment(
            proposal=make_proposal(),
            candidate=make_candidate(),
            population=[],
            candidate_filter=lambda r: True,
        )
        validation = runner.to_validation_result(experiment)

        gate = PromotionGate()
        decision = gate.evaluate(make_proposal(), make_candidate(), validation)
        assert decision.eligible is False


# ═══════════════════════════════════════════════════════════════════════════════
# GOVERNANCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestGovernance:

    def test_experiment_has_governance_note(self):
        runner = ExperimentRunner()
        result = runner.run_filter_experiment(
            proposal=make_proposal(),
            candidate=make_candidate(),
            population=make_population(),
            candidate_filter=lambda r: True,
        )
        assert "COUNTERFACTUAL" in result.governance_note
        assert "cannot modify" in result.governance_note.lower()

    def test_no_deploy_method(self):
        runner = ExperimentRunner()
        methods = [m for m in dir(runner) if not m.startswith("_")]
        dangerous = [m for m in methods if any(w in m for w in ["deploy", "activate", "execute_trade", "modify_bot"])]
        assert dangerous == []


# ═══════════════════════════════════════════════════════════════════════════════
# SERIALIZATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestSerialization:

    def test_experiment_to_dict(self):
        runner = ExperimentRunner()
        result = runner.run_filter_experiment(
            proposal=make_proposal(),
            candidate=make_candidate(),
            population=make_population(),
            candidate_filter=lambda r: True,
        )
        d = result.to_dict()
        assert json.dumps(d, default=str)
        assert d["provenance"] == "COUNTERFACTUAL"
        assert d["status"] == "COMPLETED"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short", "-p", "no:conftest"]))
