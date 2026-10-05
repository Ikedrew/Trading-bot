from __future__ import annotations

from datetime import date
import json

from core.production_data_contract import current_schema, s3_base_prefix
from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.v10.continuous.frontier_coordinator import (
    DATASET_SCOPE,
    FRONTIER_INCOMPLETE,
    FRONTIER_INVALID,
    NO_NEW_GOVERNED_EVIDENCE,
    SNAPSHOT_READY,
    FrontierStateStore,
    run_frontier_snapshot_cycle,
)
from research_engine.v10.investigation_snapshot import (
    BOUND_DATASETS,
    OPTIONAL_DATASETS,
    REQUIRED_DATASETS,
    freeze_investigation_snapshot,
    load_investigation_snapshot_id,
)


def _key(dataset: str, day: str = "2026-09-25", part: str = "part-000.jsonl") -> str:
    return (
        f"{s3_base_prefix(dataset)}/schema_version={current_schema(dataset)}"
        f"/symbol=EURUSD/date={day}/{part}"
    )


def _body(dataset: str, marker: str = "base") -> str:
    return json.dumps({
        "schema_version": current_schema(dataset),
        "dataset": dataset,
        "marker": marker,
        "timestamp_utc": "2026-09-25T12:00:00Z",
    }, sort_keys=True) + "\n"


def _objects(day: str = "2026-09-25") -> dict[str, str]:
    return {_key(dataset, day): _body(dataset) for dataset in BOUND_DATASETS}


class MemoryS3:
    def __init__(self, objects: dict[str, str], versions: dict[str, str] | None = None):
        self.objects = dict(objects)
        self.versions = dict(versions or {})
        self.get_calls = 0
        self.head_calls = 0

    def list_objects_v2(self, **kwargs):
        import hashlib

        prefix = str(kwargs.get("Prefix") or "")
        return {
            "IsTruncated": False,
            "Contents": [
                {
                    "Key": key,
                    "ETag": '"' + hashlib.md5(body.encode()).hexdigest() + '"',
                    "Size": len(body.encode()),
                    "LastModified": "2026-09-25T13:00:00Z",
                }
                for key, body in sorted(self.objects.items()) if key.startswith(prefix)
            ],
        }

    def get_object(self, **kwargs):
        import hashlib

        self.get_calls += 1
        key = str(kwargs["Key"])
        if key not in self.objects:
            raise KeyError(key)
        body = self.objects[key]

        class Body:
            def __init__(self):
                self.remaining = body.encode()

            def read(self, amount=-1):
                if not self.remaining:
                    return b""
                size = len(self.remaining) if amount < 0 else min(amount, 17)
                chunk, self.remaining = self.remaining[:size], self.remaining[size:]
                return chunk

        response = {
            "Body": Body(),
            "ETag": '"' + hashlib.md5(body.encode()).hexdigest() + '"',
            "ContentLength": len(body.encode()),
            "LastModified": "2026-09-25T13:00:00Z",
        }
        if key in self.versions:
            response["VersionId"] = self.versions[key]
        return response

    def head_object(self, **kwargs):
        import hashlib

        self.head_calls += 1
        key = str(kwargs["Key"])
        if key not in self.objects:
            raise KeyError(key)
        body = self.objects[key]
        response = {
            "ETag": '"' + hashlib.md5(body.encode()).hexdigest() + '"',
            "ContentLength": len(body.encode()),
            "LastModified": "2026-09-25T13:00:00Z",
        }
        if key in self.versions:
            response["VersionId"] = self.versions[key]
        return response


def _source(fake: MemoryS3) -> S3ResearchDataSource:
    return S3ResearchDataSource(bucket="frontier-test", client=fake)


def _run(tmp_path, fake: MemoryS3, **kwargs):
    return run_frontier_snapshot_cycle(
        source=_source(fake),
        state_directory=tmp_path / "state",
        manifest_directory=tmp_path / "manifests",
        as_of_date=date(2026, 10, 3),
        **kwargs,
    )


def test_scope_reuses_investigation_policy_and_excludes_local_candidates():
    assert {name for name, value in DATASET_SCOPE.items()
            if value["classification"] == "REQUIRED"} == set(REQUIRED_DATASETS)
    assert set(OPTIONAL_DATASETS) <= {
        name for name, value in DATASET_SCOPE.items()
        if value["classification"] == "OPTIONAL"
    }
    assert DATASET_SCOPE["opportunities"]["classification"] == "EXCLUDED"
    assert DATASET_SCOPE["assessments"]["classification"] == "EXCLUDED"
    assert DATASET_SCOPE["decision_ledger"]["classification"] == "EXCLUDED"
    assert DATASET_SCOPE["portfolio_rankings"]["classification"] == "EXCLUDED"
    assert DATASET_SCOPE["shadow_candidate"]["classification"] == "EXCLUDED"
    assert DATASET_SCOPE["shadow_candidate_evaluation"]["classification"] == "EXCLUDED"


