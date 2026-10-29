"""
Trade Observability Validator — on-demand lifecycle integrity verification.

Validates that the trade lifecycle event stream is complete, ordered, and
consistent with TradeStateManager internal state. Designed for post-run
auditing and debug-mode validation — NOT for every-tick execution.

Usage:
    from core.observability_validator import validate_observability, reconstruct_timeline
    validate_observability(trade_manager, event_buffer)
    timeline = reconstruct_timeline("pos_12345", event_buffer)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from core.trade_management.events import TradeEvent, TradeLifecycleEvent
from core.trade_management.position import PositionStatus

logger = logging.getLogger(__name__)

# Terminal events — exactly one must close a trade
_TERMINAL_EVENTS = frozenset({
    TradeLifecycleEvent.ON_STOP_LOSS_HIT,
    TradeLifecycleEvent.ON_TAKE_PROFIT_HIT,
    TradeLifecycleEvent.ON_MANAGEMENT_EXIT,
    TradeLifecycleEvent.ON_TRADE_CLOSE,
})


@dataclass
class TradeTimeline:
    """Reconstructed chronological event sequence for a single trade."""
    position_id: str
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_open(self) -> bool:
        return any(e["event"] == "TRADE_OPEN" for e in self.events)

    @property
    def has_terminal(self) -> bool:
        return any(e["event"] in ("TRADE_SL_HIT", "TRADE_TP_HIT", "TRADE_MANAGEMENT_EXIT", "TRADE_CLOSE") for e in self.events)

    @property
    def is_complete(self) -> bool:
        return self.has_open and self.has_terminal


# ─── EVENT BUFFER (in-memory collector for validation) ────────────────────────

class TradeEventBuffer:
    """
    Lightweight in-memory buffer that collects trade lifecycle events for validation.
    Attach as a secondary listener or call .record() from the primary listener.
    """

    def __init__(self, max_events: int = 10000) -> None:
        self._events: list[dict[str, Any]] = []
        self._max = max_events

    def record(self, event: TradeEvent) -> None:
        """Record a trade event into the buffer."""
        if len(self._events) >= self._max:
            self._events.pop(0)  # Drop oldest to prevent unbounded growth

        self._events.append({
            "position_id": event.position.position_id,
            "event": _event_name(event.kind),
            "kind": event.kind,
            "time_s": event.time_s,
            "symbol": event.position.symbol,
            "status": event.position.status.value,
        })

    def events_for(self, position_id: str) -> list[dict[str, Any]]:
        """Get all events for a specific position, sorted by time."""
        return sorted(
            [e for e in self._events if e["position_id"] == position_id],
            key=lambda e: e["time_s"],
        )

    def all_position_ids(self) -> set[str]:
        """Get all unique position IDs in the buffer."""
        return {e["position_id"] for e in self._events}

    @property
    def event_count(self) -> int:
        return len(self._events)


def _event_name(kind: TradeLifecycleEvent) -> str:
    """Map internal event kind to log-friendly name."""
    mapping = {
        TradeLifecycleEvent.ON_TRADE_OPEN: "TRADE_OPEN",
        TradeLifecycleEvent.ON_PRICE_UPDATE: "TRADE_PRICE_UPDATE",
        TradeLifecycleEvent.ON_PARTIAL_CLOSE: "TRADE_PARTIAL_CLOSE",
        TradeLifecycleEvent.ON_TRADE_CLOSE: "TRADE_CLOSE",
        TradeLifecycleEvent.ON_STOP_LOSS_HIT: "TRADE_SL_HIT",
        TradeLifecycleEvent.ON_TAKE_PROFIT_HIT: "TRADE_TP_HIT",
        TradeLifecycleEvent.ON_MANAGEMENT_EXIT: "TRADE_MANAGEMENT_EXIT",
    }
    return mapping.get(kind, kind.value)


# ─── VALIDATION FUNCTIONS ─────────────────────────────────────────────────────

def validate_observability(
    trade_manager: Any,
    event_buffer: TradeEventBuffer,
) -> dict[str, Any]:
    """
    Run all observability validation checks. Returns a summary dict.
    Safe to call at any time — does not modify state.
    """
    issues: list[str] = []
    position_ids = event_buffer.all_position_ids()
    valid_count = 0

    for pid in position_ids:
        events = event_buffer.events_for(pid)
        pid_issues = _validate_single_trade(pid, events, trade_manager)
        if pid_issues:
            issues.extend(pid_issues)
        else:
            valid_count += 1

    total = len(position_ids)
    replay_ready = len(issues) == 0

    logger.info(
        "[OBS_VALIDATION_COMPLETE] total_trades=%d valid_trades=%d issues_found=%d replay_ready=%s",
        total, valid_count, len(issues), str(replay_ready).lower(),
    )

    return {
        "total_trades": total,
        "valid_trades": valid_count,
        "issues_found": len(issues),
        "issues": issues,
        "replay_ready": replay_ready,
    }


def _validate_single_trade(pid: str, events: list[dict[str, Any]], trade_manager: Any) -> list[str]:
    """Validate a single trade's event sequence. Returns list of issue strings."""
    issues: list[str] = []

    # A. Completeness check
    event_names = [e["event"] for e in events]
    has_open = "TRADE_OPEN" in event_names
    terminal_events = [e for e in event_names if e in ("TRADE_CLOSE", "TRADE_SL_HIT", "TRADE_TP_HIT", "TRADE_MANAGEMENT_EXIT")]

    if not has_open:
        msg = f"[OBS_INCOMPLETE_LIFECYCLE] trade_id={pid} missing_events=[TRADE_OPEN]"
        logger.info(msg)
        issues.append(msg)

    # Only check terminal completeness for positions that are CLOSED
    pos = _find_position(trade_manager, pid)
    if pos is not None and pos.status == PositionStatus.CLOSED and not terminal_events:
        msg = f"[OBS_INCOMPLETE_LIFECYCLE] trade_id={pid} missing_events=[terminal_event]"
        logger.info(msg)
        issues.append(msg)

    # B. Ordering check
    if has_open:
        open_idx = event_names.index("TRADE_OPEN")
        if open_idx != 0:
            msg = f"[OBS_EVENT_ORDER_VIOLATION] trade_id={pid} TRADE_OPEN not first (index={open_idx})"
            logger.info(msg)
            issues.append(msg)

    if terminal_events:
        last_terminal_idx = max(i for i, e in enumerate(event_names) if e in ("TRADE_CLOSE", "TRADE_SL_HIT", "TRADE_TP_HIT", "TRADE_MANAGEMENT_EXIT"))
        events_after_terminal = event_names[last_terminal_idx + 1:]
        non_close_after = [e for e in events_after_terminal if e != "TRADE_CLOSE"]
        if non_close_after:
            msg = f"[OBS_EVENT_ORDER_VIOLATION] trade_id={pid} events_after_terminal={non_close_after}"
            logger.info(msg)
            issues.append(msg)

    # C. Duplicate detection
    open_count = event_names.count("TRADE_OPEN")
    if open_count > 1:
        msg = f"[OBS_DUPLICATE_EVENT] trade_id={pid} event_type=TRADE_OPEN count={open_count}"
        logger.info(msg)
        issues.append(msg)

    if len(terminal_events) > 2:  # Allow SL_HIT + TRADE_CLOSE pair (normal)
        msg = f"[OBS_DUPLICATE_EVENT] trade_id={pid} terminal_events={terminal_events}"
        logger.info(msg)
        issues.append(msg)

    # E. State vs event consistency
    if pos is not None:
        last_event_status = events[-1]["status"] if events else None
        if pos.status == PositionStatus.CLOSED and last_event_status == "open":
            msg = f"[OBS_STATE_EVENT_MISMATCH] trade_id={pid} state=CLOSED event_state=OPEN"
            logger.info(msg)
            issues.append(msg)
        elif pos.status == PositionStatus.OPEN and terminal_events:
            msg = f"[OBS_STATE_EVENT_MISMATCH] trade_id={pid} state=OPEN but terminal_event exists"
            logger.info(msg)
            issues.append(msg)

    return issues


def _find_position(trade_manager: Any, position_id: str) -> Any:
    """Safely find a position in TradeStateManager by ID."""
    if trade_manager is None:
        return None
    try:
        return trade_manager._by_id.get(position_id)
    except (AttributeError, TypeError):
        return None


# ─── TIMELINE RECONSTRUCTION ──────────────────────────────────────────────────

def reconstruct_timeline(position_id: str, event_buffer: TradeEventBuffer) -> TradeTimeline:
    """
    Rebuild a chronological event timeline for a single trade.
    For validation/debug only — not runtime execution.
    """
    events = event_buffer.events_for(position_id)
    timeline = TradeTimeline(position_id=position_id)

    for e in events:
        timeline.events.append({
            "event": e["event"],
            "time_s": e["time_s"],
            "symbol": e["symbol"],
            "status": e["status"],
        })

    return timeline
