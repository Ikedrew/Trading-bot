"""
Tests for V3 Market Context Engine.

Verifies:
    - Context models are immutable and serializable
    - HTF structure builder interprets macro bias and alignment correctly
    - Location builder identifies zones, premium/discount, institutional alignment
    - Behaviour builder classifies regime, momentum, displacement
    - Orchestrator combines all layers
    - Observer integration persists context
    - No execution coupling
"""

import json

import pytest

from core.market_understanding.models import (
    MarketUnderstanding,
    H4Understanding,
    H1Understanding,
    M15Understanding,
    M5Understanding,
    M1Understanding,
)
from core.market_understanding.context_models import (
    HTFStructureContext,
    LocationContext,
    BehaviourContext,
    MarketContextInterpretation,
    _CONTEXT_SCHEMA_VERSION,
)
from core.market_understanding.context_builders import (
    build_htf_structure_context,
    build_location_context,
    build_behaviour_context,
    build_market_context_interpretation,
)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _mu(
    h4_trend="", h4_strength=0.0, h4_phase="", h4_vol="",
    h1_trend="", h1_bos=False, h1_bos_dir="", h1_clarity=0.0,
    h1_swing_high=0.0, h1_swing_low=0.0, h1_structure="",
    h1_eq_highs=0.0, h1_eq_lows=0.0, h1_session_high=0.0, h1_session_low=0.0,
    m15_range_pos=0.0, m15_swing_high=0.0, m15_swing_low=0.0,
    m15_displacement=False, m15_disp_mag=0.0, m15_pullback=False, m15_pullback_depth=0.0,
    m15_demand_ob=0.0, m15_supply_ob=0.0, m15_fvg=0.0,
    m5_at_zone=False, m5_zone_type="", m5_momentum_dir="", m5_momentum_str=0.0,
    m5_rejection=False, m5_rejection_dir="", m5_atr=0.001,
) -> MarketUnderstanding:
    """Build a MarketUnderstanding with specified fields."""
    return MarketUnderstanding(
        symbol="EURUSD",
        timestamp_utc=1753574400.0,
        confidence=0.7,
        h4=H4Understanding(
            trend=h4_trend, trend_strength=h4_strength,
            market_phase=h4_phase, volatility_state=h4_vol),
        h1=H1Understanding(
            dominant_trend=h1_trend, bos_confirmed=h1_bos, bos_direction=h1_bos_dir,
            structural_clarity=h1_clarity, swing_high=h1_swing_high, swing_low=h1_swing_low,
            structure_type=h1_structure,
            equal_highs_level=h1_eq_highs, equal_lows_level=h1_eq_lows,
            session_high=h1_session_high, session_low=h1_session_low),
        m15=M15Understanding(
            range_position=m15_range_pos, swing_high=m15_swing_high, swing_low=m15_swing_low,
            displacement_present=m15_displacement, displacement_magnitude_atr=m15_disp_mag,
            pullback_active=m15_pullback, pullback_depth_atr=m15_pullback_depth,
            refined_demand_ob_high=m15_demand_ob, refined_supply_ob_high=m15_supply_ob,
            nearest_fvg=m15_fvg),
        m5=M5Understanding(
            at_institutional_zone=m5_at_zone, zone_type=m5_zone_type,
            momentum_direction=m5_momentum_dir, momentum_strength=m5_momentum_str,
            rejection_present=m5_rejection, rejection_direction=m5_rejection_dir,
            atr=m5_atr),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestContextModels:
    """Context models are frozen and serializable."""

    def test_v3_market_context_frozen(self):
        ctx = MarketContextInterpretation(symbol="EURUSD")
        with pytest.raises(Exception):
            ctx.symbol = "CHANGED"

    def test_to_dict(self):
        ctx = MarketContextInterpretation(
            symbol="EURUSD", timestamp_utc=1.0, overall_confidence=0.75)
        d = ctx.to_dict()
        assert d["schema_version"] == _CONTEXT_SCHEMA_VERSION
        assert d["symbol"] == "EURUSD"
        assert d["overall_confidence"] == 0.75
        assert "htf_structure" in d
        assert "location" in d
        assert "behaviour" in d

    def test_json_serializable(self):
        ctx = MarketContextInterpretation(symbol="EURUSD", timestamp_utc=1.0)
        s = json.dumps(ctx.to_dict(), default=str)
        assert json.loads(s)["symbol"] == "EURUSD"


# ═══════════════════════════════════════════════════════════════════════════════
# HTF STRUCTURE BUILDER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestHTFStructureBuilder:
    """HTF structure context interpretation."""

    def test_bullish_agreement(self):
        """H4+H1 both bullish → BULLISH macro bias."""
        mu = _mu(h4_trend="BULLISH", h4_strength=0.7, h1_trend="BULLISH", h1_clarity=0.8)
        htf = build_htf_structure_context(mu)
        assert htf.macro_bias == "BULLISH"
        assert htf.macro_bias_strength > 0.5

    def test_bearish_agreement(self):
        """H4+H1 both bearish → BEARISH macro bias."""
        mu = _mu(h4_trend="BEARISH", h4_strength=0.6, h1_trend="BEARISH", h1_clarity=0.7)
        htf = build_htf_structure_context(mu)
        assert htf.macro_bias == "BEARISH"

    def test_conflict(self):
        """H4 bullish + H1 bearish → CONFLICTED."""
        mu = _mu(h4_trend="BULLISH", h1_trend="BEARISH")
        htf = build_htf_structure_context(mu)
        assert htf.macro_bias == "CONFLICTED"

    def test_bos_active(self):
        """H1 BOS detected and propagated."""
        mu = _mu(h1_bos=True, h1_bos_dir="BULLISH", h1_swing_high=1.088)
        htf = build_htf_structure_context(mu)
        assert htf.bos_active is True
        assert htf.bos_direction == "BULLISH"

    def test_authority_timeframe(self):
        """Strong H4 trend gives H4 authority."""
        mu = _mu(h4_trend="BULLISH", h4_strength=0.8)
        htf = build_htf_structure_context(mu)
        assert htf.authority_timeframe == "H4"

    def test_h1_authority_on_bos(self):
        """H1 BOS without strong H4 gives H1 authority."""
        mu = _mu(h1_bos=True, h1_bos_dir="BULLISH", h1_swing_high=1.088)
        htf = build_htf_structure_context(mu)
        assert htf.authority_timeframe == "H1"

    def test_no_data(self):
        """Empty understanding → neutral context."""
        mu = _mu()
        htf = build_htf_structure_context(mu)
        assert htf.macro_bias == "NEUTRAL"
        assert htf.confidence == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# LOCATION BUILDER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestLocationBuilder:
    """Location context interpretation."""

    def test_inside_demand_ob(self):
        """Price inside demand OB detected."""
        mu = _mu(m5_at_zone=True, m5_zone_type="DEMAND_OB")
        loc = build_location_context(mu)
        assert loc.inside_institutional_zone is True
        assert loc.location_type == "DEMAND_OB"

    def test_discount_zone(self):
        """Low range position → DISCOUNT."""
        mu = _mu(m15_range_pos=0.2, m15_swing_high=1.087, m15_swing_low=1.083)
        loc = build_location_context(mu)
        assert loc.premium_discount == "DISCOUNT"
        assert loc.range_position == pytest.approx(0.2, abs=0.01)

    def test_premium_zone(self):
        """High range position → PREMIUM."""
        mu = _mu(m15_range_pos=0.8, m15_swing_high=1.087, m15_swing_low=1.083)
        loc = build_location_context(mu)
        assert loc.premium_discount == "PREMIUM"

    def test_institutional_alignment_bullish(self):
        """Demand zone + discount = BULLISH institutional alignment."""
        mu = _mu(m5_at_zone=True, m5_zone_type="DEMAND_OB",
                 m15_range_pos=0.2, m15_swing_high=1.087, m15_swing_low=1.083)
        loc = build_location_context(mu)
        assert loc.institutional_alignment == "BULLISH"

    def test_institutional_alignment_bearish(self):
        """Supply zone + premium = BEARISH institutional alignment."""
        mu = _mu(m5_at_zone=True, m5_zone_type="SUPPLY_OB",
                 m15_range_pos=0.8, m15_swing_high=1.087, m15_swing_low=1.083)
        loc = build_location_context(mu)
        assert loc.institutional_alignment == "BEARISH"

    def test_liquidity_context(self):
        """Equal highs/lows and session levels detected."""
        mu = _mu(h1_eq_highs=1.090, h1_session_low=1.080)
        loc = build_location_context(mu)
        assert loc.liquidity_above is True
        assert loc.liquidity_below is True

    def test_open_space(self):
        """No zone → OPEN_SPACE."""
        mu = _mu(m5_at_zone=False)
        loc = build_location_context(mu)
        assert loc.location_type == "OPEN_SPACE"
        assert loc.inside_institutional_zone is False

    def test_no_data(self):
        """Empty understanding → empty location."""
        mu = _mu()
        loc = build_location_context(mu)
        assert loc.premium_discount == ""
        assert loc.confidence == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# BEHAVIOUR BUILDER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestBehaviourBuilder:
    """Behaviour context interpretation."""

    def test_trending(self):
        """H4 bullish trend → TRENDING regime."""
        mu = _mu(h4_trend="BULLISH", h4_strength=0.7)
        beh = build_behaviour_context(mu)
        assert beh.regime == "TRENDING"
        assert beh.regime_confidence > 0.5

    def test_ranging(self):
        """No trend → RANGING."""
        mu = _mu(h4_phase="CONSOLIDATION")
        beh = build_behaviour_context(mu)
        assert beh.regime == "RANGING"

    def test_momentum(self):
        """M5 bullish momentum propagated."""
        mu = _mu(m5_momentum_dir="BULLISH", m5_momentum_str=0.8)
        beh = build_behaviour_context(mu)
        assert beh.momentum_direction == "BULLISH"
        assert beh.momentum_strength == 0.8

    def test_displacement(self):
        """M15 displacement detected."""
        mu = _mu(m15_displacement=True, m15_disp_mag=2.5, m5_momentum_dir="BULLISH")
        beh = build_behaviour_context(mu)
        assert beh.displacement_active is True
        assert beh.displacement_magnitude_atr == 2.5
        assert beh.expansion_state == "EXPANDING"

    def test_volatility(self):
        """H4 expansion volatility."""
        mu = _mu(h4_vol="EXPANSION")
        beh = build_behaviour_context(mu)
        assert beh.volatility_state == "EXPANSION"
        assert beh.volatility_level == 0.8

    def test_no_data(self):
        """Empty → RANGING default."""
        mu = _mu()
        beh = build_behaviour_context(mu)
        assert beh.regime == "RANGING"


# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestOrchestrator:
    """MarketContextInterpretation orchestrator."""

    def test_full_context(self):
        """Produces complete context with all three layers."""
        mu = _mu(
            h4_trend="BULLISH", h4_strength=0.7,
            h1_trend="BULLISH", h1_bos=True, h1_bos_dir="BULLISH",
            h1_swing_high=1.088, h1_clarity=0.8,
            m15_range_pos=0.25, m15_swing_high=1.087, m15_swing_low=1.083,
            m5_at_zone=True, m5_zone_type="DEMAND_OB",
            m5_momentum_dir="BULLISH", m5_momentum_str=0.7,
        )
        ctx = build_market_context_interpretation(mu)
        assert ctx.symbol == "EURUSD"
        assert ctx.htf_structure.macro_bias == "BULLISH"
        assert ctx.location.inside_institutional_zone is True
        assert ctx.location.premium_discount == "DISCOUNT"
        assert ctx.behaviour.momentum_direction == "BULLISH"
        assert ctx.overall_confidence > 0.0
        assert len(ctx.observations) > 0

    def test_empty_understanding(self):
        """Minimal understanding produces neutral context."""
        mu = MarketUnderstanding(symbol="USDJPY", timestamp_utc=1.0)
        ctx = build_market_context_interpretation(mu)
        assert ctx.symbol == "USDJPY"
        assert ctx.htf_structure.macro_bias == "NEUTRAL"
        assert ctx.location.location_type == "OPEN_SPACE"
        assert ctx.behaviour.regime == "RANGING"

    def test_observations_combined(self):
        """Observations from all layers combined with prefixes."""
        mu = _mu(h4_trend="BULLISH", h1_trend="BULLISH",
                 m5_at_zone=True, m5_zone_type="DEMAND_OB",
                 m5_momentum_dir="BULLISH", m5_momentum_str=0.8)
        ctx = build_market_context_interpretation(mu)
        htf_obs = [o for o in ctx.observations if o.startswith("[HTF]")]
        loc_obs = [o for o in ctx.observations if o.startswith("[LOC]")]
        beh_obs = [o for o in ctx.observations if o.startswith("[BEH]")]
        assert len(htf_obs) > 0
        assert len(loc_obs) > 0
        assert len(beh_obs) > 0


# TestObserverIntegration removed — v3_shadow per-dataset persistence was retired in the Production V1 consolidation (fields now integrated into decision_trace / market_context).


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafety:
    """No execution coupling in context engine."""

    def test_no_forbidden_imports_models(self):
        import inspect
        import core.market_understanding.context_models as m
        source = inspect.getsource(m)
        for f in ["import MetaTrader5", "from core.pipeline", "from core.runtime"]:
            assert f not in source

    def test_no_forbidden_imports_builders(self):
        import inspect
        import core.market_understanding.context_builders as m
        source = inspect.getsource(m)
        for f in ["import MetaTrader5", "from core.runtime"]:
            assert f not in source

    def test_no_trade_signals_in_context(self):
        """MarketContextInterpretation fields do not contain trade actions."""
        from dataclasses import fields as dc_fields
        for f in dc_fields(MarketContextInterpretation):
            assert "execute" not in f.name.lower()
            assert "order" not in f.name.lower()
            assert "position" not in f.name.lower()
