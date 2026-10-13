"""Bounded, offline connected-stage benchmark; no coordinator entry point.

Only E3 science is exercised. Other canonical entries are governed unsupported
states, never fake COMPLETE reports. Results and generated state stay in a new
retained temporary directory. No Python execution is needed to install this file.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import sys
import tempfile
import time
import tracemalloc
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


@contextmanager
def offline_guard():
    """Reject IP sockets and accidental default-source construction."""
    def denied(*args, **kwargs):
        raise RuntimeError("OFFLINE_ONLY: network/default data source forbidden")
    with patch.object(socket.socket, "connect", denied), patch.object(socket.socket, "connect_ex", denied), patch.object(socket.socket, "sendto", denied), patch.object(socket, "create_connection", denied):
        yield


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=180,
                        help="completed synthetic shadow trades, 6..600 (default 180)")
    parser.add_argument("--output-root", type=Path,
                        help="parent for a NEW temporary run directory; default OS temp")
    parser.add_argument("--scenario", choices=("all", "COLD", "UNCHANGED", "INCREMENTAL"),
                        default="all", help="later scenarios include prerequisite cycles, separately reported")
    args = parser.parse_args()
    if not 6 <= args.rows <= 600:
        parser.error("--rows must be between 6 and 600")
    if args.output_root is not None and not args.output_root.is_dir():
        parser.error("--output-root must be an existing directory")
    return args


def bounded_registry():
    """Declare actual harness runner availability without inventing reports.

    All IDs and scientific definitions remain present. Only E3 has a runnable
    implementation in this bounded harness; normal UNRUNNABLE handling supplies
    honest unsupported states for other non-alias questions. This registry has
    its own governed fingerprint and is never represented as the full registry.
    """
    from dataclasses import replace
    from research_engine.registry.research_question_registry import REGISTRY
    return tuple(question if question.id == "E3" else replace(
        question, runner_module=None, runner_function=None) for question in REGISTRY)

def run(args, directory):
    sys.path.insert(0, str(ROOT))
    # This fixture imports snapshot/frontier code, not research_loop/coordinator.
    from tests.test_incremental_snapshot_materialization import MemoryS3, _objects, _key
    from core.production_data_contract import current_schema
    from research_engine.data_access import s3_source
    from research_engine.v10.investigation_snapshot import (
        BOUND_DATASETS, CycleSnapshotInputs, freeze_investigation_snapshot,
        freeze_investigation_snapshot_incremental, save_investigation_snapshot)
    from research_engine.v10.continuous import canonical_question_cycle as canonical
    from research_engine.v10.continuous.question_cycle_state import QuestionCycleStore
    from research_engine.v10.continuous.scientific_state_bridge import run_scientific_state_bridge
    from research_engine.v10.continuous.scientific_state_store import ScientificStateStore
    from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry
    from research_engine.v10.continuous.validation_queue import ValidationQueueStore
    from research_engine.v10.continuous.research_projection import (
        ResearchProjectionStore, ResearchProjectionError, build_unified_research_projection)
    from research_engine.v10.continuous.research_lab import clear_lab_view_cache, load_lab_view
    from research_engine.experiments.strategy_expectancy import ACTIVE_FAMILIES, run_e3
    from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
    from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
    from research_engine.control_plane.evidence_provenance import evidence_digest

    def deny_source(*a, **kw):
        raise RuntimeError("OFFLINE_ONLY: explicit fake client required")

    def events(index):
        family = ACTIVE_FAMILIES[index % len(ACTIVE_FAMILIES)]
        common = {"schema_version": current_schema("shadow_runtime"),
                  "canonical_opportunity_id": f"nopp_{index}",
                  "observation_id": f"nobs_{index}",
                  "shadow_trade_id": f"nshadow_{index + 1:016x}",
                  "symbol": "EURUSD", "horizon": "SCALP", "plan_id": f"nplan_{index}"}
        opened = {**common, "event_type": "OPEN",
                  "identity": {"entity_id": f"EURUSD_{index}", "cycle_id": index,
                               "trade_horizon": "SCALP", "evaluated_horizon": "SCALP",
                               "shadow_type": "PRIMARY_HORIZON_SIMULATION"},
                  "live_facts": {"pattern": "HAMMER", "strategy": family,
                                 "score": 0.8, "regime": "TRENDING", "h4_regime": "TRENDING",
                                 "market_phase": "IMPULSE", "h1_bias": "BULLISH"},
                  "construction": {"direction": "BUY", "entry_price": 1.10,
                                   "stop_loss": 1.09, "take_profit": 1.12,
                                   "risk_distance": 0.01, "intended_rr": 2.0},
                  "entry_market_time_utc_epoch_s": 1790334000,
                  "entry_market_time_utc_iso8601": "2026-09-25T11:00:00Z"}
        closed = {**common, "event_type": "CLOSE", "exit_price": 1.12,
                  "exit_reason": "take_profit", "bars_held": 5,
                  "exit_market_time_utc_epoch_s": 1790335500,
                  "exit_market_time_utc_iso8601": "2026-09-25T11:25:00Z",
                  "outcome": {"pnl_r_multiple": 2.0 if index % 5 else -0.5,
                              "mfe_r": 2.1, "mae_r": -0.2,
                              "risk_distance": 0.01, "intended_rr": 2.0}}
        return [opened, closed]

    def jsonl(rows):
        return "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)

    class CountedS3(MemoryS3):
        def __init__(self, objects):
            super().__init__(objects, {key: "v1" for key in objects})
            self.get_keys = []
            self.rows_downloaded = 0
        def get_object(self, **kwargs):
            self.get_keys.append(kwargs["Key"])
            self.rows_downloaded += len(self.objects[kwargs["Key"]].splitlines())
            return super().get_object(**kwargs)

    harness_registry = bounded_registry()
    objects = _objects()
    shadow_rows = [event for i in range(args.rows) for event in events(i)]
    # Include exact duplicate evidence events; production reconstruction decides
    # canonical membership. Raw SHA and multiplicity are still verified.
    shadow_rows += events(0)
    objects[_key("shadow_runtime")] = jsonl(shadow_rows)
    fake = CountedS3(objects)
    source = s3_source.S3ResearchDataSource(bucket="offline-pipeline-benchmark", client=fake,
                                         verified_cache_directory=directory / "bodies")
    manifests = directory / "manifests"
    questions = QuestionCycleStore(directory / "questions")
    scientific = ScientificStateStore(directory / "scientific")
    registry = OptimisationRegistry(str(directory / "optimisation"))
    validation = ValidationQueueStore(directory / "validation.json")
    publication = ResearchProjectionStore(directory / "projection")
    clear_lab_view_cache()
    output = {"format": "offline_pipeline_benchmark_v1", "interpreter": sys.version,
              "output_directory": str(directory), "requested_scenario": args.scenario,
              "requested_shadow_trades": args.rows,
              "scope": "Connected public stage APIs; explicit E3-only runner-availability registry; coordinator NOT EXERCISED",
              "registry_scope": "All 70 IDs; non-E3 runners declared unavailable, producing real UNIMPLEMENTED/alias states",
              "not_exercised": ["69 canonical scientific evaluators other than E3",
                                "production coordinator orchestration/lease",
                                "Q71 generation and deep workers", "validation/shadow executors",
                                "real S3 latency", "process peak RSS"],
              "measurement_notes": ["One bounded pass, no baseline or speedup claim",
                  "tracemalloc enabled during scenarios; timings include its overhead",
                  "freeze includes acquisition; body-load time is inclusive and overlaps freeze",
                  "executed_questions means actual real evaluator calls, not evaluated_count",
                  "reused rows means raw rows in objects reused by incremental freeze",
                  "full-cycle replay count and retained-result count are reported separately"],
              "scenarios": [], "status": "RUNNING"}
    (directory / "results.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    predecessor = previous_cycle = previous_projection = None
    limit = {"COLD": 1, "UNCHANGED": 2, "INCREMENTAL": 3, "all": 3}[args.scenario]
    try:
        with patch.object(s3_source, "get_default_source", deny_source):
            for name in ("COLD", "UNCHANGED", "INCREMENTAL")[:limit]:
                record = {"scenario": name, "seconds": {}, "status": "RUNNING"}
                output["scenarios"].append(record)
                if name == "INCREMENTAL":
                    key = _key("shadow_runtime", part="delta.jsonl")
                    fake.objects[key] = jsonl(events(args.rows))
                    fake.versions[key] = "v2"
                get0, head0, rows0 = fake.get_calls, fake.head_calls, fake.rows_downloaded
                key0 = len(fake.get_keys)
                perf0 = dict(source.performance)
                tracemalloc.start()
                started = time.perf_counter()
                def measure(label, fn):
                    start = time.perf_counter()
                    result = fn()
                    record["seconds"][label] = time.perf_counter() - start
                    return result
                if predecessor is None:
                    snapshot = measure("snapshot_discovery_freeze", lambda: freeze_investigation_snapshot(
                        start_date="2026-09-25", end_date="2026-09-25", source=source))
                else:
                    snapshot = measure("snapshot_discovery_freeze", lambda: freeze_investigation_snapshot_incremental(
                        predecessor=predecessor, start_date="2026-09-25", end_date="2026-09-25", source=source))
                freeze_gets = fake.get_calls - get0
                if name == "UNCHANGED":
                    require(snapshot.snapshot_id == predecessor.snapshot_id, "UNCHANGED snapshot identity changed")
                    require(freeze_gets == 0, "UNCHANGED freeze downloaded bodies")
                elif name == "INCREMENTAL":
                    require(snapshot.snapshot_id != predecessor.snapshot_id, "Delta did not invalidate snapshot")
                    require(fake.get_keys[key0:] == [key], "Incremental freeze did not acquire only delta")
                measure("manifest_persistence", lambda: save_investigation_snapshot(
                    snapshot, manifests / f"{snapshot.snapshot_id}.json"))
                frontier = SimpleNamespace(snapshot_id=snapshot.snapshot_id,
                    fingerprint=snapshot.snapshot_fingerprint, investigation_epoch=snapshot.evidence_epoch,
                    frontier_start=snapshot.start_date, frontier_end=snapshot.end_date,
                    predecessor_snapshot_id=None if name != "INCREMENTAL" else predecessor.snapshot_id,
                    changed_datasets=tuple(BOUND_DATASETS) if name != "INCREMENTAL" else ("shadow_runtime",),
                    stale_datasets=(), missing_optional_datasets=(), status="SNAPSHOT_READY")
                # Unchanged handoff must retain exactly the original replay scope.
                if name == "UNCHANGED":
                    frontier = previous_frontier
                calls = []
                provenance_seconds = [0.0]
                def execute(runner, question, population, context):
                    calls.append(question.id)
                    try:
                        return canonical._execute_runner(runner, question, population, context)
                    finally:
                        provenance_seconds[0] += context.runner_artifacts.get(
                            "performance_provenance", {}).get("provenance_seconds", 0.0)
                progress = []
                inputs = CycleSnapshotInputs("offline-" + name)
                try:
                    # Admit during evaluation, preserving completed replay's ability
                    # to skip materialization. No eager reader before replay.
                    cycle = measure("evaluation_including_materialization", lambda: canonical.run_canonical_question_cycle(
                        frontier, source=source, manifest_directory=manifests,
                        state_directory=directory / "questions", store=questions,
                        registry=harness_registry, runners={"E3": run_e3}, runner_executor=execute,
                        execution_revision="offline-real-e3-v1", progress_callback=progress.append,
                        shared_inputs=inputs, input_cycle_id="offline-" + name,
                        force_inline_question_ids=("E3",)))
                    diagnostics = inputs.diagnostics()
                finally:
                    inputs.close()
                require(not cycle.failed_question_ids, "Cycle is not eligible for completed replay: " + str(cycle.failed_question_ids))
                if name != "UNCHANGED":
                    require(calls == ["E3"], "Real E3 not executed exactly once: " + str(calls))
                else:
                    require(cycle == previous_cycle and not calls, "Completed cycle did not replay exactly")
                qproj = measure("question_projection_read", lambda: questions.load_projection(cycle.cycle_id))
                require(set(qproj["questions"]) == set(BASELINE_QUESTION_IDS), "70-question registry mismatch")
                bridge = measure("scientific_state", lambda: run_scientific_state_bridge(
                    cycle, question_store=questions, scientific_store=scientific,
                    optimisation_registry=registry, treatment_memory_path=directory / "treatment_memory.json",
                    policy_catalog=()))
                require(not bridge.failures, "Scientific bridge failed: " + str(bridge.failures))
                require(canonical_json(scientific.load_run(bridge.bridge_run_id)) == canonical_json(bridge.to_dict()),
                        "Separate bridge receipt mismatch")
                projection = measure("projection_build", lambda: build_unified_research_projection(
                    continuous_cycle_id="OFFLINE-" + cycle.cycle_id, frontier=frontier,
                    question_projection=qproj, bridge=bridge, scientific_store=scientific,
                    optimisation_registry=registry, validation_store=validation))
                latest_mtime_before = publication.latest_path.stat().st_mtime_ns if publication.latest_path.exists() else None
                history = measure("projection_publication", lambda: publication.save(projection))
                record["publication_reused_without_latest_rewrite"] = (
                    latest_mtime_before is not None and publication.latest_path.stat().st_mtime_ns == latest_mtime_before)
                if name == "UNCHANGED":
                    require(record["publication_reused_without_latest_rewrite"], "Unchanged publication rewrote latest")
                view = measure("research_lab_read", lambda: load_lab_view(projection_directory=publication.directory))
                require(view["projection_version"] == projection["projection_version"], "Lab version mismatch")
                require(canonical_json(publication.load_latest()) == canonical_json(projection), "Publication mismatch")
                require({r["question_id"] for r in projection["canonical_questions"]} == set(BASELINE_QUESTION_IDS),
                        "Projection registry mismatch")
                require(all(r["result"] == qproj["questions"][r["question_id"]]["result"]
                            for r in projection["canonical_questions"]), "Scientific result projection changed")
                if previous_projection is not None:
                    if name == "UNCHANGED":
                        require(canonical_json(projection) == canonical_json(previous_projection), "Replay publication changed")
                    else:
                        require(projection["projection_version"] != previous_projection["projection_version"],
                                "Incremental projection failed to invalidate")
                record["seconds"]["total_wall"] = time.perf_counter() - started
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                perf = dict(source.performance)
                record.update(status="OK", snapshot_id=snapshot.snapshot_id,
                    snapshot_fingerprint=snapshot.snapshot_fingerprint, cycle_id=cycle.cycle_id,
                    result_ids=dict(cycle.result_ids), bridge_run_id=bridge.bridge_run_id,
                    projection_version=projection["projection_version"],
                    question_statuses={qid: row["result"]["status"] for qid, row in qproj["questions"].items()},
                    executed_questions=calls, replayed_questions=70 if name == "UNCHANGED" else 0,
                    retained_questions=cycle.retained_count, evaluated_questions=cycle.evaluated_count,
                    unsupported_question_failures=list(cycle.failed_question_ids),
                    body_gets=fake.get_calls-get0, metadata_heads=fake.head_calls-head0,
                    freeze_body_gets=freeze_gets, downloaded_raw_rows=fake.rows_downloaded-rows0,
                    freeze_reused_raw_rows=sum(len(body.splitlines()) for k, body in fake.objects.items()
                        if k not in fake.get_keys[key0:]), peak_python_traced_bytes=peak,
                    process_peak_rss="NOT MEASURED", shared_inputs=diagnostics,
                    source_performance_delta={k: perf[k]-perf0.get(k,0) for k in perf
                                              if isinstance(perf[k], (int,float))},
                    progress=progress)
                record["seconds"]["provenance_within_real_runner"] = provenance_seconds[0]
                record["seconds"]["evidence_materialization_within_evaluation"] = diagnostics["reader_construction_seconds_inclusive"]
                record["seconds"]["evidence_acquisition_body_load_inclusive"] = (
                    perf.get("body_load_seconds_inclusive", 0.0)-perf0.get("body_load_seconds_inclusive", 0.0))
                record["evidence_acquisition_timing"] = "Body-load diagnostic overlaps freeze/materialization; do not sum inclusive times"
                predecessor, previous_cycle, previous_projection = snapshot, cycle, projection
                previous_frontier = frontier
                (directory / "results.json").write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")

            # Standalone integrity checks, excluded from scenario measurements.
            integrity = directory / "integrity"
            probe = ResearchProjectionStore(integrity)
            hp = probe.save(previous_projection)
            canonical_before = canonical_json(previous_projection)
            restarted = ResearchProjectionStore(integrity)
            require(canonical_json(restarted.load_latest()) == canonical_before, "Restart identity mismatch")
            require(restarted.save(previous_projection) == hp, "Restart publication path mismatch")
            probe.latest_path.write_text("{bad json", encoding="utf-8")
            probe.save(previous_projection)
            require(canonical_json(probe.load_latest()) == canonical_before, "Corrupt latest not repaired")
            hp.write_text("{}", encoding="utf-8")
            try:
                probe.save(previous_projection)
            except ResearchProjectionError:
                pass
            else:
                raise RuntimeError("Conflicting history silently accepted")
            # Real Phase 2C duplicate-sensitive provenance, independent check.
            start = time.perf_counter()
            base = [{"schema_version": "execution_results_v1", "correlation_id": "C", "entity_id": "E"}]
            require(evidence_digest(base) != evidence_digest(base + base), "Provenance lost duplicate multiplicity")
            duplicate_seconds = time.perf_counter() - start
            # Reject a manifest-object SHA conflict through the real bound reader.
            binding = next(b for b in predecessor.datasets if b.presence == "PRESENT")
            wrong = binding.objects[0].to_dict()
            wrong["content_sha256"] = "0" * 64
            from research_engine.data_access.s3_source import ResearchDataSourceError
            try:
                source.read_bound_objects(binding.dataset, [wrong], expected_schema_version=binding.schema_version)
            except ResearchDataSourceError:
                pass
            else:
                raise RuntimeError("Conflicting SHA authority accepted")
            output["standalone_checks"] = {"provenance_duplicate_check_seconds": duplicate_seconds,
                                           "latest_corruption_repair": "PASS", "history_conflict_rejection": "PASS",
                                           "bound_object_sha_conflict_rejection": "PASS", "restart_recovery": "PASS"}
        output["status"] = "OK"
    except Exception as exc:
        output["status"] = "FAILED"
        output["error"] = f"{type(exc).__name__}: {exc}"
        if output["scenarios"]:
            output["scenarios"][-1]["status"] = "FAILED"
        raise
    finally:
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        (directory / "results.json").write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    return output


def main():
    args = parse_args()
    directory = Path(tempfile.mkdtemp(prefix="research_pipeline_benchmark_", dir=args.output_root))
    print(f"Offline generated state/results: {directory}", flush=True)
    try:
        with offline_guard():
            result = run(args, directory)
    except Exception as exc:
        if not (directory / "results.json").exists():
            (directory / "results.json").write_text(json.dumps({"status": "FAILED",
                "error": f"{type(exc).__name__}: {exc}", "output_directory": str(directory)}, indent=2), encoding="utf-8")
        print(f"BENCHMARK FAILED: {exc}\nPreserved state: {directory}", file=sys.stderr)
        return 1
    for row in result["scenarios"]:
        print(f"{row['scenario']}: {row['seconds']['total_wall']:.4f}s; "
              f"GET={row['body_gets']} HEAD={row['metadata_heads']}; "
              f"executed={row['executed_questions']} replayed={row['replayed_questions']}; "
              f"peak Python={row['peak_python_traced_bytes']} bytes")
    print("69 other evaluators and continuous coordinator NOT EXERCISED. "
          "No production/full-pipeline speedup claim.")
    print(f"Machine-readable results: {directory / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
