"""AR-01 + AR-03 targeted repair regressions for validation continuity.

AR-01 (critical): generated Q71 scientific candidates must reach the
authoritative validation queue through the exact same
``enqueue_validation_handoff`` boundary the canonical bridge uses, with
deterministic job identity, queue-boundary deduplication and fail-closed
conflict handling.

AR-03 (high): a retained (unchanged) research frontier must not stop pending
validation work, ``WAITING_FOR_DATA`` rechecks, interrupted-job recovery or
forward-validation handoff registration, while still preserving the retained
frontier optimisation (canonical question evaluation is NOT re-run).

Isolation contract
------------------
Every mutable authority in this file (queue, registry, projection, cycle
store, observation store, evidence store) lives under ``tmp_path``.  No
production registry, production evidence store, broker terminal or live
state is read or written.  The validation/forward executors here are
synthetic controlled fakes: they prove queue continuity and governance
boundaries, NOT that live prospective validation occurred.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import research_engine.v10.continuous.research_loop as research_loop_module
from research_engine.v10.continuous.research_loop import (
    _run_validation_queue_stage, run_continuous_research_cycle,
)
from research_engine.v10.continuous.cycle_state import ContinuousCycleStore
from research_engine.v10.continuous.question_cycle_state import QuestionCycleStore
from research_engine.v10.continuous.research_projection import ResearchProjectionStore
from research_engine.v10.continuous.validation_queue import (
    BLOCKED, COMPLETED, FORWARD_VALIDATION, QUEUED, RUNNING, VALIDATION,
    WAITING_FOR_DATA, ValidationQueueError, ValidationQueueStore,
    enqueue_validation_handoff, validation_job_id,
)
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry

from tests.test_continuous_research_block4 import (
    _fake_question, _handoff, _question_projection, _registry,
    _unexpected_question_call,
)

QUEUE_RELATIVE = "validation_queue.json"


# -- shared fixtures -----------------------------------------------------------

def _frontier(status="NO_NEW_GOVERNED_EVIDENCE", verification="NOT_REQUIRED"):
    return SimpleNamespace(
        status=status, verification_status=verification,
        snapshot_id="S1", fingerprint="FP", investigation_epoch="E1",
        frontier_id="FR1", frontier_start="2026-01-01",
        frontier_end="2026-01-02", predecessor_snapshot_id=None,
        changed_datasets=(), stale_datasets=(), missing_optional_datasets=())


def _canonical_bridge(handoff=()):
    return SimpleNamespace(
        bridge_run_id="BR1", status="NO_SCIENTIFIC_STATE_CHANGE",
        validation_handoff=tuple(handoff), question_changes_processed=(),
        findings_created=(), findings_weakened=(), hypotheses_created=(),
        hypotheses_invalidated=(), candidates_created=(), review_required=())


def _save_retained_projection(tmp_path) -> None:
    """A current successful projection whose frontier is snapshot ``S1``."""
    ResearchProjectionStore(tmp_path / "continuous" / "projection").save({
        "projection_schema": "unified_research_projection_v1",
        "projection_version": "RPROJ-EXISTING",
        "data_frontier": {"snapshot_id": "S1",
                          "last_successful_research_cycle": "CRCYCLE-EXISTING"},
        "canonical_questions": list(_question_projection()["questions"].values()),
    })


def _queue_path(tmp_path):
    return tmp_path / "continuous" / QUEUE_RELATIVE


def _load_queue(tmp_path) -> ValidationQueueStore:
    return ValidationQueueStore(_queue_path(tmp_path))


def _helper_kwargs(tmp_path, *, registry_dir, cycle_id="C1"):
    return dict(
        root=tmp_path / "continuous",
        registry_dir=registry_dir,
        frontier_snapshot_id="S1",
        cycle_id=cycle_id,
        timestamp="2026-01-02T00:00:00Z",
    )


def _fresh_registry(registry_dir):
    """Reload the registry from disk (the queue mutates a separate instance)."""
    registry = OptimisationRegistry(str(registry_dir))
    registry.load()
    return registry


def _full_cycle_kwargs(tmp_path, monkeypatch, *, handoff_source="none",
                       executable_question=True, max_validation_jobs=0):
    """Kwargs driving one real full cycle through the public entry point.

    ``handoff_source`` selects which bridge(s) surface the candidate's
    governed validation handoff: ``none``, ``canonical``, ``generated`` or
    ``both`` (the same identity from each bridge).
    """
    registry_dir = tmp_path / "registry"
    registry, candidate, plan = _registry(registry_dir)
    handoff = _handoff(plan) if handoff_source != "none" else ()
    canonical_handoff = handoff if handoff_source in ("canonical", "both") else ()
    generated_handoff = handoff if handoff_source in ("generated", "both") else ()
    qstate, question = _fake_question(tmp_path)
    log = {"questions": 0, "bridge": 0, "worker": 0, "generated_bridge": 0}

    def qrunner(_frontier_arg, **_kwargs):
        log["questions"] += 1
        return question

    def brunner(_question_arg, **_kwargs):
        log["bridge"] += 1
        return _canonical_bridge(canonical_handoff)

    def worker(**_kwargs):
        log["worker"] += 1
        return {
            "status": "COMPLETED",
            "batch_id": "BATCH-AR01" if executable_question else None,
            "outcomes": [], "result_ids": [],
        }

    def gen_bridge(batch_id, **_kwargs):
        log["generated_bridge"] += 1
        assert batch_id == "BATCH-AR01"
        return SimpleNamespace(
            bridge_run_id="GBR-AR01", status="COMPLETED",
            validation_handoff=tuple(generated_handoff))

    monkeypatch.setattr(research_loop_module, "run_generated_question_worker", worker)
    kwargs = dict(
        state_root=tmp_path / "continuous",
        frontier_runner=lambda **_: _frontier(
            "NEW_VERIFIED_IMMUTABLE_SNAPSHOT_READY", "VERIFIED"),
        question_runner=qrunner,
        bridge_runner=brunner,
        generated_bridge_runner=gen_bridge,
        q71_runner=lambda **_: {"status": "COMPLETED",
                                "generated_questions": [], "queue": []},
        q71_evaluator_registry=SimpleNamespace(),
        question_kwargs={"state_directory": qstate},
        bridge_kwargs={"scientific_state_directory": tmp_path / "science",
                       "optimisation_registry_directory": registry_dir},
        observation_space_manifest_directory=tmp_path / "manifests",
        observation_space_directory=tmp_path / "observation_space",
        production_coverage_directory=tmp_path / "production_coverage",
        production_coverage_store_path=tmp_path / "research_coverage.json",
        candle_authority_directory=tmp_path / "candle_authority",
        counterfactual_evidence_directory=tmp_path / "counterfactual_evidence",
        m5_candle_authority_producer=None,
        counterfactual_evidence_producer=None,
        max_validation_jobs=max_validation_jobs,
    )
    return kwargs, log, (registry, candidate, plan)



def _retained_cycle_kwargs(tmp_path, *, registry_dir, validation_executor=None,
                           forward_executor=None, max_validation_jobs=1,
                           question_runner=None):
    """Kwargs for a retained (unchanged) frontier tick.

    ``question_state_dir`` deliberately points at a non-existent directory so
    ``QuestionCycleStore.load_current()`` is ``None`` and the retained
    projection check takes the proven short-circuit path.
    """
    return dict(
        state_root=tmp_path / "continuous",
        frontier_runner=lambda **_: _frontier(),
        question_runner=question_runner or _unexpected_question_call,
        question_kwargs={"state_directory": tmp_path / "questions"},
        bridge_kwargs={"scientific_state_directory": tmp_path / "science",
                       "optimisation_registry_directory": registry_dir},
        counterfactual_evidence_directory=tmp_path / "counterfactual_evidence",
        validation_executor=validation_executor,
        forward_executor=forward_executor,
        max_validation_jobs=max_validation_jobs,
    )


def _waiting_output(snapshot_id="S1"):
    return {"status": WAITING_FOR_DATA, "reason": "MINIMUM_SAMPLE_UNMET",
            "snapshot_id": snapshot_id}


def _validated_output(candidate, snapshot_id="S1"):
    return {"status": "VALIDATED", "snapshot_id": snapshot_id,
            "candidate_id": candidate.candidate_id,
            "treatment_hash": candidate.treatment_hash}


def _forward_waiting_output(snapshot_id="S1"):
    return {"status": WAITING_FOR_DATA,
            "reason": "FORWARD_EVIDENCE_NOT_YET_AVAILABLE",
            "snapshot_id": snapshot_id, "source_snapshot_id": snapshot_id}



# -- AR-01: generated Q71 candidates enter the authoritative queue -------------

def test_generated_only_handoff_enqueues_in_full_cycle(tmp_path, monkeypatch):
    """A generated-Q71 handoff with no canonical handoff still enqueues."""
    kwargs, log, (registry, candidate, plan) = _full_cycle_kwargs(
        tmp_path, monkeypatch, handoff_source="generated")

    result = run_continuous_research_cycle(**kwargs)

    assert result.cycle_outcome == "COMPLETED", (result.failure_stage,
                                                 result.failure_reason)
    assert result.stage_statuses["Q71_EXECUTION"] == "COMPLETED"
    assert result.stage_statuses["VALIDATION_QUEUE"] == "COMPLETED"
    assert log["generated_bridge"] == 1 and log["worker"] == 1

    jobs = _load_queue(tmp_path).ordered()
    assert len(jobs) == 1
    job = jobs[0]
    # Immutable candidate / treatment / frozen-plan linkage is preserved.
    assert job.kind == VALIDATION
    assert job.candidate_id == candidate.candidate_id == "OPT-T1"
    assert job.policy_id == candidate.policy_id
    assert job.treatment_hash == candidate.treatment_hash
    assert job.validation_plan == plan.to_dict()
    assert job.validation_plan["baseline_id"] == candidate.baseline_id
    assert job.status == QUEUED
    assert job.job_id == validation_job_id(
        VALIDATION, candidate.candidate_id, candidate.policy_id,
        candidate.treatment_hash, plan.to_dict(), "S1")
    # Nothing beyond queue eligibility happened: no verdict, no approval.
    assert registry.get_candidate("OPT-T1").status == "PROPOSED"


def test_canonical_only_handoff_still_enqueues_in_full_cycle(tmp_path, monkeypatch):
    """The generated bridge running with an empty handoff changes nothing."""
    kwargs, log, (registry, candidate, plan) = _full_cycle_kwargs(
        tmp_path, monkeypatch, handoff_source="canonical")

    result = run_continuous_research_cycle(**kwargs)

    assert result.cycle_outcome == "COMPLETED", (result.failure_stage,
                                                 result.failure_reason)
    assert log["generated_bridge"] == 1  # generated bridge ran, handed nothing
    jobs = _load_queue(tmp_path).ordered()
    assert len(jobs) == 1
    assert jobs[0].candidate_id == "OPT-T1"
    assert jobs[0].status == QUEUED


def test_both_bridges_same_identity_enqueue_exactly_one_job(tmp_path, monkeypatch):
    """Canonical + generated handoffs for one identity dedupe at the boundary."""
    kwargs, _, (_, candidate, plan) = _full_cycle_kwargs(
        tmp_path, monkeypatch, handoff_source="both")

    result = run_continuous_research_cycle(**kwargs)

    assert result.cycle_outcome == "COMPLETED", (result.failure_stage,
                                                 result.failure_reason)
    jobs = _load_queue(tmp_path).ordered()
    assert len(jobs) == 1
    assert jobs[0].job_id == validation_job_id(
        VALIDATION, candidate.candidate_id, candidate.policy_id,
        candidate.treatment_hash, plan.to_dict(), "S1")


def test_absent_generated_handoff_creates_no_job(tmp_path, monkeypatch):
    """No executable generated question => generated bridge never runs, no job."""
    kwargs, log, _ = _full_cycle_kwargs(
        tmp_path, monkeypatch, executable_question=False)

    result = run_continuous_research_cycle(**kwargs)

    assert result.cycle_outcome == "COMPLETED", (result.failure_stage,
                                                 result.failure_reason)
    assert log["generated_bridge"] == 0
    assert result.stage_statuses["Q71_EXECUTION"] == \
        "NO_EXECUTABLE_GENERATED_QUESTION"
    assert _load_queue(tmp_path).ordered() == ()


def test_repeated_reconciliation_is_idempotent(tmp_path):
    """Re-enqueueing the same handoff across cycles never duplicates a job."""
    registry_dir = tmp_path / "registry"
    registry, candidate, plan = _registry(registry_dir)
    handoff = _handoff(plan)
    kwargs = _helper_kwargs(tmp_path, registry_dir=registry_dir)

    first = _run_validation_queue_stage(
        **kwargs, generated_handoff=handoff, max_validation_jobs=0)
    second = _run_validation_queue_stage(
        **kwargs, generated_handoff=handoff, max_validation_jobs=0)
    third = _run_validation_queue_stage(
        **kwargs, canonical_handoff=handoff, generated_handoff=handoff,
        max_validation_jobs=0)

    assert len(first[1]["transitions"]) == 0  # enqueue-only: no executor ran
    assert len(second[1]["transitions"]) == 0
    assert len(third[1]["transitions"]) == 0
    jobs = _load_queue(tmp_path).ordered()
    assert len(jobs) == 1
    assert jobs[0].status == QUEUED
    # Queue version is stable across repeated reconciliations.
    assert first[2] == second[2] == third[2]



def test_conflicting_treatment_identity_fails_closed_blocked(tmp_path):
    """A conflicting generated handoff is preserved as BLOCKED, never dropped."""
    registry_dir = tmp_path / "registry"
    registry, candidate, plan = _registry(registry_dir)
    conflicting = _handoff(plan, treatment_hash="b" * 64)

    _run_validation_queue_stage(
        **_helper_kwargs(tmp_path, registry_dir=registry_dir),
        generated_handoff=conflicting, max_validation_jobs=0)

    jobs = _load_queue(tmp_path).ordered()
    assert len(jobs) == 1
    assert jobs[0].status == BLOCKED
    assert jobs[0].failure_reason == "TREATMENT_IDENTITY_MISMATCH"
    # Fail-closed: the candidate keeps its upstream governed status.
    assert registry.get_candidate("OPT-T1").status == "PROPOSED"


@pytest.mark.parametrize("status", ["VALIDATED", "REJECTED", "INVALIDATED_UPSTREAM"])
def test_non_proposed_candidates_cannot_enter_validation(tmp_path, status):
    """Only governedly created PROPOSED candidates may enter initial validation."""
    registry_dir = tmp_path / "registry"
    registry, candidate, plan = _registry(registry_dir, status=status)

    _run_validation_queue_stage(
        **_helper_kwargs(tmp_path, registry_dir=registry_dir),
        generated_handoff=_handoff(plan), max_validation_jobs=0)

    jobs = _load_queue(tmp_path).ordered()
    # No VALIDATION job may be created for a non-PROPOSED candidate.  (A
    # VALIDATED candidate may legitimately receive a FORWARD job from the
    # forward scan below; that is not "entering validation".)
    assert not any(job.kind == VALIDATION for job in jobs)


def test_handoff_for_unknown_candidate_fails_closed(tmp_path):
    """A handoff whose candidate is not in the modern registry raises."""
    registry_dir = tmp_path / "registry"
    registry, candidate, plan = _registry(registry_dir)
    orphan = [dict(_handoff(plan)[0], candidate_id="OPT-NOT-CREATED")]

    with pytest.raises(ValidationQueueError,
                       match="HANDOFF_CANDIDATE_NOT_IN_MODERN_REGISTRY"):
        _run_validation_queue_stage(
            **_helper_kwargs(tmp_path, registry_dir=registry_dir),
            generated_handoff=orphan, max_validation_jobs=0)

    assert _load_queue(tmp_path).ordered() == ()


def test_queue_persistence_survives_restart(tmp_path):
    """Jobs and identities survive a fresh store instance (restart)."""
    registry_dir = tmp_path / "registry"
    registry, candidate, plan = _registry(registry_dir)

    _run_validation_queue_stage(
        **_helper_kwargs(tmp_path, registry_dir=registry_dir),
        generated_handoff=_handoff(plan), max_validation_jobs=0)

    restarted = _load_queue(tmp_path)  # fresh read model, as after a restart
    jobs = restarted.ordered()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.job_id == validation_job_id(
        VALIDATION, candidate.candidate_id, candidate.policy_id,
        candidate.treatment_hash, plan.to_dict(), "S1")
    assert job.validation_plan == plan.to_dict()

    # Reconciliation after the restart stays idempotent.
    _run_validation_queue_stage(
        **_helper_kwargs(tmp_path, registry_dir=registry_dir),
        generated_handoff=_handoff(plan), max_validation_jobs=0)
    assert len(_load_queue(tmp_path).ordered()) == 1



# -- AR-03: retained frontier continues validation progress --------------------

def _seed_queued_job(tmp_path, registry_dir, plan):
    """Create a QUEUED initial-validation job for OPT-T1 under the temp root."""
    registry = OptimisationRegistry(str(registry_dir))
    registry.load()
    store = _load_queue(tmp_path)
    created = enqueue_validation_handoff(
        _handoff(plan), registry=registry, store=store,
        snapshot_id="S1", cycle_id="QCYCLE-SEED")
    assert len(created) == 1
    assert created[0].status == QUEUED
    return created[0].job_id


def test_retained_frontier_processes_queued_job_without_questions(tmp_path):
    """An unchanged frontier still executes an existing QUEUED job."""
    registry_dir = tmp_path / "registry"
    registry, candidate, plan = _registry(registry_dir)
    _save_retained_projection(tmp_path)
    _seed_queued_job(tmp_path, registry_dir, plan)

    def executor(cand, _plan, _job):
        assert cand.candidate_id == "OPT-T1"
        return _validated_output(cand)

    result = run_continuous_research_cycle(**_retained_cycle_kwargs(
        tmp_path, registry_dir=registry_dir, validation_executor=executor,
        max_validation_jobs=1))

    assert result.cycle_outcome == "NO_NEW_RESEARCH_EVIDENCE"
    assert result.stage_statuses["VALIDATION_QUEUE"] == \
        "COMPLETED_RETAINED_FRONTIER"
    # Research stages were NOT re-run: the retained optimisation is preserved.
    assert result.stage_statuses["QUESTIONS"] == "SKIPPED_NO_NEW_EVIDENCE"
    assert result.stage_statuses["SCIENTIFIC_STATE"] == "SKIPPED_NO_NEW_EVIDENCE"
    assert result.projection_version == "RPROJ-EXISTING"

    jobs = _load_queue(tmp_path).ordered()
    validation_jobs = [j for j in jobs if j.kind == VALIDATION]
    assert len(validation_jobs) == 1
    assert validation_jobs[0].status == COMPLETED
    assert validation_jobs[0].output_validation_record["status"] == "VALIDATED"
    # Candidate advanced to VALIDATED on disk (reload the authoritative copy).
    assert _fresh_registry(registry_dir).get_candidate("OPT-T1").status == "VALIDATED"
    # A single forward-validation handoff now exists for the validated candidate.
    assert sum(1 for j in jobs if j.kind == FORWARD_VALIDATION) == 1


def test_retained_frontier_rechecks_waiting_when_evidence_changes(tmp_path):
    """WAITING_FOR_DATA re-runs each tick; a genuine verdict lands when ready."""
    registry_dir = tmp_path / "registry"
    registry, candidate, plan = _registry(registry_dir)
    _save_retained_projection(tmp_path)
    _seed_queued_job(tmp_path, registry_dir, plan)

    evidence = {"qualified": False}
    calls = {"n": 0}

    def executor(cand, _plan, _job):
        calls["n"] += 1
        if not evidence["qualified"]:
            return _waiting_output()
        return _validated_output(cand)

    # Tick 1: evidence insufficient -> stays WAITING, candidate untouched.
    first = run_continuous_research_cycle(**_retained_cycle_kwargs(
        tmp_path, registry_dir=registry_dir, validation_executor=executor,
        max_validation_jobs=1))
    assert first.cycle_outcome == "NO_NEW_RESEARCH_EVIDENCE"
    job = _load_queue(tmp_path).ordered()[0]
    assert job.status == WAITING_FOR_DATA
    assert registry.get_candidate("OPT-T1").status == "PROPOSED"

    # Tick 2 (unchanged frontier): recheck against the same store, still no
    # qualified evidence -> remains WAITING; no spurious verdict.
    second = run_continuous_research_cycle(**_retained_cycle_kwargs(
        tmp_path, registry_dir=registry_dir, validation_executor=executor,
        max_validation_jobs=1))
    assert second.cycle_outcome == "NO_NEW_RESEARCH_EVIDENCE"
    jobs = _load_queue(tmp_path).ordered()
    assert len(jobs) == 1
    assert jobs[0].status == WAITING_FOR_DATA
    assert registry.get_candidate("OPT-T1").status == "PROPOSED"
    # The recheck actually re-invoked the executor on tick 2.
    assert calls["n"] == 2

    # Qualified evidence arrives WITHOUT a frontier change -> now it validates.
    evidence["qualified"] = True
    third = run_continuous_research_cycle(**_retained_cycle_kwargs(
        tmp_path, registry_dir=registry_dir, validation_executor=executor,
        max_validation_jobs=1))
    assert third.cycle_outcome == "NO_NEW_RESEARCH_EVIDENCE"
    jobs = _load_queue(tmp_path).ordered()
    validation_jobs = [j for j in jobs if j.kind == VALIDATION]
    assert len(validation_jobs) == 1
    assert validation_jobs[0].status == COMPLETED
    assert _fresh_registry(registry_dir).get_candidate("OPT-T1").status == "VALIDATED"
    # The three ticks manufactured no duplicate validation job.  A single
    # forward-validation job now appears because the candidate reached
    # VALIDATED (forward work is exercised in its own test below).
    assert len(jobs) <= 2
    assert sum(1 for j in jobs if j.kind == FORWARD_VALIDATION) <= 1



def test_retained_frontier_recovers_interrupted_running_job(tmp_path):
    """A RUNNING job interrupted mid-attempt is recovered and re-executed."""
    registry_dir = tmp_path / "registry"
    registry, candidate, plan = _registry(registry_dir)
    _save_retained_projection(tmp_path)
    job_id = _seed_queued_job(tmp_path, registry_dir, plan)
    # Simulate a crash: the job was RUNNING when the process died.
    store = _load_queue(tmp_path)
    store.jobs[job_id].status = RUNNING
    store.save()

    def executor(cand, _plan, _job):
        return _validated_output(cand)

    result = run_continuous_research_cycle(**_retained_cycle_kwargs(
        tmp_path, registry_dir=registry_dir, validation_executor=executor,
        max_validation_jobs=1))

    assert result.cycle_outcome == "NO_NEW_RESEARCH_EVIDENCE"
    job = _load_queue(tmp_path).jobs[job_id]
    assert job.status == COMPLETED
    # The interrupted attempt was recovered (marker persisted by the queue
    # reload) and then driven to a real completion.
    assert job.failure_reason == "RECOVERED_INTERRUPTED_ATTEMPT"
    assert _fresh_registry(registry_dir).get_candidate("OPT-T1").status == "VALIDATED"


def test_retained_frontier_registers_forward_handoff_exactly_once(tmp_path):
    """A VALIDATED candidate's forward handoff is persisted once and stays."""
    registry_dir = tmp_path / "registry"
    registry, candidate, plan = _registry(registry_dir, status="VALIDATED")
    _save_retained_projection(tmp_path)

    forward_calls = {"n": 0}

    def forward_executor(cand, _plan, _job):
        forward_calls["n"] += 1
        return _forward_waiting_output()

    for _ in range(3):
        result = run_continuous_research_cycle(**_retained_cycle_kwargs(
            tmp_path, registry_dir=registry_dir,
            forward_executor=forward_executor, max_validation_jobs=1))
        assert result.cycle_outcome == "NO_NEW_RESEARCH_EVIDENCE"

    jobs = _load_queue(tmp_path).ordered()
    forward_jobs = [j for j in jobs if j.kind == FORWARD_VALIDATION]
    assert len(forward_jobs) == 1  # exactly once, across three retained ticks
    assert forward_jobs[0].candidate_id == "OPT-T1"
    assert forward_jobs[0].validation_plan == plan.to_dict()
    # Forward work progressed on retained ticks (the executor was invoked on
    # every tick).  Exact counts vary because the queue processes forward jobs
    # in both the initial and follow-up passes — existing behaviour.
    assert forward_calls["n"] >= 3
    # Idempotent: repeated invocations against unchanged evidence did not
    # manufacture attempts or a spurious forward verdict.
    assert forward_jobs[0].attempts == 1
    assert forward_jobs[0].status == WAITING_FOR_DATA
    assert _fresh_registry(registry_dir).get_candidate("OPT-T1").status == "VALIDATED"


