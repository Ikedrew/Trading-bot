"""Phase 2B contracts. All acquisition is fake S3; all state is disposable."""
from copy import deepcopy
import json
from pathlib import Path
import time
import tracemalloc

import pytest

from core.production_data_contract import current_schema
from research_engine.data_access.s3_source import S3ResearchDataSource, ResearchDataSourceError
from research_engine.v10.investigation_snapshot import (
    BOUND_DATASETS, CycleSnapshotInputs, SnapshotBoundDatasetReader,
    freeze_investigation_snapshot, freeze_investigation_snapshot_incremental,
)
from research_engine.v10.continuous.question_cycle_state import (
    QuestionCycleStateError, QuestionCycleStore, atomic_json,
)
from research_engine.v10.continuous.cycle_state import process_memory_bytes
from research_engine.v10.continuous.canonical_question_cycle import run_canonical_question_cycle
from tests.test_incremental_snapshot_materialization import MemoryS3, _objects, _key, _body
from tests.test_canonical_question_cycle import (
    MemoryS3 as QuestionS3, _objects as question_objects, _freeze,
    _runners, RecordingExecutor,
)


def source(fake, cache, **kwargs):
    return S3ResearchDataSource(bucket="incremental-test", client=fake,
                               verified_cache_directory=cache, **kwargs)


def frozen(tmp_path, *, versioned=True):
    objects = _objects()
    fake = MemoryS3(objects, {key: "v1" for key in objects} if versioned else {})
    src = source(fake, tmp_path / "bodies")
    snapshot = freeze_investigation_snapshot(start_date="2026-09-25",
        end_date="2026-09-25", source=src)
    return fake, snapshot


def read(fake, snapshot, tmp_path):
    src = source(fake, tmp_path / "bodies")
    inputs = CycleSnapshotInputs("fixture-cycle")
    reader = inputs.acquire(snapshot, cycle_id="fixture-cycle", source=src)
    result = {dataset: reader.read_dataset(dataset) for dataset in BOUND_DATASETS}
    inputs.close()
    return result, src


def test_cold_freeze_seeds_existing_reader_and_warm_inputs_are_equivalent(tmp_path):
    fake, snapshot = frozen(tmp_path)
    assert fake.get_calls == len(fake.objects)
    before = fake.get_calls
    shared, src = read(fake, snapshot, tmp_path)
    assert fake.get_calls == before
    assert src.performance["verified_cache_hits"] == len(fake.objects)
    independent = SnapshotBoundDatasetReader(snapshot,
        S3ResearchDataSource(bucket="incremental-test", client=fake))
    assert shared == {name: independent.read_dataset(name) for name in BOUND_DATASETS}


@pytest.mark.parametrize("change, expected_gets", [("add", 1), ("replace", 1), ("remove", 0)])
def test_incremental_freeze_fetches_delta_only_and_reader_fetches_nothing_again(tmp_path, change, expected_gets):
    fake, snapshot = frozen(tmp_path)
    key = _key("market_context")
    if change == "add":
        key = _key("market_context", part="new.jsonl")
        fake.objects[key] = _body("market_context", "new")
        fake.versions[key] = "v2"
    elif change == "replace":
        fake.objects[key] = _body("market_context", "new")
        fake.versions[key] = "v2"
    else:
        del fake.objects[key]
        del fake.versions[key]
    before = fake.get_calls
    incremental = freeze_investigation_snapshot_incremental(predecessor=snapshot,
        start_date="2026-09-25", end_date="2026-09-25", source=source(fake, tmp_path / "bodies"))
    assert fake.get_calls - before == expected_gets
    before = fake.get_calls
    rows, _ = read(fake, incremental, tmp_path)
    assert fake.get_calls == before
    full = freeze_investigation_snapshot(start_date="2026-09-25", end_date="2026-09-25",
        source=S3ResearchDataSource(bucket="incremental-test", client=fake))
    for actual, expected in zip(incremental.datasets, full.datasets):
        assert actual.objects == expected.objects
        assert actual.content_digest == expected.content_digest
        assert actual.dataset_snapshot_id == expected.dataset_snapshot_id
        assert actual.canonical_row_count == expected.canonical_row_count
        assert actual.source_row_count == expected.source_row_count


