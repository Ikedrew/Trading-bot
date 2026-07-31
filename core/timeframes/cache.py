"""
Multi-Timeframe Authority — Timeframe Cache Manager.

Per-symbol, per-timeframe snapshot store. Manages fetch scheduling,
staleness detection, and provides immutable HTFContext to the pipeline.

Ownership: core/timeframes/cache.py
Responsibilities:
    - Own all HTF snapshot storage per symbol
    - Detect new bar closures per timeframe
    - Trigger conditional fetch + analysis
    - Provide immutable HTF context to downstream pipeline
    - Prevent repeated MT5 calls within M5 cycles

Dependencies: types.py, analyzers, data.mt5_data
Must NOT import from: integration.py, engine.py
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from typing import Any

from core.timeframes.types import (
    BiasSnapshot,
    HTFContext,
    MacroSnapshot,
    RegimeSnapshot,
    StructureSnapshot,
)
from core.timeframes.h4_regime import analyze_regime
from core.timeframes.h1_bias import analyze_bias
from core.timeframes.m15_structure import analyze_structure
from data.mt5_data import Candle

logger = logging.getLogger(__name__)

# MT5 timeframe constants (from MetaTrader5 module)
_TF_M1 = 1
_TF_M15 = 15
_TF_H1 = 16385
_TF_H4 = 16388
_TF_D1 = 16408
_TF_W1 = 32769
_TF_MN = 49153

# Timeframe durations in seconds
_TF_SECONDS = {
    _TF_M1: 60,
    _TF_M15: 900,
    _TF_H1: 3600,
    _TF_H4: 14400,
    _TF_D1: 86400,
    _TF_W1: 604800,
    _TF_MN: 2592000,
}

# Staleness multiplier (snapshot stale after 3x timeframe duration)
_STALE_MULTIPLIER = 3


@dataclass
class _CacheEntry:
    """Single timeframe snapshot for one symbol."""

    bar_time: int = 0  # timestamp of the bar that produced this snapshot
    snapshot: RegimeSnapshot | BiasSnapshot | StructureSnapshot | None = None
    last_fetch_wall: float = 0.0  # wall-clock time of last successful fetch
    fetch_failures: int = 0  # consecutive fetch failure count


@dataclass
class _TimeframeConfig:
    """Configuration for a single timeframe layer."""

    tf_constant: int  # MT5 timeframe constant
    candle_count: int  # number of bars to fetch
    enabled: bool  # whether this layer is active
    name: str  # human-readable name for logging


class TimeframeCache:
    """
    Per-symbol, per-timeframe snapshot store.

    Lifecycle:
      1. Created once per symbol at scanner init
      2. update_if_needed() called at top of each M5 cycle
      3. get_htf_context() called by pipeline to read cached state
      4. Never modified by pipeline stages (read-only consumer)
    """

    def __init__(self, symbol: str, feed: Any = None, config: Any = None) -> None:
        self._symbol = symbol
        self._feed = feed  # MT5DataFeed instance
        self._config = config
        self._entries: dict[int, _CacheEntry] = {
            _TF_H4: _CacheEntry(),
            _TF_H1: _CacheEntry(),
            _TF_M15: _CacheEntry(),
            _TF_M1: _CacheEntry(),
            _TF_D1: _CacheEntry(),
            _TF_W1: _CacheEntry(),
            _TF_MN: _CacheEntry(),
        }
        self._last_price: float = 0.0  # cached for M15 structure analysis

        # Build timeframe configs from config module (with safe defaults)
        self._tf_configs: list[_TimeframeConfig] = [
            _TimeframeConfig(
                tf_constant=_TF_H4,
                candle_count=int(getattr(config, "MTF_H4_CANDLE_COUNT", 100)) if config else 100,
                enabled=bool(getattr(config, "MTF_H4_ENABLED", True)) if config else True,
                name="H4",
            ),
            _TimeframeConfig(
                tf_constant=_TF_H1,
                candle_count=int(getattr(config, "MTF_H1_CANDLE_COUNT", 200)) if config else 200,
                enabled=bool(getattr(config, "MTF_H1_ENABLED", True)) if config else True,
                name="H1",
            ),
            _TimeframeConfig(
                tf_constant=_TF_M15,
                candle_count=int(getattr(config, "MTF_M15_CANDLE_COUNT", 200)) if config else 200,
                enabled=bool(getattr(config, "MTF_M15_ENABLED", True)) if config else True,
                name="M15",
            ),
            _TimeframeConfig(
                tf_constant=_TF_M1,
                candle_count=int(getattr(config, "MTF_M1_CANDLE_COUNT", 60)) if config else 60,
                enabled=bool(getattr(config, "MTF_M1_ENABLED", False)) if config else False,
                name="M1",
            ),
            # Macro timeframes (D1/W1/MN — refresh infrequently)
            _TimeframeConfig(
                tf_constant=_TF_D1,
                candle_count=int(getattr(config, "MTF_D1_CANDLE_COUNT", 100)) if config else 100,
                enabled=bool(getattr(config, "MTF_D1_ENABLED", True)) if config else True,
                name="D1",
            ),
            _TimeframeConfig(
                tf_constant=_TF_W1,
                candle_count=int(getattr(config, "MTF_W1_CANDLE_COUNT", 52)) if config else 52,
                enabled=bool(getattr(config, "MTF_W1_ENABLED", True)) if config else True,
                name="W1",
            ),
            _TimeframeConfig(
                tf_constant=_TF_MN,
                candle_count=int(getattr(config, "MTF_MN_CANDLE_COUNT", 24)) if config else 24,
                enabled=bool(getattr(config, "MTF_MN_ENABLED", True)) if config else True,
                name="MN",
            ),
        ]

    @property
    def symbol(self) -> str:
        """Symbol this cache manages."""
        return self._symbol

    def update_if_needed(self, current_time_s: float, current_price: float = 0.0) -> None:
        """
        Check all configured timeframes for new bar closure; refresh stale entries.

        For each enabled timeframe:
          1. Lightweight new-bar check (fetch 1 bar, compare timestamp)
          2. If new bar OR stale: full fetch + analyze + cache
          3. On failure: retain previous snapshot, increment failure counter

        Args:
            current_time_s: Current wall-clock time (for staleness checks)
            current_price: Current bid price (for M15 structure analysis)
        """
        if self._feed is None:
            return  # No feed available (testing mode)

        self._last_price = current_price

        for tf_cfg in self._tf_configs:
            if not tf_cfg.enabled:
                continue
            try:
                self._update_timeframe(tf_cfg, current_time_s)
            except Exception as exc:
                entry = self._entries[tf_cfg.tf_constant]
                entry.fetch_failures += 1
                logger.warning(
                    "[MTF_CACHE_FAIL] symbol=%s tf=%s reason=%s failures=%d",
                    self._symbol, tf_cfg.name, str(exc)[:100], entry.fetch_failures,
                )

    def _update_timeframe(self, tf_cfg: _TimeframeConfig, current_time_s: float) -> None:
        """Update a single timeframe if new bar detected or stale."""
        entry = self._entries[tf_cfg.tf_constant]
        tf_seconds = _TF_SECONDS.get(tf_cfg.tf_constant, 60)

        # Check if stale (force refresh)
        is_stale = self._is_stale(entry, tf_seconds, current_time_s)

        # Lightweight new-bar detection
        new_bar = self._check_new_bar(tf_cfg.tf_constant)

        if not new_bar and not is_stale:
            return  # No update needed

        if is_stale and not new_bar:
            logger.info(
                "[MTF_CACHE_STALE] symbol=%s tf=%s bar_time=%d age_s=%.0f threshold_s=%d",
                self._symbol, tf_cfg.name, entry.bar_time,
                current_time_s - entry.bar_time if entry.bar_time > 0 else 0,
                tf_seconds * _STALE_MULTIPLIER,
            )

        # Full fetch
        candles = self._fetch_candles(tf_cfg.tf_constant, tf_cfg.candle_count)
        if not candles:
            entry.fetch_failures += 1
            logger.warning(
                "[MTF_CACHE_FAIL] symbol=%s tf=%s reason=empty_candles failures=%d",
                self._symbol, tf_cfg.name, entry.fetch_failures,
            )
            return

        # ─── CLOSED-BAR ENFORCEMENT (Option 1 — deterministic) ────────
        # MT5 copy_rates_from_pos includes the current forming bar as the
        # last element. HTF analyzers must ONLY process closed bars to
        # ensure deterministic regime/bias classification.
        # This trim is the HTF equivalent of _closed_bar_index(candles).
        if len(candles) > 1:
            candles = candles[:-1]  # Exclude forming bar
        else:
            entry.fetch_failures += 1
            return
        # ─── END CLOSED-BAR ENFORCEMENT ───────────────────────────────

        # Run analyzer
        snapshot = self._run_analyzer(tf_cfg.tf_constant, candles)
        if snapshot is None:
            entry.fetch_failures += 1
            return

        # Store result (bar_time is now the LAST CLOSED bar, not forming)
        entry.snapshot = snapshot
        entry.bar_time = candles[-1].time
        entry.last_fetch_wall = _time.time()
        entry.fetch_failures = 0

        logger.info(
            "[MTF_CACHE_UPDATE] symbol=%s tf=%s bar_time=%d snapshot_type=%s closed_bar_only=true",
            self._symbol, tf_cfg.name, entry.bar_time, type(snapshot).__name__,
        )

    def _check_new_bar(self, tf: int) -> bool:
        """
        Lightweight check: fetch 2 bars, compare last CLOSED bar timestamp.
        O(1) MT5 call per timeframe check.
        Returns True if a new closed bar is available.
        """
        try:
            latest = self._feed.copy_rates_closed(self._symbol, tf, 2)
            if not latest or len(latest) < 2:
                return False
            # latest[-1] is forming, latest[-2] is last closed
            new_time = latest[-2].time
            cached_time = self._entries[tf].bar_time
            return new_time > cached_time
        except (RuntimeError, Exception):
            return False

    def _is_stale(self, entry: _CacheEntry, tf_seconds: int, current_time_s: float) -> bool:
        """
        A snapshot is stale if:
          1. No snapshot exists (cold start)
          2. Age exceeds 3x timeframe duration
        """
        if entry.snapshot is None:
            return True
        if entry.bar_time <= 0:
            return True
        max_age = tf_seconds * _STALE_MULTIPLIER
        return (current_time_s - entry.bar_time) > max_age

    def _fetch_candles(self, tf: int, count: int) -> list[Candle]:
        """Fetch candles from MT5 feed. Returns empty list on failure."""
        try:
            return self._feed.copy_rates_closed(self._symbol, tf, count)
        except (RuntimeError, Exception):
            return []

    def _run_analyzer(self, tf: int, candles: list[Candle]) -> RegimeSnapshot | BiasSnapshot | StructureSnapshot | None:
        """Run the appropriate analyzer for the given timeframe. Returns None on failure."""
        try:
            if tf == _TF_H4:
                return analyze_regime(candles)
            elif tf == _TF_H1:
                return analyze_bias(candles)
            elif tf == _TF_M15:
                return analyze_structure(candles, self._last_price)
            elif tf == _TF_D1:
                return analyze_regime(candles)  # Reuse regime analyzer for daily
            elif tf == _TF_W1:
                return analyze_bias(candles)    # Reuse bias analyzer for weekly (swings/BOS)
            elif tf == _TF_MN:
                return analyze_regime(candles)  # Reuse regime analyzer for monthly
            else:
                return None  # M1 refinement handled separately
        except Exception as exc:
            logger.warning(
                "[MTF_CACHE_FAIL] symbol=%s tf=%d reason=analyzer_error error=%s",
                self._symbol, tf, str(exc)[:100],
            )
            return None

    def get_htf_context(self, current_price: float = 0.0) -> HTFContext:
        """
        Build immutable HTFContext from cached snapshots.

        Contract:
        - NEVER calls MT5
        - Only reads cached snapshots
        - Produces immutable HTFContext for pipeline consumption
        - Returns HTFContext with None fields for any timeframe without cached data
        """
        regime: RegimeSnapshot | None = None
        bias: BiasSnapshot | None = None
        structure: StructureSnapshot | None = None

        h4_entry = self._entries.get(_TF_H4)
        if h4_entry and isinstance(h4_entry.snapshot, RegimeSnapshot):
            regime = h4_entry.snapshot

        h1_entry = self._entries.get(_TF_H1)
        if h1_entry and isinstance(h1_entry.snapshot, BiasSnapshot):
            bias = h1_entry.snapshot

        m15_entry = self._entries.get(_TF_M15)
        if m15_entry and isinstance(m15_entry.snapshot, StructureSnapshot):
            structure = m15_entry.snapshot

        return HTFContext(macro=self._build_macro_snapshot(current_price), regime=regime, bias=bias, structure=structure)

    def _build_macro_snapshot(self, current_price: float) -> MacroSnapshot | None:
        """
        Build MacroSnapshot from cached D1/W1/MN analyzer outputs.

        Returns None if no macro data is available at all.
        Partially populated if only some timeframes have data.
        """
        mn_entry = self._entries.get(_TF_MN)
        w1_entry = self._entries.get(_TF_W1)
        d1_entry = self._entries.get(_TF_D1)

        mn_snap = mn_entry.snapshot if mn_entry else None
        w1_snap = w1_entry.snapshot if w1_entry else None
        d1_snap = d1_entry.snapshot if d1_entry else None

        # If ALL macro entries are empty, return None (no macro data yet)
        if mn_snap is None and w1_snap is None and d1_snap is None:
            return None

        # Extract monthly fields (from RegimeSnapshot)
        monthly_trend = ""
        monthly_trend_strength = 0.0
        monthly_phase = ""
        if isinstance(mn_snap, RegimeSnapshot):
            monthly_trend = mn_snap.trend_bias or "NEUTRAL"
            monthly_trend_strength = mn_snap.trend_strength
            monthly_phase = mn_snap.classification.value if mn_snap.classification else ""

        # Extract weekly fields (from BiasSnapshot)
        weekly_trend = ""
        weekly_trend_strength = 0.0
        weekly_swing_high = 0.0
        weekly_swing_low = 0.0
        weekly_bos_level = 0.0
        weekly_range_position = 0.0
        if isinstance(w1_snap, BiasSnapshot):
            weekly_trend = w1_snap.direction.value if w1_snap.direction else "NEUTRAL"
            weekly_trend_strength = w1_snap.confidence
            weekly_swing_high = w1_snap.last_swing_high or 0.0
            weekly_swing_low = w1_snap.last_swing_low or 0.0
            weekly_bos_level = w1_snap.bos_level or 0.0
            # Compute weekly range position
            if weekly_swing_high > weekly_swing_low and current_price > 0:
                if current_price <= weekly_swing_low:
                    weekly_range_position = 0.0
                elif current_price >= weekly_swing_high:
                    weekly_range_position = 1.0
                else:
                    weekly_range_position = (current_price - weekly_swing_low) / (weekly_swing_high - weekly_swing_low)

        # Extract daily fields (from RegimeSnapshot)
        daily_bias = ""
        daily_bias_strength = 0.0
        daily_swing_high = 0.0
        daily_swing_low = 0.0
        daily_range_position = 0.0
        daily_atr_ratio = 1.0
        if isinstance(d1_snap, RegimeSnapshot):
            daily_bias = d1_snap.trend_bias or "NEUTRAL"
            daily_bias_strength = d1_snap.trend_strength
            daily_atr_ratio = d1_snap.atr_ratio if d1_snap.atr_ratio > 0 else 1.0

        # Use the most recent bar_time from available entries
        bar_time = max(
            (mn_entry.bar_time if mn_entry else 0),
            (w1_entry.bar_time if w1_entry else 0),
            (d1_entry.bar_time if d1_entry else 0),
        )

        return MacroSnapshot(
            monthly_trend=monthly_trend,
            monthly_trend_strength=monthly_trend_strength,
            monthly_phase=monthly_phase,
            weekly_trend=weekly_trend,
            weekly_trend_strength=weekly_trend_strength,
            weekly_swing_high=weekly_swing_high,
            weekly_swing_low=weekly_swing_low,
            weekly_bos_level=weekly_bos_level,
            weekly_range_position=weekly_range_position,
            daily_bias=daily_bias,
            daily_bias_strength=daily_bias_strength,
            daily_swing_high=daily_swing_high,
            daily_swing_low=daily_swing_low,
            daily_range_position=daily_range_position,
            daily_atr_ratio=daily_atr_ratio,
            bar_time=bar_time,
        )

    def invalidate_all(self) -> None:
        """
        Mark all cache entries as stale (e.g., after MT5 reconnect).
        Snapshots are retained but will be refreshed on next update_if_needed().
        """
        for entry in self._entries.values():
            entry.bar_time = 0  # Forces staleness check to trigger refresh
        logger.info("[MTF_CACHE_INVALIDATE] symbol=%s entries=%d", self._symbol, len(self._entries))

    def get_entry(self, tf: int) -> _CacheEntry | None:
        """Get cache entry for a specific timeframe (for diagnostics/testing)."""
        return self._entries.get(tf)
