from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import sqlite3

import pytest

from core.canonical_delivery_outbox import (
    CanonicalAckError,
    CanonicalDeliveryOutbox,
    DeliveryState,
    EnqueueOutcome,
    InvalidDeliveryTransition,
    OutboxCorruptionError,
    OutboxPersistenceError,
)
from core.lifecycle_evidence_obligations import EXACT_IDENTITY_FIELDS
from core.production_data_contract import (
    PRODUCTION_SCHEMA_REGISTRY,
    current_schema,
    is_symbol_scoped,
)


DATE = "2026-10-01"


def _outbox(tmp_path, name="outbox.sqlite3", **kwargs):
    return CanonicalDeliveryOutbox(
        tmp_path / name, canonical_bucket="canonical-test-bucket", **kwargs,
    )


def _decision_payload(decision="D1", **extra):
    return {"decision_id": decision, "symbol": "EURUSD", "decision": "NO_TRADE", **extra}


def _enqueue_decision(outbox, decision="D1", **extra):
    return outbox.enqueue(
        dataset="decision_ledger",
        payload=_decision_payload(decision, **extra),
        symbol="EURUSD",
        partition_date=DATE,
    )


def _ack_evidence(record):
    return {
        "outbox_id": record.outbox_id,
        "idempotency_key": record.idempotency_key,
        "canonical_bucket": record.canonical_bucket,
        "canonical_key": record.canonical_key,
        "payload_sha256": record.payload_sha256,
        "put_status_code": 200,
        "etag": '"etag-1"',
        "version_id": "version-1",
        "verification_method": "GET_BODY_SHA256",
        "verified_payload_sha256": record.payload_sha256,
    }


def test_duplicate_enqueue_is_idempotent_and_has_deterministic_destination(tmp_path):
    outbox = _outbox(tmp_path)
    first = _enqueue_decision(outbox)
    duplicate = _enqueue_decision(outbox)

    assert first.outcome is EnqueueOutcome.CREATED
    assert duplicate.outcome is EnqueueOutcome.DUPLICATE
    assert duplicate.record.outbox_id == first.record.outbox_id
    assert duplicate.record.canonical_key == first.record.canonical_key
    assert duplicate.record.canonical_key.endswith(
        f"part-outbox-{first.record.idempotency_key}.jsonl"
    )
    assert len(outbox.records()) == 1


def test_same_identity_different_payload_enters_explicit_conflict(tmp_path):
    outbox = _outbox(tmp_path)
    first = _enqueue_decision(outbox, score=1)
    conflict = _enqueue_decision(outbox, score=2)

    assert conflict.outcome is EnqueueOutcome.CONFLICT
    assert conflict.record.outbox_id == first.record.outbox_id
    assert conflict.record.delivery_state is DeliveryState.CONFLICT
    assert conflict.record.payload["score"] == 1  # original obligation is preserved
    assert conflict.record.conflict_payload_sha256
    assert "IDEMPOTENCY_CONFLICT" in conflict.record.last_error
    outbox.close()
    reloaded = _outbox(tmp_path)
    assert reloaded.get(first.record.outbox_id).delivery_state is DeliveryState.CONFLICT


def test_restart_before_delivery_reloads_one_pending_and_duplicate_stays_one(tmp_path):
    first = _outbox(tmp_path)
    created = _enqueue_decision(first)
    first.close()

    reloaded = _outbox(tmp_path)
    current = reloaded.get(created.record.outbox_id)
    duplicate = _enqueue_decision(reloaded)

    assert current is not None
    assert current.delivery_state is DeliveryState.PENDING
    assert duplicate.outcome is EnqueueOutcome.DUPLICATE
    assert len(reloaded.records()) == 1


def test_restart_after_exact_ack_preserves_ack_and_replay_is_duplicate(tmp_path):
    outbox = _outbox(tmp_path)
    created = _enqueue_decision(outbox).record
    claimed = outbox.claim_next()
    assert claimed is not None
    acknowledged = outbox.acknowledge(claimed.outbox_id, _ack_evidence(claimed))
    outbox.close()

    reloaded = _outbox(tmp_path)
    current = reloaded.get(created.outbox_id)
    replay = _enqueue_decision(reloaded)

    assert current is not None
    assert current.delivery_state is DeliveryState.ACKNOWLEDGED
    assert current.canonical_ack["canonical_key"] == current.canonical_key
    assert replay.outcome is EnqueueOutcome.DUPLICATE
    assert replay.record.delivery_state is DeliveryState.ACKNOWLEDGED


