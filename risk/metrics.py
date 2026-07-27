"""
Risk metrics aggregation — lightweight in-memory statistics.

Tracks acceptance/rejection rates, RR profiles, SL distances, and pattern breakdowns.
No external dependencies. No persistence. Replay-safe.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

# Rolling window size for averages (last N trades)
_ROLLING_WINDOW = 200


class RiskMetrics:
    """In-memory risk layer statistics. Thread-unsafe (single-threaded runtime)."""

    def __init__(self) -> None:
        self.accepted_total: int = 0
        self.rejected_total: int = 0
        self.rejections_by_reason: dict[str, int] = {}
        self.rejections_by_pattern: dict[str, int] = {}
        self.accepted_by_pattern: dict[str, int] = {}
        self._rr_values: deque[float] = deque(maxlen=_ROLLING_WINDOW)
        self._sl_distances: deque[float] = deque(maxlen=_ROLLING_WINDOW)

    # ─── RECORDING ────────────────────────────────────────────────────

    def record_accepted(self, *, pattern: str, rr: float, sl_distance: float) -> None:
        """Record an accepted risk decision."""
        self.accepted_total += 1
        self.accepted_by_pattern[pattern] = self.accepted_by_pattern.get(pattern, 0) + 1
        self._rr_values.append(rr)
        self._sl_distances.append(sl_distance)

    def record_rejected(self, *, reason: str, pattern: str) -> None:
        """Record a rejected risk decision."""
        self.rejected_total += 1
        self.rejections_by_reason[reason] = self.rejections_by_reason.get(reason, 0) + 1
        self.rejections_by_pattern[pattern] = self.rejections_by_pattern.get(pattern, 0) + 1

    # ─── QUERIES ──────────────────────────────────────────────────────

    @property
    def total_evaluated(self) -> int:
        return self.accepted_total + self.rejected_total

    @property
    def acceptance_rate(self) -> float:
        if self.total_evaluated == 0:
            return 0.0
        return self.accepted_total / self.total_evaluated

    @property
    def rejection_rate(self) -> float:
        if self.total_evaluated == 0:
            return 0.0
        return self.rejected_total / self.total_evaluated

    @property
    def avg_rr(self) -> float:
        if not self._rr_values:
            return 0.0
        return sum(self._rr_values) / len(self._rr_values)

    @property
    def avg_sl_distance(self) -> float:
        if not self._sl_distances:
            return 0.0
        return sum(self._sl_distances) / len(self._sl_distances)

    # ─── SNAPSHOT ─────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return full metrics snapshot as dict."""
        return {
            "accepted_total": self.accepted_total,
            "rejected_total": self.rejected_total,
            "total_evaluated": self.total_evaluated,
            "acceptance_rate": round(self.acceptance_rate * 100, 1),
            "rejection_rate": round(self.rejection_rate * 100, 1),
            "avg_rr": round(self.avg_rr, 2),
            "avg_sl_distance": round(self.avg_sl_distance, 6),
            "rejections_by_reason": dict(self.rejections_by_reason),
            "accepted_by_pattern": dict(self.accepted_by_pattern),
            "rejections_by_pattern": dict(self.rejections_by_pattern),
        }

    def log_summary(self) -> None:
        """Emit structured metrics summary log."""
        s = self.snapshot()
        logger.info(
            "[RISK_METRICS] evaluated=%d accepted=%d rejected=%d "
            "acceptance_rate=%.1f%% avg_rr=%.2f avg_sl=%.6f",
            s["total_evaluated"], s["accepted_total"], s["rejected_total"],
            s["acceptance_rate"], s["avg_rr"], s["avg_sl_distance"],
        )
        if s["rejections_by_reason"]:
            parts = " ".join(f"{k}={v}" for k, v in sorted(s["rejections_by_reason"].items()))
            logger.info("[RISK_METRICS_REJECTIONS] %s", parts)

    def reset(self) -> None:
        """Reset all counters."""
        self.accepted_total = 0
        self.rejected_total = 0
        self.rejections_by_reason.clear()
        self.rejections_by_pattern.clear()
        self.accepted_by_pattern.clear()
        self._rr_values.clear()
        self._sl_distances.clear()


# Module-level singleton
risk_metrics = RiskMetrics()
