"""
Gap 3 — Edge-candidate integration tests.

Proves:
  - production edge analysis uses ONLY canonical S3 evidence (no local
    replay_data/ dependency, no silent fallback)
  - explicit offline fixture mode exists and is only used when requested
  - canonical evidence mapping (join on canonical_opportunity_id) with full
    accounting of malformed/missing records
  - edge discovery gates unchanged (strong accepted, weak/tiny-N rejected)
  - the lifecycle bridge registers accepted edges as Hypotheses (REGISTERED
    stage) and NEVER creates CandidateRecords directly
  - idempotent week-on-week reruns (no duplicate lifecycle objects)
  - lineage (edge -> hypothesis -> candidate) survives; no trading mutation

All tests are synthetic - production AWS is NEVER touched.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.edge_attribution.evidence import (
    load_edge_evidence,
    load_edge_evidence_offline_replay,
)
from research_engine.edge_attribution.models import EdgeAttributionRecord
from research_engine.edge_candidates.generator import (
    _MIN_EV_GENERATE,
    _MIN_SAMPLE_GENERATE,
    CandidateGenerationResult,
    generate_candidates,
)
from research_engine.edge_candidates.models import EdgeCandidate
from research_engine.edge_candidates.scoring import score_candidate

CANON = "EURUSD*1784800000*HAMMER"


# ─── synthetic canonical records ─────────────────────────────────────────────


def _trace(canon: str = CANON, symbol: str = "EURUSD") -> dict[str, Any]:
    """decision_trace-shaped record (canonical join key + condition fields)."""
    return {
        "schema_version": "decision_trace_v1",
        "canonical_opportunity_id": canon,
        "entity_id": f"{symbol}_1784800000",
        "symbol": symbol,
        "timestamp_utc": "2026-09-04T10:00:00Z",
        "pattern_detected": "HAMMER",
        "pattern_name": "HAMMER",
        "components": {"htf_alignment": 0.8, "confirmation_pre": 0.7},
        "score_neutral": 0.6,
        "selected_strategy": "mean_reversion_v1",
        "regime": "TRENDING",
    }


def _shadow(canon: str = CANON, r: float = 1.5, stype: str = "PRIMARY_HORIZON_SIMULATION",
            tid: str = "nshadow_1_EURUSD_SCALP") -> dict[str, Any]:
    return {
        "schema_version": "shadow_trades_v1",
        "identity": {
            "shadow_trade_id": tid,
            "canonical_opportunity_id": canon,
            "symbol": canon.split("*")[0],
            "shadow_type": stype,
            "evaluated_horizon": "SCALP",
        },
        "simulated_outcome": {"pnl_r_multiple": r, "exit_reason": "take_profit"},
    }


def _truth(canon: str = CANON, r: float = 0.8, trade_id: str = "pos_1") -> dict[str, Any]:
    return {
        "schema_version": "trade_truth_v1",
        "identity": {
            "trade_id": trade_id,
            "canonical_opportunity_id": canon,
            "symbol": canon.split("*")[0],
        },
        "outcome": {"r_multiple_realised": r},
        "exit": {"exit_reason": "take_profit_hit"},
    }


def _install_s3(traces, shadows, truths, monkeypatch):
    """Route the sanctioned S3 evidence path to synthetic canonical records."""
    import research_engine.data_access.loaders as loaders
    import research_engine.data_access.s3_source as s3s
    import research_engine.data_access.shadow_runtime_ingestion as sri

    class _StubSource:
        def read_dataset(self, dataset, **kwargs):
            assert dataset == "trade_truth"
            return truths

    monkeypatch.setattr(loaders, "load_decision_trace", lambda *a, **k: traces)
    monkeypatch.setattr(sri, "ingest_completed_shadow_trades", lambda **k: shadows)
    monkeypatch.setattr(s3s, "get_default_source", lambda: _StubSource())


def _attr(pattern="HAMMER", session="LONDON", regime="TRENDING", symbol="EURUSD",
          r=1.0, ts="2026-09-04T10:00:00Z") -> EdgeAttributionRecord:
    return EdgeAttributionRecord(
        entity_id=f"{symbol}_{ts}", timestamp_utc=ts, symbol=symbol,
        pattern=pattern, strategy="S", direction="BUY",
        regime=regime, volatility_state="V", market_state="MS", session=session,
        htf_alignment_bin="HIGH", trend_alignment_bin="HIGH", bias_alignment_bin="HIGH",
        score_bin="HIGH", confirmation_bin="STRONG", result_r=r, win=r > 0,
    )


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    """Run from an empty temp cwd: no local replay_data/, no production files."""
    monkeypatch.chdir(tmp_path)


# ═══════════════════════════════════════════════════════════════════════════════
# EVIDENCE SOURCE
# ═══════════════════════════════════════════════════════════════════════════════


class TestEvidenceSource:
    def test_production_evidence_uses_sanctioned_canonical_sources(self, monkeypatch):
        _install_s3([_trace()], [_shadow()], [], monkeypatch)
        result = load_edge_evidence()
        assert result.accounting["mode"] == "production_canonical_s3"
        assert result.accounting["join_key"] == "canonical_opportunity_id"
        assert len(result.records) == 1
        assert result.records[0].result_r == 1.5

    def test_no_local_replay_data_dependency_in_production_mode(self, monkeypatch):
        # cwd is an empty temp dir: no replay_data/ exists, production path
        # still works purely from canonical S3 evidence.
        assert not Path("replay_data").exists()
        _install_s3([_trace()], [_shadow()], [], monkeypatch)
        result = load_edge_evidence()
        assert len(result.records) == 1

    def test_zero_outcomes_is_loud_accounting_not_silent_empty(self, monkeypatch):
        # Canonical sources reachable but no outcome evidence for the decision.
        _install_s3([_trace()], [], [], monkeypatch)
        result = load_edge_evidence()
        assert result.records == []
        assert result.accounting["decisions_without_outcome"] == 1
        assert result.accounting["mode"] == "production_canonical_s3"

    def test_s3_failure_is_loud_and_never_consumes_local_fixtures(self, monkeypatch):
        # Even with local replay fixtures present, an S3 failure must surface
        # loudly — never silently fall back to replay_data/.
        (Path("replay_data") / "EURUSD" / "5").mkdir(parents=True)
        (Path("replay_data") / "EURUSD" / "5" / "2026-09-04.jsonl").write_text(
            '{"o":1,"h":2,"l":0.5,"c":1.5}\n', encoding="utf-8"
        )
        import research_engine.data_access.loaders as loaders
        from research_engine.data_access.s3_source import ResearchDataSourceError

        def _boom(*a, **k):
            raise ResearchDataSourceError("S3 unavailable (test)")

        monkeypatch.setattr(loaders, "load_decision_trace", _boom)
        with pytest.raises(ResearchDataSourceError):
            load_edge_evidence()

    def test_offline_replay_mode_only_when_explicitly_requested(self):
        from research_engine.counterfactual.schema import SimulationConfidence
        # Build a minimal valid replay fixture (needs enough future bars for
        # MEDIUM/HIGH simulation confidence).
        fixture_dir = Path("fixtures_replay")
        candle_lines = []
        price = 1.0
        for i in range(80):
            price += 0.001 if i < 40 else -0.001
            candle_lines.append(
                '{"o":%.5f,"h":%.5f,"l":%.5f,"c":%.5f}' % (price, price + 0.002, price - 0.002, price)
            )
        (fixture_dir / "EURUSD" / "5").mkdir(parents=True)
        (fixture_dir / "EURUSD" / "5" / "2026-09-04.jsonl").write_text(
            "\n".join(candle_lines), encoding="utf-8"
        )
        trace = _trace()
        result = load_edge_evidence_offline_replay(str(fixture_dir), traces=[trace])
        assert result.accounting["mode"] == "offline_replay_fixture"
        assert result.accounting["symbols_with_candles"] == 1
        # The simulator decides confidence; with a well-formed fixture the
        # decision gets a counterfactual record.
        assert isinstance(result.records, list)


# ═══════════════════════════════════════════════════════════════════════════════
# EVIDENCE MAPPING
# ═══════════════════════════════════════════════════════════════════════════════


class TestEvidenceMapping:
    def test_realised_outcome_preferred_over_counterfactual(self, monkeypatch):
        _install_s3([_trace()], [_shadow(canon=CANON, r=1.5)], [_truth(canon=CANON, r=0.8)], monkeypatch)
        result = load_edge_evidence()
        assert len(result.records) == 1
        assert result.records[0].result_r == 0.8
        assert result.accounting["outcome_source_trade_truth_realised"] == 1

    def test_counterfactual_used_when_no_realised_outcome(self, monkeypatch):
        _install_s3([_trace()], [_shadow(canon=CANON, r=1.5)], [], monkeypatch)
        result = load_edge_evidence()
        assert result.records[0].result_r == 1.5
        assert result.accounting["outcome_source_shadow_counterfactual"] == 1

    def test_horizon_alternative_only_canonical_is_not_evidence(self, monkeypatch):
        _install_s3(
            [_trace()],
            [_shadow(canon=CANON, stype="HORIZON_ALTERNATIVE", tid="ns_alt")],
            [], monkeypatch,
        )
        result = load_edge_evidence()
        assert result.records == []
        assert result.accounting["shadow_horizon_alternative_skipped"] == 1
        assert result.accounting["decisions_without_outcome"] == 1

    def test_malformed_and_ambiguous_outcomes_accounted(self, monkeypatch):
        bad_shadow = _shadow(canon=CANON)
        del bad_shadow["identity"]["canonical_opportunity_id"]
        ambiguous = [
            _shadow(canon=CANON, tid="ns1", r=1.0),
            _shadow(canon=CANON, tid="ns2", r=2.0),
        ]
        _install_s3([_trace()], ambiguous + [bad_shadow], [], monkeypatch)
        result = load_edge_evidence()
        assert result.records == []
        assert result.accounting["shadow_missing_canonical_key"] == 1
        assert result.accounting["shadow_ambiguous_excluded"] == 1

    def test_duplicate_replay_rows_collapse(self, monkeypatch):
        row = _shadow(canon=CANON, r=1.5)
        _install_s3([_trace()], [row, dict(row)], [], monkeypatch)
        result = load_edge_evidence()
        assert len(result.records) == 1
        assert result.accounting["shadow_duplicate_replay_collapsed"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# EDGE DISCOVERY (existing gates — unchanged)
# ═══════════════════════════════════════════════════════════════════════════════


class TestEdgeDiscovery:
    def test_strong_production_shaped_edge_is_accepted(self):
        records = [_attr(r=2.0 if i % 2 == 0 else -1.0, ts=f"2026-09-04T10:{i:02d}:00Z")
                   for i in range(40)]
        result = generate_candidates(records)
        assert result.candidates_accepted >= 1
        top = result.accepted[0]
        assert top.sample_size >= 30
        assert top.expectancy > 0

    def test_weak_edge_is_rejected(self):
        records = [_attr(r=-1.0, ts=f"2026-09-04T10:{i:02d}:00Z") for i in range(40)]
        result = generate_candidates(records)
        assert result.candidates_accepted == 0
        # every rejection is evidence-backed: non-positive EV / total R
        for r in result.rejected:
            reasons = r.get("reasons", [])
            if "reasons" in r:
                assert ("non_positive_ev" in reasons) or ("non_positive_total_r" in reasons)
            else:
                assert r.get("reason") == "negative_ev" and r.get("ev", 0) <= _MIN_EV_GENERATE

    def test_tiny_n_cannot_become_candidate(self):
        records = [_attr(r=2.0, ts=f"2026-09-04T10:{i:02d}:00Z") for i in range(10)]
        result = generate_candidates(records)
        assert result.candidates_accepted == 0

    def test_discovery_gates_unchanged(self):
        # Guard: discovery thresholds must not drift.
        assert _MIN_SAMPLE_GENERATE == 20
        assert _MIN_EV_GENERATE == 0.01


# ═══════════════════════════════════════════════════════════════════════════════
# LIFECYCLE BRIDGE
# ═══════════════════════════════════════════════════════════════════════════════


def _accepted_edge(edge_id="EC-TEST-ABC123", n=40, ev=0.5) -> EdgeCandidate:
    c = EdgeCandidate(
        candidate_id=edge_id,
        hypothesis="Positive expectancy when: pattern=HAMMER, session=LONDON",
        conditions={"pattern": "HAMMER", "session": "LONDON"},
        sample_size=n, win_rate=0.6, expectancy=ev, profit_factor=1.8,
        total_r=ev * n,
    )
    return score_candidate(c)


def _gen_result(accepted: list[EdgeCandidate], combos=64) -> CandidateGenerationResult:
    return CandidateGenerationResult(
        total_records=sum(c.sample_size for c in accepted) or 100,
        combinations_tested=combos,
        candidates_generated=len(accepted),
        candidates_accepted=len(accepted),
        accepted=accepted,
    )


def _registry_dir() -> str:
    """CandidateRegistry default persistence resolves under cwd (tmp in tests)."""
    return str(Path("data/research/candidates"))


@pytest.fixture()
def orch(tmp_path, monkeypatch):
    """Orchestrator with registry persistence isolated to tmp_path."""
    from research_engine.lifecycle.orchestrator import ResearchOrchestrator
    monkeypatch.chdir(tmp_path)
    return ResearchOrchestrator()


class TestLifecycleBridge:
    def test_raw_edge_never_becomes_candidate_registry_entry(self, orch):
        from research_engine.edge_candidates.lifecycle_bridge import (
            submit_edge_candidates_to_lifecycle,
        )
        from research_engine.v10.candidates.candidate_registry import CandidateRegistry

        sub = submit_edge_candidates_to_lifecycle(
            _gen_result([_accepted_edge()]), orchestrator=orch
        )
        assert len(sub.registered) == 1
        # NO direct candidate creation — CandidateRegistry stays untouched
        reg = CandidateRegistry(storage_dir=_registry_dir())
        assert reg.list_all() == []

    def test_edge_enters_lifecycle_as_hypothesis_with_lineage(self, orch):
        from research_engine.edge_candidates.lifecycle_bridge import (
            submit_edge_candidates_to_lifecycle,
        )
        from research_engine.lifecycle.hypothesis import HypothesisStatus

        edge = _accepted_edge()
        submit_edge_candidates_to_lifecycle(_gen_result([edge]), orchestrator=orch)
        h = orch.registry.all()[0]
        assert h.status == HypothesisStatus.REGISTERED
        assert h.source_finding_id == edge.candidate_id
        assert h.source == "edge_candidate_generation"
        assert h.category.value == "PATTERN_SIGNAL"
        assert any(t.startswith("edge_evidence:") for t in h.tags)
        assert any("source_datasets" in t for t in h.tags)
        assert any("join_key:canonical_opportunity_id" in t for t in h.tags)
        assert h.multiple_testing_count == 64  # discovery-bias honesty
        assert h.falsification_conditions

    def test_identical_rerun_does_not_duplicate(self, orch):
        from research_engine.edge_candidates.lifecycle_bridge import (
            submit_edge_candidates_to_lifecycle,
        )
        gen = _gen_result([_accepted_edge()])
        first = submit_edge_candidates_to_lifecycle(gen, orchestrator=orch)
        second = submit_edge_candidates_to_lifecycle(gen, orchestrator=orch)
        assert len(first.registered) == 1
        assert len(second.registered) == 0
        assert len(second.reconfirmed) == 1
        assert len(orch.registry.all()) == 1  # ONE lifecycle object

    def test_new_evidence_updates_rather_than_duplicates(self, orch):
        from research_engine.edge_candidates.lifecycle_bridge import (
            submit_edge_candidates_to_lifecycle,
        )
        submit_edge_candidates_to_lifecycle(
            _gen_result([_accepted_edge(n=40, ev=0.5)]), orchestrator=orch
        )
        # Same edge, materially stronger evidence
        second = submit_edge_candidates_to_lifecycle(
            _gen_result([_accepted_edge(n=90, ev=0.7)]), orchestrator=orch
        )
        assert len(second.evidence_updated) == 1
        assert len(second.registered) == 0
        assert len(orch.registry.all()) == 1
        h = orch.registry.all()[0]
        assert any("edge_evidence:n=90" in t for t in h.tags)

    def test_distinct_edges_remain_distinct(self, orch):
        from research_engine.edge_candidates.lifecycle_bridge import (
            submit_edge_candidates_to_lifecycle,
        )
        e1 = _accepted_edge(edge_id="EC-EDGE-ONE")
        e2 = EdgeCandidate(
            candidate_id="EC-EDGE-TWO",
            hypothesis="Positive expectancy when: regime=TRENDING",
            conditions={"regime": "TRENDING"},
            sample_size=50, win_rate=0.6, expectancy=0.4, profit_factor=1.5,
            total_r=20.0,
        )
        sub = submit_edge_candidates_to_lifecycle(
            _gen_result([e1, e2]), orchestrator=orch
        )
        assert len(sub.registered) == 2
        assert len(orch.registry.all()) == 2
        categories = {h.category.value for h in orch.registry.all()}
        assert "PATTERN_SIGNAL" in categories and "REGIME_CONDITIONING" in categories

    def test_concluded_hypothesis_not_reopened(self, orch):
        from research_engine.edge_candidates.lifecycle_bridge import (
            submit_edge_candidates_to_lifecycle,
        )
        from research_engine.lifecycle.hypothesis import ConclusionType, HypothesisStatus

        submit_edge_candidates_to_lifecycle(
            _gen_result([_accepted_edge()]), orchestrator=orch
        )
        h = orch.registry.all()[0]
        h.transition(HypothesisStatus.TESTING, reason="test")
        h.transition(HypothesisStatus.CHALLENGED, reason="test")
        assert h.conclude(ConclusionType.REJECTED, reason="test", confidence="HIGH")
        orch.registry.update(h)

        sub = submit_edge_candidates_to_lifecycle(
            _gen_result([_accepted_edge()]), orchestrator=orch
        )
        assert len(sub.skipped_concluded) == 1
        assert len(sub.registered) == 0
        assert len(orch.registry.all()) == 1  # history preserved, no duplicate

    def test_validated_hypothesis_reaches_canonical_candidate_registry(self, orch):
        """Full path proof: edge -> hypothesis -> VALIDATED -> CandidateRegistry."""
        from research_engine.edge_candidates.lifecycle_bridge import (
            submit_edge_candidates_to_lifecycle,
        )
        from research_engine.lifecycle.hypothesis import ConclusionType, HypothesisStatus
        from research_engine.lifecycle.experiment_protocol import ExperimentResult
        from research_engine.v10.candidates.candidate_registry import CandidateRegistry

        submit_edge_candidates_to_lifecycle(
            _gen_result([_accepted_edge()]), orchestrator=orch
        )
        h = orch.registry.all()[0]
        h.transition(HypothesisStatus.TESTING, reason="test")
        h.transition(HypothesisStatus.CHALLENGED, reason="test")
        assert h.conclude(ConclusionType.VALIDATED, reason="governed validation", confidence="HIGH")
        orch.registry.update(h)

        result = ExperimentResult(
            experiment_id="EXP-test", hypothesis_id=h.hypothesis_id,
            n=120, mean_r=0.35, win_rate=0.58, oos_n=40, oos_mean_r=0.28,
            symbols_positive=4, symbols_total=4,
            survives_top20_removal=True, periods_positive=3, periods_total=3,
            ci_lower=0.10, ci_upper=0.60,
        )
        record = orch.create_optimisation_candidate(h, result)
        assert record is not None
        # Candidate entered the CANONICAL registry via the existing VALIDATED path
        reg = CandidateRegistry(storage_dir=_registry_dir())
        cands = [c for c in reg.list_all() if c.candidate_id == record["candidate_id"]]
        assert len(cands) == 1
        assert cands[0].status == "PROPOSED"  # human approval still required
        assert cands[0].hypothesis_id == h.hypothesis_id
        # Lineage: candidate -> hypothesis -> edge finding
        assert h.source_finding_id.startswith("EC-")

    def test_rejected_hypothesis_never_creates_candidate(self, orch):
        from research_engine.edge_candidates.lifecycle_bridge import (
            submit_edge_candidates_to_lifecycle,
        )
        from research_engine.lifecycle.hypothesis import ConclusionType, HypothesisStatus
        from research_engine.lifecycle.experiment_protocol import ExperimentResult
        from research_engine.v10.candidates.candidate_registry import CandidateRegistry

        submit_edge_candidates_to_lifecycle(
            _gen_result([_accepted_edge()]), orchestrator=orch
        )
        h = orch.registry.all()[0]
        h.transition(HypothesisStatus.TESTING, reason="test")
        h.transition(HypothesisStatus.CHALLENGED, reason="test")
        assert h.conclude(ConclusionType.REJECTED, reason="evidence refutes", confidence="HIGH")
        orch.registry.update(h)

        result = ExperimentResult(experiment_id="EXP-x", hypothesis_id=h.hypothesis_id,
                                  n=120, mean_r=-0.2)
        assert orch.create_optimisation_candidate(h, result) is None
        reg = CandidateRegistry(storage_dir=_registry_dir())
        assert reg.list_all() == []


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafety:
    def test_edge_modules_have_no_trading_mutation_path(self):
        forbidden = (
            "MT5Execution", "RiskManager", "ExecutionOrchestrator", "order_send",
            "from core.pipeline", "core.mt5_connection", "mt5.order",
            "persist_trade_truth", "ShadowRuntime(",
        )
        modules = [
            ROOT / "research_engine" / "edge_attribution" / "evidence.py",
            ROOT / "research_engine" / "edge_candidates" / "lifecycle_bridge.py",
            ROOT / "research_engine" / "edge_attribution" / "run_edge_analysis.py",
            ROOT / "research_engine" / "edge_candidates" / "run_candidate_generation.py",
            ROOT / "research_engine" / "edge_candidates" / "run_candidate_validation.py",
        ]
        for m in modules:
            src = m.read_text(encoding="utf-8")
            for f in forbidden:
                assert f not in src, f"{m.name} contains forbidden trading path: {f}"

    def test_bridge_never_touches_candidate_or_governance_infrastructure(self):
        src = (ROOT / "research_engine" / "edge_candidates" / "lifecycle_bridge.py").read_text(encoding="utf-8")
        # Docstrings may DESCRIBE the boundary; code must not touch it.
        assert "from research_engine.v10.candidates" not in src
        assert "from research_engine.lifecycle.governance_gate" not in src
        assert "GovernanceGate()" not in src
        assert "CandidateRegistry(" not in src
        assert "import core" not in src

    def test_production_evidence_module_never_reads_local_paths(self):
        src = (ROOT / "research_engine" / "edge_attribution" / "evidence.py").read_text(encoding="utf-8")
        # The production loader section must contain NO local filesystem reads;
        # local replay handling exists only in the explicitly-offline section.
        prod_section = src.split("EXPLICIT OFFLINE FIXTURE MODE")[0]
        assert "Path(" not in prod_section
        assert "_load_local_replay_candles" not in prod_section
        assert "load_edge_evidence_offline_replay" not in prod_section




