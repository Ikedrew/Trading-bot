"""Pass D Repair 2 — governed meta/risk population adapters (G2/R3/R5).

These tests exercise the real resolver → runner boundary: the governed canonical
question cycle must inject the snapshot-bound G2 lineage population and the
HD10 R3/R4/R5 risk population rather than letting those runners reopen storage
or fall back to legacy loaders.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from research_engine.control_plane.governed_meta_risk_evidence import (
    GovernedLineagePopulation,
    build_governed_lineage_population,
    build_governed_risk_evidence,
)
from research_engine.control_plane.risk_policy_evidence import RiskPolicyEvidence
from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.experiments import risk_simulation_governed as governed
from research_engine.experiments.drawdown_threshold import run_drawdown_threshold
from research_engine.experiments.position_sizing import run_position_sizing
from research_engine.experiments.probability_of_ruin import run_probability_of_ruin
from research_engine.registry.research_question_registry import REGISTRY
from research_engine.v10.continuous.canonical_question_cycle import (
    run_canonical_question_cycle,
)
from research_engine.v10.investigation_snapshot import BOUND_DATASETS

from tests.test_canonical_question_cycle import MemoryS3, _freeze, _objects, _row, _shadow_events

RISK_SIMULATION_ERROR = governed.RiskSimulationError


def _run_real(tmp_path: Path, fake: MemoryS3):
    snapshot, manifests = _freeze(tmp_path, fake)
    cycle = run_canonical_question_cycle(
        {"snapshot_id": snapshot.snapshot_id,
         "fingerprint": snapshot.snapshot_fingerprint,
         "investigation_epoch": snapshot.evidence_epoch,
         "changed_datasets": list(BOUND_DATASETS)},
        source=S3ResearchDataSource(bucket="question-cycle-test", client=fake),
        manifest_directory=manifests,
        state_directory=tmp_path / "cycles",
    )
    projection = json.loads(
        (tmp_path / "cycles" / "current_projection.json").read_text())
    return snapshot, projection


# ─── adapter-level: deterministic, snapshot-bound populations ────────────────


def test_lineage_population_is_derived_from_bound_snapshot_records():
    opened, closed = _shadow_events()
    decision = _row("decision_trace")
    pop = build_governed_lineage_population([decision], [opened, closed])
    assert isinstance(pop, GovernedLineagePopulation)
    assert pop.identity_fields == ("entity_id", "canonical_opportunity_id")
    assert pop.decision_source == "decision_trace"
    assert pop.outcome_source == "shadow_runtime"
    assert [item["event_type"] for item in pop.outcome_records] == ["OPEN", "CLOSE"]


def test_risk_evidence_is_derived_from_bound_snapshot_records():
    opened, closed = _shadow_events()
    decision = _row("decision_trace")
    evidence = build_governed_risk_evidence([decision], [opened, closed])
    assert isinstance(evidence, RiskPolicyEvidence)


def test_populations_are_deterministic_under_reordered_inputs():
    opened, closed = _shadow_events()
    decision = _row("decision_trace")
    one = build_governed_lineage_population([decision], [opened, closed])
    again = build_governed_lineage_population([decision], [opened, closed])
    # Same bound input produces the identical population (membership and order).
    assert one.decision_records == again.decision_records
    assert list(one.outcome_records) == list(again.outcome_records)

    # Risk evidence orders by governed chronology, so reversed source order
    # yields the same deterministic population.
    ev_one = build_governed_risk_evidence([decision], [opened, closed])
    ev_two = build_governed_risk_evidence([decision], [closed, opened])
    assert ev_one.records == ev_two.records


def test_snapshot_a_evidence_cannot_leak_into_snapshot_b():
    opened, closed = _shadow_events()
    opened2, closed2 = deepcopy(_shadow_events())
    opened2["canonical_opportunity_id"] = closed2["canonical_opportunity_id"] = "nopp_2"
    opened2["shadow_trade_id"] = closed2["shadow_trade_id"] = "nshadow_ffffffffffffffff"

    pop_a = build_governed_lineage_population([_row("decision_trace")], [opened, closed])
    pop_b = build_governed_lineage_population(
        [{**_row("decision_trace"), "canonical_opportunity_id": "nopp_2"}],
        [opened2, closed2],
    )
    assert pop_a.decision_records != pop_b.decision_records
    assert pop_a.outcome_records != pop_b.outcome_records
    assert pop_a.outcome_records[0]["canonical_opportunity_id"] == "nopp_1"
    assert pop_b.outcome_records[0]["canonical_opportunity_id"] == "nopp_2"


# ─── missing evidence fails closed; risk absence is never zero risk ─────────


def test_missing_lineage_sources_are_flagged_explicitly():
    pop = build_governed_lineage_population([], [])
    assert pop.missing_evidence
    assert "shadow_runtime" in pop.missing_evidence[0]
    assert "decision_trace" in pop.missing_evidence[1]


def test_missing_risk_evidence_is_not_zero_risk():
    opened, closed = _shadow_events()
    evidence = build_governed_risk_evidence([], [opened, closed])
    assert evidence.records == ()
    report = run_probability_of_ruin(governed_risk_evidence=evidence)
    assert report["status"] != "COMPLETE"
    assert report["status"] in {"WAITING_DATA", "INSUFFICIENT_DATA", "BLOCKED"}


def test_runners_fail_closed_without_governed_evidence():
    with pytest.raises(RISK_SIMULATION_ERROR, match="GOVERNED_RISK_EVIDENCE_REQUIRED:R3"):
        run_probability_of_ruin()
    with pytest.raises(RISK_SIMULATION_ERROR, match="GOVERNED_RISK_EVIDENCE_REQUIRED:R4"):
        run_drawdown_threshold()
    with pytest.raises(RISK_SIMULATION_ERROR, match="GOVERNED_RISK_EVIDENCE_REQUIRED:R5"):
        run_position_sizing()


def test_governed_cycle_does_not_use_legacy_risk_loader(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("legacy risk loader called during governed cycle")

    monkeypatch.setattr(governed, "load_governed_risk_evidence", forbidden)
    monkeypatch.setattr(governed, "load_upstream_report", forbidden)

    fake = MemoryS3(_objects())
    snapshot, projection = _run_real(tmp_path, fake)
    for qid in ("R3", "R4", "R5"):
        result = projection["questions"][qid]["result"]
        assert result["status"] != "INVALID", (qid, result["failure_reason"])
        assert "DATASET_NOT_BOUND" not in str(result.get("failure_reason") or ""), qid


def test_cycle_injects_governed_populations_by_name(tmp_path):
    fake = MemoryS3(_objects())
    snapshot, manifests = _freeze(tmp_path, fake)
    captured = {}

    def capture_g2(governed_lineage_population=None, **kwargs):
        captured["G2"] = governed_lineage_population
        return {"status": "COMPLETE", "sample_n": 1, "conclusion": "captured"}

    def capture_r3(governed_risk_evidence=None, **kwargs):
        captured["R3"] = governed_risk_evidence
        return {"status": "COMPLETE", "sample_n": 1, "conclusion": "captured"}

    def capture_r5(governed_risk_evidence=None, **kwargs):
        captured["R5"] = governed_risk_evidence
        return {"status": "COMPLETE", "sample_n": 1, "conclusion": "captured"}

    runners = {q.id: (lambda **kw: {"status": "COMPLETE", "sample_n": 1})
               for q in REGISTRY}
    runners["G2"] = capture_g2
    runners["R3"] = capture_r3
    runners["R5"] = capture_r5

    run_canonical_question_cycle(
        {"snapshot_id": snapshot.snapshot_id,
         "fingerprint": snapshot.snapshot_fingerprint,
         "investigation_epoch": snapshot.evidence_epoch,
         "changed_datasets": list(BOUND_DATASETS)},
        source=S3ResearchDataSource(bucket="question-cycle-test", client=fake),
        manifest_directory=manifests,
        state_directory=tmp_path / "inj",
        runners=runners,
    )
    assert isinstance(captured.get("G2"), GovernedLineagePopulation)
    assert isinstance(captured.get("R3"), RiskPolicyEvidence)
    assert isinstance(captured.get("R5"), RiskPolicyEvidence)
    assert captured["R3"].records == captured["R5"].records


def test_g2_reaches_governed_runner_not_retired_exact_population(tmp_path):
    fake = MemoryS3(_objects())
    snapshot, projection = _run_real(tmp_path, fake)
    result = projection["questions"]["G2"]["result"]
    assert result["runner"] == "research_engine.experiments.lineage_coverage.run_g2"
    assert "CURRENT_EXACT_POPULATION_RETIRED" not in str(result.get("failure_reason") or "")
    assert result["status"] in {
        "COMPLETE", "INSUFFICIENT_DATA", "WAITING_FOR_DATA", "IMPLEMENTATION_BLOCKED", "NEGATIVE_RESULT",
    }


def test_r3_r5_reach_governed_runners(tmp_path):
    fake = MemoryS3(_objects())
    snapshot, projection = _run_real(tmp_path, fake)
    for qid in ("R3", "R5"):
        entry = projection["questions"][qid]
        assert entry["result"]["runner"], qid
        assert entry["result"]["snapshot_id"] == snapshot.snapshot_id, qid


def test_unrelated_question_runners_remain_unchanged(tmp_path):
    fake = MemoryS3(_objects())
    snapshot, projection = _run_real(tmp_path, fake)
    for qid in ("M1", "M2", "EX1"):
        entry = projection["questions"][qid]
        assert entry["result"]["runner"], qid
        assert entry["result"]["implementation_status"] in {
            "IMPLEMENTED", "IMPLEMENTED_WAITING_EVIDENCE", "ALIAS_OF_E1",
        }, qid
