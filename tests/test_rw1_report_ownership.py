"""Repair Wave RW1 acceptance tests: canonical ownership / legacy routing.

RW1 repairs the canonical report-ownership collisions between:

* D1 (component-reward analysis)      and L3 (architecture assumptions)
    shared artifact: q1_component_reward.json
* E2 (pooled pattern expectancy)      and L1 (pattern degradation)
    shared artifact: q5_pattern_degradation.json

The tests below prove, in order, the 25 frozen RW1 requirements: single
canonical ownership, fail-closed routing for the non-owner, preserved legacy
compatibility where it cannot transfer ownership, metadata that cannot silently
contradict ownership, unchanged E3/S1 and R1/R2 behaviour, the derived ledger
delta of +2 (26/70), and that no data-collection, trading, or multi-account
behaviour is touched.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from research_engine.control_plane import ReportValidity, build_question_state
from research_engine.control_plane.report_history import resolve_report_history
from research_engine.control_plane.report_ownership import (
    ADJUDICATED_REPORT_OWNERS,
    FAIL_CLOSED_STATE,
    NOT_ADJUDICATED,
    OWNED,
    OWNERSHIP_DENIED,
    OWNERSHIP_METADATA_CONFLICT,
    canonical_report_owner,
    declared_co_claimants,
    is_adjudicated_report,
    resolve_report_ownership,
)
from research_engine.control_plane.report_resolver import (
    load_report_for_question,
    report_contains_authoritative_result,
    resolve_report_validity,
)
from research_engine.registry.master_repair_ledger import (
    MASTER_REPAIR_LEDGER,
    OPERATIONAL_IDS,
    REPAIR_WAVES,
    STRUCTURALLY_NON_OPERATIONAL_IDS,
    STRUCTURALLY_OPERATIONAL_IDS,
    operational_baseline,
    projected_operational_counts,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.registry.wave_a1_definitions import WAVE_A1_RESOLVED
from research_engine.registry.wave_a2_definitions import WAVE_A2_RESOLVED


D1_ARTIFACT = "q1_component_reward.json"
E2_ARTIFACT = "q5_pattern_degradation.json"

# The frozen pre-RW1 operational baseline: the 24 questions that were already
# structurally operational because the ownership collision did not apply to them.
FROZEN_PRE_RW1_OPERATIONAL = frozenset(
    (WAVE_A1_RESOLVED | WAVE_A2_RESOLVED) - {"D1", "E2"}
)

_RW1_CHANGED_MODULES = (
    "research_engine/control_plane/report_ownership.py",
    "research_engine/control_plane/report_resolver.py",
    "research_engine/control_plane/report_history.py",
    "research_engine/registry/master_repair_ledger.py",
)
_FORBIDDEN_IMPORT_ROOTS = frozenset({
    "core", "data_pipeline", "research_data", "execution", "athena",
    "deployment", "risk", "strategy", "lambda", "tools", "main", "research",
})


def _current_report(question_id: str) -> dict:
    """A VALID_CURRENT, COMPLETE canonical artifact body."""
    return {
        "question_id": question_id,
        "status": "COMPLETE",
        "overall": {"finding": "Current finding", "confidence": "HIGH"},
        "confidence": "HIGH",
        "dataset": {"source": "shadow_trades", "sample_size": 125},
        "fingerprint": {
            "epoch": "CURRENT",
            "dataset_id": "shadow_trades_2026-09-13",
            "architecture_version": "new_pipeline_v1.2",
            "records_used": 125,
            "source": "shadow_trades",
        },
        "recommendation": "MONITOR",
        "warnings": [],
        "generated": "2026-09-13T00:00:00Z",
        "provenance": {
            "experiment_module": "research_engine.experiments.component_reward",
            "registry_id": question_id,
            "pipeline": "Question -> Experiment -> Dataset -> Output",
        },
    }


def _write(root: Path, filename: str, report: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / filename
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


@pytest.fixture
def rw1_reports(tmp_path: Path) -> Path:
    """Only the two adjudicated artifacts exist, in their historical form."""
    _write(tmp_path, D1_ARTIFACT, _current_report("Q1"))
    _write(tmp_path, E2_ARTIFACT, _current_report("Q5"))
    return tmp_path


def _empty_dir(tmp_path: Path, name: str = "empty") -> Path:
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return path
# ---------------------------------------------------------------------------
# REQ 1-5: D1 owns its component-reward artifact; L3 can never inherit it
# ---------------------------------------------------------------------------

def test_req1_q1_component_reward_belongs_to_d1():
    assert {
        D1_ARTIFACT: "D1",
        E2_ARTIFACT: "E2",
        "e3_strategy_family_expectancy.json": "E3",
    }.items() <= ADJUDICATED_REPORT_OWNERS.items()
    assert canonical_report_owner(D1_ARTIFACT) == "D1"
    assert canonical_report_owner("analysis/reports/q1_component_reward.json") == "D1"
    assert canonical_report_owner("analysis\\reports\\q1_component_reward.json") == "D1"
    assert declared_co_claimants(D1_ARTIFACT) == ("L3",)

    decision = resolve_report_ownership(D1_ARTIFACT, "D1")
    assert decision.allowed is True
    assert decision.kind == OWNED
    assert decision.canonical_owner == "D1"
    assert decision.fail_closed_state == ""
    assert dict(decision.to_dict())["canonical_owner"] == "D1"


def test_req2_q1_component_reward_cannot_satisfy_l3():
    decision = resolve_report_ownership(D1_ARTIFACT, "L3")
    assert decision.allowed is False
    assert decision.kind == OWNERSHIP_DENIED
    assert decision.canonical_owner == "D1"
    assert decision.fail_closed_state == FAIL_CLOSED_STATE
    assert FAIL_CLOSED_STATE in decision.reason

    # No other canonical question may inherit the artifact either.
    for other in sorted(set(REGISTRY_BY_ID) - {"D1"}):
        assert not resolve_report_ownership(D1_ARTIFACT, other).allowed, other

    validity, reason = resolve_report_validity(
        D1_ARTIFACT,
        _current_report("Q1"),
        expected_question_id="L3",
        accepted_question_ids=("Q1",),
    )
    assert validity == ReportValidity.MISSING
    assert FAIL_CLOSED_STATE in reason
    assert report_contains_authoritative_result(_current_report("Q1"), validity) is False


def test_req3_d1_canonical_resolution_succeeds_for_its_current_report(rw1_reports):
    state = build_question_state("D1", reports_dir=rw1_reports, evidence_source={})

    assert state.report_validity == ReportValidity.VALID_CURRENT
    assert state.latest_report_status == "COMPLETE"
    assert state.state_status == "COMPLETE"
    assert state.latest_finding == "Current finding"
    assert state.authoritative_report is not None
    assert state.authoritative_report.is_authoritative is True
    assert state.authoritative_report.canonical_question_id == "D1"
    assert state.authoritative_report.source_question_id == "Q1"
    assert Path(state.latest_report_path).name == D1_ARTIFACT
    assert state.report_history_count == 1

    # D1's own canonical identity is also accepted on its own artifact.
    for identity in ("D1", "Q1"):
        validity, _ = resolve_report_validity(
            D1_ARTIFACT,
            _current_report(identity),
            expected_question_id="D1",
            accepted_question_ids=REGISTRY_BY_ID["D1"].legacy_ids,
        )
        assert validity == ReportValidity.VALID_CURRENT, identity


def test_req4_l3_is_missing_and_fail_closed_when_only_d1_report_exists(rw1_reports):
    state = build_question_state("L3", reports_dir=rw1_reports, evidence_source={})

    assert state.report_validity == ReportValidity.MISSING
    assert state.latest_report_status == "NOT_RUN"
    assert state.latest_report_path == ""
    assert state.latest_result is None
    assert state.latest_finding == ""
    assert state.authoritative_report is None
    assert state.report_history_count == 0
    assert state.state_status != "COMPLETE"
    assert any(FAIL_CLOSED_STATE in warning for warning in state.warnings)

    # The artifact is not even loaded for L3.
    report, path = load_report_for_question("L3", D1_ARTIFACT, reports_dir=rw1_reports)
    assert report is None
    assert path == ""


def test_req5_d1_completion_cannot_propagate_to_l3(rw1_reports):
    d1 = build_question_state("D1", reports_dir=rw1_reports, evidence_source={})
    l3 = build_question_state("L3", reports_dir=rw1_reports, evidence_source={})

    assert d1.state_status == "COMPLETE"
    assert l3.state_status != "COMPLETE"
    assert l3.latest_finding == ""
    assert l3.latest_result is None
    assert l3.authoritative_report is None

    l3_history = resolve_report_history("L3", D1_ARTIFACT, rw1_reports, {})
    assert l3_history.report_history_count == 0
    assert l3_history.authoritative_report is None
    assert l3_history.to_dict()["latest_finding"] == ""

    d1_history = resolve_report_history("D1", D1_ARTIFACT, rw1_reports, {})
    assert d1_history.report_history_count == 1
    assert d1_history.authoritative_report is not None
    assert d1_history.authoritative_report.source_question_id == "Q1"


# ---------------------------------------------------------------------------
# REQ 6-10: E2 owns its pooled-pattern artifact; L1 can never inherit it
# ---------------------------------------------------------------------------

def test_req6_q5_pattern_degradation_belongs_to_e2():
    assert canonical_report_owner(E2_ARTIFACT) == "E2"
    assert canonical_report_owner("analysis/reports/q5_pattern_degradation.json") == "E2"
    assert declared_co_claimants(E2_ARTIFACT) == ("L1",)
    assert is_adjudicated_report(E2_ARTIFACT) is True

    decision = resolve_report_ownership(E2_ARTIFACT, "E2")
    assert decision.allowed is True
    assert decision.kind == OWNED
    assert decision.canonical_owner == "E2"


def test_req7_q5_pattern_degradation_cannot_satisfy_l1():
    decision = resolve_report_ownership(E2_ARTIFACT, "L1")
    assert decision.allowed is False
    assert decision.kind == OWNERSHIP_DENIED
    assert decision.canonical_owner == "E2"
    assert decision.fail_closed_state == FAIL_CLOSED_STATE

    for other in sorted(set(REGISTRY_BY_ID) - {"E2"}):
        assert not resolve_report_ownership(E2_ARTIFACT, other).allowed, other

    validity, reason = resolve_report_validity(
        E2_ARTIFACT,
        _current_report("Q5"),
        expected_question_id="L1",
        accepted_question_ids=("Q5",),
    )
    assert validity == ReportValidity.MISSING
    assert FAIL_CLOSED_STATE in reason


def test_req8_e2_canonical_resolution_succeeds_for_its_current_report(rw1_reports):
    state = build_question_state("E2", reports_dir=rw1_reports, evidence_source={})

    assert state.report_validity == ReportValidity.VALID_CURRENT
    assert state.latest_report_status == "COMPLETE"
    assert state.state_status == "COMPLETE"
    assert state.latest_finding == "Current finding"
    assert state.authoritative_report is not None
    assert state.authoritative_report.canonical_question_id == "E2"
    assert Path(state.latest_report_path).name == E2_ARTIFACT

    # E2 keeps its own canonical identity and its declared compatibility IDs.
    for identity in ("E2", "Q5", "Q24"):
        validity, _ = resolve_report_validity(
            E2_ARTIFACT,
            _current_report(identity),
            expected_question_id="E2",
            accepted_question_ids=REGISTRY_BY_ID["E2"].legacy_ids,
        )
        assert validity == ReportValidity.VALID_CURRENT, identity


def test_req9_l1_is_missing_and_fail_closed_when_only_e2_report_exists(rw1_reports):
    state = build_question_state("L1", reports_dir=rw1_reports, evidence_source={})

    assert state.report_validity == ReportValidity.MISSING
    assert state.latest_report_status == "NOT_RUN"
    assert state.latest_report_path == ""
    assert state.latest_result is None
    assert state.latest_finding == ""
    assert state.authoritative_report is None
    assert state.report_history_count == 0
    assert state.state_status != "COMPLETE"
    assert any(FAIL_CLOSED_STATE in warning for warning in state.warnings)

    report, path = load_report_for_question("L1", E2_ARTIFACT, reports_dir=rw1_reports)
    assert report is None
    assert path == ""


def test_req10_e2_completion_cannot_propagate_to_l1(rw1_reports):
    e2 = build_question_state("E2", reports_dir=rw1_reports, evidence_source={})
    l1 = build_question_state("L1", reports_dir=rw1_reports, evidence_source={})

    assert e2.state_status == "COMPLETE"
    assert l1.state_status != "COMPLETE"
    assert l1.latest_finding == ""
    assert l1.authoritative_report is None

    l1_history = resolve_report_history("L1", E2_ARTIFACT, rw1_reports, {})
    assert l1_history.report_history_count == 0
    assert l1_history.authoritative_report is None

    e2_history = resolve_report_history("E2", E2_ARTIFACT, rw1_reports, {})
    assert e2_history.report_history_count == 1
    assert e2_history.authoritative_report is not None
    assert e2_history.authoritative_report.source_question_id == "Q5"


# ---------------------------------------------------------------------------
# REQ 11: unrelated, unambiguous legacy compatibility is fully preserved
# ---------------------------------------------------------------------------

def test_req11_unrelated_legacy_compatibility_is_preserved(tmp_path):
    reports = _empty_dir(tmp_path)
    _write(reports, "q19_expected_value.json", _current_report("Q19"))
    _write(reports, "q15_learning_velocity.json", _current_report("Q15"))

    e1 = build_question_state("E1", reports_dir=reports, evidence_source={})
    assert e1.report_validity == ReportValidity.VALID_CURRENT
    assert e1.state_status == "COMPLETE"
    assert e1.latest_finding == "Current finding"
    assert e1.current_sample_size == 125

    l2 = build_question_state("L2", reports_dir=reports, evidence_source={})
    assert l2.report_validity == ReportValidity.VALID_CURRENT

    # Non-adjudicated artifacts stay explicitly non-adjudicated (never implied).
    for filename, owner in (
        ("q19_expected_value.json", "E1"),
        ("q15_learning_velocity.json", "L2"),
        ("q10_guard_efficacy.json", "R1"),
        ("q24_strategy_edge.json", "E3"),
    ):
        decision = resolve_report_ownership(filename, owner)
        assert decision.kind == NOT_ADJUDICATED
        assert decision.allowed is True
        assert decision.canonical_owner is None

    # A unique declared legacy identity still resolves to its canonical question.
    legacy_lookup = build_question_state(
        "Q20", reports_dir=_empty_dir(tmp_path, "none"), evidence_source={}
    )
    assert legacy_lookup.question_id == "D2"
    assert resolve_report_ownership(
        REGISTRY_BY_ID["D2"].report_filename, "D2"
    ).allowed is True


# ---------------------------------------------------------------------------
# REQ 12: ambiguous shared legacy mappings fail closed
# ---------------------------------------------------------------------------

def test_req12_ambiguous_shared_legacy_mappings_fail_closed(rw1_reports):
    # The collisions that RW1 adjudicates are still shared legacy identities.
    assert set(REGISTRY_BY_ID["D1"].legacy_ids) & set(REGISTRY_BY_ID["L3"].legacy_ids) == {"Q1"}
    assert set(REGISTRY_BY_ID["E2"].legacy_ids) & set(REGISTRY_BY_ID["L1"].legacy_ids) == {"Q5"}
    assert "Q24" in REGISTRY_BY_ID["E2"].legacy_ids

    # A shared legacy ID never transfers the artifact: only the owner may resolve it.
    assert resolve_report_ownership(D1_ARTIFACT, "D1").allowed is True
    assert resolve_report_ownership(D1_ARTIFACT, "L3").allowed is False
    assert resolve_report_ownership(E2_ARTIFACT, "E2").allowed is True
    assert resolve_report_ownership(E2_ARTIFACT, "L1").allowed is False
    assert resolve_report_ownership(E2_ARTIFACT, "E3").allowed is False
    assert resolve_report_ownership(E2_ARTIFACT, "S1").allowed is False

    # Ambiguous direct legacy lookup stays fail-closed for every shared identity.
    for alias in ("Q1", "Q5", "Q10"):
        with pytest.raises(KeyError):
            build_question_state(alias, reports_dir=rw1_reports, evidence_source={})
    # 2B.1 removes E3/S1 from Q24 routing. The surviving E2 compatibility
    # declaration cannot resolve or complete either strategy question.
    assert build_question_state(
        "Q24", reports_dir=rw1_reports, evidence_source={}
    ).question_id == "E2"

    # Filename similarity is not ownership authority.
    assert is_adjudicated_report("q1_component_reward_v2.json") is False
    assert resolve_report_ownership("q1_component_reward_v2.json", "L3").allowed is True
    assert canonical_report_owner("q1_component_reward_v2.json") is None


# ---------------------------------------------------------------------------
# REQ 13: report metadata cannot contradict canonical ownership silently
# ---------------------------------------------------------------------------

def test_req13_report_metadata_cannot_contradict_canonical_ownership(rw1_reports):
    for declared in ("D6", "L3", "L1", "E1", "E3", "Q2"):
        decision = resolve_report_ownership(
            D1_ARTIFACT, "D1", report_metadata={"question_id": declared}
        )
        assert decision.allowed is False, declared
        assert decision.kind == OWNERSHIP_METADATA_CONFLICT
        assert decision.fail_closed_state == FAIL_CLOSED_STATE

        validity, reason = resolve_report_validity(
            D1_ARTIFACT,
            _current_report(declared),
            expected_question_id="D1",
            accepted_question_ids=REGISTRY_BY_ID["D1"].legacy_ids,
        )
        assert validity == ReportValidity.INVALIDATED, declared
        assert FAIL_CLOSED_STATE in reason

    for declared in ("E3", "S1", "D1", "Q1"):
        validity, reason = resolve_report_validity(
            E2_ARTIFACT,
            _current_report(declared),
            expected_question_id="E2",
            accepted_question_ids=REGISTRY_BY_ID["E2"].legacy_ids,
        )
        assert validity == ReportValidity.INVALIDATED, declared
        assert FAIL_CLOSED_STATE in reason

    # The owner's own canonical identity and its declared compatibility IDs stay valid.
    for declared in ("D1", "Q1"):
        assert resolve_report_ownership(
            D1_ARTIFACT, "D1", report_metadata={"question_id": declared}
        ).allowed is True
    for declared in ("E2", "Q5", "Q24"):
        assert resolve_report_ownership(
            E2_ARTIFACT, "E2", report_metadata={"question_id": declared}
        ).allowed is True

    # A contradicting artifact can never surface as a finding or history truth.
    contradicted = _empty_dir(rw1_reports, "contradicted")
    _write(contradicted, D1_ARTIFACT, _current_report("D6"))
    d1 = build_question_state("D1", reports_dir=contradicted, evidence_source={})
    assert d1.report_validity == ReportValidity.INVALIDATED
    assert d1.latest_result is None
    assert d1.latest_finding == ""
    assert d1.authoritative_report is None
    history = resolve_report_history("D1", D1_ARTIFACT, contradicted, {})
    assert history.authoritative_report is None
    assert history.latest_finding == ""


# ---------------------------------------------------------------------------
# REQ 14: report existence alone cannot create completion
# ---------------------------------------------------------------------------

def test_req14_report_existence_alone_cannot_create_completion(tmp_path, rw1_reports):
    # The owner's artifact exists and is COMPLETE -> it does complete the owner...
    owner = build_question_state("D1", reports_dir=rw1_reports, evidence_source={})
    assert owner.state_status == "COMPLETE"

    # ...but existence never completes a non-owner.
    for qid, artifact in (("L3", D1_ARTIFACT), ("L1", E2_ARTIFACT)):
        state = build_question_state(qid, reports_dir=rw1_reports, evidence_source={})
        assert state.state_status != "COMPLETE"
        assert state.authoritative_report is None
        assert report_contains_authoritative_result(
            json.loads((rw1_reports / artifact).read_text(encoding="utf-8")),
            ReportValidity.MISSING,
        ) is False

    # An artifact with no identity at all cannot be attributed to anyone.
    anonymous = _empty_dir(tmp_path, "anonymous")
    _write(anonymous, D1_ARTIFACT, _current_report(""))
    d1 = build_question_state("D1", reports_dir=anonymous, evidence_source={})
    assert d1.report_validity == ReportValidity.INVALIDATED
    assert d1.latest_finding == ""

    # A pre-CURRENT artifact cannot become current truth for its owner.
    legacy = _empty_dir(tmp_path, "legacy")
    legacy_report = _current_report("Q5")
    legacy_report.pop("epoch", None)
    legacy_report["fingerprint"].pop("epoch", None)
    _write(legacy, E2_ARTIFACT, legacy_report)
    e2 = build_question_state("E2", reports_dir=legacy, evidence_source={})
    assert e2.report_validity == ReportValidity.LEGACY
    assert e2.latest_result is None
    assert e2.state_status != "COMPLETE"

    # Valid-but-not-COMPLETE evidence does not complete the owner either.
    incomplete = _empty_dir(tmp_path, "incomplete")
    report = _current_report("Q1")
    report["status"] = "INSUFFICIENT_DATA"
    _write(incomplete, D1_ARTIFACT, report)
    d1_incomplete = build_question_state("D1", reports_dir=incomplete, evidence_source={})
    assert d1_incomplete.report_validity == ReportValidity.VALID_CURRENT
    assert d1_incomplete.latest_result is None
    assert d1_incomplete.state_status != "COMPLETE"


# ---------------------------------------------------------------------------
# REQ 15: E3/S1 ownership is explicitly adjudicated by Repair 2B.1
# ---------------------------------------------------------------------------

def test_req15_e3_s1_behaviour_is_adjudicated(tmp_path):
    e3 = REGISTRY_BY_ID["E3"]
    s1 = REGISTRY_BY_ID["S1"]
    assert e3.report_filename == "e3_strategy_family_expectancy.json"
    assert s1.report_filename == ""
    assert e3.legacy_ids == s1.legacy_ids == ()
    assert s1.scientific_owner_id == "E3"
    assert is_adjudicated_report(e3.report_filename) is True

    reports = _empty_dir(tmp_path)
    _write(reports, "q24_strategy_edge.json", _current_report("Q24"))

    for qid in ("E3", "S1"):
        state = build_question_state(qid, reports_dir=reports, evidence_source={})
        assert state.report_validity != ReportValidity.VALID_CURRENT, qid
        assert state.latest_finding == "", qid
        assert MASTER_REPAIR_LEDGER[qid].structurally_operational, qid
    assert resolve_report_ownership("q24_strategy_edge.json", "E3").allowed is True
    assert resolve_report_ownership("q24_strategy_edge.json", "S1").allowed is True

    # The two adjudicated RW1 artifacts are never attributed to E3/S1 workloads.
    for qid in ("E3", "S1"):
        for artifact in (D1_ARTIFACT, E2_ARTIFACT):
            assert load_report_for_question(qid, artifact, reports_dir=reports)[0] is None
            assert resolve_report_ownership(artifact, qid).allowed is False


# ---------------------------------------------------------------------------
# REQ 16: R1/R2 behaviour is unchanged (their ownership was NOT adjudicated)
# ---------------------------------------------------------------------------

def test_req16_r1_r2_behaviour_is_unchanged(tmp_path):
    r1 = REGISTRY_BY_ID["R1"]
    r2 = REGISTRY_BY_ID["R2"]
    assert r1.report_filename == r2.report_filename == "q10_guard_efficacy.json"
    assert r1.legacy_ids == r2.legacy_ids == ("Q10",)
    assert is_adjudicated_report("q10_guard_efficacy.json") is False

    reports = _empty_dir(tmp_path)
    _write(reports, "q10_guard_efficacy.json", _current_report("Q10"))

    # The shared (unadjudicated) artifact is still visible to both questions.
    for qid in ("R1", "R2"):
        state = build_question_state(qid, reports_dir=reports, evidence_source={})
        assert state.report_validity == ReportValidity.VALID_CURRENT, qid
        assert state.latest_finding == "Current finding", qid
        assert state.authoritative_report is not None, qid
        assert resolve_report_ownership("q10_guard_efficacy.json", qid).kind == NOT_ADJUDICATED

        entry = MASTER_REPAIR_LEDGER[qid]
        assert not entry.structurally_operational, qid
        assert entry.gates.report_ownership == "FAIL", qid
        assert entry.primary_blocker_category == "report ownership", qid
        assert entry.proposed_repair_wave == "RW9", qid

    # Ambiguous shared legacy lookup remains fail-closed, exactly as before RW1.
    with pytest.raises(KeyError):
        build_question_state("Q10", reports_dir=reports, evidence_source={})


# ---------------------------------------------------------------------------
# REQ 17-23: derived ledger delta (+2) with no other question moving
# ---------------------------------------------------------------------------

def test_req17_l1_remains_structurally_non_operational():
    entry = MASTER_REPAIR_LEDGER["L1"]
    assert entry.structurally_operational is False
    assert entry.to_dict()["structurally_operational"] is False
    assert entry.gates.report_ownership == "FAIL"
    assert entry.primary_blocker_category == "chronology"
    assert entry.proposed_repair_wave == "RW10"
    assert entry.repair_actions
    assert "L1" in STRUCTURALLY_NON_OPERATIONAL_IDS
    assert "L1" not in STRUCTURALLY_OPERATIONAL_IDS


def test_req18_l3_remains_structurally_non_operational():
    entry = MASTER_REPAIR_LEDGER["L3"]
    assert entry.structurally_operational is False
    assert entry.to_dict()["structurally_operational"] is False
    assert entry.gates.report_ownership == "FAIL"
    assert entry.primary_blocker_category == "runner mismatch"
    assert entry.proposed_repair_wave == "RW10"
    assert entry.repair_actions
    assert "L3" in STRUCTURALLY_NON_OPERATIONAL_IDS
    assert "L3" not in STRUCTURALLY_OPERATIONAL_IDS


def test_req19_d1_becomes_structurally_operational():
    entry = MASTER_REPAIR_LEDGER["D1"]
    assert entry.structurally_operational is True
    assert entry.to_dict()["structurally_operational"] is True
    assert entry.gates.report_ownership == "PASS"
    assert all(gate in {"PASS", "NOT_APPLICABLE"} for gate in entry.gates.values())
    assert "D1" in STRUCTURALLY_OPERATIONAL_IDS
    assert "D1" in OPERATIONAL_IDS


def test_req20_e2_becomes_structurally_operational():
    entry = MASTER_REPAIR_LEDGER["E2"]
    assert entry.structurally_operational is True
    assert entry.to_dict()["structurally_operational"] is True
    assert entry.gates.report_ownership == "PASS"
    assert all(gate in {"PASS", "NOT_APPLICABLE"} for gate in entry.gates.values())
    assert "E2" in STRUCTURALLY_OPERATIONAL_IDS
    assert "E2" in OPERATIONAL_IDS


def test_req21_and_req22_current_counts_remain_derived_from_all_70_entries():
    operational, non_operational = operational_baseline()
    assert len(MASTER_REPAIR_LEDGER) == 70
    assert operational == len(STRUCTURALLY_OPERATIONAL_IDS)
    assert non_operational == len(STRUCTURALLY_NON_OPERATIONAL_IDS)
    assert len(STRUCTURALLY_OPERATIONAL_IDS) + len(STRUCTURALLY_NON_OPERATIONAL_IDS) == 70

    derived = {
        qid for qid, entry in MASTER_REPAIR_LEDGER.items() if entry.structurally_operational
    }
    assert derived == set(STRUCTURALLY_OPERATIONAL_IDS) == set(OPERATIONAL_IDS)

    # Later repair waves may increase the baseline; projection stays monotonic
    # and reconciles exactly to the canonical 70.
    projection = projected_operational_counts()
    assert projection[0] == ("RW1", operational)
    assert [count for _, count in projection] == sorted(count for _, count in projection)
    assert projection[-1] == ("RW12", 70)


def test_req23_no_other_question_changes_structurally_operational_state():
    assert len(FROZEN_PRE_RW1_OPERATIONAL) == 24
    assert FROZEN_PRE_RW1_OPERATIONAL <= STRUCTURALLY_OPERATIONAL_IDS
    assert {"D1", "E2"} <= set(STRUCTURALLY_OPERATIONAL_IDS) - FROZEN_PRE_RW1_OPERATIONAL
    assert STRUCTURALLY_NON_OPERATIONAL_IDS == frozenset(MASTER_REPAIR_LEDGER) - STRUCTURALLY_OPERATIONAL_IDS

    wave = REPAIR_WAVES["RW1"]
    assert wave.direct_gain == ("D1", "E2")
    assert wave.implemented is True
    assert wave.implementation_evidence
    assert set(wave.unlocked_not_yet_operational) == {"E3", "S1", "R1", "R2", "L1", "L3"}
    for qid in {"R1", "R2", "L1", "L3"}:
        assert not MASTER_REPAIR_LEDGER[qid].structurally_operational, qid
    for qid in {"E3", "S1"}:
        assert MASTER_REPAIR_LEDGER[qid].structurally_operational, qid


# ---------------------------------------------------------------------------
# REQ 24-25: data-collection, trading, and multi-account behaviour untouched
# ---------------------------------------------------------------------------

def _imported_roots(module_path: str) -> set[str]:
    tree = ast.parse(Path(module_path).read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_req24_rw1_modules_do_not_reach_into_data_collection_or_trading_code():
    for module_path in _RW1_CHANGED_MODULES:
        assert Path(module_path).is_file(), module_path
        imported = _imported_roots(module_path)
        forbidden = imported & _FORBIDDEN_IMPORT_ROOTS
        assert not forbidden, f"{module_path} imports {sorted(forbidden)}"

    # The ownership contract is artifact-scoped only.  Canonical report names may
    # describe execution research, but the module must not reference runtime
    # execution operations, collection, accounts, or MT5 behaviours.
    ownership_source = Path(
        "research_engine/control_plane/report_ownership.py"
    ).read_text(encoding="utf-8").lower()
    for forbidden_term in (
        "mt5", "order_send", "account", "fanout",
        "data_collection", "collector", "position", "trade",
    ):
        assert forbidden_term not in ownership_source, forbidden_term


def test_req24_canonical_ownership_resolution_is_read_only(rw1_reports):
    def snapshot(root: Path) -> dict:
        return {
            path.name: (path.stat().st_mtime_ns, path.read_bytes())
            for path in sorted(root.glob("*.json"))
        }

    before = snapshot(rw1_reports)
    for qid in ("D1", "L3", "E2", "L1", "E3", "S1", "R1", "R2", "E1"):
        build_question_state(qid, reports_dir=rw1_reports, evidence_source={})
    resolve_report_history("L3", D1_ARTIFACT, rw1_reports, {})
    resolve_report_history("L1", E2_ARTIFACT, rw1_reports, {})
    after = snapshot(rw1_reports)
    assert before == after


def test_req25_multi_account_and_scientific_contracts_are_unchanged():
    # D1/E2 keep their complete Wave-A multi-account/repeated-measure contracts.
    for qid in ("D1", "E2"):
        gates = MASTER_REPAIR_LEDGER[qid].gates
        assert gates.multi_account_contract == "PASS", qid
        assert gates.repeated_measure_contract == "PASS", qid
        assert gates.unit_of_analysis == "PASS", qid
        assert gates.join_contract == "PASS", qid
        assert gates.epoch_contract == "PASS", qid
        assert gates.metric_contract == "PASS", qid

    # No registry declaration for the RW1 questions was edited to achieve the repair.
    expected = {
        "D1": ("research_engine.experiments.component_reward", "run", D1_ARTIFACT, ("Q1",)),
        "L3": ("research_engine.experiments.component_reward", "run", D1_ARTIFACT, ("Q1",)),
        "E2": ("research_engine.experiments.legacy_canonical", "run_q05", E2_ARTIFACT, ("Q5", "Q24")),
        "L1": ("research_engine.experiments.legacy_canonical", "run_q05", E2_ARTIFACT, ("Q5",)),
        "E3": ("research_engine.experiments.strategy_expectancy", "run_e3", "e3_strategy_family_expectancy.json", ()),
        "S1": ("", "", "", ()),
        "R1": ("research_engine.experiments.legacy_canonical", "run_q10", "q10_guard_efficacy.json", ("Q10",)),
        "R2": ("research_engine.experiments.legacy_canonical", "run_q10", "q10_guard_efficacy.json", ("Q10",)),
    }
    for qid, (module, function, filename, legacy_ids) in expected.items():
        question = REGISTRY_BY_ID[qid]
        assert question.runner_module == module, qid
        assert question.runner_function == function, qid
        assert question.report_filename == filename, qid
        assert tuple(question.legacy_ids) == legacy_ids, qid

    # D1/E2 scientific definitions are untouched: same population, metrics, and thresholds.
    d1 = REGISTRY_BY_ID["D1"]
    e2 = REGISTRY_BY_ID["E2"]
    assert d1.required_fields == ("score", "components", "r_multiple")
    assert [rule.field for rule in d1.validation_rules] == ["lineage_coverage", "outcome_coverage"]
    assert "Which of the 10 scoring components best predict actual R-multiple outcomes?" == d1.description
    assert e2.required_fields == ("pattern", "r_multiple")
    assert [rule.field for rule in e2.validation_rules] == ["pattern_coverage", "outcome_coverage"]
    assert "Which candlestick patterns contain positive expectancy across all conditions?" == e2.description

    # Multi-account fanout never enters the ownership path: no account-grained source.
    from research_engine.registry.research_question_models import DataSource

    for qid in ("D1", "E2", "L1", "L3"):
        for source in REGISTRY_BY_ID[qid].data_sources:
            assert source in (DataSource.SHADOW_TRADES, DataSource.DECISION_TRACE), qid
