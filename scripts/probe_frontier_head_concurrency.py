"""Bounded, read-only current-version HEAD comparison for FRONTIER's client path.

Run only in the authenticated host PowerShell session. Each configuration has a
separate child process, which the parent terminates after 30 seconds.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import subprocess
import sys
import threading
import time
from typing import Any, Callable

from botocore.config import Config
from botocore.exceptions import ConnectTimeoutError, ReadTimeoutError

from research_engine.data_access.s3_source import S3ResearchDataSource, _build_session
from research_engine.v10.continuous.frontier_coordinator import (
    FrontierAcquisitionCheckpointStore, _checkpoint_boundary,
)
from scripts.probe_frontier_head import (
    BUCKET, CHECKPOINT_DIR, CONNECT_TIMEOUT_SECONDS, PROFILE, READ_TIMEOUT_SECONDS,
    REGION, ROOT,
)


SAMPLE_SIZE = 16
WORKER_COUNTS = (1, 4, 16)
WALL_TIMEOUT_SECONDS = 30


def checkpoint_sample() -> list[dict[str, Any]]:
    source = S3ResearchDataSource(bucket=BUCKET, region=REGION, profile=PROFILE)
    datasets = FrontierAcquisitionCheckpointStore(CHECKPOINT_DIR).load(
        boundary=_checkpoint_boundary(source))
    if not datasets or "trade_truth" not in datasets:
        raise ValueError("verified trade_truth checkpoint unavailable")
    objects = [dict(item) for item in datasets["trade_truth"].get("objects") or []
               if item.get("s3_key") and item.get("version_id") not in (None, "", "null")
               and item.get("content_sha256")]
    if len(objects) < SAMPLE_SIZE:
        raise ValueError(f"need {SAMPLE_SIZE} versioned trade_truth objects; found {len(objects)}")
    positions = [i * (len(objects) - 1) // (SAMPLE_SIZE - 1)
                 for i in range(SAMPLE_SIZE)]
    return [{**objects[position], "_checkpoint_position": position}
            for position in positions]


def object_label(item: dict[str, Any], index: int) -> dict[str, Any]:
    return {"index": index, "key_sha256_prefix": hashlib.sha256(
        str(item["s3_key"]).encode("utf-8")).hexdigest()[:16],
        "checkpoint_position": item.get("_checkpoint_position")}


def items_for_workers(workers: int, sample: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Four serial requests fit the 30-second deadline at the observed ~4 s/HEAD.
    # The 4- and 16-worker runs share all 16 objects for direct comparison.
    return [sample[i] for i in (0, 5, 10, 15)] if workers == 1 else sample


def run_worker(
    workers: int, items: list[dict[str, Any]], *,
    session_factory: Callable[[str | None, str], Any] = _build_session,
    emit: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Share one bounded S3 client, as FRONTIER does, and report each request."""
    output_lock = threading.Lock()

    def report(event: dict[str, Any]) -> None:
        with output_lock:
            if emit is None:
                print(json.dumps(event, sort_keys=True), flush=True)
            else:
                emit(event)

    report({"event": "selection", "workers": workers,
            "objects": [object_label(item, i) for i, item in enumerate(items)]})
    session = session_factory(PROFILE, REGION)
    if session.get_credentials() is None:
        raise RuntimeError("named profile returned no credentials")
    config = Config(connect_timeout=CONNECT_TIMEOUT_SECONDS,
                    read_timeout=READ_TIMEOUT_SECONDS,
                    retries={"total_max_attempts": 1})
    client = session.client("s3", region_name=REGION, config=config)
    report({"event": "client_ready", "workers": workers,
            "connect_timeout_seconds": client.meta.config.connect_timeout,
            "read_timeout_seconds": client.meta.config.read_timeout,
            "total_max_attempts": client.meta.config.retries.get("total_max_attempts"),
            "max_pool_connections": client.meta.config.max_pool_connections})

    def inspect(index: int, item: dict[str, Any]) -> dict[str, Any]:
        label = object_label(item, index)
        report({"event": "started", **label})
        started = time.monotonic()
        try:
            response = client.head_object(Bucket=BUCKET, Key=str(item["s3_key"]))
            expected_size = int(item.get("listed_byte_count") or item.get("byte_count") or 0)
            checks = {
                "version_id": response.get("VersionId") == item["version_id"],
                "etag": str(response.get("ETag") or "").strip('"') == item.get("etag"),
                "size": int(response.get("ContentLength") or 0) == expected_size,
            }
            if all(checks.values()):
                status = "HEAD_MATCH"
            elif not response.get("VersionId"):
                status = "HEAD_IDENTITY_UNCONFIRMED"
            elif not checks["version_id"]:
                status = "HEAD_CHANGED"
            else:
                status = "HEAD_INCONSISTENT"
            result = {"event": "completed", **label, "status": status,
                      "checks": checks}
        except Exception as exc:
            response = getattr(exc, "response", None) or {}
            result = {"event": "completed", **label, "status": "HEAD_ERROR",
                      "error_type": type(exc).__name__,
                      "aws_error_code": (response.get("Error") or {}).get("Code"),
                      "request_timed_out": isinstance(
                          exc, (ConnectTimeoutError, ReadTimeoutError, TimeoutError))}
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        report(result)
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(inspect, index, item) for index, item in enumerate(items)]
        for future in as_completed(futures):
            future.result()