def test_ack_requires_put_response_and_exact_post_write_verification(tmp_path):
    outbox = _outbox(tmp_path)
    record = _enqueue_decision(outbox).record
    with pytest.raises(InvalidDeliveryTransition):
        outbox.acknowledge(record.outbox_id, _ack_evidence(record))

    claimed = outbox.claim_next()
    assert claimed is not None
    bad = _ack_evidence(claimed)
    bad["verified_payload_sha256"] = "0" * 64
    with pytest.raises(CanonicalAckError, match="HASH_MISMATCH"):
        outbox.acknowledge(claimed.outbox_id, bad)
    assert outbox.get(claimed.outbox_id).delivery_state is DeliveryState.IN_FLIGHT


def test_ack_accepts_verified_identical_object_after_create_only_collision(tmp_path):
    outbox = _outbox(tmp_path)
    _enqueue_decision(outbox)
    claimed = outbox.claim_next()
    evidence = _ack_evidence(claimed)
    evidence.update({"put_status_code": 412, "object_preexisted": True})

    acknowledged = outbox.acknowledge(claimed.outbox_id, evidence)
    assert acknowledged.delivery_state is DeliveryState.ACKNOWLEDGED


def test_ack_rejects_unreported_missing_etag_before_commit(tmp_path):
    outbox = _outbox(tmp_path)
    _enqueue_decision(outbox)
    claimed = outbox.claim_next()
    evidence = _ack_evidence(claimed)
    evidence.pop("etag")

    with pytest.raises(CanonicalAckError, match="ACK_ETAG_ABSENCE_NOT_EXPLICIT"):
        outbox.acknowledge(claimed.outbox_id, evidence)

    current = outbox.get(claimed.outbox_id)
    assert current.delivery_state is DeliveryState.IN_FLIGHT
    assert current.canonical_ack is None


def test_retryable_failure_and_expired_interrupted_claim_survive_restart(tmp_path):
    outbox = _outbox(tmp_path)
    first = _enqueue_decision(outbox, decision="D1").record
    claimed = outbox.claim_next()
    outbox.mark_retryable_failure(
        claimed.outbox_id, error="S3_TIMEOUT", next_retry_at="2026-10-02T00:00:00+00:00",
    )
    second = _enqueue_decision(outbox, decision="D2").record
    claimed_second = outbox.claim_next(
        now="2026-10-01T12:00:00+00:00", owner_id="process-one", lease_seconds=60,
    )
    assert claimed_second.outbox_id == second.outbox_id
    outbox.close()

    reloaded = _outbox(tmp_path)
    assert reloaded.get(first.outbox_id).delivery_state is DeliveryState.RETRYABLE_FAILURE
    recovered = reloaded.get(second.outbox_id)
    assert recovered.delivery_state is DeliveryState.IN_FLIGHT
    assert reloaded.claim_next(
        now="2026-10-01T12:00:30+00:00", owner_id="process-two",
    ) is None
    reclaimed = reloaded.claim_next(
        now="2026-10-01T12:01:01+00:00", owner_id="process-two",
    )
    assert reclaimed.outbox_id == second.outbox_id
    assert reclaimed.claim_owner == "process-two"
    assert reclaimed.attempt_count == 2


def test_local_persistence_failure_raises_and_does_not_claim_success(tmp_path, monkeypatch):
    outbox = _outbox(tmp_path)

    def fail_insert(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(outbox, "_insert_record", fail_insert)
    with pytest.raises(OutboxPersistenceError, match="ENQUEUE_NOT_DURABLE"):
        _enqueue_decision(outbox)
    assert outbox.records() == ()


def test_malformed_persisted_row_fails_closed_on_restart(tmp_path):
    outbox = _outbox(tmp_path)
    _enqueue_decision(outbox)
    outbox.close()
    db_path = tmp_path / "outbox.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE outbox_records SET payload_json='{truncated'")
        db.commit()

    with pytest.raises(OutboxCorruptionError, match="CORRUPT_OUTBOX_ROW"):
        _outbox(tmp_path)


def test_malformed_ack_evidence_fails_closed_on_restart(tmp_path):
    outbox = _outbox(tmp_path)
    record = _enqueue_decision(outbox)
    claimed = outbox.claim_next(owner_id="worker-A")
    outbox.acknowledge(claimed.outbox_id, _ack_evidence(claimed))
    outbox.close()

    with sqlite3.connect(tmp_path / "outbox.sqlite3") as db:
        db.execute("UPDATE outbox_records SET ack_json='{}'")
        db.commit()

    with pytest.raises(OutboxCorruptionError, match="CORRUPT_OUTBOX_ROW"):
        _outbox(tmp_path)


