"""Tests for V10 TimeframeContext model and builder."""

import pytest
from core.v10.timeframe_context import (
    TimeframeContext, H4MacroEnvironment, H1StructuralAuthority,
    M15OpportunityFormation, M5ExecutionEnvironment,
)
from core.v10.timeframe_context_builder import build_timeframe_context
from core.v10.market_state_builder import build_v10_from_timeframe_context
from core.market_understanding.models import (
    MarketUnderstanding, H4Understanding, H1Understanding,
    M15Understanding, M5Understanding,
)


def _make_mu():
    """Create a realistic MarketUnderstanding for testing."""
    return MarketUnderstanding(
        symbol="GBPUSD",
        timestamp_utc=1785400000.0,
        confidence=0.8,
        h4=H4Understanding(
            trend="BEARISH", trend_strength=0.7,
            market_phase="IMPULSE", structure_type="LH_LL",
            swing_high=1.2700, swing_low=1.2550,
            last_bos_direction="BEARISH",
            atr=0.0060, volatility_state="NEUTRAL", atr_percentile=0.45,
        ),
        h1=H1Understanding(
            bos_confirmed=True, bos_direction="BEARISH",
            dominant_trend="BEARISH", structure_type="LH_LL",
            swing_high=1.2650, swing_low=1.2580,
            structural_clarity=0.72,
            active_demand_ob_high=1.2590, active_demand_ob_low=1.2585,
            active_supply_ob_high=1.2640, active_supply_ob_low=1.2635,
            nearest_fvg_above=1.2620, nearest_fvg_below=1.2570,
            equal_highs_level=1.2650, equal_lows_level=1.2575,
            session_high=1.2660, session_low=1.2570,
        ),
        m15=M15Understanding(
            internal_bos=True, internal_bos_direction="BEARISH",
            pullback_active=True, pullback_depth_atr=1.2,
            retracement_pct=0.5, range_position=0.65,
            swing_high=1.2630, swing_low=1.2590,
            displacement_present=False,
            refined_supply_ob_high=1.2625, refined_supply_ob_low=1.2620,
        ),
        m5=M5Understanding(
            momentum_direction="NEUTRAL", momentum_strength=0.2,
            rejection_present=True, rejection_direction="BEARISH",
            rejection_strength_atr=0.8,
            at_institutional_zone=True, zone_type="SUPPLY_OB",
            atr=0.00045, spread=0.00012, spread_atr_ratio=0.27,
        ),
    )


# ═══════════════════════════════════════════════════════════════
# MODEL TESTS
# ═══════════════════════════════════════════════════════════════


class TestTimeframeContextModel:
    def test_creates_with_defaults(self):
        ctx = TimeframeContext()
        assert ctx.symbol == ""
        assert ctx.h4.trend_state == ""
        assert ctx.hierarchy_valid is True

    def test_immutable(self):
        ctx = TimeframeContext(symbol="EURUSD")
        with pytest.raises(Exception):
            ctx.symbol = "GBPUSD"  # type: ignore

    def test_to_dict_structure(self):
        ctx = TimeframeContext(
            symbol="USDJPY", timestamp_utc=1000.0,
            h4=H4MacroEnvironment(trend_state="BULLISH"),
        )
        d = ctx.to_dict()
        assert d["h4"]["trend_state"] == "BULLISH"
        assert d["schema_version"] == "v10_timeframe_context_v1"

    def test_m5_has_no_direction_authority_fields(self):
        """M5 should only have execution fields, not directional authority."""
        m5 = M5ExecutionEnvironment()
        field_names = [f.name for f in m5.__dataclass_fields__.values()]
        # M5 should NOT have: trend, macro_bias, structure_direction, opportunity
        forbidden = {"trend_state", "macro_bias", "structure_direction", "opportunity_state"}
        assert forbidden.isdisjoint(set(field_names))


# ═══════════════════════════════════════════════════════════════
# BUILDER TESTS
# ═══════════════════════════════════════════════════════════════


