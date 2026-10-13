"""Tests for the read-only continuous projection bridge.

The bridge imports a NEWLY completed, verified production cycle (the continuous
coordinator's ``unified_research_projection_v1`` output) into the Research Lab's
own ``STATE_ROOT`` so the existing server surfaces it automatically.

Covered:
- No source directory            -> safe no-op (NO_SOURCE)
- Source but no completed cycle   -> safe no-op (NO_COMPLETED_CYCLE)
- New completed cycle             -> IMPORTED and Lab-readable
- Unchanged source                -> UNCHANGED (idempotent, no re-copy)
- Restart recovery                -> re-import after marker loss is idempotent
- Failed / in-progress attempt    -> never surfaced (success-pointer only)
- Corrupted projection content    -> rejected (identity mismatch)
- Lineage mismatch (cycle vs proj)-> rejected
- Wrong canonical count           -> rejected
- latest.json diverges from history -> rejected
"""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.v10.continuous.research_projection import PROJECTION_SCHEMA
from research_engine.v10.continuous.continuous_projection_bridge import (
    BridgeResult,
    ContinuousBridgeError,
    resolve_source,
    sync_latest_completed_cycle,
)


# ─────────────────────────────────────────────────────────────────────────────
# Builders for a self-identifying synthetic production source
# ─────────────────────────────────────────────────────────────────────────────
def _canonical_questions() -> list[dict]:
    """70 canonical rows with the minimal shape the Lab/bridge validate."""
    rows = []
    for qid in BASELINE_QUESTION_IDS:
        rows.append({
            "question_id": qid,
            "work_state": "FRESH",
            "execution_freshness": "CURRENT",
            "result": {"question_id": qid, "status": "COMPLETE"},
        })
    return rows


def _make_projection(*, cycle_id: str, extra: dict | None = None) -> dict:
    """Build a projection whose projection_version is its own governed hash."""
    material = {
        "continuous_cycle_id": cycle_id,
        "canonical_questions": _canonical_questions(),
        "findings": [],
        "hypotheses": [],
        "candidates": [],
        "generated_questions": [],
        "research_lag": {"lag_epochs": 0, "lag_seconds": 0.0},
        "what_changed": {"questions_changed": []},
        "data_frontier": {"snapshot_id": "ISNAP-TEST", "status": "OK"},
    }
    if extra:
        material.update(extra)
    version = "RPROJ-" + hashlib.sha256(
        canonical_json(material).encode("utf-8")).hexdigest()[:32].upper()
    return {"projection_schema": PROJECTION_SCHEMA,
            "projection_version": version, **material}


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8")


def _publish_production_cycle(src: Path, *, cycle_id: str,
                              completed_at: str, predecessor: str | None = None,
                              projection: dict | None = None) -> dict:
    """Materialise one verified COMPLETED cycle in a synthetic production dir."""
    projection = projection or _make_projection(cycle_id=cycle_id)
    version = projection["projection_version"]
    _write(src / "projection" / "history" / f"{version}.json", projection)
    _write(src / "projection" / "latest.json", projection)
    cycle_record = {
        "cycle_schema": "continuous_research_cycle_v1",
        "continuous_cycle_id": cycle_id,
        "cycle_outcome": "COMPLETED",
        "projection_version": version,
        "predecessor_cycle_id": predecessor,
        "completed_at": completed_at,
        "failure_stage": None,
        "failure_reason": None,
    }
    _write(src / "cycles" / "history" / f"{cycle_id}.json", cycle_record)
    _write(src / "cycles" / "latest_success.json", {
        "continuous_cycle_id": cycle_id,
        "cycle_outcome": "COMPLETED",
        "projection_version": version,
        "completed_at": completed_at,
        "predecessor_cycle_id": predecessor,
    })
    return projection


@pytest.fixture()
def dirs():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        yield base / "production", base / "lab_state"


