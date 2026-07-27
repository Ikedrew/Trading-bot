"""MT5 connection and OHLCV access — no strategy or execution logic."""

from __future__ import annotations

import json
import logging
import os
import time as _time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from core.mt5_timeout import mt5_call

logger = logging.getLogger(__name__)


# ─── TICK TIMESTAMP NORMALISATION (broker server → UTC) ───────────────────────
# MT5 broker servers may run at a non-UTC timezone (e.g., Pepperstone = UTC+2/+3).
# The tick.time field is a Unix timestamp from the broker's clock.
# We measure the offset on first tick and subtract to produce UTC.

_TICK_UTC_OFFSET_SECONDS: int = 0
_TICK_OFFSET_MEASURED: bool = False


def _measure_tick_offset(mt5_tick_time: int) -> int:
    """
    Measure broker server clock offset from UTC.

    Compares the MT5 tick timestamp against system UTC time.time().
    Rounds to nearest hour (broker offsets are always whole hours).

    Returns offset in seconds (positive = broker ahead of UTC).
    """
    utc_now = int(_time.time())
    raw_delta = mt5_tick_time - utc_now
    # Round to nearest hour
    offset_hours = round(raw_delta / 3600)
    return offset_hours * 3600


def _normalise_tick_time(raw_tick_time: int) -> int:
    """
    Convert broker-local tick timestamp to UTC.

    On first call, measures the offset. Subsequent calls apply cached offset.
    """
    global _TICK_UTC_OFFSET_SECONDS, _TICK_OFFSET_MEASURED

    if not _TICK_OFFSET_MEASURED:
        _TICK_UTC_OFFSET_SECONDS = _measure_tick_offset(raw_tick_time)
        _TICK_OFFSET_MEASURED = True
        logger.info(
            "[BROKER_OFFSET] measured=%+ds (%+dh) — tick timestamps normalised to UTC",
            _TICK_UTC_OFFSET_SECONDS,
            _TICK_UTC_OFFSET_SECONDS // 3600,
        )

    return raw_tick_time - _TICK_UTC_OFFSET_SECONDS


def _is_centralised_init() -> bool:
    """Check if MT5 lifecycle is centrally owned (by main.py)."""
    try:
        from core import config
        return bool(getattr(config, "MT5_CENTRALISED_INIT", True))
    except ImportError:
        return False


