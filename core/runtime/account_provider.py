"""Runtime Account & Broker Context Provider — Live MT5 data, no defaults.

Reads current account state and symbol conditions directly from MT5.
Returns typed context objects consumed by the V10 pipeline.

If MT5 cannot provide information, returns an "unavailable" context
that causes the pipeline to reject execution cleanly.
"""

from __future__ import annotations

import logging

from core.v10.risk_model import AccountContext
from core.v10.broker_context import BrokerContext

logger = logging.getLogger(__name__)

# MT5 imports — may fail in test environments
try:
    import MetaTrader5 as mt5
    from core.mt5_timeout import mt5_call
except ImportError:
    mt5 = None  # type: ignore
    mt5_call = None  # type: ignore


def get_account_context(
    *,
    open_positions: int = 0,
    symbols_with_positions: list[str] | None = None,
    daily_loss_pct: float = 0.0,
    current_open_risk_pct: float = 0.0,
) -> AccountContext:
    """
    Build AccountContext from live MT5 account data.

    Returns unavailable context (balance=0) if MT5 cannot provide info.
    """
    try:
        if mt5_call is None:
            return _unavailable_account()

        info = mt5_call(mt5.account_info)
        if info is None:
            logger.warning("[ACCOUNT_PROVIDER] MT5 account_info unavailable")
            return _unavailable_account()

        return AccountContext(
            login=int(getattr(info, "login", 0)),
            server=str(getattr(info, "server", "")),
            currency=str(getattr(info, "currency", "")),
            leverage=int(getattr(info, "leverage", 0)),
            margin_mode=int(getattr(info, "margin_mode", 0)),
            balance=float(info.balance),
            equity=float(info.equity),
            credit=float(getattr(info, "credit", 0.0)),
            profit=float(getattr(info, "profit", 0.0)),
            margin=float(getattr(info, "margin", 0.0)),
            margin_free=float(getattr(info, "margin_free", 0.0)),
            margin_level=float(getattr(info, "margin_level", 0.0)),
            stop_out_level=float(getattr(info, "margin_so_so", 0.0)),
            current_open_risk_pct=current_open_risk_pct,
            open_positions=open_positions,
            daily_loss_pct=daily_loss_pct,
            symbols_with_positions=symbols_with_positions or [],
        )

    except Exception as exc:
        logger.warning("[ACCOUNT_PROVIDER] failed: %s", exc)
        return _unavailable_account()


def get_broker_context(
    *,
    symbol: str,
    bid: float = 0.0,
    ask: float = 0.0,
) -> BrokerContext:
    """
    Build BrokerContext from live MT5 terminal + symbol data.

    Returns unavailable context (connected=False) if MT5 cannot provide info.
    """
    try:
        if mt5_call is None:
            return _unavailable_broker()

        # Connection
        terminal_info = mt5_call(mt5.terminal_info)
        connected = terminal_info is not None and getattr(terminal_info, "connected", False)
        if not connected:
            return _unavailable_broker()

        terminal_name = str(getattr(terminal_info, "name", ""))
        server = str(getattr(terminal_info, "company", ""))

        # Symbol info
        sym_info = mt5_call(mt5.symbol_info, symbol)
        if sym_info is None:
            return BrokerContext(connected=True, server=server, terminal_name=terminal_name,
                                symbol=symbol, symbol_available=False)

        # Trade mode
        trade_mode = int(getattr(sym_info, "trade_mode", 0))
        market_open = trade_mode >= 4  # SYMBOL_TRADE_MODE_FULL

        # Pricing
        spread_price = abs(ask - bid) if ask > 0 and bid > 0 else 0.0
        if spread_price == 0.0:
            tick = mt5_call(mt5.symbol_info_tick, symbol)
            if tick is not None:
                bid = float(tick.bid)
                ask = float(tick.ask)
                spread_price = abs(ask - bid)

        # Symbol metadata
        digits = int(getattr(sym_info, "digits", 0))
        point = float(getattr(sym_info, "point", 0.0))
        contract_size = float(getattr(sym_info, "trade_contract_size", 0.0))
        tick_size = float(getattr(sym_info, "trade_tick_size", 0.0))
        tick_value = float(getattr(sym_info, "trade_tick_value", 0.0))
        volume_min = float(getattr(sym_info, "volume_min", 0.0))
        volume_max = float(getattr(sym_info, "volume_max", 0.0))
        volume_step = float(getattr(sym_info, "volume_step", 0.0))
        stops_level = int(getattr(sym_info, "trade_stops_level", 0))
        freeze_level = int(getattr(sym_info, "trade_freeze_level", 0))
        execution_mode = int(getattr(sym_info, "trade_exemode", 0))

        # Account margin
        acct = mt5_call(mt5.account_info)
        available_margin = float(acct.margin_free) if acct else 0.0
        account_balance = float(acct.balance) if acct else 0.0

        # Positions
        positions = mt5_call(mt5.positions_total)
        existing_positions = int(positions) if positions is not None else 0

        return BrokerContext(
            connected=True,
            server=server,
            terminal_name=terminal_name,
            symbol=symbol,
            symbol_available=True,
            market_open=market_open,
            trade_mode=trade_mode,
            execution_mode=execution_mode,
            bid=bid,
            ask=ask,
            spread=spread_price,
            digits=digits,
            point=point,
            contract_size=contract_size,
            tick_size=tick_size,
            tick_value=tick_value,
            volume_min=volume_min,
            volume_max=volume_max,
            volume_step=volume_step,
            stops_level=stops_level,
            freeze_level=freeze_level,
            available_margin=available_margin,
            existing_positions=existing_positions,
            account_balance=account_balance,
        )

    except Exception as exc:
        logger.warning("[BROKER_PROVIDER] failed for %s: %s", symbol, exc)
        return _unavailable_broker()


# ═══════════════════════════════════════════════════════════════
# UNAVAILABLE CONTEXTS
# ═══════════════════════════════════════════════════════════════

def _unavailable_account() -> AccountContext:
    return AccountContext()  # All zeros — available property = False


def _unavailable_broker() -> BrokerContext:
    return BrokerContext()  # All zeros/False — available property = False
