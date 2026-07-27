"""
MT5 connection health, reconnect state machine, and position reconciliation.

Extracted from loop.py — single responsibility: MT5 infrastructure resilience.
"""

from __future__ import annotations

import logging
import time
from typing import Any, TYPE_CHECKING

import MetaTrader5 as mt5

from core import config
from core.mt5_timeout import mt5_call
from core.trade_management.position import PositionStatus

if TYPE_CHECKING:
    from core.trade_management import TradeStateManager
    from core.trade_management.position import Position

logger = logging.getLogger(__name__)

# ─── RECONNECT STATE CONSTANTS ────────────────────────────────────────────────
MT5_CONNECTED = "CONNECTED"
MT5_DISCONNECTED = "DISCONNECTED"
MT5_RECONNECTING = "RECONNECTING"


# ─── HEALTH CHECK ─────────────────────────────────────────────────────────────

def is_mt5_healthy() -> bool:
    """
    Lightweight MT5 connection health probe.
    Returns True if terminal is accessible and connected.
    Never raises — catches all failures internally.
    """
    try:
        info = mt5_call(mt5.terminal_info, timeout=5.0)
        if info is None:
            logger.info("[MT5_HEALTH] terminal_info returned None — terminal unavailable")
            return False
        if not info.connected:
            logger.info("[MT5_HEALTH] terminal reports disconnected")
            return False
        return True
    except Exception as exc:
        logger.info("[MT5_HEALTH] probe failed: %s", exc)
        return False


# ─── RECONNECT ────────────────────────────────────────────────────────────────

def attempt_reconnect(symbol: str) -> bool:
    """
    Attempt MT5 reconnect: shutdown → initialize → health check → symbol restore.
    Returns True on success, False on failure. Never raises.
    """
    try:
        mt5.shutdown()
        if not mt5.initialize(path=getattr(config, "MT5_TERMINAL_PATH", "")):
            if not mt5.initialize():
                logger.info("[MT5_STATE] RECONNECT FAILED — initialize returned False")
                return False
        if not is_mt5_healthy():
            logger.info("[MT5_STATE] RECONNECT FAILED — post-init health check failed")
            return False
        # Re-select symbol
        if not mt5.symbol_select(symbol, True):
            logger.info("[MT5_STATE] RECONNECT FAILED — symbol_select failed for %s", symbol)
            return False
        return True
    except Exception as exc:
        logger.info("[MT5_STATE] RECONNECT FAILED — exception: %s", exc)
        return False


# ─── POSITION RESYNC ──────────────────────────────────────────────────────────

