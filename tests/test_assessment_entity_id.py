"""
Tests for entity_id on OpportunityAssessment.

Verifies:
- Assessment stores the supplied entity_id
- Serialization preserves entity_id
- Older payloads without entity_id still work (backward compat)
- assessment.entity_id matches engine_result.entity_id
"""

import sys
from pathlib import Path
from dataclasses import dataclass

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.models.opportunity_assessment import OpportunityAssessment


def _make_assessment(**overrides):
    """Create a minimal valid OpportunityAssessment."""
    defaults = dict(
        symbol="EURUSD",
        cycle_id=42,
        bar_time=1700000000,
        pattern="BULLISH_ENGULFING",
        side="BUY",
        pattern_quality=1.0,
        selected_strategy="CONTINUATION",
        strategy_confidence=0.72,
        regime="TRENDING",
        regime_confidence=0.85,
        eligible_strategies=("CONTINUATION",),
        weights_used="strategy_specific",
        components={"pattern_quality": 1.0},
        score_neutral=0.62,
        score_strategy=0.68,
        score_delta=0.06,
        market_state="STRUCTURED",
        market_state_confidence=0.78,
        delta_stability=0.72,
        bias_alignment=0.8,
        trend_alignment=0.9,
        chop_clarity=0.75,
        volatility_quality=0.8,
        confirmation_pre=0.85,
        htf_alignment=0.88,
        h4_alignment=0.7,
    )
    defaults.update(overrides)
    return OpportunityAssessment(**defaults)


class TestEntityIdStored:
    def test_entity_id_stored(self):
        """Assessment stores the supplied entity_id."""
        a = _make_assessment(entity_id="EURUSD_1700000000")
        assert a.entity_id == "EURUSD_1700000000"

    def test_entity_id_default_empty(self):
        """When not provided, entity_id defaults to empty string."""
        a = _make_assessment()
        assert a.entity_id == ""

    def test_entity_id_frozen(self):
        """entity_id cannot be mutated after construction."""
        a = _make_assessment(entity_id="EURUSD_1700000000")
        with pytest.raises(Exception):
            a.entity_id = "MODIFIED"


class TestEntityIdSerialization:
    def test_to_dict_includes_entity_id(self):
        """to_dict() includes entity_id in output."""
        a = _make_assessment(entity_id="EURUSD_1700000000")
        d = a.to_dict()
        assert d["entity_id"] == "EURUSD_1700000000"

    def test_to_dict_empty_entity_id(self):
        """to_dict() includes empty entity_id when not set."""
        a = _make_assessment()
        d = a.to_dict()
        assert d["entity_id"] == ""
        assert "entity_id" in d  # Field always present


class TestBackwardCompatibility:
    def test_construction_without_entity_id(self):
        """Older code that doesn't pass entity_id still works."""
        # This simulates existing test code / older callers
        a = OpportunityAssessment(
            symbol="EURUSD", cycle_id=1, bar_time=100,
            pattern="HAMMER", side="BUY", pattern_quality=0.5,
            selected_strategy=None, strategy_confidence=0.0,
            regime="TRENDING", regime_confidence=0.5,
            eligible_strategies=(), weights_used="global_fallback",
            components={}, score_neutral=0.5, score_strategy=0.5,
            score_delta=0.0, market_state="STRUCTURED",
            market_state_confidence=0.5, delta_stability=0.5,
            bias_alignment=0.5, trend_alignment=0.5, chop_clarity=0.5,
            volatility_quality=0.5, confirmation_pre=0.5,
            htf_alignment=0.5, h4_alignment=0.5,
        )
        assert a.entity_id == ""  # Default


class TestEntityIdConsistency:
    def test_matches_engine_result_format(self):
        """Assessment entity_id matches the f'{symbol}_{bar_time}' format."""
        a = _make_assessment(
            symbol="GBPUSD",
            bar_time=1700005000,
            entity_id="GBPUSD_1700005000",
        )
        expected = f"{a.symbol}_{a.bar_time}"
        assert a.entity_id == expected

    def test_assessment_and_engine_share_identity(self):
        """Simulate: engine creates entity_id, passes to assessment — they match."""
        # Engine creates entity_id
        symbol = "USDJPY"
        bar_time = 1700002000
        engine_entity_id = f"{symbol}_{bar_time}"

        # Assessment receives it
        a = _make_assessment(
            symbol=symbol,
            bar_time=bar_time,
            entity_id=engine_entity_id,
        )

        assert a.entity_id == engine_entity_id
        assert a.entity_id == f"{a.symbol}_{a.bar_time}"
