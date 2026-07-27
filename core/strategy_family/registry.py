"""
Strategy Family Registry — Pattern-to-family classification.

This is the single source of truth for which strategy family each pattern
belongs to. Classification is based on classical technical analysis consensus
(validated in the Strategy-Family Classification Audit, 2026-07-27).

Sources:
    - Nison, Japanese Candlestick Charting Techniques
    - Bulkowski, Encyclopedia of Candlestick Charts
    - Murphy, Technical Analysis of the Financial Markets
    - Classification Audit (2026-07-27)

Current library composition:
    REVERSAL: 12 patterns (86%)
    MOMENTUM: 2 patterns (14%)
    CONTINUATION: 0 patterns (0%) — requires new pattern detectors
    BREAKOUT: 0 patterns (0%) — requires new pattern detectors
    MEAN_REVERSION: 0 patterns (0%) — requires new pattern detectors
"""

from __future__ import annotations

from collections import Counter

from core.strategy_family.models import StrategyFamily


# ═══════════════════════════════════════════════════════════════════════════════
# PATTERN → FAMILY MAPPING
# ═══════════════════════════════════════════════════════════════════════════════

FAMILY_REGISTRY: dict[str, StrategyFamily] = {
    # ─── REVERSAL (exhaustion → direction change) ─────────────────────
    "TWEEZER_TOP": StrategyFamily.REVERSAL,
    "TWEEZER_BOTTOM": StrategyFamily.REVERSAL,
    "HAMMER": StrategyFamily.REVERSAL,
    "HANGING_MAN": StrategyFamily.REVERSAL,
    "INVERTED_HAMMER": StrategyFamily.REVERSAL,
    "SHOOTING_STAR": StrategyFamily.REVERSAL,
    "MORNING_STAR": StrategyFamily.REVERSAL,
    "EVENING_STAR": StrategyFamily.REVERSAL,
    "THREE_INSIDE_UP": StrategyFamily.REVERSAL,
    "THREE_INSIDE_DOWN": StrategyFamily.REVERSAL,
    "BULLISH_ENGULFING": StrategyFamily.REVERSAL,
    "BEARISH_ENGULFING": StrategyFamily.REVERSAL,

    # ─── MOMENTUM (strong directional conviction) ─────────────────────
    "THREE_WHITE_SOLDIERS": StrategyFamily.MOMENTUM,
    "THREE_BLACK_CROWS": StrategyFamily.MOMENTUM,

    # ─── CONTINUATION (trend-following after pullback) ────────────────
    # No patterns currently implemented.
    # Future candidates: Rising Three Methods, Falling Three Methods,
    #                    Mat Hold, Upside/Downside Gap Three Methods

    # ─── BREAKOUT (range escape) ──────────────────────────────────────
    # No patterns currently implemented.
    # Future candidates: Long Marubozu from squeeze, Gap breakouts

    # ─── MEAN_REVERSION (bounce from statistical extremes) ────────────
    # No patterns currently implemented.
    # Future candidates: Requires support/resistance context + candle signal
}


# ═══════════════════════════════════════════════════════════════════════════════
# FAMILIES WITH NO PATTERNS (placeholder tracking)
# ═══════════════════════════════════════════════════════════════════════════════

EMPTY_FAMILIES: set[StrategyFamily] = {
    f for f in StrategyFamily
    if f not in set(FAMILY_REGISTRY.values())
}


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def classify_pattern(pattern: str) -> StrategyFamily | None:
    """
    Classify a pattern name into its strategy family.

    Returns None for unknown patterns (not in the registry).
    Unknown patterns are not failures — they should be reported for
    potential registry expansion.
    """
    return FAMILY_REGISTRY.get(pattern)


def get_patterns_for_family(family: StrategyFamily) -> list[str]:
    """Return all patterns belonging to a given family."""
    return [p for p, f in FAMILY_REGISTRY.items() if f == family]


def get_family_distribution() -> dict[str, int]:
    """
    Return count of patterns per family.

    Includes ALL families (even those with 0 patterns) so the diagnostic
    output shows what's missing.
    """
    counts = Counter(f.value for f in FAMILY_REGISTRY.values())
    # Ensure all families appear even if count is 0
    for family in StrategyFamily:
        if family.value not in counts:
            counts[family.value] = 0
    return dict(counts)


def get_all_known_patterns() -> list[str]:
    """Return all pattern names registered in the system."""
    return list(FAMILY_REGISTRY.keys())


def is_known_pattern(pattern: str) -> bool:
    """Check if a pattern name exists in the registry."""
    return pattern in FAMILY_REGISTRY
