from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from core.production_data_contract import current_schema, s3_base_prefix
from research_engine.data_access.s3_source import (
    ResearchDataSourceError,
    S3ResearchDataSource,
)
from research_engine.v10.investigation_snapshot import (
    BOUND_DATASETS,
    EXCLUDED_BY_DESIGN,
    InvestigationSnapshot,
    InvestigationSnapshotError,
    SnapshotBoundDatasetReader,
    freeze_investigation_snapshot,
    load_investigation_snapshot,
    save_investigation_snapshot,
)
from research_engine.v10.investigation_views import (
    InvestigationFilters,
    InvestigationViews,
)


class MemoryS3:
    def __init__(self, objects: dict[str, str]):
        self.objects = dict(objects)
        self.list_calls: list[str] = []
        self.get_calls: list[str] = []

    def list_objects_v2(self, **kwargs):
        prefix = str(kwargs.get("Prefix") or "")
        self.list_calls.append(prefix)
        return {
            "IsTruncated": False,
            "Contents": [
                {
                    "Key": key,
                    "ETag": '"' + hashlib.md5(body.encode()).hexdigest() + '"',
                    "Size": len(body.encode()),
                    "LastModified": "2026-10-01T00:00:00Z",
                }
                for key, body in sorted(self.objects.items())
                if key.startswith(prefix)
            ],
        }

    def get_object(self, **kwargs):
        key = str(kwargs["Key"])
        self.get_calls.append(key)
        if key not in self.objects:
            raise KeyError(key)
        body = self.objects[key]

        class Body:
            def read(self):
                return body.encode("utf-8")

        return {
            "Body": Body(),
            "ETag": '"' + hashlib.md5(body.encode()).hexdigest() + '"',
            "ContentLength": len(body.encode()),
            "LastModified": "2026-10-01T00:00:00Z",
        }


def _key(dataset: str, day: str = "2026-09-25", part: str = "part-000.jsonl") -> str:
    return (
        f"{s3_base_prefix(dataset)}/schema_version={current_schema(dataset)}"
        f"/symbol=EURUSD/date={day}/{part}"
    )


def _jsonl(*rows: dict) -> str:
    return "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)