@pytest.mark.parametrize("version", [None, "null"])
def test_versionless_objects_are_always_fresh(tmp_path, version):
    fake, snapshot = frozen(tmp_path, versioned=False)
    if version == "null":
        fake.versions = {key: "null" for key in fake.objects}
        snapshot = freeze_investigation_snapshot(start_date="2026-09-25",
            end_date="2026-09-25", source=source(fake, tmp_path / "bodies"))
    before = fake.get_calls
    read(fake, snapshot, tmp_path)
    assert fake.get_calls - before == len(fake.objects)
    assert not list((tmp_path / "bodies").glob("*.cache"))


@pytest.mark.parametrize("corruption", ["truncate", "header", "body"])
def test_corrupt_cache_fails_closed(tmp_path, corruption):
    fake, snapshot = frozen(tmp_path)
    path = next((tmp_path / "bodies").glob("*.cache"))
    data = path.read_bytes()
    if corruption == "truncate":
        data = data[:-7]
    elif corruption == "header":
        data = b"!" + data[1:]
    else:
        offset = data.index(b"\n") + 1
        data = data[:offset] + data[offset:].replace(b'"symbol"', b'"symboL"', 1)
    path.write_bytes(data)
    with pytest.raises(ResearchDataSourceError):
        read(fake, snapshot, tmp_path)


@pytest.mark.parametrize("field,value", [("version_id", "v2"), ("etag", "other"),
    ("content_sha256", "0" * 64), ("row_count", 77), ("identifier", _key("market_context", part="other.jsonl"))])
def test_cache_cannot_satisfy_mismatched_object_authority(tmp_path, field, value):
    fake, snapshot = frozen(tmp_path)
    binding = next(b for b in snapshot.datasets if b.dataset == "market_context")
    item = binding.objects[0].to_dict()
    item[field] = value
    src = source(fake, tmp_path / "bodies")
    with pytest.raises(ResearchDataSourceError):
        src.read_bound_objects("market_context", [item], expected_schema_version=current_schema("market_context"))
    assert src.performance["verified_cache_hits"] == 0


def test_cache_budget_and_interrupted_partial_are_bounded(tmp_path):
    fake, snapshot = frozen(tmp_path)
    cache = tmp_path / "bounded"
    cache.mkdir()
    (cache / "interrupted.partial").write_bytes(b"incomplete")
    src = source(fake, cache, verified_cache_max_bytes=1, verified_cache_object_max_bytes=1)
    SnapshotBoundDatasetReader(snapshot, src)
    assert not list(cache.glob("*.cache"))
    assert (cache / "interrupted.partial").read_bytes() == b"incomplete"
    assert not list(cache.glob("body-*.partial"))


def question_fixture(tmp_path):
    fake = QuestionS3(question_objects())
    snapshot, manifests = _freeze(tmp_path, fake)
    return fake, snapshot, manifests


def evaluate(tmp_path, fake, snapshot, manifests, *, executor=None, progress=None, **kwargs):
    return run_canonical_question_cycle(snapshot.snapshot_id,
        source=S3ResearchDataSource(bucket="question-cycle-test", client=fake,
            verified_cache_directory=tmp_path / "bodies"),
        manifest_directory=manifests, state_directory=tmp_path / "questions",
        runners=_runners(), runner_executor=executor or RecordingExecutor(),
        execution_revision=kwargs.pop("execution_revision", "offline-fixture-v1"),
        progress_callback=progress, **kwargs)


def test_completed_replay_precedes_materialization_and_preserves_ids(tmp_path, monkeypatch):
    fake, snapshot, manifests = question_fixture(tmp_path)
    first = evaluate(tmp_path, fake, snapshot, manifests)
    before = len(fake.get_calls)
    import research_engine.v10.continuous.canonical_question_cycle as module
    monkeypatch.setattr(module, "_build_context", lambda *a, **k: pytest.fail("replay built context"))
    monkeypatch.setattr(module, "SnapshotBoundDatasetReader", lambda *a, **k: pytest.fail("replay loaded bodies"))
    executor = RecordingExecutor()
    replay = evaluate(tmp_path, fake, snapshot, manifests, executor=executor)
    assert replay == first
    assert len(fake.get_calls) == before
    assert not executor.snapshot_ids


