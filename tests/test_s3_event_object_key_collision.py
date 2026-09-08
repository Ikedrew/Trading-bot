"""Regression tests for append-only S3 event object keys."""

from __future__ import annotations

import re
from unittest.mock import Mock, patch

from core.storage.s3_batch_writer import S3BatchWriter


class _ImmediateThread:
    def __init__(self, *, target, args, **_kwargs):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


def _event(symbol="EURUSD", ts=1788883200000):
    return {"ts_utc_ms": ts, "type": "SYSTEM_HEALTH", "symbol": symbol, "payload": {"proof": True}}


def _writer(session_id: str) -> tuple[S3BatchWriter, Mock]:
    writer = S3BatchWriter(bucket="test-bucket", max_buffer_size=1, session_id=session_id)
    writer._running = False
    client = Mock()
    writer._client = client
    return writer, client


def _written_keys(client: Mock) -> list[str]:
    return [call.kwargs["Key"] for call in client.put_object.call_args_list]


def test_same_process_multiple_writes_have_distinct_create_only_keys():
    writer, client = _writer("20260908T160000000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    with patch("core.storage.s3_batch_writer.threading.Thread", _ImmediateThread):
        writer.add_event(_event())
        writer.add_event(_event(ts=1788883201000))
    keys = _written_keys(client)
    assert len(keys) == len(set(keys)) == 2
    assert keys[0].endswith("-00000001.jsonl")
    assert keys[1].endswith("-00000002.jsonl")
    assert all(call.kwargs["IfNoneMatch"] == "*" for call in client.put_object.call_args_list)


def test_simulated_same_date_restart_first_key_cannot_match_prior_first_key():
    first, first_client = _writer("20260908T160000000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    restarted, restarted_client = _writer("20260908T160001000000Z-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    with patch("core.storage.s3_batch_writer.threading.Thread", _ImmediateThread):
        first.add_event(_event())
        restarted.add_event(_event())
    assert _written_keys(first_client)[0] != _written_keys(restarted_client)[0]
    assert "/symbol=EURUSD/date=2026-09-08/" in _written_keys(first_client)[0]
    assert "/symbol=EURUSD/date=2026-09-08/" in _written_keys(restarted_client)[0]


def test_automatic_writer_instances_receive_distinct_session_ids():
    first = S3BatchWriter(bucket="test-bucket")
    second = S3BatchWriter(bucket="test-bucket")
    first._running = second._running = False
    assert first._session_id != second._session_id


def test_date_rollover_keeps_dates_partitioned_and_keys_unique():
    writer, client = _writer("20260908T160000000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    with patch("core.storage.s3_batch_writer.threading.Thread", _ImmediateThread):
        writer.add_event(_event(ts=1788911999000))  # 2026-09-08 23:59:59 UTC
        writer.add_event(_event(ts=1788912000000))  # 2026-09-09 00:00:00 UTC
    keys = _written_keys(client)
    assert "/date=2026-09-08/" in keys[0]
    assert "/date=2026-09-09/" in keys[1]
    assert keys[0] != keys[1]


def test_multiple_symbols_keep_canonical_partitions():
    writer, client = _writer("20260908T160000000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    with patch("core.storage.s3_batch_writer.threading.Thread", _ImmediateThread):
        writer.add_event(_event(symbol="EURUSD"))
        writer.add_event(_event(symbol="XAUUSD"))
    keys = _written_keys(client)
    assert any("/symbol=EURUSD/" in key for key in keys)
    assert any("/symbol=XAUUSD/" in key for key in keys)
    assert len(keys) == len(set(keys)) == 2


def test_new_leaf_remains_jsonl_reader_discoverable_and_old_leaf_is_untouched():
    writer, client = _writer("20260908T160000000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    with patch("core.storage.s3_batch_writer.threading.Thread", _ImmediateThread):
        writer.add_event(_event())
    new_key = _written_keys(client)[0]
    assert new_key.endswith(".jsonl")
    assert re.search(r"/part-\d{8}T\d{12}Z-[0-9a-f]{32}-\d{8}\.jsonl$", new_key)
    assert "part-0001.jsonl" not in new_key


def test_precondition_collision_is_loud_and_never_retried(caplog):
    class Collision(Exception):
        response = {"Error": {"Code": "PreconditionFailed"}}

    writer, client = _writer("20260908T160000000000Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    client.put_object.side_effect = Collision("already exists")
    writer._upload("core/events/existing.jsonl", b"{}\n", 1)
    assert client.put_object.call_count == 1
    assert writer.stats()["total_errors"] == 1
    assert "object_key_collision" in caplog.text
