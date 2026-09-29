from __future__ import annotations

from dataclasses import replace

import pytest

from research_engine.registry.research_question_registry import REGISTRY
from research_engine.v10.universes.evidence_integrity import (
    EvidenceBatch,
    IntegrityFinding,
    IntegrityReport,
    IntegrityStatus,
    ReconstructedArtifact,
)
from research_engine.v10.universes.models import Universe
from research_engine.v10.universes.assurance_provenance import (
    HistoricalExhaustionStatus,
    assert_accounting_conservation,
    evaluate_historical_exhaustion,
)
from research_engine.v10.universes.question_qualification import (
    QualificationEngine,
    QualificationStatus,
    QuestionContractError,
    QuestionEvidenceInput,
    StatisticalState,
    build_question_evidence_contracts,
    validate_question_evidence_contracts,
)
from research_engine.v10.universes.reconciliation import (
    ReconciliationReport,
    ReconciliationResult,
    ReconciliationTrace,
    RelationshipStatus,
)


def _contracts():
    return build_question_evidence_contracts()


def _contract(qid="X2"):
    return next(item for item in _contracts() if item.question_id == qid)


def _batch(dataset, *, universe=Universe.EXECUTION, count=1):
    return EvidenceBatch(
        universe=universe,
        dataset=dataset,
        records=tuple({"trade_id": f"t-{index}"} for index in range(count)),
    )


def _supported_input(qid="X2", **changes):
    contract = _contract(qid)
    values = dict(
        question_id=qid,
        contract_fingerprint=contract.contract_fingerprint,
        existing_state="COMPLETE",
        existing_result={"finding": "test"},
        actual_population=max(contract.minimum_sample or 1, 1),
        population_complete=True,
        collection_functional=True,
        observed_resolution=contract.resolution,
        observed_fields=contract.required_fields,
        available_lineage=contract.required_lineage,
        statistical_state=StatisticalState.SUFFICIENT,
        supported_scope={"symbols": ["EURUSD"], "epoch": "CURRENT"},
        evidence_references=("fixture:evidence",),
    )
    values.update(changes)
    return QuestionEvidenceInput(**values)


def _x2_engine(value=None, integrity_report=None):
    return QualificationEngine(
        inputs={"X2": value or _supported_input()},
        batches=(_batch("execution_results_v1"), _batch("execution_attempts_v1")),
        integrity_report=integrity_report,
    )


def _finding(status, *, finding_id="finding-1", dataset="execution_results_v1"):
    return IntegrityFinding(
        finding_id=finding_id,
        universe="EXECUTION",
        dataset=dataset,
        category="test",
        status=status,
        severity="ERROR",
        identity={},
        scope={},
        first_affected_timestamp="",
        last_affected_timestamp="",
        expected_condition="intact",
        observed_condition=status,
        evidence=("fixture",),
        legitimate_absence_evaluation="not legitimate",
        reconstruction_status="",
        provenance="fixture",
        fingerprint=f"fp-{finding_id}",
    )


def _reconciliation(status):
    trace = ReconciliationTrace(
        source_evidence_reference="source:1",
        contract_relationship="DECISION_EXECUTION",
        condition_evaluated="EXECUTE",
        condition_evidence=("source:1",),
        expectation_state="REQUIRED",
        join_identity={"entity_id": "e-1"},
        match_kind="DETERMINISTIC",
        candidate_references=("target:1",),
        integrity_blockers=(),
        semantic_comparisons=(),
        final_state=status,
    )
    result = ReconciliationResult(
        rule_id="DECISION_EXECUTION",
        source_universe="DECISION",
        source_identity={"entity_id": "e-1"},
        target_universe="EXECUTION",
        target_identities=({"trade_id": "t-1"},),
        expectation_state="REQUIRED",
        observed_counterpart_count=1,
        expected_cardinality="1:0..1",
        relationship_status=status,
        matched_join_key={"entity_id": "e-1"},
        match_kind="DETERMINISTIC",
        semantic_checks=(),
        integrity_blockers=(),
        historical_limitations=(),
        source_evidence_references=("source:1",),
        counterpart_evidence_references=("target:1",),
        provenance=("fixture",),
        root_finding_ids=(),
        dependent_impact=False,
        trace=trace,
        fingerprint=f"reconciliation-{status}",
    )
    return ReconciliationReport((result,), {status: 1}, {"DECISION_EXECUTION": {status: 1}}, ())


