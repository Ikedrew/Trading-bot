"""Mapped outbox reads retain restart validation and durable writes."""
import sqlite3

import pytest

from core.canonical_delivery_outbox import (
    CanonicalDeliveryOutbox, EnqueueOutcome, OutboxCorruptionError,
)


def open_box(path):
    return CanonicalDeliveryOutbox(path, canonical_bucket='test-bucket')


def enqueue(box, suffix='1'):
    return box.enqueue(dataset='decision_ledger',
                       payload={'decision_id': 'D-' + suffix, 'symbol': 'EURUSD'},
                       symbol='EURUSD', partition_date='2026-10-05')


def test_mapped_reads_preserve_full_durability_and_restart_idempotency(tmp_path):
    path = tmp_path / 'outbox.sqlite3'
    box = open_box(path)
    first = enqueue(box)
    assert box._db.execute('PRAGMA synchronous').fetchone()[0] == 2
    assert box._db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    assert box._db.execute('PRAGMA quick_check').fetchall()[0][0] == 'ok'
    box.close()
    box = open_box(path)
    duplicate = enqueue(box)
    assert duplicate.outcome is EnqueueOutcome.DUPLICATE
    assert duplicate.record == box.get(first.record.outbox_id)
    assert len(box.records()) == 1
    box.close()


def test_mapped_restart_still_validates_every_payload_hash(tmp_path):
    path = tmp_path / 'outbox.sqlite3'
    box = open_box(path)
    enqueue(box, 'first')
    last = enqueue(box, 'last').record
    box.close()
    with sqlite3.connect(path) as db:
        db.execute('UPDATE outbox_records SET payload_sha256=? WHERE outbox_id=?',
                   ('0' * 64, last.outbox_id))
    with pytest.raises(OutboxCorruptionError, match='payload hash mismatch'):
        open_box(path)
