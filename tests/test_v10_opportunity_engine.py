"""Tests for V10 Opportunity Engine."""

import pytest
from core.v10.market_state import (
    V10MarketState, H4State, H1State, M15State, M5State,
    RegimeState, LocationState, HTFAlignment,
)
from core.v10.opportunity_assessment import OpportunityAssessment
from core.v10.opportunity_engine import assess_opportunity


def _strong_opportunity_state():
    """Market state with strong opportunity indicators."""
    return V10MarketState(
        symbol="EURUSD", timestamp_utc=1785400000.0,
        h4=H4State(trend="BEARISH", trend_strength=0.6, market_phase="IMPULSE"),
        h1=H1State(
            dominant_trend="BEARISH", structure_type="LH_LL",
            bos_confirmed=True, bos_direction="BEARISH",
            structural_clarity=0.80,
            swing_high=1.0920, swing_low=1.0850,
        ),
        m15=M15State(
            pullback_active=True, pullback_depth_atr=1.5,
            retracement_pct=0.6, range_position=0.82,
            internal_bos=True, internal_bos_direction="BEARISH",
        ),
        m5=M5State(
            rejection_present=True, rejection_direction="BEARISH",
            rejection_strength_atr=0.9, confirmation_candle=True,
            spread=0.00012, spread_atr_ratio=0.2, atr=0.0006,
        ),
        regime=RegimeState(regime="RANGING", volatility_state="NEUTRAL"),
        location=LocationState(
            premium_discount="PREMIUM", range_position=0.82,
            liquidity_below=True,
        ),
        htf_alignment=HTFAlignment(
            macro_bias="BEARISH", structure_alignment=0.80,
        ),
    )


def _weak_opportunity_state():
    """Market state with no meaningful opportunity."""
    return V10MarketState(
        symbol="EURUSD", timestamp_utc=1785400000.0,
        h4=H4State(trend="NEUTRAL", trend_strength=0.1),
        h1=H1State(
            dominant_trend="NEUTRAL", structural_clarity=0.3,
            bos_confirmed=False,
        ),
        m15=M15State(pullback_active=False),
        m5=M5State(
            rejection_present=False, at_institutional_zone=False,
            spread=0.00012, spread_atr_ratio=0.2, atr=0.0006,
        ),
        regime=RegimeState(regime="RANGING", volatility_state="NEUTRAL"),
        location=LocationState(
            location_type="OPEN_SPACE", inside_institutional_zone=False,
            zone_quality=0.0, premium_discount="EQUILIBRIUM",
        ),
        htf_alignment=HTFAlignment(structure_alignment=0.2),
    )


class TestOpportunityEngineValid:
    def test_strong_context_produces_valid(self):
        state = _strong_opportunity_state()
        result = assess_opportunity(state)
        assert result.opportunity_state == "VALID"

    def test_valid_has_directional_bias(self):
        state = _strong_opportunity_state()
        result = assess_opportunity(state)
        assert result.directional_bias == "BEARISH"

    def test_valid_has_opportunity_type(self):
        state = _strong_opportunity_state()
        result = assess_opportunity(state)
        assert result.opportunity_type in (
            "ZONE_REACTION", "STRUCTURE_SHIFT", "TREND_CONTINUATION",
            "LIQUIDITY_SWEEP", "RANGE_REACTION",
        )

    def test_valid_has_quality_scores(self):
        state = _strong_opportunity_state()
        result = assess_opportunity(state)
        assert result.quality.location_score > 0.4
        assert result.quality.structure_score > 0.3
        assert result.quality.overall_quality > 0.5

    def test_valid_has_reasoning(self):
        state = _strong_opportunity_state()
        result = assess_opportunity(state)
        assert len(result.reasoning) > 0


class TestOpportunityEngineInvalid:
    def test_weak_context_produces_invalid(self):
        state = _weak_opportunity_state()
        result = assess_opportunity(state)
        assert result.opportunity_state == "INVALID"

    def test_no_location_rejects(self):
        state = _weak_opportunity_state()
        result = assess_opportunity(state)
        assert result.quality.location_score < 0.3

    def test_no_structure_low_score(self):
        state = _weak_opportunity_state()
        result = assess_opportunity(state)
        assert result.quality.structure_score < 0.3

    def test_invalid_has_conflicting_factors(self):
        state = _weak_opportunity_state()
        result = assess_opportunity(state)
        assert len(result.conflicting_factors) > 0


