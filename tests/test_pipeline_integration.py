"""
Pipeline integration tests — validates full process_bar() flow end-to-end.

Feeds synthetic candle data through the entire system:
candle ? filters ? pattern detection ? risk checks ? UnifiedDecision

All MT5 dependencies are mocked. Deterministic. No live connections.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.mt5_data import Candle
from core.engine import EngineState, process_bar
from core import config
from risk.manager import RiskManager
from patterns.registry import load_all_patterns

load_all_patterns()


# --- HELPERS ------------------------------------------------------------------

def C(t, o, h, l, c):
    """Shorthand candle constructor."""
    return Candle(time=t, open=o, high=h, low=l, close=c, tick_volume=100)


def _build_risk():
    return RiskManager(
        fixed_lot=0.01,
        base_rr=2.0,
        rr3_patterns=frozenset({"THREE_WHITE_SOLDIERS", "THREE_BLACK_CROWS"}),
        sl_buffer=0.0002,
        min_rr=2.0,
    )


def _run_bar(candles, closed_i, state=None, bid=None, ask=None):
    """Run process_bar with sensible defaults."""
    if state is None:
        state = EngineState()
    if bid is None:
        bid = candles[closed_i].close
    if ask is None:
        ask = candles[closed_i].close + 0.0002
    return process_bar(
        candles=candles,
        closed_i=closed_i,
        symbol="EURUSD",
        config=config,
        risk=_build_risk(),
        state=state,
        bid=bid,
        ask=ask,
        now_s=float(candles[closed_i].time),
    )


# --- SYNTHETIC DATA GENERATORS ------------------------------------------------

def _trending_up_candles(n=20, start_price=1.10, step=0.003):
    """Generate a clean uptrend with progressive higher closes."""
    candles = []
    for i in range(n):
        o = start_price + i * step
        c = o + step * 0.8
        h = c + 0.001
        l = o - 0.0005
        candles.append(C(1000 + i * 300, o, h, l, c))
    return candles


def _flat_candles(n=20, price=1.10):
    """Generate flat/choppy candles with no directional movement."""
    candles = []
    for i in range(n):
        # Tiny random-like oscillation but net zero
        offset = 0.0001 * (1 if i % 2 == 0 else -1)
        o = price + offset
        c = price - offset
        h = price + 0.0003
        l = price - 0.0003
        candles.append(C(1000 + i * 300, o, h, l, c))
    return candles


def _engulfing_setup(n=15):
    """Generate candles ending with a clear bullish engulfing pattern."""
    # Build warmup trend first
    candles = _trending_up_candles(n - 2, start_price=1.08, step=0.002)
    # Add bearish candle followed by bullish engulfing
    last_close = candles[-1].close
    candles.append(C(1000 + (n - 2) * 300, last_close, last_close, last_close - 0.005, last_close - 0.004))
    candles.append(C(1000 + (n - 1) * 300, last_close - 0.005, last_close + 0.002, last_close - 0.006, last_close + 0.001))
    return candles


# --- TEST 1: FULL PIPELINE PRODUCES VALID DECISION ----------------------------

class TestPipelineHappyPath:
    def test_process_bar_returns_unified_decision(self):
        """process_bar always returns a UnifiedDecision object."""
        candles = _trending_up_candles(20)
        state = EngineState()

        # Process multiple bars to build state
        for i in range(5, len(candles)):
            result = _run_bar(candles, i, state)

        # Verify structure
        assert result is not None
        assert hasattr(result, "decision")
        assert hasattr(result, "bar_context")
        assert hasattr(result, "last_completed_stage")
        assert result.decision is not None

    def test_decision_has_valid_fields(self):
        """UnifiedDecision.decision has expected attributes."""
        candles = _trending_up_candles(20)
        state = EngineState()

        result = _run_bar(candles, 15, state)
        dec = result.decision

        assert hasattr(dec, "should_trade")
        assert hasattr(dec, "reason")
        assert hasattr(dec, "signal")
        assert hasattr(dec, "intent")
        assert isinstance(dec.should_trade, bool)
        assert isinstance(dec.reason, str)


# --- TEST 2: NO-TRADE SCENARIO (FILTERS BLOCK) -------------------------------

class TestPipelineNoTrade:
    def test_flat_candles_produce_no_trade(self):
        """Flat/choppy candles should not produce trade signals."""
        candles = _flat_candles(20)
        state = EngineState()

        # Process all bars
        trades_found = 0
        for i in range(5, len(candles)):
            result = _run_bar(candles, i, state)
            if result.decision.should_trade:
                trades_found += 1

        # Flat market should produce zero or very few trades
        assert trades_found == 0, f"Expected no trades in flat market, got {trades_found}"

    def test_no_trade_has_reason(self):
        """When no trade, decision.reason explains why."""
        candles = _flat_candles(20)
        state = EngineState()

        result = _run_bar(candles, 10, state)
        assert result.decision.should_trade is False
        assert len(result.decision.reason) > 0

    def test_no_trade_has_no_intent(self):
        """When no trade, intent is None."""
        candles = _flat_candles(20)
        state = EngineState()

        result = _run_bar(candles, 10, state)
        assert result.decision.intent is None


# --- TEST 3: PIPELINE STAGE TRACKING -----------------------------------------

class TestPipelineStages:
    def test_last_completed_stage_is_set(self):
        """Pipeline always reports which stage it reached."""
        candles = _trending_up_candles(20)
        state = EngineState()

        result = _run_bar(candles, 15, state)
        assert result.last_completed_stage != ""
        assert result.last_completed_stage in (
            "market_context", "structure_analysis", "confirmations",
            "trade_quality", "scoring_engine", "build_intent", "complete",
        )

    def test_context_layer_evaluated(self):
        """Context layer is always evaluated."""
        candles = _trending_up_candles(20)
        state = EngineState()

        result = _run_bar(candles, 15, state)
        assert result.context.evaluated is True


# --- TEST 4: DETERMINISTIC REPLAY --------------------------------------------

class TestDeterministicReplay:
    def test_same_input_same_output(self):
        """Identical inputs always produce identical decisions."""
        candles = _trending_up_candles(20)

        # Run 1
        state1 = EngineState()
        results1 = []
        for i in range(5, len(candles)):
            r = _run_bar(candles, i, state1)
            results1.append((r.decision.should_trade, r.decision.reason))

        # Run 2 (fresh state, same candles)
        state2 = EngineState()
        results2 = []
        for i in range(5, len(candles)):
            r = _run_bar(candles, i, state2)
            results2.append((r.decision.should_trade, r.decision.reason))

        assert results1 == results2, "Pipeline is not deterministic!"

    def test_state_progression_consistent(self):
        """EngineState evolves consistently across bars."""
        candles = _trending_up_candles(20)
        state = EngineState()

        for i in range(5, len(candles)):
            _run_bar(candles, i, state)

        # State should have progressed (bias_age_seconds > 0 after multiple bars)
        assert state.current_time > 0


# --- TEST 5: EDGE CASES ------------------------------------------------------

class TestPipelineEdgeCases:
    def test_minimum_candles_does_not_crash(self):
        """Pipeline handles minimum viable candle count without crashing."""
        candles = [C(1, 1.10, 1.11, 1.09, 1.10), C(2, 1.10, 1.11, 1.09, 1.105)]
        state = EngineState()

        # Should not crash even with very few candles
        result = _run_bar(candles, 1, state)
        assert result is not None
        assert result.decision.should_trade is False

    def test_zero_range_candles_handled(self):
        """Zero-range candles don't crash the pipeline."""
        candles = [C(i, 1.10, 1.10, 1.10, 1.10) for i in range(20)]
        state = EngineState()

        result = _run_bar(candles, 15, state)
        assert result is not None
        assert result.decision.should_trade is False
