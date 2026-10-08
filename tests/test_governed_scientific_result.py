"""Repair Block 1 — real evaluator scientific semantics.

These regressions prove that REAL canonical evaluators can drive the governed
scientific pipeline end to end: a persisted evidence fixture is read by the real
evaluator, the evaluator declares its own governed scientific result, and the
canonical cycle plus the scientific-state bridge turn that result into findings,
falsifiable hypotheses and — only where the evaluator legitimately identifies a
governed intervention — a governed candidate.

No test in this module injects ``scientifically_meaningful``,
``falsification_criteria``, ``validation_criteria`` or ``governed_policy_id``:
those values originate in the evaluator under test.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_engine.control_plane.exit_bar_path import LifecyclePathSource
from research_engine.control_plane.governed_exit_evidence import (
    build_governed_exit_evidence,
)
from research_engine.control_plane.stage4_dataset_snapshot import fingerprint
from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.experiments import exit_policy_governed as governed_exit
from research_engine.experiments import exit_management, expected_value
from research_engine.experiments.governed_scientific_result import (
    AMBIGUOUS_GOVERNED_INTERVENTION,
    DESCRIPTIVE_ONLY,
    GOVERNANCE_STATE_ONLY,
    INSUFFICIENT_GOVERNED_EVIDENCE,
    NO_INTERVENTION_MAPPING,
    SCIENTIFIC_RESULT_REPORT_KEY,
    SCIENTIFIC_RESULT_SCHEMA,
    CandidateDesignContract,
    FalsificationContract,
    GovernedScientificResult,
    ScientificResultError,
    ScientificSignal,
    governed_scientific_metrics,
    governed_scientific_result,
    meaningful,
    not_meaningful,
    result_from_dict,
    result_to_metrics,
)
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.registry.exit_policy_adjudication import CANDIDATE_POLICIES_V1
from research_engine.v10.continuous.canonical_question_cycle import (
    _execute_runner,
    run_canonical_question_cycle,
)
from research_engine.v10.continuous.question_cycle_state import QuestionCycleStore
from research_engine.v10.continuous.research_lab import build_lab_view
from research_engine.v10.continuous.research_projection import (
    build_unified_research_projection,
)
from research_engine.v10.continuous.scientific_state_bridge import (
    run_scientific_state_bridge,
)
from research_engine.v10.continuous.scientific_state_store import ScientificStateStore
from research_engine.v10.investigation_snapshot import BOUND_DATASETS
from research_engine.v10.optimisation.optimisation_registry import OptimisationRegistry

from tests.test_canonical_question_cycle import MemoryS3, _freeze, _key, _jsonl, _row
from tests.test_exit_candidate_replay import _repeat, _reproduced_source
from tests.test_scientific_state_bridge import (
    POLICY,
    VALIDATION_CRITERIA,
    _candidate_metrics,
    _cycle,
    _result,
    _run as _bridge_run,
)

GOVERNED_POLICY_ID = "REDUCED_TP_0_50R_V1"
GOVERNED_POLICY = next(
    item for item in CANDIDATE_POLICIES_V1 if item["policy_id"] == GOVERNED_POLICY_ID)
HD09_FIXTURE_COUNT = 200


@pytest.fixture(autouse=True)
def _no_repository_report_writes(monkeypatch, tmp_path):
    """Real evaluators persist their reports; tests must not dirty the repo."""
    from research_engine.experiments import (
        experiment_base,
        r1_risk_layer_effectiveness,
        r2_guard_attribution,
        risk_simulation_governed,
        score_calibration,
    )

    reports_dir = tmp_path / "analysis-reports"

    def redirected(report, filename):
        path = reports_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, default=str), encoding="utf-8")
        return path

    monkeypatch.setattr(experiment_base, "persist_report", redirected)
    monkeypatch.setattr(experiment_base, "_REPORTS_DIR", reports_dir)
    monkeypatch.setattr(risk_simulation_governed, "persist_report", redirected)
    monkeypatch.setattr(risk_simulation_governed, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(
        r1_risk_layer_effectiveness, "_REPORTS_DIR", reports_dir, raising=False)
    monkeypatch.setattr(
        r2_guard_attribution, "_REPORTS_DIR", reports_dir, raising=False)
    monkeypatch.setattr(
        score_calibration, "_OUTPUT_DIR", reports_dir, raising=False)


# ── Part A: the evaluator-side contract itself ────────────────────────────────


def _signal(**overrides):
    values = dict(
        signal_type="TEST_SIGNAL",
        classification="SUPPORTED",
        primary_metric="mean_r",
        estimate=0.5,
        significance_method="test_method",
        significance_value=0.01,
        sample_size=100,
        population={"population_id": "P", "question_id": "E1"},
        null_definition="the mean is zero",
        limitations=("test limitation",),
        effect_direction="POSITIVE",
    )
    values.update(overrides)
    return ScientificSignal(**values)


def _design(**overrides):
    values = dict(
        governed_policy_id=GOVERNED_POLICY_ID,
        treatment_component="TARGET_GEOMETRY",
        treatment_parameters=dict(GOVERNED_POLICY),
        success_conditions={"weighted_effect_estimate": {"gt": 0.0}},
        failure_conditions={"weighted_effect_estimate": {"lte": 0.0}},
        validation_criteria={
            "required_sample": 200,
            "primary_metrics": ["weighted_effect_estimate"],
            "success_conditions": {"weighted_effect_estimate": {"gt": 0.0}},
            "failure_conditions": {"weighted_effect_estimate": {"lte": 0.0}},
        },
        applicable_population={"population_id": "P", "question_id": "E1"},
        intervention_rationale="the evaluator's own governed decision",
    )
    values.update(overrides)
    return CandidateDesignContract(**values)


def test_contract_declares_only_what_the_evaluator_supports():
    result = meaningful(
        "E1",
        signal=_signal(),
        falsification=FalsificationContract(criteria=("mean_r <= 0 on replication",)),
        no_intervention_reason=NO_INTERVENTION_MAPPING,
    )
    block = result.to_dict()
    assert block["schema"] == SCIENTIFIC_RESULT_SCHEMA
    assert block["scientific_signal"]["primary_metric"] == "mean_r"
    assert block["candidate_design"] is None
    assert result_from_dict(block).to_dict() == block


def test_contract_rejects_a_claim_without_a_signal():
    with pytest.raises(ScientificResultError, match="SCIENTIFIC_RESULT_SIGNAL_REQUIRED"):
        GovernedScientificResult(
            question_id="E1", scientifically_meaningful=True,
            no_intervention_reason=NO_INTERVENTION_MAPPING,
        ).validate()


def test_contract_requires_an_intervention_mapping_when_meaningful():
    with pytest.raises(
        ScientificResultError, match="SCIENTIFIC_RESULT_INTERVENTION_MAPPING_REQUIRED"
    ):
        meaningful("E1", signal=_signal()).validate()


def test_contract_rejects_reasons_outside_the_closed_vocabulary():
    with pytest.raises(
        ScientificResultError, match="SCIENTIFIC_RESULT_REASON_NOT_IN_VOCABULARY"
    ):
        not_meaningful("E1", "BECAUSE_I_SAID_SO").validate()


def test_contract_rejects_contradictory_declarations():
    with pytest.raises(ScientificResultError, match="SCIENTIFIC_RESULT_CONTRADICTORY_REASON"):
        GovernedScientificResult(
            question_id="E1", scientifically_meaningful=True, signal=_signal(),
            not_meaningful_reason=DESCRIPTIVE_ONLY,
            no_intervention_reason=NO_INTERVENTION_MAPPING,
        ).validate()
    with pytest.raises(
        ScientificResultError, match="SCIENTIFIC_RESULT_CONTRADICTORY_NOT_MEANINGFUL"
    ):
        GovernedScientificResult(
            question_id="E1", scientifically_meaningful=False,
            not_meaningful_reason=DESCRIPTIVE_ONLY, signal=_signal(),
        ).validate()


def test_contract_rejects_an_inverted_confidence_interval():
    with pytest.raises(
        ScientificResultError, match="SCIENTIFIC_RESULT_INVERTED_CONFIDENCE_INTERVAL"
    ):
        meaningful(
            "E1", signal=_signal(confidence_interval=(0.9, 0.1)),
            no_intervention_reason=NO_INTERVENTION_MAPPING,
        ).validate()



def test_contract_rejects_ungoverned_policy_and_parameter_drift():
    with pytest.raises(ScientificResultError, match="SCIENTIFIC_RESULT_UNGOVERNED_POLICY"):
        meaningful(
            "EX1", signal=_signal(),
            falsification=FalsificationContract(criteria=("x",)),
            candidate_design=_design(governed_policy_id="INVENTED_POLICY_V1"),
        ).validate()
    with pytest.raises(
        ScientificResultError, match="SCIENTIFIC_RESULT_TREATMENT_PARAMETERS_MISMATCH"
    ):
        meaningful(
            "EX1", signal=_signal(),
            falsification=FalsificationContract(criteria=("x",)),
            candidate_design=_design(treatment_parameters={"policy_id": GOVERNED_POLICY_ID}),
        ).validate()
    with pytest.raises(
        ScientificResultError, match="SCIENTIFIC_RESULT_TREATMENT_COMPONENT_MISMATCH"
    ):
        meaningful(
            "EX1", signal=_signal(),
            falsification=FalsificationContract(criteria=("x",)),
            candidate_design=_design(treatment_component="STOP_GEOMETRY"),
        ).validate()


def test_contract_rejects_incomplete_validation_criteria():
    with pytest.raises(ScientificResultError, match="INVALID_POSITIVE_INT"):
        meaningful(
            "EX1", signal=_signal(),
            falsification=FalsificationContract(criteria=("x",)),
            candidate_design=_design(validation_criteria={
                "required_sample": 0,
                "primary_metrics": ["weighted_effect_estimate"],
                "success_conditions": {"a": {"gt": 0}},
                "failure_conditions": {"a": {"lte": 0}},
            }),
        ).validate()


def test_contract_rejects_a_candidate_without_falsification_semantics():
    with pytest.raises(
        ScientificResultError, match="SCIENTIFIC_RESULT_CANDIDATE_REQUIRES_FALSIFICATION"
    ):
        meaningful("EX1", signal=_signal(), candidate_design=_design()).validate()


def test_contract_requires_candidate_population_to_refine_the_signal_population():
    with pytest.raises(
        ScientificResultError, match="SCIENTIFIC_RESULT_CANDIDATE_POPULATION_MISMATCH"
    ):
        meaningful(
            "EX1", signal=_signal(),
            falsification=FalsificationContract(criteria=("x",)),
            candidate_design=_design(applicable_population={"population_id": "OTHER"}),
        ).validate()


def test_transport_hook_is_empty_without_a_declaration_and_strict_with_one():
    assert governed_scientific_metrics({"status": "COMPLETE"}) == {}
    assert governed_scientific_result({"status": "COMPLETE"}) is None
    with pytest.raises(ScientificResultError, match="SCIENTIFIC_RESULT_MALFORMED"):
        governed_scientific_metrics({SCIENTIFIC_RESULT_REPORT_KEY: "nonsense"})
    with pytest.raises(ScientificResultError, match="SCIENTIFIC_RESULT_SCHEMA_MISMATCH"):
        governed_scientific_metrics({SCIENTIFIC_RESULT_REPORT_KEY: {"schema": "other"}})


def test_transport_projection_carries_only_evaluator_supplied_meaning():
    result = meaningful(
        "EX1",
        signal=_signal(population={"population_id": "P", "question_id": "EX1"}),
        falsification=FalsificationContract(criteria=("x",)),
        candidate_design=_design(
            applicable_population={
                "population_id": "P", "question_id": "EX1",
                "governed_policy_id": GOVERNED_POLICY_ID,
            }),
    )
    metrics = result_to_metrics(result)
    assert metrics["scientifically_meaningful"] is True
    assert metrics["governed_policy_id"] == GOVERNED_POLICY_ID
    assert metrics["governed_policy_parameters"] == GOVERNED_POLICY
    assert metrics["falsification_criteria"] == ["x"]
    declared = result_to_metrics(not_meaningful("E1", DESCRIPTIVE_ONLY))
    assert declared["scientifically_meaningful"] is False
    assert declared["scientific_not_meaningful_reason"] == DESCRIPTIVE_ONLY



# ── Part B: persisted evidence fixtures and cycle helpers ─────────────────────

# Benign bars: no stop, no target, and never high enough to arm the 0.25R
# trailing policy.  The final bar touches only the 0.50R reduced target.
BENIGN_BAR = (100.0, 100.4, 99.6, 100.2)


def _hd09_bars(index: int):
    final = (
        round(100.0 + (index % 4) * 0.05, 4),
        round(101.2 + (index % 5) * 0.1, 4),
        99.0,
        round(99.6 + (index % 4) * 0.05, 4),
    )
    return _repeat(BENIGN_BAR, 8) + (final,)


def _hd09_lifecycle(index: int) -> LifecyclePathSource:
    """One fully reproduced governed lifecycle with canonical lineage facts."""
    source = _reproduced_source(index=index, bars=_hd09_bars(index))
    trade_id = f"nshadow_{index:016x}"
    opened = dict(source.open_event)
    closed = dict(source.close_event)
    opened["shadow_trade_id"] = trade_id
    closed["shadow_trade_id"] = trade_id
    opened["identity"] = {
        "entity_id": "EURUSD_1", "cycle_id": index,
        "trade_horizon": "SCALP", "evaluated_horizon": "SCALP",
        "shadow_type": "HORIZON_ALTERNATIVE",
    }
    opened["live_facts"] = {
        "pattern": "HAMMER", "strategy": "MEAN_REVERSION", "score": 0.8,
        "regime": "TRENDING", "h4_regime": "TRENDING",
        "market_phase": "IMPULSE", "h1_bias": "BULLISH",
    }
    return LifecyclePathSource(opened, closed, source.candle_events)


def _persist_hd09_fixture(root: Path) -> tuple[Path, Path]:
    """Persist the HD09 source evidence exactly as the production loader reads it."""
    shadow_dir = root / "shadow_runtime_v1"
    event_dir = root / "events_v1"
    shadow_dir.mkdir(parents=True, exist_ok=True)
    event_dir.mkdir(parents=True, exist_ok=True)
    opens, closes, candles = [], [], []
    for index in range(HD09_FIXTURE_COUNT):
        source = _hd09_lifecycle(index)
        opens.append(source.open_event)
        closes.append(source.close_event)
        candles.extend(source.candle_events)
    (shadow_dir / "part-000.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in opens + closes),
        encoding="utf-8")
    (event_dir / "part-000.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in candles),
        encoding="utf-8")
    return shadow_dir, event_dir


def _governed_exit_evidence(root: Path):
    """Rebuild the production governed exit artifact from the persisted fixture."""
    shadow_dir, event_dir = _persist_hd09_fixture(root)
    return build_governed_exit_evidence(
        list(governed_exit._records(shadow_dir)),
        list(governed_exit._records(event_dir)),
    )



def _shadow_lifecycles(r_multiples) -> list[dict]:
    """Persisted canonical shadow_runtime_v1 OPEN/CLOSE events for E1."""
    from core.production_data_contract import current_schema

    rows: list[dict] = []
    for index, r_multiple in enumerate(r_multiples):
        common = {
            "schema_version": current_schema("shadow_runtime"),
            "canonical_opportunity_id": f"nopp_{index}",
            "observation_id": f"nobs_{index}",
            "shadow_trade_id": f"nshadow_{index:016x}",
            "symbol": "EURUSD",
            "horizon": "SCALP",
            "plan_id": f"nplan_{index}",
        }
        rows.append({
            **common,
            "event_type": "OPEN",
            "identity": {
                "entity_id": "EURUSD_1", "cycle_id": index,
                "trade_horizon": "SCALP", "evaluated_horizon": "SCALP",
                "shadow_type": "HORIZON_ALTERNATIVE",
            },
            "live_facts": {
                "pattern": "HAMMER", "strategy": "MEAN_REVERSION", "score": 0.8,
                "regime": "TRENDING", "h4_regime": "TRENDING",
                "market_phase": "IMPULSE", "h1_bias": "BULLISH",
            },
            "construction": {
                "direction": "BUY", "entry_price": 1.10, "stop_loss": 1.09,
                "take_profit": 1.12, "risk_distance": 0.01, "intended_rr": 2.0,
            },
            "entry_market_time_utc_epoch_s": 1777700000 + index * 3600,
            "entry_market_time_utc_iso8601": "2026-04-01T00:00:00Z",
        })
        rows.append({
            **common,
            "event_type": "CLOSE",
            "exit_market_time_utc_epoch_s": 1777701500 + index * 3600,
            "exit_market_time_utc_iso8601": "2026-04-01T00:25:00Z",
            "exit_price": 1.11,
            "exit_reason": "take_profit" if r_multiple > 0 else "stop_loss",
            "bars_held": 5,
            "outcome": {
                "pnl_r_multiple": float(r_multiple), "mfe_r": 1.0, "mae_r": -1.0,
                "risk_distance": 0.01, "intended_rr": 2.0,
            },
        })
    return rows


def _objects(shadow_rows: list[dict]) -> dict[str, str]:
    result = {}
    for dataset in BOUND_DATASETS:
        rows = shadow_rows if dataset == "shadow_runtime" else [_row(dataset)]
        result[_key(dataset)] = _jsonl(*rows)
    return result


def _run_cycle(tmp_path: Path, objects, *, name: str = "cycles", **kwargs):
    fake = MemoryS3(objects)
    snapshot, manifests = _freeze(tmp_path, fake)
    cycle = run_canonical_question_cycle(
        {
            "snapshot_id": snapshot.snapshot_id,
            "fingerprint": snapshot.snapshot_fingerprint,
            "investigation_epoch": snapshot.evidence_epoch,
            "changed_datasets": list(BOUND_DATASETS),
        },
        source=S3ResearchDataSource(bucket="question-cycle-test", client=fake),
        manifest_directory=manifests,
        state_directory=tmp_path / name,
        **kwargs,
    )
    return snapshot, cycle, QuestionCycleStore(tmp_path / name)


def _run_bridge(tmp_path: Path, cycle, qstore: QuestionCycleStore, **kwargs):
    registry = kwargs.pop("registry", None) or OptimisationRegistry(
        str(tmp_path / "optimisation"))
    bridge = run_scientific_state_bridge(
        cycle,
        question_store=qstore,
        scientific_store=ScientificStateStore(tmp_path / "scientific"),
        optimisation_registry=registry,
        treatment_memory_path=tmp_path / "memory.json",
        policy_catalog=CANDIDATE_POLICIES_V1,
        **kwargs,
    )
    return bridge, registry


def _result_for(qstore: QuestionCycleStore, cycle, question_id: str) -> dict:
    projection = qstore.load_projection(cycle.cycle_id)
    return projection["questions"][question_id]["result"]


# ── Part C: real evaluators declare their own governed scientific result ──────


def test_real_ex1_evaluator_emits_a_governed_intervention_from_persisted_evidence(tmp_path):
    """The real HD09 evaluator, not a fixture, names the governed policy."""
    evidence = _governed_exit_evidence(tmp_path)
    assert evidence.missing_evidence == ()
    assert evidence.completed_lifecycles == HD09_FIXTURE_COUNT

    report = governed_exit.run_ex1(governed_exit_evidence=evidence)
    assert report["status"] == "COMPLETE"

    declared = governed_scientific_result(report)
    assert declared is not None and declared.scientifically_meaningful is True
    assert declared.signal.sample_size == HD09_FIXTURE_COUNT
    assert declared.signal.significance_method.startswith("opportunity_clustered")
    design = declared.candidate_design
    assert design is not None
    assert design.governed_policy_id == GOVERNED_POLICY_ID
    assert design.treatment_component == "TARGET_GEOMETRY"
    assert dict(design.treatment_parameters) == GOVERNED_POLICY
    assert design.validation_criteria["required_sample"] == 200
    assert declared.falsification.criteria

    metrics = governed_scientific_metrics(report)
    assert metrics["scientifically_meaningful"] is True
    assert metrics["governed_policy_id"] == GOVERNED_POLICY_ID
    assert metrics["population"]["population_id"] == "GOVERNED_EXIT_POLICY_COUNTERFACTUAL"


def test_real_ex1_evaluator_declines_an_ambiguous_governed_intervention(monkeypatch):
    """Nine equally supported policies must not become one invented candidate."""
    from tests.test_exit_policy_governed import _populations

    monkeypatch.setattr(governed_exit, "_validate_foundations", lambda *args: None)
    report = governed_exit.analyse_ex1(*_populations(candidate_r=0.0, baseline_r=-0.5))
    declared = governed_scientific_result(report)
    assert declared.scientifically_meaningful is True
    assert declared.candidate_design is None
    assert declared.no_intervention_reason == AMBIGUOUS_GOVERNED_INTERVENTION


def test_real_ex1_evaluator_fails_closed_without_the_m5_candle_path():
    """The production snapshot's observation gap is declared, not smoothed over."""
    evidence = build_governed_exit_evidence([], ())
    assert evidence.missing_evidence
    report = governed_exit.run_ex1(governed_exit_evidence=evidence)
    assert report["status"] == "INSUFFICIENT_DATA"
    declared = governed_scientific_result(report)
    assert declared.scientifically_meaningful is False
    assert declared.not_meaningful_reason == INSUFFICIENT_GOVERNED_EVIDENCE


