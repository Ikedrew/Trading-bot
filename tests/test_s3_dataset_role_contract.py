"""Static guarantees for the physical Production V1 S3 role contract."""

from __future__ import annotations

import ast
from pathlib import Path

from core.production_data_contract import (
    DatasetRole,
    PRODUCTION_SCHEMA_REGISTRY,
    s3_base_prefix,
)


# This is deliberately explicit: adding a Production V1 dataset requires an
# architecture decision about its role, owner, population, and schema.
_ACCEPTED_CONTRACT = {
    "events": ("core", "events_v1", "observation_stream", "LIVE_AND_REPLAY"),
    "market_context": ("core", "market_context_v1", "market_context", "LIVE_AND_REPLAY"),
    "opportunities": ("core", "opportunities_v1", "opportunity_lifecycle", "LIVE_AND_REPLAY"),
    "assessments": ("core", "assessments_v1", "assessment", "LIVE_AND_REPLAY"),
    "decision_ledger": ("core", "decision_ledger_v1", "decision_authority", "LIVE_AND_REPLAY"),
    "execution_results": ("core", "execution_results_v1", "execution_result", "LIVE"),
    "trade_truth": ("core", "trade_truth_v1", "realized_execution", "LIVE"),
    "strategy_candidates": ("supporting", "strategy_candidates_v1", "strategy_selection", "LIVE_AND_REPLAY"),
    "horizon_candidates": ("supporting", "horizon_candidates_v1", "horizon_selection", "LIVE_AND_REPLAY"),
    "opportunity_assessment": ("supporting", "opportunity_assessment_v1", "opportunity_assessment", "LIVE_AND_REPLAY"),
    "decision_trace": ("supporting", "decision_trace_v1", "decision_diagnostics", "LIVE_AND_REPLAY"),
    "execution_context": ("supporting", "execution_context_v1", "execution_intent", "LIVE"),
    "execution_attempts": ("supporting", "execution_attempts_v1", "execution_attempt", "LIVE"),
    "protection_audit": ("supporting", "protection_audit_v1", "protection_verification", "LIVE"),
    "management_actions": ("supporting", "management_actions_v1", "trade_management", "LIVE"),
    "risk_deviation": ("supporting", "risk_deviation_v1", "risk_observation", "LIVE"),
    "portfolio_rankings": ("supporting", "portfolio_ranking_v1", "portfolio_ranking", "LIVE_AND_REPLAY"),
    "shadow_runtime": ("supporting", "shadow_runtime_v1", "shadow_runtime", "SHADOW"),
    "shadow_trades": ("supporting", "shadow_trades_v1", "shadow_trade_simulation", "SHADOW"),
    "v2_opportunities": ("supporting", "v2_opportunity_v1", "v2_observer", "OBSERVATIONAL"),
    "v3_opportunities": ("supporting", "v3_opportunity_v1", "v3_observer", "OBSERVATIONAL"),
    "v3_market_understanding": ("supporting", "market_understanding_v1", "v3_shadow", "SHADOW"),
    "v3_market_context": ("supporting", "v3_market_context_v1", "v3_shadow", "SHADOW"),
    "v3_opportunity_assessment": ("supporting", "v3_opportunity_assessment_v1", "v3_shadow", "SHADOW"),
    "v3_horizon_assessment": ("supporting", "v3_horizon_assessment_v1", "v3_shadow", "SHADOW"),
    "v3_risk_assessment": ("supporting", "v3_risk_assessment_v1", "v3_shadow", "SHADOW"),
    "v3_entry_assessment": ("supporting", "v3_entry_assessment_v1", "v3_shadow", "SHADOW"),
    "v3_execution_assessment": ("supporting", "v3_execution_assessment_v1", "v3_shadow", "SHADOW"),
    "strategy_observations": ("supporting", "strategy_observation_v1", "strategy_observation", "OBSERVATIONAL"),
    "research_shadow_trades": ("supporting", "research_shadow_trades_v1", "research_assessment", "RESEARCH"),
    "decision_audit": ("projections", "decision_audit_v1", "decision_audit", "LIVE_AND_REPLAY"),
    "trade_truth_graph": ("projections", "trade_truth_graph_v1", "trade_lineage", "LIVE"),
    "trade_journal": ("projections", "trade_journal_v1", "trade_journal", "LIVE"),
    "portfolio_shadow": ("projections", "portfolio_shadow_v1", "portfolio_ranking", "SHADOW"),
    "quarantine": ("projections", "quarantine_v1", "contract_validation", "LIVE_AND_REPLAY"),
}


