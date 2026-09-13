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

    # Migrated V1 definitions are honest: none validate as fully VALID yet
    # (hypothesis/population/metric empty on migration; D2/X5 unresolved).
    assert all(get_question_health(r) != "VALID" for r in reports.values())

    # D2 and X5 have UNRESOLVED_AUTHORITY errors
    d2_report = reports["D2"]
    assert any(r.category == "UNRESOLVED_AUTHORITY" for r in d2_report.results)

    x5_report = reports["X5"]
    assert any(r.category == "UNRESOLVED_AUTHORITY" for r in x5_report.results)


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
