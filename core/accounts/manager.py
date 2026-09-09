"""Concurrent account diagnostics using isolated, bounded subprocesses."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

from .config import configuration_blocks, terminal_key
from .terminal import hidden_process_flags
from .worker import unavailable

WORKER_MODULE = 'core.accounts.worker'
ROOT = Path(__file__).resolve().parents[2]


def run_isolated(account, request=None, *, timeout=25, include_symbol_inventory=False):
    payload = {'account': asdict(account), 'request': asdict(request) if request else None}
    payload['include_symbol_inventory'] = include_symbol_inventory
    # Workers use saved terminal sessions, so no account/AWS/Discord secrets need
    # to cross this boundary. No secret is included in argv, JSON or error output.
    child_env = {k: v for k, v in os.environ.items() if k.upper() in {
        'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE',
        'APPDATA', 'LOCALAPPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)',
        'COMMONPROGRAMFILES', 'COMSPEC', 'SYSTEMDRIVE',
    }}
    child_env['PYTHONIOENCODING'] = 'utf-8'
    try:
        result = subprocess.run(
            [sys.executable, '-m', WORKER_MODULE], input=json.dumps(payload),
            capture_output=True, encoding='utf-8', timeout=timeout, cwd=ROOT,
            env=child_env, creationflags=hidden_process_flags(),
        )
        if result.returncode:
            return unavailable(account, ['WORKER_FAILED'])
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


def diagnose(accounts, request=None, *, timeout=25, config_only=False, include_symbol_inventory=False):
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
    return [results[a.account_id] for a in accounts]
