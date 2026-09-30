from __future__ import annotations

import copy
import json

import pytest

from research_engine.control_plane import gap_governance as G
from research_engine.control_plane import stage4_identity as I
from research_engine.control_plane import stage4_observation_closeout as C
from research_engine.control_plane import stage4_observation_state as S
from research_engine.control_plane.stage4_ex2_l7_blocker_adjudication import (
    ex2_observation_requirement, l7_observation_requirement,
)


def _member(name: str) -> I.EvidenceMemberReference:
    return I.EvidenceMemberReference(
        reference_type="persisted_record", reference_id=name,
        dataset="shadow_runtime", locator="partition=governed",
        content_fingerprint=(name * 64)[:64],
    )


def test_all_15_canonical_requirements_load_and_are_unique():
    authority = I.RequirementAuthority.load()
    assert authority.ids == tuple(f"OR-{number:02d}" for number in range(1, 16))
    assert len(authority.ids) == len(set(authority.ids)) == 15
    for rid in authority.ids:
        assert authority.require(rid)["observation_requirement_id"] == rid


@pytest.mark.parametrize("value, reason", [
    ("", "MISSING"), ("OR-99", "UNKNOWN"),
    ("OG-EX2-4db003c3ab76", "LEGACY_TRANSITION"),
])
def test_noncanonical_requirement_ids_fail_closed(value, reason):
    with pytest.raises(I.Stage4IdentityError, match=reason):
        I.validate_requirement_id(value)


def test_duplicate_requirement_id_with_conflicting_semantics_fails_closed():
    payload = json.loads(I.REQUIREMENT_MATRIX_PATH.read_text(encoding="utf-8"))
    requirements = copy.deepcopy(payload["observation_requirements"])
    conflict = copy.deepcopy(requirements[13])
    conflict["semantic_definition"] = "conflicting EX2 meaning"
    requirements.append(conflict)
    with pytest.raises(I.Stage4IdentityError,
                       match="CONFLICTING_OBSERVATION_REQUIREMENT_ID:OR-14"):
        I.RequirementAuthority(requirements)


def test_ex2_l7_and_legacy_transitions_resolve_to_canonical_ids():
    ex2 = ex2_observation_requirement("f" * 64, reentry_id="RE-EX2")
    l7 = l7_observation_requirement("f" * 64, reentry_id="RE-L7")
    assert ex2["observation_requirement_id"] == "OR-14"
    assert l7["observation_requirement_id"] == "OR-15"
    assert I.resolve_legacy_transition_id("OG-EX2-4db003c3ab76") == "OR-14"
    assert I.resolve_legacy_transition_id("OG-L7-4db003c3ab76") == "OR-15"


def test_requirement_evidence_rejects_missing_unknown_and_legacy_ids():
    for rid in ("", "OR-99", "OG-EX2-4db003c3ab76"):
        with pytest.raises(S.ObservationStateError):
            S.RequirementEvidence(observation_requirement_id=rid)


def test_evidence_set_id_is_stable_and_reload_preserves_relationships(tmp_path):
    evidence = I.EvidenceSet.deterministic(
        observation_requirement_ids=("OR-14", "OR-15"),
        members=(_member("record-a"), _member("record-b")),
    )
    again = I.EvidenceSet.deterministic(
        observation_requirement_ids=("OR-15", "OR-14"),
        members=(_member("record-b"), _member("record-a")),
    )
    assert evidence.evidence_set_id == again.evidence_set_id
    registry = I.EvidenceSetRegistry((evidence, again))
    path = tmp_path / "identity" / "evidence_sets.json"
    registry.save(path)
    loaded = I.EvidenceSetRegistry.load(path)
    assert loaded.require(evidence.evidence_set_id) == evidence
    assert loaded.requirements_for_evidence_set(evidence.evidence_set_id) == (
        "OR-14", "OR-15")
    assert loaded.evidence_sets_for_requirement("OR-14") == (
        evidence.evidence_set_id,)


def test_explicit_evidence_set_id_is_accepted_and_collision_fails_closed():
    first = I.EvidenceSet("ESET-GOVERNED-0001", ("OR-14",), (_member("a"),))
    same = I.EvidenceSet("ESET-GOVERNED-0001", ("OR-14",), (_member("a"),))
    conflict = I.EvidenceSet(
        "ESET-GOVERNED-0001", ("OR-14",), (_member("different"),))
    registry = I.EvidenceSetRegistry((first,))
    assert registry.add(same) is first
    with pytest.raises(I.Stage4IdentityError,
                       match="CONFLICTING_EVIDENCE_SET_ID"):
        registry.add(conflict)


def test_evidence_set_rejects_missing_id_and_unknown_requirement():
    with pytest.raises(I.Stage4IdentityError, match="MISSING_EVIDENCE_SET_ID"):
        I.EvidenceSet("", ("OR-14",), (_member("a"),))
    with pytest.raises(I.Stage4IdentityError):
        I.EvidenceSet("ESET-GOVERNED-0002", ("OR-99",), (_member("a"),))


def test_many_to_many_requirement_evidence_relationships():
    shared = I.EvidenceSet(
        "ESET-GOVERNED-SHARED", ("OR-14", "OR-15"), (_member("shared"),))
    second = I.EvidenceSet(
        "ESET-GOVERNED-SECOND", ("OR-14",), (_member("second"),))
    registry = I.EvidenceSetRegistry((shared, second))
    assert registry.requirements_for_evidence_set(shared.evidence_set_id) == (
        "OR-14", "OR-15")
    assert registry.evidence_sets_for_requirement("OR-14") == (
        "ESET-GOVERNED-SECOND", "ESET-GOVERNED-SHARED")


def test_evidence_epochs_publish_canonical_evidence_set_identity_and_members():
    matrix = C._load(C.MATRIX_PATH)
    registry = C.build_version_registry(matrix)
    epoch = registry.epoch("STAGE4-EPOCH-SHADOW-RUNTIME-G2")
    persisted = epoch.to_dict()
    assert persisted["evidence_set_id"] == "ESET-STAGE4-EPOCH-SHADOW-RUNTIME-G2"
    assert persisted["evidence_member_references"] == [
        epoch.evidence_set.members[0].to_dict()]
    assert set(epoch.evidence_set.observation_requirement_ids) == set(
        epoch.observation_requirements)


def test_historical_stage4_state_and_accounting_remain_intact():
    gap_store = json.loads(G.CANONICAL_STATE_PATH.read_text(encoding="utf-8"))
    G.validate_store(gap_store)
    assert gap_store["unique_work_items"] == 37
    assert len(gap_store["observation_gap_transitions"]) == 2
    assert all(item["status"] == G.STATUS_RESOLVED for item in
               gap_store["work_items"] if item["gap_type"] == G.GAP_TYPE_IMPL)

    matrix = json.loads(I.REQUIREMENT_MATRIX_PATH.read_text(encoding="utf-8"))
    assert matrix["counts"]["GAP_COUNT_RAW"] == 31
    assert matrix["counts"]["UNIQUE_OBSERVATION_REQUIREMENTS"] == 15


def test_new_gap_transition_without_canonical_requirement_id_fails_closed():
    store = json.loads(G.CANONICAL_STATE_PATH.read_text(encoding="utf-8"))
    requirement = dict(store["observation_gap_transitions"][0])
    requirement.pop("observation_requirement_id", None)
    clean = copy.deepcopy(store)
    clean["observation_gap_transitions"] = []
    with pytest.raises(G.GapGovernanceError,
                       match="MISSING_OBSERVATION_REQUIREMENT_ID"):
        G.record_observation_gap_transition(clean, requirement=requirement)
