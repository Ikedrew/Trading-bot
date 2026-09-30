"""Refinement 3 acceptance and adversarial satisfaction-decision tests."""
from __future__ import annotations

import copy
import json

import pytest

from research_engine.control_plane import gap_governance as G
from research_engine.control_plane import stage4_dataset_authority as A
from research_engine.control_plane import stage4_dataset_snapshot as D
from research_engine.control_plane import stage4_identity as I
from research_engine.control_plane import stage4_observation_closeout as C
from research_engine.control_plane import stage4_observation_state as O
from research_engine.control_plane import stage4_satisfaction as S


def _snapshot(*, rid="OR-01", marker=1, generation=1,
              population_class=D.GOVERNED_REQUIREMENT_POPULATION):
    return D.freeze_population(
        dataset_name="shadow_runtime", schema_version="shadow_runtime_v1",
        schema_generation=generation, generation_state=D.GENERATION_CONFIRMED,
        generation_evidence="test contract", identity_grain="one event",
        identity_grain_evidence="test contract",
        producer_version="producer-v1", producer_fingerprint="f" * 64,
        source_boundaries=(f"object={marker}.jsonl",),
        population_filters=("scope=test",), observation_requirements=(rid,),
        population_class=population_class,
        records=({"marker": marker},), frozen_at="2026-09-30T00:00:00Z")


def _evidence(snapshot, *, rid="OR-01", evidence_id="ESET-REF3-A"):
    return D.evidence_set_for_snapshots(
        evidence_set_id=evidence_id,
        observation_requirement_ids=(rid,), snapshots=(snapshot,))


def _policy(*, rid="OR-01", required=2, version="1"):
    return S.ThresholdPolicy.create(
        observation_requirement_id=rid,
        rules=({"threshold_id": f"{rid}-COUNT", "field": "sample_count",
                "operator": ">=", "value": required},),
        authority="test authority", source="test source", version=version)


def _decision(*, rid="OR-01", marker=1, observed=2, policy=None,
              evidence_id="ESET-REF3-A", supersedes=None,
              population_class=D.GOVERNED_REQUIREMENT_POPULATION):
    snapshot = _snapshot(rid=rid, marker=marker,
                         population_class=population_class)
    evidence = _evidence(snapshot, rid=rid, evidence_id=evidence_id)
    registry = D.DatasetSnapshotRegistry((snapshot,))
    resolved_policy = policy or _policy(rid=rid)
    decision = S.evaluate(
        observation_requirement_id=rid,
        evidence_set_ids=(evidence.evidence_set_id,),
        threshold_policy=resolved_policy,
        evaluation_metrics={"sample_count": observed},
        evidence_sets={evidence.evidence_set_id: evidence},
        snapshot_registry=registry, evaluated_at="2026-09-30T00:00:00Z",
        supersedes_satisfaction_decision_id=supersedes)
    return decision, resolved_policy, evidence, registry


def test_identity_is_deterministic_and_changes_with_evidence_or_policy():
    first, policy, evidence, snapshots = _decision()
    replay = S.evaluate(
        observation_requirement_id="OR-01",
        evidence_set_ids=(evidence.evidence_set_id,), threshold_policy=policy,
        evaluation_metrics={"sample_count": 2},
        evidence_sets={evidence.evidence_set_id: evidence},
        snapshot_registry=snapshots, evaluated_at="2027-01-01T00:00:00Z")
    changed_evidence, _, _, _ = _decision(marker=2)
    changed_policy, _, _, _ = _decision(policy=_policy(required=3, version="2"))
    assert first.satisfaction_decision_id == replay.satisfaction_decision_id
    assert first.decision_fingerprint != replay.decision_fingerprint
    assert first.satisfaction_decision_id != changed_evidence.satisfaction_decision_id
    assert first.satisfaction_decision_id != changed_policy.satisfaction_decision_id


@pytest.mark.parametrize("rid", ["", "OR-99", "OG-EX2-deadbeef", "Q71", "GAP-1"])
def test_only_known_canonical_requirement_identity_is_accepted(rid):
    with pytest.raises(S.SatisfactionDecisionError):
        S.evaluate(
            observation_requirement_id=rid, evidence_set_ids=(),
            threshold_policy=_policy(), evaluation_metrics={},
            evidence_sets={}, snapshot_registry=D.DatasetSnapshotRegistry(),
            evaluated_at="2026-09-30T00:00:00Z")