def resync_positions(trade_manager: "TradeStateManager | None", symbol: str, magic: int) -> None:
    """
    Post-reconnect reconciliation: align internal TradeStateManager state with MT5 broker truth.
    READ-ONLY — does not modify broker state. Never raises.
    """
    if trade_manager is None:
        return

    try:
        start_t = time.time()
        logger.info("[MT5_RESYNC_START] symbol=%s", symbol)

        # Fetch broker positions for this symbol + magic
        broker_positions = mt5_call(mt5.positions_get, symbol=symbol)
        if broker_positions is None:
            broker_positions = []

        # Filter to our magic number
        broker_by_ticket: dict[int, Any] = {}
        for bp in broker_positions:
            if int(bp.magic) == magic:
                broker_by_ticket[int(bp.ticket)] = bp

        # Get internal positions (open/partial only)
        internal_open = trade_manager.positions_open()
        internal_by_ticket: dict[int, "Position"] = {}
        for pos in internal_open:
            if pos.mt5_ticket is not None and pos.mt5_ticket > 0:
                internal_by_ticket[pos.mt5_ticket] = pos

        # Case A: Position in MT5 but not internal → log (cannot create without full intent data)
        for ticket, bp in broker_by_ticket.items():
            if ticket not in internal_by_ticket:
                logger.info(
                    "[MT5_RESYNC_MISSING_INTERNAL] ticket=%d symbol=%s volume=%.2f sl=%.5f tp=%.5f",
                    ticket, bp.symbol, bp.volume, bp.sl, bp.tp,
                )

        # Case B: Position internal but not in MT5 → mark as closed
        for ticket, pos in internal_by_ticket.items():
            if ticket not in broker_by_ticket:
                logger.info(
                    "[MT5_RESYNC_ORPHAN_INTERNAL] ticket=%d symbol=%s position_id=%s — marking CLOSED",
                    ticket, pos.symbol, pos.position_id,
                )
                pos.status = PositionStatus.CLOSED
                pos.closed_time = time.time()

        # Case C: Both exist but SL/TP differs → update internal to match broker
        for ticket in set(internal_by_ticket) & set(broker_by_ticket):
            pos = internal_by_ticket[ticket]
            bp = broker_by_ticket[ticket]
            broker_sl = float(bp.sl)
            broker_tp = float(bp.tp)
            if abs(pos.stop_loss - broker_sl) > 1e-8 or abs(pos.take_profit - broker_tp) > 1e-8:
                logger.info(
                    "[MT5_RESYNC_SLTP_MISMATCH] ticket=%d internal_sl=%.5f broker_sl=%.5f internal_tp=%.5f broker_tp=%.5f",
                    ticket, pos.stop_loss, broker_sl, pos.take_profit, broker_tp,
                )
                pos.stop_loss = broker_sl
                pos.take_profit = broker_tp

        duration = time.time() - start_t
        total = len(broker_by_ticket)
        logger.info("[MT5_RESYNC_COMPLETE] total_broker_positions=%d duration=%.2fs", total, duration)

    except Exception as exc:
        logger.info("[MT5_RESYNC] failed — exception: %s", exc)


# ─── STATE RECONCILIATION ─────────────────────────────────────────────────────

def reconcile_state_sanity(trade_manager: "TradeStateManager | None", symbol: str, magic: int) -> dict:
    """
    Lightweight read-only sanity check: detect orphans and duplicates between
    internal TradeStateManager and MT5 broker positions. Never modifies state.
    Never raises. Returns summary dict.
    """
    result = {
        "internal_count": 0,
        "broker_count": 0,
        "orphans_internal": [],
        "orphans_broker": [],
        "duplicates": [],
    }

    if trade_manager is None:
        return result

    try:
        # Internal positions (open/partial only)
        internal_open = trade_manager.positions_open()
        result["internal_count"] = len(internal_open)
        internal_tickets: dict[int, str] = {}
        for pos in internal_open:
            if pos.mt5_ticket is not None and pos.mt5_ticket > 0:
                if pos.mt5_ticket in internal_tickets:
                    result["duplicates"].append(f"internal_dup_ticket={pos.mt5_ticket}")
                    logger.info("[STATE_RECON_DUPLICATE] internal ticket=%d appears multiple times", pos.mt5_ticket)
                internal_tickets[pos.mt5_ticket] = pos.position_id

        # Broker positions
        broker_positions = mt5_call(mt5.positions_get, symbol=symbol)
        if broker_positions is None:
            broker_positions = []
        broker_tickets: set[int] = set()
        for bp in broker_positions:
            if int(bp.magic) == magic:
                ticket = int(bp.ticket)
                if ticket in broker_tickets:
                    result["duplicates"].append(f"broker_dup_ticket={ticket}")
                    logger.info("[STATE_RECON_DUPLICATE] broker ticket=%d appears multiple times", ticket)
                broker_tickets.add(ticket)
        result["broker_count"] = len(broker_tickets)

        # A — Internal orphan (internal exists, broker missing)
        for ticket, pid in internal_tickets.items():
            if ticket not in broker_tickets:
                result["orphans_internal"].append(ticket)
                logger.info("[STATE_RECON_ORPHAN_INTERNAL] ticket=%d position_id=%s — not found in broker", ticket, pid)

        # B — Broker orphan (broker exists, internal missing)
        for ticket in broker_tickets:
            if ticket not in internal_tickets:
                result["orphans_broker"].append(ticket)
                logger.info("[STATE_RECON_ORPHAN_BROKER] ticket=%d — not tracked internally", ticket)

    except Exception as exc:
        logger.debug("[STATE_RECON] sanity check failed: %s", exc)

    return result
