"""Run the existing cadence against the qualified, isolated Stage A fixture.

This is an offline operator demonstration. It never acquires live S3 evidence or
publishes trading authority. The Research Lab reads its completed projections
through the production continuous projection bridge.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from test_canonical_question_cycle import MemoryS3, _objects, _key, _jsonl, _row
from test_stage_a_three_cycle_offline_autonomy import (
    CHANGED_DATASET, _isolation_bundle, _run_tick,
)
from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True,
                        help="isolated fixture, scientific state and cadence root")
    parser.add_argument("--interval-seconds", type=float, default=20.0,
                        help="pause between the first three demonstration ticks")
    parser.add_argument("--steady-interval-seconds", type=float, default=60.0,
                        help="unchanged-evidence cadence after the demonstration")
    parser.add_argument("--max-ticks", type=int, default=None,
                        help="stop after this many ticks; default continues")
    args = parser.parse_args()
    if args.interval_seconds < 0 or args.steady_interval_seconds <= 0:
        parser.error("intervals must be nonnegative, and steady interval positive")
    root = args.root.resolve()
    production = (ROOT / "data/research/continuous").resolve()
    if root == production or production in root.parents or root in production.parents:
        parser.error("root must be separate from production research state")
    root.mkdir(parents=True, exist_ok=True)
    events_path = root / "activation_events.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()] if events_path.exists() else []
    phase = len(events)
    fake = MemoryS3(_objects())
    if phase >= 3:
        fake.objects[_key(CHANGED_DATASET)] = _jsonl(_row(CHANGED_DATASET, "changed"))
    source = S3ResearchDataSource(bucket="stage-a-fixture", client=fake)
    registry = root / "registry"
    if not (registry / "registry.json").exists():
        OptimisationRegistry(str(registry)).save()
    state_root = root / "continuous"
    bundle = _isolation_bundle(root, source)
    tick_count = 0
    while args.max_ticks is None or tick_count < args.max_ticks:
        if phase == 2:
            fake.objects[_key(CHANGED_DATASET)] = _jsonl(_row(CHANGED_DATASET, "changed"))
        result = _run_tick(state_root, bundle)
        if result["fast_outcome"] not in ("COMPLETED", "NO_NEW_RESEARCH_EVIDENCE"):
            raise RuntimeError("OFFLINE_CADENCE_FAILED:" + str(result["fast_outcome"]))
        projection = result["projection"]
        if projection is None or len(projection.get("canonical_questions") or []) != 70:
            raise RuntimeError("OFFLINE_PUBLICATION_INCOMPLETE")
        row = {
            "phase": phase + 1,
            "outcome": result["fast_outcome"],
            "snapshot_id": result["snapshot_id"],
            "projection_version": result["projection_version"],
            "cycle_id": result["cycle_id"],
            "changed_datasets": result["changed_datasets"],
            "status_counts": result["status_counts"],
            "question_result_ids": {q["question_id"]: (q.get("result") or {}).get("result_id") for q in projection["canonical_questions"]},
            "elapsed_seconds": result["elapsed"],
        }
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        print(json.dumps({k: v for k, v in row.items() if k != "question_result_ids"}, sort_keys=True), flush=True)
        phase += 1
        tick_count += 1
        if args.max_ticks is not None and tick_count >= args.max_ticks:
            break
        time.sleep(args.interval_seconds if phase < 3 else args.steady_interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
