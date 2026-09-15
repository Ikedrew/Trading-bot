"""Phase 5A tests for canonical question definition contract."""
from __future__ import annotations

import json

from research_engine.registry import (
    BASELINE_VERSION,
    BASELINE_QUESTION_IDS,
    REGISTRY,
    build_definitions_from_registry,
    get_question_health,
    validate_all_definitions,
    validate_definition,
)
from research_engine.registry.research_question_models import (
    EvidenceAuthority,
    QuestionLifecycle,
    ResearchQuestionDefinition,
    ValidationSeverity,
)
from research_engine.registry.research_question_registry import REGISTRY_BY_ID


def test_all_70_instantiate_definition_contract():
    definitions = build_definitions_from_registry(REGISTRY)
    assert len(definitions) == len(REGISTRY) == 70
    for q in REGISTRY:
        d = definitions[q.id]
        assert d.canonical_question_id == q.id
        assert d.runner_module == q.runner_module
        assert d.runner_function == q.runner_function
        assert d.report_filename == q.report_filename
        assert d.legacy_ids == q.legacy_ids


def test_baseline_manifest_contains_original_70():
    assert len(BASELINE_QUESTION_IDS) == 70
    assert BASELINE_VERSION == 1
    for q in REGISTRY:
        assert q.id in BASELINE_QUESTION_IDS


def test_registry_cardinality_can_exceed_baseline():
    definitions = build_definitions_from_registry(REGISTRY)
    assert len(definitions) == len(BASELINE_QUESTION_IDS)
    assert len(definitions) == 70
    # Dynamic design: definitions dict supports growth beyond frozen V1 baseline.
    grown = dict(definitions)
    grown["Q71-PROBE"] = ResearchQuestionDefinition(
        canonical_question_id="Q71-PROBE",
        question_wording="probe",
        hypothesis="h",
        population_definition="p",
        metric_definition="m",
        evidence_authorities=(EvidenceAuthority(dataset="shadow_trades"),),
        epoch_requirement="CURRENT",
    )
    assert len(grown) == len(BASELINE_QUESTION_IDS) + 1


def test_definition_version_starts_at_1():
    definitions = build_definitions_from_registry(REGISTRY)
    for d in definitions.values():
        assert d.definition_version == 1


def test_lifecycle_status_distinct_from_readiness():
    definitions = build_definitions_from_registry(REGISTRY)
    for d in definitions.values():
        assert d.lifecycle_status == QuestionLifecycle.ACTIVE


def test_d2_repaired_authority_is_valid():
    definitions = build_definitions_from_registry(REGISTRY)
    report = validate_definition(definitions["D2"])
    unresolved = [r for r in report.results if r.category == "UNRESOLVED_AUTHORITY"]
    assert unresolved == []
    assert get_question_health(report) == "VALID"


def test_x5_unresolved_authority_visible():
    # X5 was repaired in RW3.5: predicted_ev_r_v1 (reused from D3) is a versioned
    # pre-decision EV authority, so no UNRESOLVED_AUTHORITY remains and X5 is VALID.
    definitions = build_definitions_from_registry(REGISTRY)
    report = validate_definition(definitions["X5"])
    unresolved = [r for r in report.results if r.category == "UNRESOLVED_AUTHORITY"]
    assert unresolved == []
    assert get_question_health(report) == "VALID"


def test_missing_authority_detected():
    d = ResearchQuestionDefinition(
        canonical_question_id="TEST",
        evidence_authorities=(),
    )
    report = validate_definition(d)
    assert any(r.category == "MISSING_AUTHORITY" for r in report.results)


def test_missing_join_contract_for_multi_source():
    d = ResearchQuestionDefinition(
        canonical_question_id="TEST",
        evidence_authorities=(
            EvidenceAuthority(dataset="shadow_trades"),
            EvidenceAuthority(dataset="decision_trace"),
        ),
        join_contract=None,
        epoch_requirement="CURRENT",
    )
    report = validate_definition(d)
    assert any(r.category == "MISSING_JOIN_CONTRACT" for r in report.results)


def test_missing_epoch_requirement_detected():
    d = ResearchQuestionDefinition(
        canonical_question_id="TEST",
        evidence_authorities=(EvidenceAuthority(dataset="shadow_trades"),),
        epoch_requirement="",
    )
    report = validate_definition(d)
    assert any(r.category == "MISSING_EPOCH_REQUIREMENT" for r in report.results)


def test_missing_runner_is_info_not_error():
    definitions = build_definitions_from_registry(REGISTRY)
    no_runner_ids = {"S5", "S6", "S7", "X6", "L6", "G1", "G2", "G3"}
    for nid in sorted(no_runner_ids):
        d = definitions[nid]
        report = validate_definition(d)
        info_results = [r for r in report.results if r.category == "MISSING_RUNNER"]
        assert len(info_results) == 1
        assert info_results[0].severity == ValidationSeverity.INFO


def test_legacy_aliases_preserved():
    definitions = build_definitions_from_registry(REGISTRY)
    e1 = definitions["E1"]
    assert "Q19" in e1.legacy_ids
    d2 = definitions["D2"]
    assert "Q4" in d2.legacy_ids
    assert "Q20" in d2.legacy_ids


def test_question_wording_from_description():
    definitions = build_definitions_from_registry(REGISTRY)
    e1 = definitions["E1"]
    assert e1.question_wording == REGISTRY_BY_ID["E1"].description


def test_semantic_mismatch_detected():
    from research_engine.registry import REGISTRY_BY_ID
    from research_engine.registry.definition_validator import validate_runner_registry_threshold_alignment
    # D2, D3, D4 and D5 were repaired in RW3 and are no longer semantic mismatches.
    mismatch_ids = {"X3", "L1", "L2", "L3", "L4", "EX5", "EX6", "EX7", "EX8", "EXEC1"}
    for mid in mismatch_ids:
        q = REGISTRY_BY_ID[mid]
        report = validate_runner_registry_threshold_alignment(q, mid)
        assert any(r.category == "SEMANTIC_MISMATCH" for r in report.results)


def test_json_serializable_output():
    definitions = build_definitions_from_registry(REGISTRY)
    reports = validate_all_definitions(definitions)
    output = {qid: report.to_dict() for qid, report in reports.items()}
    json_str = json.dumps(output, sort_keys=True)
    assert json_str is not None


def test_health_categories_populated():
    definitions = build_definitions_from_registry(REGISTRY)
    reports = validate_all_definitions(definitions)

    # D2 and X5 are both repaired in RW3; neither has unresolved authority.
    d2_report = reports["D2"]
    assert get_question_health(d2_report) == "VALID"

    x5_report = reports["X5"]
    assert not any(r.category == "UNRESOLVED_AUTHORITY" for r in x5_report.results)
    assert get_question_health(x5_report) == "VALID"

    # Wave A1 closed targets are now semantically complete and VALID (one is
    # VALID_WITH_WARNINGS because of a pre-existing report-filename ambiguity
    # with a non-target question — see AMBIGUOUS_REPORT_MAPPING below).
    from research_engine.registry.wave_a1_definitions import WAVE_A1_RESOLVED
    resolved_health = {qid: get_question_health(reports[qid]) for qid in sorted(WAVE_A1_RESOLVED)}
    assert set(resolved_health.values()) == {"VALID", "VALID_WITH_WARNINGS"}
    assert resolved_health["D1"] == "VALID_WITH_WARNINGS"
    assert any(
        r.category == "AMBIGUOUS_REPORT_MAPPING"
        for r in reports["D1"].results
    )

    # Unresolved Wave A1 targets must stay fail-closed (never forced VALID).
    from research_engine.registry.wave_a1_definitions import WAVE_A1_UNRESOLVED
    for qid in WAVE_A1_UNRESOLVED:
        assert get_question_health(reports[qid]) != "VALID"



def test_no_questions_deleted():
    from research_engine.registry.baseline_manifest import validate_registry_against_baseline
    definitions = build_definitions_from_registry(REGISTRY)
    canonical_ids = {q.id for q in REGISTRY}
    assert set(definitions.keys()) == canonical_ids
    check = validate_registry_against_baseline(canonical_ids)
    assert check["missing"] == []
    assert check["baseline_count"] == 70


def test_invalid_version_rejected():
    d = ResearchQuestionDefinition(
        canonical_question_id="TEST",
        definition_version=2,
        question_wording="w",
        hypothesis="h",
        population_definition="p",
        metric_definition="m",
        evidence_authorities=(EvidenceAuthority(dataset="shadow_trades"),),
        epoch_requirement="CURRENT",
    )
    report = validate_definition(d)
    assert any(r.category == "INVALID_VERSION" for r in report.results)


def test_dependency_error_detected():
    definitions = build_definitions_from_registry(REGISTRY)
    bad = ResearchQuestionDefinition(
        canonical_question_id="TEST",
        question_wording="w",
        hypothesis="h",
        population_definition="p",
        metric_definition="m",
        evidence_authorities=(EvidenceAuthority(dataset="shadow_trades"),),
        epoch_requirement="CURRENT",
        depends_on=("NO-SUCH-QUESTION",),
    )
    report = validate_definition(bad, registry_ids=set(definitions.keys()))
    assert any(r.category == "DEPENDENCY_ERROR" for r in report.results)


def test_no_runner_health_distinct():
    definitions = build_definitions_from_registry(REGISTRY)
    reports = validate_all_definitions(definitions)
    # NO_RUNNER questions carry MISSING_RUNNER INFO; health helper isolates
    # the pure single-finding case as NO_RUNNER.
    solo = ResearchQuestionDefinition(
        canonical_question_id="TEST",
        question_wording="w",
        hypothesis="h",
        population_definition="p",
        metric_definition="m",
        evidence_authorities=(EvidenceAuthority(dataset="shadow_trades"),),
        epoch_requirement="CURRENT",
    )
    solo_report = validate_definition(solo)
    assert get_question_health(solo_report) == "NO_RUNNER"
    assert any(
        get_question_health(r) in ("NO_RUNNER", "UNDER_SPECIFIED", "SEMANTIC_MISMATCH", "INVALID")
        for r in reports.values()
    )


# ─────────────────────────────────────────────────────────────────────────────
# WAVE A1 SAFE DEFINITION CLOSURE — focused contract tests
# ─────────────────────────────────────────────────────────────────────────────

from dataclasses import replace  # noqa: E402

from research_engine.registry.wave_a1_definitions import (  # noqa: E402
    WAVE_A1_RESOLVED,
    WAVE_A1_TARGETS,
    WAVE_A1_UNRESOLVED,
    WAVE_A1_UNRESOLVED_REASONS,
    apply_wave_a1_definitions,
)

# Datasets the canonical evidence resolver knows how to load/normalise for the
# closed subset. A closed definition must never reference an unavailable source.
_KNOWN_EVIDENCE_DATASETS = {
    "shadow_trades", "decision_trace", "trade_truth",
    "management_actions", "execution_results_v1", "execution_context",
    "strategy_candidates", "protection_audit_v1", "execution_attempts_v1",
    "risk_deviation_v1", "horizon_candidates", "portfolio_rankings",
    "opportunities", "assessments",
}

# Registry sample_size readiness thresholds the closed definitions must agree
# with. Questions without a sample_size rule must not declare a minimum_sample.
_EXPECTED_MINIMUM_SAMPLE = {
    "EX3": 200, "EX4": 200, "X1": 30, "MGMT-1": 30, "MGMT-2": 15,
    "STRAT-1": 30, "PROT1": 30, "M9": 100, "M10": 100,
}
_NO_SAMPLE_RULE = {
    "E1", "E4", "M2", "M4", "M6", "D1", "X4",
}


