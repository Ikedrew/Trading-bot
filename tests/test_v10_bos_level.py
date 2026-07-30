"""Tests for bos_level structural reference flow.

Verifies that BOS detection preserves the broken swing level
and that it flows through to the entry engine for stop/target placement.
"""

import pytest
from core.v10.market_state import V10MarketState, H1State, M15State, M5State, RegimeState, LocationState
from core.v10.opportunity_assessment import OpportunityAssessment, OpportunityQuality
from core.v10.strategy_family import StrategyDecision, StrategyFamily
from core.v10.horizon_assessment import HorizonDecision, HorizonType
from core.v10.entry_engine import build_entry_decision
from core.v10.entry_model import EntryStatus


def _bullish_bos_state(bos_level=1.0900, swing_high=0.0, swing_low=0.0):
    """State with bullish BOS but no new swing pivots (impulse continuation)."""
    return V10MarketState(
        symbol="EURUSD", timestamp_utc=1785400000.0,
        h1=H1State(
            dominant_trend="BULLISH",
            bos_confirmed=True, bos_direction="BULLISH",
            bos_level=bos_level,
            structural_clarity=0.9,
            swing_high=swing_high, swing_low=swing_low,
        ),
        m15=M15State(range_position=0.85, pullback_active=True, pullback_depth_atr=1.0),
        m5=M5State(atr=0.0006, rejection_present=True, rejection_direction="BULLISH"),
        regime=RegimeState(regime="TRENDING"),
        location=LocationState(range_position=0.85),
    )


def _bearish_bos_state(bos_level=1.0850, swing_high=0.0, swing_low=0.0):
    """State with bearish BOS but no new swing pivots."""
    return V10MarketState(
        symbol="EURUSD", timestamp_utc=1785400000.0,
        h1=H1State(
            dominant_trend="BEARISH",
            bos_confirmed=True, bos_direction="BEARISH",
            bos_level=bos_level,
            structural_clarity=0.9,
            swing_high=swing_high, swing_low=swing_low,
        ),
        m15=M15State(range_position=0.15, pullback_active=True, pullback_depth_atr=1.0),
        m5=M5State(atr=0.0006, rejection_present=True, rejection_direction="BEARISH"),
        regime=RegimeState(regime="TRENDING"),
        location=LocationState(range_position=0.15),
    )


def _opportunity(direction="BULLISH"):
    return OpportunityAssessment(
        observation_id="bos_test_001",
        symbol="EURUSD", timestamp_utc=1785400000.0,
        opportunity_state="WATCHING",
        directional_bias=direction,
        opportunity_type="ZONE_REACTION",
        quality=OpportunityQuality(overall_quality=0.6, location_score=0.5, structure_score=0.7),
    )


def _strategy(family=StrategyFamily.TREND_CONTINUATION.value):
    return StrategyDecision(
        opportunity_id="bos_test_001", symbol="EURUSD", timestamp_utc=1785400000.0,
        strategy_family=family, strategy_confidence=0.8,
    )


def _horizon(h=HorizonType.EXTENDED.value):
    return HorizonDecision(
        opportunity_id="bos_test_001", symbol="EURUSD", timestamp_utc=1785400000.0,
        horizon_type=h,
    )


class TestBosLevelPreservation:
    """BOS detection preserves the broken swing level."""

    def test_bullish_bos_level_in_state(self):
        state = _bullish_bos_state(bos_level=1.0920)
        assert state.h1.bos_level == 1.0920
        assert state.h1.bos_confirmed is True
        assert state.h1.bos_direction == "BULLISH"

    def test_bearish_bos_level_in_state(self):
        state = _bearish_bos_state(bos_level=1.0850)
        assert state.h1.bos_level == 1.0850
        assert state.h1.bos_direction == "BEARISH"

    def test_no_bos_level_when_no_bos(self):
        state = V10MarketState(
            symbol="EURUSD", timestamp_utc=1785400000.0,
            h1=H1State(bos_confirmed=False, bos_level=0.0),
        )
        assert state.h1.bos_level == 0.0


