"""Phase 2D publication fast-path regression tests (bounded, offline)."""
from __future__ import annotations
import pytest
import json
import time
from pathlib import Path
from types import SimpleNamespace
from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.v10.continuous.question_cycle_state import (
    CanonicalQuestionCycleResult, CanonicalQuestionResult,
    QuestionCycleStore, immutable_json)
from research_engine.v10.continuous.research_lab import (
    build_lab_view, clear_lab_view_cache, load_lab_view)
from research_engine.v10.continuous.research_projection import (
    PROJECTION_SCHEMA, ResearchProjectionError, ResearchProjectionStore,
    build_unified_research_projection)
from research_engine.v10.continuous.scientific_state_bridge import (
    run_scientific_state_bridge)
from research_engine.v10.continuous.scientific_state_store import (
    ScientificStateStore)
from research_engine.v10.continuous.validation_queue import ValidationQueueStore
from research_engine.v10.optimisation.optimisation_registry import (
    OptimisationRegistry)
STAMP = "2026-10-03T12:00:00+00:00"
def mkresult(snap, status="COMPLETE", qid="E1"):
    metrics = {"scientifically_meaningful": True, "effect_size": 0.25,
               "effect_direction": "POSITIVE",
               "falsification_criteria": ["expectancy_r <= 0 on replication"]}
    if status != "COMPLETE":
        metrics = {}
    return CanonicalQuestionResult(
        question_id=qid, question_version="registry:v1",
        snapshot_id=snap, snapshot_fingerprint="fp-" + snap,
        investigation_epoch="epoch-" + snap, evaluated_at=STAMP,
        status=status,
        substantive_answer={"scientific_conclusion": "ok"} if status == "COMPLETE" else None,
        sample_n=200 if status == "COMPLETE" else None,
        minimum_required_n=50 if status == "COMPLETE" else None,
        sample_deficit=0 if status == "COMPLETE" else None,
        key_metrics=metrics, confidence="HIGH" if status == "COMPLETE" else None,
        statistical_output={"confidence_interval": [0.1, 0.4]} if status == "COMPLETE" else None,
        evidence_datasets=("trade_truth",) if status == "COMPLETE" else (),
        evidence_references=({"dataset": "trade_truth", "sha256": "abc"},) if status == "COMPLETE" else (),
        limitations=("observational",) if status == "COMPLETE" else (),
        implementation_status="IMPLEMENTED",
        runner="fake.runner", runner_version="v1")


def mkcycle(store, num, changed):
    snap = changed.snapshot_id
    cid = "QCYCLE-%032X" % num
    qs, rids = {}, {}
    for qid in BASELINE_QUESTION_IDS:
        row = changed if qid == changed.question_id else CanonicalQuestionResult(
            question_id=qid, question_version="registry:v1",
            snapshot_id=snap, snapshot_fingerprint="fp-" + snap,
            investigation_epoch="epoch-" + snap, evaluated_at=STAMP,
            status="WAITING_FOR_DATA")
        rids[qid] = row.result_id
        qs[qid] = {"result": row.to_dict()}
    proj = {"cycle_schema": "canonical_question_cycle_v1", "cycle_id": cid,
            "cycle_snapshot_id": snap, "questions": qs}
    cyc = CanonicalQuestionCycleResult(
        cycle_id=cid, snapshot_id=snap, fingerprint="fp-" + snap,
        investigation_epoch="epoch-" + snap, predecessor_cycle_id=None,
        predecessor_snapshot_id=None,
        frontier_start="2026-10-01T00:00:00+00:00",
        frontier_end="2026-10-03T00:00:00+00:00",
        total_questions=70, evaluated_count=1, retained_count=69,
        complete_count=1, negative_result_count=0, insufficient_count=0,
        waiting_count=69, blocked_count=0, unimplemented_count=0,
        alias_count=0, changed_question_ids=(changed.question_id,),
        unchanged_question_ids=tuple(
            q for q in BASELINE_QUESTION_IDS if q != changed.question_id),
        failed_question_ids=(), cycle_status="COMPLETED",
        started_at=STAMP, completed_at=STAMP,
        current_projection_path=str(store.current_path),
        cycle_history_path=str(store.cycle_path(cid)),
        question_history_root=str(store.question_history_directory),
        result_ids=rids)
    store.save_question_result(changed)
    immutable_json(store.projection_path(cid), proj)
    store.save_cycle(cyc)
    return cyc
