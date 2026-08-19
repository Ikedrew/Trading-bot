"""
Synthetic End-to-End Candidate Lifecycle Test.

Proves the complete autonomous flow:
    PROPOSED → activation → SHADOW_TESTING → paired observations → minimum N → evaluation → decision

Three scenarios:
    1. Positive candidate → READY_FOR_REVIEW (human governance awaits)
    2. Negative candidate → REJECTED
    3. Insufficient evidence → remains SHADOW_TESTING (INCONCLUSIVE)

This test uses ONLY synthetic data. It does NOT:
    - Contact MT5 or any broker
    - Modify production configuration
    - Execute real trades
    - Import MT5Execution, RiskManager, or ExecutionOrchestrator
"""

import json
import pytest
from pathlib import Path
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


def _write_paired_observations(shadow_dir, candidate_id, n_pairs, candidate_better=True, mixed=False):
    """Write synthetic paired shadow observations to disk."""
    base_time = (datetime.now(timezone.utc) - timedelta(days=5)).timestamp()
    symbols = ["EURUSD", "GBPUSD", "USDJPY"]
    obs = []
    for i in range(n_pairs):
        sym = symbols[i % 3]
        entity_id = f"{sym}_{int(base_time + i * 300)}"
        entry_time = base_time + i * 300

        # Baseline observation (V10_PRIMARY)
        baseline_r = 0.3 if i % 3 == 0 else -0.8
        obs.append({
            "trade_id": f"shadow_cycle{i}_{sym}",
            "entity_id": entity_id,
            "shadow_type": "V10_PRIMARY",
            "entry_time": entry_time,
            "symbol": sym,
            "r_multiple": baseline_r,
            "direction": "BUY",
            "entry_price": 1.1000,
            "stop_loss": 1.0950,
            "take_profit": 1.1150,
        })

        # Candidate observation
        if mixed:
            # Tiny random-ish deltas — no signal
            candidate_r = baseline_r + (0.01 if i % 2 == 0 else -0.01)
        elif candidate_better:
            candidate_r = baseline_r + 1.5  # Strong improvement
        else:
            candidate_r = baseline_r - 1.5  # Strong harm

        obs.append({
            "trade_id": f"candidate_{candidate_id}_cycle{i}_{sym}",
            "entity_id": entity_id,
            "shadow_type": f"CANDIDATE_{candidate_id}",
            "entry_time": entry_time,
            "symbol": sym,
            "r_multiple": candidate_r,
            "direction": "SELL" if candidate_better else "BUY",
            "entry_price": 1.1000,
            "stop_loss": 1.1050 if candidate_better else 1.0950,
            "take_profit": 1.0850 if candidate_better else 1.1150,
        })

    # Write to JSONL
    for sym in symbols:
        sym_dir = shadow_dir / sym
        sym_dir.mkdir(parents=True, exist_ok=True)
        sym_obs = [o for o in obs if o["symbol"] == sym]
        if sym_obs:
            path = sym_dir / "2026-07-27.jsonl"
            with open(path, "w", encoding="utf-8") as f:
                for o in sym_obs:
                    f.write(json.dumps(o) + "\n")


class TestE2EPositiveCandidate:
    """Scenario 1: Candidate that consistently outperforms baseline → READY_FOR_REVIEW."""

    def test_full_lifecycle(self, tmp_path):
        reg_dir = str(tmp_path / "registry")
        shadow_dir = tmp_path / "shadows"

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

        # ─── STEP 3: Write sufficient paired observations ─────────────
        _write_paired_observations(shadow_dir, "OPT-POSITIVE", 60, candidate_better=True)

        # ─── STEP 4: Automatic evaluation → READY_FOR_REVIEW ─────────
        result = auto_evaluate_candidates(
            registry_dir=reg_dir,
            shadow_dir=str(shadow_dir),
            minimum_pairs=30,
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
        shadow_dir = tmp_path / "shadows"

        # ─── Create and activate ──────────────────────────────────────
        reg = CandidateRegistry(storage_dir=reg_dir)
        reg.create(_make_candidate("OPT-NEGATIVE"))

        activate_eligible_candidates(registry_dir=reg_dir)

        reg2 = CandidateRegistry(storage_dir=reg_dir)
        assert reg2.get("OPT-NEGATIVE").status == CandidateStatus.SHADOW_TESTING

        # ─── Write harmful observations ───────────────────────────────
        _write_paired_observations(shadow_dir, "OPT-NEGATIVE", 60, candidate_better=False)

        # ─── Auto-evaluate → REJECTED ────────────────────────────────
        result = auto_evaluate_candidates(
            registry_dir=reg_dir,
            shadow_dir=str(shadow_dir),
            minimum_pairs=30,
        )
        assert result.candidates_evaluated == 1
        assert result.evaluations[0]["decision"] == "REJECTED"

        reg3 = CandidateRegistry(storage_dir=reg_dir)
        c = reg3.get("OPT-NEGATIVE")
        assert c.status == CandidateStatus.REJECTED
        assert c.validation_history[-1].decision == "WORSENED"


class TestE2EInsufficientEvidence:
    """Scenario 3: Not enough pairs → INCONCLUSIVE, stays SHADOW_TESTING."""

    def test_full_lifecycle(self, tmp_path):
        reg_dir = str(tmp_path / "registry")
        shadow_dir = tmp_path / "shadows"

        # ─── Create and activate ──────────────────────────────────────
        reg = CandidateRegistry(storage_dir=reg_dir)
        reg.create(_make_candidate("OPT-INSUFF"))

        activate_eligible_candidates(registry_dir=reg_dir)

        reg2 = CandidateRegistry(storage_dir=reg_dir)
        assert reg2.get("OPT-INSUFF").status == CandidateStatus.SHADOW_TESTING

        # ─── Write INSUFFICIENT observations (10 < 30 minimum) ───────
        _write_paired_observations(shadow_dir, "OPT-INSUFF", 10, candidate_better=True)

        # ─── Auto-evaluate → insufficient, NOT evaluated ─────────────
        result = auto_evaluate_candidates(
            registry_dir=reg_dir,
            shadow_dir=str(shadow_dir),
            minimum_pairs=30,
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
        shadow_dir = tmp_path / "shadows"

        reg = CandidateRegistry(storage_dir=reg_dir)
        reg.create(_make_candidate("OPT-MIXED"))
        activate_eligible_candidates(registry_dir=reg_dir)

        # Write 40 pairs with tiny random deltas (no signal)
        _write_paired_observations(shadow_dir, "OPT-MIXED", 40, mixed=True)

        result = auto_evaluate_candidates(
            registry_dir=reg_dir,
            shadow_dir=str(shadow_dir),
            minimum_pairs=30,
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
