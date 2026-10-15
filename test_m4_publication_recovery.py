"""Disposable transaction tests for the Milestone 4 publication recovery."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import m4_publication_recovery as recovery
from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.v10.continuous.continuous_projection_bridge import sync_latest_completed_cycle
from research_engine.v10.continuous.cycle_state import (
    ContinuousCycleStore, ContinuousResearchCycleResult,
)
from research_engine.v10.continuous.research_projection import (
    PROJECTION_SCHEMA, ResearchProjectionError, ResearchProjectionStore,
)


def projection(cycle, question, predecessor, snapshot):
    material = {
        "continuous_cycle_id": cycle,
        "canonical_question_cycle_id": question,
        "predecessor_projection_version": predecessor,
        "canonical_questions": [
            {"question_id": f"Q{i}", "cycle_snapshot_id": snapshot,
             "result": {"result_id": f"R{i}"}} for i in range(70)],
        "data_frontier": {"snapshot_id": snapshot},
    }
    version = "RPROJ-" + hashlib.sha256(canonical_json(material).encode(
        "utf-8")).hexdigest()[:32].upper()
    return {"projection_schema": PROJECTION_SCHEMA,
            "projection_version": version, **material}


def cycle(cycle_id, snapshot, question, bridge, version, predecessor):
    return ContinuousResearchCycleResult(
        continuous_cycle_id=cycle_id, cycle_outcome="COMPLETED",
        frontier_snapshot_id=snapshot, question_cycle_id=question,
        bridge_run_id=bridge, q71_agenda_id=None, q71_queue_id=None,
        validation_queue_version=recovery.EMPTY_QUEUE_DIGEST,
        projection_version=version, predecessor_cycle_id=predecessor,
        started_at="2026-10-15T00:00:00+00:00",
        completed_at="2026-10-15T01:00:00+00:00",
        stage_statuses={"PROJECTION": "COMPLETED"},
        projection_path="fixture-history", cycle_attempt_id="FIXTURE-ATTEMPT",
        evidence_identity=snapshot)


@pytest.fixture
def transaction(tmp_path, monkeypatch):
    root = tmp_path / "isolated"
    source = root / "continuous"
    lab = root / "lab_publication"
    prior = projection("CRCYCLE-PRIOR", "QCYCLE-PRIOR", None, "ISNAP-PRIOR")
    prior_store = ResearchProjectionStore(source / "projection")
    prior_store.save(prior)
    prior_cycle = cycle("CRCYCLE-PRIOR", "ISNAP-PRIOR", "QCYCLE-PRIOR",
                        "SBRIDGE-PRIOR", prior["projection_version"], None)
    ContinuousCycleStore(source / "cycles").save(prior_cycle, successful=True)
    assert sync_latest_completed_cycle(lab, source=source).status == "IMPORTED"

    successor = projection("CRCYCLE-TARGET", "QCYCLE-TARGET",
                           prior["projection_version"], "ISNAP-TARGET")
    seed = ResearchProjectionStore(tmp_path / "seed").save(successor)
    history = source / "projection/history" / (successor["projection_version"] + ".json")
    history.write_bytes(seed.read_bytes())
    completed = cycle("CRCYCLE-TARGET", "ISNAP-TARGET", "QCYCLE-TARGET",
                      "SBRIDGE-TARGET", successor["projection_version"],
                      "CRCYCLE-PRIOR")
    monkeypatch.setattr(recovery, "CYCLE", completed.continuous_cycle_id)
    monkeypatch.setattr(recovery, "VERSION", successor["projection_version"])
    monkeypatch.setattr(recovery, "IMMUTABLE_SHA256", recovery.sha256(history))
    monkeypatch.setattr(recovery, "BASELINE_POINTER_SHA256", recovery.sha256(
        source / "projection/latest.json"))
    monkeypatch.setattr(recovery, "FAILED", "CRCYCLE-FAILED-FIXTURE")
    return recovery.Plan(root, successor, completed, {"fixture": "sha256"}, False)


def test_valid_recovery_and_repeated_invocation(transaction):
    first = recovery.publish(transaction)
    source = transaction.root / "continuous"
    lab = transaction.root / "lab_publication"
    receipt = source / "cycles/history" / (recovery.CYCLE + ".json")
    receipt_hash = recovery.sha256(receipt)
    marker = lab / "continuous_bridge_state.json"
    marker_hash = recovery.sha256(marker)
    assert first["lab_bridge_status"] == "IMPORTED"
    second = recovery.publish(transaction)
    assert second["lab_bridge_status"] == "UNCHANGED"
    assert recovery.sha256(receipt) == receipt_hash
    assert recovery.sha256(marker) == marker_hash
    assert recovery.sha256(source / "projection/latest.json") == recovery.sha256(
        lab / "projection/latest.json")


@pytest.mark.parametrize("boundary", ["pointer", "receipt"])
def test_interruption_and_safe_retry(transaction, boundary):
    with pytest.raises(recovery.RecoveryError, match="INJECTED_INTERRUPTION"):
        recovery.publish(transaction, stop_after=boundary)
    assert recovery.sha256(transaction.root / "continuous/projection/latest.json") == (
        recovery.IMMUTABLE_SHA256)
    if boundary == "pointer":
        assert not (transaction.root / "continuous/cycles/history" /
                    (recovery.CYCLE + ".json")).exists()
    assert recovery.publish(transaction)["status"] == "PASS"


def test_conflicting_current_pointer_fails_without_receipt(transaction):
    pointer = transaction.root / "continuous/projection/latest.json"
    pointer.write_text("{}", encoding="utf-8")
    with pytest.raises(recovery.RecoveryError, match="POINTER_CHANGED"):
        recovery.publish(transaction)
    assert not (transaction.root / "continuous/cycles/history" /
                (recovery.CYCLE + ".json")).exists()


def test_conflicting_completion_receipt_fails(transaction):
    history = transaction.root / "continuous/cycles/history" / (recovery.CYCLE + ".json")
    history.write_text(json.dumps({"cycle_schema": "continuous_research_cycle_v1",
                                   "continuous_cycle_id": recovery.CYCLE,
                                   "cycle_outcome": "FAILED"}), encoding="utf-8")
    with pytest.raises(recovery.RecoveryError, match="CONFLICTING_COMPLETION"):
        recovery.publish(transaction)


def test_pointer_failure_preserves_operating_system_error(transaction, monkeypatch):
    store_atomic = ResearchProjectionStore._atomic

    def fail_latest(path, value):
        if Path(path).name == "latest.json":
            raise ResearchProjectionError(
                f"PROJECTION_PERSISTENCE_FAILED: PermissionError: injected; path={path}")
        return store_atomic(path, value)

    monkeypatch.setattr(ResearchProjectionStore, "_atomic", staticmethod(fail_latest))
    with pytest.raises(ResearchProjectionError, match="PermissionError: injected"):
        recovery.publish(transaction)
    assert not (transaction.root / "continuous/cycles/history" /
                (recovery.CYCLE + ".json")).exists()


def test_lab_parity_failure_has_no_success_result(transaction, monkeypatch):
    import research_engine.v10.continuous.continuous_projection_bridge as bridge_module
    atomic_copy = bridge_module._atomic_copy

    def corrupt_lab_latest(source, destination):
        atomic_copy(source, destination)
        if Path(destination) == transaction.root / "lab_publication/projection/latest.json":
            Path(destination).write_text("{}", encoding="utf-8")

    monkeypatch.setattr(bridge_module, "_atomic_copy", corrupt_lab_latest)
    with pytest.raises(recovery.RecoveryError, match="LAB_PROJECTION_PARITY_INVALID"):
        recovery.publish(transaction)
    assert (transaction.root / "continuous/cycles/history" /
            (recovery.CYCLE + ".json")).exists()


def test_projection_version_and_lineage_validation(transaction, monkeypatch):
    monkeypatch.setattr(recovery, "QUESTION", "QCYCLE-TARGET")
    monkeypatch.setattr(recovery, "SUCCESSOR", "ISNAP-TARGET")
    valid = transaction.projection
    ids = {f"Q{i}": f"R{i}" for i in range(70)}
    with pytest.raises(Exception, match="PROJECTION_IDENTITY_MISMATCH"):
        recovery.validate_projection({**valid, "data_frontier": {"snapshot_id": "tampered"}}, ids)
    monkeypatch.setattr(recovery, "VERSION", valid["projection_version"])
    with pytest.raises(recovery.RecoveryError, match="UNIFIED_PROJECTION_LINEAGE_INVALID"):
        recovery.validate_projection(valid, ids)


def test_invalid_question_and_scientific_receipts_and_archived_fingerprint(
        tmp_path, monkeypatch):
    failed = {"continuous_cycle_id": recovery.FAILED, "cycle_outcome": "FAILED",
              "failure_stage": "PROJECTION", "frontier_snapshot_id": recovery.SUCCESSOR,
              "question_cycle_id": recovery.QUESTION, "bridge_run_id": recovery.BRIDGE,
              "stage_statuses": {"FRONTIER": "NEW_VERIFIED_IMMUTABLE_SNAPSHOT_READY",
                                 "QUESTIONS": "COMPLETED",
                                 "SCIENTIFIC_STATE": "NO_SCIENTIFIC_STATE_CHANGE",
                                 "OBSERVATION_SPACE": "COMPLETED", "Q71_PLUS": "COMPLETED",
                                 "Q71_EXECUTION": "SKIPPED_NO_EVALUATOR_REGISTRY",
                                 "VALIDATION_QUEUE": "COMPLETED", "PROJECTION": "FAILED"},
              "q71_agenda_id": "AGD-TEST", "predecessor_cycle_id": "CRCYCLE-PRIOR",
              "cycle_attempt_id": "ATTEMPT"}
    question = SimpleNamespace(cycle_id=recovery.QUESTION,
                               snapshot_id=recovery.SUCCESSOR, cycle_status="COMPLETED",
                               total_questions=70, evaluated_count=11,
                               retained_count=58, alias_count=1,
                               complete_count=14, insufficient_count=37,
                               blocked_count=10, waiting_count=8,
                               result_ids={f"Q{i}": f"R{i}" for i in range(70)})
    scientific = {"bridge_run_id": recovery.BRIDGE,
                  "source_cycle_id": recovery.QUESTION,
                  "snapshot_id": recovery.SUCCESSOR,
                  "source_kind": "CANONICAL_QUESTION_CYCLE",
                  "status": "NO_SCIENTIFIC_STATE_CHANGE",
                  "findings_created": [], "hypotheses_created": [],
                  "candidates_created": [], "validation_handoff": []}
    replay = {"authority": {"scope_digest": "ARCHIVED-SCOPE"}}
    qprojection = {"replay_implementation_identity": recovery.ARCHIVED_IMPLEMENTATION,
                   "replay_scope_digest": "ARCHIVED-SCOPE",
                   "questions": dict(question.result_ids)}
    with pytest.raises(recovery.RecoveryError, match="QUESTION_ACCOUNTING_INVALID"):
        recovery.validate_small_records(tmp_path, failed,
                                        replace_namespace(question, total_questions=69),
                                        scientific, replay, qprojection)
    with pytest.raises(recovery.RecoveryError, match="SCIENTIFIC_BRIDGE_INVALID"):
        recovery.validate_small_records(tmp_path, failed, question,
                                        {**scientific, "status": "FAILED"},
                                        replay, qprojection)
    # The archived fingerprint is accepted only as provenance. A different
    # current implementation cannot be used to replay the question evaluator.
    with pytest.raises(recovery.RecoveryError, match="ARCHIVED_IMPLEMENTATION"):
        recovery.validate_small_records(tmp_path, failed, question, scientific,
                                        replay, {**qprojection,
                                                 "replay_implementation_identity": "CURRENT"})


def replace_namespace(value, **updates):
    return SimpleNamespace(**{**vars(value), **updates})
