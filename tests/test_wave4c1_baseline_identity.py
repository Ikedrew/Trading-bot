"""
Wave 4C.1 — Canonical Baseline Identity: focused proof tests.

Identity and provenance ONLY. All persistence uses temporary stores
(tmp_path + monkeypatch); production data/baselines/ state is never touched
(conftest additionally redirects the baseline authority for every test).

Covered proofs:
    1.  Stable/collision-safe snapshot identity (equivalent state → same ID)
    2.  Snapshot persistence (save → restart registry → load, identity intact)
    3.  Bootstrap (no active baseline → persisted snapshot + active pointer)
    4.  Bootstrap idempotency (no duplicate equivalent baselines)
    5.  Active pointer durability across authority re-instantiation
    6.  A → B: active=B, previous=A
    7.  Idempotent reactivation of B keeps previous=A
    8.  Activation of an unknown snapshot fails closed, pointer unchanged
    9.  CandidateRecord receives the REAL persisted active baseline (never
        "current_v10") via the production orchestrator path
    10. Candidate baseline immutability across active-baseline changes
    11. Persisted evaluation retains candidate_id/baseline_id/config_hash/
        evaluation_id
    12. Stale baseline cannot silently promote to READY_FOR_REVIEW
    13. Config drift cannot silently promote to READY_FOR_REVIEW
    14. Zero trading/config mutation on the identity path

Malformed-pointer fail-closed behaviour is also covered.
"""
import ast
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _s3_fake import FakeS3, install_fake_s3, reset_fake_s3

import research_engine.v10.baselines.baseline_authority as baseline_authority
from research_engine.v10.baselines.baseline_authority import (
    BaselineMissingError,
    BaselineStateError,
    ensure_active_baseline,
    get_active,
    load_baseline_snapshot,
    set_active,
)
from research_engine.v10.baselines.models import BaselineSnapshot
from research_engine.v10.baselines.snapshot_builder import SnapshotBuilder
from research_engine.v10.baselines.snapshot_registry import SnapshotRegistry
from research_engine.lifecycle.candidate_evaluation_bridge import evaluate_candidate
from research_engine.lifecycle.candidate_evaluator import EvaluationConfig
from research_engine.v10.candidates.models import CandidateRecord, CandidateStatus
from research_engine.v10.candidates.candidate_registry import CandidateRegistry
from research_engine.lifecycle.candidate_shadow_hook import candidate_trade_id

# Fixed 4D.1 treatment digest embedded in fixture candidate trade_ids
# (production mint format: candidate_<id>_<cycle>_<symbol>_<tid>).
_TREATMENT_ID = "a4d2c0de4d2feed1"
from core.research_events import compute_config_hash


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES / HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _baseline_isolation(tmp_path, monkeypatch):
    """Baseline authority persistence on temporary stores only."""
    monkeypatch.setattr(
        baseline_authority, "_BASELINES_DIR", str(tmp_path / "baselines")
    )
    monkeypatch.setattr(
        baseline_authority, "_ACTIVE_POINTER_FILE",
        str(tmp_path / "baselines" / "active_baseline.json"),
    )


@pytest.fixture
def fake_universe():
    """Isolated fake S3 with a seeded research universe (empty is valid)."""
    fake = install_fake_s3()
    yield fake
    reset_fake_s3()


def _universe_record(r, pnl):
    return {"execution": {"r_multiple": r, "net_realised_pnl": pnl}}


def _tmp_registry():
    return SnapshotRegistry(baselines_dir=baseline_authority._BASELINES_DIR)


def _second_state_snapshot(seed=2):
    """
    Build + persist a second, DISTINCT baseline snapshot (state drift).

    Installs a FRESH fake S3 source first: the sanctioned S3 research data
    source keeps a run-level in-memory cache per source instance, so changed
    universe content is only observed through a newly installed source.
    """
    fake = install_fake_s3()
    fake.add_artifact(
        "research_universe",
        [_universe_record(0.1 * seed, 1.0 * seed) for _ in range(3)],
    )
    snapshot = SnapshotBuilder().build()
    _tmp_registry().save(snapshot)
    return snapshot


# ═══════════════════════════════════════════════════════════════════════════════
# 1-2. SNAPSHOT IDENTITY + PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════

