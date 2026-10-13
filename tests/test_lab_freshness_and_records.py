"""Tests for the ORIGINAL Research Lab freshness correction and record
coexistence (findings/hypotheses/candidates) — SYNTHETIC fixtures only.

These exercise the server's pure helpers:
  * ``_freshness_block`` must present the REAL production completion time and
    keep the local import/sync time distinct — never present an import as a
    newly completed cycle; and it must surface a FAILED production latest
    attempt (observational, not imported).
  * ``_additional_records`` must expose historical + separate-local records
    alongside an authoritative projection, de-duped and labelled, never merged.

Nothing here is real production research; every record is a test fixture.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.v10.lab import server


# ─────────────────────────────────────────────────────────────────────────────
# _freshness_block
# ─────────────────────────────────────────────────────────────────────────────
def _success(cycle_id="CRCYCLE-AAA", version="RPROJ-AAA",
             completed_at="2026-10-05T02:34:36.613207+00:00"):
    return {
        "continuous_cycle_id": cycle_id,
        "cycle_outcome": "COMPLETED",
        "projection_version": version,
        "completed_at": completed_at,
    }


def test_freshness_distinguishes_completion_from_import(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "STATE_ROOT", tmp_path)
    projection = {"projection_version": "RPROJ-AAA"}
    success = _success()
    cycles = [{
        "continuous_cycle_id": "CRCYCLE-AAA", "cycle_outcome": "COMPLETED",
        "completed_at": "2026-10-05T02:34:36.613207+00:00",
        "imported_at": "2026-10-11T20:18:30Z",
    }]
    bridge_status = {
        "status": "UNCHANGED",
        "latest_attempt": None,
    }
    fr = server._freshness_block(
        projection=projection, cycles=cycles, success=success,
        projection_imported_at="2026-10-11T20:18:30Z",
        research_lag={}, bridge_status=bridge_status)

    # Real completion is 10-05; import is 10-11 — never conflated.
    assert fr["production_cycle_completed_at"] == "2026-10-05T02:34:36.613207+00:00"
    assert fr["projection_imported_at"] == "2026-10-11T20:18:30Z"
    assert fr["synced_at"] == "2026-10-11T20:18:30Z"
    assert fr["production_cycle_completed_at"] != fr["projection_imported_at"]
    # Deprecated aliases must point at REAL completion, not the import mtime.
    assert fr["projection_persisted_at"] == fr["production_cycle_completed_at"]
    assert fr["last_attempt_at"] == "2026-10-05T02:34:36.613207+00:00"
    # Elapsed since genuine completion is computed and positive.
    assert fr["seconds_since_completion"] is not None
    assert fr["seconds_since_completion"] > 0
    assert fr["last_successful_cycle_id"] == "CRCYCLE-AAA"


def test_freshness_surfaces_failed_production_latest_attempt(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "STATE_ROOT", tmp_path)
    bridge_status = {
        "status": "UNCHANGED",
        "latest_attempt": {
            "scope": "PRODUCTION_LATEST_ATTEMPT_DIAGNOSTIC",
            "continuous_cycle_id": "CRCYCLE-FAIL",
            "cycle_outcome": "FAILED",
            "failure_stage": "FRONTIER",
            "failure_reason": "FUTURE_DATED_GOVERNED_RECORD:trade_truth",
            "observed_at": "2026-10-11T02:26:14Z",
            "is_successful": False,
            "imported": False,
        },
    }
    fr = server._freshness_block(
        projection={"projection_version": "RPROJ-AAA"},
        cycles=[{"continuous_cycle_id": "CRCYCLE-AAA",
                 "cycle_outcome": "COMPLETED",
                 "completed_at": "2026-10-05T02:34:36+00:00",
                 "imported_at": "2026-10-11T20:18:30Z"}],
        success=_success(), projection_imported_at="2026-10-11T20:18:30Z",
        research_lag={}, bridge_status=bridge_status)

    # Last attempt reflects production reality (FAILED), not the imported cycle.
    assert fr["last_attempt_cycle_id"] == "CRCYCLE-FAIL"
    assert fr["last_attempt_outcome"] == "FAILED"
    assert fr["last_attempt_failure_stage"] == "FRONTIER"
    assert "FUTURE_DATED_GOVERNED_RECORD" in fr["last_attempt_failure_reason"]
    assert fr["last_attempt_imported"] is False
    # The last SUCCESS is still the imported successful cycle.
    assert fr["last_successful_cycle_id"] == "CRCYCLE-AAA"


def test_freshness_handles_no_completion_without_crashing(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "STATE_ROOT", tmp_path)
    fr = server._freshness_block(
        projection=None, cycles=[], success=None,
        projection_imported_at=None, research_lag={}, bridge_status={})
    assert fr["production_cycle_completed_at"] is None
    assert fr["time_since_completion"] == "unknown"
    assert fr["last_successful_cycle_id"] is None


# ─────────────────────────────────────────────────────────────────────────────
# _additional_records
# ─────────────────────────────────────────────────────────────────────────────
def test_additional_records_dedup_and_labels(monkeypatch, tmp_path):
    # No historical archive here -> that side reports unavailable; focus on the
    # separate-local-registry de-dup + labelling path.
    monkeypatch.setattr(server, "HISTORICAL_ARCHIVE", tmp_path / "no_archive")

    def fake_local():
        hyps = [{"hypothesis_id": "H-SHARED", "status": "ACTIVE"},
                {"hypothesis_id": "H-LOCALONLY", "status": "PROPOSED"}]
        cands = [{"candidate_id": "C-SHARED", "status": "PROPOSED"},
                 {"candidate_id": "C-LOCALONLY", "status": "PROPOSED"}]
        return hyps, cands

    monkeypatch.setattr(server, "_local_scientific_records", fake_local)

    projection = {
        "findings": [],
        "hypotheses": [{"hypothesis_id": "H-SHARED"}],   # already authoritative
        "candidates": [{"candidate_id": "C-SHARED"}],     # already authoritative
    }
    ar = server._additional_records(projection)

    # Shared records are NOT duplicated into additional_records.
    local_hyp_ids = {h["hypothesis_id"] for h in ar["separate_local_registry"]["hypotheses"]}
    local_cand_ids = {c["candidate_id"] for c in ar["separate_local_registry"]["candidates"]}
    assert local_hyp_ids == {"H-LOCALONLY"}
    assert local_cand_ids == {"C-LOCALONLY"}

    # Local records are explicitly labelled as non-authoritative + not live.
    for c in ar["separate_local_registry"]["candidates"]:
        assert c["authority"] == "SEPARATE_LOCAL_REGISTRY_NON_AUTHORITATIVE"
        assert c["human_approval_required"] is True
        assert c["belongs_to_current_projection"] is False

    assert ar["counts"]["local_hypotheses"] == 1
    assert ar["counts"]["local_candidates"] == 1
    # The notice makes the authority boundary explicit.
    assert "NOT part of the current projection" in ar["notice"]


def test_additional_records_survives_projection_change(monkeypatch, tmp_path):
    """A changed/empty projection must not remove inspectable local records."""
    monkeypatch.setattr(server, "HISTORICAL_ARCHIVE", tmp_path / "no_archive")
    monkeypatch.setattr(server, "_local_scientific_records",
                        lambda: ([], [{"candidate_id": "C-LOCALONLY", "status": "PROPOSED"}]))
    ar = server._additional_records({"findings": [], "hypotheses": [], "candidates": []})
    assert ar["counts"]["local_candidates"] == 1
