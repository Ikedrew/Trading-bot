"""
FeatureBundle — Immutable container of pure market-derived features.

Contains ONLY signals computed from candle data and tick prices.
NEVER contains FSM state, bias counters, or scoring outputs.

Ownership: core/features/bundle.py
Produced by: feature_engine.compute_features()
Consumed by: StateSnapshot (via from_features), then voters
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeatureBundle:
    """
    Pure market-derived features computed from M5 candles + tick data.

    Every field here is:
      - Computed from raw price data (candles, bid, ask)
      - Independent of FSM state
      - Independent of scoring engine outputs
      - Deterministic (same candles → same features)
    """

    # ─── VOLATILITY FEATURES ──────────────────────────────────────────
    m5_atr_14: float              # Wilder ATR-14 at closed bar
    m5_atr_ratio: float           # current ATR / 50-bar rolling average ATR
    candle_overlap_ratio: float   # adjacent candle overlap fraction (0.0–1.0)

    # ─── EXECUTION FEATURES ───────────────────────────────────────────
    spread: float                 # ask - bid (raw execution cost)

    # ─── STRUCTURE FEATURES (M5, candle-derived, NOT FSM) ─────────────
    m5_swing_high_count: int      # swing highs in last 20 bars (2-bar L/R)
    m5_swing_low_count: int       # swing lows in last 20 bars (2-bar L/R)
    m5_structure_clarity: float   # swing separation / ATR (0.0–1.0)

    # ─── LIQUIDITY FEATURES ───────────────────────────────────────────
    last_sweep_high: float | None  # most recent sweep above prior swing high
    last_sweep_low: float | None   # most recent sweep below prior swing low
