"""Canonical production dataset contract for the new AWS account/bucket."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


DATA_CONTRACT_VERSION = "production_v1"


class DatasetRole(str, Enum):
    CORE = "core"
    SUPPORTING = "supporting"
    PROJECTION = "projections"
    LEGACY = "legacy"


@dataclass(frozen=True)
class ProductionSchema:
    dataset: str
    role: DatasetRole
    s3_base_prefix: str
    current: str
    status: str
    legacy_supported_versions: tuple[str, ...]
    semantic_owner: str
    population: str


def _schema(
    dataset: str,
    *,
    legacy: tuple[str, ...] = (),
    owner: str,
    population: str,
    role: DatasetRole,
    current: str | None = None,
    s3_name: str | None = None,
) -> ProductionSchema:
    return ProductionSchema(
        dataset=dataset,
        role=role,
        s3_base_prefix=f"{role.value}/{s3_name or dataset}",
        current=current or f"{dataset}_v1",
        status="PRODUCTION",
        legacy_supported_versions=legacy,
        semantic_owner=owner,
        population=population,
    )


# Persisted dataset names are keys.  V2/V3 in a dataset name denotes an engine
# family/path namespace; the schema value is nevertheless production V1.
PRODUCTION_SCHEMA_REGISTRY: dict[str, ProductionSchema] = {
    "events": _schema("events", role=DatasetRole.CORE, legacy=("1", "2", "3"), owner="observation_stream", population="LIVE_AND_REPLAY"),
    "market_context": _schema("market_context", role=DatasetRole.CORE, legacy=("market_context_v2",), owner="market_context", population="LIVE_AND_REPLAY"),
    "opportunities": _schema("opportunities", role=DatasetRole.CORE, legacy=("opportunities_v2",), owner="opportunity_lifecycle", population="LIVE_AND_REPLAY"),
    "assessments": _schema("assessments", role=DatasetRole.CORE, owner="assessment", population="LIVE_AND_REPLAY"),
    "decision_ledger": _schema("decision_ledger", role=DatasetRole.CORE, owner="decision_authority", population="LIVE_AND_REPLAY"),
    "execution_results": _schema("execution_results", role=DatasetRole.CORE, owner="execution_result", population="LIVE"),
    "trade_truth": _schema("trade_truth", role=DatasetRole.CORE, legacy=("trade_truth_v2", "trade_truth_v3"), owner="realized_execution", population="LIVE"),
    "strategy_candidates": _schema("strategy_candidates", role=DatasetRole.SUPPORTING, owner="strategy_selection", population="LIVE_AND_REPLAY"),
    "horizon_candidates": _schema("horizon_candidates", role=DatasetRole.SUPPORTING, owner="horizon_selection", population="LIVE_AND_REPLAY"),
    "opportunity_assessment": _schema("opportunity_assessment", role=DatasetRole.SUPPORTING, owner="opportunity_assessment", population="LIVE_AND_REPLAY"),
    "decision_trace": _schema("decision_trace", role=DatasetRole.SUPPORTING, legacy=("decision_trace_v2",), owner="decision_diagnostics", population="LIVE_AND_REPLAY"),
    "execution_context": _schema("execution_context", role=DatasetRole.SUPPORTING, owner="execution_intent", population="LIVE"),
    "execution_attempts": _schema("execution_attempts", role=DatasetRole.SUPPORTING, owner="execution_attempt", population="LIVE"),
    "protection_audit": _schema("protection_audit", role=DatasetRole.SUPPORTING, owner="protection_verification", population="LIVE"),
    "management_actions": _schema("management_actions", role=DatasetRole.SUPPORTING, owner="trade_management", population="LIVE"),
    "risk_deviation": _schema("risk_deviation", role=DatasetRole.SUPPORTING, owner="risk_observation", population="LIVE"),
    "portfolio_rankings": _schema("portfolio_rankings", role=DatasetRole.SUPPORTING, current="portfolio_ranking_v1", owner="portfolio_ranking", population="LIVE_AND_REPLAY"),
    "shadow_runtime": _schema("shadow_runtime", role=DatasetRole.SUPPORTING, owner="shadow_runtime", population="SHADOW"),
    "shadow_trades": _schema("shadow_trades", role=DatasetRole.SUPPORTING, legacy=("shadow_trades_v2",), owner="shadow_trade_simulation", population="SHADOW"),
    "v2_opportunities": _schema("v2_opportunities", role=DatasetRole.SUPPORTING, current="v2_opportunity_v1", owner="v2_observer", population="OBSERVATIONAL"),
    "v3_opportunities": _schema("v3_opportunities", role=DatasetRole.SUPPORTING, current="v3_opportunity_v1", owner="v3_observer", population="OBSERVATIONAL"),
    "v3_market_understanding": _schema("v3_market_understanding", role=DatasetRole.SUPPORTING, current="market_understanding_v1", owner="v3_shadow", population="SHADOW"),
    "v3_market_context": _schema("v3_market_context", role=DatasetRole.SUPPORTING, current="v3_market_context_v1", owner="v3_shadow", population="SHADOW"),
    "v3_opportunity_assessment": _schema("v3_opportunity_assessment", role=DatasetRole.SUPPORTING, current="v3_opportunity_assessment_v1", owner="v3_shadow", population="SHADOW"),
    "v3_horizon_assessment": _schema("v3_horizon_assessment", role=DatasetRole.SUPPORTING, current="v3_horizon_assessment_v1", legacy=("v3_horizon_assessment_v2",), owner="v3_shadow", population="SHADOW"),
    "v3_risk_assessment": _schema("v3_risk_assessment", role=DatasetRole.SUPPORTING, current="v3_risk_assessment_v1", owner="v3_shadow", population="SHADOW"),
    "v3_entry_assessment": _schema("v3_entry_assessment", role=DatasetRole.SUPPORTING, current="v3_entry_assessment_v1", owner="v3_shadow", population="SHADOW"),
    "v3_execution_assessment": _schema("v3_execution_assessment", role=DatasetRole.SUPPORTING, current="v3_execution_assessment_v1", owner="v3_shadow", population="SHADOW"),
    "strategy_observations": _schema("strategy_observations", role=DatasetRole.SUPPORTING, current="strategy_observation_v1", owner="strategy_observation", population="OBSERVATIONAL"),
    "research_shadow_trades": _schema("research_shadow_trades", role=DatasetRole.SUPPORTING, owner="research_assessment", population="RESEARCH"),
    "decision_audit": _schema("decision_audit", role=DatasetRole.PROJECTION, owner="decision_audit", population="LIVE_AND_REPLAY"),
    "trade_truth_graph": _schema("trade_truth_graph", role=DatasetRole.PROJECTION, legacy=("trade_truth_graph_v2",), owner="trade_lineage", population="LIVE"),
    "trade_journal": _schema("trade_journal", role=DatasetRole.PROJECTION, owner="trade_journal", population="LIVE"),
    "portfolio_shadow": _schema("portfolio_shadow", role=DatasetRole.PROJECTION, owner="portfolio_ranking", population="SHADOW"),
    "quarantine": _schema("quarantine", role=DatasetRole.PROJECTION, owner="contract_validation", population="LIVE_AND_REPLAY"),
}


def current_schema(dataset: str) -> str:
    """Return the sole schema emitted for a production dataset."""
    return PRODUCTION_SCHEMA_REGISTRY[dataset].current


def supported_schemas(dataset: str) -> frozenset[str]:
    """Return current plus explicitly supported historical reader schemas."""
    entry = PRODUCTION_SCHEMA_REGISTRY[dataset]
    return frozenset((entry.current, *entry.legacy_supported_versions))


def s3_base_prefix(dataset: str) -> str:
    """Return the role-qualified base prefix for new Production V1 writes."""
    return PRODUCTION_SCHEMA_REGISTRY[dataset].s3_base_prefix
