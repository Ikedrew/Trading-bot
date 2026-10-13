"""Offline source-authority qualification for the ten Gate B4 questions."""

from __future__ import annotations

import json

import pytest

from core.production_data_contract import s3_base_prefix
from research_engine.control_plane.evidence_provenance import evidence_digest
from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.experiments import legacy_canonical
from research_engine.registry.research_question_registry import REGISTRY
from research_engine.v10.continuous.canonical_question_cycle import (
    run_canonical_question_cycle,
)
from research_engine.v10.investigation_snapshot import BOUND_DATASETS
from test_canonical_question_cycle import MemoryS3, _freeze, _objects
from test_shadow_market_provenance_migration import _shadow


BLOCKED_SOURCE = {
    "D6": "portfolio_rankings",
    "MGMT-1": "management_actions",
    "MGMT-2": "management_actions",
    "HORIZON-1": "horizon_candidates",
    "STRAT-1": "strategy_candidates",
    "PORT-1": "portfolio_rankings",
    "OPP-1": "horizon_candidates",
}


@pytest.fixture(scope="module")
def bound_cycle(tmp_path_factory):
    root = tmp_path_factory.mktemp("gate-b4-bound-cycle")
    fake = MemoryS3(_objects())
    snapshot, manifests = _freeze(root, fake)
    cycle = run_canonical_question_cycle(
        {
            "snapshot_id": snapshot.snapshot_id,
            "fingerprint": snapshot.snapshot_fingerprint,
            "investigation_epoch": snapshot.evidence_epoch,
            "changed_datasets": list(BOUND_DATASETS),
        },
        source=S3ResearchDataSource(bucket="question-cycle-test", client=fake),
        manifest_directory=manifests,
        state_directory=root / "question-state",
    )
    projection = json.loads(
        (root / "question-state" / "current_projection.json").read_text()
    )
    assert cycle.total_questions == len(REGISTRY) == 70
    assert set(projection["questions"]) == {question.id for question in REGISTRY}
    assert all(any(key.startswith(s3_base_prefix(dataset)) for dataset in BOUND_DATASETS)
               for key in fake.get_calls)
    return snapshot, projection


@pytest.mark.parametrize("question_id,expected", [
    ("E2", "INSUFFICIENT_DATA"),
    ("L2", "BLOCKED"),
    ("G3", "BLOCKED"),
])
def test_repaired_questions_fail_closed_on_sparse_bound_snapshot(
    bound_cycle, question_id, expected,
):
    snapshot, projection = bound_cycle
    result = projection["questions"][question_id]["result"]
    assert result["status"] == expected
    assert result["snapshot_id"] == snapshot.snapshot_id
    assert result["failure_reason"] is None
    assert result["runner"]
    assert all(reference["dataset"] in BOUND_DATASETS
               for reference in result["evidence_references"])
    if question_id == "E2":
        assert result["sample_n"] == 1
        assert {reference["dataset"] for reference in result["evidence_references"]} == {
            "shadow_runtime"
        }
    elif question_id == "L2":
        assert result["reason_code"] == "SNAPSHOT_REPORT_HISTORY_UNAVAILABLE"
        assert result["evidence_references"] == []
    else:
        assert any("Canonical QuestionState population was not supplied" in limitation
                   for limitation in result["limitations"])
        assert result["evidence_references"] == []


@pytest.mark.parametrize("question_id,source", sorted(BLOCKED_SOURCE.items()))
def test_unbound_question_waits_for_its_declared_source(
    bound_cycle, question_id, source,
):
    snapshot, projection = bound_cycle
    result = projection["questions"][question_id]["result"]
    assert result["status"] == "WAITING_FOR_DATA"
    assert result["snapshot_id"] == snapshot.snapshot_id
    assert result["failure_reason"] == f"{source}: Dataset not supplied"
    assert source in result["missing_evidence"]
    assert all(reference["dataset"] in BOUND_DATASETS
               for reference in result["evidence_references"])


def test_e2_uses_injected_bound_population_and_calculates_pattern_mean(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("E2 attempted an unbound evidence read")

    monkeypatch.setattr(legacy_canonical, "_load_jsonl", forbidden)
    monkeypatch.setattr(
        "research_engine.data_access.shadow_runtime_ingestion.ingest_completed_shadow_trades",
        forbidden,
    )
    current = [_shadow(index, pnl=1.0) for index in range(5)]
    for record in current:
        record["decision_snapshot"]["pattern"] = "HAMMER"
    stale = _shadow(100, epoch="LEGACY", pnl=-5.0)
    stale["decision_snapshot"]["pattern"] = "HAMMER"
    report = legacy_canonical.run_q05([*current, stale])
    assert report["status"] == "COMPLETE"
    assert report["dataset"]["sample_size"] == 5
    assert report["overall"]["pattern_performance"]["HAMMER"]["avg_r"] == 1.0
    component = report["fingerprint"]["evidence_provenance"]["components"][0]
    assert component["digest"] == evidence_digest(current)
    assert component["records_excluded"] == 1


def test_e2_does_not_complete_without_an_estimable_pattern(monkeypatch):
    monkeypatch.setattr(legacy_canonical, "_load_jsonl", lambda *args: [])
    report = legacy_canonical.run_q05([_shadow(0, pnl=1.0)])
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["overall"]["pattern_performance"] == {}


def test_e2_standalone_path_uses_canonical_runtime_only(monkeypatch):
    current = [_shadow(index, pnl=1.0) for index in range(5)]
    for record in current:
        record["decision_snapshot"]["pattern"] = "HAMMER"

    def forbidden(*args, **kwargs):
        raise AssertionError("E2 read the excluded research_shadow_trades source")

    monkeypatch.setattr(legacy_canonical, "_load_jsonl", forbidden)
    monkeypatch.setattr(
        "research_engine.data_access.shadow_runtime_ingestion.ingest_completed_shadow_trades",
        lambda: current,
    )
    report = legacy_canonical.run_q05()
    assert report["status"] == "COMPLETE"
    assert report["dataset"]["sample_size"] == 5


def test_l2_never_counts_unauthenticated_local_reports(monkeypatch):
    from pathlib import Path

    def forbidden(*args, **kwargs):
        raise AssertionError("L2 inspected the local report directory")

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "exists", forbidden)
        scoped.setattr(Path, "glob", forbidden)
        report = legacy_canonical.run_q15()
    assert report["status"] == "BLOCKED"
    assert report["overall"]["reports_generated"] is None
    assert report["dataset"]["sample_size"] == 0