class TestSnapshotIdentity:
    def test_equivalent_state_stable_identity(self, fake_universe):
        """Equivalent captured state → identical deterministic snapshot ID."""
        s1 = SnapshotBuilder().build()
        s2 = SnapshotBuilder().build()
        assert s1.snapshot_id == s2.snapshot_id
        assert s1.identity_hash == s2.identity_hash
        assert s1.snapshot_id == f"V10_BASELINE_{s1.identity_hash}"
        assert len(s1.identity_hash) == 16  # collision-safe hash body, no clock
        assert s1.config_hash == compute_config_hash()  # shared primitive
        assert s1.config_hash != ""

    def test_different_state_different_identity(self, fake_universe):
        s1 = SnapshotBuilder().build()
        # Fresh source instance so the changed universe is actually observed
        fake = install_fake_s3()
        fake.add_artifact(
            "research_universe",
            [_universe_record(0.4, 4.0) for _ in range(7)],
        )
        s2 = SnapshotBuilder().build()
        assert s1.snapshot_id != s2.snapshot_id
        assert s1.identity_hash != s2.identity_hash

    def test_persistence_roundtrip_preserves_identity(self, fake_universe, tmp_path):
        """save → fresh registry (restart) → load: identity survives."""
        snapshot = SnapshotBuilder().build()
        reg = SnapshotRegistry(baselines_dir=str(tmp_path / "store"))
        reg.save(snapshot)

        restarted = SnapshotRegistry(baselines_dir=str(tmp_path / "store"))
        loaded = restarted.load(snapshot.snapshot_id)
        assert loaded is not None
        assert loaded.snapshot_id == snapshot.snapshot_id
        assert loaded.config_hash == snapshot.config_hash
        assert loaded.identity_hash == snapshot.identity_hash


# ═══════════════════════════════════════════════════════════════════════════════
# 3-5. BOOTSTRAP + POINTER DURABILITY
# ═══════════════════════════════════════════════════════════════════════════════

class TestBootstrap:
    def test_initial_bootstrap(self, fake_universe):
        """No active baseline → snapshot persisted + active pointer set."""
        reg = _tmp_registry()
        assert get_active(registry=reg) is None

        result = ensure_active_baseline()
        assert result.action == "created"
        snapshot = result.snapshot
        assert reg.exists(snapshot.snapshot_id)
        state = get_active(registry=reg)
        assert state is not None
        assert state.active_baseline_id == snapshot.snapshot_id
        assert load_baseline_snapshot(state.active_baseline_id) is not None
        assert state.actor == baseline_authority.BOOTSTRAP_ACTOR
        assert state.reason != ""
        assert state.previous_baseline_id == ""

    def test_bootstrap_idempotent_unchanged_state(self, fake_universe):
        """Repeated bootstrap → same effective baseline, no duplicate explosion."""
        r1 = ensure_active_baseline()
        r2 = ensure_active_baseline()
        assert r1.snapshot.snapshot_id == r2.snapshot.snapshot_id
        assert r2.action == "existing_active_retained"
        assert len(_tmp_registry().list_snapshots()) == 1

        # Even after pointer loss, unchanged state reuses the equivalent
        # persisted snapshot instead of creating a duplicate:
        Path(baseline_authority._ACTIVE_POINTER_FILE).unlink()
        r3 = ensure_active_baseline()
        assert r3.action == "reused_equivalent"
        assert r3.snapshot.snapshot_id == r1.snapshot.snapshot_id
        assert len(_tmp_registry().list_snapshots()) == 1

    def test_existing_active_baseline_retained_on_config_drift(
        self, fake_universe, monkeypatch
    ):
        """Drift is reported, NEVER silently rebased onto a new baseline."""
        result = ensure_active_baseline()
        active_before = result.snapshot.snapshot_id

        # Simulate configuration identity drift (config_hash changes while the
        # active baseline's captured config identity stays on disk):
        monkeypatch.setattr(
            "core.research_events.compute_config_hash",
            lambda: "aaaaaaaaaaaaaaaa",
        )
        drifted = ensure_active_baseline()
        assert drifted.action == "existing_active_retained"
        assert drifted.snapshot.snapshot_id == active_before
        assert drifted.config_drift_detected is True
        assert "config_hash" in drifted.drift_detail

    def test_active_pointer_durability_across_reinstantiation(self, fake_universe):
        ensure_active_baseline()
        # Fresh authority objects = restart simulation
        reg = SnapshotRegistry(baselines_dir=baseline_authority._BASELINES_DIR)
        state = get_active(
            registry=reg, pointer_file=baseline_authority._ACTIVE_POINTER_FILE
        )
        assert state is not None
        assert reg.load(state.active_baseline_id) is not None

    def test_malformed_pointer_fails_closed(self, fake_universe):
        ensure_active_baseline()
        pointer = Path(baseline_authority._ACTIVE_POINTER_FILE)
        pointer.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(BaselineStateError, match="malformed"):
            get_active()


