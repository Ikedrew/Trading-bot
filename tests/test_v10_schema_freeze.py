"""Phase H — V10 Schema Freeze Tests.

Verifies the frozen research schema contract is enforced.
"""

import pytest
import json
from core.v10.schema_contract import (
    SCHEMA_VERSION, CRITICAL_FIELDS, EXECUTE_CRITICAL_FIELDS,
    SCHEMA_FIELDS, S3_DATASETS, validate_decision_record,
)
from core.v10.persistence_adapter import build_v10_decision_record
from core.v10.pipeline import V10Pipeline
from core.v10.risk_model import AccountContext
from core.v10.broker_context import BrokerContext
from core.v3_shadow.models import (
    MarketUnderstanding, H4Understanding, H1Understanding,
    M15Understanding, M5Understanding,
)
from core.v3_shadow.context_models import (
    V3MarketContext, HTFStructureContext, LocationContext, BehaviourContext,
)


def _full_pipeline():
    mu = MarketUnderstanding(
        symbol="EURUSD", timestamp_utc=1785400000.0, confidence=0.85,
        h4=H4Understanding(trend="NEUTRAL", trend_strength=0.15),
        h1=H1Understanding(
            bos_confirmed=True, bos_direction="BEARISH",
            dominant_trend="BEARISH", structural_clarity=0.80,
            swing_high=1.0920, swing_low=1.0850,
            active_supply_ob_high=1.0910, active_supply_ob_low=1.0905,
            active_demand_ob_high=1.0860, active_demand_ob_low=1.0855,
            session_high=1.0930, session_low=1.0840,
        ),
        m15=M15Understanding(pullback_active=True, pullback_depth_atr=1.3, range_position=0.75),
        m5=M5Understanding(
            rejection_present=True, rejection_direction="BEARISH",
            rejection_strength_atr=0.9, at_institutional_zone=True,
            zone_type="SUPPLY_OB", atr=0.00055, spread=0.00012,
        ),
    )
    ctx = V3MarketContext(
        symbol="EURUSD", timestamp_utc=1785400000.0,
        htf_structure=HTFStructureContext(macro_bias="NEUTRAL", structure_alignment=0.30, bos_active=True, bos_direction="BEARISH"),
        location=LocationContext(location_type="SUPPLY_OB", inside_institutional_zone=True, premium_discount="PREMIUM", range_position=0.75, zone_quality=0.85, liquidity_below=True),
        behaviour=BehaviourContext(regime="RANGING", volatility_state="NEUTRAL", momentum_direction="NEUTRAL"),
        overall_confidence=0.8,
    )
    account = AccountContext(balance=10000.0, equity=10000.0, leverage=100, currency="USD")
    broker = BrokerContext(connected=True, symbol_available=True, market_open=True, symbol="EURUSD",
                           spread=0.00012, available_margin=5000.0, tick_value=1.0, tick_size=0.00001,
                           volume_min=0.01, volume_max=100.0, volume_step=0.01, point=0.00001)
    return V10Pipeline().process(mu, ctx, account, broker)


def _reject_pipeline():
    mu = MarketUnderstanding(symbol="GBPUSD", timestamp_utc=1785500000.0)
    return V10Pipeline().process(mu, None, AccountContext(balance=10000.0), BrokerContext())


class TestSchemaVersionContract:
    def test_schema_version_is_defined(self):
        assert SCHEMA_VERSION == "v10_decision_v1"

    def test_record_has_schema_version(self):
        record = build_v10_decision_record(_full_pipeline())
        assert record["schema_version"] == SCHEMA_VERSION

    def test_reject_record_has_schema_version(self):
        record = build_v10_decision_record(_reject_pipeline())
        assert record["schema_version"] == SCHEMA_VERSION


class TestCriticalFieldsContract:
    def test_critical_fields_defined(self):
        assert len(CRITICAL_FIELDS) >= 5
        assert "observation_id" in CRITICAL_FIELDS
        assert "symbol" in CRITICAL_FIELDS
        assert "final_action" in CRITICAL_FIELDS

    def test_execute_record_passes_validation(self):
        record = build_v10_decision_record(_full_pipeline())
        valid, violations = validate_decision_record(record)
        assert valid, f"Violations: {violations}"

    def test_reject_record_passes_validation(self):
        record = build_v10_decision_record(_reject_pipeline())
        valid, violations = validate_decision_record(record)
        assert valid, f"Violations: {violations}"

    def test_empty_record_fails_validation(self):
        valid, violations = validate_decision_record({})
        assert not valid
        assert len(violations) >= 5

    def test_missing_observation_id_fails(self):
        record = build_v10_decision_record(_full_pipeline())
        record.pop("observation_id", None)
        record.pop("decision_id", None)  # decision_id = observation_id in current impl
        valid, violations = validate_decision_record(record)
        # Should detect missing observation_id
        assert any("observation_id" in v for v in violations) or not valid