def _exec1_engine(status):
    contract = _contract("EXEC1")
    supplied = _supported_input("EXEC1")
    return QualificationEngine(
        inputs={"EXEC1": supplied},
        batches=tuple(_batch(name, universe=Universe.EXECUTION) for name in contract.required_datasets),
        reconciliation_report=_reconciliation(status),
    )


def test_contracts_reference_exact_canonical_70_without_parallel_bank():
    contracts = _contracts()
    assert len(contracts) == len(REGISTRY) == 70
    assert [item.question_id for item in contracts] == [item.id for item in REGISTRY]
    assert len({item.contract_fingerprint for item in contracts}) == 70
    assert all(item.canonical_question_reference.startswith("REGISTRY_BY_ID[") for item in contracts)
    assert all(item.phenomenon and item.population and item.resolution for item in contracts)


def test_all_questions_receive_exactly_one_deterministic_qualification():
    first = QualificationEngine().qualify_all()
    second = QualificationEngine().qualify_all()
    assert len(first.qualifications) == 70
    assert len({item.question_id for item in first.qualifications}) == 70
    assert first.to_dict() == second.to_dict()
    assert first.report_fingerprint == second.report_fingerprint


def test_malformed_and_noncanonical_contracts_fail_closed():
    contracts = list(_contracts())
    contracts[0] = replace(contracts[0], phenomenon="")
    with pytest.raises(QuestionContractError, match="missing phenomenon"):
        validate_question_evidence_contracts(contracts)
    with pytest.raises(QuestionContractError, match="coverage mismatch"):
        validate_question_evidence_contracts(_contracts()[:-1])


def test_stale_question_or_contract_fingerprint_fails_closed():
    contracts = list(_contracts())
    contracts[0] = replace(contracts[0], question_fingerprint="changed-semantics")
    with pytest.raises(QuestionContractError, match="stale question fingerprint"):
        QualificationEngine(contracts=contracts)
    with pytest.raises(QuestionContractError, match="stale contract fingerprint"):
        QualificationEngine(inputs={"X2": replace(_supported_input(), contract_fingerprint="old")})
    contracts = list(_contracts())
    contracts[0] = replace(contracts[0], population="silently changed population")
    with pytest.raises(QuestionContractError, match="stale contract fingerprint"):
        QualificationEngine(contracts=contracts)


def test_verified_full_scope_and_existing_complete_are_separate():
    result = _x2_engine().qualify_question("X2")
    assert result.existing_research_state == "COMPLETE"
    assert result.qualification_status == QualificationStatus.VERIFIED.value
    assert result.sufficiency.structural == "PASS"
    assert result.existing_research_result == {"finding": "test"}


def test_partial_population_and_resolution_are_explicit():
    supplied = _supported_input(
        population_complete=False,
        observed_resolution="daily aggregate",
        supported_scope={"broker": ["A"]},
        unsupported_scope={"brokers": ["B"]},
    )
    result = _x2_engine(supplied).qualify_question("X2")
    assert result.qualification_status == QualificationStatus.PARTIAL.value
    assert "POPULATION_SUBSET_ONLY" in result.reason_codes
    assert "RESOLUTION_MISMATCH" in result.reason_codes
    assert result.unsupported_scope == {"brokers": ["B"]}


def test_degraded_integrity_warning_and_historical_boundary():
    report = IntegrityReport((), (_finding(IntegrityStatus.STALE.value),))
    supplied = _supported_input(historical_limitations=("pre-migration schema",))
    result = _x2_engine(supplied, report).qualify_question("X2")
    assert result.qualification_status == QualificationStatus.DEGRADED.value
    assert set(result.reason_codes) >= {"INTEGRITY_WARNING", "HISTORICAL_LIMITATION"}


