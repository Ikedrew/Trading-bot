"""STAGE 4 OBSERVATION GAP CLOSEOUT -- focused tests.

Covers versioning, the EX2 and L7 contracts, the p_success producer, the OPP-1
conservation accounting, threshold governance, the requirement state machine
and the 31-gap reconciliation.
"""
from __future__ import annotations

import copy
import json

import pytest

from core.shadow import observability as OBS
from core.pipeline import predicted_success as PS
from research_engine.control_plane import stage4_data_versioning as V
from research_engine.control_plane import stage4_observation_state as S
from research_engine.control_plane import stage4_observation_thresholds as TH
from research_engine.control_plane import stage4_observation_closeout as C

ALL_OK = {axis: True for axis in V.MATERIAL_INCOMPATIBILITY_AXES}

M5_BASE = 1756000000 // 300 * 300


def _m5_bars(count, *, with_open=True):
    return [
        OBS.m5_bar_entry(
            bar_time_utc=M5_BASE + i * 300,
            bar_open=(1.0 + i * 0.001) if with_open else None,
            bar_high=1.01, bar_low=0.99, bar_close=1.005, bar_index=i)
        for i in range(count)
    ]


def _path(bars=None, **kw):
    bars = _m5_bars(3) if bars is None else bars
    params = dict(
        shadow_trade_id="ST-1", canonical_opportunity_id="EURUSD*1*P",
        trade_horizon="SCALP", entry_market_time_utc=M5_BASE,
        exit_market_time_utc=M5_BASE + 600, symbol="EURUSD")
    params.update(kw)
    return OBS.build_lifecycle_m5_path(bars, **params)


def _arm(**kw):
    params = dict(canonical_opportunity_id="EURUSD*1*P", trade_horizon="SCALP",
                  shadow_trade_id="ST-1", decision_market_time_utc=M5_BASE)
    params.update(kw)
    return OBS.assign_experiment_arm(**params)


# ═══════════════════════════════════════════════════════════════════════════
# VERSIONING
# ═══════════════════════════════════════════════════════════════════════════

def test_additive_field_bumps_schema_generation_not_dataset_version():
    a = V.classify_evolution(new_fields=["x"], producer_code_changed=True,
                             axes_compatible=ALL_OK)
    assert a.compatibility_class == V.ADDITIVE_SCHEMA_EVOLUTION
    assert a.schema_generation_change_required is True
    assert a.dataset_version_change_required is False


@pytest.mark.parametrize("axis", [
    "canonical_grain", "canonical_entity_identity", "primary_key_semantics",
    "fundamental_event_meaning", "field_semantics",
    "record_lifecycle_semantics", "backward_compatibility_without_ambiguity",
])
def test_breaking_axis_requires_dataset_version(axis):
    a = V.classify_evolution(changed_axes=[axis], new_fields=["x"],
                             axes_compatible=ALL_OK)
    assert a.compatibility_class == V.BREAKING_DATASET_EVOLUTION
    assert a.dataset_version_change_required is True
    assert a.schema_generation_change_required is False


def test_producer_only_change_does_not_bump_schema_generation():
    a = V.classify_evolution(producer_code_changed=True,
                             axes_compatible=ALL_OK)
    assert a.compatibility_class == V.ADDITIVE_SCHEMA_EVOLUTION
    assert a.schema_generation_change_required is False
    assert a.producer_version_change_required is True


def test_unasserted_axis_fails_closed_to_breaking():
    a = V.classify_evolution(new_fields=["x"])
    assert a.compatibility_class == V.BREAKING_DATASET_EVOLUTION
    assert set(a.unasserted_axes) == set(V.MATERIAL_INCOMPATIBILITY_AXES)


def test_field_reinterpretation_and_removal_are_breaking():
    assert V.classify_evolution(
        new_fields=["x"], field_reinterpretations=["state"],
        axes_compatible=ALL_OK).compatibility_class == (
            V.BREAKING_DATASET_EVOLUTION)
    assert V.classify_evolution(
        removed_fields=["y"], axes_compatible=ALL_OK
    ).compatibility_class == V.BREAKING_DATASET_EVOLUTION


