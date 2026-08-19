"""Phase E — Decision ID Lineage Tests.

Verifies the complete identity hierarchy from observation_id through all layers.
"""

import pytest
from core.v10.pipeline import V10Pipeline
from core.v10.risk_model import AccountContext
from core.v10.broker_context import BrokerContext
from core.v10.scanner_adapter import _build_order_intent
from core.v3_shadow.models import (
    MarketUnderstanding, H1Understanding, M5Understanding,
)
from unittest.mock import MagicMock


def _run_pipeline():
    """Run a complete pipeline to get all stage outputs."""
    mu = MarketUnderstanding(
        symbol="EURUSD", timestamp_utc=1785400000.0, confidence=0.8,
        h1=H1Understanding(
            bos_confirmed=True, bos_direction="BEARISH",
            dominant_trend="BEARISH", structural_clarity=0.75,
            swing_high=1.095, swing_low=1.085,
            active_supply_ob_high=1.094, active_supply_ob_low=1.0935,
            active_demand_ob_high=1.086, active_demand_ob_low=1.0855,
        ),
        m5=M5Understanding(
            atr=0.0006, spread=0.00012,
            rejection_present=True, rejection_direction="BEARISH",
            at_institutional_zone=True, zone_type="SUPPLY_OB",
        ),
    )
    account = AccountContext(balance=10000.0, equity=10000.0)
    broker = BrokerContext(
        connected=True, symbol_available=True, market_open=True,
        spread=0.00012, available_margin=5000.0,
        tick_value=1.0, tick_size=0.00001,
        volume_min=0.01, volume_max=100.0, volume_step=0.01, point=0.00001,
    )
    pipeline = V10Pipeline()
    return pipeline.process(mu, None, account, broker)


class TestObservationIdPropagation:
    """observation_id must exist in every V10 stage."""

    def test_opportunity_has_observation_id(self):
        result = _run_pipeline()
        assert result.opportunity.observation_id != ""
        assert len(result.opportunity.observation_id) == 16

    def test_strategy_references_observation_id(self):
        result = _run_pipeline()
        assert result.strategy.opportunity_id == result.opportunity.observation_id

    def test_horizon_references_observation_id(self):
        result = _run_pipeline()
        assert result.horizon.opportunity_id == result.opportunity.observation_id

    def test_entry_references_observation_id(self):
        result = _run_pipeline()
        assert result.entry.opportunity_id == result.opportunity.observation_id

    def test_risk_references_observation_id(self):
        result = _run_pipeline()
        assert result.risk.opportunity_id == result.opportunity.observation_id

    def test_execution_references_observation_id(self):
        result = _run_pipeline()
        assert result.execution.opportunity_id == result.opportunity.observation_id

    def test_all_stages_same_id(self):
        """Single root identity across all stages."""
        result = _run_pipeline()
        root = result.opportunity.observation_id
        assert root != ""
        assert result.strategy.opportunity_id == root
        assert result.horizon.opportunity_id == root
        assert result.entry.opportunity_id == root
        assert result.risk.opportunity_id == root
        assert result.execution.opportunity_id == root


class TestObservationIdDeterministic:
    """Same inputs must produce same observation_id."""

    def test_same_symbol_timestamp_same_id(self):
        r1 = _run_pipeline()
        r2 = _run_pipeline()
        assert r1.opportunity.observation_id == r2.opportunity.observation_id

    def test_different_timestamp_different_id(self):
        mu1 = MarketUnderstanding(symbol="EURUSD", timestamp_utc=1000.0)
        mu2 = MarketUnderstanding(symbol="EURUSD", timestamp_utc=2000.0)
        pipeline = V10Pipeline()
        r1 = pipeline.process(mu1)
        r2 = pipeline.process(mu2)
        assert r1.opportunity.observation_id != r2.opportunity.observation_id

    def test_different_symbol_different_id(self):
        mu1 = MarketUnderstanding(symbol="EURUSD", timestamp_utc=1000.0)
        mu2 = MarketUnderstanding(symbol="GBPUSD", timestamp_utc=1000.0)
        pipeline = V10Pipeline()
        r1 = pipeline.process(mu1)
        r2 = pipeline.process(mu2)
        assert r1.opportunity.observation_id != r2.opportunity.observation_id


class TestOrderIntentLineage:
    """OrderIntent must carry observation_id for execution tracing."""

    def test_risk_id_is_observation_id(self):
        result = _run_pipeline()
        if result.approved:
            intent = _build_order_intent(result, "EURUSD")
            assert intent.risk_id == result.opportunity.observation_id

    def test_metadata_decision_id_is_observation_id(self):
        result = _run_pipeline()
        if result.approved:
            intent = _build_order_intent(result, "EURUSD")
            assert intent.metadata["decision_id"] == result.opportunity.observation_id

    def test_metadata_contains_strategy(self):
        result = _run_pipeline()
        if result.approved:
            intent = _build_order_intent(result, "EURUSD")
            assert "strategy_family" in intent.metadata
            assert intent.metadata["strategy_family"] == result.strategy.strategy_family

    def test_metadata_contains_horizon(self):
        result = _run_pipeline()
        if result.approved:
            intent = _build_order_intent(result, "EURUSD")
            assert intent.metadata["horizon"] == result.horizon.horizon_type


class TestDecisionRecordLineage:
    """Persistence record must use observation_id as decision_id."""

    def test_decision_record_uses_observation_id(self):
        from core.v10.persistence_adapter import build_v10_decision_record
        result = _run_pipeline()
        record = build_v10_decision_record(result)
        assert record["decision_id"] == result.opportunity.observation_id

    def test_ledger_entry_uses_observation_id(self):
        from core.v10.persistence_adapter import build_v10_ledger_entry
        result = _run_pipeline()
        entry = build_v10_ledger_entry(result)
        assert entry["correlation_id"] == result.opportunity.observation_id
        assert entry["observation_id"] == result.opportunity.observation_id

    def test_ledger_entry_preserves_entity_id(self):
        """entity_id is preserved as-is; observation_id is added alongside as a separate field."""
        from core.v10.persistence_adapter import build_v10_ledger_entry
        result = _run_pipeline()
        entry = build_v10_ledger_entry(result)
        # entity_id is preserved unchanged (same value as before this pass)
        assert entry["entity_id"] == result.opportunity.observation_id
        # observation_id is added alongside as a separate field
        assert entry["observation_id"] == result.opportunity.observation_id
        # Both fields exist in the entry
        assert "entity_id" in entry
        assert "observation_id" in entry


class TestNoCompetingRoot:
    """No V10 component should generate a second root identity."""

    def test_strategy_does_not_generate_own_id(self):
        """StrategyDecision.opportunity_id references parent, not self-generated."""
        result = _run_pipeline()
        # opportunity_id should be the SAME as observation_id, not a new UUID
        assert result.strategy.opportunity_id == result.opportunity.observation_id

    def test_horizon_does_not_generate_own_id(self):
        result = _run_pipeline()
        assert result.horizon.opportunity_id == result.opportunity.observation_id

    def test_entry_does_not_generate_own_id(self):
        result = _run_pipeline()
        assert result.entry.opportunity_id == result.opportunity.observation_id

    def test_no_uuid_in_v10_pipeline(self):
        """V10 pipeline should not generate random UUIDs — uses deterministic hash."""
        import inspect
        from core.v10 import pipeline, opportunity_engine
        for module in [pipeline, opportunity_engine]:
            source = inspect.getsource(module)
            assert "uuid4" not in source
            assert "uuid.uuid4" not in source