def _objects() -> dict[str, str]:
    from test_investigation_views import shadow_events

    correlation = "C1"
    common = {"schema_version": "shadow_runtime_v1", "shadow_trade_id": "S1",
              "canonical_opportunity_id": "O1", "symbol": "EURUSD", "horizon": "SCALP"}
    opened = {
        **common, "event_type": "OPEN", "entry_market_time_utc_epoch_s": 1767225600,
        "entry_market_time_utc_iso8601": "2026-01-01T00:00:00Z",
        "identity": {"entity_id": "E1", "cycle_id": 1, "trade_horizon": "SCALP",
                     "evaluated_horizon": "SCALP", "shadow_type": "TREATMENT"},
        "construction": {"direction": "BUY", "entry_price": 1.1, "stop_loss": 1.09,
                          "take_profit": 1.12, "risk_distance": 0.01, "intended_rr": 2.0},
        "live_facts": {"strategy": "REVERSAL", "pattern": "HAMMER", "regime": "TRENDING"},
    }
    closed = {
        **common, "event_type": "CLOSE", "exit_market_time_utc_epoch_s": 1767229200,
        "exit_market_time_utc_iso8601": "2026-01-01T01:00:00Z",
        "exit_reason": "take_profit", "exit_price": 1.12, "bars_held": 12,
        "outcome": {"pnl_r_multiple": 2.0, "mfe_r": 2.1, "mae_r": -0.2,
                    "risk_distance": 0.01, "intended_rr": 2.0},
    }
    rows = {
        "trade_truth": ({
            "schema_version": "trade_truth_v1",
            "symbol": "EURUSD",
            "identity": {"trade_id": "T1", "correlation_id": correlation,
                         "account_id": "A1", "position_ticket": 1,
                         "canonical_opportunity_id": "O1", "symbol": "EURUSD",
                         "broker": "B1", "broker_server": "S1"},
            "execution": {"entry_fill_price": 1.1, "exit_fill_price": 1.12,
                          "volume_executed": 0.1},
            "timestamps": {"entry_timestamp_broker": "2026-09-25T09:00:00Z",
                           "exit_timestamp_broker": "2026-09-25T10:00:00Z",
                           "duration_seconds": 3600},
            "outcome": {"r_multiple_realised": 2.0, "pnl_realised": 20.0,
                        "net_profit": 19.0, "mfe_r": 2.1, "mae_r": -0.2},
            "exit": {"exit_reason": "take_profit_hit"},
        },),
        "execution_results": ({"schema_version": "execution_results_v1",
                                "symbol": "EURUSD",
                                "correlation_id": correlation, "account_id": "A1",
                                "entity_id": "E1", "position_ticket": 1,
                                "result_ok": True, "retcode": 0,
                                "deal_ticket": "D1",
                                "timestamp_utc": "2026-09-25T09:00:00Z",
                                "comment": "filled",
                                "request": {"volume": 0.1},
                                "submission": {"volume": 0.1},
                                "response": {"retcode": 0, "comment": "filled"},
                                "fill": {"price": 1.1},
                                "protection_confirmation": {"source": None}},),
        "decision_trace": ({"schema_version": "decision_trace_v1", "decision_id": "D1", "entity_id": "E1",
                             "correlation_id": correlation, "symbol": "EURUSD",
                             "cycle_id": 1, "action": "EXECUTE",
                             "timestamp_utc": "2026-09-25T09:00:00Z",
                             "v10_strategy": {"family": "REVERSAL", "direction": "BUY"},
                             "v10_market_state": {"market_phase": "IMPULSE",
                                                  "regime": {"regime": "TRENDING"}}},),
        "shadow_runtime": tuple(shadow_events()),
        "execution_attempts": ({"schema_version": "execution_attempts_v1", "attempt_id": "AT1", "trade_id": "T1",
                                 "correlation_id": correlation, "account_id": "A1",
                                 "position_ticket": 1, "symbol": "EURUSD",
                                 "action_type": "ENTRY",
                                 "timestamp_utc": "2026-09-25T09:00:00Z",
                                 "broker_result": {"ok": True, "retcode": 0}},),
        "execution_context": ({"schema_version": "execution_context_v1", "correlation_id": correlation, "entity_id": "E1",
                                "symbol": "EURUSD", "cycle_id": 1,
                                "timestamp_utc": "2026-09-25T09:00:00Z",
                                "market_access": {"session_state": "LONDON"},
                                "risk_environment": {"open_positions": 0}},),
        "market_context": ({"schema_version": "market_context_v1", "symbol": "EURUSD", "cycle_id": 1,
                             "entity_id": "E1", "regime": "TRENDING"},),
        "strategy_observations": ({"schema_version": "strategy_observation_v1", "entity_id": "E1", "observation_id": "SO1",
                                    "symbol": "EURUSD", "canonical_opportunity_id": "O1",
                                    "family": "REVERSAL"},),
        "protection_audit": ({"schema_version": "protection_audit_v1", "audit_id": "PA1", "correlation_id": correlation,
                               "symbol": "EURUSD", "account_id": "A1", "position_ticket": 1,
                               "protection_status": "VERIFIED"},),
        "risk_deviation": ({"schema_version": "risk_deviation_v1", "observation_id": "RD1", "trade_id": "T1",
                             "symbol": "EURUSD", "correlation_id": correlation,
                             "risk_classification": "NORMAL",
                             "semantic_stage": "post_outcome_analysis",
                             "authority": "diagnostic_projection"},),
    }
    return {_key(name): _jsonl(*rows[name]) for name in BOUND_DATASETS}


def _source(objects=None):
    fake = MemoryS3(_objects() if objects is None else objects)
    return fake, S3ResearchDataSource(bucket="test-bucket", client=fake)


