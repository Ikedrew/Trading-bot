"""New-account Production V1 dataset-contract guarantees."""

from __future__ import annotations

from pathlib import Path

from core.config import DATA_CONTRACT_VERSION, NEW_RUNTIME_S3_BUCKET
from core.production_data_contract import (
    PRODUCTION_SCHEMA_REGISTRY,
    current_schema,
    supported_schemas,
)


def test_global_contract_and_bucket_are_new_baseline():
    assert DATA_CONTRACT_VERSION == "production_v1"
    assert NEW_RUNTIME_S3_BUCKET == "trading-bot-v10-data"


def test_every_registered_production_schema_is_v1():
    assert PRODUCTION_SCHEMA_REGISTRY
    for entry in PRODUCTION_SCHEMA_REGISTRY.values():
        assert entry.status == "PRODUCTION"
        assert entry.current.endswith("_v1"), entry


def test_reset_writers_use_registry_values():
    from core.decision_trace import _SCHEMA_VERSION as decision_trace
    from core.market_context.persistence import _SCHEMA_VERSION as market_context
    from core.persistence.opportunity_writer import _SCHEMA_VERSION as opportunities
    from core.shadow_trades import _SCHEMA_VERSION as shadow_trades
    from core.trade_truth import _SCHEMA_VERSION as trade_truth

    assert decision_trace == current_schema("decision_trace")
    assert market_context == current_schema("market_context")
    assert opportunities == current_schema("opportunities")
    assert shadow_trades == current_schema("shadow_trades")
    assert trade_truth == current_schema("trade_truth")
    # trade_truth_graph and v3_horizon_assessment datasets were retired
    # (Production V1 consolidation); their schema-version imports were removed.


def test_no_v2_v3_schema_compatibility_is_retained():
    """Fresh Production V1 baseline: every dataset supports ONLY its V1 schema.

    No V2/V3 schema-compatibility (read allowlist) may exist. Future V2/V3 work
    starts from this V1 baseline; it is not carried as legacy compatibility here.
    """
    for dataset, entry in PRODUCTION_SCHEMA_REGISTRY.items():
        supported = supported_schemas(dataset)
        # Sole supported schema is the current V1 schema.
        assert supported == frozenset({entry.current}), (dataset, supported)
        assert entry.legacy_supported_versions == (), dataset
        # No supported schema carries a v2/v3 generation token.
        for version in supported:
            assert "_v2" not in version and "_v3" not in version, (dataset, version)
    # Sanity: the previously compatibility-listed datasets now expose V1 only.
    assert supported_schemas("trade_truth") == frozenset({"trade_truth_v1"})
    assert supported_schemas("decision_trace") == frozenset({"decision_trace_v1"})
    assert supported_schemas("shadow_trades") == frozenset({"shadow_trades_v1"})
    assert current_schema("shadow_trades") == "shadow_trades_v1"


def test_new_bucket_writer_sources_do_not_embed_old_schema_partitions():
    writer_files = (
        "core/decision_trace.py",
        "core/market_context/persistence.py",
        "core/persistence/opportunity_writer.py",
        "core/shadow_trades.py",
        "core/trade_truth.py",
        "core/trade_journal.py",
    )
    forbidden = (
        "schema_version=decision_trace_v2",
        "schema_version=market_context_v2",
        "schema_version=opportunities_v2",
        "schema_version=shadow_trades_v2",
        "schema_version=trade_truth_v3",
    )
    for filename in writer_files:
        source = Path(filename).read_text(encoding="utf-8")
        assert not any(value in source for value in forbidden), filename
