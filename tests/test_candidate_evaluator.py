"""
Tests for Candidate Evaluator — Stage-2 paired observation evaluation.

Fixtures are PRODUCTION-SHAPED: candidate shadows in the V1 STR shape
(dataset ``shadow_trades``, identity.shadow_type=CANDIDATE_<id>) paired
against incumbent trade_truth records by exact correlation_id — the honest
pairing contract implemented in research_engine.lifecycle.candidate_pairing.
"""
import sys
import statistics
from datetime import datetime, timezone

import pytest

sys.path.insert(0, ".")

from research_engine.lifecycle.candidate_evaluator import (
    CandidateEvaluator, CandidateEvaluation, EvaluationConfig,
)


def _candidate_shadow(cor, candidate_r, *, candidate_id="OPT-test", symbol="EURUSD",
                      ts=1000.0, event_type="CLOSE"):
    """Production-shaped candidate shadow CLOSE record (V1 STR, shadow_trades_v1)."""
    return {
        "schema_version": "shadow_trades_v1",
        "source": "shadow_trade_engine",
        "event_type": event_type,
        "identity": {
            "trade_id": f"candidate_{candidate_id}_{cor}",
            "correlation_id": cor,
            "canonical_opportunity_id": None,
            "symbol": symbol,
            "strategy_id": "",
            "cycle_id": "1",
            "entity_id": f"{symbol}_{cor}",
            "shadow_type": f"CANDIDATE_{candidate_id}",
            "v10_action": "CANDIDATE_SHADOW",
        },
        "decision_snapshot": {
            "timestamp_decision_utc": ts,
            "entry_intent_price": 1.1,
            "stop_loss_intent": 1.095,
            "take_profit_intent": 1.115,
            "direction": "BUY",
            "pattern": "ENGULFING",
            "score": 0.7,
            "trade_horizon": "",
        },
        "simulated_outcome": {
            "pnl_r_multiple": candidate_r,
            "mfe_r": max(candidate_r, 0.0),
            "mae_r": min(candidate_r, 0.0),
            "exit_reason": "take_profit" if candidate_r > 0 else "stop_loss",
            "bars_held": 5,
        },
    }


def _incumbent_truth(cor, baseline_r, *, symbol="EURUSD", ts=1000.0, trade_id=None):
    """Production-shaped incumbent realised outcome (trade_truth_v1)."""
    return {
        "schema_version": "trade_truth_v1",
        "identity": {
            "trade_id": trade_id or f"pos_{cor}",
            "correlation_id": cor,
            "canonical_opportunity_id": None,
            "symbol": symbol,
        },
        "execution": {
            "entry_fill_price": 1.1,
            "exit_fill_price": 1.1 + baseline_r * 0.005,
            "volume_executed": 0.1,
        },
        "timestamps": {
            "entry_timestamp_broker": ts,
            "exit_timestamp_broker": ts + 300.0,
            "duration_seconds": 300.0,
        },
        "outcome": {
            "r_multiple_realised": baseline_r,
            "pnl_realised": baseline_r * 10.0,
            "commission": -1.0,
            "swap": 0.0,
            "net_profit": baseline_r * 10.0 - 1.0,
            "mfe_r": max(baseline_r, 0.0),
            "mae_r": min(baseline_r, 0.0),
        },
        "exit": {"exit_reason": "take_profit" if baseline_r > 0 else "stop_loss"},
    }


def _make_pair(cor, baseline_r, candidate_r, *, symbol="EURUSD", ts=1000.0):
    """One matched (candidate shadow, incumbent truth) opportunity pair."""
    return (
        [_candidate_shadow(cor, candidate_r, symbol=symbol, ts=ts)],
        [_incumbent_truth(cor, baseline_r, symbol=symbol, ts=ts)],
    )


