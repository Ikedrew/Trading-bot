"""
Canonical V1 contract acceptance tests.

Proves the ONE-contract invariant established by the normalization work:

    registry (generation 1, *_v1 schema)
        → writer schema == registry schema
        → writer S3 key == research-loader S3 prefix   (no writer/reader drift)
        → record validates against a REGISTERED V1 profile

Plus the clean-baseline version guard: every active canonical version indicator
is generation 1 (no *_v2/_v3, no event_layout_version>1, no dataset_version>1).

These are static/implementation checks — they do NOT require live S3 or a running
bot. Fresh-runtime V1 evidence is verified separately after the data wipe+restart.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.production_data_contract as pc
import core.canonical_profiles as cprof
from research_engine.data_access.s3_source import S3ResearchDataSource


PROFILED = cprof.registered_profiles()

# Minimal well-formed sample record per profiled dataset (required fields filled).
_SAMPLES = {
    "events": {"ts_utc_ms": 1, "type": "CANDLE", "event_layout_version": 1, "symbol": "EURUSD"},
    "market_context": {"symbol": "EURUSD", "entity_id": "EURUSD_1"},
    "opportunities": {"symbol": "EURUSD", "canonical_opportunity_id": "EURUSD*1*P"},
    "assessments": {"symbol": "EURUSD", "canonical_opportunity_id": "EURUSD*1*P",
                    "entity_id": "EURUSD_1", "correlation_id": "COR-1"},
    "decision_ledger": {"symbol": "EURUSD", "decision": "NO_TRADE", "decision_id": "d1",
                        "correlation_id": "COR-1", "observation_id": "o1"},
    "decision_trace": {"symbol": "EURUSD", "entity_id": "EURUSD_1", "action": "NO_TRADE"},
    "execution_context": {"symbol": "EURUSD", "entity_id": "EURUSD_1", "correlation_id": "COR-1"},
    "strategy_observations": {"symbol": "EURUSD", "canonical_opportunity_id": "EURUSD*1*P",
                              "observation_id": "o1", "entity_id": "EURUSD_1"},
    "horizon_candidates": {"symbol": "EURUSD", "canonical_opportunity_id": "EURUSD*1*P",
                           "entity_id": "EURUSD_1"},
    "strategy_candidates": {"symbol": "EURUSD"},
    "shadow_runtime": {"symbol": "EURUSD", "canonical_opportunity_id": "EURUSD*1*P"},
    "portfolio_rankings": {"ranking_id": "r1", "cycle_id": 5},
}


def _sample(dataset: str) -> dict:
    rec = dict(_SAMPLES[dataset])
    rec["schema_version"] = pc.current_schema(dataset)
    return rec


# ─── Registry-wide V1 invariants ──────────────────────────────────────────────

@pytest.mark.parametrize("dataset", list(pc.PRODUCTION_SCHEMA_REGISTRY))
def test_every_active_dataset_is_generation_1_and_v1(dataset):
    entry = pc.PRODUCTION_SCHEMA_REGISTRY[dataset]
    assert entry.generation == 1, f"{dataset} generation must be 1 (clean baseline)"
    assert entry.current.endswith("_v1"), f"{dataset} schema must be *_v1, got {entry.current}"


@pytest.mark.parametrize("dataset", PROFILED)
def test_profiled_dataset_schema_matches_registry(dataset):
    profile = cprof.get_profile(dataset)
    assert profile.schema_version == pc.current_schema(dataset)
    assert profile.generation == 1


@pytest.mark.parametrize("dataset", PROFILED)
def test_profiled_sample_record_validates(dataset):
    ok, violations = cprof.validate_record(dataset, _sample(dataset))
    assert ok, f"{dataset} sample failed profile validation: {violations}"


# ─── Writer/reader S3 path equivalence (the exact defect the audit found) ─────

@pytest.mark.parametrize("dataset", PROFILED)
def test_writer_key_matches_research_loader_prefix(dataset):
    """CANONICAL CONTRACT: the writer's S3 key must sit under the prefix the
    Research Engine loader lists. Both come from the same contract builder."""
    src = S3ResearchDataSource(bucket="b", client=object())
    symbol = "EURUSD" if pc.is_symbol_scoped(dataset) else None
    writer_key = pc.canonical_s3_key(dataset, symbol=(symbol or ""), date="2026-09-02")
    loader_prefix = src._list_prefixes(dataset, symbol=symbol, all_schemas=False)[0]
    assert writer_key.startswith(loader_prefix), (
        f"CANONICAL CONTRACT FAILURE {dataset}: writer key {writer_key!r} "
        f"not under loader prefix {loader_prefix!r}"
    )
    # And the schema-version partition is present.
    assert f"schema_version={pc.current_schema(dataset)}/" in writer_key


def test_execution_results_writer_emits_canonical_schema_partition():
    """Regression: the active execution_results writer must build its S3 key via
    the canonical contract (schema_version= partition) so the Research Engine
    loader can discover it. Previously it wrote a Layout-B key without the
    schema_version segment and returned 0 records to the loader."""
    import core.persistence.execution_result_writer as w
    src = S3ResearchDataSource(bucket="b", client=object())
    # Writer stamps the same schema the contract declares (single source).
    assert w._SCHEMA_VERSION == pc.current_schema("execution_results") == "execution_results_v1"
    key = pc.canonical_s3_key("execution_results", symbol="USDCAD", date="2026-09-03")
    loader_prefix = src._list_prefixes("execution_results", symbol="USDCAD", all_schemas=False)[0]
    assert key == ("core/execution_results/schema_version=execution_results_v1/"
                   "symbol=USDCAD/date=2026-09-03/part-000.jsonl")
    assert key.startswith(loader_prefix)
    # The active writer source uses the canonical helper, not a manual key.
    import inspect
    src_txt = inspect.getsource(w)
    assert 'canonical_s3_key("execution_results"' in src_txt
    assert '/symbol={symbol}/date={date_str}/part-000.jsonl"' not in src_txt


def test_portfolio_rankings_is_date_scoped_not_symbol():
    assert not pc.is_symbol_scoped("portfolio_rankings")
    key = pc.canonical_s3_key("portfolio_rankings", symbol="EURUSD", date="2026-09-02")
    assert "symbol=" not in key
    assert "schema_version=portfolio_ranking_v1/" in key


# ─── Negative: false / stale version claims must fail ─────────────────────────

def test_event_layout_version_3_fails_clean_baseline():
    rec = _sample("events")
    rec["event_layout_version"] = 3
    ok, violations = cprof.validate_record("events", rec)
    assert not ok
    assert any("event_layout_version" in v for v in violations)


def test_unregistered_v1_claim_fails():
    rec = _sample("assessments")
    rec["schema_version"] = "totally_made_up_v1"
    ok, violations = cprof.validate_record("assessments", rec)
    assert not ok
    assert any("unregistered _v1" in v or "mismatch" in v for v in violations)


def test_dataset_version_greater_than_1_fails():
    rec = _sample("portfolio_rankings")
    rec["dataset_version"] = "2026.1"
    ok, violations = cprof.validate_record("portfolio_rankings", rec)
    assert not ok
    assert any("CANONICAL CONTRACT FAILURE" in v for v in violations)


def test_unknown_dataset_cannot_validate():
    ok, violations = cprof.validate_record("not_a_dataset", {"schema_version": "x"})
    assert not ok


# ─── Clean-baseline version-guard over active canonical constants ─────────────

def test_events_active_version_constants_are_generation_1():
    from core.schema_registry import CURRENT_SCHEMA_VERSION
    from core.feature_registry import CURRENT_FEATURE_VERSION
    assert CURRENT_SCHEMA_VERSION == 1, "event_layout_version generation must be 1"
    assert CURRENT_FEATURE_VERSION == 1, "feature_version must be 1 on clean baseline"


def test_no_active_dataset_schema_is_v2_or_higher():
    for name, entry in pc.PRODUCTION_SCHEMA_REGISTRY.items():
        assert not entry.current.endswith(("_v2", "_v3", "_v4", "_v5")), (
            f"CANONICAL CONTRACT FAILURE: {name} active schema {entry.current} > v1"
        )


def test_writer_module_schema_constants_come_from_contract():
    """Active writers must not hold a hardcoded canonical schema string that
    diverges from the registry."""
    import core.assessment.assessment as a
    import core.opportunity.opportunity as o
    import core.portfolio_ranking.persistence as pr
    assert a.SCHEMA_VERSION == pc.current_schema("assessments")
    assert a.DATASET_VERSION == 1
    assert o.SCHEMA_VERSION == pc.current_schema("opportunities")
    assert o.DATASET_VERSION == 1
    assert pr.SCHEMA_VERSION == pc.current_schema("portfolio_rankings")
    assert pr.DATASET_VERSION == 1
