"""Local V1 runtime checkpoint for account ownership and restart lineage.

Not a research dataset; no S3 client or historical rewrite.
"""
import json
import os
from pathlib import Path
import tempfile

from core.position_ownership import PositionOwnership

STATE_DIR = Path('logs/owned_positions')


def position_key(owner):
    # JSON gives an unambiguous deterministic encoding of the composite key.
    return json.dumps([owner.account_id, int(owner.position_ticket)], separators=(',', ':'))


def _path(owner):
    from .config import ACCOUNT_IDS
    if owner.account_id not in ACCOUNT_IDS or owner.position_ticket <= 0:
        raise ValueError('INVALID_POSITION_OWNERSHIP')
    return STATE_DIR / owner.account_id / f'{owner.position_ticket}.json'


def save(owner, **state):
    path = _path(owner)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({'schema_version': 'owned_position_v1',
                          'ownership': owner._asdict(), **state}, allow_nan=False)
    fd, temp = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def load(owner):
    try:
        state = json.loads(_path(owner).read_text(encoding='utf-8'))
        saved = PositionOwnership(**state['ownership'])
        if (saved.account_id, saved.position_ticket, saved.broker, saved.broker_server,
                saved.broker_symbol) != (owner.account_id, owner.position_ticket,
                owner.broker, owner.broker_server, owner.broker_symbol):
            return None
        return state
    except (OSError, ValueError, KeyError, TypeError):
        return None


def ownership_from_fill(account, reader, result, *, symbol, broker_symbol,
                        magic, canonical_opportunity_id='', correlation_id='',
                        decision_id='', account_execution_id='', trade_id=''):
    """Resolve an actual position ticket; an entry DEAL is never a position key."""
    order = int(getattr(result, 'order', 0) or 0)
    deal = int(getattr(result, 'deal', 0) or 0)
    rows = reader.read('positions_get', ticket=order) if order else ()
    if not rows and deal:
        deals = reader.read('history_deals_get', ticket=deal)
        ids = {int(d.position_id) for d in (deals or ())
               if int(d.ticket) == deal and int(d.position_id) > 0}
        rows = reader.read('positions_get', ticket=ids.pop()) if len(ids) == 1 else ()
    if not rows or len(rows) != 1:
        return None  # Already closed / broker cannot prove a live position.
    pos = rows[0]
    if pos.symbol != broker_symbol or int(pos.magic) != magic:
        return None
    return PositionOwnership(account.account_id, account.broker, account.server,
        int(pos.ticket), order or None, deal or None, symbol, broker_symbol,
        canonical_opportunity_id, correlation_id, decision_id, account_execution_id, trade_id)