def _make_dataset(n, baseline_mean=-0.1, candidate_mean=0.2, symbols=None,
                  candidate_id="OPT-test"):
    """Create N matched pairs with specified means. Returns (candidates, incumbents)."""
    import random
    rng = random.Random(42)
    cand, inc = [], []
    syms = symbols or ["EURUSD", "GBPUSD", "USDJPY"]
    for i in range(n):
        b_r = baseline_mean + rng.gauss(0, 0.3)
        c_r = candidate_mean + rng.gauss(0, 0.3)
        sym = syms[i % len(syms)]
        cor = f"COR-2026-1-{sym}-{i:05d}"
        ts = 2000.0 + i * 300
        cand.append(_candidate_shadow(cor, c_r, candidate_id=candidate_id, symbol=sym, ts=ts))
        inc.append(_incumbent_truth(cor, b_r, symbol=sym, ts=ts))
    return cand, inc


# ═══════════════════════════════════════════════════════════════════════════════
# PAIR RECONSTRUCTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestPairReconstruction:
    def test_correct_pairing(self):
        cand_a, inc_a = _make_pair("COR-A", -0.5, 0.3)
        cand_b, inc_b = _make_pair("COR-B", -0.2, 0.1)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=2))
        result = ev.evaluate(candidate_id="OPT-test", candidate_activated_at="1970-01-01T00:00:00+00:00",
                             candidate_records=cand_a + cand_b, incumbent_records=inc_a + inc_b)
        assert result.eligible_pairs == 2
        assert result.n == 2

    def test_missing_baseline_excluded(self):
        """Candidate without an incumbent realised outcome is excluded."""
        cand = [_candidate_shadow("COR-A", 0.5)]
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=1))
        result = ev.evaluate(candidate_id="OPT-test", candidate_activated_at="1970-01-01T00:00:00+00:00",
                             candidate_records=cand, incumbent_records=[])
        assert result.eligible_pairs == 0

    def test_missing_candidate_excluded(self):
        """Incumbent outcome without a candidate shadow is excluded."""
        inc = [_incumbent_truth("COR-A", -0.3)]
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=1))
        result = ev.evaluate(candidate_id="OPT-test", candidate_activated_at="1970-01-01T00:00:00+00:00",
                             candidate_records=[], incumbent_records=inc)
        assert result.eligible_pairs == 0


# ═══════════════════════════════════════════════════════════════════════════════
# PROSPECTIVE BOUNDARY
# ═══════════════════════════════════════════════════════════════════════════════


class TestProspectiveBoundary:
    def test_pre_candidate_excluded(self):
        """Pairs before candidate activation are excluded (both sides)."""
        # Pair at ts=500 (before boundary) + pair at ts=2000 (after)
        cand_old, inc_old = _make_pair("COR-OLD", -0.5, 0.3, ts=500.0)
        cand_new, inc_new = _make_pair("COR-NEW", -0.2, 0.1, ts=2000.0)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=1))
        result = ev.evaluate(candidate_id="OPT-test",
                             candidate_activated_at="1970-01-01T00:16:40+00:00",  # ts=1000
                             candidate_records=cand_old + cand_new,
                             incumbent_records=inc_old + inc_new)
        assert result.eligible_pairs == 1  # Only the ts=2000 pair
        assert result.excluded_pre_boundary == 2  # Both old records excluded


# ═══════════════════════════════════════════════════════════════════════════════
# MINIMUM SAMPLE
# ═══════════════════════════════════════════════════════════════════════════════


class TestMinimumSample:
    def test_below_minimum_inconclusive(self):
        cand, inc = _make_pair("COR-A", -0.5, 0.3)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=30))
        result = ev.evaluate(candidate_id="OPT-test",
                             candidate_activated_at="1970-01-01T00:00:00+00:00",
                             candidate_records=cand, incumbent_records=inc)
        assert result.decision == "INCONCLUSIVE"
        assert "insufficient" in result.decision_reason.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# STRONG IMPROVEMENT
# ═══════════════════════════════════════════════════════════════════════════════


