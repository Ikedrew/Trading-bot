"""
Tests for Phase 4C.4 — Horizon Shadow Observability.

Verifies that horizon shadow creation and outcome events produce
structured log output for monitoring.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.shadow_trades import ShadowTradeEngine, _emit_close_event


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1: INTRADAY shadow closing produces outcome log
# ═══════════════════════════════════════════════════════════════════════════════

class TestHorizonOutcomeLogging:
    def test_intraday_close_logs_horizon(self, caplog):
        """INTRADAY shadow close produces [HORIZON_SHADOW_CLOSED] log."""
        engine = ShadowTradeEngine(max_bars=100)
        engine.open_trade(
            trade_id="shadow_100_GBPUSD_INTRADAY",
            cycle_id=100, symbol="GBPUSD", direction="SELL",
            entry_price=1.33700, stop_loss=1.33850, take_profit=1.33250,
            entry_time=1784800000.0, strategy="CONTINUATION_INTRADAY",
            pattern="TWEEZER_TOP", score=0.62,
        )

        with caplog.at_level(logging.INFO):
            engine.evaluate_bar(
                symbol="GBPUSD", bar_high=1.33750, bar_low=1.33200,
                bar_close=1.33240, bar_time=1784800300.0,
            )

        # Check log contains horizon outcome
        horizon_logs = [r for r in caplog.records if "HORIZON_SHADOW_CLOSED" in r.message]
        assert len(horizon_logs) >= 1
        msg = horizon_logs[0].message
        assert "horizon=INTRADAY" in msg
        assert "outcome=take_profit" in msg
        assert "symbol=GBPUSD" in msg

    def test_extended_close_logs_horizon(self, caplog):
        """EXTENDED shadow close produces [HORIZON_SHADOW_CLOSED] log."""
        engine = ShadowTradeEngine(max_bars=100)
        engine.open_trade(
            trade_id="shadow_200_NZDUSD_EXTENDED",
            cycle_id=200, symbol="NZDUSD", direction="SELL",
            entry_price=0.58000, stop_loss=0.58300, take_profit=0.56800,
            entry_time=1784800000.0, strategy="CONTINUATION_EXTENDED",
            pattern="TWEEZER_TOP", score=0.65,
        )

        with caplog.at_level(logging.INFO):
            engine.evaluate_bar(
                symbol="NZDUSD", bar_high=0.58350, bar_low=0.57900,
                bar_close=0.58310, bar_time=1784800300.0,
            )

        horizon_logs = [r for r in caplog.records if "HORIZON_SHADOW_CLOSED" in r.message]
        assert len(horizon_logs) >= 1
        msg = horizon_logs[0].message
        assert "horizon=EXTENDED" in msg
        assert "outcome=stop_loss" in msg

    def test_standard_shadow_uses_normal_log(self, caplog):
        """Standard shadow (no horizon tag) uses [SHADOW_TRADE_CLOSED]."""
        engine = ShadowTradeEngine(max_bars=100)
        engine.open_trade(
            trade_id="shadow_300_EURUSD",
            cycle_id=300, symbol="EURUSD", direction="BUY",
            entry_price=1.10000, stop_loss=1.09950, take_profit=1.10100,
            entry_time=1784800000.0, strategy="CONTINUATION",
            pattern="HAMMER", score=0.55,
        )

        with caplog.at_level(logging.INFO):
            engine.evaluate_bar(
                symbol="EURUSD", bar_high=1.10150, bar_low=1.09980,
                bar_close=1.10120, bar_time=1784800300.0,
            )

        standard_logs = [r for r in caplog.records if "SHADOW_TRADE_CLOSED" in r.message]
        horizon_logs = [r for r in caplog.records if "HORIZON_SHADOW_CLOSED" in r.message]
        assert len(standard_logs) >= 1
        assert len(horizon_logs) == 0  # Should NOT produce horizon log


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: No horizon info does not crash
# ═══════════════════════════════════════════════════════════════════════════════

class TestSafeFallback:
    def test_empty_record_does_not_crash(self):
        """_emit_close_event with empty record doesn't raise."""
        _emit_close_event({})  # Must not raise

    def test_malformed_record_does_not_crash(self):
        """Unexpected record structure doesn't crash."""
        _emit_close_event({"identity": None, "simulated_outcome": "bad"})

    def test_missing_identity_does_not_crash(self):
        """Record without identity section logs safely."""
        _emit_close_event({"simulated_outcome": {"exit_reason": "stop_loss", "bars_held": 5}})


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: Horizon identity survives through lifecycle
# ═══════════════════════════════════════════════════════════════════════════════

class TestHorizonIdentityPropagation:
    def test_horizon_in_trade_id_survives_to_outcome(self, caplog):
        """trade_id with _INTRADAY suffix identified correctly at close."""
        engine = ShadowTradeEngine(max_bars=5)
        engine.open_trade(
            trade_id="shadow_500_USDCHF_INTRADAY",
            cycle_id=500, symbol="USDCHF", direction="BUY",
            entry_price=0.81500, stop_loss=0.81400, take_profit=0.81800,
            entry_time=1784800000.0, strategy="REVERSAL_INTRADAY",
            pattern="HAMMER", score=0.5,
        )

        # Timeout after max_bars
        with caplog.at_level(logging.INFO):
            for i in range(6):
                engine.evaluate_bar(
                    symbol="USDCHF", bar_high=0.81550, bar_low=0.81450,
                    bar_close=0.81500, bar_time=1784800000.0 + (i + 1) * 300,
                )

        horizon_logs = [r for r in caplog.records if "HORIZON_SHADOW_CLOSED" in r.message]
        assert len(horizon_logs) >= 1
        assert "horizon=INTRADAY" in horizon_logs[0].message
        assert "outcome=max_bars_timeout" in horizon_logs[0].message
