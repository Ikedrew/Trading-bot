"""Pre-trade risk gates (open exposure) — uses MT5, no order placement.

Fail-closed: if position state is unknown, assume maximum exposure to prevent over-leverage.
"""

from __future__ import annotations

import logging

import MetaTrader5 as mt5

from core import config
from core.mt5_timeout import mt5_call

logger = logging.getLogger(__name__)

# Rejection reason for structured observability
REJECT_POSITION_STATE_UNKNOWN = "POSITION_STATE_UNKNOWN"

# Conservative fallback: assume this many positions exist when MT5 lookup fails.
# Must be >= MAX_OPEN_POSITIONS to block new trades.
_FAIL_CLOSED_POSITION_COUNT = 999

# Metrics counter for guard failures
_guard_failure_count: int = 0


def get_guard_failure_count() -> int:
    """Return total MT5 position lookup failures since process start."""
    return _guard_failure_count


def count_bot_positions(symbol: str, magic: int) -> int:
    """
    Count open positions for this symbol + magic number.

    Fail-closed behaviour (STRICT_EXPOSURE_GUARDS=True, default):
        If MT5 positions_get() returns None (disconnect, API failure, terminal freeze),
        returns _FAIL_CLOSED_POSITION_COUNT to block new trade entries.

    Lenient behaviour (STRICT_EXPOSURE_GUARDS=False):
        Logs warning but returns 0 (legacy optimistic behaviour).
        NOT recommended for production.
    """
    global _guard_failure_count
    strict = getattr(config, "STRICT_EXPOSURE_GUARDS", True)

    try:
        rows = mt5_call(mt5.positions_get, symbol=symbol)
    except Exception as exc:
        _guard_failure_count += 1
        logger.error(
            "[GUARD_REJECTED] reason=%s symbol=%s magic=%d metadata={\"error\": \"%s\"}",
            REJECT_POSITION_STATE_UNKNOWN, symbol, magic, exc,
        )
        return _FAIL_CLOSED_POSITION_COUNT if strict else 0

    if rows is None:
        # MT5 returned None — connection state unknown.
        _guard_failure_count += 1
        last_err = mt5.last_error()
        logger.warning(
            "[GUARD_REJECTED] reason=%s symbol=%s magic=%d metadata={\"mt5_last_error\": \"%s\"}",
            REJECT_POSITION_STATE_UNKNOWN, symbol, magic, last_err,
        )
        return _FAIL_CLOSED_POSITION_COUNT if strict else 0

    # Empty tuple/list = genuinely no positions (valid response)
    if not rows:
        return 0

    return sum(1 for p in rows if int(p.magic) == magic)