def _closed_definitions():
    return build_definitions_from_registry(REGISTRY)


def test_wave_a1_scope_is_exactly_the_20_targets():
    assert len(WAVE_A1_TARGETS) == 20
    assert WAVE_A1_RESOLVED | WAVE_A1_UNRESOLVED == WAVE_A1_TARGETS
    assert not (WAVE_A1_RESOLVED & WAVE_A1_UNRESOLVED)
    assert len(WAVE_A1_RESOLVED) == 16
    assert len(WAVE_A1_UNRESOLVED) == 4
    # Every unresolved target has an explicit, exact conflict reason.
    assert set(WAVE_A1_UNRESOLVED_REASONS) == WAVE_A1_UNRESOLVED


def test_every_closed_target_has_complete_scientific_definition():
    definitions = _closed_definitions()
    for qid in sorted(WAVE_A1_RESOLVED):
        d = definitions[qid]
        assert d.hypothesis.strip(), qid
        assert d.null_hypothesis.strip(), qid
        assert d.population_definition.strip(), qid
        assert d.metric_definition.strip(), qid
        assert d.evidence_authorities, qid
        assert d.epoch_requirement == "CURRENT", qid
        assert d.runner_module, qid
        assert d.runner_function, qid
        assert d.report_filename, qid


def test_closed_target_evidence_authorities_are_explicit_and_available():
    definitions = _closed_definitions()
    for qid in sorted(WAVE_A1_RESOLVED):
        d = definitions[qid]
        assert d.evidence_authorities, qid
        for auth in d.evidence_authorities:
            assert auth.dataset in _KNOWN_EVIDENCE_DATASETS, (
                f"{qid} references unavailable dataset {auth.dataset!r}"
            )
            assert auth.semantic_meaning.strip(), f"{qid} authority lacks semantic_meaning"
            assert auth.field_path, f"{qid} authority lacks field_path"
            assert auth.current_eligibility is True, f"{qid} authority not CURRENT-eligible"


def test_multi_source_closed_targets_declare_join_contract():
    definitions = _closed_definitions()
    multi = {qid for qid in WAVE_A1_RESOLVED
             if len(definitions[qid].evidence_authorities) > 1}
    assert multi == {"D1", "X1", "X4", "MGMT-1", "MGMT-2", "STRAT-1"}
    for qid in multi:
        assert definitions[qid].join_contract is not None, qid
        assert definitions[qid].join_contract.join_keys, qid


def test_closed_target_sample_semantics_agree_with_readiness_runner():
    definitions = _closed_definitions()
    for qid, expected in _EXPECTED_MINIMUM_SAMPLE.items():
        d = definitions[qid]
        assert d.minimum_sample == expected, (
            f"{qid}: minimum_sample={d.minimum_sample} != {expected}"
        )
        assert d.completion_rule is not None, qid
        assert d.completion_rule.rule_type == "sample_reached", qid
        assert d.completion_rule.threshold == expected, qid
    for qid in _NO_SAMPLE_RULE:
        d = definitions[qid]
        assert d.minimum_sample is None, f"{qid} declares sample without registry rule"
        assert d.completion_rule is not None, qid
        assert d.completion_rule.rule_type == "report_exists", qid


def test_unresolved_targets_remain_fail_closed():
    definitions = _closed_definitions()
    reports = validate_all_definitions(definitions)
    for qid in sorted(WAVE_A1_UNRESOLVED):
        d = definitions[qid]
        # Not populated (no invention) — hypothesis/population/metric stay blank.
        assert not d.hypothesis.strip(), qid
        assert not d.population_definition.strip(), qid
        assert not d.metric_definition.strip(), qid
        # And the validator refuses to call them VALID.
        assert get_question_health(reports[qid]) != "VALID", qid


def test_validator_accepts_only_semantically_complete_definitions():
    definitions = _closed_definitions()
    for qid in ("E1", "STRAT-1", "PROT1"):
        base = definitions[qid]
        # Remove the metric -> the definition must no longer be VALID.
        stripped = replace(base, metric_definition="")
        report = validate_definition(stripped)
        assert get_question_health(report) != "VALID", qid
        assert any(r.category == "MISSING_METRIC" for r in report.results)


def test_non_target_definitions_not_silently_altered():
    # apply_wave_a1_definitions must leave every non-target question untouched
    # (same object identity) and only replace the resolved target entries.
    definitions = _closed_definitions()
    applied = apply_wave_a1_definitions(definitions)
    assert set(applied) == set(definitions) == {q.id for q in REGISTRY}
    for qid, d in applied.items():
        if qid in WAVE_A1_RESOLVED:
            assert d is not definitions[qid], f"resolved target {qid} not enriched"
        else:
            assert d is definitions[qid], f"non-target {qid} definition object changed"


# ---------------------------------------------------------------------------
# WAVE A2.1 SAFE DEFINITION CLOSURE - focused contract tests
# ---------------------------------------------------------------------------

from research_engine.registry.wave_a2_definitions import (  # noqa: E402
    WAVE_A2_1_TARGETS,
    WAVE_A2_2A_TARGETS,
    WAVE_A2_2B_TARGETS,
    WAVE_A2_OVERRIDES,
    WAVE_A2_RESOLVED,
    WAVE_A2_TARGETS,
    WAVE_A2_UNRESOLVED,
    WAVE_A2_UNRESOLVED_REASONS,
    apply_wave_a2_definitions,
)


def _rw3_repair_modules():
    """(module, attr) pairs for every RW3 definition repair (D2/D3/D4/D5/X5)."""
    import research_engine.registry.rw3_d2_definitions as d2_repair
    import research_engine.registry.rw3_d3_definitions as d3_repair
    import research_engine.registry.rw3_d4_definitions as d4_repair
    import research_engine.registry.rw3_d5_definitions as d5_repair
    import research_engine.registry.rw3_x5_definitions as x5_repair

    return (
        (d2_repair, "apply_d2_definition"),
        (d3_repair, "apply_d3_definition"),
        (d4_repair, "apply_d4_definition"),
        (d5_repair, "apply_d5_definition"),
        (x5_repair, "apply_x5_definition"),
    )


def _build_pre_a2_definitions(monkeypatch):
    """Build the committed Wave A1 state, before A2/RW2/RW3 applications."""
    import research_engine.registry.wave_a2_definitions as wave_a2
    import research_engine.registry.rw2_definitions as rw2

    disabled = ((wave_a2, "apply_wave_a2_definitions"), (rw2, "apply_rw2_definitions")) + _rw3_repair_modules()
    originals = [(module, attr, getattr(module, attr)) for module, attr in disabled]
    for module, attr in disabled:
        monkeypatch.setattr(module, attr, lambda definitions: definitions)
    before = build_definitions_from_registry(REGISTRY)
    for module, attr, original in originals:
        monkeypatch.setattr(module, attr, original)
    return before


def test_wave_a2_scope_and_pre_state_are_exact(monkeypatch):
    assert WAVE_A2_1_TARGETS == {"E2", "E5", "M5", "M8", "S2", "X2"}
    assert WAVE_A2_2A_TARGETS == {"S3", "S4", "RISK-1"}
    assert WAVE_A2_2B_TARGETS == {"L5", "HORIZON-1", "OPP-1"}
    assert WAVE_A2_TARGETS == (
        WAVE_A2_1_TARGETS | WAVE_A2_2A_TARGETS | WAVE_A2_2B_TARGETS
    )
    assert WAVE_A2_RESOLVED == {
        "E2", "E5", "M5", "S2", "X2", "S3", "S4", "RISK-1", "L5",
        "HORIZON-1",
    }
    assert WAVE_A2_UNRESOLVED == {"M8", "OPP-1"}
    assert WAVE_A2_RESOLVED | WAVE_A2_UNRESOLVED == WAVE_A2_TARGETS
    assert not (WAVE_A2_RESOLVED & WAVE_A2_UNRESOLVED)
    assert set(WAVE_A2_OVERRIDES) == WAVE_A2_RESOLVED
    assert set(WAVE_A2_UNRESOLVED_REASONS) == WAVE_A2_UNRESOLVED

    before = _build_pre_a2_definitions(monkeypatch)
    reports = validate_all_definitions(before)
    assert {
        qid: get_question_health(reports[qid]) for qid in WAVE_A2_TARGETS
    } == {qid: "UNDER_SPECIFIED" for qid in WAVE_A2_TARGETS}


def test_wave_a2_resolved_health_and_unresolved_fail_closed():
    definitions = build_definitions_from_registry(REGISTRY)
    reports = validate_all_definitions(definitions)

    for qid in WAVE_A2_RESOLVED:
        assert get_question_health(reports[qid]) in {
            "VALID", "VALID_WITH_WARNINGS"
        }, qid
        definition = definitions[qid]
        assert definition.hypothesis.strip(), qid
        assert definition.null_hypothesis.strip(), qid
        assert definition.population_definition.strip(), qid
        assert definition.metric_definition.strip(), qid
        assert definition.evidence_authorities, qid

    for qid in WAVE_A2_UNRESOLVED - {"M8"}:
        unresolved = definitions[qid]
        assert not unresolved.hypothesis.strip(), qid
        assert not unresolved.population_definition.strip(), qid
        assert not unresolved.metric_definition.strip(), qid
        assert get_question_health(reports[qid]) == "UNDER_SPECIFIED", qid
    assert get_question_health(reports["M8"]) == "VALID"
    assert definitions["M8"].hypothesis.strip()
    assert "market_context" in WAVE_A2_UNRESOLVED_REASONS["M8"]
    assert "missing outcome to 0.0" in WAVE_A2_UNRESOLVED_REASONS["OPP-1"].replace(
        "a missing outcome", "missing outcome"
    )


def test_wave_a2_hidden_runner_thresholds_are_declarative():
    definitions = build_definitions_from_registry(REGISTRY)
    expected = {
        "E5": 80, "M5": 30, "S2": 30, "X2": 30,
        "S3": 30, "S4": 30, "RISK-1": 30,
        "L5": 20, "HORIZON-1": 30,
    }
    for qid, threshold in expected.items():
        definition = definitions[qid]
        assert definition.minimum_sample == threshold, qid
        assert definition.completion_rule is not None, qid
        assert definition.completion_rule.rule_type == "sample_reached", qid
        assert definition.completion_rule.threshold == threshold, qid

    assert definitions["E2"].minimum_sample is None
    assert definitions["E2"].completion_rule.rule_type == "report_exists"
    assert ">=5" in definitions["E2"].completion_rule.description
    assert ">=3 phase-transition" in definitions["M5"].completion_rule.description
    assert ">=2 explicitly observed horizons" in definitions["S2"].completion_rule.description
    assert "SCALP-only evidence remains" in definitions["S2"].completion_rule.description
    assert " OR " in definitions["X2"].completion_rule.description


def test_wave_a2_application_changes_only_resolved_targets(monkeypatch):
    before = _build_pre_a2_definitions(monkeypatch)
    after = apply_wave_a2_definitions(before)
    assert set(after) == set(before) == {question.id for question in REGISTRY}
    for qid in before:
        if qid in WAVE_A2_RESOLVED:
            assert after[qid] is not before[qid], qid
        else:
            assert after[qid] is before[qid], qid


