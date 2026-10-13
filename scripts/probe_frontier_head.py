"""One bounded, read-only HEAD using FRONTIER's AWS session path.

Run from the authenticated host: python -m scripts.probe_frontier_head [--current]
No LIST, GET, PUT, cache, checkpoint, or research-state writes are performed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable

from botocore.config import Config

from research_engine.data_access.s3_source import S3ResearchDataSource, _build_session
from research_engine.v10.continuous.frontier_coordinator import (
    FrontierAcquisitionCheckpointStore, _checkpoint_boundary,
)


ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT_DIR = ROOT / "data/research/continuous/frontier_acquisition_checkpoint"
PROFILE = "trading-bot-new"
REGION = "eu-west-2"
BUCKET = "trading-bot-v10-data"
CONNECT_TIMEOUT_SECONDS = 3
READ_TIMEOUT_SECONDS = 5
WALL_TIMEOUT_SECONDS = 20


def _checkpoint_object() -> dict[str, Any]:
    source = S3ResearchDataSource(bucket=BUCKET, region=REGION, profile=PROFILE)
    datasets = FrontierAcquisitionCheckpointStore(CHECKPOINT_DIR).load(
        boundary=_checkpoint_boundary(source))
    if not datasets or "trade_truth" not in datasets:
        raise ValueError("verified trade_truth checkpoint unavailable")
    objects = datasets["trade_truth"].get("objects") or []
    return next(
        dict(item) for item in objects
        if item.get("s3_key") and item.get("version_id") not in (None, "", "null")
        and item.get("content_sha256")
    )


def probe_once(
    item: dict[str, Any], *,
    current: bool = False,
    session_factory: Callable[[str | None, str], Any] = _build_session,
) -> dict[str, Any]:
    """Perform exactly one HEAD, optionally checking the key's current version."""
    config = Config(connect_timeout=CONNECT_TIMEOUT_SECONDS,
                    read_timeout=READ_TIMEOUT_SECONDS,
                    retries={"total_max_attempts": 1})
    started = time.monotonic()
    session = session_factory(PROFILE, REGION)
    if session.get_credentials() is None:
        raise RuntimeError("named profile returned no credentials")
    client = session.client("s3", region_name=REGION, config=config)
    request = {"Bucket": BUCKET, "Key": str(item["s3_key"])}
    if not current:
        request["VersionId"] = str(item["version_id"])
    response = client.head_object(**request)
    expected_size = int(item.get("listed_byte_count") or item.get("byte_count") or 0)
    checks = {
        "version_id": response.get("VersionId") == item["version_id"],
        "etag": str(response.get("ETag") or "").strip('"') == item.get("etag"),
        "size": int(response.get("ContentLength") or 0) == expected_size,
    }
    if all(checks.values()):
        status = "HEAD_MATCH"
    elif not current:
        status = "HEAD_MISMATCH"
    elif not response.get("VersionId"):
        status = "HEAD_IDENTITY_UNCONFIRMED"
    elif not checks["version_id"]:
        status = "HEAD_CHANGED"
    else:
        status = "HEAD_INCONSISTENT"
    return {
        "status": status,
        "mode": "current" if current else "pinned",
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "key_sha256_prefix": hashlib.sha256(
            str(item["s3_key"]).encode("utf-8")).hexdigest()[:16],
        "checks": checks,
        "connect_timeout_seconds": client.meta.config.connect_timeout,
        "read_timeout_seconds": client.meta.config.read_timeout,
        "total_max_attempts": client.meta.config.retries.get("total_max_attempts"),
        "body_sha256_checked": False,
    }


def main() -> int:
    args = sys.argv[1:]
    if args in (["--worker"], ["--worker", "--current"]):
        try:
            item = _checkpoint_object()
            result = (probe_once(item, current=True) if "--current" in args
                      else probe_once(item))
        except Exception as exc:
            response = getattr(exc, "response", None) or {}
            result = {"status": "HEAD_ERROR", "error_type": type(exc).__name__,
                      "aws_error_code": (response.get("Error") or {}).get("Code")}
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0 if result["status"] in ("HEAD_MATCH", "HEAD_CHANGED") else 1
    if args not in ([], ["--current"]):
        raise SystemExit("usage: python -m scripts.probe_frontier_head [--current]")
    try:
        worker_args = [sys.executable, "-m", "scripts.probe_frontier_head", "--worker"]
        if "--current" in args:
            worker_args.append("--current")
        child = subprocess.run(
            worker_args,
            cwd=ROOT, capture_output=True, text=True,
            timeout=WALL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        print(json.dumps({"status": "WALL_TIMEOUT",
                          "wall_timeout_seconds": WALL_TIMEOUT_SECONDS}))
        return 124
    print(child.stdout.strip() or json.dumps({"status": "NO_WORKER_OUTPUT"}))
    return child.returncode if child.returncode else (0 if child.stdout.strip() else 1)


if __name__ == "__main__":
    raise SystemExit(main())
