"""
Market Context Persistence — JSONL + S3 writer.

Persists MarketContext on material change only.
Follows existing patterns: local JSONL + S3 mirror gated by EVENT_STREAM_S3_MIRROR.
Never raises. Never affects trading.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/market_context"
_S3_BUCKET = "v10-engine"
_S3_PREFIX = "market_context"
_SCHEMA_VERSION = "market_context_v2"


class MarketContextPersistence:
    """Writes MarketContext records to local JSONL and optional S3 mirror."""

    def persist(self, context_dict: dict[str, Any], *, entity_id: str = "",
                correlation_id: str = "", bar_time=None) -> None:
        """Persist a serialized MarketContext record. Never raises.

        Phase 3 Step 2 — identity enrichment lifecycle timing:
            MarketContext is captured pre-opportunity-qualification, so the
            canonical opportunity root legitimately does NOT exist yet and is
            never fabricated here. Observation-level identity is attached when
            the caller supplies it:
                entity_id   {SYMBOL}_{int(bar_time)} (deterministic, no mint)
                bar_time    closed-bar epoch seconds of the snapshot
                correlation_id  cycle correlation string when known
        Downstream join to the canonical root happens through assessment /
        decision rows carrying both this entity_id and the root.
        """
        try:
            symbol = context_dict.get("symbol", "UNKNOWN")
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            # Observation-level identity — set only with real values (rule:
            # never fabricate lineage). Empty strings stay empty otherwise.
            if bar_time not in (None, "", 0):
                context_dict.setdefault("bar_time", int(bar_time))
                if not entity_id and symbol != "UNKNOWN":
                    context_dict.setdefault(
                        "entity_id", f"{symbol}_{int(bar_time)}"
                    )
            if entity_id:
                context_dict.setdefault("entity_id", entity_id)
            if correlation_id:
                context_dict.setdefault("correlation_id", correlation_id)

            context_dict["schema_version"] = _SCHEMA_VERSION

            # Local JSONL
            local_path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
            local_path.parent.mkdir(parents=True, exist_ok=True)

            line = json.dumps(context_dict, separators=(",", ":"), default=str) + "\n"
            fd = os.open(str(local_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, line.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)

            # S3 mirror (fire-and-forget)
            try:
                from core import config as _cfg
                if getattr(_cfg, "EVENT_STREAM_S3_MIRROR", False):
                    self._s3_append(symbol, date_str, line)
            except Exception:
                pass

        except Exception as exc:
            logger.debug("[MARKET_CONTEXT_PERSIST_FAIL] %s", exc)

    def _s3_append(self, symbol: str, date_str: str, line: str) -> None:
        """Append line to S3. Never raises."""
        try:
            import boto3
            s3 = boto3.client(
                "s3",
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                region_name=os.getenv("AWS_REGION", "eu-west-2"),
            )
            key = f"{_S3_PREFIX}/schema_version={_SCHEMA_VERSION}/symbol={symbol}/date={date_str}/part-000.jsonl"

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
            pass  # S3 failure must never affect runtime
