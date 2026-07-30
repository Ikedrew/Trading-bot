"""Tests for V10 MarketState model and builder."""

import pytest
from core.v10.market_state import (
    V10MarketState, H4State, H1State, M15State, M5State,
    RegimeState, LocationState, HTFAlignment,
)
from core.v10.market_state_builder import build_v10_market_state
from core.v3_shadow.models import (
    MarketUnderstanding, H4Understanding, H1Understanding,
    M15Understanding, M5Understanding,
)
from core.v3_shadow.context_models import (
    V3MarketContext, HTFStructureContext, LocationContext, BehaviourContext,
)


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════


def _make_understanding():
    return MarketUnderstanding(
        symbol="EURUSD",
        timestamp_utc=1785302400.0,
        confidence=0.85,
        h4=H4Understanding(
            trend="BEARISH", trend_strength=0.6,
            market_phase="IMPULSE", structure_type="LH_LL",
            swing_high=1.0950, swing_low=1.0850,
            atr=0.0045, volatility_state="NEUTRAL", atr_percentile=0.5,
        ),
        h1=H1Understanding(
            bos_confirmed=True, bos_direction="BEARISH",
            dominant_trend="BEARISH", structure_type="LH_LL",
            swing_high=1.0920, swing_low=1.0870,
            structural_clarity=0.75,
            active_demand_ob_high=1.0880, active_demand_ob_low=1.0875,
            active_supply_ob_high=1.0915, active_supply_ob_low=1.0910,
            session_high=1.0930, session_low=1.0860,
            equal_highs_level=1.0920, equal_lows_level=1.0865,
        ),
        m15=M15Understanding(
            pullback_active=True, pullback_depth_atr=1.5,
            retracement_pct=0.6, range_position=0.35,
            swing_high=1.0905, swing_low=1.0875,
            displacement_present=False,
        ),
        m5=M5Understanding(
            momentum_direction="BEARISH", momentum_strength=0.4,
            at_institutional_zone=True, zone_type="SUPPLY_OB",
            atr=0.0006, spread=0.00012, spread_atr_ratio=0.2,
        ),
        observations=["H1 BOS BEARISH", "M15 pullback into supply"],
    )


def _make_context():
    return V3MarketContext(
        symbol="EURUSD",
        timestamp_utc=1785302400.0,
        htf_structure=HTFStructureContext(
            macro_bias="BEARISH", macro_bias_strength=0.7,
            structure_alignment=0.85, authority_timeframe="H1",
            bos_active=True, bos_direction="BEARISH",
            phase_alignment="ALIGNED",
        ),
        location=LocationContext(
            location_type="SUPPLY_OB",
            inside_institutional_zone=True,
            premium_discount="PREMIUM", range_position=0.72,
            zone_quality=0.8,
            liquidity_above=True, liquidity_below=True,
            nearest_liquidity_direction="BELOW",
            nearest_liquidity_distance_pips=5.0,
            demand_zones_nearby=1, supply_zones_nearby=2,
        ),
        behaviour=BehaviourContext(
            regime="RANGING", regime_confidence=0.6,
            volatility_state="NEUTRAL", volatility_level=0.5,
            momentum_direction="BEARISH", momentum_strength=0.4,
            expansion_state="NEUTRAL",
        ),
        overall_confidence=0.8,
        observations=["[BEH] Regime: RANGING"],
    )


# ═══════════════════════════════════════════════════════════════
# MODEL TESTS
# ═══════════════════════════════════════════════════════════════


