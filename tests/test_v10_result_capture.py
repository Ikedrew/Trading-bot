"""Phase D.6 — Execution Result Capture Validation.

Verifies that V10 decision lineage is preserved through execution results.
"""

import pytest
from unittest.mock import MagicMock
from core.v10.scanner_adapter import _build_order_intent
from risk.models import OrderIntent
from strategy.signals import Side


def _v10_result(obs_id="v10_lineage_test_001", strategy="MEAN_REVERSION", horizon="SCALP"):
    result = MagicMock()
    result.approved = True
    result.execution.order_details.direction = "SELL"
    result.execution.order_details.entry_price = 1.09500
    result.execution.order_details.stop_loss = 1.09700
    result.execution.order_details.take_profit = 1.09100
    result.execution.order_details.volume = 0.15
    result.execution.order_details.order_type = "MARKET"
    result.strategy.strategy_family = strategy
    result.horizon.horizon_type = horizon
    result.opportunity.observation_id = obs_id
    result.opportunity.opportunity_type = "ZONE_REACTION"
    return result


class TestLineagePreservation:
    """V10 decision_id must flow through to execution records."""

    def test_observation_id_in_risk_id(self):
        intent = _build_order_intent(_v10_result(obs_id="abc123"), "EURUSD")
        assert intent.risk_id == "abc123"

    def test_observation_id_in_metadata_decision_id(self):
        intent = _build_order_intent(_v10_result(obs_id="abc123"), "EURUSD")
        assert intent.metadata["decision_id"] == "abc123"

    def test_strategy_family_preserved(self):
        intent = _build_order_intent(_v10_result(strategy="TREND_CONTINUATION"), "EURUSD")
        assert intent.pattern == "TREND_CONTINUATION"
        assert intent.metadata["strategy_family"] == "TREND_CONTINUATION"

    def test_horizon_preserved(self):
        intent = _build_order_intent(_v10_result(horizon="EXTENDED"), "EURUSD")
        assert intent.metadata["horizon"] == "EXTENDED"

    def test_engine_marker_preserved(self):
        intent = _build_order_intent(_v10_result(), "EURUSD")
        assert intent.metadata["engine"] == "V10"

    def test_opportunity_type_preserved(self):
        intent = _build_order_intent(_v10_result(), "EURUSD")
        assert intent.metadata["opportunity_type"] == "ZONE_REACTION"


class TestSuccessfulExecution:
    """On fill, the execution record must retain V10 lineage."""

    def test_intent_side_name_available(self):
        """MT5 execution logs use intent.side.name — must work for V10."""
        intent = _build_order_intent(_v10_result(), "EURUSD")
        assert intent.side.name == "SELL"

    def test_intent_volume_numeric(self):
        """MT5 request uses float(intent.volume)."""
        intent = _build_order_intent(_v10_result(), "EURUSD")
        assert isinstance(intent.volume, float)
        assert intent.volume == 0.15

    def test_intent_sl_numeric(self):
        intent = _build_order_intent(_v10_result(), "EURUSD")
        assert isinstance(intent.sl, float)
        assert intent.sl == 1.09700

    def test_intent_tp_numeric(self):
        intent = _build_order_intent(_v10_result(), "EURUSD")
        assert isinstance(intent.tp, float)
        assert intent.tp == 1.09100

    def test_intent_pattern_for_comment(self):
        """MT5 order comment uses intent.pattern."""
        intent = _build_order_intent(_v10_result(strategy="FALSE_BREAK"), "EURUSD")
        assert intent.pattern == "FALSE_BREAK"


class TestFailedExecution:
    """On broker rejection, the intent still contains V10 lineage for forensics."""

    def test_rejection_can_trace_back(self):
        """Even if MT5 rejects, the intent metadata identifies the V10 decision."""
        intent = _build_order_intent(_v10_result(obs_id="rejected_decision_xyz"), "EURUSD")
        # After rejection, forensics can access:
        assert intent.risk_id == "rejected_decision_xyz"
        assert intent.metadata["decision_id"] == "rejected_decision_xyz"
        assert intent.metadata["engine"] == "V10"

    def test_rejection_preserves_all_trade_params(self):
        """Original trade params preserved even on failure (for analysis)."""
        intent = _build_order_intent(_v10_result(), "EURUSD")
        assert intent.symbol == "EURUSD"
        assert intent.side == Side.SELL
        assert intent.volume == 0.15
        assert intent.sl == 1.09700
        assert intent.tp == 1.09100


class TestDecisionLedgerIntegration:
    """The cycle_decision record stores V10 lineage on successful execution."""

    def test_execution_intent_contains_horizon(self):
        """live_scanner stores intent.metadata['horizon'] in execution_intent."""
        intent = _build_order_intent(_v10_result(horizon="INTRADAY"), "EURUSD")
        # Live scanner reads: decision.intent.metadata.get("horizon", "SCALP")
        assert intent.metadata.get("horizon") == "INTRADAY"

    def test_execution_intent_contains_pattern(self):
        """live_scanner stores intent.pattern in execution_intent."""
        intent = _build_order_intent(_v10_result(strategy="BREAKOUT_EXPANSION"), "EURUSD")
        assert intent.pattern == "BREAKOUT_EXPANSION"


class TestNoOverwrite:
    """MT5 response must NOT overwrite V10 original values."""

    def test_intent_is_frozen(self):
        """OrderIntent is frozen — broker result cannot modify it."""
        intent = _build_order_intent(_v10_result(), "EURUSD")
        with pytest.raises(Exception):
            intent.sl = 999.0  # type: ignore

    def test_original_direction_immutable(self):
        intent = _build_order_intent(_v10_result(), "EURUSD")
        with pytest.raises(Exception):
            intent.side = Side.BUY  # type: ignore

    def test_original_volume_immutable(self):
        intent = _build_order_intent(_v10_result(), "EURUSD")
        with pytest.raises(Exception):
            intent.volume = 999.0  # type: ignore
