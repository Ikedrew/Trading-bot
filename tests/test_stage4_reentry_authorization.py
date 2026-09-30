"""Refinement 4 acceptance/adversarial re-entry authorization tests."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from research_engine.control_plane import gap_governance as G
from research_engine.control_plane import scientific_reentry as R
from research_engine.control_plane import stage4_dataset_snapshot as D
from research_engine.control_plane import stage4_identity as I
from research_engine.control_plane import stage4_satisfaction as S


NOW = "2026-09-30T00:00:00Z"


def _ready_gap(reentry_id: str):
    store = G.build_store()
    wid = "GWI-R1-IMPL"
    store = G.apply_resolution_evidence(
        store, wid, sorted(G.PRE_RERUN_EVIDENCE[G.GAP_TYPE_IMPL]))
    return G.mark_reentry_ready(store, wid, reentry_id)


def _fixture(reentry_id="RE-R4", *, observed=1, required=1):
    satisfaction, evidence, snapshots, epochs, decision = (
        R._fixture_reentry_authorities(
            fixture_id=reentry_id, observed=observed, required=required))
    epoch = next(iter(epochs.values()))
    state = R.bootstrap_state()
    gap = _ready_gap(reentry_id)
    request = R.build_reentry_request(
        reentry_id=reentry_id, question_id="R1", reason="R4 fixture",
        trigger_type=R.TRIGGER_IMPLEMENTATION_REPAIR,
        evidence_epoch=epoch.epoch_id, evidence_fingerprint=None,
        evidence_epochs=epochs,
        observation_requirement_id=decision.observation_requirement_id,
        satisfaction_decision_id=decision.satisfaction_decision_id,
        gap_work_item_id="GWI-R1-IMPL", requested_at=NOW, state=state)
    context = {
        "satisfaction_registry": satisfaction, "evidence_sets": evidence,
        "snapshot_registry": snapshots, "evidence_epochs": epochs,
    }
    return state, gap, request, context, decision


def _authorize(state, gap, request, context):
    return R.authorize_reentry(
        state, request, gap_store=gap, now=NOW, **context)


def test_valid_current_satisfied_decision_becomes_eligible_and_authorized():
    state, gap, request, context, decision = _fixture()
    eligibility = R.evaluate_reentry_eligibility(
        request, evaluated_at=NOW, **context)
    assert eligibility.eligibility_state == R.ELIGIBLE
    assert eligibility.satisfaction_decision_id == decision.satisfaction_decision_id
    authorized = _authorize(state, gap, request, context)
    event = authorized["reentry_events"][0]
    assert event["satisfaction_decision_id"] == decision.satisfaction_decision_id
    assert event["reentry_eligibility_id"].startswith("REEL-")
    assert event["reentry_authorization_id"].startswith("REAUTH-")
    assert authorized["counts"]["reentry_authorizations"] == 1


def test_not_satisfied_indeterminate_and_invalid_decisions_are_denied():
    for observed, required, expected in (
            (0, 1, S.NOT_SATISFIED), ("invalid", 1, S.INVALID)):
        state, gap, request, context, decision = _fixture(
            "RE-R4-" + expected, observed=observed, required=required)
        assert decision.decision == expected
        with pytest.raises(R.ReentryError, match="REENTRY_NOT_ELIGIBLE"):
            _authorize(state, gap, request, context)

    satisfaction, evidence, snapshots, epochs = R.canonical_reentry_authorities()
    decision = satisfaction.current_for_requirement("OR-01")
    assert decision is not None and decision.decision == S.INDETERMINATE
    epoch = next(iter(epochs.values()))
    state = R.bootstrap_state()
    gap = _ready_gap("RE-R4-INDET")
    request = R.build_reentry_request(
        reentry_id="RE-R4-INDET", question_id="R1", reason="deny",
        trigger_type=R.TRIGGER_IMPLEMENTATION_REPAIR,
        evidence_epoch=epoch.epoch_id,
        evidence_fingerprint=R.evidence_epoch_fingerprint(epoch),
        observation_requirement_id="OR-01",
        satisfaction_decision_id=decision.satisfaction_decision_id,
        gap_work_item_id="GWI-R1-IMPL", requested_at=NOW, state=state)
    with pytest.raises(R.ReentryError, match="INDETERMINATE"):
        R.authorize_reentry(
            state, request, gap_store=gap, now=NOW,
            satisfaction_registry=satisfaction, evidence_sets=evidence,
            snapshot_registry=snapshots, evidence_epochs=epochs)


@pytest.mark.parametrize("decision_id", ["", "not-a-decision", "SDEC-" + "A" * 32])
def test_missing_malformed_and_unknown_decision_ids_are_denied(decision_id):
    state, _gap, request, context, _ = _fixture("RE-R4-BAD-ID")
    request = replace(request, satisfaction_decision_id=decision_id)
    eligibility = R.evaluate_reentry_eligibility(request, evaluated_at=NOW,
                                                 **context)
    assert eligibility.eligibility_state == R.ELIGIBILITY_INVALID


@pytest.mark.parametrize("rid", ["OR-02", "OG-EX2-deadbeef", "GAP-1", "R1"])
def test_requirement_mismatch_or_noncanonical_identity_is_denied(rid):
    _state, _gap, request, context, _ = _fixture("RE-R4-BAD-OR-" + rid)
    eligibility = R.evaluate_reentry_eligibility(
        replace(request, observation_requirement_id=rid),
        evaluated_at=NOW, **context)
    assert eligibility.eligibility_state == R.ELIGIBILITY_INVALID


def test_superseded_satisfied_decision_denied_and_latest_accepted():
    state, gap, request, context, first = _fixture("RE-R4-SUPERSEDE")
    policy = next(iter(context["satisfaction_registry"]._policies.values()))
    second = S.evaluate(
        observation_requirement_id="OR-01",
        evidence_set_ids=first.evidence_set_ids, threshold_policy=policy,
        evaluation_metrics={"sample_count": 2},
        evidence_sets=context["evidence_sets"],
        snapshot_registry=context["snapshot_registry"], evaluated_at=NOW,
        supersedes_satisfaction_decision_id=first.satisfaction_decision_id)
    registry = S.SatisfactionDecisionRegistry(policies=(policy,))
    registry.register(first)
    registry.register(second)
    context["satisfaction_registry"] = registry
    old = R.evaluate_reentry_eligibility(request, evaluated_at=NOW, **context)
    assert old.eligibility_state == R.NOT_ELIGIBLE
    assert old.eligibility_reason == "SATISFACTION_DECISION_SUPERSEDED"
    latest_request = replace(request,
                             satisfaction_decision_id=second.satisfaction_decision_id)
    latest = R.evaluate_reentry_eligibility(
        latest_request, evaluated_at=NOW, **context)
    assert latest.eligibility_state == R.ELIGIBLE
    assert _authorize(state, gap, latest_request, context)[
        "counts"]["reentry_authorizations"] == 1


def test_missing_or_changed_evidence_membership_and_snapshot_are_denied():
    _state, _gap, request, context, _ = _fixture("RE-R4-LINEAGE")
    for changes in (
        {"evidence_sets": {}},
        {"snapshot_registry": D.DatasetSnapshotRegistry()},
    ):
        broken = dict(context)
        broken.update(changes)
        eligibility = R.evaluate_reentry_eligibility(
            request, evaluated_at=NOW, **broken)
        assert eligibility.eligibility_state == R.ELIGIBILITY_INVALID

    original = next(iter(context["evidence_sets"].values()))
    member = I.EvidenceMemberReference(
        reference_type="legacy", reference_id="changed",
        dataset="shadow_runtime")
    changed = I.EvidenceSet(original.evidence_set_id, ("OR-01",), (member,))
    broken = dict(context)
    broken["evidence_sets"] = {changed.evidence_set_id: changed}
    eligibility = R.evaluate_reentry_eligibility(
        request, evaluated_at=NOW, **broken)
    assert eligibility.eligibility_state == R.ELIGIBILITY_INVALID


def test_unresolved_legacy_and_live_populations_cannot_authorize():
    _state, _gap, request, context, _ = _fixture("RE-R4-UNRESOLVED")
    member = I.EvidenceMemberReference(
        reference_type="legacy", reference_id="legacy", dataset="shadow_runtime")
    original_id = next(iter(context["evidence_sets"]))
    unresolved = I.EvidenceSet(original_id, ("OR-01",), (member,))
    broken = dict(context)
    broken["evidence_sets"] = {original_id: unresolved}
    assert R.evaluate_reentry_eligibility(
        request, evaluated_at=NOW, **broken).eligibility_state == (
            R.ELIGIBILITY_INVALID)
    live = D.live_population(
        dataset_name="shadow_runtime", schema_version="shadow_runtime_v1",
        schema_generation=1, observed_record_count=1)
    assert live.dataset_snapshot_id is None


def test_unknown_and_incompatible_epochs_are_rejected_exactly():
    _state, _gap, request, context, _ = _fixture("RE-R4-EPOCH")
    unknown = replace(request, evidence_epoch="TOTALLY_UNREGISTERED_EPOCH")
    result = R.evaluate_reentry_eligibility(unknown, evaluated_at=NOW, **context)
    assert result.eligibility_state == R.ELIGIBILITY_INVALID
    assert result.eligibility_reason == "EVIDENCE_EPOCH_UNREGISTERED"

    epoch = next(iter(context["evidence_epochs"].values()))
    incompatible = replace(epoch, observation_requirements=("OR-02",))
    broken = dict(context)
    broken["evidence_epochs"] = {epoch.epoch_id: incompatible}
    result = R.evaluate_reentry_eligibility(request, evaluated_at=NOW, **broken)
    assert result.eligibility_reason == "EVIDENCE_EPOCH_REQUIREMENT_MISMATCH"


def test_malformed_and_mismatched_fingerprints_are_rejected_exactly():
    _state, _gap, request, context, _ = _fixture("RE-R4-FP")
    malformed = R.evaluate_reentry_eligibility(
        replace(request, evidence_fingerprint="not-a-content-hash"),
        evaluated_at=NOW, **context)
    assert malformed.eligibility_reason == "EVIDENCE_FINGERPRINT_MALFORMED"
    mismatch = R.evaluate_reentry_eligibility(
        replace(request, evidence_fingerprint="0" * 64),
        evaluated_at=NOW, **context)
    assert mismatch.eligibility_reason == "EVIDENCE_FINGERPRINT_MISMATCH"
    assert R.evaluate_reentry_eligibility(
        request, evaluated_at=NOW, **context).eligibility_state == R.ELIGIBLE


def test_label_only_gap_readiness_cannot_authorize_current_truth():
    satisfaction, evidence, snapshots, epochs = R.canonical_reentry_authorities()
    decision = satisfaction.current_for_requirement("OR-01")
    assert decision is not None
    epoch = next(iter(epochs.values()))
    state = R.bootstrap_state()
    gap = _ready_gap("RE-R4-LABELS")
    item = G.get_work_item(gap, "GWI-R1-IMPL")
    assert set(item["resolution_evidence"]) >= set(
        G.PRE_RERUN_EVIDENCE[G.GAP_TYPE_IMPL])
    request = R.build_reentry_request(
        reentry_id="RE-R4-LABELS", question_id="R1", reason="labels only",
        trigger_type=R.TRIGGER_IMPLEMENTATION_REPAIR,
        evidence_epoch=epoch.epoch_id,
        evidence_fingerprint=R.evidence_epoch_fingerprint(epoch),
        observation_requirement_id="OR-01",
        satisfaction_decision_id=decision.satisfaction_decision_id,
        gap_work_item_id="GWI-R1-IMPL", requested_at=NOW, state=state)
    with pytest.raises(R.ReentryError, match="INDETERMINATE"):
        R.authorize_reentry(
            state, request, gap_store=gap, now=NOW,
            satisfaction_registry=satisfaction, evidence_sets=evidence,
            snapshot_registry=snapshots, evidence_epochs=epochs)


def test_duplicate_authorization_is_idempotent_and_execution_is_one_shot():
    state, gap, request, context, _ = _fixture("RE-R4-ONESHOT")
    authorized = _authorize(state, gap, request, context)
    replay = _authorize(authorized, gap, request, context)
    assert replay == authorized
    executed = R.execute_scoped_run(
        authorized, request.reentry_id,
        runner=R._fixture_runner(["R1"]), now=NOW)
    with pytest.raises(R.ReentryError, match="NOT_AUTHORIZED"):
        R.execute_scoped_run(
            executed, request.reentry_id,
            runner=R._fixture_runner(["R1"]), now=NOW)


def test_eligibility_authorization_persist_reload_and_tamper_detection(tmp_path):
    state, gap, request, context, _ = _fixture("RE-R4-PERSIST")
    authorized = _authorize(state, gap, request, context)
    path = tmp_path / "reentry.json"
    R.save_state(authorized, path)
    loaded = R.load_state(path)
    assert loaded == authorized
    R.ReentryEligibility.from_dict(loaded["reentry_eligibilities"][0])
    R.ReentryAuthorization.from_dict(loaded["reentry_authorizations"][0])
    tampered = deepcopy(loaded)
    tampered["reentry_authorizations"][0]["satisfaction_decision_id"] = (
        "SDEC-" + "0" * 32)
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(R.ReentryError):
        R.load_state(path)


def test_current_persisted_truth_has_zero_live_eligibility_or_authorization():
    state = R.load_state()
    assert len(state["reentry_eligibilities"]) == 15
    assert state["counts"]["reentry_eligible"] == 0
    assert state["counts"]["reentry_authorizations"] == 0
    assert state["counts"]["governed_reentry_executions"] == 0
    assert state["counts"]["legacy_ungoverned_reentry_events"] == 8
    assert all(row["eligibility_state"] == R.NOT_ELIGIBLE
               for row in state["reentry_eligibilities"])


def test_current_persisted_satisfaction_authority_is_fifteen_indeterminate():
    registry = S.SatisfactionDecisionRegistry.load(S.STATE_PATH)
    decisions = [registry.current_for_requirement(rid)
                 for rid in I.CANONICAL_REQUIREMENT_IDS]
    assert len(decisions) == 15
    assert all(row is not None for row in decisions)
    assert {row.decision for row in decisions} == {S.INDETERMINATE}
    # No persisted decision is SATISFIED, so none can ever become eligible.
    assert not any(row.decision == S.SATISFIED for row in decisions)


# --- Refinement 4 acceptance: explicit adversarial proofs ------------------


def test_label_only_data_gap_readiness_cannot_authorize():
    """The three schema/producer labels are progress inputs, never authority."""
    store = G.build_store()
    labels = ("schema_change_deployed", "producer_emitting",
              "sample_condition_satisfied")
    assert set(labels) == set(G.PRE_RERUN_EVIDENCE[G.GAP_TYPE_DATA])
    store = G.apply_resolution_evidence(
        store, "GWI-R4-DATA", sorted(G.PRE_RERUN_EVIDENCE[G.GAP_TYPE_DATA]))
    store = G.mark_reentry_ready(store, "GWI-R4-DATA", "RE-R4-LABELSONLY")
    item = G.get_work_item(store, "GWI-R4-DATA")
    # All three labels are present and the gap really is re-entry ready...
    assert set(labels) <= set(item["resolution_evidence"])
    assert item["status"] in G.reentry_ready_statuses()
    # ...yet the governed satisfaction decision remains the only authority.
    satisfaction, evidence, snapshots, epochs = R.canonical_reentry_authorities()
    decision = satisfaction.current_for_requirement("OR-01")
    assert decision is not None and decision.decision == S.INDETERMINATE
    epoch = next(iter(epochs.values()))
    state = R.bootstrap_state()
    request = R.build_reentry_request(
        reentry_id="RE-R4-LABELSONLY", question_id="R4",
        reason="schema_change_deployed+producer_emitting+"
               "sample_condition_satisfied only",
        trigger_type=R.TRIGGER_DATA_THRESHOLD_REACHED,
        evidence_epoch=epoch.epoch_id,
        evidence_fingerprint=R.evidence_epoch_fingerprint(epoch),
        observation_requirement_id="OR-01",
        satisfaction_decision_id=decision.satisfaction_decision_id,
        gap_work_item_id="GWI-R4-DATA", requested_at=NOW, state=state)
    with pytest.raises(R.ReentryError, match="INDETERMINATE"):
        R.authorize_reentry(
            state, request, gap_store=store, now=NOW,
            satisfaction_registry=satisfaction, evidence_sets=evidence,
            snapshot_registry=snapshots, evidence_epochs=epochs)


@pytest.mark.parametrize("field,value", [
    ("producer_fingerprint", "0" * 64),
    ("dataset_version", "tampered_schema_v9"),
    ("schema_generation", 99),
    ("producer_version", "tampered-producer-v9"),
])
def test_schema_and_producer_mismatch_between_epoch_and_snapshot(field, value):
    """An epoch whose lineage disagrees with its frozen snapshot is refused."""
    _state, _gap, request, context, _ = _fixture("RE-R4-LINEAGE-" + field)
    epoch = next(iter(context["evidence_epochs"].values()))
    tampered = replace(epoch, **{field: value})
    broken = dict(context)
    broken["evidence_epochs"] = {epoch.epoch_id: tampered}
    eligibility = R.evaluate_reentry_eligibility(
        request, evaluated_at=NOW, **broken)
    assert eligibility.eligibility_state == R.ELIGIBILITY_INVALID
    assert eligibility.eligibility_reason == "EVIDENCE_EPOCH_LINEAGE_MISMATCH"


def test_caller_supplied_fingerprint_is_never_trusted_over_canonical_hash():
    """A well-formed but wrong caller fingerprint must not authorize."""
    _state, _gap, request, context, _ = _fixture("RE-R4-RECOMPUTE")
    _sat, _ev, _sn, other_epochs, _dec = R._fixture_reentry_authorities(
        fixture_id="RE-R4-RECOMPUTE-OTHER", observed=1, required=1)
    other_epoch = next(iter(other_epochs.values()))
    poisoned = replace(
        request, evidence_fingerprint=R.evidence_epoch_fingerprint(other_epoch))
    # Well-formed and genuinely a content hash - of the wrong epoch.
    assert len(poisoned.evidence_fingerprint) == 64
    eligibility = R.evaluate_reentry_eligibility(
        poisoned, evaluated_at=NOW, **context)
    assert eligibility.eligibility_state == R.ELIGIBILITY_INVALID
    assert eligibility.eligibility_reason == "EVIDENCE_FINGERPRINT_MISMATCH"
    # Canonical recomputation still accepts the untouched request.
    assert R.evaluate_reentry_eligibility(
        request, evaluated_at=NOW, **context).eligibility_state == R.ELIGIBLE


def test_arbitrary_unregistered_epoch_cannot_authorize():
    """`TOTALLY_UNREGISTERED_EPOCH` fails closed on both paths."""
    _state, _gap, request, context, _ = _fixture("RE-R4-ARBITRARY")
    forged = replace(request, evidence_epoch="TOTALLY_UNREGISTERED_EPOCH")
    eligibility = R.evaluate_reentry_eligibility(forged, evaluated_at=NOW,
                                                 **context)
    assert eligibility.eligibility_state == R.ELIGIBILITY_INVALID
    assert eligibility.eligibility_reason == "EVIDENCE_EPOCH_UNREGISTERED"
    state = R.bootstrap_state()
    gap = _ready_gap("RE-R4-ARBITRARY")
    with pytest.raises(R.ReentryError, match="REENTRY_NOT_ELIGIBLE"):
        _authorize(state, gap, forged, context)


def test_legacy_pre_refinement4_events_remain_auditable_but_cannot_act():
    """Legacy events are history: readable, never executable or extendable."""
    state = R.load_state()
    legacy = [event for event in state["reentry_events"]
              if event.get("governance_classification") == R.LEGACY_UNGOVERNED]
    assert len(legacy) == 8
    for event in legacy:
        reentry_id = event["reentry_id"]
        # Auditable: identity, question, scope and terminal state all readable.
        assert event["question_id"] in R.QUESTION_ID_SET
        assert event["authorization_state"] in R.ALLOWED_AUTHORIZATION_STATES
        assert event.get("legacy_resolution_state") == "LEGACY_UNRESOLVED"
        assert event.get("scientific_result")
        # Legacy events carry no Refinement-4 authority identity at all.
        assert not str(event.get("reentry_authorization_id") or "")
        assert not str(event.get("reentry_eligibility_id") or "")
        assert not str(event.get("satisfaction_decision_id") or "")
        # Never executable - not even if forged back into AUTHORIZED.
        forged = deepcopy(state)
        target = next(row for row in forged["reentry_events"]
                      if row["reentry_id"] == reentry_id)
        target["authorization_state"] = R.AUTH_AUTHORIZED
        R._refresh_fingerprints(forged)
        with pytest.raises(R.ReentryError,
                           match="LEGACY_UNGOVERNED_NOT_EXECUTABLE"):
            R.plan_scoped_run(forged, reentry_id)
        with pytest.raises(R.ReentryError,
                           match="LEGACY_UNGOVERNED_NOT_EXECUTABLE"):
            R.execute_scoped_run(
                forged, reentry_id, runner=R._fixture_runner(["R1"]), now=NOW)
    # No legacy event is counted as governed execution authority.
    assert state["counts"]["governed_reentry_executions"] == 0


def test_legacy_reentry_id_cannot_be_reused_for_new_live_work():
    """A legacy id is a permanent audit identity, never re-mintable."""
    real = R.load_state()
    legacy_id = real["reentry_events"][0]["reentry_id"]
    gap = _ready_gap(legacy_id)
    satisfaction, evidence, snapshots, epochs, decision = (
        R._fixture_reentry_authorities(
            fixture_id="RE-R4-LEGACY-REUSE", observed=1, required=1))
    epoch = next(iter(epochs.values()))
    request = R.build_reentry_request(
        reentry_id=legacy_id, question_id="R1", reason="reuse a legacy id",
        trigger_type=R.TRIGGER_IMPLEMENTATION_REPAIR,
        evidence_epoch=epoch.epoch_id, evidence_fingerprint=None,
        evidence_epochs=epochs,
        observation_requirement_id=decision.observation_requirement_id,
        satisfaction_decision_id=decision.satisfaction_decision_id,
        gap_work_item_id="GWI-R1-IMPL", requested_at=NOW, state=real)
    # Eligibility is genuinely ELIGIBLE: only the id reuse may fail.
    assert R.evaluate_reentry_eligibility(
        request, evaluated_at=NOW, satisfaction_registry=satisfaction,
        evidence_sets=evidence, snapshot_registry=snapshots,
        evidence_epochs=epochs).eligibility_state == R.ELIGIBLE
    with pytest.raises(R.ReentryError, match="REENTRY_ID_ALREADY_USED"):
        R.authorize_reentry(
            real, request, gap_store=gap, now=NOW,
            satisfaction_registry=satisfaction, evidence_sets=evidence,
            snapshot_registry=snapshots, evidence_epochs=epochs)
    # The persisted truth is completely untouched by the refused attempt.
    assert R.load_state() == real


def test_eligibility_and_authorization_records_are_separate_and_bound(tmp_path):
    """Eligibility is not authority; both records persist and reload intact."""
    state, gap, request, context, decision = _fixture("RE-R4-SEPARATE")
    authorized = _authorize(state, gap, request, context)
    eligibility_row = authorized["reentry_eligibilities"][0]
    authorization_row = authorized["reentry_authorizations"][0]
    # Distinct records with distinct id namespaces, one shared decision.
    assert eligibility_row["reentry_eligibility_id"].startswith("REEL-")
    assert authorization_row["reentry_authorization_id"].startswith("REAUTH-")
    assert "reentry_authorization_id" not in eligibility_row
    assert (eligibility_row["eligibility_fingerprint"]
            != authorization_row["authorization_fingerprint"])
    assert (authorization_row["reentry_eligibility_id"]
            == eligibility_row["reentry_eligibility_id"])
    for row in (eligibility_row, authorization_row):
        assert row["satisfaction_decision_id"] == decision.satisfaction_decision_id
    # Eligibility alone never authorizes: execution still fails closed.
    eligible_only = deepcopy(state)
    eligible_only["reentry_eligibilities"] = [eligibility_row]
    R._refresh_fingerprints(eligible_only)
    with pytest.raises(R.ReentryError, match="REENTRY_NOT_FOUND"):
        R.execute_scoped_run(
            eligible_only, request.reentry_id,
            runner=R._fixture_runner(["R1"]), now=NOW)
    path = tmp_path / "separate.json"
    R.save_state(authorized, path)
    assert R.load_state(path) == authorized
    # Tampering with either record independently is detected on reload.
    for collection, field, value in (
            ("reentry_eligibilities", "eligibility_state", R.NOT_ELIGIBLE),
            ("reentry_authorizations", "reentry_id", "RE-R4-OTHER")):
        tampered = deepcopy(authorized)
        tampered[collection][0][field] = value
        path.write_text(json.dumps(tampered), encoding="utf-8")
        with pytest.raises(R.ReentryError):
            R.load_state(path)


def test_one_shot_decision_cannot_authorize_two_different_reentries():
    """One SATISFIED decision mints at most one authorization, ever."""
    state, gap, request, context, decision = _fixture("RE-R4-ONE-SHOT-2")
    authorized = _authorize(state, gap, request, context)
    assert authorized["counts"]["reentry_authorizations"] == 1
    assert _authorize(authorized, gap, request, context) == authorized
    # Replaying the same decision under a different id is refused.
    other = replace(request, reentry_id="RE-R4-ONE-SHOT-3")
    with pytest.raises(R.ReentryError,
                       match="SATISFACTION_DECISION_ALREADY_AUTHORIZED"):
        R.authorize_reentry(authorized, other, gap_store=gap, now=NOW, **context)
    # Execution is one-shot, and the executed state is terminal.
    executed = R.execute_scoped_run(
        authorized, request.reentry_id,
        runner=R._fixture_runner(["R1"]), now=NOW)
    assert executed["counts"]["governed_reentry_executions"] == 1
    with pytest.raises(R.ReentryError, match="NOT_AUTHORIZED"):
        R.execute_scoped_run(
            executed, request.reentry_id,
            runner=R._fixture_runner(["R1"]), now=NOW)
    assert decision.decision == S.SATISFIED
