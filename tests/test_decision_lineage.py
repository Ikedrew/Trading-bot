"""
Tests for Decision → ShadowTrade → Outcome lineage chain.

Validates that:
    1. entity_id flows from decision to shadow trade to outcome record
    2. Multiple decisions on same symbol cannot cross-contaminate
    3. Missing lineage is detectable by the research validator
    4. Decision context fields (regime, h1_bias, strategy) persist in truth record

No trading behaviour is tested or modified.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from core.shadow_trades import ShadowTradeEngine


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1: entity_id propagates through full lifecycle
# ═══════════════════════════════════════════════════════════════════════════════


class TestEntityIdPropagation:
    """entity_id flows from open_trade → truth record → identity domain."""

    def test_entity_id_in_truth_record(self):
        engine = ShadowTradeEngine(max_bars=5)
        engine.open_trade(
            trade_id="lineage_test_1",
            cycle_id=500,
            symbol="EURUSD",
            direction="BUY",
            entry_price=1.10000,
            stop_loss=1.09900,
            take_profit=1.10200,
            entry_time=1000.0,
            pattern="HAMMER",
            score=0.7,
            entity_id="EURUSD_1000",
        )

        closed = engine.evaluate_bar(
            symbol="EURUSD",
            bar_high=1.10300,
            bar_low=1.09800,
            bar_close=1.09850,
            bar_time=1300.0,
        )

        assert len(closed) == 1
        record = closed[0]
        assert record["identity"]["entity_id"] == "EURUSD_1000"

    def test_entity_id_enables_join(self):
        """Same entity_id in trace and shadow trade enables deterministic join."""
        entity_id = "GBPUSD_2000"

        engine = ShadowTradeEngine(max_bars=5)
        engine.open_trade(
            trade_id="lineage_join_1",
            cycle_id=200,
            symbol="GBPUSD",
            direction="SELL",
            entry_price=1.30000,
            stop_loss=1.30100,
            take_profit=1.29800,
            entry_time=2000.0,
            pattern="EVENING_STAR",
            score=0.8,
            entity_id=entity_id,
        )

        closed = engine.evaluate_bar(
            symbol="GBPUSD",
            bar_high=1.30200,
            bar_low=1.29700,
            bar_close=1.30150,
            bar_time=2300.0,
        )

        assert len(closed) == 1
        # This entity_id is the JOIN KEY to DecisionTrace
        assert closed[0]["identity"]["entity_id"] == entity_id

    def test_entity_id_defaults_to_none_when_empty(self):
        """Legacy shadow trades without entity_id get None in output."""
        engine = ShadowTradeEngine(max_bars=5)
        engine.open_trade(
            trade_id="legacy_1",
            cycle_id=100,
            symbol="USDJPY",
            direction="BUY",
            entry_price=150.000,
            stop_loss=149.900,
            take_profit=150.200,
            entry_time=3000.0,
            pattern="HAMMER",
            score=0.5,
            # No entity_id passed
        )

        closed = engine.evaluate_bar(
            symbol="USDJPY",
            bar_high=150.300,
            bar_low=149.800,
            bar_close=149.850,
            bar_time=3300.0,
        )

        assert len(closed) == 1
        # Empty string → None in output (or empty)
        assert closed[0]["identity"]["entity_id"] in (None, "")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: Multiple decisions cannot cross-contaminate
# ═══════════════════════════════════════════════════════════════════════════════


class TestLinageIsolation:
    """Multiple shadow trades on same symbol have independent lineage."""

    def test_different_entity_ids_stay_separate(self):
        engine = ShadowTradeEngine(max_bars=5)

        # Trade A: EURUSD at bar_time=1000
        engine.open_trade(
            trade_id="iso_A",
            cycle_id=100,
            symbol="EURUSD",
            direction="BUY",
            entry_price=1.10000,
            stop_loss=1.09900,
            take_profit=1.10200,
            entry_time=1000.0,
            pattern="HAMMER",
            score=0.6,
            entity_id="EURUSD_1000",
            regime="TRENDING",
        )

        # Trade B: EURUSD at bar_time=1300 (different decision)
        engine.open_trade(
            trade_id="iso_B",
            cycle_id=101,
            symbol="EURUSD",
            direction="SELL",
            entry_price=1.10100,
            stop_loss=1.10200,
            take_profit=1.09900,
            entry_time=1300.0,
            pattern="EVENING_STAR",
            score=0.7,
            entity_id="EURUSD_1300",
            regime="RANGE",
        )

        # Close both
        closed = engine.evaluate_bar(
            symbol="EURUSD",
            bar_high=1.10300,
            bar_low=1.09800,
            bar_close=1.09850,
            bar_time=1600.0,
        )

        assert len(closed) == 2

        # Find each by trade_id
        record_a = next(r for r in closed if r["identity"]["trade_id"] == "iso_A")
        record_b = next(r for r in closed if r["identity"]["trade_id"] == "iso_B")

        # Lineage is isolated
        assert record_a["identity"]["entity_id"] == "EURUSD_1000"
        assert record_b["identity"]["entity_id"] == "EURUSD_1300"

        # Decision context is isolated
        assert record_a["decision_snapshot"]["regime"] == "TRENDING"
        assert record_b["decision_snapshot"]["regime"] == "RANGE"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: Missing lineage detected by research validator
# ═══════════════════════════════════════════════════════════════════════════════


class TestLineageValidation:
    """Research validator detects missing lineage correctly."""

    def test_records_with_entity_id_have_high_lineage(self):
        from research_engine.validation import validate_dataset

        records = [
            {"identity": {"entity_id": f"EURUSD_{i*100}", "trade_id": f"t{i}"}, "simulated_outcome": {"pnl_r_multiple": 1.0}}
            for i in range(25)
        ]
        result = validate_dataset(records, dataset_name="shadow_with_lineage")
        assert result.lineage_coverage.coverage_pct == 1.0

    def test_records_without_lineage_have_zero_coverage(self):
        from research_engine.validation import validate_dataset

        records = [
            {"identity": {"trade_id": f"t{i}"}, "simulated_outcome": {"pnl_r_multiple": 1.0}}
            for i in range(25)
        ]
        result = validate_dataset(records, dataset_name="shadow_no_lineage")
        assert result.lineage_coverage.coverage_pct == 0.0

    def test_horizon_prefix_counted_as_unjoinable(self):
        from research_engine.validation import validate_dataset

        records = [
            {"identity": {"trade_id": f"t{i}", "correlation_id": f"HORIZON-100-EURUSD"}, "simulated_outcome": {"pnl_r_multiple": 0.5}}
            for i in range(25)
        ]
        result = validate_dataset(records, dataset_name="shadow_horizon_only")
        # HORIZON- ids are populated but not joinable (counted as unknown)
        assert result.lineage_coverage.populated_pct == 1.0
        assert result.lineage_coverage.coverage_pct == 0.0

    def test_lineage_warning_when_low(self):
        from research_engine.validation import validate_dataset

        records = [
            {"identity": {"trade_id": f"t{i}"}, "simulated_outcome": {"pnl_r_multiple": 1.0}}
            for i in range(25)
        ]
        result = validate_dataset(records, dataset_name="shadow_no_ids")
        lineage_warnings = [w for w in result.warnings if "lineage" in w.lower()]
        assert len(lineage_warnings) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4: Decision context fields persist in truth record
# ═══════════════════════════════════════════════════════════════════════════════


class TestDecisionContextPersistence:
    """Regime, h4_regime, h1_bias, market_phase all persist in outcome record."""

    def test_all_context_fields_in_decision_snapshot(self):
        engine = ShadowTradeEngine(max_bars=5)
        engine.open_trade(
            trade_id="ctx_test_1",
            cycle_id=300,
            symbol="AUDUSD",
            direction="BUY",
            entry_price=0.67000,
            stop_loss=0.66900,
            take_profit=0.67200,
            entry_time=4000.0,
            pattern="THREE_WHITE_SOLDIERS",
            score=0.9,
            entity_id="AUDUSD_4000",
            regime="TRENDING",
            h4_regime="TRENDING",
            h1_bias="BULLISH",
            market_phase="IMPULSE",
            market_phase_confidence=0.85,
        )

        closed = engine.evaluate_bar(
            symbol="AUDUSD",
            bar_high=0.67300,
            bar_low=0.66850,
            bar_close=0.66880,
            bar_time=4300.0,
        )

        assert len(closed) == 1
        snapshot = closed[0]["decision_snapshot"]

        assert snapshot["pattern"] == "THREE_WHITE_SOLDIERS"
        assert snapshot["regime"] == "TRENDING"
        assert snapshot["h4_regime"] == "TRENDING"
        assert snapshot["h1_bias"] == "BULLISH"
        assert snapshot["market_phase"] == "IMPULSE"
        assert snapshot["market_phase_confidence"] == 0.85

    def test_missing_context_fields_are_none(self):
        """Legacy trades without context fields output None gracefully."""
        engine = ShadowTradeEngine(max_bars=5)
        engine.open_trade(
            trade_id="ctx_legacy_1",
            cycle_id=400,
            symbol="NZDUSD",
            direction="SELL",
            entry_price=0.58000,
            stop_loss=0.58100,
            take_profit=0.57800,
            entry_time=5000.0,
            pattern="TWEEZER_TOP",
            score=0.5,
        )

        closed = engine.evaluate_bar(
            symbol="NZDUSD",
            bar_high=0.58200,
            bar_low=0.57700,
            bar_close=0.58150,
            bar_time=5300.0,
        )

        assert len(closed) == 1
        snapshot = closed[0]["decision_snapshot"]

        assert snapshot["regime"] is None
        assert snapshot["h4_regime"] is None
        assert snapshot["h1_bias"] is None
        assert snapshot["market_phase"] is None