class TestV10MarketStateModel:
    def test_creates_with_defaults(self):
        state = V10MarketState()
        assert state.symbol == ""
        assert state.confidence == 0.0
        assert state.h4.trend == ""
        assert state.regime.regime == ""

    def test_immutable(self):
        state = V10MarketState(symbol="EURUSD")
        with pytest.raises(Exception):
            state.symbol = "GBPUSD"  # type: ignore

    def test_to_dict_contains_all_layers(self):
        state = V10MarketState(
            symbol="EURUSD", timestamp_utc=1000.0, confidence=0.9,
            h4=H4State(trend="BULLISH"),
            regime=RegimeState(regime="TRENDING"),
        )
        d = state.to_dict()
        assert d["symbol"] == "EURUSD"
        assert d["h4"]["trend"] == "BULLISH"
        assert d["regime"]["regime"] == "TRENDING"
        assert d["schema_version"] == "v10_market_state_v1"

    def test_to_dict_serializes_all_fields(self):
        state = V10MarketState(
            symbol="USDJPY", timestamp_utc=1234.0,
            h1=H1State(bos_confirmed=True, bos_direction="BULLISH"),
            location=LocationState(inside_institutional_zone=True, zone_quality=0.9),
        )
        d = state.to_dict()
        assert d["h1"]["bos_confirmed"] is True
        assert d["location"]["zone_quality"] == 0.9


# ═══════════════════════════════════════════════════════════════
# BUILDER TESTS
# ═══════════════════════════════════════════════════════════════


class TestV10Builder:
    def test_builds_from_understanding_only(self):
        mu = _make_understanding()
        state = build_v10_market_state(mu)

        assert state.symbol == "EURUSD"
        assert state.timestamp_utc == 1785302400.0
        assert state.h4.trend == "BEARISH"
        assert state.h1.bos_confirmed is True
        assert state.h1.bos_direction == "BEARISH"
        assert state.m15.pullback_active is True
        assert state.m5.momentum_direction == "BEARISH"
        assert state.m5.at_institutional_zone is True

    def test_builds_from_understanding_plus_context(self):
        mu = _make_understanding()
        ctx = _make_context()
        state = build_v10_market_state(mu, ctx)

        # Regime from context
        assert state.regime.regime == "RANGING"
        assert state.regime.momentum_direction == "BEARISH"
        assert state.regime.volatility_state == "NEUTRAL"

        # Location from context
        assert state.location.location_type == "SUPPLY_OB"
        assert state.location.inside_institutional_zone is True
        assert state.location.premium_discount == "PREMIUM"
        assert state.location.zone_quality == 0.8

        # HTF alignment from context
        assert state.htf_alignment.macro_bias == "BEARISH"
        assert state.htf_alignment.structure_alignment == 0.85

    def test_h4_fields_populated(self):
        mu = _make_understanding()
        state = build_v10_market_state(mu)

        assert state.h4.trend_strength == 0.6
        assert state.h4.market_phase == "IMPULSE"
        assert state.h4.atr == 0.0045
        assert state.h4.swing_high == 1.0950

    def test_h1_zones_populated(self):
        mu = _make_understanding()
        state = build_v10_market_state(mu)

        assert state.h1.demand_ob_high == 1.0880
        assert state.h1.supply_ob_high == 1.0915
        assert state.h1.equal_highs_level == 1.0920

    def test_m15_pullback_populated(self):
        mu = _make_understanding()
        state = build_v10_market_state(mu)

        assert state.m15.pullback_active is True
        assert state.m15.pullback_depth_atr == 1.5
        assert state.m15.range_position == 0.35

    def test_m5_execution_env_populated(self):
        mu = _make_understanding()
        state = build_v10_market_state(mu)

        assert state.m5.spread_atr_ratio == 0.2
        assert state.m5.zone_type == "SUPPLY_OB"

    def test_confidence_from_context(self):
        mu = _make_understanding()
        ctx = _make_context()
        state = build_v10_market_state(mu, ctx)
        assert state.confidence == 0.8

    def test_confidence_from_understanding_when_no_context(self):
        mu = _make_understanding()
        state = build_v10_market_state(mu)
        assert state.confidence == 0.85

    def test_observations_merged(self):
        mu = _make_understanding()
        ctx = _make_context()
        state = build_v10_market_state(mu, ctx)
        assert "H1 BOS BEARISH" in state.observations
        assert "[BEH] Regime: RANGING" in state.observations

    def test_result_is_immutable(self):
        mu = _make_understanding()
        state = build_v10_market_state(mu)
        with pytest.raises(Exception):
            state.confidence = 0.5  # type: ignore
