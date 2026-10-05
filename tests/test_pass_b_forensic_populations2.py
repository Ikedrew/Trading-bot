"""Pass B targeted verification (no full cycle; no re-baseline)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_engine.control_plane.pass_b_forensic_ledger import (
    CLASSIFICATION,
    FORENSIC_PROJECTION_ID,
    FORENSIC_SNAPSHOT_ID,
    FROZEN_GOVERNED_USABLE,
    ledger,
)
from research_engine.control_plane.stage4_ex2_l7_blocker_adjudication import (
    EX2_POPULATION,
)
from research_engine.control_plane.stage4_implementation_repairs import (
    CURRENT_EXACT_POPULATION_RETIRED,
    GOVERNED_USABLE,
    HISTORICAL_GOVERNED_USABLE,
    REPAIR_MODULE_VERSION,
    Stage4RepairError,
)
from research_engine.control_plane.stage4_impl_population2 import (
    enforce_exact_population,
)
from research_engine.experiments.architecture_assumption_validity import (
    validate_l3_report,
)
from research_engine.experiments.component_reward import (
    build_attribution_records,
)
from research_engine.experiments.lineage_coverage import (
    classify_lineage, run_g2, validate_g2_report,
)
from research_engine.registry.research_question_registry import REGISTRY_BY_ID

ROOT = Path(__file__).resolve().parents[1]
PROJECTION = (
    ROOT / "data" / "research" / "continuous" / "projection" / "history"
    / f"{FORENSIC_PROJECTION_ID}.json"
)


def _projection_results() -> dict:
    payload = json.loads(PROJECTION.read_text(encoding="utf-8"))
    return {item["question_id"]: item for item in payload["canonical_questions"]}


def test_historical_authority_is_preserved_but_current_l3_g2_gates_are_retired():
    assert GOVERNED_USABLE == FROZEN_GOVERNED_USABLE
    assert HISTORICAL_GOVERNED_USABLE == FROZEN_GOVERNED_USABLE
    assert GOVERNED_USABLE["L3"] == 95
    assert GOVERNED_USABLE["G2"] == 261
    assert GOVERNED_USABLE["EX2"] == 8760
    assert REPAIR_MODULE_VERSION == "stage4_impl_repairs_v2"
    assert EX2_POPULATION == 8760
    assert CURRENT_EXACT_POPULATION_RETIRED == {"L3", "G2"}


def test_exact_population_gate_is_historical_only_for_l3_and_g2():
    with pytest.raises(Stage4RepairError, match="CURRENT_EXACT_POPULATION_RETIRED:G2"):
        enforce_exact_population("G2", [{}] * 384)
    with pytest.raises(Stage4RepairError):
        enforce_exact_population("EX2", [{}] * 13795)
    with pytest.raises(Stage4RepairError, match="CURRENT_EXACT_POPULATION_RETIRED:L3"):
        enforce_exact_population("L3", [{"a": 1}] * 95)
    with pytest.raises(Stage4RepairError, match="CURRENT_EXACT_POPULATION_RETIRED:G2"):
        enforce_exact_population("G2", [{"a": 1}] * 261)
def test_l3_projection_result_is_d1_runner_contamination():
    results = _projection_results()
    l3, d1 = results["L3"], results["D1"]
    assert l3["result"]["runner"] == (
        "research_engine.experiments.component_reward.run")
    assert l3["result"]["runner"] == d1["result"]["runner"]
    assert l3["result"]["sample_n"] == d1["result"]["sample_n"] == 1542
    assert l3["result"]["key_metrics"] == d1["result"]["key_metrics"]
    owned = REGISTRY_BY_ID["L3"]
    assert owned.runner_module == (
        "research_engine.experiments.architecture_assumption_validity")
    assert owned.runner_function == "run_l3"
    assert CLASSIFICATION["L3"] == "F"


def test_l3_owned_validator_uses_hd11_subtests_not_historical_95():
    from research_engine.experiments.architecture_assumption_validity import run_l3
    report = run_l3(records=[{"canonical_opportunity_id": f"O{i}"} for i in range(1542)])
    ok, _ = validate_l3_report(report)
    assert ok is True
    assert report["dataset"]["sample_size"] == 1542
    assert report["status"] == "BLOCKED"
    assert set(report["missing_evidence"]) == {
        "L3_WEIGHT_PROFILE_ABSENT", "L3_MAPPING_EVIDENCE_BLOCKER"}
    assert report["key_metrics"]["historical_stage4_assurance_n"] == 95
    assert report["key_metrics"]["historical_count_enforced_for_current"] is False


def test_g2_current_snapshot_population_is_384_not_261_freeze():
    results = _projection_results()
    assert results["G2"]["result"]["status"] == "INVALID"
    assert "got=384:expected=261" in results["G2"]["result"]["failure_reason"]
    assert results["L3"]["result"]["key_metrics"]["total_current_population"] == 384
    assert CLASSIFICATION["G2"] == "E"


def test_g2_current_denominator_384_is_exhaustively_derived_and_ready():
    decisions = [
        {"schema_version": "decision_trace_v1", "data_epoch": "CURRENT",
         "entity_id": f"E{i}", "canonical_opportunity_id": f"O{i}"}
        for i in range(384)
    ]
    outcomes = [
        {"schema_version": "shadow_trades_v1", "data_epoch": "CURRENT",
         "entity_id": f"E{i}", "canonical_opportunity_id": f"O{i}",
         "r_multiple": 1.0}
        for i in range(384)
    ]
    report = run_g2(decision_records=decisions, outcome_records=outcomes,
                    as_of_utc="2026-10-04T00:00:00Z")
    assert report["dataset"]["sample_size"] == 384
    assert report["overall"]["denominator"] == 384
    assert report["overall"]["minimum_denominator"] == 100
    assert report["status"] == "COMPLETE"
    assert validate_g2_report(report)[0] is True


def test_g2_classify_lineage_is_deterministic_and_exact():
    decisions = [
        {"entity_id": "E1", "canonical_opportunity_id": "O1"},
        {"entity_id": "E2", "canonical_opportunity_id": "O2"},
    ]
    outcomes = [
        {"entity_id": "E1", "canonical_opportunity_id": "O1", "r_multiple": 1.0},
        {"entity_id": "E2", "canonical_opportunity_id": "O2", "r_multiple": -1.0},
    ]
    first = classify_lineage(decisions, outcomes)
    assert first["denominator"] == 2
    assert first["valid"] == 2
    assert first["coverage"] == 1.0
    repeat = classify_lineage(decisions, outcomes)
    assert repeat == first
    dup = classify_lineage(decisions + [dict(decisions[0])], outcomes)
    dup_counts = dup["class_counts"]
    assert dup_counts.get("AMBIGUOUS", 0) >= 1
    assert dup["denominator"] == 2
    assert dup["status"] if "status" in dup else True


def test_ex2_population_fix_is_independent_from_bar_path_evidence():
    results = _projection_results()
    assert "got=13795:expected=8760" in results["EX2"]["result"]["failure_reason"]
    assert CLASSIFICATION["EX2"] == "H"
    from research_engine.v10.investigation_snapshot import BOUND_DATASETS
    assert "events_v1" not in BOUND_DATASETS
    from research_engine.experiments.exit_policy_governed import run_ex2
    report = run_ex2(governed_records=[{}] * 13795)
    assert report["status"] == "BLOCKED"
    assert report["scientific_state"] == "HISTORICALLY_UNANSWERABLE"
    assert report["dataset"]["sample_size"] == 8760
    audit = report["provenance"]["observation_gap_adjudication"]
    assert audit["historically_unobserved"] == 2844
    assert report["provenance"]["current_snapshot_rows_not_substituted"] == 13795


def test_d1_duplicate_lineage_blocks_and_is_deterministic():
    results = _projection_results()
    assert results["D1"]["result"]["status"] == "IMPLEMENTATION_BLOCKED"
    assert results["D1"]["result"]["sample_n"] == 1542
    assert results["D1"]["result"]["key_metrics"][
        "ambiguous_or_unmatched_excluded"] == 21254
    decisions = [
        {"canonical_opportunity_id": "O1", "pattern_detected": True,
         "score": 1.0, "components": {"score": 1.0}},
        {"canonical_opportunity_id": "O1", "pattern_detected": True,
         "score": 2.0, "components": {"score": 2.0}},
    ]
    outcomes = [{"canonical_opportunity_id": "O1",
                 "identity": {"shadow_type": "PRIMARY_HORIZON_SIMULATION"},
                 "r_multiple": 1.0}]
    from research_engine.experiments.component_reward import (
        _build_attribution_records_with_diagnostics,
    )
    first, first_diag = _build_attribution_records_with_diagnostics(
        decisions, outcomes)
    second, second_diag = _build_attribution_records_with_diagnostics(
        decisions, outcomes)
    assert first_diag["duplicate_decision_opportunities"] == ["O1"]
    assert len(first) == 0
    assert (first_diag["duplicate_decision_opportunities"]
            == second_diag["duplicate_decision_opportunities"])
    assert CLASSIFICATION["D1"] == "H"


def test_ledger_reports_no_rebaseline_and_preserved_history():
    entry = ledger()
    assert entry["projection_id"] == FORENSIC_PROJECTION_ID
    assert entry["snapshot_id"] == FORENSIC_SNAPSHOT_ID
    assert entry["rebaselined"] == []
    assert entry["historical_baselines_preserved"] is True
    assert entry["scientific_meaning_changed"] is False
