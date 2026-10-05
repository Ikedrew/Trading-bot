"""Before/after benchmark for the terminal process inventory (read-only).

Compares the legacy PowerShell inventory (``Get-Process terminal64``) with the
native Windows process-API inventory now used by
``core.accounts.terminal.running_terminal_processes``.

Usage:
    python tools/bench_terminal_inventory.py --runs 10
"""
from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LEGACY_COMMAND = ('@(Get-Process terminal64 -ErrorAction SilentlyContinue | '
                  'ForEach-Object { $p = $_.Path; if ($p) { '
                  '[pscustomobject]@{ Id = $_.Id; Path = $p } } }) | '
                  'ConvertTo-Json -Compress')


def _legacy():
    return subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', LEGACY_COMMAND],
        capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', type=int, default=10)
    args = ap.parse_args()

    from core.accounts import terminal

    # Warm the native path (first call pays ctypes WinDLL bind).
    terminal.running_terminal_processes()
    samples = {'legacy_powershell': [], 'native_process_api': []}
    found = {}
    for _ in range(args.runs):
        t0 = time.perf_counter()
        _legacy()
        samples['legacy_powershell'].append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        procs = terminal.running_terminal_processes()
        samples['native_process_api'].append((time.perf_counter() - t0) * 1000)
        found['native_process_api'] = [(p.pid, p.path) for p in procs]

    for label, values in samples.items():
        print(f'{label:22s} n={len(values):2d} '
              f'min={min(values):7.1f} ms  median={statistics.median(values):7.1f} ms  '
              f'max={max(values):7.1f} ms')
    print('terminals_found =', found['native_process_api'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