def test_old_generation_is_immutable_and_new_records_carry_generation():
    matrix = C._load(C.MATRIX_PATH)
    registry = C.build_version_registry(matrix)
    gen1 = registry.generation(OBS.SHADOW_RUNTIME_DATASET, 1)
    gen2 = registry.generation(OBS.SHADOW_RUNTIME_DATASET, 2)
    assert gen1.immutable_history is True
    assert gen2.predecessor_generation == 1
    assert set(gen1.governed_fields) <= set(gen2.governed_fields)
    assert set(gen2.added_fields) == set(C.SHADOW_RUNTIME_GEN2_ADDED_FIELDS)
    assert gen1.schema_fingerprint != gen2.schema_fingerprint


def test_dataset_version_is_not_bumped_for_the_observability_change():
    registry = C.build_version_registry(C._load(C.MATRIX_PATH))
    auth = registry.authority(OBS.SHADOW_RUNTIME_DATASET, 2)
    assert auth["dataset_version"] == "shadow_runtime_v1"
    assert auth["schema_generation"] == 2
    assert auth["compatibility_class"] == V.ADDITIVE_SCHEMA_EVOLUTION


def test_evidence_epoch_pins_dataset_schema_and_producer():
    registry = C.build_version_registry(C._load(C.MATRIX_PATH))
    epoch = registry.epoch("STAGE4-EPOCH-SHADOW-RUNTIME-G2")
    producer = registry.producer_version(OBS.SHADOW_RUNTIME_DATASET,
                                         epoch.producer_version)
    assert epoch.dataset == OBS.SHADOW_RUNTIME_DATASET
    assert epoch.dataset_version == "shadow_runtime_v1"
    assert epoch.schema_generation == 2
    assert epoch.producer_fingerprint == producer.producer_fingerprint
    assert producer.schema_generation == epoch.schema_generation
    assert epoch.research_reentry_events == 0
    assert epoch.predecessor_epoch_id == "STAGE4-EPOCH-SHADOW-RUNTIME-G1"


def test_evidence_epoch_cannot_claim_ungoverned_requirements():
    with pytest.raises(V.DataVersioningError):
        V.VersionRegistry(
            generations=[V.SchemaGeneration(
                dataset="shadow_runtime", dataset_version="shadow_runtime_v1",
                generation=1, predecessor_generation=None,
                compatibility_class=V.ADDITIVE_SCHEMA_EVOLUTION,
                governed_fields=("a",), added_fields=(), changed_fields=(),
                reason="r", responsible_producer="p",
                observation_requirements=("OR-01",), questions=("X",),
                collection_start="2026-09-29", backfill_status="X")],
            producer_versions=[V.ProducerVersion(
                dataset="shadow_runtime", producer_version="pv1",
                producer_fingerprint="fp", modules=("m",),
                emitted_fields=("a",), schema_generation=1,
                predecessor_producer_version=None, change_kind="BASELINE",
                output_semantics_unchanged=True)],
            epochs=[V.EvidenceEpoch(
                epoch_id="E", dataset="shadow_runtime",
                dataset_version="shadow_runtime_v1", schema_generation=1,
                producer_version="pv1", producer_fingerprint="fp",
                collection_start="2026-09-29",
                observation_requirements=("OR-99",), questions=("X",),
                canonical_identities=("i",),
                evidence_contract_versions={"OR-99": "v1"},
                predecessor_epoch_id=None, status="OPEN")])


def test_orphan_schema_generation_is_rejected():
    with pytest.raises(V.DataVersioningError):
        V.SchemaGeneration(
            dataset="shadow_runtime", dataset_version="shadow_runtime_v1",
            generation=1, predecessor_generation=None,
            compatibility_class=V.ADDITIVE_SCHEMA_EVOLUTION,
            governed_fields=("a",), added_fields=(), changed_fields=(),
            reason="r", responsible_producer="p", observation_requirements=(),
            questions=("X",), collection_start="2026-09-29",
            backfill_status="X")


