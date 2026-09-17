"""
Wave 4E.2 — Governed recommendation -> human decision: focused proof tests.

Real path under test: record_human_decision() (Wave 4B governance,
research_engine/v10/candidates/candidate_decision.py) — the ONLY place
READY_FOR_REVIEW crosses to ACCEPTED on an explicit human action. Wave 4E.2
requires every human decision to bind to ONE canonical CandidateRecommendation
(Wave 4E.1) and to fail closed when that provenance is missing, inconsistent,
non-actionable or stale.

Proven chain under test:
    CandidateEvaluation -> CandidateRecommendation -> explicit human decision
    -> HumanDecision (durable) -> CandidateRegistry status transition

Isolation: every test uses tmp_path for the candidate registry, the decision
store, the recommendation store and the baseline authority. Production
data/research/** is never touched, and no application/deployment state exists
to be written.
"""

import inspect
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_engine.v10.baselines.baseline_authority as baseline_authority
from research_engine.v10.baselines.baseline_authority import get_active, set_active
from research_engine.v10.baselines.models import BaselineSnapshot
from research_engine.v10.baselines.snapshot_registry import SnapshotRegistry
from core.research_events import compute_config_hash
from research_engine.lifecycle.candidate_evaluator import CandidateEvaluation
from research_engine.lifecycle.candidate_recommendation import (
    CandidateRecommendation,
    RecommendationStore,
    create_recommendation,
)
from research_engine.v10.candidates.candidate_decision import (
    CandidateDecisionStore,
    get_human_decision,
    record_human_decision,
)
from research_engine.v10.candidates.candidate_registry import CandidateRegistry
from research_engine.v10.candidates.models import CandidateRecord, CandidateStatus

# ─── canonical identities used by the fixtures ────────────────────────────────
_BASELINE_ID = "V10_BASELINE_wave4e2test"
_TREATMENT_ID = "e2a11d0c4beef042"
_OTHER_TREATMENT_ID = "9f3c02be7711ad44"
_CAND = "OPT-4E2-001"
_EVAL = "EVAL-4e2001"
_REC = f"REC-{_EVAL}"


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES / HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _valid_baseline_env(tmp_path, monkeypatch):
    """Per-test tmp baseline authority — same convention as the 4C.2/4C.3 tests.

    Redirects the module-level store pointers on baseline_authority so the
    canonical Wave 4C authority is exercised for real while production
    data/baselines/ is never touched.
    """
    baselines_dir = str(tmp_path / "baselines")
    monkeypatch.setattr(baseline_authority, "_BASELINES_DIR", baselines_dir)
    monkeypatch.setattr(
        baseline_authority,
        "_ACTIVE_POINTER_FILE",
        str(Path(baselines_dir) / "active_baseline.json"),
    )
    SnapshotRegistry(baselines_dir=baselines_dir).save(BaselineSnapshot(
        snapshot_id=_BASELINE_ID,
        config_hash=compute_config_hash(),
        identity_hash="wave4e2test",
    ))
    set_active(_BASELINE_ID, actor="test", reason="4E.2 governance fixture")
    return baselines_dir


def _seed_candidate(
    tmp_path,
    *,
    candidate_id=_CAND,
    baseline_id=_BASELINE_ID,
    config_hash=None,
    evidence=((_EVAL, "IMPROVED"),),
    with_provenance=True,
    status=CandidateStatus.READY_FOR_REVIEW,
):
    """Seed a candidate (default READY_FOR_REVIEW) plus its evaluation evidence.

    Returns (reg_dir, dec_dir, rec_dir) — the three isolated stores.
    """
    reg_dir = str(tmp_path / "reg")
    dec_dir = str(tmp_path / "dec")
    rec_dir = str(tmp_path / "recs")
    reg = CandidateRegistry(storage_dir=reg_dir)
    change_definition = {"type": "direction_inversion"}
    if with_provenance:
        change_definition["baseline_config_hash"] = (
            config_hash if config_hash is not None else compute_config_hash()
        )
    reg.create(CandidateRecord(
        candidate_id=candidate_id,
        hypothesis_id="HYP-4e2-test",
        baseline_id=baseline_id,
        component="DIRECTION_INVERSION",
        description="Wave 4E.2 governed recommendation test candidate",
        change_definition=change_definition,
        status=status,
    ))
    for validation_id, decision in evidence:
        reg.add_validation_result(
            candidate_id,
            validation_id=validation_id,
            decision=decision,
            confidence="HIGH",
            sample_size=60,
            expectancy_delta=0.25,
        )
    return reg_dir, dec_dir, rec_dir


