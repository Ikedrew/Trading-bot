from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.v10.continuous.question_cycle_state import (
    CanonicalQuestionCycleResult,
    CanonicalQuestionResult,
    QuestionCycleStore,
    immutable_json,
)
from research_engine.v10.continuous.scientific_state_bridge import (
    NO_SCIENTIFIC_STATE_CHANGE,
    ScientificStateBridgeError,
    run_scientific_state_bridge,
)
from research_engine.v10.continuous.scientific_state_store import ScientificStateStore
from research_engine.v10.optimisation.models import OptimisationCandidate, ResearchHypothesis
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry


STAMP = "2026-10-03T12:00:00+00:00"
POLICY = {
    "policy_id": "TRAIL_ACT_0_25R_DIST_0_10R_V1",
    "policy_type": "TRAILING",
    "activation_r": 0.25,
    "distance_r": 0.10,
}


def _result(snapshot: str, *, n: int = 100, confidence: str = "MEDIUM",
            conclusion: str = "The measured effect is positive.",
            metrics: dict | None = None) -> CanonicalQuestionResult:
    return CanonicalQuestionResult(
        question_id="E1",
        question_version="registry:v1",
        snapshot_id=snapshot,
        snapshot_fingerprint="fp-" + snapshot,
        investigation_epoch="epoch-" + snapshot,
        evaluated_at=STAMP,
        status="COMPLETE",
        substantive_answer={"scientific_conclusion": conclusion},
        sample_n=n,
        minimum_required_n=50,
        sample_deficit=0,
        key_metrics={
            "scientifically_meaningful": True,
            "effect_size": 0.25,
            "effect_direction": "POSITIVE",
            **(metrics or {}),
        },
        confidence=confidence,
        statistical_output={"confidence_interval": [0.1, 0.4]},
        evidence_datasets=("trade_truth",),
        evidence_references=({"dataset": "trade_truth", "sha256": "abc"},),
        limitations=("observational",),
        implementation_status="IMPLEMENTED",
        runner="fake.runner",
        runner_version="v1",
    )


def _cycle(store: QuestionCycleStore, number: int, result: CanonicalQuestionResult | None,
           *, changed: tuple[str, ...] | None = None) -> CanonicalQuestionCycleResult:
    snapshot = result.snapshot_id if result else f"ISNAP-{number}"
    cycle_id = f"QCYCLE-{number:032X}"
    changed_ids = changed if changed is not None else (() if result is None else ("E1",))
    questions = {}
    result_ids = {}
    for qid in BASELINE_QUESTION_IDS:
        row = result if qid == "E1" and result is not None else CanonicalQuestionResult(
            question_id=qid,
            question_version="registry:v1",
            snapshot_id=snapshot,
            snapshot_fingerprint="fp-" + snapshot,
            investigation_epoch="epoch-" + snapshot,
            evaluated_at=STAMP,
            status="WAITING_FOR_DATA",
        )
        result_ids[qid] = row.result_id
        questions[qid] = {"result": row.to_dict()}
    projection = {
        "cycle_schema": "canonical_question_cycle_v1",
        "cycle_id": cycle_id,
        "cycle_snapshot_id": snapshot,
        "questions": questions,
    }
    cycle = CanonicalQuestionCycleResult(
        cycle_id=cycle_id,
        snapshot_id=snapshot,
        fingerprint="fp-" + snapshot,
        investigation_epoch="epoch-" + snapshot,
        predecessor_cycle_id=None,
        predecessor_snapshot_id=None,
        frontier_start="2026-10-01T00:00:00+00:00",
        frontier_end="2026-10-03T00:00:00+00:00",
        total_questions=70,
        evaluated_count=len(changed_ids),
        retained_count=70 - len(changed_ids),
        complete_count=1 if result else 0,
        negative_result_count=0,
        insufficient_count=0,
        waiting_count=69 if result else 70,
        blocked_count=0,
        unimplemented_count=0,
        alias_count=0,
        changed_question_ids=tuple(changed_ids),
        unchanged_question_ids=tuple(qid for qid in BASELINE_QUESTION_IDS if qid not in changed_ids),
        failed_question_ids=(),
        cycle_status="COMPLETED",
        started_at=STAMP,
        completed_at=STAMP,
        current_projection_path=str(store.current_path),
        cycle_history_path=str(store.cycle_path(cycle_id)),
        question_history_root=str(store.question_history_directory),
        result_ids=result_ids,
    )
    if result is not None and "E1" in changed_ids:
        store.save_question_result(result)
    immutable_json(store.projection_path(cycle_id), projection)
    store.save_cycle(cycle)
    return cycle