def test_unavailable_when_required_evidence_absent_or_unreconstructable():
    result = QualificationEngine(inputs={"X2": _supported_input()}, batches=()).qualify_question("X2")
    assert result.qualification_status == QualificationStatus.UNAVAILABLE.value
    assert "ESSENTIAL_DATASET_ABSENT" in result.reason_codes
    report = IntegrityReport((), (_finding(IntegrityStatus.UNRECONSTRUCTABLE.value),))
    result = _x2_engine(integrity_report=report).qualify_question("X2")
    assert result.qualification_status == QualificationStatus.UNAVAILABLE.value
    assert "ESSENTIAL_EVIDENCE_UNRECONSTRUCTABLE" in result.reason_codes


def test_integrity_conflict_is_indeterminate_and_finding_maps_to_questions():
    finding = _finding(IntegrityStatus.IDENTITY_CONFLICT.value)
    engine = _x2_engine(integrity_report=IntegrityReport((), (finding,)))
    result = engine.qualify_question("X2")
    assert result.qualification_status == QualificationStatus.INDETERMINATE.value
    assert "INTEGRITY_AMBIGUITY" in result.reason_codes
    assert "X2" in engine.questions_affected_by_finding(finding.finding_id)


def test_reconciliation_success_and_conflict_are_consumed():
    verified = _exec1_engine(RelationshipStatus.RECONCILED.value).qualify_question("EXEC1")
    assert verified.qualification_status == QualificationStatus.VERIFIED.value
    conflict_engine = _exec1_engine(RelationshipStatus.CARDINALITY_CONFLICT.value)
    conflict = conflict_engine.qualify_question("EXEC1")
    assert conflict.qualification_status == QualificationStatus.INDETERMINATE.value
    assert "RECONCILIATION_AMBIGUITY" in conflict.reason_codes
    assert conflict_engine.questions_affected_by_finding("reconciliation-CARDINALITY_CONFLICT") == (
        "X6", "L6", "G1", "G3", "EXEC1",
    )


def test_complete_result_can_be_downgraded_without_mutation():
    supplied = _supported_input(existing_state="COMPLETE", existing_result="POSITIVE")
    result = QualificationEngine(inputs={"X2": supplied}).qualify_question("X2")
    assert result.qualification_status == QualificationStatus.UNAVAILABLE.value
    assert result.existing_research_state == "COMPLETE"
    assert result.existing_research_result == "POSITIVE"


def test_verified_insufficient_data_requires_working_collection():
    supported = _supported_input(
        existing_state="INSUFFICIENT_DATA",
        statistical_state=StatisticalState.INSUFFICIENT,
        collection_functional=True,
    )
    result = _x2_engine(supported).qualify_question("X2")
    assert result.qualification_status == QualificationStatus.VERIFIED.value
    assert "VERIFIED_INSUFFICIENT_DATA" in result.reason_codes
    broken = replace(supported, collection_functional=False)
    result = _x2_engine(broken).qualify_question("X2")
    assert result.qualification_status == QualificationStatus.DEGRADED.value


def test_negative_conclusion_requires_observability():
    supplied = _supported_input(negative_result=True, negative_observable=False)
    result = _x2_engine(supplied).qualify_question("X2")
    assert result.qualification_status == QualificationStatus.INDETERMINATE.value
    assert "NEGATIVE_NOT_OBSERVABLE" in result.reason_codes


def test_completion_definitions_remove_under_specification_without_hiding_mismatch():
    contracts = _contracts()
    assert not [item.question_id for item in contracts if item.definition_health == "UNDER_SPECIFIED"]
    assert {item.question_id for item in contracts if item.definition_health == "SEMANTIC_MISMATCH"} == {"L2", "L3", "L7"}


def test_partial_lineage_prevents_verified():
    contract = _contract()
    supplied = _supported_input(available_lineage=contract.required_lineage[:1])
    result = _x2_engine(supplied).qualify_question("X2")
    assert result.qualification_status == QualificationStatus.PARTIAL.value
    assert "LINEAGE_PARTIAL" in result.reason_codes


