"""
Assessment Persistence — Local JSONL + S3 mirror for assessment records.

Every assessment is persisted regardless of trade outcome.
This enables research into:
    - Which assessment factors predict profitability?
    - Which high-confidence assessments were rejected and why?
    - Which assessment dimensions have the greatest predictive power?

Storage:
    Local:  logs/assessments/{SYMBOL}/{YYYY-MM-DD}.jsonl
    S3:     s3://trading-bot-data-mk1/assessments/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl

This module is PURELY OBSERVATIONAL. It does NOT:
    - Affect trading decisions
    - Block or gate execution
    - Modify any pipeline behaviour
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.assessment.assessment import Assessment

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/assessments"
from core.config import NEW_RUNTIME_S3_BUCKET
from core.production_data_contract import s3_base_prefix

_S3_BUCKET = NEW_RUNTIME_S3_BUCKET
_S3_PREFIX = s3_base_prefix("assessments")
_SCHEMA_VERSION = "assessments_v1"


def persist_assessment(assessment: Assessment) -> None:
    """
    Persist an Assessment record to local JSONL + S3 mirror.

    Fire-and-forget. Never raises. Never blocks the trading pipeline.
    """
    try:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")

        # ─── LOCAL PERSISTENCE ────────────────────────────────────────
        path = Path(_LOCAL_DIR) / assessment.symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        record = assessment.to_dict()
        if "schema_version" not in record:
            record["schema_version"] = _SCHEMA_VERSION
        line = json.dumps(record, separators=(",", ":"), default=str)

        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        # ─── S3 MIRROR ───────────────────────────────────────────────
        _write_s3(assessment.symbol, date_str, line)

    except Exception as exc:
        logger.error("[ASSESSMENT_PERSIST_ERROR] symbol=%s id=%s error=%s",
                     assessment.symbol, assessment.assessment_id, exc)


def _write_s3(symbol: str, date_str: str, line: str) -> None:
    """
    Mirror a single assessment line to S3. Fire-and-forget.

    Pattern matches decision_ledger.py and execution_context.py.
    Never raises. Never blocks runtime.
    """
    try:
        from core import config
        if not getattr(config, "EVENT_STREAM_S3_MIRROR", False):
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
