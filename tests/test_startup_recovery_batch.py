"""Batched startup recovery regression.

Proves the startup-recovery spawn reduction:
    ONE lifecycle worker subprocess per enabled account for the complete
    canonical symbol set (instead of one worker per account × symbol), with:

    1. all three enabled accounts participate in startup recovery
    2. multiple supported symbols use ONE transport/worker invocation per account
    3. three accounts use at most three lifecycle worker invocations total
    4. unsupported symbols create zero recovery operations inside a batch
    5. invalid capability remains fail-closed (no worker op, no promotion)
    6. a slow/failing account does not block healthy accounts
    7. a failed account's positions are never adopted
    8. wrong worker identity is still rejected (WORKER_RESPONSE_IDENTITY_MISMATCH)
    9. account-aware ownership remains (account_id, position_ticket), not ticket alone
   10. recovery results map positions back to the correct canonical symbol
   11. the startup path continues when one account recovery fails

Fake transports only: no MT5, S3, network or live trade.
"""

from types import SimpleNamespace as NS

import pytest

from core.accounts.config import AccountConfig
from core.accounts.lifecycle import LifecycleRouter
from core.accounts.worker import AccountReadError
from core.runtime.startup_recovery import (
    recover_positions_batch,
    recover_positions_on_startup,
)

MAGIC = 713001

UNIVERSE = ['EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'USDCAD',
            'AUDUSD', 'NZDUSD', 'NAS100', 'US500', 'XAUUSD']


def _accounts(tmp_path, **overrides):
    specs = (
        ('METAQUOTES', 'MetaQuotes', 'MetaQuotes-Demo', 111, 'baseline'),
        ('ADMIRALS', 'Admirals', 'Admirals-Demo', 222, 'observe_only'),
        ('VANTAGE', 'Vantage', 'Vantage-Demo', 333, 'baseline'),
    )
    out = []
    for account_id, broker, server, login, role in specs:
        kwargs = dict(account_id=account_id, broker=broker, server=server,
                      login=login,
                      terminal_path=str(tmp_path / account_id / 'terminal64.exe'),
                      enabled=True, role=role)
        kwargs.update(overrides.get(account_id, {}))
        out.append(AccountConfig(**kwargs))
    return tuple(out)


class _Recorder:
    """TradeStateManager-compatible double: validates and records positions."""

    def __init__(self, router):
        self.router = router
        self.positions = []
        self._by_key = set()

    def get_position(self, *, account_id, position_ticket):
        self.router.account(account_id)
        return (account_id, int(position_ticket)) in self._by_key

    def add_position(self, pos):
        self.router.validate(pos.ownership)
        key = (pos.ownership.account_id, int(pos.ownership.position_ticket))
        if key in self._by_key:
            return
        self._by_key.add(key)
        self.positions.append(pos)


def _row(ticket=12345, symbol='EURUSD', broker_symbol='EURUSD', magic=MAGIC,
         type_=0, volume=0.1, price_open=1.10000, price_current=1.10050,
         sl=1.09, tp=1.12, time=1717400000):
    return dict(ticket=ticket, symbol=symbol, magic=magic, type=type_,
                volume=volume, price_open=price_open, price_current=price_current,
                sl=sl, tp=tp, time=time, broker_symbol=broker_symbol)


def _batch_response(account, results=None, errors=None):
    return dict(account_id=account.account_id, broker=account.broker,
                server=account.server, login=account.login,
                identity_verified=True,
                value={'results': results or {}, 'errors': errors or {}})


def _make_transport(responses=None):
    """Return (transport, calls). ``responses`` may be a callable or a dict."""
    calls = []

    def transport(account, request):
        args = request.get('arguments', {})
        calls.append((account.account_id, request['operation'],
                      args.get('symbols'), args.get('symbol')))
        if callable(responses):
            return responses(account, request)
        return _batch_response(
            account, results={s: [] for s in (args.get('symbols') or ())})

    return transport, calls
def test_all_three_enabled_accounts_participate(tmp_path):
    accounts = _accounts(tmp_path)
    transport, calls = _make_transport()
    router = LifecycleRouter(accounts, transport=transport)
    rec = _Recorder(router)
    recover_positions_batch(magic=MAGIC, trade_managers={'EURUSD': rec},
                            lifecycle_router=router)
    assert {c[0] for c in calls} == {'METAQUOTES', 'ADMIRALS', 'VANTAGE'}
    assert all(c[1] == 'recover' for c in calls)


def test_multiple_supported_symbols_use_one_worker_invocation_per_account(tmp_path):
    accounts = _accounts(tmp_path)
    transport, calls = _make_transport()
    router = LifecycleRouter(accounts, transport=transport)
    tms = {s: _Recorder(router) for s in ('EURUSD', 'GBPUSD', 'USDJPY')}
    recover_positions_batch(magic=MAGIC, trade_managers=tms, lifecycle_router=router)
    assert len(calls) == 3
    for account_id, op, symbols, _ in calls:
        assert op == 'recover'
        assert set(symbols) == {'EURUSD', 'GBPUSD', 'USDJPY'}


