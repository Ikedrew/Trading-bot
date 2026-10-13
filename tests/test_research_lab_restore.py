"""Manual-only original Lab restore regression, using actual persisted records."""
import json
from pathlib import Path
import pytest
from research_engine.v10.lab import server

ROOT = Path(__file__).resolve().parents[1]


def test_restore_preserves_all_actual_candidate_statuses(monkeypatch):
    monkeypatch.setattr(server, "OPT_REGISTRY", ROOT / "data/research/optimisation/registry.json")
    monkeypatch.setattr(server, "CANDIDATE_REGISTRY_DIR", ROOT / "data/research/candidates")
    raw = json.loads(server.OPT_REGISTRY.read_text(encoding="utf-8"))["candidates"]
    legacy = [json.loads(line) for line in
              (server.CANDIDATE_REGISTRY_DIR / "candidates.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    expected = {**raw, **{r["candidate_id"]: r for r in legacy}}
    _, candidates = server._local_scientific_records()
    assert {r["candidate_id"]: r["status"] for r in candidates} == {
        key: row["status"] for key, row in expected.items()}
    for row in candidates:
        assert row["display_provenance"]["belongs_to_displayed_question_cycle"] is False
    assert {row["candidate_id"] for row in legacy} <= {r["candidate_id"] for r in candidates}


def test_read_only_run_cannot_launch_or_acquire_lock(monkeypatch):
    monkeypatch.setattr(server, "READ_ONLY", True)
    def forbidden(*args, **kwargs):
        pytest.fail("Read-only Lab attempted research execution or lock mutation")
    monkeypatch.setattr(server, "_acquire_lock", forbidden)
    monkeypatch.setattr(server.subprocess, "Popen", forbidden)
    status, result = server.start_run()
    assert status == 403
    assert result["error"] == "READ_ONLY"


def test_actual_completed_transfer_appears_in_original_lab(monkeypatch, tmp_path):
    # Requires the operator's successfully imported real historical archive.
    # No synthetic completion or substitute scientific record is constructed.
    monkeypatch.setattr(server, "READ_ONLY", True)
    monkeypatch.setattr(server, "STATE_ROOT", tmp_path / "no_native_projection")
    monkeypatch.setattr(server, "HISTORICAL_ARCHIVE", ROOT / "reports/research_lab_archive")
    monkeypatch.setattr(server, "OPT_REGISTRY", ROOT / "data/research/optimisation/registry.json")
    monkeypatch.setattr(server, "CANDIDATE_REGISTRY_DIR", ROOT / "data/research/candidates")
    def forbidden(*args, **kwargs):
        pytest.fail("Historical display imported live evaluator authority")
    monkeypatch.setattr(server, "_annotate_evaluation_currency", forbidden)
    state = server.build_state()
    from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
    assert {q["question_id"] for q in state["questions"]} == set(BASELINE_QUESTION_IDS)
    assert len(state["findings"]) == 2
    assert len(state["hypotheses"]) == 4
    assert len(state["candidates"]) == 5
    assert state["completed_transfer"]["cycle_id"] == "QCYCLE-5B5C406636540AE99AB46705B538F405"
    assert state["read_only"]
    assert state["completed_transfer"]["verification"]["raw_evidence_bodies_verified"] is False
    assert all(not q["evidence_references"] for q in state["questions"])
    assert sum(q["evidence_reference_count"] for q in state["questions"]) > 0
    assert {c["candidate_id"] for c in state["candidates"] if c.get("lineage_gaps")} >= {
        "OPT-fcf078b8", "OPT-441a8783", "OPT-3292f718"}
