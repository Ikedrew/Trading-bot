"""Focused operational tests for frozen HD15/G3."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from core.production_data_contract import current_schema
from research_engine.control_plane.evidence_resolver import EvidenceResolution
from research_engine.control_plane.state_builder import build_question_state
from research_engine.control_plane.models import ReportValidity, ReadinessStatus, RunnerStatus
from research_engine.control_plane.readiness import resolve_readiness
from research_engine.control_plane.report_ownership import canonical_report_owner
from research_engine.control_plane.report_resolver import resolve_report_validity
from research_engine.experiments.dataset_suitability import run_g1
from research_engine.experiments.lineage_coverage import run_g2
from research_engine.experiments.research_validity import (
    MISSING_REQUIRED_DEPENDENCY,
    REPORT_FILENAME,
    aggregate_snapshot,
    classify_question_state,
    freeze_research_state_snapshot,
    run_g3,
    validate_g3_report,
    validate_research_state_snapshot,
)
from research_engine.registry.master_repair_ledger import (
    MASTER_REPAIR_LEDGER,
    STRUCTURALLY_NON_OPERATIONAL_IDS,
    operational_baseline,
)
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.registry.wave_a_no_runner_definitions import WAVE_A_NO_RUNNER_TARGETS
from research_engine.runner_discovery import discover_runners


AS_OF = "2026-09-25T12:00:00+00:00"


def _state(qid: str, **updates):
    structural = MASTER_REPAIR_LEDGER[qid].structurally_operational
    value = {
        "question_id": qid,
        "readiness_status": "READY",
        "state_status": "READY",
        "runner_status": "READY" if structural else "NO_RUNNER",
        "report_validity": "MISSING",
        "latest_report_status": "NOT_RUN",
        "latest_finding": "",
        "latest_result": None,
        "evidence_epoch": "CURRENT",
        "authoritative_report": None,
    }
    value.update(updates)
    return value


def _states():
    return [_state(question.id) for question in REGISTRY if question.id != "G3"]


def _find(states, qid):
    return next(item for item in states if item["question_id"] == qid)


def _decision(i: int):
    return {
        "schema_version": current_schema("decision_trace"),
        "entity_id": f"entity-{i}", "canonical_opportunity_id": f"opp-{i}",
        "symbol": "EURUSD",
    }


def _outcome(i: int):
    return {
        "schema_version": current_schema("shadow_trades"),
        "entity_id": f"entity-{i}", "canonical_opportunity_id": f"opp-{i}",
        "symbol": "EURUSD", "shadow_type": "PRIMARY_HORIZON_SIMULATION",
        "r_multiple": -1.0, "strategy": "FALSE_BREAK", "trade_horizon": "INTRADAY",
        "h4_regime": "TRENDING", "shadow_trade_id": f"nshadow_{i:016x}",
    }


def _empty_resolution():
    return EvidenceResolution("G3", [], 0, 0, {}, [])


def test_population_is_exactly_69_nonself_once_in_canonical_order():
    states = list(reversed(_states()))
    frozen = freeze_research_state_snapshot(
        states, g1_report=None, g2_report=None, as_of_utc=AS_OF,
    )
    manifest = frozen.manifest()
    expected = [question.id for question in REGISTRY if question.id != "G3"]
    assert manifest["population_order"] == expected
    assert [item["question_id"] for item in manifest["question_states"]] == expected
    assert len(expected) == len(set(expected)) == 69 and "G3" not in expected
    assert validate_research_state_snapshot(manifest)[0]


def test_self_reference_duplicate_and_missing_population_fail_closed():
    states = _states()
    self_ref = deepcopy(states)
    self_ref[0]["question_id"] = "G3"
    duplicate = deepcopy(states)
    duplicate[0]["question_id"] = duplicate[1]["question_id"]
    for invalid in (self_ref, duplicate, states[:-1]):
        report = run_g3(question_states=invalid, as_of_utc=AS_OF)
        assert report["overall"]["finding"] == "EVALUATION_BLOCKED"
        assert report["status"] == "BLOCKED"
        assert validate_g3_report(report)[0]


def test_deterministic_snapshot_order_digest_and_input_immutability():
    states = _states()
    first = freeze_research_state_snapshot(
        states, g1_report=None, g2_report=None, as_of_utc=AS_OF,
    )
    second = freeze_research_state_snapshot(
        list(reversed(states)), g1_report=None, g2_report=None, as_of_utc=AS_OF,
    )
    assert first.snapshot_id == second.snapshot_id
    before = first.manifest()
    states[0]["question_id"] = "MUTATED"
    assert first.manifest() == before


def test_exact_taxonomy_preserves_negative_waiting_blocked_nonoperational_and_stale():
    negative = _state(
        "R3", readiness_status="COMPLETE", state_status="COMPLETE",
        report_validity="VALID_CURRENT", latest_report_status="COMPLETE",
        latest_finding="NEGATIVE_EDGE", authoritative_report={"finding": "NEGATIVE_EDGE"},
    )
    assert classify_question_state(negative)[0] == "TRUSTED_CURRENT_CONCLUSION"
    assert classify_question_state(_state("EX5", readiness_status="WAITING_DATA", state_status="WAITING_DATA"))[0] == "WAITING_DATA"
    assert classify_question_state(_state("L2", readiness_status="BLOCKED", state_status="BLOCKED"))[0] == "BLOCKED"
    assert classify_question_state(_state("L6"))[0] == "NON_OPERATIONAL"
    stale = _state("R3", report_validity="STALE", latest_report_status="COMPLETE", latest_finding="NEGATIVE_EDGE")
    assert classify_question_state(stale)[0] == "REPORT_NOT_VALID_CURRENT"
    assert len({"WAITING_DATA", "BLOCKED", "NON_OPERATIONAL", "REPORT_NOT_VALID_CURRENT"}) == 4


def test_unknown_is_preserved_yields_evaluation_unknown_and_cannot_complete():
    states = _states()
    _find(states, "R3").update(readiness_status="UNKNOWN", state_status="UNKNOWN")
    report = run_g3(question_states=states, as_of_utc=AS_OF)
    assert next(
        row for row in report["provenance"]["snapshot"]["question_states"]
        if row["question_id"] == "R3"
    )["trust_state"] == "UNKNOWN"
    assert report["overall"]["finding"] == "EVALUATION_UNKNOWN"
    assert report["status"] == "BLOCKED"
    validity, reason = resolve_report_validity(REPORT_FILENAME, report, expected_question_id="G3")
    assert validity == ReportValidity.VALID_CURRENT, reason
    readiness, _ = resolve_readiness(
        REGISTRY_BY_ID["G3"], _empty_resolution(), RunnerStatus.READY,
        validity, report["status"], {}, report["overall"]["finding"],
    )
    assert readiness == ReadinessStatus.UNKNOWN


def test_g3_authority_defect_is_evaluation_blocked_and_cannot_complete():
    g1 = run_g1(datasets={}, as_of_utc=AS_OF)
    g1["fingerprint"]["epoch"] = "LEGACY"
    report = run_g3(question_states=_states(), g1_report=g1, as_of_utc=AS_OF)
    assert report["overall"]["finding"] == "EVALUATION_BLOCKED"
    assert report["status"] == "BLOCKED"
    assert report["overall"]["authority_defect_prevents_completion"] is True


def test_missing_l6_is_explicit_prevents_positive_but_permits_valid_negative_complete():
    report = run_g3(question_states=_states(), as_of_utc=AS_OF)
    dependencies = {item["question_id"]: item for item in report["overall"]["dependencies"]}
    assert dependencies["L6"]["availability"] == MISSING_REQUIRED_DEPENDENCY
    assert dependencies["L6"]["positive"] is False
    assert report["overall"]["finding"] == "RESEARCH_TRUST_NOT_YET_DEMONSTRATED"
    assert report["status"] == "COMPLETE"
    assert validate_g3_report(report)[0]
    validity, reason = resolve_report_validity(REPORT_FILENAME, report, expected_question_id="G3")
    assert validity == ReportValidity.VALID_CURRENT, reason
    readiness, _ = resolve_readiness(
        REGISTRY_BY_ID["G3"], _empty_resolution(), RunnerStatus.READY,
        validity, "COMPLETE", {}, report["overall"]["finding"],
    )
    assert readiness == ReadinessStatus.COMPLETE


def test_valid_negative_g1_and_below_threshold_g2_prevent_positive_without_invalidating_g3():
    g1 = run_g1(datasets={}, as_of_utc=AS_OF)
    assert g1["status"] == "COMPLETE" and g1["overall"]["finding"] != "SUITABLE"
    g2 = run_g2(
        decision_records=[_decision(i) for i in range(100)],
        outcome_records=[_outcome(i) for i in range(49)],
        as_of_utc=AS_OF,
    )
    assert g2["status"] == "COMPLETE"
    assert g2["overall"]["threshold_result"] == "LINEAGE_THRESHOLD_NOT_MET"
    report = run_g3(question_states=_states(), g1_report=g1, g2_report=g2, as_of_utc=AS_OF)
    dependencies = {item["question_id"]: item for item in report["overall"]["dependencies"]}
    assert dependencies["G1"]["availability"] == "AVAILABLE_VALID_CURRENT"
    assert dependencies["G2"]["availability"] == "AVAILABLE_VALID_CURRENT"
    assert dependencies["G1"]["positive"] is False
    assert dependencies["G2"]["positive"] is False
    assert report["overall"]["finding"] == "RESEARCH_TRUST_NOT_YET_DEMONSTRATED"
    assert report["status"] == "COMPLETE"


def test_g3_never_reruns_dependencies_and_rejects_raw_science_inputs(monkeypatch):
    import research_engine.experiments.dataset_suitability as g1_module
    import research_engine.experiments.lineage_coverage as g2_module

    monkeypatch.setattr(g1_module, "run_g1", lambda **_: pytest.fail("G1 was rerun"))
    monkeypatch.setattr(g2_module, "run_g2", lambda **_: pytest.fail("G2 was rerun"))
    report = run_g3(question_states=_states(), as_of_utc=AS_OF)
    assert report["status"] == "COMPLETE"
    with pytest.raises(TypeError):
        run_g3(question_states=_states(), shadow_trades=[], r_multiples=[], patterns=[])
    assert "score" not in report["overall"] and "ranking" not in report["overall"]


def test_same_epoch_and_snapshot_report_tampering_fail_closed():
    states = _states()
    _find(states, "R3")["evidence_epoch"] = "LEGACY"
    assert run_g3(question_states=states, as_of_utc=AS_OF)["overall"]["finding"] == "EVALUATION_BLOCKED"
    good = run_g3(question_states=_states(), as_of_utc=AS_OF)
    tampered = deepcopy(good)
    tampered["provenance"]["snapshot"]["question_states"][0]["trust_state"] = "TRUSTED_CURRENT_CONCLUSION"
    assert not validate_g3_report(tampered)[0]
    validity, _ = resolve_report_validity(REPORT_FILENAME, tampered, expected_question_id="G3")
    assert validity == ReportValidity.INVALIDATED


def test_owned_valid_current_report_is_required_and_cannot_cross_complete():
    report = run_g3(question_states=_states(), as_of_utc=AS_OF)
    validity, reason = resolve_report_validity(REPORT_FILENAME, report, expected_question_id="G3")
    assert validity == ReportValidity.VALID_CURRENT, reason
    for other in ("G1", "G2", "L6", "R3"):
        assert resolve_report_validity(REPORT_FILENAME, report, expected_question_id=other)[0] != ReportValidity.VALID_CURRENT
    self_declared = deepcopy(report)
    self_declared["overall"]["finding"] = "RESEARCH_TRUST_DEMONSTRATED"
    assert resolve_report_validity(REPORT_FILENAME, self_declared, expected_question_id="G3")[0] == ReportValidity.INVALIDATED
    assert canonical_report_owner(REPORT_FILENAME) == "G3"


def test_canonical_state_builder_completes_only_valid_persisted_g3_report(tmp_path):
    report = run_g3(question_states=_states(), as_of_utc=AS_OF)
    (tmp_path / REPORT_FILENAME).write_text(json.dumps(report), encoding="utf-8")
    state = build_question_state("G3", reports_dir=tmp_path, evidence_source={})
    assert state.report_validity == ReportValidity.VALID_CURRENT
    assert state.readiness_status == ReadinessStatus.COMPLETE
    assert state.state_status == "COMPLETE"


def test_positive_aggregation_requires_every_hd15_positive_condition():
    report = run_g3(question_states=_states(), as_of_utc=AS_OF)
    manifest = report["provenance"]["snapshot"]
    assert aggregate_snapshot(manifest) == "RESEARCH_TRUST_NOT_YET_DEMONSTRATED"
    assert any(row["trust_state"] != "TRUSTED_CURRENT_CONCLUSION" for row in manifest["question_states"])
    dependencies = {item["question_id"]: item for item in manifest["dependencies"]}
    assert not all(item["positive"] for item in dependencies.values())
    assert dependencies["L6"]["availability"] == MISSING_REQUIRED_DEPENDENCY


def test_registry_discovery_definition_ledger_and_remaining_scope():
    assert REGISTRY_BY_ID["G3"].runner_module == "research_engine.experiments.research_validity"
    assert REGISTRY_BY_ID["G3"].runner_function == "run_g3"
    assert REGISTRY_BY_ID["G3"].report_filename == REPORT_FILENAME
    assert discover_runners()["G3"] is run_g3
    assert operational_baseline() == (63, 7)
    assert STRUCTURALLY_NON_OPERATIONAL_IDS == {"EX5", "EX6", "EX8", "L2", "L3", "L6", "L7"}
    assert WAVE_A_NO_RUNNER_TARGETS == {"L6"}