def _publish(
    rec_dir,
    *,
    candidate_id=_CAND,
    evaluation_id=_EVAL,
    treatment_id=_TREATMENT_ID,
    baseline_id=_BASELINE_ID,
    config_hash=None,
    decision="VALIDATED",
    confidence="HIGH",
    eligible_pairs=60,
    promotion_blocked=False,
    promotion_block_reason="",
):
    """Publish a canonical recommendation through the real 4E.1 factory path.

    Creates a production-shaped CandidateEvaluation and routes it through
    create_recommendation(), so the recommendation record under test carries
    exactly the provenance the 4E.1 authority would produce.
    """
    ev = CandidateEvaluation(
        evaluation_id=evaluation_id,
        candidate_id=candidate_id,
        timestamp="2026-09-17T12:00:00+00:00",
        prospective_boundary="1970-01-01T00:00:00+00:00",
        total_observations_raw=eligible_pairs * 2,
        eligible_pairs=eligible_pairs,
        n=eligible_pairs,
        mean_baseline_r=-0.3,
        mean_candidate_r=0.2,
        mean_delta_r=0.5,
        median_delta_r=0.5,
        total_baseline_r=-0.3 * eligible_pairs,
        total_candidate_r=0.2 * eligible_pairs,
        candidate_wins=int(eligible_pairs * 0.6),
        candidate_win_rate=0.6,
        ci_lower=0.2,
        ci_upper=0.8,
        permutation_p=0.001,
        oos_n=40,
        oos_delta_r=0.3,
        symbols_positive=3,
        symbols_total=3,
        periods_positive=3,
        periods_total=5,
        survives_outlier_removal=True,
        worst_delta_r=-0.1,
        risk_level="LOW",
        decision=decision,
        decision_reason="shadow evidence favours the candidate",
        confidence=confidence,
        baseline_id=baseline_id,
        config_hash=(
            config_hash if config_hash is not None else compute_config_hash()
        ),
        promotion_blocked=promotion_blocked,
        promotion_block_reason=promotion_block_reason,
        treatment_id=treatment_id,
    )
    return create_recommendation(
        ev, store=RecommendationStore(recommendations_dir=rec_dir)
    )


def _decide(candidate_id, verdict, recommendation_id, reg_dir, dec_dir, rec_dir,
            *, actor="human-4e2", reason="governed review"):
    """Invoke the real governance entry point with explicit store isolation."""
    return record_human_decision(
        candidate_id,
        verdict,
        recommendation_id,
        actor=actor,
        reason=reason,
        registry_dir=reg_dir,
        decisions_dir=dec_dir,
        recommendations_dir=rec_dir,
    )


def _rows(dec_dir):
    """Every persisted decision row, including non-effective blocked rows."""
    return CandidateDecisionStore(decisions_dir=dec_dir).list_all_rows()


def _blocked(dec_dir, outcome="RECOMMENDATION_BLOCKED"):
    return [d for d in _rows(dec_dir) if d.outcome == outcome]


def _status(reg_dir, candidate_id):
    return CandidateRegistry(storage_dir=reg_dir).get(candidate_id).status

# ═══════════════════════════════════════════════════════════════════════════════
# A. VALID ACTIONABLE RECOMMENDATION -> EXPLICIT HUMAN APPROVAL
# ══════════════════════════════════════════════════════════════════════════════

