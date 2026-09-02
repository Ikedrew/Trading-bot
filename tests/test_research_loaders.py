"""
Tests for Phase 3A Research Engine Loaders.

Covers:
    - All 13 loaders (4 existing + 9 new)
    - Missing directory handling
    - Malformed records handling
    - Symbol filtering
    - Empty dataset handling
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _write_jsonl(path: Path, records: list[dict]) -> None:
    """Write records as JSONL to a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _sample_opportunity() -> dict:
    return {
        "opportunity_id": "EURUSD_1784800000_TWEEZER_TOP",
        "symbol": "EURUSD",
        "direction": "SELL",
        "pattern": "TWEEZER_TOP",
        "state": "REJECTED",
        "schema_version": "opportunity_v1",
        "cycle_id": 100,
        "entity_id": "EURUSD_1784800000",
    }


def _sample_assessment() -> dict:
    return {
        "assessment_id": "GBPUSD_1784809820_TWEEZER_TOP_assessment",
        "opportunity_id": "GBPUSD_1784809820_TWEEZER_TOP",
        "symbol": "GBPUSD",
        "schema_version": "assessment_v1",
        "score_strategy": 0.62,
        "ev": 0.000142,
        "cycle_id": 4578,
    }


def _sample_ranking() -> dict:
    return {
        "schema_version": "portfolio_ranking_v1",
        "ranking_id": "ranking_4578_1784809820123",
        "cycle_id": 4578,
        "total_candidates": 3,
        "selected_symbol": "GBPUSD",
        "candidates": [],
    }


def _sample_shadow() -> dict:
    return {
        "cycle_id": 4578,
        "agreement": False,
        "disagreement_type": "WRONG_SYMBOL",
        "actual_executed_symbols": ["NZDUSD"],
        "ranking_selected_symbol": "GBPUSD",
    }


def _sample_execution_result() -> dict:
    return {
        "symbol": "EURUSD",
        "result_ok": True,
        "retcode": 10009,
        "fill_price": 1.10005,
        "slippage": 0.00002,
        "decision_id": "abc123",
        "correlation_id": "COR-TEST",
    }


def _sample_execution_context() -> dict:
    return {
        "correlation_id": "COR-TEST",
        "symbol": "EURUSD",
        "timestamp_utc": 1784800000.0,
        "market_access": {"session_state": "LONDON", "spread": 0.00012},
    }


def _sample_protection() -> dict:
    return {
        "symbol": "GBPUSD",
        "position_ticket": 12345,
        "protection_status": "VERIFIED",
        "requested_sl": 1.33775,
        "broker_confirmed_sl": 1.33775,
    }