def test_first_cycle_freezes_exact_membership_updates_pointer_and_rerun_is_noop(tmp_path):
    fake = MemoryS3(_objects())
    calls = []

    def freezer(**kwargs):
        calls.append(kwargs)
        return freeze_investigation_snapshot(**kwargs)

    first = _run(tmp_path, fake, freezer=freezer)
    assert first.status == SNAPSHOT_READY
    assert first.verification_status == "VERIFIED"
    assert first.predecessor_snapshot_id is None
    assert set(first.changed_datasets) == set(BOUND_DATASETS)
    assert first.new_object_count == len(BOUND_DATASETS)
    assert first.replaced_object_count == 0
    assert len(calls) == 1

    snapshot = load_investigation_snapshot_id(
        first.snapshot_id, manifest_directory=tmp_path / "manifests")
    assert {binding.dataset: [obj.identifier for obj in binding.objects]
            for binding in snapshot.datasets} == {
        dataset: [_key(dataset)] for dataset in BOUND_DATASETS
    }
    state = FrontierStateStore(tmp_path / "state").load_latest_success()
    assert state["last_successful_snapshot_id"] == first.snapshot_id
    assert state["latest_success_pointer"]["frontier_id"] == first.frontier_id

    gets_after_first = fake.get_calls
    second = _run(tmp_path, fake, freezer=freezer)
    assert second.status == NO_NEW_GOVERNED_EVIDENCE
    assert second.snapshot_id == first.snapshot_id
    assert second.changed_datasets == ()
    assert set(second.unchanged_datasets) == set(BOUND_DATASETS)
    assert len(calls) == 1
    assert fake.get_calls == gets_after_first
    assert fake.head_calls == len(BOUND_DATASETS)
    assert FrontierStateStore(tmp_path / "state").load_latest_success() == state


def test_same_row_count_changed_digest_is_replacement_and_preserves_predecessor(tmp_path):
    fake = MemoryS3(_objects())
    first = _run(tmp_path, fake)
    key = _key("trade_truth")
    original_rows = len(fake.objects[key].splitlines())
    fake.objects[key] = _body("trade_truth", marker="changed")
    assert len(fake.objects[key].splitlines()) == original_rows

    second = _run(tmp_path, fake)
    assert second.status == SNAPSHOT_READY
    assert second.predecessor_snapshot_id == first.snapshot_id
    assert second.changed_datasets == ("trade_truth",)
    assert second.new_object_count == 0
    assert second.replaced_object_count == 1
    assert second.delta["datasets"]["trade_truth"]["row_count_delta"] == 0


def test_changed_version_id_is_replacement(tmp_path):
    key = _key("execution_results")
    fake = MemoryS3(_objects(), versions={key: "v1"})
    _run(tmp_path, fake)
    fake.versions[key] = "v2"

    result = _run(tmp_path, fake)
    assert result.status == SNAPSHOT_READY
    assert result.changed_datasets == ("execution_results",)
    assert result.replaced_object_count == 1


def test_added_object_is_new_evidence_with_time_and_row_delta(tmp_path):
    fake = MemoryS3(_objects())
    _run(tmp_path, fake)
    key = _key("trade_truth", part="part-001.jsonl")
    fake.objects[key] = _body("trade_truth", "added")

    result = _run(tmp_path, fake)
    assert result.status == SNAPSHOT_READY
    assert result.changed_datasets == ("trade_truth",)
    assert result.new_object_count == 1
    delta = result.delta["datasets"]["trade_truth"]
    assert delta["row_count_delta"] == 1
    assert delta["time_coverage_after"] == {
        "start": "2026-09-25", "end": "2026-09-25"}


