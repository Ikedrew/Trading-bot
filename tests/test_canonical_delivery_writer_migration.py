from __future__ import annotations

import importlib.util
import inspect
import json

import pytest

from core.canonical_delivery import (
    CANONICAL_DELIVERY_MIGRATION,
    CANONICAL_WRITER_MODULES,
    CanonicalDeliveryHandoffError,
    MigrationClassification,
    enqueue_canonical_delivery,
    get_delivery_outbox,
    validate_migration_coverage,
)
from core.canonical_delivery_outbox import (
    CanonicalDeliveryOutbox,
    DeliveryState,
    EnqueueOutcome,
)
from core.lifecycle_evidence_obligations import EXACT_IDENTITY_FIELDS
from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY, is_symbol_scoped
from research_engine.data_access.s3_source import S3ResearchDataSource
from tests._s3_fake import FakeS3


DATE = "2026-10-02"


def _identity(dataset: str, suffix: str = "1") -> dict[str, object]:
    numeric = {"cycle_id", "position_ticket", "ts_utc_ms"}
    return {
        field: int(suffix) if field in numeric else f"{field}-{suffix}"
        for field in EXACT_IDENTITY_FIELDS[dataset]
    }


def _payload(dataset: str, suffix: str = "1") -> dict[str, object]:
    payload: dict[str, object] = {
        "dataset_fixture": dataset,
        "symbol": "EURUSD",
        "timestamp": f"{DATE}T12:00:00+00:00",
        **_identity(dataset, suffix),
    }
    # These governed identities live below identity in their real schemas.
    if dataset in {"trade_truth", "shadow_trades", "research_shadow_trades"}:
        payload["identity"] = {
            key: value for key, value in _identity(dataset, suffix).items()
            if key in {"trade_id", "account_id"}
        }
    return payload


def test_all_active_datasets_have_exactly_one_migration_classification_and_writer():
    validate_migration_coverage()
    active = set(PRODUCTION_SCHEMA_REGISTRY)
    assert len(active) == 23
    assert set(CANONICAL_DELIVERY_MIGRATION) == active == set(CANONICAL_WRITER_MODULES)
    assert set(EXACT_IDENTITY_FIELDS) == active
    assert set(CANONICAL_DELIVERY_MIGRATION.values()) == {
        MigrationClassification.OUTBOX_MIGRATED,
    }


def test_every_audited_live_writer_uses_handoff_and_has_no_direct_s3_put():
    for dataset, modules in CANONICAL_WRITER_MODULES.items():
        for module_name in modules:
            spec = importlib.util.find_spec(module_name)
            assert spec is not None and spec.origin, (dataset, module_name)
            source = open(spec.origin, encoding="utf-8").read()
            assert "enqueue_canonical_" in source, (dataset, module_name)
            assert ".put_object(" not in source, (dataset, module_name)


@pytest.mark.parametrize(
    "dataset",
    (
        "market_context", "opportunities", "assessments",
        "strategy_candidates", "horizon_candidates", "decision_ledger",
        "execution_results", "trade_truth", "protection_audit",
        "risk_deviation", "management_actions", "portfolio_rankings",
        "shadow_runtime", "events", "quarantine",
    ),
)
def test_bounded_migration_acceptance_is_local_then_pending(tmp_path, dataset):
    local = tmp_path / "local" / f"{dataset}.jsonl"
    local.parent.mkdir(parents=True, exist_ok=True)
    payload = _payload(dataset)
    with local.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")
        handle.flush()
    outbox = CanonicalDeliveryOutbox(tmp_path / f"{dataset}.sqlite3")
    result = enqueue_canonical_delivery(
        dataset=dataset,
        payload=payload,
        symbol="EURUSD" if is_symbol_scoped(dataset) else "",
        partition_date=DATE,
        outbox=outbox,
    )
    assert local.exists() and local.stat().st_size > 0
    assert result.outcome is EnqueueOutcome.CREATED
    assert result.record.delivery_state is DeliveryState.PENDING
    assert result.record.record_identity == _identity(dataset)
    assert "part-000.jsonl" not in result.record.canonical_key
    assert len(outbox.records()) == 1


def test_adapter_duplicate_restart_account_isolation_and_conflict(tmp_path):
    path = tmp_path / "outbox.sqlite3"
    first = CanonicalDeliveryOutbox(path)
    payload_a = _payload("execution_results")
    created = enqueue_canonical_delivery(
        dataset="execution_results", payload=payload_a, symbol="EURUSD",
        partition_date=DATE, outbox=first,
    )
    duplicate = enqueue_canonical_delivery(
        dataset="execution_results", payload=payload_a, symbol="EURUSD",
        partition_date=DATE, outbox=first,
    )
    payload_b = dict(payload_a, account_id="account_id-2")
    account_b = enqueue_canonical_delivery(
        dataset="execution_results", payload=payload_b, symbol="EURUSD",
        partition_date=DATE, outbox=first,
    )
    conflict_payload = dict(payload_a, outcome="CHANGED")
    conflict = enqueue_canonical_delivery(
        dataset="execution_results", payload=conflict_payload, symbol="EURUSD",
        partition_date=DATE, outbox=first,
    )
    first.close()
    reloaded = CanonicalDeliveryOutbox(path)
    assert duplicate.outcome is EnqueueOutcome.DUPLICATE
    assert account_b.record.outbox_id != created.record.outbox_id
    assert conflict.outcome is EnqueueOutcome.CONFLICT
    assert reloaded.get(created.record.outbox_id).delivery_state is DeliveryState.CONFLICT
    assert len(reloaded.records()) == 2


