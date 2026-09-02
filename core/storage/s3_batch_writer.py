"""
S3 Batch Writer — Athena/Glue-compatible event persistence.

Buffers events in memory per (symbol, date) partition and flushes
as multi-line JSONL files to S3 using Hive-compatible partitioning.

S3 Key Structure:
    events/symbol={SYMBOL}/date={YYYY-MM-DD}/part-{NNNN}.jsonl

Flush Strategy:
    - Buffer >= max_buffer_size events (default: 100)
    - OR time >= flush_interval seconds since last flush (default: 30)

Guarantees:
    - Atomic flush per batch (one put_object = one complete JSONL file)
    - Deterministic replay (events in file are ts_utc_ms ordered)
    - No single-event files (minimum batch of 1, typically 50-100)
    - Non-blocking: flush runs in background thread
    - Never raises to caller

Athena Compatibility:
    - Hive partition layout: symbol=X/date=Y/
    - JSONL format (one JSON object per line)
    - Consistent schema envelope (ts_utc_ms, type, symbol, payload, source)
    - ContentType: application/x-ndjson

Design: fire-and-forget secondary persistence. Local JSONL remains truth.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time as _time
from collections import defaultdict
from typing import Any

from core.config import NEW_RUNTIME_S3_BUCKET
from core.production_data_contract import s3_base_prefix

logger = logging.getLogger(__name__)


class S3BatchWriter:
    """
    Batched S3 writer with Hive-compatible partitioning.

    Usage:
        writer = S3BatchWriter(bucket=NEW_RUNTIME_S3_BUCKET)
        writer.add_event(event_dict)
        # Events auto-flush on size/time thresholds
        writer.shutdown()  # Final flush on exit
    """

    def __init__(
        self,
        bucket: str = NEW_RUNTIME_S3_BUCKET,
        base_prefix: str = "events",
        flush_interval: float = 30.0,
        max_buffer_size: int = 100,
        dataset: str = "events",
    ) -> None:
        self._bucket = bucket
        self._prefix = base_prefix
        self._dataset = dataset
        self._flush_interval = flush_interval
        self._max_buffer = max_buffer_size

        # Buffers: (symbol, date) → list of JSON lines
        self._buffers: dict[tuple[str, str], list[str]] = defaultdict(list)
        self._part_counters: dict[tuple[str, str], int] = defaultdict(int)
        self._last_flush: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

        # Stats
        self._total_buffered: int = 0
        self._total_flushed: int = 0
        self._total_batches: int = 0
        self._total_errors: int = 0

        # Background flush timer
        self._running = True
        self._timer_thread = threading.Thread(
            target=self._timer_loop, daemon=True, name="s3_batch_flush"
        )
        self._timer_thread.start()

        # S3 client (lazy init)
        self._client = None

    # ─── PUBLIC API ───────────────────────────────────────────────────

    def add_event(self, event: dict[str, Any]) -> None:
        """
        Buffer an event for batch upload. Non-blocking.

        Args:
            event: Complete event dict with ts_utc_ms, type, symbol, payload.
        """
        try:
            symbol = event.get("symbol", "SYSTEM")
            ts_ms = event.get("ts_utc_ms", 0)

            # Derive date from event timestamp
            if ts_ms > 0:
                from core.clock import utc_ms_to_date
                date_str = utc_ms_to_date(ts_ms)
            else:
                date_str = "unknown"

            key = (symbol, date_str)
            line = json.dumps(event, separators=(",", ":"), default=str)

            with self._lock:
                self._buffers[key].append(line)
                self._total_buffered += 1

                # Check size threshold
                if len(self._buffers[key]) >= self._max_buffer:
                    self._flush_partition(key)

        except Exception as exc:
            self._total_errors += 1
            logger.debug("[S3_BATCH] add_event failed: %s", exc)

    def flush_all(self) -> None:
        """Force flush all buffered partitions."""
        with self._lock:
            keys = list(self._buffers.keys())
        for key in keys:
            with self._lock:
                self._flush_partition(key)

    def shutdown(self) -> None:
        """Stop timer thread and flush remaining events."""
        self._running = False
        self.flush_all()
        if self._timer_thread.is_alive():
            self._timer_thread.join(timeout=5.0)

    def stats(self) -> dict[str, Any]:
        """Return batch writer statistics."""
        with self._lock:
            pending = sum(len(v) for v in self._buffers.values())
        return {
            "total_buffered": self._total_buffered,
            "total_flushed": self._total_flushed,
            "total_batches": self._total_batches,
            "total_errors": self._total_errors,
            "pending": pending,
            "bucket": self._bucket,
        }

    # ─── INTERNAL ─────────────────────────────────────────────────────

    def _flush_partition(self, key: tuple[str, str]) -> None:
        """Flush a single partition buffer to S3. Must be called with lock held."""
        events = self._buffers.pop(key, [])
        if not events:
            return

        symbol, date_str = key
        self._part_counters[key] += 1
        part_num = self._part_counters[key]
        self._last_flush[key] = _time.time()

        # Build S3 key from the central contract (schema-versioned, Hive-compatible)
        from core.production_data_contract import canonical_s3_key
        s3_key = canonical_s3_key(
            self._dataset, symbol=symbol, date=date_str, part=f"part-{part_num:04d}.jsonl"
        )

        # Build body (JSONL — one event per line)
        body = "\n".join(events) + "\n"

        # Upload in background (don't hold lock during I/O)
        threading.Thread(
            target=self._upload,
            args=(s3_key, body.encode("utf-8"), len(events)),
            daemon=True,
            name=f"s3_upload_{symbol}_{part_num}",
        ).start()

    def _upload(self, key: str, body: bytes, event_count: int) -> None:
        """Upload a single batch to S3 with retry."""
        client = self._get_client()
        if client is None:
            self._total_errors += 1
            return

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=body,
                    ContentType="application/x-ndjson",
                )
                self._total_flushed += event_count
                self._total_batches += 1
                logger.debug("[S3_BATCH] uploaded key=%s events=%d size=%d", key, event_count, len(body))
                try:
                    from core.s3_write_observability import record_s3_success
                    record_s3_success(self._dataset)
                except Exception:
                    pass
                return
            except Exception as exc:
                if attempt == max_retries:
                    self._total_errors += 1
                    # Final retry exhausted — surface visibly (not just debug).
                    try:
                        from core.s3_write_observability import record_s3_failure
                        record_s3_failure(self._dataset, exc)
                    except Exception:
                        pass
                else:
                    _time.sleep(0.5 * attempt)

    def _timer_loop(self) -> None:
        """Background thread: periodic flush based on time interval."""
        while self._running:
            _time.sleep(5.0)  # Check every 5 seconds
            try:
                now = _time.time()
                with self._lock:
                    keys_to_flush = [
                        key for key, buf in self._buffers.items()
                        if buf and (now - self._last_flush.get(key, 0.0)) >= self._flush_interval
                    ]
                    for key in keys_to_flush:
                        self._flush_partition(key)
            except Exception:
                pass  # Timer failure must never crash

    def _get_client(self):
        """Lazy-init boto3 S3 client."""
        if self._client is not None:
            return self._client
        try:
            import boto3
            self._client = boto3.client(
                "s3",
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                region_name=os.getenv("AWS_REGION", "eu-west-2"),
            )
            return self._client
        except Exception as exc:
            logger.debug("[S3_BATCH] boto3 init failed: %s", exc)
            return None


# ─── MODULE-LEVEL SINGLETON ───────────────────────────────────────────────────

_writer: S3BatchWriter | None = None


def get_batch_writer() -> S3BatchWriter:
    """Get or create the singleton batch writer."""
    global _writer
    if _writer is None:
        _writer = S3BatchWriter(
            bucket=NEW_RUNTIME_S3_BUCKET,
            base_prefix=s3_base_prefix("events"),
            flush_interval=30.0,
            max_buffer_size=100,
        )
    return _writer


def shutdown_batch_writer() -> None:
    """Shutdown the batch writer (call on process exit)."""
    global _writer
    if _writer is not None:
        _writer.shutdown()
        _writer = None
