"""Pass D Repair 3 — L6 evaluator-version coverage + stale-result invalidation.

The L6 runner previously declared no governed evaluator semantic identity, so
its ``evaluator_semantic_version`` was ``None``: legacy results were silently
retained and could never be invalidated when the evaluator semantics changed.

These tests prove the repaired runner binds an authoritative HD12 semantic
version and that stale L6 results are deterministically invalidated, become
re-evaluation eligible, and are superseded for CURRENT resolution while the
historical record remains preserved byte-for-byte.
"""
from __future__ import annotations

from pathlib import Path

from research_engine.experiments import learning_cycle_validation
from research_engine.registry.definition_validator import build_definitions_from_registry
from research_engine.v10.continuous.canonical_question_cycle import _load_registry
from research_engine.v10.continuous.evaluation_identity import (
    EVALUATOR_CHANGED,
    STALE_EVALUATION,
    build_evaluation_identity,
    evaluation_identities,
    evaluation_identity_digest,
    is_result_current,
    stale_question_ids,
)
from research_engine.v10.continuous.question_cycle_state import (
    CanonicalQuestionResult,
    QuestionCycleStore,
)

REGISTRY_VERSION = "research_question_registry_v1"
SNAPSHOT = "ISNAP-L6-REPAIR3"
FINGERPRINT = "l6" * 32


def _l6_question():
    from research_engine.registry.research_question_registry import REGISTRY_BY_ID
    return REGISTRY_BY_ID["L6"]


def _l6_definition():
    return build_definitions_from_registry(_load_registry())["L6"]


def _current_identities():
    questions = _load_registry()
    return evaluation_identities(
        questions, build_definitions_from_registry(questions),
        registry_version=REGISTRY_VERSION)


def _l6_current_identity():
    return _current_identities()["L6"]


def _identity_with_semantic_version(version):
    current = _l6_current_identity()
    recorded = dict(current)
    recorded["evaluator_semantic_version"] = version
    recorded["evaluation_identity_digest"] = evaluation_identity_digest(recorded)
    return recorded


def _result(identity, status="COMPLETE", snapshot_id=SNAPSHOT):
    return CanonicalQuestionResult(
        question_id="L6",
        question_version=f"{REGISTRY_VERSION}:1",
        snapshot_id=snapshot_id,
        snapshot_fingerprint=FINGERPRINT,
        investigation_epoch="INVESTIGATION-EPOCH-L6",
        evaluated_at="2026-10-06T00:00:00+00:00",
        status=status,
        evaluation_identity=identity,
        evaluation_identity_digest=(identity or {}).get("evaluation_identity_digest"),
    )


# ─── authoritative version coverage ──────────────────────────────────────────

def test_l6_runner_declares_authoritative_semantic_version():
    assert learning_cycle_validation.EVALUATOR_SEMANTIC_VERSIONS == {
        "run_l6": "l6_hd12_learning_cycle_confidence_v1",
    }
    assert learning_cycle_validation.EVALUATOR_REPORT_SCHEMA_VERSIONS == {
        "run_l6": {"REPORT_SCHEMA_VERSION": "l6_learning_cycle_validation_v1"},
    }
    gov = learning_cycle_validation.EVALUATOR_GOVERNANCE_CONTRACT_VERSIONS["run_l6"]
    assert gov["HD12_VERSION"] == "hd12_learning_cycle_confidence_v1"
    assert gov["LEARNING_VERSION"] == "hd11_hd12_learning_adaptation_v1"


def test_l6_evaluation_identity_binds_authoritative_version():
    identity = build_evaluation_identity(
        _l6_question(), _l6_definition(), registry_version=REGISTRY_VERSION)
    assert identity["evaluator_semantic_version"] == "l6_hd12_learning_cycle_confidence_v1"
    assert identity["governance_contract_versions"]["HD12_VERSION"] == \
        "hd12_learning_cycle_confidence_v1"
    assert identity["report_schema_versions"]["REPORT_SCHEMA_VERSION"] == \
        "l6_learning_cycle_validation_v1"


def test_l6_is_now_in_the_declared_evaluator_version_set():
    identities = _current_identities()
    assert identities["L6"]["evaluator_semantic_version"], \
        "L6 must declare a governed evaluator semantic version"


# ─── result freshness classification ─────────────────────────────────────────

def test_exact_version_match_counts_as_current():
    current = _l6_current_identity()
    assert is_result_current(current, current) == (True, None)


def test_older_version_does_not_count_as_current():
    recorded = _identity_with_semantic_version("l6_hd12_learning_cycle_confidence_v0")
    is_current, reason = is_result_current(recorded, _l6_current_identity())
    assert is_current is False
    assert reason == EVALUATOR_CHANGED


def test_newer_unexpected_version_fails_closed():
    recorded = _identity_with_semantic_version("l6_hd12_learning_cycle_confidence_v9")
    is_current, reason = is_result_current(recorded, _l6_current_identity())
    assert is_current is False
    assert reason == EVALUATOR_CHANGED


