"""Focused proofs for Hardening 1.5 candidate-activation TOCTOU closure."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

import core.research_events as research_events
import research_engine.lifecycle.candidate_activation_gate as activation_gate
import research_engine.v10.baselines.baseline_authority as authority
from research_engine.v10.baselines.models import BaselineSnapshot
from research_engine.v10.baselines.snapshot_registry import SnapshotRegistry
from research_engine.v10.candidates.candidate_registry import CandidateRegistry
from research_engine.v10.candidates.models import CandidateRecord, CandidateStatus


BASELINE = "BL-H15-A"
NEXT_BASELINE = "BL-H15-B"


@pytest.fixture(autouse=True)
def _isolated_authority(tmp_path, monkeypatch):
    baseline_dir = tmp_path / "baselines"
    monkeypatch.setattr(authority, "_BASELINES_DIR", str(baseline_dir))
    monkeypatch.setattr(
        authority,
        "_ACTIVE_POINTER_FILE",
        str(baseline_dir / "active_baseline.json"),
    )
    config_hash = research_events.compute_config_hash()
    registry = SnapshotRegistry(baselines_dir=str(baseline_dir))
    for snapshot_id in (BASELINE, NEXT_BASELINE):
        registry.save(BaselineSnapshot(
            snapshot_id=snapshot_id,
            config_hash=config_hash,
            identity_hash=f"identity-{snapshot_id}",
        ))
    authority.set_active(BASELINE, actor="test", reason="Hardening 1.5 fixture")


def _candidate(candidate_id="OPT-H15"):
    return CandidateRecord(
        candidate_id=candidate_id,
        hypothesis_id="HYP-H15",
        baseline_id=BASELINE,
        status=CandidateStatus.PROPOSED,
        change_definition={
            "type": "direction_inversion",
            "action": "invert_pattern_direction",
            "baseline_config_hash": research_events.compute_config_hash(),
        },
    )


def _store_candidate(tmp_path, candidate=None):
    candidate_dir = tmp_path / "candidates"
    CandidateRegistry(storage_dir=str(candidate_dir)).create(candidate or _candidate())
    return candidate_dir


def _reload(candidate_dir, candidate_id="OPT-H15"):
    return CandidateRegistry(storage_dir=str(candidate_dir)).get(candidate_id)


def test_normal_activation_preserves_identity_and_provenance(tmp_path):
    candidate_dir = _store_candidate(tmp_path)
    before = _reload(candidate_dir).to_dict()

    result = activation_gate.activate_eligible_candidates(
        registry_dir=str(candidate_dir)
    )

    after = _reload(candidate_dir)
    assert result.candidates_activated == 1
    assert after.status == CandidateStatus.SHADOW_TESTING
    assert after.candidate_id == before["candidate_id"]
    assert after.baseline_id == before["baseline_id"]
    assert after.change_definition == before["change_definition"]


def test_baseline_change_in_old_window_fails_closed(tmp_path, monkeypatch):
    candidate_dir = _store_candidate(tmp_path)
    before = _reload(candidate_dir).to_dict()
    real_check = activation_gate._check_baseline_provenance
    checks = 0

    def change_after_preliminary_check(candidate):
        nonlocal checks
        checks += 1
        outcome = real_check(candidate)
        if checks == 1:
            authority.set_active(
                NEXT_BASELINE,
                actor="test",
                reason="deterministic old-window mutation",
            )
        return outcome

    monkeypatch.setattr(
        activation_gate,
        "_check_baseline_provenance",
        change_after_preliminary_check,
    )
    result = activation_gate.activate_eligible_candidates(
        registry_dir=str(candidate_dir)
    )

    after = _reload(candidate_dir)
    assert checks == 2
    assert result.candidates_activated == 0
    assert any("stale_baseline" in skip["reason"] for skip in result.skips)
    assert after.to_dict() == before


def test_config_change_in_old_window_fails_closed(tmp_path, monkeypatch):
    candidate_dir = _store_candidate(tmp_path)
    before = _reload(candidate_dir).to_dict()
    current_hash = research_events.compute_config_hash()
    reads = 0

    def changed_config_hash():
        nonlocal reads
        reads += 1
        return current_hash if reads == 1 else "changed-config-hash"

    monkeypatch.setattr(research_events, "compute_config_hash", changed_config_hash)
    result = activation_gate.activate_eligible_candidates(
        registry_dir=str(candidate_dir)
    )

    after = _reload(candidate_dir)
    assert reads == 2
    assert result.candidates_activated == 0
    assert any("stale_config" in skip["reason"] for skip in result.skips)
    assert after.to_dict() == before


def test_set_active_waits_for_validation_commit_and_does_not_deadlock(
    tmp_path, monkeypatch
):
    candidate_dir = _store_candidate(tmp_path)
    commit_entered = threading.Event()
    allow_commit = threading.Event()
    change_started = threading.Event()
    change_finished = threading.Event()
    real_update = CandidateRegistry.update_status
    gate_result = []

    def paused_update(self, candidate_id, new_status):
        commit_entered.set()
        assert allow_commit.wait(2), "test did not release candidate commit"
        return real_update(self, candidate_id, new_status)

    monkeypatch.setattr(CandidateRegistry, "update_status", paused_update)

    gate_thread = threading.Thread(
        target=lambda: gate_result.append(
            activation_gate.activate_eligible_candidates(
                registry_dir=str(candidate_dir)
            )
        )
    )
    gate_thread.start()
    assert commit_entered.wait(2), "gate did not reach the guarded commit"

    def change_baseline():
        change_started.set()
        authority.set_active(
            NEXT_BASELINE,
            actor="test",
            reason="concurrent authority change",
        )
        change_finished.set()

    change_thread = threading.Thread(target=change_baseline)
    change_thread.start()
    assert change_started.wait(2)
    assert not change_finished.wait(0.1)

    allow_commit.set()
    gate_thread.join(2)
    change_thread.join(2)

    assert not gate_thread.is_alive()
    assert not change_thread.is_alive()
    assert gate_result[0].candidates_activated == 1
    assert _reload(candidate_dir).status == CandidateStatus.SHADOW_TESTING
    assert authority.get_active().active_baseline_id == NEXT_BASELINE


def test_persistence_failure_leaves_prior_candidate_status_intact(
    tmp_path, monkeypatch
):
    candidate_dir = _store_candidate(tmp_path)
    before = _reload(candidate_dir).to_dict()

    def fail_persist(self):
        raise OSError("simulated candidate commit failure")

    monkeypatch.setattr(CandidateRegistry, "_persist", fail_persist)
    result = activation_gate.activate_eligible_candidates(
        registry_dir=str(candidate_dir)
    )

    assert result.candidates_activated == 0
    assert result.candidates_skipped == 1
    assert _reload(candidate_dir).to_dict() == before
