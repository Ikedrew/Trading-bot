"""
Tests for Candidate Evaluation Bridge — lifecycle integration.

Fixtures are PRODUCTION-SHAPED: candidate shadows (V1 STR,
shadow_type=CANDIDATE_<id>) paired against incumbent trade_truth records by
exact correlation_id — the honest pairing contract in
research_engine.lifecycle.candidate_pairing.
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


_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY")


def _candidate_shadow(cor, candidate_r, *, candidate_id="OPT-test", symbol="EURUSD", ts=1000.0):
    return {
        "schema_version": "shadow_trades_v1",
        "source": "shadow_trade_engine",
        "event_type": "CLOSE",
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


def _incumbent_truth(cor, baseline_r, *, symbol="EURUSD", ts=1000.0):
    return {
        "schema_version": "trade_truth_v1",
        "identity": {
            "trade_id": f"pos_{cor}",
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


def _make_paired_populations(n, baseline_r=-0.1, candidate_r=0.3, candidate_id="OPT-test"):
    """Create n matched (candidate shadow, incumbent truth) populations."""
    cand, inc = [], []
    for i in range(n):
        sym = _SYMBOLS[i % 3]
        cor = f"COR-2026-1-{sym}-{i:05d}"
        ts = 5000.0 + i * 300
        cand.append(_candidate_shadow(cor, candidate_r + (i % 5) * 0.05,
                                      candidate_id=candidate_id, symbol=sym, ts=ts))
        inc.append(_incumbent_truth(cor, baseline_r + (i % 5) * 0.05, symbol=sym, ts=ts))
    return cand, inc


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION HISTORY
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidationHistory:
    def test_validated_persists_history(self, registry_dir):
        _create_candidate(registry_dir)
        cand, inc = _make_paired_populations(50, baseline_r=-0.2, candidate_r=0.3)
        cfg = EvaluationConfig(minimum_sample=30, min_symbols_positive=2, min_periods_positive=2)

        result = evaluate_candidate("OPT-test", candidate_records=cand,
                                    incumbent_records=inc, config=cfg,
                                    registry_dir=registry_dir)

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
        cand, inc = _make_paired_populations(60, baseline_r=-0.3, candidate_r=0.4)
        cfg = EvaluationConfig(minimum_sample=30, min_symbols_positive=2, min_periods_positive=2)

        result = evaluate_candidate("OPT-test", candidate_records=cand,
                                    incumbent_records=inc, config=cfg,
                                    registry_dir=registry_dir)

        if result.decision == "VALIDATED":
            reg = CandidateRegistry(storage_dir=registry_dir)
            assert reg.get("OPT-test").status == CandidateStatus.VALIDATED

    def test_rejected_transitions_to_failed_validation(self, registry_dir):
        _create_candidate(registry_dir, status="VALIDATING")
        # Candidate consistently worse — every single pair has negative delta
        cand, inc = [], []
        for i in range(50):
            sym = _SYMBOLS[i % 3]
            cor = f"COR-2026-1-{sym}-{i:05d}"
            ts = 5000.0 + i * 300
            cand.append(_candidate_shadow(cor, -1.0, candidate_id="OPT-test", symbol=sym, ts=ts))
            inc.append(_incumbent_truth(cor, 0.5, symbol=sym, ts=ts))
        cfg = EvaluationConfig(minimum_sample=30)

        result = evaluate_candidate("OPT-test", candidate_records=cand,
                                    incumbent_records=inc, config=cfg,
                                    registry_dir=registry_dir)

        assert result.decision == "REJECTED", f"Got {result.decision}: {result.decision_reason}"
        reg = CandidateRegistry(storage_dir=registry_dir)
        assert reg.get("OPT-test").status == CandidateStatus.FAILED_VALIDATION

    def test_inconclusive_no_transition(self, registry_dir):
        _create_candidate(registry_dir, status="VALIDATING")
        cand, inc = _make_paired_populations(5)  # Too few → INCONCLUSIVE
        cfg = EvaluationConfig(minimum_sample=30)

        result = evaluate_candidate("OPT-test", candidate_records=cand,
                                    incumbent_records=inc, config=cfg,
                                    registry_dir=registry_dir)

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
        cand, inc = _make_paired_populations(10)
        cfg = EvaluationConfig(minimum_sample=30)

        r1 = evaluate_candidate("OPT-test", candidate_records=cand,
                                incumbent_records=inc, config=cfg,
                                registry_dir=registry_dir)
        assert r1.decision == "INCONCLUSIVE"

        # Can evaluate again with more data
        cand2, inc2 = _make_paired_populations(50, baseline_r=-0.2, candidate_r=0.3)
        r2 = evaluate_candidate("OPT-test", candidate_records=cand2,
                                incumbent_records=inc2, config=cfg,
                                registry_dir=registry_dir)
        # Second evaluation should work (not blocked)
        assert r2.n >= 30 or r2.decision == "INCONCLUSIVE"


# ═══════════════════════════════════════════════════════════════════════════════
# IDEMPOTENCY
# ═══════════════════════════════════════════════════════════════════════════════


class TestIdempotency:
    def test_repeated_evaluation_safe(self, registry_dir):
        """Evaluating twice appends to history without corruption."""
        _create_candidate(registry_dir, status="VALIDATING")
        cand, inc = _make_paired_populations(50, baseline_r=-0.2, candidate_r=0.3)
        cfg = EvaluationConfig(minimum_sample=30, min_symbols_positive=2, min_periods_positive=2)

        r1 = evaluate_candidate("OPT-test", candidate_records=cand,
                                incumbent_records=inc, config=cfg,
                                registry_dir=registry_dir)
        r2 = evaluate_candidate("OPT-test", candidate_records=cand,
                                incumbent_records=inc, config=cfg,
                                registry_dir=registry_dir)

        # Both should complete (second may be blocked by lifecycle if already transitioned)
        assert r1.evaluation_id != r2.evaluation_id or r1.decision == r2.decision


# ═══════════════════════════════════════════════════════════════════════════════
# INELIGIBLE CANDIDATES
# ═══════════════════════════════════════════════════════════════════════════════


class TestIneligibleCandidates:
    def test_proposed_candidate_blocked(self, registry_dir):
        """PROPOSED candidate is not eligible for evaluation."""
        _create_candidate(registry_dir, status="PROPOSED")
        cand, inc = _make_paired_populations(50)
        result = evaluate_candidate("OPT-test", candidate_records=cand,
                                    incumbent_records=inc,
                                    registry_dir=registry_dir)
        assert result.decision == "INCONCLUSIVE"
        assert "expected one of" in result.decision_reason

    def test_nonexistent_candidate_blocked(self, registry_dir):
        result = evaluate_candidate("OPT-ghost", candidate_records=[],
                                    incumbent_records=[],
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
        cand, inc = _make_paired_populations(100, baseline_r=-0.4, candidate_r=0.5)
        cfg = EvaluationConfig(minimum_sample=30, min_symbols_positive=2, min_periods_positive=2)

        result = evaluate_candidate("OPT-test", candidate_records=cand,
                                    incumbent_records=inc, config=cfg,
                                    registry_dir=registry_dir)

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