def _freeze(source, **kwargs):
    return freeze_investigation_snapshot(
        start_date="2026-09-23", end_date="2026-09-30", source=source,
        **kwargs,
    )


def _historical_guard() -> dict:
    return {
        "schema_version": "decision_trace_v1",
        "symbol": "EURUSD", "record_role": "runtime_guard_rejection",
        "event_type": "RISK_REJECTION", "rejection_type": "RISK_GUARD",
        "guard": "risk_limit", "reason": "limit reached",
        "should_trade": False, "cycle_id": 2,
        "correlation_id": "GUARD-C2",
        "timestamp_utc": "2026-09-25T09:01:00Z",
    }


def test_historical_guard_cohort_is_counted_but_not_decision_evidence(tmp_path):
    objects = _objects()
    guard_key = _key("decision_trace", part="guard.jsonl")
    original_body = _jsonl(_historical_guard())
    objects[guard_key] = original_body
    fake, source = _source(objects)

    snapshot = _freeze(source, manifest_path=tmp_path / "cohort.json")
    binding = next(item for item in snapshot.datasets
                   if item.dataset == "decision_trace")
    assert binding.source_row_count == 2
    assert binding.historical_guard_rows == 1
    assert binding.canonical_row_count == 1
    assert sum(item.historical_guard_rows for item in binding.objects) == 1
    assert json.loads(binding.dataset_snapshot_json)["record_count"] == 1
    assert load_investigation_snapshot(tmp_path / "cohort.json") == snapshot
    assert fake.objects[guard_key] == original_body

    reader = SnapshotBoundDatasetReader(snapshot, source)
    assert len(reader.read_dataset("decision_trace")) == 1
    assert reader.reads_by_dataset["decision_trace"] == 1
    assert reader.read_dataset("decision_trace")[0]["entity_id"] == "E1"


def test_closed_frontier_roster_preserves_guard_accounting():
    objects = _objects()
    guard_key = _key("decision_trace", part="guard.jsonl")
    objects[guard_key] = _jsonl(_historical_guard())
    _, source = _source(objects)
    membership = {}
    for name in BOUND_DATASETS:
        listed = source.discover_dataset_objects(name)
        source.read_objects_for_freeze(
            name, listed, expected_schema_version=current_schema(name))
        membership[name] = tuple({
            **item,
            "historical_guard_rows": source.guard_cohort_count(
                name, item["identifier"]),
        } for item in source.object_metadata(name))

    snapshot = _freeze(source, object_membership=membership)
    binding = next(item for item in snapshot.datasets
                   if item.dataset == "decision_trace")
    assert binding.source_row_count == 2
    assert binding.historical_guard_rows == 1
    assert binding.canonical_row_count == 1
    assert len(SnapshotBoundDatasetReader(snapshot, source).read_dataset(
        "decision_trace")) == 1


def test_historical_guard_cohort_must_be_nonvacuous_and_exact(tmp_path):
    objects = _objects()
    canonical_key = _key("decision_trace")
    guard_key = _key("decision_trace", part="guard.jsonl")
    objects[guard_key] = _jsonl(_historical_guard())
    _, source = _source(objects)
    snapshot = _freeze(source)
    altered = snapshot.to_dict()
    binding = next(item for item in altered["datasets"]
                   if item["dataset"] == "decision_trace")
    next(item for item in binding["objects"]
         if item["identifier"] == guard_key)["historical_guard_rows"] = 0
    with pytest.raises(InvestigationSnapshotError):
        InvestigationSnapshot.from_dict(altered)

    objects.pop(canonical_key)
    _, source = _source(objects)
    with pytest.raises(InvestigationSnapshotError,
                       match="REQUIRED_CANONICAL_DECISION_TRACE_EMPTY"):
        _freeze(source)


