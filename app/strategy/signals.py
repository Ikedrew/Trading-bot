from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Signal:
    """Pure signal at a completed bar — no prices beyond bar identity."""

    pattern: str
    side: Side
    bar_index: int
    bar_time: int
    confidence: float = 1.0  # 0.0–1.0 pattern strength/quality (1.0 = full confidence)
    version: str = ""        # pattern detection logic version (audit metadata only)
