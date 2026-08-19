"""
Tests for Candidate Activation Gate.

Covers:
    - Eligible candidates are activated (PROPOSED → SHADOW_TESTING)
    - Ineligible candidates are skipped (no hypothesis_id, unshadowable type, no baseline)
    - Max activations per cycle is respected
    - Empty registry produces no activations
    - Non-PROPOSED candidates are ignored
    - Activation never raises
    - Production safety: no imports of MT5Execution/RiskManager/broker
"""

import pytest

from research_engine.v10.candidates.candidate_registry import CandidateRegistry
from research_engine.v10.candidates.models import CandidateRecord, CandidateStatus
from research_engine.lifecycle.candidate_activation_gate import (
    activate_eligible_candidates,
    _check_eligibility,
    _SHADOW_TESTABLE_TYPES,
    _UNSHADOWABLE_TYPES,
)


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

def _make_candidate(
    candidate_id: str = "OPT-test001",
    hypothesis_id: str = "HYP-abc12345",
    baseline_id: str = "current_v10",
    change_type: str = "direction_inversion",
    status: str = CandidateStatus.PROPOSED,
    **kwargs,
) -> CandidateRecord:
    return CandidateRecord(
        candidate_id=candidate_id,
        hypothesis_id=hypothesis_id,
        baseline_id=baseline_id,
        change_definition={"type": change_type, "action": "invert_pattern_direction"},
        status=status,
        **kwargs,
    )


# ═══════════════════════════════════════════════════════════════
# ELIGIBILITY CHECKS
# ═══════════════════════════════════════════════════════════════

class TestEligibility:
    def test_eligible_direction_inversion(self):
        c = _make_candidate(change_type="direction_inversion")
        eligible, reason = _check_eligibility(c)
        assert eligible
        assert reason == "Eligible"

    def test_eligible_geometry_modification(self):
        c = _make_candidate(change_type="geometry_modification")
        eligible, _ = _check_eligibility(c)
        assert eligible

    def test_eligible_regime_conditioning(self):
        c = _make_candidate(change_type="regime_conditioning")
        eligible, _ = _check_eligibility(c)
        assert eligible

    def test_eligible_symbol_exclusion(self):
        c = _make_candidate(change_type="symbol_exclusion")
        eligible, _ = _check_eligibility(c)
        assert eligible

    def test_ineligible_no_hypothesis_id(self):
        c = _make_candidate(hypothesis_id="")
        eligible, reason = _check_eligibility(c)
        assert not eligible
        assert "hypothesis_id" in reason

    def test_ineligible_no_baseline_id(self):
        c = _make_candidate(baseline_id="")
        eligible, reason = _check_eligibility(c)
        assert not eligible
        assert "baseline_id" in reason

    def test_ineligible_score_recalibration(self):
        c = _make_candidate(change_type="score_recalibration")
        eligible, reason = _check_eligibility(c)
        assert not eligible
        assert "cannot be shadow-tested" in reason

    def test_ineligible_pattern_weighting(self):
        c = _make_candidate(change_type="pattern_weighting")
        eligible, reason = _check_eligibility(c)
        assert not eligible
        assert "cannot be shadow-tested" in reason

    def test_ineligible_research_recommendation(self):
        c = _make_candidate(change_type="research_recommendation")
        eligible, reason = _check_eligibility(c)
        assert not eligible
        assert "cannot be shadow-tested" in reason

    def test_ineligible_empty_change_type(self):
        c = _make_candidate()
        c.change_definition = {}
        eligible, reason = _check_eligibility(c)
        assert not eligible
        assert "No change_definition type" in reason

    def test_ineligible_unknown_type(self):
        c = _make_candidate(change_type="some_future_type")
        eligible, reason = _check_eligibility(c)
        assert not eligible
        assert "unknown/unsupported" in reason


# ═══════════════════════════════════════════════════════════════
# ACTIVATION INTEGRATION
# ═══════════════════════════════════════════════════════════════

