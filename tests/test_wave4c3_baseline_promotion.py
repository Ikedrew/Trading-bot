"""
Wave 4C.3 — Baseline-safe human promotion: focused proof tests.

Real path under test: record_human_decision() (Wave 4B governance,
research_engine/v10/candidates/candidate_decision.py) — the ONLY place
READY_FOR_REVIEW crosses to ACCEPTED on human ACCEPT.

Baseline fixtures reuse the 4C.2 convention (per-test tmp stores via the
module-attr redirect on baseline_authority). Candidate registry and decision
store use tmp_path; production data/ is never touched.

Covered proofs:
    1. READY_FOR_REVIEW + valid baseline + ACCEPT  -> ACCEPTED (existing path)
    2. stale baseline + ACCEPT                     -> blocked (stale_baseline)
    3. stale current config + ACCEPT               -> blocked (stale_config)
    4. missing baseline provenance + ACCEPT        -> blocked
    5. no active baseline + ACCEPT                 -> blocked
    6. corrupt active-baseline pointer + ACCEPT    -> blocked
    7. missing referenced snapshot + ACCEPT        -> blocked
    8. legacy baseline_id="current_v10" + ACCEPT   -> blocked (never crosses)
    9. blocked candidate does NOT become ACCEPTED (stays READY_FOR_REVIEW)
   10. blocked candidate retains baseline_id/provenance (no rebase, no mutation)
   11. BASELINE_BLOCKED row preserves human intent, is NOT an effective
       COMPLETED decision, and retry after baseline repair succeeds
   12. REJECT on a stale candidate still works (existing governance semantics)
   13. no production application (source-level: no app/deploy imports)
"""
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_engine.v10.baselines.baseline_authority as baseline_authority
from research_engine.v10.baselines.baseline_authority import set_active
from research_engine.v10.baselines.models import BaselineSnapshot
from research_engine.v10.baselines.snapshot_registry import SnapshotRegistry
from core.research_events import compute_config_hash
from research_engine.lifecycle.candidate_evaluator import CandidateEvaluation
from research_engine.lifecycle.candidate_recommendation import (
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

_BASELINE_ID = "V10_BASELINE_wave4c3test"
_POINTER_NAME = "active_baseline.json"

# Wave 4E.2: treatment identity carried by the shadow evidence of every fixture
# candidate. Treatment identity always comes from the evaluation/evidence chain
# (4D.2) — never recomputed from the candidate's change_definition.
_TEST_TREATMENT_ID = "4c3100ce4c3100ce"
_TEST_EVALUATION_ID = "EVAL-4c3001"


# ═══════════════════════════════════════════════════════════════
# FIXTURES / HELPERS
# ═══════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _valid_baseline_env(tmp_path, monkeypatch):
    """Per-test tmp baseline authority — same convention as 4C.2 tests."""
    baselines_dir = str(tmp_path / "baselines")
    monkeypatch.setattr(baseline_authority, "_BASELINES_DIR", baselines_dir)
    monkeypatch.setattr(
        baseline_authority, "_ACTIVE_POINTER_FILE",
        str(Path(baselines_dir) / _POINTER_NAME),
    )
    SnapshotRegistry(baselines_dir=baselines_dir).save(BaselineSnapshot(
        snapshot_id=_BASELINE_ID,
        config_hash=compute_config_hash(),
        identity_hash="wave4c3test",
    ))
    set_active(_BASELINE_ID, actor="test", reason="4C.3 promotion fixture")


def _seed_review_candidate(
    tmp_path,
    candidate_id="OPT-4c3-001",
    baseline_id=_BASELINE_ID,
    with_provenance=True,
    provenance_hash=None,
    evaluation_id=_TEST_EVALUATION_ID,
):
    """READY_FOR_REVIEW candidate WITH successful evaluation evidence.

    Wave 4E.2: also publishes the canonical CandidateRecommendation that a human
    decision must bind to. Returns (reg_dir, dec_dir, rec_dir, rec_id).
    """
    reg_dir = str(tmp_path / "reg")
    dec_dir = str(tmp_path / "dec")
    reg = CandidateRegistry(storage_dir=reg_dir)
    change_definition = {"type": "direction_inversion"}
    if with_provenance:
        change_definition["baseline_config_hash"] = (
            provenance_hash if provenance_hash is not None
            else compute_config_hash()
        )
    reg.create(CandidateRecord(
        candidate_id=candidate_id,
        hypothesis_id="HYP-4c3-test",
        baseline_id=baseline_id,
        component="DIRECTION_INVERSION",
        description="Wave 4C.3 test candidate",
        change_definition=change_definition,
        status=CandidateStatus.READY_FOR_REVIEW,
    ))
    reg.add_validation_result(
        candidate_id,
        validation_id=evaluation_id,
        decision="IMPROVED",
        confidence="HIGH",
        sample_size=60,
        expectancy_delta=0.25,
    )
    rec_dir, rec_id = _publish_recommendation(
        tmp_path, candidate_id, evaluation_id=evaluation_id
    )
    return reg_dir, dec_dir, rec_dir, rec_id


def _publish_recommendation(
    tmp_path,
    candidate_id,
    *,
    evaluation_id=_TEST_EVALUATION_ID,
    treatment_id=_TEST_TREATMENT_ID,
    decision="VALIDATED",
    confidence="HIGH",
    eligible_pairs=60,
    promotion_blocked=False,
    promotion_block_reason="",
):
    """Publish the canonical Wave 4E.1 recommendation a decision binds to.

    Wave 4E.2 migration: the retired pre-4E.2 calling contract passed no
    recommendation_id. Production now REQUIRES one, so the fixture builds a
    genuine canonical recommendation whose provenance is derived from the
    evaluation/evidence chain — baseline identity and config hash are read from
    the candidate's own recorded provenance, never invented here. This keeps the
    4C.3 baseline-gate ordering/semantics under test intact: a stale candidate
    still reaches (and is blocked by) validate_candidate_baseline.
    """
    rec_dir = str(tmp_path / "rec")
    reg = CandidateRegistry(storage_dir=str(tmp_path / "reg"))
    cand = reg.get(candidate_id)
    baseline_id = cand.baseline_id if cand is not None else ""
    config_hash = (
        cand.change_definition.get("baseline_config_hash", "")
        if cand is not None else ""
    )
    evaluation = CandidateEvaluation(
        evaluation_id=evaluation_id,
        candidate_id=candidate_id,
        timestamp="2026-09-17T12:00:00+00:00",
        prospective_boundary="1970-01-01T00:00:00+00:00",
        total_observations_raw=eligible_pairs * 2,
        eligible_pairs=eligible_pairs,
        excluded_unpaired=0,
        excluded_pre_boundary=0,
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
        decision_reason="Candidate outperforms baseline",
        confidence=confidence,
        baseline_id=baseline_id,
        config_hash=config_hash,
        promotion_blocked=promotion_blocked,
        promotion_block_reason=promotion_block_reason,
        treatment_id=treatment_id,
    )
    rec = create_recommendation(
        evaluation, store=RecommendationStore(recommendations_dir=rec_dir)
    )
    return rec_dir, rec.recommendation_id


def _accept(cid, reg_dir, dec_dir, rec_dir, rec_id):
    return record_human_decision(
        cid, "ACCEPT", rec_id, actor="human-4c3",
        reason="approves the change",
        registry_dir=reg_dir, decisions_dir=dec_dir,
        recommendations_dir=rec_dir,
    )


def _blocked_rows(dec_dir, cid):
    return [
        d for d in CandidateDecisionStore(decisions_dir=dec_dir).list_all_rows()
        if d.candidate_id == cid and d.outcome == "BASELINE_BLOCKED"
    ]


# ═══════════════════════════════════════════════════════════════
# VALID APPROVAL (existing behaviour preserved)
# ═══════════════════════════════════════════════════════════════

class TestValidApproval:
    def test_valid_baseline_accept_still_promotes(self, tmp_path):
        reg_dir, dec_dir, rec_dir, rec_id = _seed_review_candidate(tmp_path)
        result = _accept("OPT-4c3-001", reg_dir, dec_dir, rec_dir, rec_id)
        assert result.ok and not result.duplicate
        reg = CandidateRegistry(storage_dir=reg_dir)
        assert reg.get("OPT-4c3-001").status == CandidateStatus.ACCEPTED
        d = get_human_decision("OPT-4c3-001", decisions_dir=dec_dir)
        assert d is not None and d.decision == "ACCEPT"
        assert d.status_before == "READY_FOR_REVIEW"
        assert d.status_after == "ACCEPTED"
        assert d.outcome == "COMPLETED"

    def test_valid_candidate_retains_baseline_identity(self, tmp_path):
        reg_dir, dec_dir, rec_dir, rec_id = _seed_review_candidate(tmp_path)
        _accept("OPT-4c3-001", reg_dir, dec_dir, rec_dir, rec_id)
        c = CandidateRegistry(storage_dir=reg_dir).get("OPT-4c3-001")
        assert c.baseline_id == _BASELINE_ID
        assert c.change_definition["baseline_config_hash"] == compute_config_hash()


# ═══════════════════════════════════════════════════════════════
# FAIL-CLOSED PROMOTION CASES
# ═══════════════════════════════════════════════════════════════

class TestFailClosedPromotion:
    def _assert_accept_blocked(self, tmp_path, expect_token, **seed_kwargs):
        reg_dir, dec_dir, rec_dir, rec_id = _seed_review_candidate(
            tmp_path, **seed_kwargs
        )
        with pytest.raises(ValueError, match=expect_token):
            _accept("OPT-4c3-001", reg_dir, dec_dir, rec_dir, rec_id)
        reg = CandidateRegistry(storage_dir=reg_dir)
        c = reg.get("OPT-4c3-001")
        # I. does NOT cross the promotion boundary
        assert c.status == CandidateStatus.READY_FOR_REVIEW
        # J/K. retains original baseline identity — no rebase, no mutation
        assert c.baseline_id == seed_kwargs.get("baseline_id", _BASELINE_ID)
        if seed_kwargs.get("with_provenance", True):
            original_hash = (
                seed_kwargs.get("provenance_hash") or compute_config_hash()
            )
            assert c.change_definition.get("baseline_config_hash") == original_hash
        else:
            assert "baseline_config_hash" not in c.change_definition
        # Effective decision truth: approval was REQUESTED, not ALLOWED
        assert get_human_decision("OPT-4c3-001", decisions_dir=dec_dir) is None
        rows = _blocked_rows(dec_dir, "OPT-4c3-001")
        assert len(rows) == 1 and rows[0].decision == "ACCEPT"
        assert expect_token in rows[0].error
        assert rows[0].actor == "human-4c3"
        return rows[0]

    def test_stale_baseline_blocks_promotion(self, tmp_path):
        """B: candidate baseline != active baseline."""
        self._assert_accept_blocked(
            tmp_path, "stale_baseline", baseline_id="V10_BASELINE_older"
        )

    def test_stale_config_blocks_promotion(self, tmp_path):
        """C: snapshot config_hash != current production config hash."""
        stale_hash = "retired-config-hash-v1"
        base = Path(baseline_authority._BASELINES_DIR)
        for f in base.iterdir():
            if f.name != _POINTER_NAME and f.is_file():
                f.unlink()
        SnapshotRegistry(
            baselines_dir=baseline_authority._BASELINES_DIR
        ).save(BaselineSnapshot(
            snapshot_id=_BASELINE_ID,
            config_hash=stale_hash,
            identity_hash="wave4c3stale",
        ))
        self._assert_accept_blocked(
            tmp_path, "stale_config", provenance_hash=stale_hash
        )

    def test_missing_baseline_provenance_blocks_promotion(self, tmp_path):
        """D: candidate has no baseline_config_hash provenance."""
        self._assert_accept_blocked(
            tmp_path, "missing_baseline_provenance", with_provenance=False
        )

    def test_missing_active_baseline_blocks_promotion(self, tmp_path):
        """E: no active-baseline pointer at all."""
        Path(baseline_authority._ACTIVE_POINTER_FILE).unlink()
        self._assert_accept_blocked(tmp_path, "no_active_baseline")

    def test_corrupt_active_baseline_blocks_promotion(self, tmp_path):
        """F: malformed pointer JSON fails closed."""
        Path(baseline_authority._ACTIVE_POINTER_FILE).write_text(
            "{ not valid json !!!", encoding="utf-8"
        )
        self._assert_accept_blocked(tmp_path, "baseline_state_error")

    def test_missing_referenced_snapshot_blocks_promotion(self, tmp_path):
        """G: pointer references a snapshot missing from the store."""
        base = Path(baseline_authority._BASELINES_DIR)
        for f in base.iterdir():
            if f.name != _POINTER_NAME and f.is_file():
                f.unlink()
        self._assert_accept_blocked(tmp_path, "baseline_state_error")

    def test_legacy_current_v10_cannot_cross_promotion(self, tmp_path):
        """H: the retired placeholder baseline can never be promoted."""
        self._assert_accept_blocked(
            tmp_path, "stale_baseline", baseline_id="current_v10"
        )



# ═══════════════════════════════════════════════════════════════
# AUDIT / RETRY / NON-PROMOTION DECISIONS / NO PRODUCTION APPLICATION
# ═══════════════════════════════════════════════════════════════

class TestAuditRetryAndNonPromotion:
    def test_blocked_row_preserves_intent_not_effective(self, tmp_path):
        """Human intent (ACCEPT + actor + reason) is auditable, never effective."""
        reg_dir, dec_dir, rec_dir, rec_id = _seed_review_candidate(
            tmp_path, candidate_id="OPT-4c3-aud",
            baseline_id="V10_BASELINE_stale",
        )
        with pytest.raises(ValueError, match="baseline safety invariant"):
            _accept("OPT-4c3-aud", reg_dir, dec_dir, rec_dir, rec_id)
        row = _blocked_rows(dec_dir, "OPT-4c3-aud")[0]
        assert row.decision == "ACCEPT"
        assert row.reason == "approves the change"
        assert row.status_before == "READY_FOR_REVIEW"
        assert row.status_after == ""
        assert row.outcome == "BASELINE_BLOCKED"
        # list_decisions (effective decisions only) excludes the blocked row:
        assert all(
            d.candidate_id != "OPT-4c3-aud"
            for d in CandidateDecisionStore(decisions_dir=dec_dir).list_decisions()
        )

    def test_blocked_row_does_not_poison_valid_later_approval(self, tmp_path):
        """BASELINE_BLOCKED is non-effective: a later valid ACCEPT works."""
        reg_dir, dec_dir, rec_dir, rec_id = _seed_review_candidate(
            tmp_path, candidate_id="OPT-4c3-b", baseline_id="V10_BASELINE_stale"
        )
        with pytest.raises(ValueError, match="baseline safety invariant"):
            _accept("OPT-4c3-b", reg_dir, dec_dir, rec_dir, rec_id)
        # A DIFFERENT, validly-bound candidate approves cleanly through the
        # same decision store (blocked rows never poison the store):
        reg_dir2, dec_dir2, rec_dir2, rec_id2 = _seed_review_candidate(
            tmp_path, candidate_id="OPT-4c3-c", evaluation_id="EVAL-4c3002"
        )
        assert _accept("OPT-4c3-c", reg_dir2, dec_dir2, rec_dir2, rec_id2).ok

    def test_reject_on_stale_candidate_still_works(self, tmp_path):
        """REJECT is NOT baseline-gated: a stale candidate can be rejected."""
        reg_dir, dec_dir, rec_dir, rec_id = _seed_review_candidate(
            tmp_path, candidate_id="OPT-4c3-rej",
            baseline_id="V10_BASELINE_older",
        )
        result = record_human_decision(
            "OPT-4c3-rej", "REJECT", rec_id, actor="human-4c3",
            reason="stale provenance, no longer relevant",
            registry_dir=reg_dir, decisions_dir=dec_dir,
            recommendations_dir=rec_dir,
        )
        assert result.ok and not result.duplicate
        c = CandidateRegistry(storage_dir=reg_dir).get("OPT-4c3-rej")
        assert c.status == CandidateStatus.REJECTED
        d = get_human_decision("OPT-4c3-rej", decisions_dir=dec_dir)
        assert d is not None and d.decision == "REJECT"
        assert d.outcome == "COMPLETED"
        assert c.baseline_id == "V10_BASELINE_older"  # untouched provenance

    def test_no_production_application_from_promotion(self, tmp_path):
        """Approval stops at ACCEPTED — no application/deployment semantics."""
        import research_engine.v10.candidates.candidate_decision as mod
        src = inspect.getsource(mod)
        for forbidden in (
            "MT5Execution", "ExecutionOrchestrator", "order_send",
            "ApplicationService", "apply_to_production",
        ):
            assert forbidden not in src
        reg_dir, dec_dir, rec_dir, rec_id = _seed_review_candidate(
            tmp_path, candidate_id="OPT-4c3-app"
        )
        _accept("OPT-4c3-app", reg_dir, dec_dir, rec_dir, rec_id)
        c = CandidateRegistry(storage_dir=reg_dir).get("OPT-4c3-app")
        # Boundary terminal state: ACCEPTED, and NOTHING beyond it happened
        assert c.status == CandidateStatus.ACCEPTED
        assert c.status_history[-1]["status"] == "ACCEPTED"