def test_unknown_persisted_delivery_state_fails_closed_on_restart(tmp_path):
    outbox = _outbox(tmp_path)
    _enqueue_decision(outbox)
    outbox.close()
    with sqlite3.connect(tmp_path / "outbox.sqlite3") as db:
        db.execute("UPDATE outbox_records SET delivery_state='SILENTLY_ACKED'")
        db.commit()

    with pytest.raises(OutboxCorruptionError, match="CORRUPT_OUTBOX_ROW"):
        _outbox(tmp_path)


@pytest.mark.parametrize(("field", "value"), [
    ("canonical_key", ""),
    ("next_retry_at", None),
])
def test_impossible_delivery_metadata_fails_closed_on_restart(tmp_path, field, value):
    outbox = _outbox(tmp_path)
    record = _enqueue_decision(outbox).record
    if field == "next_retry_at":
        claimed = outbox.claim_next()
        outbox.mark_retryable_failure(
            claimed.outbox_id, error="offline",
            next_retry_at="2026-10-01T12:00:10+00:00",
        )
    outbox.close()
    with sqlite3.connect(tmp_path / "outbox.sqlite3") as db:
        if field == "next_retry_at":
            db.execute(
                "UPDATE outbox_records SET delivery_state=?, next_retry_at=NULL "
                "WHERE outbox_id=?",
                (DeliveryState.RETRYABLE_FAILURE.value, record.outbox_id),
            )
        else:
            db.execute(
                "UPDATE outbox_records SET canonical_key=? WHERE outbox_id=?",
                (value, record.outbox_id),
            )
        db.commit()

    with pytest.raises(OutboxCorruptionError, match="CORRUPT_OUTBOX_ROW"):
        _outbox(tmp_path)


def test_account_and_dataset_identity_isolation(tmp_path):
    outbox = _outbox(tmp_path)
    common = {"correlation_id": "COR-1", "symbol": "EURUSD"}
    account_a = outbox.enqueue(
        dataset="execution_results", payload={**common, "account_id": "A"},
        symbol="EURUSD", partition_date=DATE,
    ).record
    account_b = outbox.enqueue(
        dataset="execution_results", payload={**common, "account_id": "B"},
        symbol="EURUSD", partition_date=DATE,
    ).record
    strategy = outbox.enqueue(
        dataset="strategy_candidates",
        payload={"candidate_id": "ENTITY-1", "symbol": "EURUSD"},
        symbol="EURUSD", partition_date=DATE,
    ).record
    horizon = outbox.enqueue(
        dataset="horizon_candidates",
        payload={"candidate_id": "ENTITY-1", "symbol": "EURUSD"},
        symbol="EURUSD", partition_date=DATE,
    ).record

    assert account_a.idempotency_key != account_b.idempotency_key
    assert strategy.idempotency_key != horizon.idempotency_key
    assert len(outbox.records()) == 4


def test_explicit_identity_must_match_serialized_payload(tmp_path):
    outbox = _outbox(tmp_path)
    with pytest.raises(ValueError, match="EXPLICIT_IDENTITY_DOES_NOT_MATCH_PAYLOAD"):
        outbox.enqueue(
            dataset="execution_results",
            payload={"correlation_id": "COR-1", "account_id": "B",
                     "symbol": "EURUSD"},
            identity={"correlation_id": "COR-1", "account_id": "A"},
            symbol="EURUSD", partition_date=DATE,
        )
    assert outbox.records() == ()


def test_prepared_local_handoff_recovers_exact_line_idempotently(tmp_path):
    from core.canonical_delivery import recover_local_handoffs

    outbox = _outbox(tmp_path)
    payload = _decision_payload("D-HANDOFF")
    path = tmp_path / "local" / "EURUSD" / "2026-10-01.jsonl"
    line = json.dumps(payload, separators=(",", ":"))
    handoff = outbox.prepare_local_handoff(
        dataset="decision_ledger", payload=payload, symbol="EURUSD",
        partition_date=DATE, local_path=path, local_line=line,
    )
    assert handoff.state == "PREPARED"
    assert outbox.records() == ()

    path.parent.mkdir(parents=True)
    path.write_text(line + "\n", encoding="utf-8")
    recovery = recover_local_handoffs(outbox=outbox, max_items=1)

    assert recovery.inspected == 1
    assert recovery.recovered == 1
    assert recovery.waiting_for_local_record == 0
    assert len(outbox.records()) == 1
    assert outbox.records()[0].outbox_id == f"outbox_{handoff.handoff_id}"
    assert outbox.local_handoff_status() == {"COMPLETED": 1}
    assert recover_local_handoffs(outbox=outbox, max_items=1).inspected == 0


