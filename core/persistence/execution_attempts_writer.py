"""
Execution Attempts Persistence Writer — Records every individual broker execution attempt.

Persists ALL broker execution attempts, including intermediate retries (requotes, timeouts)
that were previously lost in the MT5 execution adapter's in-memory retry loop.

This dataset supplements (not replaces) the existing execution_results dataset.
The execution_results record represents the overall execution outcome per orchestrator call.
The execution_attempts records represent each individual broker interaction.

Storage:
    Local: logs/execution_attempts/{SYMBOL}/{YYYY-MM-DD}.jsonl
    S3:    s3://trading-bot-v10-data/execution_attempts/schema_version=execution_attempts_v1/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl

SCHEMA: execution_attempts_v1

This module is PURELY OBSERVATIONAL. It does NOT:
    - Affect trading decisions
    - Modify execution logic, retry policy, or broker behaviour
    - Gate or block trade execution
    - Retry or recover from failures

Design:
    - Fire-and-forget. Never raises to caller.
    - Local JSONL + fsync is canonical truth. S3 mirror is secondary.
    - One record per individual broker call.
    - Persistence failure never affects trading.

Field notes:
    - ``trade_id`` uses the same trade identity as the existing execution/outcome
      lifecycle and is **never invented here**.  For ENTRY attempts no genuine
      trade ID exists yet when ``mt5.order_send()`` is invoked — the ``pos_`` ID
      is materialised downstream by Position registration / Trade Journal — so
      ``trade_id`` remains null there.  For SLTP_MODIFY and CLOSE attempts the
      open position's identity (``pos_{deal}``) is already known by
      ``TradeStateManager`` and is propagated verbatim through the execution
      call chain; when the caller does not supply it (e.g. legacy or remote
      calls without position context) it is recorded as null.
    - ``protection_status``, ``broker_confirmed_sl``, and
      ``broker_confirmed_tp`` are left null when the broker response does not
      expose authoritative confirmation at the ``order_send()`` return point.
      The MT5 ``order_send`` result for SLTP actions carries only
      ``retcode``/``deal``/``order``/``comment`` — it does **not** echo back
      the confirmed SL/TP prices.  Submitting ``sl``/``tp`` in the request is
      not equivalent to broker confirmation.
    - ``observation_id`` is a Phase 1 data-capture identifier (bar-level
      tracing).  It is a compatibility alias in some pipeline layers and is
      NOT the canonical lineage root — that role belongs to
      ``canonical_opportunity_id``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/execution_attempts"
from core.config import NEW_RUNTIME_S3_BUCKET
from core.production_data_contract import s3_base_prefix

_S3_BUCKET = NEW_RUNTIME_S3_BUCKET
_S3_PREFIX = s3_base_prefix("execution_attempts")
_SCHEMA_VERSION = "execution_attempts_v1"



def persist_execution_attempt(
    *,
    attempt_id: str,
    decision_id: str = "",
    canonical_opportunity_id: str = "",
    observation_id: str = "",
    correlation_id: str = "",
    trade_id: str = "",
    symbol: str = "",
    cycle_id: int = 0,
    action_type: str = "",
    attempt_number: int = 1,
    retry_reason: str | None = None,
    timestamp_utc: str = "",
    timestamp_unix: float = 0.0,
    side: str = "",
    volume: float = 0.0,
    entry_reference: float = 0.0,
    requested_sl: float = 0.0,
    requested_tp: float = 0.0,
    bid_at_attempt: float = 0.0,
    ask_at_attempt: float = 0.0,
    broker_ok: bool = False,
    retcode: int = 0,
    deal: int = 0,
    order_ticket: int = 0,
    comment: str = "",
    fill_price: float | None = None,
    slippage: float | None = None,
    protection_status: str | None = None,
    broker_confirmed_sl: float | None = None,
    broker_confirmed_tp: float | None = None,
    engine: str = "V10",
) -> bool:
    """Persist one execution attempt to local JSONL + S3 mirror."""
    try:
        if not timestamp_utc:
            now = datetime.now(timezone.utc)
            timestamp_utc = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            timestamp_unix = round(now.timestamp(), 3)
        else:
            now = datetime.now(timezone.utc)

        date_str = now.strftime("%Y-%m-%d")

        spread_at_attempt = None
        if bid_at_attempt > 0 and ask_at_attempt > 0:
            spread_at_attempt = round(ask_at_attempt - bid_at_attempt, 8)

        record = {
            "schema_version": _SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "decision_id": decision_id or None,
            "canonical_opportunity_id": canonical_opportunity_id or None,
            "observation_id": observation_id or None,
            "correlation_id": correlation_id or None,
            "trade_id": trade_id or None,
            "symbol": symbol,
            "cycle_id": cycle_id,
            "action_type": action_type,
            "attempt_number": attempt_number,
            "retry_reason": retry_reason,
            "timestamp_utc": timestamp_utc,
            "timestamp_unix": timestamp_unix,
            "side": side,
            "volume": volume,
            "entry_reference": entry_reference,
            "requested_sl": requested_sl or None,
            "requested_tp": requested_tp or None,
            "bid_at_attempt": bid_at_attempt or None,
            "ask_at_attempt": ask_at_attempt or None,
            "spread_at_attempt": spread_at_attempt,
            "broker_result": {
                "ok": broker_ok,
                "retcode": retcode,
                "deal": deal,
                "order_ticket": order_ticket,
                "comment": comment,
                "fill_price": fill_price,
            },
            "slippage": slippage,
            "protection_status": protection_status,
            "broker_confirmed_sl": broker_confirmed_sl,
            "broker_confirmed_tp": broker_confirmed_tp,
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
