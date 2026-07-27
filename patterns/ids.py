"""
Canonical pattern identifiers — single source of truth for all pattern name strings.

Import from here instead of using raw string literals.
This prevents typo-based coupling failures between patterns/, risk/, and strategy/.
"""

from __future__ import annotations

# ─── 1-BAR PATTERNS ──────────────────────────────────────────────────────────
HAMMER = "HAMMER"
HANGING_MAN = "HANGING_MAN"
INVERTED_HAMMER = "INVERTED_HAMMER"
SHOOTING_STAR = "SHOOTING_STAR"

# ─── 2-BAR PATTERNS ──────────────────────────────────────────────────────────
BULLISH_ENGULFING = "BULLISH_ENGULFING"
BEARISH_ENGULFING = "BEARISH_ENGULFING"
TWEEZER_TOP = "TWEEZER_TOP"
TWEEZER_BOTTOM = "TWEEZER_BOTTOM"

# ─── 3-BAR PATTERNS ──────────────────────────────────────────────────────────
MORNING_STAR = "MORNING_STAR"
EVENING_STAR = "EVENING_STAR"
THREE_WHITE_SOLDIERS = "THREE_WHITE_SOLDIERS"
THREE_BLACK_CROWS = "THREE_BLACK_CROWS"
THREE_INSIDE_UP = "THREE_INSIDE_UP"
THREE_INSIDE_DOWN = "THREE_INSIDE_DOWN"

# ─── ALL PATTERN IDS (for validation) ────────────────────────────────────────
ALL_PATTERN_IDS: frozenset[str] = frozenset({
    HAMMER, HANGING_MAN, INVERTED_HAMMER, SHOOTING_STAR,
    BULLISH_ENGULFING, BEARISH_ENGULFING, TWEEZER_TOP, TWEEZER_BOTTOM,
    MORNING_STAR, EVENING_STAR,
    THREE_WHITE_SOLDIERS, THREE_BLACK_CROWS,
    THREE_INSIDE_UP, THREE_INSIDE_DOWN,
})