def _result_for(qstore: QuestionCycleStore, cycle, question_id: str) -> dict:
    projection = qstore.load_projection(cycle.cycle_id)
    return projection["questions"][question_id]["result"]



def test_real_x3_evaluator_declares_its_own_omnibus_session_test():
    from tests.test_repair_4b2_x3_session_execution_quality import _population
    from research_engine.experiments.execution_protection_research import run_x3

    results, contexts = _population({"LONDON": 0.01, "NY": 0.04})
    report = run_x3(results, contexts)
    assert report["status"] == "COMPLETE"
    declared = governed_scientific_result(report)
    assert declared.scientifically_meaningful is True
    assert declared.signal.sample_size > 0
    assert declared.falsification.criteria
    # X3 identifies an execution-quality difference, never a trading intervention.
    assert declared.candidate_design is None
    assert declared.no_intervention_reason == NO_INTERVENTION_MAPPING
    assert "governed_policy_id" not in governed_scientific_metrics(report)


def test_real_x3_evaluator_declares_insufficient_evidence_without_a_fit():
    from tests.test_repair_4b2_x3_session_execution_quality import _population
    from research_engine.experiments.execution_protection_research import run_x3

    results, contexts = _population({"LONDON": 0.01}, clusters_per_session=3)
    report = run_x3(results, contexts)
    assert report["status"] == "INSUFFICIENT_DATA"
    declared = governed_scientific_result(report)
    assert declared.scientifically_meaningful is False
    assert declared.not_meaningful_reason == INSUFFICIENT_GOVERNED_EVIDENCE


