"""Concurrent account diagnostics using isolated, bounded subprocesses."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

from .config import configuration_blocks, terminal_key
from .terminal import INVENTORY_TIMEOUT_ENV, hidden_process_flags
from .worker import unavailable

WORKER_MODULE = 'core.accounts.worker'
ROOT = Path(__file__).resolve().parents[2]

# Per-account worker deadline. Never a hard-coded budget: operators raise/lower
# it with MT5_ACCOUNT_WORKER_TIMEOUT_SECONDS (the default is only a fallback).
WORKER_TIMEOUT_ENV = 'MT5_ACCOUNT_WORKER_TIMEOUT_SECONDS'
DEFAULT_WORKER_TIMEOUT_SECONDS = 25.0

# Canonical account-runtime codes worth surfacing from a dead child's stderr.
# ONLY these known tokens are ever echoed: raw stderr may contain platform text,
# paths or account data and is never forwarded across the process boundary.
_CHILD_STDERR_CODES = (
    'TERMINAL_INVENTORY_TIMEOUT', 'TERMINAL_INVENTORY_UNAVAILABLE',
    'ACCOUNT_WORKER_BUSY', 'WORKER_TIMEOUT', 'WORKER_FAILED',
    'TimeoutExpired', 'MemoryError', 'ModuleNotFoundError', 'ImportError',
)

# Well-known Windows NTSTATUS exits as subprocess reports them (signed).
_WINDOWS_EXIT_CODES = {
    -1073741819: 'WORKER_CRASHED_ACCESS_VIOLATION',
    -1073741571: 'WORKER_CRASHED_STACK_OVERFLOW',
    -1073741515: 'WORKER_CRASHED_MISSING_DLL',
    -1073741701: 'WORKER_CRASHED_DLL_INIT_FAILED',
}


def worker_timeout(env=None) -> float:
    """Configured per-account worker deadline in seconds (env override)."""
    env = os.environ if env is None else env
    raw = str(env.get(WORKER_TIMEOUT_ENV, '') or '').strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_WORKER_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_WORKER_TIMEOUT_SECONDS


def _exit_code_label(returncode) -> str:
    try:
        code = int(returncode)
    except (TypeError, ValueError):
        return 'WORKER_EXIT_UNKNOWN'
    # Windows reports NTSTATUS exits signed; the unsigned hex form is the
    # stable, searchable representation.
    return 'WORKER_EXIT_0x%08X' % (code & 0xFFFFFFFF) if code < 0 else f'WORKER_EXIT_{code}'


def _stderr_code(stderr: str) -> str:
    text = str(stderr or '')
    if not text.strip():
        return ''
    for token in _CHILD_STDERR_CODES:
        if token in text:
            return token
    return 'WORKER_STDERR_PRESENT'


def child_failure_reasons(result) -> list[str]:
    """Observable, secret-free failure detail for a child that did not exit 0.

    Surfaces the child return code (decimal or Windows NTSTATUS hex), a known
    crash label when the code matches one, and an allow-listed stderr code.
    """
    reasons = [_exit_code_label(getattr(result, 'returncode', None))]
    code = getattr(result, 'returncode', None)
    crash = _WINDOWS_EXIT_CODES.get(code) if isinstance(code, int) else None
    if crash:
        reasons.append(crash)
    detail = _stderr_code(getattr(result, 'stderr', '') or '')
    if detail:
        reasons.append(detail)
    return reasons


def run_isolated(account, request=None, *, timeout=None, include_symbol_inventory=False):
    budget = worker_timeout() if timeout is None else float(timeout)
    payload = {'account': asdict(account), 'request': asdict(request) if request else None}
    payload['include_symbol_inventory'] = include_symbol_inventory
    # Workers use saved terminal sessions, so no account/AWS/Discord secrets need
    # to cross this boundary. No secret is included in argv, JSON or error output.
    # MT5_TERMINAL_INVENTORY_TIMEOUT_SECONDS is a non-secret tunable and is
    # forwarded so the configured inventory budget reaches the child worker.
    child_env = {k: v for k, v in os.environ.items() if k.upper() in {
        'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE',
        'APPDATA', 'LOCALAPPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)',
        'COMMONPROGRAMFILES', 'COMSPEC', 'SYSTEMDRIVE', INVENTORY_TIMEOUT_ENV,
    }}
    child_env['PYTHONIOENCODING'] = 'utf-8'
    try:
        result = subprocess.run(
            [sys.executable, '-m', WORKER_MODULE], input=json.dumps(payload),
            capture_output=True, encoding='utf-8', timeout=budget, cwd=ROOT,
            env=child_env, creationflags=hidden_process_flags(),
        )
        if result.returncode:
            # Real child failure: raise code + stderr detail, never a bare
            # generic reason (the incident needed a missing inventory timeout).
            return unavailable(account, child_failure_reasons(result))
        response = json.loads(result.stdout)
        if (response.get('account_id'), response.get('broker'), response.get('server'), response.get('login')) != account.identity:
            return unavailable(account, ['WORKER_RESPONSE_IDENTITY_MISMATCH'])
        if response.get('connected') and not response.get('identity_verified'):
            return unavailable(account, ['WORKER_RESPONSE_NOT_VERIFIED'])
        return response
    except subprocess.TimeoutExpired:
        # subprocess.run kills/reaps only its Python worker, never terminal64.exe.
        return unavailable(account, ['WORKER_TIMEOUT'])
    except Exception:
        return unavailable(account, ['WORKER_FAILED'])


def diagnose(accounts, request=None, *, timeout=None, config_only=False,
             include_symbol_inventory=False,
             global_execution_enabled: bool | None = None):
    """Concurrent per-account snapshots; one account never blocks another.

    ``timeout`` is the per-account worker deadline in seconds. When omitted it
    resolves to :func:`worker_timeout` (MT5_ACCOUNT_WORKER_TIMEOUT_SECONDS),
    never to a hard-coded budget.
    """
    accounts = tuple(accounts)
    if len({a.account_id for a in accounts}) != len(accounts):
        raise ValueError('DUPLICATE_ACCOUNT_ID')
    blocks = configuration_blocks(accounts)
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, len(accounts))) as pool:
        futures = {}
        for account in accounts:
            reasons = blocks[account.account_id]
            if not account.enabled:
                reasons = ['DISABLED', *reasons]
            elif config_only and not reasons:
                reasons = ['CONFIGURED_NOT_PROBED']
            if reasons:
                results[account.account_id] = unavailable(account, reasons)
            else:
                futures[pool.submit(run_isolated, account, request, timeout=timeout,
                                    include_symbol_inventory=include_symbol_inventory)] = account
        for future in as_completed(futures):
            account = futures[future]
            try:
                results[account.account_id] = future.result()
            except Exception:
                results[account.account_id] = unavailable(account, ['WORKER_FAILED'])
    # Different executable paths must also resolve to different terminal data.
    connected = [a for a in accounts if results[a.account_id].get('connected')]
    data_paths = {a.account_id: terminal_key(results[a.account_id].get('terminal_data_path', ''))
                  for a in connected}
    for account in connected:
        data = data_paths[account.account_id]
        if not data:
            results[account.account_id] = unavailable(account, ['TERMINAL_DATA_PATH_UNVERIFIED'])
        elif account.account_id != 'METAQUOTES' and any(
            other.account_id != account.account_id and
            data_paths[other.account_id] == data
            for other in connected
        ):
            results[account.account_id] = unavailable(account, ['SHARED_TERMINAL_DATA_PATH'])
    # REPAIR: report the SAME effective execution gate production routing uses
    # (core.accounts.account_router.execution_enabled_for) instead of an
    # unwired default constant. No second definition of execution eligibility.
    from .account_router import execution_enabled_for
    if global_execution_enabled is None:
        try:
            from core import config as _cfg
            global_execution_enabled = bool(getattr(_cfg, 'EXECUTION_ENABLED', True))
        except Exception:
            global_execution_enabled = True
    for account in accounts:
        results[account.account_id]['execution_enabled'] = bool(
            execution_enabled_for(account, global_execution_enabled=global_execution_enabled))
    return [results[a.account_id] for a in accounts]
