"""Stage B Wave 3 governed fast/deep scheduling acceptance tests."""
from __future__ import annotations

from datetime import date
import json
import time
from types import SimpleNamespace

import pytest

from research_engine.registry.research_question_registry import REGISTRY
from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.v10.continuous.research_loop import (
    run_continuous_research_cycle,
    run_deep_research_job,
)
from research_engine.v10.continuous.research_projection import ResearchProjectionStore
from research_engine.v10.continuous.research_projection import (
    build_research_work_refresh_projection,
)
from research_engine.v10.continuous.research_work_queue import (
    COMPLETED,
    DEEP,
    FAILED,
    FAST,
    PENDING,
    SUPERSEDED,
    EvaluatorExecutionMetadata,
    ResearchExecutionPolicy,
    ResearchWorkQueueError,
    ResearchWorkQueueStore,
    deep_work_job,
    process_one_deep_job,
)
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry
from test_canonical_question_cycle import (
    MemoryS3,
    _freeze,
    _key,
    _objects,
    _projection,
    _run,
    _runners,
    RecordingExecutor,
)


def _question(question_id):
    return next(question for question in REGISTRY if question.id == question_id)


def _job(
    question_id: str, snapshot_id: str, epoch_id: str, queued_at: str,
    *, dependency: str = "dependency", end: str = "2026-09-25",
):
    question = _question(question_id)
    policy = ResearchExecutionPolicy().classify(question, population_records=100)
    return deep_work_job(
        question_id=question_id,
        evaluator=policy["evaluator"],
        evaluator_identity_digest="evaluator-digest",
        snapshot_id=snapshot_id,
        epoch_id=epoch_id,
        dependency_identity=dependency,
        prerequisite_identities={},
        policy=policy,
        frontier_start="2026-09-01",
        frontier_end=end,
        queued_at=queued_at,
    )


def test_execution_policy_uses_evaluator_metadata_and_measured_runtime():
    policy = ResearchExecutionPolicy()
    g1 = policy.classify(_question("G1"), population_records=214_247)
    x6 = policy.classify(_question("X6"), population_records=214_247)

    assert g1["execution_class"] == DEEP
    assert g1["evaluator"].endswith("dataset_suitability.run_g1")
    assert g1["measured_runtime_seconds"] == 3025.07
    assert g1["population_wide_scan"] is True
    assert "EXPLICIT_NOT_INLINE_SAFE" in g1["reasons"]
    assert x6["execution_class"] == FAST
    assert x6["measured_runtime_seconds"] == 278.54

    custom = ResearchExecutionPolicy(evaluator_metadata={
        "example.module.run": EvaluatorExecutionMetadata(
            measured_runtime_seconds=1001.0)
    })
    decision = custom.classify(SimpleNamespace(
        id="NOT_G1", runner_module="example.module", runner_function="run"))
    assert decision["execution_class"] == DEEP
    assert decision["reasons"] == ["MEASURED_RUNTIME_EXCEEDS_POLICY"]


def test_fast_only_delta_publishes_without_deep_work(tmp_path):
    fake = MemoryS3(_objects())
    first, manifests = _freeze(tmp_path, fake)
    queue = ResearchWorkQueueStore(tmp_path / "deep.json")
    _run(tmp_path, fake, first, manifests, deep_work_store=queue)

    key = _key("decision_trace")
    fake.objects[key] = fake.objects[key].replace("\n", " \n", 1)
    second, _ = _freeze(tmp_path, fake)
    result = _run(
        tmp_path, fake, second, manifests,
        predecessor_snapshot_id=first.snapshot_id,
        changed_datasets=["decision_trace"],
        deep_work_store=queue,
    )
    projection = _projection(tmp_path)

    assert result.cycle_status == "COMPLETED"
    assert result.deep_pending_question_ids == ()
    assert not [job for job in queue.ordered() if job.state == PENDING]
    assert projection["questions"]["D1"]["work_state"] == "FRESH"
    assert projection["questions"]["G1"]["work_state"] == "RETAINED_UNCHANGED"


