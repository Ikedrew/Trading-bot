"""
Macro Alignment Decision Log — Observability output.

Formats a structured log block showing exactly how macro context
influenced the confidence modifier for a given strategy decision.

This module is OBSERVABILITY ONLY:
  - Does not compute alignment (reads from MacroAlignment)
  - Does not modify confidence (reads base/final)
  - Does not affect strategy selection
  - Pure formatting function with no side effects

Ownership: core/timeframes/macro_decision_log.py
Dependencies: core/timeframes/macro_alignment.py (MacroAlignment type only)
Must NOT import from: strategy_engine, pipeline, persistence
"""

from __future__ import annotations

import logging
from core.timeframes.macro_alignment import MacroAlignment, _WEIGHT_MONTHLY, _WEIGHT_WEEKLY, _WEIGHT_DAILY
from core.timeframes.types import MacroSnapshot

logger = logging.getLogger(__name__)


def format_macro_alignment_log(
    alignment: MacroAlignment | None,
    macro: MacroSnapshot | None,
    trade_direction: str,
    base_confidence: float,
    final_confidence: float,
    enabled: bool = True,
) -> str:
    """
    Format a structured macro alignment log block.

    Args:
        alignment: Computed MacroAlignment (None if unavailable)
        macro: The MacroSnapshot used for computation (None if unavailable)
        trade_direction: "BULLISH" or "BEARISH" (mapped to BUY/SELL for display)
        base_confidence: Strategy confidence BEFORE macro modifier
        final_confidence: Confidence AFTER macro modifier applied
        enabled: Whether macro context is enabled in config

    Returns:
        Formatted multi-line string for terminal/log output.
    """
    if not enabled:
        return (
            "=" * 50 + "\n"
            "MACRO ALIGNMENT\n"
            "=" * 50 + "\n"
            "Macro Context: DISABLED\n"
            "=" * 50
        )

    if alignment is None or macro is None:
        return (
            "=" * 50 + "\n"
            "MACRO ALIGNMENT\n"
            "=" * 50 + "\n"
            f"Direction: {_direction_label(trade_direction)}\n"
            "\n"
            "MN1 : UNAVAILABLE\n"
            "W1  : UNAVAILABLE\n"
            "D1  : UNAVAILABLE\n"
            "\n"
            "Alignment State : UNAVAILABLE\n"
            "Data Quality    : UNAVAILABLE\n"
            "\n"
            f"Base Confidence : {base_confidence:.2f}\n"
            "Macro Modifier  : +0.00\n"
            f"Final Confidence: {base_confidence:.2f}\n"
            "=" * 50
        )

    # Compute per-layer contribution for display
    mn_contrib = _layer_contribution(
        alignment.monthly_alignment, macro.monthly_trend_strength, _WEIGHT_MONTHLY
    )
    w1_contrib = _layer_contribution(
        alignment.weekly_alignment, macro.weekly_trend_strength, _WEIGHT_WEEKLY
    )
    d1_contrib = _layer_contribution(
        alignment.daily_alignment, macro.daily_bias_strength, _WEIGHT_DAILY
    )

    modifier = alignment.confidence_modifier
    modifier_str = f"+{modifier:.2f}" if modifier >= 0 else f"{modifier:.2f}"

    lines = [
        "=" * 50,
        "MACRO ALIGNMENT",
        "=" * 50,
        f"Direction: {_direction_label(trade_direction)}",
        "",
        _format_layer("MN1", macro.monthly_trend, macro.monthly_trend_strength, mn_contrib, alignment.monthly_alignment),
        _format_layer("W1 ", macro.weekly_trend, macro.weekly_trend_strength, w1_contrib, alignment.weekly_alignment),
        _format_layer("D1 ", macro.daily_bias, macro.daily_bias_strength, d1_contrib, alignment.daily_alignment),
        "",
        f"Alignment State : {alignment.alignment_state}",
        f"Primary Driver  : {alignment.primary_influence}",
        f"Data Quality    : {alignment.data_quality}",
        f"Conflict        : {alignment.is_conflicted}",
        "",
        f"Base Confidence : {base_confidence:.2f}",
        f"Macro Modifier  : {modifier_str}",
        f"Final Confidence: {final_confidence:.2f}",
        "=" * 50,
    ]

    return "\n".join(lines)


def emit_macro_alignment_log(
    alignment: MacroAlignment | None,
    macro: MacroSnapshot | None,
    trade_direction: str,
    base_confidence: float,
    final_confidence: float,
    symbol: str = "",
    enabled: bool = True,
) -> None:
    """
    Emit the macro alignment log to both terminal (print) and logger.

    Only emits when a strategy has been selected (caller responsibility).
    """
    block = format_macro_alignment_log(
        alignment=alignment,
        macro=macro,
        trade_direction=trade_direction,
        base_confidence=base_confidence,
        final_confidence=final_confidence,
        enabled=enabled,
    )

    print(block)

    # Single-line structured log for persistent logger
    if alignment is not None:
        logger.info(
            "[MACRO_ALIGNMENT] symbol=%s direction=%s state=%s modifier=%.4f "
            "base=%.2f final=%.2f primary=%s quality=%s conflict=%s",
            symbol,
            trade_direction,
            alignment.alignment_state,
            alignment.confidence_modifier,
            base_confidence,
            final_confidence,
            alignment.primary_influence,
            alignment.data_quality,
            alignment.is_conflicted,
        )
    elif not enabled:
        logger.info("[MACRO_ALIGNMENT] symbol=%s DISABLED", symbol)
    else:
        logger.info("[MACRO_ALIGNMENT] symbol=%s UNAVAILABLE", symbol)


# ═══════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════


def _direction_label(direction: str) -> str:
    """Map internal direction to display label."""
    if direction == "BULLISH":
        return "BUY"
    elif direction == "BEARISH":
        return "SELL"
    return direction or "UNKNOWN"


def _layer_contribution(alignment: str, strength: float, weight: float) -> float:
    """Calculate display contribution for a single layer."""
    from core.timeframes.macro_alignment import _strength_scale, _MODIFIER_SCALE
    if alignment == "ALIGNED":
        return _strength_scale(strength) * weight * _MODIFIER_SCALE
    elif alignment == "OPPOSING":
        return -_strength_scale(strength) * weight * _MODIFIER_SCALE
    return 0.0


def _format_layer(label: str, trend: str, strength: float, contribution: float, alignment: str) -> str:
    """Format a single timeframe layer line."""
    if not trend or trend == "" or alignment == "NEUTRAL" and strength < 0.3:
        if strength == 0.0 and (not trend or trend == ""):
            return f"{label} : UNAVAILABLE"

    trend_display = trend if trend else "NEUTRAL"
    contrib_str = f"+{contribution:.2f}" if contribution >= 0 else f"{contribution:.2f}"
    return f"{label} : {trend_display:<8s} strength={strength:.2f}  contribution={contrib_str}"
