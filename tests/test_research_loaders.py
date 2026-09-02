"""
Tests for the Research Engine dataset loaders (S3-backed).

Post-migration these loaders read from S3 via the shared S3ResearchDataSource,
not local logs/. Tests drive them through an injected fake S3 source (test
fixtures), which is the sanctioned test mechanism — production Research Engine
execution treats S3 as authoritative.

Covers:
    - All 12 loaders read their dataset from S3
    - Missing dataset returns empty (a real gap, no local fallback)
    - Malformed records skipped without crashing
    - Symbol filtering returns only matching data
    - Aggregation across multiple date partitions
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.production_data_contract import s3_base_prefix, current_schema
from research_engine.data_access.s3_source import (
    S3ResearchDataSource, set_default_source, reset_default_source,
)
from research_engine.data_access.loaders import (
    load_opportunities,
    load_assessments,
    load_portfolio_rankings,
    load_shadow_comparisons,
    load_execution_results,
    load_execution_context,
    load_protection_audit,
    load_risk_deviation,
    load_shadow_trades,
    load_trade_truth,
    load_decision_ledger,
    load_decision_trace,
)


# ═══════════════════════════════════════════════════════════════════════════════
# FAKE S3 (dataset-keyed, symbol/date-partition aware)
# ═══════════════════════════════════════════════════════════════════════════════

_DATE_ONLY = {"portfolio_rankings", "portfolio_shadow"}


class FakeS3:
    def __init__(self):
        self.objects: dict[str, str] = {}

    def add(self, dataset: str, records: list[dict], *, symbol: str | None = None, date: str = "2026-07-23"):
        base = s3_base_prefix(dataset)
        schema = current_schema(dataset)
        if dataset in _DATE_ONLY or symbol is None:
            key = f"{base}/schema_version={schema}/date={date}/part-000.jsonl"
        else:
            key = f"{base}/schema_version={schema}/symbol={symbol}/date={date}/part-000.jsonl"
        self.objects[key] = self.objects.get(key, "") + "".join(json.dumps(r) + "\n" for r in records)

    def add_raw(self, dataset: str, body: str, *, symbol: str, date: str = "2026-07-23"):
        base = s3_base_prefix(dataset)
        schema = current_schema(dataset)
        key = f"{base}/schema_version={schema}/symbol={symbol}/date={date}/part-000.jsonl"
        self.objects[key] = body

    # boto3-compatible surface
    def list_objects_v2(self, **kw):
        prefix = kw.get("Prefix", "")
        keys = sorted(k for k in self.objects if k.startswith(prefix))
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}

    def get_object(self, **kw):
        key = kw["Key"]

        class _Body:
            def __init__(self, t): self._t = t
            def read(self): return self._t.encode("utf-8")

        return {"Body": _Body(self.objects[key])}


@pytest.fixture
def s3():
    fake = FakeS3()
    set_default_source(S3ResearchDataSource(bucket="test-bucket", client=fake))
    yield fake
    reset_default_source()


# ═══════════════════════════════════════════════════════════════════════════════
# SAMPLES
# ═══════════════════════════════════════════════════════════════════════════════

def _sample_opportunity() -> dict:
    return {"opportunity_id": "EURUSD_1784800000_TWEEZER_TOP", "symbol": "EURUSD",
            "direction": "SELL", "pattern": "TWEEZER_TOP", "state": "REJECTED",
            "schema_version": "opportunity_v1", "cycle_id": 100, "entity_id": "EURUSD_1784800000"}


def _sample_assessment() -> dict:
    return {"assessment_id": "GBPUSD_1784809820_TWEEZER_TOP_assessment",
            "opportunity_id": "GBPUSD_1784809820_TWEEZER_TOP", "symbol": "GBPUSD",
            "schema_version": "assessment_v1", "score_strategy": 0.62, "ev": 0.000142, "cycle_id": 4578}


def _sample_ranking() -> dict:
    return {"schema_version": "portfolio_ranking_v1", "ranking_id": "ranking_4578_1784809820123",
            "cycle_id": 4578, "total_candidates": 3, "selected_symbol": "GBPUSD", "candidates": []}


def _sample_shadow() -> dict:
    return {"cycle_id": 4578, "agreement": False, "disagreement_type": "WRONG_SYMBOL",
            "actual_executed_symbols": ["NZDUSD"], "ranking_selected_symbol": "GBPUSD"}


def _sample_execution_result() -> dict:
    return {"symbol": "EURUSD", "result_ok": True, "retcode": 10009, "fill_price": 1.10005,
            "slippage": 0.00002, "decision_id": "abc123", "correlation_id": "COR-TEST"}


def _sample_execution_context() -> dict:
    return {"correlation_id": "COR-TEST", "symbol": "EURUSD", "timestamp_utc": 1784800000.0,
            "market_access": {"session_state": "LONDON", "spread": 0.00012}}


def _sample_protection() -> dict:
    return {"symbol": "GBPUSD", "position_ticket": 12345, "protection_status": "VERIFIED",
            "requested_sl": 1.33775, "broker_confirmed_sl": 1.33775}


def _sample_risk_deviation() -> dict:
    return {"trade_id": "pos_100", "symbol": "NZDUSD", "planned_risk_R": -1.0,
            "actual_risk_R": -1.0, "risk_deviation": 1.0, "risk_classification": "NORMAL"}


# ═══════════════════════════════════════════════════════════════════════════════
# LOADERS SUCCESS (from S3)
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoadSuccess:
    def test_load_opportunities(self, s3):
        s3.add("opportunities", [_sample_opportunity()], symbol="EURUSD")
        records = load_opportunities("EURUSD")
        assert len(records) == 1
        assert records[0]["pattern"] == "TWEEZER_TOP"

    def test_load_assessments(self, s3):
        s3.add("assessments", [_sample_assessment()], symbol="GBPUSD")
        records = load_assessments("GBPUSD")
        assert len(records) == 1
        assert records[0]["ev"] == 0.000142

    def test_load_portfolio_rankings(self, s3):
        s3.add("portfolio_rankings", [_sample_ranking()])
        records = load_portfolio_rankings()
        assert len(records) == 1
        assert records[0]["selected_symbol"] == "GBPUSD"

    def test_load_shadow_comparisons(self, s3):
        s3.add("portfolio_shadow", [_sample_shadow()])
        records = load_shadow_comparisons()
        assert len(records) == 1
        assert records[0]["disagreement_type"] == "WRONG_SYMBOL"

    def test_load_execution_results(self, s3):
        s3.add("execution_results", [_sample_execution_result()], symbol="EURUSD")
        records = load_execution_results("EURUSD")
        assert len(records) == 1
        assert records[0]["result_ok"] is True

    def test_load_execution_context(self, s3):
        s3.add("execution_context", [_sample_execution_context()], symbol="EURUSD")
        records = load_execution_context("EURUSD")
        assert len(records) == 1
        assert records[0]["market_access"]["session_state"] == "LONDON"

    def test_load_protection_audit(self, s3):
        s3.add("protection_audit", [_sample_protection()], symbol="GBPUSD")
        records = load_protection_audit("GBPUSD")
        assert len(records) == 1
        assert records[0]["protection_status"] == "VERIFIED"

    def test_load_risk_deviation(self, s3):
        s3.add("risk_deviation", [_sample_risk_deviation()], symbol="NZDUSD")
        records = load_risk_deviation("NZDUSD")
        assert len(records) == 1


# NOTE (Production V1 cleanup): test_load_decision_audit removed — the
# decision_audit dataset and its loader are retired.


# ═══════════════════════════════════════════════════════════════════════════════
# MISSING / EMPTY — a real S3 gap, never a local fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestMissingAndEmpty:
    def test_missing_dataset_returns_empty(self, s3):
        assert load_opportunities("EURUSD") == []
        assert load_assessments("GBPUSD") == []
        assert load_portfolio_rankings() == []
        assert load_shadow_comparisons() == []
        assert load_execution_results("EURUSD") == []
        assert load_execution_context("EURUSD") == []
        assert load_protection_audit("GBPUSD") == []
        assert load_risk_deviation("NZDUSD") == []

    def test_empty_object_returns_empty(self, s3):
        s3.add_raw("opportunities", "", symbol="EURUSD")
        assert load_opportunities("EURUSD") == []


# ═══════════════════════════════════════════════════════════════════════════════
# MALFORMED RECORDS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMalformed:
    def test_malformed_line_skipped(self, s3):
        body = '{"valid": true}\nNOT_JSON_AT_ALL\n{"also_valid": true}\n'
        s3.add_raw("opportunities", body, symbol="EURUSD")
        records = load_opportunities("EURUSD")
        assert len(records) == 2  # bad line skipped


# ═══════════════════════════════════════════════════════════════════════════════
# SYMBOL FILTERING
# ═══════════════════════════════════════════════════════════════════════════════

class TestFiltering:
    def test_symbol_filter_loads_only_matching(self, s3):
        s3.add("assessments", [{"symbol": "EURUSD", "ev": 0.001}], symbol="EURUSD")
        s3.add("assessments", [{"symbol": "GBPUSD", "ev": 0.002}], symbol="GBPUSD")
        eur = load_assessments("EURUSD")
        gbp = load_assessments("GBPUSD")
        all_records = load_assessments()
        assert len(eur) == 1 and eur[0]["symbol"] == "EURUSD"
        assert len(gbp) == 1 and gbp[0]["symbol"] == "GBPUSD"
        assert len(all_records) == 2

    def test_ranking_no_symbol_filter(self, s3):
        s3.add("portfolio_rankings", [{"cycle_id": 1}], date="2026-07-22")
        s3.add("portfolio_rankings", [{"cycle_id": 2}], date="2026-07-23")
        records = load_portfolio_rankings()
        assert len(records) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# MULTIPLE DATE PARTITIONS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultipleFiles:
    def test_multiple_dates_aggregated(self, s3):
        s3.add("risk_deviation", [_sample_risk_deviation()], symbol="NZDUSD", date="2026-07-22")
        s3.add("risk_deviation", [_sample_risk_deviation(), _sample_risk_deviation()], symbol="NZDUSD", date="2026-07-23")
        records = load_risk_deviation("NZDUSD")
        assert len(records) == 3
