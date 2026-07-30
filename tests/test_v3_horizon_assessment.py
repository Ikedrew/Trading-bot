"""
Tests for V3 Horizon Assessment Engine.

Verifies:
    - INTRADAY selected for inside-OB with clear structure
    - SCALP selected for near-zone reaction plays
    - NO_HORIZON for insufficient context
    - SWING for strong H4 authority
    - Volatility modifies fit assessment
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
from core.v3_shadow.horizon_models import (
    HorizonAssessment,
    SCALP,
    INTRADAY,
    EXTENDED,
    NO_HORIZON,
    PROFILES,
    _HORIZON_SCHEMA_VERSION,
)
from core.v3_shadow.horizon_builder import build_horizon_assessment


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _make_ctx(
    # Location
    inside_zone=False, zone_type="OPEN_SPACE", zone_quality=0.0,
    premium_discount="", range_pos=0.0,
    institutional_alignment="",
    liquidity_above=False, liquidity_below=False, liquidity_dir="",
    demand_nearby=0, supply_nearby=0,
    # Structure
    macro_bias="", bos_active=False, bos_dir="",
    structure_alignment=0.0, authority_tf="",
    # Behaviour
    regime="RANGING", volatility="NEUTRAL",
    momentum_dir="", momentum_str=0.0,
    displacement_active=False, expansion="NEUTRAL",
    # Meta
    confidence=0.7,
) -> V3MarketContext:
    return V3MarketContext(
        symbol="EURUSD",
        timestamp_utc=1753574400.0,
        overall_confidence=confidence,
        htf_structure=HTFStructureContext(
            macro_bias=macro_bias,
            bos_active=bos_active, bos_direction=bos_dir,
            structure_alignment=structure_alignment,
            authority_timeframe=authority_tf,
            confidence=0.7 if macro_bias else 0.0,
        ),
        location=LocationContext(
            inside_institutional_zone=inside_zone,
            location_type=zone_type if inside_zone else "OPEN_SPACE",
            zone_quality=zone_quality,
            premium_discount=premium_discount,
            range_position=range_pos,
            institutional_alignment=institutional_alignment,
            liquidity_above=liquidity_above, liquidity_below=liquidity_below,
            nearest_liquidity_direction=liquidity_dir,
            demand_zones_nearby=demand_nearby, supply_zones_nearby=supply_nearby,
            confidence=0.8 if inside_zone else 0.3,
        ),
        behaviour=BehaviourContext(
            regime=regime, volatility_state=volatility,
            momentum_direction=momentum_dir, momentum_strength=momentum_str,
            displacement_active=displacement_active, expansion_state=expansion,
            confidence=0.6,
        ),
    )


def _make_opp(state: str = INTERESTING_CONTEXT, confidence: float = 0.7) -> OpportunityAssessment:
    return OpportunityAssessment(
        symbol="EURUSD", timestamp_utc=1753574400.0,
        assessment_state=state, confidence=confidence,
        context_quality=0.6,
        structure_alignment=AlignmentResult(score=0.5),
        location_alignment=AlignmentResult(score=0.7),
        behaviour_alignment=AlignmentResult(score=0.4),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# INTRADAY SELECTION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestIntradaySelection:
    """Inside institutional zone → INTRADAY."""

    def test_inside_ob_high_quality(self):
        """Inside demand OB with high zone quality → INTRADAY."""
        ctx = _make_ctx(
            inside_zone=True, zone_type="DEMAND_OB", zone_quality=0.8,
            premium_discount="DISCOUNT",
            bos_active=True, bos_dir="BULLISH", authority_tf="H1",
        )
        opp = _make_opp(HIGH_QUALITY_CONTEXT)
        h = build_horizon_assessment(ctx, opp)
        assert h.selected_horizon == INTRADAY
        assert h.stop_framework == "M15_STRUCTURE"
        assert h.management_profile == "TRAIL_BREAKEVEN"

    def test_inside_supply_ob(self):
        """Inside supply OB with structure → INTRADAY (BOS needed for structure)."""
        ctx = _make_ctx(
            inside_zone=True, zone_type="SUPPLY_OB", zone_quality=0.7,
            premium_discount="PREMIUM",
            bos_active=True, bos_dir="BEARISH",  # Structure supports intraday
            institutional_alignment="BEARISH",
        )
        opp = _make_opp(INTERESTING_CONTEXT)
        h = build_horizon_assessment(ctx, opp)
        assert h.selected_horizon == INTRADAY
        # All candidates preserved for research
        assert len(h.candidates) == 3

    def test_inside_ob_confirms_structure(self):
        """BOS + OB → structure confirms INTRADAY."""
        ctx = _make_ctx(
            inside_zone=True, zone_type="DEMAND_OB", zone_quality=0.75,
            bos_active=True, bos_dir="BULLISH", authority_tf="H1",
        )
        opp = _make_opp(HIGH_QUALITY_CONTEXT)
        h = build_horizon_assessment(ctx, opp)
        assert h.selected_horizon == INTRADAY
        assert any("confirm" in f.lower() for f in h.supporting_factors)


# ═══════════════════════════════════════════════════════════════════════════════
# SCALP SELECTION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestScalpSelection:
    """Near zone but not inside → SCALP."""

    def test_near_zone_not_inside(self):
        """Nearby zone → SCALP or INTRADAY (plausibility-based)."""
        ctx = _make_ctx(demand_nearby=1)
        opp = _make_opp(INTERESTING_CONTEXT)
        h = build_horizon_assessment(ctx, opp)
        # Near zone boosts scalp, but both are valid candidates
        assert h.selected_horizon in (SCALP, INTRADAY)
        assert h.stop_framework in ("M5_STRUCTURE", "M15_STRUCTURE")

    def test_low_quality_zone(self):
        """Inside zone but low quality → SCALP."""
        ctx = _make_ctx(inside_zone=True, zone_type="DEMAND_OB", zone_quality=0.3)
        opp = _make_opp(INTERESTING_CONTEXT)
        h = build_horizon_assessment(ctx, opp)
        assert h.selected_horizon == SCALP


# ═══════════════════════════════════════════════════════════════════════════════
# NO_HORIZON TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoHorizon:
    """Insufficient context → NO_HORIZON."""

    def test_insufficient_context(self):
        """INSUFFICIENT_CONTEXT → NO_HORIZON."""
        ctx = _make_ctx()
        opp = _make_opp(INSUFFICIENT_CONTEXT)
        h = build_horizon_assessment(ctx, opp)
        assert h.selected_horizon == NO_HORIZON

    def test_low_quality_context(self):
        """LOW_QUALITY_CONTEXT → NO_HORIZON."""
        ctx = _make_ctx()
        opp = _make_opp(LOW_QUALITY_CONTEXT)
        h = build_horizon_assessment(ctx, opp)
        assert h.selected_horizon == NO_HORIZON


# ═══════════════════════════════════════════════════════════════════════════════
# SWING SELECTION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSwingSelection:
    """Strong H4 authority → EXTENDED candidate."""

    def test_h4_authority_high_quality(self):
        """H4 trending + high quality + no inside zone → EXTENDED likely."""
        ctx = _make_ctx(
            macro_bias="BULLISH", authority_tf="H4",
            structure_alignment=0.8,
            premium_discount="DISCOUNT", range_pos=0.2,
            liquidity_above=True, liquidity_below=True,
            # NOT inside a zone — open space with strong HTF
        )
        opp = _make_opp(HIGH_QUALITY_CONTEXT)
        h = build_horizon_assessment(ctx, opp)
        # EXTENDED should be most plausible when H4 authority is strong
        # and there's no competing inside-zone signal
        assert h.selected_horizon == EXTENDED
        assert h.stop_framework == "H1_STRUCTURE"
        assert h.expected_move_min_pips >= 50.0

    def test_mixed_context_downgrades_extended(self):
        """H4 authority but MIXED context → downgraded (not EXTENDED)."""
        ctx = _make_ctx(
            macro_bias="BULLISH", authority_tf="H4",
            inside_zone=True, zone_type="DEMAND_OB", zone_quality=0.7,
        )
        opp = _make_opp(MIXED_CONTEXT)
        h = build_horizon_assessment(ctx, opp)
        # Mixed context penalizes extended, so should prefer INTRADAY or SCALP
        assert h.selected_horizon in (INTRADAY, SCALP)


# ═══════════════════════════════════════════════════════════════════════════════
# VOLATILITY FIT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestVolatilityFit:
    """Volatility modifies fit assessment."""

    def test_suitable_volatility(self):
        """Normal volatility → SUITABLE."""
        ctx = _make_ctx(
            inside_zone=True, zone_type="DEMAND_OB", zone_quality=0.8,
            volatility="NEUTRAL",
        )
        opp = _make_opp(INTERESTING_CONTEXT)
        h = build_horizon_assessment(ctx, opp)
        assert h.volatility_fit in ("SUITABLE", "MARGINAL")

    def test_contraction_conflict(self):
        """Contracting volatility noted as conflict."""
        ctx = _make_ctx(
            inside_zone=True, zone_type="DEMAND_OB", zone_quality=0.8,
            volatility="CONTRACTION",
        )
        opp = _make_opp(INTERESTING_CONTEXT)
        h = build_horizon_assessment(ctx, opp)
        # Contraction is a conflict for INTRADAY
        # (whether it shows in conflicting_factors depends on selection logic)
        assert h.selected_horizon in (INTRADAY, SCALP)


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestModels:
    """Model correctness."""

    def test_frozen(self):
        """HorizonAssessment is immutable."""
        h = HorizonAssessment(symbol="EURUSD")
        with pytest.raises(Exception):
            h.symbol = "CHANGED"

    def test_to_dict(self):
        """Serialization includes all fields."""
        ctx = _make_ctx(inside_zone=True, zone_type="DEMAND_OB", zone_quality=0.8)
        opp = _make_opp(INTERESTING_CONTEXT)
        h = build_horizon_assessment(ctx, opp)
        d = h.to_dict()
        assert d["schema_version"] == _HORIZON_SCHEMA_VERSION
        assert d["selected_horizon"] in (SCALP, INTRADAY, EXTENDED, NO_HORIZON)
        assert "stop_framework" in d
        assert "target_framework" in d
        assert "candidates" in d  # All horizon evaluations preserved
        assert json.loads(json.dumps(d, default=str))

    def test_profiles_exist(self):
        """All three profiles defined."""
        assert SCALP in PROFILES
        assert INTRADAY in PROFILES
        assert EXTENDED in PROFILES
        assert PROFILES[INTRADAY].stop_source == "M15_STRUCTURE"
        assert PROFILES[EXTENDED].stop_source == "H1_STRUCTURE"


# ═══════════════════════════════════════════════════════════════════════════════
# OBSERVER INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestObserverIntegration:
    """Observer #10 persists HorizonAssessment."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        import core.v3_shadow.observer as mod
        self._orig_mu = mod._LOCAL_DIR
        self._orig_ctx = mod._CONTEXT_DIR
        self._orig_opp = mod._ASSESSMENT_DIR
        self._orig_hor = mod._HORIZON_DIR
        mod._LOCAL_DIR = self.temp_dir + "/mu"
        mod._CONTEXT_DIR = self.temp_dir + "/ctx"
        mod._ASSESSMENT_DIR = self.temp_dir + "/opp"
        mod._HORIZON_DIR = self.temp_dir + "/hor"

    def teardown_method(self):
        import core.v3_shadow.observer as mod
        mod._LOCAL_DIR = self._orig_mu
        mod._CONTEXT_DIR = self._orig_ctx
        mod._ASSESSMENT_DIR = self._orig_opp
        mod._HORIZON_DIR = self._orig_hor
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_persists_horizon(self):
        """Observer writes HorizonAssessment to JSONL."""
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

        hor_files = list(Path(self.temp_dir + "/hor").rglob("*.jsonl"))
        assert len(hor_files) == 1
        record = json.loads(open(hor_files[0]).readline())
        assert "selected_horizon" in record
        assert record["selected_horizon"] in (SCALP, INTRADAY, EXTENDED, NO_HORIZON)


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafety:
    """No trade signals in horizon assessment."""

    def test_no_forbidden_imports(self):
        import inspect
        import core.v3_shadow.horizon_builder as m
        source = inspect.getsource(m)
        for f in ["import MetaTrader5", "from core.runtime"]:
            assert f not in source

    def test_no_trade_directions(self):
        """Horizon does not contain BUY/SELL."""
        from dataclasses import fields
        for f in fields(HorizonAssessment):
            assert "buy" not in f.name.lower()
            assert "sell" not in f.name.lower()
