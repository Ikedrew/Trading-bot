"""
MTF Calibration — Data Loader.

Fetches historical candle data from MT5 for all required timeframes.
Pure data access — no analysis, no decisions.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root on path
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import MetaTrader5 as mt5
from data.mt5_data import Candle


# MT5 timeframe constants
TF_M5 = mt5.TIMEFRAME_M5
TF_M15 = mt5.TIMEFRAME_M15
TF_H1 = mt5.TIMEFRAME_H1
TF_H4 = mt5.TIMEFRAME_H4


def fetch_candles(symbol: str, timeframe: int, count: int) -> list[Candle]:
    """
    Fetch candles from MT5 for a given symbol and timeframe.
    Requires MT5 to be initialized before calling.
    """
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"Failed to fetch {symbol} TF={timeframe}: {mt5.last_error()}")
    return [Candle.from_mt5_row(rates[i]) for i in range(len(rates))]


def load_all_timeframes(
    symbol: str,
    m5_count: int = 2000,
    h4_count: int = 100,
    h1_count: int = 200,
    m15_count: int = 200,
) -> dict[str, list[Candle]]:
    """
    Load all timeframes for a symbol. Returns dict keyed by timeframe name.
    MT5 must be initialized before calling.
    """
    return {
        "M5": fetch_candles(symbol, TF_M5, m5_count),
        "H4": fetch_candles(symbol, TF_H4, h4_count),
        "H1": fetch_candles(symbol, TF_H1, h1_count),
        "M15": fetch_candles(symbol, TF_M15, m15_count),
    }


def init_mt5(terminal_path: str | None = None) -> bool:
    """Initialize MT5 connection."""
    if terminal_path:
        return mt5.initialize(path=terminal_path)
    return mt5.initialize()


def shutdown_mt5() -> None:
    """Shutdown MT5 connection."""
    mt5.shutdown()
