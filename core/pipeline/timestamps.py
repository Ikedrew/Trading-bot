"""
Timestamp Utility — Unified tri-timestamp generation for forensic logging.

Produces three aligned timestamps per log event:
    1. UTC (system logic truth)
    2. Local (human debugging layer)
    3. MT5 (market alignment truth)

These are LOGGING-ONLY. They do NOT influence:
    - Scoring, FSM, TLSM, EV, or execution logic
    - Any decision-making process

CANONICAL CLOCK: All event ordering uses core.clock.utc_ms().
This module provides DISPLAY formatting only.

Design: deterministic, passive, no side effects.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.clock import utc_ms, utc_ms_to_iso


def get_timestamps(mt5_time: float | int | None = None) -> dict[str, str]:
    """
    Generate unified timestamp structure for a log event.

    Args:
        mt5_time: Optional MT5 server time as unix timestamp.
                  If None, MT5 field shows "unavailable".

    Returns:
        {
            "timestamp_utc": "2026-06-24T12:03:15.123Z",
            "timestamp_local": "2026-06-24T14:03:15.123+02:00",
            "timestamp_mt5": "2026-06-24T14:03:15",
            "mt5_vs_utc_offset_seconds": 7200,
        }
    """
    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now().astimezone()

    ts_utc = now_utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now_utc.microsecond // 1000:03d}Z"
    ts_local = now_local.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now_local.microsecond // 1000:03d}" + now_local.strftime("%z")
    # Format timezone offset with colon: +0200 → +02:00
    if len(ts_local) > 5 and ts_local[-5] in ("+", "-"):
        ts_local = ts_local[:-2] + ":" + ts_local[-2:]

    ts_mt5 = "unavailable"
    offset_seconds: int | None = None

    if mt5_time is not None:
        try:
            mt5_dt = datetime.fromtimestamp(float(mt5_time), tz=timezone.utc)
            ts_mt5 = mt5_dt.strftime("%Y-%m-%dT%H:%M:%S")
            offset_seconds = int(float(mt5_time) - now_utc.timestamp())
        except (ValueError, OSError, OverflowError):
            ts_mt5 = "invalid"

    result = {
        "ts_utc_ms": utc_ms(),
        "timestamp_utc": ts_utc,
        "timestamp_local": ts_local,
        "timestamp_mt5": ts_mt5,
    }
    if offset_seconds is not None:
        result["mt5_vs_utc_offset_seconds"] = offset_seconds

    return result


def format_timestamp_line(mt5_time: float | int | None = None) -> str:
    """
    Generate compact one-line timestamp string for Discord messages.

    Returns: "⏱ UTC: 12:03:15Z | MT5: 14:03:15 | Local: 14:03:15+02:00"
    """
    ts = get_timestamps(mt5_time)
    utc_short = ts["timestamp_utc"][11:19]  # HH:MM:SS
    local_short = ts["timestamp_local"][11:22]  # HH:MM:SS+offset
    mt5_short = ts["timestamp_mt5"][11:19] if ts["timestamp_mt5"] not in ("unavailable", "invalid") else "N/A"

    return f"⏱ UTC: {utc_short}Z | MT5: {mt5_short} | Local: {local_short}"
