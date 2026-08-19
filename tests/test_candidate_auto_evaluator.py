"""
Tests for Candidate Auto-Evaluator.

Covers:
    - No SHADOW_TESTING candidates → no evaluations
    - Insufficient pairs → INCONCLUSIVE / candidates_insufficient
    - Sufficient pairs → evaluation triggered
    - Minimum N gate behaviour
    - Max evaluations per cycle respected
    - VALIDATED decision transitions candidate correctly
    - REJECTED decision transitions to FAILED_VALIDATION
    - INCONCLUSIVE leaves candidate in SHADOW_TESTING
    - Production safety: no imports of MT5Execution/RiskManager
    - End-to-end synthetic lifecycle: PROPOSED → SHADOW_TESTING → paired observations → eval
"""

import json
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

from research_engine.v10.candidates.candidate_registry import CandidateRegistry
from research_engine.v10.candidates.models import CandidateRecord, CandidateStatus
from research_engine.lifecycle.candidate_auto_evaluator import (
    auto_evaluate_candidates,
    _count_prospective_pairs,
    AutoEvaluationResult,
)


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

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


def _make_shadow_observation(
    entity_id: str,
    shadow_type: str,
    r_multiple: float,
    entry_time: float = 0.0,
    symbol: str = "EURUSD",
) -> dict:
    """Create a flat-schema shadow observation for testing."""
    if entry_time == 0.0:
        entry_time = datetime.now(timezone.utc).timestamp()
    return {
        "trade_id": f"shadow_{entity_id}_{shadow_type}",
        "entity_id": entity_id,
        "shadow_type": shadow_type,
        "entry_time": entry_time,
        "symbol": symbol,
        "r_multiple": r_multiple,
        "direction": "BUY",
        "entry_price": 1.1000,
        "stop_loss": 1.0950,
        "take_profit": 1.1150,
    }


def _write_observations(shadow_dir: Path, observations: list[dict]) -> None:
    """Write observations to a JSONL file in the shadow dir."""
    symbol_dir = shadow_dir / "EURUSD"
    symbol_dir.mkdir(parents=True, exist_ok=True)
    path = symbol_dir / "2026-07-27.jsonl"
    lines = [json.dumps(obs) for obs in observations]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _create_paired_observations(
    candidate_id: str,
    n_pairs: int,
    candidate_better: bool = True,
    base_time: float = 0.0,
) -> list[dict]:
    """Create n paired observations (V10_PRIMARY + CANDIDATE_{id})."""
    if base_time == 0.0:
        base_time = datetime.now(timezone.utc).timestamp()
    obs = []
    for i in range(n_pairs):
        entity = f"EURUSD_{int(base_time + i * 300)}"
        baseline_r = 0.5 if i % 3 == 0 else -1.0
        if candidate_better:
            candidate_r = baseline_r + 0.3 + (i % 5) * 0.1
        else:
            candidate_r = baseline_r - 0.5
        obs.append(_make_shadow_observation(entity, "V10_PRIMARY", baseline_r, base_time + i * 300))
        obs.append(_make_shadow_observation(entity, f"CANDIDATE_{candidate_id}", candidate_r, base_time + i * 300))
    return obs


# ═══════════════════════════════════════════════════════════════
# PAIR COUNTING
# ═══════════════════════════════════════════════════════════════