class TestValidApprovalBindsRecommendation:
    def test_approve_succeeds_and_transitions(self, tmp_path):
        """Proof 1 + 8: valid actionable recommendation + explicit APPROVE works."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        rec = _publish(rec_dir)
        assert rec is not None and rec.actionable

        result = _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)

        assert result.ok and not result.duplicate
        assert _status(reg_dir, _CAND) == CandidateStatus.ACCEPTED
        assert result.decision.status_before == "READY_FOR_REVIEW"
        assert result.decision.status_after == "ACCEPTED"
        assert result.decision.outcome == "COMPLETED"

    def test_governance_preserves_exact_provenance(self, tmp_path):
        """Proofs 2-7: every identity is preserved exactly into governance."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        rec = _publish(rec_dir)
        _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)

        stored = get_human_decision(_CAND, decisions_dir=dec_dir)
        assert stored is not None
        # 2. recommendation_id
        assert stored.recommendation_id == _REC
        # 3. evaluation_id
        assert stored.evaluation_id == _EVAL
        # 4. candidate_id
        assert stored.candidate_id == _CAND
        # 5. treatment_id — verbatim from the 4D.2 evaluation chain
        assert stored.treatment_id == _TREATMENT_ID
        # 6/7. baseline provenance
        assert stored.baseline_id == _BASELINE_ID
        assert stored.baseline_config_hash == compute_config_hash()
        # durable across reinstantiation
        fresh = CandidateDecisionStore(decisions_dir=dec_dir).get_decision(_CAND)
        assert fresh is not None
        assert fresh.recommendation_id == rec.recommendation_id == _REC
        assert fresh.treatment_id == _TREATMENT_ID
        assert fresh.baseline_id == _BASELINE_ID

    def test_governance_record_traces_to_exact_evaluation(self, tmp_path):
        """The durable chain is walkable: decision -> rec -> evaluation provenance."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        _publish(rec_dir)
        _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)

        stored = get_human_decision(_CAND, decisions_dir=dec_dir)
        rec = RecommendationStore(
            recommendations_dir=rec_dir
        ).get_by_recommendation_id(stored.recommendation_id)
        assert rec is not None
        assert rec.recommendation_id == stored.recommendation_id
        assert rec.evaluation_id == stored.evaluation_id
        assert rec.candidate_id == stored.candidate_id
        assert rec.treatment_id == stored.treatment_id
        assert rec.baseline_id == stored.baseline_id
        assert rec.baseline_config_hash == stored.baseline_config_hash


# ═══════════════════════════════════════════════════════════════════════════════
# B. RECOMMENDATION CREATION ALONE CANNOT APPROVE
# ═══════════════════════════════════════════════════════════════════════════════

class TestCreationAloneCannotApprove:
    def test_publishing_recommendation_does_not_accept(self, tmp_path):
        """Proof 9: 4E.1 recommendation creation is inert w.r.t. lifecycle."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        rec = _publish(rec_dir)
        assert rec.actionable is True

        assert _status(reg_dir, _CAND) == CandidateStatus.READY_FOR_REVIEW
        assert _rows(dec_dir) == []
        assert get_human_decision(_CAND, decisions_dir=dec_dir) is None

    def test_recommendation_module_has_no_governance_hooks(self):
        """Source-level: 4E.1 never invokes human governance or lifecycle.

        The module docstring explicitly FORBIDS these actions, so prose cannot
        be the proof — the proof must be that no executable statement in the
        module references governance/lifecycle APIs or lifecycle state names.
        The AST is scanned (docstrings excluded) so a real hook cannot hide
        behind documentation.
        """
        import ast

        import research_engine.lifecycle.candidate_recommendation as mod

        tree = ast.parse(inspect.getsource(mod))

        # Nodes that are docstrings, not executable string data.
        docstrings = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body:
                first = body[0]
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    docstrings.add(id(first.value))

        referenced: set[str] = set()
        literals: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                referenced.add(node.id)
            elif isinstance(node, ast.Attribute):
                referenced.add(node.attr)
            elif isinstance(node, ast.alias):
                referenced.add((node.asname or node.name).split(".")[-1])
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) not in docstrings:
                    literals.add(node.value)

        # No governance / lifecycle authority may be referenced by code.
        assert "record_human_decision" not in referenced
        assert "get_human_decision" not in referenced
        assert "CandidateRegistry" not in referenced
        assert "CandidateDecisionStore" not in referenced
        assert "update_status" not in referenced
        assert "HumanDecision" not in referenced
        # No executable literal may name a lifecycle terminal state.
        assert "ACCEPTED" not in literals
        assert "REJECTED" not in literals
        assert "COMPLETED" not in literals
