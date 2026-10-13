"""Two consecutive offline FRONTIER diagnostic attempts over one shared
resumable-acquisition checkpoint and isolated per-attempt state roots.

No network, no production data, no real research cycle: an in-memory S3 double
only. Proves the diagnostic wiring points the checkpoint at a persistent
directory (beside the verified-body cache), that a timed-out attempt preserves
the completed-dataset checkpoints, that the next attempt reuses them after fresh
source-authority verification without re-downloading bodies, that the recovery
measurements are reported, and that the resumed snapshot is fingerprint-identical
to an uninterrupted run.
"""
from __future__ import annotations

from datetime import date
import json

import pytest

import scripts.run_frontier_diagnostic as launcher
from core.production_data_contract import s3_base_prefix
from research_engine.v10.continuous.frontier_coordinator import (
    CHECKPOINT_SCHEMA, run_frontier_snapshot_cycle,
)
from research_engine.v10.continuous.research_loop import run_continuous_research_cycle
from research_engine.v10.investigation_snapshot import BOUND_DATASETS
from tests.test_frontier_coordinator import MemoryS3, _body, _key, _objects, _source

DAY = date(2026, 10, 3)
CHECKPOINT_NAME = "acquisition_checkpoint.json"


class RecordingS3(MemoryS3):
    """MemoryS3 that records exactly which bodies were fetched by GET."""

    def __init__(self, objects, versions=None):
        super().__init__(objects, versions)
        self.fetched: list[str] = []

    def get_object(self, **kwargs):
        self.fetched.append(str(kwargs["Key"]))
        return super().get_object(**kwargs)

    def dataset_of(self, key: str) -> str:
        for dataset in BOUND_DATASETS:
            if key.startswith(s3_base_prefix(dataset)):
                return dataset
        return ""

    def gets_for(self, dataset: str) -> int:
        return sum(1 for key in self.fetched if self.dataset_of(key) == dataset)


def _versioned(objects) -> RecordingS3:
    return RecordingS3(dict(objects), {key: "v1" for key in objects})


def _add(objects, dataset: str, count: int) -> dict:
    for index in range(1, count + 1):
        key = _key(dataset, part=f"part-{index:03d}.jsonl")
        objects[key] = _body(dataset, marker=f"{dataset}-{index}")
    return objects


def _interrupt_after(index: int):
    """Abort the run the moment dataset ``index`` has been verified.

    The checkpoint for that dataset is persisted before the verified event is
    emitted, so the partial checkpoint always covers datasets ``1..index``.
    """
    def callback(event):
        if (event["phase"] == "FRONTIER_DATASET_VERIFIED"
                and event["dataset_index"] >= index):
            raise KeyboardInterrupt
    return callback


def _forbidden(*args, **kwargs):
    raise AssertionError("the diagnostic must stop at the FRONTIER boundary")


def _attempt(fake, *, state_root, cache_directory, **frontier):
    """One offline diagnostic attempt through the real continuous-research path.

    The shared ``cache_directory`` is what makes the checkpoint persistent across
    attempts; the isolated ``state_root`` keeps the authoritative scientific state
    (frontier_state/manifests/cycles) per attempt.
    """
    return run_continuous_research_cycle(
        state_root=state_root,
        frontier_diagnostic=True,
        frontier_diagnostic_cache_directory=cache_directory,
        frontier_kwargs={"source": _source(fake), "as_of_date": DAY, **frontier},
        question_runner=_forbidden,
    )


def _checkpoint_path(shared):
    return shared / "frontier_acquisition_checkpoint" / CHECKPOINT_NAME


def _success_fingerprint(state_root):
    state = json.loads(
        (state_root / "frontier_state" / "latest_success.json").read_text(encoding="utf-8"))
    return state["last_successful_snapshot_id"], state[
        "last_successful_snapshot_fingerprint"]


# ─── Two consecutive offline attempts ────────────────────────────────────────


