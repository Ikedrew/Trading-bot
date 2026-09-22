"""Test-only isolated worker child: the real account worker with a frozen inventory.

Reproduces the confirmed incident inside a real subprocess boundary (no MT5
terminal, no broker, no network): the PowerShell terminal-process inventory
exceeds its budget and raises ``subprocess.TimeoutExpired`` exactly as it did
under live host load.

Used by tests/test_account_execution_recovery.py via
``manager.WORKER_MODULE`` so the parent/child JSON contract is exercised for
real. The inventory double is installed with a module-attribute assignment
inside this throwaway child process only.
"""

import json
import subprocess
import sys

# Bind core modules that read MetaTrader5 attributes at import time
# (core.config does mt5.TIMEFRAME_M5) to the REAL MetaTrader5 package BEFORE
# the fake is placed in sys.modules below; otherwise a fresh child process dies
# on import and the incident reason collapses into a generic WORKER_READ_FAILED.
import core.config  # noqa: F401
import core.symbol_resolver  # noqa: F401

from core.accounts import terminal
from core.accounts.config import AccountConfig
from core.accounts.worker import run_worker
from tests._account_fake_mt5 import FakeMT5


def _inventory_times_out(_command, **kwargs):
    raise subprocess.TimeoutExpired('powershell.exe', kwargs.get('timeout', 10))


# Mirrors the healthy broker view: suffixed names for the explicit symbol map,
# and a positive trade_tick_value so account sizing is not spuriously blocked.
SUFFIX = {'METAQUOTES': '.mq', 'ADMIRALS': '.adm', 'VANTAGE': '.van'}
TICK_VALUE = {'METAQUOTES': 1.0, 'ADMIRALS': 2.0, 'VANTAGE': 4.0}


def main():
    payload = json.load(sys.stdin)
    # The incident: the terminal inventory query never returns in budget.
    terminal.subprocess.run = _inventory_times_out
    config = AccountConfig(**payload['account'])
    fake = FakeMT5(config, suffix=SUFFIX.get(config.account_id, ''))
    tick_value = TICK_VALUE.get(config.account_id, 1.0)
    for spec in fake.specs.values():
        spec.trade_tick_value = tick_value
    sys.modules['MetaTrader5'] = fake
    print(json.dumps(run_worker(
        config,
        include_symbol_inventory=bool(payload.get('include_symbol_inventory', False)),
    ), allow_nan=False))


if __name__ == '__main__':
    main()
