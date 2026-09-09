"""One short-lived read-only MT5 connection per process and pinned account.

No login, symbol selection, rates collection, order sends, persistence writers,
strategy imports, or account switching. Only JSON crosses the process boundary.
"""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sys

from .config import AccountConfig, CANONICAL_SYMBOLS, read_terminal_origin, terminal_key
from .eligibility import SPEC_FIELDS, evaluate, valid_spec
from .identity import CanonicalExecutionRequest, execution_targets, scoped_id
from .terminal import running_terminals, terminal_lease

ACCOUNT_FIELDS = ('balance', 'equity', 'margin', 'margin_free', 'margin_level',
                  'leverage', 'currency', 'trade_allowed', 'trade_expert')
SYMBOL_DESCRIPTION_FIELDS = ('description', 'path', 'currency_base', 'currency_profit',
                             'trade_calc_mode', 'start_time', 'expiration_time')


class AccountReadError(RuntimeError):
    pass


def unavailable(config, reasons):
    return {
        'account_id': config.account_id, 'broker': config.broker,
        'server': config.server, 'login': config.login, 'role': config.role,
        'enabled': config.enabled, 'connected': False, 'identity_verified': False,
        'execution_enabled': False, 'reasons': list(reasons),
        **{k: None for k in ACCOUNT_FIELDS},
        'symbols': [{
            'canonical_symbol': c, 'broker_symbol': None, 'status': 'not_checked',
            'candidates': [], **{k: None for k in (*SPEC_FIELDS, *SYMBOL_DESCRIPTION_FIELDS)}, 'contract_size': None,
        } for c in CANONICAL_SYMBOLS],
    }


