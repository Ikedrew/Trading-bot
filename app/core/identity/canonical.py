"""
Canonical Opportunity Identity — THE single authoritative lineage root.

CONTRACT (approved remediation design):

    canonical_opportunity_id = "{canonical_symbol}*{normalized_bar_time}*{primary_pattern}"

Properties:
    - Deterministic: same inputs -> same ID, always.
    - Replay-stable: derived ONLY from market data (symbol, bar close time,
      selected primary pattern). Never from runtime counters, wall clocks,
      session IDs, or correlation strings.
    - Available BEFORE the verdict: mintable the moment symbol + bar time +
      primary pattern coexist (i.e. at/after pattern selection, before
      EXECUTE/NO_TRADE resolution). NO_TRADE opportunities therefore carry
      the same root as EXECUTE ones.
    - Shared by live and shadow branches: both branches derive it from the
      identical inputs.
    - Immutable once created: never regenerated downstream.
    - Timestamp normalization is MANDATORY: int(float(bar_time)) so that
      int-typed and float-typed bar times produce identical IDs.

IDENTITY ROLES (authoritative):
    canonical_opportunity_id   canonical lineage ROOT
    entity_id                  observation-level derived alias / compatibility
    shadow_id                  individual shadow child ID
    decision_id                individual decision/execution-attempt child ID
    correlation_id             technical tracing ID only — NEVER a join key
    cycle_id                   runtime/diagnostic metadata only
    order/deal/position        broker execution IDs only
    observation_id             NOT a canonical lineage root (retired role)
    HORIZON-*                  retired as an active lineage identity

This module OWNS minting. No downstream component may construct a canonical
opportunity ID by string formatting; always import this function.
"""

from __future__ import annotations


def _normalize_symbol(symbol: str) -> str:
    """Canonicalise the symbol token (stable, case-insensitive)."""
    return str(symbol or "").strip().upper()


def _normalize_bar_time(bar_time) -> int:
    """
    Normalize any numeric bar-time representation to integer Unix seconds.

    Mandatory normalization: int(float(bar_time)) — guarantees that an
    int-typed ``1784800000`` and a float-typed ``1784800000.0`` mint the
    SAME canonical ID.
    """
    return int(float(bar_time))


def _normalize_pattern(pattern: str) -> str:
    """Canonicalise the primary-pattern token."""
    return str(pattern or "").strip().upper()


def make_canonical_opportunity_id(
    *,
    symbol: str,
    bar_time,
    pattern: str,
) -> str:
    """
    Mint THE canonical opportunity lineage ID.

    Args:
        symbol: Trading symbol (e.g. "EURUSD", "EURUSD_SB" accepted;
                normalised to uppercase, whitespace-stripped).
        bar_time: Bar close timestamp, int or float (normalised to int seconds).
        pattern: Primary selected pattern name (e.g. "TWEEZER_TOP").

    Returns:
        Canonical opportunity ID string, e.g. "EURUSD*1784800000*TWEAZER_TOP".

        Empty string if symbol or pattern is empty — callers must treat an
        empty canonical ID as "lineage not established" (e.g. pre-engine gate
        blocks where no pattern exists).
    """
    sym = _normalize_symbol(symbol)
    pat = _normalize_pattern(pattern)
    if not sym or not pat:
        return ""
    bt = _normalize_bar_time(bar_time)
    return f"{sym}*{bt}*{pat}"


def canonical_opportunity_id_from_parts(
    *,
    symbol: str,
    bar_time,
    pattern: str,
) -> str:
    """Alias of :func:`make_canonical_opportunity_id` (readable call sites)."""
    return make_canonical_opportunity_id(symbol=symbol, bar_time=bar_time, pattern=pattern)


def mint_observation_id(
    *,
    symbol: str,
    bar_time,
    timeframe: str,
) -> str:
    """
    Mint the canonical observation identity.

    This is NOT the canonical lineage root — that role belongs to
    canonical_opportunity_id. The observation_id is a bar-level identifier
    used for tracing and debugging.

    Args:
        symbol: Trading symbol (e.g. "EURUSD").
        bar_time: Bar close timestamp, int or float (normalised to int seconds).
        timeframe: Timeframe string (e.g. "M5", "H1", "D1").

    Returns:
        Observation ID string, e.g. "EURUSD*1784800000*M5".

        Empty string if symbol or timeframe is empty.
    """
    sym = _normalize_symbol(symbol)
    tf = str(timeframe or "").strip().upper()
    if not sym or not tf:
        return ""
    bt = _normalize_bar_time(bar_time)
    return f"{sym}.{tf}.{bt}"


def observation_id_from_bar(
    *,
    symbol: str,
    bar_time,
    timeframe: str,
) -> str:
    """Alias of :func:`mint_observation_id` (readable call sites)."""
    return mint_observation_id(symbol=symbol, bar_time=bar_time, timeframe=timeframe)


__all__ = [
    "make_canonical_opportunity_id",
    "canonical_opportunity_id_from_parts",
    "mint_observation_id",
    "observation_id_from_bar",
]