def mkfrontier(snap):
    return SimpleNamespace(
        snapshot_id=snap, fingerprint="fp-" + snap,
        investigation_epoch="epoch-" + snap,
        frontier_start="2026-10-01", frontier_end="2026-10-03",
        predecessor_snapshot_id=None, changed_datasets=("trade_truth",),
        stale_datasets=(), missing_optional_datasets=(),
        status="SNAPSHOT_READY")
def mkenv(tmp, snap, num=1):
    qs = QuestionCycleStore(tmp / "questions")
    cyc = mkcycle(qs, num, mkresult(snap))
    reg = OptimisationRegistry(str(tmp / "optimisation"))
    sci = ScientificStateStore(tmp / "scientific")
    bridge = run_scientific_state_bridge(
        cyc, question_store=qs, scientific_store=sci,
        optimisation_registry=reg,
        treatment_memory_path=tmp / "memory.json", policy_catalog=())
    qproj = json.loads(qs.projection_path(cyc.cycle_id).read_text())
    return qs, cyc, reg, sci, bridge, qproj
def build(tmp, qproj, bridge, sci, reg, snap):
    return build_unified_research_projection(
        continuous_cycle_id="CRCYCLE-P2D", frontier=mkfrontier(snap),
        question_projection=qproj, bridge=bridge,
        scientific_store=sci, optimisation_registry=reg,
        validation_store=ValidationQueueStore(tmp / "validation.json"))
def authoritative(proj):
    return {k: v for k, v in proj.items() if k != "projection_generated_at"}

def test_unchanged_snapshot_republication_is_byte_identical(tmp_path):
    clear_lab_view_cache()
    qs, cyc, reg, sci, bridge, qproj = mkenv(tmp_path, "ISNAP-1")
    store = ResearchProjectionStore(tmp_path / "projection")
    first = build(tmp_path, qproj, bridge, sci, reg, "ISNAP-1")
    store.save(first)
    hist = store.history_directory / (first["projection_version"] + ".json")
    mtime_hist = hist.stat().st_mtime_ns
    mtime_latest = store.latest_path.stat().st_mtime_ns
    time.sleep(0.02)
    second = build(tmp_path, qproj, bridge, sci, reg, "ISNAP-1")
    assert canonical_json(authoritative(first)) == canonical_json(authoritative(second))
    assert second["projection_version"] == first["projection_version"]
    store.save(second)
    assert hist.stat().st_mtime_ns == mtime_hist
    assert store.latest_path.stat().st_mtime_ns == mtime_latest
    view = load_lab_view(projection_directory=tmp_path / "projection")
    assert view["projection_version"] == first["projection_version"]
def test_changed_snapshot_republishes_new_version(tmp_path):
    clear_lab_view_cache()
    qs, cyc, reg, sci, bridge, qproj = mkenv(tmp_path, "ISNAP-1")
    store = ResearchProjectionStore(tmp_path / "projection")
    first = build(tmp_path, qproj, bridge, sci, reg, "ISNAP-1")
    store.save(first)
    qs2 = QuestionCycleStore(tmp_path / "q2")
    cyc2 = mkcycle(qs2, 2, mkresult("ISNAP-2"))
    reg2 = OptimisationRegistry(str(tmp_path / "opt2"))
    sci2 = ScientificStateStore(tmp_path / "sci2")
    bridge2 = run_scientific_state_bridge(
        cyc2, question_store=qs2, scientific_store=sci2,
        optimisation_registry=reg2,
        treatment_memory_path=tmp_path / "mem2.json", policy_catalog=())
    qproj2 = json.loads(qs2.projection_path(cyc2.cycle_id).read_text())
    second = build(tmp_path, qproj2, bridge2, sci2, reg2, "ISNAP-2")
    assert second["data_frontier"]["snapshot_id"] == "ISNAP-2"
    assert second["projection_version"] != first["projection_version"]
    e1 = next(r for r in second["canonical_questions"] if r["question_id"] == "E1")
    assert e1["result"]["snapshot_id"] == "ISNAP-2"