# ═══════════════════════════════════════════════════════════════════════════
# EX2
# ═══════════════════════════════════════════════════════════════════════════

def test_ex2_all_required_fields_present():
    path = _path()
    for field in OBS.EX2_REQUIRED_CONTRACT_FIELDS:
        assert field in path, field
    for bar in path["bars"]:
        for field in OBS.EX2_REQUIRED_BAR_FIELDS:
            assert field in bar, field
    assert OBS.ex2_contract_errors(path) == []


def test_ex2_timestamp_and_normalization_semantics_are_explicit():
    path = _path()
    assert path["bar_time_semantics"] == OBS.BAR_TIME_SEMANTICS
    assert path["ordering_contract"] == OBS.ORDERING_CONTRACT
    assert path["market_timestamp_normalization_version"]
    assert path["normalization_applied_by_producer"] is False
    for bar in path["bars"]:
        assert bar["bar_time_semantics"] == OBS.BAR_TIME_SEMANTICS
        assert bar["bar_time_unit"] == "seconds"
        assert bar["bar_time_timebase"] == "UTC"


def test_ex2_lifecycle_identity_is_exact():
    path = _path()
    identity = path["lifecycle_identity"]
    assert identity == {"shadow_trade_id": "ST-1",
                        "canonical_opportunity_id": "EURUSD*1*P",
                        "trade_horizon": "SCALP"}
    assert path["lineage"]["range_join_required"] is False
    assert path["lineage"]["lifecycle_identity"] == identity


def test_ex2_records_dataset_schema_generation_and_producer():
    path = _path()
    assert path["dataset_version"] == OBS.SHADOW_RUNTIME_DATASET_VERSION
    assert path["schema_generation"] == OBS.SHADOW_RUNTIME_SCHEMA_GENERATION
    assert path["producer_version"] == OBS.SHADOW_RUNTIME_PRODUCER_VERSION
    assert path["observation_requirement_id"] == "OR-14"


def test_ex2_path_completeness_state_is_derived():
    assert _path()["path_completeness_state"] == OBS.PATH_COMPLETE
    degraded = _path(_m5_bars(2, with_open=False))
    assert degraded["path_completeness_state"] == OBS.PATH_DEGRADED
    assert OBS.ex2_contract_errors(degraded)


def test_ex2_rejects_tampering_and_bad_ordering():
    path = _path()
    tampered = copy.deepcopy(path)
    tampered["bars"][0]["close"] = 9.9
    assert "EX2_PATH_DIGEST_MISMATCH" in OBS.ex2_contract_errors(tampered)
    reordered = copy.deepcopy(path)
    reordered["bars"][0], reordered["bars"][1] = (
        reordered["bars"][1], reordered["bars"][0])
    reordered["path_digest"] = None
    assert any("EX2_BAR_ORDERING_VIOLATION" in p
               for p in OBS.ex2_contract_errors(reordered))


def test_ex2_never_synthesises_a_historical_path():
    empty = _path([])
    assert empty["path_completeness_state"] == OBS.PATH_EMPTY
    assert empty["bars"] == []
    assert "FORBIDDEN" in empty["historical_synthetic_paths"]


def test_ex2_threshold_is_not_weakened():
    threshold = TH.resolve_observation_requirement_threshold("OR-14")
    assert threshold["preserved_verbatim"] is True
    assert any(r["kind"] == "minimum_event_coverage" and r["value"] == 1.0
               for r in threshold["rules"])
    assert "9,045" in threshold["forbidden"]


# ═══════════════════════════════════════════════════════════════════════════
# L7
# ═══════════════════════════════════════════════════════════════════════════

