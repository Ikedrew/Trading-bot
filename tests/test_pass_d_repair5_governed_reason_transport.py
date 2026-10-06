from __future__ import annotations

from dataclasses import replace

import pytest

from research_engine.control_plane.evidence_resolver import EvidenceResolution
from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.registry.research_question_registry import REGISTRY_BY_ID
from research_engine.v10.continuous.canonical_question_cycle import _normalise_report
from research_engine.v10.continuous.canonical_question_cycle import REGISTRY_VERSION
from research_engine.v10.continuous.evaluation_identity import build_evaluation_identity
from research_engine.v10.continuous.question_cycle_state import (
    CanonicalQuestionResult,
    QuestionCycleStore,
)


QUESTION_REASONS = {
    "E3": "INSUFFICIENT_EVIDENCE",
    "S2": "INSUFFICIENT_HORIZON_OUTCOME_COVERAGE",
    "S3": "INSUFFICIENT_STRATEGY_HORIZON_CELL_COVERAGE",
    "S5": "INSUFFICIENT_EVIDENCE",
    "S6": "INSUFFICIENT_EVIDENCE",
    "S7": "INSUFFICIENT_EVIDENCE",
    "EXEC1": "INSUFFICIENT_EVIDENCE",
    "G1": "HD13_UNKNOWN_REQUIREMENTS",
}


class _Context:
    snapshot_id = "ISNAP-REPAIR5"
    fingerprint = "5" * 64
    investigation_epoch = "INVESTIGATION-EPOCH-REPAIR5"
    reader = type(
        "Reader", (), {
            "snapshot": type(
                "Snapshot", (), {"created_at": "2026-10-06T00:00:00Z"}
            )()
        },
    )()

    def evidence_references(self, question):
        return ({"dataset": "repair5", "question_id": question.id},)


DEFINITIONS = build_definitions_from_registry(REGISTRY_BY_ID.values())


def _result(question_id: str) -> CanonicalQuestionResult:
    reason_code = QUESTION_REASONS[question_id]
    details = {
        "finding": f"exact governed explanation for {question_id}",
        "sufficiency": {"gate": False, "reason_code": reason_code},
    }
    if question_id == "G1":
        details["assessments"] = [{
            "question_id": "OPP-1",
            "status": "UNKNOWN",
            "requirements": [{
                "requirement_id": "OPP-1:field:state",
                "status": "UNKNOWN",
                "reason": "Required field could not be assessed",
            }],
        }]
    report = {
        "status": "BLOCKED" if question_id == "G1" else "INSUFFICIENT_DATA",
        "reason_code": reason_code,
        "reason": details["finding"],
        "reason_details": details,
        "overall": details,
        "recommendation": "WAIT_FOR_EVIDENCE",
        "sample_n": 17,
    }
    return _normalise_report(
        REGISTRY_BY_ID[question_id], DEFINITIONS[question_id], report,
        EvidenceResolution(question_id, [], 17, 0, {}, []), _Context(), None,
    )


@pytest.mark.parametrize("question_id", tuple(QUESTION_REASONS))
def test_all_repair5_questions_preserve_exact_reason_through_current_projection(
    tmp_path, question_id,
):
    result = _result(question_id)
    expected_status = "BLOCKED" if question_id == "G1" else "INSUFFICIENT_DATA"

    assert result.status == expected_status
    assert result.reason_code == QUESTION_REASONS[question_id]
    assert result.reason == f"exact governed explanation for {question_id}"
    assert result.reason_details["sufficiency"]["reason_code"] == result.reason_code
    assert result.failure_reason is None
    assert result.implementation_status == "IMPLEMENTED"

    store = QuestionCycleStore(tmp_path / "cycle")
    store.save_question_result(result)
    restored = store.load_question_result(
        question_id, result.snapshot_id, result.evaluation_identity_digest,
    )
    assert restored is not None
    assert restored.reason_code == result.reason_code
    assert restored.reason == result.reason
    assert restored.reason_details == result.reason_details

    projection = {
        "cycle_schema": "canonical_question_cycle_v1",
        "cycle_id": "QCYCLE-REPAIR5",
        "questions": {question_id: {"result": restored.to_dict()}},
    }
    store.save_projection(projection)
    projected = store.load_current()["questions"][question_id]["result"]
    assert projected["status"] == expected_status
    assert projected["reason_code"] == result.reason_code
    assert projected["reason"] == result.reason
    assert projected["reason_details"] == result.reason_details


