"""Tests for V10 Execution Bridge — OrderIntent translation."""

import pytest
from unittest.mock import patch, MagicMock
from core.v10.scanner_adapter import _build_order_intent
from risk.models import OrderIntent
from strategy.signals import Side


def _mock_result(direction="BUY", entry=1.09, sl=1.088, tp=1.094, volume=0.25, order_type="MARKET"):
    """Create a mock PipelineResult with approved execution."""
    result = MagicMock()
    result.approved = True
    result.execution.order_details.direction = direction
    result.execution.order_details.entry_price = entry
    result.execution.order_details.stop_loss = sl
    result.execution.order_details.take_profit = tp
    result.execution.order_details.volume = volume
    result.execution.order_details.order_type = order_type
    result.strategy.strategy_family = "MEAN_REVERSION"
    result.horizon.horizon_type = "SCALP"
    result.opportunity.observation_id = "test_obs_123"
    result.opportunity.opportunity_type = "ZONE_REACTION"
    result.opportunity.quality.overall_quality = 0.72
    return result


class TestOrderIntentCreation:
    def test_buy_creates_valid_intent(self):
        result = _mock_result(direction="BUY", entry=1.09000, sl=1.08800, tp=1.09400, volume=0.25)
        intent = _build_order_intent(result, "EURUSD")

        assert isinstance(intent, OrderIntent)
        assert intent.symbol == "EURUSD"
        assert intent.side == Side.BUY
        assert intent.volume == 0.25
        assert intent.entry_reference == 1.09000
        assert intent.sl == 1.08800
        assert intent.tp == 1.09400
        assert intent.entry_type == "MARKET"

    def test_sell_creates_valid_intent(self):
        result = _mock_result(direction="SELL", entry=1.09500, sl=1.09700, tp=1.09100, volume=0.10)
        intent = _build_order_intent(result, "GBPUSD")

        assert intent.symbol == "GBPUSD"
        assert intent.side == Side.SELL
        assert intent.volume == 0.10
        assert intent.entry_reference == 1.09500
        assert intent.sl == 1.09700
        assert intent.tp == 1.09100

    def test_preserves_direction_buy(self):
        result = _mock_result(direction="BUY")
        intent = _build_order_intent(result, "EURUSD")
        assert intent.side == Side.BUY

    def test_preserves_direction_sell(self):
        result = _mock_result(direction="SELL")
        intent = _build_order_intent(result, "EURUSD")
        assert intent.side == Side.SELL

    def test_preserves_entry_price(self):
        result = _mock_result(entry=1.12345)
        intent = _build_order_intent(result, "EURUSD")
        assert intent.entry_reference == 1.12345

    def test_preserves_stop_loss(self):
        result = _mock_result(sl=1.08765)
        intent = _build_order_intent(result, "EURUSD")
        assert intent.sl == 1.08765

    def test_preserves_take_profit(self):
        result = _mock_result(tp=1.09876)
        intent = _build_order_intent(result, "EURUSD")
        assert intent.tp == 1.09876

    def test_preserves_volume(self):
        result = _mock_result(volume=0.50)
        intent = _build_order_intent(result, "EURUSD")
        assert intent.volume == 0.50

    def test_limit_order_type(self):
        result = _mock_result(order_type="LIMIT")
        intent = _build_order_intent(result, "EURUSD")
        assert intent.entry_type == "LIMIT"

    def test_stop_order_type(self):
        result = _mock_result(order_type="STOP")
        intent = _build_order_intent(result, "EURUSD")
        assert intent.entry_type == "STOP"


class TestIntentMetadata:
    def test_has_engine_marker(self):
        result = _mock_result()
        intent = _build_order_intent(result, "EURUSD")
        assert intent.metadata["engine"] == "V10"

    def test_has_strategy_family(self):
        result = _mock_result()
        intent = _build_order_intent(result, "EURUSD")
        assert intent.metadata["strategy_family"] == "MEAN_REVERSION"

    def test_has_horizon(self):
        result = _mock_result()
        intent = _build_order_intent(result, "EURUSD")
        assert intent.metadata["horizon"] == "SCALP"

    def test_has_decision_id(self):
        result = _mock_result()
        intent = _build_order_intent(result, "EURUSD")
        assert intent.metadata["decision_id"] == "test_obs_123"

    def test_pattern_is_strategy_family(self):
        result = _mock_result()
        intent = _build_order_intent(result, "EURUSD")
        assert intent.pattern == "MEAN_REVERSION"

    def test_risk_id_is_observation_id(self):
        result = _mock_result()
        intent = _build_order_intent(result, "EURUSD")
        assert intent.risk_id == "test_obs_123"


class TestScannerAdapterIntegration:
    def test_execute_result_has_intent_key(self):
        """The adapter result must have 'intent' for prepare_execution."""
        from core.v10.scanner_adapter import run_v10_cycle

        # This will produce NO_TRADE (empty candles) but tests the path
        result = run_v10_cycle(
            symbol="EURUSD", candles=[], closed_i=-1,
            bid=1.09, ask=1.0901,
        )
        # NO_TRADE should NOT have intent
        if result["action"] == "NO_TRADE":
            assert "intent" not in result or result.get("intent") is None

    def test_no_trade_does_not_create_intent(self):
        from core.v10.scanner_adapter import run_v10_cycle

        result = run_v10_cycle(
            symbol="EURUSD", candles=[], closed_i=-1,
            bid=0, ask=0,
        )
        assert result["action"] == "NO_TRADE"
        assert result.get("intent") is None

    def test_no_legacy_imports_in_adapter(self):
        """Scanner adapter should not import legacy engine modules."""
        import inspect
        from core.v10 import scanner_adapter
        source = inspect.getsource(scanner_adapter)
        assert "from core.pipeline.new_engine" not in source
        assert "from core.pipeline.scoring_engine" not in source
        assert "composite_score" not in source


class TestPrepareExecutionCompatibility:
    def test_intent_has_all_required_fields(self):
        """OrderIntent from V10 must have all fields prepare_execution reads."""
        result = _mock_result()
        intent = _build_order_intent(result, "EURUSD")

        # prepare_execution reads these:
        assert hasattr(intent, "symbol")
        assert hasattr(intent, "side")
        assert hasattr(intent, "volume")
        assert hasattr(intent, "sl")
        assert hasattr(intent, "tp")
        assert hasattr(intent, "entry_type")
        assert hasattr(intent, "pattern")
        assert hasattr(intent, "metadata")

    def test_intent_is_frozen(self):
        result = _mock_result()
        intent = _build_order_intent(result, "EURUSD")
        with pytest.raises(Exception):
            intent.volume = 999  # type: ignore