def test_l7_full_experiment_and_treatment_identity_present():
    arm = _arm()
    for field in OBS.L7_REQUIRED_CONTRACT_FIELDS:
        assert field in arm, field
    assert OBS.experiment_arm_errors(arm) == []
    assert arm["experiment_id"] == OBS.EXPERIMENT_ID
    assert arm["treatment_id"] == OBS.ARM_TO_TREATMENT[arm["experiment_arm"]]
    assert arm["arm_to_treatment_mapping"] == OBS.ARM_TO_TREATMENT
    assert arm["arm_producer"] == OBS.SHADOW_RUNTIME_PRODUCER
    assert arm["arm_schema_generation"] == OBS.SHADOW_RUNTIME_SCHEMA_GENERATION


def test_l7_schema_version_cannot_be_the_experiment_arm():
    arm = _arm()
    assert "schema_version" not in arm
    # A dataset identity presented as an arm is rejected, never accepted.
    problems = OBS.experiment_arm_errors({"experiment_arm": "shadow_runtime_v1"})
    assert problems
    assert any("shadow_runtime_v1" in p for p in problems)
    from research_engine.control_plane.stage4_impl_ownership_labels import (
        assign_l7_labels,
    )
    from research_engine.control_plane.stage4_implementation_repairs import (
        Stage4RepairError,
    )
    with pytest.raises(Stage4RepairError):
        assign_l7_labels([{"schema_version": "CONTROL"}])
    with pytest.raises(Stage4RepairError):
        assign_l7_labels([{"schema_version": "shadow_runtime_v1",
                           "experiment_arm": {"experiment_arm":
                                              "shadow_runtime_v1"}}])


def test_l7_assignment_is_pre_outcome():
    arm = _arm()
    att = arm["arm_pre_outcome_attestation"]
    assert arm["arm_assigned_at_event"] == "OPEN"
    assert att["outcome_knowledge_at_assignment"] == "NONE"
    assert att["outcome_fields_present_at_assignment"] is False
    assert att["outcome_derived_inputs"] == []
    assert arm["arm_assigned_at_market_time_utc"] == M5_BASE
    assert arm["arm_assigned_at_utc"].endswith("Z")
    # The assignment decision record names no outcome input.
    assert set(att["derivation_inputs"]) == {"arm_policy_version",
                                            "canonical_opportunity_id"}


def test_l7_assignment_id_is_deterministic_and_unique():
    a, b = _arm(), _arm()
    assert a["experiment_arm"] == b["experiment_arm"]
    assert a["arm_assignment_id"] == b["arm_assignment_id"]
    assert a["arm_assignment_digest"] == b["arm_assignment_digest"]
    other = _arm(canonical_opportunity_id="EURUSD*2*P")
    assert other["arm_assignment_id"] != a["arm_assignment_id"]
    assert len({_arm(canonical_opportunity_id=f"S{i}*1*P")["arm_assignment_id"]
                for i in range(200)}) == 200


def test_l7_policy_and_method_are_explicit():
    arm = _arm()
    assert arm["arm_policy_version"] == OBS.EXPERIMENT_ARM_POLICY_VERSION
    assert arm["arm_assignment_method"] == OBS.ARM_ASSIGNMENT_METHOD
    assert arm["arm_schema_version"] == OBS.ARM_SCHEMA_VERSION


def test_l7_experimental_semantics_are_valid_and_honest():
    semantics = OBS.ARM_DESIGN_SEMANTICS
    assert semantics["valid_under_question_contract"] is True
    assert semantics["assignment_probability_per_arm"] == 0.5
    assert "RANDOM_ASSIGNMENT_IN_EXPLANATION" in semantics["provides"]
    assert "OUTCOME_BLINDNESS" in semantics["provides"]
    # The mechanism's limits are named, not hidden.
    assert "STRATIFIED_RANDOMIZATION" in semantics["does_not_provide"]
    assert semantics["balance_guarantee"] == "IN_EXPECTATION_OVER_THE_POPULATION_ONLY"
    deficiencies = OBS.l7_design_deficiencies()
    assert deficiencies["blocking"] is False
    assert deficiencies["deficiencies"]