class TestPairCounting:
    def test_no_observations(self):
        count = _count_prospective_pairs(
            candidate_id="OPT-001",
            candidate_created_at="2020-01-01T00:00:00+00:00",
            observations=[],
        )
        assert count == 0

    def test_only_baseline(self):
        obs = [_make_shadow_observation("E1", "V10_PRIMARY", 0.5)]
        count = _count_prospective_pairs(
            candidate_id="OPT-001",
            candidate_created_at="2020-01-01T00:00:00+00:00",
            observations=obs,
        )
        assert count == 0

    def test_only_candidate(self):
        obs = [_make_shadow_observation("E1", "CANDIDATE_OPT-001", 0.5)]
        count = _count_prospective_pairs(
            candidate_id="OPT-001",
            candidate_created_at="2020-01-01T00:00:00+00:00",
            observations=obs,
        )
        assert count == 0

    def test_paired_counts_correctly(self):
        obs = _create_paired_observations("OPT-001", 15)
        count = _count_prospective_pairs(
            candidate_id="OPT-001",
            candidate_created_at="2020-01-01T00:00:00+00:00",
            observations=obs,
        )
        assert count == 15

    def test_prospective_boundary_excludes_old(self):
        """Observations before candidate created_at should be excluded."""
        old_time = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
        obs = _create_paired_observations("OPT-001", 10, base_time=old_time)

        # Boundary is after the observations
        recent_boundary = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        count = _count_prospective_pairs(
            candidate_id="OPT-001",
            candidate_created_at=recent_boundary,
            observations=obs,
        )
        assert count == 0

    def test_wrong_candidate_not_counted(self):
        """Observations for a different candidate should not count."""
        obs = _create_paired_observations("OPT-OTHER", 20)
        count = _count_prospective_pairs(
            candidate_id="OPT-001",
            candidate_created_at="2020-01-01T00:00:00+00:00",
            observations=obs,
        )
        assert count == 0


# ═══════════════════════════════════════════════════════════════
# AUTO-EVALUATION INTEGRATION
# ═══════════════════════════════════════════════════════════════

