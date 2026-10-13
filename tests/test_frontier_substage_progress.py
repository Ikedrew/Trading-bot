"""Focused acceptance for FRONTIER substage progress (Priority B.2 / C).

Isolated, immutable fixtures and a recording in-memory S3 double only: no
network, no production data, no coordinator entry point, no real research
state. Verifies that a long FRONTIER run reports ordered, meaningful substage
progress; that a failing progress callback can never change the governed
result; that a failed frontier never reports completion; that the throttled
emitter only ever updates the RUNNING progress document; and that an
interrupted continuous cycle persists substage progress without ever writing a
false completed-cycle record.
"""
from __future__ import annotations

from datetime import date
import json

from core.production_data_contract import current_schema
from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.v10.continuous.frontier_coordinator import (
    FRONTIER_INVALID, SNAPSHOT_READY, run_frontier_snapshot_cycle,
)
from research_engine.v10.continuous.research_loop import (
    FRONTIER_PROGRESS_MIN_INTERVAL_SECONDS, _frontier_substage_emitter,
    run_continuous_research_cycle,
)
from research_engine.v10.investigation_snapshot import BOUND_DATASETS
from tests.test_frontier_coordinator import MemoryS3, _key, _objects, _source


def _run(tmp_path, fake, **kwargs):
    return run_frontier_snapshot_cycle(
        source=_source(fake),
        state_directory=tmp_path / "state",
        manifest_directory=tmp_path / "manifests",
        as_of_date=date(2026, 10, 3),
        **kwargs,
    )


def test_frontier_reports_ordered_meaningful_substage_progress(tmp_path):
    fake = MemoryS3(_objects(), {key: "v1" for key in _objects()})
    events: list[dict] = []
    result = _run(tmp_path, fake, progress_callback=events.append)

    assert result.status == SNAPSHOT_READY
    phases = [event["phase"] for event in events]

    # Ordered envelope: discovery opens, a successful run closes at completion.
    assert phases[0] == "FRONTIER_DISCOVERY_STARTED"
    assert phases[-1] == "FRONTIER_COMPLETED"

    # Exactly one verified-dataset substage per bound dataset, in bound order.
    dataset_events = [e for e in events if e["phase"] == "FRONTIER_DATASET_VERIFIED"]
    assert [e["dataset"] for e in dataset_events] == list(BOUND_DATASETS)
    assert [e["dataset_index"] for e in dataset_events] == list(
        range(1, len(BOUND_DATASETS) + 1))
    assert all(e["dataset_total"] == len(BOUND_DATASETS) for e in dataset_events)

    # Meaningful high-level substages present and correctly ordered.
    for phase in ("FRONTIER_DISCOVERY_COMPLETED", "FRONTIER_SELECTION_READY",
                  "FRONTIER_SNAPSHOT_MATERIALIZED", "FRONTIER_VERIFIED",
                  "FRONTIER_COMPLETED"):
        assert phase in phases
    assert phases.index("FRONTIER_DISCOVERY_COMPLETED") < phases.index(
        "FRONTIER_SELECTION_READY")
    assert phases.index("FRONTIER_SNAPSHOT_MATERIALIZED") < phases.index(
        "FRONTIER_VERIFIED")
    assert phases.index("FRONTIER_VERIFIED") < phases.index("FRONTIER_COMPLETED")
    assert events[-1]["snapshot_id"] == result.snapshot_id


def test_large_dataset_reports_intra_dataset_counts(tmp_path):
    objects = _objects()
    for index in range(55):
        objects[_key("decision_trace", part=f"part-{index:03d}.jsonl")] = objects[
            _key("decision_trace")]
    fake = MemoryS3(objects, {key: "v1" for key in objects})
    events: list[dict] = []
    result = _run(tmp_path, fake, progress_callback=events.append)
    assert result.status == SNAPSHOT_READY
    reads = [event for event in events if event["phase"] == "FRONTIER_DATASET_READING"
             and event["dataset"] == "decision_trace"]
    assert len(reads) >= 3
    assert reads[0]["objects_processed"] == 1
    assert reads[-1]["objects_processed"] == 55
    assert [event["objects_processed"] for event in reads] == sorted(
        event["objects_processed"] for event in reads)
    assert reads[-1]["source_io_cumulative"]["get_calls"] >= 56
    assert "head_calls" in reads[-1]["source_io_cumulative"]
    assert "verified_cache_hits" in reads[-1]["source_io_cumulative"]


def test_mid_dataset_failure_leaves_no_success_and_can_retry(tmp_path):
    objects = _objects()
    objects[_key("decision_trace", part="part-001.jsonl")] = objects[
        _key("decision_trace")]
    fake = MemoryS3(objects, {key: "v1" for key in objects})
    original_get = fake.get_object
    decision_gets = 0

    def interrupted_get(**kwargs):
        nonlocal decision_gets
        if "decision_trace" in str(kwargs["Key"]):
            decision_gets += 1
            if decision_gets == 2:
                raise RuntimeError("simulated interruption")
        return original_get(**kwargs)

    fake.get_object = interrupted_get
    events: list[dict] = []
    failed = _run(tmp_path, fake, progress_callback=events.append)
    assert failed.status == FRONTIER_INVALID
    assert any(event["phase"] == "FRONTIER_DATASET_READING"
               for event in events)
    assert not (tmp_path / "state" / "latest_success.json").exists()

    fake.get_object = original_get
    retried = _run(tmp_path, fake)
    assert retried.status == SNAPSHOT_READY
    assert (tmp_path / "state" / "latest_success.json").exists()