def test_real_e1_evaluator_declares_its_own_t_test():
    from research_engine.data_access.shadow_runtime_ingestion import (
        reconstruct_completed_shadow_trades,
    )

    population = reconstruct_completed_shadow_trades(
        _shadow_lifecycles([-1.0] * 10 + [2.0, 2.0]))
    report = expected_value.run(shadow_trades=population)
    assert report["status"] == "COMPLETE"
    declared = governed_scientific_result(report)
    assert declared.scientifically_meaningful is True
    assert declared.signal.classification == "NEGATIVE_EDGE"
    assert declared.signal.significance_method == (
        "one_sample_t_test_vs_zero_normal_approximation")
    assert declared.signal.effect_direction == "NEGATIVE"
    assert declared.signal.primary_metric == "mean_r_multiple_per_completed_lifecycle"
    assert declared.candidate_design is None
    assert declared.no_intervention_reason == NO_INTERVENTION_MAPPING


def test_real_e1_evaluator_declares_not_estimable_without_a_sample():
    report = expected_value.run(shadow_trades=[])
    declared = governed_scientific_result(report)
    assert declared.scientifically_meaningful is False
    assert declared.not_meaningful_reason == "TEST_NOT_ESTIMABLE"


def test_real_ex3_descriptive_report_declares_descriptive_only():
    report = exit_management._make_report(
        question_id="EX3", status="COMPLETE",
        overall={"finding": "TP reachability profile computed", "sample_size": 500},
        confidence="HIGH",
        dataset={"source": "shadow_runtime_v1(ingested)", "sample_size": 500},
        recommendation="FINDING: TP reachability profile computed",
    )
    assert report["status"] == "COMPLETE"
    declared = governed_scientific_result(report)
    assert declared.scientifically_meaningful is False
    assert declared.not_meaningful_reason == DESCRIPTIVE_ONLY
    metrics = governed_scientific_metrics(report)
    assert metrics["scientifically_meaningful"] is False
    assert metrics["scientific_not_meaningful_reason"] == DESCRIPTIVE_ONLY
    assert "population" not in metrics


