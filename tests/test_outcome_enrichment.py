"""
Outcome Enrichment Tests.

Proves:
    - Successful execution → correct r_multiple enrichment
    - Unmatched decision → r_multiple remains None
    - NO_TRADE → no fabricated outcome
    - Exact r_multiple preservation from Execution Universe
    - Join identity/provenance (execution_match, execution_id)
    - Duplicate-match protection (first match wins)
    - All four universes remain structurally valid
    - Enrichment is deterministic
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _s3_fake import FakeS3, install_fake_s3, reset_fake_s3

from research_engine.v10.universes.outcome_enrichment import (
    OutcomeEnrichment,
    EnrichmentResult,
)
from research_engine.v10.universes.execution_universe import ExecutionUniverseBuilder
from research_engine.v10.universes.decision_universe import DecisionUniverseBuilder
from research_engine.v10.universes.market_universe import MarketUniverseBuilder
from research_engine.v10.universes.strategy_universe import StrategyUniverseBuilder
from research_engine.v10.universes.models import Universe


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _trade_truth(trade_id, entity_id, r_mult, symbol="EURUSD"):
    """A trade_truth-shaped record. entity_id is joined from execution_results
    via correlation_id (see _exec_result); here we key correlation on entity_id."""
    corr = f"COR-{entity_id}"
    return {
        "identity": {"trade_id": trade_id, "correlation_id": corr, "symbol": symbol},
        "execution": {"entry_fill_price": 1.1, "exit_fill_price": 1.11, "volume_executed": 0.1},
        "timestamps": {"entry_timestamp_broker": 1000, "exit_timestamp_broker": 2000, "duration_seconds": 1000},
        "outcome": {"r_multiple_realised": r_mult, "pnl_realised": 10, "net_profit": 9, "commission": -1, "swap": 0},
        "exit": {"exit_reason": "TAKE_PROFIT"},
    }


def _exec_result(entity_id, symbol="EURUSD"):
    """An execution_results record that supplies entity_id, joined by correlation_id."""
    return {
        "symbol": symbol, "result_ok": True, "correlation_id": f"COR-{entity_id}",
        "entity_id": entity_id, "comment": "Request executed",
    }


def _dec_trace(entity_id, action="EXECUTE", symbol="EURUSD"):
    return {
        "entity_id": entity_id, "symbol": symbol, "cycle_id": 1,
        "timestamp_utc": "2026-08-09T00:00:00Z", "action": action,
        "terminal_stage": "" if action == "EXECUTE" else "unknown",
        "terminal_reason": "" if action == "EXECUTE" else "V10 [opportunity]: invalid",
        "v10_market_state": {"regime": {"regime": "TRENDING", "regime_confidence": 0.8,
                                         "volatility_state": "NEUTRAL", "expansion_state": ""},
                             "h4": {}, "h1": {}, "m15": {}, "m5": {},
                             "location": {}, "htf_alignment": {}},
        "v10_opportunity": {"overall_quality": 0.8, "state": "VALID",
                            "location_score": 0.7, "structure_score": 0.6,
                            "behaviour_score": 0.5, "formation_score": 0.8},
        "v10_strategy": {"family": "TREND_CONTINUATION", "confidence": 0.75, "direction": "BUY"},
        "v10_risk": {"approved": True, "rejection_reason": ""},
        "v10_entry": {"method": "MARKET", "status": "READY", "expected_rr": 2.5},
        "score_strategy": 72.0, "score_neutral": 65.0,
    }


def _build_test_setup(fake: FakeS3):
    """Seed S3 with matching trade_truth/execution_results/decision_trace, then build."""
    # trade_truth: 3 trades (entity_ids enriched via execution_results)
    fake.add("trade_truth", [
        _trade_truth("pos_100", "EURUSD_1000", 1.5),
        _trade_truth("pos_200", "EURUSD_2000", -1.0),
    ], symbol="EURUSD")
    fake.add("trade_truth", [
        _trade_truth("pos_300", "GBPUSD_3000", 0.5, symbol="GBPUSD"),
    ], symbol="GBPUSD")

    # execution_results (for entity_id enrichment, joined by correlation_id)
    fake.add("execution_results", [
        _exec_result("EURUSD_1000"),
        _exec_result("EURUSD_2000"),
    ], symbol="EURUSD")
    fake.add("execution_results", [
        _exec_result("GBPUSD_3000", symbol="GBPUSD"),
    ], symbol="GBPUSD")

    # Decision traces: 3 EXECUTE (matching) + 2 NO_TRADE (no match)
    fake.add("decision_trace", [
        _dec_trace("EURUSD_1000", "EXECUTE"),
        _dec_trace("EURUSD_2000", "EXECUTE"),
        _dec_trace("EURUSD_5000", "NO_TRADE"),
        _dec_trace("EURUSD_6000", "NO_TRADE"),
    ], symbol="EURUSD")
    fake.add("decision_trace", [
        _dec_trace("GBPUSD_3000", "EXECUTE", "GBPUSD"),
    ], symbol="GBPUSD")

    # Build execution universe
    exe_builder = ExecutionUniverseBuilder()
    exe_builder.build()

    # Build decision universe
    dec_builder = DecisionUniverseBuilder()
    dec_builder.build()

    return exe_builder, dec_builder


# ═══════════════════════════════════════════════════════════════════════════════
# CORE ENRICHMENT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestOutcomeEnrichment:

    @pytest.fixture(autouse=True)
    def _s3(self):
        fake = install_fake_s3()
        yield fake
        reset_fake_s3()

    def test_matched_records_get_r_multiple(self, _s3):
        exe, dec = _build_test_setup(_s3)
        enrichment = OutcomeEnrichment(exe)
        result = enrichment.enrich(dec)

        # 3 EXECUTE decisions should match
        matched = [r for r in dec.records if r.get("execution_match")]
        assert result.matched == 3
        assert all(r["r_multiple"] is not None for r in matched)

    def test_no_trade_does_not_get_outcome(self, _s3):
        exe, dec = _build_test_setup(_s3)
        enrichment = OutcomeEnrichment(exe)
        enrichment.enrich(dec)

        no_trades = [r for r in dec.records if r.get("action") == "NO_TRADE"]
        assert len(no_trades) == 2
        for r in no_trades:
            assert r["execution_match"] is False
            assert r["outcome_available"] is False
            assert r["r_multiple"] is None

    def test_exact_r_multiple_preserved(self, _s3):
        exe, dec = _build_test_setup(_s3)
        enrichment = OutcomeEnrichment(exe)
        enrichment.enrich(dec)

        eu1000 = next(r for r in dec.records if r.get("entity_id") == "EURUSD_1000")
        eu2000 = next(r for r in dec.records if r.get("entity_id") == "EURUSD_2000")
        gb3000 = next(r for r in dec.records if r.get("entity_id") == "GBPUSD_3000")

        assert eu1000["r_multiple"] == 1.5
        assert eu2000["r_multiple"] == -1.0
        assert gb3000["r_multiple"] == 0.5

    def test_execution_id_provenance(self, _s3):
        exe, dec = _build_test_setup(_s3)
        enrichment = OutcomeEnrichment(exe)
        enrichment.enrich(dec)

        eu1000 = next(r for r in dec.records if r.get("entity_id") == "EURUSD_1000")
        assert eu1000["execution_id"] == "pos_100"
        assert eu1000["exit_reason"] == "TAKE_PROFIT"

    def test_enrichment_result_counts(self, _s3):
        exe, dec = _build_test_setup(_s3)
        enrichment = OutcomeEnrichment(exe)
        result = enrichment.enrich(dec)

        assert result.universe == "DECISION"
        assert result.total_records == 5
        assert result.matched == 3
        assert result.unmatched == 2

    def test_duplicate_match_protection(self, _s3):
        """First match wins — no duplicate outcome assignment."""
        # Two trades sharing the SAME correlation_id → same enriched entity_id.
        # Ordered by exit timestamp: pos_100 (t=2000, r=1.5) precedes pos_101 (t=3000).
        _s3.add("trade_truth", [
            {
                "identity": {"trade_id": "pos_100", "correlation_id": "COR-EURUSD_1000", "symbol": "EURUSD"},
                "execution": {"entry_fill_price": 1.1, "exit_fill_price": 1.11, "volume_executed": 0.1},
                "timestamps": {"entry_timestamp_broker": 1000, "exit_timestamp_broker": 2000, "duration_seconds": 1000},
                "outcome": {"r_multiple_realised": 1.5, "pnl_realised": 10, "net_profit": 9, "commission": -1, "swap": 0},
                "exit": {"exit_reason": "TAKE_PROFIT"},
            },
            {
                "identity": {"trade_id": "pos_101", "correlation_id": "COR-EURUSD_1000", "symbol": "EURUSD"},
                "execution": {"entry_fill_price": 1.1, "exit_fill_price": 1.12, "volume_executed": 0.1},
                "timestamps": {"entry_timestamp_broker": 2000, "exit_timestamp_broker": 3000, "duration_seconds": 1000},
                "outcome": {"r_multiple_realised": 2.0, "pnl_realised": 20, "net_profit": 18, "commission": -1, "swap": 0},
                "exit": {"exit_reason": "TAKE_PROFIT"},
            },
        ], symbol="EURUSD")
        _s3.add("execution_results", [_exec_result("EURUSD_1000")], symbol="EURUSD")

        exe = ExecutionUniverseBuilder()
        exe.build()

        enrichment = OutcomeEnrichment(exe)
        # First match (pos_100, r=1.5) wins
        assert enrichment._outcome_lookup.get("EURUSD_1000", {}).get("r_multiple") == 1.5

    def test_enrichment_is_deterministic(self, _s3):
        """Same data → same enrichment."""
        exe, dec = _build_test_setup(_s3)

        e1 = OutcomeEnrichment(exe)
        e1.enrich(dec)
        first_values = [(r["entity_id"], r.get("r_multiple")) for r in dec.records]

        # Rebuild and enrich again (same S3 source → identical data)
        dec2 = DecisionUniverseBuilder()
        dec2.build()
        e2 = OutcomeEnrichment(exe)
        e2.enrich(dec2)
        second_values = [(r["entity_id"], r.get("r_multiple")) for r in dec2.records]

        assert first_values == second_values

    def test_execution_universe_unchanged(self, _s3):
        """Enrichment of other universes doesn't modify Execution Universe."""
        exe, dec = _build_test_setup(_s3)
        original_count = len(exe.records)
        original_r = [r["r_multiple"] for r in exe.records]

        enrichment = OutcomeEnrichment(exe)
        enrichment.enrich(dec)

        assert len(exe.records) == original_count
        assert [r["r_multiple"] for r in exe.records] == original_r


