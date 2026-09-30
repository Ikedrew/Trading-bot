"""Refinement 2 acceptance tests: immutable Stage 4 population identity."""
from __future__ import annotations

import copy
import dataclasses

import pytest

from research_engine.control_plane import stage4_dataset_snapshot as D
from research_engine.control_plane import stage4_identity as I
from research_engine.control_plane import stage4_observation_closeout as C


_DEFAULT_RECORDS = object()


def _snapshot(*, records=_DEFAULT_RECORDS, **overrides):
    values = {
        "dataset_name": "shadow_runtime",
        "schema_version": "shadow_runtime_v1",
        "schema_generation": 1,
        "generation_state": D.GENERATION_CONFIRMED,
        "generation_evidence": "test authority",
        "identity_grain": "one governed event",
        "identity_grain_evidence": "test contract",
        "source_boundaries": ("object=a.jsonl",),
        "population_filters": ("event_type=CLOSE",),
        "observation_requirements": ("OR-01",),
        "records": (({"symbol": "EURUSD", "time": 1},)
                    if records is _DEFAULT_RECORDS else records),
    }
    values.update(overrides)
    return D.freeze_population(**values)


def _set(snapshot, *, evidence_set_id="ESET-REFINEMENT-2"):
    return D.evidence_set_for_snapshots(
        evidence_set_id=evidence_set_id,
        observation_requirement_ids=("OR-01",),
        snapshots=(snapshot,),
    )


def test_snapshot_identity_is_content_bound_deterministic_and_clock_free():
    first = _snapshot(frozen_at="2026-01-01", created_at="2026-01-01")
    replay = _snapshot(frozen_at="2027-01-01", created_at="2027-01-01")
    changed = _snapshot(records=({"symbol": "EURUSD", "time": 2},))

    assert first.dataset_snapshot_id == replay.dataset_snapshot_id
    assert first.dataset_snapshot_id != changed.dataset_snapshot_id
    assert first.schema_version != first.dataset_snapshot_id
    assert first.dataset_name != first.schema_version


def test_record_order_and_json_key_order_do_not_change_snapshot_identity():
    records = (
        {"symbol": "EURUSD", "time": 1, "value": 2.0},
        {"time": 2, "value": 3.0, "symbol": "GBPUSD"},
    )
    reordered = (
        {"symbol": "GBPUSD", "value": 3.0, "time": 2},
        {"value": 2.0, "time": 1, "symbol": "EURUSD"},
    )
    assert _snapshot(records=records).dataset_snapshot_id == \
        _snapshot(records=reordered).dataset_snapshot_id


@pytest.mark.parametrize("bad", [None, "", "shadow_runtime_v1", "DSNAP-bad"])
def test_snapshot_identity_namespace_fails_closed(bad):
    with pytest.raises(I.Stage4IdentityError):
        I.validate_dataset_snapshot_id(bad, allow_none=False)


def test_mutable_population_has_no_snapshot_identity_and_freezes_explicitly():
    live = D.live_population(
        dataset_name="shadow_runtime", schema_version="shadow_runtime_v1",
        schema_generation=1, observed_record_count=1)
    assert live.dataset_snapshot_id is None
    assert live.to_dict()["snapshot_identity_state"] == \
        I.SNAPSHOT_IDENTITY_UNRESOLVED_LIVE

    frozen = live.freeze(
        records=({"symbol": "EURUSD", "time": 1},),
        identity_grain="one event", source_boundaries=("object=a",),
        population_filters=("all=true",))
    assert frozen.schema_generation == 1
    assert frozen.generation_state == D.GENERATION_CONFIRMED


def test_mutable_or_empty_population_cannot_masquerade_as_snapshot():
    with pytest.raises(D.DatasetSnapshotError):
        _snapshot(population_state=D.POPULATION_MUTABLE)
    with pytest.raises(D.DatasetSnapshotError):
        D.freeze_population(
            dataset_name="shadow_runtime",
            schema_version="shadow_runtime_v1",
            identity_grain="one event", source_boundaries=("object=a",),
            population_filters=("all=true",), records=())


