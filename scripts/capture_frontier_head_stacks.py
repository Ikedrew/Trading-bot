"""Read-only, 25-second host probe of FRONTIER's real HEAD worker path.

Uses 16 existing trade_truth checkpoint objects. No LIST, GET, cache or
checkpoint writes. A worker thread dump goes to stderr after 10 seconds.
"""
from __future__ import annotations

import faulthandler
import json
import subprocess
import sys

from core.production_data_contract import current_schema
from research_engine.data_access.s3_source import S3ResearchDataSource
from scripts.probe_frontier_head import BUCKET, PROFILE, REGION, ROOT
from scripts.probe_frontier_head_concurrency import checkpoint_sample


WALL_TIMEOUT_SECONDS = 25
STACK_AFTER_SECONDS = 10


def worker() -> int:
    items = checkpoint_sample()
    source = S3ResearchDataSource(bucket=BUCKET, region=REGION, profile=PROFILE)
    client = source._get_client()
    config = client.meta.config
    print(json.dumps({"event": "client_ready", "objects": len(items),
                      "connect_timeout_seconds": config.connect_timeout,
                      "read_timeout_seconds": config.read_timeout,
                      "total_max_attempts": config.retries.get("total_max_attempts"),
                      "max_pool_connections": config.max_pool_connections}), flush=True)
    faulthandler.dump_traceback_later(
        STACK_AFTER_SECONDS, repeat=False, file=sys.stderr)
    try:
        headed = source.head_bound_objects(
            "trade_truth", items,
            expected_schema_version=current_schema("trade_truth"),
            progress_callback=lambda counts: print(json.dumps(
                {"event": "progress", **counts}), flush=True))
        print(json.dumps({"event": "completed", "objects": len(headed),
                          "head_calls": source.performance_snapshot()["head_calls"]}),
              flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({"event": "failed", "error_type": type(exc).__name__,
                          "head_calls": source.performance_snapshot()["head_calls"]}),
              flush=True)
        return 1
    finally:
        faulthandler.cancel_dump_traceback_later()


def parent() -> int:
    command = [sys.executable, "-u", "-m", "scripts.capture_frontier_head_stacks",
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
        print(json.dumps({"event": "wall_timeout",
                          "wall_timeout_seconds": WALL_TIMEOUT_SECONDS}), flush=True)
    return status


def main() -> int:
    if sys.argv[1:] == ["--worker"]:
        return worker()
    if sys.argv[1:]:
        raise SystemExit("usage: python -m scripts.capture_frontier_head_stacks")
    return parent()


if __name__ == "__main__":
    raise SystemExit(main())
