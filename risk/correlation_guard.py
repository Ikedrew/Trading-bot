"""
A3: Correlation Guard — Currency Exposure + Pair Clustering.

Prevents hidden portfolio concentration by tracking net currency exposure
across all open positions and blocking entries that would exceed limits.

Two mechanisms:
  1. Per-currency exposure limit (blocks stacking same currency direction)
  2. Correlation group limit (blocks too many positions in same cluster)

Stateless: reconstructs exposure from TradeStateManager on every check.
No persistence needed — derived from live position state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ─── CONFIGURATION ───────────────────────────────────────────────────────────

def _get_max_currency_exposure() -> float:
    """Max net exposure per currency (in lots). 0 = disabled."""
    try:
        from core import config
        return float(getattr(config, "MAX_CURRENCY_EXPOSURE_LOTS", 0.30))
    except ImportError:
        return 0.30


def _get_max_group_positions() -> int:
    """Max open positions in same correlation group."""
    try:
        from core import config
        return int(getattr(config, "MAX_CORRELATION_GROUP_POSITIONS", 2))
    except ImportError:
        return 2


def _get_correlation_groups() -> list[list[str]]:
    """Configurable correlation groups."""
    try:
        from core import config
        return list(getattr(config, "CORRELATION_GROUPS", _DEFAULT_GROUPS))
    except ImportError:
        return _DEFAULT_GROUPS


def _is_enabled() -> bool:
    try:
        from core import config
        return bool(getattr(config, "CORRELATION_GUARD_ENABLED", True))
    except ImportError:
        return True


_DEFAULT_GROUPS = [
    ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"],  # USD-short cluster
    ["USDJPY", "USDCHF", "USDCAD"],              # USD-long cluster
]


# ─── CURRENCY DECOMPOSITION ──────────────────────────────────────────────────

_PAIR_CURRENCIES: dict[str, tuple[str, str]] = {
    "EURUSD": ("EUR", "USD"),
    "GBPUSD": ("GBP", "USD"),
    "AUDUSD": ("AUD", "USD"),
    "NZDUSD": ("NZD", "USD"),
    "USDJPY": ("USD", "JPY"),
    "USDCHF": ("USD", "CHF"),
    "USDCAD": ("USD", "CAD"),
}


def _decompose_symbol(symbol: str) -> tuple[str, str] | None:
    """Extract (base, quote) currencies from symbol. Returns None if unknown."""
    if symbol in _PAIR_CURRENCIES:
        return _PAIR_CURRENCIES[symbol]
    # Attempt generic parse: first 3 chars = base, next 3 = quote
    clean = symbol.replace("_SB", "").replace("_sb", "")
    if len(clean) >= 6:
        return clean[:3].upper(), clean[3:6].upper()
    return None


# ─── RESULT TYPE ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CorrelationGuardResult:
    """Result of correlation guard evaluation."""
    allowed: bool
    reason: str
    exposure_map: dict[str, float] = field(default_factory=dict)
    blocking_currency: str = ""
    blocking_group: str = ""


# ─── EXPOSURE CALCULATION ─────────────────────────────────────────────────────

def compute_currency_exposure(positions: list[Any]) -> dict[str, float]:
    """
    Compute net currency exposure from open positions.

    For each position:
      BUY: base +volume, quote -volume
      SELL: base -volume, quote +volume

    Args:
        positions: list of Position objects (from TradeStateManager.positions_open())

    Returns:
        dict mapping currency code → net exposure in lots
    """
    exposure: dict[str, float] = {}

    for pos in positions:
        pair = _decompose_symbol(pos.symbol)
        if pair is None:
            continue
        base, quote = pair
        vol = pos.volume

        if pos.side.value == "BUY":
            exposure[base] = exposure.get(base, 0.0) + vol
            exposure[quote] = exposure.get(quote, 0.0) - vol
        else:
            exposure[base] = exposure.get(base, 0.0) - vol
            exposure[quote] = exposure.get(quote, 0.0) + vol

    return {k: round(v, 4) for k, v in exposure.items()}


# ─── CORRELATION GROUP CHECK ──────────────────────────────────────────────────

def _count_group_positions(symbol: str, positions: list[Any]) -> tuple[str, int]:
    """
    Count open positions in the same correlation group as symbol.
    Returns (group_name, count). If not in any group, returns ("", 0).
    """
    groups = _get_correlation_groups()
    for group in groups:
        if symbol in group:
            count = sum(1 for p in positions if p.symbol in group)
            group_name = "+".join(group[:2]) + "..."
            return group_name, count
    return "", 0


# ─── MAIN GUARD FUNCTION ──────────────────────────────────────────────────────

def check_correlation(
    *,
    symbol: str,
    direction: str,
    volume: float,
    open_positions: list[Any],
) -> CorrelationGuardResult:
    """
    Evaluate whether a new trade would violate correlation/exposure limits.

    Args:
        symbol: Symbol to trade
        direction: "BUY" or "SELL"
        volume: Intended trade volume (lots)
        open_positions: Current open positions from TradeStateManager

    Returns:
        CorrelationGuardResult with allowed=True if safe.
    """
    if not _is_enabled():
        return CorrelationGuardResult(allowed=True, reason="CORRELATION_GUARD_DISABLED")

    # ─── STEP 1: Compute current exposure ─────────────────────────
    current_exposure = compute_currency_exposure(open_positions)

    # ─── STEP 2: Simulate proposed trade's impact ──────────────────
    pair = _decompose_symbol(symbol)
    if pair is None:
        return CorrelationGuardResult(allowed=True, reason="UNKNOWN_SYMBOL_PAIR")

    base, quote = pair
    proposed_exposure = dict(current_exposure)

    if direction == "BUY":
        proposed_exposure[base] = proposed_exposure.get(base, 0.0) + volume
        proposed_exposure[quote] = proposed_exposure.get(quote, 0.0) - volume
    else:
        proposed_exposure[base] = proposed_exposure.get(base, 0.0) - volume
        proposed_exposure[quote] = proposed_exposure.get(quote, 0.0) + volume

    # ─── STEP 3: Check per-currency limit ──────────────────────────
    max_exposure = _get_max_currency_exposure()
    if max_exposure > 0:
        for currency, net in proposed_exposure.items():
            if abs(net) > max_exposure:
                logger.info(
                    "[CORRELATION_GUARD] BLOCKED reason=CURRENCY_EXPOSURE_EXCEEDED "
                    "currency=%s exposure=%.4f limit=%.4f symbol=%s direction=%s",
                    currency, net, max_exposure, symbol, direction,
                )
                return CorrelationGuardResult(
                    allowed=False,
                    reason=f"CORRELATION_BLOCKED:CURRENCY_LIMIT_{currency}",
                    exposure_map=proposed_exposure,
                    blocking_currency=currency,
                )

    # ─── STEP 4: Check correlation group limit ─────────────────────
    max_group = _get_max_group_positions()
    if max_group > 0:
        group_name, group_count = _count_group_positions(symbol, open_positions)
        if group_name and group_count >= max_group:
            logger.info(
                "[CORRELATION_GUARD] BLOCKED reason=GROUP_LIMIT_EXCEEDED "
                "group=%s count=%d limit=%d symbol=%s",
                group_name, group_count, max_group, symbol,
            )
            return CorrelationGuardResult(
                allowed=False,
                reason=f"CORRELATION_BLOCKED:GROUP_LIMIT",
                exposure_map=proposed_exposure,
                blocking_group=group_name,
            )

    # ─── PASSED ────────────────────────────────────────────────────
    return CorrelationGuardResult(
        allowed=True,
        reason="PASS",
        exposure_map=proposed_exposure,
    )