def test_scientific_state_change_same_snapshot_republishes(tmp_path):
    clear_lab_view_cache()
    qs, cyc, reg, sci, bridge, qproj = mkenv(tmp_path, "ISNAP-1")
    first = build(tmp_path, qproj, bridge, sci, reg, "ISNAP-1")
    e1 = next(r for r in first["canonical_questions"] if r["question_id"] == "E1")
    assert e1["result"]["status"] == "COMPLETE"
    assert first["what_changed"]["questions_changed"] == ["E1"]
    assert e1["result"]["snapshot_id"] == "ISNAP-1"
def test_candidate_governance_change_invalidates_lab_view(tmp_path):
    clear_lab_view_cache()
    qs, cyc, reg, sci, bridge, qproj = mkenv(tmp_path, "ISNAP-1")
    store = ResearchProjectionStore(tmp_path / "projection")
    proj = build(tmp_path, qproj, bridge, sci, reg, "ISNAP-1")
    store.save(proj)
    before = load_lab_view(projection_directory=tmp_path / "projection")
    assert before["projection_version"] == proj["projection_version"]
    mutated = dict(proj)
    mutated["projection_version"] = "RPROJ-" + "0" * 32
    with open(store.latest_path, "w", encoding="utf-8") as fh:
        json.dump(mutated, fh, indent=2, sort_keys=True)
    after = load_lab_view(projection_directory=tmp_path / "projection")
    assert after["projection_version"] == mutated["projection_version"]
def test_restart_recovery_reuses_history_without_collision(tmp_path):
    clear_lab_view_cache()
    qs, cyc, reg, sci, bridge, qproj = mkenv(tmp_path, "ISNAP-1")
    store = ResearchProjectionStore(tmp_path / "projection")
    proj = build(tmp_path, qproj, bridge, sci, reg, "ISNAP-1")
    path = store.save(proj)
    assert path.exists()
    store2 = ResearchProjectionStore(tmp_path / "projection")
    latest = store2.load_latest()
    assert latest is not None
    assert latest["projection_version"] == proj["projection_version"]
    path2 = store2.save(latest)
    assert path2 == path
    assert canonical_json(json.loads(path2.read_text())) == canonical_json(proj)
def test_corrupt_or_incomplete_projection_fails_closed(tmp_path):
    clear_lab_view_cache()
    store = ResearchProjectionStore(tmp_path / "projection")
    store.latest_path.parent.mkdir(parents=True, exist_ok=True)
    store.latest_path.write_text("{bad json", encoding="utf-8")
    try:
        load_lab_view(projection_directory=tmp_path / "projection")
    except Exception as exc:
        assert "LAB_PROJECTION_UNREADABLE" in str(exc) or "LATEST" in str(exc)
    else:
        raise AssertionError("corrupt latest.json must fail closed")
    store.latest_path.write_text(json.dumps({"projection_schema": "wrong"}), encoding="utf-8")
    from research_engine.v10.continuous import research_lab as labmod
    labmod.clear_lab_view_cache()
    try:
        load_lab_view(projection_directory=tmp_path / "projection")
    except Exception as exc:
        assert "SCHEMA" in str(exc) or "LAB_" in str(exc)
    else:
        raise AssertionError("wrong-schema projection must fail closed")
    try:
        store.save({"projection_schema": PROJECTION_SCHEMA})
    except ResearchProjectionError as exc:
        assert "IDENTITY_INVALID" in str(exc)
    else:
        raise AssertionError("identity-invalid save must fail closed")
