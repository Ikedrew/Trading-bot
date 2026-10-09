from __future__ import annotations

import hashlib
from dataclasses import replace
from types import SimpleNamespace

from research_engine.control_plane.governed_counterfactual_evidence import (
    BASELINE_POLICY_ID,
    COUNTERFACTUAL_EVIDENCE_CLASS,
    COUNTERFACTUAL_EVIDENCE_ID_PREFIX,
    COUNTERFACTUAL_PRODUCER_IDENTITY,
    COUNTERFACTUAL_PRODUCER_VERSION,
    GOVERNED_COUNTERFACTUAL_EVIDENCE_SCHEMA,
    GOVERNED_POLICY_CATALOGUE,
    M5_AUTHORITY,
    REPLAY_METHOD_IDENTITY,
    REPLAY_METHOD_VERSION,
    CounterfactualEvidenceRow,
    CounterfactualEvidenceStore,
    GovernedCounterfactualEvidence,
    treatment_component,
    treatment_signature,
)
from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.registry.exit_policy_adjudication import CANDIDATE_POLICIES_V1
from research_engine.v10.continuous.production_validation import production_executors
from research_engine.v10.continuous.candidate_observation import (
    CandidateObservationStore, reconcile_candidate_observations,
)
from research_engine.v10.continuous.research_cadence import run_cadence_tick
from research_engine.v10.continuous.validation_queue import (
    BLOCKED,
    COMPLETED,
    FAILED,
    FORWARD_VALIDATION,
    REVIEW_REQUIRED,
    WAITING_FOR_DATA,
    ValidationQueueStore,
    enqueue_forward_validation,
    enqueue_validation_handoff,
    process_validation_queue,
)
from research_engine.v10.optimisation.models import OptimisationCandidate, ValidationPlan
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry


def _hash(policy):
    return hashlib.sha256(canonical_json(dict(policy)).encode()).hexdigest()


def _registry(path, *, delta=1.0, required=4, extra_condition=None):
    del delta
    policy = dict(CANDIDATE_POLICIES_V1[0])
    success = {
        "weighted_effect_estimate": {"gt": 0.0},
        "holm_adjusted_p_value": {"lte": 0.05},
        "confidence_interval_95_lower": {"gt": 0.0},
    }
    if extra_condition:
        success.update(extra_condition)
    failure = {
        "weighted_effect_estimate": {"lte": 0.0},
        "holm_adjusted_p_value": {"gt": 0.05},
    }
    requirements = {
        "required_sample": required,
        "primary_metrics": ["weighted_effect_estimate", "holm_adjusted_p_value"],
        "success_conditions": success,
        "failure_conditions": failure,
        "minimum_evidence_requirements": {
            "minimum_paired_lifecycles": required,
            "minimum_distinct_opportunities": required,
        },
    }
    candidate = OptimisationCandidate(
        candidate_id="OPT-PROD-1", hypothesis_id="", baseline_id="S1",
        created_at="2026-01-01T00:00:00Z", component="ExitManagement",
        changes={"policy_id": policy["policy_id"], "frozen_policy": policy},
        expected_outcome="positive paired delta", policy_id=policy["policy_id"],
        treatment_hash=_hash(policy), target_population={
            "scope": "governed", "canonical_authority_question_id": "EX1"},
        validation_requirements=requirements,
        provenance={"snapshot_id": "S1", "investigation_epoch": "E-S1"},
    )
    plan = ValidationPlan(
        candidate_id=candidate.candidate_id, baseline_id="S1",
        created_at="2026-01-01T00:00:00Z",
        metrics=list(requirements["primary_metrics"]), minimum_sample=required,
        success_conditions=success, failure_conditions=failure,
        notes="Frozen treatment scope; only governed policy differs.",
    )
    registry = OptimisationRegistry(str(path))
    registry.add_candidate(candidate)
    registry.add_plan(plan)
    registry.save()
    return registry, candidate, plan


