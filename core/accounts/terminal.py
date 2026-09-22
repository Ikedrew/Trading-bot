"""Read-only Windows terminal inventory and cross-process diagnostic leases."""

from collections import namedtuple
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

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
    # Constant command, no interpolated account values, secrets or command lines.
    command = ('@(Get-Process terminal64 -ErrorAction SilentlyContinue | '
               'ForEach-Object { $p = $_.Path; if ($p) { '
               '[pscustomobject]@{ Id = $_.Id; Path = $p } } }) | ConvertTo-Json -Compress')
    try:
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
            capture_output=True, text=True, timeout=budget, creationflags=hidden_process_flags(),
        )
    except subprocess.TimeoutExpired:
        # Host-load budget exhaustion: an inventory timeout must NEVER be
        # reported as "no terminal is running". Callers treat this as
        # inconclusive and keep the authoritative attach/identity check as the
        # decider (see terminal_process_inventory).
        raise TerminalInventoryTimeout(TERMINAL_INVENTORY_TIMEOUT) from None
    if result.returncode:
        raise TerminalInventoryError(TERMINAL_INVENTORY_UNAVAILABLE)
    try:
        data = json.loads(result.stdout or '[]')
    except (TypeError, ValueError):
        raise TerminalInventoryError(TERMINAL_INVENTORY_UNAVAILABLE) from None
    if isinstance(data, dict):
        data = [data]
    processes = []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        pid = item.get('Id')
        path = item.get('Path')
        if pid and path:
            processes.append(TerminalProcess(int(pid), str(path)))
    return processes


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