# ═══════════════════════════════════════════════════════════════════════════════
# 6-8. PREVIOUS POINTER + IDEMPOTENT REACTIVATION + FAIL-CLOSED ACTIVATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestActivePreviousPointer:
    def test_activation_ab_preserves_previous(self, fake_universe):
        """A → B: active=B, previous=A."""
        a = ensure_active_baseline().snapshot
        b = _second_state_snapshot()
        assert b.snapshot_id != a.snapshot_id

        state = set_active(
            b.snapshot_id, actor="tester", reason="advance to baseline B"
        )
        assert state.active_baseline_id == b.snapshot_id
        assert state.previous_baseline_id == a.snapshot_id

    def test_idempotent_reactivation_preserves_previous(self, fake_universe):
        """activate B; activate B again → active=B, previous=A (never B)."""
        a = ensure_active_baseline().snapshot
        b = _second_state_snapshot()
        set_active(b.snapshot_id, actor="tester", reason="activate B")
        state = set_active(b.snapshot_id, actor="tester", reason="replay B")
        assert state.active_baseline_id == b.snapshot_id
        assert state.previous_baseline_id == a.snapshot_id
        assert state.previous_baseline_id != state.active_baseline_id

    def test_actor_and_reason_required(self, fake_universe):
        a = ensure_active_baseline().snapshot
        b = _second_state_snapshot()
        with pytest.raises(ValueError, match="actor"):
            set_active(b.snapshot_id, actor="  ", reason="r")
        with pytest.raises(ValueError, match="reason"):
            set_active(b.snapshot_id, actor="tester", reason="")
        # Pointer unchanged
        assert get_active().active_baseline_id == a.snapshot_id

    def test_missing_snapshot_activation_fails_closed(self, fake_universe):
        """Unknown snapshot → fail closed; active pointer unchanged."""
        a = ensure_active_baseline().snapshot
        b = _second_state_snapshot()
        set_active(b.snapshot_id, actor="tester", reason="activate B")

        with pytest.raises(BaselineMissingError, match="unknown snapshot"):
            set_active("V10_BASELINE_GHOST", actor="tester", reason="bad id")

        state = get_active()
        assert state.active_baseline_id == b.snapshot_id
        assert state.previous_baseline_id == a.snapshot_id


# ═══════════════════════════════════════════════════════════════════════════════
# 9-10. CANDIDATE CREATION VIA PRODUCTION PATH + BASELINE IMMUTABILITY
# ═══════════════════════════════════════════════════════════════════════════════

def _make_validated_result():
    from research_engine.lifecycle.experiment_protocol import ExperimentResult
    return ExperimentResult(
        experiment_id="EXP-test", hypothesis_id="H-test", status="complete",
        n=200, mean_r=0.25, median_r=0.10, total_r=50.0, win_rate=0.45,
        std_dev=1.2, ci_lower=0.08, ci_upper=0.42,
        permutation_p=0.001,
        oos_n=80, oos_mean_r=0.15, oos_ci_lower=0.02, oos_ci_upper=0.28,
        symbols_positive=7, symbols_total=10,
        periods_positive=4, periods_total=5,
        placebo_passes=True, placebo_positive_fraction=0.3,
        survives_top10_removal=True, survives_top20_removal=True,
    )


def _validated_hypothesis(hypothesis_id="H-val-4c1-001"):
    from research_engine.lifecycle.hypothesis import (
        ConclusionType, Hypothesis, HypothesisCategory, HypothesisStatus,
    )
    h = Hypothesis(
        title="Wave 4C.1 candidate", hypothesis_id=hypothesis_id,
        category=HypothesisCategory.DIRECTION_INVERSION,
    )
    h.transition(HypothesisStatus.REGISTERED, reason="r")
    h.transition(HypothesisStatus.TESTING, reason="t")
    h.conclude(ConclusionType.VALIDATED, reason="strong evidence")
    return h