def test_record_byte_scope_requires_actual_records():
    with pytest.raises(D.DatasetSnapshotError,
                       match="RECORD_BYTE_DIGEST_REQUIRES_RECORDS"):
        _snapshot(
            records=None, content_digest="a" * 64, record_count=1,
            content_digest_scope=D.DIGEST_SCOPE_RECORD_BYTES)


def test_snapshot_rejects_malformed_digest_and_incomplete_producer_identity():
    with pytest.raises(D.DatasetSnapshotError,
                       match="INVALID_SNAPSHOT_CONTENT_DIGEST"):
        _snapshot(records=None, content_digest="not-a-sha256", record_count=1)
    with pytest.raises(D.DatasetSnapshotError,
                       match="PRODUCER_IDENTITY_INCOMPLETE"):
        _snapshot(producer_version="producer-v1")
    with pytest.raises(D.DatasetSnapshotError,
                       match="MALFORMED_SNAPSHOT_SCHEMA_VERSION"):
        _snapshot(schema_version="not_a_version")


def test_registry_reload_is_stable_and_conflicting_reuse_fails_closed(tmp_path):
    snapshot = _snapshot()
    registry = D.DatasetSnapshotRegistry((snapshot,))
    path = tmp_path / "registry.json"
    registry.save(path)
    assert D.DatasetSnapshotRegistry.load(path).require(
        snapshot.dataset_snapshot_id) == snapshot

    conflict = dataclasses.replace(
        snapshot, producer_version="other", producer_fingerprint="f" * 64,
        producer_identity_state=D.PRODUCER_BOUND)
    with pytest.raises(D.DatasetSnapshotError,
                       match="CONFLICTING_DATASET_SNAPSHOT_PRODUCER"):
        registry.add(conflict)
    with pytest.raises(D.DatasetSnapshotError):
        registry.add_persisted({"dataset_snapshot_id": snapshot.dataset_snapshot_id})


def test_population_bound_evidence_round_trips_without_losing_membership():
    snapshot = _snapshot()
    evidence = _set(snapshot)
    loaded = I.EvidenceSet.from_dict(evidence.to_dict())
    assert loaded == evidence
    assert loaded.members[0].dataset_snapshot_id == snapshot.dataset_snapshot_id
    assert D.require_evidence_identity(
        loaded, registry=D.DatasetSnapshotRegistry((snapshot,))).valid


def test_legacy_evidence_identity_remains_byte_compatible_but_unresolved():
    member = I.EvidenceMemberReference(
        reference_type="governed_dataset_slice", reference_id="legacy",
        dataset="shadow_runtime", locator="schema_generation=1",
        content_fingerprint="f" * 64)
    evidence = I.EvidenceSet(
        "ESET-LEGACY", ("OR-01",), (member,))
    persisted = evidence.to_dict()
    assert "dataset_snapshot_id" not in persisted["evidence_member_references"][0]
    assert "dataset_snapshot_ids" not in persisted
    assert persisted["content_fingerprint"] == I.fingerprint({
        "observation_requirement_ids": ("OR-01",),
        "members": [member.to_dict()],
    })
    verdict = D.validate_evidence_identity(
        I.EvidenceSet.from_dict(persisted),
        registry=D.DatasetSnapshotRegistry(), require_dataset_snapshots=False)
    assert verdict.state == I.LEGACY_UNRESOLVED_SNAPSHOT_IDENTITY
    assert verdict.valid is False
    with pytest.raises(D.DatasetSnapshotError):
        verdict.require_valid()


def test_unknown_snapshot_and_member_dataset_mismatch_fail_read_side_validation():
    unknown = "DSNAP-" + "0" * 24
    evidence = I.EvidenceSet(
        "ESET-UNKNOWN", ("OR-01",),
        (I.EvidenceMemberReference(
            reference_type="dataset_snapshot", reference_id=unknown,
            dataset="shadow_runtime", dataset_snapshot_id=unknown),),
        (unknown,))
    verdict = D.validate_evidence_identity(
        evidence, registry=D.DatasetSnapshotRegistry())
    assert verdict.state == D.SNAPSHOT_UNKNOWN
    assert not verdict.valid

    snapshot = _snapshot()
    wrong_dataset = I.EvidenceSet(
        "ESET-WRONG-DATASET", ("OR-01",),
        (I.EvidenceMemberReference(
            reference_type="dataset_snapshot",
            reference_id=snapshot.dataset_snapshot_id,
            dataset="decision_trace",
            content_fingerprint=snapshot.content_digest,
            dataset_snapshot_id=snapshot.dataset_snapshot_id),),
        (snapshot.dataset_snapshot_id,))
    assert D.validate_evidence_identity(
        wrong_dataset,
        registry=D.DatasetSnapshotRegistry((snapshot,))).state == \
        D.MEMBER_DATASET_MISMATCH


