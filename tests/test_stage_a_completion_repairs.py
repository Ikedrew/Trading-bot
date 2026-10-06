from __future__ import annotations

from functools import wraps
from types import SimpleNamespace

import pytest

from core.production_data_contract import current_schema
from research_engine.control_plane.governed_meta_risk_evidence import (
    build_governed_lineage_population,
)
from research_engine.control_plane.stage4_implementation_repairs import (
    Stage4RepairError,
    build_current_population_authority,
)
from research_engine.experiments import out_of_sample_validation as e5_module
from research_engine.experiments import promotion_impact as p1_module
from research_engine.experiments.dataset_suitability import run_g1
from research_engine.experiments.learning_cycle_validation import (
    run_l6,
    validate_l6_report,
)
from research_engine.experiments.lineage_coverage import run_g2
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.v10.continuous.canonical_question_cycle import _runner_kwargs
from research_engine.v10.continuous.evaluation_identity import build_evaluation_identity
from research_engine.v10.continuous.question_cycle_state import QuestionCycleStore
from research_engine.v10.continuous.research_loop import run_continuous_research_cycle
from research_engine.v10.continuous.research_projection import ResearchProjectionStore


def _shadow(index: int) -> dict:
    return {
        "schema_version": current_schema("shadow_trades"),
        "data_epoch": "CURRENT",
        "identity": {
            "canonical_opportunity_id": f"opp-{index}",
            "entity_id": f"entity-{index}",
            "shadow_trade_id": f"nshadow_{index:016x}",
        },
        "decision_snapshot": {
            "timestamp_decision_utc": f"2026-01-{1 + index // 24:02d}T{index % 24:02d}:00:00Z",
            "pattern": "GOOD" if index % 2 == 0 else "BAD",
            "strategy": "FALSE_BREAK",
            "trade_horizon": "INTRADAY",
        },
        "simulated_outcome": {"pnl_r_multiple": 1.0 if index % 2 == 0 else -0.5},
    }


@pytest.mark.parametrize(
    ("question_id", "runner", "module"),
    [
        ("E5", e5_module.run_out_of_sample_validation, e5_module),
        ("P1", p1_module.run_promotion_impact, p1_module),
    ],
)
def test_canonical_e5_p1_inject_persist_false_and_never_write(
    monkeypatch, question_id, runner, module,
):
    rows = [_shadow(index) for index in range(160)]
    context = SimpleNamespace(
        datasets={"shadow_trades": rows}, runner_artifacts={}, reader=None)
    kwargs = _runner_kwargs(runner, SimpleNamespace(id=question_id), rows, context)
    assert kwargs["persist"] is False
    monkeypatch.setattr(
        module, "persist_report",
        lambda *_args, **_kwargs: pytest.fail("canonical runner attempted report persistence"))
    monkeypatch.setattr(
        module, "update_knowledge_map",
        lambda *_args, **_kwargs: pytest.fail("canonical runner attempted knowledge persistence"))
    report = runner(**kwargs)
    assert report["question_id"] == question_id


@pytest.mark.parametrize(
    ("runner", "module"),
    [
        (e5_module.run_out_of_sample_validation, e5_module),
        (p1_module.run_promotion_impact, p1_module),
    ],
)
def test_e5_p1_explicit_standalone_persistence_remains_available(
    monkeypatch, runner, module,
):
    calls = []
    monkeypatch.setattr(module, "persist_report", lambda *args: calls.append(("report", args)))
    monkeypatch.setattr(module, "update_knowledge_map", lambda *args: calls.append(("map", args)))
    runner([_shadow(index) for index in range(160)], persist=True)
    assert [kind for kind, _args in calls] == ["report", "map"]


def test_l6_current_population_authority_replaces_historical_fixed_count_only_for_current_cycle():
    rows = [{"canonical_opportunity_id": f"opp-{index}"} for index in range(2753)]
    with pytest.raises(Stage4RepairError, match=r"got=2753:expected=1852"):
        run_l6(records=rows)

    authority = build_current_population_authority(
        "L6", rows, snapshot_id="ISNAP-CURRENT",
        evaluation_identity_digest="EVAL-L6-CURRENT")
    report = run_l6(records=rows, population_authority=authority)
    assert report["dataset"]["sample_size"] == 2753
    assert report["provenance"]["population_authority"]["kind"] == "CURRENT_SNAPSHOT"
    assert validate_l6_report(report)[0]

    with pytest.raises(Stage4RepairError, match="POPULATION_MISMATCH"):
        run_l6(records=rows[:-1], population_authority=authority)
    changed = [*rows]
    changed[-1] = {"canonical_opportunity_id": "different"}
    with pytest.raises(Stage4RepairError, match="POPULATION_MEMBERSHIP_MISMATCH"):
        run_l6(records=changed, population_authority=authority)


