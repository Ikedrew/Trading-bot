"""
Pattern interface — abstract base for all pattern detectors.

Every pattern module must implement this interface and register via registry.
Patterns are pure behavioural detectors: they observe candle data and report signals.
They never decide trades, never mutate state, never call external systems.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from data.mt5_data import Candle
from strategy.signals import Signal


@dataclass(frozen=True)
class PatternConfig:
    """Optional per-pattern configuration (thresholds, tolerances)."""
    pass


class PatternDetector(ABC):
    """
    Abstract base for all pattern detectors.

    Subclasses must implement:
        name        — unique pattern identifier (e.g. "BULLISH_ENGULFING")
        bar_count   — number of candles required (1, 2, or 3)
        detect()    — detection logic returning list of Signal objects
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this pattern (used in Signal.pattern field)."""
        ...

    @property
    @abstractmethod
    def bar_count(self) -> int:
        """Number of candles this pattern inspects (1, 2, or 3)."""
        ...

    @property
    def version(self) -> str:
        """Detection logic version for audit traceability. Override in subclass."""
        return "1.0"

    @abstractmethod
    def detect(self, candles: list[Candle], closed_index: int) -> list[Signal]:
        """
        Detect pattern at the given closed bar index.

        Args:
            candles: Full candle array (for lookback context)
            closed_index: Index of the most recently closed bar

        Returns:
            List of Signal objects detected (empty if no pattern found).
            Each Signal must have bar_index=closed_index and bar_time=candles[closed_index].time.
        """
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r} bars={self.bar_count}>"
