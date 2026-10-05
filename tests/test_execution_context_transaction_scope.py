"""Execution-context preparation stays outside its atomic SQL mutation."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.canonical_delivery import prepare_local_jsonl_handoffs
from core.canonical_delivery_outbox import CanonicalDeliveryOutbox


def test_single_context_resolves_path_and_identity_before_begin(tmp_path):
    box = CanonicalDeliveryOutbox(tmp_path / 'outbox.sqlite3', canonical_bucket='test-bucket')
    local = tmp_path / 'context.jsonl'
    payload = {'correlation_id': 'COR-SCOPE', 'symbol': 'EURUSD'}
    statements = []
    box._db.set_trace_callback(statements.append)
    original_resolve = Path.resolve
    resolved = []

    def resolve(path, *args, **kwargs):
        if path == local:
            assert not box._db.in_transaction
            resolved.append(path)
        return original_resolve(path, *args, **kwargs)

    def find_obligation(*args):
        assert not box._db.in_transaction
        return None

    with patch.object(Path, 'resolve', resolve), \
         patch('core.canonical_delivery._existing_obligation_id', side_effect=find_obligation):
        intent, = prepare_local_jsonl_handoffs(
            dataset='execution_context', content=json.dumps(payload), symbol='EURUSD',
            partition_date='2026-10-05', local_path=local, outbox=box,
        )
    assert resolved == [local]
    assert statements.count('BEGIN IMMEDIATE') == statements.count('COMMIT') == 1
    assert not local.exists()
    assert box._db.execute('PRAGMA synchronous').fetchone()[0] == 2
    assert box._db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    box.close()
    box = CanonicalDeliveryOutbox(tmp_path / 'outbox.sqlite3', canonical_bucket='test-bucket')
    recovered, = box.pending_local_handoffs()
    assert recovered == intent
    box.close()


def test_invalid_context_identity_fails_before_begin(tmp_path):
    box = CanonicalDeliveryOutbox(tmp_path / 'outbox.sqlite3', canonical_bucket='test-bucket')
    statements = []
    box._db.set_trace_callback(statements.append)
    with pytest.raises(ValueError, match='EXACT_IDENTITY_FIELD_REQUIRED'):
        prepare_local_jsonl_handoffs(
            dataset='execution_context', content='{}', symbol='EURUSD',
            partition_date='2026-10-05', local_path=tmp_path / 'local.jsonl', outbox=box,
        )
    assert 'BEGIN IMMEDIATE' not in statements
    assert not box.pending_local_handoffs()
    box.close()
