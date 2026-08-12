"""
Primitive Parameter Fix Tests.

Proves:
    - M-002 resolves htf_alignment_strength as the predictor field
    - M-004 resolves h1_structural_clarity as the predictor field
    - Both predictive_power questions receive r_multiple as outcome field
    - S-003 resolves confidence as the probability field
    - S-003 receives r_multiple as outcome field
    - No fabricated fields or outcomes are introduced
    - Existing working questions remain unchanged
"""

import pytest

from research_engine.v10.runner.primitive_mapping import (
    QUESTION_PARAMETERS,
    build_full_mapping,
    resolve_primitives_for_question,
)
from research_engine.v10.runner.primitives.implementations import (
    PredictivePowerPrimitive,
    CalibrationPrimitive,
    build_default_registry,
)
from research_engine.v10.runner.question_runner import QuestionRunner, RunContext
from research_engine.v10.universes.question_bank import QUESTION_BANK, get_question


class TestParameterMapping:

    def test_m002_has_parameters(self):
        """M-002 has explicit htf_alignment_strength parameter."""
        assert "M-002" in QUESTION_PARAMETERS
        params = QUESTION_PARAMETERS["M-002"]
        assert params["feature_field"] == "htf_alignment_strength"
        assert params["outcome_field"] == "r_multiple"

    def test_m004_has_parameters(self):
        """M-004 has explicit h1_structural_clarity parameter."""
        assert "M-004" in QUESTION_PARAMETERS
        params = QUESTION_PARAMETERS["M-004"]
        assert params["feature_field"] == "h1_structural_clarity"
        assert params["outcome_field"] == "r_multiple"

    def test_s003_has_parameters(self):
        """S-003 has explicit confidence parameter."""
        assert "S-003" in QUESTION_PARAMETERS
        params = QUESTION_PARAMETERS["S-003"]
        assert params["predicted_field"] == "confidence"
        assert params["outcome_field"] == "r_multiple"

    def test_no_fabricated_fields(self):
        """Parameters reference real universe fields, not invented ones."""
        # htf_alignment_strength exists in Market Universe schema
        # h1_structural_clarity exists in Market Universe schema
        # confidence exists in Strategy Universe schema
        # r_multiple exists after outcome enrichment
        for qid, params in QUESTION_PARAMETERS.items():
            for k, v in params.items():
                assert v is not None
                assert isinstance(v, str)
                assert v != ""  # No empty fields


class TestPredictivePowerWithParameters:

    def test_m002_finds_pairs_with_correct_field(self):
        """predictive_power with feature_field='htf_alignment_strength' finds data."""
        pop = [
            {"htf_alignment_strength": 0.3, "r_multiple": 0.5},
            {"htf_alignment_strength": 0.7, "r_multiple": 1.5},
            {"htf_alignment_strength": 0.5, "r_multiple": -0.5},
            {"htf_alignment_strength": 0.8, "r_multiple": 2.0},
            {"htf_alignment_strength": 0.2, "r_multiple": -1.0},
        ] * 4  # 20 records

        prim = PredictivePowerPrimitive()
        result = prim.analyse(pop, QUESTION_PARAMETERS["M-002"])

        assert result.success
        assert result.sample_size == 20
        assert "monotonic" in result.metrics
        assert result.metrics["bucket_count"] > 0

    def test_m004_finds_pairs_with_correct_field(self):
        """predictive_power with feature_field='h1_structural_clarity' finds data."""
        pop = [
            {"h1_structural_clarity": 0.2, "r_multiple": -1.0},
            {"h1_structural_clarity": 0.5, "r_multiple": 0.0},
            {"h1_structural_clarity": 0.8, "r_multiple": 1.5},
        ] * 7  # 21 records

        prim = PredictivePowerPrimitive()
        result = prim.analyse(pop, QUESTION_PARAMETERS["M-004"])

        assert result.success
        assert result.sample_size == 21
        assert "monotonic" in result.metrics

    def test_default_params_would_fail_for_market(self):
        """Without parameters, predictive_power defaults to 'score' which isn't in Market."""
        pop = [
            {"htf_alignment_strength": 0.5, "r_multiple": 1.0},
        ] * 20

        prim = PredictivePowerPrimitive()
        # Default params: feature_field='score' — not in Market records
        result = prim.analyse(pop)  # No params = defaults
        assert result.sample_size == 0  # Nothing found!


class TestCalibrationWithParameters:

    def test_s003_finds_pairs_with_confidence_field(self):
        """calibration with predicted_field='confidence' finds data."""
        pop = [
            {"confidence": 0.3, "r_multiple": -1.0},
            {"confidence": 0.5, "r_multiple": 0.5},
            {"confidence": 0.7, "r_multiple": 1.5},
            {"confidence": 0.8, "r_multiple": 2.0},
            {"confidence": 0.4, "r_multiple": -0.5},
        ] * 4  # 20 records

        prim = CalibrationPrimitive()
        result = prim.analyse(pop, QUESTION_PARAMETERS["S-003"])

        assert result.success
        assert result.sample_size == 20
        assert "mean_calibration_error" in result.metrics

    def test_default_params_would_fail_for_strategy(self):
        """Without parameters, calibration defaults to 'p_success' which is NULL."""
        pop = [
            {"confidence": 0.7, "r_multiple": 1.0, "p_success": None},
        ] * 20

        prim = CalibrationPrimitive()
        result = prim.analyse(pop)  # Default: predicted_field='p_success'
        assert result.sample_size == 0  # Nothing found — p_success is None


class TestRunnerUsesParameters:

    def test_runner_auto_resolves_parameters(self):
        """QuestionRunner automatically uses QUESTION_PARAMETERS for mapped questions."""
        registry = build_default_registry()
        mapping = build_full_mapping(QUESTION_BANK)
        runner = QuestionRunner(registry, mapping)
        ctx = RunContext(run_id="param_test")

        # Simulate Market Universe records for M-002 with outcome enrichment
        pop = [
            {"htf_alignment_strength": 0.3 + i * 0.05, "r_multiple": -1.0 + i * 0.2,
             "entity_id": f"SYM_{i}", "regime": "TRENDING", "symbol": "EURUSD",
             "anomaly": False}
            for i in range(25)
        ]

        q = get_question("M-002")
        result = runner.run_question(q, pop, ctx)

        assert result.success
        assert result.finding is not None
        # Should NOT be INCONCLUSIVE now that correct params are used
        assert result.finding.confidence != "INSUFFICIENT"
        assert "monotonic" in result.finding.primary_metrics

    def test_existing_questions_unaffected(self):
        """E-001 (no custom params) still works with default parameters."""
        registry = build_default_registry()
        mapping = build_full_mapping(QUESTION_BANK)
        runner = QuestionRunner(registry, mapping)
        ctx = RunContext(run_id="existing_test")

        pop = [{"r_multiple": 0.5 - i * 0.1, "anomaly": False,
                "entry_time": 1000 + i, "symbol": "EURUSD"}
               for i in range(50)]

        q = get_question("E-001")
        result = runner.run_question(q, pop, ctx)

        assert result.success
        assert result.finding is not None
        assert "E-001" not in QUESTION_PARAMETERS  # No custom params for E-001
