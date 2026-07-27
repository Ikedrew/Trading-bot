"""
Tests for strategy/horizon separation across the data pipeline.

Validates that:
    1. Strategy and horizon are independent fields in ShadowTrade
    2. ShadowTrade persists both fields separately in truth record
    3. DecisionTrace persists trade_horizon
    4. Event stream exposes trade_horizon
    5. Research validator detects contaminated combined fields
    6. Existing trades remain backwards compatible (empty defaults)

No trading behaviour is tested or modified.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from core.shadow_trades import ShadowTradeEngine


# ═══════════════════════════════════════════════════════════════════════════════
# 1. STRATEGY AND HORIZON ARE INDEPENDENT FIELDS
# ═══════════════════════════════════════════════════════════════════════════════


class TestFieldIndependence:
    """Strategy and horizon are separate, non-combined fields."""

    def test_shadow_trade_stores_strategy_without_horizon(self):
        engine = ShadowTradeEngine(max_bars=5)
        engine.open_trade(
            trade_id="sep_1",
            cycle_id=100,
            symbol="EURUSD",
            direction="BUY",
            entry_price=1.10000,
            stop_loss=1.09900,
            take_profit=1.10200,
            entry_time=1000.0,
            pattern="HAMMER",
            score=0.7,
            strategy="REVERSAL",
            trade_horizon="SCALP",
        )

        closed = engine.evaluate_bar(
            symbol="EURUSD", bar_high=1.103, bar_low=1.098, bar_close=1.098, bar_time=1300.0,
        )

        record = closed[0]
        # strategy_id in identity should be clean (no horizon suffix)
        assert record["identity"]["strategy_id"] == "REVERSAL"
        assert "_SCALP" not in record["identity"]["strategy_id"]
        assert "_INTRADAY" not in record["identity"]["strategy_id"]

    def test_horizon_stored_separately_in_decision_snapshot(self):
        engine = ShadowTradeEngine(max_bars=5)
        engine.open_trade(
            trade_id="sep_2",
            cycle_id=200,
            symbol="GBPUSD",
            direction="SELL",
            entry_price=1.30000,
            stop_loss=1.30100,
            take_profit=1.29800,
            entry_time=2000.0,
            pattern="EVENING_STAR",
            score=0.8,
            strategy="REVERSAL",
            trade_horizon="INTRADAY",
        )

        closed = engine.evaluate_bar(
            symbol="GBPUSD", bar_high=1.302, bar_low=1.297, bar_close=1.301, bar_time=2300.0,
        )

        snapshot = closed[0]["decision_snapshot"]
        assert snapshot["trade_horizon"] == "INTRADAY"

    def test_strategy_and_horizon_independently_queryable(self):
        """Can filter by strategy OR horizon without parsing combined strings."""
        engine = ShadowTradeEngine(max_bars=5)

        # Create CONTINUATION + SCALP
        engine.open_trade(
            trade_id="sep_3a", cycle_id=300, symbol="AUDUSD",
            direction="BUY", entry_price=0.670, stop_loss=0.669, take_profit=0.672,
            entry_time=3000.0, pattern="THREE_WHITE_SOLDIERS", score=0.9,
            strategy="CONTINUATION", trade_horizon="SCALP",
        )
        # Create CONTINUATION + EXTENDED
        engine.open_trade(
            trade_id="sep_3b", cycle_id=301, symbol="AUDUSD",
            direction="BUY", entry_price=0.670, stop_loss=0.669, take_profit=0.674,
            entry_time=3100.0, pattern="THREE_WHITE_SOLDIERS", score=0.9,
            strategy="CONTINUATION", trade_horizon="EXTENDED",
        )

        closed = engine.evaluate_bar(
            symbol="AUDUSD", bar_high=0.675, bar_low=0.668, bar_close=0.668, bar_time=3400.0,
        )

        assert len(closed) == 2
        strategies = [r["identity"]["strategy_id"] for r in closed]
        horizons = [r["decision_snapshot"]["trade_horizon"] for r in closed]

        # Both have same strategy
        assert all(s == "CONTINUATION" for s in strategies)
        # But different horizons
        assert set(horizons) == {"SCALP", "EXTENDED"}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. DECISION TRACE STORES HORIZON
# ═══════════════════════════════════════════════════════════════════════════════


class TestDecisionTraceHorizon:
    """DecisionTrace stores trade_horizon independently."""

    def test_horizon_in_trace_when_provided(self):
        from core.decision_trace import build_decision_trace

        result = {
            "action": "NO_TRADE",
            "reason": "test",
            "score": 0.5,
            "entity_id": "EURUSD_1000",
            "symbol": "EURUSD",
            "cycle_id": 1,
            "components": {},
            "strategy": "FALSE_BREAK",
            "trade_horizon": "SCALP",
        }
        trace = build_decision_trace(engine_result=result)
        assert trace.trade_horizon == "SCALP"

    def test_horizon_in_to_dict(self):
        from core.decision_trace import build_decision_trace

        result = {
            "action": "NO_TRADE",
            "reason": "test",
            "score": 0.5,
            "entity_id": "X_0",
            "symbol": "X",
            "cycle_id": 0,
            "components": {},
            "trade_horizon": "EXTENDED",
        }
        trace = build_decision_trace(engine_result=result)
        d = trace.to_dict()
        assert d["trade_horizon"] == "EXTENDED"

    def test_horizon_defaults_to_none(self):
        from core.decision_trace import build_decision_trace

        result = {
            "action": "NO_TRADE",
            "reason": "test",
            "score": 0.0,
            "entity_id": "Y_0",
            "symbol": "Y",
            "cycle_id": 0,
            "components": {},
        }
        trace = build_decision_trace(engine_result=result)
        assert trace.trade_horizon is None


# ═══════════════════════════════════════════════════════════════════════════════
# 3. EVENT STREAM RESOLVES HORIZON
# ═══════════════════════════════════════════════════════════════════════════════


class TestEventStreamHorizon:
    """_resolve_trade_horizon extracts horizon from events."""

    def test_resolve_from_payload(self):
        from core.event_stream import _resolve_trade_horizon

        event = {"payload": {"trade_horizon": "SCALP"}}
        assert _resolve_trade_horizon(event) == "SCALP"

    def test_resolve_from_nested_data(self):
        from core.event_stream import _resolve_trade_horizon

        event = {"payload": {"data": {"trade_horizon": "INTRADAY"}}}
        assert _resolve_trade_horizon(event) == "INTRADAY"

    def test_resolve_returns_none_when_absent(self):
        from core.event_stream import _resolve_trade_horizon

        event = {"payload": {"strategy": "REVERSAL"}}
        assert _resolve_trade_horizon(event) is None


# ═══════════════════════════════════════════════════════════════════════════════
# 4. RESEARCH VALIDATOR DETECTS CONTAMINATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidatorContaminationDetection:
    """Research validator identifies combined strategy_horizon format."""

    def test_detects_combined_format(self):
        from research_engine.validation import validate_dataset

        records = [
            {"identity": {"strategy_id": "NONE_SCALP"}, "simulated_outcome": {"pnl_r_multiple": 1.0}}
            for _ in range(25)
        ]
        result = validate_dataset(records, dataset_name="contaminated")
        assert result.strategy_contaminated == 25
        contamination_warnings = [w for w in result.warnings if "contamination" in w.lower()]
        assert len(contamination_warnings) >= 1

    def test_clean_strategy_not_flagged(self):
        from research_engine.validation import validate_dataset

        records = [
            {"identity": {"strategy_id": "REVERSAL"}, "decision_snapshot": {"trade_horizon": "SCALP"},
             "simulated_outcome": {"pnl_r_multiple": 1.0}}
            for _ in range(25)
        ]
        result = validate_dataset(records, dataset_name="clean")
        assert result.strategy_contaminated == 0
        assert result.strategy_coverage.coverage_pct == 1.0
        assert result.horizon_coverage.coverage_pct == 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# 5. BACKWARD COMPATIBILITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    """Existing trades without separate horizon field work unchanged."""

    def test_legacy_trade_without_horizon(self):
        engine = ShadowTradeEngine(max_bars=5)
        engine.open_trade(
            trade_id="legacy_1",
            cycle_id=100,
            symbol="USDCHF",
            direction="BUY",
            entry_price=0.90000,
            stop_loss=0.89900,
            take_profit=0.90200,
            entry_time=5000.0,
            pattern="HAMMER",
            score=0.5,
            # No strategy, no trade_horizon passed
        )

        closed = engine.evaluate_bar(
            symbol="USDCHF", bar_high=0.903, bar_low=0.898, bar_close=0.898, bar_time=5300.0,
        )

        record = closed[0]
        # strategy_id is empty string (legacy default)
        assert record["identity"]["strategy_id"] == ""
        # trade_horizon defaults to None (empty → None in output)
        assert record["decision_snapshot"]["trade_horizon"] is None

    def test_legacy_combined_format_still_stored_in_strategy_id(self):
        """If caller passes combined format (old code path), it stores as-is."""
        engine = ShadowTradeEngine(max_bars=5)
        engine.open_trade(
            trade_id="compat_1",
            cycle_id=200,
            symbol="NZDUSD",
            direction="SELL",
            entry_price=0.580,
            stop_loss=0.581,
            take_profit=0.578,
            entry_time=6000.0,
            pattern="TWEEZER_TOP",
            score=0.6,
            strategy="REVERSAL_SCALP",  # Old combined format
            # No trade_horizon — caller uses old pattern
        )

        closed = engine.evaluate_bar(
            symbol="NZDUSD", bar_high=0.582, bar_low=0.577, bar_close=0.581, bar_time=6300.0,
        )

        record = closed[0]
        # strategy_id stores whatever was passed (backward compat)
        assert record["identity"]["strategy_id"] == "REVERSAL_SCALP"
        # trade_horizon is None (not populated by old callers)
        assert record["decision_snapshot"]["trade_horizon"] is None