def test_ambiguous_historical_guard_fails_closed():
    objects = _objects()
    objects[_key("decision_trace", part="guard.jsonl")] = _jsonl({
        **_historical_guard(), "entity_id": "spoofed"})
    _, source = _source(objects)
    with pytest.raises(ResearchDataSourceError,
                       match="HISTORICAL_GUARD_REJECTION_UNVERIFIED"):
        _freeze(source)


def test_same_source_population_reproduces_snapshot_fingerprint(tmp_path):
    _, source = _source()
    first = _freeze(source, manifest_path=tmp_path / "first.json")
    replay = _freeze(source, manifest_path=tmp_path / "replay.json")

    assert first.snapshot_id == replay.snapshot_id
    assert first.snapshot_fingerprint == replay.snapshot_fingerprint
    assert first.evidence_epoch == replay.evidence_epoch
    assert first.immutable is True
    assert first.status == "FROZEN"
    assert first.start_date == "2026-09-23"
    assert first.end_date == "2026-09-30"


def test_changed_object_added_object_removed_object_and_date_bounds_change_identity():
    base_objects = _objects()
    _, base_source = _source(base_objects)
    base = _freeze(base_source)

    changed_objects = dict(base_objects)
    first_key = next(iter(changed_objects))
    changed_objects[first_key] += "\n"
    _, changed_source = _source(changed_objects)
    changed = _freeze(changed_source)
    assert changed.snapshot_fingerprint != base.snapshot_fingerprint

    removed_objects = dict(base_objects)
    removed_objects.pop(_key("market_context"))
    _, removed_base_source = _source(removed_objects)
    removed_base = _freeze(removed_base_source)
    assert removed_base.snapshot_fingerprint != base.snapshot_fingerprint

    added_objects = dict(base_objects)
    added_objects[_key("trade_truth", "2026-09-26", "part-001.jsonl")] = _jsonl(
        {"schema_version": "trade_truth_v1",
         "symbol": "EURUSD",
         "identity": {"trade_id": "T2", "correlation_id": "C2",
                      "account_id": "A1", "symbol": "EURUSD"},
         "execution": {"entry_fill_price": 1.1, "volume_executed": 0.1},
         "timestamps": {"exit_timestamp_broker": "2026-09-26T10:00:00Z"},
         "outcome": {"r_multiple_realised": 0.5},
         "exit": {"exit_reason": "manual_close"}})
    _, added_source = _source(added_objects)
    added = _freeze(added_source)
    assert added.snapshot_fingerprint != base.snapshot_fingerprint

    removed_objects = dict(added_objects)
    removed_objects.pop(_key("trade_truth", "2026-09-26", "part-001.jsonl"))
    _, removed_source = _source(removed_objects)
    removed = _freeze(removed_source)
    assert removed.snapshot_fingerprint == base.snapshot_fingerprint

    _, different_authority_source = _source(base_objects)
    different_authority_source._bucket = "different-test-bucket"
    different_authority = _freeze(different_authority_source)
    assert different_authority.snapshot_fingerprint != base.snapshot_fingerprint

    _, new_bounds_source = _source(base_objects)
    new_bounds = freeze_investigation_snapshot(
        start_date="2026-09-24", end_date="2026-09-30", source=new_bounds_source)
    assert new_bounds.snapshot_fingerprint != base.snapshot_fingerprint


def test_manifest_reports_required_optional_absent_and_excluded_statuses():
    objects = _objects()
    objects.pop(_key("market_context"))
    _, source = _source(objects)
    snapshot = _freeze(source)
    coverage = {row["dataset"]: row for row in snapshot.dataset_coverage}

    assert coverage["trade_truth"] == {
        "dataset": "trade_truth", "requirement": "REQUIRED", "presence": "PRESENT"}
    assert coverage["market_context"] == {
        "dataset": "market_context", "requirement": "OPTIONAL", "presence": "ABSENT"}
    assert coverage["research_universe"]["requirement"] == "EXCLUDED_BY_DESIGN"
    assert "research_universe" in snapshot.excluded_by_design


