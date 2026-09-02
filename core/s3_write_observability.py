"""
S3 write observability — make fire-and-forget S3 mirror failures VISIBLE.

The canonical persistence writers mirror to S3 fire-and-forget: local JSONL is
authoritative and an S3 failure must NEVER affect trading. Previously such
failures were swallowed with a bare ``except: pass``, so a sustained S3 outage
could silently leave S3 incomplete while the bot reported healthy.

This module adds lightweight, non-blocking observability:
    - a process-wide per-dataset failure counter
    - a rate-limited WARNING on failure (so logs are not flooded)

It changes NO control flow and adds NO synchronous dependency on S3: trading is
unaffected whether S3 is up or down. Observability only.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_failures: dict[str, int] = defaultdict(int)
_successes: dict[str, int] = defaultdict(int)
_last_warn: dict[str, float] = {}

_WARN_INTERVAL_S = 60.0  # rate-limit WARNING to once per dataset per minute


def record_s3_success(dataset: str) -> None:
    with _lock:
        _successes[dataset] += 1


def record_s3_failure(dataset: str, exc: BaseException | None = None) -> None:
    """Count a failed S3 mirror write and emit a rate-limited WARNING.

    Never raises. Safe to call from a bare ``except`` block.
    """
    try:
        now = time.time()
        with _lock:
            _failures[dataset] += 1
            count = _failures[dataset]
            last = _last_warn.get(dataset, 0.0)
            should_warn = (now - last) >= _WARN_INTERVAL_S
            if should_warn:
                _last_warn[dataset] = now
        if should_warn:
            logger.warning(
                "[S3_MIRROR_FAILURE] dataset=%s total_failures=%d last_error=%s",
                dataset, count, type(exc).__name__ if exc else "unknown",
            )
    except Exception:
        pass  # observability must never raise


def s3_write_stats() -> dict[str, dict[str, int]]:
    """Snapshot of per-dataset S3 mirror success/failure counts."""
    with _lock:
        keys = set(_failures) | set(_successes)
        return {k: {"success": _successes.get(k, 0), "failure": _failures.get(k, 0)} for k in keys}
