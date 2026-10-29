"""
Horizon Observation Builder — Converts completed trades into research observations.

Reads from TradeRecord (the single source of truth for completed trades) and
produces HorizonObservation instances grouped by horizon.

CALCULATIONS:
    - R-multiple: realised_move / initial_risk (directional)
    - Hold duration: duration_seconds → minutes
    - MFE: max_favourable_price - entry (directional, in pips)
    - MAE: derived from initial_risk - favourable excursion relationship
    - Win rate: fraction of trades with positive realised R
    - Profit factor: gross_wins / gross_losses
    - Expectancy: (win_rate * avg_win_R) - (loss_rate * avg_loss_R)

THIS MODULE DOES NOT:
    - Modify execution behaviour
    - Change trade management
    - Alter horizon classification
    - Gate any execution path
    - Create duplicate trade records

DATA SOURCE:
    core/trade_journal.py → TradeRecord (frozen dataclass)
    Already contains: entry_price, exit_price, initial_sl, initial_tp,
    entry_time, exit_time, duration_seconds, max_favourable_price,
    close_reason, direction, trade_horizon
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from core.horizon.research_contract import (
    HorizonObservation,
    ACTIVE_CONTRACT_VERSION,
)

if TYPE_CHECKING:
    from core.trade_journal import TradeRecord


# ═══════════════════════════════════════════════════════════════════════════════
# PIP CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

_JPY_PAIRS = frozenset({"USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "NZDJPY", "CADJPY", "CHFJPY"})


def _pip_divisor(symbol: str) -> float:
    """Return the pip size for a symbol (0.0001 for most FX, 0.01 for JPY)."""
    if any(jpy in symbol.upper() for jpy in ("JPY",)):
        return 0.01
    return 0.0001


# ═══════════════════════════════════════════════════════════════════════════════
# PER-TRADE METRICS
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_initial_risk_pips(record: "TradeRecord") -> float:
    """Initial risk in pips (always positive)."""
    risk_price = abs(record.entry_price - record.initial_sl)
    if risk_price == 0:
        return 0.0
    return risk_price / _pip_divisor(record.symbol)


def _compute_realised_move_pips(record: "TradeRecord") -> float:
    """Realised move in pips (positive = profit direction)."""
    if record.direction == "BUY":
        move = record.exit_price - record.entry_price
    else:
        move = record.entry_price - record.exit_price
    return move / _pip_divisor(record.symbol)


def _compute_realised_r(record: "TradeRecord") -> float | None:
    """Realised R-multiple (profit / initial_risk). None if risk is zero."""
    risk_price = abs(record.entry_price - record.initial_sl)
    if risk_price == 0:
        return None
    if record.direction == "BUY":
        move = record.exit_price - record.entry_price
    else:
        move = record.entry_price - record.exit_price
    return move / risk_price


def _compute_mfe_pips(record: "TradeRecord") -> float | None:
    """
    Maximum Favourable Excursion in pips.
    Uses max_favourable_price from Position lifecycle tracking.
    Returns None if data unavailable (max_favourable_price == 0).
    """
    if record.max_favourable_price == 0:
        return None
    if record.direction == "BUY":
        mfe = record.max_favourable_price - record.entry_price
    else:
        mfe = record.entry_price - record.max_favourable_price
    if mfe < 0:
        return 0.0
    return mfe / _pip_divisor(record.symbol)


def _compute_mae_pips(record: "TradeRecord") -> float | None:
    """
    Maximum Adverse Excursion in pips.
    Derived from initial risk: if trade hit SL, MAE = risk. Otherwise MAE < risk.
    Uses close_reason to determine if SL was hit.
    Returns approximate MAE based on available data.
    """
    risk_pips = _compute_initial_risk_pips(record)
    if risk_pips == 0:
        return None

    if record.close_reason in ("sl_hit", "stop_loss_hit", "ON_STOP_LOSS_HIT"):
        # Trade hit stop → MAE equals full initial risk
        return risk_pips

    # Trade closed by TP or management → MAE is at most the realised loss
    realised_pips = _compute_realised_move_pips(record)
    if realised_pips >= 0:
        # Winner: MAE is unknown precisely, estimate as fraction of risk
        # Conservative: assume at least some drawdown occurred
        return None  # Cannot determine without tick-level data
    else:
        # Loser (but not SL): MAE is at least the loss magnitude
        return abs(realised_pips)


def _compute_hold_minutes(record: "TradeRecord") -> float:
    """Holding duration in minutes."""
    return record.duration_seconds / 60.0


# ═══════════════════════════════════════════════════════════════════════════════
# OBSERVATION BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_horizon_observation(
    trades: list["TradeRecord"],
    *,
    horizon: str | None = None,
) -> HorizonObservation:
    """
    Build a HorizonObservation from a list of completed TradeRecords.

    Args:
        trades: List of TradeRecord instances (from trade journal).
        horizon: If provided, filter trades to this horizon only.
                 If None, uses all trades and takes horizon from first trade.

    Returns:
        HorizonObservation with aggregated metrics.
        If trades is empty, returns observation with sample_size=0.
    """
    # Filter by horizon if specified
    if horizon is not None:
        trades = [t for t in trades if getattr(t, "trade_horizon", "SCALP") == horizon]

    if not trades:
        _h = horizon or "UNKNOWN"
        _version = ACTIVE_CONTRACT_VERSION.get(_h, f"{_h}_RESEARCH_V1")
        return HorizonObservation(
            horizon=_h,
            profile_version=_version,
            sample_size=0,
        )

    _horizon = getattr(trades[0], "trade_horizon", "SCALP")
    _version = ACTIVE_CONTRACT_VERSION.get(_horizon, f"{_horizon}_RESEARCH_V1")

    # Compute per-trade metrics
    r_multiples: list[float] = []
    move_pips: list[float] = []
    hold_minutes: list[float] = []
    mfe_values: list[float] = []
    mae_values: list[float] = []
    exit_reasons: dict[str, int] = {}
    wins: int = 0
    losses: int = 0
    gross_win_r: float = 0.0
    gross_loss_r: float = 0.0

    for trade in trades:
        # R-multiple
        _r = _compute_realised_r(trade)
        if _r is not None:
            r_multiples.append(_r)
            if _r > 0:
                wins += 1
                gross_win_r += _r
            else:
                losses += 1
                gross_loss_r += abs(_r)

        # Move (pips)
        _move = _compute_realised_move_pips(trade)
        move_pips.append(abs(_move))

        # Hold duration
        hold_minutes.append(_compute_hold_minutes(trade))

        # MFE
        _mfe = _compute_mfe_pips(trade)
        if _mfe is not None:
            mfe_values.append(_mfe)

        # MAE
        _mae = _compute_mae_pips(trade)
        if _mae is not None:
            mae_values.append(_mae)

        # Exit reasons
        _reason = trade.close_reason or "unknown"
        exit_reasons[_reason] = exit_reasons.get(_reason, 0) + 1

    # Aggregations
    _sample = len(trades)
    _total_decided = wins + losses

    # Move stats
    _move_avg = statistics.mean(move_pips) if move_pips else 0.0
    _move_median = statistics.median(move_pips) if move_pips else 0.0
    _move_p95 = _percentile(move_pips, 95) if len(move_pips) >= 2 else _move_avg

    # Hold stats
    _hold_avg = statistics.mean(hold_minutes) if hold_minutes else 0.0
    _hold_median = statistics.median(hold_minutes) if hold_minutes else 0.0

    # R stats
    _observed_rr = statistics.mean(r_multiples) if r_multiples else 0.0

    # Win rate
    _win_rate = wins / _total_decided if _total_decided > 0 else 0.0

    # Profit factor
    _pf = gross_win_r / gross_loss_r if gross_loss_r > 0 else (999.0 if gross_win_r > 0 else 0.0)

    # Expectancy: (win_rate * avg_win_R) - (loss_rate * avg_loss_R)
    _avg_win_r = gross_win_r / wins if wins > 0 else 0.0
    _avg_loss_r = gross_loss_r / losses if losses > 0 else 0.0
    _loss_rate = 1.0 - _win_rate
    _expectancy = (_win_rate * _avg_win_r) - (_loss_rate * _avg_loss_r)

    # Excursion
    _mae_avg = statistics.mean(mae_values) if mae_values else 0.0
    _mfe_avg = statistics.mean(mfe_values) if mfe_values else 0.0

    return HorizonObservation(
        horizon=_horizon,
        profile_version=_version,
        sample_size=_sample,
        observed_move_average_pips=round(_move_avg, 2),
        observed_move_median_pips=round(_move_median, 2),
        observed_move_p95_pips=round(_move_p95, 2),
        observed_hold_average_minutes=round(_hold_avg, 1),
        observed_hold_median_minutes=round(_hold_median, 1),
        observed_rr=round(_observed_rr, 4),
        observed_win_rate=round(_win_rate, 4),
        observed_profit_factor=round(_pf, 3),
        observed_expectancy=round(_expectancy, 4),
        observed_mae_pips=round(_mae_avg, 2),
        observed_mfe_pips=round(_mfe_avg, 2),
        exit_reasons=exit_reasons,
    )


def build_all_horizon_observations(
    trades: list["TradeRecord"],
) -> dict[str, HorizonObservation]:
    """
    Build observations for ALL horizons from a mixed list of trades.

    Returns dict keyed by horizon name. Horizons with zero trades
    get an observation with sample_size=0 (never fails).
    """
    result: dict[str, HorizonObservation] = {}
    for horizon in ("SCALP", "INTRADAY", "EXTENDED"):
        result[horizon] = build_horizon_observation(trades, horizon=horizon)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _percentile(data: list[float], pct: float) -> float:
    """Compute percentile from sorted data (simple linear interpolation)."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (pct / 100.0) * (len(sorted_data) - 1)
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[-1]
    d = k - f
    return sorted_data[f] + d * (sorted_data[c] - sorted_data[f])