@pytest.mark.parametrize("change", ["remove", "replace", "version"])
def test_completed_replay_requires_fresh_membership_and_version(tmp_path, change):
    fake, snapshot, manifests = question_fixture(tmp_path)
    evaluate(tmp_path, fake, snapshot, manifests)
    key = next(iter(fake.objects))
    if change == "remove":
        del fake.objects[key]
    elif change == "replace":
        fake.objects[key] = fake.objects[key] + "\n"
    else:
        original = fake.head_object
        def head(**kwargs):
            result = original(**kwargs)
            result["VersionId"] = "v2"
            return result
        fake.head_object = head
    with pytest.raises(Exception, match="OBJECTS_CHANGED"):
        evaluate(tmp_path, fake, snapshot, manifests)


def test_interruption_recovers_durable_questions_and_matches_cold_results(tmp_path):
    fake, snapshot, manifests = question_fixture(tmp_path)
    original = RecordingExecutor()
    completed = []
    def interrupt(progress):
        if progress.get("phase") == "QUESTION_COMPLETED":
            completed.append(progress["last_question"]["question_id"])
            if len(completed) == 8:
                raise KeyboardInterrupt("offline interruption")
    with pytest.raises(KeyboardInterrupt):
        evaluate(tmp_path / "recovered", fake, snapshot, manifests, executor=original, progress=interrupt)
    assert not (tmp_path / "recovered/questions/current_projection.json").exists()
    resumed = []
    second = RecordingExecutor()
    recovered = evaluate(tmp_path / "recovered", fake, snapshot, manifests, executor=second,
        progress=lambda p: resumed.append(p.get("resumed_question_id")) if p.get("phase") == "QUESTION_RESUMED" else None)
    cold_executor = RecordingExecutor()
    cold = evaluate(tmp_path / "cold", fake, snapshot, manifests, executor=cold_executor)
    assert set(resumed) == set(completed)
    assert len(original.snapshot_ids) + len(second.snapshot_ids) == len(cold_executor.snapshot_ids)
    assert recovered.result_ids == cold.result_ids
    assert recovered.planning == cold.planning
    for name in ("complete_count", "negative_result_count", "evaluated_count", "retained_count"):
        assert getattr(recovered, name) == getattr(cold, name)


def test_truncated_checkpoint_cannot_publish_cycle(tmp_path):
    fake, snapshot, manifests = question_fixture(tmp_path)
    def interrupt(p):
        if p.get("phase") == "QUESTION_COMPLETED":
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        evaluate(tmp_path, fake, snapshot, manifests, progress=interrupt)
    checkpoint = next((tmp_path / "questions/checkpoints").rglob("*.json"))
    checkpoint.write_text('{"payload":', encoding="utf-8")
    with pytest.raises(QuestionCycleStateError, match="RECEIPT_INVALID"):
        evaluate(tmp_path, fake, snapshot, manifests)
    assert not (tmp_path / "questions/current_projection.json").exists()


def test_old_atomic_temporary_does_not_block_recovery(tmp_path):
    path = tmp_path / "state.json"
    path.with_suffix(".json.tmp").write_text("truncated", encoding="utf-8")
    atomic_json(path, {"complete": True})
    assert json.loads(path.read_text()) == {"complete": True}

@pytest.mark.parametrize("change", ["remove", "replace", "version", "denied"])
def test_cached_reader_cannot_replace_fresh_authorized_observation(tmp_path, change):
    fake, snapshot = frozen(tmp_path)
    key = next(iter(fake.objects))
    if change == "remove":
        del fake.objects[key]
    elif change == "replace":
        fake.objects[key] += "\n"
    elif change == "version":
        fake.versions[key] = "v2"
    else:
        def denied(**kwargs):
            raise PermissionError("offline denied")
        fake.head_object = denied
    with pytest.raises(ResearchDataSourceError):
        read(fake, snapshot, tmp_path)