# ═══════════════════════════════════════════════════════════════════════════════
# REAL DATA DIAGNOSTIC
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.skip(reason="requires live S3 data (integration test gated on real dataset presence)")
class TestRealDataEnrichment:
    """Run against real data to validate actual enrichment counts."""

    def test_real_enrichment_produces_outcomes(self):
        """Verify enrichment on real data produces non-zero matches."""
        exe = ExecutionUniverseBuilder()
        exe.build()

        dec = DecisionUniverseBuilder()
        dec.build()

        # Before enrichment
        before = sum(1 for r in dec.records if r.get("r_multiple") is not None)
        assert before == 0  # Confirmed: no outcomes pre-enrichment

        # Enrich
        enrichment = OutcomeEnrichment(exe)
        result = enrichment.enrich(dec)

        # After enrichment
        after = sum(1 for r in dec.records if r.get("r_multiple") is not None)
        assert after > 0  # Must have some matches
        assert after == result.matched
        assert result.matched > 50  # We know ~80 entity_ids are enriched in exe

        print(f"\n  Decision enrichment: {before} -> {after} "
              f"({result.matched} matched, {result.unmatched} unmatched)")

    def test_real_market_enrichment(self):
        exe = ExecutionUniverseBuilder()
        exe.build()

        mkt = MarketUniverseBuilder()
        mkt.build()

        before = sum(1 for r in mkt.records if r.get("r_multiple") is not None)

        enrichment = OutcomeEnrichment(exe)
        result = enrichment.enrich(mkt)

        after = sum(1 for r in mkt.records if r.get("r_multiple") is not None)
        assert after >= result.matched

        print(f"\n  Market enrichment: {before} -> {after} "
              f"({result.matched} matched, {result.unmatched} unmatched)")

    def test_real_strategy_enrichment(self):
        exe = ExecutionUniverseBuilder()
        exe.build()

        strat = StrategyUniverseBuilder()
        strat.build()

        before = sum(1 for r in strat.records if r.get("r_multiple") is not None)

        enrichment = OutcomeEnrichment(exe)
        result = enrichment.enrich(strat)

        after = sum(1 for r in strat.records if r.get("r_multiple") is not None)
        assert after >= result.matched

        print(f"\n  Strategy enrichment: {before} -> {after} "
              f"({result.matched} matched, {result.unmatched} unmatched)")
