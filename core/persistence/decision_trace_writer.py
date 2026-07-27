"""
DecisionTrace Persistence Writer — local JSONL persistence for decision traces.

One trace = one JSON line. Partitioned by symbol and date.

Storage:
    Local: logs/decision_trace/{SYMBOL}/{YYYY-MM-DD}.jsonl

This module is PURELY OBSERVATIONAL persistence. It does NOT:
    - Affect trading execution
    - Gate or block decisions
    - Modify engine state
    - Interact with S3 or external systems

If persistence fails for any reason, the failure is swallowed silently.
Trading must never be affected by trace persistence.

Usage:
    from core.persistence.decision_trace_writer import persist_decision_trace

    persist_decision_trace(trace)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/decision_trace"


def persist_decision_trace(trace: Any) -> bool:
    """
    Persist a DecisionTrace object locally as JSONL.

    Requirements:
        - One trace = one JSON line
        - Never affect trading execution if persistence fails
        - Create directories automatically
        - Use existing project logging conventions

    Args:
        trace: DecisionTrace object (must have .to_dict() method)
              OR a plain dict (used directly)

    Returns:
        True on success, False on failure. Never raises.
    """
    try:
        # Serialize trace to dict
        if hasattr(trace, "to_dict"):
            record = trace.to_dict()
        elif isinstance(trace, dict):
            record = trace
        else:
            logger.debug("[DECISION_TRACE_WRITER] invalid trace type: %s", type(trace).__name__)
            return False

        # Determine partition key (symbol + date)
        symbol = record.get("symbol", "UNKNOWN")
        timestamp = record.get("timestamp_utc", "")

        if len(timestamp) >= 10:
            date_str = timestamp[:10]  # YYYY-MM-DD from ISO string
        else:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Build file path
        path = Path(_LOCAL_DIR) / symbol / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        # Serialize to JSON line
        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"

        # Atomic-safe append write with fsync
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        return True

    except Exception as exc:
        # Persistence failure must NEVER affect trading
        logger.debug("[DECISION_TRACE_WRITER] write_failed: %s", exc)
        return False


def load_decision_traces(
    *,
    symbol: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    terminal_stage: str | None = None,
    local_dir: str = _LOCAL_DIR,
) -> list[dict[str, Any]]:
    """
    Load persisted DecisionTrace records from local JSONL.

    Read-only. For offline analysis / DuckDB queries.
    Supports filtering by symbol, date range, and terminal stage.

    Args:
        symbol: Filter by symbol (None = all symbols)
        date_from: Filter by date >= this (YYYY-MM-DD)
        date_to: Filter by date <= this (YYYY-MM-DD)
        terminal_stage: Filter by terminal_stage value

    Returns:
        List of trace dicts. Empty list on error.
    """
    records: list[dict[str, Any]] = []
    path = Path(local_dir)
    if not path.exists():
        return records

    for f in sorted(path.rglob("*.jsonl")):
        # Symbol filter (directory name)
        if symbol:
            rel_parts = f.relative_to(path).parts
            if len(rel_parts) >= 1 and rel_parts[0] != symbol:
                continue

        # Date filter (filename)
        fname = f.stem  # YYYY-MM-DD
        if date_from and fname < date_from:
            continue
        if date_to and fname > date_to:
            continue

        try:
            for line in f.read_text(encoding="utf-8").strip().split("\n"):
                if not line.strip():
                    continue
                rec = json.loads(line)
                if terminal_stage and rec.get("terminal_stage") != terminal_stage:
                    continue
                records.append(rec)
        except (json.JSONDecodeError, OSError):
            continue

    return records