def test_three_accounts_use_at_most_three_worker_invocations(tmp_path):
    accounts = _accounts(tmp_path)
    transport, calls = _make_transport()
    router = LifecycleRouter(accounts, transport=transport)
    tms = {s: _Recorder(router) for s in UNIVERSE}
    recover_positions_batch(magic=MAGIC, trade_managers=tms, lifecycle_router=router)
    # Not 10 symbols x 3 accounts = 30; exactly one per account.
    assert len(calls) == 3
    assert [c[2] for c in calls] == [tuple(UNIVERSE)] * 3


def test_unsupported_symbols_create_zero_recovery_operations(tmp_path, caplog):
    import logging
    accounts = _accounts(
        tmp_path,
        ADMIRALS={'unsupported_symbols': ('NAS100', 'US500', 'XAUUSD')})
    transport, calls = _make_transport()
    router = LifecycleRouter(accounts, transport=transport)
    tms = {s: _Recorder(router) for s in ('EURUSD', 'NAS100', 'US500', 'XAUUSD')}
    with caplog.at_level(logging.INFO):
        recover_positions_batch(magic=MAGIC, trade_managers=tms,
                                lifecycle_router=router)
    by_id = {c[0]: set(c[2] or ()) for c in calls}
    assert by_id['ADMIRALS'] == {'EURUSD'}
    assert by_id['METAQUOTES'] == {'EURUSD', 'NAS100', 'US500', 'XAUUSD'}
    assert len(calls) == 3
    assert '[ACCOUNT_RECOVERY_SKIPPED] account=ADMIRALS symbol=NAS100' in caplog.text


def test_invalid_capability_remains_fail_closed(tmp_path, caplog):
    import logging
    # symbol_map + unsupported overlap for the SAME symbol -> INVALID.
    accounts = _accounts(
        tmp_path,
        ADMIRALS={'symbol_map': (('NAS100', 'NAS100.i'),),
                  'unsupported_symbols': ('NAS100',)})
    transport, calls = _make_transport()
    router = LifecycleRouter(accounts, transport=transport)
    rec = _Recorder(router)
    with caplog.at_level(logging.ERROR):
        recover_positions_batch(magic=MAGIC, trade_managers={'NAS100': rec},
                                lifecycle_router=router)
    by_id = {c[0]: set(c[2] or ()) for c in calls}
    assert 'NAS100' not in by_id.get('ADMIRALS', ())
    assert 'SYMBOL_UNAVAILABLE_OR_AMBIGUOUS' in caplog.text
    assert 'ACCOUNT_RECOVERY_SKIPPED' not in caplog.text
    assert rec.positions == []
def test_slow_or_failing_account_does_not_block_healthy_accounts(tmp_path, caplog):
    import logging
    accounts = _accounts(tmp_path)

    def responses(account, request):
        if account.account_id == 'VANTAGE':
            raise RuntimeError('worker timed out')
        return _batch_response(account, results={'EURUSD': [_row(ticket=1)]})

    transport, calls = _make_transport(responses)
    router = LifecycleRouter(accounts, transport=transport)
    rec = _Recorder(router)
    with caplog.at_level(logging.ERROR):
        count = recover_positions_batch(magic=MAGIC,
                                        trade_managers={'EURUSD': rec},
                                        lifecycle_router=router)
    assert count == 2
    assert {p.ownership.account_id for p in rec.positions} == {'METAQUOTES', 'ADMIRALS'}
    assert '[ACCOUNT_RECOVERY_FAILED] account=VANTAGE' in caplog.text
    assert 'error=worker timed out' in caplog.text


def test_failed_account_positions_are_not_adopted(tmp_path, caplog):
    import logging
    accounts = _accounts(tmp_path)

    def responses(account, request):
        if account.account_id == 'ADMIRALS':
            return _batch_response(account, errors={'EURUSD': 'POSITIONS_UNAVAILABLE'})
        return _batch_response(account, results={'EURUSD': [_row(ticket=9)]})

    transport, calls = _make_transport(responses)
    router = LifecycleRouter(accounts, transport=transport)
    rec = _Recorder(router)
    with caplog.at_level(logging.ERROR):
        count = recover_positions_batch(magic=MAGIC,
                                        trade_managers={'EURUSD': rec},
                                        lifecycle_router=router)
    assert count == 2
    assert {p.ownership.account_id for p in rec.positions} == {'METAQUOTES', 'VANTAGE'}
    assert '[ACCOUNT_RECOVERY_FAILED] account=ADMIRALS symbol=EURUSD error=POSITIONS_UNAVAILABLE' in caplog.text


