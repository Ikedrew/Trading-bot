"""
Contextual Expectancy Model — Data structures for cohort-based expectancy analysis.

STRICTLY OFFLINE — never imported by runtime code.
These are pure data containers for the policy discovery layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CohortKey:
    """Identifies a unique cohort by its dimensional coordinates."""

    confirmation_strength: str   # "STRONG" / "WEAK" / "INVALID"
    entry_timing: str            # "EARLY" / "MID" / "LATE"
    market_regime: str           # "TRENDING" / "RANGING" / "UNKNOWN"


@dataclass(frozen=True)
class CohortStats:
    """Performance statistics for a single cohort."""

    win_rate: float       # 0.0–1.0
    avg_rr: float         # Average R outcome (positive = profitable)
    expectancy: float     # Expected R per trade
    variance: float       # Outcome variance (risk stability)
    trade_count: int      # Number of trades in cohort
    mfe_mean: float = 0.0   # Mean MFE in R (how far winners ran)
    mae_mean: float = 0.0   # Mean MAE in R (how far losers dipped)


@dataclass(frozen=True)
class ExpectancyResult:
    """Computed expectancy for a specific cohort context."""

    cohort: CohortKey
    stats: CohortStats