def test_exact_reconstruction_is_visible_but_not_automatically_degraded():
    artifact = ReconstructedArtifact(
        artifact={"trade_id": "t-1"},
        target_universe="EXECUTION",
        target_dataset="execution_results_v1",
        source_datasets=("source",),
        source_identities=("s-1",),
        reconstruction_rule="lossless-copy",
        rule_version="1",
        source_complete=True,
        exact=True,
        information_loss=(),
        reconstruction_timestamp="2026-09-27T00:00:00Z",
    )
    report = IntegrityReport((), (), (artifact,))
    engine = QualificationEngine(
        inputs={"X2": _supported_input()},
        batches=(_batch("execution_attempts_v1"),),
        integrity_report=report,
    )
    result = engine.qualify_question("X2")
    assert result.qualification_status == QualificationStatus.VERIFIED.value
    assert result.reconstruction_involvement == ("execution_results_v1:EXACT:lossless-copy",)


def test_lossy_reconstruction_is_never_verified():
    artifact = ReconstructedArtifact(
        artifact={"trade_id": "t-1"}, target_universe="EXECUTION",
        target_dataset="execution_results_v1", source_datasets=("source",),
        source_identities=("s-1",), reconstruction_rule="approximation",
        rule_version="1", source_complete=False, exact=False,
        information_loss=("timestamp",), reconstruction_timestamp="2026-09-27T00:00:00Z",
    )
    report = IntegrityReport((), (), (artifact,))
    result = _x2_engine(integrity_report=report).qualify_question("X2")
    assert result.qualification_status == QualificationStatus.DEGRADED.value
    assert "LOSSY_RECONSTRUCTION" in result.reason_codes


def test_information_loss_cannot_be_admitted_or_labelled_as_exact():
    artifact = ReconstructedArtifact(
        artifact={"trade_id": "t-1"}, target_universe="EXECUTION",
        target_dataset="execution_results_v1", source_datasets=("source",),
        source_identities=("s-1",), reconstruction_rule="normalised-copy",
        rule_version="1", source_complete=True, exact=True,
        information_loss=("lifecycle lineage",),
        reconstruction_timestamp="2026-09-27T00:00:00Z",
    )
    report = IntegrityReport((), (), (artifact,))
    engine = QualificationEngine(
        inputs={"X2": _supported_input()},
        batches=(_batch("execution_attempts_v1"),),
        integrity_report=report,
    )
    result = engine.qualify_question("X2")
    assert "ESSENTIAL_DATASET_ABSENT" in result.reason_codes
    assert "LOSSY_RECONSTRUCTION" in result.reason_codes
    assert result.reconstruction_involvement == (
        "execution_results_v1:LOSSY:normalised-copy",
    )


def test_every_blocker_has_compact_reason_specific_provenance():
    result = QualificationEngine().qualify_question("X2")
    blockers = [code for code in result.reason_codes if code != "QUALIFIED"]
    assert set(result.blocker_provenance) == set(result.reason_codes)
    assert all(result.blocker_provenance[code] for code in blockers)
    assert set(result.blocker_ids) == {
        reference
        for code in blockers
        for reference in result.blocker_provenance[code]
    }


def test_incomplete_historical_record_accounting_fails_closed():
    supplied = _supported_input(record_accounting={
        "historical_exhaustive": False,
        "source_record_count": 3,
        "current_record_count": 1,
        "historical_record_count": 1,
    })
    result = _x2_engine(supplied).qualify_question("X2")
    assert result.qualification_status == QualificationStatus.INDETERMINATE.value
    assert "HISTORICAL_RECORD_ACCOUNTING_INCOMPLETE" in result.reason_codes
    assert result.blocker_provenance["HISTORICAL_RECORD_ACCOUNTING_INCOMPLETE"]