def test_threshold_pass_fail_and_missing_metric_derive_controlled_states():
    passed, _, _, _ = _decision(observed=2)
    failed, _, _, _ = _decision(observed=1)
    snapshot = _snapshot()
    evidence = _evidence(snapshot)
    missing = S.evaluate(
        observation_requirement_id="OR-01", evidence_set_ids=(evidence.evidence_set_id,),
        threshold_policy=_policy(), evaluation_metrics={},
        evidence_sets={evidence.evidence_set_id: evidence},
        snapshot_registry=D.DatasetSnapshotRegistry((snapshot,)),
        evaluated_at="2026-09-30T00:00:00Z")
    assert passed.decision == S.SATISFIED
    assert failed.decision == S.NOT_SATISFIED
    assert missing.decision == S.INDETERMINATE
    assert missing.threshold_results[0].result_state == S.RESULT_MISSING


def test_unknown_evidence_and_unregistered_snapshot_are_invalid():
    unknown = S.evaluate(
        observation_requirement_id="OR-01", evidence_set_ids=("ESET-UNKNOWN",),
        threshold_policy=_policy(), evaluation_metrics={"sample_count": 2},
        evidence_sets={}, snapshot_registry=D.DatasetSnapshotRegistry(),
        evaluated_at="2026-09-30T00:00:00Z")
    snapshot = _snapshot()
    evidence = _evidence(snapshot)
    unregistered = S.evaluate(
        observation_requirement_id="OR-01",
        evidence_set_ids=(evidence.evidence_set_id,), threshold_policy=_policy(),
        evaluation_metrics={"sample_count": 2},
        evidence_sets={evidence.evidence_set_id: evidence},
        snapshot_registry=D.DatasetSnapshotRegistry(),
        evaluated_at="2026-09-30T00:00:00Z")
    assert unknown.decision == S.INVALID
    assert unregistered.decision == S.INVALID


def test_legacy_unresolved_and_future_only_backfill_remain_indeterminate():
    member = I.EvidenceMemberReference(
        reference_type="legacy", reference_id="legacy", dataset="shadow_runtime")
    legacy = I.EvidenceSet("ESET-LEGACY-REF3", ("OR-01",), (member,))
    unresolved = S.evaluate(
        observation_requirement_id="OR-01",
        evidence_set_ids=(legacy.evidence_set_id,), threshold_policy=_policy(),
        evaluation_metrics={"sample_count": 2},
        evidence_sets={legacy.evidence_set_id: legacy},
        snapshot_registry=D.DatasetSnapshotRegistry(),
        evaluated_at="2026-09-30T00:00:00Z")
    future, _, _, _ = _decision(
        rid="OR-08", policy=_policy(rid="OR-08"),
        population_class=D.AUDIT_BOUNDARY_POPULATION)
    assert unresolved.decision == S.INDETERMINATE
    assert future.decision == S.INDETERMINATE
    assert future.future_only_historical_evidence is True


def test_multiple_evidence_sets_are_recorded_and_aggregated():
    first = _snapshot(marker=1)
    second = _snapshot(marker=2)
    a = _evidence(first, evidence_id="ESET-REF3-A")
    b = _evidence(second, evidence_id="ESET-REF3-B")
    decision = S.evaluate(
        observation_requirement_id="OR-01",
        evidence_set_ids=(b.evidence_set_id, a.evidence_set_id),
        threshold_policy=_policy(), evaluation_metrics={"sample_count": 2},
        evidence_sets={a.evidence_set_id: a, b.evidence_set_id: b},
        snapshot_registry=D.DatasetSnapshotRegistry((first, second)),
        evaluated_at="2026-09-30T00:00:00Z")
    assert decision.decision == S.SATISFIED
    assert decision.evidence_set_ids == ("ESET-REF3-A", "ESET-REF3-B")
    assert len(decision.dataset_snapshot_ids) == 2


