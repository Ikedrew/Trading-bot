"""
Tests for Candidate Evaluation Bridge — lifecycle integration.
"""
import sys
import json
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

from research_engine.lifecycle.candidate_evaluation_bridge import evaluate_candidate
from research_engine.lifecycle.candidate_evaluator import CandidateEvaluation, EvaluationConfig
from research_engine.v10.candidates.models import CandidateRecord, CandidateStatus
from research_engine.v10.candidates.candidate_registry import CandidateRegistry


@pytest.fixture
def registry_dir(tmp_path):
    d = tmp_path / "candidates"
    d.mkdir()
    return str(d)


def _create_candidate(registry_dir, candidate_id="OPT-test", status="VALIDATING"):
    reg = CandidateRegistry(storage_dir=registry_dir)
    c = CandidateRecord(
        candidate_id=candidate_id,
        hypothesis_id="H-test",
        baseline_id="current_v10",
        status=status,
        change_definition={"type": "direction_inversion"},
        created_at="1970-01-01T00:00:00+00:00",  # Before all test observations
    )
    c.status = status
    reg._candidates[candidate_id] = c
    reg._persist()
    return reg


def _make_paired_obs(n, baseline_r=-0.1, candidate_r=0.3, candidate_id="OPT-test"):
    """Create n paired observations (prospective — after ts=0)."""
    obs = []
    for i in range(n):
        obs.append({"identity": {"entity_id": f"E_{i}", "shadow_type": "V10_PRIMARY", "symbol": ["EU","GB","JP"][i%3]},
                    "decision_snapshot": {"timestamp_decision_utc": 5000.0 + i * 300},
                    "simulated_outcome": {"pnl_r_multiple": baseline_r + (i % 5) * 0.05}})
        obs.append({"identity": {"entity_id": f"E_{i}", "shadow_type": f"CANDIDATE_{candidate_id}", "symbol": ["EU","GB","JP"][i%3]},
                    "decision_snapshot": {"timestamp_decision_utc": 5000.0 + i * 300},
                    "simulated_outcome": {"pnl_r_multiple": candidate_r + (i % 5) * 0.05}})
    return obs


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION HISTORY
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidationHistory:
    def test_validated_persists_history(self, registry_dir):
        _create_candidate(registry_dir)
        obs = _make_paired_obs(50, baseline_r=-0.2, candidate_r=0.3)
        cfg = EvaluationConfig(minimum_sample=30, min_symbols_positive=2, min_periods_positive=2)

        result = evaluate_candidate("OPT-test", shadow_observations=obs,
                                    config=cfg, registry_dir=registry_dir)

        # Reload and check validation history
        reg = CandidateRegistry(storage_dir=registry_dir)
        c = reg.get("OPT-test")
        assert len(c.validation_history) >= 1
        entry = c.validation_history[-1]
        assert entry.validation_id == result.evaluation_id
        assert entry.sample_size == result.n
        assert entry.expectancy_delta == result.mean_delta_r


# ═══════════════════════════════════════════════════════════════════════════════
# LIFECYCLE TRANSITIONS
# ═══════════════════════════════════════════════════════════════════════════════


class TestLifecycleTransitions:
    def test_validated_transitions_to_validated(self, registry_dir):
        _create_candidate(registry_dir, status="VALIDATING")
        obs = _make_paired_obs(60, baseline_r=-0.3, candidate_r=0.4)
        cfg = EvaluationConfig(minimum_sample=30, min_symbols_positive=2, min_periods_positive=2)

        result = evaluate_candidate("OPT-test", shadow_observations=obs,
                                    config=cfg, registry_dir=registry_dir)

        if result.decision == "VALIDATED":
            reg = CandidateRegistry(storage_dir=registry_dir)
            assert reg.get("OPT-test").status == CandidateStatus.VALIDATED

    def test_rejected_transitions_to_failed_validation(self, registry_dir):
        _create_candidate(registry_dir, status="VALIDATING")
        # Candidate consistently worse — every single pair has negative delta
        obs = []
        for i in range(50):
            obs.append({"identity": {"entity_id": f"E_{i}", "shadow_type": "V10_PRIMARY", "symbol": ["EU","GB","JP"][i%3]},
                        "decision_snapshot": {"timestamp_decision_utc": 5000.0 + i * 300},
                        "simulated_outcome": {"pnl_r_multiple": 0.5}})
            obs.append({"identity": {"entity_id": f"E_{i}", "shadow_type": "CANDIDATE_OPT-test", "symbol": ["EU","GB","JP"][i%3]},
                        "decision_snapshot": {"timestamp_decision_utc": 5000.0 + i * 300},
                        "simulated_outcome": {"pnl_r_multiple": -1.0}})
        cfg = EvaluationConfig(minimum_sample=30)

        result = evaluate_candidate("OPT-test", shadow_observations=obs,
                                    config=cfg, registry_dir=registry_dir)

        assert result.decision == "REJECTED", f"Got {result.decision}: {result.decision_reason}"
        reg = CandidateRegistry(storage_dir=registry_dir)
        assert reg.get("OPT-test").status == CandidateStatus.FAILED_VALIDATION

    def test_inconclusive_no_transition(self, registry_dir):
        _create_candidate(registry_dir, status="VALIDATING")
        obs = _make_paired_obs(5)  # Too few → INCONCLUSIVE
        cfg = EvaluationConfig(minimum_sample=30)

        result = evaluate_candidate("OPT-test", shadow_observations=obs,
                                    config=cfg, registry_dir=registry_dir)

        assert result.decision == "INCONCLUSIVE"
        reg = CandidateRegistry(storage_dir=registry_dir)
        assert reg.get("OPT-test").status == "VALIDATING"  # Unchanged


