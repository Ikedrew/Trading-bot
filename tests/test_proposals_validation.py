"""
Tests for Item 11: Finding → Proposal → Validation → Promotion.

Covers:
- Proposal creation from feedback
- Candidate creation
- Baseline vs candidate validation
- Improvement detection
- Regression detection
- Insufficient sample handling
- Promotion gates (all conditions)
- Governance boundary
- Lineage preservation
- Persistence
- No trading/runtime mutation
"""

import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.v10.proposals.model import (
    ChangeProposal,
    Candidate,
    ValidationResult,
    PromotionDecision,
    ValidationStatus,
    PromotionStatus,
)
from research_engine.v10.proposals.generator import ProposalFactory
from research_engine.v10.proposals.validator import ProposalValidator
from research_engine.v10.proposals.promotion import PromotionGate
from research_engine.v10.proposals.store import ProposalStore


def make_feedback(qid="E-001", feedback_type="IDENTIFIED_WEAKNESS", system_area="EXECUTION"):
    return {
        "feedback_id": f"fb_{qid}_run1",
        "source_finding_id": f"{qid}_run1",
        "question_id": qid,
        "feedback_type": feedback_type,
        "system_area": system_area,
        "affected_component": system_area,
        "interpretation": f"Weakness identified in {system_area}",
        "hypothesis": "",
        "proposal_eligible": True,
        "universe_versions": {"EXECUTION": "abc"},
        "population_versions": {"all_trades": "def"},
    }


class TestProposalCreation:

    def test_from_feedback(self):
        factory = ProposalFactory()
        fb = make_feedback()
        proposal = factory.from_feedback(fb)
        assert proposal.proposal_id.startswith("prop_")
        assert proposal.system_area == "EXECUTION"
        assert "EXECUTION" in proposal.universe_versions

    def test_preserves_lineage(self):
        factory = ProposalFactory()
        fb = make_feedback()
        proposal = factory.from_feedback(fb)
        assert fb["feedback_id"] in proposal.source_feedback_ids
        assert fb["source_finding_id"] in proposal.source_finding_ids

    def test_governance_note(self):
        factory = ProposalFactory()
        proposal = factory.from_feedback(make_feedback())
        assert "does not modify" in proposal.governance_note.lower()

    def test_candidate_creation(self):
        factory = ProposalFactory()
        proposal = factory.from_feedback(make_feedback())
        candidate = factory.create_candidate(proposal, description="Test candidate")
        assert candidate.candidate_id.startswith("cand_")
        assert candidate.proposal_id == proposal.proposal_id


class TestValidation:

    def test_improvement_detected(self):
        factory = ProposalFactory()
        proposal = factory.from_feedback(make_feedback())
        candidate = factory.create_candidate(proposal)
        validator = ProposalValidator()

        result = validator.validate(
            proposal=proposal,
            candidate=candidate,
            baseline_metrics={"mean_r": -0.10, "win_rate": 0.36},
            candidate_metrics={"mean_r": 0.15, "win_rate": 0.42},
            target_metric="mean_r",
            sample_sizes={"analytical_sample": 50, "minimum_required": 20},
            universe_versions={"EXECUTION": "abc"},
        )
        assert result.status == ValidationStatus.VALIDATED.value
        assert result.improvement_detected is True
        assert result.target_improvement > 0

    def test_no_improvement_inconclusive(self):
        factory = ProposalFactory()
        proposal = factory.from_feedback(make_feedback())
        candidate = factory.create_candidate(proposal)
        validator = ProposalValidator()

        result = validator.validate(
            proposal=proposal,
            candidate=candidate,
            baseline_metrics={"mean_r": 0.10},
            candidate_metrics={"mean_r": 0.10},
            target_metric="mean_r",
            sample_sizes={"analytical_sample": 50, "minimum_required": 20},
        )
        assert result.status == ValidationStatus.INCONCLUSIVE.value

    def test_regression_rejected(self):
        factory = ProposalFactory()
        proposal = factory.from_feedback(make_feedback())
        candidate = factory.create_candidate(proposal)
        validator = ProposalValidator()

        result = validator.validate(
            proposal=proposal,
            candidate=candidate,
            baseline_metrics={"mean_r": 0.10, "win_rate": 0.50},
            candidate_metrics={"mean_r": 0.15, "win_rate": 0.30},
            target_metric="mean_r",
            critical_metrics=["win_rate"],
            sample_sizes={"analytical_sample": 50, "minimum_required": 20},
        )
        assert result.status == ValidationStatus.REJECTED.value
        assert result.regression_detected is True

    def test_insufficient_sample_blocked(self):
        factory = ProposalFactory()
        proposal = factory.from_feedback(make_feedback())
        candidate = factory.create_candidate(proposal)
        validator = ProposalValidator()

        result = validator.validate(
            proposal=proposal,
            candidate=candidate,
            baseline_metrics={"mean_r": -0.10},
            candidate_metrics={"mean_r": 0.20},
            target_metric="mean_r",
            sample_sizes={"analytical_sample": 5, "minimum_required": 20},
        )
        assert result.status == ValidationStatus.BLOCKED.value

    def test_preserves_provenance(self):
        factory = ProposalFactory()
        proposal = factory.from_feedback(make_feedback())
        candidate = factory.create_candidate(proposal)
        validator = ProposalValidator()

        result = validator.validate(
            proposal=proposal,
            candidate=candidate,
            baseline_metrics={"mean_r": 0.10},
            candidate_metrics={"mean_r": 0.20},
            universe_versions={"EXECUTION": "xyz"},
            population_versions={"all_trades": "123"},
            sample_sizes={"analytical_sample": 50, "minimum_required": 20},
        )
        assert result.universe_versions == {"EXECUTION": "xyz"}


