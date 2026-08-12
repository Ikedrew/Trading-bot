"""
Tests for Item 11 Refinement: Candidate Designer.

Covers:
- Valid POPULATION_FILTER design
- Invalid configuration rejection
- Unsupported change type blocking
- CODE_CHANGE blocking
- Declarative filter construction
- Filter execution against records
- Lineage preservation
- Governance boundary
- Experiment integration
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.v10.proposals.model import ChangeProposal, Candidate
from research_engine.v10.proposals.designer import (
    CandidateDesigner,
    CandidateDesignResult,
    ChangeType,
    DesignStatus,
)


def make_proposal(pid="prop_test_001"):
    return ChangeProposal(
        proposal_id=pid,
        source_feedback_ids=["fb_E-001_run1"],
        source_finding_ids=["E-001_run1"],
        system_area="EXECUTION",
        problem_statement="Test weakness",
        hypothesis="Test hypothesis",
        universe_versions={"EXECUTION": "abc"},
        population_versions={"all_trades": "def"},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# VALID DESIGNS
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidDesigns:

    def test_population_filter_accepted(self):
        designer = CandidateDesigner()
        result = designer.design(
            proposal=make_proposal(),
            change_type=ChangeType.POPULATION_FILTER,
            configuration={"field": "regime", "operator": "!=", "value": "TRANSITIONAL"},
            target_metric="mean_r",
        )
        assert result.valid is True
        assert result.candidate.design_status == DesignStatus.EXPERIMENTABLE
        assert result.candidate.change_type == ChangeType.POPULATION_FILTER

    def test_threshold_change_accepted(self):
        designer = CandidateDesigner()
        result = designer.design(
            proposal=make_proposal(),
            change_type=ChangeType.THRESHOLD_CHANGE,
            configuration={"field": "score", "operator": ">=", "value": 70},
            target_metric="mean_r",
        )
        assert result.valid is True
        assert result.candidate.design_status == DesignStatus.EXPERIMENTABLE

    def test_preserves_lineage(self):
        designer = CandidateDesigner()
        result = designer.design(
            proposal=make_proposal(),
            change_type=ChangeType.POPULATION_FILTER,
            configuration={"field": "regime", "operator": "!=", "value": "TRANSITIONAL"},
        )
        c = result.candidate
        assert c.source_proposal_id == "prop_test_001"
        assert "E-001_run1" in c.source_finding_ids
        assert c.universe_versions == {"EXECUTION": "abc"}
        assert c.population_versions == {"all_trades": "def"}

    def test_candidate_id_generated(self):
        designer = CandidateDesigner()
        result = designer.design(
            proposal=make_proposal(),
            change_type=ChangeType.POPULATION_FILTER,
            configuration={"field": "regime", "operator": "!=", "value": "X"},
        )
        assert result.candidate.candidate_id.startswith("cand_")

    def test_critical_metrics_preserved(self):
        designer = CandidateDesigner()
        result = designer.design(
            proposal=make_proposal(),
            change_type=ChangeType.POPULATION_FILTER,
            configuration={"field": "regime", "operator": "!=", "value": "X"},
            critical_metrics=["win_rate", "profit_factor"],
        )
        assert result.candidate.critical_metrics == ["win_rate", "profit_factor"]


# ═══════════════════════════════════════════════════════════════════════════════
# INVALID / BLOCKED DESIGNS
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvalidDesigns:

    def test_missing_field_rejected(self):
        designer = CandidateDesigner()
        result = designer.design(
            proposal=make_proposal(),
            change_type=ChangeType.POPULATION_FILTER,
            configuration={"operator": "!=", "value": "TRANSITIONAL"},
        )
        assert result.valid is False
        assert any("field" in e for e in result.errors)

    def test_missing_operator_rejected(self):
        designer = CandidateDesigner()
        result = designer.design(
            proposal=make_proposal(),
            change_type=ChangeType.POPULATION_FILTER,
            configuration={"field": "regime", "value": "TRANSITIONAL"},
        )
        assert result.valid is False
        assert any("operator" in e for e in result.errors)

    def test_missing_value_rejected(self):
        designer = CandidateDesigner()
        result = designer.design(
            proposal=make_proposal(),
            change_type=ChangeType.POPULATION_FILTER,
            configuration={"field": "regime", "operator": "!="},
        )
        assert result.valid is False
        assert any("value" in e for e in result.errors)

    def test_invalid_operator_rejected(self):
        designer = CandidateDesigner()
        result = designer.design(
            proposal=make_proposal(),
            change_type=ChangeType.POPULATION_FILTER,
            configuration={"field": "regime", "operator": "LIKE", "value": "X"},
        )
        assert result.valid is False
        assert any("operator" in e for e in result.errors)

    def test_code_change_blocked(self):
        designer = CandidateDesigner()
        result = designer.design(
            proposal=make_proposal(),
            change_type=ChangeType.CODE_CHANGE,
            configuration={},
        )
        assert result.valid is False
        assert any("CODE_CHANGE" in e for e in result.errors)

    def test_unsupported_blocked(self):
        designer = CandidateDesigner()
        result = designer.design(
            proposal=make_proposal(),
            change_type=ChangeType.UNSUPPORTED,
            configuration={},
        )
        assert result.valid is False

    def test_missing_proposal_id(self):
        designer = CandidateDesigner()
        result = designer.design(
            proposal=ChangeProposal(),  # No proposal_id
            change_type=ChangeType.POPULATION_FILTER,
            configuration={"field": "x", "operator": "==", "value": "y"},
        )
        assert result.valid is False
        assert any("proposal_id" in e for e in result.errors)


# ═══════════════════════════════════════════════════════════════════════════════
# DECLARATIVE FILTER
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeclarativeFilter:

    def test_not_equal_filter(self):
        designer = CandidateDesigner()
        f = designer.build_filter({"field": "regime", "operator": "!=", "value": "TRANSITIONAL"})
        assert f({"regime": "TRENDING"}) is True
        assert f({"regime": "TRANSITIONAL"}) is False

    def test_equal_filter(self):
        designer = CandidateDesigner()
        f = designer.build_filter({"field": "direction", "operator": "==", "value": "LONG"})
        assert f({"direction": "LONG"}) is True
        assert f({"direction": "SHORT"}) is False

    def test_greater_than_filter(self):
        designer = CandidateDesigner()
        f = designer.build_filter({"field": "score", "operator": ">=", "value": 70})
        assert f({"score": 80}) is True
        assert f({"score": 50}) is False

    def test_in_filter(self):
        designer = CandidateDesigner()
        f = designer.build_filter({"field": "session", "operator": "in", "value": ["LONDON", "NEW_YORK"]})
        assert f({"session": "LONDON"}) is True
        assert f({"session": "ASIA"}) is False

    def test_not_in_filter(self):
        designer = CandidateDesigner()
        f = designer.build_filter({"field": "exit_reason", "operator": "not_in", "value": ["manual"]})
        assert f({"exit_reason": "tp"}) is True
        assert f({"exit_reason": "manual"}) is False

    def test_filter_on_real_population(self):
        """Simulate the EM-001 candidate on representative data."""
        designer = CandidateDesigner()
        f = designer.build_filter({"field": "regime", "operator": "!=", "value": "TRANSITIONAL"})

        population = [
            {"trade_id": "t1", "regime": "TRENDING", "r_multiple": 2.0},
            {"trade_id": "t2", "regime": "TRANSITIONAL", "r_multiple": -1.0},
            {"trade_id": "t3", "regime": "RANGING", "r_multiple": 1.5},
            {"trade_id": "t4", "regime": "TRANSITIONAL", "r_multiple": -1.0},
        ]
        filtered = [r for r in population if f(r)]
        assert len(filtered) == 2
        assert all(r["regime"] != "TRANSITIONAL" for r in filtered)


# ═══════════════════════════════════════════════════════════════════════════════
# GOVERNANCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestGovernance:

    def test_candidate_governance_note(self):
        designer = CandidateDesigner()
        result = designer.design(
            proposal=make_proposal(),
            change_type=ChangeType.POPULATION_FILTER,
            configuration={"field": "regime", "operator": "!=", "value": "X"},
        )
        assert "does not modify" in result.candidate.governance_note.lower()

    def test_no_deploy_method(self):
        designer = CandidateDesigner()
        methods = [m for m in dir(designer) if not m.startswith("_")]
        dangerous = [m for m in methods if any(w in m for w in ["deploy", "activate", "execute_trade"])]
        assert dangerous == []


# ═══════════════════════════════════════════════════════════════════════════════
# EXPERIMENT INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestExperimentIntegration:

    def test_experimentable_candidate_produces_filter(self):
        """A designed POPULATION_FILTER candidate can produce a filter for ExperimentRunner."""
        designer = CandidateDesigner()
        result = designer.design(
            proposal=make_proposal(),
            change_type=ChangeType.POPULATION_FILTER,
            configuration={"field": "regime", "operator": "!=", "value": "TRANSITIONAL"},
        )
        assert result.valid
        assert result.candidate.design_status == DesignStatus.EXPERIMENTABLE

        # Build filter from candidate config
        f = designer.build_filter(result.candidate.configuration)
        assert callable(f)
        assert f({"regime": "TRENDING"}) is True
        assert f({"regime": "TRANSITIONAL"}) is False

    def test_blocked_candidate_cannot_produce_filter(self):
        """CODE_CHANGE candidates are blocked before reaching experiment."""
        designer = CandidateDesigner()
        result = designer.design(
            proposal=make_proposal(),
            change_type=ChangeType.CODE_CHANGE,
            configuration={},
        )
        assert result.valid is False
        # Cannot build filter — no valid candidate


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short", "-p", "no:conftest"]))
