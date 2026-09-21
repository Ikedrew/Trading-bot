"""Focused acceptance tests for the canonical read-only control plane."""
from __future__ import annotations

import json
from pathlib import Path

from research_engine.control_plane import (
    ApplicationStatus,
    ReportValidity,
    RunnerStatus,
    build_all_question_states,
    build_question_state,
)
from research_engine.registry.research_question_registry import REGISTRY, get_question
from research_engine.v10.candidates import CandidateRecord, CandidateRegistry


def _current_report(question_id: str, *, recommendation: str = "MONITOR") -> dict:
    return {
        "question_id": question_id,
        "status": "COMPLETE",
        "overall": {"finding": "Current finding", "confidence": "HIGH"},
        "confidence": "HIGH",
        "dataset": {"source": "shadow_trades", "sample_size": 125},
        "fingerprint": {
            "epoch": "CURRENT",
            "architecture_version": "new_pipeline_v1.2",
            "records_used": 125,
            "source": "shadow_trades",
        },
        "recommendation": recommendation,
        "warnings": [],
        "generated": "2026-09-13T00:00:00Z",
    }


def _write_report(root: Path, filename: str, report: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / filename).write_text(json.dumps(report), encoding="utf-8")


def test_all_states_use_canonical_identity(tmp_path):
    states = build_all_question_states(reports_dir=tmp_path, evidence_source={})

    assert len(states) == len(REGISTRY)
    assert [state.question_id for state in states] == [question.id for question in REGISTRY]
    for state, question in zip(states, REGISTRY):
        assert state.title == question.title
        assert state.description == question.description
        assert "research_question_registry.REGISTRY" in state.canonical_registry


def test_questions_without_runner_are_not_dropped(tmp_path):
    states = build_all_question_states(reports_dir=tmp_path, evidence_source={})
    no_runner = {state.question_id for state in states if state.runner_status == RunnerStatus.NO_RUNNER}

    assert no_runner == {"X6", "L6", "G1", "G2", "G3"}
    assert all(state.state_status == "NO_RUNNER" for state in states if state.question_id in no_runner)


def test_known_invalid_report_cannot_surface_a_finding(tmp_path):
    report = _current_report("R3", recommendation="PROMOTE")
    report["fingerprint"]["dataset_id"] = "shadow_trades_2026-07-27"
    _write_report(tmp_path, "r3_probability_of_ruin.json", report)

    state = build_question_state("R3", reports_dir=tmp_path, evidence_source={})

    assert state.report_validity == ReportValidity.INVALIDATED
    assert state.readiness_status.value == "BLOCKED"
    assert state.latest_result is None
    assert state.latest_finding == ""


def test_stale_mixed_epoch_q19_cannot_surface_a_finding(tmp_path):
    report = _current_report("Q19")
    report["epoch"] = "MIXED"
    report["fingerprint"]["epoch"] = "MIXED"
    _write_report(tmp_path, "q19_expected_value.json", report)

    state = build_question_state("E1", reports_dir=tmp_path, evidence_source={})

    assert state.report_validity == ReportValidity.INVALIDATED
    assert state.latest_result is None
    assert state.latest_finding == ""


def test_current_valid_legacy_alias_report_resolves(tmp_path):
    _write_report(tmp_path, "q19_expected_value.json", _current_report("Q19"))

    state = build_question_state("E1", reports_dir=tmp_path, evidence_source={})

    assert state.report_validity == ReportValidity.VALID_CURRENT
    assert state.state_status == "COMPLETE"
    assert state.current_sample_size == 125
    assert state.latest_finding == "Current finding"


def test_report_identity_must_match_canonical_or_declared_alias(tmp_path):
    _write_report(tmp_path, "q19_expected_value.json", _current_report("Q4"))

    state = build_question_state("E1", reports_dir=tmp_path, evidence_source={})

    assert state.report_validity == ReportValidity.INVALIDATED
    assert state.latest_result is None