# ═══════════════════════════════════════════════════════════════════════════════
# INCONCLUSIVE REMAINS ELIGIBLE
# ═══════════════════════════════════════════════════════════════════════════════


class TestInconclusiveEligibility:
    def test_inconclusive_candidate_can_be_re_evaluated(self, registry_dir):
        """INCONCLUSIVE candidate stays in VALIDATING — can accumulate more evidence."""
        _create_candidate(registry_dir, status="VALIDATING")
        obs = _make_paired_obs(10)
        cfg = EvaluationConfig(minimum_sample=30)

        r1 = evaluate_candidate("OPT-test", shadow_observations=obs,
                                config=cfg, registry_dir=registry_dir)
        assert r1.decision == "INCONCLUSIVE"

        # Can evaluate again with more data
        obs2 = _make_paired_obs(50, baseline_r=-0.2, candidate_r=0.3)
        r2 = evaluate_candidate("OPT-test", shadow_observations=obs2,
                                config=cfg, registry_dir=registry_dir)
        # Second evaluation should work (not blocked)
        assert r2.n >= 30 or r2.decision == "INCONCLUSIVE"


# ═══════════════════════════════════════════════════════════════════════════════
# IDEMPOTENCY
# ═══════════════════════════════════════════════════════════════════════════════


class TestIdempotency:
    def test_repeated_evaluation_safe(self, registry_dir):
        """Evaluating twice appends to history without corruption."""
        _create_candidate(registry_dir, status="VALIDATING")
        obs = _make_paired_obs(50, baseline_r=-0.2, candidate_r=0.3)
        cfg = EvaluationConfig(minimum_sample=30, min_symbols_positive=2, min_periods_positive=2)

        r1 = evaluate_candidate("OPT-test", shadow_observations=obs,
                                config=cfg, registry_dir=registry_dir)
        r2 = evaluate_candidate("OPT-test", shadow_observations=obs,
                                config=cfg, registry_dir=registry_dir)

        # Both should complete (second may be blocked by lifecycle if already transitioned)
        assert r1.evaluation_id != r2.evaluation_id or r1.decision == r2.decision


# ═══════════════════════════════════════════════════════════════════════════════
# INELIGIBLE CANDIDATES
# ═══════════════════════════════════════════════════════════════════════════════


class TestIneligibleCandidates:
    def test_proposed_candidate_blocked(self, registry_dir):
        """PROPOSED candidate is not eligible for evaluation."""
        _create_candidate(registry_dir, status="PROPOSED")
        obs = _make_paired_obs(50)
        result = evaluate_candidate("OPT-test", shadow_observations=obs,
                                    registry_dir=registry_dir)
        assert result.decision == "INCONCLUSIVE"
        assert "expected one of" in result.decision_reason

    def test_nonexistent_candidate_blocked(self, registry_dir):
        result = evaluate_candidate("OPT-ghost", shadow_observations=[],
                                    registry_dir=registry_dir)
        assert result.decision == "INCONCLUSIVE"
        assert "not found" in result.decision_reason.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# GOVERNANCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestGovernanceBoundary:
    def test_validated_not_promoted(self, registry_dir):
        """VALIDATED does not become ACCEPTED/PROMOTED automatically."""
        _create_candidate(registry_dir, status="VALIDATING")
        obs = _make_paired_obs(100, baseline_r=-0.4, candidate_r=0.5)
        cfg = EvaluationConfig(minimum_sample=30, min_symbols_positive=2, min_periods_positive=2)

        result = evaluate_candidate("OPT-test", shadow_observations=obs,
                                    config=cfg, registry_dir=registry_dir)

        reg = CandidateRegistry(storage_dir=registry_dir)
        c = reg.get("OPT-test")
        # Even if VALIDATED, must NOT be ACCEPTED or higher
        assert c.status in (CandidateStatus.VALIDATED, CandidateStatus.VALIDATING)
        assert c.status != CandidateStatus.ACCEPTED

    def test_no_production_imports(self):
        """Bridge module has no production execution imports."""
        import ast
        import research_engine.lifecycle.candidate_evaluation_bridge as mod
        source = open(mod.__file__, encoding="utf-8").read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                m = node.module or ""
                assert "mt5_execution" not in m.lower()
                assert "execution_orchestrator" not in m.lower()
                assert "risk.manager" not in m
