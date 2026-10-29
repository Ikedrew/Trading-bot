"""
Global Correlation Key (Decision Spine ID) — Links all artefacts from one decision cycle.

A Correlation ID represents ONE complete decision cycle:
    signal → evaluation → shadow trade → outcome → analytics → governance

FORMAT:
    COR-{YYYYMMDD}-{cycle_id}-{symbol_short}-{hash_suffix}

    Example: COR-20260704-182831-EURUSD-A93F

GENERATION RULES:
    - MUST be deterministic per decision cycle
    - MUST be immutable once created
    - MUST be globally unique
    - MUST be passed through ALL layers
    - MUST NEVER be regenerated downstream

SOURCE OF TRUTH:
    Correlation ID is created ONLY at the decision entry point (live_scanner.py).
    No downstream layer may generate a new correlation_id.

PROPAGATION CONTRACT:
    Once created, correlation_id is injected into:
        1. Shadow Trade (correlation_id field)
        2. All contract violations (correlation_id field)
        3. Trade Truth record (correlation_id field)
        4. Trade Truth Graph node (correlation_id field)
        5. Discord alerts (displayed in message)
        6. Quarantine records (preserved in violations)

Usage:
    from core.correlation import generate_correlation_id, CorrelationContext

    # At decision point (live_scanner.py):
    cor_id = generate_correlation_id(cycle_id=182831, symbol="EURUSD")

    # Pass through all downstream layers:
    shadow_engine.open_trade(..., correlation_id=cor_id)
"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# CORRELATION ID GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

def generate_correlation_id(
    *,
    cycle_id: int | str,
    symbol: str,
    timestamp: float | None = None,
) -> str:
    """
    Generate a deterministic, globally unique Correlation ID.

    Format: COR-{YYYYMMDD}-{cycle_id}-{symbol_short}-{hash4}

    Args:
        cycle_id: The decision cycle number (from live_scanner)
        symbol: Trading pair (e.g., "EURUSD")
        timestamp: Optional Unix timestamp (defaults to now)

    Returns:
        Correlation ID string (e.g., "COR-20260704-182831-EURUSD-A93F")

    DETERMINISM: Same inputs → same output. This ensures replay produces
    identical correlation IDs.
    """
    if timestamp is None:
        now = datetime.now(timezone.utc)
    else:
        now = datetime.fromtimestamp(timestamp, tz=timezone.utc)

    date_str = now.strftime("%Y%m%d")

    # Short symbol: strip _SB suffix and take first 6 chars
    sym_short = symbol.replace("_SB", "").replace("_sb", "")[:6].upper()

    # Hash suffix for uniqueness (deterministic from inputs)
    raw = f"{date_str}-{cycle_id}-{symbol}-{timestamp or now.timestamp()}"
    hash_suffix = hashlib.md5(raw.encode()).hexdigest()[:4].upper()

    return f"COR-{date_str}-{cycle_id}-{sym_short}-{hash_suffix}"


# ═══════════════════════════════════════════════════════════════════════════════
# CORRELATION CONTEXT (thread-local propagation)
# ═══════════════════════════════════════════════════════════════════════════════

_context_lock = threading.Lock()
_active_correlations: dict[str, str] = {}  # symbol → current correlation_id


class CorrelationContext:
    """
    Thread-safe context manager for the active correlation ID.

    Used by live_scanner to set the current correlation_id for a decision
    cycle, then all downstream code can read it without explicit passing.

    Usage:
        with CorrelationContext(cor_id, symbol):
            # All code in this block can call get_active_correlation(symbol)
            shadow_engine.open_trade(...)

    For explicit passing (preferred for new code):
        Just pass correlation_id as a parameter.
    """

    def __init__(self, correlation_id: str, symbol: str) -> None:
        self._cor_id = correlation_id
        self._symbol = symbol
        self._prev: str | None = None

    def __enter__(self) -> "CorrelationContext":
        with _context_lock:
            self._prev = _active_correlations.get(self._symbol)
            _active_correlations[self._symbol] = self._cor_id
        return self

    def __exit__(self, *args: Any) -> None:
        with _context_lock:
            if self._prev is not None:
                _active_correlations[self._symbol] = self._prev
            else:
                _active_correlations.pop(self._symbol, None)

    @property
    def correlation_id(self) -> str:
        return self._cor_id


def get_active_correlation(symbol: str) -> str:
    """
    Get the active correlation_id for a symbol (set by CorrelationContext).

    Returns empty string if no correlation is active.
    """
    with _context_lock:
        return _active_correlations.get(symbol, "")


def set_active_correlation(symbol: str, correlation_id: str) -> None:
    """
    Explicitly set the active correlation_id for a symbol.

    Use CorrelationContext for scoped usage. This is for cases
    where context managers aren't practical.
    """
    with _context_lock:
        _active_correlations[symbol] = correlation_id


def clear_active_correlation(symbol: str) -> None:
    """Clear the active correlation_id for a symbol."""
    with _context_lock:
        _active_correlations.pop(symbol, None)
