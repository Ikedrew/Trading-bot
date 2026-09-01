"""
NEW Shadow Runtime — Persistence (single writer, append-only event stream).

Persistence contract (DATA architecture):
    - local JSONL is the source of truth
    - S3 is a fire-and-forget mirror (gated by config.EVENT_STREAM_S3_MIRROR)
    - append-only; no updates, no deletes
    - ONE writer: core/shadow/runtime.py via this module
    - every event carries schema_version + model versions

Partitioning:
    local:  {base_dir}/{SYMBOL}/{UTC-date}.jsonl
    S3:     shadow_runtime/schema_version={SV}/symbol={SYM}/date={D}/part-000.jsonl

The UTC date derives from raw market time minus persisted broker_offset_seconds
(fixes the legacy bug of interpreting broker seconds as UTC for partitioning).

PROVISIONAL LOCATION: directory is provisional (config.SHADOW_RUNTIME_DIR,
default "logs/shadow_runtime_v1"), isolated behind this module. Final
production name/location is a pending decision — legacy logs/shadow_trades/
MUST NOT be reused.

Recovery reads ONLY this domain.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from core.config import NEW_RUNTIME_S3_BUCKET
from core.production_data_contract import s3_base_prefix

_S3_BUCKET = NEW_RUNTIME_S3_BUCKET
_S3_PREFIX = s3_base_prefix("shadow_runtime")
_DEFAULT_BASE_DIR = "logs/shadow_runtime_v1"  # PROVISIONAL


def get_base_dir() -> str:
    """Resolve the provisional dataset directory (config-overridable)."""
    try:
        from core import config as _cfg

        return str(getattr(_cfg, "SHADOW_RUNTIME_DIR", _DEFAULT_BASE_DIR))
    except Exception:
        return _DEFAULT_BASE_DIR


def get_broker_offset_seconds() -> int:
    """
    Broker-server → UTC offset in seconds (positive = broker ahead).

    Measured at runtime by data.mt5_data on first tick; persisted on every
    event so UTC derivations remain reproducible forever.
    """
    try:
        from data.mt5_data import _TICK_UTC_OFFSET_SECONDS

        return int(_TICK_UTC_OFFSET_SECONDS)
    except Exception:
        return 0


class ShadowEventWriter:
    """Append-only writer for NEW Shadow events. Never raises to caller."""

    def __init__(self, base_dir: str | None = None) -> None:
        self._base_dir = base_dir or get_base_dir()

    @property
    def base_dir(self) -> str:
        return self._base_dir

    def partition_path(self, symbol: str, market_time_raw: int, broker_offset_seconds: int) -> Path:
        utc_date = datetime.fromtimestamp(
            int(market_time_raw) - int(broker_offset_seconds), tz=timezone.utc
        ).strftime("%Y-%m-%d")
        return Path(self._base_dir) / (symbol or "UNKNOWN") / f"{utc_date}.jsonl"

    def append(
        self,
        *,
        event: dict[str, Any],
        symbol: str,
        market_time_raw: int,
        broker_offset_seconds: int,
    ) -> None:
        """Append one event to the local stream (+ gated S3 mirror). Never raises."""
        try:
            path = self.partition_path(symbol, market_time_raw, broker_offset_seconds)
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(event, separators=(",", ":"), default=str) + "\n"

            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, line.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)

            self._mirror_s3(symbol, market_time_raw, broker_offset_seconds, line)
        except Exception as exc:  # persistence must never affect any caller
            logger.debug("[SHADOW_RUNTIME_PERSIST_FAIL] %s", exc)

    def _mirror_s3(
        self,
        symbol: str,
        market_time_raw: int,
        broker_offset_seconds: int,
        line: str,
    ) -> None:
        """S3 mirror. Fire-and-forget; never raises; gated by config."""
        try:
            from core import config as _cfg

            if not getattr(_cfg, "EVENT_STREAM_S3_MIRROR", False):
                return

            import boto3

            from core.shadow.models import SCHEMA_VERSION

            utc_date = datetime.fromtimestamp(
                int(market_time_raw) - int(broker_offset_seconds), tz=timezone.utc
            ).strftime("%Y-%m-%d")
            key = (
                f"{_S3_PREFIX}/schema_version={SCHEMA_VERSION}"
                f"/symbol={symbol}/date={utc_date}/part-000.jsonl"
            )
            s3 = boto3.client(
                "s3",
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                region_name=os.getenv("AWS_REGION", "eu-west-2"),
            )
            body = line
            try:
                existing = s3.get_object(Bucket=_S3_BUCKET, Key=key)
                body = existing["Body"].read().decode("utf-8") + line
            except Exception:
                pass  # new object
            s3.put_object(
                Bucket=_S3_BUCKET,
                Key=key,
                Body=body.encode("utf-8"),
                ContentType="application/x-ndjson",
            )
        except Exception:
            pass  # mirror failure must never affect runtime


def load_events(base_dir: str | None = None) -> list[dict[str, Any]]:
    """
    Load all NEW Shadow events (recovery/replay). Reads ONLY this domain.

    Ordered by file path then in-file order (chronological per symbol;
    cross-symbol order is irrelevant — state is keyed by shadow_trade_id).
    """
    root = Path(base_dir or get_base_dir())
    events: list[dict[str, Any]] = []
    if not root.exists():
        return events
    for f in sorted(root.rglob("*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # corrupt line — never fabricate, never crash
        except OSError:
            continue
    return events
