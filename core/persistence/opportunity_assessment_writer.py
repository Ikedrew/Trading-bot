"""
OpportunityAssessment Persistence — Write-only, append-only JSONL + S3 mirror.

Persists every OpportunityAssessment immediately after creation,
BEFORE any policy decision can terminate the pipeline.

Storage:
    Local: logs/opportunity_assessment_log/{SYMBOL}/{YYYY-MM-DD}.jsonl
    S3:    s3://trading-bot-data-mk1/opportunity_assessment/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl

One JSON line per assessment. One assessment per market cycle per symbol.

Design:
    - Write-only (never reads back during runtime)
    - No coupling to policy, risk, or execution
    - Never modifies the assessment object
    - Failure never blocks trading
    - S3 mirror is fire-and-forget (gated by EVENT_STREAM_S3_MIRROR)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/opportunity_assessment_log"
_S3_BUCKET = "trading-bot-data-mk1"
_S3_PREFIX = "opportunity_assessment"
_SCHEMA_VERSION = "opportunity_assessment_v1"


def _write_s3(symbol: str, date_str: str, line: str) -> None:
    """
    Mirror assessment record to S3. Fire-and-forget. Never raises.
    Follows the same pattern as decision_audit.py and decision_ledger.py.
    """
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
            config=BotoConfig(
                connect_timeout=3,
                read_timeout=5,
                retries={"max_attempts": 0},
            ),
        )
        key = f"{_S3_PREFIX}/symbol={symbol}/date={date_str}/part-000.jsonl"
        body = line + "\n"

        # Read-append-write (acceptable for assessment volume)
        try:
            existing = s3.get_object(Bucket=_S3_BUCKET, Key=key)
            body = existing["Body"].read().decode("utf-8") + body
        except Exception:
            pass  # New file

        s3.put_object(
            Bucket=_S3_BUCKET, Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
    except Exception:
        pass  # S3 failure must never affect runtime


def persist_opportunity_assessment(assessment: Any) -> bool:
    """
    Persist an OpportunityAssessment to local JSONL + S3 mirror.

    Args:
        assessment: OpportunityAssessment dataclass instance (must have .to_dict())

    Returns:
        True on success, False on failure. Never raises.
    """
    if assessment is None:
        return False

    try:
        # Serialize via the assessment's own to_dict method
        record = assessment.to_dict()

        # Generate unique identifier from content
        symbol = record.get("symbol", "UNKNOWN")
        cycle_id = record.get("cycle_id", 0)
        bar_time = record.get("bar_time", 0)
        assessment_id = f"{symbol}_{bar_time}_{cycle_id}"
        record["assessment_id"] = assessment_id

        # Add persistence timestamp
        record["persisted_at_utc"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z"

        # Determine file path (partitioned by symbol + date)
        if bar_time > 0:
            date_str = datetime.fromtimestamp(bar_time, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        local_path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
        local_path.parent.mkdir(parents=True, exist_ok=True)

        record["schema_version"] = _SCHEMA_VERSION

        # Local JSONL persistence (source of truth)
        line = json.dumps(record, separators=(",", ":"), default=str)
        fd = os.open(str(local_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        # S3 mirror (fire-and-forget durability)
        _write_s3(symbol, date_str, line)

        return True

    except Exception as exc:
        logger.debug("[OPPORTUNITY_ASSESSMENT_PERSIST] failed: %s", exc)
        return False
