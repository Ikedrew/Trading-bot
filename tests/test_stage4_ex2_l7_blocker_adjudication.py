"""Stage 4 EX2/L7 blocker adjudication tests.

The adjudication is exhaustive and frozen-evidence read-only: no live/S3 read,
no Q71+, no report rewriting, and no post-hoc promotion of reconstructed
evidence into authoritative historical evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_engine.control_plane import gap_governance as G
from research_engine.control_plane import scientific_reentry as S
from research_engine.control_plane import stage4_ex2_l7_blocker_adjudication as ADJ
from research_engine.control_plane.stage4_impl_ownership_labels import (
    assign_l7_labels, govern_l7_label,
)
from research_engine.control_plane.stage4_impl_population2 import (
    enforce_exact_population,
)
from research_engine.control_plane.stage4_implementation_repairs import (
    FORBIDDEN_RUNNER_N, GOVERNED_USABLE,
)

EX2_POPULATION = 8760
EX2_AUTHORITATIVE = 5916
EX2_MISSING = 2844
L7_POPULATION = 14046
BASELINE_CERTIFICATION_FINGERPRINT = (
    "b42b4bfcfa1eaf7df09cac9f88011c89362cf2ca907217e8466fff9fea3adc8b")
CANON = Path("research_engine/control_plane/gap_governance_state.json")
REENTRY_STATE = Path("research_engine/control_plane/scientific_reentry_state.json")
ADJUDICATION_JSON = Path(
    "analysis/assurance/stage4_ex2_l7_blocker_adjudication_20260929.json")
ADJUDICATION_MD = Path(
    "analysis/assurance/stage4_ex2_l7_blocker_adjudication_20260929.md")


@pytest.fixture(scope="module")
def adjudication():
    return ADJ.adjudicate()


@pytest.fixture(scope="module")
def gap_store():
    return json.loads(CANON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def reentry_state():
    return S.load_state(REENTRY_STATE)


# ---------------------------------------------------------------------------
# EX2 - ordered M5 OHLC exit path
# ---------------------------------------------------------------------------

def test_01_ex2_governed_denominator_is_8760(adjudication):
    assert adjudication["EX2"]["governed_population"] == EX2_POPULATION
    assert EX2_POPULATION == GOVERNED_USABLE["EX2"]


def test_02_ex2_authoritative_m5_path_is_5916(adjudication):
    assert adjudication["EX2"]["exact_authoritative_match"] == EX2_AUTHORITATIVE


def test_03_ex2_missing_m5_path_is_2844(adjudication):
    ex2 = adjudication["EX2"]
    assert ex2["historically_unobserved"] == EX2_MISSING
    assert ex2["implementation_repair_exclusions"] == {"MISSING_M5_PATH": EX2_MISSING}


def test_04_ex2_ambiguous_and_unexplained_are_zero(adjudication):
    ex2 = adjudication["EX2"]
    assert ex2["ambiguous"] == 0
    assert ex2["unexplained"] == 0


def test_05_ex2_accounting_conserves(adjudication):
    ex2 = adjudication["EX2"]
    assert (EX2_AUTHORITATIVE + EX2_MISSING + 0 + 0) == EX2_POPULATION
    assert (ex2["exact_authoritative_match"] + ex2["historically_unobserved"]
            + ex2["ambiguous"] + ex2["unexplained"]
            == ex2["governed_population"] == EX2_POPULATION)


def test_06_ex2_classification_is_historical_observation_gap(adjudication):
    assert adjudication["EX2"]["classification"] == "HISTORICAL_OBSERVATION_GAP"


def test_07_ex2_no_implementation_resolvable_population_remains(adjudication):
    ex2 = adjudication["EX2"]
    assert ex2["implementation_resolvable_missing"] == 0
    assert ex2["exact_match_before_implementation_repair"] == EX2_AUTHORITATIVE


def test_08_ex2_every_missing_case_has_exact_canonical_identity(adjudication):
    ex2 = adjudication["EX2"]
    # Every unmatched lifecycle resolved to a checkpoint OPEN/CLOSE pair, so
    # no lifecycle identity is missing on identity grounds.
    assert ex2["unmatched_without_checkpoint_open_close"] == 0
    assert ex2["unmatched_with_checkpoint_open_close"] == EX2_MISSING


def test_09_ex2_no_weak_key_or_ambiguous_identity_match(adjudication):
    ex2 = adjudication["EX2"]
    assert ex2["weak_shadow_id_overlap"] == 0
    assert ex2["weak_opportunity_horizon_overlap"] == 0


def test_10_ex2_bar_close_r_cannot_satisfy_ohlc_contract(adjudication):
    ex2 = adjudication["EX2"]
    # The only progression retained for the missing rows is bar/close/r.
    assert ex2["reconstructed_progression_field_shapes"] == {
        "bar,close,r": EX2_MISSING}
    assert ex2["classification"] == "HISTORICAL_OBSERVATION_GAP"


def test_11_ex2_9045_widening_path_cannot_become_authoritative():
    assert FORBIDDEN_RUNNER_N["EX2"] == 9045
    assert 9045 != GOVERNED_USABLE["EX2"]
    from research_engine.control_plane.stage4_registry_successor import (
        EVIDENCE_CONTRACT_EXTENSIONS,
    )
    extension = EVIDENCE_CONTRACT_EXTENSIONS["EX2"]
    assert extension["analytical_population"] == EX2_POPULATION
    assert extension["forbidden_runner_population"] == 9045
    assert extension["inadequate_reconstruction"] == ["bar", "close", "r"]


def test_12_ex2_no_silent_evidence_loss_and_no_synthetic_ohlc(adjudication):
    ex2 = adjudication["EX2"]
    # Conservation plus zero unexplained proves no row was silently dropped.
    assert ex2["unexplained"] == 0
    assert ex2["governed_population"] == (
        ex2["exact_authoritative_match"] + ex2["historically_unobserved"])
    # The reconstruction attempt records why it cannot be authoritative.
    assert ex2["implementation_repair_exclusions"]


def test_13_ex2_sources_and_identity_chain_are_recorded(adjudication):
    ex2 = adjudication["EX2"]
    assert ex2["authoritative_sources_searched"]
    for token in ("shadow_trade_id", "canonical_opportunity_id", "trade_horizon"):
        assert token in ex2["join_chain"]
    assert "ordered events_v1 M5 OHLC bars after entry through exit" in ex2[
        "join_chain"]


def test_14_ex2_governed_roster_is_exact_and_unique():
    rows = ADJ.governed_ex2_rows()
    assert len(rows) == EX2_POPULATION
    assert len({ADJ._identity(row) for row in rows}) == EX2_POPULATION


def test_15_ex2_roster_integrity_fails_closed_on_corruption(monkeypatch):
    monkeypatch.setattr(ADJ, "_artifacts", lambda **_: [])
    with pytest.raises(ValueError, match="EX2_GOVERNED_ROSTER_INTEGRITY_FAILURE"):
        ADJ.governed_ex2_rows()


# ---------------------------------------------------------------------------
# L7 - producer-authoritative CONTROL/CANDIDATE assignment
# ---------------------------------------------------------------------------

def test_16_l7_governed_denominator_is_14046(adjudication):
    l7 = adjudication["L7"]
    assert l7["governed_population"] == L7_POPULATION
    assert L7_POPULATION == GOVERNED_USABLE["L7"]


def test_17_l7_authoritative_control_and_candidate_are_zero(adjudication):
    l7 = adjudication["L7"]
    assert l7["producer_authoritative_CONTROL"] == 0
    assert l7["producer_authoritative_CANDIDATE"] == 0
    assert l7["other_authoritative_assignment"] == 0


def test_18_l7_ambiguous_and_unexplained_are_zero(adjudication):
    l7 = adjudication["L7"]
    assert l7["ambiguous"] == 0
    assert l7["unexplained"] == 0


def test_19_l7_accounting_conserves(adjudication):
    l7 = adjudication["L7"]
    total = (l7["producer_authoritative_CONTROL"]
             + l7["producer_authoritative_CANDIDATE"]
             + l7["other_authoritative_assignment"]
             + l7["unlabeled_historically"]
             + l7["ambiguous"] + l7["unexplained"])
    assert total == l7["governed_population"] == L7_POPULATION
    assert l7["unlabeled_historically"] == L7_POPULATION


def test_20_l7_classification_is_historical_observation_gap(adjudication):
    assert adjudication["L7"]["classification"] == "HISTORICAL_OBSERVATION_GAP"


def test_21_l7_shadow_trades_v1_cannot_satisfy_assignment_contract(adjudication):
    l7 = adjudication["L7"]
    assert l7["governed_schema_versions"] == {"shadow_trades_v1": L7_POPULATION}
    assert l7["control_candidate_tokens_in_governed_rows"] == {}
    assert l7["control_candidate_tokens_elsewhere"] == []
    from research_engine.control_plane.stage4_registry_successor import (
        EVIDENCE_CONTRACT_EXTENSIONS,
    )
    assert EVIDENCE_CONTRACT_EXTENSIONS["L7"][
        "dataset_schema_identity_is_not_an_arm"] is True


def test_22_l7_exhaustive_frozen_source_scan_was_performed(adjudication):
    searched = adjudication["L7"]["authoritative_sources_searched"]
    assert any("shadow_trades.jsonl" in item for item in searched)
    assert any("reconstruction.json" in item for item in searched)
    assert len(searched) >= 10


def _arm_row(arm, **extra):
    """Build a governed row carrying a producer-issued pre-outcome arm block.

    The arm lives in the ``experiment_arm`` block.  It is deliberately NOT in
    ``schema_version``: that field carries the record-structure identity and
    reading an arm from it is the semantic collision ROOT-05 removes.
    """
    from core.shadow.observability import assign_experiment_arm
    block = assign_experiment_arm(
        canonical_opportunity_id=extra.pop("canonical_opportunity_id",
                                           f"SYM{abs(hash(arm))%97}*1000*P"),
        trade_horizon=extra.pop("trade_horizon", "SCALP"),
        shadow_trade_id=extra.pop("shadow_trade_id", "ST-1"),
        decision_market_time_utc=extra.pop("decision_market_time_utc",
                                           1756000000),
    )
    block["experiment_arm"] = arm
    block["treatment_id"] = ("BASELINE_POLICY" if arm == "CONTROL"
                             else "ADAPTATION_CANDIDATE_POLICY")
    row = {"schema_version": "shadow_runtime_v1", "experiment_arm": block}
    row.update(extra)
    return row


def test_23_l7_chronology_and_proxy_inference_fails_closed():
    # A record-structure identity is NOT an arm and must never bind one.
    with pytest.raises(Exception, match="L7_SCHEMA_IDENTITY_PRESENTED_AS_ARM"):
        assign_l7_labels([{"schema_version": "shadow_trades_v1",
                           "experiment_arm": {"experiment_arm":
                                              "shadow_trades_v1"}}])
    # A row with no producer-issued arm block cannot be labelled at all.
    with pytest.raises(Exception, match="L7_ARM_BLOCK_MISSING_FAIL_CLOSED"):
        assign_l7_labels([{"schema_version": "shadow_trades_v1"}])
    # An arm read out of schema_version is no longer accepted.
    with pytest.raises(Exception, match="L7_ARM_BLOCK_MISSING_FAIL_CLOSED"):
        assign_l7_labels([{"schema_version": "CONTROL"}])


def test_24_l7_unknown_labels_fail_closed():
    # Every non-governed label fails closed, whether it is an unknown token or
    # a version-shaped identity. Both are rejections, never a silent default.
    for value in ("", None, "TREATMENT", "CONTROL_CANDIDATE", "control_v2"):
        with pytest.raises(Exception, match="L7_(UNKNOWN_LABEL_FAIL_CLOSED|"
                                             "SCHEMA_IDENTITY_PRESENTED_AS_ARM)"):
            assign_l7_labels([{"schema_version": "shadow_runtime_v1",
                               "experiment_arm": {"experiment_arm": value}}])


def test_25_l7_explicit_producer_labels_still_bind():
    labelled = assign_l7_labels([_arm_row("CONTROL"), _arm_row("CANDIDATE")])
    assert [row["governed_arm"] for row in labelled] == ["CONTROL", "CANDIDATE"]
    assert govern_l7_label(labelled[0]["governed_arm"]) == "CONTROL"
    # The bound provenance records the real source block and pre-outcome state.
    prov = labelled[0]["label_provenance"]
    assert prov["source_field"] == "experiment_arm.experiment_arm"
    assert prov["pre_outcome"] is True
    assert prov["assigned_at_event"] == "OPEN"


def test_26_l7_unknown_label_governance_fails_closed():
    with pytest.raises(Exception):
        govern_l7_label("TREATMENT")


def test_27_l7_governed_population_is_exact():
    with pytest.raises(Exception):
        enforce_exact_population("L7", [{"schema_version": "shadow_trades_v1"}])


def test_28_l7_runner_publishes_historical_unanswerable():
    from research_engine.experiments.adaptation_evidence import run_l7
    rows = [{"schema_version": "shadow_trades_v1",
             "canonical_opportunity_id": f"OPPORTUNITY-{index}"}
            for index in range(L7_POPULATION)]
    report = run_l7(records=rows)
    assert report["scientific_state"] == "HISTORICALLY_UNANSWERABLE"
    assert report["status"] == "BLOCKED"
    assert report["provenance"]["observation_gap_adjudication"][
        "classification"] == "HISTORICAL_OBSERVATION_GAP"


def test_29_l7_report_validation_rejects_broken_accounting():
    from research_engine.experiments.adaptation_evidence import validate_l7_report
    report = {
        "question_id": "L7",
        "report_schema_version": "l7_adaptation_evidence_v1",
        "status": "BLOCKED",
        "scientific_state": "HISTORICALLY_UNANSWERABLE",
        "provenance": {
            "label_contract": {
                "contract_version": "l7_label_contract_v2",
                "unknown_label_behaviour": "FAIL_CLOSED",
            },
            "observation_gap_adjudication": {
                "classification": "HISTORICAL_OBSERVATION_GAP",
                "governed_population": 14046,
                "producer_authoritative_CONTROL": 1,
                "producer_authoritative_CANDIDATE": 0,
                "other_authoritative_assignment": 0,
                "unlabeled_historically": 14045,
                "ambiguous": 0,
                "unexplained": 0,
                "governed_schema_versions": {"shadow_trades_v1": 14046},
            },
        },
    }
    ok, _ = validate_l7_report(report)
    assert ok is True
    broken = json.loads(json.dumps(report))
    audit = broken["provenance"]["observation_gap_adjudication"]
    audit["producer_authoritative_CONTROL"] = 0
    audit["unlabeled_historically"] = 14044
    ok, reason = validate_l7_report(broken)
    assert ok is False and "conserve" in reason


# ---------------------------------------------------------------------------
# Governance transition
# ---------------------------------------------------------------------------

def test_30_observation_gap_transitions_are_recorded(gap_store):
    transitions = gap_store.get("observation_gap_transitions", [])
    assert [item["question_id"] for item in transitions] == ["EX2", "L7"]
    for item in transitions:
        assert item["status"] == "OPEN"
        assert item["historical_backfill_possible"] is False
        assert item["future_collection_only"] is True
        assert item["missing_observable"]
        assert item["required_fields"]
        assert item["evidence_contract_consumer"]


def test_31_observation_gap_requires_exhausted_implementation(gap_store):
    with pytest.raises(G.GapGovernanceError,
                       match="OBSERVATION_GAP_BEFORE_IMPLEMENTATION_EXHAUSTED"):
        G.record_observation_gap_transition(
            G.build_store(),
            requirement=gap_store["observation_gap_transitions"][0])


def test_32_observation_gap_rejects_invented_backfill(gap_store):
    requirement = dict(gap_store["observation_gap_transitions"][0])
    requirement["question_id"] = "EX9"
    requirement["historical_backfill_possible"] = True
    with pytest.raises(G.GapGovernanceError, match="HISTORICAL_ABSENCE_NOT_PROVEN"):
        G.record_observation_gap_transition(gap_store, requirement=requirement)


def test_33_observation_gap_rejects_duplicate_question(gap_store):
    with pytest.raises(G.GapGovernanceError,
                       match="DUPLICATE_OBSERVATION_GAP_TRANSITION"):
        G.record_observation_gap_transition(
            gap_store, requirement=gap_store["observation_gap_transitions"][0])


def test_34_observation_gap_requires_complete_requirement(gap_store):
    requirement = dict(gap_store["observation_gap_transitions"][0])
    requirement["question_id"] = "EX9"
    del requirement["minimum_completeness_rule"]
    with pytest.raises(G.GapGovernanceError,
                       match="OBSERVATION_GAP_TRANSITION_INCOMPLETE"):
        G.record_observation_gap_transition(gap_store, requirement=requirement)


def test_35_observation_gap_rejects_non_open_status(gap_store):
    requirement = dict(gap_store["observation_gap_transitions"][0])
    requirement["question_id"] = "EX9"
    requirement["status"] = "CLOSED"
    with pytest.raises(G.GapGovernanceError, match="NEW_OBSERVATION_GAP_NOT_OPEN"):
        G.record_observation_gap_transition(gap_store, requirement=requirement)


def test_36_all_eight_implementation_gaps_are_resolved(gap_store):
    impl = [item for item in gap_store["work_items"]
            if item["gap_type"] == "IMPLEMENTATION_GAP"]
    assert len(impl) == 8
    assert all(item["status"] == G.STATUS_RESOLVED for item in impl)
    assert all(item["resolution_linkage"] for item in impl)


def test_37_no_false_implementation_blocked_remains(reentry_state):
    for qid in ("EX2", "L7"):
        finding = S.current_finding(reentry_state, qid)
        assert finding["scientific_state"] == "HISTORICALLY_UNANSWERABLE"


def test_38_ex2_and_l7_published_v2_through_governed_reentry(reentry_state):
    for qid, reentry_id in (("EX2", "RE-IMPL-20260929-EX2"),
                            ("L7", "RE-IMPL-20260929-L7")):
        assert S.current_certification(reentry_state, qid)[
            "certification_version"] == 2
        finding = S.current_finding(reentry_state, qid)
        assert finding["finding_version"] == 2
        assert finding["reentry_id"] == reentry_id
        assert S.scientific_result_history(reentry_state, qid)["versions"][-1][
            "scientific_result_version"] == 2


def test_39_v1_findings_and_certification_are_not_mutated(reentry_state):
    assert reentry_state["baseline"]["certification_fingerprint"] == (
        BASELINE_CERTIFICATION_FINGERPRINT)
    assert reentry_state["v1_findings_mutated"] is False
    assert reentry_state["global_certification_mutated"] is False
    projection = S.effective_current_state(reentry_state)
    assert projection["question_count"] == 70
    # Only the eight governed implementation-gap questions advanced.
    assert projection["certification_version_counts"] == {"1": 62, "2": 8}


def test_40_no_unrelated_question_version_changed(reentry_state):
    advanced = {
        qid for qid in S.QUESTION_IDS
        if S.current_certification(reentry_state, qid)["certification_version"] != 1}
    assert advanced == {"R1", "R2", "L3", "L6", "L7", "G2", "G3", "EX2"}


def test_41_superseded_v1_history_is_retained(reentry_state):
    for qid in ("EX2", "L7"):
        history = S.finding_history(reentry_state, qid)
        assert [int(row["finding_version"]) for row in history] == [1, 2]
        assert history[0]["finding_status"] == "SUPERSEDED"
        assert history[0]["superseded_by"] == f"{qid}:v2"


def test_42_no_global_70q_rerun_no_live_reads_no_q71(reentry_state):
    assert reentry_state["q71_started"] is False
    assert reentry_state["live_or_s3_reads"] is False
    for event in reentry_state["reentry_events"]:
        result = event.get("scientific_result") or {}
        assert result.get("unrelated_questions_executed") == 0
        assert result.get("live_or_s3_reads") is False
        assert len(result.get("scope", [])) <= 1


def test_43_gap_governance_store_validates(gap_store):
    G.validate_store(gap_store)
    assert gap_store["q71_started"] is False
    assert gap_store["live_or_s3_reads"] is False
    assert gap_store["new_scientific_research_run"] is False
    assert gap_store["certified_gap_relationships"] == 37
    assert gap_store["certification_fingerprint"] == BASELINE_CERTIFICATION_FINGERPRINT



# ---------------------------------------------------------------------------
# Future observation requirements (recorded, not deployed)
# ---------------------------------------------------------------------------

def test_44_ex2_requirement_is_recorded_not_deployed(gap_store):
    ex2 = gap_store["observation_gap_transitions"][0]
    assert ex2["question_id"] == "EX2"
    assert ex2["required_dataset_domain"] == "shadow_trades"
    assert ex2["required_canonical_identity"] == [
        "shadow_trade_id", "canonical_opportunity_id", "trade_horizon"]
    assert ex2["allowed_values_type"]["ordering"] == "strictly ascending ts"
    assert set(ex2["allowed_values_type"]["per_bar_required_keys"]) == {
        "ts", "open", "high", "low", "close"}
    assert ex2["historical_backfill_possible"] is False
    assert ex2["future_collection_only"] is True
    assert ex2["expected_reentry_trigger_class"]


def test_45_l7_requirement_is_recorded_not_deployed(gap_store):
    l7 = gap_store["observation_gap_transitions"][1]
    assert l7["question_id"] == "L7"
    assert l7["allowed_values_type"]["allowed_values"] == ["CONTROL", "CANDIDATE"]
    assert l7["allowed_values_type"]["unknown_value_behavior"] == "FAIL_CLOSED"
    assert l7["allowed_values_type"]["proxy_or_inferred_labels"] == "FORBIDDEN"
    assert "before any outcome field" in l7["required_capture_timestamp_event"]
    assert l7["historical_backfill_possible"] is False
    assert l7["future_collection_only"] is True


def test_46_requirements_bind_to_the_adjudication_evidence(gap_store, adjudication):
    for item in gap_store["observation_gap_transitions"]:
        assert item["adjudication_evidence_fingerprint"] == (
            adjudication["evidence_fingerprint"])


def test_47_requirements_do_not_touch_the_29_gap_register(gap_store):
    assert len([item for item in gap_store["work_items"]
                if item["gap_type"] == G.GAP_TYPE_DATA]) == 29


def test_48_requirements_are_inside_the_governed_store_fingerprint(gap_store):
    import hashlib
    material = {key: value for key, value in gap_store.items()
                if key != "store_fingerprint"}
    digest = hashlib.sha256(json.dumps(
        material, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("utf-8")).hexdigest()
    assert digest == gap_store["store_fingerprint"]


# ---------------------------------------------------------------------------
# Final artifacts
# ---------------------------------------------------------------------------

def test_49_adjudication_artifacts_are_persisted():
    assert ADJUDICATION_JSON.is_file()
    assert ADJUDICATION_MD.is_file()
    payload = json.loads(ADJUDICATION_JSON.read_text(encoding="utf-8"))
    assert payload["evidence_fingerprint"]
    assert {row["question_id"] for row in payload["rows"]} == {"EX2", "L7"}


def test_50_adjudication_rows_carry_every_required_field():
    payload = json.loads(ADJUDICATION_JSON.read_text(encoding="utf-8"))
    required = {
        "question_id", "governed_population", "authoritative_matched_population",
        "historically_missing_population", "ambiguous", "unexplained",
        "evidence_sources_searched", "identity_join_chain",
        "blocker_classification", "implementation_defect_remains",
        "historical_observation_gap", "scientific_state_before",
        "scientific_state_after", "scientific_result_version_before",
        "scientific_result_version_after", "certification_version_before",
        "certification_version_after", "finding_version_before",
        "finding_version_after", "gap_work_item_transition",
        "observation_requirement_id", "future_collection_required",
        "historical_backfill_possible", "reentry_trigger_expected",
        "remaining_blocker",
    }
    for row in payload["rows"]:
        assert required.issubset(row), sorted(required - set(row))


def test_51_implementation_gap_phase_accounting():
    payload = json.loads(ADJUDICATION_JSON.read_text(encoding="utf-8"))
    accounting = payload["implementation_gap_phase_accounting"]
    assert accounting["original_implementation_gaps"] == 8
    assert accounting["repaired_through_governed_reentry"] == 6
    assert accounting["converted_to_historical_observation_gaps"] == 2
    assert accounting["genuine_implementation_defects_remaining"] == 0
    assert accounting["unexplained_evidence_identities"] == 0


def test_52_artifacts_declare_no_live_reads_and_no_q71():
    payload = json.loads(ADJUDICATION_JSON.read_text(encoding="utf-8"))
    assert payload["live_or_s3_reads"] is False
    assert payload["q71_started"] is False
    assert payload["new_observation_requirements"] == 2