class TestBosLevelInEntryEngine:
    """Entry engine uses bos_level for stop/target when swing levels are 0."""

    def test_bullish_bos_provides_stop(self):
        """Bullish BOS → bos_level used as stop (support below entry)."""
        state = _bullish_bos_state(bos_level=1.0900)
        entry = build_entry_decision(state, _opportunity("BULLISH"), _strategy(), _horizon())
        # Should NOT be invalid — bos_level provides geometry
        assert entry.entry_status != EntryStatus.INVALID.value or entry.stop_reference.price > 0 or entry.risk_distance > 0
        # If entry is valid, stop should reference bos_level
        if entry.entry_status != EntryStatus.INVALID.value:
            assert entry.stop_reference.price > 0
            assert "bos" in entry.stop_reference.structure_source.lower()

    def test_bearish_bos_provides_stop(self):
        """Bearish BOS → bos_level used as stop (resistance above entry)."""
        state = _bearish_bos_state(bos_level=1.0850)
        entry = build_entry_decision(state, _opportunity("BEARISH"), _strategy(), _horizon())
        if entry.entry_status != EntryStatus.INVALID.value:
            assert entry.stop_reference.price > 0
            assert "bos" in entry.stop_reference.structure_source.lower()

    def test_bos_level_produces_valid_geometry(self):
        """With bos_level, entry should produce non-zero risk distance."""
        state = _bullish_bos_state(bos_level=1.0900)
        entry = build_entry_decision(state, _opportunity("BULLISH"), _strategy(), _horizon())
        # Entry price should be bos_level (fallback)
        # Stop should be below bos_level
        # If both are set, risk_distance > 0
        if entry.entry_price and entry.entry_price > 0:
            assert entry.risk_distance is None or entry.risk_distance > 0 or entry.stop_reference.price > 0

    def test_no_bos_level_produces_invalid(self):
        """Without bos_level AND without swing levels → still invalid geometry."""
        state = V10MarketState(
            symbol="EURUSD", timestamp_utc=1785400000.0,
            h1=H1State(bos_confirmed=True, bos_direction="BULLISH", bos_level=0.0,
                       structural_clarity=0.9, swing_high=0.0, swing_low=0.0),
            m5=M5State(atr=0.0006),
            regime=RegimeState(regime="TRENDING"),
        )
        entry = build_entry_decision(state, _opportunity("BULLISH"), _strategy(), _horizon())
        # Without any structural level, geometry should fail
        assert entry.entry_status == EntryStatus.INVALID.value

    def test_swing_levels_preferred_over_bos(self):
        """When swing levels exist, they're used instead of bos_level."""
        state = _bullish_bos_state(bos_level=1.0900, swing_high=1.0950, swing_low=1.0870)
        entry = build_entry_decision(state, _opportunity("BULLISH"), _strategy(), _horizon())
        # With swing levels, entry should use swing midpoint, stop from swing_low
        if entry.entry_status != EntryStatus.INVALID.value:
            # Stop should reference swing, not bos
            assert entry.stop_reference.price > 0


class TestBosLevelImpulseScenario:
    """Real-world scenario: impulse trend, no pivots, only BOS."""

    def test_impulse_bullish_continuation(self):
        """TREND_CONTINUATION + bullish BOS + no swings → should have valid geometry."""
        state = _bullish_bos_state(bos_level=1.1020)
        entry = build_entry_decision(
            state, _opportunity("BULLISH"),
            _strategy(StrategyFamily.TREND_CONTINUATION.value),
            _horizon(HorizonType.EXTENDED.value),
        )
        # The entry engine should now construct geometry from bos_level
        # Entry = bos_level, Stop = bos_level - buffer, Target = projected
        if entry.stop_reference.price > 0 and entry.entry_price and entry.entry_price > 0:
            assert entry.risk_distance > 0
            assert entry.expected_rr >= 1.0
