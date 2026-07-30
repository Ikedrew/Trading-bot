"""
Tests for V3 Entry Assessment Engine.

Verifies:
    - VALID_ENTRY_CONFIRMATION when BOS + zone + direction align
    - WEAK_ENTRY when partial confirmation exists
    - NO_ENTRY when context exists but no trigger fires
    - INSUFFICIENT when no horizon
    - Multiple candidates evaluated
    - Direction derived from context
    - Observer integration
"""

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from core.v3_shadow.context_models import (
    HTFStructureContext, LocationContext, BehaviourContext, V3MarketContext,
)
from core.v3_shadow.opportunity_models import (
    OpportunityAssessment, AlignmentResult,
    HIGH_QUALITY_CONTEXT, INTERESTING_CONTEXT, INSUFFICIENT_CONTEXT,
)
from core.v3_shadow.horizon_models import HorizonAssessment, INTRADAY, NO_HORIZON
from core.v3_shadow.entry_models import (
    EntryAssessment, EntryCandidate,
    VALID_ENTRY_CONFIRMATION, WEAK_ENTRY_CONFIRMATION,
    NO_ENTRY_CONFIRMATION, INSUFFICIENT_ENTRY_DATA,
    TRIGGER_BOS, TRIGGER_RETEST, TRIGGER_REJECTION, TRIGGER_MOMENTUM,
    TRIGGER_DISPLACEMENT, TRIGGER_NONE,
)
from core.v3_shadow.entry_builder import build_entry_assessment


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _ctx(
    macro_bias="BULLISH", bos_active=True, bos_dir="BULLISH",
    inside_zone=True, zone_type="DEMAND_OB", institutional_align="BULLISH",
    premium_discount="DISCOUNT", zone_quality=0.7,
    momentum_dir="BULLISH", momentum_str=0.6,
    displacement=False, disp_dir="", disp_mag=0.0,
    volatility="NEUTRAL",
) -> V3MarketContext:
    return V3MarketContext(
        symbol="EURUSD", timestamp_utc=1753574400.0, overall_confidence=0.7,
        htf_structure=HTFStructureContext(
            macro_bias=macro_bias, bos_active=bos_active, bos_direction=bos_dir,
            structure_alignment=0.7, confidence=0.7,
        ),
        location=LocationContext(
            inside_institutional_zone=inside_zone,
            location_type=zone_type if inside_zone else "OPEN_SPACE",
            institutional_alignment=institutional_align,
            premium_discount=premium_discount,
            zone_quality=zone_quality,
            confidence=0.8,
        ),
        behaviour=BehaviourContext(
            momentum_direction=momentum_dir, momentum_strength=momentum_str,
            displacement_active=displacement, displacement_direction=disp_dir,
            displacement_magnitude_atr=disp_mag,
            volatility_state=volatility,
            confidence=0.6,
        ),
    )


def _opp(state=INTERESTING_CONTEXT, conf=0.7):
    return OpportunityAssessment(
        symbol="EURUSD", timestamp_utc=1753574400.0,
        assessment_state=state, confidence=conf, context_quality=0.6,
    )


