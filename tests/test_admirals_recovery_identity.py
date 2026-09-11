"""ADMIRALS startup-recovery identity regression.

Observed runtime failure:
    [ACCOUNT_RECOVERY_FAILED] account=ADMIRALS error=WORKER_RESPONSE_IDENTITY_MISMATCH

Root cause under test: LifecycleRouter.call checked
``tuple != identity or not identity_verified`` in one condition, so any
worker-side operational failure (correct identity tuple echoed
pre-verification, identity_verified=False, error=<true cause>) was masked as
WORKER_RESPONSE_IDENTITY_MISMATCH.

Fake transports only: no MT5, S3, network or live trade.
"""

from types import SimpleNamespace as NS

import pytest

from core.accounts.config import AccountConfig
from core.accounts.lifecycle import LifecycleRouter
from core.accounts.worker import AccountReadError
from core.runtime.startup_recovery import recover_positions_on_startup

MAGIC = 713001


def _accounts(tmp_path):
    specs = (
        ('METAQUOTES', 'MetaQuotes', 'MetaQuotes-Demo', 111, 'baseline'),
        ('VANTAGE', 'Vantage', 'Vantage-Demo', 333, 'baseline'),
        ('ADMIRALS', 'Admirals', 'Admirals-Demo', 222, 'observe_only'),
    )
    return tuple(
        AccountConfig(
            account_id=a, broker=b, server=s, login=login,
            terminal_path=str(tmp_path / a / 'terminal64.exe'),
            enabled=True, role=role,
        )
        for a, b, s, login, role in specs
    )


def _identity_response(account, *, verified=True, error=None, value=None):
    response = dict(
        account_id=account.account_id, broker=account.broker,
        server=account.server, login=account.login,
        identity_verified=verified,
    )
    if error is not None:
        response['error'] = error
    if value is not None:
        response['value'] = value
    return response


def _by_id(accounts, account_id):
    return next(a for a in accounts if a.account_id == account_id)


def test_valid_admirals_recovery_accepted(tmp_path):
    """Valid ADMIRALS recover raises no MISMATCH; role stays observe_only."""
    accounts = _accounts(tmp_path)
    admirals = _by_id(accounts, 'ADMIRALS')
    seen = []

    def transport(account, request):
        seen.append((account.account_id, request['operation']))
        assert request['operation'] == 'recover'
        assert request['ownership'] is None
        return _identity_response(account, verified=True, value=[])

    router = LifecycleRouter(accounts, transport=transport)
    assert router.call('ADMIRALS', 'recover', symbol='EURUSD', magic=MAGIC) == []
    assert seen == [('ADMIRALS', 'recover')]
    assert admirals.role == 'observe_only'


def test_admirals_worker_error_surfaces_verbatim(tmp_path):
    """Correct-identity worker failure is NOT masked as MISMATCH."""
    accounts = _accounts(tmp_path)

    def transport(account, request):
        return _identity_response(
            account, verified=False,
            error='SYMBOL_UNAVAILABLE_OR_AMBIGUOUS', value=None)

    router = LifecycleRouter(accounts, transport=transport)
    with pytest.raises(AccountReadError) as excinfo:
        router.call('ADMIRALS', 'recover', symbol='EURUSD', magic=MAGIC)
    assert str(excinfo.value) == 'SYMBOL_UNAVAILABLE_OR_AMBIGUOUS'
    assert 'WORKER_RESPONSE_IDENTITY_MISMATCH' not in str(excinfo.value)


def test_wrong_worker_identity_still_rejected(tmp_path):
    """Genuinely wrong worker identity stays fail-closed as MISMATCH."""
    accounts = _accounts(tmp_path)
    vantage = _by_id(accounts, 'VANTAGE')

    def cross_wired(account, request):
        return _identity_response(vantage, verified=True, value=[])

    router = LifecycleRouter(accounts, transport=cross_wired)
    with pytest.raises(AccountReadError) as excinfo:
        router.call('ADMIRALS', 'recover', symbol='EURUSD', magic=MAGIC)
    assert str(excinfo.value) == 'WORKER_RESPONSE_IDENTITY_MISMATCH'

    def unverified_no_error(account, request):
        return _identity_response(account, verified=False, value=[])

    router2 = LifecycleRouter(accounts, transport=unverified_no_error)
    with pytest.raises(AccountReadError) as excinfo2:
        router2.call('ADMIRALS', 'recover', symbol='EURUSD', magic=MAGIC)
    assert str(excinfo2.value) == 'WORKER_RESPONSE_IDENTITY_MISMATCH'