# ─────────────────────────────────────────────────────────────────────────────
# No-op / benign conditions
# ─────────────────────────────────────────────────────────────────────────────
def test_no_source_is_safe_noop(dirs):
    _src, lab = dirs
    missing = _src / "does_not_exist"
    result = sync_latest_completed_cycle(lab, source=missing)
    assert result.status == "NO_SOURCE"
    assert not (lab / "projection" / "latest.json").exists()


def test_source_without_completed_cycle_is_noop(dirs):
    src, lab = dirs
    (src / "projection").mkdir(parents=True)
    result = sync_latest_completed_cycle(lab, source=src)
    assert result.status == "NO_COMPLETED_CYCLE"
    assert not (lab / "projection" / "latest.json").exists()


# ─────────────────────────────────────────────────────────────────────────────
# Happy path + idempotency + restart recovery
# ─────────────────────────────────────────────────────────────────────────────
def test_new_completed_cycle_is_imported_and_lab_readable(dirs):
    src, lab = dirs
    projection = _publish_production_cycle(
        src, cycle_id="CRCYCLE-AAA", completed_at="2026-10-05T02:34:36+00:00")

    result = sync_latest_completed_cycle(lab, source=src)
    assert result.status == "IMPORTED"
    assert result.imported_projection_version == projection["projection_version"]
    assert result.imported_cycle_id == "CRCYCLE-AAA"

    # Lab-facing artifacts exist and are readable by the authoritative store.
    from research_engine.v10.continuous.research_projection import ResearchProjectionStore
    loaded = ResearchProjectionStore(lab / "projection").load_latest()
    assert loaded is not None
    assert loaded["projection_version"] == projection["projection_version"]

    from research_engine.v10.continuous.research_lab import build_lab_view
    view = build_lab_view(loaded)
    assert view["canonical_question_count"] == 70

    assert (lab / "cycles" / "history" / "CRCYCLE-AAA.json").exists()
    assert (lab / "cycles" / "latest_success.json").exists()


def test_unchanged_source_is_idempotent_noop(dirs):
    src, lab = dirs
    _publish_production_cycle(src, cycle_id="CRCYCLE-AAA",
                              completed_at="2026-10-05T02:34:36+00:00")
    first = sync_latest_completed_cycle(lab, source=src)
    assert first.status == "IMPORTED"

    latest = lab / "projection" / "latest.json"
    mtime_before = latest.stat().st_mtime_ns
    second = sync_latest_completed_cycle(lab, source=src)
    assert second.status == "UNCHANGED"
    # No re-copy of the large artifact on an unchanged source.
    assert latest.stat().st_mtime_ns == mtime_before


def test_restart_recovery_reimports_after_marker_loss(dirs):
    src, lab = dirs
    _publish_production_cycle(src, cycle_id="CRCYCLE-AAA",
                              completed_at="2026-10-05T02:34:36+00:00")
    assert sync_latest_completed_cycle(lab, source=src).status == "IMPORTED"

    # Simulate a restart that lost only the idempotency marker.
    (lab / "continuous_bridge_state.json").unlink()
    recovered = sync_latest_completed_cycle(lab, source=src)
    # Re-establishes the marker without harm; history identity is stable.
    assert recovered.status == "IMPORTED"
    assert (lab / "continuous_bridge_state.json").exists()


def test_newer_completed_cycle_supersedes(dirs):
    src, lab = dirs
    _publish_production_cycle(src, cycle_id="CRCYCLE-AAA",
                              completed_at="2026-10-05T02:00:00+00:00")
    assert sync_latest_completed_cycle(lab, source=src).status == "IMPORTED"

    newer = _publish_production_cycle(
        src, cycle_id="CRCYCLE-BBB", completed_at="2026-10-06T02:00:00+00:00",
        predecessor="CRCYCLE-AAA")
    result = sync_latest_completed_cycle(lab, source=src)
    assert result.status == "IMPORTED"
    assert result.imported_cycle_id == "CRCYCLE-BBB"
    from research_engine.v10.continuous.research_projection import ResearchProjectionStore
    loaded = ResearchProjectionStore(lab / "projection").load_latest()
    assert loaded["projection_version"] == newer["projection_version"]
    # Prior history retained (immutable), both cycle records present.
    assert (lab / "cycles" / "history" / "CRCYCLE-AAA.json").exists()
    assert (lab / "cycles" / "history" / "CRCYCLE-BBB.json").exists()


