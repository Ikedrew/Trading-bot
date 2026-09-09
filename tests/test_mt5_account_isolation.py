"""Phase-1 account configuration, process isolation, previews and compatibility."""

from dataclasses import asdict, replace
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest

from core.accounts.config import (
    ACCOUNT_IDS, BASELINE_TERMINAL, CANONICAL_SYMBOLS, AccountConfig,
    configuration_blocks, load_accounts,
)
from core.accounts.eligibility import evaluate
from core.accounts.identity import CanonicalExecutionRequest, account_trade_id, execution_targets
from core.accounts import manager, worker
from core.accounts.terminal import terminal_lease
from core.symbol_resolver import AccountSymbolResolver, broker_symbol_for, clear_resolved_symbols
from tests._account_fake_mt5 import FakeMT5


@pytest.fixture
def accounts(tmp_path):
    env = {}
    for i, account_id in enumerate(ACCOUNT_IDS, 1):
        prefix = f'MT5_{account_id}_'
        env.update({prefix + 'ENABLED': 'true', prefix + 'LOGIN': str(i),
                    prefix + 'SERVER': account_id + '-Demo',
                    prefix + 'TERMINAL_PATH': str(tmp_path / account_id / 'terminal64.exe'),
                    prefix + 'PASSWORD': 'secret-only-in-environment'})
    return load_accounts(env)


@pytest.fixture
def request_record():
    return CanonicalExecutionRequest('opp-parent', 'cor-parent', 'decision-parent',
                                     'EURUSD', 'BUY', .01, 1.10002, 1.09, 1.12)


def test_three_independent_configurations_and_no_password_values(accounts):
    assert tuple(a.account_id for a in accounts) == ACCOUNT_IDS
    assert len({a.identity for a in accounts}) == 3
    assert len({a.terminal_path for a in accounts}) == 3
    assert len({a.password_env for a in accounts}) == 3
    assert all(not a.errors() for a in accounts)
    assert 'secret-only-in-environment' not in repr(accounts)
    assert 'secret-only-in-environment' not in json.dumps([asdict(a) for a in accounts])


def test_default_keeps_metaquotes_baseline_and_extras_disabled():
    from core import config
    baseline, admirals, vantage = load_accounts({})
    assert baseline.account_id == 'METAQUOTES' and baseline.enabled
    assert baseline.role == 'baseline' and baseline.terminal_path == config.MT5_TERMINAL_PATH == BASELINE_TERMINAL
    assert not admirals.enabled and not vantage.enabled
    assert tuple(config.CANONICAL_SYMBOLS) == CANONICAL_SYMBOLS


def test_bad_config_does_not_block_other_accounts(accounts):
    bad = replace(accounts[1], login=None, configuration_errors=('INVALID_LOGIN',))
    blocks = configuration_blocks([accounts[0], bad, accounts[2]])
    assert blocks['ADMIRALS'] == ['INVALID_LOGIN', 'LOGIN_REQUIRED']
    assert blocks['METAQUOTES'] == blocks['VANTAGE'] == []
    parsed = load_accounts({'MT5_ADMIRALS_LOGIN': 'invalid', 'MT5_ADMIRALS_SYMBOL_MAP': 'secret-invalid-json'})
    assert 'INVALID_LOGIN' in parsed[1].errors()
    assert 'INVALID_SYMBOL_MAP' in parsed[1].errors()
    assert 'secret-invalid-json' not in repr(parsed)


def test_shared_terminal_is_rejected_and_baseline_reserved_even_disabled(accounts):
    a, b, c = accounts
    bad = replace(b, terminal_path=a.terminal_path)
    blocks = configuration_blocks([replace(a, enabled=False), bad, c])
    assert blocks[a.account_id] == blocks[c.account_id] == []
    assert blocks[b.account_id] == ['BASELINE_TERMINAL_RESERVED']
    blocks = configuration_blocks([a, b, replace(c, terminal_path=b.terminal_path)])
    assert blocks[b.account_id] == blocks[c.account_id] == ['SHARED_TERMINAL_PATH']


def test_no_additional_execution_role_can_be_enabled(accounts):
    assert 'ROLE_NOT_ALLOWED_IN_PHASE1' in replace(accounts[1], role='execute').errors()


def test_account_symbols_do_not_touch_legacy_global_mapping(accounts):
    clear_resolved_symbols()
    a = AccountSymbolResolver(accounts[0].identity, ['EURUSD'])
    b = AccountSymbolResolver(accounts[1].identity, ['EURUSD.a'])
    c = AccountSymbolResolver(accounts[2].identity, ['EURUSD.v'])
    assert [r.resolve('EURUSD')['broker_symbol'] for r in (a, b, c)] == ['EURUSD', 'EURUSD.a', 'EURUSD.v']
    assert broker_symbol_for('EURUSD') == 'EURUSD'
    changed = b.resolve('EURUSD')
    changed['broker_symbol'] = 'wrong'
    assert b.resolve('EURUSD')['broker_symbol'] == 'EURUSD.a'