class TestPromotionGate:

    def _make_validated_set(self):
        factory = ProposalFactory()
        proposal = factory.from_feedback(make_feedback())
        candidate = factory.create_candidate(proposal)
        validator = ProposalValidator()
        validation = validator.validate(
            proposal=proposal, candidate=candidate,
            baseline_metrics={"mean_r": -0.10},
            candidate_metrics={"mean_r": 0.20},
            sample_sizes={"analytical_sample": 50, "minimum_required": 20},
            universe_versions={"EXECUTION": "abc"},
        )
        return proposal, candidate, validation

    def test_all_gates_pass(self):
        proposal, candidate, validation = self._make_validated_set()
        gate = PromotionGate()
        decision = gate.evaluate(proposal, candidate, validation)
        assert decision.eligible is True
        assert decision.status == PromotionStatus.PROMOTION_ELIGIBLE.value
        assert len(decision.blockers) == 0

    def test_rejected_validation_blocks(self):
        factory = ProposalFactory()
        proposal = factory.from_feedback(make_feedback())
        candidate = factory.create_candidate(proposal)
        validator = ProposalValidator()
        validation = validator.validate(
            proposal=proposal, candidate=candidate,
            baseline_metrics={"mean_r": 0.10, "win_rate": 0.50},
            candidate_metrics={"mean_r": 0.15, "win_rate": 0.20},
            critical_metrics=["win_rate"],
            sample_sizes={"analytical_sample": 50, "minimum_required": 20},
        )
        gate = PromotionGate()
        decision = gate.evaluate(proposal, candidate, validation)
        assert decision.eligible is False
        assert any("regression" in b.lower() for b in decision.blockers)

    def test_insufficient_sample_blocks(self):
        factory = ProposalFactory()
        proposal = factory.from_feedback(make_feedback())
        candidate = factory.create_candidate(proposal)
        validator = ProposalValidator()
        validation = validator.validate(
            proposal=proposal, candidate=candidate,
            baseline_metrics={"mean_r": -0.10},
            candidate_metrics={"mean_r": 0.20},
            sample_sizes={"analytical_sample": 5, "minimum_required": 20},
        )
        gate = PromotionGate()
        decision = gate.evaluate(proposal, candidate, validation)
        assert decision.eligible is False

    def test_governance_note_present(self):
        proposal, candidate, validation = self._make_validated_set()
        gate = PromotionGate()
        decision = gate.evaluate(proposal, candidate, validation)
        assert "does not deploy" in decision.governance_note.lower()


class TestGovernanceBoundary:

    def test_proposal_has_no_deploy_method(self):
        p = ChangeProposal()
        methods = [m for m in dir(p) if not m.startswith("_")]
        dangerous = [m for m in methods if any(w in m for w in ["deploy", "activate", "execute_trade", "modify_bot"])]
        assert dangerous == []

    def test_promotion_has_no_deploy_method(self):
        d = PromotionDecision()
        methods = [m for m in dir(d) if not m.startswith("_")]
        dangerous = [m for m in methods if any(w in m for w in ["deploy", "activate", "execute", "modify"])]
        assert dangerous == []


class TestPersistence:

    def test_save_and_load_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProposalStore(base_dir=tmp)
            factory = ProposalFactory()
            proposal = factory.from_feedback(make_feedback())
            store.save_proposal(proposal.to_dict())

            loaded = store.load_proposal(proposal.proposal_id)
            assert loaded is not None
            assert loaded["proposal_id"] == proposal.proposal_id

    def test_save_and_load_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProposalStore(base_dir=tmp)
            factory = ProposalFactory()
            proposal = factory.from_feedback(make_feedback())
            candidate = factory.create_candidate(proposal)
            validator = ProposalValidator()
            result = validator.validate(
                proposal=proposal, candidate=candidate,
                baseline_metrics={"mean_r": 0.1},
                candidate_metrics={"mean_r": 0.2},
                sample_sizes={"analytical_sample": 50, "minimum_required": 20},
            )
            store.save_proposal(proposal.to_dict())
            store.save_validation(result.to_dict())

            loaded = store.load_validation(proposal.proposal_id)
            assert loaded is not None
            assert loaded["status"] == result.status

    def test_list_proposals(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProposalStore(base_dir=tmp)
            factory = ProposalFactory()
            p1 = factory.from_feedback(make_feedback(qid="E-001"))
            p2 = factory.from_feedback(make_feedback(qid="D-001"))
            store.save_proposal(p1.to_dict())
            store.save_proposal(p2.to_dict())

            proposals = store.list_proposals()
            assert len(proposals) == 2


class TestSerialization:

    def test_proposal_to_dict(self):
        factory = ProposalFactory()
        p = factory.from_feedback(make_feedback())
        d = p.to_dict()
        assert json.dumps(d, default=str)  # JSON serializable

    def test_validation_to_dict(self):
        factory = ProposalFactory()
        proposal = factory.from_feedback(make_feedback())
        candidate = factory.create_candidate(proposal)
        validator = ProposalValidator()
        result = validator.validate(
            proposal=proposal, candidate=candidate,
            baseline_metrics={"mean_r": 0.1},
            candidate_metrics={"mean_r": 0.2},
            sample_sizes={"analytical_sample": 50, "minimum_required": 20},
        )
        d = result.to_dict()
        assert json.dumps(d, default=str)

    def test_promotion_to_dict(self):
        d = PromotionDecision(eligible=True).to_dict()
        assert json.dumps(d, default=str)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short", "-p", "no:conftest"]))