def test_missing_report_is_clean_not_run(tmp_path):
    state = build_question_state("E4", reports_dir=tmp_path, evidence_source={})

    assert state.report_validity == ReportValidity.MISSING
    assert state.latest_report_status == "NOT_RUN"
    assert state.state_status == "BLOCKED"
    assert state.current_sample_size is None


def test_candidate_projection_does_not_mutate_lifecycle(tmp_path):
    candidate_dir = tmp_path / "candidates"
    registry = CandidateRegistry(storage_dir=str(candidate_dir))
    registry.create(CandidateRecord(
        candidate_id="OPT-READ-ONLY",
        created_from_question="E1",
        status="PROPOSED",
        description="Read-only projection test",
    ))
    registry_file = candidate_dir / "candidates.jsonl"
    before = registry_file.read_bytes()

    state = build_question_state(
        "E1",
        reports_dir=tmp_path / "reports",
        evidence_source={},
        candidate_registry=registry,
    )

    assert state.candidate_status == "PROPOSED"
    assert state.candidate_id == "OPT-READ-ONLY"
    assert registry_file.read_bytes() == before
    assert registry.get("OPT-READ-ONLY").status == "PROPOSED"


def test_unmapped_candidate_is_exposed_without_guessing_a_question(tmp_path):
    registry = CandidateRegistry(storage_dir=str(tmp_path / "candidates"))
    registry.create(CandidateRecord(
        candidate_id="OPT-UNMAPPED",
        created_from_question="",
        status="PROPOSED",
    ))

    state = build_question_state(
        "E1",
        reports_dir=tmp_path / "reports",
        evidence_source={},
        candidate_registry=registry,
    )

    assert state.candidate_status == "NONE"
    assert state.candidate_mapping_status == "UNMAPPED"
    assert state.unmapped_candidate_ids == ["OPT-UNMAPPED"]


def test_promote_recommendation_is_not_production_application(tmp_path):
    _write_report(
        tmp_path,
        "q19_expected_value.json",
        _current_report("Q19", recommendation="PROMOTE"),
    )

    state = build_question_state("E1", reports_dir=tmp_path, evidence_source={})

    assert state.production_application_status == ApplicationStatus.UNKNOWN
    assert state.production_application_status != ApplicationStatus.APPLIED


def test_output_is_deterministic_and_json_serializable(tmp_path):
    first = [state.to_dict() for state in build_all_question_states(
        reports_dir=tmp_path, evidence_source={}
    )]
    second = [state.to_dict() for state in build_all_question_states(
        reports_dir=tmp_path, evidence_source={}
    )]

    assert first == second
    json.dumps(first, sort_keys=True)


def test_q1_q25_dashboard_is_not_required(tmp_path, monkeypatch):
    dashboard = Path("analysis/summaries/research_dashboard.json")

    def forbidden_read_text(self, *args, **kwargs):
        if self == dashboard:
            raise AssertionError("legacy dashboard was read")
        return original_read_text(self, *args, **kwargs)

    original_read_text = Path.read_text
    monkeypatch.setattr(Path, "read_text", forbidden_read_text)

    states = build_all_question_states(reports_dir=tmp_path, evidence_source={})

    assert len(states) == len(REGISTRY)
    assert states[0].title == get_question(states[0].question_id).title


def test_required_sample_is_only_exposed_when_registry_declares_it(tmp_path):
    assert build_question_state(
        "EX1", reports_dir=tmp_path, evidence_source={}
    ).required_sample_size == 200
    assert build_question_state(
        "E1", reports_dir=tmp_path, evidence_source={}
    ).required_sample_size is None


def test_unique_canonical_legacy_alias_can_be_used_for_lookup(tmp_path):
    state = build_question_state("Q20", reports_dir=tmp_path, evidence_source={})

    assert state.question_id == "D2"
    assert state.title == get_question("D2").title
    assert state.production_application_status == ApplicationStatus.NOT_APPLIED
