"""Lifecycle events — observability and hooks; no strategy content."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from core.trade_management.position import Position


class TradeLifecycleEvent(str, Enum):
    ON_TRADE_OPEN = "on_trade_open"
    ON_PRICE_UPDATE = "on_price_update"
    ON_PARTIAL_CLOSE = "on_partial_close"
    ON_TRADE_CLOSE = "on_trade_close"
    ON_STOP_LOSS_HIT = "on_stop_loss_hit"
    ON_TAKE_PROFIT_HIT = "on_take_profit_hit"
    #: Local management exit (time stop, etc.), not broker taxonomy.
    ON_MANAGEMENT_EXIT = "on_management_exit"


@dataclass(frozen=True)
class TradeEvent:
    kind: TradeLifecycleEvent
    position: Position
    price_snapshot: tuple[float, float]  # bid, ask
    time_s: float
    detail: dict[str, Any]


class TradeLifecycleListener(Protocol):
    def on_trade_event(self, event: TradeEvent) -> None: ...


class NoOpTradeListener:
    def on_trade_event(self, event: TradeEvent) -> None:
        return