def test_old_normalisation_loss_is_repaired_for_structured_reason():
    result = _result("S7")
    # Before Repair 5 these fields did not exist and only WAIT_FOR_EVIDENCE
    # survived as the substantive answer.
    assert result.substantive_answer == "WAIT_FOR_EVIDENCE"
    assert result.reason_code == "INSUFFICIENT_EVIDENCE"
    assert result.reason_details["sufficiency"] == {
        "gate": False, "reason_code": "INSUFFICIENT_EVIDENCE",
    }


def test_g1_blocked_hd13_reason_is_not_conflated_with_implementation_failure():
    result = _result("G1")
    assert result.status == "BLOCKED"
    assert result.reason_code == "HD13_UNKNOWN_REQUIREMENTS"
    assert result.reason_details["assessments"][0]["requirements"][0] == {
        "requirement_id": "OPP-1:field:state",
        "status": "UNKNOWN",
        "reason": "Required field could not be assessed",
    }
    assert result.status != "IMPLEMENTATION_BLOCKED"
    assert result.failure_reason is None
    assert result.implementation_status == "IMPLEMENTED"


def test_missing_required_governed_reason_fails_closed_without_fabrication():
    question_id = "E3"
    result = _normalise_report(
        REGISTRY_BY_ID[question_id], DEFINITIONS[question_id],
        {"status": "INSUFFICIENT_DATA", "recommendation": "WAIT_FOR_EVIDENCE"},
        EvidenceResolution(question_id, [], 0, 0, {}, []), _Context(), None,
    )
    assert result.status == "INVALID"
    assert result.reason_code is None
    assert result.reason is None
    assert result.reason_details is None
    assert result.failure_reason == "RUNNER_REPORT_MISSING_GOVERNED_REASON"


def test_historical_reasonless_result_is_not_enriched_or_reidentified():
    current = _result("E3")
    historical = replace(
        current, reason_code=None, reason=None, reason_details=None, result_id="",
    )
    payload = historical.to_dict()
    assert "reason_code" not in payload
    assert "reason" not in payload
    assert "reason_details" not in payload

    restored = CanonicalQuestionResult.from_dict(payload)
    assert restored.result_id == historical.result_id
    assert "reason_code" not in restored.to_dict()
    assert "reason" not in restored.to_dict()
    assert "reason_details" not in restored.to_dict()


@pytest.mark.parametrize("question_id", tuple(QUESTION_REASONS))
def test_reason_transport_schema_is_part_of_evaluator_identity(question_id):
    identity = build_evaluation_identity(
        REGISTRY_BY_ID[question_id], DEFINITIONS[question_id],
        registry_version=REGISTRY_VERSION,
    )
    assert identity["report_schema_versions"]["GOVERNED_REASON_SCHEMA"] == (
        "governed_reason_v1"
    )


def test_registered_evaluators_emit_reasons_consumed_unchanged_by_normalisation():
    from research_engine.experiments.dataset_suitability import run_g1
    from research_engine.experiments.execution_protection_research import run_exec1
    from research_engine.experiments.horizon_expectancy import run_s6
    from research_engine.experiments.selection_research import run_s2, run_s3
    from research_engine.experiments.strategy_expectancy import run_e3
    from research_engine.experiments.strategy_horizon_interaction import run_s7
    from research_engine.experiments.strategy_identity_expectancy import run_s5

    reports = {
        "E3": run_e3([]),
        "S2": run_s2([]),
        "S3": run_s3([]),
        "S5": run_s5([]),
        "S6": run_s6([]),
        "S7": run_s7([]),
        "EXEC1": run_exec1([], [], []),
        "G1": run_g1(datasets={}),
    }
    for question_id, report in reports.items():
        assert report["reason_code"] == QUESTION_REASONS[question_id]
        assert report["reason"]
        assert report["reason_details"] == report["overall"]
        result = _normalise_report(
            REGISTRY_BY_ID[question_id], DEFINITIONS[question_id], report,
            EvidenceResolution(question_id, [], 0, 0, {}, []), _Context(), None,
        )
        assert result.reason_code == report["reason_code"]
        assert result.reason == report["reason"]
        assert result.reason_details == report["reason_details"]
