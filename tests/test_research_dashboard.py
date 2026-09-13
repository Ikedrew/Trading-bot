"""Canonical Research Engine dashboard contract tests."""
from __future__ import annotations

from research_engine.control_plane.models import QuestionState, ReadinessStatus
from research_engine.dashboard import can_execute, generate_dashboard, get_execution_gate
from research_engine.registry.research_question_registry import REGISTRY


def _state(question, status: str) -> QuestionState:
    return QuestionState(
        question_id=question.id,
        title=question.title,
        description=question.description,
        category=question.category.value,
        state_status=status,
        readiness_status=ReadinessStatus(status),
        readiness_reason=f"canonical {status}",
    )


def test_generate_dashboard_projects_all_canonical_states():
    states = [_state(question, "WAITING_DATA") for question in REGISTRY]

    projection = generate_dashboard(states)

    assert projection["total_questions"] == len(REGISTRY)
    assert projection["state_counts"]["WAITING_DATA"] == len(REGISTRY)
    assert [item["question_id"] for item in projection["questions"]] == [
        question.id for question in REGISTRY
    ]


def test_execution_gate_uses_canonical_readiness():
    states = [_state(question, "WAITING_DATA") for question in REGISTRY]
    states[0] = _state(REGISTRY[0], "READY")
    projection = generate_dashboard(states)

    assert can_execute(REGISTRY[0].id, projection) is True
    assert can_execute(REGISTRY[1].id, projection) is False
    assert get_execution_gate(REGISTRY[0].id, projection) == {
        "allowed": True,
        "question_id": REGISTRY[0].id,
        "status": "READY",
        "reason": "canonical READY",
        "requirements": [],
    }


def test_unknown_question_fails_closed():
    projection = generate_dashboard([])

    assert can_execute("Z99", projection) is False
    assert get_execution_gate("Z99", projection)["status"] == "NOT_FOUND"
