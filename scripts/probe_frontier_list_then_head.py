"""Read-only reproduction of FRONTIER's all-dataset LIST then first HEAD batch.

Run only on the authenticated host. The parent kills its child after 60 seconds.
No GET, cache/checkpoint writes, snapshot publication, or cadence invocation.
"""
from __future__ import annotations

import faulthandler
import json
import subprocess
import sys
import threading
import time

from core.production_data_contract import current_schema
from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.v10.continuous.frontier_coordinator import (
    BOUND_DATASETS, FrontierAcquisitionCheckpointStore, _checkpoint_boundary,
    _same_frontier_listing_identity,
)
from scripts.probe_frontier_head import BUCKET, CHECKPOINT_DIR, PROFILE, REGION, ROOT


WALL_TIMEOUT_SECONDS = 60
HEAD_STACK_AFTER_SECONDS = 12
LIST_STACK_AFTER_SECONDS = 35
HEAD_BATCH_SIZE = 32


def emit(event: dict) -> None:
    print(json.dumps(event, sort_keys=True), flush=True)


def first_revalidation_batch(listed: tuple[dict, ...], prior: list[dict]) -> tuple[list[dict], int]:
    prior_by_key = {str(item.get("s3_key") or item.get("identifier") or ""): item
                    for item in prior}
    checks = []
    for item in listed:
        key = str(item.get("identifier") or "")
        previous = prior_by_key.get(key)
        if (previous is not None
                and _same_frontier_listing_identity(previous, item)
                and not (previous.get("version_id") is None
                         and item.get("version_id") is None)):
            checks.append(item)
    return checks[:HEAD_BATCH_SIZE], len(checks)


def worker() -> int:
    source = S3ResearchDataSource(
        bucket=BUCKET, region=REGION, profile=PROFILE,
        verified_cache_directory=ROOT / "data/research/continuous/verified_object_bodies",
        verified_cache_max_bytes=1024 * 1024 * 1024)
    checkpoint = FrontierAcquisitionCheckpointStore(CHECKPOINT_DIR).load(
        boundary=_checkpoint_boundary(source))
    if not checkpoint or "trade_truth" not in checkpoint:
        raise ValueError("verified trade_truth checkpoint unavailable")
    client = source._get_client()
    config = client.meta.config
    settings = (config.connect_timeout, config.read_timeout,
                config.retries.get("total_max_attempts"), config.max_pool_connections)
    emit({"event": "client_ready", "client_identity": id(client),
          "connect_timeout_seconds": settings[0], "read_timeout_seconds": settings[1],
          "total_max_attempts": settings[2], "max_pool_connections": settings[3],
          "checkpoint_datasets": len(checkpoint)})
    if settings != (3, 5, 1, 16):
        raise RuntimeError("research S3 client does not have the bounded production config")

    faulthandler.dump_traceback_later(
        LIST_STACK_AFTER_SECONDS, repeat=False, file=sys.stderr)
    stage = "list"
    try:
        listed_by_dataset = {}
        list_started = time.monotonic()
        for dataset in BOUND_DATASETS:
            started = time.monotonic()
            listed_by_dataset[dataset] = tuple(source.discover_dataset_objects(dataset))
            emit({"event": "list_completed", "dataset": dataset,
                  "objects": len(listed_by_dataset[dataset]),
                  "list_calls": source.performance_snapshot()["list_calls"],
                  "elapsed_seconds": round(time.monotonic() - started, 3),
                  "client_identity": id(source._get_client())})
        emit({"event": "all_lists_completed", "datasets": len(listed_by_dataset),
              "list_calls": source.performance_snapshot()["list_calls"],
              "elapsed_seconds": round(time.monotonic() - list_started, 3)})

        listed = listed_by_dataset["trade_truth"]
        prior = list(checkpoint["trade_truth"].get("objects") or [])
        batch, total_checks = first_revalidation_batch(listed, prior)
        if not batch:
            raise ValueError("no trade_truth checkpoint objects need HEAD revalidation")
        emit({"event": "head_batch_ready", "listed_objects": len(listed),
              "objects_requiring_head": total_checks, "batch_objects": len(batch),
              "client_identity": id(source._get_client())})
        faulthandler.cancel_dump_traceback_later()
        faulthandler.dump_traceback_later(
            HEAD_STACK_AFTER_SECONDS, repeat=False, file=sys.stderr)
        stage = "head"
        head_started = time.monotonic()
        latest = {"queued": 0, "completed": 0, "successful": 0,
                  "failed": 0, "retried": 0}
        done = threading.Event()

        def monitor() -> None:
            while not done.wait(2):
                emit({"event": "head_heartbeat", **dict(latest),
                      "head_calls_started": source.performance_snapshot()["head_calls"],
                      "elapsed_seconds": round(time.monotonic() - head_started, 3)})

        observer = threading.Thread(target=monitor, daemon=True)
        observer.start()

        def progress(counts: dict) -> None:
            latest.update(counts)
            emit({"event": "head_progress", **dict(counts),
                  "head_calls_started": source.performance_snapshot()["head_calls"],
                  "elapsed_seconds": round(time.monotonic() - head_started, 3)})

        try:
            headed = source.head_bound_objects(
                "trade_truth", batch, expected_schema_version=current_schema("trade_truth"),
                max_workers=16, progress_callback=progress)
        finally:
            done.set()
            observer.join(timeout=1)
        emit({"event": "head_batch_completed", "objects": len(headed),
              "elapsed_seconds": round(time.monotonic() - head_started, 3),
              "source_io": source.performance_snapshot(),
              "client_identity": id(source._get_client())})
        return 0
    except Exception as exc:
        emit({"event": "failed", "stage": stage,
              "error_type": type(exc).__name__, "error": str(exc)[:500],
              "list_calls": source.performance_snapshot()["list_calls"],
              "head_calls_started": source.performance_snapshot()["head_calls"]})
        return 1
    finally:
        faulthandler.cancel_dump_traceback_later()


def parent() -> int:
    command = [sys.executable, "-u", "-m", "scripts.probe_frontier_list_then_head",
               "--worker"]
    try:
        child = subprocess.run(command, cwd=ROOT, capture_output=True,
                               text=True, timeout=WALL_TIMEOUT_SECONDS)
        stdout, stderr, status = child.stdout, child.stderr, child.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        status = 124
    if stdout:
        print(stdout, end="" if stdout.endswith("\n") else "\n")
    if stderr:
        print(stderr, file=sys.stderr,
              end="" if stderr.endswith("\n") else "\n")
    if status == 124:
        emit({"event": "wall_timeout", "wall_timeout_seconds": WALL_TIMEOUT_SECONDS})
    return status


def main() -> int:
    if sys.argv[1:] == ["--worker"]:
        try:
            return worker()
        except Exception as exc:
            emit({"event": "failed", "stage": "setup",
                  "error_type": type(exc).__name__, "error": str(exc)[:500]})
            return 1
    if sys.argv[1:]:
        raise SystemExit("usage: python -m scripts.probe_frontier_list_then_head")
    return parent()


if __name__ == "__main__":
    raise SystemExit(main())
