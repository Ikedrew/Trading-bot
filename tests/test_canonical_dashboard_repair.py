"""Focused P0 tests for the canonical operator path."""
from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest

import research_engine.dashboard as dashboard
import research_engine.control_plane.state_builder as state_builder
from research_engine.control_plane.models import (
    QuestionState,
    ReadinessStatus,
    ReportValidity,
    RunnerStatus,
)
from research_engine.control_plane.operator_projection import (
    build_operator_projection,
    render_html,
    render_terminal,
)
from research_engine.registry.research_question_registry import REGISTRY


class _EmptyCandidates:
    def list_all(self):
        return []


def _states() -> list[QuestionState]:
    return [
        QuestionState(
            question_id=question.id,
            title=question.title,
            description=question.description,
            category=question.category.value,
            state_status="ERROR",
            readiness_status=ReadinessStatus.ERROR,
            readiness_reason="AWS unavailable",
            runner_status=(RunnerStatus.NO_RUNNER if not question.runner_module else RunnerStatus.READY),
            report_validity=ReportValidity.MISSING,
        )
        for question in REGISTRY
    ]


def test_repaired_control_plane_and_governance_modules_import():
    for module in (
        "research_engine.control_plane.state_builder",
        "research_engine.control_plane.decision_ledger",
        "research_engine.control_plane.application_ledger",
    ):
        assert importlib.import_module(module)


def test_governance_transition_validation_remains_fail_closed():
    from research_engine.control_plane.application_ledger import validate_application_transition
    from research_engine.control_plane.decision_ledger import validate_decision_transition

    assert validate_decision_transition("APPROVED", "REJECTED")[0] is False
    assert validate_application_transition("VERIFIED", "DEPLOYED")[0] is False


def test_decision_ledger_round_trip_and_transition_guard(tmp_path):
    from research_engine.control_plane.decision_ledger import DecisionLedger

    ledger = DecisionLedger(tmp_path / "decisions.jsonl")
    record = ledger.append("OPT-1", "E1", "APPROVED", timestamp="2026-09-13T00:00:00Z")

    assert ledger.list_all()[0].to_dict() == record.to_dict()
    with pytest.raises(ValueError, match="Invalid transition"):
        ledger.append("OPT-1", "E1", "REJECTED")


def test_application_ledger_round_trip_and_transition_guard(tmp_path):
    from research_engine.control_plane.application_ledger import ApplicationLedger

    ledger = ApplicationLedger(tmp_path / "application.jsonl")
    record = ledger.append(
        "OPT-1", "E1", "DEPLOYED",
        new_value="candidate", timestamp="2026-09-13T00:00:00Z",
    )

    assert ledger.list_all()[0].to_dict() == record.to_dict()
    with pytest.raises(ValueError, match="Invalid transition"):
        ledger.append("OPT-1", "E1", "NOT_APPLIED")


def test_build_all_isolates_one_question_resolution_failure(monkeypatch, tmp_path):
    original = state_builder.resolve_question_evidence

    def fail_one(question, snapshot):
        if question.id == "E2":
            raise RuntimeError("isolated resolver failure")
        return original(question, snapshot)

    monkeypatch.setattr(state_builder, "resolve_question_evidence", fail_one)
    states = state_builder.build_all_question_states(
        reports_dir=tmp_path,
        evidence_source={},
        candidate_registry=_EmptyCandidates(),
    )
    by_id = {state.question_id: state for state in states}

    assert len(states) == 70
    assert len(by_id) == 70
    assert by_id["E2"].state_status == "ERROR"
    assert by_id["E2"].readiness_reason == (
        "Question state resolution failed: RuntimeError: isolated resolver failure"
    )
    assert "E1" in by_id and "G3" in by_id


def test_dashboard_has_only_canonical_dependencies():
    source = inspect.getsource(dashboard)
    assert "build_all_question_states" in source
    assert "build_operator_projection" in source
    assert "registry_audit" not in source
    assert "question_bank" not in source
    assert "v10.cockpit" not in source


def test_terminal_and_html_render_the_same_projection():
    projection = build_operator_projection(_states())
    terminal = render_terminal(projection)
    html = render_html(projection)

    assert "Questions: 70" in terminal
    assert "70 questions" in html
    for question in REGISTRY:
        assert question.id in terminal
        assert f"<td>{question.id}</td>" in html
    assert "POSITIVE_EDGE" not in terminal
    assert "POSITIVE_EDGE" not in html


def test_html_cli_builds_state_and_projection_once(monkeypatch, tmp_path):
    states = _states()
    calls = {"states": 0, "projection": 0}
    real_projection = dashboard.build_operator_projection

    def build_states():
        calls["states"] += 1
        return states

    def build_projection(value):
        calls["projection"] += 1
        return real_projection(value)

    monkeypatch.setattr(dashboard, "build_all_question_states", build_states)
    monkeypatch.setattr(dashboard, "build_operator_projection", build_projection)
    monkeypatch.chdir(tmp_path)

    assert dashboard.main(["--html"]) == 0
    output = Path("reports/research/cockpit.html")
    assert output.is_file()
    content = output.read_text(encoding="utf-8")
    assert calls == {"states": 1, "projection": 1}
    assert all(f"<td>{question.id}</td>" in content for question in REGISTRY)
    assert "OPT-TEST" not in content


def test_terminal_cli_renders_when_evidence_states_are_errors(monkeypatch, capsys):
    monkeypatch.setattr(dashboard, "build_all_question_states", _states)

    assert dashboard.main([]) == 0
    output = capsys.readouterr().out
    assert "Questions: 70" in output
    assert "ERROR: 70" in output
