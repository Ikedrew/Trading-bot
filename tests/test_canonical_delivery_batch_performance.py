"""Durable producer batching preserves recovery, identity, and ordering."""
import json
import threading
import time
from unittest.mock import patch

import pytest

from core.canonical_delivery import (
    enqueue_canonical_batch, prepare_local_jsonl_handoffs, recover_local_handoffs,
    try_prepare_local_jsonl_handoffs,
)
from core.canonical_delivery_outbox import CanonicalDeliveryOutbox, EnqueueOutcome


def box(path):
    return CanonicalDeliveryOutbox(path, canonical_bucket='test-bucket')


def payloads():
    return [{'decision_id': f'D-{i}', 'symbol': 'EURUSD'} for i in range(3)]


def test_prepare_and_enqueue_each_commit_once_and_survive_restart(tmp_path):
    path = tmp_path / 'outbox.sqlite3'
    local = tmp_path / 'local.jsonl'
    records = payloads()
    content = '\n'.join(json.dumps(record) for record in records)
    outbox = box(path)
    statements = []
    outbox._db.set_trace_callback(statements.append)
    with patch('core.canonical_delivery._existing_obligation_id', return_value=None):
        intents = prepare_local_jsonl_handoffs(
            dataset='decision_ledger', content=content, symbol='EURUSD',
            partition_date='2026-10-05', local_path=local, outbox=outbox,
        )
    assert len(intents) == 3
    assert statements.count('COMMIT') == 1
    assert not local.exists()
    assert outbox._db.execute('PRAGMA synchronous').fetchone()[0] == 2
    outbox.close()
    outbox = box(path)
    assert len(outbox.pending_local_handoffs()) == 3
    local.write_text(content + '\n', encoding='utf-8')
    statements.clear()
    outbox._db.set_trace_callback(statements.append)
    with patch('core.canonical_delivery._existing_obligation_id', return_value=None):
        result = enqueue_canonical_batch(
            dataset='decision_ledger', payloads=records, symbol='EURUSD',
            partition_date='2026-10-05', outbox=outbox,
        )
    assert [r.record.payload['decision_id'] for r in result.results] == ['D-0', 'D-1', 'D-2']
    assert statements.count('COMMIT') == 1
    assert not outbox.pending_local_handoffs()
    outbox.close()
    outbox = box(path)
    with patch('core.canonical_delivery._existing_obligation_id', return_value=None):
        result = enqueue_canonical_batch(
            dataset='decision_ledger', payloads=records, symbol='EURUSD',
            partition_date='2026-10-05', outbox=outbox,
        )
    assert all(r.outcome is EnqueueOutcome.DUPLICATE for r in result.results)
    assert len(outbox.records()) == 3
    outbox.close()


def test_failed_batch_rolls_back_and_can_retry(tmp_path):
    outbox = box(tmp_path / 'outbox.sqlite3')
    with patch('core.canonical_delivery._existing_obligation_id', return_value=None):
        with pytest.raises(Exception):
            enqueue_canonical_batch(
                dataset='decision_ledger', payloads=[payloads()[0], {}],
                symbol='EURUSD', partition_date='2026-10-05', outbox=outbox,
            )
        assert not outbox.records()
        result = enqueue_canonical_batch(
            dataset='decision_ledger', payloads=payloads(), symbol='EURUSD',
            partition_date='2026-10-05', outbox=outbox,
        )
    assert len(result.results) == 3
    outbox.close()


def test_other_thread_cannot_join_uncommitted_batch(tmp_path):
    outbox = box(tmp_path / 'outbox.sqlite3')
    started = threading.Event()
    finished = threading.Event()

    def producer():
        started.set()
        outbox.enqueue(dataset='decision_ledger', payload=payloads()[1],
                       symbol='EURUSD', partition_date='2026-10-05')
        finished.set()

    with outbox.durable_batch():
        outbox.enqueue(dataset='decision_ledger', payload=payloads()[0],
                       symbol='EURUSD', partition_date='2026-10-05')
        thread = threading.Thread(target=producer)
        thread.start()
        assert started.wait(2)
        assert not finished.is_set()
    thread.join(2)
    assert finished.is_set()
    assert len(outbox.records()) == 2
    outbox.close()


