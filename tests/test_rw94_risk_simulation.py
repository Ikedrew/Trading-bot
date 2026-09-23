"""Focused deterministic tests for governed RW9.4 R3-R5."""
from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path

import pytest

from research_engine.control_plane.report_ownership import canonical_report_owner
from research_engine.control_plane.risk_policy_evidence import (
    build_chronological_risk_population,
    build_risk_policy_evidence,
    split_chronological_windows,
)
from research_engine.experiments import risk_simulation_governed as governed
from research_engine.experiments.drawdown_threshold import validate_r4_report
from research_engine.experiments.position_sizing import validate_r5_report
from research_engine.experiments.probability_of_ruin import validate_r3_report
from research_engine.registry.definition_validator import (
    build_definitions_from_registry,
    validate_all_definitions,
)
from research_engine.registry.master_repair_ledger import operational_baseline
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID
from research_engine.registry.risk_policy_adjudication import (
    BASELINE_RISK_POLICY_V1,
    HALT_THRESHOLD_GRID_V1,
    R5_CONTRACT,
    RUIN_MODEL_V1,
    SIZING_MODELS_V1,
)


def _decision(opportunity: str, timestamp: int) -> dict:
    return {
        "schema_version": "decision_trace_v1",
        "canonical_opportunity_id": opportunity,
        "canonical_symbol": "EURUSD",
        "entity_id": f"entity-{opportunity}",
        "decision_id": f"decision-{opportunity}",
        "correlation_id": f"correlation-{opportunity}",
        "timestamp_utc": timestamp,
        "stages_reached": ["risk"],
        "stages_passed": ["risk"],
    }


def _outcome(
    opportunity: str,
    timestamp: int,
    outcome_r: float,
    suffix: int,
    *,
    account: str = "account-a",
    exit_timestamp: int | None = None,
) -> dict:
    return {
        "schema_version": "shadow_trades_v1",
        "identity": {
            "shadow_trade_id": f"nshadow_{suffix:016x}",
            "canonical_opportunity_id": opportunity,
            "entity_id": f"entity-{opportunity}",
            "decision_id": f"decision-{opportunity}",
            "correlation_id": f"correlation-{opportunity}",
            "strategy_id": "MEAN_REVERSION",
            "evaluated_horizon": "SCALP",
            "account_id": account,
            "shadow_type": "PRIMARY_HORIZON_SIMULATION",
        },
        "decision_snapshot": {
            "timestamp_decision_utc": timestamp,
            "trade_horizon": "SCALP",
            "h4_regime": "TRENDING",
        },
        "simulated_outcome": {
            "pnl_r_multiple": outcome_r,
            "exit_timestamp": timestamp + 10 if exit_timestamp is None else exit_timestamp,
        },
    }


def _evidence(
    calibration_prefix: list[float] | None = None,
    *,
    default_r: float = 1.0,
    fanout: bool = False,
    reverse_inputs: bool = False,
    purge_first: bool = False,
):
    decisions: list[dict] = []
    outcomes: list[dict] = []
    prefix = calibration_prefix or []
    for index in range(170):
        opportunity = f"EURUSD*sim-{index:03d}"
        timestamp = 1_000 + index * 100
        value = prefix[index] if index < len(prefix) else default_r
        decisions.append(_decision(opportunity, timestamp))
        exit_timestamp = 13_000 if purge_first and index == 0 else None
        outcomes.append(_outcome(
            opportunity, timestamp, value, index + 1, exit_timestamp=exit_timestamp,
        ))
        if fanout and index == 0:
            outcomes.append(_outcome(
                opportunity, timestamp, value, 10_000, account="account-b",
                exit_timestamp=exit_timestamp,
            ))
    if reverse_inputs:
        decisions.reverse()
        outcomes.reverse()
    return build_risk_policy_evidence(
        decisions, outcomes, baseline_authority=deepcopy(BASELINE_RISK_POLICY_V1),
    )


@pytest.fixture(scope="module")
def complete_chain():
    evidence = _evidence([-1.0] * 6)
    r3 = governed.analyse_r3(evidence)
    r4 = governed.analyse_r4(evidence, r3)
    r5 = governed.analyse_r5(evidence, r3, r4)
    return evidence, r3, r4, r5