def test_e2_and_l1_shared_artifact_does_not_collapse_semantics():
    definitions = build_definitions_from_registry(REGISTRY)
    reports = validate_all_definitions(definitions)
    e2 = definitions["E2"]
    l1 = definitions["L1"]

    assert e2.runner_module == l1.runner_module
    assert e2.runner_function == l1.runner_function
    assert e2.report_filename == l1.report_filename
    assert e2.question_wording != l1.question_wording
    assert e2.hypothesis.strip()
    assert not l1.hypothesis.strip()
    assert any(
        result.category == "AMBIGUOUS_REPORT_MAPPING"
        for result in reports["E2"].results
    )


def test_wave_a2_does_not_change_d2_or_x5(monkeypatch):
    before = _build_pre_a2_definitions(monkeypatch)
    after = apply_wave_a2_definitions(before)
    for qid in ("D2", "X5"):
        assert after[qid] is before[qid]
        assert after[qid].to_dict() == before[qid].to_dict()
        report = validate_definition(after[qid])
        # The A2 module leaves both untouched; their pre-RW3 registry-derived
        # definitions are UNDER_SPECIFIED (empty hypothesis/population/metric).
        # X5 no longer carries an UNRESOLVED_AUTHORITY error (repaired in RW3.5).
        assert not any(result.category == "UNRESOLVED_AUTHORITY" for result in report.results)
        assert get_question_health(report) == "UNDER_SPECIFIED"


# ---------------------------------------------------------------------------
# WAVE A2.2a - S3 / S4 / RISK-1 focused closure tests
# ---------------------------------------------------------------------------

def test_wave_a22a_targets_move_from_under_specified_to_valid(monkeypatch):
    before = _build_pre_a2_definitions(monkeypatch)
    after = build_definitions_from_registry(REGISTRY)
    before_reports = validate_all_definitions(before)
    after_reports = validate_all_definitions(after)

    for qid in WAVE_A2_2A_TARGETS:
        assert get_question_health(before_reports[qid]) == "UNDER_SPECIFIED", qid
        assert get_question_health(after_reports[qid]) == "VALID", qid


def test_wave_a22a_targets_have_complete_scientific_contracts():
    definitions = build_definitions_from_registry(REGISTRY)
    for qid in WAVE_A2_2A_TARGETS:
        definition = definitions[qid]
        assert definition.definition_version == 1
        assert definition.lifecycle_status == QuestionLifecycle.ACTIVE
        assert definition.hypothesis.strip()
        assert definition.null_hypothesis.strip()
        assert definition.population_definition.strip()
        assert definition.metric_definition.strip()
        assert definition.evidence_authorities
        assert definition.join_contract is None
        assert definition.epoch_requirement == "CURRENT"
        assert definition.minimum_sample == 30
        assert definition.completion_rule.rule_type == "sample_reached"
        assert definition.completion_rule.threshold == 30


def test_wave_a22a_units_of_analysis_preserve_multi_account_boundary():
    definitions = build_definitions_from_registry(REGISTRY)
    s3 = definitions["S3"]
    s4 = definitions["S4"]
    risk1 = definitions["RISK-1"]

    assert "(canonical_opportunity_id, evaluated_horizon)" in s3.population_definition
    assert "Account-grained execution fanout is not part" in s3.population_definition
    assert "PRIMARY_HORIZON_SIMULATION" in s4.population_definition
    assert "one primary-horizon shadow lifecycle per canonical" in s4.population_definition
    assert "account-grained execution fanout are excluded" in s4.population_definition
    assert "one recorded closed trade, not one canonical decision" in risk1.population_definition
    assert "distinct closed-trade trade_ids" in risk1.population_definition
    assert "No strategy-observation count" in risk1.population_definition


def test_wave_a22a_existing_sufficiency_and_metric_semantics_are_exact():
    definitions = build_definitions_from_registry(REGISTRY)
    s3 = definitions["S3"]
    s4 = definitions["S4"]
    risk1 = definitions["RISK-1"]

    assert ">=10 outcomes" in s3.completion_rule.description
    assert "mean R > 0" in s3.metric_definition
    assert ">=2 phase cells with >=10 outcomes" in s4.completion_rule.description
    assert "Spread >=0.5R" in s4.metric_definition
    assert "classified loss records" in risk1.metric_definition
    assert "_MIN_SAMPLE=30" in risk1.completion_rule.description
    assert "_MIN_CELL=10" in risk1.completion_rule.description


def test_wave_a22a_preserves_a21_definitions(monkeypatch):
    before_all_a2 = _build_pre_a2_definitions(monkeypatch)
    after_a22a = apply_wave_a2_definitions(before_all_a2)

    # Reconstruct the A2.1 result from its unchanged override entries, then
    # compare every field with the cumulative A2 result.
    for qid in WAVE_A2_1_TARGETS:
        expected = before_all_a2[qid]
        if qid in WAVE_A2_OVERRIDES:
            expected = replace(expected, **WAVE_A2_OVERRIDES[qid])
        assert after_a22a[qid].to_dict() == expected.to_dict(), qid


def test_wave_a22a_keeps_m8_d2_x5_and_other_non_targets_unchanged(monkeypatch):
    before = _build_pre_a2_definitions(monkeypatch)
    after = apply_wave_a2_definitions(before)
    protected = {"M8", "D2", "X5"}
    for qid in protected:
        assert after[qid] is before[qid]
        assert after[qid].to_dict() == before[qid].to_dict()

    changed = {
        qid for qid in before
        if before[qid].to_dict() != after[qid].to_dict()
    }
    assert changed == WAVE_A2_RESOLVED


# ---------------------------------------------------------------------------
# WAVE A2.2b - L5 / HORIZON-1 / OPP-1 focused closure tests
# ---------------------------------------------------------------------------

def test_wave_a22b_before_to_after_health_is_fail_closed_where_required(monkeypatch):
    before = _build_pre_a2_definitions(monkeypatch)
    after = build_definitions_from_registry(REGISTRY)
    before_reports = validate_all_definitions(before)
    after_reports = validate_all_definitions(after)

    assert {
        qid: get_question_health(before_reports[qid])
        for qid in WAVE_A2_2B_TARGETS
    } == {qid: "UNDER_SPECIFIED" for qid in WAVE_A2_2B_TARGETS}
    assert get_question_health(after_reports["L5"]) == "VALID"
    assert get_question_health(after_reports["HORIZON-1"]) == "VALID"
    assert get_question_health(after_reports["OPP-1"]) == "UNDER_SPECIFIED"
    assert "OPP-1" not in WAVE_A2_OVERRIDES


def test_wave_a22b_resolved_contracts_are_complete_and_declarative():
    definitions = build_definitions_from_registry(REGISTRY)
    expected_samples = {"L5": 20, "HORIZON-1": 30}

    for qid, sample in expected_samples.items():
        definition = definitions[qid]
        assert definition.definition_version == 1
        assert definition.lifecycle_status == QuestionLifecycle.ACTIVE
        assert definition.hypothesis.strip()
        assert definition.null_hypothesis.strip()
        assert definition.population_definition.strip()
        assert definition.metric_definition.strip()
        assert definition.evidence_authorities
        assert definition.epoch_requirement == "CURRENT"
        assert definition.minimum_sample == sample
        assert definition.completion_rule.rule_type == "sample_reached"
        assert definition.completion_rule.threshold == sample

    assert ">=20 records" in definitions["L5"].completion_rule.description
    assert ">=30 comparable canonical opportunities" in (
        definitions["HORIZON-1"].completion_rule.description
    )


def test_l5_is_observational_and_does_not_claim_causal_improvement():
    definition = build_definitions_from_registry(REGISTRY)["L5"]

    assert "observational" in definition.hypothesis.lower()
    assert "descriptive historical association" in definition.metric_definition
    assert "does not establish that adaptation caused" in definition.metric_definition
    assert "(canonical_opportunity_id, evaluated_horizon)" in (
        definition.population_definition
    )
    assert "account-grained execution fanout is absent" in (
        definition.population_definition
    )


def test_horizon1_unit_is_one_opportunity_with_repeated_simulations():
    definition = build_definitions_from_registry(REGISTRY)["HORIZON-1"]

    assert "one comparable canonical opportunity" in definition.population_definition
    assert "repeated counterfactual measures within that unit" in (
        definition.population_definition
    )
    assert "Actual executions and account fanout are excluded" in (
        definition.population_definition
    )
    assert definition.join_contract.join_keys == ("canonical_opportunity_id",)
    assert definition.join_contract.cardinality == "one_to_many"
    assert "legacy opportunity_id" in definition.join_contract.description
    assert "0.05R" in definition.metric_definition


def test_opp1_missing_outcome_zero_contamination_remains_fail_closed():
    from research_engine.experiments.opportunity_selection import run_opp_1

    opportunities = [
        {"opportunity_id": f"local-{i}", "canonical_opportunity_id": f"canon-{i}"}
        for i in range(10)
    ]
    assessments = [
        {"opportunity_id": f"local-{i}", "score_strategy": 0.9}
        for i in range(10)
    ]
    horizon_candidates = [
        {
            "canonical_opportunity_id": f"canon-{i}",
            "selection_status": "SELECTED" if i % 2 == 0 else "REJECTED",
        }
        for i in range(10)
    ]
    shadows = [
        {
            "canonical_opportunity_id": f"canon-{i}",
            "simulated_outcome": {"pnl_r_multiple": 1.0},
        }
        for i in range(5)
    ]

    report = run_opp_1(
        opportunities=opportunities,
        assessments=assessments,
        horizon_candidates=horizon_candidates,
        shadow_trades=shadows,
    )
    high_bucket = report["overall"]["score_bucket_outcomes"]["HIGH_0.80+"]

    assert report["overall"]["opportunities_with_outcome"] == 5
    assert high_bucket["n"] == 10
    assert high_bucket["mean_r"] == 0.5
    assert "converts a missing outcome to 0.0" in (
        WAVE_A2_UNRESOLVED_REASONS["OPP-1"]
    )
    assert not build_definitions_from_registry(REGISTRY)["OPP-1"].hypothesis


def test_opp1_canonical_and_legacy_join_conflict_is_explicit():
    reason = WAVE_A2_UNRESOLVED_REASONS["OPP-1"]

    assert "horizon-candidate promotion and shadow outcomes by " \
           "canonical_opportunity_id" in reason
    assert "assessments by legacy opportunity_id" in reason
    assert "opportunity_id as a canonical-ID fallback" in reason
    assert "single canonical root join" in reason


def test_wave_a22b_preserves_all_prior_a2_and_non_target_definitions(monkeypatch):
    before_all_a2 = _build_pre_a2_definitions(monkeypatch)
    after = apply_wave_a2_definitions(before_all_a2)
    prior_targets = WAVE_A2_1_TARGETS | WAVE_A2_2A_TARGETS

    for qid in prior_targets:
        expected = before_all_a2[qid]
        if qid in WAVE_A2_OVERRIDES:
            expected = replace(expected, **WAVE_A2_OVERRIDES[qid])
        assert after[qid].to_dict() == expected.to_dict(), qid

    for qid in ("M8", "D2", "X5"):
        assert after[qid] is before_all_a2[qid]
        assert after[qid].to_dict() == before_all_a2[qid].to_dict()

    changed = {
        qid for qid in before_all_a2
        if before_all_a2[qid].to_dict() != after[qid].to_dict()
    }
    assert changed == WAVE_A2_RESOLVED


# ---------------------------------------------------------------------------
# WAVE A3.1 - M3 / M7 / P1 focused fail-closed assessment tests
# ---------------------------------------------------------------------------

