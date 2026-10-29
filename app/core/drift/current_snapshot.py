"""
Current Snapshot — Rolling performance snapshot for drift comparison against baseline.

Consumes ONLY CanonicalTradeEvent records. NO recalculation of stored fields.
NO execution logic. NO live system modification.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from core.trade_schema import CanonicalTradeEvent


# ─── SNAPSHOT TYPE ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CurrentCohortState:
    """Rolling performance state for a single cohort over a recent window."""

    cohort_key: str
    current_expectancy: float
    current_win_rate: float
    current_avg_r: float
    current_variance: float
    sample_size: int
    recent_window: str  # e.g. "last_50_trades" or "2024-07-01_to_2024-07-31"


# ─── CONFIGURATION ────────────────────────────────────────────────────────────

_DEFAULT_WINDOW_SIZE = 50  # Default: use last 50 trades per cohort


# ─── SNAPSHOT BUILDER ─────────────────────────────────────────────────────────

def build_current_snapshot(
    recent_trade_data: list[CanonicalTradeEvent],
    window_size: int = _DEFAULT_WINDOW_SIZE,
    window_label: str | None = None,
) -> dict[str, CurrentCohortState]:
    """
    Build rolling performance snapshot from recent CanonicalTradeEvent records.

    Groups trades by cohort key (confirmation_strength + entry_timing + market_regime),
    then computes stats using ONLY stored fields from the canonical event — no
    recalculation of timing, confirmation, or regime.

    Args:
        recent_trade_data: List of CanonicalTradeEvent objects (recent period).
        window_size: Maximum trades per cohort to include (most recent N).
        window_label: Optional label for the window (default: "last_{window_size}_trades").

    Returns:
        Mapping of cohort_key → CurrentCohortState for cohorts with data.
    """
    if window_label is None:
        window_label = f"last_{window_size}_trades"

    # Group by cohort key using ONLY stored fields
    groups: dict[str, list[CanonicalTradeEvent]] = defaultdict(list)

    for event in recent_trade_data:
        key = _build_cohort_key(event)
        groups[key].append(event)

    # Build snapshot per cohort (capped to window_size most recent)
    snapshots: dict[str, CurrentCohortState] = {}

    for cohort_key, events in groups.items():
        # Take most recent N trades (assumes input is chronologically ordered)
        windowed = events[-window_size:]

        if not windowed:
            continue

        state = _compute_cohort_state(cohort_key, windowed, window_label)
        snapshots[cohort_key] = state

    return snapshots


# ─── INTERNAL HELPERS ─────────────────────────────────────────────────────────

def _build_cohort_key(event: CanonicalTradeEvent) -> str:
    """
    Build cohort key string from stored CanonicalTradeEvent fields.

    Uses confirmation_strength, entry_timing, market_regime directly —
    NO recalculation.
    """
    return f"{event.confirmation_strength}+{event.entry_timing}+{event.market_regime}"


def _compute_cohort_state(
    cohort_key: str,
    events: list[CanonicalTradeEvent],
    window_label: str,
) -> CurrentCohortState:
    """Compute performance stats from a windowed set of canonical events."""
    r_values: list[float] = []
    wins = 0

    for event in events:
        if event.final_r is not None:
            r_values.append(event.final_r)
            if event.final_r > 0:
                wins += 1

    sample_size = len(events)

    if not r_values:
        return CurrentCohortState(
            cohort_key=cohort_key,
            current_expectancy=0.0,
            current_win_rate=0.0,
            current_avg_r=0.0,
            current_variance=0.0,
            sample_size=sample_size,
            recent_window=window_label,
        )

    avg_r = sum(r_values) / len(r_values)
    win_rate = wins / len(r_values)
    variance = (
        sum((r - avg_r) ** 2 for r in r_values) / len(r_values)
        if len(r_values) > 1
        else 0.0
    )

    return CurrentCohortState(
        cohort_key=cohort_key,
        current_expectancy=round(avg_r, 4),
        current_win_rate=round(win_rate, 4),
        current_avg_r=round(avg_r, 4),
        current_variance=round(variance, 4),
        sample_size=sample_size,
        recent_window=window_label,
    )
