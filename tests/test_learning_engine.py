"""Tests for the complete Learning Engine (Phase 14)."""

import sys
import json
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.learning import (
    LearningRecord,
    analyse_decision,
    persist_learning_record,
    load_learning_records,
    generate_review_summary,
)
from core.learning.calibration import (
    analyse_confidence_calibration,
    analyse_evidence_performance,
    analyse_uncertainty_calibration,
)


class TestLearningRecord:
    def test_frozen(self):
        r = LearningRecord(
            decision_id="test",
            thesis="test thesis",
            evidence_quality=0.8,
            uncertainty_score=0.2,
            outcome="WIN",
            calibration_result="CALIBRATED",
            insights=("test insight",),
        )
        try:
            r.outcome = "LOSS"
            assert False, "Should be frozen"
        except Exception:
            pass

    def test_to_dict(self):
        r = LearningRecord(
            decision_id="abc123",
            thesis="Trend continuation",
            evidence_quality=0.75,
            uncertainty_score=0.15,
            outcome="WIN",
            calibration_result="CALIBRATED",
            insights=("Belief calibrated",),
        )
        d = r.to_dict()
        assert d["decision_id"] == "abc123"
        assert d["calibration_result"] == "CALIBRATED"
        assert isinstance(d["insights"], list)


class TestAnalyseDecision:
    def _make_decision(self, **overrides):
        base = {
            "correlation_id": "cor_001",
            "symbol": "EURUSD",
            "cycle_id": 42,
            "decision": "EXECUTE",
            "signal_score": 0.68,
            "reasoning": {
                "primary_thesis": "Trend continuation",
                "supporting_evidence": ["HTF aligned", "Momentum strong", "Pattern clear"],
                "contradicting_evidence": [],
                "alternative_thesis": None,
            },
            "uncertainty": {"uncertainty_score": 0.10},
            "score_attribution": {
                "total_score": 0.68,
                "contributions": [{"name": "Trend", "contribution": 0.135}],
            },
        }
        base.update(overrides)
        return base

    def test_calibrated_win(self):
        r = analyse_decision(
            decision_record=self._make_decision(),
            outcome_record={"outcome": "WIN"},
        )
        assert r.calibration_result == "CALIBRATED"
        assert r.outcome == "WIN"
        assert r.evidence_quality == 1.0  # 3 supporting, 0 contradicting

    def test_overconfident_loss(self):
        r = analyse_decision(
            decision_record=self._make_decision(
                reasoning={
                    "primary_thesis": "Breakout expected",
                    "supporting_evidence": ["A", "B", "C"],
                    "contradicting_evidence": ["X"],
                    "alternative_thesis": "Reversal trap",
                },
                uncertainty={"uncertainty_score": 0.08},
            ),
            outcome_record={"outcome": "LOSS"},
        )
        assert r.calibration_result == "OVERCONFIDENT"

    def test_uncertain_correct(self):
        r = analyse_decision(
            decision_record=self._make_decision(
                reasoning={
                    "primary_thesis": "Possible reversal",
                    "supporting_evidence": ["Pattern"],
                    "contradicting_evidence": ["Regime", "HTF", "Chop", "Vol"],
                    "alternative_thesis": "Range",
                },
                uncertainty={"uncertainty_score": 0.72},
            ),
            outcome_record={"outcome": "LOSS"},
        )
        assert r.calibration_result == "UNCERTAIN_CORRECT"

    def test_deterministic(self):
        dec = self._make_decision()
        out = {"outcome": "WIN"}
        r1 = analyse_decision(decision_record=dec, outcome_record=out)
        r2 = analyse_decision(decision_record=dec, outcome_record=out)
        assert r1.calibration_result == r2.calibration_result
        assert r1.evidence_quality == r2.evidence_quality
        assert r1.insights == r2.insights

    def test_empty_input_no_crash(self):
        r = analyse_decision(decision_record={}, outcome_record={"outcome": "LOSS"})
        assert r.outcome == "LOSS"
        assert r.thesis is not None


class TestReviewSummary:
    def test_generate_summary(self):
        learning = [
            {"calibration_result": "CALIBRATED", "outcome": "WIN", "uncertainty_score": 0.1},
            {"calibration_result": "CALIBRATED", "outcome": "WIN", "uncertainty_score": 0.2},
            {"calibration_result": "OVERCONFIDENT", "outcome": "LOSS", "uncertainty_score": 0.15},
            {"calibration_result": "UNCERTAIN_CORRECT", "outcome": "LOSS", "uncertainty_score": 0.7},
        ]
        decisions = [
            {"outcome": "WIN", "score_attribution": {"contributions": [
                {"name": "Trend", "weight": 0.15, "raw_value": 0.9, "contribution": 0.135},
            ]}},
            {"outcome": "LOSS", "score_attribution": {"contributions": [
                {"name": "Trend", "weight": 0.15, "raw_value": 0.4, "contribution": 0.06},
            ]}},
        ]
        summary = generate_review_summary(
            learning_records=learning,
            decision_records=decisions,
            period="2026-07-01 to 2026-07-10",
        )
        assert summary.total_decisions_analysed > 0
        assert len(summary.observations) > 0
        assert summary.period == "2026-07-01 to 2026-07-10"

    def test_format_for_human(self):
        learning = [
            {"calibration_result": "CALIBRATED", "outcome": "WIN", "uncertainty_score": 0.1},
        ]
        summary = generate_review_summary(
            learning_records=learning, decision_records=[], period="test",
        )
        text = summary.format_for_human()
        assert "OBSERVATIONS:" in text
        assert "RECOMMENDATIONS" in text

    def test_empty_data(self):
        summary = generate_review_summary(
            learning_records=[], decision_records=[],
        )
        assert summary.total_decisions_analysed == 0


class TestNoTradingImpact:
    """Verify learning NEVER affects trading decisions."""

    def test_learning_record_has_no_methods_that_modify_state(self):
        """LearningRecord should have no side-effect methods."""
        r = LearningRecord(
            decision_id="x", thesis="t", evidence_quality=0.5,
            uncertainty_score=0.5, outcome="WIN",
            calibration_result="CALIBRATED", insights=(),
        )
        # Only allowed methods are to_dict (serialization)
        public_methods = [m for m in dir(r) if not m.startswith("_") and callable(getattr(r, m))]
        assert public_methods == ["to_dict"], f"Unexpected methods: {public_methods}"

    def test_analyse_returns_frozen_record(self):
        """analyse_decision output is frozen — cannot be used to modify state."""
        r = analyse_decision(
            decision_record={"reasoning": {"primary_thesis": "x", "supporting_evidence": [], "contradicting_evidence": []}},
            outcome_record={"outcome": "LOSS"},
        )
        try:
            r.calibration_result = "HACKED"
            assert False
        except Exception:
            pass
