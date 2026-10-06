"""Pass D0 — governed evaluation invalidation and same-snapshot re-evaluation.

Evidence identity and evaluation identity are independent.  A published question
result is current only when BOTH still match, so a changed evaluator must force
re-evaluation even when the immutable evidence snapshot is unchanged.
"""
from __future__ import annotations

from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from research_engine.v10.continuous.evaluation_identity import (
    EVALUATOR_CHANGED,
    STALE_EVALUATION,
    build_evaluation_identity,
    evaluation_identities,
    is_result_current,
    stale_question_ids,
)
from research_engine.v10.continuous.canonical_question_cycle import (
    REQUIRES_RECHECK,
    REQUIRES_REEVALUATION,
    UNAFFECTED,
    _load_registry,
    plan_affected_questions,
)
from research_engine.v10.continuous.question_cycle_state import (
    CanonicalQuestionResult,
    QuestionCycleStore,
)
from research_engine.v10.continuous.research_loop import (
    _is_current_successful_projection,
    _stale_evaluation_questions,
)
from research_engine.v10.continuous.research_projection import (
    PROJECTION_SCHEMA,
    ResearchProjectionStore,
    build_evaluation_refresh_projection,
)

REGISTRY_VERSION = "research_question_registry_v1"
SNAPSHOT = "ISNAP-D34D1B9F46DA67251CBA4051"
FINGERPRINT = "d34d1b9f46da67251cba405172a87fcb86c82e6055a01d74090ce0e2b5a748a6"


def _module(name="pkg.mod", **constants):
    module = ModuleType(name)
    for key, value in constants.items():
        setattr(module, key, value)
    return module


def _question(question_id="L3", module="pkg.mod", function="run"):
    # ``data_sources`` is required by the governed definition builder.
    return SimpleNamespace(id=question_id, runner_module=module,
                           runner_function=function, data_sources=())


def _definition(definition_version=1):
    return SimpleNamespace(definition_version=definition_version)


def _identity(question=None, definition=None, monkeypatch=None, module=None):
    if module is not None:
        import research_engine.v10.continuous.evaluation_identity as identity_module
        monkeypatch.setattr(identity_module, "_load_runner_module",
                            lambda _path: module)
    return build_evaluation_identity(
        question or _question(), definition or _definition(),
        registry_version=REGISTRY_VERSION)


def _result(evaluation_identity, question_id="L3", snapshot_id=SNAPSHOT):
    return CanonicalQuestionResult(
        question_id=question_id,
        question_version=f"{REGISTRY_VERSION}:1",
        snapshot_id=snapshot_id,
        snapshot_fingerprint=FINGERPRINT,
        investigation_epoch="INVESTIGATION-EPOCH-D34D1B9F46DA67251CBA4051",
        evaluated_at="2026-10-02T00:00:00+00:00",
        status="COMPLETE",
        evaluation_identity=evaluation_identity,
        evaluation_identity_digest=(None if evaluation_identity is None else
                                    evaluation_identity.get(
                                        "evaluation_identity_digest")),
    )


# --- A: same evidence + same evaluator -> true no-op ------------------------

def test_same_evidence_and_same_evaluator_is_current():
    identity = _identity()
    assert is_result_current(identity, identity) == (True, None)


def _projection_with_identity(identity, count=70):
    """A retained projection whose every question result is current."""
    rows = [{"question_id": f"Q{i:03d}", "result": {"evaluation_identity": identity}}
            for i in range(1, count + 1)]
    return {
        "projection_version": "RPROJ-1",
        "data_frontier": {"snapshot_id": SNAPSHOT,
                          "last_successful_research_cycle": "CRCYCLE-1"},
        "canonical_questions": rows,
    }


def test_matching_projection_is_a_true_noop():
    projection = _projection_with_identity(_identity())
    frontier = SimpleNamespace(snapshot_id=SNAPSHOT)
    assert _is_current_successful_projection(projection, frontier, {}) is True
    assert _is_current_successful_projection(projection, frontier, None) is True


# --- B: new evidence + same evaluator -> normal frontier rerun --------------

def test_changed_evidence_is_not_current_regardless_of_evaluator():
    projection = _projection_with_identity(_identity())
    projection["data_frontier"]["snapshot_id"] = "ISNAP-OTHER"
    assert _is_current_successful_projection(
        projection, SimpleNamespace(snapshot_id=SNAPSHOT), {}) is False


# --- C: same evidence + changed runner identity -> re-evaluation ------------

def test_changed_runner_identity_forces_reevaluation():
    is_current, reason = is_result_current(
        _identity(), _identity(_question(module="pkg.other")))
    assert is_current is False
    assert reason == EVALUATOR_CHANGED


