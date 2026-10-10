"""Isolated regression: frontier/freeze schema-integrity acceptance defect.

The freeze boundary (``read_objects_for_freeze`` -> ``read_bound_objects`` ->
``_read_object``) previously:
  * SILENTLY dropped valid-JSON, non-object rows (arrays/scalars/null) without
    counting them, so a malformed object could reduce the frozen row count
    without any error, and
  * appended object rows WITHOUT verifying that the record's ``schema_version``
    matches the dataset's governed schema or that a registered canonical profile
    is satisfied.

These tests reproduce that defect at the frontier boundary. They FAIL before the
repair and PASS after it. They are scoped strictly to the freeze boundary; the
live ``read_dataset`` path is untouched (its malformed rows are still skipped and
reported, never raised), so no existing loader regresses.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

import pytest

from core.production_data_contract import current_schema, s3_base_prefix
from core.canonical_profiles import validate_record
from research_engine.data_access.s3_source import (
    ResearchDataSourceError,
    S3ResearchDataSource,
)
from research_engine.v10.continuous.frontier_coordinator import _read_frontier_object


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


def _jsonl(*rows: object) -> str:
    return "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)


def _source(objects: dict[str, str]) -> S3ResearchDataSource:
    return S3ResearchDataSource(bucket="test-bucket", client=MemoryS3(objects))


def _valid_trade_truth_row() -> dict:
    # Minimal producer-shaped trade truth record. Lifecycle evidence may be
    # null, while the writer always supplies its identity and section spine.
    return {
        "schema_version": current_schema("trade_truth"), "symbol": "EURUSD",
        "identity": {"trade_id": "T1", "correlation_id": "C1",
                     "symbol": "EURUSD"},
        "execution": {"entry_fill_price": 1.1, "volume_executed": 0.1},
        "timestamps": {"exit_timestamp_broker": 1},
        "outcome": {"r_multiple_realised": None},
        "exit": {"exit_reason": "manual_close"},
    }


def _manifest(dataset: str) -> list[dict]:
    return [{"identifier": _key(dataset), "content_sha256": "", "row_count": 1}]


def test_freeze_rejects_profiled_record_missing_required_fields():
    row = {"schema_version": current_schema("decision_trace")}
    valid, violations = validate_record("decision_trace", row)
    assert not valid
    assert "required field missing: symbol" in violations
    source = _source({_key("decision_trace"): _jsonl(row)})
    with pytest.raises(ResearchDataSourceError, match="SNAPSHOT_RECORD_PROFILE_INVALID"):
        source.read_objects_for_freeze(
            "decision_trace", _manifest("decision_trace"),
            expected_schema_version=current_schema("decision_trace"))


def test_freeze_accepts_complete_profiled_record():
    row = {
        "schema_version": current_schema("decision_trace"),
        "symbol": "EURUSD", "entity_id": "E1", "action": "HOLD",
    }
    assert validate_record("decision_trace", row) == (True, [])
    source = _source({_key("decision_trace"): _jsonl(row)})
    assert source.read_objects_for_freeze(
        "decision_trace", _manifest("decision_trace"),
        expected_schema_version=current_schema("decision_trace")) == [row]


def test_frontier_object_carries_exact_historical_cohort_count():
    canonical = {"schema_version": current_schema("decision_trace"),
                 "symbol": "EURUSD", "entity_id": "E1", "action": "HOLD",
                 "timestamp_utc": "2026-09-25T12:00:00Z"}
    historical = {
        "schema_version": current_schema("decision_trace"),
        "symbol": "EURUSD", "record_role": "runtime_guard_rejection",
        "event_type": "RISK_REJECTION", "rejection_type": "RISK_GUARD",
        "guard": "risk_limit", "reason": "limit", "should_trade": False,
        "cycle_id": 2, "correlation_id": "C2",
        "timestamp_utc": "2026-09-25T09:00:00Z",
    }
    key = _key("decision_trace")
    body = _jsonl(canonical, historical, historical)
    source = _source({key: body})
    listed = source.discover_dataset_objects("decision_trace")
    record = _read_frontier_object(source, "decision_trace", listed[0],
                                   date(2026, 9, 25))
    assert record["row_count"] == 3
    assert record["historical_guard_rows"] == 2
    assert record["event_time_coverage"]["start"] == "2026-09-25T12:00:00+00:00"
    assert source.guard_cohort_count("decision_trace", key) == 2
    assert source.guard_cohort_report("decision_trace").eligible_objects == 1
    assert source._get_client().objects[key] == body


def test_live_read_dataset_keeps_incomplete_profiled_record():
    row = {"schema_version": current_schema("decision_trace")}
    source = _source({_key("decision_trace"): _jsonl(row)})
    assert source.read_dataset("decision_trace", symbol="EURUSD") == [row]


def test_direct_bound_read_keeps_existing_profile_scope():
    row = {"schema_version": current_schema("decision_trace")}
    source = _source({_key("decision_trace"): _jsonl(row)})
    assert source.read_bound_objects(
        "decision_trace", _manifest("decision_trace"),
        expected_schema_version=current_schema("decision_trace"),
        _verify_content=False) == [row]

# ─── DEFECT 1: non-object rows silently reduce the frozen count ─────────────────

def test_freeze_rejects_non_object_row_instead_of_silently_dropping_it():
    # A valid-JSON ARRAY line is not an object. Before the repair it was dropped
    # silently (never counted), so a two-line file would freeze as one row.
    body = _jsonl(_valid_trade_truth_row(), [1, 2, 3])
    source = _source({_key("trade_truth"): body})

    with pytest.raises(ResearchDataSourceError):
        source.read_objects_for_freeze(
            "trade_truth", _manifest("trade_truth"),
            expected_schema_version=current_schema("trade_truth"))


def test_freeze_rejects_scalar_and_null_rows():
    for bad in (42, "a-string", None, True):
        body = _jsonl(_valid_trade_truth_row(), bad)
        source = _source({_key("trade_truth"): body})
        with pytest.raises(ResearchDataSourceError):
            source.read_objects_for_freeze(
                "trade_truth", _manifest("trade_truth"),
                expected_schema_version=current_schema("trade_truth"))


def test_freeze_non_object_row_is_not_silently_accepted():
    # A file of ONLY a non-object row must not freeze with no error.
    source = _source({_key("trade_truth"): _jsonl([1, 2, 3])})
    with pytest.raises(ResearchDataSourceError):
        source.read_objects_for_freeze(
            "trade_truth", _manifest("trade_truth"),
            expected_schema_version=current_schema("trade_truth"))


# ─── DEFECT 2: wrong-schema record accepted at the freeze boundary ──────────────

def test_freeze_rejects_record_with_wrong_schema_version():
    wrong = {"schema_version": "trade_truth_v2"}
    source = _source({_key("trade_truth"): _jsonl(wrong)})
    with pytest.raises(ResearchDataSourceError):
        source.read_objects_for_freeze(
            "trade_truth", _manifest("trade_truth"),
            expected_schema_version=current_schema("trade_truth"))


def test_freeze_rejects_record_with_missing_schema_version():
    source = _source({_key("trade_truth"): _jsonl({"identity": {"trade_id": "T1"}})})
    with pytest.raises(ResearchDataSourceError):
        source.read_objects_for_freeze(
            "trade_truth", _manifest("trade_truth"),
            expected_schema_version=current_schema("trade_truth"))


# ─── DEFECT 3: schema identity enforced for every governed freeze-boundary dataset

def test_freeze_enforces_schema_identity_for_each_bound_dataset():
    # Schema identity must hold for EVERY dataset acquired at the freeze
    # boundary — both profiled and unprofiled. A record whose schema_version
    # does not match the dataset's canonical schema must be rejected. This uses
    # the existing production contract (current_schema); it never invents
    # required fields for unprofiled datasets.
    for dataset in ("trade_truth", "execution_results", "decision_trace",
                    "shadow_runtime", "market_context", "execution_attempts",
                    "protection_audit", "risk_deviation"):
        wrong = {"schema_version": current_schema(dataset) + "_WRONG",
                 "symbol": "EURUSD"}
        source = _source({_key(dataset): _jsonl(wrong)})
        with pytest.raises(ResearchDataSourceError,
                           match="SNAPSHOT_RECORD_SCHEMA_MISMATCH"):
            source.read_objects_for_freeze(
                dataset, _manifest(dataset),
                expected_schema_version=current_schema(dataset))


def test_freeze_accepts_a_record_with_the_governed_schema_identity():
    # Positive control: schema identity and the verified producer spine pass.
    good = _valid_trade_truth_row()
    source = _source({_key("trade_truth"): _jsonl(good)})
    rows = source.read_objects_for_freeze(
        "trade_truth", _manifest("trade_truth"),
        expected_schema_version=current_schema("trade_truth"))
    assert rows == [good]



# ─── NON-REGRESSION: the live read_dataset path is unchanged ────────────────────

def test_live_read_dataset_still_skips_and_reports_malformed_lines():
    # The repair is scoped to the freeze boundary. The live path must keep its
    # documented contract: malformed JSON lines are skipped and reported, never
    # raised, so no existing loader regresses.
    key = _key("trade_truth")
    body = (
        json.dumps({"schema_version": current_schema("trade_truth"),
                    "identity": {"trade_id": "ok"}}) + "\nNOT_JSON\n"
    )
    source = _source({key: body})
    recs = source.read_dataset("trade_truth", symbol="EURUSD")
    assert len(recs) == 1
    rep = source.malformed_report("trade_truth")
    assert rep is not None and rep.malformed_lines == 1


def test_live_read_dataset_does_not_raise_on_non_object_row():
    # read_dataset (live, non-freeze) must NOT start raising on non-object rows;
    # strict rejection is a freeze-boundary behavior only.
    key = _key("trade_truth")
    body = _jsonl({"schema_version": current_schema("trade_truth")}, [1, 2, 3])
    source = _source({key: body})
    recs = source.read_dataset("trade_truth", symbol="EURUSD")
    assert all(isinstance(r, dict) for r in recs)

