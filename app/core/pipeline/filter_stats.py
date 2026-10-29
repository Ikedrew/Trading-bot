"""
Lightweight pipeline filter statistics — in-memory, observational only.

Tracks pass/reject counts per filter. Never influences filtering behaviour.
Never persists. Never blocks. Purely passive.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class _FilterCounter:
    """Accumulator for a single filter's pass/reject counts."""
    evaluated: int = 0
    passed: int = 0
    rejected: int = 0

    @property
    def rejection_rate(self) -> float:
        if self.evaluated == 0:
            return 0.0
        return self.rejected / self.evaluated

    def record(self, passed: bool) -> None:
        self.evaluated += 1
        if passed:
            self.passed += 1
        else:
            self.rejected += 1


class FilterStats:
    """
    Pipeline filter statistics tracker.

    Usage:
        stats.record("market_filter", passed=False)
        stats.record("trend_filter", passed=True)
        stats.summary()  # logs all filter stats
    """

    def __init__(self) -> None:
        self._filters: dict[str, _FilterCounter] = {}

    def record(self, filter_name: str, *, passed: bool) -> None:
        """Record a filter evaluation result. Creates counter on first use."""
        if filter_name not in self._filters:
            self._filters[filter_name] = _FilterCounter()
        self._filters[filter_name].record(passed)

    def get(self, filter_name: str) -> _FilterCounter | None:
        """Get counter for a specific filter. Returns None if never evaluated."""
        return self._filters.get(filter_name)

    def summary(self) -> dict[str, dict[str, float | int]]:
        """Return summary dict of all filter statistics."""
        return {
            name: {
                "evaluated": c.evaluated,
                "passed": c.passed,
                "rejected": c.rejected,
                "rejection_rate": round(c.rejection_rate * 100, 1),
            }
            for name, c in self._filters.items()
        }

    def log_summary(self) -> None:
        """Emit structured log with all filter statistics."""
        for name, c in self._filters.items():
            logger.info(
                "[FILTER_STATS] %s | evaluated=%d passed=%d rejected=%d rejection_rate=%.1f%%",
                name, c.evaluated, c.passed, c.rejected, c.rejection_rate * 100,
            )

    def reset(self) -> None:
        """Reset all counters (e.g. between sessions)."""
        self._filters.clear()


# Module-level singleton — shared across pipeline stages within a process
pipeline_stats = FilterStats()
