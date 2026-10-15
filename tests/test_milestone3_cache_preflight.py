"""Offline checks for exact cache preflight and HEAD failure attribution."""
import time

import pytest

from research_engine.data_access.s3_source import (
    ResearchDataSourceError, S3ResearchDataSource)
from tests.test_cold_start_cache_recovery import _freeze, _objects, _versioned
from tools.milestone3_cache_preflight import preflight_snapshot_cache
import tools.milestone3_cache_preflight as preflight_module


def test_preflight_reports_all_local_issues_without_remote_io(tmp_path):
    fake = _versioned(_objects())
    cache = tmp_path / "cache"
    snapshot = _freeze(fake, cache)
    source = S3ResearchDataSource(bucket="incremental-test", client=object(),
                                  verified_cache_directory=cache)
    bound = [(binding.dataset, item) for binding in snapshot.datasets
             for item in binding.objects]
    first_path = source._verified_body_path(
        source._verified_body_identity(bound[0][0], bound[0][1].to_dict()))
    second_path = source._verified_body_path(
        source._verified_body_identity(bound[1][0], bound[1][1].to_dict()))
    first_path.write_bytes(first_path.read_bytes()[:-1])
    second_path.unlink()
    events = []
    report = preflight_snapshot_cache(snapshot, source, max_workers=2,
                                      progress_callback=events.append)
    assert report["status"] == "INVALID"
    assert report["checked"] == report["total"] == len(bound)
    assert len(report["issues"]) == 2
    assert {issue["identifier"] for issue in report["issues"]} == {
        bound[0][1].identifier, bound[1][1].identifier}
    assert report["remote_calls"] == {"head_calls": 0, "get_calls": 0,
                                      "list_calls": 0}
    assert events[-1]["checked"] == len(bound)


def test_preflight_deadline_fails_incomplete_without_remote_io(tmp_path, monkeypatch):
    fake = _versioned(_objects())
    cache = tmp_path / "cache"
    snapshot = _freeze(fake, cache)
    source = S3ResearchDataSource(bucket="incremental-test", client=object(),
                                  verified_cache_directory=cache)

    def slow_local_check(*args):
        time.sleep(0.05)

    monkeypatch.setattr(preflight_module, "verify_cached_entry", slow_local_check)
    report = preflight_snapshot_cache(snapshot, source, max_workers=1,
                                      deadline_seconds=0.001)
    assert report["status"] == "TIMEOUT"
    assert report["checked"] < report["total"]
    assert report["remote_calls"] == {"head_calls": 0, "get_calls": 0,
                                      "list_calls": 0}


def test_valid_cache_head_failure_is_not_reported_as_cache_corruption(tmp_path):
    fake = _versioned(_objects())
    cache = tmp_path / "cache"
    snapshot = _freeze(fake, cache)

    class FailingHead:
        def head_object(self, **kwargs):
            raise TimeoutError("offline head timeout")

    source = S3ResearchDataSource(bucket="incremental-test",
                                  client=FailingHead(),
                                  verified_cache_directory=cache)
    binding = next(binding for binding in snapshot.datasets if binding.objects)
    item = binding.objects[0]
    with pytest.raises(ResearchDataSourceError, match="head_object") as caught:
        source.read_bound_objects(binding.dataset, [item.to_dict()],
                                  expected_schema_version=binding.schema_version)
    assert "VERIFIED_BODY_CACHE_INVALID" not in str(caught.value)
    assert source.performance_snapshot()["head_failures"] == 1
    assert source.performance_snapshot()["get_calls"] == 0


def test_valid_cache_changed_head_identity_fails_closed(tmp_path):
    fake = _versioned(_objects())
    cache = tmp_path / "cache"
    snapshot = _freeze(fake, cache)
    binding = next(binding for binding in snapshot.datasets if binding.objects)
    item = binding.objects[0]
    fake.versions[item.identifier] = "different-version"
    source = S3ResearchDataSource(bucket="incremental-test", client=fake,
                                  verified_cache_directory=cache)
    with pytest.raises(ResearchDataSourceError,
                       match="VERIFIED_BODY_AUTHORITY_MISMATCH"):
        source.read_bound_objects(binding.dataset, [item.to_dict()],
                                  expected_schema_version=binding.schema_version)
    assert source.performance_snapshot()["get_calls"] == 0