from research_engine.registry.wave_a3_definitions import (  # noqa: E402
    WAVE_A3_1_TARGETS,
    WAVE_A3_2_TARGETS,
    WAVE_A3_3_TARGETS,
    WAVE_A3_OVERRIDES,
    WAVE_A3_RESOLVED,
    WAVE_A3_TARGETS,
    WAVE_A3_UNRESOLVED,
    WAVE_A3_UNRESOLVED_REASONS,
    apply_wave_a3_definitions,
)


def _build_pre_a3_definitions(monkeypatch):
    """Build the committed A1/A2 state, before A3/RW2/RW3 applications."""
    import research_engine.registry.wave_a3_definitions as wave_a3
    import research_engine.registry.rw2_definitions as rw2

    disabled = ((wave_a3, "apply_wave_a3_definitions"), (rw2, "apply_rw2_definitions")) + _rw3_repair_modules()
    originals = [(module, attr, getattr(module, attr)) for module, attr in disabled]
    for module, attr in disabled:
        monkeypatch.setattr(module, attr, lambda definitions: definitions)
    before = build_definitions_from_registry(REGISTRY)
    for module, attr, original in originals:
        monkeypatch.setattr(module, attr, original)
    return before


def test_wave_a31_scope_before_and_after_health_are_exact(monkeypatch):
    assert WAVE_A3_1_TARGETS == {"M3", "M7", "P1"}
    assert WAVE_A3_2_TARGETS == {"R3", "R4", "R5"}
    assert WAVE_A3_3_TARGETS == {"EX9", "D2", "X5"}
    assert WAVE_A3_TARGETS == (
        WAVE_A3_1_TARGETS | WAVE_A3_2_TARGETS | WAVE_A3_3_TARGETS
    )
    assert WAVE_A3_RESOLVED == set()
    assert WAVE_A3_UNRESOLVED == WAVE_A3_TARGETS
    assert WAVE_A3_OVERRIDES == {}
    assert set(WAVE_A3_UNRESOLVED_REASONS) == WAVE_A3_UNRESOLVED

    before = _build_pre_a3_definitions(monkeypatch)
    after = build_definitions_from_registry(REGISTRY)
    before_reports = validate_all_definitions(before)
    after_reports = validate_all_definitions(after)

    for qid in WAVE_A3_TARGETS:
        assert get_question_health(before_reports[qid]) == "UNDER_SPECIFIED", qid
        # M3/M7 (RW2), D2 (RW3.1) and X5 (RW3.5) are repaired and now VALID.
        if qid in {"M3", "M7", "D2", "X5"}:
            assert get_question_health(after_reports[qid]) == "VALID", qid
            assert after[qid].hypothesis.strip(), qid
            assert after[qid].population_definition.strip(), qid
            assert after[qid].metric_definition.strip(), qid
        else:
            assert get_question_health(after_reports[qid]) == "UNDER_SPECIFIED", qid
            assert not after[qid].hypothesis.strip(), qid
            assert not after[qid].population_definition.strip(), qid
            assert not after[qid].metric_definition.strip(), qid


def test_m3_and_m7_predictive_claims_remain_fail_closed():
    m3_reason = WAVE_A3_UNRESOLVED_REASONS["M3"]
    m7_reason = WAVE_A3_UNRESOLVED_REASONS["M7"]

    assert "unweighted standard deviation" in m3_reason
    assert ">115%" in m3_reason
    assert "any positive number" in m3_reason
    assert "no scientifically sufficient minimum sample" in m3_reason
    assert "unweighted standard deviation" in m7_reason
    assert "observational, pooled comparison" in m7_reason
    assert "no predictive validation" in m7_reason
    assert "no scientifically sufficient minimum sample" in m7_reason
    assert "descriptive association" in m7_reason


def test_wave_a31_units_exclude_account_fanout_without_inventing_deduplication():
    m3_reason = WAVE_A3_UNRESOLVED_REASONS["M3"]
    m7_reason = WAVE_A3_UNRESOLVED_REASONS["M7"]
    p1_reason = WAVE_A3_UNRESOLVED_REASONS["P1"]

    assert "each record as an observation" in m3_reason
    assert "Account executions are not in" in m3_reason
    assert "no canonical-opportunity deduplication rule" in m3_reason
    assert "Each CURRENT completed shadow record is counted" in m7_reason
    assert "no account execution input" in m7_reason
    assert "unit is a completed shadow record" in p1_reason
    assert "account executions are absent" in p1_reason
    assert "multiple shadow simulations" in p1_reason


def test_p1_runner_registry_and_sufficiency_conflicts_are_explicit():
    reason = WAVE_A3_UNRESOLVED_REASONS["P1"]

    assert "shadow_trades plus decision_trace" in reason
    assert "reads only CURRENT shadow records" in reason
    assert "no decision_trace join" in reason
    assert "100 shadow records" in reason
    assert "95% outcome" in reason
    assert "80% entity lineage" in reason
    assert "50% canonical strategy" in reason
    assert "100 R outcomes" in reason
    assert ">=10 remaining trades" in reason
    assert "does not estimate promotion effects on drawdown or risk" in reason
    assert "in-sample scenarios" in reason
    assert "causal/predictive impact" in reason


def test_wave_a31_preserves_a1_a2_protected_and_all_non_targets(monkeypatch):
    before = _build_pre_a3_definitions(monkeypatch)
    after = apply_wave_a3_definitions(before)

    assert set(after) == set(before) == {question.id for question in REGISTRY}
    for qid in before:
        assert after[qid] is before[qid], qid
        assert after[qid].to_dict() == before[qid].to_dict(), qid

    for qid in ("M8", "OPP-1", "D2", "X5"):
        assert after[qid] is before[qid]
        assert after[qid].to_dict() == before[qid].to_dict()


# ---------------------------------------------------------------------------
# WAVE A3.2 - R3 / R4 / R5 focused fail-closed assessment tests
# ---------------------------------------------------------------------------

def test_wave_a32_before_to_after_health_remains_fail_closed(monkeypatch):
    before = _build_pre_a3_definitions(monkeypatch)
    after = build_definitions_from_registry(REGISTRY)
    before_reports = validate_all_definitions(before)
    after_reports = validate_all_definitions(after)

    for qid in WAVE_A3_2_TARGETS:
        assert get_question_health(before_reports[qid]) == "UNDER_SPECIFIED", qid
        assert get_question_health(after_reports[qid]) == "UNDER_SPECIFIED", qid
        assert qid in WAVE_A3_UNRESOLVED
        assert qid not in WAVE_A3_RESOLVED
        assert qid not in WAVE_A3_OVERRIDES
        assert not after[qid].hypothesis.strip(), qid
        assert not after[qid].population_definition.strip(), qid
        assert not after[qid].metric_definition.strip(), qid


def test_r3_assumptions_and_probability_semantics_are_explicitly_unresolved():
    reason = WAVE_A3_UNRESOLVED_REASONS["R3"]

    assert ">=50 records" in reason
    assert ">=95% outcome" in reason
    assert ">=80% entity-lineage" in reason
    assert "samples R outcomes independently with replacement" in reason
    assert "stationary identically distributed" in reason
    assert "fixed 1% additive risk" in reason
    assert "ignores the registry's measured position_size" in reason
    assert "50% peak drawdown" in reason
    assert "1/ruin_threshold (=2 capital units)" in reason
    assert "conditional scenario modelling" in reason
    assert "not a literal eventual-ruin probability forecast" in reason


def test_r4_ordering_drawdown_and_policy_semantics_are_explicitly_unresolved():
    reason = WAVE_A3_UNRESOLVED_REASONS["R4"]

    assert ">=50 records" in reason
    assert ">=95% outcome" in reason
    assert "additive 1%-risk synthetic equity curve" in reason
    assert "not account equity" in reason
    assert "sorts reconstructed lifecycles by shadow_trade_id" in reason
    assert "does not sort by entry_time" in reason
    assert "fixed threshold grid" in reason
    assert "defaults to 50%" in reason
    assert "not an optimal-policy test" in reason


def test_r5_model_set_and_optimisation_semantics_are_explicitly_unresolved():
    reason = WAVE_A3_UNRESOLVED_REASONS["R5"]

    assert ">=50 records" in reason
    assert ">=95% outcome" in reason
    assert "fixed 0.5%/1%/2%" in reason
    assert "Fixed-lot and dynamic models" in reason
    assert "stationary, independent, identically distributed" in reason
    assert "hard-coded 30% maximum drawdown" in reason
    assert "annualises by 252 observations" in reason
    assert "heuristic historical scenario comparison" in reason
    assert "not proven long-term optimisation" in reason


def test_a32_units_do_not_silently_use_multi_account_fanout():
    for qid in WAVE_A3_2_TARGETS:
        reason = WAVE_A3_UNRESOLVED_REASONS[qid]
        assert "completed shadow simulation record" in reason, qid
        assert "account executions are absent" in reason, qid
        assert "canonical-opportunity deduplication" in reason, qid
        assert "horizon" in reason, qid


def test_historical_risk_reports_and_unverified_reruns_are_non_authoritative():
    from research_engine.control_plane.models import ReportValidity
    from research_engine.control_plane.report_resolver import resolve_report_validity

    invalidated = {
        "R3": ("r3_probability_of_ruin.json", 100),
        "R4": ("r4_drawdown_threshold.json", 901),
        "R5": ("r5_position_sizing.json", 901),
    }
    for qid, (filename, records_used) in invalidated.items():
        old_report = {
            "question_id": qid,
            "status": "COMPLETE",
            "fingerprint": {
                "dataset_id": "shadow_trades_2026-07-27",
                "records_used": records_used,
                "epoch": "CURRENT",
            },
        }
        validity, _ = resolve_report_validity(
            filename, old_report, expected_question_id=qid
        )
        assert validity == ReportValidity.INVALIDATED, qid

        unverified_report = {
            "question_id": qid,
            "status": "COMPLETE",
            "fingerprint": {
                "dataset_id": "shadow_trades_future_current_rerun",
                "records_used": 100,
                "epoch": "UNVERIFIED",
            },
        }
        validity, _ = resolve_report_validity(
            filename, unverified_report, expected_question_id=qid
        )
        assert validity == ReportValidity.STALE, qid
        assert "default to UNVERIFIED" in WAVE_A3_UNRESOLVED_REASONS[qid]


def test_wave_a32_preserves_a31_earlier_waves_and_every_non_target(monkeypatch):
    before = _build_pre_a3_definitions(monkeypatch)
    after = apply_wave_a3_definitions(before)

    for qid in before:
        assert after[qid] is before[qid], qid
        assert after[qid].to_dict() == before[qid].to_dict(), qid

    protected = {"M3", "M7", "P1", "M8", "OPP-1", "D2", "X5"}
    for qid in protected:
        assert after[qid] is before[qid]
        assert after[qid].to_dict() == before[qid].to_dict()


# ---------------------------------------------------------------------------
# WAVE A3.3 - EX9 / D2 / X5 focused fail-closed assessment tests
# ---------------------------------------------------------------------------

