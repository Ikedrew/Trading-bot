"""Wire 4 — generic candidate observation registration, evidence accounting
and reevaluation wiring.

These tests prove that a newly created eligible candidate is discovered from the
authoritative optimisation registry, registered generically (no hard-coded
candidate ID), survives restarts without duplication, accumulates only its own
governed evidence, and deactivates truthfully when it leaves the eligible
trajectory.
"""

from __future__ import annotations

import pytest
from dataclasses import replace

from research_engine.control_plane.governed_counterfactual_evidence import CounterfactualEvidenceStore
from tests.test_production_validation_executors import _evidence

from research_engine.v10.continuous.candidate_observation import (
    CandidateObservationStore,
    candidate_evidence_accounting,
    default_policy_catalog,
    observation_eligibility,
    observation_registration_id,
    reconcile_candidate_observations,
    treatment_hash,
)
from research_engine.v10.optimisation.models import (
    OptimisationCandidate,
    ValidationPlan,
)
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry


POLICY_A = "TRAIL_ACT_0_25R_DIST_0_10R_V1"
POLICY_B = "TRAIL_ACT_0_50R_DIST_0_25R_V1"


def _catalog():
    return default_policy_catalog()


def _candidate(cid, policy_id, *, baseline_id="BASE-1", status="PROPOSED",
               treatment=None, shadow_binding=None, provenance=None):
    catalog = _catalog()
    return OptimisationCandidate(
        candidate_id=cid,
        hypothesis_id="HYP-" + cid,
        baseline_id=baseline_id,
        status=status,
        policy_id=policy_id,
        treatment_hash=(treatment if treatment is not None
                        else treatment_hash(catalog[policy_id])),
        shadow_binding=dict(shadow_binding or {}),
        provenance=dict(provenance or {}),
        target_population={"scope": "governed",
                           "canonical_authority_question_id": "EX1"},
    )


def _plan(cid, *, baseline_id="BASE-1", minimum_sample=100):
    return ValidationPlan(
        candidate_id=cid,
        baseline_id=baseline_id,
        minimum_sample=minimum_sample,
        metrics=["paired_equal_opportunity_expectancy_delta_r"],
        success_conditions={"expectancy_delta": "> 0"},
        failure_conditions={"expectancy_delta": "<= 0"},
    )


def _registry(tmp_path, candidates_with_plans):
    registry = OptimisationRegistry(str(tmp_path / "optimisation"))
    for candidate, plan in candidates_with_plans:
        registry.add_candidate(candidate)
        registry.add_plan(plan)
    return registry


def test_generic_registration_no_hardcoded_candidate_id(tmp_path):
    registry = _registry(tmp_path, [
        (_candidate("OPT-B3-AAAA", POLICY_A), _plan("OPT-B3-AAAA")),
        (_candidate("OPT-B3-BBBB", POLICY_B), _plan("OPT-B3-BBBB")),
    ])
    store = CandidateObservationStore(tmp_path / "obs.json")
    report = reconcile_candidate_observations(registry, store, _catalog())
    assert set(report["registered"]) == {"OPT-B3-AAAA", "OPT-B3-BBBB"}
    assert report["active_count"] == 2
    assert report["blocked"] == []
    regs = store.load()
    assert set(regs) == {"OPT-B3-AAAA", "OPT-B3-BBBB"}


def test_registration_idempotent_and_restart_safe(tmp_path):
    registry = _registry(tmp_path, [
        (_candidate("OPT-B3-AAAA", POLICY_A), _plan("OPT-B3-AAAA")),
    ])
    store = CandidateObservationStore(tmp_path / "obs.json")
    first = reconcile_candidate_observations(registry, store, _catalog())
    second = reconcile_candidate_observations(registry, store, _catalog())
    assert first["registered"] == ["OPT-B3-AAAA"]
    assert second["registered"] == []  # idempotent: no duplicate registration
    # New store object over the same path (simulated restart) reconciles cleanly.
    fresh = CandidateObservationStore(tmp_path / "obs.json")
    third = reconcile_candidate_observations(registry, fresh, _catalog())
    assert third["registered"] == []
    assert set(fresh.load()) == {"OPT-B3-AAAA"}


def test_deterministic_identity(tmp_path):
    a1 = _candidate("OPT-B3-AAAA", POLICY_A)
    a2 = _candidate("OPT-B3-AAAA", POLICY_A)
    assert observation_registration_id(a1) == observation_registration_id(a2)
    b = _candidate("OPT-B3-AAAA", POLICY_B)
    assert observation_registration_id(a1) != observation_registration_id(b)