def test_l7_arm_allocation_is_unbiased():
    from collections import Counter
    counts = Counter(
        _arm(canonical_opportunity_id=f"S{i}*1*P")["experiment_arm"]
        for i in range(4000))
    assert set(counts) == OBS.EXPERIMENT_ARMS
    assert 0.45 <= counts["CONTROL"] / 4000 <= 0.55


def test_l7_unassigned_fails_closed_with_no_default_to_control():
    arm = OBS.assign_experiment_arm(canonical_opportunity_id="",
                                    trade_horizon="SCALP")
    assert arm["experiment_arm"] is None
    assert arm["arm_unassigned_reason"] == OBS.ARM_UNASSIGNED_NO_IDENTITY
    assert arm["treatment_id"] is None
    assert OBS.experiment_arm_errors(arm)


def test_l7_thresholds_are_preserved():
    threshold = TH.resolve_observation_requirement_threshold("OR-15")
    values = {r["field"]: r["value"] for r in threshold["rules"]}
    assert values["control"] == 100
    assert values["candidate"] == 100
    assert values["cell"] == 30
    assert threshold["preserved_verbatim"] is True


# ═══════════════════════════════════════════════════════════════════════════
# p_success
# ═══════════════════════════════════════════════════════════════════════════

def _predict(**kw):
    params = dict(opportunity_quality=0.62, market_regime="TRENDING")
    params.update(kw)
    return PS.predict_success_probability(**params)


def test_p_success_comes_from_the_authoritative_estimator():
    block = _predict()
    assert block["state"] == PS.STATE_PREDICTED
    assert block["estimator_owner"] == PS.ESTIMATOR_OWNER
    from core.pipeline.probability_estimator import (
        _ESTIMATOR_VERSION,
    )
    assert block["model_version"] == _ESTIMATOR_VERSION
    assert block["calibration_version"]
    assert block["inputs_digest"]


def test_p_success_range_and_type_are_validated():
    for quality in (0.0, 0.25, 0.5, 0.99):
        block = _predict(opportunity_quality=quality)
        assert isinstance(block["p_success"], float)
        assert PS.P_SUCCESS_MIN <= block["p_success"] <= PS.P_SUCCESS_MAX
    # Out-of-range and non-numeric inputs never yield a value.
    for bad in (5.0, -1.0, "x", None, True, float("nan")):
        block = _predict(opportunity_quality=bad)
        assert block["p_success"] is None
        assert block["state"] == PS.STATE_UNAVAILABLE


def test_p_success_has_no_outcome_leakage():
    block = _predict()
    assert block["predicted_pre_outcome"] is True
    assert block["outcome_knowledge_at_prediction"] == "NONE"
    assert set(block["inputs"]) == {"opportunity_quality", "estimator_market_state",
                                    "v10_regime", "v10_regime_mapped",
                                    "confirmation_score"}
    # The prediction depends only on decision-instant context.
    assert block["inputs"]["opportunity_quality"] == 0.62
    assert block["inputs"]["v10_regime"] == "TRENDING"


def test_p_success_missing_stays_missing_and_is_never_defaulted():
    no_quality = _predict(opportunity_quality=None)
    assert no_quality["p_success"] is None
    assert no_quality["unavailable_reason"] == PS.REASON_NO_OPPORTUNITY_QUALITY
    no_regime = _predict(market_regime="")
    assert no_regime["p_success"] is None
    assert no_regime["unavailable_reason"] == PS.REASON_NO_MARKET_STATE
    # Explicitly NOT a 0/0.5 default.
    for block in (no_quality, no_regime):
        assert block["p_success"] not in (0.0, 0.5)


def test_p_success_regime_mapping_is_explicit_and_versioned():
    state, mapped = PS.map_v10_regime_to_estimator_state("TRENDING")
    assert state == "STRUCTURED" and mapped is True
    state, mapped = PS.map_v10_regime_to_estimator_state("VOLATILE")
    assert state == "CHOP" and mapped is True
    state, mapped = PS.map_v10_regime_to_estimator_state("WEIRD")
    assert mapped is False
    assert _predict()["market_state_mapping_version"] == (
        PS.MARKET_STATE_MAPPING_VERSION)


