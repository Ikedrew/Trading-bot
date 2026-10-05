import ctypes
import os
from unittest.mock import Mock

import pytest

from core.accounts import terminal

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows inventory')


def kernel(monkeypatch, rows):
    api = Mock()
    api.CreateToolhelp32Snapshot.return_value = 10
    remaining = iter(rows)

    def advance(handle, pointer):
        row = next(remaining, None)
        if row is None:
            return False
        pointer._obj.th32ProcessID, pointer._obj.szExeFile = row
        return True

    api.Process32FirstW.side_effect = advance
    api.Process32NextW.side_effect = advance
    api.OpenProcess.side_effect = lambda flags, inherit, pid: pid + 100

    def path(handle, flags, buffer, length):
        buffer.value = r'C:\Pinned\terminal64.exe'
        return True

    api.QueryFullProcessImageNameW.side_effect = path
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *args, **kwargs: api)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 18)
    return api


def test_native_inventory_preserves_duplicate_pids_paths_and_closes_handles(monkeypatch):
    api = kernel(monkeypatch, [(1, 'terminal64.exe'), (2, 'python.exe'),
                               (3, 'TERMINAL64.EXE')])
    monkeypatch.setattr(terminal.subprocess, 'run',
                        lambda *args, **kwargs: pytest.fail('PowerShell spawned'))
    result = terminal.running_terminal_processes()
    assert [item.pid for item in result] == [1, 3]
    assert result[0].path == result[1].path
    assert api.CloseHandle.call_count == 3
    assert api.CloseHandle.call_args.args == (10,)


def test_snapshot_failure_is_not_empty_inventory(monkeypatch):
    api = kernel(monkeypatch, [])
    api.CreateToolhelp32Snapshot.return_value = ctypes.c_void_p(-1).value
    with pytest.raises(terminal.TerminalInventoryError):
        terminal.running_terminal_processes()
    api.CloseHandle.assert_not_called()


def test_native_timeout_remains_inconclusive_and_closes_snapshot(monkeypatch):
    api = kernel(monkeypatch, [(1, 'terminal64.exe')])
    assert terminal.terminal_process_inventory(
        lambda: terminal.running_terminal_processes(timeout=0)) == (
            None, terminal.TERMINAL_INVENTORY_TIMEOUT)
    api.CloseHandle.assert_called_once_with(10)


def test_unreadable_process_path_matches_existing_visibility_semantics(monkeypatch):
    api = kernel(monkeypatch, [(1, 'terminal64.exe')])
    api.QueryFullProcessImageNameW.return_value = False
    api.QueryFullProcessImageNameW.side_effect = None
    assert terminal.running_terminal_processes() == []
    assert api.CloseHandle.call_count == 2


def test_enumeration_error_fails_closed_and_closes_snapshot(monkeypatch):
    api = kernel(monkeypatch, [])
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 5)
    with pytest.raises(terminal.TerminalInventoryError):
        terminal.running_terminal_processes()
    api.CloseHandle.assert_called_once_with(10)


def test_same_pid_rows_are_not_collapsed(monkeypatch):
    """Snapshot rows survive verbatim: a repeated PID is never deduplicated."""
    api = kernel(monkeypatch, [(7, 'terminal64.exe'), (7, 'terminal64.exe')])
    result = terminal.running_terminal_processes()
    assert [item.pid for item in result] == [7, 7]
    # snapshot + one readable handle per row
    assert api.CloseHandle.call_count == 3


def test_inaccessible_process_is_skipped_without_losing_others(monkeypatch):
    """A process we cannot open (missing/exited/denied) is skipped, not fatal."""
    api = kernel(monkeypatch, [(1, 'terminal64.exe'), (2, 'terminal64.exe')])
    api.OpenProcess.side_effect = lambda flags, inherit, pid: 0 if pid == 1 else pid + 100
    result = terminal.running_terminal_processes()
    assert [item.pid for item in result] == [2]
    # snapshot + the single handle that opened successfully
    assert api.CloseHandle.call_count == 2
    assert api.CloseHandle.call_args.args == (10,)


def test_executable_paths_are_resolved_per_process(monkeypatch):
    """Each terminal reports its own executable path (distinct installs kept)."""
    api = kernel(monkeypatch, [(11, 'terminal64.exe'), (12, 'terminal64.exe')])
    pinned = {111: r'C:\A\terminal64.exe', 112: r'C:\B\terminal64.exe'}

    def path(handle, flags, buffer, length):
        buffer.value = pinned[handle]
        return True

    api.QueryFullProcessImageNameW.side_effect = path
    result = terminal.running_terminal_processes()
    assert [(item.pid, item.path) for item in result] == [
        (11, r'C:\A\terminal64.exe'), (12, r'C:\B\terminal64.exe')]


def test_inventory_exposes_only_visibility_never_identity(monkeypatch):
    """The native inventory is a visibility signal only: it never carries or
    substitutes account identity, which stays owned by AccountReader.verify."""
    kernel(monkeypatch, [(1, 'terminal64.exe')])
    entries, state = terminal.terminal_process_inventory()
    assert state == terminal.TERMINAL_INVENTORY_OBSERVED
    assert entries == [terminal.TerminalProcess(1, r'C:\Pinned\terminal64.exe')]
    assert terminal.TerminalProcess._fields == ('pid', 'path')