class AccountReader:
    """Only instantiated inside a dedicated worker (fake MT5 may be injected)."""

    def __init__(self, config, mt5):
        self.config = config
        self._mt5 = mt5
        self._data_path = None

    def verify(self):
        account = self._mt5.account_info()
        if account is None:
            raise AccountReadError('ACCOUNT_INFO_UNAVAILABLE')
        if (account.login, account.server) != (self.config.login, self.config.server):
            raise AccountReadError('ACCOUNT_IDENTITY_MISMATCH')
        if getattr(account, 'trade_mode', None) != self._mt5.ACCOUNT_TRADE_MODE_DEMO:
            raise AccountReadError('NOT_A_DEMO_ACCOUNT')
        terminal = self._mt5.terminal_info()
        if terminal is None or not terminal.connected:
            raise AccountReadError('TERMINAL_DISCONNECTED')
        expected = terminal_key(str(Path(self.config.terminal_path).parent))
        if terminal_key(terminal.path) != expected:
            raise AccountReadError('TERMINAL_PATH_MISMATCH')
        actual_data_path = getattr(terminal, 'data_path', '')
        data_path = terminal_key(actual_data_path)
        if self.config.terminal_data_path:
            expected_data_path = terminal_key(self.config.terminal_data_path)
            if data_path == expected_data_path:
                # True portable mode: writable data directory == installation dir.
                pass
            elif (data_path
                    and terminal_key(read_terminal_origin(actual_data_path)) == expected_data_path
                    and expected_data_path == terminal_key(str(Path(self.config.terminal_path).parent))):
                # Main (non-portable) launch of a dedicated installation:
                # MT5 reports an AppData hash directory whose origin.txt
                # names the configured installation directory. The expected
                # data path must still equal the configured installation
                # directory (which itself must equal the executable's
                # directory, already checked above via TERMINAL_PATH), so a
                # wrong terminal/data directory cannot pass via a foreign
                # origin. This branch applies regardless of the configured
                # portable flag so the already-running dedicated terminals
                # verify without a restart; true portable mode still takes
                # the exact-match branch above.
                pass
            else:
                raise AccountReadError('TERMINAL_DATA_PATH_MISMATCH')
        if not data_path or (self._data_path is not None and data_path != self._data_path):
            raise AccountReadError('TERMINAL_DATA_PATH_MISMATCH')
        self._data_path = data_path
        return account, terminal

    def read(self, method, *args, **kwargs):
        # Explicit allowlist prevents arbitrary IPC commands reaching MT5.
        if method not in ('symbols_get', 'symbol_info', 'symbol_info_tick',
                          'positions_get', 'orders_get', 'history_deals_get', 'order_calc_margin'):
            raise AccountReadError('READ_OPERATION_NOT_ALLOWED')
        self.verify()
        value = getattr(self._mt5, method)(*args, **kwargs)
        self.verify()
        return value

    def snapshot(self, request=None, *, include_symbol_inventory=False):
        from core.symbol_resolver import AccountSymbolResolver
        from core.config import SYMBOL_ALIASES

        account, terminal = self.verify()
        symbols = self.read('symbols_get')
        if symbols is None:
            raise AccountReadError('SYMBOL_INVENTORY_UNAVAILABLE')
        resolver = AccountSymbolResolver(
            self.config.identity, [s.name for s in symbols],
            explicit=dict(self.config.symbol_map), aliases=SYMBOL_ALIASES,
        )
        result = unavailable(self.config, [])
        result.update(connected=True, identity_verified=True, worker_pid=os.getpid(),
                      captured_at_utc=datetime.now(timezone.utc).isoformat(),
                      terminal_path=terminal.path, terminal_data_path=terminal.data_path,
                      terminal_trade_allowed=bool(terminal.trade_allowed),
                      terminal_tradeapi_disabled=bool(getattr(terminal, 'tradeapi_disabled', True)),
                      **{k: getattr(account, k, None) for k in ACCOUNT_FIELDS})
        infos = {}
        result['symbols'] = []
        if include_symbol_inventory:
            result['symbol_inventory'] = [
                {'broker_symbol': s.name, **{k: getattr(s, k, None)
                 for k in (*SPEC_FIELDS, *SYMBOL_DESCRIPTION_FIELDS)}} for s in symbols
            ]
        for canonical in CANONICAL_SYMBOLS:
            row = resolver.resolve(canonical)
            row.update({k: None for k in SPEC_FIELDS})
            row.update({k: None for k in SYMBOL_DESCRIPTION_FIELDS})
            row['contract_size'] = None
            if row['status'] == 'available':
                info = self.read('symbol_info', row['broker_symbol'])
                if info is None:
                    row['status'] = 'unavailable'
                else:
                    row.update({k: getattr(info, k, None) for k in SPEC_FIELDS})
                    row.update({k: getattr(info, k, None) for k in SYMBOL_DESCRIPTION_FIELDS})
                    row['contract_size'] = getattr(info, 'trade_contract_size', None)
                    if info.name != row['broker_symbol'] or not valid_spec(info):
                        row['status'] = 'invalid_spec'
                    else:
                        infos[canonical] = info
            result['symbols'].append(row)

        # Account-owned snapshots; no legacy risk, ownership or ticket caches.
        now = datetime.now(timezone.utc)
        for label, method, args in (
            ('positions', 'positions_get', ()), ('orders', 'orders_get', ()),
            ('deals', 'history_deals_get', (now - timedelta(days=1), now)),
        ):
            rows = self.read(method, *args)
            if rows is None:
                result[label] = None  # Unknown must never mean zero exposure.
                result['reasons'].append(label.upper() + '_UNAVAILABLE')
                continue
            result[label] = [self._owned_record(row, label) for row in rows]
        result['deals_window_start_utc'] = (now - timedelta(days=1)).isoformat()
        result['deals_window_end_utc'] = now.isoformat()
        result['exposure'] = None if result['positions'] is None else {
            'position_count': len(result['positions']),
            'gross_volume_by_broker_symbol': self._exposure(result['positions']),
        }
        if request is not None:
            target = execution_targets(request, [self.config])
            result['execution_target'] = asdict(target[0]) if target else None
            info = infos.get(request.symbol)
            if info is None:
                result['eligibility'] = {'broker_eligible': False,
                    'reasons': ['SYMBOL_UNAVAILABLE_OR_AMBIGUOUS'], 'execution_enabled': False}
            else:
                tick = self.read('symbol_info_tick', info.name)
                price = getattr(tick, 'ask' if request.side == 'BUY' else 'bid', None)
                margin = None
                if (isinstance(price, (int, float)) and math.isfinite(price) and price > 0
                        and request.side in ('BUY', 'SELL') and isinstance(request.volume, (int, float))
                        and math.isfinite(request.volume) and request.volume > 0):
                    order_type = self._mt5.ORDER_TYPE_BUY if request.side == 'BUY' else self._mt5.ORDER_TYPE_SELL
                    margin = self.read('order_calc_margin', order_type, info.name, request.volume, price)
                current_account, current_terminal = self.verify()
                result['eligibility'] = evaluate(request, current_account, current_terminal, info, tick, margin)
                if result['positions'] is None or result['orders'] is None:
                    result['eligibility']['broker_eligible'] = False
                    result['eligibility']['reasons'].append('EXPOSURE_UNAVAILABLE')
        self.verify()  # A mid-snapshot identity change discards the whole result.
        return result

    def _owned_record(self, row, kind):
        fields = ('ticket', 'position_id', 'identifier', 'order', 'symbol', 'type',
                  'volume', 'volume_current', 'volume_initial', 'price_open', 'price',
                  'sl', 'tp', 'profit', 'magic', 'time', 'time_msc')
        return {**{k: getattr(row, k) for k in fields if hasattr(row, k)},
                'account_id': self.config.account_id, 'broker': self.config.broker,
                'broker_server': self.config.server, 'login': self.config.login,
                'scoped_id': scoped_id(self.config, kind, int(row.ticket))}

    @staticmethod
    def _exposure(positions):
        totals = {}
        for p in positions:
            totals[p['symbol']] = totals.get(p['symbol'], 0.0) + float(p['volume'])
        return totals


