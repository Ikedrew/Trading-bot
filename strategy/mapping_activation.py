"""
Mapping Activation — Pattern → Strategy candidate mapping with strength.

Maps detected candlestick patterns to potential strategy types.
Does NOT gate or filter — only identifies which strategies a pattern CAN belong to
and how strongly the pattern suggests that strategy.

Consumed by: gating_activation → selection_activation
"""

from __future__ import annotations

from dataclasses import dataclass

from strategy.signals import Signal


@dataclass(frozen=True)
class PatternMapping:
    """One pattern-to-strategy mapping with strength."""
    strategy: str                   # REVERSAL / FALSE_BREAK / CONTINUATION
    strength: float                 # 0.0–1.0 (how strongly pattern suggests this strategy)
    context_dependency: str         # HIGH / MEDIUM / LOW (how much context matters for this mapping)


# ─── PATTERN → STRATEGY MAPPING ───────────────────────────────────────────────

_REVERSAL_PATTERNS = frozenset({
    "HAMMER", "HANGING_MAN", "INVERTED_HAMMER", "SHOOTING_STAR",
    "BULLISH_ENGULFING", "BEARISH_ENGULFING",
    "TWEEZER_TOP", "TWEEZER_BOTTOM",
    "MORNING_STAR", "EVENING_STAR",
    "THREE_INSIDE_UP", "THREE_INSIDE_DOWN",
})

_FALSE_BREAK_PATTERNS = frozenset({
    "HAMMER", "HANGING_MAN", "INVERTED_HAMMER", "SHOOTING_STAR",
    "BULLISH_ENGULFING", "BEARISH_ENGULFING",
    "TWEEZER_TOP", "TWEEZER_BOTTOM",
    "MORNING_STAR", "EVENING_STAR",
    "THREE_INSIDE_UP", "THREE_INSIDE_DOWN",
})

_CONTINUATION_PATTERNS = frozenset({
    "THREE_WHITE_SOLDIERS", "THREE_BLACK_CROWS",
    "BULLISH_ENGULFING", "BEARISH_ENGULFING",
})

# Strength values: how naturally a pattern maps to a strategy
# HIGH = pattern is almost always this strategy type
# MEDIUM = context determines interpretation
# LOW = weak mapping, heavily context-dependent
_PATTERN_STRENGTH: dict[str, dict[str, float]] = {
    # Strong reversal signals
    "MORNING_STAR": {"REVERSAL": 0.9, "FALSE_BREAK": 0.5, "CONTINUATION": 0.0},
    "EVENING_STAR": {"REVERSAL": 0.9, "FALSE_BREAK": 0.5, "CONTINUATION": 0.0},
    "TWEEZER_TOP": {"REVERSAL": 0.8, "FALSE_BREAK": 0.6, "CONTINUATION": 0.0},
    "TWEEZER_BOTTOM": {"REVERSAL": 0.8, "FALSE_BREAK": 0.6, "CONTINUATION": 0.0},
    # Context-dependent (could be reversal OR false break OR continuation)
    "HAMMER": {"REVERSAL": 0.6, "FALSE_BREAK": 0.7, "CONTINUATION": 0.0},
    "SHOOTING_STAR": {"REVERSAL": 0.6, "FALSE_BREAK": 0.7, "CONTINUATION": 0.0},
    "HANGING_MAN": {"REVERSAL": 0.5, "FALSE_BREAK": 0.6, "CONTINUATION": 0.0},
    "INVERTED_HAMMER": {"REVERSAL": 0.5, "FALSE_BREAK": 0.6, "CONTINUATION": 0.0},
    # Engulfing: dual purpose
    "BULLISH_ENGULFING": {"REVERSAL": 0.5, "FALSE_BREAK": 0.4, "CONTINUATION": 0.7},
    "BEARISH_ENGULFING": {"REVERSAL": 0.5, "FALSE_BREAK": 0.4, "CONTINUATION": 0.7},
    # Pure continuation
    "THREE_WHITE_SOLDIERS": {"REVERSAL": 0.0, "FALSE_BREAK": 0.0, "CONTINUATION": 0.9},
    "THREE_BLACK_CROWS": {"REVERSAL": 0.0, "FALSE_BREAK": 0.0, "CONTINUATION": 0.9},
    # Inside patterns
    "THREE_INSIDE_UP": {"REVERSAL": 0.7, "FALSE_BREAK": 0.5, "CONTINUATION": 0.0},
    "THREE_INSIDE_DOWN": {"REVERSAL": 0.7, "FALSE_BREAK": 0.5, "CONTINUATION": 0.0},
}


def get_candidate_strategies(pattern: Signal) -> list[str]:
    """
    Return list of strategies this pattern is eligible for.

    Does NOT filter or gate — only maps pattern name to potential strategy types.
    Strength information is available via get_pattern_mappings().
    """
    candidates: list[str] = []
    if pattern.pattern in _REVERSAL_PATTERNS:
        candidates.append("REVERSAL")
    if pattern.pattern in _FALSE_BREAK_PATTERNS:
        candidates.append("FALSE_BREAK")
    if pattern.pattern in _CONTINUATION_PATTERNS:
        candidates.append("CONTINUATION")
    if not candidates:
        candidates.append("CONTINUATION")
    return candidates


def get_pattern_mappings(pattern: Signal) -> list[PatternMapping]:
    """
    Return detailed mappings with strength and context dependency.

    Used for logging and activation scoring refinement.
    """
    strengths = _PATTERN_STRENGTH.get(pattern.pattern, {})
    mappings: list[PatternMapping] = []

    for strategy in ("REVERSAL", "FALSE_BREAK", "CONTINUATION"):
        strength = strengths.get(strategy, 0.0)
        if strength <= 0.0:
            continue

        # Context dependency: high strength = low context dependency
        if strength >= 0.8:
            ctx_dep = "LOW"
        elif strength >= 0.5:
            ctx_dep = "MEDIUM"
        else:
            ctx_dep = "HIGH"

        mappings.append(PatternMapping(strategy=strategy, strength=strength, context_dependency=ctx_dep))

    if not mappings:
        mappings.append(PatternMapping(strategy="CONTINUATION", strength=0.3, context_dependency="HIGH"))

    return mappings


def get_raw_pressure(pattern: Signal) -> dict[str, float]:
    """
    Compute raw pattern pressure for ALL strategies.

    This represents pure pattern intent BEFORE any eligibility, gating, or regime influence.
    Non-mapped strategies get a residual floor (0.05).

    Returns:
        {"REVERSAL": float, "FALSE_BREAK": float, "CONTINUATION": float}
    """
    strengths = _PATTERN_STRENGTH.get(pattern.pattern, {})
    return {
        "REVERSAL": strengths.get("REVERSAL", 0.05),
        "FALSE_BREAK": strengths.get("FALSE_BREAK", 0.05),
        "CONTINUATION": strengths.get("CONTINUATION", 0.05),
    }
