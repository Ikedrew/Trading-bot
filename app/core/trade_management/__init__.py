"""
Layer 9 — post-entry trade lifecycle (reactive price/time only).

Entry stack must not import from here. Downstream flow:

  MT5Execution.place_market → TradeStateManager.register_from_execution
  Poll loop → TradeStateManager.on_price_update

Optional: MT5Execution.position_modify_sl_tp for server-side SL/TP sync on trails/BE.
"""

from __future__ import annotations

from core.trade_management.config import TradeManagementConfig
from core.trade_management.events import (
    NoOpTradeListener,
    TradeEvent,
    TradeLifecycleEvent,
    TradeLifecycleListener,
)
from core.trade_management.manager import TradeStateManager
from core.trade_management.position import Position, PositionStatus

__all__ = [
    "TradeManagementConfig",
    "TradeStateManager",
    "Position",
    "PositionStatus",
    "TradeEvent",
    "TradeLifecycleEvent",
    "TradeLifecycleListener",
    "NoOpTradeListener",
]