def _row(policy, i, delta):
    opportunity = f"OPP-{i:03d}"
    return CounterfactualEvidenceRow(
        lifecycle_identity=(f"L-{i:03d}", opportunity, "H1"),
        canonical_opportunity_id=opportunity, canonical_symbol="EURUSD",
        trade_horizon="H1", timeframe="M5", entry_state={"entry_utc_epoch_s": i},
        baseline_policy_id=BASELINE_POLICY_ID,
        baseline_exit={"exit_reason": "timeout", "exit_utc_epoch_s": i + 300},
        baseline_r=0.0, governed_policy_id=policy["policy_id"],
        treatment_component=treatment_component(policy),
        treatment_parameters=policy, treatment_signature=treatment_signature(policy),
        counterfactual_exit={"exit_reason": "take_profit", "exit_utc_epoch_s": i + 300},
        counterfactual_r=delta, delta_r=delta,
        required_bar_availability={"supplied_m5_bars": 1},
        leakage_guard={"post_entry_only": True, "strictly_ascending": True,
                       "exit_aligned": True},
        replay_method=REPLAY_METHOD_IDENTITY, replay_version=REPLAY_METHOD_VERSION,
        source_evidence_lineage={"candidate_replay_digest": f"C-{i}"},
    )


def _evidence(snapshot, *, delta=1.0, n=4, frontier_start="2026-01-01T00:00:00Z",
              frontier_end="2026-01-02T00:00:00Z", rows=None, offset=0):
    if rows is None:
        rows = tuple(
            _row(dict(policy), offset + i, delta)
            for policy in CANDIDATE_POLICIES_V1 for i in range(n)
        )
    return GovernedCounterfactualEvidence(
        schema=GOVERNED_COUNTERFACTUAL_EVIDENCE_SCHEMA,
        dataset_id=COUNTERFACTUAL_EVIDENCE_ID_PREFIX + snapshot,
        produced_at=frontier_end, producer_identity=COUNTERFACTUAL_PRODUCER_IDENTITY,
        producer_version=COUNTERFACTUAL_PRODUCER_VERSION,
        evidence_class=COUNTERFACTUAL_EVIDENCE_CLASS,
        baseline_policy_id=BASELINE_POLICY_ID,
        governed_policy_catalogue=GOVERNED_POLICY_CATALOGUE,
        governed_policy_ids=tuple(item["policy_id"] for item in CANDIDATE_POLICIES_V1),
        population_identity=COUNTERFACTUAL_EVIDENCE_CLASS,
        replay_method=REPLAY_METHOD_IDENTITY, replay_version=REPLAY_METHOD_VERSION,
        m5_authority=M5_AUTHORITY, snapshot_id=snapshot,
        snapshot_fingerprint="FP-" + snapshot, investigation_epoch="E-" + snapshot,
        frontier_start=frontier_start, frontier_end=frontier_end,
        source_dataset_identities=(("shadow_runtime", "D1"),),
        source_shadow_runtime_digest="D1", source_candle_digest="D2",
        candle_authority_id="M5-A", candle_authority_digest="D2", candle_rows=(),
        rows=tuple(rows), exclusions=(), reason_codes=(), scientifically_analysable=True,
        completed_lifecycles=n, eligible_lifecycles=n,
    )


def _enqueue(registry, candidate, plan, store):
    reconcile_candidate_observations(
        registry, CandidateObservationStore(
            store.path.parent / "candidate_observations.json"), snapshot_id="S1")
    enqueue_validation_handoff([{
        "candidate_id": candidate.candidate_id,
        "policy_id": candidate.policy_id,
        "treatment_hash": candidate.treatment_hash,
        "validation_plan": plan.to_dict(),
    }], registry=registry, store=store, snapshot_id="S1", cycle_id="C1")