def test_snapshot_round_trip_and_finalized_instance_are_immutable(tmp_path):
    _, source = _source()
    path = tmp_path / "manifest.json"
    snapshot = _freeze(source, manifest_path=path)
    loaded = load_investigation_snapshot(path)

    assert loaded == snapshot
    assert loaded.snapshot_id == snapshot.snapshot_id
    with pytest.raises((AttributeError, TypeError)):
        snapshot.status = "CAPTURING"
    mutated = snapshot.to_dict()
    mutated["datasets"].clear()
    assert len(snapshot.datasets) == len(BOUND_DATASETS)


def test_snapshot_reader_fetches_only_bound_keys_and_rejects_scope_widening():
    fake, source = _source()
    snapshot = _freeze(source)
    bound_keys = {obj.identifier for binding in snapshot.datasets for obj in binding.objects}
    extra_key = _key("trade_truth", "2026-09-28", "late-arrival.jsonl")
    fake.objects[extra_key] = _jsonl(
        {"schema_version": "trade_truth_v1", "symbol": "EURUSD",
         "identity": {"trade_id": "late", "correlation_id": "LATE",
                      "symbol": "EURUSD"},
         "execution": {"entry_fill_price": 1.1, "volume_executed": 0.1},
         "timestamps": {"exit_timestamp_broker": "2026-09-28T10:00:00Z"},
         "outcome": {"r_multiple_realised": None},
         "exit": {"exit_reason": "manual_close"}})
    list_calls_before_open = len(fake.list_calls)

    reader = SnapshotBoundDatasetReader(snapshot, source)
    assert len(fake.list_calls) == list_calls_before_open
    assert set(fake.get_calls[-len(bound_keys):]) == bound_keys
    assert extra_key not in fake.get_calls
    with pytest.raises(InvestigationSnapshotError, match="SNAPSHOT_DATE_SCOPE_MISMATCH"):
        reader.read_dataset("trade_truth", start_date="2026-09-01")
    with pytest.raises(InvestigationSnapshotError, match="DATASET_NOT_BOUND"):
        reader.read_dataset("shadow_trades")


def test_all_six_views_reference_the_same_frozen_manifest_without_live_reads(tmp_path):
    fake, source = _source()
    snapshot = _freeze(source)
    path = tmp_path / f"{snapshot.snapshot_id}.json"
    save_investigation_snapshot(snapshot, path)
    bound_keys = {obj.identifier for binding in snapshot.datasets for obj in binding.objects}
    lists_before_open = len(fake.list_calls)

    views = InvestigationViews.from_snapshot_id(
        snapshot.snapshot_id, source=source, manifest_directory=tmp_path)
    filters = InvestigationFilters(start_date="2026-09-23", end_date="2026-09-30")
    results = [
        views.build_trade_investigation(filters),
        views.build_decision_execution_outcome(filters),
        views.build_shadow_comparison(filters),
        views.build_context_performance(filters),
        views.build_execution_quality(filters),
        views.build_risk_sequence(filters),
    ]
    gets_after_open = tuple(fake.get_calls)

    assert len(fake.list_calls) == lists_before_open
    assert set(gets_after_open) == bound_keys
    assert tuple(fake.get_calls) == gets_after_open
    assert {item.snapshot_id for item in results} == {snapshot.snapshot_id}
    assert {item.evidence_epoch for item in results} == {snapshot.evidence_epoch}
    assert {item.snapshot_fingerprint for item in results} == {
        snapshot.snapshot_fingerprint
    }
    assert {item.immutable for item in results} == {True}
    assert {item.source_state for item in results} == {"FROZEN"}
    assert all(item.snapshot_manifest["datasets"] == results[0].snapshot_manifest["datasets"]
               for item in results)
    assert all(item.snapshot_manifest["dataset_snapshots"] ==
               results[0].snapshot_manifest["dataset_snapshots"] for item in results)
    assert [len(item.records) for item in results] == [1, 1, 1, 1, 2, 1]