def test_wrapped_g1_preserves_signature_for_canonical_dataset_injection():
    captured = {}

    @wraps(run_g1)
    def wrapped(*args, **kwargs):
        captured.update(kwargs)
        return {"question_id": "G1"}

    datasets = {"shadow_trades": [_shadow(0)], "assessments": []}
    context = SimpleNamespace(datasets=datasets, runner_artifacts={}, reader=None)
    kwargs = _runner_kwargs(wrapped, SimpleNamespace(id="G1"), [], context)
    assert kwargs["datasets"] is datasets
    wrapped(**kwargs)
    assert captured["datasets"] is datasets


@pytest.mark.parametrize("question_id", ["E5", "P1", "L6", "G2"])
def test_repaired_integrations_are_part_of_governed_evaluator_identity(question_id):
    from research_engine.registry.definition_validator import build_definitions_from_registry
    from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID

    question = REGISTRY_BY_ID[question_id]
    definition = build_definitions_from_registry(REGISTRY)[question_id]
    identity = build_evaluation_identity(
        question, definition, registry_version="research_question_registry_v1")
    assert identity["governance_contract_versions"]


def _decision(index: int) -> dict:
    return {
        "schema_version": current_schema("decision_trace"),
        "entity_id": f"entity-{index}",
        "canonical_opportunity_id": f"opp-{index}",
        "symbol": "EURUSD",
    }


def _outcome(index: int) -> dict:
    return {
        "schema_version": current_schema("shadow_runtime"),
        "entity_id": f"entity-{index}",
        "canonical_opportunity_id": f"opp-{index}",
        "symbol": "EURUSD",
        "shadow_type": "PRIMARY_HORIZON_SIMULATION",
        "r_multiple": 1.0,
        "strategy": "FALSE_BREAK",
        "trade_horizon": "INTRADAY",
        "h4_regime": "TRENDING",
        "shadow_trade_id": f"nshadow_{index:016x}",
    }


def test_g2_governed_path_is_exhaustive_without_second_full_payload_snapshot(monkeypatch):
    decisions = [_decision(index) for index in range(120)]
    outcomes = [_outcome(index) for index in range(120)]
    predecessor = run_g2(
        datasets={"decision_trace": decisions, "shadow_runtime": outcomes},
        as_of_utc="2026-09-25T00:00:00+00:00")
    population = build_governed_lineage_population(
        decisions, outcomes, trusted_immutable_inputs=True)
    assert population.decision_records[0] is decisions[0]
    assert population.outcome_records[0] is outcomes[0]

    from research_engine.experiments import lineage_coverage as module
    original = module.freeze_current_snapshot
    observed = {}

    def recording_freeze(datasets, **kwargs):
        observed["retain_record_payloads"] = kwargs.get("retain_record_payloads")
        snapshot = original(datasets, **kwargs)
        observed["payloads"] = snapshot._payloads
        observed["borrowed_decisions"] = snapshot._borrowed_datasets["decision_trace"]
        return snapshot

    monkeypatch.setattr(module, "freeze_current_snapshot", recording_freeze)
    report = run_g2(
        governed_lineage_population=population,
        as_of_utc="2026-09-25T00:00:00+00:00")
    assert observed["retain_record_payloads"] is False
    assert observed["payloads"] == ()
    assert observed["borrowed_decisions"] is population.decision_records
    assert report["overall"]["denominator"] == 120
    assert len(report["overall"]["classifications"]) == 120
    assert report["overall"]["valid"] == 120
    assert report["overall"]["coverage"] == 1.0
    assert report["overall"] == predecessor["overall"]
    assert report["dataset"] == predecessor["dataset"]
    assert report["fingerprint"] == predecessor["fingerprint"]


