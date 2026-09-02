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


class PartitionModel(str, Enum):
    """Canonical S3 partition semantics for a dataset."""
    SYMBOL_DATE = "symbol_date"   # {base}/schema_version={s}/symbol={SYM}/date={DATE}/
    DATE = "date"                 # {base}/schema_version={s}/date={DATE}/  (portfolio-wide)


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
    # Canonical generation. This is a FRESH V1 baseline: every active dataset
    # begins at generation 1. Future genuine revisions become 2, 3, ...
    generation: int = 1
    # Canonical S3 partition semantics owned by the contract (not the writers).
    partition_model: PartitionModel = PartitionModel.SYMBOL_DATE


def _schema(
    dataset: str,
    *,
    legacy: tuple[str, ...] = (),
    owner: str,
    population: str,
    role: DatasetRole,
    current: str | None = None,
    s3_name: str | None = None,
    partition_model: PartitionModel = PartitionModel.SYMBOL_DATE,
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
        generation=1,
        partition_model=partition_model,
    )


# Persisted dataset names are keys. This is a fresh Production V1 baseline:
# every dataset emits and reads its V1 schema only. No V2/V3 dataset generation
# or V2/V3 schema compatibility exists. Future V2/V3 work starts from this V1 base.
PRODUCTION_SCHEMA_REGISTRY: dict[str, ProductionSchema] = {
    "events": _schema("events", role=DatasetRole.CORE, owner="observation_stream", population="LIVE_AND_REPLAY"),
    "market_context": _schema("market_context", role=DatasetRole.CORE, owner="market_context", population="LIVE_AND_REPLAY"),
    "opportunities": _schema("opportunities", role=DatasetRole.CORE, owner="opportunity_lifecycle", population="LIVE_AND_REPLAY"),
    "assessments": _schema("assessments", role=DatasetRole.CORE, owner="assessment", population="LIVE_AND_REPLAY"),
    "decision_ledger": _schema("decision_ledger", role=DatasetRole.CORE, owner="decision_authority", population="LIVE_AND_REPLAY"),
    "execution_results": _schema("execution_results", role=DatasetRole.CORE, owner="execution_result", population="LIVE"),
    "trade_truth": _schema("trade_truth", role=DatasetRole.CORE, owner="realized_execution", population="LIVE"),
    "strategy_candidates": _schema("strategy_candidates", role=DatasetRole.SUPPORTING, owner="strategy_selection", population="LIVE_AND_REPLAY"),
    "horizon_candidates": _schema("horizon_candidates", role=DatasetRole.SUPPORTING, owner="horizon_selection", population="LIVE_AND_REPLAY"),
    "decision_trace": _schema("decision_trace", role=DatasetRole.SUPPORTING, owner="decision_diagnostics", population="LIVE_AND_REPLAY"),
    "execution_context": _schema("execution_context", role=DatasetRole.SUPPORTING, owner="execution_intent", population="LIVE"),
    "execution_attempts": _schema("execution_attempts", role=DatasetRole.SUPPORTING, owner="execution_attempt", population="LIVE"),
    "protection_audit": _schema("protection_audit", role=DatasetRole.SUPPORTING, owner="protection_verification", population="LIVE"),
    "management_actions": _schema("management_actions", role=DatasetRole.SUPPORTING, owner="trade_management", population="LIVE"),
    "risk_deviation": _schema("risk_deviation", role=DatasetRole.SUPPORTING, owner="risk_observation", population="LIVE"),
    "portfolio_rankings": _schema("portfolio_rankings", role=DatasetRole.SUPPORTING, current="portfolio_ranking_v1", owner="portfolio_ranking", population="LIVE_AND_REPLAY", partition_model=PartitionModel.DATE),
    "shadow_runtime": _schema("shadow_runtime", role=DatasetRole.SUPPORTING, owner="shadow_runtime", population="SHADOW"),
    "shadow_trades": _schema("shadow_trades", role=DatasetRole.SUPPORTING, owner="shadow_trade_simulation", population="SHADOW"),
    "strategy_observations": _schema("strategy_observations", role=DatasetRole.SUPPORTING, current="strategy_observation_v1", owner="strategy_observation", population="OBSERVATIONAL"),
    "research_shadow_trades": _schema("research_shadow_trades", role=DatasetRole.SUPPORTING, owner="research_assessment", population="RESEARCH"),
    "trade_journal": _schema("trade_journal", role=DatasetRole.PROJECTION, owner="trade_journal", population="LIVE"),
    "portfolio_shadow": _schema("portfolio_shadow", role=DatasetRole.PROJECTION, owner="portfolio_ranking", population="SHADOW", partition_model=PartitionModel.DATE),
    "quarantine": _schema("quarantine", role=DatasetRole.PROJECTION, owner="contract_validation", population="LIVE_AND_REPLAY"),
}