def _sample_risk_deviation() -> dict:
    return {
        "trade_id": "pos_100",
        "symbol": "NZDUSD",
        "planned_risk_R": -1.0,
        "actual_risk_R": -1.0,
        "risk_deviation": 1.0,
        "risk_classification": "NORMAL",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: LOADERS SUCCESS
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoadSuccess:
    """All loaders successfully read valid JSONL data."""

    def test_load_opportunities(self, tmp_path):
        _write_jsonl(tmp_path / "opportunities" / "EURUSD" / "2026-07-23.jsonl", [_sample_opportunity()])
        with patch("research_engine.data_access.loaders._get_logs_dir", return_value=tmp_path):
            records = load_opportunities("EURUSD")
        assert len(records) == 1
        assert records[0]["pattern"] == "TWEEZER_TOP"

    def test_load_assessments(self, tmp_path):
        _write_jsonl(tmp_path / "assessments" / "GBPUSD" / "2026-07-23.jsonl", [_sample_assessment()])
        with patch("research_engine.data_access.loaders._get_logs_dir", return_value=tmp_path):
            records = load_assessments("GBPUSD")
        assert len(records) == 1
        assert records[0]["ev"] == 0.000142

    def test_load_portfolio_rankings(self, tmp_path):
        _write_jsonl(tmp_path / "portfolio_rankings" / "2026-07-23.jsonl", [_sample_ranking()])
        with patch("research_engine.data_access.loaders._get_logs_dir", return_value=tmp_path):
            records = load_portfolio_rankings()
        assert len(records) == 1
        assert records[0]["selected_symbol"] == "GBPUSD"

    def test_load_shadow_comparisons(self, tmp_path):
        _write_jsonl(tmp_path / "portfolio_shadow" / "2026-07-23.jsonl", [_sample_shadow()])
        with patch("research_engine.data_access.loaders._get_logs_dir", return_value=tmp_path):
            records = load_shadow_comparisons()
        assert len(records) == 1
        assert records[0]["disagreement_type"] == "WRONG_SYMBOL"

    def test_load_execution_results(self, tmp_path):
        _write_jsonl(tmp_path / "execution_results" / "EURUSD" / "2026-07-23.jsonl", [_sample_execution_result()])
        with patch("research_engine.data_access.loaders._get_logs_dir", return_value=tmp_path):
            records = load_execution_results("EURUSD")
        assert len(records) == 1
        assert records[0]["result_ok"] is True

    def test_load_execution_context(self, tmp_path):
        _write_jsonl(tmp_path / "execution_context" / "EURUSD" / "2026-07-23.jsonl", [_sample_execution_context()])
        with patch("research_engine.data_access.loaders._get_logs_dir", return_value=tmp_path):
            records = load_execution_context("EURUSD")
        assert len(records) == 1
        assert records[0]["market_access"]["session_state"] == "LONDON"

    def test_load_protection_audit(self, tmp_path):
        _write_jsonl(tmp_path / "protection_audit" / "GBPUSD" / "2026-07-23.jsonl", [_sample_protection()])
        with patch("research_engine.data_access.loaders._get_logs_dir", return_value=tmp_path):
            records = load_protection_audit("GBPUSD")
        assert len(records) == 1
        assert records[0]["protection_status"] == "VERIFIED"

    def test_load_risk_deviation(self, tmp_path):
        _write_jsonl(tmp_path / "risk_deviation" / "NZDUSD" / "2026-07-23.jsonl", [_sample_risk_deviation()])
        with patch("research_engine.data_access.loaders._get_logs_dir", return_value=tmp_path):
            records = load_risk_deviation("NZDUSD")
        assert len(records) == 1
        assert records[0]["risk_classification"] == "NORMAL"

# NOTE (Production V1 cleanup): test_load_decision_audit removed — the
# decision_audit dataset and its loader are retired. Decision facts are read
# from decision_ledger / decision_trace loaders (tested elsewhere).


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: MISSING FILES / EMPTY
# ═══════════════════════════════════════════════════════════════════════════════

class TestMissingAndEmpty:
    """Loaders handle missing directories and empty datasets gracefully."""

    def test_missing_directory_returns_empty(self, tmp_path):
        with patch("research_engine.data_access.loaders._get_logs_dir", return_value=tmp_path):
            assert load_opportunities("EURUSD") == []
            assert load_assessments("GBPUSD") == []
            assert load_portfolio_rankings() == []
            assert load_shadow_comparisons() == []
            assert load_execution_results("EURUSD") == []
            assert load_execution_context("EURUSD") == []
            assert load_protection_audit("GBPUSD") == []
            assert load_risk_deviation("NZDUSD") == []

    def test_empty_file_returns_empty(self, tmp_path):
        (tmp_path / "opportunities" / "EURUSD").mkdir(parents=True)
        (tmp_path / "opportunities" / "EURUSD" / "2026-07-23.jsonl").write_text("")
        with patch("research_engine.data_access.loaders._get_logs_dir", return_value=tmp_path):
            assert load_opportunities("EURUSD") == []


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: MALFORMED RECORDS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMalformed:
    """Malformed records are skipped without crashing."""

    def test_malformed_line_skipped(self, tmp_path):
        content = '{"valid": true}\nNOT_JSON_AT_ALL\n{"also_valid": true}\n'
        p = tmp_path / "opportunities" / "EURUSD" / "2026-07-23.jsonl"
        p.parent.mkdir(parents=True)
        p.write_text(content)
        with patch("research_engine.data_access.loaders._get_logs_dir", return_value=tmp_path):
            records = load_opportunities("EURUSD")
        assert len(records) == 2  # Skipped the bad line


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: SYMBOL FILTERING
# ═══════════════════════════════════════════════════════════════════════════════

class TestFiltering:
    """Symbol filtering returns only matching data."""

    def test_symbol_filter_loads_only_matching(self, tmp_path):
        _write_jsonl(tmp_path / "assessments" / "EURUSD" / "2026-07-23.jsonl",
                     [{"symbol": "EURUSD", "ev": 0.001}])
        _write_jsonl(tmp_path / "assessments" / "GBPUSD" / "2026-07-23.jsonl",
                     [{"symbol": "GBPUSD", "ev": 0.002}])
        with patch("research_engine.data_access.loaders._get_logs_dir", return_value=tmp_path):
            eur = load_assessments("EURUSD")
            gbp = load_assessments("GBPUSD")
            all_records = load_assessments()
        assert len(eur) == 1
        assert eur[0]["symbol"] == "EURUSD"
        assert len(gbp) == 1
        assert gbp[0]["symbol"] == "GBPUSD"
        assert len(all_records) == 2

    def test_ranking_no_symbol_filter(self, tmp_path):
        """Portfolio rankings are cross-symbol — no symbol filter."""
        _write_jsonl(tmp_path / "portfolio_rankings" / "2026-07-22.jsonl", [{"cycle_id": 1}])
        _write_jsonl(tmp_path / "portfolio_rankings" / "2026-07-23.jsonl", [{"cycle_id": 2}])
        with patch("research_engine.data_access.loaders._get_logs_dir", return_value=tmp_path):
            records = load_portfolio_rankings()
        assert len(records) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: MULTIPLE FILES
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultipleFiles:
    """Loaders aggregate across multiple date files."""

    def test_multiple_dates_aggregated(self, tmp_path):
        _write_jsonl(tmp_path / "risk_deviation" / "NZDUSD" / "2026-07-22.jsonl",
                     [_sample_risk_deviation()])
        _write_jsonl(tmp_path / "risk_deviation" / "NZDUSD" / "2026-07-23.jsonl",
                     [_sample_risk_deviation(), _sample_risk_deviation()])
        with patch("research_engine.data_access.loaders._get_logs_dir", return_value=tmp_path):
            records = load_risk_deviation("NZDUSD")
        assert len(records) == 3
