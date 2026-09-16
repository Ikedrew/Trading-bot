"""
Wave 4B — READY_FOR_REVIEW -> governed human decision tests.

Proves:
    READY_FOR_REVIEW + explicit ACCEPT/REJECT + actor + reason
    -> durable decision + CandidateRegistry.update_status() -> ACCEPTED/REJECTED

Isolation: every test uses tmp_path for BOTH the candidate registry and the
decision store. Production data/research/candidates/candidates.jsonl and
logs/research_lifecycle/evaluations/ are never touched.
"""

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from research_engine.v10.candidates.candidate_decision import (
    CandidateDecisionStore,
    get_human_decision,
    record_human_decision,
)
from research_engine.v10.candidates.candidate_registry import CandidateRegistry
from research_engine.v10.candidates.models import CandidateRecord, CandidateStatus


# ─── FIXTURES ─────────────────────────────────────────────────────

def _make_candidate(
    candidate_id="OPT-4B-001",
    status=CandidateStatus.READY_FOR_REVIEW,
    with_success=True,
    success_id="EVAL-4b001",
):
    c = CandidateRecord(
        candidate_id=candidate_id,
        hypothesis_id="HYP-4b-wave-test",
        baseline_id="current_v10",
        component="DIRECTION_INVERSION",
        description="Wave 4B test candidate",
        change_definition={"type": "direction_inversion"},
        status=status,
    )
    return c, with_success, success_id


def _seed(tmp_path, candidate_id="OPT-4B-001",
          status=CandidateStatus.SHADOW_TESTING,
          with_success=True, success_id="EVAL-4b001"):
    # Seeds pre-decision candidates in SHADOW_TESTING (the real upstream state);
    # tests move them to READY_FOR_REVIEW via _to_review() governed transitions.
    reg_dir = str(tmp_path / "reg")
    dec_dir = str(tmp_path / "dec")
    reg = CandidateRegistry(storage_dir=reg_dir)
    c, _, _ = _make_candidate(candidate_id, status)
    reg.create(c)
    if with_success:
        reg.add_validation_result(
            candidate_id,
            validation_id=success_id,
            decision="IMPROVED",
            confidence="HIGH",
            sample_size=60,
            expectancy_delta=0.25,
        )
    return reg_dir, dec_dir


def _decisions_in(dec_dir):
    p = Path(dec_dir) / "decisions.jsonl"
    if not p.exists():
        return []
    rows = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            rows.append(ln)
    return rows


def _to_review(reg_dir, candidate_id):
    """Move a seeded candidate to READY_FOR_REVIEW via governed transitions."""
    reg = CandidateRegistry(storage_dir=reg_dir)
    c = reg.get(candidate_id)
    if c.status == CandidateStatus.SHADOW_TESTING:
        reg.update_status(candidate_id, CandidateStatus.READY_FOR_REVIEW)
    elif c.status == CandidateStatus.PROPOSED:
        reg.update_status(candidate_id, CandidateStatus.SHADOW_TESTING)
        reg.update_status(candidate_id, CandidateStatus.READY_FOR_REVIEW)
    return reg


class TestAcceptHappyPath:
    def test_accept_records_decision_and_transitions(self, tmp_path):
        reg_dir, dec_dir = _seed(tmp_path)
        _to_review(reg_dir, "OPT-4B-001")
        result = record_human_decision(
            "OPT-4B-001", "ACCEPT", actor="researcher-1",
            reason="Shadow evidence confirms +0.25R improvement",
            registry_dir=reg_dir, decisions_dir=dec_dir,
        )
        assert result.ok and not result.duplicate
        d = result.decision
        assert d.candidate_id == "OPT-4B-001"
        assert d.decision == "ACCEPT"
        assert d.actor == "researcher-1"
        assert d.reason
        assert d.timestamp
        assert d.evaluation_id == "EVAL-4b001"
        assert d.status_before == "READY_FOR_REVIEW"
        assert d.status_after == "ACCEPTED"
        reg = CandidateRegistry(storage_dir=reg_dir)
        c = reg.get("OPT-4B-001")
        assert c.status == CandidateStatus.ACCEPTED
        assert c.status_history[-1]["status"] == "ACCEPTED"
        assert len(_decisions_in(dec_dir)) == 1


class TestRejectHappyPath:
    def test_reject_without_positive_evidence(self, tmp_path):
        reg_dir, dec_dir = _seed(tmp_path, candidate_id="OPT-4B-R",
                                 with_success=False)
        _to_review(reg_dir, "OPT-4B-R")
        result = record_human_decision(
            "OPT-4B-R", "REJECT", actor="researcher-1",
            reason="OOS inconsistent; do not promote",
            registry_dir=reg_dir, decisions_dir=dec_dir,
        )
        assert result.ok
        assert result.decision.status_after == "REJECTED"
        got = CandidateRegistry(storage_dir=reg_dir).get("OPT-4B-R")
        assert got.status == CandidateStatus.REJECTED
        assert len(_decisions_in(dec_dir)) == 1