# ── Part D: the real production chain ────────────────────────────────────────

# 240 completed lifecycles: E1 has a well-sampled negative expectancy and the
# descriptive EX3/EX4 evaluators reach their own strict 200-lifecycle gate.
E1_R_MULTIPLES = [-1.0] * 200 + [2.0] * 40
NEGATIVE_EDGE_R_MULTIPLES = E1_R_MULTIPLES


@pytest.fixture(scope="module")
def e1_chain(tmp_path_factory):
    """One real cycle + bridge over a persisted evidence fixture."""
    root = tmp_path_factory.mktemp("e1-chain")
    snapshot, cycle, qstore = _run_cycle(
        root, _objects(_shadow_lifecycles(NEGATIVE_EDGE_R_MULTIPLES)))
    bridge, registry = _run_bridge(root, cycle, qstore)
    return SimpleNamespace(
        root=root, snapshot=snapshot, cycle=cycle, qstore=qstore,
        bridge=bridge, registry=registry,
    )


def test_real_evaluator_result_carries_its_governed_signal_into_the_cycle(e1_chain):
    result = _result_for(e1_chain.qstore, e1_chain.cycle, "E1")
    assert result["status"] == "COMPLETE"
    metrics = result["key_metrics"]
    assert metrics["scientifically_meaningful"] is True
    assert metrics["scientific_result_schema"] == SCIENTIFIC_RESULT_SCHEMA
    assert metrics["scientific_classification"] == "NEGATIVE_EDGE"
    assert metrics["primary_metric"] == "mean_r_multiple_per_completed_lifecycle"
    assert metrics["significance_method"] == (
        "one_sample_t_test_vs_zero_normal_approximation")
    assert metrics["population"]["population_id"] == "CURRENT_COMPLETED_SHADOW_LIFECYCLES"
    assert metrics["falsification_criteria"]
    assert "governed_policy_id" not in metrics