def test_missing_local_handoff_does_not_starve_later_valid_handoff_after_restart(
    tmp_path,
):
    from core.canonical_delivery import LocalHandoffRecoveryResult, recover_local_handoffs

    clock_values = iter([
        "2026-10-01T10:00:00+00:00",
        "2026-10-01T10:00:01+00:00",
        "2026-10-01T10:00:02+00:00",
    ])
    outbox = _outbox(tmp_path, clock=lambda: next(clock_values))

    missing_payload = _decision_payload("D-HANDOFF-MISSING")
    missing_line = json.dumps(missing_payload, separators=(",", ":"))
    missing = outbox.prepare_local_handoff(
        dataset="decision_ledger", payload=missing_payload, symbol="EURUSD",
        partition_date=DATE, local_path=tmp_path / "missing.jsonl",
        local_line=missing_line,
    )

    valid_payload = _decision_payload("D-HANDOFF-VALID")
    valid_line = json.dumps(valid_payload, separators=(",", ":"))
    valid_path = tmp_path / "valid.jsonl"
    valid = outbox.prepare_local_handoff(
        dataset="decision_ledger", payload=valid_payload, symbol="EURUSD",
        partition_date=DATE, local_path=valid_path, local_line=valid_line,
    )
    valid_path.write_text(valid_line + "\n", encoding="utf-8")

    first = recover_local_handoffs(outbox=outbox, max_items=1)
    assert first == LocalHandoffRecoveryResult(
        inspected=1, recovered=0, waiting_for_local_record=1, failed=0,
    )
    outbox.close()

    with sqlite3.connect(tmp_path / "outbox.sqlite3") as db:
        attempts = dict(db.execute(
            "SELECT handoff_id,recovery_attempts FROM local_handoffs"
        ).fetchall())
    assert attempts == {missing.handoff_id: 1, valid.handoff_id: 0}

    restarted = _outbox(tmp_path)
    second = recover_local_handoffs(outbox=restarted, max_items=1)

    assert second == LocalHandoffRecoveryResult(
        inspected=1, recovered=1, waiting_for_local_record=0, failed=0,
    )
    assert restarted.get(f"outbox_{valid.handoff_id}").payload["decision_id"] \
        == "D-HANDOFF-VALID"
    assert restarted.local_handoff_status() == {"COMPLETED": 1, "PREPARED": 1}


def test_prewrite_handoff_only_bypasses_incomplete_identity(tmp_path):
    from core.canonical_delivery import try_prepare_local_jsonl_handoffs

    outbox = _outbox(tmp_path)
    assert try_prepare_local_jsonl_handoffs(
        dataset="decision_ledger", content='{"symbol":"EURUSD"}',
        symbol="EURUSD", partition_date=DATE,
        local_path=tmp_path / "local" / "decisions.jsonl", outbox=outbox,
    ) == ()
    with pytest.raises(ValueError, match="PAYLOAD_NOT_CANONICAL_JSON"):
        try_prepare_local_jsonl_handoffs(
            dataset="decision_ledger",
            content='{"decision_id":"D-NAN","value":NaN}',
            symbol="EURUSD", partition_date=DATE,
            local_path=tmp_path / "local" / "decisions.jsonl", outbox=outbox,
        )


def test_local_handoff_same_identity_changed_payload_is_durable_conflict(tmp_path):
    outbox = _outbox(tmp_path)
    first_payload = _decision_payload("D-HANDOFF-CONFLICT", reason="first")
    first_line = json.dumps(first_payload, separators=(",", ":"))
    outbox.prepare_local_handoff(
        dataset="decision_ledger", payload=first_payload, symbol="EURUSD",
        partition_date=DATE, local_path=tmp_path / "first.jsonl",
        local_line=first_line,
    )

    changed_payload = _decision_payload("D-HANDOFF-CONFLICT", reason="changed")
    changed_line = json.dumps(changed_payload, separators=(",", ":"))
    with pytest.raises(ValueError, match="LOCAL_HANDOFF_IDEMPOTENCY_CONFLICT"):
        outbox.prepare_local_handoff(
            dataset="decision_ledger", payload=changed_payload, symbol="EURUSD",
            partition_date=DATE, local_path=tmp_path / "first.jsonl",
            local_line=changed_line,
        )

    handoffs = outbox.pending_local_handoffs(limit=10)
    assert handoffs == ()
    assert outbox.local_handoff_status() == {"CONFLICT": 1}