def test_governed_success_persists_record_and_validates(tmp_path):
    registry, candidate, plan = _registry(tmp_path / "registry")
    evidence_store = CounterfactualEvidenceStore(tmp_path / "evidence")
    evidence_store.register(_evidence("S1"))
    store = ValidationQueueStore(tmp_path / "state" / "validation_queue.json")
    _enqueue(registry, candidate, plan, store)
    validation, forward = production_executors(
        state_root=tmp_path / "state", evidence_directory=tmp_path / "evidence")
    process_validation_queue(store=store, registry=registry,
                             validation_executor=validation, forward_executor=forward)
    persisted = ValidationQueueStore(store.path).ordered()[0]
    assert persisted.status == COMPLETED
    assert persisted.output_validation_record["status"] == "VALIDATED"
    assert persisted.output_validation_record["evidence_row_digests"]
    assert registry.get_candidate(candidate.candidate_id).status == "VALIDATED"
    assert persisted.output_validation_record["live_approved"] is False


def test_ex9_timeout_protocol_uses_its_frozen_endpoint_not_r_delta(tmp_path):
    registry, candidate, plan = _registry(tmp_path / "registry")
    success = {
        "weighted_effect_estimate": {"lt": 0.0},
        "holm_adjusted_p_value": {"lte": 0.05},
    }
    failure = {
        "weighted_effect_estimate": {"gte": 0.0},
        "holm_adjusted_p_value": {"gt": 0.05},
    }
    candidate.target_population["canonical_authority_question_id"] = "EX9"
    candidate.validation_requirements.update({
        "success_conditions": success,
        "failure_conditions": failure,
        "minimum_evidence_requirements": {
            "minimum_paired_lifecycles": 4,
            "minimum_distinct_opportunities": 4,
            "endpoint_b_minimum_distinct_opportunities": 4,
        },
    })
    plan.success_conditions = success
    plan.failure_conditions = failure
    registry.save()
    rows = tuple(
        replace(
            _row(dict(policy), i, 2.0), baseline_r=-1.0,
            counterfactual_r=1.0, delta_r=2.0,
            baseline_exit={"exit_reason": "timeout", "exit_utc_epoch_s": i + 300},
            counterfactual_exit={"exit_reason": "take_profit",
                                 "exit_utc_epoch_s": i + 300},
        )
        for policy in CANDIDATE_POLICIES_V1 for i in range(4)
    )
    CounterfactualEvidenceStore(tmp_path / "evidence").register(
        _evidence("S1", rows=rows))
    store = ValidationQueueStore(tmp_path / "state" / "validation_queue.json")
    _enqueue(registry, candidate, plan, store)
    validation, forward = production_executors(
        state_root=tmp_path / "state", evidence_directory=tmp_path / "evidence")
    process_validation_queue(store=store, registry=registry,
                             validation_executor=validation, forward_executor=forward)
    record = ValidationQueueStore(store.path).ordered()[0].output_validation_record
    assert record["status"] == "VALIDATED"
    assert record["measured"]["weighted_effect_estimate"] == -1.0
    assert record["measured"]["expected_r_delta"] == 2.0


def test_scientific_failure_is_not_waiting(tmp_path):
    registry, candidate, plan = _registry(tmp_path / "registry")
    evidence_store = CounterfactualEvidenceStore(tmp_path / "evidence")
    evidence_store.register(_evidence("S1", delta=-1.0))
    store = ValidationQueueStore(tmp_path / "state" / "validation_queue.json")
    _enqueue(registry, candidate, plan, store)
    validation, forward = production_executors(
        state_root=tmp_path / "state", evidence_directory=tmp_path / "evidence")
    process_validation_queue(store=store, registry=registry,
                             validation_executor=validation, forward_executor=forward)
    assert ValidationQueueStore(store.path).ordered()[0].status == FAILED
    assert registry.get_candidate(candidate.candidate_id).status == "VALIDATION_FAILED"


