"""
Horizon Profiles — Descriptive definitions for each trade horizon.

These are metadata/configuration ONLY. They do NOT affect execution.
They describe what each horizon MEANS in terms of market behaviour expectations.

Used by the classifier to determine eligibility and by future phases
for horizon-aware trade management.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HorizonProfile:
    """Descriptive profile for one trade horizon."""

    name: str
    # Time expectations
    expected_hold_minutes_min: int
    expected_hold_minutes_max: int
    # Structure source
    primary_timeframe: str           # Which timeframe provides structure
    sl_source: str                   # Where stop loss comes from
    # Target characteristics
    typical_rr: float                # Expected reward:risk ratio
    # Requirements
    min_htf_alignment: float         # Minimum HTF alignment score (0.0–1.0) to qualify
    requires_trend: bool             # Must H4 regime be TRENDING?
    requires_bos: bool               # Must H1 BOS be confirmed?
    requires_structure_quality: float # Minimum M15 structure quality


# ═══════════════════════════════════════════════════════════════════════════════
# HORIZON PROFILE DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════

SCALP = HorizonProfile(
    name="SCALP",
    expected_hold_minutes_min=2,
    expected_hold_minutes_max=45,
    primary_timeframe="M5",
    sl_source="M5_CANDLE_GEOMETRY",
    typical_rr=2.0,
    min_htf_alignment=0.0,           # No HTF requirement for scalp
    requires_trend=False,
    requires_bos=False,
    requires_structure_quality=0.0,
)

INTRADAY = HorizonProfile(
    name="INTRADAY",
    expected_hold_minutes_min=60,
    expected_hold_minutes_max=480,
    primary_timeframe="M15/H1",
    sl_source="M15_STRUCTURE",
    typical_rr=3.0,
    min_htf_alignment=0.5,           # Moderate HTF support needed
    requires_trend=False,            # Works in trends and ranges (if structure clear)
    requires_bos=False,
    requires_structure_quality=0.5,
)

EXTENDED = HorizonProfile(
    name="EXTENDED",
    expected_hold_minutes_min=480,
    expected_hold_minutes_max=4320,   # Up to 3 days
    primary_timeframe="H1/H4",
    sl_source="H1_STRUCTURE",
    typical_rr=4.0,
    min_htf_alignment=0.7,           # Strong HTF alignment required
    requires_trend=True,             # Only viable in trending regime
    requires_bos=True,               # Must have H1 structural confirmation
    requires_structure_quality=0.6,
)

# Registry for iteration
ALL_PROFILES = {
    "SCALP": SCALP,
    "INTRADAY": INTRADAY,
    "EXTENDED": EXTENDED,
}
