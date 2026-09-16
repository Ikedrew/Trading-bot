"""
Wave 4C.2 — Baseline-bound candidate activation: focused proof tests.

Drives the REAL gate path (activate_eligible_candidates → _check_eligibility
→ _check_baseline_provenance → validate_candidate_baseline) against
temporary stores. Production data/baselines/ is never touched.

Covered proofs (each fail-closed case leaves the candidate PROPOSED, with
NO silent rebase and NO baseline/candidate mutation):

    1. Valid baseline-bound candidate enters SHADOW_TESTING
    2. candidate baseline != active baseline        → stale_baseline (blocked)
    3. stale current config                         → stale_config (blocked)
    4. missing baseline_config_hash                 → missing_baseline_provenance (blocked)
    5. missing active baseline (no pointer)         → no_active_baseline (blocked)
    6. corrupt active-baseline pointer state        → baseline_state_error (blocked)
    7. missing referenced snapshot                  → fail closed (blocked)
    8. candidate hash != snapshot config_hash       → config_provenance_mismatch (blocked)
    9. legacy baseline_id="current_v10"             → blocked (can never activate)
   10. blocked candidate remains outside SHADOW_TESTING (stays PROPOSED)
   11. blocked candidate is NOT silently rebased (record unchanged)
   12. valid candidate retains its original baseline_id
   13. existing non-baseline activation requirements still apply
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_engine.v10.baselines.baseline_authority as baseline_authority
from research_engine.v10.baselines.baseline_authority import (
    set_active,
    validate_candidate_baseline,
)
from research_engine.v10.baselines.models import BaselineSnapshot
from research_engine.v10.baselines.snapshot_registry import SnapshotRegistry
from core.research_events import compute_config_hash
from research_engine.v10.candidates.candidate_registry import CandidateRegistry
from research_engine.v10.candidates.models import CandidateRecord, CandidateStatus
from research_engine.lifecycle.candidate_activation_gate import (
    activate_eligible_candidates,
)

# The canonical active baseline every valid default candidate is bound to.
_BASELINE_ID = "V10_BASELINE_wave4c2test"
_POINTER_NAME = "active_baseline.json"


# ═══════════════════════════════════════════════════════════════
# FIXTURES / HELPERS
# ═══════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _valid_baseline_env(tmp_path, monkeypatch):
    """
    Wave 4C.2: every test runs against a VALID canonical active baseline on
    temporary stores (per-test redirect — same convention as the existing
    gate and 4C.1 test modules). Default candidates created via
    _make_candidate bind to it with matching config provenance.
    """
    baselines_dir = str(tmp_path / "baselines")
    monkeypatch.setattr(baseline_authority, "_BASELINES_DIR", baselines_dir)
    monkeypatch.setattr(
        baseline_authority, "_ACTIVE_POINTER_FILE",
        str(Path(baselines_dir) / _POINTER_NAME),
    )
    SnapshotRegistry(baselines_dir=baselines_dir).save(BaselineSnapshot(
        snapshot_id=_BASELINE_ID,
        config_hash=compute_config_hash(),
        identity_hash="wave4c2test",
    ))
    set_active(_BASELINE_ID, actor="test", reason="4C.2 activation fixture")


def _make_candidate(
    candidate_id: str = "OPT-4c2-001",
    hypothesis_id: str = "HYP-4c2abcde",
    baseline_id: str = _BASELINE_ID,
    change_type: str = "direction_inversion",
    status: str = CandidateStatus.PROPOSED,
    with_provenance: bool = True,
    provenance_hash: str | None = None,
    **kwargs,
) -> CandidateRecord:
    change_definition = {"type": change_type, "action": "invert_pattern_direction"}
    if with_provenance:
        change_definition["baseline_config_hash"] = (
            provenance_hash if provenance_hash is not None else compute_config_hash()
        )
    return CandidateRecord(
        candidate_id=candidate_id,
        hypothesis_id=hypothesis_id,
        baseline_id=baseline_id,
        change_definition=change_definition,
        status=status,
        **kwargs,
    )


def _run_gate(tmp_path, candidate):
    """Persist one candidate and run the REAL activation gate over it."""
    reg = CandidateRegistry(storage_dir=str(tmp_path / "candidates"))
    reg.create(candidate)
    return activate_eligible_candidates(
        registry_dir=str(tmp_path / "candidates")
    ), CandidateRegistry(storage_dir=str(tmp_path / "candidates"))


def _baseline_skip_reasons(result):
    return [
        s["reason"] for s in result.skips
        if s["reason"].startswith("baseline_provenance: ")
    ]


def _drop_snapshot_files():
    """Delete all persisted snapshot files (pointer file left in place)."""
    base = Path(baseline_authority._BASELINES_DIR)
    for f in base.iterdir():
        if f.name != _POINTER_NAME and f.is_file():
            f.unlink()


# ═══════════════════════════════════════════════════════════════
# VALID ACTIVATION
# ═══════════════════════════════════════════════════════════════

class TestValidActivation:
    def test_valid_candidate_enters_shadow_testing(self, tmp_path):
        """Proof 1: valid baseline-bound candidate PROPOSED → SHADOW_TESTING."""
        result, reg = _run_gate(tmp_path, _make_candidate())
        assert result.candidates_scanned == 1
        assert result.candidates_activated == 1
        assert result.activations[0]["candidate_id"] == "OPT-4c2-001"
        assert reg.get("OPT-4c2-001").status == CandidateStatus.SHADOW_TESTING

    def test_valid_candidate_retains_original_baseline_id(self, tmp_path):
        """Proof 12: valid candidate keeps its ORIGINAL baseline binding."""
        result, reg = _run_gate(tmp_path, _make_candidate())
        assert result.candidates_activated == 1
        reloaded = reg.get("OPT-4c2-001")
        assert reloaded.baseline_id == _BASELINE_ID
        assert reloaded.change_definition["baseline_config_hash"] == (
            compute_config_hash()
        )


# ═══════════════════════════════════════════════════════════════
# FAIL-CLOSED BASELINE CASES
# ═══════════════════════════════════════════════════════════════

class TestFailClosedBaseline:
    def _assert_blocked(self, result, reg, cid, expected_token):
        assert result.candidates_activated == 0
        reasons = _baseline_skip_reasons(result)
        assert len(reasons) == 1, f"expected one baseline block, got {reasons}"
        assert expected_token in reasons[0]
        assert reg.get(cid).status == CandidateStatus.PROPOSED

    def test_candidate_baseline_not_active_baseline(self, tmp_path):
        """Proof 2: candidate baseline != active baseline → stale_baseline."""
        result, reg = _run_gate(
            tmp_path, _make_candidate(baseline_id="V10_BASELINE_somewhereelse")
        )
        self._assert_blocked(result, reg, "OPT-4c2-001", "stale_baseline")

    def test_stale_current_config_blocked(self, tmp_path):
        """
        Proof 3: candidate provenance matches its snapshot (checks 1-7 pass),
        but the snapshot config_hash != CURRENT production config hash →
        stale_config. Install a stale-hash active snapshot, bind the
        candidate's provenance to THAT stale hash.
        """
        stale_hash = "retired-config-hash-v1"
        _drop_snapshot_files()
        SnapshotRegistry(
            baselines_dir=baseline_authority._BASELINES_DIR
        ).save(BaselineSnapshot(
            snapshot_id=_BASELINE_ID,
            config_hash=stale_hash,
            identity_hash="wave4c2stale",
        ))
        # Pointer still references _BASELINE_ID, which now resolves to the
        # stale snapshot — get_active stays valid; only the CURRENT-config
        # comparison can fail.
        result, reg = _run_gate(
            tmp_path, _make_candidate(provenance_hash=stale_hash)
        )
        self._assert_blocked(result, reg, "OPT-4c2-001", "stale_config")

    def test_missing_baseline_config_hash_blocked(self, tmp_path):
        """Proof 4: no baseline_config_hash provenance → missing_baseline_provenance."""
        result, reg = _run_gate(tmp_path, _make_candidate(with_provenance=False))
        self._assert_blocked(
            result, reg, "OPT-4c2-001", "missing_baseline_provenance"
        )

    def test_missing_active_baseline_blocked(self, tmp_path):
        """Proof 5: no active-baseline pointer at all → no_active_baseline."""
        Path(baseline_authority._ACTIVE_POINTER_FILE).unlink()
        result, reg = _run_gate(tmp_path, _make_candidate())
        self._assert_blocked(result, reg, "OPT-4c2-001", "no_active_baseline")

    def test_corrupt_active_baseline_state_blocked(self, tmp_path):
        """Proof 6: malformed pointer JSON → baseline_state_error (fail closed)."""
        pointer = Path(baseline_authority._ACTIVE_POINTER_FILE)
        pointer.write_text("{ not valid json !!!", encoding="utf-8")
        result, reg = _run_gate(tmp_path, _make_candidate())
        self._assert_blocked(result, reg, "OPT-4c2-001", "baseline_state_error")

    def test_missing_referenced_snapshot_blocked(self, tmp_path):
        """
        Proof 7: the referenced snapshot is missing from the store. The 4C.1
        authority fails closed at pointer-read time (get_active raises
        BaselineStateError for a pointer to a missing snapshot), which the
        gate converts to a deterministic block — the candidate is never
        activated against an unprovable baseline.
        """
        _drop_snapshot_files()
        result, reg = _run_gate(tmp_path, _make_candidate())
        self._assert_blocked(
            result, reg, "OPT-4c2-001", "baseline_state_error"
        )
        # Unit-level: the same candidate proof also fails closed directly.
        ok, reason = validate_candidate_baseline(_BASELINE_ID, compute_config_hash())
        assert ok is False and reason != "baseline_valid"

    def test_candidate_hash_mismatch_blocked(self, tmp_path):
        """
        Proof 8: candidate provenance != snapshot config_hash →
        config_provenance_mismatch (direct unit proof against the live,
        valid snapshot).
        """
        ok, reason = validate_candidate_baseline(
            _BASELINE_ID, "definitely-not-the-snapshot-hash"
        )
        assert ok is False
        assert "config_provenance_mismatch" in reason

    def test_legacy_current_v10_cannot_activate(self, tmp_path):
        """
        Proof 9: the retired "current_v10" placeholder baseline can never
        pass the invariant — the active canonical baseline is the real one.
        """
        result, reg = _run_gate(
            tmp_path, _make_candidate(baseline_id="current_v10")
        )
        self._assert_blocked(result, reg, "OPT-4c2-001", "stale_baseline")
        # Direct proof as well: placeholder id always fails.
        ok, reason = validate_candidate_baseline(
            "current_v10", compute_config_hash()
        )
        assert ok is False
        assert reason != "baseline_valid"


# ═══════════════════════════════════════════════════════════════
# NO SILENT REBASE / STATE PRESERVATION
# ═══════════════════════════════════════════════════════════════

class TestNoSilentRebase:
    def test_blocked_candidate_untouched_after_gate(self, tmp_path):
        """
        Proofs 10 + 11: a blocked candidate stays PROPOSED (outside
        SHADOW_TESTING) and its record is NOT rebased/mutated onto any
        other baseline.
        """
        before = _make_candidate(
            candidate_id="OPT-4c2-block",
            baseline_id="V10_BASELINE_oldstate",
            with_provenance=False,
        )
        result, reg = _run_gate(tmp_path, before)
        assert result.candidates_activated == 0

        reloaded = reg.get("OPT-4c2-block")
        assert reloaded.status == CandidateStatus.PROPOSED  # never in shadow
        # Immutable provenance — no silent rebase onto the active baseline:
        assert reloaded.baseline_id == "V10_BASELINE_oldstate"
        assert "baseline_config_hash" not in reloaded.change_definition

    def test_blocked_candidates_not_rebased_when_active_changed(self, tmp_path):
        """
        Extra rebase proof: even with a DIFFERENT (newer) valid active
        baseline installed, a stale candidate is blocked as-is — never
        rewritten to the new active baseline.
        """
        reg = CandidateRegistry(storage_dir=str(tmp_path / "candidates"))
        reg.create(_make_candidate("OPT-4c2-stale"))

        # Install a second, distinct active baseline.
        second_id = "V10_BASELINE_wave4c2second"
        SnapshotRegistry(
            baselines_dir=baseline_authority._BASELINES_DIR
        ).save(BaselineSnapshot(
            snapshot_id=second_id,
            config_hash=compute_config_hash(),
            identity_hash="wave4c2second",
        ))
        set_active(second_id, actor="test", reason="4C.2 rebase-proof")

        result = activate_eligible_candidates(
            registry_dir=str(tmp_path / "candidates")
        )
        assert result.candidates_activated == 0
        reloaded = CandidateRegistry(
            storage_dir=str(tmp_path / "candidates")
        ).get("OPT-4c2-stale")
        assert reloaded.status == CandidateStatus.PROPOSED
        assert reloaded.baseline_id == _BASELINE_ID  # NOT rebased to second_id


# ═══════════════════════════════════════════════════════════════
# EXISTING ACTIVATION REQUIREMENTS (pre-4C.2 behaviour intact)
# ═══════════════════════════════════════════════════════════════

class TestExistingRequirementsStillApply:
    def test_non_baseline_requirements_still_enforced(self, tmp_path):
        """Proof 13: eligibility (hypothesis, type, status) still applies."""
        reg = CandidateRegistry(storage_dir=str(tmp_path / "candidates"))
        reg.create(_make_candidate("OPT-nohyp", hypothesis_id=""))
        reg.create(_make_candidate("OPT-badtype", change_type="score_recalibration"))

        result = activate_eligible_candidates(
            registry_dir=str(tmp_path / "candidates")
        )
        assert result.candidates_activated == 0
        assert result.candidates_ineligible == 2
        non_baseline_reasons = [
            s["reason"] for s in result.skips
            if not s["reason"].startswith("baseline_provenance: ")
        ]
        assert len(non_baseline_reasons) == 2
        for cid in ("OPT-nohyp", "OPT-badtype"):
            assert reg.get(cid).status == CandidateStatus.PROPOSED

    def test_max_activations_still_respected(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path / "candidates"))
        for i in range(5):
            reg.create(_make_candidate(f"OPT-cap-{i:03d}"))
        result = activate_eligible_candidates(
            registry_dir=str(tmp_path / "candidates"), max_activations=2
        )
        assert result.candidates_activated == 2