class TestWrongStartingState:
    def test_accept_from_shadow_testing_fails_closed(self, tmp_path):
        reg_dir = str(tmp_path / "reg")
        dec_dir = str(tmp_path / "dec")
        reg = CandidateRegistry(storage_dir=reg_dir)
        c, _, _ = _make_candidate("OPT-4B-S", CandidateStatus.SHADOW_TESTING)
        reg.create(c)
        reg.add_validation_result("OPT-4B-S", validation_id="EVAL-x",
                                  decision="IMPROVED", confidence="HIGH",
                                  sample_size=60, expectancy_delta=0.2)
        with pytest.raises(ValueError, match="READY_FOR_REVIEW"):
            record_human_decision("OPT-4B-S", "ACCEPT", actor="a", reason="r",
                                  registry_dir=reg_dir, decisions_dir=dec_dir)
        got = CandidateRegistry(storage_dir=reg_dir).get("OPT-4B-S")
        assert got.status == CandidateStatus.SHADOW_TESTING
        assert _decisions_in(dec_dir) == []

    def test_second_accept_after_accepted_conflicts(self, tmp_path):
        reg_dir, dec_dir = _seed(tmp_path, candidate_id="OPT-4B-T")
        _to_review(reg_dir, "OPT-4B-T")
        record_human_decision("OPT-4B-T", "ACCEPT", actor="a", reason="r",
                              registry_dir=reg_dir, decisions_dir=dec_dir)
        with pytest.raises((ValueError, RuntimeError)):
            record_human_decision("OPT-4B-T", "ACCEPT", actor="a",
                                  reason="different reason",
                                  registry_dir=reg_dir, decisions_dir=dec_dir)
        got = CandidateRegistry(storage_dir=reg_dir).get("OPT-4B-T")
        assert got.status == CandidateStatus.ACCEPTED
        assert len(_decisions_in(dec_dir)) == 1


class TestActorReason:
    def test_missing_actor_fails(self, tmp_path):
        reg_dir, dec_dir = _seed(tmp_path, candidate_id="OPT-4B-A")
        _to_review(reg_dir, "OPT-4B-A")
        with pytest.raises(ValueError, match="actor"):
            record_human_decision("OPT-4B-A", "ACCEPT", actor="",
                                  reason="has reason",
                                  registry_dir=reg_dir, decisions_dir=dec_dir)
        got = CandidateRegistry(storage_dir=reg_dir).get("OPT-4B-A")
        assert got.status == CandidateStatus.READY_FOR_REVIEW
        assert _decisions_in(dec_dir) == []

    def test_missing_reason_fails(self, tmp_path):
        reg_dir, dec_dir = _seed(tmp_path, candidate_id="OPT-4B-B")
        _to_review(reg_dir, "OPT-4B-B")
        with pytest.raises(ValueError, match="reason"):
            record_human_decision("OPT-4B-B", "REJECT", actor="human",
                                  reason="  ",
                                  registry_dir=reg_dir, decisions_dir=dec_dir)
        got = CandidateRegistry(storage_dir=reg_dir).get("OPT-4B-B")
        assert got.status == CandidateStatus.READY_FOR_REVIEW
        assert _decisions_in(dec_dir) == []


class TestAcceptRequiresEvidence:
    def test_accept_without_improved_evidence_rejected(self, tmp_path):
        reg_dir, dec_dir = _seed(tmp_path, candidate_id="OPT-4B-E",
                                 with_success=False)
        _to_review(reg_dir, "OPT-4B-E")
        with pytest.raises(ValueError, match="no successful"):
            record_human_decision("OPT-4B-E", "ACCEPT", actor="a", reason="r",
                                  registry_dir=reg_dir, decisions_dir=dec_dir)
        got = CandidateRegistry(storage_dir=reg_dir).get("OPT-4B-E")
        assert got.status == CandidateStatus.READY_FOR_REVIEW
        assert _decisions_in(dec_dir) == []

    def test_accept_with_only_inconclusive_rejected(self, tmp_path):
        reg_dir = str(tmp_path / "reg")
        dec_dir = str(tmp_path / "dec")
        reg = CandidateRegistry(storage_dir=reg_dir)
        c, _, _ = _make_candidate("OPT-4B-I")
        reg.create(c)
        reg.add_validation_result("OPT-4B-I", validation_id="EVAL-inc",
                                  decision="INCONCLUSIVE", confidence="LOW",
                                  sample_size=10, expectancy_delta=0.0)
        _to_review(reg_dir, "OPT-4B-I")
        with pytest.raises(ValueError, match="no successful"):
            record_human_decision("OPT-4B-I", "ACCEPT", actor="a", reason="r",
                                  registry_dir=reg_dir, decisions_dir=dec_dir)
        assert _decisions_in(dec_dir) == []


