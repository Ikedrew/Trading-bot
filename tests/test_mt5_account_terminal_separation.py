"""Operational terminal pins and contract inventory, without real MT5 I/O.

Regression coverage for the non-portable-launch data-path defect: MT5 reports
an AppData ``<hash>`` data directory whose ``origin.txt`` names the
configured installation directory. That representation must be accepted only
for its own dedicated installation, while a foreign data directory, wrong
login, wrong server, or cross-account terminal attachment still fails closed.
"""

from dataclasses import replace
import sys

import pytest

from core.accounts.config import (
    AccountConfig,
    load_accounts,
    read_terminal_origin,
    terminal_key,
)
from core.accounts import worker
from tests._account_fake_mt5 import FakeMT5


def account(tmp_path):
    return AccountConfig('VANTAGE', 'Vantage', server='Vantage-Demo', login=123,
                         terminal_path=str(tmp_path / 'terminal64.exe'),
                         terminal_data_path=str(tmp_path), portable=True, enabled=True)


def test_portable_environment_pins_executable_and_data_directory(tmp_path):
    a = load_accounts({
        'MT5_VANTAGE_LOGIN': '123', 'MT5_VANTAGE_SERVER': 'Vantage-Demo',
        'MT5_VANTAGE_TERMINAL_PATH': str(tmp_path / 'terminal64.exe'),
        'MT5_VANTAGE_TERMINAL_DATA_PATH': str(tmp_path),
        'MT5_VANTAGE_PORTABLE': 'true', 'MT5_VANTAGE_ENABLED': 'true',
    })[2]
    assert not a.errors() and a.portable
    assert a.terminal_data_path == str(tmp_path)
    assert 'PORTABLE_DATA_PATH_MISMATCH' in replace(a, terminal_data_path=str(tmp_path / 'other')).errors()
    assert 'PORTABLE_DATA_PATH_MISMATCH' in replace(a, terminal_data_path='').errors()
    assert 'INVALID_PORTABLE' in load_accounts({'MT5_VANTAGE_PORTABLE': 'perhaps'})[2].errors()


def test_wrong_configured_data_path_fails_before_symbol_reads(tmp_path, monkeypatch):
    a = account(tmp_path)
    fake = FakeMT5(a)  # Fake's default data directory differs from pinned root.
    monkeypatch.setattr(fake, 'symbols_get', lambda: pytest.fail('must fail before symbols'))
    with pytest.raises(worker.AccountReadError, match='TERMINAL_DATA_PATH_MISMATCH'):
        worker.AccountReader(a, fake).snapshot()


def test_actual_nonportable_data_path_representation_is_accepted(tmp_path):
    """The real MT5 main-mode representation verifies (portable + non-portable)."""
    for portable in (True, False):
        install_dir = tmp_path / f'install-{portable}'
        data_dir = tmp_path / f'hash-{portable}'
        install_dir.mkdir()
        data_dir.mkdir()
        (data_dir / 'origin.txt').write_text(str(install_dir), encoding='utf-16')
        assert terminal_key(read_terminal_origin(str(data_dir))) == terminal_key(str(install_dir))
        a = replace(account(tmp_path), terminal_path=str(install_dir / 'terminal64.exe'),
                    terminal_data_path=str(install_dir), portable=portable)
        assert not a.errors()
        fake = FakeMT5(a)
        fake.terminal.path = str(install_dir)
        fake.terminal.data_path = str(data_dir)
        report = worker.AccountReader(a, fake).snapshot()
        assert report['connected'] and report['identity_verified']


def test_genuinely_different_data_directory_is_rejected(tmp_path):
    """A foreign AppData hash directory whose origin names another install fails."""
    install_dir = tmp_path / 'install'
    foreign_install = tmp_path / 'foreign-install'
    foreign_data = tmp_path / 'foreign-hash'
    install_dir.mkdir()
    foreign_install.mkdir()
    foreign_data.mkdir()
    (foreign_data / 'origin.txt').write_text(str(foreign_install), encoding='utf-16')
    a = replace(account(tmp_path), terminal_path=str(install_dir / 'terminal64.exe'),
                terminal_data_path=str(install_dir))
    fake = FakeMT5(a)
    fake.terminal.path = str(install_dir)
    fake.terminal.data_path = str(foreign_data)
    with pytest.raises(worker.AccountReadError, match='TERMINAL_DATA_PATH_MISMATCH'):
        worker.AccountReader(a, fake).snapshot()


def test_wrong_login_remains_rejected_with_origin_backed_data_path(tmp_path):
    install_dir = tmp_path / 'install'
    data_dir = tmp_path / 'hash'
    install_dir.mkdir()
    data_dir.mkdir()
    (data_dir / 'origin.txt').write_text(str(install_dir), encoding='utf-16')
    a = replace(account(tmp_path), terminal_path=str(install_dir / 'terminal64.exe'),
                terminal_data_path=str(install_dir))
    fake = FakeMT5(a)
    fake.terminal.path = str(install_dir)
    fake.terminal.data_path = str(data_dir)
    fake.account.login += 1
    with pytest.raises(worker.AccountReadError, match='ACCOUNT_IDENTITY_MISMATCH'):
        worker.AccountReader(a, fake).snapshot()