def test_deep_and_fast_delta_defers_g1_without_false_freshness(tmp_path):
    fake = MemoryS3(_objects())
    first, manifests = _freeze(tmp_path, fake)
    queue = ResearchWorkQueueStore(tmp_path / "deep.json")
    _run(tmp_path, fake, first, manifests, deep_work_store=queue)
    first_projection = _projection(tmp_path)
    first_g1 = first_projection["questions"]["G1"]["result"]

    key = _key("shadow_runtime")
    fake.objects[key] = fake.objects[key].replace("\n", " \n", 1)
    second, _ = _freeze(tmp_path, fake)
    result = _run(
        tmp_path, fake, second, manifests,
        predecessor_snapshot_id=first.snapshot_id,
        changed_datasets=["shadow_runtime"],
        deep_work_store=queue,
    )
    projection = _projection(tmp_path)
    g1 = projection["questions"]["G1"]
    fast = projection["questions"]["E1"]

    assert result.cycle_status == "COMPLETED"
    assert result.deep_pending_question_ids == ("G1",)
    assert g1["result"]["result_id"] == first_g1["result_id"]
    assert g1["last_evaluated_snapshot_id"] == first.snapshot_id
    assert g1["retained_previous"] is True
    assert g1["work_state"] == "DEEP_PENDING"
    assert g1["execution_freshness"] == "DEEP_STALE"
    assert g1["triggering_evidence_epoch"] == second.evidence_epoch
    assert fast["last_evaluated_snapshot_id"] == second.snapshot_id
    assert fast["work_state"] != "DEEP_PENDING"
    jobs = queue.ordered()
    assert len(jobs) == 1
    assert jobs[0].question_id == "G1"
    assert jobs[0].state == PENDING


