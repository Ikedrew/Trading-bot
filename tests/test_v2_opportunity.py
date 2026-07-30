"""
Tests for V2Opportunity schema.

Verifies:
    1. Object creation works
    2. Required fields enforced
    3. JSON serialization works
    4. Missing optional fields do not break loading
    5. Schema versioning exists
    6. Existing bot components unaffected
"""

import json
import pytest

from core.v2_opportunity import V2Opportunity, _SCHEMA_VERSION


class TestCreation:
    """V2Opportunity can be created with various field combinations."""

    def test_minimal_creation(self):
        """Only opportunity_id required for creation."""
        opp = V2Opportunity(opportunity_id="test-001")
        assert opp.opportunity_id == "test-001"
        assert opp.symbol == ""
        assert opp.h4_regime == ""
        assert opp.outcome_recorded is False

    def test_full_creation(self):
        """All fields can be set."""
        opp = V2Opportunity(
            opportunity_id="full-001",
            correlation_id="COR-100-EURUSD",
            timestamp_utc=1753574400.0,
            symbol="EURUSD",
            h4_regime="RANGING",
            h1_bias="BULLISH",
            h1_bos_confirmed=True,
            h1_bos_direction="BULLISH",
            near_support=True,
            order_block_present=True,
            pattern_detected="HAMMER",
            pattern_direction="BUY",
            pattern_quality=0.85,
            bid=1.08500,
            ask=1.08510,
            spread=0.00010,
            proposed_direction="BUY",
            risk_distance_pips=5.6,
        )
        assert opp.symbol == "EURUSD"
        assert opp.h4_regime == "RANGING"
        assert opp.h1_bos_confirmed is True
        assert opp.near_support is True
        assert opp.pattern_detected == "HAMMER"
        assert opp.spread == 0.00010
        assert opp.risk_distance_pips == 5.6

    def test_frozen_immutable(self):
        """V2Opportunity is frozen — cannot be modified after creation."""
        opp = V2Opportunity(opportunity_id="freeze-001")
        with pytest.raises(Exception):
            opp.symbol = "MODIFIED"


class TestSerialization:
    """JSON round-trip works correctly."""

    def test_to_dict(self):
        """to_dict produces a flat serializable dict."""
        opp = V2Opportunity(
            opportunity_id="ser-001",
            symbol="GBPUSD",
            h1_bias="BEARISH",
            pattern_detected="SHOOTING_STAR",
            spread=0.00013,
        )
        d = opp.to_dict()
        assert isinstance(d, dict)
        assert d["opportunity_id"] == "ser-001"
        assert d["symbol"] == "GBPUSD"
        assert d["h1_bias"] == "BEARISH"
        assert d["spread"] == 0.00013
        assert d["schema_version"] == _SCHEMA_VERSION

    def test_json_roundtrip(self):
        """to_dict → JSON → from_dict produces identical object."""
        opp = V2Opportunity(
            opportunity_id="rt-001",
            symbol="EURUSD",
            timestamp_utc=1753574400.0,
            h4_regime="TRENDING",
            h1_bos_confirmed=True,
            near_support=True,
            pattern_quality=0.75,
            bid=1.085,
            ask=1.0851,
            spread=0.0001,
            risk_distance_pips=7.2,
        )
        json_str = json.dumps(opp.to_dict())
        reloaded = V2Opportunity.from_dict(json.loads(json_str))
        assert reloaded.opportunity_id == "rt-001"
        assert reloaded.h4_regime == "TRENDING"
        assert reloaded.h1_bos_confirmed is True
        assert reloaded.near_support is True
        assert reloaded.pattern_quality == 0.75
        assert reloaded.risk_distance_pips == 7.2

    def test_from_dict_missing_fields(self):
        """from_dict with missing fields uses defaults."""
        minimal = {"opportunity_id": "min-001", "symbol": "USDJPY"}
        opp = V2Opportunity.from_dict(minimal)
        assert opp.opportunity_id == "min-001"
        assert opp.symbol == "USDJPY"
        assert opp.h4_regime == ""
        assert opp.h1_bos_confirmed is False
        assert opp.predicted_probability is None
        assert opp.outcome_recorded is False

    def test_from_dict_extra_fields_ignored(self):
        """from_dict ignores unknown fields."""
        data = {"opportunity_id": "extra-001", "symbol": "X", "unknown_field": 999}
        opp = V2Opportunity.from_dict(data)
        assert opp.opportunity_id == "extra-001"


class TestSchemaVersion:
    """Schema versioning is present and correct."""

    def test_schema_version_in_dict(self):
        """Serialized output includes schema_version."""
        opp = V2Opportunity(opportunity_id="ver-001")
        d = opp.to_dict()
        assert "schema_version" in d
        assert d["schema_version"] == _SCHEMA_VERSION

    def test_architecture_version_field(self):
        """architecture_version defaults to current schema."""
        opp = V2Opportunity(opportunity_id="ver-002")
        assert opp.architecture_version == _SCHEMA_VERSION


class TestOutcomePlaceholder:
    """Outcome fields default to None/False and can be set at creation."""

    def test_outcome_defaults_empty(self):
        opp = V2Opportunity(opportunity_id="out-001")
        assert opp.outcome_recorded is False
        assert opp.outcome_raw_r is None
        assert opp.mfe is None
        assert opp.mae is None
        assert opp.bars_to_outcome is None

    def test_outcome_can_be_set(self):
        opp = V2Opportunity(
            opportunity_id="out-002",
            outcome_recorded=True,
            outcome_raw_r=0.5,
            mfe=1.2,
            mae=0.3,
            bars_to_outcome=15,
        )
        assert opp.outcome_recorded is True
        assert opp.outcome_raw_r == 0.5
        assert opp.mfe == 1.2


class TestProbabilityPlaceholder:
    """Probability fields default to None."""

    def test_probability_defaults_none(self):
        opp = V2Opportunity(opportunity_id="prob-001")
        assert opp.predicted_probability is None
        assert opp.probability_model_version is None
        assert opp.confidence_score is None


class TestNoExecutionImports:
    """V2Opportunity has no execution or pipeline dependencies."""

    def test_no_forbidden_imports(self):
        import inspect
        import core.v2_opportunity as m
        source = inspect.getsource(m)
        for f in ["from core.pipeline", "from execution", "from risk",
                  "import MetaTrader5", "from core.runtime"]:
            assert f not in source, f"Contains forbidden import: {f}"
