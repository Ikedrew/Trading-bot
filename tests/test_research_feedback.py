"""
Tests for Research → System Feedback Loop (Item 9).

Covers:
- Feedback type classification (deterministic rules)
- System area mapping
- Proposal eligibility
- Lineage preservation
- Persistence
- Governance boundary
- Positive/negative/inconclusive findings
- Insufficient evidence handling
- Serialization
"""

import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.v10.feedback.model import (
    FeedbackType,
    SystemArea,
    ResearchFeedback,
)
from research_engine.v10.feedback.generator import FeedbackGenerator
from research_engine.v10.feedback.persistence import FeedbackStore


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


def make_finding(question_id="E-001", outcome="POSITIVE", confidence="MEDIUM",
                 universes_used=None, sample_sizes=None, title="Test Finding"):
    return {
        "question_id": question_id,
        "title": title,
        "run_id": "run_test_001",
        "outcome": outcome,
        "confidence": confidence,
        "conclusion": f"Test conclusion: {outcome}",
        "universes_used": universes_used or ["EXECUTION"],
        "sample_sizes": sample_sizes or {"population": 94, "analytical_sample": 94, "minimum_required": 20},
        "primary_metrics": {"mean_r": 0.18},
        "question_version": "1.0.0",
        "analysis_version": "1.0.0",
        "universe_versions": {"EXECUTION": "abc123"},
        "population_versions": {"all_trades": "def456"},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# FEEDBACK TYPE CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestFeedbackTypeClassification:

    def test_positive_high_is_strength(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(outcome="POSITIVE", confidence="HIGH"))
        assert fb.feedback_type == FeedbackType.CONFIRMED_STRENGTH.value

    def test_positive_medium_is_strength(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(outcome="POSITIVE", confidence="MEDIUM"))
        assert fb.feedback_type == FeedbackType.CONFIRMED_STRENGTH.value

    def test_positive_low_is_opportunity(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(outcome="POSITIVE", confidence="LOW"))
        assert fb.feedback_type == FeedbackType.OPPORTUNITY.value

    def test_negative_medium_is_weakness(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(outcome="NEGATIVE", confidence="MEDIUM"))
        assert fb.feedback_type == FeedbackType.IDENTIFIED_WEAKNESS.value

    def test_negative_high_is_weakness(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(outcome="NEGATIVE", confidence="HIGH"))
        assert fb.feedback_type == FeedbackType.IDENTIFIED_WEAKNESS.value

    def test_negative_low_is_uncertainty(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(outcome="NEGATIVE", confidence="LOW"))
        assert fb.feedback_type == FeedbackType.UNCERTAINTY.value

    def test_inconclusive_insufficient_is_data_gap(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(outcome="INCONCLUSIVE", confidence="INSUFFICIENT"))
        assert fb.feedback_type == FeedbackType.DATA_GAP.value

    def test_inconclusive_medium_is_uncertainty(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(outcome="INCONCLUSIVE", confidence="MEDIUM"))
        assert fb.feedback_type == FeedbackType.UNCERTAINTY.value

    def test_predictive_is_strength(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(outcome="PREDICTIVE", confidence="MEDIUM"))
        assert fb.feedback_type == FeedbackType.CONFIRMED_STRENGTH.value

    def test_not_predictive_is_weakness(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(outcome="NOT_PREDICTIVE", confidence="MEDIUM"))
        assert fb.feedback_type == FeedbackType.IDENTIFIED_WEAKNESS.value

    def test_completed_is_no_action(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(outcome="COMPLETED", confidence="MEDIUM"))
        assert fb.feedback_type == FeedbackType.NO_ACTION.value

    def test_deterministic(self):
        gen = FeedbackGenerator()
        f = make_finding()
        fb1 = gen.from_finding(f)
        fb2 = gen.from_finding(f)
        assert fb1.feedback_type == fb2.feedback_type
        assert fb1.system_area == fb2.system_area
        assert fb1.proposal_eligible == fb2.proposal_eligible


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM AREA MAPPING
# ═══════════════════════════════════════════════════════════════════════════════


class TestSystemAreaMapping:

    def test_execution_prefix(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(question_id="E-001"))
        assert fb.system_area == SystemArea.EXECUTION.value

    def test_decision_prefix(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(question_id="D-001", universes_used=["DECISION"]))
        assert fb.system_area == SystemArea.DECISION.value

    def test_market_prefix(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(question_id="M-001", universes_used=["MARKET"]))
        assert fb.system_area == SystemArea.MARKET.value

    def test_strategy_prefix(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(question_id="S-001", universes_used=["STRATEGY"]))
        assert fb.system_area == SystemArea.STRATEGY.value

    def test_cross_universe(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(question_id="ED-001", universes_used=["EXECUTION", "DECISION"]))
        assert fb.system_area == SystemArea.CROSS_UNIVERSE.value

    def test_edm_cross(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(question_id="EDM-001", universes_used=["EXECUTION", "DECISION", "MARKET"]))
        assert fb.system_area == SystemArea.CROSS_UNIVERSE.value


# ═══════════════════════════════════════════════════════════════════════════════
# PROPOSAL ELIGIBILITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestProposalEligibility:

    def test_weakness_medium_eligible(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(outcome="NEGATIVE", confidence="MEDIUM"))
        assert fb.proposal_eligible is True

    def test_weakness_low_not_eligible(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(outcome="NEGATIVE", confidence="LOW"))
        assert fb.proposal_eligible is False

    def test_strength_not_eligible(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(outcome="POSITIVE", confidence="HIGH"))
        assert fb.proposal_eligible is False

    def test_insufficient_sample_blocks(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(
            outcome="NEGATIVE", confidence="MEDIUM",
            sample_sizes={"population": 10, "analytical_sample": 5, "minimum_required": 20},
        ))
        assert fb.proposal_eligible is False
        assert "analytical_sample" in fb.proposal_blocked_reason

    def test_data_gap_not_eligible(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(outcome="INCONCLUSIVE", confidence="INSUFFICIENT"))
        assert fb.proposal_eligible is False


# ═══════════════════════════════════════════════════════════════════════════════
# LINEAGE PRESERVATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestLineagePreservation:

    def test_preserves_run_id(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding())
        assert fb.run_id == "run_test_001"

    def test_preserves_question_id(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding(question_id="M-003"))
        assert fb.question_id == "M-003"

    def test_preserves_universe_versions(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding())
        assert fb.universe_versions == {"EXECUTION": "abc123"}

    def test_preserves_population_versions(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding())
        assert fb.population_versions == {"all_trades": "def456"}

    def test_preserves_analysis_version(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding())
        assert fb.analysis_version == "1.0.0"


# ═══════════════════════════════════════════════════════════════════════════════
# GOVERNANCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestGovernance:

    def test_governance_note_present(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding())
        assert "research" in fb.governance_note.lower()
        assert "cannot" in fb.governance_note.lower() or "not" in fb.governance_note.lower()

    def test_feedback_cannot_modify_trading(self):
        """ResearchFeedback has no method to modify trading state."""
        fb = ResearchFeedback()
        methods = [m for m in dir(fb) if not m.startswith("_")]
        trading_methods = [m for m in methods if any(
            word in m for word in ["execute", "trade", "deploy", "activate", "modify_bot"]
        )]
        assert trading_methods == []


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestFeedbackPersistence:

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(base_dir=tmp)
            gen = FeedbackGenerator()
            fb = gen.from_finding(make_finding(question_id="E-001"))
            store.save(fb)

            loaded = store.load_latest("E-001")
            assert loaded is not None
            assert loaded["question_id"] == "E-001"
            assert loaded["feedback_type"] == fb.feedback_type

    def test_immutable_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(base_dir=tmp)
            gen = FeedbackGenerator()

            # Save first feedback
            fb1 = gen.from_finding(make_finding(outcome="POSITIVE"))
            store.save(fb1)

            # Save second (different outcome)
            fb2 = gen.from_finding(make_finding(outcome="NEGATIVE"))
            store.save(fb2)

            # Both should exist in history
            from pathlib import Path
            history = list((Path(tmp) / "E-001" / "history").glob("*.json"))
            assert len(history) == 2

    def test_list_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(base_dir=tmp)
            gen = FeedbackGenerator()
            store.save(gen.from_finding(make_finding(question_id="E-001")))
            store.save(gen.from_finding(make_finding(question_id="D-001")))

            questions = store.list_questions()
            assert "E-001" in questions
            assert "D-001" in questions


# ═══════════════════════════════════════════════════════════════════════════════
# BATCH / SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════


class TestBatchAndSummary:

    def test_from_run(self):
        gen = FeedbackGenerator()
        findings = [
            make_finding(question_id="E-001", outcome="POSITIVE", confidence="HIGH"),
            make_finding(question_id="E-002", outcome="NEGATIVE", confidence="MEDIUM"),
            make_finding(question_id="M-001", outcome="INCONCLUSIVE", confidence="INSUFFICIENT"),
        ]
        feedbacks = gen.from_run(findings)
        assert len(feedbacks) == 3

    def test_summary(self):
        gen = FeedbackGenerator()
        findings = [
            make_finding(question_id="E-001", outcome="POSITIVE", confidence="HIGH"),
            make_finding(question_id="E-002", outcome="NEGATIVE", confidence="MEDIUM"),
            make_finding(question_id="M-001", outcome="INCONCLUSIVE", confidence="INSUFFICIENT"),
        ]
        feedbacks = gen.from_run(findings)
        summary = gen.summary(feedbacks)
        assert summary["total"] == 3
        assert summary["strengths"] == 1
        assert summary["weaknesses"] == 1
        assert summary["data_gaps"] == 1

    def test_to_dict_serializable(self):
        gen = FeedbackGenerator()
        fb = gen.from_finding(make_finding())
        d = fb.to_dict()
        # Must be JSON-serializable
        serialized = json.dumps(d, default=str)
        assert len(serialized) > 0


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short", "-p", "no:conftest"]))