# ═══════════════════════════════════════════════════════════════════════════════
# C. MISSING / UNKNOWN RECOMMENDATION FAILS CLOSED
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecommendationRequired:
    def test_blank_recommendation_id_fails_closed(self, tmp_path):
        """Proof 10: no recommendation identity -> nothing written, nothing changed."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        with pytest.raises(ValueError, match="recommendation"):
            _decide(_CAND, "ACCEPT", "", reg_dir, dec_dir, rec_dir)
        assert _rows(dec_dir) == []
        assert _status(reg_dir, _CAND) == CandidateStatus.READY_FOR_REVIEW

    def test_whitespace_recommendation_id_fails_closed(self, tmp_path):
        """Proof 10: whitespace is not an identity either."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        with pytest.raises(ValueError, match="recommendation"):
            _decide(_CAND, "ACCEPT", "   ", reg_dir, dec_dir, rec_dir)
        assert _rows(dec_dir) == []
        assert _status(reg_dir, _CAND) == CandidateStatus.READY_FOR_REVIEW

    def test_unknown_recommendation_id_fails_closed(self, tmp_path):
        """Proof 11: a recommendation_id that does not resolve cannot approve."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        with pytest.raises(ValueError, match="not found"):
            _decide(_CAND, "ACCEPT", "REC-DOES-NOT-EXIST", reg_dir, dec_dir, rec_dir)
        # Auditable blocked attempt, never an effective decision.
        assert get_human_decision(_CAND, decisions_dir=dec_dir) is None
        assert _status(reg_dir, _CAND) == CandidateStatus.READY_FOR_REVIEW
        blocked = _blocked(dec_dir)
        assert len(blocked) == 1
        assert blocked[0].decision == "ACCEPT"
        assert blocked[0].outcome == "RECOMMENDATION_BLOCKED"
        assert blocked[0].recommendation_id == "REC-DOES-NOT-EXIST"

    def test_reject_also_requires_recommendation(self, tmp_path):
        """Proof 10: binding is required for negative decisions too (identity,
        not eligibility) — REJECT cannot proceed without an exact recommendation.
        """
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        with pytest.raises(ValueError, match="recommendation"):
            _decide(_CAND, "REJECT", "", reg_dir, dec_dir, rec_dir)
        assert _rows(dec_dir) == []
        assert _status(reg_dir, _CAND) == CandidateStatus.READY_FOR_REVIEW
# ═══════════════════════════════════════════════════════════════════════════════
# D. PROVENANCE MISMATCH FAILS CLOSED (no silent substitution)
# ═══════════════════════════════════════════════════════════════════════════════

class TestProvenanceMismatchBlocked:
    def test_recommendation_candidate_mismatch_blocks(self, tmp_path):
        """Proof 12: another candidate's recommendation cannot be substituted."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        _publish(rec_dir, candidate_id="OPT-4E2-OTHER")
        with pytest.raises(ValueError, match="candidate_id mismatch"):
            _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)
        assert get_human_decision(_CAND, decisions_dir=dec_dir) is None
        assert _status(reg_dir, _CAND) == CandidateStatus.READY_FOR_REVIEW

    def test_recommendation_evaluation_mismatch_blocks(self, tmp_path):
        """Proof 13: a recommendation from an unrelated evaluation cannot approve."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        _publish(rec_dir, evaluation_id="EVAL-unrelated0001")
        with pytest.raises(ValueError, match="evaluation_id mismatch"):
            _decide(_CAND, "ACCEPT", "REC-EVAL-unrelated0001",
                    reg_dir, dec_dir, rec_dir)
        assert get_human_decision(_CAND, decisions_dir=dec_dir) is None
        assert _status(reg_dir, _CAND) == CandidateStatus.READY_FOR_REVIEW

    def test_missing_treatment_identity_blocks(self, tmp_path):
        """Proof 14: an evaluation with no treatment identity cannot be approved."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        rec = _publish(rec_dir, treatment_id="")
        assert rec is not None and rec.treatment_id == ""
        with pytest.raises(ValueError, match="no treatment_id"):
            _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)
        assert get_human_decision(_CAND, decisions_dir=dec_dir) is None
        assert _status(reg_dir, _CAND) == CandidateStatus.READY_FOR_REVIEW

    def test_mixed_treatment_evidence_blocks(self, tmp_path):
        """Proof 14: conflicting treatment identities for one evaluation are
        unattributable — even when every row looks actionable on its own.

        Written directly to the canonical JSONL (bypassing append(), which
        dedups by evaluation_id) to model a corrupted/legacy recommendation
        store.
        """
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        canonical = _publish(rec_dir, treatment_id=_TREATMENT_ID)
        conflicting = CandidateRecommendation.from_dict(canonical.to_dict())
        conflicting.treatment_id = _OTHER_TREATMENT_ID
        path = Path(rec_dir) / "recommendations.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(conflicting.to_dict()) + "\n")
        assert len(RecommendationStore(recommendations_dir=rec_dir).list_all()) == 2

        with pytest.raises(ValueError, match="mixed treatment evidence"):
            _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)
        assert get_human_decision(_CAND, decisions_dir=dec_dir) is None
        assert _status(reg_dir, _CAND) == CandidateStatus.READY_FOR_REVIEW

    def test_recommendation_baseline_mismatch_blocks(self, tmp_path):
        """Proof 15: a recommendation bound to another baseline cannot approve."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        _publish(rec_dir, baseline_id="V10_BASELINE_somewhereelse")
        with pytest.raises(ValueError, match="baseline_id mismatch"):
            _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)
        assert get_human_decision(_CAND, decisions_dir=dec_dir) is None
        assert _status(reg_dir, _CAND) == CandidateStatus.READY_FOR_REVIEW

    def test_recommendation_config_hash_mismatch_blocks(self, tmp_path):
        """Proof 15: baseline config provenance must match the Wave 4C contract."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        _publish(rec_dir, config_hash="deadbeefdeadbeef")
        with pytest.raises(ValueError, match="baseline_config_hash mismatch"):
            _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)
        assert get_human_decision(_CAND, decisions_dir=dec_dir) is None
        assert _status(reg_dir, _CAND) == CandidateStatus.READY_FOR_REVIEW