def test_wrong_server_remains_rejected_with_origin_backed_data_path(tmp_path):
    install_dir = tmp_path / 'install'
    data_dir = tmp_path / 'hash'
    install_dir.mkdir()
    data_dir.mkdir()
    (data_dir / 'origin.txt').write_text(str(install_dir), encoding='utf-16')
    a = replace(account(tmp_path), terminal_path=str(install_dir / 'terminal64.exe'),
                terminal_data_path=str(install_dir))
    fake = FakeMT5(a)
    fake.terminal.path = str(install_dir)
    fake.terminal.data_path = str(data_dir)
    fake.account.server = 'other-server'
    with pytest.raises(worker.AccountReadError, match='ACCOUNT_IDENTITY_MISMATCH'):
        worker.AccountReader(a, fake).snapshot()


def test_account_a_cannot_attach_to_account_b_terminal(tmp_path):
    """Cross-account attach fails even when the data dir origin matches B."""
    dir_a = tmp_path / 'a'
    dir_b = tmp_path / 'b'
    hash_b = tmp_path / 'hash-b'
    dir_a.mkdir()
    dir_b.mkdir()
    hash_b.mkdir()
    (hash_b / 'origin.txt').write_text(str(dir_b), encoding='utf-16')
    config_a = AccountConfig('METAQUOTES', 'MetaQuotes', server='MQ-Demo', login=111,
                             role='baseline',
                             terminal_path=str(dir_a / 'terminal64.exe'),
                             terminal_data_path=str(dir_a),
                             portable=False, enabled=True)
    fake = FakeMT5(config_a)
    # Terminal actually belongs to account B: executable dir, data dir, origin.
    fake.terminal.path = str(dir_b)
    fake.terminal.data_path = str(hash_b)
    fake.account.login = 111
    fake.account.server = 'MQ-Demo'
    with pytest.raises(worker.AccountReadError, match='TERMINAL_PATH_MISMATCH'):
        worker.AccountReader(config_a, fake).snapshot()
    # Same-terminal executable but account B's login/server must also fail.
    # Same-terminal executable claimed by A but actually logged into B's
    # login/server must fail on account identity (role stays baseline so the
    # config itself remains valid and the test isolates the identity guard).
    same_exe = replace(config_a, terminal_path=str(dir_b / 'terminal64.exe'),
                       terminal_data_path=str(dir_b))
    assert not same_exe.errors()
    fake_b = FakeMT5(config_a)
    fake_b.terminal.path = str(dir_b)
    fake_b.terminal.data_path = str(hash_b)
    fake_b.account.login = 222
    fake_b.account.server = 'AD-Demo'
    fake_b.terminal.path = str(dir_b)
    fake_b.terminal.data_path = str(hash_b)
    with pytest.raises(worker.AccountReadError, match='ACCOUNT_IDENTITY_MISMATCH'):
        worker.AccountReader(same_exe, fake_b).snapshot()


def test_portable_attach_passes_portable_flag_without_login(tmp_path, monkeypatch):
    a = account(tmp_path)
    fake = FakeMT5(a)
    fake.terminal.data_path = str(tmp_path)
    monkeypatch.setitem(sys.modules, 'MetaTrader5', fake)
    monkeypatch.setattr(worker, 'running_terminals', lambda: [a.terminal_path])
    result = worker.run_worker(a)
    assert result['connected'] and result['identity_verified']
    assert fake.calls == [('initialize', (a.terminal_path,), {'timeout': 5000, 'portable': True}), ('shutdown',)]


def test_contract_inventory_preserves_descriptions_without_guessing(tmp_path):
    a = replace(account(tmp_path), portable=False, terminal_data_path='')
    fake = FakeMT5(a)
    cash = fake.specs.pop('NAS100')
    cash.name = 'NAS100.i'
    cash.description = 'US Tech 100 Cash'
    cash.path = 'Indices/Cash'
    cash.currency_base = 'USD'
    cash.currency_profit = 'USD'
    cash.expiration_time = 0
    future = type(cash)(**vars(cash))
    future.name = 'NAS100ft.i'
    future.description = 'US Tech 100 Futures'
    future.path = 'Indices/Futures'
    fake.specs[cash.name] = cash
    fake.specs[future.name] = future
    report = worker.AccountReader(a, fake).snapshot(include_symbol_inventory=True)
    nasdaq = next(s for s in report['symbols'] if s['canonical_symbol'] == 'NAS100')
    assert nasdaq['status'] == 'ambiguous' and nasdaq['broker_symbol'] is None
    inventory = {s['broker_symbol']: s for s in report['symbol_inventory']}
    assert inventory['NAS100.i']['description'] == 'US Tech 100 Cash'
    assert inventory['NAS100ft.i']['path'] == 'Indices/Futures'
    assert inventory['NAS100.i']['currency_base'] == 'USD'
    explicit = replace(a, symbol_map=(('NAS100', 'NAS100.i'),))
    selected = worker.AccountReader(explicit, fake).snapshot()
    row = next(s for s in selected['symbols'] if s['canonical_symbol'] == 'NAS100')
    assert row['broker_symbol'] == 'NAS100.i' and row['description'] == 'US Tech 100 Cash'


def test_unavailable_accounts_still_report_all_ten_contract_fields(tmp_path):
    result = worker.unavailable(account(tmp_path), ['INITIALIZE_FAILED'])
    assert len(result['symbols']) == 10
    for symbol in result['symbols']:
        assert symbol['status'] == 'not_checked'
        for field in (*worker.SPEC_FIELDS, *worker.SYMBOL_DESCRIPTION_FIELDS):
            assert field in symbol and symbol[field] is None
