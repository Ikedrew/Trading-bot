"""Stage 4 implementation-gap resolution tests (Q1-Q70 scope, no S3/live).

Covers GWI-R1/R2/L3/L6/L7/G2/G3/EX2 repair evidence.  Read-only with respect
to every frozen V1 artifact: no certification, finding, report, or gap-state
file is written by this module.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import pytest
from research_engine.control_plane import assured_epistemic_findings as A
from research_engine.control_plane import gap_governance as G
from research_engine.control_plane import stage4_implementation_repairs as R
from research_engine.control_plane.stage4_impl_ownership_labels import (
    assign_l7_labels, gate_g3_on_l6, govern_l7_label, l6_runner_identity,
    resolve_l3_ownership,
)
from research_engine.control_plane.stage4_impl_population2 import (
    enforce_exact_population, require_governed_identities_for_science,
)
from research_engine.control_plane.report_ownership import (
    ADJUDICATED_REPORT_OWNERS, canonical_report_owner, resolve_report_ownership,
)
CERT = Path("analysis/assurance/final_70_question_certification_20260928.json")
FIND = Path("analysis/assurance/assured_epistemic_findings_20260928.json")
CANON = Path("research_engine/control_plane/gap_governance_state.json")
REPAIR_MODULES = (
    Path("research_engine/control_plane/stage4_implementation_repairs.py"),
    Path("research_engine/control_plane/stage4_impl_population2.py"),
    Path("research_engine/control_plane/stage4_impl_ownership_labels.py"),
    Path("research_engine/experiments/learning_cycle_validation.py"),
    Path("research_engine/experiments/adaptation_evidence.py"),
)
@pytest.fixture(scope="module")
def rows():
    return {r["question_id"]: r for r in json.loads(CERT.read_text(encoding="utf-8"))["certifications"]}
@pytest.fixture(scope="module")
def store():
    return G.build_store()
def _recs(n, key="canonical_opportunity_id"):
    return [{key: f"OPP-{i:06d}"} for i in range(n)]
@pytest.mark.parametrize("qid,n,forbidden", [
    ("R1", 635, 10803), ("R2", 635, 10803),
    ("EX2", 8760, 9045),
])
def test_01_exact_governed_population_admitted(qid, n, forbidden):
    assert len(enforce_exact_population(qid, _recs(n))) == n
def test_02_r1_r2_cannot_fall_back_to_10803():
    for qid in ("R1", "R2"):
        with pytest.raises(R.Stage4RepairError, match="FORBIDDEN_RUNNER_POPULATION"):
            enforce_exact_population(qid, _recs(10803))
        with pytest.raises(R.Stage4RepairError, match="POPULATION_MISMATCH"):
            enforce_exact_population(qid, _recs(634))
def test_03_g2_current_exact_gate_is_retired_for_all_denominators():
    assert R.HISTORICAL_GOVERNED_USABLE["G2"] == 261
    with pytest.raises(R.Stage4RepairError, match="CURRENT_EXACT_POPULATION_RETIRED:G2"):
        enforce_exact_population("G2", _recs(261))
    with pytest.raises(R.Stage4RepairError, match="CURRENT_EXACT_POPULATION_RETIRED:G2"):
        enforce_exact_population("G2", _recs(22521))
def test_04_ex2_cannot_reconstruct_9045_event_rows():
    with pytest.raises(R.Stage4RepairError, match="FORBIDDEN_RUNNER_POPULATION"):
        enforce_exact_population("EX2", _recs(9045))
    with pytest.raises(R.Stage4RepairError, match="POPULATION_MISMATCH"):
        enforce_exact_population("EX2", _recs(8761))
def test_05_identity_roster_selection_is_exact_and_non_widening():
    roster = [f"OPP-{i:06d}" for i in range(635)]
    rows_ = _recs(700)
    got = enforce_exact_population("R1", rows_, roster)
    assert [r["canonical_opportunity_id"] for r in got] == roster
    with pytest.raises(R.Stage4RepairError, match="GOVERNED_IDENTITY_COUNT_MISMATCH"):
        enforce_exact_population("R1", rows_, roster[:634])
    with pytest.raises(R.Stage4RepairError, match="GOVERNED_IDENTITY_ABSENT"):
        enforce_exact_population("R1", rows_, roster[:-1] + ["OPP-999999"])
def test_06_population_fingerprint_is_deterministic():
    ids = [f"OPP-{i:04d}" for i in range(95)]
    assert (R.population_fingerprint("L3", ids) == R.population_fingerprint("L3", list(reversed(ids))))
    assert R.population_fingerprint("L3", ids) != R.population_fingerprint("L6", ids)
def test_07_l3_has_unique_report_ownership():
    dec = resolve_l3_ownership("L3", R.L3_OWNED_REPORT)
    assert dec["allowed"] is True and dec["owner"] == "L3"
    assert dec["allowed"] is True
    assert R.L3_OWNED_REPORT != R.D1_OWNED_REPORT
def test_08_d1_ownership_remains_unchanged():
    assert ADJUDICATED_REPORT_OWNERS["q1_component_reward.json"] == "D1"
    assert canonical_report_owner(R.D1_OWNED_REPORT) == "D1"
    assert resolve_report_ownership(R.D1_OWNED_REPORT, "D1").allowed is True
    assert resolve_report_ownership(R.D1_OWNED_REPORT, "L3").allowed is False
    assert resolve_l3_ownership("D1", R.D1_OWNED_REPORT)["allowed"] is True
def test_09_ambiguous_l3_d1_authority_fails_closed():
    denied = resolve_l3_ownership("L3", R.D1_OWNED_REPORT)
    assert denied["allowed"] is False and "AMBIGUOUS_REPORT_MAPPING" in denied["reason"]
    back = resolve_l3_ownership("D1", R.L3_OWNED_REPORT)
    assert back["allowed"] is False and "AMBIGUOUS_REPORT_MAPPING" in back["reason"]
    meta = resolve_l3_ownership("L3", R.L3_OWNED_REPORT, {"question_id": "D1"})
    assert meta["allowed"] is False
    assert resolve_l3_ownership("L3", "")["allowed"] is False
def test_10_l3_active_registry_and_successor_use_independent_authority():
    import importlib.util as _ilu
    from research_engine.registry.learning_adaptation_adjudication import (
        FORBIDDEN_L3,
    )
    from research_engine.registry.research_question_registry import REGISTRY_BY_ID
    from research_engine.control_plane.stage4_registry_successor import REGISTRY_V2_BY_ID
    assert REGISTRY_BY_ID["L3"].report_filename == R.L3_OWNED_REPORT
    assert REGISTRY_BY_ID["L3"].runner_function == "run_l3"
    assert FORBIDDEN_L3 == (R.D1_OWNED_REPORT,)
    assert _ilu.find_spec("research_engine.experiments.architecture_assumption_validity") is not None
    assert REGISTRY_V2_BY_ID["L3"].report_filename == R.L3_OWNED_REPORT
    assert REGISTRY_V2_BY_ID["L3"].runner_function == "run_l3"
    # D1's artifact can never be loaded or validated for L3.
    assert resolve_report_ownership(R.D1_OWNED_REPORT, "L3").allowed is False
    assert resolve_l3_ownership("L3", R.D1_OWNED_REPORT)["allowed"] is False
    # L3's own identity is reserved and remains uniquely resolvable.
    assert resolve_l3_ownership("L3", R.L3_OWNED_REPORT)["allowed"] is True
    assert resolve_l3_ownership("L6", R.L3_OWNED_REPORT)["allowed"] is False
    # Gate-1 n=95 remains historical assurance and is not a CURRENT selector.
    assert R.HISTORICAL_GOVERNED_USABLE["L3"] == 95
    with pytest.raises(R.Stage4RepairError, match="CURRENT_EXACT_POPULATION_RETIRED:L3"):
        enforce_exact_population("L3", _recs(95))
    with pytest.raises(R.Stage4RepairError, match="CURRENT_EXACT_POPULATION_RETIRED:L3"):
        enforce_exact_population("L3", _recs(96))

def test_11_l6_governed_historical_runner_exists():
    ident = l6_runner_identity()
    assert ident["module"] == "research_engine.experiments.learning_cycle_validation"
    assert ident["function"] == "run_l6"
    module = __import__(ident["module"], fromlist=["run_l6"])
    assert callable(getattr(module, "run_l6"))
    report = module.run_l6(records=_recs(1852))
    assert report["question_id"] == "L6"
    assert report["dataset"]["sample_size"] == 1852
def test_12_l6_runner_has_no_live_or_s3_fallback():
    for path in (Path("research_engine/experiments/learning_cycle_validation.py"),
                 Path("research_engine/experiments/adaptation_evidence.py"),
                 Path("research_engine/control_plane/stage4_impl_population2.py"),
                 Path("research_engine/control_plane/stage4_impl_ownership_labels.py"),
                 Path("research_engine/control_plane/stage4_implementation_repairs.py")):
        src = path.read_text(encoding="utf-8")
        assert "get_default_source" not in src
        assert "read_dataset(" not in src
        assert "boto3" not in src
        assert "import s3" not in src.lower()
    assert l6_runner_identity()["live_s3_fallback"] == "FORBIDDEN"
def _arm_row(arm, **extra):
    """A governed row carrying a producer-issued pre-outcome arm block.

    The arm lives in the ``experiment_arm`` block, never in ``schema_version``:
    that field is the record-structure identity and reading an arm from it is
    the semantic collision ROOT-05 removes.
    """
    from core.shadow.observability import assign_experiment_arm
    block = assign_experiment_arm(
        canonical_opportunity_id=extra.pop(
            "canonical_opportunity_id", f"SYM{abs(hash(arm))%97}*1000*P"),
        trade_horizon=extra.pop("trade_horizon", "SCALP"),
        shadow_trade_id=extra.pop("shadow_trade_id", "ST-1"),
        decision_market_time_utc=extra.pop("decision_market_time_utc",
                                           1756000000),
    )
    block["experiment_arm"] = arm
    block["treatment_id"] = ("BASELINE_POLICY" if arm == "CONTROL"
                             else "ADAPTATION_CANDIDATE_POLICY")
    row = {"schema_version": "shadow_runtime_v1", "experiment_arm": block}
    row.update(extra)
    return row


def test_13_l7_unknown_labels_fail_closed():
    with pytest.raises(R.Stage4RepairError, match="L7_UNKNOWN_LABEL_FAIL_CLOSED"):
        govern_l7_label("SOMETHING_ELSE")
    with pytest.raises(R.Stage4RepairError, match="L7_UNKNOWN_LABEL_FAIL_CLOSED"):
        govern_l7_label(None)
    # No producer-issued arm block => fail closed, never inferred.
    with pytest.raises(R.Stage4RepairError, match="L7_ARM_BLOCK_MISSING_FAIL_CLOSED"):
        assign_l7_labels([{"schema_version": "shadow_trades_v1"}])
    # An arm in schema_version is no longer a valid assignment source.
    with pytest.raises(R.Stage4RepairError, match="L7_ARM_BLOCK_MISSING_FAIL_CLOSED"):
        assign_l7_labels([{"schema_version": "CANDIDATE_CONTROL_MIXED"}])
    # A record-structure identity presented as an arm is named explicitly.
    with pytest.raises(R.Stage4RepairError,
                       match="L7_SCHEMA_IDENTITY_PRESENTED_AS_ARM"):
        assign_l7_labels([{"schema_version": "shadow_runtime_v1",
                           "experiment_arm": {"experiment_arm":
                                              "shadow_trades_v1"}}])
    from research_engine.experiments.adaptation_evidence import run_l7
    with pytest.raises(R.Stage4RepairError):
        run_l7(records=[_arm_row("UNMAPPED")] * 14046)


def test_14_l7_labels_are_deterministic_and_versioned():
    from research_engine.experiments.adaptation_evidence import label_contract
    contract = label_contract()
    assert contract["contract_version"] == R.L7_LABEL_CONTRACT_VERSION
    assert contract["unknown_label_behaviour"] == "FAIL_CLOSED"
    assert contract["free_text_interpretation"] is False
    # The arm must come from the producer-issued pre-outcome arm block.  A
    # dataset identity such as ``shadow_trades_v1`` is NOT an arm, and a
    # version-suffixed or embedded arm is not a producer assignment either.
    rows_ = [_arm_row("CONTROL")] * 100 + [_arm_row("CANDIDATE")] * 100
    first = [r["governed_arm"] for r in assign_l7_labels(rows_)]
    second = [r["governed_arm"] for r in assign_l7_labels(rows_)]
    assert first == second
    assert first.count("CONTROL") == 100 and first.count("CANDIDATE") == 100
    assert all(r["label_provenance"]["contract"] == R.L7_LABEL_CONTRACT_VERSION
               for r in assign_l7_labels(rows_))
    for rejected in ("shadow_trades_CONTROL_v2", "shadow_trades_v1",
                     "control_v2", "CONTROL_CANDIDATE"):
        with pytest.raises(R.Stage4RepairError,
                           match="L7_(UNKNOWN_LABEL_FAIL_CLOSED|"
                                 "SCHEMA_IDENTITY_PRESENTED_AS_ARM)"):
            assign_l7_labels([{"schema_version": "shadow_runtime_v1",
                               "experiment_arm": {"experiment_arm": rejected}}])


def test_15_l7_runner_population_and_report_are_bound():
    from research_engine.experiments.adaptation_evidence import run_l7, validate_l7_report
    report = run_l7(records=[_arm_row("CANDIDATE")] * 14046)
    assert report["dataset"]["sample_size"] == 14046
    assert report["overall"]["arm_counts"] == {"CONTROL": 0, "CANDIDATE": 14046}
    valid, reason = validate_l7_report(report)
    assert valid, reason
    with pytest.raises(R.Stage4RepairError, match="POPULATION_MISMATCH"):
        run_l7(records=[_arm_row("CANDIDATE")] * 14047)

        enforce_exact_population("R1", rows_, roster[:-1] + ["OPP-999999"])
def test_06_population_fingerprint_is_deterministic():
    ids = [f"OPP-{i:04d}" for i in range(95)]
    assert (R.population_fingerprint("L3", ids) == R.population_fingerprint("L3", list(reversed(ids))))
    assert R.population_fingerprint("L3", ids) != R.population_fingerprint("L6", ids)
def test_16_g3_cannot_run_before_l6_dependency_is_satisfied():
    assert gate_g3_on_l6(None)["allowed"] is False
    blocked = {"scientific_state": "IMPLEMENTATION_BLOCKED", "epoch": "CURRENT"}
    assert gate_g3_on_l6(blocked)["allowed"] is False
    assert gate_g3_on_l6({"scientific_state": "COMPLETE", "epoch": "LEGACY"})["allowed"] is False
    assert gate_g3_on_l6({"status": "UNKNOWN", "epoch": "CURRENT"})["allowed"] is False
    assert gate_g3_on_l6({"scientific_state": "COMPLETE", "epoch": "CURRENT"})["allowed"] is True
def test_17_valid_l6_dependency_unlocks_g3():
    for state in ("COMPLETE", "NEGATIVE_RESULT", "NO_EFFECT", "INSUFFICIENT_DATA",
                  "WAITING_DATA", "HISTORICALLY_UNANSWERABLE"):
        assert gate_g3_on_l6({"scientific_state": state, "epoch": "CURRENT"})["allowed"] is True
    store = G.build_store()
    edge = next(e for e in store["dependency_edges"] if e["to"] == "GWI-G3-IMPL")
    assert edge["from"] == "GWI-L6-IMPL"
    assert edge["dependency_status"] == "UNRESOLVED"
def test_18_scientific_rerun_is_not_claimable_yet(rows):
    for qid in ("R1", "R2", "L3", "L6", "L7", "G2", "G3", "EX2"):
        assert rows[qid]["scientific_state"] == "IMPLEMENTATION_BLOCKED"
        assert rows[qid]["assurance_status"] == "VERIFIED"
    with pytest.raises(R.Stage4RepairError, match="GOVERNED_IDENTITY_LIST_REQUIRED"):
        require_governed_identities_for_science("R1", None)
def test_19_no_new_assurance_verification_was_manufactured():
    cert = json.loads(CERT.read_text(encoding="utf-8"))
    assert cert["assurance_counts"] == {"FAILED": 0, "INDETERMINATE": 0, "VERIFIED": 70}
    assert cert["implementation_gap_count"] == 8
    assert cert["dataset_schema_gap_count"] == 29
    assert cert["certification_fingerprint"] == A.EXPECTED_CERTIFICATION_FINGERPRINT
def test_20_finding_v2_supersedes_v1_only_on_a_copy(store):
    canonical = json.loads(CANON.read_text(encoding="utf-8"))
    working = copy.deepcopy(store)
    item = next(i for i in working["work_items"] if i["gap_work_item_id"] == "GWI-R1-IMPL")
    full = G.apply_resolution_evidence(working, "GWI-R1-IMPL", list(item["required_evidence"]))
    done = G.supersede_with_new_finding(full, "GWI-R1-IMPL", 2)
    got = next(i for i in done["work_items"] if i["gap_work_item_id"] == "GWI-R1-IMPL")
    assert got["status"] == "RESOLVED" and got["assured_finding_version"] == 2
    assert json.loads(CANON.read_text(encoding="utf-8")) == canonical

def test_21_v1_findings_remain_immutable(rows):
    finds = json.loads(FIND.read_text(encoding="utf-8"))
    assert finds["certification_fingerprint"] == A.EXPECTED_CERTIFICATION_FINGERPRINT
    assert finds["question_count"] == 70 == len(finds["findings"])
    assert finds["supersession"]["superseded_count"] == 0
    for f in finds["findings"]:
        assert f["finding_version"] == 1 and f["supersedes"] is None
        assert f["finding_status"] == "CURRENT"
    for qid in ("R1", "R2", "L3", "L6", "L7", "G2", "G3", "EX2"):
        assert rows[qid]["result_fingerprint"] == next(
            f["result_fingerprint"] for f in finds["findings"] if f["question_id"] == qid)
def test_22_gap_cannot_resolve_before_revalidation(store):
    working = copy.deepcopy(store)
    item = next(i for i in working["work_items"] if i["gap_work_item_id"] == "GWI-G2-IMPL")
    with pytest.raises(G.GapGovernanceError):
        G.supersede_with_new_finding(working, "GWI-G2-IMPL", 2)
    assert item["status"] == "OPEN" and item["resolution_evidence"] == []
def test_23_resolved_gap_must_carry_complete_resolution_evidence(store):
    working = copy.deepcopy(store)
    item = next(i for i in working["work_items"] if i["gap_work_item_id"] == "GWI-L6-IMPL")
    partial = list(item["required_evidence"])[:-1]
    staged = G.apply_resolution_evidence(working, "GWI-L6-IMPL", partial)
    got = next(i for i in staged["work_items"] if i["gap_work_item_id"] == "GWI-L6-IMPL")
    assert got["status"] != G.STATUS_VALIDATION_REQUIRED
    with pytest.raises(G.GapGovernanceError, match="RESOLVED_WITHOUT_EVIDENCE"):
        G.supersede_with_new_finding(staged, "GWI-L6-IMPL", 2)
    for work_item in store["work_items"]:
        if work_item["gap_type"] == G.GAP_TYPE_IMPL:
            assert work_item["resolution_evidence"] == []
            assert work_item["status"] in {G.STATUS_OPEN, G.STATUS_BLOCKED}
def test_24_repair_cannot_become_market_truth():
    for qid in ("R1", "R2", "L3", "L6", "L7", "G2", "G3", "EX2"):
        decision = A.decide(qid, "VERIFIED", "IMPLEMENTATION_BLOCKED")
        assert decision.scientific_truth_consumable is False
        assert decision.permitted_consumption == ("IMPLEMENTATION_REPAIR",)
        assert decision.admitted is True
def test_25_no_live_or_s3_read_in_repair_layer():
    for path in REPAIR_MODULES:
        src = path.read_text(encoding="utf-8")
        for token in ("get_default_source", "read_dataset(", "ingest_completed_shadow_trades",
                      "reconstruct_completed_shadow_trades", "boto3"):
            assert token not in src, f"{path} references {token}"
def test_26_no_data_schema_gap_was_mutated(store):
    gaps = json.loads(Path(
        "analysis/assurance/stage4_dataset_schema_gap_register_20260928.json"
    ).read_text(encoding="utf-8"))["gaps"]
    assert len(gaps) == 29
    data_items = [i for i in store["work_items"] if i["gap_type"] == G.GAP_TYPE_DATA]
    assert len(data_items) == 29
    assert store["certified_data_gap_relationships"] == 29
    for item in data_items:
        assert item["status"] == G.STATUS_OPEN
        assert item["resolution_evidence"] == []
        assert item["consumable_as_scientific_truth"] is False
        assert item["assured_finding_version"] == 1
def test_27_ex2_specialized_report_schema_and_digest_still_valid():
    from research_engine.experiments.exit_policy_governed import validate_governed_exit_report
    report = json.loads(Path(
        "analysis/reports/ex2_profit_retention.json").read_text(encoding="utf-8"))
    valid, reason = validate_governed_exit_report(report, "EX2")
    assert valid, reason
    assert report["report_schema_version"] == "hd09_governed_exit_research_v1"
    assert canonical_report_owner("ex2_profit_retention.json") == "EX2"
    assert report["provenance"]["report_digest"]
def test_28_repair_scope_is_the_eight_governed_work_items(store):
    impl = [i for i in store["work_items"] if i["gap_type"] == G.GAP_TYPE_IMPL]
    assert {i["gap_work_item_id"] for i in impl} == {
        "GWI-R1-IMPL", "GWI-R2-IMPL", "GWI-L3-IMPL", "GWI-L6-IMPL",
        "GWI-L7-IMPL", "GWI-G2-IMPL", "GWI-G3-IMPL", "GWI-EX2-IMPL"}
    assert set(R.GOVERNED_USABLE) == {"R1", "R2", "L3", "L6", "L7", "G2", "G3", "EX2"}
    assert store["q71_started"] is False
    assert store["live_or_s3_reads"] is False