# ═══════════════════════════════════════════════════════════════════════════════
# E. STALE BASELINE FAILS CLOSED (existing Wave 4C authority, not a new check)
# ═══════════════════════════════════════════════════════════════════════════════

class TestStaleBaselineFailsClosed:
    def test_stale_baseline_accept_blocked_by_4c_authority(self, tmp_path):
        """Proof 16: a candidate bound to a no-longer-active baseline cannot be
        approved, and the block comes from the Wave 4C.3 authority rather than a
        second validator. The recommendation itself stays perfectly valid.
        """
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        rec = _publish(rec_dir)
        assert rec.actionable is True

        # Move the canonical active baseline elsewhere (4C.1 authority only).
        other = "V10_BASELINE_wave4e2successor"
        SnapshotRegistry(
            baselines_dir=str(tmp_path / "baselines")
        ).save(BaselineSnapshot(
            snapshot_id=other,
            config_hash=compute_config_hash(),
            identity_hash="wave4e2successor",
        ))
        set_active(other, actor="test", reason="4E.2 staleness proof")
        assert get_active().active_baseline_id == other

        with pytest.raises(ValueError, match="baseline safety invariant"):
            _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)

        # No effective decision, no lifecycle change, no fabricated verdict.
        assert get_human_decision(_CAND, decisions_dir=dec_dir) is None
        assert _status(reg_dir, _CAND) == CandidateStatus.READY_FOR_REVIEW
        blocked = _blocked(dec_dir, outcome="BASELINE_BLOCKED")
        assert len(blocked) == 1
        assert blocked[0].decision == "ACCEPT"
        assert "stale_baseline" in blocked[0].error
        assert blocked[0].recommendation_id == _REC

    def test_stale_baseline_reject_still_allowed(self, tmp_path):
        """Negative human decisions are not over-gated: rejecting a stale
        candidate stays possible, and it still binds the exact recommendation.
        """
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        _publish(rec_dir)
        other = "V10_BASELINE_wave4e2successor"
        SnapshotRegistry(
            baselines_dir=str(tmp_path / "baselines")
        ).save(BaselineSnapshot(
            snapshot_id=other,
            config_hash=compute_config_hash(),
            identity_hash="wave4e2successor",
        ))
        set_active(other, actor="test", reason="4E.2 staleness proof")

        result = _decide(_CAND, "REJECT", _REC, reg_dir, dec_dir, rec_dir)
        assert result.ok
        assert _status(reg_dir, _CAND) == CandidateStatus.REJECTED
        assert result.decision.recommendation_id == _REC
        assert result.decision.baseline_id == _BASELINE_ID


# ═══════════════════════════════════════════════════════════════════════════════
# F. NON-ACTIONABLE RECOMMENDATIONS CANNOT BE HUMAN-APPROVED
# ═══════════════════════════════════════════════════════════════════════════════

