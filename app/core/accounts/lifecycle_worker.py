"""Pinned lifecycle worker. Reuses A–F session verification and terminal lease."""
from dataclasses import asdict
import json
import sys

from core.position_ownership import PositionOwnership
from .config import AccountConfig, terminal_key
from .terminal import terminal_lease, running_terminals
from .worker import AccountReader, AccountReadError


def execute_lifecycle(account, request, mt5):
    reader = AccountReader(account, mt5)
    reader.verify()
    op, args = request['operation'], request.get('arguments', {})
    if op == 'verify':
        # Terminal-manager readiness probe: the fail-closed session checks
        # above (exe path, data path via portable/origin rules, login, server,
        # demo mode) are the entire operation. Nothing else runs here.
        return {'verified': True}
    if op == 'recover':
        from core.symbol_resolver import AccountSymbolResolver
        from core.config import SYMBOL_ALIASES
        inventory = reader.read('symbols_get')
        if inventory is None:
            raise AccountReadError('SYMBOL_INVENTORY_UNAVAILABLE')
        resolver = AccountSymbolResolver(account.identity, [s.name for s in inventory],
            explicit=dict(account.symbol_map), aliases=SYMBOL_ALIASES)
        if args.get('symbols') is not None:
            # Batched startup recovery: ONE verified MT5 session for the whole
            # symbol set. Per-symbol failures are explicit ('errors') and never
            # abort the remaining symbols in the same session; the parent keeps
            # adoption and fail-closed semantics. No broker-name branches.
            results, errors = {}, {}
            for canonical in args['symbols']:
                resolved = resolver.resolve(canonical)
                if resolved['status'] != 'available':
                    errors[canonical] = 'SYMBOL_UNAVAILABLE_OR_AMBIGUOUS'
                    continue
                rows = reader.read('positions_get', symbol=resolved['broker_symbol'])
                if rows is None:
                    errors[canonical] = 'POSITIONS_UNAVAILABLE'
                    continue
                results[canonical] = [
                    dict(_row(p), broker_symbol=resolved['broker_symbol']) for p in rows
                    if int(p.magic) == int(args['magic']) and p.symbol == resolved['broker_symbol']]
            return {'results': results, 'errors': errors}
        resolved = resolver.resolve(args['symbol'])
        if resolved['status'] != 'available':
            raise AccountReadError('SYMBOL_UNAVAILABLE_OR_AMBIGUOUS')
        rows = reader.read('positions_get', symbol=resolved['broker_symbol'])
        if rows is None:
            raise AccountReadError('POSITIONS_UNAVAILABLE')
        return [dict(_row(p), broker_symbol=resolved['broker_symbol']) for p in rows
                if int(p.magic) == int(args['magic']) and p.symbol == resolved['broker_symbol']]

    owner = PositionOwnership(**(request.get('ownership') or {}))
    if (owner.account_id, owner.broker, owner.broker_server) != (
            account.account_id, account.broker, account.server):
        raise AccountReadError('OWNERSHIP_ACCOUNT_MISMATCH')
    if owner.position_ticket <= 0 or not owner.broker_symbol:
        raise AccountReadError('INCOMPLETE_POSITION_OWNERSHIP')
    if op == 'history_deals_get':
        # MT5's position keyword selects all deals for this position; no merged
        # account history and no ticket/deal namespace fallback.
        rows = reader.read(op, position=owner.position_ticket)
        if rows is None:
            raise AccountReadError('HISTORY_UNAVAILABLE')
        return [_row(d) for d in rows if int(d.position_id) == owner.position_ticket]
    if op == 'orders_get':
        if not owner.order_ticket:
            raise AccountReadError('ORDER_TICKET_UNAVAILABLE')
        rows = reader.read(op, ticket=owner.order_ticket)
        if rows is None:
            raise AccountReadError('ORDERS_UNAVAILABLE')
        return [_row(d) for d in rows if int(d.ticket) == owner.order_ticket
                and d.symbol == owner.broker_symbol]
    if op not in ('positions_get', 'modify', 'close'):
        raise AccountReadError('LIFECYCLE_OPERATION_NOT_ALLOWED')
    rows = reader.read('positions_get', ticket=owner.position_ticket)
    if rows is None:
        raise AccountReadError('POSITIONS_UNAVAILABLE')
    if not rows:
        return [] if op == 'positions_get' else _result(False, 'POSITION_NOT_FOUND')
    if len(rows) != 1 or int(rows[0].ticket) != owner.position_ticket:
        raise AccountReadError('POSITION_IDENTITY_MISMATCH')
    pos = rows[0]
    if pos.symbol != owner.broker_symbol or int(pos.magic) != int(args['magic']):
        raise AccountReadError('POSITION_OWNERSHIP_VIOLATION')
    if op == 'positions_get':
        return [_row(pos)]
    # These values are sent explicitly by the parent from its existing safety
    # switches. The worker has no credentials or production persistence config.
    if not args.get('allowed'):
        return _result(False, 'EXECUTION_DISABLED')
    account_info, terminal = reader.verify()
    if not account_info.trade_allowed or not account_info.trade_expert or not terminal.trade_allowed or terminal.tradeapi_disabled:
        return _result(False, 'BROKER_TRADING_DISABLED')
    from core.mt5_symbol_spec import MT5SymbolSpec, validate_stops, validate_volume
    info = reader.read('symbol_info', owner.broker_symbol)
    tick = reader.read('symbol_info_tick', owner.broker_symbol)
    if info is None or tick is None:
        return _result(False, 'SYMBOL_OR_TICK_UNAVAILABLE')
    spec = MT5SymbolSpec.from_info(owner.broker_symbol, info)
    if op == 'modify':
        error = validate_stops(spec, market_price=(float(tick.bid) + float(tick.ask)) / 2,
            sl=args['sl'], tp=args['tp'], include_freeze=True)
        if error:
            return _result(False, error)
        order = {'action': mt5.TRADE_ACTION_SLTP, 'symbol': owner.broker_symbol,
                 'position': owner.position_ticket,
                 'sl': spec.normalize_price(args['sl']) if args['sl'] else 0.,
                 'tp': spec.normalize_price(args['tp']) if args['tp'] else 0.}
    else:
        volume = float(pos.volume if args.get('volume') is None else args['volume'])
        error = validate_volume(spec, volume)
        if error or volume > float(pos.volume):
            return _result(False, error or 'CLOSE_VOLUME_EXCEEDS_POSITION')
        buy = int(pos.type) == mt5.ORDER_TYPE_BUY
        filling = int(getattr(info, 'filling_mode', 0))
        order = {'action': mt5.TRADE_ACTION_DEAL, 'symbol': owner.broker_symbol,
                 'position': owner.position_ticket, 'volume': volume,
                 'type': mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
                 'price': float(tick.bid if buy else tick.ask),
                 'deviation': args['deviation'], 'magic': args['magic'],
                 'comment': 'CLOSE_POSITION', 'type_time': mt5.ORDER_TIME_GTC,
                 'type_filling': mt5.ORDER_FILLING_IOC if filling & 2 else
                     (mt5.ORDER_FILLING_FOK if filling & 1 else
                      (mt5.ORDER_FILLING_RETURN if filling & 4 else mt5.ORDER_FILLING_IOC))}
    if args.get('dry_run'):
        return _result(True, 'dry_run_' + op)
    reader.verify()
    result = mt5.order_send(order)
    reader.verify()
    if result is None:
        return _result(False, 'ORDER_SEND_UNAVAILABLE')
    return dict(ok=int(result.retcode) == int(mt5.TRADE_RETCODE_DONE),
                retcode=int(result.retcode), deal=int(result.deal or 0),
                order=int(result.order or 0), comment=str(result.comment),
                fill_price=float(result.price or 0))


