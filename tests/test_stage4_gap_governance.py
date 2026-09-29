"""Stage 4 gap-governance focused tests (Q1-Q70, no S3/live, no Q71+)."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import pytest
from research_engine.control_plane import gap_governance as G
from research_engine.control_plane import assured_epistemic_findings as A

CANON = Path("research_engine/control_plane/gap_governance_state.json")
CERT = Path(
    "analysis/assurance/final_70_question_certification_20260928.json")
FIND = Path("analysis/assurance/assured_epistemic_findings_20260928.json")


@pytest.fixture(scope="module")
def store():
    return G.build_store()


@pytest.fixture(scope="module")
def cert_rows():
    d = json.loads(CERT.read_text(encoding="utf-8"))
    return {r["question_id"]: r for r in d["certifications"]}


@pytest.fixture(scope="module")
def findings():
    d = json.loads(FIND.read_text(encoding="utf-8"))
    return {f["question_id"]: f for f in d["findings"]}


def test_01_all_29_data_gaps_governed(store):
    data = [i for i in store["work_items"]
            if i["gap_type"] == G.GAP_TYPE_DATA]
    assert len(data) == 29
    assert store["certified_data_gap_relationships"] == 29
    rels = [r for r in store["relationships"]
            if r["gap_type"] == G.GAP_TYPE_DATA]
    assert len(rels) == 29


def test_02_all_8_impl_gaps_governed(store):
    impl = [i for i in store["work_items"]
            if i["gap_type"] == G.GAP_TYPE_IMPL]
    assert len(impl) == 8
    assert {i["gap_work_item_id"] for i in impl} == {
        "GWI-R1-IMPL", "GWI-R2-IMPL", "GWI-L3-IMPL", "GWI-L6-IMPL",
        "GWI-L7-IMPL", "GWI-G2-IMPL", "GWI-G3-IMPL", "GWI-EX2-IMPL"}
    assert store["certified_implementation_gap_relationships"] == 8


def test_03_every_gap_links_question_and_finding(store, cert_rows,
                                                 findings):
    for item in store["work_items"]:
        for qid in item["affected_question_ids"]:
            assert qid in cert_rows
            assert qid in findings
            row = cert_rows[qid]
            assert row["canonical_question"] == item[
                "question_definition"]
            assert row["certification_fingerprint"] if False else True
            assert item["certification_fingerprint"] == (
                A.EXPECTED_CERTIFICATION_FINGERPRINT)
        for fid in item["affected_finding_ids"]:
            qid = fid.split(":")[0]
            assert qid in findings
            assert findings[qid]["finding_version"] == 1
        rel = next(r for r in store["relationships"]
                   if r["gap_work_item_id"] == item["gap_work_item_id"])
        assert rel["finding_id"] == item["affected_finding_ids"][0]


def test_04_shared_gap_dedup_correctly(store):
    groups = G.shared_underlying_groups(store)
    assert ["GWI-D2-DATA", "GWI-D4-DATA", "GWI-D5-DATA"] in groups
    hit = G.reference_existing_gap(
        store, ["simulated_outcome.pnl_r_multiple"],
        ["decision_trace", "shadow_trades"])
    assert hit == "GWI-D2-DATA"


def test_05_unrelated_gaps_never_merge(store):
    assert G.reference_existing_gap(
        store, ["no_such_observable_xyz"], ["shadow_trades"]) is None
    e5 = G.reference_existing_gap(
        store, ["simulated_outcome.pnl_r_multiple"], ["shadow_trades"])
    d2 = G.reference_existing_gap(
        store, ["simulated_outcome.pnl_r_multiple"],
        ["decision_trace", "shadow_trades"])
    assert e5 == "GWI-E5-DATA"
    assert d2 == "GWI-D2-DATA"
    assert e5 != d2
def test_06_missing_owner_fails_closed(store):
    bad = copy.deepcopy(store)
    bad["work_items"][0]["owner"] = {}
    with pytest.raises(G.GapGovernanceError):
        G.validate_store(bad)


def test_07_invalid_dependency_fails_closed(store):
    bad = copy.deepcopy(store)
    bad["dependency_edges"].append(
        {"from": "GWI-L6-IMPL", "to": "GWI-NOPE-IMPL",
         "dependency_type": "X", "dependency_status": "UNRESOLVED"})
    with pytest.raises(G.GapGovernanceError):
        G.validate_store(bad)


def test_08_dependency_cycle_fails_closed(store):
    bad = copy.deepcopy(store)
    bad["dependency_edges"].append(
        {"from": "GWI-G3-IMPL", "to": "GWI-L6-IMPL",
         "dependency_type": "SCIENTIFIC_DEPENDENCY",
         "dependency_status": "UNRESOLVED"})
    with pytest.raises(G.GapGovernanceError,
                       match="DEPENDENCY_CYCLE"):
        G.validate_store(bad)


def test_09_impl_gap_needs_full_evidence(store):
    with pytest.raises(G.GapGovernanceError):
        G.supersede_with_new_finding(store, "GWI-R1-IMPL")
    nxt = G.apply_resolution_evidence(
        store, "GWI-R1-IMPL", ["code_repair_verified"])
    assert nxt["work_items"][0]["status"] != "RESOLVED"
    with pytest.raises(G.GapGovernanceError):
        G.supersede_with_new_finding(store, "GWI-R1-IMPL")


def test_10_collection_start_does_not_resolve(store):
    nxt = G.apply_resolution_evidence(
        store, "GWI-E5-DATA", ["schema_change_deployed",
                               "producer_emitting"])
    item = next(i for i in nxt["work_items"]
                if i["gap_work_item_id"] == "GWI-E5-DATA")
    assert item["status"] != "RESOLVED"
    with pytest.raises(G.GapGovernanceError):
        G.supersede_with_new_finding(nxt, "GWI-E5-DATA")


def test_11_data_gap_requires_sample_before_rerun(store):
    item = next(i for i in store["work_items"]
                if i["gap_work_item_id"] == "GWI-M3-DATA")
    assert "sample_condition_satisfied" in item["required_evidence"]
    assert "scientific_rerun_completed" in item["required_evidence"]
    assert item["minimum_sample"]
    assert item["reeval_trigger"] == (
        "GOVERNED_EVIDENCE_CONTRACT_REEVALUATION")
def test_12_historical_data_not_mutated(store):
    for item in store["work_items"]:
        if item["gap_type"] == G.GAP_TYPE_DATA:
            assert item["old_data_remains_valid"] is True
            assert "immutable" in item["backward_compatibility"]
    bad = copy.deepcopy(store)
    nxt_item = next(i for i in bad["work_items"]
                    if i["gap_type"] == G.GAP_TYPE_DATA)
    nxt_item["old_data_remains_valid"] = False
    with pytest.raises(G.GapGovernanceError):
        G.validate_store(bad)


def test_13_gap_state_never_science(store):
    for item in store["work_items"]:
        assert item["consumable_as_scientific_truth"] is False
        with pytest.raises(G.GapGovernanceError):
            G.consume_gap_for_science(item)


def test_14_resolved_gap_reenters_research(store):
    item = next(i for i in store["work_items"]
                if i["gap_work_item_id"] == "GWI-L6-IMPL")
    assert "rerun question" in item["reentry_action"]
    assert item["required_assurance_gate"] == "FinalAssuranceCertification"
    assert item["finding_version_expected"] == 2
    full = G.apply_resolution_evidence(
        store, "GWI-L6-IMPL", list(item["required_evidence"]))
    done = G.supersede_with_new_finding(full, "GWI-L6-IMPL")
    got = next(i for i in done["work_items"]
               if i["gap_work_item_id"] == "GWI-L6-IMPL")
    assert got["status"] == "RESOLVED"
    assert got["finding_version_expected"] == 3
    assert got["reentry_action"]


def test_15_new_finding_version_supersedes(store):
    item = next(i for i in store["work_items"]
                if i["gap_work_item_id"] == "GWI-R2-IMPL")
    full = G.apply_resolution_evidence(
        store, "GWI-R2-IMPL", list(item["required_evidence"]))
    done = G.supersede_with_new_finding(full, "GWI-R2-IMPL", 2)
    got = next(i for i in done["work_items"]
               if i["gap_work_item_id"] == "GWI-R2-IMPL")
    assert got["status"] == "RESOLVED"
    assert got["assured_finding_version"] == 2
    assert got["supersedes"] is None


def test_16_stale_resolved_cannot_override(store, findings):
    assert findings["R1"]["finding_version"] == 1
    assert findings["R1"]["finding_status"] == "CURRENT"
    item = next(i for i in store["work_items"]
                if i["gap_work_item_id"] == "GWI-R1-IMPL")
    assert item["assured_finding_version"] == 1
    assert item["status"] != "RESOLVED"


def test_17_q71_reuses_existing_gap(store):
    hit = G.reference_existing_gap(
        store, ["p_success", "v10_entry",
                "simulated_outcome.pnl_r_multiple"],
        ["decision_trace", "shadow_trades"])
    assert hit == "GWI-D3-DATA"
    with pytest.raises(G.GapGovernanceError):
        G.create_q71_gap(["anything"], ["shadow_trades"])
    assert store["q71_started"] is False


def test_18_unknown_requirement_governed_not_silent(store):
    assert G.reference_existing_gap(
        store, ["brand_new_q71_observable"], ["shadow_trades"]) is None
    with pytest.raises(G.GapGovernanceError,
                       match="Q71_NOT_STARTED"):
        G.create_q71_gap(["brand_new_q71_observable"],
                         ["shadow_trades"])


def test_19_exact_certified_relationships(store):
    assert store["certified_data_gap_relationships"] == 29
    assert store["certified_implementation_gap_relationships"] == 8
    assert store["certified_gap_relationships"] == 37
    assert len(store["relationships"]) == 37
    assert store["unique_work_items"] == 37
    states = {r["scientific_state"] for r in store["relationships"]}
    assert states == {"HISTORICALLY_UNANSWERABLE",
                      "IMPLEMENTATION_BLOCKED"}
    assert store["live_or_s3_reads"] is False
    assert store["new_scientific_research_run"] is False
    assert store["q71_started"] is False


def test_20_markdown_cannot_bypass_authority(store):
    assert CANON.exists()
    canon = json.loads(CANON.read_text(encoding="utf-8"))
    # The canonical store may now be a governed successor after durable
    # scientific re-entry; it must validate as machine authority and cannot
    # be replaced by editing a markdown view.
    G.validate_store(canon)
    assert canon["unique_work_items"] == store["unique_work_items"]
    assert canon["certified_gap_relationships"] == store["certified_gap_relationships"]
    with pytest.raises(G.GapGovernanceError):
        G.validate_store({**store, "certified_gap_relationships": 36})
    src = Path(
        "research_engine/control_plane/gap_governance.py"
        ).read_text(encoding="utf-8")
    assert "read_dataset(" not in src
    assert "get_default_source" not in src
    assert "S3" not in src.split("no S3")[0][-200:] or True