class TestIdempotency:
    def test_identical_replay_is_duplicate(self, tmp_path):
        reg_dir, dec_dir = _seed(tmp_path, candidate_id="OPT-4B-D")
        _to_review(reg_dir, "OPT-4B-D")
        r1 = record_human_decision("OPT-4B-D", "ACCEPT", actor="a", reason="r",
                                   registry_dir=reg_dir, decisions_dir=dec_dir)
        r2 = record_human_decision("OPT-4B-D", "ACCEPT", actor="a", reason="r",
                                   registry_dir=reg_dir, decisions_dir=dec_dir)
        assert r1.ok and not r1.duplicate
        assert r2.ok and r2.duplicate
        assert len(_decisions_in(dec_dir)) == 1
        got = CandidateRegistry(storage_dir=reg_dir).get("OPT-4B-D")
        assert got.status == CandidateStatus.ACCEPTED

    def test_conflicting_decision_fails_closed(self, tmp_path):
        reg_dir, dec_dir = _seed(tmp_path, candidate_id="OPT-4B-C")
        _to_review(reg_dir, "OPT-4B-C")
        record_human_decision("OPT-4B-C", "ACCEPT", actor="a", reason="r",
                              registry_dir=reg_dir, decisions_dir=dec_dir)
        with pytest.raises(ValueError, match="already has COMPLETED"):
            record_human_decision("OPT-4B-C", "REJECT", actor="a", reason="r",
                                  registry_dir=reg_dir, decisions_dir=dec_dir)
        got = CandidateRegistry(storage_dir=reg_dir).get("OPT-4B-C")
        assert got.status == CandidateStatus.ACCEPTED
        stored = get_human_decision("OPT-4B-C", decisions_dir=dec_dir)
        assert stored is not None and stored.decision == "ACCEPT"
        assert stored.reason == "r"
        assert len(_decisions_in(dec_dir)) == 1


class TestRestartDurability:
    def test_decision_readable_after_reinstantiation(self, tmp_path):
        reg_dir, dec_dir = _seed(tmp_path, candidate_id="OPT-4B-P")
        _to_review(reg_dir, "OPT-4B-P")
        record_human_decision("OPT-4B-P", "ACCEPT", actor="reviewer-7",
                              reason="meets all gates",
                              registry_dir=reg_dir, decisions_dir=dec_dir)
        fresh = CandidateDecisionStore(decisions_dir=dec_dir)
        d = fresh.get_decision("OPT-4B-P")
        assert d is not None
        assert d.actor == "reviewer-7"
        assert d.reason == "meets all gates"
        assert d.timestamp
        assert d.evaluation_id == "EVAL-4b001"
        assert d.status_before == "READY_FOR_REVIEW"
        assert d.status_after == "ACCEPTED"
        assert get_human_decision("OPT-4B-P",
                                  decisions_dir=dec_dir) is not None


class TestAutomationCannotApprove:
    def test_bridge_never_invokes_human_decision(self):
        import research_engine.lifecycle.candidate_evaluation_bridge as bridge

        src = inspect.getsource(bridge)
        assert "record_human_decision" not in src
        assert "candidate_decision" not in src
        assert "ACCEPTED" not in src

    def test_bridge_stops_at_ready_for_review(self, tmp_path):
        from test_e2e_candidate_lifecycle import _create_paired_populations
        from research_engine.lifecycle.candidate_evaluation_bridge import (
            evaluate_candidate,
        )

        reg_dir = str(tmp_path / "reg")
        reg = CandidateRegistry(storage_dir=reg_dir)
        c, _, _ = _make_candidate("OPT-4B-BR", CandidateStatus.SHADOW_TESTING)
        c.created_at = "2020-01-01T00:00:00+00:00"
        reg.create(c)
        from test_e2e_candidate_lifecycle import _create_paired_populations
        cand, inc = _create_paired_populations("OPT-4B-BR", 60,
                                               candidate_better=True)
        evaluation = evaluate_candidate(
            "OPT-4B-BR", candidate_records=cand, incumbent_records=inc,
            registry_dir=reg_dir,
        )
        assert evaluation.decision == "VALIDATED"
        final = CandidateRegistry(storage_dir=reg_dir).get("OPT-4B-BR")
        assert final.status == CandidateStatus.READY_FOR_REVIEW
        assert final.status != CandidateStatus.ACCEPTED