def test_wave_a33_before_to_after_health_remains_fail_closed(monkeypatch):
    before = _build_pre_a3_definitions(monkeypatch)
    after = build_definitions_from_registry(REGISTRY)
    before_reports = validate_all_definitions(before)
    after_reports = validate_all_definitions(after)

    for qid in WAVE_A3_3_TARGETS:
        assert get_question_health(before_reports[qid]) == "UNDER_SPECIFIED", qid
        assert qid in WAVE_A3_UNRESOLVED
        assert qid not in WAVE_A3_RESOLVED
        assert qid not in WAVE_A3_OVERRIDES
        if qid in ("D2", "X5"):
            # D2 (RW3.1) and X5 (RW3.5) are repaired and now VALID.
            assert get_question_health(after_reports[qid]) == "VALID", qid
            assert after[qid].hypothesis.strip(), qid
            assert after[qid].population_definition.strip(), qid
            assert after[qid].metric_definition.strip(), qid
        else:
            # EX9 remains fail-closed / under-specified.
            assert get_question_health(after_reports[qid]) == "UNDER_SPECIFIED", qid
            assert not after[qid].hypothesis.strip(), qid
            assert not after[qid].population_definition.strip(), qid
            assert not after[qid].metric_definition.strip(), qid


def test_ex9_counterfactual_and_sufficiency_conflicts_are_explicit():
    reason = WAVE_A3_UNRESOLVED_REASONS["EX9"]

    assert "observational description" in reason
    assert "does not simulate any proposed policy" in reason
    assert "ordered trade_state_progression" in reason
    assert ">=200 records" in reason
    assert ">=30 total records" in reason
    assert ">=10 timeout records" in reason
    assert "requires MAE" in reason
    assert "one aligned total/cell sufficiency contract" in reason


def test_ex9_shadow_lifecycle_unit_excludes_account_fanout():
    reason = WAVE_A3_UNRESOLVED_REASONS["EX9"]

    assert "one completed shadow lifecycle" in reason
    assert "shadow_trade_id plus canonical_opportunity_id and evaluated horizon" in reason
    assert "not an account execution" in reason
    assert "Account fanout cannot inflate it" in reason
    assert "multiple horizon simulations" in reason


def test_d2_probability_authority_does_not_bless_unpaired_calibration():
    reason = WAVE_A3_UNRESOLVED_REASONS["D2"]

    assert "ProbabilityEstimator score_v1" in reason
    assert "pre-decision heuristic estimate" in reason
    assert "decision_trace persists only the numeric p_success" in reason
    assert "does not persist that provenance" in reason
    assert "reads the literal p_success field" in reason
    assert "not confidence/score aliases" in reason
    assert "never joins a prediction to its outcome" in reason
    assert "defining success as pnl_r_multiple > 0" in reason
    assert "missing pnl_r_multiple can default to 0" in reason
    assert ">=20 shadow outcomes but only one prediction" in reason
    assert "no probability-bin or proper calibration analysis" in reason


def test_x5_ev_authority_does_not_bless_unit_or_alias_mismatch():
    reason = WAVE_A3_UNRESOLVED_REASONS["X5"]

    assert "computed pre-decision" in reason
    assert "instrument-price units" in reason
    assert "not expected R or money P&L" in reason
    assert "not an EV semantic/model version" in reason
    assert "inline probability fallback" in reason
    assert "expected_value, expectancy, predicted_r" in reason
    assert "aliases do not supply the missing authority" in reason
    assert "canonical_opportunity_id" in reason
    assert ">=30 pairs" in reason
    assert "subtracts mean realised R from mean price-distance EV" in reason
    assert "units were commensurate" in reason


def test_d2_x5_prediction_populations_do_not_silently_absorb_account_fanout():
    d2_reason = WAVE_A3_UNRESOLVED_REASONS["D2"]
    x5_reason = WAVE_A3_UNRESOLVED_REASONS["X5"]

    assert "canonical-opportunity deduplication" in d2_reason
    assert "Account executions are absent" in d2_reason
    assert "multiple shadow horizons" in d2_reason
    assert "multi-account fanout produces multiple trade_truth outcomes" in x5_reason
    assert "excludes a canonical opportunity" in x5_reason
    assert "does not deduplicate repeated decision_trace rows" in x5_reason
    assert "canonical-decision/account aggregation contract" in x5_reason


def test_wave_a33_preserves_every_definition_and_prior_unresolved_targets(monkeypatch):
    before = _build_pre_a3_definitions(monkeypatch)
    after = apply_wave_a3_definitions(before)

    assert set(after) == set(before) == {question.id for question in REGISTRY}
    for qid in before:
        assert after[qid] is before[qid], qid
        assert after[qid].to_dict() == before[qid].to_dict(), qid

    protected = {
        "M3", "M7", "P1", "R3", "R4", "R5", "M8", "OPP-1"
    }
    for qid in protected:
        assert after[qid] is before[qid]
        assert after[qid].to_dict() == before[qid].to_dict()


# ---------------------------------------------------------------------------
# WAVE A4.1 - M1 / M11 / X3 / EXEC1 semantic-alignment tests
# ---------------------------------------------------------------------------

from research_engine.registry.wave_a4_definitions import (  # noqa: E402
    WAVE_A4_1_TARGETS,
    WAVE_A4_2_TARGETS,
    WAVE_A4_3_TARGETS,
    WAVE_A4_4_RESEARCH_CLASSIFICATIONS,
    WAVE_A4_4_TARGETS,
    WAVE_A4_OVERRIDES,
    WAVE_A4_RESOLVED,
    WAVE_A4_TARGETS,
    WAVE_A4_UNRESOLVED,
    WAVE_A4_UNRESOLVED_REASONS,
    apply_wave_a4_definitions,
)


def _build_pre_a4_definitions(monkeypatch):
    """Build the current A1/A2/A3 state with A4 application disabled."""
    import research_engine.registry.wave_a4_definitions as wave_a4
    import research_engine.registry.rw2_definitions as rw2

    original = wave_a4.apply_wave_a4_definitions
    original_rw2 = rw2.apply_rw2_definitions
    monkeypatch.setattr(
        wave_a4, "apply_wave_a4_definitions", lambda definitions: definitions
    )
    monkeypatch.setattr(rw2, "apply_rw2_definitions", lambda definitions: definitions)
    before = build_definitions_from_registry(REGISTRY)
    monkeypatch.setattr(wave_a4, "apply_wave_a4_definitions", original)
    monkeypatch.setattr(rw2, "apply_rw2_definitions", original_rw2)
    return before


def test_wave_a41_scope_and_before_to_after_health_are_exact(monkeypatch):
    assert WAVE_A4_1_TARGETS == {"M1", "M11", "X3", "EXEC1"}
    assert WAVE_A4_2_TARGETS == {"D3", "D4", "D5"}
    assert WAVE_A4_3_TARGETS == {"L1", "L2", "L3", "L4"}
    assert WAVE_A4_4_TARGETS == {"EX5", "EX6", "EX7", "EX8"}
    assert WAVE_A4_TARGETS == (
        WAVE_A4_1_TARGETS | WAVE_A4_2_TARGETS | WAVE_A4_3_TARGETS
        | WAVE_A4_4_TARGETS
    )
    assert WAVE_A4_RESOLVED == set()
    assert WAVE_A4_UNRESOLVED == WAVE_A4_TARGETS
    assert WAVE_A4_OVERRIDES == {}
    assert set(WAVE_A4_UNRESOLVED_REASONS) == WAVE_A4_UNRESOLVED

    before = _build_pre_a4_definitions(monkeypatch)
    after = build_definitions_from_registry(REGISTRY)
    before_reports = validate_all_definitions(before)
    after_reports = validate_all_definitions(after)

    for qid in WAVE_A4_1_TARGETS:
        if qid in {"M1", "M11"}:
            assert get_question_health(before_reports[qid]) == "UNDER_SPECIFIED", qid
            assert get_question_health(after_reports[qid]) == "VALID", qid
            assert after[qid].hypothesis.strip(), qid
            assert after[qid].population_definition.strip(), qid
            assert after[qid].metric_definition.strip(), qid
        else:
            assert get_question_health(before_reports[qid]) == "SEMANTIC_MISMATCH", qid
            assert get_question_health(after_reports[qid]) == "SEMANTIC_MISMATCH", qid
            assert not after[qid].hypothesis.strip(), qid
            assert not after[qid].population_definition.strip(), qid
            assert not after[qid].metric_definition.strip(), qid


def test_m1_remains_descriptive_not_predictive_and_has_no_account_fanout():
    reason = WAVE_A4_UNRESOLVED_REASONS["M1"]

    assert "does not relate regime to outcomes at all" in reason
    assert "reports only regime frequency" in reason
    assert "no deterministic trace-to-outcome join" in reason
    assert "COMPLETE requires merely one shadow outcome" in reason
    assert "no sample threshold" in reason
    assert "Account execution fanout is not an input" in reason
    assert "repeated horizon simulations" in reason
    assert "descriptive regime-frequency reporting" in reason
    assert "not associative or predictive" in reason


def test_m11_dispersion_proxy_remains_associative_not_predictive():
    reason = WAVE_A4_UNRESOLVED_REASONS["M11"]

    assert "loads only shadow trades" in reason
    assert "no decision_trace join" in reason
    assert "compatibility fields" in reason
    assert "bias is not a registry required field" in reason
    assert "one shadow lifecycle is one observation" in reason
    assert "multiple horizon simulations" in reason
    assert "unweighted standard deviation" in reason
    assert "exceeds pattern dispersion by 15%" in reason
    assert "no per-cell minimum" in reason
    assert "or predictive score" in reason
    assert "pooled descriptive association" in reason


def test_x3_preserves_account_grain_nested_session_and_measured_slippage():
    from research_engine.experiments.execution_protection_research import (
        _extract_context,
        _extract_result,
        join_context_to_results,
    )

    context = {
        "correlation_id": "corr-1",
        "canonical_opportunity_id": "opp-1",
        "symbol": "EURUSD",
        "market_access": {"session_state": "LONDON", "spread": 0.0001},
    }
    results = [
        {
            "correlation_id": "corr-1",
            "canonical_opportunity_id": "opp-1",
            "symbol": "EURUSD",
            "account_id": account,
            "slippage": slippage,
            "slippage_semantic": "measured_execution_slippage",
        }
        for account, slippage in (("METAQUOTES", 0.00001), ("VANTAGE", 0.00002))
    ]
    joined = join_context_to_results(results, [context])

    assert len(joined["matched"]) == 2
    assert {p["result"]["account_id"] for p in joined["matched"]} == {
        "METAQUOTES", "VANTAGE"
    }
    assert {p["context"]["session_state"] for p in joined["matched"]} == {"LONDON"}
    assert _extract_context(context)["session_state"] == "LONDON"

    derived = _extract_result({
        "correlation_id": "corr-2",
        "account_id": "METAQUOTES",
        "entry_reference": 1.1000,
        "fill_price": 1.1002,
    })
    assert derived["slippage"] is None
    assert derived["slippage_provenance"] == "derived_compatibility"
    assert derived["derived_compatibility_slippage"] > 0

    reason = WAVE_A4_UNRESOLVED_REASONS["X3"]
    assert "does not compute rejection or failure rates" in reason
    assert ">=30 matched observations" in reason
    assert ">=10 observations" in reason
    assert "descriptive account-execution slippage" in reason


def test_exec1_keeps_execution_results_primary_and_protection_separate():
    from research_engine.registry import DataSource, REGISTRY_BY_ID

    question = REGISTRY_BY_ID["EXEC1"]
    assert question.data_sources == (DataSource.EXECUTION_RESULTS,)
    assert DataSource.PROTECTION_AUDIT not in question.data_sources
    assert DataSource.EXECUTION_ATTEMPTS not in question.data_sources

    reason = WAVE_A4_UNRESOLVED_REASONS["EXEC1"]
    assert "CURRENT execution_results_v1 as the primary population" in reason
    assert "does not use protection_audit" in reason
    assert "execution_attempts" in reason
    assert "Each account-grained execution result" in reason
    assert "different account outcomes for one canonical decision" in reason
    assert "missing result_ok defaults to failure" in reason
    assert "populated with result slippage rather than context spread" in reason
    assert "No valid-opportunity or outcome evidence is joined" in reason
    assert ">=30 result rows" in reason
    assert "per-symbol cells require >=10" in reason
    assert "descriptive execution-result reliability" in reason