# ─── RETIRED DATASETS (Production V1 consolidation: 35 → 23) ──────────────────
# The following 12 dataset generations were removed. Their unique fields were
# integrated into a retained V1 owner at the field's natural runtime point
# (no observer/runtime reordering). This tuple is the anti-regression allowlist:
# no writer/reader/registry entry may reintroduce these dataset names.
# The V2/V3 opportunity/shadow lineage that once produced these dataset names
# has been DELETED (canonical V1 cleanup). This frozenset is the anti-regression
# allowlist naming retired datasets that must NEVER return; it does not describe
# any active route. Canonical V1 data flows only via observation_id and
# canonical_opportunity_id.
RETIRED_DATASETS: frozenset[str] = frozenset({
    "opportunity_assessment",   # duplicate assessment-stage write
    "trade_truth_graph",        # reference-only pointers; lineage via correlation_id joins
    "v3_market_understanding",  # objective description now via core/market_understanding
    "v2_opportunities",         # retired V2 opportunity observation lineage
    "v3_opportunities",         # retired V3 opportunity observation lineage
    "v3_opportunity_assessment",# retired V3 shadow assessment lineage
    "v3_market_context",        # retired V3 shadow context lineage
    "v3_horizon_assessment",    # retired V3 shadow assessment lineage
    "v3_risk_assessment",       # retired V3 shadow assessment lineage
    "v3_entry_assessment",      # retired V3 shadow assessment lineage
    "v3_execution_assessment",  # retired V3 shadow assessment lineage
    "decision_audit",           # consolidated into decision_trace (same-cycle engine output)
})


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


def current_generation(dataset: str) -> int:
    """Return the canonical numeric generation for a dataset (V1 baseline == 1)."""
    return PRODUCTION_SCHEMA_REGISTRY[dataset].generation


def partition_model(dataset: str) -> "PartitionModel":
    """Return the canonical S3 partition semantics for a dataset."""
    return PRODUCTION_SCHEMA_REGISTRY[dataset].partition_model


def is_symbol_scoped(dataset: str) -> bool:
    """True when the dataset partitions by symbol (vs portfolio/date-scoped)."""
    return PRODUCTION_SCHEMA_REGISTRY[dataset].partition_model is PartitionModel.SYMBOL_DATE


# ─── CANONICAL S3 PATH AUTHORITY ──────────────────────────────────────────────
# ONE builder for both the WRITER key and the Research Engine READ prefix, so a
# writer and the loader can never diverge on path convention. All canonical keys
# are Hive-partitioned:
#   symbol-scoped : {base}/schema_version={schema}/symbol={SYM}/date={DATE}/{part}
#   date-scoped   : {base}/schema_version={schema}/date={DATE}/{part}


def canonical_s3_schema_prefix(dataset: str, *, schema: str | None = None) -> str:
    """`{base}/schema_version={schema}/` — the schema-version partition root."""
    entry = PRODUCTION_SCHEMA_REGISTRY[dataset]
    return f"{entry.s3_base_prefix}/schema_version={schema or entry.current}/"


def canonical_s3_list_prefix(
    dataset: str,
    *,
    symbol: str | None = None,
    schema: str | None = None,
) -> str:
    """Read-side listing prefix. Adds symbol= for symbol-scoped datasets only."""
    prefix = canonical_s3_schema_prefix(dataset, schema=schema)
    if symbol and is_symbol_scoped(dataset):
        return f"{prefix}symbol={symbol}/"
    return prefix


def canonical_s3_key(
    dataset: str,
    *,
    symbol: str,
    date: str,
    part: str = "part-000.jsonl",
    schema: str | None = None,
) -> str:
    """Write-side object key. symbol is ignored for date-scoped datasets."""
    prefix = canonical_s3_schema_prefix(dataset, schema=schema)
    if is_symbol_scoped(dataset):
        return f"{prefix}symbol={symbol}/date={date}/{part}"
    return f"{prefix}date={date}/{part}"
