"""
Exit checks and stop adjustments from price/time/risk only.

No patterns, bias, or strategy inputs.
"""

from __future__ import annotations

from typing import Literal

from strategy.signals import Side


def risk_unit_r(entry: float, initial_sl: float) -> float:
    return abs(entry - initial_sl)


def check_exit_trigger(
    side: Side,
    bid: float,
    ask: float,
    sl: float,
    tp: float,
) -> Literal["sl", "tp"] | None:
    """
    Conservative triggers: long uses bid vs levels, short uses ask.
    """

    if side is Side.BUY:
        if bid <= sl:
            return "sl"
        if bid >= tp:
            return "tp"
    else:
        if ask >= sl:
            return "sl"
        if ask <= tp:
            return "tp"
    return None


def maybe_break_even_sl(
    side: Side,
    bid: float,
    ask: float,
    *,
    entry: float,
    initial_sl: float,
    current_sl: float,
    trigger_rr: float,
    buffer_rr: float,
) -> float | None:
    """Return new SL price if BE rule activates, else None.

    Args:
        buffer_rr: Buffer beyond entry in R-fraction units.
                   0.1 means move SL to entry + 0.1 * risk_distance.
    """

    if trigger_rr <= 0:
        return None

    r = risk_unit_r(entry, initial_sl)
    if r <= 0:
        return None

    buffer_price = buffer_rr * r

    if side is Side.BUY:
        favourable = bid - entry
        if favourable < trigger_rr * r:
            return None
        be = entry + buffer_price
        return max(current_sl, be)
    else:
        favourable = entry - ask
        if favourable < trigger_rr * r:
            return None
        # Short: SL is above price — lock near entry from above.
        be = entry - buffer_price
        return min(current_sl, be)


def maybe_trailing_sl(
    side: Side,
    bid: float,
    ask: float,
    *,
    entry: float,
    initial_sl: float,
    current_sl: float,
    mfe_extreme: float,
    trail_step: float,
    start_rr: float,
) -> float | None:
    """
    Trail stop `trail_step` behind best favourable extreme.
    mfe_extreme: best bid seen (BUY) or best ask seen (SELL) for excursion.
    """

    if trail_step <= 0:
        return None

    r = risk_unit_r(entry, initial_sl)
    if r <= 0:
        return None

    if side is Side.BUY:
        if start_rr > 0 and (mfe_extreme - entry) < start_rr * r:
            return None
        candidate = mfe_extreme - trail_step
        return max(current_sl, candidate)
    else:
        if start_rr > 0 and (entry - mfe_extreme) < start_rr * r:
            return None
        candidate = mfe_extreme + trail_step
        return min(current_sl, candidate)


def update_mfe_extreme(side: Side, bid: float, ask: float, current: float) -> float:
    if side is Side.BUY:
        return max(current, bid)
    return min(current, ask)


def update_mae_extreme(side: Side, bid: float, ask: float, current: float | None) -> float:
    """Track the worst ADVERSE excursion price, symmetric with update_mfe_extreme.

    Observational only — never used for exit/stop/close decisions.

    Uses the SAME per-side tick price as the MFE tracker so favourable and
    adverse excursions are directly comparable:
        BUY  favourable = highest bid  → adverse = lowest bid
        SELL favourable = lowest ask   → adverse = highest ask

    ``current`` is None when no adverse observation exists yet (unknown state);
    the first observation seeds it. Never fabricates a value.
    """
    if side is Side.BUY:
        return bid if current is None else min(current, bid)
    return ask if current is None else max(current, ask)