def run_worker(config, request=None, *, include_symbol_inventory=False):
    if not config.enabled or config.errors():
        return unavailable(config, config.errors() or ['DISABLED'])
    try:
        paths = running_terminals()
        if terminal_key(config.terminal_path) not in {terminal_key(p) for p in paths}:
            return unavailable(config, ['TERMINAL_NOT_RUNNING_OR_NOT_VISIBLE'])
        with terminal_lease(config.terminal_path):
            import MetaTrader5 as mt5
            try:
                # Attach ONLY. No explicit login/password/server and no fallback
                # to another terminal. Expected identity is checked before reads.
                options = {'timeout': 5000}
                if config.portable:
                    options['portable'] = True
                if not mt5.initialize(config.terminal_path, **options):
                    return unavailable(config, ['INITIALIZE_FAILED'])
                reader = AccountReader(config, mt5)
                reader.verify()
                with terminal_lease(reader._data_path):
                    return reader.snapshot(request, include_symbol_inventory=include_symbol_inventory)
            finally:
                mt5.shutdown()  # Disconnect this worker's IPC; never stop terminal.
    except AccountReadError as exc:
        return unavailable(config, [str(exc)])
    except Exception as exc:
        # Never echo exception text: MT5/platform messages may contain secrets.
        code = str(exc) if str(exc) in ('ACCOUNT_WORKER_BUSY', 'TERMINAL_INVENTORY_UNAVAILABLE') else 'WORKER_READ_FAILED'
        return unavailable(config, [code])


def main():
    payload = json.load(sys.stdin)
    config = AccountConfig(**payload['account'])
    request = CanonicalExecutionRequest(**payload['request']) if payload.get('request') else None
    print(json.dumps(run_worker(config, request,
                               include_symbol_inventory=bool(payload.get('include_symbol_inventory', False))),
                     allow_nan=False))


if __name__ == '__main__':
    main()