class TestNonActionableCannotBeApproved:
    def test_promotion_blocked_recommendation_cannot_be_approved(self, tmp_path):
        """Proof 18: promotion_blocked evidence is preserved, never approved."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        rec = _publish(
            rec_dir,
            promotion_blocked=True,
            promotion_block_reason="stale_baseline: candidate baseline != active",
        )
        assert rec.promotion_blocked is True
        assert rec.actionable is False

        with pytest.raises(ValueError, match="not actionable"):
            _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)

        assert _status(reg_dir, _CAND) == CandidateStatus.READY_FOR_REVIEW
        assert get_human_decision(_CAND, decisions_dir=dec_dir) is None
        # The blocked row still records the true scientific reason.
        stored_rec = RecommendationStore(
            recommendations_dir=rec_dir
        ).get_by_recommendation_id(_REC)
        assert stored_rec.promotion_block_reason.startswith("stale_baseline")

    def test_inconclusive_recommendation_cannot_be_approved(self, tmp_path):
        """Proof 19: INCONCLUSIVE research knowledge stays non-actionable."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        rec = _publish(rec_dir, decision="INCONCLUSIVE", confidence="LOW")
        assert rec.decision == "INCONCLUSIVE"
        assert rec.actionable is False

        with pytest.raises(ValueError, match="not actionable"):
            _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)
        assert _status(reg_dir, _CAND) == CandidateStatus.READY_FOR_REVIEW

    def test_insufficient_evidence_recommendation_cannot_be_approved(self, tmp_path):
        """Proof 19: insufficient confidence / small sample is not optimisable."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        rec = _publish(rec_dir, eligible_pairs=5, confidence="INSUFFICIENT")
        assert rec.actionable is False
        assert any("small_sample" in lim for lim in rec.limitations)

        with pytest.raises(ValueError, match="not actionable"):
            _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)
        assert _status(reg_dir, _CAND) == CandidateStatus.READY_FOR_REVIEW

# ══════════════════════════════════════════════════════════════════════════════
# G. BLOCKED APPROVAL: AUDIT-ONLY, NO FAKE REJECT, NO LIFECYCLE CHANGE
# ═══════════════════════════════════════════════════════════════════════════════

class TestBlockedApprovalIsAuditOnly:
    def test_blocked_approval_creates_no_fake_reject(self, tmp_path):
        """Proof 20: a failed APPROVE is never rewritten into a REJECT."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        _publish(rec_dir, promotion_blocked=True,
                 promotion_block_reason="stale_baseline")
        with pytest.raises(ValueError):
            _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)

        rows = _rows(dec_dir)
        assert rows, "a blocked attempt must remain auditable"
        assert all(r.outcome == "RECOMMENDATION_BLOCKED" for r in rows)
        assert not any(r.decision == "REJECT" for r in rows)
        assert not any(r.status_after == "REJECTED" for r in rows)
        assert get_human_decision(_CAND, decisions_dir=dec_dir) is None

    def test_blocked_approval_does_not_change_lifecycle(self, tmp_path):
        """Proof 21: candidate stays READY_FOR_REVIEW after a blocked APPROVE."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        _publish(rec_dir, decision="INCONCLUSIVE", confidence="LOW")
        with pytest.raises(ValueError):
            _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)

        reg = CandidateRegistry(storage_dir=reg_dir)
        cand = reg.get(_CAND)
        assert cand.status == CandidateStatus.READY_FOR_REVIEW
        assert all(s["status"] != "ACCEPTED" for s in cand.status_history)
        # A later valid retry remains possible (blocked rows are non-effective).
        assert get_human_decision(_CAND, decisions_dir=dec_dir) is None


# ══════════════════════════════════════════════════════════════════════════════
# H. REJECT IS EXPLICIT AND RECOMMENDATION-BOUND; DEFER UNSUPPORTED
# ═══════════════════════════════════════════════════════════════════════════════

class TestRejectAndDefer:
    def test_reject_binds_exact_recommendation_provenance(self, tmp_path):
        """Proof 22: REJECT records the exact recommendation/evaluation chain."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        _publish(rec_dir)
        result = _decide(_CAND, "REJECT", _REC, reg_dir, dec_dir, rec_dir)
        assert result.ok and not result.duplicate
        d = result.decision
        assert d.decision == "REJECT"
        assert d.recommendation_id == _REC
        assert d.evaluation_id == _EVAL
        assert d.treatment_id == _TREATMENT_ID
        assert d.baseline_id == _BASELINE_ID
        assert d.baseline_config_hash == compute_config_hash()
        assert d.status_after == "REJECTED"
        assert _status(reg_dir, _CAND) == CandidateStatus.REJECTED

    def test_reject_with_unrelated_evaluation_blocks(self, tmp_path):
        """Proof 22: REJECT still requires the recommendation to belong to the
        candidate's own recorded evidence (no silent substitution).
        """
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        _publish(rec_dir, evaluation_id="EVAL-unrelated0002")
        with pytest.raises(ValueError, match="not part of candidate"):
            _decide(_CAND, "REJECT", "REC-EVAL-unrelated0002",
                    reg_dir, dec_dir, rec_dir)
        assert get_human_decision(_CAND, decisions_dir=dec_dir) is None
        assert _status(reg_dir, _CAND) == CandidateStatus.READY_FOR_REVIEW

    def test_defer_is_not_supported_by_candidate_governance(self, tmp_path):
        """Proof 23 (reported, not invented): the candidate governance
        vocabulary is ACCEPT|REJECT only. DEFER must be rejected outright and
        must never silently ACCEPT or write an effective decision.
        """
        from research_engine.v10.candidates.candidate_decision import (
            _VALID_DECISIONS,
        )
        assert _VALID_DECISIONS == ("ACCEPT", "REJECT")

        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        _publish(rec_dir)
        with pytest.raises(ValueError, match="Invalid decision"):
            _decide(_CAND, "DEFER", _REC, reg_dir, dec_dir, rec_dir)
        assert _rows(dec_dir) == []
        assert _status(reg_dir, _CAND) == CandidateStatus.READY_FOR_REVIEW
    def test_non_actionable_can_still_be_rejected(self, tmp_path):
        """Proof 22: REJECT is not gated on actionability (identity only)."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        _publish(rec_dir, decision="INCONCLUSIVE", confidence="LOW")
        result = _decide(_CAND, "REJECT", _REC, reg_dir, dec_dir, rec_dir)
        assert result.ok
        assert _status(reg_dir, _CAND) == CandidateStatus.REJECTED
        assert result.decision.recommendation_id == _REC


# ══════════════════════════════════════════════════════════════════════════════
# I. IDEMPOTENCY / IDENTITY DETERMINISM
# ══════════════════════════════════════════════════════════════════════════════

class TestIdempotentIdentity:
    def test_identical_replay_is_duplicate_not_contradiction(self, tmp_path):
        """Proof 24: replaying the same governed decision is inert.

        Two identical ACCEPT calls produce exactly ONE durable row and ONE
        lifecycle transition: the second call is reported as a duplicate, never
        as a contradictory second approval.
        """
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        _publish(rec_dir)

        first = _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)
        second = _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)

        assert first.ok and not first.duplicate
        assert second.ok and second.duplicate
        assert first.decision.recommendation_id == _REC
        assert second.decision.recommendation_id == _REC
        assert len(_rows(dec_dir)) == 1
        assert _status(reg_dir, _CAND) == CandidateStatus.ACCEPTED

    def test_replay_cannot_substitute_a_different_recommendation(self, tmp_path):
        """Proof 24: a second APPROVE citing a DIFFERENT recommendation fails
        closed — the durable approval stays bound to the original identity.
        """
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        _publish(rec_dir)
        _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)

        # A second, materially different recommendation for the same candidate
        # (different treatment identity -> different evaluation fingerprint).
        other_eval = "EVAL-4e2alt002"
        other_rec = _publish(
            rec_dir,
            evaluation_id=other_eval,
            treatment_id=_OTHER_TREATMENT_ID,
        )
        assert other_rec is not None
        assert other_rec.recommendation_id == f"REC-{other_eval}" != _REC

        with pytest.raises(ValueError):
            _decide(_CAND, "ACCEPT", f"REC-{other_eval}",
                    reg_dir, dec_dir, rec_dir)

        # The original effective approval is untouched and singular.
        stored = get_human_decision(_CAND, decisions_dir=dec_dir)
        assert stored is not None
        assert stored.recommendation_id == _REC
        assert stored.treatment_id == _TREATMENT_ID
        completed = [r for r in _rows(dec_dir) if r.outcome == "COMPLETED"]
        assert len(completed) == 1

    def test_repeated_evaluation_publishing_is_idempotent(self, tmp_path):
        """Proof 24: the 4E.1 store dedups by evaluation_id, so re-processing
        the same logical evaluation cannot mint a second recommendation record
        with contradictory identity semantics.
        """
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        first = _publish(rec_dir)
        second = _publish(rec_dir)   # identical evaluation re-processed

        assert first.recommendation_id == second.recommendation_id == _REC
        path = Path(rec_dir) / "recommendations.jsonl"
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
                 if ln.strip()]
        assert len(lines) == 1, "same evaluation must persist exactly once"

    def test_malformed_recommendation_row_does_not_corrupt_truth(self, tmp_path):
        """Proof 28: one malformed persisted row cannot hide or corrupt the
        valid recommendation truth.
        """
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        _publish(rec_dir)

        path = Path(rec_dir) / "recommendations.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write("{not valid json at all\n")
            fh.write("\n")

        store = RecommendationStore(recommendations_dir=rec_dir)
        assert store.get_by_recommendation_id(_REC) is not None
        assert len(store.list_all()) == 1

        # A valid approval still succeeds after the malformed row.
        result = _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)
        assert result.ok
        assert _status(reg_dir, _CAND) == CandidateStatus.ACCEPTED

# ══════════════════════════════════════════════════════════════════════════════
# J. NO APPLICATION / DEPLOYMENT, NO BASELINE OR CONFIG MUTATION
# ══════════════════════════════════════════════════════════════════════════════

class TestNoDownstreamSideEffects:
    def test_governance_source_has_no_application_or_deployment_hooks(self):
        """Proof 25: the governance authority never references application,
        deployment, rollback or production-apply surfaces.

        Prose comments cannot prove this, so the executable statement names are
        scanned from the AST (docstrings excluded).
        """
        import ast

        from research_engine.v10.candidates import candidate_decision

        src = Path(inspect.getsourcefile(candidate_decision)).read_text(
            encoding="utf-8"
        )
        tree = ast.parse(src)
        forbidden = (
            "application", "applications", "application_record",
            "deploy", "deployment", "rollback", "activate_baseline",
            "set_active", "apply_candidate", "mt5", "broker",
        )
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id.lower())
            elif isinstance(node, ast.Attribute):
                names.add(node.attr.lower())
            elif isinstance(node, ast.alias):
                names.add((node.asname or node.name).lower().split(".")[-1])
        leaked = sorted(n for n in names if n in forbidden)
        assert leaked == [], f"governance references downstream surfaces: {leaked}"

    def test_approval_creates_no_application_or_deployment_artifact(self, tmp_path):
        """Proof 25: a successful approval writes only governance truth.

        Nothing resembling an application/deployment/rollback record appears
        anywhere under the isolated workspace.
        """
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        _publish(rec_dir)
        _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)

        suspicious = ("application", "deploy", "rollback", "execution")
        offenders = [
            str(p.relative_to(tmp_path))
            for p in Path(tmp_path).rglob("*")
            if any(s in str(p.relative_to(tmp_path)).lower() for s in suspicious)
        ]
        assert offenders == [], f"unexpected downstream artifacts: {offenders}"

    def test_active_baseline_untouched_by_approval(self, tmp_path):
        """Proof 26: the canonical active baseline is never modified."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        _publish(rec_dir)
        before = get_active()

        _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)

        after = get_active()
        assert after is not None and before is not None
        assert after.active_baseline_id == before.active_baseline_id == _BASELINE_ID
        # Nothing was re-activated: the pointer metadata is byte-identical.
        assert after.activated_at == before.activated_at
        assert after.actor == before.actor
        assert after.reason == before.reason

    def test_candidate_configuration_not_mutated_by_approval(self, tmp_path):
        """Proof 27: the candidate's change_definition is never rewritten."""
        reg_dir, dec_dir, rec_dir = _seed_candidate(tmp_path)
        _publish(rec_dir)
        before = CandidateRegistry(storage_dir=reg_dir).get(_CAND)
        before_def = json.dumps(before.change_definition, sort_keys=True)
        before_baseline = before.baseline_id

        _decide(_CAND, "ACCEPT", _REC, reg_dir, dec_dir, rec_dir)

        after = CandidateRegistry(storage_dir=reg_dir).get(_CAND)
        assert json.dumps(after.change_definition, sort_keys=True) == before_def
        assert after.baseline_id == before_baseline == _BASELINE_ID
        assert after.status == CandidateStatus.ACCEPTED
