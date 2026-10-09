"""Controlled adversarial evidence identity regressions; all stores use tmp_path."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json

import pytest

from research_engine.control_plane.governed_counterfactual_evidence import (
    CounterfactualEvidenceStore, treatment_signature,
)
from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.experiments.exit_policy_governed import _governed_population
from research_engine.v10.continuous.candidate_observation import (
    CandidateObservationStore, candidate_evidence_accounting,
    reconcile_candidate_observations,
)
from research_engine.v10.continuous.production_validation import production_executors
from research_engine.v10.continuous.validation_queue import (
    BLOCKED, COMPLETED, FORWARD_VALIDATION, ValidationQueueStore,
    enqueue_forward_validation, process_validation_queue,
)
from tests.test_candidate_observation import (
    POLICY_A, POLICY_B, _candidate, _catalog, _plan,
    _registry as observation_registry,
)
from tests.test_production_validation_executors import (
    _evidence, _enqueue, _registry as validation_registry,
)


def _registered(tmp_path, specifications):
    pairs = []
    for cid, snapshot, baseline, epoch in specifications:
        pairs.append((_candidate(cid, POLICY_A, baseline_id=baseline,
                                 provenance={"snapshot_id": snapshot,
                                             "investigation_epoch": epoch}),
                      _plan(cid, baseline_id=baseline)))
    registry = observation_registry(tmp_path, pairs)
    store = CandidateObservationStore(tmp_path / "observations.json")
    reconcile_candidate_observations(registry, store, _catalog())
    return registry, store


def test_ar02_same_policy_different_source_cannot_steal_and_same_source_can_share(tmp_path):
    _, registrations = _registered(tmp_path, [
        ("A", "S1", "S1", "E-S1"),
        ("B", "S2", "S2", "E-S2"),
        ("C", "S1", "S1", "E-S1"),
    ])
    evidence = CounterfactualEvidenceStore(tmp_path / "evidence")
    evidence.register(_evidence("S1"))
    result = candidate_evidence_accounting(registrations.load(), evidence)
    assert result["A"]["sample_count"] == result["C"]["sample_count"] == 4
    assert result["A"]["opportunity_ids"] == result["C"]["opportunity_ids"]
    assert result["B"]["sample_count"] == 0
    assert result["B"]["reason"] == "NO_ELIGIBLE_EVIDENCE_FOR_REGISTRATION"
    assert result["A"]["registration_id"] != result["C"]["registration_id"]


def test_ar02_wrong_baseline_epoch_treatment_and_missing_evidence_fail_closed(tmp_path):
    _, registrations = _registered(tmp_path, [
        ("GOOD", "S1", "S1", "E-S1"),
        ("BASE", "S1", "OTHER", "E-S1"),
        ("EPOCH", "S1", "S1", "OTHER"),
    ])
    unavailable = candidate_evidence_accounting(registrations.load(), None)
    assert unavailable["GOOD"]["reason"] == "EVIDENCE_UNAVAILABLE"
    evidence = CounterfactualEvidenceStore(tmp_path / "evidence")
    original = _evidence("S1")
    evidence.register(original)
    result = candidate_evidence_accounting(registrations.load(), evidence)
    assert result["GOOD"]["sample_count"] == 4
    assert result["BASE"]["sample_count"] == 0
    assert result["EPOCH"]["sample_count"] == 0
    altered_policy = dict(_catalog()[POLICY_B])
    bad_row = replace(original.rows[0], treatment_parameters=altered_policy,
                      treatment_signature=treatment_signature(altered_policy))
    rows = (bad_row,) + original.rows[1:]
    bad_store = CounterfactualEvidenceStore(tmp_path / "bad-evidence")
    bad_store.register(replace(original, dataset_id="CFE-BAD-TREATMENT", rows=rows))
    bad = candidate_evidence_accounting(registrations.load(), bad_store)
    assert bad["GOOD"]["sample_count"] == 0
    assert bad["GOOD"]["reason"] == "EVIDENCE_TREATMENT_OR_MEMBERSHIP_INVALID"


def test_ar02_valid_empty_is_distinct_from_unavailable_or_corrupt(tmp_path):
    _, registrations = _registered(tmp_path, [("A", "S1", "S1", "E-S1")])
    evidence = CounterfactualEvidenceStore(tmp_path / "evidence")
    empty = replace(_evidence("S1"), rows=(), scientifically_analysable=False,
                    eligible_lifecycles=0)
    evidence.register(empty)
    result = candidate_evidence_accounting(registrations.load(), evidence)["A"]
    assert result["status"] == "ACCOUNTED"
    assert result["sample_count"] == 0
    evidence.path_for(empty.dataset_id).write_text("{broken", encoding="utf-8")
    corrupt = candidate_evidence_accounting(registrations.load(), evidence)["A"]
    assert corrupt["status"] == "BLOCKED"
    assert corrupt["reason"].startswith("EVIDENCE_AUTHORITY_INVALID")


def test_ar02_unsupported_population_scope_deactivates_registration(tmp_path):
    registry, registrations = _registered(tmp_path, [("A", "S1", "S1", "E-S1")])
    registry.get_candidate("A").target_population = {"symbol": "EURUSD"}
    report = reconcile_candidate_observations(registry, registrations, _catalog())
    assert report["blocked"] == [{"candidate_id": "A",
                                  "reason": "UNSUPPORTED_POPULATION_SCOPE"}]
    evidence = CounterfactualEvidenceStore(tmp_path / "evidence")
    evidence.register(_evidence("S1"))
    result = candidate_evidence_accounting(registrations.load(), evidence)["A"]
    assert result["sample_count"] == 0
    assert result["reason"] == "REGISTRATION_INACTIVE_OR_INVALID"


def test_ar05_frozen_plan_snapshot_collision_and_restart(tmp_path):
    registry, registrations = _registered(tmp_path, [("A", "S1", "S1", "E-S1")])
    original = registrations.path.read_bytes()
    reconcile_candidate_observations(registry, registrations, _catalog())
    assert registrations.path.read_bytes() == original
    for field, value in (("validation_plan_digest", "WRONG"),
                         ("source_snapshot_id", "S2"),
                         ("investigation_epoch", "E-S2")):
        payload = json.loads(original)
        payload["registrations"]["A"][field] = value
        registrations.path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="REGISTRATION_IDENTITY_INVALID"):
            registrations.load()
    registrations.path.write_bytes(original)
    registry.get_plan("A").minimum_sample += 1
    with pytest.raises(ValueError, match="CANDIDATE_OBSERVATION_IDENTITY_CONFLICT"):
        reconcile_candidate_observations(registry, registrations, _catalog())
    assert registrations.path.read_bytes() == original


def test_ar05_invalidation_stays_inactive_and_new_candidate_epoch_keeps_lineage(tmp_path):
    registry, registrations = _registered(tmp_path, [("A", "S1", "S1", "E-S1")])
    registry.get_candidate("A").status = "INVALIDATED_UPSTREAM"
    reconcile_candidate_observations(registry, registrations, _catalog())
    inactive = registrations.load()["A"]
    assert inactive.status == "INACTIVE"
    assert inactive.history[-1]["event"] == "DEACTIVATED"
    registry.get_candidate("A").status = "PROPOSED"
    report = reconcile_candidate_observations(registry, registrations, _catalog())
    assert report["active_count"] == 0
    assert registrations.load()["A"].history == inactive.history
    registry.add_candidate(_candidate("B", POLICY_A, baseline_id="S2", provenance={
        "snapshot_id": "S2", "investigation_epoch": "E-S2"}))
    registry.add_plan(_plan("B", baseline_id="S2"))
    reconcile_candidate_observations(registry, registrations, _catalog())
    persisted = CandidateObservationStore(registrations.path).load()
    assert persisted["A"].status == "INACTIVE"
    assert persisted["B"].status == "ACTIVE"
    assert persisted["A"].registration_id != persisted["B"].registration_id


def test_ar05_queue_rejects_plan_change_after_registration(tmp_path):
    registry, candidate, plan = validation_registry(tmp_path / "registry")
    state = tmp_path / "state"
    queue = ValidationQueueStore(state / "validation_queue.json")
    _enqueue(registry, candidate, plan, queue)
    CounterfactualEvidenceStore(tmp_path / "evidence").register(_evidence("S1"))
    registry.get_plan(candidate.candidate_id).minimum_sample += 1
    validation, forward = production_executors(
        state_root=state, evidence_directory=tmp_path / "evidence")
    process_validation_queue(store=queue, registry=registry,
                             validation_executor=validation, forward_executor=forward)
    job = queue.ordered()[0]
    assert job.status == BLOCKED
    assert job.failure_reason == "QUEUED_FROZEN_PLAN_IDENTITY_MISMATCH"
    assert registry.get_candidate(candidate.candidate_id).status == "PROPOSED"


def test_governed_ex1_population_contract_links_registration_and_validation(tmp_path):
    registry, candidate, plan = validation_registry(tmp_path / "registry")
    candidate.target_population = _governed_population("EX1")
    registry.save()
    state = tmp_path / "state"
    queue = ValidationQueueStore(state / "validation_queue.json")
    _enqueue(registry, candidate, plan, queue)
    evidence = CounterfactualEvidenceStore(tmp_path / "evidence")
    evidence.register(_evidence("S1"))
    accounting = candidate_evidence_accounting(
        CandidateObservationStore(state / "candidate_observations.json").load(),
        evidence)[candidate.candidate_id]
    assert accounting["sample_count"] == 4
    validation, forward = production_executors(
        state_root=state, evidence_directory=tmp_path / "evidence")
    process_validation_queue(store=queue, registry=registry,
                             validation_executor=validation, forward_executor=forward)
    assert queue.ordered()[0].status == COMPLETED


def _initial_and_forward(tmp_path, later, *, mutate_initial=None):
    registry, candidate, plan = validation_registry(tmp_path / "registry")
    evidence = CounterfactualEvidenceStore(tmp_path / "evidence")
    evidence.register(_evidence("S1"))
    state = tmp_path / "state"
    queue = ValidationQueueStore(state / "validation_queue.json")
    _enqueue(registry, candidate, plan, queue)
    initial_executor, forward_executor = production_executors(
        state_root=state, evidence_directory=tmp_path / "evidence")
    process_validation_queue(store=queue, registry=registry,
                             validation_executor=initial_executor,
                             forward_executor=forward_executor)
    initial = queue.ordered()[0]
    assert initial.status == COMPLETED
    assert initial.output_validation_record["evidence_observation_membership"]
    accounting = candidate_evidence_accounting(
        CandidateObservationStore(state / "candidate_observations.json").load(),
        evidence)[candidate.candidate_id]
    assert accounting["registration_id"] == initial.output_validation_record[
        "observation_registration_id"]
    assert accounting["opportunity_ids"] == sorted(
        item["canonical_opportunity_id"] for item in
        initial.output_validation_record["evidence_observation_membership"])
    if mutate_initial:
        mutate_initial(initial.output_validation_record)
        queue.save()
    enqueue_forward_validation(candidate, plan.to_dict(), store=queue,
                               snapshot_id="S1", cycle_id="C2",
                               timestamp="2026-01-02T00:00:00Z")
    evidence.register(later)
    # New executor and queue instances prove the manifest survives restart.
    initial_executor, forward_executor = production_executors(
        state_root=state, evidence_directory=tmp_path / "evidence")
    queue = ValidationQueueStore(queue.path)
    process_validation_queue(store=queue, registry=registry,
                             validation_executor=initial_executor,
                             forward_executor=forward_executor)
    return next(job for job in queue.ordered() if job.kind == FORWARD_VALIDATION)


@pytest.mark.parametrize("change", ["changed_content", "changed_lifecycle", "identical"])
def test_ar04_underlying_overlap_blocks_despite_distinct_artifact(tmp_path, change):
    later = _evidence("S2", delta=1.2 if change == "changed_content" else 1.0,
                      frontier_start="2026-01-02T00:00:00Z",
                      frontier_end="2026-01-03T00:00:00Z")
    if change == "changed_lifecycle":
        later = replace(later, rows=tuple(replace(row, lifecycle_identity=(
            "OTHER-" + row.lifecycle_identity[0], *row.lifecycle_identity[1:]))
                                          for row in later.rows))
    job = _initial_and_forward(tmp_path, later)
    assert job.status == BLOCKED
    assert job.output_validation_record["reason"] == "FORWARD_OBSERVATION_POPULATION_OVERLAP"


@pytest.mark.parametrize("change", ["missing", "corrupt", "forged"])
def test_ar04_missing_or_corrupt_initial_manifest_blocks_after_restart(tmp_path, change):
    later = _evidence("S2", offset=100,
                      frontier_start="2026-01-02T00:00:00Z",
                      frontier_end="2026-01-03T00:00:00Z")
    def mutate(record):
        if change == "missing":
            record.pop("evidence_observation_membership")
        else:
            record["evidence_observation_membership"][0]["canonical_opportunity_id"] = "WRONG"
            if change == "forged":
                record["evidence_membership_digest"] = hashlib.sha256(canonical_json({
                    "members": record["evidence_observation_membership"]}).encode(
                        "utf-8")).hexdigest()
    job = _initial_and_forward(tmp_path, later, mutate_initial=mutate)
    assert job.status == BLOCKED
    assert job.output_validation_record["reason"] == (
        "INITIAL_MEMBERSHIP_AUTHORITY_UNVERIFIABLE" if change == "forged"
        else "INITIAL_MEMBERSHIP_INVALID_OR_MISSING")


def test_ar04_disjoint_later_population_validates_after_restart(tmp_path):
    later = _evidence("S2", offset=100,
                      frontier_start="2026-01-02T00:00:00Z",
                      frontier_end="2026-01-03T00:00:00Z")
    job = _initial_and_forward(tmp_path, later)
    assert job.status == COMPLETED
    assert job.output_validation_record["status"] == "FORWARD_VALIDATED"
    assert job.output_validation_record["evidence_observation_membership"]


def test_ar04_overlap_in_another_holm_family_policy_blocks(tmp_path):
    later = _evidence("S2", offset=100,
                      frontier_start="2026-01-02T00:00:00Z",
                      frontier_end="2026-01-03T00:00:00Z")
    reused = next(row for row in _evidence("S1").rows
                  if row.governed_policy_id == POLICY_B)
    changed = replace(reused, counterfactual_r=1.2, delta_r=1.2)
    rows = list(later.rows)
    index = next(i for i, row in enumerate(rows)
                 if row.governed_policy_id == POLICY_B)
    rows[index] = changed
    job = _initial_and_forward(tmp_path, replace(later, rows=tuple(rows)))
    assert job.status == BLOCKED
    assert job.output_validation_record["reason"] == (
        "FORWARD_OBSERVATION_POPULATION_OVERLAP")
