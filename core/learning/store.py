"""
Learning Insights Store — persists LearningRecords and calibration reports.

Storage:
    Local:  logs/learning/{YYYY-MM-DD}.jsonl (append-only)
    S3:     s3://trading-bot-data-mk1/learning/date={YYYY-MM-DD}/

This store is WRITE-ONLY from the learning engine's perspective.
It does NOT feed back into trading decisions.

Usage:
    from core.learning.store import persist_learning_record, persist_calibration_report

    persist_learning_record(record)
    persist_calibration_report(report)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/learning"
_S3_BUCKET = "v10-engine"
_S3_PREFIX = "learning"
_SCHEMA_VERSION = "learning_v1"


def persist_learning_record(record: Any) -> None:
    """
    Persist a single LearningRecord to local JSONL.
    Fire-and-forget. Never raises. Never affects trading.
    """
    try:
        data = record.to_dict() if hasattr(record, "to_dict") else dict(record)
        data["persisted_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        data["record_type"] = "learning_record"
        data["schema_version"] = _SCHEMA_VERSION

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _write_local(date_str, data)
        _write_s3(date_str, data)
    except Exception:
        pass  # Learning persistence must never affect runtime


def persist_calibration_report(report: Any, *, report_type: str = "calibration") -> None:
    """
    Persist a calibration/evidence/uncertainty report to local JSONL.
    Fire-and-forget. Never raises. Never affects trading.
    """
    try:
        data = report.to_dict() if hasattr(report, "to_dict") else dict(report)
        data["persisted_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        data["record_type"] = report_type

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _write_local(date_str, data, subdir="reports")
        _write_s3(date_str, data, subdir="reports")
    except Exception:
        pass


def load_learning_records(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """
    Load persisted LearningRecords from local JSONL.
    Read-only. For offline analysis / human review.
    """
    records: list[dict[str, Any]] = []
    path = Path(_LOCAL_DIR)
    if not path.exists():
        return records

    for f in sorted(path.glob("*.jsonl")):
        fname = f.stem
        if date_from and fname < date_from:
            continue
        if date_to and fname > date_to:
            continue

        try:
            for line in f.read_text(encoding="utf-8").strip().split("\n"):
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("record_type") == "learning_record":
                    records.append(rec)
        except (json.JSONDecodeError, OSError):
            continue

    return records


def _write_local(date_str: str, data: dict[str, Any], *, subdir: str = "") -> None:
    """Append to local JSONL. Never raises."""
    try:
        base = Path(_LOCAL_DIR)
        if subdir:
            base = base / subdir
        base.mkdir(parents=True, exist_ok=True)
        local_path = base / f"{date_str}.jsonl"

        line = json.dumps(data, separators=(",", ":"), default=str) + "\n"
        fd = os.open(str(local_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        pass


def _write_s3(date_str: str, data: dict[str, Any], *, subdir: str = "") -> None:
    """Append to S3 partition. Fire-and-forget. Never raises."""
    try:
        from core import config as _cfg
        if not getattr(_cfg, "EVENT_STREAM_S3_MIRROR", False):
            return

        import boto3
        from botocore.config import Config as BotoConfig
        s3 = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "eu-west-2"),
            config=BotoConfig(connect_timeout=3, read_timeout=5, retries={"max_attempts": 0}),
        )

        prefix = f"{_S3_PREFIX}/{subdir}" if subdir else _S3_PREFIX
        key = f"{prefix}/date={date_str}/part-000.jsonl"
        line = json.dumps(data, separators=(",", ":"), default=str) + "\n"

        # Read-append-write
        try:
            existing = s3.get_object(Bucket=_S3_BUCKET, Key=key)
            body = existing["Body"].read().decode("utf-8") + line
        except Exception:
            body = line

        s3.put_object(
            Bucket=_S3_BUCKET, Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
    except Exception:
        pass