# --- D: same evidence + changed question-definition version ----------------

def test_changed_question_definition_version_forces_reevaluation():
    is_current, reason = is_result_current(
        _identity(), _identity(definition=_definition(definition_version=2)))
    assert is_current is False
    assert reason == STALE_EVALUATION


# --- E: same evidence + changed report/governance semantic version ---------

def test_changed_report_schema_version_forces_reevaluation(monkeypatch):
    fake = _module(EVALUATOR_REPORT_SCHEMA_VERSIONS={
        "run": {"REPORT_SCHEMA_VERSION": "report_v1"}})
    recorded = _identity(monkeypatch=monkeypatch, module=fake)
    fake.EVALUATOR_REPORT_SCHEMA_VERSIONS["run"]["REPORT_SCHEMA_VERSION"] = "report_v2"
    current = _identity(monkeypatch=monkeypatch, module=fake)
    assert current["report_schema_versions"] == {"REPORT_SCHEMA_VERSION": "report_v2"}
    is_current, reason = is_result_current(recorded, current)
    assert is_current is False
    assert reason == STALE_EVALUATION


def test_changed_evaluator_semantic_version_forces_reevaluation(monkeypatch):
    fake = _module(EVALUATOR_SEMANTIC_VERSIONS={"run": "semantic_v1"})
    recorded = _identity(monkeypatch=monkeypatch, module=fake)
    fake.EVALUATOR_SEMANTIC_VERSIONS["run"] = "semantic_v2"
    current = _identity(monkeypatch=monkeypatch, module=fake)
    is_current, reason = is_result_current(recorded, current)
    assert is_current is False
    assert reason == EVALUATOR_CHANGED


def test_changed_adjudication_contract_version_forces_reevaluation(monkeypatch):
    fake = _module(EVALUATOR_GOVERNANCE_CONTRACT_VERSIONS={
        "run": {"CONTRACT_VERSION": "contract_v1"}})
    recorded = _identity(monkeypatch=monkeypatch, module=fake)
    fake.EVALUATOR_GOVERNANCE_CONTRACT_VERSIONS["run"]["CONTRACT_VERSION"] = "contract_v2"
    current = _identity(monkeypatch=monkeypatch, module=fake)
    is_current, reason = is_result_current(recorded, current)
    assert is_current is False
    assert reason == STALE_EVALUATION


# --- F: incidental non-semantic change -> no false invalidation ------------

def test_incidental_module_attributes_do_not_change_identity(monkeypatch):
    fake = _module(EVALUATOR_SEMANTIC_VERSIONS={"run": "semantic_v1"})
    before = _identity(monkeypatch=monkeypatch, module=fake)
    fake.logger = object()            # logging/comment-only change
    fake._HELPER = lambda: None
    after = _identity(monkeypatch=monkeypatch, module=fake)
    assert before == after
    assert is_result_current(before, after) == (True, None)


def test_incidental_call_site_change_does_not_change_identity():
    before = _identity()
    after = _identity(_question(question_id="L3", module="pkg.mod", function="run"))
    assert before == after
    assert is_result_current(before, after) == (True, None)


# --- legacy migration ------------------------------------------------------

def test_legacy_result_without_identity_is_retained_when_no_contract():
    assert _result(None).evaluation_identity is None
    assert is_result_current(None, _identity()) == (True, None)


def test_legacy_result_is_stale_once_evaluator_declares_a_contract(monkeypatch):
    fake = _module(EVALUATOR_SEMANTIC_VERSIONS={"run": "semantic_v2"})
    current = _identity(monkeypatch=monkeypatch, module=fake)
    is_current, reason = is_result_current(None, current)
    assert is_current is False
    assert reason == STALE_EVALUATION


def test_legacy_result_ids_are_unchanged_by_the_new_field():
    legacy = CanonicalQuestionResult(
        question_id="L3", question_version="research_question_registry_v1:1",
        snapshot_id=SNAPSHOT, snapshot_fingerprint=FINGERPRINT,
        investigation_epoch="E", evaluated_at="T", status="COMPLETE")
    assert legacy.evaluation_identity is None
    assert legacy.evaluation_identity_digest is None
    assert legacy.to_dict()["result_schema"] == "canonical_question_result_v1"
# --- G: old projection remains immutable -----------------------------------

