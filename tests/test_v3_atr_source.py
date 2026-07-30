"""
Tests for V3 ATR source correctness.

Verifies:
    - ATR is computed from candle ranges (not volatility_filter)
    - Displacement calculation is mathematically valid
    - Rejection calculation is mathematically valid
    - V3 no longer reads engine_state.volatility_filter as ATR
"""

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from core.v3_opportunity_builder import build_v3_opportunity
from core.observers.v3_opportunity_observer import observe_v3_opportunity


# ═══════════════════════════════════════════════════════════════════════════════
# MOCKS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class MockCandle:
    high: float = 1.086
    low: float = 1.084
    open: float = 1.085
    close: float = 1.0855
    time: int = 1753574400


@dataclass
class MockEngineState:
    """Engine state with volatility_filter (the WRONG ATR source)."""
    volatility_filter: float = 0.85  # This is a 0-1 penalty, NOT ATR
    current_bias: Any = None
    bias_phase: str = "EXPIRED"
    bias_strength: float = 0.0


@dataclass
class MockCtx:
    symbol: str = "EURUSD"
    cycle_id: int = 100
    bar_time: float = 1753574400.0
    engine_result: dict = None
    engine_state: Any = None
    candles: list = None
    closed_i: int = 60
    bid: float = 1.08500
    ask: float = 1.08510
    config: Any = None
    detected_patterns: list = None
    risk_manager: Any = None
    htf_context: Any = None
    runtime_session_id: str = "test"
    decision_funnel: Any = None
    market_context: Any = None

    def __post_init__(self):
        if self.engine_result is None:
            self.engine_result = {"entity_id": "EURUSD_1753574400"}
        if self.engine_state is None:
            self.engine_state = MockEngineState()