def test_insufficient_and_missing_evidence_wait_idempotently(tmp_path):
    registry, candidate, plan = _registry(tmp_path / "registry", required=5)
    evidence_store = CounterfactualEvidenceStore(tmp_path / "evidence")
    evidence_store.register(_evidence("S1", n=4))
    store = ValidationQueueStore(tmp_path / "state" / "validation_queue.json")
    _enqueue(registry, candidate, plan, store)
    validation, forward = production_executors(
        state_root=tmp_path / "state", evidence_directory=tmp_path / "evidence")
    process_validation_queue(store=store, registry=registry,
                             validation_executor=validation, forward_executor=forward)
    first = ValidationQueueStore(store.path).ordered()[0]
    assert first.status == WAITING_FOR_DATA
    attempts, transitions = first.attempts, list(first.transitions)
    process_validation_queue(store=store, registry=registry,
                             validation_executor=validation, forward_executor=forward)
    second = ValidationQueueStore(store.path).ordered()[0]
    assert second.attempts == attempts
    assert second.transitions == transitions
    assert registry.get_candidate(candidate.candidate_id).status == "PROPOSED"

    registry2, candidate2, plan2 = _registry(tmp_path / "registry2")
    store2 = ValidationQueueStore(tmp_path / "state2" / "validation_queue.json")
    _enqueue(registry2, candidate2, plan2, store2)
    empty_validation, empty_forward = production_executors(
        state_root=tmp_path / "state2", evidence_directory=tmp_path / "empty")
    process_validation_queue(store=store2, registry=registry2,
                             validation_executor=empty_validation,
                             forward_executor=empty_forward)
    assert ValidationQueueStore(store2.path).ordered()[0].status == WAITING_FOR_DATA
    assert registry2.get_candidate(candidate2.candidate_id).status == "PROPOSED"


def test_missing_executor_and_unknown_criterion_fail_closed(tmp_path):
    registry, candidate, plan = _registry(tmp_path / "registry")
    store = ValidationQueueStore(tmp_path / "state" / "validation_queue.json")
    _enqueue(registry, candidate, plan, store)
    process_validation_queue(store=store, registry=registry)
    assert ValidationQueueStore(store.path).ordered()[0].status == BLOCKED
    assert registry.get_candidate(candidate.candidate_id).status == "PROPOSED"
    recovery_evidence = CounterfactualEvidenceStore(tmp_path / "recovery-evidence")
    recovery_evidence.register(_evidence("S1"))
    recovery_validation, recovery_forward = production_executors(
        state_root=tmp_path / "state",
        evidence_directory=tmp_path / "recovery-evidence")
    process_validation_queue(
        store=store, registry=registry,
        validation_executor=recovery_validation, forward_executor=recovery_forward)
    assert ValidationQueueStore(store.path).ordered()[0].status == COMPLETED
    assert registry.get_candidate(candidate.candidate_id).status == "VALIDATED"

    registry2, candidate2, plan2 = _registry(
        tmp_path / "registry2", extra_condition={"unmeasurable_metric": {"gt": 0}})
    evidence_store = CounterfactualEvidenceStore(tmp_path / "evidence")
    evidence_store.register(_evidence("S1"))
    store2 = ValidationQueueStore(tmp_path / "state2" / "validation_queue.json")
    _enqueue(registry2, candidate2, plan2, store2)
    validation, forward = production_executors(
        state_root=tmp_path / "state2", evidence_directory=tmp_path / "evidence")
    process_validation_queue(store=store2, registry=registry2,
                             validation_executor=validation, forward_executor=forward)
    assert ValidationQueueStore(store2.path).ordered()[0].status == REVIEW_REQUIRED
    assert registry2.get_candidate(candidate2.candidate_id).status == "PROPOSED"