def test_old_projection_and_history_survive_reevaluation(tmp_path):
    from research_engine.v10.continuous.evaluation_identity import (
        evaluation_identity_digest)
    store = QuestionCycleStore(tmp_path)
    identity_v1 = _identity()
    result_v1 = _result(identity_v1)
    # The historical result was published before governed evaluation identity
    # existed, so it occupies the legacy digest-less history path.
    store.save_question_result(_result(None))
    legacy_path = store.legacy_question_result_path("L3", SNAPSHOT)
    legacy_bytes = legacy_path.read_bytes()

    identity_v2 = dict(identity_v1)
    identity_v2["evaluator_semantic_version"] = "semantic_v2"
    identity_v2["evaluation_identity_digest"] = evaluation_identity_digest(identity_v2)
    result_v2 = _result(identity_v2)
    store.save_question_result(result_v2)

    # The historical payload is untouched and still readable.
    assert legacy_path.read_bytes() == legacy_bytes
    assert store.load_question_result("L3", SNAPSHOT).evaluation_identity is None
    # The new result is a distinct governed artefact on the SAME snapshot.
    assert result_v2.snapshot_id == result_v1.snapshot_id
    assert result_v2.result_id != result_v1.result_id
    reloaded = store.load_question_result(
        "L3", SNAPSHOT, result_v2.evaluation_identity_digest)
    assert reloaded.result_id == result_v2.result_id


def test_same_snapshot_evaluation_refresh_publishes_new_immutable_projection(tmp_path):
    rows = [
        {"question_id": f"Q{i:03d}", "result": {"snapshot_id": SNAPSHOT}}
        for i in range(1, 71)
    ]
    predecessor = {
        "projection_schema": PROJECTION_SCHEMA,
        "projection_version": "RPROJ-OLD",
        "continuous_cycle_id": "CRCYCLE-OLD",
        "data_frontier": {"snapshot_id": SNAPSHOT, "fingerprint": FINGERPRINT},
        "canonical_questions": rows,
        "generated_questions": [], "findings": [], "hypotheses": [], "candidates": [],
        "investigations_and_work_queues": {}, "what_changed": {},
        "predecessor_projection_version": None,
    }
    question_projection = {"questions": {
        row["question_id"]: {
            **row,
            "result": {**row["result"], "evaluation_identity_digest": "digest-v2"},
        }
        for row in rows
    }}
    store = ResearchProjectionStore(tmp_path / "projections")
    old_path = store.save(predecessor)
    old_bytes = old_path.read_bytes()
    refreshed = build_evaluation_refresh_projection(
        continuous_cycle_id="CRCYCLE-NEW",
        frontier=SimpleNamespace(
            snapshot_id=SNAPSHOT, fingerprint=FINGERPRINT,
            investigation_epoch="E", frontier_start="S", frontier_end="E",
            predecessor_snapshot_id=SNAPSHOT, changed_datasets=(),
            stale_datasets=(), missing_optional_datasets=(),
            status="NO_NEW_GOVERNED_EVIDENCE"),
        question_projection=question_projection,
        predecessor_projection=predecessor,
        refreshed_question_ids=("Q001",),
    )
    new_path = store.save(refreshed)
    assert refreshed["projection_version"] != predecessor["projection_version"]
    assert refreshed["data_frontier"]["snapshot_id"] == SNAPSHOT
    assert refreshed["canonical_questions"][0]["result"][
        "evaluation_identity_digest"] == "digest-v2"
    assert new_path != old_path
    assert old_path.read_bytes() == old_bytes


# --- H: new result shares snapshot, new evaluation identity ----------------

def test_new_result_shares_snapshot_but_not_evaluation_identity():
    old_result = _result(_identity())
    new_result = _result(_identity(_question(module="pkg.other")))
    assert old_result.snapshot_id == new_result.snapshot_id
    assert old_result.snapshot_fingerprint == new_result.snapshot_fingerprint
    assert (old_result.evaluation_identity_digest
            != new_result.evaluation_identity_digest)


# --- I: downstream only when required --------------------------------------

def test_stale_set_is_empty_when_everything_is_current():
    identities = {"L3": _identity()}
    assert stale_question_ids({"L3": identities["L3"]}, identities) == {}


# --- real registry: the eight known stale questions ------------------------

KNOWN_STALE = ("L3", "G2", "EX2", "D1", "EXEC1", "G1", "X6", "L6")


def _live_identities():
    from research_engine.registry.definition_validator import build_definitions_from_registry
    questions = _load_registry()
    return evaluation_identities(
        questions, build_definitions_from_registry(questions),
        registry_version=REGISTRY_VERSION)


@pytest.mark.parametrize("question_id", KNOWN_STALE)
def test_known_stale_questions_are_detected(question_id):
    identities = _live_identities()
    assert identities[question_id]["evaluator_semantic_version"], (
        f"{question_id} must declare a governed evaluator semantic version")
    assert question_id in stale_question_ids({question_id: None}, identities)