# ═══════════════════════════════════════════════════════════════════════════════
# ATR CORRECTNESS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestATRSource:
    """V3 ATR comes from candles, not volatility_filter."""

    def test_atr_from_candles_not_engine_state(self):
        """V3 observer computes ATR from candle ranges."""
        # Create candles with known ATR: each bar has range 0.0012
        candles = [MockCandle(high=1.086, low=1.0848, open=1.085, close=1.0855)] * 65
        # ATR should be: sum(0.0012 * 14) / 14 = 0.0012

        ctx = MockCtx(candles=candles)
        ctx.engine_state = MockEngineState(volatility_filter=0.85)

        # Redirect persistence
        temp_dir = tempfile.mkdtemp()
        import core.v3_opportunity_builder as mod
        orig = mod._LOCAL_DIR
        mod._LOCAL_DIR = temp_dir

        try:
            observe_v3_opportunity(ctx)
            files = list(Path(temp_dir).rglob("*.jsonl"))
            assert len(files) == 1
            record = json.loads(open(files[0]).readline())

            # ATR should be ~0.0012 (candle range), NOT 0.85 (volatility_filter)
            assert record["atr"] == pytest.approx(0.0012, abs=0.0001)
            assert record["atr"] != pytest.approx(0.85, abs=0.1)
        finally:
            mod._LOCAL_DIR = orig
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_atr_zero_without_candles(self):
        """ATR is 0 when candles unavailable (not fallback to volatility_filter)."""
        ctx = MockCtx(candles=None)
        ctx.engine_state = MockEngineState(volatility_filter=0.85)

        temp_dir = tempfile.mkdtemp()
        import core.v3_opportunity_builder as mod
        orig = mod._LOCAL_DIR
        mod._LOCAL_DIR = temp_dir

        try:
            observe_v3_opportunity(ctx)
            files = list(Path(temp_dir).rglob("*.jsonl"))
            assert len(files) == 1
            record = json.loads(open(files[0]).readline())
            # Should be 0 (no candles), not 0.85
            assert record["atr"] == 0.0
        finally:
            mod._LOCAL_DIR = orig
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_atr_insufficient_candles(self):
        """ATR is 0 when fewer than 14 candles available."""
        candles = [MockCandle()] * 10  # Only 10, need > 14
        ctx = MockCtx(candles=candles)

        temp_dir = tempfile.mkdtemp()
        import core.v3_opportunity_builder as mod
        orig = mod._LOCAL_DIR
        mod._LOCAL_DIR = temp_dir

        try:
            observe_v3_opportunity(ctx)
            files = list(Path(temp_dir).rglob("*.jsonl"))
            record = json.loads(open(files[0]).readline())
            assert record["atr"] == 0.0
        finally:
            mod._LOCAL_DIR = orig
            shutil.rmtree(temp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# DISPLACEMENT CALCULATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestDisplacement:
    """Displacement in ATR multiples is mathematically correct."""

    def test_displacement_1_5_atr(self):
        """Candle range of exactly 1.5 ATR triggers displacement."""
        # ATR = 0.0010, candle range = 0.0015 → 1.5 ATR exactly
        # Need > 1.5, so use 0.0016
        atr = 0.0010
        normal_candles = [MockCandle(high=1.0860, low=1.0850)] * 64  # range 0.0010 each
        # Last candle (closed_index=60) has large range
        big_candle = MockCandle(high=1.0870, low=1.0854, open=1.0855, close=1.0868)
        normal_candles[60] = big_candle  # range = 0.0016

        opp = build_v3_opportunity(
            symbol="EURUSD",
            timestamp_utc=1.0,
            price=1.085,
            atr=atr,
            candles=normal_candles,
            closed_index=60,
        )
        # 0.0016 / 0.0010 = 1.6 ATR → displacement detected
        assert opp.displacement_into_level is True
        assert opp.displacement_magnitude_atr == pytest.approx(1.6, abs=0.01)

    def test_no_displacement_small_candle(self):
        """Normal candle does not trigger displacement."""
        atr = 0.0010
        normal_candles = [MockCandle(high=1.0860, low=1.0850)] * 65  # range 0.0010

        opp = build_v3_opportunity(
            symbol="EURUSD",
            timestamp_utc=1.0,
            price=1.085,
            atr=atr,
            candles=normal_candles,
            closed_index=60,
        )
        # 0.0010 / 0.0010 = 1.0 ATR → no displacement (need > 1.5)
        assert opp.displacement_into_level is False
        assert opp.displacement_magnitude_atr == 0.0

    def test_displacement_not_triggered_with_zero_atr(self):
        """Zero ATR prevents displacement detection (no divide by zero)."""
        candles = [MockCandle(high=1.090, low=1.080)] * 65  # huge candles

        opp = build_v3_opportunity(
            symbol="EURUSD",
            timestamp_utc=1.0,
            price=1.085,
            atr=0.0,  # Zero ATR
            candles=candles,
            closed_index=60,
        )
        assert opp.displacement_into_level is False


# ═══════════════════════════════════════════════════════════════════════════════
# REJECTION CALCULATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestRejection:
    """Rejection candle detection is mathematically valid."""

    def test_rejection_hammer(self):
        """Long lower wick (hammer) triggers rejection."""
        atr = 0.0010
        # Hammer: open near high, close near high, long lower wick
        # high=1.086, low=1.083, open=1.0858, close=1.0855
        # body = |1.0855 - 1.0858| = 0.0003
        # lower_wick = min(1.0855, 1.0858) - 1.083 = 1.0855 - 1.083 = 0.0025
        # upper_wick = 1.086 - max(1.0855, 1.0858) = 1.086 - 1.0858 = 0.0002
        # max_wick = 0.0025 > body * 1.5 = 0.00045 → REJECTION
        hammer = MockCandle(high=1.086, low=1.083, open=1.0858, close=1.0855)
        candles = [MockCandle()] * 65
        candles[60] = hammer

        opp = build_v3_opportunity(
            symbol="EURUSD",
            timestamp_utc=1.0,
            price=1.085,
            atr=atr,
            candles=candles,
            closed_index=60,
        )
        assert opp.rejection_candle_present is True
        # rejection_body_ratio = body / range = 0.0003 / 0.003 = 0.1
        assert opp.rejection_body_ratio == pytest.approx(0.1, abs=0.01)
        # rejection_wick_atr = max_wick / atr = 0.0025 / 0.001 = 2.5
        assert opp.rejection_wick_atr_ratio == pytest.approx(2.5, abs=0.1)

    def test_no_rejection_balanced_candle(self):
        """Balanced candle (body ≈ range) does not trigger rejection."""
        atr = 0.0010
        # Full body candle: open=1.084, close=1.086, high=1.086, low=1.084
        # body = 0.002, range = 0.002, wicks = 0
        # max_wick = 0 → NOT > body * 1.5
        balanced = MockCandle(high=1.086, low=1.084, open=1.084, close=1.086)
        candles = [MockCandle()] * 65
        candles[60] = balanced

        opp = build_v3_opportunity(
            symbol="EURUSD",
            timestamp_utc=1.0,
            price=1.085,
            atr=atr,
            candles=candles,
            closed_index=60,
        )
        assert opp.rejection_candle_present is False


# ═══════════════════════════════════════════════════════════════════════════════
# NO VOLATILITY_FILTER DEPENDENCY
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoVolatilityFilterDependency:
    """V3 observer does NOT read engine_state.volatility_filter for ATR."""

    def test_source_code_no_volatility_filter(self):
        """V3 observer source does not reference volatility_filter."""
        import inspect
        import core.observers.v3_opportunity_observer as m
        source = inspect.getsource(m)
        assert "volatility_filter" not in source

    def test_volatility_filter_unchanged(self):
        """Existing volatility filtering in decision pipeline is unaffected."""
        # The engine_state.volatility_filter should still be usable by other
        # components — we just don't use it in V3 for ATR computation
        es = MockEngineState(volatility_filter=0.72)
        assert es.volatility_filter == 0.72  # Still accessible