def test_skipped_recovery_does_not_acquire_outbox_lock():
    with patch('core.canonical_delivery.get_delivery_outbox', side_effect=AssertionError):
        result = recover_local_handoffs(max_items=0)
    assert result.inspected == 0


def test_invalid_forensic_row_does_not_drop_valid_batch_intents(tmp_path):
    outbox = box(tmp_path / 'outbox.sqlite3')
    content = '\n'.join(json.dumps(row) for row in [payloads()[0], {}, payloads()[1]])
    with patch('core.canonical_delivery._existing_obligation_id', return_value=None):
        intents = try_prepare_local_jsonl_handoffs(
            dataset='decision_ledger', content=content, symbol='EURUSD',
            partition_date='2026-10-05', local_path=tmp_path / 'local.jsonl', outbox=outbox,
        )
    assert len(intents) == len(outbox.pending_local_handoffs()) == 2
    outbox.close()


def test_service_recovers_only_on_first_attempt_in_batch(tmp_path):
    from types import SimpleNamespace
    from core.canonical_delivery_service import CanonicalDeliveryService
    from core.canonical_delivery_worker import CanonicalDeliveryWorker
    outbox = box(tmp_path / 'outbox.sqlite3')
    worker = CanonicalDeliveryWorker(outbox, s3_client=object())
    service = CanonicalDeliveryService(worker=worker, batch_size=3)
    calls = []

    def run_once(*, recover_handoffs):
        calls.append(recover_handoffs)
        if len(calls) == 3:
            service._stop.set()
        return SimpleNamespace(error_class=None, reconciliation_anomaly=None, state_after=None)

    with patch.object(worker, 'run_once', side_effect=run_once), \
         patch.object(worker, 'reconcile_acknowledged', return_value=()):
        service._run_loop()
    assert calls == [True, False, False]
    outbox.close()


def test_real_sqlite_batch_before_after(tmp_path):
    """Compare the old per-record operations with the repaired adapters."""
    measured = {}
    with patch('core.canonical_delivery._existing_obligation_id', return_value=None):
        for mode in ('before', 'after'):
            outbox = box(tmp_path / f'{mode}.sqlite3')
            statements = []
            outbox._db.set_trace_callback(statements.append)
            started = time.perf_counter()
            for batch in range(10):
                records = [{'decision_id': f'D-{batch}-{i}', 'symbol': 'EURUSD'} for i in range(3)]
                lines = [json.dumps(record) for record in records]
                local = tmp_path / f'{mode}-{batch}.jsonl'
                if mode == 'before':
                    for record, line in zip(records, lines):
                        outbox.prepare_local_handoff(
                            dataset='decision_ledger', payload=record, symbol='EURUSD',
                            partition_date='2026-10-05', local_path=local, local_line=line,
                        )
                    for record in records:
                        outbox.enqueue(dataset='decision_ledger', payload=record,
                                       symbol='EURUSD', partition_date='2026-10-05')
                else:
                    prepare_local_jsonl_handoffs(
                        dataset='decision_ledger', content='\n'.join(lines), symbol='EURUSD',
                        partition_date='2026-10-05', local_path=local, outbox=outbox,
                    )
                    enqueue_canonical_batch(
                        dataset='decision_ledger', payloads=records, symbol='EURUSD',
                        partition_date='2026-10-05', outbox=outbox,
                    )
            measured[mode] = {'seconds': time.perf_counter() - started,
                              'commits': statements.count('COMMIT')}
            assert len(outbox.records()) == 30
            assert not outbox.pending_local_handoffs()
            outbox.close()
    assert measured['before']['commits'] == 60
    assert measured['after']['commits'] == 20
    print('REAL_SQLITE_BATCH_BEFORE_AFTER', measured)