def test_real_evaluator_result_creates_a_finding_and_falsifiable_hypothesis(e1_chain):
    bridge = e1_chain.bridge
    assert len(bridge.findings_created) == 1
    assert bridge.hypotheses_created
    assert not bridge.candidates_created
    assert bridge.candidate_design_required

    hypothesis = e1_chain.registry.get_hypothesis(bridge.hypotheses_created[0])
    assert hypothesis.source_question == "E1"
    assert hypothesis.falsification_criteria == [
        "E1: the mean R-multiple per completed lifecycle becomes positive on the "
        "governed replication population",
        "E1: the sample becomes too small or degenerate to estimate the mean at all",
    ]
    result_id = _result_for(e1_chain.qstore, e1_chain.cycle, "E1")["result_id"]
    assert hypothesis.source_question_results == [result_id]

    state = ScientificStateStore(e1_chain.root / "scientific").document
    row = state["findings"][hypothesis.source_finding][-1]


def test_complete_descriptive_and_insufficient_results_create_no_finding(e1_chain):
    bridge = e1_chain.bridge
    # The real EX1 evaluator cannot bind the M5 candle path in the common
    # snapshot, so it declares INSUFFICIENT_GOVERNED_EVIDENCE.
    ex1 = _result_for(e1_chain.qstore, e1_chain.cycle, "EX1")
    assert ex1["status"] == "INSUFFICIENT_DATA"
    assert ex1["key_metrics"]["scientifically_meaningful"] is False
    assert ex1["key_metrics"]["scientific_not_meaningful_reason"] == (
        INSUFFICIENT_GOVERNED_EVIDENCE)
    # The real EX3/EX4 descriptive evaluators are structurally COMPLETE.
    for question_id in ("EX3", "EX4"):
        result = _result_for(e1_chain.qstore, e1_chain.cycle, question_id)
        assert result["status"] == "COMPLETE", (question_id, result["status"])
        assert result["key_metrics"]["scientifically_meaningful"] is False
        assert result["key_metrics"]["scientific_not_meaningful_reason"] == DESCRIPTIVE_ONLY

    processed = set(bridge.question_changes_processed)
    assert {"EX1", "EX3", "EX4"} <= processed
    findings = ScientificStateStore(e1_chain.root / "scientific").document["findings"]
    assert all(
        "E1" in row[-1]["source_question_ids"] for row in findings.values())
    assert any("RUNNER_DECLARED_NOT_MEANINGFUL" in row for row in bridge.review_required)