def _run(tmp_path: Path, cycle: CanonicalQuestionCycleResult, qstore: QuestionCycleStore,
         registry: OptimisationRegistry | None = None, **kwargs):
    registry = registry or OptimisationRegistry(str(tmp_path / "optimisation"))
    return run_scientific_state_bridge(
        cycle,
        question_store=qstore,
        scientific_store=ScientificStateStore(tmp_path / "scientific"),
        optimisation_registry=registry,
        treatment_memory_path=tmp_path / "memory.json",
        policy_catalog=(POLICY,),
        **kwargs,
    ), registry


def test_meaningful_finding_hypothesis_history_and_idempotency(tmp_path):
    qstore = QuestionCycleStore(tmp_path / "questions")
    cycle = _cycle(qstore, 1, _result("ISNAP-1"))
    first, registry = _run(tmp_path, cycle, qstore)
    second, _ = _run(tmp_path, cycle, qstore, registry)
    assert len(first.findings_created) == 1
    assert len(first.hypotheses_created) == 1
    assert first.candidate_design_required
    hypothesis = registry.get_hypothesis(first.hypotheses_created[0])
    assert hypothesis.hypothesis_type == "OBSERVATIONAL_HYPOTHESIS"
    assert hypothesis.mechanism_unknown is True
    assert second.to_dict() == first.to_dict()
    state = ScientificStateStore(tmp_path / "scientific").document
    assert len(next(iter(state["findings"].values()))) == 1


def test_strengthen_weaken_invalidate_and_trivial_sample_change(tmp_path):
    qstore = QuestionCycleStore(tmp_path / "questions")
    first, registry = _run(tmp_path, _cycle(qstore, 1, _result("ISNAP-1", confidence="LOW")), qstore)
    strengthened, _ = _run(
        tmp_path, _cycle(qstore, 2, _result("ISNAP-2", confidence="HIGH")), qstore, registry)
    trivial, _ = _run(
        tmp_path, _cycle(qstore, 3, _result("ISNAP-3", n=150, confidence="HIGH")), qstore, registry)
    weakened, _ = _run(
        tmp_path,
        _cycle(qstore, 4, _result("ISNAP-4", metrics={"finding_action": "FINDING_WEAKENED"})),
        qstore, registry,
    )
    invalidated, _ = _run(
        tmp_path,
        _cycle(qstore, 5, _result("ISNAP-5", metrics={"finding_action": "FINDING_INVALIDATED"})),
        qstore, registry,
    )
    assert first.findings_created
    assert strengthened.findings_strengthened
    assert trivial.status == NO_SCIENTIFIC_STATE_CHANGE
    assert weakened.findings_weakened and weakened.hypotheses_updated
    assert invalidated.findings_invalidated and invalidated.hypotheses_invalidated
    state = ScientificStateStore(tmp_path / "scientific").document
    assert len(next(iter(state["findings"].values()))) == 4


def test_no_threshold_is_review_not_finding(tmp_path):
    qstore = QuestionCycleStore(tmp_path / "questions")
    result = _result("ISNAP-1")
    result = CanonicalQuestionResult(**{
        **result.identity_material(),
        "key_metrics": {"effect_size": 0.25},
        "statistical_output": None,
    })
    bridge, _ = _run(tmp_path, _cycle(qstore, 1, result), qstore)
    assert bridge.status == NO_SCIENTIFIC_STATE_CHANGE
    assert bridge.review_required
    assert not bridge.findings_created


def test_governed_policy_creates_proposed_candidate_and_plan(tmp_path):
    qstore = QuestionCycleStore(tmp_path / "questions")
    result = _result("ISNAP-1", metrics={
        "governed_policy_id": POLICY["policy_id"],
        "governed_policy_parameters": POLICY,
        "target_component": "ExitManagement",
        "expected_effect": "positive paired expectancy",
        "validation_criteria": {
            "required_sample": 300,
            "primary_metrics": ["paired_expectancy_delta_r"],
            "secondary_metrics": ["profit_factor", "max_drawdown_r"],
            "robustness_slices": ["symbol", "regime"],
            "success_conditions": {"ci_lower": "> 0"},
            "failure_conditions": {"ci_includes_zero": "true"},
        },
    })
    bridge, registry = _run(tmp_path, _cycle(qstore, 1, result), qstore)
    assert len(bridge.candidates_created) == 1
    candidate = registry.get_candidate(bridge.candidates_created[0])
    assert candidate.status == "PROPOSED"
    assert candidate.policy_id == POLICY["policy_id"]
    assert len(candidate.treatment_hash) == 64
    assert candidate.source_question_results == [result.result_id]
    assert registry.get_plan(candidate.candidate_id).minimum_sample == 300
    assert bridge.validation_handoff[0]["source_question_result_id"] == result.result_id