# ─────────────────────────────────────────────────────────────────────────────
# Fail-closed verification
# ─────────────────────────────────────────────────────────────────────────────
def test_failed_attempt_is_never_surfaced(dirs):
    src, lab = dirs
    # Only a failed attempt exists — no success pointer at all.
    _write(src / "cycles" / "latest_attempt.json", {
        "continuous_cycle_id": "CRCYCLE-FAIL",
        "cycle_outcome": "FAILED",
        "failure_stage": "FRONTIER",
        "projection_version": None,
    })
    result = sync_latest_completed_cycle(lab, source=src)
    assert result.status == "NO_COMPLETED_CYCLE"
    assert not (lab / "projection" / "latest.json").exists()


def test_success_pointer_but_missing_projection_history_is_rejected(dirs):
    src, lab = dirs
    _write(src / "cycles" / "latest_success.json", {
        "continuous_cycle_id": "CRCYCLE-AAA",
        "cycle_outcome": "COMPLETED",
        "projection_version": "RPROJ-DOESNOTEXIST",
        "completed_at": "2026-10-05T02:34:36+00:00",
    })
    with pytest.raises(ContinuousBridgeError) as exc:
        sync_latest_completed_cycle(lab, source=src)
    assert "PROJECTION_HISTORY_MISSING" in str(exc.value)
    assert not (lab / "projection" / "latest.json").exists()


def test_corrupted_projection_identity_is_rejected(dirs):
    src, lab = dirs
    projection = _publish_production_cycle(
        src, cycle_id="CRCYCLE-AAA", completed_at="2026-10-05T02:34:36+00:00")
    version = projection["projection_version"]
    # Tamper with the history artifact content WITHOUT changing its filename:
    # its content no longer reproduces its own governed version id.
    tampered = dict(projection)
    tampered["candidates"] = [{"candidate_id": "INJECTED"}]
    _write(src / "projection" / "history" / f"{version}.json", tampered)
    _write(src / "projection" / "latest.json", tampered)
    with pytest.raises(ContinuousBridgeError) as exc:
        sync_latest_completed_cycle(lab, source=src)
    assert "PROJECTION_IDENTITY_MISMATCH" in str(exc.value)


def test_cycle_projection_lineage_mismatch_is_rejected(dirs):
    src, lab = dirs
    projection = _publish_production_cycle(
        src, cycle_id="CRCYCLE-AAA", completed_at="2026-10-05T02:34:36+00:00")
    # Point the cycle history record at a different projection version.
    other = _make_projection(cycle_id="CRCYCLE-OTHER")
    cycle_record = json.loads(
        (src / "cycles" / "history" / "CRCYCLE-AAA.json").read_text(encoding="utf-8"))
    cycle_record["projection_version"] = other["projection_version"]
    _write(src / "cycles" / "history" / "CRCYCLE-AAA.json", cycle_record)
    with pytest.raises(ContinuousBridgeError) as exc:
        sync_latest_completed_cycle(lab, source=src)
    assert "CYCLE_PROJECTION_VERSION_MISMATCH" in str(exc.value)


