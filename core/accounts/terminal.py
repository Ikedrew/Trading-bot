"""Read-only Windows terminal inventory and cross-process diagnostic leases."""

from collections import namedtuple
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import time

from .config import terminal_key

# Process-inventory budget. The Windows process inventory is a PRE-FLIGHT
# convenience, never an authority: under live host load a fixed budget is
# routinely exceeded, so the budget is configurable and its timeout is
# classified as INCONCLUSIVE instead of "no terminal is running". The
# authoritative deciders remain mt5.initialize + AccountReader.verify (identity,
# terminal path and data path), which still fail closed.
INVENTORY_TIMEOUT_ENV = 'MT5_TERMINAL_INVENTORY_TIMEOUT_SECONDS'
DEFAULT_INVENTORY_TIMEOUT_SECONDS = 10.0
TERMINAL_INVENTORY_OBSERVED = 'TERMINAL_INVENTORY_OBSERVED'
TERMINAL_INVENTORY_TIMEOUT = 'TERMINAL_INVENTORY_TIMEOUT'
TERMINAL_INVENTORY_UNAVAILABLE = 'TERMINAL_INVENTORY_UNAVAILABLE'


class TerminalInventoryError(RuntimeError):
    """The terminal process inventory could not be read (callers fail closed)."""


class TerminalInventoryTimeout(TerminalInventoryError):
    """The inventory query exceeded its budget: INCONCLUSIVE, never "absent"."""


def terminal_inventory_timeout(env=None) -> float:
    """Configured inventory budget in seconds (env override, never hard-coded).

    A missing/invalid/non-positive override falls back to the default so the
    inventory can never run unbounded.
    """
    env = os.environ if env is None else env
    raw = str(env.get(INVENTORY_TIMEOUT_ENV, '') or '').strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_INVENTORY_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_INVENTORY_TIMEOUT_SECONDS


def hidden_process_flags() -> int:
    return getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0


# One entry PER running terminal process. Two instances of the same
# executable are distinct entries and must never be collapsed for
# terminal-manager duplicate decisions.
TerminalProcess = namedtuple('TerminalProcess', ('pid', 'path'))


def running_terminal_processes(*, timeout: float | None = None) -> list[TerminalProcess]:
    """Enumerate every running terminal64 process as (pid, executable path).

    ``timeout`` overrides the configured inventory budget for this call.

    Raises ``TerminalInventoryTimeout`` when the inventory query exceeds its
    budget (inconclusive: the terminal may well be running) and
    ``TerminalInventoryError`` when the inventory could not be read at all
    (non-zero exit / unparseable output — callers fail closed).
    """
    if os.name != 'nt':
        return []
    budget = terminal_inventory_timeout() if timeout is None else float(timeout)
    # The process inventory is local OS work. Starting PowerShell per pinned
    # worker adds seconds before the authoritative MT5 attach/identity checks.
    try:
        return _native_terminal_processes(budget)
    except TerminalInventoryError:
        raise
    except (OSError, ValueError, TypeError):
        raise TerminalInventoryError(TERMINAL_INVENTORY_UNAVAILABLE) from None


def _native_terminal_processes(budget: float) -> list[TerminalProcess]:
    """Read PIDs and executable paths directly, retaining duplicate terminals."""
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD),
                    ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD),
                    ('pcPriClassBase', wintypes.LONG), ('dwFlags', wintypes.DWORD),
                    ('szExeFile', wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ('Process32FirstW', 'Process32NextW'):
        function = getattr(kernel, name)
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        function.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    deadline = time.perf_counter() + budget

    def check_budget():
        if time.perf_counter() >= deadline:
            raise TerminalInventoryTimeout(TERMINAL_INVENTORY_TIMEOUT)

    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if snapshot == ctypes.c_void_p(-1).value or snapshot is None:
        raise TerminalInventoryError(TERMINAL_INVENTORY_UNAVAILABLE)
    processes = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            check_budget()
            if entry.szExeFile.casefold() == 'terminal64.exe':
                handle = kernel.OpenProcess(0x1000, False, entry.th32ProcessID)
                if handle:
                    try:
                        path = ctypes.create_unicode_buffer(32768)
                        length = wintypes.DWORD(len(path))
                        if kernel.QueryFullProcessImageNameW(
                                handle, 0, path, ctypes.byref(length)) and path.value:
                            processes.append(TerminalProcess(entry.th32ProcessID, path.value))
                    finally:
                        kernel.CloseHandle(handle)
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise TerminalInventoryError(TERMINAL_INVENTORY_UNAVAILABLE)
        check_budget()
        return processes
    finally:
        kernel.CloseHandle(snapshot)


def running_terminals(*, timeout: float | None = None) -> list[str]:
    """Executable paths of every running terminal64 process (may repeat)."""
    return [p.path for p in running_terminal_processes(timeout=timeout)]


def terminal_process_inventory(reader=None) -> tuple[list | None, str]:
    """Terminal inventory for liveness pre-flight decisions, with its state.

    ``reader`` overrides the inventory source (defaults to
    :func:`running_terminal_processes`; ``running_terminals`` may be passed when
    only executable paths are needed).

    Returns:

      ``(entries, TERMINAL_INVENTORY_OBSERVED)``
          The inventory was read. An absent terminal is a DEFINITE absence.
      ``(None, TERMINAL_INVENTORY_TIMEOUT)``
          INCONCLUSIVE (budget exceeded under host load). Callers must fall
          through to their authoritative attach + identity verification, which
          still fails closed, instead of blocking a healthy account snapshot.

    A read *failure* that is not a timeout is never swallowed here: it
    propagates so the caller reports the real code and fails closed.
    """
    read = running_terminal_processes if reader is None else reader
    try:
        return list(read()), TERMINAL_INVENTORY_OBSERVED
    except TerminalInventoryTimeout:
        return None, TERMINAL_INVENTORY_TIMEOUT


@contextmanager
def terminal_lease(path: str):
    """Only one diagnostic worker at a time can own this terminal/data directory.

    OS locks release on worker exit/crash. This never locks the production bot
    or an MT5 file, and never terminates an MT5 process.
    """
    digest = hashlib.sha256(terminal_key(path).encode()).hexdigest()
    root = Path(tempfile.gettempdir()) / 'trading_bot_mt5_account_leases'
    root.mkdir(exist_ok=True)
    with (root / (digest + '.lock')).open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('ACCOUNT_WORKER_BUSY') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
