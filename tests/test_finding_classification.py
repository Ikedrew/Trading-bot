"""
Finding Classification Fix Tests.

Proves:
    - Large raw population + zero analytical records → INCONCLUSIVE / INSUFFICIENT
    - Large raw population + valid analytical records → normal classification
    - Small analytical sample → INSUFFICIENT
    - Empty metrics with zero analytical sample → INCONCLUSIVE
    - Existing successful findings remain correctly classified
"""

import pytest

from research_engine.v10.runner.primitives.base import AnalysisResult
from research_engine.v10.runner.question_runner import (
    compose_evidence,
    _determine_outcome,
    _determine_confidence,
    RunContext,
)
from research_engine.v10.universes.question_bank import get_question


class TestDetermineOutcome:

    def test_zero_analytical_sample_is_inconclusive(self):
        """Primary primitive with 0 usable records → INCONCLUSIVE."""
        primary = AnalysisResult(
            analysis_type="predictive_power",
            success=True,
            sample_size=0,
            metrics={},
        )
        outcome = _determine_outcome(primary, {})
        assert outcome == "INCONCLUSIVE"

    def test_empty_metrics_is_inconclusive(self):
        """Primary produced no meaningful numeric metrics → INCONCLUSIVE."""
        primary = AnalysisResult(
            analysis_type="segmentation",
            success=True,
            sample_size=50,
            metrics={"dimensions": ["symbol"], "segment_count": 0},
        )
        # Only non-numeric/zero values → INCONCLUSIVE
        outcome = _determine_outcome(primary, {"dimensions": ["symbol"], "segment_count": 0})
        assert outcome == "INCONCLUSIVE"

    def test_valid_expectancy_positive(self):
        """Normal case: valid mean_r > 0.05 → POSITIVE."""
        primary = AnalysisResult(
            analysis_type="expectancy", success=True, sample_size=94,
            metrics={"mean_r": 0.15, "count": 94},
        )
        outcome = _determine_outcome(primary, {"mean_r": 0.15, "count": 94})
        assert outcome == "POSITIVE"

    def test_valid_expectancy_negative(self):
        primary = AnalysisResult(
            analysis_type="expectancy", success=True, sample_size=94,
            metrics={"mean_r": -0.18, "count": 94},
        )
        outcome = _determine_outcome(primary, {"mean_r": -0.18, "count": 94})
        assert outcome == "NEGATIVE"

    def test_calibration_metric_classified(self):
        primary = AnalysisResult(
            analysis_type="calibration", success=True, sample_size=50,
            metrics={"mean_calibration_error": 0.05, "buckets": 5},
        )
        outcome = _determine_outcome(primary, {"mean_calibration_error": 0.05, "buckets": 5})
        assert outcome == "WELL_CALIBRATED"

    def test_predictive_power_classified(self):
        primary = AnalysisResult(
            analysis_type="predictive_power", success=True, sample_size=80,
            metrics={"monotonic": True, "top_bottom_spread": 0.5},
        )
        outcome = _determine_outcome(primary, {"monotonic": True, "top_bottom_spread": 0.5})
        assert outcome == "PREDICTIVE"

    def test_failed_primary_is_analysis_failed(self):
        primary = AnalysisResult(
            analysis_type="expectancy", success=False, error="test error",
        )
        outcome = _determine_outcome(primary, {})
        assert outcome == "ANALYSIS_FAILED"


class TestDetermineConfidence:

    def test_zero_analytical_sample_is_insufficient(self):
        """Primary with 0 usable analytical records → INSUFFICIENT."""
        primary = AnalysisResult(
            analysis_type="predictive_power", success=True, sample_size=0,
        )
        # Even though raw population might be 7900
        confidence = _determine_confidence(primary, 7900)
        assert confidence == "INSUFFICIENT"

    def test_large_analytical_sample_is_high(self):
        primary = AnalysisResult(
            analysis_type="expectancy", success=True, sample_size=250,
        )
        confidence = _determine_confidence(primary, 250)
        assert confidence == "HIGH"

    def test_medium_analytical_sample(self):
        primary = AnalysisResult(
            analysis_type="expectancy", success=True, sample_size=80,
        )
        confidence = _determine_confidence(primary, 80)
        assert confidence == "MEDIUM"

    def test_low_analytical_sample(self):
        primary = AnalysisResult(
            analysis_type="expectancy", success=True, sample_size=25,
        )
        confidence = _determine_confidence(primary, 25)
        assert confidence == "LOW"

    def test_tiny_analytical_sample_is_insufficient(self):
        primary = AnalysisResult(
            analysis_type="calibration", success=True, sample_size=5,
        )
        confidence = _determine_confidence(primary, 5)
        assert confidence == "INSUFFICIENT"

    def test_raw_population_ignored_when_analytical_is_zero(self):
        """7900 raw records but 0 analytical → INSUFFICIENT, NOT HIGH."""
        primary = AnalysisResult(
            analysis_type="predictive_power", success=True, sample_size=0,
        )
        confidence = _determine_confidence(primary, 7900)
        assert confidence == "INSUFFICIENT"
        # This is the D-001 scenario: 7900 decisions, 0 with r_multiple


class TestComposeEvidenceClassification:

    def test_d001_scenario_becomes_inconclusive(self):
        """D-001 scenario: 7900 records, 0 analytical → INCONCLUSIVE/INSUFFICIENT."""
        q = get_question("D-001")
        # Simulate what predictive_power returns with 0 usable records
        results = [
            AnalysisResult(
                analysis_type="predictive_power", success=True,
                sample_size=0, metrics={},
                warnings=["Insufficient data (0 records)"],
            ),
        ]
        ctx = RunContext(run_id="test")
        # 7900 records in raw population but none with r_multiple
        population = [{"score": 70, "r_multiple": None}] * 7900

        finding = compose_evidence(q, results, ctx, population)
        assert finding.outcome == "INCONCLUSIVE"
        assert finding.confidence == "INSUFFICIENT"

    def test_e001_scenario_remains_negative(self):
        """E-001 scenario: 94 records with valid r_multiple → NEGATIVE/MEDIUM."""
        q = get_question("E-001")
        results = [
            AnalysisResult(
                analysis_type="expectancy", success=True,
                sample_size=94,
                metrics={"mean_r": -0.18, "count": 94, "win_rate": 0.36,
                         "wins": 34, "losses": 60},
                evidence=["Negative expectancy: -0.18R per trade"],
            ),
        ]
        ctx = RunContext(run_id="test")
        population = [{"r_multiple": -0.5}] * 94

        finding = compose_evidence(q, results, ctx, population)
        assert finding.outcome == "NEGATIVE"
        assert finding.confidence == "MEDIUM"

    def test_high_confidence_only_with_large_analytical_sample(self):
        """HIGH confidence requires 200+ analytical records, not just population size."""
        q = get_question("D-001")
        results = [
            AnalysisResult(
                analysis_type="predictive_power", success=True,
                sample_size=250,
                metrics={"monotonic": True, "top_bottom_spread": 0.4, "bucket_count": 4},
            ),
        ]
        ctx = RunContext(run_id="test")
        population = [{}] * 250

        finding = compose_evidence(q, results, ctx, population)
        assert finding.confidence == "HIGH"
        assert finding.outcome == "PREDICTIVE"