def test_new_partition_is_detected_but_not_consumed_until_required_frontier_catches_up(tmp_path):
    fake = MemoryS3(_objects())
    first = _run(tmp_path, fake)
    fake.objects[_key("trade_truth", "2026-09-26")] = _body("trade_truth", "leading")

    pending = _run(tmp_path, fake)
    assert pending.status == FRONTIER_INCOMPLETE
    assert pending.snapshot_id == first.snapshot_id
    assert pending.verification_status == "RETAINED_VERIFIED"
    assert pending.candidate_status == "CANDIDATE_INCOMPLETE"
    assert pending.candidate_frontier_end == "2026-09-26"
    assert pending.pending_missing_datasets == tuple(sorted(
        set(REQUIRED_DATASETS) - {"trade_truth"}))
    assert pending.failure_reason.startswith(
        "REQUIRED_DATASETS_NOT_YET_COHERENT_AT_NEWEST_COVERAGE:")
    assert FrontierStateStore(tmp_path / "state").load_latest_success()[
        "last_successful_snapshot_id"] == first.snapshot_id

    for dataset in REQUIRED_DATASETS:
        fake.objects.setdefault(
            _key(dataset, "2026-09-26"), _body(dataset, "caught-up"))
    advanced = _run(tmp_path, fake)
    assert advanced.status == SNAPSHOT_READY
    assert advanced.frontier_end == "2026-09-26"
    assert advanced.predecessor_snapshot_id == first.snapshot_id
    candidate = FrontierStateStore(tmp_path / "state").load_latest_candidate()
    assert candidate["candidate_status"] == "PROMOTED"
    assert candidate["promoted_snapshot_id"] == advanced.snapshot_id
    assert candidate["missing_required_datasets"] == []


def test_short_runtime_partial_delivery_retains_coherent_snapshot_and_objects(tmp_path):
    fake = MemoryS3(_objects())
    first = _run(tmp_path, fake)
    manifest = tmp_path / "manifests" / f"{first.snapshot_id}.json"
    before = manifest.read_bytes()
    shadow_key = _key("shadow_runtime", "2026-09-26")
    context_key = _key("execution_context", "2026-09-26")
    fake.objects[shadow_key] = _body("shadow_runtime", "short-session")
    fake.objects[context_key] = _body("execution_context", "short-session")

    pending = _run(tmp_path, fake)

    assert pending.status == FRONTIER_INCOMPLETE
    assert pending.snapshot_id == first.snapshot_id
    assert pending.changed_datasets == ()
    assert pending.pending_missing_datasets == tuple(sorted(
        set(REQUIRED_DATASETS) - {"shadow_runtime", "execution_context"}))
    assert set(pending.pending_required_objects) == {
        "shadow_runtime", "execution_context"}
    assert fake.objects[shadow_key]
    assert fake.objects[context_key]
    assert manifest.read_bytes() == before
    candidate = FrontierStateStore(tmp_path / "state").load_latest_candidate()
    assert candidate["candidate_status"] == "CANDIDATE_INCOMPLETE"
    assert candidate["last_coherent_snapshot_id"] == first.snapshot_id

    for dataset in REQUIRED_DATASETS:
        fake.objects.setdefault(
            _key(dataset, "2026-09-26"), _body(dataset, "delayed-member"))
    promoted = _run(tmp_path, fake)
    assert promoted.status == SNAPSHOT_READY
    assert promoted.predecessor_snapshot_id == first.snapshot_id
    assert promoted.frontier_end == "2026-09-26"
    assert FrontierStateStore(tmp_path / "state").load_latest_candidate()[
        "candidate_status"] == "PROMOTED"


def test_unchanged_optional_dataset_causes_no_false_positive(tmp_path):
    fake = MemoryS3(_objects())
    first = _run(tmp_path, fake)
    second = _run(tmp_path, fake)
    assert first.status == SNAPSHOT_READY
    assert second.status == NO_NEW_GOVERNED_EVIDENCE
    assert set(OPTIONAL_DATASETS) <= set(second.unchanged_datasets)


def test_required_overlap_selects_common_range_and_reports_partition_gaps(tmp_path):
    objects = _objects("2026-09-25")
    for dataset in REQUIRED_DATASETS:
        objects[_key(dataset, "2026-09-24")] = _body(dataset, "older")
        objects[_key(dataset, "2026-09-26")] = _body(dataset, "newer")
    result = _run(tmp_path, MemoryS3(objects))
    assert result.status == SNAPSHOT_READY
    assert result.frontier_start == "2026-09-24"
    assert result.frontier_end == "2026-09-26"


def test_missing_required_fails_loud_without_snapshot_or_pointer(tmp_path):
    objects = _objects()
    objects.pop(_key("trade_truth"))
    result = _run(tmp_path, MemoryS3(objects))
    assert result.status == FRONTIER_INCOMPLETE
    assert result.failure_reason == "REQUIRED_DATASET_ABSENT:trade_truth"
    assert not (tmp_path / "state" / "latest_success.json").exists()
    assert not list((tmp_path / "manifests").glob("*.json")) if (tmp_path / "manifests").exists() else True