def test_wrong_canonical_count_is_rejected(dirs):
    src, lab = dirs
    short = _make_projection(cycle_id="CRCYCLE-AAA")
    short["canonical_questions"] = short["canonical_questions"][:69]
    # Re-identify so the identity check passes and the COUNT check is what fails.
    material = {k: v for k, v in short.items()
                if k not in ("projection_schema", "projection_version")}
    short["projection_version"] = "RPROJ-" + hashlib.sha256(
        canonical_json(material).encode("utf-8")).hexdigest()[:32].upper()
    _publish_production_cycle(
        src, cycle_id="CRCYCLE-AAA", completed_at="2026-10-05T02:34:36+00:00",
        projection=short)
    with pytest.raises(ContinuousBridgeError) as exc:
        sync_latest_completed_cycle(lab, source=src)
    assert "CANONICAL_COUNT_INVALID" in str(exc.value)


def test_source_latest_diverging_from_history_is_rejected(dirs):
    src, lab = dirs
    projection = _publish_production_cycle(
        src, cycle_id="CRCYCLE-AAA", completed_at="2026-10-05T02:34:36+00:00")
    # Make latest.json a DIFFERENT (but internally self-consistent) projection
    # than the history artifact the success pointer names.
    divergent = _make_projection(cycle_id="CRCYCLE-AAA",
                                 extra={"data_frontier": {"snapshot_id": "ISNAP-DIFF"}})
    _write(src / "projection" / "latest.json", divergent)
    with pytest.raises(ContinuousBridgeError) as exc:
        sync_latest_completed_cycle(lab, source=src)
    assert "SOURCE_LATEST_DIVERGES_FROM_HISTORY" in str(exc.value)


# ─────────────────────────────────────────────────────────────────────────────
# Source resolution
# ─────────────────────────────────────────────────────────────────────────────
def test_resolve_source_prefers_explicit_then_env(dirs, monkeypatch):
    src, _lab = dirs
    (src / "projection").mkdir(parents=True)
    # Explicit wins.
    assert resolve_source(src) == src.resolve()
    # Env fallback.
    monkeypatch.setenv("RESEARCH_LAB_CONTINUOUS_SOURCE", str(src))
    assert resolve_source(None) == src.resolve()
    # Nonexistent env -> None (safe no-op).
    monkeypatch.setenv("RESEARCH_LAB_CONTINUOUS_SOURCE", str(src / "nope"))
    assert resolve_source(None) is None


# ─────────────────────────────────────────────────────────────────────────────
# Timestamp distinctness + production latest-attempt diagnostic
# (added for the freshness-correction work; SYNTHETIC fixtures, not real
#  production research)
# ─────────────────────────────────────────────────────────────────────────────
def _publish_failed_attempt(src: Path, *, cycle_id: str, predecessor: str,
                            failure_stage: str = "FRONTIER",
                            failure_reason: str = "FRONTIER_DID_NOT_PRODUCE_VERIFIED_SNAPSHOT",
                            completed_at: str = "2026-10-05T02:34:36+00:00") -> None:
    """Write a FAILED attempt (history + latest_attempt pointer). TEST FIXTURE."""
    rec = {
        "cycle_schema": "continuous_research_cycle_v1",
        "continuous_cycle_id": cycle_id,
        "cycle_outcome": "FAILED",
        "failure_stage": failure_stage,
        "failure_reason": failure_reason,
        "projection_version": None,
        "predecessor_cycle_id": predecessor,
        "completed_at": completed_at,
    }
    _write(src / "cycles" / "history" / f"{cycle_id}.json", rec)
    _write(src / "cycles" / "latest_attempt.json", {
        "continuous_cycle_id": cycle_id,
        "cycle_outcome": "FAILED",
        "failure_stage": failure_stage,
        "projection_version": None,
        "predecessor_cycle_id": predecessor,
        "completed_at": completed_at,
    })


