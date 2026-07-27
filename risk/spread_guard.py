"""
Spread Guard — Hard Pre-Execution Block.

This is a PRODUCTION ENFORCEMENT LAYER, not observational.
No trade can reach order_send() without passing this gate.

Fail-safe: if any data is missing, BLOCK the trade.

Two conditions checked (both must pass):
  1. ATR-relative: spread / risk_distance <= MAX_SPREAD_ATR_RATIO
  2. Absolute cap: spread <= MAX_SPREAD_ABSOLUTE[symbol]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ─── CONFIGURATION ───────────────────────────────────────────────────────────

def _get_max_spread_atr_ratio() -> float:
    try:
        from core import config
        return float(getattr(config, "MAX_SPREAD_ATR_RATIO", 0.30))
    except ImportError:
        return 0.30


def _get_max_spread_absolute(symbol: str) -> float:
    """Get per-symbol absolute spread cap. Falls back to global default."""
    try:
        from core import config
        per_symbol = getattr(config, "MAX_SPREAD_ABSOLUTE", {})
        if isinstance(per_symbol, dict) and symbol in per_symbol:
            return float(per_symbol[symbol])
        return float(getattr(config, "MAX_SPREAD_ABSOLUTE_DEFAULT", 0.0005))
    except ImportError:
        return 0.0005


def _is_spread_guard_enabled() -> bool:
    try:
        from core import config
        return bool(getattr(config, "SPREAD_GUARD_ENABLED", True))
    except ImportError:
        return True


# ─── RESULT TYPE ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SpreadGuardResult:
    """Result of spread guard evaluation."""
    allowed: bool
    reason: str
    spread: float = 0.0
    risk_distance: float = 0.0
    ratio: float = 0.0
    threshold_ratio: float = 0.0
    threshold_absolute: float = 0.0
    symbol: str = ""


# ─── METRICS ──────────────────────────────────────────────────────────────────

_metrics: dict[str, int] = {
    "checked": 0,
    "allowed": 0,
    "blocked_ratio": 0,
    "blocked_absolute": 0,
    "blocked_missing_data": 0,
}


def get_spread_guard_metrics() -> dict[str, int]:
    """Return spread guard metrics snapshot."""
    return dict(_metrics)


def reset_spread_guard_metrics() -> None:
    """Reset metrics (for testing)."""
    for k in _metrics:
        _metrics[k] = 0


# ─── GUARD EVALUATION ─────────────────────────────────────────────────────────

def check_spread(
    *,
    symbol: str,
    bid: float,
    ask: float,
    risk_distance: float,
) -> SpreadGuardResult:
    """
    Evaluate whether current spread conditions are safe for execution.

    Args:
        symbol: Trading symbol
        bid: Current bid price (from live tick)
        ask: Current ask price (from live tick)
        risk_distance: Distance from entry to SL in price units (proxy for ATR).
                       This is |entry - sl| from the OrderIntent.

    Returns:
        SpreadGuardResult with allowed=True if safe, allowed=False if blocked.

    Fail-safe behaviour:
        - If bid/ask missing or invalid → BLOCK
        - If risk_distance is zero/negative → BLOCK
        - If spread is negative → BLOCK
    """
    _metrics["checked"] += 1

    if not _is_spread_guard_enabled():
        _metrics["allowed"] += 1
        return SpreadGuardResult(allowed=True, reason="SPREAD_GUARD_DISABLED", symbol=symbol)

    max_ratio = _get_max_spread_atr_ratio()
    max_absolute = _get_max_spread_absolute(symbol)

    # ─── FAIL-SAFE: Missing or invalid data ───────────────────────
    if bid <= 0 or ask <= 0:
        _metrics["blocked_missing_data"] += 1
        logger.warning(
            "[SPREAD_GUARD] BLOCKED reason=INVALID_TICK symbol=%s bid=%.5f ask=%.5f",
            symbol, bid, ask,
        )
        return SpreadGuardResult(
            allowed=False,
            reason="SPREAD_EXCEEDED:INVALID_TICK",
            spread=0.0,
            symbol=symbol,
        )

    spread = ask - bid
    if spread < 0:
        _metrics["blocked_missing_data"] += 1
        logger.warning(
            "[SPREAD_GUARD] BLOCKED reason=NEGATIVE_SPREAD symbol=%s spread=%.6f",
            symbol, spread,
        )
        return SpreadGuardResult(
            allowed=False,
            reason="SPREAD_EXCEEDED:NEGATIVE_SPREAD",
            spread=spread,
            symbol=symbol,
        )

    if risk_distance <= 0:
        _metrics["blocked_missing_data"] += 1
        logger.warning(
            "[SPREAD_GUARD] BLOCKED reason=MISSING_RISK_DISTANCE symbol=%s "
            "spread=%.6f risk_distance=%.6f",
            symbol, spread, risk_distance,
        )
        return SpreadGuardResult(
            allowed=False,
            reason="SPREAD_EXCEEDED:MISSING_RISK_DISTANCE",
            spread=spread,
            risk_distance=risk_distance,
            symbol=symbol,
        )

    # ─── CHECK 1: ATR-relative ratio ─────────────────────────────
    ratio = spread / risk_distance

    if ratio > max_ratio:
        _metrics["blocked_ratio"] += 1
        logger.warning(
            "[SPREAD_GUARD] BLOCKED reason=RATIO_EXCEEDED symbol=%s "
            "spread=%.6f risk_distance=%.6f ratio=%.4f threshold=%.4f",
            symbol, spread, risk_distance, ratio, max_ratio,
        )
        return SpreadGuardResult(
            allowed=False,
            reason="SPREAD_EXCEEDED:RATIO",
            spread=spread,
            risk_distance=risk_distance,
            ratio=ratio,
            threshold_ratio=max_ratio,
            symbol=symbol,
        )

    # ─── CHECK 2: Absolute cap ────────────────────────────────────
    if spread > max_absolute:
        _metrics["blocked_absolute"] += 1
        logger.warning(
            "[SPREAD_GUARD] BLOCKED reason=ABSOLUTE_EXCEEDED symbol=%s "
            "spread=%.6f threshold=%.6f",
            symbol, spread, max_absolute,
        )
        return SpreadGuardResult(
            allowed=False,
            reason="SPREAD_EXCEEDED:ABSOLUTE",
            spread=spread,
            risk_distance=risk_distance,
            ratio=ratio,
            threshold_absolute=max_absolute,
            symbol=symbol,
        )

    # ─── PASSED ───────────────────────────────────────────────────
    _metrics["allowed"] += 1
    return SpreadGuardResult(
        allowed=True,
        reason="PASS",
        spread=spread,
        risk_distance=risk_distance,
        ratio=ratio,
        threshold_ratio=max_ratio,
        threshold_absolute=max_absolute,
        symbol=symbol,
    )