class TestSchemaEvolutionRules:
    """Fields must never be removed or renamed between versions."""

    def test_all_v1_fields_defined(self):
        assert "observation_id" in SCHEMA_FIELDS
        assert "market_state" in SCHEMA_FIELDS
        assert "opportunity" in SCHEMA_FIELDS
        assert "strategy_family" in SCHEMA_FIELDS
        assert "horizon" in SCHEMA_FIELDS
        assert "entry_method" in SCHEMA_FIELDS
        assert "risk_approved" in SCHEMA_FIELDS
        assert "execution_approved" in SCHEMA_FIELDS

    def test_field_types_defined(self):
        for field, spec in SCHEMA_FIELDS.items():
            assert "type" in spec, f"Field {field} missing type"
            assert "required" in spec, f"Field {field} missing required"
            assert "nullable" in spec, f"Field {field} missing nullable"


class TestSerializationStability:
    def test_json_roundtrip(self):
        record = build_v10_decision_record(_full_pipeline())
        json_str = json.dumps(record, default=str)
        restored = json.loads(json_str)
        assert restored["observation_id"] == record["observation_id"]
        assert restored["symbol"] == record["symbol"]
        assert restored["final_action"] == record["final_action"]

    def test_nested_structures_survive(self):
        record = build_v10_decision_record(_full_pipeline())
        json_str = json.dumps(record, default=str)
        restored = json.loads(json_str)
        assert isinstance(restored["market_state"], dict)
        assert isinstance(restored["opportunity"], dict)
        assert "reasoning" in restored["opportunity"]

    def test_null_fields_serialize_correctly(self):
        record = build_v10_decision_record(_reject_pipeline())
        json_str = json.dumps(record, default=str)
        restored = json.loads(json_str)
        # Null strategy for rejected is valid
        assert restored.get("strategy_family") is None or restored.get("strategy_family") == ""


class TestHistoricalCompatibility:
    """Old records must remain readable."""

    def test_minimal_v1_record_validates(self):
        """A record with only critical fields should pass basic validation."""
        minimal = {
            "schema_version": "v10_decision_v1",
            "observation_id": "test_123",
            "decision_id": "test_123",
            "correlation_id": "cor_test",
            "symbol": "EURUSD",
            "timestamp_utc": 1785400000.0,
            "engine_version": "V10",
            "final_action": "NO_TRADE",
            "rejection_stage": "opportunity",
            "rejection_reason": "INVALID",
            "market_state": {"regime": "RANGING"},
            "opportunity": {"state": "INVALID"},
            "risk_approved": False,
            "execution_approved": False,
            "lineage": {"engine": "V10"},
        }
        valid, violations = validate_decision_record(minimal)
        assert valid, f"Violations: {violations}"

    def test_missing_optional_fields_still_valid(self):
        """Records missing optional fields (horizon, entry) should still validate."""
        record = {
            "schema_version": "v10_decision_v1",
            "observation_id": "opt_test",
            "decision_id": "opt_test",
            "correlation_id": "cor_opt",
            "symbol": "USDJPY",
            "timestamp_utc": 1785400000.0,
            "engine_version": "V10",
            "final_action": "NO_TRADE",
            "market_state": {"regime": "NEUTRAL"},
            "opportunity": {"state": "INVALID"},
            "risk_approved": False,
            "execution_approved": False,
            "lineage": {"engine": "V10"},
        }
        valid, violations = validate_decision_record(record)
        assert valid


class TestS3DatasetStructure:
    def test_datasets_defined(self):
        assert "decisions" in S3_DATASETS
        assert "executions" in S3_DATASETS
        assert "outcomes" in S3_DATASETS

    def test_all_datasets_have_path(self):
        for name, ds in S3_DATASETS.items():
            assert "path" in ds
            assert "{symbol}" in ds["path"]
            assert "{date}" in ds["path"]

    def test_all_datasets_join_on_observation_id(self):
        """Primary join key is observation_id for research queries."""
        assert S3_DATASETS["decisions"]["join_key"] == "observation_id"
        assert S3_DATASETS["outcomes"]["join_key"] == "observation_id"

    def test_executions_join_on_decision_id(self):
        """Execution attempts link via decision_id."""
        assert S3_DATASETS["executions"]["join_key"] == "decision_id"