def test_p_success_model_lineage_is_recorded():
    block = _predict()
    for key in ("model_version", "calibration_version", "calibration_source",
                "calibrator_active_version", "raw_score",
                "calibrated_probability", "uncertainty_dampening",
                "evidence_used", "inputs", "inputs_digest"):
        assert key in block, key
    assert block["evidence_used"]


def test_p_success_is_deterministic():
    assert _predict()["inputs_digest"] == _predict()["inputs_digest"]
    assert _predict()["p_success"] == _predict()["p_success"]


def test_p_success_historical_backfill_is_refused_not_inferred():
    assert PS.HISTORICAL_BACKFILL_SUPPORTED is False
    assert "No value is inferred" in PS.HISTORICAL_BACKFILL_REFUSAL
    assert "UNAVAILABLE" in PS.HISTORICAL_BACKFILL_REFUSAL


def test_p_success_is_wired_into_the_v10_decision_trace():
    from core.decision_trace import DecisionTrace
    assert "predicted_success" in DecisionTrace.__dataclass_fields__
    row = _predict()
    assert row["state"] == PS.STATE_PREDICTED


# ═══════════════════════════════════════════════════════════════════════════
# OPP-1
# ═══════════════════════════════════════════════════════════════════════════

def test_opp1_coverage_accounting_is_conserved():
    opp = C.analyse_opp1_coverage()
    cons = opp["conservation"]
    assert cons["total"] == 100850
    assert cons["present"] + cons["missing"] == cons["total"]
    assert cons["present_plus_missing_equals_total"] is True
    assert opp["remaining_unexplained"] == 0


def test_opp1_missing_records_are_classified_by_cause():
    opp = C.analyse_opp1_coverage()
    causes = opp["reason_categories"]
    assert causes
    assert sum(c["records"] for c in causes) == opp["records_still_missing"]
    for cause in causes:
        assert cause["cause"] in C.MISSING_CAUSE_CATEGORIES


def test_opp1_backfill_is_refused_as_inference_not_performed():
    opp = C.analyse_opp1_coverage()
    cause = opp["reason_categories"][0]
    assert cause["cause"] == C.CAUSE_PRODUCER_DIVERSION
    assert cause["authoritative_backfill_possible"] is False
    assert "DISJOINT closed" in cause["backfill_refusal"]
    assert opp["records_repaired"] == 0
    assert opp["coverage_changed"] is False
    assert opp["residual_gap_preserved_truthfully"] is True


def test_opp1_shape_evidence_proves_two_producers():
    evidence = C.analyse_opp1_coverage()["sampled_shape_evidence"]
    assert evidence["shapes_sum_to_sample"] is True
    assert evidence["legacy_shape_rows"] > 0
    assert evidence["v10_shape_rows"] > 0


def test_opp1_vocabularies_are_disjoint_closed_sets():
    assert set(C.OPP1_LEGACY_STATE_VOCABULARY).isdisjoint(
        C.OPP1_V10_STATE_VOCABULARY)
    assert C.OPP1_V10_STATE_VOCABULARY == ("VALID", "INVALID", "WATCHING")


# ═══════════════════════════════════════════════════════════════════════════
# THRESHOLDS
# ═══════════════════════════════════════════════════════════════════════════

def test_every_gap_has_a_threshold_rule_or_explicit_method_blocker():
    matrix = C._load(C.MATRIX_PATH)
    document = C.build_threshold_document(
        C.reconcile_gaps(matrix, C.build_version_registry(matrix)))
    per_gap = document["per_gap"]
    assert len(per_gap) == 31
    for gap, entry in per_gap.items():
        assert entry["classification"] in (
            TH.THRESHOLD_GOVERNED, TH.METHOD_THRESHOLD_DEFINITION_REQUIRED), gap
        if entry["classification"] == TH.THRESHOLD_GOVERNED:
            assert entry["rules"], gap
            assert entry["threshold_source"], gap
        else:
            assert entry["rules"] == []
    assert (document["counts"]["gaps_with_governed_threshold"]
            + document["counts"]["gaps_method_threshold_definition_required"]
            == 31)