def test_published_authoritative_fields_exact_equivalence(tmp_path):
    clear_lab_view_cache()
    qs, cyc, reg, sci, bridge, qproj = mkenv(tmp_path, "ISNAP-1")
    proj = build(tmp_path, qproj, bridge, sci, reg, "ISNAP-1")
    assert len(proj["canonical_questions"]) == 70
    assert {r["question_id"] for r in proj["canonical_questions"]} == set(BASELINE_QUESTION_IDS)
    e1 = next(r for r in proj["canonical_questions"] if r["question_id"] == "E1")
    assert e1["result"]["snapshot_id"] == "ISNAP-1"
    assert e1["result"]["result_id"] == mkresult("ISNAP-1").result_id
    assert proj["data_frontier"]["snapshot_id"] == cyc.snapshot_id
    assert proj["canonical_question_cycle_id"] == cyc.cycle_id
    # Canonical evaluation state belongs to the result, not generated lifecycle.
    assert e1["result"]["status"] == "COMPLETE"
    document = sci.document
    assert e1["linked_findings"]  # Exercise real scientific relationships.
    for row in proj["canonical_questions"]:
        qid = row["question_id"]
        assert row["result"] == qproj["questions"][qid]["result"]
        assert row["result"]["snapshot_id"] == cyc.snapshot_id
        assert row["result"]["result_id"] == cyc.result_ids[qid]
        assert row["changed_this_cycle"] == (qid in bridge.question_changes_processed)
        expected_findings = sorted(
            fid for fid, versions in document["findings"].items()
            if qid in (versions[-1].get("source_question_ids")
                       or [versions[-1].get("source_question_id")]))
        assert row["linked_findings"] == expected_findings
        for field, dependency in (("linked_hypotheses", "hypotheses"),
                                  ("linked_candidates", "candidates")):
            assert row[field] == sorted({
                linked_id for fid in expected_findings
                for linked_id in document["dependencies"].get(fid, {}).get(dependency, [])})

    # Bridge identity is authoritative in its separate immutable run receipt.
    receipt = sci.load_run(bridge.bridge_run_id)
    assert receipt is not None
    assert canonical_json(receipt) == canonical_json(bridge.to_dict())
    assert receipt["bridge_run_id"] == bridge.bridge_run_id
    assert receipt["snapshot_id"] == cyc.snapshot_id
    assert receipt["source_cycle_id"] == cyc.cycle_id
    view = build_lab_view(proj)
    assert view["projection_version"] == proj["projection_version"]
    assert view["complete_count"] >= 1


@pytest.mark.parametrize("artifact", ["history", "latest"])
@pytest.mark.parametrize("damage", ["missing", "malformed", "incomplete", "changed"])
def test_duplicate_save_verifies_persisted_artifacts(tmp_path, artifact, damage):
    _, _, reg, sci, bridge, qproj = mkenv(tmp_path, "ISNAP-1")
    proj = build(tmp_path, qproj, bridge, sci, reg, "ISNAP-1")
    store = ResearchProjectionStore(tmp_path / "projection")
    history = store.save(proj)
    target = history if artifact == "history" else store.latest_path
    if damage == "missing":
        target.unlink()
    elif damage == "malformed":
        target.write_text("{bad json", encoding="utf-8")
    else:
        damaged = json.loads(json.dumps(proj))
        if damage == "incomplete":
            del damaged["canonical_questions"]
        else:
            damaged["canonical_questions"][0]["result"]["status"] = "BLOCKED"
        target.write_text(json.dumps(damaged), encoding="utf-8")
    if artifact == "history" and damage != "missing":
        before = store.latest_path.read_bytes()
        with pytest.raises(ResearchProjectionError, match=(
                "HISTORY_PROJECTION_UNREADABLE" if damage == "malformed"
                else "PROJECTION_IDENTITY_COLLISION")):
            store.save(proj)
        assert store.latest_path.read_bytes() == before
    else:
        assert store.save(proj) == history
        assert canonical_json(json.loads(history.read_text())) == canonical_json(proj)
        assert canonical_json(store.load_latest()) == canonical_json(proj)
        # The repaired artifact remains valid after a fresh store is constructed.
        restarted = ResearchProjectionStore(store.directory)
        assert restarted.save(proj) == history
        assert canonical_json(restarted.load_latest()) == canonical_json(proj)


