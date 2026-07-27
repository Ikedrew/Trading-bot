"""
Pattern detection: orchestration layer.
Consumes patterns/ registry for detection, applies deduplication and confirmation.
No execution, risk, or MT5 calls.
"""

from __future__ import annotations
from collections.abc import Sequence
from data.mt5_data import Candle
from strategy.signals import Side, Signal

from patterns.registry import detect_all as _registry_detect_all, load_all_patterns
from patterns.ids import HAMMER, HANGING_MAN, INVERTED_HAMMER, SHOOTING_STAR

# Load all pattern modules on first import (triggers @register_class decorators)
load_all_patterns()


def _dedupe_signals(signals: list[Signal]) -> list[Signal]:
    seen: set[tuple[int, Side]] = set()
    out: list[Signal] = []
    for s in sorted(signals, key=lambda x: (x.bar_index, x.pattern)):
        key = (s.bar_index, s.side)
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def evaluate_closed_bar(candles: Sequence[Candle] | list[Candle], closed_index: int) -> list[Signal]:
    """
    Return pattern signals for a *single* closed bar index.
    Delegates detection to the patterns/ registry, then deduplicates.
    """
    if not candles:
        return []
    seq = list(candles)
    if closed_index <= 0 or closed_index >= len(seq):
        return []

    # Verify chronological ordering (pattern detection assumes sequential market data)
    for i in range(len(seq) - 1):
        if seq[i].time > seq[i + 1].time:
            return []

    raw_signals = _registry_detect_all(seq, closed_index)
    return _dedupe_signals(raw_signals)


def detect_pattern(candles: Sequence[Candle] | list[Candle], closed_index: int) -> list[Signal]:
    """
    Linear pipeline alias: setup_bias -> detect_pattern -> confirm_signal.
    Keeps existing pattern detection behavior unchanged.
    """
    return evaluate_closed_bar(candles, closed_index)


def evaluate(candles: Sequence[Candle] | list[Candle]) -> list[Signal]:
    """Return all pattern signals (full history). Caller filters to live bar."""
    seq = list(candles)
    if len(seq) < 3:
        return []
    # Scan all bars using registry (mirrors legacy: 2-bar from index 1, 3-bar from index 2)
    all_signals: list[Signal] = []
    for i in range(1, len(seq)):
        raw = _registry_detect_all(seq, i)
        # Legacy evaluate() only included 2-bar and 3-bar patterns (no 1-bar)
        for s in raw:
            if s.pattern not in (HAMMER, HANGING_MAN, INVERTED_HAMMER, SHOOTING_STAR):
                all_signals.append(s)
    return _dedupe_signals(all_signals)

# ─── CONFIRMATION RESULT ──────────────────────────────────────────────────────

from dataclasses import dataclass
from enum import Enum


class ConfirmationStrength(str, Enum):
    """Graded confirmation quality level."""
    INVALID = "INVALID"   # Not confirmed — should not trade
    WEAK = "WEAK"         # Confirmed but low quality (reduced confidence)
    STRONG = "STRONG"     # Fully confirmed (high confidence)


@dataclass(frozen=True)
class ConfirmationResult:
    """Structured confirmation outcome with quality metrics."""

    confirmed: bool
    strength: ConfirmationStrength
    reason: str
    body_pct: float          # Body as percentage of total candle range (0.0–1.0)
    wick_ratio: float        # Combined wick length / total range (0.0–1.0)
    close_location: float    # Close position within range (0.0=low, 1.0=high)


# Thresholds for graded confirmation
_BODY_PCT_INVALID_BELOW = 0.45   # Below 45% body → INVALID (not confirmed)
_BODY_PCT_WEAK_BELOW = 0.60      # 45–59% body → WEAK
_BODY_PCT_STRONG_AT = 0.60       # 60%+ body → STRONG
_MIN_CANDLE_RANGE = 0.0005       # Below this → INVALID (too small to be meaningful)