@pytest.fixture
def orchestrator_env(tmp_path, monkeypatch):
    """Isolate every persistence surface the production candidate path touches."""
    monkeypatch.setattr(
        "research_engine.lifecycle.registry._REGISTRY_DIR", tmp_path / "inv"
    )
    monkeypatch.setattr(
        "research_engine.lifecycle.registry._REGISTRY_FILE", tmp_path / "inv" / "reg.json"
    )
    monkeypatch.setattr(
        "research_engine.lifecycle.registry._AUDIT_LOG", tmp_path / "inv" / "audit.jsonl"
    )
    monkeypatch.setattr(
        "research_engine.v10.candidates.candidate_registry._STORAGE_DIR",
        str(tmp_path / "candidates"),
    )
    from research_engine.lifecycle.orchestrator import ResearchOrchestrator
    orch = ResearchOrchestrator()
    orch._knowledge_path = tmp_path / "km.json"
    return orch


class TestCandidateBaselineIntegration:
    def test_candidate_receives_real_persisted_baseline(
        self, fake_universe, orchestrator_env
    ):
        """Production path: candidate.baseline_id == persisted active snapshot."""
        active = ensure_active_baseline().snapshot
        orch = orchestrator_env
        h = _validated_hypothesis()

        record = orch.create_optimisation_candidate(h, _make_validated_result())
        assert record is not None

        # Real canonical baseline — never the "current_v10" placeholder
        assert record["baseline_id"] == active.snapshot_id
        assert record["baseline_id"] != "current_v10"
        assert record["change_definition"]["baseline_config_hash"] == active.config_hash

        # CandidateRecord → existing persisted BaselineSnapshot
        reg = CandidateRegistry()
        stored = reg.get(record["candidate_id"])
        assert stored is not None
        assert stored.baseline_id == active.snapshot_id
        assert _tmp_registry().exists(stored.baseline_id)
        assert load_baseline_snapshot(stored.baseline_id) is not None

    def test_candidate_baseline_immutable_across_activation(
        self, fake_universe, orchestrator_env
    ):
        """candidate bound to A stays bound to A after A → B activation."""
        active_a = ensure_active_baseline().snapshot
        orch = orchestrator_env
        h = _validated_hypothesis()

        record = orch.create_optimisation_candidate(h, _make_validated_result())
        assert record is not None
        assert record["baseline_id"] == active_a.snapshot_id

        # Active baseline advances A → B
        b = _second_state_snapshot()
        set_active(b.snapshot_id, actor="tester", reason="advance to B")
        assert get_active().active_baseline_id == b.snapshot_id

        # Historical candidate record is NOT rewritten
        reg = CandidateRegistry()
        stored = reg.get(record["candidate_id"])
        assert stored.baseline_id == active_a.snapshot_id


# ═══════════════════════════════════════════════════════════════════════════════
# 11-13. EVALUATION PROVENANCE + STALENESS GATES
# ═══════════════════════════════════════════════════════════════════════════════

_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY")


