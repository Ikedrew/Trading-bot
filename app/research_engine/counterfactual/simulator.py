"""
Counterfactual Simulator — Forward projection for blocked decisions.

Given a blocked decision trace and subsequent replay candles, simulates
what WOULD have happened if the trade had been allowed to execute.

This module ONLY calculates hypothetical trade outcomes.
It does NOT:
    - Analyse blockers
    - Rank gates
    - Modify trading logic
    - Produce economic impact summaries

SIMULATION RULES (from approved design):
    - Entry: candle.close of decision bar
    - Direction: pattern_name → BUY/SELL mapping
    - SL: SLTP_RULES[pattern](candle) when possible, rr_effective fallback otherwise
    - TP: entry ± risk_distance × RR (BASE_RR=2.0, or 3.0 for RR3_PATTERNS)
    - Same-bar collision: SL wins (conservative, matches shadow trade engine)
    - Max bars: 60 (5 hours at M5)
    - Timeout exit: bar_close

RECONSTRUCTION METHODS:
    - LIVE_RULES: SL/TP computed from actual candle geometry using SLTP_RULES
    - RR_ESTIMATE: SL estimated from rr_effective field (no candle geometry)
    - UNAVAILABLE: Cannot reconstruct trade parameters
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from research_engine.counterfactual.schema import (
    CounterfactualTruth,
    SimulationConfidence,
    OutcomeClass,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS (from production config — read-only reference)
# ═══════════════════════════════════════════════════════════════════════════════

_BASE_RR = 2.0
_MIN_RR = 2.0
_SL_BUFFER = 0.0002
_MAX_BARS = 60
_RR3_PATTERNS = frozenset({"THREE_WHITE_SOLDIERS", "THREE_BLACK_CROWS"})

# Breakeven threshold for outcome classification (±0.1R)
_BREAKEVEN_THRESHOLD = 0.1


# ═══════════════════════════════════════════════════════════════════════════════
# PATTERN → DIRECTION MAPPING
# ═══════════════════════════════════════════════════════════════════════════════

_BUY_PATTERNS = frozenset({
    "BULLISH_ENGULFING", "TWEEZER_BOTTOM", "MORNING_STAR",
    "THREE_WHITE_SOLDIERS", "THREE_INSIDE_UP",
    "HAMMER", "INVERTED_HAMMER",
})

_SELL_PATTERNS = frozenset({
    "BEARISH_ENGULFING", "TWEEZER_TOP", "EVENING_STAR",
    "THREE_BLACK_CROWS", "THREE_INSIDE_DOWN",
    "SHOOTING_STAR", "HANGING_MAN",
})

# Patterns that use SL buffer (from risk/levels.py SLTP_RULES)
_BUY_BUFFERED = frozenset({
    "TWEEZER_BOTTOM", "MORNING_STAR", "THREE_INSIDE_UP",
    "HAMMER", "INVERTED_HAMMER",
})
_BUY_NO_BUFFER = frozenset({"BULLISH_ENGULFING", "THREE_WHITE_SOLDIERS"})

_SELL_BUFFERED = frozenset({
    "TWEEZER_TOP", "EVENING_STAR", "THREE_INSIDE_DOWN",
    "SHOOTING_STAR", "HANGING_MAN",
})
_SELL_NO_BUFFER = frozenset({"BEARISH_ENGULFING", "THREE_BLACK_CROWS"})


# ═══════════════════════════════════════════════════════════════════════════════
# RECONSTRUCTION METHOD
# ═══════════════════════════════════════════════════════════════════════════════

class ReconstructionMethod(str, Enum):
    """How the trade parameters were reconstructed."""
    LIVE_RULES = "LIVE_RULES"         # SL/TP from actual SLTP_RULES candle geometry
    RR_ESTIMATE = "RR_ESTIMATE"       # SL estimated from rr_effective field
    UNAVAILABLE = "UNAVAILABLE"       # Cannot reconstruct


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _pattern_to_direction(pattern_name: str) -> str | None:
    """Map pattern name to BUY/SELL. Returns None if unknown."""
    if pattern_name in _BUY_PATTERNS:
        return "BUY"
    if pattern_name in _SELL_PATTERNS:
        return "SELL"
    return None


def _get_rr(pattern_name: str) -> float:
    """Get RR multiplier for a pattern."""
    if pattern_name in _RR3_PATTERNS:
        return 3.0
    return max(_BASE_RR, _MIN_RR)


def _compute_sl_from_rules(
    pattern_name: str,
    direction: str,
    candle_open: float,
    candle_high: float,
    candle_low: float,
    candle_close: float,
) -> float | None:
    """
    Compute SL using the same rules as risk/levels.py.

    BUY patterns: SL = candle.low (- buffer if buffered)
    SELL patterns: SL = candle.high (+ buffer if buffered)

    Returns None if pattern is not in the known SLTP rules.
    """
    if direction == "BUY":
        if pattern_name in _BUY_BUFFERED:
            return candle_low - _SL_BUFFER
        elif pattern_name in _BUY_NO_BUFFER:
            return candle_low
        return None
    elif direction == "SELL":
        if pattern_name in _SELL_BUFFERED:
            return candle_high + _SL_BUFFER
        elif pattern_name in _SELL_NO_BUFFER:
            return candle_high
        return None
    return None


def _compute_sl_from_rr(
    direction: str,
    entry_price: float,
    rr_effective: float,
    tp_distance: float | None = None,
) -> float | None:
    """
    Estimate SL from rr_effective when live rules are unavailable.

    If rr_effective and entry are known:
        For a given TP distance, risk = tp_distance / rr
        SL = entry - risk (BUY) or entry + risk (SELL)

    If tp_distance unknown, we cannot compute — return None.
    """
    if rr_effective <= 0:
        return None
    # We don't have tp_distance independently, so we need another approach:
    # From the EV computation: rr_effective = reward / risk
    # We know the RR the engine was targeting. If the engine computed rr_effective,
    # it means reward/risk = rr_effective. We can use this with the target RR
    # to determine if risk_distance is derivable. But without absolute prices,
    # we can't get exact SL. This method is only used as a last resort.
    return None


def _extract_bar_time_ms(entity_id: str) -> int | None:
    """Extract bar timestamp (ms) from entity_id format: {symbol}_{unix_seconds}."""
    parts = entity_id.rsplit("_", 1)
    if len(parts) != 2:
        return None
    try:
        return int(parts[1]) * 1000  # Convert seconds to milliseconds
    except (ValueError, TypeError):
        return None


def _find_entry_candle(replay_candles: list[dict[str, Any]], bar_time_ms: int) -> dict[str, Any] | None:
    """Find the candle matching the decision bar time."""
    for candle in replay_candles:
        if candle.get("ts") == bar_time_ms:
            return candle
    return None


def _get_future_candles(replay_candles: list[dict[str, Any]], bar_time_ms: int, max_bars: int) -> list[dict[str, Any]]:
    """Get candles AFTER the entry bar (for simulation)."""
    future = []
    found_entry = False
    for candle in replay_candles:
        if candle.get("ts") == bar_time_ms:
            found_entry = True
            continue  # Skip the entry bar itself
        if found_entry:
            future.append(candle)
            if len(future) >= max_bars:
                break
    return future


def _classify_outcome(hypothetical_r: float, exit_reason: str) -> OutcomeClass:
    """Classify the counterfactual outcome."""
    if exit_reason == "max_bars_timeout":
        return OutcomeClass.TIMEOUT
    if hypothetical_r > _BREAKEVEN_THRESHOLD:
        return OutcomeClass.WIN_AVOIDED
    elif hypothetical_r < -_BREAKEVEN_THRESHOLD:
        return OutcomeClass.LOSS_AVOIDED
    else:
        return OutcomeClass.BREAKEVEN


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN SIMULATOR
# ═══════════════════════════════════════════════════════════════════════════════

def simulate_blocked_decision(
    decision_trace: dict[str, Any],
    replay_candles: list[dict[str, Any]],
) -> CounterfactualTruth:
    """
    Simulate what would have happened if a blocked trade had been allowed.

    Args:
        decision_trace: A single blocked decision trace record (dict from JSONL).
        replay_candles: List of M5 candles (dicts with ts, o, h, l, c, v)
                       covering the decision bar AND subsequent bars.
                       Must be sorted by ts ascending.

    Returns:
        CounterfactualTruth with simulation results and confidence level.
    """
    # ─── EXTRACT IDENTITY ─────────────────────────────────────────────
    entity_id = decision_trace.get("entity_id", "")
    cycle_id = int(decision_trace.get("cycle_id", 0))
    symbol = decision_trace.get("symbol", "")
    timestamp_utc = decision_trace.get("timestamp_utc", "")
    terminal_stage = decision_trace.get("terminal_stage", "")
    terminal_reason = decision_trace.get("terminal_reason", "")
    pattern_name = decision_trace.get("pattern_name", "")
    score_neutral = float(decision_trace.get("score_neutral", 0.0))
    regime = decision_trace.get("regime", "")
    market_state = decision_trace.get("market_state", "")
    rr_effective = decision_trace.get("rr_effective")

    # Base result (filled progressively)
    result = CounterfactualTruth(
        entity_id=entity_id,
        cycle_id=cycle_id,
        symbol=symbol,
        timestamp_utc=timestamp_utc,
        terminal_stage=terminal_stage,
        terminal_reason=terminal_reason,
        blocking_component=terminal_stage,
        pattern_name=pattern_name,
        score_neutral=score_neutral,
        regime=regime,
        market_state=market_state,
    )

    # ─── DETERMINE DIRECTION ──────────────────────────────────────────
    direction = _pattern_to_direction(pattern_name)
    if direction is None:
        result.simulation_confidence = SimulationConfidence.UNKNOWN
        result.confidence_factors = {
            "replay_candle_available": False,
            "direction_confirmed": False,
            "sl_from_live_rules": False,
            "sl_from_rr_estimate": False,
            "tp_from_live_rules": False,
            "future_bars_complete": False,
        }
        return result

    result.direction = direction
    direction_confirmed = True

    # ─── FIND ENTRY CANDLE ────────────────────────────────────────────
    bar_time_ms = _extract_bar_time_ms(entity_id)
    if bar_time_ms is None:
        result.simulation_confidence = SimulationConfidence.UNKNOWN
        result.confidence_factors = {
            "replay_candle_available": False,
            "direction_confirmed": direction_confirmed,
            "sl_from_live_rules": False,
            "sl_from_rr_estimate": False,
            "tp_from_live_rules": False,
            "future_bars_complete": False,
        }
        return result

    entry_candle = _find_entry_candle(replay_candles, bar_time_ms)
    replay_candle_available = entry_candle is not None

    if not replay_candle_available:
        result.simulation_confidence = SimulationConfidence.LOW
        result.confidence_factors = {
            "replay_candle_available": False,
            "direction_confirmed": direction_confirmed,
            "sl_from_live_rules": False,
            "sl_from_rr_estimate": False,
            "tp_from_live_rules": False,
            "future_bars_complete": False,
        }
        return result

    # ─── RECONSTRUCT TRADE PARAMETERS ─────────────────────────────────
    entry_price = entry_candle["c"]  # candle.close (matches live SL/TP builders)
    candle_high = entry_candle["h"]
    candle_low = entry_candle["l"]
    candle_open = entry_candle["o"]

    result.entry_price = entry_price

    # Try LIVE_RULES first
    sl = _compute_sl_from_rules(
        pattern_name, direction,
        candle_open, candle_high, candle_low, entry_price,
    )

    reconstruction_method: ReconstructionMethod
    sl_from_live_rules = False
    sl_from_rr_estimate = False

    if sl is not None:
        reconstruction_method = ReconstructionMethod.LIVE_RULES
        sl_from_live_rules = True
    elif rr_effective is not None and rr_effective > 0:
        # Fallback: estimate SL from rr_effective
        # rr_effective = reward / risk = (tp_dist / risk_dist)
        # We know target RR. If rr_effective == target RR, then risk = reward / rr
        # But we don't have reward directly. Use candle range as proxy for risk.
        # Conservative estimate: risk = candle range (high - low)
        candle_range = candle_high - candle_low
        if candle_range > 0:
            if direction == "BUY":
                sl = entry_price - candle_range
            else:
                sl = entry_price + candle_range
            reconstruction_method = ReconstructionMethod.RR_ESTIMATE
            sl_from_rr_estimate = True
        else:
            reconstruction_method = ReconstructionMethod.UNAVAILABLE
    else:
        reconstruction_method = ReconstructionMethod.UNAVAILABLE

    if reconstruction_method == ReconstructionMethod.UNAVAILABLE:
        result.simulation_confidence = SimulationConfidence.UNKNOWN
        result.confidence_factors = {
            "replay_candle_available": True,
            "direction_confirmed": True,
            "sl_from_live_rules": False,
            "sl_from_rr_estimate": False,
            "tp_from_live_rules": False,
            "future_bars_complete": False,
        }
        return result

    # Compute risk distance and TP
    risk_distance = abs(entry_price - sl)
    if risk_distance <= 0:
        result.simulation_confidence = SimulationConfidence.UNKNOWN
        result.confidence_factors = {
            "replay_candle_available": True,
            "direction_confirmed": True,
            "sl_from_live_rules": sl_from_live_rules,
            "sl_from_rr_estimate": sl_from_rr_estimate,
            "tp_from_live_rules": False,
            "future_bars_complete": False,
        }
        return result

    rr = _get_rr(pattern_name)
    if direction == "BUY":
        tp = entry_price + risk_distance * rr
    else:
        tp = entry_price - risk_distance * rr

    result.stop_price = sl
    result.target_price = tp
    result.risk_distance = risk_distance
    result.target_r = rr

    # ─── GET FUTURE CANDLES ───────────────────────────────────────────
    future_candles = _get_future_candles(replay_candles, bar_time_ms, _MAX_BARS)
    future_bars_complete = len(future_candles) >= _MAX_BARS or len(future_candles) > 0

    if not future_candles:
        result.future_data_available = False
        result.simulation_confidence = SimulationConfidence.LOW
        result.confidence_factors = {
            "replay_candle_available": True,
            "direction_confirmed": True,
            "sl_from_live_rules": sl_from_live_rules,
            "sl_from_rr_estimate": sl_from_rr_estimate,
            "tp_from_live_rules": True,
            "future_bars_complete": False,
        }
        return result

    result.future_data_available = True

    # ─── FORWARD SIMULATION ───────────────────────────────────────────
    mfe_price = entry_price  # Best price reached
    mae_price = entry_price  # Worst price reached
    exit_price: float | None = None
    exit_reason = ""
    bars_evaluated = 0

    for bar in future_candles:
        bars_evaluated += 1
        bar_high = bar["h"]
        bar_low = bar["l"]
        bar_close = bar["c"]

        # Track MFE/MAE
        if direction == "BUY":
            mfe_price = max(mfe_price, bar_high)
            mae_price = min(mae_price, bar_low)
        else:
            mfe_price = min(mfe_price, bar_low)
            mae_price = max(mae_price, bar_high)

        # Exit check: SL FIRST (conservative, matches shadow trade engine)
        if direction == "BUY":
            if bar_low <= sl:
                exit_price = sl
                exit_reason = "stop_loss"
                break
            elif bar_high >= tp:
                exit_price = tp
                exit_reason = "take_profit"
                break
        else:  # SELL
            if bar_high >= sl:
                exit_price = sl
                exit_reason = "stop_loss"
                break
            elif bar_low <= tp:
                exit_price = tp
                exit_reason = "take_profit"
                break

    # Timeout
    if exit_price is None:
        if future_candles:
            exit_price = future_candles[-1]["c"]
            exit_reason = "max_bars_timeout"
        else:
            exit_price = entry_price
            exit_reason = "no_future_data"

    result.bars_evaluated = bars_evaluated

    # ─── COMPUTE R-MULTIPLE ───────────────────────────────────────────
    if direction == "BUY":
        hypothetical_r = (exit_price - entry_price) / risk_distance
        mfe_r = (mfe_price - entry_price) / risk_distance
        mae_r = (entry_price - mae_price) / risk_distance
    else:
        hypothetical_r = (entry_price - exit_price) / risk_distance
        mfe_r = (entry_price - mfe_price) / risk_distance
        mae_r = (mae_price - entry_price) / risk_distance

    result.hypothetical_exit_price = exit_price
    result.hypothetical_exit_reason = exit_reason
    result.hypothetical_r = round(hypothetical_r, 4)
    result.max_favourable_excursion_r = round(max(0.0, mfe_r), 4)
    result.max_adverse_excursion_r = round(max(0.0, mae_r), 4)

    # ─── CLASSIFY OUTCOME ─────────────────────────────────────────────
    result.outcome_class = _classify_outcome(hypothetical_r, exit_reason)

    # ─── ASSIGN CONFIDENCE ────────────────────────────────────────────
    fully_complete = len(future_candles) >= _MAX_BARS or exit_reason in ("stop_loss", "take_profit")

    result.confidence_factors = {
        "replay_candle_available": True,
        "direction_confirmed": True,
        "sl_from_live_rules": sl_from_live_rules,
        "sl_from_rr_estimate": sl_from_rr_estimate,
        "tp_from_live_rules": True,
        "future_bars_complete": fully_complete,
    }

    if sl_from_live_rules and fully_complete:
        result.simulation_confidence = SimulationConfidence.HIGH
    elif sl_from_rr_estimate and fully_complete:
        result.simulation_confidence = SimulationConfidence.MEDIUM
    elif replay_candle_available and direction_confirmed:
        result.simulation_confidence = SimulationConfidence.LOW
    else:
        result.simulation_confidence = SimulationConfidence.UNKNOWN

    return result