def test_schema_upgrade_adds_local_handoff_table_without_losing_outbox_rows(tmp_path):
    outbox = _outbox(tmp_path)
    record = _enqueue_decision(outbox).record
    outbox.close()
    db_path = tmp_path / "outbox.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute("DROP TABLE local_handoffs")
        db.execute("PRAGMA user_version=3")
        db.commit()

    upgraded = _outbox(tmp_path)
    assert upgraded.get(record.outbox_id).delivery_state is DeliveryState.PENDING
    with sqlite3.connect(db_path) as db:
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        version = db.execute("PRAGMA user_version").fetchone()[0]
    assert "local_handoffs" in tables
    assert version == 5


def test_observation_timestamp_is_payload_not_implicit_identity_or_clock_material(tmp_path):
    clocks = iter(["2026-10-01T10:00:00+00:00", "2026-10-01T11:00:00+00:00"])
    outbox = _outbox(tmp_path, clock=lambda: next(clocks))
    first = _enqueue_decision(outbox, observed_at="2026-10-01T09:00:00Z")
    second = _enqueue_decision(outbox, observed_at="2026-10-01T09:01:00Z")

    assert first.record.idempotency_key == second.record.idempotency_key
    assert second.outcome is EnqueueOutcome.CONFLICT


def test_thread_and_cross_connection_duplicate_race_creates_one_record(tmp_path):
    left = _outbox(tmp_path)
    right = _outbox(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda box: _enqueue_decision(box), (left, right)))

    assert {result.outcome for result in results} == {
        EnqueueOutcome.CREATED, EnqueueOutcome.DUPLICATE,
    }
    assert len(left.records()) == 1
    assert len(right.records()) == 1


def test_lifecycle_boundary_requires_ack_not_enqueue(tmp_path):
    outbox = _outbox(tmp_path)
    record = outbox.enqueue(
        dataset="decision_ledger", payload=_decision_payload(), symbol="EURUSD",
        partition_date=DATE, lifecycle_obligation_id="obligation-1",
    ).record
    assert outbox.acknowledged_lifecycle_obligation_ids() == frozenset()
    contract = outbox.reconciliation_contract(record.outbox_id)
    assert contract["canonical_ack_required_for_present"] is True

    claimed = outbox.claim_next()
    outbox.acknowledge(claimed.outbox_id, _ack_evidence(claimed))
    assert outbox.acknowledged_lifecycle_obligation_ids() == frozenset({"obligation-1"})


def test_status_exposes_counts_oldest_age_and_dataset_breakdown(tmp_path):
    times = iter([
        "2026-10-01T10:00:00+00:00", "2026-10-01T10:01:00+00:00",
        "2026-10-01T10:02:00+00:00", "2026-10-01T10:03:00+00:00",
    ])
    outbox = _outbox(tmp_path, clock=lambda: next(times))
    _enqueue_decision(outbox, decision="D1")
    _enqueue_decision(outbox, decision="D2")
    claimed = outbox.claim_next(now="2026-10-01T10:02:00+00:00")
    outbox.mark_retryable_failure(
        claimed.outbox_id, error="temporary",
        next_retry_at="2026-10-01T12:00:10+00:00",
    )

    status = outbox.status(now=datetime(2026, 10, 1, 10, 5, tzinfo=timezone.utc))
    assert status.pending_count == 1
    assert status.retryable_failure_count == 1
    assert status.acknowledged_count == 0
    assert status.oldest_pending_age_seconds == 300.0
    assert status.dataset_breakdown["decision_ledger"] == {
        "PENDING": 1, "RETRYABLE_FAILURE": 1,
    }


def test_terminal_failure_is_durable_and_not_claimable(tmp_path):
    outbox = _outbox(tmp_path)
    record = _enqueue_decision(outbox).record
    claimed = outbox.claim_next()
    terminal = outbox.mark_terminal_failure(claimed.outbox_id, error="INVALID_DESTINATION")
    assert terminal.delivery_state is DeliveryState.TERMINAL_FAILURE
    assert outbox.claim_next() is None
    outbox.close()

    reloaded = _outbox(tmp_path)
    assert reloaded.get(record.outbox_id).delivery_state is DeliveryState.TERMINAL_FAILURE
    assert reloaded.status().terminal_or_conflict_count == 1


