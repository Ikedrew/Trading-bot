"""
Tests for entity_id placement fix.

Verifies:
- Same candle always produces the same entity_id
- Early exits (no_viable_pattern) include entity_id (not NULL)
- All decision paths include entity_id
- entity_id format is f"{symbol}_{bar_time}"
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ─── HELPERS ──────────────────────────────────────────────────────────────────

@dataclass
class _FakeCandle:
    time: int
    open: float = 1.1000
    high: float = 1.1050
    low: float = 1.0950
    close: float = 1.1020
    tick_volume: int = 100
    spread: int = 2
    real_volume: int = 0


@dataclass
class _FakeEngineState:
    current_bias: object = None
    bias_phase: str = "EXPIRED"
    bias_strength: float = 0.0
    bias_age_seconds: float = 0.0
    regime_state: str = "TRANSITIONAL"
    volatility_filter: float = 0.001
    bias_confirmation_score: float = 0.0
    bias_confirmation_count: int = 0
    bias_contradiction_count: int = 0


@dataclass
class _FakeConfig:
    MARKET_FILTER_LOOKBACK: int = 5
    TREND_EMA_PERIOD: int = 50
    MTF_SHADOW_MODE: bool = False


# ─── TESTS ────────────────────────────────────────────────────────────────────

class TestEntityIdOnAllPaths:
    """Every return path from run_new_engine includes non-null entity_id."""

    def _run_engine(self, patterns=None, candles=None, closed_i=5):
        """Helper to call run_new_engine with minimal mocking."""
        from core.pipeline.new_engine import run_new_engine
        from risk.manager import RiskManager

        if candles is None:
            candles = [_FakeCandle(time=1700000000 + i * 300) for i in range(10)]

        rm = RiskManager(fixed_lot=0.01, base_rr=2.0, rr3_patterns=frozenset(), sl_buffer=0.0002, min_rr=2.0)

        return run_new_engine(
            candles=candles,
            closed_i=closed_i,
            symbol="EURUSD",
            bid=1.1020,
            ask=1.1022,
            engine_state=_FakeEngineState(),
            config=_FakeConfig(),
            detected_patterns=patterns or [],
            risk_manager=rm,
            htf_context=None,
            cycle_id=42,
        )

    def test_no_viable_pattern_has_entity_id(self):
        """Early exit at no_viable_pattern includes entity_id."""
        result = self._run_engine(patterns=[])

        assert result["action"] == "NO_TRADE"
        assert result["reason"] == "no_viable_pattern"
        assert "entity_id" in result
        assert result["entity_id"] != ""
        assert result["entity_id"] is not None
        assert result["entity_id"] == "EURUSD_1700001500"  # candle at index 5

    def test_same_candle_same_entity_id(self):
        """Same candle always produces the same entity_id."""
        result1 = self._run_engine(patterns=[])
        result2 = self._run_engine(patterns=[])

        assert result1["entity_id"] == result2["entity_id"]

    def test_different_candle_different_entity_id(self):
        """Different bar times produce different entity_ids."""
        candles_a = [_FakeCandle(time=1700000000 + i * 300) for i in range(10)]
        candles_b = [_FakeCandle(time=1700010000 + i * 300) for i in range(10)]

        result_a = self._run_engine(patterns=[], candles=candles_a)
        result_b = self._run_engine(patterns=[], candles=candles_b)

        assert result_a["entity_id"] != result_b["entity_id"]

    def test_entity_id_format(self):
        """entity_id follows f'{symbol}_{bar_time}' format."""
        candles = [_FakeCandle(time=1700005000 + i * 300) for i in range(10)]
        result = self._run_engine(patterns=[], candles=candles, closed_i=3)

        expected = f"EURUSD_{1700005000 + 3 * 300}"
        assert result["entity_id"] == expected

    def test_entity_id_present_on_execute_path(self):
        """EXECUTE path also has entity_id (regression — was already working)."""
        from strategy.signals import Signal, Side

        # Create a pattern that will proceed through the pipeline
        pattern = Signal(
            pattern="BULLISH_ENGULFING",
            side=Side.BUY,
            bar_index=5,
            bar_time=1700001500,
        )

        result = self._run_engine(patterns=[pattern])

        # Regardless of whether it passes all gates, entity_id must be present
        assert "entity_id" in result
        assert result["entity_id"] == "EURUSD_1700001500"
        assert result["entity_id"] != ""


class TestEntityIdConsistency:
    """entity_id is consistent across the same bar regardless of outcome."""

    def test_same_bar_different_patterns_same_entity(self):
        """Different patterns on same bar produce same entity_id."""
        from core.pipeline.new_engine import run_new_engine
        from strategy.signals import Signal, Side
        from risk.manager import RiskManager

        candles = [_FakeCandle(time=1700000000 + i * 300) for i in range(10)]
        rm = RiskManager(fixed_lot=0.01, base_rr=2.0, rr3_patterns=frozenset(), sl_buffer=0.0002, min_rr=2.0)

        # Run with pattern A
        pat_a = Signal(pattern="BULLISH_ENGULFING", side=Side.BUY, bar_index=5, bar_time=candles[5].time)
        result_a = run_new_engine(
            candles=candles, closed_i=5, symbol="EURUSD",
            bid=1.1020, ask=1.1022, engine_state=_FakeEngineState(),
            config=_FakeConfig(), detected_patterns=[pat_a],
            risk_manager=rm, cycle_id=1,
        )

        # Run with pattern B (same bar)
        pat_b = Signal(pattern="HAMMER", side=Side.BUY, bar_index=5, bar_time=candles[5].time)
        result_b = run_new_engine(
            candles=candles, closed_i=5, symbol="EURUSD",
            bid=1.1020, ask=1.1022, engine_state=_FakeEngineState(),
            config=_FakeConfig(), detected_patterns=[pat_b],
            risk_manager=rm, cycle_id=2,
        )

        # Same bar = same entity_id
        assert result_a["entity_id"] == result_b["entity_id"]

    def test_no_pattern_vs_pattern_same_entity(self):
        """No-pattern and with-pattern on same bar produce same entity_id."""
        from core.pipeline.new_engine import run_new_engine
        from strategy.signals import Signal, Side
        from risk.manager import RiskManager

        candles = [_FakeCandle(time=1700000000 + i * 300) for i in range(10)]
        rm = RiskManager(fixed_lot=0.01, base_rr=2.0, rr3_patterns=frozenset(), sl_buffer=0.0002, min_rr=2.0)

        # No patterns
        result_none = run_new_engine(
            candles=candles, closed_i=5, symbol="EURUSD",
            bid=1.1020, ask=1.1022, engine_state=_FakeEngineState(),
            config=_FakeConfig(), detected_patterns=[],
            risk_manager=rm, cycle_id=1,
        )

        # With pattern
        pat = Signal(pattern="BULLISH_ENGULFING", side=Side.BUY, bar_index=5, bar_time=candles[5].time)
        result_pat = run_new_engine(
            candles=candles, closed_i=5, symbol="EURUSD",
            bid=1.1020, ask=1.1022, engine_state=_FakeEngineState(),
            config=_FakeConfig(), detected_patterns=[pat],
            risk_manager=rm, cycle_id=2,
        )

        # Same bar = same entity_id regardless of pattern presence
        assert result_none["entity_id"] == result_pat["entity_id"]
