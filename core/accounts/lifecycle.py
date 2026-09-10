"""Account-owned lifecycle IPC. No terminal connection exists in this process."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from core.position_ownership import PositionOwnership
from .config import load_accounts, configuration_blocks
from .terminal import hidden_process_flags
from .worker import AccountReadError


def isolated_lifecycle(account, request, *, timeout=25):
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE',
        'APPDATA', 'LOCALAPPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)',
        'COMMONPROGRAMFILES', 'COMSPEC', 'SYSTEMDRIVE'}}
    env['PYTHONIOENCODING'] = 'utf-8'
    result = subprocess.run(
        [sys.executable, '-m', 'core.accounts.lifecycle_worker'],
        input=json.dumps({'account': asdict(account), **request}),
        capture_output=True, encoding='utf-8', timeout=timeout,
        cwd=Path(__file__).resolve().parents[2], env=env,
        creationflags=hidden_process_flags())
    if result.returncode:
        raise AccountReadError('LIFECYCLE_WORKER_FAILED')
    return json.loads(result.stdout)


class LifecycleRouter:
    def __init__(self, accounts=None, *, transport=None):
        self.accounts = tuple(load_accounts() if accounts is None else accounts)
        self.transport = transport or isolated_lifecycle
        self.blocks = configuration_blocks(self.accounts)

    def account(self, account_id):
        matches = [a for a in self.accounts if a.account_id == account_id and a.enabled]
        if len(matches) != 1 or self.blocks.get(account_id):
            raise AccountReadError('UNKNOWN_OR_INVALID_ACCOUNT')
        return matches[0]

    def validate(self, owner):
        if not isinstance(owner, PositionOwnership):
            raise AccountReadError('MISSING_POSITION_OWNERSHIP')
        account = self.account(owner.account_id)
        if (owner.broker, owner.broker_server) != (account.broker, account.server):
            raise AccountReadError('OWNERSHIP_ACCOUNT_MISMATCH')
        if owner.position_ticket <= 0 or not owner.broker_symbol or not owner.canonical_symbol:
            raise AccountReadError('INCOMPLETE_POSITION_OWNERSHIP')
        return account

    def call(self, account_id, operation, *, owner=None, **arguments):
        account = self.account(account_id)
        if owner is not None:
            self.validate(owner)
            if owner.account_id != account_id:
                raise AccountReadError('OWNERSHIP_ACCOUNT_MISMATCH')
        elif operation != 'recover':
            raise AccountReadError('MISSING_POSITION_OWNERSHIP')
        response = self.transport(account, {
            'operation': operation, 'ownership': owner._asdict() if owner else None,
            'arguments': arguments})
        if (response.get('account_id'), response.get('broker'), response.get('server'),
                response.get('login')) != account.identity or not response.get('identity_verified'):
            raise AccountReadError('WORKER_RESPONSE_IDENTITY_MISMATCH')
        if response.get('error'):
            raise AccountReadError(response['error'])
        value = response.get('value')
        if isinstance(value, list):
            return [SimpleNamespace(**row) for row in value]
        return value

    def read(self, owner, operation, **arguments):
        return self.call(owner.account_id, operation, owner=owner, **arguments)


def legacy_owner(*, ticket, symbol, broker_symbol, mt5, accounts=None):
    """Only one enabled account, METAQUOTES, with verified configured session.

    Missing identity is never defaulted in multi-account mode. The legacy
    connection is inspected, never switched; subsequent lifecycle I/O uses IPC.
    """
    from .worker import AccountReader
    router = LifecycleRouter(accounts)
    enabled = [a for a in router.accounts if a.enabled]
    if len(enabled) != 1 or enabled[0].account_id != 'METAQUOTES':
        raise AccountReadError('MISSING_POSITION_OWNERSHIP')
    account = router.account(enabled[0].account_id)
    AccountReader(account, mt5).verify()
    return PositionOwnership(account.account_id, account.broker, account.server,
                             ticket, canonical_symbol=symbol, broker_symbol=broker_symbol)
