"""Phase F — Outcome Lineage Tests.

Verifies that trade outcomes can be traced back to V10 observation_id.
"""

import pytest
from core.trade_identity import TradeIdentity


class TestTradeIdentityHasObservationId:
    """TradeIdentity must carry observation_id for direct lineage."""

    def test_observation_id_field_exists(self):
        ti = TradeIdentity(correlation_id="cor_123", observation_id="obs_abc")
        assert ti.observation_id == "obs_abc"

    def test_default_observation_id_is_empty(self):
        ti = TradeIdentity(correlation_id="cor_123")
        assert ti.observation_id == ""

    def test_to_dict_includes_observation_id(self):
        ti = TradeIdentity(correlation_id="x", observation_id="obs_xyz")
        d = ti.to_dict()
        assert d["observation_id"] == "obs_xyz"

    def test_from_dict_restores_observation_id(self):
        d = {"correlation_id": "x", "observation_id": "obs_restored"}
        ti = TradeIdentity.from_dict(d)
        assert ti.observation_id == "obs_restored"

    def test_frozen(self):
        ti = TradeIdentity(correlation_id="x", observation_id="y")
        with pytest.raises(Exception):
            ti.observation_id = "changed"  # type: ignore


class TestLineageFromV10ToOutcome:
    """V10 observation_id must flow from OrderIntent through to TradeIdentity."""

    def test_order_intent_carries_observation_id(self):
        from core.v10.scanner_adapter import _build_order_intent
        from unittest.mock import MagicMock

        result = MagicMock()
        result.execution.order_details.direction = "BUY"
        result.execution.order_details.entry_price = 1.09
        result.execution.order_details.stop_loss = 1.088
        result.execution.order_details.take_profit = 1.094
        result.execution.order_details.volume = 0.25
        result.execution.order_details.order_type = "MARKET"
        result.strategy.strategy_family = "MEAN_REVERSION"
        result.horizon.horizon_type = "SCALP"
        result.opportunity.observation_id = "v10_root_001"
        result.opportunity.opportunity_type = "ZONE_REACTION"

        intent = _build_order_intent(result, "EURUSD")
        # TradeIdentity will read from intent.risk_id
        assert intent.risk_id == "v10_root_001"

    def test_trade_identity_can_receive_from_intent(self):
        """The flow: intent.risk_id → TradeIdentity.observation_id."""
        from risk.models import OrderIntent
        from strategy.signals import Side

        intent = OrderIntent(
            symbol="EURUSD", side=Side.BUY, volume=0.25,
            entry_reference=1.09, sl=1.088, tp=1.094,
            risk_id="v10_root_002",
        )
        # Simulate what live_scanner does:
        ti = TradeIdentity(
            correlation_id="cor_test",
            observation_id=intent.risk_id,  # V10 lineage
            strategy=intent.pattern,
            pattern=intent.pattern,
        )
        assert ti.observation_id == "v10_root_002"


class TestMultipleScenarios:
    def test_no_trade_has_observation_id_in_decision_record(self):
        """NO_TRADE decisions store observation_id directly."""
        from core.v10.persistence_adapter import build_v10_decision_record
        from core.v10.pipeline import V10Pipeline
        from core.market_understanding.models import MarketUnderstanding
        from core.v10.risk_model import AccountContext
        from core.v10.broker_context import BrokerContext

        mu = MarketUnderstanding(symbol="EURUSD", timestamp_utc=1000.0)
        result = V10Pipeline().process(mu, None, AccountContext(), BrokerContext())
        record = build_v10_decision_record(result)

        # NO_TRADE should still have decision_id = observation_id
        assert record["decision_id"] == result.opportunity.observation_id
        assert record["decision_id"] != ""

    def test_execute_fills_observation_id_through_intent(self):
        """EXECUTE decisions carry observation_id in OrderIntent.risk_id."""
        from core.v10.scanner_adapter import _build_order_intent
        from unittest.mock import MagicMock

        result = MagicMock()
        result.approved = True
        result.execution.order_details.direction = "SELL"
        result.execution.order_details.entry_price = 1.095
        result.execution.order_details.stop_loss = 1.097
        result.execution.order_details.take_profit = 1.091
        result.execution.order_details.volume = 0.10
        result.execution.order_details.order_type = "MARKET"
        result.strategy.strategy_family = "TREND_CONTINUATION"
        result.horizon.horizon_type = "EXTENDED"
        result.opportunity.observation_id = "exec_test_obs"
        result.opportunity.opportunity_type = "STRUCTURE_SHIFT"

        intent = _build_order_intent(result, "GBPUSD")
        assert intent.risk_id == "exec_test_obs"
        # TradeIdentity would get: observation_id = intent.risk_id
        ti = TradeIdentity(
            correlation_id="test_cor",
            observation_id=intent.risk_id,
        )
        assert ti.observation_id == "exec_test_obs"


class TestNoOrphanedOutcomes:
    def test_trade_identity_empty_is_detectable(self):
        """Empty identity is explicitly detectable — no silent missing."""
        ti = TradeIdentity.empty()
        assert ti.correlation_id == ""
        assert ti.observation_id == ""

    def test_empty_observation_id_means_no_v10_lineage(self):
        """Trades without observation_id are from legacy or recovery."""
        ti = TradeIdentity(correlation_id="legacy_cor")
        assert ti.observation_id == ""  # Explicitly empty — not silently missing
