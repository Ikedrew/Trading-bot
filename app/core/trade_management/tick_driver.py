"""
Tick Driver — Drives tick-level position management updates.

Dispatches bid/ask price updates to TradeStateManager and drains
any pending retry queues (SL/TP modifications, close retries).

This module owns ONLY:
    - Price update dispatch to TradeStateManager
    - Retry queue draining

This module does NOT own:
    - Position state (owned by TradeStateManager)
    - SL/TP logic (owned by sl_tp_rules)
    - Trade lifecycle decisions
    - Kill switch evaluation (received as parameter)

Design: fire-and-forget, never raises, never blocks trading.
"""

from __future__ import annotations

import time
from typing import Any


def drive_tick(
    trade_manager: Any,
    symbol: str,
    bid: float,
    ask: float,
    kill_active: bool,
) -> None:
    """
    Drive tick-level position management update.

    Dispatches current bid/ask to TradeStateManager for open position
    management (break-even, trailing stop, etc.) and drains retry queues.

    Paused when kill switch is active (no modifications while halted).
    Never raises — failures are silently swallowed.

    Args:
        trade_manager: TradeStateManager instance (or None)
        symbol: Trading symbol for the price update
        bid: Current bid price
        ask: Current ask price
        kill_active: Whether kill switch is currently active
    """
    if trade_manager is None or kill_active:
        return

    try:
        trade_manager.on_price_update(symbol, bid, ask, time.time())
        trade_manager.drain_sltp_retry_queue()
        trade_manager.drain_close_retry_queue()
    except Exception:
        pass