class TestOpportunityEngineHierarchy:
    def test_m5_does_not_determine_direction(self):
        """M5 momentum should NOT override H1 structural direction."""
        state = V10MarketState(
            symbol="TEST", timestamp_utc=1000.0,
            h1=H1State(
                bos_confirmed=True, bos_direction="BEARISH",
                dominant_trend="BEARISH", structural_clarity=0.8,
            ),
            m5=M5State(
                momentum_direction="BULLISH", momentum_strength=0.9,
                at_institutional_zone=True, zone_type="SUPPLY_OB",
            ),
            location=LocationState(
                inside_institutional_zone=True, zone_quality=0.7,
                location_type="SUPPLY_OB",
            ),
            m15=M15State(internal_bos=True, internal_bos_direction="BEARISH"),
            regime=RegimeState(regime="RANGING"),
            htf_alignment=HTFAlignment(structure_alignment=0.7),
        )
        result = assess_opportunity(state)
        # Direction should come from H1 BOS, not M5 momentum
        assert result.directional_bias == "BEARISH"

    def test_h1_structure_authoritative(self):
        """Directional bias comes from H1 structure, not other sources."""
        state = V10MarketState(
            symbol="TEST", timestamp_utc=1000.0,
            h1=H1State(
                bos_confirmed=True, bos_direction="BULLISH",
                dominant_trend="BULLISH", structural_clarity=0.75,
            ),
            m15=M15State(pullback_active=True, pullback_depth_atr=1.0),
            m5=M5State(at_institutional_zone=True, zone_type="DEMAND_OB"),
            location=LocationState(
                inside_institutional_zone=True, zone_quality=0.7,
                location_type="DEMAND_OB", liquidity_above=True,
            ),
            regime=RegimeState(regime="RANGING"),
            htf_alignment=HTFAlignment(structure_alignment=0.6),
        )
        result = assess_opportunity(state)
        assert result.directional_bias == "BULLISH"

    def test_m15_formation_contributes_quality(self):
        """M15 displacement/BOS should increase formation score."""
        state = V10MarketState(
            symbol="TEST", timestamp_utc=1000.0,
            h1=H1State(bos_confirmed=True, bos_direction="BEARISH", structural_clarity=0.7),
            m15=M15State(
                displacement_present=True, displacement_direction="BEARISH",
                displacement_magnitude_atr=2.0,
                internal_bos=True, internal_bos_direction="BEARISH",
            ),
            m5=M5State(at_institutional_zone=True, zone_type="SUPPLY_OB"),
            location=LocationState(
                inside_institutional_zone=True, zone_quality=0.7,
                location_type="SUPPLY_OB",
            ),
            regime=RegimeState(regime="RANGING"),
            htf_alignment=HTFAlignment(structure_alignment=0.7),
        )
        result = assess_opportunity(state)
        assert result.quality.formation_score >= 0.5


class TestOpportunityAssessmentModel:
    def test_to_dict(self):
        state = _strong_opportunity_state()
        result = assess_opportunity(state)
        d = result.to_dict()
        assert "opportunity_state" in d
        assert "quality" in d
        assert "reasoning" in d
        assert d["symbol"] == "EURUSD"

    def test_observation_id_generated(self):
        state = _strong_opportunity_state()
        result = assess_opportunity(state)
        assert len(result.observation_id) == 16

    def test_observation_id_deterministic(self):
        state = _strong_opportunity_state()
        r1 = assess_opportunity(state)
        r2 = assess_opportunity(state)
        assert r1.observation_id == r2.observation_id

    def test_immutable(self):
        state = _strong_opportunity_state()
        result = assess_opportunity(state)
        with pytest.raises(Exception):
            result.opportunity_state = "INVALID"  # type: ignore
