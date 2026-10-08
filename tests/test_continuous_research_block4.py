from __future__ import annotations

from types import SimpleNamespace
import hashlib
import json

import pytest

from research_engine.v10.continuous.cycle_state import (
    ContinuousCycleLease, ContinuousCycleStateError, ContinuousCycleStore,
    ContinuousResearchCycleResult,
)
from research_engine.v10.continuous.question_cycle_state import QuestionCycleStore
from research_engine.v10.continuous.research_loop import run_continuous_research_cycle
from research_engine.v10.continuous.evaluation_identity import evaluation_identities
from research_engine.v10.continuous.canonical_question_cycle import (
    REGISTRY_VERSION, _load_registry,
)
from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.v10.continuous.q71_orchestration import run_q71_orchestration
from research_engine.v10.continuous.research_projection import (
    ResearchProjectionStore, build_unified_research_projection,
)
from research_engine.v10.continuous.scientific_state_store import ScientificStateStore
from research_engine.v10.continuous.validation_queue import (
    BLOCKED, COMPLETED, REVIEW_REQUIRED, ValidationQueueStore,
    enqueue_forward_validation, enqueue_validation_handoff, process_validation_queue,
)
from research_engine.v10.optimisation.models import (
    OptimisationCandidate, ResearchHypothesis, ValidationPlan,
)
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry
from research_engine.lifecycle.generated_research_identity import (
    GeneratedResearchKind, GeneratedResearchProposal,
)
from research_engine.lifecycle.generated_research_store import GeneratedResearchStore
from research_engine.lifecycle.research_agenda_store import ResearchAgendaStore
from research_engine.lifecycle.research_coverage_store import ResearchCoverageStore


