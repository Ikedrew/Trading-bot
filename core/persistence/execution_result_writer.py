"""
ExecutionResult Persistence — records every broker execution attempt.

Storage:
    Local: logs/execution_results/{SYMBOL}/{YYYY-MM-DD}.jsonl
    S3:    s3://trading-bot-data-mk1/execution_results/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl

Every call to execution.execute() produces one record — regardless of success or failure.

This module is PURELY OBSERVATIONAL. It does NOT:
    - Affect trading decisions
    - Modify execution logic
    - Gate or block trades
    - Retry failed orders
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/execution_results"
from core.config import NEW_RUNTIME_S3_BUCKET

_S3_BUCKET = NEW_RUNTIME_S3_BUCKET
_S3_PREFIX = "execution_results"
_SCHEMA_VERSION = "execution_results_v1"


def persist_execution_result(
    *,
    symbol: str,
    cycle_id: int,
    result_ok: bool,
    retcode: int,
    deal: int,
    order: int,
    comment: str,
    fill_price: float | None = None,
    # OrderIntent fields (what was attempted)
    side: str = "",
    volume: float = 0.0,
    entry_reference: float = 0.0,
    sl: float = 0.0,
    tp: float = 0.0,
    pattern: str = "",
    # Linkage
    decision_id: str = "",
    correlation_id: str = "",
    entity_id: str = "",
    observation_id: str = "",
    canonical_opportunity_id: str = "",
    # Execution metadata
    decision_ts_utc_ms: int = 0,
    slippage: float = 0.0,
    # Phase 3 Step 4: execution-moment market facts (additive; 0.0 = unknown).
    # Derived from the live feed tick at the execution boundary — never invented.
    bid_at_execution: float = 0.0,
    ask_at_execution: float = 0.0,
    risk_distance: float = 0.0,
    # Protection verification (Phase 1 hardening)
    requested_sl: float = 0.0,
    broker_confirmed_sl: float = 0.0,
    requested_tp: float = 0.0,
    broker_confirmed_tp: float = 0.0,
    protection_status: str = "",
    protection_failure_reason: str = "",
) -> None:
    """
    Persist one execution attempt to local JSONL + S3.

    Fire-and-forget. Never raises. Never affects trading.
    """
    try:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")

        record = {
            "timestamp_utc": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "timestamp_unix": round(now.timestamp(), 3),
            "symbol": symbol,
            "cycle_id": cycle_id,
            # Execution result
            "result_ok": result_ok,
            "retcode": retcode,
            "deal": deal,
            "order_ticket": order,
            "comment": comment,
            "fill_price": fill_price,
            "slippage": round(slippage, 6) if slippage else 0.0,
            # What was attempted
            "side": side,
            "volume": volume,
            "entry_reference": entry_reference,
            "sl": sl,
            "tp": tp,
            "pattern": pattern,
            # Linkage
            "decision_id": decision_id,
            "correlation_id": correlation_id,
            "entity_id": entity_id,
            "observation_id": observation_id,
            "canonical_opportunity_id": canonical_opportunity_id,
            "decision_ts_utc_ms": decision_ts_utc_ms,
            # Phase 3 Step 4/7: execution-moment facts + derived planned-risk
            # geometry. This row is the ENTRY-FACTS snapshot for the trade:
            # outcome fields (pnl/MFE/MAE/exit) are structurally absent here.
            "bid_at_execution": bid_at_execution or None,
            "ask_at_execution": ask_at_execution or None,
            "spread_at_execution": round(
                ask_at_execution - bid_at_execution, 8
            ) if (ask_at_execution > 0 and bid_at_execution > 0) else None,
            "risk_distance": round(risk_distance, 8) if risk_distance else None,
            # Protection verification (Phase 1 hardening)
            "requested_sl": requested_sl,
            "broker_confirmed_sl": broker_confirmed_sl,
            "requested_tp": requested_tp,
            "broker_confirmed_tp": broker_confirmed_tp,
            "protection_status": protection_status,
            "protection_failure_reason": protection_failure_reason,
        }

        record["schema_version"] = _SCHEMA_VERSION

        # Local JSONL persistence
        path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(record, separators=(",", ":"), default=str)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        # S3 mirror
        _write_s3(symbol, date_str, line)

    except Exception:
        pass  # Execution result persistence must NEVER affect trading


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
        key = f"{_S3_PREFIX}/symbol={symbol}/date={date_str}/part-000.jsonl"
        body = line + "\n"

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
        pass
