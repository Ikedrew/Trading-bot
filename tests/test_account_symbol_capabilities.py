"""Broker-agnostic account symbol capabilities for startup recovery.

SUPPORTED   — proceed to the account worker (explicit map entry when
              present, else worker-side dynamic resolution decides live
              availability/ambiguity and fails closed on its own).
UNSUPPORTED — explicit configuration (unsupported_symbols): clean INFO skip,
              no worker call, not an error.
INVALID     — malformed/inconsistent config or request: fail closed with
              SYMBOL_UNAVAILABLE_OR_AMBIGUOUS; never silently UNSUPPORTED.

No broker-name branches: the same contract applies to any future Broker D/E.
Fake transports only: no MT5, S3, network, or live trade.
"""

from dataclasses import replace

from core.accounts.config import AccountConfig, load_accounts
from core.accounts.lifecycle import LifecycleRouter
from core.runtime.startup_recovery import recover_positions_on_startup
from types import SimpleNamespace as NS


MAGIC = 713001


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


def _ok(account, **kw):
    response = dict(account_id=account.account_id, broker=account.broker,
                    server=account.server, login=account.login,
                    identity_verified=True, value=[])
    response.update(kw)
    return response


def test_capability_contract_states():
    base = dict(account_id='FUTURE_D', broker='FutureD', server='s', login=1,
                terminal_path='C:/t/terminal64.exe', enabled=True)
    supported = AccountConfig(**base, symbol_map=(('NAS100', 'NAS100.i'),))
    assert supported.capability('NAS100') == 'SUPPORTED'
    # No explicit statement: worker-side dynamic resolution still decides.
    assert supported.capability('EURUSD') == 'SUPPORTED'
    unsupported = AccountConfig(**base, unsupported_symbols=('NAS100',))
    assert unsupported.capability('NAS100') == 'UNSUPPORTED'
    # Conflict / malformed / unknown never become a silent skip.
    assert AccountConfig(
        **base, symbol_map=(('NAS100', 'NAS100.i'),),
        unsupported_symbols=('NAS100',)).capability('NAS100') == 'INVALID'
    assert AccountConfig(
        **base, symbol_map=(('NAS100', ''),)).capability('NAS100') == 'INVALID'
    assert AccountConfig(
        **base, symbol_map=(('NAS100', 'a'), ('NAS100', 'b')),
    ).capability('NAS100') == 'INVALID'
    assert AccountConfig(**base).capability('NOT_A_SYMBOL') == 'INVALID'
    assert AccountConfig(**base).capability('') == 'INVALID'
    # A typo elsewhere is reported via errors() (and blocks the worker) but
    # does not poison an unrelated per-symbol query.
    typo = AccountConfig(**base, unsupported_symbols=('NOT_A_SYMBOL',))
    assert 'INVALID_UNSUPPORTED_SYMBOLS' in typo.errors()
    assert typo.capability('EURUSD') == 'SUPPORTED'


def test_load_accounts_parses_unsupported_and_rejects_conflict():
    env = {}
    for i, account_id in enumerate(('METAQUOTES', 'ADMIRALS', 'VANTAGE'), 1):
        prefix = f'MT5_{account_id}_'
        env.update({prefix + 'ENABLED': 'true', prefix + 'LOGIN': str(i),
                    prefix + 'SERVER': account_id + '-Demo',
                    prefix + 'TERMINAL_PATH': f'C:/t/{account_id}/terminal64.exe',
                    prefix + 'PASSWORD': 'x'})
    env['MT5_ADMIRALS_UNSUPPORTED_SYMBOLS'] = '["NAS100", "US500", "XAUUSD"]'
    admirals = load_accounts(env)[1]
    assert admirals.unsupported_symbols == ('NAS100', 'US500', 'XAUUSD')
    assert admirals.capability('NAS100') == 'UNSUPPORTED'
    assert admirals.capability('EURUSD') == 'SUPPORTED'
    assert not admirals.errors()
    env['MT5_ADMIRALS_SYMBOL_MAP'] = '{"NAS100": "NAS100.i"}'
    conflicted = load_accounts(env)[1]
    assert 'INVALID_SYMBOL_CAPABILITY_CONFLICT' in conflicted.errors()
    assert conflicted.capability('NAS100') == 'INVALID'
    bad = dict(env, MT5_ADMIRALS_SYMBOL_MAP='{}',
               MT5_ADMIRALS_UNSUPPORTED_SYMBOLS='["NOPE"]')
    parsed = load_accounts(bad)[1]
    assert 'INVALID_UNSUPPORTED_SYMBOLS' in parsed.errors()


