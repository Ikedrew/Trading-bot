"""Focused operational tests for frozen HD13/G1 and HD14/G2."""
from __future__ import annotations

from copy import deepcopy
from collections import Counter

from core.production_data_contract import current_schema
from research_engine.control_plane.models import ReportValidity, ReadinessStatus, RunnerStatus
from research_engine.control_plane.report_ownership import canonical_report_owner
from research_engine.control_plane.report_resolver import resolve_report_validity
from research_engine.control_plane.readiness import resolve_readiness
from research_engine.control_plane.evidence_resolver import EvidenceResolution
from research_engine.experiments.dataset_suitability import run_g1, validate_g1_report
from research_engine.experiments.lineage_coverage import classify_lineage, run_g2, validate_g2_report
from research_engine.registry.master_repair_ledger import STRUCTURALLY_NON_OPERATIONAL_IDS, operational_baseline
from research_engine.registry.research_question_registry import REGISTRY_BY_ID


def decision(i: int, **extra):
    return {"schema_version": current_schema("decision_trace"), "entity_id": f"entity-{i}",
            "canonical_opportunity_id": f"opp-{i}", "symbol": "EURUSD", **extra}


def outcome(i: int, **extra):
    return {"schema_version": current_schema("shadow_trades"), "entity_id": f"entity-{i}",
            "canonical_opportunity_id": f"opp-{i}", "symbol": "EURUSD",
            "shadow_type": "PRIMARY_HORIZON_SIMULATION", "r_multiple": 1.0,
            "strategy": "FALSE_BREAK", "trade_horizon": "INTRADAY",
            "h4_regime": "TRENDING", "shadow_trade_id": f"nshadow_{i:016x}", **extra}


def empty_resolution():
    return EvidenceResolution("G1", [], 0, 0, {}, [])


def test_g1_all70_immutable_exhaustive_and_nonrecursive():
    report = run_g1(datasets={}, as_of_utc="2026-09-25T00:00:00+00:00")
    valid, reason = validate_g1_report(report)
    assert valid, reason
    assessments = report["overall"]["assessments"]
    assert len(assessments) == len({item["question_id"] for item in assessments}) == 70
    assert all(len(item["requirements"]) == len({row["requirement_id"] for row in item["requirements"]}) for item in assessments)
    assert {row["category"] for item in assessments for row in item["requirements"]} == {
        "SCIENCE_CONTRACT", "SOURCE_AUTHORITY", "FIELD_COVERAGE", "JOINABILITY",
        "SAMPLE_SUFFICIENCY", "CURRENT_PROVENANCE",
    }
    assert report["provenance"]["snapshot"]["snapshot_id"] == report["fingerprint"]["dataset_id"]
    assert "G1 is evaluated nonrecursively" in report["assumptions"][0]
    broken = deepcopy(report)
    broken["overall"]["assessments"][0]["requirements"].pop()
    assert not validate_g1_report(broken)[0]


def test_g1_waiting_blocked_unknown_and_negative_completion_semantics():
    report = run_g1(datasets={}, as_of_utc="2026-09-25T00:00:00+00:00")
    statuses = {item["status"] for item in report["overall"]["assessments"]}
    assert "WAITING_DATA" in statuses
    assert "BLOCKED" in statuses
    assert report["status"] == "BLOCKED"  # UNKNOWN is fail-closed under HD13
    unsuitable = deepcopy(report)
    target = next(item for item in unsuitable["overall"]["assessments"] if item["status"] == "WAITING_DATA")
    next(row for row in target["requirements"] if row["category"] != "SCIENCE_CONTRACT")["status"] = "FAIL"
    target["status"] = "UNSUITABLE"
    unsuitable_statuses = [item["status"] for item in unsuitable["overall"]["assessments"]]
    unsuitable["overall"]["status_counts"] = dict(Counter(unsuitable_statuses))
    assert unsuitable["status"] == "BLOCKED"
    assert validate_g1_report(unsuitable)[0]
    unknown = deepcopy(report)
    target = unknown["overall"]["assessments"][0]
    target["requirements"][0]["status"] = "UNKNOWN"
    target["status"] = "UNKNOWN"
    unknown["overall"]["finding"] = "UNKNOWN"
    unknown["status"] = "COMPLETE"
    assert not validate_g1_report(unknown)[0]


