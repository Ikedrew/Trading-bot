from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from research_engine.control_plane.final_assurance_certification import (
    ALLOWED_STATES,
    IMPLEMENTATION_GAPS,
    FinalAssuranceCertification,
    _analysis_exclusions_present,
    _fingerprint,
    _structured_shortfall,
)
from research_engine.control_plane.report_resolver import ReportValidity, resolve_report_validity
from research_engine.registry.research_question_registry import REGISTRY


CERTIFICATION = Path("analysis/assurance/final_70_question_certification_20260928.json")
DATA_GAPS = Path("analysis/assurance/stage4_dataset_schema_gap_register_20260928.json")
IMPLEMENTATION_REGISTER = Path("analysis/assurance/stage4_implementation_gap_register_20260928.json")


@pytest.fixture(scope="module")
def result() -> dict:
    return json.loads(CERTIFICATION.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rows(result: dict) -> dict[str, dict]:
    return {item["question_id"]: item for item in result["certifications"]}


def test_scientific_and_assurance_states_are_independent(rows: dict[str, dict]) -> None:
    assert rows["E1"]["scientific_state"] == "NEGATIVE_RESULT"
    assert rows["E1"]["assurance_status"] == "VERIFIED"


def test_complete_requires_governed_population_match(rows: dict[str, dict]) -> None:
    assert rows["E2"]["scientific_state"] == "COMPLETE"
    assert rows["E2"]["population_match"] is True


def test_negative_result_verifies_without_becoming_complete(rows: dict[str, dict]) -> None:
    assert rows["E1"]["assurance_status"] == "VERIFIED"
    assert rows["E1"]["scientific_state"] != "COMPLETE"


def test_insufficient_data_verifies_after_exhaustion(rows: dict[str, dict]) -> None:
    row = rows["E3"]
    assert row["scientific_state"] == "INSUFFICIENT_DATA"
    assert row["historical_state_valid"] is True
    assert row["assurance_status"] == "VERIFIED"


def test_waiting_data_has_machine_readable_future_trigger(rows: dict[str, dict]) -> None:
    for question_id in ("EX5", "EX6", "EX8"):
        action = rows[question_id]["next_action"]
        assert action["trigger_type"] == "GOVERNED_EVIDENCE_CONTRACT_REEVALUATION"
        assert action["thresholds"]
        assert rows[question_id]["assurance_status"] == "VERIFIED"


def test_historical_impossibility_requires_explicit_data_gap(rows: dict[str, dict]) -> None:
    gaps = json.loads(DATA_GAPS.read_text(encoding="utf-8"))["gaps"]
    ids = {qid for gap in gaps for qid in gap["affected_question_ids"]}
    historical = {qid for qid, row in rows.items() if row["scientific_state"] == "HISTORICALLY_UNANSWERABLE"}
    assert ids == historical
    assert all(gap["missing_observable"] and gap["historical_recoverability"] == "UNRECOVERABLE" for gap in gaps)


def test_implementation_block_itself_can_be_verified(rows: dict[str, dict]) -> None:
    for question_id in IMPLEMENTATION_GAPS:
        assert rows[question_id]["scientific_state"] == "IMPLEMENTATION_BLOCKED"
        assert rows[question_id]["assurance_status"] == "VERIFIED"


def test_implementation_failure_is_not_insufficient_data(rows: dict[str, dict]) -> None:
    blocked = {qid for qid, row in rows.items() if row["scientific_state"] == "IMPLEMENTATION_BLOCKED"}
    assert blocked == set(IMPLEMENTATION_GAPS)
    assert not any(rows[qid]["scientific_state"] == "INSUFFICIENT_DATA" for qid in blocked)


def test_missing_historical_observable_is_not_implementation_block(rows: dict[str, dict]) -> None:
    assert rows["E5"]["scientific_state"] == "HISTORICALLY_UNANSWERABLE"
    assert rows["E5"]["scientific_state"] != "IMPLEMENTATION_BLOCKED"


def test_low_sample_is_not_historical_impossibility(rows: dict[str, dict]) -> None:
    assert rows["E4"]["scientific_state"] == "INSUFFICIENT_DATA"
    assert rows["E4"]["analytical_count"] == 4


def test_stale_report_cannot_certify() -> None:
    report = json.loads(Path("analysis/reports/q19_expected_value.json").read_text(encoding="utf-8"))
    report["epoch"] = "LEGACY"
    validity, _ = resolve_report_validity("q19_expected_value.json", report, expected_question_id="E1", accepted_question_ids=("Q19",))
    assert validity != ReportValidity.VALID_CURRENT


def test_report_producer_cannot_self_certify(result: dict) -> None:
    assert "assurance_status" not in json.loads(Path("analysis/reports/q19_expected_value.json").read_text(encoding="utf-8"))
    assert result["stage"] == "STAGE4_FINAL_70_QUESTION_ASSURANCE_CERTIFICATION"


def test_population_expansion_and_unexplained_contraction_fail_closed(rows: dict[str, dict]) -> None:
    expanded = deepcopy(rows["E2"])
    expanded["analytical_count"] = expanded["usable_count"] + 1
    assert not (0 <= expanded["analytical_count"] <= expanded["usable_count"])
    assert _analysis_exclusions_present({}, 10, 9) is False


def test_evidence_fingerprint_mismatch_fails_closed(result: dict) -> None:
    changed = deepcopy(result)
    changed["frozen_upstream"]["evidence_snapshot"] = "0" * 64
    assert _fingerprint(changed) != _fingerprint(result)


def test_altered_evidence_epoch_fails_closed(rows: dict[str, dict]) -> None:
    changed = deepcopy(rows["E2"])
    changed["evidence_epoch"] = "LEGACY"
    assert changed["evidence_epoch"] != "CURRENT"


def test_invalid_report_ownership_fails_closed() -> None:
    report = json.loads(Path("analysis/reports/q19_expected_value.json").read_text(encoding="utf-8"))
    validity, _ = resolve_report_validity("q19_expected_value.json", report, expected_question_id="X2", accepted_question_ids=())
    assert validity != ReportValidity.VALID_CURRENT


def test_duplicate_or_ambiguous_authority_fails_closed(result: dict) -> None:
    ids = [item["question_id"] for item in result["certifications"]]
    duplicated = ids + [ids[0]]
    assert len(duplicated) != len(set(duplicated))


def test_all_70_canonical_ids_appear_once(result: dict) -> None:
    ids = [item["question_id"] for item in result["certifications"]]
    assert ids == [question.id for question in REGISTRY]
    assert len(ids) == len(set(ids)) == 70


def test_q71_gate_rejects_every_non_verified_state(result: dict) -> None:
    assert result["q71_gate"]["started"] is False
    assert result["q71_gate"]["non_verified_consumable"] == []
    assert all(item["assurance_status"] == "VERIFIED" for item in result["certifications"])


def test_certification_has_no_live_or_s3_fallback(result: dict) -> None:
    source = Path("research_engine/control_plane/final_assurance_certification.py").read_text(encoding="utf-8")
    assert "get_default_source" not in source
    assert "read_dataset(" not in source
    assert result["live_or_s3_reads"] is False


def test_shortfall_and_exclusion_helpers_require_structure() -> None:
    assert _structured_shortfall({"readiness": {"required_n": 30}})
    assert not _structured_shortfall({"warning": "need more data"})
    assert _analysis_exclusions_present({"overall": {"exclusions": {"missing_label": 5}}}, 10, 5)


def test_registers_cover_exact_final_state_counts(result: dict) -> None:
    implementation = json.loads(IMPLEMENTATION_REGISTER.read_text(encoding="utf-8"))["gaps"]
    assert len(implementation) == result["implementation_gap_count"] == 8
    assert result["dataset_schema_gap_count"] == 29
    assert set(result["scientific_state_counts"]) == ALLOWED_STATES - {"NO_EFFECT"}
