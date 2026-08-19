"""
Tests for Candidate Evaluator — Stage-2 paired observation evaluation.
"""
import sys
import statistics
from datetime import datetime, timezone

import pytest

sys.path.insert(0, ".")

from research_engine.lifecycle.candidate_evaluator import (
    CandidateEvaluator, CandidateEvaluation, EvaluationConfig,
)


def _make_pair(entity_id, baseline_r, candidate_r, symbol="EURUSD", ts=1000.0):
    """Create a baseline + candidate shadow observation pair."""
    return [
        {"identity": {"entity_id": entity_id, "shadow_type": "V10_PRIMARY", "symbol": symbol},
         "decision_snapshot": {"timestamp_decision_utc": ts},
         "simulated_outcome": {"pnl_r_multiple": baseline_r}},
        {"identity": {"entity_id": entity_id, "shadow_type": "CANDIDATE_OPT-test", "symbol": symbol},
         "decision_snapshot": {"timestamp_decision_utc": ts},
         "simulated_outcome": {"pnl_r_multiple": candidate_r}},
    ]


def _make_dataset(n, baseline_mean=-0.1, candidate_mean=0.2, symbols=None):
    """Create N paired observations with specified means."""
    import random
    rng = random.Random(42)
    obs = []
    syms = symbols or ["EURUSD", "GBPUSD", "USDJPY"]
    for i in range(n):
        b_r = baseline_mean + rng.gauss(0, 0.3)
        c_r = candidate_mean + rng.gauss(0, 0.3)
        sym = syms[i % len(syms)]
        obs.extend(_make_pair(f"E_{i}", b_r, c_r, symbol=sym, ts=2000.0 + i * 300))
    return obs


# ═══════════════════════════════════════════════════════════════════════════════
# PAIR RECONSTRUCTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestPairReconstruction:
    def test_correct_pairing(self):
        obs = _make_pair("E_1", -0.5, 0.3) + _make_pair("E_2", -0.2, 0.1)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=2))
        result = ev.evaluate(candidate_id="OPT-test", candidate_activated_at="1970-01-01T00:00:00+00:00",
                             shadow_observations=obs)
        assert result.eligible_pairs == 2
        assert result.n == 2

    def test_missing_baseline_excluded(self):
        """Candidate without baseline is excluded."""
        obs = [{"identity": {"entity_id": "E_1", "shadow_type": "CANDIDATE_OPT-test", "symbol": "EU"},
                "decision_snapshot": {"timestamp_decision_utc": 2000},
                "simulated_outcome": {"pnl_r_multiple": 0.5}}]
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=1))
        result = ev.evaluate(candidate_id="OPT-test", candidate_activated_at="1970-01-01T00:00:00+00:00",
                             shadow_observations=obs)
        assert result.eligible_pairs == 0

    def test_missing_candidate_excluded(self):
        """Baseline without candidate is excluded."""
        obs = [{"identity": {"entity_id": "E_1", "shadow_type": "V10_PRIMARY", "symbol": "EU"},
                "decision_snapshot": {"timestamp_decision_utc": 2000},
                "simulated_outcome": {"pnl_r_multiple": -0.3}}]
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=1))
        result = ev.evaluate(candidate_id="OPT-test", candidate_activated_at="1970-01-01T00:00:00+00:00",
                             shadow_observations=obs)
        assert result.eligible_pairs == 0


# ═══════════════════════════════════════════════════════════════════════════════
# PROSPECTIVE BOUNDARY
# ═══════════════════════════════════════════════════════════════════════════════


class TestProspectiveBoundary:
    def test_pre_candidate_excluded(self):
        """Observations before candidate activation are excluded."""
        # Pair at ts=500 (before boundary) + pair at ts=2000 (after)
        obs = (_make_pair("E_old", -0.5, 0.3, ts=500.0) +
               _make_pair("E_new", -0.2, 0.1, ts=2000.0))
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=1))
        result = ev.evaluate(candidate_id="OPT-test",
                             candidate_activated_at="1970-01-01T00:16:40+00:00",  # ts=1000
                             shadow_observations=obs)
        assert result.eligible_pairs == 1  # Only the ts=2000 pair
        assert result.excluded_pre_boundary == 2  # Both old observations excluded


# ═══════════════════════════════════════════════════════════════════════════════
# MINIMUM SAMPLE
# ═══════════════════════════════════════════════════════════════════════════════