def test_mixed_schema_generations_fail_invalid():
    first = _snapshot(marker=1, generation=1)
    second = _snapshot(marker=2, generation=2)
    evidence = D.evidence_set_for_snapshots(
        evidence_set_id="ESET-MIXED-GENERATION",
        observation_requirement_ids=("OR-01",), snapshots=(first, second))
    decision = S.evaluate(
        observation_requirement_id="OR-01",
        evidence_set_ids=(evidence.evidence_set_id,), threshold_policy=_policy(),
        evaluation_metrics={"sample_count": 2},
        evidence_sets={evidence.evidence_set_id: evidence},
        snapshot_registry=D.DatasetSnapshotRegistry((first, second)),
        evaluated_at="2026-09-30T00:00:00Z")
    assert decision.decision == S.INVALID
    assert decision.evidence_validation_state == D.GENERATION_MIXED


def test_multiple_thresholds_are_aggregated_deterministically():
    snapshot = _snapshot()
    evidence = _evidence(snapshot)
    policy = S.ThresholdPolicy.create(
        observation_requirement_id="OR-01", authority="test authority",
        source="test source", rules=(
            {"threshold_id": "COUNT", "field": "sample_count",
             "operator": ">=", "value": 2},
            {"threshold_id": "COVERAGE", "field": "coverage",
             "operator": ">=", "value": 1.0},
        ))
    decision = S.evaluate(
        observation_requirement_id="OR-01",
        evidence_set_ids=(evidence.evidence_set_id,), threshold_policy=policy,
        evaluation_metrics={"coverage": 0.99, "sample_count": 3},
        evidence_sets={evidence.evidence_set_id: evidence},
        snapshot_registry=D.DatasetSnapshotRegistry((snapshot,)),
        evaluated_at="2026-09-30T00:00:00Z")
    assert decision.decision == S.NOT_SATISFIED
    assert [(row.threshold_id, row.passed) for row in decision.threshold_results] == [
        ("COUNT", True), ("COVERAGE", False)]


def test_live_unfrozen_evidence_cannot_produce_satisfied():
    live = D.live_population(
        dataset_name="shadow_runtime", schema_version="shadow_runtime_v1",
        schema_generation=1, observed_record_count=10)
    assert live.dataset_snapshot_id is None
    member = I.EvidenceMemberReference(
        reference_type="live_population", reference_id="live",
        dataset="shadow_runtime")
    evidence = I.EvidenceSet("ESET-LIVE-REF3", ("OR-01",), (member,))
    decision = S.evaluate(
        observation_requirement_id="OR-01",
        evidence_set_ids=(evidence.evidence_set_id,), threshold_policy=_policy(),
        evaluation_metrics={"sample_count": 10},
        evidence_sets={evidence.evidence_set_id: evidence},
        snapshot_registry=D.DatasetSnapshotRegistry(),
        evaluated_at="2026-09-30T00:00:00Z")
    assert decision.decision == S.INDETERMINATE
    assert decision.dataset_snapshot_ids == ()


def test_arbitrary_satisfied_and_tampered_payload_are_rejected():
    decision, _, _, _ = _decision()
    forged = decision.to_dict()
    forged["evaluation_metrics"] = {"sample_count": 0}
    with pytest.raises(S.SatisfactionDecisionError):
        S.GovernedSatisfactionDecision.from_dict(forged)
    forged = decision.to_dict()
    forged["decision_fingerprint"] = "0" * 64
    with pytest.raises(S.SatisfactionDecisionError,
                       match="DECISION_FINGERPRINT_MISMATCH"):
        S.GovernedSatisfactionDecision.from_dict(forged)
    label_only = O.RequirementEvidence(
        observation_requirement_id="OR-01", schema_ready=True,
        producer_ready=True, evidence_valid=True, completeness_met=True,
        threshold_rule_present=True, threshold_met=True, lineage_valid=True)
    assert O.can_satisfy(label_only) == (
        False, ("governed_satisfaction_decision",))


def test_membership_or_policy_change_under_same_decision_id_is_rejected():
    decision, _, _, _ = _decision()
    changed_membership = decision.to_dict()
    changed_membership["evidence_set_ids"] = ["ESET-DIFFERENT"]
    with pytest.raises(S.SatisfactionDecisionError,
                       match="SATISFACTION_DECISION_ID_MISMATCH"):
        S.GovernedSatisfactionDecision.from_dict(changed_membership)
    changed_policy = decision.to_dict()
    changed_policy["threshold_policy_id"] = "TPOL-" + "A" * 24
    with pytest.raises(S.SatisfactionDecisionError,
                       match="SATISFACTION_DECISION_ID_MISMATCH"):
        S.GovernedSatisfactionDecision.from_dict(changed_policy)