def _row(value):
    return dict(value._asdict()) if hasattr(value, '_asdict') else vars(value).copy()


def _result(ok, comment):
    return dict(ok=ok, retcode=0 if ok else -1, deal=0, order=0, comment=comment)


def run_lifecycle_worker(account, request, *, mt5=None):
    response = dict(account_id=account.account_id, broker=account.broker,
                    server=account.server, login=account.login, identity_verified=False)
    try:
        if not account.enabled or account.errors():
            raise AccountReadError('INVALID_ACCOUNT_CONFIGURATION')
        if terminal_key(account.terminal_path) not in {terminal_key(p) for p in running_terminals()}:
            raise AccountReadError('TERMINAL_NOT_RUNNING')
        with terminal_lease(account.terminal_path):
            if mt5 is None:
                import MetaTrader5 as mt5
            try:
                options = {'timeout': 5000}
                if account.portable:
                    options['portable'] = True
                if not mt5.initialize(account.terminal_path, **options):
                    raise AccountReadError('INITIALIZE_FAILED')
                response['value'] = execute_lifecycle(account, request, mt5)
                response['identity_verified'] = True
            finally:
                mt5.shutdown()
    except Exception as exc:
        response['error'] = str(exc) if isinstance(exc, AccountReadError) else type(exc).__name__
    return response


if __name__ == '__main__':
    request = json.load(sys.stdin)
    print(json.dumps(run_lifecycle_worker(AccountConfig(**request['account']), request), allow_nan=False))
