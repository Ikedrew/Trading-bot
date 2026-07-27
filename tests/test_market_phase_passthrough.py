"""
Tests for market_phase passthrough — observability only, no behaviour change.

Validates that MarketContext.phase flows through:
    1. DecisionTrace (with and without phase)
    2. Shadow trade snapshot
    3. Event stream resolution
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# 1. DECISION TRACE — PHASE POPULATED
# ═══════════════════════════════════════════════════════════════════════════════


class TestDecisionTracePhasePopulated:
    """When engine_result contains market_phase, it appears in DecisionTrace."""

    def test_phase_in_trace_when_provided(self):
        from core.decision_trace import build_decision_trace

        result = {
            "action": "NO_TRADE",
            "reason": "score_below_threshold",
            "score": 0.3,
            "entity_id": "EURUSD_1000",
            "symbol": "EURUSD",
            "cycle_id": 1,
            "components": {},
            "strategy": "REVERSAL",
            "strategy_confidence": 0.6,
            "market_phase": "IMPULSE",
            "market_phase_confidence": 0.77,
        }
        trace = build_decision_trace(engine_result=result)
        assert trace.market_phase == "IMPULSE"
        assert trace.market_phase_confidence == 0.77

    def test_phase_in_to_dict(self):
        from core.decision_trace import build_decision_trace

        result = {
            "action": "NO_TRADE",
            "reason": "no_viable_pattern",
            "score": 0.0,
            "entity_id": "GBPUSD_2000",
            "symbol": "GBPUSD",
            "cycle_id": 5,
            "components": {},
            "market_phase": "EXHAUSTION",
            "market_phase_confidence": 0.4,
        }
        trace = build_decision_trace(engine_result=result)
        d = trace.to_dict()
        assert d["market_phase"] == "EXHAUSTION"
        assert d["market_phase_confidence"] == 0.4


# ═══════════════════════════════════════════════════════════════════════════════
# 2. DECISION TRACE — PHASE ABSENT (backward compat)
# ═══════════════════════════════════════════════════════════════════════════════


class TestDecisionTracePhaseAbsent:
    """When engine_result has no market_phase, trace defaults gracefully."""

    def test_phase_defaults_to_none(self):
        from core.decision_trace import build_decision_trace

        result = {
            "action": "NO_TRADE",
            "reason": "no_viable_pattern",
            "score": 0.0,
            "entity_id": "USDJPY_3000",
            "symbol": "USDJPY",
            "cycle_id": 10,
            "components": {},
        }
        trace = build_decision_trace(engine_result=result)
        assert trace.market_phase is None
        assert trace.market_phase_confidence == 0.0

    def test_to_dict_includes_none_phase(self):
        from core.decision_trace import build_decision_trace

        result = {
            "action": "NO_TRADE",
            "reason": "test",
            "score": 0.0,
            "entity_id": "X_0",
            "symbol": "X",
            "cycle_id": 0,
            "components": {},
        }
        trace = build_decision_trace(engine_result=result)
        d = trace.to_dict()
        assert "market_phase" in d
        assert d["market_phase"] is None
        assert d["market_phase_confidence"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SHADOW TRADE — PHASE PERSISTED
# ═══════════════════════════════════════════════════════════════════════════════


class TestShadowTradePhase:
    """Shadow trade records include market_phase when provided."""

    def test_phase_in_shadow_trade_record(self):
        from core.shadow_trades import ShadowTradeEngine

        engine = ShadowTradeEngine(max_bars=5)
        engine.open_trade(
            trade_id="phase_test_1",
            cycle_id=100,
            symbol="EURUSD",
            direction="BUY",
            entry_price=1.10000,
            stop_loss=1.09900,
            take_profit=1.10200,
            entry_time=1000.0,
            pattern="HAMMER",
            score=0.7,
            market_phase="PULLBACK",
            market_phase_confidence=0.65,
        )

        # Close the trade via bar evaluation
        closed = engine.evaluate_bar(
            symbol="EURUSD",
            bar_high=1.10300,
            bar_low=1.09800,
            bar_close=1.09850,
            bar_time=1300.0,
        )

        assert len(closed) == 1
        record = closed[0]
        snapshot = record["decision_snapshot"]
        assert snapshot["market_phase"] == "PULLBACK"
        assert snapshot["market_phase_confidence"] == 0.65

    def test_phase_defaults_when_not_provided(self):
        from core.shadow_trades import ShadowTradeEngine

        engine = ShadowTradeEngine(max_bars=5)
        engine.open_trade(
            trade_id="phase_test_2",
            cycle_id=200,
            symbol="GBPUSD",
            direction="SELL",
            entry_price=1.30000,
            stop_loss=1.30100,
            take_profit=1.29800,
            entry_time=2000.0,
            pattern="TWEEZER_TOP",
            score=0.5,
        )

        closed = engine.evaluate_bar(
            symbol="GBPUSD",
            bar_high=1.30200,
            bar_low=1.29700,
            bar_close=1.30150,
            bar_time=2300.0,
        )

        assert len(closed) == 1
        record = closed[0]
        snapshot = record["decision_snapshot"]
        assert snapshot["market_phase"] is None  # empty string → None in output
        assert snapshot["market_phase_confidence"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# 4. EVENT STREAM — PHASE RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestEventStreamPhaseResolution:
    """_resolve_market_phase extracts phase from event payloads."""

    def test_resolve_from_payload(self):
        from core.event_stream import _resolve_market_phase

        event = {"payload": {"market_phase": "CONSOLIDATION"}}
        assert _resolve_market_phase(event) == "CONSOLIDATION"

    def test_resolve_from_nested_data(self):
        from core.event_stream import _resolve_market_phase

        event = {"payload": {"data": {"market_phase": "REVERSAL"}}}
        assert _resolve_market_phase(event) == "REVERSAL"

    def test_resolve_returns_none_when_absent(self):
        from core.event_stream import _resolve_market_phase

        event = {"payload": {"regime": "TRENDING"}}
        assert _resolve_market_phase(event) is None

    def test_resolve_returns_none_for_empty_payload(self):
        from core.event_stream import _resolve_market_phase

        event = {}
        assert _resolve_market_phase(event) is None

    def test_resolve_returns_none_for_empty_string(self):
        from core.event_stream import _resolve_market_phase

        event = {"payload": {"market_phase": ""}}
        assert _resolve_market_phase(event) is None
