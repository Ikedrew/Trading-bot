"""Tests for the Learning Engine calibration analysis."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.learning.calibration import (
    analyse_confidence_calibration,
    analyse_evidence_performance,
    analyse_uncertainty_calibration,
)


class TestConfidenceCalibration:
    def test_basic_calibration_report(self):
        records = [
            {"calibration_result": "CALIBRATED", "outcome": "WIN", "uncertainty_score": 0.1},
            {"calibration_result": "CALIBRATED", "outcome": "WIN", "uncertainty_score": 0.15},
            {"calibration_result": "CALIBRATED", "outcome": "WIN", "uncertainty_score": 0.2},
            {"calibration_result": "OVERCONFIDENT", "outcome": "LOSS", "uncertainty_score": 0.1},
            {"calibration_result": "UNCERTAIN_CORRECT", "outcome": "LOSS", "uncertainty_score": 0.7},
            {"calibration_result": "CALIBRATED", "outcome": "WIN", "uncertainty_score": 0.3},
            {"calibration_result": "OVERCONFIDENT", "outcome": "LOSS", "uncertainty_score": 0.2},
            {"calibration_result": "CALIBRATED", "outcome": "LOSS", "uncertainty_score": 0.35},
            {"calibration_result": "UNCERTAIN_WRONG", "outcome": "WIN", "uncertainty_score": 0.6},
            {"calibration_result": "CALIBRATED", "outcome": "WIN", "uncertainty_score": 0.1},
        ]
        cal = analyse_confidence_calibration(records)
        assert cal.total_decisions == 10
        assert cal.calibrated_count == 6
        assert cal.overconfident_count == 2
        assert cal.calibration_rate == 0.6
        assert cal.overconfidence_rate == 0.2
        assert len(cal.insights) > 0

    def test_empty_records(self):
        cal = analyse_confidence_calibration([])
        assert cal.total_decisions == 0
        assert cal.calibration_rate == 0.0

    def test_serialization(self):
        records = [{"calibration_result": "CALIBRATED", "outcome": "WIN", "uncertainty_score": 0.1}]
        cal = analyse_confidence_calibration(records)
        d = cal.to_dict()
        assert "calibration_rate" in d
        assert "insights" in d


class TestEvidencePerformance:
    def test_factor_correlation(self):
        """Trend higher in wins = positive. Bias higher in losses = negative."""
        decisions = [
            {"outcome": "WIN", "score_attribution": {"contributions": [
                {"name": "Trend", "weight": 0.15, "raw_value": 0.9, "contribution": 0.135},
                {"name": "Bias", "weight": 0.18, "raw_value": 0.3, "contribution": 0.054},
            ]}},
            {"outcome": "WIN", "score_attribution": {"contributions": [
                {"name": "Trend", "weight": 0.15, "raw_value": 0.85, "contribution": 0.128},
                {"name": "Bias", "weight": 0.18, "raw_value": 0.25, "contribution": 0.045},
            ]}},
            {"outcome": "LOSS", "score_attribution": {"contributions": [
                {"name": "Trend", "weight": 0.15, "raw_value": 0.3, "contribution": 0.045},
                {"name": "Bias", "weight": 0.18, "raw_value": 0.95, "contribution": 0.171},
            ]}},
            {"outcome": "LOSS", "score_attribution": {"contributions": [
                {"name": "Trend", "weight": 0.15, "raw_value": 0.25, "contribution": 0.038},
                {"name": "Bias", "weight": 0.18, "raw_value": 0.90, "contribution": 0.162},
            ]}},
        ]
        ev = analyse_evidence_performance(decisions)
        trend = next(r for r in ev.factor_reports if r["name"] == "Trend")
        bias = next(r for r in ev.factor_reports if r["name"] == "Bias")
        assert trend["correlation"] == "positive"
        assert bias["correlation"] == "negative"
        assert len(ev.insights) > 0

    def test_empty_input(self):
        ev = analyse_evidence_performance([])
        assert ev.factor_reports == ()


class TestUncertaintyCalibration:
    def test_uncertainty_predictive(self):
        """Low uncertainty should win more than high uncertainty."""
        records = [
            {"uncertainty_score": 0.1, "outcome": "WIN"},
            {"uncertainty_score": 0.15, "outcome": "WIN"},
            {"uncertainty_score": 0.2, "outcome": "WIN"},
            {"uncertainty_score": 0.3, "outcome": "WIN"},
            {"uncertainty_score": 0.35, "outcome": "LOSS"},
            {"uncertainty_score": 0.6, "outcome": "WIN"},
            {"uncertainty_score": 0.7, "outcome": "LOSS"},
            {"uncertainty_score": 0.8, "outcome": "LOSS"},
            {"uncertainty_score": 0.75, "outcome": "LOSS"},
        ]
        unc = analyse_uncertainty_calibration(records)
        assert unc.low_uncertainty_win_rate > unc.high_uncertainty_win_rate
        assert unc.uncertainty_predictive is True
        assert len(unc.insights) > 0

    def test_empty_input(self):
        unc = analyse_uncertainty_calibration([])
        assert unc.low_uncertainty_count == 0
        assert unc.uncertainty_predictive is False

    def test_serialization(self):
        records = [
            {"uncertainty_score": 0.1, "outcome": "WIN"},
            {"uncertainty_score": 0.8, "outcome": "LOSS"},
        ]
        unc = analyse_uncertainty_calibration(records)
        d = unc.to_dict()
        assert "low_uncertainty_win_rate" in d
        assert "uncertainty_predictive" in d