def test_method_threshold_definition_required_names_the_missing_decision():
    resolved = TH.resolve_threshold("NOT_A_REAL_QUESTION_XYZ")
    assert resolved["classification"] == TH.METHOD_THRESHOLD_DEFINITION_REQUIRED
    assert resolved["rules"] == []
    assert "effect size" in resolved["missing_methodological_decision"]


def test_no_arbitrary_fallback_threshold_exists():
    # L2 has an EMPTY registry rule set, so its threshold must come from the
    # authoritative adjudication constant, never from a generic default.
    l2 = TH.resolve_threshold("L2")
    assert l2["threshold_source"] == TH.SOURCE_ADJUDICATION_MIN
    assert l2["constant"] == "L2_MIN"
    assert l2["authority"].endswith("L2_MIN")
    values = {r["field"]: r["value"] for r in l2["rules"]}
    assert values == {"pre": 100, "post": 100, "cell": 30}


def test_thresholds_are_derived_from_the_question_scientific_method():
    m3 = TH.resolve_threshold("M3")
    assert m3["threshold_source"] == TH.SOURCE_QUESTION_VALIDATION_RULES
    fields = {r["registry_field"] for r in m3["rules"]}
    assert "h4_regime_coverage" in fields
    assert "market_phase_coverage" in fields
    assert "outcome_coverage" in fields
    for rule in m3["rules"]:
        assert rule["kind"] in TH.THRESHOLD_RULE_KINDS
        assert rule["value"] is not None


