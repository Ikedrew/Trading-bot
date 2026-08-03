"""
Opportunity Persistence — JSONL storage for all detected opportunities.

Every opportunity is persisted regardless of outcome (executed, rejected, expired).
This creates the dataset needed for future analysis:
    - How many opportunities appear per session?
    - Which opportunities become trades?
    - Which rejected opportunities would have worked?
    - Are filters removing valuable setups?

Storage: logs/opportunities/{SYMBOL}/{YYYY-MM-DD}.jsonl

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

from core.opportunity.opportunity import Opportunity

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/opportunities"
_S3_BUCKET = "v10-engine"
_S3_PREFIX = "opportunities"
_SCHEMA_VERSION = "opportunities_v1"


def persist_opportunity(opportunity: Opportunity) -> None:
    """
    Persist an Opportunity record to local JSONL + S3 mirror.

    Fire-and-forget. Never raises. Never blocks the trading pipeline.
    Called on every state transition (DETECTED, ASSESSED, REJECTED, EXECUTED, EXPIRED).
    """
    try:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")

        path = Path(_LOCAL_DIR) / opportunity.symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        record = opportunity.to_dict()
        # Add persistence metadata
        record["_persisted_at"] = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        record["_state_at_persist"] = opportunity.state
        record["schema_version"] = _SCHEMA_VERSION

        line = json.dumps(record, separators=(",", ":"), default=str)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        # ─── S3 MIRROR (Hive-partitioned, fire-and-forget) ───────────
        try:
            _write_s3_opportunity(opportunity.symbol, date_str, line + "\n")
        except Exception:
            pass  # S3 failure must NEVER affect opportunity persistence
        # ─── END S3 MIRROR ────────────────────────────────────────────

    except Exception as exc:
        logger.error("[OPPORTUNITY_PERSIST_ERROR] symbol=%s id=%s error=%s",
                     opportunity.symbol, opportunity.opportunity_id, exc)


def persist_opportunity_batch(opportunities: list[Opportunity]) -> None:
    """
    Persist multiple opportunities efficiently (single file open per symbol/date).

    Fire-and-forget. Never raises.
    """
    if not opportunities:
        return

    try:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        persisted_at = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        # Group by symbol for efficient file writes
        by_symbol: dict[str, list[Opportunity]] = {}
        for opp in opportunities:
            by_symbol.setdefault(opp.symbol, []).append(opp)

        for symbol, opps in by_symbol.items():
            path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)

            lines: list[str] = []
            for opp in opps:
                record = opp.to_dict()
                record["_persisted_at"] = persisted_at
                record["_state_at_persist"] = opp.state
                lines.append(json.dumps(record, separators=(",", ":"), default=str))

            content = "\n".join(lines) + "\n"
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, content.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)

            # ─── S3 MIRROR (batch — one put per symbol/date) ─────────
            try:
                _write_s3_opportunity_batch(symbol, date_str, content)
            except Exception:
                pass  # S3 failure must NEVER affect opportunity persistence
            # ─── END S3 MIRROR ────────────────────────────────────────

    except Exception as exc:
        logger.error("[OPPORTUNITY_BATCH_PERSIST_ERROR] count=%d error=%s",
                     len(opportunities), exc)


# ═══════════════════════════════════════════════════════════════════════════════
# S3 MIRROR (Hive-partitioned, fire-and-forget)
# ═══════════════════════════════════════════════════════════════════════════════


def _write_s3_opportunity(symbol: str, date_str: str, line: str) -> None:
    """
    Mirror opportunity record to S3. Fire-and-forget. Never raises.

    S3 Layout (Hive-compatible, Athena-queryable):
        opportunities/schema_version=opportunities_v1/symbol={SYMBOL}/date={DATE}/part-000.jsonl

    Partition keys:
        - schema_version: enables future schema evolution
        - symbol: enables per-pair opportunity analysis
        - date: enables time-range partition pruning
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
        key = (
            f"{_S3_PREFIX}/schema_version={_SCHEMA_VERSION}"
            f"/symbol={symbol}/date={date_str}/part-000.jsonl"
        )
        body = line

        # Read-append-write (acceptable for opportunity volume)
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
        pass  # S3 failure must NEVER affect opportunity persistence


def _write_s3_opportunity_batch(symbol: str, date_str: str, content: str) -> None:
    """
    Mirror a batch of opportunity records to S3. Fire-and-forget. Never raises.

    Same S3 key format as _write_s3_opportunity — appends to existing object.
    More efficient: one S3 round-trip per symbol/date batch instead of per record.
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
        key = (
            f"{_S3_PREFIX}/schema_version={_SCHEMA_VERSION}"
            f"/symbol={symbol}/date={date_str}/part-000.jsonl"
        )
        body = content

        # Read-append-write (acceptable for opportunity volume)
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
        pass  # S3 failure must NEVER affect opportunity persistence