def test_no_parallel_proposal_path_creates_a_candidate(e1_chain):
    """E1 has no governed intervention mapping: no candidate may appear."""
    assert not e1_chain.bridge.candidates_created
    assert e1_chain.registry.list_candidates() == []
    assert e1_chain.bridge.candidate_design_required
    assert any(
        "CANDIDATE_DESIGN_REQUIRED" in row
        for row in e1_chain.bridge.review_required)


# ── Part D2: the governed candidate chain from the real EX1 evaluator ────────


def _fixture_executor(evidence, question_id="EX1"):
    """Supply the persisted-fixture governed artifact exactly as the resolver does.

    The real runner, the real runner-kwargs binding and the real execution
    boundary are all unchanged; only the governed evidence artifact for the
    question under test is sourced from the persisted fixture instead of the
    candle-less common snapshot.
    """
    def executor(runner, question, population, context):
        if question.id == question_id:
            context.runner_artifacts["governed_exit_evidence"] = evidence
        return _execute_runner(runner, question, population, context)
    return executor


@pytest.fixture(scope="module")
def ex1_chain(tmp_path_factory):
    root = tmp_path_factory.mktemp("ex1-chain")
    evidence = _governed_exit_evidence(root)
    snapshot, cycle, qstore = _run_cycle(
        root, _objects(_shadow_lifecycles(NEGATIVE_EDGE_R_MULTIPLES)),
        runner_executor=_fixture_executor(evidence))
    bridge, registry = _run_bridge(root, cycle, qstore)
    return SimpleNamespace(
        root=root, snapshot=snapshot, cycle=cycle, qstore=qstore,
        bridge=bridge, registry=registry,
    )


def test_real_ex1_evaluator_drives_the_governed_candidate_chain(ex1_chain):
    bridge = ex1_chain.bridge
    result = _result_for(ex1_chain.qstore, ex1_chain.cycle, "EX1")
    assert result["status"] == "COMPLETE"
    assert result["key_metrics"]["governed_policy_id"] == GOVERNED_POLICY_ID
    assert result["key_metrics"]["scientifically_meaningful"] is True

    assert len(bridge.candidates_created) == 1
    candidate_id = bridge.candidates_created[0]
    candidate = ex1_chain.registry.get_candidate(candidate_id)
    assert candidate.policy_id == GOVERNED_POLICY_ID
    assert candidate.component == "TARGET_GEOMETRY"
    assert candidate.status == "PROPOSED"
    assert candidate.changes["frozen_policy"] == GOVERNED_POLICY
    assert candidate.source_question_results == [result["result_id"]]

    # Lineage reaches the original question, result, finding and hypothesis.
    hypothesis = ex1_chain.registry.get_hypothesis(candidate.hypothesis_id)
    assert hypothesis.source_question == "EX1"
    assert hypothesis.source_question_results == [result["result_id"]]
    assert hypothesis.falsification_criteria
    assert candidate.provenance["question_id"] == "EX1"
    assert candidate.provenance["question_result_id"] == result["result_id"]
    assert candidate.provenance["finding_id"] == hypothesis.source_finding
    assert candidate.provenance["snapshot_id"] == ex1_chain.snapshot.snapshot_id

    plan = ex1_chain.registry.get_plan(candidate_id)
    assert plan is not None
    assert plan.minimum_sample == 200
    assert plan.target_questions == ["EX1"]
    assert plan.success_conditions
    assert plan.failure_conditions
    assert bridge.validation_handoff[0]["candidate_id"] == candidate_id




def test_governed_candidate_reaches_projection_and_lab_without_live_approval(ex1_chain):
    from research_engine.v10.continuous.validation_queue import ValidationQueueStore

    candidate_id = ex1_chain.bridge.candidates_created[0]
    candidate = ex1_chain.registry.get_candidate(candidate_id)
    question_projection = ex1_chain.qstore.load_projection(ex1_chain.cycle.cycle_id)
    frontier = SimpleNamespace(
        snapshot_id=ex1_chain.snapshot.snapshot_id,
        fingerprint=ex1_chain.snapshot.snapshot_fingerprint,
        investigation_epoch=ex1_chain.snapshot.evidence_epoch,
        frontier_start=ex1_chain.snapshot.start_date,
        frontier_end=ex1_chain.snapshot.end_date,
        predecessor_snapshot_id=None,
        changed_datasets=tuple(BOUND_DATASETS),
        stale_datasets=(), missing_optional_datasets=(), status="SNAPSHOT_READY",
    )
    projection = build_unified_research_projection(
        continuous_cycle_id="CRCYCLE-RB1",
        frontier=frontier,
        question_projection=question_projection,
        bridge=ex1_chain.bridge,
        scientific_store=ScientificStateStore(ex1_chain.root / "scientific"),
        optimisation_registry=ex1_chain.registry,
        validation_store=ValidationQueueStore(ex1_chain.root / "validation.json"),
    )
    rows = {row["candidate_id"]: row for row in projection["candidates"]}
    assert candidate_id in rows
    assert rows[candidate_id]["live_approved"] is False
    assert rows[candidate_id]["status"] == "PROPOSED"
    assert rows[candidate_id]["source_question_ids"] == ["EX1"]
    assert rows[candidate_id]["plan"]["minimum_sample"] == 200
    assert projection["hypotheses"]
    assert projection["findings"]

    ex1_row = next(
        row for row in projection["canonical_questions"] if row["question_id"] == "EX1")
    assert ex1_row["linked_candidates"] == [candidate_id]

    lab = build_lab_view(projection)
    assert [row["candidate_id"] for row in lab["candidates"]] == [candidate_id]
    assert lab["candidates"][0]["live_approved"] is False

    # No autonomous live authority: the proposal stays a proposal.
    assert candidate.status == "PROPOSED"
    assert dict(candidate.shadow_binding) == {}
    assert candidate.policy_id in {
        item["policy_id"] for item in CANDIDATE_POLICIES_V1}
    assert all(
        item.status not in {
            "VALIDATED", "FORWARD_VALIDATED", "SHADOW_VALIDATION_ACTIVE",
            "SHADOW_VALIDATED", "READY_FOR_PROMOTION_REVIEW", "ACCEPTED",
        }
        for item in ex1_chain.registry.list_candidates()
    )


