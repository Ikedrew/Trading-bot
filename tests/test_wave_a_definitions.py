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


def test_d2_unresolved_authority_visible():
    definitions = build_definitions_from_registry(REGISTRY)
    report = validate_definition(definitions["D2"])
    unresolved = [r for r in report.results if r.category == "UNRESOLVED_AUTHORITY"]
    assert len(unresolved) >= 6


def test_x5_unresolved_authority_visible():
    definitions = build_definitions_from_registry(REGISTRY)
    report = validate_definition(definitions["X5"])
    unresolved = [r for r in report.results if r.category == "UNRESOLVED_AUTHORITY"]
    assert len(unresolved) >= 5


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
    mismatch_ids = {"M1", "M11", "D3", "D4", "D5", "X3", "L1", "L2", "L3", "L4", "EX5", "EX6", "EX7", "EX8", "EXEC1"}
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

    # D2 and X5 have UNRESOLVED_AUTHORITY errors
    d2_report = reports["D2"]
    assert any(r.category == "UNRESOLVED_AUTHORITY" for r in d2_report.results)

    x5_report = reports["X5"]
    assert any(r.category == "UNRESOLVED_AUTHORITY" for r in x5_report.results)

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


def _build_pre_a2_definitions(monkeypatch):
    """Build the committed Wave A1 state with the A2 application disabled."""
    import research_engine.registry.wave_a2_definitions as wave_a2

    original = wave_a2.apply_wave_a2_definitions
    monkeypatch.setattr(wave_a2, "apply_wave_a2_definitions", lambda definitions: definitions)
    before = build_definitions_from_registry(REGISTRY)
    monkeypatch.setattr(wave_a2, "apply_wave_a2_definitions", original)
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

    for qid in WAVE_A2_UNRESOLVED:
        unresolved = definitions[qid]
        assert not unresolved.hypothesis.strip(), qid
        assert not unresolved.population_definition.strip(), qid
        assert not unresolved.metric_definition.strip(), qid
        assert get_question_health(reports[qid]) == "UNDER_SPECIFIED", qid
    assert get_question_health(reports["M8"]) == "UNDER_SPECIFIED"
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
        assert any(
            result.category == "UNRESOLVED_AUTHORITY"
            for result in report.results
        )


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
    after_a22a = build_definitions_from_registry(REGISTRY)

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
    """Build the committed A1/A2 state with A3 application disabled."""
    import research_engine.registry.wave_a3_definitions as wave_a3

    original = wave_a3.apply_wave_a3_definitions
    monkeypatch.setattr(
        wave_a3, "apply_wave_a3_definitions", lambda definitions: definitions
    )
    before = build_definitions_from_registry(REGISTRY)
    monkeypatch.setattr(wave_a3, "apply_wave_a3_definitions", original)
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
        assert get_question_health(after_reports[qid]) == "UNDER_SPECIFIED", qid
        assert qid in WAVE_A3_UNRESOLVED
        assert qid not in WAVE_A3_RESOLVED
        assert qid not in WAVE_A3_OVERRIDES
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

    original = wave_a4.apply_wave_a4_definitions
    monkeypatch.setattr(
        wave_a4, "apply_wave_a4_definitions", lambda definitions: definitions
    )
    before = build_definitions_from_registry(REGISTRY)
    monkeypatch.setattr(wave_a4, "apply_wave_a4_definitions", original)
    return before


def test_wave_a41_scope_and_before_to_after_health_are_exact(monkeypatch):
    assert WAVE_A4_1_TARGETS == {"M1", "M11", "X3", "EXEC1"}
    assert WAVE_A4_TARGETS == WAVE_A4_1_TARGETS
    assert WAVE_A4_RESOLVED == set()
    assert WAVE_A4_UNRESOLVED == WAVE_A4_TARGETS
    assert WAVE_A4_OVERRIDES == {}
    assert set(WAVE_A4_UNRESOLVED_REASONS) == WAVE_A4_UNRESOLVED

    before = _build_pre_a4_definitions(monkeypatch)
    after = build_definitions_from_registry(REGISTRY)
    before_reports = validate_all_definitions(before)
    after_reports = validate_all_definitions(after)

    for qid in WAVE_A4_1_TARGETS:
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