def test_wave_a41_preserves_prior_protected_later_and_all_non_targets(monkeypatch):
    before = _build_pre_a4_definitions(monkeypatch)
    after = apply_wave_a4_definitions(before)

    assert set(after) == set(before) == {question.id for question in REGISTRY}
    for qid in before:
        assert after[qid] is before[qid], qid
        assert after[qid].to_dict() == before[qid].to_dict(), qid

    protected = {
        "M8", "OPP-1", "M3", "M7", "P1", "R3", "R4", "R5",
        "EX9", "D2", "X5",
    }
    later_a4 = {
        "D3", "D4", "D5", "L1", "L2", "L3", "L4",
        "EX5", "EX6", "EX7", "EX8",
    }
    for qid in protected | later_a4:
        assert after[qid] is before[qid]
        assert after[qid].to_dict() == before[qid].to_dict()


# ---------------------------------------------------------------------------
# WAVE A4.2 - D3 / D4 / D5 semantic-alignment tests
# ---------------------------------------------------------------------------

def test_wave_a42_before_to_after_health_remains_fail_closed(monkeypatch):
    before = _build_pre_a4_definitions(monkeypatch)
    after = build_definitions_from_registry(REGISTRY)
    before_reports = validate_all_definitions(before)
    after_reports = validate_all_definitions(after)

    # D3 (RW3.2), D4 (RW3.3) and D5 (RW3.4) were repaired and are now VALID.
    # All of WAVE_A4_2_TARGETS are repaired, so this fail-closed loop is empty;
    # the assertions below still guard any future unrepaired A4.2 target.
    for qid in WAVE_A4_2_TARGETS - {"D3", "D4", "D5"}:
        assert get_question_health(before_reports[qid]) == "SEMANTIC_MISMATCH", qid
        assert get_question_health(after_reports[qid]) == "SEMANTIC_MISMATCH", qid
        assert qid in WAVE_A4_UNRESOLVED
        assert qid not in WAVE_A4_RESOLVED
        assert qid not in WAVE_A4_OVERRIDES
        assert not after[qid].hypothesis.strip(), qid
        assert not after[qid].population_definition.strip(), qid
        assert not after[qid].metric_definition.strip(), qid


def test_d3_price_distance_ev_and_policy_authority_remain_fail_closed():
    from research_engine.control_plane.evidence_resolver import (
        normalise_evidence_record,
    )

    reason = WAVE_A4_UNRESOLVED_REASONS["D3"]
    assert "never consumes ev, policy_trade_allowed, decision_trace" in reason
    assert "decision_snapshot.score is >=0.45" in reason
    assert "comparative price-distance heuristic" in reason
    assert "not expected R or monetary P&L" in reason
    assert "semantic version" in reason
    assert "multi-gate ExecutionPolicy" in reason
    assert "not an EV-gate-only treatment indicator" in reason
    assert "DecisionTrace does not persist that field" in reason
    assert ">=20 shadow records" in reason
    assert "descriptive score-threshold coverage" in reason
    assert "not EV calibration or policy evaluation" in reason

    compatibility = normalise_evidence_record(
        {"expected_value": 0.25, "trade_allowed": True}, "decision_trace"
    )
    assert "ev" not in compatibility
    assert "policy_trade_allowed" not in compatibility


def test_d4_ambiguous_scores_cannot_establish_predictive_threshold_authority():
    from research_engine.control_plane.evidence_resolver import (
        normalise_evidence_record,
    )

    reason = WAVE_A4_UNRESOLVED_REASONS["D4"]
    assert "literal decision_snapshot.score" in reason
    assert "neither selects an optimum" in reason
    assert "nor groups outcomes by regime or market_state" in reason
    assert "score_neutral and regime only" in reason
    assert "no trace-to-shadow join" in reason
    assert "competing unpaired score authorities" in reason
    assert "score, score_strategy, overall_score, or signal_score" in reason
    assert "confidence and p_success are distinct fields" in reason
    assert "Score and regime inputs are pre-decision" in reason
    assert "shadow R is post-outcome" in reason
    assert "missing shadow pnl_r_multiple defaults to zero" in reason
    assert ">=20 shadow records" in reason
    assert "descriptive pooled threshold filtering" in reason

    for alias in ("score_strategy", "overall_score", "signal_score"):
        normalised = normalise_evidence_record({alias: 0.7}, "decision_trace")
        assert normalised["score"] == 0.7
    assert "score" not in normalise_evidence_record(
        {"confidence": 0.7, "p_success": 0.7}, "decision_trace"
    )


def test_d3_d4_decision_samples_exclude_accounts_but_not_repeated_horizons():
    for qid in ("D3", "D4"):
        reason = WAVE_A4_UNRESOLVED_REASONS[qid]
        assert "completed shadow simulation" in reason, qid
        assert "canonical opportunit" in reason.replace("-", " "), qid
        assert "account" in reason.lower(), qid
        assert "repeated horizon" in reason, qid


def test_d5_rejection_and_counterfactual_authorities_remain_distinct():
    reason = WAVE_A4_UNRESOLVED_REASONS["D5"]

    assert "action == NO_TRADE" in reason
    assert "decision_trace only" in reason
    assert "does not load shadow evidence" in reason
    assert "join canonical_opportunity_id/entity_id" in reason
    assert "distinguish actual realised trades from hypothetical evidence" in reason
    assert "PATTERN_REJECT, NO_TRADE, and RISK_BLOCK" in reason
    assert "not given an authoritative equivalence" in reason
    assert "Rejection status and reason are pre-outcome" in reason
    assert "no subsequent label" in reason
    assert "COMPLETE requires just one matching trace" in reason
    assert "zero broker executions" in reason
    assert "repeated measures within one canonical opportunity" in reason
    assert "not independent rejected opportunities" in reason
    assert "descriptive rejection-funnel reporting" in reason
    assert "not counterfactual missed-opportunity research" in reason


def test_wave_a42_preserves_a41_prior_waves_protected_and_later_targets(monkeypatch):
    before = _build_pre_a4_definitions(monkeypatch)
    after = apply_wave_a4_definitions(before)

    assert set(after) == set(before) == {question.id for question in REGISTRY}
    for qid in before:
        assert after[qid] is before[qid], qid
        assert after[qid].to_dict() == before[qid].to_dict(), qid

    protected = {
        "M1", "M11", "X3", "EXEC1", "M8", "OPP-1", "M3", "M7",
        "P1", "R3", "R4", "R5", "EX9", "D2", "X5",
    }
    later_a4 = {"L1", "L2", "L3", "L4", "EX5", "EX6", "EX7", "EX8"}
    for qid in protected | later_a4:
        assert after[qid] is before[qid]
        assert after[qid].to_dict() == before[qid].to_dict()


# ---------------------------------------------------------------------------
# WAVE A4.3 - L1 / L2 / L3 / L4 semantic-alignment tests
# ---------------------------------------------------------------------------

def test_wave_a43_before_to_after_health_remains_fail_closed(monkeypatch):
    before = _build_pre_a4_definitions(monkeypatch)
    after = build_definitions_from_registry(REGISTRY)
    before_reports = validate_all_definitions(before)
    after_reports = validate_all_definitions(after)

    for qid in WAVE_A4_3_TARGETS:
        assert get_question_health(before_reports[qid]) == "SEMANTIC_MISMATCH", qid
        assert get_question_health(after_reports[qid]) == "SEMANTIC_MISMATCH", qid
        assert qid in WAVE_A4_UNRESOLVED
        assert qid not in WAVE_A4_RESOLVED
        assert qid not in WAVE_A4_OVERRIDES
        assert not after[qid].hypothesis.strip(), qid
        assert not after[qid].population_definition.strip(), qid
        assert not after[qid].metric_definition.strip(), qid


def test_l1_is_pooled_pattern_performance_not_temporal_learning():
    from research_engine.registry import REGISTRY_BY_ID

    l1 = REGISTRY_BY_ID["L1"]
    e2 = REGISTRY_BY_ID["E2"]
    reason = WAVE_A4_UNRESOLVED_REASONS["L1"]

    assert "never reads entry_time" in reason
    assert "early/late or rolling windows" in reason
    assert ">=5 observations" in reason
    assert "COMPLETE whenever any shadow outcome exists" in reason
    assert "no timestamp" in reason
    assert "repeated horizon simulations" in reason
    assert "pooled descriptive pattern performance" in reason
    assert "not temporal drift, learning, adaptation, or causal improvement" in reason
    assert l1.runner_module == e2.runner_module
    assert l1.runner_function == e2.runner_function == "run_q05"
    assert l1.report_filename == e2.report_filename == "q5_pattern_degradation.json"
    assert l1.description != e2.description
    assert "Q5" in l1.legacy_ids and "Q5" in e2.legacy_ids
    assert "same artifact as authority for either claim" in reason


def test_l2_report_count_has_no_valid_chronology_or_adaptation_semantics():
    reason = WAVE_A4_UNRESOLVED_REASONS["L2"]

    assert "reads none of those fields or shadow evidence" in reason
    assert "counts JSON files" in reason
    assert "always declares COMPLETE" in reason
    assert "zero reports" in reason
    assert "neither an architecture-change boundary nor pre/post outcome windows" in reason
    assert "File count and filesystem enumeration are not authoritative chronology" in reason
    assert "do not demonstrate temporal improvement, system adaptation, or causal benefit" in reason
    assert "no validation or minimum-sample rule" in reason
    assert "Account fanout is not consumed" in reason


def test_l3_cannot_inherit_d1_component_report_as_architecture_authority():
    from research_engine.registry import REGISTRY_BY_ID

    l3 = REGISTRY_BY_ID["L3"]
    d1 = REGISTRY_BY_ID["D1"]
    reason = WAVE_A4_UNRESOLVED_REASONS["L3"]

    assert l3.runner_module == d1.runner_module
    assert l3.runner_function == d1.runner_function == "run"
    assert l3.report_filename == d1.report_filename == "q1_component_reward.json"
    assert l3.description != d1.description
    assert "Q1" in l3.legacy_ids and "Q1" in d1.legacy_ids
    assert "never tests regime classification or strategy-mapping correctness" in reason
    assert "One matched decision trace/shadow outcome" in reason
    assert "account executions are absent" in reason
    assert ">=5 matched outcomes" in reason
    assert ">=3 for interactions" in reason
    assert "declares COMPLETE when merely one matched outcome exists" in reason
    assert "descriptive/associative component attribution" in reason
    assert "one artifact can falsely establish report ownership/completion for L3" in reason


def test_l4_trade_truth_count_is_not_market_drift_or_learning():
    reason = WAVE_A4_UNRESOLVED_REASONS["L4"]

    assert "loads trade_truth only" in reason
    assert "does not read market_context or shadow evidence" in reason
    assert "computes no regime, pattern, outcome, temporal, drift" in reason
    assert "only the number of trade-truth rows" in reason
    assert "q17_drawdown_precursors.json" in reason
    assert "chronology is undefined" in reason
    assert "account-level live outcome evidence" in reason
    assert "multi-account fanout could inflate" in reason
    assert "requires only one row" in reason
    assert "not temporal drift, adaptation, or causal improvement research" in reason


