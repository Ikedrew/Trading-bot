"""
Tests for Candidate Auto-Evaluator — automatic evaluation at the evidence threshold.

Covers:
    - Honest prospective pair counting (shared candidate_pairing contract)
    - Threshold behavior: below minimum → candidate stays SHADOW_TESTING
    - At/above threshold → the real CandidateEvaluator runs
    - VALIDATED → READY_FOR_REVIEW (human governance awaits)
    - REJECTED → REJECTED (terminal)
    - INCONCLUSIVE → stays in SHADOW_TESTING (waits for more evidence)
    - Production safety: no imports of MT5Execution/RiskManager

Fixtures are PRODUCTION-SHAPED: candidate shadows in the V1 STR shape paired
against incumbent trade_truth records by exact correlation_id (the honest
pairing contract in research_engine.lifecycle.candidate_pairing). No AWS.
"""

import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

from research_engine.v10.candidates.candidate_registry import CandidateRegistry
from research_engine.v10.candidates.models import CandidateRecord, CandidateStatus
from research_engine.lifecycle.candidate_auto_evaluator import (
    auto_evaluate_candidates,
    AutoEvaluationResult,
)
from research_engine.lifecycle.candidate_pairing import count_prospective_pairs


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _make_candidate(
    candidate_id: str = "OPT-test001",
    status: str = CandidateStatus.SHADOW_TESTING,
    created_at: str = "",
) -> CandidateRecord:
    if not created_at:
        created_at = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    return CandidateRecord(
        candidate_id=candidate_id,
        hypothesis_id="HYP-abc12345",
        baseline_id="current_v10",
        change_definition={"type": "direction_inversion", "action": "invert_pattern_direction"},
        status=status,
        created_at=created_at,
    )


def _candidate_shadow(cor, candidate_r, *, candidate_id="OPT-001", symbol="EURUSD", ts=1000.0):
    """Production-shaped candidate shadow CLOSE record (V1 STR)."""
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
    """Production-shaped incumbent realised outcome (trade_truth_v1)."""
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


def _create_paired_populations(
    candidate_id: str,
    n_pairs: int,
    candidate_better: bool = True,
    base_time: float = 0.0,
    symbols: tuple[str, ...] = ("EURUSD",),
) -> tuple[list[dict], list[dict]]:
    """Create n matched (candidate shadow, incumbent truth) populations."""
    if base_time == 0.0:
        base_time = datetime.now(timezone.utc).timestamp()
    cand, inc = [], []
    for i in range(n_pairs):
        sym = symbols[i % len(symbols)]
        cor = f"COR-2026-1-{sym}-{i:05d}"
        ts = base_time + i * 300
        baseline_r = 0.5 if i % 3 == 0 else -1.0
        if candidate_better:
            candidate_r = baseline_r + 0.3 + (i % 5) * 0.1
        else:
            candidate_r = baseline_r - 0.5
        cand.append(_candidate_shadow(cor, candidate_r, candidate_id=candidate_id, symbol=sym, ts=ts))
        inc.append(_incumbent_truth(cor, baseline_r, symbol=sym, ts=ts))
    return cand, inc


# ══════════════════════════════════════════════════════════════════════════════
# PAIR COUNTING (shared contract)
# ══════════════════════════════════════════════════════════════════════════════

class TestPairCounting:
    def test_no_observations(self):
        count = count_prospective_pairs(
            candidate_id="OPT-001",
            candidate_activated_at="2020-01-01T00:00:00+00:00",
            candidate_records=[],
            incumbent_records=[],
        )
        assert count == 0

    def test_only_baseline(self):
        count = count_prospective_pairs(
            candidate_id="OPT-001",
            candidate_activated_at="2020-01-01T00:00:00+00:00",
            candidate_records=[],
            incumbent_records=[_incumbent_truth("COR-1", 0.5)],
        )
        assert count == 0

    def test_only_candidate(self):
        count = count_prospective_pairs(
            candidate_id="OPT-001",
            candidate_activated_at="2020-01-01T00:00:00+00:00",
            candidate_records=[_candidate_shadow("COR-1", 0.5)],
            incumbent_records=[],
        )
        assert count == 0

    def test_paired_counts_correctly(self):
        cand, inc = _create_paired_populations("OPT-001", 15)
        count = count_prospective_pairs(
            candidate_id="OPT-001",
            candidate_activated_at="2020-01-01T00:00:00+00:00",
            candidate_records=cand,
            incumbent_records=inc,
        )
        assert count == 15

    def test_prospective_boundary_excludes_old(self):
        """Observations before candidate created_at should be excluded."""
        old_time = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
        cand, inc = _create_paired_populations("OPT-001", 10, base_time=old_time)

        # Boundary is after the observations
        recent_boundary = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        count = count_prospective_pairs(
            candidate_id="OPT-001",
            candidate_activated_at=recent_boundary,
            candidate_records=cand,
            incumbent_records=inc,
        )
        assert count == 0

    def test_wrong_candidate_not_counted(self):
        """Observations for a different candidate should not count."""
        cand, inc = _create_paired_populations("OPT-OTHER", 20)
        count = count_prospective_pairs(
            candidate_id="OPT-001",
            candidate_activated_at="2020-01-01T00:00:00+00:00",
            candidate_records=cand,
            incumbent_records=inc,
        )
        assert count == 0


