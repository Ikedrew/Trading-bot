"""Phase D.5 — V10 Execution Bridge Validation Tests.

Verifies that V10 decisions flow through to MT5 without modification.
"""

import pytest
from unittest.mock import MagicMock
from core.v10.scanner_adapter import _build_order_intent
from risk.models import OrderIntent
from strategy.signals import Side


def _v10_result(direction="BUY", entry=1.09000, sl=1.08800, tp=1.09400,
                volume=0.25, order_type="MARKET", strategy="MEAN_REVERSION",
                horizon="SCALP", obs_id="bridge_test_001"):
    """Mock a complete V10 PipelineResult."""
    result = MagicMock()
    result.approved = True
    result.execution.order_details.direction = direction
    result.execution.order_details.entry_price = entry
    result.execution.order_details.stop_loss = sl
    result.execution.order_details.take_profit = tp
    result.execution.order_details.volume = volume
    result.execution.order_details.order_type = order_type
    result.strategy.strategy_family = strategy
    result.horizon.horizon_type = horizon
    result.opportunity.observation_id = obs_id
    result.opportunity.opportunity_type = "ZONE_REACTION"
    return result


class TestFieldPreservation:
    """Every V10 field must arrive at OrderIntent unchanged."""

    def test_buy_direction_preserved(self):
        intent = _build_order_intent(_v10_result(direction="BUY"), "EURUSD")
        assert intent.side == Side.BUY

    def test_sell_direction_preserved(self):
        intent = _build_order_intent(_v10_result(direction="SELL"), "EURUSD")
        assert intent.side == Side.SELL

    def test_entry_price_preserved(self):
        intent = _build_order_intent(_v10_result(entry=1.12345), "EURUSD")
        assert intent.entry_reference == 1.12345

    def test_stop_loss_preserved(self):
        intent = _build_order_intent(_v10_result(sl=1.08765), "EURUSD")
        assert intent.sl == 1.08765

    def test_take_profit_preserved(self):
        intent = _build_order_intent(_v10_result(tp=1.09876), "EURUSD")
        assert intent.tp == 1.09876

    def test_volume_preserved(self):
        intent = _build_order_intent(_v10_result(volume=0.37), "EURUSD")
        assert intent.volume == 0.37

    def test_symbol_preserved(self):
        intent = _build_order_intent(_v10_result(), "GBPUSD")
        assert intent.symbol == "GBPUSD"

    def test_market_order_type(self):
        intent = _build_order_intent(_v10_result(order_type="MARKET"), "EURUSD")
        assert intent.entry_type == "MARKET"

    def test_limit_order_type(self):
        intent = _build_order_intent(_v10_result(order_type="LIMIT"), "EURUSD")
        assert intent.entry_type == "LIMIT"

    def test_stop_order_type(self):
        intent = _build_order_intent(_v10_result(order_type="STOP"), "EURUSD")
        assert intent.entry_type == "STOP"


class TestMetadataPreservation:
    """V10 strategy/horizon/identity must flow into OrderIntent metadata."""

    def test_strategy_in_pattern(self):
        intent = _build_order_intent(_v10_result(strategy="LIQUIDITY_SWEEP_REVERSAL"), "EURUSD")
        assert intent.pattern == "LIQUIDITY_SWEEP_REVERSAL"

    def test_strategy_in_metadata(self):
        intent = _build_order_intent(_v10_result(strategy="TREND_CONTINUATION"), "EURUSD")
        assert intent.metadata["strategy_family"] == "TREND_CONTINUATION"

    def test_horizon_in_metadata(self):
        intent = _build_order_intent(_v10_result(horizon="EXTENDED"), "EURUSD")
        assert intent.metadata["horizon"] == "EXTENDED"

    def test_decision_id_in_metadata(self):
        intent = _build_order_intent(_v10_result(obs_id="unique_123"), "EURUSD")
        assert intent.metadata["decision_id"] == "unique_123"

    def test_engine_marker(self):
        intent = _build_order_intent(_v10_result(), "EURUSD")
        assert intent.metadata["engine"] == "V10"

    def test_risk_id_is_observation_id(self):
        intent = _build_order_intent(_v10_result(obs_id="obs_xyz"), "EURUSD")
        assert intent.risk_id == "obs_xyz"