def _publication_fixture(tmp_path):
    state_root = tmp_path / "continuous"
    question_state = tmp_path / "questions"
    fresh_questions = {
        question_id: {
            "question_id": question_id,
            "reason_code": "GOVERNED_REASON",
            "reason": f"reason-{question_id}",
            "reason_details": {"question_id": question_id},
            "result": {
                "question_id": question_id,
                "result_id": f"FRESH-{question_id}",
                "reason_code": "GOVERNED_REASON",
                "reason": f"reason-{question_id}",
                "reason_details": {"question_id": question_id},
            },
        }
        for question_id in BASELINE_QUESTION_IDS
    }
    QuestionCycleStore(question_state).save_projection({
        "cycle_schema": "canonical_question_cycle_v1",
        "cycle_id": "QCYCLE-FRESH",
        "questions": fresh_questions,
    })
    predecessor_rows = [
        {
            "question_id": question_id,
            "result": {"question_id": question_id, "result_id": f"OLD-{question_id}"},
        }
        for question_id in sorted(BASELINE_QUESTION_IDS)
    ]
    predecessor = {
        "projection_schema": "unified_research_projection_v1",
        "projection_version": "RPROJ-PREDECESSOR",
        "continuous_cycle_id": "CRCYCLE-PREDECESSOR",
        "canonical_question_cycle_id": "QCYCLE-PREDECESSOR",
        "data_frontier": {
            "snapshot_id": "ISNAP-CURRENT",
            "last_successful_research_cycle": "CRCYCLE-PREDECESSOR",
        },
        "canonical_questions": predecessor_rows,
        "generated_questions": [{"generated_question_id": "Q71-KEEP", "status": "ACTIVE"}],
        "findings": [], "hypotheses": [], "candidates": [],
        "investigations_and_work_queues": {}, "what_changed": {},
        "predecessor_projection_version": None,
    }
    ResearchProjectionStore(state_root / "projection").save(predecessor)
    frontier = SimpleNamespace(
        status="NO_NEW_GOVERNED_EVIDENCE", verification_status="NOT_REQUIRED",
        snapshot_id="ISNAP-CURRENT", fingerprint="FP", investigation_epoch="E1",
        frontier_id="FR1", frontier_start="2026-10-01", frontier_end="2026-10-02",
        predecessor_snapshot_id="ISNAP-CURRENT", changed_datasets=(),
        stale_datasets=(), missing_optional_datasets=())
    question = SimpleNamespace(
        cycle_id="QCYCLE-FRESH", total_questions=70, cycle_status="COMPLETED",
        completed_at="2026-10-02T00:00:00Z", evaluated_count=0,
        retained_count=70, changed_question_ids=())
    return state_root, question_state, predecessor, frontier, question


def test_fresh_canonical_cycle_replaces_predecessor_unified_projection(monkeypatch, tmp_path):
    state_root, question_state, predecessor, frontier, question = _publication_fixture(tmp_path)
    from research_engine.v10.continuous import research_loop as module
    monkeypatch.setattr(module, "_stale_evaluation_questions", lambda *_args, **_kwargs: {})
    calls = []
    result = run_continuous_research_cycle(
        state_root=state_root,
        frontier_runner=lambda **_: frontier,
        question_runner=lambda _frontier, **kwargs: (
            calls.append(kwargs.get("evaluation_only_question_ids")) or question),
        bridge_runner=lambda *_args, **_kwargs: pytest.fail("equivalent publication must not bridge"),
        q71_runner=lambda **_kwargs: pytest.fail("equivalent publication must not run Q71"),
        question_kwargs={"state_directory": question_state},
    )
    assert calls == [()]
    assert result.cycle_outcome == "COMPLETED"
    published = ResearchProjectionStore(state_root / "projection").load_latest()
    assert published["projection_version"] != predecessor["projection_version"]
    assert published["canonical_question_cycle_id"] == "QCYCLE-FRESH"
    assert published["data_frontier"]["snapshot_id"] == "ISNAP-CURRENT"
    rows = published["canonical_questions"]
    assert [row["question_id"] for row in rows] == list(BASELINE_QUESTION_IDS)
    assert len({row["question_id"] for row in rows}) == 70
    assert [row["result"]["result_id"] for row in rows] == [
        f"FRESH-{question_id}" for question_id in BASELINE_QUESTION_IDS]
    assert all(row["result"]["reason_code"] == "GOVERNED_REASON" for row in rows)
    assert published["generated_questions"] == [
        {"generated_question_id": "Q71-KEEP", "status": "ACTIVE"}]


def test_failed_canonical_publication_does_not_move_unified_current(monkeypatch, tmp_path):
    state_root, question_state, predecessor, frontier, _question = _publication_fixture(tmp_path)
    from research_engine.v10.continuous import research_loop as module
    monkeypatch.setattr(module, "_stale_evaluation_questions", lambda *_args, **_kwargs: {})
    result = run_continuous_research_cycle(
        state_root=state_root,
        frontier_runner=lambda **_: frontier,
        question_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("INTERRUPTED_CANONICAL_CYCLE")),
        question_kwargs={"state_directory": question_state},
    )
    assert result.cycle_outcome == "FAILED"
    assert result.failure_stage == "QUESTIONS"
    assert ResearchProjectionStore(state_root / "projection").load_latest() == predecessor