def test_forward_requires_distinct_non_overlapping_evidence(tmp_path):
    registry, candidate, plan = _registry(tmp_path / "registry")
    evidence_store = CounterfactualEvidenceStore(tmp_path / "evidence")
    initial = _evidence("S1")
    evidence_store.register(initial)
    state = tmp_path / "state"
    store = ValidationQueueStore(state / "validation_queue.json")
    _enqueue(registry, candidate, plan, store)
    validation, forward = production_executors(
        state_root=state, evidence_directory=tmp_path / "evidence")
    process_validation_queue(store=store, registry=registry,
                             validation_executor=validation, forward_executor=forward)
    enqueue_forward_validation(candidate, plan.to_dict(), store=store,
                               snapshot_id="S1", cycle_id="C1",
                               timestamp="2026-01-02T00:00:00Z")
    process_validation_queue(store=store, registry=registry,
                             validation_executor=validation, forward_executor=forward)
    forward_job = next(job for job in ValidationQueueStore(store.path).ordered()
                       if job.kind == FORWARD_VALIDATION)
    assert forward_job.status == WAITING_FOR_DATA
    assert registry.get_candidate(candidate.candidate_id).status == "VALIDATED"

    evidence_store.register(_evidence(
        "S2", frontier_start="2026-01-02T00:00:00Z",
        frontier_end="2026-01-03T00:00:00Z", offset=100))
    process_validation_queue(store=store, registry=registry,
                             validation_executor=validation, forward_executor=forward)
    forward_job = next(job for job in ValidationQueueStore(store.path).ordered()
                       if job.kind == FORWARD_VALIDATION)
    assert forward_job.status == COMPLETED
    assert forward_job.output_validation_record["status"] == "FORWARD_VALIDATED"
    assert forward_job.output_validation_record["evidence_dataset_id"] != (
        next(job for job in store.ordered() if job.kind != FORWARD_VALIDATION)
        .output_validation_record["evidence_dataset_id"])
    assert registry.get_candidate(candidate.candidate_id).status == "FORWARD_VALIDATED"


def test_production_cadence_wires_real_executor_and_no_evidence_cannot_advance(
        tmp_path, monkeypatch):
    import research_engine.v10.continuous.research_loop as loop

    state = tmp_path / "continuous"
    registry, candidate, plan = _registry(tmp_path / "registry")
    store = ValidationQueueStore(state / "validation_queue.json")
    _enqueue(registry, candidate, plan, store)
    governed_directory = tmp_path / "analysis" / "assurance" / "counterfactual_evidence"
    CounterfactualEvidenceStore(governed_directory).register(_evidence("S1"))
    monkeypatch.chdir(tmp_path)

    observed = {}

    def production_cycle(**kwargs):
        observed["validation_executor"] = kwargs.get("validation_executor")
        observed["forward_executor"] = kwargs.get("forward_executor")
        result = process_validation_queue(
            store=store, registry=registry,
            validation_executor=kwargs["validation_executor"],
            forward_executor=kwargs["forward_executor"],
        )
        observed["transitions"] = result["transitions"]
        return SimpleNamespace(cycle_outcome="COMPLETED")

    monkeypatch.setattr(loop, "run_continuous_research_cycle", production_cycle)
    monkeypatch.setattr(loop, "_production_evaluator_registry", lambda: None)
    report = run_cadence_tick(state_root=state, force_fast_cycle=True,
                              now="2026-01-03T00:00:00+00:00")
    assert report.fast_cycle_outcome == "COMPLETED"
    assert observed["validation_executor"].__class__.__name__ == (
        "ProductionValidationExecutor")
    assert observed["forward_executor"].__class__.__name__ == (
        "ProductionValidationExecutor")
    assert ValidationQueueStore(store.path).ordered()[0].status == COMPLETED
    assert registry.get_candidate(candidate.candidate_id).status == "VALIDATED"

    empty_root = tmp_path / "empty-run"
    empty_root.mkdir()
    empty_state = empty_root / "continuous"
    empty_registry, empty_candidate, empty_plan = _registry(empty_root / "registry")
    empty_store = ValidationQueueStore(empty_state / "validation_queue.json")
    _enqueue(empty_registry, empty_candidate, empty_plan, empty_store)
    monkeypatch.chdir(empty_root)

    def empty_cycle(**kwargs):
        process_validation_queue(
            store=empty_store, registry=empty_registry,
            validation_executor=kwargs["validation_executor"],
            forward_executor=kwargs["forward_executor"],
        )
        return SimpleNamespace(cycle_outcome="COMPLETED")

    monkeypatch.setattr(loop, "run_continuous_research_cycle", empty_cycle)
    report = run_cadence_tick(state_root=empty_state, force_fast_cycle=True,
                              now="2026-01-04T00:00:00+00:00")
    assert report.fast_cycle_outcome == "COMPLETED"
    assert ValidationQueueStore(empty_store.path).ordered()[0].status == WAITING_FOR_DATA
    assert empty_registry.get_candidate(empty_candidate.candidate_id).status == "PROPOSED"