def test_continuous_fast_path_publishes_pending_and_lag_without_running_g1(
    tmp_path, monkeypatch,
):
    enqueue_seconds = []
    projection_seconds = []
    original_enqueue = ResearchWorkQueueStore.enqueue
    original_projection_save = ResearchProjectionStore.save

    def timed_enqueue(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return original_enqueue(self, *args, **kwargs)
        finally:
            enqueue_seconds.append(time.perf_counter() - started)

    def timed_projection_save(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return original_projection_save(self, *args, **kwargs)
        finally:
            projection_seconds.append(time.perf_counter() - started)

    monkeypatch.setattr(ResearchWorkQueueStore, "enqueue", timed_enqueue)
    monkeypatch.setattr(ResearchProjectionStore, "save", timed_projection_save)
    fake = MemoryS3(_objects())
    source = S3ResearchDataSource(bucket="wave3-fast-path", client=fake)
    state_root = tmp_path / "continuous"
    question_state = tmp_path / "questions"
    frontier_state = tmp_path / "frontier"
    manifests = tmp_path / "manifests"
    registry_dir = tmp_path / "registry"
    OptimisationRegistry(str(registry_dir)).save()
    runner_map = _runners()
    original_g1 = runner_map["G1"]
    g1_calls = []

    def counted_g1(*args, **kwargs):
        g1_calls.append(time.perf_counter())
        return original_g1(*args, **kwargs)

    runner_map["G1"] = counted_g1

    def bridge(question, **_kwargs):
        return SimpleNamespace(
            bridge_run_id="BR-" + question.cycle_id[-12:],
            status="NO_SCIENTIFIC_STATE_CHANGE", validation_handoff=(),
            question_changes_processed=question.changed_question_ids,
            findings_created=(), findings_weakened=(), hypotheses_created=(),
            hypotheses_invalidated=(), candidates_created=(), review_required=(),
        )

    common = {
        "state_root": state_root,
        "frontier_kwargs": {
            "source": source, "state_directory": frontier_state,
            "manifest_directory": manifests, "as_of_date": date(2026, 10, 3),
        },
        "question_kwargs": {
            "source": source, "manifest_directory": manifests,
            "state_directory": question_state, "runners": runner_map,
            "runner_executor": RecordingExecutor(),
        },
        "bridge_runner": bridge,
        "bridge_kwargs": {
            "scientific_state_directory": tmp_path / "science",
            "optimisation_registry_directory": registry_dir,
        },
        "q71_runner": lambda **_: {
            "status": "COMPLETED", "generated_questions": [], "queue": []},
        "max_validation_jobs": 0,
    }
    first = run_continuous_research_cycle(**common)
    assert first.cycle_outcome == "COMPLETED"
    assert len(g1_calls) == 1

    key = _key("shadow_runtime")
    fake.objects[key] = fake.objects[key].replace("\n", " \n", 1)
    started = time.perf_counter()
    second = run_continuous_research_cycle(**common)
    elapsed = time.perf_counter() - started
    projection = ResearchProjectionStore(
        state_root / "projection").load_latest()
    assert projection is not None
    g1 = next(
        row for row in projection["canonical_questions"]
        if row["question_id"] == "G1")

    assert second.cycle_outcome == "COMPLETED"
    assert len(g1_calls) == 1
    assert g1["work_state"] == "DEEP_PENDING"
    assert g1["execution_freshness"] == "DEEP_STALE"
    assert projection["research_lag"]["pending_deep_jobs"] == 1
    assert projection["research_lag"]["running_deep_jobs"] == 0
    assert projection["research_lag"]["lag_epochs"] == 1
    assert len(projection["investigations_and_work_queues"][
        "deep_research_queue"]) == 1
    # This bounded fixture proves the publication path does not execute G1.
    # The duration is diagnostic only and is not a scientific threshold.
    assert elapsed < 10.0
    fast_path_metrics = {
        "fast_path_seconds": elapsed,
        "deep_enqueue_seconds": enqueue_seconds[-1],
        "projection_publication_seconds": projection_seconds[-1],
        "measured_inline_g1_seconds_avoided": 3025.07,
        "pending_jobs_after_fast_path": 1,
    }

    completion = run_deep_research_job(
        state_root=state_root,
        question_kwargs=common["question_kwargs"],
        bridge_runner=bridge,
        bridge_kwargs=common["bridge_kwargs"],
        execution_policy=ResearchExecutionPolicy(
            deep_work_cooldown_seconds=0),
    )
    completed_projection = ResearchProjectionStore(
        state_root / "projection").load_latest()
    assert completed_projection is not None
    completed_g1 = next(
        row for row in completed_projection["canonical_questions"]
        if row["question_id"] == "G1")
    assert completion["status"] == "COMPLETED"
    assert len(g1_calls) == 2
    assert completed_g1["work_state"] == "FRESH"
    assert completed_g1["last_evaluated_snapshot_id"] == (
        projection["data_frontier"]["snapshot_id"])
    assert completion["projection_version"] != projection["projection_version"]
    assert completed_projection["research_lag"]["pending_deep_jobs"] == 0
    assert completed_projection["research_lag"]["lag_epochs"] == 0
    queue_after = ResearchWorkQueueStore(state_root / "deep_work_queue.json")
    assert queue_after.ordered()[0].state == COMPLETED
    print("WAVE3_PERFORMANCE=" + json.dumps(
        fast_path_metrics, sort_keys=True))


def test_newer_governed_scope_supersedes_pending_job_and_preserves_epochs(tmp_path):
    store = ResearchWorkQueueStore(tmp_path / "deep.json")
    first = _job(
        "G1", "ISNAP-1", "EPOCH-1", "2026-10-07T10:00:00+00:00",
        dependency="one", end="2026-09-25")
    second = _job(
        "G1", "ISNAP-2", "EPOCH-2", "2026-10-07T10:01:00+00:00",
        dependency="two", end="2026-09-26")
    store.enqueue(first, latest_scope_supersedes=True, max_pending_jobs=4)
    store.record_epoch(
        epoch_id="EPOCH-1", snapshot_id="ISNAP-1",
        closed_at=first.queued_at, deep_job_ids=[first.job_id])
    store.enqueue(second, latest_scope_supersedes=True, max_pending_jobs=4)
    store.record_epoch(
        epoch_id="EPOCH-2", snapshot_id="ISNAP-2",
        closed_at=second.queued_at, deep_job_ids=[second.job_id])

    assert store.jobs[first.job_id].state == SUPERSEDED
    assert store.jobs[first.job_id].superseded_by == second.job_id
    assert store.jobs[second.job_id].state == PENDING
    metrics = store.metrics(
        current_epoch_id="EPOCH-2",
        generated_at="2026-10-07T10:02:00+00:00")
    assert metrics["pending_deep_jobs"] == 1
    assert metrics["running_deep_jobs"] == 0
    assert metrics["latest_closed_epoch_id"] == "EPOCH-2"
    assert metrics["latest_deep_processed_epoch_id"] == "EPOCH-1"
    assert metrics["lag_epochs"] == 1
    assert metrics["oldest_pending_seconds"] == 60.0


def test_one_worker_completion_is_identity_checked_and_stale_completion_rejected(tmp_path):
    store = ResearchWorkQueueStore(tmp_path / "deep.json")
    job = _job(
        "G1", "ISNAP-1", "EPOCH-1", "2026-10-07T10:00:00+00:00")
    store.enqueue(job, latest_scope_supersedes=True, max_pending_jobs=4)

    completed = process_one_deep_job(
        store=store,
        policy=ResearchExecutionPolicy(deep_work_cooldown_seconds=0),
        now="2026-10-07T10:01:00+00:00",
        executor=lambda claimed: {
            "result_id": "QRESULT-1",
            "snapshot_id": claimed.triggering_snapshot_id,
            "evaluator_identity_digest": claimed.evaluator_identity_digest,
            "completed_at": "2026-10-07T10:01:30+00:00",
        },
    )
    assert completed is not None
    assert completed.state == COMPLETED
    assert completed.result_id == "QRESULT-1"

    stale = _job(
        "G1", "ISNAP-2", "EPOCH-2", "2026-10-07T10:02:00+00:00",
        dependency="two", end="2026-09-26")
    store.enqueue(stale, latest_scope_supersedes=True, max_pending_jobs=4)
    claimed = store.claim_next(
        now="2026-10-07T10:03:00+00:00",
        policy=ResearchExecutionPolicy(deep_work_cooldown_seconds=0))
    newer = _job(
        "G1", "ISNAP-3", "EPOCH-3", "2026-10-07T10:04:00+00:00",
        dependency="three", end="2026-09-27")
    store.enqueue(newer, latest_scope_supersedes=True, max_pending_jobs=4)
    assert claimed is not None
    assert store.jobs[claimed.job_id].state == SUPERSEDED
    with pytest.raises(ResearchWorkQueueError, match="COMPLETION_NOT_RUNNING"):
        store.complete(
            claimed.job_id, result_id="STALE", result_snapshot_id="ISNAP-2",
            evaluator_identity_digest="evaluator-digest",
            completed_at="2026-10-07T10:05:00+00:00")


def test_interrupted_running_job_recovers_once_and_corruption_fails_closed(tmp_path):
    path = tmp_path / "deep.json"
    store = ResearchWorkQueueStore(path)
    job = _job(
        "G1", "ISNAP-1", "EPOCH-1", "2026-10-07T10:00:00+00:00")
    store.enqueue(job, latest_scope_supersedes=True, max_pending_jobs=4)
    claimed = store.claim_next(
        now="2026-10-07T10:01:00+00:00",
        policy=ResearchExecutionPolicy(deep_work_cooldown_seconds=0))
    assert claimed is not None

    recovered = ResearchWorkQueueStore(path)
    assert recovered.jobs[job.job_id].state == PENDING
    assert recovered.jobs[job.job_id].attempts == 1
    assert recovered.jobs[job.job_id].failure_reason == (
        "RECOVERED_INTERRUPTED_ATTEMPT")

    path.write_text('{"schema":"wrong","jobs":[],"epochs":[]}', encoding="utf-8")
    with pytest.raises(ResearchWorkQueueError, match="SCHEMA_INVALID"):
        ResearchWorkQueueStore(path)


def test_failure_backpressure_and_cooldown_are_bounded_and_visible(tmp_path):
    store = ResearchWorkQueueStore(tmp_path / "deep.json")
    job = _job(
        "G1", "ISNAP-1", "EPOCH-1", "2026-10-07T10:00:00+00:00")
    store.enqueue(job, latest_scope_supersedes=True, max_pending_jobs=1)
    store.record_epoch(
        epoch_id="EPOCH-1", snapshot_id="ISNAP-1",
        closed_at=job.queued_at, deep_job_ids=[job.job_id])
    with pytest.raises(RuntimeError, match="deterministic deep failure"):
        process_one_deep_job(
            store=store,
            policy=ResearchExecutionPolicy(deep_work_cooldown_seconds=0),
            now="2026-10-07T10:01:00+00:00",
            executor=lambda _job: (_ for _ in ()).throw(
                RuntimeError("deterministic deep failure")),
        )
    assert store.jobs[job.job_id].state == FAILED
    assert process_one_deep_job(
        store=store,
        policy=ResearchExecutionPolicy(deep_work_cooldown_seconds=0),
        now="2026-10-07T10:02:00+00:00",
        executor=lambda _job: pytest.fail("failed jobs must not spin"),
    ) is None

    predecessor = {
        "projection_schema": "unified_research_projection_v1",
        "projection_version": "RPROJ-OLD",
        "continuous_cycle_id": "CRCYCLE-OLD",
        "data_frontier": {"investigation_epoch": "EPOCH-1"},
        "canonical_questions": [{
            "question_id": "G1", "deep_work_job_id": job.job_id,
            "work_state": "DEEP_PENDING", "execution_freshness": "DEEP_STALE",
            "result": {"result_id": "QRESULT-OLD", "status": "COMPLETE"},
        }],
        "investigations_and_work_queues": {},
        "research_lag": {},
    }
    refreshed = build_research_work_refresh_projection(
        continuous_cycle_id="CRCYCLE-FAILED",
        predecessor_projection=predecessor,
        research_work_store=store,
        projection_generated_at="2026-10-07T10:02:00+00:00",
    )
    assert refreshed["canonical_questions"][0]["result"] == (
        predecessor["canonical_questions"][0]["result"])
    assert refreshed["canonical_questions"][0]["work_state"] == "DEEP_STALE"
    assert refreshed["research_lag"]["failed_deep_jobs"] == [job.job_id]
    assert refreshed["research_lag"]["lag_epochs"] == 1
    assert refreshed["research_lag"]["lag_seconds"] == 120.0

    pending = _job(
        "G2", "ISNAP-2", "EPOCH-2", "2026-10-07T10:03:00+00:00")
    store.enqueue(pending, latest_scope_supersedes=False, max_pending_jobs=1)
    excess = _job(
        "R3", "ISNAP-2", "EPOCH-2", "2026-10-07T10:03:01+00:00")
    store.enqueue(
        excess, latest_scope_supersedes=False, max_pending_jobs=1)
    pressure = store.metrics(
        current_epoch_id="EPOCH-2",
        generated_at="2026-10-07T10:03:02+00:00")
    assert pressure["pending_deep_jobs"] == 2
    assert pressure["backpressure_active"] is True
    assert store.jobs[excess.job_id].transitions[-1]["reason"] == (
        "BACKPRESSURE_HIGH_WATERMARK")

    claimed = store.claim_next(
        now="2026-10-07T10:04:00+00:00",
        policy=ResearchExecutionPolicy(deep_work_cooldown_seconds=0))
    assert claimed is not None
    assert store.claim_next(
        now="2026-10-07T10:04:01+00:00",
        policy=ResearchExecutionPolicy(deep_work_cooldown_seconds=0)) is None