def test_identical_and_equivalent_candidate_suppressed(tmp_path):
    qstore = QuestionCycleStore(tmp_path / "questions")
    metrics = {"governed_policy_id": POLICY["policy_id"]}
    result1 = _result("ISNAP-1", metrics=metrics)
    first, registry = _run(tmp_path, _cycle(qstore, 1, result1), qstore)
    result2 = _result("ISNAP-2", metrics={**metrics, "finding_action": "FINDING_STRENGTHENED"})
    second, _ = _run(tmp_path, _cycle(qstore, 2, result2), qstore, registry)
    assert len(registry.list_candidates()) == 1
    assert second.duplicate_equivalent_treatments_suppressed == first.candidates_created


def test_upstream_state_propagation_respects_live_boundary(tmp_path):
    qstore = QuestionCycleStore(tmp_path / "questions")
    result = _result("ISNAP-1", metrics={"governed_policy_id": POLICY["policy_id"]})
    first, registry = _run(tmp_path, _cycle(qstore, 1, result), qstore)
    candidate = registry.get_candidate(first.candidates_created[0])
    candidate.status = "SHADOW_VALIDATION_ACTIVE"
    candidate.shadow_binding = {"live_approved": False, "identity": "frozen"}
    registry.save()
    invalid = _result("ISNAP-2", metrics={"finding_action": "FINDING_INVALIDATED"})
    bridge, _ = _run(tmp_path, _cycle(qstore, 2, invalid), qstore, registry)
    assert bridge.governance_signals
    assert candidate.status == "SHADOW_VALIDATION_ACTIVE"
    assert candidate.shadow_binding == {"live_approved": False, "identity": "frozen"}


def test_unvalidated_candidate_is_blocked_or_reviewed_by_upstream_change(tmp_path):
    qstore = QuestionCycleStore(tmp_path / "questions")
    result = _result("ISNAP-1", metrics={"governed_policy_id": POLICY["policy_id"]})
    first, registry = _run(tmp_path, _cycle(qstore, 1, result), qstore)
    candidate_id = first.candidates_created[0]
    weak = _result("ISNAP-2", metrics={"finding_action": "FINDING_WEAKENED"})
    weakened, _ = _run(tmp_path, _cycle(qstore, 2, weak), qstore, registry)
    candidate = registry.get_candidate(candidate_id)
    assert candidate.status == "PROPOSED"
    assert candidate.provenance["evidence_state"] == "FINDING_WEAKENED"
    assert candidate_id in weakened.candidates_updated
    invalid = _result("ISNAP-3", metrics={"finding_action": "FINDING_INVALIDATED"})
    invalidated, _ = _run(tmp_path, _cycle(qstore, 3, invalid), qstore, registry)
    assert registry.get_candidate(candidate_id).status == "BLOCKED_UPSTREAM_INVALIDATED"
    assert candidate_id in invalidated.candidates_updated


class _FakeMemory:
    memory_identity = "TMR-FAKE"

    def __init__(self, evidence_bearing=True):
        self._evidence_bearing = evidence_bearing

    def to_dict(self):
        return {"policy": POLICY["policy_id"]}

    def is_evidence_bearing(self):
        return self._evidence_bearing


class _FakeDecision:
    memory_identities = ("TMR-FAKE",)
    decision_identity = "RVD-FAKE"

    def __init__(self, permitted):
        self._permitted = permitted

    def was_permitted(self):
        return self._permitted


class _FakeTreatmentStore:
    def __init__(self, permitted=None):
        self._permitted = permitted

    def memories(self):
        return (_FakeMemory(),)

    def decisions(self):
        return () if self._permitted is None else (_FakeDecision(self._permitted),)


def test_treatment_memory_rejection_blocks_and_revisit_permission_reopens(tmp_path):
    qstore = QuestionCycleStore(tmp_path / "questions")
    result = _result("ISNAP-1", metrics={"governed_policy_id": POLICY["policy_id"]})
    cycle = _cycle(qstore, 1, result)
    registry = OptimisationRegistry(str(tmp_path / "optimisation"))
    blocked = run_scientific_state_bridge(
        cycle, question_store=qstore,
        scientific_store=ScientificStateStore(tmp_path / "scientific-blocked"),
        optimisation_registry=registry,
        treatment_store=_FakeTreatmentStore(), policy_catalog=(POLICY,),
    )
    assert not blocked.candidates_created
    assert blocked.duplicate_equivalent_treatments_suppressed
    assert any("REVISIT_NOT_AUTHORISED" in row for row in blocked.review_required)

    reopened = run_scientific_state_bridge(
        cycle, question_store=qstore,
        scientific_store=ScientificStateStore(tmp_path / "scientific-reopened"),
        optimisation_registry=OptimisationRegistry(str(tmp_path / "optimisation-reopened")),
        treatment_store=_FakeTreatmentStore(permitted=True), policy_catalog=(POLICY,),
    )
    assert reopened.candidates_created


