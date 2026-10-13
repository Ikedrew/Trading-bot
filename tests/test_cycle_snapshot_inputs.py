"""Offline contracts for lease-scoped immutable snapshot reuse."""
from copy import deepcopy
from dataclasses import replace
import json
import time
import tracemalloc
import pytest
from tests.test_investigation_snapshot import _source, _freeze, _objects
from research_engine.v10.investigation_snapshot import (
    CycleSnapshotInputs, SnapshotBoundDatasetReader, InvestigationSnapshotError,
    save_investigation_snapshot,
)
from research_engine.v10.continuous.q71_worker import _load_snapshot_context


def test_shared_matches_independent_and_blocks_nested_mutation():
    fake, source = _source()
    snapshot = _freeze(source)
    independent = SnapshotBoundDatasetReader(snapshot, source)
    pool = CycleSnapshotInputs("cycle-a")
    fake.get_calls.clear()
    first = pool.acquire(snapshot, cycle_id="cycle-a", source=source)
    gets = len(fake.get_calls)
    for binding in snapshot.datasets:
        assert first.read_dataset(binding.dataset) == independent.read_dataset(binding.dataset)
    rows = first.cycle_cached_dataset("trade_truth")
    with pytest.raises(InvestigationSnapshotError, match="MUTATION"):
        rows[0]["identity"]["trade_id"] = "tampered"
    with pytest.raises(InvestigationSnapshotError, match="MUTATION"):
        rows.clear()
    copied = first.read_dataset("trade_truth")
    copied[0]["identity"]["trade_id"] = "safe-private-copy"
    assert pool.acquire(snapshot, cycle_id="cycle-a", source=source) is first
    assert first.read_dataset("trade_truth") == independent.read_dataset("trade_truth")
    assert len(fake.get_calls) == gets
    assert pool.diagnostics()["reader_reuses"] == 1


@pytest.mark.parametrize("field,value", [
    ("snapshot_fingerprint", "0" * 64), ("snapshot_id", "ISNAP-INVALID"),
])
def test_corrupt_manifest_rejected(field, value):
    _, source = _source()
    snapshot = _freeze(source)
    pool = CycleSnapshotInputs("a")
    pool.acquire(snapshot, cycle_id="a", source=source)
    with pytest.raises(InvestigationSnapshotError):
        pool.acquire(replace(snapshot, **{field: value}), cycle_id="a", source=source)


@pytest.mark.parametrize("field,value", [
    ("version_id", "changed"), ("content_sha256", "0" * 64),
    ("row_count", 999), ("identifier", "wrong-key"),
])
def test_bound_membership_corruption_rejected(field, value):
    _, source = _source()
    snapshot = _freeze(source)
    pool = CycleSnapshotInputs("a")
    pool.acquire(snapshot, cycle_id="a", source=source)
    binding = snapshot.datasets[0]
    broken = replace(binding.objects[0], **{field: value})
    with pytest.raises(InvestigationSnapshotError):
        changed = replace(binding, objects=(broken, *binding.objects[1:]))
        pool.acquire(replace(snapshot, datasets=(changed, *snapshot.datasets[1:])), cycle_id="a", source=source)


def test_cycle_source_schema_and_closed_scope_rejected(monkeypatch):
    import research_engine.v10.investigation_snapshot as module
    _, source = _source()
    snapshot = _freeze(source)
    pool = CycleSnapshotInputs("a")
    pool.acquire(snapshot, cycle_id="a", source=source)
    with pytest.raises(InvestigationSnapshotError, match="CYCLE"):
        pool.acquire(snapshot, cycle_id="b", source=source)
    _, other = _source()
    with pytest.raises(InvestigationSnapshotError, match="SOURCE"):
        pool.acquire(snapshot, cycle_id="a", source=other)
    monkeypatch.setattr(module, "current_schema", lambda name: "changed")
    with pytest.raises(InvestigationSnapshotError, match="SCHEMA"):
        pool.acquire(snapshot, cycle_id="a", source=source)
    pool.close()
    with pytest.raises(InvestigationSnapshotError, match="CYCLE"):
        pool.acquire(snapshot, cycle_id="a", source=source)


def test_q71_context_reuses_admitted_evidence(tmp_path):
    fake, source = _source()
    snapshot = _freeze(source)
    save_investigation_snapshot(snapshot, tmp_path / (snapshot.snapshot_id + ".json"))
    pool = CycleSnapshotInputs("a")
    reader = pool.acquire(snapshot, cycle_id="a", source=source)
    fake.get_calls.clear()
    for _ in range(3):
        context = _load_snapshot_context(snapshot_id=snapshot.snapshot_id,
            manifest_directory=tmp_path, source=source, required_datasets=("trade_truth",),
            shared_inputs=pool, input_cycle_id="a")
        assert not context.failure
        assert context.reader is reader
        assert context.datasets["trade_truth"] == reader.read_dataset("trade_truth")
    assert fake.get_calls == []