def test_timed_out_attempt_preserves_checkpoint_then_next_attempt_resumes(tmp_path):
    objects = _add(_objects(), "decision_trace", 4)
    fake = _versioned(objects)
    shared = tmp_path / "shared"
    cache = shared / "verified_object_bodies"
    attempt1 = tmp_path / "state-attempt-1"
    attempt2 = tmp_path / "state-attempt-2"

    # ── Attempt 1: interrupted after dataset 2 (a timed-out acquisition). ──
    with pytest.raises(KeyboardInterrupt):
        _attempt(fake, state_root=attempt1, cache_directory=cache,
                 progress_callback=_interrupt_after(2))

    checkpoint = _checkpoint_path(shared)
    assert checkpoint.exists()
    assert set(json.loads(checkpoint.read_text(encoding="utf-8"))["datasets"]) == set(
        BOUND_DATASETS[:2])
    # Nothing authoritative was written into the isolated attempt root.
    assert not (attempt1 / "frontier_state" / "latest_success.json").exists()
    assert not (attempt1 / "cycles" / "latest_success.json").exists()
    assert not list((attempt1 / "manifests").glob("ISNAP-*.json"))

    # ── Attempt 2: fresh isolated state root, the SAME persistent checkpoint. ──
    fake.fetched.clear()
    events: list[dict] = []
    result = _attempt(fake, state_root=attempt2, cache_directory=cache,
                      progress_callback=events.append)

    assert result.cycle_outcome == "FRONTIER_DIAGNOSTIC_STOPPED"

    # Completed datasets are reused: no body GET for datasets 1-2, acquired after.
    for dataset in BOUND_DATASETS[:2]:
        assert fake.gets_for(dataset) == 0
    for dataset in BOUND_DATASETS[2:]:
        assert fake.gets_for(dataset) >= 1

    # Explicit recovery measurements are reported on the persisted substages.
    started = next(e for e in events if e["phase"] == "FRONTIER_DISCOVERY_STARTED")
    assert started["resumed_datasets"] == 2
    assert started["checkpoint_loaded"] is True
    assert started["checkpoint_load_seconds"] >= 0.0
    assert started["resumed_objects"] > 0
    assert started["checkpoint_path"].replace("\\", "/").endswith(
        "frontier_acquisition_checkpoint/" + CHECKPOINT_NAME)

    completed = next(e for e in events if e["phase"] == "FRONTIER_DISCOVERY_COMPLETED")
    assert completed["datasets_recovered"] == 2
    assert completed["objects_reused"] > 0
    assert completed["revalidation_seconds"] >= 0.0
    assert completed["remaining_datasets_seconds"] > 0.0

    # A successful attempt clears the shared checkpoint only after its snapshot
    # is safely persisted in the attempt's own authoritative state.
    assert not checkpoint.exists()
    resumed_id, resumed_fp = _success_fingerprint(attempt2)

    # Deterministic: identical to an uninterrupted acquisition of the same objects.
    clean = run_frontier_snapshot_cycle(
        source=_source(_versioned(objects)),
        state_directory=tmp_path / "clean-state",
        manifest_directory=tmp_path / "clean-manifests",
        checkpoint_directory=tmp_path / "clean-checkpoint",
        as_of_date=DAY,
    )
    assert resumed_id == clean.snapshot_id
    assert resumed_fp == clean.fingerprint


def test_checkpoint_isolation_keeps_authoritative_state_per_attempt(tmp_path):
    fake = _versioned(_objects())
    shared = tmp_path / "shared"
    cache = shared / "verified_object_bodies"
    attempt1 = tmp_path / "state-attempt-1"

    _attempt(fake, state_root=attempt1, cache_directory=cache)

    # The successful attempt's authoritative state lives only in its own root.
    assert (attempt1 / "frontier_state" / "latest_success.json").exists()
    assert not (tmp_path / "state-attempt-2").exists()
    # And its success cleared the shared checkpoint (nothing left to resume).
    assert not _checkpoint_path(shared).exists()