def test_r3_frozen_constants_deterministic_rng_resampling_and_wilson(monkeypatch):
    assert RUIN_MODEL_V1["ruin_threshold_pct"] == 0.50
    assert RUIN_MODEL_V1["risk_per_trade_pct"] == 0.01
    assert RUIN_MODEL_V1["monte_carlo"]["n_simulations"] == 10_000
    assert RUIN_MODEL_V1["monte_carlo"]["trade_horizon"] == 5_000
    assert RUIN_MODEL_V1["seed_contract"]["seed_base"] == 20240101

    original = governed.random.Random
    seeds: list[int] = []

    class TrackingRandom:
        def __init__(self, seed):
            seeds.append(seed)
            self._rng = original(seed)

        def randrange(self, stop):
            return self._rng.randrange(stop)

    monkeypatch.setattr(governed.random, "Random", TrackingRandom)
    monkeypatch.setattr(governed.random, "random", lambda: (_ for _ in ()).throw(
        AssertionError("process-global RNG used")
    ))
    first = governed._simulate_ruin_paths(
        [1.0, 2.0], n_simulations=3, trade_horizon=4, seed_base=10,
        risk_per_trade=0.01, ruin_threshold=0.50,
    )
    second = governed._simulate_ruin_paths(
        [1.0, 2.0], n_simulations=3, trade_horizon=4, seed_base=10,
        risk_per_trade=0.01, ruin_threshold=0.50,
    )
    assert first == second
    assert seeds == [11, 12, 13, 11, 12, 13]
    assert first["sampling"].endswith("with replacement from calibration R-multiples")
    assert first["wilson_interval_95"] == governed.wilson_interval(0, 3)


def test_r3_reports_analytical_and_mc_and_disagreement_fails_closed(monkeypatch):
    evidence = _evidence()
    report = governed.analyse_r3(evidence)
    assert report["status"] == "COMPLETE"
    assert report["overall"]["analytical_ruin_probability"] == 0.0
    assert report["overall"]["monte_carlo"]["ruin_probability"] == 0.0
    assert "wilson_interval_95" in report["overall"]["monte_carlo"]
    assert report["overall"]["survival_probability"] == 1.0
    assert report["overall"]["expected_survival_trades"] == 5_000

    fake = deepcopy(report["overall"]["monte_carlo"])
    fake["ruin_probability"] = 1.0
    fake["survival_probability"] = 0.0
    monkeypatch.setattr(governed, "monte_carlo_ruin", lambda values: fake)
    blocked = governed.analyse_r3(evidence)
    assert blocked["status"] == "BLOCKED"
    assert blocked["provenance"]["readiness"]["blockers"] == [
        "ANALYTICAL_MONTE_CARLO_DISAGREEMENT"
    ]


def test_chronological_split_purge_freeze_and_fanout_are_canonical():
    evidence = _evidence(fanout=True, reverse_inputs=True)
    windows = split_chronological_windows(build_chronological_risk_population(evidence))
    assert evidence.summary.account_fanout_observation_count == 1
    assert evidence.summary.eligible_observations == 170
    assert len(windows.calibration_records) == 119
    assert len(windows.validation_records) == 51
    assert windows.calibration_fraction == 0.70
    assert windows.provenance["calibration_digest"]
    assert windows.provenance["validation_digest"]
    assert max(item.entry_utc_epoch_s for item in windows.calibration_records) < min(
        item.entry_utc_epoch_s for item in windows.validation_records
    )

    purged = split_chronological_windows(build_chronological_risk_population(
        _evidence(purge_first=True)
    ))
    assert len(purged.purged_records) == 1
    assert len(purged.calibration_records) == 118


def test_r4_closed_grid_chronology_definitions_fallback_and_resume():
    fallback = governed.drawdown_recovery_path([-1.0] * 6 + [1.0] * 20)
    assert tuple(item["threshold"] for item in fallback["threshold_grid"]) == HALT_THRESHOLD_GRID_V1
    assert fallback["selected_halt_threshold"] == 0.50
    assert fallback["resume_threshold"] == 0.25
    assert fallback["runtime_effect"] == "NONE"
    assert fallback["definitions"]["breach"].startswith("first lifecycle")
    assert "series end counts as unrecovered" in fallback["definitions"]["unrecovered_at_series_end"]

    failed = governed.drawdown_recovery_path([-1.0] * 100)
    assert failed["selected_halt_threshold"] == 0.05
    assert failed["resume_threshold"] == 0.025
    first = failed["threshold_grid"][0]
    assert first["breach_episodes"] == 1
    assert first["recovery_episodes"] == 0
    assert first["unrecovered_episodes"] == 1
    assert first["recovery_probability"] == 0.0