class TestActivation:
    def test_empty_registry(self, tmp_path):
        result = activate_eligible_candidates(registry_dir=str(tmp_path / "empty"))
        assert result.candidates_scanned == 0
        assert result.candidates_activated == 0

    def test_single_eligible_activated(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(_make_candidate("OPT-001"))

        result = activate_eligible_candidates(registry_dir=str(tmp_path))
        assert result.candidates_scanned == 1
        assert result.candidates_activated == 1
        assert result.activations[0]["candidate_id"] == "OPT-001"

        # Verify status changed
        reloaded = CandidateRegistry(storage_dir=str(tmp_path))
        assert reloaded.get("OPT-001").status == CandidateStatus.SHADOW_TESTING

    def test_ineligible_not_activated(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(_make_candidate("OPT-BAD", change_type="score_recalibration"))

        result = activate_eligible_candidates(registry_dir=str(tmp_path))
        assert result.candidates_scanned == 1
        assert result.candidates_activated == 0
        assert result.candidates_ineligible == 1

        # Verify status unchanged
        reloaded = CandidateRegistry(storage_dir=str(tmp_path))
        assert reloaded.get("OPT-BAD").status == CandidateStatus.PROPOSED

    def test_max_activations_respected(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        for i in range(5):
            reg.create(_make_candidate(f"OPT-{i:03d}"))

        result = activate_eligible_candidates(registry_dir=str(tmp_path), max_activations=2)
        assert result.candidates_activated == 2

    def test_non_proposed_ignored(self, tmp_path):
        """Only PROPOSED candidates are scanned by the gate."""
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        c = _make_candidate("OPT-SHADOW")
        reg.create(c)
        reg.update_status("OPT-SHADOW", CandidateStatus.SHADOW_TESTING)

        result = activate_eligible_candidates(registry_dir=str(tmp_path))
        # list_by_status(PROPOSED) will return 0
        assert result.candidates_scanned == 0
        assert result.candidates_activated == 0

    def test_mixed_eligible_and_ineligible(self, tmp_path):
        reg = CandidateRegistry(storage_dir=str(tmp_path))
        reg.create(_make_candidate("OPT-GOOD", change_type="direction_inversion"))
        reg.create(_make_candidate("OPT-BAD1", change_type="pattern_weighting"))
        reg.create(_make_candidate("OPT-BAD2", hypothesis_id=""))

        result = activate_eligible_candidates(registry_dir=str(tmp_path))
        assert result.candidates_activated == 1
        assert result.candidates_ineligible == 2

    def test_activation_never_raises(self, tmp_path):
        """Even with a corrupted registry path, the gate should not raise."""
        # Pass a path that will fail on load
        result = activate_eligible_candidates(registry_dir=str(tmp_path / "nonexistent" / "deep"))
        assert result.candidates_scanned == 0


# ═══════════════════════════════════════════════════════════════
# PRODUCTION SAFETY
# ═══════════════════════════════════════════════════════════════

class TestProductionSafety:
    def test_no_mt5_imports(self):
        """Verify the activation gate has no path to production execution."""
        import inspect
        import research_engine.lifecycle.candidate_activation_gate as gate_mod

        source = inspect.getsource(gate_mod)
        # Check for actual import/usage patterns — not docstring mentions
        assert "import MT5Execution" not in source
        assert "from" not in source or "MT5Execution" not in source.split("from")[-1].split("\n")[0] if "from" in source else True
        assert "import RiskManager" not in source
        assert "order_send(" not in source
        assert "import ExecutionOrchestrator" not in source
        # Verify the module does not import any production execution modules
        import sys
        gate_imports = set(gate_mod.__dict__.keys())
        assert "MT5Execution" not in gate_imports
        assert "RiskManager" not in gate_imports
        assert "ExecutionOrchestrator" not in gate_imports
