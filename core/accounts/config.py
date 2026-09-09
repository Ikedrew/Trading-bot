"""Account configuration without importing or changing the live runtime."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path

ACCOUNT_IDS = ('METAQUOTES', 'ADMIRALS', 'VANTAGE')
CANONICAL_SYMBOLS = ('EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'USDCAD',
                     'AUDUSD', 'NZDUSD', 'NAS100', 'US500', 'XAUUSD')
BASELINE_TERMINAL = r'C:\Program Files\MetaTrader 5\terminal64.exe'


def terminal_key(path: str) -> str:
    return str(Path(path).resolve()).replace('\\', '/').casefold() if path else ''


ORIGIN_FILENAME = 'origin.txt'


def read_terminal_origin(data_path: str) -> str:
    """Return the installation directory recorded in ``origin.txt``.

    In MT5 main (non-portable) mode the writable data directory is
    ``%APPDATA%\\MetaQuotes\\Terminal\\<hash>\\`` and that directory contains
    an ``origin.txt`` file (UTF-16, sometimes with BOM) naming the
    installation directory the terminal was launched from. In true portable
    mode (``/portable``) the data directory *is* the installation directory
    and no ``origin.txt`` exists.

    This helper never raises: missing/unreadable files yield ``''`` so the
    caller fails closed with ``TERMINAL_DATA_PATH_MISMATCH``.
    """
    if not data_path:
        return ''
    try:
        candidate = Path(data_path) / ORIGIN_FILENAME
        raw = candidate.read_bytes()
    except (OSError, ValueError):
        return ''
    text = ''
    for encoding in ('utf-16', 'utf-16-le', 'utf-16-be', 'utf-8-sig', 'utf-8'):
        try:
            text = raw.decode(encoding)
            break
        except (UnicodeDecodeError, ValueError):
            continue
    if not text:
        return ''
    # Strip BOM/NUL/whitespace; origin contains a single path line.
    text = text.replace('\x00', '').replace('\ufeff', '').strip()
    if not text:
        return ''
    line = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return line[0] if line else ''


@dataclass(frozen=True)
class AccountConfig:
    account_id: str
    broker: str
    server: str = ''
    login: int | None = None
    terminal_path: str = ''
    enabled: bool = False
    role: str = 'observe_only'
    # Store only the variable NAME. Diagnostics use MT5's already logged-in
    # session; they never read a password or call login/change the account.
    password_env: str = field(default='', repr=False)
    symbol_map: tuple[tuple[str, str], ...] = ()
    configuration_errors: tuple[str, ...] = ()
    terminal_data_path: str = ''
    portable: bool = False

    @property
    def identity(self) -> tuple:
        return self.account_id, self.broker, self.server, self.login

    def errors(self) -> list[str]:
        errors = list(self.configuration_errors)
        if self.account_id not in ACCOUNT_IDS:
            errors.append('INVALID_ACCOUNT_ID')
        if self.login is None or self.login <= 0:
            errors.append('LOGIN_REQUIRED')
        if not self.server:
            errors.append('SERVER_REQUIRED')
        if not self.terminal_path:
            errors.append('TERMINAL_PATH_REQUIRED')
        elif not Path(self.terminal_path).is_absolute():
            errors.append('TERMINAL_PATH_MUST_BE_ABSOLUTE')
        if self.terminal_data_path and not Path(self.terminal_data_path).is_absolute():
            errors.append('TERMINAL_DATA_PATH_MUST_BE_ABSOLUTE')
        if self.portable and terminal_key(self.terminal_data_path) != terminal_key(str(Path(self.terminal_path).parent)):
            errors.append('PORTABLE_DATA_PATH_MISMATCH')
        expected = 'baseline' if self.account_id == 'METAQUOTES' else 'observe_only'
        if self.role != expected:
            errors.append('ROLE_NOT_ALLOWED_IN_PHASE1')
        return errors


def load_accounts(env=None) -> tuple[AccountConfig, ...]:
    """Bad configuration is account-local; additional targets default disabled."""
    env = os.environ if env is None else env
    result = []
    for account_id, broker in zip(ACCOUNT_IDS, ('MetaQuotes', 'Admirals', 'Vantage')):
        prefix = f'MT5_{account_id}_'
        errors = []
        raw_login = env.get(prefix + 'LOGIN', '').strip()
        try:
            login = int(raw_login) if raw_login else None
        except ValueError:
            login = None
            errors.append('INVALID_LOGIN')
        default_enabled = 'true' if account_id == 'METAQUOTES' else 'false'
        raw_enabled = env.get(prefix + 'ENABLED', default_enabled).lower().strip()
        if raw_enabled not in ('true', 'false', '1', '0'):
            errors.append('INVALID_ENABLED')
        raw_portable = env.get(prefix + 'PORTABLE', 'false').strip().lower()
        if raw_portable not in ('true', 'false', '1', '0'):
            errors.append('INVALID_PORTABLE')
        try:
            mapping = json.loads(env.get(prefix + 'SYMBOL_MAP', '{}') or '{}')
            if not isinstance(mapping, dict) or any(
                k not in CANONICAL_SYMBOLS or not isinstance(v, str) or not v.strip()
                for k, v in mapping.items()
            ):
                raise ValueError
            symbol_map = tuple(sorted(mapping.items()))
        except (ValueError, TypeError):
            symbol_map = ()
            errors.append('INVALID_SYMBOL_MAP')
        result.append(AccountConfig(
            account_id=account_id, broker=broker,
            server=env.get(prefix + 'SERVER', '').strip(), login=login,
            terminal_path=env.get(prefix + 'TERMINAL_PATH',
                                  BASELINE_TERMINAL if account_id == 'METAQUOTES' else '').strip(),
            enabled=raw_enabled in ('true', '1'),
            role=env.get(prefix + 'ROLE', 'baseline' if account_id == 'METAQUOTES' else 'observe_only'),
            password_env=prefix + 'PASSWORD', symbol_map=symbol_map,
            configuration_errors=tuple(errors),
            terminal_data_path=env.get(prefix + 'TERMINAL_DATA_PATH', '').strip(),
            portable=raw_portable in ('true', '1'),
        ))
    return tuple(result)


def configuration_blocks(accounts) -> dict[str, list[str]]:
    """Reserve the baseline terminal even when baseline diagnostics are disabled."""
    blocks = {a.account_id: a.errors() for a in accounts}
    baseline_paths = {terminal_key(str(Path(a.terminal_path).parent)) for a in accounts
                      if a.account_id == 'METAQUOTES' and a.terminal_path}
    baseline_accounts = {(a.server.casefold(), a.login) for a in accounts
                         if a.account_id == 'METAQUOTES' and a.server and a.login}
    for a in accounts:
        if not a.enabled or not a.terminal_path or a.account_id == 'METAQUOTES':
            continue
        key = terminal_key(str(Path(a.terminal_path).parent))
        if key in baseline_paths:
            blocks[a.account_id].append('BASELINE_TERMINAL_RESERVED')
        elif any(b.account_id != a.account_id and b.enabled and
                 b.terminal_path and terminal_key(str(Path(b.terminal_path).parent)) == key for b in accounts):
            blocks[a.account_id].append('SHARED_TERMINAL_PATH')
        if a.server and a.login:
            identity = (a.server.casefold(), a.login)
            if identity in baseline_accounts:
                blocks[a.account_id].append('BASELINE_ACCOUNT_RESERVED')
            elif any(b.account_id != a.account_id and b.enabled and
                     (b.server.casefold(), b.login) == identity for b in accounts):
                blocks[a.account_id].append('DUPLICATE_BROKER_ACCOUNT')
    return blocks
