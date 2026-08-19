"""
Trade Lifecycle State Machine (TLSM) — Self-contained post-entry trade management.

Each trade becomes a CLOSED SYSTEM after entry. The lifecycle FSM governs
trade behaviour using ONLY trade-internal telemetry.

HARD ISOLATION RULE:
    After entry, the trade has NO access to:
    ❌ FSM bias state
    ❌ regime_label
    ❌ strategy classification
    ❌ scoring / EV / ranking outputs
    ❌ divergence signals

    It may ONLY use:
    ✔ entry_price
    ✔ current_price
    ✔ unrealised PnL
    ✔ time in trade (bars elapsed)
    ✔ price movement relative to entry
    ✔ trade_profile (frozen at entry — never updated)

Lifecycle:
    ACTIVE → PROFIT_EXPANSION → MATURITY → EXIT_CONDITIONING → CLOSED

Architecture:
    - One TLSM instance per open trade
    - Updated once per bar with price data ONLY
    - Produces exit signals when conditions met
    - No external queries, no external state reads

Design: deterministic, self-contained, no learning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ─── LIFECYCLE PHASES ─────────────────────────────────────────────────────────

class TradePhase(str, Enum):
    """Trade lifecycle states."""
    ACTIVE = "ACTIVE"
    PROFIT_EXPANSION = "PROFIT_EXPANSION"
    MATURITY = "MATURITY"
    EXIT_CONDITIONING = "EXIT_CONDITIONING"
    CLOSED = "CLOSED"


# ─── EXIT SIGNALS ─────────────────────────────────────────────────────────────

class ExitSignal(str, Enum):
    """Reason for exit signal generation."""
    NONE = "NONE"
    TRAILING_STOP_HIT = "TRAILING_STOP_HIT"
    TIME_STOP = "TIME_STOP"
    DRAWDOWN_FROM_PEAK = "DRAWDOWN_FROM_PEAK"
    STAGNATION = "STAGNATION"
    BREAK_EVEN_STOP = "BREAK_EVEN_STOP"
    PARTIAL_TP = "PARTIAL_TP"


# ─── TLSM PARAMETERS (from trade_profile at entry — frozen) ──────────────────

@dataclass(frozen=True)
class TLSMParams:
    """
    Parameters governing lifecycle transitions.

    Set ONCE at trade creation from trade_profile.
    NEVER updated during trade lifetime.
    """
    break_even_trigger_r: float = 1.0       # R-multiple to move SL to entry
    profit_expansion_trigger_r: float = 1.0 # R at which phase → PROFIT_EXPANSION
    trailing_start_r: float = 1.0           # R at which trailing begins
    trailing_step_fraction: float = 0.5     # Trail step as fraction of risk distance
    maturity_bars: int = 30                 # Bars before MATURITY phase
    stagnation_bars: int = 10              # Bars of no new highs before stagnation exit
    drawdown_from_peak_r: float = 0.5       # R drawdown from peak → exit signal
    max_hold_bars: int = 0                  # Forced exit after N bars (0 = unlimited)
    partial_tp_r: float = 0.0              # R at which partial TP triggers (0 = disabled)
    partial_tp_fraction: float = 0.0       # Fraction to close at partial


# ─── TRADE STATE (MUTABLE — one instance per trade) ───────────────────────────

@dataclass
class TradeLifecycleState:
    """
    Internal state of a single trade's lifecycle.

    Contains ONLY trade-internal metrics. No external references.
    """
    # Identity
    trade_id: str = ""
    symbol: str = ""
    side: str = "BUY"                  # "BUY" or "SELL"

    # Fixed at entry (never changes)
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    risk_distance: float = 0.0         # |entry - SL|

    # Lifecycle
    phase: TradePhase = TradePhase.ACTIVE
    bars_elapsed: int = 0
    bars_since_new_peak: int = 0

    # Performance metrics (updated each bar)
    current_price: float = 0.0
    r_multiple: float = 0.0            # Current unrealised PnL in R
    peak_favourable_r: float = 0.0     # Best R achieved
    drawdown_from_peak_r: float = 0.0  # Current drawdown from peak in R
    trailing_stop: float = 0.0         # Current trailing stop price (0 = not active)

    # Exit
    exit_signal: ExitSignal = ExitSignal.NONE
    partial_tp_triggered: bool = False

    # Parameters (frozen at creation)
    params: TLSMParams = field(default_factory=TLSMParams)


# ─── FACTORY ──────────────────────────────────────────────────────────────────

def create_trade_lifecycle(
    *,
    trade_id: str,
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    params: TLSMParams,
) -> TradeLifecycleState:
    """
    Create a new trade lifecycle instance at entry time.

    After this point, the trade is a closed system.
    """
    risk_distance = abs(entry_price - stop_loss)

    return TradeLifecycleState(
        trade_id=trade_id,
        symbol=symbol,
        side=side,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_distance=risk_distance,
        phase=TradePhase.ACTIVE,
        current_price=entry_price,
        trailing_stop=stop_loss,
        params=params,
    )


# ─── MAIN UPDATE (CALLED ONCE PER BAR) ───────────────────────────────────────

def update_trade_lifecycle(
    state: TradeLifecycleState,
    current_price: float,
) -> TradeLifecycleState:
    """
    Advance trade lifecycle by one bar.

    ONLY input: current_price.
    No external state, no FSM, no regime, no scoring.

    Args:
        state: Current trade lifecycle state (mutated in place)
        current_price: Latest price for this symbol

    Returns:
        Same state object (mutated). Check state.exit_signal for exit triggers.
    """
    if state.phase == TradePhase.CLOSED:
        return state

    # ─── UPDATE TELEMETRY ─────────────────────────────────────────────
    state.current_price = current_price
    state.bars_elapsed += 1

    # Compute R-multiple
    if state.risk_distance > 0:
        if state.side == "BUY":
            pnl = current_price - state.entry_price
        else:
            pnl = state.entry_price - current_price
        state.r_multiple = round(pnl / state.risk_distance, 4)

    # Track peak favourable excursion
    if state.r_multiple > state.peak_favourable_r:
        state.peak_favourable_r = state.r_multiple
        state.bars_since_new_peak = 0
    else:
        state.bars_since_new_peak += 1

    # Drawdown from peak
    state.drawdown_from_peak_r = round(state.peak_favourable_r - state.r_multiple, 4)

    # ─── PHASE TRANSITIONS ────────────────────────────────────────────

    params = state.params

    # Time stop (any phase)
    if params.max_hold_bars > 0 and state.bars_elapsed >= params.max_hold_bars:
        state.phase = TradePhase.EXIT_CONDITIONING
        state.exit_signal = ExitSignal.TIME_STOP
        return state

    # Phase-specific logic
    if state.phase == TradePhase.ACTIVE:
        _update_active(state, params)

    elif state.phase == TradePhase.PROFIT_EXPANSION:
        _update_profit_expansion(state, params)

    elif state.phase == TradePhase.MATURITY:
        _update_maturity(state, params)

    elif state.phase == TradePhase.EXIT_CONDITIONING:
        # Already signalled — maintain state
        pass

    # ─── TRAILING STOP CHECK (all phases after activation) ────────────
    if state.trailing_stop > 0 and state.phase != TradePhase.ACTIVE:
        _check_trailing_stop(state)

    # ─── PARTIAL TP CHECK ─────────────────────────────────────────────
    if params.partial_tp_r > 0 and not state.partial_tp_triggered:
        if state.r_multiple >= params.partial_tp_r:
            state.partial_tp_triggered = True
            state.exit_signal = ExitSignal.PARTIAL_TP

    return state


# ─── PHASE UPDATE FUNCTIONS ───────────────────────────────────────────────────

def _update_active(state: TradeLifecycleState, params: TLSMParams) -> None:
    """ACTIVE phase: waiting for profit expansion trigger."""
    # Move to break-even
    if params.break_even_trigger_r > 0 and state.r_multiple >= params.break_even_trigger_r:
        if state.side == "BUY":
            state.trailing_stop = max(state.trailing_stop, state.entry_price)
        else:
            state.trailing_stop = min(state.trailing_stop, state.entry_price) if state.trailing_stop > 0 else state.entry_price

    # Transition to PROFIT_EXPANSION
    if state.r_multiple >= params.profit_expansion_trigger_r:
        state.phase = TradePhase.PROFIT_EXPANSION
        # Activate trailing
        if params.trailing_start_r > 0 and state.r_multiple >= params.trailing_start_r:
            _activate_trailing(state, params)


def _update_profit_expansion(state: TradeLifecycleState, params: TLSMParams) -> None:
    """PROFIT_EXPANSION: tracking favourable movement."""
    # Update trailing stop position
    if params.trailing_start_r > 0 and state.r_multiple >= params.trailing_start_r:
        _activate_trailing(state, params)

    # Check for maturity transition
    if state.bars_elapsed >= params.maturity_bars:
        state.phase = TradePhase.MATURITY

    # Drawdown from peak → exit
    if params.drawdown_from_peak_r > 0 and state.drawdown_from_peak_r >= params.drawdown_from_peak_r:
        state.phase = TradePhase.EXIT_CONDITIONING
        state.exit_signal = ExitSignal.DRAWDOWN_FROM_PEAK


def _update_maturity(state: TradeLifecycleState, params: TLSMParams) -> None:
    """MATURITY: momentum likely decelerating."""
    # Stagnation detection
    if state.bars_since_new_peak >= params.stagnation_bars:
        state.phase = TradePhase.EXIT_CONDITIONING
        state.exit_signal = ExitSignal.STAGNATION
        return

    # Drawdown from peak
    if params.drawdown_from_peak_r > 0 and state.drawdown_from_peak_r >= params.drawdown_from_peak_r:
        state.phase = TradePhase.EXIT_CONDITIONING
        state.exit_signal = ExitSignal.DRAWDOWN_FROM_PEAK


# ─── TRAILING STOP ────────────────────────────────────────────────────────────

def _activate_trailing(state: TradeLifecycleState, params: TLSMParams) -> None:
    """Update trailing stop to lock in profit."""
    if state.risk_distance <= 0:
        return

    trail_distance = state.risk_distance * params.trailing_step_fraction

    if state.side == "BUY":
        new_trail = state.current_price - trail_distance
        state.trailing_stop = max(state.trailing_stop, new_trail)
    else:
        new_trail = state.current_price + trail_distance
        if state.trailing_stop <= 0:
            state.trailing_stop = new_trail
        else:
            state.trailing_stop = min(state.trailing_stop, new_trail)


def _check_trailing_stop(state: TradeLifecycleState) -> None:
    """Check if trailing stop has been hit."""
    if state.side == "BUY":
        if state.current_price <= state.trailing_stop:
            state.phase = TradePhase.EXIT_CONDITIONING
            state.exit_signal = ExitSignal.TRAILING_STOP_HIT
    else:
        if state.current_price >= state.trailing_stop:
            state.phase = TradePhase.EXIT_CONDITIONING
            state.exit_signal = ExitSignal.TRAILING_STOP_HIT


# ─── LOGGING / NARRATIVE (read-only) ─────────────────────────────────────────

def format_lifecycle_status(state: TradeLifecycleState) -> str:
    """Format current trade lifecycle state for logging. Purely observational."""
    return (
        f"[TLSM] {state.symbol} | {state.phase.value} | "
        f"R={state.r_multiple:+.2f} | peak={state.peak_favourable_r:.2f}R | "
        f"dd={state.drawdown_from_peak_r:.2f}R | bars={state.bars_elapsed} | "
        f"trail={state.trailing_stop:.5f} | exit={state.exit_signal.value}"
    )
