"""
Management Actions Persistence Writer — Records every trade-management action initiated by the trade-management layer.

This is a STANDALONE research dataset for trade-management actions:

    SLTP_MODIFY    — SL/TP modification pushed to the broker
    PARTIAL_CLOSE  — partial position close sent to the broker
    CLOSE          — full position close sent to the broker

One record is persisted at the moment the management layer INITIATES the
action (before the broker call), so broker rejections/failures still leave a
management-action record. This dataset supplements (not replaces) the existing
``execution_attempts`` (one record per broker ``order_send()`` call) and
``execution_results`` (one record per orchestrator execution) datasets.

Storage:
    Local: logs/management_actions/{SYMBOL}/{YYYY-MM-DD}.jsonl
    S3:    s3://trading-bot-v10-data/management_actions/schema_version=management_actions_v1/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl

SCHEMA: management_actions_v1

This module is PURELY OBSERVATIONAL. It does NOT:
    - Affect trade-management decisions or position state transitions
    - Modify SL/TP calculations, close-volume calculations, or retry behaviour
    - Gate, block, retry, or alter any broker request or execution behaviour

Design:
    - Fire-and-forget. Never raises to caller.
    - Local JSONL + fsync is canonical truth. S3 mirror is secondary.
    - One record per separately initiated management action (retries each get
      their own record — they are never collapsed).
    - Persistence failure never affects trading.

Field notes (NO invented research values):
    - ``trade_id`` is propagated verbatim from the position identity kept by
      ``TradeStateManager`` (``pos_{deal}``) — the SAME trade identity used by
      the existing execution/outcome lifecycle. It is never invented here.
    - ``decision_id`` / ``canonical_opportunity_id`` / ``observation_id`` /
      ``correlation_id`` / ``cycle_id`` are propagated verbatim from the
      position's ``TradeIdentity`` (or the retry entry). When the management
      layer genuinely has no value (e.g. recovered positions without
      identity), the field is persisted as ``null`` — no IDs are fabricated.
    - ``requested_sl`` / ``requested_tp`` carry the SL/TP values the
      management layer is requesting for SLTP_MODIFY actions. For CLOSE /
      PARTIAL_CLOSE actions no SL/TP is part of the request, so they remain
      ``null`` (position SL/TP are NOT mislabelled as requested values).
    - ``requested_volume`` carries the close volume for PARTIAL_CLOSE actions
      and is ``null`` for CLOSE and SLTP_MODIFY actions.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/management_actions"
from core.config import NEW_RUNTIME_S3_BUCKET

_S3_BUCKET = NEW_RUNTIME_S3_BUCKET
_S3_PREFIX = "management_actions"
_SCHEMA_VERSION = "management_actions_v1"


def persist_management_action(
    *,
    management_action_id: str,
    trade_id: str = "",
    decision_id: str = "",
    canonical_opportunity_id: str = "",
    observation_id: str = "",
    correlation_id: str = "",
    cycle_id: int = 0,
    symbol: str = "",
    action_type: str = "",
    action_reason: str = "",
    requested_sl: float | None = None,
    requested_tp: float | None = None,
    requested_volume: float | None = None,
    timestamp_utc: str = "",
    timestamp_unix: float = 0.0,
    engine: str = "V10",
) -> bool:
    """Persist one management action to local JSONL + S3 mirror.

    Fire-and-forget: returns False on any failure, never raises.
    """
    try:
        if not timestamp_utc:
            now = datetime.now(timezone.utc)
            timestamp_utc = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            timestamp_unix = round(now.timestamp(), 3)
        else:
            now = datetime.now(timezone.utc)

        date_str = now.strftime("%Y-%m-%d")

        record = {
            "schema_version": _SCHEMA_VERSION,
            "management_action_id": management_action_id,
            "trade_id": trade_id or None,
            "decision_id": decision_id or None,
            "canonical_opportunity_id": canonical_opportunity_id or None,
            "observation_id": observation_id or None,
            "correlation_id": correlation_id or None,
            "cycle_id": cycle_id,
            "symbol": symbol,
            "action_type": action_type,
            "action_reason": action_reason or None,
            "requested_sl": requested_sl,
            "requested_tp": requested_tp,
            "requested_volume": requested_volume,
            "timestamp_utc": timestamp_utc,
            "timestamp_unix": timestamp_unix,
            "engine": engine,
        }

        path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(record, separators=(",", ":"), default=str)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        _write_s3(symbol, date_str, line)
        return True
    except Exception:
        return False


def _write_s3(symbol: str, date_str: str, line: str) -> None:
    """Mirror to S3. Fire-and-forget. Never raises."""
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
            f"{_S3_PREFIX}/"
            f"schema_version={_SCHEMA_VERSION}/"
            f"symbol={symbol}/"
            f"date={date_str}/"
            f"part-000.jsonl"
        )
        body = line + "\n"

        try:
            existing = s3.get_object(Bucket=_S3_BUCKET, Key=key)
            body = existing["Body"].read().decode("utf-8") + body
        except Exception:
            pass

        s3.put_object(
            Bucket=_S3_BUCKET, Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
    except Exception:
        pass
