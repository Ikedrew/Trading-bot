"""Test-only MT5 double and subprocess entry point. No terminal/network access."""

from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

from core.accounts.config import AccountConfig


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(self, config, *, balance=None, suffix=''):
        self.config = config
        self.calls = []
        index = ('METAQUOTES', 'ADMIRALS', 'VANTAGE').index(config.account_id) + 1
        amount = index * 10000.0 if balance is None else balance
        self.account = NS(login=config.login, server=config.server, trade_mode=0,
                          balance=amount, equity=amount - 1, margin=1., margin_free=amount - 2,
                          margin_level=123., leverage=30 * index, currency=('GBP', 'EUR', 'USD')[index - 1],
                          trade_allowed=True, trade_expert=True)
        self.terminal = NS(path=str(Path(config.terminal_path).parent),
                           data_path=str(Path(config.terminal_path).parent / 'data'),
                           connected=True, trade_allowed=True, tradeapi_disabled=False)
        self.specs = {}
        for symbol in ('EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'USDCAD', 'AUDUSD', 'NZDUSD', 'NAS100', 'US500', 'XAUUSD'):
            name = symbol + suffix
            self.specs[name] = NS(name=name, digits=5, point=.00001,
                trade_contract_size=100000., volume_min=.01, volume_step=.01, volume_max=100.,
                trade_stops_level=10, trade_freeze_level=5, trade_tick_size=.00001,
                trade_mode=4, filling_mode=1)
        self.position = NS(ticket=42, symbol='EURUSD' + suffix, volume=.01, magic=123)

    def initialize(self, *args, **kw):
        self.calls.append(('initialize', args, kw))
        return True

    def shutdown(self):
        self.calls.append(('shutdown',))

    def account_info(self):
        return self.account

    def terminal_info(self):
        return self.terminal

    def symbols_get(self):
        return list(self.specs.values())

    def symbol_info(self, name):
        return self.specs.get(name)

    def symbol_info_tick(self, name):
        return NS(bid=1.1, ask=1.10002)

    def positions_get(self):
        return [self.position]

    def orders_get(self):
        return [self.position]

    def history_deals_get(self, *args):
        return [self.position]

    def order_calc_margin(self, *args):
        self.calls.append(('margin', args))
        return 10.

    def order_send(self, *args, **kw):
        raise AssertionError('Tests must never submit orders')

    def login(self, *args, **kw):
        raise AssertionError('Tests must never switch accounts')

    def symbol_select(self, *args, **kw):
        raise AssertionError('Read-only diagnostics must not change Market Watch')


if __name__ == '__main__':
    from core.accounts.identity import CanonicalExecutionRequest
    from core.accounts.worker import AccountReader, unavailable
    data = json.load(sys.stdin)
    config = AccountConfig(**data['account'])
    request = CanonicalExecutionRequest(**data['request']) if data.get('request') else None
    fake = FakeMT5(config)
    if config.server == 'simulate_failure':
        print(json.dumps(unavailable(config, ['INITIALIZE_FAILED'])))
    else:
        print(json.dumps(AccountReader(config, fake).snapshot(request)))