def test_import_records_distinct_timestamps(dirs):
    src, lab = dirs
    _publish_production_cycle(src, cycle_id="CRCYCLE-AAA",
                              completed_at="2026-10-05T02:34:36.613207+00:00")
    result = sync_latest_completed_cycle(lab, source=src)
    assert result.status == "IMPORTED"
    # Real production completion time is carried from the cycle record.
    assert result.production_completed_at == "2026-10-05T02:34:36.613207+00:00"
    # Local import time is distinct and present.
    assert result.imported_at is not None
    assert result.imported_at != result.production_completed_at
    # Projection publication (source history mtime) is recorded independently.
    assert result.projection_published_at is not None
    # Marker persists all three for cheap UNCHANGED reuse.
    marker = json.loads(
        (lab / "continuous_bridge_state.json").read_text(encoding="utf-8"))
    assert marker["production_completed_at"] == "2026-10-05T02:34:36.613207+00:00"
    assert marker["imported_at"] == result.imported_at
    assert marker["projection_published_at"] == result.projection_published_at


def test_unchanged_preserves_timestamps_from_marker(dirs):
    src, lab = dirs
    _publish_production_cycle(src, cycle_id="CRCYCLE-AAA",
                              completed_at="2026-10-05T02:34:36.613207+00:00")
    first = sync_latest_completed_cycle(lab, source=src)
    second = sync_latest_completed_cycle(lab, source=src)
    assert second.status == "UNCHANGED"
    assert second.production_completed_at == first.production_completed_at
    assert second.imported_at == first.imported_at
    assert second.projection_published_at == first.projection_published_at


def test_latest_attempt_diagnostic_reports_failed_without_importing(dirs):
    src, lab = dirs
    # A successful cycle exists AND a newer failed attempt supersedes it.
    _publish_production_cycle(src, cycle_id="CRCYCLE-AAA",
                              completed_at="2026-10-05T02:34:36+00:00")
    _publish_failed_attempt(src, cycle_id="CRCYCLE-FAIL",
                            predecessor="CRCYCLE-AAA",
                            failure_reason="FUTURE_DATED_GOVERNED_RECORD:trade_truth")
    result = sync_latest_completed_cycle(lab, source=src)

    # The successful cycle is still what gets imported (the failed attempt is
    # never imported and never replaces it).
    assert result.status == "IMPORTED"
    assert result.imported_cycle_id == "CRCYCLE-AAA"

    # The read-only diagnostic surfaces the real failed latest attempt.
    attempt = result.latest_attempt
    assert attempt is not None
    assert attempt["scope"] == "PRODUCTION_LATEST_ATTEMPT_DIAGNOSTIC"
    assert attempt["continuous_cycle_id"] == "CRCYCLE-FAIL"
    assert attempt["cycle_outcome"] == "FAILED"
    assert attempt["failure_stage"] == "FRONTIER"
    assert "FUTURE_DATED_GOVERNED_RECORD" in (attempt["failure_reason"] or "")
    assert attempt["imported"] is False
    assert attempt["is_successful"] is False

    # The authoritative imported projection is unaffected by the failed attempt.
    from research_engine.v10.continuous.research_projection import ResearchProjectionStore
    loaded = ResearchProjectionStore(lab / "projection").load_latest()
    assert loaded["continuous_cycle_id"] == "CRCYCLE-AAA"


def test_failed_attempt_cannot_replace_successful_result(dirs):
    """Even after a failed attempt supersedes, re-sync stays on the success."""
    src, lab = dirs
    _publish_production_cycle(src, cycle_id="CRCYCLE-AAA",
                              completed_at="2026-10-05T02:34:36+00:00")
    sync_latest_completed_cycle(lab, source=src)
    _publish_failed_attempt(src, cycle_id="CRCYCLE-FAIL",
                            predecessor="CRCYCLE-AAA")
    again = sync_latest_completed_cycle(lab, source=src)
    assert again.status == "UNCHANGED"
    assert again.imported_cycle_id == "CRCYCLE-AAA"
    # No failed cycle leaked into the Lab's cycle history.
    names = {p.name for p in (lab / "cycles" / "history").glob("*.json")}
    assert names == {"CRCYCLE-AAA.json"}
