"""
Synthetic End-to-End Candidate Lifecycle Test.

Proves the complete autonomous flow:
    PROPOSED → activation → SHADOW_TESTING → matched prospective pairs →
    minimum N → evaluation → decision

Four scenarios:
    1. Positive candidate → READY_FOR_REVIEW (human governance awaits)
    2. Negative candidate → REJECTED
    3. Insufficient evidence → remains SHADOW_TESTING (not evaluated)
    4. Mixed/noisy evidence → INCONCLUSIVE, remains SHADOW_TESTING

Fixtures are PRODUCTION-SHAPED: candidate shadows in the V1 STR shape
(dataset shadow_trades, identity.shadow_type=CANDIDATE_<id>) paired against
incumbent trade_truth records by exact correlation_id — the honest pairing
contract in research_engine.lifecycle.candidate_pairing.

This test uses ONLY synthetic data. It does NOT:
    - Contact MT5 or any broker
    - Modify production configuration
    - Execute real trades
    - Import MT5Execution, RiskManager, or ExecutionOrchestrator
"""

import pytest
from datetime import datetime, timezone, timedelta

from research_engine.v10.candidates.candidate_registry import CandidateRegistry
from research_engine.v10.candidates.models import CandidateRecord, CandidateStatus
from research_engine.lifecycle.candidate_activation_gate import activate_eligible_candidates
from research_engine.lifecycle.candidate_auto_evaluator import auto_evaluate_candidates


def _make_candidate(candidate_id, change_type="direction_inversion", created_at=None):
    """Create a PROPOSED candidate as if from a VALIDATED research conclusion."""
    if created_at is None:
        created_at = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    return CandidateRecord(
        candidate_id=candidate_id,
        hypothesis_id="HYP-e2e-test-12345678",
        baseline_id="current_v10",
        component="DIRECTION_INVERSION",
        description="E2E test candidate",
        change_definition={"type": change_type, "action": "invert_pattern_direction"},
        expected_outcome="+0.15R/trade",
        risk_level="MEDIUM",
        status=CandidateStatus.PROPOSED,
        created_at=created_at,
    )


def _candidate_shadow(cor, candidate_r, *, candidate_id, symbol, ts):
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


def _incumbent_truth(cor, baseline_r, *, symbol, ts):
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


def _create_paired_populations(candidate_id, n_pairs, candidate_better=True, mixed=False):
    """Build n matched (candidate shadow, incumbent truth) populations."""
    base_time = (datetime.now(timezone.utc) - timedelta(days=5)).timestamp()
    symbols = ["EURUSD", "GBPUSD", "USDJPY"]
    cand, inc = [], []
    for i in range(n_pairs):
        sym = symbols[i % 3]
        cor = f"COR-2026-1-{sym}-{i:05d}"
        ts = base_time + i * 300

        # Incumbent (deployed logic) realised outcome
        baseline_r = 0.3 if i % 3 == 0 else -0.8

        # Candidate prospective outcome
        if mixed:
            # Tiny deltas — no signal
            candidate_r = baseline_r + (0.01 if i % 2 == 0 else -0.01)
        elif candidate_better:
            candidate_r = baseline_r + 1.5  # Strong improvement
        else:
            candidate_r = baseline_r - 1.5  # Strong harm

        cand.append(_candidate_shadow(cor, candidate_r, candidate_id=candidate_id,
                                      symbol=sym, ts=ts))
        inc.append(_incumbent_truth(cor, baseline_r, symbol=sym, ts=ts))
    return cand, inc


class TestE2EPositiveCandidate:
    """Scenario 1: Candidate that consistently outperforms the incumbent → READY_FOR_REVIEW."""

    def test_full_lifecycle(self, tmp_path):
        reg_dir = str(tmp_path / "registry")

        # ─── STEP 1: Create candidate in PROPOSED ─────────────────────
        reg = CandidateRegistry(storage_dir=reg_dir)
        reg.create(_make_candidate("OPT-POSITIVE"))

        c = reg.get("OPT-POSITIVE")
        assert c.status == CandidateStatus.PROPOSED

        # ─── STEP 2: Automatic activation → SHADOW_TESTING ───────────
        activation = activate_eligible_candidates(registry_dir=reg_dir)
        assert activation.candidates_activated == 1
        assert activation.activations[0]["candidate_id"] == "OPT-POSITIVE"

        reg2 = CandidateRegistry(storage_dir=reg_dir)
        c = reg2.get("OPT-POSITIVE")
        assert c.status == CandidateStatus.SHADOW_TESTING

        # ─── STEP 3: Accumulate sufficient matched prospective pairs ──
        cand, inc = _create_paired_populations("OPT-POSITIVE", 60, candidate_better=True)

        # ─── STEP 4: Automatic evaluation → READY_FOR_REVIEW ─────────
        result = auto_evaluate_candidates(
            registry_dir=reg_dir,
            minimum_pairs=30,
            candidate_records=cand,
            incumbent_records=inc,
        )
        assert result.candidates_evaluated == 1
        assert result.evaluations[0]["decision"] == "VALIDATED"

        reg3 = CandidateRegistry(storage_dir=reg_dir)
        c = reg3.get("OPT-POSITIVE")
        assert c.status == CandidateStatus.READY_FOR_REVIEW
        assert len(c.validation_history) >= 1
        assert c.validation_history[-1].decision == "IMPROVED"
        assert c.validation_history[-1].sample_size >= 30