def _compute_confirmation_metrics(c: "Candle") -> tuple[float, float, float]:
    """
    Compute candle quality metrics.

    Returns:
        (body_pct, wick_ratio, close_location)
    """
    candle_range = c.high - c.low
    if candle_range <= 0:
        return 0.0, 0.0, 0.5

    body = abs(c.close - c.open)
    body_pct = body / candle_range

    upper_wick = c.high - max(c.open, c.close)
    lower_wick = min(c.open, c.close) - c.low
    wick_ratio = (upper_wick + lower_wick) / candle_range

    close_location = (c.close - c.low) / candle_range

    return round(body_pct, 4), round(wick_ratio, 4), round(close_location, 4)


def confirm_signal(signal: Signal, candles: list[Candle]) -> tuple[bool, str]:
    """
    Second-layer confirmation for pattern signals.

    Returns (allowed, reason) for backward compatibility.
    For graded confirmation, use confirm_signal_detailed().
    """
    result = confirm_signal_detailed(signal, candles)
    return result.confirmed, result.reason


def confirm_signal_detailed(signal: Signal, candles: list[Candle]) -> ConfirmationResult:
    """
    Structured confirmation with graded quality assessment.

    Thresholds:
        body_pct < 45%  → INVALID (not confirmed)
        body_pct 45–59% → WEAK (confirmed but low quality)
        body_pct >= 60% → STRONG (fully confirmed)

    Additional invalidation:
        - Candle range below _MIN_CANDLE_RANGE → INVALID
        - Direction mismatch (bullish signal + bearish candle) → INVALID

    Returns:
        ConfirmationResult with confirmed flag, strength grade, and metrics.
    """
    c = candles[signal.bar_index]
    body_pct, wick_ratio, close_location = _compute_confirmation_metrics(c)
    candle_range = c.high - c.low

    # Rule 1: Direction sanity check (hard invalidation)
    if signal.side == Side.BUY and c.close < c.open:
        return ConfirmationResult(
            confirmed=False,
            strength=ConfirmationStrength.INVALID,
            reason="bullish signal but bearish candle",
            body_pct=body_pct,
            wick_ratio=wick_ratio,
            close_location=close_location,
        )

    if signal.side == Side.SELL and c.close > c.open:
        return ConfirmationResult(
            confirmed=False,
            strength=ConfirmationStrength.INVALID,
            reason="bearish signal but bullish candle",
            body_pct=body_pct,
            wick_ratio=wick_ratio,
            close_location=close_location,
        )

    # Rule 2: Minimum candle range (hard invalidation)
    if candle_range < _MIN_CANDLE_RANGE:
        return ConfirmationResult(
            confirmed=False,
            strength=ConfirmationStrength.INVALID,
            reason="candle range too small",
            body_pct=body_pct,
            wick_ratio=wick_ratio,
            close_location=close_location,
        )

    # Rule 3: Body strength grading
    if body_pct < _BODY_PCT_INVALID_BELOW:
        return ConfirmationResult(
            confirmed=False,
            strength=ConfirmationStrength.INVALID,
            reason=f"body too weak ({body_pct:.0%} < {_BODY_PCT_INVALID_BELOW:.0%})",
            body_pct=body_pct,
            wick_ratio=wick_ratio,
            close_location=close_location,
        )

    if body_pct < _BODY_PCT_STRONG_AT:
        return ConfirmationResult(
            confirmed=True,
            strength=ConfirmationStrength.WEAK,
            reason=f"weak confirmation ({body_pct:.0%} body)",
            body_pct=body_pct,
            wick_ratio=wick_ratio,
            close_location=close_location,
        )

    # Strong confirmation
    return ConfirmationResult(
        confirmed=True,
        strength=ConfirmationStrength.STRONG,
        reason="pattern confirmed",
        body_pct=body_pct,
        wick_ratio=wick_ratio,
        close_location=close_location,
    )