def test_retained_frontier_forward_job_not_duplicated_after_restart(tmp_path):
    """Restart (fresh stores) preserves forward identity without duplication."""
    registry_dir = tmp_path / "registry"
    registry, candidate, plan = _registry(registry_dir, status="VALIDATED")
    _save_retained_projection(tmp_path)

    def forward_executor(cand, _plan, _job):
        return _forward_waiting_output()

    run_continuous_research_cycle(**_retained_cycle_kwargs(
        tmp_path, registry_dir=registry_dir, forward_executor=forward_executor,
        max_validation_jobs=1))
    first_ids = [j.job_id for j in _load_queue(tmp_path).ordered()]

    # "Restart": every store is reloaded from disk on the next tick.
    run_continuous_research_cycle(**_retained_cycle_kwargs(
        tmp_path, registry_dir=registry_dir, forward_executor=forward_executor,
        max_validation_jobs=1))
    second_ids = [j.job_id for j in _load_queue(tmp_path).ordered()]

    assert first_ids == second_ids
    assert len(second_ids) == 1


def test_retained_frontier_validation_failure_is_governed(tmp_path):
    """Executor failure surfaces as a governed BLOCKED outcome, not success."""
    registry_dir = tmp_path / "registry"
    registry, candidate, plan = _registry(registry_dir)
    _save_retained_projection(tmp_path)
    _seed_queued_job(tmp_path, registry_dir, plan)

    def executor(cand, _plan, _job):
        raise RuntimeError("evidence authority unavailable")

    result = run_continuous_research_cycle(**_retained_cycle_kwargs(
        tmp_path, registry_dir=registry_dir, validation_executor=executor,
        max_validation_jobs=1))

    # The cycle itself completed; the job failed closed as BLOCKED with an
    # explicit reason.  The candidate is NOT advanced.
    assert result.cycle_outcome == "NO_NEW_RESEARCH_EVIDENCE"
    job = _load_queue(tmp_path).ordered()[0]
    assert job.status == BLOCKED
    assert "evidence authority unavailable" in (job.failure_reason or "")
    assert registry.get_candidate("OPT-T1").status == "PROPOSED"