def _candidate_shadow(cor, candidate_r, *, candidate_id, symbol="EURUSD", ts=5000.0):
    return {
        "schema_version": "shadow_trades_v1",
        "source": "shadow_trade_engine",
        "event_type": "CLOSE",
        "identity": {
            "trade_id": candidate_trade_id(candidate_id, 1, symbol, _TREATMENT_ID),
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


def _incumbent_truth(cor, baseline_r, *, symbol="EURUSD", ts=5000.0):
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


def _paired_populations(n, candidate_id, baseline_r=-0.2, candidate_r=0.3):
    cand, inc = [], []
    for i in range(n):
        sym = _SYMBOLS[i % 3]
        cor = f"COR-2026-1-{sym}-{i:05d}"
        ts = 5000.0 + i * 300
        cand.append(_candidate_shadow(cor, candidate_r + (i % 5) * 0.05,
                                      candidate_id=candidate_id, symbol=sym, ts=ts))
        inc.append(_incumbent_truth(cor, baseline_r + (i % 5) * 0.05,
                                    symbol=sym, ts=ts))
    return cand, inc


_EVAL_CFG = EvaluationConfig(minimum_sample=30, min_symbols_positive=2,
                             min_periods_positive=2)


def _shadow_candidate(candidate_id, baseline_id, registry_dir):
    """Create a SHADOW_TESTING candidate bound to a persisted baseline ID."""
    reg = CandidateRegistry(storage_dir=registry_dir)
    record = CandidateRecord(
        candidate_id=candidate_id,
        hypothesis_id="H-4c1-eval",
        baseline_id=baseline_id,
        status=CandidateStatus.SHADOW_TESTING,
        change_definition={"type": "direction_inversion"},
        created_at="1970-01-01T00:00:00+00:00",  # before all test observations
    )
    reg._candidates[candidate_id] = record
    reg._persist()
    return record


class TestEvaluationProvenanceAndStaleness:
    def test_evaluation_provenance_persisted(
        self, fake_universe, tmp_path, monkeypatch
    ):
        """11. Persisted evaluation retains candidate_id/baseline_id/
        config_hash/evaluation_id."""
        active = ensure_active_baseline().snapshot
        monkeypatch.setattr(
            "research_engine.lifecycle.candidate_evaluation_bridge._EVALUATIONS_DIR",
            tmp_path / "evals",
        )
        reg_dir = str(tmp_path / "candidates")
        _shadow_candidate("OPT-4c1-prov", active.snapshot_id, reg_dir)
        cand, inc = _paired_populations(50, "OPT-4c1-prov")

        evaluation = evaluate_candidate(
            "OPT-4c1-prov", candidate_records=cand,
            incumbent_records=inc, config=_EVAL_CFG,
            registry_dir=reg_dir,
        )
        # Fresh baseline + unchanged config → promotion allowed
        assert evaluation.decision == "VALIDATED"
        assert evaluation.promotion_blocked is False
        assert evaluation.baseline_id == active.snapshot_id
        assert evaluation.config_hash == active.config_hash

        # Full audit-trail persistence carries all four provenance fields
        eval_path = tmp_path / "evals" / "OPT-4c1-prov.jsonl"
        assert eval_path.exists()
        persisted = json.loads(eval_path.read_text(encoding="utf-8").splitlines()[-1])
        assert persisted["candidate_id"] == "OPT-4c1-prov"
        assert persisted["baseline_id"] == active.snapshot_id
        assert persisted["config_hash"] == active.config_hash
        assert persisted["evaluation_id"] == evaluation.evaluation_id

        # Candidate promoted (provenance coherent path)
        stored = CandidateRegistry(storage_dir=reg_dir).get("OPT-4c1-prov")
        assert stored.status == CandidateStatus.READY_FOR_REVIEW
        assert stored.validation_history[-1].validation_id == evaluation.evaluation_id

    def test_stale_baseline_cannot_promote(
        self, fake_universe, tmp_path, monkeypatch
    ):
        """12. candidate baseline=A, active baseline=B → no READY_FOR_REVIEW."""
        a = ensure_active_baseline().snapshot
        b = _second_state_snapshot()
        set_active(b.snapshot_id, actor="tester", reason="advance to B")

        monkeypatch.setattr(
            "research_engine.lifecycle.candidate_evaluation_bridge._EVALUATIONS_DIR",
            tmp_path / "evals",
        )
        reg_dir = str(tmp_path / "candidates")
        _shadow_candidate("OPT-4c1-stale", a.snapshot_id, reg_dir)
        cand, inc = _paired_populations(50, "OPT-4c1-stale")

        evaluation = evaluate_candidate(
            "OPT-4c1-stale", candidate_records=cand,
            incumbent_records=inc, config=_EVAL_CFG,
            registry_dir=reg_dir,
        )
        # Statistical verdict is still computed — but promotion is blocked
        assert evaluation.decision == "VALIDATED"
        assert evaluation.promotion_blocked is True
        assert "stale_baseline" in evaluation.promotion_block_reason
        assert a.snapshot_id in evaluation.promotion_block_reason
        assert b.snapshot_id in evaluation.promotion_block_reason

        # Candidate must NOT be silently treated as validated against B
        stored = CandidateRegistry(storage_dir=reg_dir).get("OPT-4c1-stale")
        assert stored.status == CandidateStatus.SHADOW_TESTING
        entry = stored.validation_history[-1]
        assert entry.decision == "INCONCLUSIVE"
        assert any("stale_baseline" in r for r in entry.regressions)
        # candidate.baseline_id is never rewritten
        assert stored.baseline_id == a.snapshot_id

    def test_config_drift_cannot_promote(
        self, fake_universe, tmp_path, monkeypatch
    ):
        """13. baseline config_hash != current config hash → no promotion."""
        snapshot = ensure_active_baseline().snapshot
        # Simulate configuration identity drift on the persisted baseline
        snapshot.config_hash = "f" * 16
        _tmp_registry().save(snapshot)  # atomic overwrite, same snapshot_id

        monkeypatch.setattr(
            "research_engine.lifecycle.candidate_evaluation_bridge._EVALUATIONS_DIR",
            tmp_path / "evals",
        )
        reg_dir = str(tmp_path / "candidates")
        _shadow_candidate("OPT-4c1-drift", snapshot.snapshot_id, reg_dir)
        cand, inc = _paired_populations(50, "OPT-4c1-drift")

        evaluation = evaluate_candidate(
            "OPT-4c1-drift", candidate_records=cand,
            incumbent_records=inc, config=_EVAL_CFG,
            registry_dir=reg_dir,
        )
        assert evaluation.decision == "VALIDATED"
        assert evaluation.promotion_blocked is True
        assert "stale_config" in evaluation.promotion_block_reason

        stored = CandidateRegistry(storage_dir=reg_dir).get("OPT-4c1-drift")
        assert stored.status == CandidateStatus.SHADOW_TESTING


# ═══════════════════════════════════════════════════════════════════════════════
# 14. ZERO TRADING/CONFIG MUTATION
# ═══════════════════════════════════════════════════════════════════════════════

_WAVE_MODULES = (
    "research_engine/v10/baselines/baseline_authority.py",
    "research_engine/v10/baselines/snapshot_builder.py",
    "research_engine/v10/baselines/snapshot_registry.py",
    "research_engine/lifecycle/candidate_evaluation_bridge.py",
)

_FORBIDDEN_IMPORT_SUBSTRINGS = (
    "mt5", "risk.manager", "risk.levels", "risk_manager",
    "broker", "config_profile_loader", "order_send",
)
_FORBIDDEN_CALL_NAMES = ("order_send", "load_and_apply_profile")


class TestTradingSafety:
    def test_no_trading_mutation_imports_or_calls(self):
        """Baseline/bootstrap/evaluation identity path never touches trading."""
        for rel in _WAVE_MODULES:
            source = Path(rel).read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        lowered = alias.name.lower()
                        for frag in _FORBIDDEN_IMPORT_SUBSTRINGS:
                            assert frag not in lowered, f"{rel}: imports {alias.name}"
                elif isinstance(node, ast.ImportFrom):
                    module = (node.module or "").lower()
                    for frag in _FORBIDDEN_IMPORT_SUBSTRINGS:
                        assert frag not in module, f"{rel}: imports from {node.module}"
                elif isinstance(node, ast.Call):
                    fn = node.func
                    name = fn.attr if isinstance(fn, ast.Attribute) else (
                        fn.id if isinstance(fn, ast.Name) else ""
                    )
                    assert name not in _FORBIDDEN_CALL_NAMES, f"{rel}: calls {name}"

    def test_runtime_config_untouched_by_identity_path(
        self, fake_universe, tmp_path, monkeypatch
    ):
        """Runtime proof: bootstrap + evaluation leave core.config untouched."""
        import core.config as cfg

        watched = (
            "EXECUTION_ENABLED", "DRY_RUN", "RISK_PER_TRADE_PERCENT",
            "MIN_SCORE_TO_TRADE", "MAX_TOTAL_OPEN_POSITIONS", "ENGINE_MODE",
        )
        before = {k: getattr(cfg, k, "<missing>") for k in watched}

        active = ensure_active_baseline().snapshot
        monkeypatch.setattr(
            "research_engine.lifecycle.candidate_evaluation_bridge._EVALUATIONS_DIR",
            tmp_path / "evals",
        )
        reg_dir = str(tmp_path / "candidates")
        _shadow_candidate("OPT-4c1-safe", active.snapshot_id, reg_dir)
        cand, inc = _paired_populations(50, "OPT-4c1-safe")
        evaluate_candidate(
            "OPT-4c1-safe", candidate_records=cand,
            incumbent_records=inc, config=_EVAL_CFG,
            registry_dir=reg_dir,
        )

        after = {k: getattr(cfg, k, "<missing>") for k in watched}
        assert after == before