class TestNoModification:
    """Bridge must NOT recalculate, modify, or override any field."""

    def test_no_volume_recalculation(self):
        """Volume from V10 RiskDecision must not be recomputed."""
        intent = _build_order_intent(_v10_result(volume=0.1234), "EURUSD")
        assert intent.volume == 0.1234  # Exact match, no rounding by bridge

    def test_no_stop_modification(self):
        """Stop from V10 EntryDecision must not be adjusted."""
        intent = _build_order_intent(_v10_result(sl=1.23456), "EURUSD")
        assert intent.sl == 1.23456

    def test_no_target_modification(self):
        intent = _build_order_intent(_v10_result(tp=1.34567), "EURUSD")
        assert intent.tp == 1.34567

    def test_no_direction_flip(self):
        """Direction must never be inverted or changed."""
        buy_intent = _build_order_intent(_v10_result(direction="BUY"), "EURUSD")
        sell_intent = _build_order_intent(_v10_result(direction="SELL"), "EURUSD")
        assert buy_intent.side == Side.BUY
        assert sell_intent.side == Side.SELL


class TestOrderIntentCompatibility:
    """OrderIntent from V10 must be accepted by prepare_execution."""

    def test_all_required_fields_present(self):
        intent = _build_order_intent(_v10_result(), "EURUSD")
        # These are the fields prepare_execution and MT5Execution read:
        assert isinstance(intent.symbol, str) and intent.symbol
        assert isinstance(intent.side, Side)
        assert isinstance(intent.volume, float) and intent.volume > 0
        assert isinstance(intent.sl, float) and intent.sl > 0
        assert isinstance(intent.tp, float) and intent.tp > 0
        assert isinstance(intent.entry_reference, float) and intent.entry_reference > 0
        assert isinstance(intent.entry_type, str)
        assert isinstance(intent.pattern, str)
        assert isinstance(intent.metadata, dict)

    def test_intent_is_frozen(self):
        intent = _build_order_intent(_v10_result(), "EURUSD")
        with pytest.raises(Exception):
            intent.volume = 999.0  # type: ignore

    def test_side_has_name_attribute(self):
        """MT5Execution uses intent.side.name — verify it exists."""
        intent = _build_order_intent(_v10_result(direction="BUY"), "EURUSD")
        assert intent.side.name == "BUY"
        intent2 = _build_order_intent(_v10_result(direction="SELL"), "EURUSD")
        assert intent2.side.name == "SELL"


class TestGuardChainSafety:
    """Runtime guards should only check safety, never V10 decision quality."""

    def test_regime_guard_disabled(self):
        """Regime guard must be disabled — it's a legacy decision gate."""
        from core import config
        assert config.REGIME_GUARD_ENABLED is False

    def test_no_score_in_guard_chain(self):
        """Guard chain should not import or check scores."""
        import inspect
        from risk import runtime_guard_chain
        source = inspect.getsource(runtime_guard_chain)
        assert "composite_score" not in source
        assert "MIN_SCORE_TO_TRADE" not in source
        assert "strategy_score" not in source
        assert "neutral_score" not in source

    def test_guard_chain_does_not_modify_intent(self):
        """Guard chain returns allowed/not-allowed — never modifies intent."""
        import inspect
        from risk import runtime_guard_chain
        source = inspect.getsource(runtime_guard_chain)
        # Should not assign to intent fields
        assert "intent.sl =" not in source
        assert "intent.tp =" not in source
        assert "intent.volume =" not in source
        assert "intent.side =" not in source