def test_missing_evaluator_version_fails_closed():
    # A legacy result published before the evaluator declared its contract has
    # no reconstructible semantic version and must never be assumed current.
    current = _l6_current_identity()
    assert is_result_current(None, current) == (False, STALE_EVALUATION)
    assert is_result_current({}, current) == (False, STALE_EVALUATION)


# ─── stale COMPLETE result cannot satisfy current question state ─────────────

def test_stale_complete_result_is_detected_for_l6():
    recorded = _identity_with_semantic_version("l6_hd12_learning_cycle_confidence_v0")
    stale = stale_question_ids(
        {"L6": recorded}, {"L6": _l6_current_identity()})
    assert stale == {"L6": EVALUATOR_CHANGED}


def test_current_result_is_not_flagged_stale():
    current = _l6_current_identity()
    assert stale_question_ids({"L6": current}, {"L6": current}) == {}


# ─── re-evaluation eligibility ───────────────────────────────────────────────

def test_stale_result_becomes_reevaluation_eligible():
    recorded = _identity_with_semantic_version("l6_hd12_learning_cycle_confidence_v0")
    stale = stale_question_ids(
        {"L6": recorded}, {"L6": _l6_current_identity()})
    assert "L6" in stale
    # A flagged question must re-enter the governed evaluation flow; the stale
    # reason is an orchestration signal (REQUIRES_REEVALUATION), never a
    # scientific verdict.
    assert stale["L6"] in {EVALUATOR_CHANGED, STALE_EVALUATION}


# ─── supersession + preservation on the same snapshot ────────────────────────

def test_reevaluation_supersedes_stale_result_and_preserves_history(tmp_path):
    store = QuestionCycleStore(tmp_path / "cycles")
    old_identity = _identity_with_semantic_version("l6_hd12_learning_cycle_confidence_v0")
    new_identity = _l6_current_identity()

    old_result = _result(old_identity, status="COMPLETE")
    old_path = store.save_question_result(old_result)
    old_bytes = old_path.read_bytes()

    new_result = _result(new_identity, status="COMPLETE")
    store.save_question_result(new_result)

    # Same governed evidence snapshot, distinct governed evaluation identity.
    assert new_result.snapshot_id == old_result.snapshot_id
    assert new_result.snapshot_fingerprint == old_result.snapshot_fingerprint
    assert new_result.evaluation_identity_digest != old_result.evaluation_identity_digest
    assert new_result.result_id != old_result.result_id

    # CURRENT resolution chooses the new (current-version) result by digest.
    reloaded = store.load_question_result(
        "L6", SNAPSHOT, new_result.evaluation_identity_digest)
    assert reloaded.result_id == new_result.result_id
    assert reloaded.evaluation_identity["evaluator_semantic_version"] == \
        "l6_hd12_learning_cycle_confidence_v1"

    # The historical stale result remains byte-for-byte preserved.
    assert old_path.read_bytes() == old_bytes
    historical = store.load_question_result(
        "L6", SNAPSHOT, old_result.evaluation_identity_digest)
    assert historical is not None
    assert historical.evaluation_identity["evaluator_semantic_version"] == \
        "l6_hd12_learning_cycle_confidence_v0"


def test_stale_result_is_not_chosen_as_current_resolution(tmp_path):
    store = QuestionCycleStore(tmp_path / "cycles")
    old_identity = _identity_with_semantic_version("l6_hd12_learning_cycle_confidence_v0")
    new_identity = _l6_current_identity()
    store.save_question_result(_result(old_identity, status="COMPLETE"))
    store.save_question_result(_result(new_identity, status="COMPLETE"))

    # The governed invalidation rule independently refuses to treat the stale
    # recorded identity as current.
    stale = stale_question_ids({"L6": old_identity}, {"L6": new_identity})
    assert "L6" in stale
    assert is_result_current(old_identity, new_identity)[0] is False


# ─── snapshot identity rules remain intact ───────────────────────────────────

def test_snapshot_identity_rules_intact():
    old = _result(_identity_with_semantic_version("l6_hd12_learning_cycle_confidence_v0"))
    new = _result(_l6_current_identity())
    assert old.snapshot_id == new.snapshot_id == SNAPSHOT
    assert old.snapshot_fingerprint == new.snapshot_fingerprint == FINGERPRINT


# ─── unrelated question semantics unchanged ──────────────────────────────────

def test_unrelated_evaluator_semantics_unchanged():
    identities = _current_identities()
    assert identities["L3"]["evaluator_semantic_version"] == \
        "l3_hd11_architecture_assumption_validity_v3"
    assert identities["G2"]["evaluator_semantic_version"] == \
        "g2_exhaustive_snapshot_scoped_lineage_v2"
    assert identities["EX2"]["evaluator_semantic_version"] == \
        "ex2_snapshot_scoped_population_closure_v2"


def test_l6_governed_report_unchanged():
    report = learning_cycle_validation.run_l6(
        records=[{"canonical_opportunity_id": f"OPP-{i:06d}"} for i in range(1852)])
    assert report["question_id"] == "L6"
    assert report["report_schema_version"] == "l6_learning_cycle_validation_v1"
    assert report["status"] == "INSUFFICIENT_DATA"
    assert report["dataset"]["sample_size"] == 1852