def test_capability_contract_and_startup_skip(tmp_path, caplog):
    """Broker-agnostic: SUPPORTED recovers, UNSUPPORTED skips, INVALID fails closed."""
    import logging
    from dataclasses import replace
    base = _accounts(tmp_path)
    meta = replace(_by_id(base, 'METAQUOTES'),
                   symbol_map=(('EURUSD', 'EURUSD'), ('NAS100', 'NAS100'),
                               ('US500', 'US500'), ('XAUUSD', 'XAUUSD')))
    vant = replace(_by_id(base, 'VANTAGE'),
                   symbol_map=(('EURUSD', 'EURUSD.v'),))
    adm = replace(_by_id(base, 'ADMIRALS'),
                  symbol_map=(('EURUSD', 'EURUSD.adm'),),
                  unsupported_symbols=('NAS100', 'US500', 'XAUUSD'))
    assert meta.capability('NAS100') == 'SUPPORTED'
    assert adm.capability('NAS100') == 'UNSUPPORTED'
    assert adm.capability('EURUSD') == 'SUPPORTED'
    # No explicit statement: SUPPORTED by contract; worker-side dynamic
    # resolution still decides live availability/ambiguity and fails closed.
    assert vant.capability('NAS100') == 'SUPPORTED'
    assert not adm.errors() and not meta.errors()

    called = []

    def transport(account, request):
        called.append(account.account_id)
        return _identity_response(account, verified=True, value=[])

    router = LifecycleRouter((meta, vant, adm), transport=transport)
    with caplog.at_level(logging.INFO):
        count = recover_positions_on_startup(
            trade_manager=_stub_manager(), symbol='NAS100', magic=MAGIC,
            lifecycle_router=router)
    assert count == 0
    # ADMIRALS never touched a worker: clean skip, not an error.
    assert set(called) == {'METAQUOTES', 'VANTAGE'}
    assert 'ACCOUNT_RECOVERY_SKIPPED' in caplog.text
    assert 'account=ADMIRALS' in caplog.text
    # Fail-closed INVALID is covered by the dedicated capability tests
    # (conflict / malformed / unknown never silently skip).
    assert 'WORKER_RESPONSE_IDENTITY_MISMATCH' not in caplog.text


def test_capability_conflict_and_bad_names_fail_closed(tmp_path):
    """Overlap or unknown names can never silently become UNSUPPORTED."""
    import json
    from core.accounts.config import load_accounts
    base_env = {}
    for i, aid in enumerate(('METAQUOTES', 'VANTAGE', 'ADMIRALS'), 1):
        base_env.update({
            f'MT5_{aid}_ENABLED': 'true', f'MT5_{aid}_LOGIN': str(i),
            f'MT5_{aid}_SERVER': aid + '-Demo',
            f'MT5_{aid}_TERMINAL_PATH': str(tmp_path / aid / 'terminal64.exe'),
        })
    conflict = dict(base_env, MT5_ADMIRALS_SYMBOL_MAP=json.dumps({'NAS100': 'NAS100.x'}),
                    MT5_ADMIRALS_UNSUPPORTED_SYMBOLS=json.dumps(['NAS100']))
    assert 'INVALID_SYMBOL_CAPABILITY_CONFLICT' in load_accounts(conflict)[1].errors()
    bad = dict(base_env, MT5_ADMIRALS_UNSUPPORTED_SYMBOLS=json.dumps(['NOPE']))
    assert 'INVALID_UNSUPPORTED_SYMBOLS' in load_accounts(bad)[1].errors()




def _stub_manager():
    return NS(get_position=lambda **kwargs: None,
              add_position=lambda position: None)


def test_startup_recovery_logs_true_admirals_error(tmp_path, caplog):
    """Startup path logs true ADMIRALS cause, not MISMATCH."""
    import logging
    accounts = _accounts(tmp_path)

    def transport(account, request):
        if account.account_id == 'ADMIRALS':
            return _identity_response(
                account, verified=False,
                error='SYMBOL_UNAVAILABLE_OR_AMBIGUOUS')
        return _identity_response(account, verified=True, value=[])

    router = LifecycleRouter(accounts, transport=transport)
    with caplog.at_level(logging.ERROR):
        count = recover_positions_on_startup(
            trade_manager=_stub_manager(), symbol='EURUSD', magic=MAGIC,
            lifecycle_router=router)
    assert count == 0
    assert 'SYMBOL_UNAVAILABLE_OR_AMBIGUOUS' in caplog.text
    assert 'WORKER_RESPONSE_IDENTITY_MISMATCH' not in caplog.text


def test_startup_recovery_wrong_identity_still_mismatch(tmp_path, caplog):
    """Startup path still reports MISMATCH for forged worker identity."""
    import logging
    accounts = _accounts(tmp_path)
    vantage = _by_id(accounts, 'VANTAGE')

    def transport(account, request):
        if account.account_id == 'ADMIRALS':
            return _identity_response(vantage, verified=True, value=[])
        return _identity_response(account, verified=True, value=[])

    router = LifecycleRouter(accounts, transport=transport)
    with caplog.at_level(logging.ERROR):
        count = recover_positions_on_startup(
            trade_manager=_stub_manager(), symbol='EURUSD', magic=MAGIC,
            lifecycle_router=router)
    assert count == 0
    assert 'WORKER_RESPONSE_IDENTITY_MISMATCH' in caplog.text