def _governed_record_accounting(*, resolved=True, population_unexplained=0):
    return {
        "resolved": resolved,
        "historical_exhaustive": resolved,
        "sources": [{
            "source": "execution_results_v1", "available": True,
            "total_records": 12, "current_records": 9,
            "transitional_records": 1, "legacy_records": 2,
            "balanced": True,
            "exclusion_reason_counts": {
                "WRONG_EVIDENCE_EPOCH_TRANSITIONAL": 1,
                "WRONG_EVIDENCE_EPOCH_LEGACY": 2,
            },
        }],
        "population_stage": {
            "candidate_records": 9,
            "used_records": 6,
            "excluded_records": 3 - population_unexplained,
            "unexplained_records": population_unexplained,
            "exclusion_reason_counts": {
                "AMBIGUOUS_OR_UNMATCHED_IDENTITY": 1,
                "FAILED_SIZEING_QUALITY_PREDICATE": 1,
                "MISSING_REQUIRED_FIELD": 1 - population_unexplained,
            },
        },
        "candidate_denominator_authority": "CONTROL_PLANE_EVIDENCE_RESOLVER",
    }


def test_source_and_population_accounting_conserve_without_double_counting():
    result = _x2_engine(_supported_input(
        actual_population=6,
        record_accounting=_governed_record_accounting(),
    )).qualify_question("X2")
    accounting = result.evidence_accounting
    assert_accounting_conservation(accounting)
    assert accounting["candidate_records"] == 12
    assert accounting["used_records"] == 9
    assert accounting["excluded_records"] == 3
    assert accounting["unexplained_records"] == 0
    assert accounting["population_stage"]["candidate_records"] == 9
    assert accounting["population_stage"]["used_records"] == 6
    assert accounting["population_stage"]["excluded_records"] == 3
    assert sum(accounting["exclusion_reason_counts"].values()) == 3
    assert sum(accounting["population_stage"]["exclusion_reason_counts"].values()) == 3
    assert accounting["historical_exhaustion_status"] == HistoricalExhaustionStatus.EXHAUSTED


def test_unexplained_or_unresolved_accounting_cannot_be_exhausted():
    unresolved = _governed_record_accounting(resolved=False)
    unresolved["population_stage"]["candidate_records"] = None
    unexplained = _governed_record_accounting(population_unexplained=1)
    assert evaluate_historical_exhaustion(unresolved)[0] == HistoricalExhaustionStatus.ACCOUNTING_UNRESOLVED
    assert evaluate_historical_exhaustion(unexplained)[0] == HistoricalExhaustionStatus.ACCOUNTING_UNRESOLVED


def test_waiting_data_is_suppressed_until_exhaustion_is_proven():
    unresolved = _x2_engine(_supported_input(
        existing_state="WAITING_DATA",
        statistical_state=StatisticalState.INSUFFICIENT,
        record_accounting={"resolved": False},
    )).qualify_question("X2")
    assert unresolved.evidence_accounting["historical_exhaustion_status"] == "ACCOUNTING_UNRESOLVED"
    assert unresolved.downstream_gate["scientific_state"] == "UNCLASSIFIED"
    assert "FUTURE_DATA_REQUESTED_BEFORE_HISTORICAL_EXHAUSTION" in unresolved.reason_codes

    exhausted = _x2_engine(_supported_input(
        existing_state="WAITING_DATA",
        statistical_state=StatisticalState.INSUFFICIENT,
        actual_population=6,
        record_accounting=_governed_record_accounting(),
    )).qualify_question("X2")
    assert exhausted.evidence_accounting["historical_exhaustion_status"] == "EXHAUSTED"
    assert exhausted.downstream_gate["scientific_state"] == "WAITING_DATA"
    assert "FUTURE_DATA_REQUESTED_BEFORE_HISTORICAL_EXHAUSTION" not in exhausted.reason_codes


def test_trace_blockers_set_qualification_and_restart_determinism():
    first = _x2_engine()
    second = _x2_engine()
    assert first.trace("X2") == second.trace("X2")
    assert first.qualify_question("X2").qualification_fingerprint == second.qualify_question("X2").qualification_fingerprint
    assert first.blockers_for_question("X2") == ()
    unavailable = QualificationEngine().qualify_question("X2")
    assert unavailable.reason_codes == QualificationEngine().blockers_for_question("X2")