def benchmark(shared, latency=0.0):
    # Bounded duplicate-preserving JSONL fixture; no remote client.
    objects = {key: body * 40 for key, body in _objects().items()}
    fake, source = _source(objects)
    snapshot = _freeze(source)
    fake.get_calls.clear()
    pool = CycleSnapshotInputs("benchmark")
    original_get = fake.get_object
    def delayed_get(**kwargs):
        time.sleep(latency) if latency else None
        return original_get(**kwargs)
    fake.get_object = delayed_get
    tracemalloc.start()
    started = time.perf_counter()
    durations = []
    for _ in range(4):
        consumer_started = time.perf_counter()
        reader = (pool.acquire(snapshot, cycle_id="benchmark", source=source)
                  if shared else SnapshotBoundDatasetReader(snapshot, source))
        assert reader.reads_by_dataset
        del reader
        durations.append(time.perf_counter() - consumer_started)
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    from research_engine.v10.continuous.cycle_state import process_memory_bytes
    rss, peak_rss = process_memory_bytes()
    return {"shared": shared, "seconds": elapsed, "gets": len(fake.get_calls),
            "fake_get_latency_seconds": latency, "cold_seconds": durations[0],
            "subsequent_consumers_seconds": sum(durations[1:]),
            "tracemalloc_peak_bytes": peak, "rss_bytes": rss, "process_peak_rss_bytes": peak_rss,
            "records": sum(binding.canonical_row_count for binding in snapshot.datasets)}


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    print(json.dumps(benchmark(sys.argv[1] == "shared")))


def test_canonical_equivalence(tmp_path):
    from tests.test_canonical_question_cycle import MemoryS3, _objects as objects, _freeze as freeze, _run, _projection
    fake = MemoryS3(objects())
    snapshot, manifests = freeze(tmp_path, fake)
    _run(tmp_path / "independent", fake, snapshot, manifests)
    pool = CycleSnapshotInputs("a")
    _run(tmp_path / "shared", fake, snapshot, manifests,
         shared_inputs=pool, input_cycle_id="a")
    before = _projection(tmp_path / "independent")["questions"]
    after = _projection(tmp_path / "shared")["questions"]
    assert {k: v["result"] for k, v in before.items()} == {k: v["result"] for k, v in after.items()}


def test_missing_object_fails_and_optional_absence_matches():
    from research_engine.v10.investigation_snapshot import REQUIRED_DATASETS
    fake, source = _source()
    snapshot = _freeze(source)
    first = snapshot.datasets[0].objects[0].identifier
    del fake.objects[first]
    with pytest.raises(Exception):
        CycleSnapshotInputs("a").acquire(snapshot, cycle_id="a", source=source)
    objects = {k: v for k, v in _objects().items()
               if any(k in [item.identifier for item in b.objects]
                      for b in snapshot.datasets if b.dataset in REQUIRED_DATASETS)}
    _, source = _source(objects)
    snapshot = _freeze(source)
    shared = CycleSnapshotInputs("b").acquire(snapshot, cycle_id="b", source=source)
    independent = SnapshotBoundDatasetReader(snapshot, source)
    assert shared.reads_by_dataset == independent.reads_by_dataset
    absent = next(b.dataset for b in snapshot.datasets if b.presence == "ABSENT")
    with pytest.raises(InvestigationSnapshotError, match="MUTATION"):
        shared.cycle_cached_dataset(absent).append({"fabricated": True})


def test_duplicate_guard_rows_conserved_in_shared_path():
    from tests.test_investigation_snapshot import _historical_guard, _key, _jsonl
    objects = _objects()
    objects[_key("decision_trace", part="guard.jsonl")] = _jsonl(_historical_guard(), _historical_guard())
    _, source = _source(objects)
    snapshot = _freeze(source)
    pool = CycleSnapshotInputs("a")
    shared = pool.acquire(snapshot, cycle_id="a", source=source)
    binding = next(b for b in snapshot.datasets if b.dataset == "decision_trace")
    assert binding.historical_guard_rows == 2
    assert len(shared.read_dataset("decision_trace")) == binding.canonical_row_count
    assert shared.read_dataset("decision_trace") == SnapshotBoundDatasetReader(snapshot, source).read_dataset("decision_trace")


