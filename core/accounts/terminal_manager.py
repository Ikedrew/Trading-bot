"""Operational manager for one persistent MT5 terminal per enabled account.

Scope is terminal PROCESS management only: detect exact dedicated processes,
launch exactly one when absent, verify readiness/identity, and hide/show the
terminal window. The manager never terminates or relogins a terminal, never
touches a foreign process, and never weakens account identity or data-path
checks (AccountReader.verify semantics are reused through the pinned
lifecycle worker). No strategy, fanout, lifecycle ownership, research or
schema behaviour lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import subprocess
import time

from .config import terminal_key
from .terminal import TerminalProcess, running_terminal_processes

# ShowWindow commands (Windows); kept as plain constants so the module
# imports safely on every platform.
SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2


@dataclass(frozen=True)
class TerminalStatus:
    """In-memory terminal state for one account (never persisted to S3)."""

    account_id: str
    pid: int | None = None
    path: str = ''
    running: bool = False
    manager_launched: bool = False
    verified: bool = False
    reason: str = ''
    duplicate: bool = False
    visible: bool | None = None

    @property
    def ok(self) -> bool:
        return self.running and self.verified and not self.duplicate


class WindowsWindowController:
    """PID-scoped MT5 window hide/show via ctypes user32.

    Only top-level windows belonging to the exact managed terminal PID are
    touched; unrelated terminal64 windows are never enumerated or modified.
    Hiding/showing never restarts the process and never changes account
    identity or data-path validation.
    """

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._ctypes = ctypes
        self._user32 = ctypes.windll.user32
        self._WNDENUMPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def find(self, pid: int, *, visible_only: bool = False):
        windows = []

        def _cb(hwnd, _lparam):
            owner = wintypes.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == int(pid):
                windows.append(hwnd)
            return True

        self._user32.EnumWindows(self._WNDENUMPROC(_cb), 0)
        if visible_only:
            windows = [h for h in windows if self._user32.IsWindowVisible(h)]
        return windows[0] if windows else None

    def hide(self, pid: int) -> bool:
        hwnd = self.find(pid, visible_only=False)
        if not hwnd:
            return False
        return bool(self._user32.ShowWindow(int(hwnd), SW_HIDE))

    def show(self, pid: int) -> bool:
        hwnd = self.find(pid, visible_only=False)
        if not hwnd:
            return False
        self._user32.ShowWindow(int(hwnd), SW_SHOWNORMAL)
        return True

    def visible(self, pid: int):
        hwnd = self.find(pid, visible_only=False)
        if not hwnd:
            return None
        return bool(self._user32.IsWindowVisible(hwnd))


def _default_launcher(account) -> TerminalProcess | None:
    r"""Launch the account's dedicated terminal exactly once.

    Mirrors tools/prepare_mt5_account_terminals.ps1: the configured
    executable, ``/portable`` for portable accounts, ``/config:<data
    dir>\config\bootstrap.ini`` when present, working directory = the
    configured terminal/data directory. No broker-specific branches; any
    future Broker D works from its account config alone. Non-portable
    accounts never receive ``/portable``.
    """
    exe = str(account.terminal_path)
    if not exe or not Path(exe).is_file():
        return None
    if account.terminal_data_path:
        workdir = str(account.terminal_data_path)
    else:
        workdir = str(Path(exe).parent)
    args = [exe]
    if account.portable:
        args.append('/portable')
    if account.terminal_data_path:
        bootstrap = Path(account.terminal_data_path) / 'config' / 'bootstrap.ini'
        if bootstrap.is_file():
            args.append(f'/config:{bootstrap}')
    startupinfo = None
    if os.name == 'nt':
        # Start without a disruptive foreground window (same model as the
        # provisioning script's -WindowStyle Hidden). Console-creation flags
        # are irrelevant for the GUI binary; the window itself is hidden via
        # STARTUPINFO so no window flashes on the desktop/taskbar.
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = SW_HIDE
    proc = subprocess.Popen(args, cwd=workdir, startupinfo=startupinfo)
    return TerminalProcess(proc.pid, exe)


def _default_verifier(account, pid=None) -> tuple[bool, str]:
    """Bounded readiness probe through the pinned lifecycle worker.

    Runs the existing fail-closed checks (exe path, data path via
    portable/origin rules, login, server, demo mode) inside an isolated
    worker; the parent bot process never attaches to MT5 for verification.
    Wrong identity stays fail-closed.
    """
    from .lifecycle import isolated_lifecycle
    try:
        response = isolated_lifecycle(account, {'operation': 'verify', 'arguments': {}})
    except Exception as exc:
        return False, str(exc) or 'VERIFY_WORKER_FAILED'
    if (response.get('account_id'), response.get('broker'), response.get('server'),
            response.get('login')) != account.identity:
        return False, 'WORKER_RESPONSE_IDENTITY_MISMATCH'
    if response.get('error'):
        return False, response['error']
    if not response.get('identity_verified'):
        return False, 'WORKER_RESPONSE_IDENTITY_MISMATCH'
    return True, ''


class TerminalManager:
    """One persistent terminal per enabled account; duplicates fail closed.

    Detection matches processes by canonical executable path and never
    collapses duplicates into a path set. ``ensure`` decides:

        0 exact processes -> launch exactly one
        1 exact process   -> reuse and verify
        2+ exact processes-> FAIL CLOSED (never choose/kill/adopt)

    Only terminals launched by this manager instance are marked
    ``manager_launched``; pre-existing terminals are reused but never
    owned, restarted or terminated.
    """

    def __init__(self, *, processes=None, launcher=None, verifier=None,
                 window_controller=None, sleep=None,
                 verify_attempts=3, verify_delay=2.0):
        self._processes = processes or running_terminal_processes
        self._launcher = launcher or _default_launcher
        self._verifier = verifier or _default_verifier
        self._window = window_controller
        if self._window is None and os.name == 'nt':
            self._window = WindowsWindowController()
        self._sleep = sleep or time.sleep
        self._verify_attempts = max(1, int(verify_attempts))
        self._verify_delay = max(0.0, float(verify_delay))
        self._launched: dict[str, int] = {}
        self._statuses: dict[str, TerminalStatus] = {}

    def _match(self, account) -> list[TerminalProcess]:
        expected = terminal_key(account.terminal_path)
        return [p for p in self._processes()
                if terminal_key(p.path) == expected]

    def ensure(self, account, *, verify=True, hide=True) -> TerminalStatus:
        key = account.account_id
        errors = tuple(account.errors() or ())
        if not account.enabled or errors:
            return self._record(key, TerminalStatus(
                account_id=key, path=account.terminal_path,
                reason='INVALID_ACCOUNT_CONFIGURATION:'
                       + ','.join(errors or ('DISABLED',))))
        matches = self._match(account)
        if len(matches) > 1:
            # Fail closed on ambiguity: never choose, never kill, never adopt.
            return self._record(key, TerminalStatus(
                account_id=key, path=account.terminal_path, running=True,
                duplicate=True, reason='DUPLICATE_TERMINAL_PROCESS'))
        if matches:
            proc = matches[0]
            manager_launched = self._launched.get(key) == proc.pid
        else:
            proc = self._launch(account)
            if proc is None:
                return self._record(key, TerminalStatus(
                    account_id=key, path=account.terminal_path,
                    reason='LAUNCH_FAILED'))
            self._launched[key] = proc.pid
            manager_launched = True
        status = TerminalStatus(
            account_id=key, pid=proc.pid, path=proc.path, running=True,
            manager_launched=manager_launched)
        if verify:
            ok, reason = self._verify_ready(account)
            status = replace(status, verified=ok, reason=reason)
        if status.verified and self._window is not None and hide:
            # Keep the terminal out of desktop/taskbar clutter. Hiding is
            # PID-scoped, never restarts the process and never changes the
            # account identity or data-path state; show() restores it.
            self._window.hide(proc.pid)
            status = replace(status, visible=False)
        return self._record(key, status)

    def ensure_all(self, accounts, *, verify=True, hide=True) -> dict[str, TerminalStatus]:
        """Ensure every enabled account once; failures stay account-local."""
        statuses: dict[str, TerminalStatus] = {}
        for account in accounts:
            if not getattr(account, 'enabled', False):
                continue
            try:
                statuses[account.account_id] = self.ensure(
                    account, verify=verify, hide=hide)
            except Exception as exc:  # one account must never block the others
                statuses[account.account_id] = TerminalStatus(
                    account_id=account.account_id, path=account.terminal_path,
                    reason=f'ENSURE_ERROR:{exc}')
        return statuses

    def status(self, account) -> TerminalStatus:
        cached = self._statuses.get(account.account_id) or TerminalStatus(
            account_id=account.account_id, path=account.terminal_path)
        matches = self._match(account)
        if not matches:
            return replace(cached, running=False, reason='TERMINAL_NOT_RUNNING')
        if len(matches) > 1:
            return replace(cached, running=True, duplicate=True,
                           reason='DUPLICATE_TERMINAL_PROCESS')
        return replace(cached, running=True, pid=matches[0].pid,
                       path=matches[0].path,
                       manager_launched=self._launched.get(
                           account.account_id) == matches[0].pid)

    def show(self, account) -> TerminalStatus:
        return self._visibility(account, True)

    def hide(self, account) -> TerminalStatus:
        return self._visibility(account, False)

    def _visibility(self, account, visible) -> TerminalStatus:
        st = self.status(account)
        if st.duplicate or not st.running or st.pid is None:
            return replace(st, reason=st.reason or 'TERMINAL_NOT_RUNNING')
        if self._window is None:
            return replace(st, visible=None)
        if visible:
            self._window.show(st.pid)
        else:
            self._window.hide(st.pid)
        return replace(st, visible=visible)

    def _launch(self, account):
        try:
            return self._launcher(account)
        except Exception:
            return None

    def _verify_ready(self, account) -> tuple[bool, str]:
        last = 'VERIFY_UNATTEMPTED'
        for attempt in range(self._verify_attempts):
            try:
                ok, last = self._verifier(account)
            except Exception as exc:
                ok, last = False, f'VERIFY_ERROR:{exc}'
            if ok:
                return True, ''
            if attempt + 1 < self._verify_attempts:
                self._sleep(self._verify_delay)
        return False, last

    def _record(self, key, status) -> TerminalStatus:
        self._statuses[key] = status
        return status