def test_handoff_failure_is_explicit_and_does_not_quarantine_recursively(monkeypatch):
    calls = []

    class FailedOutbox:
        def enqueue(self, **_kwargs):
            raise OSError("disk full")

    monkeypatch.setattr(
        "core.canonical_delivery._record_handoff_failure",
        lambda obligation_id, dataset, exc: calls.append((dataset, type(exc).__name__)),
    )
    with pytest.raises(CanonicalDeliveryHandoffError, match="HANDOFF_FAILED"):
        enqueue_canonical_delivery(
            dataset="quarantine", payload=_payload("quarantine"), symbol="",
            partition_date=DATE, outbox=FailedOutbox(),
        )
    assert calls == [("quarantine", "OSError")]
    assert "quarantine(" not in inspect.getsource(
        __import__("core.canonical_delivery", fromlist=["_record_handoff_failure"])
        ._record_handoff_failure
    )


def test_handoff_failure_marks_existing_lifecycle_obligation_producer_failed(
    tmp_path, monkeypatch,
):
    from core.lifecycle_evidence_obligations import (
        LifecycleEvidenceLedger,
        ObligationStatus,
        create_dataset_obligation,
    )

    class FailedOutbox:
        def enqueue(self, **_kwargs):
            raise OSError("disk full")

    ledger = LifecycleEvidenceLedger(tmp_path / "obligations.jsonl")
    obligation = create_dataset_obligation(
        ledger, event_id="assessment:H1", lifecycle_stage="ASSESSMENT",
        dataset="assessments", identity={"assessment_id": "H1", "symbol": "EURUSD"},
        timestamp=f"{DATE}T12:00:00+00:00", producer="test",
        trigger="LOCAL_WRITE_COMPLETE",
    )
    monkeypatch.setattr(
        "core.lifecycle_evidence_obligations.obligation_ledger", lambda: ledger,
    )
    with pytest.raises(CanonicalDeliveryHandoffError):
        enqueue_canonical_delivery(
            dataset="assessments", payload={"assessment_id": "H1"},
            symbol="EURUSD", partition_date=DATE,
            lifecycle_obligation_id=obligation.obligation_id,
            outbox=FailedOutbox(),
        )
    failed = ledger.get(obligation.obligation_id)
    assert failed.current_status == ObligationStatus.PRODUCER_FAILED.value
    assert failed.failure_reason == "CANONICAL_OUTBOX_HANDOFF_FAILED:OSError"
    assert failed.provenance["producer_write"] == "LOCAL_FSYNC_SUCCEEDED"


def test_decision_ledger_buffer_acceptance_is_not_ack(tmp_path):
    from core.decision_ledger import DecisionLedgerWriter

    writer = DecisionLedgerWriter(
        local_dir=str(tmp_path / "ledger"), flush_batch_size=100,
    )
    entry = _payload("decision_ledger")
    assert writer.write(entry) is True
    assert get_delivery_outbox().records() == ()
    writer.flush()
    rows = get_delivery_outbox().records()
    assert len(rows) == 1
    assert rows[0].delivery_state is DeliveryState.PENDING
    assert rows[0].canonical_ack is None


def test_event_producer_persists_locally_then_uses_outbox(tmp_path, monkeypatch):
    import core.event_stream as stream

    stream.close()
    monkeypatch.setattr(stream, "_EVENT_DIR", tmp_path / "events")
    monkeypatch.setattr(stream, "_get_event_dir", lambda: tmp_path / "events")
    monkeypatch.setattr(stream, "_current_file", None)
    monkeypatch.setattr(stream, "_current_date", None)
    monkeypatch.setattr(stream, "_file_handle", None)
    monkeypatch.setattr(stream, "_enabled", True)
    assert stream.emit_candle(
        "EURUSD", {"ts": 1790942400000, "o": 1.1, "h": 1.2,
                    "l": 1.0, "c": 1.15, "v": 1}, timeframe="M5",
    ) is True
    stream.close()
    local_files = list((tmp_path / "events").glob("*.jsonl"))
    rows = [row for row in get_delivery_outbox().records() if row.dataset == "events"]
    assert len(local_files) == 1 and local_files[0].stat().st_size > 0
    assert len(rows) == 1
    assert rows[0].delivery_state is DeliveryState.PENDING
    assert rows[0].canonical_ack is None


def test_historical_part_000_reader_remains_compatible():
    fake = FakeS3()
    record = _payload("decision_ledger")
    fake.add("decision_ledger", [record], symbol="EURUSD", date=DATE)
    source = S3ResearchDataSource(bucket="test-bucket", client=fake)
    assert source.read_dataset(
        "decision_ledger", symbol="EURUSD", start_date=DATE, end_date=DATE,
    ) == [record]
    assert any(key.endswith("part-000.jsonl") for key in fake.objects)


def test_excursion_checkpoint_is_the_only_noncanonical_direct_s3_exemption():
    root = importlib.util.find_spec("core").submodule_search_locations[0]
    direct = []
    from pathlib import Path
    for path in Path(root).rglob("*.py"):
        if ".put_object(" in path.read_text(encoding="utf-8", errors="ignore"):
            direct.append(path.relative_to(root).as_posix())
    assert direct == [
        "canonical_delivery_worker.py",
        "trade_management/excursion_state.py",
    ]
