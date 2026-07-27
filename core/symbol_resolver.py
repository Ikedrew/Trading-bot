"""
Symbol Resolver — Canonical-to-Broker mapping layer.

Maps broker-agnostic canonical symbols (EURUSD) to whatever the connected
MT5 broker uses (EURUSD, EURUSD_SB, EURUSD.c, EURUSDm, etc.).

Resolution is dynamic at startup — no hardcoded suffixes, no assumptions
about broker naming conventions.

Usage:
    from core.symbol_resolver import resolve_broker_symbol, resolve_all

    resolved = resolve_broker_symbol("EURUSD")  # → "EURUSD_SB" on Pepperstone SB
    mapping = resolve_all(["EURUSD", "GBPUSD"])  # → {"EURUSD": "EURUSD_SB", ...}
"""

from __future__ import annotations

import logging
from typing import Any

import MetaTrader5 as mt5

logger = logging.getLogger(__name__)


def resolve_broker_symbol(canonical: str) -> str:
    """
    Map a canonical symbol (EURUSD) to the broker's actual MT5 symbol.

    Resolution order:
        1. Exact match (canonical IS the broker symbol)
        2. Single prefix match (canonical + broker suffix)
        3. Ambiguous → raise ValueError
        4. Not found → raise ValueError

    Args:
        canonical: Base currency pair name without broker suffix (e.g., "EURUSD")

    Returns:
        The broker's MT5 symbol name (e.g., "EURUSD_SB")

    Raises:
        RuntimeError: MT5 not connected or no symbols available
        ValueError: Symbol not found or ambiguous match
    """
    symbols = mt5.symbols_get()
    if not symbols:
        raise RuntimeError(
            f"MT5 returned no symbols — connection not ready: {mt5.last_error()}"
        )

    # 1. Exact match (broker uses bare symbol names)
    for s in symbols:
        if s.name == canonical:
            mt5.symbol_select(canonical, True)
            return canonical

    # 2. Prefix match (broker adds suffix: _SB, .c, _CFD, m, etc.)
    matches = [s.name for s in symbols if s.name.startswith(canonical)]

    if len(matches) == 1:
        resolved = matches[0]
        mt5.symbol_select(resolved, True)
        return resolved

    if len(matches) > 1:
        # Try to disambiguate: prefer shortest match (closest to canonical)
        matches_sorted = sorted(matches, key=len)
        # If shortest is canonical + single suffix token, use it
        if len(matches_sorted[0]) <= len(canonical) + 4:
            resolved = matches_sorted[0]
            mt5.symbol_select(resolved, True)
            logger.info(
                "[SYMBOL_RESOLVER] canonical=%s matched=%s (shortest of %d candidates)",
                canonical, resolved, len(matches),
            )
            return resolved
        raise ValueError(
            f"Ambiguous symbol mapping for '{canonical}': {matches}. "
            f"Add explicit mapping or check broker symbol names."
        )

    # 3. Not found
    raise ValueError(
        f"No MT5 symbol found for canonical '{canonical}'. "
        f"Available count: {len(symbols)}. Check broker supports this instrument."
    )


def resolve_all(
    canonicals: list[str],
    *,
    fail_mode: str = "skip",
) -> dict[str, str]:
    """
    Resolve all canonical symbols to broker symbols.

    Args:
        canonicals: List of canonical symbol names
        fail_mode: "skip" (log warning, continue) or "raise" (fail on first error)

    Returns:
        Dict mapping canonical → broker symbol (only successful resolutions)
    """
    mapping: dict[str, str] = {}

    for canonical in canonicals:
        try:
            resolved = resolve_broker_symbol(canonical)
            mapping[canonical] = resolved
            logger.info("[SYMBOL_MAP] canonical=%s → broker=%s", canonical, resolved)
        except (ValueError, RuntimeError) as exc:
            if fail_mode == "raise":
                raise
            logger.warning(
                "[SYMBOL_MAP] canonical=%s → FAILED: %s", canonical, exc
            )

    return mapping


def get_canonical(broker_symbol: str, canonical_list: list[str]) -> str | None:
    """
    Reverse lookup: broker symbol → canonical symbol.

    Finds which canonical symbol the broker symbol was resolved from.
    Returns None if no match found.
    """
    for canonical in canonical_list:
        if broker_symbol.startswith(canonical):
            return canonical
    return None