def test_startup_recovery_supported_recovers_unsupported_skips_invalid_fails_closed(tmp_path, caplog):
    """Per-(account, symbol): SUPPORTED recovers, UNSUPPORTED skips, INVALID fails closed."""
    import logging
    from dataclasses import replace
    # METAQUOTES/VANTAGE: empty maps (unchanged behaviour) -> SUPPORTED.
    # ADMIRALS: NAS100/US500/XAUUSD unsupported by config only -> skip.
    accounts = _accounts(tmp_path)
    by_id = {a.account_id: a for a in accounts}
    admirals = replace(by_id['ADMIRALS'],
                       unsupported_symbols=('NAS100', 'US500', 'XAUUSD'))
    configured = (by_id['METAQUOTES'], by_id['VANTAGE'], admirals)
    assert configured[0].capability('NAS100') == 'SUPPORTED'
    assert configured[1].capability('NAS100') == 'SUPPORTED'
    assert admirals.capability('NAS100') == 'UNSUPPORTED'
    assert admirals.capability('US500') == 'UNSUPPORTED'
    assert admirals.capability('XAUUSD') == 'UNSUPPORTED'
    assert admirals.capability('EURUSD') == 'SUPPORTED'
    assert admirals.role == 'observe_only'

    called = []

    def transport(account, request):
        called.append(account.account_id)
        return _ok(account)

    router = LifecycleRouter(configured, transport=transport)
    stub = NS(get_position=lambda **kw: None, add_position=lambda p: None)
    with caplog.at_level(logging.INFO):
        assert recover_positions_on_startup(
            trade_manager=stub, symbol='NAS100', magic=MAGIC,
            lifecycle_router=router) == 0
    assert set(called) == {'METAQUOTES', 'VANTAGE'}  # ADMIRALS: no worker call.
    assert '[ACCOUNT_RECOVERY_SKIPPED] account=ADMIRALS symbol=NAS100' in caplog.text
    assert 'WORKER_RESPONSE_IDENTITY_MISMATCH' not in caplog.text

    # INVALID (conflicted config) fails closed: real error, no worker call.
    bad = replace(by_id['METAQUOTES'], symbol_map=(('NAS100', 'NAS100'),),
                  unsupported_symbols=('NAS100',))
    assert bad.capability('NAS100') == 'INVALID'
    called2 = []
    router2 = LifecycleRouter((bad,), transport=lambda a, r: (called2.append(a.account_id), _ok(a))[1])
    with caplog.at_level(logging.ERROR):
        assert recover_positions_on_startup(
            trade_manager=stub, symbol='NAS100', magic=MAGIC,
            lifecycle_router=router2) == 0
    assert called2 == []
    assert 'SYMBOL_UNAVAILABLE_OR_AMBIGUOUS' in caplog.text


def test_future_broker_uses_same_contract_without_name_branch(tmp_path):
    """Future Broker D/E: same capability contract, no hardcoded broker names."""
    base = dict(broker='FutureD', server='s', login=9,
                terminal_path='C:/t/terminal64.exe', enabled=True)
    d = AccountConfig(account_id='METAQUOTES', **base,
                      unsupported_symbols=('NAS100',))
    assert d.capability('NAS100') == 'UNSUPPORTED'
    assert d.capability('EURUSD') == 'SUPPORTED'