def test_ambiguity_missing_symbols_and_explicit_account_override(accounts):
    names = ['NAS100.i', 'NAS100ft.i', 'XAUUSD', 'XAUUSD.crp', 'USTEC', 'USTECH100M']
    strict = AccountSymbolResolver(accounts[1].identity, names,
                                  aliases={'NAS100': ('USTEC', 'USTECH100M')})
    assert strict.resolve('NAS100')['status'] == 'ambiguous'
    assert strict.resolve('XAUUSD')['status'] == 'ambiguous'
    assert strict.resolve('US500')['status'] == 'unavailable'
    explicit = AccountSymbolResolver(accounts[1].identity, names, explicit={'NAS100': 'NAS100.i'})
    assert explicit.resolve('NAS100')['broker_symbol'] == 'NAS100.i'
    wrong = AccountSymbolResolver(accounts[2].identity, names, explicit={'NAS100': 'missing'})
    assert wrong.resolve('NAS100')['status'] == 'unavailable'  # No fallback/guess.


def test_balances_positions_orders_deals_specs_and_exposure_stay_account_owned(accounts):
    results = [worker.AccountReader(a, FakeMT5(a, suffix=f'.{i}')).snapshot()
               for i, a in enumerate(accounts)]
    assert [r['balance'] for r in results] == [10000, 20000, 30000]
    assert [r['currency'] for r in results] == ['GBP', 'EUR', 'USD']
    for r, a in zip(results, accounts):
        assert len(r['symbols']) == 10 and r['identity_verified']
        assert r['exposure']['position_count'] == 1
        for kind in ('positions', 'orders', 'deals'):
            assert r[kind][0]['account_id'] == a.account_id
            assert r[kind][0]['login'] == a.login
    for kind in ('positions', 'orders', 'deals'):
        assert len({r[kind][0]['scoped_id'] for r in results}) == 3


@pytest.mark.parametrize('change,reason', [
    ('login', 'ACCOUNT_IDENTITY_MISMATCH'), ('server', 'ACCOUNT_IDENTITY_MISMATCH'),
    ('real', 'NOT_A_DEMO_ACCOUNT'), ('path', 'TERMINAL_PATH_MISMATCH'),
    ('disconnected', 'TERMINAL_DISCONNECTED'),
])
def test_wrong_account_or_terminal_is_never_labelled_as_target(accounts, change, reason):
    fake = FakeMT5(accounts[0])
    if change == 'login': fake.account.login += 1
    if change == 'server': fake.account.server = 'other'
    if change == 'real': fake.account.trade_mode = 2
    if change == 'path': fake.terminal.path = 'other-terminal'
    if change == 'disconnected': fake.terminal.connected = False
    with pytest.raises(worker.AccountReadError, match=reason):
        worker.AccountReader(accounts[0], fake).snapshot()


def test_mid_read_account_switch_discards_data(accounts, monkeypatch):
    fake = FakeMT5(accounts[0])
    def switched(name):
        fake.account.login = accounts[1].login
        return fake.specs[name]
    monkeypatch.setattr(fake, 'symbol_info', switched)
    with pytest.raises(worker.AccountReadError, match='ACCOUNT_IDENTITY_MISMATCH'):
        worker.AccountReader(accounts[0], fake).snapshot()


def test_attach_only_worker_uses_no_login_no_fallback_and_no_trades(accounts, monkeypatch):
    a = accounts[0]
    fake = FakeMT5(a)
    monkeypatch.setitem(sys.modules, 'MetaTrader5', fake)
    monkeypatch.setattr(worker, 'running_terminals', lambda: [a.terminal_path])
    result = worker.run_worker(a)
    assert result['connected']
    assert fake.calls == [('initialize', (a.terminal_path,), {'timeout': 5000}), ('shutdown',)]
    fake.calls.clear()
    monkeypatch.setattr(fake, 'initialize', lambda *args, **kwargs: False)
    assert worker.run_worker(a)['reasons'] == ['INITIALIZE_FAILED']
    assert fake.calls == [('shutdown',)]


def test_worker_does_not_launch_a_stopped_terminal(accounts, monkeypatch):
    monkeypatch.setattr(worker, 'running_terminals', lambda: [])
    result = worker.run_worker(accounts[0])
    assert result['reasons'] == ['TERMINAL_NOT_RUNNING_OR_NOT_VISIBLE']


