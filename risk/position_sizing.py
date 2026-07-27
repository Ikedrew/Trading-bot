"""Lot size from account risk and SL distance — with structured logging."""

from __future__ import annotations

import logging

import MetaTrader5 as mt5

from core.mt5_timeout import mt5_call

logger = logging.getLogger(__name__)


def volume_for_risk(
    symbol: str,
    order_type: int,
    price_open: float,
    price_sl: float,
    risk_percent: float,
) -> float | None:
    """
    Risk-based volume using MT5 profit calc for 1.0 lot.
    `order_type`: mt5.ORDER_TYPE_BUY or ORDER_TYPE_SELL.

    Returns None on any failure (MT5 unavailable, invalid inputs, broker constraints).
    Logs structured diagnostics for observability.
    """
    # Pre-validate risk distance
    risk_distance = abs(price_open - price_sl)
    if risk_distance < 1e-12:
        logger.debug(
            "[POSITION_SIZING] REJECTED risk_distance=0 symbol=%s entry=%.5f sl=%.5f",
            symbol, price_open, price_sl,
        )
        return None

    info = mt5_call(mt5.account_info)
    if info is None:
        logger.warning("[POSITION_SIZING] MT5 account_info unavailable — cannot size")
        return None
    balance = float(info.balance)
    risk_money = balance * (risk_percent / 100.0)
    if risk_money <= 0:
        logger.debug("[POSITION_SIZING] risk_money=%.2f (invalid) balance=%.2f pct=%.2f", risk_money, balance, risk_percent)
        return None

    loss_for_one_lot = mt5_call(mt5.order_calc_profit, order_type, symbol, 1.0, price_open, price_sl)
    if loss_for_one_lot is None:
        logger.warning("[POSITION_SIZING] order_calc_profit returned None symbol=%s", symbol)
        return None
    loss_abs = abs(float(loss_for_one_lot))
    if loss_abs < 1e-12:
        logger.debug("[POSITION_SIZING] loss_for_one_lot=0 symbol=%s — tick value issue", symbol)
        return None

    raw = risk_money / loss_abs
    sym = mt5_call(mt5.symbol_info, symbol)
    if sym is None:
        logger.warning("[POSITION_SIZING] symbol_info unavailable symbol=%s", symbol)
        return None

    step = float(sym.volume_step)
    vmin = float(sym.volume_min)
    vmax = float(sym.volume_max)
    if step <= 0:
        logger.warning("[POSITION_SIZING] invalid volume_step=%.8f symbol=%s", step, symbol)
        return None

    steps = int(raw / step)
    vol = max(vmin, min(vmax, steps * step))
    if vol < vmin:
        logger.debug("[POSITION_SIZING] calculated volume below minimum vol=%.8f vmin=%.8f", vol, vmin)
        return None

    vol = round(vol, 8)
    logger.info(
        "[POSITION_SIZING] mode=DYNAMIC symbol=%s risk_pct=%.2f balance=%.2f risk_amount=%.2f volume=%.4f",
        symbol, risk_percent, balance, risk_money, vol,
    )
    return vol
