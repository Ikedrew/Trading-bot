"""
Single source of truth for timeframe constant → seconds mapping.

Pure Python. No MT5 dependency. Immutable.
Keys are MQL5 integer constants (same values MT5 Python module exposes).
"""

from __future__ import annotations

# MQL5 timeframe integer constants → duration in seconds
TIMEFRAME_SECONDS: dict[int, int] = {
    1: 60,         # M1
    2: 120,        # M2
    3: 180,        # M3
    4: 240,        # M4
    5: 300,        # M5
    6: 360,        # M6
    10: 600,       # M10
    12: 720,       # M12
    15: 900,       # M15
    20: 1200,      # M20
    30: 1800,      # M30
    16385: 3600,   # H1
    16386: 7200,   # H2
    16387: 10800,  # H3
    16388: 14400,  # H4
    16390: 21600,  # H6
    16392: 28800,  # H8
    16396: 43200,  # H12
    16408: 86400,  # D1
    32769: 604800, # W1
    49153: 2592000,  # MN
}

# MQL5 timeframe integer constants → canonical textual names.
# This is the single source of truth for the canonical name used by the CANDLE
# event contract so the full identity (symbol, timeframe, bar_timestamp) is
# reconstructable. Values match MetaTrader5's module constants.
TIMEFRAME_NAMES: dict[int, str] = {
    1: "M1", 2: "M2", 3: "M3", 4: "M4", 5: "M5", 6: "M6",
    10: "M10", 12: "M12", 15: "M15", 20: "M20", 30: "M30",
    16385: "H1", 16386: "H2", 16387: "H3", 16388: "H4",
    16390: "H6", 16392: "H8", 16396: "H12",
    16408: "D1", 32769: "W1", 49153: "MN",
}


def timeframe_name(timeframe: int) -> str:
    """Return the canonical textual timeframe name for an MQL5 constant.

    Unknown constants fall back to the raw integer as a string (explicit,
    never silently guessed as any particular timeframe such as M5).
    """
    return TIMEFRAME_NAMES.get(int(timeframe), str(int(timeframe)))