def test_worker_lease_excludes_duplicate_owners(accounts):
    with terminal_lease(accounts[0].terminal_path):
        with pytest.raises(RuntimeError, match='ACCOUNT_WORKER_BUSY'):
            with terminal_lease(accounts[0].terminal_path):
                pytest.fail('duplicate owner')


def test_one_canonical_request_many_targets_and_account_unique_ids(accounts, request_record):
    targets = execution_targets(request_record, accounts)
    assert len(targets) == 3
    assert all(t.request is request_record and not t.execution_enabled for t in targets)
    assert len({t.execution_id for t in targets}) == 3
    assert len({account_trade_id(a, 42) for a in accounts}) == 3
    assert targets == execution_targets(request_record, accounts)  # Stable idempotency.
    assert account_trade_id(accounts[0], 42) != account_trade_id(replace(accounts[0], login=12345), 42)


def test_real_subprocesses_produce_isolated_account_snapshots(accounts, request_record, monkeypatch):
    monkeypatch.setattr(manager, 'WORKER_MODULE', 'tests._account_fake_mt5')
    reports = manager.diagnose(accounts, request_record)
    assert all(r['connected'] for r in reports)
    assert len({r['worker_pid'] for r in reports}) == 3
    assert [r['balance'] for r in reports] == [10000, 20000, 30000]
    assert all(r['eligibility']['broker_eligible'] for r in reports)
    assert all(r['eligibility']['volume'] == .01 and not r['execution_enabled'] for r in reports)


def test_account_failure_is_local_in_real_subprocess_run(accounts, monkeypatch):
    monkeypatch.setattr(manager, 'WORKER_MODULE', 'tests._account_fake_mt5')
    reports = manager.diagnose([accounts[0], replace(accounts[1], server='simulate_failure'), accounts[2]])
    assert [r['connected'] for r in reports] == [True, False, True]
    assert reports[1]['reasons'] == ['INITIALIZE_FAILED']


def test_timeout_and_wrong_worker_response_fail_closed_without_secret_output(accounts, monkeypatch):
    secret = 'never-print-or-forward-this'
    monkeypatch.setenv('MT5_METAQUOTES_PASSWORD', secret)
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', secret)
    def timeout(*args, **kwargs):
        assert secret not in json.dumps([args, kwargs], default=str)
        assert kwargs['timeout'] == .1
        raise subprocess.TimeoutExpired(args[0], .1, output=secret, stderr=secret)
    monkeypatch.setattr(manager.subprocess, 'run', timeout)
    result = manager.run_isolated(accounts[0], timeout=.1)
    assert result['reasons'] == ['WORKER_TIMEOUT'] and secret not in json.dumps(result)
    wrong = worker.unavailable(accounts[1], [])
    monkeypatch.setattr(manager.subprocess, 'run', lambda *a, **kw: NS(returncode=0, stdout=json.dumps(wrong)))
    assert manager.run_isolated(accounts[0])['reasons'] == ['WORKER_RESPONSE_IDENTITY_MISMATCH']


@pytest.mark.parametrize('field,value,reason', [
    ('volume', .001, 'VOLUME_BELOW_MIN'), ('volume', 1000, 'VOLUME_ABOVE_MAX'),
    ('volume', .015, 'INVALID_VOLUME_STEP'), ('sl', 1.09999, 'SL_TOO_CLOSE'),
    ('sl', 1.2, 'STOP_DIRECTION_INVALID'), ('entry_price', 1.100021, 'PRICE_NOT_NORMALIZED'),
    ('volume', float('nan'), 'INVALID_REQUEST_PRICES_OR_VOLUME'),
])
def test_account_broker_eligibility_constraints(accounts, request_record, field, value, reason):
    fake = FakeMT5(accounts[0])
    result = evaluate(replace(request_record, **{field: value}), fake.account,
                      fake.terminal, fake.specs['EURUSD'], fake.symbol_info_tick('EURUSD'), 10)
    assert not result['broker_eligible'] and reason in result['reasons']
    assert not result['execution_enabled']