@pytest.mark.parametrize("change", ["revision", "policy", "implementation"])
def test_interrupted_checkpoint_authority_change_requires_fresh_evaluation(tmp_path, monkeypatch, change):
    from research_engine.v10.continuous.research_work_queue import ResearchExecutionPolicy
    import research_engine.v10.continuous.canonical_question_cycle as module
    fake, snapshot, manifests = question_fixture(tmp_path)
    def interrupt(p):
        if p.get("phase") == "QUESTION_COMPLETED":
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        evaluate(tmp_path, fake, snapshot, manifests, progress=interrupt)
    kwargs = {}
    if change == "revision":
        kwargs["execution_revision"] = "offline-fixture-v2"
    elif change == "policy":
        kwargs["execution_policy"] = ResearchExecutionPolicy(deep_runtime_seconds=321)
    else:
        monkeypatch.setattr(module, "_replay_implementation_identity", lambda: "changed-implementation")
    executor = RecordingExecutor()
    events = []
    def second_interrupt(p):
        events.append(p)
        interrupt(p)
    with pytest.raises(KeyboardInterrupt):
        evaluate(tmp_path, fake, snapshot, manifests, executor=executor,
                 progress=second_interrupt, **kwargs)
    assert executor.snapshot_ids
    assert not any(e.get("phase") == "QUESTION_RESUMED" for e in events)
    assert not (tmp_path / "questions/current_projection.json").exists()


@pytest.mark.parametrize("corrupt", ["history", "dependencies"])
def test_checkpoint_requires_immutable_history_and_current_dependencies(tmp_path, corrupt):
    from research_engine.control_plane.stage4_dataset_snapshot import fingerprint
    fake, snapshot, manifests = question_fixture(tmp_path)
    def interrupt(p):
        if p.get("phase") == "QUESTION_COMPLETED":
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        evaluate(tmp_path, fake, snapshot, manifests, progress=interrupt)
    path = next((tmp_path / "questions/checkpoints").rglob("*.json"))
    if corrupt == "history":
        history = next((tmp_path / "questions/question_history").rglob("*.json"))
        history.write_text("truncated", encoding="utf-8")
    else:
        value = json.loads(path.read_text())
        value["payload"]["prerequisites"]["invented"] = {"result_id": "wrong"}
        value["sha256"] = fingerprint(value["payload"])
        path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(QuestionCycleStateError):
        evaluate(tmp_path, fake, snapshot, manifests)
    assert not (tmp_path / "questions/current_projection.json").exists()


def test_completed_policy_change_revalidates_without_changing_scientific_results(tmp_path):
    from research_engine.v10.continuous.research_work_queue import ResearchExecutionPolicy
    fake, snapshot, manifests = question_fixture(tmp_path)
    first = evaluate(tmp_path, fake, snapshot, manifests)
    executor = RecordingExecutor()
    second = evaluate(tmp_path, fake, snapshot, manifests, executor=executor,
        execution_policy=ResearchExecutionPolicy(deep_runtime_seconds=321))
    assert executor.snapshot_ids
    assert second.cycle_id != first.cycle_id
    assert second.result_ids == first.result_ids


def test_completed_implementation_change_requires_governed_revision(tmp_path, monkeypatch):
    import research_engine.v10.continuous.canonical_question_cycle as module
    fake, snapshot, manifests = question_fixture(tmp_path)
    first = evaluate(tmp_path, fake, snapshot, manifests)
    monkeypatch.setattr(module, "_replay_implementation_identity", lambda: "changed-implementation")
    before = len(fake.get_calls)
    with pytest.raises(module.CanonicalQuestionCycleError, match="REQUIRES_GOVERNED_REVISION"):
        evaluate(tmp_path, fake, snapshot, manifests)
    assert len(fake.get_calls) == before
    assert QuestionCycleStore(tmp_path / "questions").load_current()["cycle_id"] == first.cycle_id


def test_corrupt_completed_receipt_is_rejected(tmp_path):
    fake, snapshot, manifests = question_fixture(tmp_path)
    evaluate(tmp_path, fake, snapshot, manifests)
    path = next((tmp_path / "questions/replay").glob("*.json"))
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(QuestionCycleStateError, match="RECEIPT_INVALID"):
        evaluate(tmp_path, fake, snapshot, manifests)


# ─────────────────────────────────────────────────────────────────────────────
# Bounded offline benchmark (Phase 2B qualification).
#
# NOT collected by pytest (python_functions = test_*). Invoke explicitly, one
# scenario per process so the process-wide RSS high-water mark is attributable
# to that scenario alone:
#     python tests/test_incremental_acquisition_replay.py <scenario>
# where <scenario> in {full, unchanged, delta}.
#
# All workloads are synthetic and bounded: an in-memory fake S3 (no remote
# client), one day, one part per dataset, eleven bound datasets. Nothing here
# is production-scale and no production improvement is claimed from it.
# ─────────────────────────────────────────────────────────────────────────────

BENCH_DAY = "2026-09-25"
BENCH_SCENARIOS = ("full", "unchanged", "delta")