_ACTIVE_WRITERS = {
    "core/assessment/persistence.py": "assessments",
    "core/contracts/quarantine.py": "quarantine",
    "core/decision_audit.py": "decision_audit",
    "core/decision_ledger.py": "decision_ledger",
    "core/decision_trace.py": "decision_trace",
    "core/execution_context.py": "execution_context",
    "core/market_context/persistence.py": "market_context",
    "core/opportunity/persistence.py": "opportunities",
    "core/persistence/execution_attempts_writer.py": "execution_attempts",
    "core/persistence/execution_result_writer.py": "execution_results",
    "core/persistence/horizon_candidates_writer.py": "horizon_candidates",
    "core/persistence/management_actions_writer.py": "management_actions",
    "core/persistence/opportunity_assessment_writer.py": "opportunity_assessment",
    "core/persistence/opportunity_writer.py": "opportunities",
    "core/persistence/strategy_candidates_writer.py": "strategy_candidates",
    "core/portfolio_ranking/persistence.py": "portfolio_rankings",
    "core/portfolio_ranking/shadow_comparison.py": "portfolio_shadow",
    "core/protection_verification.py": "protection_audit",
    "core/research_assessment/research_shadow_engine.py": "research_shadow_trades",
    "core/risk_deviation.py": "risk_deviation",
    "core/shadow/persistence.py": "shadow_runtime",
    "core/shadow_trades.py": "shadow_trades",
    "core/strategies/observation_persistence.py": "strategy_observations",
    "core/trade_journal.py": "trade_journal",
    "core/trade_truth.py": "trade_truth",
    "core/trade_truth_graph.py": "trade_truth_graph",
}


def test_every_active_production_v1_dataset_has_exactly_one_accepted_role():
    assert set(PRODUCTION_SCHEMA_REGISTRY) == set(_ACCEPTED_CONTRACT)
    for dataset, expected in _ACCEPTED_CONTRACT.items():
        entry = PRODUCTION_SCHEMA_REGISTRY[dataset]
        assert isinstance(entry.role, DatasetRole)
        assert (entry.role.value, entry.current, entry.semantic_owner, entry.population) == expected
        assert entry.status == "PRODUCTION"
        assert entry.s3_base_prefix == f"{entry.role.value}/{dataset}"


def test_active_writers_resolve_prefixes_only_through_the_registry():
    for filename, dataset in _ACTIVE_WRITERS.items():
        source = Path(filename).read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=filename)
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "s3_base_prefix"
        ]
        assert any(
            call.args and isinstance(call.args[0], ast.Constant)
            and call.args[0].value == dataset
            for call in calls
        ), filename


def test_roles_cannot_cross_physical_prefix_boundaries():
    for entry in PRODUCTION_SCHEMA_REGISTRY.values():
        top_level = entry.s3_base_prefix.split("/", 1)[0]
        assert top_level == entry.role.value
        if entry.role is DatasetRole.CORE:
            assert not entry.s3_base_prefix.startswith(("supporting/", "projections/"))


def test_non_core_datasets_cannot_advertise_canonical_stage_authority():
    canonical_owners = {
        "observation_stream", "market_context", "opportunity_lifecycle",
        "assessment", "decision_authority", "execution_result", "realized_execution",
    }
    for entry in PRODUCTION_SCHEMA_REGISTRY.values():
        if entry.role is DatasetRole.PROJECTION:
            assert entry.semantic_owner not in canonical_owners
        if entry.role is DatasetRole.SUPPORTING:
            assert entry.semantic_owner not in {"decision_authority", "realized_execution"}


def test_no_active_writer_contains_a_flat_new_bucket_dataset_prefix():
    dataset_names = set(PRODUCTION_SCHEMA_REGISTRY)
    for filename in _ACTIVE_WRITERS:
        source = Path(filename).read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=filename)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            is_s3_prefix = any(
                isinstance(target, ast.Name) and "S3" in target.id and "PREFIX" in target.id
                for target in targets
            )
            value = node.value
            if is_s3_prefix and isinstance(value, ast.Constant) and isinstance(value.value, str):
                first = value.value.strip("/").split("/", 1)[0]
                assert first not in dataset_names, (filename, value.value)


def test_canonical_reconstruction_path_is_core_only():
    reconstruction = (
        "events", "market_context", "opportunities", "assessments",
        "decision_ledger", "execution_results", "trade_truth",
    )
    assert all(PRODUCTION_SCHEMA_REGISTRY[name].role is DatasetRole.CORE for name in reconstruction)
    assert [s3_base_prefix(name) for name in reconstruction] == [
        "core/events", "core/market_context", "core/opportunities", "core/assessments",
        "core/decision_ledger", "core/execution_results", "core/trade_truth",
    ]


def test_no_legacy_dataset_is_emitted_in_the_new_bucket():
    assert not [entry for entry in PRODUCTION_SCHEMA_REGISTRY.values() if entry.role is DatasetRole.LEGACY]
