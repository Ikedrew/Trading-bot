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
from pathlib import Path

import pytest

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

def _write_jsonl(path: Path, records: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _exe_record(trade_id, entity_id, r_mult, symbol="EURUSD"):
    return {
        "trade_id": trade_id,
        "execution": {
            "ticket": int(trade_id.replace("pos_", "")),
            "symbol": symbol, "direction": "BUY",
            "entry_price": 1.1, "exit_price": 1.11, "entry_time": 1000,
            "exit_time": 2000, "stop_loss": 1.09, "take_profit": 1.12,
            "gross_profit": 10, "commission": -1, "swap": 0,
            "net_realised_pnl": 9, "r_multiple": r_mult,
            "volume": 0.1, "duration_seconds": 1000, "exit_reason": "TAKE_PROFIT",
        },
        "decision": {"strategy": "", "score": 70, "confidence": 0.8,
                     "decision_type": "EXECUTE", "decision_timestamp": 999,
                     "components": {}, "weakest_component": "", "ev": None, "p_success": None},
        "market": {"regime": "TRENDING", "session": "LONDON", "volatility": "",
                   "trend_state": "", "higher_timeframe_bias": "",
                   "h4_phase": "", "h1_clarity": 0},
        "strategy": {"family": "TREND_CONTINUATION", "pattern": "ENGULFING",
                     "conditions_met": 3, "strategy_confidence": 0.7,
                     "opportunity_quality": 0.8, "opportunity_type": ""},
        "quality": {"anomaly": False, "anomaly_reasons": [],
                    "governance_status": "VALID", "data_completeness": "FULL",
                    "missing": [], "join_method": "", "pnl_source": ""},
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


def _build_test_setup(tmp_path):
    """Build a test setup with matching execution and decision records."""
    # Execution universe with 3 trades, entity_ids enriched
    exe_path = tmp_path / "exe.jsonl"
    _write_jsonl(exe_path, [
        _exe_record("pos_100", "EURUSD_1000", 1.5),
        _exe_record("pos_200", "EURUSD_2000", -1.0),
        _exe_record("pos_300", "GBPUSD_3000", 0.5, symbol="GBPUSD"),
    ])

    # Execution results (for entity_id enrichment)
    er_dir = tmp_path / "exec_results" / "EURUSD"
    er_dir.mkdir(parents=True)
    _write_jsonl(er_dir / "data.jsonl", [
        {"deal": 100, "entity_id": "EURUSD_1000", "result_ok": True, "comment": "Request executed"},
        {"deal": 200, "entity_id": "EURUSD_2000", "result_ok": True, "comment": "Request executed"},
    ])
    er_dir2 = tmp_path / "exec_results" / "GBPUSD"
    er_dir2.mkdir(parents=True)
    _write_jsonl(er_dir2 / "data.jsonl", [
        {"deal": 300, "entity_id": "GBPUSD_3000", "result_ok": True, "comment": "Request executed"},
    ])

    # Decision traces: 3 EXECUTE (matching) + 2 NO_TRADE (no match)
    dt_dir = tmp_path / "dt" / "EURUSD"
    dt_dir.mkdir(parents=True)
    _write_jsonl(dt_dir / "data.jsonl", [
        _dec_trace("EURUSD_1000", "EXECUTE"),
        _dec_trace("EURUSD_2000", "EXECUTE"),
        _dec_trace("EURUSD_5000", "NO_TRADE"),
        _dec_trace("EURUSD_6000", "NO_TRADE"),
    ])
    dt_dir2 = tmp_path / "dt" / "GBPUSD"
    dt_dir2.mkdir(parents=True)
    _write_jsonl(dt_dir2 / "data.jsonl", [
        _dec_trace("GBPUSD_3000", "EXECUTE", "GBPUSD"),
    ])

    # Build execution universe
    exe_builder = ExecutionUniverseBuilder(
        source_path=exe_path,
        execution_results_dir=tmp_path / "exec_results",
    )
    exe_builder.build()

    # Build decision universe
    dec_builder = DecisionUniverseBuilder(source_dir=tmp_path / "dt")
    dec_builder.build()

    return exe_builder, dec_builder


# ═══════════════════════════════════════════════════════════════════════════════
# CORE ENRICHMENT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestOutcomeEnrichment:

    def test_matched_records_get_r_multiple(self, tmp_path):
        exe, dec = _build_test_setup(tmp_path)
        enrichment = OutcomeEnrichment(exe)
        result = enrichment.enrich(dec)

        # 3 EXECUTE decisions should match
        matched = [r for r in dec.records if r.get("execution_match")]
        assert result.matched == 3
        assert all(r["r_multiple"] is not None for r in matched)

    def test_no_trade_does_not_get_outcome(self, tmp_path):
        exe, dec = _build_test_setup(tmp_path)
        enrichment = OutcomeEnrichment(exe)
        enrichment.enrich(dec)

        no_trades = [r for r in dec.records if r.get("action") == "NO_TRADE"]
        assert len(no_trades) == 2
        for r in no_trades:
            assert r["execution_match"] is False
            assert r["outcome_available"] is False
            assert r["r_multiple"] is None

    def test_exact_r_multiple_preserved(self, tmp_path):
        exe, dec = _build_test_setup(tmp_path)
        enrichment = OutcomeEnrichment(exe)
        enrichment.enrich(dec)

        eu1000 = next(r for r in dec.records if r.get("entity_id") == "EURUSD_1000")
        eu2000 = next(r for r in dec.records if r.get("entity_id") == "EURUSD_2000")
        gb3000 = next(r for r in dec.records if r.get("entity_id") == "GBPUSD_3000")

        assert eu1000["r_multiple"] == 1.5
        assert eu2000["r_multiple"] == -1.0
        assert gb3000["r_multiple"] == 0.5

    def test_execution_id_provenance(self, tmp_path):
        exe, dec = _build_test_setup(tmp_path)
        enrichment = OutcomeEnrichment(exe)
        enrichment.enrich(dec)

        eu1000 = next(r for r in dec.records if r.get("entity_id") == "EURUSD_1000")
        assert eu1000["execution_id"] == "pos_100"
        assert eu1000["exit_reason"] == "TAKE_PROFIT"

    def test_enrichment_result_counts(self, tmp_path):
        exe, dec = _build_test_setup(tmp_path)
        enrichment = OutcomeEnrichment(exe)
        result = enrichment.enrich(dec)

        assert result.universe == "DECISION"
        assert result.total_records == 5
        assert result.matched == 3
        assert result.unmatched == 2

    def test_duplicate_match_protection(self, tmp_path):
        """First match wins — no duplicate outcome assignment."""
        exe_path = tmp_path / "exe.jsonl"
        _write_jsonl(exe_path, [
            _exe_record("pos_100", "EURUSD_1000", 1.5),
            _exe_record("pos_101", "EURUSD_1000", 2.0),  # Duplicate entity_id!
        ])
        er_dir = tmp_path / "exec_results" / "EURUSD"
        er_dir.mkdir(parents=True)
        _write_jsonl(er_dir / "data.jsonl", [
            {"deal": 100, "entity_id": "EURUSD_1000", "result_ok": True, "comment": "Request executed"},
            {"deal": 101, "entity_id": "EURUSD_1000", "result_ok": True, "comment": "Request executed"},
        ])

        exe = ExecutionUniverseBuilder(
            source_path=exe_path,
            execution_results_dir=tmp_path / "exec_results",
        )
        exe.build()

        enrichment = OutcomeEnrichment(exe)
        # First match (pos_100, r=1.5) wins
        assert enrichment._outcome_lookup.get("EURUSD_1000", {}).get("r_multiple") == 1.5

    def test_enrichment_is_deterministic(self, tmp_path):
        """Same data → same enrichment."""
        exe, dec = _build_test_setup(tmp_path)

        e1 = OutcomeEnrichment(exe)
        e1.enrich(dec)
        first_values = [(r["entity_id"], r.get("r_multiple")) for r in dec.records]

        # Rebuild and enrich again
        dec2 = DecisionUniverseBuilder(source_dir=tmp_path / "dt")
        dec2.build()
        e2 = OutcomeEnrichment(exe)
        e2.enrich(dec2)
        second_values = [(r["entity_id"], r.get("r_multiple")) for r in dec2.records]

        assert first_values == second_values

    def test_execution_universe_unchanged(self, tmp_path):
        """Enrichment of other universes doesn't modify Execution Universe."""
        exe, dec = _build_test_setup(tmp_path)
        original_count = len(exe.records)
        original_r = [r["r_multiple"] for r in exe.records]

        enrichment = OutcomeEnrichment(exe)
        enrichment.enrich(dec)

        assert len(exe.records) == original_count
        assert [r["r_multiple"] for r in exe.records] == original_r


# ═══════════════════════════════════════════════════════════════════════════════
# REAL DATA DIAGNOSTIC
# ═══════════════════════════════════════════════════════════════════════════════


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
