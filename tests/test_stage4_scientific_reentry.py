"""Stage 4 governed scientific re-entry, recertification and versioning tests.

Covers the 40 required properties of the question-scoped re-entry authority.
Read-only with respect to every frozen V1 artifact: no certification, finding,
report or gap-state file is written by this module, and no full 70-question
historical pass, S3/live read or Q71+ work is performed.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from research_engine.control_plane import assured_epistemic_findings as A
from research_engine.control_plane import gap_governance as G
from research_engine.control_plane import scientific_reentry as S

BASELINE_CERTIFICATION = Path(
    "analysis/assurance/final_70_question_certification_20260928.json")
BASELINE_FINDINGS = Path(
    "analysis/assurance/assured_epistemic_findings_20260928.json")
REENTRY_STATE = Path("research_engine/control_plane/scientific_reentry_state.json")
REENTRY_MODULES = (Path("research_engine/control_plane/scientific_reentry.py"),)
FROZEN_ARTIFACTS = (
    BASELINE_CERTIFICATION,
    BASELINE_FINDINGS,
    Path("analysis/assurance/historical_research_pass_20260928.json"),
)
NOW = "2026-09-29T00:00:00Z"


@pytest.fixture(scope="module")
def state():
    return S.bootstrap_state()


@pytest.fixture(scope="module")
def baseline_certification():
    return json.loads(BASELINE_CERTIFICATION.read_text(encoding="utf-8"))


def _ready_gap_store(question_id, reentry_id, gap_type=G.GAP_TYPE_IMPL):
    """Gap store with only the governed pre-rerun evidence applied."""
    store = G.build_store()
    wid = f"GWI-{question_id}-IMPL" if gap_type == G.GAP_TYPE_IMPL else None
    if wid is None:
        wid = G.work_item_for_question(store, question_id)["gap_work_item_id"]
    store = G.apply_resolution_evidence(
        store, wid, sorted(G.PRE_RERUN_EVIDENCE[gap_type]))
    return G.mark_reentry_ready(store, wid, reentry_id), wid


def _request(state, question_id, *, reentry_id, trigger, gap_store,
             evidence_fingerprint=None, **overrides):
    item = G.work_item_for_question(gap_store, question_id)
    request = S.build_reentry_request(
        reentry_id=reentry_id,
        question_id=question_id,
        reason="focused re-entry test",
        trigger_type=trigger,
        evidence_epoch="CURRENT",
        evidence_fingerprint=evidence_fingerprint
        or S._fingerprint({"test": reentry_id}),
        gap_work_item_id=item["gap_work_item_id"],
        requested_at=NOW,
        state=state)
    return replace(request, **overrides) if overrides else request


def _authorized(state, question_id, reentry_id, trigger, gap_type):
    gap_store, _ = _ready_gap_store(question_id, reentry_id, gap_type)
    request = _request(state, question_id, reentry_id=reentry_id,
                       trigger=trigger, gap_store=gap_store)
    return S.authorize_reentry(state, request, now=NOW,
                               gap_store=gap_store), gap_store


# --- 1-3: bootstrap ---------------------------------------------------------
def test_01_bootstrap_creates_70_v1_certification_histories(state):
    assert len(state["question_certifications"]) == 70
    for rows in state["question_certifications"].values():
        assert len(rows) == 1
        assert rows[0]["certification_version"] == 1
        assert rows[0]["certification_status"] == "CURRENT"
        assert rows[0]["supersedes_certification_id"] is None
        assert rows[0]["reentry_id"] is None
    assert state["counts"]["certification_superseded"] == 0


def test_02_bootstrap_creates_70_v1_assured_findings(state):
    assert len(state["assured_findings"]) == 70
    for rows in state["assured_findings"].values():
        assert len(rows) == 1
        assert rows[0]["finding_version"] == 1
        assert rows[0]["finding_status"] == "CURRENT"
        assert rows[0]["supersedes"] is None
    assert state["counts"]["finding_superseded"] == 0


def test_03_bootstrap_changes_zero_scientific_states(state,
                                                     baseline_certification):
    baseline = {row["question_id"]: row["scientific_state"]
                for row in baseline_certification["certifications"]}
    assert state["baseline"]["scientific_states"] == baseline
    projection = S.effective_current_state(state)
    assert {row["question_id"]: row["scientific_state"]
            for row in projection["questions"]} == baseline
    assert state["baseline"]["certification_fingerprint"] == (
        A.EXPECTED_CERTIFICATION_FINGERPRINT)


# --- 4-5: uniqueness --------------------------------------------------------
def test_04_exactly_one_current_finding_per_question(state):
    for qid in S.QUESTION_IDS:
        rows = state["assured_findings"][qid]
        current = [r for r in rows if r["finding_status"] == "CURRENT"]
        assert len(current) == 1, qid
        A.validate_finding_history(qid, rows)


def test_05_exactly_one_current_certification_per_question(state):
    for qid in S.QUESTION_IDS:
        rows = state["question_certifications"][qid]
        current = [r for r in rows if r["certification_status"] == "CURRENT"]
        assert len(current) == 1, qid
        assert S.current_certification(state, qid)["certification_version"] == 1


# --- 6-8: scoped execution --------------------------------------------------
def test_06_r1_can_receive_a_scoped_reentry_request(state):
    authorized, _ = _authorized(
        state, "R1", "RE-T06", S.TRIGGER_IMPLEMENTATION_REPAIR,
        G.GAP_TYPE_IMPL)
    assert len(authorized["reentry_events"]) == 1
    event = authorized["reentry_events"][0]
    assert event["authorization_state"] == S.AUTH_AUTHORIZED
    assert event["scope"] == ["R1"]
    assert S.plan_scoped_run(authorized, "RE-T06") == ["R1"]


def test_07_r1_scoped_execution_does_not_execute_unrelated_questions(state):
    authorized, _ = _authorized(
        state, "R1", "RE-T07", S.TRIGGER_IMPLEMENTATION_REPAIR,
        G.GAP_TYPE_IMPL)
    seen: list[str] = []

    def scoped_runner(scope):
        seen.extend(scope)
        return S._fixture_runner(scope)(scope)

    result = S.execute_scoped_run(authorized, "RE-T07",
                                  runner=scoped_runner, now=NOW)
    assert seen == ["R1"]
    scientific = result["reentry_events"][0]["scientific_result"]
    assert scientific["executed_question_ids"] == ["R1"]
    assert scientific["unrelated_questions_executed"] == 0
    for qid in S.QUESTION_IDS:
        if qid == "R1":
            continue
        assert len(result["question_certifications"][qid]) == 1
        assert len(result["assured_findings"][qid]) == 1


def test_08_multiple_scoped_ids_execute_only_the_dependency_set(state):
    # R1 and R2 both have governed runners, so a two-question scoped run is
    # legal.  The scope widens only to the explicitly requested set.
    authorized, _ = _authorized(
        state, "R1", "RE-T08", S.TRIGGER_IMPLEMENTATION_REPAIR,
        G.GAP_TYPE_IMPL)
    seen: list[str] = []

    def scoped_runner(scope):
        seen.extend(scope)
        return S._fixture_runner(scope)(scope)

    result = S.execute_scoped_run(authorized, "RE-T08", question_ids=["R2"],
                                  runner=scoped_runner, now=NOW)
    assert seen == ["R1", "R2"]
    scientific = result["reentry_events"][0]["scientific_result"]
    assert sorted(scientific["executed_question_ids"]) == ["R1", "R2"]
    assert set(scientific["rows"]) == {"R1", "R2"}
    # Every other question remains untouched at V1.
    for qid in S.QUESTION_IDS:
        if qid in {"R1", "R2"}:
            continue
        assert len(result["question_certifications"][qid]) == 1


# --- 9-11: fail-closed authorization ---------------------------------------
def test_09_missing_governed_trigger_fails_closed(state):
    gap_store, _ = _ready_gap_store("R1", "RE-T09", G.GAP_TYPE_IMPL)
    request = _request(state, "R1", reentry_id="RE-T09",
                       trigger="BECAUSE_I_SAID_SO", gap_store=gap_store)
    with pytest.raises(S.ReentryError, match="TRIGGER_NOT_GOVERNED"):
        S.authorize_reentry(state, request, now=NOW, gap_store=gap_store)
    assert state["counts"]["reentry_events"] == 0


def test_10_unresolved_gap_cannot_authorize_reentry(state):
    unresolved = G.build_store()
    request = _request(state, "R1", reentry_id="RE-T10",
                       trigger=S.TRIGGER_IMPLEMENTATION_REPAIR,
                       gap_store=unresolved)
    with pytest.raises(S.ReentryError, match="GAP_NOT_REENTRY_READY"):
        S.authorize_reentry(state, request, now=NOW, gap_store=unresolved)
    assert G.get_work_item(unresolved, "GWI-R1-IMPL")["status"] == "OPEN"


def test_11_unresolved_dependency_fails_closed(state):
    gap_store, _ = _ready_gap_store("G3", "RE-T11", G.GAP_TYPE_IMPL)
    request = _request(state, "G3", reentry_id="RE-T11",
                       trigger=S.TRIGGER_IMPLEMENTATION_REPAIR,
                       gap_store=gap_store)
    assert S.dependency_state_for(state, "G3")["L6"]["satisfied"] is False
    with pytest.raises(S.ReentryError, match="DEPENDENCY_UNRESOLVED:L6"):
        S.authorize_reentry(state, request, now=NOW, gap_store=gap_store)


def _to_assurance(state, reentry_id, question_id, gap_type=G.GAP_TYPE_IMPL,
                  trigger=S.TRIGGER_IMPLEMENTATION_REPAIR,
                  assurance_status="VERIFIED",
                  scientific_state="INSUFFICIENT_DATA"):
    """Drive a governed re-entry up to (but not past) publication."""
    authorized, gap_store = _authorized(
        state, question_id, reentry_id, trigger, gap_type)
    executed = S.execute_scoped_run(
        authorized, reentry_id, runner=S._fixture_runner([question_id]),
        now=NOW)
    certification = S._fixture_assurance(
        [question_id], executed, reentry_id,
        scientific_state=scientific_state, assurance_status=assurance_status)
    recorded = S.record_assurance(
        executed, reentry_id, certification=certification, now=NOW)
    return recorded, gap_store, certification


# --- 12-13: no silent recertification ---------------------------------------
def test_12_successful_scientific_result_alone_cannot_publish_v2(state):
    authorized, _ = _authorized(
        state, "R1", "RE-T12", S.TRIGGER_IMPLEMENTATION_REPAIR,
        G.GAP_TYPE_IMPL)
    executed = S.execute_scoped_run(
        authorized, "RE-T12", runner=S._fixture_runner(["R1"]), now=NOW)
    assert executed["reentry_events"][0]["authorization_state"] == (
        S.AUTH_RESULT_RECORDED)
    with pytest.raises(S.ReentryError, match="ASSURANCE_NOT_RECORDED"):
        S.publish_new_version(executed, "RE-T12", now=NOW)
    assert len(executed["assured_findings"]["R1"]) == 1
    assert S.current_finding(executed, "R1")["finding_version"] == 1


def test_13_assurance_verified_is_required_before_v2_publication(state):
    recorded, _, _ = _to_assurance(state, "RE-T13", "R1",
                                    assurance_status="FAILED")
    assert recorded["reentry_events"][0]["assurance"][
        "assurance_status"] == "FAILED"
    with pytest.raises(S.ReentryError, match="ASSURANCE_NOT_VERIFIED"):
        S.publish_new_version(recorded, "RE-T13", now=NOW)
    assert S.current_finding(recorded, "R1")["finding_version"] == 1
    recorded, _, certification = _to_assurance(
        state, "RE-T13V", "R1", assurance_status="VERIFIED")
    published = S.publish_new_version(recorded, "RE-T13V", now=NOW)
    assert S.current_finding(published, "R1")["finding_version"] == 2
    assert certification["assurance_status"] == "VERIFIED"


# --- 14-17: supersession ----------------------------------------------------
def test_14_certification_v2_supersedes_certification_v1(state):
    recorded, _, _ = _to_assurance(state, "RE-T14", "R1")
    published = S.publish_new_version(recorded, "RE-T14", now=NOW)
    history = S.certification_history(published, "R1")
    assert [row["certification_version"] for row in history] == [1, 2]
    assert history[0]["certification_status"] == "SUPERSEDED"
    assert history[1]["certification_status"] == "CURRENT"
    assert history[1]["supersedes_certification_id"] == history[0][
        "certification_id"]
    assert history[0]["superseded_by"] == history[1]["certification_id"]


def test_15_finding_v2_supersedes_finding_v1(state):
    recorded, _, _ = _to_assurance(state, "RE-T15", "R1")
    published = S.publish_new_version(recorded, "RE-T15", now=NOW)
    history = S.finding_history(published, "R1")
    assert [row["finding_version"] for row in history] == [1, 2]
    assert history[1]["supersedes"] == history[0]["finding_id"]
    assert history[0]["superseded_by"] == history[1]["finding_id"]
    assert history[0]["finding_status"] == "SUPERSEDED"
    assert len(published["supersession_events"]) == 1


def test_16_v1_remains_queryable_and_immutable(state):
    baseline_content = {
        qid: rows[0]["content_fingerprint"]
        for qid, rows in state["assured_findings"].items()}
    baseline_certification = {
        qid: rows[0]["content_fingerprint"]
        for qid, rows in state["question_certifications"].items()}
    baseline_states = dict(state["baseline"]["scientific_states"])
    recorded, _, _ = _to_assurance(state, "RE-T16", "R1")
    published = S.publish_new_version(recorded, "RE-T16", now=NOW)
    v1_finding = S.finding_history(published, "R1")[0]
    assert v1_finding["finding_version"] == 1
    assert v1_finding["finding_status"] == "SUPERSEDED"
    assert v1_finding["scientific_state"] == baseline_states["R1"]
    assert v1_finding["content_fingerprint"] == baseline_content["R1"]
    assert (S.certification_history(published, "R1")[0]["content_fingerprint"]
            == baseline_certification["R1"])


def test_17_v2_becomes_unique_current_finding(state):
    recorded, _, _ = _to_assurance(state, "RE-T17", "R1")
    published = S.publish_new_version(recorded, "RE-T17", now=NOW)
    current = [row for row in published["assured_findings"]["R1"]
               if row["finding_status"] == "CURRENT"]
    assert len(current) == 1
    assert current[0]["finding_id"] == "R1:v2"
    assert S.current_finding(published, "R1")["finding_id"] == "R1:v2"
    assert [row for row in published["question_certifications"]["R1"]
            if row["certification_status"] == "CURRENT"][0][
                "certification_version"] == 2


# --- 18-20: effective current-state projection ------------------------------
def test_18_current_state_projection_remains_exactly_70_questions(state):
    recorded, _, _ = _to_assurance(state, "RE-T18", "R1")
    published = S.publish_new_version(recorded, "RE-T18", now=NOW)
    projection = S.effective_current_state(published)
    assert projection["question_count"] == 70
    assert {row["question_id"] for row in projection["questions"]} == set(
        S.QUESTION_IDS)
    assert projection["certification_version_counts"] == {"1": 69, "2": 1}
    assert projection["at_version_1"] == 69 and projection["at_version_2"] == 1


def test_19_only_changed_question_moves_to_v2(state):
    recorded, _, _ = _to_assurance(state, "RE-T19", "R1")
    published = S.publish_new_version(recorded, "RE-T19", now=NOW)
    projection = S.effective_current_state(published)
    at_v2 = [row["question_id"] for row in projection["questions"]
             if row["certification_version"] == 2]
    assert at_v2 == ["R1"]
    assert len(published["reentry_events"]) == 1


def test_20_unaffected_69_remain_v1(state):
    recorded, _, _ = _to_assurance(state, "RE-T20", "R1")
    published = S.publish_new_version(recorded, "RE-T20", now=NOW)
    projection = S.effective_current_state(published)
    unchanged = [row for row in projection["questions"]
                 if row["question_id"] != "R1"]
    assert len(unchanged) == 69
    assert all(row["certification_version"] == 1 for row in unchanged)
    assert all(row["finding_version"] == 1 for row in unchanged)
    for row in unchanged:
        assert (published["assured_findings"][row["question_id"]][0][
            "content_fingerprint"] == state["assured_findings"][
            row["question_id"]][0]["content_fingerprint"])


# --- 21-25: corruption fails closed -----------------------------------------
def test_21_duplicate_current_finding_fails_closed(state):
    recorded, _, _ = _to_assurance(state, "RE-T21", "R1")
    published = S.publish_new_version(recorded, "RE-T21", now=NOW)
    corrupt = deepcopy(published)
    clone = deepcopy(corrupt["assured_findings"]["R1"][1])
    clone["finding_version"] = 3
    clone["finding_id"] = "R1:v3"
    clone["supersedes"] = "R1:v2"
    clone["finding_status"] = "CURRENT"
    corrupt["assured_findings"]["R1"].append(clone)
    with pytest.raises(A.AssuredFindingsError, match="DUPLICATE_CURRENT_FINDING"):
        S.validate_state(corrupt)


def test_22_duplicate_current_certification_fails_closed(state):
    recorded, _, _ = _to_assurance(state, "RE-T22", "R1")
    published = S.publish_new_version(recorded, "RE-T22", now=NOW)
    corrupt = deepcopy(published)
    corrupt["question_certifications"]["R1"][1]["certification_status"] = (
        "CURRENT")
    corrupt["question_certifications"]["R1"][0]["certification_status"] = (
        "CURRENT")
    with pytest.raises(S.ReentryError,
                       match="DUPLICATE_OR_MISSING_CURRENT_CERTIFICATION"):
        S.validate_state(corrupt)


def test_23_cyclic_supersession_fails_closed(state):
    recorded, _, _ = _to_assurance(state, "RE-T23", "R1")
    published = S.publish_new_version(recorded, "RE-T23", now=NOW)
    corrupt = deepcopy(published)
    corrupt["assured_findings"]["R1"][0]["superseded_by"] = "R1:v1"
    with pytest.raises(A.AssuredFindingsError, match="CYCLIC_SUPERSSESSION"):
        S.validate_state(corrupt)


def test_24_missing_predecessor_fails_closed(state):
    recorded, _, _ = _to_assurance(state, "RE-T24", "R1")
    published = S.publish_new_version(recorded, "RE-T24", now=NOW)
    corrupt = deepcopy(published)
    corrupt["assured_findings"]["R1"][1]["supersedes"] = "R1:v9"
    with pytest.raises(A.AssuredFindingsError, match="PREDECESSOR_MISSING"):
        S.validate_state(corrupt)


def test_25_version_regression_fails_closed(state):
    recorded, _, _ = _to_assurance(state, "RE-T25", "R1")
    published = S.publish_new_version(recorded, "RE-T25", now=NOW)
    corrupt = deepcopy(published)
    corrupt["assured_findings"]["R1"].append(
        deepcopy(corrupt["assured_findings"]["R1"][1]))
    with pytest.raises(A.AssuredFindingsError, match="FINDING_VERSION_REGRESSION"):
        S.validate_state(corrupt)


# --- 26-29: staleness and fingerprint binding -------------------------------
def test_26_stale_certification_cannot_overwrite_newer_certification(state):
    recorded, _, _ = _to_assurance(state, "RE-T26", "R1")
    published = S.publish_new_version(recorded, "RE-T26", now=NOW)
    gap_store, _ = _ready_gap_store("R1", "RE-T26B", G.GAP_TYPE_IMPL)
    # This second request is built from the published state, so it pins V2.
    fresh = _request(published, "R1", reentry_id="RE-T26B",
                     trigger=S.TRIGGER_IMPLEMENTATION_REPAIR,
                     gap_store=gap_store)
    # Forge a stale request that still claims the V1 certification.
    stale = replace(fresh, previous_certification_fingerprint=(
        state["question_certifications"]["R1"][0]["certification_fingerprint"]))
    with pytest.raises(S.ReentryError, match="PREVIOUS_CERTIFICATION"):
        S.authorize_reentry(published, stale, now=NOW, gap_store=gap_store)
    # A correctly pinned second re-entry still works and yields V3.
    authorized = S.authorize_reentry(published, fresh, now=NOW,
                                     gap_store=gap_store)
    executed = S.execute_scoped_run(
        authorized, "RE-T26B", runner=S._fixture_runner(["R1"]), now=NOW)
    certification = S._fixture_assurance(
        ["R1"], executed, "RE-T26B", scientific_state="COMPLETE")
    recorded = S.record_assurance(
        executed, "RE-T26B", certification=certification, now=NOW)
    published_v3 = S.publish_new_version(recorded, "RE-T26B", now=NOW)
    assert S.current_certification(
        published_v3, "R1")["certification_version"] == 3


def test_27_mismatched_result_fingerprint_fails_closed(state):
    authorized, _ = _authorized(
        state, "R1", "RE-T27", S.TRIGGER_IMPLEMENTATION_REPAIR,
        G.GAP_TYPE_IMPL)
    executed = S.execute_scoped_run(
        authorized, "RE-T27", runner=S._fixture_runner(["R1"]), now=NOW)
    certification = S._fixture_assurance(["R1"], executed, "RE-T27")
    certification["certifications"][0]["result_fingerprint"] = "0" * 64
    # Re-fingerprint so the semantic binding check is what fails, not the
    # self-consistency of the material.
    certification["certification_fingerprint"] = S._fingerprint({
        key: value for key, value in certification.items()
        if key != "certification_fingerprint"})
    with pytest.raises(S.ReentryError, match="RESULT_FINGERPRINT_MISMATCH"):
        S.record_assurance(executed, "RE-T27", certification=certification,
                           now=NOW)


def test_28_mismatched_contract_fingerprint_fails_closed(state):
    authorized, _ = _authorized(
        state, "R1", "RE-T28", S.TRIGGER_IMPLEMENTATION_REPAIR,
        G.GAP_TYPE_IMPL)
    executed = S.execute_scoped_run(
        authorized, "RE-T28", runner=S._fixture_runner(["R1"]), now=NOW)
    certification = S._fixture_assurance(["R1"], executed, "RE-T28")
    certification["question_contract_fingerprint"] = "0" * 64
    certification["certification_fingerprint"] = S._fingerprint({
        key: value for key, value in certification.items()
        if key != "certification_fingerprint"})
    with pytest.raises(S.ReentryError, match="ASSURANCE_CONTRACT_MISMATCH"):
        S.record_assurance(executed, "RE-T28", certification=certification,
                           now=NOW)


def test_29_different_evidence_epoch_creates_new_version_not_mutation(state):
    recorded, _, _ = _to_assurance(state, "RE-T29", "R1")
    published = S.publish_new_version(recorded, "RE-T29", now=NOW)
    v1_content = S.finding_history(published, "R1")[0]["content_fingerprint"]
    gap_store, _ = _ready_gap_store("R1", "RE-T29B", G.GAP_TYPE_IMPL)
    request = _request(published, "R1", reentry_id="RE-T29B",
                       trigger=S.TRIGGER_NEW_EVIDENCE_EPOCH,
                       gap_store=gap_store,
                       evidence_fingerprint=S._fingerprint({"epoch": 2}))
    authorized = S.authorize_reentry(published, request, now=NOW,
                                     gap_store=gap_store)
    executed = S.execute_scoped_run(
        authorized, "RE-T29B", runner=S._fixture_runner(["R1"]), now=NOW)
    certification = S._fixture_assurance(
        ["R1"], executed, "RE-T29B", scientific_state="COMPLETE")
    recorded = S.record_assurance(
        executed, "RE-T29B", certification=certification, now=NOW)
    published_v3 = S.publish_new_version(recorded, "RE-T29B", now=NOW)
    history = S.finding_history(published_v3, "R1")
    assert [row["finding_version"] for row in history] == [1, 2, 3]
    assert history[0]["content_fingerprint"] == v1_content
    assert history[0]["scientific_state"] == "IMPLEMENTATION_BLOCKED"
    assert history[1]["scientific_state"] == "INSUFFICIENT_DATA"
    assert history[2]["scientific_state"] == "COMPLETE"
    assert S.current_finding(published_v3, "R1")["finding_version"] == 3


# --- 30-31: trigger classes --------------------------------------------------
def test_30_data_gap_reentry_trigger_is_supported(state):
    question_id = "E5"
    gap_store, wid = _ready_gap_store(question_id, "RE-T30", G.GAP_TYPE_DATA)
    assert G.get_work_item(gap_store, wid)["gap_type"] == G.GAP_TYPE_DATA
    authorized, _ = _authorized(
        state, question_id, "RE-T30", S.TRIGGER_DATA_THRESHOLD_REACHED,
        G.GAP_TYPE_DATA)
    event = authorized["reentry_events"][0]
    assert event["trigger_type"] == S.TRIGGER_DATA_THRESHOLD_REACHED
    assert event["gap_type"] == G.GAP_TYPE_DATA
    assert S.TRIGGER_SCHEMA_COLLECTION_RECOVERED in S.ALLOWED_TRIGGERS


def test_31_implementation_gap_reentry_trigger_is_supported(state):
    assert S.TRIGGER_IMPLEMENTATION_REPAIR in S.ALLOWED_TRIGGERS
    authorized, _ = _authorized(
        state, "R1", "RE-T31", S.TRIGGER_IMPLEMENTATION_REPAIR,
        G.GAP_TYPE_IMPL)
    event = authorized["reentry_events"][0]
    assert event["trigger_type"] == S.TRIGGER_IMPLEMENTATION_REPAIR
    assert event["gap_type"] == G.GAP_TYPE_IMPL
    assert event["work_item_id"] == "GWI-R1-IMPL"
    # Exactly the six governed classes; no free-text execution authority.
    assert set(S.ALLOWED_TRIGGERS) == {
        "IMPLEMENTATION_REPAIR", "DATA_THRESHOLD_REACHED",
        "SCHEMA_COLLECTION_RECOVERED", "DEPENDENCY_RESOLVED",
        "GOVERNED_METHOD_REPAIR", "NEW_EVIDENCE_EPOCH"}


# --- 32-33: dependency semantics ---------------------------------------------
def test_32_l6_to_g3_dependency_semantics_are_representable(state):
    edges = state["dependency_edges"]
    assert [(e["prerequisite_question_id"], e["dependent_question_id"])
            for e in edges] == [("L6", "G3")]
    assert edges[0]["source"].startswith("gap_governance:")
    snapshot = S.dependency_state_for(state, "G3")
    assert "L6" in snapshot
    assert snapshot["L6"]["requirement"]["required_assurance_status"] == (
        "VERIFIED")
    assert snapshot["L6"]["requirement"]["disallowed_scientific_states"] == [
        "IMPLEMENTATION_BLOCKED"]


def test_33_g3_cannot_reenter_before_required_l6_state(state):
    assert S.current_finding(state, "L6")["scientific_state"] == (
        "IMPLEMENTATION_BLOCKED")
    assert S.dependency_state_for(state, "G3")["L6"]["satisfied"] is False
    gap_store, _ = _ready_gap_store("G3", "RE-T33", G.GAP_TYPE_IMPL)
    request = _request(state, "G3", reentry_id="RE-T33",
                       trigger=S.TRIGGER_IMPLEMENTATION_REPAIR,
                       gap_store=gap_store)
    with pytest.raises(S.ReentryError, match="DEPENDENCY_UNRESOLVED:L6"):
        S.authorize_reentry(state, request, now=NOW, gap_store=gap_store)
    # G3 is never executed automatically as a side effect.
    assert state["counts"]["reentry_events"] == 0
    # Once L6 holds a non-blocked CURRENT certification the gate re-opens.
    lifted = deepcopy(state)
    for key, value in (("scientific_state", "COMPLETE"),
                       ("certification_version", 2)):
        lifted["assured_findings"]["L6"][0][key] = value
        lifted["question_certifications"]["L6"][0][key] = value
    assert S.dependency_state_for(lifted, "G3")["L6"]["satisfied"] is True


# --- 34: gap lifecycle integration ------------------------------------------
def test_34_gap_cannot_resolve_without_full_reentry_linkage(state):
    recorded, gap_store, _ = _to_assurance(state, "RE-T34", "R1")
    with pytest.raises(S.ReentryError, match="NOT_PUBLISHED"):
        S.resolve_gap_via_reentry(gap_store, recorded, "RE-T34")
    assert G.get_work_item(gap_store, "GWI-R1-IMPL")["status"] == (
        G.STATUS_VALIDATION_REQUIRED)
    published = S.publish_new_version(recorded, "RE-T34", now=NOW)
    linkage = S.gap_resolution_linkage(published, "RE-T34")
    assert set(linkage) == set(G.REENTRY_LINKAGE_KEYS)
    assert linkage["finding_id"] == "R1:v2"
    incomplete = {k: v for k, v in linkage.items()
                  if k != "certification_id"}
    with pytest.raises(G.GapGovernanceError, match="REENTRY_LINKAGE_INCOMPLETE"):
        G.resolve_via_reentry(gap_store, "GWI-R1-IMPL", incomplete)
    assert G.get_work_item(gap_store, "GWI-R1-IMPL")["status"] != (
        G.STATUS_RESOLVED)
    resolved = S.resolve_gap_via_reentry(gap_store, published, "RE-T34")
    item = G.get_work_item(resolved, "GWI-R1-IMPL")
    assert item["status"] == G.STATUS_RESOLVED
    assert item["assured_finding_version"] == 2
    assert item["resolution_linkage"][0]["finding_id"] == "R1:v2"


# --- 35-36: no live reads, no full rerun -------------------------------------
def test_35_reentry_does_not_read_s3_or_live_data(state):
    for path in REENTRY_MODULES:
        source = path.read_text(encoding="utf-8")
        for token in ("boto3", "get_default_source", "read_dataset(",
                      "ingest_completed_shadow_trades",
                      "reconstruct_completed_shadow_trades"):
            assert token not in source, f"{path} references {token}"
    assert state["live_or_s3_reads"] is False
    recorded, _, _ = _to_assurance(state, "RE-T35", "R1")
    assert recorded["reentry_events"][0]["scientific_result"][
        "live_or_s3_reads"] is False


def test_36_no_full_70_question_rerun_is_required_for_one_question(state):
    authorized, _ = _authorized(
        state, "R1", "RE-T36", S.TRIGGER_IMPLEMENTATION_REPAIR,
        G.GAP_TYPE_IMPL)
    calls: list[list[str]] = []

    def scoped_runner(scope):
        calls.append(list(scope))
        return S._fixture_runner(scope)(scope)

    result = S.execute_scoped_run(authorized, "RE-T36",
                                  runner=scoped_runner, now=NOW)
    assert calls == [["R1"]]
    assert result["reentry_events"][0]["scientific_result"][
        "unrelated_questions_executed"] == 0
    assert S.effective_current_state(result)["question_count"] == 70


# --- 37-40: baseline preservation and consumption ---------------------------
def test_37_old_global_v1_certification_remains_unchanged(state):
    certification = json.loads(BASELINE_CERTIFICATION.read_text(encoding="utf-8"))
    assert certification["certification_fingerprint"] == (
        A.EXPECTED_CERTIFICATION_FINGERPRINT)
    assert certification["question_count"] == 70
    recorded, _, _ = _to_assurance(state, "RE-T37", "R1")
    published = S.publish_new_version(recorded, "RE-T37", now=NOW)
    after = json.loads(BASELINE_CERTIFICATION.read_text(encoding="utf-8"))
    assert after == certification
    assert after["certification_fingerprint"] == (
        A.EXPECTED_CERTIFICATION_FINGERPRINT)
    assert S.validate_state(published)["baseline"][
        "certification_fingerprint"] == A.EXPECTED_CERTIFICATION_FINGERPRINT


def test_38_effective_current_state_fingerprint_is_deterministic(state):
    first = S.effective_current_state(state)
    second = S.effective_current_state(deepcopy(state))
    assert first["projection_fingerprint"] == second["projection_fingerprint"]
    recorded, _, _ = _to_assurance(state, "RE-T38", "R1")
    published = S.publish_new_version(recorded, "RE-T38", now=NOW)
    third = S.effective_current_state(published)
    fourth = S.effective_current_state(deepcopy(published))
    assert third["projection_fingerprint"] == fourth["projection_fingerprint"]
    assert third["projection_fingerprint"] != first["projection_fingerprint"]


def test_39_q71_plus_remains_not_started(state):
    assert state["q71_started"] is False
    assert S.effective_current_state(state)["q71_started"] is False
    with pytest.raises(G.GapGovernanceError, match="Q71_NOT_STARTED"):
        G.create_q71_gap(["p_success"], ["shadow_trades"])
    with pytest.raises(S.ReentryError, match="UNKNOWN_QUESTION"):
        S.authorize_reentry(
            state,
            S.build_reentry_request(
                reentry_id="RE-T39", question_id="Q71", reason="n/a",
                trigger_type=S.TRIGGER_IMPLEMENTATION_REPAIR,
                evidence_epoch="CURRENT", evidence_fingerprint="0" * 64,
                gap_work_item_id="GWI-Q71-IMPL", requested_at=NOW,
                state=state),
            now=NOW, gap_store=G.build_store())


def test_40_truth_consumption_uses_latest_current_finding_only(state):
    recorded, _, _ = _to_assurance(state, "RE-T40", "R1")
    published = S.publish_new_version(recorded, "RE-T40", now=NOW)
    current = S.current_finding(published, "R1")
    assert current["finding_id"] == "R1:v2"
    # V1 was IMPLEMENTATION_BLOCKED and must never be consumable.
    with pytest.raises(A.AssuredFindingsError):
        A.consume_scientific_truth(S.finding_history(published, "R1")[0])
    # V2 is INSUFFICIENT_DATA: admitted, verified, but never market truth.
    decision = A.decide("R1", current["assurance_status"],
                        current["scientific_state"])
    assert decision.admitted is True
    assert decision.scientific_truth_consumable is False
    assert A.decide("R1", "VERIFIED", "COMPLETE").scientific_truth_consumable
    history = S.scientific_result_history(published, "R1")
    assert [v["finding_version"] for v in history["versions"]] == [1, 2]
    # The evidence epoch is unchanged; the new knowledge is the new state and
    # the new result fingerprint.
    assert history["changes"][0]["changed_fields"] == [
        "evidence_fingerprint", "scientific_result_fingerprint",
        "scientific_state"]


# --- 41-44: durable store, demonstration and contract evolution --------------
def test_41_durable_store_exists_and_validates():
    if not REENTRY_STATE.is_file():
        pytest.skip("durable state store not bootstrapped yet")
    state = S.load_state(REENTRY_STATE)
    assert state["counts"]["questions"] == 70
    assert state["q71_started"] is False
    assert state["counts"]["reentry_events"] == len(state["reentry_events"])
    assert state["counts"]["finding_superseded"] == len(
        state["supersession_events"])
    assert all(event["authorization_state"] == S.AUTH_PUBLISHED
               for event in state["reentry_events"])


def test_42_fixture_demonstration_proves_single_question_v1_to_v2():
    outcome = S.run_reentry_demo()
    demo = outcome["demonstration"]
    assert demo["old_certification_version"] == 1
    assert demo["new_certification_version"] == 2
    assert demo["old_finding_version"] == 1
    assert demo["new_finding_version"] == 2
    assert demo["old_scientific_state"] == "IMPLEMENTATION_BLOCKED"
    assert demo["effective_question_count"] == 70
    assert demo["unaffected_questions_changed"] == 0
    assert demo["v1_content_preserved"] is True
    assert demo["production_state_persisted"] is False
    assert demo["gap_status"] == G.STATUS_RESOLVED
    assert demo["supersession_valid"] is True


def test_43_contract_evolution_requires_explicit_record():
    base = S.bootstrap_state()
    gap_store, _ = _ready_gap_store("R1", "RE-T43", G.GAP_TYPE_IMPL)
    request = _request(base, "R1", reentry_id="RE-T43",
                       trigger=S.TRIGGER_IMPLEMENTATION_REPAIR,
                       gap_store=gap_store,
                       question_contract_fingerprint="0" * 64)
    with pytest.raises(S.ReentryError, match="QUESTION_CONTRACT_FINGERPRINT"):
        S.authorize_reentry(base, request, now=NOW, gap_store=gap_store)
    evolved = replace(request, contract_evolution={
        "from_version": 1, "to_version": 2, "semantic_change": False,
        "governance_ref": "STAGE4-L3-L6-L7-REGISTRY-AMENDMENT"})
    authorized = S.authorize_reentry(base, evolved, now=NOW,
                                     gap_store=gap_store)
    assert authorized["reentry_events"][0]["contract_evolution"][
        "to_version"] == 2


def test_44_result_history_explains_what_changed_and_why(state):
    recorded, _, _ = _to_assurance(state, "RE-T44", "R1")
    published = S.publish_new_version(recorded, "RE-T44", now=NOW)
    event = published["reentry_events"][0]
    history = S.scientific_result_history(published, "R1")
    assert history["versions"][1]["reentry_id"] == "RE-T44"
    assert history["changes"][0]["from_finding_id"] == "R1:v1"
    assert history["changes"][0]["to_finding_id"] == "R1:v2"
    assert event["trigger_type"] == S.TRIGGER_IMPLEMENTATION_REPAIR
    assert event["reason"]
    supersession = published["supersession_events"][0]
    assert supersession["append_only"] is True
    assert supersession["superseded_finding_id"] == "R1:v1"
    assert supersession["superseding_finding_id"] == "R1:v2"


# --- 45: a pure no-op re-entry cannot publish a new version -------------------
def test_45_pure_noop_reentry_cannot_publish_a_version(state):
    recorded, _, _ = _to_assurance(state, "RE-T45", "R1")
    published = S.publish_new_version(recorded, "RE-T45", now=NOW)
    v2 = S.finding_history(published, "R1")[1]
    # Re-enter once more and reproduce exactly the same certified knowledge:
    # same scientific state, same result fingerprint, same evidence epoch.
    gap_store, _ = _ready_gap_store("R1", "RE-T45B", G.GAP_TYPE_IMPL)
    request = _request(
        published, "R1", reentry_id="RE-T45B",
        trigger=S.TRIGGER_IMPLEMENTATION_REPAIR, gap_store=gap_store,
        evidence_fingerprint=v2["evidence_fingerprint"])
    authorized = S.authorize_reentry(published, request, now=NOW,
                                     gap_store=gap_store)
    executed = S.execute_scoped_run(
        authorized, "RE-T45B", runner=S._fixture_runner(["R1"]), now=NOW)
    assert (executed["reentry_events"][0]["scientific_result"]["rows"]["R1"][
        "result_fingerprint"] == v2["scientific_result_fingerprint"])
    certification = S._fixture_assurance(
        ["R1"], executed, "RE-T45B",
        scientific_state=v2["scientific_state"])
    recorded = S.record_assurance(
        executed, "RE-T45B", certification=certification, now=NOW)
    with pytest.raises(S.ReentryError, match="WITHOUT_NEW_EVIDENCE_OR_STATE"):
        S.publish_new_version(recorded, "RE-T45B", now=NOW)
    assert S.current_finding(published, "R1")["finding_version"] == 2







