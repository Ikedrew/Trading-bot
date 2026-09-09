"""Decision-ledger immutable S3 batches; all I/O uses temporary files/fake S3."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import boto3
import pytest
from botocore.exceptions import ClientError

from core import decision_ledger as ledger
from research_engine.data_access.loaders import load_decision_ledger
from research_engine.data_access.s3_source import (
    S3ResearchDataSource, reset_default_source, set_default_source,
)
from tests.test_research_engine_s3_source import FakeS3


class CreateOnlyS3(FakeS3):
    def __init__(self):
        super().__init__({}, page_size=2)
        self.lock = threading.Lock()
        self.puts = []

    def put_object(self, **kw):
        with self.lock:
            self.puts.append(kw)
            assert kw['IfNoneMatch'] == '*'
            if kw['Key'] in self.objects:
                raise ClientError({'Error': {'Code': 'PreconditionFailed'}}, 'PutObject')
            self.objects[kw['Key']] = kw['Body'].decode('utf-8')
        return {'VersionId': 'only-version'}


@pytest.fixture
def storage(monkeypatch, disable_s3_mirror_in_tests):
    fake = CreateOnlyS3()
    monkeypatch.setattr(boto3, 'client', lambda *a, **kw: fake)
    monkeypatch.setattr('core.config.EVENT_STREAM_S3_MIRROR', True)
    monkeypatch.setattr(ledger, '_S3_BUCKET', 'test-bucket')
    monkeypatch.setattr('core.s3_write_observability.record_s3_success', Mock())
    failure = Mock()
    monkeypatch.setattr('core.s3_write_observability.record_s3_failure', failure)
    set_default_source(S3ResearchDataSource(bucket='test-bucket', client=fake))
    yield fake, failure
    reset_default_source()


def entry(i, symbol='EURUSD', date='2026-09-09'):
    record = ledger.build_ledger_entry(
        symbol=symbol, cycle_id=i, decision=ledger.DecisionOutcome.NO_TRADE,
        reason='score_below_threshold', decision_id=f'decision-{i}',
        canonical_opportunity_id=f'opportunity-{i}', observation_id=f'obs-{i}',
        entity_id=f'entity-{i}', correlation_id=f'correlation-{i}',
        v10={'nested': {'score': 0.25}},
    )
    record['timestamp'] = f'{date}T11:30:00.000Z'
    return record


def test_consecutive_flushes_never_reuse_or_mutate_objects(storage, tmp_path):
    fake, failure = storage
    writer = ledger.DecisionLedgerWriter(local_dir=str(tmp_path), flush_batch_size=1)
    first, second = entry(1), entry(2)
    assert writer.write(first)
    snapshot = dict(fake.objects)
    assert len(snapshot) == 1
    assert writer.write(second)
    assert len(fake.objects) == 2
    assert len({p['Key'] for p in fake.puts}) == 2
    assert all(fake.objects[k] == body for k, body in snapshot.items())
    assert fake.get_calls == []  # No read/append/replace step.
    assert load_decision_ledger('EURUSD') == [first, second]
    failure.assert_not_called()


def test_concurrent_independent_writers_recover_every_record(storage, tmp_path, monkeypatch):
    fake, failure = storage
    records = [entry(i) for i in range(32)]
    frozen = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(ledger, 'datetime', SimpleNamespace(now=lambda tz: frozen))

    def persist(record):
        # Independent instances model separate processes/restarts, without a
        # shared writer lock or counter. Fake S3 serializes conditional creates.
        writer = ledger.DecisionLedgerWriter(
            local_dir=str(tmp_path / str(record['cycle_id'])), flush_batch_size=1,
        )
        return writer.write(record)

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(persist, records))
    assert len(fake.puts) == len(fake.objects) == len(records)
    assert fake.get_calls == []
    assert load_decision_ledger('EURUSD') == records
    assert fake.list_calls > 1  # Same partition spans multiple listing pages.
    assert len(fake.get_calls) == len(records)
    failure.assert_not_called()


def test_reader_loads_legacy_leaf_and_new_batches(storage, tmp_path):
    fake, _ = storage
    old = entry(0)
    old_key = ('core/decision_ledger/schema_version=decision_ledger_v1/'
               'symbol=EURUSD/date=2026-09-09/part-000.jsonl')
    old_body = json.dumps(old) + '\n'
    fake.objects[old_key] = old_body
    writer = ledger.DecisionLedgerWriter(local_dir=str(tmp_path), flush_batch_size=1)
    records = [entry(1), entry(2), entry(3)]
    for record in records:
        assert writer.write(record)
    assert load_decision_ledger('EURUSD') == [old, *records]
    assert fake.objects[old_key] == old_body
    assert old_key not in [p['Key'] for p in fake.puts]


@pytest.mark.parametrize('symbol,date', [('EURUSD', '2026-09-09'), ('GBPUSD', '2026-09-08')])
def test_contract_and_record_date_partition_unchanged(storage, tmp_path, symbol, date):
    fake, _ = storage
    records = [entry(1, symbol, date), entry(2, symbol, date)]
    original = json.loads(json.dumps(records))
    writer = ledger.DecisionLedgerWriter(local_dir=str(tmp_path))
    for record in records:
        assert writer.write(record)
    writer.flush()
    key, body = next(iter(fake.objects.items()))
    assert key.startswith(f'core/decision_ledger/schema_version=decision_ledger_v1/symbol={symbol}/date={date}/part-')
    assert key.endswith('.jsonl') and not key.endswith('/part-000.jsonl')
    assert all(r['schema_version'] == 'decision_ledger_v1' for r in records)
    assert records == original == [json.loads(line) for line in body.splitlines()]
    local = (tmp_path / symbol / f'{date}.jsonl').read_text(encoding='utf-8')
    assert [json.loads(line) for line in local.splitlines()] == original
    assert load_decision_ledger(symbol) == original


def test_forced_collision_preserves_existing_object_and_reports_failure(storage, tmp_path, monkeypatch):
    fake, failure = storage
    frozen = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(ledger, 'datetime', SimpleNamespace(now=lambda tz: frozen))
    monkeypatch.setattr(ledger, 'uuid4', lambda: SimpleNamespace(hex='forced-collision'))
    writer = ledger.DecisionLedgerWriter(local_dir=str(tmp_path), flush_batch_size=1)
    first, second = entry(1), entry(2)
    writer.write(first)
    snapshot = dict(fake.objects)
    writer.write(second)
    assert len(fake.puts) == 2
    assert fake.objects == snapshot
    failure.assert_called_once()
    assert failure.call_args.args[0] == 'decision_ledger'
    assert failure.call_args.args[1].response['Error']['Code'] == 'PreconditionFailed'
    # Existing fire-and-forget failure semantics retain the batch locally.
    local = tmp_path / 'EURUSD' / '2026-09-09.jsonl'
    assert [json.loads(line) for line in local.read_text().splitlines()] == [first, second]


def test_installed_sdk_supports_create_only_put():
    from botocore.session import get_session
    model = get_session().get_service_model('s3').operation_model('PutObject')
    assert 'IfNoneMatch' in model.input_shape.members