def _registry(path, *, status="PROPOSED", complete=True):
    registry = OptimisationRegistry(str(path))
    registry.add_hypothesis(ResearchHypothesis(
        hypothesis_id="HYP-T1", source_finding="F-T1", source_question="Q001",
        domain="EXIT", statement="governed statement", target_component="Exit",
        expected_effect="positive", confidence="MEDIUM", status="PROPOSED",
        created_at="2026-01-01T00:00:00Z"))
    policy = {"policy_id": "POLICY-1", "parameter": "frozen"}
    treatment_hash = hashlib.sha256(json.dumps(
        policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    candidate = OptimisationCandidate(
        candidate_id="OPT-T1", hypothesis_id="HYP-T1", baseline_id="BASE",
        created_at="2026-01-01T00:00:00Z", component="Exit",
        changes={"policy_id": "POLICY-1", "treatment": "frozen",
                 "frozen_policy": policy},
        expected_outcome="positive", status=status, policy_id="POLICY-1",
        treatment_hash=treatment_hash, source_finding_versions=["F-T1:v1"],
        source_question_results=["QR-1"],
        target_population={"scope": "all"} if complete else {},
        provenance={"snapshot_id": "S1"})
    registry.add_candidate(candidate)
    plan = ValidationPlan(
        candidate_id="OPT-T1", baseline_id="BASE", created_at="2026-01-01T00:00:00Z",
        metrics=["expectancy"], minimum_sample=100,
        success_conditions={"scope_fidelity": "only treatment differs", "expectancy": ">0"},
        failure_conditions={"expectancy": "<=0"}, notes="treatment fidelity required")
    registry.add_plan(plan)
    registry.save()
    return registry, candidate, plan


def _handoff(plan, *, treatment_hash=None):
    if treatment_hash is None:
        policy = {"policy_id": "POLICY-1", "parameter": "frozen"}
        treatment_hash = hashlib.sha256(json.dumps(
            policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return [{"candidate_id": "OPT-T1", "status": "PROPOSED", "policy_id": "POLICY-1",
             "treatment_hash": treatment_hash, "validation_plan": plan.to_dict(),
             "source_finding_version": "F-T1:v1", "source_hypothesis_id": "HYP-T1"}]


def test_validation_queue_dedup_review_block_and_existing_lifecycle(tmp_path):
    registry, candidate, plan = _registry(tmp_path / "registry")
    store = ValidationQueueStore(tmp_path / "queue.json")
    assert len(enqueue_validation_handoff(_handoff(plan), registry=registry, store=store,
                                          snapshot_id="S1", cycle_id="C1")) == 1
    assert len(enqueue_validation_handoff(_handoff(plan), registry=registry, store=store,
                                          snapshot_id="S1", cycle_id="C1")) == 0
    output = process_validation_queue(
        store=store, registry=registry, max_jobs=1,
        validation_executor=lambda *_: {"status": "VALIDATED", "record_id": "VAL-1",
                                        "snapshot_id": "S1", "candidate_id": "OPT-T1"})
    assert store.ordered()[0].status == COMPLETED
    assert registry.get_candidate("OPT-T1").status == "VALIDATED"
    assert output["transitions"][0]["candidate_status"] == "VALIDATED"

    forward = enqueue_forward_validation(candidate, plan.to_dict(), store=store,
                                         snapshot_id="S1", cycle_id="C1",
                                         timestamp="2026-01-01T00:00:00Z")
    process_validation_queue(
        store=store, registry=registry, max_jobs=1,
        forward_executor=lambda *_: {"status": "FORWARD_VALIDATED", "record_id": "FVAL-1",
                                     "snapshot_id": "S1", "candidate_id": "OPT-T1"})
    assert forward.status == COMPLETED
    assert registry.get_candidate("OPT-T1").status == "FORWARD_VALIDATED"
    eligibility = process_validation_queue(store=store, registry=registry, max_jobs=0)
    assert eligibility["shadow_eligibility"][0]["live_approved"] is False

    incomplete, _, incomplete_plan = _registry(tmp_path / "incomplete", complete=False)
    incomplete_store = ValidationQueueStore(tmp_path / "incomplete-queue.json")
    enqueue_validation_handoff(_handoff(incomplete_plan), registry=incomplete,
                               store=incomplete_store, snapshot_id="S1", cycle_id="C1")
    assert incomplete_store.ordered()[0].status == REVIEW_REQUIRED

    mismatch, _, mismatch_plan = _registry(tmp_path / "mismatch")
    mismatch_store = ValidationQueueStore(tmp_path / "mismatch-queue.json")
    enqueue_validation_handoff(_handoff(mismatch_plan, treatment_hash="b" * 64),
                               registry=mismatch, store=mismatch_store,
                               snapshot_id="S1", cycle_id="C1")
    assert mismatch_store.ordered()[0].status == BLOCKED

    wrong_snapshot, _, wrong_plan = _registry(tmp_path / "wrong-snapshot")
    wrong_store = ValidationQueueStore(tmp_path / "wrong-snapshot-queue.json")
    enqueue_validation_handoff(_handoff(wrong_plan), registry=wrong_snapshot,
                               store=wrong_store, snapshot_id="S1", cycle_id="C1")
    process_validation_queue(
        store=wrong_store, registry=wrong_snapshot, max_jobs=1,
        validation_executor=lambda *_: {"status": "VALIDATED", "snapshot_id": "CURRENT"})
    assert wrong_store.ordered()[0].status == "FAILED"
    assert wrong_snapshot.get_candidate("OPT-T1").status == "PROPOSED"

    already, _, already_plan = _registry(tmp_path / "already", status="VALIDATED")
    already_store = ValidationQueueStore(tmp_path / "already-queue.json")
    assert not enqueue_validation_handoff(
        _handoff(already_plan), registry=already, store=already_store,
        snapshot_id="S1", cycle_id="C1")
    assert not already_store.ordered()


def _question_projection():
    questions = _load_registry()
    identities = evaluation_identities(
        questions, build_definitions_from_registry(questions),
        registry_version=REGISTRY_VERSION)
    return {"questions": {
        question_id: {"question_id": question_id, "question_text": f"Question {i}",
                      "status": "WAITING_FOR_DATA" if i == 6 else "COMPLETE",
                      "sample_n": i, "substantive_answer": None,
                      "last_evaluated_snapshot": "S1",
                      "result": {"question_id": question_id,
                                 "evaluation_identity": identities[question_id]}}
        for i, question_id in enumerate(BASELINE_QUESTION_IDS, start=1)
    }}


def _unexpected_question_call(*args, **kwargs):
    raise AssertionError(
        f"questions unexpectedly called with {len(args)} positional and {len(kwargs)} keyword arguments")


def _evaluator_refresh_fixture(tmp_path, *, changed_question_ids=()):
    questions = _load_registry()
    identities = evaluation_identities(
        questions, build_definitions_from_registry(questions),
        registry_version=REGISTRY_VERSION)
    rows = []
    qrows = {}
    for question in questions:
        identity = identities[question.id]
        recorded = None if question.id == "L3" else identity
        result = {
            "question_id": question.id, "snapshot_id": "S1", "status": "COMPLETE",
            "evaluation_identity": recorded,
            "evaluation_identity_digest": (
                None if recorded is None else recorded["evaluation_identity_digest"]),
        }
        row = {"question_id": question.id, "result": result}
        rows.append(row)
        qrows[question.id] = row
    state_root = tmp_path / "continuous"
    projection_store = ResearchProjectionStore(state_root / "projection")
    projection_store.save({
        "projection_schema": "unified_research_projection_v1",
        "projection_version": "RPROJ-STALE",
        "continuous_cycle_id": "CRCYCLE-STALE",
        "data_frontier": {"snapshot_id": "S1", "fingerprint": "FP",
                          "last_successful_research_cycle": "CRCYCLE-STALE"},
        "canonical_questions": rows,
        "generated_questions": [], "findings": [], "hypotheses": [], "candidates": [],
        "investigations_and_work_queues": {}, "what_changed": {},
        "predecessor_projection_version": None,
    })
    qstate = tmp_path / "questions"
    QuestionCycleStore(qstate).save_projection({
        "cycle_schema": "canonical_question_cycle_v1", "cycle_id": "QCYCLE-REFRESH",
        "questions": qrows,
    })
    question_result = SimpleNamespace(
        cycle_id="QCYCLE-REFRESH", total_questions=70, cycle_status="COMPLETED",
        completed_at="2026-01-02T00:00:00Z", evaluated_count=1,
        retained_count=69, changed_question_ids=tuple(changed_question_ids))
    frontier = SimpleNamespace(
        status="NO_NEW_GOVERNED_EVIDENCE", verification_status="NOT_REQUIRED",
        snapshot_id="S1", fingerprint="FP", investigation_epoch="E1", frontier_id="FR1",
        frontier_start="2026-01-01", frontier_end="2026-01-02",
        predecessor_snapshot_id="S1", changed_datasets=(), stale_datasets=(),
        missing_optional_datasets=())
    return state_root, qstate, frontier, question_result


def test_projection_is_deterministic_all_70_and_opt_dp_visible(tmp_path):
    registry = OptimisationRegistry("data/research/optimisation")
    registry.load()
    store = ValidationQueueStore(tmp_path / "queue.json")
    science = ScientificStateStore(tmp_path / "science")
    frontier = SimpleNamespace(snapshot_id="S1", fingerprint="FP", investigation_epoch="E1",
                               frontier_start="2026-01-01", frontier_end="2026-01-02",
                               predecessor_snapshot_id=None, changed_datasets=("trade_truth",),
                               stale_datasets=(), missing_optional_datasets=(), status="SNAPSHOT_READY")
    bridge = SimpleNamespace(question_changes_processed=("Q001",), findings_created=(),
                             findings_weakened=(), hypotheses_created=(),
                             hypotheses_invalidated=(), candidates_created=(), review_required=())
    kwargs = dict(continuous_cycle_id="CRCYCLE-X", frontier=frontier,
                  question_projection=_question_projection(), bridge=bridge,
                  scientific_store=science, optimisation_registry=registry,
                  validation_store=store, q71={"generated_questions": [], "queue": []},
                  shadow_evidence={"OPT-DP1-002": {"paired_n": 12}})
    one = build_unified_research_projection(**kwargs)
    two = build_unified_research_projection(**kwargs)
    assert one == two
    assert len(one["canonical_questions"]) == 70
    assert any(row["status"] == "WAITING_FOR_DATA" for row in one["canonical_questions"])
    dp = next(row for row in one["candidates"] if row["candidate_id"] == "OPT-DP1-002")
    assert dp["status"] == "SHADOW_VALIDATION_ACTIVE"
    assert dp["live_approved"] is False
    assert dp["shadow_evidence"]["paired_n"] == 12
    assert dp["plan"] == registry.get_plan("OPT-DP1-002").to_dict()


def _fake_question(tmp_path):
    state = tmp_path / "questions"
    QuestionCycleStore(state).save_projection({
        "cycle_schema": "canonical_question_cycle_v1", "cycle_id": "QCYCLE-X",
        **_question_projection(),
    })
    return state, SimpleNamespace(
        cycle_id="QCYCLE-X", total_questions=70, cycle_status="COMPLETED",
        completed_at="2026-01-02T00:00:00Z")


def test_unchanged_frontier_without_projection_bootstraps_downstream(tmp_path):
    qstate, question = _fake_question(tmp_path)
    registry_dir = tmp_path / "registry"
    OptimisationRegistry(str(registry_dir)).save()
    calls = []
    frontier = SimpleNamespace(
        status="NO_NEW_GOVERNED_EVIDENCE", verification_status="NOT_REQUIRED",
        snapshot_id="S1", fingerprint="FP", investigation_epoch="E1", frontier_id="FR1",
        frontier_start="2026-01-01", frontier_end="2026-01-02",
        predecessor_snapshot_id=None, changed_datasets=(), stale_datasets=(),
        missing_optional_datasets=())
    bridge = SimpleNamespace(
        bridge_run_id="BR1", status="NO_SCIENTIFIC_STATE_CHANGE",
        validation_handoff=(), question_changes_processed=(), findings_created=(),
        findings_weakened=(), hypotheses_created=(), hypotheses_invalidated=(),
        candidates_created=(), review_required=())
    state_root = tmp_path / "continuous"
    failed_frontier = SimpleNamespace(**{
        **frontier.__dict__, "status": "FRONTIER_INVALID",
        "verification_status": "FAILED",
        "failure_reason": "temporary frontier verification failure",
    })
    failed = run_continuous_research_cycle(
        state_root=state_root, frontier_runner=lambda **_: failed_frontier,
        question_runner=_unexpected_question_call)
    failed_path = (
        state_root / "cycles" / "history" / f"{failed.continuous_cycle_id}.json")
    failed_history = failed_path.read_bytes()
    assert failed.cycle_outcome == "FAILED"
    assert failed.evidence_identity == "S1"

    result = run_continuous_research_cycle(
        state_root=state_root, frontier_runner=lambda **_: frontier,
        question_runner=lambda value, **_: calls.append(("questions", value.snapshot_id)) or question,
        bridge_runner=lambda value, **_: calls.append(("bridge", value.cycle_id)) or bridge,
        q71_runner=lambda **_: {"status": "COMPLETED", "generated_questions": [], "queue": []},
        question_kwargs={"state_directory": qstate},
        bridge_kwargs={"scientific_state_directory": tmp_path / "science",
                       "optimisation_registry_directory": registry_dir},
        max_validation_jobs=0)

    assert result.cycle_outcome == "COMPLETED", (result.failure_stage, result.failure_reason)
    assert result.continuous_cycle_id != failed.continuous_cycle_id
    assert result.cycle_attempt_id != failed.cycle_attempt_id
    assert result.evidence_identity == failed.evidence_identity == "S1"
    assert failed_path.read_bytes() == failed_history
    assert calls == [("questions", "S1"), ("bridge", "QCYCLE-X")]
    cycle_store = ContinuousCycleStore(state_root / "cycles")
    assert cycle_store.load_latest_success() == result
    assert cycle_store.load_latest_attempt() == result
    projection = ResearchProjectionStore(tmp_path / "continuous" / "projection").load_latest()
    assert projection is not None
    assert projection["data_frontier"]["snapshot_id"] == "S1"
    assert projection["data_frontier"]["status"] == "NO_NEW_GOVERNED_EVIDENCE"
    assert projection["data_frontier"]["changed_datasets"] == []
    assert len(projection["canonical_questions"]) == 70
    progress = json.loads(
        (tmp_path / "continuous" / "cycle_progress.json").read_text(encoding="utf-8"))
    assert progress["status"] == "COMPLETED"
    assert set(progress["stages"]) == {
        "FRONTIER", "QUESTIONS", "SCIENTIFIC_STATE", "Q71_PLUS",
        "VALIDATION_QUEUE", "PROJECTION",
    }
    assert all(item["finished_at"] and item["elapsed_seconds"] is not None
               for item in progress["stages"].values())


def test_unchanged_frontier_with_existing_projection_is_noop(tmp_path):
    projection_store = ResearchProjectionStore(tmp_path / "continuous" / "projection")
    projection_store.save({
        "projection_schema": "unified_research_projection_v1",
        "projection_version": "RPROJ-EXISTING",
        "data_frontier": {
            "snapshot_id": "S1",
            "last_successful_research_cycle": "CRCYCLE-EXISTING",
        },
        "canonical_questions": list(_question_projection()["questions"].values()),
    })
    frontier = SimpleNamespace(
        status="NO_NEW_GOVERNED_EVIDENCE", verification_status="NOT_REQUIRED",
        snapshot_id="S1", fingerprint="FP", investigation_epoch="E1", frontier_id="FR1",
        frontier_start="2026-01-01", frontier_end="2026-01-02",
        predecessor_snapshot_id=None, changed_datasets=(), stale_datasets=(),
        missing_optional_datasets=())

    result = run_continuous_research_cycle(
        state_root=tmp_path / "continuous", frontier_runner=lambda **_: frontier,
        question_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("questions must not run")),
        question_kwargs={"state_directory": tmp_path / "questions"})

    assert result.cycle_outcome == "NO_NEW_RESEARCH_EVIDENCE"
    assert result.projection_version == "RPROJ-EXISTING"
    assert result.stage_statuses["QUESTIONS"] == "SKIPPED_NO_NEW_EVIDENCE"
    assert result.stage_statuses["PROJECTION"] == "RETAINED"
    store = ContinuousCycleStore(tmp_path / "continuous" / "cycles")
    assert store.load_latest_success() == result
    assert store.load_latest_attempt() == result

    restarted = run_continuous_research_cycle(
        state_root=tmp_path / "continuous", frontier_runner=lambda **_: frontier,
        question_runner=_unexpected_question_call,
        question_kwargs={"state_directory": tmp_path / "questions"})
    assert restarted.cycle_outcome == "NO_NEW_RESEARCH_EVIDENCE"
    assert restarted.continuous_cycle_id != result.continuous_cycle_id
    assert restarted.cycle_attempt_id != result.cycle_attempt_id
    assert restarted.evidence_identity == result.evidence_identity == "S1"
    assert store.load_latest_success() == restarted
    assert store.load_latest_attempt() == restarted


def test_active_cycle_lease_prevents_overlap_before_frontier_or_publication(tmp_path):
    state_root = tmp_path / "continuous"
    projection_store = ResearchProjectionStore(state_root / "projection")
    predecessor = {
        "projection_schema": "unified_research_projection_v1",
        "projection_version": "RPROJ-PREDECESSOR",
        "data_frontier": {"snapshot_id": "S0"},
        "canonical_questions": [],
    }
    projection_store.save(predecessor)
    called = []

    with ContinuousCycleLease(
            state_root / "active_cycle.lock", lease_id="FIRST-CYCLE"):
        with pytest.raises(
                ContinuousCycleStateError,
                match="CONTINUOUS_RESEARCH_CYCLE_ALREADY_ACTIVE"):
            run_continuous_research_cycle(
                state_root=state_root,
                frontier_runner=lambda **_: called.append("frontier"))

    assert called == []
    assert projection_store.load_latest() == predecessor
    assert not (state_root / "cycle_progress.json").exists()


def test_equivalent_evaluator_refresh_skips_scientific_downstream_and_publishes(tmp_path):
    state_root, qstate, frontier, question = _evaluator_refresh_fixture(tmp_path)
    calls = []

    def qrunner(_frontier, **kwargs):
        calls.append(("questions", kwargs.get("evaluation_only_question_ids")))
        return question

    result = run_continuous_research_cycle(
        state_root=state_root, frontier_runner=lambda **_: frontier,
        question_runner=qrunner,
        bridge_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("scientific bridge must not run")),
        q71_runner=lambda **_: (_ for _ in ()).throw(
            AssertionError("Q71 must not run")),
        question_kwargs={"state_directory": qstate})

    assert calls == [("questions", ("L3",))]
    assert result.cycle_outcome == "COMPLETED"
    assert result.stage_statuses["SCIENTIFIC_STATE"] == \
        "SKIPPED_NO_MATERIAL_QUESTION_CHANGE"
    assert result.stage_statuses["Q71_PLUS"] == "SKIPPED_NO_MATERIAL_QUESTION_CHANGE"
    assert result.stage_statuses["VALIDATION_QUEUE"] == \
        "SKIPPED_NO_MATERIAL_QUESTION_CHANGE"
    assert result.stage_statuses["PROJECTION"] == "COMPLETED_EVALUATION_REFRESH"
    projection = ResearchProjectionStore(state_root / "projection").load_latest()
    assert projection["projection_version"] != "RPROJ-STALE"
    assert projection["data_frontier"]["snapshot_id"] == "S1"
    assert projection["what_changed"]["evaluation_refresh"] == ["L3"]


def test_incomplete_candidate_allows_evaluator_refresh_on_retained_snapshot(tmp_path):
    state_root, qstate, frontier, question = _evaluator_refresh_fixture(tmp_path)
    frontier = SimpleNamespace(**{
        **frontier.__dict__,
        "status": "FRONTIER_INCOMPLETE",
        "verification_status": "RETAINED_VERIFIED",
        "failure_reason": (
            "REQUIRED_DATASETS_NOT_YET_COHERENT_AT_NEWEST_COVERAGE:"
            '{"execution_context":["new-context"],'
            '"shadow_runtime":["new-shadow"]}'),
        "candidate_status": "CANDIDATE_INCOMPLETE",
        "candidate_frontier_end": "2026-01-03",
        "pending_missing_datasets": (
            "decision_trace", "execution_attempts", "execution_results", "trade_truth"),
    })
    calls = []

    result = run_continuous_research_cycle(
        state_root=state_root, frontier_runner=lambda **_: frontier,
        question_runner=lambda _frontier, **kwargs: (
            calls.append(kwargs.get("evaluation_only_question_ids")) or question),
        bridge_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("scientific bridge must not run")),
        q71_runner=lambda **_: (_ for _ in ()).throw(
            AssertionError("Q71 must not run")),
        question_kwargs={"state_directory": qstate})

    assert calls == [("L3",)]
    assert result.cycle_outcome == "COMPLETED"
    assert result.frontier_snapshot_id == "S1"
    assert result.stage_statuses["FRONTIER"] == "FRONTIER_INCOMPLETE"
    assert result.stage_statuses["PROJECTION"] == "COMPLETED_EVALUATION_REFRESH"


def test_unverified_incomplete_frontier_remains_fatal(tmp_path):
    frontier = SimpleNamespace(
        status="FRONTIER_INCOMPLETE", verification_status="FAILED",
        snapshot_id="S1", fingerprint="FP", investigation_epoch="E1",
        frontier_id="FR1", frontier_start="2026-01-01",
        frontier_end="2026-01-02", failure_reason="REQUIRED_DATASET_ABSENT:trade_truth")
    result = run_continuous_research_cycle(
        state_root=tmp_path / "continuous",
        frontier_runner=lambda **_: frontier,
        question_runner=_unexpected_question_call)
    assert result.cycle_outcome == "FAILED"
    assert result.failure_stage == "FRONTIER"


def test_material_evaluator_refresh_runs_scientific_downstream(tmp_path):
    state_root, qstate, frontier, question = _evaluator_refresh_fixture(
        tmp_path, changed_question_ids=("L3",))
    registry_dir = tmp_path / "registry"
    OptimisationRegistry(str(registry_dir)).save()
    calls = []
    bridge = SimpleNamespace(
        bridge_run_id="BR-REFRESH", status="NO_SCIENTIFIC_STATE_CHANGE",
        validation_handoff=(), question_changes_processed=("L3",),
        findings_created=(), findings_weakened=(), hypotheses_created=(),
        hypotheses_invalidated=(), candidates_created=(), review_required=())

    def qrunner(_frontier, **kwargs):
        calls.append(("questions", kwargs.get("evaluation_only_question_ids")))
        return question

    def brunner(_question, **_kwargs):
        calls.append(("bridge", _question.cycle_id))
        return bridge

    def q71runner(**_kwargs):
        calls.append(("q71", None))
        return {"status": "COMPLETED", "generated_questions": [], "queue": []}

    result = run_continuous_research_cycle(
        state_root=state_root, frontier_runner=lambda **_: frontier,
        question_runner=qrunner, bridge_runner=brunner, q71_runner=q71runner,
        question_kwargs={"state_directory": qstate},
        bridge_kwargs={"scientific_state_directory": tmp_path / "science",
                       "optimisation_registry_directory": registry_dir},
        max_validation_jobs=0)

    assert result.cycle_outcome == "COMPLETED", (result.failure_stage, result.failure_reason)
    assert calls == [("questions", ("L3",)), ("bridge", "QCYCLE-REFRESH"),
                     ("q71", None)]
    assert result.stage_statuses["SCIENTIFIC_STATE"] == "NO_SCIENTIFIC_STATE_CHANGE"
    assert result.stage_statuses["VALIDATION_QUEUE"] == "COMPLETED"
    assert result.stage_statuses["PROJECTION"] == "COMPLETED"


def test_continuous_cycle_order_noop_and_checkpoint(tmp_path):
    qstate, question = _fake_question(tmp_path)
    registry_dir = tmp_path / "registry"
    OptimisationRegistry(str(registry_dir)).save()
    calls = []
    frontier = SimpleNamespace(
        status="NEW_VERIFIED_IMMUTABLE_SNAPSHOT_READY", verification_status="VERIFIED", snapshot_id="S1",
        fingerprint="FP", investigation_epoch="E1", frontier_id="FR1",
        frontier_start="2026-01-01", frontier_end="2026-01-02",
        predecessor_snapshot_id=None, changed_datasets=("trade_truth",),
        stale_datasets=(), missing_optional_datasets=())
    bridge = SimpleNamespace(
        bridge_run_id="BR1", status="NO_SCIENTIFIC_STATE_CHANGE",
        validation_handoff=(), question_changes_processed=(), findings_created=(),
        findings_weakened=(), hypotheses_created=(), hypotheses_invalidated=(),
        candidates_created=(), review_required=())
    def qrunner(_frontier, **_kwargs): calls.append("questions"); return question
    def brunner(_question, **_kwargs): calls.append("bridge"); return bridge
    result = run_continuous_research_cycle(
        state_root=tmp_path / "continuous", frontier_runner=lambda **_: frontier,
        question_runner=qrunner, bridge_runner=brunner,
        q71_runner=lambda **_: {"status": "COMPLETED", "generated_questions": [], "queue": []},
        question_kwargs={"state_directory": qstate},
        bridge_kwargs={"scientific_state_directory": tmp_path / "science",
                       "optimisation_registry_directory": registry_dir},
        max_validation_jobs=0)
    assert result.cycle_outcome == "COMPLETED", (result.failure_stage, result.failure_reason)
    assert calls == ["questions", "bridge"]
    assert ContinuousCycleStore(tmp_path / "continuous" / "cycles").load_latest_success()
    assert ResearchProjectionStore(tmp_path / "continuous" / "projection").load_latest()

    calls.clear()
    no_change = SimpleNamespace(**{**frontier.__dict__, "status": "NO_NEW_GOVERNED_EVIDENCE",
                                  "verification_status": "NOT_REQUIRED", "changed_datasets": ()})
    noop = run_continuous_research_cycle(
        state_root=tmp_path / "continuous", frontier_runner=lambda **_: no_change,
        question_runner=qrunner, bridge_runner=brunner,
        question_kwargs={"state_directory": qstate},
        bridge_kwargs={"scientific_state_directory": tmp_path / "science",
                       "optimisation_registry_directory": registry_dir})
    assert noop.cycle_outcome == "NO_NEW_RESEARCH_EVIDENCE"
    assert noop.continuous_cycle_id != result.continuous_cycle_id
    assert noop.evidence_identity == result.evidence_identity == "S1"
    assert calls == []
    cycle_store = ContinuousCycleStore(tmp_path / "continuous" / "cycles")
    assert cycle_store.load_latest_success() == noop
    assert cycle_store.load_latest_attempt() == noop
    failed_frontier = SimpleNamespace(
        status="FRONTIER_INVALID", verification_status="FAILED",
        snapshot_id="S1", frontier_id="FR1", frontier_end="2026-01-03",
        failure_reason="later frontier failure")
    failed = run_continuous_research_cycle(
        state_root=tmp_path / "continuous",
        frontier_runner=lambda **_: failed_frontier)
    assert failed.cycle_outcome == "FAILED"
    assert cycle_store.load_latest_attempt() == failed
    assert cycle_store.load_latest_success() == noop
    assert ResearchProjectionStore(
        tmp_path / "continuous" / "projection").load_latest()[
            "projection_version"] == result.projection_version


def test_repeated_frontier_failure_attempts_do_not_overwrite_history(tmp_path):
    state_root = tmp_path / "continuous"

    def run_failure(frontier_end):
        bad = SimpleNamespace(
            status="FRONTIER_INVALID", verification_status="FAILED",
            snapshot_id="S1", frontier_id="FR1", frontier_end=frontier_end,
            failure_reason="same deterministic frontier failure")
        return run_continuous_research_cycle(
            state_root=state_root, frontier_runner=lambda **_: bad)

    first = run_failure("2026-01-01")
    first_path = state_root / "cycles" / "history" / f"{first.continuous_cycle_id}.json"
    first_bytes = first_path.read_bytes()
    second = run_failure("2026-01-02")
    second_path = state_root / "cycles" / "history" / f"{second.continuous_cycle_id}.json"

    assert first.cycle_outcome == second.cycle_outcome == "FAILED"
    assert first.continuous_cycle_id != second.continuous_cycle_id
    assert first.cycle_attempt_id != second.cycle_attempt_id
    assert first.evidence_identity == second.evidence_identity == "S1"
    assert first_path != second_path
    assert first_path.read_bytes() == first_bytes
    assert second_path.exists()
    assert len(list((state_root / "cycles" / "history").glob("*.json"))) == 2
    store = ContinuousCycleStore(state_root / "cycles")
    assert store.load_latest_attempt() == second
    assert store.load_latest_success() is None


def test_cycle_store_still_rejects_conflicting_rewrite_of_same_id(tmp_path):
    store = ContinuousCycleStore(tmp_path / "cycles")
    base = dict(
        continuous_cycle_id="CRCYCLE-X", cycle_outcome="FAILED",
        frontier_snapshot_id="S1", question_cycle_id=None, bridge_run_id=None,
        q71_agenda_id=None, q71_queue_id=None, validation_queue_version=None,
        projection_version=None, predecessor_cycle_id=None, started_at="",
        completed_at="", stage_statuses={}, failure_stage="FRONTIER")
    first = ContinuousResearchCycleResult(**base, cycle_attempt_id="A1")
    path = store.save(first, successful=False)
    original = path.read_bytes()
    store.save(first, successful=False)  # byte-identical replay is idempotent
    with pytest.raises(ContinuousCycleStateError, match="IDENTITY_COLLISION"):
        store.save(ContinuousResearchCycleResult(**base, cycle_attempt_id="A2"),
                   successful=False)
    assert path.read_bytes() == original


def test_continuous_cycle_records_explicit_stage_failure(tmp_path):
    bad = SimpleNamespace(status="FRONTIER_INVALID", verification_status="FAILED",
                          snapshot_id=None, frontier_id="FR-BAD", frontier_end="2026-01-01",
                          failure_reason="digest mismatch")
    result = run_continuous_research_cycle(
        state_root=tmp_path / "state", frontier_runner=lambda **_: bad)
    assert result.cycle_outcome == "FAILED"
    assert result.failure_stage == "FRONTIER"
    assert result.stage_statuses["FRONTIER"] == "FAILED"


def test_q71_uses_generated_authority_dedup_and_bounded_priority(tmp_path):
    generated = GeneratedResearchStore(tmp_path / "generated.json")
    common_cost = {
        "data_availability": "AVAILABLE", "additional_observations": "NONE",
        "prospective_waiting": "NONE", "confirmation_requirement": "NONE",
        "alternative_count": 1, "dependency_depth": 0,
    }
    high = GeneratedResearchProposal(
        research_kind=GeneratedResearchKind.EXPANSION, trigger_ref="finding:F1",
        target_kind="question", target_ref="high information question",
        specification={
            "snapshot_bound_runner": "runner.v1", "evidence_available": True,
            "information_value": {
                "question_state": "UNRESOLVED", "evidential_insufficiency": "HIGH",
                "explanation_discrimination": "DISCRIMINATES_COMPETING_EXPLANATIONS",
                "answerability": "ANSWERABLE_WITH_FROZEN_EVIDENCE"},
            "research_cost": common_cost})
    low = GeneratedResearchProposal(
        research_kind=GeneratedResearchKind.EXPANSION, trigger_ref="finding:F2",
        target_kind="question", target_ref="resolved low-information question",
        specification={
            "snapshot_bound_runner": "runner.v1", "evidence_available": True,
            "information_value": {
                "question_state": "PARTIALLY_RESOLVED", "evidential_insufficiency": "LOW",
                "explanation_discrimination": "SINGLE_HYPOTHESIS_ONLY",
                "answerability": "ANSWERABLE_WITH_FROZEN_EVIDENCE"},
            "research_cost": common_cost})
    high_record = generated.register(high)
    assert generated.register(high).generated_research_id == high_record.generated_research_id
    low_record = generated.register(low)
    result = run_q71_orchestration(
        snapshot_id="S1", capacity=1,
        coverage_store=ResearchCoverageStore(tmp_path / "coverage.json"),
        generated_store=generated,
        agenda_store=ResearchAgendaStore(tmp_path / "agenda.json"),
        state_path=tmp_path / "q71.json")
    assert len(result["generated_questions"]) == 2
    assert len(result["queue"]) == 1
    assert result["queue"][0]["generated_question_id"] == high_record.generated_research_id
    assert result["queue"][0]["generated_question_id"] != low_record.generated_research_id
    assert result["execution_mode"] == "QUEUE_ONLY_UNLESS_SNAPSHOT_BOUND_RUNNER_REGISTERED"