def test_margin_permissions_freeze_bad_spec_and_unavailable_exposure(accounts, request_record):
    fake = FakeMT5(accounts[0])
    spec = fake.specs['EURUSD']
    tick = fake.symbol_info_tick('EURUSD')
    result = evaluate(request_record, fake.account, fake.terminal, spec, tick, 999999)
    assert result['reasons'] == ['INSUFFICIENT_FREE_MARGIN']
    result = evaluate(request_record, fake.account, fake.terminal, spec, tick, None)
    assert result['reasons'] == ['MARGIN_UNAVAILABLE']
    fake.account.trade_expert = False
    assert 'TRADING_NOT_ALLOWED' in evaluate(request_record, fake.account, fake.terminal, spec, tick, 10)['reasons']
    fake.account.trade_expert = True
    spec.trade_freeze_level = 2000
    assert 'STOP_OR_FREEZE_DISTANCE_INVALID' in evaluate(request_record, fake.account, fake.terminal, spec, tick, 10)['reasons']
    spec.trade_freeze_level = 0
    spec.volume_step = 0
    assert 'INVALID_SYMBOL_SPEC' in evaluate(request_record, fake.account, fake.terminal, spec, tick, 10)['reasons']
    spec.volume_step = .01
    fake.positions_get = lambda: None
    report = worker.AccountReader(accounts[0], fake).snapshot(request_record)
    assert report['exposure'] is None
    assert 'EXPOSURE_UNAVAILABLE' in report['eligibility']['reasons']


def test_config_only_never_invokes_a_worker(accounts, monkeypatch):
    monkeypatch.setattr(manager, 'run_isolated', lambda *a, **k: pytest.fail('must not connect'))
    assert all(r['reasons'] == ['CONFIGURED_NOT_PROBED'] for r in manager.diagnose(accounts, config_only=True))


def test_shared_data_directory_rejected_for_both_additional_accounts(accounts, monkeypatch):
    def same_data(a, *args, **kw):
        result = worker.AccountReader(a, FakeMT5(a)).snapshot()
        if a.account_id != 'METAQUOTES':
            result['terminal_data_path'] = str(Path(a.terminal_path).parents[1] / 'shared-data')
        return result
    monkeypatch.setattr(manager, 'run_isolated', same_data)
    results = manager.diagnose(accounts)
    assert results[0]['connected']
    assert all(r['reasons'] == ['SHARED_TERMINAL_DATA_PATH'] for r in results[1:])


def test_account_response_mismatch_never_exposes_other_account_state(accounts, monkeypatch):
    fake = FakeMT5(accounts[0])
    def swapped_positions():
        fake.account.login = accounts[1].login
        return [fake.position]
    fake.positions_get = swapped_positions
    monkeypatch.setitem(sys.modules, 'MetaTrader5', fake)
    monkeypatch.setattr(worker, 'running_terminals', lambda: [accounts[0].terminal_path])
    result = worker.run_worker(accounts[0])
    assert result['reasons'] == ['ACCOUNT_IDENTITY_MISMATCH']
    assert result['balance'] is None and 'positions' not in result


def test_duplicate_broker_account_and_same_installation_are_rejected(accounts):
    a, b, c = accounts
    duplicate = replace(b, server=a.server, login=a.login)
    assert configuration_blocks([a, duplicate, c])[b.account_id] == ['BASELINE_ACCOUNT_RESERVED']
    duplicate = replace(c, server=b.server, login=b.login)
    blocks = configuration_blocks([a, b, duplicate])
    assert blocks[b.account_id] == blocks[c.account_id] == ['DUPLICATE_BROKER_ACCOUNT']
    same_dir = replace(b, terminal_path=str(Path(a.terminal_path).parent / 'terminal.exe'))
    assert configuration_blocks([a, same_dir, c])[b.account_id] == ['BASELINE_TERMINAL_RESERVED']
    assert 'TERMINAL_PATH_MUST_BE_ABSOLUTE' in replace(a, terminal_path='terminal64.exe').errors()


def test_same_request_keeps_volume_and_independent_margin_eligibility(accounts, request_record):
    reports = []
    for a in accounts:
        fake = FakeMT5(a)
        if a.account_id == 'ADMIRALS':
            fake.account.margin_free = 5.
        reports.append(worker.AccountReader(a, fake).snapshot(request_record))
    assert [r['eligibility']['broker_eligible'] for r in reports] == [True, False, True]
    assert reports[1]['eligibility']['reasons'] == ['INSUFFICIENT_FREE_MARGIN']
    assert all(r['eligibility']['volume'] == .01 for r in reports)
    assert all(not r['execution_enabled'] for r in reports)


def test_cli_config_only_and_incomplete_symbol_matrix_exit_codes(accounts, monkeypatch, capsys):
    from core.accounts import __main__ as cli
    monkeypatch.setattr(cli, 'load_accounts', lambda: accounts)
    assert cli.main(['--config-only', '--json']) == 0
    reports = json.loads(capsys.readouterr().out)
    assert len(reports) == 3 and all(not r['connected'] for r in reports)
    reports = [worker.AccountReader(a, FakeMT5(a)).snapshot() for a in accounts]
    reports[1]['symbols'][0]['status'] = 'ambiguous'
    monkeypatch.setattr(cli, 'diagnose', lambda *a, **kw: reports)
    assert cli.main(['--json']) == 1
    assert len(json.loads(capsys.readouterr().out)) == 3