def test_repeated_cycle_and_bridge_create_no_duplicate_candidate(ex1_chain):
    """The same governed science must never mint a second candidate."""
    root = ex1_chain.root
    evidence = _governed_exit_evidence(root / "second")
    snapshot, cycle, qstore = _run_cycle(
        root, _objects(_shadow_lifecycles(NEGATIVE_EDGE_R_MULTIPLES)),
        name="cycles-second",
        runner_executor=_fixture_executor(evidence))
    bridge, _ = _run_bridge(root, cycle, qstore, registry=ex1_chain.registry)
    candidates = ex1_chain.registry.list_candidates()
    assert len(candidates) == 1
    assert candidates[0].candidate_id == ex1_chain.bridge.candidates_created[0]
    assert not bridge.candidates_created



# ── Part E: finding / hypothesis / candidate eligibility fails closed ─────────


def _blocked_like(result, status: str):
    from research_engine.v10.continuous.question_cycle_state import (
        CanonicalQuestionResult,
    )

    return CanonicalQuestionResult(**{
        **result.identity_material(),
        "status": status,
        "substantive_answer": None,
        "key_metrics": {},
        "missing_evidence": ("trade_truth",),
    })


@pytest.mark.parametrize("status", ["BLOCKED", "WAITING_FOR_DATA", "INSUFFICIENT_DATA"])
def test_non_scientific_statuses_never_create_a_finding(tmp_path, status):
    qstore = QuestionCycleStore(tmp_path / "questions")
    cycle = _cycle(qstore, 1, _blocked_like(_result("ISNAP-1"), status))
    bridge, registry = _bridge_run(tmp_path, cycle, qstore)
    assert not bridge.findings_created
    assert not registry.list_hypotheses()
    assert not registry.list_candidates()


def test_explicit_not_meaningful_declaration_cannot_be_upgraded(tmp_path):
    """A descriptive declaration wins over any incidental classification."""
    qstore = QuestionCycleStore(tmp_path / "questions")
    result = _result("ISNAP-1", metrics={
        "scientifically_meaningful": False,
        "scientific_not_meaningful_reason": DESCRIPTIVE_ONLY,
        "finding_classification": "SUPPORTED",
        "effect_size": 5.0,
    })
    bridge, registry = _bridge_run(tmp_path, _cycle(qstore, 1, result), qstore)
    assert not bridge.findings_created
    assert not registry.list_hypotheses()
    assert any(
        "RUNNER_DECLARED_NOT_MEANINGFUL:" + DESCRIPTIVE_ONLY in row
        for row in bridge.review_required)


def test_contradictory_scientific_metadata_fails_closed(tmp_path):
    qstore = QuestionCycleStore(tmp_path / "questions")
    contradictory = _result("ISNAP-1", metrics={
        "scientific_not_meaningful_reason": DESCRIPTIVE_ONLY,
    })
    bridge, registry = _bridge_run(
        tmp_path, _cycle(qstore, 1, contradictory), qstore)
    assert bridge.failures
    assert "SCIENTIFIC_METADATA_CONTRADICTION" in bridge.failures[0]["reason"]
    assert not bridge.findings_created
    assert not registry.list_hypotheses()


def test_invalid_confidence_interval_fails_closed(tmp_path):
    qstore = QuestionCycleStore(tmp_path / "questions")
    result = _result("ISNAP-1")
    result = type(result)(**{
        **result.identity_material(),
        "statistical_output": {"confidence_interval": [0.9, 0.1]},
    })
    bridge, registry = _bridge_run(tmp_path, _cycle(qstore, 1, result), qstore)
    assert bridge.failures
    assert "INVALID_CONFIDENCE_INTERVAL" in bridge.failures[0]["reason"]
    assert not bridge.findings_created


def test_finding_without_falsification_criteria_stops_at_the_finding(tmp_path):
    qstore = QuestionCycleStore(tmp_path / "questions")
    result = _result("ISNAP-1", metrics={"falsification_criteria": []})
    bridge, registry = _bridge_run(tmp_path, _cycle(qstore, 1, result), qstore)
    assert bridge.findings_created
    assert not registry.list_hypotheses()
    assert any(
        "FALSIFICATION_CRITERIA_REQUIRED" in row for row in bridge.review_required)


def test_candidate_design_requires_complete_validation_criteria(tmp_path):
    qstore = QuestionCycleStore(tmp_path / "questions")
    result = _result("ISNAP-1", metrics={"governed_policy_id": POLICY["policy_id"]})
    bridge, registry = _bridge_run(tmp_path, _cycle(qstore, 1, result), qstore)
    assert bridge.hypotheses_created
    assert not registry.list_candidates()
    assert any(
        "VALIDATION_CRITERIA_REQUIRED" in row for row in bridge.review_required)


