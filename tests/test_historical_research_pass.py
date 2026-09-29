from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from research_engine.control_plane.historical_research_pass import (
    FrozenDatasetSource,
    HistoricalQuestionResult,
    HistoricalResearchPass,
    _fingerprint,
    scientific_state_for_report,
)
from research_engine.experiments.execution_protection_research import run_x2


MANIFEST = Path("analysis/assurance/historical_research_pass_20260928.json")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _row(**changes: object) -> HistoricalQuestionResult:
    base = HistoricalQuestionResult(
        question_id="T1", canonical_question="test", initial_classification="HISTORICAL_RUN_REQUIRED",
        runner_status="AVAILABLE", runner_module="tests.fake", runner_function="run", runner_inputs=(),
        candidate_historical_population=10, usable_historical_population=10,
        excluded_historical_population=0, unexplained_historical_population=0,
        historical_exhaustion_state="EXHAUSTED", current_report_before="MISSING",
        current_result_valid_before=False, current_report_status="MISSING", current_report_path="",
        result_fingerprint="", scientific_state="IMPLEMENTATION_BLOCKED",
        assurance_status="NOT_REQUALIFIED", next_action="RUN_FROZEN_HISTORY", reason="test",
    )
    return replace(base, **changes)


def test_frozen_source_is_mandatory_and_has_no_missing_fallback() -> None:
    source = FrozenDatasetSource({"execution_results": [{"id": 1}]})
    assert source.read_dataset("execution_results_v1") == [{"id": 1}]
    with pytest.raises(RuntimeError, match="FROZEN_DATASET_NOT_AVAILABLE"):
        source.read_dataset("unfrozen_live_dataset")


def test_zero_population_complete_is_normalised_to_insufficient() -> None:
    coordinator = HistoricalResearchPass.__new__(HistoricalResearchPass)
    coordinator.analysis_time = "2026-09-28T00:00:00Z"
    coordinator.upstream_fingerprints = {"evidence_snapshot": "a" * 64, "reconstruction": "b" * 64}
    question = SimpleNamespace(id="T1", runner_module="tests.fake", runner_function="run")
    report = coordinator._prepare_report(
        question, {"question_id": "T1", "status": "COMPLETE", "dataset": {"sample_size": 0}}, _row()
    )
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["scientific_state"] == "INSUFFICIENT_DATA"


def test_negative_and_no_effect_states_are_not_discarded() -> None:
    assert scientific_state_for_report({"status": "COMPLETE", "overall": {"finding": "NEGATIVE RESULT"}}) == "NEGATIVE_RESULT"
    assert scientific_state_for_report({"status": "COMPLETE", "overall": {"finding": "VALID NULL"}}) == "NO_EFFECT"


def test_completed_blocked_report_is_not_mislabelled_implementation_failure() -> None:
    report = {
        "question_id": "D4", "status": "BLOCKED", "scientific_state": "IMPLEMENTATION_BLOCKED",
        "historical_pass": {"usable_records": 635},
    }
    assert scientific_state_for_report(report) == "HISTORICALLY_UNANSWERABLE"


def test_g1_blocked_result_is_a_scientific_negative() -> None:
    report = {
        "question_id": "G1", "status": "BLOCKED", "scientific_state": "IMPLEMENTATION_BLOCKED",
        "historical_pass": {"usable_records": 14046},
    }
    assert scientific_state_for_report(report) == "NEGATIVE_RESULT"


def test_x2_empty_optional_attempt_population_retains_current_provenance() -> None:
    results = [
        {
            "schema_version": "execution_results_v1", "data_epoch": "CURRENT",
            "correlation_id": f"c-{index}", "canonical_opportunity_id": f"o-{index}",
            "symbol": "EURUSD", "result_ok": True, "retcode": 10009,
        }
        for index in range(30)
    ]
    with patch("research_engine.experiments.execution_protection_research._load_results", return_value=results), patch(
        "research_engine.experiments.execution_protection_research._load_attempts", return_value=[]
    ):
        report = run_x2()
    assert report["status"] == "COMPLETE"
    assert report["epoch"] == "CURRENT"
    assert report["fingerprint"]["evidence_provenance"]["state"] == "CURRENT"


def test_manifest_has_all_70_ids_exactly_once() -> None:
    rows = _manifest()["questions"]
    ids = [row["question_id"] for row in rows]
    assert len(ids) == len(set(ids)) == 70


def test_manifest_has_no_unexplained_runner_failure() -> None:
    rows = _manifest()["questions"]
    assert not [row for row in rows if row["runner_failed"]]
    assert all(row["scientific_state"] != "" for row in rows)


def test_waiting_data_requires_exhausted_balanced_history() -> None:
    waiting = [row for row in _manifest()["questions"] if row["scientific_state"] == "WAITING_DATA"]
    assert waiting
    assert all(row["historical_exhaustion_state"] == "EXHAUSTED" for row in waiting)
    assert all(row["unexplained_historical_population"] == 0 for row in waiting)


def test_implementation_mismatches_fail_closed() -> None:
    rows = {row["question_id"]: row for row in _manifest()["questions"]}
    for question_id in ("R1", "R2", "G2", "EX2"):
        assert rows[question_id]["scientific_state"] == "IMPLEMENTATION_BLOCKED"
        assert "population" in rows[question_id]["reason"].lower() or "denominator" in rows[question_id]["reason"].lower()
        assert not rows[question_id]["runner_failed"]


def test_historical_impossibility_is_explicit_not_a_runner_error() -> None:
    row = next(row for row in _manifest()["questions"] if row["question_id"] == "EX10")
    assert row["usable_historical_population"] == 0
    assert row["historical_exhaustion_state"] == "EXHAUSTED"
    assert row["scientific_state"] == "HISTORICALLY_UNANSWERABLE"
    assert not row["runner_failed"]


def test_persisted_manifest_round_trips_deterministically() -> None:
    first = _manifest()
    second = json.loads(json.dumps(first, sort_keys=True))
    assert _fingerprint(first) == _fingerprint(second)

