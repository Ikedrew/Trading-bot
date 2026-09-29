from __future__ import annotations
import json
from pathlib import Path
import pytest
from research_engine.control_plane import assured_epistemic_findings as A

STORE = Path("analysis/assurance/assured_epistemic_findings_20260928.json")

@pytest.fixture(scope="module")
def store():
    return json.loads(STORE.read_text(encoding="utf-8"))

def test_exact_70_current(store):
    assert store["question_count"] == 70 == len(store["findings"])
    assert {f["question_id"] for f in store["findings"]}
    assert all(f["finding_status"] == "CURRENT" for f in store["findings"])
    assert all(f["finding_version"] == 1 and f["supersedes"] is None
               for f in store["findings"])
    assert store["supersession"]["superseded_count"] == 0
    assert store["q71_started"] is False
    assert store["certification_fingerprint"] == (
        A.EXPECTED_CERTIFICATION_FINGERPRINT)

def test_state_counts(store):
    assert store["scientific_state_counts"] == {
        "COMPLETE": 12, "NEGATIVE_RESULT": 2, "INSUFFICIENT_DATA": 16,
        "WAITING_DATA": 3, "HISTORICALLY_UNANSWERABLE": 29,
        "IMPLEMENTATION_BLOCKED": 8}
    assert not [f for f in store["findings"]
                if f["scientific_state"] == "UNKNOWN"]
    assert not [f for f in store["findings"]
                if f["scientific_state"] == "NO_EFFECT"]

def test_verified_only_truth_consumable(store):
    truth = [f for f in store["findings"]
             if f["consumption"]["scientific_truth_consumable"]]
    assert len(truth) == 14
    assert {f["scientific_state"] for f in truth} <= {
        "COMPLETE", "NEGATIVE_RESULT"}
    for f in store["findings"]:
        if f["consumption"]["scientific_truth_consumable"]:
            A.consume_scientific_truth(f)
        else:
            with pytest.raises(A.AssuredFindingsError):
                A.consume_scientific_truth(f)

def test_unresolved_never_market_truth(store):
    bad = [f["question_id"] for f in store["findings"]
           if f["scientific_state"] in (
               "INSUFFICIENT_DATA", "WAITING_DATA",
               "HISTORICALLY_UNANSWERABLE", "IMPLEMENTATION_BLOCKED")
           and f["consumption"]["scientific_truth_consumable"]]
    assert bad == []
    for f in store["findings"]:
        d = A.decide(f["question_id"], f["assurance_status"],
                     f["scientific_state"])
        if d.scientific_state in ("INSUFFICIENT_DATA", "WAITING_DATA",
                                  "HISTORICALLY_UNANSWERABLE"):
            assert d.permitted_consumption == (
                "EVIDENCE_COLLECTION", "OBSERVABILITY")
        if d.scientific_state == "IMPLEMENTATION_BLOCKED":
            assert d.permitted_consumption == ("IMPLEMENTATION_REPAIR",)

def test_shortfall_and_trigger_semantics(store):
    rows = {f["question_id"]: f for f in store["findings"]}
    for q in [f for f in rows.values()
              if f["scientific_state"] == "INSUFFICIENT_DATA"]:
        assert isinstance(q["next_action"], dict)
        assert q["next_action"]["missing_observables"]
        assert "Shortfall" in q["interpretation"] or "shortfall" in (
            q["interpretation"].lower())
    for qid in ("EX5", "EX6", "EX8"):
        q = rows[qid]
        assert q["scientific_state"] == "WAITING_DATA"
        assert q["next_action"]["trigger_type"] == (
            "GOVERNED_EVIDENCE_CONTRACT_REEVALUATION")
        assert q["next_action"]["thresholds"]

def test_gap_linkage(store):
    dgaps = json.loads(Path(
        "analysis/assurance/stage4_dataset_schema_gap_register_20260928.json"
        ).read_text(encoding="utf-8"))["gaps"]
    igaps = json.loads(Path(
        "analysis/assurance/stage4_implementation_gap_register_20260928.json"
        ).read_text(encoding="utf-8"))["gaps"]
    hist = [f for f in store["findings"]
            if f["scientific_state"] == "HISTORICALLY_UNANSWERABLE"]
    assert len(hist) == 29 == len(dgaps)
    assert {f["schema_gap_id"] for f in hist} == {
        g["gap_id"] for g in dgaps}
    blocked = [f for f in store["findings"]
               if f["scientific_state"] == "IMPLEMENTATION_BLOCKED"]
    assert len(blocked) == 8 == len(igaps)
    assert {f["implementation_gap_id"] for f in blocked} == {
        g["question_id"] for g in igaps}

def test_no_raw_report_bypass():
    src = Path(
        "research_engine/control_plane/assured_epistemic_findings.py"
        ).read_text(encoding="utf-8")
    assert "get_default_source" not in src
    assert "read_dataset(" not in src
    assert "load_report_for_question" not in src
    assert "_load_report" not in src
    assert "json.load(open(\"analysis/reports" not in src
    store = json.loads(STORE.read_text(encoding="utf-8"))
    assert store["certification_fingerprint"] == (
        A.EXPECTED_CERTIFICATION_FINGERPRINT)

def test_q71_gated(store):
    assert A.gate_q71("E2", "VERIFIED", "COMPLETE").admitted is True
    d = A.gate_q71("EX5", "VERIFIED", "WAITING_DATA")
    assert d.admitted and not d.scientific_truth_consumable
    with pytest.raises(Exception):
        A.gate_q71("EX5", "VERIFIED", "WAITING_DATA", claim=True)
    with pytest.raises(Exception):
        A.gate_q71("R1", "VERIFIED", "IMPLEMENTATION_BLOCKED", claim=True)
    with pytest.raises(Exception):
        A.gate_q71("E2", "INDETERMINATE", "COMPLETE", claim=True)