def test_absent_optional_history_stays_absent_in_frozen_view(tmp_path):
    objects = _objects()
    objects.pop(_key("market_context"))
    _, source = _source(objects)
    snapshot = _freeze(source)
    reader = SnapshotBoundDatasetReader(snapshot, source)

    result = InvestigationViews(reader).build_trade_investigation(
        InvestigationFilters(start_date="2026-09-23", end_date="2026-09-30"))

    assert result.snapshot_id == snapshot.snapshot_id
    assert result.immutable is True
    assert result.accounting["missing_evidence_by_source_field"][
        "market_context.symbol+cycle_id"
    ] == 1
    assert result.records[0]["market_context_evidence"] is None


def test_schema_authority_change_fails_closed_before_read(monkeypatch):
    _, source = _source()
    snapshot = _freeze(source)
    import research_engine.v10.investigation_snapshot as snapshot_module
    original = snapshot_module.current_schema

    monkeypatch.setattr(
        snapshot_module, "current_schema",
        lambda name: "trade_truth_v2" if name == "trade_truth" else original(name),
    )
    with pytest.raises(InvestigationSnapshotError, match="SCHEMA_AUTHORITY_CHANGED"):
        SnapshotBoundDatasetReader(snapshot, source)


def test_live_result_is_not_mislabeled_as_frozen():
    class Reader:
        rows = {
            name: [json.loads(line) for line in body.splitlines() if line.strip()]
            for name, body in _objects().items()
        }

        def read_dataset(self, dataset, **kwargs):
            prefix = f"{s3_base_prefix(dataset)}/schema_version={current_schema(dataset)}/"
            key = next((key for key in self.rows if key.startswith(prefix)), None)
            return [] if key is None else self.rows[key]

    result = InvestigationViews(Reader()).build_trade_investigation()

    assert result.snapshot_id is None
    assert result.immutable is False
    assert result.source_state == "LIVE_MUTABLE_UNBOUND"


def test_missing_bound_object_and_digest_mismatch_fail_closed():
    objects = _objects()
    fake, source = _source(objects)
    snapshot = _freeze(source)
    key = snapshot.datasets[0].objects[0].identifier

    del fake.objects[key]
    with pytest.raises(ResearchDataSourceError):
        SnapshotBoundDatasetReader(snapshot, source)

    fake, source = _source(objects)
    snapshot = _freeze(source)
    key = snapshot.datasets[0].objects[0].identifier
    fake.objects[key] += "\n"
    with pytest.raises(ResearchDataSourceError, match="SNAPSHOT_OBJECT_DIGEST_MISMATCH"):
        SnapshotBoundDatasetReader(snapshot, source)


def test_frozen_capture_cannot_be_mislabeled_if_object_population_changes_mid_freeze():
    class ChangingS3(MemoryS3):
        def __init__(self, objects):
            super().__init__(objects)
            self.changed = False

        def list_objects_v2(self, **kwargs):
            response = super().list_objects_v2(**kwargs)
            if not self.changed and self.list_calls:
                self.changed = True
                self.objects[_key("trade_truth", "2026-09-26", "during-freeze.jsonl")] = _jsonl(
                    {"schema_version": "trade_truth_v1",
                     "symbol": "EURUSD",
                     "identity": {"trade_id": "late", "correlation_id": "LATE",
                                  "symbol": "EURUSD"},
                     "execution": {"entry_fill_price": 1.1,
                                   "volume_executed": 0.1},
                     "timestamps": {"exit_timestamp_broker":
                                    "2026-09-26T10:00:00Z"},
                     "outcome": {"r_multiple_realised": None},
                     "exit": {"exit_reason": "manual_close"}})
            return response

    fake = ChangingS3(_objects())
    source = S3ResearchDataSource(bucket="test-bucket", client=fake)
    with pytest.raises(InvestigationSnapshotError, match="OBJECT_POPULATION_CHANGED_DURING_FREEZE"):
        _freeze(source)