def test_every_declared_snapshot_must_have_an_evidence_member():
    snapshot = _snapshot()
    other = _snapshot(records=({"symbol": "EURUSD", "time": 2},))
    member = D.snapshot_member(snapshot)
    with pytest.raises(I.Stage4IdentityError,
                       match="DATASET_SNAPSHOT_WITHOUT_EVIDENCE_MEMBER"):
        I.EvidenceSet(
            "ESET-EXTRA-SNAPSHOT", ("OR-01",), (member,),
            (snapshot.dataset_snapshot_id, other.dataset_snapshot_id))


def test_schema_generation_digest_and_producer_mismatches_fail_closed():
    snapshot = _snapshot(
        producer_version="producer-v1", producer_fingerprint="f" * 64)
    evidence = _set(snapshot)
    registry = D.DatasetSnapshotRegistry((snapshot,))
    assert D.validate_evidence_identity(
        evidence, registry=registry,
        required_schema_version="shadow_runtime_v2").state == D.SCHEMA_MISMATCH
    assert D.validate_evidence_identity(
        evidence, registry=registry,
        required_schema_generation=2).state == D.GENERATION_MISMATCH
    assert D.validate_evidence_identity(
        evidence, registry=registry,
        required_producers={"shadow_runtime": "producer-v2"}).state == \
        D.PRODUCER_MISMATCH
    assert D.validate_evidence_identity(
        evidence, registry=registry,
        required_producers={"decision_trace": "producer-v1"}).state == \
        D.SNAPSHOT_DATASET_MISMATCH

    bad_member = dataclasses.replace(
        evidence.members[0], content_fingerprint="0" * 64)
    tampered = I.EvidenceSet(
        "ESET-BAD-DIGEST", ("OR-01",), (bad_member,),
        evidence.dataset_snapshot_ids)
    assert D.validate_evidence_identity(
        tampered, registry=registry).state == D.CONTENT_DIGEST_MISMATCH


def test_bootstrap_store_persists_reloads_and_detects_tampering(tmp_path):
    store = D.build_store()
    assert store["dataset_snapshot_count"] == 5
    assert all(item["validation_state"] == D.IDENTITY_VERIFIED
               for item in store["read_side_validation"].values())
    path = tmp_path / "snapshot-state.json"
    D.save_store(store, path)
    assert D.load_canonical_state(path) == store

    tampered = copy.deepcopy(store)
    tampered["limitations"].append("silent mutation")
    with pytest.raises(D.DatasetSnapshotError,
                       match="SNAPSHOT_STORE_FINGERPRINT_CHANGED"):
        D.validate_store(tampered)


def test_frozen_epochs_are_bound_and_live_epochs_remain_unbound():
    snapshots = D.bootstrap_registry()
    matrix = D._read_json(I.REQUIREMENT_MATRIX_PATH)
    versions = C.build_version_registry(matrix, snapshots)
    for epoch in versions.epochs():
        if epoch.status in D.FROZEN_EPOCH_STATUSES:
            assert epoch.dataset_snapshot_ids
            assert D.validate_epoch_evidence_identity(
                epoch, registry=snapshots).valid
        else:
            assert epoch.dataset_snapshot_ids == ()
            assert epoch.snapshot_identity_state == \
                I.SNAPSHOT_IDENTITY_UNRESOLVED_LIVE


def test_bootstrap_preserves_stage4_conservation_and_performs_no_mutation():
    store = D.build_store()
    assert store["authority_conservation"]["observation_requirements"] == 15
    assert store["authority_conservation"]["governed_gaps"] == 31
    assert set(store["mutation_ledger"].values()) == {0}
    assert len(D.bootstrap_registry().snapshots_for_requirement("OR-14")) >= 1
    assert len(D.bootstrap_registry().snapshots_for_requirement("OR-15")) >= 1