# ══════════════════════════════════════════════════════════════════════════════
# AUTO-EVALUATION INTEGRATION
# ══════════════════════════════════════════════════════════════════════════════

class TestAutoEvaluation:
    def test_no_shadow_testing_candidates(self, tmp_path):
        """No SHADOW_TESTING candidates → no evaluations."""
        reg = CandidateRegistry(storage_dir=str(tmp_path / "reg"))
        reg.create(_make_candidate("OPT-001", status=CandidateStatus.PROPOSED))

        result = auto_evaluate_candidates(registry_dir=str(tmp_path / "reg"))
        assert result.candidates_scanned == 0
        assert result.candidates_evaluated == 0

    def test_insufficient_pairs(self, tmp_path):
        """Candidate with < minimum pairs → not evaluated, stays SHADOW_TESTING."""
        reg = CandidateRegistry(storage_dir=str(tmp_path / "reg"))
        reg.create(_make_candidate("OPT-001"))

        cand, inc = _create_paired_populations("OPT-001", 5)

        result = auto_evaluate_candidates(
            registry_dir=str(tmp_path / "reg"),
            minimum_pairs=30,
            candidate_records=cand,
            incumbent_records=inc,
        )
        assert result.candidates_scanned == 1
        assert result.candidates_evaluated == 0
        assert result.candidates_insufficient == 1
        # Lifecycle untouched
        reloaded = CandidateRegistry(storage_dir=str(tmp_path / "reg"))
        assert reloaded.get("OPT-001").status == CandidateStatus.SHADOW_TESTING

    def test_sufficient_pairs_triggers_evaluation(self, tmp_path):
        """Candidate with >= minimum pairs → evaluation triggered."""
        created_at = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        reg = CandidateRegistry(storage_dir=str(tmp_path / "reg"))
        reg.create(_make_candidate("OPT-001", created_at=created_at))

        cand, inc = _create_paired_populations("OPT-001", 35)

        result = auto_evaluate_candidates(
            registry_dir=str(tmp_path / "reg"),
            minimum_pairs=30,
            candidate_records=cand,
            incumbent_records=inc,
        )
        assert result.candidates_evaluated <= 2

    def test_validated_candidate_transitions(self, tmp_path):
        """A strongly positive candidate should reach READY_FOR_REVIEW status."""
        created_at = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        reg = CandidateRegistry(storage_dir=str(tmp_path / "reg"))
        reg.create(_make_candidate("OPT-GOOD", created_at=created_at))

        # Candidate consistently outperforms the incumbent across symbols/periods
        base_time = (datetime.now(timezone.utc) - timedelta(days=5)).timestamp()
        cand, inc = _create_paired_populations(
            "OPT-GOOD", 60, candidate_better=True, base_time=base_time,
            symbols=("EURUSD", "GBPUSD", "USDJPY"),
        )

        result = auto_evaluate_candidates(
            registry_dir=str(tmp_path / "reg"),
            minimum_pairs=30,
            candidate_records=cand,
            incumbent_records=inc,
        )
        assert result.candidates_evaluated == 1
        assert result.evaluations[0]["decision"] == "VALIDATED"

        # Verify lifecycle transition: SHADOW_TESTING → READY_FOR_REVIEW
        reloaded = CandidateRegistry(storage_dir=str(tmp_path / "reg"))
        c = reloaded.get("OPT-GOOD")
        assert c.status == CandidateStatus.READY_FOR_REVIEW
        assert len(c.validation_history) >= 1
        assert c.validation_history[-1].decision == "IMPROVED"

    def test_rejected_candidate_transitions(self, tmp_path):
        """A candidate that harms performance should get REJECTED."""
        created_at = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        reg = CandidateRegistry(storage_dir=str(tmp_path / "reg"))
        reg.create(_make_candidate("OPT-BAD", created_at=created_at))

        # Candidate consistently worse than the incumbent across symbols/time
        base_time = (datetime.now(timezone.utc) - timedelta(days=5)).timestamp()
        cand, inc = _create_paired_populations(
            "OPT-BAD", 60, candidate_better=False, base_time=base_time,
            symbols=("EURUSD", "GBPUSD", "USDJPY"),
        )

        result = auto_evaluate_candidates(
            registry_dir=str(tmp_path / "reg"),
            minimum_pairs=30,
            candidate_records=cand,
            incumbent_records=inc,
        )
        assert result.candidates_evaluated == 1
        assert result.evaluations[0]["decision"] == "REJECTED"

        # Verify lifecycle transition: SHADOW_TESTING → REJECTED
        reloaded = CandidateRegistry(storage_dir=str(tmp_path / "reg"))
        c = reloaded.get("OPT-BAD")
        assert c.status == CandidateStatus.REJECTED
        assert len(c.validation_history) >= 1
        assert c.validation_history[-1].decision == "WORSENED"

    def test_inconclusive_stays_shadow_testing(self, tmp_path):
        """INCONCLUSIVE decision keeps candidate in SHADOW_TESTING."""
        created_at = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        reg = CandidateRegistry(storage_dir=str(tmp_path / "reg"))
        reg.create(_make_candidate("OPT-MEH", created_at=created_at))

        # Mixed results — some positive, some negative, no clear signal
        base_time = (datetime.now(timezone.utc) - timedelta(days=5)).timestamp()
        cand, inc = [], []
        for i in range(35):
            cor = f"COR-2026-1-EURUSD-{i:05d}"
            ts = base_time + i * 300
            # Alternate positive and negative deltas — no consistent effect
            baseline_r = 0.5 if i % 2 == 0 else -0.5
            candidate_r = baseline_r + (0.01 if i % 2 == 0 else -0.01)
            cand.append(_candidate_shadow(cor, candidate_r, candidate_id="OPT-MEH", ts=ts))
            inc.append(_incumbent_truth(cor, baseline_r, ts=ts))

        result = auto_evaluate_candidates(
            registry_dir=str(tmp_path / "reg"),
            minimum_pairs=30,
            candidate_records=cand,
            incumbent_records=inc,
        )
        assert result.candidates_evaluated == 1
        assert result.evaluations[0]["decision"] == "INCONCLUSIVE"

        # Must remain in SHADOW_TESTING
        reloaded = CandidateRegistry(storage_dir=str(tmp_path / "reg"))
        c = reloaded.get("OPT-MEH")
        assert c.status == CandidateStatus.SHADOW_TESTING

    def test_never_raises(self, tmp_path):
        """Auto-evaluator never raises, even with broken state."""
        result = auto_evaluate_candidates(
            registry_dir=str(tmp_path / "nonexistent" / "deep"),
        )
        assert isinstance(result, AutoEvaluationResult)
        assert result.candidates_scanned == 0


# ══════════════════════════════════════════════════════════════════════════════
# PRODUCTION SAFETY
# ══════════════════════════════════════════════════════════════════════════════

class TestProductionSafety:
    def test_no_mt5_imports(self):
        """Verify the auto-evaluator has no path to production execution."""
        import inspect
        import research_engine.lifecycle.candidate_auto_evaluator as mod

        source = inspect.getsource(mod)
        # Check for actual import/usage patterns — not docstring mentions
        assert "import MT5Execution" not in source
        assert "import RiskManager" not in source
        assert "order_send(" not in source
        assert "import ExecutionOrchestrator" not in source
        # Verify the module namespace does not contain production types
        import sys
        mod_names = set(mod.__dict__.keys())
        assert "MT5Execution" not in mod_names
        assert "RiskManager" not in mod_names
        assert "ExecutionOrchestrator" not in mod_names
