"""Instrument Utilities — Unified pip/point abstraction for FX, indices, and commodities.

Provides a single source of truth for price-unit conversions across all
instrument classes supported by the system.

Usage:
    from core.instrument_utils import get_pip_size, get_instrument_class

    pip_size = get_pip_size("EURUSD")   # → 0.0001
    pip_size = get_pip_size("USDJPY")   # → 0.01
    pip_size = get_pip_size("NAS100")   # → 1.0
    pip_size = get_pip_size("US500")    # → 0.1
    pip_size = get_pip_size("XAUUSD")   # → 0.01
"""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple


class InstrumentClass(str, Enum):
    """Broad instrument classification."""
    FX_MAJOR = "FX_MAJOR"
    FX_JPY = "FX_JPY"
    INDEX = "INDEX"
    COMMODITY = "COMMODITY"
    CRYPTO = "CRYPTO"
    UNKNOWN = "UNKNOWN"


class InstrumentProfile(NamedTuple):
    """Static properties for a given instrument class."""
    pip_size: float             # Minimum meaningful price unit
    typical_spread_pips: float  # Normal session spread in pips/points
    typical_stop_pips: float    # Standard M5 stop distance in pips/points
    session_start_utc: int      # Hour (UTC) market typically active from
    session_end_utc: int        # Hour (UTC) market typically active until
    is_24h: bool                # True if trades ~24h (FX, Gold, Crypto)


# ═══════════════════════════════════════════════════════════════
# INSTRUMENT CLASSIFICATION
# ═══════════════════════════════════════════════════════════════

# Index symbol patterns — matched by prefix/containment
_INDEX_SYMBOLS = frozenset({
    "NAS100", "US500", "US30", "US2000",
    "GER40", "UK100", "FRA40", "JPN225",
    "AUS200", "SPX500", "USTEC", "DE40",
})

_COMMODITY_SYMBOLS = frozenset({
    "XAUUSD", "XAGUSD", "XTIUSD", "XBRUSD",
    "GOLD", "SILVER", "OIL",
})

_CRYPTO_SYMBOLS = frozenset({
    "BTCUSD", "ETHUSD", "LTCUSD", "XRPUSD",
})


def get_instrument_class(symbol: str) -> InstrumentClass:
    """Classify an instrument by its symbol name."""
    s = symbol.upper().rstrip("_SB").rstrip(".C")  # Strip broker suffixes

    # Check explicit sets first
    for idx_sym in _INDEX_SYMBOLS:
        if s.startswith(idx_sym) or idx_sym in s:
            return InstrumentClass.INDEX

    for cmd_sym in _COMMODITY_SYMBOLS:
        if s.startswith(cmd_sym) or cmd_sym in s:
            return InstrumentClass.COMMODITY

    for cry_sym in _CRYPTO_SYMBOLS:
        if s.startswith(cry_sym) or cry_sym in s:
            return InstrumentClass.CRYPTO

    # FX classification
    if "JPY" in s:
        return InstrumentClass.FX_JPY

    # Default: assume FX major
    return InstrumentClass.FX_MAJOR


# ═══════════════════════════════════════════════════════════════
# PIP SIZE
# ═══════════════════════════════════════════════════════════════

# Per-instrument pip sizes (override for specific symbols)
_PIP_SIZE_OVERRIDES: dict[str, float] = {
    "NAS100": 1.0,
    "US500": 0.1,
    "US30": 1.0,
    "US2000": 0.1,
    "GER40": 0.1,
    "UK100": 0.1,
    "FRA40": 0.1,
    "JPN225": 1.0,
    "AUS200": 0.1,
    "SPX500": 0.1,
    "USTEC": 1.0,
    "DE40": 0.1,
    "XAUUSD": 0.01,
    "XAGUSD": 0.001,
    "XTIUSD": 0.01,
    "XBRUSD": 0.01,
    "BTCUSD": 1.0,
    "ETHUSD": 0.1,
}

# Default pip sizes by instrument class
_PIP_SIZE_DEFAULTS: dict[InstrumentClass, float] = {
    InstrumentClass.FX_MAJOR: 0.0001,
    InstrumentClass.FX_JPY: 0.01,
    InstrumentClass.INDEX: 1.0,
    InstrumentClass.COMMODITY: 0.01,
    InstrumentClass.CRYPTO: 1.0,
    InstrumentClass.UNKNOWN: 0.0001,
}


def get_pip_size(symbol: str) -> float:
    """
    Return the pip/point size for a given symbol.

    This is the single source of truth — replaces all inline
    `0.01 if "JPY" in symbol else 0.0001` patterns.

    Args:
        symbol: Canonical or broker symbol name

    Returns:
        The minimum meaningful price unit (pip or point)
    """
    s = symbol.upper()

    # Strip common broker suffixes for lookup
    for suffix in ("_SB", ".C", "_CFD", "M", ".R"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break

    # Check explicit overrides
    for override_sym, pip_size in _PIP_SIZE_OVERRIDES.items():
        if s.startswith(override_sym) or override_sym in s:
            return pip_size

    # Fall back to class-based default
    inst_class = get_instrument_class(symbol)
    return _PIP_SIZE_DEFAULTS[inst_class]


# ═══════════════════════════════════════════════════════════════
# INSTRUMENT PROFILES
# ═══════════════════════════════════════════════════════════════

_PROFILES: dict[str, InstrumentProfile] = {
    "NAS100": InstrumentProfile(
        pip_size=1.0, typical_spread_pips=1.5, typical_stop_pips=15.0,
        session_start_utc=13, session_end_utc=21, is_24h=False,
    ),
    "US500": InstrumentProfile(
        pip_size=0.1, typical_spread_pips=4.0, typical_stop_pips=50.0,
        session_start_utc=13, session_end_utc=21, is_24h=False,
    ),
    "XAUUSD": InstrumentProfile(
        pip_size=0.01, typical_spread_pips=20.0, typical_stop_pips=100.0,
        session_start_utc=0, session_end_utc=23, is_24h=True,
    ),
    "EURUSD": InstrumentProfile(
        pip_size=0.0001, typical_spread_pips=1.0, typical_stop_pips=7.0,
        session_start_utc=7, session_end_utc=21, is_24h=True,
    ),
}


def get_instrument_profile(symbol: str) -> InstrumentProfile | None:
    """Return the instrument profile if available, else None."""
    s = symbol.upper()
    for suffix in ("_SB", ".C", "_CFD", "M", ".R"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    for key in _PROFILES:
        if s.startswith(key) or key in s:
            return _PROFILES[key]
    return None


def get_cost_to_stop_ratio(symbol: str) -> float:
    """
    Return the typical spread/stop ratio for an instrument.

    This is the key metric that determines cost pressure:
    - FX M5: ~0.20 (20% of stop eaten by spread)
    - NAS100: ~0.10 (10%)
    - XAUUSD: ~0.08-0.10

    Returns 0.20 (FX default) if profile not found.
    """
    profile = get_instrument_profile(symbol)
    if profile and profile.typical_stop_pips > 0:
        return profile.typical_spread_pips / profile.typical_stop_pips
    # FX default
    return 0.20