def test_threshold_state_controls_reentry_eligibility():
    matrix = C._load(C.MATRIX_PATH)
    closeout = C.reconcile_gaps(matrix, C.build_version_registry(matrix))
    for row in closeout["rows"]:
        if row["closeout_state"] == C.SATISFIED_OBSERVATION:
            assert row["threshold_classification"] == TH.THRESHOLD_GOVERNED
    assert closeout["reentry"]["executed"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# OBSERVATION REQUIREMENT STATE MACHINE
# ═══════════════════════════════════════════════════════════════════════════

def test_state_machine_states_and_gates_are_complete():
    assert len(S.SATISFIED_GATES) == 6
    for state in S.STATES:
        assert state in S.LEGAL_TRANSITIONS


def test_code_existing_is_not_satisfied():
    evidence = S.RequirementEvidence(
        observation_requirement_id="OR-01", schema_ready=True,
        producer_ready=True)
    assert S.next_state(S.PRODUCER_READY, evidence) != S.SATISFIED
    satisfied, missing = S.can_satisfy(evidence)
    assert satisfied is False
    assert "valid_evidence" in missing


_GATE_TO_FIELD = {
    "correct_schema_and_version": "schema_ready",
    "correct_producer": "producer_ready",
    "valid_evidence": "evidence_valid",
    "completeness_requirement_met": "completeness_met",
    "threshold_met_where_applicable": "threshold_met",
    "lineage_valid": "lineage_valid",
}


def test_satisfied_requires_all_six_gates():
    base = dict(observation_requirement_id="OR-01", schema_ready=True,
                producer_ready=True, evidence_valid=True, completeness_met=True,
                threshold_rule_present=True, threshold_met=True,
                lineage_valid=True)
    assert S.next_state(S.COLLECTING, S.RequirementEvidence(**base)) == (
        S.SATISFIED)
    for gate in S.SATISFIED_GATES:
        broken = dict(base)
        broken[_GATE_TO_FIELD[gate]] = False
        evidence = S.RequirementEvidence(**broken)
        assert S.can_satisfy(evidence)[0] is False, gate
        assert gate in S.unmet_satisfied_gates(evidence), gate


def test_state_machine_rejects_unknown_state():
    with pytest.raises(S.ObservationStateError):
        S.next_state("NOT_A_STATE", S.RequirementEvidence(
            observation_requirement_id="OR-01"))


def test_satisfied_is_not_reachable_directly_from_early_states():
    sources = {state for state, targets in S.LEGAL_TRANSITIONS.items()
               if S.SATISFIED in targets}
    assert S.WAITING_THRESHOLD in sources
    assert S.DECLARED not in sources
    assert S.SCHEMA_READY not in sources
    assert S.PRODUCER_READY not in sources


# ═══════════════════════════════════════════════════════════════════════════
# CONSERVATION
# ═══════════════════════════════════════════════════════════════════════════

def test_all_31_gaps_are_accounted_in_exactly_one_state():
    matrix = C._load(C.MATRIX_PATH)
    closeout = C.reconcile_gaps(matrix, C.build_version_registry(matrix))
    rows = closeout["rows"]
    assert len(rows) == 31
    assert len({r["gap_id"] for r in rows}) == 31
    for row in rows:
        assert row["closeout_state"] in C.CLOSEOUT_STATES
    assert sum(closeout["counts"].values()) == 31


def test_all_15_observation_requirements_are_accounted():
    matrix = C._load(C.MATRIX_PATH)
    closeout = C.reconcile_gaps(matrix, C.build_version_registry(matrix))
    cons = closeout["conservation"]
    assert cons["total_observation_requirements"] == 15
    assert cons["accounted_observation_requirements"] == 15
    assert cons["unaccounted_observation_requirements"] == []


def test_no_unaccounted_no_ownerless_no_unexplained():
    matrix = C._load(C.MATRIX_PATH)
    closeout = C.reconcile_gaps(matrix, C.build_version_registry(matrix))
    C.assert_conservation(closeout)
    cons = closeout["conservation"]
    assert cons["unaccounted_gaps"] == 0
    assert cons["ownerless_gaps"] == 0
    assert cons["unexplained_gaps"] == 0
    assert cons["unknown_gap_ids"] == []


def test_no_dataset_or_schema_mismatch_is_unexplained():
    matrix = C._load(C.MATRIX_PATH)
    closeout = C.reconcile_gaps(matrix, C.build_version_registry(matrix))
    for row in closeout["rows"]:
        assert row["dataset_version"]
        assert row["dataset_version"] not in ("", "UNVERSIONED_AUDITED_DATASET")
        assert isinstance(row["schema_generation"], int)
        assert row["compatibility_class"] in V.COMPATIBILITY_CLASSES


def test_gap_links_question_to_requirement_to_epoch():
    matrix = C._load(C.MATRIX_PATH)
    closeout = C.reconcile_gaps(matrix, C.build_version_registry(matrix))
    for row in closeout["rows"]:
        assert row["question_id"]
        assert row["observation_requirement_id"].startswith("OR-")
        assert row["observation_requirements_served"]
        assert row["producer_version"]
        assert row["collection_start"]
        assert row["reentry_eligibility"] in (
            C.REENTRY_ELIGIBLE, C.REENTRY_INELIGIBLE)


def test_no_premature_reentry_and_no_mutation():
    document = C.build_all()["closeout"]
    assert document["reentry"]["eligible"] == 0
    assert document["reentry"]["executed"] == 0
    ledger = document["mutation_ledger"]
    assert ledger["s3_writes"] == 0
    assert ledger["historical_mutations"] == 0
    assert ledger["backfills_performed"] == 0
    assert ledger["research_reentry_events"] == 0
    assert ledger["q71_started"] is False


def test_required_artifacts_exist_and_are_deterministic():
    first = C.build_all()
    second = C.build_all()
    for path in C.CLOSEOUT_JSON_PATH, C.POLICY_JSON_PATH, \
            C.THRESHOLD_JSON_PATH, C.EPOCH_JSON_PATH:
        assert path.exists(), path
    assert first["closeout"] == second["closeout"]
    assert first["thresholds"] == second["thresholds"]
    assert C.CLOSEOUT_MD_PATH.exists()

