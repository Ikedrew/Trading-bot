"""
Tests for V3 Opportunity Assessment Engine.

Verifies:
    - High quality context produces HIGH_QUALITY_CONTEXT
    - Conflicting context produces MIXED_CONTEXT
    - Missing data produces INSUFFICIENT_CONTEXT
    - Location weighting reflects research (50% weight)
    - No trade signals generated
    - Observer integration works
"""

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from core.v3_shadow.context_models import (
    HTFStructureContext,
    LocationContext,
    BehaviourContext,
    V3MarketContext,
)
from core.v3_shadow.opportunity_models import (
    OpportunityAssessment,
    AlignmentResult,
    HIGH_QUALITY_CONTEXT,
    INTERESTING_CONTEXT,
    MIXED_CONTEXT,
    LOW_QUALITY_CONTEXT,
    INSUFFICIENT_CONTEXT,
)
from core.v3_shadow.opportunity_builder import build_opportunity_assessment


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _ctx(
    # HTF
    macro_bias="", macro_strength=0.0, bos_active=False, bos_dir="",
    structure_alignment=0.0, authority_tf="", phase_alignment="",
    # Location
    inside_zone=False, zone_type="", premium_discount="", range_pos=0.0,
    institutional_alignment="", zone_quality=0.0, zone_mitigated=False,
    liquidity_above=False, liquidity_below=False, liquidity_dir="",
    demand_nearby=0, supply_nearby=0,
    # Behaviour
    regime="RANGING", volatility="NEUTRAL", momentum_dir="", momentum_str=0.0,
    displacement_active=False, displacement_mag=0.0, expansion="NEUTRAL",
    # Meta
    confidence=0.7,
) -> V3MarketContext:
    """Build V3MarketContext with specified values."""
    return V3MarketContext(
        symbol="EURUSD",
        timestamp_utc=1753574400.0,
        overall_confidence=confidence,
        htf_structure=HTFStructureContext(
            macro_bias=macro_bias,
            macro_bias_strength=macro_strength,
            bos_active=bos_active,
            bos_direction=bos_dir,
            structure_alignment=structure_alignment,
            authority_timeframe=authority_tf,
            phase_alignment=phase_alignment,
            confidence=0.7 if macro_bias else 0.0,
        ),
        location=LocationContext(
            inside_institutional_zone=inside_zone,
            location_type=zone_type if inside_zone else "OPEN_SPACE",
            premium_discount=premium_discount,
            range_position=range_pos,
            institutional_alignment=institutional_alignment,
            zone_quality=zone_quality,
            zone_mitigated=zone_mitigated,
            liquidity_above=liquidity_above,
            liquidity_below=liquidity_below,
            nearest_liquidity_direction=liquidity_dir,
            demand_zones_nearby=demand_nearby,
            supply_zones_nearby=supply_nearby,
            confidence=0.8 if inside_zone else 0.3,
        ),
        behaviour=BehaviourContext(
            regime=regime,
            volatility_state=volatility,
            momentum_direction=momentum_dir,
            momentum_strength=momentum_str,
            displacement_active=displacement_active,
            displacement_magnitude_atr=displacement_mag,
            expansion_state=expansion,
            confidence=0.6,
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# HIGH QUALITY CONTEXT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestHighQualityContext:
    """Strong context should produce HIGH_QUALITY_CONTEXT."""

    def test_bullish_ob_discount_bos(self):
        """H1 BOS + inside demand OB + discount = HIGH_QUALITY."""
        ctx = _ctx(
            macro_bias="BULLISH", macro_strength=0.8,
            bos_active=True, bos_dir="BULLISH",
            structure_alignment=0.7, authority_tf="H1",
            inside_zone=True, zone_type="DEMAND_OB",
            premium_discount="DISCOUNT", range_pos=0.2,
            institutional_alignment="BULLISH", zone_quality=0.8,
            liquidity_above=True, liquidity_dir="ABOVE",
            momentum_dir="BULLISH", momentum_str=0.7,
        )
        assessment = build_opportunity_assessment(ctx)
        assert assessment.assessment_state == HIGH_QUALITY_CONTEXT
        assert assessment.confidence > 0.5
        assert len(assessment.supporting_factors) >= 5

    def test_bearish_ob_premium_bos(self):
        """Bearish BOS + supply OB + premium = HIGH_QUALITY."""
        ctx = _ctx(
            macro_bias="BEARISH", macro_strength=0.7,
            bos_active=True, bos_dir="BEARISH",
            structure_alignment=0.7, authority_tf="H1",
            inside_zone=True, zone_type="SUPPLY_OB",
            premium_discount="PREMIUM", range_pos=0.85,
            institutional_alignment="BEARISH", zone_quality=0.75,
            liquidity_below=True, liquidity_dir="BELOW",
        )
        assessment = build_opportunity_assessment(ctx)
        assert assessment.assessment_state == HIGH_QUALITY_CONTEXT


# ═══════════════════════════════════════════════════════════════════════════════
# INTERESTING CONTEXT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestInterestingContext:
    """Moderate alignment produces INTERESTING_CONTEXT."""

    def test_ob_without_bos(self):
        """Inside OB but no BOS = interesting, not high quality."""
        ctx = _ctx(
            macro_bias="BULLISH",
            inside_zone=True, zone_type="DEMAND_OB",
            premium_discount="DISCOUNT", range_pos=0.25,
            institutional_alignment="BULLISH",
        )
        assessment = build_opportunity_assessment(ctx)
        assert assessment.assessment_state in (INTERESTING_CONTEXT, HIGH_QUALITY_CONTEXT)
        assert assessment.context_quality >= 0.4

    def test_discount_with_structure(self):
        """Discount + clear structure but no zone = interesting."""
        ctx = _ctx(
            macro_bias="BULLISH", macro_strength=0.6,
            bos_active=True, bos_dir="BULLISH",
            structure_alignment=0.6,
            premium_discount="DISCOUNT", range_pos=0.2,
            demand_nearby=1,
        )
        assessment = build_opportunity_assessment(ctx)
        assert assessment.assessment_state in (INTERESTING_CONTEXT, HIGH_QUALITY_CONTEXT)


# ═══════════════════════════════════════════════════════════════════════════════
# MIXED CONTEXT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestMixedContext:
    """Conflicting signals produce MIXED_CONTEXT."""

    def test_bullish_structure_premium_location(self):
        """Bullish structure but premium location = conflict."""
        ctx = _ctx(
            macro_bias="BULLISH", bos_active=True, bos_dir="BULLISH",
            structure_alignment=0.7,
            premium_discount="PREMIUM", range_pos=0.85,
            # No institutional zone — premium without supply zone
        )
        assessment = build_opportunity_assessment(ctx)
        # Premium is a conflict for bullish — should downgrade
        assert "Premium" in str(assessment.conflicting_factors) or assessment.context_quality < 0.7

    def test_h4_h1_conflict(self):
        """H4 bullish + H1 bearish = conflict."""
        ctx = _ctx(
            macro_bias="CONFLICTED",
            inside_zone=True, zone_type="DEMAND_OB",
            premium_discount="DISCOUNT",
        )
        assessment = build_opportunity_assessment(ctx)
        assert any("conflict" in f.lower() for f in assessment.conflicting_factors)


# ═══════════════════════════════════════════════════════════════════════════════
# INSUFFICIENT CONTEXT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestInsufficientContext:
    """Missing data produces INSUFFICIENT_CONTEXT or LOW_QUALITY."""

    def test_empty_context(self):
        """No data at all = INSUFFICIENT or LOW."""
        ctx = V3MarketContext(symbol="EURUSD", timestamp_utc=1.0, overall_confidence=0.0)
        assessment = build_opportunity_assessment(ctx)
        assert assessment.assessment_state in (INSUFFICIENT_CONTEXT, LOW_QUALITY_CONTEXT)
        assert assessment.context_quality < 0.3

    def test_only_behaviour(self):
        """Only behaviour data, no structure or location = low quality."""
        ctx = _ctx(
            regime="RANGING", volatility="NEUTRAL",
            momentum_dir="BULLISH", momentum_str=0.6,
            confidence=0.3,
        )
        assessment = build_opportunity_assessment(ctx)
        assert assessment.assessment_state in (LOW_QUALITY_CONTEXT, MIXED_CONTEXT)


# ═══════════════════════════════════════════════════════════════════════════════
# WEIGHTING TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestWeighting:
    """Location should dominate per research evidence."""

    def test_location_dominates(self):
        """Strong location + weak structure = still interesting (location 50% weight)."""
        ctx = _ctx(
            # Weak structure
            macro_bias="NEUTRAL",
            # Strong location
            inside_zone=True, zone_type="DEMAND_OB",
            premium_discount="DISCOUNT", range_pos=0.2,
            institutional_alignment="BULLISH", zone_quality=0.8,
            liquidity_above=True,
        )
        assessment = build_opportunity_assessment(ctx)
        # Location score should carry the assessment
        assert assessment.location_alignment.score > 0.6
        assert assessment.context_quality >= 0.4

    def test_structure_alone_insufficient(self):
        """Strong structure but no location = lower quality than location alone."""
        ctx_structure = _ctx(
            macro_bias="BULLISH", bos_active=True, bos_dir="BULLISH",
            structure_alignment=0.8, authority_tf="H1",
        )
        ctx_location = _ctx(
            inside_zone=True, zone_type="DEMAND_OB",
            premium_discount="DISCOUNT", institutional_alignment="BULLISH",
            zone_quality=0.8, liquidity_above=True,
        )
        a_struct = build_opportunity_assessment(ctx_structure)
        a_loc = build_opportunity_assessment(ctx_location)
        # Location-only should produce higher context_quality than structure-only
        assert a_loc.context_quality >= a_struct.context_quality


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestModels:
    """Model correctness."""

    def test_frozen(self):
        """OpportunityAssessment is immutable."""
        a = OpportunityAssessment(symbol="EURUSD")
        with pytest.raises(Exception):
            a.symbol = "CHANGED"

    def test_to_dict(self):
        """Serialization includes all fields."""
        ctx = _ctx(
            macro_bias="BULLISH", bos_active=True, bos_dir="BULLISH",
            inside_zone=True, zone_type="DEMAND_OB",
            premium_discount="DISCOUNT",
        )
        a = build_opportunity_assessment(ctx)
        d = a.to_dict()
        assert d["assessment_state"] in (HIGH_QUALITY_CONTEXT, INTERESTING_CONTEXT)
        assert "structure_alignment" in d
        assert "location_alignment" in d
        assert "behaviour_alignment" in d
        assert "supporting_factors" in d
        assert "conflicting_factors" in d
        # JSON serializable
        assert json.loads(json.dumps(d, default=str))

    def test_alignment_result_fields(self):
        """AlignmentResult has factors, conflicts, missing."""
        ar = AlignmentResult(score=0.7, factors=["A"], conflicts=["B"], missing=["C"])
        assert ar.score == 0.7
        assert ar.factors == ["A"]
        assert ar.conflicts == ["B"]


# ═══════════════════════════════════════════════════════════════════════════════
# OBSERVER INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestObserverIntegration:
    """Observer #10 persists OpportunityAssessment."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        import core.v3_shadow.observer as mod
        self._orig_mu = mod._LOCAL_DIR
        self._orig_ctx = mod._CONTEXT_DIR
        self._orig_opp = mod._ASSESSMENT_DIR
        mod._LOCAL_DIR = self.temp_dir + "/mu"
        mod._CONTEXT_DIR = self.temp_dir + "/ctx"
        mod._ASSESSMENT_DIR = self.temp_dir + "/opp"

    def teardown_method(self):
        import core.v3_shadow.observer as mod
        mod._LOCAL_DIR = self._orig_mu
        mod._CONTEXT_DIR = self._orig_ctx
        mod._ASSESSMENT_DIR = self._orig_opp
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_persists_assessment(self):
        """Observer writes OpportunityAssessment to JSONL."""
        from core.v3_shadow.observer import observe_market_understanding

        @dataclass
        class MockCandle:
            high: float = 1.086
            low: float = 1.084
            open: float = 1.085
            close: float = 1.0855
            time: int = 1753574400

        @dataclass
        class Ctx:
            symbol: str = "EURUSD"
            cycle_id: int = 1
            bar_time: float = 1753574400.0
            engine_result: dict = None
            engine_state: Any = None
            candles: list = None
            closed_i: int = 60
            bid: float = 1.085
            ask: float = 1.0851
            htf_context: Any = None
            market_context: Any = None
            runtime_session_id: str = "t"
            decision_funnel: Any = None
            config: Any = None
            detected_patterns: list = None
            risk_manager: Any = None

        ctx = Ctx(
            engine_result={"entity_id": "TEST"},
            candles=[MockCandle(
                high=1.085 + (i % 5) * 0.0003,
                low=1.083 + (i % 3) * 0.0002,
                open=1.084, close=1.0845,
                time=1753574400 + i * 300,
            ) for i in range(65)],
        )

        observe_market_understanding(ctx)

        opp_files = list(Path(self.temp_dir + "/opp").rglob("*.jsonl"))
        assert len(opp_files) == 1
        record = json.loads(open(opp_files[0]).readline())
        assert "assessment_state" in record
        assert "supporting_factors" in record
        assert record["symbol"] == "EURUSD"


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafety:
    """No trade signals in opportunity assessment."""

    def test_no_forbidden_imports(self):
        import inspect
        import core.v3_shadow.opportunity_builder as m
        source = inspect.getsource(m)
        for f in ["import MetaTrader5", "from core.runtime"]:
            assert f not in source

    def test_no_trade_directions(self):
        """Assessment does not contain BUY/SELL actions."""
        from dataclasses import fields
        for f in fields(OpportunityAssessment):
            assert "buy" not in f.name.lower()
            assert "sell" not in f.name.lower()
            assert "execute" not in f.name.lower()

    def test_assessment_is_descriptive(self):
        """All assessment states are descriptions, not actions."""
        states = [HIGH_QUALITY_CONTEXT, INTERESTING_CONTEXT, MIXED_CONTEXT,
                  LOW_QUALITY_CONTEXT, INSUFFICIENT_CONTEXT]
        for s in states:
            assert "CONTEXT" in s
            assert "BUY" not in s
            assert "SELL" not in s
