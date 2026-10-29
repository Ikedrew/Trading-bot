"""
Risk Timeline — Append-only cycle-by-cycle risk history buffer.

STRICTLY OBSERVATIONAL. Records risk snapshots over time for:
  - Debugging trade decisions
  - Auditing why trades were allowed or blocked
  - Observing regime + risk evolution
  - Replaying risk conditions per cycle

MUST NOT affect trading decisions.
MUST NOT block or allow trades.
MUST NOT call execution functions.

Design: O(1) append into a bounded deque (FIFO). No disk I/O in hot path.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


# ─── CONFIGURATION ───────────────────────────────────────────────────────────

_DEFAULT_MAX_ENTRIES = 2000


# ─── DATA STRUCTURE ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RiskTimelineEntry:
    """Single point-in-time risk snapshot."""
    timestamp: float
    symbol: str
    snapshot: dict[str, Any]


# ─── TIMELINE BUFFER (module-level singleton) ──────────────────────────────────

_buffer: deque[RiskTimelineEntry] = deque(maxlen=_DEFAULT_MAX_ENTRIES)


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def record_risk_snapshot(summary: dict[str, Any]) -> None:
    """
    Append a risk summary snapshot to the timeline buffer.

    Args:
        summary: dict from get_risk_summary() — not modified.

    O(1) operation. Never raises. Never blocks.
    """
    try:
        entry = RiskTimelineEntry(
            timestamp=summary.get("timestamp", 0.0),
            symbol=summary.get("symbol", ""),
            snapshot=summary,
        )
        _buffer.append(entry)
    except Exception:
        pass  # Timeline failure must never affect runtime


def get_risk_timeline(
    *,
    symbol: str | None = None,
    last_n: int = 100,
) -> list[RiskTimelineEntry]:
    """
    Retrieve recent timeline entries.

    Args:
        symbol: Optional filter. None = all symbols.
        last_n: Maximum entries to return (most recent first).

    Returns:
        List of RiskTimelineEntry, newest first.
    """
    try:
        if symbol is None:
            entries = list(_buffer)
        else:
            entries = [e for e in _buffer if e.symbol == symbol]
        # Return most recent first, limited to last_n
        return entries[-last_n:][::-1]
    except Exception:
        return []


def export_risk_timeline() -> list[dict[str, Any]]:
    """
    Export full timeline as list of dicts (for logging dumps / offline analysis).

    Returns:
        List of snapshot dicts with timestamp and symbol, oldest first.
    """
    try:
        return [
            {"timestamp": e.timestamp, "symbol": e.symbol, **e.snapshot}
            for e in _buffer
        ]
    except Exception:
        return []


def get_timeline_length() -> int:
    """Current number of entries in the buffer."""
    return len(_buffer)


def clear_timeline() -> None:
    """Clear buffer (for testing only)."""
    _buffer.clear()