def test_duplicate_save_latest_repair_failure_and_retry(tmp_path, monkeypatch):
    _, _, reg, sci, bridge, qproj = mkenv(tmp_path, "ISNAP-1")
    proj = build(tmp_path, qproj, bridge, sci, reg, "ISNAP-1")
    store = ResearchProjectionStore(tmp_path / "projection")
    history = store.save(proj)
    original_history = history.read_bytes()
    store.latest_path.unlink()
    atomic = store._atomic
    def fail(path, value):
        raise ResearchProjectionError("PROJECTION_PERSISTENCE_FAILED")
    monkeypatch.setattr(store, "_atomic", fail)
    with pytest.raises(ResearchProjectionError, match="PROJECTION_PERSISTENCE_FAILED"):
        store.save(proj)
    assert history.read_bytes() == original_history
    assert not store.latest_path.exists()
    monkeypatch.setattr(store, "_atomic", atomic)
    store.save(proj)
    assert canonical_json(store.load_latest()) == canonical_json(proj)


def governed_candidate(tmp_path):
    from research_engine.v10.optimisation.models import OptimisationCandidate
    qs, cyc, reg, sci, bridge, qproj = mkenv(tmp_path, "ISNAP-1")
    hypothesis = reg.list_hypotheses()[0]
    candidate = OptimisationCandidate(
        candidate_id="C-P2D", hypothesis_id=hypothesis.hypothesis_id,
        baseline_id="BASELINE-P2D", created_at=STAMP,
        policy_id="POLICY-P2D", treatment_hash="TREATMENT-P2D")
    reg.add_candidate(candidate)
    document = sci.document
    finding_ids = [
        fid for fid, links in document["dependencies"].items()
        if hypothesis.hypothesis_id in links["hypotheses"]]
    assert len(finding_ids) == 1
    finding_id = finding_ids[0]
    finding = document["findings"][finding_id][-1]
    finding_ref = f"{finding_id}:v{finding['finding_version']}"
    result = qproj["questions"][hypothesis.source_question]["result"]
    candidate.source_finding_versions = [finding_ref]
    candidate.source_question_results = [result["result_id"]]
    # Match the bridge's candidate creation representation: dependency edge,
    # current finding-version reference, and a lineage-bearing history row.
    document["dependencies"][finding_id]["candidates"].append(candidate.candidate_id)
    document["current"]["candidates"][candidate.candidate_id] = finding_ref
    document["candidate_history"][candidate.candidate_id] = [{
        "candidate_id": candidate.candidate_id,
        "status": candidate.status,
        "finding_version": finding_ref,
        "hypothesis_id": hypothesis.hypothesis_id,
        "question_result_id": result["result_id"],
        "snapshot_id": result["snapshot_id"],
        "policy_id": candidate.policy_id,
        "treatment_hash": candidate.treatment_hash,
    }]
    sci.replace_document(document)
    assert sci.document["current"]["candidates"][candidate.candidate_id] == finding_ref
    assert sci.document["candidate_history"][candidate.candidate_id][0]["finding_version"] == finding_ref
    reg.save()
    return reg, sci, bridge, qproj, candidate