@dataclass(frozen=True)
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    tick_volume: int

    @staticmethod
    def from_mt5_row(row: Any) -> "Candle":
        return Candle(
            time=int(row["time"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            tick_volume=int(row["tick_volume"]),
        )


def _rows_to_candles(rates: Any) -> list[Candle]:
    return [Candle.from_mt5_row(rates[i]) for i in range(len(rates))]


def _get_last_cached_timestamp(filepath: Path) -> int | None:
    """
    Read the last valid JSONL line from a replay file and return its candle timestamp.

    Reads from the end of the file without loading the entire file into memory.
    Returns None if file does not exist or contains no valid records.
    Never raises — returns None on any failure.
    """
    try:
        if not filepath.exists() or filepath.stat().st_size == 0:
            return None

        # Read last non-empty line by seeking from end
        with open(filepath, "rb") as f:
            # Seek to end
            f.seek(0, 2)
            file_size = f.tell()
            if file_size == 0:
                return None

            # Read backwards to find last newline-terminated line
            pos = file_size - 1
            # Skip trailing newline if present
            f.seek(pos)
            if f.read(1) == b"\n":
                pos -= 1

            # Scan backwards for the start of the last line
            while pos >= 0:
                f.seek(pos)
                if f.read(1) == b"\n":
                    break
                pos -= 1

            # Read from pos+1 to end (the last line)
            f.seek(pos + 1)
            last_line = f.read().decode("utf-8").strip()

        if not last_line:
            return None

        record = json.loads(last_line)
        return int(record.get("ts", 0)) or None

    except Exception:
        return None


def _persist_candles_to_cache(symbol: str, timeframe: int, candles: list[Candle]) -> None:
    """
    Persist only NEW candles to disk (incremental append-only).

    Deduplication: reads the last persisted timestamp for this symbol/timeframe/day
    and only appends candles with timestamp > last_saved. Guarantees idempotency
    across bot restarts and overlapping fetches.

    Schema (one line per candle):
        {"ts": 1719388800, "o": 1.07423, "h": 1.07456, "l": 1.07401, "c": 1.07445, "v": 342}

    Path: replay_data/{SYMBOL}/{TIMEFRAME}/{YYYY-MM-DD}.jsonl

    Never raises — failures are logged and swallowed.
    """
    try:
        from core import config as _cfg
        if not getattr(_cfg, "ENABLE_CANDLE_REPLAY_CACHE", False):
            return

        if not candles:
            return

        from core.clock import now_date, candle_ts_to_ms

        cache_dir = getattr(_cfg, "REPLAY_CACHE_DIR", "replay_data")
        date_str = now_date()

        # Build path: replay_data/EURUSD_SB/5/2026-06-26.jsonl
        out_dir = Path(cache_dir) / symbol / str(timeframe)
        out_dir.mkdir(parents=True, exist_ok=True)
        filepath = out_dir / f"{date_str}.jsonl"

        # Get last persisted timestamp for deduplication
        last_ts = _get_last_cached_timestamp(filepath)

        # Always exclude the last candle in the array (it may still be forming).
        # MT5's copy_rates_from_pos includes the current forming bar as the last element.
        closed_candles = candles[:-1] if len(candles) > 1 else []
        if not closed_candles:
            return

        # Filter to only new candles (strictly newer than last persisted)
        if last_ts is not None:
            new_candles = [c for c in closed_candles if candle_ts_to_ms(c.time) > last_ts]
        else:
            # First write of the day — persist all closed candles
            new_candles = closed_candles

        if not new_candles:
            return

        # Append one JSONL record per candle (compact schema, ts in UTC millis)
        with open(filepath, "a", encoding="utf-8") as f:
            for c in new_candles:
                record = {
                    "ts": candle_ts_to_ms(c.time),
                    "o": c.open,
                    "h": c.high,
                    "l": c.low,
                    "c": c.close,
                    "v": c.tick_volume,
                }
                f.write(json.dumps(record, separators=(",", ":")) + "\n")

                # Unified event bus
                try:
                    from core.event_stream import emit_candle
                    emit_candle(symbol, record, source="mt5_data")
                except Exception:
                    pass

        logger.debug(
            "[DATA_REPLAY] incremental_persist symbol=%s timeframe=%d new_candles=%d "
            "last_ts=%s file=%s",
            symbol, timeframe, len(new_candles),
            last_ts if last_ts else "none",
            filepath,
        )

    except Exception as exc:
        logger.warning("[DATA_REPLAY] failed_to_persist symbol=%s error=%s", symbol, exc)


class MT5DataFeed:
    def __init__(self, symbol_hint: str) -> None:
        self._symbol_hint = symbol_hint

    def connect(self) -> None:
        if _is_centralised_init():
            logger.debug("[DATA_CONNECT] centralised init — skipping local connect")
            return

        from core import config as _cfg
        mt5_path = getattr(_cfg, "MT5_TERMINAL_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe")

        logger.debug("[DATA_CONNECT] initialising MT5 connection path=%s", mt5_path)
        result = mt5.initialize(path=mt5_path)

        if not result:
            err = mt5.last_error()
            logger.warning("[DATA_CONNECT] initialise_failed attempt=1 retrying_in_s=1 error=%s", err)
            _time.sleep(1.0)

            result = mt5.initialize(path=mt5_path)
            if not result:
                err = mt5.last_error()
                logger.error("[DATA_CONNECT] initialise_failed attempt=2 giving_up=true error=%s", err)
                raise RuntimeError(f"MT5 initialize failed: {err}")

            logger.debug("[DATA_CONNECT] initialise_retry_success attempt=2 version=%s", mt5.version())
            return

        logger.debug("[DATA_CONNECT] success version=%s", mt5.version())

    def disconnect(self) -> None:
        if _is_centralised_init():
            return
        mt5.shutdown()
        logger.debug("[DATA_DISCONNECT] MT5 shutdown (local ownership)")

    def resolve_symbol(self) -> str:
        """
        Resolve symbol hint to exact MT5 symbol name.

        Resolution order:
            1. Exact match (case-sensitive) against available symbols
            2. Case-insensitive exact match
            3. Fail with ValueError (no fuzzy/substring matching)

        Never silently resolves ambiguous symbols.
        """
        symbols = mt5.symbols_get()
        if not symbols:
            raise RuntimeError(f"No symbols available: {mt5.last_error()}")

        hint = self._symbol_hint

        # 1. Exact match (case-sensitive)
        for s in symbols:
            if s.name == hint:
                if not mt5.symbol_select(s.name, True):
                    raise RuntimeError(f"symbol_select failed for {s.name}: {mt5.last_error()}")
                logger.debug("[DATA_SYMBOL] requested=%s resolved=%s match=exact", hint, s.name)
                return s.name

        # 2. Case-insensitive exact match
        hint_upper = hint.upper()
        for s in symbols:
            if s.name.upper() == hint_upper:
                if not mt5.symbol_select(s.name, True):
                    raise RuntimeError(f"symbol_select failed for {s.name}: {mt5.last_error()}")
                logger.debug("[DATA_SYMBOL] requested=%s resolved=%s match=case_insensitive", hint, s.name)
                return s.name

        # 3. No match — fail explicitly
        logger.error("[DATA_SYMBOL] resolution failed requested=%s available_count=%d", hint, len(symbols))
        raise ValueError(
            f"Symbol {hint!r} not found in MT5 (exact match required). "
            f"Available symbols: {len(symbols)} total. Check config.SYMBOLS for typos."
        )

    def copy_rates_closed(
        self,
        symbol: str,
        timeframe: int,
        count: int,
    ) -> list[Candle]:
        """Return last `count` bars (last bar may still be forming)."""
        t0 = _time.perf_counter()
        rates = mt5_call(mt5.copy_rates_from_pos, symbol, timeframe, 0, count)
        latency_ms = int((_time.perf_counter() - t0) * 1000)

        if rates is None or len(rates) == 0:
            raise RuntimeError(f"copy_rates_from_pos failed: {mt5.last_error()}")

        returned = len(rates)
        if returned < count:
            logger.warning("[DATA_FETCH] symbol=%s bars_requested=%d bars_returned=%d (fewer than expected)", symbol, count, returned)
        else:
            logger.debug("[DATA_FETCH] symbol=%s bars=%d timeframe=%d latency_ms=%d", symbol, returned, timeframe, latency_ms)

        # Validate chronological ordering
        for i in range(returned - 1):
            if int(rates[i]["time"]) > int(rates[i + 1]["time"]):
                logger.error(
                    "[DATA_ORDER_VIOLATION] symbol=%s index=%d time[%d]=%d > time[%d]=%d",
                    symbol, i, i, int(rates[i]["time"]), i + 1, int(rates[i + 1]["time"]),
                )
                raise RuntimeError(
                    f"MT5 returned candles out of chronological order for {symbol} "
                    f"at index {i}: time={int(rates[i]['time'])} > {int(rates[i + 1]['time'])}"
                )

        candles = _rows_to_candles(rates)

        # ─── MARKET_INGEST_AUDIT ──────────────────────────────────────
        # Emits once per fetch to verify live broker data is the source.
        # Logs: timestamp delta between wall-clock and latest candle open
        # time, replay/dry-run flags, and OHLCV of the last returned bar.
        # delta_seconds: gap between wall-clock UTC and latest bar open time.
        #   M5 in active market → expected ~0–300 s (one bar duration).
        #   Values > 600 s indicate stale feed or market closure.
        try:
            from datetime import datetime, timezone as _tz
            from core import config as _audit_cfg
            _latest = rates[-1]
            _now_utc = datetime.now(_tz.utc)
            _bar_utc = datetime.fromtimestamp(int(_latest["time"]), _tz.utc)
            _delta_s = (_now_utc - _bar_utc).total_seconds()
            logger.info(
                "[MARKET_INGEST_AUDIT] %s",
                {
                    "type": "MARKET_INGEST_AUDIT",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "bars_returned": returned,
                    "now_utc": _now_utc.isoformat(),
                    "latest_candle_utc": _bar_utc.isoformat(),
                    "open": float(_latest["open"]),
                    "high": float(_latest["high"]),
                    "low": float(_latest["low"]),
                    "close": float(_latest["close"]),
                    "tick_volume": int(_latest["tick_volume"]),
                    "delta_seconds": round(_delta_s, 1),
                    "latency_ms": latency_ms,
                    "replay_mode": bool(getattr(_audit_cfg, "REPLAY_MODE", False)),
                    "dry_run": bool(getattr(_audit_cfg, "DRY_RUN", True)),
                    "ordering_ok": True,  # would have raised above if False
                    "live_feed": not bool(getattr(_audit_cfg, "REPLAY_MODE", False)),
                },
            )
        except Exception:
            pass  # Audit log must never affect data delivery
        # ─── END MARKET_INGEST_AUDIT ──────────────────────────────────

        _persist_candles_to_cache(symbol, timeframe, candles)
        return candles

    def last_tick(self, symbol: str) -> tuple[float, float, int]:
        t = mt5_call(mt5.symbol_info_tick, symbol)
        if t is None:
            raise RuntimeError(f"No tick for {symbol}: {mt5.last_error()}")

        bid, ask = float(t.bid), float(t.ask)
        tick_time = _normalise_tick_time(int(t.time))

        # Tick freshness check (observability only — does not block)
        try:
            from core import config as _cfg
            threshold = float(getattr(_cfg, "FEED_STALE_THRESHOLD_SECONDS", 10.0))
        except Exception:
            threshold = 10.0

        tick_age = _time.time() - tick_time
        if tick_age > threshold:
            logger.warning(
                "[DATA_TICK] symbol=%s tick_age_s=%.1f threshold_s=%.1f stale_tick_detected=true bid=%.5f ask=%.5f",
                symbol, tick_age, threshold, bid, ask,
            )

        return bid, ask, tick_time