def test_io_diagnostics_match_fake_s3():
    fake, source = _source()
    snapshot = _freeze(source)
    source.performance["get_calls"] = 0
    source.performance["body_bytes"] = 0
    fake.get_calls.clear()
    pool = CycleSnapshotInputs("a")
    pool.acquire(snapshot, cycle_id="a", source=source)
    metrics = pool.diagnostics()["source_io_cumulative"]
    assert metrics["get_calls"] == len(fake.get_calls)
    assert metrics["body_bytes"] == sum(o.byte_size for b in snapshot.datasets for o in b.objects)
    assert metrics["decode_seconds"] >= 0
    assert metrics["hash_seconds"] >= 0


# Exercise the real producer/worker seams, not just the context loader.
from tests.test_research_loop_counterfactual_handoff import wire1


def test_producers_worker_get_once(wire1, monkeypatch):
    from collections import Counter
    from tests.test_research_loop_counterfactual_handoff import _run, _spy_worker
    captured = _spy_worker(monkeypatch)
    wire1.client.get_calls.clear()
    result = _run(wire1)
    assert result.cycle_outcome == "COMPLETED", result.failure_reason
    bound = {o.identifier for b in wire1.snapshot.datasets for o in b.objects}
    counts = Counter(wire1.client.get_calls)
    assert all(counts[key] == 1 for key in bound)
    pool = captured["kwargs"]["shared_inputs"]
    assert pool.constructions == 1
    assert pool.reuses >= 2
    with pytest.raises(InvestigationSnapshotError, match="CYCLE"):
        pool.acquire(wire1.snapshot, cycle_id=pool.cycle_id, source=wire1.source)


def test_new_cycle_requires_new_body_verification():
    fake, source = _source()
    snapshot = _freeze(source)
    first = CycleSnapshotInputs("a")
    fake.get_calls.clear()
    first.acquire(snapshot, cycle_id="a", source=source)
    first_gets = len(fake.get_calls)
    first.close()
    second = CycleSnapshotInputs("b")
    second.acquire(snapshot, cycle_id="b", source=source)
    assert len(fake.get_calls) == 2 * first_gets


def test_changed_authority_rejects_reuse(monkeypatch):
    import research_engine.v10.investigation_snapshot as module
    _, source = _source()
    snapshot = _freeze(source)
    pool = CycleSnapshotInputs("a")
    pool.acquire(snapshot, cycle_id="a", source=source)
    monkeypatch.setattr(module, "_authority_material", lambda source: {"changed": True})
    with pytest.raises(InvestigationSnapshotError, match="SOURCE_AUTHORITY_CHANGED"):
        pool.acquire(snapshot, cycle_id="a", source=source)


def test_scoped_normalization_and_provenance_timings():
    from research_engine.control_plane.evidence_resolver import measure_normalization, normalise_evidence_record
    from research_engine.control_plane.evidence_provenance import measure_provenance, select_current_evidence
    row = {"schema_version": "execution_results_v1", "correlation_id": "C", "entity_id": "E"}
    expected = normalise_evidence_record(row, "execution_results_v1")
    with measure_normalization() as timing:
        assert normalise_evidence_record(row, "execution_results_v1") == expected
    assert timing["normalization_seconds"] > 0
    with measure_provenance() as timing:
        measured = select_current_evidence("execution_results_v1", [row])
    assert timing["provenance_seconds"] > 0
    assert measured == select_current_evidence("execution_results_v1", [row])


def test_real_e3_accepts_readonly_evidence():
    from tests.test_repair_2b1_strategy_expectancy import _sufficient_population
    from research_engine.experiments.strategy_expectancy import run_e3
    from research_engine.v10.investigation_snapshot import _freeze_rows
    rows = _sufficient_population()
    independent = run_e3(rows)
    shared = run_e3(_freeze_rows(rows))
    independent.pop("generated", None)
    shared.pop("generated", None)
    assert shared == independent
    assert shared["status"] == "COMPLETE"


def test_failed_runner_preserves_provenance_timing(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from research_engine.control_plane.evidence_provenance import select_current_evidence
    import research_engine.v10.continuous.canonical_question_cycle as module
    monkeypatch.setattr(module, "_runner_kwargs", lambda *args: {})
    monkeypatch.setattr(module, "_snapshot_only_runner_environment", lambda reader: nullcontext())
    context = SimpleNamespace(reader=None, runner_artifacts={})
    def failing():
        select_current_evidence("execution_results_v1", [])
        raise RuntimeError("fixture")
    with pytest.raises(RuntimeError, match="fixture"):
        module._execute_runner(failing, None, [], context)
    assert context.runner_artifacts["performance_provenance"]["provenance_seconds"] > 0
