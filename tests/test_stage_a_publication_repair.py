"""Isolated regressions for read-only publication and cadence ownership."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.v10.continuous.continuous_projection_bridge import (
    ContinuousBridgeError, sync_latest_completed_cycle,
)
from research_engine.v10.continuous.cycle_state import (
    ContinuousCycleStore, ContinuousResearchCycleResult,
)
from research_engine.v10.continuous.research_cadence import (
    ResearchCadenceError, _cadence_service_lease,
)
from research_engine.v10.continuous.research_lab_archive import load_archive
from research_engine.v10.lab import server
from tests.test_continuous_projection_bridge import (
    _make_projection, _publish_production_cycle, _write,
)


def _lab_fixture(monkeypatch, lab, src):
    monkeypatch.setenv("RESEARCH_LAB_CONTINUOUS_SOURCE", str(src))
    monkeypatch.setattr(server, "STATE_ROOT", lab)
    monkeypatch.setattr(server, "READ_ONLY", True)
    monkeypatch.setattr(server, "_registry_questions", lambda: [
        {"question_id": qid, "title": qid, "status": "NOT_YET_EVALUATED",
         "registry_status": "NOT_YET_EVALUATED"} for qid in BASELINE_QUESTION_IDS])
    monkeypatch.setattr(server, "_bound_evidence_references", lambda rows: None)
    monkeypatch.setattr(server, "_cycles", lambda: ([], None))
    monkeypatch.setattr(server, "_projection_events", lambda *args: [])
    monkeypatch.setattr(server, "_candidate_lifecycle_state", lambda: {"candidates": []})
    monkeypatch.setattr(server, "_additional_records", lambda projection: {})
    monkeypatch.setattr(server, "run_status", lambda: {"status": "READ_ONLY"})
    monkeypatch.setattr(server, "_completed_read_only_state", lambda: {
        "source": "VERIFIED_HISTORICAL_ARCHIVE", "errors": [], "read_only": True})


def test_completed_cycle_publishes_in_read_only_lab_without_running_research(tmp_path, monkeypatch):
    src, lab = tmp_path / "source", tmp_path / "lab"
    projection = _publish_production_cycle(
        src, cycle_id="CRCYCLE-ONE", completed_at="2026-10-10T00:00:00+00:00")
    _lab_fixture(monkeypatch, lab, src)
    monkeypatch.setattr(server.subprocess, "Popen", lambda *args, **kwargs:
                        pytest.fail("read-only publication launched research"))

    state = server.build_state()
    assert state["source"] == "UNIFIED_PROJECTION"
    assert state["read_only"] is True
    assert state["freshness"]["projection_version"] == projection["projection_version"]
    assert len(state["questions"]) == 70
    code, body = server.start_run()
    assert code == 403 and body["error"] == "READ_ONLY"

    imported = lab / "projection" / "latest.json"
    mtime = imported.stat().st_mtime_ns
    again = server.build_state()
    assert again["source"] == "UNIFIED_PROJECTION"
    assert imported.stat().st_mtime_ns == mtime

    # A projection written before its completed-cycle pointer must not appear.
    _write(imported, _make_projection(cycle_id="CRCYCLE-IN-PROGRESS"))
    unpublished = server.build_state()
    assert unpublished["source"] == "VERIFIED_HISTORICAL_ARCHIVE"
    assert any("Projection not publishable" in error for error in unpublished["errors"])

    tampered = json.loads(json.dumps(projection))
    tampered["data_frontier"]["snapshot_id"] = "TAMPERED"
    _write(imported, tampered)
    rejected = server.build_state()
    assert rejected["source"] == "VERIFIED_HISTORICAL_ARCHIVE"
    assert any("PROJECTION_IDENTITY_MISMATCH" in error for error in rejected["errors"])


@pytest.mark.parametrize("source_state", ["missing", "invalid"])
def test_missing_or_invalid_source_keeps_archive_fallback(tmp_path, monkeypatch, source_state):
    src, lab = tmp_path / "source", tmp_path / "lab"
    if source_state == "invalid":
        _write(src / "cycles" / "latest_completed.json", {
            "continuous_cycle_id": "CRCYCLE-BAD", "cycle_outcome": "COMPLETED",
            "projection_version": "RPROJ-MISSING"})
    _lab_fixture(monkeypatch, lab, src)
    state = server.build_state()
    assert state["source"] == "VERIFIED_HISTORICAL_ARCHIVE"
    assert not (lab / "projection" / "latest.json").exists()
    assert state["publication_bridge"]["status"] in {"NO_SOURCE", "ERROR"}


def test_idle_success_does_not_displace_completed_publication(tmp_path):
    src, lab = tmp_path / "source", tmp_path / "lab"
    projection = _publish_production_cycle(
        src, cycle_id="CRCYCLE-ONE", completed_at="2026-10-10T00:00:00+00:00")
    completed = json.loads((src / "cycles" / "latest_success.json").read_text())
    _write(src / "cycles" / "latest_completed.json", completed)
    _write(src / "cycles" / "latest_success.json", {
        **completed, "continuous_cycle_id": "CRCYCLE-IDLE",
        "cycle_outcome": "NO_NEW_RESEARCH_EVIDENCE",
        "predecessor_cycle_id": "CRCYCLE-ONE"})
    result = sync_latest_completed_cycle(lab, source=src)
    assert result.status == "IMPORTED"
    assert result.imported_projection_version == projection["projection_version"]
    assert result.imported_cycle_id == "CRCYCLE-ONE"


def test_in_place_lab_sync_preserves_producer_idle_pointer(tmp_path, monkeypatch):
    root = tmp_path / "shared_state"
    _publish_production_cycle(
        root, cycle_id="CRCYCLE-ONE", completed_at="2026-10-10T00:00:00+00:00")
    completed = json.loads((root / "cycles" / "latest_success.json").read_text())
    _write(root / "cycles" / "latest_completed.json", completed)
    idle = {**completed, "continuous_cycle_id": "CRCYCLE-IDLE",
            "cycle_outcome": "NO_NEW_RESEARCH_EVIDENCE"}
    _write(root / "cycles" / "latest_success.json", idle)
    before = (root / "projection" / "latest.json").stat().st_mtime_ns

    assert sync_latest_completed_cycle(root, source=root).status == "IMPORTED"
    assert json.loads((root / "cycles" / "latest_success.json").read_text()) == idle
    assert (root / "projection" / "latest.json").stat().st_mtime_ns == before
    assert sync_latest_completed_cycle(root, source=root).status == "UNCHANGED"
    monkeypatch.setattr(server, "STATE_ROOT", root)
    _, latest_completed = server._cycles()
    assert latest_completed["continuous_cycle_id"] == "CRCYCLE-ONE"


def test_cycle_store_keeps_completed_pointer_across_idle_success(tmp_path):
    store = ContinuousCycleStore(tmp_path / "cycles")
    def cycle(cycle_id, outcome, predecessor=None):
        return ContinuousResearchCycleResult(
            continuous_cycle_id=cycle_id, cycle_outcome=outcome,
            frontier_snapshot_id="ISNAP-ONE", question_cycle_id=None,
            bridge_run_id=None, q71_agenda_id=None, q71_queue_id=None,
            validation_queue_version=None, projection_version="RPROJ-ONE",
            predecessor_cycle_id=predecessor, started_at="2026-10-10T00:00:00+00:00",
            completed_at="2026-10-10T00:00:00+00:00", stage_statuses={})
    store.save(cycle("CRCYCLE-ONE", "COMPLETED"), successful=True)
    store.save(cycle("CRCYCLE-IDLE", "NO_NEW_RESEARCH_EVIDENCE", "CRCYCLE-ONE"),
               successful=True)
    assert json.loads(store.latest_completed_path.read_text())["continuous_cycle_id"] == "CRCYCLE-ONE"
    assert json.loads(store.latest_path.read_text())["continuous_cycle_id"] == "CRCYCLE-IDLE"


def test_invalid_projection_provenance_is_rejected(tmp_path):
    src, lab = tmp_path / "source", tmp_path / "lab"
    projection = _publish_production_cycle(
        src, cycle_id="CRCYCLE-ONE", completed_at="2026-10-10T00:00:00+00:00")
    projection["data_frontier"]["snapshot_id"] = "TAMPERED"
    _write(src / "projection" / "history" /
           f"{projection['projection_version']}.json", projection)
    with pytest.raises(ContinuousBridgeError, match="PROJECTION_IDENTITY_MISMATCH"):
        sync_latest_completed_cycle(lab, source=src)
    assert not (lab / "projection" / "latest.json").exists()


def test_invalid_archive_identity_is_rejected(tmp_path):
    archive = tmp_path / "archive"
    _write(archive / "index.json", {
        "schema": "research_lab_archive_index_v1", "latest": "a" * 64,
        "runs": [{"archive_id": "a" * 64, "catalog_sha256": "b" * 64}]})
    _write(archive / ("a" * 64) / "catalog.json", {"files": {}})
    with pytest.raises(Exception, match="ARCHIVE_CATALOG_CHANGED"):
        load_archive(archive)


def test_duplicate_cadence_service_lease_rejected(tmp_path):
    with _cadence_service_lease(tmp_path):
        with pytest.raises(ResearchCadenceError, match="CADENCE_SERVICE_ALREADY_RUNNING"):
            with _cadence_service_lease(tmp_path):
                pytest.fail("duplicate service acquired the state")
        command = (
            "import sys; "
            "from research_engine.v10.continuous.research_cadence import _cadence_service_lease; "
            "_cadence_service_lease(sys.argv[1]).__enter__()"
        )
        other = subprocess.run(
            [sys.executable, "-c", command, str(tmp_path)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, timeout=15, check=False)
        assert other.returncode != 0
        assert "CADENCE_SERVICE_ALREADY_RUNNING" in other.stderr
