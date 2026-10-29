"""
V10 Execution Bridge Trace — Observability for the decision-to-broker chain.

Logs structured events at each transition point between V10 pipeline EXECUTE
decision and actual broker order submission.

Events:
  EXECUTION_ATTEMPT   — V10 pipeline produced EXECUTE, entering execution bridge
  EXECUTION_BLOCKED   — Runtime guard or pre-execution check blocked the trade
  ORDER_SUBMITTED     — mt5.order_send() was called
  ORDER_FILLED        — Broker confirmed successful fill
  ORDER_FAILED        — Broker rejected the order or execution error

This module is OBSERVABILITY ONLY:
  - Does not make decisions
  - Does not modify order parameters
  - Does not affect trading logic
  - Only reads and logs state

Ownership: core/runtime/execution_trace.py
Dependencies: logging (stdlib)
Must NOT import from: strategy_engine, pipeline, risk_engine
"""

from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_LOG_PREFIX = "[EXEC_TRACE]"


def log_execution_attempt(
    *,
    symbol: str,
    correlation_id: str,
    direction: str,
    volume: float,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    strategy: str = "",
    confidence: float = 0.0,
) -> None:
    """Log when V10 pipeline produces EXECUTE and enters the execution bridge."""
    ts = datetime.now(tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    msg = (
        f"{_LOG_PREFIX} EXECUTION_ATTEMPT | {ts} | {symbol} | "
        f"cor={correlation_id[:16]} | dir={direction} | vol={volume:.2f} | "
        f"entry={entry_price:.5f} | sl={stop_loss:.5f} | tp={take_profit:.5f} | "
        f"strategy={strategy} | conf={confidence:.2f}"
    )
    print(msg)
    logger.info(
        "%s event=EXECUTION_ATTEMPT symbol=%s correlation_id=%s direction=%s "
        "volume=%.4f entry=%.5f sl=%.5f tp=%.5f strategy=%s confidence=%.2f",
        _LOG_PREFIX, symbol, correlation_id, direction,
        volume, entry_price, stop_loss, take_profit, strategy, confidence,
    )


def log_execution_blocked(
    *,
    symbol: str,
    correlation_id: str,
    blocker: str,
    reason: str,
) -> None:
    """Log when a runtime guard blocks the trade after V10 approved it."""
    ts = datetime.now(tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    msg = (
        f"{_LOG_PREFIX} EXECUTION_BLOCKED | {ts} | {symbol} | "
        f"cor={correlation_id[:16]} | blocker={blocker} | reason={reason}"
    )
    print(msg)
    logger.warning(
        "%s event=EXECUTION_BLOCKED symbol=%s correlation_id=%s blocker=%s reason=%s",
        _LOG_PREFIX, symbol, correlation_id, blocker, reason,
    )


def log_order_submitted(
    *,
    symbol: str,
    correlation_id: str,
    direction: str,
    volume: float,
    price: float,
) -> None:
    """Log when mt5.order_send() is actually called."""
    ts = datetime.now(tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    msg = (
        f"{_LOG_PREFIX} ORDER_SUBMITTED | {ts} | {symbol} | "
        f"cor={correlation_id[:16]} | dir={direction} | vol={volume:.2f} | price={price:.5f}"
    )
    print(msg)
    logger.info(
        "%s event=ORDER_SUBMITTED symbol=%s correlation_id=%s direction=%s volume=%.4f price=%.5f",
        _LOG_PREFIX, symbol, correlation_id, direction, volume, price,
    )


def log_order_filled(
    *,
    symbol: str,
    correlation_id: str,
    ticket: int = 0,
    deal: int = 0,
    fill_price: float = 0.0,
    volume: float = 0.0,
) -> None:
    """Log when broker confirms a successful fill."""
    ts = datetime.now(tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    msg = (
        f"{_LOG_PREFIX} ORDER_FILLED | {ts} | {symbol} | "
        f"cor={correlation_id[:16]} | ticket={ticket} | deal={deal} | "
        f"fill={fill_price:.5f} | vol={volume:.2f}"
    )
    print(msg)
    logger.info(
        "%s event=ORDER_FILLED symbol=%s correlation_id=%s ticket=%d deal=%d fill_price=%.5f volume=%.4f",
        _LOG_PREFIX, symbol, correlation_id, ticket, deal, fill_price, volume,
    )


def log_order_failed(
    *,
    symbol: str,
    correlation_id: str,
    error_code: int = 0,
    error_message: str = "",
    stage: str = "",
) -> None:
    """Log when broker rejects the order or execution fails."""
    ts = datetime.now(tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    msg = (
        f"{_LOG_PREFIX} ORDER_FAILED | {ts} | {symbol} | "
        f"cor={correlation_id[:16]} | stage={stage} | "
        f"error_code={error_code} | error={error_message[:100]}"
    )
    print(msg)
    logger.error(
        "%s event=ORDER_FAILED symbol=%s correlation_id=%s stage=%s error_code=%d error=%s",
        _LOG_PREFIX, symbol, correlation_id, stage, error_code, error_message[:200],
    )
