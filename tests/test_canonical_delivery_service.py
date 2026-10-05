from __future__ import annotations

import inspect
import threading

from core.canonical_delivery_outbox import CanonicalDeliveryOutbox
from core.canonical_delivery_worker import CanonicalDeliveryWorker, DeliveryState
from core.lifecycle_evidence_obligations import (
    LifecycleEvidenceLedger,
    ObligationStatus,
    create_dataset_obligation,
)
from core.canonical_delivery_service import (
    CanonicalDeliveryService,
    start_canonical_delivery_service,
    stop_canonical_delivery_service,
)


class StubWorker:
    def __init__(self, outbox, *, first_call=None, fail=False):
        self.outbox = outbox
        self.first_call = first_call
        self.fail = fail
        self.calls = 0

    def run_once(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated worker failure")
        if self.first_call:
            self.first_call()
            self.first_call = None
        return None

    def status(self):
        return {"pending": len(self.outbox.records())}


class ServiceS3:
    class Body:
        def __init__(self, data):
            self.data = data

        def read(self):
            return self.data

    def __init__(self):
        self.objects = {}
        self.put_calls = []

    def put_object(self, **kwargs):
        key = (kwargs["Bucket"], kwargs["Key"])
        self.put_calls.append(key)
        self.objects[key] = {
            "Body": kwargs["Body"], "Metadata": kwargs["Metadata"],
            "ETag": '"service-etag"', "VersionId": "v1",
        }
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_object(self, **kwargs):
        key = (kwargs["Bucket"], kwargs["Key"])
        if key not in self.objects:
            error = RuntimeError("NoSuchKey")
            error.response = {"Error": {"Code": "NoSuchKey"},
                              "ResponseMetadata": {"HTTPStatusCode": 404}}
            raise error
        item = self.objects[key]
        return {**item, "Body": self.Body(item["Body"])}


def test_service_recovers_local_intent_before_worker_loop_and_stops(tmp_path):
    clock_value = "2026-10-02T12:00:00+00:00"
    box = CanonicalDeliveryOutbox(
        tmp_path / "outbox.sqlite3", canonical_bucket="test-bucket",
        clock=lambda: clock_value,
    )
    payload = {"decision_id": "D-SERVICE", "symbol": "EURUSD"}
    local_path = tmp_path / "local" / "EURUSD" / "2026-10-02.jsonl"
    line = '{"decision_id":"D-SERVICE","symbol":"EURUSD"}'
    box.prepare_local_handoff(
        dataset="decision_ledger", payload=payload, symbol="EURUSD",
        partition_date="2026-10-02", local_path=local_path, local_line=line,
    )
    local_path.parent.mkdir(parents=True)
    local_path.write_text(line + "\n", encoding="utf-8")

    attempted = threading.Event()
    worker = StubWorker(box, first_call=lambda: attempted.set())
    service = CanonicalDeliveryService(
        worker=worker, poll_interval_seconds=0.01, batch_size=2,
        startup_recovery_limit=10, shutdown_timeout_seconds=2,
    )

    assert service.start() is True
    assert service.start() is False
    assert attempted.wait(2)
    assert len(box.records()) == 1
    assert service.status().startup_recovered_handoffs == 1
    assert service.stop() is True
    assert service.status().running is False
    box.close()


def test_worker_exception_is_observable_and_does_not_escape_service(tmp_path):
    box = CanonicalDeliveryOutbox(
        tmp_path / "outbox.sqlite3", canonical_bucket="test-bucket",
    )
    attempted = threading.Event()

    class FailingWorker(StubWorker):
        def run_once(self):
            self.calls += 1
            attempted.set()
            raise RuntimeError("expired credentials")

    service = CanonicalDeliveryService(
        worker=FailingWorker(box), poll_interval_seconds=0.01,
        shutdown_timeout_seconds=2,
    )
    assert service.start() is True
    assert attempted.wait(2)
    assert service.status().last_error == "RuntimeError:expired credentials"
    assert service.stop() is True
    box.close()


def test_runtime_restart_reuses_pending_durable_outbox_work(tmp_path):
    path = tmp_path / "outbox.sqlite3"
    box = CanonicalDeliveryOutbox(path, canonical_bucket="test-bucket")
    payload = {"decision_id": "D-PENDING", "symbol": "EURUSD"}
    box.enqueue(
        dataset="decision_ledger", payload=payload, symbol="EURUSD",
        partition_date="2026-10-02",
    )
    box.close()

    reloaded = CanonicalDeliveryOutbox(path, canonical_bucket="test-bucket")
    attempted = threading.Event()
    service = CanonicalDeliveryService(
        worker=StubWorker(reloaded, first_call=lambda: attempted.set()),
        poll_interval_seconds=0.01, shutdown_timeout_seconds=2,
    )
    assert service.start() is True
    assert attempted.wait(2)
    assert len(reloaded.records()) == 1
    assert service.stop() is True
    reloaded.close()


def test_managed_service_automatically_delivers_pending_row(tmp_path):
    class Body:
        def __init__(self, data):
            self.data = data

        def read(self):
            return self.data

    class S3:
        def __init__(self):
            self.objects = {}

        def put_object(self, **kwargs):
            key = (kwargs["Bucket"], kwargs["Key"])
            self.objects[key] = {
                "Body": kwargs["Body"], "Metadata": kwargs["Metadata"],
                "ETag": '"test-etag"', "VersionId": "v1",
            }
            return {"ETag": '"test-etag"',
                    "ResponseMetadata": {"HTTPStatusCode": 200}}

        def get_object(self, **kwargs):
            key = (kwargs["Bucket"], kwargs["Key"])
            if key not in self.objects:
                error = RuntimeError("NoSuchKey")
                error.response = {
                    "Error": {"Code": "NoSuchKey"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                }
                raise error
            item = self.objects[key]
            return {**item, "Body": Body(item["Body"])}

    box = CanonicalDeliveryOutbox(
        tmp_path / "outbox.sqlite3", canonical_bucket="test-bucket",
    )
    pending = box.enqueue(
        dataset="events",
        payload={"ts_utc_ms": 1790942400000, "type": "CANDLE",
                 "symbol": "EURUSD", "schema_version": "events_v1"},
        symbol="EURUSD", partition_date="2026-10-02",
    ).record
    acked = threading.Event()
    s3 = S3()

    class AckSignalingWorker(CanonicalDeliveryWorker):
        def run_once(self, **kwargs):
            result = super().run_once(**kwargs)
            if result is not None and result.ack_verified:
                acked.set()
            return result

    worker = AckSignalingWorker(box, s3_client=s3, owner_id="service-test")
    service = CanonicalDeliveryService(
        worker=worker, poll_interval_seconds=0.01, batch_size=2,
        shutdown_timeout_seconds=2,
    )

    assert service.start() is True
    try:
        assert acked.wait(2), service.status()
        assert box.get(pending.outbox_id).delivery_state is DeliveryState.ACKNOWLEDGED
        assert len(s3.objects) == 1
    finally:
        assert service.stop() is True
        box.close()


def test_empty_startup_recovery_is_harmless(tmp_path):
    box = CanonicalDeliveryOutbox(
        tmp_path / "outbox.sqlite3", canonical_bucket="test-bucket",
    )
    service = CanonicalDeliveryService(
        worker=StubWorker(box), poll_interval_seconds=0.01,
        shutdown_timeout_seconds=2,
    )
    assert service.start() is True
    assert service.status().startup_recovered_handoffs == 0
    assert service.stop() is True
    box.close()


def test_ack_only_service_repairs_lifecycle_after_outbox_and_ledger_restart(
    tmp_path, monkeypatch,
):
    path = tmp_path / "outbox.sqlite3"
    lifecycle_path = tmp_path / "lifecycle.jsonl"
    ledger = LifecycleEvidenceLedger(lifecycle_path)
    clock_value = "2026-10-02T12:00:00+00:00"
    obligation = create_dataset_obligation(
        ledger, event_id="decision:D-RESTART", lifecycle_stage="DECISION",
        dataset="decision_ledger",
        identity={"decision_id": "D-RESTART", "symbol": "EURUSD"},
        timestamp=clock_value, producer="test", trigger="DECISION_WRITTEN",
    )
    box = CanonicalDeliveryOutbox(
        path, canonical_bucket="test-bucket", clock=lambda: clock_value,
    )
    record = box.enqueue(
        dataset="decision_ledger",
        payload={"decision_id": "D-RESTART", "symbol": "EURUSD"},
        symbol="EURUSD", partition_date="2026-10-02",
        lifecycle_obligation_id=obligation.obligation_id,
    ).record
    s3 = ServiceS3()
    monkeypatch.setattr(
        ledger, "update", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk")),
    )
    failed_reconcile = CanonicalDeliveryWorker(
        box, s3_client=s3, lifecycle_ledger=ledger, owner_id="before-restart",
    ).run_once()
    assert failed_reconcile.state_after is DeliveryState.ACKNOWLEDGED
    assert box.get(record.outbox_id).reconciliation_state == "ANOMALY"
    assert ledger.get(obligation.obligation_id).current_status == ObligationStatus.NOT_YET_DUE.value
    put_count = len(s3.put_calls)
    box.close()

    reloaded_box = CanonicalDeliveryOutbox(
        path, canonical_bucket="test-bucket", clock=lambda: clock_value,
    )
    reloaded_ledger = LifecycleEvidenceLedger(lifecycle_path)
    repaired_event = threading.Event()

    class ObservedWorker(CanonicalDeliveryWorker):
        def reconcile_acknowledged(self, *, max_items=100):
            result = super().reconcile_acknowledged(max_items=max_items)
            if any(item.lifecycle_reconciled for item in result):
                repaired_event.set()
            return result

    service = CanonicalDeliveryService(
        worker=ObservedWorker(
            reloaded_box, s3_client=s3, lifecycle_ledger=reloaded_ledger,
            owner_id="after-restart",
        ),
        poll_interval_seconds=0.01, batch_size=2, shutdown_timeout_seconds=2,
    )
    assert service.start() is True
    try:
        assert repaired_event.wait(2), service.status()
        assert reloaded_box.get(record.outbox_id).delivery_state is DeliveryState.ACKNOWLEDGED
        assert reloaded_box.get(record.outbox_id).reconciliation_state == "RECONCILED"
        assert reloaded_ledger.get(obligation.obligation_id).current_status == ObligationStatus.PRESENT.value
        assert len(s3.put_calls) == put_count
    finally:
        assert service.stop() is True
        reloaded_box.close()


def test_ack_only_sweep_keeps_wrong_account_and_missing_obligation_anomalous(tmp_path):
    clock_value = "2026-10-02T12:00:00+00:00"
    path = tmp_path / "outbox.sqlite3"
    ledger = LifecycleEvidenceLedger(tmp_path / "lifecycle.jsonl")
    wrong_account = create_dataset_obligation(
        ledger, event_id="wrong-account", lifecycle_stage="ACCOUNT_EXECUTION_BRANCH",
        dataset="execution_results",
        identity={"correlation_id": "COR-X", "account_id": "B", "symbol": "EURUSD"},
        timestamp=clock_value, producer="test", trigger="ORDER_SEND",
    )
    box = CanonicalDeliveryOutbox(path, canonical_bucket="test-bucket",
                                  clock=lambda: clock_value)
    wrong_record = box.enqueue(
        dataset="execution_results",
        payload={"correlation_id": "COR-X", "account_id": "A", "symbol": "EURUSD"},
        symbol="EURUSD", partition_date="2026-10-02",
        lifecycle_obligation_id=wrong_account.obligation_id,
    ).record
    missing_record = box.enqueue(
        dataset="decision_ledger",
        payload={"decision_id": "D-NO-OBLIGATION", "symbol": "EURUSD"},
        symbol="EURUSD", partition_date="2026-10-02",
        lifecycle_obligation_id="EOB-NOT-FOUND",
    ).record
    s3 = ServiceS3()
    worker = CanonicalDeliveryWorker(box, s3_client=s3, lifecycle_ledger=ledger)
    assert len(worker.drain(max_items=2)) == 2
    puts = len(s3.put_calls)

    results = worker.reconcile_acknowledged(max_items=10)

    assert len(results) == 2
    assert box.get(wrong_record.outbox_id).delivery_state is DeliveryState.ACKNOWLEDGED
    assert box.get(missing_record.outbox_id).delivery_state is DeliveryState.ACKNOWLEDGED
    assert box.get(wrong_record.outbox_id).reconciliation_state == "ANOMALY"
    assert box.get(missing_record.outbox_id).reconciliation_state == "ANOMALY"
    assert ledger.get(wrong_account.obligation_id).current_status == ObligationStatus.NOT_YET_DUE.value
    assert len(s3.put_calls) == puts


def test_service_delivers_pending_record_while_ack_reconciliation_is_anomalous(
    tmp_path,
):
    clock_value = "2026-10-02T12:00:00+00:00"
    ledger = LifecycleEvidenceLedger(tmp_path / "lifecycle.jsonl")
    box = CanonicalDeliveryOutbox(
        tmp_path / "outbox.sqlite3", canonical_bucket="test-bucket",
        clock=lambda: clock_value,
    )
    acknowledged_anomaly = box.enqueue(
        dataset="decision_ledger",
        payload={"decision_id": "D-MISSING", "symbol": "EURUSD"},
        symbol="EURUSD", partition_date="2026-10-02",
        lifecycle_obligation_id="EOB-MISSING",
    ).record
    pending = box.enqueue(
        dataset="events",
        payload={"ts_utc_ms": 1790942400000, "type": "CANDLE",
                 "symbol": "EURUSD", "schema_version": "events_v1"},
        symbol="EURUSD", partition_date="2026-10-02",
    ).record
    s3 = ServiceS3()
    worker = CanonicalDeliveryWorker(box, s3_client=s3, lifecycle_ledger=ledger)
    service = CanonicalDeliveryService(
        worker=worker, poll_interval_seconds=0.01, batch_size=2,
        shutdown_timeout_seconds=2,
    )
    assert service.start() is True
    try:
        # Wait until the pending operational row reaches ACK; the anomalous ACK
        # stays in its own reconciliation path and never blocks delivery.
        delivered = threading.Event()
        original_run_once = worker.run_once

        def observe_delivery(*args, **kwargs):
            result = original_run_once(*args, **kwargs)
            if box.get(pending.outbox_id).delivery_state is DeliveryState.ACKNOWLEDGED:
                delivered.set()
            return result

        worker.run_once = observe_delivery
        assert delivered.wait(2)
        assert box.get(pending.outbox_id).delivery_state is DeliveryState.ACKNOWLEDGED
        assert box.get(acknowledged_anomaly.outbox_id).delivery_state is DeliveryState.ACKNOWLEDGED
        assert box.get(acknowledged_anomaly.outbox_id).reconciliation_state == "ANOMALY"
    finally:
        assert service.stop() is True
        box.close()


def test_expired_credentials_retry_durably_while_healthy_row_progresses(tmp_path):
    class ExpiredCredentialsS3(ServiceS3):
        def __init__(self, blocked_key):
            super().__init__()
            self.blocked_key = blocked_key

        def put_object(self, **kwargs):
            key = (kwargs["Bucket"], kwargs["Key"])
            if key == self.blocked_key:
                error = RuntimeError("ExpiredToken")
                error.response = {
                    "Error": {"Code": "ExpiredToken"},
                    "ResponseMetadata": {"HTTPStatusCode": 403},
                }
                raise error
            return super().put_object(**kwargs)

    box = CanonicalDeliveryOutbox(
        tmp_path / "outbox.sqlite3", canonical_bucket="test-bucket",
        # Equal timestamps exercise the documented outbox_id tie-break rather
        # than assuming enqueue order determines background delivery order.
        clock=lambda: "2026-10-02T12:00:00+00:00",
    )
    blocked = box.enqueue(
        dataset="events",
        payload={"ts_utc_ms": 1790942400010, "type": "CANDLE",
                 "symbol": "EURUSD", "schema_version": "events_v1"},
        symbol="EURUSD", partition_date="2026-10-02",
    ).record
    healthy = box.enqueue(
        dataset="events",
        payload={"ts_utc_ms": 1790942400020, "type": "CANDLE",
                 "symbol": "GBPUSD", "schema_version": "events_v1"},
        symbol="GBPUSD", partition_date="2026-10-02",
    ).record
    s3 = ExpiredCredentialsS3((blocked.canonical_bucket, blocked.canonical_key))
    worker = CanonicalDeliveryWorker(box, s3_client=s3, owner_id="service-auth-test")
    service = CanonicalDeliveryService(
        worker=worker, poll_interval_seconds=0.01, batch_size=2,
        shutdown_timeout_seconds=2,
    )
    delivered = threading.Event()
    original_run_once = worker.run_once

    def observe_healthy_delivery(*args, **kwargs):
        result = original_run_once(*args, **kwargs)
        if (box.get(healthy.outbox_id).delivery_state is DeliveryState.ACKNOWLEDGED
                and box.get(blocked.outbox_id).delivery_state is DeliveryState.RETRYABLE_FAILURE):
            delivered.set()
        return result

    worker.run_once = observe_healthy_delivery
    assert service.start() is True
    try:
        assert delivered.wait(2), service.status()
        assert box.get(blocked.outbox_id).delivery_state is DeliveryState.RETRYABLE_FAILURE
        assert box.get(blocked.outbox_id).next_retry_at is not None
        assert box.get(healthy.outbox_id).delivery_state is DeliveryState.ACKNOWLEDGED
        assert service.status().running is True
    finally:
        assert service.stop() is True
        box.close()


def test_process_service_entrypoint_suppresses_duplicate_loops(monkeypatch):
    import core.canonical_delivery_service as delivery_service

    instances = []

    class FakeService:
        running = True

        def __init__(self):
            instances.append(self)

        def start(self):
            return True

        def stop(self):
            self.running = False
            return True

    monkeypatch.setattr(delivery_service, "_SERVICE", None)
    monkeypatch.setattr(delivery_service, "CanonicalDeliveryService", FakeService)
    first = start_canonical_delivery_service()
    second = start_canonical_delivery_service()

    assert first is second
    assert len(instances) == 1
    assert stop_canonical_delivery_service() is True


def test_main_owns_live_delivery_startup_and_shutdown():
    from main import main

    source = inspect.getsource(main)
    assert "start_canonical_delivery_service" in source
    assert "stop_canonical_delivery_service" in source
    assert "if not config.REPLAY_MODE" in source
    assert "CANONICAL_DELIVERY_SERVICE_START_FAILED" in source


def test_startup_triage_parks_historical_backlog_and_reports_skipped(
    tmp_path, monkeypatch,
):
    import core.canonical_delivery_service as svc

    clock_value = "2026-10-02T12:00:00+00:00"
    box = CanonicalDeliveryOutbox(
        tmp_path / "outbox.sqlite3", canonical_bucket="test-bucket",
        clock=lambda: clock_value,
    )
    historical = box.enqueue(
        dataset="decision_ledger",
        payload={"decision_id": "D-HIST", "symbol": "EURUSD"},
        symbol="EURUSD", partition_date="2026-10-02",
    ).record
    s3 = ServiceS3()
    worker = CanonicalDeliveryWorker(box, s3_client=s3, owner_id="triage-test")
    # ACK the row, leaving the unlinked-anomaly reconciliation in place.
    assert worker.run_once().state_after is DeliveryState.ACKNOWLEDGED
    assert box.get(historical.outbox_id).reconciliation_state == "ANOMALY"

    monkeypatch.setattr(svc, "_utc_now", lambda: "2026-10-03T00:00:00+00:00")
    service = CanonicalDeliveryService(
        worker=worker, poll_interval_seconds=0.01, batch_size=2,
        shutdown_timeout_seconds=2,
    )
    assert service.start() is True
    try:
        triage = service.status().historical_triage
        assert triage.scanned == 1
        assert triage.skipped_terminal_historical == 1
        assert triage.deferred_to_active_pass == 0
        parked = box.get(historical.outbox_id)
        assert parked.reconciliation_state == "TERMINAL_HISTORICAL"
        # The parked historical row is never re-selected as active work.
        assert box.acknowledged_needing_reconciliation(limit=10) == ()
    finally:
        assert service.stop() is True
        box.close()
