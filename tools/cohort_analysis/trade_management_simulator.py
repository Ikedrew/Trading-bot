"""
Trade Management Counterfactual Simulator — Post-trade "what if" analysis.

Simulates how different trade management strategies would have affected
outcomes using MFE (Max Favorable Excursion) and MAE (Max Adverse Excursion).

Strategies simulated:
  1. Break-Even: Move SL to 0R after reaching +1R
  2. Trailing Stop: Trail at configurable distance after +1R
  3. Partial Take-Profit: Close portion at TP1, remainder runs

IMPORTANT:
  This module is STRICTLY POST-TRADE ANALYSIS.
  It MUST NOT affect execution, scoring, or risk logic.
  It operates on historical MFE/MAE data only.

All results are expressed in R-multiples (risk units).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ─── SIMULATION RESULT TYPES ──────────────────────────────────────────────────

@dataclass(frozen=True)
class BreakEvenResult:
    """Result of break-even stop simulation."""
    triggered: bool          # Did price reach BE trigger level (+1R)?
    outcome_r: float         # Final R if BE was active (0R if triggered then reversed)
    actual_outcome_r: float  # Original trade outcome for comparison
    improvement_r: float     # Difference: simulated - actual

    def to_dict(self) -> dict[str, Any]:
        return {
            "triggered": self.triggered,
            "outcome_r": round(self.outcome_r, 3),
            "actual_outcome_r": round(self.actual_outcome_r, 3),
            "improvement_r": round(self.improvement_r, 3),
        }


@dataclass(frozen=True)
class TrailingStopResult:
    """Result of trailing stop simulation."""
    activated: bool          # Did price reach trailing activation level?
    exit_r: float            # R at trailing stop exit
    max_locked_r: float      # Highest R the trailing stop locked in
    actual_outcome_r: float  # Original trade outcome for comparison
    improvement_r: float     # Difference: simulated - actual

    def to_dict(self) -> dict[str, Any]:
        return {
            "activated": self.activated,
            "exit_r": round(self.exit_r, 3),
            "max_locked_r": round(self.max_locked_r, 3),
            "actual_outcome_r": round(self.actual_outcome_r, 3),
            "improvement_r": round(self.improvement_r, 3),
        }


@dataclass(frozen=True)
class PartialTakeProfitResult:
    """Result of partial take-profit simulation."""
    tp1_hit: bool            # Did price reach TP1 level?
    tp1_r: float             # R level of TP1
    tp1_fraction: float      # Fraction closed at TP1
    remainder_r: float       # R of the remaining position at final exit
    combined_r: float        # Weighted average R of total position
    actual_outcome_r: float  # Original trade outcome for comparison
    improvement_r: float     # Difference: simulated - actual

    def to_dict(self) -> dict[str, Any]:
        return {
            "tp1_hit": self.tp1_hit,
            "tp1_r": round(self.tp1_r, 3),
            "tp1_fraction": round(self.tp1_fraction, 3),
            "remainder_r": round(self.remainder_r, 3),
            "combined_r": round(self.combined_r, 3),
            "actual_outcome_r": round(self.actual_outcome_r, 3),
            "improvement_r": round(self.improvement_r, 3),
        }


@dataclass(frozen=True)
class CounterfactualResult:
    """Combined result of all three management simulations for one trade."""
    mfe_r: float
    mae_r: float
    actual_outcome_r: float
    break_even: BreakEvenResult
    trailing_stop: TrailingStopResult
    partial_tp: PartialTakeProfitResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "mfe_r": round(self.mfe_r, 3),
            "mae_r": round(self.mae_r, 3),
            "actual_outcome_r": round(self.actual_outcome_r, 3),
            "break_even": self.break_even.to_dict(),
            "trailing_stop": self.trailing_stop.to_dict(),
            "partial_tp": self.partial_tp.to_dict(),
        }


# ─── SIMULATION CONFIGURATION ─────────────────────────────────────────────────

@dataclass(frozen=True)
class SimulationConfig:
    """Configuration for counterfactual simulations."""

    # Break-even
    be_trigger_r: float = 1.0        # R level at which BE activates
    be_buffer_r: float = 0.0         # Buffer above entry (0 = true breakeven)

    # Trailing stop
    trail_activation_r: float = 1.0  # R level at which trailing starts
    trail_distance_r: float = 1.0    # Distance trailing stop sits behind MFE

    # Partial take-profit
    tp1_level_r: float = 1.0         # R level for first partial close
    tp1_fraction: float = 0.5        # Fraction of position closed at TP1
    tp2_level_r: float = 2.0         # R level for second partial (remainder)


DEFAULT_CONFIG = SimulationConfig()


# ─── SIMULATION FUNCTIONS ──────────────────────────────────────────────────────

def simulate_break_even(
    mfe_r: float,
    mae_r: float,
    actual_outcome_r: float,
    *,
    config: SimulationConfig = DEFAULT_CONFIG,
) -> BreakEvenResult:
    """
    Simulate break-even stop management.

    Logic:
    - If MFE >= be_trigger_r: BE was triggered (SL moved to 0R + buffer)
    - After BE: worst case is 0R (not -1R)
    - If actual outcome < 0R and BE triggered: outcome becomes max(actual, buffer)
    - If actual outcome > 0R: no change (already profitable)

    Args:
        mfe_r: Maximum favorable excursion in R (always >= 0)
        mae_r: Maximum adverse excursion in R (always <= 0 or stored as negative)
        actual_outcome_r: Actual trade result in R

    Returns:
        BreakEvenResult with simulated outcome.
    """
    triggered = mfe_r >= config.be_trigger_r

    if not triggered:
        # Never reached BE level — outcome unchanged
        return BreakEvenResult(
            triggered=False,
            outcome_r=actual_outcome_r,
            actual_outcome_r=actual_outcome_r,
            improvement_r=0.0,
        )

    # BE triggered: worst case is now buffer_r instead of -1R
    if actual_outcome_r < config.be_buffer_r:
        # Trade reversed past BE after triggering — capped at buffer
        simulated = config.be_buffer_r
    else:
        # Trade was profitable anyway — no change
        simulated = actual_outcome_r

    return BreakEvenResult(
        triggered=True,
        outcome_r=simulated,
        actual_outcome_r=actual_outcome_r,
        improvement_r=simulated - actual_outcome_r,
    )


def simulate_trailing_stop(
    mfe_r: float,
    mae_r: float,
    actual_outcome_r: float,
    *,
    config: SimulationConfig = DEFAULT_CONFIG,
) -> TrailingStopResult:
    """
    Simulate trailing stop management.

    Logic:
    - If MFE >= trail_activation_r: trailing starts
    - Trailing stop sits at MFE - trail_distance_r
    - Exit occurs at max(MFE - trail_distance, actual_outcome)
    - Can't do better than MFE - trail_distance (the trail exit point)

    Simplified model (no tick-by-tick replay):
    - Assumes price reached MFE first, THEN retraced
    - Trail exit = MFE - trail_distance_r (bounded by 0)

    Args:
        mfe_r: Maximum favorable excursion in R
        mae_r: Maximum adverse excursion in R
        actual_outcome_r: Actual trade result in R

    Returns:
        TrailingStopResult with simulated outcome.
    """
    activated = mfe_r >= config.trail_activation_r

    if not activated:
        # Never reached activation — outcome unchanged
        return TrailingStopResult(
            activated=False,
            exit_r=actual_outcome_r,
            max_locked_r=0.0,
            actual_outcome_r=actual_outcome_r,
            improvement_r=0.0,
        )

    # Trailing activated: exit at MFE - trail_distance
    trail_exit = max(0.0, mfe_r - config.trail_distance_r)

    # The simulated outcome is:
    # - If actual outcome > trail_exit: trade hit TP before trail triggered exit → use actual
    # - If actual outcome <= trail_exit: trail would have exited earlier → use trail_exit
    simulated = max(trail_exit, actual_outcome_r) if actual_outcome_r > trail_exit else trail_exit

    # Max locked R is the highest the trail reached
    max_locked = trail_exit

    return TrailingStopResult(
        activated=True,
        exit_r=simulated,
        max_locked_r=max_locked,
        actual_outcome_r=actual_outcome_r,
        improvement_r=simulated - actual_outcome_r,
    )


def simulate_partial_take_profit(
    mfe_r: float,
    mae_r: float,
    actual_outcome_r: float,
    *,
    config: SimulationConfig = DEFAULT_CONFIG,
) -> PartialTakeProfitResult:
    """
    Simulate partial take-profit management.

    Logic:
    - If MFE >= tp1_level_r: close tp1_fraction at tp1_level_r
    - Remainder runs to actual exit
    - Combined R = (tp1_fraction × tp1_level) + ((1 - tp1_fraction) × actual_outcome)

    Args:
        mfe_r: Maximum favorable excursion in R
        mae_r: Maximum adverse excursion in R
        actual_outcome_r: Actual trade result in R

    Returns:
        PartialTakeProfitResult with simulated outcome.
    """
    tp1_hit = mfe_r >= config.tp1_level_r

    if not tp1_hit:
        # Never reached TP1 — outcome unchanged (full position exits at actual)
        return PartialTakeProfitResult(
            tp1_hit=False,
            tp1_r=config.tp1_level_r,
            tp1_fraction=config.tp1_fraction,
            remainder_r=actual_outcome_r,
            combined_r=actual_outcome_r,
            actual_outcome_r=actual_outcome_r,
            improvement_r=0.0,
        )

    # TP1 hit: close fraction at TP1, remainder exits at actual outcome
    tp1_portion = config.tp1_fraction * config.tp1_level_r
    remainder_portion = (1.0 - config.tp1_fraction) * actual_outcome_r
    combined = tp1_portion + remainder_portion

    return PartialTakeProfitResult(
        tp1_hit=True,
        tp1_r=config.tp1_level_r,
        tp1_fraction=config.tp1_fraction,
        remainder_r=actual_outcome_r,
        combined_r=combined,
        actual_outcome_r=actual_outcome_r,
        improvement_r=combined - actual_outcome_r,
    )


# ─── COMBINED SIMULATION ──────────────────────────────────────────────────────

def simulate_all(
    mfe_r: float,
    mae_r: float,
    actual_outcome_r: float,
    *,
    config: SimulationConfig = DEFAULT_CONFIG,
) -> CounterfactualResult:
    """
    Run all three counterfactual simulations for a single trade.

    Args:
        mfe_r: Maximum favorable excursion in R (>= 0)
        mae_r: Maximum adverse excursion in R (<= 0 typically)
        actual_outcome_r: Actual trade outcome in R

    Returns:
        CounterfactualResult combining all simulations.
    """
    be = simulate_break_even(mfe_r, mae_r, actual_outcome_r, config=config)
    trail = simulate_trailing_stop(mfe_r, mae_r, actual_outcome_r, config=config)
    partial = simulate_partial_take_profit(mfe_r, mae_r, actual_outcome_r, config=config)

    return CounterfactualResult(
        mfe_r=mfe_r,
        mae_r=mae_r,
        actual_outcome_r=actual_outcome_r,
        break_even=be,
        trailing_stop=trail,
        partial_tp=partial,
    )


# ─── BATCH SIMULATION ─────────────────────────────────────────────────────────

def simulate_batch(
    trades: list[dict[str, Any]],
    *,
    config: SimulationConfig = DEFAULT_CONFIG,
) -> list[dict[str, Any]]:
    """
    Run counterfactual simulations on a batch of enriched trade records.

    Expects each trade to have:
        - outcome_rr (float): actual outcome in R
        - mfe_r (float): max favorable excursion in R (optional — estimated from outcome_rr if missing)
        - mae_r (float): max adverse excursion in R (optional)

    Returns:
        List of trades enriched with counterfactual simulation results.
    """
    results = []

    for trade in trades:
        enriched = dict(trade)

        actual_r = trade.get("outcome_rr") or trade.get("outcome_rr", 0.0)
        if actual_r is None:
            actual_r = 0.0

        # Extract or estimate MFE/MAE
        mfe_r = trade.get("mfe_r")
        mae_r = trade.get("mae_r")

        # If MFE not available, estimate from outcome
        if mfe_r is None:
            # Conservative estimate: winners reached at least their exit R
            # Losers may have briefly gone positive (estimate +0.5R)
            mfe_r = max(actual_r, 0.5) if actual_r >= 0 else 0.5

        if mae_r is None:
            # Conservative estimate: winners may have dipped slightly
            # Losers reached at least their loss level
            mae_r = min(actual_r, -0.3) if actual_r < 0 else -0.3

        sim = simulate_all(mfe_r, mae_r, actual_r, config=config)
        enriched["counterfactual"] = sim.to_dict()

        results.append(enriched)

    return results