def test_wave_a43_preserves_prior_protected_e2_d1_later_and_all_non_targets(monkeypatch):
    before = _build_pre_a4_definitions(monkeypatch)
    after = apply_wave_a4_definitions(before)

    assert set(after) == set(before) == {question.id for question in REGISTRY}
    for qid in before:
        assert after[qid] is before[qid], qid
        assert after[qid].to_dict() == before[qid].to_dict(), qid

    protected = {
        "M1", "M11", "X3", "EXEC1", "D3", "D4", "D5", "M8",
        "OPP-1", "M3", "M7", "P1", "R3", "R4", "R5", "EX9",
        "D2", "X5", "E2", "D1", "EX5", "EX6", "EX7", "EX8",
    }
    for qid in protected:
        assert after[qid] is before[qid]
        assert after[qid].to_dict() == before[qid].to_dict()


# ---------------------------------------------------------------------------
# WAVE A4.4 - EX5 / EX6 / EX7 / EX8 semantic-alignment tests
# ---------------------------------------------------------------------------

def test_wave_a44_before_to_after_health_remains_fail_closed(monkeypatch):
    before = _build_pre_a4_definitions(monkeypatch)
    after = build_definitions_from_registry(REGISTRY)
    before_reports = validate_all_definitions(before)
    after_reports = validate_all_definitions(after)

    assert WAVE_A4_4_TARGETS == {"EX5", "EX6", "EX7", "EX8"}
    assert WAVE_A4_4_RESEARCH_CLASSIFICATIONS == {
        "EX5": "associative",
        "EX6": "associative",
        "EX7": "descriptive",
        "EX8": "descriptive",
    }
    for qid in WAVE_A4_4_TARGETS:
        assert get_question_health(before_reports[qid]) == "SEMANTIC_MISMATCH", qid
        assert get_question_health(after_reports[qid]) == "SEMANTIC_MISMATCH", qid
        assert qid in WAVE_A4_UNRESOLVED
        assert qid not in WAVE_A4_RESOLVED
        assert qid not in WAVE_A4_OVERRIDES
        assert not after[qid].hypothesis.strip(), qid
        assert not after[qid].null_hypothesis.strip(), qid
        assert not after[qid].population_definition.strip(), qid
        assert not after[qid].metric_definition.strip(), qid
        assert after[qid].minimum_sample is None, qid
        assert after[qid].completion_rule is None, qid


def test_ex5_is_horizon_association_not_trailing_policy_evaluation():
    reason = WAVE_A4_UNRESOLVED_REASONS["EX5"]

    assert "does not test or simulate a trailing rule" in reason
    assert "observed shadow realised R" in reason
    assert "literal mapped shadow exit-reason counts" in reason
    assert "at least two available horizon means span >0.15" in reason
    assert "in-sample observational association, not policy evaluation" in reason
    assert "trade_state_progression and entry/exit timestamps are not consumed" in reason
    assert ">=30" in reason and ">=10 per reported cell" in reason
    assert "declares COMPLETE when any one cell remains" in reason


def test_ex6_historical_exit_categories_are_not_causal_policy_effects():
    reason = WAVE_A4_UNRESOLVED_REASONS["EX6"]

    assert "REVERSAL, CONTINUATION, and FALSE_BREAK" in reason
    assert "REVERSAL, MOMENTUM, and CONTINUATION" in reason
    assert "historical shadow categories, not selectable treatments" in reason
    assert "no alternative policy behaviour is simulated" in reason
    assert "no causal or policy effect" in reason
    assert "post-outcome diagnostics" in reason
    assert "cannot be pre-exit predictive inputs" in reason


def test_ex7_requires_ordered_path_authority_for_alternative_exit_claims():
    reason = WAVE_A4_UNRESOLVED_REASONS["EX7"]

    assert "performs no between-regime comparison" in reason
    assert "candidate-rule simulation, predictive validation, or policy evaluation" in reason
    assert "forward-appended per-bar trade_state_progression" in reason
    assert "consumes neither timestamps nor the path" in reason
    assert "post-outcome facts" in reason
    assert "neither when an alternative exit was feasible nor what it would have realised" in reason
    assert "ordered, field-audited path authority" in reason


def test_ex8_count_ranking_is_not_best_or_optimal_exit_policy():
    reason = WAVE_A4_UNRESOLVED_REASONS["EX8"]

    assert "ranked by sample count, not exit performance" in reason
    assert "does not identify a best or optimal policy" in reason
    assert "compare candidate rules" in reason
    assert "define an evaluation design" in reason
    assert "leakage-safe pre-exit information" in reason
    assert "optimality criterion" in reason


def test_a44_preserves_actual_shadow_hypothetical_and_sample_grains():
    for qid in WAVE_A4_4_TARGETS:
        reason = WAVE_A4_UNRESOLVED_REASONS[qid]
        lower_reason = reason.lower()
        assert "Account execution" in reason or "account fanout" in reason
        assert "canonical opportunity" in reason
        assert "repeated horizon" in reason or "multiple horizon" in reason
        assert "actual broker" in lower_reason
        assert "shadow" in lower_reason
        assert "candidate-policy" in lower_reason or "hypothetical" in lower_reason


def test_a44_runner_and_resolver_population_authorities_remain_distinct():
    for qid in WAVE_A4_4_TARGETS:
        reason = WAVE_A4_UNRESOLVED_REASONS[qid]
        assert "shadow_runtime_v1" in reason
        assert "research_shadow_trades" in reason


def test_wave_a44_preserves_all_non_targets_prior_waves_and_protected(monkeypatch):
    before = _build_pre_a4_definitions(monkeypatch)
    after = apply_wave_a4_definitions(before)

    assert set(after) == set(before) == {question.id for question in REGISTRY}
    for qid in before:
        assert after[qid] is before[qid], qid
        assert after[qid].to_dict() == before[qid].to_dict(), qid

    protected = {
        "M1", "M11", "X3", "EXEC1", "D3", "D4", "D5",
        "L1", "L2", "L3", "L4", "M8", "OPP-1", "M3", "M7",
        "P1", "R3", "R4", "R5", "EX9", "D2", "X5", "EX1",
        "EX2", "EX10", "L7",
    }
    for qid in protected:
        assert after[qid] is before[qid]
        assert after[qid].to_dict() == before[qid].to_dict()


# ---------------------------------------------------------------------------
# WAVE A5 - canonical ownership relationships
# ---------------------------------------------------------------------------

from research_engine.registry.wave_a5_definitions import (  # noqa: E402
    WAVE_A5_OWNERSHIP,
    WAVE_A5_TARGET_RELATIONSHIPS,
    WAVE_A5_TARGETS,
    apply_wave_a5_definitions,
)


def test_wave_a5_relationship_scope_and_metadata_are_exact():
    assert WAVE_A5_TARGET_RELATIONSHIPS == (
        ("E3", "S1"),
        ("D6", "PORT-1"),
        ("R1", "R2"),
        ("D1", "L3"),
        ("E2", "L1"),
    )
    assert WAVE_A5_TARGETS == {
        "E3", "S1", "D6", "PORT-1", "R1", "R2", "D1", "L3", "E2", "L1",
    }
    assert tuple(WAVE_A5_OWNERSHIP) == WAVE_A5_TARGET_RELATIONSHIPS
    assert all(item.to_dict() == item.to_dict() for item in WAVE_A5_OWNERSHIP.values())


def test_e3_s1_equivalent_intent_does_not_bless_wrong_shared_runner():
    from research_engine.registry import REGISTRY_BY_ID

    item = WAVE_A5_OWNERSHIP[("E3", "S1")]
    e3, s1 = REGISTRY_BY_ID["E3"], REGISTRY_BY_ID["S1"]
    assert item.scientifically_equivalent is True
    assert "TRUE_ALIAS" in item.relationship_types
    assert "UNRESOLVED" in item.relationship_types
    assert item.canonical_owners == ()
    assert e3.required_fields == s1.required_fields == ("strategy", "r_multiple")
    assert e3.data_sources == s1.data_sources
    assert tuple((r.field, r.operator, r.threshold) for r in e3.validation_rules) == tuple(
        (r.field, r.operator, r.threshold) for r in s1.validation_rules
    )
    assert e3.runner_function == s1.runner_function == "run_q24"
    assert "answers neither canonical intent" in item.runner_ownership_status
    assert "activation report" in item.false_completion_risk


def test_d6_port1_share_calculation_but_keep_distinct_ownership():
    from research_engine.registry import REGISTRY_BY_ID

    item = WAVE_A5_OWNERSHIP[("D6", "PORT-1")]
    d6, port1 = REGISTRY_BY_ID["D6"], REGISTRY_BY_ID["PORT-1"]
    assert item.relationship_types == ("SHARED_CALCULATION_DISTINCT_OWNERSHIP",)
    assert item.scientifically_equivalent is False
    assert item.highest_safe_sharing_level == "calculation"
    assert "candidate outcome joining" in item.shared_helper_status
    assert d6.runner_function != port1.runner_function
    assert d6.report_filename != port1.report_filename
    assert d6.legacy_ids == port1.legacy_ids == ()
    assert dict(item.canonical_owners) == {"D6": "D6", "PORT-1": "PORT-1"}
    assert "Account executions are not independent" in item.unit_of_analysis_boundary


def test_r1_r2_same_risk_data_does_not_share_scientific_ownership():
    from research_engine.registry import REGISTRY_BY_ID

    item = WAVE_A5_OWNERSHIP[("R1", "R2")]
    r1, r2 = REGISTRY_BY_ID["R1"], REGISTRY_BY_ID["R2"]
    assert item.scientifically_equivalent is False
    assert "SHARED_HELPER_ONLY" in item.relationship_types
    assert "DISTINCT_RUNNER_REQUIRED" in item.relationship_types
    assert "DISTINCT_REPORT_REQUIRED" in item.relationship_types
    assert "LEGACY_IDENTITY_COLLISION" in item.relationship_types
    assert r1.runner_function == r2.runner_function == "run_q10"
    assert r1.report_filename == r2.report_filename == "q10_guard_efficacy.json"
    assert r1.legacy_ids == r2.legacy_ids == ("Q10",)
    assert "neither overall risk benefit nor per-guard value" in item.runner_ownership_status
    assert "falsely complete both distinct claims" in item.false_completion_risk


def test_d1_l3_keep_d1_component_ownership_and_require_l3_separation():
    from research_engine.registry import REGISTRY_BY_ID

    item = WAVE_A5_OWNERSHIP[("D1", "L3")]
    d1, l3 = REGISTRY_BY_ID["D1"], REGISTRY_BY_ID["L3"]
    assert item.scientifically_equivalent is False
    assert item.highest_safe_sharing_level == "calculation"
    assert d1.runner_function == l3.runner_function == "run"
    assert d1.report_filename == l3.report_filename == "q1_component_reward.json"
    assert d1.legacy_ids == l3.legacy_ids == ("Q1",)
    assert dict(item.canonical_owners) == {
        "component_reward.run": "D1",
        "q1_component_reward.json": "D1",
    }
    assert "L3 requires an independent" in item.runner_ownership_status
    assert "L3 requires a distinct canonical report" in item.report_ownership_status