def test_g1_owned_valid_current_report_is_the_only_completion_authority():
    report = run_g1(datasets={}, as_of_utc="2026-09-25T00:00:00+00:00")
    validity, reason = resolve_report_validity("g1_dataset_suitability.json", report, expected_question_id="G1")
    assert validity == ReportValidity.VALID_CURRENT, reason
    denied, _ = resolve_report_validity("g1_dataset_suitability.json", report, expected_question_id="G2")
    assert denied != ReportValidity.VALID_CURRENT
    generic = deepcopy(report)
    generic["provenance"]["report_identity"] = "registry_audit.json"
    assert resolve_report_validity("g1_dataset_suitability.json", generic, expected_question_id="G1")[0] != ReportValidity.VALID_CURRENT
    ready, _ = resolve_readiness(REGISTRY_BY_ID["G1"], empty_resolution(), RunnerStatus.READY,
                                 ReportValidity.VALID_CURRENT, "COMPLETE", {})
    assert ready == ReadinessStatus.COMPLETE
    missing, _ = resolve_readiness(REGISTRY_BY_ID["G1"], empty_resolution(), RunnerStatus.READY,
                                   ReportValidity.MISSING, "COMPLETE", {})
    assert missing == ReadinessStatus.BLOCKED


def test_g2_exhaustive_union_composite_identity_and_no_fanout_inflation():
    decisions = [decision(1), decision(1, account_id="A"), decision(2)]
    outcomes = [outcome(1), outcome(2), outcome(2, shadow_type="HORIZON_ALTERNATIVE", evaluated_horizon="H4"), outcome(3)]
    result = classify_lineage(decisions, outcomes)
    assert result["identity"] == ["entity_id", "canonical_opportunity_id"]
    assert result["denominator"] == 3
    assert result["excluded_alternative_horizon_records"] == 1
    assert sum(result["class_counts"].values()) == 3
    by_id = {row["canonical_opportunity_id"]: row["classification"] for row in result["classifications"]}
    assert by_id == {"opp-1": "AMBIGUOUS", "opp-2": "VALID", "opp-3": "MISSING"}


def test_g2_missing_ambiguous_conflicting_and_no_fallback_or_drop():
    decisions = [decision(1), decision(2), decision(2), decision(3),
                 {"schema_version": current_schema("decision_trace"), "entity_id": "entity-x", "correlation_id": "opp-4"}]
    outcomes = [outcome(1), outcome(2), outcome(3, symbol="GBPUSD"), outcome(4)]
    result = classify_lineage(decisions, outcomes)
    by_id = {row["canonical_opportunity_id"]: row["classification"] for row in result["classifications"]}
    assert by_id["opp-1"] == "VALID"
    assert by_id["opp-2"] == "AMBIGUOUS"
    assert by_id["opp-3"] == "CONFLICTING"
    assert by_id["opp-4"] == "MISSING"
    assert result["unresolved_orphan_count"] == 1
    assert result["denominator"] == sum(result["class_counts"].values())
    assert result["coverage"] == result["valid"] / result["denominator"]
    blocked = run_g2(decision_records=decisions, outcome_records=outcomes,
                     as_of_utc="2026-09-25T00:00:00+00:00")
    assert blocked["status"] == "BLOCKED"
    assert blocked["overall"]["unresolved_orphan_count"] == 1


def test_g2_readiness_threshold_negative_completion_and_no_interval():
    insufficient = run_g2(decision_records=[decision(i) for i in range(99)],
                          outcome_records=[outcome(i) for i in range(99)],
                          as_of_utc="2026-09-25T00:00:00+00:00")
    assert insufficient["status"] == "INSUFFICIENT_DATA"
    below = run_g2(decision_records=[decision(i) for i in range(100)],
                   outcome_records=[outcome(i) for i in range(49)],
                   as_of_utc="2026-09-25T00:00:00+00:00")
    assert below["status"] == "COMPLETE"
    assert below["overall"]["coverage"] == 0.49
    assert below["overall"]["threshold_result"] == "LINEAGE_THRESHOLD_NOT_MET"
    assert "interval" not in str(below["overall"]).lower()
    assert validate_g2_report(below)[0]
    validity, reason = resolve_report_validity("g2_lineage_coverage.json", below, expected_question_id="G2")
    assert validity == ReportValidity.VALID_CURRENT, reason
    exact = run_g2(decision_records=[decision(i) for i in range(100)],
                   outcome_records=[outcome(i) for i in range(50)],
                   as_of_utc="2026-09-25T00:00:00+00:00")
    assert exact["overall"]["coverage"] == 0.5
    assert exact["overall"]["threshold_result"] == "LINEAGE_THRESHOLD_MET"


def test_g1_g2_unique_integration_tracks_derived_governance_baseline():
    assert canonical_report_owner("g1_dataset_suitability.json") == "G1"
    assert canonical_report_owner("g2_lineage_coverage.json") == "G2"
    assert operational_baseline() == (
        len(REGISTRY_BY_ID) - len(STRUCTURALLY_NON_OPERATIONAL_IDS),
        len(STRUCTURALLY_NON_OPERATIONAL_IDS),
    )
    assert STRUCTURALLY_NON_OPERATIONAL_IDS == {"EX5", "EX6", "EX8", "L2", "L6", "L7"}
    assert REGISTRY_BY_ID["G3"].runner_module == "research_engine.experiments.research_validity"
    assert REGISTRY_BY_ID["G3"].report_filename == "g3_research_validity.json"