class TestTimeframeContextBuilder:
    def test_h4_fields_populated(self):
        mu = _make_mu()
        ctx = build_timeframe_context(mu)
        assert ctx.h4.trend_state == "BEARISH"
        assert ctx.h4.trend_strength == 0.7
        assert ctx.h4.market_phase == "IMPULSE"
        assert ctx.h4.range_or_trend == "TRENDING"
        assert ctx.h4.major_swing_high == 1.2700
        assert ctx.h4.atr == 0.0060

    def test_h1_structure_authoritative(self):
        mu = _make_mu()
        ctx = build_timeframe_context(mu)
        assert ctx.h1.structure_direction == "BEARISH"
        assert ctx.h1.bos_confirmed is True
        assert ctx.h1.bos_direction == "BEARISH"
        assert ctx.h1.structural_clarity == 0.72
        assert ctx.h1.demand_ob_high == 1.2590
        assert ctx.h1.supply_ob_high == 1.2640
        assert ctx.h1.premium_discount == "EQUILIBRIUM"  # range_pos=0.65 → between 0.3 and 0.7

    def test_h1_premium_discount_classification(self):
        mu = _make_mu()
        ctx = build_timeframe_context(mu)
        # range_position = 0.65 → between 0.3 and 0.7 → EQUILIBRIUM
        assert ctx.h1.premium_discount == "EQUILIBRIUM"

    def test_m15_formation_data(self):
        mu = _make_mu()
        ctx = build_timeframe_context(mu)
        assert ctx.m15.pullback_active is True
        assert ctx.m15.pullback_depth_atr == 1.2
        assert ctx.m15.internal_bos is True
        assert ctx.m15.internal_bos_direction == "BEARISH"
        assert ctx.m15.at_order_block is True
        assert ctx.m15.order_block_type == "SUPPLY"

    def test_m5_only_execution_info(self):
        mu = _make_mu()
        ctx = build_timeframe_context(mu)
        assert ctx.m5.spread == 0.00012
        assert ctx.m5.spread_atr_ratio == 0.27
        assert ctx.m5.rejection_present is True
        assert ctx.m5.momentum_direction == "NEUTRAL"
        assert ctx.m5.at_institutional_zone is True

    def test_hierarchy_validates_without_error(self):
        mu = _make_mu()
        ctx = build_timeframe_context(mu)
        assert ctx.hierarchy_valid is True

    def test_hierarchy_flags_contradiction(self):
        """When H4 is strong BEARISH but H1 says BULLISH — flag it."""
        mu = MarketUnderstanding(
            symbol="TEST", timestamp_utc=1000.0,
            h4=H4Understanding(trend="BEARISH", trend_strength=0.8),
            h1=H1Understanding(dominant_trend="BULLISH", structural_clarity=0.3),
            m15=M15Understanding(),
            m5=M5Understanding(),
        )
        ctx = build_timeframe_context(mu)
        # Should produce a warning note (not invalid — divergences happen)
        assert any("contradicts" in note for note in ctx.validation_notes)

    def test_ranging_h4_flags_low_clarity_bos(self):
        """H1 BOS in ranging H4 with low clarity should be flagged."""
        mu = MarketUnderstanding(
            symbol="TEST", timestamp_utc=1000.0,
            h4=H4Understanding(trend="NEUTRAL", trend_strength=0.1),
            h1=H1Understanding(
                bos_confirmed=True, bos_direction="BULLISH",
                dominant_trend="NEUTRAL", structural_clarity=0.3,
            ),
            m15=M15Understanding(),
            m5=M5Understanding(),
        )
        ctx = build_timeframe_context(mu)
        assert any("noise" in note for note in ctx.validation_notes)


# ═══════════════════════════════════════════════════════════════
# INTEGRATION: TimeframeContext → V10MarketState
# ═══════════════════════════════════════════════════════════════


class TestTimeframeToMarketState:
    def test_builds_market_state_from_tf_context(self):
        mu = _make_mu()
        tf_ctx = build_timeframe_context(mu)
        state = build_v10_from_timeframe_context(tf_ctx)

        assert state.symbol == "GBPUSD"
        assert state.h4.trend == "BEARISH"
        assert state.h1.bos_confirmed is True
        assert state.m15.pullback_active is True
        assert state.m5.spread_atr_ratio == 0.27

    def test_receives_correct_hierarchy(self):
        """V10MarketState gets H4 trend, H1 structure, M15 formation, M5 execution."""
        mu = _make_mu()
        tf_ctx = build_timeframe_context(mu)
        state = build_v10_from_timeframe_context(tf_ctx)

        # H4 owns trend
        assert state.h4.trend == "BEARISH"
        assert state.h4.market_phase == "IMPULSE"
        # H1 owns structure
        assert state.h1.dominant_trend == "BEARISH"
        assert state.h1.demand_ob_high == 1.2590
        # M15 owns formation
        assert state.m15.internal_bos_direction == "BEARISH"
        # M5 owns execution only
        assert state.m5.rejection_present is True
        assert state.m5.atr == 0.00045

    def test_regime_derived_from_tf_context(self):
        mu = _make_mu()
        tf_ctx = build_timeframe_context(mu)
        state = build_v10_from_timeframe_context(tf_ctx)

        # Without MarketContextInterpretation, regime derived from H4
        assert state.regime.regime == "TRENDING"  # H4 is trending
        assert state.regime.volatility_state == "NEUTRAL"
