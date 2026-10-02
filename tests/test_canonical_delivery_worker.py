from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from core.canonical_delivery_outbox import CanonicalDeliveryOutbox, DeliveryState
from core.canonical_delivery_worker import (
    CanonicalDeliveryWorker,
    InvalidDeliveryRecord,
    classify_delivery_failure,
)
from core.lifecycle_evidence_obligations import (
    DATASET_DISPOSITIONS,
    EXACT_IDENTITY_FIELDS,
    LifecycleEvidenceLedger,
    ObligationStatus,
    create_dataset_obligation,
)
from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY, is_symbol_scoped


DATE = "2026-10-02"


class FakeS3Error(Exception):
    def __init__(self, code: str, status: int, message: str | None = None):
        super().__init__(message or code)
        self.response = {
            "Error": {"Code": code, "Message": message or code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


class Body:
    def __init__(self, value: bytes):
        self.value = value

    def read(self):
        return self.value


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.failures: dict[str, list[Exception]] = {}
        self.put_calls = []
        self.get_calls = []

    def put_object(self, **kwargs):
        key = kwargs["Key"]
        self.put_calls.append(kwargs)
        failures = self.failures.get(key, [])
        if failures:
            raise failures.pop(0)
        object_key = (kwargs["Bucket"], key)
        if object_key in self.objects and kwargs.get("IfNoneMatch") == "*":
            raise FakeS3Error("PreconditionFailed", 412)
        body = kwargs["Body"]
        etag = '"' + hashlib.md5(body).hexdigest() + '"'
        self.objects[object_key] = {
            "Body": body,
            "Metadata": dict(kwargs.get("Metadata") or {}),
            "ETag": etag,
            "VersionId": "v1",
        }
        return {"ETag": etag, "VersionId": "v1",
                "ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_object(self, **kwargs):
        self.get_calls.append(kwargs)
        object_key = (kwargs["Bucket"], kwargs["Key"])
        if object_key not in self.objects:
            raise FakeS3Error("NoSuchKey", 404)
        value = self.objects[object_key]
        return {**value, "Body": Body(value["Body"])}

    def seed_exact(self, record):
        body = json.dumps(
            record.payload, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ).encode()
        metadata = CanonicalDeliveryWorker._metadata(record)
        self.objects[(record.canonical_bucket, record.canonical_key)] = {
            "Body": body, "Metadata": metadata, "ETag": '"seed"',
            "VersionId": "seed-v1",
        }


class Clock:
    def __init__(self, value="2026-10-02T12:00:00+00:00"):
        self.value = datetime.fromisoformat(value)

    def __call__(self):
        return self.value

    def iso(self):
        return self.value.isoformat()

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def outbox(tmp_path, clock, name="outbox.sqlite3"):
    return CanonicalDeliveryOutbox(
        tmp_path / name, canonical_bucket="test-bucket", clock=clock.iso,
    )


def enqueue(box, *, dataset="decision_ledger", suffix="1", account=None,
            lifecycle_obligation_id=None):
    identities = {
        "decision_ledger": {"decision_id": f"D-{suffix}"},
        "market_context": {"entity_id": f"E-{suffix}"},
        "execution_results": {"correlation_id": f"C-{suffix}", "account_id": account},
        "trade_truth": {"trade_id": f"T-{suffix}", "account_id": account},
        "protection_audit": {
            "correlation_id": f"C-{suffix}", "account_id": account,
            "position_ticket": int(suffix),
        },
        "shadow_runtime": {"event_id": f"S-{suffix}"},
        "events": {"ts_utc_ms": 1790942400000 + int(suffix),
                   "type": "CANDLE", "symbol": "EURUSD"},
    }
    identity = identities[dataset]
    payload = {**identity, "symbol": "EURUSD", "value": suffix}
    if dataset == "trade_truth":
        payload["identity"] = dict(identity)
    return box.enqueue(
        dataset=dataset, payload=payload, identity=identity,
        symbol="EURUSD", partition_date=DATE,
        lifecycle_obligation_id=lifecycle_obligation_id,
    ).record


def worker(box, s3, clock, **kwargs):
    owner_id = kwargs.pop("owner_id", "worker-test")
    return CanonicalDeliveryWorker(
        box, s3_client=s3, clock=clock, jitter=lambda: 0.0,
        base_retry_seconds=10, lease_seconds=30, owner_id=owner_id,
        **kwargs,
    )


def assert_delivery_invariants(box, s3, ledger=None):
    records = box.records()
    ids = [record.idempotency_key for record in records]
    assert len(ids) == len(set(ids)), "duplicate logical outbox identity"

    for record in records:
        if record.delivery_state is not DeliveryState.ACKNOWLEDGED:
            continue
        assert record.canonical_ack is not None, ("ACK without evidence", record.outbox_id)
        object_key = (record.canonical_bucket, record.canonical_key)
        assert object_key in s3.objects, ("ACK without canonical object", record.outbox_id)
        stored = s3.objects[object_key]
        expected_body = json.dumps(
            record.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        assert stored["Body"] == expected_body, ("ACK body mismatch", record.outbox_id)
        assert hashlib.sha256(stored["Body"]).hexdigest() == record.payload_sha256
        assert stored["Metadata"] == CanonicalDeliveryWorker._metadata(record)
        ack = record.canonical_ack
        assert ack["payload_sha256"] == record.payload_sha256
        assert ack["verified_payload_sha256"] == record.payload_sha256
        assert ack["canonical_key"] == record.canonical_key
        assert ack["canonical_bucket"] == record.canonical_bucket
        if ack["etag_available"]:
            assert ack.get("etag")
        else:
            assert ack.get("etag") in (None, "")
            assert ack.get("etag_observation") == "NOT_RETURNED_BY_S3"

        if ledger is not None and record.lifecycle_obligation_id \
                and record.reconciliation_state == "RECONCILED":
            obligation = ledger.get(record.lifecycle_obligation_id)
            assert obligation is not None
            assert obligation.current_status == ObligationStatus.PRESENT.value
            assert obligation.expected_dataset == record.dataset
            assert dict(obligation.expected_identity) == dict(record.record_identity)

    if ledger is not None:
        for obligation in ledger.obligations():
            if obligation.current_status != ObligationStatus.PRESENT.value \
                    or obligation.provenance.get("canonical_delivery") != "ACKNOWLEDGED":
                continue
            linked = [record for record in records
                      if record.outbox_id == obligation.observed_record_id]
            assert len(linked) == 1, ("PRESENT without unique outbox ACK", obligation.obligation_id)
            assert linked[0].delivery_state is DeliveryState.ACKNOWLEDGED
            assert linked[0].dataset == obligation.expected_dataset
            assert dict(linked[0].record_identity) == dict(obligation.expected_identity)

    return {
        "records": len(records),
        "acknowledged": sum(record.delivery_state is DeliveryState.ACKNOWLEDGED
                             for record in records),
        "conflicts": sum(record.delivery_state is DeliveryState.CONFLICT
                          for record in records),
        "reconciled": sum(record.reconciliation_state == "RECONCILED"
                           for record in records),
    }


def test_pending_success_is_verified_and_acknowledged(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock)
    record = enqueue(box)
    s3 = FakeS3()
    result = worker(box, s3, clock).run_once()
    current = box.get(record.outbox_id)
    assert result.ack_verified is True
    assert result.state_after is DeliveryState.ACKNOWLEDGED
    assert current.delivery_state is DeliveryState.ACKNOWLEDGED
    assert current.canonical_ack["verification_method"] == "GET_BODY_SHA256"
    assert current.canonical_ack["attempt_number"] == 1
    assert s3.put_calls[0]["IfNoneMatch"] == "*"


def test_missing_verification_etag_is_recorded_without_weakening_exact_ack(tmp_path):
    class NoVerificationETagS3(FakeS3):
        def get_object(self, **kwargs):
            response = super().get_object(**kwargs)
            response.pop("ETag", None)
            return response

    clock = Clock()
    box = outbox(tmp_path, clock)
    record = enqueue(box)
    s3 = NoVerificationETagS3()

    result = worker(box, s3, clock).run_once()
    current = box.get(record.outbox_id)

    assert result.ack_verified is True
    assert current.delivery_state is DeliveryState.ACKNOWLEDGED
    assert current.canonical_ack["etag"] is None
    assert current.canonical_ack["etag_available"] is False
    assert current.canonical_ack["etag_observation"] == "NOT_RETURNED_BY_S3"
    assert current.canonical_ack["verified_payload_sha256"] == record.payload_sha256
    assert current.canonical_ack["verification_method"] == "GET_BODY_SHA256"


def test_mismatched_etag_is_informational_when_exact_body_hash_and_metadata_match(tmp_path):
    class MismatchedETagS3(FakeS3):
        def get_object(self, **kwargs):
            response = super().get_object(**kwargs)
            response["ETag"] = '"different-opaque-etag"'
            return response

    clock = Clock()
    box = outbox(tmp_path, clock)
    record = enqueue(box)
    result = worker(box, MismatchedETagS3(), clock).run_once()

    assert result.state_after is DeliveryState.ACKNOWLEDGED
    ack = box.get(record.outbox_id).canonical_ack
    assert ack["etag"] == '"different-opaque-etag"'
    assert ack["verified_payload_sha256"] == record.payload_sha256
    assert ack["verification_method"] == "GET_BODY_SHA256"


def test_ack_reconciles_exact_lifecycle_obligation_and_is_idempotent(tmp_path):
    clock = Clock()
    ledger = LifecycleEvidenceLedger(tmp_path / "lifecycle.jsonl")
    obligation = create_dataset_obligation(
        ledger, event_id="decision:D-1", lifecycle_stage="DECISION",
        dataset="decision_ledger",
        identity={"decision_id": "D-1", "symbol": "EURUSD"},
        timestamp=clock.iso(), producer="test", trigger="DECISION_WRITTEN",
    )
    box = outbox(tmp_path, clock)
    record = enqueue(box, lifecycle_obligation_id=obligation.obligation_id)
    s3 = FakeS3()
    executor = worker(box, s3, clock, lifecycle_ledger=ledger)
    result = executor.run_once()
    assert result.lifecycle_reconciled is True
    present = ledger.get(obligation.obligation_id)
    assert present.current_status == ObligationStatus.PRESENT.value
    revision = present.revision
    assert executor._reconcile_lifecycle(box.get(record.outbox_id)) == (True, None)
    assert ledger.get(obligation.obligation_id).revision == revision


@pytest.mark.parametrize("exc", [
    TimeoutError("timeout"),
    FakeS3Error("InternalError", 500),
    FakeS3Error("SlowDown", 503),
    FakeS3Error("ExpiredToken", 403),
])
def test_transient_failures_schedule_durable_retry_then_recover(tmp_path, exc):
    clock = Clock()
    box = outbox(tmp_path, clock)
    record = enqueue(box)
    s3 = FakeS3()
    s3.failures[record.canonical_key] = [exc]
    executor = worker(box, s3, clock)
    failed = executor.run_once()
    durable = box.get(record.outbox_id)
    assert failed.state_after is DeliveryState.RETRYABLE_FAILURE
    assert failed.retry_scheduled is True
    assert durable.next_retry_at == "2026-10-02T12:00:10+00:00"
    assert durable.last_error_class is not None
    clock.advance(11)
    recovered = executor.run_once()
    assert recovered.state_after is DeliveryState.ACKNOWLEDGED


def test_access_denied_is_terminal_and_inspectable(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock)
    record = enqueue(box)
    s3 = FakeS3()
    s3.failures[record.canonical_key] = [FakeS3Error("AccessDenied", 403)]
    result = worker(box, s3, clock).run_once()
    current = box.get(record.outbox_id)
    assert result.state_after is DeliveryState.TERMINAL_FAILURE
    assert current.last_error_class == "AccessDenied"
    assert current.payload_sha256 == record.payload_sha256


def test_invalid_delivery_record_is_terminal_before_put(tmp_path, monkeypatch):
    clock = Clock()
    box = outbox(tmp_path, clock)
    enqueue(box)
    s3 = FakeS3()
    executor = worker(box, s3, clock)
    monkeypatch.setattr(
        executor, "_validate_record",
        lambda *_: (_ for _ in ()).throw(InvalidDeliveryRecord("invalid")),
    )
    result = executor.run_once()
    assert result.state_after is DeliveryState.TERMINAL_FAILURE
    assert s3.put_calls == []


def test_existing_exact_object_is_orphan_ack_recovery_without_put(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock)
    record = enqueue(box)
    s3 = FakeS3()
    s3.seed_exact(record)
    result = worker(box, s3, clock).run_once()
    assert result.orphan_ack_recovered is True
    assert result.state_after is DeliveryState.ACKNOWLEDGED
    assert s3.put_calls == []


def test_existing_different_object_is_conflict_and_never_overwritten(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock)
    record = enqueue(box)
    s3 = FakeS3()
    key = (record.canonical_bucket, record.canonical_key)
    s3.objects[key] = {"Body": b'{"different":true}', "Metadata": {},
                       "ETag": '"different"'}
    before = dict(s3.objects[key])
    result = worker(box, s3, clock).run_once()
    assert result.state_after is DeliveryState.CONFLICT
    assert s3.objects[key] == before
    assert s3.put_calls == []


@pytest.mark.parametrize("attack", [
    "missing_metadata", "wrong_outbox_id", "wrong_payload_hash",
    "wrong_dataset", "wrong_body",
])
def test_existing_object_metadata_and_body_attacks_conflict_without_overwrite(
    tmp_path, attack,
):
    clock = Clock()
    box = outbox(tmp_path, clock)
    record = enqueue(box)
    s3 = FakeS3()
    s3.seed_exact(record)
    object_key = (record.canonical_bucket, record.canonical_key)
    existing = s3.objects[object_key]
    if attack == "wrong_body":
        existing["Body"] = b'{"decision_id":"ATTACK"}'
    else:
        metadata = dict(existing["Metadata"])
        if attack == "missing_metadata":
            metadata.pop("payload-sha256")
        elif attack == "wrong_outbox_id":
            metadata["outbox-id"] = "outbox-other"
        elif attack == "wrong_dataset":
            metadata["dataset"] = "events"
        else:
            metadata["payload-sha256"] = "0" * 64
        existing["Metadata"] = metadata
    preserved = {**existing, "Metadata": dict(existing["Metadata"])}

    result = worker(box, s3, clock).run_once()

    assert result.state_after is DeliveryState.CONFLICT
    assert s3.objects[object_key] == preserved
    assert s3.put_calls == []


def test_create_only_race_with_exact_object_is_verified_as_success(tmp_path):
    class ConcurrentCreateS3(FakeS3):
        def put_object(self, **kwargs):
            self.put_calls.append(kwargs)
            object_key = (kwargs["Bucket"], kwargs["Key"])
            self.objects[object_key] = {
                "Body": kwargs["Body"], "Metadata": kwargs["Metadata"],
                "ETag": '"raced"', "VersionId": "race-v1",
            }
            raise FakeS3Error("PreconditionFailed", 412)

    clock = Clock()
    box = outbox(tmp_path, clock)
    enqueue(box)
    result = worker(box, ConcurrentCreateS3(), clock).run_once()
    assert result.state_after is DeliveryState.ACKNOWLEDGED
    assert result.orphan_ack_recovered is True


def test_post_put_verification_failure_retries_then_repairs_orphan(tmp_path):
    class VerificationFailureS3(FakeS3):
        def get_object(self, **kwargs):
            object_key = (kwargs["Bucket"], kwargs["Key"])
            if object_key in self.objects and not getattr(self, "failed_verify", False):
                self.failed_verify = True
                raise FakeS3Error("InternalError", 500)
            return super().get_object(**kwargs)

    clock = Clock()
    box = outbox(tmp_path, clock)
    enqueue(box)
    s3 = VerificationFailureS3()
    executor = worker(box, s3, clock)
    assert executor.run_once().state_after is DeliveryState.RETRYABLE_FAILURE
    clock.advance(11)
    repaired = executor.run_once()
    assert repaired.state_after is DeliveryState.ACKNOWLEDGED
    assert repaired.orphan_ack_recovered is True
    assert len(s3.put_calls) == 1


def test_put_object_temporarily_invisible_to_get_retries_without_duplicate_put(tmp_path):
    class DelayedVisibilityS3(FakeS3):
        def get_object(self, **kwargs):
            if getattr(self, "hide_first_read", False):
                self.hide_first_read = False
                raise FakeS3Error("NoSuchKey", 404, "temporarily not visible")
            return super().get_object(**kwargs)

        def put_object(self, **kwargs):
            result = super().put_object(**kwargs)
            self.hide_first_read = True
            return result

    clock = Clock()
    box = outbox(tmp_path, clock)
    record = enqueue(box)
    s3 = DelayedVisibilityS3()
    executor = worker(box, s3, clock)

    first = executor.run_once()
    assert first.state_after is DeliveryState.RETRYABLE_FAILURE
    assert len(s3.objects) == 1
    clock.advance(10)
    recovered = executor.run_once()

    assert recovered.state_after is DeliveryState.ACKNOWLEDGED
    assert recovered.orphan_ack_recovered is True
    assert len(s3.put_calls) == 1
    assert box.get(record.outbox_id).canonical_ack["verified_payload_sha256"] == record.payload_sha256


def test_put_success_crash_then_restart_recovers_lost_ack(tmp_path):
    class CrashAfterPut(BaseException):
        pass

    clock = Clock()
    path = tmp_path / "lost-ack.sqlite3"
    box = CanonicalDeliveryOutbox(path, canonical_bucket="test-bucket", clock=clock.iso)
    record = enqueue(box)
    s3 = FakeS3()
    crashing = worker(
        box, s3, clock,
        after_put_hook=lambda _record: (_ for _ in ()).throw(CrashAfterPut()),
    )
    with pytest.raises(CrashAfterPut):
        crashing.run_once()
    assert box.get(record.outbox_id).delivery_state is DeliveryState.IN_FLIGHT
    assert len(s3.objects) == 1
    box.close()

    clock.advance(31)
    restarted_box = CanonicalDeliveryOutbox(
        path, canonical_bucket="test-bucket", clock=clock.iso,
    )
    result = worker(restarted_box, s3, clock).run_once()
    assert result.expired_lease_reclaimed is True
    assert result.orphan_ack_recovered is True
    assert result.state_after is DeliveryState.ACKNOWLEDGED
    assert len(s3.objects) == 1
    restarted_box.close()


def test_put_committed_but_response_lost_recovers_without_duplicate_object(tmp_path):
    class CommitThenTimeoutS3(FakeS3):
        def put_object(self, **kwargs):
            super().put_object(**kwargs)
            raise TimeoutError("response lost after S3 commit")

    clock = Clock()
    box = outbox(tmp_path, clock)
    record = enqueue(box)
    s3 = CommitThenTimeoutS3()
    executor = worker(box, s3, clock)

    failed = executor.run_once()
    assert failed.state_after is DeliveryState.RETRYABLE_FAILURE
    assert len(s3.objects) == 1
    clock.advance(11)
    recovered = executor.run_once()

    assert recovered.state_after is DeliveryState.ACKNOWLEDGED
    assert recovered.orphan_ack_recovered is True
    assert len(s3.objects) == 1
    assert len(s3.put_calls) == 1


def test_local_fsync_crash_before_enqueue_recovers_and_reconciles_after_ack(
    tmp_path, monkeypatch,
):
    from core.canonical_delivery import (
        configure_delivery_outbox, get_delivery_outbox, recover_local_handoffs,
    )
    from core.decision_ledger import DecisionLedgerWriter, DecisionOutcome
    from core.runtime.decision_recorder import DecisionRecorder

    class CrashBeforeEnqueue(BaseException):
        pass

    path = tmp_path / "recovery.sqlite3"
    local_dir = tmp_path / "decision-ledger"
    lifecycle = LifecycleEvidenceLedger(tmp_path / "lifecycle.jsonl")
    configure_delivery_outbox(path)
    writer = DecisionLedgerWriter(local_dir=str(local_dir), flush_batch_size=1)
    recorder = DecisionRecorder(writer)
    decision = recorder.init_cycle(
        symbol="EURUSD", cycle_id=18, regime="RANGE",
        context_snapshot_id="COR-18", drawdown_pct=0.0, daily_loss_pct=0.0,
        decision_id="D-LOCAL-CRASH",
    )
    decision.update({"decision": DecisionOutcome.NO_TRADE, "reason": "crash_fixture"})
    create_terminal = __import__(
        "core.lifecycle_evidence_obligations", fromlist=["create_terminal_decision_obligations"],
    ).create_terminal_decision_obligations
    create_terminal(
        lifecycle, event_id="decision:D-LOCAL-CRASH",
        identity={"symbol": "EURUSD", "cycle_id": 18,
                  "decision_id": "D-LOCAL-CRASH", "correlation_id": "COR-18",
                  "entity_id": "E-18"},
        timestamp="2026-10-02T12:00:00+00:00", no_trade=True,
    )
    monkeypatch.setattr(
        "core.lifecycle_evidence_obligations.obligation_ledger",
        lambda path=None: lifecycle,
    )
    original_enqueue_batch = __import__(
        "core.canonical_delivery", fromlist=["enqueue_canonical_batch"],
    ).enqueue_canonical_batch
    monkeypatch.setattr(
        "core.canonical_delivery.enqueue_canonical_batch",
        lambda **_kwargs: (_ for _ in ()).throw(CrashBeforeEnqueue()),
    )

    with pytest.raises(CrashBeforeEnqueue):
        recorder.finalize(cycle_start=1.0)
    local_files = list(local_dir.rglob("*.jsonl"))
    assert len(local_files) == 1
    assert len(local_files[0].read_text(encoding="utf-8").splitlines()) == 1
    assert get_delivery_outbox().records() == ()
    linked, = [item for item in get_delivery_outbox().pending_local_handoffs()
               if item.dataset == "decision_ledger"]
    obligation, = lifecycle.find_exact(
        "decision_ledger", {"decision_id": "D-LOCAL-CRASH"},
    )
    assert linked.lifecycle_obligation_id == obligation.obligation_id
    assert obligation.current_status == ObligationStatus.NOT_YET_DUE.value

    monkeypatch.setattr("core.canonical_delivery.enqueue_canonical_batch", original_enqueue_batch)
    configure_delivery_outbox(path)
    recovered_box = get_delivery_outbox()
    recovery = recover_local_handoffs(outbox=recovered_box, max_items=10)
    assert recovery.recovered == 1
    assert len(recovered_box.records()) == 1
    s3 = FakeS3()
    result, = worker(recovered_box, s3, Clock(), lifecycle_ledger=lifecycle).drain(max_items=1)
    assert result.state_after is DeliveryState.ACKNOWLEDGED
    assert lifecycle.get(obligation.obligation_id).current_status == ObligationStatus.PRESENT.value
    assert len(s3.objects) == 1
    assert len(local_files[0].read_text(encoding="utf-8").splitlines()) == 1
    recovered_box.close()
    configure_delivery_outbox(None)


def test_crash_after_exact_verification_before_local_ack_recovers(tmp_path, monkeypatch):
    class CrashBeforeAck(BaseException):
        pass

    clock = Clock()
    path = tmp_path / "verified-before-ack.sqlite3"
    box = CanonicalDeliveryOutbox(path, canonical_bucket="test-bucket", clock=clock.iso)
    record = enqueue(box)
    s3 = FakeS3()
    monkeypatch.setattr(
        box, "acknowledge",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CrashBeforeAck()),
    )
    with pytest.raises(CrashBeforeAck):
        worker(box, s3, clock).run_once()
    assert box.get(record.outbox_id).delivery_state is DeliveryState.IN_FLIGHT
    assert len(s3.objects) == 1
    box.close()

    clock.advance(30)
    restarted = CanonicalDeliveryOutbox(path, canonical_bucket="test-bucket", clock=clock.iso)
    result = worker(restarted, s3, clock).run_once()
    assert result.expired_lease_reclaimed is True
    assert result.orphan_ack_recovered is True
    assert restarted.get(record.outbox_id).delivery_state is DeliveryState.ACKNOWLEDGED
    assert len(s3.objects) == 1


def test_crash_after_ack_before_lifecycle_reconciliation_repairs_on_restart(
    tmp_path, monkeypatch,
):
    class CrashAfterAck(BaseException):
        pass

    clock = Clock()
    path = tmp_path / "ack-before-reconcile.sqlite3"
    ledger_path = tmp_path / "lifecycle.jsonl"
    ledger = LifecycleEvidenceLedger(ledger_path)
    obligation = create_dataset_obligation(
        ledger, event_id="decision:D-71", lifecycle_stage="DECISION",
        dataset="decision_ledger", identity={"decision_id": "D-71", "symbol": "EURUSD"},
        timestamp=clock.iso(), producer="test", trigger="DECISION_WRITTEN",
    )
    box = CanonicalDeliveryOutbox(path, canonical_bucket="test-bucket", clock=clock.iso)
    record = enqueue(box, suffix="71", lifecycle_obligation_id=obligation.obligation_id)
    s3 = FakeS3()
    first = worker(box, s3, clock, lifecycle_ledger=ledger)
    monkeypatch.setattr(
        first, "_safe_reconcile_lifecycle",
        lambda *_args: (_ for _ in ()).throw(CrashAfterAck()),
    )
    with pytest.raises(CrashAfterAck):
        first.run_once()
    assert box.get(record.outbox_id).delivery_state is DeliveryState.ACKNOWLEDGED
    assert ledger.get(obligation.obligation_id).current_status == ObligationStatus.NOT_YET_DUE.value
    box.close()

    restarted = CanonicalDeliveryOutbox(path, canonical_bucket="test-bucket", clock=clock.iso)
    recovered_ledger = LifecycleEvidenceLedger(ledger_path)
    result, = worker(restarted, s3, clock, lifecycle_ledger=recovered_ledger).reconciliation_sweep(
        max_items=1)
    assert result.state_after is DeliveryState.ACKNOWLEDGED
    assert result.lifecycle_reconciled is True
    assert recovered_ledger.get(obligation.obligation_id).current_status == ObligationStatus.PRESENT.value
    assert len(s3.objects) == 1


def test_crash_after_lifecycle_present_before_reconciliation_commit_is_idempotent(
    tmp_path, monkeypatch,
):
    class CrashBeforeReconciliationCommit(BaseException):
        pass

    clock = Clock()
    path = tmp_path / "present-before-reconcile-commit.sqlite3"
    ledger_path = tmp_path / "lifecycle.jsonl"
    ledger = LifecycleEvidenceLedger(ledger_path)
    obligation = create_dataset_obligation(
        ledger, event_id="decision:D-72", lifecycle_stage="DECISION",
        dataset="decision_ledger", identity={"decision_id": "D-72", "symbol": "EURUSD"},
        timestamp=clock.iso(), producer="test", trigger="DECISION_WRITTEN",
    )
    box = CanonicalDeliveryOutbox(path, canonical_bucket="test-bucket", clock=clock.iso)
    record = enqueue(box, suffix="72", lifecycle_obligation_id=obligation.obligation_id)
    s3 = FakeS3()
    original_record_reconciliation = box.record_reconciliation

    def crash_before_commit(outbox_id, *, state, error=None):
        if state == "RECONCILED":
            raise CrashBeforeReconciliationCommit()
        return original_record_reconciliation(outbox_id, state=state, error=error)

    monkeypatch.setattr(box, "record_reconciliation", crash_before_commit)
    with pytest.raises(CrashBeforeReconciliationCommit):
        worker(box, s3, clock, lifecycle_ledger=ledger).run_once()
    assert box.get(record.outbox_id).delivery_state is DeliveryState.ACKNOWLEDGED
    assert ledger.get(obligation.obligation_id).current_status == ObligationStatus.PRESENT.value
    box.close()

    restarted = CanonicalDeliveryOutbox(path, canonical_bucket="test-bucket", clock=clock.iso)
    recovered_ledger = LifecycleEvidenceLedger(ledger_path)
    result, = worker(restarted, s3, clock, lifecycle_ledger=recovered_ledger).reconciliation_sweep(
        max_items=1)
    assert result.lifecycle_reconciled is True
    assert restarted.get(record.outbox_id).delivery_state is DeliveryState.ACKNOWLEDGED
    assert restarted.get(record.outbox_id).reconciliation_state == "RECONCILED"
    assert recovered_ledger.get(obligation.obligation_id).revision == 2
    assert len(s3.objects) == 1
    restarted.close()


def test_active_lease_is_not_stolen_then_expired_lease_is_reclaimed(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock)
    enqueue(box)
    claimed = box.claim_next_result(
        now=clock.iso(), owner_id="worker-A", lease_seconds=30,
    )
    assert claimed.record.claim_owner == "worker-A"
    assert worker(box, FakeS3(), clock, owner_id="worker-B").run_once() is None
    clock.advance(30)
    result = worker(box, FakeS3(), clock, owner_id="worker-B").run_once()
    assert result.expired_lease_reclaimed is True
    assert result.state_after is DeliveryState.ACKNOWLEDGED


def test_retry_is_claimable_at_exact_next_retry_boundary(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock)
    record = enqueue(box)
    s3 = FakeS3()
    s3.failures[record.canonical_key] = [TimeoutError("retry boundary")]
    executor = worker(box, s3, clock)

    failed = executor.run_once()
    assert failed.state_after is DeliveryState.RETRYABLE_FAILURE
    assert box.get(record.outbox_id).next_retry_at == "2026-10-02T12:00:10+00:00"
    clock.advance(10)

    recovered = executor.run_once()

    assert recovered.state_after is DeliveryState.ACKNOWLEDGED


def test_two_workers_claim_independent_records_without_stealing(tmp_path):
    clock = Clock()
    path = tmp_path / "two-workers.sqlite3"
    worker_a_box = CanonicalDeliveryOutbox(
        path, canonical_bucket="test-bucket", clock=clock.iso)
    first = enqueue(worker_a_box, suffix="81")
    second = enqueue(worker_a_box, suffix="82")
    worker_b_box = CanonicalDeliveryOutbox(
        path, canonical_bucket="test-bucket", clock=clock.iso)

    claim_a = worker_a_box.claim_next_result(
        now=clock.iso(), owner_id="worker-A", lease_seconds=30)
    claim_b = worker_b_box.claim_next_result(
        now=clock.iso(), owner_id="worker-B", lease_seconds=30)

    assert claim_a is not None and claim_b is not None
    assert claim_a.record.outbox_id != claim_b.record.outbox_id
    assert {claim_a.record.outbox_id, claim_b.record.outbox_id} == {
        first.outbox_id, second.outbox_id,
    }
    assert {claim_a.record.claim_owner, claim_b.record.claim_owner} == {
        "worker-A", "worker-B",
    }


def test_500_record_backlog_preserves_fairness_and_terminal_states(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock, "backlog.sqlite3")
    records = [enqueue(box, suffix=str(index)) for index in range(1, 501)]
    s3 = FakeS3()
    timeout = records[0]
    denied = records[1]
    conflict = records[2]
    s3.failures[timeout.canonical_key] = [TimeoutError("temporary outage")]
    s3.failures[denied.canonical_key] = [FakeS3Error("AccessDenied", 403)]
    conflict_object_key = (conflict.canonical_bucket, conflict.canonical_key)
    s3.objects[conflict_object_key] = {
        "Body": b'{"payload":"not-the-record"}',
        "Metadata": {}, "ETag": '"conflict"',
    }

    results = worker(box, s3, clock).drain(max_items=500)
    states = [result.state_after for result in results]

    assert len(results) == 500
    assert states.count(DeliveryState.RETRYABLE_FAILURE) == 1
    assert states.count(DeliveryState.TERMINAL_FAILURE) == 1
    assert states.count(DeliveryState.CONFLICT) == 1
    assert states.count(DeliveryState.ACKNOWLEDGED) == 497
    assert box.get(timeout.outbox_id).next_retry_at is not None
    assert box.get(denied.outbox_id).next_retry_at is None
    assert box.get(conflict.outbox_id).delivery_state is DeliveryState.CONFLICT
    assert s3.objects[conflict_object_key]["Body"] == b'{"payload":"not-the-record"}'
    status = box.status()
    assert status.acknowledged_count == 497
    assert status.retryable_failure_count == 1
    assert status.terminal_failure_count == 1
    assert status.conflict_count == 1


def test_max_attempts_transitions_to_terminal(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock)
    record = enqueue(box)
    s3 = FakeS3()
    s3.failures[record.canonical_key] = [TimeoutError("one"), TimeoutError("two")]
    executor = worker(box, s3, clock, max_attempts=2)
    assert executor.run_once().state_after is DeliveryState.RETRYABLE_FAILURE
    clock.advance(11)
    result = executor.run_once()
    assert result.state_after is DeliveryState.TERMINAL_FAILURE
    assert "MAX_ATTEMPTS_EXHAUSTED" in box.get(record.outbox_id).last_error


def test_account_a_success_b_failure_are_independent_and_b_later_recovers(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock)
    a = enqueue(box, dataset="execution_results", suffix="1", account="A")
    b = enqueue(box, dataset="execution_results", suffix="1", account="B")
    s3 = FakeS3()
    s3.failures[b.canonical_key] = [TimeoutError("B unavailable")]
    executor = worker(box, s3, clock)
    executor.drain(max_items=2)
    assert box.get(a.outbox_id).delivery_state is DeliveryState.ACKNOWLEDGED
    assert box.get(b.outbox_id).delivery_state is DeliveryState.RETRYABLE_FAILURE
    assert a.canonical_key != b.canonical_key
    clock.advance(11)
    assert executor.run_once().state_after is DeliveryState.ACKNOWLEDGED


def test_account_a_object_cannot_satisfy_account_b_delivery(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock)
    account_a = enqueue(box, dataset="execution_results", suffix="7", account="A")
    account_b = enqueue(box, dataset="execution_results", suffix="7", account="B")
    s3 = FakeS3()
    executor = worker(box, s3, clock)

    assert executor.run_once(
        exclude_outbox_ids=(account_b.outbox_id,),
    ).state_after is DeliveryState.ACKNOWLEDGED
    a_object = s3.objects[(account_a.canonical_bucket, account_a.canonical_key)]
    b_destination = (account_b.canonical_bucket, account_b.canonical_key)
    s3.objects[b_destination] = dict(a_object)
    preserved = dict(s3.objects[b_destination])

    result = executor.run_once(exclude_outbox_ids=(account_a.outbox_id,))

    assert result.state_after is DeliveryState.CONFLICT
    assert box.get(account_a.outbox_id).delivery_state is DeliveryState.ACKNOWLEDGED
    assert box.get(account_b.outbox_id).delivery_state is DeliveryState.CONFLICT
    assert s3.objects[b_destination] == preserved


def test_acknowledged_row_is_not_redelivered(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock)
    enqueue(box)
    s3 = FakeS3()
    executor = worker(box, s3, clock)
    assert executor.run_once().ack_verified
    calls = len(s3.put_calls)
    assert executor.run_once() is None
    assert len(s3.put_calls) == calls


def test_missing_linked_obligation_records_durable_anomaly(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock)
    record = enqueue(box, lifecycle_obligation_id="EOB-MISSING")
    ledger = LifecycleEvidenceLedger(tmp_path / "empty-ledger.jsonl")
    result = worker(box, FakeS3(), clock, lifecycle_ledger=ledger).run_once()
    current = box.get(record.outbox_id)
    assert result.state_after is DeliveryState.ACKNOWLEDGED
    assert result.reconciliation_anomaly == "LINKED_LIFECYCLE_OBLIGATION_NOT_FOUND"
    assert current.reconciliation_state == "ANOMALY"


def test_live_ack_without_obligation_link_records_anomaly(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock)
    record = enqueue(box, lifecycle_obligation_id=None)

    result = worker(box, FakeS3(), clock).run_once()

    current = box.get(record.outbox_id)
    assert current.delivery_state is DeliveryState.ACKNOWLEDGED
    assert result.reconciliation_anomaly == "LIFECYCLE_OBLIGATION_NOT_LINKED"
    assert current.reconciliation_state == "ANOMALY"


def test_lifecycle_storage_failure_does_not_undo_verified_ack(tmp_path, monkeypatch):
    clock = Clock()
    ledger = LifecycleEvidenceLedger(tmp_path / "lifecycle-failure.jsonl")
    obligation = create_dataset_obligation(
        ledger, event_id="decision:D-1", lifecycle_stage="DECISION",
        dataset="decision_ledger",
        identity={"decision_id": "D-1", "symbol": "EURUSD"},
        timestamp=clock.iso(), producer="test", trigger="DECISION_WRITTEN",
    )
    box = outbox(tmp_path, clock)
    record = enqueue(box, lifecycle_obligation_id=obligation.obligation_id)
    s3 = FakeS3()
    monkeypatch.setattr(
        ledger, "update", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk")),
    )
    executor = worker(box, s3, clock, lifecycle_ledger=ledger)
    result = executor.run_once()
    current = box.get(record.outbox_id)
    assert result.state_after is DeliveryState.ACKNOWLEDGED
    assert result.reconciliation_anomaly == "LIFECYCLE_RECONCILIATION_FAILED:OSError"
    assert current.reconciliation_state == "ANOMALY"
    assert ledger.get(obligation.obligation_id).current_status == ObligationStatus.NOT_YET_DUE.value

    box.close()
    box = CanonicalDeliveryOutbox(
        tmp_path / "outbox.sqlite3", canonical_bucket="test-bucket", clock=clock.iso,
    )
    ledger = LifecycleEvidenceLedger(ledger.path)
    executor = worker(box, s3, clock, lifecycle_ledger=ledger)
    monkeypatch.setattr(
        ledger, "update",
        LifecycleEvidenceLedger.update.__get__(ledger, LifecycleEvidenceLedger),
    )
    swept, = executor.reconciliation_sweep(max_items=1)
    repaired = box.get(record.outbox_id)
    assert swept.state_before is DeliveryState.ACKNOWLEDGED
    assert swept.state_after is DeliveryState.ACKNOWLEDGED
    assert swept.lifecycle_reconciled is True
    assert repaired.delivery_state is DeliveryState.ACKNOWLEDGED
    assert repaired.reconciliation_state == "RECONCILED"
    assert ledger.get(obligation.obligation_id).current_status == ObligationStatus.PRESENT.value


def test_reconciliation_sweep_repairs_missing_obligation_after_ack(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock)
    obligation_id = LifecycleEvidenceLedger.obligation_id(
        lifecycle_event_id="decision:D-1", expected_dataset="decision_ledger",
        expected_identity_key="decision_id", expected_identity={"decision_id": "D-1"},
    )
    record = enqueue(box, lifecycle_obligation_id=obligation_id)
    ledger = LifecycleEvidenceLedger(tmp_path / "late-lifecycle.jsonl")
    executor = worker(box, FakeS3(), clock, lifecycle_ledger=ledger)

    first = executor.run_once()
    assert first.state_after is DeliveryState.ACKNOWLEDGED
    assert first.reconciliation_anomaly == "LINKED_LIFECYCLE_OBLIGATION_NOT_FOUND"
    assert box.get(record.outbox_id).delivery_state is DeliveryState.ACKNOWLEDGED

    obligation = create_dataset_obligation(
        ledger, event_id="decision:D-1", lifecycle_stage="DECISION",
        dataset="decision_ledger", identity={"decision_id": "D-1", "symbol": "EURUSD"},
        timestamp=clock.iso(), producer="test", trigger="DECISION_WRITTEN",
    )
    assert obligation.obligation_id == obligation_id
    result, = executor.reconciliation_sweep(max_items=1)

    assert result.lifecycle_reconciled is True
    assert box.get(record.outbox_id).delivery_state is DeliveryState.ACKNOWLEDGED
    assert box.get(record.outbox_id).reconciliation_state == "RECONCILED"
    assert ledger.get(obligation.obligation_id).current_status == ObligationStatus.PRESENT.value


def test_retry_schedule_survives_worker_and_outbox_restart(tmp_path):
    clock = Clock()
    path = tmp_path / "restart.sqlite3"
    box = CanonicalDeliveryOutbox(path, canonical_bucket="test-bucket", clock=clock.iso)
    record = enqueue(box)
    s3 = FakeS3()
    s3.failures[record.canonical_key] = [TimeoutError("offline")]
    worker(box, s3, clock).run_once()
    retry_at = box.get(record.outbox_id).next_retry_at
    box.close()
    reloaded = CanonicalDeliveryOutbox(path, canonical_bucket="test-bucket", clock=clock.iso)
    assert reloaded.get(record.outbox_id).next_retry_at == retry_at
    assert worker(reloaded, s3, clock).run_once() is None


def test_failed_record_does_not_block_healthy_record(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock)
    first = enqueue(box, suffix="1")
    second = enqueue(box, suffix="2")
    s3 = FakeS3()
    s3.failures[first.canonical_key] = [TimeoutError("offline")]
    results = worker(box, s3, clock).drain(max_items=2)
    assert {item.state_after for item in results} == {
        DeliveryState.RETRYABLE_FAILURE, DeliveryState.ACKNOWLEDGED,
    }
    assert box.get(second.outbox_id).delivery_state is DeliveryState.ACKNOWLEDGED


def test_reconciliation_sweep_repairs_orphan_even_before_retry_due(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock)
    record = enqueue(box)
    s3 = FakeS3()
    s3.failures[record.canonical_key] = [TimeoutError("response lost")]
    executor = worker(box, s3, clock)
    assert executor.run_once().state_after is DeliveryState.RETRYABLE_FAILURE
    s3.seed_exact(record)
    result, = executor.reconciliation_sweep(max_items=1)
    assert result.orphan_ack_recovered is True
    assert result.state_after is DeliveryState.ACKNOWLEDGED


def test_reconciliation_sweep_visits_each_missing_record_once(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock)
    first = enqueue(box, suffix="1")
    second = enqueue(box, suffix="2")
    results = worker(box, FakeS3(), clock).reconciliation_sweep(max_items=10)
    assert {item.outbox_id for item in results} == {first.outbox_id, second.outbox_id}
    assert len(results) == 2
    assert [
        (item.dataset, item.state_after.value, item.error_class) for item in results
        if item.state_after is not DeliveryState.ACKNOWLEDGED
    ] == []


def test_operational_dataset_acks_without_lifecycle_promotion(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock)
    record = enqueue(box, dataset="events")
    result = worker(box, FakeS3(), clock).run_once()
    assert result.state_after is DeliveryState.ACKNOWLEDGED
    assert result.lifecycle_reconciled is False
    assert box.get(record.outbox_id).reconciliation_state == "NOT_APPLICABLE"


def test_worker_observability_is_bounded_and_classified(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock)
    record = enqueue(box)
    s3 = FakeS3()
    s3.failures[record.canonical_key] = [TimeoutError("offline")]
    executor = worker(box, s3, clock)
    executor.run_once()
    status = executor.status()
    assert status.retryable == 1
    assert status.attempts_by_dataset == {"decision_ledger": 1}
    assert status.failures_by_error_class == {"TimeoutError": 1}


def test_failure_classification_contract():
    assert classify_delivery_failure(FakeS3Error("InternalError", 500)).category == "RETRYABLE"
    assert classify_delivery_failure(FakeS3Error("SlowDown", 503)).category == "RETRYABLE"
    assert classify_delivery_failure(FakeS3Error("AccessDenied", 403)).category == "TERMINAL"


def test_bounded_end_to_end_acceptance_matrix(tmp_path):
    clock = Clock()
    ledger = LifecycleEvidenceLedger(tmp_path / "acceptance-lifecycle.jsonl")
    box = outbox(tmp_path, clock, "acceptance.sqlite3")
    specs = [
        ("market_context", {"entity_id": "E-1"}, None),
        ("execution_results", {"correlation_id": "C-SHARED", "account_id": "A"}, "A"),
        ("execution_results", {"correlation_id": "C-SHARED", "account_id": "B"}, "B"),
        ("trade_truth", {"trade_id": "T-1", "account_id": "A"}, "A"),
        ("protection_audit", {"correlation_id": "C-1", "account_id": "A",
                              "position_ticket": 101}, "A"),
        ("shadow_runtime", {"event_id": "S-1"}, None),
        ("decision_ledger", {"decision_id": "D-1"}, None),
        ("events", {"ts_utc_ms": 1790942400001, "type": "CANDLE",
                    "symbol": "EURUSD"}, None),
    ]
    rows = []
    obligations = {}
    for index, (dataset, identity, account) in enumerate(specs):
        payload = {**identity, "symbol": "EURUSD", "fixture": dataset}
        if dataset == "trade_truth":
            payload["identity"] = dict(identity)
        obligation_id = None
        if dataset != "events":
            obligation = create_dataset_obligation(
                ledger, event_id=f"acceptance:{dataset}:{index}",
                lifecycle_stage="BLOCK_1C_ACCEPTANCE", dataset=dataset,
                identity={**identity, "symbol": "EURUSD"},
                timestamp=clock.iso(), producer="test", trigger="LOCAL_FSYNC",
            )
            obligations[(dataset, account)] = obligation.obligation_id
            obligation_id = obligation.obligation_id
        row = box.enqueue(
            dataset=dataset, payload=payload, identity=identity,
            symbol="EURUSD", partition_date=DATE,
            lifecycle_obligation_id=obligation_id,
        ).record
        rows.append((dataset, account, row))

    s3 = FakeS3()
    account_b = next(row for dataset, account, row in rows
                     if dataset == "execution_results" and account == "B")
    s3.failures[account_b.canonical_key] = [TimeoutError("account B outage")]
    executor = worker(box, s3, clock, lifecycle_ledger=ledger)
    first_pass = executor.drain(max_items=8)
    assert len(first_pass) == 8
    for dataset, account, row in rows:
        expected = (DeliveryState.RETRYABLE_FAILURE
                    if dataset == "execution_results" and account == "B"
                    else DeliveryState.ACKNOWLEDGED)
        assert box.get(row.outbox_id).delivery_state is expected
        if dataset != "events" and expected is DeliveryState.ACKNOWLEDGED:
            assert ledger.get(obligations[(dataset, account)]).current_status \
                == ObligationStatus.PRESENT.value
    assert ledger.get(obligations[("execution_results", "B")]).current_status \
        == ObligationStatus.NOT_YET_DUE.value
    clock.advance(11)
    recovered = executor.run_once()
    assert recovered.outbox_id == account_b.outbox_id
    assert recovered.state_after is DeliveryState.ACKNOWLEDGED
    assert ledger.get(obligations[("execution_results", "B")]).current_status \
        == ObligationStatus.PRESENT.value
    assert_delivery_invariants(box, s3, ledger)


def test_worker_can_deliver_all_23_production_v1_datasets(tmp_path):
    clock = Clock()
    box = outbox(tmp_path, clock, "all-datasets.sqlite3")
    ledger = LifecycleEvidenceLedger(tmp_path / "all-datasets-lifecycle.jsonl")
    lifecycle_ids = {}
    for index, dataset in enumerate(PRODUCTION_SCHEMA_REGISTRY, start=1):
        identity = {
            field: ("EURUSD" if dataset == "events" and field == "symbol"
                else index if field in {"cycle_id", "position_ticket", "ts_utc_ms"}
                    else f"{field}-{index}")
            for field in EXACT_IDENTITY_FIELDS[dataset]
        }
        payload = {**identity, "symbol": "EURUSD", "fixture": dataset}
        if dataset in {"trade_truth", "shadow_trades", "research_shadow_trades"}:
            payload["identity"] = {
                key: value for key, value in identity.items()
                if key in {"trade_id", "account_id"}
            }
        obligation_id = None
        if DATASET_DISPOSITIONS[dataset]["class"] in {
            "A_LIVE_REQUIRED", "B_LIVE_CONDITIONAL",
        }:
            obligation = create_dataset_obligation(
                ledger, event_id=f"all-datasets:{dataset}:{index}",
                lifecycle_stage="BLOCK_1D_ALL_DATASET_TEST", dataset=dataset,
                identity={**identity, "symbol": "EURUSD"},
                timestamp=clock.iso(), producer="test", trigger="REPRESENTATIVE_RECORD",
            )
            lifecycle_ids[dataset] = obligation.obligation_id
            obligation_id = obligation.obligation_id
        box.enqueue(
            dataset=dataset, payload=payload, identity=identity,
            symbol="EURUSD" if is_symbol_scoped(dataset) else "",
            partition_date=DATE,
            lifecycle_obligation_id=obligation_id,
        )
    s3 = FakeS3()
    results = worker(box, s3, clock, lifecycle_ledger=ledger).drain(max_items=23)
    assert len(results) == 23
    assert all(item.state_after is DeliveryState.ACKNOWLEDGED for item in results), [
        (item.dataset, item.state_after.value, item.error_class) for item in results
    ]
    assert box.status().acknowledged_count == 23
    counts = assert_delivery_invariants(box, s3, ledger)
    assert counts["records"] == 23
    by_dataset = {item.dataset: item for item in results}
    for dataset, disposition in DATASET_DISPOSITIONS.items():
        item = by_dataset[dataset]
        assert item.ack_verified is True
        if disposition["class"] in {"A_LIVE_REQUIRED", "B_LIVE_CONDITIONAL"}:
            assert item.lifecycle_reconciled is True
            assert ledger.get(lifecycle_ids[dataset]).current_status == ObligationStatus.PRESENT.value
        else:
            assert item.lifecycle_reconciled is False
            assert box.get(item.outbox_id).reconciliation_state == "NOT_APPLICABLE"