def _bench_source(fake, cache):
    return source(fake, cache)


def _bench_freeze(fake, cache):
    return freeze_investigation_snapshot(start_date=BENCH_DAY, end_date=BENCH_DAY,
        source=_bench_source(fake, cache))


def benchmark(scenario):
    """Run one bounded synthetic scenario; return a JSON-serializable report.

    Memory is reported two ways on purpose:
      * tracemalloc_peak_bytes - true per-scenario Python allocation peak.
      * process_peak_rss_bytes - process-wide OS high-water mark. On Windows
        this is monotonically non-decreasing, so it is only a clean per-scenario
        figure because each scenario runs in its own process (see __main__).
    """
    if scenario not in BENCH_SCENARIOS:
        raise SystemExit("usage: test_incremental_acquisition_replay.py "
                         "{full|unchanged|delta}")

    import tempfile

    objects = _objects()
    fake = MemoryS3(objects, {key: "v1" for key in objects})

    with tempfile.TemporaryDirectory(prefix="p2b-bench-") as cache_dir:
        cache = Path(cache_dir) / "bodies"
        predecessor = _bench_freeze(fake, cache)  # setup, not measured work

        if scenario == "full":
            work_fake = MemoryS3(dict(objects), {key: "v1" for key in objects})
            tracemalloc.start()
            started = time.perf_counter()
            snapshot = _bench_freeze(work_fake, cache)
            elapsed = time.perf_counter() - started
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            get_calls = work_fake.get_calls
            head_calls = work_fake.head_calls
            reused_datasets = 0
            reread_objects = get_calls

        elif scenario == "unchanged":
            work_fake = MemoryS3(dict(objects), {key: "v1" for key in objects})
            before_gets, before_heads = work_fake.get_calls, work_fake.head_calls
            tracemalloc.start()
            started = time.perf_counter()
            snapshot = freeze_investigation_snapshot_incremental(
                predecessor=predecessor, start_date=BENCH_DAY, end_date=BENCH_DAY,
                source=_bench_source(work_fake, cache))
            elapsed = time.perf_counter() - started
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            get_calls = work_fake.get_calls - before_gets
            head_calls = work_fake.head_calls - before_heads
            reused_datasets = len(BOUND_DATASETS)
            reread_objects = get_calls
            assert snapshot.snapshot_fingerprint == predecessor.snapshot_fingerprint

        else:  # delta - replace one object in one dataset.
            work_fake = MemoryS3(dict(objects), {key: "v1" for key in objects})
            key = _key("market_context", part="new.jsonl")
            work_fake.objects[key] = _body("market_context", "new")
            work_fake.versions[key] = "v2"
            before_gets, before_heads = work_fake.get_calls, work_fake.head_calls
            tracemalloc.start()
            started = time.perf_counter()
            snapshot = freeze_investigation_snapshot_incremental(
                predecessor=predecessor, start_date=BENCH_DAY, end_date=BENCH_DAY,
                source=_bench_source(work_fake, cache))
            elapsed = time.perf_counter() - started
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            get_calls = work_fake.get_calls - before_gets
            head_calls = work_fake.head_calls - before_heads
            reused_datasets = len(BOUND_DATASETS) - 1
            reread_objects = get_calls
            full = freeze_investigation_snapshot(start_date=BENCH_DAY,
                end_date=BENCH_DAY,
                source=S3ResearchDataSource(bucket="incremental-test", client=work_fake))
            for actual, expected in zip(snapshot.datasets, full.datasets):
                assert actual.objects == expected.objects
                assert actual.content_digest == expected.content_digest

        rss, peak_rss = process_memory_bytes()
        return {
            "scenario": scenario,
            "synthetic_bounded": True,
            "datasets": len(BOUND_DATASETS),
            "canonical_rows": sum(b.canonical_row_count for b in snapshot.datasets),
            "seconds": elapsed,
            "get_calls": get_calls,
            "head_calls": head_calls,
            "reused_datasets": reused_datasets,
            "reread_objects": reread_objects,
            "tracemalloc_peak_bytes": peak,
            "rss_bytes": rss,
            "process_peak_rss_bytes": peak_rss,
            "snapshot_id": snapshot.snapshot_id,
        }


if __name__ == "__main__":
    import sys
    print(json.dumps(benchmark(sys.argv[1] if len(sys.argv) > 1 else "full")))
