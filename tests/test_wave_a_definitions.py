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
