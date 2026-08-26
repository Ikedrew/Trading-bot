"""
Bar Provider — Bar fetching, normalisation, classification, and deduplication.

Obtains the current bar data for a symbol: fetches candles from MT5, selects
the closed bar, converts timestamps, classifies feed health, detects duplicate
bars, and manages stale data monitoring.

This module OWNS:
    - Retrieving latest bar data from MT5 feed
    - Converting timestamps (broker-local → UTC)
    - Normalising bar format (selecting closed bar index)
    - Duplicate bar detection (dedup via last_closed_time)
    - Feed state classification (HEALTHY/SLOW/SUSPICIOUS/FEED_STALE)
    - Returning BarResult

This module does NOT own:
    - Strategy decisions
    - Feature calculation
    - Pattern detection
    - Risk checks
    - Trade execution
    - Observer notification
    - Cycle control (no sleep/continue/break — returns None to signal skip)
    - Runtime decisions

Design: returns BarResult on success, None on skip. Never raises to caller.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from core.runtime.runtime_utils import _closed_bar_index

logger = logging.getLogger(__name__)


# ─── RESULT TYPE ──────────────────────────────────────────────────────────────

@dataclass
class BarResult:
    """Result of bar fetching and validation — all downstream values."""
    candles: Any
    """Full candle array from MT5."""
    closed_i: int
    """Index of the closed bar."""
    closed_time: int
    """Raw broker-local bar close timestamp (used for entity_id, dedup)."""
    closed_time_utc: int
    """UTC-converted bar close timestamp (used for age comparisons)."""
    feed_state: str
    """Feed health classification: HEALTHY, SLOW, SUSPICIOUS."""
    is_new_bar: bool
    """Whether this is a genuinely new bar (not duplicate)."""


# ─── BAR PROVIDER ─────────────────────────────────────────────────────────────

class BarProvider:
    """
    Fetches, validates, and deduplicates bar data for a symbol.

    Encapsulates:
        1. Candle fetch from MT5 feed
        2. Closed bar index selection
        3. Diagnostic prints (candle selection visibility)
        4. UTC timestamp conversion
        5. Shadow trade evaluation (fire-and-forget, independent lifecycle)
        6. Feed state classification
        7. Feed stale hard-block
        8. Bar deduplication (stale counter management)
        9. Stale data monitor (candle freshness)
        10. State update (last_closed_time, iterations)

    Usage:
        provider = BarProvider(config)
        result = provider.fetch_bar(sym_state)
        if result is None:
            continue  # Symbol skipped this cycle
    """

    def __init__(self, config: Any) -> None:
        self._config = config

    def fetch_bar(self, sym_state: Any) -> BarResult | None:
        """
        Fetch and validate bar data for a symbol.

        Returns:
            BarResult if a valid new bar is available for processing.
            None if the symbol should be skipped this cycle (fetch fail,
            no valid bar, feed stale, duplicate bar, candle critically stale).

        Never raises. All errors result in None (skip symbol).
        """
        # ─── 1. CANDLE FETCH ──────────────────────────────────────────
        try:
            candles = sym_state.feed.copy_rates_closed(
                sym_state.symbol, self._config.TIMEFRAME, self._config.CANDLE_COUNT
            )
        except RuntimeError:
            logger.info("[LIVE_SCANNER] %s candle fetch failed — skipping", sym_state.symbol)
            return None

        # ─── 2. BAR INDEX SELECTION ───────────────────────────────────
        closed_i = _closed_bar_index(candles)
        if closed_i is None:
            return None
        closed_time = candles[closed_i].time

        # ─── 3. DIAGNOSTIC: candle selection visibility ───────────────
        if len(candles) >= 3:
            print(f"[CANDLE SELECT] {sym_state.symbol} | [-3]={candles[-3].time} [-2]={candles[-2].time} [-1]={candles[-1].time} | closed_i={closed_i} selected={closed_time} last={sym_state.last_closed_time}")

        # ─── 4. UTC CONVERSION ────────────────────────────────────────
        try:
            from data.mt5_data import _TICK_UTC_OFFSET_SECONDS
            _closed_time_utc = closed_time - _TICK_UTC_OFFSET_SECONDS
        except Exception:
            _closed_time_utc = closed_time  # Fallback: use raw if offset unavailable

        # ─── 5. SHADOW TRADE EVALUATE (fire-and-forget, independent) ──
        try:
            if getattr(self._config, "SHADOW_RUNTIME_V2_ENABLED", False):
                # NEW Shadow Runtime: evaluation is keyed on the authoritative
                # closed-bar boundary (bar_time watermark), independent of
                # poll count and of any live decision outcome.
                from core.shadow.integration import evaluate_closed_bar

                evaluate_closed_bar(
                    symbol=sym_state.symbol,
                    bar_time=float(closed_time),
                    bar_high=candles[closed_i].high,
                    bar_low=candles[closed_i].low,
                    bar_close=candles[closed_i].close,
                    bar_index=closed_i,
                )
            else:
                from core.shadow_trades import get_shadow_engine
                get_shadow_engine().evaluate_bar(
                    symbol=sym_state.symbol,
                    bar_high=candles[closed_i].high,
                    bar_low=candles[closed_i].low,
                    bar_close=candles[closed_i].close,
                    bar_time=float(closed_time),
                    bar_index=closed_i,
                )
        except Exception:
            pass  # Shadow engine must never affect live pipeline

        # ─── 5b. RESEARCH SHADOW TRADE EVALUATE (fire-and-forget) ─────
        try:
            from core.research_assessment.research_shadow_engine import evaluate_research_bar
            evaluate_research_bar(
                symbol=sym_state.symbol,
                bar_high=candles[closed_i].high,
                bar_low=candles[closed_i].low,
                bar_close=candles[closed_i].close,
                bar_time=float(closed_time),
                bar_index=closed_i,
            )
        except Exception:
            pass  # Research shadow must never affect live pipeline

        # ─── 6. FEED STATE CLASSIFICATION ─────────────────────────────
        _bar_age_s = int(time.time()) - _closed_time_utc
        if _bar_age_s < 600:
            _feed_state = "HEALTHY"
        elif _bar_age_s < 1200:
            _feed_state = "SLOW"
        elif _bar_age_s < 1800:
            _feed_state = "SUSPICIOUS"
        else:
            _feed_state = "FEED_STALE"
        print(f"[MARKET STATE] symbol={sym_state.symbol} | bar_age={_bar_age_s}s | feed={_feed_state} | closed_time={closed_time}")

        # ─── 7. FEED STALE HARD BLOCK ─────────────────────────────────
        if _feed_state == "FEED_STALE":
            if not getattr(sym_state, '_feed_stale_alerted', False):
                print(f"[FEED BLOCKED] {sym_state.symbol} | bar_age={_bar_age_s}s | feed is STALE - skipping until fresh data arrives")
                try:
                    from core.discord_notifier import send_discord
                    send_discord("errors", f"🚨 **FEED STALE** | `{sym_state.symbol}` | bar_age={_bar_age_s}s | pipeline blocked")
                except Exception:
                    pass
                sym_state._feed_stale_alerted = True
            return None
        else:
            sym_state._feed_stale_alerted = False

        # ─── 8. DEBUG: candle feed progression ────────────────────────
        if len(candles) >= 5:
            from datetime import datetime as _dt_dbg, timezone as _tz_dbg
            _last5 = candles[-5:]
            _ts_list = " -> ".join(_dt_dbg.fromtimestamp(c.time, tz=_tz_dbg.utc).strftime("%H:%M") for c in _last5)
            print(f"[CANDLE FEED] {sym_state.symbol} | last 5 bars: {_ts_list} | closed_i={closed_i}")

        # ─── 9. BAR DEDUPLICATION ─────────────────────────────────────
        _is_new_bar = (sym_state.last_closed_time != closed_time)

        def _fmt_ts(ts: int) -> str:
            from datetime import datetime as _dt, timezone as _tz
            return _dt.fromtimestamp(ts, tz=_tz.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        _gap_m = (closed_time - sym_state.last_closed_time) // 60 if sym_state.last_closed_time else 0
        print(f"[BAR CHECK] symbol={sym_state.symbol} latest={closed_time} ({_fmt_ts(closed_time)}) last={sym_state.last_closed_time} ({_fmt_ts(sym_state.last_closed_time) if sym_state.last_closed_time else 'NONE'}) gap={_gap_m}m new_bar={_is_new_bar}")

        if sym_state.last_closed_time == closed_time:
            # BAR PROGRESSION VALIDATOR (stale counter)
            _stale_count = getattr(sym_state, '_bar_stale_counter', 0) + 1
            sym_state._bar_stale_counter = _stale_count

            # Classification: 0-2 normal, 3-50 low activity, >50 feed frozen
            if _stale_count == 10:
                print(f"[BAR WAIT] {sym_state.symbol} | stale_counter={_stale_count} | normal inter-bar waiting")
            elif _stale_count == 50:
                print(f"[BAR SLOW] {sym_state.symbol} | stale_counter={_stale_count} | extended wait - low activity or session gap")
            elif _stale_count > 50 and _stale_count % 50 == 0:
                _bar_age_s = time.time() - _closed_time_utc
                print(f"[BAR STALL] {sym_state.symbol} | stale_counter={_stale_count} | bar_age={_bar_age_s:.0f}s | [WARN] MT5 FEED MAY BE FROZEN")
                try:
                    from core.discord_notifier import send_discord
                    send_discord("errors", f"⚠️ **FEED STALL** | `{sym_state.symbol}` | stale_counter={_stale_count} | bar_age={_bar_age_s:.0f}s | MT5 may be disconnected")
                except Exception:
                    pass

            # FEED STALENESS CHECK (age-based)
            _bar_age_s = time.time() - _closed_time_utc
            if _bar_age_s > 600 and sym_state.iterations > 0:
                if not getattr(sym_state, '_stale_warned', False):
                    print(f"[FEED STALE WARNING] {sym_state.symbol} | last_closed_bar is {_bar_age_s:.0f}s old | MT5 may be disconnected or market closed")
                    sym_state._stale_warned = True
            elif _bar_age_s <= 600:
                sym_state._stale_warned = False
            return None  # No new bar for this symbol

        # New bar detected — reset stale counter
        sym_state._bar_stale_counter = 0

        # ─── 10. STALE DATA MONITOR: candle freshness ─────────────────
        try:
            _candle_stale = sym_state.stale_monitor.on_candle(_closed_time_utc, time.time())
        except Exception:
            logger.warning("[STALE_DATA] symbol=%s monitor_error=on_candle — fail-safe skip", sym_state.symbol)
            return None

        if _candle_stale.is_stale and _candle_stale.escalation_level >= 2:
            logger.warning(
                "[STALE_DATA] symbol=%s type=CANDLE stale_seconds=%.1f "
                "level=%d action=%s",
                sym_state.symbol, _candle_stale.stale_duration_seconds,
                _candle_stale.escalation_level, _candle_stale.action,
            )
            return None  # Skip evaluation — candle data is critically stale

        # ─── STATE UPDATE ─────────────────────────────────────────────
        sym_state.last_closed_time = closed_time
        sym_state.iterations += 1

        return BarResult(
            candles=candles,
            closed_i=closed_i,
            closed_time=closed_time,
            closed_time_utc=_closed_time_utc,
            feed_state=_feed_state,
            is_new_bar=True,
        )