def test_r3_r4_r5_complete_chain_and_no_retrofit(complete_chain):
    evidence, r3, r4, r5 = complete_chain
    assert r3["status"] == r4["status"] == r5["status"] == "COMPLETE"
    assert validate_r3_report(r3)[0] is True
    assert validate_r4_report(r4)[0] is True
    assert validate_r5_report(r5)[0] is True
    assert r4["provenance"]["upstream_authority"]["question_id"] == "R3"
    assert set(r5["provenance"]["upstream_authority"]) == {"R3", "R4"}
    assert r3["provenance"]["chronology"]["split_digest"] == r4["provenance"]["chronology"]["split_digest"]
    assert r4["provenance"]["chronology"]["split_digest"] == r5["provenance"]["chronology"]["split_digest"]
    assert all(
        report["provenance"]["chronology"]["validation_outcomes_used_for_fitting"] is False
        for report in (r3, r4, r5)
    )

    changed = _evidence([-1.0] * 7)
    blocked = governed.analyse_r4(changed, r3)
    assert blocked["status"] == "BLOCKED"
    assert "NO_RETROFIT_SPLIT_MISMATCH" in blocked["provenance"]["readiness"]["blockers"][0]


def test_r5_exact_vocabulary_kelly_objective_constraints_and_determinism(complete_chain):
    _, _, _, report = complete_chain
    models = report["overall"]["models"]
    assert tuple(report["overall"]["model_vocabulary"]) == tuple(
        model["model_id"] for model in SIZING_MODELS_V1
    )
    assert len(models) == 8
    assert set(report["overall"]["declared_families"]) == {
        "FIXED_RISK", "KELLY", "FRACTIONAL_KELLY", "FIXED_LOT", "DYNAMIC",
    }
    assert report["overall"]["selection_objective"] == R5_CONTRACT["objective"]
    assert "Sharpe ratio" in report["overall"]["forbidden_objectives"][0]
    assert "terminal equity" in report["overall"]["forbidden_objectives"][1]
    assert all("sharpe" not in model and "terminal_equity" not in model for model in models)
    assert report["overall"]["selected_model_id"] == governed.analyse_r5(
        complete_chain[0], complete_chain[1], complete_chain[2]
    )["overall"]["selected_model_id"]
    assert all(
        not model["eligible"] or model["maximum_drawdown"] <= 0.30
        for model in models
    )
    assert all(
        not model["eligible"] or model["ruin_probability"] <= RUIN_MODEL_V1["acceptable_ruin_threshold"]
        for model in models
    )


def test_negative_kelly_makes_whole_kelly_family_ineligible():
    values = [-1.0, -1.0, 1.0]
    kelly = governed.kelly_fraction(values)
    assert kelly < 0
    results = [
        governed.evaluate_sizing_model(model, values, kelly=kelly, upstream_ruin_probability=0.0)
        for model in SIZING_MODELS_V1
    ]
    for result in results:
        if result["family"] in {"KELLY", "FRACTIONAL_KELLY"}:
            assert result["eligible"] is False
            assert "NON_POSITIVE_KELLY_FAMILY_INELIGIBLE" in result["ineligibility_reasons"]


def test_invalid_baseline_blocks_all_three():
    invalid = build_risk_policy_evidence([], [], baseline_authority=None)
    r3 = governed.analyse_r3(invalid)
    r4 = governed.analyse_r4(invalid, {})
    r5 = governed.analyse_r5(invalid, {}, {})
    assert [r3["status"], r4["status"], r5["status"]] == ["BLOCKED"] * 3


def test_reports_are_deterministic_under_reordered_inputs():
    first = governed.analyse_r3(_evidence())
    second = governed.analyse_r3(_evidence(reverse_inputs=True))
    for report in (first, second):
        report.pop("generated", None)
    assert first == second


def test_registry_ownership_versions_counts_and_production_boundary():
    expected = {
        "R3": ("run_probability_of_ruin", "r3_probability_of_ruin.json"),
        "R4": ("run_drawdown_threshold", "r4_drawdown_threshold.json"),
        "R5": ("run_position_sizing", "r5_position_sizing.json"),
    }
    for question_id, (runner, filename) in expected.items():
        question = REGISTRY_BY_ID[question_id]
        assert (question.runner_function, question.report_filename) == (runner, filename)
        assert canonical_report_owner(filename) == question_id
    assert len(REGISTRY) == len({item.id for item in REGISTRY}) == 70
    definitions = build_definitions_from_registry(REGISTRY)
    health = validate_all_definitions(definitions)
    assert all(item.definition_version == 1 for item in definitions.values())
    assert all(
        not any(issue.category == "INVALID_VERSION" for issue in result.results)
        for result in health.values()
    )
    assert operational_baseline() == (58, 12)
    assert REGISTRY_BY_ID["R1"].runner_function == "run_r1"
    assert REGISTRY_BY_ID["R2"].runner_function == "run_r2"

    for module in (governed,):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert imported.isdisjoint({"config", "core", "risk"})