def test_only_questions_with_a_declared_contract_are_invalidated():
    identities = _live_identities()
    stale = stale_question_ids({qid: None for qid in identities}, identities)
    assert set(stale) == set(KNOWN_STALE)
    for question_id in stale:
        assert identities[question_id]["evaluator_semantic_version"], (
            f"{question_id} was invalidated without a declared evaluator contract")


# --- orchestration ---------------------------------------------------------

def test_no_new_evidence_with_stale_evaluator_is_not_a_noop():
    identities = _live_identities()
    projection = {
        "projection_version": "RPROJ-1",
        "data_frontier": {"snapshot_id": SNAPSHOT,
                          "last_successful_research_cycle": "CRCYCLE-1"},
        "canonical_questions": [
            {"question_id": qid, "result": {"evaluation_identity": identities[qid]}}
            for qid in sorted(identities)],
    }
    frontier = SimpleNamespace(snapshot_id=SNAPSHOT)
    # Every evaluator is current, so this is a genuine no-op...
    assert _stale_evaluation_questions(projection, question_state_dir=Path(".")) == {}
    assert _is_current_successful_projection(projection, frontier, {}) is True
    # ...but once one evaluator changes, the same projection is no longer current
    # and the cycle must re-evaluate rather than skip.
    assert _is_current_successful_projection(
        projection, frontier, {"L3": STALE_EVALUATION}) is False


def test_stale_detection_flags_the_known_stale_questions_over_a_projection():
    identities = _live_identities()
    projection = {
        "canonical_questions": [
            {"question_id": qid, "result": {"evaluation_identity": None}}
            for qid in sorted(identities)],
    }
    stale = _stale_evaluation_questions(projection, question_state_dir=Path("."))
    assert set(stale) == set(KNOWN_STALE)


def test_shared_module_runner_version_does_not_cross_invalidate(monkeypatch):
    fake = _module(EVALUATOR_SEMANTIC_VERSIONS={"run_a": "a_v1"})
    import research_engine.v10.continuous.evaluation_identity as identity_module
    monkeypatch.setattr(identity_module, "_load_runner_module", lambda _path: fake)
    questions = (
        _question("A", function="run_a"),
        _question("B", function="run_b"),
    )
    definitions = {"A": _definition(), "B": _definition()}
    before = evaluation_identities(
        questions, definitions, registry_version=REGISTRY_VERSION)
    fake.EVALUATOR_SEMANTIC_VERSIONS["run_a"] = "a_v2"
    after = evaluation_identities(
        questions, definitions, registry_version=REGISTRY_VERSION)
    assert stale_question_ids(before, after) == {"A": EVALUATOR_CHANGED}
    assert before["B"] == after["B"]


def test_evaluator_only_plan_excludes_normal_rechecks():
    questions = _load_registry()
    previous = {"questions": {
        question.id: {"result": {"status": "WAITING_FOR_DATA"}}
        for question in questions
    }}
    context = SimpleNamespace(changed_datasets_known=True, changed_datasets=())
    evaluator_only = plan_affected_questions(
        questions, context, previous, _live_identities(),
        evaluation_only_question_ids=KNOWN_STALE)
    assert {qid for qid, state in evaluator_only.items()
            if state == REQUIRES_REEVALUATION} == set(KNOWN_STALE)
    unrelated = {"D6", "HORIZON-1", "MGMT-1", "MGMT-2",
                 "OPP-1", "PORT-1", "STRAT-1"}
    assert all(evaluator_only[qid] == UNAFFECTED for qid in unrelated)

    evidence_driven = plan_affected_questions(
        questions, context, previous, identities=None)
    assert all(evidence_driven[qid] == REQUIRES_RECHECK for qid in unrelated)


def test_no_production_hardcoded_known_stale_list():
    root = Path(__file__).parents[1]
    for relative in (
        "research_engine/v10/continuous/evaluation_identity.py",
        "research_engine/v10/continuous/canonical_question_cycle.py",
        "research_engine/v10/continuous/research_loop.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "KNOWN_STALE" not in source
        assert repr(KNOWN_STALE) not in source


def test_stale_detection_fails_closed_when_identity_is_unavailable():
    def _boom():
        raise RuntimeError("registry unavailable")

    projection = {"canonical_questions": [{"question_id": "L3", "result": {}}]}
    stale = _stale_evaluation_questions(
        projection, question_state_dir=Path("."), registry_loader=_boom)
    assert stale, "cannot prove current -> must not skip re-evaluation"