def test_ungoverned_policy_identity_blocks_the_candidate(tmp_path):
    qstore = QuestionCycleStore(tmp_path / "questions")
    result = _result("ISNAP-1", metrics={
        "governed_policy_id": "INVENTED_POLICY_V1",
        "validation_criteria": VALIDATION_CRITERIA,
    })
    bridge, registry = _bridge_run(tmp_path, _cycle(qstore, 1, result), qstore)
    assert bridge.hypotheses_created
    assert not registry.list_candidates()
    assert any("UNGOVERNED_POLICY_ID" in row for row in bridge.review_required)


def test_invalid_treatment_parameters_fail_closed(tmp_path):
    from research_engine.v10.continuous.scientific_state_bridge import (
        ScientificStateBridgeError,
    )

    qstore = QuestionCycleStore(tmp_path / "questions")
    result = _result("ISNAP-1", metrics={
        "governed_policy_id": POLICY["policy_id"],
        "governed_policy_parameters": {**POLICY, "distance_r": 0.99},
        "validation_criteria": VALIDATION_CRITERIA,
    })
    with pytest.raises(
        ScientificStateBridgeError, match="GOVERNED_POLICY_PARAMETERS_MISMATCH"
    ):
        _bridge_run(tmp_path, _cycle(qstore, 1, result), qstore)


# ── Part F: the contract is evaluator-agnostic (Repair Block 2 interface) ────


def test_contract_is_evaluator_agnostic_for_generated_questions():
    """A generated-question evaluator can emit the identical governed shape."""
    generated = meaningful(
        "Q71",
        signal=_signal(
            signal_type="GENERATED_QUESTION_SIGNAL",
            population={"population_id": "GENERATED_POPULATION", "question_id": "Q71"},
        ),
        falsification=FalsificationContract(criteria=("Q71 falsification",)),
        no_intervention_reason=NO_INTERVENTION_MAPPING,
    )
    block = generated.to_dict()
    assert block["question_id"] == "Q71"
    assert governed_scientific_metrics({SCIENTIFIC_RESULT_REPORT_KEY: block})[
        "scientifically_meaningful"] is True

    # A generated question may also name a governed policy, using the same
    # governed catalogue and the same exact parameters.
    with_design = meaningful(
        "Q71",
        signal=_signal(population={"population_id": "P", "question_id": "Q71"}),
        falsification=FalsificationContract(criteria=("Q71 falsification",)),
        candidate_design=_design(
            applicable_population={"population_id": "P", "question_id": "Q71"}),
    )
    metrics = governed_scientific_metrics(
        {SCIENTIFIC_RESULT_REPORT_KEY: with_design.to_dict()})
    assert metrics["governed_policy_id"] == GOVERNED_POLICY_ID
    assert metrics["validation_criteria"]["required_sample"] == 200


def test_every_canonical_question_declares_a_machine_readable_verdict():
    """The evaluator inventory is mechanical: every question says yes or no."""
    import research_engine.experiments.expected_value as e1_module
    import research_engine.experiments.exit_management as descriptive_module
    import research_engine.experiments.exit_policy_governed as governed_module

    assert e1_module.EVALUATOR_SEMANTIC_VERSIONS["run"].startswith("e1_")
    assert governed_module.EVALUATOR_SEMANTIC_VERSIONS["run_ex1"].startswith("ex1_")
    assert governed_module.EVALUATOR_SEMANTIC_VERSIONS["run_ex9"].startswith("ex9_")
    # The descriptive evaluator declares its boundary in its own report builder.
    report = descriptive_module._make_report(
        question_id="EX4", status="COMPLETE",
        overall={"finding": "adverse-excursion profile computed", "sample_size": 100},
        confidence="HIGH",
        dataset={"source": "shadow_runtime_v1(ingested)", "sample_size": 100},
        recommendation="FINDING: adverse-excursion profile computed",
    )
    assert governed_scientific_result(report).not_meaningful_reason == DESCRIPTIVE_ONLY




def test_one_governed_autonomous_candidate_path_has_no_parallel_writer():
    """Authority boundary: only the bridge derives candidates from research."""
    import research_engine

    root = Path(research_engine.__file__).resolve().parent
    bridge_writers = set()
    bridge_callers = set()
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        relative = str(path.relative_to(root)).replace("\\", "/")
        if "OptimisationRegistry" in text:
            bridge_writers.add(relative)
        if "run_scientific_state_bridge" in text:
            bridge_callers.add(relative)
    # The governed registry is touched only by the bridge, its store, the
    # validation queue, the unified projection, the research loop, and the
    # Repair Block 3 control-plane modules that READ it to derive ONE canonical
    # candidate lifecycle.  Those readers must never mutate it (asserted below).
    assert bridge_writers == {
        "v10/continuous/research_loop.py",
        "v10/continuous/research_projection.py",
        "v10/continuous/scientific_state_bridge.py",
        "v10/continuous/validation_queue.py",
        "v10/optimisation/optimisation_registry.py",
        "v10/optimisation/__init__.py",
        "control_plane/candidate_lifecycle_authority.py",
        "control_plane/candidate_lifecycle_service.py",
    }, sorted(bridge_writers)
    # The Repair Block 3 lifecycle modules are READ-ONLY consumers of the
    # governed registry: no mutation primitive may ever appear in them.
    for relative in ("control_plane/candidate_lifecycle_authority.py",
                     "control_plane/candidate_lifecycle_service.py"):
        text = (root / relative).read_text(encoding="utf-8", errors="ignore")
        for mutation in ("add_candidate(", "add_hypothesis(", "add_plan(",
                         "update_candidate_status(", "update_hypothesis_status(",
                         "bind_shadow_candidate("):
            assert mutation not in text, (relative, mutation)
    # The legacy proposal/designer workflow never touches the governed registry.
    assert [item for item in bridge_writers if "proposals" in item] == []
    assert bridge_callers == {
        "v10/continuous/research_loop.py",
        "v10/continuous/scientific_state_bridge.py",
        "v10/continuous/__init__.py",
    }, sorted(bridge_callers)