def test_all_active_datasets_have_identity_destination_and_json_support(tmp_path):
    assert len(PRODUCTION_SCHEMA_REGISTRY) == 29
    assert set(PRODUCTION_SCHEMA_REGISTRY) == set(EXACT_IDENTITY_FIELDS)
    outbox = _outbox(tmp_path)

    for index, dataset in enumerate(PRODUCTION_SCHEMA_REGISTRY, start=1):
        identity = {
            field: (index if field in {"cycle_id", "position_ticket", "ts_utc_ms"}
                    else f"{field}-{index}")
            for field in EXACT_IDENTITY_FIELDS[dataset]
        }
        result = outbox.enqueue(
            dataset=dataset,
            payload={
                **identity,
                "dataset_fixture": dataset,
                "identity_fixture": identity,
                **({"identity": {
                    key: value for key, value in identity.items()
                    if key in {"trade_id", "account_id"}
                }} if dataset in {"trade_truth", "shadow_trades", "research_shadow_trades"}
                   else {}),
            },
            identity=identity,
            symbol="EURUSD" if is_symbol_scoped(dataset) else "",
            partition_date=DATE,
        )
        assert result.record.schema_version == current_schema(dataset)
        assert result.record.canonical_key.endswith(
            f"part-outbox-{result.record.idempotency_key}.jsonl"
        )
        assert result.record.payload["identity_fixture"] == identity

    assert len(outbox.records()) == 29


def test_bounded_acceptance_matrix(tmp_path, monkeypatch):
    outbox = _outbox(tmp_path)
    normal = _enqueue_decision(outbox, decision="NORMAL")
    duplicate = _enqueue_decision(outbox, decision="NORMAL")
    account_a = outbox.enqueue(
        dataset="execution_results",
        payload={"correlation_id": "C", "account_id": "A", "symbol": "EURUSD"},
        symbol="EURUSD", partition_date=DATE,
    )
    account_b = outbox.enqueue(
        dataset="execution_results",
        payload={"correlation_id": "C", "account_id": "B", "symbol": "EURUSD"},
        symbol="EURUSD", partition_date=DATE,
    )
    cross_a = outbox.enqueue(
        dataset="strategy_candidates", payload={"candidate_id": "SAME"},
        symbol="EURUSD", partition_date=DATE,
    )
    cross_b = outbox.enqueue(
        dataset="horizon_candidates", payload={"candidate_id": "SAME"},
        symbol="EURUSD", partition_date=DATE,
    )
    ack_source = _enqueue_decision(outbox, decision="ACK").record
    claimed = outbox.claim_next()
    # Equal high-resolution timestamps can legitimately tie; claim until the
    # specific logical delivery under test is reached.
    while claimed.outbox_id != normal.record.outbox_id:
        outbox.mark_terminal_failure(claimed.outbox_id, error="matrix skip")
        claimed = outbox.claim_next()
    acknowledged = outbox.acknowledge(claimed.outbox_id, _ack_evidence(claimed))
    ack_replay = _enqueue_decision(outbox, decision="NORMAL")
    conflict = _enqueue_decision(outbox, decision="ACK", mutation=True)
    path = outbox.path
    outbox.close()
    reloaded = CanonicalDeliveryOutbox(path, canonical_bucket="canonical-test-bucket")
    failed_box = _outbox(tmp_path, "failed.sqlite3")

    def fail_insert(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(failed_box, "_insert_record", fail_insert)
    with pytest.raises(OutboxPersistenceError):
        _enqueue_decision(failed_box, decision="FAIL")
    local_failure = len(failed_box.records()) == 0

    rows = {
        "normal enqueue": normal.outcome.value,
        "duplicate enqueue": duplicate.outcome.value,
        "account A/B separation": account_a.record.outbox_id != account_b.record.outbox_id,
        "cross-dataset same entity": cross_a.record.outbox_id != cross_b.record.outbox_id,
        "restart": reloaded.get(ack_source.outbox_id) is not None,
        "acknowledged replay": ack_replay.record.delivery_state.value,
        "payload conflict": conflict.outcome.value,
        "local persistence failure": local_failure,
    }
    assert rows == {
        "normal enqueue": "CREATED",
        "duplicate enqueue": "DUPLICATE",
        "account A/B separation": True,
        "cross-dataset same entity": True,
        "restart": True,
        "acknowledged replay": "ACKNOWLEDGED",
        "payload conflict": "CONFLICT",
        "local persistence failure": True,
    }