def assert_governed_republication(tmp_path, store, before, after, sci, bridge):
    assert after["projection_version"] != before["projection_version"]
    assert after["canonical_questions"] == before["canonical_questions"]
    assert after["findings"] == before["findings"]
    assert after["hypotheses"] == before["hypotheses"]
    assert after["data_frontier"] == before["data_frontier"]
    assert canonical_json(sci.load_run(bridge.bridge_run_id)) == canonical_json(bridge.to_dict())
    store.save(after)
    restarted = ResearchProjectionStore(store.directory)
    assert canonical_json(restarted.load_latest()) == canonical_json(after)
    view = load_lab_view(projection_directory=store.directory)
    assert view["projection_version"] == after["projection_version"]
    assert canonical_json(authoritative(after)) == canonical_json(authoritative(store.load_latest()))
    assert len(after["canonical_questions"]) == 70


def test_nonempty_validation_blocker_invalidates_publication(tmp_path):
    from research_engine.v10.continuous.validation_queue import (
        ValidationJob, validation_job_id, process_validation_queue)
    clear_lab_view_cache()
    reg, sci, bridge, qproj, candidate = governed_candidate(tmp_path)
    queue = ValidationQueueStore(tmp_path / "validation.json")
    plan = {"minimum_sample": 50}
    job = ValidationJob(
        job_id=validation_job_id("VALIDATION", candidate.candidate_id,
                                 candidate.policy_id, candidate.treatment_hash,
                                 plan, "ISNAP-1"),
        kind="VALIDATION", candidate_id=candidate.candidate_id,
        policy_id=candidate.policy_id, treatment_hash=candidate.treatment_hash,
        validation_plan=plan, source_snapshot_id="ISNAP-1",
        source_cycle_id=qproj["cycle_id"])
    queue.add(job)
    before = build(tmp_path, qproj, bridge, sci, reg, "ISNAP-1")
    store = ResearchProjectionStore(tmp_path / "projection")
    store.save(before)
    load_lab_view(projection_directory=store.directory)
    # Existing queue governance blocks when an executor is unavailable.
    process_validation_queue(store=queue, registry=reg, max_jobs=1)
    assert job.status == "BLOCKED"
    assert job.failure_reason == "VALIDATION_EXECUTOR_UNAVAILABLE"
    after = build(tmp_path, qproj, bridge, sci, reg, "ISNAP-1")
    assert after["what_changed"]["validation_transitions"] == [job.to_dict()]
    assert after["candidates"][0]["validation_queue"] == [job.to_dict()]
    assert_governed_republication(tmp_path, store, before, after, sci, bridge)
    view = load_lab_view(projection_directory=store.directory)
    assert view["validation_blocked"] == [job.to_dict()]
    assert not after["candidates"][0]["live_approved"]


def test_candidate_shadow_transition_invalidates_without_live_authority(tmp_path):
    clear_lab_view_cache()
    reg, sci, bridge, qproj, candidate = governed_candidate(tmp_path)
    reg.update_candidate_status(candidate.candidate_id, "FORWARD_VALIDATED")
    before = build(tmp_path, qproj, bridge, sci, reg, "ISNAP-1")
    store = ResearchProjectionStore(tmp_path / "projection")
    store.save(before)
    load_lab_view(projection_directory=store.directory)
    reg.bind_shadow_candidate(
        candidate.candidate_id, policy_id=candidate.policy_id,
        treatment_hash=candidate.treatment_hash,
        binding={"live_approved": False, "activated_at": STAMP,
                 "forward_validation_record": "OFFLINE-P2D-RECORD"})
    reg.save()
    after = build(tmp_path, qproj, bridge, sci, reg, "ISNAP-1")
    row = after["candidates"][0]
    assert row["status"] == "SHADOW_VALIDATION_ACTIVE"
    assert row["shadow_binding"] == candidate.shadow_binding
    assert row["runtime_authority"] == "NOT_LIVE"
    assert row["human_approval_required"] is True
    assert row["live_approved"] is False
    e1 = next(r for r in after["canonical_questions"] if r["question_id"] == "E1")
    assert candidate.candidate_id in e1["linked_candidates"]
    assert_governed_republication(tmp_path, store, before, after, sci, bridge)
