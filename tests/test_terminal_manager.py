"""Terminal manager regression tests — fakes only.

No real MT5 terminal is ever launched, attached to, or terminated here:
process enumeration, the launcher, the verifier and the window controller
are all injected fakes. Windows API code is exercised only through the
fake controller boundary.

Covers the required manager contract:
  0 exact processes  -> exactly one launch
  1 exact process    -> reuse, zero launches, manager_launched=False
  2+ exact processes -> fail closed (never choose/kill/adopt)
  wrong identity     -> fail closed (login/server/path/data path)
  hide/show          -> PID-scoped, never restarts the process
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from core.accounts.config import AccountConfig, load_accounts
from core.accounts.terminal import TerminalProcess
from core.accounts.terminal_manager import (
    TerminalManager,
    TerminalStatus,
    WindowsWindowController,
    _default_launcher,
    _default_verifier,
)

MAGIC = 713001

SPECS = (
    ('METAQUOTES', 'MetaQuotes', 'MetaQuotes-Demo', 111, 'baseline'),
    ('ADMIRALS', 'Admirals', 'AdmiralsGroup-Demo', 222, 'observe_only'),
    ('VANTAGE', 'Vantage', 'VantageGlobalPrimeLLP-Demo', 333, 'baseline'),
)


def _accounts(tmp_path, **overrides):
    """Three portable, provisioned-style account configs (real tmp files)."""
    out = []
    for account_id, broker, server, login, role in SPECS:
        data = tmp_path / account_id
        (data / 'config').mkdir(parents=True, exist_ok=True)
        (data / 'config' / 'bootstrap.ini').write_text('[Common]\n', encoding='utf-8')
        exe = data / 'terminal64.exe'
        exe.write_bytes(b'MZ-fake-terminal')
        kwargs = dict(
            account_id=account_id, broker=broker, server=server, login=login,
            terminal_path=str(exe), terminal_data_path=str(data),
            portable=True, enabled=True, role=role,
        )
        kwargs.update(overrides.get(account_id, {}))
        out.append(AccountConfig(**kwargs))
    return tuple(out)


class FakeProcessTable:
    """Mutable stand-in for running_terminal_processes."""

    def __init__(self, processes=()):
        self.processes = list(processes)

    def __call__(self):
        return list(self.processes)

    def add(self, pid, path):
        self.processes.append(TerminalProcess(pid, path))


class FakeLauncher:
    """Records launches; simulates the process appearing in the table."""

    def __init__(self, table, fail_for=(), start_pid=1000):
        self.table = table
        self.fail_for = set(fail_for)
        self.calls = []
        self._next = start_pid

    def __call__(self, account):
        self.calls.append(account.account_id)
        if account.account_id in self.fail_for:
            return None
        self._next += 1
        proc = TerminalProcess(self._next, account.terminal_path)
        self.table.add(proc.pid, proc.path)  # launched process becomes visible
        return proc


def _verifier(ok=True, reason=''):
    def verifier(account, pid=None):
        return ok, reason
    return verifier


class FakeWindowController:
    """PID-scoped hide/show recording without any Windows API."""

    def __init__(self, pids=()):
        self.hidden = set()
        self.hide_calls = []
        self.show_calls = []

    def hide(self, pid):
        self.hide_calls.append(pid)
        self.hidden.add(pid)
        return True

    def show(self, pid):
        self.show_calls.append(pid)
        self.hidden.discard(pid)
        return True

    def visible(self, pid):
        return pid not in self.hidden


def _manager(table, launcher, verifier, window=None):
    return TerminalManager(
        processes=table, launcher=launcher, verifier=verifier,
        window_controller=window if window is not None else FakeWindowController(),
        verify_attempts=1, verify_delay=0.0)
def test_absent_terminal_launches_exactly_one(tmp_path):
    accounts = _accounts(tmp_path)
    table, launcher = FakeProcessTable(), None
    launcher = FakeLauncher(table)
    manager = _manager(table, launcher, _verifier())
    st = manager.ensure(accounts[0])
    assert launcher.calls == ['METAQUOTES']
    assert st.running and st.verified and st.ok
    assert st.manager_launched is True
    assert st.pid == 1001
    assert st.visible is False  # hidden after verified readiness


def test_existing_exact_terminal_reused_without_launch(tmp_path):
    accounts = _accounts(tmp_path)
    exe = str(accounts[0].terminal_path)
    table = FakeProcessTable([TerminalProcess(2001, exe)])
    launcher = FakeLauncher(table)
    manager = _manager(table, launcher, _verifier())
    st = manager.ensure(accounts[0])
    assert launcher.calls == []  # never launched a second copy
    assert st.running and st.verified and st.ok
    assert st.pid == 2001
    assert st.manager_launched is False


def test_ensure_twice_never_duplicates_process(tmp_path):
    accounts = _accounts(tmp_path)
    table = FakeProcessTable()
    launcher = FakeLauncher(table)
    manager = _manager(table, launcher, _verifier())
    first = manager.ensure(accounts[0])
    second = manager.ensure(accounts[0])
    # The first ensure launches; the second sees the exact process and reuses.
    assert launcher.calls == ['METAQUOTES']
    assert first.pid == second.pid
    assert len(table.processes) == 1  # still exactly one real process
    assert second.manager_launched is True
    assert second.running and second.verified


def test_duplicate_exact_processes_fail_closed(tmp_path):
    accounts = _accounts(tmp_path)
    exe = str(accounts[0].terminal_path)
    table = FakeProcessTable([TerminalProcess(1, exe), TerminalProcess(2, exe)])
    launcher = FakeLauncher(table)
    manager = _manager(table, launcher, _verifier())
    st = manager.ensure(accounts[0])
    assert st.duplicate is True
    assert st.ok is False
    assert st.reason == 'DUPLICATE_TERMINAL_PROCESS'
    assert launcher.calls == []  # never launch into ambiguity


def test_no_process_is_killed_on_ambiguity(tmp_path):
    accounts = _accounts(tmp_path)
    exe = str(accounts[0].terminal_path)
    table = FakeProcessTable([TerminalProcess(1, exe), TerminalProcess(2, exe)])
    manager = _manager(table, FakeLauncher(table), _verifier())
    manager.ensure(accounts[0])
    assert len(table.processes) == 2  # both processes still alive
    assert not any(name for name in dir(manager)
                   if 'kill' in name.lower() or 'terminate' in name.lower())


def test_three_accounts_get_three_independent_processes(tmp_path):
    accounts = _accounts(tmp_path)
    table = FakeProcessTable()
    launcher = FakeLauncher(table)
    manager = _manager(table, launcher, _verifier())
    statuses = manager.ensure_all(accounts)
    assert set(statuses) == {'METAQUOTES', 'ADMIRALS', 'VANTAGE'}
    pids = {aid: st.pid for aid, st in statuses.items()}
    assert len(set(pids.values())) == 3
    assert all(st.ok for st in statuses.values())


def test_one_terminal_failure_does_not_block_other_accounts(tmp_path):
    accounts = _accounts(tmp_path)
    table = FakeProcessTable()
    launcher = FakeLauncher(table, fail_for={'ADMIRALS'})
    manager = _manager(table, launcher, _verifier())
    statuses = manager.ensure_all(accounts)
    assert statuses['ADMIRALS'].ok is False
    assert statuses['ADMIRALS'].reason == 'LAUNCH_FAILED'
    assert statuses['METAQUOTES'].ok and statuses['VANTAGE'].ok
    assert statuses['METAQUOTES'].pid != statuses['VANTAGE'].pid


def test_pre_existing_process_marked_not_manager_launched(tmp_path):
    accounts = _accounts(tmp_path)
    exe = str(accounts[2].terminal_path)
    table = FakeProcessTable([TerminalProcess(3003, exe)])
    manager = _manager(table, FakeLauncher(table), _verifier())
    st = manager.ensure(accounts[2])
    assert st.manager_launched is False


def test_manager_launched_process_marked_true(tmp_path):
    accounts = _accounts(tmp_path)
    table = FakeProcessTable()
    manager = _manager(table, FakeLauncher(table), _verifier())
    st = manager.ensure(accounts[1])
    assert st.manager_launched is True


def test_wrong_login_fails_closed(tmp_path):
    accounts = _accounts(tmp_path)
    table = FakeProcessTable([TerminalProcess(11, str(accounts[0].terminal_path))])
    manager = _manager(table, FakeLauncher(table),
                       _verifier(False, 'ACCOUNT_IDENTITY_MISMATCH'))
    st = manager.ensure(accounts[0])
    assert st.verified is False and st.ok is False
    assert st.reason == 'ACCOUNT_IDENTITY_MISMATCH'
    assert st.visible is None  # unverified terminal is never hidden/adopted


def test_wrong_server_fails_closed(tmp_path):
    accounts = _accounts(tmp_path)
    table = FakeProcessTable([TerminalProcess(12, str(accounts[0].terminal_path))])
    manager = _manager(table, FakeLauncher(table),
                       _verifier(False, 'ACCOUNT_IDENTITY_MISMATCH'))
    st = manager.ensure(accounts[0])
    assert st.ok is False and st.reason == 'ACCOUNT_IDENTITY_MISMATCH'


def test_wrong_terminal_path_fails_closed(tmp_path):
    accounts = _accounts(tmp_path)
    table = FakeProcessTable([TerminalProcess(13, str(accounts[0].terminal_path))])
    manager = _manager(table, FakeLauncher(table),
                       _verifier(False, 'TERMINAL_PATH_MISMATCH'))
    st = manager.ensure(accounts[0])
    assert st.ok is False and st.reason == 'TERMINAL_PATH_MISMATCH'


def test_wrong_data_path_fails_closed(tmp_path):
    accounts = _accounts(tmp_path)
    table = FakeProcessTable([TerminalProcess(14, str(accounts[0].terminal_path))])
    manager = _manager(table, FakeLauncher(table),
                       _verifier(False, 'TERMINAL_DATA_PATH_MISMATCH'))
    st = manager.ensure(accounts[0])
    assert st.ok is False and st.reason == 'TERMINAL_DATA_PATH_MISMATCH'
def test_portable_launch_arguments_match_provisioning_model(tmp_path):
    accounts = _accounts(tmp_path)
    captured = {}

    class FakePopen:
        def __init__(self, args, cwd=None, startupinfo=None):
            self.pid = 4242
            captured['args'] = list(args)
            captured['cwd'] = cwd

    with patch('core.accounts.terminal_manager.subprocess.Popen', FakePopen):
        proc = _default_launcher(accounts[0])
    data = accounts[0].terminal_data_path
    assert proc.pid == 4242
    assert captured['args'][0] == accounts[0].terminal_path
    assert '/portable' in captured['args']
    assert f'/config:{data}\\config\\bootstrap.ini' in captured['args']


def test_working_directory_is_account_data_dir(tmp_path):
    accounts = _accounts(tmp_path)
    captured = {}

    class FakePopen:
        def __init__(self, args, cwd=None, startupinfo=None):
            self.pid = 4243
            captured['cwd'] = cwd

    with patch('core.accounts.terminal_manager.subprocess.Popen', FakePopen):
        _default_launcher(accounts[1])
    assert captured['cwd'] == accounts[1].terminal_data_path


def test_non_portable_account_receives_no_portable_flag(tmp_path):
    accounts = _accounts(tmp_path, VANTAGE={'portable': False, 'terminal_data_path': ''})
    vantage = accounts[2]
    captured = {}

    class FakePopen:
        def __init__(self, args, cwd=None, startupinfo=None):
            self.pid = 4244
            captured['args'] = list(args)
            captured['cwd'] = cwd

    with patch('core.accounts.terminal_manager.subprocess.Popen', FakePopen):
        proc = _default_launcher(vantage)
    assert captured['args'] == [vantage.terminal_path]  # no /portable, no /config
    assert captured['cwd'] == str(Path(vantage.terminal_path).parent)


def test_launched_pid_belongs_to_configured_executable(tmp_path):
    accounts = _accounts(tmp_path)
    table = FakeProcessTable()
    manager = _manager(table, FakeLauncher(table), _verifier())
    st = manager.ensure(accounts[0])
    matches = manager._match(accounts[0])
    assert len(matches) == 1
    assert matches[0].pid == st.pid
    assert matches[0].path == accounts[0].terminal_path


def test_default_verifier_rejects_forged_worker_identity(tmp_path, monkeypatch):
    accounts = _accounts(tmp_path)
    vantage = accounts[2]

    def forged_isolated_lifecycle(account, request):
        return dict(account_id=vantage.account_id, broker=vantage.broker,
                    server=vantage.server, login=vantage.login,
                    identity_verified=True, value={'verified': True})

    monkeypatch.setattr('core.accounts.lifecycle.isolated_lifecycle',
                        forged_isolated_lifecycle)
    ok, reason = _default_verifier(accounts[0])
    assert ok is False
    assert reason == 'WORKER_RESPONSE_IDENTITY_MISMATCH'


def test_default_verifier_surfaces_worker_error_fail_closed(tmp_path, monkeypatch):
    accounts = _accounts(tmp_path)

    def failing_isolated_lifecycle(account, request):
        return dict(account_id=account.account_id, broker=account.broker,
                    server=account.server, login=account.login,
                    identity_verified=False, error='TERMINAL_DATA_PATH_MISMATCH')

    monkeypatch.setattr('core.accounts.lifecycle.isolated_lifecycle',
                        failing_isolated_lifecycle)
    ok, reason = _default_verifier(accounts[0])
    assert ok is False
    assert reason == 'TERMINAL_DATA_PATH_MISMATCH'


def test_default_verifier_accepts_verified_session(tmp_path, monkeypatch):
    accounts = _accounts(tmp_path)

    def ok_isolated_lifecycle(account, request):
        assert request['operation'] == 'verify'
        return dict(account_id=account.account_id, broker=account.broker,
                    server=account.server, login=account.login,
                    identity_verified=True, value={'verified': True})

    monkeypatch.setattr('core.accounts.lifecycle.isolated_lifecycle',
                        ok_isolated_lifecycle)
    assert _default_verifier(accounts[0]) == (True, '')
def test_hide_targets_only_that_terminal_pid(tmp_path):
    accounts = _accounts(tmp_path)
    table = FakeProcessTable()
    launcher = FakeLauncher(table)
    window = FakeWindowController()
    manager = _manager(table, launcher, _verifier(), window)
    manager.ensure_all(accounts)
    # ensure_all hides each managed terminal exactly once, by its own PID.
    assert sorted(window.hide_calls) == [1001, 1002, 1003]
    # Showing ADMIRALS touches only ADMIRALS' PID.
    manager.show(accounts[1])
    assert window.show_calls == [1002]
    assert 1002 not in window.hidden
    assert 1001 in window.hidden and 1003 in window.hidden


def test_show_restores_only_that_terminal(tmp_path):
    accounts = _accounts(tmp_path)
    table = FakeProcessTable()
    launcher = FakeLauncher(table)
    window = FakeWindowController()
    manager = _manager(table, launcher, _verifier(), window)
    manager.ensure_all(accounts)
    st = manager.show(accounts[2])
    assert st.visible is True
    assert manager.status(accounts[0]).visible is False
    assert manager.status(accounts[1]).visible is False


def test_hide_show_does_not_restart_process(tmp_path):
    accounts = _accounts(tmp_path)
    table = FakeProcessTable()
    launcher = FakeLauncher(table)
    window = FakeWindowController()
    manager = _manager(table, launcher, _verifier(), window)
    st = manager.ensure(accounts[0])
    pid = st.pid
    manager.show(accounts[0])
    manager.hide(accounts[0])
    assert launcher.calls == ['METAQUOTES']  # no relaunch, no restart
    assert manager.status(accounts[0]).pid == pid
    assert len(table.processes) == 1


def test_visibility_never_touches_foreign_terminal_windows(tmp_path):
    accounts = _accounts(tmp_path)
    exe = str(accounts[0].terminal_path)
    table = FakeProcessTable([TerminalProcess(900, exe)])
    window = FakeWindowController(pids={900})
    manager = _manager(table, FakeLauncher(table), _verifier(), window)
    manager.hide(accounts[0])
    manager.show(accounts[0])
    assert window.hide_calls == [900] and window.show_calls == [900]


def test_ensure_skips_disabled_or_invalid_accounts(tmp_path):
    accounts = _accounts(tmp_path)
    disabled = replace(accounts[0], enabled=False)
    table = FakeProcessTable()
    launcher = FakeLauncher(table)
    manager = _manager(table, launcher, _verifier())
    st = manager.ensure(disabled)
    assert launcher.calls == []
    assert st.ok is False
    assert st.reason.startswith('INVALID_ACCOUNT_CONFIGURATION')
def test_ensure_all_ensures_each_enabled_account_at_most_once(tmp_path, monkeypatch):
    accounts = _accounts(tmp_path)
    ensured = []

    class CountingManager:
        def ensure_all(self, accs):
            for a in accs:
                if a.enabled:
                    ensured.append(a.account_id)
            return {a.account_id: TerminalStatus(account_id=a.account_id)
                    for a in accs if a.enabled}

    import core.accounts.terminal_manager as tm_module
    import core.accounts.config as config_module
    monkeypatch.setattr(tm_module, 'TerminalManager', CountingManager)
    monkeypatch.setattr(config_module, 'load_accounts', lambda: accounts)
    import core.runtime.scanner_init as scanner_init
    import core.config as runtime_config
    # _ensure_enabled_account_terminals reads the real core.config module.
    monkeypatch.setattr(runtime_config, 'MT5_TERMINAL_MANAGER_ENABLED', True)
    statuses = scanner_init._ensure_enabled_account_terminals()
    assert ensured == ['METAQUOTES', 'ADMIRALS', 'VANTAGE']  # once per account
    assert set(statuses) == {'METAQUOTES', 'ADMIRALS', 'VANTAGE'}


def test_terminal_ensure_disabled_by_default(tmp_path, monkeypatch):
    import core.runtime.scanner_init as scanner_init
    import core.config as runtime_config
    monkeypatch.setattr(runtime_config, 'MT5_TERMINAL_MANAGER_ENABLED', False)
    assert scanner_init._ensure_enabled_account_terminals() == {}


def test_ensure_runs_before_batched_recovery_and_is_account_isolated(
        tmp_path, monkeypatch, caplog):
    import logging
    accounts = _accounts(tmp_path)
    order = []

    class OrderManager:
        def ensure_all(self, accs):
            order.append('ensure')
            return {'METAQUOTES': TerminalStatus(
                account_id='METAQUOTES', running=True, verified=False,
                reason='LAUNCH_FAILED')}

    import core.accounts.terminal_manager as tm_module
    import core.accounts.config as config_module
    monkeypatch.setattr(tm_module, 'TerminalManager', OrderManager)
    monkeypatch.setattr(config_module, 'load_accounts', lambda: accounts)
    import core.runtime.scanner_init as scanner_init
    import core.config as runtime_config
    monkeypatch.setattr(runtime_config, 'MT5_TERMINAL_MANAGER_ENABLED', True)
    monkeypatch.setattr(scanner_init.config, 'BOT_MAGIC', MAGIC, raising=False)
    monkeypatch.setattr(scanner_init.config, 'TRADE_MANAGEMENT_ENABLED',
                        True, raising=False)

    def fake_recover(**kwargs):
        order.append('recover')
        return 0

    import core.runtime.startup_recovery as recovery_module
    monkeypatch.setattr(recovery_module, 'recover_positions_batch', fake_recover)
    monkeypatch.setattr(scanner_init.MT5DataFeed, 'connect', lambda self: None)
    monkeypatch.setattr(scanner_init.MT5DataFeed, 'resolve_symbol',
                        lambda self: 'EURUSD')
    with caplog.at_level(logging.WARNING):
        states = scanner_init.initialize_symbol_states(
            symbols=['EURUSD'], execution=NS(lifecycle_router=None))
    assert [s.symbol for s in states] == ['EURUSD']
    # Terminal ensure happens ONCE, before batched recovery.
    assert order == ['ensure', 'recover']
    # A failed account terminal is logged but never blocks startup/recovery.
    assert 'METAQUOTES' in caplog.text