def test_eligibility_rule_coverage(tmp_path):
    catalog = _catalog()
    eligible = _candidate("C1", POLICY_A)
    assert observation_eligibility(eligible, _plan("C1"), catalog).eligible is True

    assert observation_eligibility(
        eligible, None, catalog).reason == "MISSING_VALIDATION_PLAN"
    bad_hash = _candidate("C2", POLICY_A, treatment="0" * 64)
    assert observation_eligibility(
        bad_hash, _plan("C2"), catalog).reason == "TREATMENT_HASH_MISMATCH"
    unknown = _candidate("C3", "NOT_A_POLICY", treatment="x" * 64)
    assert observation_eligibility(
        unknown, _plan("C3"), catalog).reason == "UNGOVERNED_POLICY:NOT_A_POLICY"
    superseded = _candidate("C4", POLICY_A, status="SUPERSEDED")
    assert observation_eligibility(
        superseded, _plan("C4"), catalog).reason == "BLOCKED_STATUS:SUPERSEDED"
    live = _candidate("C5", POLICY_A, shadow_binding={"live_approved": True})
    assert observation_eligibility(
        live, _plan("C5"), catalog).reason == "LIVE_APPROVED_FORBIDDEN"


def test_missing_baseline_and_malformed_candidate():
    catalog = _catalog()
    malformed = _candidate("C1", POLICY_A)
    malformed.candidate_id = ""
    assert observation_eligibility(
        malformed, _plan("C1"), catalog).reason == "MISSING_CANDIDATE_ID"
    no_baseline = _candidate("C2", POLICY_A)
    no_baseline.baseline_id = ""
    assert observation_eligibility(
        no_baseline, _plan("C2"), catalog).reason == "MISSING_BASELINE_ID"


def test_superseded_candidate_deactivated_not_reregistered(tmp_path):
    registry = _registry(tmp_path, [
        (_candidate("C1", POLICY_A), _plan("C1")),
    ])
    store = CandidateObservationStore(tmp_path / "obs.json")
    reconcile_candidate_observations(registry, store, _catalog())
    assert store.load()["C1"].status == "ACTIVE"
    registry.get_candidate("C1").status = "SUPERSEDED"
    report = reconcile_candidate_observations(registry, store, _catalog())
    assert report["registered"] == []
    assert report["deactivated"] == [{"candidate_id": "C1",
                                      "reason": "BLOCKED_STATUS:SUPERSEDED"}]
    assert store.load()["C1"].status == "INACTIVE"
    assert report["active_count"] == 0


def test_invalidated_candidate_not_registered(tmp_path):
    registry = _registry(tmp_path, [
        (_candidate("C1", POLICY_A, status="INVALIDATED_UPSTREAM"), _plan("C1")),
    ])
    store = CandidateObservationStore(tmp_path / "obs.json")
    report = reconcile_candidate_observations(registry, store, _catalog())
    assert report["registered"] == []
    assert report["active_count"] == 0
    assert report["blocked"][0]["reason"] == "BLOCKED_STATUS:INVALIDATED_UPSTREAM"


def test_multiple_candidate_isolation(tmp_path):
    registry = _registry(tmp_path, [
        (_candidate("CAND-A", POLICY_A, baseline_id="BASE-A"),
         _plan("CAND-A", baseline_id="BASE-A")),
        (_candidate("CAND-B", POLICY_B, baseline_id="BASE-B"),
         _plan("CAND-B", baseline_id="BASE-B")),
    ])
    store = CandidateObservationStore(tmp_path / "obs.json")
    reconcile_candidate_observations(registry, store, _catalog())
    regs = store.load()
    assert regs["CAND-A"].policy_id == POLICY_A
    assert regs["CAND-B"].policy_id == POLICY_B
    assert regs["CAND-A"].treatment_hash == treatment_hash(_catalog()[POLICY_A])
    assert regs["CAND-B"].treatment_hash == treatment_hash(_catalog()[POLICY_B])
    assert regs["CAND-A"].registration_id != regs["CAND-B"].registration_id
    registry.get_candidate("CAND-B").status = "REJECTED"
    report = reconcile_candidate_observations(registry, store, _catalog())
    assert store.load()["CAND-A"].status == "ACTIVE"
    assert store.load()["CAND-B"].status == "INACTIVE"
    assert report["active_count"] == 1


def _fake_evidence_store(policy_rows):
    class _Row:
        def __init__(self, digest):
            self.row_digest = digest

    class _Evidence:
        dataset_id = "CFE-DS-1"
        snapshot_id = "ISNAP-1"
        content_digest = "digest-1"

        def __init__(self, rows):
            self.rows = rows

        def rows_for_policy(self, policy_id):
            return self.rows.get(policy_id, ())

    class _Store:
        def __init__(self, evidence):
            self.evidence = evidence

        def load_latest(self):
            return self.evidence

    rows = {pid: tuple(_Row(f"{pid}-{i}") for i in range(n))
            for pid, n in policy_rows.items()}
    return _Store(_Evidence(rows))


