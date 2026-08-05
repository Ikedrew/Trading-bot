# ==========================================================
# LEGACY COMPATIBILITY LAYER (FREEZE ZONE)
# ==========================================================
# This module exists ONLY for backward compatibility.
# DO NOT extend or improve this file.
# All new event logic must go into core/event_stream.py.
# This file will be removed after full migration.
# ==========================================================
#
# IMPORTANT:
# New modules must NOT import this file unless they are legacy runtime components.
# Preferred import: core.event_stream
#
# WARNING: Do not add new logic here. Use event_stream instead.
# ==========================================================

"""
Event Bus — FROZEN backward-compatibility bridge.

Provides legacy runtime API (EventState, emit_*, log_*) used by:
    - core/runtime/live_scanner.py
    - core/runtime/replay_runtime.py
    - core/runtime/replay_scanner.py

Also re-exports core.event_stream canonical API for convenience,
but new code MUST import from core.event_stream directly.
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ─── RE-EXPORT: UNIFIED EVENT STREAM (canonical API) ──────────────────────────
# REPLACE (use event_stream directly in new code)

from core.event_stream import (  # noqa: F401, E402
    EventType,
    emit,
    emit_decision,
    emit_strategy,
    emit_entity,
    emit_candle,
    emit_execution,
    emit_outcome,
    emit_feature_update,
    emit_pattern_detected,
    emit_bias_change,
    emit_confluence_score,
    emit_risk_check,
    emit_trade_management,
    read_stream,
    stats as stream_stats,
    flush as stream_flush,
    close as stream_close,
    disable as stream_disable,
    enable as stream_enable,
)


# ─── KEEP (legacy runtime dependency: EventState) ────────────────────────────

@dataclass
class EventState:
    """
    Per-symbol event deduplication state for Discord/console logging.
    Prevents repeated emission of identical events when nothing has changed.

    KEEP — used by live_scanner, replay_runtime, replay_scanner.
    """
    last_bias: str | None = None
    last_bias_phase: str | None = None
    last_pattern: str | None = None
    last_regime: str | None = None
    last_strategy: str | None = None
    last_decision: str | None = None
    last_heartbeat_time: float = 0.0
    cycle_count: int = 0


# ─── KEEP (legacy runtime dependency: active symbol context) ──────────────────

_active_symbol: str = "UNKNOWN"


# KEEP (legacy runtime dependency)
def set_active_symbol(symbol: str) -> None:
    """Set the currently active symbol for log routing."""
    global _active_symbol
    _active_symbol = symbol


# KEEP (legacy runtime dependency)
def get_active_symbol() -> str:
    """Get the currently active symbol."""
    return _active_symbol


# ─── KEEP (legacy runtime dependency: Discord emit) ───────────────────────────

# REPLACE (use event_stream.emit instead for structured events)
# KEEP as bridge — still called by runtime modules for Discord output
def emit_event(channel: str, message: str) -> None:
    """Emit a message to a named Discord channel. Silent on failure."""
    try:
        from core.discord_notifier import send_discord
        send_discord(channel, message)
    except Exception:
        pass


def _emit_to_pair_channel(symbol: str, message: str) -> None:
    """Duplicate a message to the pair-{symbol} channel. Silent on failure."""
    try:
        from core.discord_notifier import send_discord
        base = symbol.lower().replace("_sb", "").replace("_", "")
        channel = f"pair-{base}"
        send_discord(channel, message)
    except Exception:
        pass


# ─── KEEP (legacy runtime dependency: bias events) ───────────────────────────

# KEEP (legacy runtime dependency)
def emit_bias_events(
    *,
    symbol: str,
    event_state: EventState,
    engine_state: Any,
    **kwargs: Any,
) -> None:
    """Emit bias state change events to Discord (if state changed)."""
    try:
        current_bias = getattr(engine_state, "current_bias", None)
        bias_phase = getattr(engine_state, "bias_phase", None)

        bias_str = str(current_bias) if current_bias else "NONE"
        phase_str = str(bias_phase) if bias_phase else "EXPIRED"

        if bias_str == event_state.last_bias and phase_str == event_state.last_bias_phase:
            return

        event_state.last_bias = bias_str
        event_state.last_bias_phase = phase_str

        msg = f"\U0001f9ed **BIAS UPDATE** | `{symbol}` | {bias_str} ({phase_str})"
        emit_event("market-context", msg)
    except Exception:
        pass


# ─── KEEP (legacy runtime dependency: setup events) ──────────────────────────

# KEEP (legacy runtime dependency)
def emit_setup_events(
    *,
    symbol: str,
    event_state: EventState,
    decision: Any,
    **kwargs: Any,
) -> None:
    """Emit pattern/setup detection events to Discord (if new)."""
    try:
        if isinstance(decision, dict):
            pattern = decision.get("pattern")
            action = decision.get("action", "NO_TRADE")
        else:
            pattern = getattr(decision, "pattern", None)
            action = getattr(decision, "action", "NO_TRADE")

        pattern_str = str(pattern) if pattern else None

        if pattern_str == event_state.last_pattern and action == event_state.last_decision:
            return

        event_state.last_pattern = pattern_str
        event_state.last_decision = action

        if pattern_str and action != "NO_TRADE":
            msg = f"\U0001f4ca **SETUP FOUND** | `{symbol}` | {pattern_str} | {action}"
            emit_event("decision-log", msg)
    except Exception:
        pass


# ─── KEEP (legacy runtime dependency: trade events) ──────────────────────────

# KEEP (legacy runtime dependency)
def emit_trade_events(
    *,
    symbol: str,
    event_state: EventState,
    decision: Any,
    **kwargs: Any,
) -> None:
    """Emit trade execution events to Discord."""
    try:
        if isinstance(decision, dict):
            action = decision.get("action", "NO_TRADE")
        else:
            action = getattr(decision, "action", "NO_TRADE")

        if action == "EXECUTE":
            msg = f"\U0001f680 **TRADE SIGNAL** | `{symbol}` | Execution triggered"
            emit_event("trade-execution", msg)
    except Exception:
        pass


# ─── KEEP (legacy runtime dependency: cycle summary) ─────────────────────────

# KEEP (legacy runtime dependency)
def log_cycle_summary_simple(
    cycle_id: Any = None,
    state_count: Any = None,
    latency_ms: Any = None,
    *,
    symbol: str = "",
    iteration: int = 0,
    decision: Any = None,
    elapsed_ms: float = 0.0,
    **kwargs: Any,
) -> None:
    """Log compact cycle summary to console.

    Supports both positional calling convention (cycle_id, state_count, latency_ms)
    used by live_scanner/replay_scanner, and keyword-only convention used by
    legacy pipeline trace callers.
    """
    try:
        # Positional call path: log_cycle_summary_simple(cycle_id, state_count, latency_ms)
        if cycle_id is not None and decision is None:
            _cid = cycle_id
            _count = state_count if state_count is not None else 0
            _lat = latency_ms if latency_ms is not None else 0
            print(f"[CYCLE] #{_cid} | states={_count} | {_lat}ms")
            return

        # Keyword call path (legacy pipeline trace format)
        _sym = symbol
        _iter = iteration
        if isinstance(decision, dict):
            action = decision.get("action", "NO_TRADE")
            reason = decision.get("reason", "")
            score = decision.get("score", 0)
        else:
            action = getattr(decision, "action", "NO_TRADE")
            reason = getattr(decision, "reason", "")
            score = getattr(decision, "score", 0)

        print(f"[CYCLE] {_sym} #{_iter} | {action} | score={score:.3f} | {reason[:50]} | {elapsed_ms:.0f}ms")
    except Exception:
        pass


# KEEP (legacy alias — replay_runtime.py imports this name)
def log_cycle_summary(*args: Any, **kwargs: Any) -> None:
    """Legacy alias for log_cycle_summary_simple."""
    return log_cycle_summary_simple(*args, **kwargs)


# ─── KEEP (legacy runtime dependency: heartbeat) ─────────────────────────────

# KEEP (legacy runtime dependency)
def log_heartbeat(
    *args: Any,
    symbols_active: int = 0,
    iteration: int = 0,
    **kwargs: Any,
) -> None:
    """Emit periodic heartbeat to Discord.

    Supports positional args (cycle_id, ...) from live/replay scanner
    and keyword-only convention from legacy callers.
    """
    try:
        # Positional call path: log_heartbeat(cycle_id, value, symbol, source)
        if args:
            _iter = args[0] if args else 0
            _syms = args[1] if len(args) > 1 else symbols_active
        else:
            _iter = iteration
            _syms = symbols_active
        now = _time.time()
        msg = f"\U0001f493 **HEARTBEAT** | symbols={_syms} | cycle={_iter} | ts={int(now)}"
        emit_event("heartbeat", msg)
    except Exception:
        pass


# ─── KEEP (legacy runtime dependency: liveness) ──────────────────────────────

# KEEP (legacy runtime dependency)
def log_liveness_status(
    status_or_symbol: Any = None,
    latency: Any = None,
    cycle: Any = None,
    *,
    symbol: str = "",
    status: str = "",
    **kwargs: Any,
) -> None:
    """Log liveness/stale status.

    Supports positional args (status, latency, cycle_id) from live_scanner
    and keyword-only convention from legacy callers.
    """
    try:
        # Positional call path: log_liveness_status("STALLED", latency_s, cycle_id)
        if status_or_symbol is not None and not symbol and not status:
            _status = str(status_or_symbol)
        else:
            _status = status
            if not _status:
                _status = str(status_or_symbol) if status_or_symbol else "UNKNOWN"

        _sym = symbol if symbol else "SYSTEM"

        if _status != "HEALTHY" and _status != "OK":
            msg = f"\u26a0\ufe0f **LIVENESS** | `{_sym}` | {_status}"
            emit_event("system-status", msg)
    except Exception:
        pass


# ─── KEEP (legacy runtime dependency: exceptions) ────────────────────────────

# KEEP (legacy runtime dependency)
def log_runtime_exception(*args: Any, **kwargs: Any) -> None:
    """Log runtime exception to errors channel. Accepts positional or keyword args."""
    try:
        # Handle both call signatures:
        #   log_runtime_exception(exc, context, mt5_state)  — positional (live_scanner)
        #   log_runtime_exception(symbol=..., error=...)    — keyword
        if args:
            error_str = str(args[0])[:200]
            symbol = kwargs.get("symbol", _active_symbol)
        else:
            symbol = kwargs.get("symbol", _active_symbol)
            error_str = str(kwargs.get("error", "unknown"))[:200]

        msg = f"\U0001f6a8 **ERROR** | `{symbol}` | {error_str}"
        emit_event("errors", msg)
    except Exception:
        pass


# ─── KEEP (legacy runtime dependency: debug mode check) ──────────────────────

# KEEP (legacy runtime dependency — replay_runtime.py)
def is_full_debug_mode() -> bool:
    """Returns True if FULL_DEBUG print mode is active."""
    try:
        from core import config
        return str(getattr(config, "PRINT_MODE", "EVENT_ONLY")).upper() == "FULL_DEBUG"
    except ImportError:
        return False


# ─── KEEP (legacy runtime dependency: TradeLifecycleLogger) ──────────────────

# KEEP (legacy runtime dependency — live_scanner.py)
class TradeLifecycleLogger:
    """
    Tracks and logs trade lifecycle events (open, modify, close).
    Used by live_scanner for Discord trade-execution channel.
    Also handles trade journal persistence on trade close.
    """

    def __init__(self) -> None:
        self._active_trades: dict[str, dict[str, Any]] = {}

    def on_trade_event(self, event: Any) -> None:
        """
        Protocol-compliant dispatch for TradeLifecycleListener.
        Routes trade lifecycle events to appropriate handlers and persistence.
        """
        try:
            pos = event.position
            prices = event.price_snapshot
            kind = event.kind

            # Import here to avoid circular dependency
            from core.trade_management.events import TradeLifecycleEvent

            if kind == TradeLifecycleEvent.ON_TRADE_OPEN:
                self.on_trade_open(
                    symbol=pos.symbol,
                    side=pos.side.value if hasattr(pos.side, "value") else str(pos.side),
                    volume=pos.volume,
                    entry=pos.entry_price,
                    sl=pos.stop_loss,
                    tp=pos.take_profit,
                )
            elif kind == TradeLifecycleEvent.ON_TRADE_CLOSE:
                exit_price = prices[0] if pos.side.value == "BUY" else prices[1]
                pnl = pos.unrealised_pnl
                reason = event.detail.get("reason", "unknown") if event.detail else "unknown"
                self.on_trade_close(
                    symbol=pos.symbol,
                    exit_price=exit_price,
                    reason=reason,
                    pnl=pnl,
                )
                # Persist to trade journal (with Position-owned identity)
                self._persist_trade_close(pos, exit_price, event)
        except Exception:
            pass  # Lifecycle logging must never affect trade management

    def _persist_trade_close(self, position: Any, exit_price: float, event: Any) -> None:
        """Persist trade record and Trade Truth on position close."""
        try:
            from core.trade_journal import build_trade_record, persist_trade_once
            from core.trade_management.events import TradeLifecycleEvent

            # Determine close reason from the preceding lifecycle event kind
            detail = event.detail if hasattr(event, "detail") else {}
            ts = event.time_s if hasattr(event, "time_s") else _time.time()

            # Map lifecycle event detail to close_reason
            _reason = detail.get("reason", "")
            if _reason:
                close_reason = _reason
            else:
                # Infer from what's in the active trades record
                close_reason = "unknown"

            record = build_trade_record(
                position=position,
                exit_price=exit_price,
                exit_time=ts,
                close_reason=close_reason,
                realised_pnl_override=detail.get("broker_profit"),
            )
            persist_trade_once(record)
        except Exception:
            pass  # Persistence failure must never crash trade management

    def on_trade_open(self, symbol: str, side: str, volume: float, entry: float, sl: float, tp: float) -> None:
        """Log trade open event."""
        try:
            self._active_trades[symbol] = {
                "side": side, "volume": volume, "entry": entry,
                "sl": sl, "tp": tp, "open_time": _time.time(),
            }
            msg = (
                f"\U0001f7e2 **TRADE OPEN** | `{symbol}`\n"
                f"Side: {side} | Vol: {volume} | Entry: {entry:.5f}\n"
                f"SL: {sl:.5f} | TP: {tp:.5f}"
            )
            emit_event("trade-execution", msg)
            _emit_to_pair_channel(symbol, msg)
        except Exception:
            pass

    def on_trade_close(self, symbol: str, exit_price: float, reason: str, pnl: float = 0.0) -> None:
        """Log trade close event."""
        try:
            self._active_trades.pop(symbol, {})
            icon = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
            msg = (
                f"{icon} **TRADE CLOSE** | `{symbol}`\n"
                f"Exit: {exit_price:.5f} | Reason: {reason} | P&L: {pnl:.2f}"
            )
            emit_event("trade-execution", msg)
            _emit_to_pair_channel(symbol, msg)
        except Exception:
            pass

    def on_trade_modify(self, symbol: str, new_sl: float | None = None, new_tp: float | None = None) -> None:
        """Log trade modification event."""
        try:
            parts = [f"\u270f\ufe0f **TRADE MODIFY** | `{symbol}`"]
            if new_sl is not None:
                parts.append(f"SL -> {new_sl:.5f}")
            if new_tp is not None:
                parts.append(f"TP -> {new_tp:.5f}")
            msg = " | ".join(parts)
            emit_event("trade-execution", msg)
            _emit_to_pair_channel(symbol, msg)
        except Exception:
            pass


# ─── SAFETY GUARD ─────────────────────────────────────────────────────────────

def _event_bus_warning():
    """
    Runtime warning for accidental usage in new code.
    This file is a FROZEN compatibility layer.
    All new event logic belongs in core/event_stream.py.
    """
    pass

# WARNING: Do not add new logic here. Use event_stream instead.