class TestE2ENegativeCandidate:
    """Scenario 2: Candidate that harms performance → REJECTED."""

    def test_full_lifecycle(self, tmp_path):
        reg_dir = str(tmp_path / "registry")

        # ─── Create and activate ──────────────────────────────────────
        reg = CandidateRegistry(storage_dir=reg_dir)
        reg.create(_make_candidate("OPT-NEGATIVE"))

        activate_eligible_candidates(registry_dir=reg_dir)

        reg2 = CandidateRegistry(storage_dir=reg_dir)
        assert reg2.get("OPT-NEGATIVE").status == CandidateStatus.SHADOW_TESTING

        # ─── Accumulate harmful matched pairs ────────────────────────
        cand, inc = _create_paired_populations("OPT-NEGATIVE", 60, candidate_better=False)

        # ─── Auto-evaluate → REJECTED ────────────────────────────────
        result = auto_evaluate_candidates(
            registry_dir=reg_dir,
            minimum_pairs=30,
            candidate_records=cand,
            incumbent_records=inc,
        )
        assert result.candidates_evaluated == 1
        assert result.evaluations[0]["decision"] == "REJECTED"

        reg3 = CandidateRegistry(storage_dir=reg_dir)
        c = reg3.get("OPT-NEGATIVE")
        assert c.status == CandidateStatus.REJECTED
        assert c.validation_history[-1].decision == "WORSENED"


class TestE2EInsufficientEvidence:
    """Scenario 3: Not enough pairs → not evaluated, stays SHADOW_TESTING."""

    def test_full_lifecycle(self, tmp_path):
        reg_dir = str(tmp_path / "registry")

        # ─── Create and activate ──────────────────────────────────────
        reg = CandidateRegistry(storage_dir=reg_dir)
        reg.create(_make_candidate("OPT-INSUFF"))

        activate_eligible_candidates(registry_dir=reg_dir)

        reg2 = CandidateRegistry(storage_dir=reg_dir)
        assert reg2.get("OPT-INSUFF").status == CandidateStatus.SHADOW_TESTING

        # ─── Accumulate INSUFFICIENT pairs (10 < 30 minimum) ─────────
        cand, inc = _create_paired_populations("OPT-INSUFF", 10, candidate_better=True)

        # ─── Auto-evaluate → insufficient, NOT evaluated ─────────────
        result = auto_evaluate_candidates(
            registry_dir=reg_dir,
            minimum_pairs=30,
            candidate_records=cand,
            incumbent_records=inc,
        )
        assert result.candidates_evaluated == 0
        assert result.candidates_insufficient == 1

        # ─── Candidate remains SHADOW_TESTING ─────────────────────────
        reg3 = CandidateRegistry(storage_dir=reg_dir)
        c = reg3.get("OPT-INSUFF")
        assert c.status == CandidateStatus.SHADOW_TESTING
        assert len(c.validation_history) == 0


class TestE2EMixedEvidence:
    """Scenario 4: Mixed/noisy evidence → INCONCLUSIVE, stays SHADOW_TESTING."""

    def test_full_lifecycle(self, tmp_path):
        reg_dir = str(tmp_path / "registry")

        reg = CandidateRegistry(storage_dir=reg_dir)
        reg.create(_make_candidate("OPT-MIXED"))
        activate_eligible_candidates(registry_dir=reg_dir)

        # 40 matched pairs with tiny deltas (no signal)
        cand, inc = _create_paired_populations("OPT-MIXED", 40, mixed=True)

        result = auto_evaluate_candidates(
            registry_dir=reg_dir,
            minimum_pairs=30,
            candidate_records=cand,
            incumbent_records=inc,
        )
        assert result.candidates_evaluated == 1
        assert result.evaluations[0]["decision"] == "INCONCLUSIVE"

        reg3 = CandidateRegistry(storage_dir=reg_dir)
        c = reg3.get("OPT-MIXED")
        assert c.status == CandidateStatus.SHADOW_TESTING


class TestE2EProductionSafety:
    """Verify the E2E flow has no production execution path."""

    def test_no_broker_imports_in_lifecycle_modules(self):
        """All candidate lifecycle modules must be free of production imports."""
        import ast
        import os

        target_files = [
            'research_engine/lifecycle/candidate_activation_gate.py',
            'research_engine/lifecycle/candidate_auto_evaluator.py',
            'research_engine/lifecycle/candidate_evaluation_bridge.py',
            'research_engine/lifecycle/candidate_shadow_hook.py',
            'research_engine/lifecycle/candidate_evaluator.py',
            'research_engine/lifecycle/candidate_pairing.py',
        ]
        forbidden = {'MT5Execution', 'RiskManager', 'ExecutionOrchestrator', 'order_send'}

        for fpath in target_files:
            assert os.path.exists(fpath), f"Missing: {fpath}"
            with open(fpath, 'r', encoding='utf-8') as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ''
                    names = [a.name for a in (node.names or [])]
                    for fb in forbidden:
                        assert fb not in module, f"{fpath} imports forbidden module containing {fb}"
                        assert fb not in names, f"{fpath} imports forbidden name {fb}"
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        for fb in forbidden:
                            assert fb not in alias.name, f"{fpath} imports {alias.name} containing {fb}"