class TestAutoEvaluation:
    def test_no_shadow_testing_candidates(self, tmp_path):
        """No SHADOW_TESTING candidates → no evaluations."""
        reg = CandidateRegistry(storage_dir=str(tmp_path / "reg"))
        reg.create(_make_candidate("OPT-001", status=CandidateStatus.PROPOSED))

        result = auto_evaluate_candidates(
            registry_dir=str(tmp_path / "reg"),
            shadow_dir=str(tmp_path / "shadows"),
        )
        assert result.candidates_scanned == 0
        assert result.candidates_evaluated == 0

    def test_insufficient_pairs(self, tmp_path):
        """Candidate with < minimum pairs → not evaluated."""
        reg = CandidateRegistry(storage_dir=str(tmp_path / "reg"))
        reg.create(_make_candidate("OPT-001"))

        # Write only 5 pairs (below minimum of 30)
        shadow_dir = tmp_path / "shadows"
        obs = _create_paired_observations("OPT-001", 5)
        _write_observations(shadow_dir, obs)

        result = auto_evaluate_candidates(
            registry_dir=str(tmp_path / "reg"),
            shadow_dir=str(shadow_dir),
            minimum_pairs=30,
        )
        assert result.candidates_scanned == 1
        assert result.candidates_evaluated == 0
        assert result.candidates_insufficient == 1

    def test_sufficient_pairs_triggers_evaluation(self, tmp_path):
        """Candidate with >= minimum pairs → evaluation triggered."""
        created_at = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        reg = CandidateRegistry(storage_dir=str(tmp_path / "reg"))
        reg.create(_make_candidate("OPT-001", created_at=created_at))

        # Write 35 pairs (above minimum of 30)
        shadow_dir = tmp_path / "shadows"
        obs = _create_paired_observations("OPT-001", 35, candidate_better=True)
        _write_observations(shadow_dir, obs)

        result = auto_evaluate_candidates(
            registry_dir=str(tmp_path / "reg"),
            shadow_dir=str(shadow_dir),
            minimum_pairs=30,
        )
        assert result.candidates_scanned == 1
        assert result.candidates_evaluated == 1
        assert len(result.evaluations) == 1
        # Decision should be VALIDATED or INCONCLUSIVE (depends on stat tests)
        assert result.evaluations[0]["decision"] in ("VALIDATED", "INCONCLUSIVE", "REJECTED")

    def test_max_evaluations_respected(self, tmp_path):
        """Max evaluations per cycle is honoured."""
        created_at = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        reg = CandidateRegistry(storage_dir=str(tmp_path / "reg"))
        for i in range(4):
            reg.create(_make_candidate(f"OPT-{i:03d}", created_at=created_at))

        shadow_dir = tmp_path / "shadows"
        all_obs = []
        for i in range(4):
            all_obs.extend(_create_paired_observations(f"OPT-{i:03d}", 35, candidate_better=True))
        _write_observations(shadow_dir, all_obs)

        result = auto_evaluate_candidates(
            registry_dir=str(tmp_path / "reg"),
            shadow_dir=str(shadow_dir),
            minimum_pairs=30,
            max_evaluations=2,
        )
        assert result.candidates_evaluated <= 2

    def test_validated_candidate_transitions(self, tmp_path):
        """A strongly positive candidate should reach READY_FOR_REVIEW status."""
        created_at = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        reg = CandidateRegistry(storage_dir=str(tmp_path / "reg"))
        reg.create(_make_candidate("OPT-GOOD", created_at=created_at))

        # Create observations where candidate consistently outperforms baseline
        # across multiple symbols and time periods
        shadow_dir = tmp_path / "shadows"
        base_time = (datetime.now(timezone.utc) - timedelta(days=5)).timestamp()
        obs = []
        symbols = ["EURUSD", "GBPUSD", "USDJPY"]
        for i in range(60):
            sym = symbols[i % 3]
            entity = f"{sym}_{int(base_time + i * 300)}"
            obs.append(_make_shadow_observation(entity, "V10_PRIMARY", -0.3, base_time + i * 300, sym))
            obs.append(_make_shadow_observation(entity, "CANDIDATE_OPT-GOOD", 1.5, base_time + i * 300, sym))
        _write_observations(shadow_dir, obs)

        result = auto_evaluate_candidates(
            registry_dir=str(tmp_path / "reg"),
            shadow_dir=str(shadow_dir),
            minimum_pairs=30,
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

        # Candidate consistently worse than baseline across symbols/time
        shadow_dir = tmp_path / "shadows"
        base_time = (datetime.now(timezone.utc) - timedelta(days=5)).timestamp()
        obs = []
        symbols = ["EURUSD", "GBPUSD", "USDJPY"]
        for i in range(60):
            sym = symbols[i % 3]
            entity = f"{sym}_{int(base_time + i * 300)}"
            obs.append(_make_shadow_observation(entity, "V10_PRIMARY", 1.0, base_time + i * 300, sym))
            obs.append(_make_shadow_observation(entity, "CANDIDATE_OPT-BAD", -1.0, base_time + i * 300, sym))
        _write_observations(shadow_dir, obs)

        result = auto_evaluate_candidates(
            registry_dir=str(tmp_path / "reg"),
            shadow_dir=str(shadow_dir),
            minimum_pairs=30,
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
        shadow_dir = tmp_path / "shadows"
        base_time = (datetime.now(timezone.utc) - timedelta(days=5)).timestamp()
        obs = []
        for i in range(35):
            entity = f"EURUSD_{int(base_time + i * 300)}"
            # Alternate positive and negative deltas — no consistent effect
            baseline_r = 0.5 if i % 2 == 0 else -0.5
            candidate_r = baseline_r + (0.01 if i % 2 == 0 else -0.01)
            obs.append(_make_shadow_observation(entity, "V10_PRIMARY", baseline_r, base_time + i * 300))
            obs.append(_make_shadow_observation(entity, "CANDIDATE_OPT-MEH", candidate_r, base_time + i * 300))
        _write_observations(shadow_dir, obs)

        result = auto_evaluate_candidates(
            registry_dir=str(tmp_path / "reg"),
            shadow_dir=str(shadow_dir),
            minimum_pairs=30,
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
            shadow_dir=str(tmp_path / "also_nonexistent"),
        )
        assert isinstance(result, AutoEvaluationResult)
        assert result.candidates_scanned == 0


# ═══════════════════════════════════════════════════════════════
# PRODUCTION SAFETY
# ═══════════════════════════════════════════════════════════════

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