def test_opt_dp1_002_lineage_reconciled_without_mutation(tmp_path):
    qstore = QuestionCycleStore(tmp_path / "questions")
    cycle = _cycle(qstore, 1, None)
    registry = OptimisationRegistry(str(tmp_path / "optimisation"))
    registry.add_hypothesis(ResearchHypothesis(
        hypothesis_id="HYP-DP1-002", source_finding="F-DP1-006", status="VALIDATED"))
    registry.add_candidate(OptimisationCandidate(
        candidate_id="OPT-DP1-002",
        hypothesis_id="HYP-DP1-002",
        baseline_id="BASE",
        status="SHADOW_VALIDATION_ACTIVE",
        policy_id=POLICY["policy_id"],
        treatment_hash="preserved-hash",
        shadow_binding={"live_approved": False, "frozen": True},
    ))
    bridge, _ = _run(tmp_path, cycle, qstore, registry)
    candidate = registry.get_candidate("OPT-DP1-002")
    assert bridge.status == NO_SCIENTIFIC_STATE_CHANGE
    assert len(registry.list_candidates()) == 1
    assert candidate.treatment_hash == "preserved-hash"
    assert candidate.status == "SHADOW_VALIDATION_ACTIVE"
    assert candidate.shadow_binding == {"live_approved": False, "frozen": True}
    reconciled = ScientificStateStore(tmp_path / "scientific").document["reconciled_external_lineage"]
    assert reconciled["OPT-DP1-002"]["finding_id"] == "F-DP1-006"


def test_missing_immutable_result_and_invalid_cycle_fail_loud(tmp_path):
    qstore = QuestionCycleStore(tmp_path / "questions")
    result = _result("ISNAP-1")
    cycle = _cycle(qstore, 1, result)
    qstore.question_result_path("E1", "ISNAP-1").unlink()
    with pytest.raises(ScientificStateBridgeError, match="MISSING_IMMUTABLE"):
        _run(tmp_path, cycle, qstore)
    with pytest.raises(ScientificStateBridgeError, match="SOURCE_CYCLE_NOT_FOUND"):
        run_scientific_state_bridge(
            "QCYCLE-DOES-NOT-EXIST", question_store=qstore,
            scientific_state_directory=tmp_path / "scientific")


def test_persistence_failure_rolls_back_registry_and_no_success_receipt(tmp_path):
    qstore = QuestionCycleStore(tmp_path / "questions")
    result = _result("ISNAP-1", metrics={"governed_policy_id": POLICY["policy_id"]})
    cycle = _cycle(qstore, 1, result)
    registry = OptimisationRegistry(str(tmp_path / "optimisation"))

    def fail(stage):
        if stage == "before_scientific_state":
            raise OSError("simulated state failure")

    with pytest.raises(ScientificStateBridgeError, match="BRIDGE_PERSISTENCE_FAILED"):
        _run(tmp_path, cycle, qstore, registry, persistence_hook=fail)
    path = tmp_path / "optimisation" / "registry.json"
    assert not path.exists()
    assert not list((tmp_path / "scientific" / "runs").glob("*.json")) \
        if (tmp_path / "scientific" / "runs").exists() else True


def test_isolated_item_failure_leaves_no_partial_finding(tmp_path):
    qstore = QuestionCycleStore(tmp_path / "questions")
    result = _result("ISNAP-1", metrics={"falsification_criteria": 7})
    bridge, registry = _run(tmp_path, _cycle(qstore, 1, result), qstore)
    assert bridge.failures and bridge.failures[0]["question_id"] == "E1"
    state = ScientificStateStore(tmp_path / "scientific").document
    assert state["findings"] == {}
    assert registry.list_hypotheses() == []


def test_corrupt_authoritative_state_fails_closed(tmp_path):
    qstore = QuestionCycleStore(tmp_path / "questions")
    cycle = _cycle(qstore, 1, None)
    state_dir = tmp_path / "scientific"
    state_dir.mkdir()
    (state_dir / "state.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(Exception, match="SCIENTIFIC_STATE_UNREADABLE"):
        run_scientific_state_bridge(
            cycle, question_store=qstore,
            scientific_state_directory=state_dir,
            optimisation_registry=OptimisationRegistry(str(tmp_path / "optimisation")),
            treatment_memory_path=tmp_path / "memory.json",
            policy_catalog=(POLICY,),
        )