def test_candidate_evidence_accounting_is_policy_specific(tmp_path):
    registry = _registry(tmp_path, [
        (_candidate("CAND-A", POLICY_A, baseline_id="S1", provenance={
            "snapshot_id": "S1", "investigation_epoch": "E-S1"}),
         _plan("CAND-A", baseline_id="S1")),
        (_candidate("CAND-B", POLICY_B, baseline_id="S1", provenance={
            "snapshot_id": "S1", "investigation_epoch": "E-S1"}),
         _plan("CAND-B", baseline_id="S1")),
    ])
    store = CandidateObservationStore(tmp_path / "obs.json")
    reconcile_candidate_observations(registry, store, _catalog())
    evidence = CounterfactualEvidenceStore(tmp_path / "evidence")
    evidence.register(_evidence("S1", n=4))
    accounting = candidate_evidence_accounting(store.load(), evidence)
    assert accounting["CAND-A"]["sample_count"] == 4
    assert accounting["CAND-B"]["sample_count"] == 4
    assert accounting["CAND-A"]["row_digests"] != accounting["CAND-B"]["row_digests"]


def test_no_evidence_yields_zero_samples(tmp_path):
    registry = _registry(tmp_path, [
        (_candidate("CAND-A", POLICY_A), _plan("CAND-A")),
    ])
    store = CandidateObservationStore(tmp_path / "obs.json")
    reconcile_candidate_observations(registry, store, _catalog())
    accounting = candidate_evidence_accounting(store.load(), None)
    assert accounting["CAND-A"]["sample_count"] == 0


def test_sample_accumulation_grows_with_new_evidence(tmp_path):
    registry = _registry(tmp_path, [
        (_candidate("CAND-A", POLICY_A, baseline_id="S1", provenance={
            "snapshot_id": "S1", "investigation_epoch": "E-S1"}),
         _plan("CAND-A", baseline_id="S1")),
    ])
    store = CandidateObservationStore(tmp_path / "obs.json")
    reconcile_candidate_observations(registry, store, _catalog())
    evidence = CounterfactualEvidenceStore(tmp_path / "evidence")
    first = _evidence("S1", n=4)
    evidence.register(first)
    accounting_1 = candidate_evidence_accounting(store.load(), evidence)
    assert accounting_1["CAND-A"]["sample_count"] == 4
    assert candidate_evidence_accounting(store.load(), evidence) == accounting_1
    evidence.register(replace(first, dataset_id="CFE-DS-S1-COPY"))
    assert candidate_evidence_accounting(store.load(), evidence)["CAND-A"]["sample_count"] == 4
    evidence.register(replace(_evidence("S1", n=4, offset=100),
                              dataset_id="CFE-DS-S1-NEW"))
    accounting_2 = candidate_evidence_accounting(store.load(), evidence)
    assert accounting_2["CAND-A"]["sample_count"] == 8
    assert len(accounting_2["CAND-A"]["evidence_dataset_ids"]) == 3
    assert accounting_2["CAND-A"]["validation_eligible_sample_count"] == 0
    assert accounting_2["CAND-A"]["validation_authority_status"] == (
        "AMBIGUOUS_MULTIPLE_DATASETS")


def test_identity_conflict_raises(tmp_path):
    registry = _registry(tmp_path, [
        (_candidate("C1", POLICY_A), _plan("C1")),
    ])
    store = CandidateObservationStore(tmp_path / "obs.json")
    from research_engine.v10.continuous.candidate_observation import (
        CandidateObservationRegistration,
    )
    store.save({"C1": CandidateObservationRegistration(
        registration_id="OBS-WRONG", candidate_id="C1", policy_id=POLICY_A,
        treatment_hash=treatment_hash(_catalog()[POLICY_A]),
        baseline_id="BASE-1", status="ACTIVE", registered_at="t")})
    with pytest.raises(ValueError, match="CANDIDATE_OBSERVATION_IDENTITY_CONFLICT"):
        reconcile_candidate_observations(registry, store, _catalog())


def test_store_rejects_key_mismatch(tmp_path):
    registry = _registry(tmp_path, [
        (_candidate("C1", POLICY_A), _plan("C1")),
    ])
    store = CandidateObservationStore(tmp_path / "obs.json")
    reconcile_candidate_observations(registry, store, _catalog())
    import json
    path = tmp_path / "obs.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["registrations"]["C1"]["candidate_id"] = "OTHER"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="CANDIDATE_OBSERVATION_REGISTRATION_KEY_MISMATCH"):
        store.load()