def _horizon(selected=INTRADAY, conf=0.7):
    return HorizonAssessment(
        symbol="EURUSD", timestamp_utc=1753574400.0,
        selected_horizon=selected, confidence=conf,
        expected_move_min_pips=20.0, expected_move_max_pips=50.0,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# VALID ENTRY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidEntry:
    """Strong confirmation → VALID_ENTRY_CONFIRMATION."""

    def test_bos_plus_zone_plus_momentum(self):
        """BOS + inside demand zone + bullish momentum → VALID."""
        ctx = _ctx(bos_active=True, bos_dir="BULLISH",
                   inside_zone=True, institutional_align="BULLISH",
                   momentum_dir="BULLISH", momentum_str=0.7)
        entry = build_entry_assessment(ctx, _opp(), _horizon(), current_price=1.085)
        assert entry.entry_state == VALID_ENTRY_CONFIRMATION
        assert entry.direction == "BULLISH"
        assert len([c for c in entry.candidates if c.detected]) >= 2

    def test_retest_entry(self):
        """BOS + back at zone = retest → strongest trigger."""
        ctx = _ctx(bos_active=True, bos_dir="BULLISH",
                   inside_zone=True, institutional_align="BULLISH",
                   momentum_dir="BULLISH", momentum_str=0.6)
        entry = build_entry_assessment(ctx, _opp(HIGH_QUALITY_CONTEXT), _horizon())
        # Retest should be detected (BOS + inside zone)
        retest_candidates = [c for c in entry.candidates if c.trigger_type == TRIGGER_RETEST]
        assert retest_candidates[0].detected is True
        assert retest_candidates[0].strength >= 0.8


# ═══════════════════════════════════════════════════════════════════════════════
# WEAK ENTRY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestWeakEntry:
    """Partial confirmation → WEAK_ENTRY_CONFIRMATION."""

    def test_momentum_only(self):
        """Momentum present but no BOS, not at zone → WEAK or NO."""
        ctx = _ctx(bos_active=False, inside_zone=False,
                   institutional_align="NEUTRAL",
                   momentum_dir="BULLISH", momentum_str=0.6)
        entry = build_entry_assessment(ctx, _opp(), _horizon())
        assert entry.entry_state in (WEAK_ENTRY_CONFIRMATION, NO_ENTRY_CONFIRMATION)

    def test_bos_opposing_direction(self):
        """BOS exists but opposes institutional alignment → reduced quality."""
        ctx = _ctx(bos_active=True, bos_dir="BEARISH",
                   inside_zone=True, institutional_align="BULLISH",
                   momentum_dir="NEUTRAL", momentum_str=0.3)
        entry = build_entry_assessment(ctx, _opp(), _horizon())
        # BOS detected but opposing → lower strength
        bos_cand = [c for c in entry.candidates if c.trigger_type == TRIGGER_BOS][0]
        assert bos_cand.detected is True
        assert bos_cand.strength < 0.5  # Opposing reduces strength


# ═══════════════════════════════════════════════════════════════════════════════
# NO ENTRY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoEntry:
    """No confirmation triggers → NO_ENTRY_CONFIRMATION."""

    def test_no_triggers(self):
        """No BOS, no momentum, not at zone → NO_ENTRY."""
        ctx = _ctx(bos_active=False, inside_zone=False,
                   institutional_align="NEUTRAL",
                   momentum_dir="NEUTRAL", momentum_str=0.2)
        entry = build_entry_assessment(ctx, _opp(), _horizon())
        assert entry.entry_state == NO_ENTRY_CONFIRMATION
        assert entry.primary_trigger == TRIGGER_NONE


# ═══════════════════════════════════════════════════════════════════════════════
# INSUFFICIENT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestInsufficient:
    """Missing upstream data → INSUFFICIENT_ENTRY_DATA."""

    def test_no_horizon(self):
        """NO_HORIZON → insufficient entry data."""
        ctx = _ctx()
        entry = build_entry_assessment(ctx, _opp(), _horizon(NO_HORIZON))
        assert entry.entry_state == INSUFFICIENT_ENTRY_DATA

    def test_insufficient_context(self):
        """INSUFFICIENT_CONTEXT opportunity → no entry."""
        ctx = _ctx()
        entry = build_entry_assessment(ctx, _opp(INSUFFICIENT_CONTEXT), _horizon())
        assert entry.entry_state == INSUFFICIENT_ENTRY_DATA


# ═══════════════════════════════════════════════════════════════════════════════
# CANDIDATE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestCandidates:
    """Multiple trigger hypotheses evaluated."""

    def test_all_candidates_present(self):
        """All 5 trigger types are evaluated."""
        ctx = _ctx()
        entry = build_entry_assessment(ctx, _opp(), _horizon())
        trigger_types = {c.trigger_type for c in entry.candidates}
        assert TRIGGER_BOS in trigger_types
        assert TRIGGER_DISPLACEMENT in trigger_types
        assert TRIGGER_REJECTION in trigger_types
        assert TRIGGER_MOMENTUM in trigger_types
        assert TRIGGER_RETEST in trigger_types

    def test_candidates_preserved_for_research(self):
        """All candidates in to_dict for research comparison."""
        ctx = _ctx()
        entry = build_entry_assessment(ctx, _opp(), _horizon())
        d = entry.to_dict()
        assert "candidates" in d
        assert len(d["candidates"]) == 5


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestModels:
    """Model correctness."""

    def test_frozen(self):
        e = EntryAssessment(symbol="EURUSD")
        with pytest.raises(Exception):
            e.symbol = "X"

    def test_to_dict_json_serializable(self):
        ctx = _ctx()
        entry = build_entry_assessment(ctx, _opp(), _horizon(), current_price=1.085)
        d = entry.to_dict()
        assert d["symbol"] == "EURUSD"
        assert "entry_state" in d
        assert "primary_trigger" in d
        s = json.dumps(d, default=str)
        assert json.loads(s)["direction"] == "BULLISH"


# ═══════════════════════════════════════════════════════════════════════════════
# OBSERVER INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestObserver:
    """Observer #10 persists EntryAssessment."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        import core.v3_shadow.observer as mod
        self._orig = {
            "mu": mod._LOCAL_DIR,
            "ctx": mod._CONTEXT_DIR,
            "opp": mod._ASSESSMENT_DIR,
            "hor": mod._HORIZON_DIR,
            "entry": mod._ENTRY_DIR,
            "risk": mod._RISK_DIR,
        }
        mod._LOCAL_DIR = self.temp_dir + "/mu"
        mod._CONTEXT_DIR = self.temp_dir + "/ctx"
        mod._ASSESSMENT_DIR = self.temp_dir + "/opp"
        mod._HORIZON_DIR = self.temp_dir + "/hor"
        mod._ENTRY_DIR = self.temp_dir + "/entry"
        mod._RISK_DIR = self.temp_dir + "/risk"

    def teardown_method(self):
        import core.v3_shadow.observer as mod
        mod._LOCAL_DIR = self._orig["mu"]
        mod._CONTEXT_DIR = self._orig["ctx"]
        mod._ASSESSMENT_DIR = self._orig["opp"]
        mod._HORIZON_DIR = self._orig["hor"]
        mod._ENTRY_DIR = self._orig["entry"]
        mod._RISK_DIR = self._orig["risk"]
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_persists_entry(self):
        """Observer writes EntryAssessment to JSONL."""
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
            bid: float = 1.08500
            ask: float = 1.08510
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

        entry_files = list(Path(self.temp_dir + "/entry").rglob("*.jsonl"))
        assert len(entry_files) == 1
        record = json.loads(open(entry_files[0]).readline())
        assert "entry_state" in record
        assert "primary_trigger" in record
        assert "candidates" in record


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafety:
    def test_no_forbidden_imports(self):
        import inspect
        import core.v3_shadow.entry_builder as m
        source = inspect.getsource(m)
        for f in ["import MetaTrader5", "from core.runtime"]:
            assert f not in source

    def test_no_execution_fields(self):
        from dataclasses import fields
        for f in fields(EntryAssessment):
            assert "execute" not in f.name.lower()
            assert "order" not in f.name.lower()
            assert "position" not in f.name.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# BEHAVIOUR TYPE CLASSIFICATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

from core.v3_shadow.entry_models import (
    get_behaviour_type,
    BEHAVIOUR_STRUCTURE_ALIGNMENT,
    BEHAVIOUR_DISPLACEMENT,
    BEHAVIOUR_REJECTION,
    BEHAVIOUR_RETEST,
    BEHAVIOUR_MOMENTUM_TRANSITION,
    BEHAVIOUR_UNKNOWN,
)


class TestBehaviourTypeMapping:
    """Trigger observations map to correct behaviour types."""

    def test_bos_maps_to_structure(self):
        assert get_behaviour_type("BOS_CONFIRMATION") == BEHAVIOUR_STRUCTURE_ALIGNMENT

    def test_choch_maps_to_structure(self):
        assert get_behaviour_type("CHOCH_CONFIRMATION") == BEHAVIOUR_STRUCTURE_ALIGNMENT

    def test_displacement_maps_correctly(self):
        assert get_behaviour_type("DISPLACEMENT_CANDLE") == BEHAVIOUR_DISPLACEMENT

    def test_rejection_maps_correctly(self):
        assert get_behaviour_type("REJECTION_CANDLE") == BEHAVIOUR_REJECTION

    def test_retest_maps_correctly(self):
        assert get_behaviour_type("RETEST_ENTRY") == BEHAVIOUR_RETEST

    def test_momentum_maps_correctly(self):
        assert get_behaviour_type("MOMENTUM_SHIFT") == BEHAVIOUR_MOMENTUM_TRANSITION

    def test_none_maps_to_unknown(self):
        assert get_behaviour_type("NONE") == BEHAVIOUR_UNKNOWN

    def test_unknown_trigger_maps_to_unknown(self):
        assert get_behaviour_type("SOMETHING_ELSE") == BEHAVIOUR_UNKNOWN


class TestBehaviourTypeInAssessment:
    """EntryAssessment contains correct behaviour type."""

    def test_retest_entry_has_retest_behaviour(self):
        """Retest trigger → RETEST_BEHAVIOUR type."""
        ctx = _ctx(bos_active=True, bos_dir="BULLISH",
                   inside_zone=True, institutional_align="BULLISH",
                   momentum_dir="BULLISH", momentum_str=0.6)
        entry = build_entry_assessment(ctx, _opp(), _horizon())
        # Retest is strongest when BOS + inside zone
        if entry.primary_trigger == "RETEST_ENTRY":
            assert entry.entry_behaviour_type == BEHAVIOUR_RETEST

    def test_behaviour_type_in_to_dict(self):
        """to_dict includes entry_behaviour_type."""
        ctx = _ctx()
        entry = build_entry_assessment(ctx, _opp(), _horizon(), current_price=1.085)
        d = entry.to_dict()
        assert "entry_behaviour_type" in d
        assert d["entry_behaviour_type"] in (
            BEHAVIOUR_STRUCTURE_ALIGNMENT,
            BEHAVIOUR_DISPLACEMENT,
            BEHAVIOUR_REJECTION,
            BEHAVIOUR_RETEST,
            BEHAVIOUR_MOMENTUM_TRANSITION,
            BEHAVIOUR_UNKNOWN,
        )

    def test_no_entry_has_unknown_behaviour(self):
        """NO_ENTRY_CONFIRMATION maps trigger NONE → UNKNOWN behaviour."""
        ctx = _ctx(bos_active=False, inside_zone=False,
                   institutional_align="NEUTRAL",
                   momentum_dir="NEUTRAL", momentum_str=0.2)
        entry = build_entry_assessment(ctx, _opp(), _horizon())
        if entry.entry_state == NO_ENTRY_CONFIRMATION:
            assert entry.entry_behaviour_type == BEHAVIOUR_UNKNOWN