def test_e2_l1_keep_e2_pooled_ownership_and_require_l1_temporal_separation():
    from research_engine.registry import REGISTRY_BY_ID

    item = WAVE_A5_OWNERSHIP[("E2", "L1")]
    e2, l1 = REGISTRY_BY_ID["E2"], REGISTRY_BY_ID["L1"]
    assert item.scientifically_equivalent is False
    assert item.highest_safe_sharing_level == "helper"
    assert e2.runner_function == l1.runner_function == "run_q05"
    assert e2.report_filename == l1.report_filename == "q5_pattern_degradation.json"
    assert "Q5" in e2.legacy_ids and "Q5" in l1.legacy_ids
    assert dict(item.canonical_owners) == {
        "legacy_canonical.run_q05": "E2",
        "q5_pattern_degradation.json": "E2",
    }
    assert "does not use timestamps" in item.runner_ownership_status
    assert "non-temporal finding" in item.false_completion_risk


def test_shared_reports_and_legacy_ids_never_establish_equivalence():
    for pair in (("R1", "R2"), ("D1", "L3"), ("E2", "L1")):
        item = WAVE_A5_OWNERSHIP[pair]
        assert item.scientifically_equivalent is False
        assert "DISTINCT_REPORT_REQUIRED" in item.relationship_types
        assert "LEGACY_IDENTITY_COLLISION" in item.relationship_types


def test_wave_a5_is_metadata_only_and_preserves_every_definition():
    before = build_definitions_from_registry(REGISTRY)
    after = apply_wave_a5_definitions(before)

    assert after is before
    assert set(after) == set(before)
    for qid in before:
        assert after[qid] is before[qid], qid
        assert after[qid].to_dict() == before[qid].to_dict(), qid


def test_wave_a5_does_not_mutate_operational_ownership_mappings():
    from research_engine.registry import REGISTRY_BY_ID

    expected = {
        "E3": ("run_q24", "q24_strategy_edge.json", ("Q24",)),
        "S1": ("run_q24", "q24_strategy_edge.json", ("Q24",)),
        "D6": ("run_portfolio_ranking", "d6_portfolio_ranking.json", ()),
        "PORT-1": ("run_port_1", "port1_portfolio_selection.json", ()),
        "R1": ("run_q10", "q10_guard_efficacy.json", ("Q10",)),
        "R2": ("run_q10", "q10_guard_efficacy.json", ("Q10",)),
        "D1": ("run", "q1_component_reward.json", ("Q1",)),
        "L3": ("run", "q1_component_reward.json", ("Q1",)),
        "E2": ("run_q05", "q5_pattern_degradation.json", ("Q5", "Q24")),
        "L1": ("run_q05", "q5_pattern_degradation.json", ("Q5",)),
    }
    for qid, mapping in expected.items():
        question = REGISTRY_BY_ID[qid]
        assert (question.runner_function, question.report_filename, question.legacy_ids) == mapping


# ---------------------------------------------------------------------------
# FINAL WAVE A - NO_RUNNER operational-design contracts
# ---------------------------------------------------------------------------

from research_engine.registry.wave_a_no_runner_definitions import (  # noqa: E402
    ALREADY_AVAILABLE,
    DERIVABLE,
    EVIDENCE_GAP_CLASSIFICATIONS,
    EXISTING_V1_CONTRACT_VIOLATION,
    NEW_RESEARCH_EVIDENCE,
    WAVE_A_NO_RUNNER_DESIGNS,
    WAVE_A_NO_RUNNER_TARGETS,
    apply_wave_a_no_runner_designs,
)


def test_no_runner_design_scope_and_metadata_are_deterministic():
    expected = {"S5", "S6", "S7", "X6", "L6", "G1", "G2", "G3"}
    assert WAVE_A_NO_RUNNER_TARGETS == expected
    assert set(WAVE_A_NO_RUNNER_DESIGNS) == expected
    for qid, design in WAVE_A_NO_RUNNER_DESIGNS.items():
        assert design.canonical_question_id == qid
        assert design.definition_version == 1
        assert design.lifecycle == "PROPOSED"
        assert design.to_dict() == design.to_dict()
        assert design.canonical_intent
        assert design.hypothesis and design.null_hypothesis
        assert design.research_classification


def test_no_runner_targets_remain_truthfully_unmapped_and_under_specified():
    definitions = build_definitions_from_registry(REGISTRY)
    reports = validate_all_definitions(definitions)

    for qid in WAVE_A_NO_RUNNER_TARGETS:
        question = REGISTRY_BY_ID[qid]
        definition = definitions[qid]
        assert question.runner_module == question.runner_function == ""
        assert question.report_filename == ""
        assert definition.runner_module == definition.runner_function == ""
        assert definition.report_filename == ""
        assert definition.minimum_sample is None
        assert definition.completion_rule is None
        assert get_question_health(reports[qid]) == "UNDER_SPECIFIED"


def test_every_no_runner_design_has_implementation_ready_contract_fields():
    for design in WAVE_A_NO_RUNNER_DESIGNS.values():
        assert design.population_definition
        assert design.unit_of_analysis
        assert design.metric_definition
        assert design.evidence_authority
        assert design.join_contract
        assert design.epoch_requirement
        assert design.minimum_sample
        assert design.cell_sufficiency
        assert design.completion_criterion
        assert design.dependencies
        assert design.runner_specification.proposed_module
        assert design.runner_specification.proposed_function
        assert design.runner_specification.inputs
        assert design.runner_specification.filters
        assert design.runner_specification.unit_of_analysis
        assert design.runner_specification.joins
        assert design.runner_specification.grouping
        assert design.runner_specification.metrics
        assert design.runner_specification.sufficiency
        assert design.runner_specification.output
        assert design.runner_specification.completion_rule
        assert design.runner_specification.fail_closed_conditions
        assert design.report_identity.endswith(".json")
        assert design.multi_account_rule
        assert design.repeated_measure_rule
        assert design.leakage_rule
        assert design.implementation_requirements
        assert design.human_semantic_decisions


def test_evidence_gaps_are_explicit_and_new_research_is_not_a_v1_defect():
    assert EVIDENCE_GAP_CLASSIFICATIONS == {
        ALREADY_AVAILABLE,
        DERIVABLE,
        NEW_RESEARCH_EVIDENCE,
        EXISTING_V1_CONTRACT_VIOLATION,
    }
    for design in WAVE_A_NO_RUNNER_DESIGNS.values():
        assert design.evidence_gap_classification in EVIDENCE_GAP_CLASSIFICATIONS
        assert design.evidence_gap_classification != EXISTING_V1_CONTRACT_VIOLATION
        for authority in design.evidence_authority:
            assert authority.gap_classification in EVIDENCE_GAP_CLASSIFICATIONS
            assert authority.gap_classification != EXISTING_V1_CONTRACT_VIOLATION

    assert WAVE_A_NO_RUNNER_DESIGNS["G3"].evidence_gap_classification == NEW_RESEARCH_EVIDENCE
    assert not WAVE_A_NO_RUNNER_DESIGNS["G3"].existing_evidence_sufficient_in_principle
    for qid in WAVE_A_NO_RUNNER_TARGETS - {"G3"}:
        assert WAVE_A_NO_RUNNER_DESIGNS[qid].evidence_gap_classification == DERIVABLE
        assert WAVE_A_NO_RUNNER_DESIGNS[qid].existing_evidence_sufficient_in_principle


def test_strategy_designs_exclude_account_fanout_and_cluster_horizons():
    for qid in ("S5", "S6", "S7"):
        design = WAVE_A_NO_RUNNER_DESIGNS[qid]
        assert "canonical opportunity" in design.unit_of_analysis
        assert "Account" in design.multi_account_rule or "account" in design.multi_account_rule
        assert "excluded" in design.multi_account_rule
        assert "repeated" in design.repeated_measure_rule
        assert "independent" in design.repeated_measure_rule
        assert "simulated R" in design.leakage_rule
        assert "outcome" in design.leakage_rule


def test_x6_uses_justified_account_grain_and_preexecution_conditions():
    design = WAVE_A_NO_RUNNER_DESIGNS["X6"]
    datasets = {authority.dataset for authority in design.evidence_authority}
    assert datasets == {"execution_results_v1", "execution_context", "decision_trace_v1"}
    assert "account execution result" in design.unit_of_analysis
    assert "clustered by correlation_id" in design.unit_of_analysis
    assert "legitimate observations" in design.multi_account_rule
    assert "producer-measured" in design.metric_definition
    assert "pre-execution volatility" in design.canonical_intent
    assert "slippage_journal" in " ".join(design.implementation_requirements)
    assert "not a V1 defect" in " ".join(design.implementation_requirements)
    assert all(join.conflict_policy.startswith("reject") for join in design.join_contract)


def test_predictive_leakage_and_meta_governance_are_fail_closed():
    for design in WAVE_A_NO_RUNNER_DESIGNS.values():
        leakage = design.leakage_rule.lower()
        assert "outcome" in leakage or "predict" in leakage or "production" in leakage

    assert "outcome label only" in WAVE_A_NO_RUNNER_DESIGNS["S5"].leakage_rule
    assert "outcomes only" in WAVE_A_NO_RUNNER_DESIGNS["S7"].leakage_rule
    assert "frozen before execution" in WAVE_A_NO_RUNNER_DESIGNS["X6"].leakage_rule
    assert "cannot approve" in WAVE_A_NO_RUNNER_DESIGNS["G3"].leakage_rule
    assert "exclude G3 self-result" in WAVE_A_NO_RUNNER_DESIGNS["G3"].runner_specification.filters


def test_g2_lineage_denominator_cannot_be_inflated_or_joined_by_fallback_alias():
    design = WAVE_A_NO_RUNNER_DESIGNS["G2"]
    assert design.unit_of_analysis == (
        "One canonical opportunity; account executions and repeated shadow horizons never enlarge the denominator."
    )
    assert design.join_contract[0].keys == ("entity_id", "canonical_opportunity_id")
    assert "partial keys" in design.join_contract[0].conflict_policy
    assert "Collapse all completed horizon lifecycles" in design.repeated_measure_rule
    assert "Account fanout is excluded" in design.multi_account_rule
    assert "never determines whether lineage is valid" in design.leakage_rule


def test_no_runner_report_identities_are_unique_and_do_not_collide():
    proposed = [design.report_identity for design in WAVE_A_NO_RUNNER_DESIGNS.values()]
    existing = {
        question.report_filename
        for question in REGISTRY
        if question.id not in WAVE_A_NO_RUNNER_TARGETS and question.report_filename
    }
    assert len(proposed) == len(set(proposed)) == 8
    assert not set(proposed) & existing


def test_no_runner_design_layer_preserves_a1_to_a5_and_every_definition():
    before = build_definitions_from_registry(REGISTRY)
    snapshots = {qid: definition.to_dict() for qid, definition in before.items()}
    after = apply_wave_a_no_runner_designs(before)

    assert after is before
    assert set(after) == set(before) == {question.id for question in REGISTRY}
    for qid in before:
        assert after[qid] is before[qid]
        assert after[qid].to_dict() == snapshots[qid]

    assert tuple(WAVE_A5_OWNERSHIP) == WAVE_A5_TARGET_RELATIONSHIPS


def test_no_runner_design_does_not_change_runtime_resolver_or_readiness_contracts():
    for qid in WAVE_A_NO_RUNNER_TARGETS:
        question = REGISTRY_BY_ID[qid]
        assert question.runner_module == ""
        assert question.runner_function == ""
        assert question.report_filename == ""
        # Original registry evidence/readiness declarations remain the runtime truth.
        assert question.data_sources
        assert isinstance(question.validation_rules, tuple)
