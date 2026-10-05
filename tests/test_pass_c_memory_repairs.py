"""Pass C structural memory bounds and scientific equivalence tests."""
from __future__ import annotations

from copy import deepcopy

import research_engine.control_plane.execution_evidence as execution_module
import research_engine.experiments.dataset_suitability as g1_module
from research_engine.control_plane.evidence_resolver import (
    EvidenceSnapshot,
    resolve_question_evidence,
)
from research_engine.control_plane.execution_evidence import (
    build_governed_execution_evidence,
)
from research_engine.experiments.execution_protection_research import run_exec1
from research_engine.experiments.execution_stability import run_x6
from research_engine.registry.research_question_registry import REGISTRY, REGISTRY_BY_ID


def _execution_population(decisions: int = 12, accounts: int = 2):
    results, contexts, traces = [], [], []
    for index in range(decisions):
        correlation = f"COR-{index}"
        common = {
            "data_epoch": "CURRENT", "correlation_id": correlation,
            "canonical_opportunity_id": f"OPP-{index}",
            "decision_id": f"DEC-{index}", "entity_id": f"ENTITY-{index}",
            "symbol": "EURUSD" if index % 2 == 0 else "GBPUSD",
        }
        contexts.append({
            **common, "schema_version": "execution_context_v1",
            "market_access": {
                "session_state": "LONDON" if index % 2 == 0 else "NEW_YORK",
                "spread": 0.0001 + index * 0.00001,
                "spread_atr_ratio": 0.05 + index * 0.02,
            },
        })
        traces.append({
            **common, "schema_version": "decision_trace_v1", "action": "EXECUTE",
            "v10_market_state": {"regime": {
                "volatility_state": "CALM" if index % 2 == 0 else "VOLATILE",
            }},
        })
        for account in range(accounts):
            results.append({
                **common, "schema_version": "execution_results_v1",
                "account_id": f"ACCOUNT-{account}", "broker": "Broker",
                "broker_server": "Server", "order_ticket": index * 10 + account + 1,
                "result_ok": index < decisions // 2, "retcode": 10009,
                "slippage": 0.00001 * (index + account + 1),
                "slippage_semantic": "measured_execution_slippage",
            })
    return results, contexts, traces


def _without_generated(report):
    value = deepcopy(report)
    value.pop("generated", None)
    return value


def test_exec1_and_x6_reuse_one_governed_join_with_equivalent_reports(monkeypatch):
    results, contexts, traces = _execution_population()
    results.append({
        "schema_version": "execution_results_v1", "data_epoch": "CURRENT",
        "malformed_non_json_value": {"not", "json"},
    })
    datasets = {
        "execution_results_v1": results,
        "execution_context": contexts,
        "decision_trace": traces,
    }
    calls = 0
    original = execution_module.build_governed_execution_evidence

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(execution_module, "build_governed_execution_evidence", counted)
    snapshot = EvidenceSnapshot(datasets=datasets)
    exec_resolution = resolve_question_evidence(REGISTRY_BY_ID["EXEC1"], snapshot)
    x6_resolution = resolve_question_evidence(REGISTRY_BY_ID["X6"], snapshot)
    assert calls == 1
    assert exec_resolution.runner_artifacts["governed_execution_evidence"] is (
        x6_resolution.runner_artifacts["governed_execution_evidence"]
    )

    governed = exec_resolution.runner_artifacts["governed_execution_evidence"]
    source_before = deepcopy(datasets)
    cached_exec = run_exec1(governed_evidence=governed)
    cached_x6 = run_x6(governed_evidence=governed)
    assert calls == 1
    assert datasets == source_before
    assert _without_generated(cached_exec) == _without_generated(
        run_exec1(results, contexts, traces)
    )
    assert _without_generated(cached_x6) == _without_generated(
        run_x6(results, contexts, traces)
    )


def test_trusted_execution_build_does_not_recursively_deepcopy(monkeypatch):
    results, contexts, traces = _execution_population(decisions=3, accounts=1)

    def forbidden(_value):
        raise AssertionError("trusted immutable population was recursively copied")

    monkeypatch.setattr(execution_module, "deepcopy", forbidden)
    evidence = build_governed_execution_evidence(
        results, contexts, traces,
        require_decision_trace=True,
        trusted_immutable_inputs=True,
    )
    assert evidence.account_result_count == 3
    assert evidence._result_inputs[0] is results[0]
    assert evidence._context_inputs[0] is contexts[0]
    assert evidence._trace_inputs[0] is traces[0]


def test_generic_population_cache_is_bounded_across_question_families():
    shadow = [{
        "data_epoch": "CURRENT", "schema_version": "shadow_runtime_v1",
        "canonical_opportunity_id": "OPP-1", "entity_id": "ENTITY-1",
        "r_multiple": 1.0, "score": 0.8, "components": {"x": 1},
    }]
    trace = [{
        "data_epoch": "CURRENT", "schema_version": "decision_trace_v1",
        "canonical_opportunity_id": "OPP-1", "entity_id": "ENTITY-1",
        "score": 0.8, "components": {"x": 1},
    }]
    snapshot = EvidenceSnapshot(datasets={
        "shadow_trades": shadow, "decision_trace": trace,
    })
    resolve_question_evidence(REGISTRY_BY_ID["D1"], snapshot)
    assert len(snapshot._population_cache) == 1
    resolve_question_evidence(REGISTRY_BY_ID["G1"], snapshot)
    assert len(snapshot._population_cache) == 1
    assert next(iter(snapshot._population_cache)) == ("shadow_trades",)


def test_g1_metadata_snapshot_is_report_equivalent_and_question_scoped(monkeypatch):
    results, contexts, traces = _execution_population(decisions=3, accounts=1)
    datasets = {
        "execution_results_v1": results,
        "execution_context": contexts,
        "decision_trace": traces,
    }
    as_of = "2026-10-04T12:00:00+00:00"
    retained = g1_module._snapshot(
        datasets, as_of, retain_record_payloads=True,
    )
    metadata_only = g1_module._snapshot(
        datasets, as_of, retain_record_payloads=False,
    )
    assert retained.manifest() == metadata_only.manifest()
    assert retained.snapshot_id == metadata_only.snapshot_id
    assert metadata_only._payloads == ()
    assert metadata_only._input_payloads == ()
    assert metadata_only.records("execution_context") == retained.records(
        "execution_context"
    )

    created = []
    original_snapshot = g1_module.EvidenceSnapshot

    class TrackingSnapshot(original_snapshot):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(g1_module, "EvidenceSnapshot", TrackingSnapshot)
    optimized = g1_module.run_g1(snapshot=metadata_only)
    assert len(created) == len(REGISTRY)
    assert max(len(item._cache) for item in created) <= max(
        len(question.data_sources) for question in REGISTRY
    )
    baseline = g1_module.run_g1(snapshot=retained)
    assert _without_generated(optimized) == _without_generated(baseline)

    borrowed_context = datasets["execution_context"][0]
    returned_context = metadata_only.records("execution_context")[0]
    returned_context["market_access"]["session_state"] = "MUTATED"
    assert borrowed_context["market_access"]["session_state"] != "MUTATED"
