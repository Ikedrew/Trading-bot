"""Read-only broker preflight. Never sizes, changes or submits a decision."""

import math
from decimal import Decimal

from core.mt5_symbol_spec import MT5SymbolSpec, validate_stops, validate_volume
from .config import CANONICAL_SYMBOLS


SPEC_FIELDS = ('digits', 'point', 'trade_contract_size', 'volume_min', 'volume_step',
               'volume_max', 'trade_stops_level', 'trade_freeze_level',
               'trade_tick_size', 'trade_mode', 'filling_mode')


def valid_spec(info) -> bool:
    try:
        values = {k: float(getattr(info, k)) for k in SPEC_FIELDS}
        return (
            all(math.isfinite(v) for v in values.values())
            and 0 <= values['digits'] <= 15 and values['digits'].is_integer()
            and all(values[k] > 0 for k in ('point', 'trade_contract_size',
                                          'volume_min', 'volume_step', 'volume_max', 'trade_tick_size'))
            and values['volume_max'] >= values['volume_min']
            and all(values[k] >= 0 and values[k].is_integer()
                    for k in ('trade_stops_level', 'trade_freeze_level', 'trade_mode', 'filling_mode'))
        )
    except (AttributeError, TypeError, ValueError):
        return False


def evaluate(request, account, terminal, symbol_info, tick, margin_required):
    """Preliminary eligibility only; final broker acceptance is not guaranteed.

    Margin calculation is supplied by the owning worker, never a global MT5
    session. Freeze constraints are conservatively included for this preview.
    """
    reasons = []
    if request.symbol not in CANONICAL_SYMBOLS or request.side not in ('BUY', 'SELL'):
        reasons.append('INVALID_CANONICAL_REQUEST')
    if not all((request.canonical_opportunity_id, request.correlation_id, request.decision_id)):
        reasons.append('PARENT_LINEAGE_REQUIRED')
    numbers = (request.volume, request.entry_price, request.sl, request.tp)
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in numbers):
        reasons.append('INVALID_REQUEST_PRICES_OR_VOLUME')
    if not (getattr(account, 'trade_allowed', False) and getattr(account, 'trade_expert', False)
            and getattr(terminal, 'trade_allowed', False)
            and not getattr(terminal, 'tradeapi_disabled', True)):
        reasons.append('TRADING_NOT_ALLOWED')
    if not valid_spec(symbol_info):
        reasons.append('INVALID_SYMBOL_SPEC')
    if reasons:
        return _result(reasons)
    spec = MT5SymbolSpec.from_info(symbol_info.name, symbol_info)
    if spec.trade_mode not in (4, 1 if request.side == 'BUY' else 2):
        reasons.append('SYMBOL_TRADE_MODE_BLOCKED')
    volume_error = validate_volume(spec, request.volume)
    if volume_error:
        reasons.append(volume_error)
    step = Decimal(str(symbol_info.trade_tick_size))
    for value in (request.entry_price, request.sl, request.tp):
        if abs(spec.normalize_price(value) - value) > spec.point * 1e-6 or Decimal(str(value)) % step:
            reasons.append('PRICE_NOT_NORMALIZED')
            break
    bid, ask = getattr(tick, 'bid', None), getattr(tick, 'ask', None)
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in (bid, ask)) or ask < bid:
        reasons.append('TICK_UNAVAILABLE')
    else:
        market = bid if request.side == 'BUY' else ask
        order_price = ask if request.side == 'BUY' else bid
        if abs(spec.normalize_price(order_price) - order_price) > spec.point * 1e-6 or Decimal(str(order_price)) % step:
            reasons.append('MARKET_PRICE_NOT_NORMALIZED')
        if not ((request.sl < bid and request.tp > ask) if request.side == 'BUY'
                else (request.tp < bid and request.sl > ask)):
            reasons.append('STOP_DIRECTION_INVALID')
        stops_error = validate_stops(spec, market_price=market, sl=request.sl, tp=request.tp)
        if stops_error:
            reasons.append(stops_error)
        if validate_stops(spec, market_price=market, sl=request.sl, tp=request.tp, include_freeze=True):
            reasons.append('STOP_OR_FREEZE_DISTANCE_INVALID')
    free = getattr(account, 'margin_free', None)
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0
               for v in (free, margin_required)):
        reasons.append('MARGIN_UNAVAILABLE')
    elif margin_required > free:
        reasons.append('INSUFFICIENT_FREE_MARGIN')
    return _result(reasons, margin_required=margin_required, margin_free=free,
                   volume=request.volume)


def _result(reasons, **values):
    return {'broker_eligible': not reasons, 'reasons': reasons,
            'execution_enabled': False, 'preliminary_only': True, **values}