def test_wrong_worker_identity_still_rejected(tmp_path, caplog):
    import logging
    accounts = _accounts(tmp_path)
    vantage = next(a for a in accounts if a.account_id == 'VANTAGE')

    def responses(account, request):
        if account.account_id == 'ADMIRALS':
            # Forged worker response: claims VANTAGE identity.
            return dict(account_id=vantage.account_id, broker=vantage.broker,
                        server=vantage.server, login=vantage.login,
                        identity_verified=True,
                        value={'results': {'EURUSD': [_row(ticket=7)]},
                               'errors': {}})
        return _batch_response(account, results={'EURUSD': [_row(ticket=7)]})

    transport, calls = _make_transport(responses)
    router = LifecycleRouter(accounts, transport=transport)
    rec = _Recorder(router)
    with caplog.at_level(logging.ERROR):
        count = recover_positions_batch(magic=MAGIC,
                                        trade_managers={'EURUSD': rec},
                                        lifecycle_router=router)
    assert count == 2
    assert {p.ownership.account_id for p in rec.positions} == {'METAQUOTES', 'VANTAGE'}
    assert 'WORKER_RESPONSE_IDENTITY_MISMATCH' in caplog.text


def test_ownership_is_account_and_ticket_scoped_not_ticket_alone(tmp_path):
    accounts = _accounts(tmp_path)

    def responses(account, request):
        return _batch_response(account, results={'EURUSD': [_row(ticket=12345)]})

    transport, calls = _make_transport(responses)
    router = LifecycleRouter(accounts, transport=transport)
    rec = _Recorder(router)
    count = recover_positions_batch(magic=MAGIC, trade_managers={'EURUSD': rec},
                                    lifecycle_router=router)
    assert count == 3
    assert len(rec.positions) == 3
    ids = {p.ownership.account_id: p.position_id for p in rec.positions}
    assert ids['METAQUOTES'] != ids['VANTAGE'] != ids['ADMIRALS']
    for p in rec.positions:
        assert p.ownership.position_ticket == 12345
        assert p.mt5_ticket == 12345
def test_results_map_positions_back_to_canonical_symbol(tmp_path):
    accounts = _accounts(tmp_path)

    def responses(account, request):
        symbols = list(request['arguments']['symbols'] or ())
        by_symbol = {
            s: [_row(ticket=account.login * 10 + i, symbol=s,
                     broker_symbol=account.broker[0] + s)]
            for i, s in enumerate(sorted(symbols))
        }
        return _batch_response(account, results=by_symbol)

    transport, calls = _make_transport(responses)
    router = LifecycleRouter(accounts, transport=transport)
    rec_eur = _Recorder(router)
    rec_gbp = _Recorder(router)
    recover_positions_batch(magic=MAGIC,
                            trade_managers={'EURUSD': rec_eur, 'GBPUSD': rec_gbp},
                            lifecycle_router=router)
    assert {p.symbol for p in rec_eur.positions} == {'EURUSD'}
    assert {p.symbol for p in rec_gbp.positions} == {'GBPUSD'}
    for p in rec_eur.positions:
        assert p.ownership.canonical_symbol == 'EURUSD'
    for p in rec_gbp.positions:
        assert p.ownership.canonical_symbol == 'GBPUSD'


def test_startup_continues_when_one_account_recovery_fails(tmp_path, caplog):
    import logging
    accounts = _accounts(tmp_path)

    def responses(account, request):
        if account.account_id == 'METAQUOTES':
            raise RuntimeError('METAQUOTES worker crashed')
        return _batch_response(account, results={'EURUSD': [_row(ticket=5)]})

    transport, calls = _make_transport(responses)
    router = LifecycleRouter(accounts, transport=transport)
    rec = _Recorder(router)
    with caplog.at_level(logging.ERROR):
        # No exception escapes: startup must still proceed to LIVE.
        count = recover_positions_batch(magic=MAGIC,
                                        trade_managers={'EURUSD': rec},
                                        lifecycle_router=router)
    assert count == 2
    assert {p.ownership.account_id for p in rec.positions} == {'ADMIRALS', 'VANTAGE'}
    assert '[ACCOUNT_RECOVERY_FAILED] account=METAQUOTES' in caplog.text


def test_legacy_single_symbol_wrapper_uses_batch(tmp_path):
    """recover_positions_on_startup still works and delegates to the batch path."""
    accounts = _accounts(tmp_path)
    transport, calls = _make_transport()
    router = LifecycleRouter(accounts, transport=transport)
    rec = _Recorder(router)
    recover_positions_on_startup(trade_manager=rec, symbol='EURUSD', magic=MAGIC,
                                 lifecycle_router=router)
    assert len(calls) == 3
    assert all(c[2] == ('EURUSD',) for c in calls)


def test_unsupported_account_spawns_no_worker(tmp_path, caplog):
    """An account with zero supported symbols spawns nothing."""
    import logging
    accounts = _accounts(
        tmp_path,
        ADMIRALS={'unsupported_symbols': ('EURUSD',)})
    transport, calls = _make_transport()
    router = LifecycleRouter(accounts, transport=transport)
    rec = _Recorder(router)
    with caplog.at_level(logging.INFO):
        recover_positions_batch(magic=MAGIC, trade_managers={'EURUSD': rec},
                                lifecycle_router=router)
    assert {c[0] for c in calls} == {'METAQUOTES', 'VANTAGE'}
    assert '[ACCOUNT_RECOVERY_SKIPPED] account=ADMIRALS symbol=EURUSD' in caplog.text