def test_nonoverlapping_required_coverage_is_incomplete(tmp_path):
    objects = _objects("2026-09-25")
    objects.pop(_key("trade_truth", "2026-09-25"))
    objects[_key("trade_truth", "2026-09-20")] = _body("trade_truth")
    result = _run(tmp_path, MemoryS3(objects))
    assert result.status == FRONTIER_INCOMPLETE
    assert result.failure_reason.startswith("REQUIRED_DATASET_COVERAGE_DOES_NOT_OVERLAP:")
    assert result.snapshot_id is None


def test_optional_missing_and_stale_do_not_block_and_are_explicit(tmp_path):
    objects = _objects()
    objects.pop(_key("market_context"))
    objects.pop(_key("strategy_observations"))
    objects[_key("strategy_observations", "2026-09-20")] = _body("strategy_observations")
    result = _run(tmp_path, MemoryS3(objects))
    assert result.status == SNAPSHOT_READY
    assert result.dataset_status["market_context"] == "MISSING"
    assert result.dataset_status["strategy_observations"] == "STALE"
    assert result.missing_optional_datasets == ("market_context",)
    assert result.stale_datasets == ("strategy_observations",)


def test_future_dated_required_evidence_fails_closed(tmp_path):
    objects = _objects()
    objects[_key("trade_truth", "2026-10-04")] = _body("trade_truth", "future")
    result = _run(tmp_path, MemoryS3(objects))
    assert result.status == FRONTIER_INVALID
    assert result.failure_reason.startswith("FUTURE_DATED_GOVERNED_EVIDENCE:trade_truth:")
    assert not (tmp_path / "state" / "latest_success.json").exists()


def test_future_dated_required_record_fails_closed_even_in_valid_partition(tmp_path):
    objects = _objects()
    objects[_key("decision_trace")] = json.dumps({
        "schema_version": current_schema("decision_trace"),
        "timestamp_utc": "2026-10-04T00:00:00Z",
    }) + "\n"
    result = _run(tmp_path, MemoryS3(objects))
    assert result.status == FRONTIER_INVALID
    assert result.failure_reason.startswith(
        "FUTURE_DATED_GOVERNED_RECORD:decision_trace:")
    assert not (tmp_path / "state" / "latest_success.json").exists()


def test_verification_failure_does_not_advance_consumed_frontier(tmp_path):
    fake = MemoryS3(_objects())
    first = _run(tmp_path, fake)
    first_state = FrontierStateStore(tmp_path / "state").load_latest_success()
    fake.objects[_key("trade_truth")] = _body("trade_truth", "changed")

    def broken_verifier(*args, **kwargs):
        raise RuntimeError("forced verification failure")

    failed = _run(tmp_path, fake, verifier=broken_verifier)
    assert failed.status == FRONTIER_INVALID
    assert "forced verification failure" in failed.failure_reason
    assert FrontierStateStore(tmp_path / "state").load_latest_success() == first_state
    assert failed.snapshot_id == first.snapshot_id


def test_restart_reloads_state_and_new_evidence_creates_exactly_one_snapshot(tmp_path):
    fake = MemoryS3(_objects())
    first = _run(tmp_path, fake)
    unchanged_after_restart = _run(tmp_path, fake)
    assert unchanged_after_restart.status == NO_NEW_GOVERNED_EVIDENCE
    fake.objects[_key("execution_context", part="part-001.jsonl")] = _body(
        "execution_context", "new")
    advanced = _run(tmp_path, fake)
    final_noop = _run(tmp_path, fake)
    assert advanced.status == SNAPSHOT_READY
    assert advanced.predecessor_snapshot_id == first.snapshot_id
    assert final_noop.status == NO_NEW_GOVERNED_EVIDENCE
    assert final_noop.snapshot_id == advanced.snapshot_id
    assert len(list((tmp_path / "state" / "history").glob("*.json"))) == 2


def test_failed_first_run_does_not_consume_then_repair_creates_first_snapshot(tmp_path):
    objects = _objects()
    missing = objects.pop(_key("execution_attempts"))
    fake = MemoryS3(objects)
    failed = _run(tmp_path, fake)
    assert failed.status == FRONTIER_INCOMPLETE
    fake.objects[_key("execution_attempts")] = missing
    repaired = _run(tmp_path, fake)
    assert repaired.status == SNAPSHOT_READY
    assert repaired.predecessor_snapshot_id is None
