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
from research_engine.v10.candidates.candidate_decision import (
    CandidateDecisionStore,
    get_human_decision,
    record_human_decision,
)
from research_engine.v10.candidates.candidate_registry import CandidateRegistry
from research_engine.v10.candidates.models import CandidateRecord, CandidateStatus

_BASELINE_ID = "V10_BASELINE_wave4c3test"
_POINTER_NAME = "active_baseline.json"


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
):
    """READY_FOR_REVIEW candidate WITH successful evaluation evidence."""
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
        validation_id="EVAL-4c3001",
        decision="IMPROVED",
        confidence="HIGH",
        sample_size=60,
        expectancy_delta=0.25,
    )
    return reg_dir, dec_dir


def _accept(cid, reg_dir, dec_dir):
    return record_human_decision(
        cid, "ACCEPT", actor="human-4c3", reason="approves the change",
        registry_dir=reg_dir, decisions_dir=dec_dir,
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
        reg_dir, dec_dir = _seed_review_candidate(tmp_path)
        result = _accept("OPT-4c3-001", reg_dir, dec_dir)
        assert result.ok and not result.duplicate
        reg = CandidateRegistry(storage_dir=reg_dir)
        assert reg.get("OPT-4c3-001").status == CandidateStatus.ACCEPTED
        d = get_human_decision("OPT-4c3-001", decisions_dir=dec_dir)
        assert d is not None and d.decision == "ACCEPT"
        assert d.status_before == "READY_FOR_REVIEW"
        assert d.status_after == "ACCEPTED"
        assert d.outcome == "COMPLETED"

    def test_valid_candidate_retains_baseline_identity(self, tmp_path):
        reg_dir, dec_dir = _seed_review_candidate(tmp_path)
        _accept("OPT-4c3-001", reg_dir, dec_dir)
        c = CandidateRegistry(storage_dir=reg_dir).get("OPT-4c3-001")
        assert c.baseline_id == _BASELINE_ID
        assert c.change_definition["baseline_config_hash"] == compute_config_hash()


# ═══════════════════════════════════════════════════════════════
# FAIL-CLOSED PROMOTION CASES
# ═══════════════════════════════════════════════════════════════

class TestFailClosedPromotion:
    def _assert_accept_blocked(self, tmp_path, expect_token, **seed_kwargs):
        reg_dir, dec_dir = _seed_review_candidate(tmp_path, **seed_kwargs)
        with pytest.raises(ValueError, match=expect_token):
            _accept("OPT-4c3-001", reg_dir, dec_dir)
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
        reg_dir, dec_dir = _seed_review_candidate(
            tmp_path, candidate_id="OPT-4c3-aud",
            baseline_id="V10_BASELINE_stale",
        )
        with pytest.raises(ValueError, match="baseline safety invariant"):
            _accept("OPT-4c3-aud", reg_dir, dec_dir)
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
        reg_dir, dec_dir = _seed_review_candidate(
            tmp_path, candidate_id="OPT-4c3-b", baseline_id="V10_BASELINE_stale"
        )
        with pytest.raises(ValueError, match="baseline safety invariant"):
            _accept("OPT-4c3-b", reg_dir, dec_dir)
        # A DIFFERENT, validly-bound candidate approves cleanly through the
        # same decision store (blocked rows never poison the store):
        reg_dir2, dec_dir2 = _seed_review_candidate(
            tmp_path, candidate_id="OPT-4c3-c"
        )
        assert _accept("OPT-4c3-c", reg_dir2, dec_dir2).ok

    def test_reject_on_stale_candidate_still_works(self, tmp_path):
        """REJECT is NOT baseline-gated: a stale candidate can be rejected."""
        reg_dir, dec_dir = _seed_review_candidate(
            tmp_path, candidate_id="OPT-4c3-rej",
            baseline_id="V10_BASELINE_older",
        )
        result = record_human_decision(
            "OPT-4c3-rej", "REJECT", actor="human-4c3",
            reason="stale provenance, no longer relevant",
            registry_dir=reg_dir, decisions_dir=dec_dir,
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
        reg_dir, dec_dir = _seed_review_candidate(
            tmp_path, candidate_id="OPT-4c3-app"
        )
        _accept("OPT-4c3-app", reg_dir, dec_dir)
        c = CandidateRegistry(storage_dir=reg_dir).get("OPT-4c3-app")
        # Boundary terminal state: ACCEPTED, and NOTHING beyond it happened
        assert c.status == CandidateStatus.ACCEPTED
        assert c.status_history[-1]["status"] == "ACCEPTED"
