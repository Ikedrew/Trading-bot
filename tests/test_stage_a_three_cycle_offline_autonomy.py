"""Stage A - Foundational Offline Scientific Autonomy: three-cycle integration.

These tests drive the ACTUAL production research cadence
(``run_cadence_tick`` -> ``run_continuous_research_cycle``) against a
deterministic in-memory S3 fixture, exercising the real frontier freeze,
canonical question cycle, scientific-state bridge, Q71+, validation-queue and
Research Lab projection paths.  Nothing here fabricates a scientific result:
every status is whatever the governed runners actually produce (COMPLETE /
INSUFFICIENT_DATA / WAITING_FOR_DATA / BLOCKED / INVALID / ALIAS_OR_SUPERSEDED),
and blocked or waiting states are asserted to remain visible rather than cleared.

The single production change is the optional ``offline_isolation`` seam on
``run_cadence_tick`` (research_engine/v10/continuous/research_cadence.py): a
bundle of the exact source/directory keyword arguments
``run_continuous_research_cycle`` already exposes.  When the bundle is None the
cadence is byte-identical to production, so the injected source never reaches
the live S3/AWS fallback and all stage output stays inside a temp directory.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from test_canonical_question_cycle import (  # noqa: E402
    MemoryS3, _objects, _key, _jsonl, _row,
)
from research_engine.data_access.s3_source import S3ResearchDataSource  # noqa: E402
from research_engine.v10.continuous.research_cadence import run_cadence_tick  # noqa: E402
from research_engine.v10.continuous.research_lab import build_lab_view  # noqa: E402
from research_engine.v10.continuous.research_projection import (  # noqa: E402
    ResearchProjectionStore,
)
from research_engine.v10.continuous.scientific_state_store import (  # noqa: E402
    ScientificStateStore,
)
from research_engine.v10.continuous.cycle_state import (  # noqa: E402
    ContinuousCycleStore,
    _pid_is_alive,
)
from research_engine.v10.optimisation.optimisation_registry import (  # noqa: E402
    OptimisationRegistry,
)
from research_engine.v10.investigation_snapshot import BOUND_DATASETS  # noqa: E402

AS_OF = date(2026, 10, 3)
CANONICAL_COUNT = 70
# A single decision_trace row is the deliberate, scientifically meaningful change
# introduced between cycle 2 (unchanged) and cycle 3 (changed).
CHANGED_DATASET = "decision_trace"


def _isolation_bundle(tmp: Path, source: S3ResearchDataSource) -> dict:
    """Exact ``run_continuous_research_cycle`` seams EXCEPT ``state_root`` and the
    three keys the cadence owns (q71_evaluator_registry / validation_executor /
    forward_executor), which would otherwise be duplicate kwargs.  Every path
    points inside ``tmp`` so no live S3/AWS fallback is reached and no production
    directory is written."""
    return dict(
        frontier_kwargs={
            "source": source,
            "state_directory": tmp / "frontier",
            "manifest_directory": tmp / "manifests",
            "as_of_date": AS_OF,
        },
        question_kwargs={
            "source": source,
            "manifest_directory": tmp / "manifests",
            "state_directory": tmp / "questions",
        },
        bridge_kwargs={
            "scientific_state_directory": tmp / "science",
            "optimisation_registry_directory": tmp / "registry",
            "question_state_directory": tmp / "questions",
        },
        observation_space_manifest_directory=tmp / "manifests",
        observation_space_directory=tmp / "observation_space",
        production_coverage_directory=tmp / "production_coverage",
        production_coverage_store_path=tmp / "research_coverage.json",
        candle_authority_directory=tmp / "m5_candle_authority",
        counterfactual_evidence_directory=tmp / "counterfactual_evidence",
        q71_evidence_source=source,
    )


def _status_counts(projection: dict) -> dict:
    counts: dict[str, int] = {}
    for row in projection.get("canonical_questions") or []:
        status = str((row.get("result") or {}).get("status") or "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _result_ids(projection: dict) -> dict:
    return {row["question_id"]: (row.get("result") or {}).get("result_id")
            for row in projection["canonical_questions"]}


def _run_tick(state_root: Path, bundle: dict) -> dict:
    t0 = time.perf_counter()
    report = run_cadence_tick(
        state_root=state_root, force_fast_cycle=True, force_deep_job=False,
        offline_isolation=bundle)
    elapsed = time.perf_counter() - t0
    projection = ResearchProjectionStore(state_root / "projection").load_latest()
    return {
        "report": report,
        "fast_outcome": report.fast_cycle_outcome,
        "fast_elapsed": report.fast_cycle_elapsed_seconds,
        "elapsed": round(elapsed, 3),
        "projection": projection,
        "changed_datasets": (projection.get("data_frontier") or {}).get("changed_datasets"),
        "snapshot_id": (projection.get("data_frontier") or {}).get("snapshot_id"),
        "snapshot_fingerprint": (projection.get("data_frontier") or {}).get("snapshot_fingerprint"),
        "projection_version": projection.get("projection_version"),
        "cycle_id": projection.get("canonical_question_cycle_id"),
        "status_counts": _status_counts(projection),
        "canonical_count": len(projection.get("canonical_questions") or []),
    }


@pytest.fixture(scope="module")
def three_cycles(tmp_path_factory):
    """Run the three REAL production cadence cycles once.

    Cycle 1 baseline -> Cycle 2 unchanged evidence -> Cycle 3 changed evidence
    (one decision_trace row mutated).  Returns the per-cycle facts and the
    Research Lab view so each test asserts actual governed behaviour rather than
    any fabricated success.
    """
    tmp = tmp_path_factory.mktemp("stage_a_cycles")
    fake = MemoryS3(_objects())
    source = S3ResearchDataSource(bucket="stage-a-fixture", client=fake)
    OptimisationRegistry(str(tmp / "registry")).save()
    state_root = tmp / "continuous"
    bundle = _isolation_bundle(tmp, source)

    # Cycle 1 - baseline (first bind of all datasets).
    c1 = _run_tick(state_root, bundle)
    proj1 = ResearchProjectionStore(state_root / "projection").load_latest()

    # Cycle 2 - unchanged evidence (same source objects, same as_of date).
    c2 = _run_tick(state_root, bundle)
    proj2 = ResearchProjectionStore(state_root / "projection").load_latest()

    # Cycle 3 - changed evidence: a single decision_trace row is replaced.
    fake.objects[_key(CHANGED_DATASET)] = _jsonl(_row(CHANGED_DATASET, "changed"))
    c3 = _run_tick(state_root, bundle)
    proj3 = ResearchProjectionStore(state_root / "projection").load_latest()

    ids1 = _result_ids(proj1)
    ids3 = _result_ids(proj3)
    reevaluated = sorted(q for q in ids1 if ids1[q] != ids3[q])
    retained = sorted(q for q in ids1 if ids1[q] == ids3[q])

    science = ScientificStateStore(tmp / "science").document
    lab = build_lab_view(proj3)
    return {
        "tmp": tmp, "source": source, "state_root": state_root, "bundle": bundle,
        "c1": c1, "c2": c2, "c3": c3,
        "proj1": proj1, "proj2": proj2, "proj3": proj3,
        "reevaluated": reevaluated, "retained": retained,
        "science": science, "lab": lab,
    }


def test_cycle1_baseline_accounts_for_70_and_publishes(three_cycles):
    c1 = three_cycles["c1"]
    # The real production cadence completed the baseline cycle.
    assert c1["fast_outcome"] == "COMPLETED"
    # Exactly the 70 registered canonical questions are accounted for.
    assert c1["canonical_count"] == CANONICAL_COUNT
    # An authoritative projection was published with a stable identity.
    assert c1["projection_version"]
    assert c1["cycle_id"]
    assert c1["snapshot_id"]
    # First bind changes every governed dataset.
    assert set(c1["changed_datasets"]) == set(BOUND_DATASETS)
    # Truthful governed statuses: blocked and waiting states remain visible.
    counts = c1["status_counts"]
    assert counts.get("BLOCKED", 0) >= 1
    assert counts.get("WAITING_FOR_DATA", 0) >= 1
    assert sum(counts.values()) == CANONICAL_COUNT
    # Every canonical question is present as an immutable row in the projection.
    assert len(three_cycles["proj1"]["canonical_questions"]) == CANONICAL_COUNT


def test_cycle2_unchanged_evidence_is_reused_without_duplicate_work(three_cycles):
    c1, c2 = three_cycles["c1"], three_cycles["c2"]
    # Unchanged evidence is governed as NO_NEW_RESEARCH_EVIDENCE, not a fresh run.
    assert c2["fast_outcome"] == "NO_NEW_RESEARCH_EVIDENCE"
    # The identical evidence identity is reused: same snapshot, cycle, projection.
    assert c2["snapshot_id"] == c1["snapshot_id"]
    assert c2["cycle_id"] == c1["cycle_id"]
    assert c2["projection_version"] == c1["projection_version"]
    # Findings/hypotheses are preserved and no new finding is invented.
    assert c2["status_counts"] == c1["status_counts"]
    assert c2["canonical_count"] == CANONICAL_COUNT
    # No new immutable question results were minted (all 70 result_ids stable).
    ids1 = _result_ids(three_cycles["proj1"])
    ids2 = _result_ids(three_cycles["proj2"])
    assert ids1 == ids2


def test_cycle3_changed_evidence_reevaluates_only_affected_questions(three_cycles):
    c1, c3 = three_cycles["c1"], three_cycles["c3"]
    # The deliberate change produced a new governed cycle and new evidence id.
    assert c3["fast_outcome"] == "COMPLETED"
    assert c3["snapshot_id"] != c1["snapshot_id"]
    assert c3["cycle_id"] != c1["cycle_id"]
    assert c3["projection_version"] != c1["projection_version"]
    # Exactly the mutated dataset is represented as changed.
    assert list(c3["changed_datasets"]) == [CHANGED_DATASET]
    # Relevant questions were reevaluated; unaffected questions were retained.
    reevaluated, retained = three_cycles["reevaluated"], three_cycles["retained"]
    assert len(reevaluated) + len(retained) == CANONICAL_COUNT
    assert len(reevaluated) >= 1
    # Every question that depends on decision_trace must have been reevaluated.
    from research_engine.registry.research_question_registry import REGISTRY
    direct = {q.id for q in REGISTRY
              if any(str(s.value) == CHANGED_DATASET for s in q.data_sources)}
    assert direct, "expected questions that depend on decision_trace"
    assert direct <= set(reevaluated)
    # The change is NOT forced to produce a positive finding: the governed
    # status mix is unchanged and no findings/hypotheses were fabricated.
    assert c3["status_counts"] == c1["status_counts"]
    science = three_cycles["science"]
    assert len(science.get("findings") or {}) == 0
    assert len(science.get("hypotheses") or {}) == 0
    # Lineage is preserved and governed: the changed snapshot reevaluated exactly
    # its affected questions (which now bind the NEW snapshot id), while every
    # unaffected question was RETAINED and keeps its prior immutable result bound
    # to the original snapshot.  Retaining an unchanged result against its own
    # evidence is the correct, lineage-preserving behaviour -- not a regression.
    proj3 = three_cycles["proj3"]
    result_by_qid = {row["question_id"]: (row.get("result") or {})
                     for row in proj3["canonical_questions"]}
    for qid in reevaluated:
        assert result_by_qid[qid].get("snapshot_id") == c3["snapshot_id"]
    for qid in retained:
        assert result_by_qid[qid].get("snapshot_id") == c1["snapshot_id"]
    for qid in retained:
        assert result_by_qid[qid].get("snapshot_id") == c1["snapshot_id"]


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — interruption at a governed boundary, then restart
# ─────────────────────────────────────────────────────────────────────────────

def _isolated_state(tmp: Path):
    """Fresh isolated fixture state (source + registry + bundle + state_root)."""
    fake = MemoryS3(_objects())
    source = S3ResearchDataSource(bucket="stage-a-interrupt", client=fake)
    OptimisationRegistry(str(tmp / "registry")).save()
    state_root = tmp / "continuous"
    state_root.mkdir(parents=True, exist_ok=True)
    return source, state_root, _isolation_bundle(tmp, source)


def test_interruption_at_governed_lease_boundary_is_not_a_publication(tmp_path):
    """A live cycle lease is honoured: the interrupted tick publishes NOTHING."""
    source, state_root, bundle = _isolated_state(tmp_path)
    lock = state_root / "active_cycle.lock"
    # A live holder (this very process) is mid-cycle at the governed boundary.
    lock.write_text(json.dumps({
        "lease_id": "STAGE-A-HOLDER-LIVE", "pid": os.getpid(),
        "started_at": "2026-10-03T00:00:00Z"}), encoding="utf-8")

    report = run_cadence_tick(
        state_root=state_root, force_fast_cycle=True, offline_isolation=bundle)

    # The interruption is refused, never silently treated as a success.
    assert "ALREADY_ACTIVE" in str(report.fast_cycle_outcome)
    # No incomplete output is treated as a successful publication.
    projection = ResearchProjectionStore(state_root / "projection").load_latest()
    assert projection is None
    store = ContinuousCycleStore(state_root / "cycles")
    assert store.load_latest_success() is None
    history = state_root / "cycles" / "history"
    history_files = list(history.glob("*.json")) if history.exists() else []
    assert len(history_files) == 0
    # The lease is still held by its (live) owner; it was not stolen.
    assert lock.exists()


def test_restart_recovers_state_without_duplicate_success(tmp_path):
    """After the holder crashes (stale dead-pid lease), restart reclaims the
    boundary, recovers persisted identity, and records exactly ONE success."""
    source, state_root, bundle = _isolated_state(tmp_path)
    lock = state_root / "active_cycle.lock"
    # The previous holder crashed: its lease now records a dead pid (0).
    lock.write_text(json.dumps({
        "lease_id": "STAGE-A-HOLDER-CRASHED", "pid": 0,
        "started_at": "2026-10-03T00:00:00Z"}), encoding="utf-8")

    report = run_cadence_tick(
        state_root=state_root, force_fast_cycle=True, offline_isolation=bundle)
    assert report.fast_cycle_outcome == "COMPLETED"

    # Incomplete prior output was NOT mistaken for success; the restart is the
    # single authoritative publication and the stale lease was reclaimed.
    projection = ResearchProjectionStore(state_root / "projection").load_latest()
    assert projection is not None
    assert projection.get("projection_version")
    assert (projection.get("data_frontier") or {}).get("snapshot_id")
    store = ContinuousCycleStore(state_root / "cycles")
    success = store.load_latest_success()
    assert success is not None
    assert not lock.exists()  # released by the rightful owner
    # Persisted identities are recovered and NOT duplicated.
    history = state_root / "cycles" / "history"
    history_files = list(history.glob("*.json")) if history.exists() else []
    completed = [
        f for f in history_files
        if json.loads(f.read_text(encoding="utf-8")).get("cycle_outcome") == "COMPLETED"
    ]
    assert len(history_files) == 1
    assert len(completed) == 1
    # The 70 questions survived the interruption/restart intact.
    assert len(projection.get("canonical_questions") or []) == CANONICAL_COUNT


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — publication parity: Research Lab vs. persisted scientific records
# ─────────────────────────────────────────────────────────────────────────────

def test_publication_parity_all_70_statuses_match_persisted_state(three_cycles):
    lab = three_cycles["lab"]
    proj3 = three_cycles["proj3"]
    science = three_cycles["science"]

    # (a) All 70 question identities are represented in the Lab view.
    assert lab["canonical_question_count"] == CANONICAL_COUNT
    lab_rows = lab["canonical_questions"]
    assert len(lab_rows) == CANONICAL_COUNT
    proj_rows = proj3["canonical_questions"]
    assert len(proj_rows) == CANONICAL_COUNT
    lab_by_id = {r["question_id"]: r for r in lab_rows}
    proj_by_id = {r["question_id"]: r for r in proj_rows}
    assert set(lab_by_id) == set(proj_by_id)
    assert len(lab_by_id) == CANONICAL_COUNT

    # (b) Each Lab status matches the governed persisted scientific result.
    for qid, lrow in lab_by_id.items():
        presult = (proj_by_id[qid].get("result") or {})
        lresult = (lrow.get("result") or {})
        assert lresult.get("status") == presult.get("status"), qid
        assert lresult.get("result_id") == presult.get("result_id"), qid
        assert lresult.get("snapshot_id") == presult.get("snapshot_id"), qid

    # (c) Findings / hypotheses / candidates reflect actual persisted state.
    science_findings = science.get("findings") or {}
    science_hypotheses = (science.get("hypothesis_history")
                          or science.get("hypotheses") or {})
    assert len(lab["findings"]) == len(science_findings)
    assert len(lab["hypotheses"]) == len(science_hypotheses)
    # The Lab carries the projection's candidate list verbatim (no invention).
    assert lab["candidates"] == list(proj3.get("candidates") or [])

    # (d) Confidence, uncertainty and evidence limitations remain visible.
    for qid, lrow in lab_by_id.items():
        result = lrow.get("result") or {}
        assert "confidence" in result, qid
        assert "limitations" in result, qid
        assert "missing_evidence" in result, qid
        assert "evidence_datasets" in result, qid
        assert isinstance(result.get("limitations"), (list, tuple)), qid
        assert isinstance(result.get("missing_evidence"), (list, tuple)), qid

    # (e) Blocked and waiting states remain visible (never cleared to success).
    assert lab["blocked_count"] >= 1
    assert lab["waiting_count"] >= 1
    assert lab["questions_by_state"]["blocked"]
    assert lab["questions_by_state"]["waiting"]
    # Governed status mix is preserved end-to-end (no status reclassified).
    assert _status_counts(proj3) == three_cycles["c3"]["status_counts"]