def test_persistence_reload_recomputes_decision_and_detects_store_tampering(tmp_path):
    decision, policy, evidence, snapshots = _decision()
    registry = S.SatisfactionDecisionRegistry(policies=(policy,),
                                               decisions=(decision,))
    path = tmp_path / "satisfaction.json"
    registry.save(path)
    loaded = S.SatisfactionDecisionRegistry.load(path)
    verified = loaded.verify(
        decision.satisfaction_decision_id,
        evidence_sets={evidence.evidence_set_id: evidence},
        snapshot_registry=snapshots)
    assert verified.to_dict() == decision.to_dict()

    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["decisions"][0]["decision"] = S.NOT_SATISFIED
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(S.SatisfactionDecisionError,
                       match="SATISFACTION_STORE_FINGERPRINT_MISMATCH"):
        S.SatisfactionDecisionRegistry.load(path)


def test_supersession_is_additive_and_history_is_preserved():
    first, policy, _, _ = _decision(marker=1)
    second, _, _, _ = _decision(
        marker=2, policy=policy,
        supersedes=first.satisfaction_decision_id)
    registry = S.SatisfactionDecisionRegistry(policies=(policy,))
    registry.register(first)
    registry.register(second)
    assert registry.current_for_requirement("OR-01") == second
    assert registry.history_for_requirement("OR-01") == (first, second)
    assert registry.get(first.satisfaction_decision_id) == first


def test_same_id_conflicting_content_and_policy_reinterpretation_fail_closed():
    decision, policy, _, _ = _decision()
    payload = decision.to_dict()
    payload["threshold_policy_version"] = "999"
    with pytest.raises(S.SatisfactionDecisionError):
        S.GovernedSatisfactionDecision.from_dict(payload)
    registry = S.SatisfactionDecisionRegistry(policies=(policy,),
                                               decisions=(decision,))
    with pytest.raises(S.SatisfactionDecisionError, match="SUPERSESSION_REQUIRED"):
        registry.register(_decision(marker=2, policy=policy)[0])


def test_bootstrap_authority_is_persisted_and_independently_verifiable(tmp_path):
    snapshots = D.bootstrap_registry()
    evidence = S.governed_evidence_map(snapshots)
    registry = S.build_registry(snapshots)
    path = tmp_path / "authority.json"
    registry.save(path)
    loaded = S.SatisfactionDecisionRegistry.load(path)
    for rid in I.CANONICAL_REQUIREMENT_IDS:
        decision = loaded.current_for_requirement(rid)
        assert decision is not None
        assert decision.decision in S.DECISION_STATES
        assert loaded.verify(
            decision.satisfaction_decision_id,
            evidence_sets=evidence, snapshot_registry=snapshots) == decision


def test_closeout_state_and_dataset_authority_reference_governed_decisions():
    matrix = C._load(C.MATRIX_PATH)
    snapshots = D.bootstrap_registry()
    decisions = S.build_registry(snapshots)
    versions = C.build_version_registry(matrix, snapshots)
    closeout = C.reconcile_gaps(matrix, versions, decisions)
    assert len(closeout["rows"]) == 31
    assert all(row["satisfaction_decision_id"].startswith("SDEC-")
               for row in closeout["rows"])
    assert not any(row["closeout_state"] == C.SATISFIED_OBSERVATION
                   for row in closeout["rows"])

    store = A.build_store()
    assert all(row["final_satisfaction_authority"] ==
               "GOVERNED_SATISFACTION_DECISION_ONLY"
               for row in store["observation_requirement_transitions"])
    assert any(row["satisfies_current_contract"] and
               not row["governed_satisfied"]
               for row in store["observation_requirement_transitions"])


def test_gap_authority_exposes_decision_without_changing_reentry_behavior():
    authority = G.observation_requirement_authority("OR-01")
    assert authority["satisfaction_decision_id"].startswith("SDEC-")
    assert authority["legacy_resolution_labels_role"] == "THRESHOLD_INPUTS_ONLY"
    assert authority["governed_satisfied"] is False
