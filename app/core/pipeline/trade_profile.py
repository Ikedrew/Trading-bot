"""
Trade Profile Layer — Post-entry lifecycle behaviour differentiation.

Assigns a persistent trade management profile at execution time based on
the current regime_label from the Bias FSM.

This layer ONLY affects post-entry trade management:
    - Stop loss behaviour
    - Take profit logic
    - Trailing stop rules
    - Scaling rules
    - Exit aggressiveness

This layer does NOT affect:
    - FSM / bias logic
    - Scoring / EV calculation
    - Entry selection / ranking
    - Strategy classification

Architecture:
    regime_label (from FSM) → trade_profile assignment → trade management parameters

Design: deterministic, no learning, no adaptation.
Every trade is structurally identical at entry but behaviourally unique after entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


# ─── TRADE PROFILE TYPES ─────────────────────────────────────────────────────

class TradeProfileType(str, Enum):
    """Post-entry trade management behaviour archetypes."""
    TREND_CONTINUATION = "TREND_CONTINUATION"
    MEAN_REVERSION = "MEAN_REVERSION"
    TRANSITIONAL_PROTECTION = "TRANSITIONAL_PROTECTION"
    DEFENSIVE = "DEFENSIVE"


# ─── PROFILE PARAMETERS ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class TradeProfileParams:
    """
    Immutable trade management parameters for a specific profile.

    Consumed by trade management layer AFTER entry.
    Does NOT influence entry decisions.
    """
    profile: TradeProfileType

    # Position sizing modifier (1.0 = full, 0.5 = half)
    position_size_fraction: float

    # Stop loss
    sl_buffer_multiplier: float     # Multiplier on base SL distance (1.0 = normal, 1.3 = wider)
    break_even_trigger_rr: float    # R:R at which stop moves to break-even (0 = disabled)

    # Take profit
    tp_multiplier: float            # Multiplier on base TP distance (1.0 = normal)
    partial_tp_fraction: float      # Fraction to close at first TP (0.0 = disabled)
    partial_tp_rr: float            # R:R at which partial TP triggers (0 = disabled)

    # Trailing stop
    trailing_enabled: bool
    trailing_start_rr: float        # R:R at which trailing begins
    trailing_step_atr: float        # Trail step as fraction of ATR (0.5 = half ATR)

    # Exit behaviour
    max_hold_bars: int              # Max bars before forced exit (0 = unlimited)
    exit_aggressiveness: float      # 0.0 = passive, 1.0 = aggressive exits

    # Scaling
    scaling_in_allowed: bool


# ─── PROFILE DEFINITIONS ──────────────────────────────────────────────────────

_PROFILES: dict[TradeProfileType, TradeProfileParams] = {

    TradeProfileType.TREND_CONTINUATION: TradeProfileParams(
        profile=TradeProfileType.TREND_CONTINUATION,
        position_size_fraction=1.0,
        sl_buffer_multiplier=1.3,           # Wider stops — allow trend room
        break_even_trigger_rr=1.5,          # Move to BE after 1.5R
        tp_multiplier=1.5,                  # Extended TP target
        partial_tp_fraction=0.0,            # No partial — let it run
        partial_tp_rr=0.0,
        trailing_enabled=True,
        trailing_start_rr=1.0,              # Trail from 1R
        trailing_step_atr=0.8,              # Wider trail (trend needs room)
        max_hold_bars=0,                    # No time limit
        exit_aggressiveness=0.2,            # Passive — let trend develop
        scaling_in_allowed=True,
    ),

    TradeProfileType.MEAN_REVERSION: TradeProfileParams(
        profile=TradeProfileType.MEAN_REVERSION,
        position_size_fraction=0.7,
        sl_buffer_multiplier=0.8,           # Tight stops — invalidation is clear
        break_even_trigger_rr=0.8,          # BE very early
        tp_multiplier=0.7,                  # Shorter TP — quick extraction
        partial_tp_fraction=0.5,            # Close half at first target
        partial_tp_rr=0.5,                  # Partial at 0.5R
        trailing_enabled=False,             # No trail — take profit and exit
        trailing_start_rr=0.0,
        trailing_step_atr=0.0,
        max_hold_bars=24,                   # 2 hours max on M5 (24 bars)
        exit_aggressiveness=0.9,            # Very aggressive exits
        scaling_in_allowed=False,
    ),

    TradeProfileType.TRANSITIONAL_PROTECTION: TradeProfileParams(
        profile=TradeProfileType.TRANSITIONAL_PROTECTION,
        position_size_fraction=0.5,         # Half size in unstable regimes
        sl_buffer_multiplier=1.0,           # Normal stops
        break_even_trigger_rr=0.7,          # BE faster than normal
        tp_multiplier=0.9,                  # Slightly reduced TP
        partial_tp_fraction=0.5,            # Close half early
        partial_tp_rr=0.7,                  # Partial at 0.7R
        trailing_enabled=True,
        trailing_start_rr=0.8,              # Trail earlier
        trailing_step_atr=0.5,              # Tighter trail
        max_hold_bars=36,                   # 3 hours max
        exit_aggressiveness=0.6,            # Moderately aggressive
        scaling_in_allowed=False,
    ),

    TradeProfileType.DEFENSIVE: TradeProfileParams(
        profile=TradeProfileType.DEFENSIVE,
        position_size_fraction=0.5,
        sl_buffer_multiplier=0.9,           # Slightly tighter
        break_even_trigger_rr=0.6,          # BE very early
        tp_multiplier=0.8,                  # Reduced targets
        partial_tp_fraction=0.5,            # Close half at first target
        partial_tp_rr=0.5,                  # Partial at 0.5R
        trailing_enabled=True,
        trailing_start_rr=0.5,              # Trail very early
        trailing_step_atr=0.4,              # Tight trail
        max_hold_bars=30,                   # 2.5 hours max
        exit_aggressiveness=0.7,            # Aggressive exits
        scaling_in_allowed=False,
    ),
}


# ─── REGIME → PROFILE MAPPING ─────────────────────────────────────────────────

_REGIME_TO_PROFILE: dict[str, TradeProfileType] = {
    "TRENDING_STABLE": TradeProfileType.TREND_CONTINUATION,
    "TRENDING_WEAKENING": TradeProfileType.DEFENSIVE,
    "CHOPPING": TradeProfileType.MEAN_REVERSION,
    "TRANSITIONAL": TradeProfileType.TRANSITIONAL_PROTECTION,
    "POST_FLIP_RECOVERY": TradeProfileType.TRANSITIONAL_PROTECTION,
}


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def assign_trade_profile(regime_label: str) -> TradeProfileParams:
    """
    Map regime label to trade management profile.

    Called at execution time. Returns immutable parameters that control
    post-entry trade lifecycle behaviour.

    Args:
        regime_label: Current regime classification from Bias FSM

    Returns:
        TradeProfileParams (frozen, immutable)
    """
    profile_type = _REGIME_TO_PROFILE.get(regime_label, TradeProfileType.TRANSITIONAL_PROTECTION)
    return _PROFILES[profile_type]


def get_profile_params(profile_type: TradeProfileType) -> TradeProfileParams:
    """Direct lookup by profile type."""
    return _PROFILES[profile_type]


def format_profile_narrative(params: TradeProfileParams) -> str:
    """
    Format trade profile into human-readable summary for logging.

    Purely observational. Does not influence anything.
    """
    lines = [
        f"📋 TRADE PROFILE: {params.profile.value}",
        f"  Position size:  {params.position_size_fraction:.0%}",
        f"  SL multiplier:  {params.sl_buffer_multiplier:.1f}x",
        f"  BE trigger:     {params.break_even_trigger_rr:.1f}R",
        f"  TP multiplier:  {params.tp_multiplier:.1f}x",
    ]
    if params.partial_tp_fraction > 0:
        lines.append(f"  Partial TP:     {params.partial_tp_fraction:.0%} at {params.partial_tp_rr:.1f}R")
    if params.trailing_enabled:
        lines.append(f"  Trailing:       from {params.trailing_start_rr:.1f}R, step={params.trailing_step_atr:.1f} ATR")
    if params.max_hold_bars > 0:
        lines.append(f"  Max hold:       {params.max_hold_bars} bars")
    lines.append(f"  Exit aggression:{params.exit_aggressiveness:.1f}")
    lines.append(f"  Scaling in:     {'YES' if params.scaling_in_allowed else 'NO'}")
    return "\n".join(lines)