def test_corrupt_checkpoint_falls_back_to_cold_acquisition(tmp_path):
    objects = _objects()
    fake = _versioned(objects)
    shared = tmp_path / "shared"
    checkpoint_dir = shared / "frontier_acquisition_checkpoint"
    checkpoint_dir.mkdir(parents=True)
    (checkpoint_dir / CHECKPOINT_NAME).write_text("}{ not json", encoding="utf-8")

    events: list[dict] = []
    result = _attempt(fake, state_root=tmp_path / "state",
                      cache_directory=(shared / "verified_object_bodies"),
                      progress_callback=events.append)

    # A corrupt checkpoint never breaks the governed run; it degrades to a cold
    # acquisition that re-downloads every object exactly once.
    assert result.cycle_outcome == "FRONTIER_DIAGNOSTIC_STOPPED"
    started = next(e for e in events if e["phase"] == "FRONTIER_DISCOVERY_STARTED")
    assert started["checkpoint_loaded"] is False
    assert started["resumed_datasets"] == 0
    for dataset in BOUND_DATASETS:
        assert fake.gets_for(dataset) == 1


# ─── Watchdog surfaces the recovery measurements ─────────────────────────────


def _progress_document(substage):
    return {
        "cycle_attempt_id": "ATTEMPT",
        "status": "RUNNING",
        "current_stage": "FRONTIER",
        "stages": {"FRONTIER": {"status": "RUNNING", "details": {
            "frontier_substage": substage,
            "frontier_substage_events": 4,
        }}},
    }


def test_watchdog_sample_reports_recovery_measurements():
    document = _progress_document({
        "phase": "FRONTIER_DISCOVERY_COMPLETED",
        "checkpoint_loaded": True,
        "checkpoint_load_seconds": 0.002,
        "resumed_datasets": 2,
        "resumed_objects": 410,
        "datasets_recovered": 2,
        "objects_reused": 410,
        "revalidation_seconds": 1.5,
        "recovered_datasets_seconds": 3.25,
        "remaining_datasets_seconds": 12.75,
    })
    sample = launcher._sample(document, "available", pid=1, elapsed=1.0)

    recovery = sample["recovery"]
    assert recovery["checkpoint_loaded"] is True
    assert recovery["checkpoint_load_seconds"] == 0.002
    assert recovery["resumed_datasets"] == 2
    assert recovery["resumed_objects"] == 410
    assert recovery["datasets_recovered"] == 2
    assert recovery["objects_reused"] == 410
    assert recovery["revalidation_seconds"] == 1.5
    assert recovery["remaining_datasets_seconds"] == 12.75


def test_watchdog_sample_reports_no_recovery_for_a_cold_attempt():
    document = _progress_document({"phase": "FRONTIER_DISCOVERY_STARTED",
                                   "checkpoint_loaded": False,
                                   "resumed_datasets": 0, "resumed_objects": 0})
    sample = launcher._sample(document, "available", pid=1, elapsed=1.0)
    recovery = sample["recovery"]
    assert recovery["checkpoint_loaded"] is False
    assert recovery["resumed_datasets"] == 0
    assert recovery["resumed_objects"] == 0
    # The aggregate recovery timings only appear on FRONTIER_DISCOVERY_COMPLETED.
    for key in ("datasets_recovered", "objects_reused", "revalidation_seconds",
                "recovered_datasets_seconds", "remaining_datasets_seconds"):
        assert recovery[key] is None


def test_checkpoint_schema_constant_is_stable():
    # The boundary check binds the schema name; a rename would silently invalidate
    # every persisted checkpoint, so it must stay explicit and versioned.
    assert CHECKPOINT_SCHEMA == "canonical_research_frontier_acquisition_checkpoint_v1"