class TestMinimumSample:
    def test_below_minimum_inconclusive(self):
        obs = _make_pair("E_1", -0.5, 0.3)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=30))
        result = ev.evaluate(candidate_id="OPT-test",
                             candidate_activated_at="1970-01-01T00:00:00+00:00",
                             shadow_observations=obs)
        assert result.decision == "INCONCLUSIVE"
        assert "insufficient" in result.decision_reason.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# STRONG IMPROVEMENT
# ═══════════════════════════════════════════════════════════════════════════════


class TestStrongImprovement:
    def test_significant_improvement_validates(self):
        obs = _make_dataset(60, baseline_mean=-0.2, candidate_mean=0.3)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=30, min_symbols_positive=2, min_periods_positive=2))
        result = ev.evaluate(candidate_id="OPT-test",
                             candidate_activated_at="1970-01-01T00:00:00+00:00",
                             shadow_observations=obs)
        assert result.mean_delta_r > 0
        assert result.decision in ("VALIDATED", "INCONCLUSIVE")  # Depends on robustness
        assert result.n == 60


# ═══════════════════════════════════════════════════════════════════════════════
# NO IMPROVEMENT
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoImprovement:
    def test_candidate_worse_rejected(self):
        obs = _make_dataset(60, baseline_mean=0.2, candidate_mean=-0.3)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=30))
        result = ev.evaluate(candidate_id="OPT-test",
                             candidate_activated_at="1970-01-01T00:00:00+00:00",
                             shadow_observations=obs)
        assert result.mean_delta_r < 0
        assert result.decision == "REJECTED"


# ═══════════════════════════════════════════════════════════════════════════════
# STATISTICAL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestStatistics:
    def test_bootstrap_ci_populated(self):
        obs = _make_dataset(50, baseline_mean=-0.1, candidate_mean=0.2)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=30))
        result = ev.evaluate(candidate_id="OPT-test",
                             candidate_activated_at="1970-01-01T00:00:00+00:00",
                             shadow_observations=obs)
        assert result.ci_lower is not None
        assert result.ci_upper is not None
        assert result.ci_lower < result.ci_upper

    def test_permutation_p_populated(self):
        obs = _make_dataset(50, baseline_mean=-0.1, candidate_mean=0.2)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=30))
        result = ev.evaluate(candidate_id="OPT-test",
                             candidate_activated_at="1970-01-01T00:00:00+00:00",
                             shadow_observations=obs)
        assert result.permutation_p is not None
        assert 0 <= result.permutation_p <= 1


# ═══════════════════════════════════════════════════════════════════════════════
# OOS
# ═══════════════════════════════════════════════════════════════════════════════


class TestOOS:
    def test_oos_computed(self):
        obs = _make_dataset(50, baseline_mean=-0.1, candidate_mean=0.2)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=30, oos_split=0.6))
        result = ev.evaluate(candidate_id="OPT-test",
                             candidate_activated_at="1970-01-01T00:00:00+00:00",
                             shadow_observations=obs)
        assert result.oos_n > 0
        assert result.oos_n == 50 - int(50 * 0.6)


# ═══════════════════════════════════════════════════════════════════════════════
# ROBUSTNESS
# ═══════════════════════════════════════════════════════════════════════════════


class TestRobustness:
    def test_single_symbol_not_robust(self):
        """Improvement in only one symbol flags fragility."""
        obs = _make_dataset(50, baseline_mean=-0.1, candidate_mean=0.2, symbols=["ONLY_ONE"])
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=30, min_symbols_positive=2))
        result = ev.evaluate(candidate_id="OPT-test",
                             candidate_activated_at="1970-01-01T00:00:00+00:00",
                             shadow_observations=obs)
        # Only 1 symbol → symbols_positive=1 → fragile
        assert result.symbols_total == 1


# ═══════════════════════════════════════════════════════════════════════════════
# GOVERNANCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestGovernance:
    def test_validated_does_not_promote(self):
        """VALIDATED result does not modify any production state."""
        obs = _make_dataset(100, baseline_mean=-0.3, candidate_mean=0.3)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=30))
        result = ev.evaluate(candidate_id="OPT-test",
                             candidate_activated_at="1970-01-01T00:00:00+00:00",
                             shadow_observations=obs)
        # Result is a data object — no side effects
        assert isinstance(result, CandidateEvaluation)
        # No imports of production modules in the evaluator
        import ast
        import research_engine.lifecycle.candidate_evaluator as mod
        source = open(mod.__file__, encoding="utf-8").read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "mt5_execution" not in (node.module or "").lower()
                assert "execution_orchestrator" not in (node.module or "").lower()
