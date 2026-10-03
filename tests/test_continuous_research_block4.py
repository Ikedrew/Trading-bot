from __future__ import annotations

from types import SimpleNamespace
import hashlib
import json

from research_engine.v10.continuous.cycle_state import ContinuousCycleStore
from research_engine.v10.continuous.question_cycle_state import QuestionCycleStore
from research_engine.v10.continuous.research_loop import run_continuous_research_cycle
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
    return {"questions": {
        f"Q{i:03d}": {"question_id": f"Q{i:03d}", "question_text": f"Question {i}",
                      "status": "WAITING_FOR_DATA" if i == 6 else "COMPLETE",
                      "sample_n": i, "substantive_answer": None,
                      "last_evaluated_snapshot": "S1"}
        for i in range(1, 71)
    }}


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


def _fake_question(tmp_path):
    state = tmp_path / "questions"
    QuestionCycleStore(state).save_projection({
        "cycle_schema": "canonical_question_cycle_v1", "cycle_id": "QCYCLE-X",
        **_question_projection(),
    })
    return state, SimpleNamespace(
        cycle_id="QCYCLE-X", total_questions=70, cycle_status="COMPLETED",
        completed_at="2026-01-02T00:00:00Z")


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
    assert calls == []


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
