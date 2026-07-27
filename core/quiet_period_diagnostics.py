"""
F2: Quiet Period Diagnostics — Root-cause visibility for no-trade periods.

Reports WHY trades are not being executed by tracking gate rejection reasons
and producing ranked summaries during quiet periods.

This is diagnostics only — no behaviour change, no blocking logic.
Pure observability layer.
"""

from __future__ import annotations

import logging
import time as _time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ─── DATA MODEL ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RejectionEntry:
    """Single gate rejection event with timestamp."""
    gate_name: str
    timestamp: float


@dataclass(frozen=True)
class DiagnosticSummary:
    """Quiet period diagnostic breakdown."""
    no_trade_cycles: int
    top_reasons: list  # list of (gate_name, count) tuples
    session_total_rejections: int
    last_30_min_rejections: int
    last_1_hour_rejections: int


# ─── REJECTION REASON TRACKER ─────────────────────────────────────────────────

class RejectionReasonTracker:
    """
    Centralized tracker for gate rejection reasons.

    Every gate in the execution pipeline should call record() when
    it blocks a trade. This data is used for diagnostics only.
    """

    def __init__(self, max_history: int = 5000) -> None:
        self._session_counts: dict[str, int] = defaultdict(int)
        self._history: deque[RejectionEntry] = deque(maxlen=max_history)
        self._session_start: float = _time.time()

    def record(self, gate_name: str) -> None:
        """
        Record a gate rejection event.

        Args:
            gate_name: Identifier of the gate that blocked
                       (e.g. "A4_daily_trade_limit", "I2_regime_guard")
        """
        self._session_counts[gate_name] += 1
        self._history.append(RejectionEntry(
            gate_name=gate_name,
            timestamp=_time.time(),
        ))

    def get_top_reasons(self, n: int = 3) -> list[tuple[str, int]]:
        """
        Get top N rejection reasons sorted by frequency (descending).

        Returns list of (gate_name, count) tuples.
        """
        sorted_reasons = sorted(
            self._session_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )
        return sorted_reasons[:n]

    def get_session_total(self) -> int:
        """Total rejections this session."""
        return sum(self._session_counts.values())

    def get_last_n_minutes(self, minutes: float) -> int:
        """Count rejections in the last N minutes."""
        cutoff = _time.time() - (minutes * 60.0)
        return sum(1 for entry in self._history if entry.timestamp >= cutoff)

    def get_counts_by_gate(self) -> dict[str, int]:
        """Return all gate counts as a plain dict."""
        return dict(self._session_counts)

    def reset(self) -> None:
        """Reset all counters (called by D4 daily reset)."""
        self._session_counts.clear()
        self._history.clear()
        self._session_start = _time.time()
        logger.info("[QUIET_PERIOD_DIAGNOSTICS] counters reset")


# ─── MODULE SINGLETON ─────────────────────────────────────────────────────────

_tracker: RejectionReasonTracker | None = None


def get_tracker() -> RejectionReasonTracker:
    """Get or create the singleton rejection tracker."""
    global _tracker
    if _tracker is None:
        _tracker = RejectionReasonTracker()
    return _tracker


# ─── CONVENIENCE API ──────────────────────────────────────────────────────────

def record_rejection(gate_name: str) -> None:
    """Record a gate rejection. Uses singleton tracker."""
    get_tracker().record(gate_name)


def get_top_rejection_reasons(n: int = 3) -> list[tuple[str, int]]:
    """Get top N rejection reasons. Uses singleton tracker."""
    return get_tracker().get_top_reasons(n)


# ─── DIAGNOSTIC SUMMARY BUILDER ───────────────────────────────────────────────

def build_rejection_summary(no_trade_cycles: int = 0) -> DiagnosticSummary:
    """
    Build a complete diagnostic summary for quiet period alerting.

    Args:
        no_trade_cycles: Current consecutive no-trade cycle count.

    Returns:
        DiagnosticSummary with ranked reasons and time-window breakdowns.
    """
    tracker = get_tracker()
    return DiagnosticSummary(
        no_trade_cycles=no_trade_cycles,
        top_reasons=tracker.get_top_reasons(3),
        session_total_rejections=tracker.get_session_total(),
        last_30_min_rejections=tracker.get_last_n_minutes(30),
        last_1_hour_rejections=tracker.get_last_n_minutes(60),
    )


# ─── ALERT EMITTER ────────────────────────────────────────────────────────────

def emit_quiet_period_alert(no_trade_cycles: int) -> DiagnosticSummary:
    """
    Emit structured quiet period diagnostic alert.

    Called when consecutive_no_trade_cycles exceeds threshold.
    Logs ranked rejection summary and returns summary for external alerting.
    """
    summary = build_rejection_summary(no_trade_cycles)

    # Build ranked reasons string
    reasons_str = ""
    for i, (gate, count) in enumerate(summary.top_reasons, 1):
        reasons_str += f"\n    {i}. {gate} → {count}"

    logger.warning(
        "[QUIET_PERIOD_DIAGNOSTIC] No-trade cycles: %d "
        "Top rejection reasons:%s "
        "Session total: %d Last 30min: %d Last 1hr: %d",
        no_trade_cycles, reasons_str or "\n    (none recorded)",
        summary.session_total_rejections,
        summary.last_30_min_rejections,
        summary.last_1_hour_rejections,
    )

    return summary


def build_telegram_payload(summary: DiagnosticSummary) -> dict[str, Any]:
    """
    Build structured payload for Telegram/Discord alert integration.

    Returns dict suitable for JSON serialization and external alerting.
    """
    top_rejections = [
        {"gate": gate, "count": count}
        for gate, count in summary.top_reasons
    ]

    return {
        "alert": "QUIET_PERIOD",
        "cycles": summary.no_trade_cycles,
        "session_total_rejections": summary.session_total_rejections,
        "last_30_min": summary.last_30_min_rejections,
        "last_1_hour": summary.last_1_hour_rejections,
        "top_rejections": top_rejections,
    }