def summarize(workers: int, lines: str, wall_seconds: float, *,
              deadline_hit: bool, worker_exit_code: int | None) -> dict[str, Any]:
    events = []
    for line in lines.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # A killed worker may leave one incomplete final line.
    selected = next((event["objects"] for event in events
                     if event.get("event") == "selection"), [])
    client = next((event for event in events if event.get("event") == "client_ready"), None)
    worker_error = next((event for event in events
                         if event.get("event") == "worker_error"), None)
    started = {event["index"] for event in events if event.get("event") == "started"}
    completed = {event["index"]: event for event in events
                 if event.get("event") == "completed"}
    failed = [event for event in completed.values() if event["status"] == "HEAD_ERROR"]
    timed_out = sum(bool(event.get("request_timed_out")) for event in failed)
    outstanding = sorted(started - completed.keys())
    return {
        "workers": workers, "status": ("WALL_TIMEOUT" if deadline_hit else
                                         "COMPLETE" if worker_exit_code == 0 else "WORKER_ERROR"),
        "selected_objects": selected, "client_config": client,
        "worker_error": worker_error,
        "requests_started": len(started), "requests_completed": len(completed),
        "successful": len(completed) - len(failed), "failed": len(failed),
        "timed_out": timed_out + (len(outstanding) if deadline_hit else 0),
        "outstanding_at_deadline": outstanding if deadline_hit else [],
        "request_results": [completed[index] for index in sorted(completed)],
        "wall_seconds": round(wall_seconds, 3),
    }


def run_parent() -> int:
    all_complete = True
    for workers in WORKER_COUNTS:
        started = time.monotonic()
        try:
            child = subprocess.run(
                [sys.executable, "-u", "-m", "scripts.probe_frontier_head_concurrency",
                 "--worker", str(workers)],
                cwd=ROOT, capture_output=True, text=True,
                timeout=WALL_TIMEOUT_SECONDS)
            output, return_code, deadline_hit = child.stdout, child.returncode, False
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or b""
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            return_code, deadline_hit = None, True
        result = summarize(workers, output, time.monotonic() - started,
                           deadline_hit=deadline_hit, worker_exit_code=return_code)
        print(json.dumps(result, sort_keys=True), flush=True)
        all_complete &= result["status"] == "COMPLETE"
    return 0 if all_complete else 1


def main() -> int:
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--worker" and args[1] in ("1", "4", "16"):
        try:
            workers = int(args[1])
            run_worker(workers, items_for_workers(workers, checkpoint_sample()))
        except Exception as exc:
            response = getattr(exc, "response", None) or {}
            print(json.dumps({"event": "worker_error", "error_type": type(exc).__name__,
                              "aws_error_code": (response.get("Error") or {}).get("Code")}),
                  flush=True)
            return 1
        return 0
    if args:
        raise SystemExit("usage: python -m scripts.probe_frontier_head_concurrency")
    return run_parent()


if __name__ == "__main__":
    raise SystemExit(main())