def test_cadence_tick_retained_frontier_still_processes_validation(tmp_path, monkeypatch):
    """The real cadence entry point reaches validation on a retained tick.

    This drives ``run_cadence_tick`` (the scheduler entry point) with isolated
    injected runners so no production frontier, registry or evidence store is
    touched, and asserts that a retained frontier still progresses a QUEUED
    job.  Against the original defect this tick returned before the queue was
    ever opened, so the job stayed QUEUED and the test would fail.
    """
    import research_engine.v10.continuous.production_validation as prod_val
    from research_engine.v10.continuous.research_cadence import (
        ResearchCadenceConfig, run_cadence_tick,
    )

    registry_dir = tmp_path / "registry"
    registry, candidate, plan = _registry(registry_dir)
    _save_retained_projection(tmp_path)
    _seed_queued_job(tmp_path, registry_dir, plan)

    def executor(cand, _plan, _job):
        return _validated_output(cand)

    real_cycle = research_loop_module.run_continuous_research_cycle

    def isolated_cycle(**kwargs):
        # Inject controlled, isolated runners/executors over the defaults the
        # cadence caller omitted, then run the REAL cycle unchanged.
        kwargs.update(
            frontier_runner=lambda **_: _frontier(),
            question_runner=_unexpected_question_call,
            question_kwargs={"state_directory": tmp_path / "questions"},
            bridge_kwargs={"scientific_state_directory": tmp_path / "science",
                           "optimisation_registry_directory": registry_dir},
            counterfactual_evidence_directory=tmp_path / "counterfactual_evidence",
            validation_executor=executor,
            forward_executor=None,
            max_validation_jobs=1,
        )
        return real_cycle(**kwargs)

    monkeypatch.setattr(research_loop_module, "run_continuous_research_cycle",
                        isolated_cycle)
    # Keep the cadence's own production registry/evidence reads off-path.
    monkeypatch.setattr(research_loop_module, "_production_evaluator_registry",
                        lambda: None)
    monkeypatch.setattr(prod_val, "production_executors",
                        lambda *, state_root, **_: (None, None))

    report = run_cadence_tick(
        state_root=tmp_path / "continuous",
        config=ResearchCadenceConfig(fast_cycle_min_interval_seconds=0),
        force_fast_cycle=True)

    assert report.fast_cycle_outcome == "NO_NEW_RESEARCH_EVIDENCE", \
        (report.fast_cycle_outcome, report.skipped_reason)
    job = _load_queue(tmp_path).ordered()[0]
    assert job.status == COMPLETED
    assert _fresh_registry(registry_dir).get_candidate("OPT-T1").status == "VALIDATED"
