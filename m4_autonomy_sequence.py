"""Run the governed changed/unchanged cycles after m4_integrity_gate passes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

HOST = Path(__file__).resolve().parent
REPO = HOST.parent / "Trading-bot-stage-a-restored"
BASE = HOST / "milestone3_resume_run"
GATE = HOST / "milestone4_integrity_gate"
ROOT = HOST / "milestone4_autonomy_sequence"
BASELINE_ID = "ISNAP-5FB0DD9FC4D808D6C23789CD"
os.environ["RESEARCH_AWS_PROFILE"] = "trading-bot-new"
sys.path.insert(0, str(REPO))

from m4_integrity_gate import LocalEvidenceSource, indexed_cache
from research_engine.v10.investigation_snapshot import (
    authenticate_snapshot_inputs, load_investigation_snapshot_id,
    verify_investigation_snapshot_identity,
)
from research_engine.v10.continuous.frontier_coordinator import (
    FrontierCycleResult, FrontierSelection, FrontierStateStore, STATE_SCHEMA,
    SNAPSHOT_READY, _compute_delta, _full_object_record)
from research_engine.v10.continuous.research_loop import (
    _stale_evaluation_questions, run_continuous_research_cycle)
from research_engine.v10.continuous.research_projection import ResearchProjectionStore
from research_engine.v10.continuous.question_cycle_state import QuestionCycleStore
from research_engine.v10.continuous.continuous_projection_bridge import sync_latest_completed_cycle


def event(stage, **facts):
    ROOT.mkdir(exist_ok=True)
    row = {"at_utc": datetime.now(timezone.utc).isoformat(), "stage": stage, **facts}
    with (ROOT / "m4_events.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(row, default=str) + "\n")
    print(json.dumps(row, default=str), flush=True)


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def seed_frontier(baseline):
    members = {}
    for binding in baseline.datasets:
        name = binding.dataset
        members[name] = {
            "classification": binding.requirement,
            "status": "READY" if binding.objects else "MISSING",
            "schema_version": binding.schema_version,
            "producer": None,
            "event_time_coverage": {"start": None, "end": None},
            "objects": [_full_object_record(name, obj.to_dict()) for obj in binding.objects],
        }
    frontier = FrontierSelection(
        frontier_id="M4-MANIFEST-BOUND-" + baseline.snapshot_id,
        selected_start_time=baseline.start_date,
        selected_end_time=baseline.end_date,
        dataset_membership=members,
        dataset_status={name: member["status"] for name, member in members.items()},
        stale_datasets=(), missing_optional_datasets=(), gap_diagnostics={},
        pending_required_objects={}, coherence_decision="FROZEN_MANIFEST_BOUND_BASELINE",
        predecessor_snapshot_id=None)
    stamp = datetime.now(timezone.utc).isoformat()
    state = {
        "coordinator_schema": STATE_SCHEMA, "status": "SUCCESS",
        "last_successful_snapshot_id": baseline.snapshot_id,
        "last_successful_snapshot_fingerprint": baseline.snapshot_fingerprint,
        "last_successful_investigation_epoch": baseline.evidence_epoch,
        "exact_consumed_objects_by_dataset": {
            name: value["objects"] for name, value in members.items()},
        "coherent_frontier_start": baseline.start_date,
        "coherent_frontier_end": baseline.end_date,
        "membership_closed_at": stamp, "completion_timestamp": stamp,
        "failure_information": None, "predecessor_snapshot_id": None,
        "frontier": frontier.to_dict(), "delta": {},
    }
    FrontierStateStore(ROOT / "frontier_state").save_success(state)


def source_for_successor():
    gate = json.loads((GATE / "gate_result.json").read_text(encoding="utf-8"))
    successor = load_investigation_snapshot_id(gate["successor_id"],
        manifest_directory=ROOT / "manifests")
    baseline = load_investigation_snapshot_id(BASELINE_ID,
        manifest_directory=ROOT / "manifests")
    index_path = ROOT / "local_cache_path_index.json"
    if index_path.exists():
        indexed = json.loads(index_path.read_text(encoding="utf-8"))
        if indexed.get("baseline_id") != BASELINE_ID or len(indexed.get("paths", {})) != 44938:
            raise RuntimeError("LOCAL_CACHE_PATH_INDEX_INVALID")
        paths = {key: Path(value) for key, value in indexed["paths"].items()}
    else:
        paths, _ = indexed_cache(baseline)
        temp_path = index_path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps({"baseline_id": BASELINE_ID,
            "paths": {key: str(value) for key, value in paths.items()}},
            sort_keys=True), encoding="utf-8")
        temp_path.replace(index_path)
    membership = {binding.dataset: [obj.to_dict() for obj in binding.objects]
                  for binding in successor.datasets}
    source = LocalEvidenceSource(paths, membership,
        GATE / "simulated_execution_context.jsonl", gate["simulated_key"])
    source.cache_paths = paths
    return source, baseline, successor


def recover_verified_frontier(**options):
    """Replay the persisted production FRONTIER receipt after fresh authentication."""
    source = options["source"]
    store = FrontierStateStore(ROOT / "frontier_state")
    history = store.load_latest_success()
    if history is None or history["last_successful_snapshot_id"] != (
            "ISNAP-5DA156EB1082C659C56EF4B7"):
        raise RuntimeError("RECOVERY_FRONTIER_HISTORY_MISSING_OR_CHANGED")
    snapshot = verify_investigation_snapshot_identity(
        history["last_successful_snapshot_id"], source=source,
        manifest_directory=ROOT / "manifests", fresh_observation=True)
    authenticate_snapshot_inputs(snapshot, source)
    if (history["last_successful_snapshot_fingerprint"] != snapshot.snapshot_fingerprint
            or history["last_successful_investigation_epoch"] != snapshot.evidence_epoch):
        raise RuntimeError("RECOVERY_FRONTIER_SNAPSHOT_IDENTITY_MISMATCH")
    prior_path = store.history_directory / (BASELINE_ID + ".json")
    prior = json.loads(prior_path.read_text(encoding="utf-8"))
    frontier = FrontierSelection(**history["frontier"])
    delta = _compute_delta(frontier, prior)
    if (json.dumps(delta, sort_keys=True) != json.dumps(history["delta"], sort_keys=True)
            or tuple(delta["changed_datasets"]) != ("execution_context",)):
        raise RuntimeError("RECOVERY_FRONTIER_DELTA_MISMATCH")
    event("FRONTIER_RECEIPT_REUSED", snapshot_id=snapshot.snapshot_id,
          frontier_id=frontier.frontier_id, changed_datasets=delta["changed_datasets"])
    return FrontierCycleResult(
        status=SNAPSHOT_READY, snapshot_id=snapshot.snapshot_id,
        fingerprint=snapshot.snapshot_fingerprint,
        investigation_epoch=snapshot.evidence_epoch,
        frontier_id=frontier.frontier_id,
        frontier_start=frontier.selected_start_time,
        frontier_end=frontier.selected_end_time,
        predecessor_snapshot_id=history["predecessor_snapshot_id"],
        membership_closed_at=frontier.membership_closed_at,
        changed_datasets=tuple(delta["changed_datasets"]),
        unchanged_datasets=tuple(delta["unchanged_datasets"]),
        missing_optional_datasets=tuple(frontier.missing_optional_datasets),
        stale_datasets=tuple(frontier.stale_datasets),
        new_object_count=int(delta["new_object_count"]),
        replaced_object_count=int(delta["replaced_object_count"]),
        verification_status="VERIFIED", dataset_status=frontier.dataset_status,
        delta=delta)


def prepare():
    gate_result = json.loads((GATE / "gate_result.json").read_text(encoding="utf-8"))
    if gate_result.get("status") != "PASS":
        raise RuntimeError("HARD_GATE_NOT_PASSED")
    if ROOT.exists():
        raise RuntimeError("AUTONOMY_OUTPUT_ALREADY_EXISTS")
    event("PREPARING_ISOLATED_STATE", baseline=BASELINE_ID,
          successor=gate_result["successor_id"])
    # The original directory is read-only input; every mutable read model and
    # immutable history it references receives an isolated copy.
    for name in ("continuous", "questions", "science", "registry",
                 "observation_space", "production_coverage", "lab_publication",
                 "logs"):
        source = BASE / name
        if source.exists():
            shutil.copytree(source, ROOT / name)
            event("STATE_COMPONENT_COPIED", component=name)
    shutil.copytree(GATE / "manifests", ROOT / "manifests")
    baseline = load_investigation_snapshot_id(BASELINE_ID,
        manifest_directory=ROOT / "manifests")
    seed_frontier(baseline)
    event("ISOLATED_BASELINE_READY", objects=sum(len(b.objects) for b in baseline.datasets))


def worker(sequence, *, recovery=False):
    source, baseline, successor = source_for_successor()
    if sequence == 2:
        projection = ResearchProjectionStore(ROOT / "continuous/projection").load_latest()
        stale = _stale_evaluation_questions(projection,
            question_state_dir=ROOT / "questions")
        if "G1" in stale:
            raise RuntimeError("G1_EVALUATION_IDENTITY_STALE:" + stale["G1"])
        event("EVALUATION_IDENTITY_PRECHECK", stale=stale)
    before = source.performance_snapshot()
    started = time.perf_counter()
    event("CYCLE_STARTED", sequence=sequence, expected_snapshot=successor.snapshot_id)
    os.chdir(ROOT)
    result = run_continuous_research_cycle(
        state_root=ROOT / "continuous",
        **({"frontier_runner": recover_verified_frontier} if recovery and sequence == 2 else {}),
        frontier_kwargs={"source": source,
            "state_directory": ROOT / "frontier_state",
            "manifest_directory": ROOT / "manifests",
            "as_of_date": date(2026, 10, 15)},
        question_kwargs={"source": source,
            "manifest_directory": ROOT / "manifests",
            "state_directory": ROOT / "questions",
            "snapshot_load_deadline_seconds": 1800},
        bridge_kwargs={"scientific_state_directory": ROOT / "science",
            "optimisation_registry_directory": ROOT / "registry",
            "question_state_directory": ROOT / "questions",
            "treatment_memory_path": ROOT / "treatment_memory.json"},
        observation_space_manifest_directory=ROOT / "manifests",
        observation_space_directory=ROOT / "observation_space",
        production_coverage_directory=ROOT / "production_coverage",
        production_coverage_store_path=ROOT / "research_coverage.json",
        candle_authority_directory=ROOT / "m5_candle_authority",
        counterfactual_evidence_directory=ROOT / "counterfactual_evidence",
        q71_manifest_directory=ROOT / "manifests",
        q71_evidence_source=source,
    )
    path = ROOT / f"cycle{sequence}_result.json"
    path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    event("CYCLE_ENGINE_FINISHED", sequence=sequence,
          outcome=result.cycle_outcome, failure_stage=result.failure_stage,
          failure_reason=result.failure_reason, stages=result.stage_statuses,
          source_io=source.performance_snapshot(), local_lists=source.local_lists,
          local_heads=source.local_client.heads,
          local_gets=source.local_client.gets,
          elapsed_seconds=round(time.perf_counter()-started, 3))
    expected_outcome = "COMPLETED" if sequence == 2 else "NO_NEW_RESEARCH_EVIDENCE"
    if result.cycle_outcome != expected_outcome:
        raise RuntimeError(f"CYCLE_{sequence}_OUTCOME:{result.cycle_outcome}:"
                           f"{result.failure_stage}:{result.failure_reason}")
    if result.frontier_snapshot_id != successor.snapshot_id:
        raise RuntimeError("CYCLE_SNAPSHOT_ID_MISMATCH")
    bridge = sync_latest_completed_cycle(ROOT / "lab_publication",
        source=ROOT / "continuous")
    continuous = ROOT / "continuous/projection/latest.json"
    lab = ROOT / "lab_publication/projection/latest.json"
    parity = sha256(continuous) == sha256(lab)
    if not parity:
        raise RuntimeError("RESEARCH_LAB_PROJECTION_PARITY_FAILED")
    event("CYCLE_PUBLISHED", sequence=sequence, bridge_status=bridge.status,
          projection_sha256=sha256(continuous), parity=parity,
          elapsed_seconds=round(time.perf_counter()-started, 3))


def parent(*, recovery=False):
    if not recovery:
        prepare()
    elif not (ROOT / "questions/checkpoints/QCYCLE-EB2CA76B313ED76A6D67791B06C7D7D5").exists():
        raise RuntimeError("RECOVERY_QUESTION_CHECKPOINTS_MISSING")
    for sequence in (2, 3):
        event("WORKER_LAUNCH", sequence=sequence)
        try:
            completed = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--worker",
                 str(sequence), *(("--recovery",) if recovery else ())],
                cwd=ROOT, timeout=5400, check=False)
        except subprocess.TimeoutExpired:
            event("WORKER_TIMEOUT", sequence=sequence, timeout_seconds=5400)
            return 1
        if completed.returncode != 0:
            event("SEQUENCE_STOPPED", sequence=sequence,
                  worker_exit_code=completed.returncode)
            return 1
    event("THREE_CYCLE_SEQUENCE_COMPLETED",
          cycle1_source=str(HOST / "milestone4_isolated_attempt/cycle1_result.json"),
          cycle2=str(ROOT / "cycle2_result.json"),
          cycle3=str(ROOT / "cycle3_result.json"))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=int, choices=(2, 3))
    parser.add_argument("--recovery", action="store_true")
    args = parser.parse_args()
    try:
        raise SystemExit(worker(args.worker, recovery=args.recovery)
                         if args.worker else parent(recovery=args.recovery))
    except Exception as exc:
        event("SEQUENCE_FAILED", worker=args.worker,
              error=f"{type(exc).__name__}:{exc}")
        raise