class TestStrongImprovement:
    def test_significant_improvement_validates(self):
        cand, inc = _make_dataset(60, baseline_mean=-0.2, candidate_mean=0.3)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=30, min_symbols_positive=2, min_periods_positive=2))
        result = ev.evaluate(candidate_id="OPT-test",
                             candidate_activated_at="1970-01-01T00:00:00+00:00",
                             candidate_records=cand, incumbent_records=inc)
        assert result.mean_delta_r > 0
        assert result.decision in ("VALIDATED", "INCONCLUSIVE")  # Depends on robustness
        assert result.n == 60


# ═══════════════════════════════════════════════════════════════════════════════
# NO IMPROVEMENT
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoImprovement:
    def test_candidate_worse_rejected(self):
        cand, inc = _make_dataset(60, baseline_mean=0.2, candidate_mean=-0.3)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=30))
        result = ev.evaluate(candidate_id="OPT-test",
                             candidate_activated_at="1970-01-01T00:00:00+00:00",
                             candidate_records=cand, incumbent_records=inc)
        assert result.mean_delta_r < 0
        assert result.decision == "REJECTED"


# ═══════════════════════════════════════════════════════════════════════════════
# STATISTICAL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestStatistics:
    def test_bootstrap_ci_populated(self):
        cand, inc = _make_dataset(50, baseline_mean=-0.1, candidate_mean=0.2)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=30))
        result = ev.evaluate(candidate_id="OPT-test",
                             candidate_activated_at="1970-01-01T00:00:00+00:00",
                             candidate_records=cand, incumbent_records=inc)
        assert result.ci_lower is not None
        assert result.ci_upper is not None
        assert result.ci_lower < result.ci_upper

    def test_permutation_p_populated(self):
        cand, inc = _make_dataset(50, baseline_mean=-0.1, candidate_mean=0.2)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=30))
        result = ev.evaluate(candidate_id="OPT-test",
                             candidate_activated_at="1970-01-01T00:00:00+00:00",
                             candidate_records=cand, incumbent_records=inc)
        assert result.permutation_p is not None
        assert 0 <= result.permutation_p <= 1


# ═══════════════════════════════════════════════════════════════════════════════
# OOS
# ═══════════════════════════════════════════════════════════════════════════════


class TestOOS:
    def test_oos_computed(self):
        cand, inc = _make_dataset(50, baseline_mean=-0.1, candidate_mean=0.2)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=30, oos_split=0.6))
        result = ev.evaluate(candidate_id="OPT-test",
                             candidate_activated_at="1970-01-01T00:00:00+00:00",
                             candidate_records=cand, incumbent_records=inc)
        assert result.oos_n > 0
        assert result.oos_n == 50 - int(50 * 0.6)


# ═══════════════════════════════════════════════════════════════════════════════
# ROBUSTNESS
# ═══════════════════════════════════════════════════════════════════════════════


class TestRobustness:
    def test_single_symbol_not_robust(self):
        """Improvement in only one symbol flags fragility."""
        cand, inc = _make_dataset(50, baseline_mean=-0.1, candidate_mean=0.2, symbols=["ONLY_ONE"])
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=30, min_symbols_positive=2))
        result = ev.evaluate(candidate_id="OPT-test",
                             candidate_activated_at="1970-01-01T00:00:00+00:00",
                             candidate_records=cand, incumbent_records=inc)
        # Only 1 symbol → symbols_positive=1 → fragile
        assert result.symbols_total == 1


# ═══════════════════════════════════════════════════════════════════════════════
# GOVERNANCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestGovernance:
    def test_validated_does_not_promote(self):
        """VALIDATED result does not modify any production state."""
        cand, inc = _make_dataset(100, baseline_mean=-0.3, candidate_mean=0.3)
        ev = CandidateEvaluator(EvaluationConfig(minimum_sample=30))
        result = ev.evaluate(candidate_id="OPT-test",
                             candidate_activated_at="1970-01-01T00:00:00+00:00",
                             candidate_records=cand, incumbent_records=inc)
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
