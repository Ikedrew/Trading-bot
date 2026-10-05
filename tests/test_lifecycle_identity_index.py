from concurrent.futures import ThreadPoolExecutor

from core.lifecycle_evidence_obligations import (
    LifecycleEvidenceLedger, ObligationStatus, begin_active_cycle,
)


def create(ledger, event, correlation):
    return begin_active_cycle(
        ledger, event_id=event, symbol="EURUSD", cycle_id=1,
        entity_id=event, correlation_id=correlation,
        timestamp="2026-10-01T10:00:00Z",
    )[0]


def test_lookup_visits_only_exact_identity_bucket(tmp_path):
    ledger = LifecycleEvidenceLedger(tmp_path / "ledger.jsonl")
    target = create(ledger, "target", "C")
    unrelated = create(ledger, "unrelated", "OTHER")
    key = ledger._identity_bucket("execution_context", "correlation_id",
                                 unrelated.expected_identity)

    class Unreadable(dict):
        def values(self):
            raise AssertionError("unrelated history scanned")

    ledger._by_key[key] = Unreadable(ledger._by_key[key])
    assert ledger.find_exact("execution_context", {"correlation_id": "C"}) == (target,)
    assert ledger.find_exact("execution_context", {"correlation_id": "missing"}) == ()


def test_ambiguity_ordering_latest_revision_and_restart(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = LifecycleEvidenceLedger(path)
    first = create(ledger, "first", "C")
    second = create(ledger, "second", "C")
    assert create(ledger, "first", "C") == first
    updated = ledger.update(first.obligation_id, ObligationStatus.PRESENT,
                            observed_record_id="record")
    expected = tuple(sorted((updated, second), key=lambda item: item.obligation_id))
    assert ledger.find_exact("execution_context", {"correlation_id": "C"}) == expected
    assert LifecycleEvidenceLedger(path).find_exact(
        "execution_context", {"correlation_id": "C"}) == expected


def test_failed_append_keeps_index_and_latest_unchanged(tmp_path, monkeypatch):
    ledger = LifecycleEvidenceLedger(tmp_path / "ledger.jsonl")
    original = create(ledger, "event", "C")

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(ledger, "_append", fail)
    import pytest
    with pytest.raises(OSError):
        ledger.update(original.obligation_id, ObligationStatus.PRESENT)
    assert ledger.find_exact("execution_context", {"correlation_id": "C"}) == (original,)


def test_concurrent_updates_and_lookup_return_one_latest_record(tmp_path):
    ledger = LifecycleEvidenceLedger(tmp_path / "ledger.jsonl")
    original = create(ledger, "event", "C")

    def run(index):
        if index % 2:
            ledger.update(original.obligation_id, ObligationStatus.NOT_YET_DUE)
        result = ledger.find_exact("execution_context", {"correlation_id": "C"})
        assert len(result) == 1
        assert result[0].obligation_id == original.obligation_id

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run, range(24)))
    reopened = LifecycleEvidenceLedger(ledger.path)
    assert reopened.find_exact("execution_context", {"correlation_id": "C"}) == (
        ledger.get(original.obligation_id),)
