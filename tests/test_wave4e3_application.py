"""Wave 4E.3: persisted effective approval -> eligibility, never deployment."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.research_events import compute_config_hash
from research_engine.control_plane.application_ledger import (
    ApplicationLedger, ApplicationRecord, ApplicationState,
    validate_application_transition,
)
from research_engine.lifecycle.candidate_evaluator import CandidateEvaluation
from research_engine.lifecycle.candidate_recommendation import (
    RecommendationStore, create_recommendation,
)
from research_engine.v10.baselines import baseline_authority
from research_engine.v10.baselines.models import BaselineSnapshot
from research_engine.v10.baselines.snapshot_registry import SnapshotRegistry
from research_engine.v10.candidates.candidate_decision import (
    CandidateDecisionStore, HumanDecision, record_human_decision,
)
from research_engine.v10.candidates.candidate_registry import CandidateRegistry
from research_engine.v10.candidates.models import CandidateRecord, CandidateStatus


IDENTITIES = (
    "candidate_id", "recommendation_id", "evaluation_id", "treatment_id",
    "baseline_id", "baseline_config_hash",
)
NEW_FIELDS = (
    "recommendation_id", "evaluation_id", "treatment_id", "baseline_id",
    "baseline_config_hash", "human_decision_outcome",
)
CANDIDATE = "OPT-4E3-001"
EVALUATION = "EVAL-4E3-001"
RECOMMENDATION = f"REC-{EVALUATION}"
BASELINE = "V10_BASELINE_wave4e3test"


@pytest.fixture
def approval_env(tmp_path, monkeypatch):
    """Real Wave 4E.1/4E.2 authorities, all persistence under tmp_path."""
    baseline_dir = str(tmp_path / "baselines")
    monkeypatch.setattr(baseline_authority, "_BASELINES_DIR", baseline_dir)
    monkeypatch.setattr(
        baseline_authority, "_ACTIVE_POINTER_FILE",
        str(Path(baseline_dir) / "active_baseline.json"),
    )
    config_hash = compute_config_hash()
    SnapshotRegistry(baselines_dir=baseline_dir).save(BaselineSnapshot(
        snapshot_id=BASELINE, config_hash=config_hash, identity_hash="wave4e3test",
    ))
    baseline_authority.set_active(BASELINE, actor="test", reason="isolated fixture")
    dirs = {
        "registry_dir": str(tmp_path / "candidates"),
        "decisions_dir": str(tmp_path / "decisions"),
        "recommendations_dir": str(tmp_path / "recommendations"),
    }
    registry = CandidateRegistry(storage_dir=dirs["registry_dir"])
    registry.create(CandidateRecord(
        candidate_id=CANDIDATE, baseline_id=BASELINE,
        created_from_question="E1", component="DIRECTION_INVERSION",
        change_definition={"type": "direction_inversion", "baseline_config_hash": config_hash},
        status=CandidateStatus.READY_FOR_REVIEW,
    ))
    registry.add_validation_result(
        CANDIDATE, validation_id=EVALUATION, decision="IMPROVED",
        confidence="HIGH", sample_size=60, expectancy_delta=0.25,
    )
    recommendation = create_recommendation(
        CandidateEvaluation(
            evaluation_id=EVALUATION, candidate_id=CANDIDATE,
            treatment_id="e3a11d0c4beef042", baseline_id=BASELINE,
            config_hash=config_hash, decision="VALIDATED", confidence="HIGH",
            eligible_pairs=60, survives_outlier_removal=True, risk_level="LOW",
        ),
        store=RecommendationStore(recommendations_dir=dirs["recommendations_dir"]),
    )
    assert recommendation is not None and recommendation.actionable
    return dirs, recommendation, tmp_path / "governance" / "application.jsonl"


def _approve(dirs, decision="ACCEPT"):
    result = record_human_decision(
        CANDIDATE, decision, RECOMMENDATION,
        actor="human-4e3", reason="exact evidence reviewed", **dirs,
    )
    assert result.ok
    return result.decision


def _create(dirs, path, candidate_id=CANDIDATE, recommendation_id=RECOMMENDATION):
    return ApplicationLedger(path).create_application_from_approval(
        candidate_id, recommendation_id, **dirs,
    )


def test_effective_approval_persists_exact_provenance_and_is_idempotent(approval_env):
    dirs, recommendation, path = approval_env
    decision = _approve(dirs)

    application = _create(dirs, path)

    assert application.state == ApplicationState.APPROVED_NOT_DEPLOYED
    assert application.canonical_question_id == "E1"
    assert application.actor == decision.actor == "human-4e3"
    assert application.reason == decision.reason == "exact evidence reviewed"
    assert application.human_decision_outcome == "COMPLETED"
    for name in IDENTITIES:
        assert getattr(application, name) == getattr(decision, name)
        assert getattr(application, name) == getattr(recommendation, name)
    assert application.deployment_reference == ""
    assert application.target_config_key == application.new_value == ""
    stored = ApplicationLedger(path).list_all()
    assert stored == [application]
    assert json.loads(path.read_text(encoding="utf-8")) == application.to_dict()
    before = path.read_bytes()
    assert _create(dirs, path) == application
    assert path.read_bytes() == before
    assert ApplicationLedger(path).list_all() == [application]


def _rewrite_row(directory, filename, **changes):
    """Corrupt isolated persisted evidence to exercise fail-closed reads."""
    path = Path(directory) / filename
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0].update(changes)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _assert_blocked(dirs, path, **kwargs):
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(ValueError):
        _create(dirs, path, **kwargs)
    assert (path.read_bytes() if path.exists() else None) == before
    if before is None:
        assert ApplicationLedger(path).list_all() == []
        assert not path.parent.exists()


def test_no_persisted_decision_fails_closed(approval_env):
    dirs, _, path = approval_env
    _assert_blocked(dirs, path)


def test_fabricated_in_memory_accept_cannot_authorize(approval_env):
    dirs, recommendation, path = approval_env
    fabricated = HumanDecision(
        **{name: getattr(recommendation, name) for name in IDENTITIES},
        decision="ACCEPT", outcome="COMPLETED", actor="fabricator", reason="invented",
    )
    store = CandidateDecisionStore(decisions_dir=dirs["decisions_dir"])
    store._decisions.append(fabricated)
    assert store.get_decision(CANDIDATE) is fabricated
    _assert_blocked(dirs, path)
    with pytest.raises(TypeError):
        ApplicationLedger(path).create_application_from_approval(
            CANDIDATE, RECOMMENDATION, decision=fabricated, **dirs,
        )
    assert not path.exists()


def test_persisted_reject_fails_closed(approval_env):
    dirs, _, path = approval_env
    _approve(dirs, "REJECT")
    _assert_blocked(dirs, path)


@pytest.mark.parametrize("outcome", [
    "RECOMMENDATION_BLOCKED", "BASELINE_BLOCKED", "STATUS_FAILED", "", "PENDING",
])
def test_non_effective_accept_fails_closed(approval_env, outcome):
    dirs, _, path = approval_env
    _approve(dirs)
    _rewrite_row(dirs["decisions_dir"], "decisions.jsonl", outcome=outcome)
    _assert_blocked(dirs, path)


@pytest.mark.parametrize("side", ["decision", "recommendation"])
@pytest.mark.parametrize("name", IDENTITIES)
def test_identity_substitution_fails_closed(approval_env, side, name):
    dirs, _, path = approval_env
    _approve(dirs)
    directory = dirs["decisions_dir" if side == "decision" else "recommendations_dir"]
    filename = "decisions.jsonl" if side == "decision" else "recommendations.jsonl"
    _rewrite_row(directory, filename, **{name: "substituted-identity"})
    _assert_blocked(dirs, path)


@pytest.mark.parametrize("side", ["decision", "recommendation", "both"])
@pytest.mark.parametrize("name", IDENTITIES)
@pytest.mark.parametrize("empty", ["", "   ", None])
def test_missing_identity_fails_closed(approval_env, side, name, empty):
    dirs, _, path = approval_env
    _approve(dirs)
    if side in ("decision", "both"):
        _rewrite_row(dirs["decisions_dir"], "decisions.jsonl", **{name: empty})
    if side in ("recommendation", "both"):
        _rewrite_row(dirs["recommendations_dir"], "recommendations.jsonl", **{name: empty})
    _assert_blocked(dirs, path)


def test_missing_recommendation_fails_closed_even_on_replay(approval_env):
    dirs, _, path = approval_env
    _approve(dirs)
    recommendation_path = Path(dirs["recommendations_dir"]) / "recommendations.jsonl"
    evidence = recommendation_path.read_bytes()
    recommendation_path.unlink()
    _assert_blocked(dirs, path)
    recommendation_path.write_bytes(evidence)
    _create(dirs, path)
    recommendation_path.unlink()
    _assert_blocked(dirs, path)


def test_other_recommendation_never_substituted_by_idempotency(approval_env):
    dirs, recommendation, path = approval_env
    _approve(dirs)
    application = _create(dirs, path)
    recommendation.evaluation_id = "OTHER-EVALUATION"
    recommendation.recommendation_id = "REC-OTHER-EVALUATION"
    RecommendationStore(dirs["recommendations_dir"]).append(recommendation)
    _assert_blocked(dirs, path, recommendation_id=recommendation.recommendation_id)
    assert ApplicationLedger(path).list_all() == [application]


def test_actor_reason_cannot_be_rewritten_by_caller_or_cached_object(approval_env):
    dirs, _, path = approval_env
    returned = _approve(dirs)
    returned.actor = "caller"
    returned.reason = "rewritten"
    cached = CandidateDecisionStore(dirs["decisions_dir"]).get_decision(CANDIDATE)
    cached.actor = "cached caller"
    cached.reason = "cached rewrite"
    with pytest.raises(TypeError):
        ApplicationLedger(path).create_application_from_approval(
            CANDIDATE, RECOMMENDATION, actor="caller", reason="rewrite", **dirs,
        )
    assert not path.exists()
    application = _create(dirs, path)
    assert application.actor == "human-4e3"
    assert application.reason == "exact evidence reviewed"


def test_creation_writes_only_ledger_and_ignores_mutable_identity(approval_env, tmp_path, monkeypatch):
    import ast
    import inspect
    import core.config as config
    from research_engine.control_plane import application_ledger

    dirs, recommendation, path = approval_env
    _approve(dirs)
    _rewrite_row(
        dirs["registry_dir"], "candidates.jsonl",
        baseline_id="mutable-baseline", validation_history=[],
        change_definition={"type": "changed", "baseline_config_hash": "mutable-hash"},
    )
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    active_before = baseline_authority.get_active()
    config_path = Path(config.__file__)
    config_before = config_path.read_bytes()
    hash_before = compute_config_hash()

    def forbidden(*args, **kwargs):
        pytest.fail("Application creation called a mutation/baseline scoring surface")

    monkeypatch.setattr(CandidateRegistry, "_persist", forbidden)
    monkeypatch.setattr(CandidateRegistry, "update_status", forbidden)
    monkeypatch.setattr(baseline_authority, "set_active", forbidden)
    monkeypatch.setattr(baseline_authority, "validate_candidate_baseline", forbidden)
    monkeypatch.setattr(CandidateDecisionStore, "_append_row_atomic", forbidden)
    monkeypatch.setattr(RecommendationStore, "append", forbidden)
    application = _create(dirs, path)

    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert set(after) - set(before) == {path}
    assert all(after[p] == content for p, content in before.items())
    assert baseline_authority.get_active() == active_before
    assert config_path.read_bytes() == config_before
    assert compute_config_hash() == hash_before
    for name in IDENTITIES:
        assert getattr(application, name) == getattr(recommendation, name)
    assert application.state == "APPROVED_NOT_DEPLOYED"
    assert application.deployment_reference == application.verification_evidence == ""
    # Executable names, not comments: no live/deployment/config-write calls.
    tree = ast.parse(inspect.getsource(application_ledger))
    names = {node.id.lower() for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names.update(node.attr.lower() for node in ast.walk(tree) if isinstance(node, ast.Attribute))
    assert not names.intersection({
        "mt5", "broker", "order_send", "deploy", "apply_candidate", "set_active",
        "update_status", "compute_config_hash", "change_definition",
    })


def test_legacy_row_loads_with_empty_provenance(tmp_path):
    path = tmp_path / "application.jsonl"
    legacy = {
        "application_id": "APP-legacy", "candidate_id": "OPT-legacy",
        "canonical_question_id": "E1", "state": "DEPLOYED",
        "occurred_at": "2026-09-13T00:00:00Z", "actor": "legacy-human",
        "new_value": "legacy-value", "reason": "legacy-reason",
    }
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    record, = ApplicationLedger(path).list_all()
    assert record == ApplicationRecord(**legacy)
    for name in NEW_FIELDS:
        assert getattr(record, name) == ""
    for name, value in legacy.items():
        assert getattr(record, name) == value


@pytest.mark.parametrize("current,new", [
    ("VERIFIED", "DEPLOYED"), ("DEPLOYED", "NOT_APPLIED"),
    ("DEPLOYED", "APPROVED_NOT_DEPLOYED"), ("NOT_APPLIED", "INVALID"),
])
def test_existing_transition_guards_fail_closed(tmp_path, current, new):
    assert validate_application_transition(current, new)[0] is False
    path = tmp_path / "application.jsonl"
    ledger = ApplicationLedger(path)
    ledger.append(CANDIDATE, "E1", current)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        ledger.append(CANDIDATE, "E1", new)
    assert path.read_bytes() == before


def test_governed_creation_does_not_bypass_transition_guard(approval_env):
    dirs, _, path = approval_env
    _approve(dirs)
    ApplicationLedger(path).append(CANDIDATE, "E1", "DEPLOYED")
    _assert_blocked(dirs, path)


def test_conflicting_existing_application_is_not_blessed(approval_env):
    dirs, _, path = approval_env
    _approve(dirs)
    _create(dirs, path)
    _rewrite_row(str(path.parent), path.name, treatment_id="conflicting-treatment")
    _assert_blocked(dirs, path)


@pytest.mark.parametrize("question,expected", [
    ("E1", "E1"), ("Q19", "E1"), ("Q24", ""), ("unknown", ""), ("", ""),
])
def test_question_attribution_uses_existing_canonical_resolver(approval_env, question, expected):
    dirs, _, path = approval_env
    _approve(dirs)
    _rewrite_row(dirs["registry_dir"], "candidates.jsonl", created_from_question=question)
    assert _create(dirs, path).canonical_question_id == expected


def test_no_second_recommendation_scoring_system(approval_env):
    dirs, _, path = approval_env
    _approve(dirs)
    _rewrite_row(
        dirs["recommendations_dir"], "recommendations.jsonl",
        actionable=False, eligible_pairs=0, confidence="INSUFFICIENT",
    )
    assert _create(dirs, path).state == "APPROVED_NOT_DEPLOYED"


