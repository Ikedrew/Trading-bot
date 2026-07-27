"""
MT5 Timestamp Normalization — Single authority for broker time → UTC conversion.

Pepperstone MT5 server uses UTC+3 (EET/EEST — Eastern European Time).
All MT5 timestamps (deal.time, position.time, tick.time) are in server-local time.
Python time.time() is UTC.

This module provides the SINGLE conversion point to prevent timezone mixing.

RULE: Every MT5 timestamp MUST pass through normalize_mt5_timestamp() before
being stored in any persistence layer.

Usage:
    from core.mt5_timestamp import normalize_mt5_timestamp
    utc_time = normalize_mt5_timestamp(deal.time)
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Pepperstone MT5 server timezone offset from UTC (in seconds).
# Server is UTC+3 (EET summer time). Deals, positions, ticks all use this.
_MT5_SERVER_UTC_OFFSET_SECONDS: int = 3 * 3600  # +3 hours


def normalize_mt5_timestamp(broker_time: float | int) -> float:
    """
    Convert an MT5 broker server timestamp to UTC.

    MT5 timestamps (from deals, positions, ticks) are in server-local time (UTC+3).
    This function subtracts the server offset to produce UTC epoch seconds.

    Args:
        broker_time: Unix timestamp from MT5 (server-local, UTC+3)

    Returns:
        Unix timestamp in UTC (float)

    Example:
        # MT5 deal shows time=1784752774 (broker says 20:39:34 server time)
        # Actual UTC = 17:39:34
        utc = normalize_mt5_timestamp(1784752774)
        # utc = 1784741974 (17:39:34 UTC)
    """
    if broker_time <= 0:
        return 0.0
    return float(broker_time) - _MT5_SERVER_UTC_OFFSET_SECONDS


def get_server_offset_seconds() -> int:
    """Return the configured MT5 server UTC offset in seconds."""
    return _MT5_SERVER_UTC_OFFSET_SECONDS
