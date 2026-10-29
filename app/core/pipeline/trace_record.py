"""
TraceRecord — Pure observational decision trace model.

Records pipeline decisions without influencing them.
Removing this module would not affect execution.

No module may consume TraceRecord for decision-making.
It exists solely for observability and post-hoc analysis.

Ownership: core/pipeline/trace_record.py
Mutability: NONE (frozen records)
Dependencies: NONE
Consumers: NONE (observational only — logs/diagnostics)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TraceRecord:
    """
    Immutable observation of a pipeline stage decision.

    Fields:
        symbol: Trading symbol (e.g. "EURUSD")
        timeframe: Timeframe string (e.g. "M5")
        stage: Pipeline stage that produced this trace
        result: Outcome at this stage ("PASS" | "REJECT" | "ALLOW")
        reason: Why this result occurred (None for PASS)
        timestamp: Monotonic time when recorded
    """

    symbol: str
    timeframe: str
    stage: str
    result: str  # "PASS" | "REJECT" | "ALLOW"
    reason: str | None = None
    timestamp: float = field(default_factory=time.monotonic)


class TraceCollector:
    """
    Per-cycle trace accumulator. Observational only.

    Usage:
        collector = TraceCollector(symbol="EURUSD", timeframe="M5")
        collector.trace("market_context", "PASS")
        collector.trace("scoring_engine", "REJECT", "below_threshold")
        ...
        records = collector.records  # list[TraceRecord]
    """

    def __init__(self, symbol: str = "", timeframe: str = "M5") -> None:
        self._symbol = symbol
        self._timeframe = timeframe
        self._records: list[TraceRecord] = []

    def trace(self, stage: str, result: str, reason: str | None = None) -> None:
        """Record a trace observation. Does not affect pipeline flow."""
        record = TraceRecord(
            symbol=self._symbol,
            timeframe=self._timeframe,
            stage=stage,
            result=result,
            reason=reason,
        )
        self._records.append(record)
        _logger.debug(
            "[TRACE] symbol=%s tf=%s stage=%s result=%s reason=%s",
            self._symbol,
            self._timeframe,
            stage,
            result,
            reason,
        )

    @property
    def records(self) -> list[TraceRecord]:
        """All trace records this cycle (read-only copy)."""
        return list(self._records)

    def reset(self) -> None:
        """Clear for next cycle."""
        self._records.clear()