def test_progress_callback_failure_never_changes_governed_result(tmp_path):
    fake = MemoryS3(_objects(), {key: "v1" for key in _objects()})

    def boom(event):
        raise RuntimeError("diagnostic callback failure")

    result = _run(tmp_path, fake, progress_callback=boom)
    assert result.status == SNAPSHOT_READY
    assert (tmp_path / "state" / "latest_success.json").exists()


def test_failed_frontier_reports_failure_and_never_completion(tmp_path):
    fake = MemoryS3(_objects(), {key: "v1" for key in _objects()})
    # A canonical decision_trace record with missing required fields fails closed.
    fake.objects[_key("decision_trace")] = json.dumps({
        "schema_version": current_schema("decision_trace"),
        "timestamp_utc": "2026-09-25T12:00:00Z",
    }) + "\n"
    events: list[dict] = []
    result = _run(tmp_path, fake, progress_callback=events.append)

    assert result.status == FRONTIER_INVALID
    phases = [event["phase"] for event in events]
    assert "FRONTIER_FAILED" in phases
    assert "FRONTIER_COMPLETED" not in phases
    assert "FRONTIER_VERIFIED" not in phases


def test_throttled_emitter_bounds_repeat_writes_but_keeps_transitions():
    calls: list[tuple] = []

    def progress(method, *args):
        calls.append((method, args))

    emit, state = _frontier_substage_emitter(
        progress, min_interval_seconds=1000.0)
    emit({"phase": "FRONTIER_DISCOVERY_STARTED"})
    emit({"phase": "FRONTIER_DATASET_VERIFIED", "dataset": "a"})
    emit({"phase": "FRONTIER_DATASET_VERIFIED", "dataset": "b"})
    emit({"phase": "FRONTIER_COMPLETED"})

    # Repeated same-phase events inside the window are dropped; transitions kept.
    assert state["events"] == 4
    assert state["persisted"] == 3
    assert [c[0] for c in calls] == ["update_stage"] * 3
    assert [c[1][0] for c in calls] == ["FRONTIER"] * 3
    assert [c[1][1]["frontier_substage"]["phase"] for c in calls] == [
        "FRONTIER_DISCOVERY_STARTED", "FRONTIER_DATASET_VERIFIED",
        "FRONTIER_COMPLETED"]
    # It only ever updates the RUNNING document; it never starts/finishes a cycle.
    assert {c[0] for c in calls} == {"update_stage"}
    assert FRONTIER_PROGRESS_MIN_INTERVAL_SECONDS > 0


def test_throttled_emitter_persists_repeats_after_interval():
    calls: list[tuple] = []

    def progress(method, *args):
        calls.append((method, args))

    emit, state = _frontier_substage_emitter(progress, min_interval_seconds=0.0)
    for _ in range(3):
        emit({"phase": "FRONTIER_DATASET_VERIFIED"})
    assert state["events"] == 3
    assert state["persisted"] == 3
    assert len(calls) == 3


def test_continuous_loop_persists_frontier_substage_without_false_completion(tmp_path):
    # No objects at all -> required datasets absent -> the real frontier runner
    # fails closed. The loop must persist substage progress and never record a
    # completed cycle.
    fake = MemoryS3({})
    source = S3ResearchDataSource(bucket="frontier-test", client=fake)
    state_root = tmp_path / "continuous"

    def unexpected_question_call(*args, **kwargs):
        raise AssertionError("questions must not run after a failed frontier")

    result = run_continuous_research_cycle(
        state_root=state_root,
        frontier_kwargs={
            "source": source,
            "state_directory": tmp_path / "frontier_state",
            "manifest_directory": tmp_path / "manifests",
            "as_of_date": date(2026, 10, 3),
        },
        question_runner=unexpected_question_call,
    )

    assert result.cycle_outcome == "FAILED"
    assert result.failure_stage == "FRONTIER"

    progress = json.loads(
        (state_root / "cycle_progress.json").read_text(encoding="utf-8"))
    frontier_details = progress["stages"]["FRONTIER"]["details"]
    # Substage progress was persisted during the run.
    assert frontier_details["frontier_substage_events"] > 0
    assert frontier_details["frontier_substage_persisted"] > 0
    assert frontier_details["frontier_substage"]["phase"].startswith("FRONTIER_")
    # The RUNNING document is never presented as a completed cycle.
    assert progress["status"] == "FAILED"
    assert not (state_root / "cycles" / "latest_completed.json").exists()
    assert not (state_root / "cycles" / "latest_success.json").exists()
