"""Read-only Windows terminal inventory and cross-process diagnostic leases."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from .config import terminal_key


def hidden_process_flags() -> int:
    return getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0


def running_terminals() -> list[str]:
    if os.name != 'nt':
        return []
    # Constant command, no interpolated account values, secrets or command lines.
    command = ('@(Get-Process terminal64 -ErrorAction SilentlyContinue | '
               'Select-Object -ExpandProperty Path -ErrorAction SilentlyContinue | '
               'Where-Object { $_ }) | ConvertTo-Json -Compress')
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
        capture_output=True, text=True, timeout=10, creationflags=hidden_process_flags(),
    )
    if result.returncode:
        raise RuntimeError('TERMINAL_INVENTORY_UNAVAILABLE')
    paths = json.loads(result.stdout or '[]')
    return [paths] if isinstance(paths, str) else (paths or [])


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
